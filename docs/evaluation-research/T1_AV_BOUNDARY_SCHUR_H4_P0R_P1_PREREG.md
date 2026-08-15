# SPD Decap PI Evaluator v0.22.0 - AV-BS1 H4-P0R-P1 executable-contract candidate

## 1. Current status and authority

This document preregisters the second corrective static executable-contract
candidate after two public invocations stopped before claim and factor work.
It is pending final document audit and a new clean token-absent contract
commit. It does not itself authorize a pilot, and neither prior token may be
reused.

The current safe manifest classification is:

- `token_state=absent`;
- `status=candidate_token_missing_no_factor`;
- `authorization_state=not_authorized`;
- `terminal_evidence_complete=false`;
- `authoritative_stage_pass=false`;
- no H4-P0R-P1 factorization or physics solve has run; and
- no later H4-P1 stage is authorized.

The current live manifest now records
`control_plane.independently_bounded=true`,
`tree_thresholds_equal_factor_envelope=true`,
`system_floor_recheck_before_and_after_each=true`, and
`implementation_status=implemented_and_static_audited` for both the bounded
control-plane supervisor and outer observer, with their readiness-specific
`authorization_blocker=false`. These facts make the exact candidate eligible
for later token review; they do not authorize a pilot while the token is absent
and do not change any terminal or next-stage authorization blocker.

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
| [Python fixture](../../tools/research/av_bs1_boundary_schur_h4_p0r_p1.py) | `8c497cb1d0926600b2ddd974df6fd8d40b09471c617869294a01c0e018ce5acf` |
| [PowerShell runner](../../tools/research/run_av_bs1_h4_p0r_p1_stage.ps1) | `4272d6725cb0e16b7ce39b083985965f792da5893f222ba07cf91695fba8f30c` |
| [Static P1 tests](../../tests/test_research_av_bs1_boundary_schur_h4_p0r_p1.py) | `b44366596690d6d55d18a89d8df2370faca767fca722affd5e07268da3195235` |

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
exclusive claim creation, an untouched authorized token may remain retryable;
after a non-adversarial interruption with a surviving owned claim, local replay
remains blocked. No uncontrolled interruption by itself creates terminal
authority, and protection against self-consistent adversarial rewrites is not
claimed.

The exact disclosures remain
`external_runner_or_machine_kill_terminal_state_guaranteed=false` and
`adversarial_local_caller_exclusion_claimed=false`.

## 15. Static evidence for this candidate

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

## 16. Exact next sequence

The only permitted next sequence is:

The control-plane and outer-observer readiness transition plus the bounded
tree-sample correction have been applied to this candidate. They do not create
or authorize a token. The retired token-only commits `b6c8615...` and
`d9e064f...` must never be invoked again.

1. Freeze these readiness-complete corrective bytes, complete an independent final
   document/contract audit, and compute the final document SHA-256 and resulting
   live P1 wrapper payload externally.
2. Only after that audit, create one clean executable-contract commit
   containing the reviewed fixture, runner, tests, document, and manifest
   bindings. The contract commit contains no P1 review token.
3. Re-read the exact committed manifest. Proceed only if its authorization
   prerequisites validate; otherwise stop with no token.
4. Create one child commit with exactly one parent (the contract commit) that
   adds only the canonical one-use P1 review-token file. The token binds the
   final contract commit and every frozen hash.
5. From that clean token-only commit, invoke the public
   `primary-h4-p0r` runner exactly once for the two-factor, zero-RHS,
   zero-solve pilot.
6. Classify the outcome only from the v2 tombstone, outer terminal seal, and
   the complete current-byte-bound claim, guard, resource, result, child,
   marker, prefix, report, index, close, and applicable recovery-journal chain.
   Preserve the quarantine and journals. Regardless of pass or failure, do
   not authorize H4-P1 or any physics stage.

Any hash drift, dirty checkout, missing prerequisite, failed final audit,
unexpected token-commit content, missing cleanup proof, or absent/invalid seal
stops this sequence fail-closed.
