from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import cmr_contracts
from cmr_runtime import OccurrenceAudit

sys.dont_write_bytecode = True


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DEFECT_SEPARATOR_RE = re.compile(r"[- \t\n\r\f\v]+")
_ARTIFACT_PATH_PATTERN = (
    r"^(?!/)(?![A-Za-z]:)(?!.*//)"
    r"(?!.*(?:^|/)(?:\.|\.\.)(?:/|$))[^\\\x00-\x1f\x7f]+$"
)
_HISTORY_EVENTS = {
    "attempt_completed_failed",
    "attempt_completed_passed",
    "declared_resolved",
    "reappeared",
}
_SYSTEM_BLOCKER_IDS = {
    "controller_result_invalid",
    "occurrence_invalid",
    "runtime_input_invalid",
    "artifact_binding_invalid",
    "preflight_result_invalid",
    "preflight_non_monotonic",
    "preflight_blocked",
    "dispatch_bundle_invalid",
    "legacy_dispatch_forbidden",
}
SELECTOR_TRIGGER_IDS = (
    "artifact_binding_invalid",
    "safe_lane_unavailable",
    "nondefault_worker_route",
    "hard_gate_or_recurrence",
    "integration_or_elevated_risk",
    "luna_max_eligibility",
    "branch_final_review",
    "external_action_present",
    *cmr_contracts.UNCERTAINTY_IDS,
)
_REVIEW_ROUTES = {
    ("gpt-5.6-luna", "xhigh"): ("gpt-5.6-luna", "max"),
    ("gpt-5.6-luna", "max"): ("gpt-5.6-luna", "max"),
    ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "xhigh"),
    ("gpt-5.6-sol", "max"): ("gpt-5.6-sol", "max"),
}
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_OCCURRENCE_FIELDS = (
    "schema_version",
    "phase",
    "scope_kind",
    "scope_id",
    "round_index",
    "task_id",
    "response_policy",
    "model",
    "reasoning_effort",
    "fork_turns",
    "prompt_sha256",
    "thread_policy",
    "thread_id",
    "cwd",
    "write_policy",
    "exit_code",
    "external_writes",
    "transcript_sha256",
    "raw_sha256",
    "usage",
    "tool_event_count",
    "terminal_report_sha256",
    "ignored_empty_agent_messages",
    "accepted",
    "failure_class",
    "errors",
)


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        errors = _validate_artifact_ref(self.to_dict(), "artifact")
        if errors:
            raise ValueError("; ".join(errors))

    @classmethod
    def from_value(cls, value: Any) -> "ArtifactRef":
        errors = _validate_artifact_ref(value, "artifact")
        if errors:
            raise ValueError("; ".join(errors))
        assert isinstance(value, Mapping)
        return cls(
            path=value["path"],
            sha256=value["sha256"],
            size_bytes=value["size_bytes"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class Recurrence:
    active: bool
    reason_id: str | None
    defect_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "reason_id": self.reason_id,
            "defect_id": self.defect_id,
        }


@dataclass(frozen=True)
class BoundPreflightResult:
    result: Mapping[str, Any]
    task_id: str
    brief: ArtifactRef
    candidate_evidence: ArtifactRef
    candidate_decision: ArtifactRef
    selector: Mapping[str, Any]
    prompt: ArtifactRef
    raw: ArtifactRef
    transcript: ArtifactRef
    occurrence_ref: ArtifactRef
    occurrence: OccurrenceAudit


@dataclass(frozen=True)
class PreflightTransition:
    outcome: str
    replacement_evidence: Mapping[str, Any] | None
    final_decision: Mapping[str, Any] | None
    blocker_ids: tuple[str, ...]
    adjudication: Mapping[str, str] | None


class CompilationError(ValueError):
    def __init__(self, blocker_id: str, errors: Sequence[str] = ()) -> None:
        if blocker_id not in _SYSTEM_BLOCKER_IDS:
            raise ValueError(f"unknown compilation blocker ID: {blocker_id}")
        self.blocker_id = blocker_id
        self.blocker_ids = (blocker_id,)
        self.errors = tuple(sorted(set(errors)))
        detail = f": {'; '.join(self.errors)}" if self.errors else ""
        super().__init__(f"{blocker_id}{detail}")


def _exact_fields(value: Any, fields: set[str], label: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{label} must be an object"]
    errors = [
        f"{label}: unknown field {field}" for field in sorted(set(value) - fields)
    ]
    errors += [
        f"{label}: missing field {field}" for field in sorted(fields - set(value))
    ]
    return errors


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_absolute_normalized_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if _CONTROL_RE.search(value):
        return False
    if value.startswith("/"):
        components = value[1:].split("/")
    elif re.match(r"^[A-Za-z]:/", value):
        components = value[3:].split("/")
    else:
        return False
    return bool(components) and all(
        component not in {"", ".", ".."} for component in components
    )


def _is_relative_artifact_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or _DRIVE_RE.match(value) or "\\" in value:
        return False
    if _CONTROL_RE.search(value):
        return False
    components = value.split("/")
    return all(component not in {"", ".", ".."} for component in components)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonical JSON") from exc


def _json_type_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return set(left) == set(right) and all(
            _json_type_equal(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _json_type_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def _validate_artifact_ref(value: Any, label: str) -> list[str]:
    fields = {"path", "sha256", "size_bytes"}
    errors = _exact_fields(value, fields, label)
    if not isinstance(value, Mapping):
        return errors
    if not _is_relative_artifact_path(value.get("path")):
        errors.append(f"{label}.path must be a normalized relative POSIX path")
    if not _is_sha256(value.get("sha256")):
        errors.append(f"{label}.sha256 must be lowercase SHA-256")
    size = value.get("size_bytes")
    if type(size) is not int or size < 0:
        errors.append(f"{label}.size_bytes must be a nonnegative integer")
    return sorted(set(errors))


def validate_selector(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return ["selector must be an object"]
    decision = value.get("decision")
    if decision == "skip":
        fields = {"decision", "trigger_ids", "safe_lane"}
    elif decision in {"run", "block"}:
        fields = {"decision", "trigger_ids"}
    else:
        return ["selector.decision must be skip, run, or block"]
    errors = _exact_fields(value, fields, "selector")
    triggers = value.get("trigger_ids")
    if type(triggers) is not list:
        errors.append("selector.trigger_ids must be an array")
        return sorted(set(errors))
    if any(not isinstance(item, str) for item in triggers):
        errors.append("selector.trigger_ids must contain strings")
        return sorted(set(errors))
    if len(triggers) != len(set(triggers)):
        errors.append("selector.trigger_ids must be unique")
    unknown = set(triggers) - set(SELECTOR_TRIGGER_IDS)
    if unknown:
        errors.append("selector.trigger_ids contains an unknown trigger")
    canonical = [item for item in SELECTOR_TRIGGER_IDS if item in set(triggers)]
    if triggers != canonical:
        errors.append("selector.trigger_ids must use canonical order")
    if decision == "skip":
        if triggers:
            errors.append("skip selector.trigger_ids must be empty")
        errors += _validate_artifact_ref(value.get("safe_lane"), "selector.safe_lane")
    elif decision == "run":
        if not triggers:
            errors.append("run selector.trigger_ids must be non-empty")
        if "artifact_binding_invalid" in triggers:
            errors.append("run selector cannot contain artifact_binding_invalid")
    elif triggers != ["artifact_binding_invalid"]:
        errors.append(
            "block selector.trigger_ids must equal artifact_binding_invalid"
        )
    return sorted(set(errors))


def _validate_usage(value: Any, label: str) -> list[str]:
    errors = _exact_fields(value, set(_USAGE_FIELDS), label)
    if not isinstance(value, Mapping):
        return errors
    for field in _USAGE_FIELDS:
        amount = value.get(field)
        if type(amount) is not int or amount < 0:
            errors.append(f"{label}.{field} must be a nonnegative integer")
    return errors


def _validate_control_cwd(value: Any, label: str) -> list[str]:
    fields = {"kind", "path", "fresh", "destroyed", "read_only"}
    errors = _exact_fields(value, fields, label)
    if not isinstance(value, Mapping):
        return errors
    if value.get("kind") != "ephemeral":
        errors.append(f"{label}.kind must equal ephemeral")
    if not _is_absolute_normalized_path(value.get("path")):
        errors.append(f"{label}.path must be an absolute normalized path")
    for field in ("fresh", "destroyed", "read_only"):
        if value.get(field) is not True:
            errors.append(f"{label}.{field} must be true")
    return errors


def _validate_occurrence_record(
    value: Any,
    *,
    task_id: Any,
) -> list[str]:
    errors = _exact_fields(value, set(_OCCURRENCE_FIELDS), "route evidence.occurrence")
    if not isinstance(value, Mapping):
        return sorted(set(errors))
    label = "route evidence.occurrence"
    if value.get("schema_version") != "cmr-occurrence-audit-v1":
        errors.append(f"{label}.schema_version must equal cmr-occurrence-audit-v1")
    phase = value.get("phase")
    if not isinstance(phase, str) or phase != "controller":
        errors.append(f"{label}.phase is not allowed for route evidence")
    if value.get("scope_kind") != "task":
        errors.append(f"{label}.scope_kind must equal task")
    if value.get("scope_id") != task_id:
        errors.append(f"{label}.scope_id must bind task_id")
    if value.get("task_id") != task_id:
        errors.append(f"{label}.task_id must bind task_id")
    if value.get("round_index") is not None:
        errors.append(f"{label}.round_index must be null")
    if value.get("response_policy") != "closed_json":
        errors.append(f"{label}.response_policy must equal closed_json")
    if (value.get("model"), value.get("reasoning_effort")) != (
        "gpt-5.6-luna",
        "xhigh",
    ):
        errors.append(f"{label} route does not match its phase")
    if value.get("fork_turns") != "none":
        errors.append(f"{label}.fork_turns must equal none")
    if not _is_sha256(value.get("prompt_sha256")):
        errors.append(f"{label}.prompt_sha256 must be lowercase SHA-256")
    if value.get("thread_policy") != "fresh":
        errors.append(f"{label}.thread_policy must equal fresh")
    if not _is_nonempty_string(value.get("thread_id")):
        errors.append(f"{label}.thread_id must be non-empty")
    errors += _validate_control_cwd(value.get("cwd"), f"{label}.cwd")
    if value.get("write_policy") != "control_plane_no_write":
        errors.append(f"{label}.write_policy must equal control_plane_no_write")
    if type(value.get("exit_code")) is not int or value.get("exit_code") != 0:
        errors.append(f"{label}.exit_code must equal integer zero")
    if value.get("external_writes") is not False:
        errors.append(f"{label}.external_writes must be false")
    if not _is_sha256(value.get("transcript_sha256")):
        errors.append(f"{label}.transcript_sha256 must be lowercase SHA-256")
    if not _is_sha256(value.get("raw_sha256")):
        errors.append(f"{label}.raw_sha256 must be lowercase SHA-256")
    errors += _validate_usage(value.get("usage"), f"{label}.usage")
    if type(value.get("tool_event_count")) is not int or value.get("tool_event_count") != 0:
        errors.append(f"{label}.tool_event_count must equal integer zero")
    if value.get("terminal_report_sha256") is not None:
        errors.append(f"{label}.terminal_report_sha256 must be null")
    ignored = value.get("ignored_empty_agent_messages")
    if type(ignored) is not int or ignored < 0:
        errors.append(
            f"{label}.ignored_empty_agent_messages must be a nonnegative integer"
        )
    if value.get("accepted") is not True:
        errors.append(f"{label}.accepted must be true")
    if value.get("failure_class") is not None:
        errors.append(f"{label}.failure_class must be null")
    if value.get("errors") != []:
        errors.append(f"{label}.errors must be empty")
    return sorted(set(errors))


def _semantic_envelope_errors(
    value: Mapping[str, Any], ontologies: cmr_contracts.Ontologies
) -> list[str]:
    controller_shape = {
        "schema_version": "cmr-controller-result-v1",
        "fact_ids": value.get("fact_ids"),
        "uncertainty_ids": value.get("uncertainty_ids"),
        "action_intents": value.get("action_intents"),
    }
    return [
        error.replace("controller result", "route evidence", 1)
        for error in cmr_contracts.validate_controller_result(
            controller_shape, ontologies
        )
    ]


def _frozen_ontologies() -> cmr_contracts.Ontologies:
    return cmr_contracts.Ontologies(
        fact_entries=tuple(cmr_contracts.FACT_ENTRIES),
        uncertainty_ids=tuple(cmr_contracts.UNCERTAINTY_IDS),
        action_entries=tuple(cmr_contracts.ACTION_ENTRIES),
        safe_lane_reason_ids=tuple(cmr_contracts.SAFE_LANE_REASON_IDS),
    )


def _route_evidence_error_groups(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> tuple[list[str], list[str], list[str]]:
    fields = {
        "schema_version",
        "task_id",
        "brief",
        "fact_ids",
        "uncertainty_ids",
        "action_intents",
        "raw",
        "occurrence",
    }
    controller_errors = _exact_fields(value, fields, "route evidence")
    occurrence_errors: list[str] = []
    artifact_errors: list[str] = []
    if not isinstance(value, Mapping):
        return sorted(set(controller_errors)), [], []
    if value.get("schema_version") != "cmr-route-evidence-v2":
        controller_errors.append(
            "route evidence.schema_version must equal cmr-route-evidence-v2"
        )
    task_id = value.get("task_id")
    if not _is_nonempty_string(task_id):
        controller_errors.append("route evidence.task_id must be a non-empty string")
    controller_errors += _semantic_envelope_errors(value, ontologies)
    artifact_errors += _validate_artifact_ref(
        value.get("brief"), "route evidence.brief"
    )
    artifact_errors += _validate_artifact_ref(value.get("raw"), "route evidence.raw")
    occurrence_errors += _validate_occurrence_record(
        value.get("occurrence"),
        task_id=task_id,
    )
    occurrence = value.get("occurrence")
    brief = value.get("brief")
    raw = value.get("raw")
    if isinstance(occurrence, Mapping) and isinstance(brief, Mapping):
        if occurrence.get("prompt_sha256") != brief.get("sha256"):
            artifact_errors.append(
                "route evidence brief does not bind occurrence prompt_sha256"
            )
    if isinstance(occurrence, Mapping) and isinstance(raw, Mapping):
        if occurrence.get("raw_sha256") != raw.get("sha256"):
            artifact_errors.append(
                "route evidence raw does not bind occurrence raw_sha256"
            )
    return (
        sorted(set(controller_errors)),
        sorted(set(occurrence_errors)),
        sorted(set(artifact_errors)),
    )


def validate_route_evidence(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> list[str]:
    groups = _route_evidence_error_groups(value, ontologies)
    return sorted(set(error for group in groups for error in group))


def _raise_first_evidence_error(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> None:
    controller_errors, occurrence_errors, artifact_errors = _route_evidence_error_groups(
        value, ontologies
    )
    if controller_errors:
        raise CompilationError("controller_result_invalid", controller_errors)
    if occurrence_errors:
        raise CompilationError("occurrence_invalid", occurrence_errors)
    if artifact_errors:
        raise CompilationError("artifact_binding_invalid", artifact_errors)


def _binding_occurrence_errors(
    occurrence: Any,
    *,
    task_id: str,
    result: Mapping[str, Any],
) -> list[str]:
    if not isinstance(occurrence, OccurrenceAudit):
        return ["occurrence must be an OccurrenceAudit"]
    errors = _validate_occurrence_record(occurrence.to_record(), task_id=task_id)
    if occurrence.raw_object != dict(result):
        errors.append("occurrence raw_object does not equal controller result")
    if occurrence.terminal_report is not None:
        errors.append("controller occurrence must not contain a terminal report")
    return sorted(set(errors))


def bind_controller_result(
    result: Mapping[str, Any],
    *,
    task_id: str,
    brief: ArtifactRef,
    raw: ArtifactRef,
    occurrence: OccurrenceAudit,
) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise CompilationError(
            "controller_result_invalid", ["controller result must be an object"]
        )
    controller_errors = cmr_contracts.validate_controller_result(
        dict(result), _frozen_ontologies()
    )
    if controller_errors:
        raise CompilationError("controller_result_invalid", controller_errors)
    if not _is_nonempty_string(task_id):
        raise CompilationError(
            "controller_result_invalid", ["task_id must be a non-empty string"]
        )
    if not isinstance(brief, ArtifactRef) or not isinstance(raw, ArtifactRef):
        raise CompilationError(
            "artifact_binding_invalid", ["brief and raw must be ArtifactRef values"]
        )
    occurrence_errors = _binding_occurrence_errors(
        occurrence, task_id=task_id, result=result
    )
    if occurrence_errors:
        raise CompilationError("occurrence_invalid", occurrence_errors)
    binding_errors: list[str] = []
    if occurrence.prompt_sha256 != brief.sha256:
        binding_errors.append("brief does not bind controller prompt SHA-256")
    if occurrence.raw_sha256 != raw.sha256:
        binding_errors.append("raw artifact does not bind controller raw SHA-256")
    if binding_errors:
        raise CompilationError("artifact_binding_invalid", binding_errors)
    return {
        "schema_version": "cmr-route-evidence-v2",
        "task_id": task_id,
        "brief": brief.to_dict(),
        "fact_ids": list(result.get("fact_ids", [])),
        "uncertainty_ids": list(result.get("uncertainty_ids", [])),
        "action_intents": [dict(item) for item in result.get("action_intents", [])],
        "raw": raw.to_dict(),
        "occurrence": occurrence.to_record(),
    }


def _preflight_occurrence_errors(
    occurrence: Any,
    *,
    task_id: str,
    result: Mapping[str, Any],
) -> list[str]:
    if not isinstance(occurrence, OccurrenceAudit):
        return ["occurrence must be an OccurrenceAudit"]
    value = occurrence.to_record()
    label = "preflight occurrence"
    errors = _exact_fields(value, set(_OCCURRENCE_FIELDS), label)
    if value.get("schema_version") != "cmr-occurrence-audit-v1":
        errors.append(f"{label}.schema_version must equal cmr-occurrence-audit-v1")
    expected_values = {
        "phase": "preflight",
        "scope_kind": "task",
        "scope_id": task_id,
        "round_index": None,
        "task_id": task_id,
        "response_policy": "closed_json",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "fork_turns": "none",
        "thread_policy": "fresh",
        "write_policy": "control_plane_no_write",
        "exit_code": 0,
        "external_writes": False,
        "tool_event_count": 0,
        "terminal_report_sha256": None,
        "accepted": True,
        "failure_class": None,
        "errors": [],
    }
    for field, expected in expected_values.items():
        if not _json_type_equal(value.get(field), expected):
            errors.append(f"{label}.{field} does not match audited preflight")
    for field in ("prompt_sha256", "transcript_sha256", "raw_sha256"):
        if not _is_sha256(value.get(field)):
            errors.append(f"{label}.{field} must be lowercase SHA-256")
    if not _is_nonempty_string(value.get("thread_id")):
        errors.append(f"{label}.thread_id must be non-empty")
    errors += _validate_control_cwd(value.get("cwd"), f"{label}.cwd")
    errors += _validate_usage(value.get("usage"), f"{label}.usage")
    ignored = value.get("ignored_empty_agent_messages")
    if type(ignored) is not int or ignored < 0:
        errors.append(
            f"{label}.ignored_empty_agent_messages must be a nonnegative integer"
        )
    if not _json_type_equal(occurrence.raw_object, dict(result)):
        errors.append("preflight occurrence raw_object does not equal result")
    if occurrence.terminal_report is not None:
        errors.append("preflight occurrence must not contain a terminal report")
    return sorted(set(errors))


def bind_preflight_result(
    result: Mapping[str, Any],
    *,
    task_id: str,
    brief: ArtifactRef,
    candidate_evidence: ArtifactRef,
    candidate_decision: ArtifactRef,
    selector: Mapping[str, Any],
    prompt: ArtifactRef,
    raw: ArtifactRef,
    transcript: ArtifactRef,
    occurrence_ref: ArtifactRef,
    occurrence: OccurrenceAudit,
) -> BoundPreflightResult:
    if not isinstance(result, Mapping):
        raise CompilationError(
            "preflight_result_invalid", ["preflight result must be an object"]
        )
    semantic_errors = cmr_contracts.validate_preflight_result(
        dict(result), _frozen_ontologies()
    )
    if semantic_errors:
        raise CompilationError("preflight_result_invalid", semantic_errors)
    if not _is_nonempty_string(task_id):
        raise CompilationError(
            "artifact_binding_invalid", ["task_id must be a non-empty string"]
        )
    references = {
        "brief": brief,
        "candidate_evidence": candidate_evidence,
        "candidate_decision": candidate_decision,
        "prompt": prompt,
        "raw": raw,
        "transcript": transcript,
        "occurrence": occurrence_ref,
    }
    invalid_refs = [
        f"{name} must be an ArtifactRef"
        for name, reference in references.items()
        if not isinstance(reference, ArtifactRef)
    ]
    if invalid_refs:
        raise CompilationError("artifact_binding_invalid", invalid_refs)
    selector_errors = validate_selector(selector)
    if selector_errors or selector.get("decision") != "run":
        if selector.get("decision") != "run":
            selector_errors.append("audited preflight requires a run selector")
        raise CompilationError("artifact_binding_invalid", selector_errors)
    occurrence_errors = _preflight_occurrence_errors(
        occurrence, task_id=task_id, result=result
    )
    if occurrence_errors:
        raise CompilationError("occurrence_invalid", occurrence_errors)
    binding_errors: list[str] = []
    assert isinstance(occurrence, OccurrenceAudit)
    if occurrence.prompt_sha256 != prompt.sha256:
        binding_errors.append("prompt does not bind preflight prompt SHA-256")
    if occurrence.raw_sha256 != raw.sha256:
        binding_errors.append("raw does not bind preflight raw SHA-256")
    if occurrence.transcript_sha256 != transcript.sha256:
        binding_errors.append(
            "transcript does not bind preflight transcript SHA-256"
        )
    occurrence_payload = _canonical_json_bytes(occurrence.to_record())
    if occurrence_ref.sha256 != hashlib.sha256(occurrence_payload).hexdigest():
        binding_errors.append("occurrence reference SHA-256 does not bind audit record")
    if occurrence_ref.size_bytes != len(occurrence_payload):
        binding_errors.append("occurrence reference size does not bind audit record")
    if binding_errors:
        raise CompilationError("artifact_binding_invalid", binding_errors)
    return BoundPreflightResult(
        result=copy.deepcopy(dict(result)),
        task_id=task_id,
        brief=brief,
        candidate_evidence=candidate_evidence,
        candidate_decision=candidate_decision,
        selector=copy.deepcopy(dict(selector)),
        prompt=prompt,
        raw=raw,
        transcript=transcript,
        occurrence_ref=occurrence_ref,
        occurrence=occurrence,
    )


def _route_rank(route: Any) -> int | None:
    if not isinstance(route, Mapping):
        return None
    pair = (route.get("model"), route.get("reasoning_effort"))
    ranks = {
        ("gpt-5.6-luna", "xhigh"): 0,
        ("gpt-5.6-luna", "max"): 1,
        ("gpt-5.6-sol", "xhigh"): 2,
        ("gpt-5.6-sol", "max"): 3,
    }
    return ranks.get(pair)


def _sol_max_adjudication() -> dict[str, str]:
    return {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "max",
        "fork_turns": "none",
        "thread_policy": "fresh",
    }


def validate_preflight_monotonicity(
    candidate_evidence: Any,
    candidate_decision: Any,
    replacement_evidence: Any,
    replacement_decision: Any,
    ontologies: cmr_contracts.Ontologies,
) -> list[str]:
    values = {
        "candidate evidence": candidate_evidence,
        "candidate decision": candidate_decision,
        "replacement evidence": replacement_evidence,
        "replacement decision": replacement_decision,
    }
    errors = [
        f"{label} must be an object"
        for label, value in values.items()
        if not isinstance(value, Mapping)
    ]
    if errors:
        return sorted(set(errors))
    assert isinstance(candidate_evidence, Mapping)
    assert isinstance(candidate_decision, Mapping)
    assert isinstance(replacement_evidence, Mapping)
    assert isinstance(replacement_decision, Mapping)

    semantic_fields = {"fact_ids", "uncertainty_ids", "action_intents"}
    candidate_provenance = {
        field: value
        for field, value in candidate_evidence.items()
        if field not in semantic_fields
    }
    replacement_provenance = {
        field: value
        for field, value in replacement_evidence.items()
        if field not in semantic_fields
    }
    if not _json_type_equal(candidate_provenance, replacement_provenance):
        errors.append("replacement must preserve immutable route evidence")

    fact_entries = {str(entry["id"]): entry for entry in ontologies.fact_entries}
    candidate_facts_value = candidate_evidence.get("fact_ids")
    replacement_facts_value = replacement_evidence.get("fact_ids")
    if not isinstance(candidate_facts_value, list) or any(
        fact_id not in fact_entries for fact_id in candidate_facts_value
    ):
        errors.append("candidate facts are invalid")
        candidate_facts: set[str] = set()
    else:
        candidate_facts = set(candidate_facts_value)
    if not isinstance(replacement_facts_value, list) or any(
        fact_id not in fact_entries for fact_id in replacement_facts_value
    ):
        errors.append("replacement facts are invalid")
        replacement_facts: set[str] = set()
    else:
        replacement_facts = set(replacement_facts_value)

    candidate_hard = {
        fact_id
        for fact_id in candidate_facts
        if fact_entries[fact_id]["hard_gate"]
    }
    replacement_hard = {
        fact_id
        for fact_id in replacement_facts
        if fact_entries[fact_id]["hard_gate"]
    }
    if not candidate_hard <= replacement_hard:
        errors.append("replacement must preserve every candidate hard gate")

    candidate_integration_or_elevated = {
        fact_id
        for fact_id in candidate_facts
        if fact_entries[fact_id]["routing_class"]
        in {"integration", "elevated_risk"}
    }
    replacement_integration_or_elevated = {
        fact_id
        for fact_id in replacement_facts
        if fact_entries[fact_id]["routing_class"]
        in {"integration", "elevated_risk"}
    }
    if candidate_integration_or_elevated and not (
        replacement_integration_or_elevated or replacement_hard
    ):
        errors.append("replacement must not weaken integration or risk")

    candidate_actions = candidate_evidence.get("action_intents")
    replacement_actions = replacement_evidence.get("action_intents")
    if not isinstance(candidate_actions, list) or not isinstance(
        replacement_actions, list
    ):
        errors.append("replacement action intents are invalid")
    elif any(
        not any(
            _json_type_equal(intent, replacement)
            for replacement in replacement_actions
        )
        for intent in candidate_actions
    ):
        errors.append("replacement must preserve external action intents")

    candidate_worker_rank = _route_rank(candidate_decision.get("worker"))
    replacement_worker_rank = _route_rank(replacement_decision.get("worker"))
    candidate_review_rank = _route_rank(candidate_decision.get("review"))
    replacement_review_rank = _route_rank(replacement_decision.get("review"))
    if (
        candidate_worker_rank is None
        or replacement_worker_rank is None
        or replacement_worker_rank < candidate_worker_rank
    ):
        errors.append("replacement worker downroute is forbidden")
    if (
        candidate_review_rank is None
        or replacement_review_rank is None
        or replacement_review_rank < candidate_review_rank
    ):
        errors.append("replacement review downroute is forbidden")
    if not _json_type_equal(
        candidate_decision.get("recurrence"), replacement_decision.get("recurrence")
    ):
        errors.append("replacement must preserve runtime recurrence")
    if (
        candidate_evidence.get("task_id") != replacement_evidence.get("task_id")
        or not _json_type_equal(
            candidate_evidence.get("brief"), replacement_evidence.get("brief")
        )
        or replacement_decision.get("task_id") != candidate_decision.get("task_id")
        or not _json_type_equal(
            replacement_decision.get("brief"), candidate_decision.get("brief")
        )
    ):
        errors.append("replacement identity drift is forbidden")
    return sorted(set(errors))


def apply_preflight(
    candidate_evidence: Any,
    runtime: Any,
    bound_preflight: BoundPreflightResult,
    ontologies: cmr_contracts.Ontologies,
) -> PreflightTransition:
    if not isinstance(bound_preflight, BoundPreflightResult):
        raise CompilationError(
            "preflight_result_invalid",
            ["apply_preflight requires a BoundPreflightResult"],
        )
    _raise_first_evidence_error(candidate_evidence, ontologies)
    runtime_errors, _ = _runtime_errors_and_recurrence(runtime, ontologies)
    if runtime_errors:
        raise CompilationError("runtime_input_invalid", runtime_errors)
    assert isinstance(candidate_evidence, Mapping)
    if candidate_evidence.get("task_id") != bound_preflight.task_id:
        raise CompilationError(
            "artifact_binding_invalid", ["preflight task_id does not bind candidate"]
        )
    if not _json_type_equal(
        candidate_evidence.get("brief"), bound_preflight.brief.to_dict()
    ):
        raise CompilationError(
            "artifact_binding_invalid", ["preflight brief does not bind candidate"]
        )
    candidate_evidence_payload = _canonical_json_bytes(candidate_evidence)
    if (
        hashlib.sha256(candidate_evidence_payload).hexdigest()
        != bound_preflight.candidate_evidence.sha256
        or len(candidate_evidence_payload)
        != bound_preflight.candidate_evidence.size_bytes
    ):
        raise CompilationError(
            "artifact_binding_invalid",
            ["candidate evidence reference does not bind candidate evidence"],
        )
    candidate_decision = compile_route(candidate_evidence, runtime, ontologies)
    candidate_decision_payload = _canonical_json_bytes(candidate_decision)
    if (
        hashlib.sha256(candidate_decision_payload).hexdigest()
        != bound_preflight.candidate_decision.sha256
        or len(candidate_decision_payload)
        != bound_preflight.candidate_decision.size_bytes
    ):
        raise CompilationError(
            "artifact_binding_invalid",
            ["candidate decision reference does not bind compiled decision"],
        )

    outcome = bound_preflight.result["outcome"]
    if outcome == "accept":
        return PreflightTransition(
            outcome="accepted",
            replacement_evidence=None,
            final_decision=copy.deepcopy(candidate_decision),
            blocker_ids=(),
            adjudication=None,
        )
    if outcome == "block":
        return PreflightTransition(
            outcome="blocked",
            replacement_evidence=None,
            final_decision=None,
            blocker_ids=tuple(bound_preflight.result["blocker_ids"]),
            adjudication=_sol_max_adjudication(),
        )

    replacement_evidence = copy.deepcopy(dict(candidate_evidence))
    for field in ("fact_ids", "uncertainty_ids", "action_intents"):
        replacement_evidence[field] = copy.deepcopy(bound_preflight.result[field])
    replacement_decision = compile_route(replacement_evidence, runtime, ontologies)

    monotonic_errors = validate_preflight_monotonicity(
        candidate_evidence,
        candidate_decision,
        replacement_evidence,
        replacement_decision,
        ontologies,
    )

    if monotonic_errors:
        return PreflightTransition(
            outcome="blocked",
            replacement_evidence=None,
            final_decision=None,
            blocker_ids=("preflight_non_monotonic",),
            adjudication=_sol_max_adjudication(),
        )
    return PreflightTransition(
        outcome="replaced",
        replacement_evidence=replacement_evidence,
        final_decision=replacement_decision,
        blocker_ids=(),
        adjudication=None,
    )


def _normalize_defect_id(value: Any, label: str) -> tuple[str | None, list[str]]:
    if not isinstance(value, str) or not value:
        return None, [f"{label} must be a non-empty string"]
    normalized = _DEFECT_SEPARATOR_RE.sub("_", value.strip().casefold())
    if not normalized:
        return None, [f"{label} normalizes to empty"]
    return normalized, []


def _derive_recurrence(
    history: Any, current_defect_id: Any
) -> tuple[Recurrence, list[str]]:
    inactive = Recurrence(False, None, None)
    errors: list[str] = []
    if not isinstance(history, list):
        return inactive, ["runtime input.failure_history must be an array"]
    current: str | None = None
    if current_defect_id is not None:
        current, current_errors = _normalize_defect_id(
            current_defect_id, "runtime input.current_defect_id"
        )
        errors += current_errors
    elif history:
        errors.append(
            "runtime input.current_defect_id is required when failure_history is non-empty"
        )

    states: dict[str, str] = {}
    failure_counts: dict[str, int] = {}
    reappeared: set[str] = set()
    for index, item in enumerate(history):
        label = f"runtime input.failure_history[{index}]"
        item_errors = _exact_fields(item, {"defect_id", "event"}, label)
        if not isinstance(item, Mapping):
            errors += item_errors
            continue
        errors += item_errors
        defect_id, defect_errors = _normalize_defect_id(
            item.get("defect_id"), f"{label}.defect_id"
        )
        errors += defect_errors
        event = item.get("event")
        event_is_public = isinstance(event, str) and event in _HISTORY_EVENTS
        if not event_is_public:
            errors.append(f"{label}.event is not a public history event")
        if defect_id is None or not event_is_public:
            continue
        state = states.get(defect_id, "open")
        if event == "attempt_completed_failed":
            if state != "open":
                errors.append(f"{label}.event contradicts prior {state} state")
            else:
                failure_counts[defect_id] = failure_counts.get(defect_id, 0) + 1
        elif event == "attempt_completed_passed":
            if state != "open":
                errors.append(f"{label}.event contradicts prior {state} state")
            else:
                states[defect_id] = "passed"
        elif event == "declared_resolved":
            if state != "passed":
                errors.append(f"{label}.event requires a preceding completed pass")
            else:
                states[defect_id] = "resolved"
        elif event == "reappeared":
            if state != "resolved":
                errors.append(f"{label}.event requires an earlier declared resolution")
            else:
                reappeared.add(defect_id)
                states[defect_id] = "open"
    if errors:
        return inactive, sorted(set(errors))
    if current is not None and current in reappeared:
        return (
            Recurrence(
                True, "regression_after_declared_resolved", current
            ),
            [],
        )
    if current is not None and failure_counts.get(current, 0) >= 2:
        return Recurrence(True, "recurring_failure", current), []
    return inactive, []


def derive_recurrence(history: Any, current_defect_id: Any) -> Recurrence:
    recurrence, errors = _derive_recurrence(history, current_defect_id)
    if errors:
        raise CompilationError("runtime_input_invalid", errors)
    return recurrence


def _validate_registry(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> list[str]:
    label = "runtime input.authorization_registry"
    errors = _exact_fields(value, {"schema_version", "entries"}, label)
    if not isinstance(value, Mapping):
        return errors
    if value.get("schema_version") != "cmr-authorization-registry-v1":
        errors.append(
            f"{label}.schema_version must equal cmr-authorization-registry-v1"
        )
    entries = value.get("entries")
    if not isinstance(entries, list):
        errors.append(f"{label}.entries must be an array")
        return sorted(set(errors))
    pairs: list[tuple[str, str]] = []
    for index, entry in enumerate(entries):
        entry_label = f"{label}.entries[{index}]"
        allowed = {
            "action",
            "target",
            "authorization_id",
            "preview_sha256",
            "readback_id",
        }
        required = allowed - {"readback_id"}
        entry_fields = (
            required
            if isinstance(entry, Mapping) and "readback_id" not in entry
            else allowed
        )
        errors += _exact_fields(entry, entry_fields, entry_label)
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        target = entry.get("target")
        semantic = {
            "schema_version": "cmr-controller-result-v1",
            "fact_ids": [],
            "uncertainty_ids": [],
            "action_intents": [{"action": action, "target": target}],
        }
        target_errors = cmr_contracts.validate_controller_result(semantic, ontologies)
        errors += [
            error.replace(
                "controller result.action_intents[0]", entry_label, 1
            )
            for error in target_errors
        ]
        if isinstance(action, str) and isinstance(target, str):
            pairs.append((action, target))
        if not _is_nonempty_string(entry.get("authorization_id")):
            errors.append(f"{entry_label}.authorization_id must be non-empty")
        if not _is_sha256(entry.get("preview_sha256")):
            errors.append(f"{entry_label}.preview_sha256 must be lowercase SHA-256")
        if "readback_id" in entry and not _is_nonempty_string(entry.get("readback_id")):
            errors.append(f"{entry_label}.readback_id must be non-empty")
    if len(pairs) != len(set(pairs)):
        errors.append(f"{label}.entries must have unique action and target pairs")
    return sorted(set(errors))


def _runtime_errors_and_recurrence(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> tuple[list[str], Recurrence]:
    fields = {
        "schema_version",
        "task_phase",
        "current_defect_id",
        "failure_history",
        "review_scope",
        "authorization_registry",
    }
    errors = _exact_fields(value, fields, "runtime input")
    inactive = Recurrence(False, None, None)
    if not isinstance(value, Mapping):
        return sorted(set(errors)), inactive
    if value.get("schema_version") != "cmr-runtime-input-v1":
        errors.append("runtime input.schema_version must equal cmr-runtime-input-v1")
    task_phase = value.get("task_phase")
    if not isinstance(task_phase, str) or task_phase not in {
        "implementation",
        "final_review",
    }:
        errors.append("runtime input.task_phase must be implementation or final_review")
    review_scope = value.get("review_scope")
    if not isinstance(review_scope, str) or review_scope not in {"task", "branch"}:
        errors.append("runtime input.review_scope must be task or branch")
    if task_phase == "final_review" and review_scope != "branch":
        errors.append("runtime input.final_review requires branch review_scope")
    current = value.get("current_defect_id")
    if current is not None:
        _, current_errors = _normalize_defect_id(
            current, "runtime input.current_defect_id"
        )
        errors += current_errors
    recurrence, history_errors = _derive_recurrence(
        value.get("failure_history"), current
    )
    errors += history_errors
    errors += _validate_registry(value.get("authorization_registry"), ontologies)
    return sorted(set(errors)), recurrence


def validate_runtime_input(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> list[str]:
    errors, _ = _runtime_errors_and_recurrence(value, ontologies)
    return errors


def _eligible_luna_max_facts(fact_ids: Sequence[str]) -> list[str]:
    facts = set(fact_ids)
    eligible: set[str] = set()
    if "low_blast_radius" in facts:
        eligible.update(
            facts & {"audit", "judgment_required", "reconciliation"}
        )
    if {
        "complex_local_execution",
        "architecture_approved",
    }.issubset(facts):
        eligible.add("complex_local_execution")
    return [fact_id for fact_id in fact_ids if fact_id in eligible]


def _compile_external_actions(
    intents: Sequence[Mapping[str, Any]], registry: Mapping[str, Any]
) -> list[dict[str, Any]]:
    entries = {
        (entry["action"], entry["target"]): entry
        for entry in registry["entries"]
    }
    compiled: list[dict[str, Any]] = []
    for intent in intents:
        action = intent["action"]
        target = intent.get("target")
        if target is None:
            compiled.append(
                {
                    "action": action,
                    "state": "blocked",
                    "authorized": False,
                    "preview": False,
                    "readback": False,
                    "blocker_ids": ["external_target_missing"],
                }
            )
            continue
        evidence = entries.get((action, target))
        if evidence is None:
            compiled.append(
                {
                    "action": action,
                    "target": target,
                    "state": "blocked",
                    "authorized": False,
                    "preview": False,
                    "readback": False,
                    "blocker_ids": [],
                }
            )
            continue
        completed = "readback_id" in evidence
        item = {
            "action": action,
            "target": target,
            "state": "completed" if completed else "ready",
            "authorized": True,
            "preview": True,
            "readback": completed,
            "blocker_ids": [],
            "authorization_id": evidence["authorization_id"],
            "preview_sha256": evidence["preview_sha256"],
        }
        if completed:
            item["readback_id"] = evidence["readback_id"]
        compiled.append(item)
    return compiled


def compile_route(
    evidence: Any,
    runtime: Any,
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    _raise_first_evidence_error(evidence, ontologies)
    runtime_errors, recurrence = _runtime_errors_and_recurrence(runtime, ontologies)
    if runtime_errors:
        raise CompilationError("runtime_input_invalid", runtime_errors)
    assert isinstance(evidence, Mapping)
    assert isinstance(runtime, Mapping)

    facts = list(evidence["fact_ids"])
    fact_entries = {
        str(entry["id"]): entry for entry in ontologies.fact_entries
    }
    hard_gates = [fact_id for fact_id in facts if fact_entries[fact_id]["hard_gate"]]
    elevated = [
        fact_id
        for fact_id in facts
        if fact_entries[fact_id]["routing_class"]
        in {"integration", "elevated_risk"}
    ]
    eligible = _eligible_luna_max_facts(facts)
    derived_flags: list[str] = []
    for fact_id in facts:
        for flag in fact_entries[fact_id]["derived_flags"]:
            if flag not in derived_flags:
                derived_flags.append(flag)
    risk_rank = {"low": 0, "elevated": 1, "critical": 2}
    risk_level = "low"
    for fact_id in facts:
        projection = str(fact_entries[fact_id]["risk_projection"])
        if risk_rank[projection] > risk_rank[risk_level]:
            risk_level = projection

    final_review = (
        runtime["task_phase"] == "final_review"
        and runtime["review_scope"] == "branch"
    )
    if final_review or recurrence.active or hard_gates:
        worker = ("gpt-5.6-sol", "max")
    elif elevated:
        worker = ("gpt-5.6-sol", "xhigh")
    elif eligible:
        worker = ("gpt-5.6-luna", "max")
    else:
        worker = ("gpt-5.6-luna", "xhigh")
    review = _REVIEW_ROUTES[worker]

    if final_review:
        role = "final_review"
    elif "audit" in facts:
        role = "audit"
    elif "reconciliation" in facts:
        role = "reconciliation"
    elif "judgment_required" in facts:
        role = "judgment"
    else:
        role = "implementation"

    if final_review:
        reason_id = "branch_final_review"
    elif recurrence.active:
        assert recurrence.reason_id is not None
        reason_id = recurrence.reason_id
    elif hard_gates:
        reason_id = hard_gates[0]
    elif elevated:
        reason_id = elevated[0]
    elif eligible:
        reason_id = eligible[0]
    else:
        reason_id = "default_clear_isolated_work"
    justification = (
        f"{reason_id} requires {worker[0]}/{worker[1]}; task review is "
        f"{review[0]}/{review[1]}."
    )

    return {
        "schema_version": "cmr-route-decision-v1",
        "task_id": evidence["task_id"],
        "role": role,
        "brief": dict(evidence["brief"]),
        "fact_ids": facts,
        "uncertainty_ids": list(evidence["uncertainty_ids"]),
        "risk_level": risk_level,
        "risk_signals": list(facts),
        "hard_gates": hard_gates,
        "derived_flags": derived_flags,
        "recurrence": recurrence.to_dict(),
        "worker": {"model": worker[0], "reasoning_effort": worker[1]},
        "review": {"model": review[0], "reasoning_effort": review[1]},
        "execution": {
            "fork_turns": "none",
            "context_mode": "fresh",
            "execution_engine": "superpowers-sdd",
        },
        "status": "ready",
        "blocker_ids": [],
        "external_actions": _compile_external_actions(
            evidence["action_intents"], runtime["authorization_registry"]
        ),
        "decisive_reason_id": reason_id,
        "justification": justification,
    }


def _artifact_ref_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "sha256", "size_bytes"],
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "pattern": _ARTIFACT_PATH_PATTERN,
            },
            "sha256": {
                "type": "string",
                "pattern": cmr_contracts.LOWER_SHA256_PATTERN,
            },
            "size_bytes": {"type": "integer", "minimum": 0},
        },
    }


def _usage_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_USAGE_FIELDS),
        "properties": {
            field: {"type": "integer", "minimum": 0} for field in _USAGE_FIELDS
        },
    }


def _control_cwd_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "path", "fresh", "destroyed", "read_only"],
        "properties": {
            "kind": {"const": "ephemeral"},
            "path": {"type": "string", "pattern": r"^(?:/|[A-Za-z]:/).+"},
            "fresh": {"const": True},
            "destroyed": {"const": True},
            "read_only": {"const": True},
        },
    }


def _occurrence_evidence_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_OCCURRENCE_FIELDS),
        "properties": {
            "schema_version": {"const": "cmr-occurrence-audit-v1"},
            "phase": {"const": "controller"},
            "scope_kind": {"const": "task"},
            "scope_id": {"type": "string", "minLength": 1},
            "round_index": {"type": "null"},
            "task_id": {"type": "string", "minLength": 1},
            "response_policy": {"const": "closed_json"},
            "model": {"const": "gpt-5.6-luna"},
            "reasoning_effort": {"const": "xhigh"},
            "fork_turns": {"const": "none"},
            "prompt_sha256": {
                "type": "string",
                "pattern": cmr_contracts.LOWER_SHA256_PATTERN,
            },
            "thread_policy": {"const": "fresh"},
            "thread_id": {"type": "string", "minLength": 1},
            "cwd": {"$ref": "#/$defs/CwdEvidence"},
            "write_policy": {"const": "control_plane_no_write"},
            "exit_code": {"const": 0},
            "external_writes": {"const": False},
            "transcript_sha256": {
                "type": "string",
                "pattern": cmr_contracts.LOWER_SHA256_PATTERN,
            },
            "raw_sha256": {
                "type": "string",
                "pattern": cmr_contracts.LOWER_SHA256_PATTERN,
            },
            "usage": {"$ref": "#/$defs/UsageEvidence"},
            "tool_event_count": {"const": 0},
            "terminal_report_sha256": {"type": "null"},
            "ignored_empty_agent_messages": {"type": "integer", "minimum": 0},
            "accepted": {"const": True},
            "failure_class": {"type": "null"},
            "errors": {"type": "array", "maxItems": 0},
        },
    }


def _semantic_schema_properties(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    return {
        "fact_ids": {
            "type": "array",
            "items": {"type": "string", "enum": list(ontologies.fact_ids)},
            "uniqueItems": True,
        },
        "uncertainty_ids": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(ontologies.uncertainty_ids),
            },
            "uniqueItems": True,
        },
        "action_intents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action"],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(ontologies.action_ids),
                    },
                    "target": {"type": "string", "minLength": 1},
                },
            },
            "uniqueItems": True,
        },
    }


