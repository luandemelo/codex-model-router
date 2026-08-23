from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.dont_write_bytecode = True

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
LOWER_SHA256_PATTERN = "^[0-9a-f]{64}$"
GIT_HEAD_PATTERN = "^(?:[0-9a-f]{40}|[0-9a-f]{64})$"

_HARD_GATE_FLAGS = {
    "architecture": "architecture_change",
    "authentication": "security",
    "authorization": "security",
    "backfill": "data_change",
    "billing": "financial",
    "concurrency": "concurrency_control",
    "critical_integrity": "integrity_or_irreversibility",
    "cryptography": "security",
    "destruction": "integrity_or_irreversibility",
    "distributed_consistency": "concurrency_control",
    "encryption": "security",
    "entitlement": "financial",
    "finding_critical": "critical_finding",
    "idempotency": "concurrency_control",
    "invoice": "financial",
    "irreversible_effect": "integrity_or_irreversibility",
    "ledger_financial": "financial",
    "locks": "concurrency_control",
    "material_data_transformation": "data_change",
    "multiple_plausible_tradeoffs": "architecture_change",
    "payment": "financial",
    "pii": "sensitive_data",
    "price": "financial",
    "public_api_structural_change": "architecture_change",
    "race_condition": "concurrency_control",
    "regulated_data": "sensitive_data",
    "rollback": "data_change",
    "schema_migration": "data_change",
    "secrets": "security",
    "structural_conflict": "architecture_change",
    "trust_boundary": "security",
}
_CRITICAL_FLAGS = {
    "security",
    "financial",
    "sensitive_data",
    "integrity_or_irreversibility",
    "critical_finding",
}
_INTEGRATION_FACTS = (
    "contract_coordination",
    "cross_module_integration",
    "integration_tests",
    "multi_module_integration",
)
_ELEVATED_FACTS = (
    "availability_or_integrity_impact",
    "critical_risk",
    "data_integrity",
    "elevated_risk",
    "incident_risk",
    "security_risk",
    "untrusted_input",
)
_LOW_FACTS = {
    "audit": ("audit", "audit"),
    "judgment_required": ("judgment", "judgment"),
    "reconciliation": ("reconciliation", "reconciliation"),
    "complex_local_execution": ("complex_local", "complex_local"),
    "architecture_approved": ("qualifier", "architecture_approved"),
    "clear_isolated_low_risk": ("qualifier", "clear_isolated_low_risk"),
    "low_blast_radius": ("qualifier", "low_blast_radius"),
}

UNCERTAINTY_IDS = (
    "multiple_policy_facts",
    "negation_contrast_or_condition",
    "indirect_or_ambiguous_implication",
    "controller_uncertainty",
)
SAFE_LANE_REASON_IDS = (
    "clear_isolated_low_risk",
    "single_module_bounded_change",
    "read_only_or_reversible_local_work",
    "approved_plan_no_open_design_decision",
)
PREFLIGHT_BLOCKER_IDS = (
    "ambiguous_semantics",
    "contradictory_facts",
    "insufficient_task_evidence",
)
ACTION_SPECS = (
    ("github_authenticate", "github_account"),
    ("repository_create", "repository"),
    ("repository_settings_update", "repository"),
    ("repository_topics_update", "repository"),
    ("issue_create", "repository"),
    ("issue_update", "numbered_resource"),
    ("issue_comment", "numbered_resource"),
    ("push", "full_ref"),
    ("pr_create", "pull_request_create"),
    ("pr_update", "numbered_resource"),
    ("merge", "numbered_resource"),
    ("tag_create", "tag_ref"),
    ("release_create", "release_tag"),
    ("deploy", "environment"),
    ("install_skill", "absolute_path"),
    ("install_plugin", "absolute_path"),
    ("marketplace_publish", "marketplace_plugin"),
)

