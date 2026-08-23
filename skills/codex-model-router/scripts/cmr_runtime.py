from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import cmr_contracts

sys.dont_write_bytecode = True

_CONTROL_PHASES = {"planning", "controller", "preflight"}
_SDD_PHASES = {"worker", "task_review", "re_review", "final_review"}
_ALL_PHASES = _CONTROL_PHASES | _SDD_PHASES
_PHASE_ORDER = (
    "planning",
    "controller",
    "preflight",
    "worker",
    "task_review",
    "re_review",
    "final_review",
)
_SELECTORS = ("run", "skip", "block")
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_OCCURRENCE_RECORD_FIELDS = frozenset(
    {
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
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEAD_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CONTROL_ROUTES = {
    "planning": ("gpt-5.6-sol", "max"),
    "controller": ("gpt-5.6-luna", "xhigh"),
    "preflight": ("gpt-5.6-luna", "max"),
}
_ROUND_FOUR_ROUTES = {
    ("gpt-5.6-luna", "xhigh"): ("gpt-5.6-luna", "max"),
    ("gpt-5.6-luna", "max"): ("gpt-5.6-sol", "max"),
    ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "max"),
    ("gpt-5.6-sol", "max"): ("gpt-5.6-sol", "max"),
}
_REVIEW_ROUTES = {
    ("gpt-5.6-luna", "xhigh"): ("gpt-5.6-luna", "max"),
    ("gpt-5.6-luna", "max"): ("gpt-5.6-luna", "max"),
    ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "xhigh"),
    ("gpt-5.6-sol", "max"): ("gpt-5.6-sol", "max"),
}
_TOOL_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "web_search",
    "tool_call",
}
_ITEM_WRAPPER_TYPES = {
    "item.started",
    "item.updated",
    "item.completed",
}
_TOP_LEVEL_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "error",
    "agent_message",
    *_ITEM_WRAPPER_TYPES,
}
_ITEM_TYPES = {"agent_message", "error", *_TOOL_ITEM_TYPES}


@dataclass(frozen=True)
class OccurrenceExpectation:
    phase: str
    scope_kind: str
    scope_id: str
    round_index: int | None
    task_id: str | None
    model: str
    reasoning_effort: str
    prompt_sha256: str
    forbidden_thread_ids: tuple[str, ...]
    prior_thread_id: str | None
    fork_turns: str
    thread_policy: str
    cwd_policy: Mapping[str, Any]
    write_policy: str
    response_policy: str

    @classmethod
    def for_sdd(
        cls,
        *,
        phase: str,
        scope_kind: str,
        scope_id: str,
        round_index: int | None,
        task_id: str | None,
        initial_worker_model: str,
        initial_worker_effort: str,
        prompt_sha256: str,
        forbidden_thread_ids: tuple[str, ...] = (),
        prior_thread_id: str | None = None,
        cwd_policy: Mapping[str, Any],
    ) -> "OccurrenceExpectation":
        initial_route = (initial_worker_model, initial_worker_effort)
        if initial_route not in _ROUND_FOUR_ROUTES:
            raise ValueError("initial worker route is outside the frozen route matrix")
        if phase not in _SDD_PHASES:
            raise ValueError("for_sdd requires an SDD phase")
        if round_index is not None and type(round_index) is not int:
            raise ValueError("SDD round_index must be an integer or null")

        if scope_kind == "release":
            if phase == "final_review" and round_index is None:
                route = ("gpt-5.6-sol", "max")
            elif phase in {"worker", "re_review"} and round_index == 1:
                route = ("gpt-5.6-sol", "max")
            else:
                raise ValueError("release SDD occurrence has an invalid phase or round")
        elif scope_kind == "task":
            if phase == "worker":
                route = _worker_route(initial_route, round_index)
            elif phase == "task_review" and type(round_index) is int and round_index == 0:
                route = _REVIEW_ROUTES[initial_route]
            elif phase == "re_review" and isinstance(round_index, int):
                route = _REVIEW_ROUTES[_worker_route(initial_route, round_index)]
            else:
                raise ValueError("task SDD occurrence has an invalid phase or round")
        else:
            raise ValueError("SDD scope must be task or release")

        resume = (
            phase == "worker"
            and scope_kind == "task"
            and isinstance(round_index, int)
            and 1 <= round_index <= 3
        )
        return cls(
            phase=phase,
            scope_kind=scope_kind,
            scope_id=scope_id,
            round_index=round_index,
            task_id=task_id,
            model=route[0],
            reasoning_effort=route[1],
            prompt_sha256=prompt_sha256,
            forbidden_thread_ids=tuple(forbidden_thread_ids),
            prior_thread_id=prior_thread_id,
            fork_turns="none",
            thread_policy="resume" if resume else "fresh",
            cwd_policy=dict(cwd_policy),
            write_policy=(
                "worktree_write" if phase == "worker" else "source_read_only"
            ),
            response_policy="sdd_report",
        )


def _worker_route(
    initial_route: tuple[str, str], round_index: int | None
) -> tuple[str, str]:
    if not isinstance(round_index, int) or isinstance(round_index, bool):
        raise ValueError("task worker round must be an integer")
    if 0 <= round_index <= 3:
        return initial_route
    if round_index == 4:
        return _ROUND_FOUR_ROUTES[initial_route]
    if round_index == 5:
        return ("gpt-5.6-sol", "max")
    raise ValueError("task worker round must be between 0 and 5")


@dataclass(frozen=True)
class UsageEvidence:
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int

    @classmethod
    def from_value(cls, value: Any) -> "UsageEvidence":
        required = (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
        if not isinstance(value, dict) or set(value) != set(required):
            raise ValueError(
                "usage must contain exactly input_tokens, cached_input_tokens, "
                "cache_write_input_tokens, output_tokens, and reasoning_output_tokens"
            )
        for field in required:
            amount = value[field]
            if type(amount) is not int or amount < 0:
                raise ValueError(f"usage.{field} must be a nonnegative integer")
        return cls(
            input_tokens=value["input_tokens"],
            cached_input_tokens=value["cached_input_tokens"],
            cache_write_input_tokens=value["cache_write_input_tokens"],
            output_tokens=value["output_tokens"],
            reasoning_output_tokens=value["reasoning_output_tokens"],
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
        }


@dataclass(frozen=True)
class ParsedEvents:
    events: tuple[Mapping[str, Any], ...]
    thread_id: str | None
    usage: UsageEvidence | None
    raw_object: dict[str, Any] | None
    terminal_report: str | None
    tool_event_count: int
    ignored_empty_agent_messages: int
    raw_sha256: str
    instrument_errors: tuple[str, ...]
    operational_errors: tuple[str, ...]

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.instrument_errors + self.operational_errors)))


