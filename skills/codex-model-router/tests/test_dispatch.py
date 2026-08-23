from __future__ import annotations

import copy
import dataclasses
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest import mock

sys.dont_write_bytecode = True

from support import (
    FIXTURES_ROOT,
    SKILL_ROOT,
    canonical_json_bytes,
    load_fixture,
    load_module,
    sha256,
    write_frozen_bytes,
)

compiler = load_module("cmr_compiler")
contracts = load_module("cmr_contracts")
runtime_module = load_module("cmr_runtime")
dispatch = load_module("cmr_dispatch")


USAGE = {
    "input_tokens": 37,
    "cached_input_tokens": 11,
    "cache_write_input_tokens": 3,
    "output_tokens": 19,
    "reasoning_output_tokens": 7,
}
CONTROL_CWDS = {
    phase: {
        "kind": "ephemeral",
        "path": f"/private/tmp/cmr-{phase}-task-3",
        "fresh": True,
        "destroyed": True,
        "read_only": True,
    }
    for phase in ("planning", "controller", "preflight")
}


class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("dispatch-cases.json")
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.ledger = Path(self.temporary.name).resolve() / "ledger"
        self.ledger.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def artifact(self, relative: str, payload: bytes) -> Any:
        value = write_frozen_bytes(
            self.ledger / relative,
            payload,
            relative_to=self.ledger,
        )
        return compiler.ArtifactRef.from_value(value)

    def json_artifact(self, relative: str, value: Any) -> Any:
        return self.artifact(relative, canonical_json_bytes(value))

    def read_ref(self, reference: Any) -> Any:
        return json.loads((self.ledger / reference.path).read_text(encoding="utf-8"))

    def read_ref_bytes(self, reference: Any) -> bytes:
        return (self.ledger / reference.path).read_bytes()

    def rewrite_json_ref(self, reference: Any, value: Any) -> Any:
        payload = canonical_json_bytes(value)
        path = self.ledger / reference.path
        path.chmod(0o644)
        path.write_bytes(payload)
        path.chmod(0o444)
        return compiler.ArtifactRef(reference.path, sha256(payload), len(payload))

    def control_occurrence(
        self,
        *,
        phase: str,
        result: Mapping[str, Any],
        prompt: Any,
        thread_id: str,
        task_id: str | None = None,
        plan: Any | None = None,
        forbidden_thread_ids: tuple[str, ...] = (),
        prefix: str,
    ) -> tuple[Any, Any, Any, Any]:
        response = json.dumps(
            dict(result), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        events = [
            {"type": "thread.started", "thread_id": thread_id},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": f"{prefix}-response",
                    "type": "agent_message",
                    "text": response,
                },
            },
            {"type": "turn.completed", "usage": copy.deepcopy(USAGE)},
        ]
        raw_payload = (
            "\n".join(json.dumps(event, separators=(",", ":")) for event in events)
            + "\n"
        ).encode("utf-8")
        raw = self.artifact(f"control/{prefix}.raw.jsonl", raw_payload)
        transcript_payload = f"cmr {phase} transcript for {prefix}\n".encode("utf-8")
        transcript = self.artifact(
            f"control/{prefix}.transcript.txt", transcript_payload
        )

        if phase == "planning":
            assert plan is not None
            scope_kind = "plan"
            scope_id = plan.sha256
            expected_task_id = None
            model, effort = "gpt-5.6-sol", "max"
            validator: Callable[[Any], list[str]] = lambda value: (
                contracts.validate_planner_result(
                    value,
                    [entry["task_id"] for entry in self.fixture["safe_lanes"]],
                    self.ontologies,
                )
            )
        else:
            assert task_id is not None
            scope_kind = "task"
            scope_id = task_id
            expected_task_id = task_id
            if phase == "controller":
                model, effort = "gpt-5.6-luna", "xhigh"
                validator = lambda value: contracts.validate_controller_result(
                    value, self.ontologies
                )
            else:
                model, effort = "gpt-5.6-luna", "max"
                validator = lambda value: contracts.validate_preflight_result(
                    value, self.ontologies
                )

        expected = runtime_module.OccurrenceExpectation(
            phase=phase,
            scope_kind=scope_kind,
            scope_id=scope_id,
            round_index=None,
            task_id=expected_task_id,
            model=model,
            reasoning_effort=effort,
            prompt_sha256=prompt.sha256,
            forbidden_thread_ids=forbidden_thread_ids,
            prior_thread_id=None,
            fork_turns="none",
            thread_policy="fresh",
            cwd_policy=copy.deepcopy(CONTROL_CWDS[phase]),
            write_policy="control_plane_no_write",
            response_policy="closed_json",
        )
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
            "cwd": copy.deepcopy(CONTROL_CWDS[phase]),
            "write_policy": expected.write_policy,
            "exit_code": 0,
            "external_writes": False,
            "transcript_payload": transcript_payload,
            "transcript_sha256": transcript.sha256,
            "raw_sha256": raw.sha256,
            "usage": copy.deepcopy(USAGE),
            "terminal_report_sha256": None,
        }
        if phase == "planning":
            metadata["plan_sha256"] = plan.sha256
        occurrence = runtime_module.audit_occurrence(
            raw_payload, metadata, expected, validator
        )
        self.assertTrue(occurrence.accepted, occurrence.errors)
        occurrence_ref = self.json_artifact(
            f"control/{prefix}.occurrence.json", occurrence.to_record()
        )
        return occurrence, raw, transcript, occurrence_ref

    def planning_inputs(self) -> tuple[Any, dict[str, Any], Any, Any]:
        briefs = {
            "T-001": self.artifact(
                "briefs/T-001.md", b"Task T-001 approved implementation brief\n"
            ),
            "T-002": self.artifact(
                "briefs/T-002.md", b"Task T-002 approved implementation brief\n"
            ),
        }
        plan_value = {
            "schema_version": "test-frozen-plan-v1",
            "tasks": [
                {"task_id": task_id, "brief": brief.to_dict()}
                for task_id, brief in briefs.items()
            ],
        }
        plan = self.json_artifact("inputs/plan.json", plan_value)
        prompt = self.artifact(
            "control/planning.prompt.md", b"Classify exact plan safe lanes.\n"
        )
        result = {
            "schema_version": "cmr-planner-result-v1",
            "safe_lanes": copy.deepcopy(self.fixture["safe_lanes"]),
        }
        occurrence, raw, transcript, occurrence_ref = self.control_occurrence(
            phase="planning",
            result=result,
            prompt=prompt,
            thread_id="thread-planning-task-3",
            plan=plan,
            prefix="planning",
        )
        evidence = dispatch.bind_planner_result(
            result,
            plan=plan,
            briefs=briefs,
            prompt=prompt,
            raw=raw,
            transcript=transcript,
            occurrence_ref=occurrence_ref,
            occurrence=occurrence,
        )
        return evidence, briefs, plan, prompt

    def materialized_safe_lane(self) -> tuple[Any, dict[str, Any], Any]:
        evidence, briefs, plan, _ = self.planning_inputs()
        artifacts = dispatch.materialize_safe_lane(evidence, ledger_root=self.ledger)
        return artifacts, briefs, plan

    def controller_bundle_inputs(
        self,
        *,
        task_id: str = "T-001",
        brief: Any | None = None,
        fact_ids: list[str] | None = None,
        uncertainty_ids: list[str] | None = None,
        action_intents: list[dict[str, str]] | None = None,
        runtime_changes: Mapping[str, Any] | None = None,
        prefix: str = "task",
    ) -> dict[str, Any]:
        if brief is None:
            brief = self.artifact(
                f"briefs/{task_id}.md",
                f"Task {task_id} approved implementation brief\n".encode("utf-8"),
            )
        result = {
            "schema_version": "cmr-controller-result-v1",
            "fact_ids": list(fact_ids or []),
            "uncertainty_ids": list(uncertainty_ids or []),
            "action_intents": copy.deepcopy(action_intents or []),
        }
        occurrence, raw, _, _ = self.control_occurrence(
            phase="controller",
            result=result,
            prompt=brief,
            thread_id=f"thread-controller-{task_id}-{prefix}",
            task_id=task_id,
            prefix=f"controller-{task_id}-{prefix}",
        )
        evidence = compiler.bind_controller_result(
            result,
            task_id=task_id,
            brief=brief,
            raw=raw,
            occurrence=occurrence,
        )
        evidence_ref = self.json_artifact(
            f"tasks/{task_id}/{prefix}.controller-evidence.json", evidence
        )
        runtime = {
            "schema_version": "cmr-runtime-input-v1",
            "task_phase": "implementation",
            "current_defect_id": None,
            "failure_history": [],
            "review_scope": "task",
            "authorization_registry": {
                "schema_version": "cmr-authorization-registry-v1",
                "entries": [],
            },
        }
        runtime.update(copy.deepcopy(dict(runtime_changes or {})))
        runtime_ref = self.json_artifact(
            f"tasks/{task_id}/{prefix}.runtime-input.json", runtime
        )
        decision = compiler.compile_route(evidence, runtime, self.ontologies)
        decision_ref = self.json_artifact(
            f"tasks/{task_id}/{prefix}.candidate-decision.json", decision
        )
        return {
            "brief": brief,
            "controller_result": result,
            "controller_occurrence": occurrence,
            "evidence": evidence,
            "evidence_ref": evidence_ref,
            "runtime": runtime,
            "runtime_ref": runtime_ref,
            "decision": decision,
            "decision_ref": decision_ref,
        }

    def run_preflight(
        self,
        bundle_inputs: Mapping[str, Any],
        selector: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        task_id: str = "T-001",
        prefix: str,
    ) -> tuple[Any, Any, Any]:
        prompt = self.artifact(
            f"control/{prefix}.prompt.md",
            f"Audit {task_id} semantic routing.\n".encode("utf-8"),
        )
        forbidden = (
            "thread-planning-task-3",
            bundle_inputs["controller_occurrence"].thread_id,
        )
        occurrence, raw, transcript, occurrence_ref = self.control_occurrence(
            phase="preflight",
            result=result,
            prompt=prompt,
            thread_id=f"thread-preflight-{task_id}-{prefix}",
            task_id=task_id,
            forbidden_thread_ids=forbidden,
            prefix=prefix,
        )
        bound = dispatch.bind_preflight_result(
            result,
            task_id=task_id,
            brief=bundle_inputs["brief"],
            candidate_evidence=bundle_inputs["evidence_ref"],
            candidate_decision=bundle_inputs["decision_ref"],
            selector=selector,
            prompt=prompt,
            raw=raw,
            transcript=transcript,
            occurrence_ref=occurrence_ref,
            occurrence=occurrence,
        )
        transition = dispatch.apply_preflight(
            bundle_inputs["evidence"],
            bundle_inputs["runtime"],
            bound,
            self.ontologies,
        )
        artifacts = dispatch.materialize_preflight(
            task_id=task_id,
            selector=selector,
            candidate_decision=bundle_inputs["decision_ref"],
            bound_preflight=bound,
            transition=transition,
            ledger_root=self.ledger,
        )
        return bound, transition, artifacts

    def assert_ref(self, reference: Any) -> None:
        payload = self.read_ref_bytes(reference)
        self.assertEqual(reference.sha256, sha256(payload))
        self.assertEqual(reference.size_bytes, len(payload))
        self.assertEqual(
            stat.S_IMODE((self.ledger / reference.path).stat().st_mode), 0o444
        )

    def test_safe_lane_materialization_is_global_plan_bound_frozen_and_exclusive(self) -> None:
        evidence, briefs, plan, _ = self.planning_inputs()
        self.assertEqual(
            set(evidence.__dataclass_fields__),
            {
                "result",
                "plan",
                "briefs",
                "prompt",
                "raw",
                "transcript",
                "occurrence_ref",
                "occurrence",
            },
        )
        artifacts = dispatch.materialize_safe_lane(evidence, ledger_root=self.ledger)
        self.assertEqual(
            set(artifacts.__dataclass_fields__),
            {"certificate", "planning_invocation", "manifest"},
        )
        prefix = f"safe-lane/{plan.sha256}"
        self.assertEqual(artifacts.certificate.path, f"{prefix}/certificate.json")
        self.assertEqual(
            artifacts.planning_invocation.path,
            f"{prefix}/planning-invocation.json",
        )
        self.assertEqual(artifacts.manifest.path, f"{prefix}/manifest.json")
        for reference in (
            artifacts.certificate,
            artifacts.planning_invocation,
            artifacts.manifest,
        ):
            self.assert_ref(reference)

        certificate = self.read_ref(artifacts.certificate)
        invocation = self.read_ref(artifacts.planning_invocation)
        manifest = self.read_ref(artifacts.manifest)
        expected_entries = [
            {
                "task_id": lane["task_id"],
                "brief": briefs[lane["task_id"]].to_dict(),
                "reason_ids": lane["reason_ids"],
            }
            for lane in self.fixture["safe_lanes"]
        ]
        self.assertEqual(
            set(certificate), {"schema_version", "plan", "entries"}
        )
        self.assertEqual(certificate["entries"], expected_entries)
        self.assertEqual(manifest["entries"], expected_entries)
        self.assertEqual(
            canonical_json_bytes(certificate["entries"]),
            canonical_json_bytes(manifest["entries"]),
        )
        self.assertEqual(invocation["purpose"], "canonical_planning")
        self.assertEqual(
            (invocation["model"], invocation["reasoning_effort"]),
            ("gpt-5.6-sol", "max"),
        )
        self.assertEqual(invocation["certificate"], artifacts.certificate.to_dict())
        self.assertEqual(
            dispatch.validate_safe_lane_manifest(
                self.ledger / artifacts.manifest.path, self.ledger
            ),
            [],
        )
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_safe_lane(evidence, ledger_root=self.ledger)

    def test_planner_binding_rejects_authored_fields_and_occurrence_or_ref_drift(self) -> None:
        evidence, _, _, _ = self.planning_inputs()
        invalid_result = dict(evidence.result)
        invalid_result["model"] = "gpt-5.6-sol"
        with self.assertRaises(dispatch.DispatchError):
            dispatch.bind_planner_result(
                invalid_result,
                plan=evidence.plan,
                briefs=evidence.briefs,
                prompt=evidence.prompt,
                raw=evidence.raw,
                transcript=evidence.transcript,
                occurrence_ref=evidence.occurrence_ref,
                occurrence=evidence.occurrence,
            )

        for label, changes in {
            "route": {"reasoning_effort": "xhigh"},
            "phase": {"phase": "controller"},
            "thread": {"thread_id": None},
            "cwd": {"cwd": {**CONTROL_CWDS["planning"], "fresh": False}},
            "external": {"external_writes": True},
            "raw object": {"raw_object": {"schema_version": "wrong"}},
        }.items():
            with self.subTest(label=label):
                changed = dataclasses.replace(evidence.occurrence, **changes)
                with self.assertRaises(dispatch.DispatchError):
                    dispatch.bind_planner_result(
                        evidence.result,
                        plan=evidence.plan,
                        briefs=evidence.briefs,
                        prompt=evidence.prompt,
                        raw=evidence.raw,
                        transcript=evidence.transcript,
                        occurrence_ref=evidence.occurrence_ref,
                        occurrence=changed,
                    )

        stale = compiler.ArtifactRef(
            evidence.raw.path, "f" * 64, evidence.raw.size_bytes
        )
        with self.assertRaises(dispatch.DispatchError):
            dispatch.bind_planner_result(
                evidence.result,
                plan=evidence.plan,
                briefs=evidence.briefs,
                prompt=evidence.prompt,
                raw=stale,
                transcript=evidence.transcript,
                occurrence_ref=evidence.occurrence_ref,
                occurrence=evidence.occurrence,
            )

    def test_safe_lane_rejects_plan_brief_divergence_before_write(self) -> None:
        evidence, briefs, _, _ = self.planning_inputs()
        split = dataclasses.replace(
            evidence,
            briefs={"T-001": briefs["T-002"], "T-002": briefs["T-001"]},
        )
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_safe_lane(split, ledger_root=self.ledger)
        self.assertFalse((self.ledger / "safe-lane").exists())

        reordered = dataclasses.replace(
            evidence,
            result={
                "schema_version": "cmr-planner-result-v1",
                "safe_lanes": list(reversed(evidence.result["safe_lanes"])),
            },
        )
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_safe_lane(reordered, ledger_root=self.ledger)
        self.assertFalse((self.ledger / "safe-lane").exists())

    def test_invalid_global_safe_lane_degrades_to_run_not_block(self) -> None:
        artifacts, briefs, _ = self.materialized_safe_lane()
        bundle = self.controller_bundle_inputs(brief=briefs["T-001"])
        valid_path = self.ledger / artifacts.manifest.path
        self.assertEqual(
            dispatch.select_preflight(
                bundle["decision"], manifest_path=valid_path, ledger_root=self.ledger
            )["decision"],
            "skip",
        )

        snapshots = Path(self.temporary.name) / "snapshots"
        snapshots.mkdir()
        cases: list[tuple[str, Callable[[Path, Any], Path]]] = []

        def missing(root: Path, copied: Any) -> Path:
            return root / "safe-lane" / ("0" * 64) / "manifest.json"

        def writable(root: Path, copied: Any) -> Path:
            path = root / copied.manifest.path
            path.chmod(0o644)
            return path

        def stale_global_brief(root: Path, copied: Any) -> Path:
            (root / briefs["T-002"].path).unlink()
            return root / copied.manifest.path

        def replaced_certificate(root: Path, copied: Any) -> Path:
            path = root / copied.certificate.path
            path.chmod(0o644)
            path.write_bytes(b'{"schema_version":"replaced"}\n')
            path.chmod(0o444)
            return root / copied.manifest.path

        def malformed_manifest(root: Path, copied: Any) -> Path:
            path = root / copied.manifest.path
            path.chmod(0o644)
            path.write_bytes(b'{"schema_version":"cmr-safe-lane-manifest-v1",')
            path.chmod(0o444)
            return path

        def split_certificate(root: Path, copied: Any) -> Path:
            path = root / copied.manifest.path
            manifest = json.loads(path.read_text(encoding="utf-8"))
            duplicate = write_frozen_bytes(
                root / "split/certificate.json",
                (root / copied.certificate.path).read_bytes(),
                relative_to=root,
            )
            manifest["certificate"] = duplicate
            path.chmod(0o644)
            path.write_bytes(canonical_json_bytes(manifest))
            path.chmod(0o444)
            return path

        cases.extend(
            [
                ("missing", missing),
                ("writable", writable),
                ("incomplete", stale_global_brief),
                ("replaced", replaced_certificate),
                ("malformed", malformed_manifest),
                ("split", split_certificate),
            ]
        )
        for index, (label, mutation) in enumerate(cases):
            with self.subTest(label=label):
                copied_root = snapshots / str(index) / "ledger"
                shutil.copytree(self.ledger, copied_root)
                path = mutation(copied_root, artifacts)
                selector = dispatch.select_preflight(
                    bundle["decision"],
                    manifest_path=path,
                    ledger_root=copied_root,
                )
                self.assertEqual(
                    selector,
                    {
                        "decision": "run",
                        "trigger_ids": ["safe_lane_unavailable"],
                    },
                )

    def test_only_exact_safe_default_lane_skips_preflight(self) -> None:
        artifacts, briefs, _ = self.materialized_safe_lane()
        bundle = self.controller_bundle_inputs(brief=briefs["T-001"])
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / artifacts.manifest.path,
            ledger_root=self.ledger,
        )
        self.assertEqual(
            selector,
            {
                "decision": "skip",
                "trigger_ids": [],
                "safe_lane": artifacts.manifest.to_dict(),
            },
        )

    def test_selector_canonically_runs_every_nondefault_or_uncertain_case(self) -> None:
        artifacts, briefs, _ = self.materialized_safe_lane()
        base = self.controller_bundle_inputs(brief=briefs["T-001"])
        evidence = base["evidence"]
        runtime = base["runtime"]
        manifest = self.ledger / artifacts.manifest.path

        cases: list[tuple[str, dict[str, Any], dict[str, Any], list[str]]] = [
            (
                "luna max",
                {**evidence, "fact_ids": ["audit", "low_blast_radius"]},
                runtime,
                ["nondefault_worker_route", "luna_max_eligibility"],
            ),
            (
                "integration",
                {**evidence, "fact_ids": ["integration_tests"]},
                runtime,
                ["nondefault_worker_route", "integration_or_elevated_risk"],
            ),
            (
                "hard gate",
                {**evidence, "fact_ids": ["architecture"]},
                runtime,
                ["nondefault_worker_route", "hard_gate_or_recurrence"],
            ),
            (
                "recurrence",
                evidence,
                {
                    **runtime,
                    "current_defect_id": "same-defect",
                    "failure_history": [
                        {
                            "defect_id": "same-defect",
                            "event": "attempt_completed_failed",
                        },
                        {
                            "defect_id": "same-defect",
                            "event": "attempt_completed_failed",
                        },
                    ],
                },
                ["nondefault_worker_route", "hard_gate_or_recurrence"],
            ),
            (
                "branch review",
                evidence,
                {**runtime, "task_phase": "final_review", "review_scope": "branch"},
                [
                    "nondefault_worker_route",
                    "branch_final_review",
                ],
            ),
            (
                "external",
                {
                    **evidence,
                    "action_intents": [{"action": "push"}],
                },
                runtime,
                ["external_action_present"],
            ),
            (
                "uncertainty",
                {
                    **evidence,
                    "uncertainty_ids": [
                        "multiple_policy_facts",
                        "controller_uncertainty",
                    ],
                },
                runtime,
                ["multiple_policy_facts", "controller_uncertainty"],
            ),
        ]
        for label, changed_evidence, changed_runtime, expected_triggers in cases:
            with self.subTest(label=label):
                decision = compiler.compile_route(
                    changed_evidence, changed_runtime, self.ontologies
                )
                self.assertEqual(
                    dispatch.select_preflight(
                        decision, manifest_path=manifest, ledger_root=self.ledger
                    ),
                    {"decision": "run", "trigger_ids": expected_triggers},
                )

        malformed = copy.deepcopy(base["decision"])
        malformed["execution"]["context_mode"] = "inherited"
        self.assertEqual(
            dispatch.select_preflight(
                malformed, manifest_path=manifest, ledger_root=self.ledger
            ),
            {
                "decision": "block",
                "trigger_ids": ["artifact_binding_invalid"],
            },
        )

    def test_accept_is_audited_bound_byte_preserving_and_materialized(self) -> None:
        bundle = self.controller_bundle_inputs()
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        bound, transition, artifacts = self.run_preflight(
            bundle,
            selector,
            self.fixture["preflight_results"]["accept"],
            prefix="accept",
        )
        self.assertTrue(bound.__dataclass_params__.frozen)
        self.assertEqual(transition.outcome, "accepted")
        self.assertEqual(
            canonical_json_bytes(transition.final_decision),
            canonical_json_bytes(bundle["decision"]),
        )
        self.assertIsNotNone(artifacts.invocation)
        self.assertIsNone(artifacts.replacement_evidence)
        self.assertEqual(artifacts.final_decision, bundle["decision_ref"])
        invocation = self.read_ref(artifacts.invocation)
        evidence = self.read_ref(artifacts.evidence)
        self.assertEqual(
            set(invocation),
            {
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
            },
        )
        self.assertEqual(
            set(evidence),
            {
                "schema_version",
                "task_id",
                "selector",
                "outcome",
                "candidate_decision",
                "invocation",
            },
        )
        self.assertEqual(evidence["outcome"], "accepted")
        self.assertEqual(
            dispatch.validate_preflight_evidence(
                self.ledger / artifacts.evidence.path, self.ledger
            ),
            [],
        )

    def test_replace_rebinds_route_evidence_and_recompiles_monotonically(self) -> None:
        bundle = self.controller_bundle_inputs()
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        _, transition, artifacts = self.run_preflight(
            bundle,
            selector,
            self.fixture["preflight_results"]["replace_audit"],
            prefix="replace",
        )
        self.assertEqual(transition.outcome, "replaced")
        self.assertEqual(
            transition.replacement_evidence["schema_version"],
            "cmr-route-evidence-v2",
        )
        self.assertEqual(
            transition.final_decision["worker"],
            {"model": "gpt-5.6-luna", "reasoning_effort": "max"},
        )
        self.assertIsNotNone(artifacts.replacement_evidence)
        self.assertIsNotNone(artifacts.final_decision)
        self.assertNotEqual(artifacts.final_decision, bundle["decision_ref"])
        evidence = self.read_ref(artifacts.evidence)
        self.assertEqual(evidence["outcome"], "replaced")
        self.assertEqual(
            evidence["replacement_evidence"],
            artifacts.replacement_evidence.to_dict(),
        )
        self.assertEqual(evidence["final_decision"], artifacts.final_decision.to_dict())
        self.assertEqual(
            dispatch.validate_preflight_evidence(
                self.ledger / artifacts.evidence.path, self.ledger
            ),
            [],
        )

    def test_invalid_or_nonmonotonic_preflight_cannot_downroute(self) -> None:
        hard = self.controller_bundle_inputs(
            fact_ids=["architecture"], prefix="hard"
        )
        selector = dispatch.select_preflight(
            hard["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        weakening = {
            "schema_version": "cmr-preflight-result-v1",
            "outcome": "replace",
            "fact_ids": [],
            "uncertainty_ids": [],
            "action_intents": [],
        }
        _, transition, artifacts = self.run_preflight(
            hard, selector, weakening, prefix="nonmonotonic"
        )
        self.assertEqual(transition.outcome, "blocked")
        self.assertEqual(transition.blocker_ids, ("preflight_non_monotonic",))
        self.assertEqual(
            transition.adjudication,
            {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "max",
                "fork_turns": "none",
                "thread_policy": "fresh",
            },
        )
        self.assertIsNone(artifacts.final_decision)
        self.assertEqual(self.read_ref(artifacts.evidence)["outcome"], "blocked")

        with self.assertRaises(compiler.CompilationError) as captured:
            dispatch.apply_preflight(
                hard["evidence"],
                hard["runtime"],
                dict(self.fixture["preflight_results"]["accept"]),
                self.ontologies,
            )
        self.assertEqual(captured.exception.blocker_id, "preflight_result_invalid")

    def test_preflight_binding_rejects_wrong_audit_and_artifact_bindings(self) -> None:
        bundle = self.controller_bundle_inputs()
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        result = self.fixture["preflight_results"]["accept"]
        prompt = self.artifact("control/bind.prompt.md", b"Preflight bind prompt\n")
        occurrence, raw, transcript, occurrence_ref = self.control_occurrence(
            phase="preflight",
            result=result,
            prompt=prompt,
            thread_id="thread-preflight-bind",
            task_id="T-001",
            forbidden_thread_ids=(bundle["controller_occurrence"].thread_id,),
            prefix="bind",
        )
        cases = {
            "model": ({"model": "gpt-5.6-sol"}, "occurrence_invalid"),
            "effort": ({"reasoning_effort": "xhigh"}, "occurrence_invalid"),
            "fork": ({"fork_turns": "all"}, "occurrence_invalid"),
            "task": ({"task_id": "T-OTHER"}, "occurrence_invalid"),
            "prompt": ({"prompt_sha256": "e" * 64}, "artifact_binding_invalid"),
            "raw": ({"raw_sha256": "d" * 64}, "artifact_binding_invalid"),
            "usage": ({"usage": None}, "occurrence_invalid"),
            "exit": ({"exit_code": 1}, "occurrence_invalid"),
        }
        for label, (changes, expected_blocker) in cases.items():
            with self.subTest(label=label):
                changed = dataclasses.replace(occurrence, **changes)
                with self.assertRaises(compiler.CompilationError) as captured:
                    dispatch.bind_preflight_result(
                        result,
                        task_id="T-001",
                        brief=bundle["brief"],
                        candidate_evidence=bundle["evidence_ref"],
                        candidate_decision=bundle["decision_ref"],
                        selector=selector,
                        prompt=prompt,
                        raw=raw,
                        transcript=transcript,
                        occurrence_ref=occurrence_ref,
                        occurrence=changed,
                    )
                self.assertEqual(captured.exception.blocker_id, expected_blocker)

    def test_skip_and_both_blocked_branches_have_exact_absence_rules(self) -> None:
        artifacts, briefs, _ = self.materialized_safe_lane()
        bundle = self.controller_bundle_inputs(brief=briefs["T-001"])
        skip_selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / artifacts.manifest.path,
            ledger_root=self.ledger,
        )
        skipped = dispatch.materialize_preflight(
            task_id="T-001",
            selector=skip_selector,
            candidate_decision=bundle["decision_ref"],
            bound_preflight=None,
            transition=None,
            ledger_root=self.ledger,
        )
        self.assertIsNone(skipped.invocation)
        self.assertIsNone(skipped.replacement_evidence)
        self.assertEqual(skipped.final_decision, bundle["decision_ref"])
        self.assertEqual(
            set(self.read_ref(skipped.evidence)),
            {
                "schema_version",
                "task_id",
                "selector",
                "outcome",
                "candidate_decision",
            },
        )

        pre_call = dispatch.materialize_preflight(
            task_id="T-BLOCK-PRE",
            selector={
                "decision": "block",
                "trigger_ids": ["artifact_binding_invalid"],
            },
            candidate_decision=None,
            bound_preflight=None,
            transition=None,
            ledger_root=self.ledger,
        )
        self.assertIsNone(pre_call.invocation)
        self.assertIsNone(pre_call.final_decision)
        self.assertEqual(
            set(self.read_ref(pre_call.evidence)),
            {"schema_version", "task_id", "selector", "outcome", "blocker_ids"},
        )

        post_bundle = self.controller_bundle_inputs(
            task_id="T-BLOCK-POST", prefix="post"
        )
        run_selector = dispatch.select_preflight(
            post_bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        _, transition, post_call = self.run_preflight(
            post_bundle,
            run_selector,
            self.fixture["preflight_results"]["block"],
            task_id="T-BLOCK-POST",
            prefix="post-block",
        )
        self.assertEqual(transition.outcome, "blocked")
        self.assertIsNotNone(post_call.invocation)
        self.assertIsNone(post_call.final_decision)
        self.assertEqual(
            set(self.read_ref(post_call.evidence)),
            {
                "schema_version",
                "task_id",
                "selector",
                "outcome",
                "candidate_decision",
                "invocation",
                "blocker_ids",
            },
        )

    def materialize_valid_bundle(self, branch: str) -> tuple[Any, Any, dict[str, Any]]:
        if branch == "skip":
            safe, briefs, _ = self.materialized_safe_lane()
            bundle = self.controller_bundle_inputs(brief=briefs["T-001"])
            selector = dispatch.select_preflight(
                bundle["decision"],
                manifest_path=self.ledger / safe.manifest.path,
                ledger_root=self.ledger,
            )
            preflight = dispatch.materialize_preflight(
                task_id="T-001",
                selector=selector,
                candidate_decision=bundle["decision_ref"],
                bound_preflight=None,
                transition=None,
                ledger_root=self.ledger,
            )
        else:
            bundle = self.controller_bundle_inputs()
            selector = dispatch.select_preflight(
                bundle["decision"],
                manifest_path=self.ledger / "missing.json",
                ledger_root=self.ledger,
            )
            result = self.fixture["preflight_results"][
                "accept" if branch == "accept" else "replace_audit"
            ]
            _, _, preflight = self.run_preflight(
                bundle, selector, result, prefix=f"bundle-{branch}"
            )
        assert preflight.final_decision is not None
        reference = dispatch.materialize_dispatch_bundle(
            task_id="T-001",
            brief=bundle["brief"],
            controller_evidence=bundle["evidence_ref"],
            runtime_input=bundle["runtime_ref"],
            candidate_decision=bundle["decision_ref"],
            preflight_evidence=preflight.evidence,
            final_decision=preflight.final_decision,
            ledger_root=self.ledger,
        )
        return reference, preflight, bundle

    def test_dispatch_bundle_authorizes_only_skip_accept_and_replace(self) -> None:
        for branch in ("skip", "accept", "replace"):
            with self.subTest(branch=branch):
                if branch != "skip":
                    self.temporary.cleanup()
                    self.temporary = tempfile.TemporaryDirectory()
                    self.ledger = Path(self.temporary.name).resolve() / "ledger"
                    self.ledger.mkdir()
                reference, preflight, bundle = self.materialize_valid_bundle(branch)
                self.assert_ref(reference)
                value = self.read_ref(reference)
                self.assertEqual(
                    set(value),
                    {
                        "schema_version",
                        "task_id",
                        "brief",
                        "controller_evidence",
                        "runtime_input",
                        "candidate_decision",
                        "preflight_evidence",
                        "final_decision",
                    },
                )
                self.assertEqual(
                    dispatch.validate_dispatch_bundle(
                        self.ledger / reference.path, self.ledger
                    ),
                    [],
                )
                if branch in {"skip", "accept"}:
                    self.assertEqual(
                        value["candidate_decision"], value["final_decision"]
                    )
                else:
                    self.assertEqual(
                        value["final_decision"],
                        preflight.final_decision.to_dict(),
                    )
                    self.assertNotEqual(
                        value["candidate_decision"], value["final_decision"]
                    )

    def test_blocked_transition_cannot_materialize_a_dispatch_bundle(self) -> None:
        bundle = self.controller_bundle_inputs()
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        _, _, blocked = self.run_preflight(
            bundle,
            selector,
            self.fixture["preflight_results"]["block"],
            prefix="blocked-dispatch",
        )
        self.assertIsNone(blocked.final_decision)
        with self.assertRaises((TypeError, ValueError, dispatch.DispatchError)):
            dispatch.materialize_dispatch_bundle(
                task_id="T-001",
                brief=bundle["brief"],
                controller_evidence=bundle["evidence_ref"],
                runtime_input=bundle["runtime_ref"],
                candidate_decision=bundle["decision_ref"],
                preflight_evidence=blocked.evidence,
                final_decision=blocked.final_decision,
                ledger_root=self.ledger,
            )

    def forged_nonmonotonic_transition(
        self, *, prefix: str
    ) -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
        bundle = self.controller_bundle_inputs(
            fact_ids=["architecture"], prefix=prefix
        )
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        result = {
            "schema_version": "cmr-preflight-result-v1",
            "outcome": "replace",
            "fact_ids": [],
            "uncertainty_ids": [],
            "action_intents": [],
        }
        prompt = self.artifact(
            f"control/{prefix}.prompt.md",
            b"Audit the persisted replacement.\n",
        )
        occurrence, raw, transcript, occurrence_ref = self.control_occurrence(
            phase="preflight",
            result=result,
            prompt=prompt,
            thread_id=f"thread-preflight-T-001-{prefix}",
            task_id="T-001",
            forbidden_thread_ids=(bundle["controller_occurrence"].thread_id,),
            prefix=prefix,
        )
        bound = dispatch.bind_preflight_result(
            result,
            task_id="T-001",
            brief=bundle["brief"],
            candidate_evidence=bundle["evidence_ref"],
            candidate_decision=bundle["decision_ref"],
            selector=selector,
            prompt=prompt,
            raw=raw,
            transcript=transcript,
            occurrence_ref=occurrence_ref,
            occurrence=occurrence,
        )
        replacement = copy.deepcopy(bundle["evidence"])
        for field in ("fact_ids", "uncertainty_ids", "action_intents"):
            replacement[field] = copy.deepcopy(result[field])
        forged = compiler.PreflightTransition(
            outcome="replaced",
            replacement_evidence=replacement,
            final_decision=compiler.compile_route(
                replacement, bundle["runtime"], self.ontologies
            ),
            blocker_ids=(),
            adjudication=None,
        )
        return bundle, selector, bound, forged

    def test_dispatch_rechecks_monotonicity_of_persisted_replacement(self) -> None:
        bundle, selector, bound, forged = self.forged_nonmonotonic_transition(
            prefix="forged-replacement"
        )
        occurrence = bound.occurrence
        invocation = {
            "schema_version": "cmr-preflight-invocation-v1",
            "phase": "preflight",
            "task_id": "T-001",
            "brief": bound.brief.to_dict(),
            "candidate_evidence": bound.candidate_evidence.to_dict(),
            "candidate_decision": bound.candidate_decision.to_dict(),
            "selector": copy.deepcopy(selector),
            "prompt": bound.prompt.to_dict(),
            "raw": bound.raw.to_dict(),
            "transcript": bound.transcript.to_dict(),
            "occurrence": bound.occurrence_ref.to_dict(),
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "fork_turns": "none",
            "thread_id": occurrence.thread_id,
            "cwd": copy.deepcopy(occurrence.cwd),
            "exit_code": 0,
            "external_writes": False,
            "usage": occurrence.usage.to_dict(),
        }
        invocation_ref = self.json_artifact(
            "tasks/T-001/preflight/invocation.json", invocation
        )
        replacement_ref = self.json_artifact(
            "tasks/T-001/preflight/replacement-evidence.json",
            forged.replacement_evidence,
        )
        final_ref = self.json_artifact(
            "tasks/T-001/preflight/final-decision.json", forged.final_decision
        )
        evidence_ref = self.json_artifact(
            "tasks/T-001/preflight/evidence.json",
            {
                "schema_version": "cmr-preflight-evidence-v1",
                "task_id": "T-001",
                "selector": copy.deepcopy(selector),
                "outcome": "replaced",
                "candidate_decision": bundle["decision_ref"].to_dict(),
                "invocation": invocation_ref.to_dict(),
                "replacement_evidence": replacement_ref.to_dict(),
                "final_decision": final_ref.to_dict(),
            },
        )
        (self.ledger / "tasks/T-001/preflight").chmod(0o555)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_dispatch_bundle(
                task_id="T-001",
                brief=bundle["brief"],
                controller_evidence=bundle["evidence_ref"],
                runtime_input=bundle["runtime_ref"],
                candidate_decision=bundle["decision_ref"],
                preflight_evidence=evidence_ref,
                final_decision=final_ref,
                ledger_root=self.ledger,
            )

    def test_dispatch_rejects_rehashed_replacement_occurrence_thread_drift(
        self,
    ) -> None:
        bundle_ref, preflight, _ = self.materialize_valid_bundle("replace")
        assert preflight.replacement_evidence is not None

        replacement = self.read_ref(preflight.replacement_evidence)
        replacement["occurrence"]["thread_id"] = (
            "thread-controller-T-001-rehashed-replacement"
        )
        replacement_ref = self.rewrite_json_ref(
            preflight.replacement_evidence, replacement
        )

        preflight_evidence = self.read_ref(preflight.evidence)
        preflight_evidence["replacement_evidence"] = replacement_ref.to_dict()
        preflight_evidence_ref = self.rewrite_json_ref(
            preflight.evidence, preflight_evidence
        )

        bundle = self.read_ref(bundle_ref)
        bundle["preflight_evidence"] = preflight_evidence_ref.to_dict()
        self.rewrite_json_ref(bundle_ref, bundle)

        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(
                self.ledger / bundle_ref.path, self.ledger
            ),
        )

    def test_preflight_monotonicity_rejects_immutable_provenance_drift(
        self,
    ) -> None:
        bundle = self.controller_bundle_inputs(prefix="immutable-provenance")
        candidate_evidence = bundle["evidence"]
        candidate_decision = bundle["decision"]
        replacement_evidence = copy.deepcopy(candidate_evidence)
        replacement_decision = copy.deepcopy(candidate_decision)
        self.assertEqual(
            compiler.validate_preflight_monotonicity(
                candidate_evidence,
                candidate_decision,
                replacement_evidence,
                replacement_decision,
                self.ontologies,
            ),
            [],
        )

        immutable_top_level = {
            "schema_version": "cmr-route-evidence-v999",
            "task_id": "T-OTHER",
            "brief": {
                **candidate_evidence["brief"],
                "sha256": "f" * 64,
            },
            "raw": {
                **candidate_evidence["raw"],
                "sha256": "e" * 64,
            },
        }
        for field, changed_value in immutable_top_level.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(replacement_evidence)
                changed[field] = copy.deepcopy(changed_value)
                self.assertTrue(
                    compiler.validate_preflight_monotonicity(
                        candidate_evidence,
                        candidate_decision,
                        changed,
                        replacement_decision,
                        self.ontologies,
                    )
                )

        occurrence_changes = {
            "thread_id": "thread-controller-provenance-drift",
            "prompt_sha256": "a" * 64,
            "raw_sha256": "b" * 64,
            "transcript_sha256": "c" * 64,
            "usage": {
                **candidate_evidence["occurrence"]["usage"],
                "input_tokens": (
                    candidate_evidence["occurrence"]["usage"]["input_tokens"] + 1
                ),
            },
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "fork_turns": "all",
            "cwd": {
                **candidate_evidence["occurrence"]["cwd"],
                "path": "/private/tmp/cmr-provenance-drift",
            },
            "exit_code": 1,
        }
        for field, changed_value in occurrence_changes.items():
            with self.subTest(occurrence_field=field):
                changed = copy.deepcopy(replacement_evidence)
                changed["occurrence"][field] = copy.deepcopy(changed_value)
                self.assertTrue(
                    compiler.validate_preflight_monotonicity(
                        candidate_evidence,
                        candidate_decision,
                        changed,
                        replacement_decision,
                        self.ontologies,
                    )
                )

    def test_preflight_materializer_rejects_forged_nonmonotonic_transition(
        self,
    ) -> None:
        bundle, selector, bound, forged = self.forged_nonmonotonic_transition(
            prefix="forged-producer"
        )
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_preflight(
                task_id="T-001",
                selector=selector,
                candidate_decision=bundle["decision_ref"],
                bound_preflight=bound,
                transition=forged,
                ledger_root=self.ledger,
            )

    def test_dispatch_rejects_reference_identity_and_occurrence_mutations(self) -> None:
        reference, _, _ = self.materialize_valid_bundle("accept")
        valid = self.read_ref(reference)
        mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = []
        for field in (
            "brief",
            "controller_evidence",
            "runtime_input",
            "candidate_decision",
            "preflight_evidence",
            "final_decision",
        ):
            mutations.append(
                (
                    f"{field} sha",
                    lambda value, field=field: value[field].update(
                        {"sha256": "f" * 64}
                    ),
                )
            )
        for index, (label, mutate) in enumerate(mutations):
            with self.subTest(label=label):
                changed = copy.deepcopy(valid)
                mutate(changed)
                path = self.ledger / f"invalid/{index}.bundle.json"
                write_frozen_bytes(path, canonical_json_bytes(changed))
                errors = dispatch.validate_dispatch_bundle(path, self.ledger)
                self.assertIn("dispatch_bundle_invalid", errors)

        controller_ref = compiler.ArtifactRef.from_value(valid["controller_evidence"])
        controller_path = self.ledger / controller_ref.path
        controller_path.chmod(0o644)
        controller = json.loads(controller_path.read_text(encoding="utf-8"))
        controller["occurrence"]["reasoning_effort"] = "max"
        controller_path.write_bytes(canonical_json_bytes(controller))
        controller_path.chmod(0o444)
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(
                self.ledger / reference.path, self.ledger
            ),
        )

    def test_dot_task_components_fail_before_filesystem_mutation(self) -> None:
        for index, task_id in enumerate((".", "..")):
            with self.subTest(task_id=task_id):
                if index:
                    self.temporary.cleanup()
                    self.temporary = tempfile.TemporaryDirectory()
                    self.ledger = Path(self.temporary.name).resolve() / "ledger"
                    self.ledger.mkdir()
                with self.assertRaises(dispatch.DispatchError):
                    dispatch.materialize_preflight(
                        task_id=task_id,
                        selector={
                            "decision": "block",
                            "trigger_ids": ["artifact_binding_invalid"],
                        },
                        candidate_decision=None,
                        bound_preflight=None,
                        transition=None,
                        ledger_root=self.ledger,
                    )
                self.assertEqual(list(self.ledger.iterdir()), [])

    def assert_validator_rejects_scope_directory_swap(
        self,
        *,
        scope: str,
        validator: Callable[[], list[str]],
    ) -> None:
        original_directory_state = dispatch._directory_state
        swapped = False

        def swap_after_observation(
            root_descriptor: int, relative_path: str
        ) -> tuple[set[str], os.stat_result]:
            nonlocal swapped
            observed = original_directory_state(root_descriptor, relative_path)
            if relative_path == scope and not swapped:
                scope_path = self.ledger / scope
                backup = scope_path.with_name(f"{scope_path.name}-replaced")
                scope_path.parent.chmod(0o755)
                scope_path.rename(backup)
                shutil.copytree(backup, scope_path)
                swapped = True
            return observed

        with mock.patch.object(
            dispatch, "_directory_state", side_effect=swap_after_observation
        ):
            self.assertNotEqual(validator(), [])
        self.assertTrue(swapped)

    def test_validators_reject_scope_directory_identity_changes(self) -> None:
        safe, _, plan = self.materialized_safe_lane()
        safe_scope = f"safe-lane/{plan.sha256}"
        self.assert_validator_rejects_scope_directory_swap(
            scope=safe_scope,
            validator=lambda: dispatch.validate_safe_lane_manifest(
                self.ledger / safe.manifest.path, self.ledger
            ),
        )

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.ledger = Path(self.temporary.name).resolve() / "ledger"
        self.ledger.mkdir()
        bundle = self.controller_bundle_inputs(prefix="identity-preflight")
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        _, _, preflight = self.run_preflight(
            bundle,
            selector,
            self.fixture["preflight_results"]["accept"],
            prefix="identity-preflight",
        )
        self.assert_validator_rejects_scope_directory_swap(
            scope="tasks/T-001/preflight",
            validator=lambda: dispatch.validate_preflight_evidence(
                self.ledger / preflight.evidence.path, self.ledger
            ),
        )

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.ledger = Path(self.temporary.name).resolve() / "ledger"
        self.ledger.mkdir()
        dispatch_bundle, _, _ = self.materialize_valid_bundle("accept")
        self.assert_validator_rejects_scope_directory_swap(
            scope="tasks/T-001/dispatch",
            validator=lambda: dispatch.validate_dispatch_bundle(
                self.ledger / dispatch_bundle.path, self.ledger
            ),
        )

    def test_path_safety_duplicate_json_and_legacy_route_fail_closed(self) -> None:
        reference, _, _ = self.materialize_valid_bundle("accept")
        actual = self.ledger / reference.path
        symlink_leaf = self.ledger / "dispatch-symlink.json"
        symlink_leaf.symlink_to(actual)
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(symlink_leaf, self.ledger),
        )

        dangling = self.ledger / "dangling.json"
        dangling.symlink_to(self.ledger / "does-not-exist")
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(dangling, self.ledger),
        )

        ancestor_target = Path(self.temporary.name) / "symlink-ancestor-target"
        ancestor_target.mkdir()
        ancestor_bundle = ancestor_target / "bundle.json"
        ancestor_bundle.write_bytes(actual.read_bytes())
        ancestor_bundle.chmod(0o444)
        symlink_ancestor = self.ledger / "symlink-ancestor"
        symlink_ancestor.symlink_to(ancestor_target, target_is_directory=True)
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(
                symlink_ancestor / "bundle.json", self.ledger
            ),
        )

        directory = self.ledger / "not-a-file"
        directory.mkdir()
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(directory, self.ledger),
        )

        outside = Path(self.temporary.name) / "outside.json"
        outside.write_bytes(actual.read_bytes())
        outside.chmod(0o444)
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(outside, self.ledger),
        )

        duplicate = self.ledger / "duplicate.json"
        duplicate.write_bytes(
            b'{"schema_version":"cmr-dispatch-bundle-v1",'
            b'"schema_version":"cmr-dispatch-bundle-v1"}\n'
        )
        duplicate.chmod(0o444)
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(duplicate, self.ledger),
        )

        invalid_path = copy.deepcopy(self.read_ref(reference))
        invalid_path["brief"]["path"] = "../escape"
        invalid_bundle = self.ledger / "dotdot.json"
        invalid_bundle.write_bytes(canonical_json_bytes(invalid_path))
        invalid_bundle.chmod(0o444)
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(invalid_bundle, self.ledger),
        )

        for label, invalid_reference_path in (
            ("absolute", "/private/tmp/cmr-escape"),
            ("backslash", "briefs\\escape.md"),
        ):
            with self.subTest(reference_path=label):
                invalid_path = copy.deepcopy(self.read_ref(reference))
                invalid_path["brief"]["path"] = invalid_reference_path
                path = self.ledger / f"{label}.json"
                path.write_bytes(canonical_json_bytes(invalid_path))
                path.chmod(0o444)
                self.assertIn(
                    "dispatch_bundle_invalid",
                    dispatch.validate_dispatch_bundle(path, self.ledger),
                )

        bundle_value = self.read_ref(reference)
        runtime_ref = compiler.ArtifactRef.from_value(bundle_value["runtime_input"])
        runtime_path = self.ledger / runtime_ref.path
        runtime_path.chmod(0o644)
        self.assertIn(
            "dispatch_bundle_invalid",
            dispatch.validate_dispatch_bundle(actual, self.ledger),
        )
        runtime_path.chmod(0o444)

        legacy = self.ledger / "legacy.json"
        legacy.write_bytes(
            canonical_json_bytes(
                {"schema_version": "qfr-semantic-preflight-sidecar-v3"}
            )
        )
        legacy.chmod(0o444)
        self.assertEqual(
            dispatch.validate_dispatch_bundle(legacy, self.ledger),
            ["legacy_dispatch_forbidden"],
        )

    def test_all_six_runtime_contracts_reject_adversarial_mutation_classes(
        self,
    ) -> None:
        safe, _, _ = self.materialized_safe_lane()
        certificate = self.read_ref(safe.certificate)
        planning_invocation = self.read_ref(safe.planning_invocation)
        manifest = self.read_ref(safe.manifest)

        task_id = "T-SCHEMA-MUTATION"
        bundle = self.controller_bundle_inputs(task_id=task_id, prefix="schemas")
        selector = dispatch.select_preflight(
            bundle["decision"],
            manifest_path=self.ledger / "missing.json",
            ledger_root=self.ledger,
        )
        _, _, preflight = self.run_preflight(
            bundle,
            selector,
            self.fixture["preflight_results"]["accept"],
            task_id=task_id,
            prefix="schema-preflight",
        )
        assert preflight.invocation is not None
        assert preflight.final_decision is not None
        dispatch_ref = dispatch.materialize_dispatch_bundle(
            task_id=task_id,
            brief=bundle["brief"],
            controller_evidence=bundle["evidence_ref"],
            runtime_input=bundle["runtime_ref"],
            candidate_decision=bundle["decision_ref"],
            preflight_evidence=preflight.evidence,
            final_decision=preflight.final_decision,
            ledger_root=self.ledger,
        )
        preflight_invocation = self.read_ref(preflight.invocation)
        preflight_evidence = self.read_ref(preflight.evidence)
        dispatch_bundle = self.read_ref(dispatch_ref)

        cases: list[
            tuple[str, dict[str, Any], Callable[[Any], list[str]], str]
        ] = [
            (
                "certificate",
                certificate,
                lambda value: dispatch._certificate_errors(
                    value, self.ontologies
                ),
                "plan",
            ),
            (
                "planning invocation",
                planning_invocation,
                dispatch._planning_invocation_errors,
                "plan",
            ),
            (
                "manifest",
                manifest,
                lambda value: dispatch._manifest_errors(value, self.ontologies),
                "plan",
            ),
            (
                "preflight invocation",
                preflight_invocation,
                dispatch._preflight_invocation_errors,
                "brief",
            ),
            (
                "preflight evidence",
                preflight_evidence,
                dispatch._preflight_evidence_errors,
                "candidate_decision",
            ),
            (
                "dispatch bundle",
                dispatch_bundle,
                dispatch._bundle_shape_errors,
                "brief",
            ),
        ]
        for label, value, validator, reference_field in cases:
            with self.subTest(contract=label, mutation="unknown key"):
                changed = copy.deepcopy(value)
                changed["unknown"] = True
                self.assertTrue(validator(changed))
            for field in value:
                with self.subTest(contract=label, mutation="missing", field=field):
                    changed = copy.deepcopy(value)
                    changed.pop(field)
                    self.assertTrue(validator(changed))
                with self.subTest(contract=label, mutation="type", field=field):
                    changed = copy.deepcopy(value)
                    changed[field] = None
                    self.assertTrue(validator(changed))
            for nested_field in ("path", "sha256", "size_bytes"):
                with self.subTest(
                    contract=label, mutation="reference key", field=nested_field
                ):
                    changed = copy.deepcopy(value)
                    changed[reference_field].pop(nested_field)
                    self.assertTrue(validator(changed))
            for invalid_path in ("/private/tmp/absolute", "briefs\\split.md"):
                with self.subTest(
                    contract=label, mutation="reference path", path=invalid_path
                ):
                    changed = copy.deepcopy(value)
                    changed[reference_field]["path"] = invalid_path
                    self.assertTrue(validator(changed))

        constant_fields = {
            "planning invocation": {
                "schema_version": "wrong",
                "phase": "controller",
                "purpose": "other",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "xhigh",
                "fork_turns": "all",
                "exit_code": 1,
                "external_writes": True,
            },
            "preflight invocation": {
                "schema_version": "wrong",
                "phase": "planning",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "fork_turns": "all",
                "exit_code": 1,
                "external_writes": True,
            },
            "preflight evidence": {
                "schema_version": "wrong",
                "outcome": "blocked",
            },
        }
        by_label = {label: (value, validator) for label, value, validator, _ in cases}
        for label, mutations in constant_fields.items():
            value, validator = by_label[label]
            for field, invalid in mutations.items():
                with self.subTest(contract=label, mutation="constant", field=field):
                    changed = copy.deepcopy(value)
                    changed[field] = invalid
                    self.assertTrue(validator(changed))

        for label in ("planning invocation", "preflight invocation"):
            value, validator = by_label[label]
            for nested_object in ("cwd", "usage"):
                for nested_field in value[nested_object]:
                    with self.subTest(
                        contract=label,
                        mutation="nested key",
                        field=f"{nested_object}.{nested_field}",
                    ):
                        changed = copy.deepcopy(value)
                        changed[nested_object].pop(nested_field)
                        self.assertTrue(validator(changed))

        invalid_selector = {
            "decision": "run",
            "trigger_ids": [
                "nondefault_worker_route",
                "safe_lane_unavailable",
            ],
        }
        self.assertTrue(compiler.validate_selector(invalid_selector))
        invalid_branch = copy.deepcopy(preflight_evidence)
        invalid_branch["blocker_ids"] = ["ambiguous_semantics"]
        self.assertTrue(dispatch._preflight_evidence_errors(invalid_branch))
        invalid_invocation_branch = copy.deepcopy(preflight_invocation)
        invalid_invocation_branch["selector"] = {
            "decision": "skip",
            "trigger_ids": [],
            "safe_lane": safe.manifest.to_dict(),
        }
        self.assertTrue(
            dispatch._preflight_invocation_errors(invalid_invocation_branch)
        )
        reversed_reasons = copy.deepcopy(certificate)
        reversed_reasons["entries"][0]["reason_ids"].reverse()
        self.assertTrue(
            dispatch._certificate_errors(reversed_reasons, self.ontologies)
        )
        self.assertTrue(
            dispatch._blocker_errors(
                ["ambiguous_semantics", "artifact_binding_invalid"],
                "blockers",
            )
        )

    def test_all_six_published_schemas_have_exact_runtime_parity_and_local_defs(self) -> None:
        expected = dispatch.expected_dispatch_schemas(self.ontologies)
        self.assertEqual(
            set(expected),
            {
                "cmr-safe-lane-certificate-v1.schema.json",
                "cmr-planning-invocation-v1.schema.json",
                "cmr-safe-lane-manifest-v1.schema.json",
                "cmr-preflight-invocation-v1.schema.json",
                "cmr-preflight-evidence-v1.schema.json",
                "cmr-dispatch-bundle-v1.schema.json",
            },
        )
        for filename, runtime_schema in expected.items():
            with self.subTest(filename=filename):
                published = json.loads(
                    (
                        SKILL_ROOT / "references" / "schemas" / filename
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(published, runtime_schema)
                self.assertEqual(
                    published["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                branches = published.get("oneOf", [published])
                for branch in branches:
                    self.assertFalse(branch["additionalProperties"])
                    self.assertEqual(
                        set(branch["required"]), set(branch["properties"])
                    )
                self.assertTrue(set(published.get("$defs", {})) <= {
                    "ArtifactRef",
                    "UsageEvidence",
                    "CwdEvidence",
                    "Selector",
                    "RunSelector",
                    "SafeLaneEntry",
                })

        invocation_names = (
            "cmr-planning-invocation-v1.schema.json",
            "cmr-preflight-invocation-v1.schema.json",
        )
        for filename in invocation_names:
            schema = expected[filename]
            usage = schema["$defs"]["UsageEvidence"]
            cwd = schema["$defs"]["CwdEvidence"]
            self.assertEqual(set(usage["required"]), set(USAGE))
            self.assertFalse(usage["additionalProperties"])
            self.assertEqual(cwd["properties"]["kind"], {"const": "ephemeral"})
            self.assertEqual(cwd["properties"]["fresh"], {"const": True})
            self.assertEqual(cwd["properties"]["destroyed"], {"const": True})
            self.assertEqual(cwd["properties"]["read_only"], {"const": True})

        selector = expected["cmr-preflight-evidence-v1.schema.json"]["$defs"][
            "Selector"
        ]
        self.assertEqual(len(selector["oneOf"]), 3)
        self.assertEqual(
            selector["oneOf"][0]["properties"]["trigger_ids"]["maxItems"], 0
        )
        self.assertEqual(
            selector["oneOf"][2]["properties"]["trigger_ids"]["prefixItems"],
            [{"const": "artifact_binding_invalid"}],
        )


if __name__ == "__main__":
    unittest.main()
