# Evaluation Accuracy and Modeling Boundary

> Technical appendix to the canonical [Product Purpose and Technical
> Baseline](PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md). This document explains
> the current model; it does not define product priority or prove PowerSI
> accuracy promotion. If a historical statement here conflicts with the
> canonical baseline, the canonical baseline governs the current claim.

## Scope

SPD Decap PI Evaluator is a pre-design, single-rail `Zii` evaluator. It uses
the imported SPD drawing and does not write a stack-up, plane, or optimization
model. It is not a PowerSI/SIwave replacement, and it has no reference-plane
feedback loop from a field solver into its results.

The v0.11 actual-short cluster handling remains in force: source-established
TOP copper shorts are represented as one physical cluster, rather than being
split into synthetic independent decap branches.

## Production layer-surface composition

v0.22.0 keeps every retained physical `(layer, NET)` artwork surface as an
independent circuit node. For every adjacent conductor gap `k`, exact ordered
add/subtract artwork produces a multi-NET Maxwell capacitance block `Ck`.
Frequency-dependent dielectric data convert it to a complex gap admittance,
and a Boolean embedding matrix `Ek` maps the local block into the global
surface-node ordering:

```text
Ygap,k(f) = j 2 pi f * epsilon_ratio,k(f) * Ck
Yglobal(f) = sum_k transpose(Ek) * Ygap,k(f) * Ek + Yvia(f)
```

Raw-SPD same-NET Trace/Via connected components coalesce only the physical
surfaces whose retained artwork they actually contact. A component with
branched topology provides an ideal topology link but no invented serial R/L.
A uniquely owned source Via link may contribute its finite passive R/L once;
Device-terminal and decap-loop R/L remain external branches and are not
duplicated inside the substrate.

For a selected port incidence vector `b`, one gauge is removed and the sparse
system is solved rather than explicitly inverted:

```text
Yreduced * v = b
Zport = transpose(b) * v
Yport = 1 / Zport
```

This is the open-port Schur/Kron equivalent of eliminating all internal layer
interfaces together. It is not the same as multiplying scalar impedances or
raw two-port S matrices. An S-parameter/ABCD product is valid only for sections
with compatible wave-port bases and a simple cascade. Shared planes, floating
copper, branched Vias, and shunt decaps require common internal unknowns and a
global network elimination (or an equivalent Redheffer-star construction).

For the production terminal-complete scope, the global-Y Schur/Kron result is
read directly at the external differential Device port. It is the sole Zii
input: the rectangular modal matrix is not prepared, modal C00 is not
double-stamped, and no legacy higher-mode one-port difference is added in
parallel. This is still a source-derived quasi-static circuit network, not a
full-wave layer-pair S-parameter solver. PowerSI Touchstone data are
comparison-only and are never read while constructing the network or fitting
its parameters.

The previous rectangular shared-PWR formulation remains available only through
the explicit **Legacy modal** rollback profile; it is no longer the desktop
application default.

## MLO microvia conductor model

For a source-proven vertical segment, v0.14 uses a solid copper-filled area
`A = pi(d/2)^2` only when its source padstack declares `Material=COPPER`, its
drill is at most 150 um, its endpoints are exactly two conductor layers with
one intervening dielectric row, and `dielectric thickness / drill <= 1.0`.
The `USER_CONFIRMED_MLO_COPPER_FILL_ASSUMPTION...` provenance identifies a
user-confirmed fabrication assumption supported by source material and
geometry; the SPD does not itself prove copper fill or provide a generic
manufacturing fill flag. Any missing, conflicting, deep/core, or non-qualifying evidence
keeps the prior conservative plated-barrel area
`A = pi*d*min(20 um, d/4)`. The straight-segment inductance expression is
unchanged.

The source audit supporting this approximation recorded 15,859 DGND tie
evidence items (5.056/mm2); the reported spacing statistics were p50 130 um
and p95 184 um. Reports identify the validation inputs by basename/hash only,
not by a local user path. SPD: `S4LB002-2Para_260724_1_injected.spd`, SHA-256
`51c0521c153344d99020b3adf54204d7a18b4673dc5bc460eb653f790d0f7ed2`. Touchstone:
`S4LB002-2Para_260724_1_injected_072626_223114_32096_S.s24p`, SHA-256
`bf1c7db0b3d64c9cd60894cc1b277bea4b3e617795a0c76f0e18fa0451d147fa`.

## Numerical convergence and validation

The intended v0.22.0 named-case evidence structure is kept in the
[two-case validation record](EVALUATION_LAYER_SURFACE_VALIDATION_2026-08-06.md),
but its final production-policy and release-evidence sections remain
incomplete. It is therefore not current release proof. The measurements below
predate the production layer-surface profile and are retained as a historical
Legacy-modal/mode-selection baseline; they are not current Layerwise results.

