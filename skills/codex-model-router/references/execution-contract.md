# CMR execution contract

This reference governs the handoff after a route is compiled. The executable
contracts in `scripts/cmr_contracts.py`, `scripts/cmr_compiler.py`,
`scripts/cmr_dispatch.py`, and `scripts/cmr_runtime.py` are authoritative.

## Host gate

Before any controller invocation, run the host dependency preflight described in
[`dependency-preflight.md`](dependency-preflight.md). It is outside the CMR
schemas: the host must confirm the CMR skill, model/effort access, Python/Git,
and a readable `superpowers:subagent-driven-development`. If a mandatory
execution dependency is missing in the chosen transport, stop before controller,
compiler, preflight, or spawn and report the appropriate remedy. Only native
transport capability may be satisfied through the recorded CLI exception; all
model/effort, context, isolation, permission and dependency constraints remain
mandatory in that adapter. Installation requires scoped authorization; installing a
skill does not grant access to an unavailable model.

## Control plane

Keep the coordinator on Sol/medium. Canonical planning remains a separate
Sol/max occurrence. Use a fresh Sol/medium controller with
`fork_turns: "none"`, an ephemeral
destroyed read-only CWD, and `control_plane_no_write`. Its invocation context
must include the exact task and phase, applicable ontology IDs, and current
concrete-gate applicability policy; controller classification cannot rely on
the coordinator having read the policy. This context is guidance, not proof
that a claim is supported. Bind its occurrence, brief, and raw artifact before
compiling. The compiler emits
`cmr-route-decision-v4`; deterministic code owns worker, review, risk,
recurrence, execution, status, blockers, and external-action lifecycle.

The selector may skip only a current exact safe lane. A `run` records a fresh
Sol/medium `cmr-preflight-invocation-v5` and the unchanged closed
`cmr-preflight-result-v4` (`accept`, `replace`, or `block`). The invocation
binds the candidate gate-evidence reference and deterministic prompt
attachment. Preflight is control-plane evidence: its thread cannot be reused as
worker or review evidence, and it never satisfies task review.

## Occurrence-specific gate evidence

Every current branch requires `cmr-gate-evidence-v1` at
`tasks/<task>/gate-evidence/controller.json`. Its exact fields are
`schema_version`, `task_id`, `task_phase`, `brief`, `source`, and `claims`;
`source` binds the phase, raw audited artifact, thread, and semantic digest.
Claims are ordered by fact ID and cover every asserted ontology fact that is a
hard gate or has an integration/elevated-risk routing class. Each claim records
`fact_id`, `basis`, `start_byte`, `end_byte`, `quote`, and `explanation`. Basis
is `requested_change`, `concrete_decision`, or `critical_assurance`; offsets
are non-boolean integers for a nonempty UTF-8 byte slice of the exact brief.
The explanation must be nonempty. The semantic digest hashes canonical JSON
containing exactly `fact_ids`, `uncertainty_ids`, and `action_intents` from the
bound source result. An explicit empty claims list is valid only when there are
no required gate facts, but the sidecar is still required.

The v5 preflight invocation binds this candidate reference. Its frozen prompt
contains the exact sidecar bytes. For `replace`, provide a replacement sidecar
at `tasks/<task>/gate-evidence/preflight.json` bound to the exact audited
preflight result and covering every final gate fact, including preserved facts.
This sidecar is required even when the final claim list is empty. For all other
outcomes, the replacement evidence reference is null.

Structural validation verifies task/phase/source identity, semantic digest,
brief path/digest/size, exact coverage, and quote-byte equality. It cannot prove
that the brief text semantically requests a security change or assures a
critical property. A context inventory, ontology wording, file/module label,
negation, preserved constraint, ordinary build choice, or test presence is not
enough by itself. Missing, contradictory, context-only, or invalid evidence
blocks; it never deletes facts or silently down-routes. Fresh Sol/medium
preflight judges relevance against the full brief and phase. If scope or
classification must change, use a fresh brief and classification and preserve
sealed history and monotonicity.

