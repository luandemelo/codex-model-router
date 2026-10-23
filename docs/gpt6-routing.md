# GPT-6 routing profile

Public package version 0.9.0 distributes the locally validated 0.8.0 routing matrix and native-first correction. The package uses CMR v4 contracts and route evidence v5. This distribution does not bump schemas from the 0.8.0 profile.

| Role or situation | Model | Effort |
| --- | --- | --- |
| Coordinator, controller, semantic preflight, diagnosis, audit, judgment, reconciliation, and default review | `gpt-6.1-sol` | `medium` |
| Clear bounded low-risk implementation | `gpt-6-luna` | `xhigh` |
| Dense approved bounded local implementation | `gpt-6-luna` | `max` |
| Integration or elevated-risk implementation and review | `gpt-6.1-sol` | `high` |
| Hard gates, recurrence, canonical plan certification, and whole-branch final review | `gpt-6.1-sol` | `max` |

Both Luna routes receive independent Sol/medium task review. The Luna/max lane requires the implementation phase; `complex_local_execution`, `architecture_approved`, and `low_blast_radius`; no uncertainty IDs; none of `audit`, `judgment_required`, or `reconciliation`; and no higher-precedence gate. Missing any predicate keeps the work on the applicable Sol route. The default Luna/xhigh safe-lane selector checks the exact model and effort; Luna/max always receives a fresh Sol/medium semantic preflight.

The compiler never silently substitutes a model or effort. The active host must expose the exact pair before dispatch. Native collaboration is mandatory whenever it can meet the occurrence's model, effort, context, isolation, and permission requirements. CLI is allowed only for a documented, occurrence-scoped actual native capability gap after prior user notification that the CLI execution will not appear as a native child in the main conversation's panel. Busy slots, convenience, JSON capture, cost, and permission bypass do not qualify. An exception preserves the exact requirements; stop if no admitted transport can satisfy them. Host registration alone does not confirm visible panel rendering.

## Evidence and limits

The policy rationale and the limits of benchmark comparisons appear in [policy-basis.md](../skills/codex-model-router/references/policy-basis.md). Those generic comparisons are not a direct coding-agent effort sweep and do not establish this repository's correctness, quality, latency, completed-task cost, or subscription usage. The regression suite validates routing and evidence integrity; it is not a model-quality benchmark or an end-to-end SDD quality certification.

## Public upgrade from 0.5.0

The last public package before 0.9.0 was 0.5.0, using CMR v1 contracts and route evidence v2. Profiles 0.6.0, 0.7.0, and 0.8.0 were local updates, not published GitHub releases. The 0.9.0 package directly distributes the current validated policy with CMR v4 contracts and route evidence v5.

Keep sealed 0.5.0 evidence and its package/commit reference unchanged for historical validation. Create fresh plans, task ledgers, controller occurrences, preflight evidence, safe-lane manifests, and dispatch bundles for new work. The current schemas identify v4 contracts and v5 route evidence; 0.9.0 itself does not change those identifiers from the locally validated 0.8.0 profile. Evidence has no package-version field that can establish which release created it, so do not infer or claim automatic rejection of a record from a nonexistent release-version field. Changing model strings in historical evidence is not migration. QFR remains documentary and cannot authorize dispatch.

General UI defaults and model instructions do not override the exact model and
effort compiled for a dispatched occurrence.
