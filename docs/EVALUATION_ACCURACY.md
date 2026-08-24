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
The current product-level PowerSI accuracy status remains `unknown / not_run`
under the canonical baseline. A failed Research/Legacy m12 check must not
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

## W5-GATE DRAFT contract (not approved)

This section is a **DRAFT policy judgment** awaiting explicit user approval. Every
number below is proposed and is not historical evidence, a current pass/fail
result, or a product sign-off threshold. No W5 numerical or PowerSI run has been
performed in this document update. The final promotion remains blocked because no
P5 unseen design is registered.

For each rail and frequency sample, define

```text
e_f = 20 log10(|Zm(f)| / |Zr(f)|)                 [dB]
low_offset = abs((e_100k + e_1M) / 2)             [dB]
p_i = principal_angle(Zm(f_i) / Zr(f_i))         [deg]
c_i = abs(Zm(f_i) - Zr(f_i)) * 1e6               [uOhm]
```

The proposed low-frequency offset is `low_offset <= 1.0 dB` per rail. The
critical grid is 241 logarithmic points from 100 kHz through 100 MHz. Its phase
RMS is `sqrt(mean(p_i^2))` and phase max is `max(abs(p_i))`; proposed ceilings
are RMS `<= 7 deg` and max `<= 15 deg` per rail. Every rail's magnitude max
absolute error is proposed as `<= 2.00 dB`.

The design/stratum macro is the **unweighted arithmetic equal-rail mean** of
each rail's 241-point magnitude RMS. Frequency samples, rails, designs, and
strata are never pooled. For 260729, each bare and loaded macro is proposed as
`<= 1.00 dB`; for 260804, each bare and loaded macro is proposed as `<= 1.25
dB`. All numbers remain unapproved policy judgments.

For the strict applicability class `abs(Zref) < 1e-3 Ohm` only, proposed
complex-error ceilings are RMS `<= 100 uOhm` and p95 `<= 200 uOhm`. Zero samples
is explicitly `N/A`; N/A does not enter pass/fail or any macro. Each report must
include applicability and sample counts plus per-stratum applicable/pass/fail/
N/A counts. Other gates still apply.

For resonance, use the same 241-point critical grid, interior local maxima only
(a point must be at least both neighbors; band edges are excluded), and choose
the dominant maximum by magnitude, breaking ties by lowest frequency. If the
reference has no peak, the result is `N/A`, not pass. If a reference peak exists
but the model has none, it is `FAIL`. If both exist, proposed frequency error is
`100 * abs(fm/fr - 1) <= 10%` and amplitude error is
`abs(20 log10(|Zm_peak| / |Zr_peak|)) <= 2 dB`. These are unapproved policy
judgments.

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

### DRAFT reference partition and artifact registry

`260729` is retrospective development and `260804` is retrospective design
holdout. Both designs use the same exact 16-rail manifest. The registered files
have status `path_exists_size_matches/hash_unverified`; the listed SHA values are
registered values and were **not recomputed in this turn**.

| Design | Artifact | Registered path | Bytes | Registered SHA-256 | Status |
|---|---|---|---:|---|---|
| 260729 | raw SPD | `D:\S4LB002-2Para_260729_1_injected.spd` | 1116717287 | `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2` | path_exists_size_matches/hash_unverified |
| 260729 | S92P | `D:\S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p` | 303974090 | `c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11` | path_exists_size_matches/hash_unverified |
| 260804 | raw SPD | `D:\S4LB002-2Para_260804_1_injected.spd` | 1120159188 | `45253f438fc7c328c50364fe610a7ecfbf72ca842a032921fbbb8d645e2a4f35` | path_exists_size_matches/hash_unverified |
| 260804 | S92P | `D:\S4LB002-2Para_260804_1_injected_080526_104445_27112_S.s92p` | 303902333 | `cd103f42412c2a63518105d7e10fae8a0538c84982e1eddbfb74829a6972951` | path_exists_size_matches/hash_unverified |

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

