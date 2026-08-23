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

from support import SKILL_ROOT, load_fixture, load_module


compiler = load_module("cmr_compiler")
contracts = load_module("cmr_contracts")
dispatch = load_module("cmr_dispatch")
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
        model="gpt-5.6-luna",
        reasoning_effort="xhigh",
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
                    decision = compiler.compile_route(
                        evidence, scenario["runtime"], self.ontologies
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
