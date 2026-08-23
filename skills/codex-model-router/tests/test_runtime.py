from __future__ import annotations

import copy
import dataclasses
import hashlib
import sys
import unittest
from typing import Any, Callable

sys.dont_write_bytecode = True

from support import SKILL_ROOT, load_fixture, load_module, sha256

contracts = load_module("cmr_contracts")
runtime = load_module("cmr_runtime")


PROMPT_SHA256 = "9" * 64
BASE_HEAD = "a" * 40
WORK_HEAD = "b" * 40
FINAL_HEAD = "c" * 40
EXPORT_HEAD = "d" * 40

CONTROL_CWDS = {
    phase: {
        "kind": "ephemeral",
        "path": f"/private/tmp/cmr-{phase}-001",
        "fresh": True,
        "destroyed": True,
        "read_only": True,
    }
    for phase in ("planning", "controller", "preflight")
}
WORKER_CWD = {
    "kind": "plan_worktree",
    "path": "/workspace/codex-model-router",
    "workspace_id": "cmr-v0.4.0-plan",
    "base_head": BASE_HEAD,
    "head": WORK_HEAD,
    "read_only": False,
}
REVIEW_CWD = {
    "kind": "plan_worktree",
    "path": "/workspace/codex-model-router",
    "workspace_id": "cmr-v0.4.0-plan",
    "base_head": WORK_HEAD,
    "head": WORK_HEAD,
    "read_only": True,
}


