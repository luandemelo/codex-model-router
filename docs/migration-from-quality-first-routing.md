# Migrating from Quality-First Routing to Codex Model Router

`codex-model-router` is the published v0.4.0 skill and the only dispatch
surface for new work. It preserves the useful routing invariants while moving
the wire contract to closed CMR schemas and deterministic compiler/runtime
code.

## What changes

- Emit only `cmr-controller-result-v1` from a fresh Luna/xhigh controller.
- Bind the audited occurrence and compile `cmr-route-decision-v1` with the
  CMR compiler; do not author worker/reviewer/lifecycle fields in model prose.
- Use the CMR selector, Luna/max preflight for `run`, and
  `cmr-dispatch-bundle-v1` validation before SDD handoff.
- Keep each external action independently authorized, previewed, and read
  back. Cost, urgency, recurrence pressure, and social permission do not
  change compiler precedence.

## Inspecting historical records

`scripts/qfr_legacy.py` exposes
`validate_legacy_decision(record)`. It validates one tracked historical QFR
decision for documentary inspection. A successful message explicitly says
that the record cannot authorize dispatch. The five JSON references in
`references/legacy/` retain historical `qfr-*` versions with documentary-only
labels; they are not a second discoverable skill.

Unknown or malformed records fail closed. Do not copy campaign fixtures,
private adjudications, transcripts, token ledgers, raw responses, or evaluation
artifacts into a migration or new ledger. A legacy record that appears valid
must still be translated into a fresh CMR controller occurrence, compiled by
current code, and validated by the current dispatcher.

## Safe migration sequence

1. Preserve the old record as read-only input and inspect it with the legacy
   validator.
2. Reconstruct the current brief and exact CMR ontology IDs; omit invented
   route or review fields.
3. Run the fresh controller, bind its occurrence, compile, and resolve all
   artifact references in a new ledger.
4. Run CMR preflight/dispatch validation and then hand off to Superpowers SDD.
5. Treat every external action as blocked until its independent authorization,
   preview, and readback are recorded.
