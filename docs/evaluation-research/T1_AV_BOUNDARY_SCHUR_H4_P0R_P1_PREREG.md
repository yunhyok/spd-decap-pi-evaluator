# SPD Decap PI Evaluator v0.22.0 - AV-BS1 H4-P0R-P1 executable-contract candidate

## 1. Current status and authority

This document now preregisters the retry-v7 corrective static
executable-contract candidate after seven public invocations. The first four
stopped before claim and factor work; the fifth reached the factor child but
left factor entry indeterminate; the sixth created claim and factor child but
failed before `_factor_one`/`splu`; the seventh reached `_factor_one`, and native
`splu` returned for `A_background_II` before the certificate path rejected an
invalid native/exported nnz equality assumption. It remains token-absent and
does not itself authorize a pilot. None of the seven prior tokens may be reused.

The current safe manifest classification is:

- `token_state=absent`;
- `status=candidate_token_missing_no_factor`;
- `authorization_state=not_authorized`;
- `terminal_evidence_complete=false` for the current token-absent candidate;
  the seventh attempt's complete outer terminal seal remains immutable
  historical failure evidence;
- `authoritative_stage_pass=false`;
- the seventh child records attempted/performed `true/true` and
  `completed_factors=["A_background_II"]`, meaning that native `splu` returned;
  no factor certificate or prefix was completed, and `A_conductor_II` was not
  attempted;
- exact native/exported factor nnz counts were not persisted, and no RHS,
  solve, H4 physics, or PowerSI work has run; and
- no later H4-P1 stage is authorized.

The retry-v7 candidate retains execution resource scope v2,
`control_plane.independently_bounded=true`,
`tree_thresholds_equal_factor_envelope=true`,
`system_floor_recheck_before_and_after_each=true`, and
`implementation_status=implemented_and_static_audited` for both the bounded
control-plane supervisor and outer observer, with the readiness-specific
`authorization_blocker=false`. Exactly six explicitly instrumented
outer-observer sampling contexts plus six explicitly instrumented control-plane
sampling contexts (12 total), plus the four active factor sampling calls, opt
into `MaximumAttempts=3`; the caught-final factor cleanup call and function
default remain `1`. Outer/control retry evidence has bounded cap `64`. Retry-v7
does not alter any of those retry semantics, schemas, or factor order. It only
replaces the invalid native/exported equality with the fail-closed requirement
`0 < exported <= native`, checks distinct native/exported portable-byte
formulas, and includes both in the existing cap. These facts make the exact
candidate eligible for later token review; they do not authorize a pilot while
the token is absent and do not change any terminal or next-stage blocker.

## 2. Frozen program identity and ancestry

The visible program identity is **SPD Decap PI Evaluator v0.22.0**. The case is
`AV-BS1-CIRCLE-PRIMARY`, and the prospective public stage is
`primary-h4-p0r`.

The immutable committed H4-P0R parent manifest payload SHA-256 is:

`eaab10df7fb1557490cc75db7e7ff9fca2881013b6a6fb13f02faea43ddf7023`

The P1 candidate must remain a descendant of that parent contract. It may
correct executable resource-scope wording, add the one-use lifecycle, and add
terminal evidence, but it may not change the frozen matrix inputs, factor
order, numerical ceilings, or forbidden-operation boundary.

## 3. Candidate artifact bindings

| Artifact | SHA-256 |
| --- | --- |
| [Python fixture](../../tools/research/av_bs1_boundary_schur_h4_p0r_p1.py) | `95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234` |
| [PowerShell runner](../../tools/research/run_av_bs1_h4_p0r_p1_stage.ps1) | `cee65497b414a5c9da2b572dc2f026a496b304889c7ddf86a0a2856ebb842d9c` |
| [Static P1 tests](../../tests/test_research_av_bs1_boundary_schur_h4_p0r_p1.py) | `0f118612aefa9dc80526abf6604a2234f14c50454000f76ef172a534398042fd` |

The final SHA-256 of this document and the live P1 wrapper manifest payload are
intentionally not embedded here. The fixture binds
`preregistration_doc_sha256`, so editing this document changes both values.
They must be computed after these bytes are frozen, independently audited, and
then bound by the later clean contract commit and token. Any earlier wrapper
payload is a non-normative pre-document-update diagnostic.

## 4. Factor-only scope and forbidden operations

The only prospective primary work is construction, equilibration, and factor
certification for the two frozen interior matrices. The candidate generates
no RHS and calls no factor solve. It generates no harmonic extension, boundary
operator, Schur/DtN/Y matrix, modal response, PDE residual, power result,
analytic verdict, convergence verdict, H4 physics result, product change,
PowerSI claim, or 8 GiB claim.

Every result and tombstone keeps `physics_solve_performed=false` and
`next_stage_authorized=false`. The terminal seal also keeps
`next_stage_authorized=false` and cannot authorize physics. Even an
authoritative factor-only pass grants no H4-P1 authorization.

## 5. Exact numerical sequence

The factor sequence is fixed:

1. Reconstruct and manually row/column equilibrate `A_background_II`.
2. Verify its sparse input and call SuperLU `splu` with `COLAMD`,
   `diag_pivot_thresh=1.0`, and `Equil=False`.
3. Finish the first certificate, release native and exported factor state plus
   the matrix, and run `gc.collect()` inside the factor routine.
4. After that cleanup returns, durably publish the first certificate prefix;
   only then reconstruct, equilibrate, verify, and factor
   `A_conductor_II` with the same fixed options.
5. Finish and clean the second factor, durably publish its prefix, and prove
   fixed order and non-overlap.

Each certificate binds the raw and equilibrated sparse inputs, row and column
scales, canonical CSC L/U storage, integer index storage, permutations,
finite/nonzero U diagonal, fill ratio, native and portable factor bytes,
monotonic factor and cleanup intervals, and the one-factor hard cap. The inner
success status is
`passed_AV_BS_h4_p0r_factor_only_pending_h4_p1_preregistration`; it remains
provisional until a valid outer terminal seal exists.

## 6. Three distinct resource scopes

The candidate has three nested but non-interchangeable resource scopes.

### 6.1 Outer lifecycle envelope

The public PowerShell dispatcher starts its `Stopwatch` before candidate-token
hashing, observer-session setup, and inner-runner spawn. The measured tree root
is the outer observer PID plus birth identity. Successful samples cover the
outer observer and the identity-bound sampled inner tree. The interval ends
after retained-inner actual exit, owned-tree cleanup, and final pre-seal system
samples, but before materializing the outer envelope close.