@dataclass(frozen=True)
class OccurrenceAudit:
    schema_version: str
    phase: Any
    scope_kind: Any
    scope_id: Any
    round_index: Any
    task_id: Any
    response_policy: Any
    model: Any
    reasoning_effort: Any
    fork_turns: Any
    prompt_sha256: Any
    thread_policy: Any
    thread_id: str | None
    cwd: Any
    write_policy: Any
    exit_code: Any
    external_writes: Any
    transcript_sha256: str | None
    raw_sha256: str
    usage: UsageEvidence | None
    tool_event_count: int
    terminal_report_sha256: str | None
    ignored_empty_agent_messages: int
    accepted: bool
    failure_class: str | None
    errors: tuple[str, ...]
    raw_object: dict[str, Any] | None
    terminal_report: str | None

    @classmethod
    def from_record(
        cls,
        value: Any,
        *,
        label: str = "occurrence audit",
    ) -> "OccurrenceAudit":
        """Strictly deserialize one published audit record without raw evidence."""

        errors = _validate_occurrence_record(value)
        if errors:
            raise ValueError(f"{label} is invalid: {'; '.join(errors)}")
        item = dict(value)
        usage = (
            UsageEvidence.from_value(item["usage"])
            if item["usage"] is not None
            else None
        )
        return cls(
            schema_version=item["schema_version"],
            phase=item["phase"],
            scope_kind=item["scope_kind"],
            scope_id=item["scope_id"],
            round_index=item["round_index"],
            task_id=item["task_id"],
            response_policy=item["response_policy"],
            model=item["model"],
            reasoning_effort=item["reasoning_effort"],
            fork_turns=item["fork_turns"],
            prompt_sha256=item["prompt_sha256"],
            thread_policy=item["thread_policy"],
            thread_id=item["thread_id"],
            cwd=dict(item["cwd"]) if item["cwd"] is not None else None,
            write_policy=item["write_policy"],
            exit_code=item["exit_code"],
            external_writes=item["external_writes"],
            transcript_sha256=item["transcript_sha256"],
            raw_sha256=item["raw_sha256"],
            usage=usage,
            tool_event_count=item["tool_event_count"],
            terminal_report_sha256=item["terminal_report_sha256"],
            ignored_empty_agent_messages=item["ignored_empty_agent_messages"],
            accepted=item["accepted"],
            failure_class=item["failure_class"],
            errors=tuple(item["errors"]),
            raw_object=None,
            terminal_report=None,
        )

    def to_record(self) -> dict[str, Any]:
        cwd = dict(self.cwd) if isinstance(self.cwd, Mapping) else self.cwd
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "round_index": self.round_index,
            "task_id": self.task_id,
            "response_policy": self.response_policy,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "fork_turns": self.fork_turns,
            "prompt_sha256": self.prompt_sha256,
            "thread_policy": self.thread_policy,
            "thread_id": self.thread_id,
            "cwd": cwd,
            "write_policy": self.write_policy,
            "exit_code": self.exit_code,
            "external_writes": self.external_writes,
            "transcript_sha256": self.transcript_sha256,
            "raw_sha256": self.raw_sha256,
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "tool_event_count": self.tool_event_count,
            "terminal_report_sha256": self.terminal_report_sha256,
            "ignored_empty_agent_messages": self.ignored_empty_agent_messages,
            "accepted": self.accepted,
            "failure_class": self.failure_class,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class TaskAccountingState:
    """Immutable task state used to derive the expected SDD call shape."""

    task_id: str
    selector: str
    dispatchable: bool
    completed_worker_rounds: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("task_id must be a non-empty string")
        if not isinstance(self.selector, str) or self.selector not in _SELECTORS:
            raise ValueError("selector must be run, skip, or block")
        if type(self.dispatchable) is not bool:
            raise ValueError("dispatchable must be a boolean")
        if type(self.completed_worker_rounds) is not tuple:
            raise ValueError("completed_worker_rounds must be a tuple")
        for round_index in self.completed_worker_rounds:
            if (
                type(round_index) is not int
                or round_index < 0
                or round_index > 5
            ):
                raise ValueError("completed worker rounds must be integers from 0 through 5")
        if tuple(sorted(self.completed_worker_rounds)) != self.completed_worker_rounds:
            raise ValueError("completed worker rounds must be canonically ordered")
        if len(set(self.completed_worker_rounds)) != len(self.completed_worker_rounds):
            raise ValueError("completed worker rounds must be unique")
        if self.selector == "block" and self.dispatchable:
            raise ValueError("blocked task cannot be dispatchable")
        if not self.dispatchable:
            if self.completed_worker_rounds:
                raise ValueError("non-dispatchable task cannot have worker rounds")
            return
        if not self.completed_worker_rounds or self.completed_worker_rounds[0] != 0:
            raise ValueError("dispatchable task must start at worker round 0")
        expected = tuple(range(self.completed_worker_rounds[-1] + 1))
        if self.completed_worker_rounds != expected:
            raise ValueError("completed worker rounds must be contiguous from round 0")


@dataclass(frozen=True)
class ReleaseReviewState:
    """Immutable release review scope; scope IDs are always derived from HEADs."""

    initial_implementation_head: str
    initial_export_head: str
    initial_scope_id: str = field(init=False)
    fix_required: bool
    final_implementation_head: str
    final_export_head: str
    final_scope_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not _is_head(self.initial_implementation_head):
            raise ValueError("initial_implementation_head must be a lowercase Git HEAD")
        if not _is_head(self.initial_export_head):
            raise ValueError("initial_export_head must be a lowercase Git HEAD")
        if type(self.fix_required) is not bool:
            raise ValueError("fix_required must be a boolean")
        if not _is_head(self.final_implementation_head):
            raise ValueError("final_implementation_head must be a lowercase Git HEAD")
        if not _is_head(self.final_export_head):
            raise ValueError("final_export_head must be a lowercase Git HEAD")
        if not self.fix_required and (
            self.final_implementation_head != self.initial_implementation_head
            or self.final_export_head != self.initial_export_head
        ):
            raise ValueError("final HEAD pair must equal initial pair when no fix is required")
        if self.fix_required and (
            self.final_implementation_head == self.initial_implementation_head
            and self.final_export_head == self.initial_export_head
        ):
            raise ValueError("fix-required release must change at least one final HEAD")
        object.__setattr__(
            self,
            "initial_scope_id",
            _release_scope_id(
                self.initial_implementation_head, self.initial_export_head
            ),
        )
        object.__setattr__(
            self,
            "final_scope_id",
            _release_scope_id(self.final_implementation_head, self.final_export_head),
        )


