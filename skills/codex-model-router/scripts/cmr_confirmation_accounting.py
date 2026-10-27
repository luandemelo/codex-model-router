"""Complete current execution accounting over the immutable v4 audit inventory.

Purpose is derived from audited source packets; it never changes an occurrence's
phase, authorizes dispatch, or modifies historical runtime accounting contracts.
"""

from __future__ import annotations

import copy
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import cmr_compiler
import cmr_contracts
import cmr_dispatch
import cmr_gate_evidence
import cmr_runtime

sys.dont_write_bytecode = True

_USAGE_FIELDS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens",
)
_TASK_SOURCE_FIELDS = {"preflight_evidence", "runtime_input"}
_INPUT_FIELDS = {
    "schema_version", "occurrences", "task_states", "plan_sha256",
    "release_review", "semantic_results", "instrument_errors", "task_preflight_evidence",
}
_STATE_FIELDS = {"task_id", "selector", "dispatchable", "completed_worker_rounds"}
_RELEASE_FIELDS = {
    "initial_implementation_head", "initial_export_head", "fix_required",
    "final_implementation_head", "final_export_head",
}


@dataclass(frozen=True)
class _ConfirmationScope:
    task_id: str
    packet: Mapping[str, Any]
    packet_sha256: str
    prompt_sha256: str
    forbidden_threads: tuple[str, ...]


def _reference(value: Any) -> cmr_compiler.ArtifactRef:
    if isinstance(value, cmr_compiler.ArtifactRef):
        return value
    return cmr_compiler.ArtifactRef.from_value(value)


def _read_object(descriptor: int, reference: cmr_compiler.ArtifactRef, label: str) -> dict[str, Any]:
    payload, _ = cmr_dispatch._read_reference(descriptor, reference)
    return cmr_contracts.strict_json_object(payload, label)


def _ordered(errors: Sequence[str]) -> list[str]:
    return sorted(set(errors))


