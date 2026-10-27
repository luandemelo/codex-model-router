---
name: codex-model-router
description: Use when a development task needs deterministic routing among gpt-6-luna, gpt-6.1-sol, and an explicitly selected gpt-6.1-terra latency route before Superpowers SDD, especially when risk, recurrence, review, or external actions affect dispatch.
---

# Codex Model Router

Use this skill before dispatching development work through Superpowers SDD.
CMR keeps model judgment in a small envelope and derives route evidence in code.

## Host dependency preflight

Run this host-level gate **before the Sol controller**. It is not a model
decision and it does not add fields to any CMR envelope or dispatch schema.

1. Confirm that the active Codex surface supports skills and a permitted
   execution transport: native collaboration whenever sufficient, or Codex
   exec only under the documented transport exception below. Confirm the exact
   `gpt-6-luna` and `gpt-6.1-sol` model slugs with Luna/xhigh, Luna/max and
   Sol/medium, Sol/high, and Sol/max before the controller. After
   `dispatch-request` selects Terra, confirm `gpt-6.1-terra`/xhigh before
   spawning that worker. Terra is optional for default Luna work; its absence
   does not block that route and never triggers a substitute model. Confirm
   Python 3.10+, Git, and the loaded CMR skill.
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

The published profile retains the locally validated 0.8.0 routing matrix:
GPT-6.1 Sol/medium coordinates, while clear bounded low-risk implementation
defaults to GPT-6 Luna/xhigh. Dense local logic uses Luna/max only when
architecture and interfaces are already approved, blast radius is low, and no
open uncertainty, judgment, or higher risk applies. An explicit, task-specific
latency preference can select GPT-6.1 Terra/xhigh for otherwise Luna-eligible
implementation. The selected Terra route trades lower latency for potentially
higher model cost. Luna/xhigh remains the default when no valid preference is
bound. Use the exact predicates in [routing-policy.md](references/routing-policy.md).
The Sol coordinator must dispatch the compiled route; it must not write
eligible implementation code itself. Classify each unit's actual scope: a
risky module does not escalate an independent low-risk unit in the same
initiative. Model and effort must be explicit host parameters, never prose. Do
not substitute models or efforts, silently fall back, or claim a compiled route
was executed. If the selected model/effort cannot run, report the exact blocker.

Public package 0.11.0 preserves the locally validated 0.8.0 routing matrix and
native-first correction distributed in 0.9.0, and adds the bound Terra latency
preference. The last public package before
0.9.0 was 0.5.0, which used CMR v1 contracts and route evidence v2; 0.6.0
through 0.8.0 were local profiles, not published GitHub releases. Controller-
result-v4, preflight-result-v4, route-evidence-v5, route-decision-v4,
runtime-input-v4, occurrence-audit-v4, and execution-accounting-input-v1 remain
unchanged historical contracts. Runtime-input-v5 and route-decision-v5 carry
the new routing policy. Current dispatch still uses bundle-v5 and
preflight-invocation-v5 with mandatory gate evidence; a v5 decision produces
dispatch-request-v2 with its exact policy in `scope`. New v5 occurrence audits
are accounted with execution-accounting-input-v2. Keep sealed historical
records, packages, and commit references unchanged; create fresh plans,
occurrences, sidecars, and bundles for new work. Package versions are not
embedded in evidence, so do not infer rejection from a nonexistent version
field. QFR remains documentary only, with its validator and reference schemas
byte-preserved.

## Explicit latency preference

The default remains Luna/xhigh for clear eligible implementation and Luna/max
for dense approved local implementation. A task may choose Terra/xhigh only
with a valid explicit latency preference bound to that implementation's task,
phase, complete brief reference, quoted UTF-8 byte span, and current routing
profile. The preference records the latency-versus-model-cost tradeoff; it
cannot name a model, effort, or route. Use
`cmr_latency.profile_binding()` and the published
[`cmr-latency-preference-v1`](references/schemas/cmr-latency-preference-v1.schema.json)
contract. Malformed, stale, or contradictory evidence blocks. Risk, recurrence,
judgment, review, semantic-preflight, replacement-confirmation, and
external-action gates keep their normal precedence.

