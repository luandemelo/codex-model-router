from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from support import FIXTURES_ROOT, SKILL_ROOT, load_module

contracts = load_module("cmr_contracts")
gates = load_module("cmr_gate_evidence")


class GateEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(
            (FIXTURES_ROOT / "gate-evidence-cases.json").read_text(encoding="utf-8")
        )
        cls.schema_ontologies = contracts.load_ontologies(SKILL_ROOT)
        cls.ontologies = contracts.Ontologies(
            fact_entries=(
                {"id": "hard_change", "hard_gate": True, "routing_class": "ordinary"},
                {"id": "integration_change", "hard_gate": False, "routing_class": "integration"},
                {"id": "elevated_change", "hard_gate": False, "routing_class": "elevated_risk"},
                {"id": "ordinary_choice", "hard_gate": False, "routing_class": "ordinary"},
            ),
            uncertainty_ids=(),
            action_entries=(),
            safe_lane_reason_ids=(),
        )

    def case(self, key: str = "valid_multigate") -> tuple[dict[str, Any], Any, bytes]:
        source = copy.deepcopy(self.fixture[key])
        brief_bytes = source["brief_text"].encode("utf-8")
        brief = artifact_ref(f"tasks/{source['task_id']}/brief.md", brief_bytes)
        raw_bytes = b'{"kind":"audited-source"}\n'
        source_raw = artifact_ref(
            f"tasks/{source['task_id']}/source/raw.json", raw_bytes
        )
        context = gates.GateEvidenceContext(
            task_id=source["task_id"],
            task_phase=source["task_phase"],
            brief=brief,
            source_phase=source["source_phase"],
            source_raw=source_raw,
            source_thread_id=source["source_thread_id"],
            semantic=copy.deepcopy(source["semantic"]),
        )
        claims = []
        for claim in source["claims"]:
            start = brief_bytes.index(claim["quote"].encode("utf-8"))
            end = start + len(claim["quote"].encode("utf-8"))
            claims.append(
                {
                    "fact_id": claim["fact_id"],
                    "basis": claim["basis"],
                    "start_byte": start,
                    "end_byte": end,
                    "quote": claim["quote"],
                    "explanation": claim["explanation"],
                }
            )
        value = {
            "schema_version": "cmr-gate-evidence-v1",
            "task_id": source["task_id"],
            "task_phase": source["task_phase"],
            "brief": brief,
            "source": {
                "phase": source["source_phase"],
                "raw": source_raw,
                "thread_id": source["source_thread_id"],
                "semantic_sha256": semantic_digest(source["semantic"]),
            },
            "claims": claims,
        }
        return value, context, brief_bytes

    def assert_invalid(
        self,
        value: Any,
        context: Any,
        brief_bytes: Any,
        *needles: str,
    ) -> list[str]:
        errors = gates.validate_gate_evidence(
            value,
            context=context,
            brief_bytes=brief_bytes,
            ontologies=self.ontologies,
        )
        self.assertTrue(errors, "malformed evidence unexpectedly validated")
        self.assertEqual(errors, sorted(set(errors)))
        joined = "\n".join(errors)
        for needle in needles:
            self.assertIn(needle, joined)
        return errors

    def test_required_ids_come_from_ontology_metadata(self) -> None:
        self.assertEqual(
            gates.required_gate_ids(
                ["ordinary_choice", "elevated_change", "hard_change", "integration_change"],
                self.ontologies,
            ),
            ("elevated_change", "hard_change", "integration_change"),
        )

    def test_valid_multigate_evidence_binds_all_required_claims(self) -> None:
        value, context, brief_bytes = self.case()
        self.assertEqual(
            gates.validate_gate_evidence(
                value,
                context=context,
                brief_bytes=brief_bytes,
                ontologies=self.ontologies,
            ),
            [],
        )
        bound = gates.bind_gate_evidence(
            value,
            context=context,
            brief_bytes=brief_bytes,
            ontologies=self.ontologies,
        )
        self.assertIsInstance(bound, gates.BoundGateEvidence)
        self.assertEqual(bound.to_dict(), value)
        self.assertIsNone(bound.assert_matches(context, brief_bytes, self.ontologies))

    def test_valid_empty_artifact_binds_only_when_no_gate_fact_is_asserted(self) -> None:
        value, context, brief_bytes = self.case("valid_empty")
        self.assertEqual(value["claims"], [])
        self.assertEqual(
            gates.validate_gate_evidence(
                value,
                context=context,
                brief_bytes=brief_bytes,
                ontologies=self.ontologies,
            ),
            [],
        )
        bound = gates.bind_gate_evidence(
            value,
            context=context,
            brief_bytes=brief_bytes,
            ontologies=self.ontologies,
        )
        self.assertEqual(bound.to_dict()["claims"], [])

    def test_empty_claims_cannot_omit_required_facts(self) -> None:
        value, context, brief_bytes = self.case()
        value["claims"] = []
        self.assert_invalid(value, context, brief_bytes, "coverage")

    def test_missing_claim_field_and_unknown_object_fields_are_rejected(self) -> None:
        value, context, brief_bytes = self.case()
        missing_top = copy.deepcopy(value)
        del missing_top["task_phase"]
        self.assert_invalid(missing_top, context, brief_bytes, "missing field task_phase")

        missing_source = copy.deepcopy(value)
        del missing_source["source"]["thread_id"]
        self.assert_invalid(missing_source, context, brief_bytes, "missing field thread_id")

        missing = copy.deepcopy(value)
        del missing["claims"][0]["explanation"]
        self.assert_invalid(missing, context, brief_bytes, "missing field explanation")

        unknown = copy.deepcopy(value)
        unknown["unexpected"] = True
        unknown["source"]["extra"] = "x"
        unknown["claims"][0]["note"] = "x"
        errors = self.assert_invalid(unknown, context, brief_bytes, "unknown field")
        self.assertEqual(errors, sorted(set(errors)))

    def test_duplicate_extra_unordered_and_unknown_claims_are_rejected(self) -> None:
        value, context, brief_bytes = self.case()

        duplicate = copy.deepcopy(value)
        duplicate["claims"].append(copy.deepcopy(duplicate["claims"][0]))
        self.assert_invalid(duplicate, context, brief_bytes, "duplicate")

        extra = copy.deepcopy(value)
        extra["claims"].append(
            {
                "fact_id": "ordinary_choice",
                "basis": "requested_change",
                "start_byte": 0,
                "end_byte": 7,
                "quote": "Implement",
                "explanation": "An ordinary fact is not a gate.",
            }
        )
        self.assert_invalid(extra, context, brief_bytes, "extra")

        unordered = copy.deepcopy(value)
        unordered["claims"].reverse()
        self.assert_invalid(unordered, context, brief_bytes, "sorted")

        unknown = copy.deepcopy(value)
        unknown["claims"][0]["fact_id"] = "unknown_gate"
        self.assert_invalid(unknown, context, brief_bytes, "unknown fact")

    def test_wrong_basis_empty_quote_and_empty_explanation_are_rejected(self) -> None:
        value, context, brief_bytes = self.case()
        wrong_basis = copy.deepcopy(value)
        wrong_basis["claims"][0]["basis"] = "context_only"
        self.assert_invalid(wrong_basis, context, brief_bytes, "basis")

        empty_quote = copy.deepcopy(value)
        empty_quote["claims"][0].update(start_byte=0, end_byte=0, quote="")
        self.assert_invalid(empty_quote, context, brief_bytes, "quote")

        mismatched_quote = copy.deepcopy(value)
        mismatched_quote["claims"][0]["quote"] = "different words"
        self.assert_invalid(mismatched_quote, context, brief_bytes, "exact brief byte slice")

        empty_explanation = copy.deepcopy(value)
        empty_explanation["claims"][0]["explanation"] = "  \n"
        self.assert_invalid(empty_explanation, context, brief_bytes, "explanation")

    def test_bool_negative_and_out_of_bounds_offsets_are_rejected(self) -> None:
        value, context, brief_bytes = self.case()
        for start, end in ((True, 4), (-1, 4), (0, len(brief_bytes) + 1)):
            with self.subTest(start=start, end=end):
                changed = copy.deepcopy(value)
                changed["claims"][0]["start_byte"] = start
                changed["claims"][0]["end_byte"] = end
                self.assert_invalid(changed, context, brief_bytes, "offset")

    def test_offsets_are_utf8_byte_slices_and_reject_multibyte_splits(self) -> None:
        value, context, brief_bytes = self.case()
        multilingual = next(
            claim for claim in value["claims"] if claim["fact_id"] == "elevated_change"
        )
        self.assertIn("café", multilingual["quote"])
        self.assertEqual(
            brief_bytes[multilingual["start_byte"] : multilingual["end_byte"]].decode("utf-8"),
            multilingual["quote"],
        )

        split = copy.deepcopy(value)
        split_claim = next(
            claim for claim in split["claims"] if claim["fact_id"] == "elevated_change"
        )
        cafe_start = brief_bytes.index("café".encode("utf-8"))
        split_claim["start_byte"] = cafe_start + len("caf".encode("utf-8")) + 1
        self.assert_invalid(split, context, brief_bytes, "UTF-8")

    def test_brief_reference_and_bytes_must_match_path_digest_and_exact_size(self) -> None:
        value, context, brief_bytes = self.case()
        for field, bad in (
            ("path", "../brief.md"),
            ("sha256", "A" * 64),
            ("size_bytes", True),
            ("size_bytes", -1),
        ):
            with self.subTest(field=field, bad=bad):
                changed = copy.deepcopy(value)
                changed["brief"][field] = bad
                self.assert_invalid(changed, context, brief_bytes, "brief")

        alternate_path = copy.deepcopy(value)
        alternate_path["brief"]["path"] = "tasks/another-task/brief.md"
        self.assert_invalid(alternate_path, context, brief_bytes, "context.brief")

        wrong_bytes = brief_bytes + b"\n"
        self.assert_invalid(value, context, wrong_bytes, "brief")

    def test_brief_bytes_must_decode_as_utf8_even_outside_quotes(self) -> None:
        value, context, brief_bytes = self.case()
        invalid_bytes = brief_bytes + b"\xff"
        changed_brief = artifact_ref(context.brief["path"], invalid_bytes)
        changed_context = context_with(context, brief=changed_brief)
        value["brief"] = changed_brief
        self.assert_invalid(value, changed_context, invalid_bytes, "valid UTF-8")

    def test_task_phase_and_source_identity_must_match_context(self) -> None:
        value, context, brief_bytes = self.case()
        for field, bad, needle in (
            ("task_id", "another-task", "task_id"),
            ("task_phase", "planning", "task_phase"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(value)
                changed[field] = bad
                self.assert_invalid(changed, context, brief_bytes, needle)

        for field, bad, needle in (
            ("phase", "preflight", "source"),
            ("thread_id", "other-thread", "source"),
            ("raw", artifact_ref("tasks/elsewhere/raw.json", b"raw"), "source"),
        ):
            with self.subTest(source_field=field):
                changed = copy.deepcopy(value)
                changed["source"][field] = bad
                self.assert_invalid(changed, context, brief_bytes, needle)

    def test_semantic_digest_and_context_shape_are_checked(self) -> None:
        value, context, brief_bytes = self.case()
        changed = copy.deepcopy(value)
        changed["source"]["semantic_sha256"] = "0" * 64
        self.assert_invalid(changed, context, brief_bytes, "semantic")

        malformed = {
            key: list(item) if isinstance(item, tuple) else copy.deepcopy(item)
            for key, item in context.semantic.items()
        }
        malformed["unrecognized"] = []
        bad_context = context_with(context, semantic=malformed)
        self.assert_invalid(value, bad_context, brief_bytes, "semantic")

    def test_semantic_digest_canonicalizes_nested_action_intents(self) -> None:
        value, context, brief_bytes = self.case()
        semantic = {
            key: list(item) if isinstance(item, tuple) else copy.deepcopy(item)
            for key, item in context.semantic.items()
        }
        semantic["action_intents"] = [
            {"action": "sample_repository_update", "target": "owner/repository"}
        ]
        ontologies = contracts.Ontologies(
            fact_entries=self.ontologies.fact_entries,
            uncertainty_ids=(),
            action_entries=(
                {
                    "id": "sample_repository_update",
                    "target_kind": "repository",
                    "target_required": True,
                },
            ),
            safe_lane_reason_ids=(),
        )
        changed_context = context_with(context, semantic=semantic)
        value["source"]["semantic_sha256"] = semantic_digest(semantic)
        self.assertEqual(
            gates.validate_gate_evidence(
                value,
                context=changed_context,
                brief_bytes=brief_bytes,
                ontologies=ontologies,
            ),
            [],
        )

    def test_raw_reference_requires_normalized_relative_path_and_sha_shape(self) -> None:
        value, context, brief_bytes = self.case()
        bad_raw = artifact_ref("tasks/task-demo-001/source/raw.json", b"raw")
        bad_raw["sha256"] = "f" * 63 + "G"
        bad_context = context_with(context, source_raw=bad_raw)
        self.assert_invalid(value, bad_context, brief_bytes, "raw")

    def test_artifact_path_schema_and_runtime_reject_drive_relative_references(self) -> None:
        schema = gates.expected_gate_evidence_schema(self.schema_ontologies)
        path_pattern = re.compile(
            schema["$defs"]["ArtifactRef"]["properties"]["path"]["pattern"]
        )
        self.assertIsNone(path_pattern.fullmatch("C:brief.md"))
        self.assertIsNone(path_pattern.fullmatch("D:source.json"))

        value, context, brief_bytes = self.case()
        bad_brief = copy.deepcopy(value)
        bad_brief["brief"]["path"] = "C:brief.md"
        self.assert_invalid(
            bad_brief, context, brief_bytes, "brief.path must be a normalized relative POSIX path"
        )

        bad_raw = copy.deepcopy(value)
        bad_raw["source"]["raw"]["path"] = "D:source.json"
        self.assert_invalid(
            bad_raw, context, brief_bytes, "source.raw.path must be a normalized relative POSIX path"
        )

    def test_validate_is_deterministic_and_returns_errors_for_malformed_values(self) -> None:
        value, context, brief_bytes = self.case()
        errors = gates.validate_gate_evidence(
            ["not", "an", "object"],
            context=context,
            brief_bytes=brief_bytes,
            ontologies=self.ontologies,
        )
        self.assertEqual(errors, sorted(set(errors)))
        self.assertTrue(errors)
        self.assertEqual(
            errors,
            gates.validate_gate_evidence(
                ["not", "an", "object"],
                context=context,
                brief_bytes=brief_bytes,
                ontologies=self.ontologies,
            ),
        )

        malformed_phase = copy.deepcopy(value)
        malformed_phase["task_phase"] = []
        self.assert_invalid(malformed_phase, context, brief_bytes, "task_phase")

        malformed_source_phase = copy.deepcopy(value)
        malformed_source_phase["source"]["phase"] = []
        self.assert_invalid(malformed_source_phase, context, brief_bytes, "source.phase")

    def test_binding_deep_copies_claims_and_revalidates_against_new_context(self) -> None:
        value, context, brief_bytes = self.case()
        original = copy.deepcopy(value)
        bound = gates.bind_gate_evidence(
            value,
            context=context,
            brief_bytes=brief_bytes,
            ontologies=self.ontologies,
        )
        value["claims"][0]["quote"] = "mutated"
        value["source"]["raw"]["path"] = "changed"
        self.assertEqual(bound.to_dict(), original)
        changed_context = context_with(context, task_phase="final_review")
        with self.assertRaises(gates.GateEvidenceError):
            bound.assert_matches(changed_context, brief_bytes, self.ontologies)

    def test_structural_validation_does_not_claim_natural_language_truth(self) -> None:
        value, context, brief_bytes = self.case()
        preserved = next(
            claim for claim in value["claims"] if claim["fact_id"] == "elevated_change"
        )
        self.assertIn("preserve", preserved["quote"])
        self.assertEqual(
            gates.validate_gate_evidence(
                value,
                context=context,
                brief_bytes=brief_bytes,
                ontologies=self.ontologies,
            ),
            [],
            "byte/source binding does not decide whether a preserved constraint is an applicable gate",
        )

    def test_schema_file_matches_generated_closed_contract(self) -> None:
        schema_path = (
            SKILL_ROOT
            / "references"
            / "schemas"
            / "cmr-gate-evidence-v1.schema.json"
        )
        published = json.loads(schema_path.read_text(encoding="utf-8"))
        expected = gates.expected_gate_evidence_schema(self.schema_ontologies)
        self.assertEqual(published, expected)
        self.assertEqual(published["$defs"]["Claim"]["additionalProperties"], False)
        self.assertEqual(
            published["$defs"]["Claim"]["properties"]["fact_id"]["enum"],
            list(
                gates.required_gate_ids(
                    self.schema_ontologies.fact_ids, self.schema_ontologies
                )
            ),
        )
        self.assertIn("ArtifactRef", published["$defs"])


def artifact_ref(path: str, payload: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def semantic_digest(semantic: dict[str, Any]) -> str:
    payload = (
        json.dumps(
            semantic,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def context_with(context: Any, **changes: Any) -> Any:
    fields = {
        "task_id": context.task_id,
        "task_phase": context.task_phase,
        "brief": context.brief,
        "source_phase": context.source_phase,
        "source_raw": context.source_raw,
        "source_thread_id": context.source_thread_id,
        "semantic": context.semantic,
    }
    fields.update(changes)
    return gates.GateEvidenceContext(**fields)


if __name__ == "__main__":
    unittest.main()