_FACT_KEYS = {"id", "routing_class", "risk_projection", "hard_gate", "derived_flags"}
_ACTION_KEYS = {"id", "target_kind", "target_required"}
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _expected_fact_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for fact_id, flag in _HARD_GATE_FLAGS.items():
        entries.append(
            {
                "id": fact_id,
                "routing_class": "hard_gate",
                "risk_projection": "critical" if flag in _CRITICAL_FLAGS else "elevated",
                "hard_gate": True,
                "derived_flags": [flag],
            }
        )
    for fact_id in _INTEGRATION_FACTS:
        entries.append(
            {
                "id": fact_id,
                "routing_class": "integration",
                "risk_projection": "elevated",
                "hard_gate": False,
                "derived_flags": ["integration"],
            }
        )
    for fact_id in _ELEVATED_FACTS:
        entries.append(
            {
                "id": fact_id,
                "routing_class": "elevated_risk",
                "risk_projection": "elevated",
                "hard_gate": False,
                "derived_flags": ["elevated_risk"],
            }
        )
    for fact_id, (routing_class, flag) in _LOW_FACTS.items():
        entries.append(
            {
                "id": fact_id,
                "routing_class": routing_class,
                "risk_projection": "low",
                "hard_gate": False,
                "derived_flags": [flag],
            }
        )
    return sorted(entries, key=lambda entry: entry["id"])


FACT_ENTRIES = tuple(_expected_fact_entries())
ACTION_ENTRIES = tuple(
    {"id": action_id, "target_kind": target_kind, "target_required": True}
    for action_id, target_kind in ACTION_SPECS
)


@dataclass(frozen=True)
class Ontologies:
    fact_entries: tuple[Mapping[str, Any], ...]
    uncertainty_ids: tuple[str, ...]
    action_entries: tuple[Mapping[str, Any], ...]
    safe_lane_reason_ids: tuple[str, ...]

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(str(entry["id"]) for entry in self.fact_entries)

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(str(entry["id"]) for entry in self.action_entries)


class _DuplicateKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _strict_json_value(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label}: invalid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except (json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        raise ValueError(f"{label}: invalid strict JSON: {exc}") from exc


def strict_json_object(payload: bytes, label: str) -> dict[str, Any]:
    value = _strict_json_value(payload, label)
    if not isinstance(value, dict):
        raise ValueError(f"{label}: JSON value must be one object")
    return value


def _load_json_file(path: Path, label: str) -> Any:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label}: unable to read {path.name}: {exc}") from exc
    return _strict_json_value(payload, label)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _validate_fact_file(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list):
        return ["fact ontology must be an array"]
    identifiers: list[str] = []
    for index, entry in enumerate(value):
        label = f"fact ontology[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        if set(entry) != _FACT_KEYS:
            errors.append(f"{label} must contain exactly {sorted(_FACT_KEYS)}")
        fact_id = entry.get("id")
        if not isinstance(fact_id, str) or not fact_id:
            errors.append(f"{label}.id must be a non-empty string")
        else:
            identifiers.append(fact_id)
        for field in ("routing_class", "risk_projection"):
            if not isinstance(entry.get(field), str) or not entry.get(field):
                errors.append(f"{label}.{field} must be a non-empty string")
        if type(entry.get("hard_gate")) is not bool:
            errors.append(f"{label}.hard_gate must be a boolean")
        flags = entry.get("derived_flags")
        if (
            not isinstance(flags, list)
            or len(flags) != 1
            or not isinstance(flags[0], str)
            or not flags[0]
        ):
            errors.append(f"{label}.derived_flags must contain exactly one string")
    if len(identifiers) != len(set(identifiers)):
        errors.append("fact ontology IDs must be unique")
    if identifiers != sorted(identifiers):
        errors.append("fact ontology IDs must be in canonical lexicographic order")
    if value != list(FACT_ENTRIES):
        errors.append("fact ontology differs from the frozen public contract")
    return sorted(set(errors))