Balanced remains the desktop's shared preset and the m-index fields remain in
the cache/report identity for compatibility. They do not select or add modes
for a terminal-complete Layerwise solve. Its bounded frequency policy allows
three refinement iterations with at most 64 new points per iteration. The
existing convergence-report modal slots encode “not applicable” as equal
lower/final indices with exact-zero deltas and a passing analytic invariance
flag; no lower/higher modal matrices are evaluated. Internal numerical status
therefore uses the frequency gate plus that external-input invariance. This is
not a PowerSI accuracy-promotion gate. Legacy and Research continue to use the
actual rectangular modal orders documented for those profiles. PowerSI error
and runtime never select an order.

The modal figures below are a historical pre-v0.22 Legacy/modal-selection
baseline, not the current layer-surface release result. The 2026-07-29 loaded
benchmark changed VTRIP1 maximum magnitude by up to 1.346 dB from m10 to m12,
left 3 of 6 loaded configurations nonconverged, and used 4,139 s of solver
runtime. More modes worsened external correlation in that historical run, but
that does not justify selecting a lower modal order. PowerSI is comparison-only,
never a calibration input.

Adaptive evaluation reports frequency-grid and modal deltas, including RMS,
maximum dB difference, and dominant-peak shift. For the validation comparison,
VINT0 signed/RMS/max magnitude error changed from
`+2.438/+2.598/+3.723 dB` to `+0.358/+0.841/+2.007 dB`; RMS/max magnitude
error changed from `401.5/1600.2 uOhm` to `62.1/145.9 uOhm`. VINT1 changed
from `+2.174/+2.385/+3.668 dB` to `+0.081/+0.552/+1.787 dB`, and from
`386.6/1574.9 uOhm` to `43.5/126.1 uOhm`. VINT0 phase RMS/maximum changed
from `5.65/11.03 deg` to `4.93/9.89 deg`. VINT1 phase RMS improved from
`5.19 deg` to `3.72 deg`, while its maximum phase error worsened from
`7.92 deg` to `8.88 deg`. The one-sided VCPU result remains exactly unchanged.

In that historical 2026-07-29 loaded six-configuration benchmark, the mode
6/8/12 loaded PowerSI RMS values were
`3.1874/3.5387/4.0106 dB`; all six loaded configurations were nonconverged at
m6 and m8, and VTRIP0, VTRIP1, and VCPU0 remained nonconverged at m12.
Those numbers remain diagnostic history only. Current internal result status
uses frequency convergence plus terminal-complete external-input invariance for
Layerwise, and combined frequency/modal convergence for Research and Legacy.
The current product-level status is `260729 retrospective FAIL`; unseen/generalization
remains `unknown / not_run` under the canonical baseline. A failed Research/Legacy m12 check must not
silently fall back to a lower order.

The comparison contract is `Z = Z0(I+S)(I-S)^-1`; all non-driven currents are
zero (open), and the selected result is row-major `Zpp`. The tracked,
read-only validator is
[`scripts/validate_powersi_reference.py`](../scripts/validate_powersi_reference.py):

```text
python scripts/validate_powersi_reference.py --scenario <scenario.spdpi> --touchstone <reference.s24p> --rail-port "ADC_VDD_070_VINT/0=5" --output <report.json>
```

The reference is never fed back into the solver. These metrics establish only
numerical stability of this disclosed model.

## W5-GATE approved/machine-frozen contract

This section is the user-approved **machine-frozen policy judgment**. Every
number below is approved policy and is not historical evidence, a current pass/fail
result, or a product sign-off result. No W5 numerical or PowerSI run has been
performed in this document update. The final promotion remains blocked because no
P5 unseen design is registered.

For each rail and frequency sample, define

```text
e_f = 20 log10(|Zm(f)| / |Zr(f)|)                 [dB]
low_offset = abs((e_100k + e_1M) / 2)             [dB]
p_i = principal_angle(Zm(f_i) / Zr(f_i))         [deg]
c_i = abs(Zm(f_i) - Zr(f_i)) * 1e6               [uOhm]
```

The approved low-frequency offset is `low_offset <= 1.0 dB` per rail. The
critical grid is 241 logarithmic points from 100 kHz through 100 MHz. Its phase
RMS is `sqrt(mean(p_i^2))` and phase max is `max(abs(p_i))`; approved ceilings
are RMS `<= 7 deg` and max `<= 15 deg` per rail. Every rail's magnitude max
absolute error is approved as `<= 2.00 dB`.

