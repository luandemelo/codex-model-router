from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import cmr_compiler
import cmr_contracts
import cmr_runtime

sys.dont_write_bytecode = True


ArtifactRef = cmr_compiler.ArtifactRef
BoundPreflightResult = cmr_compiler.BoundPreflightResult
PreflightTransition = cmr_compiler.PreflightTransition
bind_preflight_result = cmr_compiler.bind_preflight_result
apply_preflight = cmr_compiler.apply_preflight

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_TASK_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_SYSTEM_AND_PREFLIGHT_BLOCKERS = (
    "controller_result_invalid",
    "occurrence_invalid",
    "runtime_input_invalid",
    "artifact_binding_invalid",
    "preflight_result_invalid",
    "preflight_non_monotonic",
    "preflight_blocked",
    "dispatch_bundle_invalid",
    "legacy_dispatch_forbidden",
    *cmr_contracts.PREFLIGHT_BLOCKER_IDS,
)


class DispatchError(ValueError):
    def __init__(
        self,
        errors: Sequence[str] | str,
        blocker_id: str = "dispatch_bundle_invalid",
    ) -> None:
        details = [errors] if isinstance(errors, str) else list(errors)
        self.blocker_id = blocker_id
        self.blocker_ids = (blocker_id,)
        self.errors = tuple(sorted(set(details)))
        suffix = f": {'; '.join(self.errors)}" if self.errors else ""
        super().__init__(f"{blocker_id}{suffix}")


@dataclass(frozen=True)
class PlanningEvidence:
    result: Mapping[str, Any]
    plan: ArtifactRef
    briefs: Mapping[str, ArtifactRef]
    prompt: ArtifactRef
    raw: ArtifactRef
    transcript: ArtifactRef
    occurrence_ref: ArtifactRef
    occurrence: cmr_runtime.OccurrenceAudit


@dataclass(frozen=True)
class SafeLaneArtifacts:
    certificate: ArtifactRef
    planning_invocation: ArtifactRef
    manifest: ArtifactRef


@dataclass(frozen=True)
class PreflightArtifacts:
    invocation: ArtifactRef | None
    evidence: ArtifactRef
    replacement_evidence: ArtifactRef | None
    final_decision: ArtifactRef | None


def _frozen_ontologies() -> cmr_contracts.Ontologies:
    return cmr_contracts.Ontologies(
        fact_entries=tuple(cmr_contracts.FACT_ENTRIES),
        uncertainty_ids=tuple(cmr_contracts.UNCERTAINTY_IDS),
        action_entries=tuple(cmr_contracts.ACTION_ENTRIES),
        safe_lane_reason_ids=tuple(cmr_contracts.SAFE_LANE_REASON_IDS),
    )


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
        raise DispatchError("artifact is not canonical JSON") from exc


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


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _absolute_normalized_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if _CONTROL_RE.search(value):
        return False
    if value.startswith("/"):
        parts = value[1:].split("/")
    elif re.match(r"^[A-Za-z]:/", value):
        parts = value[3:].split("/")
    else:
        return False
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _ref_errors(value: Any, label: str) -> list[str]:
    try:
        ArtifactRef.from_value(value)
    except ValueError as exc:
        return [f"{label}: {exc}"]
    return []


def _ref(value: Any, label: str) -> ArtifactRef:
    try:
        return ArtifactRef.from_value(value)
    except ValueError as exc:
        raise DispatchError(f"{label}: {exc}") from exc


def _task_component(task_id: Any) -> str:
    if (
        not isinstance(task_id, str)
        or task_id in {".", ".."}
        or not _TASK_COMPONENT_RE.fullmatch(task_id)
    ):
        raise DispatchError("task_id cannot be materialized as a safe path component")
    return task_id


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _open_root(ledger_root: Path) -> tuple[int, os.stat_result]:
    root = Path(ledger_root)
    if not root.is_absolute():
        raise DispatchError("ledger root must be absolute")
    try:
        descriptor = os.open(root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        observed = os.fstat(descriptor)
    except OSError as exc:
        raise DispatchError("ledger root must be a nonsymlink directory") from exc
    if not stat.S_ISDIR(observed.st_mode):
        os.close(descriptor)
        raise DispatchError("ledger root must be a directory")
    return descriptor, observed


def _relative_parts(path: str) -> tuple[str, ...]:
    reference = ArtifactRef(path, "0" * 64, 0)
    return tuple(PurePosixPath(reference.path).parts)


def _open_relative(
    root_descriptor: int,
    relative_path: str,
    *,
    require_regular: bool,
) -> tuple[int, os.stat_result]:
    parts = _relative_parts(relative_path)
    if not parts:
        raise OSError("empty path")
    current = os.dup(root_descriptor)
    try:
        for index, component in enumerate(parts):
            final = index == len(parts) - 1
            flags = os.O_RDONLY | _NOFOLLOW
            if not final or not require_regular:
                flags |= _DIRECTORY
            opened = os.open(component, flags, dir_fd=current)
            observed = os.fstat(opened)
            expected = stat.S_ISREG if final and require_regular else stat.S_ISDIR
            if not expected(observed.st_mode):
                os.close(opened)
                raise OSError("artifact has wrong filesystem type")
            os.close(current)
            current = opened
        return current, os.fstat(current)
    except Exception:
        os.close(current)
        raise


def _read_reference(
    root_descriptor: int,
    reference: ArtifactRef,
    *,
    require_frozen: bool = True,
) -> tuple[bytes, os.stat_result]:
    descriptor = -1
    try:
        descriptor, opened = _open_relative(
            root_descriptor, reference.path, require_regular=True
        )
        if require_frozen and opened.st_mode & 0o222:
            raise OSError("artifact is writable")
        payload = _read_descriptor(descriptor)
        if len(payload) != reference.size_bytes:
            raise OSError("artifact size does not match reference")
        if hashlib.sha256(payload).hexdigest() != reference.sha256:
            raise OSError("artifact SHA-256 does not match reference")
        reopened, reopened_stat = _open_relative(
            root_descriptor, reference.path, require_regular=True
        )
        try:
            if not _same_identity(opened, reopened_stat):
                raise OSError("artifact identity changed during readback")
            if _read_descriptor(reopened) != payload:
                raise OSError("artifact changed during readback")
        finally:
            os.close(reopened)
        return payload, opened
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _strict_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        return cmr_contracts.strict_json_object(payload, label)
    except ValueError as exc:
        raise DispatchError(str(exc)) from exc


def _frozen_plan_entries(payload: bytes) -> list[tuple[str, ArtifactRef]]:
    plan = _strict_object(payload, "frozen plan")
    if not _nonempty_string(plan.get("schema_version")):
        raise DispatchError("frozen plan.schema_version must be non-empty")
    tasks = plan.get("tasks")
    if type(tasks) is not list:
        raise DispatchError("frozen plan.tasks must be an array")
    entries: list[tuple[str, ArtifactRef]] = []
    for index, task in enumerate(tasks):
        label = f"frozen plan.tasks[{index}]"
        field_errors = _exact_fields(task, {"task_id", "brief"}, label)
        if field_errors:
            raise DispatchError(field_errors)
        assert isinstance(task, Mapping)
        task_id = task.get("task_id")
        if not _nonempty_string(task_id):
            raise DispatchError(f"{label}.task_id must be non-empty")
        entries.append((task_id, _ref(task.get("brief"), f"{label}.brief")))
    task_ids = [task_id for task_id, _ in entries]
    if len(task_ids) != len(set(task_ids)):
        raise DispatchError("frozen plan task IDs must be unique")
    return entries


def _reference_for_payload(path: str, payload: bytes) -> ArtifactRef:
    return ArtifactRef(path, hashlib.sha256(payload).hexdigest(), len(payload))


def _ensure_directory(root_descriptor: int, parts: Sequence[str]) -> int:
    current = os.dup(root_descriptor)
    try:
        for component in parts:
            if component in {".", ".."} or not _TASK_COMPONENT_RE.fullmatch(
                component
            ):
                raise OSError("unsafe output path component")
            try:
                os.mkdir(component, mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            opened = os.open(
                component,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                dir_fd=current,
            )
            if not stat.S_ISDIR(os.fstat(opened).st_mode):
                os.close(opened)
                raise OSError("output ancestor is not a directory")
            os.close(current)
            current = opened
        return current
    except Exception:
        os.close(current)
        raise


def _exclusive_write(
    root_descriptor: int,
    relative_path: str,
    payload: bytes,
) -> ArtifactRef:
    parts = tuple(PurePosixPath(relative_path).parts)
    if not parts:
        raise DispatchError("output path is empty")
    parent = -1
    descriptor = -1
    try:
        parent = _ensure_directory(root_descriptor, parts[:-1])
        descriptor = os.open(
            parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o444,
            dir_fd=parent,
        )
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
        written_stat = os.fstat(descriptor)
        reference = _reference_for_payload(relative_path, payload)
        readback, readback_stat = _read_reference(root_descriptor, reference)
        if readback != payload or not _same_identity(written_stat, readback_stat):
            raise OSError("exclusive artifact changed during readback")
        return reference
    except (OSError, ValueError) as exc:
        raise DispatchError(f"artifact could not be created exclusively: {relative_path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


def _seal_directory(root_descriptor: int, relative_path: str) -> None:
    descriptor = -1
    try:
        descriptor, _ = _open_relative(
            root_descriptor, relative_path, require_regular=False
        )
        os.fchmod(descriptor, 0o555)
        os.fsync(descriptor)
    except OSError as exc:
        raise DispatchError(f"artifact directory could not be sealed: {relative_path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _directory_state(
    root_descriptor: int, relative_path: str
) -> tuple[set[str], os.stat_result]:
    descriptor = -1
    try:
        descriptor, opened = _open_relative(
            root_descriptor, relative_path, require_regular=False
        )
        return set(os.listdir(descriptor)), opened
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _directory_state_unchanged(
    root_descriptor: int,
    relative_path: str,
    expected_members: set[str],
    expected_stat: os.stat_result,
) -> bool:
    try:
        members, observed = _directory_state(root_descriptor, relative_path)
    except OSError:
        return False
    return bool(
        members == expected_members
        and _same_identity(expected_stat, observed)
        and stat.S_IMODE(expected_stat.st_mode) == stat.S_IMODE(observed.st_mode)
    )


def _root_identity_unchanged(
    ledger_root: Path, original: os.stat_result
) -> bool:
    try:
        current = os.stat(ledger_root, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISDIR(current.st_mode) and _same_identity(original, current)


def _artifact_ref_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "sha256", "size_bytes"],
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "pattern": cmr_compiler._ARTIFACT_PATH_PATTERN,
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


def _cwd_schema() -> dict[str, Any]:
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


def _safe_lane_entry_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id", "brief", "reason_ids"],
        "properties": {
            "task_id": {"type": "string", "minLength": 1},
            "brief": {"$ref": "#/$defs/ArtifactRef"},
            "reason_ids": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "string",
                    "enum": list(ontologies.safe_lane_reason_ids),
                },
                "uniqueItems": True,
            },
        },
    }


def _run_selector_schema() -> dict[str, Any]:
    allowed = list(cmr_compiler.SELECTOR_TRIGGER_IDS[1:])
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "trigger_ids"],
        "properties": {
            "decision": {"const": "run"},
            "trigger_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": allowed},
                "uniqueItems": True,
                "x-canonical-order": allowed,
            },
        },
    }


def _skip_selector_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "trigger_ids", "safe_lane"],
        "properties": {
            "decision": {"const": "skip"},
            "trigger_ids": {"type": "array", "maxItems": 0},
            "safe_lane": {"$ref": "#/$defs/ArtifactRef"},
        },
    }


def _block_selector_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "trigger_ids"],
        "properties": {
            "decision": {"const": "block"},
            "trigger_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "prefixItems": [{"const": "artifact_binding_invalid"}],
                "items": False,
            },
        },
    }


def _selector_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _skip_selector_schema(),
            {"$ref": "#/$defs/RunSelector"},
            _block_selector_schema(),
        ]
    }


