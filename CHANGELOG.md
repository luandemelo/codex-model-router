# Changelog

All notable changes to Codex Model Router are documented here.

## [0.10.0] - 2026-10-23

- Preserved the 0.8.0 routing matrix distributed in 0.9.0 and its native-first execution policy.
- Required occurrence-bound `cmr-gate-evidence-v1` for every dispatch branch. Positive claims quote the bound brief and identify the requested change, concrete decision, or critical assurance; structural checks bind evidence but semantic relevance remains a preflight judgment.
- Added current `cmr-dispatch-bundle-v5` and `cmr-preflight-invocation-v5` contracts. Existing controller-result-v4, preflight-result-v4, route-evidence-v5, route-decision-v4, and runtime-input-v4 contracts remain unchanged; historical v4 bundle/invocation records do not authorize current dispatch.
- Added `cmr-gate-confirmation-v1` and its closed `cmr-gate-confirmation-result-v1`. One fresh independent Sol/medium accept/block confirmation is required before dispatch when a preflight replacement has nonempty final gate claims. A block or invalid confirmation ends that occurrence; correction requires fresh classification while preserving its history.
- Added the read-only `cmr_confirmation_accounting.py` facade, closed `cmr-execution-accounting-input-v1` / `cmr-execution-accounting-v1` schemas, and `check-execution-call-shape` CLI for complete core and packet-bound gate-confirmation accounting. Runtime/occurrence v4 records remain unchanged; each unique raw-call identity contributes once, valid blocks are confirmation calls rather than retries, and unknown usage is reported as incomplete.
- Required current task, phase, ontology, and concrete-gate applicability guidance in the fresh controller invocation. CLI compilation reads its evidence, optional runtime, sidecar, and brief from frozen no-follow inputs.
- Preserved sealed historical records and package references. New work uses fresh bound artifacts; no old record is retrofitted.

## [0.9.0] - 2026-10-23

- Published the locally validated 0.8.0 routing matrix: GPT-6.1 Sol/medium coordinates, controls, diagnoses, and reviews Luna work; Luna/xhigh handles clear bounded low-risk implementation; Luna/max is limited to dense approved bounded local implementation; Sol/high handles integration and elevated risk; Sol/max handles hard gates, recurrence, canonical planning, and final review.
- Made native collaboration mandatory whenever the host can satisfy an occurrence's exact model, effort, context, isolation, and permission requirements. Documented the narrow occurrence-scoped CLI exception, prior user notice, and distinction between host registration and visible panel confirmation.
- Updated public documentation and packaging metadata. This distribution retains CMR v4 schemas and route evidence v5; it does not introduce a schema bump from the local 0.8.0 profile.
- Documented the direct public upgrade from 0.5.0 and preserved sealed evidence and historical package references. Profiles 0.6.0 through 0.8.0 were local and were not published GitHub releases. QFR remains documentary.

## [0.5.0] - 2026-09-30

- Moved the current routing profile to GPT-6 Luna/medium, GPT-6 Sol/medium
  and high, and GPT-6 Astra/max, including controller, preflight, review,
  planning, recurrence, and fix-round escalation.
- Preserved deterministic risk precedence, independent review, exact evidence
  binding, and per-action authorization.
- Added the host dependency preflight to the public skill package.
- Documented benchmark evidence, its limits, supported local defaults, and
  why GPT-6.1 Sol is not silently substituted for the available Sol profile.
- Breaking policy update: keep 0.4.0 sealed evidence unchanged and create fresh
  ledgers under 0.5.0. Schema field layouts/identifiers stay unchanged, but old
  GPT-5.6 routes cannot authorize current dispatch. QFR remains documentary.

## [0.4.0] - 2026-08-22

- Renamed the public identity to `codex-model-router`.
- Added deterministic CMR controller validation, compilation, dispatch,
  documentary legacy inspection, occurrence auditing, and call-shape checks.
- Added a standard-library-only public release checker and exact file manifest.
- Added Apache-2.0 packaging, dependency documentation, CI, security guidance,
  Portuguese quickstart, and contributor DCO requirements.