The design/stratum macro is the **unweighted arithmetic equal-rail mean** of
each rail's 241-point magnitude RMS. Frequency samples, rails, designs, and
strata are never pooled. For 260729, each bare and loaded macro is approved as
`<= 1.00 dB`; for 260804, each bare and loaded macro is approved as `<= 1.25
dB`. These are machine-frozen policy judgments, not historical accuracy evidence.

For the strict applicability class `abs(Zref) < 1e-3 Ohm` only, approved
complex-error ceilings are RMS `<= 100 uOhm` and p95 `<= 200 uOhm`. Zero samples
is explicitly `N/A`; N/A does not enter pass/fail or any macro. Each report must
include applicability and sample counts plus per-stratum applicable/pass/fail/
N/A counts. Other gates still apply.

For resonance, use the same 241-point critical grid, interior local maxima only
(a point must be at least both neighbors; band edges are excluded), and choose
the dominant maximum by magnitude, breaking ties by lowest frequency. If the
reference has no peak, the result is `N/A`, not pass. If a reference peak exists
but the model has none, it is `FAIL`. If both exist, approved frequency error is
`100 * abs(fm/fr - 1) <= 10%` and amplitude error is
`abs(20 log10(|Zm_peak| / |Zr_peak|)) <= 2 dB`. These are approved machine-frozen
policy judgments, not historical accuracy evidence.

The ordered exact 16-rail manifest, used identically by the manifest and each
phase-2 repeated `--rail` flag, is:

```text
ADC_VDD_180_VQPS_OTP_TOP_AON/0
ADC_VDD_180_VQPS_SYS_0_AON/0
ADC_VDD_180_VQPS_SYS_1_AON/0
ADC_VDD_180_VQPS_SYS_2_AON/0
ADC_VDD_180_VQPS_SYS_3_AON/0
ADC_VDD_180_VQPS_OTP_TOP_AON/1
ADC_VDD_180_VQPS_SYS_0_AON/1
ADC_VDD_180_VQPS_SYS_1_AON/1
ADC_VDD_180_VQPS_SYS_2_AON/1
ADC_VDD_180_VQPS_SYS_3_AON/1
ADC_VDD_055_VTRIP/0
ADC_VDD_055_VTRIP/1
ADC_VDD_070_VINT/0
ADC_VDD_070_VINT/1
ADC_VDD_075_VCPU/0
ADC_VDD_075_VCPU/1
```

The first 10 are bare VQPS and the final 6 are loaded VTRIP/VINT/VCPU; these
strata are separate and are never pooled.

### Approved reference partition and artifact registry

`260729` is retrospective development and `260804` is retrospective design
holdout. Both designs use the same exact 16-rail manifest. The registered files
have status `path_exists_size_matches/hash_unverified`; the listed SHA values are
registered values and were **not recomputed in this turn**.

| Design | Artifact | Registered path | Bytes | Registered SHA-256 | Status |
|---|---|---|---:|---|---|
| 260729 | raw SPD | `D:\S4LB002-2Para_260729_1_injected.spd` | 1116717287 | `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2` | path_exists_size_matches/hash_unverified |
| 260729 | S92P | `D:\S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p` | 303974090 | `c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11` | path_exists_size_matches/hash_unverified |
| 260804 | raw SPD | `D:\S4LB002-2Para_260804_1_injected.spd` | 1120159188 | `45253f438fc7c328c50364fe610a7ecfbf72ca842a032921fbbb8d645e2a4f35` | path_exists_size_matches/hash_unverified |
| 260804 | S92P | `D:\S4LB002-2Para_260804_1_injected_080526_104445_27112_S.s92p` | 303902333 | `cd103f42412c2a63518105d7e10fae8a0538c84982e1eddbfb74829a6972951b` | path_exists_size_matches/hash_unverified |

Candidate per design is `absent/must_be_generated`; no candidate hash is
invented. `site0`, `site1`, and `loaded` are exposed strata, not blind samples.
P1 transfer/scale raw and Touchstone artifacts are present, but the holdout is
blocked by the missing 160-port runner and memory-safe preprocessing. P2 raw and
Touchstone artifacts are present, but it is blocked by current weighting,
reference-plane, and de-embedding uncertainty. P3/P4 raw SPD exists at `D:\Downloads` without
`(2)`, but Touchstone is absent at registered/current paths and multifactor /
solver-state confounds remain. P5 is absent and mandatory for final
generalization. W6 can at most produce a non-blind retrospective 260729/260804
baseline until these blockers change.

### W6 manifest contract and completed 260729 evidence