def _safe_lane_certificate_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-safe-lane-certificate-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "plan", "entries"],
        "properties": {
            "schema_version": {"const": "cmr-safe-lane-certificate-v1"},
            "plan": {"$ref": "#/$defs/ArtifactRef"},
            "entries": {
                "type": "array",
                "items": {"$ref": "#/$defs/SafeLaneEntry"},
                "uniqueItems": True,
            },
        },
        "$defs": {
            "ArtifactRef": _artifact_ref_schema(),
            "SafeLaneEntry": _safe_lane_entry_schema(ontologies),
        },
    }


def _planning_invocation_schema() -> dict[str, Any]:
    required = [
        "schema_version",
        "phase",
        "purpose",
        "plan",
        "prompt",
        "raw",
        "transcript",
        "occurrence",
        "certificate",
        "model",
        "reasoning_effort",
        "fork_turns",
        "thread_id",
        "cwd",
        "exit_code",
        "external_writes",
        "usage",
    ]
    refs = {
        field: {"$ref": "#/$defs/ArtifactRef"}
        for field in (
            "plan",
            "prompt",
            "raw",
            "transcript",
            "occurrence",
            "certificate",
        )
    }
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-planning-invocation-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "schema_version": {"const": "cmr-planning-invocation-v1"},
            "phase": {"const": "planning"},
            "purpose": {"const": "canonical_planning"},
            **refs,
            "model": {"const": "gpt-5.6-sol"},
            "reasoning_effort": {"const": "max"},
            "fork_turns": {"const": "none"},
            "thread_id": {"type": "string", "minLength": 1},
            "cwd": {"$ref": "#/$defs/CwdEvidence"},
            "exit_code": {"const": 0},
            "external_writes": {"const": False},
            "usage": {"$ref": "#/$defs/UsageEvidence"},
        },
        "$defs": {
            "ArtifactRef": _artifact_ref_schema(),
            "UsageEvidence": _usage_schema(),
            "CwdEvidence": _cwd_schema(),
        },
    }


def _safe_lane_manifest_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-safe-lane-manifest-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "plan",
            "certificate",
            "planning_invocation",
            "entries",
        ],
        "properties": {
            "schema_version": {"const": "cmr-safe-lane-manifest-v1"},
            "plan": {"$ref": "#/$defs/ArtifactRef"},
            "certificate": {"$ref": "#/$defs/ArtifactRef"},
            "planning_invocation": {"$ref": "#/$defs/ArtifactRef"},
            "entries": {
                "type": "array",
                "items": {"$ref": "#/$defs/SafeLaneEntry"},
                "uniqueItems": True,
            },
        },
        "$defs": {
            "ArtifactRef": _artifact_ref_schema(),
            "SafeLaneEntry": _safe_lane_entry_schema(ontologies),
        },
    }


def _preflight_invocation_schema() -> dict[str, Any]:
    required = [
        "schema_version",
        "phase",
        "task_id",
        "brief",
        "candidate_evidence",
        "candidate_decision",
        "selector",
        "prompt",
        "raw",
        "transcript",
        "occurrence",
        "model",
        "reasoning_effort",
        "fork_turns",
        "thread_id",
        "cwd",
        "exit_code",
        "external_writes",
        "usage",
    ]
    refs = {
        field: {"$ref": "#/$defs/ArtifactRef"}
        for field in (
            "brief",
            "candidate_evidence",
            "candidate_decision",
            "prompt",
            "raw",
            "transcript",
            "occurrence",
        )
    }
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-preflight-invocation-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "schema_version": {"const": "cmr-preflight-invocation-v1"},
            "phase": {"const": "preflight"},
            "task_id": {"type": "string", "minLength": 1},
            **refs,
            "selector": {"$ref": "#/$defs/RunSelector"},
            "model": {"const": "gpt-5.6-luna"},
            "reasoning_effort": {"const": "max"},
            "fork_turns": {"const": "none"},
            "thread_id": {"type": "string", "minLength": 1},
            "cwd": {"$ref": "#/$defs/CwdEvidence"},
            "exit_code": {"const": 0},
            "external_writes": {"const": False},
            "usage": {"$ref": "#/$defs/UsageEvidence"},
        },
        "$defs": {
            "ArtifactRef": _artifact_ref_schema(),
            "UsageEvidence": _usage_schema(),
            "CwdEvidence": _cwd_schema(),
            "RunSelector": _run_selector_schema(),
        },
    }


def _preflight_evidence_schema() -> dict[str, Any]:
    common = {
        "schema_version": {"const": "cmr-preflight-evidence-v1"},
        "task_id": {"type": "string", "minLength": 1},
    }
    branches: list[dict[str, Any]] = []
    branch_specs = (
        (
            [
                "schema_version",
                "task_id",
                "selector",
                "outcome",
                "candidate_decision",
            ],
            {
                **common,
                "selector": _skip_selector_schema(),
                "outcome": {"const": "skipped"},
                "candidate_decision": {"$ref": "#/$defs/ArtifactRef"},
            },
        ),
        (
            [
                "schema_version",
                "task_id",
                "selector",
                "outcome",
                "candidate_decision",
                "invocation",
            ],
            {
                **common,
                "selector": {"$ref": "#/$defs/RunSelector"},
                "outcome": {"const": "accepted"},
                "candidate_decision": {"$ref": "#/$defs/ArtifactRef"},
                "invocation": {"$ref": "#/$defs/ArtifactRef"},
            },
        ),
        (
            [
                "schema_version",
                "task_id",
                "selector",
                "outcome",
                "candidate_decision",
                "invocation",
                "replacement_evidence",
                "final_decision",
            ],
            {
                **common,
                "selector": {"$ref": "#/$defs/RunSelector"},
                "outcome": {"const": "replaced"},
                "candidate_decision": {"$ref": "#/$defs/ArtifactRef"},
                "invocation": {"$ref": "#/$defs/ArtifactRef"},
                "replacement_evidence": {"$ref": "#/$defs/ArtifactRef"},
                "final_decision": {"$ref": "#/$defs/ArtifactRef"},
            },
        ),
        (
            ["schema_version", "task_id", "selector", "outcome", "blocker_ids"],
            {
                **common,
                "selector": _block_selector_schema(),
                "outcome": {"const": "blocked"},
                "blocker_ids": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "string",
                        "enum": list(_SYSTEM_AND_PREFLIGHT_BLOCKERS),
                    },
                    "uniqueItems": True,
                    "x-canonical-order": list(_SYSTEM_AND_PREFLIGHT_BLOCKERS),
                },
            },
        ),
        (
            [
                "schema_version",
                "task_id",
                "selector",
                "outcome",
                "candidate_decision",
                "invocation",
                "blocker_ids",
            ],
            {
                **common,
                "selector": {"$ref": "#/$defs/RunSelector"},
                "outcome": {"const": "blocked"},
                "candidate_decision": {"$ref": "#/$defs/ArtifactRef"},
                "invocation": {"$ref": "#/$defs/ArtifactRef"},
                "blocker_ids": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "string",
                        "enum": list(_SYSTEM_AND_PREFLIGHT_BLOCKERS),
                    },
                    "uniqueItems": True,
                    "x-canonical-order": list(_SYSTEM_AND_PREFLIGHT_BLOCKERS),
                },
            },
        ),
    )
    for required, properties in branch_specs:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            }
        )
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-preflight-evidence-v1.schema.json",
        "oneOf": branches,
        "$defs": {
            "ArtifactRef": _artifact_ref_schema(),
            "Selector": _selector_schema(),
            "RunSelector": _run_selector_schema(),
        },
    }


def _dispatch_bundle_schema() -> dict[str, Any]:
    fields = [
        "schema_version",
        "task_id",
        "brief",
        "controller_evidence",
        "runtime_input",
        "candidate_decision",
        "preflight_evidence",
        "final_decision",
    ]
    return {
        "$schema": cmr_contracts.JSON_SCHEMA_DIALECT,
        "$id": "cmr-dispatch-bundle-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": fields,
        "properties": {
            "schema_version": {"const": "cmr-dispatch-bundle-v1"},
            "task_id": {"type": "string", "minLength": 1},
            **{
                field: {"$ref": "#/$defs/ArtifactRef"}
                for field in fields[2:]
            },
        },
        "$defs": {"ArtifactRef": _artifact_ref_schema()},
    }


def expected_dispatch_schemas(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, dict[str, Any]]:
    return {
        "cmr-safe-lane-certificate-v1.schema.json": _safe_lane_certificate_schema(
            ontologies
        ),
        "cmr-planning-invocation-v1.schema.json": _planning_invocation_schema(),
        "cmr-safe-lane-manifest-v1.schema.json": _safe_lane_manifest_schema(
            ontologies
        ),
        "cmr-preflight-invocation-v1.schema.json": _preflight_invocation_schema(),
        "cmr-preflight-evidence-v1.schema.json": _preflight_evidence_schema(),
        "cmr-dispatch-bundle-v1.schema.json": _dispatch_bundle_schema(),
    }


def _usage_errors(value: Any, label: str) -> list[str]:
    errors = _exact_fields(value, set(_USAGE_FIELDS), label)
    if not isinstance(value, Mapping):
        return errors
    for field in _USAGE_FIELDS:
        amount = value.get(field)
        if type(amount) is not int or amount < 0:
            errors.append(f"{label}.{field} must be a nonnegative integer")
    return errors


def _cwd_errors(value: Any, label: str) -> list[str]:
    fields = {"kind", "path", "fresh", "destroyed", "read_only"}
    errors = _exact_fields(value, fields, label)
    if not isinstance(value, Mapping):
        return errors
    if value.get("kind") != "ephemeral":
        errors.append(f"{label}.kind must equal ephemeral")
    if not _absolute_normalized_path(value.get("path")):
        errors.append(f"{label}.path must be absolute and normalized")
    for field in ("fresh", "destroyed", "read_only"):
        if value.get(field) is not True:
            errors.append(f"{label}.{field} must be true")
    return errors


def _canonical_ids_errors(
    value: Any,
    allowed: Sequence[str],
    label: str,
    *,
    allow_empty: bool,
) -> list[str]:
    if type(value) is not list:
        return [f"{label} must be an array"]
    errors: list[str] = []
    if not allow_empty and not value:
        errors.append(f"{label} must be non-empty")
    if any(not isinstance(item, str) for item in value):
        errors.append(f"{label} must contain strings")
        return errors
    if len(value) != len(set(value)):
        errors.append(f"{label} must be unique")
    unknown = set(value) - set(allowed)
    if unknown:
        errors.append(f"{label} contains an unknown ID")
    expected = [item for item in allowed if item in set(value)]
    if value != expected:
        errors.append(f"{label} must use canonical order")
    return errors


def _safe_lane_entry_errors(
    value: Any,
    ontologies: cmr_contracts.Ontologies,
    label: str,
) -> list[str]:
    fields = {"task_id", "brief", "reason_ids"}
    errors = _exact_fields(value, fields, label)
    if not isinstance(value, Mapping):
        return errors
    if not _nonempty_string(value.get("task_id")):
        errors.append(f"{label}.task_id must be non-empty")
    errors += _ref_errors(value.get("brief"), f"{label}.brief")
    errors += _canonical_ids_errors(
        value.get("reason_ids"),
        ontologies.safe_lane_reason_ids,
        f"{label}.reason_ids",
        allow_empty=False,
    )
    return errors


