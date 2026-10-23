from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import cmr_contracts


SCHEMA_VERSION = "cmr-gate-evidence-v1"
_TASK_PHASES = frozenset({"implementation", "final_review"})
_SOURCE_PHASES = frozenset({"controller", "preflight"})
_BASIS_VALUES = ("requested_change", "concrete_decision", "critical_assurance")
_TOP_FIELDS = frozenset(
    {"schema_version", "task_id", "task_phase", "brief", "source", "claims"}
)
_SOURCE_FIELDS = frozenset({"phase", "raw", "thread_id", "semantic_sha256"})
_CLAIM_FIELDS = frozenset(
    {"fact_id", "basis", "start_byte", "end_byte", "quote", "explanation"}
)
_ARTIFACT_REF_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_SEMANTIC_FIELDS = ("fact_ids", "uncertainty_ids", "action_intents")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE_RE = re.compile(r"[A-Za-z]:")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    return copy.deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class GateEvidenceContext:
    task_id: str
    task_phase: str
    brief: Mapping[str, Any]
    source_phase: str
    source_raw: Mapping[str, Any]
    source_thread_id: str
    semantic: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in ("brief", "source_raw", "semantic"):
            original = getattr(self, name)
            object.__setattr__(self, name, _deep_freeze(original))


class GateEvidenceError(ValueError):
    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(sorted(set(errors)))
        detail = "; ".join(self.errors) if self.errors else "invalid gate evidence"
        super().__init__(f"gate evidence invalid: {detail}")


@dataclass(frozen=True)
class BoundGateEvidence:
    """Immutable snapshot of structurally validated gate evidence."""

    _value: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_value", _deep_freeze(self._value))

    @property
    def value(self) -> dict[str, Any]:
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return _thaw(self._value)

    def assert_matches(
        self,
        context: GateEvidenceContext,
        brief_bytes: bytes,
        ontologies: cmr_contracts.Ontologies,
    ) -> None:
        errors = validate_gate_evidence(
            self.to_dict(),
            context=context,
            brief_bytes=brief_bytes,
            ontologies=ontologies,
        )
        if errors:
            raise GateEvidenceError(errors)


def required_gate_ids(
    fact_ids: Sequence[str], ontologies: cmr_contracts.Ontologies
) -> tuple[str, ...]:
    """Return asserted hard or integration/elevated-risk fact IDs in lexical order."""
    if isinstance(fact_ids, (str, bytes)) or not isinstance(fact_ids, Sequence):
        return ()
    asserted = {fact_id for fact_id in fact_ids if isinstance(fact_id, str)}
    required: set[str] = set()
    for entry in getattr(ontologies, "fact_entries", ()):
        if not isinstance(entry, Mapping):
            continue
        fact_id = entry.get("id")
        if not isinstance(fact_id, str) or fact_id not in asserted:
            continue
        if entry.get("hard_gate") is True or entry.get("routing_class") in {
            "integration",
            "elevated_risk",
        }:
            required.add(fact_id)
    return tuple(sorted(required))


