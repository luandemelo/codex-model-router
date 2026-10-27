"""Versioned, task-bound latency preference; model selection remains deterministic.

The preference records a positive instruction quoted from the exact task brief.
Its semantic applicability is independently adjudicated by preflight. Structural
validation does not claim to prove the meaning of natural language.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping

import cmr_contracts

PROFILE = {
    "schema_version": "cmr-routing-profile-v1",
    "profile_id": "luna-default-terra-latency-v1",
    "default_clear": {"model": "gpt-6-luna", "reasoning_effort": "xhigh"},
    "default_dense": {"model": "gpt-6-luna", "reasoning_effort": "max"},
    "latency_override": {"model": "gpt-6.1-terra", "reasoning_effort": "xhigh"},
    "latency_review": {"model": "gpt-6.1-sol", "reasoning_effort": "medium"},
    "latency_scope": "explicit_task_only",
    "fallback": "forbidden",
    "tradeoff": "latency_over_model_cost",
    "precedence": "existing_risk_recurrence_review_gates",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def profile_binding() -> dict[str, str]:
    return {"profile_id": PROFILE["profile_id"],
            "profile_sha256": hashlib.sha256(_canonical(PROFILE)).hexdigest()}


def _exact(value: Any, fields: set[str], label: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{label} must be an object"]
    return ([f"{label}: missing field {key}" for key in sorted(fields - set(value))]
            + [f"{label}: unknown field {key}" for key in sorted(set(value) - fields)])


def validate_policy(value: Any, *, task_id: Any = None, task_phase: Any = None,
                    brief: Any = None, brief_bytes: bytes | None = None) -> list[str]:
    errors = _exact(value, {"profile", "latency_preference"}, "routing policy")
    if not isinstance(value, Mapping):
        return errors
    if value.get("profile") != profile_binding():
        errors.append("routing policy profile binding is missing, stale, or invalid")
    preference = value.get("latency_preference")
    if preference is None:
        return sorted(set(errors))
    fields = {"schema_version", "task_id", "task_phase", "brief", "preference",
              "basis", "tradeoff", "evidence"}
    errors += _exact(preference, fields, "latency preference")
    if not isinstance(preference, Mapping):
        return sorted(set(errors))
    constants = {"schema_version": "cmr-latency-preference-v1", "preference": "latency",
                 "basis": "explicit_task_opt_in", "tradeoff": "latency_over_model_cost"}
    for field, expected in constants.items():
        if preference.get(field) != expected:
            errors.append(f"latency preference.{field} is invalid")
    if not isinstance(preference.get("task_id"), str) or not preference["task_id"].strip():
        errors.append("latency preference.task_id must be non-empty")
    if not isinstance(preference.get("task_phase"), str) or preference["task_phase"] not in {"implementation", "final_review"}:
        errors.append("latency preference.task_phase is invalid")
    for field, expected in (("task_id", task_id), ("task_phase", task_phase), ("brief", brief)):
        if expected is not None and preference.get(field) != expected:
            errors.append(f"latency preference.{field} does not bind this occurrence")
    reference = preference.get("brief")
    errors += _exact(reference, {"path", "sha256", "size_bytes"}, "latency preference.brief")
    if isinstance(reference, Mapping):
        if not cmr_contracts._valid_target("absolute_path", "/" + str(reference.get("path", ""))):
            errors.append("latency preference.brief.path must be a normalized relative path")
        path = reference.get("path")
        if not isinstance(path, str) or not path or path.startswith("/") or ":" in path:
            errors.append("latency preference.brief.path must be a normalized relative path")
        digest = reference.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(cmr_contracts.LOWER_SHA256_PATTERN, digest) is None:
            errors.append("latency preference.brief.sha256 is invalid")
        size = reference.get("size_bytes")
        if type(size) is not int or size < 0:
            errors.append("latency preference.brief.size_bytes is invalid")
    evidence = preference.get("evidence")
    errors += _exact(evidence, {"start_byte", "end_byte", "quote"}, "latency preference.evidence")
    if isinstance(evidence, Mapping):
        start, end, quote = (evidence.get(key) for key in ("start_byte", "end_byte", "quote"))
        valid_span = type(start) is int and type(end) is int and 0 <= start < end
        if not valid_span:
            errors.append("latency preference evidence requires an integer byte span")
        if not isinstance(quote, str) or not quote.strip():
            errors.append("latency preference evidence quote must be non-empty")
        elif valid_span:
            try:
                if len(quote.encode("utf-8")) != end - start:
                    errors.append("latency preference evidence quote does not bind span")
                if isinstance(reference, Mapping) and type(reference.get("size_bytes")) is int and end > reference["size_bytes"]:
                    errors.append("latency preference evidence exceeds the bound brief")
                if brief_bytes is not None:
                    if type(brief_bytes) is not bytes or brief_bytes[start:end].decode("utf-8") != quote:
                        errors.append("latency preference evidence quote does not bind exact brief bytes")
            except UnicodeError:
                errors.append("latency preference evidence must bind UTF-8 byte boundaries")
    if brief_bytes is not None and isinstance(reference, Mapping):
        if type(brief_bytes) is not bytes or (hashlib.sha256(brief_bytes).hexdigest(), len(brief_bytes)) != (
                reference.get("sha256"), reference.get("size_bytes")):
            errors.append("latency preference brief binding is stale")
    return sorted(set(errors))


_LATENCY_MARKER = b"\nCMR_LATENCY_PREFERENCE_ATTACHMENT_V1\n"
_LATENCY_INSTRUCTIONS = (
    b"Independently verify the exact routing profile and per-task preference against the full brief. "
    b"A non-null preference requires positive explicit authorization for this task and phase, "
    b"quoted at the bound UTF-8 byte span, accepting latency over model cost. Global defaults, "
    b"hypothetical, negated, stale, or unrelated instructions are insufficient. Block unsupported "
    b"or ambiguous preference evidence; do not remove or rewrite the preference, silently fall "
    b"back, or emit a model, effort, or route. Existing risk, recurrence, judgment, integration "
    b"and review gates retain precedence. Latency is a time/cost tradeoff, not a cheaper-model claim.\n"
)


def build_preflight_latency_attachment(routing_policy: Mapping[str, Any]) -> bytes:
    errors = validate_policy(routing_policy)
    if errors:
        raise ValueError("; ".join(errors))
    return _LATENCY_MARKER + _LATENCY_INSTRUCTIONS + _canonical({"routing_policy": routing_policy}) + b"\n"


def validate_preflight_latency_attachment(prompt_bytes: bytes, routing_policy: Any,
                                           gate_attachment: bytes) -> list[str]:
    try:
        attachment = build_preflight_latency_attachment(routing_policy)
    except (ValueError, TypeError) as exc:
        return [f"latency attachment is invalid: {exc}"]
    if (type(prompt_bytes) is not bytes or prompt_bytes.count(_LATENCY_MARKER) != 1
            or not prompt_bytes.endswith(attachment + gate_attachment)):
        return ["preflight prompt lacks its exact deterministic latency preference attachment"]
    return []


def _closed(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": dict(properties)}


def preference_schema(artifact_ref: Mapping[str, Any]) -> dict[str, Any]:
    return _closed({"schema_version": {"const": "cmr-latency-preference-v1"},
        "task_id": {"type": "string", "minLength": 1},
        "task_phase": {"enum": ["implementation", "final_review"]},
        "brief": copy.deepcopy(artifact_ref), "preference": {"const": "latency"},
        "basis": {"const": "explicit_task_opt_in"}, "tradeoff": {"const": "latency_over_model_cost"},
        "evidence": _closed({"start_byte": {"type": "integer", "minimum": 0},
            "end_byte": {"type": "integer", "minimum": 1}, "quote": {"type": "string", "minLength": 1}})})


def policy_schema(artifact_ref: Mapping[str, Any]) -> dict[str, Any]:
    return _closed({"profile": {"const": profile_binding()},
        "latency_preference": {"oneOf": [preference_schema(artifact_ref), {"type": "null"}]}})