The disclosed excluded head is public PowerShell startup and parsing, native
monitor/function initialization, and immutable runner/argument discovery
before the dispatcher stopwatch. The disclosed excluded tail is post-cleanup
token/claim classification, outer-envelope-close materialization and readback,
terminal-seal source rechecks and materialization, returned-text packaging,
and outer-process exit. The terminal seal binds both the raw and canonical
hashes of the outer resource-envelope close.

### 6.2 Nested factor-fit envelope

The factor-fit wall interval begins before exclusive claim and guard
materialization and ends after verified primary-tree exit or cleanup, a
terminal system sample, and the single stopped `Stopwatch` value. Simultaneous
100 ms Toolhelp32/PSAPI samples cover the outer observer, inner runner, and
identity-bound factor child/tree processes present at each successful sample.

The `monitor-ready.json`, `factor-complete.json`, and `monitor-release.json`
handshake brackets the actual factor interval with real samples. OS lifetime
working-set and commit peaks for observed outer/inner identities are retained
as conservative lifecycle-contaminated stop gates; they are not represented
as factor-only simultaneous peaks.

### 6.3 Per-invocation control-plane envelopes

Preflight, canonical-JSON helpers, finalizer, and token consumer are excluded
from factor-interval simultaneous measurements. Each candidate control-plane
invocation instead has its own identity-bound supervisor envelope: a 180 s
wall stop, 16 MiB stop for each output stream, the factor-envelope tree
thresholds, high system-floor checks before and after the invocation, retained
process handles, actual exit-code comparison, and verified zero-survivor
cleanup.

Each successful invocation is bound by a process report, a pre-close session
index, an envelope close, and a final session index. Its report explicitly
states `factor_fit_interval_included=false`. Outer and inner OS lifetime stop
fields can conservatively include earlier control-plane history. The frozen
implementation and independent static audit support the live
`implemented_and_static_audited` readiness state in Section 1. This is an
implementation-readiness statement, not a claim that an authorized pilot has
already run.

Retry-v3 versions the report and close as
`AV-BS1-h4-p0r-control-plane-process-report-v2` and
`AV-BS1-h4-p0r-control-plane-envelope-close-v2`; execution resource scope is
`AV-BS1-h4-p0r-execution-resource-scope-v2`. Both report and close carry exactly
`tree_sample_max_attempts`, `tree_sample_confirmed_disappearance_count`,
`tree_sample_retry_events`, `tree_sample_retry_events_truncated`, and
`monitor_failure` as the new control retry fields.

## 7. Resource ceilings and failure classification

The inherited factor and outer ceilings are 900 s wall time, 4 GiB sampled
tree working set, 5 GiB private commit, 5 GiB committed pagefile, a 2 GiB
system commit-headroom floor, a 1.5 GiB available-physical floor, and a 2 GiB
one-factor hard cap. The frozen higher pre-spawn floors are 5,618,345,703 bytes
of commit headroom and 5,081,474,791 bytes of available physical memory.

One exact runner function classifies both outer emergency tombstones and the
public no-seal failure prefix. These 17 stop reasons map to
`BLOCKED_AV_BS_RESOURCE`:

- `OUTER_PRESPAWN_COMMIT_HEADROOM_STOP`
- `OUTER_PRESPAWN_AVAILABLE_PHYSICAL_STOP`
- `OUTER_TREE_WS_STOP`
- `OUTER_TREE_LIFETIME_PEAK_WS_STOP`
- `OUTER_TREE_PRIVATE_STOP`
- `OUTER_TREE_COMMIT_STOP`
- `OUTER_TREE_LIFETIME_PEAK_COMMIT_STOP`
- `OUTER_SYSTEM_COMMIT_HEADROOM_STOP`
- `OUTER_AVAILABLE_PHYSICAL_STOP`
- `OUTER_WALL_TIME_STOP`
- `OUTER_STDOUT_SIZE_STOP`
- `OUTER_STDERR_SIZE_STOP`
- `OUTER_INNER_CLEANUP_FAILED`
- `OUTER_POSTEXIT_COMMIT_HEADROOM_STOP`
- `OUTER_POSTEXIT_AVAILABLE_PHYSICAL_STOP`
- `OUTER_TERMINAL_SAMPLE_FAILED`
- `OUTER_RESOURCE_EXCEPTION`

Unknown states and handshake, schema, exit, token, or classification drift map
to `BLOCKED_AV_BS_RESULT_SCHEMA`. The complete failure-code allowlist is
`BLOCKED_AV_BS_FACTOR`, `BLOCKED_AV_BS_MESH_HASH`,
`BLOCKED_AV_BS_RESOURCE`, and `BLOCKED_AV_BS_RESULT_SCHEMA`.

## 8. Process identity and evidence limits

Every owned process is tracked by PID and birth time. PID reuse, an identity
predating its parent, missing retained handles, missing required samples,
monitor failure, cleanup failure, or any observed survivor fails closed. A
terminal sample is accepted only after the relevant retained root has actually
exited, or after a verified no-spawn outcome, and the observed owned tree has
zero survivors.

### 8.1 Historical retry-v2 outer-observer sampler

The envelopes use sampled Toolhelp32/PSAPI membership, not a Windows Job
Object. Descendants created and exited entirely between polls are not claimed.
Each tree sample is nevertheless transactional and bounded. It has at most
three total attempts and returns only one complete attempt. If a non-root PID
vanishes between a complete Toolhelp snapshot and its metric probe, the runner
restarts the entire sample only after the native probe reports a retained
handle exit or exact not-found state, a fresh complete Toolhelp snapshot proves
the PID absent, and the root PID/birth identity remains unchanged before and
after confirmation. Every partial numeric accumulator and returned membership
set is discarded; already identity-bound PIDs remain retained for cleanup and
evidence.

Root disappearance or reuse, incomplete Toolhelp enumeration, a live
same-birth process with a failed counter query, access denial or any other
native error, target reappearance, malformed metrics, and retry exhaustion are
fatal. The outer-close-v2 ABI records the maximum-attempt count, a bounded list
of fixed-schema confirmed-disappearance events, a truncation flag, and nullable
typed `monitor_failure`. Those objects contain allowlisted operation/context
codes, PID/birth/error integers, and fixed ASCII message codes; they never
serialize localized exception text or paths. A successful outer resource gate
requires no monitor failure and no truncated retry evidence.

An exception during the initial pre-token outer sample can still precede
observer-session directory creation, so it cannot create an outer close. That
early boundary remains fail-closed through the public nonzero exit, unchanged
token bytes, and absence of session/claim/factor evidence; it is not represented
as a durable sampled close.
The evidence is therefore a precise sampled contract, not a race-free proof of
every process that ever existed.