def _certificate_errors(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> list[str]:
    fields = {"schema_version", "plan", "entries"}
    errors = _exact_fields(value, fields, "safe-lane certificate")
    if not isinstance(value, Mapping):
        return errors
    if value.get("schema_version") != "cmr-safe-lane-certificate-v1":
        errors.append("safe-lane certificate schema_version is invalid")
    errors += _ref_errors(value.get("plan"), "safe-lane certificate.plan")
    entries = value.get("entries")
    if type(entries) is not list:
        errors.append("safe-lane certificate.entries must be an array")
        return sorted(set(errors))
    task_ids: list[str] = []
    for index, entry in enumerate(entries):
        errors += _safe_lane_entry_errors(
            entry, ontologies, f"safe-lane certificate.entries[{index}]"
        )
        if isinstance(entry, Mapping) and isinstance(entry.get("task_id"), str):
            task_ids.append(entry["task_id"])
    if len(task_ids) != len(set(task_ids)):
        errors.append("safe-lane certificate task IDs must be unique")
    return sorted(set(errors))


def _planning_invocation_errors(value: Any) -> list[str]:
    fields = {
        "schema_version",
        "phase",
        "purpose",
        "plan",
        "prompt",
        "raw",
        "transcript",
        "occurrence",
        "certificate",
        "model",
        "reasoning_effort",
        "fork_turns",
        "thread_id",
        "cwd",
        "exit_code",
        "external_writes",
        "usage",
    }
    errors = _exact_fields(value, fields, "planning invocation")
    if not isinstance(value, Mapping):
        return errors
    constants = {
        "schema_version": "cmr-planning-invocation-v1",
        "phase": "planning",
        "purpose": "canonical_planning",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "max",
        "fork_turns": "none",
        "exit_code": 0,
        "external_writes": False,
    }
    for field, expected in constants.items():
        if not _json_type_equal(value.get(field), expected):
            errors.append(f"planning invocation.{field} is invalid")
    for field in ("plan", "prompt", "raw", "transcript", "occurrence", "certificate"):
        errors += _ref_errors(value.get(field), f"planning invocation.{field}")
    if not _nonempty_string(value.get("thread_id")):
        errors.append("planning invocation.thread_id must be non-empty")
    errors += _cwd_errors(value.get("cwd"), "planning invocation.cwd")
    errors += _usage_errors(value.get("usage"), "planning invocation.usage")
    return sorted(set(errors))


def _manifest_errors(
    value: Any, ontologies: cmr_contracts.Ontologies
) -> list[str]:
    fields = {
        "schema_version",
        "plan",
        "certificate",
        "planning_invocation",
        "entries",
    }
    errors = _exact_fields(value, fields, "safe-lane manifest")
    if not isinstance(value, Mapping):
        return errors
    if value.get("schema_version") != "cmr-safe-lane-manifest-v1":
        errors.append("safe-lane manifest schema_version is invalid")
    for field in ("plan", "certificate", "planning_invocation"):
        errors += _ref_errors(value.get(field), f"safe-lane manifest.{field}")
    entries = value.get("entries")
    if type(entries) is not list:
        errors.append("safe-lane manifest.entries must be an array")
        return sorted(set(errors))
    task_ids: list[str] = []
    for index, entry in enumerate(entries):
        errors += _safe_lane_entry_errors(
            entry, ontologies, f"safe-lane manifest.entries[{index}]"
        )
        if isinstance(entry, Mapping) and isinstance(entry.get("task_id"), str):
            task_ids.append(entry["task_id"])
    if len(task_ids) != len(set(task_ids)):
        errors.append("safe-lane manifest task IDs must be unique")
    return sorted(set(errors))


_DECISION_FIELDS = {
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
}


def _decision_errors(value: Any) -> list[str]:
    errors = _exact_fields(value, _DECISION_FIELDS, "route decision")
    if not isinstance(value, Mapping):
        return errors
    if value.get("schema_version") != "cmr-route-decision-v1":
        errors.append("route decision.schema_version is invalid")
    if not _nonempty_string(value.get("task_id")):
        errors.append("route decision.task_id must be non-empty")
    errors += _ref_errors(value.get("brief"), "route decision.brief")
    envelope = {
        "schema_version": "cmr-controller-result-v1",
        "fact_ids": value.get("fact_ids"),
        "uncertainty_ids": value.get("uncertainty_ids"),
        "action_intents": [],
    }
    errors += [
        error.replace("controller result", "route decision", 1)
        for error in cmr_contracts.validate_controller_result(
            envelope, _frozen_ontologies()
        )
    ]
    if value.get("risk_signals") != value.get("fact_ids"):
        errors.append("route decision.risk_signals must equal fact_ids")
    if value.get("risk_level") not in {"low", "elevated", "critical"}:
        errors.append("route decision.risk_level is invalid")
    if not isinstance(value.get("hard_gates"), list):
        errors.append("route decision.hard_gates must be an array")
    if not isinstance(value.get("derived_flags"), list):
        errors.append("route decision.derived_flags must be an array")
    recurrence = value.get("recurrence")
    if not isinstance(recurrence, Mapping) or set(recurrence) != {
        "active",
        "reason_id",
        "defect_id",
    }:
        errors.append("route decision.recurrence is invalid")
    elif type(recurrence.get("active")) is not bool:
        errors.append("route decision.recurrence.active must be boolean")
    allowed_routes = {
        ("gpt-5.6-luna", "xhigh"),
        ("gpt-5.6-luna", "max"),
        ("gpt-5.6-sol", "xhigh"),
        ("gpt-5.6-sol", "max"),
    }
    for field in ("worker", "review"):
        route = value.get(field)
        if not isinstance(route, Mapping) or set(route) != {
            "model",
            "reasoning_effort",
        }:
            errors.append(f"route decision.{field} is invalid")
        elif (route.get("model"), route.get("reasoning_effort")) not in allowed_routes:
            errors.append(f"route decision.{field} route is invalid")
    expected_reviews = {
        ("gpt-5.6-luna", "xhigh"): ("gpt-5.6-luna", "max"),
        ("gpt-5.6-luna", "max"): ("gpt-5.6-luna", "max"),
        ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "xhigh"),
        ("gpt-5.6-sol", "max"): ("gpt-5.6-sol", "max"),
    }
    worker = value.get("worker")
    review = value.get("review")
    if isinstance(worker, Mapping) and isinstance(review, Mapping):
        expected_review = expected_reviews.get(
            (worker.get("model"), worker.get("reasoning_effort"))
        )
        if expected_review != (
            review.get("model"),
            review.get("reasoning_effort"),
        ):
            errors.append("route decision review does not match worker route")
    if value.get("execution") != {
        "fork_turns": "none",
        "context_mode": "fresh",
        "execution_engine": "superpowers-sdd",
    }:
        errors.append("route decision.execution is invalid")
    if value.get("status") != "ready" or value.get("blocker_ids") != []:
        errors.append("route decision is not dispatch-ready")
    if type(value.get("external_actions")) is not list:
        errors.append("route decision.external_actions must be an array")
    if not _nonempty_string(value.get("role")):
        errors.append("route decision.role must be non-empty")
    if not _nonempty_string(value.get("decisive_reason_id")):
        errors.append("route decision.decisive_reason_id must be non-empty")
    if not _nonempty_string(value.get("justification")):
        errors.append("route decision.justification must be non-empty")
    fact_entries = {
        str(entry["id"]): entry for entry in _frozen_ontologies().fact_entries
    }
    facts_value = value.get("fact_ids")
    facts = list(facts_value) if isinstance(facts_value, list) else []
    if all(fact in fact_entries for fact in facts):
        hard = [fact for fact in facts if fact_entries[fact]["hard_gate"]]
        elevated = [
            fact
            for fact in facts
            if fact_entries[fact]["routing_class"]
            in {"integration", "elevated_risk"}
        ]
        fact_set = set(facts)
        eligible: list[str] = []
        if "low_blast_radius" in fact_set:
            eligible.extend(
                fact
                for fact in facts
                if fact in {"audit", "judgment_required", "reconciliation"}
            )
        if {
            "complex_local_execution",
            "architecture_approved",
        }.issubset(fact_set):
            eligible.extend(
                fact for fact in facts if fact == "complex_local_execution"
            )
        recurrence_active = bool(
            isinstance(recurrence, Mapping) and recurrence.get("active") is True
        )
        final_review = value.get("role") == "final_review"
        if final_review or recurrence_active or hard:
            expected_worker = ("gpt-5.6-sol", "max")
        elif elevated:
            expected_worker = ("gpt-5.6-sol", "xhigh")
        elif eligible:
            expected_worker = ("gpt-5.6-luna", "max")
        else:
            expected_worker = ("gpt-5.6-luna", "xhigh")
        if isinstance(worker, Mapping) and (
            worker.get("model"), worker.get("reasoning_effort")
        ) != expected_worker:
            errors.append("route decision worker is not derived from its facts")
        if value.get("hard_gates") != hard:
            errors.append("route decision hard_gates are not derived")
        derived_flags: list[str] = []
        for fact in facts:
            for flag in fact_entries[fact]["derived_flags"]:
                if flag not in derived_flags:
                    derived_flags.append(flag)
        if value.get("derived_flags") != derived_flags:
            errors.append("route decision derived_flags are not derived")
        risk_rank = {"low": 0, "elevated": 1, "critical": 2}
        expected_risk = "low"
        for fact in facts:
            projection = str(fact_entries[fact]["risk_projection"])
            if risk_rank[projection] > risk_rank[expected_risk]:
                expected_risk = projection
        if value.get("risk_level") != expected_risk:
            errors.append("route decision risk_level is not derived")
        if final_review:
            expected_role = "final_review"
            expected_reason = "branch_final_review"
        else:
            if recurrence_active and isinstance(recurrence, Mapping):
                expected_reason = recurrence.get("reason_id")
            elif hard:
                expected_reason = hard[0]
            elif elevated:
                expected_reason = elevated[0]
            elif eligible:
                expected_reason = eligible[0]
            else:
                expected_reason = "default_clear_isolated_work"
            if "audit" in facts:
                expected_role = "audit"
            elif "reconciliation" in facts:
                expected_role = "reconciliation"
            elif "judgment_required" in facts:
                expected_role = "judgment"
            else:
                expected_role = "implementation"
        if value.get("role") != expected_role:
            errors.append("route decision role is not derived")
        if value.get("decisive_reason_id") != expected_reason:
            errors.append("route decision decisive_reason_id is not derived")
        if isinstance(review, Mapping) and isinstance(expected_reason, str):
            expected_justification = (
                f"{expected_reason} requires {expected_worker[0]}/{expected_worker[1]}; "
                f"task review is {review.get('model')}/{review.get('reasoning_effort')}."
            )
            if value.get("justification") != expected_justification:
                errors.append("route decision justification is not derived")
    actions = value.get("external_actions")
    if isinstance(actions, list):
        known_actions = set(_frozen_ontologies().action_ids)
        seen_pairs: list[tuple[Any, Any]] = []
        for index, action in enumerate(actions):
            label = f"route decision.external_actions[{index}]"
            if not isinstance(action, Mapping):
                errors.append(f"{label} must be an object")
                continue
            state = action.get("state")
            if state == "blocked":
                allowed_fields = {
                    "action",
                    "state",
                    "authorized",
                    "preview",
                    "readback",
                    "blocker_ids",
                }
                if "target" in action:
                    allowed_fields.add("target")
            elif state == "ready":
                allowed_fields = {
                    "action",
                    "target",
                    "state",
                    "authorized",
                    "preview",
                    "readback",
                    "blocker_ids",
                    "authorization_id",
                    "preview_sha256",
                }
            elif state == "completed":
                allowed_fields = {
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
                }
            else:
                errors.append(f"{label}.state is invalid")
                continue
            errors += _exact_fields(action, allowed_fields, label)
            if action.get("action") not in known_actions:
                errors.append(f"{label}.action is invalid")
            if state == "blocked":
                if (
                    action.get("authorized") is not False
                    or action.get("preview") is not False
                    or action.get("readback") is not False
                ):
                    errors.append(f"{label} blocked lifecycle is invalid")
                expected_blockers = (
                    ["external_target_missing"] if "target" not in action else []
                )
                if action.get("blocker_ids") != expected_blockers:
                    errors.append(f"{label}.blocker_ids is invalid")
            elif action.get("blocker_ids") != []:
                errors.append(f"{label}.blocker_ids must be empty")
            if state in {"ready", "completed"}:
                expected_lifecycle = (
                    True,
                    True,
                    state == "completed",
                )
                if (
                    action.get("authorized"),
                    action.get("preview"),
                    action.get("readback"),
                ) != expected_lifecycle:
                    errors.append(f"{label} lifecycle is invalid")
                if not _nonempty_string(action.get("target")):
                    errors.append(f"{label}.target must be non-empty")
                if not _nonempty_string(action.get("authorization_id")):
                    errors.append(f"{label}.authorization_id must be non-empty")
                preview = action.get("preview_sha256")
                if not isinstance(preview, str) or not _SHA256_RE.fullmatch(preview):
                    errors.append(f"{label}.preview_sha256 is invalid")
            if state == "completed" and not _nonempty_string(
                action.get("readback_id")
            ):
                errors.append(f"{label}.readback_id must be non-empty")
            seen_pairs.append((action.get("action"), action.get("target")))
        if len(seen_pairs) != len(set(seen_pairs)):
            errors.append("route decision external actions must be unique")
    return sorted(set(errors))