def _source_scopes(
    descriptor: int, states: Sequence[cmr_runtime.TaskAccountingState],
    task_preflight_evidence: Any, ledger_root: Path,
) -> tuple[list[_ConfirmationScope], dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    scopes: list[_ConfirmationScope] = []
    proposers: dict[str, dict[str, Any]] = {}
    if not isinstance(task_preflight_evidence, Mapping):
        return [], {}, ["task_preflight_evidence must be a task evidence dictionary"]
    expected_ids = {state.task_id for state in states}
    if set(task_preflight_evidence) != expected_ids:
        errors.append("task_preflight_evidence must contain exactly all task state IDs")
    ontologies = cmr_dispatch._frozen_ontologies()
    for state in sorted(states, key=lambda item: item.task_id):
        task_id = state.task_id
        label = f"task {task_id} accounting source"
        try:
            source = task_preflight_evidence.get(task_id)
            source_errors = cmr_dispatch._exact_fields(source, _TASK_SOURCE_FIELDS, label)
            if source_errors:
                errors.extend(source_errors)
                continue
            reference = _reference(source["preflight_evidence"])
            cmr_dispatch._read_reference(descriptor, reference)
            source_errors, preflight, observed_ref, invocation, result = cmr_dispatch._preflight_evidence_details(
                ledger_root / reference.path, ledger_root)
            if observed_ref != reference:
                source_errors.append("preflight evidence reference is stale")
            if preflight is None or preflight.get("task_id") != task_id:
                source_errors.append("preflight evidence does not bind task")
            if preflight is not None and preflight.get("selector", {}).get("decision") != state.selector:
                source_errors.append("preflight selector does not bind task state")
            if source_errors:
                errors.extend(f"{label}: {error}" for error in source_errors)
                continue
            if state.selector == "block":
                if source["runtime_input"] is not None:
                    errors.append(f"{label}: pre-call block forbids runtime input")
                continue
            runtime_ref = _reference(source["runtime_input"])
            runtime_input = _read_object(descriptor, runtime_ref, "accounting runtime input")
            runtime_errors = cmr_compiler.validate_runtime_input(runtime_input, ontologies)
            if runtime_errors:
                errors.extend(f"{label}: {error}" for error in runtime_errors)
                continue
            if state.selector != "run":
                continue
            if invocation is None or result is None:
                errors.append(f"{label}: run requires the audited proposing invocation")
                continue
            proposer_record = _read_object(descriptor, _reference(invocation["occurrence"]), "proposing occurrence")
            cmr_runtime.OccurrenceAudit.from_record(proposer_record)
            candidate_ref = _reference(invocation["candidate_evidence"])
            candidate_errors, candidate, controller_thread = cmr_dispatch._controller_evidence_details(descriptor, candidate_ref)
            if candidate_errors or candidate is None:
                errors.extend(f"{label}: {error}" for error in candidate_errors or ["candidate source is unavailable"])
                continue
            if candidate.get("task_id") != task_id or invocation.get("task_id") != task_id:
                errors.append(f"{label}: candidate or invocation task is split")
                continue
            candidate_gate_ref = _reference(invocation["candidate_gate_evidence"])
            replacement = preflight["outcome"] == "replaced"
            replacement_attempt = result["outcome"] == "replace"
            source_errors = cmr_dispatch._canonical_gate_ref_errors(candidate_gate_ref, task_id, "controller")
            source_errors += cmr_dispatch._gate_directory_errors(descriptor, task_id, replacement=replacement_attempt)
            candidate_gate = cmr_dispatch._bound_gate_from_reference(descriptor, candidate_gate_ref, candidate, runtime_input)
            bound_preflight = cmr_dispatch._bound_preflight_from_invocation(descriptor, invocation, result)
            replacement_ref = None
            replacement_gate_ref = None
            replacement_value = None
            replacement_gate = None
            if replacement_attempt:
                if replacement:
                    replacement_ref = _reference(preflight["replacement_evidence"])
                    replacement_value = _read_object(descriptor, replacement_ref, "accounting replacement evidence")
                else:
                    # A non-monotonic replace is blocked before replacement files
                    # exist, but replay still needs its audited attempted facts.
                    replacement_value = copy.deepcopy(candidate)
                    for field in ("fact_ids", "uncertainty_ids", "action_intents"):
                        replacement_value[field] = copy.deepcopy(result[field])
                replacement_gate_path = f"tasks/{cmr_dispatch._task_component(task_id)}/gate-evidence/preflight.json"
                _, replacement_gate_ref, _ = cmr_dispatch._read_artifact_path(
                    descriptor, ledger_root / replacement_gate_path, ledger_root)
                replacement_gate = cmr_dispatch._bound_gate_from_reference(descriptor, replacement_gate_ref,
                    replacement_value, runtime_input, source_phase="preflight",
                    source_raw=invocation["raw"], source_thread_id=invocation["thread_id"])
            brief_payload, _ = cmr_dispatch._read_reference(descriptor, _reference(invocation["brief"]))
            replayed = cmr_dispatch.apply_preflight(candidate, runtime_input, bound_preflight, ontologies,
                candidate_gate_evidence=candidate_gate, replacement_gate_evidence=replacement_gate,
                brief_bytes=brief_payload)
            if replayed.outcome != preflight["outcome"]:
                source_errors.append("preflight outcome does not bind replayed task evidence")
            if replacement:
                final_ref = _reference(preflight["final_decision"])
                final = _read_object(descriptor, final_ref, "accounting final decision")
                if not cmr_dispatch._json_type_equal(replayed.final_decision, final):
                    source_errors.append("final decision does not bind replayed task evidence")
                if not cmr_dispatch._json_type_equal(replayed.replacement_evidence, replacement_value):
                    source_errors.append("replacement evidence does not bind replayed task evidence")
            if source_errors:
                errors.extend(f"{label}: {error}" for error in source_errors)
                continue
            proposers[task_id] = proposer_record
            if not replacement or not cmr_gate_evidence.required_gate_ids(replayed.final_decision["fact_ids"], ontologies):
                continue
            packet = {
                "task_id": task_id, "task_phase": runtime_input["task_phase"],
                "brief": invocation["brief"], "runtime_input": runtime_ref.to_dict(),
                "candidate_evidence": invocation["candidate_evidence"],
                "candidate_gate_evidence": invocation["candidate_gate_evidence"],
                "replacement_evidence": replacement_ref.to_dict(),
                "replacement_gate_evidence": replacement_gate_ref.to_dict(),
                "final_decision": preflight["final_decision"],
                "proposing_preflight_invocation": preflight["invocation"],
            }
            prompt = cmr_dispatch.build_gate_confirmation_prompt(packet, ledger_root)
            scopes.append(_ConfirmationScope(task_id, packet,
                hashlib.sha256(cmr_dispatch._canonical_json_bytes(packet)).hexdigest(),
                hashlib.sha256(prompt).hexdigest(),
                tuple(thread for thread in (controller_thread, invocation["thread_id"]) if thread)))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"{label} is invalid: {exc}")
    return scopes, proposers, _ordered(errors)