For a `replace` outcome with any final gate claims, including only preserved
claims, require exactly one independent confirmation before dispatch. The
confirmation is a fresh Sol/medium preflight-phase occurrence with a fresh
read-only control context, `fork_turns: "none"`, and a thread distinct from the
controller and proposing preflight. Its closed
`cmr-gate-confirmation-result-v1` contains only `schema_version` and
`outcome: "accept" | "block"`. The model checks all final claims against the
full frozen task, phase, both classifications, both gate sidecars, and final
decision. The `cmr-gate-confirmation-v1` artifact has exactly
`schema_version`, `packet`, `prompt`, `raw`, `transcript`, and `occurrence`;
the final four values are artifact references. Its closed packet contains
exactly `task_id`, `task_phase`, `brief`, `runtime_input`, `candidate_evidence`,
`candidate_gate_evidence`, `replacement_evidence`,
`replacement_gate_evidence`, `final_decision`, and
`proposing_preflight_invocation`. Every reference is bound to the bundle and
audited preflight. Rebuild the deterministic prompt from fixed instructions,
the canonical packet, and the exact UTF-8 contents of its brief, runtime input,
candidate evidence and gate sidecar, replacement evidence and gate sidecar, and
final decision references; compare every prompt byte before accepting the
occurrence. Store the artifact at
`tasks/<task>/gate-confirmation/evidence.json`. It cannot add facts, propose a
route, or request reclassification. A block, missing result, or invalid binding
ends the replacement; correction requires a fresh classified occurrence with
blocked history retained. `skip`, `accept`, and `replace` with no final gate
claims require a null confirmation reference. Task and final review are not
substitutes for this gate.

## Executable API and CLI contracts

The pure gate-evidence API remains in `cmr_gate_evidence`:
`required_gate_ids(fact_ids, ontologies) -> tuple[str, ...]`,
`validate_gate_evidence(value, *, context, brief_bytes, ontologies) -> list[str]`,
`bind_gate_evidence(value, *, context, brief_bytes, ontologies) -> BoundGateEvidence`,
and `expected_gate_evidence_schema(ontologies) -> dict`. Validation is
deterministic and has no filesystem, network, or route-mutation behavior.

The public route compiler requires both the binding and actual brief bytes:

```python
cmr_compiler.compile_route(
    evidence, runtime, ontologies,
    *, gate_evidence: BoundGateEvidence, brief_bytes: bytes,
) -> dict[str, Any]
```

Both keyword arguments are required and have no defaults. Invalid binding raises
`CompilationError` with the existing `artifact_binding_invalid` code after the
existing controller, runtime, and occurrence error checks. The helper
`cmr_compiler.gate_evidence_context(evidence, runtime, *,
source_phase="controller", source_raw=None, source_thread_id=None) ->
GateEvidenceContext` constructs validator context only; it does not authorize
routing. The private alternate-source compiler is used only by evidence-bound
preflight and is not an unchecked route API.

Create the preflight attachment with
`cmr_compiler.build_preflight_gate_attachment(candidate_gate_evidence:
ArtifactRef, payload: bytes) -> bytes`. Append the returned bytes exactly once
to the complete prompt before freezing and auditing it. Bind the result with
`cmr_compiler.bind_preflight_result(result, *, task_id: str, brief: ArtifactRef,
candidate_evidence: ArtifactRef, candidate_gate_evidence: ArtifactRef,
candidate_decision: ArtifactRef, selector: Mapping[str, Any],
prompt: ArtifactRef, raw: ArtifactRef, transcript: ArtifactRef,
occurrence_ref: ArtifactRef, occurrence: OccurrenceAudit,
prompt_bytes: bytes, candidate_gate_evidence_bytes: bytes) ->
BoundPreflightResult`. Every keyword is required; the gate reference, full
prompt bytes, and exact sidecar bytes are mandatory. The binder verifies the
exact single attachment. The invocation is `cmr-preflight-invocation-v5`,
adding only `candidate_gate_evidence`; the closed `cmr-preflight-result-v4`
wrapper remains unchanged.

Apply the result only through
`cmr_compiler.apply_preflight(candidate_evidence, runtime, bound_preflight,
ontologies, *, candidate_gate_evidence: BoundGateEvidence,
replacement_gate_evidence: BoundGateEvidence | None, brief_bytes: bytes) ->
PreflightTransition`. All three new keywords are required. `replace` requires a
full-coverage replacement binding sourced from the audited proposing
preflight raw/thread/semantic result; `accept` and `block` require a null
replacement binding. The candidate binding must match the prompt attachment.
Existing monotonicity, recurrence, and final-review precedence remain enforced.

Freeze sidecars at `tasks/<task>/gate-evidence/controller.json` and
`tasks/<task>/gate-evidence/preflight.json` before calling
`cmr_dispatch.materialize_preflight(*, task_id: str, selector: Mapping[str, Any],
candidate_decision: ArtifactRef | None, bound_preflight: BoundPreflightResult | None,
transition: PreflightTransition | None, candidate_evidence: ArtifactRef | None,
runtime_input: ArtifactRef | None, candidate_gate_evidence: ArtifactRef | None,
replacement_gate_evidence: ArtifactRef | None, ledger_root: Path) ->
PreflightArtifacts`. Every parameter is required, including values passed as
null for selector `block` or a non-replacement. `skip` and `run` require
candidate, runtime, and candidate-sidecar references. `replace` requires a
replacement sidecar, including when its claims are empty. The run path reopens
and replays the evidence-bound transition, then seals the gate-evidence
directory after its sidecars exist and before confirmation, with exact
directory membership.