def _route_evidence_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    properties = {
        "schema_version": {"const": "cmr-route-evidence-v2"},
        "task_id": {"type": "string", "minLength": 1},
        "brief": {"$ref": "#/$defs/ArtifactRef"},
        **_semantic_schema_properties(ontologies),
        "raw": {"$ref": "#/$defs/ArtifactRef"},
        "occurrence": {"$ref": "#/$defs/OccurrenceEvidence"},
    }
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-route-evidence-v2.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "task_id",
            "brief",
            "fact_ids",
            "uncertainty_ids",
            "action_intents",
            "raw",
            "occurrence",
        ],
        "properties": properties,
        "$defs": {
            "ArtifactRef": _artifact_ref_schema(),
            "UsageEvidence": _usage_schema(),
            "CwdEvidence": _control_cwd_schema(),
            "OccurrenceEvidence": _occurrence_evidence_schema(),
        },
    }


def _runtime_input_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-runtime-input-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "task_phase",
            "current_defect_id",
            "failure_history",
            "review_scope",
            "authorization_registry",
        ],
        "properties": {
            "schema_version": {"const": "cmr-runtime-input-v1"},
            "task_phase": {
                "type": "string",
                "enum": ["implementation", "final_review"],
            },
            "current_defect_id": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "null"},
                ]
            },
            "failure_history": {
                "type": "array",
                "items": {"$ref": "#/$defs/HistoryEntry"},
            },
            "review_scope": {"type": "string", "enum": ["task", "branch"]},
            "authorization_registry": {
                "$ref": "#/$defs/AuthorizationRegistry"
            },
        },
        "allOf": [
            {
                "if": {
                    "properties": {"task_phase": {"const": "final_review"}},
                    "required": ["task_phase"],
                },
                "then": {
                    "properties": {"review_scope": {"const": "branch"}}
                },
            },
            {
                "if": {
                    "properties": {"failure_history": {"minItems": 1}},
                    "required": ["failure_history"],
                },
                "then": {
                    "properties": {
                        "current_defect_id": {
                            "type": "string",
                            "minLength": 1,
                        }
                    }
                },
            },
        ],
        "$defs": {
            "HistoryEntry": {
                "type": "object",
                "additionalProperties": False,
                "required": ["defect_id", "event"],
                "properties": {
                    "defect_id": {"type": "string", "minLength": 1},
                    "event": {
                        "type": "string",
                        "enum": [
                            "attempt_completed_failed",
                            "attempt_completed_passed",
                            "declared_resolved",
                            "reappeared",
                        ],
                    },
                },
            },
            "AuthorizationRegistry": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "entries"],
                "properties": {
                    "schema_version": {
                        "const": "cmr-authorization-registry-v1"
                    },
                    "entries": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/AuthorizationEntry"},
                        "uniqueItems": True,
                    },
                },
            },
            "AuthorizationEntry": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "target",
                    "authorization_id",
                    "preview_sha256",
                ],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(ontologies.action_ids),
                    },
                    "target": {"type": "string", "minLength": 1},
                    "authorization_id": {"type": "string", "minLength": 1},
                    "preview_sha256": {
                        "type": "string",
                        "pattern": cmr_contracts.LOWER_SHA256_PATTERN,
                    },
                    "readback_id": {"type": "string", "minLength": 1},
                },
            },
        },
    }