def _dispatch_accounting_bindings(descriptor: int, states: Sequence[cmr_runtime.TaskAccountingState],
    sources: Any, occurrences: Sequence[cmr_runtime.OccurrenceAudit], ledger_root: Path) -> list[str]:
    """Keep new-profile calls attached to this task's exact dispatch runtime.

    A v4-looking model/effort record cannot erase a task's explicit preference,
    and a different well-formed runtime cannot substitute for the sealed one.
    Failures remain in the accounting inventory, including their full usage.
    """
    errors: list[str] = []
    if not isinstance(sources, Mapping):
        return errors  # The source-scope validator reports the malformed input.
    for state in states:
        observed = [item for item in occurrences if item.task_id == state.task_id
            and item.scope_kind == "task" and item.phase in {"worker", "task_review", "re_review"}]
        source = sources.get(state.task_id)
        if not isinstance(source, Mapping) or source.get("runtime_input") is None:
            continue
        try:
            runtime_ref = _reference(source["runtime_input"])
            runtime_value = _read_object(descriptor, runtime_ref, "accounting bound runtime")
            # The sealed preflight decision is authoritative even on a skip.
            # Caller-supplied runtime/audit versions cannot erase its v5 policy.
            preflight_value = _read_object(descriptor, _reference(source["preflight_evidence"]),
                                           "accounting preflight evidence")
            candidate = _read_object(descriptor, _reference(preflight_value["candidate_decision"]),
                                     "accounting candidate decision")
            if (candidate.get("schema_version") != "cmr-route-decision-v5"
                    and runtime_value.get("schema_version") != "cmr-runtime-input-v5"
                    and not any(item.schema_version == "cmr-occurrence-audit-v5" for item in observed)):
                continue
            if not observed and not state.dispatchable:
                continue
            bundle_path = ledger_root / f"tasks/{cmr_dispatch._task_component(state.task_id)}/dispatch/bundle.json"
            request = cmr_dispatch.dispatch_request(bundle_path, ledger_root)
            scope = request["scope"]
            bundle = _read_object(descriptor, _reference(scope["bundle"]), "accounting dispatch bundle")
            if not cmr_dispatch._json_type_equal(bundle["runtime_input"], source["runtime_input"]):
                errors.append(f"task {state.task_id} accounting runtime does not bind dispatch bundle")
            for item in observed:
                if item.schema_version != "cmr-occurrence-audit-v5" or not isinstance(item.dispatch_binding, Mapping):
                    errors.append(f"task {state.task_id} requires version-5 dispatch-bound audit for {item.phase}")
                elif (not cmr_dispatch._json_type_equal(item.dispatch_binding.get("scope"), scope)
                        or item.dispatch_binding.get("ledger_root") != str(ledger_root.absolute())):
                    errors.append(f"task {state.task_id} observed audit does not bind this dispatch scope")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"task {state.task_id} accounting dispatch binding is invalid: {exc}")
    return _ordered(errors)


def _usage(occurrence: cmr_runtime.OccurrenceAudit) -> dict[str, int] | None:
    parsed, _ = cmr_runtime._usage_dict(occurrence.usage)
    return parsed