### 8.2 Retained retry-v3 outer and control sampling contract

`Get-TreeSample` retains its default `MaximumAttempts=1`, and every factor call
site remains unchanged at that default. Explicit `MaximumAttempts=3` and retry
event limit `16` apply only to the already instrumented outer-observer contexts
and these six control contexts:

1. `control_pre_helper_tree_sample`;
2. `control_active_outer_tree_sample`;
3. `control_active_cleanup_root_tree_sample`;
4. `control_post_completion_outer_tree_sample`;
5. `control_post_completion_cleanup_root_tree_sample`; and
6. `control_envelope_close_tree_sample`.

Control typed events use the same fixed schema and arrival order as outer
events. The process-report event list is the exact prefix frozen before
envelope-close sampling; the close list is final. Confirmed count is count-all,
while the stored list is bounded to 16 and exposes truncation explicitly. The
first fatal `monitor_failure` is sticky even if later cleanup or close sampling
succeeds. A passing control gate requires null failure, no truncation,
confirmed count equal to emitted events, and no attempt-three exhaustion.

Every close-only event that carries a non-null expected or observed birth must
match the frozen report PID→birth map. It must identify the envelope-close
context, a PID, at least one non-null birth, and matching non-null birth values;
null/null evidence cannot confer pass. Incomplete or truncated evidence fails
with `CONTROL_TREE_SAMPLE_RETRY_EVIDENCE_INCOMPLETE`. An uncovered close-only
identity fails with `CONTROL_ENVELOPE_CLOSE_RETRY_IDENTITY_UNCOVERED`. Both use
the typed `control_provenance_exception` fallback and cannot overwrite an
earlier sticky fatal. Generic non-tree supervisor and cleanup catches use only
`control_supervisor_exception` and `control_cleanup_exception`; structured tree
exceptions retain their exact context and native operation.

Outer and inner sampling share one PID→birth evidence dictionary, so every
successful inner-only observation is serialized in final
`sampled_process_identities` and a cross-context PID/birth conflict fails as
`NONROOT_PID_REUSE` at the exact sample attempt. Their ownership ID sets remain
separate and are the only sets iterated by live-check and termination helpers;
the evidence union therefore cannot expand cleanup ownership. Before any
disappearance confirmation, an `exited` metric probe must carry an actual
positive `Int64` birth equal to the already bound birth. Missing/invalid birth
fails `PROCESS_METRIC_IDENTITY_OR_VALUE_INVALID`; mismatch fails
`NONROOT_PID_REUSE`; neither path retries. After report freeze, close-phase
threshold/system checks assign a stop reason only when none exists, preserving
the first failed disposition together with the sticky first monitor failure.

## 9. One-use token and claim lifecycle

A future token must directly bind the exact clean contract commit, fixture,
runner, tests, final document, inherited parent manifest payload, parent matrix
contract, resource policy, and all three execution scopes. The live P1 wrapper
payload is bound transitively through the validated preflight and claim
evidence. The public runner validates that token before creating an exclusive
same-volume claim with `CreateNew`. An existing claim blocks local replay.

Normal token consumption re-reads the exact original authorized token bytes
immediately before replacement. Any byte drift refuses replacement. Once a
claim is owned, normal consumption or conservative emergency replacement is
permitted only after the relevant retained process root has exited, cleanup is
verified, and the owned-survivor count is zero. Unresolved cleanup leaves the
claim and token replay-blocked and non-authoritative; it does not relax the
mutation gate.

## 10. Result-v2 and provisional tombstone-v2 ABI

The result schema is `AV-BS1-h4-p0r-result-v2`. Normal consumption uses
`AV-BS1-h4-p0r-consumed-review-token-v2`; controlled recovery uses
`AV-BS1-h4-p0r-emergency-consumed-review-token-v2`.

All three carry the same seven pending-terminal fields:

- `outer_observer_contract_sha256`, bound to the frozen observer contract;
- `outer_observer_handshake_prefix_sha256`, bound to the claim prefix;
- `expected_terminal_seal_relative_path`, bound to the token identifier;
- `terminal_seal_required_for_authoritative_disposition=true`;
- `terminal_seal_state=pending_outer_observed_inner_exit`;
- `terminal_evidence_complete=false`; and
- `authoritative_stage_pass=false`.

These values are literal provisional state, not a pass. A strictly valid
normal tombstone without a seal remains
`consumed_v2_provisional_missing_terminal_seal`. A postvalidated emergency
replacement without a seal also remains non-authoritative.

## 11. Outer terminal seal-v2 authority

Only `AV-BS1-h4-p0r-outer-terminal-seal-v2`, written by the outer observer
after retained-inner actual exit, can supersede provisional disposition. It
must contain the strict Boolean
`supersedes_provisional_tombstone_and_any_present_result_disposition=true`.
The result path and hash must be an exact present binding or a strict null
pair; failure paths are allowed to have no result.

`authoritative_stage_pass=true` if and only if all of the following are true:

1. A strict result-v2 provisional pass and strict normal tombstone-v2
   provisional pass agree on all evidence.
2. The inner reported exit is zero, the retained inner actual exit is zero,
   and the two values match.
3. Claim, guard, resource, result, numerical output, stderr, factor prefixes,
   monitor markers, published result, pre-exit evidence, inner complete,
   exit release, final control index, and outer resource close all have exact
   current path/hash bindings.
4. Consumer and outer resource gates pass, cleanup is verified, and all
   required source bytes match both before and after seal materialization.
5. No attempt failure code remains. The terminal
   `authorization_blocker=true` and `next_stage_authorized=false` remain
   intentionally asserted because a factor-only result cannot authorize the
   next stage.

Every other valid seal is an authoritative failure with
`authoritative_stage_pass=false`. The field
`tombstone_success_was_provisional_without_this_seal` is derived solely from
the tombstone's provisional success truth, independent of the observed actual
inner exit code. The seal still keeps `next_stage_authorized=false`.

## 12. Marker bindings and durable quarantine

The three factor-monitor file hashes are carried end to end under the exact
names `monitor_ready_marker_sha256`, `factor_complete_marker_sha256`, and
`monitor_release_marker_sha256`. They are checked in the resource evidence,
pre-exit evidence, inner-complete record, terminal seal, and current-source
rechecks before and after the seal. Presence is an ordered nullable prefix:
none; ready only; ready plus complete; or all three.

When present, the files have the canonical quarantine names
`monitor-ready.json`, `factor-complete.json`, and `monitor-release.json`.
Every claimed attempt with any non-null terminal artifact hash is quarantined,
including a clean provisional pass. Guard, resource, result, numerical stdout,
stderr, factor-prefix, and marker bytes needed for terminal validation are not
deleted before or after the seal.