def _route_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "reasoning_effort"],
        "properties": {
            "model": {
                "type": "string",
                "enum": ["gpt-5.6-luna", "gpt-5.6-sol"],
            },
            "reasoning_effort": {"type": "string", "enum": ["xhigh", "max"]},
        },
    }


def _review_route_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["model", "reasoning_effort"],
                "properties": {
                    "model": {"const": model},
                    "reasoning_effort": {"const": effort},
                },
            }
            for model, effort in (
                ("gpt-5.6-luna", "max"),
                ("gpt-5.6-sol", "xhigh"),
                ("gpt-5.6-sol", "max"),
            )
        ]
    }


def _recurrence_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["active", "reason_id", "defect_id"],
                "properties": {
                    "active": {"const": False},
                    "reason_id": {"type": "null"},
                    "defect_id": {"type": "null"},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["active", "reason_id", "defect_id"],
                "properties": {
                    "active": {"const": True},
                    "reason_id": {
                        "type": "string",
                        "enum": [
                            "regression_after_declared_resolved",
                            "recurring_failure",
                        ],
                    },
                    "defect_id": {"type": "string", "minLength": 1},
                },
            },
        ]
    }


def _external_action_properties(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    return {
        "action": {"type": "string", "enum": list(ontologies.action_ids)},
        "target": {"type": "string", "minLength": 1},
        "state": {"type": "string", "enum": ["blocked", "ready", "completed"]},
        "authorized": {"type": "boolean"},
        "preview": {"type": "boolean"},
        "readback": {"type": "boolean"},
        "blocker_ids": {
            "type": "array",
            "items": {"const": "external_target_missing"},
            "uniqueItems": True,
        },
        "authorization_id": {"type": "string", "minLength": 1},
        "preview_sha256": {
            "type": "string",
            "pattern": cmr_contracts.LOWER_SHA256_PATTERN,
        },
        "readback_id": {"type": "string", "minLength": 1},
    }


def _external_action_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    props = _external_action_properties(ontologies)
    common = ["action", "state", "authorized", "preview", "readback", "blocker_ids"]
    return {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": common,
                "properties": {
                    **{key: props[key] for key in common},
                    "state": {"const": "blocked"},
                    "authorized": {"const": False},
                    "preview": {"const": False},
                    "readback": {"const": False},
                    "blocker_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {"const": "external_target_missing"},
                    },
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "target",
                    "state",
                    "authorized",
                    "preview",
                    "readback",
                    "blocker_ids",
                ],
                "properties": {
                    **{
                        key: props[key]
                        for key in [
                            "action",
                            "target",
                            "state",
                            "authorized",
                            "preview",
                            "readback",
                            "blocker_ids",
                        ]
                    },
                    "state": {"const": "blocked"},
                    "authorized": {"const": False},
                    "preview": {"const": False},
                    "readback": {"const": False},
                    "blocker_ids": {"type": "array", "maxItems": 0},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "target",
                    "state",
                    "authorized",
                    "preview",
                    "readback",
                    "blocker_ids",
                    "authorization_id",
                    "preview_sha256",
                ],
                "properties": {
                    **{
                        key: props[key]
                        for key in [
                            "action",
                            "target",
                            "state",
                            "authorized",
                            "preview",
                            "readback",
                            "blocker_ids",
                            "authorization_id",
                            "preview_sha256",
                        ]
                    },
                    "state": {"const": "ready"},
                    "authorized": {"const": True},
                    "preview": {"const": True},
                    "readback": {"const": False},
                    "blocker_ids": {"type": "array", "maxItems": 0},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "target",
                    "state",
                    "authorized",
                    "preview",
                    "readback",
                    "blocker_ids",
                    "authorization_id",
                    "preview_sha256",
                    "readback_id",
                ],
                "properties": {
                    **props,
                    "state": {"const": "completed"},
                    "authorized": {"const": True},
                    "preview": {"const": True},
                    "readback": {"const": True},
                    "blocker_ids": {"type": "array", "maxItems": 0},
                },
            },
        ]
    }