Under existing scoped authorization, the coordinator may choose a task-specific
latency preference and record it in that task's brief; no additional human
confirmation is required for each use. The exact brief evidence and CMR binding
are still required for every opted-in task.

Latency routing requires `cmr-runtime-input-v5`; do not add preference fields
to `cmr-runtime-input-v4`. Its `routing_policy` contains the current profile
binding and either a null preference or the task-bound preference. Null keeps
the default Luna routes. A v5 decision retains that policy, including when a
stronger gate selects Sol. If Terra/xhigh is selected, the active host must
support that exact pair; missing support blocks that dispatch without changing
the default Luna route or substituting another model.

Every asserted hard-gate or integration/elevated-risk fact needs occurrence-
specific evidence bound to the task brief and exact audited source. Support each
claim with a verifiable brief quote, a positive basis (requested change,
concrete decision, or critical assurance), and an explanation of why the text
requires that gate. An explicit empty record is required when no such facts are
asserted. Context mentions, negations, preserved constraints, filenames, test
presence, and ordinary build choices alone do not establish risk. Missing,
contradictory, context-only, or invalid evidence blocks; never remove the fact
or silently down-route. Structural validation proves binding and coverage, not
natural-language truth, so preflight assesses relevance against the full task
and phase. Correct scope or classification with a fresh brief and
classification, preserving sealed history and monotonicity. Initial diagnosis
uses Sol/medium only when the same occurrence supports no stronger gate. If the
brief supports a hard-gate or integration/elevated-risk fact, route it at that
precedence in this occurrence; do not defer it to a later unit. A stronger
route requires an actual requested change, concrete decision, or critical
assurance;
real high-risk gates retain precedence regardless of cost or convenience.

## Required sequence

1. Give the fresh `gpt-6.1-sol`/`medium` controller the exact task and phase,
   applicable ontology IDs, and current concrete-gate applicability policy in
   its invocation context; it cannot rely on the coordinator having read the
   policy. It emits only the `cmr-controller-result-v4` envelope: ontology IDs
   and action intents. It must not author a model, effort, route, review,
   status, lifecycle, or policy field. This context does not guarantee correct
   classification; gate support must still be evidenced and independently
   assessed.
2. Bind the audited controller occurrence and brief, and create the required
   `cmr-gate-evidence-v1` sidecar at
   `tasks/<task>/gate-evidence/controller.json`. Claims cover every asserted
   hard-gate and integration/elevated-risk fact exactly once in lexical order;
   an empty claim list is required when none are asserted. Each claim has a
   positive basis, a nonempty quote matching a UTF-8 byte slice of the brief,
   and a nonempty explanation. Bind the sidecar before route compilation.
   For a latency opt-in, use `cmr-runtime-input-v5`; v4 retains its original
   route semantics and does not accept the new policy. Compile through
   `cmr_compiler.compile_route(...,
   gate_evidence=bound_gate_evidence, brief_bytes=brief_bytes)`; both keyword
   inputs are required. CLI compilation also requires explicit
   `--gate-evidence SIDECAR_PATH --brief BRIEF_PATH` flags. The compiler owns
   worker, review, risk, recurrence, and lifecycle fields.
3. Select preflight with the CMR dispatcher. Skip only for an exact current
   Luna/xhigh entry in one frozen safe-lane manifest. The exact model and effort
   must match: dense Luna/max and Terra/xhigh always run preflight and cannot
   reuse that skip. Every `run` invokes fresh Sol/medium preflight. For a v5
   preference, append the deterministic `build_preflight_latency_attachment`
   exactly once immediately before the existing exact gate-evidence
   attachment; then bind the full prompt and exact sidecar bytes. The v5
   invocation binds the candidate sidecar and the full prompt includes its
   exact bytes. Preflight
   assesses relevance against the full brief and phase; it does not delete
   facts or lower routes. A `replace` result requires a replacement sidecar
   bound to that audited preflight result, including when it has no gate claims.
