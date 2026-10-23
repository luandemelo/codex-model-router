from __future__ import annotations

import json
import copy
import dataclasses
import os
import stat
import subprocess
import sys
import unittest
from unittest import mock

from support import SKILL_ROOT, canonical_json_bytes, load_module, sha256, fixture_gate_evidence

compiler = load_module("cmr_compiler")
dispatch = load_module("cmr_dispatch")
gates = load_module("cmr_gate_evidence")
compiler_tests = load_module("test_compiler")
dispatch_tests = load_module("test_dispatch")


class GateDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler_tests.CompilerTests.setUpClass()
        dispatch_tests.DispatchTests.setUpClass()

    def setUp(self):
        self.helper = dispatch_tests.DispatchTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.compiler_helper = compiler_tests.CompilerTests()
        self.ontologies = self.compiler_helper.ontologies

    def fresh_helper(self):
        self.helper = dispatch_tests.DispatchTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)

    def replacement(self, facts, *, candidate_facts=None, task_id="T-001",
                    brief=b"Choose the architecture and implement the approved trust boundary.\n"):
        reference = self.helper.artifact(f"briefs/{task_id}.md", brief)
        inputs = self.helper.controller_bundle_inputs(task_id=task_id, brief=reference,
            fact_ids=list(candidate_facts or []))
        selector = dispatch.select_preflight(inputs["decision"],
            manifest_path=self.helper.ledger / "absent.json", ledger_root=self.helper.ledger)
        result = {"schema_version": "cmr-preflight-result-v4", "outcome": "replace",
                  "fact_ids": sorted(facts), "uncertainty_ids": [], "action_intents": []}
        bound, transition, preflight = self.helper.run_preflight(inputs, selector, result,
            task_id=task_id, prefix="proposal-" + task_id)
        packet = {
            "task_id": task_id, "task_phase": inputs["runtime"]["task_phase"],
            "brief": inputs["brief"].to_dict(), "runtime_input": inputs["runtime_ref"].to_dict(),
            "candidate_evidence": inputs["evidence_ref"].to_dict(),
            "candidate_gate_evidence": inputs["candidate_gate_ref"].to_dict(),
            "replacement_evidence": preflight.replacement_evidence.to_dict(),
            "replacement_gate_evidence": inputs["replacement_gate_ref"].to_dict(),
            "final_decision": preflight.final_decision.to_dict(),
            "proposing_preflight_invocation": preflight.invocation.to_dict(),
        }
        return inputs, bound, transition, preflight, packet

    def confirmation_inputs(self, packet, *, outcome="accept", thread_id="thread-confirmation-independent",
                            prompt_bytes=None, prefix="confirmation"):
        prompt_bytes = dispatch.build_gate_confirmation_prompt(packet, self.helper.ledger) if prompt_bytes is None else prompt_bytes
        prompt = self.helper.artifact(f"control/{prefix}.prompt.md", prompt_bytes)
        result = {"schema_version": "cmr-gate-confirmation-result-v1", "outcome": outcome}
        occurrence, raw, transcript, occurrence_ref = self.helper.control_occurrence(
            phase="preflight", result=result, prompt=prompt, thread_id=thread_id,
            task_id=packet["task_id"], prefix=prefix)
        return dict(result=result, packet=packet, prompt=prompt, raw=raw,
            transcript=transcript, occurrence_ref=occurrence_ref, occurrence=occurrence,
            ledger_root=self.helper.ledger)

    def confirm(self, packet, **kwargs):
        arguments = self.confirmation_inputs(packet, **kwargs)
        bound = dispatch.bind_gate_confirmation_result(**arguments)
        reference = dispatch.materialize_gate_confirmation(bound, ledger_root=self.helper.ledger)
        return reference, bound, arguments

    def bundle_arguments(self, inputs, preflight, confirmation=None):
        return dict(task_id=inputs["evidence"]["task_id"], brief=inputs["brief"],
            controller_evidence=inputs["evidence_ref"], runtime_input=inputs["runtime_ref"],
            candidate_decision=inputs["decision_ref"], preflight_evidence=preflight.evidence,
            final_decision=preflight.final_decision, candidate_gate_evidence=inputs["candidate_gate_ref"],
            replacement_gate_evidence=inputs["replacement_gate_ref"],
            replacement_gate_confirmation=confirmation, ledger_root=self.helper.ledger)

    def test_direct_compiler_omission_cannot_authorize_even_an_empty_gate_set(self):
        with self.assertRaises(TypeError):
            compiler.compile_route(
                self.compiler_helper.bound_evidence(),
                self.compiler_helper.runtime_input(), self.ontologies,
            )

    def test_cli_compile_omission_is_one_json_error_and_exit_one(self):
        request = self.helper.json_artifact("compile.json", {
            "evidence": self.compiler_helper.bound_evidence(),
            "runtime": self.compiler_helper.runtime_input(),
        })
        result = subprocess.run([
            sys.executable, "-B", str(SKILL_ROOT / "scripts/codex_model_router.py"),
            "compile", str(self.helper.ledger / request.path),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1, result.stdout)
        value = json.loads(result.stdout)
        self.assertFalse(value["valid"])
        self.assertNotIn("decision", value)

    def test_gated_replace_without_independent_confirmation_is_denied(self):
        inputs = self.helper.controller_bundle_inputs()
        selector = dispatch.select_preflight(
            inputs["decision"], manifest_path=self.helper.ledger / "absent.json",
            ledger_root=self.helper.ledger,
        )
        result = {"schema_version": "cmr-preflight-result-v4", "outcome": "replace",
                  "fact_ids": ["architecture"], "uncertainty_ids": [], "action_intents": []}
        _, _, preflight = self.helper.run_preflight(inputs, selector, result, prefix="gate")
        arguments = dict(
            task_id="T-001", brief=inputs["brief"], controller_evidence=inputs["evidence_ref"],
            runtime_input=inputs["runtime_ref"], candidate_decision=inputs["decision_ref"],
            preflight_evidence=preflight.evidence, final_decision=preflight.final_decision,
            ledger_root=self.helper.ledger,
        )
        # Supply the mandatory v5 fields when available; omission of the actual
        # confirmation must fail independently of the schema migration.
        if "candidate_gate_ref" in inputs:
            arguments.update(candidate_gate_evidence=inputs["candidate_gate_ref"],
                             replacement_gate_evidence=inputs["replacement_gate_ref"],
                             replacement_gate_confirmation=None)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_dispatch_bundle(**arguments)

    def test_current_dispatch_rejects_historical_v4_bundle(self):
        reference, _, _ = self.helper.materialize_valid_bundle("accept")
        value = self.helper.read_ref(reference)
        value["schema_version"] = "cmr-dispatch-bundle-v4"
        self.helper.rewrite_json_ref(reference, value)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.dispatch_request(self.helper.ledger / reference.path, self.helper.ledger)

    def test_gated_replacement_requires_accept_for_all_final_claims_before_native_request(self):
        inputs, proposing, transition, preflight, packet = self.replacement(
            ["architecture", "trust_boundary"], candidate_facts=["architecture"])
        reference, bound, arguments = self.confirm(packet)
        self.assertTrue(bound.__dataclass_params__.frozen)
        detached = bound.to_dict()
        detached["packet"]["task_id"] = "other"
        self.assertEqual(bound.to_dict()["packet"]["task_id"], "T-001")
        self.assertEqual(dispatch.validate_gate_confirmation(reference,
            expected_packet=packet, ledger_root=self.helper.ledger), [])
        self.assertEqual(reference.path, "tasks/T-001/gate-confirmation/evidence.json")
        self.assertEqual(set(os.listdir((self.helper.ledger / reference.path).parent)), {"evidence.json"})
        self.assertEqual(stat.S_IMODE((self.helper.ledger / reference.path).stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE((self.helper.ledger / reference.path).parent.stat().st_mode), 0o555)
        prompt = self.helper.read_ref_bytes(arguments["prompt"])
        self.assertEqual(prompt, dispatch.build_gate_confirmation_prompt(packet, self.helper.ledger))
        body = json.loads(prompt.splitlines()[-1])
        self.assertEqual(body["packet"], packet)
        self.assertEqual(set(body["contents"]), set(packet) - {"task_id", "task_phase", "proposing_preflight_invocation"})
        for field, text in body["contents"].items():
            self.assertEqual(text.encode("utf-8"), self.helper.read_ref_bytes(compiler.ArtifactRef.from_value(packet[field])))
        bundle = dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight, reference))
        request = dispatch.dispatch_request(self.helper.ledger / bundle.path, self.helper.ledger)
        self.assertEqual(request["request"]["model"], "gpt-6.1-sol")
        self.assertEqual(request["request"]["reasoning_effort"], "max")
        self.assertEqual(request["request"]["fork_turns"], "none")
        self.assertEqual(transition.final_decision["fact_ids"], ["architecture", "trust_boundary"])
        self.assertNotEqual(arguments["occurrence"].thread_id, proposing.occurrence.thread_id)

    def test_preserved_gate_alone_also_requires_confirmation(self):
        inputs, _, _, preflight, _ = self.replacement(["architecture"], candidate_facts=["architecture"])
        with self.assertRaisesRegex(dispatch.DispatchError, "independent gate confirmation"):
            dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight))

    def test_block_is_retained_and_never_dispatches_or_retries_same_occurrence(self):
        inputs, _, _, preflight, packet = self.replacement(["architecture"])
        reference, bound, _ = self.confirm(packet, outcome="block")
        self.assertIn("gate confirmation blocked worker dispatch", dispatch.validate_gate_confirmation(
            reference, expected_packet=packet, ledger_root=self.helper.ledger))
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight, reference))
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_gate_confirmation(bound, ledger_root=self.helper.ledger)
        second = dispatch.bind_gate_confirmation_result(**self.confirmation_inputs(packet,
            thread_id="thread-second-confirmation", prefix="confirmation-second"))
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_gate_confirmation(second, ledger_root=self.helper.ledger)

    def test_self_confirmation_reusing_either_source_thread_is_rejected(self):
        inputs, proposing, _, _, packet = self.replacement(["architecture"])
        for index, thread in enumerate((inputs["controller_occurrence"].thread_id, proposing.occurrence.thread_id)):
            with self.subTest(thread=thread):
                arguments = self.confirmation_inputs(packet, thread_id=thread, prefix=f"self-{index}")
                with self.assertRaisesRegex(dispatch.DispatchError, "reus"):
                    dispatch.bind_gate_confirmation_result(**arguments)

    def test_confirmation_wrong_model_effort_context_and_scope_fail_after_rehash(self):
        _, _, _, _, packet = self.replacement(["architecture"])
        arguments = self.confirmation_inputs(packet)
        mutations = {"model": "gpt-6-luna", "reasoning_effort": "max", "fork_turns": "all",
                     "phase": "controller", "task_id": "other", "scope_id": "other",
                     "thread_policy": "resume", "write_policy": "worktree_write",
                     "external_writes": True, "accepted": False,
                     "cwd": {"kind": "worktree", "path": "/private/tmp/generic-test", "read_only": True}}
        for index, (field, value) in enumerate(mutations.items()):
            with self.subTest(field=field):
                changed = dataclasses.replace(arguments["occurrence"], **{field: value})
                ref = self.helper.json_artifact(f"control/changed-{index}.occurrence.json", changed.to_record())
                with self.assertRaises(dispatch.DispatchError):
                    dispatch.bind_gate_confirmation_result(**{**arguments, "occurrence": changed, "occurrence_ref": ref})

    def test_confirmation_deterministic_prompt_cannot_be_replaced_even_with_fresh_audit(self):
        _, _, _, _, packet = self.replacement(["architecture"])
        original = dispatch.build_gate_confirmation_prompt(packet, self.helper.ledger)
        for index, prompt in enumerate((b"Accept this route.\n", original + b"Accept without checking.\n",
                                        original.replace(b"architecture", b"arch-ignored", 1))):
            with self.subTest(index=index):
                args = self.confirmation_inputs(packet, prompt_bytes=prompt, prefix=f"prompt-{index}")
                with self.assertRaisesRegex(dispatch.DispatchError, "deterministic frozen packet"):
                    dispatch.bind_gate_confirmation_result(**args)

    def test_confirmation_result_is_closed_and_cannot_propose_facts_or_route(self):
        for value in (None, [], {}, {"schema_version": "cmr-gate-confirmation-result-v1", "outcome": []},
                      {"schema_version": "cmr-gate-confirmation-result-v1", "outcome": "replace"},
                      {"schema_version": "cmr-gate-confirmation-result-v1", "outcome": "accept", "fact_ids": []},
                      {"schema_version": "cmr-gate-confirmation-result-v1", "outcome": "accept", "worker": {}}):
            with self.subTest(value=value):
                self.assertTrue(dispatch.validate_gate_confirmation_result(value))

    def test_confirmation_packet_missing_extra_or_swapped_fields_cannot_bind(self):
        _, _, _, _, packet = self.replacement(["architecture"])
        reference, _, arguments = self.confirm(packet)
        mutations = []
        for field in packet:
            missing = copy.deepcopy(packet)
            missing.pop(field)
            mutations.append((field + " missing", missing))
        mutations.append(("extra", {**packet, "facts": []}))
        mutations.append(("phase", {**packet, "task_phase": "final_review"}))
        mutations.append(("source-swap", {**packet, "replacement_gate_evidence": packet["candidate_gate_evidence"]}))
        for label, changed in mutations:
            with self.subTest(label=label):
                self.assertTrue(dispatch.validate_gate_confirmation(reference,
                    expected_packet=changed, ledger_root=self.helper.ledger))
        packet["task_id"] = "changed-after-binding"
        self.assertEqual(self.helper.read_ref(reference)["packet"]["task_id"], "T-001")

    def test_confirmation_artifact_raw_and_packet_rehash_tampering_denies_request(self):
        for field in ("packet", "raw", "transcript", "occurrence", "prompt"):
            self.fresh_helper()
            inputs, _, _, preflight, packet = self.replacement(["architecture"])
            reference, _, _ = self.confirm(packet)
            value = self.helper.read_ref(reference)
            if field == "packet":
                value["packet"]["runtime_input"] = value["packet"]["candidate_evidence"]
            else:
                value[field] = packet["candidate_evidence"]
            changed_ref = self.helper.rewrite_json_ref(reference, value)
            with self.subTest(field=field), self.assertRaises(dispatch.DispatchError):
                dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight, changed_ref))

    def test_confirmation_reference_from_another_task_or_packet_cannot_be_reused(self):
        inputs, _, _, preflight, packet = self.replacement(["architecture"], task_id="T-FIRST")
        reference, _, _ = self.confirm(packet)
        other, _, _, other_preflight, other_packet = self.replacement(["trust_boundary"], task_id="T-SECOND")
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_dispatch_bundle(**self.bundle_arguments(other, other_preflight, reference))
        self.assertTrue(dispatch.validate_gate_confirmation(reference,
            expected_packet=other_packet, ledger_root=self.helper.ledger))

    def test_empty_replace_requires_explicit_replacement_evidence_and_forbids_confirmation(self):
        inputs, _, _, preflight, packet = self.replacement(["audit"])
        self.assertEqual(self.helper.read_ref(inputs["replacement_gate_ref"])["claims"], [])
        arguments = self.bundle_arguments(inputs, preflight)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_dispatch_bundle(**{**arguments, "replacement_gate_evidence": None})
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_dispatch_bundle(**{**arguments, "replacement_gate_confirmation": inputs["candidate_gate_ref"]})
        with self.assertRaises(dispatch.DispatchError):
            dispatch.build_gate_confirmation_prompt(packet, self.helper.ledger)
        reference = dispatch.materialize_dispatch_bundle(**arguments)
        self.assertEqual(dispatch.validate_dispatch_bundle(self.helper.ledger / reference.path, self.helper.ledger), [])

    def test_skip_and_accept_forbid_extra_confirmation_and_replacement_evidence(self):
        for branch in ("skip", "accept"):
            for field in ("replacement_gate_evidence", "replacement_gate_confirmation"):
                self.fresh_helper()
                reference, _, inputs = self.helper.materialize_valid_bundle(branch)
                value = self.helper.read_ref(reference)
                value[field] = inputs["candidate_gate_ref"].to_dict()
                self.helper.rewrite_json_ref(reference, value)
                with self.subTest(branch=branch, field=field), self.assertRaises(dispatch.DispatchError):
                    dispatch.dispatch_request(self.helper.ledger / reference.path, self.helper.ledger)

    def test_compile_consumer_revalidates_all_structural_and_source_bindings(self):
        inputs = self.helper.controller_bundle_inputs(fact_ids=["architecture", "trust_boundary"])
        bound_value = inputs["candidate_gate_bound"].to_dict()
        mutations = {
            "task": lambda v: v.update(task_id="other"),
            "phase": lambda v: v.update(task_phase="final_review"),
            "brief-path": lambda v: v["brief"].update(path="briefs/other.md"),
            "brief-hash": lambda v: v["brief"].update(sha256="f" * 64),
            "brief-size": lambda v: v["brief"].update(size_bytes=1),
            "source-phase": lambda v: v["source"].update(phase="preflight"),
            "source-raw": lambda v: v["source"]["raw"].update(path="control/other.jsonl"),
            "source-thread": lambda v: v["source"].update(thread_id="other"),
            "semantic": lambda v: v["source"].update(semantic_sha256="e" * 64),
            "quote": lambda v: v["claims"][0].update(quote="not the brief"),
            "bool-offset": lambda v: v["claims"][0].update(start_byte=False),
            "missing": lambda v: v["claims"].pop(),
            "extra": lambda v: v["claims"].append({**v["claims"][0], "fact_id": "audit"}),
            "duplicate": lambda v: v["claims"].append(copy.deepcopy(v["claims"][0])),
            "unordered": lambda v: v["claims"].reverse(),
        }
        for label, mutate in mutations.items():
            value = copy.deepcopy(bound_value)
            mutate(value)
            with self.subTest(label=label), self.assertRaises(compiler.CompilationError) as caught:
                compiler.compile_route(inputs["evidence"], inputs["runtime"], self.ontologies,
                    gate_evidence=gates.BoundGateEvidence(value), brief_bytes=self.helper.read_ref_bytes(inputs["brief"]))
            self.assertEqual(caught.exception.blocker_id, "artifact_binding_invalid")
        for invalid in (None, bound_value):
            with self.assertRaises(compiler.CompilationError):
                compiler.compile_route(inputs["evidence"], inputs["runtime"], self.ontologies,
                    gate_evidence=invalid, brief_bytes=self.helper.read_ref_bytes(inputs["brief"]))

    def test_apply_preflight_omission_and_controller_origin_replacement_are_denied(self):
        inputs, bound, _, _, _ = self.replacement(["architecture"])
        with self.assertRaises(TypeError):
            dispatch.apply_preflight(inputs["evidence"], inputs["runtime"], bound, self.ontologies)
        replacement = copy.deepcopy(inputs["evidence"])
        replacement["fact_ids"] = ["architecture"]
        copied_origin = fixture_gate_evidence(replacement, inputs["runtime"], self.ontologies,
            self.helper.read_ref_bytes(inputs["brief"]))
        with self.assertRaises(compiler.CompilationError):
            dispatch.apply_preflight(inputs["evidence"], inputs["runtime"], bound, self.ontologies,
                candidate_gate_evidence=inputs["candidate_gate_bound"], replacement_gate_evidence=copied_origin,
                brief_bytes=self.helper.read_ref_bytes(inputs["brief"]))

    def test_added_or_preserved_replacement_gate_cannot_be_omitted_from_sidecar(self):
        inputs, bound, _, preflight, _ = self.replacement(["architecture", "trust_boundary"], candidate_facts=["architecture"])
        for fact in ("architecture", "trust_boundary"):
            value = inputs["replacement_gate_bound"].to_dict()
            value["claims"] = [claim for claim in value["claims"] if claim["fact_id"] != fact]
            with self.subTest(fact=fact), self.assertRaises(compiler.CompilationError):
                dispatch.apply_preflight(inputs["evidence"], inputs["runtime"], bound, self.ontologies,
                    candidate_gate_evidence=inputs["candidate_gate_bound"], replacement_gate_evidence=gates.BoundGateEvidence(value),
                    brief_bytes=self.helper.read_ref_bytes(inputs["brief"]))

    def test_preflight_candidate_attachment_and_old_invocation_cannot_be_substituted(self):
        for mutation in ("sidecar", "old-invocation"):
            self.fresh_helper()
            reference, preflight, inputs = self.helper.materialize_valid_bundle("accept")
            invocation = self.helper.read_ref(preflight.invocation)
            if mutation == "sidecar":
                invocation["candidate_gate_evidence"] = inputs["evidence_ref"].to_dict()
            else:
                invocation["schema_version"] = "cmr-preflight-invocation-v4"
                invocation.pop("candidate_gate_evidence")
            invocation_ref = self.helper.rewrite_json_ref(preflight.invocation, invocation)
            evidence = self.helper.read_ref(preflight.evidence)
            evidence["invocation"] = invocation_ref.to_dict()
            evidence_ref = self.helper.rewrite_json_ref(preflight.evidence, evidence)
            bundle = self.helper.read_ref(reference)
            bundle["preflight_evidence"] = evidence_ref.to_dict()
            self.helper.rewrite_json_ref(reference, bundle)
            with self.subTest(mutation=mutation), self.assertRaises(dispatch.DispatchError):
                dispatch.dispatch_request(self.helper.ledger / reference.path, self.helper.ledger)

    def test_recurrence_and_branch_review_retain_max_and_require_empty_evidence(self):
        for changes in ({"task_phase": "final_review", "review_scope": "branch"},
                        {"current_defect_id": "same-defect", "failure_history": [
                            {"defect_id": "same-defect", "event": "attempt_completed_failed"},
                            {"defect_id": "same-defect", "event": "attempt_completed_failed"}]}):
            self.fresh_helper()
            inputs = self.helper.controller_bundle_inputs(runtime_changes=changes)
            self.assertEqual(inputs["candidate_gate_bound"].to_dict()["claims"], [])
            self.assertEqual(inputs["decision"]["worker"], {"model": "gpt-6.1-sol", "reasoning_effort": "max"})
            with self.assertRaises(compiler.CompilationError):
                compiler.compile_route(inputs["evidence"], inputs["runtime"], self.ontologies,
                    gate_evidence=None, brief_bytes=self.helper.read_ref_bytes(inputs["brief"]))

    def test_generic_route_regressions_use_explicit_positive_gate_claims(self):
        cases = (
            ("inventory_context", b"Inventory context includes auth modules; diagnose the wrapper only.\n", ["judgment_required"], "gpt-6.1-sol", "medium"),
            ("capture_wrapper_review", b"Review the capture wrapper and preserve existing concurrency machinery.\n", ["audit"], "gpt-6.1-sol", "medium"),
            ("ordinary_build_choice", b"Diagnose the ordinary build configuration choice.\n", ["judgment_required"], "gpt-6.1-sol", "medium"),
            ("static_error_labels", b"Replace these static error labels with supplied text.\n", [], "gpt-6-luna", "xhigh"),
            ("paired_async_deadline_wiring", b"Wire both callbacks and their paired deadline behavior across the producer and consumer modules.\n", ["cross_module_integration"], "gpt-6.1-sol", "high"),
            ("real_expiry_boundary", b"Audit expiry enforcement at the external trust boundary and implement the required integrity control.\n", ["trust_boundary"], "gpt-6.1-sol", "max"),
            ("real_peer_auth", b"Change peer authentication to enforce the approved identity check.\n", ["authentication"], "gpt-6.1-sol", "max"),
            ("real_zeroizing_decoder", b"Implement secret zeroization when the decoder fails.\n", ["secrets"], "gpt-6.1-sol", "max"),
            ("real_architecture", b"Choose the persistence architecture and settle its module contracts.\n", ["architecture"], "gpt-6.1-sol", "max"),
        )
        for name, brief, facts, model, effort in cases:
            self.fresh_helper()
            reference = self.helper.artifact(f"briefs/{name}.md", brief)
            inputs = self.helper.controller_bundle_inputs(task_id=name, brief=reference, fact_ids=facts)
            with self.subTest(name=name):
                self.assertEqual(inputs["decision"]["worker"], {"model": model, "reasoning_effort": effort})
                self.assertEqual([claim["fact_id"] for claim in inputs["candidate_gate_bound"].to_dict()["claims"]],
                    list(gates.required_gate_ids(facts, self.ontologies)))

    def test_context_or_preserved_only_claim_is_structural_but_audited_semantic_block_denies_dispatch(self):
        for brief in (b"Context inventory: architecture, authentication, locks, secrets. Review labels only.\n",
                      b"Preserve the existing architecture; update static error labels only.\n"):
            self.fresh_helper()
            inputs, _, transition, preflight, packet = self.replacement(["architecture"], brief=brief)
            # The pure validator deliberately cannot establish language truth.
            self.assertEqual(transition.final_decision["worker"]["reasoning_effort"], "max")
            reference, _, _ = self.confirm(packet, outcome="block")
            with self.assertRaises(dispatch.DispatchError):
                dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight, reference))
            self.assertEqual(transition.final_decision["fact_ids"], ["architecture"])

    def test_audited_preflight_blocks_context_only_candidate_without_deleting_facts(self):
        inputs = self.helper.controller_bundle_inputs(fact_ids=["architecture"])
        selector = dispatch.select_preflight(inputs["decision"], manifest_path=self.helper.ledger / "absent.json", ledger_root=self.helper.ledger)
        _, transition, preflight = self.helper.run_preflight(inputs, selector,
            {"schema_version": "cmr-preflight-result-v4", "outcome": "block", "blocker_ids": ["ambiguous_semantics"]}, prefix="semantic-block")
        self.assertEqual(transition.outcome, "blocked")
        self.assertEqual(inputs["evidence"]["fact_ids"], ["architecture"])
        with self.assertRaises(dispatch.DispatchError):
            dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight))

    def test_bundle_materializer_requires_typed_optional_artifact_references(self):
        inputs, _, _, preflight, packet = self.replacement(["architecture"])
        confirmation, _, _ = self.confirm(packet)
        arguments = self.bundle_arguments(inputs, preflight, confirmation)
        for field in ("replacement_gate_evidence", "replacement_gate_confirmation"):
            with self.subTest(field=field), self.assertRaises(dispatch.DispatchError):
                dispatch.materialize_dispatch_bundle(**{**arguments, field: arguments[field].to_dict()})

    def test_cli_compile_accepts_both_explicit_forms_and_rejects_source_or_brief_drift(self):
        inputs = self.helper.controller_bundle_inputs(fact_ids=["cross_module_integration"])
        self.helper.ensure_candidate_gate(inputs)
        combined = self.helper.json_artifact("compile.json", {"evidence": inputs["evidence"], "runtime": inputs["runtime"]})
        common = ["--gate-evidence", str(self.helper.ledger / inputs["candidate_gate_ref"].path),
                  "--brief", str(self.helper.ledger / inputs["brief"].path)]
        forms = ([str(self.helper.ledger / combined.path)],
                 [str(self.helper.ledger / inputs["evidence_ref"].path), str(self.helper.ledger / inputs["runtime_ref"].path)])
        for form in forms:
            completed = subprocess.run([sys.executable, "-B", str(SKILL_ROOT / "scripts/codex_model_router.py"),
                "compile", *form, *common], capture_output=True, text=True, check=False)
            with self.subTest(form=form):
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["decision"], inputs["decision"])
        for flags in ([], common[:2], common[2:], [*common[:2], "--brief", str(self.helper.ledger / "wrong.md")]):
            completed = subprocess.run([sys.executable, "-B", str(SKILL_ROOT / "scripts/codex_model_router.py"),
                "compile", *forms[0], *flags], capture_output=True, text=True, check=False)
            with self.subTest(flags=flags):
                self.assertEqual(completed.returncode, 1, completed.stdout)
                self.assertFalse(json.loads(completed.stdout)["valid"])
        value = inputs["candidate_gate_bound"].to_dict()
        value["source"]["thread_id"] = "unbound-source"
        self.helper.rewrite_json_ref(inputs["candidate_gate_ref"], value)
        completed = subprocess.run([sys.executable, "-B", str(SKILL_ROOT / "scripts/codex_model_router.py"),
            "compile", *forms[0], *common], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("thread_id", completed.stdout)

    def test_new_sidecar_and_confirmation_paths_enforce_nofollow_and_exact_membership(self):
        for target in ("candidate-sidecar", "replacement-sidecar", "confirmation", "confirmation-directory", "gate-directory"):
            self.fresh_helper()
            inputs, _, _, preflight, packet = self.replacement(["architecture"])
            reference, _, _ = self.confirm(packet)
            bundle_ref = dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight, reference))
            if target in {"candidate-sidecar", "replacement-sidecar", "confirmation"}:
                ref = {"candidate-sidecar": inputs["candidate_gate_ref"],
                       "replacement-sidecar": inputs["replacement_gate_ref"], "confirmation": reference}[target]
                path = self.helper.ledger / ref.path
                outside = self.helper.artifact("outside-" + target + ".json", path.read_bytes())
                path.parent.chmod(0o755)
                path.unlink()
                path.symlink_to(self.helper.ledger / outside.path)
                path.parent.chmod(0o555)
            else:
                suffix = "gate-confirmation" if target == "confirmation-directory" else "gate-evidence"
                directory = self.helper.ledger / "tasks/T-001" / suffix
                directory.chmod(0o755)
                self.helper.artifact(f"tasks/T-001/{suffix}/extra.json", b"{}\n")
                directory.chmod(0o555)
            with self.subTest(target=target), self.assertRaises(dispatch.DispatchError):
                dispatch.dispatch_request(self.helper.ledger / bundle_ref.path, self.helper.ledger)

    def test_gate_scope_identity_swap_after_membership_check_cannot_authorize(self):
        inputs, _, _, preflight, packet = self.replacement(["architecture"])
        confirmation, _, _ = self.confirm(packet)
        bundle = dispatch.materialize_dispatch_bundle(**self.bundle_arguments(inputs, preflight, confirmation))
        original = dispatch._gate_directory_errors
        swapped = False

        def swap_after_check(*args, **kwargs):
            nonlocal swapped
            errors = original(*args, **kwargs)
            if not swapped:
                swapped = True
                directory = self.helper.ledger / "tasks/T-001/gate-evidence"
                directory.rename(directory.with_name("gate-evidence-old"))
                directory.mkdir()
                for path in directory.with_name("gate-evidence-old").iterdir():
                    self.helper.artifact(f"tasks/T-001/gate-evidence/{path.name}", path.read_bytes())
                directory.chmod(0o555)
            return errors

        with mock.patch.object(dispatch, "_gate_directory_errors", side_effect=swap_after_check):
            with self.assertRaises(dispatch.DispatchError):
                dispatch.dispatch_request(self.helper.ledger / bundle.path, self.helper.ledger)

    def test_cli_compile_source_inputs_also_require_frozen_nofollow_reads(self):
        inputs = self.helper.controller_bundle_inputs()
        self.helper.ensure_candidate_gate(inputs)
        combined = self.helper.json_artifact("compile.json", {"evidence": inputs["evidence"], "runtime": inputs["runtime"]})
        alias = self.helper.ledger / "compile-link.json"
        alias.symlink_to(self.helper.ledger / combined.path)
        flags = ["--gate-evidence", str(self.helper.ledger / inputs["candidate_gate_ref"].path),
                 "--brief", str(self.helper.ledger / inputs["brief"].path)]
        for path in (alias, self.helper.ledger / combined.path):
            if path != alias:
                path.chmod(0o644)
            completed = subprocess.run([sys.executable, "-B", str(SKILL_ROOT / "scripts/codex_model_router.py"),
                "compile", str(path), *flags], capture_output=True, text=True, check=False)
            with self.subTest(path=path):
                self.assertEqual(completed.returncode, 1, completed.stdout)
                self.assertFalse(json.loads(completed.stdout)["valid"])

    def test_standalone_confirmation_rejects_rehashed_replacement_provenance_drift(self):
        for api in ("prompt", "bind", "materialize", "validate"):
            self.fresh_helper()
            _, _, _, preflight, packet = self.replacement(["architecture"])
            original_prompt = dispatch.build_gate_confirmation_prompt(packet, self.helper.ledger)
            replacement = self.helper.read_ref(preflight.replacement_evidence)
            replacement["raw"]["path"] = "control/nonexistent-controller-source.jsonl"
            replacement_ref = self.helper.rewrite_json_ref(preflight.replacement_evidence, replacement)
            preflight_value = self.helper.read_ref(preflight.evidence)
            preflight_value["replacement_evidence"] = replacement_ref.to_dict()
            preflight_ref = self.helper.rewrite_json_ref(preflight.evidence, preflight_value)
            packet["replacement_evidence"] = replacement_ref.to_dict()
            self.assertEqual(dispatch.validate_preflight_evidence(
                self.helper.ledger / preflight_ref.path, self.helper.ledger), [])

            # Every reference and prompt byte is honestly rehashed. Only the
            # immutable provenance differs from the controller-bound replay.
            lines = original_prompt.splitlines(keepends=True)
            body = json.loads(lines[-1])
            body["packet"] = copy.deepcopy(packet)
            body["contents"]["replacement_evidence"] = self.helper.read_ref_bytes(replacement_ref).decode("utf-8")
            forged_prompt = b"".join(lines[:-1]) + canonical_json_bytes(body)

            with self.subTest(api=api):
                if api == "prompt":
                    with self.assertRaisesRegex(dispatch.DispatchError, "replacement evidence"):
                        dispatch.build_gate_confirmation_prompt(packet, self.helper.ledger)
                    continue
                arguments = self.confirmation_inputs(packet, prompt_bytes=forged_prompt)
                if api == "bind":
                    with self.assertRaisesRegex(dispatch.DispatchError, "replacement evidence"):
                        dispatch.bind_gate_confirmation_result(**arguments)
                    continue
                value = {
                    "schema_version": "cmr-gate-confirmation-v1", "packet": packet,
                    **{field: arguments["occurrence_ref" if field == "occurrence" else field].to_dict()
                       for field in ("prompt", "raw", "transcript", "occurrence")},
                }
                if api == "materialize":
                    bound = dispatch.BoundGateConfirmation(canonical_json_bytes(value))
                    with self.assertRaisesRegex(dispatch.DispatchError, "replacement evidence"):
                        dispatch.materialize_gate_confirmation(bound, ledger_root=self.helper.ledger)
                else:
                    reference = self.helper.json_artifact("tasks/T-001/gate-confirmation/evidence.json", value)
                    (self.helper.ledger / reference.path).parent.chmod(0o555)
                    errors = dispatch.validate_gate_confirmation(reference,
                        expected_packet=packet, ledger_root=self.helper.ledger)
                    self.assertTrue(any("replacement evidence" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