def _canonical_semantic_bytes(semantic: Mapping[str, Any]) -> bytes:
    payload = {field: _thaw(semantic[field]) for field in _SEMANTIC_FIELDS}
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _exact_fields(value: Any, expected: frozenset[str], label: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{label} must be an object"]
    keys = list(value.keys())
    string_keys = {key for key in keys if isinstance(key, str)}
    errors = [
        f"{label}: unknown field {key!r}"
        for key in keys
        if not isinstance(key, str) or key not in expected
    ]
    errors.extend(
        f"{label}: missing field {key}" for key in sorted(expected - string_keys)
    )
    return errors


def _is_relative_artifact_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or _DRIVE_RE.match(value) or "\\" in value:
        return False
    if _CONTROL_RE.search(value):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _validate_artifact_ref(value: Any, label: str) -> list[str]:
    errors = _exact_fields(value, _ARTIFACT_REF_FIELDS, label)
    if not isinstance(value, Mapping):
        return errors
    if not _is_relative_artifact_path(value.get("path")):
        errors.append(f"{label}.path must be a normalized relative POSIX path")
    sha256 = value.get("sha256")
    if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
        errors.append(f"{label}.sha256 must be lowercase SHA-256")
    size_bytes = value.get("size_bytes")
    if type(size_bytes) is not int or size_bytes < 0:
        errors.append(f"{label}.size_bytes must be a nonnegative integer")
    return errors


def _ref_equal(left: Any, right: Any) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return all(
        type(left.get(field)) is type(right.get(field))
        and left.get(field) == right.get(field)
        for field in _ARTIFACT_REF_FIELDS
    ) and set(left) == _ARTIFACT_REF_FIELDS and set(right) == _ARTIFACT_REF_FIELDS


def _validate_semantic_context(
    semantic: Any, ontologies: cmr_contracts.Ontologies
) -> list[str]:
    errors = _exact_fields(semantic, frozenset(_SEMANTIC_FIELDS), "context.semantic")
    if not isinstance(semantic, Mapping):
        return errors
    # The existing semantic contract is the authority for ID membership, order,
    # uniqueness, and action shape. It is a pure value validator.
    semantic_envelope = {"schema_version": "cmr-controller-result-v4"}
    semantic_envelope.update(
        {
            key: _thaw(value)
            for key, value in semantic.items()
            if isinstance(key, str)
        }
    )
    errors.extend(cmr_contracts.validate_controller_result(semantic_envelope, ontologies))
    return errors


def _context_value_errors(
    context: Any,
    brief_bytes: Any,
    ontologies: cmr_contracts.Ontologies,
) -> tuple[list[str], dict[str, Any] | None, Mapping[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(context, GateEvidenceContext):
        return ["context must be GateEvidenceContext"], None, None

    if not isinstance(context.task_id, str) or not context.task_id:
        errors.append("context.task_id must be a non-empty string")
    if not isinstance(context.task_phase, str) or context.task_phase not in _TASK_PHASES:
        errors.append("context.task_phase must be implementation or final_review")
    errors.extend(_validate_artifact_ref(context.brief, "context.brief"))
    if not isinstance(context.source_phase, str) or context.source_phase not in _SOURCE_PHASES:
        errors.append("context.source_phase must be controller or preflight")
    errors.extend(_validate_artifact_ref(context.source_raw, "context.source.raw"))
    if not isinstance(context.source_thread_id, str) or not context.source_thread_id:
        errors.append("context.source.thread_id must be a non-empty string")

    semantic_errors = _validate_semantic_context(context.semantic, ontologies)
    errors.extend(semantic_errors)
    if not isinstance(context.semantic, Mapping) or semantic_errors:
        semantic = None
    else:
        semantic = context.semantic

    if type(brief_bytes) is not bytes:
        errors.append("brief bytes must be bytes")
    else:
        try:
            brief_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            errors.append("brief bytes must be valid UTF-8")
        if not any(error.startswith("context.brief") for error in errors):
            brief_ref = context.brief
            digest = hashlib.sha256(brief_bytes).hexdigest()
            if brief_ref.get("sha256") != digest:
                errors.append("context.brief.sha256 does not match brief bytes")
            if brief_ref.get("size_bytes") != len(brief_bytes):
                errors.append("context.brief.size_bytes does not match brief bytes")

    return errors, semantic, context.brief if isinstance(context.brief, Mapping) else None


def validate_gate_evidence(
    value: Any,
    *,
    context: GateEvidenceContext,
    brief_bytes: bytes,
    ontologies: cmr_contracts.Ontologies,
) -> list[str]:
    """Validate structure, exact source binding, gate coverage, and UTF-8 quotes.

    This validates bytes and source identity. It does not decide whether quoted
    natural-language text truthfully describes a request, decision, or assurance.
    """
    errors: list[str] = []
    context_errors, semantic, expected_brief = _context_value_errors(
        context, brief_bytes, ontologies
    )
    errors.extend(context_errors)

    errors.extend(_exact_fields(value, _TOP_FIELDS, "gate evidence"))
    if not isinstance(value, Mapping):
        return sorted(set(errors))

    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"gate evidence.schema_version must equal {SCHEMA_VERSION}")
    task_id = value.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        errors.append("gate evidence.task_id must be a non-empty string")
    elif isinstance(context, GateEvidenceContext) and task_id != context.task_id:
        errors.append("gate evidence.task_id does not match context.task_id")

    task_phase = value.get("task_phase")
    if not isinstance(task_phase, str) or task_phase not in _TASK_PHASES:
        errors.append("gate evidence.task_phase must be implementation or final_review")
    elif isinstance(context, GateEvidenceContext) and task_phase != context.task_phase:
        errors.append("gate evidence.task_phase does not match context.task_phase")

    errors.extend(_validate_artifact_ref(value.get("brief"), "gate evidence.brief"))
    if isinstance(expected_brief, Mapping) and not _ref_equal(value.get("brief"), expected_brief):
        errors.append("gate evidence.brief does not match context.brief")
    if isinstance(value.get("brief"), Mapping) and type(brief_bytes) is bytes:
        reference = value["brief"]
        if reference.get("sha256") != hashlib.sha256(brief_bytes).hexdigest():
            errors.append("gate evidence.brief.sha256 does not match brief bytes")
        if type(reference.get("size_bytes")) is int and reference["size_bytes"] != len(brief_bytes):
            errors.append("gate evidence.brief.size_bytes does not match brief bytes")

    source = value.get("source")
    errors.extend(_exact_fields(source, _SOURCE_FIELDS, "gate evidence.source"))
    if isinstance(source, Mapping):
        source_phase = source.get("phase")
        if not isinstance(source_phase, str) or source_phase not in _SOURCE_PHASES:
            errors.append("gate evidence.source.phase must be controller or preflight")
        if isinstance(context, GateEvidenceContext):
            if source_phase != context.source_phase:
                errors.append("gate evidence.source.phase does not match context.source_phase")
            if source.get("thread_id") != context.source_thread_id:
                errors.append("gate evidence.source.thread_id does not match context")
            if not _ref_equal(source.get("raw"), context.source_raw):
                errors.append("gate evidence.source.raw does not match context.source.raw")
        errors.extend(_validate_artifact_ref(source.get("raw"), "gate evidence.source.raw"))
        thread_id = source.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            errors.append("gate evidence.source.thread_id must be a non-empty string")
        semantic_sha = source.get("semantic_sha256")
        if not isinstance(semantic_sha, str) or _SHA256_RE.fullmatch(semantic_sha) is None:
            errors.append("gate evidence.source.semantic_sha256 must be lowercase SHA-256")
        elif semantic is not None:
            try:
                expected_digest = hashlib.sha256(_canonical_semantic_bytes(semantic)).hexdigest()
            except (TypeError, ValueError, OverflowError):
                errors.append("context.semantic cannot be serialized as canonical JSON")
            else:
                if semantic_sha != expected_digest:
                    errors.append("gate evidence.source.semantic_sha256 does not match context.semantic")

    claims = value.get("claims")
    if not isinstance(claims, list):
        errors.append("gate evidence.claims must be an array")
        return sorted(set(errors))

    required_ids: tuple[str, ...] = ()
    if semantic is not None:
        fact_ids = semantic.get("fact_ids")
        if isinstance(fact_ids, Sequence) and not isinstance(fact_ids, (str, bytes)):
            required_ids = required_gate_ids(fact_ids, ontologies)

    seen_ids: list[str] = []
    ontology_ids = set(getattr(ontologies, "fact_ids", ()))
    for index, claim in enumerate(claims):
        label = f"gate evidence.claims[{index}]"
        errors.extend(_exact_fields(claim, _CLAIM_FIELDS, label))
        if not isinstance(claim, Mapping):
            continue
        fact_id = claim.get("fact_id")
        if not isinstance(fact_id, str) or not fact_id:
            errors.append(f"{label}.fact_id must be a non-empty string")
        else:
            seen_ids.append(fact_id)
            if fact_id not in ontology_ids:
                errors.append(f"{label}.fact_id is an unknown fact ID")

        basis = claim.get("basis")
        if not isinstance(basis, str) or basis not in _BASIS_VALUES:
            errors.append(f"{label}.basis must be one of {', '.join(_BASIS_VALUES)}")

        start = claim.get("start_byte")
        end = claim.get("end_byte")
        if type(start) is not int or type(end) is not int:
            errors.append(f"{label} offsets must be integers (boolean is invalid)")
        elif start < 0 or end <= start or type(brief_bytes) is not bytes or end > len(brief_bytes):
            errors.append(f"{label} offsets must be nonnegative, nonempty, and in bounds")
        else:
            sliced = brief_bytes[start:end]
            try:
                decoded = sliced.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                errors.append(f"{label} quote offsets must align to UTF-8 boundaries")
            else:
                quote = claim.get("quote")
                if not isinstance(quote, str) or not quote:
                    errors.append(f"{label}.quote must be a non-empty string")
                elif decoded != quote:
                    errors.append(f"{label}.quote must equal its exact brief byte slice")

        quote = claim.get("quote")
        if not isinstance(quote, str) or not quote:
            errors.append(f"{label}.quote must be a non-empty string")
        explanation = claim.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            errors.append(f"{label}.explanation must be a non-empty string")

    duplicates = sorted({fact_id for fact_id in seen_ids if seen_ids.count(fact_id) > 1})
    if duplicates:
        errors.append(f"gate evidence.claims contain duplicate fact IDs: {', '.join(duplicates)}")
    if seen_ids != sorted(seen_ids):
        errors.append("gate evidence.claims must be sorted by fact_id")
    seen_set = set(seen_ids)
    missing = sorted(set(required_ids) - seen_set)
    extra = sorted(seen_set - set(required_ids))
    if missing:
        errors.append(f"gate evidence.claims coverage is missing required gate IDs: {', '.join(missing)}")
    if extra:
        errors.append(f"gate evidence.claims coverage has extra fact IDs: {', '.join(extra)}")

    return sorted(set(errors))


def bind_gate_evidence(
    value: Any,
    *,
    context: GateEvidenceContext,
    brief_bytes: bytes,
    ontologies: cmr_contracts.Ontologies,
) -> BoundGateEvidence:
    try:
        snapshot = copy.deepcopy(value)
    except Exception as exc:
        raise GateEvidenceError([f"gate evidence cannot be copied: {type(exc).__name__}"]) from exc
    errors = validate_gate_evidence(
        snapshot,
        context=context,
        brief_bytes=brief_bytes,
        ontologies=ontologies,
    )
    if errors:
        raise GateEvidenceError(errors)
    return BoundGateEvidence(_deep_freeze(snapshot))


def expected_gate_evidence_schema(
    ontologies: cmr_contracts.Ontologies,
) -> dict[str, Any]:
    gate_ids = list(required_gate_ids(ontologies.fact_ids, ontologies))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "cmr-gate-evidence-v1.schema.json",
        "$comment": (
            "Runtime validation also binds task/source/semantic fields, checks exact gate coverage, "
            "validates UTF-8 brief bytes and non-whitespace explanations, and compares byte offsets "
            "to the brief. Structural validation does not decide whether quoted language truthfully "
            "supports a gate; independent semantic review does."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "task_id", "task_phase", "brief", "source", "claims"],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "task_id": {"type": "string", "minLength": 1},
            "task_phase": {"type": "string", "enum": sorted(_TASK_PHASES)},
            "brief": {"$ref": "#/$defs/ArtifactRef"},
            "source": {"$ref": "#/$defs/Source"},
            "claims": {
                "type": "array",
                "items": {"$ref": "#/$defs/Claim"},
            },
        },
        "$defs": {
            "ArtifactRef": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "sha256", "size_bytes"],
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "^(?!/)(?![A-Za-z]:)(?!.*\\\\)(?!.*//)(?!.*(?:^|/)\\.{1,2}(?:/|$))(?!.*[\\u0000-\\u001f\\u007f])[^/]+(?:/[^/]+)*$",
                    },
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "size_bytes": {"type": "integer", "minimum": 0},
                },
            },
            "Source": {
                "type": "object",
                "additionalProperties": False,
                "required": ["phase", "raw", "thread_id", "semantic_sha256"],
                "properties": {
                    "phase": {"type": "string", "enum": sorted(_SOURCE_PHASES)},
                    "raw": {"$ref": "#/$defs/ArtifactRef"},
                    "thread_id": {"type": "string", "minLength": 1},
                    "semantic_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
            },
            "Claim": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "fact_id",
                    "basis",
                    "start_byte",
                    "end_byte",
                    "quote",
                    "explanation",
                ],
                "properties": {
                    "fact_id": {"type": "string", "enum": gate_ids},
                    "basis": {"type": "string", "enum": list(_BASIS_VALUES)},
                    "start_byte": {"type": "integer", "minimum": 0},
                    "end_byte": {"type": "integer", "minimum": 0},
                    "quote": {"type": "string", "minLength": 1},
                    "explanation": {"type": "string", "minLength": 1},
                },
            },
        },
    }


__all__ = [
    "BoundGateEvidence",
    "GateEvidenceContext",
    "GateEvidenceError",
    "bind_gate_evidence",
    "expected_gate_evidence_schema",
    "required_gate_ids",
    "validate_gate_evidence",
]
