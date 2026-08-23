from __future__ import annotations

import copy
import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

from support import (
    SKILL_ROOT,
    copy_references,
    load_fixture,
    load_module,
    sha256,
    write_json,
)

compiler = load_module("cmr_compiler")
contracts = load_module("cmr_contracts")
runtime_module = load_module("cmr_runtime")


TASK_ID = "T-002"
THREAD_ID = "thread-controller-task-2"
TRANSCRIPT = b"cmr transcript v1\ncontroller task 2\n"
USAGE = {
    "input_tokens": 101,
    "cached_input_tokens": 17,
    "cache_write_input_tokens": 5,
    "output_tokens": 23,
    "reasoning_output_tokens": 7,
}
CONTROLLER_CWD = {
    "kind": "ephemeral",
    "path": "/private/tmp/cmr-controller-task-2",
    "fresh": True,
    "destroyed": True,
    "read_only": True,
}


class CompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("compiler-cases.json")
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def controller_result(
        self,
        fact_ids: list[str] | None = None,
        *,
        uncertainty_ids: list[str] | None = None,
        action_intents: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "cmr-controller-result-v1",
            "fact_ids": list(fact_ids or []),
            "uncertainty_ids": list(uncertainty_ids or []),
            "action_intents": copy.deepcopy(action_intents or []),
        }

    def controller_occurrence(
        self,
        result: Mapping[str, Any],
        brief: Any,
        *,
        task_id: str = TASK_ID,
    ) -> tuple[bytes, Any]:
        response = json.dumps(
            dict(result), ensure_ascii=False, separators=(",", ":")
        )
        events = [
            {"type": "thread.started", "thread_id": THREAD_ID},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "item-response",
                    "type": "agent_message",
                    "text": response,
                },
            },
            {"type": "turn.completed", "usage": copy.deepcopy(USAGE)},
        ]
        payload = (
            "\n".join(json.dumps(item, separators=(",", ":")) for item in events)
            + "\n"
        ).encode("utf-8")
        expected = runtime_module.OccurrenceExpectation(
            phase="controller",
            scope_kind="task",
            scope_id=task_id,
            round_index=None,
            task_id=task_id,
            model="gpt-5.6-luna",
            reasoning_effort="xhigh",
            prompt_sha256=brief.sha256,
            forbidden_thread_ids=(),
            prior_thread_id=None,
            fork_turns="none",
            thread_policy="fresh",
            cwd_policy=CONTROLLER_CWD,
            write_policy="control_plane_no_write",
            response_policy="closed_json",
        )
        metadata = {
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
            "thread_id": THREAD_ID,
            "cwd": copy.deepcopy(CONTROLLER_CWD),
            "write_policy": expected.write_policy,
            "exit_code": 0,
            "external_writes": False,
            "transcript_payload": TRANSCRIPT,
            "transcript_sha256": sha256(TRANSCRIPT),
            "raw_sha256": sha256(payload),
            "usage": copy.deepcopy(USAGE),
            "terminal_report_sha256": None,
        }
        occurrence = runtime_module.audit_occurrence(
            payload,
            metadata,
            expected,
            lambda value: contracts.validate_controller_result(value, self.ontologies),
        )
        self.assertTrue(occurrence.accepted, occurrence.errors)
        return payload, occurrence

    def bound_evidence(
        self,
        fact_ids: list[str] | None = None,
        *,
        uncertainty_ids: list[str] | None = None,
        action_intents: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        result = self.controller_result(
            fact_ids,
            uncertainty_ids=uncertainty_ids,
            action_intents=action_intents,
        )
        brief_payload = b"Task T-002 approved implementation brief\n"
        brief = compiler.ArtifactRef(
            path="briefs/T-002.md",
            sha256=sha256(brief_payload),
            size_bytes=len(brief_payload),
        )
        payload, occurrence = self.controller_occurrence(result, brief)
        raw = compiler.ArtifactRef(
            path="controller/T-002.raw.jsonl",
            sha256=sha256(payload),
            size_bytes=len(payload),
        )
        return compiler.bind_controller_result(
            result,
            task_id=TASK_ID,
            brief=brief,
            raw=raw,
            occurrence=occurrence,
        )

    def runtime_input(
        self,
        *,
        task_phase: str = "implementation",
        current_defect_id: str | None = None,
        failure_history: list[dict[str, str]] | None = None,
        review_scope: str = "task",
        entries: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "cmr-runtime-input-v1",
            "task_phase": task_phase,
            "current_defect_id": current_defect_id,
            "failure_history": copy.deepcopy(failure_history or []),
            "review_scope": review_scope,
            "authorization_registry": {
                "schema_version": "cmr-authorization-registry-v1",
                "entries": copy.deepcopy(entries or []),
            },
        }

    def compile_case(self, case_id: str) -> dict[str, Any]:
        case = self.fixture["route_cases"][case_id]
        return compiler.compile_route(
            self.bound_evidence(case["fact_ids"]),
            self.runtime_input(),
            self.ontologies,
        )

    def assert_compilation_blocker(
        self,
        blocker_id: str,
        evidence: Any,
        runtime: Any,
    ) -> None:
        with self.assertRaises(compiler.CompilationError) as captured:
            compiler.compile_route(evidence, runtime, self.ontologies)
        self.assertEqual(captured.exception.blocker_id, blocker_id)
        self.assertEqual(captured.exception.blocker_ids, (blocker_id,))

    def test_artifact_reference_is_frozen_and_rejects_noncanonical_values(self) -> None:
        ref = compiler.ArtifactRef("briefs/T-002.md", "a" * 64, 9)
        self.assertEqual(
            ref.to_dict(),
            {"path": "briefs/T-002.md", "sha256": "a" * 64, "size_bytes": 9},
        )
        self.assertTrue(ref.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ref.path = "other"

        invalid = (
            ("", "a" * 64, 0),
            ("/absolute", "a" * 64, 0),
            ("C:/drive", "a" * 64, 0),
            ("a//b", "a" * 64, 0),
            ("a/./b", "a" * 64, 0),
            ("a/../b", "a" * 64, 0),
            ("a\\b", "a" * 64, 0),
            ("a/\u0001b", "a" * 64, 0),
            ("ok", "A" * 64, 0),
            ("ok", "a" * 63, 0),
            ("ok", "a" * 64, -1),
            ("ok", "a" * 64, True),
        )
        for path, digest, size in invalid:
            with self.subTest(path=path, digest=digest, size=size):
                with self.assertRaises(ValueError):
                    compiler.ArtifactRef(path, digest, size)

    def test_binding_creates_closed_route_evidence_from_the_audited_occurrence(self) -> None:
        evidence = self.bound_evidence(
            ["audit", "low_blast_radius"],
            uncertainty_ids=["controller_uncertainty"],
            action_intents=[{"action": "push", "target": "owner/repo:refs/heads/main"}],
        )
        self.assertEqual(
            list(evidence),
            [
                "schema_version",
                "task_id",
                "brief",
                "fact_ids",
                "uncertainty_ids",
                "action_intents",
                "raw",
                "occurrence",
            ],
        )
        self.assertEqual(evidence["schema_version"], "cmr-route-evidence-v2")
        self.assertEqual(evidence["task_id"], TASK_ID)
        self.assertEqual(evidence["occurrence"]["task_id"], TASK_ID)
        self.assertEqual(evidence["occurrence"]["scope_id"], TASK_ID)
        self.assertEqual(
            evidence["occurrence"]["prompt_sha256"], evidence["brief"]["sha256"]
        )
        self.assertEqual(
            evidence["occurrence"]["raw_sha256"], evidence["raw"]["sha256"]
        )
        self.assertEqual(evidence["occurrence"]["usage"], USAGE)
        self.assertEqual(compiler.validate_route_evidence(evidence, self.ontologies), [])

    def test_binding_rejects_controller_occurrence_and_artifact_mutations(self) -> None:
        result = self.controller_result()
        brief_payload = b"Task T-002 approved implementation brief\n"
        brief = compiler.ArtifactRef(
            "briefs/T-002.md", sha256(brief_payload), len(brief_payload)
        )
        payload, accepted = self.controller_occurrence(result, brief)
        raw = compiler.ArtifactRef(
            "controller/T-002.raw.jsonl", sha256(payload), len(payload)
        )

        occurrence_mutations = {
            "accepted": {"accepted": False},
            "phase": {"phase": "preflight"},
            "scope": {"scope_id": "T-OTHER"},
            "task": {"task_id": "T-OTHER"},
            "round": {"round_index": 0},
            "response": {"response_policy": "sdd_report"},
            "model": {"model": "gpt-5.6-sol"},
            "effort": {"reasoning_effort": "max"},
            "fork": {"fork_turns": "all"},
            "thread": {"thread_id": None},
            "thread policy": {"thread_policy": "resume"},
            "cwd": {"cwd": {**CONTROLLER_CWD, "fresh": False}},
            "write": {"write_policy": "worktree_write"},
            "exit": {"exit_code": 1},
            "external": {"external_writes": True},
            "transcript": {"transcript_sha256": None},
            "usage": {"usage": None},
            "errors": {"errors": ("bad",)},
            "failure class": {"failure_class": "instrument"},
            "terminal": {"terminal_report": "not closed json"},
            "raw object": {"raw_object": self.controller_result(["architecture"])},
        }
        for label, changes in occurrence_mutations.items():
            with self.subTest(label=label):
                occurrence = dataclasses.replace(accepted, **changes)
                with self.assertRaises(compiler.CompilationError) as captured:
                    compiler.bind_controller_result(
                        result,
                        task_id=TASK_ID,
                        brief=brief,
                        raw=raw,
                        occurrence=occurrence,
                    )
                self.assertEqual(captured.exception.blocker_id, "occurrence_invalid")

        binding_mutations = (
            ("wrong raw", brief, compiler.ArtifactRef("raw", "f" * 64, len(payload))),
            (
                "wrong prompt",
                compiler.ArtifactRef("brief", "e" * 64, len(brief_payload)),
                raw,
            ),
        )
        for label, changed_brief, changed_raw in binding_mutations:
            with self.subTest(label=label), self.assertRaises(
                compiler.CompilationError
            ) as captured:
                compiler.bind_controller_result(
                    result,
                    task_id=TASK_ID,
                    brief=changed_brief,
                    raw=changed_raw,
                    occurrence=accepted,
                )
            self.assertEqual(captured.exception.blocker_id, "artifact_binding_invalid")

    def test_binding_rejects_invalid_controller_semantics_before_audit_binding(self) -> None:
        brief = compiler.ArtifactRef("brief", "a" * 64, 1)
        raw = compiler.ArtifactRef("raw", "b" * 64, 1)
        malformed_results = (
            self.controller_result(["canonical_planning"]),
            self.controller_result(["audit", "architecture"]),
            self.controller_result(
                action_intents=[{"action": "push", "target": "owner/repo:main"}]
            ),
            {**self.controller_result(), "selected_model": "gpt-5.6-sol"},
        )
        for result in malformed_results:
            with self.subTest(result=result), self.assertRaises(
                compiler.CompilationError
            ) as captured:
                compiler.bind_controller_result(
                    result,
                    task_id=TASK_ID,
                    brief=brief,
                    raw=raw,
                    occurrence=None,
                )
            self.assertEqual(captured.exception.blocker_id, "controller_result_invalid")

    def test_route_matrix_is_derived_not_model_authored(self) -> None:
        expected = {
            "default": ("gpt-5.6-luna", "xhigh", "gpt-5.6-luna", "max"),
            "audit": ("gpt-5.6-luna", "max", "gpt-5.6-luna", "max"),
            "integration": ("gpt-5.6-sol", "xhigh", "gpt-5.6-sol", "xhigh"),
            "hard_gate": ("gpt-5.6-sol", "max", "gpt-5.6-sol", "max"),
        }
        for case_id, want in expected.items():
            decision = self.compile_case(case_id)
            got = (
                decision["worker"]["model"],
                decision["worker"]["reasoning_effort"],
                decision["review"]["model"],
                decision["review"]["reasoning_effort"],
            )
            self.assertEqual(got, want, case_id)

    def test_every_literal_route_case_has_absolute_review_and_stable_reason(self) -> None:
        for case_id, case in self.fixture["route_cases"].items():
            with self.subTest(case_id=case_id):
                decision = self.compile_case(case_id)
                self.assertEqual(
                    decision["worker"],
                    {"model": case["worker"][0], "reasoning_effort": case["worker"][1]},
                )
                self.assertEqual(
                    decision["review"],
                    {"model": case["review"][0], "reasoning_effort": case["review"][1]},
                )
                self.assertEqual(decision["role"], case["role"])
                self.assertEqual(decision["risk_level"], case["risk_level"])
                self.assertEqual(
                    decision["decisive_reason_id"], case["decisive_reason_id"]
                )
                self.assertEqual(
                    decision["justification"],
                    f'{case["decisive_reason_id"]} requires '
                    f'{case["worker"][0]}/{case["worker"][1]}; task review is '
                    f'{case["review"][0]}/{case["review"][1]}.',
                )

    def test_every_hard_gate_fact_selects_sol_max_and_ontology_projections(self) -> None:
        fact_entries = {entry["id"]: dict(entry) for entry in self.ontologies.fact_entries}
        for fact_id in self.fixture["hard_gate_facts"]:
            with self.subTest(fact_id=fact_id):
                decision = compiler.compile_route(
                    self.bound_evidence([fact_id]),
                    self.runtime_input(),
                    self.ontologies,
                )
                entry = fact_entries[fact_id]
                self.assertEqual(
                    decision["worker"],
                    {"model": "gpt-5.6-sol", "reasoning_effort": "max"},
                )
                self.assertEqual(decision["risk_signals"], [fact_id])
                self.assertEqual(decision["hard_gates"], [fact_id])
                self.assertEqual(decision["derived_flags"], entry["derived_flags"])
                self.assertEqual(decision["risk_level"], entry["risk_projection"])

    def test_decision_is_closed_ordered_and_uses_only_fresh_sdd_execution(self) -> None:
        evidence = self.bound_evidence(
            ["architecture", "authentication"],
            uncertainty_ids=["multiple_policy_facts"],
        )
        decision = compiler.compile_route(evidence, self.runtime_input(), self.ontologies)
        self.assertEqual(
            list(decision),
            [
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
            ],
        )
        self.assertEqual(decision["schema_version"], "cmr-route-decision-v1")
        self.assertEqual(decision["task_id"], TASK_ID)
        self.assertEqual(decision["brief"], evidence["brief"])
        self.assertEqual(decision["fact_ids"], ["architecture", "authentication"])
        self.assertEqual(decision["risk_signals"], ["architecture", "authentication"])
        self.assertEqual(decision["hard_gates"], ["architecture", "authentication"])
        self.assertEqual(decision["derived_flags"], ["architecture_change", "security"])
        self.assertEqual(
            decision["execution"],
            {
                "fork_turns": "none",
                "context_mode": "fresh",
                "execution_engine": "superpowers-sdd",
            },
        )
        self.assertEqual(decision["status"], "ready")
        self.assertEqual(decision["blocker_ids"], [])

    def test_controller_cannot_author_runtime_or_compiler_owned_fields(self) -> None:
        forbidden = (
            "worker",
            "review",
            "risk_level",
            "role",
            "execution",
            "fork_turns",
            "status",
            "lifecycle",
            "task_phase",
            "failure_history",
            "review_scope",
            "authorization_registry",
            "justification",
        )
        base = self.controller_result()
        for field in forbidden:
            with self.subTest(field=field):
                value = dict(base)
                value[field] = "model-authored"
                self.assertIn(
                    f"unknown field {field}",
                    "\n".join(
                        contracts.validate_controller_result(value, self.ontologies)
                    ),
                )

        for fake_fact in (
            "canonical_planning",
            "branch_final_review",
            "recurring_failure",
            "regression_after_declared_resolved",
        ):
            with self.subTest(fake_fact=fake_fact):
                value = self.controller_result([fake_fact])
                self.assertIn(
                    f"controller result.fact_ids: unknown ID {fake_fact}",
                    contracts.validate_controller_result(value, self.ontologies),
                )

    def test_runtime_branch_final_review_is_absolute_and_planning_is_invalid(self) -> None:
        decision = compiler.compile_route(
            self.bound_evidence(["audit", "low_blast_radius"]),
            self.runtime_input(task_phase="final_review", review_scope="branch"),
            self.ontologies,
        )
        self.assertEqual(decision["role"], "final_review")
        self.assertEqual(decision["worker"], {"model": "gpt-5.6-sol", "reasoning_effort": "max"})
        self.assertEqual(decision["review"], {"model": "gpt-5.6-sol", "reasoning_effort": "max"})
        self.assertEqual(decision["decisive_reason_id"], "branch_final_review")

        for invalid_runtime in (
            self.runtime_input(task_phase="planning"),
            self.runtime_input(task_phase="final_review", review_scope="task"),
        ):
            with self.subTest(runtime=invalid_runtime):
                self.assert_compilation_blocker(
                    "runtime_input_invalid", self.bound_evidence(), invalid_runtime
                )

    def test_one_failure_does_not_escalate_but_second_same_defect_does(self) -> None:
        one = [{"defect_id": " Defect-A ", "event": "attempt_completed_failed"}]
        one_decision = compiler.compile_route(
            self.bound_evidence(),
            self.runtime_input(current_defect_id="defect a", failure_history=one),
            self.ontologies,
        )
        self.assertEqual(
            one_decision["recurrence"],
            {"active": False, "reason_id": None, "defect_id": None},
        )
        self.assertEqual(one_decision["worker"]["model"], "gpt-5.6-luna")

        two = one + [
            {"defect_id": "defect a", "event": "attempt_completed_failed"}
        ]
        two_decision = compiler.compile_route(
            self.bound_evidence(),
            self.runtime_input(current_defect_id="DEFECT--A", failure_history=two),
            self.ontologies,
        )
        self.assertEqual(
            two_decision["recurrence"],
            {
                "active": True,
                "reason_id": "recurring_failure",
                "defect_id": "defect_a",
            },
        )
        self.assertEqual(
            two_decision["worker"],
            {"model": "gpt-5.6-sol", "reasoning_effort": "max"},
        )
        self.assertEqual(two_decision["decisive_reason_id"], "recurring_failure")

    def test_distinct_defects_do_not_combine_into_recurrence(self) -> None:
        history = [
            {"defect_id": "defect-a", "event": "attempt_completed_failed"},
            {"defect_id": "defect-b", "event": "attempt_completed_failed"},
        ]
        recurrence = compiler.derive_recurrence(history, "defect-a")
        self.assertEqual(
            recurrence.to_dict(),
            {"active": False, "reason_id": None, "defect_id": None},
        )

    def test_reappearance_after_declared_resolution_escalates_immediately(self) -> None:
        history = [
            {"defect_id": "Defect A", "event": "attempt_completed_passed"},
            {"defect_id": "defect-a", "event": "declared_resolved"},
            {"defect_id": "DEFECT--A", "event": "reappeared"},
        ]
        decision = compiler.compile_route(
            self.bound_evidence(["cross_module_integration"]),
            self.runtime_input(current_defect_id=" defect a ", failure_history=history),
            self.ontologies,
        )
        self.assertEqual(
            decision["recurrence"],
            {
                "active": True,
                "reason_id": "regression_after_declared_resolved",
                "defect_id": "defect_a",
            },
        )
        self.assertEqual(decision["worker"], {"model": "gpt-5.6-sol", "reasoning_effort": "max"})
        self.assertEqual(
            decision["decisive_reason_id"], "regression_after_declared_resolved"
        )

    def test_history_is_closed_ordered_complete_and_fail_closed(self) -> None:
        invalid_histories = (
            [{"defect_id": "d", "event": "unknown"}],
            [{"event": "attempt_completed_failed"}],
            [{"defect_id": "d", "event": "attempt_completed_failed", "extra": True}],
            [{"defect_id": "   ", "event": "attempt_completed_failed"}],
            [{"defect_id": "d", "event": "declared_resolved"}],
            [{"defect_id": "d", "event": "reappeared"}],
            [
                {"defect_id": "d", "event": "attempt_completed_passed"},
                {"defect_id": "d", "event": "attempt_completed_failed"},
            ],
            [
                {"defect_id": "d", "event": "attempt_completed_failed"},
                {"defect_id": "d", "event": "declared_resolved"},
            ],
            "not-an-array",
        )
        for history in invalid_histories:
            with self.subTest(history=history):
                runtime = self.runtime_input(current_defect_id="d")
                runtime["failure_history"] = history
                self.assert_compilation_blocker(
                    "runtime_input_invalid", self.bound_evidence(), runtime
                )

        no_current = self.runtime_input(
            failure_history=[
                {"defect_id": "d", "event": "attempt_completed_failed"}
            ]
        )
        self.assert_compilation_blocker(
            "runtime_input_invalid", self.bound_evidence(), no_current
        )

    def test_recurrence_cannot_be_removed_by_semantic_envelope_changes(self) -> None:
        history = [
            {"defect_id": "d", "event": "attempt_completed_failed"},
            {"defect_id": "d", "event": "attempt_completed_failed"},
        ]
        runtime = self.runtime_input(current_defect_id="d", failure_history=history)
        for facts in ([], ["clear_isolated_low_risk"], ["audit", "low_blast_radius"]):
            with self.subTest(facts=facts):
                decision = compiler.compile_route(
                    self.bound_evidence(facts), runtime, self.ontologies
                )
                self.assertTrue(decision["recurrence"]["active"])
                self.assertEqual(
                    decision["worker"],
                    {"model": "gpt-5.6-sol", "reasoning_effort": "max"},
                )

    def test_all_external_action_target_forms_compile_only_exact_registry_pairs(self) -> None:
        for index, (action, target) in enumerate(
            self.fixture["valid_action_targets"].items(), start=1
        ):
            with self.subTest(action=action):
                entry = {
                    "action": action,
                    "target": target,
                    "authorization_id": f"auth-{index}",
                    "preview_sha256": f"{index:064x}",
                }
                decision = compiler.compile_route(
                    self.bound_evidence(action_intents=[{"action": action, "target": target}]),
                    self.runtime_input(entries=[entry]),
                    self.ontologies,
                )
                self.assertEqual(
                    decision["external_actions"],
                    [
                        {
                            "action": action,
                            "target": target,
                            "state": "ready",
                            "authorized": True,
                            "preview": True,
                            "readback": False,
                            "blocker_ids": [],
                            "authorization_id": f"auth-{index}",
                            "preview_sha256": f"{index:064x}",
                        }
                    ],
                )

    def test_external_action_lifecycle_is_action_local_and_evidence_exact(self) -> None:
        intents = [
            {"action": "repository_create", "target": "owner/repo"},
            {"action": "issue_create", "target": "owner/repo"},
            {"action": "push", "target": "owner/repo:refs/heads/main"},
            {"action": "release_create", "target": "owner/repo:v0.4.0"},
        ]
        registry = [
            {
                "action": "repository_create",
                "target": "owner/repo",
                "authorization_id": "auth-repository",
                "preview_sha256": "1" * 64,
                "readback_id": "readback-repository",
            },
            {
                "action": "issue_create",
                "target": "owner/repo",
                "authorization_id": "auth-issue",
                "preview_sha256": "2" * 64,
            },
            {
                "action": "push",
                "target": "owner/other:refs/heads/main",
                "authorization_id": "auth-wrong-ref",
                "preview_sha256": "3" * 64,
            },
        ]
        decision = compiler.compile_route(
            self.bound_evidence(action_intents=intents),
            self.runtime_input(entries=registry),
            self.ontologies,
        )
        self.assertEqual(decision["worker"], {"model": "gpt-5.6-luna", "reasoning_effort": "xhigh"})
        self.assertEqual(decision["status"], "ready")
        self.assertEqual(
            [item["state"] for item in decision["external_actions"]],
            ["completed", "ready", "blocked", "blocked"],
        )
        completed, ready, blocked_push, blocked_release = decision["external_actions"]
        self.assertTrue(completed["authorized"] and completed["preview"] and completed["readback"])
        self.assertEqual(completed["readback_id"], "readback-repository")
        self.assertNotIn("readback_id", ready)
        self.assertEqual(
            {key for key in blocked_push if key.endswith("_id") or key.endswith("sha256")},
            set(),
        )
        self.assertEqual(blocked_push["blocker_ids"], [])
        self.assertEqual(blocked_release["blocker_ids"], [])

    def test_authorization_never_propagates_between_actions_or_targets(self) -> None:
        cases = (
            (
                {"action": "issue_create", "target": "owner/repo"},
                {"action": "push", "target": "owner/repo:refs/heads/main"},
            ),
            (
                {"action": "repository_create", "target": "owner/repo"},
                {"action": "repository_settings_update", "target": "owner/repo"},
            ),
            (
                {"action": "repository_create", "target": "owner/repo"},
                {"action": "repository_topics_update", "target": "owner/repo"},
            ),
            (
                {"action": "push", "target": "owner/repo:refs/tags/v0.4.0"},
                {"action": "tag_create", "target": "owner/repo:refs/tags/v0.4.0"},
            ),
            (
                {"action": "push", "target": "owner/repo:refs/tags/v0.4.0"},
                {"action": "release_create", "target": "owner/repo:v0.4.0"},
            ),
            (
                {"action": "install_plugin", "target": "/opt/cmr"},
                {"action": "marketplace_publish", "target": "community/cmr"},
            ),
        )
        for authorized, requested in cases:
            with self.subTest(authorized=authorized, requested=requested):
                registry = [{
                    **authorized,
                    "authorization_id": "auth-one-action-only",
                    "preview_sha256": "a" * 64,
                }]
                decision = compiler.compile_route(
                    self.bound_evidence(action_intents=[requested]),
                    self.runtime_input(entries=registry),
                    self.ontologies,
                )
                self.assertEqual(decision["external_actions"][0]["state"], "blocked")
                self.assertFalse(decision["external_actions"][0]["authorized"])

    def test_missing_action_target_is_local_blocker_without_evidence_identifiers(self) -> None:
        decision = compiler.compile_route(
            self.bound_evidence(action_intents=[{"action": "push"}]),
            self.runtime_input(),
            self.ontologies,
        )
        self.assertEqual(
            decision["external_actions"],
            [
                {
                    "action": "push",
                    "state": "blocked",
                    "authorized": False,
                    "preview": False,
                    "readback": False,
                    "blocker_ids": ["external_target_missing"],
                }
            ],
        )
        self.assertEqual(decision["status"], "ready")
        self.assertEqual(decision["blocker_ids"], [])
        self.assertEqual(decision["worker"], {"model": "gpt-5.6-luna", "reasoning_effort": "xhigh"})

    def test_malformed_or_duplicate_authorization_registry_fails_closed(self) -> None:
        valid = {
            "action": "push",
            "target": "owner/repo:refs/heads/main",
            "authorization_id": "auth-push",
            "preview_sha256": "a" * 64,
        }
        invalid_entries = (
            [{**valid, "extra": True}],
            [{**valid, "authorization_id": ""}],
            [{**valid, "preview_sha256": "A" * 64}],
            [{**valid, "readback_id": ""}],
            [{**valid, "target": "owner/repo:main"}],
            [valid, {**valid, "authorization_id": "other"}],
        )
        for entries in invalid_entries:
            with self.subTest(entries=entries):
                self.assert_compilation_blocker(
                    "runtime_input_invalid",
                    self.bound_evidence(
                        action_intents=[
                            {"action": "push", "target": "owner/repo:refs/heads/main"}
                        ]
                    ),
                    self.runtime_input(entries=entries),
                )

    def test_route_and_runtime_validation_reject_unknown_fields_and_bad_types(self) -> None:
        evidence = self.bound_evidence()
        runtime = self.runtime_input()
        evidence_extra = dict(evidence, selected_model="gpt-5.6-sol")
        runtime_extra = dict(runtime, final_review=True)
        self.assert_compilation_blocker(
            "controller_result_invalid", evidence_extra, runtime
        )
        self.assert_compilation_blocker(
            "runtime_input_invalid", evidence, runtime_extra
        )
        self.assertEqual(
            compiler.validate_route_evidence(evidence, self.ontologies), []
        )
        self.assertEqual(
            compiler.validate_runtime_input(runtime, self.ontologies), []
        )

    def test_json_discriminants_fail_closed_without_type_errors(self) -> None:
        malformed_discriminants = ([], {}, False)

        for malformed in malformed_discriminants:
            with self.subTest(field="occurrence.phase", malformed=malformed):
                evidence = self.bound_evidence()
                evidence["occurrence"]["phase"] = copy.deepcopy(malformed)
                try:
                    errors = compiler.validate_route_evidence(
                        evidence, self.ontologies
                    )
                except TypeError as exc:
                    self.fail(f"route evidence validator leaked TypeError: {exc}")
                self.assertIn("occurrence.phase", "\n".join(errors))
                self.assert_compilation_blocker(
                    "occurrence_invalid", evidence, self.runtime_input()
                )

        runtime_mutations = (
            (
                "task_phase",
                lambda value, malformed: value.__setitem__(
                    "task_phase", copy.deepcopy(malformed)
                ),
            ),
            (
                "review_scope",
                lambda value, malformed: value.__setitem__(
                    "review_scope", copy.deepcopy(malformed)
                ),
            ),
            (
                "failure_history.event",
                lambda value, malformed: value.update(
                    {
                        "current_defect_id": "d",
                        "failure_history": [
                            {
                                "defect_id": "d",
                                "event": copy.deepcopy(malformed),
                            }
                        ],
                    }
                ),
            ),
            (
                "authorization_registry.action",
                lambda value, malformed: value["authorization_registry"][
                    "entries"
                ].append(
                    {
                        "action": copy.deepcopy(malformed),
                        "target": "owner/repo",
                        "authorization_id": "auth-malformed-action",
                        "preview_sha256": "a" * 64,
                    }
                ),
            ),
        )
        for field, mutate in runtime_mutations:
            for malformed in malformed_discriminants:
                with self.subTest(field=field, malformed=malformed):
                    runtime = self.runtime_input()
                    mutate(runtime, malformed)
                    try:
                        errors = compiler.validate_runtime_input(
                            runtime, self.ontologies
                        )
                    except TypeError as exc:
                        self.fail(f"runtime validator leaked TypeError: {exc}")
                    self.assertTrue(errors)
                    self.assert_compilation_blocker(
                        "runtime_input_invalid", self.bound_evidence(), runtime
                    )

        runtime = self.runtime_input(
            current_defect_id="d",
            failure_history=[{"defect_id": "d", "event": "placeholder"}],
        )
        runtime["task_phase"] = []
        runtime["review_scope"] = {}
        runtime["failure_history"][0]["event"] = False
        errors = compiler.validate_runtime_input(runtime, self.ontologies)
        joined = "\n".join(errors)
        self.assertIn("runtime input.task_phase", joined)
        self.assertIn("runtime input.review_scope", joined)
        self.assertIn("runtime input.failure_history[0].event", joined)

    def test_persisted_evidence_revalidates_semantics_occurrence_and_artifacts(self) -> None:
        evidence = self.bound_evidence()
        runtime = self.runtime_input()
        mutations = (
            (
                "controller_result_invalid",
                lambda value: value["fact_ids"].append("canonical_planning"),
            ),
            (
                "occurrence_invalid",
                lambda value: value["occurrence"].__setitem__(
                    "reasoning_effort", "max"
                ),
            ),
            (
                "artifact_binding_invalid",
                lambda value: value["raw"].__setitem__("sha256", "f" * 64),
            ),
            (
                "artifact_binding_invalid",
                lambda value: value["brief"].__setitem__("path", "../brief"),
            ),
        )
        for blocker_id, mutate in mutations:
            with self.subTest(blocker_id=blocker_id):
                changed = copy.deepcopy(evidence)
                mutate(changed)
                self.assert_compilation_blocker(blocker_id, changed, runtime)

    def test_route_evidence_occurrence_is_controller_luna_xhigh_only(self) -> None:
        mutations = (
            ("preflight-max", "preflight", "max"),
            ("controller-max", "controller", "max"),
            ("preflight-xhigh", "preflight", "xhigh"),
        )
        for label, phase, effort in mutations:
            with self.subTest(label=label):
                evidence = self.bound_evidence()
                evidence["occurrence"]["phase"] = phase
                evidence["occurrence"]["reasoning_effort"] = effort
                errors = compiler.validate_route_evidence(
                    evidence, self.ontologies
                )
                self.assertTrue(errors)
                self.assert_compilation_blocker(
                    "occurrence_invalid", evidence, self.runtime_input()
                )

        schema = contracts.expected_published_schemas(self.ontologies)[
            "cmr-route-evidence-v2.schema.json"
        ]
        occurrence = schema["$defs"]["OccurrenceEvidence"]["properties"]
        self.assertEqual(occurrence["phase"], {"const": "controller"})
        self.assertEqual(occurrence["model"], {"const": "gpt-5.6-luna"})
        self.assertEqual(occurrence["reasoning_effort"], {"const": "xhigh"})

    def test_decisive_reason_tie_break_is_literal(self) -> None:
        history = [
            {"defect_id": "d", "event": "attempt_completed_failed"},
            {"defect_id": "d", "event": "attempt_completed_failed"},
            {"defect_id": "d", "event": "attempt_completed_passed"},
            {"defect_id": "d", "event": "declared_resolved"},
            {"defect_id": "d", "event": "reappeared"},
        ]
        decision = compiler.compile_route(
            self.bound_evidence(
                [
                    "architecture",
                    "audit",
                    "cross_module_integration",
                    "low_blast_radius",
                    "schema_migration",
                ]
            ),
            self.runtime_input(
                task_phase="final_review",
                review_scope="branch",
                current_defect_id="d",
                failure_history=history,
            ),
            self.ontologies,
        )
        self.assertEqual(decision["decisive_reason_id"], "branch_final_review")

        recurrence = compiler.compile_route(
            self.bound_evidence(["architecture"]),
            self.runtime_input(current_defect_id="d", failure_history=history),
            self.ontologies,
        )
        self.assertEqual(
            recurrence["decisive_reason_id"],
            "regression_after_declared_resolved",
        )

        hard_only = compiler.compile_route(
            self.bound_evidence(["architecture", "schema_migration"]),
            self.runtime_input(),
            self.ontologies,
        )
        self.assertEqual(hard_only["decisive_reason_id"], "architecture")

        elevated = compiler.compile_route(
            self.bound_evidence(["critical_risk", "cross_module_integration"]),
            self.runtime_input(),
            self.ontologies,
        )
        self.assertEqual(elevated["decisive_reason_id"], "critical_risk")

        eligible = compiler.compile_route(
            self.bound_evidence(
                ["audit", "judgment_required", "low_blast_radius", "reconciliation"]
            ),
            self.runtime_input(),
            self.ontologies,
        )
        self.assertEqual(eligible["decisive_reason_id"], "audit")

    def test_new_published_schemas_are_closed_and_detect_drift(self) -> None:
        expected_names = {
            "cmr-route-evidence-v2.schema.json",
            "cmr-runtime-input-v1.schema.json",
            "cmr-route-decision-v1.schema.json",
        }
        schemas = contracts.expected_published_schemas(self.ontologies)
        self.assertTrue(expected_names.issubset(schemas))
        self.assertEqual(
            contracts.validate_published_contracts(SKILL_ROOT, self.ontologies), []
        )
        for filename in sorted(expected_names):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = copy_references(Path(directory))
                path = root / "references" / "schemas" / filename
                value = json.loads(path.read_text(encoding="utf-8"))
                value["additionalProperties"] = True
                write_json(path, value)
                errors = contracts.validate_published_contracts(root, self.ontologies)
                self.assertIn("drift", "\n".join(errors))

    def test_published_schema_parity_is_json_type_exact(self) -> None:
        mutations = {
            "cmr-route-evidence-v2.schema.json": (
                (
                    ("$defs", "OccurrenceEvidence", "properties", "exit_code", "const"),
                    False,
                ),
                (
                    ("$defs", "OccurrenceEvidence", "properties", "exit_code", "const"),
                    0.0,
                ),
                (
                    ("$defs", "ArtifactRef", "properties", "size_bytes", "minimum"),
                    False,
                ),
                (
                    ("$defs", "ArtifactRef", "properties", "size_bytes", "minimum"),
                    0.0,
                ),
            ),
            "cmr-runtime-input-v1.schema.json": (
                (("additionalProperties",), 0),
                (("additionalProperties",), 0.0),
                (
                    ("properties", "current_defect_id", "oneOf", 0, "minLength"),
                    True,
                ),
                (
                    ("properties", "current_defect_id", "oneOf", 0, "minLength"),
                    1.0,
                ),
            ),
            "cmr-route-decision-v1.schema.json": (
                (
                    ("$defs", "Recurrence", "oneOf", 0, "properties", "active", "const"),
                    0,
                ),
                (
                    ("$defs", "Recurrence", "oneOf", 0, "properties", "active", "const"),
                    0.0,
                ),
                (
                    ("$defs", "ArtifactRef", "properties", "size_bytes", "minimum"),
                    False,
                ),
                (
                    ("$defs", "ArtifactRef", "properties", "size_bytes", "minimum"),
                    0.0,
                ),
            ),
        }
        for filename, cases in mutations.items():
            for key_path, replacement in cases:
                with self.subTest(
                    filename=filename,
                    key_path=key_path,
                    replacement=replacement,
                ), tempfile.TemporaryDirectory() as directory:
                    root = copy_references(Path(directory))
                    path = root / "references" / "schemas" / filename
                    value = json.loads(path.read_text(encoding="utf-8"))
                    parent = value
                    for key in key_path[:-1]:
                        parent = parent[key]
                    original = parent[key_path[-1]]
                    self.assertEqual(original, replacement)
                    self.assertIsNot(type(original), type(replacement))
                    parent[key_path[-1]] = replacement
                    write_json(path, value)
                    errors = contracts.validate_published_contracts(
                        root, self.ontologies
                    )
                    self.assertIn("drift", "\n".join(errors))

    def test_published_schema_gate_rejects_overflowed_json_number(self) -> None:
        filename = "cmr-route-evidence-v2.schema.json"
        with tempfile.TemporaryDirectory() as directory:
            root = copy_references(Path(directory))
            path = root / "references" / "schemas" / filename
            payload = path.read_text(encoding="utf-8")
            mutated = payload.replace('"minimum": 0', '"minimum": 1e999', 1)
            self.assertNotEqual(mutated, payload)
            path.write_text(mutated, encoding="utf-8")

            try:
                errors = contracts.validate_published_contracts(
                    root, self.ontologies
                )
            except ValueError as exc:
                self.fail(f"published schema gate leaked ValueError: {exc}")
            self.assertEqual(
                errors,
                [f"schema drift: {filename} contains a non-finite JSON number"],
            )

    def test_schemas_exclude_impossible_review_reason_and_history_shapes(self) -> None:
        schemas = contracts.expected_published_schemas(self.ontologies)
        decision = schemas["cmr-route-decision-v1.schema.json"]
        runtime = schemas["cmr-runtime-input-v1.schema.json"]

        self.assertEqual(
            decision["properties"]["review"],
            {"$ref": "#/$defs/ReviewRoute"},
        )
        review_routes = {
            (
                branch["properties"]["model"]["const"],
                branch["properties"]["reasoning_effort"]["const"],
            )
            for branch in decision["$defs"]["ReviewRoute"]["oneOf"]
        }
        self.assertEqual(
            review_routes,
            {
                ("gpt-5.6-luna", "max"),
                ("gpt-5.6-sol", "xhigh"),
                ("gpt-5.6-sol", "max"),
            },
        )
        reason_ids = decision["properties"]["decisive_reason_id"]["enum"]
        for qualifier in (
            "architecture_approved",
            "clear_isolated_low_risk",
            "low_blast_radius",
        ):
            self.assertNotIn(qualifier, reason_ids)

        registry_entries = runtime["$defs"]["AuthorizationRegistry"]["properties"][
            "entries"
        ]
        self.assertTrue(registry_entries["uniqueItems"])
        self.assertEqual(len(runtime["allOf"]), 2)


if __name__ == "__main__":
    unittest.main()