def release_scope_id(implementation_head: str, export_head: str) -> str:
    payload = (
        f"cmr-release-scope-v1\n{implementation_head}\n{export_head}\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("occurrence-events.json")
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def payload(self, name: str) -> bytes:
        return self.fixture["events"][name].encode("utf-8")

    def validate_controller(self, value: Any) -> list[str]:
        return contracts.validate_controller_result(value, self.ontologies)

    def validate_preflight(self, value: Any) -> list[str]:
        return contracts.validate_preflight_result(value, self.ontologies)

    def validate_planner(self, value: Any) -> list[str]:
        return contracts.validate_planner_result(value, ["T-001"], self.ontologies)

    def control_expectation(
        self,
        phase: str = "controller",
        *,
        forbidden_thread_ids: tuple[str, ...] = ("thread-used-before",),
    ) -> runtime.OccurrenceExpectation:
        routes = {
            "planning": ("gpt-5.6-sol", "max"),
            "controller": ("gpt-5.6-luna", "xhigh"),
            "preflight": ("gpt-5.6-luna", "max"),
        }
        if phase == "planning":
            scope_kind = "plan"
            scope_id = sha256(b"frozen-plan-v1\n")
            task_id = None
        else:
            scope_kind = "task"
            scope_id = "T-001"
            task_id = "T-001"
        model, effort = routes[phase]
        return runtime.OccurrenceExpectation(
            phase=phase,
            scope_kind=scope_kind,
            scope_id=scope_id,
            round_index=None,
            task_id=task_id,
            model=model,
            reasoning_effort=effort,
            prompt_sha256=PROMPT_SHA256,
            forbidden_thread_ids=forbidden_thread_ids,
            prior_thread_id=None,
            fork_turns="none",
            thread_policy="fresh",
            cwd_policy=CONTROL_CWDS[phase],
            write_policy="control_plane_no_write",
            response_policy="closed_json",
        )

    def sdd_expectation(
        self,
        *,
        phase: str = "worker",
        round_index: int | None = 0,
        initial_model: str = "gpt-5.6-luna",
        initial_effort: str = "xhigh",
        scope_kind: str = "task",
        scope_id: str = "T-001",
        task_id: str | None = "T-001",
        prior_thread_id: str | None = None,
        forbidden_thread_ids: tuple[str, ...] = (),
        cwd_policy: dict[str, Any] | None = None,
    ) -> runtime.OccurrenceExpectation:
        if cwd_policy is None:
            cwd_policy = WORKER_CWD if phase == "worker" else REVIEW_CWD
        return runtime.OccurrenceExpectation.for_sdd(
            phase=phase,
            scope_kind=scope_kind,
            scope_id=scope_id,
            round_index=round_index,
            task_id=task_id,
            initial_worker_model=initial_model,
            initial_worker_effort=initial_effort,
            prompt_sha256=PROMPT_SHA256,
            forbidden_thread_ids=forbidden_thread_ids,
            prior_thread_id=prior_thread_id,
            cwd_policy=cwd_policy,
        )

    def valid_metadata(
        self,
        payload: bytes,
        expected: runtime.OccurrenceExpectation,
        *,
        thread_id: str,
        transcript_kind: str | None = None,
        release_heads: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if transcript_kind is None:
            transcript_kind = (
                "closed" if expected.response_policy == "closed_json" else "sdd"
            )
        transcript_payload = self.fixture["transcripts"][transcript_kind].encode(
            "utf-8"
        )
        terminal_hash = None
        if expected.response_policy == "sdd_report":
            terminal_hash = sha256(self.fixture["terminal_report"].encode("utf-8"))
        metadata: dict[str, Any] = {
            "phase": expected.phase,
            "scope_kind": expected.scope_kind,
            "scope_id": expected.scope_id,
            "round_index": expected.round_index,
            "task_id": expected.task_id,
            "response_policy": expected.response_policy,
            "model": expected.model,
            "reasoning_effort": expected.reasoning_effort,
            "fork_turns": expected.fork_turns,
            "prompt_sha256": expected.prompt_sha256,
            "thread_policy": expected.thread_policy,
            "thread_id": thread_id,
            "cwd": copy.deepcopy(expected.cwd_policy),
            "write_policy": expected.write_policy,
            "exit_code": 0,
            "external_writes": False,
            "transcript_payload": transcript_payload,
            "transcript_sha256": sha256(transcript_payload),
            "raw_sha256": sha256(payload),
            "usage": copy.deepcopy(self.fixture["usage"]),
            "terminal_report_sha256": terminal_hash,
        }
        if expected.phase == "planning":
            metadata["plan_sha256"] = expected.scope_id
        if expected.scope_kind == "release":
            self.assertIsNotNone(release_heads)
            implementation_head, export_head = release_heads or ("", "")
            metadata["release_scope"] = {
                "implementation_head": implementation_head,
                "export_head": export_head,
            }
        return metadata

    def audit(
        self,
        fixture_name: str,
        expected: runtime.OccurrenceExpectation,
        validator: Callable[[Any], list[str]] | None,
        *,
        thread_id: str,
        release_heads: tuple[str, str] | None = None,
    ) -> runtime.OccurrenceAudit:
        payload = self.payload(fixture_name)
        return runtime.audit_occurrence(
            payload,
            self.valid_metadata(
                payload,
                expected,
                thread_id=thread_id,
                release_heads=release_heads,
            ),
            expected,
            validator,
        )

    def test_occurrence_expectation_is_frozen(self) -> None:
        expected = self.control_expectation()
        self.assertTrue(expected.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            expected.phase = "planning"

    def test_usage_evidence_requires_exact_five_nonnegative_integer_fields(self) -> None:
        value = {
            "input_tokens": 11,
            "cached_input_tokens": 7,
            "cache_write_input_tokens": 5,
            "output_tokens": 13,
            "reasoning_output_tokens": 3,
        }
        usage = runtime.UsageEvidence.from_value(value)
        self.assertEqual(usage.to_dict(), value)
        self.assertEqual(set(self.fixture["usage"]), set(value))

        for field in value:
            for invalid in (-1, True):
                with self.subTest(field=field, invalid=invalid):
                    malformed = dict(value)
                    malformed[field] = invalid
                    with self.assertRaises(ValueError):
                        runtime.UsageEvidence.from_value(malformed)

        missing = dict(value)
        missing.pop("reasoning_output_tokens")
        extra = dict(value, total_tokens=39)
        for malformed in (missing, extra):
            with self.subTest(keys=sorted(malformed)):
                with self.assertRaises(ValueError):
                    runtime.UsageEvidence.from_value(malformed)

    def test_whitespace_transport_message_is_ignored_only_with_one_valid_json(self) -> None:
        payload = self.payload("empty_then_controller_json")
        expected = self.control_expectation()
        audit = runtime.audit_occurrence(
            payload,
            self.valid_metadata(
                payload, expected, thread_id="thread-controller-001"
            ),
            expected,
            self.validate_controller,
        )
        self.assertTrue(audit.accepted)
        self.assertEqual(audit.ignored_empty_agent_messages, 1)
        self.assertEqual(
            audit.raw_object["schema_version"], "cmr-controller-result-v1"
        )
        self.assertIsNone(audit.terminal_report)

    def test_all_closed_json_phases_accept_one_bound_closed_object(self) -> None:
        cases = (
            ("planning", "planning_json", "thread-planning-001", self.validate_planner),
            (
                "controller",
                "controller_json",
                "thread-controller-001",
                self.validate_controller,
            ),
            (
                "preflight",
                "preflight_json",
                "thread-preflight-001",
                self.validate_preflight,
            ),
        )
        for phase, fixture_name, thread_id, validator in cases:
            with self.subTest(phase=phase):
                audit = self.audit(
                    fixture_name,
                    self.control_expectation(phase),
                    validator,
                    thread_id=thread_id,
                )
                self.assertTrue(audit.accepted, audit.errors)
                self.assertEqual(audit.tool_event_count, 0)
                self.assertIsNotNone(audit.raw_object)

    def test_closed_json_rejects_missing_extra_prose_fenced_tool_error_and_duplicates(self) -> None:
        expected = self.control_expectation()
        invalid_fixtures = (
            "controller_no_message",
            "controller_whitespace_only",
            "two_controller_messages",
            "prose_before_controller_json",
            "prose_after_controller_json",
            "second_json_object",
            "code_fenced_controller_json",
            "duplicate_key_controller_json",
            "controller_error",
            "controller_tool",
        )
        for fixture_name in invalid_fixtures:
            with self.subTest(fixture_name=fixture_name):
                audit = self.audit(
                    fixture_name,
                    expected,
                    self.validate_controller,
                    thread_id="thread-controller-001",
                )
                self.assertFalse(audit.accepted)
                self.assertEqual(audit.failure_class, "operational")
                self.assertTrue(audit.errors)

    def test_closed_json_requires_a_validator_and_sdd_forbids_one(self) -> None:
        controller = self.audit(
            "controller_json",
            self.control_expectation(),
            None,
            thread_id="thread-controller-001",
        )
        self.assertFalse(controller.accepted)
        self.assertEqual(controller.failure_class, "instrument")

        worker = self.audit(
            "sdd_worker_initial",
            self.sdd_expectation(),
            self.validate_controller,
            thread_id="thread-implementer-001",
        )
        self.assertFalse(worker.accepted)
        self.assertEqual(worker.failure_class, "instrument")

    def test_sdd_phases_accept_tools_progress_and_one_terminal_report(self) -> None:
        release_id = release_scope_id(FINAL_HEAD, EXPORT_HEAD)
        cases = (
            (
                "worker",
                self.sdd_expectation(),
                "sdd_worker_initial",
                "thread-implementer-001",
                None,
            ),
            (
                "task_review",
                self.sdd_expectation(phase="task_review", round_index=0),
                "sdd_reviewer",
                "thread-reviewer-001",
                None,
            ),
            (
                "re_review",
                self.sdd_expectation(
                    phase="re_review",
                    round_index=1,
                    prior_thread_id="thread-implementer-001",
                ),
                "sdd_reviewer",
                "thread-reviewer-001",
                None,
            ),
            (
                "final_review",
                self.sdd_expectation(
                    phase="final_review",
                    round_index=None,
                    initial_model="gpt-5.6-sol",
                    initial_effort="max",
                    scope_kind="release",
                    scope_id=release_id,
                    task_id=None,
                ),
                "sdd_reviewer",
                "thread-reviewer-001",
                (FINAL_HEAD, EXPORT_HEAD),
            ),
        )
        for phase, expected, fixture_name, thread_id, heads in cases:
            with self.subTest(phase=phase):
                audit = self.audit(
                    fixture_name,
                    expected,
                    None,
                    thread_id=thread_id,
                    release_heads=heads,
                )
                self.assertTrue(audit.accepted, audit.errors)
                self.assertEqual(audit.tool_event_count, 1)
                self.assertEqual(audit.terminal_report, self.fixture["terminal_report"])
                self.assertIsNone(audit.raw_object)

    def test_unknown_sdd_event_or_item_type_is_rejected_fail_closed(self) -> None:
        base_events = self.payload("sdd_worker_initial").decode("utf-8").splitlines()
        cases = (
            (
                '{"type":"mystery_event"}',
                "unsupported event type mystery_event",
            ),
            (
                '{"type":"item.completed","item":{"id":"item-unknown",'
                '"type":"mystery_tool"}}',
                "unsupported item type mystery_tool",
            ),
        )
        expected = self.sdd_expectation()
        for inserted_event, expected_error in cases:
            with self.subTest(event=inserted_event):
                payload = (
                    "\n".join(base_events[:-1] + [inserted_event, base_events[-1]])
                    + "\n"
                ).encode("utf-8")
                metadata = self.valid_metadata(
                    payload, expected, thread_id="thread-implementer-001"
                )
                audit = runtime.audit_occurrence(payload, metadata, expected)
                self.assertFalse(audit.accepted)
                self.assertEqual(audit.failure_class, "instrument")
                self.assertIn(expected_error, audit.errors)

    def test_sdd_report_rejects_error_absent_report_and_report_hash_drift(self) -> None:
        expected = self.sdd_expectation()
        for fixture_name in ("sdd_error", "sdd_no_report"):
            with self.subTest(fixture_name=fixture_name):
                payload = self.payload(fixture_name)
                metadata = self.valid_metadata(
                    payload, expected, thread_id="thread-implementer-001"
                )
                if fixture_name == "sdd_no_report":
                    metadata["terminal_report_sha256"] = None
                audit = runtime.audit_occurrence(payload, metadata, expected)
                self.assertFalse(audit.accepted)
                self.assertEqual(audit.failure_class, "operational")

        payload = self.payload("sdd_worker_initial")
        metadata = self.valid_metadata(
            payload, expected, thread_id="thread-implementer-001"
        )
        metadata["terminal_report_sha256"] = "0" * 64
        audit = runtime.audit_occurrence(payload, metadata, expected)
        self.assertFalse(audit.accepted)
        self.assertEqual(audit.failure_class, "instrument")
        self.assertIn("terminal_report_sha256", "\n".join(audit.errors))

    def test_response_policy_phase_matrix_is_closed(self) -> None:
        for phase in ("planning", "controller", "preflight"):
            with self.subTest(phase=phase, policy="sdd_report"):
                bad = dataclasses.replace(
                    self.control_expectation(phase), response_policy="sdd_report"
                )
                fixture_name = {
                    "planning": "planning_json",
                    "controller": "controller_json",
                    "preflight": "preflight_json",
                }[phase]
                thread_id = {
                    "planning": "thread-planning-001",
                    "controller": "thread-controller-001",
                    "preflight": "thread-preflight-001",
                }[phase]
                audit = self.audit(
                    fixture_name, bad, None, thread_id=thread_id
                )
                self.assertFalse(audit.accepted)
                self.assertEqual(audit.failure_class, "instrument")

        for phase, round_index in (
            ("worker", 0),
            ("task_review", 0),
            ("re_review", 1),
            ("final_review", None),
        ):
            with self.subTest(phase=phase, policy="closed_json"):
                if phase == "final_review":
                    scope_id = release_scope_id(FINAL_HEAD, EXPORT_HEAD)
                    expected = self.sdd_expectation(
                        phase=phase,
                        round_index=round_index,
                        initial_model="gpt-5.6-sol",
                        initial_effort="max",
                        scope_kind="release",
                        scope_id=scope_id,
                        task_id=None,
                    )
                    heads = (FINAL_HEAD, EXPORT_HEAD)
                else:
                    expected = self.sdd_expectation(
                        phase=phase,
                        round_index=round_index,
                        prior_thread_id=(
                            "thread-implementer-001" if phase == "re_review" else None
                        ),
                    )
                    heads = None
                bad = dataclasses.replace(expected, response_policy="closed_json")
                payload = self.payload("sdd_reviewer")
                metadata = self.valid_metadata(
                    payload,
                    bad,
                    thread_id="thread-reviewer-001",
                    release_heads=heads,
                )
                audit = runtime.audit_occurrence(
                    payload, metadata, bad, self.validate_controller
                )
                self.assertFalse(audit.accepted)
                self.assertEqual(audit.failure_class, "instrument")

    def test_metadata_bindings_reject_each_mutation(self) -> None:
        payload = self.payload("controller_json")
        expected = self.control_expectation()
        base = self.valid_metadata(
            payload, expected, thread_id="thread-controller-001"
        )
        mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("phase", lambda value: value.__setitem__("phase", "preflight")),
            ("scope_kind", lambda value: value.__setitem__("scope_kind", "plan")),
            ("scope_id", lambda value: value.__setitem__("scope_id", "T-002")),
            ("round_index", lambda value: value.__setitem__("round_index", 0)),
            ("task_id", lambda value: value.__setitem__("task_id", "T-002")),
            (
                "response_policy",
                lambda value: value.__setitem__("response_policy", "sdd_report"),
            ),
            ("model", lambda value: value.__setitem__("model", "gpt-5.6-sol")),
            ("reasoning_effort", lambda value: value.__setitem__("reasoning_effort", "max")),
            ("fork_turns", lambda value: value.__setitem__("fork_turns", "all")),
            ("prompt_sha256", lambda value: value.__setitem__("prompt_sha256", "0" * 64)),
            ("thread_policy", lambda value: value.__setitem__("thread_policy", "resume")),
            ("thread_id", lambda value: value.__setitem__("thread_id", "thread-other")),
            ("write_policy", lambda value: value.__setitem__("write_policy", "worktree_write")),
            ("exit_code", lambda value: value.__setitem__("exit_code", 1)),
            ("external_writes", lambda value: value.__setitem__("external_writes", True)),
            (
                "transcript_sha256",
                lambda value: value.__setitem__("transcript_sha256", "0" * 64),
            ),
            ("raw_sha256", lambda value: value.__setitem__("raw_sha256", "0" * 64)),
            (
                "usage",
                lambda value: value["usage"].__setitem__("output_tokens", 24),
            ),
            (
                "terminal_report_sha256",
                lambda value: value.__setitem__("terminal_report_sha256", "0" * 64),
            ),
        )
        scenarios = [
            ("closed_json", payload, expected, base, self.validate_controller),
        ]
        sdd_payload = self.payload("sdd_worker_initial")
        sdd_expected = self.sdd_expectation()
        sdd_base = self.valid_metadata(
            sdd_payload, sdd_expected, thread_id="thread-implementer-001"
        )
        scenarios.append(("sdd_report", sdd_payload, sdd_expected, sdd_base, None))

        for policy, scenario_payload, scenario_expected, scenario_base, validator in scenarios:
            for field, mutate in mutations:
                with self.subTest(policy=policy, field=field):
                    metadata = copy.deepcopy(scenario_base)
                    mutate(metadata)
                    if metadata.get(field) == scenario_base.get(field):
                        metadata[field] = {
                            "round_index": 1,
                            "response_policy": "closed_json",
                            "write_policy": "source_read_only",
                        }[field]
                    audit = runtime.audit_occurrence(
                        scenario_payload, metadata, scenario_expected, validator
                    )
                    self.assertFalse(audit.accepted)
                    self.assertIn(field, "\n".join(audit.errors))

    def test_thread_absence_fresh_reuse_and_unapproved_resume_are_rejected(self) -> None:
        payload = self.payload("controller_json")
        without_thread = b"\n".join(payload.splitlines()[1:]) + b"\n"
        expected = self.control_expectation()
        audit = runtime.audit_occurrence(
            without_thread,
            self.valid_metadata(
                without_thread, expected, thread_id="thread-controller-001"
            ),
            expected,
            self.validate_controller,
        )
        self.assertFalse(audit.accepted)
        self.assertIn("thread", "\n".join(audit.errors))

        sdd_payload = self.payload("sdd_worker_initial")
        sdd_without_thread = b"\n".join(sdd_payload.splitlines()[1:]) + b"\n"
        sdd_expected = self.sdd_expectation()
        sdd_audit = runtime.audit_occurrence(
            sdd_without_thread,
            self.valid_metadata(
                sdd_without_thread,
                sdd_expected,
                thread_id="thread-implementer-001",
            ),
            sdd_expected,
        )
        self.assertFalse(sdd_audit.accepted)
        self.assertIn("thread", "\n".join(sdd_audit.errors))

        reused = self.control_expectation(
            forbidden_thread_ids=("thread-controller-001",)
        )
        audit = self.audit(
            "controller_json",
            reused,
            self.validate_controller,
            thread_id="thread-controller-001",
        )
        self.assertFalse(audit.accepted)
        self.assertIn("reused", "\n".join(audit.errors))

        bad_resume = dataclasses.replace(
            self.sdd_expectation(
                round_index=1, prior_thread_id="thread-implementer-001"
            ),
            prior_thread_id="thread-other",
        )
        audit = self.audit(
            "sdd_worker_resume",
            bad_resume,
            None,
            thread_id="thread-implementer-001",
        )
        self.assertFalse(audit.accepted)
        self.assertIn("resume", "\n".join(audit.errors))

    def test_control_and_sdd_cwd_policies_are_closed(self) -> None:
        payload = self.payload("controller_json")
        expected = self.control_expectation()
        for field, bad_value in (
            ("kind", "plan_worktree"),
            ("path", "relative/path"),
            ("fresh", False),
            ("destroyed", False),
            ("read_only", False),
        ):
            with self.subTest(control_field=field):
                metadata = self.valid_metadata(
                    payload, expected, thread_id="thread-controller-001"
                )
                metadata["cwd"][field] = bad_value
                audit = runtime.audit_occurrence(
                    payload, metadata, expected, self.validate_controller
                )
                self.assertFalse(audit.accepted)
                self.assertIn("cwd", "\n".join(audit.errors))

        worker_payload = self.payload("sdd_worker_initial")
        worker = self.sdd_expectation()
        for field, bad_value in (
            ("kind", "ephemeral"),
            ("workspace_id", ""),
            ("base_head", "A" * 40),
            ("head", "short"),
            ("read_only", True),
        ):
            with self.subTest(worker_field=field):
                metadata = self.valid_metadata(
                    worker_payload, worker, thread_id="thread-implementer-001"
                )
                metadata["cwd"][field] = bad_value
                audit = runtime.audit_occurrence(worker_payload, metadata, worker)
                self.assertFalse(audit.accepted)
                self.assertIn("cwd", "\n".join(audit.errors))

        review_payload = self.payload("sdd_reviewer")
        review = self.sdd_expectation(phase="task_review", round_index=0)
        metadata = self.valid_metadata(
            review_payload, review, thread_id="thread-reviewer-001"
        )
        metadata["cwd"]["base_head"] = BASE_HEAD
        audit = runtime.audit_occurrence(review_payload, metadata, review)
        self.assertFalse(audit.accepted)
        self.assertIn("equal HEAD", "\n".join(audit.errors))

    def test_initial_fix_and_review_thread_write_policies_are_literal(self) -> None:
        initial = self.sdd_expectation()
        self.assertEqual(initial.round_index, 0)
        self.assertEqual(initial.thread_policy, "fresh")
        self.assertEqual(initial.write_policy, "worktree_write")
        initial_audit = self.audit(
            "sdd_worker_initial",
            initial,
            None,
            thread_id="thread-implementer-001",
        )
        self.assertTrue(initial_audit.accepted, initial_audit.errors)

        for round_index in (1, 2, 3):
            with self.subTest(round_index=round_index):
                resumed = self.sdd_expectation(
                    round_index=round_index,
                    prior_thread_id="thread-implementer-001",
                    forbidden_thread_ids=("thread-controller-001", "thread-reviewer-001"),
                )
                self.assertEqual(resumed.thread_policy, "resume")
                audit = self.audit(
                    "sdd_worker_resume",
                    resumed,
                    None,
                    thread_id="thread-implementer-001",
                )
                self.assertTrue(audit.accepted, audit.errors)

        round_four = self.sdd_expectation(
            round_index=4,
            prior_thread_id="thread-implementer-001",
            forbidden_thread_ids=("thread-implementer-001", "thread-reviewer-001"),
        )
        self.assertEqual(round_four.thread_policy, "fresh")
        audit = self.audit(
            "sdd_worker_fresh4",
            round_four,
            None,
            thread_id="thread-implementer-fix4",
        )
        self.assertTrue(audit.accepted, audit.errors)

        reviewer = self.sdd_expectation(
            phase="task_review",
            round_index=0,
            forbidden_thread_ids=("thread-implementer-001", "thread-controller-001"),
        )
        self.assertEqual(reviewer.thread_policy, "fresh")
        self.assertEqual(reviewer.write_policy, "source_read_only")
        self.assertTrue(reviewer.cwd_policy["read_only"])
        review_audit = self.audit(
            "sdd_reviewer",
            reviewer,
            None,
            thread_id="thread-reviewer-001",
        )
        self.assertTrue(review_audit.accepted, review_audit.errors)

    def test_fix_escalation_and_matching_re_review_routes_are_derived(self) -> None:
        round_four_routes = {
            ("gpt-5.6-luna", "xhigh"): ("gpt-5.6-luna", "max"),
            ("gpt-5.6-luna", "max"): ("gpt-5.6-sol", "max"),
            ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "max"),
            ("gpt-5.6-sol", "max"): ("gpt-5.6-sol", "max"),
        }
        review_routes = {
            ("gpt-5.6-luna", "xhigh"): ("gpt-5.6-luna", "max"),
            ("gpt-5.6-luna", "max"): ("gpt-5.6-luna", "max"),
            ("gpt-5.6-sol", "xhigh"): ("gpt-5.6-sol", "xhigh"),
            ("gpt-5.6-sol", "max"): ("gpt-5.6-sol", "max"),
        }
        for initial_route, round_four_route in round_four_routes.items():
            with self.subTest(initial_route=initial_route):
                model, effort = initial_route
                worker_four = self.sdd_expectation(
                    round_index=4,
                    initial_model=model,
                    initial_effort=effort,
                    prior_thread_id="thread-implementer-001",
                    forbidden_thread_ids=("thread-implementer-001",),
                )
                self.assertEqual(
                    (worker_four.model, worker_four.reasoning_effort),
                    round_four_route,
                )
                self.assertEqual(worker_four.thread_policy, "fresh")

                worker_five = self.sdd_expectation(
                    round_index=5,
                    initial_model=model,
                    initial_effort=effort,
                    prior_thread_id="thread-implementer-001",
                    forbidden_thread_ids=("thread-implementer-001",),
                )
                self.assertEqual(
                    (worker_five.model, worker_five.reasoning_effort),
                    ("gpt-5.6-sol", "max"),
                )

                re_review_four = self.sdd_expectation(
                    phase="re_review",
                    round_index=4,
                    initial_model=model,
                    initial_effort=effort,
                    prior_thread_id="thread-implementer-fix4",
                    forbidden_thread_ids=(
                        "thread-implementer-001",
                        "thread-implementer-fix4",
                    ),
                )
                self.assertEqual(
                    (re_review_four.model, re_review_four.reasoning_effort),
                    review_routes[round_four_route],
                )
                self.assertEqual(re_review_four.thread_policy, "fresh")

        escalated_review = self.sdd_expectation(
            phase="re_review",
            round_index=4,
            initial_model="gpt-5.6-luna",
            initial_effort="max",
            prior_thread_id="thread-implementer-fix4",
            forbidden_thread_ids=("thread-implementer-fix4",),
        )
        payload = self.payload("sdd_reviewer")
        metadata = self.valid_metadata(
            payload, escalated_review, thread_id="thread-reviewer-001"
        )
        metadata["model"] = "gpt-5.6-luna"
        audit = runtime.audit_occurrence(payload, metadata, escalated_review)
        self.assertFalse(audit.accepted)
        self.assertIn("model", "\n".join(audit.errors))

    def test_direct_expectation_rejects_unsupported_or_underpowered_sdd_routes(self) -> None:
        worker = self.sdd_expectation()
        reviewer = self.sdd_expectation(phase="task_review", round_index=0)
        worker_four = self.sdd_expectation(
            round_index=4, prior_thread_id="thread-implementer-001"
        )
        worker_five = self.sdd_expectation(
            round_index=5, prior_thread_id="thread-implementer-001"
        )
        sol_review_four = self.sdd_expectation(
            phase="re_review",
            round_index=4,
            initial_model="gpt-5.6-sol",
            initial_effort="xhigh",
            prior_thread_id="thread-implementer-fix4",
        )
        cases = (
            (
                dataclasses.replace(worker, model="gpt-5.6-terra"),
                "sdd_worker_initial",
                "thread-implementer-001",
            ),
            (
                dataclasses.replace(worker, reasoning_effort="high"),
                "sdd_worker_initial",
                "thread-implementer-001",
            ),
            (
                dataclasses.replace(
                    reviewer,
                    model="gpt-5.6-luna",
                    reasoning_effort="xhigh",
                ),
                "sdd_reviewer",
                "thread-reviewer-001",
            ),
            (
                dataclasses.replace(worker_four, reasoning_effort="xhigh"),
                "sdd_worker_fresh4",
                "thread-implementer-fix4",
            ),
            (
                dataclasses.replace(worker_five, reasoning_effort="xhigh"),
                "sdd_worker_fresh4",
                "thread-implementer-fix4",
            ),
            (
                dataclasses.replace(sol_review_four, reasoning_effort="xhigh"),
                "sdd_reviewer",
                "thread-reviewer-001",
            ),
        )
        for bad_expected, fixture_name, thread_id in cases:
            with self.subTest(route=(bad_expected.model, bad_expected.reasoning_effort)):
                payload = self.payload(fixture_name)
                metadata = self.valid_metadata(
                    payload, bad_expected, thread_id=thread_id
                )
                audit = runtime.audit_occurrence(payload, metadata, bad_expected)
                self.assertFalse(audit.accepted)
                self.assertEqual(audit.failure_class, "instrument")
                self.assertIn("route", "\n".join(audit.errors))

    def test_planning_and_release_scopes_bind_their_canonical_hashes(self) -> None:
        planning = self.control_expectation("planning")
        payload = self.payload("planning_json")
        metadata = self.valid_metadata(
            payload, planning, thread_id="thread-planning-001"
        )
        metadata["plan_sha256"] = "0" * 64
        audit = runtime.audit_occurrence(
            payload, metadata, planning, self.validate_planner
        )
        self.assertFalse(audit.accepted)
        self.assertIn("plan_sha256", "\n".join(audit.errors))

        initial_scope = release_scope_id(BASE_HEAD, EXPORT_HEAD)
        final_scope = release_scope_id(FINAL_HEAD, EXPORT_HEAD)
        release_worker = self.sdd_expectation(
            phase="worker",
            round_index=1,
            initial_model="gpt-5.6-luna",
            initial_effort="xhigh",
            scope_kind="release",
            scope_id=initial_scope,
            task_id=None,
            forbidden_thread_ids=("thread-implementer-001",),
        )
        self.assertEqual(
            (release_worker.model, release_worker.reasoning_effort),
            ("gpt-5.6-sol", "max"),
        )
        self.assertEqual(release_worker.thread_policy, "fresh")
        worker_audit = self.audit(
            "sdd_worker_fresh4",
            release_worker,
            None,
            thread_id="thread-implementer-fix4",
            release_heads=(BASE_HEAD, EXPORT_HEAD),
        )
        self.assertTrue(worker_audit.accepted, worker_audit.errors)

        release_review = self.sdd_expectation(
            phase="re_review",
            round_index=1,
            initial_model="gpt-5.6-luna",
            initial_effort="xhigh",
            scope_kind="release",
            scope_id=final_scope,
            task_id=None,
            prior_thread_id="thread-implementer-fix4",
            forbidden_thread_ids=("thread-implementer-fix4",),
        )
        self.assertEqual(
            (release_review.model, release_review.reasoning_effort),
            ("gpt-5.6-sol", "max"),
        )
        review_audit = self.audit(
            "sdd_reviewer",
            release_review,
            None,
            thread_id="thread-reviewer-001",
            release_heads=(FINAL_HEAD, EXPORT_HEAD),
        )
        self.assertTrue(review_audit.accepted, review_audit.errors)

        for fixture_name, expected, thread_id, heads in (
            (
                "sdd_worker_fresh4",
                release_worker,
                "thread-implementer-fix4",
                (BASE_HEAD, EXPORT_HEAD),
            ),
            (
                "sdd_reviewer",
                release_review,
                "thread-reviewer-001",
                (FINAL_HEAD, EXPORT_HEAD),
            ),
        ):
            with self.subTest(non_sol_release_phase=expected.phase):
                release_payload = self.payload(fixture_name)
                non_sol_metadata = self.valid_metadata(
                    release_payload,
                    expected,
                    thread_id=thread_id,
                    release_heads=heads,
                )
                non_sol_metadata["model"] = "gpt-5.6-luna"
                rejected_route = runtime.audit_occurrence(
                    release_payload, non_sol_metadata, expected
                )
                self.assertFalse(rejected_route.accepted)
                self.assertIn("model", "\n".join(rejected_route.errors))

        bad_metadata = self.valid_metadata(
            self.payload("sdd_reviewer"),
            release_review,
            thread_id="thread-reviewer-001",
            release_heads=(BASE_HEAD, EXPORT_HEAD),
        )
        rejected = runtime.audit_occurrence(
            self.payload("sdd_reviewer"), bad_metadata, release_review
        )
        self.assertFalse(rejected.accepted)
        self.assertIn("release scope", "\n".join(rejected.errors))

    def test_task_and_release_scope_substitution_is_rejected(self) -> None:
        task_expected = self.sdd_expectation()
        task_payload = self.payload("sdd_worker_initial")
        task_metadata = self.valid_metadata(
            task_payload, task_expected, thread_id="thread-implementer-001"
        )
        task_metadata["scope_kind"] = "release"
        task_metadata["task_id"] = None
        audit = runtime.audit_occurrence(task_payload, task_metadata, task_expected)
        self.assertFalse(audit.accepted)

        scope_id = release_scope_id(BASE_HEAD, EXPORT_HEAD)
        release_expected = self.sdd_expectation(
            phase="worker",
            round_index=1,
            scope_kind="release",
            scope_id=scope_id,
            task_id=None,
            initial_model="gpt-5.6-sol",
            initial_effort="max",
        )
        release_payload = self.payload("sdd_worker_fresh4")
        release_metadata = self.valid_metadata(
            release_payload,
            release_expected,
            thread_id="thread-implementer-fix4",
            release_heads=(BASE_HEAD, EXPORT_HEAD),
        )
        release_metadata["scope_kind"] = "task"
        release_metadata["task_id"] = "T-001"
        audit = runtime.audit_occurrence(
            release_payload, release_metadata, release_expected
        )
        self.assertFalse(audit.accepted)

    def test_boolean_round_index_is_never_an_integer_cardinality(self) -> None:
        with self.assertRaises(ValueError):
            self.sdd_expectation(phase="task_review", round_index=False)
        with self.assertRaises(ValueError):
            self.sdd_expectation(
                phase="worker",
                round_index=True,
                scope_kind="release",
                scope_id=release_scope_id(BASE_HEAD, EXPORT_HEAD),
                task_id=None,
                initial_model="gpt-5.6-sol",
                initial_effort="max",
            )

        payload = self.payload("sdd_reviewer")
        expected = self.sdd_expectation(phase="task_review", round_index=0)
        metadata = self.valid_metadata(
            payload, expected, thread_id="thread-reviewer-001"
        )
        metadata["round_index"] = False
        audit = runtime.audit_occurrence(payload, metadata, expected)
        self.assertFalse(audit.accepted)
        self.assertIn("round_index", "\n".join(audit.errors))

    def test_extract_usage_survives_other_rejections_and_malformed_usage_is_instrument(self) -> None:
        usage = runtime.extract_usage(self.payload("controller_error"))
        self.assertEqual(usage.to_dict(), self.fixture["usage"])

        with self.assertRaises(ValueError):
            runtime.extract_usage(self.payload("malformed_usage"))

        payload = self.payload("malformed_usage")
        expected = self.control_expectation()
        metadata = self.valid_metadata(
            payload, expected, thread_id="thread-controller-001"
        )
        audit = runtime.audit_occurrence(
            payload, metadata, expected, self.validate_controller
        )
        self.assertFalse(audit.accepted)
        self.assertEqual(audit.failure_class, "instrument")
        self.assertIn("usage", "\n".join(audit.errors))

    def test_boolean_metadata_usage_cannot_bind_integer_one_and_raw_usage_survives(self) -> None:
        payload = self.payload("controller_json").replace(
            b'"input_tokens":101', b'"input_tokens":1'
        )
        expected = self.control_expectation()
        metadata = self.valid_metadata(
            payload, expected, thread_id="thread-controller-001"
        )
        metadata["usage"] = {
            "input_tokens": True,
            "cached_input_tokens": 17,
            "cache_write_input_tokens": 5,
            "output_tokens": 23,
            "reasoning_output_tokens": 7,
        }
        audit = runtime.audit_occurrence(
            payload, metadata, expected, self.validate_controller
        )
        raw_usage = {
            "input_tokens": 1,
            "cached_input_tokens": 17,
            "cache_write_input_tokens": 5,
            "output_tokens": 23,
            "reasoning_output_tokens": 7,
        }
        self.assertFalse(audit.accepted)
        self.assertEqual(audit.failure_class, "instrument")
        self.assertIn("metadata usage", "\n".join(audit.errors))
        self.assertIsNotNone(audit.usage)
        self.assertEqual(audit.usage.to_dict(), raw_usage)
        self.assertEqual(audit.to_record()["usage"], raw_usage)

    def test_serialized_audit_has_exact_public_fields(self) -> None:
        audit = self.audit(
            "controller_json",
            self.control_expectation(),
            self.validate_controller,
            thread_id="thread-controller-001",
        )
        record = audit.to_record()
        self.assertEqual(
            set(record),
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
            },
        )
        self.assertTrue(record["accepted"])
        self.assertIsNone(record["failure_class"])
        self.assertEqual(record["errors"], [])
        self.assertEqual(record["usage"], self.fixture["usage"])

    def test_occurrence_from_record_is_strict_and_preserves_rejected_usage(self) -> None:
        self.assertTrue(
            hasattr(runtime.OccurrenceAudit, "from_record"),
            "OccurrenceAudit must expose the strict serialized-record boundary",
        )
        expected = self.control_expectation()
        accepted = self.audit(
            "controller_json",
            expected,
            self.validate_controller,
            thread_id="thread-controller-001",
        )
        accepted_round_trip = runtime.OccurrenceAudit.from_record(
            accepted.to_record(), label="accepted audit"
        )
        self.assertEqual(accepted_round_trip.to_record(), accepted.to_record())

        rejected = self.audit(
            "controller_tool",
            expected,
            self.validate_controller,
            thread_id="thread-controller-001",
        )
        self.assertFalse(rejected.accepted)
        rejected_round_trip = runtime.OccurrenceAudit.from_record(
            rejected.to_record(), label="rejected audit"
        )
        self.assertFalse(rejected_round_trip.accepted)
        self.assertEqual(rejected_round_trip.failure_class, "operational")
        self.assertEqual(rejected_round_trip.usage.to_dict(), self.fixture["usage"])

        invalid = accepted.to_record()
        invalid["unknown_field"] = True
        with self.assertRaisesRegex(ValueError, "unknown fields: unknown_field"):
            runtime.OccurrenceAudit.from_record(invalid, label="mutated audit")

        invalid_type = accepted.to_record()
        invalid_type["response_policy"] = []
        with self.assertRaisesRegex(
            ValueError,
            "response_policy must be closed_json or sdd_report",
        ):
            runtime.OccurrenceAudit.from_record(
                invalid_type,
                label="type-drift audit",
            )


class AccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("occurrence-events.json")
        cls.accounting = load_fixture("accounting-cases.json")
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def payload(self, name: str) -> bytes:
        return self.fixture["events"][name].encode("utf-8")

    def state(self, task_id: str, selector: str, rounds: tuple[int, ...]):
        return runtime.TaskAccountingState(
            task_id=task_id,
            selector=selector,
            dispatchable=selector != "block",
            completed_worker_rounds=rounds,
        )

    def states(self, *values: tuple[str, str, tuple[int, ...]]):
        return tuple(self.state(*value) for value in values)

    def release_review(self, *, fix_required: bool = True):
        value = self.accounting["release_review"]
        if not fix_required:
            value = dict(value)
            value["fix_required"] = False
            value["final_implementation_head"] = value[
                "initial_implementation_head"
            ]
            value["final_export_head"] = value["initial_export_head"]
        return runtime.ReleaseReviewState(**value)

    def _control_expectation(
        self, phase: str, task_id: str | None = None
    ) -> runtime.OccurrenceExpectation:
        routes = {
            "planning": ("gpt-5.6-sol", "max"),
            "controller": ("gpt-5.6-luna", "xhigh"),
            "preflight": ("gpt-5.6-luna", "max"),
        }
        if phase == "planning":
            scope_kind = "plan"
            scope_id = self.accounting["plan_sha256"]
            task_id = None
        else:
            assert task_id is not None
            scope_kind = "task"
            scope_id = task_id
        model, effort = routes[phase]
        return runtime.OccurrenceExpectation(
            phase=phase,
            scope_kind=scope_kind,
            scope_id=scope_id,
            round_index=None,
            task_id=task_id,
            model=model,
            reasoning_effort=effort,
            prompt_sha256=PROMPT_SHA256,
            forbidden_thread_ids=(),
            prior_thread_id=None,
            fork_turns="none",
            thread_policy="fresh",
            cwd_policy=copy.deepcopy(CONTROL_CWDS[phase]),
            write_policy="control_plane_no_write",
            response_policy="closed_json",
        )

    def _sdd_expectation(
        self,
        *,
        phase: str,
        scope_kind: str,
        scope_id: str,
        round_index: int | None,
        task_id: str | None,
        prior_thread_id: str | None = None,
    ) -> runtime.OccurrenceExpectation:
        if scope_kind == "release":
            initial_model, initial_effort = "gpt-5.6-sol", "max"
        else:
            initial_model, initial_effort = "gpt-5.6-luna", "xhigh"
        return runtime.OccurrenceExpectation.for_sdd(
            phase=phase,
            scope_kind=scope_kind,
            scope_id=scope_id,
            round_index=round_index,
            task_id=task_id,
            initial_worker_model=initial_model,
            initial_worker_effort=initial_effort,
            prompt_sha256=PROMPT_SHA256,
            prior_thread_id=prior_thread_id,
            cwd_policy=copy.deepcopy(WORKER_CWD if phase == "worker" else REVIEW_CWD),
        )

    def _audit(
        self,
        fixture_name: str,
        expected: runtime.OccurrenceExpectation,
        *,
        thread_id: str,
        release_heads: tuple[str, str] | None = None,
        validator: Callable[[Any], list[str]] | None = None,
    ) -> runtime.OccurrenceAudit:
        payload = self.payload(fixture_name)
        metadata = RuntimeTests.valid_metadata(
            self, payload, expected, thread_id=thread_id, release_heads=release_heads
        )
        return runtime.audit_occurrence(payload, metadata, expected, validator)

    def _occurrence_for_key(
        self,
        *,
        phase: str,
        scope_kind: str,
        scope_id: str,
        round_index: int | None,
        task_id: str | None,
        ordinal: int,
        release_review: Any | None,
    ) -> runtime.OccurrenceAudit:
        if phase in {"planning", "controller", "preflight"}:
            expected = self._control_expectation(
                phase, None if phase == "planning" else task_id
            )
            fixture = {
                "planning": "planning_json",
                "controller": "controller_json",
                "preflight": "preflight_json",
            }[phase]
            if phase == "planning":
                validator = lambda value: contracts.validate_planner_result(
                    value, ["T-001"], self.ontologies
                )
            elif phase == "controller":
                validator = lambda value: contracts.validate_controller_result(
                    value, self.ontologies
                )
            else:
                validator = lambda value: contracts.validate_preflight_result(
                    value, self.ontologies
                )
            return self._audit(
                fixture,
                expected,
                thread_id={
                    "planning": "thread-planning-001",
                    "controller": "thread-controller-001",
                    "preflight": "thread-preflight-001",
                }[phase],
                validator=validator,
            )
        assert release_review is not None or scope_kind == "task"
        if phase == "worker":
            fixture = (
                "sdd_worker_initial"
                if scope_kind == "task" and round_index in (0, 1, 2, 3)
                else "sdd_worker_fresh4"
            )
            prior = (
                "thread-implementer-001"
                if round_index in (1, 2, 3)
                else None
            )
            expected = self._sdd_expectation(
                phase=phase,
                scope_kind=scope_kind,
                scope_id=scope_id,
                round_index=round_index,
                task_id=task_id,
                prior_thread_id=prior,
            )
            thread_id = (
                "thread-implementer-001"
                if scope_kind == "task" and round_index in (0, 1, 2, 3)
                else "thread-implementer-fix4"
            )
            heads = None
            if scope_kind == "release":
                heads = (
                    release_review.initial_implementation_head,
                    release_review.initial_export_head,
                )
            return self._audit(
                fixture, expected, thread_id=thread_id, release_heads=heads
            )
        expected = self._sdd_expectation(
            phase=phase,
            scope_kind=scope_kind,
            scope_id=scope_id,
            round_index=round_index,
            task_id=task_id,
            prior_thread_id=(
                "thread-implementer-001"
                if phase == "re_review" and scope_kind == "task"
                and round_index in (1, 2, 3)
                else "thread-implementer-fix4"
                if phase == "re_review"
                else None
            ),
        )
        heads = None
        if scope_kind == "release":
            if phase == "final_review":
                heads = (
                    release_review.initial_implementation_head,
                    release_review.initial_export_head,
                )
            else:
                heads = (
                    release_review.final_implementation_head,
                    release_review.final_export_head,
                )
        return self._audit(
            "sdd_reviewer",
            expected,
            thread_id="thread-reviewer-001",
            release_heads=heads,
        )

    def canonical_occurrences(self, states, release_review):
        occurrences = []
        ordinal = 0
        plan = self.accounting["plan_sha256"]

        def add(**key):
            nonlocal ordinal
            occurrences.append(
                self._occurrence_for_key(
                    ordinal=ordinal, release_review=release_review, **key
                )
            )
            ordinal += 1

        add(
            phase="planning",
            scope_kind="plan",
            scope_id=plan,
            round_index=None,
            task_id=None,
        )
        for state in states:
            add(
                phase="controller",
                scope_kind="task",
                scope_id=state.task_id,
                round_index=None,
                task_id=state.task_id,
            )
        for state in states:
            if state.selector == "run":
                add(
                    phase="preflight",
                    scope_kind="task",
                    scope_id=state.task_id,
                    round_index=None,
                    task_id=state.task_id,
                )
        for state in states:
            if not state.dispatchable:
                continue
            for round_index in state.completed_worker_rounds:
                add(
                    phase="worker",
                    scope_kind="task",
                    scope_id=state.task_id,
                    round_index=round_index,
                    task_id=state.task_id,
                )
            add(
                phase="task_review",
                scope_kind="task",
                scope_id=state.task_id,
                round_index=0,
                task_id=state.task_id,
            )
            for round_index in state.completed_worker_rounds:
                if round_index > 0:
                    add(
                        phase="re_review",
                        scope_kind="task",
                        scope_id=state.task_id,
                        round_index=round_index,
                        task_id=state.task_id,
                    )
        if release_review is not None:
            add(
                phase="final_review",
                scope_kind="release",
                scope_id=release_review.initial_scope_id,
                round_index=None,
                task_id=None,
            )
            if release_review.fix_required:
                add(
                    phase="worker",
                    scope_kind="release",
                    scope_id=release_review.initial_scope_id,
                    round_index=1,
                    task_id=None,
                )
                add(
                    phase="re_review",
                    scope_kind="release",
                    scope_id=release_review.final_scope_id,
                    round_index=1,
                    task_id=None,
                )
        return occurrences

    def test_accounting_states_are_frozen_and_release_scope_ids_are_derived(self) -> None:
        state = self.state("T-001", "run", (0, 1, 2))
        self.assertTrue(dataclasses.is_dataclass(state))
        self.assertTrue(state.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            state.task_id = "T-002"

        release = self.release_review()
        self.assertTrue(release.__dataclass_params__.frozen)
        self.assertEqual(
            release.initial_scope_id,
            release_scope_id(
                release.initial_implementation_head, release.initial_export_head
            ),
        )
        self.assertEqual(
            release.final_scope_id,
            release_scope_id(release.final_implementation_head, release.final_export_head),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            release.fix_required = False
        with self.assertRaises(TypeError):
            runtime.ReleaseReviewState(
                initial_implementation_head=release.initial_implementation_head,
                initial_export_head=release.initial_export_head,
                initial_scope_id=release.initial_scope_id,
                fix_required=True,
                final_implementation_head=release.final_implementation_head,
                final_export_head=release.final_export_head,
                final_scope_id=release.final_scope_id,
            )

    def test_release_fix_requires_a_changed_final_head_pair(self) -> None:
        value = dict(self.accounting["release_review"])
        value["final_implementation_head"] = value["initial_implementation_head"]
        value["final_export_head"] = value["initial_export_head"]
        with self.assertRaisesRegex(ValueError, "fix-required release"):
            runtime.ReleaseReviewState(**value)

        no_fix = runtime.ReleaseReviewState(**dict(value, fix_required=False))
        self.assertEqual(
            (no_fix.initial_implementation_head, no_fix.initial_export_head),
            (no_fix.final_implementation_head, no_fix.final_export_head),
        )
        runtime.ReleaseReviewState(
            **dict(value, final_implementation_head="b" * 40)
        )
        runtime.ReleaseReviewState(**dict(value, final_export_head="e" * 40))
        for invalid in (1, 0, None, "true"):
            with self.subTest(fix_required=invalid):
                with self.assertRaises(ValueError):
                    runtime.ReleaseReviewState(**dict(value, fix_required=invalid))

    def test_summarize_counts_all_phases_usage_and_fix_rounds(self) -> None:
        states = self.states(
            ("T-001", "run", (0, 1)),
            ("T-002", "skip", (0,)),
            ("T-003", "block", ()),
        )
        release = self.release_review()
        occurrences = self.canonical_occurrences(states, release)
        summary = runtime.summarize_accounting(
            occurrences,
            states,
            {"T-001": {"passed": True}, "T-002": {"passed": True}},
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertEqual(summary["task_count"], 3)
        self.assertEqual(summary["selector_counts"], {"run": 1, "skip": 1, "block": 1})
        self.assertEqual(
            summary["phase_counts"],
            {
                "planning": 1,
                "controller": 3,
                "preflight": 1,
                "worker": 4,
                "task_review": 2,
                "re_review": 2,
                "final_review": 1,
            },
        )
        self.assertEqual(summary["expected_phase_counts"], summary["phase_counts"])
        self.assertEqual(summary["total_calls"], len(occurrences))
        self.assertEqual(summary["transport_retry_count"], 0)
        self.assertEqual(summary["sdd_fix_rounds"], 2)
        self.assertTrue(summary["canonical_call_shape"])
        self.assertEqual(
            summary["usage"],
            {
                key: self.fixture["usage"][key] * len(occurrences)
                for key in self.fixture["usage"]
            },
        )
        self.assertEqual(summary["operational_failures"], [])
        self.assertEqual(summary["semantic_failures"], [])
        self.assertEqual(summary["instrument_failures"], [])

    def test_wrong_phase_distribution_is_noncanonical_even_with_same_total(self) -> None:
        states = self.states(("T-001", "run", (0,)), ("T-002", "skip", (0,)))
        occurrences = self.canonical_occurrences(states, None)
        old = ("preflight", "task", "T-001", None)
        for index, occurrence in enumerate(occurrences):
            key = (
                occurrence.phase,
                occurrence.scope_kind,
                occurrence.scope_id,
                occurrence.round_index,
            )
            if key == old:
                occurrences[index] = dataclasses.replace(
                    occurrence,
                    phase="worker",
                    scope_kind="task",
                    scope_id="T-001",
                    round_index=2,
                    task_id="T-001",
                )
                break
        errors = runtime.validate_call_shape(
            occurrences,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertIn("missing preflight key for T-001", errors)

    def test_same_total_with_two_controllers_for_one_task_is_noncanonical(self) -> None:
        states = self.states(("T-A", "skip", (0,)), ("T-B", "skip", (0,)))
        occurrences = self.canonical_occurrences(states, None)
        for index, occurrence in enumerate(occurrences):
            if (
                occurrence.phase,
                occurrence.scope_kind,
                occurrence.scope_id,
                occurrence.round_index,
            ) == ("controller", "task", "T-B", None):
                occurrences[index] = dataclasses.replace(
                    occurrence, scope_id="T-A", task_id="T-A"
                )
                break
        errors = runtime.validate_call_shape(
            occurrences,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertIn("controller key for T-A has 2 attempts", errors)
        self.assertIn("missing controller key for T-B", errors)

    def test_rejected_usage_is_counted_and_failure_categories_do_not_overlap(self) -> None:
        states = self.states(("T-001", "skip", (0,)))
        canonical = self.canonical_occurrences(states, None)
        rejected_index = next(
            index
            for index, occurrence in enumerate(canonical)
            if occurrence.phase == "controller"
        )
        expected = self._control_expectation("controller", "T-001")
        payload = self.payload("controller_error")
        rejected = self._audit(
            "controller_error",
            expected,
            thread_id="thread-controller-001",
            validator=lambda value: [],
        )
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.failure_class, "operational")
        canonical[rejected_index] = rejected
        summary = runtime.summarize_accounting(
            canonical,
            states,
            {},
            ("ledger-001",),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertEqual(summary["total_calls"], len(canonical))
        self.assertEqual(
            summary["usage"],
            {
                key: self.fixture["usage"][key] * len(canonical)
                for key in self.fixture["usage"]
            },
        )
        self.assertEqual(len(summary["operational_failures"]), 1)
        self.assertEqual(summary["semantic_failures"], [])
        self.assertIn("ledger-001", summary["instrument_failures"])
        self.assertFalse(summary["canonical_call_shape"])

    def test_task_and_release_rounds_are_not_transport_retries(self) -> None:
        states = self.states(("T-001", "skip", (0, 1, 2)))
        release = self.release_review()
        summary = runtime.summarize_accounting(
            self.canonical_occurrences(states, release),
            states,
            {},
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertEqual(summary["sdd_fix_rounds"], 3)
        self.assertEqual(summary["transport_retry_count"], 0)

    def test_release_scope_and_fix_wave_keys_are_derived_not_caller_supplied(self) -> None:
        states = self.states(("T-001", "skip", (0,)))
        release = self.release_review(fix_required=False)
        occurrences = self.canonical_occurrences(states, release)
        final_review = next(
            occurrence for occurrence in occurrences if occurrence.phase == "final_review"
        )
        self.assertEqual(final_review.scope_id, release.initial_scope_id)
        self.assertFalse(any(occurrence.scope_kind == "release" and occurrence.phase == "worker" for occurrence in occurrences))
        self.assertFalse(any(occurrence.scope_kind == "release" and occurrence.phase == "re_review" for occurrence in occurrences))

        wrong = list(occurrences)
        wrong.append(
            self._occurrence_for_key(
                phase="worker",
                scope_kind="release",
                scope_id=release.initial_scope_id,
                round_index=1,
                task_id=None,
                ordinal=99,
                release_review=release,
            )
        )
        errors = runtime.validate_call_shape(
            wrong,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertIn("unexpected worker key", " ".join(errors))

    def test_accounting_summary_has_exact_closed_fields_and_schema_parity(self) -> None:
        expected = {
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
        }
        schema = contracts.expected_published_schemas(
            contracts.load_ontologies(SKILL_ROOT)
        )["cmr-accounting-summary-v1.schema.json"]
        self.assertEqual(set(schema["required"]), expected)
        self.assertFalse(schema["additionalProperties"])

    def test_invalid_usage_shape_is_instrument_and_never_synthesized(self) -> None:
        states = self.states(("T-001", "skip", (0,)))
        release = None
        base = self.canonical_occurrences(states, release)
        usage_fields = tuple(self.fixture["usage"])
        for field_name in usage_fields:
            with self.subTest(field=field_name):
                for invalid in (-1, True):
                    mutated = list(base)
                    index = next(
                        index
                        for index, occurrence in enumerate(mutated)
                        if occurrence.phase == "worker"
                    )
                    usage = dict(self.fixture["usage"])
                    usage[field_name] = invalid
                    mutated[index] = dataclasses.replace(mutated[index], usage=usage)
                    summary = runtime.summarize_accounting(
                        mutated,
                        states,
                        {},
                        (),
                        plan_sha256=self.accounting["plan_sha256"],
                        release_review=release,
                    )
                    self.assertFalse(summary["canonical_call_shape"])
                    self.assertEqual(
                        summary["usage"][field_name],
                        self.fixture["usage"][field_name] * (len(mutated) - 1),
                    )
                    self.assertTrue(summary["instrument_failures"])

        for malformed in (
            {key: value for key, value in self.fixture["usage"].items() if key != "input_tokens"},
            dict(self.fixture["usage"], unknown_tokens=1),
            None,
        ):
            with self.subTest(malformed=malformed):
                mutated = list(base)
                index = next(
                    index
                    for index, occurrence in enumerate(mutated)
                    if occurrence.phase == "worker"
                )
                mutated[index] = dataclasses.replace(mutated[index], usage=malformed)
                summary = runtime.summarize_accounting(
                    mutated,
                    states,
                    {},
                    (),
                    plan_sha256=self.accounting["plan_sha256"],
                    release_review=release,
                )
                self.assertFalse(summary["canonical_call_shape"])
                self.assertTrue(summary["instrument_failures"])

    def test_control_retry_is_counted_but_sdd_duplicate_is_not(self) -> None:
        states = self.states(("T-001", "skip", (0, 1)))
        release = None
        base = self.canonical_occurrences(states, release)
        planning = next(occurrence for occurrence in base if occurrence.phase == "planning")
        with_control_retry = base + [planning]
        control_summary = runtime.summarize_accounting(
            with_control_retry,
            states,
            {},
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertEqual(control_summary["transport_retry_count"], 1)
        self.assertFalse(control_summary["canonical_call_shape"])

        worker = next(occurrence for occurrence in base if occurrence.phase == "worker")
        with_sdd_duplicate = base + [worker]
        sdd_summary = runtime.summarize_accounting(
            with_sdd_duplicate,
            states,
            {},
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertEqual(sdd_summary["transport_retry_count"], 0)
        self.assertFalse(sdd_summary["canonical_call_shape"])

    def test_wrong_plan_release_pair_round_and_skipped_preflight_are_identity_errors(self) -> None:
        states = self.states(("T-001", "run", (0, 1)), ("T-002", "skip", (0,)))
        release = self.release_review()
        base = self.canonical_occurrences(states, release)
        self.assertTrue(
            runtime.validate_call_shape(
                base,
                states,
                plan_sha256=self.accounting["plan_sha256"],
                release_review=release,
            )
            == []
        )

        wrong_plan = runtime.validate_call_shape(
            base,
            states,
            plan_sha256="0" * 64,
            release_review=release,
        )
        self.assertIn("missing planning key", " ".join(wrong_plan))

        wrong_release = list(base)
        final_index = next(
            index
            for index, occurrence in enumerate(wrong_release)
            if occurrence.phase == "final_review"
        )
        wrong_release[final_index] = dataclasses.replace(
            wrong_release[final_index],
            scope_id=release_scope_id(
                release.initial_implementation_head, "e" * 40
            ),
        )
        wrong_release_errors = runtime.validate_call_shape(
            wrong_release,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertIn("missing final_review key", " ".join(wrong_release_errors))

        skipped_preflight = list(base)
        skipped_preflight.append(
            self._occurrence_for_key(
                phase="preflight",
                scope_kind="task",
                scope_id="T-002",
                round_index=None,
                task_id="T-002",
                ordinal=100,
                release_review=release,
            )
        )
        skipped_errors = runtime.validate_call_shape(
            skipped_preflight,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertIn("unexpected preflight key for T-002", skipped_errors)

        wrong_round = list(base)
        worker_index = next(
            index
            for index, occurrence in enumerate(wrong_round)
            if occurrence.phase == "worker"
            and occurrence.scope_kind == "task"
            and occurrence.scope_id == "T-001"
            and occurrence.round_index == 0
        )
        wrong_round[worker_index] = dataclasses.replace(
            wrong_round[worker_index], round_index=2
        )
        wrong_round_errors = runtime.validate_call_shape(
            wrong_round,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertIn("missing worker key for T-001 round 0", wrong_round_errors)
        self.assertIn("unexpected worker key for T-001 round 2", wrong_round_errors)

        wrong_review = list(base)
        review_index = next(
            index
            for index, occurrence in enumerate(wrong_review)
            if occurrence.phase == "re_review"
            and occurrence.scope_kind == "task"
            and occurrence.scope_id == "T-001"
        )
        wrong_review[review_index] = dataclasses.replace(
            wrong_review[review_index], round_index=2
        )
        wrong_review_errors = runtime.validate_call_shape(
            wrong_review,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release,
        )
        self.assertIn("missing re_review key for T-001 round 1", wrong_review_errors)

    def test_final_fix_wave_presence_is_derived_from_release_state(self) -> None:
        states = self.states(("T-001", "skip", (0,)))
        release_with_fix = self.release_review(fix_required=True)
        canonical = self.canonical_occurrences(states, release_with_fix)
        without_fix_wave = [
            occurrence
            for occurrence in canonical
            if not (
                occurrence.scope_kind == "release"
                and occurrence.phase in {"worker", "re_review"}
            )
        ]
        missing_errors = runtime.validate_call_shape(
            without_fix_wave,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release_with_fix,
        )
        self.assertIn("missing worker key for", " ".join(missing_errors))
        self.assertIn("missing re_review key for", " ".join(missing_errors))

        release_without_fix = self.release_review(fix_required=False)
        extra_fix_wave = self.canonical_occurrences(states, release_without_fix)
        extra_fix_wave.extend(
            occurrence
            for occurrence in canonical
            if occurrence.scope_kind == "release"
            and occurrence.phase in {"worker", "re_review"}
        )
        extra_errors = runtime.validate_call_shape(
            extra_fix_wave,
            states,
            plan_sha256=self.accounting["plan_sha256"],
            release_review=release_without_fix,
        )
        self.assertIn("unexpected worker key for", " ".join(extra_errors))
        self.assertIn("unexpected re_review key for", " ".join(extra_errors))

    def test_semantic_failure_is_only_recorded_for_clean_accepted_occurrences(self) -> None:
        states = self.states(("T-001", "skip", (0,)))
        occurrences = self.canonical_occurrences(states, None)
        clean = runtime.summarize_accounting(
            occurrences,
            states,
            {"T-001": {"passed": False}},
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertEqual(clean["semantic_failures"], ["T-001"])
        self.assertEqual(clean["operational_failures"], [])

        rejected_index = next(
            index
            for index, occurrence in enumerate(occurrences)
            if occurrence.phase == "worker"
        )
        occurrences[rejected_index] = dataclasses.replace(
            occurrences[rejected_index],
            accepted=False,
            failure_class="operational",
            errors=("semantic payload was not accepted",),
        )
        rejected_result = runtime.summarize_accounting(
            occurrences,
            states,
            {
                "T-001": {
                    "accepted": False,
                    "passed": False,
                }
            },
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertEqual(rejected_result["semantic_failures"], [])
        self.assertEqual(len(rejected_result["operational_failures"]), 1)
        self.assertNotIn(
            rejected_result["operational_failures"][0],
            rejected_result["instrument_failures"],
        )

    def test_semantic_unknown_and_duplicate_bindings_are_operational(self) -> None:
        states = self.states(("T-001", "skip", (0,)))
        summary = runtime.summarize_accounting(
            self.canonical_occurrences(states, None),
            states,
            [
                {"task_id": "UNKNOWN", "passed": False},
                {"task_id": "T-001", "passed": False},
                {"task_id": "T-001", "passed": False},
            ],
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertEqual(summary["semantic_failures"], [])
        self.assertEqual(summary["operational_failures"], ["T-001", "UNKNOWN"])
        self.assertTrue(summary["canonical_call_shape"])

        malformed_duplicate = runtime.summarize_accounting(
            self.canonical_occurrences(states, None),
            states,
            [
                {"task_id": "T-001", "passed": False},
                {"task_id": "T-001", "unrecognized": True},
            ],
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertEqual(malformed_duplicate["semantic_failures"], [])
        self.assertTrue(malformed_duplicate["instrument_failures"])

    def test_semantic_rejected_and_non_clean_bindings_are_operational(self) -> None:
        states = self.states(("T-001", "skip", (0,)))
        base = self.canonical_occurrences(states, None)
        rejected_id = "worker:task:T-001:0"
        rejected = [
            dataclasses.replace(
                occurrence,
                accepted=False,
                failure_class="operational",
                errors=("transport rejected",),
            )
            if occurrence.phase == "worker"
            else occurrence
            for occurrence in base
        ]
        rejected_summary = runtime.summarize_accounting(
            rejected,
            states,
            [{"task_id": rejected_id, "passed": False}],
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertNotIn(rejected_id, rejected_summary["semantic_failures"])
        self.assertIn(rejected_id, rejected_summary["operational_failures"])

        rejected_task_summary = runtime.summarize_accounting(
            rejected,
            states,
            {"T-001": {"passed": False}},
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertNotIn("T-001", rejected_task_summary["semantic_failures"])
        self.assertIn("T-001", rejected_task_summary["operational_failures"])

        non_clean_id = "task_review:task:T-001:0"
        non_clean = [
            dataclasses.replace(
                occurrence,
                accepted=True,
                failure_class="instrument",
                errors=("binding drift",),
            )
            if occurrence.phase == "task_review"
            else occurrence
            for occurrence in base
        ]
        non_clean_summary = runtime.summarize_accounting(
            non_clean,
            states,
            [{"task_id": non_clean_id, "passed": False}],
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertNotIn(non_clean_id, non_clean_summary["semantic_failures"])
        self.assertIn(non_clean_id, non_clean_summary["operational_failures"])

        malformed_summary = runtime.summarize_accounting(
            base,
            states,
            [{"task_id": "T-001"}],
            (),
            plan_sha256=self.accounting["plan_sha256"],
            release_review=None,
        )
        self.assertEqual(malformed_summary["semantic_failures"], [])
        self.assertTrue(malformed_summary["instrument_failures"])

    def test_call_cardinality_scope_kind_tie_break_is_deterministic(self) -> None:
        release = self.release_review()
        states = self.states((release.initial_scope_id, "skip", (0, 1)))
        occurrences = self.canonical_occurrences(states, release)

        def summary(values):
            return runtime.summarize_accounting(
                values,
                states,
                {},
                (),
                plan_sha256=self.accounting["plan_sha256"],
                release_review=release,
            )

        forward = summary(occurrences)["call_cardinality"]
        reverse = summary(list(reversed(occurrences)))["call_cardinality"]
        self.assertEqual(forward, reverse)
        tied = [
            entry
            for entry in forward
            if entry["phase"] == "worker"
            and entry["scope_id"] == release.initial_scope_id
            and entry["round_index"] == 1
        ]
        self.assertEqual([entry["scope_kind"] for entry in tied], ["release", "task"])


if __name__ == "__main__":
    unittest.main()
