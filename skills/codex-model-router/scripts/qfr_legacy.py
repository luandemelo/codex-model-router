"""Documentary validation for one historical Quality-First Routing record.

This module deliberately has no dispatch, safe-lane, freezer, or
external-action execution code.  A successful result is a human-readable
inspection notice, not an authorization to spawn work.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any


LEGACY_SUCCESS_MESSAGE = (
    "legacy QFR record is valid for inspection only; it cannot authorize dispatch."
)
ALLOWED_MODELS = frozenset({"gpt-5.6-luna", "gpt-5.6-sol"})
ALLOWED_EFFORTS = frozenset({"xhigh", "max"})
ALLOWED_EXTERNAL_ACTIONS = frozenset(
    {
        "github",
        "issue_update",
        "issue_comment",
        "push",
        "pr_create",
        "pr_update",
        "merge",
        "deploy",
        "install_skill",
    }
)
EXTERNAL_ACTION_ALIASES = {
    "github_issue": "issue_update",
    "issue": "issue_update",
    "comment": "issue_comment",
    "pr": "pr_update",
    "pull_request": "pr_update",
    "deployment": "deploy",
    "skill_installation": "install_skill",
}
EXTERNAL_ACTION_LIFECYCLE = {
    "blocked": {"authorized": False, "preview": False, "readback": False},
    "ready": {"authorized": True, "preview": True, "readback": False},
    "completed": {"authorized": True, "preview": True, "readback": True},
}
EXTERNAL_ACTION_STATES = frozenset(EXTERNAL_ACTION_LIFECYCLE)
FAILURE_OUTCOMES = frozenset({"failed", "failure", "passed", "success"})
FAILED_OUTCOMES = frozenset({"failed", "failure"})
RECURRENCE_BOOL_FIELDS = (
    "same_defect",
    "defect_declared_resolved",
    "recurring_failure",
    "defect_reappeared_after_declared_resolved",
    "regression_after_declared_resolved",
    "reappeared_after_resolution",
    "defect_reappeared_after_resolution",
    "regression_after_resolved",
)
SEMANTIC_PREFLIGHT_STATES = frozenset({"skipped", "accepted", "amended", "blocked"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Exact property names from the tracked documentary decision-record schema.
# The historical schema stays byte-compatible (and is intentionally labeled
# documentary), while this direct validator is the fail-closed authority.
LEGACY_TOP_LEVEL_FIELDS = frozenset(
    {
        "architecture_approved",
        "authorizations",
        "context_files",
        "context_inherited",
        "context_mode",
        "correction_attempts",
        "critical_signal",
        "critical_signal_detected",
        "critical_signals",
        "defect_declared_resolved",
        "defect_reappeared_after_declared_resolved",
        "defect_reappeared_after_resolution",
        "downgrade_after_critical_signal",
        "downgrade_attempted",
        "effort",
        "execution_engine",
        "execution_engines",
        "external_action_authorizations",
        "external_actions",
        "failed_attempts",
        "failure_history",
        "final_review",
        "fork_turns",
        "github_write_authorized",
        "hard_gates",
        "history_inherited",
        "inherited_history",
        "judgment_required",
        "justification",
        "low_blast_radius",
        "model",
        "previous_model",
        "previous_reasoning_effort",
        "previous_route",
        "prior_model",
        "prior_reasoning_effort",
        "prior_route",
        "reasoning_effort",
        "reappeared_after_resolution",
        "recurring_failure",
        "regression_after_declared_resolved",
        "regression_after_resolved",
        "review",
        "review_effort",
        "review_model",
        "review_scope",
        "risk_level",
        "risk_signals",
        "role",
        "same_defect",
        "selected_model",
        "semantic_preflight",
        "task_id",
    }
)

HARD_GATE_SIGNALS = frozenset(
    {
        "architecture",
        "architecture_or_canonical_planning",
        "canonical_design",
        "canonical_planning",
        "complete_planning",
        "complete_project_planning",
        "planning_complete",
        "schema_migration",
        "schema_migration_or_backfill",
        "backfill",
        "rollback",
        "rollback_or_material_data_transformation",
        "material_data_transformation",
        "data_transformation",
        "concurrency",
        "race_condition",
        "locks",
        "idempotency",
        "distributed_consistency",
        "consistency_distributed",
        "authentication",
        "authentication_or_authorization",
        "authorization",
        "cryptography",
        "encryption",
        "secrets",
        "trust_boundary",
        "billing",
        "payment",
        "price",
        "invoice",
        "ledger_financial",
        "entitlement",
        "pii",
        "regulated_data",
        "critical_integrity",
        "integrity_critical",
        "destruction",
        "irreversibility_risk",
        "irreversible",
        "effects_difficult_to_reverse",
        "external_effect_difficult_to_reverse",
        "multiple_plausible_tradeoffs",
        "public_api_structural_change",
        "structural_api_change",
        "failure_survived_two_complete_attempts",
        "recurring_failure",
        "regression_after_declared_resolved",
        "defect_reappeared_after_declared_resolved",
        "reopened_defect",
        "finding_critical",
        "finding_important_repeated",
        "structural_conflict",
        "conflict_structural_plan",
    }
)
INTEGRATION_SIGNALS = frozenset(
    {
        "multi_module_integration",
        "cross_module_integration",
        "contract_coordination",
        "integration_tests",
    }
)
ELEVATED_SIGNALS = frozenset(
    {
        "availability_or_integrity_impact",
        "critical_risk",
        "data_integrity",
        "elevated",
        "elevated_risk",
        "high_risk",
        "incident_risk",
        "security_risk",
        "untrusted_input",
    }
)
JUDGMENT_SIGNALS = frozenset(
    {"audit", "judgment_required", "judgment_high", "reconciliation"}
)
COMPLEX_LOCAL_SIGNALS = frozenset(
    {"complex_local", "complex_local_execution", "complex_local_low_risk"}
)
LOW_BLAST_SIGNALS = frozenset({"low_blast_radius"})


def _normalise(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _is_non_empty_string(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and not isinstance(value, bool)


def _normalise_defect_id(value: Any) -> tuple[str, Any] | None:
    if _is_non_empty_string(value):
        return "string", _normalise(value)
    if _is_number(value):
        return "number", value
    return None


def _add_required_string(
    record: Mapping[str, Any], field: str, violations: set[str]
) -> Any:
    if field not in record:
        violations.add(f"{field} is required")
        return None
    value = record[field]
    if not _is_non_empty_string(value):
        violations.add(f"{field} must be a non-empty string")
    return value


def _add_string_list(
    record: Mapping[str, Any], field: str, violations: set[str]
) -> list[str]:
    if field not in record:
        violations.add(f"{field} is required")
        return []
    value = record[field]
    if type(value) is not list:
        violations.add(f"{field} must be a list of non-empty strings")
        return []
    result: list[str] = []
    for item in value:
        if not _is_non_empty_string(item):
            violations.add(f"{field} items must be non-empty strings")
        else:
            result.append(_normalise(item))
    return result


def _validate_route_mapping(value: Any, field: str, violations: set[str]) -> None:
    if not isinstance(value, Mapping):
        violations.add(f"{field} must be an object")
        return
    allowed = {"selected_model", "model", "reasoning_effort", "effort"}
    if set(value) - allowed:
        violations.add(f"{field} contains an unsupported field")
    models = [value[key] for key in ("selected_model", "model") if key in value]
    efforts = [
        value[key] for key in ("reasoning_effort", "effort") if key in value
    ]
    for label, values, choices in (
        ("model", models, ALLOWED_MODELS),
        ("effort", efforts, ALLOWED_EFFORTS),
    ):
        if not values:
            violations.add(f"{field} must declare a {label}")
        for item in values:
            if not _is_non_empty_string(item):
                violations.add(f"{field}.{label} must be a non-empty string")
            elif item not in choices:
                violations.add(f"{field}.{label} is unsupported")
        if len(values) > 1 and any(item != values[0] for item in values[1:]):
            violations.add(f"{field}.{label} aliases conflict")


def _validate_previous_routes(
    record: Mapping[str, Any], violations: set[str]
) -> None:
    for field in ("previous_route", "prior_route"):
        if field in record:
            _validate_route_mapping(record[field], field, violations)
    for prefix in ("previous", "prior"):
        model_field = f"{prefix}_model"
        effort_field = f"{prefix}_reasoning_effort"
        has_model = model_field in record
        has_effort = effort_field in record
        if not has_model and not has_effort:
            continue
        if not has_model or not has_effort:
            violations.add(
                f"{prefix} route must declare model and reasoning_effort"
            )
        if has_model:
            model = record[model_field]
            if not _is_non_empty_string(model):
                violations.add(f"{model_field} must be a non-empty string")
            elif model not in ALLOWED_MODELS:
                violations.add(f"{model_field} is unsupported")
        if has_effort:
            effort = record[effort_field]
            if not _is_non_empty_string(effort):
                violations.add(f"{effort_field} must be a non-empty string")
            elif effort not in ALLOWED_EFFORTS:
                violations.add(f"{effort_field} is unsupported")


def _validate_spawn_aliases(
    record: Mapping[str, Any], violations: set[str]
) -> None:
    for alias, canonical in (("model", "selected_model"), ("effort", "reasoning_effort")):
        if alias in record and canonical in record and record[alias] != record[canonical]:
            violations.add(f"{alias} conflicts with {canonical}")
    if "review" not in record:
        return
    review = record["review"]
    if not isinstance(review, Mapping):
        violations.add("review must be an object")
        return
    allowed = {
        "model",
        "selected_model",
        "review_model",
        "effort",
        "reasoning_effort",
        "review_effort",
    }
    if set(review) - allowed:
        violations.add("review contains an unsupported field")
    model_values = [
        review[key]
        for key in ("model", "selected_model", "review_model")
        if key in review
    ]
    effort_values = [
        review[key]
        for key in ("effort", "reasoning_effort", "review_effort")
        if key in review
    ]
    for label, values, choices in (
        ("model", model_values, ALLOWED_MODELS),
        ("effort", effort_values, ALLOWED_EFFORTS),
    ):
        for value in values:
            if not _is_non_empty_string(value):
                violations.add(f"review.{label} must be a non-empty string")
            elif value not in choices:
                violations.add(f"review.{label} is unsupported")
        if len(values) > 1 and any(value != values[0] for value in values[1:]):
            violations.add(f"review.{label} aliases conflict")
        canonical = "review_model" if label == "model" else "review_effort"
        if canonical in record and any(value != record[canonical] for value in values):
            violations.add(f"review.{canonical} aliases conflict with {canonical}")


def _validate_context(record: Mapping[str, Any], violations: set[str]) -> None:
    context_files = record.get("context_files")
    if type(context_files) is not list or not context_files:
        violations.add("context_files must contain a fresh brief context")
    elif any(not _is_non_empty_string(path) for path in context_files):
        violations.add("context_files must contain non-empty file paths")
    for field in ("context_inherited", "inherited_history", "history_inherited"):
        if field in record:
            if type(record[field]) is not bool:
                violations.add(f"{field} must be boolean")
            elif record[field]:
                violations.add("context must be fresh; inherited history is forbidden")
    if "context_mode" in record and record["context_mode"] not in {"fresh", "brief"}:
        violations.add("context_mode must be fresh or brief")
    if record.get("fork_turns") != "none":
        violations.add("fork_turns is required and must be none")


def _validate_external_actions(record: Mapping[str, Any], violations: set[str]) -> None:
    for field in ("authorizations", "external_action_authorizations"):
        if field in record:
            value = record[field]
            if not isinstance(value, Mapping) or any(
                type(item) is not bool for item in value.values()
            ):
                violations.add(f"{field} must map actions to booleans")
    if "github_write_authorized" in record and type(
        record["github_write_authorized"]
    ) is not bool:
        violations.add("github_write_authorized must be boolean")
    if "external_actions" not in record:
        return
    actions = record["external_actions"]
    if type(actions) is not list:
        violations.add("external_actions must be a list of action objects")
        return
    allowed_fields = {"action", "state", "authorized", "target", "preview", "readback"}
    for item in actions:
        if not isinstance(item, Mapping):
            violations.add("external_actions items must be objects")
            continue
        if set(item) - allowed_fields:
            violations.add("external_actions item contains an unsupported field")
        action = item.get("action")
        if not _is_non_empty_string(action):
            action_name = "<missing>"
            violations.add("external_actions items require a non-empty action")
        else:
            action_name = EXTERNAL_ACTION_ALIASES.get(action, action)
            if action_name not in ALLOWED_EXTERNAL_ACTIONS:
                violations.add("external_actions contains an unsupported action")
        state = item.get("state")
        if state not in EXTERNAL_ACTION_STATES:
            violations.add(
                f"external action {action_name} state must be blocked, ready, or completed"
            )
            continue
        for field in ("authorized", "preview", "readback"):
            if field not in item:
                violations.add(f"external action {action_name} requires {field}")
            elif type(item[field]) is not bool:
                violations.add(f"external_actions.{field} must be boolean")
        if "target" in item and not _is_non_empty_string(item["target"]):
            violations.add("external_actions.target must be a non-empty string")
        for field, expected in EXTERNAL_ACTION_LIFECYCLE[state].items():
            if item.get(field) is not expected:
                violations.add(
                    f"{state} action {action_name} requires {field}={str(expected).lower()}"
                )
        if state in {"ready", "completed"} and not _is_non_empty_string(
            item.get("target")
        ):
            violations.add(f"{state} action {action_name} requires a non-empty target")


def _validate_semantic_preflight_pointer(
    record: Mapping[str, Any], violations: set[str]
) -> None:
    if "semantic_preflight" not in record:
        return
    pointer = record["semantic_preflight"]
    if not isinstance(pointer, Mapping):
        violations.add("semantic_preflight must be an object")
        return
    if set(pointer) != {"state", "path", "sha256"}:
        violations.add("semantic_preflight must contain exactly state, path, and sha256")
    if pointer.get("state") not in SEMANTIC_PREFLIGHT_STATES:
        violations.add("semantic_preflight.state is invalid")
    path_value = pointer.get("path")
    if not _is_non_empty_string(path_value):
        violations.add("semantic_preflight.path must be a non-empty relative path")
    else:
        path = PurePosixPath(path_value)
        if path.is_absolute() or ".." in path.parts or "\\" in path_value:
            violations.add("semantic_preflight.path must be a safe relative path")
    digest = pointer.get("sha256")
    if type(digest) is not str or SHA256_RE.fullmatch(digest) is None:
        violations.add("semantic_preflight.sha256 must be lowercase SHA-256")


def _validate_execution_engines(record: Mapping[str, Any], violations: set[str]) -> None:
    if "execution_engines" not in record:
        return
    engines = record["execution_engines"]
    if type(engines) is not list or not engines or any(not _is_non_empty_string(item) for item in engines):
        violations.add("execution_engines must be a non-empty list of strings")
        return
    normalized = [_normalise(item) for item in engines]
    if len(normalized) != len(set(normalized)):
        violations.add("execution_engines must contain unique engines")
    if any(item != "superpowers_sdd" for item in normalized):
        violations.add("execution_engines contains an unsupported engine")


def _validate_failure_attempt(
    attempt: Any, field: str, index: int, violations: set[str]
) -> tuple[tuple[str, Any] | None, bool] | None:
    if not isinstance(attempt, Mapping):
        violations.add(f"{field} items must be objects")
        return None
    if set(attempt) - {"defect_id", "complete", "outcome"}:
        violations.add(f"{field} item contains an unsupported field")
    defect_id = _normalise_defect_id(attempt.get("defect_id"))
    if defect_id is None:
        violations.add(f"{field}[{index}].defect_id must be a non-empty string or number")
    if type(attempt.get("complete")) is not bool:
        violations.add(f"{field}[{index}].complete must be boolean")
    outcome = attempt.get("outcome")
    if type(outcome) is not str or outcome not in FAILURE_OUTCOMES:
        violations.add(f"{field}[{index}].outcome is invalid")
    failed = attempt.get("complete") is True and outcome in FAILED_OUTCOMES
    return defect_id, failed


def _validate_recurrence(record: Mapping[str, Any], violations: set[str]) -> bool:
    for field in RECURRENCE_BOOL_FIELDS:
        if field in record and type(record[field]) is not bool:
            violations.add(f"{field} must be boolean")

    failed_attempt_count: int | None = None
    if "failed_attempts" in record:
        if type(record["failed_attempts"]) is not int or record["failed_attempts"] < 0:
            violations.add("failed_attempts must be a non-negative integer")
        else:
            failed_attempt_count = record["failed_attempts"]

    correction_attempt_count: int | None = None
    histories: list[tuple[str, list[tuple[tuple[str, Any] | None, bool]]]] = []
    for field in ("failure_history", "correction_attempts"):
        if field not in record:
            continue
        value = record[field]
        if field == "correction_attempts" and type(value) is int:
            if value < 0:
                violations.add(
                    "correction_attempts must be a non-negative integer or list"
                )
            else:
                correction_attempt_count = value
            continue
        if type(value) is not list:
            violations.add(
                "correction_attempts must be a non-negative integer or list"
                if field == "correction_attempts"
                else "failure_history must be a list of failure attempts"
            )
            continue
        parsed: list[tuple[tuple[str, Any] | None, bool]] = []
        for index, attempt in enumerate(value):
            parsed_attempt = _validate_failure_attempt(attempt, field, index, violations)
            if parsed_attempt is not None:
                parsed.append(parsed_attempt)
        histories.append((field, parsed))
    if len(histories) == 2 and histories[0][1] != histories[1][1]:
        violations.add("failure_history and correction_attempts conflict")
    parsed_history = histories[0][1] if histories else []
    failed_by_defect: dict[tuple[str, Any], int] = {}
    for defect_id, failed in parsed_history:
        if defect_id is not None and failed:
            failed_by_defect[defect_id] = failed_by_defect.get(defect_id, 0) + 1
    effective_failed = max(failed_by_defect.values(), default=0)
    if not histories and correction_attempt_count is not None:
        effective_failed = correction_attempt_count
    elif not histories and failed_attempt_count is not None:
        effective_failed = failed_attempt_count
    if failed_attempt_count is not None and correction_attempt_count is not None:
        if failed_attempt_count != correction_attempt_count:
            violations.add("correction_attempts conflicts with failed_attempts")
    if correction_attempt_count is not None and histories:
        observed = sum(1 for _, failed in parsed_history if failed)
        if correction_attempt_count != observed:
            violations.add("correction_attempts conflicts with failure_history")
    if failed_attempt_count is not None and histories:
        observed = sum(1 for _, failed in parsed_history if failed)
        if failed_attempt_count != observed:
            violations.add("failed_attempts conflicts with failure_history")
    reappeared = any(
        record.get(field) is True
        for field in (
            "defect_reappeared_after_declared_resolved",
            "regression_after_declared_resolved",
            "reappeared_after_resolution",
            "defect_reappeared_after_resolution",
            "regression_after_resolved",
        )
    )
    if record.get("same_defect") is False and effective_failed >= 2:
        violations.add("same_defect conflicts with recurring failure evidence")
    if record.get("recurring_failure") is True and effective_failed < 2 and not reappeared:
        violations.add("recurring_failure lacks two complete failed attempts")
    if record.get("recurring_failure") is False and (effective_failed >= 2 or reappeared):
        violations.add("recurring_failure contradicts failure evidence")
    return reappeared or effective_failed >= 2 or record.get("recurring_failure") is True


def _semantic_facts(record: Mapping[str, Any], recurrence: bool) -> dict[str, Any]:
    def items(field: str) -> list[str]:
        value = record.get(field)
        return [_normalise(item) for item in value if _is_non_empty_string(item)] if type(value) is list else []

    risk_signals = items("risk_signals")
    hard_gates = items("hard_gates")
    semantic = tuple(sorted(set(risk_signals + hard_gates)))
    role = _normalise(record.get("role"))
    signals = tuple(sorted(set(semantic + ((role,) if role else ()))))
    risk_level = _normalise(record.get("risk_level"))
    hard = risk_level in {"hard_gate", "critical_hard_gate"} or any(
        signal in HARD_GATE_SIGNALS for signal in signals
    )
    integration = record.get("integration") is True or any(
        signal in INTEGRATION_SIGNALS for signal in signals
    )
    elevated = risk_level in {"elevated", "high", "critical", "risk_elevated"} or any(
        signal in ELEVATED_SIGNALS for signal in signals
    )
    low_blast = (
        record.get("low_blast_radius") is True
        if "low_blast_radius" in record
        else any(signal in LOW_BLAST_SIGNALS for signal in signals)
    )
    judgment = (
        record.get("judgment_required") is True
        if "judgment_required" in record
        else any(signal in JUDGMENT_SIGNALS for signal in signals)
        or role in {"audit", "reconciliation", "judgment"}
    )
    complex_local = any(signal in COMPLEX_LOCAL_SIGNALS for signal in signals)
    architecture_approved = (
        record.get("architecture_approved") is True
        if "architecture_approved" in record
        else "architecture_approved" in semantic
    )
    risk_low = risk_level == "low"
    luna_max = risk_low and low_blast and (
        judgment or (complex_local and architecture_approved)
    )
    final_review = (
        record.get("final_review") is True
        or record.get("review_scope") == "branch"
        or role in {"final_review", "branch_review", "review_final"}
    )
    return {
        "hard": hard,
        "integration": integration,
        "elevated": elevated,
        "recurrence": recurrence,
        "luna_max": luna_max,
        "final_review": final_review,
    }


def _route_rank(model: Any, effort: Any) -> int | None:
    return {
        ("gpt-5.6-luna", "xhigh"): 1,
        ("gpt-5.6-luna", "max"): 2,
        ("gpt-5.6-sol", "xhigh"): 3,
        ("gpt-5.6-sol", "max"): 4,
    }.get((model, effort))


def _downgrade_detected(record: Mapping[str, Any], current_rank: int | None) -> bool:
    if record.get("downgrade_attempted") is True or record.get(
        "downgrade_after_critical_signal"
    ) is True:
        return True
    previous = record.get("previous_route", record.get("prior_route"))
    if isinstance(previous, Mapping):
        previous_model = previous.get("selected_model", previous.get("model"))
        previous_effort = previous.get("reasoning_effort", previous.get("effort"))
    else:
        previous_model = record.get("previous_model", record.get("prior_model"))
        previous_effort = record.get(
            "previous_reasoning_effort", record.get("prior_reasoning_effort")
        )
    previous_rank = _route_rank(previous_model, previous_effort)
    return previous_rank is not None and current_rank is not None and current_rank < previous_rank


def validate_legacy_decision(record: Mapping[str, Any]) -> list[str]:
    """Validate one historical QFR decision for inspection, never dispatch."""

    if not isinstance(record, Mapping):
        return ["record must be a JSON object"]
    violations: set[str] = set()
    for field in sorted(set(record) - LEGACY_TOP_LEVEL_FIELDS):
        violations.add(f"unsupported top-level field: {field}")
    _add_required_string(record, "task_id", violations)
    _add_required_string(record, "role", violations)
    _add_required_string(record, "justification", violations)
    risk_signals = _add_string_list(record, "risk_signals", violations)
    hard_gates = _add_string_list(record, "hard_gates", violations)
    if "risk_level" in record and not _is_non_empty_string(record["risk_level"]):
        violations.add("risk_level must be a non-empty string")
    selected_model = _add_required_string(record, "selected_model", violations)
    effort = _add_required_string(record, "reasoning_effort", violations)
    review_model = _add_required_string(record, "review_model", violations)
    review_effort = _add_required_string(record, "review_effort", violations)
    for label, value, choices in (
        ("selected_model", selected_model, ALLOWED_MODELS),
        ("review_model", review_model, ALLOWED_MODELS),
        ("reasoning_effort", effort, ALLOWED_EFFORTS),
        ("review_effort", review_effort, ALLOWED_EFFORTS),
    ):
        if isinstance(value, str) and value not in choices:
            violations.add(f"{label} is unsupported")
    if "fork_turns" not in record or record.get("fork_turns") != "none":
        violations.add("fork_turns is required and must be none")
    if record.get("execution_engine") != "superpowers-sdd":
        violations.add("execution_engine must be the exact string superpowers-sdd")
    _validate_spawn_aliases(record, violations)
    _validate_previous_routes(record, violations)
    _validate_context(record, violations)
    _validate_execution_engines(record, violations)
    _validate_external_actions(record, violations)
    _validate_semantic_preflight_pointer(record, violations)
    for field in (
        "final_review",
        "integration",
        "architecture_approved",
        "low_blast_radius",
        "judgment_required",
    ):
        if field in record and type(record[field]) is not bool:
            violations.add(f"{field} must be boolean")
    if "review_scope" in record and record["review_scope"] not in {"task", "branch"}:
        violations.add("review_scope must be task or branch")
    for field in (
        "downgrade_attempted",
        "downgrade_after_critical_signal",
        "critical_signal_detected",
        "critical_signal",
    ):
        if field in record and type(record[field]) is not bool:
            violations.add(f"{field} must be boolean")
    if "critical_signals" in record:
        _add_string_list({"critical_signals": record["critical_signals"]}, "critical_signals", violations)
    recurrence = _validate_recurrence(record, violations)
    facts = _semantic_facts(record, recurrence)
    if set(hard_gates) - HARD_GATE_SIGNALS:
        violations.add("hard_gates contains an unsupported signal")
    route = (selected_model, effort)
    review = (review_model, review_effort)
    current_rank = _route_rank(*route)
    if facts["hard"] or facts["final_review"] or recurrence:
        if route != ("gpt-5.6-sol", "max"):
            if recurrence:
                violations.add(
                    "recurring failure requires gpt-5.6-sol with reasoning_effort max"
                )
            else:
                violations.add(
                    "hard gate requires gpt-5.6-sol with reasoning_effort max"
                )
        if review != ("gpt-5.6-sol", "max"):
            violations.add("hard gate and final review require a gpt-5.6-sol max review")
    elif facts["integration"] or facts["elevated"]:
        if route[0] != "gpt-5.6-sol" or route[1] not in ALLOWED_EFFORTS:
            violations.add("integration or elevated risk requires gpt-5.6-sol at xhigh or max")
        if review[0] != "gpt-5.6-sol" or review[1] not in ALLOWED_EFFORTS:
            violations.add("integration or elevated risk requires a gpt-5.6-sol review")
    elif facts["luna_max"]:
        if route != ("gpt-5.6-luna", "max"):
            violations.add("eligible low-risk audit or judgment requires gpt-5.6-luna with reasoning_effort max")
        if review != ("gpt-5.6-luna", "max"):
            violations.add("low-risk Luna work requires a gpt-5.6-luna max review")
    elif route == ("gpt-5.6-luna", "max"):
        violations.add("luna max requires low risk, low blast radius, and an explicit eligible task")
    if (
        (facts["hard"] or facts["integration"] or facts["elevated"] or recurrence)
        and _downgrade_detected(record, current_rank)
    ):
        violations.add("downgrade after a critical signal is forbidden")
    # Keep these local assignments explicit: they are part of the documentary
    # field audit and prevent a malformed list from being silently accepted.
    _ = risk_signals
    if violations:
        return sorted(violations)
    return [LEGACY_SUCCESS_MESSAGE]


__all__ = ["LEGACY_SUCCESS_MESSAGE", "validate_legacy_decision"]
