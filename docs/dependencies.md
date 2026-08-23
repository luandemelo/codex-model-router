# Dependencies

## Required host capabilities

- A current Codex app, CLI, or IDE surface that supports agent skills and
  subagents.
- Account access to the exact model slugs `gpt-5.6-luna` and `gpt-5.6-sol`.
  CMR uses `xhigh` and `max` reasoning efforts according to its routing policy.
- Superpowers with `superpowers:subagent-driven-development`. This release is
  tested against Superpowers 6.3.0, which is a host dependency and is not
  redistributed here.
- Python 3.10 or newer with the standard library only.
- Git for local history and review workflows.

There is no runtime PyPI, npm, database, hosted-service, API-key, or MCP
dependency. The six CLI commands use only Python standard-library modules and
the sibling CMR source files.

## Optional integrations

GitHub CLI and GitHub Issues are optional. They can support separately
authorized repository, issue, and pull-request workflows; the plugin never
invokes them automatically. Plugin installation and marketplace publication
are also optional and remain independent from local validation.

Model availability, Codex skill/subagent support, and Superpowers are host
capabilities rather than bundled dependencies. Python 3.10, 3.11, 3.12, and
3.13 are exercised in CI.

## Preflight and recovery

CMR checks the required host capabilities before invoking the Luna controller.
The check must confirm an enabled, readable
`superpowers:subagent-driven-development` skill, model/effort access, Python
3.10+, Git, and the CMR skill itself. A missing, disabled, or unreadable
capability blocks controller, compiler, semantic preflight, and worker/reviewer
execution. CMR does not silently downgrade or install dependencies.

Installation is a separately authorized action. In the Codex app, open
**Plugins**, find **Superpowers** in the Coding section, and click `+`. In the
Codex CLI, run `/plugins`, search for `superpowers`, and select **Install
Plugin**. Start a fresh conversation or restart Codex if the skill list is
stale, then confirm `$superpowers:subagent-driven-development` is available.
The canonical behavior and recovery message live in
[`skills/codex-model-router/references/dependency-preflight.md`](../skills/codex-model-router/references/dependency-preflight.md).
