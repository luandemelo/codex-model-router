# Host dependency preflight

CMR must fail closed when the host cannot execute its required workflow. Run
this check before invoking the Luna controller; it is a host check, not a CMR
model response.

## Required capabilities

- Codex with skills and subagents enabled;
- `gpt-5.6-luna` and `gpt-5.6-sol`, including the `xhigh` and `max` efforts used
  by the routing policy;
- Python 3.10+ and Git for the deterministic scripts and SDD worktree workflow;
- the CMR skill itself;
- the enabled, readable skill `superpowers:subagent-driven-development`.

If the host exposes a Superpowers version, record it. CMR is tested against
Superpowers 6.3.0; a different version is unverified unless the required skill
is present and readable.

## Missing dependency response

If any capability is missing, report the exact blocker and do not invoke the
controller, compiler, preflight, worker, or reviewer. Ask for explicit
authorization before installation; CMR never installs plugins or skills
silently.

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
