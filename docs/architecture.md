# Architecture

Codex Model Router (CMR) is a deterministic hybrid control plane for routing
Codex development work. A fresh Luna/xhigh controller emits only ontology IDs
and action intents. Python code binds that response to the task brief and
audited occurrence, then derives the worker route, review route, lifecycle,
and external-action state.

## Control flow

1. Run the host dependency preflight before spending a controller turn. Require
   the CMR skill, model access, Python/Git, and readable
   `superpowers:subagent-driven-development`; missing capabilities block the
   workflow and require separately authorized installation.
2. Validate the closed `cmr-controller-result-v1` envelope.
3. Bind the controller occurrence and brief into route evidence.
4. Compile route precedence in code: hard gates, recurrence, and final review
   use Sol/max; integration or elevated risk uses Sol/xhigh; eligible
   low-blast-radius audit, reconciliation, judgment, or approved local work
   may use Luna/max; the default is Luna/xhigh.
5. Select a preflight from the frozen safe-lane evidence. A `run` uses a fresh
   Luna/max occurrence; a current exact safe-lane entry may be skipped.
6. Validate the complete `cmr-dispatch-bundle-v1` before handing it to
   Superpowers SDD. Historical QFR records are documentary and cannot dispatch.

The compiler owns model, effort, review, status, lifecycle, and authorization
projections. The controller cannot author those fields. The runtime validates
fresh context, thread identity, prompt and artifact hashes, usage, and the
closed JSON response shape.

## External actions

GitHub, issue, push, PR, merge, deploy, tag, release, plugin installation, and
marketplace publication are independent action intents. A generic permission
or previous readback does not authorize another action. CMR records each
action as blocked, ready, or completed and performs no external mutation.

## Public boundary

The plugin ships deterministic Python source, schemas, references, tests, and
community documentation. Private evaluation fixtures, transcripts, raw model
responses, scores, invocation histories, and local ledgers are not release
artifacts. `check_public_release.py` enforces the explicit public manifest,
regular-file tree, JSON syntax, standard-library imports, and cross-file
identity checks.
