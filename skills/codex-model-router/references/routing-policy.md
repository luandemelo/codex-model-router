# CMR routing policy

This is the decision reference for the deterministic CMR compiler. The
controller supplies only IDs from the published ontologies; it does not write
the route record.

The coordinator remains Sol/medium. Apply this matrix to each bounded unit of
work, including eligible units within an initiative that also has risky
modules. The coordinator delegates eligible implementation to the exact
compiled model and effort below; it does not implement that source itself.
Luna/xhigh is the default eligible implementation route. Terra/xhigh is
available only when the current runtime binds a valid, explicit, task-specific
latency preference for an otherwise Luna-eligible implementation. An
unavailable selected effort blocks that dispatch; it does not authorize a
silent model substitution. Risk, judgment, recurrence, and independent review
retain precedence over a latency preference.

## Precedence

The compiler applies the first matching rule:

| Condition | Worker | Fresh review |
| --- | --- | --- |
| branch final review | gpt-6.1-sol/max | gpt-6.1-sol/max |
| recurring failure or reappeared defect | gpt-6.1-sol/max | gpt-6.1-sol/max |
| hard gate | gpt-6.1-sol/max | gpt-6.1-sol/max |
| integration or elevated risk | gpt-6.1-sol/high | gpt-6.1-sol/high |
| audit, judgment, diagnosis, or reconciliation | gpt-6.1-sol/medium | gpt-6.1-sol/medium |
| complex local work that fails the dense implementation predicates below | gpt-6.1-sol/medium | gpt-6.1-sol/medium |
| otherwise Luna-eligible implementation with a valid explicit task latency preference | gpt-6.1-terra/xhigh | gpt-6.1-sol/medium |
| dense approved bounded local implementation without a latency preference | gpt-6-luna/max | gpt-6.1-sol/medium |
| clear isolated low-risk implementation | gpt-6-luna/xhigh | gpt-6.1-sol/medium |

The Terra row is conditional, not a default. The exact `latency_preference` runtime object is
bound to the task, phase, complete brief reference and quoted UTF-8 byte span,
and the current profile binding. It cannot set a model, effort, or route. The
current profile is identified by `cmr_latency.profile_binding()`
(`luna-default-terra-latency-v1`, SHA-256
`a4fbe81eeed636e5e431fd43cfaaf73a4b9374293546cecc731025373886378c`). A null
preference preserves the Luna routes. Terra/xhigh is the only Terra route and
requires fresh independent Sol/medium task review. The Luna/xhigh safe-lane
skip does not apply to Terra.

Hard gates include architecture, authentication/authorization, schema
migration, rollback, concurrency, locks, idempotency, cryptography, secrets,
trust boundaries, billing/payment/price/invoice, financial ledgers,
entitlements, PII or regulated data, critical integrity, irreversible effects,
multiple plausible tradeoffs, structural API changes, and structural or
recurring failures. Use only IDs present in `references/fact-ontology.json`;
do not create aliases.

Integration or elevated-risk IDs include cross-module or multi-module
integration, contract coordination, integration tests, untrusted input,
security or incident risk, availability/integrity impact, critical risk, and
data integrity. A single-module change can still be elevated.

For example, changing two existing wrappers to share one original observation
deadline couples their completion and timeout behavior and supports
`integration_tests`/Sol-high when architecture, interfaces, and ownership are
already approved and the task preserves production concurrency mechanisms,
clock authority, scheduling, ownership, cancellation, and retry mechanisms.
The mere presence of an integration-test file or unchanged code compiled by an
integration test does not establish integration risk. A requested change or
critical assurance involving ordering, synchronization, concurrent ownership,
cancellation/races, or security still triggers its concrete hard gate in the
same occurrence; this example does not lower those gates.

The dense Luna/max lane requires `task_phase: "implementation"`, all three
facts `complex_local_execution`, `architecture_approved`, and `low_blast_radius`,
no uncertainty IDs, and none of `audit`, `judgment_required`, or `reconciliation`.
`architecture_approved` means the architecture and interfaces are already
decided; an open design decision is judgment work. Higher precedence gates
always win. Dense success uses reason ID `dense_approved_local_execution`.
Complex local work missing any required qualifier stays Sol/medium with reason
`complex_local_execution`. Diagnosis uses `judgment_required`; audit, judgment,
and reconciliation need no `low_blast_radius` qualifier to stay on Sol/medium.