def _usage_totals(occurrences: Sequence[cmr_runtime.OccurrenceAudit]) -> tuple[dict[str, int] | None, dict[str, int]]:
    known = dict.fromkeys(_USAGE_FIELDS, 0)
    complete = True
    for occurrence in occurrences:
        usage = _usage(occurrence)
        if usage is None:
            complete = False
        else:
            for field in _USAGE_FIELDS:
                known[field] += usage[field]
    return (dict(known) if complete else None), known


def _attempt(occurrence: cmr_runtime.OccurrenceAudit, outcome: str = "unknown") -> dict[str, Any]:
    return {"thread_id": occurrence.thread_id, "raw_sha256": occurrence.raw_sha256,
        "prompt_sha256": occurrence.prompt_sha256, "accepted": occurrence.accepted,
        "failure_class": occurrence.failure_class, "outcome": outcome, "usage": _usage(occurrence)}


def _confirmation_artifact(
    descriptor: int, scope: _ConfirmationScope, ledger_root: Path,
) -> tuple[cmr_compiler.ArtifactRef | None, dict[str, Any] | None, str | None, list[str]]:
    reference = None
    try:
        path = ledger_root / f"tasks/{cmr_dispatch._task_component(scope.task_id)}/gate-confirmation/evidence.json"
        payload, reference, _ = cmr_dispatch._read_artifact_path(descriptor, path, ledger_root)
        value = cmr_contracts.strict_json_object(payload, "accounting gate confirmation")
        errors = cmr_dispatch._validate_gate_confirmation(reference, expected_packet=scope.packet,
            ledger_root=ledger_root, require_accept=False)
        binding_errors, result = cmr_dispatch._gate_confirmation_value_errors(value,
            expected_packet=scope.packet, ledger_root=ledger_root, require_accept=False)
        errors.extend(binding_errors)
        record = _read_object(descriptor, _reference(value["occurrence"]), "confirmation occurrence")
        outcome = result.get("outcome") if not errors and isinstance(result, Mapping) else None
        return reference, record, outcome, _ordered(errors)
    except FileNotFoundError:
        return None, None, None, []
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return reference, None, None, [f"gate confirmation artifact is invalid: {exc}"]