Raw file hashes remain binding even on a failure whose resource evidence is
invalid. Deep marker JSON and chronology validation is required only when the
resource evidence is itself strictly valid. This preserves malformed failure
evidence without promoting it to pass evidence.

## 13. Controlled one-shot recovery

An inner consumer failure may invoke conservative emergency replacement only
after factor-root exit is observed, factor cleanup is verified, and the owned
survivor count is zero. A missing or invalid consumer wrapper never bypasses
those gates. A same-attempt normal v2 tombstone already on disk remains
provisional and is not overwritten merely because its surrounding control
report failed.

The outer observer may replace an exact original authorized token after a
controlled post-claim failure only when all of these facts still hold:

- the claim is exact and observer-bound;
- the current token bytes and SHA-256 equal the retained original authorized
  bytes;
- retained-inner actual exit is observed;
- outer cleanup is verified;
- the inner process is no longer live; and
- zero owned descendant survivors remain.

The replacement tombstone's nested `bindings` map must exactly equal the
current validated manifest bindings. It therefore contains all eight frozen
keys, including `resource_policy_sha256`; the top-level
`resource_guard_policy_sha256` is independent and cannot substitute for that
nested key. Review-binding recomputation uses the same complete eight-key
manifest map, which the tombstone's own nested map must exactly equal. Missing,
extra, or drifted binding data remains a strict validation failure. The
emergency producer must satisfy this existing contract; the Python validator is
not relaxed.

That branch uses review disposition
`emergency_consumed_after_outer_observer_post_claim_failure` and attempt/effect
status `outer_observer_post_claim_failure`. It does not claim inner factor
phase knowledge: `child_launched`, child PID/exit, and factorization
attempted/performed are null; guard, result, resource, child, and prefix
evidence remain untrusted/null or false.

Both emergency branches use the `File.Replace` backup to validate the
displaced original token bytes and then move those bytes to the canonical
recovery path; the temporary `.bak` is not required to coexist afterward.
The recovery bytes are bound by an intent record followed by a postvalidation
record. The replacement authority is only
`postvalidated_file_replacement_only_not_terminal_lifecycle_evidence`.
Unknown, drifted, or cleanup-unverified state detected before replacement
causes no token mutation and no seal. Interruption after replacement begins
may leave a pending, non-authoritative tombstone plus recovery/journal state;
it never creates a valid seal or terminal authority by itself.

When no claim exists after cleanup, the runner always classifies the canonical
token read-only and never replaces, deletes, recovers, or seals it. Exact
original bytes record their raw SHA-256 and
`no_claim_exact_original_authorized_token_retained_public_attempt_spent_no_recovery_performed`.
Bounded present drift records the current raw SHA-256 and
`no_claim_token_present_but_drifted_no_recovery_no_terminal_seal`. Absence uses
null SHA-256 and `no_claim_token_absent_no_recovery_no_terminal_seal`; an
unbounded present token is also drifted with null SHA-256. Exact classification
requires hash-before/read/hash-after stability plus byte equality.

## 14. Mandatory no-seal and external boundaries

A missing token-consumer control report or envelope-close reference is a valid
controlled emergency condition, but it is not terminal-complete evidence.
Pre-exit and inner-complete state must keep `terminal_evidence_complete=false`,
and the outer observer must withhold both the seal and authoritative
disposition. No report is synthesized and no validator is weakened.

Outer emergency replacement without a later exact inner-completion chain also
withholds the seal. A v2-shaped file that has not passed strict field,
type, binding, raw-byte, and journal validation is described as unverified,
never as matching.

The lifecycle guarantees apply to controlled paths while the responsible
runner/observer is executing. An external runner kill, machine kill, or
adversarial local caller that kills processes or mutates owned evidence is
explicitly not guaranteed to produce a tombstone or terminal seal. Before
exclusive claim creation, authorized token bytes may remain unchanged, but a
public attempt is spent: that token is never reused and must be retired without
mutation or terminal sealing. After a non-adversarial interruption with a
surviving owned claim, local replay remains blocked. No uncontrolled
interruption by itself creates terminal authority, and protection against
self-consistent adversarial rewrites is not claimed.

The exact disclosures remain
`external_runner_or_machine_kill_terminal_state_guaranteed=false` and
`adversarial_local_caller_exclusion_claimed=false`.

## 15. Static evidence for this candidate

### 15.1 Historical retry-v2 evidence and the first two attempts

The static P1 suite passed **187 tests** with an autouse tripwire that raises on
any real `scipy.sparse.linalg.splu` call. It includes direct filesystem tests
for a valid no-seal normal tombstone, both inner and outer emergency semantic
branches with intent/postvalidation/recovery journals, normal pre-replace token
drift, marker raw-binding versus deep-validation gating, cleanup-conditioned
token mutation, marker quarantine, and the exact 17-reason outer resource map.
It also constructs fake filesystem-backed sealed failure-v1 and result-v2 pass
chains through the complete, exit-release, outer-close, terminal-seal, and
manifest terminal-evidence validators, plus strict type, source-hash, marker,
nullable-prefix, and seven-case terminal tamper checks. Those tests call no
primary stage, factor child, or real SuperLU factorization.

The new monitor regressions execute the exact marker-delimited
`Get-TreeSample` function body with internal deterministic providers, without
dot-sourcing or dispatching the runner. They prove whole-attempt accumulator
discard after a confirmed non-root disappearance, fatal live-process metric
failure, fatal root disappearance/reuse, exact three-attempt exhaustion,
rejection when an error-87 PID remains in the fresh snapshot, strict native
Toolhelp completion, and typed close-v2 tamper rejection. The extracted slice
contains no process spawn, token, primary-stage, consumer, or factor operation.
The suite also freezes that an already-exited retained inner is not sampled as
a live cleanup root, while any cleanup-tree sampling failure for a still-live
inner is preserved as typed monitor failure and prevents the mandatory outer
gate even if a later terminal sample succeeds.

The suite also freezes the public-dispatcher strict-mode regression: every
root/descendant enumeration is array-captured before `.Count`, zero/one/many
cardinalities are exercised under `Set-StrictMode -Version Latest`, JSON
failure-code counts are defensively array-wrapped, and both public and inner
top-level catches emit diagnostics non-terminatingly before returning exit 2.

Python compilation passed, and PowerShell AST parsing reported no syntax
errors. Safe Python and PowerShell manifest diagnostics observed token absent,
not authorized, no factorization, no physics solve, no terminal authority, and
no next-stage authorization.