def _validate_string_ontology(value: Any, expected: Sequence[str], label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list):
        return [f"{label} must be an array"]
    if any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{label} entries must be non-empty strings")
    if len(value) != len(set(item for item in value if isinstance(item, str))):
        errors.append(f"{label} IDs must be unique")
    if value != list(expected):
        errors.append(f"{label} differs from the frozen public contract")
    return sorted(set(errors))


def _validate_action_file(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list):
        return ["action ontology must be an array"]
    identifiers: list[str] = []
    for index, entry in enumerate(value):
        label = f"action ontology[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        if set(entry) != _ACTION_KEYS:
            errors.append(f"{label} must contain exactly {sorted(_ACTION_KEYS)}")
        action_id = entry.get("id")
        if not isinstance(action_id, str) or not action_id:
            errors.append(f"{label}.id must be a non-empty string")
        else:
            identifiers.append(action_id)
        if not isinstance(entry.get("target_kind"), str) or not entry.get("target_kind"):
            errors.append(f"{label}.target_kind must be a non-empty string")
        if type(entry.get("target_required")) is not bool:
            errors.append(f"{label}.target_required must be a boolean")
    if len(identifiers) != len(set(identifiers)):
        errors.append("action ontology IDs must be unique")
    if value != list(ACTION_ENTRIES):
        errors.append("action ontology differs from the frozen public contract")
    return sorted(set(errors))


def load_ontologies(skill_root: Path) -> Ontologies:
    references = Path(skill_root) / "references"
    facts = _load_json_file(references / "fact-ontology.json", "fact ontology")
    uncertainties = _load_json_file(
        references / "uncertainty-ontology.json", "uncertainty ontology"
    )
    actions = _load_json_file(references / "action-ontology.json", "action ontology")
    reasons = _load_json_file(
        references / "safe-lane-reason-ontology.json", "safe-lane reason ontology"
    )
    errors = (
        _validate_fact_file(facts)
        + _validate_string_ontology(
            uncertainties, UNCERTAINTY_IDS, "uncertainty ontology"
        )
        + _validate_action_file(actions)
        + _validate_string_ontology(
            reasons, SAFE_LANE_REASON_IDS, "safe-lane reason ontology"
        )
    )
    if errors:
        raise ValueError("invalid public ontologies:\n" + "\n".join(sorted(set(errors))))
    return Ontologies(
        fact_entries=tuple(dict(entry) for entry in facts),
        uncertainty_ids=tuple(uncertainties),
        action_entries=tuple(dict(entry) for entry in actions),
        safe_lane_reason_ids=tuple(reasons),
    )