def summarize_execution_accounting(
    occurrences: Sequence[cmr_runtime.OccurrenceAudit],
    task_states: Sequence[cmr_runtime.TaskAccountingState],
    semantic_results: Any, instrument_errors: Any, *, plan_sha256: str,
    release_review: cmr_runtime.ReleaseReviewState | None,
    task_preflight_evidence: Mapping[str, Mapping[str, Any]], ledger_root: Path,
) -> dict[str, Any]:
    """Account for every unique attempted call, deriving confirmation purpose.

    Callers must supply the complete host inventory, including failures. A raw
    JSONL digest identifies one immutable call; duplicate rows are errors and do
    not add its usage twice. Unknown usage makes aggregate usage null while the
    independently reported known counters remain available.
    """
    errors: list[str] = []
    try:
        values = list(occurrences)
    except TypeError:
        values = []
        errors.append("occurrences must be a sequence of OccurrenceAudit")
    states, state_errors = cmr_runtime._state_validation(task_states)
    errors.extend(state_errors)
    unique: list[cmr_runtime.OccurrenceAudit] = []
    seen: dict[str, dict[str, Any] | None] = {}
    invalid_count = 0
    for index, occurrence in enumerate(values):
        if not isinstance(occurrence, cmr_runtime.OccurrenceAudit):
            errors.append(f"occurrences[{index}] must be OccurrenceAudit")
            invalid_count += 1
            continue
        record = None
        try:
            record = occurrence.to_record()
            cmr_runtime.OccurrenceAudit.from_record(record)
        except (AttributeError, ValueError, TypeError) as exc:
            errors.append(f"occurrences[{index}] audit record is invalid: {exc}")
        identity = occurrence.raw_sha256 if isinstance(occurrence.raw_sha256, str) else f"invalid-raw-{index}"
        if identity in seen:
            errors.append(f"duplicate occurrence identity {identity}")
            if not cmr_dispatch._json_type_equal(record, seen[identity]):
                errors.append(f"cross-partition or stale occurrence identity {identity}")
            continue
        seen[identity] = record
        unique.append(occurrence)
        if _usage(occurrence) is None:
            errors.append(f"occurrence {identity} usage is missing or invalid")
    core: list[cmr_runtime.OccurrenceAudit] = []
    unmatched: list[cmr_runtime.OccurrenceAudit] = []
    confirmation_calls: list[cmr_runtime.OccurrenceAudit] = []
    public_scopes: list[dict[str, Any]] = []
    descriptor = -1
    try:
        root = Path(ledger_root)
        descriptor, root_stat = cmr_dispatch._open_root(root)
        scopes, proposers, source_errors = _source_scopes(descriptor, states, task_preflight_evidence, root)
        errors.extend(source_errors)
        errors.extend(_dispatch_accounting_bindings(descriptor, states, task_preflight_evidence, unique, root))
        members: dict[str, list[cmr_runtime.OccurrenceAudit]] = {scope.packet_sha256: [] for scope in scopes}
        for occurrence in unique:
            matches = [scope for scope in scopes if occurrence.prompt_sha256 == scope.prompt_sha256
                and occurrence.phase == "preflight" and occurrence.scope_kind == "task"
                and occurrence.task_id == scope.task_id and occurrence.scope_id == scope.task_id
                and occurrence.round_index is None]
            proposer = proposers.get(occurrence.task_id) if isinstance(occurrence.task_id, str) else None
            is_proposer = proposer is not None and cmr_dispatch._json_type_equal(occurrence.to_record(), proposer)
            if len(matches) == 1 and not is_proposer:
                members[matches[0].packet_sha256].append(occurrence)
                confirmation_calls.append(occurrence)
            elif matches:
                errors.append(f"ambiguous or cross-partition occurrence {occurrence.raw_sha256}")
                unmatched.append(occurrence)
            elif occurrence.phase == "preflight" and not is_proposer:
                errors.append(f"unmatched preflight occurrence {occurrence.raw_sha256}; no exact task/packet/prompt scope")
                unmatched.append(occurrence)
            else:
                core.append(occurrence)
        core_threads = {occurrence.thread_id for occurrence in core if occurrence.thread_id is not None}
        confirmation_threads: set[str] = set()
        for scope in scopes:
            attempts = members[scope.packet_sha256]
            reference, artifact_record, outcome, artifact_errors = _confirmation_artifact(descriptor, scope, root)
            errors.extend(f"gate confirmation {scope.task_id}: {error}" for error in artifact_errors)
            bound_attempt = None
            if artifact_record is not None:
                exact = [attempt for attempt in attempts
                    if cmr_dispatch._json_type_equal(attempt.to_record(), artifact_record)]
                if len(exact) != 1:
                    errors.append(f"gate confirmation {scope.task_id} artifact does not bind inventory exactly once")
                elif not artifact_errors:
                    bound_attempt = exact[0]
            for attempt in attempts:
                if attempt.thread_id in scope.forbidden_threads or attempt.thread_id in core_threads:
                    errors.append(f"gate confirmation {scope.task_id} reuses a core or source thread")
                if attempt.thread_id is not None:
                    if attempt.thread_id in confirmation_threads:
                        errors.append(f"gate confirmation {scope.task_id} reuses a confirmation thread")
                    confirmation_threads.add(attempt.thread_id)
                if attempt.accepted is not True or attempt.failure_class is not None or attempt.errors:
                    errors.append(f"gate confirmation {scope.task_id} attempt failed ({attempt.failure_class or 'instrument'})")
                else:
                    attempt_errors = cmr_dispatch._control_occurrence_errors(attempt.to_record(),
                        phase="preflight", task_id=scope.task_id, plan_sha256=None)
                    errors.extend(f"gate confirmation {scope.task_id}: {error}" for error in attempt_errors)
            count = len(attempts)
            if count == 0:
                scope_outcome = "missing"
                errors.append(f"missing gate confirmation for {scope.task_id} packet {scope.packet_sha256}")
            elif count > 1:
                scope_outcome = "multiple"
                errors.append(f"gate confirmation {scope.task_id} has {count} attempts; expected exactly one")
            elif attempts[0].accepted is not True:
                scope_outcome = "failed"
            elif bound_attempt is not None and outcome in {"accept", "block"}:
                scope_outcome = outcome
            else:
                scope_outcome = "unverified"
                errors.append(f"gate confirmation {scope.task_id} requires matching frozen confirmation evidence")
            if scope_outcome == "block" and any(state.task_id == scope.task_id and state.dispatchable for state in states):
                errors.append(f"gate confirmation {scope.task_id} blocked dispatch but task state is dispatchable")
            usage, known_usage = _usage_totals(attempts)
            public_scopes.append({"purpose": "gate_confirmation", "task_id": scope.task_id,
                "replacement_packet_sha256": scope.packet_sha256, "prompt_sha256": scope.prompt_sha256,
                "proposing_preflight_invocation": scope.packet["proposing_preflight_invocation"],
                "confirmation_evidence": reference.to_dict() if reference else None,
                "attempt_count": count, "extra_attempt_count": max(count - 1, 0), "outcome": scope_outcome,
                "attempts": [_attempt(attempt, outcome if attempt is bound_attempt and outcome else "unknown") for attempt in attempts],
                "usage": usage, "known_usage": known_usage})
        if not cmr_dispatch._root_identity_unchanged(root, root_stat):
            errors.append("ledger root identity changed during execution accounting")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"execution accounting source is invalid: {exc}")
        # Failed source inspection never discards a supplied occurrence.
        classified = {id(value) for value in core + unmatched + confirmation_calls}
        unmatched.extend(value for value in unique if id(value) not in classified)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    core_summary = cmr_runtime.summarize_accounting(core, states, semantic_results, instrument_errors,
        plan_sha256=plan_sha256, release_review=release_review)
    errors.extend(cmr_runtime.validate_call_shape(core, states, plan_sha256=plan_sha256, release_review=release_review))
    errors.extend(f"core_v4 instrument failure: {identifier}" for identifier in core_summary["instrument_failures"])
    total_usage, known_usage = _usage_totals(unique)
    confirmation_usage, confirmation_known_usage = _usage_totals(confirmation_calls)
    if invalid_count:
        total_usage = None
    accounting_errors = _ordered(errors)
    return {"schema_version": "cmr-execution-accounting-v1", "core_v4": core_summary,
        "confirmation_scopes": public_scopes, "confirmation_count": len(confirmation_calls),
        "confirmation_extra_attempts": sum(scope["extra_attempt_count"] for scope in public_scopes),
        "confirmation_usage": confirmation_usage, "confirmation_known_usage": confirmation_known_usage,
        "unmatched_occurrences": [_attempt(value) for value in unmatched],
        "observed_record_count": len(values), "total_calls": len(unique) + invalid_count,
        "total_usage": total_usage, "known_usage": known_usage, "usage_complete": total_usage is not None,
        "canonical_call_shape": core_summary["canonical_call_shape"] and not accounting_errors,
        "accounting_errors": accounting_errors}