The manifest schema is `powersi-retrospective-run-manifest-v1`. Its approval basis
commit `027ac7a09a3eded15f45c41860945f9d4c7f488d` is historical; runtime uses a
caller/standing-authorization supplied exact clean `main` HEAD. Policy, adapter,
v6 validator, accuracy-validator, and controller hashes are atomically pinned.
W6-BLOCK-B is blocked; W6-BLOCK-C, W6-BLOCK-D, and W6-BLOCK-E are DONE. W6-BASE
completed exactly one 260729 run from clean `main` HEAD
`fb36288781dcc0b884950ef5a486c474090ceebd` in a brand-new output root. The
controller and offline verifier both exited `2`; the manifest was completed and
the numerical score was FAIL.
It also binds app `0.23.0`, solver `modal-mvp-0.8.5`, profile
`layerwise_admittance_v1`, compiler
`layer-surface-adjacent-y-island-finite-via-termination-kron-v8`, full static
compiler SHA `3894d6174dd6765f5f830bc19511cd1bb843f3f88b1b305ceb5be32c52056615`,
and convergence `adaptive-frequency-modal-v5` with iterations `3`, new points
`64`, curvature `0.75 dB`, RMS `0.2 dB`, max `0.5 dB`, peak shift `2%`. It uses
critical 100 kHz–100 MHz/241 log points, diagnostic 1 kHz–1 GHz/481 log points,
compatibility modes 10/12 with ceiling 12, exact 16 rails, strict terminal
reuse/no fallback, registered source/reference basename/bytes/SHA, worker `1`,
`SPD_DECAP_PI_BLAS_THREADS=1`, resolved BLAS limit `1`, and retries `0`.

W6 must record the loaded backend/version and resolved thread count through
`threadpoolctl` and block unless the resolved count is exactly `1`. It must use
an exact command/configuration. For every candidate, import report, correlation
report, v6 stdout/stderr logs, accuracy sidecar, manifest copy, and phase
stdout/stderr logs, the
manifest records basename, bytes, SHA-256, and exit/cancel status. The candidate
hash is created by W6 fresh import and cross-bound between import and correlation
reports. Any cancellation, error, or resource exhaustion is `blocked partial`: no
scoring, W6-BASE controller reuse, or retry. W6-BLOCK-B correlation is historical
evidence. C and D consumed their exactly-one old candidate/import reads. E had one
production diagnostic invocation using candidate/import plus one separate
orchestration ZIP central-directory read, did not use the correlation report, and
wrote only to a fresh root; the old root remains forbidden for W6-BASE/controller/
scoring/retry/mutation.

W6 is controller-only. The caller first invokes the controller with the approved
policy/case and exact current clean `main` HEAD, then verifies the completed
snapshot offline; direct benchmark execution is forbidden. The PowerShell
command templates, tied to the artifact table and new/empty output directory,
are:

```text
$env:SPD_DECAP_PI_BLAS_THREADS = '1'
python scripts/run_powersi_retrospective_baseline.py --policy validation-policies/powersi_accuracy_v1.json --case <260729-or-260804> --expected-head <caller-approved-exact-current-main-HEAD> --out-root <new-empty-output-dir>

python scripts/validate_powersi_accuracy.py --policy validation-policies/powersi_accuracy_v1.json --manifest <new-empty-output-dir>\run_manifest.json --sidecar <new-empty-output-dir>\accuracy_sidecar.json --verify-sidecar
```

The controller reconstructs the exact two internal phase argv arrays using
`benchmark_raw_spd_powersi_correlation_v6.py` for both import-save-only and
correlation; those internal commands are not run directly. The exact 16-rail
configuration, source/reference paths, and manifest are required inputs; output
directories must be new/empty and automatic retry is `0`. These are execution
templates for a consumed W6 one-run gate; any future use is dormant until the user
activates and completes a new exclusive-owner classification item selecting exactly
one source-derived block, then exactly one physical change, focused evidence, and a
new gate. They were used once for 260729
at the exact HEAD recorded below; no retry, resume, reuse, or mutation occurred.
The completed root is
`D:\SPD-Decap-PI-Evaluator-W6\fb36288781dcc0b884950ef5a486c474090ceebd\260729`.
Manifest SHA is `2b14f90e762abc49833518137812e8fc97fcde0e9c145795b7384210cfd9f5de`,
sidecar SHA `0d103e0ad47df80641fac0952a35a6eaa56cc9fdb71be24661903e451926e932`,
and correlation SHA `969e40046e3a099d09557ba7500693460362336d76b067962436bd3b5177abc4`.
Bare macro `1.7071112227372152` and loaded macro `15.910646842123072` both exceeded
the 1.0 dB limit; this is retrospective numerical FAIL, not unseen/generalization
or PowerSI sign-off.

