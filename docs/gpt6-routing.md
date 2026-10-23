# GPT-6 routing profile

Public package version 0.10.0 preserves the locally validated 0.8.0 routing matrix and the 0.9.0 native-first correction. Controller, preflight-result, route-decision, runtime-input, and route-evidence contracts remain v4/v5. Current dispatch adds `cmr-gate-evidence-v1`, `cmr-dispatch-bundle-v5`, and `cmr-preflight-invocation-v5`, plus `cmr-gate-confirmation-v1` and its closed `cmr-gate-confirmation-result-v1`; historical v4 bundle/invocation formats are not accepted for current dispatch.

| Role or situation | Model | Effort |
| --- | --- | --- |
| Coordinator, controller, semantic preflight, diagnosis, audit, judgment, reconciliation, and default review | `gpt-6.1-sol` | `medium` |
| Clear bounded low-risk implementation | `gpt-6-luna` | `xhigh` |
| Dense approved bounded local implementation | `gpt-6-luna` | `max` |
| Integration or elevated-risk implementation and review | `gpt-6.1-sol` | `high` |
| Hard gates, recurrence, canonical plan certification, and whole-branch final review | `gpt-6.1-sol` | `max` |

Both Luna routes receive independent Sol/medium task review. The Luna/max lane requires the implementation phase; `complex_local_execution`, `architecture_approved`, and `low_blast_radius`; no uncertainty IDs; none of `audit`, `judgment_required`, or `reconciliation`; and no higher-precedence gate. Missing any predicate keeps the work on the applicable Sol route. The default Luna/xhigh safe-lane selector checks the exact model and effort; Luna/max always receives a fresh Sol/medium semantic preflight.

The compiler never silently substitutes a model or effort. The active host must expose the exact pair before dispatch. Native collaboration is mandatory whenever it can meet the occurrence's model, effort, context, isolation, and permission requirements. CLI is allowed only for a documented, occurrence-scoped actual native capability gap after prior user notification that the CLI execution will not appear as a native child in the main conversation's panel. Busy slots, convenience, JSON capture, cost, and permission bypass do not qualify. An exception preserves the exact requirements; stop if no admitted transport can satisfy them. Host registration alone does not confirm visible panel rendering.

Each asserted hard-gate or integration/elevated-risk fact requires occurrence-specific evidence from the bound brief and exact audited source. A claim needs a verifiable quote, positive basis, and explanation that ties the requested change, concrete decision, or critical assurance to the gate. Context mentions, negation, preserved constraints, filenames, test presence, and ordinary build choices alone are insufficient. Missing, contradictory, context-only, or invalid evidence blocks without deleting facts or silently down-routing. Structural validation proves source and quote binding, not natural-language truth; fresh Sol/medium preflight independently assesses relevance. Initial diagnosis stays Sol/medium only when the same occurrence supports no stronger gate; a supported hard-gate or integration/elevated-risk fact takes precedence in that occurrence and is not deferred to a later unit. Real security, architecture, concurrency, integration, recurrence, and final-review gates keep their existing precedence.

## Evidence and limits

The policy rationale and the limits of benchmark comparisons appear in [policy-basis.md](../skills/codex-model-router/references/policy-basis.md). Those generic comparisons are not a direct coding-agent effort sweep and do not establish this repository's correctness, quality, latency, completed-task cost, or subscription usage. The regression suite validates routing and evidence integrity; it is not a model-quality benchmark or an end-to-end SDD quality certification.

## Public upgrade from 0.5.0

The last public package before 0.9.0 was 0.5.0, using CMR v1 contracts and route evidence v2. Profiles 0.6.0, 0.7.0, and 0.8.0 were local updates, not published GitHub releases. Package 0.9.0 directly distributed the validated policy with CMR v4 contracts and route evidence v5; 0.10.0 adds occurrence-bound gate evidence and current v5 dispatch contracts.

Keep sealed historical evidence and package/commit references unchanged. Create fresh plans, task ledgers, controller occurrences, gate evidence, preflight evidence, safe-lane manifests, and dispatch bundles for new work. The package version is not embedded in evidence, so do not infer or claim automatic rejection based on a nonexistent release-version field. Version 0.10.0 requires its new bundle and invocation contracts for current dispatch; do not edit sealed older records to retrofit them. QFR remains documentary and cannot authorize dispatch.

General UI defaults and model instructions do not override the exact model and
effort compiled for a dispatched occurrence.
