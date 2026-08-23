# Codex Model Router

Codex Model Router (CMR) is a quality-first plugin for deterministic routing
of Codex development work between `gpt-5.6-luna` and `gpt-5.6-sol`. It keeps a
small model judgment envelope and derives route, review, recurrence, lifecycle,
and authorization state in auditable standard-library Python.

## Why it exists

Development tasks do not all have the same blast radius. A schema migration,
security boundary, recurring failure, or branch review needs a stronger route
than a bounded local audit. CMR makes those distinctions explicit and keeps
cost, urgency, social pressure, and executive authority from weakening a
route.

## Deterministic hybrid architecture

A fresh Luna/xhigh controller returns only facts, uncertainties, and requested
action intents. The compiler binds that response to a fresh brief and audited
occurrence. It owns the route matrix and emits a complete decision. The
dispatcher selects or runs a fresh Luna/max semantic preflight, then validates
the complete CMR dispatch bundle before Superpowers SDD receives it.

| Situation | Worker | Task review |
| --- | --- | --- |
| Hard gate, recurrence, canonical planning, or final review | Sol/max | Sol/max |
| Integration or elevated risk | Sol/xhigh | Sol/xhigh |
| Eligible low-blast-radius audit, reconciliation, judgment, or approved local work | Luna/max | Luna/max |
| Default isolated work | Luna/xhigh | Luna/max |

Historical QFR records can be inspected with `validate-legacy`, but they are
documentary and cannot authorize CMR dispatch.

## Quick install and use

Clone or download the public repository, then install the plugin through the
Codex surface you use. Direct skill development does not require a package
manager. From the repository root, validate a controller response:

```bash
python3 -B skills/codex-model-router/scripts/codex_model_router.py \
  validate-controller controller-result.json
```

The same CLI exposes `compile`, `validate-dispatch`, `validate-legacy`,
`audit-occurrence`, and `check-call-shape`. Each command reads real JSON or
JSONL files, emits one canonical JSON object, returns zero only for accepted
input, and prints a concise diagnostic to stderr on rejection.

For a planning `audit-occurrence`, the trusted expectation must include the
exact plan-order array `planning_task_ids`; it is bound to the same planning
scope/SHA and is forbidden for every other phase. `check-call-shape` accepts
only strict, duplicate-free `cmr-occurrence-audit-v1` records.

### Início rápido em português

Instale o plugin no Codex e confirme que sua conta oferece `gpt-5.6-luna` e
`gpt-5.6-sol`. No diretório do projeto, valide o resultado fechado do
controlador com o comando acima. Depois compile a evidência vinculada, valide
o bundle CMR e só então entregue o trabalho ao fluxo Superpowers SDD. O CMR
não chama o GitHub nem autoriza ações externas automaticamente.

## Dependencies and limitations

Python 3.10+ e Git são necessários, além de uma superfície Codex com skills e
subagentes e Superpowers SDD 6.3.0 (testado, não incluído). GitHub CLI, GitHub
Issues e instalação do plugin são opcionais. Veja
[`docs/dependencies.md`](docs/dependencies.md) for the full host matrix.

CMR does not observe a model's hidden reasoning, guarantee model availability,
or replace human review. It validates the evidence supplied to it; it does
not create private evidence, retry malformed responses, or repair an invalid
route. A preflight is control-plane adjudication, not worker execution or task
review.

## Authorization boundary

Issue creation, repository settings, pushes, pull requests, merges, deploys,
tags, releases, plugin installation, and marketplace publication each require
their own explicit authorization, preview, and readback. Preparing a file or
preview never performs that action. CMR has no GitHub network or shell side
effects.

## Tests

Run all skill tests and the local release checker:

```bash
python3 -B -m unittest discover -s skills/codex-model-router/tests -p 'test_*.py' -v
python3 -B skills/codex-model-router/scripts/check_public_release.py .
```

The CI workflow repeats these checks on Python 3.10 through 3.13 and parses
all JSON files. No test requires a network connection or mutates a remote.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md), use a focused branch, add a
deterministic regression test before implementation, update the changelog,
and submit a reviewed pull request with Developer Certificate of Origin sign
off. Security reports use the private process in [`SECURITY.md`](SECURITY.md).