### Hash rotation boundary

The frozen `scripts/validate_correlation_v5.py`,
`scripts/validate_known_case_nonregression.py`,
`validation-policies/known_case_nonregression_v1.json`, and historical
fixtures/results remain unchanged now. They intentionally retain their pre-W4
identities; W6 must not rewrite them. Any future identity or fresh-import binding
schema rotation must be versioned and atomic, and must never rewrite historical
fixtures/results.

## Remaining limits

- The terminal-complete Layerwise result uses exact retained-artwork Maxwell-Y
  and does not add a rectangular nonuniform correction. It is nevertheless a
  quasi-static circuit extraction, not a nonuniform/full-wave plane field solve.
- Terminal paths may use source-proven vertical Via geometry/R/L or a disclosed
  fallback template. Only qualified MLO microvias use solid copper. Trace/Via
  components prove connectivity, but lateral trace impedance, Via mutual
  coupling, anti-pad fields, copper spreading, and plane-sheet nonuniform R/L
  are not inferred by the layer-surface substrate.
- Inter-rail and site-transfer coupling, DC IR drop, and DGND tie impedance
  are not modeled.
- There is no field-solver feedback/calibration and no claim of full-wave
  layer-pair S-parameter extraction.

## Primary references

## W5/W6 closure and execution evidence

W5 policy and implementation closure are machine-frozen and DONE. W6-BLOCK-A is
DONE; W6-BLOCK-B is BLOCKED after a deterministic pivot reproduction,
W6-BLOCK-C is DONE after preserving deterministic factor/matrix context,
W6-BLOCK-D is DONE after recording a sparse raw-system condition lower bound,
and W6-BLOCK-E is DONE after classifying and gating the rejected factor. W6-BASE is
DONE for the completed 260729 numerical FAIL from clean `main` HEAD
`fb36288781dcc0b884950ef5a486c474090ceebd`; offline verification exited `2`
(integrity-valid). Accuracy for unseen/generalization remains `unknown / not_run`;
P5 unseen design is mandatory for generalization/final signoff. The approved basis commit
`027ac7a09a3eded15f45c41860945f9d4c7f488d` is distinct from the caller-supplied
exact current W6 HEAD. Historical v5 validator/policy/fixtures and base benchmark
remain byte-identical. The 260804 S92P SHA trailing `b` is a frozen registry typo
correction, not a recomputation.

W6 production authority is consumed by the completed 260729 numerical FAIL. The prior
W7 frozen-artifact audits are complete and negative/unclassified. The completed
`W7-PHYS-PRODUCTION-VIA-ROUTE-SOURCE-TRACE` source trace classifies ownership as
raw-base/global finite-route ownership in the v4 scenario network;
`local_calibrated_via_half_branches` was not selected and mixed ownership is
fail-closed. This is source-proven with high confidence, not certificate R/L or
PowerSI accuracy evidence. Active work is now NONE. A future production rerun requires the
user to activate and complete a new exclusive-owner classification item selecting
exactly one source-derived block, then exactly one physical change, focused evidence,
and a new gate. Remote/release/installer, retries, old-root W6-BASE reuse or mutation,
and threshold, fallback, reordering, port, or physics changes remain unauthorized. C and D already consumed their
exactly-one old candidate/import reads. E had one production candidate/import
invocation plus one separate orchestration ZIP read, did not use the correlation
report, and wrote only to a brand-new E root. W6-BASE is now complete for 260729
numerical FAIL; W7-PHYS-AUDIT-MOUNTED-PATH is DONE negative/unclassified and W7-PHYS
is BLOCKED because no exclusive owner was classified. At that prior closure, the active
item was NONE; the current read-only owner-classification item is recorded separately.
260804/P5 and additional production reruns remain forbidden while the development case is FAIL.
The local bounded V3 is a mirror of the required CI selection; remote CI was not run.