Build the frozen confirmation prompt with
`cmr_dispatch.build_gate_confirmation_prompt(packet: Mapping[str, Any],
ledger_root: Path) -> bytes`. Its packet/source bindings are checked before it
returns. The prompt has fixed accept/block instructions, canonical packet JSON,
and exact UTF-8 contents of the packet's brief, runtime, candidate evidence and
sidecar, replacement evidence and sidecar, and final decision. Freeze and audit
those exact bytes for a fresh independent Sol/medium occurrence.

Bind its result with
`cmr_dispatch.bind_gate_confirmation_result(result: Mapping[str, Any], *,
packet: Mapping[str, Any], prompt: ArtifactRef, raw: ArtifactRef,
transcript: ArtifactRef, occurrence_ref: ArtifactRef,
occurrence: OccurrenceAudit, ledger_root: Path) -> BoundGateConfirmation`.
The required result is closed `cmr-gate-confirmation-result-v1`; it contains
only `schema_version` and `outcome: "accept" | "block"`. Validate that shape
with `validate_gate_confirmation_result(value) -> list[str]`. Binding reopens
and re-audits source refs and raw JSONL and verifies the deterministic prompt,
result, occurrence, and packet. The occurrence must be fresh Sol/medium,
phase `preflight`, task-scoped, `fork_turns: none`,
`control_plane_no_write`, have no tool events, and use a thread different from
both the controller and proposing preflight.

`cmr_dispatch.materialize_gate_confirmation(bound_confirmation:
BoundGateConfirmation, *, ledger_root: Path) -> ArtifactRef` writes once and
seals `tasks/<task>/gate-confirmation/evidence.json`; the directory must
contain exactly that file. A block result may be retained as sealed evidence,
but cannot authorize dispatch, and no second result can overwrite the same
confirmation. `cmr_dispatch.validate_gate_confirmation(reference: ArtifactRef,
*, expected_packet: Mapping[str, Any], ledger_root: Path) -> list[str]` requires
the exact expected packet and an accepted outcome. A correction requires a
fresh classified occurrence with the prior history preserved.

## Artifacts and dispatch

All ledger references are relative, normalized POSIX paths with lowercase
SHA-256 and exact byte sizes. Freeze the brief, controller evidence, gate
sidecars, preflight evidence, any required confirmation, final route, and v5
dispatch bundle before spawn. Validate
the bundle from the absolute skill directory and absolute ledger root; require
the real CMR dispatcher to return no errors. A schema beginning `qfr-` returns
`legacy_dispatch_forbidden`, regardless of any documentary validation result.

The closed `cmr-dispatch-bundle-v5` includes mandatory
`candidate_gate_evidence`, plus `replacement_gate_evidence` and
`replacement_gate_confirmation`. The candidate reference is required for every
branch. The replacement evidence reference is non-null exactly when preflight
outcome is `replace`; the confirmation reference is non-null exactly when that
replacement has one or more final required gate claims. Historical bundle-v4
and invocation-v4 schemas remain unchanged, but do not authorize current
dispatch. Inspect historical CMR v4 through the retained 0.9.0 source, schemas,
and package; there is no implicit format fallback.

The current bundle API is:

```python
cmr_dispatch.materialize_dispatch_bundle(
    *, task_id: str, brief: ArtifactRef, controller_evidence: ArtifactRef,
    candidate_gate_evidence: ArtifactRef,
    replacement_gate_evidence: ArtifactRef | None,
    replacement_gate_confirmation: ArtifactRef | None,
    runtime_input: ArtifactRef, candidate_decision: ArtifactRef,
    preflight_evidence: ArtifactRef, final_decision: ArtifactRef,
    ledger_root: Path,
) -> ArtifactRef
```

All three gate-reference arguments are required values, including explicit null
values on branches where replacement or confirmation evidence is forbidden.
`validate_dispatch_bundle(bundle_path: Path, ledger_root: Path) -> list[str]`
reopens and verifies every binding. `dispatch_request(bundle_path: Path,
ledger_root: Path)` validates the same bundle before constructing request-v1;
the request retains its native model, effort, and `fork_turns: none`, with all
new references transitively bound through the bundle digest.