One earlier public-dispatcher invocation from token-only commit
`b6c8615639a8fb283909bd6613ab5bf5b0e09cb2` began at
`2026-08-15T08:49:35Z` and stopped in 0.83 s before token read, observer-session
creation, inner spawn, claim, or factor work. A root-only process enumeration
was pipeline-unrolled to scalar `Int32`; strict-mode `$ids.Count` therefore
raised `The property 'Count' cannot be found on this object`. The untouched
token `92e6edd1bf01428199c5a492e64858ea` retained `uses_remaining=1`, while its
claim, seal, session, output, and live-process sets were all absent. It was not
retried and was retired by deletion-only commit
`412e88049ec1461dd15ae324b583c114fb890393`. The current runner array-captures
that enumeration and preserves the intended fail-closed exit code; no factor or
physics solve occurred in the failed dispatcher attempt.

A second fresh token-only child
`d9e064fee6822d8f39318a55646860e24b502f35` bound contract commit
`2b7e302aa4ef5abedcd22cda0003380cb8765a42`. Its one public invocation created
observer session `e017a79e5ce345959d8e77f46b8d90b2` from
`2026-08-15T09:16:22.8172642Z` through `2026-08-15T09:16:24.0712914Z`.
The only durable session evidence is empty inner stdout/stderr plus
`outer-resource-envelope-close.json`, SHA-256
`3de68a75e4e1fa23876b63f1843ad217f7ba0290f8a86b491bac09124f5ea39c`.
That close recorded `OUTER_RESOURCE_EXCEPTION`, two completed samples, two
inner-visible samples, `inner_actual_exit_code=-1`, verified cleanup, a failed
outer gate, and `no_claim_no_recovery_required`.

No ready/start-release/complete/exit-release marker, control-plane report,
claim, guard, result, prefix, quarantine, journal, tombstone, or terminal seal
exists for that token. The required ready/start-release handshake is earlier
than preflight, claim, and factor-child spawn, so no factorization, RHS, solve,
or physics operation could have started. The exact native operation and PID are
not recoverable because the v1 close did not persist the caught exception.
Timing makes a short-lived inner `Add-Type` compiler/bootstrap descendant
disappearing between enumeration and metrics the high-confidence explanation,
but this is an inference rather than a proved PID/API cause.

The token ID `7a26f8ea4f89493a95fa78bc62b2d431` remained byte-identical with
`uses_remaining=1`; it was not retried and was removed by deletion-only
retirement commit `7dd1db501b505d1a6a36f0d1f99df23fca655a93`. The current checkout is
token-absent. The present whole-sample retry and typed outer-close-v2 evidence
are the corrective contract for that second pre-factor interruption, not a
factor-fit result.

### 15.2 Third public attempt under retry-v2

Retry-v2 contract commit `29aeed318abb1cefb189917d707066987e5ea3b3`
was the sole parent of fresh token-only child
`6328174b8315f71b407f79584f134359b9f48685`. Token ID
`7af97159e9224924832085a293c65c27` was invoked exactly once from
`2026-08-15T10:58:35.7129115Z` through
`2026-08-15T10:58:39.7341700Z`; the public runner returned exit `2`.

The outer session is
`validation-output/av-bs1/outer-observer/session-399b2ac2a1754822bd7da61aae88acaf`
and its close SHA-256 is
`a675b5f829e343717d66c1c1d4d40335aed7015c09ad5c3385d2d90aa97e20cd`.
The control session is
`validation-output/av-bs1/control-plane/session-401d8ddafdbe42628541ebe2bdc367cf`.
Its preflight process report SHA-256 is
`da6069826cf46335bb8b86655abfaad8eb3fb61c87d076ebf916849f998c5f7b`;
its pre-close and final session-index SHA-256 values are
`c693ad457906f54047427e7b9d831d9b1cd764d0457d3557a872c6a5f8fe5018`
and `f4824fba0f58c71b85acd1fc286aa29e0d323ef704e828649119c6e39f78d579`.

Outer inner-ready/start-release and control bootstrap-ready/start-release were
durable before the failure. The preflight report then recorded
`TRANSIENT_DESCENDANT_DISAPPEARANCE_RETRY_EXHAUSTED` and
`CONTROL_PLANE_SUPERVISOR_EXCEPTION`, cleanup verified, and gate false. The
retry-v2 control sampling call sites inherited the function default
`MaximumAttempts=1`; only outer sampling had the explicit three-attempt
diagnostics. Thus a confirmed non-root disappearance exhausted the sole
control attempt. Control target completion, claim creation, and factor-child
spawn occur later and are absent. No factor, RHS, solve, H4 physics, PowerSI
result, 8 GiB proof, tombstone, or terminal seal was produced.

The exact original authorized token bytes remained present but the public
attempt was spent. The token was never reused and was removed by deletion-only
retirement commit `ba97dd8b274659a649d9a4020193c3ef72572665`. Current token
state is absent.

### 15.3 Frozen control-plane retry-v3 static evidence

Retry-v3 adds report/close-v2 and scope-v2 control retry evidence described in
Sections 6 and 8, including exact report-prefix-close ordering, count-all and
bounded-storage semantics, sticky first-fatal preservation, completeness and
close-only PID/birth coverage gates, and strict null/pass semantics. Factor
sampling remains uninstrumented at the one-attempt default. The exact frozen
source hashes are in Section 3. The full no-cache suite passed **312/312** in
77.99 s; the implementer-focused set passed **45/45**, and an independent
focused audit passed **74** with **238 deselected**. Python compilation,
PowerShell AST parsing, exact-byte binding checks, and diff checks are clean.
No static test calls a primary stage, creates a review token, or performs a real
factor or physics operation.

Two independent-audit observations remain explicitly deferred hardening, not
authorization gaps. A capped failed attempt-three virtual identity omitted
from stored retry events is not separately bound, but attempt-three exhaustion
already makes `monitor_failure` non-null and the gate false. A preserved failed
`stop_reason` is required to be nonempty rather than revalidated against an
exact allowlist at the final close, but it is coupled to an already sticky
failure and cannot authorize pass. Both observations are failed-only; neither
can produce terminal authority, and `next_stage_authorized` remains false.

### 15.4 Fourth public attempt under retry-v3

Clean retry-v3 contract `4d39eab9c464f67e8684e2d539ac3a2b092142a2` was
the sole parent of token-only commit
`9b4854d0cc7ae21e5e9eafc394a9474cb53ea689`. The canonical token ID was
`4c209c8a4dac49b89c59dd69bf68c4c2`, raw SHA-256
`d3bb3a42c83848678f0b99c0c669fffb5ab02e594cfba31ee9251c8216d05d64`.
The public runner was invoked exactly once from
`2026-08-15T13:37:16.0468244Z` through
`2026-08-15T13:37:20.5004777Z` and returned exit `2`.