The B diagnostic root
`D:\SPD-Decap-PI-Evaluator-W6-Diagnostics\6bbe44e2f36610755103757d6a4502c9ed9760d3\260729-vtrip0-1khz`
ran only `ADC_VDD_055_VTRIP/0` at 1000 Hz and exited 1 after reproducing
`2.543e17 > 1e13`. Its stdout SHA is
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`, stderr SHA is
`cb0d72713189a6fb71f6622afc80128fdc435917f63b8a27032981e3e732623a`, and no report
or scored artifact exists. The existing exception lacked factor context, so root cause
remains unclassified. C added only deterministic context to that existing fail-closed
exception; solver results, thresholds, ordering, cache, ports, physics, and trust
identities are unchanged. D's exactly-once diagnostic is consumed; no retry or full
W6 run is authorized until that new user-selected owner-classification,
physical-change, focused-evidence, and new-gate sequence closes.

W6-BLOCK-A supplies missing `terminal_complete_external_input=True` only in the
versioned layerwise diagnostic/correlation adapter, preserves explicit values and
restores the hook; import passthrough and legacy/research profiles are unchanged.
V1 was red once and then `1 passed in 0.88s`; V2 was `10 passed in 2.13s`. The
current bounded V3 was `361 passed, 1 skipped in 22.88s`, exit 0; the skip is the
unavailable local v0.13 SPD regression bundle at
`tests/test_spd_decap_scenario_io.py:1048`.

The first 260729 controller attempt used source HEAD
`46d17dc73381d4292ea342d7a10e85d4f2e338f6` and immutable root
`D:\SPD-Decap-PI-Evaluator-W6\46d17dc73381d4292ea342d7a10e85d4f2e338f6\260729`.
Its `blocked_partial.json` SHA was
`b81525bd47744dc1ea5c75bb26f20ea354246ad88b8ce5bc9aef131cb50c09f7`: phase1
passed, phase2 exited 2, v6 was not_started, and scoring was refused. The fresh
import took `5078.367847900023s`; candidate was `796205663` bytes with SHA
`8b02836c03aa38c447fba37ddd30434a3e4ed34ce772654fa5bc3a8512543320`; import
report SHA was `e65cae7297b28a36e405074e7135c216c65c483d237973928d9bf63488d3b0b5`.
Phase1 import report recorded frequency solves `0` and Touchstone read `false`.
Phase2 consumed and hashed the registered PowerSI Touchstone and attempted correlation;
all rails were blocked, so no completed comparison or score was produced. Correlation
report SHA was `1fbc44ddb9f9c59254a56fd355096332d2fa4ea091516aa02cec3e9a20368dbb`.

Mode10 executed all 16 selected rails. Twelve rails (10 VQPS and 2 VCPU) hit the
comparison-runner rectangular-plane contract; VTRIP/0,/1 and VINT/0,/1 hit the
separate correct fail-closed pivot gate `2.543e17 > 1e13`. Mode12 executed no
new solves and retained/reused 16 source blockers; release failure count 32 is
not 32 independent solves. No exact parity comparison or numerical accuracy
result exists. The tombstone is bound to old policy SHA
`c362acb01ef28cefbbd1d32753f86bccafbdd53355b42eda83c03a6ea810698b` and must not
be retroactively verified under the new policy; the old root is never written or used
as a W6-BASE controller output, scoring snapshot, retry, or mutation. W6-BLOCK-B
correlation remains historical evidence. C and D opened their candidate and import
report exactly once, read-only, and E had one production candidate/import invocation
plus one separate orchestration ZIP read; E did not use the correlation report. 260804 was not run. In the first blocked
`46d17dc...` root only, no completed manifest, sidecar, or offline verification
exists; the later `fb36288...` W6-BASE root is the completed numerical FAIL above.

W6-BLOCK-C's exact fresh-root evidence is
`D:\SPD-Decap-PI-Evaluator-W6-Diagnostics\e9a1ca124d94f1bd0192d519aa22996e87266ac5\260729-vtrip0-1khz`:
exit 1 after about `667.19s`, no report, stdout SHA
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`, stderr SHA
`8468b8f0798dc754c2c5926cc01dbe26d612b9cba7867efe4fa96f389bf89fc8`. The retained
context was `756888` nodes, `2904010` nonzeros, local magnitudes
`2.467e-13..1.575e+08`, pivots `3.290e-10..8.366e+07`, ratio `2.543e17`,
backward residual `6.808e-20`, and matrix SHA
`709efcd1b8857c1ab9c4f73bec20cc7ec7d144cd9bfd32b9ee1657b956587524`.
W6-BLOCK-D adds only `||A||1` and factor-solve inverse `onenormest(t=1,itmax=5)`
inside this existing pivot failure branch. Its diagnostic root was
`D:\SPD-Decap-PI-Evaluator-W6-Diagnostics\ac828f306da4ca4fa4e4c80c2cb77cadf0d3185f\260729-vtrip0-1khz-cond1`:
exit 1 after `668.63s`, no report, inverse lower bound `3.040e+09`, condition
lower bound `9.572e+17`, stderr SHA
`d40503688889e51afd00504321ca37e032ff6b81fcb800c3bb51d722abab5c58`. A lower
bound above `1e13` proves only that the raw local system is at least that
ill-conditioned; it does not prove actual condition, assembly/topology
correctness, forward accuracy, or PowerSI accuracy.

