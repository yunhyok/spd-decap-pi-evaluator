# SPD Decap PI Evaluator v0.23.1 — L14 fixed-current magnetic diagnostic

This is a diagnostic of the accepted 1 A, 1 MHz current field. It does not
replace finite/native inductances, update the field, or predict a finite port
change. The comparison scale supplied by HQ is the existing complex board gap,
0.00021262886699454942 ohm. Its phase is not fitted.

## Actual support and normalization

The source is the frozen `astra-combined-magnetic-source-descriptor-02` NPZ,
SHA-256 `7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02`.
L14 has 214,873 P1 triangles, a constant in-plane sheet current K on each
triangle in A/m, and slab z=[655,675] micrometres. The uniform volume-current
interpretation is J=K/h in A/m², h=20 micrometres. Therefore the integrated
current moment is m=integral(J dV)=A K in A·m. The independent normalization
check is sum(A |K|²/(sigma h)) against the saved triangle Joule contributions,
with sigma=59,590,000 S/m.
The saved P1 field has weak nodal conservation; this diagnostic does not
promote its piecewise constant current to an H(div)-conforming RT0 field.

For a triangle prism V, define the normalized direct coefficient

    S = (A h)^(-2) integral_V integral_V 1/|r-r'| dV dV'  [1/m].

For constant K, its same-triangle terms are

    B_self = 1e-7 A² S (Kx² + Ky²),
    H_self = 1e-7 A² S (|Kx|² + |Ky|²).

They have units H·A², numerically H for the stored unit-current drive. No
additional h² factor belongs here. Finite h remains inside S. The numerical
coefficient multiplying sheet-density products has units H·m².

`astra_prism_covariogram_self.prism_self(...)[0]` supplies S through analytic
depth integration and triangle-overlap quadrature. Only the direct free-space
1/R coefficient is used. The helper's image result is discarded; there is no
dielectric epsilon or reflection coefficient in this static magnetic kernel.

## Computation and coverage

`compute_l14_self.py` groups triangles by their exact sorted squared edge
lengths in source micrometre coordinates, without approximate rounding. There
are 24,609 templates. The area consistency within each template is checked.
Each template is evaluated once and expanded using the actual area and current
of each member. Translation, rotation, reflection and vertex order do not
change this isotropic same-prism scalar coefficient.

Templates are ranked by their current-weighted absolute upper bound. The run
compares orders 32 and 64, then 128/256/512 when required, and qualifies a
coefficient when its last relative change is at most 1e-6. A 240-second
integration budget leaves any uncomputed contribution explicitly bounded.
Successive-order agreement is convergence evidence, not a rigorous quadrature
error enclosure. Unqualified values are not silently included.

For fixed x, planar rearrangement gives integral_T 1/|x-y| dy <= 2 sqrt(pi A).
Since the finite-depth kernel is no larger than the planar kernel,

    0 < S <= 2 sqrt(pi/A),
    |B_self,T| <= H_self,T <= 2e-7 sqrt(pi) A^(3/2) |K|².

Summing that bound over omitted triangles bounds the absolute missing
same-triangle bilinear term without relying on phase cancellation. Multiplying
by omega converts it to the frozen-current ohmic diagnostic. This bound does
not cover quadrature error in computed entries or distinct-triangle terms.
Zero-current templates can remain unevaluated with exactly zero contribution
for this field; their coefficients are stored as NaN, not fabricated zero.

Numerical results and per-template coverage are saved in `l14-self/result.json`,
`l14-self/l14-self-coefficients.npz`, and `l14-self/convergence.json`.

The bounded run completed 22,802 templates and 187,214 triangles in 240 seconds.
There are 15,267 uncomputed triangles with nonzero current and 12,392 with zero
current. The computed population accounts for 99.99993905% of the summed
absolute self upper-bound weight, despite covering 87.13% of triangle count.

| Quantity, normalized to the saved 1 A drive | Computed value |
|---|---:|
| Ordinary B_self | 7.037670793908323e-11 + j2.1434377860205943e-12 H |
| Hermitian H_self | 7.118848617606593e-11 H |
| j omega B_self at 1 MHz | -13.4676168 + j442.1898973 micro-ohm |
| Absolute omitted self bound, multiplied by omega | 3.9120366e-10 ohm |
| Sum of last-refinement absolute indicators, multiplied by omega | 1.3444711e-12 ohm |

The computed magnitude is 2.0806 times the existing board complex-gap
magnitude. This is a scale comparison of one frozen-current contribution,
not the size or direction of a solved correction. The omitted bound is
1.83985e-6 times that gap; the refinement indicator is not an error bound.
`l14-self/readback.json` independently recomputes the aggregate using m=A K.
`l14-self-validation.json` records four actual-shape scale/rotation/permutation
checks and order128-to256 changes no larger than 4.60e-14 relative.

## Attribution and admissible interpretation