```text
outer session: validation-output/av-bs1/outer-observer/session-2085628c53894c8eade7a735ec60f36c
control session: validation-output/av-bs1/control-plane/session-b2db30095a1042f4b4d5bbdc56550727
control invocation: preflight-2b050acbc608406fbf0308823618358f
outer inner-ready SHA-256: 485beb7c468c5f849554a099bf8fa4f456dc4748782968cbc4d2a3729bf7c5fc
outer start-release SHA-256: 296353a46cccd301adfdbeeef7d7538a79a724a73225c219c988608e20060c51
outer close SHA-256: 73073432faa0525f560b6536ea2a5fd54329ba3a30f04ab05ab8cef59460e758
control bootstrap SHA-256: 3b8d2230e316b541c0d59d6334c13eaa1cf1c0e56ab7f02b1753dbefaeddec0f
control canonical helper SHA-256: 7d80a4c230409aa0462a59f5cb9de167101ddd53b9f31119d0679f08a853d45c
control bootstrap-ready SHA-256: 3d123e0ca997949cc24d7a80ac775d0f129d803632e64e59a19d8bde80d05416
control start-release SHA-256: d0c6b2f8fbfcf39ea8bfdc8d9d28398007a7d75347224d3f6150616769406ede
public exit: 2
outer stop: OUTER_RESOURCE_EXCEPTION
monitor failure: NONROOT_DISAPPEARANCE_NOT_CONFIRMED
monitor context / attempt / PID: outer_tree_sample / 1 / 40224
expected / observed birth: 639223978396874096 / 639223978396874096
cleanup: verified
claim/factor/RHS/solve/physics/tombstone/seal: absent
```

The control bootstrap wrote ready and received start release, but did not write
target-complete. Consequently it never returned to the runner, which requires a
complete bounded preflight report/index/close before exclusive claim creation.
The outer monitor independently observed PID `40224` as identity-queryable and
exited with the exact expected positive birth, while a fresh complete Toolhelp
snapshot still contained that PID. Retry-v3 treated this exact transitional
combination as immediately fatal before its three-attempt whole-sample retry.

The close records 17 successful outer samples, one earlier confirmed
disappearance retry, cleanup verified, and a terminal post-cleanup sample. The
resource thresholds were not close to failing: peak tree working set
`464506880 B`, peak commit `1718468608 B`, lifetime peak commit `1806934016 B`,
minimum system commit headroom `73405591552 B`, and minimum available physical
memory `47205404672 B`. This is neither a factor result nor an 8 GiB feasibility
result.

No claim existed, so the exact authorized token bytes remained on disk. The
public attempt was nevertheless semantically spent under the frozen one-use
rule. The token was never reused or sealed and was removed by deletion-only
retirement commit `f1aeeac018a96cbd82341db36efbf5e5a9a55431`. Current
token state is absent; no owned attempt process remains live.

### 15.5 Historical retry-v4 static evidence

Retry-v4 changes only exited-descendant snapshot settling inside
`Get-ConfirmedTreeSampleDisappearance`. The additional path is enabled only
when the caller already passes `MaximumAttempts>1`, and only when the initial
probe is `exited`, carries a positive birth equal to the expected bound birth,
and the first complete snapshot still contains the PID. It then performs at
most two 25 ms rechecks, for at most three complete snapshots total, with root
identity checks around every recheck.

If a complete snapshot becomes absent, the function returns the existing
`CONFIRMED_NONROOT_DISAPPEARANCE` event and the existing whole-sample retry
logic applies. If the PID remains present after all three snapshots, the
existing `NONROOT_DISAPPEARANCE_NOT_CONFIRMED` failure remains fatal.
`not_found`/87 plus snapshot presence is still immediately fatal. So are live
reappearance, PID reuse, invalid or mismatched birth, root loss/reuse,
query/access/malformed results, incomplete snapshots, and retry exhaustion.
The function default and every factor call site remain `MaximumAttempts=1`.
There is no Python, schema, matrix, factor, or physics change.

```text
Python fixture SHA-256: 95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234
PowerShell runner SHA-256: 7893cd57fd4b1686addd434fa0103d9f3b0e0087f4783f2c82e459661abfb645
static tests SHA-256: 18aabcdf7b32c6b013aec65497d31f4d908f3085f53f64cb3791f38a2e79381d
focused retry-v4 tests: 11/11 passed
full no-cache P1 suite: 322/322 passed in 82.01 s
PowerShell AST: 47,623 tokens / 0 errors
Python AST: clean
token_state: absent
factorization_performed: false
physics_solve_performed: false
next_stage_authorized: false
```

### 15.6 Fifth public attempt and frozen retry-v5 static evidence

Retry-v4 contract `099db849564207b636f7431ee2fb52a540a7cb4e` was the sole
parent of token-only commit `5ba4b69398f526a0fcf640cf1dc4e7197cc7e660`.
Token ID `96d4f060ffa94d4888ffe3e59f550225`, raw/canonical SHA-256
`a9cbd954b27685892bf720c777ef57f49830da7970eee9a267d6d87eb5665d00` /
`a17271e2e3d4621a04b002edc7ada972a462f60552958282b3d07f99d441852b`,
was invoked exactly once from `2026-08-15T14:33:12.122Z` through
`2026-08-15T14:34:23.345Z`; public exit was `2`.

The claim exists at
`validation-output/av-bs1/claims/96d4f060ffa94d4888ffe3e59f550225.json`,
raw/canonical SHA-256
`0301eeccbf092799b97a914b4678bf8856aa8bb73296a07144435eddabf78103` /
`d9b7a41503dc32c591da9b2a06b512df5675f3a46eb78eaa24d3fe2b7e4b596e`.
Guard and monitor-ready SHA-256 are
`78c66a1b45e19c04a30a37ac433d0437994798408cce46afb2c327a8f2b80c65`
and `d6d76b6dc7c700f7681a0bd52593222a5fc6fa97420a6e066fd939df7dda95ac`.
Factor child PID/birth `55552/639224012129440335` was visible in all
`108/108` successful resource samples. Resource SHA-256
`d9b9a858c8f4aaebc1ca6ebb34f90235bcb84dd6288c04ecc95b3d7e5abc1da6`
records `MONITOR_QUERY_FAILED` and
`NONROOT_DISAPPEARANCE_NOT_CONFIRMED`; the active factor calls still used the
function default maximum `1`.