def _exact_object_fields(
    value: Any, expected_fields: set[str], label: str
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, [f"{label} must be an object"]
    errors: list[str] = []
    for field in sorted(set(value) - expected_fields):
        errors.append(f"{label}: unknown field {field}")
    for field in sorted(expected_fields - set(value)):
        errors.append(f"{label}: missing field {field}")
    return value, errors


def _validate_ordered_ids(
    value: Any, allowed: Sequence[str], label: str, *, allow_empty: bool = True
) -> list[str]:
    if not isinstance(value, list):
        return [f"{label} must be an array"]
    errors: list[str] = []
    if not allow_empty and not value:
        errors.append(f"{label} must be non-empty")
    if any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{label} entries must be non-empty strings")
        return errors
    if len(value) != len(set(value)):
        errors.append(f"{label} entries must be unique")
    allowed_set = set(allowed)
    unknown = sorted(set(value) - allowed_set)
    for item in unknown:
        errors.append(f"{label}: unknown ID {item}")
    if not unknown:
        order = {item: index for index, item in enumerate(allowed)}
        if value != sorted(value, key=order.__getitem__):
            errors.append(f"{label} must use canonical order")
    return errors


def _valid_component(value: str) -> bool:
    return (
        bool(value)
        and value not in {".", ".."}
        and _COMPONENT_RE.fullmatch(value) is not None
        and _CONTROL_RE.search(value) is None
        and "\\" not in value
    )


def _valid_repository(value: str) -> bool:
    parts = value.split("/")
    return len(parts) == 2 and all(_valid_component(part) for part in parts)


def _valid_ref(value: str, *, tags_only: bool = False) -> bool:
    prefixes = ("refs/tags/",) if tags_only else ("refs/heads/", "refs/tags/")
    prefix = next((candidate for candidate in prefixes if value.startswith(candidate)), None)
    if prefix is None:
        return False
    name = value[len(prefix) :]
    return bool(name) and all(_valid_component(part) for part in name.split("/"))


def _split_repository_target(value: str, separator: str) -> tuple[str, str] | None:
    if separator not in value:
        return None
    repository, suffix = value.split(separator, 1)
    if not _valid_repository(repository) or not suffix:
        return None
    return repository, suffix


def _valid_absolute_path(value: str) -> bool:
    if _CONTROL_RE.search(value) or "\\" in value or "//" in value:
        return False
    if value.startswith("/"):
        remainder = value[1:]
    elif re.match(r"^[A-Za-z]:/", value):
        remainder = value[3:]
    else:
        return False
    if not remainder:
        return True
    components = remainder.split("/")
    return all(
        component not in {"", ".", ".."}
        and _CONTROL_RE.search(component) is None
        and "\\" not in component
        for component in components
    )


def _valid_target(target_kind: str, value: str) -> bool:
    if not value or _CONTROL_RE.search(value) or "\\" in value:
        return False
    if target_kind == "github_account":
        prefix = "github.com/account/"
        return value.startswith(prefix) and _valid_component(value[len(prefix) :])
    if target_kind in {"repository", "marketplace_plugin"}:
        return _valid_repository(value)
    if target_kind == "numbered_resource":
        match = re.fullmatch(r"(.+)#([1-9][0-9]*)", value)
        return match is not None and _valid_repository(match.group(1))
    if target_kind == "full_ref":
        split = _split_repository_target(value, ":")
        return split is not None and _valid_ref(split[1])
    if target_kind == "pull_request_create":
        split = _split_repository_target(value, ":")
        if split is None or "<-" not in split[1]:
            return False
        base_ref, head_ref = split[1].split("<-", 1)
        return _valid_ref(base_ref) and _valid_ref(head_ref)
    if target_kind == "tag_ref":
        split = _split_repository_target(value, ":")
        return split is not None and _valid_ref(split[1], tags_only=True)
    if target_kind in {"release_tag", "environment"}:
        split = _split_repository_target(value, ":")
        return split is not None and all(
            _valid_component(part) for part in split[1].split("/")
        )
    if target_kind == "absolute_path":
        return _valid_absolute_path(value)
    return False


def _validate_action_intents(value: Any, ontologies: Ontologies, label: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{label} must be an array"]
    errors: list[str] = []
    action_order = {action_id: index for index, action_id in enumerate(ontologies.action_ids)}
    target_kinds = {
        str(entry["id"]): str(entry["target_kind"])
        for entry in ontologies.action_entries
    }
    canonical_values: list[tuple[int, int, bytes]] = []
    pairs: list[tuple[str, str | None]] = []
    can_check_order = True
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be an object")
            can_check_order = False
            continue
        allowed_fields = {"action", "target"}
        if "action" not in item:
            errors.append(f"{item_label}: missing field action")
        for field in sorted(set(item) - allowed_fields):
            errors.append(f"{item_label}: unknown field {field}")
        action = item.get("action")
        if not isinstance(action, str) or action not in action_order:
            errors.append(f"{item_label}.action is not a public action ID")
            can_check_order = False
            continue
        target = item.get("target") if "target" in item else None
        if target is not None:
            if not isinstance(target, str) or not target:
                errors.append(f"{item_label}.target must be a non-empty string")
                can_check_order = False
            elif not _valid_target(target_kinds[action], target):
                errors.append(
                    f"{item_label}.target does not match {target_kinds[action]}"
                )
        pairs.append((action, target if isinstance(target, str) else None))
        canonical_values.append(
            (
                action_order[action],
                0 if target is None else 1,
                b"" if target is None else target.encode("utf-8"),
            )
        )
    if len(pairs) != len(set(pairs)):
        errors.append(f"{label} pairs must be unique")
    if can_check_order and canonical_values != sorted(canonical_values):
        errors.append(f"{label} must use canonical action and target order")
    return errors


def _validate_semantic_envelope(
    value: dict[str, Any], ontologies: Ontologies, label: str
) -> list[str]:
    errors = _validate_ordered_ids(value.get("fact_ids"), ontologies.fact_ids, f"{label}.fact_ids")
    errors += _validate_ordered_ids(
        value.get("uncertainty_ids"),
        ontologies.uncertainty_ids,
        f"{label}.uncertainty_ids",
    )
    errors += _validate_action_intents(
        value.get("action_intents"), ontologies, f"{label}.action_intents"
    )
    return errors


def validate_controller_result(value: Any, ontologies: Ontologies) -> list[str]:
    fields = {"schema_version", "fact_ids", "uncertainty_ids", "action_intents"}
    obj, errors = _exact_object_fields(value, fields, "controller result")
    if obj is None:
        return errors
    if obj.get("schema_version") != "cmr-controller-result-v1":
        errors.append("controller result.schema_version must equal cmr-controller-result-v1")
    errors += _validate_semantic_envelope(obj, ontologies, "controller result")
    return sorted(set(errors))


def validate_preflight_result(value: Any, ontologies: Ontologies) -> list[str]:
    if not isinstance(value, dict):
        return ["preflight result must be an object"]
    outcome = value.get("outcome")
    fields_by_outcome = {
        "accept": {"schema_version", "outcome"},
        "replace": {
            "schema_version",
            "outcome",
            "fact_ids",
            "uncertainty_ids",
            "action_intents",
        },
        "block": {"schema_version", "outcome", "blocker_ids"},
    }
    if outcome not in fields_by_outcome:
        return ["preflight result.outcome must be accept, replace, or block"]
    obj, errors = _exact_object_fields(
        value, fields_by_outcome[outcome], "preflight result"
    )
    assert obj is not None
    if obj.get("schema_version") != "cmr-preflight-result-v1":
        errors.append("preflight result.schema_version must equal cmr-preflight-result-v1")
    if outcome == "replace":
        errors += _validate_semantic_envelope(obj, ontologies, "preflight result")
    elif outcome == "block":
        errors += _validate_ordered_ids(
            obj.get("blocker_ids"),
            PREFLIGHT_BLOCKER_IDS,
            "preflight result.blocker_ids",
            allow_empty=False,
        )
    return sorted(set(errors))


def validate_planner_result(
    value: Any, task_ids: Sequence[str], ontologies: Ontologies
) -> list[str]:
    obj, errors = _exact_object_fields(
        value, {"schema_version", "safe_lanes"}, "planner result"
    )
    if obj is None:
        return errors
    if obj.get("schema_version") != "cmr-planner-result-v1":
        errors.append("planner result.schema_version must equal cmr-planner-result-v1")
    if isinstance(task_ids, (str, bytes)) or any(
        not isinstance(task_id, str) or not task_id for task_id in task_ids
    ):
        errors.append("planner task IDs must be non-empty strings")
        task_order: dict[str, int] = {}
    else:
        task_order = {task_id: index for index, task_id in enumerate(task_ids)}
        if len(task_order) != len(task_ids):
            errors.append("planner task IDs must be unique")
    safe_lanes = obj.get("safe_lanes")
    if not isinstance(safe_lanes, list):
        errors.append("planner result.safe_lanes must be an array")
        return sorted(set(errors))
    seen: list[str] = []
    for index, lane in enumerate(safe_lanes):
        label = f"planner result.safe_lanes[{index}]"
        lane_obj, lane_errors = _exact_object_fields(
            lane, {"task_id", "reason_ids"}, label
        )
        errors += lane_errors
        if lane_obj is None:
            continue
        task_id = lane_obj.get("task_id")
        if not isinstance(task_id, str) or task_id not in task_order:
            errors.append(f"{label}.task_id is not in the frozen plan")
        else:
            seen.append(task_id)
        errors += _validate_ordered_ids(
            lane_obj.get("reason_ids"),
            ontologies.safe_lane_reason_ids,
            f"{label}.reason_ids",
            allow_empty=False,
        )
    if len(seen) != len(set(seen)):
        errors.append("planner safe-lane task IDs must be unique")
    if seen and seen != sorted(seen, key=task_order.__getitem__):
        errors.append("planner safe lanes must follow frozen-plan task order")
    return sorted(set(errors))


def _action_intent_schema(ontologies: Ontologies) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": list(ontologies.action_ids)},
            "target": {"type": "string", "minLength": 1},
        },
    }


