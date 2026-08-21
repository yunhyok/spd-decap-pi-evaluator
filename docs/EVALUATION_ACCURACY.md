# Evaluation Accuracy and Modeling Boundary

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

The v0.22.0 release evidence for the production layer-surface profile is kept
in the [two-case validation record](EVALUATION_LAYER_SURFACE_VALIDATION_2026-08-06.md).
The measurements below predate that profile and are retained as the historical
Legacy-modal/mode-selection baseline; they are not v0.22 layer-surface results.

Balanced remains the desktop's shared preset and the m-index fields remain in
the cache/report identity for compatibility. They do not select or add modes
for a terminal-complete Layerwise solve. Its bounded frequency policy allows
three refinement iterations with at most 64 new points per iteration. The
existing convergence-report modal slots encode “not applicable” as equal
lower/final indices with exact-zero deltas and a passing analytic invariance
flag; no lower/higher modal matrices are evaluated. Acceptance therefore uses
the frequency gate plus that external-input invariance. Legacy and Research
continue to use the actual rectangular modal orders documented for those
profiles. PowerSI error and runtime never select an order.

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
Those numbers remain diagnostic history only. Current release acceptance uses
frequency convergence plus terminal-complete external-input invariance for
Layerwise, and combined frequency/modal convergence for Research and Legacy.
A failed Research/Legacy m12 check must not silently fall back to a lower order.

The comparison contract is `Z = Z0(I+S)(I-S)^-1`; all non-driven currents are
zero (open), and the selected result is row-major `Zpp`. The tracked,
read-only validator is
[`scripts/validate_powersi_reference.py`](../scripts/validate_powersi_reference.py):

```text
python scripts/validate_powersi_reference.py --scenario <scenario.spdpi> --touchstone <reference.s24p> --rail-port "ADC_VDD_070_VINT/0=5" --output <report.json>
```

The reference is never fed back into the solver. These metrics establish only
numerical stability of this disclosed model.

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
