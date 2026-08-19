# Evaluation Accuracy and Modeling Boundary

## Scope

In v0.22.7, strict Evaluation remains the default and source scenarios remain
immutable. Raw SPD import first recovers source-graph connectivity and selects a
deterministic source-proven plane pair; the validated VINT rails use
`L09 (MAIN_POWER1) / L08 (DGND)`, not merely the closest geometric pair. A graph
target must be strictly interior to its retained ordered artwork. The numerical
solver uses the bounded `RECTANGULAR_CAVITY_FINITE_PORT_V1` finite-port basis and
persists `FINAL_TEMPLATE_ARTWORK_CONTAINMENT_V2` provenance. This proves graph
connectivity, not a source-exact electrical model: source-graph connectivity is
source-proven, while nearest-contact reduction, legacy vertical/contact
impedance, and rectangular geometry are LOW confidence and are not
PowerSI/SIwave sign-off.

An unrelated rail with no proven source-graph pair can remain in an imported
project, but Evaluation of that selected rail blocks with
`SOURCE_GRAPH_PLANE_PAIR_UNRESOLVED`; no terminals are silently dropped and no
other rail is degraded. Existing v0.22.6 bundles lack the new source-graph
provenance and require re-import of the matching raw SPD for strict repair. The
old bundle is never silently mutated. Missing assets, hash failures, target
voids/boundaries, and all other blockers remain fail-closed.

SPD Decap PI Evaluator is a pre-design, single-rail `Zii` evaluator. It uses
the imported SPD drawing and does not write a stack-up, plane, or optimization
model. It is not a PowerSI/SIwave replacement, and it has no reference-plane
feedback loop from a field solver into its results.

The v0.11 actual-short cluster handling remains in force: source-established
TOP copper shorts are represented as one physical cluster, rather than being
split into synthetic independent decap branches.

## Rectangular shared-PWR equivalent

The selected PWR/DGND pair establishes the rectangular bbox basis. If the
first conductor on the *opposite* side of that same PWR layer is also a
configured-GND-only layer, with only valid dielectric rows in between, it is
included as one additional return component. No conductor is crossed and a
neighbor containing PWR is not a return component. A general multilayer
cascade is deliberately inactive.

For components `i`, the ideal-common-reference equivalent uses

```text
Ysh = sum_i(j omega epsilon_i / h_i)
Zreturn,i = Rg,i + j omega mu0 h_i
Zsheet = Rp + 1 / sum_i(1 / Zreturn,i)
gamma^2 = -Zsheet Ysh
Zmodal(k) = Zsheet / A / (k^2 - gamma^2)
Zmodal(0,0) = 1 / (A Ysh)
```

`Rp` is one shared PWR sheet resistance, not one copy per cavity. Every
component must therefore have the same PWR thickness and conductivity. DGND
layers are treated as an ideal common reference; their measured tie topology,
slotting, and spreading impedance are not solved.

This is not a general layer-by-layer cascade. A physical cascade requires full
complex multiport Y matrices with explicit shared-interface degrees of freedom,
then eliminates each interface by Schur/Kron reduction. Scalar impedance or
admittance merging is not a cascade. The standalone SPD import does not extract
those section matrices, so the production evaluator remains disconnected from
the `LayerPairNetwork` foundation.

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

Balanced uses m8 (81 modes) as the starting high basis and first compares
`m6↔m8`, then escalates the high basis through `m10`, `m12`, and a bounded m14
ceiling. The evaluator accepts only an adjacent-order convergence pair; RMS
0.2 dB, maximum 0.5 dB, and peak-shift 2% thresholds are unchanged. If m14 is
exhausted, the result remains rejected and reports the compared orders and
actionable ceiling guidance. PowerSI is comparison-only, never a calibration
input.

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

The 2026-07-29 loaded six-configuration benchmark is historical fixed-order
evidence, not the current adaptive contract. Its mode 6/8/12 loaded PowerSI RMS values were
`3.1874/3.5387/4.0106 dB`; all six loaded configurations were nonconverged at
m6 and m8, and VTRIP0, VTRIP1, and VCPU0 remained nonconverged at m12.
It documents why a fixed m12 result was not accepted as a release gate. Current
acceptance requires combined frequency and adaptive modal convergence for every
Original and Tuned rail through the bounded m14 ceiling; it must not silently
fall back to a lower order.

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

- Non-rectangular PWR artwork is a rectangular bounding-box approximation.
- Terminal paths may use source-proven vertical Via geometry/R/L or a disclosed
  fallback template. Only qualified MLO microvias use solid copper; lateral
  trace, mutual Via, anti-pad, and spreading terms are not inferred.
- Inter-rail and site-transfer coupling, DC IR drop, and DGND tie impedance
  are not modeled.
- There is no general layer cascade and no field-solver feedback/calibration.

## Primary references

- [D. M. Pozar, *Microwave Engineering*, cavity and parallel-plate foundations](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119770580)
- [Zhang et al., multilayer microwave-network cascade, IEEE TEMC (2010)](https://doi.org/10.1109/TEMC.2010.2040389)
- [Ren et al., frequency-dependent Via self/mutual inductance, ISEMC (2009)](https://doi.org/10.1109/ISEMC.2009.5284628)
