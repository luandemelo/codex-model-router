# GitHub and external-action governance

GitHub is a separately authorized projection, never the routing authority.
Specs, plans, source, tests, and the local SDD ledger remain canonical in
their respective roles. A GitHub Issue or comment cannot approve a push,
merge, deploy, PR operation, or skill installation.

## Per-action protocol

For every external item, independently:

1. Resolve the repository, operation, and exact target.
2. Verify authorization naming that action and target.
3. Show the exact payload preview without secrets.
4. Execute only the approved ready item.
5. Read back the same target and operation.
6. Record the result as completed only after readback.

Repository-scoped targets stay fully qualified: push uses
`<owner>/<repo>:<full-ref>`, deploy uses `<owner>/<repo>:<environment>`, and
PR/Issue targets retain repository and identifier. An isolated ref or
environment is not a resolved target. Missing scope keeps only that item
blocked and does not promote the model or block safe local work.

The CMR controller may emit an action intent, but deterministic compilation
creates the lifecycle item from the exact authorization registry. Legacy maps
such as `github_write_authorized` are documentary and cannot fill missing
authorization, promote `blocked`, or authorize a different action.

Never place credentials, cookies, tokens, or unnecessary payloads in the
ledger, Issue, PR, or review package. If readback differs or fails, retain a
safe state and request a new scoped preview; do not retry a mutation silently.
