from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest

from support import SKILL_ROOT, load_module


from support import compile_fixture_route

compiler = load_module("cmr_compiler")
contracts = load_module("cmr_contracts")
dispatch = load_module("cmr_dispatch")
test_compiler = load_module("test_compiler")
test_dispatch = load_module("test_dispatch")


class ProfileEightTests(unittest.TestCase):
    """Fresh 0.8 fixtures; no historical record is rewritten by these tests."""

    @classmethod
    def setUpClass(cls) -> None:
        test_compiler.CompilerTests.setUpClass()
        cls.helper = test_compiler.CompilerTests()
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def test_sol_coordinates_and_luna_xhigh_implements_each_clear_eligible_unit(self) -> None:
        evidence = self.helper.bound_evidence()
        self.assertEqual(
            (evidence["occurrence"]["model"], evidence["occurrence"]["reasoning_effort"]),
            ("gpt-6.1-sol", "medium"),
        )
        decision = compile_fixture_route(evidence, self.helper.runtime_input(), self.ontologies)
        self.assertEqual(decision["schema_version"], "cmr-route-decision-v4")
        self.assertEqual(decision["worker"], {"model": "gpt-6-luna", "reasoning_effort": "xhigh"})
        self.assertEqual(decision["review"], {"model": "gpt-6.1-sol", "reasoning_effort": "medium"})
        risky = compile_fixture_route(
            self.helper.bound_evidence(["cross_module_integration"]),
            self.helper.runtime_input(), self.ontologies,
        )
        self.assertEqual(risky["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": "high"})
        # Compiling another module must not contaminate a bounded unit's route.
        self.assertEqual(
            compile_fixture_route(evidence, self.helper.runtime_input(), self.ontologies),
            decision,
        )

    def test_profile_seven_controller_and_route_evidence_are_rejected(self) -> None:
        result = self.helper.controller_result()
        result["schema_version"] = "cmr-controller-result-v3"
        self.assertTrue(contracts.validate_controller_result(result, self.ontologies))
        evidence = copy.deepcopy(self.helper.bound_evidence())
        evidence["schema_version"] = "cmr-route-evidence-v4"
        with self.assertRaises(compiler.CompilationError):
            compile_fixture_route(evidence, self.helper.runtime_input(), self.ontologies)


class DispatchRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        test_dispatch.DispatchTests.setUpClass()

    def setUp(self) -> None:
        self.helper = test_dispatch.DispatchTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.reference, self.preflight, self.inputs = self.helper.materialize_valid_bundle("accept")
        self.path = self.helper.ledger / self.reference.path

    def request(self) -> dict:
        return dispatch.dispatch_request(self.path, self.helper.ledger)

    def test_validated_request_contains_actual_native_spawn_arguments_and_bound_brief(self) -> None:
        value = self.request()
        self.assertEqual(value["schema_version"], "cmr-dispatch-request-v1")
        self.assertEqual(value["scope"]["task_id"], "T-001")
        self.assertEqual(value["scope"]["brief"], self.inputs["brief"].to_dict())
        self.assertEqual(value["scope"]["bundle"], self.reference.to_dict())
        request = value["request"]
        self.assertEqual(request["model"], "gpt-6-luna")
        self.assertEqual(request["reasoning_effort"], "xhigh")
        self.assertEqual(request["fork_turns"], "none")
        self.assertEqual(request["message"], self.helper.read_ref_bytes(self.inputs["brief"]).decode("utf-8"))
        self.assertEqual(set(request), {"task_name", "message", "model", "reasoning_effort", "fork_turns"})

    def test_cli_returns_the_same_request_and_never_executes_it(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts/codex_model_router.py"),
             "dispatch-request", str(self.path), str(self.helper.ledger)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        value = json.loads(completed.stdout)
        self.assertTrue(value["valid"])
        self.assertEqual(value["dispatch_request"], self.request())

    def test_stale_profile_seven_bundle_cannot_produce_a_request(self) -> None:
        value = self.helper.read_ref(self.reference)
        value["schema_version"] = "cmr-dispatch-bundle-v3"
        self.helper.rewrite_json_ref(self.reference, value)
        with self.assertRaises(dispatch.DispatchError) as caught:
            self.request()
        self.assertIn("dispatch bundle.schema_version is invalid", caught.exception.errors)

    def test_luna_max_without_dense_facts_cannot_produce_a_request_even_with_updated_refs(self) -> None:
        decision = self.helper.read_ref(self.inputs["decision_ref"])
        decision["worker"]["reasoning_effort"] = "max"
        decision_ref = self.helper.rewrite_json_ref(self.inputs["decision_ref"], decision)
        preflight_value = self.helper.read_ref(self.preflight.evidence)
        preflight_value["candidate_decision"] = decision_ref.to_dict()
        preflight_ref = self.helper.rewrite_json_ref(self.preflight.evidence, preflight_value)
        bundle = self.helper.read_ref(self.reference)
        bundle["candidate_decision"] = decision_ref.to_dict()
        bundle["final_decision"] = decision_ref.to_dict()
        bundle["preflight_evidence"] = preflight_ref.to_dict()
        self.helper.rewrite_json_ref(self.reference, bundle)
        with self.assertRaises(dispatch.DispatchError) as caught:
            self.request()
        self.assertIn("route decision worker is not derived from its facts", caught.exception.errors)

    def test_hash_tampering_and_symlink_briefs_are_rejected(self) -> None:
        brief_path = self.helper.ledger / self.inputs["brief"].path
        original = brief_path.read_bytes()
        brief_path.chmod(0o644)
        brief_path.write_bytes(b"outside the approved scope\n")
        brief_path.chmod(0o444)
        with self.assertRaises(dispatch.DispatchError):
            self.request()
        brief_path.unlink()
        target = self.helper.ledger / "unbound-brief.md"
        target.write_bytes(original)
        target.chmod(0o444)
        brief_path.symlink_to(target)
        with self.assertRaises(dispatch.DispatchError):
            self.request()

    def test_noncanonical_bundle_location_cannot_bypass_dispatch_validation(self) -> None:
        other = self.helper.ledger / "bundle.json"
        other.write_bytes(self.path.read_bytes())
        other.chmod(0o444)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.dispatch_request(other, self.helper.ledger)


if __name__ == "__main__":
    unittest.main()