def _route_decision_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    hard_gate_ids = [
        str(entry["id"]) for entry in ontologies.fact_entries if entry["hard_gate"]
    ]
    derived_flag_ids: list[str] = []
    for entry in ontologies.fact_entries:
        for flag in entry["derived_flags"]:
            if flag not in derived_flag_ids:
                derived_flag_ids.append(str(flag))
    reason_ids = [
        str(entry["id"])
        for entry in ontologies.fact_entries
        if entry["hard_gate"]
        or entry["routing_class"]
        in {
            "integration",
            "elevated_risk",
            "audit",
            "judgment",
            "reconciliation",
            "complex_local",
        }
    ] + [
        "branch_final_review",
        "regression_after_declared_resolved",
        "recurring_failure",
        "default_clear_isolated_work",
    ]
    required = [
        "schema_version",
        "task_id",
        "role",
        "brief",
        "fact_ids",
        "uncertainty_ids",
        "risk_level",
        "risk_signals",
        "hard_gates",
        "derived_flags",
        "recurrence",
        "worker",
        "review",
        "execution",
        "status",
        "blocker_ids",
        "external_actions",
        "decisive_reason_id",
        "justification",
    ]
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-route-decision-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "schema_version": {"const": "cmr-route-decision-v1"},
            "task_id": {"type": "string", "minLength": 1},
            "role": {
                "type": "string",
                "enum": [
                    "final_review",
                    "audit",
                    "reconciliation",
                    "judgment",
                    "implementation",
                ],
            },
            "brief": {"$ref": "#/$defs/ArtifactRef"},
            "fact_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(ontologies.fact_ids)},
                "uniqueItems": True,
            },
            "uncertainty_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(ontologies.uncertainty_ids)},
                "uniqueItems": True,
            },
            "risk_level": {"type": "string", "enum": ["low", "elevated", "critical"]},
            "risk_signals": {
                "type": "array",
                "items": {"type": "string", "enum": list(ontologies.fact_ids)},
                "uniqueItems": True,
            },
            "hard_gates": {
                "type": "array",
                "items": {"type": "string", "enum": hard_gate_ids},
                "uniqueItems": True,
            },
            "derived_flags": {
                "type": "array",
                "items": {"type": "string", "enum": derived_flag_ids},
                "uniqueItems": True,
            },
            "recurrence": {"$ref": "#/$defs/Recurrence"},
            "worker": {"$ref": "#/$defs/Route"},
            "review": {"$ref": "#/$defs/ReviewRoute"},
            "execution": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fork_turns", "context_mode", "execution_engine"],
                "properties": {
                    "fork_turns": {"const": "none"},
                    "context_mode": {"const": "fresh"},
                    "execution_engine": {"const": "superpowers-sdd"},
                },
            },
            "status": {"const": "ready"},
            "blocker_ids": {"type": "array", "maxItems": 0},
            "external_actions": {
                "type": "array",
                "items": {"$ref": "#/$defs/ExternalAction"},
                "uniqueItems": True,
            },
            "decisive_reason_id": {"type": "string", "enum": reason_ids},
            "justification": {"type": "string", "minLength": 1},
        },
        "$defs": {
            "ArtifactRef": _artifact_ref_schema(),
            "Route": _route_schema(),
            "ReviewRoute": _review_route_schema(),
            "Recurrence": _recurrence_schema(),
            "ExternalAction": _external_action_schema(ontologies),
        },
    }


def expected_compiler_schemas(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, dict[str, Any]]:
    schemas = {
        "cmr-route-evidence-v2.schema.json": _route_evidence_schema(ontologies),
        "cmr-runtime-input-v1.schema.json": _runtime_input_schema(ontologies),
        "cmr-route-decision-v1.schema.json": _route_decision_schema(ontologies),
    }
    # Task 3 owns the artifact schemas while Task 1's parity gate remains the
    # single published-contract entry point.
    from cmr_dispatch import expected_dispatch_schemas

    schemas.update(expected_dispatch_schemas(ontologies))
    return schemas


__all__ = [
    "ArtifactRef",
    "BoundPreflightResult",
    "CompilationError",
    "PreflightTransition",
    "Recurrence",
    "SELECTOR_TRIGGER_IDS",
    "apply_preflight",
    "bind_controller_result",
    "bind_preflight_result",
    "compile_route",
    "derive_recurrence",
    "expected_compiler_schemas",
    "validate_route_evidence",
    "validate_runtime_input",
    "validate_selector",
    "validate_preflight_monotonicity",
]
