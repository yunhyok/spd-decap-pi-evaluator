# SPD Decap PI Evaluator v0.22.0 - AV-BS1 H4-P1 manifest-only preregistration

## 1. Current status and authority

The current status is **`preregistered_H4_P1_contract_only_no_solve`**. This
document and its Python fixture freeze a prospective `primary-h4` accuracy,
resource, sidecar, and lifecycle contract. They do not implement or authorize
that stage.

Current authority is deliberately closed:

- `authorization_state=not_authorized` and `execution_authorized=false`;
- the review-token path is absent from the worktree, index, and `HEAD`;
- `token_state=absent`, but only a successfully validated manifest may assert
  `token_absence_validated=true`;
- `mandatory_stage_pass=false`, `terminal_evidence_complete=false`,
  `authoritative_terminal_evidence=false`, and
  `authoritative_stage_pass=false`;
- `current_terminal_classification=token_absent_no_terminal_evidence` and
  `current_terminal_validation_error=null`;
- `available_stages=["manifest"]`, `available_solve_stages=[]`, and
  `token_gated_stages=[]`;
- `primary-h4`, `withheld`, and `EQ0` remain unavailable; and
- `next_stage_authorized=false`.

No token, claim, RHS, factorization, factor solve, harmonic extension,
boundary operator, binary sidecar, modal response, power result, public result,
finalizer, consumer, H4 physics, or PowerSI comparison was created or run by
this preregistration. It changes no production parser, solver, UI, installer,
release, or application version. The recognizable program identity remains
**SPD Decap PI Evaluator v0.22.0**.

The authoritative sealed eleventh H4-P0R factor-only pass remains immutable
historical parent evidence. Its authority is not current H4-P1 terminal
evidence and does not create a token or authorize a solve.

## 2. Frozen artifact and provenance bindings

The stable manifest-only implementation is:

| artifact | bytes | LF | CR | SHA-256 |
|---|---:|---:|---:|---|
| `tools/research/av_bs1_boundary_schur_h4_p1.py` | `47295` | `852` | `0` | `6f527c76cbc0928a4b504e01abbb4c049fd39edc75ef865a3335f3a619863200` |
| `tests/test_research_av_bs1_boundary_schur_h4_p1.py` | `63350` | `1341` | `0` | `6254e3d28b56d8ec640089c02fdf3308e33775e4c3cd2d6946932bfc1ed7ac9d` |

The manifest schema is `AV-BS1-h4-p1-manifest-v1`; its frozen canonical
payload SHA-256 is
`ec489c784e01635955560fa0ac0681b22bd890759d6693c111cb8ff28c31b753`
and its wrapper SHA-256 is
`cfa8f9a2504a8fad40ef03e9cb3842877e176a9f3d620f5805099a98c80523e7`.

Frozen canonical sub-contract SHA-256 digests are:

| canonical sub-contract | SHA-256 |
|---|---|
| matrix contract | `9d5106c3086d04fa71c19f37bbf22230c3ea5f4696c4dd47e07cc727845f2910` |
| execution contract | `251f645fc4f6e4ef489dabffe63e5bb88ec2f4cff8493abd5110846f9facf52c` |
| accuracy gates | `7aacde866ccf22a7d81e54fda6c95346e65d4f6de8918617b84d1f51d0e69e6d` |
| binary sidecar descriptor | `fd664715dfc151cc1f4bd9bbebfd1cf6c6d9c2da37c9c723699c4129fed03cf6` |
| resource policy | `d12da67dbc23cc2bc34755d1fa73c45aa993561cd5be4de75d95402cdc638b26` |

Required ancestry is exact:

| role | commit |
|---|---|
| retry-v10 strict contract | `01d198a851be7ce08db963507bb902986eb0dd15` |
| eleventh token-only child | `e35ef01214f4bf9ec75e7b428e72b21d38c9161b` |
| M-only consumed token | `bf94e1890c489e690801b3d90bcbf50ff61ca233` |
| D-only token retirement | `389ec1e51b87f3eff928278c76bbbf60241af972` |
| post-eleventh exact15 documentation | `50f9e908d0fc740c05e84c6ae65262dd8774553a` |

The existing H4-P0R Python, runner, and tests remain frozen at SHA-256
`46082123fbf98102f3e5995c32bf65d8d04bb835b9c1305de0150349e75e48c7`,
`f0159061dbd4d1b34881911edfdfb72146a3a23e5cdc75ca8fa4069c08aadb86`,
and `e5496ceb24c33c835b88ce20a3f67f941c8279e0471708a01022238fc211b109`.
The H4-P1 fixture reads the committed historical blobs through an allowlisted
read-only Git interface; it does not open ignored evidence files or reinterpret
uncommitted quarantine data as authority.

