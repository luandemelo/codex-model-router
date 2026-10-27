# Host dependency preflight

CMR must fail closed when the host cannot execute its required workflow. Run
this check before invoking the Sol controller; it is a host check, not a CMR
model response.

## Required capabilities

- Codex with skills enabled and an admitted execution transport: native
  collaboration whenever sufficient, or only the recorded and pre-disclosed
  CLI exception described in SKILL.md;
- `gpt-6-luna` and `gpt-6.1-sol`, with Luna/xhigh, Luna/max and
  Sol/medium, Sol/high, and Sol/max;
- `gpt-6.1-terra`/xhigh is an optional dependency. Require it only when the
  compiled route selects Terra. Its absence does not block a default Luna route
  and never permits an automatic model fallback;
- Python 3.10+ and Git for the deterministic scripts and SDD worktree workflow;
- the CMR skill itself;
- the enabled, readable skill `superpowers:subagent-driven-development`.

If the host exposes a Superpowers version, record it. Superpowers 6.3.0 was
previously tested. Version 6.4.2 was checked for a
present and readable SDD skill during this update; this is not end-to-end
certification. For any version, verify the actual required skill and host
capabilities instead of treating a version string as proof of compatibility.
Public package 0.11.0 preserves the locally validated 0.8.0 routing matrix
distributed in 0.9.0: GPT-6.1 Sol/medium coordinates, GPT-6 Luna/xhigh handles
clear eligible implementation by default, and Luna/max handles dense approved
bounded local logic. A valid, explicit task-specific latency preference can
select Terra/xhigh for otherwise Luna-eligible implementation. Its profile and
preference are bound through runtime-input-v5 and route-decision-v5; v4 inputs
retain their original shapes and semantics. Current dispatch continues to
require bundle-v5 and preflight-invocation-v5 with gate-evidence-v1. A v5
decision yields dispatch-request-v2, and v5 occurrence audits use
execution-accounting-input-v2. Verify the active host's exact model slugs and
efforts for the route actually selected; do not infer availability from API
documentation.

When a v5 dispatch request selects Terra/xhigh, check that exact pair before
spawn. If Terra is absent, stop that selected dispatch and report the blocker;
do not substitute Luna or Sol. Without a Terra selection, proceed with the
default Luna route after checking the Luna and Sol capabilities.

Host capability checks do not establish task risk. Every asserted hard-gate or
integration/elevated-risk fact needs a source-bound sidecar with a verifiable
quote from the exact task brief, a positive basis, and an explanation. Missing,
contradictory, context-only, or invalid evidence blocks without deleting facts
or silently down-routing. Structural validation proves bindings and coverage,
not natural-language relevance; fresh Sol/medium preflight assesses claims
against the full task and phase. A replacement with nonempty final gate claims
requires one independent Sol/medium accept/block confirmation before dispatch.

## Native transport capability check

Inspect the real native delegation tool before selecting an execution adapter.
Confirm support for the required model/effort and independent context, and any
phase-specific permission/CWD constraints. Prefer native collaboration whenever
these capabilities are sufficient. Only a concrete capability gap may enable
an occurrence-scoped CLI exception; record it and notify the user, including
the panel-visibility limitation, before execution. Follow the execution-transport
policy in SKILL.md. Missing telemetry convenience or occupied slots does not
justify substituting CLI. A control-plane-only gap never enables CLI workers.

## Missing dependency response

If any mandatory execution dependency is missing in the chosen transport,
report the exact blocker and do not invoke the controller, compiler, preflight,
worker, or reviewer. Only native transport capability may be satisfied by the
documented CLI exception; exact model/effort, independent context, isolation,
permissions, skills and all other dependencies must still be satisfied by that
adapter. If no admitted transport satisfies them, stop. For an unavailable model
or effort, check the active client's model list and account/workspace access;
installing a skill cannot grant model access. Do not silently choose another
model or effort. If the remedy requires installation, ask for explicit
authorization before installation; CMR never installs plugins or skills
silently. Honor existing authorization for that same scoped action.

For Superpowers:

1. **Codex app:** open **Plugins** in the sidebar, find **Superpowers** in the
   Coding section, click `+`, and follow the prompts.
2. **Codex CLI:** run `/plugins`, search for `superpowers`, and select
   **Install Plugin**.
3. Start a fresh conversation or restart Codex if the skill list is stale.
4. Confirm that `$superpowers:subagent-driven-development` is available before
   retrying `$codex-model-router`.

Do not substitute another workflow, silently downgrade to direct coding, or
continue based on a previous installation check. If installation is not
authorized or still unavailable, leave the task blocked and explain what is
needed.
