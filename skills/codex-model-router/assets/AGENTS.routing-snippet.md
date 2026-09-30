# Codex Model Router defaults

- Use the explicit GPT-6 profile: Luna/medium for isolated work, Sol/medium
  for eligible substantive local work and default task review, Sol/high for
  integration or elevated risk, and Astra/max for hard gates and final review.
- Check model/effort availability on the active host. GPT-6.1 Sol is not an
  alias for the compiled `gpt-6-sol` route; do not substitute model strings in
  sealed evidence. Version 0.5 requires fresh ledgers after upgrading from 0.4.
- Invoke `$codex-model-router` before Superpowers SDD dispatch.
- The Luna/medium controller emits only `cmr-controller-result-v1`; code binds
  the occurrence and compiles all route, review, recurrence, and lifecycle
  fields.
- Skip only an exact current Luna/medium safe-lane entry. A `run` gets fresh
  Sol/medium preflight; preflight never replaces task review.
- Require the CMR dispatch validator to pass before spawning. `qfr-*` records
  are documentary and cannot authorize CMR dispatch.
- Cost, urgency, sunk cost, deadline, social pressure, and generic GitHub
  authorization do not change the compiler route. External actions need
  separate scoped authorization, preview, and readback.