The pre-H4-P1 `SESSION_LOG.md` prefix is immutable: `200255` bytes, `2707` LF,
`0` CR, trailing LF present, SHA-256
`0d44c6a6abebe1a48fba351f18bced194428ee13a47792a8fd8386da1f9ae3cb`.
All new session evidence must be appended after that exact prefix.

## 3. Parser and output boundary

The CLI parser has one required option and one accepted value:

```text
--stage manifest
```

There is no default stage, no `primary-h4` parser choice, no runner, and no
public execution entry point. A successful call returns one canonical JSON
wrapper and exit `0`. The wrapper binds the payload and wrapper hashes above.
An `AvBsError` returns the manifest-failure schema and exit `2`; it must use
`token_state=unvalidated_or_unexpected` and
`token_absence_validated=false`, because a failure before provenance validation
cannot claim that token absence was checked. `argparse` rejection remains an
ordinary parser failure rather than a fabricated manifest result.

Canonical JSON uses sorted keys, compact separators, UTF-8, and
`allow_nan=false`. A checksum mismatch is
`BLOCKED_AV_BS_RESULT_SCHEMA`. The fixture has no code path that writes a token,
claim, sidecar, result, tombstone, seal, or any other artifact.

## 4. Historical eleventh factor-only parent evidence

The eleventh public `primary-h4-p0r` invocation ran exactly once. Public runner,
inner runner, factor producer, finalizer, and consumer PIDs were
`43900`/`62976`/`63268`/`39580`/`60012`; all exited `0`. Exactly two factors and
prefixes were retained in order:

| factor | input nnz | L/U nnz | fill ratio | exported bytes | portable bytes | native-portable bytes | factor wall |
|---|---:|---:|---:|---:|---:|---:|---:|
| `A_background_II` | `204545` | `1570627` / `1615176` | `15.57507150015889` | `64219892` | `77466936` | `81218256` | `1.3330735 s` |
| `A_conductor_II` | `219393` | `1702688` / `1716333` | `15.584002224318917` | `68884252` | `83064168` | `86058168` | `1.4459152 s` |

Both certificates have `rhs_count=0`, `factor_solve_called=false`, and
`physics_solve_performed=false`. Inner monitoring retained `243/243` samples in
`34.0036179 s`, with working-set/private/lifetime-commit peaks
`510849024`/`1954021376`/`2358079488` bytes. Outer monitoring retained
`677/677` samples and `47` nontruncated retries in `94.5411269 s`, with peaks
`513384448`/`1958121472`/`2358079488` bytes. All `164` outer identities were
absent after cleanup. The broader `207` identities had zero same-birth live
processes: `206` PIDs absent and one reused.

Historical classification is `consumed_v2_authoritative_sealed_pass` /
`consumed_v2_authoritative_sealed_pass_no_next_stage_authorization`. Minimum
host available physical memory was `43885748224` bytes. This is authoritative
factor-only evidence on that host, not H4 physics, PowerSI accuracy, or an
8 GB laptop result. The published result and consumed tombstone were both
non-authoritative before the seal:
`published_result_pre_seal_authoritative=false` and
`consumed_tombstone_pre_seal_authoritative=false`. Only the retained terminal
seal is historically authoritative (`terminal_seal_historical_authoritative=true`).
The manifest does not open the ignored evidence files and does not live-revalidate
their commitments. None of this historical authority is current H4-P1 authority.

## 5. Frozen matrix, equilibration, factor, and solve contract

The H4 interior has `31489` nodes and `512` boundary nodes. Frozen physical
inputs are `frequency_hz=100000.0`,
`angular_frequency_rad_per_s=628318.5307179586`, and
`conductor_sigma_siemens_per_m=59600000.0`. The two raw matrices are exactly
`A_background_II=complex128(K_II)` and
`A_conductor_II=complex128(K_II)+1j*angular_frequency_rad_per_s*conductor_sigma_siemens_per_m*M_II`.
Frozen hashes are:

