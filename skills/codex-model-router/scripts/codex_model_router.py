#!/usr/bin/env python3
"""Small, deterministic command-line facade for the public CMR contracts.

The CLI deliberately accepts JSON files and returns JSON.  It does not call a
model, run a shell command, contact a service, or infer route fields from a
model-authored object.  The sibling contract modules remain the single source
of truth for validation and compilation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

SCRIPT_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import cmr_compiler
import cmr_confirmation_accounting
import cmr_contracts
import cmr_dispatch
import cmr_gate_evidence
import cmr_runtime
import qfr_legacy


class CliError(ValueError):
    """An input or contract error suitable for a concise JSON response."""


def _read_json(path: str | Path, label: str) -> Any:
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise CliError(f"{label} could not be read as JSON: {exc}") from exc
    try:
        return cmr_contracts.strict_json_object(payload, label)
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CliError(f"{label} must be a JSON object")
    return value


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CliError(f"value is not JSON serializable: {exc}") from exc


def _emit(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True))


def _error_result(command: str, errors: list[str]) -> dict[str, Any]:
    return {"command": command, "valid": False, "errors": sorted(set(errors))}


def _validate_controller(path: str) -> tuple[int, dict[str, Any]]:
    value = _read_json(path, "controller result")
    errors = cmr_contracts.validate_controller_result(
        value, cmr_contracts.load_ontologies(SKILL_ROOT)
    )
    result = {"command": "validate-controller", "valid": not errors, "errors": errors}
    return (0 if not errors else 1), result


def _read_compile_json(path: str, label: str) -> dict[str, Any]:
    """Use the ledger's frozen descriptor/readback contract for compile inputs."""
    absolute = Path(path).absolute()
    root = absolute.parent
    descriptor, root_stat = cmr_dispatch._open_root(root)
    try:
        payload, reference, _ = cmr_dispatch._read_artifact_path(descriptor, absolute, root)
        value = cmr_contracts.strict_json_object(payload, label)
        cmr_dispatch._read_reference(descriptor, reference)
        if not cmr_dispatch._root_identity_unchanged(root, root_stat):
            raise CliError("compile input directory identity changed during readback")
        return value
    finally:
        os.close(descriptor)


def _compile(path: str, runtime_path: str | None = None, *,
             gate_evidence_path: str | None, brief_path: str | None) -> tuple[int, dict[str, Any]]:
    if gate_evidence_path is None or brief_path is None:
        raise CliError("compile requires explicit --gate-evidence and --brief paths")
    request = _object(_read_compile_json(path, "compile input"), "compile input")
    if runtime_path is not None:
        evidence = request
        runtime = _object(_read_compile_json(runtime_path, "runtime input"), "runtime input")
    else:
        evidence = request.get("evidence", request.get("route_evidence"))
        runtime = request.get("runtime", request.get("runtime_input"))
    if evidence is None or runtime is None:
        raise CliError("compile input must contain evidence and runtime objects")
    ontologies = cmr_contracts.load_ontologies(SKILL_ROOT)
    try:
        evidence = _object(evidence, "route evidence")
        runtime = _object(runtime, "runtime input")
        cmr_compiler._raise_first_evidence_error(evidence, ontologies)
        runtime_errors = cmr_compiler.validate_runtime_input(runtime, ontologies)
        if runtime_errors:
            raise cmr_compiler.CompilationError("runtime_input_invalid", runtime_errors)
        brief_ref = cmr_compiler.ArtifactRef.from_value(evidence.get("brief"))
        supplied_brief = Path(brief_path).absolute()
        brief_parts = Path(brief_ref.path).parts
        if supplied_brief.parts[-len(brief_parts):] != brief_parts:
            raise CliError("--brief path does not bind route evidence brief.path")
        ledger_root = supplied_brief.parents[len(brief_parts) - 1]
        descriptor, root_stat = cmr_dispatch._open_root(ledger_root)
        try:
            brief_bytes, _ = cmr_dispatch._read_reference(descriptor, brief_ref)
            gate_bytes, _, _ = cmr_dispatch._read_artifact_path(
                descriptor, Path(gate_evidence_path).absolute(), ledger_root)
            gate_value = cmr_contracts.strict_json_object(gate_bytes, "gate evidence")
            bound_gate = cmr_gate_evidence.bind_gate_evidence(gate_value,
                context=cmr_compiler.gate_evidence_context(evidence, runtime),
                brief_bytes=brief_bytes, ontologies=ontologies)
            decision = cmr_compiler.compile_route(evidence, runtime, ontologies,
                gate_evidence=bound_gate, brief_bytes=brief_bytes)
            if not cmr_dispatch._root_identity_unchanged(ledger_root, root_stat):
                raise CliError("ledger root identity changed during compile")
        finally:
            os.close(descriptor)
    except cmr_compiler.CompilationError as exc:
        result = _error_result("compile", list(exc.errors) or [str(exc)])
        result["blocker_id"] = exc.blocker_id
        return 1, result
    except (TypeError, ValueError, KeyError) as exc:
        return 1, _error_result("compile", [str(exc)])
    return 0, {"command": "compile", "valid": True, "errors": [], "decision": decision}


