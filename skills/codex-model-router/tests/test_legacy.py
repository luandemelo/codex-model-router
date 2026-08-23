from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

from support import SKILL_ROOT, load_fixture, load_module


legacy = load_module("qfr_legacy")
dispatch = load_module("cmr_dispatch")


LEGACY_REFERENCE_NAMES = (
    "decision-record.schema.json",
    "semantic-preflight.schema.json",
    "safe-lane-certificate.schema.json",
    "safe-lane-manifest.schema.json",
    "preflight-invocation.schema.json",
)


class LegacyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("skill-pressure-scenarios.json")["legacy_records"]
        cls.reference_root = SKILL_ROOT / "references" / "legacy"

    def test_historical_schemas_are_json_documentary_references(self) -> None:
        for name in LEGACY_REFERENCE_NAMES:
            with self.subTest(schema=name):
                path = self.reference_root / name
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(value, dict)
                self.assertIn("title", value)
                self.assertIn("description", value)
                self.assertIn("documentary", value["description"].casefold())
                if name != "decision-record.schema.json":
                    serialized = json.dumps(value, sort_keys=True)
                    self.assertIn("qfr-", serialized)

    def test_validator_allowlist_matches_historical_decision_schema_properties(self) -> None:
        schema = json.loads(
            (self.reference_root / "decision-record.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(legacy.LEGACY_TOP_LEVEL_FIELDS),
            set(schema["properties"]),
        )

    def test_valid_records_return_non_authorizing_documentary_message(self) -> None:
        for name in ("valid", "valid_blocked_external_action"):
            with self.subTest(record=name):
                messages = legacy.validate_legacy_decision(self.fixture[name])
                self.assertTrue(messages)
                self.assertIn("cannot authorize dispatch", " ".join(messages))

    def test_invalid_records_fail_closed_with_specific_violations(self) -> None:
        missing = legacy.validate_legacy_decision(
            self.fixture["invalid_missing_lifecycle"]
        )
        self.assertIn("review_model is required", missing)
        self.assertIn("review_effort is required", missing)

        recurrence = legacy.validate_legacy_decision(
            self.fixture["invalid_recurrence_downroute"]
        )
        self.assertIn("recurring failure requires gpt-5.6-sol with reasoning_effort max", recurrence)

        malformed = legacy.validate_legacy_decision(self.fixture["invalid_malformed"])
        self.assertTrue(any("risk_signals" in message for message in malformed))
        self.assertEqual(
            legacy.validate_legacy_decision([]), ["record must be a JSON object"]
        )

    def test_unknown_top_level_fields_fail_closed(self) -> None:
        record = dict(self.fixture["valid"])
        record["invented_route"] = {"model": "gpt-5.6-sol", "effort": "max"}
        messages = legacy.validate_legacy_decision(record)
        self.assertIn("unsupported top-level field: invented_route", messages)

    def test_previous_route_nested_shape_and_aliases_are_closed(self) -> None:
        for field in ("previous_route", "prior_route"):
            valid_route = dict(self.fixture["valid"])
            valid_route[field] = {
                "selected_model": "gpt-5.6-luna",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "xhigh",
                "effort": "xhigh",
            }
            with self.subTest(field=field):
                self.assertIn(
                    "cannot authorize dispatch",
                    " ".join(legacy.validate_legacy_decision(valid_route)),
                )

        unknown_nested = dict(self.fixture["valid"])
        unknown_nested["previous_route"] = {"unexpected": True}
        self.assertIn(
            "previous_route contains an unsupported field",
            legacy.validate_legacy_decision(unknown_nested),
        )

        conflicting_aliases = dict(self.fixture["valid"])
        conflicting_aliases["prior_route"] = {
            "selected_model": "gpt-5.6-luna",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
        }
        self.assertIn(
            "prior_route.model aliases conflict",
            legacy.validate_legacy_decision(conflicting_aliases),
        )

    def test_correction_attempts_accepts_zero_and_positive_aggregate_counts(self) -> None:
        zero = dict(self.fixture["valid"])
        zero["correction_attempts"] = 0
        self.assertIn(
            "cannot authorize dispatch",
            " ".join(legacy.validate_legacy_decision(zero)),
        )

        one = dict(self.fixture["valid"])
        one["correction_attempts"] = 1
        self.assertIn(
            "cannot authorize dispatch",
            " ".join(legacy.validate_legacy_decision(one)),
        )

        two = dict(self.fixture["valid"])
        two.update(
            {
                "correction_attempts": 2,
                "selected_model": "gpt-5.6-sol",
                "reasoning_effort": "max",
                "review_model": "gpt-5.6-sol",
                "review_effort": "max",
            }
        )
        self.assertIn(
            "cannot authorize dispatch",
            " ".join(legacy.validate_legacy_decision(two)),
        )

    def test_correction_attempts_rejects_bool_negative_float_and_conflicts(self) -> None:
        for invalid in (True, -1, 1.5):
            with self.subTest(invalid=invalid):
                record = dict(self.fixture["valid"])
                record["correction_attempts"] = invalid
                self.assertIn(
                    "correction_attempts must be a non-negative integer or list",
                    legacy.validate_legacy_decision(record),
                )

        conflict = dict(self.fixture["valid"])
        conflict.update({"failed_attempts": 1, "correction_attempts": 0})
        self.assertIn(
            "correction_attempts conflicts with failed_attempts",
            legacy.validate_legacy_decision(conflict),
        )

        detailed_conflict = dict(self.fixture["valid"])
        detailed_conflict.update(
            {
                "correction_attempts": 0,
                "failure_history": [
                    {
                        "defect_id": "shape-regression",
                        "complete": True,
                        "outcome": "failed",
                    }
                ],
            }
        )
        self.assertIn(
            "correction_attempts conflicts with failure_history",
            legacy.validate_legacy_decision(detailed_conflict),
        )

    def test_documentary_success_does_not_authorize_any_qfr_schema_dispatch(self) -> None:
        self.assertTrue(
            any(
                "cannot authorize dispatch" in message
                for message in legacy.validate_legacy_decision(self.fixture["valid"])
            )
        )
        schema_versions = {
            "decision-record.schema.json": "qfr-decision-record-v1",
            "semantic-preflight.schema.json": "qfr-semantic-preflight-v1",
            "safe-lane-certificate.schema.json": "qfr-safe-lane-v1",
            "safe-lane-manifest.schema.json": "qfr-safe-lane-manifest-v1",
            "preflight-invocation.schema.json": "qfr-semantic-preflight-invocation-v1",
        }
        for name in LEGACY_REFERENCE_NAMES:
            with self.subTest(schema=name), tempfile.TemporaryDirectory() as temporary:
                ledger = Path(temporary).resolve()
                dispatch_dir = ledger / "tasks" / "QFR-LEGACY-001" / "dispatch"
                dispatch_dir.mkdir(parents=True)
                bundle = dispatch_dir / "bundle.json"
                bundle.write_text(
                    json.dumps({"schema_version": schema_versions[name]}) + "\n",
                    encoding="utf-8",
                )
                bundle.chmod(0o444)
                dispatch_dir.chmod(0o555)
                self.assertEqual(
                    dispatch.validate_dispatch_bundle(bundle, ledger),
                    ["legacy_dispatch_forbidden"],
                )


if __name__ == "__main__":
    unittest.main()