No factor prefix, factor-complete, or monitor-release artifact exists;
`numerical.json` and child stderr are zero bytes. Result SHA-256
`cf0d59c36e3f8275eeee56f30ccc7a2db74c986052714c6e31c6e26bc9de08e9`
contains the secondary `resource references missing child stdout` schema
overlay. Empty-stdout normalization is deferred outside retry-v5. Durable
completed factors, factor order, and certificates are empty; attempted and
performed are `null`; RHS/solve/physics are absent.

Outer session
`validation-output/av-bs1/outer-observer/session-1c765eb54ec54d5a801c87c960970c50`
has ready/start/complete/release/close SHA-256
`2480626ba65f421edddac2167c0493b788b66e0a4aa5ba6b83223a5d4e73d763`,
`431fe18fc919cc900e79e27c4d5d226bccfb86211b47bacbaa68703a1aab6b2c`,
`d2070d0a17253d2418ded332625887d5b6bde4473c7dce354f0c0bcd1e84ac86`,
`e8b44474a841d61370be17b0cb28fa34c91584c1a4a8a613a3a65dc04f508cb2`,
and `9d0c0aab58e78eb5d91d36be2aef46bf89968df87d4abcc044089805acce9af2`.
It verified cleanup/exit `2` with null monitor failure/stop reason but counted
`27` confirmed disappearances, stored `16`, and marked truncation. The
mandatory outer gate is false and terminal seal absent.

All six control invocations closed with true final gates. Preflight,
three canonical helpers, finalizer, and consumer report/close pairs are
`43a8563e.../55116d2b...`, `5deb17e3.../1fd601f6...`,
`4b89f156.../6d486bb5...`, `d4b4c97f.../bb372493...`,
`408620e1.../d7fb75d9...`, and `4865c635.../aa52f5f0...`; final session index
`0012` SHA-256 is `bc1a4a46d04e95d677577556da66f5aa0e669315530e1a6699b8c7990ec7f78f`.
The consumed tombstone SHA-256
`3caed852fd51fd908cbfa750becd2c63cbd6f768abff91eba23221bd0f418c2b`
is preserved by `46c08d405f530cce0cfbba9d908f8c266a26a002`; deletion-only
retirement `71d3dab442cbdfa6361e4e91de57d8f5b4d1a990` leaves token absent.

Retry-v5 changes only the four active factor calls to explicit maximum `3` and
the shared outer/control event cap `16` to `64`. Retry predicates and failure
policy are unchanged. Frozen static evidence is:

```text
Python fixture SHA-256: 54aa7da9daa013cec41585ae07757e6c54e8e9d15da7f4f16d4f6546b6fb35aa
PowerShell runner SHA-256: 3888524877f7a90966fb932f7f9fc4c9a48480134eb6295712eaa0ef548416a3
static tests SHA-256: b51498ebe27a0210a09b3a12b26e0146d39c1249906469bcb1add1d2f2443f08
focused retry-v5 tests: 29/29 passed in 15.91 s
full no-cache P1 suite: 324/324 passed in 82.91 s
PowerShell AST: 47,631 tokens / 0 errors
Python/test syntax: clean
token_state: absent
factor_fit_unproven: true
physics_solve_performed: false
next_stage_authorized: false
```

### 15.7 Sixth public attempt and retry-v6 corrective evidence

Clean retry-v5 contract `bddbf9cb3547ae0385c6e6bbc47424f630cca87e` was the sole
parent of token-only commit `82775327d79742b6c3111ad33a87fd1a4953ee79`.
Token ID `a3f49b44dd164da3a0ca1a6dc4c976c3`, raw/canonical SHA-256
`69b7e93720d8044d60f4ca95ccc5440670904d27f75ee77730e2947a01d58ed4` /
`68444b687e63750bdd0c7056a45b29d53d825c3572c2db03245cb032d63037a8`,
was invoked exactly once. Outer observation ran from
`2026-08-15T15:39:01.9701124Z` through `2026-08-15T15:39:55.3757836Z`;
the public invocation returned exit `2`.

The exact claim is
`validation-output/av-bs1/claims/a3f49b44dd164da3a0ca1a6dc4c976c3.json`,
raw SHA-256
`f15962f38e5141420a15536eda96653c76fa2ff26be6ec1e7c3fb69fe50ab955`.
Factor child PID `55380` was launched. Provisional numerical artifact SHA-256
`ab5adac793f9acda882bfa0c66c5ce20319e1cb06fe5cb88c9968803acf8822e`
records `BLOCKED_AV_BS_RESULT_SCHEMA`, detail
`claimed preflight payload mismatch`, and attempted/performed `false/false`.
The failure occurred before `_factor_one` and before the `splu` call. No
factor-prefix, factor-complete, monitor-release, completed factor, or
certificate exists; RHS, solve, H4 physics, and PowerSI remain unexecuted.

Outer session
`validation-output/av-bs1/outer-observer/session-21f46fec2e314e248cf051e4273b97f0`
has ready/start/close SHA-256
`b91d5b80d73efc02bd0ca55a48692d2db744fd033de1fff63ef301e20a88dd17`,
`118addd5e9740ab3ddd0e8dfd48279d933f9c874db6f073305096417b56ef3bd`,
and `d2777207e4000cbde8e4bf2332eeba96dd54ec3b8918a0d107049531d15cd8f8`.
It stopped independently on attempt-3
`TRANSIENT_DESCENDANT_DISAPPEARANCE_RETRY_EXHAUSTED`, context
`outer_tree_sample`, operation `get_process_times`, PID `53404`, with equal
positive expected/observed birth `639224051920616566`. All `30/30` retry events
were retained, `tree_sample_retry_events_truncated=false`, cleanup was verified,
and no terminal seal exists. Resource ceilings were not approached. This
failure does not invalidate the direct child false/false evidence, and the
child mismatch does not convert the outer exhaustion into a factor result.

Emergency replacement wrote exact raw record SHA-256
`43bb34b225b6b1d376715615a90b7b6202e10da5a10679484a644b1911c4d1fb`.
It truthfully records consumption and no terminal authority, and conservatively
keeps factor fields null because the outer layer lacks trusted inner phase
evidence. It is nevertheless strict-validator-invalid: nested `bindings`
omits `resource_policy_sha256`, although the top-level resource policy binding
is present. Commit `06061a234ad7b1b911d7425b7765482bda58a87a` preserves this
provisional record; deletion-only retirement
`c001b4498fc750b5955f5118844945c499fce119` removes the token. It is not a
validated tombstone or seal, and the sixth token may never be reused.