4. Materialize preflight artifacts with
   `cmr_dispatch.materialize_preflight` after freezing the required sidecars;
   it replays the transition and seals the evidence directory. If `replace`
   leaves any final gate claims, require one new independent Sol/medium
   preflight-phase confirmation before dispatch. Build its prompt from the
   frozen packet with `build_gate_confirmation_prompt` and bind the audited
   result with `bind_gate_confirmation_result`. It emits only the
   closed `cmr-gate-confirmation-result-v1` result (`accept` or `block`), uses a
   fresh control context and a different thread from the controller and
   proposing preflight, and binds the exact task, phase, brief, runtime input,
   both classifications, both gate sidecars, final decision, and proposing
   invocation. Seal its artifact `cmr-gate-confirmation-v1` at
   `tasks/<task>/gate-confirmation/evidence.json` with
   `materialize_gate_confirmation`. A block, missing result, or
   invalid binding terminates that replacement; correction needs fresh
   classification with history preserved. Replace with no final gate claims
   needs no confirmation; `skip` and `accept` require a null confirmation.
   Confirmation cannot add facts or a route and does not replace task or final
   review.
5. Materialize and validate the complete CMR dispatch bundle-v5. Resolve the
   validator from this skill directory and require no errors before any spawn.
   Current dispatch rejects historical bundle-v4 records with no implicit
   fallback. Inspect historical CMR v4 through the retained 0.9.0 source,
   schemas, and package. A legacy `qfr-*` artifact is documentary and cannot
   authorize CMR dispatch. When producing complete execution accounting, use
   `python -B scripts/codex_model_router.py check-execution-call-shape INPUT
   LEDGER_ROOT` with the full occurrence inventory; the v4 `check-call-shape`
   result is core-only when gate confirmations exist. See the execution
   contract for input bindings, usage handling, and report semantics.
6. Generate the worker request with `cmr_dispatch.dispatch_request`, or run
   `python3 <absolute-skill>/scripts/codex_model_router.py dispatch-request
   <absolute-bundle> <absolute-ledger>`. For a v5 decision, the returned
   `cmr-dispatch-request-v2` carries the exact `routing_policy` in `scope`;
   `request` contains only native spawn arguments. Pass its exact model, effort,
   fresh context, and bound brief to the native spawn tool through **REQUIRED
   SUB-SKILL:** `superpowers:subagent-driven-development`. An explicit Codex
   exec adapter may be used only under the justified and pre-disclosed
   exception in the execution-transport policy below. Record the actual
   worker model/effort, transcript, generated diff, and verification results;
   request JSON or synthetic occurrence fixtures are not execution evidence.
   Luna/xhigh, Luna/max, and Terra/xhigh each require fresh independent
   Sol/medium task review, separate from preflight; whole-branch final review
   is independent Sol/max. Terra cannot use the Luna/xhigh safe-lane skip.
7. Keep Issue, push, PR, merge, deploy, and skill-install actions blocked until
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
  current `cmr-dispatch-bundle-v5` path only. Inspect historical CMR v4
  artifacts with the retained 0.9.0 source, schemas, and package; the
  `qfr_legacy.py` documentary helper is only for QFR records. Never edit sealed
  historical records to retrofit current evidence.

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

`scripts/qfr_legacy.py` validates only an old QFR decision for inspection; it
does not inspect historical CMR v4 artifacts, which use the retained 0.9.0
source, schemas, and package. The
five files under `references/legacy/` preserve tracked historical schemas and
are not a second skill or dispatch contract. A documentary success explicitly
cannot authorize dispatch; migrate by producing a fresh CMR controller result
and compiling it through the current APIs.
