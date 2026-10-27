from __future__ import annotations

import copy
import dataclasses
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import (SKILL_ROOT, canonical_json_bytes, compile_fixture_route,
                     fixture_gate_evidence, load_module, sha256)
test_compiler = load_module('test_compiler')
test_dispatch = load_module('test_dispatch')
test_runtime = load_module('test_runtime')

compiler = load_module('cmr_compiler')
contracts = load_module('cmr_contracts')
dispatch = load_module('cmr_dispatch')
runtime = load_module('cmr_runtime')

PROFILE = {
    'schema_version': 'cmr-routing-profile-v1',
    'profile_id': 'luna-default-terra-latency-v1',
    'default_clear': {'model': 'gpt-6-luna', 'reasoning_effort': 'xhigh'},
    'default_dense': {'model': 'gpt-6-luna', 'reasoning_effort': 'max'},
    'latency_override': {'model': 'gpt-6.1-terra', 'reasoning_effort': 'xhigh'},
    'latency_review': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium'},
    'latency_scope': 'explicit_task_only',
    'fallback': 'forbidden',
    'tradeoff': 'latency_over_model_cost',
    'precedence': 'existing_risk_recurrence_review_gates',
}
PROFILE_BINDING = {
    'profile_id': 'luna-default-terra-latency-v1',
    'profile_sha256': sha256(json.dumps(PROFILE, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf-8')),
}
OPT_IN = b'For this task, prefer latency over model cost using the optional latency policy.'
BRIEF = b'Implement the approved isolated function.\n' + OPT_IN + b'\n'

# SHA-256 of the exact schema bytes at immutable baseline commit
# 018ef3f5fb5a2ff5f22a6cc090e4b8e5e23ac2ea. Keep these independent of the
# mutable release tree so archive validation needs no Git history.
HISTORICAL_SCHEMA_SHA256 = {
    'cmr-runtime-input-v4.schema.json': 'bae436aebc0d9e9cfcea08aaa8725d3e382171595fd4d030c08b9f468e911aa8',
    'cmr-route-decision-v4.schema.json': 'e5afef27072a214325fc6d5d2e8e704c95080d1b0752d4e27d2f92f22651d30a',
    'cmr-occurrence-audit-v4.schema.json': '3e42c69dc11df4ad8ab9b6dc6a9968d1a9eaf88e6d508eba1345743a7a2ea3b9',
    'cmr-execution-accounting-input-v1.schema.json': '3555ecf36ac05d32dec67aab9fbcd1a9e77dc8e924d77800ea0d23d70f21e5f0',
}


def observation_wire_domains_allow(schema, record):
    """Check only the wire version/scope/phase domains, without runtime helpers."""
    domains = [schema['properties']]
    for rule in schema.get('allOf', []):
        accepted = rule.get('if', {}).get('properties', {}).get('accepted', {})
        if 'const' in accepted and accepted['const'] is record['accepted']:
            domains.append(rule.get('then', {}).get('properties', {}))
    for properties in domains:
        for field in ('schema_version', 'scope_kind', 'phase'):
            constraint = properties.get(field, {})
            if set(constraint) - {'type', 'const', 'enum'}:
                raise AssertionError('unsupported domain constraint for ' + field)
            if 'const' in constraint and record[field] != constraint['const']:
                return False
            if 'enum' in constraint and record[field] not in constraint['enum']:
                return False
    return True


def policy(task_id, brief, *, enabled=True, phase='implementation'):
    preference = None
    if enabled:
        preference = {
            'schema_version': 'cmr-latency-preference-v1',
            'task_id': task_id,
            'task_phase': phase,
            'brief': brief.to_dict() if hasattr(brief, 'to_dict') else dict(brief),
            'preference': 'latency',
            'basis': 'explicit_task_opt_in',
            'tradeoff': 'latency_over_model_cost',
            'evidence': {'start_byte': BRIEF.index(OPT_IN),
                         'end_byte': BRIEF.index(OPT_IN) + len(OPT_IN),
                         'quote': OPT_IN.decode('utf-8')},
        }
    return {'profile': copy.deepcopy(PROFILE_BINDING), 'latency_preference': preference}


class LatencyCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_compiler.CompilerTests.setUpClass()
        cls.helper = test_compiler.CompilerTests()
        cls.ontologies = cls.helper.ontologies

    def inputs(self, facts=(), *, enabled=True, uncertainties=(), phase='implementation'):
        result = self.helper.controller_result(sorted(facts), uncertainty_ids=list(uncertainties))
        brief = compiler.ArtifactRef('briefs/T-002.md', sha256(BRIEF), len(BRIEF))
        payload, occurrence = self.helper.controller_occurrence(result, brief)
        evidence = compiler.bind_controller_result(result, task_id='T-002', brief=brief,
            raw=compiler.ArtifactRef('controller/T-002.raw.jsonl', sha256(payload), len(payload)),
            occurrence=occurrence)
        value = self.helper.runtime_input(task_phase=phase,
            review_scope='branch' if phase == 'final_review' else 'task')
        value.update(schema_version='cmr-runtime-input-v5',
                     routing_policy=policy('T-002', brief, enabled=enabled, phase=phase))
        return evidence, value

    def compile(self, evidence, value):
        return compile_fixture_route(evidence, value, self.ontologies, brief_bytes=BRIEF)

    def test_missing_opt_in_stays_luna_and_old_contract_is_byte_compatible(self):
        for facts, effort in (([], 'xhigh'),
            (['architecture_approved', 'complex_local_execution', 'low_blast_radius'], 'max')):
            with self.subTest(facts=facts):
                evidence, value = self.inputs(facts, enabled=False)
                decision = self.compile(evidence, value)
                self.assertEqual(decision['worker'], {'model': 'gpt-6-luna', 'reasoning_effort': effort})
                self.assertEqual(decision['schema_version'], 'cmr-route-decision-v5')
                self.assertEqual(decision['routing_policy'], value['routing_policy'])
                legacy = copy.deepcopy(value)
                legacy['schema_version'] = 'cmr-runtime-input-v4'
                legacy.pop('routing_policy')
                historical = self.compile(evidence, legacy)
                self.assertEqual(historical['schema_version'], 'cmr-route-decision-v4')
                self.assertNotIn('routing_policy', historical)
                self.assertEqual(historical['worker'], decision['worker'])

    def test_explicit_opt_in_selects_only_terra_xhigh_in_both_luna_lanes(self):
        for facts in ([], ['architecture_approved', 'complex_local_execution', 'low_blast_radius']):
            evidence, value = self.inputs(facts)
            decision = self.compile(evidence, value)
            self.assertEqual(decision['worker'], {'model': 'gpt-6.1-terra', 'reasoning_effort': 'xhigh'})
            self.assertEqual(decision['review'], {'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium'})
            self.assertEqual(decision['decisive_reason_id'], 'explicit_task_latency_preference')
            self.assertEqual(decision['routing_policy'], value['routing_policy'])
            self.assertEqual(dispatch._decision_errors(decision), [])

    def test_every_hard_integration_and_elevated_gate_overrides_opt_in(self):
        for entry in self.ontologies.fact_entries:
            if not entry['hard_gate'] and entry['routing_class'] not in {'integration', 'elevated_risk'}:
                continue
            with self.subTest(fact=entry['id']):
                evidence, value = self.inputs([entry['id']])
                decision = self.compile(evidence, value)
                effort = 'max' if entry['hard_gate'] else 'high'
                self.assertEqual(decision['worker'], {'model': 'gpt-6.1-sol', 'reasoning_effort': effort})
                self.assertEqual(decision['review']['reasoning_effort'], effort)
                self.assertEqual(decision['routing_policy'], value['routing_policy'])

    def test_judgment_uncertainty_unapproved_complex_work_and_final_review_win(self):
        cases = [(['audit'], (), 'implementation', 'medium'),
                 (['judgment_required'], (), 'implementation', 'medium'),
                 (['reconciliation'], (), 'implementation', 'medium'),
                 (['complex_local_execution'], (), 'implementation', 'medium'),
                 ([], ('controller_uncertainty',), 'implementation', 'medium'),
                 ([], (), 'final_review', 'max')]
        for facts, uncertainties, phase, effort in cases:
            with self.subTest(facts=facts, uncertainty=uncertainties, phase=phase):
                evidence, value = self.inputs(facts, uncertainties=uncertainties, phase=phase)
                decision = self.compile(evidence, value)
                self.assertEqual(decision['worker'], {'model': 'gpt-6.1-sol', 'reasoning_effort': effort})
                self.assertEqual(dispatch._decision_errors(decision), [])

    def test_recurring_or_reappeared_defect_cannot_use_latency_override(self):
        for history in ([{'defect_id': 'DEF-1', 'event': 'attempt_completed_failed'}] * 2,
                        [{'defect_id': 'DEF-1', 'event': 'attempt_completed_passed'},
                         {'defect_id': 'DEF-1', 'event': 'declared_resolved'},
                         {'defect_id': 'DEF-1', 'event': 'reappeared'}]):
            evidence, value = self.inputs()
            value.update(current_defect_id='DEF-1', failure_history=history)
            decision = self.compile(evidence, value)
            self.assertEqual(decision['worker'], {'model': 'gpt-6.1-sol', 'reasoning_effort': 'max'})
            self.assertTrue(decision['recurrence']['active'])

    def test_malformed_missing_stale_and_route_bearing_preferences_fail_closed(self):
        mutations = [lambda v: v.pop('routing_policy'),
            lambda v: v['routing_policy'].pop('latency_preference'),
            lambda v: v['routing_policy']['profile'].update(profile_id='old-profile'),
            lambda v: v['routing_policy']['profile'].update(profile_sha256='0' * 64),
            lambda v: v['routing_policy']['latency_preference'].update(task_id='OTHER'),
            lambda v: v['routing_policy']['latency_preference'].update(task_phase='final_review'),
            lambda v: v['routing_policy']['latency_preference']['brief'].update(sha256='0' * 64),
            lambda v: v['routing_policy']['latency_preference']['evidence'].update(quote='invented opt in'),
            lambda v: v['routing_policy']['latency_preference']['evidence'].update(start_byte=True),
            lambda v: v['routing_policy']['latency_preference'].update(model='gpt-6.1-terra'),
            lambda v: v['routing_policy']['latency_preference'].update(reasoning_effort='low'),
            lambda v: v['routing_policy']['latency_preference'].update(basis='global_default'),
            lambda v: v['routing_policy'].update(latency_preference={}),
            lambda v: v.update(schema_version='cmr-runtime-input-v4')]
        for mutate in mutations:
            evidence, value = self.inputs()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(compiler.CompilationError):
                self.compile(evidence, value)

    def test_malformed_container_types_are_blocked_by_public_compiler(self):
        mutations = [lambda v: v.update(routing_policy=[]),
            lambda v: v['routing_policy'].update(profile=[]),
            lambda v: v['routing_policy'].update(latency_preference=[]),
            lambda v: v['routing_policy']['latency_preference'].update(task_phase=[]),
            lambda v: v['routing_policy']['latency_preference'].update(evidence=[]),
            lambda v: v['routing_policy']['latency_preference'].update(brief=[]),
            lambda v: v['routing_policy']['latency_preference']['evidence'].update(quote='\ud800')]
        for mutate in mutations:
            evidence, value = self.inputs()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(compiler.CompilationError):
                self.compile(evidence, value)

    def test_uncertainty_without_opt_in_preserves_legacy_routing_and_cannot_select_terra(self):
        for enabled, model in ((False, 'gpt-6-luna'), (True, 'gpt-6.1-sol')):
            evidence, value = self.inputs(enabled=enabled, uncertainties=('controller_uncertainty',))
            decision = self.compile(evidence, value)
            self.assertEqual(decision['worker']['model'], model)
            if enabled:
                self.assertEqual(decision['decisive_reason_id'], 'controller_uncertainty')
        legacy = copy.deepcopy(value)
        legacy.pop('routing_policy')
        legacy['schema_version'] = 'cmr-runtime-input-v4'
        self.assertEqual(self.compile(evidence, legacy)['worker'],
                         {'model': 'gpt-6-luna', 'reasoning_effort': 'xhigh'})

    def test_controller_and_preflight_cannot_author_preferences_or_routes(self):
        for field in ('routing_policy', 'latency_preference', 'model', 'reasoning_effort', 'worker'):
            controller = self.helper.controller_result()
            controller[field] = 'unauthorized'
            self.assertTrue(contracts.validate_controller_result(controller, self.ontologies))
            preflight = {'schema_version': 'cmr-preflight-result-v4', 'outcome': 'accept', field: 'unauthorized'}
            self.assertTrue(contracts.validate_preflight_result(preflight, self.ontologies))

    def test_profile_and_new_schemas_roundtrip_without_rewriting_historical_schemas(self):
        expected = contracts.expected_published_schemas(self.ontologies)
        for name in ('cmr-runtime-input-v5.schema.json', 'cmr-route-decision-v5.schema.json',
                     'cmr-latency-preference-v1.schema.json', 'cmr-occurrence-audit-v5.schema.json',
                     'cmr-execution-accounting-input-v2.schema.json',
                     'cmr-dispatch-request-v2.schema.json', 'cmr-routing-profile-v1.schema.json'):
            self.assertTrue(name in expected, name)
        for name, schema in expected.items():
            self.assertEqual(json.loads((SKILL_ROOT / 'references/schemas' / name).read_text()), schema, name)
        for name, expected_sha256 in HISTORICAL_SCHEMA_SHA256.items():
            schema_bytes = (SKILL_ROOT / 'references/schemas' / name).read_bytes()
            self.assertEqual(sha256(schema_bytes), expected_sha256, name)

    def test_historical_schema_check_runs_without_git_history(self):
        with tempfile.TemporaryDirectory(prefix='cmr-archive-') as temporary:
            archive_root = Path(temporary) / 'release'
            archive_skill = archive_root / 'skills/codex-model-router'
            shutil.copytree(SKILL_ROOT, archive_skill)
            self.assertFalse(any((parent / '.git').exists()
                                 for parent in (archive_root, *archive_root.parents)))
            command = [sys.executable, '-B', '-m', 'unittest',
                'test_latency.LatencyCompilerTests.'
                'test_profile_and_new_schemas_roundtrip_without_rewriting_historical_schemas', '-v']
            process = subprocess.run(command,
                cwd=archive_skill / 'tests', text=True, capture_output=True, check=False)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            # Equivalent JSON with changed bytes must still fail preservation.
            schema = archive_skill / 'references/schemas/cmr-runtime-input-v4.schema.json'
            schema.write_bytes(schema.read_bytes() + b'\n')
            changed = subprocess.run(command,
                cwd=archive_skill / 'tests', text=True, capture_output=True, check=False)
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn('cmr-runtime-input-v4.schema.json', changed.stderr)


class LatencyDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_dispatch.DispatchTests.setUpClass()
        test_runtime.RuntimeTests.setUpClass()

    def setUp(self):
        self.helper = test_dispatch.DispatchTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        brief = self.helper.artifact('briefs/T-001.md', BRIEF)
        self.inputs = self.helper.controller_bundle_inputs(brief=brief,
            runtime_changes={'schema_version': 'cmr-runtime-input-v5',
                             'routing_policy': policy('T-001', brief)})
        self.selector = dispatch.select_preflight(self.inputs['decision'],
            manifest_path=self.helper.ledger / 'absent.json', ledger_root=self.helper.ledger)

    def accepted_bundle(self):
        _, _, artifacts = self.helper.run_preflight(self.inputs, self.selector,
            {'schema_version': 'cmr-preflight-result-v4', 'outcome': 'accept'}, prefix='latency')
        reference = self.helper.materialize_dispatch_bundle(task_id='T-001', brief=self.inputs['brief'],
            controller_evidence=self.inputs['evidence_ref'], runtime_input=self.inputs['runtime_ref'],
            candidate_decision=self.inputs['decision_ref'], preflight_evidence=artifacts.evidence,
            final_decision=artifacts.final_decision, ledger_root=self.helper.ledger)
        return reference, artifacts

    def test_terra_requires_preflight_and_dispatch_parameters_match_binding(self):
        self.assertEqual(self.selector['decision'], 'run')
        self.assertIn('nondefault_worker_route', self.selector['trigger_ids'])
        reference, _ = self.accepted_bundle()
        value = dispatch.dispatch_request(self.helper.ledger / reference.path, self.helper.ledger)
        self.assertEqual(value['request']['model'], 'gpt-6.1-terra')
        self.assertEqual(value['request']['reasoning_effort'], 'xhigh')
        self.assertEqual(value['request']['fork_turns'], 'none')
        self.assertEqual(value['scope']['routing_policy'], self.inputs['runtime']['routing_policy'])
        self.assertEqual(value['scope']['bundle'], reference.to_dict())

    def test_preflight_requires_exact_latency_attachment_before_accept(self):
        self.helper.ensure_candidate_gate(self.inputs)
        prompt = self.helper.artifact('control/unbound.prompt.md',
            b'Audit task.\n' + compiler.build_preflight_gate_attachment(
                self.inputs['candidate_gate_ref'], self.inputs['candidate_gate_bytes']))
        result = {'schema_version': 'cmr-preflight-result-v4', 'outcome': 'accept'}
        occurrence, raw, transcript, occurrence_ref = self.helper.control_occurrence(
            phase='preflight', result=result, prompt=prompt, thread_id='thread-unbound-preference',
            task_id='T-001', prefix='unbound')
        bound = compiler.bind_preflight_result(result, task_id='T-001', brief=self.inputs['brief'],
            candidate_evidence=self.inputs['evidence_ref'], candidate_gate_evidence=self.inputs['candidate_gate_ref'],
            candidate_decision=self.inputs['decision_ref'], selector=self.selector, prompt=prompt, raw=raw,
            transcript=transcript, occurrence_ref=occurrence_ref, occurrence=occurrence,
            prompt_bytes=self.helper.read_ref_bytes(prompt), candidate_gate_evidence_bytes=self.inputs['candidate_gate_bytes'])
        with self.assertRaises(compiler.CompilationError):
            compiler.apply_preflight(self.inputs['evidence'], self.inputs['runtime'], bound,
                self.helper.ontologies, candidate_gate_evidence=self.inputs['candidate_gate_bound'],
                replacement_gate_evidence=None, brief_bytes=BRIEF)

    def test_preflight_risk_replacement_preserves_preference_and_wins(self):
        _, transition, _ = self.helper.run_preflight(self.inputs, self.selector,
            {'schema_version': 'cmr-preflight-result-v4', 'outcome': 'replace',
             'fact_ids': ['architecture'], 'uncertainty_ids': [], 'action_intents': []}, prefix='risk')
        self.assertEqual(transition.outcome, 'replaced')
        self.assertEqual(transition.final_decision['worker'], {'model': 'gpt-6.1-sol', 'reasoning_effort': 'max'})
        self.assertEqual(transition.final_decision['routing_policy'], self.inputs['runtime']['routing_policy'])
        self.assertEqual(compiler.validate_preflight_monotonicity(self.inputs['evidence'],
            self.inputs['decision'], transition.replacement_evidence, transition.final_decision,
            self.helper.ontologies), [])
        changed = copy.deepcopy(transition.final_decision)
        changed['routing_policy']['latency_preference'] = None
        self.assertTrue(compiler.validate_preflight_monotonicity(self.inputs['evidence'],
            self.inputs['decision'], transition.replacement_evidence, changed, self.helper.ontologies))

    def test_dispatch_replay_rejects_tampered_preference_even_after_ref_rewrites(self):
        reference, _ = self.accepted_bundle()
        changed = copy.deepcopy(self.inputs['runtime'])
        changed['routing_policy']['latency_preference'] = None
        new_runtime = self.helper.rewrite_json_ref(self.inputs['runtime_ref'], changed)
        bundle = self.helper.read_ref(reference)
        bundle['runtime_input'] = new_runtime.to_dict()
        new_bundle = self.helper.rewrite_json_ref(reference, bundle)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.dispatch_request(self.helper.ledger / new_bundle.path, self.helper.ledger)

    def test_matching_opt_in_safe_lane_cannot_skip_or_be_manually_materialized(self):
        helper = test_dispatch.DispatchTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        planning, briefs, _, _ = helper.planning_inputs(brief_payloads={'T-001': BRIEF})
        replacement = briefs['T-001']
        artifacts = dispatch.materialize_safe_lane(planning, ledger_root=helper.ledger)
        inputs = helper.controller_bundle_inputs(brief=replacement, runtime_changes={
            'schema_version': 'cmr-runtime-input-v5', 'routing_policy': policy('T-001', replacement)})
        selector = dispatch.select_preflight(inputs['decision'],
            manifest_path=helper.ledger / artifacts.manifest.path, ledger_root=helper.ledger)
        self.assertEqual(selector['decision'], 'run')
        self.assertEqual(selector['trigger_ids'], ['nondefault_worker_route'])
        with self.assertRaises(dispatch.DispatchError) as caught:
            helper.materialize_preflight(task_id='T-001',
                selector={'decision': 'skip', 'trigger_ids': [], 'safe_lane': artifacts.manifest.to_dict()},
                candidate_decision=inputs['decision_ref'], bound_preflight=None, transition=None,
                ledger_root=helper.ledger)
        self.assertIn('skip candidate requires semantic preflight', caught.exception.errors)

    def test_accounting_v2_preserves_bound_rejected_attempt_usage_and_v1_rejects_new_audits(self):
        reference, artifacts = self.accepted_bundle()
        expected = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / reference.path, ledger_root=self.helper.ledger,
            phase='worker', round_index=0, cwd_policy=test_runtime.WORKER_CWD)
        helper = test_runtime.RuntimeTests()
        payload = helper.payload('sdd_worker_initial')
        parsed = runtime.audit_jsonl(payload, expected, None)
        metadata = helper.valid_metadata(payload, expected, thread_id=parsed.thread_id)
        metadata['dispatch_binding'] = copy.deepcopy(expected.dispatch_binding)
        metadata['model'] = 'gpt-6-luna'
        failed = runtime.audit_occurrence(payload, metadata, expected)
        self.assertFalse(failed.accepted)
        self.assertEqual(runtime.OccurrenceAudit.from_record(failed.to_record()).to_record(), failed.to_record())
        accounting = load_module('cmr_confirmation_accounting')
        request = {'schema_version': 'cmr-execution-accounting-input-v2',
            'occurrences': [failed.to_record()], 'task_states': [], 'plan_sha256': 'a' * 64,
            'release_review': None, 'semantic_results': None, 'instrument_errors': [],
            'task_preflight_evidence': {}}
        self.assertEqual(accounting.validate_execution_accounting_input(request), [])
        request['schema_version'] = 'cmr-execution-accounting-input-v1'
        self.assertTrue(accounting.validate_execution_accounting_input(request))
        summary = accounting.summarize_execution_accounting([failed], [], None, [],
            plan_sha256='a' * 64, release_review=None, task_preflight_evidence={}, ledger_root=self.helper.ledger)
        self.assertEqual(summary['total_calls'], 1)
        self.assertEqual(summary['total_usage']['input_tokens'], failed.usage.input_tokens)
        self.assertFalse(summary['canonical_call_shape'])
        # An actually attempted unsupported Terra effort is rejected, but its
        # host-observed route and usage still survive the versioned ledger.
        metadata['model'] = 'gpt-6.1-terra'
        metadata['reasoning_effort'] = 'low'
        unsupported = runtime.audit_occurrence(payload, metadata, expected)
        self.assertFalse(unsupported.accepted)
        self.assertEqual(unsupported.failure_class, 'instrument')
        self.assertEqual(runtime.OccurrenceAudit.from_record(unsupported.to_record()).to_record(), unsupported.to_record())
        request['schema_version'] = 'cmr-execution-accounting-input-v2'
        request['occurrences'] = [unsupported.to_record()]
        self.assertEqual(accounting.validate_execution_accounting_input(request), [])
        # Revocation/tampering of the sealed dispatch cannot authorize an old audit.
        path = self.helper.ledger / self.inputs['runtime_ref'].path
        path.chmod(0o644)
        path.write_bytes(b'{}\n')
        path.chmod(0o444)
        with self.assertRaises(ValueError):
            runtime.OccurrenceAudit.from_record(failed.to_record())

    def test_rejected_observed_fields_reach_complete_accounting_without_authorizing_them(self):
        reference, _ = self.accepted_bundle()
        expected = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / reference.path, ledger_root=self.helper.ledger,
            phase='worker', round_index=0, cwd_policy=test_runtime.WORKER_CWD)
        helper = test_runtime.RuntimeTests()
        payload = helper.payload('sdd_worker_initial')
        parsed = runtime.audit_jsonl(payload, expected, None)
        baseline = helper.valid_metadata(payload, expected, thread_id=parsed.thread_id)
        baseline['dispatch_binding'] = copy.deepcopy(expected.dispatch_binding)
        accounting = load_module('cmr_confirmation_accounting')
        expected_usage = {'input_tokens': 101, 'cached_input_tokens': 17,
            'cache_write_input_tokens': 5, 'output_tokens': 23, 'reasoning_output_tokens': 7}
        variants = {
            'model': ({'model': 'gpt-6-luna'}, 'does not match validated dispatch route'),
            'effort': ({'reasoning_effort': 'low'}, 'does not match validated dispatch route'),
            'prompt': ({'prompt_sha256': '8' * 64}, 'does not bind initial worker prompt'),
            'task_scope': ({'scope_id': 'OTHER', 'task_id': 'OTHER'}, 'does not bind occurrence task scope'),
            'release_scope': ({'scope_kind': 'release', 'scope_id': 'b' * 64,
                'task_id': None, 'round_index': 1}, 'does not bind occurrence task scope'),
            'combined': ({'model': 'gpt-6-luna', 'reasoning_effort': 'max',
                'prompt_sha256': '8' * 64, 'scope_id': 'OTHER', 'task_id': 'OTHER'},
                'does not bind occurrence task scope'),
        }
        for name, (changes, denial) in variants.items():
            with self.subTest(observed_mismatch=name):
                metadata = copy.deepcopy(baseline)
                metadata.update(changes)
                failed = runtime.audit_occurrence(payload, metadata, expected)
                self.assertFalse(failed.accepted)
                self.assertEqual(failed.failure_class, 'instrument')
                for field, value in changes.items():
                    self.assertEqual(getattr(failed, field), value)
                    self.assertIn(f'metadata {field} does not match expectation', failed.errors)
                record = failed.to_record()
                self.assertEqual(record['dispatch_binding'], expected.dispatch_binding)
                self.assertEqual(runtime.OccurrenceAudit.from_record(record).to_record(), record)
                request = {'schema_version': 'cmr-execution-accounting-input-v2',
                    'occurrences': [record], 'task_states': [], 'plan_sha256': 'a' * 64,
                    'release_review': None, 'semantic_results': None, 'instrument_errors': [],
                    'task_preflight_evidence': {}}
                self.assertEqual(accounting.validate_execution_accounting_input(request), [])
                request_ref = self.helper.json_artifact('inputs/rejected-' + name + '.json', request)
                process = subprocess.run([sys.executable, '-B', str(SKILL_ROOT / 'scripts/codex_model_router.py'),
                    'check-execution-call-shape', str(self.helper.ledger / request_ref.path), str(self.helper.ledger)],
                    text=True, capture_output=True, check=False)
                value = json.loads(process.stdout)
                self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
                self.assertFalse(value['valid'])
                self.assertIn('report', value)
                report = value['report']
                self.assertFalse(report['canonical_call_shape'])
                self.assertEqual(report['total_calls'], 1)
                self.assertEqual(report['observed_record_count'], 1)
                self.assertTrue(report['usage_complete'])
                self.assertEqual(report['total_usage'], expected_usage)
                self.assertEqual(report['known_usage'], expected_usage)
                self.assertEqual(report['core_v4']['total_calls'], 1)
                self.assertEqual(report['core_v4']['usage'], expected_usage)
                claimed_accepted = dataclasses.replace(failed, accepted=True, failure_class=None, errors=())
                with self.assertRaisesRegex(ValueError, denial):
                    runtime.OccurrenceAudit.from_record(claimed_accepted.to_record())
        request = dispatch.dispatch_request(self.helper.ledger / reference.path, self.helper.ledger)
        self.assertEqual(request['scope']['task_id'], 'T-001')
        self.assertEqual(request['request']['model'], 'gpt-6.1-terra')
        self.assertEqual(request['request']['reasoning_effort'], 'xhigh')

    def test_rejected_task_mismatch_still_requires_trusted_dispatch_binding(self):
        reference, _ = self.accepted_bundle()
        expected = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / reference.path, ledger_root=self.helper.ledger,
            phase='worker', round_index=0, cwd_policy=test_runtime.WORKER_CWD)
        helper = test_runtime.RuntimeTests()
        payload = helper.payload('sdd_worker_initial')
        parsed = runtime.audit_jsonl(payload, expected, None)
        metadata = helper.valid_metadata(payload, expected, thread_id=parsed.thread_id)
        metadata.update(scope_id='OTHER', task_id='OTHER',
                        dispatch_binding=copy.deepcopy(expected.dispatch_binding))
        failed = runtime.audit_occurrence(payload, metadata, expected)
        self.assertFalse(failed.accepted)
        for field, value in (('task_id', 'OTHER'), ('prompt_sha256', '8' * 64),
                             ('routing_policy', {'profile': {'profile_id': PROFILE_BINDING['profile_id'],
                                 'profile_sha256': '8' * 64}, 'latency_preference': None})):
            with self.subTest(binding_field=field):
                record = failed.to_record()
                record['dispatch_binding']['scope'][field] = value
                with self.assertRaisesRegex(ValueError, 'scope does not match validated dispatch request'):
                    runtime.OccurrenceAudit.from_record(record)
        unbound_expectation = dataclasses.replace(expected, scope_id='OTHER', task_id='OTHER')
        denied = runtime.audit_occurrence(payload, metadata, unbound_expectation)
        self.assertFalse(denied.accepted)
        self.assertIn('dispatch binding does not bind occurrence task scope', denied.errors)

    def test_rejected_diagnostic_scope_and_phase_match_wire_domains_but_accepted_claims_do_not(self):
        reference, _ = self.accepted_bundle()
        expected = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / reference.path, ledger_root=self.helper.ledger,
            phase='worker', round_index=0, cwd_policy=test_runtime.WORKER_CWD)
        helper = test_runtime.RuntimeTests()
        payload = helper.payload('sdd_worker_initial')
        parsed = runtime.audit_jsonl(payload, expected, None)
        baseline = helper.valid_metadata(payload, expected, thread_id=parsed.thread_id)
        baseline['dispatch_binding'] = copy.deepcopy(expected.dispatch_binding)
        accounting = load_module('cmr_confirmation_accounting')
        schema_root = SKILL_ROOT / 'references/schemas'
        audit_schema = json.loads((schema_root / 'cmr-occurrence-audit-v5.schema.json').read_text())
        input_schema = json.loads((schema_root / 'cmr-execution-accounting-input-v2.schema.json').read_text())
        branches = input_schema['properties']['occurrences']['items']['oneOf']
        accepted = runtime.audit_occurrence(payload, baseline, expected)
        self.assertTrue(accepted.accepted, accepted.errors)
        self.assertTrue(observation_wire_domains_allow(audit_schema, accepted.to_record()))
        self.assertEqual([observation_wire_domains_allow(branch, accepted.to_record())
                          for branch in branches], [False, True])
        release = {'scope_kind': 'release', 'scope_id': 'b' * 64, 'task_id': None}
        review = {'cwd': test_runtime.REVIEW_CWD, 'write_policy': 'source_read_only'}
        variants = {
            'release_worker': {**release, 'round_index': 1},
            'release_re_review': {**release, **review, 'phase': 're_review', 'round_index': 1},
            'final_review': {**release, **review, 'phase': 'final_review', 'round_index': None},
            'planning': {'phase': 'planning', 'scope_kind': 'plan', 'scope_id': 'b' * 64,
                'task_id': None, 'round_index': None, 'response_policy': 'closed_json',
                'cwd': test_runtime.CONTROL_CWDS['planning'], 'write_policy': 'control_plane_no_write'},
            'controller': {'phase': 'controller', 'round_index': None, 'response_policy': 'closed_json',
                'cwd': test_runtime.CONTROL_CWDS['controller'], 'write_policy': 'control_plane_no_write'},
            'preflight': {'phase': 'preflight', 'round_index': None, 'response_policy': 'closed_json',
                'cwd': test_runtime.CONTROL_CWDS['preflight'], 'write_policy': 'control_plane_no_write'},
        }
        expected_usage = {'input_tokens': 101, 'cached_input_tokens': 17,
            'cache_write_input_tokens': 5, 'output_tokens': 23, 'reasoning_output_tokens': 7}
        for name, changes in variants.items():
            with self.subTest(diagnostic=name):
                metadata = copy.deepcopy(baseline)
                metadata.update(changes)
                observed = runtime.audit_occurrence(payload, metadata, expected)
                self.assertFalse(observed.accepted)
                self.assertEqual(observed.failure_class, 'instrument')
                record = observed.to_record()
                self.assertEqual(runtime.OccurrenceAudit.from_record(record).to_record(), record)
                self.assertEqual(record['usage'], expected_usage)
                self.assertTrue(observation_wire_domains_allow(audit_schema, record), name)
                self.assertEqual([observation_wire_domains_allow(branch, record)
                                  for branch in branches], [False, True], name)
                request = {'schema_version': 'cmr-execution-accounting-input-v2',
                    'occurrences': [record], 'task_states': [], 'plan_sha256': 'a' * 64,
                    'release_review': None, 'semantic_results': None, 'instrument_errors': [],
                    'task_preflight_evidence': {}}
                self.assertEqual(accounting.validate_execution_accounting_input(request), [])
                request_ref = self.helper.json_artifact('inputs/diagnostic-' + name + '.json', request)
                process = subprocess.run([sys.executable, '-B', str(SKILL_ROOT / 'scripts/codex_model_router.py'),
                    'check-execution-call-shape', str(self.helper.ledger / request_ref.path), str(self.helper.ledger)],
                    text=True, capture_output=True, check=False)
                value = json.loads(process.stdout)
                self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
                self.assertFalse(value['valid'])
                self.assertFalse(value['report']['canonical_call_shape'])
                self.assertEqual(value['report']['total_calls'], 1)
                self.assertTrue(value['report']['usage_complete'])
                self.assertEqual(value['report']['total_usage'], expected_usage)
                self.assertEqual(value['report']['known_usage'], expected_usage)
                claimed = {**record, 'accepted': True, 'failure_class': None, 'errors': []}
                self.assertFalse(observation_wire_domains_allow(audit_schema, claimed), name)
                self.assertEqual([observation_wire_domains_allow(branch, claimed)
                                  for branch in branches], [False, False], name)
                with self.assertRaises(ValueError):
                    runtime.OccurrenceAudit.from_record(claimed)

    def test_accounting_cannot_drop_binding_or_substitute_other_runtime_for_new_profile(self):
        reference, artifacts = self.accepted_bundle()
        expected = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / reference.path, ledger_root=self.helper.ledger,
            phase='worker', round_index=0, cwd_policy=test_runtime.WORKER_CWD)
        helper = test_runtime.RuntimeTests()
        payload = helper.payload('sdd_worker_initial')
        parsed = runtime.audit_jsonl(payload, expected, None)
        metadata = helper.valid_metadata(payload, expected, thread_id=parsed.thread_id)
        metadata['dispatch_binding'] = copy.deepcopy(expected.dispatch_binding)
        accepted = runtime.audit_occurrence(payload, metadata, expected)
        accounting = load_module('cmr_confirmation_accounting')
        states = [runtime.TaskAccountingState('T-001', 'run', True, (0,))]
        sources = {'T-001': {'preflight_evidence': artifacts.evidence.to_dict(),
                             'runtime_input': self.inputs['runtime_ref'].to_dict()}}
        legacy = dataclasses.replace(accepted, schema_version='cmr-occurrence-audit-v4',
                                     dispatch_binding=None, model='gpt-6-luna')
        summary = accounting.summarize_execution_accounting([legacy], states, None, [],
            plan_sha256='a' * 64, release_review=None, task_preflight_evidence=sources,
            ledger_root=self.helper.ledger)
        self.assertTrue(any('requires version-5 dispatch-bound audit' in item for item in summary['accounting_errors']),
                        summary['accounting_errors'])
        # A separate frozen runtime is well formed, but is not the runtime in
        # this task's validated dispatch bundle.
        other = copy.deepcopy(self.inputs['runtime'])
        other['routing_policy']['latency_preference'] = None
        other_ref = self.helper.json_artifact('inputs/other-runtime.json', other)
        sources['T-001']['runtime_input'] = other_ref.to_dict()
        summary = accounting.summarize_execution_accounting([accepted], states, None, [],
            plan_sha256='a' * 64, release_review=None, task_preflight_evidence=sources,
            ledger_root=self.helper.ledger)
        self.assertTrue(any('runtime does not bind dispatch bundle' in item for item in summary['accounting_errors']),
                        summary['accounting_errors'])

    def test_terra_worker_and_review_accounting_bind_validated_dispatch_and_observed_route(self):
        reference, _ = self.accepted_bundle()
        expected = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / reference.path, ledger_root=self.helper.ledger,
            phase='worker', round_index=0, cwd_policy=test_runtime.WORKER_CWD)
        self.assertEqual((expected.model, expected.reasoning_effort), ('gpt-6.1-terra', 'xhigh'))
        helper = test_runtime.RuntimeTests()
        payload = helper.payload('sdd_worker_initial')
        parsed = runtime.audit_jsonl(payload, expected, None)
        metadata = helper.valid_metadata(payload, expected, thread_id=parsed.thread_id)
        metadata['dispatch_binding'] = copy.deepcopy(expected.dispatch_binding)
        observed = runtime.audit_occurrence(payload, metadata, expected)
        self.assertTrue(observed.accepted, observed.errors)
        record = observed.to_record()
        self.assertEqual(record['schema_version'], 'cmr-occurrence-audit-v5')
        self.assertEqual(runtime.OccurrenceAudit.from_record(record).to_record(), record)
        self.assertEqual(record['dispatch_binding']['scope']['routing_policy'], self.inputs['runtime']['routing_policy'])
        summary = runtime.summarize_accounting([observed], [], None, [], plan_sha256='a' * 64, release_review=None)
        self.assertEqual(summary['total_calls'], 1)
        self.assertEqual(summary['usage']['input_tokens'], observed.usage.input_tokens)
        metadata['model'] = 'gpt-6-luna'
        wrong = runtime.audit_occurrence(payload, metadata, expected)
        self.assertFalse(wrong.accepted)
        metadata['model'] = expected.model
        metadata['dispatch_binding']['scope']['routing_policy']['latency_preference'] = None
        wrong = runtime.audit_occurrence(payload, metadata, expected)
        self.assertFalse(wrong.accepted)
        review = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / reference.path, ledger_root=self.helper.ledger,
            phase='task_review', round_index=0, cwd_policy=test_runtime.REVIEW_CWD,
            prompt_sha256='7' * 64, forbidden_thread_ids=(parsed.thread_id,))
        self.assertEqual((review.model, review.reasoning_effort, review.thread_policy),
                         ('gpt-6.1-sol', 'medium', 'fresh'))
        with self.assertRaises(ValueError):
            runtime.OccurrenceExpectation.for_sdd(phase='worker', scope_kind='task', scope_id='T-001',
                round_index=0, task_id='T-001', initial_worker_model='gpt-6.1-terra',
                initial_worker_effort='xhigh', prompt_sha256=self.inputs['brief'].sha256,
                cwd_policy=test_runtime.WORKER_CWD)
        for effort in ('low', 'medium', 'high', 'max'):
            wrong_record = copy.deepcopy(record)
            wrong_record['reasoning_effort'] = effort
            with self.assertRaises(ValueError):
                runtime.OccurrenceAudit.from_record(wrong_record)
        old = copy.deepcopy(record)
        old['schema_version'] = 'cmr-occurrence-audit-v4'
        old.pop('dispatch_binding')
        with self.assertRaises(ValueError):
            runtime.OccurrenceAudit.from_record(old)


