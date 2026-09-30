# Dependencies

## Required host capabilities

- A current Codex app, CLI, or IDE surface that supports agent skills and
  subagents.
- Account access to the exact model slugs `gpt-6-luna`, `gpt-6-sol`, and `gpt-6-astra`.
  CMR requires Luna/medium, Sol/medium and Sol/high, and Astra/max. It does
  not require `ultra`.
- Superpowers with `superpowers:subagent-driven-development`. Previous releases
  were tested against Superpowers 6.3.0. The required skill was present
  and readable in 6.4.2 during this update; that is a host-capability check,
  not an end-to-end certification. Superpowers is not redistributed here.
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

Run the [host dependency preflight](../skills/codex-model-router/references/dependency-preflight.md)
before creating a controller. GPT-6.1 Sol is not an alias for GPT-6 Sol and is
not selected by this release. Availability must be checked on the active host;
API documentation alone cannot establish access through a Codex client.
