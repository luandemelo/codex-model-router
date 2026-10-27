from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

sys.dont_write_bytecode = True

from support import SKILL_ROOT, load_fixture, load_module, compile_fixture_route, fixture_gate_evidence, canonical_json_bytes, write_frozen_bytes


compiler = load_module("cmr_compiler")
contracts = load_module("cmr_contracts")
dispatch = load_module("cmr_dispatch")
latency = load_module("cmr_latency")
runtime_module = load_module("cmr_runtime")


USAGE = {
    "input_tokens": 31,
    "cached_input_tokens": 7,
    "cache_write_input_tokens": 2,
    "output_tokens": 13,
    "reasoning_output_tokens": 5,
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _controller_evidence(
    scenario: Mapping[str, Any], ledger: Path,
) -> dict[str, Any]:
    task_id = scenario["task_id"]
    result = copy.deepcopy(scenario["controller"])
    brief_payload = (scenario["brief"] + "\n").encode("utf-8")
    brief = compiler.ArtifactRef(
        f"briefs/{task_id}.md", _sha256(brief_payload), len(brief_payload)
    )
    brief_path = ledger / brief.path
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_bytes(brief_payload)
    brief_path.chmod(0o444)

    response = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    events = [
        {"type": "thread.started", "thread_id": f"thread-controller-{task_id}"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": f"response-{task_id}",
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
    transcript_payload = f"fresh controller transcript for {task_id}\n".encode()
    cwd = {
        "kind": "ephemeral",
        "path": f"/private/tmp/cmr-controller-{task_id}",
        "fresh": True,
        "destroyed": True,
        "read_only": True,
    }
    expected = runtime_module.OccurrenceExpectation(
        phase="controller",
        scope_kind="task",
        scope_id=task_id,
        round_index=None,
        task_id=task_id,
        model="gpt-6.1-sol",
        reasoning_effort="medium",
        prompt_sha256=brief.sha256,
        forbidden_thread_ids=(),
        prior_thread_id=None,
        fork_turns="none",
        thread_policy="fresh",
        cwd_policy=cwd,
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
        "thread_id": f"thread-controller-{task_id}",
        "cwd": copy.deepcopy(cwd),
        "write_policy": expected.write_policy,
        "exit_code": 0,
        "external_writes": False,
        "transcript_payload": transcript_payload,
        "transcript_sha256": _sha256(transcript_payload),
        "raw_sha256": _sha256(raw_payload),
        "usage": copy.deepcopy(USAGE),
        "terminal_report_sha256": None,
    }
    occurrence = runtime_module.audit_occurrence(
        raw_payload,
        metadata,
        expected,
        lambda value: contracts.validate_controller_result(value, contracts.load_ontologies(SKILL_ROOT)),
    )
    if not occurrence.accepted:
        raise AssertionError(occurrence.errors)
    raw = compiler.ArtifactRef(
        f"controller/{task_id}.raw.jsonl", _sha256(raw_payload), len(raw_payload)
    )
    return compiler.bind_controller_result(
        result,
        task_id=task_id,
        brief=brief,
        raw=raw,
        occurrence=occurrence,
    )


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("skill-pressure-scenarios.json")
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def test_pressure_scenarios_use_real_compiler_and_dispatcher(self) -> None:
        for scenario in self.fixture["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                self.assertEqual(
                    contracts.validate_controller_result(
                        scenario["controller"], self.ontologies
                    ),
                    [],
                )
                self.assertEqual(
                    compiler.validate_runtime_input(
                        scenario["runtime"], self.ontologies
                    ),
                    [],
                )
                with tempfile.TemporaryDirectory() as temporary:
                    ledger = Path(temporary).resolve()
                    evidence = _controller_evidence(scenario, ledger)
                    decision = compile_fixture_route(
                        evidence, scenario["runtime"], self.ontologies,
                        brief_bytes=(scenario["brief"] + "\n").encode("utf-8")
                    )
                    expected = scenario["expected"]
                    self.assertEqual(
                        [decision["worker"]["model"], decision["worker"]["reasoning_effort"]],
                        expected["worker"],
                    )
                    self.assertEqual(
                        [decision["review"]["model"], decision["review"]["reasoning_effort"]],
                        expected["review"],
                    )
                    self.assertEqual(
                        decision["decisive_reason_id"], expected["decisive_reason_id"]
                    )
                    external_states = [
                        item["state"] for item in decision["external_actions"]
                    ]
                    self.assertEqual(external_states, expected["external_action_states"])
                    selector = dispatch.select_preflight(
                        decision,
                        manifest_path=ledger / "safe-lane" / "manifest.json",
                        ledger_root=ledger,
                    )
                    self.assertEqual(selector, expected["selector"])

                    dispatch_dir = ledger / "tasks" / scenario["task_id"] / "dispatch"
                    dispatch_dir.mkdir(parents=True)
                    bundle = dispatch_dir / "bundle.json"
                    bundle.write_text(
                        json.dumps({"schema_version": "qfr-dispatch-bundle-v1"}) + "\n",
                        encoding="utf-8",
                    )
                    bundle.chmod(0o444)
                    dispatch_dir.chmod(0o555)
                    self.assertEqual(
                        dispatch.validate_dispatch_bundle(bundle, ledger),
                        [expected["dispatch_blocker"]],
                    )

    def test_public_skill_entrypoint_exists(self) -> None:
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())

    def test_public_guidance_describes_bound_terra_opt_in_and_011_release(self) -> None:
        plugin_root = SKILL_ROOT.parents[1]
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        policy_text = (SKILL_ROOT / "references/routing-policy.md").read_text(
            encoding="utf-8"
        )
        dependency_text = (SKILL_ROOT / "references/dependency-preflight.md").read_text(
            encoding="utf-8"
        )
        basis_text = (SKILL_ROOT / "references/policy-basis.md").read_text(
            encoding="utf-8"
        )
        execution_text = (SKILL_ROOT / "references/execution-contract.md").read_text(
            encoding="utf-8"
        )
        agents_text = (SKILL_ROOT / "agents/openai.yaml").read_text(encoding="utf-8")
        release_text = (plugin_root / "README.md").read_text(encoding="utf-8")
        checker_text = (SKILL_ROOT / "scripts/check_public_release.py").read_text(
            encoding="utf-8"
        )
        plugin = json.loads(
            (plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )

        self.assertEqual(plugin["version"], "0.11.0")
        self.assertIn("Terra", plugin["description"])
        self.assertIn("0.11.0", release_text)
        self.assertIn('"0.11.0"', checker_text)
        self.assertIn("Terra/xhigh", agents_text)
        for fragment in (
            "gpt-6.1-terra",
            "task-specific",
            "latency preference",
            "Luna/xhigh remains the default",
            "never triggers a",
            "no additional human",
            "confirmation is required for each use",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, skill_text)
        for fragment in (
            "gpt-6.1-terra/xhigh",
            "latency_preference",
            "profile_binding()",
            "does not apply to Terra",
            "Sol/medium task review",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, policy_text)
        for fragment in (
            "`gpt-6.1-terra`/xhigh is an optional dependency",
            "Require it only when the",
            "compiled route selects Terra",
            "absence does not block a default Luna route",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, dependency_text)
        for fragment in (
            "native four-task pilot",
            "limited directional sample",
            "no quality superiority",
            "not Codex subscription billing",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, basis_text)
        for fragment in (
            "cmr-runtime-input-v5",
            "cmr-route-decision-v5",
            "cmr-dispatch-request-v2",
            "cmr-execution-accounting-input-v2",
            "OccurrenceExpectation.for_dispatch",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, execution_text)

    def test_sdd_handoff_uses_validated_worker_and_generic_execution_proof(self) -> None:
        execution_text = (SKILL_ROOT / "references/execution-contract.md").read_text(
            encoding="utf-8"
        )
        handoff_text = " ".join(
            execution_text.split("## SDD handoff and reviews", 1)[1].split()
        )
        for fragment in (
            "Luna/xhigh is the default",
            "valid runtime-v5 latency preference can select Terra/xhigh",
            "when no valid latency preference is bound",
            "The validated dispatch request controls the exact worker model and effort",
            "Luna and Terra workers require independent Sol/medium task review",
            "selected worker produced the code",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, handoff_text)
        for stale_fragment in (
            "must run as Luna/xhigh",
            "runs as Luna/max",
            "Luna produced the code",
        ):
            with self.subTest(stale_fragment=stale_fragment):
                self.assertNotIn(stale_fragment, handoff_text)

    def test_profile_binding_example_matches_helper_and_control_plane_contract(self) -> None:
        execution_text = (SKILL_ROOT / "references/execution-contract.md").read_text(
            encoding="utf-8"
        )
        example_marker = "The default `routing_policy` is shown below."
        self.assertIn(example_marker, execution_text)
        example_start = execution_text.index(example_marker)
        example_tail = execution_text[example_start:].split("```json\n", 1)[1]
        example_json = example_tail.split("\n```", 1)[0]
        documented_policy = json.loads(example_json)
        actual_binding = latency.profile_binding()

        self.assertEqual(set(actual_binding), {"profile_id", "profile_sha256"})
        self.assertEqual(
            documented_policy,
            {"profile": actual_binding, "latency_preference": None},
        )
        control_plane = " ".join(
            execution_text.split("## Occurrence-specific gate evidence", 1)[0].split()
        )
        self.assertIn(
            "`cmr-runtime-input-v4` continues to compile `cmr-route-decision-v4`",
            control_plane,
        )
        self.assertNotIn("The compiler emits `cmr-runtime-input-v4`", control_plane)

    def test_host_dependency_preflight_is_before_dispatch_and_fail_closed(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        preflight_marker = "## Host dependency preflight"
        sequence_marker = "## Required sequence"
        self.assertIn(preflight_marker, skill_text)
        self.assertLess(skill_text.index(preflight_marker), skill_text.index(sequence_marker))
        required_fragments = (
            "superpowers:subagent-driven-development",
            "before the Sol controller",
            "stop before the controller",
            "ask for explicit",
            "authorization",
            "do not install automatically",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, skill_text)

    def test_dependency_recovery_steps_are_self_contained(self) -> None:
        reference = SKILL_ROOT / "references" / "dependency-preflight.md"
        self.assertTrue(reference.is_file())
        reference_text = reference.read_text(encoding="utf-8")
        for fragment in (
            "Codex app",
            "Plugins",
            "Codex CLI",
            "/plugins",
            "fresh conversation",
            "$superpowers:subagent-driven-development",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, reference_text)

    def test_public_cli_entrypoint_is_declared(self) -> None:
        cli = SKILL_ROOT / "scripts" / "codex_model_router.py"
        checker = SKILL_ROOT / "scripts" / "check_public_release.py"
        self.assertTrue(cli.is_file())
        self.assertTrue(checker.is_file())

    def test_quick_validator_is_the_frontmatter_contract(self) -> None:
        validator = Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
        if not validator.is_file():
            self.skipTest("official quick validator unavailable on this host")
        completed = subprocess.run(
            [sys.executable, str(validator), str(SKILL_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode and "No module named 'yaml'" in completed.stderr:
            self.skipTest("official quick validator dependency unavailable: PyYAML")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_quick_validator_is_skipped_when_tool_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(Path, "home", return_value=Path(temporary)):
                with self.assertRaisesRegex(
                    unittest.SkipTest,
                    "official quick validator unavailable on this host",
                ):
                    self.test_quick_validator_is_the_frontmatter_contract()


if __name__ == "__main__":
    unittest.main()