Write the source-labelled real symmetric magnetic kernel as G_ab, for layers
a,b in {14,25}. Retaining four real channels (x/y times real/imaginary current)
for each of two source labels permits separate layer actions. Four channels
of mixed sources alone lose that attribution.

    B = m14^T G14,14 m14 + m25^T G25,25 m25
        + 2 m14^T G14,25 m25,
    H = m14^H G14,14 m14 + m25^H G25,25 m25
        + 2 Re(m14^H G14,25 m25).

The transpose bilinear B is complex. j omega B is the appropriate
fixed-current perturbative contribution for the same reciprocal driving port;
it is not generally positive or purely imaginary. The Hermitian H is real and
nonnegative for a complete physical magnetic kernel. A cross term or an
incomplete centroid approximation need not be nonnegative. Neither quantity
is the time-averaged magnetic energy without the phasor convention's energy
factor. No energy factor 1/2 is inserted in the impedance diagnostic.

Check reciprocal cross contractions independently before using factor 2.
Count each same-triangle term once. If the centroid FMM omits its coincident
point diagonal, these finite self terms can be added to that omitted diagonal;
they do not repair distinct-triangle centroid interactions.

Missing full L14 near correction need not block a **PROVISIONAL** source-labelled
centroid layer-cross diagnostic. L14-L25 cross has finite separated supports
and its own pair-quality assessment, which HQ handles. It does prevent calling
the assembled same-layer centroid-plus-self action a converged magnetic
operator. Actual L14 neighboring-pair checks can show sensitivity to finite
support, but a few pairs cannot certify a global near-error bound. Retain the
same-layer and between-layer contributions separately, with an explicit
uncertainty label if their cancellation controls the combined conclusion.

This work can prioritize the sheet-mutual hypothesis. It cannot certify a
full-return model, broadband behavior, mesh convergence, a finite correction
to the board impedance, or improved agreement with PowerSI.

## Readback of HQ's six finite-support cross pairs

Reviewed HQ's `tools/research/check_astra_l14_l25_cross_pairs.py` and saved
`outputs/research/astra-l14-l25-finite-cross-pairs-20260914-01/result.json`.
The constant-L14/affine-L25 volume-current normalization is consistent with the
definition above. In the forward direction, tetra_inner returns
M=integral((y-x)/R dV), giving J25(x) integral(1/R dV)+alpha M/h25. Reversing
source and target correctly preserves the ordinary contraction and conjugates
the Hermitian contraction. The finite and centroid terms use the same units.

The six saved cases refine outer quadrature from 8 to 16 with maximum relative
change 9.3803e-5 and independent reciprocity difference 1.5676e-7. The four
strong pairs have ordinary both-direction imaginary corrections of roughly
-2.5195, -0.8584, -3.7190, and -2.2065 micro-ohm versus their centroid values.
These demonstrate material finite-support corrections in selected pairs;
they give neither a global correction nor a global cross-error certificate.

HQ's real `both_direction_hermitian_delta_ohm` is 2 omega Re(delta H14,25), a
reactive-scale diagnostic. It is not an actual complex port-impedance or loss
change. The ordinary complex `both_direction_ordinary_delta_ohm` includes 2j
omega and already counts both reciprocal directions.

## Six actual L14 same-layer neighbors

The independent `l14-near-review` check selects six disjoint shared-edge pairs
among high current-moment candidates. It reuses the event-partitioned two-prism
overlap helpers, represents constant current exactly through RT0 face fluxes,
and checks both directions, constant-current x/y isotropy, overlap mass,
kernel-one reproduction and extra interpolation nodes. All six pass order8
to16 refinement, with maximum relative change 8.91e-7 and raw reciprocal
difference 1.17e-14.
The worst pair (151692,151694) additionally passed direct non-polynomial
order32-to64 integration: change 9.61e-14 relative and agreement with the
polynomial result 1.67e-11. Its saved check is
`l14-near-review/worst-pair-direct-check.json`.

The selected pairs are (151682,151683), (151675,151676), (151720,151721),
(151747,151750), (151692,151694), and (151695,151696). Their centroid-kernel
errors relative to finite-support values are 63.3%, 66.9%, 31.3%, 23.5%,
361.3%, and 361.2%, respectively. The last two contain long triangles with
centroids much closer than their length; centroid proximity is a poor measure
of their distributed interaction.

The sum for these six pairs only, already including both directions, is

    delta B = -3.413075945606765e-12 - j2.8698397937448113e-13 H,
    j omega delta B = +1.80317 - j21.44499 micro-ohm at 1 MHz.

This is a computable selected-pair correction, not an estimated or bounded
remainder over other neighbors. Do not add it again if a returned action
already includes the same corrections. The same-triangle omitted bound above
does not make the whole same-layer action accurate. Keep the provisional
source-labelled result separated into same-triangle, distinct same-layer,
and between-layer terms. A decision depending on cancellation among them
needs the unqualified near/cross contributions to remain visibly provisional.
