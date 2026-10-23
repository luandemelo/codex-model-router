# Dependencies

## Required host capabilities

- A current Codex app, CLI, or IDE surface with agent skills and native collaboration.
- Access to the exact model slugs `gpt-6-luna` and `gpt-6.1-sol`. Required pairs are Luna/xhigh and Luna/max; Sol/medium, Sol/high, and Sol/max.
- Superpowers with `superpowers:subagent-driven-development`. Superpowers 6.3.0 was previously tested; the required skill was checked as present and readable in 6.4.2 during the local profile update. These checks do not certify every host version end to end. Superpowers is not redistributed here.
- Python 3.10 or newer with the standard library only.
- Git for local history and review workflows.

Native collaboration is mandatory whenever the actual host capability meets an occurrence's exact model, effort, fresh context, isolation, and permission requirements. A busy native slot is a reason to wait. Do not use CLI for convenience, JSON capture, cost, or to bypass a permission denial.

CLI is an exception only for a documented real native capability gap, scoped to the affected occurrence. Before execution, record observed capabilities, affected phase/task, exact model and effort, selected transport, and expected loss of native panel visibility; notify the user that the independent CLI execution will not be registered as a native child in the main conversation's panel. The exception preserves model, effort, fresh context, isolation, permissions, skills, and other dependencies. If no admitted transport satisfies them, stop.

Host registration and visual panel confirmation are separate. Confirm visible rendering through UI observation or explicit user confirmation; a host registration record alone does not establish that the child appeared in the panel.

The host's actual model list and permission context determine whether a route can run. Do not substitute another model or effort when an exact pair is unavailable. Run the [host dependency preflight](../skills/codex-model-router/references/dependency-preflight.md) before creating a controller.

The 0.5.0 historical profile listed `gpt-6-sol` and `gpt-6-astra`; neither
is an active route in the current 0.10.0 package. The required current model
slugs are only `gpt-6-luna` and `gpt-6.1-sol`.

## Runtime boundary

There is no runtime PyPI, npm, database, hosted-service, API-key, or MCP dependency. The CMR command-line tools use Python standard-library modules and sibling CMR source files. Python 3.10, 3.11, 3.12, and 3.13 are exercised in CI.

GitHub CLI and GitHub Issues are optional for separately authorized repository and issue workflows; the plugin does not invoke them automatically. Plugin installation and marketplace publication are also independent from local validation.
