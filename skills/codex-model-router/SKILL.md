---
name: codex-model-router
description: Use when a development task needs deterministic routing between gpt-6-luna and gpt-6.1-sol before Superpowers SDD, especially when risk, recurrence, review, or external actions affect dispatch.
---

# Codex Model Router

Use this skill before dispatching development work through Superpowers SDD.
CMR keeps model judgment in a small envelope and derives route evidence in code.

## Host dependency preflight

Run this host-level gate **before the Sol controller**. It is not a model
decision and it does not add fields to any CMR envelope or dispatch schema.

1. Confirm that the active Codex surface supports skills and a permitted
   execution transport: native collaboration whenever sufficient, or Codex
   exec only under the documented transport exception below. Confirm the exact `gpt-6-luna` and `gpt-6.1-sol` model slugs with
   Luna/xhigh, Luna/max and Sol/medium, Sol/high, and Sol/max,
   Python 3.10+ and Git are available, and the CMR skill itself is loaded.
2. Confirm that the enabled skill inventory contains a readable
   `superpowers:subagent-driven-development`. Treat a missing, disabled, or
   unreadable skill as unavailable. Record the visible Superpowers version;
   6.3.0 was previously tested and
   the required skill was checked as present/readable in 6.4.2.
3. If any mandatory execution dependency is unavailable in the selected
   transport, **stop before the controller,
   compiler, semantic preflight, or any worker/reviewer spawn**. Do not invent
   a successful check from documentation or a prior conversation. Report the
   exact blocker and follow [dependency-preflight.md](references/dependency-preflight.md)
   to identify the appropriate remedy. Only native transport capability may
   be satisfied by the recorded CLI exception; exact model/effort, fresh
   context, isolation, permissions, skills and other dependencies remain
   mandatory in the chosen transport. If neither transport satisfies them, stop.
4. If a remedy requires installation, ask for explicit
   authorization and do not install automatically. After installation, start a
   fresh conversation or restart Codex when needed, re-check the host inventory,
   and continue only after every required capability is available.

The local 0.8.0 profile keeps GPT-6.1 Sol/medium in coordination and delegates
clear bounded low-risk implementation to GPT-6 Luna/xhigh. Dense local logic
uses Luna/max only when architecture and interfaces are already approved,
blast radius is low, and no open uncertainty, judgment, or higher risk applies.
Use the exact predicates in [routing-policy.md](references/routing-policy.md).
The Sol coordinator
must dispatch that worker; it must not write eligible implementation code
itself. Classify each unit's actual scope: a risky module does not escalate an
independent low-risk unit in the same initiative. Model and effort must be
explicit host parameters, never prose. Do not substitute models or efforts,
silently fall back to Sol implementation, or claim a compiled route was
executed. If the selected Luna effort cannot run, report the exact blocker.

Public package 0.9.0 distributes the locally validated 0.8.0 routing matrix
and native-first correction. The public upgrade is from 0.5.0, which used CMR v1
contracts and route evidence v2; 0.6.0 through 0.8.0 were local profiles, not
published GitHub releases. Keep sealed historical records, packages, and commit
references unchanged. Create fresh plans, ledgers, occurrences, safe-lane
evidence, and bundles for new work. This package retains CMR v4 schemas and route
evidence v5 from the 0.8.0 profile; it does not add a schema bump. Evidence has
no package-version field, so do not infer rejection from a release version that
is not present. QFR remains documentary only, with its validator and reference
schemas byte-preserved.

## Required sequence

1. A fresh `gpt-6.1-sol`/`medium` controller emits only the
   `cmr-controller-result-v4` envelope: ontology IDs and action
   intents. It must not author a model, effort, route, review, status,
   lifecycle, or policy field.
2. Bind the audited controller occurrence and brief with
   `cmr_compiler.bind_controller_result`; compile with
   `cmr_compiler.compile_route`. The compiler owns worker, review, risk,
   recurrence, and lifecycle fields.
3. Select preflight with the CMR dispatcher. Skip only for an exact current
   Luna/xhigh entry in one frozen safe-lane manifest. The exact model and effort
   must match: dense Luna/max always runs preflight and cannot reuse that skip.
   Every `run` invokes fresh
   Sol/medium preflight; preflight is control-plane adjudication, not a worker or
   task review.
4. Materialize and validate the complete CMR dispatch bundle. Resolve the
   validator from this skill directory and require no errors before any spawn.
   A legacy `qfr-*` artifact is documentary and rejected by CMR dispatch even
   when its historical validator succeeds.