def _semantic_properties(ontologies: Ontologies) -> dict[str, Any]:
    return {
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
        "action_intents": {
            "type": "array",
            "items": _action_intent_schema(ontologies),
            "uniqueItems": True,
        },
    }


def _controller_schema(ontologies: Ontologies) -> dict[str, Any]:
    properties = {
        "schema_version": {"const": "cmr-controller-result-v1"},
        **_semantic_properties(ontologies),
    }
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "cmr-controller-result-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "fact_ids", "uncertainty_ids", "action_intents"],
        "properties": properties,
    }


def _preflight_schema(ontologies: Ontologies) -> dict[str, Any]:
    semantic = _semantic_properties(ontologies)
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "cmr-preflight-result-v1.schema.json",
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "outcome"],
                "properties": {
                    "schema_version": {"const": "cmr-preflight-result-v1"},
                    "outcome": {"const": "accept"},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "outcome",
                    "fact_ids",
                    "uncertainty_ids",
                    "action_intents",
                ],
                "properties": {
                    "schema_version": {"const": "cmr-preflight-result-v1"},
                    "outcome": {"const": "replace"},
                    **semantic,
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "outcome", "blocker_ids"],
                "properties": {
                    "schema_version": {"const": "cmr-preflight-result-v1"},
                    "outcome": {"const": "block"},
                    "blocker_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "enum": list(PREFLIGHT_BLOCKER_IDS)},
                        "uniqueItems": True,
                    },
                },
            },
        ],
    }