def _decode_events(payload: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return [], ["raw JSONL is not valid UTF-8"]
    if not text:
        return [], ["raw JSONL is empty"]
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            errors.append(f"raw JSONL line {line_number} is empty")
            continue
        try:
            event = cmr_contracts.strict_json_object(
                line.encode("utf-8"), f"raw JSONL line {line_number}"
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            errors.append(f"raw JSONL line {line_number} has no event type")
            continue
        events.append(event)
    if not events:
        errors.append("raw JSONL contains no events")
    return events, errors


def _usage_from_events(events: list[dict[str, Any]]) -> UsageEvidence:
    usage_values = [
        event.get("usage")
        for event in events
        if event.get("type") == "turn.completed"
    ]
    if len(usage_values) != 1:
        raise ValueError("usage requires exactly one turn.completed event")
    return UsageEvidence.from_value(usage_values[0])


def extract_usage(payload: bytes) -> UsageEvidence:
    events, errors = _decode_events(payload)
    if errors:
        raise ValueError("invalid occurrence JSONL: " + "; ".join(sorted(set(errors))))
    return _usage_from_events(events)


def _event_evidence(
    events: list[dict[str, Any]], response_policy: str
) -> tuple[
    str | None,
    list[str],
    int,
    int,
    list[str],
    list[str],
]:
    thread_ids: list[str] = []
    messages: list[str] = []
    ignored_empty = 0
    tool_count = 0
    instrument_errors: list[str] = []
    operational_errors: list[str] = []

    for event in events:
        event_type = event.get("type")
        if (
            not isinstance(event_type, str)
            or event_type not in _TOP_LEVEL_EVENT_TYPES
        ):
            label = (
                event_type
                if isinstance(event_type, str) and event_type
                else "<invalid>"
            )
            instrument_errors.append(f"unsupported event type {label}")
            continue
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                instrument_errors.append("thread.started.thread_id must be non-empty")
            else:
                thread_ids.append(thread_id)
            continue
        if event_type in {"turn.started", "turn.completed"}:
            continue
        if event_type == "error":
            operational_errors.append("occurrence contains an error event")
            continue

        if event_type == "agent_message":
            item_type = "agent_message"
            item = event
        elif event_type in _ITEM_WRAPPER_TYPES:
            item = event.get("item")
            if not isinstance(item, dict):
                instrument_errors.append(f"{event_type}.item must be an object")
                continue
            item_type = item.get("type")

        if (
            not isinstance(item_type, str)
            or item_type not in _ITEM_TYPES
        ):
            label = (
                item_type
                if isinstance(item_type, str) and item_type
                else "<invalid>"
            )
            instrument_errors.append(f"unsupported item type {label}")
            continue

        if item_type == "agent_message":
            text = item.get("text")
            if not isinstance(text, str):
                instrument_errors.append("agent_message.text must be a string")
            elif not text.strip():
                ignored_empty += 1
            else:
                messages.append(text)
            continue
        if item_type == "error":
            operational_errors.append("occurrence contains an error item")
            continue

        if item_type in _TOOL_ITEM_TYPES:
            tool_count += 1
        if response_policy == "closed_json":
            operational_errors.append(
                f"closed_json contains disallowed {item_type or 'unknown'} item"
            )

    if len(thread_ids) != 1:
        instrument_errors.append("occurrence requires exactly one thread.started event")
    thread_id = thread_ids[0] if len(thread_ids) == 1 else None
    return (
        thread_id,
        messages,
        ignored_empty,
        tool_count,
        instrument_errors,
        operational_errors,
    )


def audit_jsonl(
    payload: bytes,
    expected: OccurrenceExpectation,
    response_validator: Callable[[Any], list[str]] | None,
) -> ParsedEvents:
    events, decode_errors = _decode_events(payload)
    instrument_errors = list(decode_errors)
    operational_errors: list[str] = []
    expected_policy = (
        "closed_json"
        if expected.phase in _CONTROL_PHASES
        else "sdd_report"
        if expected.phase in _SDD_PHASES
        else None
    )
    if expected_policy is None:
        instrument_errors.append(f"unknown occurrence phase {expected.phase}")
    elif expected.response_policy != expected_policy:
        instrument_errors.append(
            f"phase {expected.phase} requires response_policy {expected_policy}"
        )

    (
        thread_id,
        messages,
        ignored_empty,
        tool_count,
        event_instrument_errors,
        event_operational_errors,
    ) = _event_evidence(events, expected.response_policy)
    instrument_errors += event_instrument_errors
    operational_errors += event_operational_errors

    try:
        usage = _usage_from_events(events)
    except ValueError as exc:
        usage = None
        instrument_errors.append(str(exc))

    raw_object: dict[str, Any] | None = None
    terminal_report: str | None = None
    if expected.response_policy == "closed_json":
        if response_validator is None or not callable(response_validator):
            instrument_errors.append("closed_json requires a response validator")
        if len(messages) != 1:
            operational_errors.append(
                "closed_json requires exactly one non-empty agent_message"
            )
        else:
            try:
                raw_object = cmr_contracts.strict_json_object(
                    messages[0].encode("utf-8"), "closed_json response"
                )
            except ValueError as exc:
                operational_errors.append(str(exc))
            if raw_object is not None and callable(response_validator):
                try:
                    violations = response_validator(raw_object)
                except Exception as exc:  # pragma: no cover - defensive boundary
                    instrument_errors.append(f"response validator raised: {exc}")
                else:
                    if not isinstance(violations, list) or any(
                        not isinstance(item, str) or not item for item in violations
                    ):
                        instrument_errors.append(
                            "response validator must return a list of non-empty strings"
                        )
                    else:
                        operational_errors.extend(
                            f"response: {violation}" for violation in violations
                        )
    elif expected.response_policy == "sdd_report":
        if response_validator is not None:
            instrument_errors.append("sdd_report forbids a response validator")
        if not messages:
            operational_errors.append("sdd_report requires a non-empty terminal report")
        else:
            terminal_report = messages[-1]
    else:
        instrument_errors.append(
            f"unknown response_policy {expected.response_policy}"
        )

    return ParsedEvents(
        events=tuple(events),
        thread_id=thread_id,
        usage=usage,
        raw_object=raw_object,
        terminal_report=terminal_report,
        tool_event_count=tool_count,
        ignored_empty_agent_messages=ignored_empty,
        raw_sha256=hashlib.sha256(payload).hexdigest(),
        instrument_errors=tuple(sorted(set(instrument_errors))),
        operational_errors=tuple(sorted(set(operational_errors))),
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_head(value: Any) -> bool:
    return isinstance(value, str) and _HEAD_RE.fullmatch(value) is not None


def _is_absolute_normalized_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if value.startswith("/"):
        components = value[1:].split("/")
    elif re.match(r"^[A-Za-z]:/", value):
        components = value[3:].split("/")
    else:
        return False
    return bool(components) and all(component not in {"", ".", ".."} for component in components)


def _validate_cwd(value: Any, phase: str) -> list[str]:
    if not isinstance(value, dict):
        return ["cwd must be an object"]
    errors: list[str] = []
    if phase in _CONTROL_PHASES:
        fields = {"kind", "path", "fresh", "destroyed", "read_only"}
        if set(value) != fields:
            errors.append("control-plane cwd has unknown or missing fields")
        if value.get("kind") != "ephemeral":
            errors.append("control-plane cwd.kind must be ephemeral")
        if value.get("fresh") is not True:
            errors.append("control-plane cwd.fresh must be true")
        if value.get("destroyed") is not True:
            errors.append("control-plane cwd.destroyed must be true")
        if value.get("read_only") is not True:
            errors.append("control-plane cwd.read_only must be true")
    elif phase in _SDD_PHASES:
        fields = {
            "kind",
            "path",
            "workspace_id",
            "base_head",
            "head",
            "read_only",
        }
        if set(value) != fields:
            errors.append("SDD cwd has unknown or missing fields")
        if value.get("kind") != "plan_worktree":
            errors.append("SDD cwd.kind must be plan_worktree")
        if not isinstance(value.get("workspace_id"), str) or not value.get("workspace_id"):
            errors.append("SDD cwd.workspace_id must be non-empty")
        if not _is_head(value.get("base_head")):
            errors.append("SDD cwd.base_head must be a lowercase Git HEAD")
        if not _is_head(value.get("head")):
            errors.append("SDD cwd.head must be a lowercase Git HEAD")
        if type(value.get("read_only")) is not bool:
            errors.append("SDD cwd.read_only must be a boolean")
        elif phase == "worker" and value.get("read_only") is not False:
            errors.append("worker cwd.read_only must be false")
        elif phase in {"task_review", "re_review", "final_review"}:
            if value.get("read_only") is not True:
                errors.append("review cwd.read_only must be true")
            if value.get("base_head") != value.get("head"):
                errors.append("review cwd must bind equal HEAD values")
    else:
        errors.append("cwd cannot be validated for an unknown phase")
    if not _is_absolute_normalized_path(value.get("path")):
        errors.append("cwd.path must be an absolute normalized path")
    return errors


def _validate_occurrence_record(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return ["occurrence record must be a JSON object"]
    item = dict(value)
    errors: list[str] = []
    missing = sorted(_OCCURRENCE_RECORD_FIELDS - set(item))
    unknown = sorted(
        set(item) - _OCCURRENCE_RECORD_FIELDS,
        key=lambda field: repr(field),
    )
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    if unknown:
        errors.append(
            "unknown fields: " + ", ".join(str(field) for field in unknown)
        )
    if missing or unknown:
        return sorted(set(errors))

    phase = item["phase"]
    scope_kind = item["scope_kind"]
    scope_id = item["scope_id"]
    round_index = item["round_index"]
    task_id = item["task_id"]
    response_policy = item["response_policy"]
    model = item["model"]
    effort = item["reasoning_effort"]

    if item["schema_version"] != "cmr-occurrence-audit-v1":
        errors.append("schema_version must equal cmr-occurrence-audit-v1")
    if not isinstance(phase, str) or phase not in _ALL_PHASES:
        errors.append("phase is outside the published phase set")
    if not isinstance(scope_kind, str) or scope_kind not in {"plan", "task", "release"}:
        errors.append("scope_kind must be plan, task, or release")
    if not isinstance(scope_id, str) or not scope_id:
        errors.append("scope_id must be a non-empty string")
    if round_index is not None and (
        type(round_index) is not int or not 0 <= round_index <= 5
    ):
        errors.append("round_index must be an integer from 0 through 5 or null")
    if task_id is not None and (not isinstance(task_id, str) or not task_id):
        errors.append("task_id must be a non-empty string or null")

    if (
        not isinstance(response_policy, str)
        or response_policy not in {"closed_json", "sdd_report"}
    ):
        errors.append("response_policy must be closed_json or sdd_report")
    expected_response_policy = (
        "closed_json"
        if isinstance(phase, str) and phase in _CONTROL_PHASES
        else "sdd_report"
        if isinstance(phase, str) and phase in _SDD_PHASES
        else None
    )
    if expected_response_policy is not None and response_policy != expected_response_policy:
        errors.append("response_policy conflicts with phase")

    if not isinstance(model, str) or model not in {"gpt-5.6-luna", "gpt-5.6-sol"}:
        errors.append("model is outside the public route matrix")
    if not isinstance(effort, str) or effort not in {"xhigh", "max"}:
        errors.append("reasoning_effort is outside the public route matrix")
    route = (model, effort)
    if isinstance(phase, str) and phase in _CONTROL_ROUTES and route != _CONTROL_ROUTES[phase]:
        errors.append("model/reasoning_effort conflicts with phase and round")
    if isinstance(phase, str) and phase in {"task_review", "re_review", "final_review"} and route == (
        "gpt-5.6-luna",
        "xhigh",
    ):
        errors.append("model/reasoning_effort conflicts with phase and round")
    if (
        isinstance(phase, str)
        and phase in {"worker", "re_review"}
        and scope_kind == "task"
        and round_index == 4
        and effort != "max"
    ):
        errors.append("model/reasoning_effort conflicts with phase and round")
    if (
        isinstance(phase, str)
        and phase in {"worker", "re_review"}
        and scope_kind == "task"
        and round_index == 5
        and route != ("gpt-5.6-sol", "max")
    ):
        errors.append("model/reasoning_effort conflicts with phase and round")
    if scope_kind == "release" and isinstance(phase, str) and phase in _SDD_PHASES and route != (
        "gpt-5.6-sol",
        "max",
    ):
        errors.append("model/reasoning_effort conflicts with phase and round")

    if item["fork_turns"] != "none":
        errors.append("fork_turns must equal none")
    if not _is_sha256(item["prompt_sha256"]):
        errors.append("prompt_sha256 must be lowercase SHA-256")
    if (
        not isinstance(item["thread_policy"], str)
        or item["thread_policy"] not in {"fresh", "resume"}
    ):
        errors.append("thread_policy must be fresh or resume")
    expected_thread_policy = (
        "resume"
        if phase == "worker"
        and scope_kind == "task"
        and type(round_index) is int
        and 1 <= round_index <= 3
        else "fresh"
    )
    if item["thread_policy"] != expected_thread_policy:
        errors.append("thread_policy conflicts with phase and round")
    thread_id = item["thread_id"]
    if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
        errors.append("thread_id must be a non-empty string or null")

    cwd = item["cwd"]
    if cwd is not None:
        if isinstance(phase, str):
            errors.extend(_validate_cwd(cwd, phase))
        else:
            errors.append("cwd cannot be validated for an unknown phase")
    write_policy = item["write_policy"]
    if not isinstance(write_policy, str) or write_policy not in {
            "control_plane_no_write",
            "worktree_write",
            "source_read_only",
        }:
        errors.append("write_policy is outside the published policy set")
    expected_write_policy = (
        "control_plane_no_write"
        if isinstance(phase, str) and phase in _CONTROL_PHASES
        else "worktree_write"
        if phase == "worker"
        else "source_read_only"
        if isinstance(phase, str) and phase in {"task_review", "re_review", "final_review"}
        else None
    )
    if expected_write_policy is not None and write_policy != expected_write_policy:
        errors.append("write_policy conflicts with phase")

    if type(item["exit_code"]) is not int:
        errors.append("exit_code must be an integer")
    if type(item["external_writes"]) is not bool:
        errors.append("external_writes must be a boolean")
    transcript_sha256 = item["transcript_sha256"]
    if transcript_sha256 is not None and not _is_sha256(transcript_sha256):
        errors.append("transcript_sha256 must be lowercase SHA-256 or null")
    if not _is_sha256(item["raw_sha256"]):
        errors.append("raw_sha256 must be lowercase SHA-256")
    terminal_report_sha256 = item["terminal_report_sha256"]
    if terminal_report_sha256 is not None and not _is_sha256(terminal_report_sha256):
        errors.append("terminal_report_sha256 must be lowercase SHA-256 or null")

    usage_value = item["usage"]
    if usage_value is not None:
        try:
            UsageEvidence.from_value(usage_value)
        except ValueError as exc:
            errors.append(str(exc))
    for field_name in ("tool_event_count", "ignored_empty_agent_messages"):
        amount = item[field_name]
        if type(amount) is not int or amount < 0:
            errors.append(f"{field_name} must be a nonnegative integer")
    if type(item["accepted"]) is not bool:
        errors.append("accepted must be a boolean")
    if item["failure_class"] is not None and (
        not isinstance(item["failure_class"], str)
        or item["failure_class"] not in {"operational", "instrument"}
    ):
        errors.append("failure_class must be operational, instrument, or null")
    record_errors = item["errors"]
    valid_error_strings = isinstance(record_errors, list) and all(
        isinstance(error, str) and bool(error) for error in record_errors
    )
    if not valid_error_strings or len(record_errors) != len(set(record_errors)):
        errors.append("errors must contain unique non-empty strings")

    if isinstance(phase, str):
        if phase == "planning":
            if (
                scope_kind != "plan"
                or not _is_sha256(scope_id)
                or round_index is not None
                or task_id is not None
            ):
                errors.append("planning cardinality key is invalid")
        elif phase in {"controller", "preflight"}:
            if scope_kind != "task" or round_index is not None or task_id != scope_id:
                errors.append(f"{phase} cardinality key is invalid")
        elif phase == "worker":
            task_scope = (
                scope_kind == "task"
                and task_id == scope_id
                and type(round_index) is int
                and 0 <= round_index <= 5
            )
            release_scope = (
                scope_kind == "release"
                and _is_sha256(scope_id)
                and task_id is None
                and round_index == 1
            )
            if not task_scope and not release_scope:
                errors.append("worker cardinality key is invalid")
        elif phase == "task_review":
            if (
                scope_kind != "task"
                or task_id != scope_id
                or type(round_index) is not int
                or round_index != 0
            ):
                errors.append("task_review cardinality key is invalid")
        elif phase == "re_review":
            task_scope = (
                scope_kind == "task"
                and task_id == scope_id
                and type(round_index) is int
                and 1 <= round_index <= 5
            )
            release_scope = (
                scope_kind == "release"
                and _is_sha256(scope_id)
                and task_id is None
                and round_index == 1
            )
            if not task_scope and not release_scope:
                errors.append("re_review cardinality key is invalid")
        elif phase == "final_review":
            if (
                scope_kind != "release"
                or not _is_sha256(scope_id)
                or task_id is not None
                or round_index is not None
            ):
                errors.append("final_review cardinality key is invalid")

    accepted = item["accepted"]
    failure_class = item["failure_class"]
    if accepted is True:
        if failure_class is not None or record_errors != []:
            errors.append("accepted audit must have null failure_class and empty errors")
        if not isinstance(thread_id, str) or not thread_id:
            errors.append("accepted audit must have a non-empty thread_id")
        if cwd is None:
            errors.append("accepted audit must have CWD evidence")
        if item["exit_code"] != 0:
            errors.append("accepted audit must have exit_code zero")
        if item["external_writes"] is not False:
            errors.append("accepted audit must have external_writes false")
        if transcript_sha256 is None:
            errors.append("accepted audit must have transcript_sha256")
        if usage_value is None:
            errors.append("accepted audit must have usage")
        if response_policy == "closed_json":
            if item["tool_event_count"] != 0:
                errors.append("accepted closed_json audit must have zero tool events")
            if terminal_report_sha256 is not None:
                errors.append("accepted closed_json audit must not have terminal report hash")
        elif response_policy == "sdd_report" and terminal_report_sha256 is None:
            errors.append("accepted sdd_report audit must have terminal report hash")
    elif accepted is False:
        if (
            not isinstance(failure_class, str)
            or failure_class not in {"operational", "instrument"}
            or not record_errors
        ):
            errors.append("rejected audit must have a failure_class and non-empty errors")

    return sorted(set(errors))


def _expected_thread_policy(expected: OccurrenceExpectation) -> str:
    if (
        expected.phase == "worker"
        and expected.scope_kind == "task"
        and isinstance(expected.round_index, int)
        and not isinstance(expected.round_index, bool)
        and 1 <= expected.round_index <= 3
    ):
        return "resume"
    return "fresh"


def _validate_expectation(expected: OccurrenceExpectation) -> list[str]:
    errors: list[str] = []
    route = (expected.model, expected.reasoning_effort)
    if route not in _ROUND_FOUR_ROUTES:
        errors.append("expectation route is outside the public model/effort matrix")
    if expected.phase in {"task_review", "re_review", "final_review"} and route == (
        "gpt-5.6-luna",
        "xhigh",
    ):
        errors.append("expectation review route cannot be gpt-5.6-luna/xhigh")
    if (
        expected.scope_kind == "task"
        and expected.phase in {"worker", "re_review"}
        and expected.round_index == 4
        and expected.reasoning_effort != "max"
    ):
        errors.append("expectation round-4 route must use max effort")
    if (
        expected.scope_kind == "task"
        and expected.phase in {"worker", "re_review"}
        and expected.round_index == 5
        and route != ("gpt-5.6-sol", "max")
    ):
        errors.append("expectation round-5 route must be gpt-5.6-sol/max")
    if expected.phase not in _ALL_PHASES:
        errors.append("expectation.phase is unknown")
    expected_response = (
        "closed_json" if expected.phase in _CONTROL_PHASES else "sdd_report"
    )
    if expected.response_policy != expected_response:
        errors.append("expectation response_policy conflicts with phase")
    if expected.fork_turns != "none":
        errors.append("expectation fork_turns must be none")
    if not _is_sha256(expected.prompt_sha256):
        errors.append("expectation prompt_sha256 must be lowercase SHA-256")
    if expected.thread_policy != _expected_thread_policy(expected):
        errors.append("expectation thread_policy conflicts with phase and round")
    if any(not isinstance(item, str) or not item for item in expected.forbidden_thread_ids):
        errors.append("expectation forbidden_thread_ids must be non-empty strings")
    if len(expected.forbidden_thread_ids) != len(set(expected.forbidden_thread_ids)):
        errors.append("expectation forbidden_thread_ids must be unique")
    if expected.thread_policy == "resume" and not expected.prior_thread_id:
        errors.append("resume expectation requires prior_thread_id")
    if (
        expected.thread_policy == "resume"
        and expected.prior_thread_id in set(expected.forbidden_thread_ids)
    ):
        errors.append("prior_thread_id cannot be forbidden for a resume")

    if expected.phase in _CONTROL_PHASES:
        if expected.write_policy != "control_plane_no_write":
            errors.append("control-plane expectation has wrong write_policy")
        if (expected.model, expected.reasoning_effort) != _CONTROL_ROUTES[expected.phase]:
            errors.append("control-plane expectation has wrong model route")
    elif expected.phase == "worker":
        if expected.write_policy != "worktree_write":
            errors.append("worker expectation has wrong write_policy")
    elif expected.phase in {"task_review", "re_review", "final_review"}:
        if expected.write_policy != "source_read_only":
            errors.append("review expectation has wrong write_policy")

    if expected.phase == "planning":
        valid_cardinality = (
            expected.scope_kind == "plan"
            and _is_sha256(expected.scope_id)
            and expected.round_index is None
            and expected.task_id is None
        )
    elif expected.phase in {"controller", "preflight"}:
        valid_cardinality = (
            expected.scope_kind == "task"
            and isinstance(expected.scope_id, str)
            and expected.scope_id
            and expected.task_id == expected.scope_id
            and expected.round_index is None
        )
    elif expected.scope_kind == "task":
        if expected.phase == "worker":
            round_valid = (
                type(expected.round_index) is int and 0 <= expected.round_index <= 5
            )
        elif expected.phase == "task_review":
            round_valid = type(expected.round_index) is int and expected.round_index == 0
        elif expected.phase == "re_review":
            round_valid = (
                type(expected.round_index) is int and 1 <= expected.round_index <= 5
            )
        else:
            round_valid = False
        valid_cardinality = (
            round_valid
            and isinstance(expected.scope_id, str)
            and bool(expected.scope_id)
            and expected.task_id == expected.scope_id
        )
    elif expected.scope_kind == "release":
        valid_cardinality = (
            _is_sha256(expected.scope_id)
            and expected.task_id is None
            and (
                (expected.phase == "final_review" and expected.round_index is None)
                or (
                    expected.phase in {"worker", "re_review"}
                    and type(expected.round_index) is int
                    and expected.round_index == 1
                )
            )
        )
        if expected.phase in {"worker", "re_review", "final_review"} and (
            expected.model,
            expected.reasoning_effort,
        ) != ("gpt-5.6-sol", "max"):
            errors.append("release expectation route must be gpt-5.6-sol/max")
    else:
        valid_cardinality = False
    if not valid_cardinality:
        errors.append("expectation cardinality key is invalid for phase")
    errors += [f"expectation {error}" for error in _validate_cwd(expected.cwd_policy, expected.phase)]
    return sorted(set(errors))


def _release_scope_id(implementation_head: str, export_head: str) -> str:
    payload = (
        f"cmr-release-scope-v1\n{implementation_head}\n{export_head}\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _accounting_key(
    phase: Any, scope_kind: Any, scope_id: Any, round_index: Any
) -> tuple[Any, Any, Any, Any]:
    return (phase, scope_kind, scope_id, round_index)


def _accounting_key_text(key: tuple[Any, Any, Any, Any]) -> str:
    phase, scope_kind, scope_id, round_index = key
    round_text = "null" if round_index is None else str(round_index)
    return f"{phase}:{scope_kind}:{scope_id}:{round_text}"


def _accounting_key_label(key: tuple[Any, Any, Any, Any]) -> str:
    phase, _scope_kind, scope_id, round_index = key
    label = f"{phase} key for {scope_id}"
    if round_index is not None:
        label += f" round {round_index}"
    return label


def _valid_accounting_key(key: tuple[Any, Any, Any, Any]) -> bool:
    phase, scope_kind, scope_id, round_index = key
    return (
        isinstance(phase, str)
        and phase in _ALL_PHASES
        and isinstance(scope_kind, str)
        and scope_kind in {"plan", "task", "release"}
        and isinstance(scope_id, str)
        and bool(scope_id)
        and (
            round_index is None
            or (type(round_index) is int and 0 <= round_index <= 5)
        )
    )


def _cardinality_sort_key(key: tuple[Any, Any, Any, Any]) -> tuple[Any, ...]:
    phase, scope_kind, scope_id, round_index = key
    phase_index = _PHASE_ORDER.index(phase) if phase in _PHASE_ORDER else len(_PHASE_ORDER)
    scope_bytes = scope_id.encode("utf-8") if isinstance(scope_id, str) else repr(scope_id).encode("utf-8")
    round_key = (0, 0) if round_index is None else (1, round_index)
    scope_kind_bytes = (
        scope_kind.encode("utf-8")
        if isinstance(scope_kind, str)
        else repr(scope_kind).encode("utf-8")
    )
    return (phase_index, scope_bytes, round_key, scope_kind_bytes)


def _state_validation(
    task_states: Sequence[TaskAccountingState] | Any,
) -> tuple[list[TaskAccountingState], list[str]]:
    errors: list[str] = []
    if isinstance(task_states, (str, bytes)):
        return [], ["task_states must be a sequence of TaskAccountingState"]
    try:
        values = list(task_states)
    except TypeError:
        return [], ["task_states must be a sequence of TaskAccountingState"]
    states: list[TaskAccountingState] = []
    seen: set[str] = set()
    for index, state in enumerate(values):
        if not isinstance(state, TaskAccountingState):
            errors.append(f"task_states[{index}] must be TaskAccountingState")
            continue
        if state.task_id in seen:
            errors.append(f"duplicate task ID {state.task_id}")
            continue
        seen.add(state.task_id)
        states.append(state)
    return states, errors


def _expected_accounting_keys(
    states: Sequence[TaskAccountingState],
    *,
    plan_sha256: str,
    release_review: ReleaseReviewState | None,
) -> set[tuple[Any, Any, Any, Any]]:
    expected = {
        _accounting_key("planning", "plan", plan_sha256, None),
    }
    for state in states:
        expected.add(_accounting_key("controller", "task", state.task_id, None))
        if state.selector == "run":
            expected.add(_accounting_key("preflight", "task", state.task_id, None))
        if not state.dispatchable:
            continue
        for round_index in state.completed_worker_rounds:
            expected.add(
                _accounting_key("worker", "task", state.task_id, round_index)
            )
        expected.add(_accounting_key("task_review", "task", state.task_id, 0))
        for round_index in state.completed_worker_rounds:
            if round_index > 0:
                expected.add(
                    _accounting_key("re_review", "task", state.task_id, round_index)
                )
    if release_review is not None:
        expected.add(
            _accounting_key("final_review", "release", release_review.initial_scope_id, None)
        )
        if release_review.fix_required:
            expected.add(
                _accounting_key("worker", "release", release_review.initial_scope_id, 1)
            )
            expected.add(
                _accounting_key("re_review", "release", release_review.final_scope_id, 1)
            )
    return expected


def _validate_observed_occurrence_key(
    occurrence: OccurrenceAudit,
) -> tuple[tuple[Any, Any, Any, Any] | None, list[str]]:
    phase = occurrence.phase
    scope_kind = occurrence.scope_kind
    scope_id = occurrence.scope_id
    round_index = occurrence.round_index
    task_id = occurrence.task_id
    key = _accounting_key(phase, scope_kind, scope_id, round_index)
    errors: list[str] = []
    if not isinstance(phase, str) or phase not in _ALL_PHASES:
        errors.append(f"unknown occurrence phase {phase!r}")
    if not isinstance(scope_kind, str) or scope_kind not in {"plan", "task", "release"}:
        errors.append("occurrence scope_kind is invalid")
    if not isinstance(scope_id, str) or not scope_id:
        errors.append("occurrence scope_id must be a non-empty string")
    if round_index is not None and (
        type(round_index) is not int or round_index < 0 or round_index > 5
    ):
        errors.append("occurrence round_index must be an integer from 0 through 5 or null")
    if phase == "planning":
        if scope_kind != "plan" or round_index is not None or task_id is not None:
            errors.append("planning cardinality key is invalid")
    elif phase in {"controller", "preflight"}:
        if scope_kind != "task" or round_index is not None or task_id != scope_id:
            errors.append(f"{phase} cardinality key is invalid")
    elif phase in {"worker", "task_review", "re_review"}:
        if scope_kind == "task":
            if task_id != scope_id:
                errors.append(f"{phase} task ID does not match scope ID")
            if phase == "worker" and (
                type(round_index) is not int or not 0 <= round_index <= 5
            ):
                errors.append("worker cardinality key is invalid")
            elif phase == "task_review" and round_index != 0:
                errors.append("task_review cardinality key is invalid")
            elif phase == "re_review" and (
                type(round_index) is not int or not 1 <= round_index <= 5
            ):
                errors.append("re_review cardinality key is invalid")
        elif scope_kind == "release":
            if task_id is not None or phase not in {"worker", "re_review"} or round_index != 1:
                errors.append(f"{phase} release cardinality key is invalid")
        else:
            errors.append(f"{phase} cardinality key is invalid")
    elif phase == "final_review":
        if scope_kind != "release" or round_index is not None or task_id is not None:
            errors.append("final_review cardinality key is invalid")
    if not _valid_accounting_key(key):
        return None, errors
    return key, errors


def validate_call_shape(
    occurrences: Sequence[OccurrenceAudit],
    task_states: Sequence[TaskAccountingState],
    *,
    plan_sha256: str,
    release_review: ReleaseReviewState | None,
) -> list[str]:
    """Return stable violations for the complete immutable call-cardinality ledger."""

    states, errors = _state_validation(task_states)
    if not _is_sha256(plan_sha256):
        errors.append("plan_sha256 must be a lowercase SHA-256")
    if release_review is not None and not isinstance(release_review, ReleaseReviewState):
        errors.append("release_review must be ReleaseReviewState or None")
        release_review = None
    expected_plan = (
        plan_sha256
        if isinstance(plan_sha256, str)
        else f"<invalid-plan-{type(plan_sha256).__name__}>"
    )
    expected = _expected_accounting_keys(
        states,
        plan_sha256=expected_plan,
        release_review=release_review,
    )
    observed: dict[tuple[Any, Any, Any, Any], int] = {}
    try:
        occurrence_values = list(occurrences)
    except TypeError:
        return sorted(set(errors + ["occurrences must be a sequence of OccurrenceAudit"]))
    for index, occurrence in enumerate(occurrence_values):
        if not isinstance(occurrence, OccurrenceAudit):
            errors.append(f"occurrences[{index}] must be OccurrenceAudit")
            continue
        key, key_errors = _validate_observed_occurrence_key(occurrence)
        errors.extend(key_errors)
        if key is not None:
            observed[key] = observed.get(key, 0) + 1
    for key in sorted(expected, key=_cardinality_sort_key):
        count = observed.get(key, 0)
        if count == 0:
            errors.append(f"missing {_accounting_key_label(key)}")
        elif count != 1:
            errors.append(f"{_accounting_key_label(key)} has {count} attempts")
    for key in sorted(set(observed) - expected, key=_cardinality_sort_key):
        errors.append(f"unexpected {_accounting_key_label(key)}")
    return sorted(set(errors))


def _ordered_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values), key=lambda value: value.encode("utf-8"))


def _occurrence_failure_id(occurrence: OccurrenceAudit, index: int) -> str:
    key = _accounting_key(
        occurrence.phase,
        occurrence.scope_kind,
        occurrence.scope_id,
        occurrence.round_index,
    )
    if all(isinstance(item, str) for item in key[:3]) and (
        key[3] is None or type(key[3]) is int
    ):
        return _accounting_key_text(key)
    return f"occurrence-{index}"


def _usage_dict(value: Any) -> tuple[dict[str, int] | None, str | None]:
    if isinstance(value, UsageEvidence):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        return None, "usage is missing or not an object"
    try:
        parsed = UsageEvidence.from_value(payload)
    except ValueError as exc:
        return None, str(exc)
    return parsed.to_dict(), None


def _input_failure_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        values = list(value.keys())
    elif isinstance(value, (str, bytes)):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    result: list[str] = []
    for index, item in enumerate(values):
        if isinstance(item, str) and item:
            result.append(item)
        else:
            result.append(f"instrument-error-{index}")
    return _ordered_unique(result)


def _semantic_failed(value: Any) -> bool | None:
    if type(value) is bool:
        return not value
    if isinstance(value, Mapping):
        if type(value.get("passed")) is bool:
            return not value["passed"]
        if type(value.get("semantic_failure")) is bool:
            return value["semantic_failure"]
        if type(value.get("failed")) is bool:
            return value["failed"]
        status = value.get("status")
        if isinstance(status, str):
            if status in {"pass", "passed", "success", "accepted"}:
                return False
            if status in {"fail", "failed", "failure", "rejected"}:
                return True
    if isinstance(value, str):
        if value in {"pass", "passed", "success", "accepted"}:
            return False
        if value in {"fail", "failed", "failure", "rejected"}:
            return True
    return None


def _semantic_failure_ids(
    semantic_results: Any,
    occurrences: Sequence[OccurrenceAudit],
    instrument_ids: set[str],
    operational_ids: list[str],
) -> list[str]:
    known_tasks: set[str] = set()
    non_clean_tasks: set[str] = set()
    known_occurrences: set[str] = set()
    accepted_tasks: set[str] = set()
    accepted_occurrences: dict[str, str] = {}
    for index, occurrence in enumerate(occurrences):
        if not isinstance(occurrence, OccurrenceAudit):
            continue
        occurrence_id = _occurrence_failure_id(occurrence, index)
        _key, key_errors = _validate_observed_occurrence_key(occurrence)
        _parsed_usage, usage_error = _usage_dict(occurrence.usage)
        if not key_errors and usage_error is None:
            known_occurrences.add(occurrence_id)
        if isinstance(occurrence.task_id, str):
            known_tasks.add(occurrence.task_id)
            if (
                occurrence.accepted is not True
                or occurrence.failure_class is not None
                or occurrence.errors
                or key_errors
                or usage_error is not None
            ):
                non_clean_tasks.add(occurrence.task_id)
        if occurrence.accepted is not True:
            continue
        if occurrence.failure_class is not None or occurrence.errors:
            continue
        if key_errors or usage_error is not None:
            continue
        if isinstance(occurrence.task_id, str):
            accepted_tasks.add(occurrence.task_id)
            accepted_occurrences[occurrence_id] = occurrence.task_id
        else:
            accepted_occurrences[occurrence_id] = occurrence_id

    def binding_state(identifier: str) -> tuple[str, str]:
        if identifier in accepted_occurrences:
            return "clean", accepted_occurrences[identifier]
        if identifier in accepted_tasks and identifier not in non_clean_tasks:
            return "clean", identifier
        if identifier in known_occurrences or identifier in known_tasks:
            return "non_clean", identifier
        return "unknown", identifier

    def add_binding_failure(identifier: str) -> None:
        operational_ids.append(identifier)

    failures: set[str] = set()
    semantic_sources: dict[str, str] = {}
    if semantic_results is None:
        return []
    if isinstance(semantic_results, Mapping):
        items = list(semantic_results.items())
    else:
        try:
            values = list(semantic_results)
        except TypeError:
            instrument_ids.add("semantic-results")
            return []
        items = []
        for index, item in enumerate(values):
            if not isinstance(item, Mapping) or not isinstance(item.get("task_id"), str):
                instrument_ids.add(f"semantic-result-{index}")
                continue
            items.append((item["task_id"], item))
    seen_identifiers: set[str] = set()
    for identifier, value in items:
        if not isinstance(identifier, str) or not identifier:
            instrument_ids.add("semantic-result-id")
            continue
        duplicate = identifier in seen_identifiers
        seen_identifiers.add(identifier)
        if duplicate:
            previous = semantic_sources.pop(identifier, None)
            if previous is not None and previous not in semantic_sources.values():
                failures.discard(previous)
        state, task_id = binding_state(identifier)
        if isinstance(value, Mapping) and "accepted" in value:
            if type(value["accepted"]) is not bool:
                instrument_ids.add(f"semantic:{identifier}")
                continue
            if not value["accepted"]:
                if state == "unknown":
                    add_binding_failure(identifier)
                if duplicate:
                    add_binding_failure(identifier)
                continue
        failed = _semantic_failed(value)
        if failed is None:
            instrument_ids.add(f"semantic:{identifier}")
            continue
        if state != "clean":
            add_binding_failure(identifier)
            continue
        if duplicate:
            add_binding_failure(identifier)
            continue
        if failed:
            failures.add(task_id)
            semantic_sources[identifier] = task_id
    return _ordered_unique(list(failures))


def summarize_accounting(
    occurrences: Sequence[OccurrenceAudit],
    task_states: Sequence[TaskAccountingState],
    semantic_results: Any,
    instrument_errors: Any,
    *,
    plan_sha256: str,
    release_review: ReleaseReviewState | None,
) -> dict[str, Any]:
    """Summarize every attempted occurrence without collapsing phase identity."""

    states, state_errors = _state_validation(task_states)
    shape_errors = validate_call_shape(
        occurrences,
        task_states,
        plan_sha256=plan_sha256,
        release_review=release_review,
    )
    instrument_ids: set[str] = set(_input_failure_ids(instrument_errors))
    if state_errors:
        instrument_ids.add("accounting-task-state")
    if shape_errors:
        instrument_ids.add("accounting-call-shape")

    try:
        occurrence_values = list(occurrences)
    except TypeError:
        occurrence_values = []
        instrument_ids.add("occurrences")

    phase_counts = {phase: 0 for phase in _PHASE_ORDER}
    cardinality: dict[tuple[Any, Any, Any, Any], int] = {}
    usage = {field_name: 0 for field_name in _USAGE_FIELDS}
    operational_ids: list[str] = []
    for index, occurrence in enumerate(occurrence_values):
        if not isinstance(occurrence, OccurrenceAudit):
            instrument_ids.add(f"occurrence-{index}")
            continue
        if isinstance(occurrence.phase, str) and occurrence.phase in phase_counts:
            phase_counts[occurrence.phase] += 1
        key, key_errors = _validate_observed_occurrence_key(occurrence)
        if key_errors:
            instrument_ids.add(_occurrence_failure_id(occurrence, index))
        if key is not None:
            cardinality[key] = cardinality.get(key, 0) + 1

        occurrence_id = _occurrence_failure_id(occurrence, index)
        if occurrence.accepted is True:
            if occurrence.failure_class is not None or occurrence.errors:
                instrument_ids.add(occurrence_id)
        elif occurrence.accepted is False:
            if occurrence.failure_class == "operational":
                operational_ids.append(occurrence_id)
            elif occurrence.failure_class == "instrument":
                instrument_ids.add(occurrence_id)
            else:
                instrument_ids.add(occurrence_id)
        else:
            instrument_ids.add(occurrence_id)

        parsed_usage, usage_error = _usage_dict(occurrence.usage)
        if usage_error is not None:
            instrument_ids.add(occurrence_id)
        elif parsed_usage is not None:
            for field_name in _USAGE_FIELDS:
                usage[field_name] += parsed_usage[field_name]

    semantic_ids = _semantic_failure_ids(
        semantic_results, occurrence_values, instrument_ids, operational_ids
    )
    expected_plan = (
        plan_sha256
        if isinstance(plan_sha256, str)
        else f"<invalid-plan-{type(plan_sha256).__name__}>"
    )
    expected_keys = _expected_accounting_keys(
        states,
        plan_sha256=expected_plan,
        release_review=(release_review if isinstance(release_review, ReleaseReviewState) else None),
    )
    expected_phase_counts = {phase: 0 for phase in _PHASE_ORDER}
    for key in expected_keys:
        if key[0] in expected_phase_counts:
            expected_phase_counts[key[0]] += 1
    selector_counts = {selector: 0 for selector in _SELECTORS}
    for state in states:
        selector_counts[state.selector] += 1
    transport_retry_count = sum(
        max(count - 1, 0)
        for key, count in cardinality.items()
        if key[0] in _CONTROL_PHASES
    )
    sdd_fix_rounds = sum(
        sum(1 for round_index in state.completed_worker_rounds if round_index > 0)
        for state in states
    )
    if isinstance(release_review, ReleaseReviewState) and release_review.fix_required:
        sdd_fix_rounds += 1
    return {
        "schema_version": "cmr-accounting-summary-v1",
        "task_count": len(states),
        "selector_counts": selector_counts,
        "phase_counts": phase_counts,
        "expected_phase_counts": expected_phase_counts,
        "call_cardinality": [
            {
                "phase": key[0],
                "scope_kind": key[1],
                "scope_id": key[2],
                "round_index": key[3],
                "attempt_count": cardinality[key],
            }
            for key in sorted(cardinality, key=_cardinality_sort_key)
        ],
        "total_calls": len(occurrence_values),
        "transport_retry_count": transport_retry_count,
        "sdd_fix_rounds": sdd_fix_rounds,
        "canonical_call_shape": not shape_errors and not instrument_ids,
        "usage": usage,
        "operational_failures": _ordered_unique(operational_ids),
        "semantic_failures": semantic_ids,
        "instrument_failures": _ordered_unique(list(instrument_ids)),
    }


def _metadata_value(metadata: Mapping[str, Any], field: str) -> Any:
    return metadata.get(field)


def audit_occurrence(
    payload: bytes,
    metadata: Mapping[str, Any],
    expected: OccurrenceExpectation,
    response_validator: Callable[[Any], list[str]] | None = None,
) -> OccurrenceAudit:
    parsed = audit_jsonl(payload, expected, response_validator)
    instrument_errors = list(parsed.instrument_errors)
    operational_errors = list(parsed.operational_errors)
    instrument_errors += _validate_expectation(expected)

    exact_fields = (
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
        "write_policy",
    )
    for field in exact_fields:
        observed = _metadata_value(metadata, field)
        expected_value = getattr(expected, field)
        mismatch = observed != expected_value
        if field == "round_index" and observed is not None:
            mismatch = type(observed) is not int or observed != expected_value
        if mismatch:
            instrument_errors.append(f"metadata {field} does not match expectation")

    observed_cwd = metadata.get("cwd")
    instrument_errors += _validate_cwd(observed_cwd, expected.phase)
    if observed_cwd != expected.cwd_policy:
        instrument_errors.append("metadata cwd does not match expectation")

    metadata_thread = metadata.get("thread_id")
    if metadata_thread != parsed.thread_id:
        instrument_errors.append("metadata thread_id does not match JSONL thread")
    if parsed.thread_id is not None:
        if expected.thread_policy == "fresh":
            if parsed.thread_id in set(expected.forbidden_thread_ids):
                instrument_errors.append("fresh thread_id was reused")
            if expected.prior_thread_id and parsed.thread_id == expected.prior_thread_id:
                instrument_errors.append("fresh thread_id reused prior thread")
        elif expected.thread_policy == "resume":
            if parsed.thread_id != expected.prior_thread_id:
                instrument_errors.append("resume thread_id does not equal prior implementer thread")

    if metadata.get("external_writes") is not False:
        instrument_errors.append("metadata external_writes must be false")
    exit_code = metadata.get("exit_code")
    if type(exit_code) is not int:
        instrument_errors.append("metadata exit_code must be an integer")
    elif exit_code != 0:
        operational_errors.append("metadata exit_code must be zero")

    if metadata.get("raw_sha256") != parsed.raw_sha256:
        instrument_errors.append("metadata raw_sha256 does not bind raw JSONL")

    transcript_payload = metadata.get("transcript_payload")
    transcript_sha256: str | None = None
    if not isinstance(transcript_payload, bytes):
        instrument_errors.append("metadata transcript_payload must be bytes")
    else:
        transcript_sha256 = hashlib.sha256(transcript_payload).hexdigest()
    if metadata.get("transcript_sha256") != transcript_sha256:
        instrument_errors.append("metadata transcript_sha256 does not bind transcript_payload")

    metadata_usage_value = metadata.get("usage")
    metadata_usage: UsageEvidence | None = None
    try:
        metadata_usage = UsageEvidence.from_value(metadata_usage_value)
    except ValueError as exc:
        instrument_errors.append(f"metadata usage is invalid: {exc}")

    if parsed.usage is None:
        if metadata_usage_value is not None:
            instrument_errors.append("metadata usage cannot bind malformed usage")
    elif metadata_usage is not None and metadata_usage != parsed.usage:
        instrument_errors.append("metadata usage does not bind JSONL usage")

    terminal_report_sha256: str | None = None
    if parsed.terminal_report is not None:
        terminal_report_sha256 = hashlib.sha256(
            parsed.terminal_report.encode("utf-8")
        ).hexdigest()
    if metadata.get("terminal_report_sha256") != terminal_report_sha256:
        instrument_errors.append(
            "metadata terminal_report_sha256 does not bind terminal report"
        )

    if expected.phase == "planning":
        if metadata.get("plan_sha256") != expected.scope_id:
            instrument_errors.append("metadata plan_sha256 does not bind planning scope")
    if expected.scope_kind == "release":
        release_scope = metadata.get("release_scope")
        if not isinstance(release_scope, dict) or set(release_scope) != {
            "implementation_head",
            "export_head",
        }:
            instrument_errors.append("metadata release scope must contain the canonical HEAD pair")
        else:
            implementation_head = release_scope.get("implementation_head")
            export_head = release_scope.get("export_head")
            if not _is_head(implementation_head) or not _is_head(export_head):
                instrument_errors.append("metadata release scope HEAD values are invalid")
            elif _release_scope_id(implementation_head, export_head) != expected.scope_id:
                instrument_errors.append("metadata release scope does not bind scope_id")

    instrument_errors = sorted(set(instrument_errors))
    operational_errors = sorted(set(operational_errors))
    errors = tuple(sorted(set(instrument_errors + operational_errors)))
    accepted = not errors
    failure_class = None
    if not accepted:
        failure_class = "instrument" if instrument_errors else "operational"

    return OccurrenceAudit(
        schema_version="cmr-occurrence-audit-v1",
        phase=metadata.get("phase"),
        scope_kind=metadata.get("scope_kind"),
        scope_id=metadata.get("scope_id"),
        round_index=metadata.get("round_index"),
        task_id=metadata.get("task_id"),
        response_policy=metadata.get("response_policy"),
        model=metadata.get("model"),
        reasoning_effort=metadata.get("reasoning_effort"),
        fork_turns=metadata.get("fork_turns"),
        prompt_sha256=metadata.get("prompt_sha256"),
        thread_policy=metadata.get("thread_policy"),
        thread_id=parsed.thread_id,
        cwd=observed_cwd,
        write_policy=metadata.get("write_policy"),
        exit_code=exit_code,
        external_writes=metadata.get("external_writes"),
        transcript_sha256=transcript_sha256,
        raw_sha256=parsed.raw_sha256,
        usage=parsed.usage,
        tool_event_count=parsed.tool_event_count,
        terminal_report_sha256=terminal_report_sha256,
        ignored_empty_agent_messages=parsed.ignored_empty_agent_messages,
        accepted=accepted,
        failure_class=failure_class,
        errors=errors,
        raw_object=parsed.raw_object,
        terminal_report=parsed.terminal_report,
    )


__all__ = [
    "OccurrenceAudit",
    "OccurrenceExpectation",
    "ParsedEvents",
    "ReleaseReviewState",
    "TaskAccountingState",
    "UsageEvidence",
    "audit_jsonl",
    "audit_occurrence",
    "extract_usage",
    "summarize_accounting",
    "validate_call_shape",
]