| object | SHA-256 |
|---|---|
| aggregate matrix contract | `89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46` |
| `K_II` | `dddee397ee96ff0bd7acc321cd5755dd611f1e3d1f6bfcf9664c388d27184361` |
| `M_II` | `4dccc26f1a12bbab4a4ee863e509d317cb518075661867da4e0b6980f7dcc6c4` |
| background raw / `Dr` / `Dc` / `Aeq` | `8c099d2cb5947d1b72bd74f64329879c221b86c569b4100baa47dfea9aeff641` / `13565a65f7c443cb5aadb9dc01cfd0b9e0c7d71bd5b92d4f62e8a2723120732e` / `879a3dfdcde792239d2d2ad235c36e96c02eeb75f989c45adcaf3eee50cbc633` / `9216cc8938d9efc1e531a4fb301aca45547fad55f01dec57863986622327dd74` |
| conductor raw / `Dr` / `Dc` / `Aeq` | `5ead3bbdc2fc1b4f7a1a94bb7c9ebf9401c6a7d96ee63335045d64e3b082225f` / `2e7a7b1c7d1bc2b51d33efd4185a2214422aaea0530d3483348c53422311987d` / `0a531cd1ded535c412bb9151298e108a429bc82e29b96f665d8ba1448acba3f4` / `b4efc2d388b2fe5c3280e8481d5afedd497952da3f18b34a86674a7ce97daf54` |

For each raw matrix, compute finite positive row maxima and `Dr`, then
`A_row=Dr@A_raw`; compute finite positive column maxima from `A_row` and `Dc`,
then `Aeq=Dr@A_raw@Dc`. A zero or nonfinite row or column maximum fails before
factorization. Factorization is exactly once per factor:

```text
splu(Aeq, permc_spec="COLAMD", diag_pivot_thresh=1.0,
     options={"Equil": False})
```

No fallback ordering, pivot threshold, or tuning is registered. Each resident
factor must solve all `512` raw RHS columns in contiguous ascending batches of
at most `4`: `rhs_eq=Dr@rhs_raw`, `z=factor.solve(rhs_eq)`, and `x=Dc@z`.
Residuals are measured on the original `A_raw`, `rhs_raw`, and recovered `x`.
The same factor may not be rebuilt. Only one factor is resident at a time, and
each completed interior extension is retained through operator assembly. After
the first extension is complete, its factor, L/U, permutations, scales, and
matrix are released before the second factor and its matrix are constructed;
the two completed extensions, but never the two factors, coexist. L/U or
permutation persistence or rehydration and a standalone synthetic solve result
are forbidden.

## 6. Fixed binary sidecar contract

Future full output is too large for the 16 MiB JSON boundary. The fixed
operator construction is exactly
`H_background=vertical_stack([X_background,I_boundary])`,
`H_conductor=vertical_stack([X_conductor,I_boundary])`,
`Y=sigma*H_background.T@M@H_conductor`, and
`Y_reverse=sigma*H_conductor.T@M@H_background`. These `.T` operations are
bilinear, non-conjugate transposes. For signed modes `-4..+4`,
`V_M9[n,j]=exp(1j*signed_modes[j]*theta_boundary[n])` and
`M9_interior=X_conductor@V_M9`.

The fixed little-endian IEEE-754 complex128, C-order, uncompressed layout is:

| array | offset | shape | bytes |
|---|---:|---:|---:|
| `Y` | `0` | `512 x 512` | `4194304` |
| `Y_reverse` | `4194304` | `512 x 512` | `4194304` |
| `M9_interior` | `8388608` | `31489 x 9` | `4534416` |

Total raw bytes are `12923024`. Payload-only base64 would be `17230700` bytes,
which exceeds 16 MiB by `453484` bytes before JSON descriptor overhead.
Therefore future execution must publish `av_bs1_h4_p1_arrays.bin` from a
same-directory create-new temporary file; temporary and final paths must be on
the same volume. The temporary file is flushed before an atomic no-replace
publish. Check-then-replace and overwrite are forbidden, and an existing final
target is immutable and rejected without mutation. After publication, exact
size, raw SHA-256, and file identity are rechecked; any failure requires
temporary-file cleanup.

The final relative path remains in the descriptor's quarantine directory, with
no absolute or parent-traversal path and no symlink/reparse point. Exact offset,
shape, dtype, order, size, finiteness, and raw-SHA checks apply. Each negative
zero component is normalized before serialization. Missing/trailing bytes,
tamper, identity drift, nonfinite values, or descriptor/binary hash mismatch
fail closed. Descriptor canonical hash and binary raw hash must both bind the
quarantine result, consumed tombstone, and terminal seal. This preregistration
creates no sidecar.

## 7. Accuracy and raw-physics gates