def _planner_schema(ontologies: Ontologies) -> dict[str, Any]:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "cmr-planner-result-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "safe_lanes"],
        "properties": {
            "schema_version": {"const": "cmr-planner-result-v1"},
            "safe_lanes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_id", "reason_ids"],
                    "properties": {
                        "task_id": {"type": "string", "minLength": 1},
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
                },
                "uniqueItems": True,
            },
        },
    }


def _occurrence_schema() -> dict[str, Any]:
    hash_value = {"type": "string", "pattern": LOWER_SHA256_PATTERN}
    nullable_hash = {"oneOf": [hash_value, {"type": "null"}]}
    usage_ref = {"$ref": "#/$defs/UsageEvidence"}
    cwd_ref = {
        "oneOf": [
            {"$ref": "#/$defs/EphemeralCwd"},
            {"$ref": "#/$defs/PlanWorktreeCwd"},
            {"type": "null"},
        ]
    }
    required = [
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
    ]
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "cmr-occurrence-audit-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "schema_version": {"const": "cmr-occurrence-audit-v1"},
            "phase": {
                "type": "string",
                "enum": [
                    "planning",
                    "controller",
                    "preflight",
                    "worker",
                    "task_review",
                    "re_review",
                    "final_review",
                ],
            },
            "scope_kind": {"type": "string", "enum": ["plan", "task", "release"]},
            "scope_id": {"type": "string", "minLength": 1},
            "round_index": {
                "oneOf": [
                    {"type": "integer", "minimum": 0, "maximum": 5},
                    {"type": "null"},
                ]
            },
            "task_id": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "null"},
                ]
            },
            "response_policy": {
                "type": "string",
                "enum": ["closed_json", "sdd_report"],
            },
            "model": {"type": "string", "enum": ["gpt-5.6-luna", "gpt-5.6-sol"]},
            "reasoning_effort": {"type": "string", "enum": ["xhigh", "max"]},
            "fork_turns": {"const": "none"},
            "prompt_sha256": hash_value,
            "thread_policy": {"type": "string", "enum": ["fresh", "resume"]},
            "thread_id": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "null"},
                ]
            },
            "cwd": cwd_ref,
            "write_policy": {
                "type": "string",
                "enum": [
                    "control_plane_no_write",
                    "worktree_write",
                    "source_read_only",
                ],
            },
            "exit_code": {"type": "integer"},
            "external_writes": {"type": "boolean"},
            "transcript_sha256": nullable_hash,
            "raw_sha256": hash_value,
            "usage": {"oneOf": [usage_ref, {"type": "null"}]},
            "tool_event_count": {"type": "integer", "minimum": 0},
            "terminal_report_sha256": nullable_hash,
            "ignored_empty_agent_messages": {"type": "integer", "minimum": 0},
            "accepted": {"type": "boolean"},
            "failure_class": {
                "oneOf": [
                    {"type": "string", "enum": ["operational", "instrument"]},
                    {"type": "null"},
                ]
            },
            "errors": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            },
        },
        "$defs": {
            "UsageEvidence": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                ],
                "properties": {
                    "input_tokens": {"type": "integer", "minimum": 0},
                    "cached_input_tokens": {"type": "integer", "minimum": 0},
                    "cache_write_input_tokens": {"type": "integer", "minimum": 0},
                    "output_tokens": {"type": "integer", "minimum": 0},
                    "reasoning_output_tokens": {"type": "integer", "minimum": 0},
                },
            },
            "EphemeralCwd": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "path", "fresh", "destroyed", "read_only"],
                "properties": {
                    "kind": {"const": "ephemeral"},
                    "path": {"type": "string", "pattern": "^(?:/|[A-Za-z]:/).+"},
                    "fresh": {"const": True},
                    "destroyed": {"const": True},
                    "read_only": {"const": True},
                },
            },
            "PlanWorktreeCwd": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "kind",
                    "path",
                    "workspace_id",
                    "base_head",
                    "head",
                    "read_only",
                ],
                "properties": {
                    "kind": {"const": "plan_worktree"},
                    "path": {"type": "string", "pattern": "^(?:/|[A-Za-z]:/).+"},
                    "workspace_id": {"type": "string", "minLength": 1},
                    "base_head": {"type": "string", "pattern": GIT_HEAD_PATTERN},
                    "head": {"type": "string", "pattern": GIT_HEAD_PATTERN},
                    "read_only": {"type": "boolean"},
                },
            },
        },
    }


