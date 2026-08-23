---
name: codex-model-router
description: Use when a development task needs deterministic routing between gpt-5.6-luna and gpt-5.6-sol before Superpowers SDD, especially when risk, recurrence, review, or external actions affect dispatch.
---

# Codex Model Router

Use this skill before dispatching development work through Superpowers SDD.
CMR keeps model judgment in a small envelope and derives route evidence in code.

## Host dependency preflight

Run this host-level gate **before the Luna controller**. It is not a model
decision and it does not add fields to any CMR envelope or dispatch schema.

1. Confirm that the active Codex surface supports skills and subagents, the
   exact `gpt-5.6-luna` and `gpt-5.6-sol` model slugs with the required efforts,
   Python 3.10+ and Git are available, and the CMR skill itself is loaded.
2. Confirm that the enabled skill inventory contains a readable
   `superpowers:subagent-driven-development`. Treat a missing, disabled, or
   unreadable skill as unavailable. This release is tested with Superpowers
   6.3.0; record a visible version when the host provides one.
3. If any required capability is unavailable, **stop before the controller,
   compiler, semantic preflight, or any worker/reviewer spawn**. Do not invent
   a successful check from documentation or a prior conversation. Report the
   exact blocker and follow [dependency-preflight.md](references/dependency-preflight.md)
   to recommend installation.
4. Installation is a separately authorized external action: ask for explicit
   authorization and do not install automatically. After installation, start a
   fresh conversation or restart Codex when needed, re-check the host inventory,
   and continue only after every required capability is available.

## Required sequence

1. A fresh `gpt-5.6-luna`/`xhigh` controller emits only the
   `cmr-controller-result-v1` envelope: ontology IDs and action
   intents. It must not author a model, effort, route, review, status,
   lifecycle, or policy field.
2. Bind the audited controller occurrence and brief with
   `cmr_compiler.bind_controller_result`; compile with
   `cmr_compiler.compile_route`. The compiler owns worker, review, risk,
   recurrence, and lifecycle fields.
3. Select preflight with the CMR dispatcher. Skip only for an exact current
   Luna/xhigh entry in one frozen safe-lane manifest. Every `run` invokes fresh
   Luna/max preflight; preflight is control-plane adjudication, not a worker or
   task review.
4. Materialize and validate the complete CMR dispatch bundle. Resolve the
   validator from this skill directory and require no errors before any spawn.
   A legacy `qfr-*` artifact is documentary and rejected by CMR dispatch even
   when its historical validator succeeds.
5. Hand a valid bundle to **REQUIRED SUB-SKILL:** use
   `superpowers:subagent-driven-development`. Give the worker a fresh brief,
   `fork_turns: "none"`, and the approved scope. Fresh task review is separate
   from preflight; whole-branch final review is Sol/max.
6. Keep Issue, push, PR, merge, deploy, and skill-install actions blocked until
   their own scoped authorization, preview, and readback are recorded.

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
  `cmr-dispatch-bundle-v1` API only; inspect old records with the documentary
  migration helper described below.

## References

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