def validate_execution_call_shape(
    occurrences: Sequence[cmr_runtime.OccurrenceAudit], task_states: Sequence[cmr_runtime.TaskAccountingState], *,
    plan_sha256: str, release_review: cmr_runtime.ReleaseReviewState | None,
    task_preflight_evidence: Mapping[str, Mapping[str, Any]], ledger_root: Path,
) -> list[str]:
    """Validate complete current shape, including all independent confirmations."""
    return summarize_execution_accounting(occurrences, task_states, None, [], plan_sha256=plan_sha256,
        release_review=release_review, task_preflight_evidence=task_preflight_evidence,
        ledger_root=ledger_root)["accounting_errors"]


def validate_execution_accounting_input(value: Any) -> list[str]:
    """Validate the closed current CLI wire input without authorizing dispatch."""
    errors = cmr_dispatch._exact_fields(value, _INPUT_FIELDS, "execution accounting input")
    if not isinstance(value, Mapping):
        return errors
    version = value.get("schema_version")
    if not isinstance(version, str) or version not in {"cmr-execution-accounting-input-v1", "cmr-execution-accounting-input-v2"}:
        errors.append("execution accounting input.schema_version is invalid")
    for field in ("occurrences", "task_states", "instrument_errors"):
        if not isinstance(value.get(field), list):
            errors.append(f"{field} must be a JSON array")
    if isinstance(value.get("occurrences"), list):
        for index, record in enumerate(value["occurrences"]):
            if (value.get("schema_version") == "cmr-execution-accounting-input-v1"
                    and isinstance(record, Mapping) and record.get("schema_version") != "cmr-occurrence-audit-v4"):
                errors.append("version-5 occurrences require cmr-execution-accounting-input-v2")
            try:
                cmr_runtime.OccurrenceAudit.from_record(record, label=f"occurrences[{index}]")
            except ValueError as exc:
                errors.append(str(exc))
    if isinstance(value.get("task_states"), list):
        for index, state in enumerate(value["task_states"]):
            state_errors = cmr_dispatch._exact_fields(state, _STATE_FIELDS, f"task_states[{index}]")
            errors.extend(state_errors)
            if not state_errors:
                try:
                    if not isinstance(state["completed_worker_rounds"], list):
                        raise ValueError("completed_worker_rounds must be an array")
                    cmr_runtime.TaskAccountingState(state["task_id"], state["selector"], state["dispatchable"],
                        tuple(state["completed_worker_rounds"]))
                except (ValueError, TypeError) as exc:
                    errors.append(f"task_states[{index}] is invalid: {exc}")
    if not cmr_runtime._is_sha256(value.get("plan_sha256")):
        errors.append("plan_sha256 must be a lowercase SHA-256")
    release = value.get("release_review")
    if release is not None:
        release_errors = cmr_dispatch._exact_fields(release, _RELEASE_FIELDS, "release_review")
        errors.extend(release_errors)
        if not release_errors:
            try:
                cmr_runtime.ReleaseReviewState(**release)
            except (ValueError, TypeError) as exc:
                errors.append(f"release_review is invalid: {exc}")
    semantic = value.get("semantic_results")
    if semantic is not None and (not isinstance(semantic, Mapping)
            or any(not isinstance(key, str) or not key or type(item) is not bool for key, item in semantic.items())):
        errors.append("semantic_results must be null or a dictionary of nonempty identifiers to booleans")
    instrument = value.get("instrument_errors")
    if isinstance(instrument, list) and (any(not isinstance(item, str) or not item for item in instrument)
            or len(set(item for item in instrument if isinstance(item, str))) != len(instrument)):
        errors.append("instrument_errors must contain unique nonempty strings")
    sources = value.get("task_preflight_evidence")
    if not isinstance(sources, Mapping):
        errors.append("task_preflight_evidence must be a dictionary")
    else:
        for task_id, source in sources.items():
            if not isinstance(task_id, str) or not task_id:
                errors.append("task_preflight_evidence task IDs must be nonempty strings")
            source_errors = cmr_dispatch._exact_fields(source, _TASK_SOURCE_FIELDS, f"task_preflight_evidence[{task_id}]")
            errors.extend(source_errors)
            if not source_errors:
                for field in _TASK_SOURCE_FIELDS:
                    if field == "runtime_input" and source[field] is None:
                        continue
                    try:
                        cmr_compiler.ArtifactRef.from_value(source[field])
                    except (ValueError, TypeError) as exc:
                        errors.append(f"task_preflight_evidence[{task_id}].{field} is invalid: {exc}")
    return _ordered(errors)