def _preflight_invocation_errors(value: Any) -> list[str]:
    fields = {
        "schema_version",
        "phase",
        "task_id",
        "brief",
        "candidate_evidence",
        "candidate_decision",
        "selector",
        "prompt",
        "raw",
        "transcript",
        "occurrence",
        "model",
        "reasoning_effort",
        "fork_turns",
        "thread_id",
        "cwd",
        "exit_code",
        "external_writes",
        "usage",
    }
    errors = _exact_fields(value, fields, "preflight invocation")
    if not isinstance(value, Mapping):
        return errors
    constants = {
        "schema_version": "cmr-preflight-invocation-v1",
        "phase": "preflight",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "fork_turns": "none",
        "exit_code": 0,
        "external_writes": False,
    }
    for field, expected in constants.items():
        if not _json_type_equal(value.get(field), expected):
            errors.append(f"preflight invocation.{field} is invalid")
    if not _nonempty_string(value.get("task_id")):
        errors.append("preflight invocation.task_id must be non-empty")
    for field in (
        "brief",
        "candidate_evidence",
        "candidate_decision",
        "prompt",
        "raw",
        "transcript",
        "occurrence",
    ):
        errors += _ref_errors(value.get(field), f"preflight invocation.{field}")
    selector_errors = cmr_compiler.validate_selector(value.get("selector"))
    if isinstance(value.get("selector"), Mapping) and value["selector"].get(
        "decision"
    ) != "run":
        selector_errors.append("preflight invocation selector must be run")
    errors += selector_errors
    if not _nonempty_string(value.get("thread_id")):
        errors.append("preflight invocation.thread_id must be non-empty")
    errors += _cwd_errors(value.get("cwd"), "preflight invocation.cwd")
    errors += _usage_errors(value.get("usage"), "preflight invocation.usage")
    return sorted(set(errors))


def _blocker_errors(value: Any, label: str) -> list[str]:
    return _canonical_ids_errors(
        value,
        _SYSTEM_AND_PREFLIGHT_BLOCKERS,
        label,
        allow_empty=False,
    )