class LatencyAccountingReviewTests(unittest.TestCase):
    """Exact review reproductions using fresh sealed, skip-eligible v5 tasks."""

    @classmethod
    def setUpClass(cls):
        test_dispatch.DispatchTests.setUpClass()
        test_runtime.RuntimeTests.setUpClass()

    def setUp(self):
        self.helper = test_dispatch.DispatchTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.accounting = load_module('cmr_confirmation_accounting')
        planning, briefs, self.plan, _ = self.helper.planning_inputs()
        safe = dispatch.materialize_safe_lane(planning, ledger_root=self.helper.ledger)
        self.inputs = self.helper.controller_bundle_inputs(brief=briefs['T-001'], runtime_changes={
            'schema_version': 'cmr-runtime-input-v5',
            'routing_policy': policy('T-001', briefs['T-001'], enabled=False)})
        selector = dispatch.select_preflight(self.inputs['decision'],
            manifest_path=self.helper.ledger / safe.manifest.path, ledger_root=self.helper.ledger)
        self.assertEqual(selector['decision'], 'skip')
        self.preflight = self.helper.materialize_preflight(task_id='T-001', selector=selector,
            candidate_decision=self.inputs['decision_ref'], bound_preflight=None,
            transition=None, ledger_root=self.helper.ledger)
        self.bundle = self.helper.materialize_dispatch_bundle(task_id='T-001', brief=self.inputs['brief'],
            controller_evidence=self.inputs['evidence_ref'], runtime_input=self.inputs['runtime_ref'],
            candidate_decision=self.inputs['decision_ref'], preflight_evidence=self.preflight.evidence,
            final_decision=self.preflight.final_decision, ledger_root=self.helper.ledger)
        self.expected_worker = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / self.bundle.path, ledger_root=self.helper.ledger,
            phase='worker', round_index=0, cwd_policy=test_runtime.WORKER_CWD)
        runtime_helper = test_runtime.RuntimeTests()
        self.worker_payload = runtime_helper.payload('sdd_worker_initial')
        parsed = runtime.audit_jsonl(self.worker_payload, self.expected_worker, None)
        self.worker_metadata = runtime_helper.valid_metadata(self.worker_payload, self.expected_worker,
                                                            thread_id=parsed.thread_id)
        self.worker_metadata['dispatch_binding'] = copy.deepcopy(self.expected_worker.dispatch_binding)
        self.worker = runtime.audit_occurrence(self.worker_payload, self.worker_metadata, self.expected_worker)
        self.assertTrue(self.worker.accepted, self.worker.errors)
        review_expected = runtime.OccurrenceExpectation.for_dispatch(
            bundle_path=self.helper.ledger / self.bundle.path, ledger_root=self.helper.ledger,
            phase='task_review', round_index=0, cwd_policy=test_runtime.REVIEW_CWD,
            prompt_sha256='7' * 64, forbidden_thread_ids=(self.worker.thread_id,))
        review_payload = runtime_helper.payload('sdd_reviewer')
        review_parsed = runtime.audit_jsonl(review_payload, review_expected, None)
        review_metadata = runtime_helper.valid_metadata(review_payload, review_expected,
                                                        thread_id=review_parsed.thread_id)
        review_metadata['dispatch_binding'] = copy.deepcopy(review_expected.dispatch_binding)
        self.review = runtime.audit_occurrence(review_payload, review_metadata, review_expected)
        self.assertTrue(self.review.accepted, self.review.errors)
        self.observations = [planning.occurrence, self.inputs['controller_occurrence'], self.worker, self.review]
        self.states = [runtime.TaskAccountingState('T-001', 'skip', True, (0,))]
        self.sources = {'T-001': {'preflight_evidence': self.preflight.evidence.to_dict(),
                                  'runtime_input': self.inputs['runtime_ref'].to_dict()}}
        control = self.summary(self.observations, self.sources)
        self.assertTrue(control['canonical_call_shape'], control['accounting_errors'])

    def summary(self, observations, sources):
        return self.accounting.summarize_execution_accounting(observations, self.states, None, [],
            plan_sha256=self.plan.sha256, release_review=None, task_preflight_evidence=sources,
            ledger_root=self.helper.ledger)

    def request(self, observations, sources, *, states=None):
        states = self.states if states is None else states
        return {'schema_version': 'cmr-execution-accounting-input-v2',
            'occurrences': [item.to_record() for item in observations],
            'task_states': [{**dataclasses.asdict(state),
                'completed_worker_rounds': list(state.completed_worker_rounds)} for state in states],
            'plan_sha256': self.plan.sha256, 'release_review': None, 'semantic_results': None,
            'instrument_errors': [], 'task_preflight_evidence': sources}

    def run_cli(self, request):
        reference = self.helper.json_artifact('inputs/review-regression-request.json', request)
        process = subprocess.run([sys.executable, '-B', str(SKILL_ROOT / 'scripts/codex_model_router.py'),
            'check-execution-call-shape', str(self.helper.ledger / reference.path), str(self.helper.ledger)],
            text=True, capture_output=True, check=False)
        return process, json.loads(process.stdout)

    def test_skipped_v5_task_cannot_downgrade_runtime_and_audits_to_hide_wrong_effort(self):
        legacy_runtime = copy.deepcopy(self.inputs['runtime'])
        legacy_runtime['schema_version'] = 'cmr-runtime-input-v4'
        legacy_runtime.pop('routing_policy')
        legacy_ref = self.helper.json_artifact('inputs/substituted-runtime-v4.json', legacy_runtime)
        sources = copy.deepcopy(self.sources)
        sources['T-001']['runtime_input'] = legacy_ref.to_dict()
        legacy_worker = dataclasses.replace(self.worker, schema_version='cmr-occurrence-audit-v4',
            dispatch_binding=None, reasoning_effort='max')
        legacy_review = dataclasses.replace(self.review, schema_version='cmr-occurrence-audit-v4',
            dispatch_binding=None)
        observations = self.observations[:2] + [legacy_worker, legacy_review]
        request = self.request(observations, sources)
        self.assertEqual(self.accounting.validate_execution_accounting_input(request), [])
        report = self.summary(observations, sources)
        self.assertFalse(report['canonical_call_shape'])
        self.assertTrue(any('runtime does not bind dispatch bundle' in error
                            for error in report['accounting_errors']), report['accounting_errors'])
        self.assertTrue(any('requires version-5 dispatch-bound audit' in error
                            for error in report['accounting_errors']), report['accounting_errors'])
        process, value = self.run_cli(request)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertFalse(value['valid'])
        self.assertFalse(value['report']['canonical_call_shape'])
        self.assertEqual(value['report']['total_calls'], 4)
        self.assertEqual(value['report']['total_usage'], {'input_tokens': 276, 'cached_input_tokens': 56,
            'cache_write_input_tokens': 16, 'output_tokens': 84, 'reasoning_output_tokens': 28})
        self.assertEqual(dispatch.validate_dispatch_bundle(
            self.helper.ledger / self.bundle.path, self.helper.ledger), [])
        self.assertEqual(dispatch.dispatch_request(self.helper.ledger / self.bundle.path,
            self.helper.ledger)['request']['reasoning_effort'], 'xhigh')

    def test_rejected_wrong_prompt_v5_attempt_roundtrips_with_usage_without_authorizing_it(self):
        metadata = copy.deepcopy(self.worker_metadata)
        metadata['prompt_sha256'] = '8' * 64
        failed = runtime.audit_occurrence(self.worker_payload, metadata, self.expected_worker)
        self.assertFalse(failed.accepted)
        self.assertEqual(failed.failure_class, 'instrument')
        self.assertIn('metadata prompt_sha256 does not match expectation', failed.errors)
        self.assertEqual(runtime.OccurrenceAudit.from_record(failed.to_record()).to_record(), failed.to_record())
        request = self.request([failed], {}, states=[])
        self.assertEqual(self.accounting.validate_execution_accounting_input(request), [])
        process, value = self.run_cli(request)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertFalse(value['valid'])
        self.assertIn('report', value)
        self.assertEqual(value['report']['total_calls'], 1)
        expected_usage = {'input_tokens': 101, 'cached_input_tokens': 17,
            'cache_write_input_tokens': 5, 'output_tokens': 23, 'reasoning_output_tokens': 7}
        self.assertEqual(value['report']['total_usage'], expected_usage)
        self.assertEqual(value['report']['known_usage'], expected_usage)
        self.assertFalse(value['report']['canonical_call_shape'])
        claimed_accepted = dataclasses.replace(failed, accepted=True, failure_class=None, errors=())
        with self.assertRaisesRegex(ValueError, 'does not bind initial worker prompt'):
            runtime.OccurrenceAudit.from_record(claimed_accepted.to_record())


if __name__ == '__main__':
    unittest.main()
