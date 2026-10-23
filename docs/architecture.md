# Architecture

Codex Model Router (CMR) is a deterministic control plane for routing Codex development work. GPT-6.1 Sol/medium coordinates and controls each task. A fresh Sol/medium controller emits ontology IDs and action intents; Python binds that response to the task brief and audited occurrence, then derives the worker route, review route, lifecycle, and external-action state.

## Control flow

1. Run the host dependency preflight before the controller. Use native collaboration whenever it can meet the occurrence's exact model, effort, context, isolation, and permission requirements.
2. Validate the closed `cmr-controller-result-v4` envelope and bind the controller occurrence and brief into route evidence.
3. Compile route precedence in code. Sol/max covers hard gates, recurrence, canonical plan certification, and final review. Sol/high covers integration and elevated risk. Sol/medium covers diagnosis, audit, judgment, reconciliation, and complex local work that does not qualify for the dense lane. Clear eligible implementation defaults to Luna/xhigh. Dense bounded implementation can use Luna/max only with the implementation phase, approved architecture, low blast radius, no uncertainty, none of the audit/judgment/reconciliation facts, and no higher gate.
4. Select preflight from frozen safe-lane evidence. Only an exact Luna/xhigh safe-lane entry may skip it; `run` uses a fresh Sol/medium occurrence. Luna/max always runs fresh preflight.
5. Validate the complete `cmr-dispatch-bundle-v4` before handoff to Superpowers SDD.
6. Give both Luna routes fresh, independent Sol/medium task review. Keep whole-branch final review as a separate Sol/max occurrence.

The compiler owns model, effort, review, status, lifecycle, and authorization projections. The controller cannot author those fields. The runtime validates fresh context, thread identity, prompt and artifact hashes, usage, and the closed JSON response shape. CMR v4 envelopes carry route evidence v5.

## Execution transport

Native collaboration is mandatory whenever its actual capabilities satisfy the occurrence. A busy slot means wait. CLI is an exception only for a documented, occurrence-scoped real native capability gap, such as missing native delegation or inability to set an exact required model, effort, fresh context, or required isolation. Before execution, document the observed gap, affected phase and task, exact model and effort, selected transport, and expected panel-visibility loss; notify the user that the CLI session will not be registered as a native child in the main conversation's panel.

The exception preserves the exact model, effort, context, isolation, permissions, skills, and dependencies. Convenience, JSON capture, cost, or a permission denial never justifies CLI. Stop if no admitted transport meets the full requirements. Record the native agent identity and parent-conversation association when native is used. Host registration does not prove visible rendering; panel appearance requires UI observation or explicit user confirmation.

## External actions

GitHub, issue, push, PR, merge, deploy, tag, release, plugin installation, and marketplace publication are independent action intents. A generic permission or previous readback does not authorize another action. CMR records each action as blocked, ready, or completed and performs no external mutation.

## Public boundary

The plugin ships deterministic Python source, schemas, references, tests, and community documentation. Private evaluation fixtures, transcripts, raw model responses, scores, invocation histories, and local ledgers are not release artifacts. `check_public_release.py` enforces the explicit public manifest, regular-file tree, JSON syntax, standard-library imports, and cross-file identity checks.