### W6 manifest DRAFT

The proposed manifest schema is `powersi-retrospective-run-manifest-v1`. It binds
W5 DRAFT source-before `9ce0d65d4190d55c8abb6ee157bd30f1031f2309`, runtime change
commit `b2608a260a1f952c9b1f6c11d57b71e060ae575c`, and a post-approval W6
run-control commit that is `UNASSIGNED/BLOCKED` until policy rotation is atomic.
It also binds app `0.23.0`, solver `modal-mvp-0.8.4`, profile
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
report, structural/accuracy sidecar, manifest copy, and stdout/stderr log, the
manifest records basename, bytes, SHA-256, and exit/cancel status. The candidate
hash is created by W6 fresh import and cross-bound between import and correlation
reports. Any cancellation, error, or resource exhaustion is `blocked partial`:
no scoring, reuse, or retry without new approval.

Two-phase PowerShell command templates, tied to the artifact table and
new/empty output directories, are:

```text
$env:SPD_DECAP_PI_BLAS_THREADS = '1'
python scripts/benchmark_raw_spd_powersi_correlation.py --spd <registered-raw.spd> --out-dir <new-empty-import-dir> --solver-profile layerwise_admittance_v1 --import-save-only

python scripts/benchmark_raw_spd_powersi_correlation.py --spd <same-registered-raw.spd> --touchstone <registered-reference.s92p> --out-dir <new-empty-correlation-dir> --reuse-candidate <new-empty-import-dir>\<raw-stem>_candidate.spdpi --reuse-candidate-import-report <new-empty-import-dir>\import_save_validation_report.json --solver-profile layerwise_admittance_v1 --modal-max-index 10 --modal-max-index 12 --modal-ceiling-index 12 --require-all-converged --require-terminal-complete-reuse `
  --rail ADC_VDD_180_VQPS_OTP_TOP_AON/0 --rail ADC_VDD_180_VQPS_SYS_0_AON/0 --rail ADC_VDD_180_VQPS_SYS_1_AON/0 --rail ADC_VDD_180_VQPS_SYS_2_AON/0 --rail ADC_VDD_180_VQPS_SYS_3_AON/0 `
  --rail ADC_VDD_180_VQPS_OTP_TOP_AON/1 --rail ADC_VDD_180_VQPS_SYS_0_AON/1 --rail ADC_VDD_180_VQPS_SYS_1_AON/1 --rail ADC_VDD_180_VQPS_SYS_2_AON/1 --rail ADC_VDD_180_VQPS_SYS_3_AON/1 `
  --rail ADC_VDD_055_VTRIP/0 --rail ADC_VDD_055_VTRIP/1 --rail ADC_VDD_070_VINT/0 --rail ADC_VDD_070_VINT/1 --rail ADC_VDD_075_VCPU/0 --rail ADC_VDD_075_VCPU/1
```

The exact 16-rail configuration, source/reference paths, and manifest are
required inputs; output directories must be new/empty and automatic retry is
`0`. These templates are unapproved and were not executed.

### Hash rotation boundary

The frozen `scripts/validate_correlation_v5.py`,
`scripts/validate_known_case_nonregression.py`,
`validation-policies/known_case_nonregression_v1.json`, and historical
fixtures/results remain unchanged now. They intentionally retain their pre-W4
identities and must fail closed against the current runtime until W5 is
approved. After explicit approval, rotate current identity, validator hash,
release identity, new accuracy policy plus hash, fresh-import binding schema, and
offline tests atomically; never rewrite historical fixtures/results.

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

- [D. M. Pozar, *Microwave Engineering*, cavity and parallel-plate foundations](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119770580)
- [Zhang et al., multilayer microwave-network cascade, IEEE TEMC (2010)](https://doi.org/10.1109/TEMC.2010.2040389)
- [Ren et al., frequency-dependent Via self/mutual inductance, ISEMC (2009)](https://doi.org/10.1109/ISEMC.2009.5284628)