def _accounting_schema() -> dict[str, Any]:
    phases = [
        "planning",
        "controller",
        "preflight",
        "worker",
        "task_review",
        "re_review",
        "final_review",
    ]
    selectors = ["run", "skip", "block"]
    count_properties = {
        key: {"type": "integer", "minimum": 0}
        for key in selectors
    }
    phase_count_properties = {
        key: {"type": "integer", "minimum": 0}
        for key in phases
    }
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "cmr-accounting-summary-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "task_count",
            "selector_counts",
            "phase_counts",
            "expected_phase_counts",
            "call_cardinality",
            "total_calls",
            "transport_retry_count",
            "sdd_fix_rounds",
            "canonical_call_shape",
            "usage",
            "operational_failures",
            "semantic_failures",
            "instrument_failures",
        ],
        "properties": {
            "schema_version": {"const": "cmr-accounting-summary-v1"},
            "task_count": {"type": "integer", "minimum": 0},
            "selector_counts": {
                "type": "object",
                "additionalProperties": False,
                "required": selectors,
                "properties": count_properties,
            },
            "phase_counts": {
                "type": "object",
                "additionalProperties": False,
                "required": phases,
                "properties": phase_count_properties,
            },
            "expected_phase_counts": {
                "type": "object",
                "additionalProperties": False,
                "required": phases,
                "properties": phase_count_properties,
            },
            "call_cardinality": {
                "type": "array",
                "items": {"$ref": "#/$defs/Cardinality"},
                "uniqueItems": True,
            },
            "total_calls": {"type": "integer", "minimum": 0},
            "transport_retry_count": {"type": "integer", "minimum": 0},
            "sdd_fix_rounds": {"type": "integer", "minimum": 0},
            "canonical_call_shape": {"type": "boolean"},
            "usage": {"$ref": "#/$defs/UsageEvidence"},
            "operational_failures": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            },
            "semantic_failures": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            },
            "instrument_failures": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            },
        },
        "$defs": {
            "Cardinality": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "phase",
                    "scope_kind",
                    "scope_id",
                    "round_index",
                    "attempt_count",
                ],
                "properties": {
                    "phase": {"type": "string", "enum": phases},
                    "scope_kind": {
                        "type": "string",
                        "enum": ["plan", "task", "release"],
                    },
                    "scope_id": {"type": "string", "minLength": 1},
                    "round_index": {
                        "oneOf": [
                            {"type": "integer", "minimum": 0, "maximum": 5},
                            {"type": "null"},
                        ]
                    },
                    "attempt_count": {"type": "integer", "minimum": 1},
                },
            },
            "UsageEvidence": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                ],
                "properties": {
                    field_name: {"type": "integer", "minimum": 0}
                    for field_name in (
                        "input_tokens",
                        "cached_input_tokens",
                        "cache_write_input_tokens",
                        "output_tokens",
                        "reasoning_output_tokens",
                    )
                },
            },
        },
    }

