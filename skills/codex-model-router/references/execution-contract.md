# CMR execution contract

This reference governs the handoff after a route is compiled. The executable
contracts in `scripts/cmr_contracts.py`, `scripts/cmr_compiler.py`,
`scripts/cmr_dispatch.py`, and `scripts/cmr_runtime.py` are authoritative.

## Control plane

Use a fresh Luna/xhigh controller with `fork_turns: "none"`, an ephemeral
destroyed read-only CWD, and `control_plane_no_write`. Bind its occurrence,
brief, and raw artifact before compiling. The compiler emits
`cmr-route-decision-v1`; deterministic code owns worker, review, risk,
recurrence, execution, status, blockers, and external-action lifecycle.

The selector may skip only a current exact safe lane. A `run` records a fresh
Luna/max preflight invocation and closed result (`accept`, `replace`, or
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

Only a valid CMR dispatch bundle can hand off to
`superpowers:subagent-driven-development`. The implementer gets a fresh brief,
exact scope, base/head binding, `fork_turns: "none"`, and no authority to
create subagents or external writes. A fresh phase-correct task reviewer reads
the same brief, report, and diff in a read-only worktree. Fix rounds follow the
CMR escalation matrix. The final whole-branch reviewer is always fresh
gpt-5.6-sol/max.

## External effects

External actions are per-item state machines. `blocked` is
`authorized=false, preview=false, readback=false`; `ready` is
`true, true, false`; `completed` is `true, true, true`. A ready item needs a
non-empty resolved target; completed requires readback of that same target.
Authorization for one Issue, push, PR, merge, deploy, or skill installation
does not authorize another. Analysis, preparation, or preview never implies a
mutation.
