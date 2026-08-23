from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SKILL_ROOT, load_fixture


SCRIPT_ROOT = SKILL_ROOT / "scripts"
CLI = SCRIPT_ROOT / "codex_model_router.py"
CHECKER = SCRIPT_ROOT / "check_public_release.py"
MANIFEST = SKILL_ROOT / "references" / "public-file-manifest.json"
PLUGIN_ROOT = SKILL_ROOT.parents[1]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(CLI), *args],
        cwd=str(PLUGIN_ROOT.parent),
        capture_output=True,
        text=True,
        check=False,
    )


def _closed_jsonl(thread_id: str, response: object, usage: object) -> str:
    events = (
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "item-response",
                "type": "agent_message",
                "text": json.dumps(
                    response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        },
        {"type": "turn.completed", "usage": usage},
    )
    return "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in events
    )


def _planning_occurrence_record() -> dict:
    return {
        "schema_version": "cmr-occurrence-audit-v1",
        "phase": "planning",
        "scope_kind": "plan",
        "scope_id": "a" * 64,
        "round_index": None,
        "task_id": None,
        "response_policy": "closed_json",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "max",
        "fork_turns": "none",
        "prompt_sha256": "b" * 64,
        "thread_policy": "fresh",
        "thread_id": "thread-planning-ledger-001",
        "cwd": {
            "kind": "ephemeral",
            "path": "/private/tmp/cmr-planning-ledger",
            "fresh": True,
            "destroyed": True,
            "read_only": True,
        },
        "write_policy": "control_plane_no_write",
        "exit_code": 0,
        "external_writes": False,
        "transcript_sha256": "c" * 64,
        "raw_sha256": "d" * 64,
        "usage": {
            "input_tokens": 101,
            "cached_input_tokens": 17,
            "cache_write_input_tokens": 5,
            "output_tokens": 23,
            "reasoning_output_tokens": 7,
        },
        "tool_event_count": 0,
        "terminal_report_sha256": None,
        "ignored_empty_agent_messages": 0,
        "accepted": True,
        "failure_class": None,
        "errors": [],
    }


class PublicReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.controllers = load_fixture("controller-results.json")
        cls.legacy = load_fixture("skill-pressure-scenarios.json")["legacy_records"]
        cls.occurrences = load_fixture("occurrence-events.json")
        cls.accounting = load_fixture("accounting-cases.json")

    def _assert_json_result(self, completed: subprocess.CompletedProcess[str]) -> dict:
        self.assertNotIn("Traceback", completed.stderr)
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        value = json.loads(completed.stdout)
        self.assertIsInstance(value, dict)
        return value

    def _run_checker(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CHECKER), str(root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def _copy_public_tree(self, temporary: str) -> Path:
        copy = Path(temporary) / "codex-model-router"

        def ignore_root_git(directory: str, names: list[str]) -> set[str]:
            if Path(directory) == PLUGIN_ROOT and ".git" in names:
                return {".git"}
            return set()

        shutil.copytree(PLUGIN_ROOT, copy, ignore=ignore_root_git)
        return copy

    def _planning_cli_input(
        self,
        *,
        response: object | None = None,
        task_ids: object = ("T-001",),
    ) -> tuple[str, dict, dict]:
        plan_sha256 = hashlib.sha256(b"# Frozen plan\n").hexdigest()
        prompt_sha256 = hashlib.sha256(b"canonical planning brief\n").hexdigest()
        cwd = {
            "kind": "ephemeral",
            "path": "/private/tmp/cmr-cli-planning",
            "fresh": True,
            "destroyed": True,
            "read_only": True,
        }
        transcript = "planning transcript\n"
        planner = response if response is not None else self.controllers["valid_planner"]
        raw = _closed_jsonl(
            "thread-planning-cli-001", planner, self.occurrences["usage"]
        )
        metadata = {
            "phase": "planning",
            "scope_kind": "plan",
            "scope_id": plan_sha256,
            "round_index": None,
            "task_id": None,
            "response_policy": "closed_json",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "fork_turns": "none",
            "prompt_sha256": prompt_sha256,
            "thread_policy": "fresh",
            "thread_id": "thread-planning-cli-001",
            "cwd": cwd,
            "write_policy": "control_plane_no_write",
            "exit_code": 0,
            "external_writes": False,
            "transcript_payload": transcript,
            "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "usage": self.occurrences["usage"],
            "terminal_report_sha256": None,
            "plan_sha256": plan_sha256,
        }
        expected = {
            "phase": "planning",
            "scope_kind": "plan",
            "scope_id": plan_sha256,
            "round_index": None,
            "task_id": None,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "prompt_sha256": prompt_sha256,
            "forbidden_thread_ids": [],
            "prior_thread_id": None,
            "fork_turns": "none",
            "thread_policy": "fresh",
            "cwd_policy": cwd,
            "write_policy": "control_plane_no_write",
            "response_policy": "closed_json",
            "planning_task_ids": list(task_ids) if isinstance(task_ids, tuple) else task_ids,
        }
        return raw, metadata, expected

    def _run_audit_files(
        self,
        root: Path,
        raw: str,
        metadata: object,
        expected: object,
    ) -> subprocess.CompletedProcess[str]:
        raw_path = root / "occurrence.jsonl"
        metadata_path = root / "metadata.json"
        expected_path = root / "expected.json"
        raw_path.write_text(raw, encoding="utf-8")
        _write_json(metadata_path, metadata)
        _write_json(expected_path, expected)
        return _run_cli(
            "audit-occurrence",
            str(raw_path),
            str(metadata_path),
            str(expected_path),
        )

    def _run_call_shape_record(
        self,
        root: Path,
        record: dict,
    ) -> subprocess.CompletedProcess[str]:
        request = root / "call-shape.json"
        _write_json(
            request,
            {
                "occurrences": [record],
                "task_states": [],
                "plan_sha256": "a" * 64,
                "release_review": None,
            },
        )
        return _run_cli("check-call-shape", str(request))

    def test_cli_validates_controller_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "controller.json"
            _write_json(record, self.controllers["valid_controller"])
            completed = _run_cli("validate-controller", str(record))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._assert_json_result(completed)
        self.assertEqual(result["command"], "validate-controller")
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_cli_compile_rejects_model_authored_route_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = Path(temporary) / "compile.json"
            evidence = dict(self.controllers["valid_controller"])
            evidence["worker"] = {"model": "gpt-5.6-sol", "reasoning_effort": "max"}
            _write_json(request, {"evidence": evidence, "runtime": {}})
            completed = _run_cli("compile", str(request))
        self.assertEqual(completed.returncode, 1)
        result = self._assert_json_result(completed)
        self.assertEqual(result["command"], "compile")
        self.assertFalse(result["valid"])
        self.assertTrue(any("unknown field" in error for error in result["errors"]))

    def test_cli_legacy_is_documentary_and_dispatch_rejects_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = root / "legacy.json"
            _write_json(record, self.legacy["valid"])
            documentary = _run_cli("validate-legacy", str(record))
            dispatch_dir = root / "tasks" / "LEGACY-001" / "dispatch"
            dispatch_dir.mkdir(parents=True)
            bundle = dispatch_dir / "bundle.json"
            _write_json(bundle, {"schema_version": "qfr-dispatch-bundle-v1"})
            bundle.chmod(0o444)
            dispatch_dir.chmod(0o555)
            rejected = _run_cli("validate-dispatch", str(bundle), str(root))
        self.assertEqual(documentary.returncode, 0, documentary.stderr)
        documentary_result = self._assert_json_result(documentary)
        self.assertTrue(documentary_result["valid"])
        self.assertTrue(documentary_result["documentary"])
        self.assertIn("cannot authorize dispatch", " ".join(documentary_result["messages"]))
        self.assertEqual(rejected.returncode, 1, rejected.stderr)
        rejected_result = self._assert_json_result(rejected)
        self.assertIn("legacy_dispatch_forbidden", rejected_result["errors"])

    def test_cli_audit_preserves_usage_for_rejected_call(self) -> None:
        raw = self.occurrences["events"]["controller_tool"]
        usage = self.occurrences["usage"]
        prompt = b"controller brief\n"
        transcript = "controller transcript\n"
        metadata = {
            "phase": "controller",
            "scope_kind": "task",
            "scope_id": "TASK-001",
            "round_index": None,
            "task_id": "TASK-001",
            "response_policy": "closed_json",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "xhigh",
            "fork_turns": "none",
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "thread_policy": "fresh",
            "cwd": {
                "kind": "ephemeral",
                "path": "/private/tmp/cmr-cli-controller",
                "fresh": True,
                "destroyed": True,
                "read_only": True,
            },
            "write_policy": "control_plane_no_write",
            "exit_code": 0,
            "external_writes": False,
            "transcript_payload": transcript,
            "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "usage": usage,
            "terminal_report_sha256": None,
        }
        expected = {
            "phase": "controller",
            "scope_kind": "task",
            "scope_id": "TASK-001",
            "round_index": None,
            "task_id": "TASK-001",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "xhigh",
            "prompt_sha256": metadata["prompt_sha256"],
            "forbidden_thread_ids": [],
            "prior_thread_id": None,
            "fork_turns": "none",
            "thread_policy": "fresh",
            "cwd_policy": metadata["cwd"],
            "write_policy": "control_plane_no_write",
            "response_policy": "closed_json",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "controller.jsonl"
            metadata_path = root / "metadata.json"
            expected_path = root / "expected.json"
            raw_path.write_text(raw, encoding="utf-8")
            _write_json(metadata_path, metadata)
            _write_json(expected_path, expected)
            completed = _run_cli(
                "audit-occurrence",
                str(raw_path),
                str(metadata_path),
                str(expected_path),
            )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = self._assert_json_result(completed)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["usage"], usage)
        self.assertGreater(result["tool_event_count"], 0)

    def test_cli_audits_published_planner_with_explicit_plan_task_order(self) -> None:
        raw, metadata, expected = self._planning_cli_input()
        with tempfile.TemporaryDirectory() as temporary:
            completed = self._run_audit_files(
                Path(temporary), raw, metadata, expected
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._assert_json_result(completed)
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["failure_class"])
        self.assertEqual(result["errors"], [])

    def test_cli_planning_task_order_is_closed_exact_and_not_response_derived(self) -> None:
        invalid_contexts = (
            ("missing", None, "planning_task_ids is required"),
            ("duplicate", ["T-001", "T-001"], "must be unique"),
            ("array-type", "T-001", "must be a JSON array"),
            ("item-type", ["T-001", 2], "must contain only non-empty strings"),
        )
        for label, task_ids, expected_error in invalid_contexts:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                raw, metadata, expected = self._planning_cli_input()
                if task_ids is None:
                    expected.pop("planning_task_ids")
                else:
                    expected["planning_task_ids"] = task_ids
                completed = self._run_audit_files(
                    Path(temporary), raw, metadata, expected
                )
                self.assertEqual(completed.returncode, 1)
                result = self._assert_json_result(completed)
                self.assertIn(expected_error, "\n".join(result["errors"]))

        response_cases = (
            (
                "membership",
                ["T-001"],
                {
                    "schema_version": "cmr-planner-result-v1",
                    "safe_lanes": [
                        {
                            "task_id": "T-002",
                            "reason_ids": ["clear_isolated_low_risk"],
                        }
                    ],
                },
                "not in the frozen plan",
            ),
            (
                "order",
                ["T-001", "T-002"],
                {
                    "schema_version": "cmr-planner-result-v1",
                    "safe_lanes": [
                        {
                            "task_id": "T-002",
                            "reason_ids": ["clear_isolated_low_risk"],
                        },
                        {
                            "task_id": "T-001",
                            "reason_ids": ["single_module_bounded_change"],
                        },
                    ],
                },
                "must follow frozen-plan task order",
            ),
            (
                "omission",
                ["T-001"],
                {"schema_version": "cmr-planner-result-v1"},
                "missing field",
            ),
            (
                "addition",
                ["T-001"],
                {
                    "schema_version": "cmr-planner-result-v1",
                    "safe_lanes": [],
                    "selected_model": "gpt-5.6-sol",
                },
                "unknown field",
            ),
        )
        for label, task_ids, response, expected_error in response_cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                raw, metadata, expected = self._planning_cli_input(
                    response=response, task_ids=tuple(task_ids)
                )
                completed = self._run_audit_files(
                    Path(temporary), raw, metadata, expected
                )
                self.assertEqual(completed.returncode, 1)
                result = self._assert_json_result(completed)
                self.assertEqual(result["failure_class"], "operational")
                self.assertIn(expected_error, "\n".join(result["errors"]))

    def test_cli_forbids_planning_task_order_on_other_phases(self) -> None:
        raw = self.occurrences["events"]["controller_json"]
        usage = self.occurrences["usage"]
        prompt = b"controller brief\n"
        transcript = "controller transcript\n"
        cwd = {
            "kind": "ephemeral",
            "path": "/private/tmp/cmr-cli-controller",
            "fresh": True,
            "destroyed": True,
            "read_only": True,
        }
        metadata = {
            "phase": "controller",
            "scope_kind": "task",
            "scope_id": "TASK-001",
            "round_index": None,
            "task_id": "TASK-001",
            "response_policy": "closed_json",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "xhigh",
            "fork_turns": "none",
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "thread_policy": "fresh",
            "thread_id": "thread-controller-001",
            "cwd": cwd,
            "write_policy": "control_plane_no_write",
            "exit_code": 0,
            "external_writes": False,
            "transcript_payload": transcript,
            "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "usage": usage,
            "terminal_report_sha256": None,
        }
        expected = {
            "phase": "controller",
            "scope_kind": "task",
            "scope_id": "TASK-001",
            "round_index": None,
            "task_id": "TASK-001",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "xhigh",
            "prompt_sha256": metadata["prompt_sha256"],
            "forbidden_thread_ids": [],
            "prior_thread_id": None,
            "fork_turns": "none",
            "thread_policy": "fresh",
            "cwd_policy": cwd,
            "write_policy": "control_plane_no_write",
            "response_policy": "closed_json",
            "planning_task_ids": ["TASK-001"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            completed = self._run_audit_files(
                Path(temporary), raw, metadata, expected
            )
        self.assertEqual(completed.returncode, 1)
        result = self._assert_json_result(completed)
        self.assertIn(
            "planning_task_ids is only valid for planning",
            "\n".join(result["errors"]),
        )

    def test_cli_check_call_shape_accepts_accounting_file(self) -> None:
        payload = {
            "occurrences": [],
            "task_states": self.accounting["tasks"],
            "plan_sha256": self.accounting["plan_sha256"],
            "release_review": self.accounting["release_review"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            request = Path(temporary) / "accounting.json"
            _write_json(request, payload)
            completed = _run_cli("check-call-shape", str(request))
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = self._assert_json_result(completed)
        self.assertEqual(result["command"], "check-call-shape")
        self.assertFalse(result["valid"])
        self.assertTrue(any("missing" in error for error in result["errors"]))

    def test_cli_check_call_shape_rejects_unknown_serialized_audit_field(self) -> None:
        record = _planning_occurrence_record()
        record["unknown_field"] = "fabricated"
        with tempfile.TemporaryDirectory() as temporary:
            completed = self._run_call_shape_record(Path(temporary), record)
        self.assertEqual(completed.returncode, 1)
        result = self._assert_json_result(completed)
        self.assertFalse(result["valid"])
        self.assertIn("unknown fields: unknown_field", "\n".join(result["errors"]))

    def test_cli_check_call_shape_rejects_duplicate_json_keys(self) -> None:
        payload = {
            "occurrences": [_planning_occurrence_record()],
            "task_states": [],
            "plan_sha256": "a" * 64,
            "release_review": None,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        encoded = encoded.replace(
            '"schema_version":"cmr-occurrence-audit-v1"',
            '"schema_version":"cmr-occurrence-audit-v1",'
            '"schema_version":"cmr-occurrence-audit-v1"',
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            request = Path(temporary) / "duplicate.json"
            request.write_text(encoded + "\n", encoding="utf-8")
            completed = _run_cli("check-call-shape", str(request))
        self.assertEqual(completed.returncode, 1)
        result = self._assert_json_result(completed)
        self.assertFalse(result["valid"])
        self.assertIn("duplicate JSON key: schema_version", "\n".join(result["errors"]))

    def test_cli_check_call_shape_strictly_rejects_audit_contract_drift(self) -> None:
        mutations = (
            ("missing", lambda value: value.pop("raw_sha256"), "missing fields: raw_sha256"),
            (
                "model",
                lambda value: value.__setitem__("model", "gpt-5.6-terra"),
                "model is outside the public route matrix",
            ),
            (
                "effort",
                lambda value: value.__setitem__("reasoning_effort", "high"),
                "reasoning_effort is outside the public route matrix",
            ),
            (
                "response-policy",
                lambda value: value.__setitem__("response_policy", "sdd_report"),
                "response_policy conflicts with phase",
            ),
            (
                "response-policy-type",
                lambda value: value.__setitem__("response_policy", []),
                "response_policy must be closed_json or sdd_report",
            ),
            (
                "write-policy",
                lambda value: value.__setitem__("write_policy", "worktree_write"),
                "write_policy conflicts with phase",
            ),
            (
                "cwd-path",
                lambda value: value["cwd"].__setitem__("path", "relative/path"),
                "cwd.path must be an absolute normalized path",
            ),
            (
                "cwd-field",
                lambda value: value["cwd"].__setitem__("unknown", True),
                "cwd has unknown or missing fields",
            ),
            (
                "prompt-hash",
                lambda value: value.__setitem__("prompt_sha256", "A" * 64),
                "prompt_sha256 must be lowercase SHA-256",
            ),
            (
                "transcript-hash",
                lambda value: value.__setitem__("transcript_sha256", "short"),
                "transcript_sha256 must be lowercase SHA-256 or null",
            ),
            (
                "accepted-state",
                lambda value: value.__setitem__("failure_class", "operational"),
                "accepted audit must have null failure_class and empty errors",
            ),
            (
                "rejected-state",
                lambda value: value.update(
                    {"accepted": False, "failure_class": None, "errors": []}
                ),
                "rejected audit must have a failure_class and non-empty errors",
            ),
            (
                "exit-bool",
                lambda value: value.__setitem__("exit_code", True),
                "exit_code must be an integer",
            ),
            (
                "tool-count-bool",
                lambda value: value.__setitem__("tool_event_count", False),
                "tool_event_count must be a nonnegative integer",
            ),
            (
                "ignored-count-bool",
                lambda value: value.__setitem__("ignored_empty_agent_messages", True),
                "ignored_empty_agent_messages must be a nonnegative integer",
            ),
            (
                "usage-bool",
                lambda value: value["usage"].__setitem__("input_tokens", True),
                "usage.input_tokens must be a nonnegative integer",
            ),
            (
                "round-bool",
                lambda value: value.__setitem__("round_index", False),
                "round_index must be an integer from 0 through 5 or null",
            ),
            (
                "errors-duplicate",
                lambda value: value.update(
                    {
                        "accepted": False,
                        "failure_class": "operational",
                        "errors": ["rejected", "rejected"],
                    }
                ),
                "errors must contain unique non-empty strings",
            ),
        )
        for label, mutate, expected_error in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                record = copy.deepcopy(_planning_occurrence_record())
                mutate(record)
                completed = self._run_call_shape_record(Path(temporary), record)
                self.assertEqual(completed.returncode, 1)
                result = self._assert_json_result(completed)
                self.assertFalse(result["valid"])
                self.assertIn(expected_error, "\n".join(result["errors"]))

    def test_cli_check_call_shape_accepts_canonical_rejected_audit(self) -> None:
        record = _planning_occurrence_record()
        record.update(
            {
                "tool_event_count": 1,
                "accepted": False,
                "failure_class": "operational",
                "errors": ["closed_json contains disallowed tool_call item"],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = self._run_call_shape_record(Path(temporary), record)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._assert_json_result(completed)
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_public_checker_accepts_source_tree(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", str(CHECKER), str(PLUGIN_ROOT)],
            cwd=str(PLUGIN_ROOT.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["valid"])

    def test_public_checker_rejects_unlisted_file_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "codex-model-router"
            shutil.copytree(PLUGIN_ROOT, copy, symlinks=True)
            extra = copy / "unexpected.txt"
            extra.write_text("public fixture\n", encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, "-B", str(CHECKER), str(copy)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("unlisted", json.loads(rejected.stdout)["errors"][0])
            extra.unlink()
            os.symlink(copy / "README.md", copy / "symlink.md")
            rejected_link = subprocess.run(
                [sys.executable, "-B", str(CHECKER), str(copy)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(rejected_link.returncode, 1)
        self.assertIn("symlink", " ".join(json.loads(rejected_link.stdout)["errors"]))

    def test_public_checker_rejects_missing_listed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = self._copy_public_tree(temporary)
            (copy / "README.md").unlink()
            rejected = self._run_checker(copy)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("missing", " ".join(json.loads(rejected.stdout)["errors"]))

    def test_public_checker_rejects_nonregular_file_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = self._copy_public_tree(temporary)
            cache = copy / ".cache"
            cache.mkdir()
            (cache / "generated.txt").write_text("cache\n", encoding="utf-8")
            fifo = copy / "named-pipe"
            if hasattr(os, "mkfifo"):
                os.mkfifo(fifo)
            rejected = self._run_checker(copy)
        errors = " ".join(json.loads(rejected.stdout)["errors"])
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("cache", errors)
        if hasattr(os, "mkfifo"):
            self.assertIn("non-regular", errors)

    def test_public_checker_rejects_macos_and_windows_user_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = self._copy_public_tree(temporary)
            readme = copy / "README.md"
            mac_path = "/" + "Users" + "/alice/private.txt"
            windows_path = "C:" + "\\" + "Users" + "\\" + "Alice\\secret.txt"
            readme.write_text(
                readme.read_text(encoding="utf-8")
                + "\nmacOS fixture: " + mac_path + "\n"
                + "Windows fixture: " + windows_path + "\n",
                encoding="utf-8",
            )
            rejected = self._run_checker(copy)
        self.assertEqual(rejected.returncode, 1)
        errors = " ".join(json.loads(rejected.stdout)["errors"])
        self.assertIn("absolute user path", errors)

    def test_public_checker_rejects_campaign_payload_private_and_oracle_paths(self) -> None:
        cases = (
            ("campaign/evidence/secret.txt", "campaign payload"),
            ("evaluation/evidence/secret.txt", "campaign payload"),
            ("campaign/raw/secret.txt", "campaign payload"),
            ("private/secret.txt", "private"),
            ("fixtures/pressure-oracle.json", "oracle"),
            ("fixtures/pressure-score.json", "score"),
        )
        for relative, expected in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                copy = self._copy_public_tree(temporary)
                payload = copy / relative
                payload.parent.mkdir(parents=True, exist_ok=True)
                payload.write_text("not public\n", encoding="utf-8")
                rejected = self._run_checker(copy)
                self.assertEqual(rejected.returncode, 1)
                errors = " ".join(json.loads(rejected.stdout)["errors"])
                self.assertIn(expected, errors)

    def test_public_checker_rejects_bad_or_missing_apache_license(self) -> None:
        for mode in ("missing", "bad"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                copy = self._copy_public_tree(temporary)
                license_path = copy / "LICENSE"
                if mode == "missing":
                    license_path.unlink()
                else:
                    license_path.write_text("proprietary terms\n", encoding="utf-8")
                rejected = self._run_checker(copy)
                self.assertEqual(rejected.returncode, 1)
                errors = " ".join(json.loads(rejected.stdout)["errors"])
                self.assertIn("license", errors.casefold())

    def test_public_checker_rejects_plugin_and_skill_name_version_drift(self) -> None:
        mutations = (
            ("plugin-name", lambda value: value.__setitem__("name", "other-plugin")),
            ("plugin-version", lambda value: value.__setitem__("version", "9.9.9")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                copy = self._copy_public_tree(temporary)
                plugin_path = copy / ".codex-plugin/plugin.json"
                plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
                mutate(plugin)
                _write_json(plugin_path, plugin)
                rejected = self._run_checker(copy)
                self.assertEqual(rejected.returncode, 1)
                self.assertTrue(json.loads(rejected.stdout)["errors"])

        with tempfile.TemporaryDirectory() as temporary:
            copy = self._copy_public_tree(temporary)
            skill_path = copy / "skills/codex-model-router/SKILL.md"
            skill_path.write_text(
                skill_path.read_text(encoding="utf-8").replace(
                    "name: codex-model-router", "name: other-skill", 1
                ),
                encoding="utf-8",
            )
            rejected = self._run_checker(copy)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("skill", " ".join(json.loads(rejected.stdout)["errors"]))

    def test_public_checker_rejects_missing_dependency_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = self._copy_public_tree(temporary)
            dependencies = copy / "docs/dependencies.md"
            dependencies.write_text(
                dependencies.read_text(encoding="utf-8").replace("GitHub CLI", "", 1),
                encoding="utf-8",
            )
            rejected = self._run_checker(copy)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("GitHub CLI", " ".join(json.loads(rejected.stdout)["errors"]))

    def test_public_checker_rejects_nonstdlib_import_and_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = self._copy_public_tree(temporary)
            script = copy / "skills/codex-model-router/scripts/cmr_runtime.py"
            script.write_text(
                script.read_text(encoding="utf-8") + "\nimport requests\n",
                encoding="utf-8",
            )
            rejected_import = self._run_checker(copy)
            self.assertEqual(rejected_import.returncode, 1)
            self.assertIn("non-stdlib", " ".join(json.loads(rejected_import.stdout)["errors"]))

            manifest = copy / "skills/codex-model-router/references/action-ontology.json"
            manifest.write_text("{bad json\n", encoding="utf-8")
            rejected_json = self._run_checker(copy)
        self.assertEqual(rejected_json.returncode, 1)
        self.assertIn("invalid JSON", " ".join(json.loads(rejected_json.stdout)["errors"]))

    def test_public_checker_allows_root_git_metadata_but_rejects_nested_git(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = self._copy_public_tree(temporary)
            root_git = copy / ".git"
            root_git.mkdir()
            (root_git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            accepted = self._run_checker(copy)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

            nested_git = copy / "docs/.git"
            nested_git.mkdir()
            (nested_git / "secret.txt").write_text("metadata\n", encoding="utf-8")
            rejected = self._run_checker(copy)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("nested .git", " ".join(json.loads(rejected.stdout)["errors"]))

    def test_public_checker_rejects_private_markers_and_allows_runtime_words(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "codex-model-router"
            shutil.copytree(PLUGIN_ROOT, copy)
            readme = copy / "README.md"
            original = readme.read_text(encoding="utf-8")
            readme.write_text(original + "\nThis raw transcript ledger is public documentation.\n", encoding="utf-8")
            allowed = subprocess.run(
                [sys.executable, "-B", str(CHECKER), str(copy)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            token = "ghp_" + "123456789012345678901234567890123456"
            readme.write_text(original + "\nprivate bearer " + token + "\n", encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, "-B", str(CHECKER), str(copy)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("secret", " ".join(json.loads(rejected.stdout)["errors"]))


if __name__ == "__main__":
    unittest.main()
