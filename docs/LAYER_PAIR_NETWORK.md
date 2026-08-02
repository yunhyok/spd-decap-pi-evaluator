# Exact Layer-Pair Multiport Foundation

`LayerPairNetwork` represents one adjacent-layer section as a sampled,
complex-symmetric multiport admittance matrix. Its port order is
`top_port_ids + bottom_port_ids`; every current is positive into the section.
It is a numerical foundation only. It is not connected to Evaluation Analysis,
Decap Distribution, the GUI, cache identity, or the current scalar via/rail
templates. The evaluator's v0.13 shared-PWR ideal-common-reference equivalent
is intentionally narrower than this general cascade; see
[Evaluation accuracy](EVALUATION_ACCURACY.md).

For two adjacent sections, partitioned by the common interface `b`,

```text
[ I_a ]   [ Y1_aa  Y1_ab ] [ V_a ]
[ I_b ] = [ Y1_ba  Y1_bb ] [ V_b ]

[ I'_b]   [ Y2_tt  Y2_tb ] [ V_b ]
[ I_c ] = [ Y2_bt  Y2_bb ] [ V_c ]
```

Voltage continuity gives the same `V_b`, and outward-current KCL gives
`I_b + I'_b = 0`. With `Q = Y1_bb + Y2_tt`, eliminating `b` produces

```text
Y_ac = -Y1_ab Q^-1 Y2_tb
Y_ca = -Y2_bt Q^-1 Y1_ba
Y_aa =  Y1_aa - Y1_ab Q^-1 Y1_ba
Y_cc =  Y2_bb - Y2_bt Q^-1 Y2_tb
```

The implementation uses a linear solve for every frequency, never an explicit
inverse. It aligns a shuffled second top-interface ordering by its port IDs
and fails closed for a mismatched port set, unequal frequency grids, duplicate
or overlapping IDs, non-finite/nonreciprocal matrices, and singular or
numerically unstable interface blocks. The latter uses the explicit algebraic
forward-error guard `cond(Q) * eps <= 1e-8`, rather than accepting a nearly
singular interface merely because a solve returns. A sampled
Hermitian-conductance PSD diagnostic evaluates each frequency with
`absolute_tolerance + relative_tolerance * ||G(f)||inf`, and reports one paired
worst frequency/index, eigenvalue, tolerance, and margin. It deliberately
makes no continuous-frequency passivity, causality, or physical-realizability
claim.

## What this does not establish

The original `S4LB002-2Para_260724_1_injected.spd` audit found zero recovered
strictly vertical source paths out of 60,152 requested terminals. A
representative VTRIP L06 region is a shorted trace mesh, not one mandatory
single route: Node821251 branches through `Trace562220` to Node821244 and
`Trace562221` to Node821245; Nodes821244/821245/821250 also connect through
`Trace562216`/`Trace562219`/`Trace562224` toward Via columns
582383..582381 and 582386..582384. `Trace562220` is one representative edge,
with length 0.1 mm and width 0.1 mm. Consequently, exact cascade algebra alone
does not create a physically valid board model or a PowerSI correlation gain.
The source data still lacks an extracted return path and calibrated treatment
of trace/via mutual coupling, anti-pad effects, and via-plate capacitance.

Multiprocessing is intentionally deferred. Independent sampled layer-pair
matrices could later be constructed in parallel, but that requires an
extraction definition and a benchmark showing that serialization and process
startup do not erase the benefit.

## References

- Zhang et al., systematic multilayer microwave-network cascade, IEEE TEMC
  (2010), [DOI 10.1109/TEMC.2010.2040389](https://doi.org/10.1109/TEMC.2010.2040389).
- Ren et al., frequency-dependent self/mutual Via inductance, ISEMC (2009),
  [DOI 10.1109/ISEMC.2009.5284628](https://doi.org/10.1109/ISEMC.2009.5284628).
- Zhang et al., barrel and pad-plate capacitance, APMC (2009),
  [DOI 10.1109/APMC.2009.5385476](https://doi.org/10.1109/APMC.2009.5385476).

The cascade is exact only for the sampled full complex multiport matrices and
the complete shared-interface degrees of freedom supplied to it. It is not a
justification for scalar impedance summation or scalar-Z merging.
