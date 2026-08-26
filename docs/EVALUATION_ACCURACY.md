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
PowerSI accuracy evidence. At the 17D closure active work was NONE. The first corrected
successor remains BLOCKED after its static REJECT; successor-2 is DONE and current active
work is NONE. `W7-PHYS-W6-MULTISEGMENT-EXPOSURE` is now DONE as source-classified evidence
unavailable/actual exposure unknown; the prospective runtime-term evidence candidate is
BLOCKED/YAGNI. A future production rerun requires the
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
remain unknown. Accuracy and causal-owner claims remain out of scope. The completed
17F one-file V0 and Sol review classify the producer as delegated/unclassified (high
confidence): `spd.py` assembles geometry/provenance and delegates R/L to
`src/spd_decap_pi/_core/via_model.py::estimate_via_segment_rl` with length, drill,
material, layer endpoints, and stackup; returns are Ω/H and exceptions become
incomplete/None. Local series terms use count 1 and local R/L/length sums; edges use
parallel_path_count=1 and per_path/raw count equal to path length. A multi-segment
chain can be built (`spd.py:7853-7861`) but `finite_physical` passes only
`segments[0].length_um`; no len==1 invariant or complete path-length modeling is
claimed, and this is not called a bug. At 17F closure ACTIVE was NONE; subsequent
approval activated 17G. The completed 17G one-file V0+Sol review bound sigma `5.959e7`,
hollow/solid area policies, R/L equations, fallback, and Ω/H outputs. It confirmed a
conditional caller-contract bug: `spd.py` may construct multiple segments without a
len-one guard, then passes `segments[0].length_um` with full endpoints; W6 exposure is
unknown. This is not an accuracy or causal-owner claim. At 17G closure ACTIVE was NONE;
the consumed evidence boundary is clean `main` HEAD `17836612ceaf09adf83237ec377c845fd1b6ecfd`,
with retained uncommitted diff exactly `src/spd_decap_pi/_core/io/spd.py` and
`tests/test_io_spd.py`; pytest produced no artifact/result root. The original 17H remains
BLOCKED after one focused fixture-contract red:
exit 1, `1 failed in 2.13s` (wall 3.40s), expected two segments but the fixture correctly
produced one `Signal$TOP→Signal$PWR` segment of 220um. The production diff remains statically
accepted and uncommitted. The first static-reject/successor-2 retained-diff base is clean
`main` HEAD `0cbe18e6b7c36dbfd686a14439a164c12f4abccb`, exactly
`src/spd_decap_pi/_core/io/spd.py` and `tests/test_io_spd.py`; zero new pytest executions
and no pytest artifact/result root. The first corrected successor is BLOCKED at static REJECT with
zero pytest executions because its long Via used `DR-0102_60` without GND PadDef and its
long stackup omitted intermediate PWR. `17H-CORRECTED-SUCCESSOR-2` is DONE: the exact node
ran once and passed (`1 passed in 1.08s`, wall~2.07s), with no rerun or artifact/result root.
Commit `7fd8df954791b5a17229b49153d0f5dd57a248d2` contains exactly the production/test files
and the worktree was clean. The narrow behavior evidence is per-segment geometry, fsum tuple
aggregation, no partial aggregate on segment failure, and one contribution per segment; this
does not prove W6 exposure, PowerSI/forward accuracy, causal ownership, artifact/solver/
production validation, or release readiness. The next
`W7-PHYS-W6-MULTISEGMENT-EXPOSURE` is DONE as source-classified evidence unavailable; actual
W6 first-segment versus all-segment exposure remains unknown. The sole prospective evidence
candidate is `W7-PHYS-PROSPECTIVE-RUNTIME-TERM-EVIDENCE`, BLOCKED/YAGNI. The prior
`W7-PHYS-PLANE-SHEET-NONUNIFORM-RL-SOURCE-TRACE` is DONE, using clean `main`
historical HEAD `9d8cb2e5e09c2cef49895cbaee3b23c66d305820` and only
`src/spd_decap_pi/_core/solver/layer_surface_network.py`; one source V0 and one Sol review
were allowed, with all execution/artifact/edit paths at zero. This was an observability
classification, not causality, accuracy, or physics authorization. The accepted 17J table records
topology-only links as ideal vertical-node coalescence with no topology-only numeric stamp;
incoming C[F] is
remapped/collapsed and dielectric-ratio `j2πf·C` is S, finite vertical Via links use
`count/(R+j2πfL)`, and termination returns S. No local plane-sheet series R/L or
nonuniform/current-spreading term was found; Maxwell partial generation remains external/
unclassified. 17K is DONE: local adjacent-gap dispersive admittance is classified by
`Ybulk=j2πf Σ[(Dk(1−jDf)/εr_nom) C_M]` (Hz/F/dimensionless inputs yield S), while
Maxwell C/dispersion/load/solver insertion remains external/unclassified. Grounding/Schur/
modal(0,0) replacement and numerical gates are reduction/validation, not extra physics; arbitrary
caller loads remain external/unclassified. 17L is DONE: exact 2D polygon-overlay lumped parallel-plate
Maxwell C is source-classified as `C=eps0·epsr·A/d` (F) with Laplacian assembly; optional
nonadjacent opening coupling is an opt-in projected-aperture parallel-plate approximation.
There is no frequency/complex-Y, conductivity, sheet R/L, skin, nonuniform in-plane current, or
current-spreading term; W6 input/asset fidelity remains external. 17M is DONE: the canonical rg
ran once (exit 0), found candidate symbols in solver files, but made no reuse or production-binding
proof. 17N is DONE: the surface-impedance constitutive law and MFDM stamp are source-classified,
with one-/two-face coth/csch Ω/square behavior, DC `1/(σt)`, and high-frequency transfer tending
to zero; W6 reuse is unclassified/STOP. 17O is DONE: the surface-patch local solver/operator is
source-classified, but whole-solver W6 compatibility reuse is unclassified/STOP. Its ceiling is
polygon clip/mesh and constant-signature strips; scalar σ/t and dielectric provenance; copper helper
+ gap L + dielectric C/loss differential nodal S; common-potential null/differential-only output;
absolute MNA rejected; gates/cache. Terminal/Via/pad/antipad/fringe/full-wave and drop-in W6
compatibility were not proven. 17P is BLOCKED before coding: the high-level decision is an explicit
new `layerwise_surface_patch_v1` replacement profile; the historical profile remains unchanged
with no automatic fallback. The new profile owns polygon C/loss + lateral gap L + sheet impedance,
disables old plane Maxwell C/ideal plane topology, retains finite Via + termination exactly once,
and fails closed on missing evidence; app v0.23.0, exact solver identity pending. 17Q is BLOCKED:
profile/fallback, worker/cache, raw geometry/material provenance, differential nullspace/final
rail-order Zii, and no-double-counting owner transition remain external to its five-file trace.
17R ends as **source-classified BLOCKED**. At clean main HEAD
`8451c4afca57fb46e89b7096026b281ba54bbf52`, the exact three files were each read once and no
other source was read. All five acceptance items were UNPROVEN: the opt-in no-fallback profile
and application enforcement are absent/outside scope; hash-bound plane input without solve-time
raw/full Scenario hydration is not established and the visible adapter retains a
`legacy_rail_template` fallback; balanced `N^H B=0`, stable rail/Zii and nullspace ownership are
delegated; the old Maxwell C/ideal disable seam with exactly-once Via/termination is absent or
delegated; and global OrderedDict/RLock caches leave single-worker/no-shared-mutable-cache
unproven. Sol accepted the fail-closed stop: no fourth read, automatic whitelist expansion,
coding, tests, or execution. User W6 reimplementation authority is recorded but cannot proceed
without an explicit new bounded source-whitelist/contract decision; production runs, old-root
reuse, release, and parent accuracy/causal promotion remain unauthorized. Separately,
17S ends as **source-classified BLOCKED**. At clean main HEAD
`bc0147e5e15b91a77593ad7306b492aae3079d4c`, `services.py` and
`layer_surface_termination.py` were each read completely once. The approved
`src/spd_decap_pi/_core/solver/surface_certificate_asset.py` was missing (`Cannot find path`),
with no search, substitution, or fourth read. Services left profile/application, no-fallback,
replacement, and cache-lifetime seams delegated/unproven, with legacy/whole-outline fallback
visible. Termination proves local Via/termination-once and owner/Kron checks only, not global
`N^H B=0`, stable rail/Zii, or old-plane disable. The missing asset leaves hash-bound solve
payload unproven; all joint acceptance groups are unmet. Sol accepted the fail-closed stop and
ACTIVE is now NONE.
W6 reimplementation authority remains unusable; no automatic path discovery/whitelist expansion,
code, test, artifact, production, old-root, release, or accuracy claim is authorized.
17T ends as **source-classified BLOCKED**. At clean main HEAD
`57aae22531d481623d34b6fb1b68bc0a201702c9`, A/B/C ran exactly once sequentially: A returned
4 lines/2 files, B 29 lines/4 files, C 12 lines/1 file, all within caps. A's only new candidate
was `src/spd_decap_pi/raw_spatial_contact_compiler.py`; B's candidates were prior-read/frozen
`evaluation.py`, `services.py`, `layerwise_network.py` plus re-export-only `solver/__init__.py`;
C found only prior-read `surface_patch_plane.py` definitions/references and no integration caller.
Future candidate budget is A1/B0/C0; symbol matches are not implementation proof. 17U's compiler
trust/resource/determinism/fail-closed behavior is source-proven, but asset assembly, certificate
envelope, complete polygon/material/dielectric provenance, and a raw-SPD/full-Scenario-free solve-time
payload are delegated/unproven; 17U is source-classified BLOCKED. 17V proved only structural
schema/hash/bounds and certificate-envelope checks; full polygons/material/dielectric fields and a
solve-ready payload remain absent, so 17V is source-classified BLOCKED. 17W then ran once at clean main
HEAD `43b244909b4bbb03057666a2451b14f2cce566e5`, returned exactly 2 definition lines in 2 files, and
closed DONE (source-classified); `domain.py` was unread before the query while `spd.py` was prior-17F
evidence and was not reread. 17X is source-classified BLOCKED on an existing-contract gap: same-file
ProjectSpec types prove geometry primitives/assets, `_um`, source SHA/order, stackup thickness/conductivity,
and dielectric frequency epsilon/loss, while adjacent-conductor spacing is only derived by summing
positive intervening dielectric thickness; the existing compiler handoff is absent and frozen 17F
remains unproven. 17Y ran its filename query once with no matches (0 paths, rg exit 1), so it is
source-classified BLOCKED. 17Z then ran its symbol query once at clean HEAD
`4f5d149a1e803caa2a2b78b854b6579cafb3bed4`, returning 17 lines/5 files; the unique compiler test is
`tests/test_raw_spatial_contact_compiler.py`, so 17Z is DONE. 17AA
`W7-PHYS-W6-RAW-SPATIAL-ASSET-V3-EMISSION` is BLOCKED at its test gate. The approved focused
command `pytest -q tests/test_raw_spatial_contact_compiler.py::test_plane_sheet_payload_v3_is_hash_bound_and_v2_remains_unchanged`
ran exactly once: exit 1, `1 failed in 1.55s`; `_plane_sheet_payload` raised
`RAW_SPATIAL_PLANE_SHEET_REQUIRED` with `ProjectSpec stackup physical fields are incomplete` before
the v3 asset. There was no rerun, and this does not classify fixture versus production cause.
The three-path technical diff was atomically committed as
`c7306f2b2633b8d610bb962eb5b64966235afd2f` (3 files, 618 insertions/21 deletions), leaving clean
`main` as the query base. 17AA and 17AA-S1 remain historical BLOCKED failed attempts. 17AA-S2 is
DONE: the approved command
`pytest -q tests/test_raw_spatial_contact_compiler.py::test_plane_sheet_payload_v3_is_hash_bound_and_v2_remains_unchanged`
ran once under S2 with exit 0, `1 passed in 1.09s`; no rerun, broad-suite, or production run.
The v3 emission goal is achieved via S2. 17AB then ran its exact frozen rg once on clean `main`
(exit 0, 26 lines/5 files). The useful unique raw-spatial persistence seam is
`src/spd_decap_pi/spd_adapter.py` (imports both keys, requires the compiled manifest, and
normalizes/sets `RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY`); other matches are definitions/validator,
the sibling compiled builder/persistence in `surface_certificate_asset`, and the compiled reader in
`layerwise_network`. Matches are location evidence only. 17AC is BLOCKED (source-classified STOP)
after its single authorized read of `src/spd_decap_pi/spd_adapter.py` lines 100-215:
`_has_compiled_topology_manifest` lines 114-128 and `_merge_raw_spatial_contact_asset` lines 131-206
prove copy-on-write persistence, exact generated attachment name/type/payload match, casefold collision
rejection, canonical `RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY` preservation, and divergent existing
manifest rejection. v3 semantic validation and compiler/caller ownership remain explicitly unproven/
delegated; absence is not evidence. 17AD is DONE at clean main HEAD
`7537c8ebee9109071db54cc84f5562e375bfc07f`: its exact frozen query ran once, exit 0, with 2 output
lines/1 file. The definition is `src/spd_decap_pi/spd_adapter.py:131` and the exactly one selected
non-definition caller is `src/spd_decap_pi/spd_adapter.py:7868`; location evidence only, no files
opened and no compiler/delegate inference. 17AE is DONE as source-location boundary evidence at clean
main HEAD `c5f3b1a234c8deeffa26b0fa6cf60dabc4d576d9`: exact frozen PowerShell once, exit 0, exactly
2 lines/1 file: `src/spd_decap_pi/spd_adapter.py:6423:def import_spd_scenario(` and
`src/spd_decap_pi/spd_adapter.py:8024:def verify_scenario_source(`. The bracketing owner is
`import_spd_scenario`, but the 6423-8024 span is 1601 lines and not reasonably bounded; no contiguous/
implementation read was made and no compiler/delegate inference is claimed. Sole ACTIVE next is
`W7-PHYS-W6-RAW-SPATIAL-V3-COMPILER-MERGE-SYMBOL-DISCOVERY` (17AF), limited to the exact query
`rg -n --with-filename --no-heading --color never '\b(?:compile_raw_spatial_contact_asset|_merge_raw_spatial_contact_asset)\b' 'src/spd_decap_pi/spd_adapter.py'`;
cap <=10 output lines and exactly 1 file. Ignore import/definition matches; require a non-import/
non-definition compiler occurrence at or before the known non-definition merge call, select nearest
compiler occurrence, and require compiler-to-merge span <=120 lines. Error, over-cap, wrong file,
missing actual call, compiler after merge, or span >120 => STOP. Location evidence only; 17AF opens no
files and any contiguous read requires separate authorization. 17AF is DONE at clean main HEAD
`e4a6704247770cce084fd70fa3d5307f95a3a64a`: exact query once exit0, 4 lines/1 file (compiler import
45, merge definition 131, actual compiler call 7860, known merge call 7868); nearest compiler-to-merge
span 8 lines <=120, location evidence only with no file open or inference beyond placement. 17AG is DONE
(integration-gap evidence) at clean main HEAD `300d5aacc6386ad8c55ce6bd198ab207fb1f4597`: exact read
lines 7848-7885 found guard 7854 `_has_compiled_topology_manifest(base_project)`; compiler tuple
7859-7867 passes source_path, analysis, base_project, scenario_attachments, cancelled but omits
`include_plane_sheet_payload=True`, so frozen default False leaves actual SPD import schema-v2;
7868-7873 pass manifest/generated unchanged to merge and rebind updated project/attachments; 7874-7877
validate updated envelope; 7878-7881 report success/block completion. 7882 onward is unrelated sorting;
`nets=sorted` is endpoint-truncated, so downstream persistence/return remains UNPROVEN/outside with no
extension. Missing flag is an immediate wiring gap, not a safe standalone fix or PowerSI root cause;
unconditional opt-in may violate legacy/default v2 imports. 17AH is DONE (bounded location evidence)
at clean main HEAD `d45a23f62ebd417a50498724bdea79ba77d8d041`: its exact query ran once, exit0, 22
lines/1 file; `import_spd_scenario` import line70 and non-import calls 509, 587, 618, 647, 698, 791,
863, 965, 1207, 1208, 1229, 1249, 1272, 1291, 1409, 1450, 2298; raw metadata key import45 and
checks 1750, 1752, 2487; no compiler/include flag matches. Location evidence only; no test owner was
selected. 17AI is BLOCKED (gate STOP) at clean main HEAD `0b9e75f5367c12e76e8e06edb652f112d518b69d`:
the exact frozen PowerShell ran once, exit2, zero output; no rerun; failure stage unclassified among
silent gates and no cause inferred. 17AJ is DONE as deterministic owner-location evidence at clean
main HEAD `4aaf242dc6da530b600b01e924f4a9c01ab88c01`: exact diagnostic once exit0, exactly four lines
for the expected file; target 1750 bracketed by definition lines 1736-1764 with no frozen 17AH
non-import call; target 2487 bracketed by line 1764 and valid EOF sentinel with frozen call 2298;
exactly one owner selected. 17AK is BLOCKED (source-classified endpoint STOP) at clean main HEAD
`286e939fce53918465de72f8be9b67d3f1844470`: excerpts A lines 2284-2312 and B lines 2468-2505 each
ran once. A proves `import_spd_scenario(source)` line2298 without args/kwargs/opt-in and immediate
status assertions. B proves persisted topology/raw-manifest attachment/source/project/certificate/
topology-hash checks through2499, then `load_raw_spatial_contact_asset` begins2500 and truncates at2505;
schema/version/tables/`require_plane_sheet_payload`/tail remain UNPROVEN; no extension or reread. 17AL
is DONE (source-classified test gap) at clean main HEAD `7618408f8979d07f6810ed1052cb472b8837b222`:
exact tail2500-2540 once, natural EOF2524; loader checks expected source/project/certificate/topology/
geometry hashes but no `require_plane_sheet_payload=True`, only get_via assertions plus rail witness.
Together with known `import_spd_scenario(source)`, the owner is a viable integration seam, but
default-v2 and no-v3-regression claims remain unproven. 17AM is DONE at clean main
`2076ba2764a4ae7614a7fdaa55ee6b208f6fdbad`: exact6423-6465 once, complete
`import_spd_scenario(path: str|Path, *, progress=None, is_cancelled=None) -> ScenarioImport`, safe
kw-only extension point, setup not truncated. 17AN is DONE at clean main
`f178f4a12720a30db6f12e0011c79d9e2346bd54`: 2 files, 4 insertions/1 deletion; adapter kw-only
defaultFalse forwarded, selected test include True + loader require True, Sol static ACCEPT, and the
focused node ran once with exit0 (`1 passed in 1.59s`); no rerun, default callers remain v2, and no
production W6/accuracy claim. 17AO is DONE as location evidence at clean main
`f9ab13c752f7b9c832e69f1d0a16f54fabd38085`: exact query once exit0, 4 lines/2 files; gui/main_window.py
line109 import and line1867 actual call, spd_adapter.py line6423 def and line8070 __all__; ignoring
import/def/string yields exactly one app caller `src/spd_decap_pi/gui/main_window.py:1867`; no production
activation claim. 17AP is DONE at clean main
`d476ce2da4bdb852c8164f2036be07eed8c504b0`: exact once read gui/main_window.py 1838-1895 (58 lines)
completed `_job_import_spd` boundary1859-1877; sole call omits include_plane_sheet_payload => default-v2;
progress scaled to65%, cancellation forwarded, view prep + `_PreparedScenarioImport` return; no local
try/except or product/profile opt-in, upper error handling unproven. No code/test/production/accuracy claim.
17AQ is BLOCKED at clean main `f79927da0f96adc4a60644aa29388965b0dd6df8`: exact authorized rg once exit1
zero output; no rerun; no existing direct `_job_import_spd` test seam; no source/test/code. 17AR is DONE at
clean main commit `fba20767abfbc357f972f77172f6e7f0a5f67772` (source-before `d20eabf267f06799a4f13caa0dce51357eecd4aa`):
exactly 1 file/1 insertion `include_plane_sheet_payload=True,` in the GUI call, Sol static ACCEPT, no tests,
and no solver/production/accuracy claim. 17AS is DONE-negative at clean main
`9dba3076b6648e7aeb874c96fa721997e1629bb3`: the exact authorized query ran once with exit0, 6 lines/1 file
(`raw_spatial_contact_asset.py`); definitions/signatures/guards/exports only, with zero actual
`require_plane_sheet_payload=True` source callers. Producer active/no solver consumer; no rerun/read/code/
test/production/PowerSI/accuracy/causal claim. 17AT is BLOCKED/implementation REJECT after one
frozen-evidence design pass; all six bindings remain UNPROVEN: profile+require owner, hash-bound
evaluation→solver handoff, replacement/no-double-counting owner-off seam, differential/nullspace/gauge/
rail-order mapping, worker/immutable/cache fencing, and minimal file/test/V1/V2 whitelist. 17AU is DONE
(location evidence) at clean main `9f1c95fa0a933445bbb918aff050fcb6238b1e6b`: exact query once exit0,
22 lines/2 files; raw loader/require cluster 2460-2477 unique; evaluation profile resolution 2363 plus
build import 2404/call 2420 one unambiguous cluster; no source range/code/test/claims. 17AV is BLOCKED/STOP:
exact2330-2449 read started mid-signature/function name and ended mid termination exception/comment/downstream;
no extension. 17AW is DONE (location evidence) at clean main `24382198a02c01cb9720b02148784d1b57abf33c`:
exact PowerShell once exit0, exactly 2 lines/1 file, span176<=240. 17AX is BLOCKED/STOP (source-classified
preflight-only): raw-v3 loader/requireTrue, replacement, nullspace/gauge, and rail-order Zii are absent or
delegate-owned; no implementation approval. 17AY is DONE (location evidence) at clean main `ab36421d41146613574fa65538efa0695adb398a`:
exact query once exit0, 2 lines/1 file, span174<=400. The 17AZ owner trace is BLOCKED/STOP
(source-classified delegated substrate owner): exact5432-5605 validated v4 certificate/rail/port/device/
geometry/provenance and returned `LayerwiseUniformSourceModel`, but raw-v3/replacement/nullspace/Zii were
absent and substrate was delegated to `compile_layerwise_substrate`. 17BA is BLOCKED/STOP:
its exact query ran once exit0 with target line3669 and next definition4296, span627>500; no file read.
17BB is BLOCKED/STOP (cache-only multiple clusters): its exact query ran once exit0 with 8 unique
exact-file rows in3669-4295 (3901,3906,3933,3939; 4278,4283,4284,4285), combined span385; raw-v3
loader/require/plane/surface/layer tokens=0 and no source range read. 17BC is BLOCKED/STOP:
exact3669-3945 at clean main `76b550ddbf0301d3c682840d206f312ed60345fc` proved signature/project/
attachments, required_rail_id/progress/is_cancelled, snapshot/cache lookups/asset SHA/cancellation;
raw-v3/require/plane-sheet absent, identity delegated to `_finite_via_substrate_identity` and
`_substrate_identity`, and the endpoint continued a comment/block. 17BD is DONE (location evidence) at
clean main `662de67882c27bafc3420c965bb069da7458b826`: exact query once exit0, 4 lines/1 file, finite
identity3462→3546 span84 and substrate identity3343→3462 span119; repeated3462 is intended adjacency.
17BE is BLOCKED/STOP (raw-v3/hash seam unproven): exact3343-3545 at clean main
`0bea406939f3f84a1000c56d4b74b490cbf3a2cb` completed both helpers; current identity binds source/geometry/
material/blocks/GND/certificate or topology/ports/omissions/compiler/static `layerwise_admittance_v1`, but
raw-v3 manifest/payload/content hash is absent, no profile opt-in arg exists, and canonical/key/static identity
is delegated externally. 17BF docs-only design gate is BLOCKED/design REJECT: frozen evidence cannot name
activation owner/call boundary, require=True v3 loader/attachment/project binding, loader return identity/hash,
kw-only defaultFalse propagation, v2/v3 cache alias/resource contract, or exact whitelist/focused V1. ACTIVE is
NONE; no successor or source/query/code/test/solver/production/release/accuracy execution is authorized. Sole
ACTIVE successor is 17BG `W7-PHYS-W6-RAW-SPATIAL-V3-LOADER-RETURN-CONTRACT-TRACE`: exactly one read of
`raw_spatial_contact_asset.py` lines2460-2558 under standing W6 authority; loader def2460/export2559. Questions
signature/return/error, require=True binding/hash inputs, canonical payload/content identity, manifest-only versus
hydration, cancellation/resource, and no fallback; endpoint/delegated identity => STOP, no expansion or profile/
physical/code/test/solver/production/release/accuracy claim.
Contract: v2 constants/API byte-identical; profile/global/app/solver/convergence/policy/controller
identities unchanged; only the opt-in raw-spatial v3 schema/compiler sibling identity is new. v3 sibling with kw-only opt-in flags, five normalized tables,
no gap table, positive-intervening-thickness fsum spacing, row/coordinate/ProjectSpec/source-SHA digest,
and explicit v3 fail-closed against v2/compiled-only/legacy/missing/tampered payloads. Existing
spool/hash/bounds/cancel/cleanup are reused; Sol reviews the 4-file diff before exactly one focused
`test_plane_sheet_payload_v3_is_hash_bound_and_v2_remains_unchanged` node, with failure stopping rerun.
No accuracy/causal/production claim follows.
The compact scenario quotient
stores only schema/status, compiled SQLite stores aggregate `finite_parallel_rl` links/count/
R/L/owners without source series terms, segments, or `physical_model_status`, and the
production-complete surface certificate uses a compiled-only stub without the raw canonical
certificate. Import/correlation/manifest data retain identities/counts, not in-memory
retained/suppressed/retarget rows, so candidate rereading cannot classify the old execution.
This turn's evidence budget was D: root/candidate path+size metadata enumeration only; candidate
outer hash, ZIP central/manifest, `scenario.json`, small reports, prior audit JSON, and Python/
test/solver/production execution were all zero. No exposure, affected-term/rail count, R/L
delta, W6 cause, accuracy, causal owner, or fix-benefit claim is made. The sole next candidate,
`W7-PHYS-PROSPECTIVE-RUNTIME-TERM-EVIDENCE`, is BLOCKED/YAGNI and would require an independently
selected grounded physical change, new candidate/HEAD/root, and one no-retry term digest/count/
owner-partition run.

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