def _validate_dispatch(path: str, ledger_root: str | None) -> tuple[int, dict[str, Any]]:
    bundle = Path(path)
    root = Path(ledger_root) if ledger_root is not None else bundle.parent.parent.parent
    errors = cmr_dispatch.validate_dispatch_bundle(bundle, root)
    return (0 if not errors else 1), {
        "command": "validate-dispatch",
        "valid": not errors,
        "errors": errors,
    }


def _validate_legacy(path: str) -> tuple[int, dict[str, Any]]:
    record = _read_json(path, "legacy record")
    messages = qfr_legacy.validate_legacy_decision(record)
    valid = messages == [qfr_legacy.LEGACY_SUCCESS_MESSAGE]
    return (0 if valid else 1), {
        "command": "validate-legacy",
        "valid": valid,
        "documentary": True,
        "messages": messages,
        "errors": [] if valid else messages,
    }


def _dispatch_request(path: str, ledger_root: str) -> tuple[int, dict[str, Any]]:
    try:
        request = cmr_dispatch.dispatch_request(Path(path), Path(ledger_root))
    except cmr_dispatch.DispatchError as exc:
        result = _error_result("dispatch-request", list(exc.errors) or [str(exc)])
        result["blocker_id"] = exc.blocker_id
        return 1, result
    return 0, {
        "command": "dispatch-request", "valid": True, "errors": [],
        "dispatch_request": request,
    }


