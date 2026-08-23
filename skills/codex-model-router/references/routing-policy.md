# CMR routing policy

This is the decision reference for the deterministic CMR compiler. The
controller supplies only IDs from the published ontologies; it does not write
the route record.

## Precedence

The compiler applies the first matching rule:

| Condition | Worker | Fresh review |
| --- | --- | --- |
| branch final review | gpt-5.6-sol/max | gpt-5.6-sol/max |
| hard gate | gpt-5.6-sol/max | gpt-5.6-sol/max |
| recurring failure or reappeared defect | gpt-5.6-sol/max | gpt-5.6-sol/max |
| integration or elevated risk | gpt-5.6-sol/xhigh | gpt-5.6-sol/xhigh |
| explicit low-risk Luna/max eligibility | gpt-5.6-luna/max | gpt-5.6-luna/max |
| otherwise | gpt-5.6-luna/xhigh | gpt-5.6-luna/max |

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

Luna/max is closed eligibility, not a cheaper default: it requires low risk,
`low_blast_radius`, and either substantive `audit`, `judgment_required`, or
`reconciliation`, or `complex_local_execution` together with
`architecture_approved`. A controller role such as `implementation`, generic
classification work, urgency, or a request to avoid max does not qualify.

## Recurrence

The runtime derives recurrence from the exact `failure_history` events and
current defect binding. Two complete failures of the same defect are
`recurring_failure`; a valid resolution followed by `reappeared` is
`regression_after_declared_resolved`. A first failure is not recurrence.
Contradictory history, a missing current defect, or malformed events is a
runtime blocker. Do not reset a recurrence with prose, sunk cost, a deadline,
or a declaration unsupported by the event history.

## Semantic envelope and selector

`cmr-controller-result-v1` is exactly:

```json
{"schema_version":"cmr-controller-result-v1","fact_ids":[],"uncertainty_ids":[],"action_intents":[]}
```

The CMR selector is closed: `skip` has no triggers and one exact safe-lane
manifest reference; `run` has canonical trigger IDs and receives Luna/max
preflight; `block` is reserved for invalid artifact binding. Safe-lane
membership is exact to the frozen plan, brief, and task. Missing or stale
global evidence means `safe_lane_unavailable`, not a down-route or a task
block.

Uncertainty IDs (`multiple_policy_facts`, `negation_contrast_or_condition`,
`indirect_or_ambiguous_implication`, and `controller_uncertainty`) force a
fresh semantic preflight. They do not authorize the preflight to become a
worker, review, or external mutation.