Retry-v6 makes exactly two minimal corrections. Python deletes the redundant
three-line post-claim call/comparison whose current manifest could not equal the
historical pre-claim payload. The runner adds
`bindings.resource_policy_sha256` to the emergency producer. Schema, ABI,
validator strictness, retry policy and cap64, matrix/factor operations, RHS,
solve, and physics remain unchanged. Frozen candidate evidence is:

```text
Python fixture SHA-256: 2373a13f51e2833e416e1ce6326587b9e1c782b5165d99f7002e7dbc4658ebc4
PowerShell runner SHA-256: 852ce8a03b25e33b9eb26ec6f5ce295381dab493b1b26762ddea14be7196000d
static tests SHA-256: f1d0b044cbf53e90dba128ec398ccd8b7a81da8c5137bea202b2852eb3f288af
focused retry-v6 tests: 8/8 passed
full no-cache P1 suite: 330/330 passed in 81.83 s
token_state: absent
factor_fit_unproven: true
physics_solve_performed: false
next_stage_authorized: false
```

### 15.8 Seventh public attempt and retry-v7 corrective evidence

The seventh public `primary-h4-p0r` invocation ran exactly once from token-only
commit `4f60bd5e5fe4e166e72ba00e9bb019a70504e7df`. Token ID
`a7a3942cb0cf421e9fc52fd43176bb35` had original raw SHA-256
`3b6cd6557832358e6235824d680b8fe01eaf29899b89c2dea1850e9109e4511b`.
Result and quarantined final bytes are identical at SHA-256
`e2be4dd5fa0467012c194239c8b8ac833723b4355a371c42a8aafc089efeb932`.
Numerical stdout SHA-256 is
`5bf26072794a4030ec311d3c60bcf3efd8c8d112319587d0a48d65490fd56e72`;
its canonical payload SHA-256 is
`173e22362525cb34e3d1812731779b5ad454e6d5ea16245ad891904814df19ee`.

The child entered `_factor_one("A_background_II")`, and native `splu` returned.
Durable phase state therefore records attempted/performed `true/true` and
`completed_factors=["A_background_II"]`. Factor-complete marker SHA-256
`e62d3964dcab03c16e88be9e69c78aca0583bf96e2188ac24058bbbc67b5cea5`
binds that completed-name list and the canonical empty certificate list. The
obsolete check required `factor.L.nnz + factor.U.nnz` to equal native
`factor.nnz`; SciPy's exported CSC factors may have fewer stored entries than
the internal SuperLU representation. The check failed with
`A_background_II native/exported nnz mismatch` before certificate construction
or either prefix write. Consequently `factor_certificates=[]`, both prefix
hashes are null, `A_conductor_II` was not attempted, and exact native/exported
factor nnz counts are absent from durable evidence. A native return is not a
certified or reusable factor result.

Resource SHA-256
`0f738eb5f098944618d80b1cb2c18b161068a991ef8a4e98ed8a055ba6af324b`
retains `174` inner samples and passes its mandatory resource gate. Outer close
SHA-256
`0e88d08f60f56c95678dd8f6c7ae850b681fd60ba8ed6fc9d3868dcb8080ec01`
retains `573` samples and all `31` retry events without truncation, verifies
cleanup, and passes its mandatory resource gate. Terminal seal SHA-256
`0b2f56231315bb260921f2b3fe220fea33315cce4679964c6b86711a4f8b51a1`
sets `terminal_evidence_complete=true`, `authoritative_stage_pass=false`, and
`next_stage_authorized=false`. These observations make no accuracy claim and
do not prove an 8 GiB factor fit. No RHS, solve, H4 physics, or PowerSI work ran.

Commit `51e50699546fe5b594dfdc669fb1628de0c3adb7` records the consumed
attempt; deletion-only retirement `17414cf0be147d0d5d9d75046354e199f707e7a6`
removes the token. Retry-v7 changes only the producer and downstream validator
interpretation of existing fields: require `0 < exported L/U nnz <= native
factor nnz`; compute native and exported portable-byte formulas separately;
and apply the existing cap to `exported_factor_bytes`, exported portable bytes,
and conservative native portable bytes. Schema, messages, retry/cap policy,
factor order, forbidden operations, and later-stage authority remain unchanged.

```text
Python fixture SHA-256: 7a1dba5eafcbf601fd532a9a3d2bc10de2218c74bccdacd04bdba5f1038df348
PowerShell runner SHA-256: 852ce8a03b25e33b9eb26ec6f5ce295381dab493b1b26762ddea14be7196000d
static tests SHA-256: bd2e3e2ca4d668d3cd8c92b55737a74f6bff743a823bae024713d59d393c8ba7
focused retry-v7 tests: 25/25 passed
full no-cache P1 suite: 341/341 passed in 88.40 s
token_state: absent
factor_fit_unproven: true
physics_solve_performed: false
next_stage_authorized: false
```

## 16. Exact next sequence

The only permitted next sequence is:

The retry-v7 nnz-semantic correction does not create or authorize a token and
does not widen the factor-only boundary. The retired token-only commits
`b6c8615...`, `d9e064f...`, `6328174...`, `9b4854d0...`, `5ba4b693...`,
`82775327...`, and `4f60bd5...` must never be invoked again.

1. Freeze the retry-v7 fixture, unchanged runner, tests, documentation,
   unchanged schema bindings, safe no-token manifest, and completed full
   no-cache `341/341` pass in `88.40 s`. Complete independent code,
   documentation, and contract audits.
2. Create one clean executable-contract commit containing exactly those
   reviewed bytes and bindings. The contract commit contains no P1 review
   token.
3. Re-read the exact committed no-token manifest. Proceed only if every frozen
   binding and authorization prerequisite validates; otherwise stop with no
   token.
4. Create one child commit with exactly one parent (the clean contract commit)
   that adds only the canonical one-use P1 review-token file. The token binds
   the final contract commit and every frozen hash.
5. Only with separate authorization, invoke the public `primary-h4-p0r` runner
   exactly once for the two-factor, zero-RHS, zero-solve pilot. Never invoke any
   of the seven prior token commits.
6. Classify the outcome only from the v2 tombstone, outer terminal seal, and
   the complete current-byte-bound claim, guard, resource, result, child,
   marker, prefix, report, index, close, and applicable recovery-journal chain.
   Preserve the quarantine and journals. Regardless of pass or failure, do
   not authorize H4-P1 or any physics stage.

Any hash drift, dirty checkout, missing prerequisite, failed final audit,
unexpected token-commit content, missing cleanup proof, or absent/invalid seal
stops this sequence fail-closed.