def _closed(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
        "required": list(properties), "properties": dict(properties)}


def expected_execution_accounting_schemas() -> dict[str, dict[str, Any]]:
    """Return the closed additive report and current input wire schemas."""
    count = {"type": "integer", "minimum": 0}
    hash_value = {"type": "string", "pattern": cmr_contracts.LOWER_SHA256_PATTERN}
    usage = cmr_dispatch._usage_schema()
    nullable_usage = {"oneOf": [usage, {"type": "null"}]}
    ref = cmr_dispatch._artifact_ref_schema()
    nullable_ref = {"oneOf": [ref, {"type": "null"}]}
    attempt = _closed({"thread_id": {"type": ["string", "null"]}, "raw_sha256": hash_value,
        "prompt_sha256": hash_value, "accepted": {"type": "boolean"},
        "failure_class": {"enum": [None, "instrument", "operational"]},
        "outcome": {"enum": ["accept", "block", "unknown"]}, "usage": nullable_usage})
    scope = _closed({"purpose": {"const": "gate_confirmation"}, "task_id": {"type": "string", "minLength": 1},
        "replacement_packet_sha256": hash_value, "prompt_sha256": hash_value,
        "proposing_preflight_invocation": ref, "confirmation_evidence": nullable_ref,
        "attempt_count": count, "extra_attempt_count": count,
        "outcome": {"enum": ["accept", "block", "missing", "failed", "unverified", "multiple"]},
        "attempts": {"type": "array", "items": attempt}, "usage": nullable_usage, "known_usage": usage})
    core = cmr_contracts._accounting_schema()
    core_defs = core.pop("$defs")
    core.pop("$id")
    core.pop("$schema")
    report = _closed({"schema_version": {"const": "cmr-execution-accounting-v1"}, "core_v4": core,
        "confirmation_scopes": {"type": "array", "items": scope}, "confirmation_count": count,
        "confirmation_extra_attempts": count, "confirmation_usage": nullable_usage,
        "confirmation_known_usage": usage, "unmatched_occurrences": {"type": "array", "items": attempt},
        "observed_record_count": count, "total_calls": count, "total_usage": nullable_usage,
        "known_usage": usage, "usage_complete": {"type": "boolean"},
        "canonical_call_shape": {"type": "boolean"},
        "accounting_errors": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}})
    report.update({"$schema": cmr_contracts.JSON_SCHEMA_DIALECT, "$id": "cmr-execution-accounting-v1.schema.json",
        "$defs": core_defs})
    occurrence = cmr_contracts._occurrence_schema()
    occurrence_defs = occurrence.pop("$defs")
    occurrence.pop("$id")
    occurrence.pop("$schema")
    state = _closed({"task_id": {"type": "string", "minLength": 1}, "selector": {"enum": ["run", "skip", "block"]},
        "dispatchable": {"type": "boolean"}, "completed_worker_rounds": {"type": "array", "items": {
            "type": "integer", "minimum": 0, "maximum": 5}, "uniqueItems": True}})
    head = {"type": "string", "pattern": cmr_contracts.GIT_HEAD_PATTERN}
    release = _closed({"initial_implementation_head": head, "initial_export_head": head,
        "fix_required": {"type": "boolean"}, "final_implementation_head": head, "final_export_head": head})
    source = _closed({"preflight_evidence": ref, "runtime_input": nullable_ref})
    request = _closed({"schema_version": {"const": "cmr-execution-accounting-input-v1"},
        "occurrences": {"type": "array", "items": occurrence}, "task_states": {"type": "array", "items": state},
        "plan_sha256": hash_value, "release_review": {"oneOf": [release, {"type": "null"}]},
        "semantic_results": {"oneOf": [{"type": "object", "propertyNames": {"minLength": 1},
            "additionalProperties": {"type": "boolean"}}, {"type": "null"}]},
        "instrument_errors": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
        "task_preflight_evidence": {"type": "object", "propertyNames": {"minLength": 1}, "additionalProperties": source}})
    request.update({"$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-execution-accounting-input-v1.schema.json", "$defs": occurrence_defs})
    import copy
    latency_request = copy.deepcopy(request)
    latency_request["$id"] = "cmr-execution-accounting-input-v2.schema.json"
    latency_request["properties"]["schema_version"] = {"const": "cmr-execution-accounting-input-v2"}
    latency_occurrence = cmr_contracts._latency_occurrence_schema()
    latency_occurrence.pop("$defs")
    latency_occurrence.pop("$id")
    latency_occurrence.pop("$schema")
    latency_request["properties"]["occurrences"]["items"] = {"oneOf": [occurrence, latency_occurrence]}
    return {"cmr-execution-accounting-v1.schema.json": report,
        "cmr-execution-accounting-input-v1.schema.json": request,
        "cmr-execution-accounting-input-v2.schema.json": latency_request}


__all__ = ["validate_execution_call_shape", "summarize_execution_accounting",
    "validate_execution_accounting_input", "expected_execution_accounting_schemas"]