W6-BLOCK-E uses positive row scaling `S=diag(1/sqrt(row_norm))` and sparse
`Aeq=SAS`, solves `beq=S*b`, maps `x=S*y`, and retains the original-coordinate
`A@x-b` residual gate. The rejected-factor raw inverse lower bound uses
`S*factor.solve(S*x)` and its H equivalent. Pivot ceiling `1e13`, residual `1e-9`,
cache/hash, assembly, ports, and physics are unchanged; solver identity is
`modal-mvp-0.8.5`. V1 analytic red was `1 failed in 0.93s`, corrected green was
`1 passed in 0.72s`; two intermediate source-indentation/syntax collection failures
occurred. The first valid exact V2 was `129 passed, 2 failed in 3.89s`; after the
test-only correction it was `130 passed, 1 failed in 4.63s`. Two command/path typo
attempts collected zero tests and are not validation evidence. The final valid bounded
V2 was `131 passed in 4.13s`, exit 0. This is local structural evidence, not PowerSI
accuracy evidence.

The single E production diagnostic at
`D:\SPD-Decap-PI-Evaluator-W6-Diagnostics\f23c5b241d522d05f50f6de05bd6819723b7d3db\260729-vtrip0-1khz-equilibrated`
completed with status `completed`, exit 0, and elapsed `697.35s`. Report `6464`
bytes/SHA `70e1264e6e4223da9c8715ade5695d02f1ab951aa36b418e5b067a3145fd2e9f`,
stdout `281` bytes/SHA `da71076cad8e6c7308b46bcfc42b8ef0395b430b81cb78dcb905607c4a5d783d`,
stderr `0` bytes/SHA `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Solver/profile/rail/frequency were `modal-mvp-0.8.5` /
`layerwise_admittance_v1` / `ADC_VDD_055_VTRIP/0` / `1000 Hz`; finite admittance
was `[0.13437046955500517, 12.100420788716686]`, scaled pivot
`9.966405069248625e9 <= 1e13`, original residual
`3.919670457452144e-17 <= 1e-9`, with one solve, Touchstone `false`, and adaptive
sweep `false`. Candidate/import bindings matched the immutable inputs. The ZIP
central directory was opened read-only once during orchestration; there was one
production diagnostic invocation and no retry/edit. E is DONE and W6-BASE is DONE
for the 260729 numerical FAIL. These structural/artifact checks are not model-form
correctness, unseen/generalization evidence, or a PowerSI-accuracy PASS/sign-off;
the retrospective 260729 comparison is FAIL.
W7-PHYS-AUDIT-MOUNTED-PATH is DONE negative/unclassified. It was read-only over frozen
artifacts and retained cap/Via/spatial provenance, but did not select an owning term;
W7-PHYS is BLOCKED and no physics code change is authorized.

### W7 mounted-path numerical evidence (negative)

The single audit output was
`D:\SPD-Decap-PI-Evaluator-W7\1af7dd7a1150749a579b55623415ee8abedefda4\260729\mounted_path_audit.json`
(15,796 bytes, SHA-256
`e3c28144b4578c6d17846b70bc345ffab634eb58c9faa698401b9bffcc663ceb`). It returned
exit 2 after 62.98s: `diagnostic_fail`, selected block `null`, causal owner `null`,
owner `unclassified`, six failures, all cap-only first-peak bin mismatches. Cap-only /
candidate first peaks were VTRIP 2.660725/2.440619 MHz (3 bins), VINT
2.660725/2.371374 MHz (4), and VCPU 2.818383/2.585235 MHz (3); the ±1-bin condition
therefore failed. Pair RMS candidate/reference dB was VTRIP 0.004525/0.073046,
VINT 0.011577/0.787786, VCPU 0.041046/2.430263. These are diagnostic/provenance
numbers only, not numeric accuracy or PowerSI evidence; 260729 remains FAIL and
unseen/generalization remains unknown/not_run.

`W7-PHYS-OWNER-TERMINAL-VIA-VS-SPATIAL` is DONE (negative/evidence-unavailable).
The versioned audit commit `5d3846cd85b4b5631440dc7f84f8f865900e7bcd` ran exactly
once from clean `main` against
`D:\SPD-Decap-PI-Evaluator-W7\5d3846cd85b4b5631440dc7f84f8f865900e7bcd\260729`;
it exited 2 after 82.84s with `integrity failure: target Via path evidence is missing`,
created no JSON, and therefore produced no owner/rail/Via/count/RL field. At least one
target landing lacks persisted path evidence; no physics change is authorized.

`W7-PHYS-EVIDENCE-MISSING-PATH-COVERAGE` is DONE (diagnostic-complete, negative/evidence-unavailable),
not a retry. It inspected persisted source-bound terminal
Via all-segment self-R/L and landing geometry after cap-mix control, but a repeated
`estimate_via_segment_rl` calculation is implementation-consistency evidence only.
Disabled-link counterfactuals and pair-RMS-only ownership are invalid; absent persisted
antipad/return-artwork/current-spreading impedance makes those contributions N/A. The
exact read budget is one sequential candidate SHA, one ZIP central/manifest pass, one
streaming `scenario.json` pass extracting decaps/connection_analysis/normalized rails
and stackup, and at most one read each for small reports. Six independent quantitative
signatures are required; otherwise the item exits 2 as negative/unclassified and does
not authorize physics, scoring, or production rerun. The coverage contract reports
8,986 selected decaps across six rails with terminal/unit/Via states
`available`/`missing`/`trace_NA`; only available paths contribute all-segment R/L and
classification, while missing/trace values are N/A. The coverage report schema is v2;
trust conflicts remain no-output;
a normal coverage report is diagnostic-complete exit 2 with owner null/unclassified.
The corrected v2 run from commit `66b2e2d5c39fe24d224544ffb590a87bc6f2a9aa` was
exactly one audit in
`D:\SPD-Decap-PI-Evaluator-W7\66b2e2d5c39fe24d224544ffb590a87bc6f2a9aa\260729`:
82.08s, exit 2, JSON 527477 bytes, SHA-256
`2816958e48d3713d179d7420834ad9ff97b177ba661848f1646def104c867a14`.
Coverage was available 0, missing 19,218, trace_NA 0, total 19,218, with state
classification complete true and coverage complete false. This is path-coverage
evidence only; it creates no accuracy PASS/FAIL and no owner.

The completed `W7-PHYS-PRODUCTION-VIA-ROUTE-SOURCE-TRACE` used only the listed 11
source modules and provenance definitions, with no artifact reread, numeric
recomputation, test/solver/production execution, physics change, or owner promotion.
The statically bound chain is v6 → base benchmark → `_build_bound_layerwise_source_model`
→ v4-only termination factory → required v4 certificate → scenario network retaining
base `finite_parallel_rl` links plus cap-only termination manifest. Local calibrated
half-branches were not selected; mixed ownership is rejected. The subsequent 17E
six-file source V0 and one Sol review are DONE as `producer unclassified (high
confidence)`: adapter reachability fields are persisted; reduced-conductor count is
source-Via parallel count without invented R/L; finite-route reduction is exact series
`fsum` with equivalent count 1; finite-via and compiled-asset layers validate and
persist precomputed R/L; isolated PEEC is diagnostic-only and not production-bound in
the allowed files. The production formula/unit conversion and runtime producer branch
remain unknown. Accuracy and causal-owner claims remain out of scope. The next
candidate `W7-PHYS-GROUND-REACHABILITY-RL-PRODUCER-TRACE` is BLOCKED pending a new
whitelist authority for `src/spd_decap_pi/_core/io/spd.py`; it is not active.

Frozen historical v5 validator/policy/fixtures and base benchmark remain byte-identical.
W6-E atomically rotated the current v6→policy→accuracy-validator→controller trust
chain for solver `modal-mvp-0.8.5`; after the result commit, listed current identities
and the exact Git HEAD are frozen together. Current trust identities are base
`d43b868629464f408ea19362daa78fc369d2fd446cf3d458cfdc044ccbf57f08`, adapter
`6b7e399b4a843028ce754ac9154b8ce1a4575b1581c8d6007cebf26f94e6d440`, v6
`3f26b2aa7880cd9aff89cd5407643c934367764b590db98962cbc30bfa1b04a0`, accuracy
validator `8487be60cad523f9ed2ea1c61c57b580d9c2bb0fee598eea0bb938145b82151e`,
policy `6ea6e0b3327eaf828257334d7bb0211582fcc85ed632468223c7b566d0d3fd4d`,
controller normalized `b7d5b87d97e1441ccaa950a1fbe50a49f599483e68acee99596eda7dd612262d`.

- [D. M. Pozar, *Microwave Engineering*, cavity and parallel-plate foundations](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119770580)
- [Zhang et al., multilayer microwave-network cascade, IEEE TEMC (2010)](https://doi.org/10.1109/TEMC.2010.2040389)
- [Ren et al., frequency-dependent Via self/mutual inductance, ISEMC (2009)](https://doi.org/10.1109/ISEMC.2009.5284628)