The supported compile CLI requires both explicit sidecar and brief inputs for
combined and separate-runtime forms:

```text
python -B scripts/codex_model_router.py compile INPUT [RUNTIME] --gate-evidence SIDECAR_PATH --brief BRIEF_PATH
```

`INPUT`, optional `RUNTIME`, `SIDECAR_PATH`, and `BRIEF_PATH` are read through
frozen no-follow checks: symlink files and inputs with write-permission bits
are rejected. `BRIEF_PATH` must resolve to the exact `evidence.brief.path`; its
implied ledger root anchors hardened descriptor/readback and digest/size checks,
and the sidecar must be under that root. Missing flags or invalid frozen input
files produce one JSON error result, exit 1, and return no route decision. Other
CLI commands retain their existing input semantics. Current
`validate-dispatch BUNDLE [LEDGER_ROOT]` and `dispatch-request BUNDLE
LEDGER_ROOT` accept only bundle-v5.

## Execution accounting

The v4 `check-call-shape` and accounting summary cover core calls only. The
current read-only facade is `scripts/cmr_confirmation_accounting.py`:

```python
validate_execution_call_shape(occurrences, task_states, *, plan_sha256,
    release_review, task_preflight_evidence, ledger_root) -> list[str]
summarize_execution_accounting(occurrences, task_states, semantic_results,
    instrument_errors, *, plan_sha256, release_review,
    task_preflight_evidence, ledger_root) -> dict
validate_execution_accounting_input(value) -> list[str]
expected_execution_accounting_schemas() -> dict[str, dict]
```

Every listed keyword argument is required. `occurrences` is the full immutable
host inventory, including failed and blocked attempts. Existing task-state,
plan, release-review, semantic-result, and instrumentation inputs retain their
v4 meanings. `task_preflight_evidence` maps **every** task-state ID to exactly
`{"preflight_evidence": ArtifactRef, "runtime_input": ArtifactRef | None}`;
runtime input is null only for selector pre-call `block`. Since the sealed
preflight evidence does not carry a runtime reference, the caller supplies it
for replay binding rather than passing a free-form confirmation flag or
caller-created packet.

The closed input shape is `cmr-execution-accounting-input-v1`, defined in
[`cmr-execution-accounting-input-v1.schema.json`](schemas/cmr-execution-accounting-input-v1.schema.json).
Its top-level fields are exactly `schema_version`, `occurrences` (v4 audit
records), `task_states` (closed v4 states), `plan_sha256`, `release_review`
(closed v4 input or null), `semantic_results` (boolean map or null),
`instrument_errors` (unique nonempty strings), and `task_preflight_evidence`
(the map above). Source files must be frozen; canonical preflight, gate, and
confirmation directories must pass the dispatcher's sealed no-follow/readback
validation.

Confirmation scopes derive from valid canonical sealed replaced-preflight
evidence whose final decision has required gate claims. The facade rebuilds
the exact packet and deterministic prompt from bound evidence and runtime
references. Scope identity is
`(gate_confirmation, task_id, sha256(canonical_packet_bytes))`; occurrences
partition by exact task, phase, scope, and prompt identity. The facade verifies
proposer-invocation and available final confirmation bindings.
Missing confirmation artifacts never erase observed attempts. Duplicate raw
call identities count once and raise an accounting error; unique unmatched
calls remain visible, and cross-task, stale, ambiguous, or reused identities
fail accounting. Complete inventory capture remains the caller's obligation.

The closed report is `cmr-execution-accounting-v1`, defined in
[`cmr-execution-accounting-v1.schema.json`](schemas/cmr-execution-accounting-v1.schema.json).
Its top-level fields are exactly `schema_version`, `core_v4`,
`confirmation_scopes`, `confirmation_count`, `confirmation_extra_attempts`,
`confirmation_usage`, `confirmation_known_usage`, `unmatched_occurrences`,
`observed_record_count`, `total_calls`, `total_usage`, `known_usage`,
`usage_complete`, `canonical_call_shape`, and `accounting_errors`. `core_v4`
is the unchanged summary derived only from core calls. `observed_record_count`
includes duplicate supplied rows;
`total_calls` counts unique immutable raw-call identities. `known_usage` sums
valid usage exactly once. `total_usage`, `confirmation_usage`, and an affected
scope's `usage` are null if any contributing occurrence has missing or invalid
usage; `usage_complete` becomes false and an accounting error exposes the
unknown. Never report unknown usage as zero. Confirmation extra attempts stay
separate; only `core_v4.transport_retry_count` is available, with no global
retry count. A valid `block` is one bound confirmation call and its usage, not
a transport retry. Exact scope/attempt fields and the outcome enum are defined
in the report schema. Global `canonical_call_shape` requires a valid core shape,
exact confirmation cardinality, and no partition or instrumentation errors; a
bound block can have canonical shape while dispatch remains denied.