Luna/xhigh is the default eligible implementation effort. A latency
preference is not model-selection authority by itself: only the exact validated
CMR binding can select Terra/xhigh. Cost, urgency, a prose request for max, or
the presence of tests does not create dense facts or override a higher route.
Hard gates, integration/elevated risk, recurrence, diagnosis or judgment, and
final review retain their compiled precedence even when the routing policy
contains a latency preference. If a valid preference coincides with unresolved
uncertainty, the compiler keeps the policy bound and routes the occurrence to
Sol/medium for judgment. Missing Terra capability blocks only a dispatch whose
compiled route selected Terra; it does not block a default Luna route and does
not cause automatic fallback. Canonical plan certification remains Sol/max,
separate from the Sol/medium coordinator, controller, and preflight.

## Gate evidence

Every asserted hard-gate or integration/elevated-risk fact needs occurrence-
specific evidence tied to the task brief and the exact audited controller or
preflight source. Each claim identifies a requested change, concrete decision,
or critical assurance with a verifiable brief quote and an explanation of how
that text requires the gate. An explicit empty evidence record is required when
no gate fact is asserted.

A context mention, negation, preserved constraint, filename, test presence, or
ordinary build choice alone does not establish risk. Missing, contradictory,
context-only, or invalid evidence blocks the occurrence; it does not authorize
deleting a fact or silently selecting a lower route. Structural validation can
verify bindings, quote bytes, and coverage, but cannot prove natural-language
relevance. Preflight must assess relevance against the full task and phase.

Correct a scope or classification with a fresh brief and classification while
preserving sealed history and route monotonicity. Initial diagnosis remains
Sol/medium only when the same occurrence supports no stronger gate. If its
brief supports a hard-gate or integration/elevated-risk fact, apply that higher
precedence in the same occurrence rather than deferring the work to a later
unit. A stronger route requires an actual requested change, concrete decision,
or critical assurance. Real authentication, cryptography, secrets, concurrency,
architecture, integration, recurrence, and final-review gates keep their
existing precedence; cost or implementation convenience never moves them to
Luna.

## Recurrence

The runtime derives recurrence from the exact `failure_history` events and
current defect binding. Two complete failures of the same defect are
`recurring_failure`; a valid resolution followed by `reappeared` is
`regression_after_declared_resolved`. A first failure is not recurrence.
Contradictory history, a missing current defect, or malformed events is a
runtime blocker. Do not reset a recurrence with prose, sunk cost, a deadline,
or a declaration unsupported by the event history.

## Semantic envelope and selector

`cmr-controller-result-v4` is exactly:

```json
{"schema_version":"cmr-controller-result-v4","fact_ids":[],"uncertainty_ids":[],"action_intents":[]}
```

The CMR selector is closed: `skip` has no triggers and one exact safe-lane
manifest reference; `run` has canonical trigger IDs and receives Sol/medium
preflight; `block` is reserved for invalid artifact binding. Safe-lane
membership is exact to the frozen plan, brief, task, and Luna/xhigh worker
pair. The selector compares both model and effort. Dense Luna/max and Terra/xhigh
always run fresh preflight, even when their task and brief match a default safe
lane. Missing or stale global evidence means `safe_lane_unavailable`, not a
down-route or a task block.

Uncertainty IDs (`multiple_policy_facts`, `negation_contrast_or_condition`,
`indirect_or_ambiguous_implication`, and `controller_uncertainty`) force a
fresh semantic preflight. They do not authorize the preflight to become a
worker, review, or external mutation.

Luna/xhigh, Luna/max, and Terra/xhigh have independent Sol/medium task review
and the same fix-loop escalation: rounds 0–3 keep the initial worker effort,
round 4 is fresh Sol/medium, and round 5 is fresh Sol/max. These review rounds do not reset the
same-defect recurrence gate. Preflight replacement can raise xhigh to max;
lowering max to xhigh is forbidden.