def expected_published_schemas(ontologies: Ontologies) -> dict[str, dict[str, Any]]:
    schemas = {
        "cmr-controller-result-v1.schema.json": _controller_schema(ontologies),
        "cmr-preflight-result-v1.schema.json": _preflight_schema(ontologies),
        "cmr-planner-result-v1.schema.json": _planner_schema(ontologies),
        "cmr-occurrence-audit-v1.schema.json": _occurrence_schema(),
        "cmr-accounting-summary-v1.schema.json": _accounting_schema(),
    }
    # Task-specific schema producers extend parity without moving their runtime
    # validation logic into the Task 1 semantic-wire module.
    from cmr_compiler import expected_compiler_schemas

    schemas.update(expected_compiler_schemas(ontologies))
    return schemas


def validate_published_contracts(
    skill_root: Path, ontologies: Ontologies
) -> list[str]:
    errors: list[str] = []
    schemas_root = Path(skill_root) / "references" / "schemas"
    for filename, expected in expected_published_schemas(ontologies).items():
        path = schemas_root / filename
        try:
            value = _load_json_file(path, filename)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(value, dict):
            errors.append(f"schema drift: {filename} must contain one object")
            continue
        try:
            published_bytes = _canonical_json_bytes(value)
        except ValueError:
            errors.append(
                f"schema drift: {filename} contains a non-finite JSON number"
            )
            continue
        if published_bytes != _canonical_json_bytes(expected):
            errors.append(f"schema drift: {filename} differs from runtime contract")
    return sorted(set(errors))


__all__ = [
    "Ontologies",
    "load_ontologies",
    "strict_json_object",
    "validate_controller_result",
    "validate_planner_result",
    "validate_preflight_result",
    "validate_published_contracts",
]
