from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

from support import SKILL_ROOT, load_fixture, load_module

contracts = load_module("cmr_contracts")


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("controller-results.json")
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def test_published_ontologies_equal_the_frozen_literal_sets(self) -> None:
        self.assertEqual(
            [dict(entry) for entry in self.ontologies.fact_entries],
            self.fixture["fact_ontology"],
        )
        self.assertEqual(
            list(self.ontologies.fact_ids),
            [entry["id"] for entry in self.fixture["fact_ontology"]],
        )
        self.assertEqual(
            list(self.ontologies.uncertainty_ids),
            self.fixture["uncertainty_ids"],
        )
        self.assertEqual(
            [dict(entry) for entry in self.ontologies.action_entries],
            self.fixture["action_ontology"],
        )
        self.assertEqual(
            list(self.ontologies.safe_lane_reason_ids),
            self.fixture["safe_lane_reason_ids"],
        )

    def test_occurrence_usage_schema_has_exact_five_token_counters(self) -> None:
        schema = contracts.expected_published_schemas(self.ontologies)[
            "cmr-occurrence-audit-v1.schema.json"
        ]["$defs"]["UsageEvidence"]
        fields = [
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ]
        self.assertEqual(schema["required"], fields)
        self.assertEqual(set(schema["properties"]), set(fields))
        self.assertFalse(schema["additionalProperties"])
        for field in fields:
            with self.subTest(field=field):
                self.assertEqual(
                    schema["properties"][field],
                    {"type": "integer", "minimum": 0},
                )

    def test_strict_json_object_rejects_non_object_trailing_and_duplicate_keys(self) -> None:
        self.assertEqual(
            contracts.strict_json_object(b'{"answer":1}', "answer"),
            {"answer": 1},
        )
        invalid = (
            b"[]",
            b'{"answer":1} trailing',
            b'{"answer":1,"answer":2}',
            b'{"nested":{"id":1,"id":2}}',
            b"\xff",
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as captured:
                    contracts.strict_json_object(payload, "answer")
                self.assertIn("answer", str(captured.exception))

    def test_controller_accepts_only_minimal_canonical_envelope(self) -> None:
        value = {
            "schema_version": "cmr-controller-result-v1",
            "fact_ids": ["clear_isolated_low_risk"],
            "uncertainty_ids": [],
            "action_intents": [],
        }
        self.assertEqual(contracts.validate_controller_result(value, self.ontologies), [])
        for forbidden in (
            "selected_model", "reasoning_effort", "review_model", "review_effort",
            "role", "risk_level", "hard_gates", "fork_turns", "context_mode",
            "status", "authorization", "justification",
        ):
            mutated = dict(value)
            mutated[forbidden] = "model-authored"
            self.assertIn("unknown field", "\n".join(
                contracts.validate_controller_result(mutated, self.ontologies)
            ))

    def test_controller_rejects_unknown_duplicate_and_noncanonical_ids(self) -> None:
        cases = (
            ("unknown fact", ["github"], [], []),
            ("legacy phase alias", ["canonical_planning"], [], []),
            ("duplicate fact", ["architecture", "architecture"], [], []),
            ("fact order", ["audit", "architecture"], [], []),
            ("unknown uncertainty", [], ["uncertain"], []),
            (
                "uncertainty order",
                [],
                ["controller_uncertainty", "multiple_policy_facts"],
                [],
            ),
        )
        for label, fact_ids, uncertainty_ids, action_intents in cases:
            with self.subTest(label=label):
                value = {
                    "schema_version": "cmr-controller-result-v1",
                    "fact_ids": fact_ids,
                    "uncertainty_ids": uncertainty_ids,
                    "action_intents": action_intents,
                }
                self.assertNotEqual(
                    contracts.validate_controller_result(value, self.ontologies), []
                )

    def test_controller_action_targets_follow_the_literal_target_kinds(self) -> None:
        valid_targets = {
            "github_authenticate": "github.com/account/codex-user",
            "repository_create": "owner/repo",
            "repository_settings_update": "owner/repo",
            "repository_topics_update": "owner/repo",
            "issue_create": "owner/repo",
            "issue_update": "owner/repo#12",
            "issue_comment": "owner/repo#12",
            "push": "owner/repo:refs/heads/main",
            "pr_create": "owner/repo:refs/heads/main<-refs/heads/feature",
            "pr_update": "owner/repo#12",
            "merge": "owner/repo#12",
            "tag_create": "owner/repo:refs/tags/v0.4.0",
            "release_create": "owner/repo:v0.4.0",
            "deploy": "owner/repo:production",
            "install_skill": "/opt/Codex Skills/codex-model-router",
            "install_plugin": "C:/",
            "marketplace_publish": "community/codex-model-router",
        }
        for action, target in valid_targets.items():
            with self.subTest(action=action):
                value = {
                    "schema_version": "cmr-controller-result-v1",
                    "fact_ids": [],
                    "uncertainty_ids": [],
                    "action_intents": [{"action": action, "target": target}],
                }
                self.assertEqual(
                    contracts.validate_controller_result(value, self.ontologies), []
                )

        invalid_targets = {
            "github_authenticate": "github.com/codex-user",
            "repository_create": "owner/repo/extra",
            "issue_update": "owner/repo#0",
            "push": "owner/repo:main",
            "pr_create": "owner/repo:main<-feature",
            "tag_create": "owner/repo:refs/heads/v0.4.0",
            "release_create": "owner/repo:",
            "deploy": "owner/repo:",
            "install_skill": "relative/skill",
            "install_plugin": "/opt/../plugin",
            "marketplace_publish": "community/plugin/extra",
        }
        for action, target in invalid_targets.items():
            with self.subTest(action=action):
                value = {
                    "schema_version": "cmr-controller-result-v1",
                    "fact_ids": [],
                    "uncertainty_ids": [],
                    "action_intents": [{"action": action, "target": target}],
                }
                self.assertIn(
                    "target",
                    "\n".join(
                        contracts.validate_controller_result(value, self.ontologies)
                    ),
                )

    def test_controller_allows_missing_target_but_rejects_empty_unknown_or_bad_order(self) -> None:
        base = {
            "schema_version": "cmr-controller-result-v1",
            "fact_ids": [],
            "uncertainty_ids": [],
        }
        missing_target = {
            **base,
            "action_intents": [{"action": "push"}],
        }
        self.assertEqual(
            contracts.validate_controller_result(missing_target, self.ontologies), []
        )

        invalid_action_lists = (
            [{"action": "push", "target": ""}],
            [{"action": "github"}],
            [{"action": "push"}, {"action": "push"}],
            [
                {"action": "push", "target": "owner/repo:refs/heads/b"},
                {"action": "push", "target": "owner/repo:refs/heads/a"},
            ],
            [{"action": "push"}, {"action": "issue_create", "target": "owner/repo"}],
            [{"action": "push", "authorization": True}],
        )
        for action_intents in invalid_action_lists:
            with self.subTest(action_intents=action_intents):
                self.assertNotEqual(
                    contracts.validate_controller_result(
                        {**base, "action_intents": action_intents}, self.ontologies
                    ),
                    [],
                )

    def test_preflight_accept_replace_and_block_are_exact_one_of_shapes(self) -> None:
        for outcome in ("accept", "replace", "block"):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    contracts.validate_preflight_result(
                        self.fixture["valid_preflight"][outcome], self.ontologies
                    ),
                    [],
                )

        invalid = (
            {
                "schema_version": "cmr-preflight-result-v1",
                "outcome": "accept",
                "fact_ids": [],
            },
            {
                "schema_version": "cmr-preflight-result-v1",
                "outcome": "replace",
                "fact_ids": [],
                "uncertainty_ids": [],
                "action_intents": [],
                "blocker_ids": ["ambiguous_semantics"],
            },
            {
                "schema_version": "cmr-preflight-result-v1",
                "outcome": "block",
                "blocker_ids": [],
            },
            {
                "schema_version": "cmr-preflight-result-v1",
                "outcome": "block",
                "blocker_ids": ["insufficient_task_evidence", "ambiguous_semantics"],
            },
            {"schema_version": "cmr-preflight-result-v1", "outcome": "retry"},
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertNotEqual(
                    contracts.validate_preflight_result(value, self.ontologies), []
                )

    def test_planner_uses_frozen_task_order_and_public_reason_order(self) -> None:
        task_ids = ["T-001", "T-002", "T-003"]
        value = {
            "schema_version": "cmr-planner-result-v1",
            "safe_lanes": [
                {
                    "task_id": "T-001",
                    "reason_ids": [
                        "clear_isolated_low_risk",
                        "single_module_bounded_change",
                    ],
                },
                {
                    "task_id": "T-003",
                    "reason_ids": ["approved_plan_no_open_design_decision"],
                },
            ],
        }
        self.assertEqual(
            contracts.validate_planner_result(value, task_ids, self.ontologies), []
        )
        self.assertEqual(
            contracts.validate_planner_result(
                {"schema_version": "cmr-planner-result-v1", "safe_lanes": []},
                [],
                self.ontologies,
            ),
            [],
        )

        invalid_safe_lanes = (
            [value["safe_lanes"][1], value["safe_lanes"][0]],
            [value["safe_lanes"][0], value["safe_lanes"][0]],
            [{"task_id": "T-999", "reason_ids": ["clear_isolated_low_risk"]}],
            [{"task_id": "T-001", "reason_ids": []}],
            [
                {
                    "task_id": "T-001",
                    "reason_ids": [
                        "single_module_bounded_change",
                        "clear_isolated_low_risk",
                    ],
                }
            ],
            [{"task_id": "T-001", "reason_ids": ["private_reason"]}],
            [
                {
                    "task_id": "T-001",
                    "reason_ids": ["clear_isolated_low_risk"],
                    "justification": "model-authored",
                }
            ],
        )
        for safe_lanes in invalid_safe_lanes:
            with self.subTest(safe_lanes=safe_lanes):
                self.assertNotEqual(
                    contracts.validate_planner_result(
                        {
                            "schema_version": "cmr-planner-result-v1",
                            "safe_lanes": safe_lanes,
                        },
                        task_ids,
                        self.ontologies,
                    ),
                    [],
                )

    def test_validation_errors_are_stably_sorted(self) -> None:
        errors = contracts.validate_controller_result(
            {
                "schema_version": "wrong",
                "fact_ids": ["github", "architecture"],
                "uncertainty_ids": ["unknown"],
                "action_intents": [{"action": "unknown", "extra": True}],
                "extra": True,
            },
            self.ontologies,
        )
        self.assertGreater(len(errors), 3)
        self.assertEqual(errors, sorted(errors))

    def _copy_references(self, directory: str) -> Path:
        root = Path(directory) / "skill"
        shutil.copytree(SKILL_ROOT / "references", root / "references")
        return root

    def _write_json(self, path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_ontology_loader_rejects_shape_type_order_and_exact_set_drift(self) -> None:
        mutations: tuple[tuple[str, str, Callable[[Any], None]], ...] = (
            (
                "fact order",
                "fact-ontology.json",
                lambda value: value.__setitem__(slice(0, 2), list(reversed(value[:2]))),
            ),
            (
                "fact extra key",
                "fact-ontology.json",
                lambda value: value[0].__setitem__("alias", "architecture_change"),
            ),
            (
                "fact exact set",
                "fact-ontology.json",
                lambda value: value[0].__setitem__("id", "architecture_alias"),
            ),
            (
                "fact boolean type",
                "fact-ontology.json",
                lambda value: value[0].__setitem__("hard_gate", 1),
            ),
            (
                "uncertainty order",
                "uncertainty-ontology.json",
                lambda value: value.__setitem__(slice(0, 2), list(reversed(value[:2]))),
            ),
            (
                "action exact metadata",
                "action-ontology.json",
                lambda value: value[0].__setitem__("target_required", False),
            ),
            (
                "reason exact set",
                "safe-lane-reason-ontology.json",
                lambda value: value.append("private_reason"),
            ),
        )
        for label, filename, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = self._copy_references(directory)
                path = root / "references" / filename
                value = json.loads(path.read_text(encoding="utf-8"))
                mutate(value)
                self._write_json(path, value)
                with self.assertRaises(ValueError):
                    contracts.load_ontologies(root)

    def test_published_contracts_detect_required_enum_const_and_closure_drift(self) -> None:
        self.assertEqual(
            contracts.validate_published_contracts(SKILL_ROOT, self.ontologies), []
        )
        mutations: tuple[tuple[str, str, Callable[[dict[str, Any]], None]], ...] = (
            (
                "required",
                "cmr-controller-result-v1.schema.json",
                lambda schema: schema["required"].remove("action_intents"),
            ),
            (
                "enum",
                "cmr-controller-result-v1.schema.json",
                lambda schema: schema["properties"]["fact_ids"]["items"]["enum"].append(
                    "github"
                ),
            ),
            (
                "const",
                "cmr-preflight-result-v1.schema.json",
                lambda schema: schema["oneOf"][0]["properties"]["schema_version"].__setitem__(
                    "const", "wrong"
                ),
            ),
            (
                "additional properties",
                "cmr-planner-result-v1.schema.json",
                lambda schema: schema.__setitem__("additionalProperties", True),
            ),
            (
                "one-of branch closure",
                "cmr-preflight-result-v1.schema.json",
                lambda schema: schema["oneOf"][1].__setitem__("additionalProperties", True),
            ),
        )
        for label, filename, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = self._copy_references(directory)
                path = root / "references" / "schemas" / filename
                value = json.loads(path.read_text(encoding="utf-8"))
                mutate(value)
                self._write_json(path, value)
                errors = contracts.validate_published_contracts(root, self.ontologies)
                self.assertTrue(errors)
                self.assertEqual(errors, sorted(errors))
                self.assertIn("drift", "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
