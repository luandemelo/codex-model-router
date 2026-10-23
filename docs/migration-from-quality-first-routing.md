# Migrating from Quality-First Routing and CMR 0.5.0

The current public skill package is version 0.9.0. The last preceding public package was 0.5.0, which used CMR v1 contracts and route evidence v2. Versions 0.6.0, 0.7.0, and 0.8.0 were local profiles, not published GitHub releases. Version 0.9.0 distributes the validated 0.8.0 routing matrix and native-first correction with CMR v4 contracts and route evidence v5; it does not introduce a schema bump from 0.8.0.

## What changes from 0.5.0

- The controller, coordinator, and semantic preflight use GPT-6.1 Sol/medium.
- Clear bounded low-risk implementation uses GPT-6 Luna/xhigh and gets independent Sol/medium task review.
- Dense approved bounded local implementation can use Luna/max under the exact implementation-phase predicates and also gets independent Sol/medium review.
- Diagnosis, audit, judgment, reconciliation, and complex local work that fails a dense predicate use Sol/medium. Integration or elevated risk uses Sol/high. Hard gates, recurrence, canonical plan certification, and whole-branch final review use Sol/max.
- The current package uses CMR v4 schemas and route evidence v5. New plans and ledgers must be created for new work.
- Native collaboration is mandatory whenever the host supports the exact occurrence. CLI is a documented, pre-disclosed, occurrence-scoped exception for a real native capability gap only.

## Preserve history

Keep 0.5.0 records, their package, and their commit reference sealed for historical validation. Do not rewrite old records or change their model strings. The intermediate 0.6.0-0.8.0 profiles were local, so they are not public GitHub release references. CMR evidence does not contain a package-version field that can identify its source release; do not claim that the current validator rejects a record based on a nonexistent release-version field. Current CMR v4 schemas and route evidence v5 are explicit contracts, while QFR remains documentary and cannot authorize CMR dispatch.

## Safe migration sequence

1. Preserve existing QFR or 0.5.0 evidence as read-only historical input and inspect QFR with the legacy validator where applicable.
2. Reconstruct the current task brief and use exact CMR ontology IDs; omit invented route or review fields.
3. Create fresh plans, task ledgers, controller occurrences, preflight evidence, safe-lane manifests, and dispatch bundles under the current package.
4. Run the current host dependency preflight and current CMR preflight/dispatch validation. Use the exact compiled model, effort, context, permissions, and isolation requirements.
5. Hand off through native collaboration whenever capable. A CLI exception requires the occurrence-scoped capability record and prior user notification, including expected panel-visibility loss; otherwise do not execute through CLI. Stop if no admitted transport satisfies the requirements.
6. Keep every external action blocked until its independent authorization, preview, execution, and readback are recorded.

For transport requirements, see [dependency preflight](../skills/codex-model-router/references/dependency-preflight.md) and [execution contract](../skills/codex-model-router/references/execution-contract.md).
