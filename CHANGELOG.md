# Changelog

All notable changes to Codex Model Router are documented here.

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