def _expectation(
    value: Mapping[str, Any],
) -> tuple[cmr_runtime.OccurrenceExpectation, tuple[str, ...] | None]:
    required = {
        "phase", "scope_kind", "scope_id", "round_index", "task_id", "model",
        "reasoning_effort", "prompt_sha256", "forbidden_thread_ids", "prior_thread_id",
        "fork_turns", "thread_policy", "cwd_policy", "write_policy", "response_policy",
    }
    missing = sorted(required - set(value))
    if missing:
        raise CliError(f"occurrence expectation missing fields: {', '.join(missing)}")
    planning_task_ids: tuple[str, ...] | None = None
    if value.get("phase") == "planning":
        if "planning_task_ids" not in value:
            raise CliError("occurrence expectation planning_task_ids is required for planning")
        task_ids_value = value["planning_task_ids"]
        if not isinstance(task_ids_value, list):
            raise CliError("occurrence expectation planning_task_ids must be a JSON array")
        if any(not isinstance(task_id, str) or not task_id for task_id in task_ids_value):
            raise CliError(
                "occurrence expectation planning_task_ids must contain only non-empty strings"
            )
        if len(task_ids_value) != len(set(task_ids_value)):
            raise CliError("occurrence expectation planning_task_ids must be unique")
        planning_task_ids = tuple(task_ids_value)
    elif "planning_task_ids" in value:
        raise CliError(
            "occurrence expectation planning_task_ids is only valid for planning"
        )
    try:
        expected = cmr_runtime.OccurrenceExpectation(
            phase=value["phase"],
            scope_kind=value["scope_kind"],
            scope_id=value["scope_id"],
            round_index=value["round_index"],
            task_id=value["task_id"],
            model=value["model"],
            reasoning_effort=value["reasoning_effort"],
            prompt_sha256=value["prompt_sha256"],
            forbidden_thread_ids=tuple(value["forbidden_thread_ids"]),
            prior_thread_id=value["prior_thread_id"],
            fork_turns=value["fork_turns"],
            thread_policy=value["thread_policy"],
            cwd_policy=value["cwd_policy"],
            write_policy=value["write_policy"],
            response_policy=value["response_policy"],
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise CliError(f"occurrence expectation is invalid: {exc}") from exc
    return expected, planning_task_ids


def _response_validator(
    expected: cmr_runtime.OccurrenceExpectation,
    planning_task_ids: tuple[str, ...] | None,
):
    ontologies = cmr_contracts.load_ontologies(SKILL_ROOT)
    validators = {
        "controller": lambda value: cmr_contracts.validate_controller_result(value, ontologies),
        "planning": lambda value: cmr_contracts.validate_planner_result(
            value, planning_task_ids, ontologies
        ),
        "preflight": lambda value: cmr_contracts.validate_preflight_result(value, ontologies),
    }
    return validators.get(expected.phase)


def _audit_occurrence(raw_path: str, metadata_path: str, expected_path: str) -> tuple[int, dict[str, Any]]:
    raw = Path(raw_path).read_bytes()
    metadata_value = dict(_object(_read_json(metadata_path, "occurrence metadata"), "occurrence metadata"))
    expected, planning_task_ids = _expectation(
        _object(
            _read_json(expected_path, "occurrence expectation"),
            "occurrence expectation",
        )
    )
    transcript = metadata_value.get("transcript_payload")
    if isinstance(transcript, str):
        metadata_value["transcript_payload"] = transcript.encode("utf-8")
    occurrence = cmr_runtime.audit_occurrence(
        raw,
        metadata_value,
        expected,
        _response_validator(expected, planning_task_ids),
    )
    record = occurrence.to_record()
    result = {"command": "audit-occurrence", **record}
    return (0 if occurrence.accepted else 1), result


def _occurrence(value: Any, index: int) -> cmr_runtime.OccurrenceAudit:
    try:
        return cmr_runtime.OccurrenceAudit.from_record(
            value,
            label=f"occurrences[{index}]",
        )
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def _task_state(value: Any, index: int) -> cmr_runtime.TaskAccountingState:
    item = _object(value, f"task_states[{index}]")
    try:
        return cmr_runtime.TaskAccountingState(
            task_id=item["task_id"], selector=item["selector"], dispatchable=item["dispatchable"],
            completed_worker_rounds=tuple(item["completed_worker_rounds"]),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise CliError(f"task_states[{index}] is invalid: {exc}") from exc


def _release_review(value: Any) -> cmr_runtime.ReleaseReviewState | None:
    if value is None:
        return None
    item = _object(value, "release_review")
    try:
        return cmr_runtime.ReleaseReviewState(
            initial_implementation_head=item["initial_implementation_head"],
            initial_export_head=item["initial_export_head"], fix_required=item["fix_required"],
            final_implementation_head=item["final_implementation_head"],
            final_export_head=item["final_export_head"],
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise CliError(f"release_review is invalid: {exc}") from exc


def _check_call_shape(path: str) -> tuple[int, dict[str, Any]]:
    request = _object(_read_json(path, "call-shape input"), "call-shape input")
    values = request.get("occurrences", [])
    if not isinstance(values, list):
        raise CliError("occurrences must be a JSON array")
    states_value = request.get("task_states", [])
    if not isinstance(states_value, list):
        raise CliError("task_states must be a JSON array")
    occurrences = [_occurrence(value, index) for index, value in enumerate(values)]
    states = [_task_state(value, index) for index, value in enumerate(states_value)]
    errors = cmr_runtime.validate_call_shape(
        occurrences, states, plan_sha256=request.get("plan_sha256"),
        release_review=_release_review(request.get("release_review")),
    )
    return (0 if not errors else 1), {"command": "check-call-shape", "valid": not errors, "errors": errors}


def _check_execution_call_shape(path: str, ledger_root: str) -> tuple[int, dict[str, Any]]:
    request = _read_json(path, "execution accounting input")
    errors = cmr_confirmation_accounting.validate_execution_accounting_input(request)
    if errors:
        return 1, _error_result("check-execution-call-shape", errors)
    occurrences = [_occurrence(value, index) for index, value in enumerate(request["occurrences"])]
    states = [_task_state(value, index) for index, value in enumerate(request["task_states"])]
    report = cmr_confirmation_accounting.summarize_execution_accounting(
        occurrences, states, request["semantic_results"], request["instrument_errors"],
        plan_sha256=request["plan_sha256"], release_review=_release_review(request["release_review"]),
        task_preflight_evidence=request["task_preflight_evidence"], ledger_root=Path(ledger_root))
    errors = report["accounting_errors"]
    valid = report["canonical_call_shape"] and not errors
    return (0 if valid else 1), {"command": "check-execution-call-shape", "valid": valid,
        "errors": errors, "report": report}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex_model_router.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-controller").add_argument("record")
    compile_parser = sub.add_parser("compile")
    compile_parser.add_argument("input")
    compile_parser.add_argument("runtime", nargs="?")
    compile_parser.add_argument("--gate-evidence")
    compile_parser.add_argument("--brief")
    dispatch_parser = sub.add_parser("validate-dispatch")
    dispatch_parser.add_argument("bundle")
    dispatch_parser.add_argument("ledger_root", nargs="?")
    request_parser = sub.add_parser("dispatch-request")
    request_parser.add_argument("bundle")
    request_parser.add_argument("ledger_root")
    sub.add_parser("validate-legacy").add_argument("record")
    audit_parser = sub.add_parser("audit-occurrence")
    audit_parser.add_argument("raw")
    audit_parser.add_argument("metadata")
    audit_parser.add_argument("expected")
    sub.add_parser("check-call-shape").add_argument("input")
    accounting_parser = sub.add_parser("check-execution-call-shape")
    accounting_parser.add_argument("input")
    accounting_parser.add_argument("ledger_root")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            # Keep invalid user data on the public one-object/exit-1 contract;
            # argparse's normal help path remains the only intentional exit 0.
            if exc.code == 0:
                return 0
            result = _error_result("cli", ["invalid command arguments"])
            _emit(result)
            print("cli: invalid command arguments", file=sys.stderr)
            return 1
        if args.command == "validate-controller":
            code, result = _validate_controller(args.record)
        elif args.command == "compile":
            code, result = _compile(args.input, args.runtime,
                gate_evidence_path=args.gate_evidence, brief_path=args.brief)
        elif args.command == "validate-dispatch":
            code, result = _validate_dispatch(args.bundle, args.ledger_root)
        elif args.command == "dispatch-request":
            code, result = _dispatch_request(args.bundle, args.ledger_root)
        elif args.command == "validate-legacy":
            code, result = _validate_legacy(args.record)
        elif args.command == "audit-occurrence":
            code, result = _audit_occurrence(args.raw, args.metadata, args.expected)
        elif args.command == "check-call-shape":
            code, result = _check_call_shape(args.input)
        elif args.command == "check-execution-call-shape":
            code, result = _check_execution_call_shape(args.input, args.ledger_root)
        else:  # pragma: no cover - argparse enforces the command choices.
            raise CliError("unknown command")
    except CliError as exc:
        code, result = 1, _error_result(getattr(locals().get("args", None), "command", "cli"), [str(exc)])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        code, result = 1, _error_result(getattr(locals().get("args", None), "command", "cli"), [str(exc)])
    _emit(result)
    if code:
        errors = result.get("errors") or result.get("messages") or ["operation rejected"]
        print(f"{result.get('command', 'cli')}: {errors[0]}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
