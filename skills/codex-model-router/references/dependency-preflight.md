# Host dependency preflight

CMR must fail closed when the host cannot execute its required workflow. Run
this check before invoking the Luna controller; it is a host check, not a CMR
model response.

## Required capabilities

- Codex with skills and subagents enabled;
- `gpt-6-luna`, `gpt-6-sol`, and `gpt-6-astra`, with Luna/medium,
  Sol/medium and Sol/high, and Astra/max;
- Python 3.10+ and Git for the deterministic scripts and SDD worktree workflow;
- the CMR skill itself;
- the enabled, readable skill `superpowers:subagent-driven-development`.

If the host exposes a Superpowers version, record it. Superpowers 6.3.0 was
previously tested. Version 6.4.2 was checked for a
present and readable SDD skill during this update; this is not end-to-end
certification. For any version, verify the actual required skill and host
capabilities instead of treating a version string as proof of compatibility.
GPT-6.1 Sol is not selected by this release: do not substitute it merely
because it is listed in API documentation.

## Missing dependency response

If any capability is missing, report the exact blocker and do not invoke the
controller, compiler, preflight, worker, or reviewer. For an unavailable model
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