def _preflight_evidence_errors(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return ["preflight evidence must be an object"]
    outcome = value.get("outcome")
    selector = value.get("selector")
    selector_decision = selector.get("decision") if isinstance(selector, Mapping) else None
    if outcome == "skipped":
        fields = {
            "schema_version",
            "task_id",
            "selector",
            "outcome",
            "candidate_decision",
        }
        expected_selector = "skip"
    elif outcome == "accepted":
        fields = {
            "schema_version",
            "task_id",
            "selector",
            "outcome",
            "candidate_decision",
            "invocation",
        }
        expected_selector = "run"
    elif outcome == "replaced":
        fields = {
            "schema_version",
            "task_id",
            "selector",
            "outcome",
            "candidate_decision",
            "invocation",
            "replacement_evidence",
            "final_decision",
        }
        expected_selector = "run"
    elif outcome == "blocked" and selector_decision == "block":
        fields = {
            "schema_version",
            "task_id",
            "selector",
            "outcome",
            "blocker_ids",
        }
        expected_selector = "block"
    elif outcome == "blocked" and selector_decision == "run":
        fields = {
            "schema_version",
            "task_id",
            "selector",
            "outcome",
            "candidate_decision",
            "invocation",
            "blocker_ids",
        }
        expected_selector = "run"
    else:
        return ["preflight evidence branch is invalid"]
    errors = _exact_fields(value, fields, "preflight evidence")
    if value.get("schema_version") != "cmr-preflight-evidence-v1":
        errors.append("preflight evidence.schema_version is invalid")
    if not _nonempty_string(value.get("task_id")):
        errors.append("preflight evidence.task_id must be non-empty")
    errors += cmr_compiler.validate_selector(selector)
    if selector_decision != expected_selector:
        errors.append("preflight evidence selector does not match outcome")
    for field in (
        "candidate_decision",
        "invocation",
        "replacement_evidence",
        "final_decision",
    ):
        if field in value:
            errors += _ref_errors(value.get(field), f"preflight evidence.{field}")
    if outcome == "blocked":
        errors += _blocker_errors(value.get("blocker_ids"), "preflight evidence.blocker_ids")
    return sorted(set(errors))


def _bundle_shape_errors(value: Any) -> list[str]:
    fields = {
        "schema_version",
        "task_id",
        "brief",
        "controller_evidence",
        "runtime_input",
        "candidate_decision",
        "preflight_evidence",
        "final_decision",
    }
    errors = _exact_fields(value, fields, "dispatch bundle")
    if not isinstance(value, Mapping):
        return errors
    if value.get("schema_version") != "cmr-dispatch-bundle-v1":
        errors.append("dispatch bundle.schema_version is invalid")
    if not _nonempty_string(value.get("task_id")):
        errors.append("dispatch bundle.task_id must be non-empty")
    for field in fields - {"schema_version", "task_id"}:
        errors += _ref_errors(value.get(field), f"dispatch bundle.{field}")
    return sorted(set(errors))


def _control_occurrence_errors(
    value: Any,
    *,
    phase: str,
    task_id: str | None,
    plan_sha256: str | None,
) -> list[str]:
    label = f"{phase} occurrence"
    errors = _exact_fields(value, set(cmr_compiler._OCCURRENCE_FIELDS), label)
    if not isinstance(value, Mapping):
        return errors
    if phase == "planning":
        expected = {
            "phase": "planning",
            "scope_kind": "plan",
            "scope_id": plan_sha256,
            "task_id": None,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
        }
    else:
        expected = {
            "phase": "preflight",
            "scope_kind": "task",
            "scope_id": task_id,
            "task_id": task_id,
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
        }
    expected.update(
        {
            "schema_version": "cmr-occurrence-audit-v1",
            "round_index": None,
            "response_policy": "closed_json",
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
    )
    for field, expected_value in expected.items():
        if not _json_type_equal(value.get(field), expected_value):
            errors.append(f"{label}.{field} is invalid")
    if not _nonempty_string(value.get("thread_id")):
        errors.append(f"{label}.thread_id must be non-empty")
    for field in ("prompt_sha256", "transcript_sha256", "raw_sha256"):
        if not isinstance(value.get(field), str) or not _SHA256_RE.fullmatch(
            value[field]
        ):
            errors.append(f"{label}.{field} must be lowercase SHA-256")
    errors += _cwd_errors(value.get("cwd"), f"{label}.cwd")
    errors += _usage_errors(value.get("usage"), f"{label}.usage")
    ignored = value.get("ignored_empty_agent_messages")
    if type(ignored) is not int or ignored < 0:
        errors.append(f"{label}.ignored_empty_agent_messages is invalid")
    return sorted(set(errors))


def bind_planner_result(
    result: Mapping[str, Any],
    *,
    plan: ArtifactRef,
    briefs: Mapping[str, ArtifactRef],
    prompt: ArtifactRef,
    raw: ArtifactRef,
    transcript: ArtifactRef,
    occurrence_ref: ArtifactRef,
    occurrence: cmr_runtime.OccurrenceAudit,
) -> PlanningEvidence:
    if not isinstance(result, Mapping):
        raise DispatchError("planner result must be an object", "artifact_binding_invalid")
    if not isinstance(briefs, Mapping):
        raise DispatchError("briefs must be a mapping", "artifact_binding_invalid")
    if any(not _nonempty_string(task_id) for task_id in briefs):
        raise DispatchError(
            "brief task IDs must be non-empty strings", "artifact_binding_invalid"
        )
    references = [plan, prompt, raw, transcript, occurrence_ref, *briefs.values()]
    if any(not isinstance(reference, ArtifactRef) for reference in references):
        raise DispatchError(
            "planner binding requires ArtifactRef values", "artifact_binding_invalid"
        )
    semantic_errors = cmr_contracts.validate_planner_result(
        dict(result), list(briefs), _frozen_ontologies()
    )
    if semantic_errors:
        raise DispatchError(semantic_errors, "artifact_binding_invalid")
    if not isinstance(occurrence, cmr_runtime.OccurrenceAudit):
        raise DispatchError("occurrence must be audited", "occurrence_invalid")
    occurrence_errors = _control_occurrence_errors(
        occurrence.to_record(),
        phase="planning",
        task_id=None,
        plan_sha256=plan.sha256,
    )
    if not _json_type_equal(occurrence.raw_object, dict(result)):
        occurrence_errors.append("planning occurrence raw_object does not equal result")
    if occurrence.terminal_report is not None:
        occurrence_errors.append("planning occurrence has a terminal report")
    if occurrence_errors:
        raise DispatchError(occurrence_errors, "occurrence_invalid")
    binding_errors: list[str] = []
    if occurrence.prompt_sha256 != prompt.sha256:
        binding_errors.append("planning prompt binding is stale")
    if occurrence.raw_sha256 != raw.sha256:
        binding_errors.append("planning raw binding is stale")
    if occurrence.transcript_sha256 != transcript.sha256:
        binding_errors.append("planning transcript binding is stale")
    occurrence_payload = _canonical_json_bytes(occurrence.to_record())
    if occurrence_ref.sha256 != hashlib.sha256(occurrence_payload).hexdigest():
        binding_errors.append("planning occurrence reference hash is stale")
    if occurrence_ref.size_bytes != len(occurrence_payload):
        binding_errors.append("planning occurrence reference size is stale")
    if binding_errors:
        raise DispatchError(binding_errors, "artifact_binding_invalid")
    return PlanningEvidence(
        result=copy.deepcopy(dict(result)),
        plan=plan,
        briefs=dict(briefs),
        prompt=prompt,
        raw=raw,
        transcript=transcript,
        occurrence_ref=occurrence_ref,
        occurrence=occurrence,
    )


def _validate_bound_inputs(
    root_descriptor: int, references: Sequence[ArtifactRef]
) -> None:
    for reference in references:
        try:
            _read_reference(root_descriptor, reference)
        except OSError as exc:
            raise DispatchError(
                f"bound artifact is unavailable or mutable: {reference.path}"
            ) from exc


def materialize_safe_lane(
    evidence: PlanningEvidence,
    *,
    ledger_root: Path,
) -> SafeLaneArtifacts:
    if not isinstance(evidence, PlanningEvidence):
        raise DispatchError("safe-lane materialization requires PlanningEvidence")
    root_descriptor, root_stat = _open_root(Path(ledger_root))
    try:
        references = [
            evidence.plan,
            *evidence.briefs.values(),
            evidence.prompt,
            evidence.raw,
            evidence.transcript,
            evidence.occurrence_ref,
        ]
        _validate_bound_inputs(root_descriptor, references)
        plan_payload, _ = _read_reference(root_descriptor, evidence.plan)
        frozen_entries = _frozen_plan_entries(plan_payload)
        bound_entries = list(evidence.briefs.items())
        if frozen_entries != bound_entries:
            raise DispatchError(
                "planner brief mapping does not bind frozen plan",
                "artifact_binding_invalid",
            )
        planner_errors = cmr_contracts.validate_planner_result(
            dict(evidence.result),
            [task_id for task_id, _ in frozen_entries],
            _frozen_ontologies(),
        )
        if not _json_type_equal(evidence.occurrence.raw_object, dict(evidence.result)):
            planner_errors.append("planner result does not bind audited occurrence")
        if planner_errors:
            raise DispatchError(planner_errors, "artifact_binding_invalid")
        entries = [
            {
                "task_id": lane["task_id"],
                "brief": evidence.briefs[lane["task_id"]].to_dict(),
                "reason_ids": list(lane["reason_ids"]),
            }
            for lane in evidence.result["safe_lanes"]
        ]
        prefix = f"safe-lane/{evidence.plan.sha256}"
        try:
            safe_members, _ = _directory_state(root_descriptor, "safe-lane")
        except OSError:
            safe_members = set()
        if safe_members and safe_members != {evidence.plan.sha256}:
            raise DispatchError("safe-lane scope belongs to a different plan")

        certificate_value = {
            "schema_version": "cmr-safe-lane-certificate-v1",
            "plan": evidence.plan.to_dict(),
            "entries": entries,
        }
        certificate = _exclusive_write(
            root_descriptor,
            f"{prefix}/certificate.json",
            _canonical_json_bytes(certificate_value),
        )
        occurrence = evidence.occurrence
        assert occurrence.usage is not None
        invocation_value = {
            "schema_version": "cmr-planning-invocation-v1",
            "phase": "planning",
            "purpose": "canonical_planning",
            "plan": evidence.plan.to_dict(),
            "prompt": evidence.prompt.to_dict(),
            "raw": evidence.raw.to_dict(),
            "transcript": evidence.transcript.to_dict(),
            "occurrence": evidence.occurrence_ref.to_dict(),
            "certificate": certificate.to_dict(),
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "fork_turns": "none",
            "thread_id": occurrence.thread_id,
            "cwd": copy.deepcopy(occurrence.cwd),
            "exit_code": 0,
            "external_writes": False,
            "usage": occurrence.usage.to_dict(),
        }
        planning_invocation = _exclusive_write(
            root_descriptor,
            f"{prefix}/planning-invocation.json",
            _canonical_json_bytes(invocation_value),
        )
        manifest_value = {
            "schema_version": "cmr-safe-lane-manifest-v1",
            "plan": evidence.plan.to_dict(),
            "certificate": certificate.to_dict(),
            "planning_invocation": planning_invocation.to_dict(),
            "entries": entries,
        }
        manifest = _exclusive_write(
            root_descriptor,
            f"{prefix}/manifest.json",
            _canonical_json_bytes(manifest_value),
        )
        _seal_directory(root_descriptor, prefix)
        _seal_directory(root_descriptor, "safe-lane")
        if not _root_identity_unchanged(Path(ledger_root), root_stat):
            raise DispatchError("ledger root identity changed during materialization")
    finally:
        os.close(root_descriptor)
    errors = validate_safe_lane_manifest(
        Path(ledger_root) / manifest.path, Path(ledger_root)
    )
    if errors:
        raise DispatchError(errors)
    return SafeLaneArtifacts(certificate, planning_invocation, manifest)


def _parsed_control_raw(
    payload: bytes,
    *,
    phase: str,
    task_id: str | None,
    plan_sha256: str | None,
    prompt_sha256: str,
    cwd: Mapping[str, Any],
    validator: Any,
) -> cmr_runtime.ParsedEvents:
    if phase == "planning":
        scope_kind = "plan"
        scope_id = plan_sha256 or ""
        model, effort = "gpt-5.6-sol", "max"
    else:
        scope_kind = "task"
        scope_id = task_id or ""
        model, effort = "gpt-5.6-luna", "max"
    expected = cmr_runtime.OccurrenceExpectation(
        phase=phase,
        scope_kind=scope_kind,
        scope_id=scope_id,
        round_index=None,
        task_id=task_id,
        model=model,
        reasoning_effort=effort,
        prompt_sha256=prompt_sha256,
        forbidden_thread_ids=(),
        prior_thread_id=None,
        fork_turns="none",
        thread_policy="fresh",
        cwd_policy=dict(cwd),
        write_policy="control_plane_no_write",
        response_policy="closed_json",
    )
    return cmr_runtime.audit_jsonl(payload, expected, validator)


def _occurrence_matches_parsed(
    occurrence: Mapping[str, Any], parsed: cmr_runtime.ParsedEvents
) -> bool:
    return bool(
        not parsed.errors
        and parsed.thread_id == occurrence.get("thread_id")
        and parsed.raw_sha256 == occurrence.get("raw_sha256")
        and parsed.tool_event_count == occurrence.get("tool_event_count")
        and parsed.ignored_empty_agent_messages
        == occurrence.get("ignored_empty_agent_messages")
        and parsed.terminal_report is None
        and parsed.usage is not None
        and _json_type_equal(parsed.usage.to_dict(), occurrence.get("usage"))
    )


def _read_artifact_path(
    root_descriptor: int,
    artifact_path: Path,
    ledger_root: Path,
) -> tuple[bytes, ArtifactRef, os.stat_result]:
    path = Path(artifact_path)
    if not path.is_absolute():
        raise OSError("artifact path must be absolute")
    try:
        relative = path.relative_to(ledger_root).as_posix()
    except ValueError as exc:
        raise OSError("artifact path escapes ledger root") from exc
    if not relative or relative == "." or "\\" in relative:
        raise OSError("artifact path is invalid")
    descriptor = -1
    try:
        descriptor, opened = _open_relative(
            root_descriptor, relative, require_regular=True
        )
        if opened.st_mode & 0o222:
            raise OSError("artifact is writable")
        payload = _read_descriptor(descriptor)
        reference = _reference_for_payload(relative, payload)
        reopened, reopened_stat = _open_relative(
            root_descriptor, relative, require_regular=True
        )
        try:
            if not _same_identity(opened, reopened_stat):
                raise OSError("artifact identity changed")
            if _read_descriptor(reopened) != payload:
                raise OSError("artifact bytes changed")
        finally:
            os.close(reopened)
        return payload, reference, opened
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _safe_lane_details(
    manifest_path: Path,
    ledger_root: Path,
) -> tuple[list[str], dict[str, Any] | None, ArtifactRef | None, dict[str, Any] | None]:
    errors: list[str] = []
    root_descriptor = -1
    safe_state: tuple[set[str], os.stat_result] | None = None
    plan_state: tuple[set[str], os.stat_result] | None = None
    try:
        root_descriptor, root_stat = _open_root(ledger_root)
        manifest_payload, manifest_ref, _ = _read_artifact_path(
            root_descriptor, manifest_path, ledger_root
        )
        manifest = _strict_object(manifest_payload, "safe-lane manifest")
        errors += _manifest_errors(manifest, _frozen_ontologies())
        if errors:
            return sorted(set(errors)), manifest, manifest_ref, None

        plan_ref = _ref(manifest["plan"], "safe-lane plan")
        certificate_ref = _ref(manifest["certificate"], "safe-lane certificate")
        invocation_ref = _ref(
            manifest["planning_invocation"], "planning invocation"
        )
        prefix = f"safe-lane/{plan_ref.sha256}"
        if manifest_ref.path != f"{prefix}/manifest.json":
            errors.append("safe-lane manifest path is not canonical")
        if certificate_ref.path != f"{prefix}/certificate.json":
            errors.append("safe-lane certificate path is not canonical")
        if invocation_ref.path != f"{prefix}/planning-invocation.json":
            errors.append("planning invocation path is not canonical")
        try:
            safe_members, safe_stat = _directory_state(root_descriptor, "safe-lane")
            plan_members, plan_stat = _directory_state(root_descriptor, prefix)
            safe_state = safe_members, safe_stat
            plan_state = plan_members, plan_stat
            if safe_members != {plan_ref.sha256}:
                errors.append("safe-lane scope membership is not exact")
            if plan_members != {
                "certificate.json",
                "planning-invocation.json",
                "manifest.json",
            }:
                errors.append("safe-lane plan membership is not exact")
            if safe_stat.st_mode & 0o222 or plan_stat.st_mode & 0o222:
                errors.append("safe-lane directories must be sealed")
        except OSError:
            errors.append("safe-lane canonical directory is unavailable")

        plan_payload, _ = _read_reference(root_descriptor, plan_ref)
        frozen_entries = _frozen_plan_entries(plan_payload)
        frozen_by_task = dict(frozen_entries)
        certificate_payload, _ = _read_reference(root_descriptor, certificate_ref)
        invocation_payload, _ = _read_reference(root_descriptor, invocation_ref)
        certificate = _strict_object(certificate_payload, "safe-lane certificate")
        invocation = _strict_object(invocation_payload, "planning invocation")
        errors += _certificate_errors(certificate, _frozen_ontologies())
        errors += _planning_invocation_errors(invocation)
        if not _json_type_equal(certificate.get("plan"), manifest.get("plan")):
            errors.append("safe-lane certificate does not bind manifest plan")
        if not _json_type_equal(certificate.get("entries"), manifest.get("entries")):
            errors.append("safe-lane certificate entries do not bind manifest")
        if canonical_json := _canonical_json_bytes(certificate.get("entries")):
            if canonical_json != _canonical_json_bytes(manifest.get("entries")):
                errors.append("safe-lane entry serialization differs")
        expected_invocation_refs = {
            "plan": manifest.get("plan"),
            "certificate": manifest.get("certificate"),
        }
        for field, expected_value in expected_invocation_refs.items():
            if not _json_type_equal(invocation.get(field), expected_value):
                errors.append(f"planning invocation.{field} does not bind manifest")

        manifest_task_ids: list[str] = []
        for entry in manifest.get("entries", []):
            if isinstance(entry, Mapping):
                brief_ref = _ref(entry.get("brief"), "safe-lane entry brief")
                _read_reference(root_descriptor, brief_ref)
                task_id = entry.get("task_id")
                if not isinstance(task_id, str) or frozen_by_task.get(
                    task_id
                ) != brief_ref:
                    errors.append("safe-lane entry is not bound to frozen plan")
                else:
                    manifest_task_ids.append(task_id)
        frozen_order = {task_id: index for index, (task_id, _) in enumerate(frozen_entries)}
        if manifest_task_ids != sorted(
            manifest_task_ids, key=frozen_order.__getitem__
        ):
            errors.append("safe-lane entries do not follow frozen plan order")
        for _, brief_ref in frozen_entries:
            _read_reference(root_descriptor, brief_ref)

        prompt_ref = _ref(invocation.get("prompt"), "planning prompt")
        raw_ref = _ref(invocation.get("raw"), "planning raw")
        transcript_ref = _ref(invocation.get("transcript"), "planning transcript")
        occurrence_ref = _ref(invocation.get("occurrence"), "planning occurrence")
        prompt_payload, _ = _read_reference(root_descriptor, prompt_ref)
        del prompt_payload
        raw_payload, _ = _read_reference(root_descriptor, raw_ref)
        transcript_payload, _ = _read_reference(root_descriptor, transcript_ref)
        occurrence_payload, _ = _read_reference(root_descriptor, occurrence_ref)
        occurrence = _strict_object(occurrence_payload, "planning occurrence")
        errors += _control_occurrence_errors(
            occurrence,
            phase="planning",
            task_id=None,
            plan_sha256=plan_ref.sha256,
        )
        if occurrence.get("prompt_sha256") != prompt_ref.sha256:
            errors.append("planning prompt hash binding is invalid")
        if occurrence.get("raw_sha256") != raw_ref.sha256:
            errors.append("planning raw hash binding is invalid")
        if occurrence.get("transcript_sha256") != transcript_ref.sha256:
            errors.append("planning transcript hash binding is invalid")
        if hashlib.sha256(transcript_payload).hexdigest() != transcript_ref.sha256:
            errors.append("planning transcript bytes are stale")
        for field in (
            "thread_id",
            "cwd",
            "model",
            "reasoning_effort",
            "fork_turns",
            "exit_code",
            "external_writes",
            "usage",
        ):
            if not _json_type_equal(invocation.get(field), occurrence.get(field)):
                errors.append(f"planning invocation.{field} does not bind occurrence")
        expected_result = {
            "schema_version": "cmr-planner-result-v1",
            "safe_lanes": [
                {
                    "task_id": entry["task_id"],
                    "reason_ids": list(entry["reason_ids"]),
                }
                for entry in manifest.get("entries", [])
                if isinstance(entry, Mapping)
            ],
        }
        task_ids = [task_id for task_id, _ in frozen_entries]
        parsed = _parsed_control_raw(
            raw_payload,
            phase="planning",
            task_id=None,
            plan_sha256=plan_ref.sha256,
            prompt_sha256=prompt_ref.sha256,
            cwd=invocation.get("cwd", {}),
            validator=lambda value: cmr_contracts.validate_planner_result(
                value, task_ids, _frozen_ontologies()
            ),
        )
        if not _occurrence_matches_parsed(occurrence, parsed):
            errors.append("planning raw events do not bind occurrence")
        if not _json_type_equal(parsed.raw_object, expected_result):
            errors.append("planning raw result does not bind certificate entries")
        if safe_state is not None and not _directory_state_unchanged(
            root_descriptor, "safe-lane", *safe_state
        ):
            errors.append("safe-lane scope identity changed during validation")
        if plan_state is not None and not _directory_state_unchanged(
            root_descriptor, prefix, *plan_state
        ):
            errors.append("safe-lane plan identity changed during validation")
        if not _root_identity_unchanged(ledger_root, root_stat):
            errors.append("ledger root identity changed during validation")
        return sorted(set(errors)), manifest, manifest_ref, invocation
    except (DispatchError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"safe-lane evidence is invalid: {exc}")
        return sorted(set(errors)), None, None, None
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def validate_safe_lane_manifest(
    manifest_path: Path,
    ledger_root: Path,
) -> list[str]:
    errors, _, _, _ = _safe_lane_details(
        Path(manifest_path), Path(ledger_root)
    )
    return errors


def _decision_brief_available(
    decision: Mapping[str, Any], ledger_root: Path
) -> bool:
    root_descriptor = -1
    try:
        root_descriptor, _ = _open_root(ledger_root)
        brief = _ref(decision.get("brief"), "route decision brief")
        _read_reference(root_descriptor, brief)
        return True
    except (DispatchError, OSError, ValueError):
        return False
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _selector_triggers(decision: Mapping[str, Any]) -> set[str]:
    triggers: set[str] = set()
    worker = decision.get("worker")
    route = (
        worker.get("model"),
        worker.get("reasoning_effort"),
    ) if isinstance(worker, Mapping) else (None, None)
    if route != ("gpt-5.6-luna", "xhigh"):
        triggers.add("nondefault_worker_route")
    hard_gates = decision.get("hard_gates")
    recurrence = decision.get("recurrence")
    if (isinstance(hard_gates, list) and hard_gates) or (
        isinstance(recurrence, Mapping) and recurrence.get("active") is True
    ):
        triggers.add("hard_gate_or_recurrence")
    facts = set(decision.get("fact_ids", []))
    entries = {str(entry["id"]): entry for entry in _frozen_ontologies().fact_entries}
    if any(
        fact in entries
        and entries[fact]["routing_class"] in {"integration", "elevated_risk"}
        for fact in facts
    ):
        triggers.add("integration_or_elevated_risk")
    if route == ("gpt-5.6-luna", "max"):
        triggers.add("luna_max_eligibility")
    if decision.get("role") == "final_review" or decision.get(
        "decisive_reason_id"
    ) == "branch_final_review":
        triggers.add("branch_final_review")
    if isinstance(decision.get("external_actions"), list) and decision[
        "external_actions"
    ]:
        triggers.add("external_action_present")
    uncertainty_ids = decision.get("uncertainty_ids")
    if isinstance(uncertainty_ids, list):
        triggers.update(uncertainty_ids)
    return triggers


def select_preflight(
    decision: Any,
    *,
    manifest_path: Path,
    ledger_root: Path,
) -> dict[str, Any]:
    if _decision_errors(decision) or not isinstance(decision, Mapping):
        return {
            "decision": "block",
            "trigger_ids": ["artifact_binding_invalid"],
        }
    if not _decision_brief_available(decision, Path(ledger_root)):
        return {
            "decision": "block",
            "trigger_ids": ["artifact_binding_invalid"],
        }
    triggers = _selector_triggers(decision)
    safe_errors, manifest, manifest_ref, _ = _safe_lane_details(
        Path(manifest_path), Path(ledger_root)
    )
    matching_safe_lane = False
    if not safe_errors and manifest is not None and manifest_ref is not None:
        matching_safe_lane = sum(
            isinstance(entry, Mapping)
            and entry.get("task_id") == decision.get("task_id")
            and _json_type_equal(entry.get("brief"), decision.get("brief"))
            for entry in manifest.get("entries", [])
        ) == 1
    if not matching_safe_lane:
        triggers.add("safe_lane_unavailable")
    ordered = [
        trigger
        for trigger in cmr_compiler.SELECTOR_TRIGGER_IDS
        if trigger in triggers
    ]
    if ordered:
        return {"decision": "run", "trigger_ids": ordered}
    assert manifest_ref is not None
    return {
        "decision": "skip",
        "trigger_ids": [],
        "safe_lane": manifest_ref.to_dict(),
    }


def _preflight_invocation_details(
    root_descriptor: int,
    invocation_ref: ArtifactRef,
) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None]:
    errors: list[str] = []
    try:
        payload, _ = _read_reference(root_descriptor, invocation_ref)
        invocation = _strict_object(payload, "preflight invocation")
        errors += _preflight_invocation_errors(invocation)
        if errors:
            return sorted(set(errors)), invocation, None
        prompt_ref = _ref(invocation["prompt"], "preflight prompt")
        raw_ref = _ref(invocation["raw"], "preflight raw")
        transcript_ref = _ref(invocation["transcript"], "preflight transcript")
        occurrence_ref = _ref(invocation["occurrence"], "preflight occurrence")
        brief_ref = _ref(invocation["brief"], "preflight brief")
        candidate_evidence_ref = _ref(
            invocation["candidate_evidence"], "preflight candidate evidence"
        )
        candidate_decision_ref = _ref(
            invocation["candidate_decision"], "preflight candidate decision"
        )
        for reference in (
            prompt_ref,
            transcript_ref,
            brief_ref,
            candidate_evidence_ref,
            candidate_decision_ref,
        ):
            _read_reference(root_descriptor, reference)
        raw_payload, _ = _read_reference(root_descriptor, raw_ref)
        transcript_payload, _ = _read_reference(root_descriptor, transcript_ref)
        occurrence_payload, _ = _read_reference(root_descriptor, occurrence_ref)
        occurrence = _strict_object(occurrence_payload, "preflight occurrence")
        errors += _control_occurrence_errors(
            occurrence,
            phase="preflight",
            task_id=invocation.get("task_id"),
            plan_sha256=None,
        )
        if occurrence.get("prompt_sha256") != prompt_ref.sha256:
            errors.append("preflight prompt hash binding is invalid")
        if occurrence.get("raw_sha256") != raw_ref.sha256:
            errors.append("preflight raw hash binding is invalid")
        if occurrence.get("transcript_sha256") != transcript_ref.sha256:
            errors.append("preflight transcript hash binding is invalid")
        if hashlib.sha256(transcript_payload).hexdigest() != transcript_ref.sha256:
            errors.append("preflight transcript bytes are stale")
        for field in (
            "thread_id",
            "cwd",
            "model",
            "reasoning_effort",
            "fork_turns",
            "exit_code",
            "external_writes",
            "usage",
        ):
            if not _json_type_equal(invocation.get(field), occurrence.get(field)):
                errors.append(f"preflight invocation.{field} does not bind occurrence")
        parsed = _parsed_control_raw(
            raw_payload,
            phase="preflight",
            task_id=invocation.get("task_id"),
            plan_sha256=None,
            prompt_sha256=prompt_ref.sha256,
            cwd=invocation.get("cwd", {}),
            validator=lambda value: cmr_contracts.validate_preflight_result(
                value, _frozen_ontologies()
            ),
        )
        if not _occurrence_matches_parsed(occurrence, parsed):
            errors.append("preflight raw events do not bind occurrence")
        return sorted(set(errors)), invocation, parsed.raw_object
    except (DispatchError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"preflight invocation is invalid: {exc}")
        return sorted(set(errors)), None, None


def _preflight_evidence_details(
    evidence_path: Path,
    ledger_root: Path,
) -> tuple[
    list[str],
    dict[str, Any] | None,
    ArtifactRef | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    errors: list[str] = []
    root_descriptor = -1
    scope_state: tuple[set[str], os.stat_result] | None = None
    try:
        root_descriptor, root_stat = _open_root(ledger_root)
        payload, evidence_ref, _ = _read_artifact_path(
            root_descriptor, evidence_path, ledger_root
        )
        evidence = _strict_object(payload, "preflight evidence")
        errors += _preflight_evidence_errors(evidence)
        if errors:
            return sorted(set(errors)), evidence, evidence_ref, None, None
        task_id = evidence["task_id"]
        task_component = _task_component(task_id)
        prefix = f"tasks/{task_component}/preflight"
        if evidence_ref.path != f"{prefix}/evidence.json":
            errors.append("preflight evidence path is not canonical")
        outcome = evidence["outcome"]
        expected_members = {"evidence.json"}
        if outcome in {"accepted", "blocked", "replaced"} and evidence[
            "selector"
        ]["decision"] == "run":
            expected_members.add("invocation.json")
        if outcome == "replaced":
            expected_members.update(
                {"replacement-evidence.json", "final-decision.json"}
            )
        try:
            members, directory_stat = _directory_state(root_descriptor, prefix)
            scope_state = members, directory_stat
            if members != expected_members:
                errors.append("preflight directory membership is not exact")
            if directory_stat.st_mode & 0o222:
                errors.append("preflight directory must be sealed")
        except OSError:
            errors.append("preflight directory is unavailable")

        invocation: dict[str, Any] | None = None
        raw_result: dict[str, Any] | None = None
        if "candidate_decision" in evidence:
            candidate_ref = _ref(
                evidence["candidate_decision"], "preflight candidate decision"
            )
            candidate_payload, _ = _read_reference(root_descriptor, candidate_ref)
            candidate = _strict_object(candidate_payload, "candidate decision")
            errors += _decision_errors(candidate)
            if candidate.get("task_id") != task_id:
                errors.append("preflight candidate task_id is invalid")

        if outcome == "skipped":
            selector = evidence["selector"]
            safe_ref = _ref(selector["safe_lane"], "safe-lane manifest")
            safe_errors, manifest, observed_ref, _ = _safe_lane_details(
                ledger_root / safe_ref.path, ledger_root
            )
            errors += safe_errors
            if observed_ref != safe_ref:
                errors.append("skip selector safe-lane reference is stale")
            if manifest is not None:
                matches = sum(
                    isinstance(entry, Mapping)
                    and entry.get("task_id") == task_id
                    and _json_type_equal(entry.get("brief"), candidate.get("brief"))
                    for entry in manifest.get("entries", [])
                )
                if matches != 1:
                    errors.append("skip task has no exact safe-lane entry")
        elif "invocation" in evidence:
            invocation_ref = _ref(
                evidence["invocation"], "preflight invocation"
            )
            invocation_errors, invocation, raw_result = _preflight_invocation_details(
                root_descriptor, invocation_ref
            )
            errors += invocation_errors
            if invocation is not None:
                if invocation.get("task_id") != task_id:
                    errors.append("preflight invocation task_id is invalid")
                if not _json_type_equal(
                    invocation.get("selector"), evidence.get("selector")
                ):
                    errors.append("preflight invocation selector is split")
                if not _json_type_equal(
                    invocation.get("candidate_decision"),
                    evidence.get("candidate_decision"),
                ):
                    errors.append("preflight invocation candidate is split")
            expected_raw_outcome = {
                "accepted": "accept",
                "replaced": "replace",
                "blocked": "block",
            }[outcome]
            if not isinstance(raw_result, Mapping) or raw_result.get(
                "outcome"
            ) != expected_raw_outcome:
                if not (
                    outcome == "blocked"
                    and isinstance(raw_result, Mapping)
                    and raw_result.get("outcome") == "replace"
                ):
                    errors.append("preflight raw outcome does not bind evidence")

        if outcome == "replaced":
            replacement_ref = _ref(
                evidence["replacement_evidence"], "replacement evidence"
            )
            final_ref = _ref(evidence["final_decision"], "final decision")
            replacement_payload, _ = _read_reference(
                root_descriptor, replacement_ref
            )
            final_payload, _ = _read_reference(root_descriptor, final_ref)
            replacement = _strict_object(
                replacement_payload, "replacement route evidence"
            )
            final = _strict_object(final_payload, "replacement final decision")
            errors += cmr_compiler.validate_route_evidence(
                replacement, _frozen_ontologies()
            )
            errors += _decision_errors(final)
            if isinstance(raw_result, Mapping):
                for field in ("fact_ids", "uncertainty_ids", "action_intents"):
                    if not _json_type_equal(
                        replacement.get(field), raw_result.get(field)
                    ):
                        errors.append(
                            f"replacement evidence.{field} does not bind preflight raw"
                        )
        if outcome == "blocked" and evidence["selector"]["decision"] == "run":
            if raw_result is not None and raw_result.get("outcome") == "block":
                if not _json_type_equal(
                    evidence.get("blocker_ids"), raw_result.get("blocker_ids")
                ):
                    errors.append("model-blocked evidence has invalid blocker IDs")
        if scope_state is not None and not _directory_state_unchanged(
            root_descriptor, prefix, *scope_state
        ):
            errors.append("preflight scope identity changed during validation")
        if not _root_identity_unchanged(ledger_root, root_stat):
            errors.append("ledger root identity changed during preflight validation")
        return sorted(set(errors)), evidence, evidence_ref, invocation, raw_result
    except (DispatchError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"preflight evidence is invalid: {exc}")
        return sorted(set(errors)), None, None, None, None
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def validate_preflight_evidence(
    evidence_path: Path,
    ledger_root: Path,
) -> list[str]:
    errors, _, _, _, _ = _preflight_evidence_details(
        Path(evidence_path), Path(ledger_root)
    )
    return errors


def materialize_preflight(
    *,
    task_id: str,
    selector: Mapping[str, Any],
    candidate_decision: ArtifactRef | None,
    bound_preflight: BoundPreflightResult | None,
    transition: PreflightTransition | None,
    ledger_root: Path,
) -> PreflightArtifacts:
    component = _task_component(task_id)
    selector_errors = cmr_compiler.validate_selector(selector)
    if selector_errors:
        raise DispatchError(selector_errors)
    decision = selector["decision"]
    root_descriptor, root_stat = _open_root(Path(ledger_root))
    try:
        prefix = f"tasks/{component}/preflight"
        invocation_ref: ArtifactRef | None = None
        replacement_ref: ArtifactRef | None = None
        final_ref: ArtifactRef | None = None
        if decision == "skip":
            if not isinstance(candidate_decision, ArtifactRef):
                raise DispatchError("skip requires candidate decision ArtifactRef")
            if bound_preflight is not None or transition is not None:
                raise DispatchError("skip forbids bound preflight and transition")
            candidate_payload, _ = _read_reference(
                root_descriptor, candidate_decision
            )
            candidate = _strict_object(candidate_payload, "candidate decision")
            if _decision_errors(candidate) or candidate.get("task_id") != task_id:
                raise DispatchError("skip candidate decision is invalid")
            safe_ref = _ref(selector["safe_lane"], "skip safe-lane")
            safe_errors, manifest, observed_ref, _ = _safe_lane_details(
                Path(ledger_root) / safe_ref.path, Path(ledger_root)
            )
            if safe_errors or observed_ref != safe_ref or manifest is None:
                raise DispatchError(safe_errors or ["skip safe-lane is stale"])
            if sum(
                isinstance(entry, Mapping)
                and entry.get("task_id") == task_id
                and _json_type_equal(entry.get("brief"), candidate.get("brief"))
                for entry in manifest.get("entries", [])
            ) != 1:
                raise DispatchError("skip has no exact safe-lane membership")
            evidence_value = {
                "schema_version": "cmr-preflight-evidence-v1",
                "task_id": task_id,
                "selector": copy.deepcopy(dict(selector)),
                "outcome": "skipped",
                "candidate_decision": candidate_decision.to_dict(),
            }
            final_ref = candidate_decision
        elif decision == "block":
            if any(
                value is not None
                for value in (candidate_decision, bound_preflight, transition)
            ):
                raise DispatchError("pre-call block forbids optional inputs")
            evidence_value = {
                "schema_version": "cmr-preflight-evidence-v1",
                "task_id": task_id,
                "selector": copy.deepcopy(dict(selector)),
                "outcome": "blocked",
                "blocker_ids": ["artifact_binding_invalid"],
            }
        else:
            if not isinstance(candidate_decision, ArtifactRef):
                raise DispatchError("run requires candidate decision ArtifactRef")
            if not isinstance(bound_preflight, BoundPreflightResult) or not isinstance(
                transition, PreflightTransition
            ):
                raise DispatchError("run requires bound preflight and transition")
            if bound_preflight.task_id != task_id:
                raise DispatchError("run preflight task_id is split")
            if bound_preflight.candidate_decision != candidate_decision:
                raise DispatchError("run candidate decision is split")
            if not _json_type_equal(bound_preflight.selector, selector):
                raise DispatchError("run selector is split")
            _validate_bound_inputs(
                root_descriptor,
                [
                    bound_preflight.brief,
                    bound_preflight.candidate_evidence,
                    bound_preflight.candidate_decision,
                    bound_preflight.prompt,
                    bound_preflight.raw,
                    bound_preflight.transcript,
                    bound_preflight.occurrence_ref,
                ],
            )
            candidate_evidence_payload, _ = _read_reference(
                root_descriptor, bound_preflight.candidate_evidence
            )
            candidate_decision_payload, _ = _read_reference(
                root_descriptor, bound_preflight.candidate_decision
            )
            candidate_evidence_value = _strict_object(
                candidate_evidence_payload, "candidate route evidence"
            )
            candidate_decision_value = _strict_object(
                candidate_decision_payload, "candidate decision"
            )
            candidate_errors = cmr_compiler.validate_route_evidence(
                candidate_evidence_value, _frozen_ontologies()
            )
            candidate_errors += _decision_errors(candidate_decision_value)
            if candidate_errors:
                raise DispatchError(candidate_errors)
            if transition.outcome == "replaced":
                if (
                    transition.replacement_evidence is None
                    or transition.final_decision is None
                ):
                    raise DispatchError("replaced transition is incomplete")
                monotonic_errors = cmr_compiler.validate_preflight_monotonicity(
                    candidate_evidence_value,
                    candidate_decision_value,
                    transition.replacement_evidence,
                    transition.final_decision,
                    _frozen_ontologies(),
                )
                if monotonic_errors:
                    raise DispatchError(
                        monotonic_errors, "preflight_non_monotonic"
                    )
            occurrence = bound_preflight.occurrence
            assert occurrence.usage is not None
            invocation_value = {
                "schema_version": "cmr-preflight-invocation-v1",
                "phase": "preflight",
                "task_id": task_id,
                "brief": bound_preflight.brief.to_dict(),
                "candidate_evidence": bound_preflight.candidate_evidence.to_dict(),
                "candidate_decision": bound_preflight.candidate_decision.to_dict(),
                "selector": copy.deepcopy(dict(selector)),
                "prompt": bound_preflight.prompt.to_dict(),
                "raw": bound_preflight.raw.to_dict(),
                "transcript": bound_preflight.transcript.to_dict(),
                "occurrence": bound_preflight.occurrence_ref.to_dict(),
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "fork_turns": "none",
                "thread_id": occurrence.thread_id,
                "cwd": copy.deepcopy(occurrence.cwd),
                "exit_code": 0,
                "external_writes": False,
                "usage": occurrence.usage.to_dict(),
            }
            invocation_ref = _exclusive_write(
                root_descriptor,
                f"{prefix}/invocation.json",
                _canonical_json_bytes(invocation_value),
            )
            common = {
                "schema_version": "cmr-preflight-evidence-v1",
                "task_id": task_id,
                "selector": copy.deepcopy(dict(selector)),
                "candidate_decision": candidate_decision.to_dict(),
                "invocation": invocation_ref.to_dict(),
            }
            if transition.outcome == "accepted":
                candidate_payload, _ = _read_reference(
                    root_descriptor, candidate_decision
                )
                if transition.final_decision is None or not _json_type_equal(
                    _strict_object(candidate_payload, "candidate decision"),
                    transition.final_decision,
                ):
                    raise DispatchError("accepted transition changed candidate bytes")
                if transition.replacement_evidence is not None or transition.blocker_ids:
                    raise DispatchError("accepted transition has forbidden fields")
                evidence_value = {**common, "outcome": "accepted"}
                final_ref = candidate_decision
            elif transition.outcome == "replaced":
                if (
                    transition.replacement_evidence is None
                    or transition.final_decision is None
                    or transition.blocker_ids
                ):
                    raise DispatchError("replaced transition is incomplete")
                replacement_ref = _exclusive_write(
                    root_descriptor,
                    f"{prefix}/replacement-evidence.json",
                    _canonical_json_bytes(transition.replacement_evidence),
                )
                final_ref = _exclusive_write(
                    root_descriptor,
                    f"{prefix}/final-decision.json",
                    _canonical_json_bytes(transition.final_decision),
                )
                evidence_value = {
                    **common,
                    "outcome": "replaced",
                    "replacement_evidence": replacement_ref.to_dict(),
                    "final_decision": final_ref.to_dict(),
                }
            elif transition.outcome == "blocked":
                if transition.final_decision is not None or transition.replacement_evidence is not None:
                    raise DispatchError("blocked transition cannot carry final artifacts")
                blockers = list(transition.blocker_ids)
                blocker_errors = _blocker_errors(blockers, "transition.blocker_ids")
                if blocker_errors:
                    raise DispatchError(blocker_errors)
                evidence_value = {
                    **common,
                    "outcome": "blocked",
                    "blocker_ids": blockers,
                }
            else:
                raise DispatchError("unknown preflight transition outcome")

        evidence_ref = _exclusive_write(
            root_descriptor,
            f"{prefix}/evidence.json",
            _canonical_json_bytes(evidence_value),
        )
        _seal_directory(root_descriptor, prefix)
        if not _root_identity_unchanged(Path(ledger_root), root_stat):
            raise DispatchError("ledger root identity changed during preflight write")
    finally:
        os.close(root_descriptor)
    errors = validate_preflight_evidence(
        Path(ledger_root) / evidence_ref.path, Path(ledger_root)
    )
    if errors:
        raise DispatchError(errors)
    return PreflightArtifacts(
        invocation=invocation_ref,
        evidence=evidence_ref,
        replacement_evidence=replacement_ref,
        final_decision=final_ref,
    )


def _controller_evidence_details(
    root_descriptor: int,
    reference: ArtifactRef,
) -> tuple[list[str], dict[str, Any] | None, str | None]:
    errors: list[str] = []
    try:
        payload, _ = _read_reference(root_descriptor, reference)
        evidence = _strict_object(payload, "controller evidence")
        errors += cmr_compiler.validate_route_evidence(
            evidence, _frozen_ontologies()
        )
        if errors:
            return sorted(set(errors)), evidence, None
        raw_ref = _ref(evidence["raw"], "controller raw")
        brief_ref = _ref(evidence["brief"], "controller brief")
        _read_reference(root_descriptor, brief_ref)
        raw_payload, _ = _read_reference(root_descriptor, raw_ref)
        occurrence = evidence["occurrence"]
        expected = cmr_runtime.OccurrenceExpectation(
            phase="controller",
            scope_kind="task",
            scope_id=evidence["task_id"],
            round_index=None,
            task_id=evidence["task_id"],
            model="gpt-5.6-luna",
            reasoning_effort="xhigh",
            prompt_sha256=brief_ref.sha256,
            forbidden_thread_ids=(),
            prior_thread_id=None,
            fork_turns="none",
            thread_policy="fresh",
            cwd_policy=dict(occurrence["cwd"]),
            write_policy="control_plane_no_write",
            response_policy="closed_json",
        )
        parsed = cmr_runtime.audit_jsonl(
            raw_payload,
            expected,
            lambda value: cmr_contracts.validate_controller_result(
                value, _frozen_ontologies()
            ),
        )
        if not _occurrence_matches_parsed(occurrence, parsed):
            errors.append("controller raw events do not bind occurrence")
        expected_result = {
            "schema_version": "cmr-controller-result-v1",
            "fact_ids": list(evidence["fact_ids"]),
            "uncertainty_ids": list(evidence["uncertainty_ids"]),
            "action_intents": copy.deepcopy(evidence["action_intents"]),
        }
        if not _json_type_equal(parsed.raw_object, expected_result):
            errors.append("controller raw result does not bind route evidence")
        return sorted(set(errors)), evidence, occurrence.get("thread_id")
    except (DispatchError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"controller evidence is invalid: {exc}")
        return sorted(set(errors)), None, None


def _bundle_value_errors(
    value: Mapping[str, Any],
    ledger_root: Path,
) -> list[str]:
    errors = _bundle_shape_errors(value)
    if errors:
        return errors
    root_descriptor = -1
    try:
        root_descriptor, root_stat = _open_root(ledger_root)
        references = {
            field: _ref(value[field], f"dispatch bundle.{field}")
            for field in (
                "brief",
                "controller_evidence",
                "runtime_input",
                "candidate_decision",
                "preflight_evidence",
                "final_decision",
            )
        }
        payloads: dict[str, bytes] = {}
        for field, reference in references.items():
            payloads[field], _ = _read_reference(root_descriptor, reference)
        task_id = value["task_id"]
        controller_errors, controller, controller_thread = _controller_evidence_details(
            root_descriptor, references["controller_evidence"]
        )
        errors += controller_errors
        runtime_input = _strict_object(payloads["runtime_input"], "runtime input")
        candidate = _strict_object(payloads["candidate_decision"], "candidate decision")
        final = _strict_object(payloads["final_decision"], "final decision")
        errors += cmr_compiler.validate_runtime_input(
            runtime_input, _frozen_ontologies()
        )
        errors += _decision_errors(candidate)
        errors += _decision_errors(final)
        if controller is not None:
            if controller.get("task_id") != task_id:
                errors.append("controller evidence task_id does not bind bundle")
            if not _json_type_equal(controller.get("brief"), value.get("brief")):
                errors.append("controller evidence brief does not bind bundle")
            try:
                expected_candidate = cmr_compiler.compile_route(
                    controller, runtime_input, _frozen_ontologies()
                )
                if not _json_type_equal(expected_candidate, candidate):
                    errors.append("candidate decision is not the compiled route")
            except cmr_compiler.CompilationError as exc:
                errors.append(f"candidate compilation failed: {exc.blocker_id}")
        if candidate.get("task_id") != task_id or final.get("task_id") != task_id:
            errors.append("candidate or final task_id does not bind bundle")
        if not _json_type_equal(candidate.get("brief"), value.get("brief")):
            errors.append("candidate brief does not bind bundle")
        if not _json_type_equal(final.get("brief"), value.get("brief")):
            errors.append("final brief does not bind bundle")

        preflight_errors, preflight, observed_preflight_ref, invocation, raw_result = (
            _preflight_evidence_details(
                ledger_root / references["preflight_evidence"].path,
                ledger_root,
            )
        )
        errors += preflight_errors
        if observed_preflight_ref != references["preflight_evidence"]:
            errors.append("preflight evidence reference is stale")
        if preflight is None:
            return sorted(set(errors))
        if preflight.get("task_id") != task_id:
            errors.append("preflight evidence task_id does not bind bundle")
        outcome = preflight.get("outcome")
        if outcome == "blocked":
            errors.append("blocked preflight never authorizes dispatch")
        elif outcome in {"skipped", "accepted"}:
            if not _json_type_equal(
                value.get("candidate_decision"), value.get("final_decision")
            ):
                errors.append("skip and accept require identical candidate/final refs")
            if not _json_type_equal(candidate, final):
                errors.append("skip and accept cannot change final semantics")
            if not _json_type_equal(
                preflight.get("candidate_decision"), value.get("candidate_decision")
            ):
                errors.append("preflight candidate reference is split")
        elif outcome == "replaced":
            if not _json_type_equal(
                preflight.get("candidate_decision"), value.get("candidate_decision")
            ):
                errors.append("replacement candidate reference is split")
            if not _json_type_equal(
                preflight.get("final_decision"), value.get("final_decision")
            ):
                errors.append("replacement final decision reference is split")
            replacement_ref = _ref(
                preflight.get("replacement_evidence"), "replacement evidence"
            )
            replacement_payload, _ = _read_reference(
                root_descriptor, replacement_ref
            )
            replacement = _strict_object(
                replacement_payload, "replacement route evidence"
            )
            if isinstance(raw_result, Mapping):
                for field in ("fact_ids", "uncertainty_ids", "action_intents"):
                    if not _json_type_equal(
                        replacement.get(field), raw_result.get(field)
                    ):
                        errors.append(
                            f"replacement {field} does not bind preflight result"
                        )
            try:
                expected_final = cmr_compiler.compile_route(
                    replacement, runtime_input, _frozen_ontologies()
                )
                if not _json_type_equal(expected_final, final):
                    errors.append("replacement final decision was not recompiled")
                if controller is not None:
                    monotonic_errors = (
                        cmr_compiler.validate_preflight_monotonicity(
                            controller,
                            candidate,
                            replacement,
                            final,
                            _frozen_ontologies(),
                        )
                    )
                    if monotonic_errors:
                        errors.append("preflight_non_monotonic")
                        errors.extend(monotonic_errors)
            except cmr_compiler.CompilationError as exc:
                errors.append(f"replacement compilation failed: {exc.blocker_id}")
        else:
            errors.append("preflight evidence outcome cannot authorize dispatch")

        if invocation is not None:
            if not _json_type_equal(
                invocation.get("brief"), value.get("brief")
            ):
                errors.append("preflight invocation brief is split")
            if not _json_type_equal(
                invocation.get("candidate_evidence"),
                value.get("controller_evidence"),
            ):
                errors.append("preflight invocation controller evidence is split")
            if not _json_type_equal(
                invocation.get("candidate_decision"),
                value.get("candidate_decision"),
            ):
                errors.append("preflight invocation candidate decision is split")
            if invocation.get("thread_id") == controller_thread:
                errors.append("preflight thread reused controller thread")
        if outcome == "skipped":
            selector = preflight.get("selector")
            if isinstance(selector, Mapping):
                safe_ref = _ref(selector.get("safe_lane"), "skip safe-lane")
                safe_errors, manifest, observed_safe_ref, planning_invocation = (
                    _safe_lane_details(ledger_root / safe_ref.path, ledger_root)
                )
                errors += safe_errors
                if observed_safe_ref != safe_ref:
                    errors.append("skip safe-lane reference is stale")
                if planning_invocation is not None and planning_invocation.get(
                    "thread_id"
                ) == controller_thread:
                    errors.append("planning thread reused controller thread")
                if manifest is not None:
                    if sum(
                        isinstance(entry, Mapping)
                        and entry.get("task_id") == task_id
                        and _json_type_equal(entry.get("brief"), value.get("brief"))
                        for entry in manifest.get("entries", [])
                    ) != 1:
                        errors.append("dispatch task has no exact safe-lane entry")
        if not _root_identity_unchanged(ledger_root, root_stat):
            errors.append("ledger root identity changed during dispatch validation")
        return sorted(set(errors))
    except (DispatchError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"dispatch bundle binding is invalid: {exc}")
        return sorted(set(errors))
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def materialize_dispatch_bundle(
    *,
    task_id: str,
    brief: ArtifactRef,
    controller_evidence: ArtifactRef,
    runtime_input: ArtifactRef,
    candidate_decision: ArtifactRef,
    preflight_evidence: ArtifactRef,
    final_decision: ArtifactRef,
    ledger_root: Path,
) -> ArtifactRef:
    component = _task_component(task_id)
    references = (
        brief,
        controller_evidence,
        runtime_input,
        candidate_decision,
        preflight_evidence,
        final_decision,
    )
    if any(not isinstance(reference, ArtifactRef) for reference in references):
        raise DispatchError("dispatch bundle requires non-null ArtifactRef inputs")
    value = {
        "schema_version": "cmr-dispatch-bundle-v1",
        "task_id": task_id,
        "brief": brief.to_dict(),
        "controller_evidence": controller_evidence.to_dict(),
        "runtime_input": runtime_input.to_dict(),
        "candidate_decision": candidate_decision.to_dict(),
        "preflight_evidence": preflight_evidence.to_dict(),
        "final_decision": final_decision.to_dict(),
    }
    errors = _bundle_value_errors(value, Path(ledger_root))
    if errors:
        raise DispatchError(errors)
    root_descriptor, root_stat = _open_root(Path(ledger_root))
    try:
        relative_path = f"tasks/{component}/dispatch/bundle.json"
        reference = _exclusive_write(
            root_descriptor, relative_path, _canonical_json_bytes(value)
        )
        _seal_directory(root_descriptor, f"tasks/{component}/dispatch")
        if not _root_identity_unchanged(Path(ledger_root), root_stat):
            raise DispatchError("ledger root identity changed during bundle write")
    finally:
        os.close(root_descriptor)
    readback_errors = validate_dispatch_bundle(
        Path(ledger_root) / reference.path, Path(ledger_root)
    )
    if readback_errors:
        raise DispatchError(readback_errors)
    return reference


def validate_dispatch_bundle(bundle_path: Path, ledger_root: Path) -> list[str]:
    root_descriptor = -1
    errors: list[str] = []
    scope_state: tuple[set[str], os.stat_result] | None = None
    try:
        root_descriptor, root_stat = _open_root(Path(ledger_root))
        payload, bundle_ref, _ = _read_artifact_path(
            root_descriptor, Path(bundle_path), Path(ledger_root)
        )
        value = _strict_object(payload, "dispatch bundle")
        schema_version = value.get("schema_version")
        if isinstance(schema_version, str) and schema_version.startswith("qfr-"):
            return ["legacy_dispatch_forbidden"]
        errors += _bundle_shape_errors(value)
        if not errors:
            component = _task_component(value["task_id"])
            expected_path = f"tasks/{component}/dispatch/bundle.json"
            if bundle_ref.path != expected_path:
                errors.append("dispatch bundle path is not canonical")
            try:
                members, directory_stat = _directory_state(
                    root_descriptor, f"tasks/{component}/dispatch"
                )
                scope_state = members, directory_stat
                if members != {"bundle.json"}:
                    errors.append("dispatch directory membership is not exact")
                if directory_stat.st_mode & 0o222:
                    errors.append("dispatch directory must be sealed")
            except OSError:
                errors.append("dispatch directory is unavailable")
            errors += _bundle_value_errors(value, Path(ledger_root))
            if scope_state is not None and not _directory_state_unchanged(
                root_descriptor,
                f"tasks/{component}/dispatch",
                *scope_state,
            ):
                errors.append("dispatch scope identity changed during validation")
        if not _root_identity_unchanged(Path(ledger_root), root_stat):
            errors.append("ledger root identity changed during bundle readback")
    except (DispatchError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"dispatch bundle is invalid: {exc}")
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
    if errors:
        return ["dispatch_bundle_invalid", *sorted(set(errors))]
    return []


__all__ = [
    "ArtifactRef",
    "BoundPreflightResult",
    "DispatchError",
    "PlanningEvidence",
    "PreflightArtifacts",
    "PreflightTransition",
    "SafeLaneArtifacts",
    "apply_preflight",
    "bind_planner_result",
    "bind_preflight_result",
    "expected_dispatch_schemas",
    "materialize_dispatch_bundle",
    "materialize_preflight",
    "materialize_safe_lane",
    "select_preflight",
    "validate_dispatch_bundle",
    "validate_preflight_evidence",
    "validate_safe_lane_manifest",
]