Use the current CLI for complete accounting:

```text
python -B scripts/codex_model_router.py check-execution-call-shape INPUT LEDGER_ROOT
```

For structurally valid input it emits one JSON object with exactly
`command`, `valid`, `errors`, and `report`; `report` equals the Python summary.
It exits 0 only when there are no accounting errors and canonical call shape,
otherwise 1. Malformed input follows the one-object JSON error contract and
omits `report`. Runtime and occurrence v4 contracts and sealed records remain
unchanged. Accounting is read-only and cannot authorize dispatch; a valid
confirmation does not excuse missing or invalid accounting evidence.

Binding failures keep existing errors, including `artifact_binding_invalid`,
`occurrence_invalid`, `preflight_result_invalid`, `preflight_non_monotonic`,
and `dispatch_bundle_invalid`. Missing confirmation reports
`gated replacement requires one independent gate confirmation`; a block result
reports `gate confirmation blocked worker dispatch`; prompt mismatch reports
`gate confirmation prompt does not equal the deterministic frozen packet`.
Reuse of the controller or proposing-preflight thread is explicitly denied.
Validators return deterministic error lists; dispatch materializers raise
`DispatchError`.

Do not resolve the validator from the caller's CWD. Do not rewrite or reopen a
sealed artifact, reuse a plan ledger for a new canonical plan, or accept a
pointer whose bytes, path, identity, or digest drifted.

## SDD handoff and reviews

Call `cmr_dispatch.dispatch_request(bundle_path, ledger_root)` or the CLI
`python3 <absolute-skill>/scripts/codex_model_router.py dispatch-request
<absolute-bundle> <absolute-ledger>` before handoff. The adapter revalidates
the full current bundle and reads the frozen, hash-bound UTF-8 brief. Its
`request` contains exactly `task_name`, `message`, `model`, `reasoning_effort`,
and `fork_turns` for a native spawn; `scope` binds the task, brief, bundle,
final decision, and prompt SHA-256. The message is the literal brief, so the
prompt digest matches the bytes actually sent. Pass all request fields to the
host through native collaboration when its capabilities satisfy the occurrence.
The execution-transport policy in SKILL.md is mandatory. A Codex exec adapter
is allowed only for a concrete, recorded native capability gap, scoped to that
occurrence and disclosed to the user before execution, including the loss of
main-panel visibility. It must map model and effort to real host parameters
and use a fresh invocation with the literal prompt. Do not silently switch
transport when native execution is available. This is an execution request,
not evidence that a worker ran.

An eligible clear low-risk implementation unit must run as Luna/xhigh. Dense
bounded local logic with approved architecture/interfaces, low blast radius,
and no uncertainty or higher gates runs as Luna/max; use the exact predicates
in `routing-policy.md`. Both efforts require independent Sol/medium task review.
The Sol
coordinator may prepare its scope, review its diff, and run verification, but
must delegate generation of that implementation. Classify each unit on its
own dependencies and risk; unrelated risky work does not change its route.
If the host cannot run the exact pair, stop that dispatch and report the
capability blocker without silently coding it in Sol.

Only a valid `cmr-dispatch-bundle-v5` can hand off to
`superpowers:subagent-driven-development`. The implementer gets a fresh brief,
exact scope, base/head binding, `fork_turns: "none"`, and no authority to
create subagents or external writes. A fresh phase-correct task reviewer reads
the same brief, report, and diff in a read-only worktree. Fix rounds follow the
CMR escalation matrix. The final whole-branch reviewer is always fresh
gpt-6.1-sol/max.

Retain actual host model/effort metadata, worker transcript, generated diff,
and verification results to establish that Luna produced the code. A route,
request JSON, or synthetic test occurrence cannot establish live execution.

For native execution, retain the native agent identity and parent association.
Host Active/Done registration is execution evidence; visible panel rendering
requires UI observation or explicit user confirmation. If either is unavailable,
report its absence instead of treating a CLI transcript as native visibility.

## External effects

External actions are per-item state machines. `blocked` is
`authorized=false, preview=false, readback=false`; `ready` is
`true, true, false`; `completed` is `true, true, true`. A ready item needs a
non-empty resolved target; completed requires readback of that same target.
Authorization for one Issue, push, PR, merge, deploy, or skill installation
does not authorize another. Analysis, preparation, or preview never implies a
mutation.