All required values and intermediates must be finite; every required
denominator must be finite and strictly positive. Missing signed modes or no
eligible phase mode in either comparison family fails closed. No
symmetrization, plus/minus averaging, clipping, post-hoc floor adjustment,
seed change, mode selection, eligibility change, or result-driven tuning is
allowed.

- Every one of `512` RHS for each raw matrix must have backward residual
  `maxabs(A*x-b)/(norminf(A)*maxabs(x)+maxabs(b)) <= 1e-10`.
- For both `A_background_II_Aeq` and `A_conductor_II_Aeq`, the inverse linear
  operator uses the same resident factor: `factor.solve(value,trans='N')` for
  `matvec`/`matmat` and `factor.solve(value,trans='H')` for
  `rmatvec`/`rmatmat`. Save the NumPy RNG state, seed and run
  `onenormest(t=4,itmax=10)` once with `1729` and once with `2718`, then restore
  the saved RNG state. The gate is exactly
  `kappa1_u=norm1(Aeq)*max(inverse_estimate_seed_1729,inverse_estimate_seed_2718)*2**-53 <= 1e-8`;
  minimum, average, or single-seed substitution is forbidden.
- `K_II`, `M_II`, and both raw A matrices require
  `normF(A-A.T)/normF(A) <= 1e-12`.
- With `operator_floor=max(1e-18,1e-10*maxabs(Y))`, independent reverse
  consistency is `<=1e-12` and raw reciprocity is `<=1e-8`, both using
  denominator `max(normF(Y),512*operator_floor)`.
- Raw passivity evaluates only `H=(Y+Y^H)/2` and requires
  `lambda_min(H) >= -max(operator_floor,1e-9*norm2(Y))`; `H` never repairs `Y`.
- Signed modes are exactly `-4..+4`, with
  `e_m[n]=exp(1j*m*theta_n)` and
  `Yhat_m=(e_m.conj().T@Y@e_m)/(e_m.conj().T@M_Gamma@e_m)`. The trace denominator e_m.conj().T@M_Gamma@e_m must be finite, real, and strictly positive; the numerator, Yhat_m, and all intermediates must be finite. Modal field residual is `<=1e-10`.
  Boundary power is `0.5*Re(v^H*Y*v)` and volume power is
  `0.5*sigma*Re(u^H*(MII*u+MIg*v)+v^H*(MGI*u+MGG*v))`; mismatch is `<=1e-8`
  with denominator `max(abs(Pboundary),abs(Pvolume),1e-18)`.
- Frozen analytic/H2 comparison floor is `5.214985896880959e-08 S`. Analytic
  relative error is
  `abs(Yhat_h4-analytic)/max(abs(analytic),frozen_analytic_mode_floor_S)` and
  must be `<=0.5%` for every signed mode. Analytic phase is eligible only when
  both `abs(Yhat_h4)` and `abs(analytic)` are at least `10*floor`; eligible
  phase must be `<=0.25 deg`, with at least one eligible analytic mode.
- H2-to-H4 relative error uses exactly
  `abs(Yhat_h4-Yhat_h2)/max(abs(Yhat_h4),frozen_h2_comparison_floor_S)`.
  Its RMS is the equal-weight nine-mode
  `sqrt(sum(relative_error_m**2 for m in signed_modes)/9)` and must be
  `<=0.5%`; the maximum must be `<=1%`. H2 phase is eligible only when both
  `abs(Yhat_h2)` and `abs(Yhat_h4)` are at least `10*floor`; eligible phase
  must be `<=0.25 deg`, with at least one eligible mesh phase mode.
- Every ineligible analytic or H2 phase output is JSON `null`, never zero and
  never a substituted pass.
- Each `+/-m` degeneracy error for `m=1..4` is exactly
  `abs(Yhat_m-Yhat_minus_m)/max(abs(Yhat_m),abs(Yhat_minus_m),plus_minus_mode_floor_S)`
  and must be `<=1e-8`.

## 8. H2 comparison commitment

The H2 comparison is a frozen input, not live ignored evidence:

| evidence | SHA-256 |
|---|---|
| H2 result file | `b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55` |
| H2 result payload | `5704f3feb5bb90b6e38f8ed3ec67fc690339e9b7f0c1722d40a977295e82593e` |
| H2 numerical payload | `bd2f6542a93e3a7adc62f4435540752651ddf1cff84226319b4aa5e8dbdc0be5` |

