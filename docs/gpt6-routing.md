# GPT-6 routing profile

Version 0.5.0 updates the model profile while retaining deterministic risk
precedence, recurrence escalation, independent reviews, artifact binding, and
external-action authorization. The profile is deliberately explicit:

| Role or situation | Model | Effort |
| --- | --- | --- |
| Controller and default isolated worker | `gpt-6-luna` | `medium` |
| Eligible substantive local work, semantic preflight, and default task review | `gpt-6-sol` | `medium` |
| Integration or elevated-risk worker and review | `gpt-6-sol` | `high` |
| Hard gates, recurring failures, canonical planning, and final review | `gpt-6-astra` | `max` |

The active host must expose every required model/effort pair before dispatch.
CMR does not infer account access from API documentation, substitute a model
on failure, or use `ultra`. Ultra is a Codex mode that delegates work across
agents; it is distinct from this compiler's per-occurrence effort selection.
See [Codex model guidance](https://learn.chatgpt.com/docs/models).

## Evidence and limits

Sources checked on September 30, 2026. In the same
[OpenAI Astra launch comparison](https://openai.com/index/gpt-6-astra/),
GPT-5.6 Sol and GPT-6 Astra scored as follows. These are the best scores at
any evaluated effort, not equal-effort or equal-cost measurements.

| Benchmark | GPT-5.6 Sol | GPT-6 Astra |
| --- | --- | --- |
| Terminal-Bench 4.0 | 37.3% | 57.9% |
| FrontierCode 1.1 Main | 47.5% | 53.3% |
| DeepSWE v1.1 | 72.7% | 74.1% |

The [independent DeepSWE leaderboard](https://deepswe.datacurve.ai/) also
reports overlapping uncertainty ranges for Astra and GPT-5.6 Sol. Gains vary
by workload; benchmark results do not prove that every route is faster,
cheaper, or better on a particular repository.

The [GPT-6 Sol/Luna announcement](https://openai.com/index/introducing-gpt-6-sol-and-luna/)
lists standard API input/output prices per million tokens of $2/$10 for Sol
and $0.10/$0.50 for Luna, compared with $4/$20 and $0.20/$1.20 for their 5.6
predecessors. Token prices alone do not determine cost per completed task
or subscription credit usage.

The [GPT-6.1 Sol announcement](https://openai.com/index/introducing-gpt-6-1-sol/)
reports a 6.4 percentage-point DeepSWE improvement over GPT-6 Sol's best
score and an Astra-matching score at roughly one-fifth the cost. GPT-6.1 Sol
was absent from the implementation session's subagent model list, so 0.5.0
uses `gpt-6-sol`. Do not combine absolute scores from different announcements
to reconstruct a GPT-6.1 result. A future 6.1 profile needs verified host
availability, updated contracts, and representative task comparisons.

The regression suite checks routing and evidence integrity; it does not
measure GPT quality. No live inference benchmark or end-to-end SDD quality
certification was performed for this release. Before relying on the new
efforts for a workload, compare fixed representative tasks for completion,
correctness, review findings, latency, and total cost including retries.

## Upgrade from 0.4.0

This is a breaking, forward-only policy update. JSON field layouts and their
schema identifiers remain unchanged; allowed model/effort pairs and selected
route reasons change. A schema identifier alone is therefore insufficient to
identify the policy: retain the package version or Git commit with records.

1. Finish or archive old runs with their original 0.4.0 package. Keep their
   sealed evidence unchanged and retain that package for historical checks.
2. Install the complete 0.5.0 skill, including scripts, schemas, and references.
3. Run the host dependency preflight in a fresh conversation. The required
   Superpowers SDD skill must be present and readable.
4. Create fresh plans, task ledgers, controller occurrences, preflight evidence,
   safe-lane manifests, and dispatch bundles. Compile them under 0.5.0.

Changing model strings inside old evidence is not migration. Old GPT-5.6
occurrences and bundles cannot authorize current dispatch. Historical QFR
validation remains documentary and does not authorize either CMR profile.

## Local defaults

An optional default for routine work is:

```toml
model = "gpt-6-luna"
model_reasoning_effort = "medium"

[agents]
default_subagent_model = "gpt-6-sol"
default_subagent_reasoning_effort = "medium"
```

Merge only these entries into existing user configuration. Preserve unrelated
settings and existing agent roles. These defaults do not override the CMR
compiler: each dispatched occurrence uses its explicit compiled model and
effort. Selecting a model in AGENTS.md prose does not change a running model.
