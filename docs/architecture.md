# Architecture

Codex Model Router (CMR) is a deterministic control plane for routing Codex development work. GPT-6.1 Sol/medium coordinates and controls each task. A fresh Sol/medium controller emits ontology IDs and action intents; Python binds that response to the task brief and audited occurrence, then derives the worker route, review route, lifecycle, and external-action state.

## Control flow

1. Run the host dependency preflight before the controller. Use native collaboration whenever it can meet the occurrence's exact model, effort, context, isolation, and permission requirements.
2. Validate the closed `cmr-controller-result-v4` envelope and bind the controller occurrence, brief, and required `cmr-gate-evidence-v1` artifact.
3. Compile route precedence in code from validated, source-bound gate evidence. Sol/max covers hard gates, recurrence, canonical plan certification, and final review. Sol/high covers integration and elevated risk. Sol/medium covers diagnosis, audit, judgment, reconciliation, and complex local work that does not qualify for the dense lane. Clear eligible implementation defaults to Luna/xhigh. Dense bounded implementation can use Luna/max only with the implementation phase, approved architecture, low blast radius, no uncertainty, none of the audit/judgment/reconciliation facts, and no higher gate.
4. Select preflight from frozen safe-lane evidence. Only an exact Luna/xhigh safe-lane entry may skip it; `run` binds candidate gate evidence to a fresh Sol/medium occurrence and checks its semantic relevance. Luna/max always runs fresh preflight. A replacement uses evidence tied to that exact preflight result; nonempty final replacement claims require one second, independent Sol/medium confirmation (`cmr-gate-confirmation-v1` with closed `cmr-gate-confirmation-result-v1`) before dispatch.
5. Validate the complete `cmr-dispatch-bundle-v5` and its `cmr-preflight-invocation-v5` before handoff to Superpowers SDD.
6. Give both Luna routes fresh, independent Sol/medium task review. Keep whole-branch final review as a separate Sol/max occurrence.

The compiler owns model, effort, review, status, lifecycle, and authorization projections. The controller cannot author those fields. Its fresh invocation receives the exact task and phase, ontology IDs, and current concrete-gate applicability policy; that guidance supports classification but does not prove claim relevance. Structural gate-evidence checks bind source, brief, task phase, quote bytes, and required coverage; they do not prove that natural-language text establishes risk. Preflight makes that semantic assessment against the full task and phase. Missing, contradictory, context-only, or invalid evidence blocks without deleting a fact or lowering the route. The runtime validates fresh context, thread identity, prompt and artifact hashes, usage, and closed JSON response shapes. Controller, route-decision, runtime-input, and route-evidence contracts remain v4/v5; current dispatch uses bundle-v5 and preflight-invocation-v5.

Execution accounting keeps the existing occurrence/runtime v4 records unchanged. The current accounting facade separates core calls from gate-confirmation attempts by their bound task and replacement packet, then reports their aggregate usage once across the complete supplied occurrence inventory. A block still counts as one confirmation call, not a retry; failed and extra attempts remain visible, and unknown usage makes the report incomplete. The existing v4 call-shape result is core-only when confirmations exist. Accounting supports audit and release reporting; it does not authorize dispatch.

## Execution transport

Native collaboration is mandatory whenever its actual capabilities satisfy the occurrence. A busy slot means wait. CLI is an exception only for a documented, occurrence-scoped real native capability gap, such as missing native delegation or inability to set an exact required model, effort, fresh context, or required isolation. Before execution, document the observed gap, affected phase and task, exact model and effort, selected transport, and expected panel-visibility loss; notify the user that the CLI session will not be registered as a native child in the main conversation's panel.

The exception preserves the exact model, effort, context, isolation, permissions, skills, and dependencies. Convenience, JSON capture, cost, or a permission denial never justifies CLI. Stop if no admitted transport meets the full requirements. Record the native agent identity and parent-conversation association when native is used. Host registration does not prove visible rendering; panel appearance requires UI observation or explicit user confirmation.

## External actions

GitHub, issue, push, PR, merge, deploy, tag, release, plugin installation, and marketplace publication are independent action intents. A generic permission or previous readback does not authorize another action. CMR records each action as blocked, ready, or completed and performs no external mutation.

## Public boundary

The plugin ships deterministic Python source, schemas, references, tests, and community documentation. Private evaluation fixtures, transcripts, raw model responses, scores, invocation histories, and local ledgers are not release artifacts. `check_public_release.py` enforces the explicit public manifest, regular-file tree, JSON syntax, standard-library imports, and cross-file identity checks.
