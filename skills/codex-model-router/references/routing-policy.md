# CMR routing policy

This is the decision reference for the deterministic CMR compiler. The
controller supplies only IDs from the published ontologies; it does not write
the route record.

The coordinator remains Sol/medium. Apply this matrix to each bounded unit of work,
including eligible units within an initiative that also has risky modules.
The coordinator must delegate eligible implementation to the exact Luna effort
below and must not implement that source itself. An unavailable effort blocks that dispatch;
it does not authorize a silent Sol fallback. Risk, judgment, recurrence, and
independent review retain the rules below.

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
| dense approved bounded local implementation | gpt-6-luna/max | gpt-6.1-sol/medium |
| clear isolated low-risk implementation | gpt-6-luna/xhigh | gpt-6.1-sol/medium |

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

The dense Luna/max lane requires `task_phase: "implementation"`, all three
facts `complex_local_execution`, `architecture_approved`, and `low_blast_radius`,
no uncertainty IDs, and none of `audit`, `judgment_required`, or `reconciliation`.
`architecture_approved` means the architecture and interfaces are already
decided; an open design decision is judgment work. Higher precedence gates
always win. Dense success uses reason ID `dense_approved_local_execution`.
Complex local work missing any required qualifier stays Sol/medium with reason
`complex_local_execution`. Diagnosis uses `judgment_required`; audit, judgment,
and reconciliation need no `low_blast_radius` qualifier to stay on Sol/medium.

Luna/xhigh is the default eligible implementation effort. Cost, urgency, a
prose request for max, or the presence of tests does not create dense facts or
override a higher route. Canonical plan certification remains Sol/max, separate
from the Sol/medium coordinator, controller, and preflight.

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
pair. The selector compares both model and effort. Dense Luna/max always runs
fresh preflight, even when its task and brief match a default safe lane. Missing or stale
global evidence means `safe_lane_unavailable`, not a down-route or a task
block.

Uncertainty IDs (`multiple_policy_facts`, `negation_contrast_or_condition`,
`indirect_or_ambiguous_implication`, and `controller_uncertainty`) force a
fresh semantic preflight. They do not authorize the preflight to become a
worker, review, or external mutation.

Both Luna efforts have the same independent Sol/medium task review and fix-loop
escalation: rounds 0–3 keep the initial worker effort, round 4 is fresh
Sol/medium, and round 5 is fresh Sol/max. These review rounds do not reset the
same-defect recurrence gate. Preflight replacement can raise xhigh to max;
lowering max to xhigh is forbidden.