Its retained status string is
`passed_AV_BS_h2_stage_only_pending_h4_preregistration`; that string describes
the historical H2 artifact and is not renamed retroactively. The manifest does
not live-revalidate ignored H2 files. Any future execution requires exact H2
evidence restoration or a separately committed compact certificate.

## 9. Resource policy and 8 GB non-claim

Prospective memory arithmetic is exact:

| term | bytes |
|---|---:|
| sparse base arrays | `5449772` |
| 16x sparse-copy allowance | `87196352` |
| one-factor hard cap | `2147483648` |
| two interior extensions | `515915776` |
| three dense boundary matrices | `12582912` |
| batch-4 RHS work | `8061184` |
| raw total | `2776689644` |
| ceiling with 25% margin | `3470862055` |
| slack below 4 GiB working-set stop | `824105241` |

Pre-spawn minimum available physical memory is `5081474791` bytes and minimum
system commit headroom is `5618345703` bytes. Runtime stops are process-tree
working set above `4294967296`, private or lifetime commit above `5368709120`,
wall time above `900 s`, available physical memory below `1610612736`, or
system commit headroom below `2147483648`.

Monitoring is every `100 ms` across the outer observer, inner runner, and all
descendants bound by `(process_id, process_birth_ticks)`. Parent-only sampling
cannot satisfy factor-solve visibility: at least one successful sample must
observe the factor-solve child identity. An incomplete or unreadable query,
identity ambiguity, sample or retry truncation, or any resource stop is a
failure. Terminal pass requires every same-birth descendant to be absent; PID
reuse counts as absence only when the observed birth identity differs.

These are prospective arithmetic and stop rules only. They are not a measured
8 GB laptop result, and `eight_gib_execution_proven=false` remains mandatory.
PowerSI accuracy remains the first priority; laptop feasibility is second.

## 10. Fail-closed result boundary

The allowlist is `BLOCKED_AV_BS_SOLVE`, `BLOCKED_AV_BS_RECIPROCITY`,
`BLOCKED_AV_BS_PASSIVITY`, `BLOCKED_AV_BS_POWER`, `BLOCKED_AV_BS_ANALYTIC`,
`BLOCKED_AV_BS_RESOURCE`, `BLOCKED_AV_BS_MESH_HASH`,
`BLOCKED_AV_BS_RESULT_SCHEMA`, and `BLOCKED_AV_BS_FACTOR`. Missing provenance,
hash drift, token presence, dirty unexpected paths, unbound historical evidence,
invalid sidecar identity, nonfinite values, zero denominators, resource-monitor
gaps, or any forbidden operation stops the future sequence. A failure cannot
be converted into a synthetic pass, a partial result, or an authorization.

## 11. Static validation and final documentation freeze

The stable fixture/test bytes passed the focused selection `32/32` in
`19.01 s`. Two independent same-byte runs passed `32/32` in `19.51 s` and
`18.92 s`. The confirmed collection applies exactly to
`tests/test_research_av_bs1_boundary_schur_h4_p0r_p1.py` and
`tests/test_research_av_bs1_boundary_schur_h4_p1.py`: `432 tests collected in
0.56 s`, exit `0`, without invoking any execution stage.

The first successful full run of those same two paths recorded the exact pytest
terminal result `432 passed in 111.82s (0:01:51)`, exit `0`, with no failure. In
exact-document form, `432/432` passed in `111.82 s`, exit `0`, with no failure.
This is the **FINAL DOC FREEZE**. Do not record any later closure runtime.

## 12. Exact next sequence

1. Preserve the stable fixture and test hashes above and the immutable
   `SESSION_LOG.md` prefix.
2. Update and mechanically validate exactly the 15 baseline Markdown documents
   plus this new preregistration document.
3. Preserve the confirmed selected collection and verify UTF-8/LF, balanced
   fences, local links, parser wording, current/historical separation, and
   absence of stale current authority.
4. Preserve the first successful full exact-document result, `432/432` passed
   in `111.82 s`, exit `0`, with no failure. Do not replace it with later
   closure timing.
5. Independently audit the final diff and commit only the manifest fixture,
   static test, this preregistration, and the exact15 documentation updates.
6. Keep `primary-h4` unavailable and the token absent. A future executable,
   runner, lifecycle, and fresh one-use token require a new clean contract,
   independent approval, and explicit authorization. This manifest creates
   none of them.

Any failed prerequisite, byte drift, unexpected token, broader code change, or
attempt to reinterpret the historical factor-only seal as current H4-P1
authority returns to the fail-closed no-token state.
