# CMR execution contract

This reference governs the handoff after a route is compiled. The executable
contracts in `scripts/cmr_contracts.py`, `scripts/cmr_compiler.py`,
`scripts/cmr_dispatch.py`, and `scripts/cmr_runtime.py` are authoritative.

## Host gate

Before any controller invocation, run the host dependency preflight described in
[`dependency-preflight.md`](dependency-preflight.md). It is outside the CMR
schemas: the host must confirm the CMR skill, model/effort access, Python/Git,
and a readable `superpowers:subagent-driven-development`. If a mandatory
execution dependency is missing in the chosen transport, stop before controller,
compiler, preflight, or spawn and report the appropriate remedy. Only native
transport capability may be satisfied through the recorded CLI exception; all
model/effort, context, isolation, permission and dependency constraints remain
mandatory in that adapter. Installation requires scoped authorization; installing a
skill does not grant access to an unavailable model.

## Control plane

Keep the coordinator on Sol/medium. Canonical planning remains a separate
Sol/max occurrence. Use a fresh Sol/medium controller with
`fork_turns: "none"`, an ephemeral
destroyed read-only CWD, and `control_plane_no_write`. Bind its occurrence,
brief, and raw artifact before compiling. The compiler emits
`cmr-route-decision-v4`; deterministic code owns worker, review, risk,
recurrence, execution, status, blockers, and external-action lifecycle.

The selector may skip only a current exact safe lane. A `run` records a fresh
Sol/medium preflight invocation and closed result (`accept`, `replace`, or
`block`). Preflight is control-plane evidence: its thread cannot be reused as
worker or review evidence, and it never satisfies task review.

## Artifacts and dispatch

All ledger references are relative, normalized POSIX paths with lowercase
SHA-256 and exact byte sizes. Freeze the brief, controller evidence, sidecar,
preflight evidence, final route, and dispatch bundle before spawn. Validate
the bundle from the absolute skill directory and absolute ledger root; require
the real CMR dispatcher to return no errors. A schema beginning `qfr-` returns
`legacy_dispatch_forbidden`, regardless of any documentary validation result.

Do not resolve the validator from the caller's CWD. Do not rewrite or reopen a
sealed artifact, reuse a plan ledger for a new canonical plan, or accept a
pointer whose bytes, path, identity, or digest drifted.

## SDD handoff and reviews

Call `cmr_dispatch.dispatch_request(bundle_path, ledger_root)` or the CLI
`python3 <absolute-skill>/scripts/codex_model_router.py dispatch-request
<absolute-bundle> <absolute-ledger>` before handoff. The adapter revalidates
the full current bundle and reads the frozen, hash-bound UTF-8 brief. Its
`request` contains exactly `task_name`, `message`, `model`, `reasoning_effort`,
and `fork_turns` for a native spawn; `scope` binds the task, brief, bundle,
final decision, and prompt SHA-256. The message is the literal brief, so the
prompt digest matches the bytes actually sent. Pass all request fields to the
host through native collaboration when its capabilities satisfy the occurrence.
The execution-transport policy in SKILL.md is mandatory. A Codex exec adapter
is allowed only for a concrete, recorded native capability gap, scoped to that
occurrence and disclosed to the user before execution, including the loss of
main-panel visibility. It must map model and effort to real host parameters
and use a fresh invocation with the literal prompt. Do not silently switch
transport when native execution is available. This is an execution request,
not evidence that a worker ran.

An eligible clear low-risk implementation unit must run as Luna/xhigh. Dense
bounded local logic with approved architecture/interfaces, low blast radius,
and no uncertainty or higher gates runs as Luna/max; use the exact predicates
in `routing-policy.md`. Both efforts require independent Sol/medium task review.
The Sol
coordinator may prepare its scope, review its diff, and run verification, but
must delegate generation of that implementation. Classify each unit on its
own dependencies and risk; unrelated risky work does not change its route.
If the host cannot run the exact pair, stop that dispatch and report the
capability blocker without silently coding it in Sol.

Only a valid CMR dispatch bundle can hand off to
`superpowers:subagent-driven-development`. The implementer gets a fresh brief,
exact scope, base/head binding, `fork_turns: "none"`, and no authority to
create subagents or external writes. A fresh phase-correct task reviewer reads
the same brief, report, and diff in a read-only worktree. Fix rounds follow the
CMR escalation matrix. The final whole-branch reviewer is always fresh
gpt-6.1-sol/max.

Retain actual host model/effort metadata, worker transcript, generated diff,
and verification results to establish that Luna produced the code. A route,
request JSON, or synthetic test occurrence cannot establish live execution.

For native execution, retain the native agent identity and parent association.
Host Active/Done registration is execution evidence; visible panel rendering
requires UI observation or explicit user confirmation. If either is unavailable,
report its absence instead of treating a CLI transcript as native visibility.

## External effects

External actions are per-item state machines. `blocked` is
`authorized=false, preview=false, readback=false`; `ready` is
`true, true, false`; `completed` is `true, true, true`. A ready item needs a
non-empty resolved target; completed requires readback of that same target.
Authorization for one Issue, push, PR, merge, deploy, or skill installation
does not authorize another. Analysis, preparation, or preview never implies a
mutation.
