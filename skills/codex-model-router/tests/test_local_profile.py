from __future__ import annotations

import copy
import unittest

from support import SKILL_ROOT, load_module


compiler = load_module("cmr_compiler")
contracts = load_module("cmr_contracts")
dispatch = load_module("cmr_dispatch")
runtime = load_module("cmr_runtime")
test_compiler = load_module("test_compiler")
test_dispatch = load_module("test_dispatch")


class LocalProfileTests(unittest.TestCase):
    """New 0.8.0 fixtures; historical sealed records are never migrated in place."""

    @classmethod
    def setUpClass(cls) -> None:
        test_compiler.CompilerTests.setUpClass()
        cls.helper = test_compiler.CompilerTests()
        cls.ontologies = contracts.load_ontologies(SKILL_ROOT)

    def test_new_profile_routes_use_only_sol_61_and_adaptive_luna_efforts(self) -> None:
        for facts, pair in (
            ([], ("gpt-6-luna", "xhigh")),
            (["architecture_approved", "complex_local_execution", "low_blast_radius"], ("gpt-6-luna", "max")),
            (["audit", "low_blast_radius"], ("gpt-6.1-sol", "medium")),
            (["cross_module_integration"], ("gpt-6.1-sol", "high")),
            (["architecture"], ("gpt-6.1-sol", "max")),
        ):
            with self.subTest(facts=facts):
                decision = compiler.compile_route(
                    self.helper.bound_evidence(facts),
                    self.helper.runtime_input(),
                    self.ontologies,
                )
                self.assertEqual(tuple(decision["worker"].values()), pair)
                self.assertEqual(decision["schema_version"], "cmr-route-decision-v4")

    def test_substantive_judgment_does_not_require_a_blast_radius_qualifier(self) -> None:
        for fact in ("audit", "judgment_required", "reconciliation", "complex_local_execution"):
            with self.subTest(fact=fact):
                decision = compiler.compile_route(
                    self.helper.bound_evidence([fact]), self.helper.runtime_input(), self.ontologies,
                )
                self.assertEqual(decision["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": "medium"})
                self.assertEqual(dispatch._decision_errors(decision), [])

    def test_dense_local_max_requires_every_qualifier_without_open_uncertainty(self) -> None:
        dense = ["architecture_approved", "complex_local_execution", "low_blast_radius"]
        for removed in ("architecture_approved", "low_blast_radius"):
            with self.subTest(removed=removed):
                facts = [fact for fact in dense if fact != removed]
                decision = compiler.compile_route(
                    self.helper.bound_evidence(facts), self.helper.runtime_input(), self.ontologies,
                )
                self.assertEqual(decision["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": "medium"})
        for uncertainty in contracts.UNCERTAINTY_IDS:
            with self.subTest(uncertainty=uncertainty):
                decision = compiler.compile_route(
                    self.helper.bound_evidence(dense, uncertainty_ids=[uncertainty]),
                    self.helper.runtime_input(), self.ontologies,
                )
                self.assertEqual(decision["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": "medium"})
                self.assertEqual(dispatch._decision_errors(decision), [])

    def test_dense_local_max_yields_to_judgment_risk_recurrence_and_final_review(self) -> None:
        dense = ["architecture_approved", "complex_local_execution", "low_blast_radius"]
        for extra, effort in (("audit", "medium"), ("judgment_required", "medium"),
                              ("reconciliation", "medium"), ("cross_module_integration", "high"),
                              ("elevated_risk", "high"), ("architecture", "max")):
            with self.subTest(extra=extra):
                facts = sorted(dense + [extra])
                decision = compiler.compile_route(
                    self.helper.bound_evidence(facts), self.helper.runtime_input(), self.ontologies,
                )
                self.assertEqual(decision["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": effort})
                self.assertEqual(dispatch._decision_errors(decision), [])
        for changes in (
            {"task_phase": "final_review", "review_scope": "branch"},
            {"current_defect_id": "DENSE-DEFECT", "failure_history": [
                {"defect_id": "DENSE-DEFECT", "event": "attempt_completed_failed"},
                {"defect_id": "DENSE-DEFECT", "event": "attempt_completed_failed"},
            ]},
        ):
            decision = compiler.compile_route(
                self.helper.bound_evidence(dense), self.helper.runtime_input(**changes), self.ontologies,
            )
            self.assertEqual(decision["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": "max"})

    def test_both_luna_efforts_require_independent_sol_medium_review_and_fix_escalation(self) -> None:
        for effort in ("xhigh", "max"):
            for phase, round_index, expected in (
                ("worker", 0, ("gpt-6-luna", effort)),
                ("task_review", 0, ("gpt-6.1-sol", "medium")),
                ("worker", 4, ("gpt-6.1-sol", "medium")),
                ("re_review", 4, ("gpt-6.1-sol", "medium")),
                ("worker", 5, ("gpt-6.1-sol", "max")),
            ):
                with self.subTest(effort=effort, phase=phase, round=round_index):
                    try:
                        occurrence = runtime.OccurrenceExpectation.for_sdd(
                            phase=phase, scope_kind="task", scope_id="T-002", round_index=round_index,
                            task_id="T-002", initial_worker_model="gpt-6-luna", initial_worker_effort=effort,
                            prompt_sha256="b" * 64, forbidden_thread_ids=("worker-thread",),
                            cwd_policy={"kind": "worktree", "path": "/private/tmp/profile-test", "read_only": phase != "worker"},
                        )
                    except ValueError as error:
                        self.fail(f"allowed Luna/{effort} route was rejected: {error}")
                    self.assertEqual((occurrence.model, occurrence.reasoning_effort), expected)
                    self.assertEqual(occurrence.fork_turns, "none")
                    self.assertEqual(occurrence.forbidden_thread_ids, ("worker-thread",))

    def test_default_safe_lane_never_skips_dense_max_preflight(self) -> None:
        test_dispatch.DispatchTests.setUpClass()
        helper = test_dispatch.DispatchTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        safe, briefs, _ = helper.materialized_safe_lane()
        default = helper.controller_bundle_inputs(brief=briefs["T-001"], prefix="default")
        dense = helper.controller_bundle_inputs(
            brief=briefs["T-001"], prefix="dense",
            fact_ids=["architecture_approved", "complex_local_execution", "low_blast_radius"],
        )
        default_selector = dispatch.select_preflight(
            default["decision"], manifest_path=helper.ledger / safe.manifest.path, ledger_root=helper.ledger,
        )
        self.assertEqual(default["decision"]["worker"], {"model": "gpt-6-luna", "reasoning_effort": "xhigh"})
        self.assertEqual(default_selector["decision"], "skip")
        dense_selector = dispatch.select_preflight(
            dense["decision"], manifest_path=helper.ledger / safe.manifest.path, ledger_root=helper.ledger,
        )
        self.assertEqual(dense["decision"]["worker"], {"model": "gpt-6-luna", "reasoning_effort": "max"})
        self.assertEqual(dense_selector, {"decision": "run", "trigger_ids": ["nondefault_worker_route"]})
        for original, forged_effort in ((default["decision"], "max"), (dense["decision"], "xhigh")):
            forged = copy.deepcopy(original)
            forged["worker"]["reasoning_effort"] = forged_effort
            self.assertIn("route decision worker is not derived from its facts", dispatch._decision_errors(forged))
        _, _, preflight = helper.run_preflight(dense, dense_selector, helper.fixture["preflight_results"]["accept"], prefix="dense-accept")
        reference = dispatch.materialize_dispatch_bundle(
            task_id="T-001", brief=dense["brief"], controller_evidence=dense["evidence_ref"],
            runtime_input=dense["runtime_ref"], candidate_decision=dense["decision_ref"],
            preflight_evidence=preflight.evidence, final_decision=preflight.final_decision, ledger_root=helper.ledger,
        )
        request = dispatch.dispatch_request(helper.ledger / reference.path, helper.ledger)
        self.assertEqual(request["request"]["model"], "gpt-6-luna")
        self.assertEqual(request["request"]["reasoning_effort"], "max")
        self.assertEqual(request["request"]["fork_turns"], "none")

    def test_old_profile_envelopes_and_decisions_cannot_authorize_dispatch(self) -> None:
        result = self.helper.controller_result()
        result["schema_version"] = "cmr-controller-result-v3"
        self.assertTrue(contracts.validate_controller_result(result, self.ontologies))
        evidence = self.helper.bound_evidence()
        old_evidence = copy.deepcopy(evidence)
        old_evidence["schema_version"] = "cmr-route-evidence-v4"
        with self.assertRaises(compiler.CompilationError):
            compiler.compile_route(old_evidence, self.helper.runtime_input(), self.ontologies)
        decision = compiler.compile_route(evidence, self.helper.runtime_input(), self.ontologies)
        decision["schema_version"] = "cmr-route-decision-v3"
        self.assertTrue(dispatch._decision_errors(decision))

    def test_old_astra_sol_and_luna_medium_routes_are_rejected(self) -> None:
        for model, effort in (
            ("gpt-6-astra", "max"),
            ("gpt-6-sol", "medium"),
            ("gpt-6-sol", "high"),
            ("gpt-6-luna", "medium"),
            ("gpt-6-luna", "high"),
        ):
            with self.subTest(model=model, effort=effort), self.assertRaises(ValueError):
                runtime.OccurrenceExpectation.for_sdd(
                    phase="worker", scope_kind="task", scope_id="T-002",
                    round_index=0, task_id="T-002", initial_worker_model=model,
                    initial_worker_effort=effort, prompt_sha256="a" * 64,
                    cwd_policy={"kind": "worktree", "path": "/private/tmp/profile-test", "read_only": False},
                )

    def test_valid_bundle_with_old_profile_version_cannot_authorize_dispatch(self) -> None:
        test_dispatch.DispatchTests.setUpClass()
        helper = test_dispatch.DispatchTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        reference, _, _ = helper.materialize_valid_bundle("skip")
        path = helper.ledger / reference.path
        self.assertEqual(dispatch.validate_dispatch_bundle(path, helper.ledger), [])
        value = helper.read_ref(reference)
        value["schema_version"] = "cmr-dispatch-bundle-v3"
        helper.rewrite_json_ref(reference, value)
        errors = dispatch.validate_dispatch_bundle(path, helper.ledger)
        self.assertIn("dispatch bundle.schema_version is invalid", errors)

    def test_recurrence_stays_sol_max_with_independent_fresh_review(self) -> None:
        decision = compiler.compile_route(
            self.helper.bound_evidence(),
            self.helper.runtime_input(
                current_defect_id="DEF-1",
                failure_history=[
                    {"defect_id": "DEF-1", "event": "attempt_completed_failed"},
                    {"defect_id": "DEF-1", "event": "attempt_completed_failed"},
                ],
            ),
            self.ontologies,
        )
        self.assertTrue(decision["recurrence"]["active"])
        self.assertEqual(decision["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": "max"})
        review = runtime.OccurrenceExpectation.for_sdd(
            phase="task_review", scope_kind="task", scope_id="T-002", round_index=0,
            task_id="T-002", initial_worker_model=decision["worker"]["model"],
            initial_worker_effort=decision["worker"]["reasoning_effort"],
            prompt_sha256="b" * 64, forbidden_thread_ids=("worker-thread",),
            cwd_policy={"kind": "worktree", "path": "/private/tmp/profile-test", "read_only": True},
        )
        self.assertEqual((review.model, review.reasoning_effort), ("gpt-6.1-sol", "max"))
        self.assertEqual(review.thread_policy, "fresh")
        self.assertEqual(review.fork_turns, "none")
        self.assertEqual(review.forbidden_thread_ids, ("worker-thread",))
        self.assertIsNone(review.prior_thread_id)


if __name__ == "__main__":
    unittest.main()
