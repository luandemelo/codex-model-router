from __future__ import annotations

import copy
import dataclasses
import json
import subprocess
import sys
import unittest

from support import SKILL_ROOT, canonical_json_bytes, load_module

contracts = load_module("cmr_contracts")
runtime = load_module("cmr_runtime")
dispatch = load_module("cmr_dispatch")
gate_tests = load_module("test_gate_dispatch")
try:
    accounting = load_module("cmr_confirmation_accounting")
except ModuleNotFoundError:
    accounting = None


class ConfirmationAccountingTests(unittest.TestCase):
    """Synthetic frozen fixtures exercise accounting, never live model judgment."""

    @classmethod
    def setUpClass(cls):
        gate_tests.GateDispatchTests.setUpClass()

    def setUp(self):
        self.gates = gate_tests.GateDispatchTests()
        self.gates.setUp()
        self.addCleanup(self.gates.doCleanups)
        self.helper = self.gates.helper
        self.plan = self.helper.json_artifact("inputs/accounting-plan.json", {"tasks": ["T-001"]})
        prompt = self.helper.artifact("control/accounting-plan.prompt.md", b"Classify the frozen plan.\n")
        self.planning, _, _, _ = self.helper.control_occurrence(
            phase="planning", result={"schema_version": "cmr-planner-result-v4", "safe_lanes": []},
            prompt=prompt, thread_id="thread-accounting-plan", plan=self.plan, prefix="accounting-plan")

    def require_accounting(self):
        self.assertIsNotNone(accounting, "current package requires an execution-accounting facade")
        return accounting

    def replacement(self, *, outcome=None):
        inputs, proposer, _, preflight, packet = self.gates.replacement(["architecture"])
        observations = [self.planning, inputs["controller_occurrence"], proposer.occurrence]
        if outcome is not None:
            reference, _, confirmation = self.gates.confirm(packet, outcome=outcome)
            observations.append(confirmation["occurrence"])
        else:
            reference, confirmation = None, None
        keywords = dict(plan_sha256=self.plan.sha256, release_review=None,
            task_preflight_evidence={"T-001": {"preflight_evidence": preflight.evidence,
                                             "runtime_input": inputs["runtime_ref"]}},
            ledger_root=self.helper.ledger)
        states = [runtime.TaskAccountingState("T-001", "run", False, ())]
        return observations, states, keywords, packet, reference, confirmation, inputs, preflight

    def summary(self, observations, states, keywords):
        return self.require_accounting().summarize_execution_accounting(
            observations, states, None, [], **keywords)

    def request(self, observations, states, keywords):
        return {"schema_version": "cmr-execution-accounting-input-v1",
            "occurrences": [value.to_record() for value in observations],
            "task_states": [dataclasses.asdict(value) for value in states],
            "semantic_results": None, "instrument_errors": [],
            "plan_sha256": keywords["plan_sha256"], "release_review": None,
            "task_preflight_evidence": {task_id: {field: ref.to_dict() if ref else None
                for field, ref in value.items()} for task_id, value in keywords["task_preflight_evidence"].items()}}

    def run_cli(self, request):
        reference = self.helper.json_artifact("inputs/accounting-request.json", request)
        return subprocess.run([sys.executable, "-B", str(SKILL_ROOT / "scripts/codex_model_router.py"),
            "check-execution-call-shape", str(self.helper.ledger / reference.path), str(self.helper.ledger)],
            text=True, capture_output=True, check=False)

    def test_accepted_confirmation_is_a_distinct_purpose_with_all_usage_and_no_false_retry(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement(outcome="accept")
        old = runtime.summarize_accounting(observations, states, None, [],
            plan_sha256=self.plan.sha256, release_review=None)
        self.assertIn("preflight key for T-001 has 2 attempts", runtime.validate_call_shape(
            observations, states, plan_sha256=self.plan.sha256, release_review=None))
        self.assertEqual(old["transport_retry_count"], 1)
        report = self.summary(observations, states, keywords)
        self.assertEqual(report["core_v4"]["phase_counts"]["preflight"], 1)
        self.assertEqual(report["core_v4"]["transport_retry_count"], 0)
        self.assertEqual(report["confirmation_count"], 1)
        self.assertEqual(report["confirmation_extra_attempts"], 0)
        self.assertEqual(report["total_calls"], 4)
        self.assertEqual(report["total_usage"], {"input_tokens": 148, "cached_input_tokens": 44,
            "cache_write_input_tokens": 12, "output_tokens": 76, "reasoning_output_tokens": 28})
        self.assertEqual(report["confirmation_usage"]["input_tokens"], 37)
        self.assertEqual(report["confirmation_scopes"][0]["outcome"], "accept")
        self.assertTrue(report["usage_complete"])
        self.assertTrue(report["canonical_call_shape"], report["accounting_errors"])
        self.assertEqual(self.require_accounting().validate_execution_call_shape(observations, states, **keywords), [])

    def test_block_confirmation_has_canonical_shape_and_usage_but_still_denies_dispatch(self):
        observations, states, keywords, _, reference, _, inputs, preflight = self.replacement(outcome="block")
        report = self.summary(observations, states, keywords)
        self.assertEqual(report["confirmation_scopes"][0]["outcome"], "block")
        self.assertEqual(report["total_usage"]["input_tokens"], 148)
        self.assertEqual(report["core_v4"]["expected_phase_counts"]["worker"], 0)
        self.assertTrue(report["canonical_call_shape"], report["accounting_errors"])
        with self.assertRaisesRegex(dispatch.DispatchError, "blocked worker dispatch"):
            dispatch.materialize_dispatch_bundle(**self.gates.bundle_arguments(inputs, preflight, reference))

    def test_missing_confirmation_is_required_before_any_dispatch_bundle_exists(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement()
        report = self.summary(observations, states, keywords)
        self.assertEqual(report["confirmation_count"], 0)
        self.assertEqual(report["confirmation_scopes"][0]["outcome"], "missing")
        self.assertEqual(report["total_calls"], 3)
        self.assertFalse(report["canonical_call_shape"])
        self.assertTrue(any("missing gate confirmation" in error for error in report["accounting_errors"]))

    def test_operationally_failed_confirmation_without_final_artifact_keeps_known_usage(self):
        observations, states, keywords, packet, _, _, _, _ = self.replacement()
        attempted = self.gates.confirmation_inputs(packet)["occurrence"]
        failed = dataclasses.replace(attempted, accepted=False, failure_class="operational",
            errors=("model turn failed",), exit_code=1, raw_object=None)
        report = self.summary(observations + [failed], states, keywords)
        self.assertEqual(report["confirmation_count"], 1)
        self.assertEqual(report["confirmation_scopes"][0]["outcome"], "failed")
        self.assertEqual(report["confirmation_scopes"][0]["attempts"][0]["failure_class"], "operational")
        self.assertEqual(report["total_usage"]["input_tokens"], 148)
        self.assertEqual(report["core_v4"]["transport_retry_count"], 0)
        self.assertFalse(report["canonical_call_shape"])

    def test_extra_confirmation_attempts_are_visible_separately_from_transport_retries(self):
        observations, states, keywords, packet, _, _, _, _ = self.replacement(outcome="accept")
        extra = self.gates.confirmation_inputs(packet, prefix="extra-confirmation",
            thread_id="thread-extra-confirmation")["occurrence"]
        report = self.summary(observations + [extra], states, keywords)
        self.assertEqual(report["confirmation_count"], 2)
        self.assertEqual(report["confirmation_extra_attempts"], 1)
        self.assertEqual(report["confirmation_scopes"][0]["outcome"], "multiple")
        self.assertEqual(report["total_usage"]["input_tokens"], 185)
        self.assertEqual(report["core_v4"]["transport_retry_count"], 0)
        self.assertFalse(report["canonical_call_shape"])

    def test_unmatched_confirmation_prompt_remains_visible_with_usage(self):
        observations, states, keywords, packet, _, _, _, _ = self.replacement()
        stale = self.gates.confirmation_inputs(packet, prompt_bytes=b"Stale confirmation packet.\n")["occurrence"]
        report = self.summary(observations + [stale], states, keywords)
        self.assertEqual(report["confirmation_count"], 0)
        self.assertEqual(len(report["unmatched_occurrences"]), 1)
        self.assertEqual(report["total_calls"], 4)
        self.assertEqual(report["known_usage"]["input_tokens"], 148)
        self.assertFalse(report["canonical_call_shape"])

    def test_duplicate_call_identity_cannot_double_count_total_or_confirmation_usage(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement(outcome="accept")
        report = self.summary(observations + [observations[-1]], states, keywords)
        self.assertEqual(report["observed_record_count"], 5)
        self.assertEqual(report["total_calls"], 4)
        self.assertEqual(report["confirmation_count"], 1)
        self.assertEqual(report["total_usage"]["input_tokens"], 148)
        self.assertFalse(report["canonical_call_shape"])
        self.assertTrue(any("duplicate occurrence identity" in error for error in report["accounting_errors"]))

    def test_cross_task_and_source_identity_reuse_cannot_classify_a_confirmation(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement(outcome="accept")
        for changed in (dataclasses.replace(observations[-1], task_id="T-OTHER", scope_id="T-OTHER"),
                        dataclasses.replace(observations[-1], raw_sha256=observations[-2].raw_sha256)):
            with self.subTest(task=changed.task_id, raw=changed.raw_sha256):
                report = self.summary(observations[:-1] + [changed], states, keywords)
                self.assertEqual(report["confirmation_count"], 0)
                self.assertFalse(report["canonical_call_shape"])
                self.assertTrue(report["accounting_errors"])

    def test_stale_authoritative_preflight_reference_cannot_create_a_confirmation_scope(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement(outcome="accept")
        reference = keywords["task_preflight_evidence"]["T-001"]["preflight_evidence"]
        keywords["task_preflight_evidence"]["T-001"]["preflight_evidence"] = dataclasses.replace(reference, sha256="0" * 64)
        report = self.summary(observations, states, keywords)
        self.assertEqual(report["confirmation_scopes"], [])
        self.assertFalse(report["canonical_call_shape"])
        self.assertEqual(report["known_usage"]["input_tokens"], 148)

    def test_confirmation_artifact_must_bind_the_exact_observed_record(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement(outcome="accept")
        changed = dataclasses.replace(observations[-1], thread_id="thread-forged-confirmation")
        report = self.summary(observations[:-1] + [changed], states, keywords)
        self.assertFalse(report["canonical_call_shape"])
        self.assertTrue(any("does not bind inventory" in error for error in report["accounting_errors"]))
        self.assertEqual(report["total_calls"], 4)

    def test_unknown_usage_is_null_while_known_usage_is_retained(self):
        observations, states, keywords, packet, _, _, _, _ = self.replacement()
        attempted = self.gates.confirmation_inputs(packet)["occurrence"]
        failed = dataclasses.replace(attempted, accepted=False, failure_class="instrument",
            errors=("usage is unavailable",), usage=None, raw_object=None)
        report = self.summary(observations + [failed], states, keywords)
        self.assertIsNone(report["total_usage"])
        self.assertIsNone(report["confirmation_usage"])
        self.assertIsNone(report["confirmation_scopes"][0]["usage"])
        self.assertFalse(report["usage_complete"])
        self.assertEqual(report["known_usage"]["input_tokens"], 111)
        self.assertTrue(any("usage" in error for error in report["accounting_errors"]))

    def test_nongated_execution_preserves_the_existing_core_v4_summary(self):
        inputs = self.helper.controller_bundle_inputs()
        selector = dispatch.select_preflight(inputs["decision"], manifest_path=self.helper.ledger / "absent.json",
            ledger_root=self.helper.ledger)
        proposer, _, preflight = self.helper.run_preflight(inputs, selector,
            {"schema_version": "cmr-preflight-result-v4", "outcome": "accept"}, prefix="nongated")
        observations = [self.planning, inputs["controller_occurrence"], proposer.occurrence]
        states = [runtime.TaskAccountingState("T-001", "run", False, ())]
        keywords = dict(plan_sha256=self.plan.sha256, release_review=None, ledger_root=self.helper.ledger,
            task_preflight_evidence={"T-001": {"preflight_evidence": preflight.evidence,
                                             "runtime_input": inputs["runtime_ref"]}})
        report = self.summary(observations, states, keywords)
        expected = runtime.summarize_accounting(observations, states, None, [],
            plan_sha256=self.plan.sha256, release_review=None)
        self.assertEqual(report["core_v4"], expected)
        self.assertEqual(report["confirmation_scopes"], [])
        self.assertEqual(report["confirmation_count"], 0)
        self.assertTrue(report["canonical_call_shape"], report["accounting_errors"])

    def test_nonmonotonic_replace_stays_a_blocked_core_preflight_without_confirmation(self):
        inputs = self.helper.controller_bundle_inputs(fact_ids=["architecture"])
        selector = dispatch.select_preflight(inputs["decision"], manifest_path=self.helper.ledger / "absent.json",
            ledger_root=self.helper.ledger)
        proposer, transition, preflight = self.helper.run_preflight(inputs, selector,
            {"schema_version": "cmr-preflight-result-v4", "outcome": "replace", "fact_ids": ["audit"],
             "uncertainty_ids": [], "action_intents": []}, prefix="nonmonotonic")
        observations = [self.planning, inputs["controller_occurrence"], proposer.occurrence]
        states = [runtime.TaskAccountingState("T-001", "run", False, ())]
        keywords = dict(plan_sha256=self.plan.sha256, release_review=None, ledger_root=self.helper.ledger,
            task_preflight_evidence={"T-001": {"preflight_evidence": preflight.evidence,
                                             "runtime_input": inputs["runtime_ref"]}})
        self.assertEqual(transition.outcome, "blocked")
        self.assertEqual(transition.blocker_ids, ("preflight_non_monotonic",))
        self.assertIsNone(preflight.replacement_evidence)
        self.assertIsNone(preflight.final_decision)
        self.assertEqual(dispatch.validate_preflight_evidence(
            self.helper.ledger / preflight.evidence.path, self.helper.ledger), [])
        self.assertEqual(runtime.validate_call_shape(observations, states,
            plan_sha256=self.plan.sha256, release_review=None), [])
        report = self.summary(observations, states, keywords)
        self.assertTrue(report["canonical_call_shape"], report["accounting_errors"])
        self.assertEqual(report["accounting_errors"], [])
        self.assertEqual(report["core_v4"], runtime.summarize_accounting(observations, states, None, [],
            plan_sha256=self.plan.sha256, release_review=None))
        self.assertEqual(report["core_v4"]["phase_counts"]["preflight"], 1)
        self.assertEqual(report["core_v4"]["expected_phase_counts"]["worker"], 0)
        self.assertEqual(report["confirmation_scopes"], [])
        self.assertEqual(report["confirmation_count"], 0)
        self.assertEqual(report["unmatched_occurrences"], [])
        self.assertEqual(report["total_calls"], 3)
        self.assertEqual(report["total_usage"], {"input_tokens": 111, "cached_input_tokens": 33,
            "cache_write_input_tokens": 9, "output_tokens": 57, "reasoning_output_tokens": 21})

    def test_python_and_cli_return_the_same_complete_report(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement(outcome="accept")
        report = self.summary(observations, states, keywords)
        process = self.run_cli(self.request(observations, states, keywords))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        value = json.loads(process.stdout)
        self.assertTrue(value["valid"])
        self.assertEqual(value["report"], report)

    def test_current_input_is_closed_and_caller_flags_cannot_create_confirmation_membership(self):
        observations, states, keywords, _, _, _, _, _ = self.replacement(outcome="accept")
        request = self.request(observations, states, keywords)
        request["occurrences"][-1]["is_confirmation"] = True
        errors = self.require_accounting().validate_execution_accounting_input(request)
        self.assertTrue(errors)
        process = self.run_cli(request)
        self.assertEqual(process.returncode, 1)
        self.assertNotIn("report", json.loads(process.stdout))

    def test_published_current_input_and_report_schemas_match_executable_contracts(self):
        facade = self.require_accounting()
        schemas = facade.expected_execution_accounting_schemas()
        self.assertEqual(set(schemas), {"cmr-execution-accounting-v1.schema.json",
            "cmr-execution-accounting-input-v1.schema.json"})
        for filename, schema in schemas.items():
            with self.subTest(filename=filename):
                self.assertEqual(json.loads((SKILL_ROOT / "references/schemas" / filename).read_bytes()), schema)
                self.assertFalse(schema["additionalProperties"])
        self.assertEqual(contracts.validate_published_contracts(SKILL_ROOT, self.gates.ontologies), [])


if __name__ == "__main__":
    unittest.main()