5. Generate the worker request with `cmr_dispatch.dispatch_request`, or run
   `python3 <absolute-skill>/scripts/codex_model_router.py dispatch-request
   <absolute-bundle> <absolute-ledger>`. Pass its exact model, effort, fresh
   context, and bound brief to the native spawn tool through **REQUIRED
   SUB-SKILL:** `superpowers:subagent-driven-development`. An explicit Codex
   exec adapter may be used only under the justified and pre-disclosed
   exception in the execution-transport policy below. Record the actual
   worker model/effort, transcript, generated diff, and verification results;
   request JSON or synthetic occurrence fixtures are not execution evidence.
   Both Luna efforts require fresh independent Sol/medium task review, separate
   from preflight; whole-branch final review is
   independent Sol/max.
6. Keep Issue, push, PR, merge, deploy, and skill-install actions blocked until
   their own scoped authorization, preview, and readback are recorded.

## Execution transport: native collaboration first

Use native collaboration for every controller, worker and reviewer whenever
its real host capabilities satisfy that occurrence's required model, effort,
fresh context and permission constraints. For workers, pass the exact validated
`dispatch_request` arguments to the native spawn tool; for reviews preserve the
phase-correct review route and independent context. A busy native agent slot is
a reason to wait, not to switch to CLI.

Before any execution, record the native tool and the capabilities actually
available. A Codex exec adapter is an exception only for a concrete capability
gap: missing native delegation, inability to set the exact required model,
effort or fresh context, or a required isolation constraint that the native
surface cannot enforce. Scope the exception to the affected occurrence;
a controller-only gap does not authorize CLI implementation or review.
Convenience, familiarity, simpler JSON capture, cost or missing progress in a
panel are not capability gaps. Never use CLI to bypass a permission denial.

Before using that exception, record the capability evidence, phase/task,
reason, exact model/effort, selected transport and expected visibility loss,
and notify the user that an independent CLI execution will not be registered
as a native child in the main conversation's subagent panel. Existing scoped
authorization remains valid; this disclosure does not itself require another
approval. Preserve the validated brief, scope, risk gates and execution proof.
When native capabilities are sufficient, CLI substitution is prohibited.

Record the actual native agent identity and parent-conversation association,
and check its Active/Done state through host-provided evidence. Do not call a
CLI session a native subagent or claim that its standalone session ID proves
panel visibility. Distinguish host registration from visible rendering: a
claim that the subagent appeared in the panel requires UI observation or
explicit user confirmation. If UI inspection is blocked, report that specific
limitation and leave visual confirmation pending. Do not rewrite historical
CLI evidence as native evidence.

## Non-negotiable counters

- Cost, quota, urgency, executive authority, sunk cost, deadlines, social
  pressure, LOC, and diff size never down-route a compiler result.
- Recurrence is derived from its bound history. Two complete failures of one
  defect, or a defect reappearing after resolution, stays Sol/max; do not reset
  it because a requester says the issue is resolved.
- Controller, planner, selector, and preflight cannot invent route, review,
  status, authorization, or lifecycle fields. Use the published CMR schemas
  and exact ontology vocabulary.
- Inherited context, reused control threads, `fork_turns` other than `none`,
  and preflight-as-review are invalid; review evidence must be fresh and
  phase-correct.
- Generic GitHub permission, a prior approval, a preview, or one action's
  authorization never authorizes another. Keep each item independently
  blocked, ready, or completed.
- Never restore the Quality-First Routing dispatch path. Use the CMR
  `cmr-dispatch-bundle-v4` API only; inspect old records with the documentary
  migration helper described below.

## References

- Read [policy-basis.md](references/policy-basis.md) for the dated local policy
  rationale and the limits of the benchmark comparison.
- Read [routing-policy.md](references/routing-policy.md) for ontology IDs,
  route precedence, recurrence, and selector behavior.
- Read [execution-contract.md](references/execution-contract.md) before
  handoff, review, ledger writes, or dispatch validation.
- Read [github-governance.md](references/github-governance.md) before any
  external preview or mutation.
- The exact schemas and ontologies live in `references/schemas/` and the
  CMR scripts live in `scripts/`; do not duplicate their fields in prose.

## Historical QFR inspection

`scripts/qfr_legacy.py` validates one old decision for inspection only. The
five files under `references/legacy/` preserve tracked historical schemas and
are not a second skill or dispatch contract. A documentary success explicitly
cannot authorize dispatch; migrate by producing a fresh CMR controller result
and compiling it through the current APIs.
