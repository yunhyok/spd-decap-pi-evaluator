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

### W6 manifest contract (READY for a new run; prior 260729 attempt blocked_partial)

The manifest schema is `powersi-retrospective-run-manifest-v1`. Its approval basis
commit `027ac7a09a3eded15f45c41860945f9d4c7f488d` is historical; runtime uses a
caller/standing-authorization supplied exact clean `main` HEAD. Policy, adapter,
v6 validator, accuracy-validator, and controller hashes are atomically pinned.
W6-BLOCK-B must close before the next W6-BASE run.
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
report, v6 stdout/stderr logs, accuracy sidecar, manifest copy, and phase
stdout/stderr logs, the
manifest records basename, bytes, SHA-256, and exit/cancel status. The candidate
hash is created by W6 fresh import and cross-bound between import and correlation
reports. Any cancellation, error, or resource exhaustion is `blocked partial`: no
scoring, W6-BASE controller reuse, or retry. W6-BLOCK-B may inspect the old
candidate, import report, and correlation report read-only as diagnostic inputs,
but all new diagnostic outputs use a separate fresh root.

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
templates for a standing-authorized one-run gate. They were used
once for 260729 at the exact HEAD recorded in the execution evidence below;
that attempt produced `blocked_partial`, so no offline verifier was run and a
new run must use a new output root.

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
DONE; W6-BASE remains READY for a new clean main HEAD and a brand-new output root.
Accuracy remains `unknown / not_run`; P5 unseen design is mandatory for
generalization/final signoff. The approved basis commit
`027ac7a09a3eded15f45c41860945f9d4c7f488d` is distinct from the caller-supplied
exact current W6 HEAD. Historical v5 validator/policy/fixtures and base benchmark
remain byte-identical. The 260804 S92P SHA trailing `b` is a frozen registry typo
correction, not a recomputation.

Standing authorization permits bounded W6 and ranked in-scope local code/tests to
proceed until the Usage Guard stop/checkpoint, with Sol review/Luna writes. It does
not authorize remote/release/installer, retries, old-root W6-BASE reuse or mutation,
or any threshold,
fallback, reordering, port, or physics change. Before W6-BASE, W6-BLOCK-B must
classify the `2.543e17 > 1e13` pivot using immutable old candidate/report read-only
and a separate fresh diagnostic output. The local bounded V3 is a mirror of the
required CI selection; remote CI was not run.

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
as a W6-BASE controller output, scoring snapshot, or retry. W6-BLOCK-B may inspect its
candidate/import/correlation artifacts read-only, with all new diagnostics written to
a separate fresh root. 260804 was not run. No completed manifest, sidecar, or offline
verification exists.

Current trust identities are base
`d43b868629464f408ea19362daa78fc369d2fd446cf3d458cfdc044ccbf57f08`, adapter
`6b7e399b4a843028ce754ac9154b8ce1a4575b1581c8d6007cebf26f94e6d440`, v6
`ae6757057044cdc603106210997a45fa3bcba0238f50089fe2c45d29d6573552`, accuracy
validator `18dd2010b85dd9cf4a355ff6214119ed16fbec3f63f6fe6fa5834ed1bf732caa`,
policy `192bcb127a7ece49d4f7f6ec4d10d7bd0ccc3fbdb3e527290fd6b8ab033d3496`,
controller normalized `3defa5991049e70042b3ac7c7d8243b34d2cab355aef5244b9643d3487550840`.

- [D. M. Pozar, *Microwave Engineering*, cavity and parallel-plate foundations](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119770580)
- [Zhang et al., multilayer microwave-network cascade, IEEE TEMC (2010)](https://doi.org/10.1109/TEMC.2010.2040389)
- [Ren et al., frequency-dependent Via self/mutual inductance, ISEMC (2009)](https://doi.org/10.1109/ISEMC.2009.5284628)
