# v0.22.0 Layer-Surface Evaluation Validation — 2026-08-06

This record binds the promoted Evaluation Analysis algorithm to the two raw
SPD/PowerSI cases supplied for this release. PowerSI Touchstone is used only as
an external comparison. It is never read by the source-model compiler and no
R, L, C, material, geometry, or correction coefficient is fitted from it.

## Release identity

| Item | Value |
| --- | --- |
| Application | `SPD Decap PI Evaluator v0.22.0` |
| Solver | `modal-mvp-0.8.3` |
| Profile | `layerwise_admittance_v1` / `LAYERWISE` |
| Compiler algorithm/version | `layer-surface-adjacent-y-island-finite-via-termination-kron-v8` |
| Static compiler identity SHA-256 | `3894d6174dd6765f5f830bc19511cd1bb843f3f88b1b305ceb5be32c52056615` |
| Desktop preset under test | `Balanced` shared run identity; terminal-complete Layerwise stamps no rectangular correction modes |
| Convergence policy | `adaptive-frequency-modal-v4` schema with terminal-complete external-input invariance: three frequency-refinement iterations, at most 64 new points per iteration, 0.75 dB curvature trigger, RMS/max/peak-shift gates 0.2 dB / 0.5 dB / 2%; legacy modal slots are N/A/zero for this profile |

## Named inputs

| Case | Raw SPD | PowerSI Touchstone | SHA-256 |
| --- | --- | --- | --- |
| 260729 | `S4LB002-2Para_260729_1_injected.spd` | `S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p` | SPD `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2`; S92P `c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11` |
| 260804 | `S4LB002-2Para_260804_1_injected.spd` | `S4LB002-2Para_260804_1_injected_080526_104445_27112_S.s92p` | SPD `45253f438fc7c328c50364fe610a7ecfbf72ca842a032921fbbb8d645e2a4f35`; S92P `cd103f42412c2a63518105d7e10fae8a0538c84982e1eddbfb74829a6972951b` |

Both references are 92-port `# Hz S RI R 1` files with one DC and 825 positive
frequency records. The report's open-port comparison contract is
`Z = Z0 (I + S) (I - S)^-1`, with the selected diagonal `Zpp` used for each
canonical rail/port mapping.

## Implemented multilayer merge

Every retained physical `(layer, NET)` artwork surface is a separate node. For
each adjacent conductor gap `k`, the ordered add/subtract artwork produces a
multi-NET Maxwell block. Its dielectric dispersion produces `Ygap,k(f)`, and a
Boolean embedding maps it into the global node ordering:

```text
Yglobal(f) = sum_k transpose(Ek) * Ygap,k(f) * Ek + Yvia(f)
```

Exact raw-SPD same-NET Trace/Via connected components prove only whether every
retained artwork island of one physical `(layer, NET)` may coalesce. Every
island must be coordinate-bound and all islands must belong to one source graph
component; missing or split proofs fail closed. Cross-layer component records
are disclosure only: they never create an ideal short. `Yvia(f)` contains only
finite parallel R/L links from positive, ownership-resolved substrate Via
populations after exact decap and complete Device incident-Via owners are
subtracted. All internal surface unknowns are then eliminated in one sparse
open-port solve:

```text
Yreduced * v = b
Zport = transpose(b) * v
Yport = 1 / Zport
```

This realizes the intended layer-by-layer analysis and merge without assuming
that the board is a simple two-port chain. Direct multiplication such as
`S(T-La) * S(La-Lb) * S(Lb-Lc)` is valid only after compatible wave-port/chain
bases and internal terminations are defined. Shared planes, branches, floating
copper, Vias, and shunt decaps instead require common interface unknowns and
joint elimination (or an equivalent Redheffer-star construction).

The terminal-complete global-Y result is read at the external differential
Device port and is the sole Layerwise Zii input. The rectangular modal matrix
is not prepared, modal C00 is not double-stamped, and no legacy higher-mode
one-port difference is added. The release remains a source-derived quasi-static
circuit model rather than a full-wave layer-pair S-parameter solver.

## Source-topology gates

- 92 active rails compiled to 22,722 Device branches with 22,722 unique PWR
  anchors and 19,952 unique GND anchors (42,674 unique anchors total).
- All 92 rails use the exact configured `DGND` reference.
- The ten no-decap `VQPS` rails contain 30 Device branches and all 30 terminal
  anchors have complete surface-contact evidence.
- Twelve anchors have no directly incident first Via, but each reaches retained
  artwork through a source-observed Trace-first same-NET component. Absence of
  a direct first Via remains visible in provenance; it is not treated as a
  geometry failure when the full component contact is proven.
- Gap-isolated retained surfaces are indexed as zero-capacitance rows. They
  remain independent across layers unless an ownership-safe finite Via link
  connects them; the compiler synthesizes no missing adjacent-gap capacitance,
  ideal cross-layer shortcut, fringing, or Trace R/L.
- Exact asset/source/compiler hashes, branch anchor IDs, source nodes, contact
  surfaces, island-equivalence proofs, and finite Via owner counts are
  fail-closed before solving.

## Comparison scope and gates

Each case uses 16 rails:

- five site-0 `VQPS` rails as the development control group;
- five site-1 `VQPS` rails as a held-out no-decap control group;
- site 0/1 `VTRIP`, `VINT`, and `VCPU` as six loaded rails.

A report is valid only when all 16 rails complete, all rail outcomes carry the
release solver/profile/compiler/source/convergence-policy identity, and the raw
SPD and candidate source SHA/size agree. Every sampled layer-surface network
must pass the fail-closed finite, reciprocity, row-sum, passive-admittance, and
`< 1e-9` internal residual gates. Frequency convergence is measured normally.
The established report's modal fields are retained for schema compatibility
but encode terminal-complete external-input invariance (equal indices, zero
deltas, pass), not a rectangular higher-mode sweep. A GUI batch is accepted
only when frequency convergence and that invariance flag are true for every
Original and Tuned result.

## Historical pre-policy 0.8.0 hybrid mode-8 diagnostic — not release evidence

These reports used the superseded one-iteration/32-point frequency budget and
fixed m8 ceiling. They completed all predeclared rails and proved that the
source/profile/compiler path ran, but neither case passed the required 16/16
combined convergence gate.

| Case | Complete | Frequency pass | Modal pass | Combined pass | Loaded modal RMS delta range | Loaded modal max delta range | Max modal residual | Runtime | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 260729 | 16/16 | 15/16 | 10/16 | 9/16 | 0.421–0.638 dB | 0.955–1.949 dB | `7.642e-17` | 1,039.8 s | Blocked |
| 260804 | 16/16 | 10/16 | 10/16 | 4/16 | 0.541–1.050 dB | 1.336–2.582 dB | `8.870e-17` | 1,082.5 s | Blocked |

Diagnostic reports:

- `validation-output\v0.22.0-release-v4-260729-mode8-r1\correlation_report.json`
- `validation-output\v0.22.0-release-v4-260804-mode8-r1\correlation_report.json`

## Historical 0.8.1 hybrid loaded higher-order diagnostic

The intended mode-10/mode-12 runs under the superseded 0.8.1 hybrid policy did
not finish and are not release evidence. In each case, mode 10 completed only
`ADC_VDD_055_VTRIP/0` and `/1`, then stopped after starting
`ADC_VDD_070_VINT/0`; mode 12 was never reached and no
`correlation_report.json` was produced. The retained stdout logs are:

- `validation-output\v0.22.0-solver081-logs\260729.stdout.log`
- `validation-output\v0.22.0-solver081-logs\260804.stdout.log`

These partial runs must not be substituted for the final terminal-complete
production-policy evidence below.

## Final production-policy validation

TO_FILL_FINAL_POLICY_RESULTS

### Known-case non-regression release gate

The final reports must first pass the tracked v5 convergence, provenance, and
terminal-complete invariance validator. Each report must then pass
`known-case-nonregression-policy-v1`. That policy compares every persisted
rail metric and each equal-rail group macro mean with the worse of the frozen
mode-6 and mode-8 historical values for the same named case. The four frozen
reports are tracked under
`validation-fixtures/known-case-nonregression-v1` and are verified by exact
size and SHA-256 before their thresholds are recomputed.

The gate is a known-case catastrophe/regression floor, not an absolute PowerSI
accuracy specification. It does not authenticate the controlled r4 generator
cryptographically and does not recompute impedance curves or metrics from raw
Touchstone data. A release requires both the tracked v5 result and a canonical,
verified `known-case-nonregression-result-v1` sidecar for each case. The Windows
release workflow fails before building an installer if either tracked r4 report
is missing, untracked, invalid, or above a frozen threshold.

## Same-mode diagnostic comparison

Before the terminal-complete release identity was frozen, mode-6 comparison
runs used the superseded hybrid layer-surface/modal composition and completed
all 16 rails in both cases. They are retained as diagnostic, not
release-identity, evidence:

| Case/group | Layer-surface mean rail magnitude RMS | Prior Legacy mean | Change |
| --- | ---: | ---: | ---: |
| 260729 VQPS development | 1.431 dB | 4.735 dB | 69.8% lower |
| 260729 VQPS holdout | 1.748 dB | 4.802 dB | 63.6% lower |
| 260804 VQPS development | 2.277 dB | 4.581 dB | 50.3% lower |
| 260804 VQPS holdout | 2.948 dB | 4.640 dB | 36.5% lower |
| 260804 loaded | 2.568 dB | 6.974 dB, incomplete earlier layerwise path | 63.2% lower |

The 260729 loaded mean changed from 2.583 dB under Legacy to 2.979 dB under
mode-6 layer-surface (15.3% higher). This is disclosed rather than hidden.
Source-faithful topology, the large blinded VQPS gains, and previously blocked
loaded-rail completion in the untouched 260804 case are supporting evidence,
but they do not override internal convergence. Promotion remains blocked until
the final production-policy reports pass every release gate; no uniform
improvement is claimed for every loaded rail.

## UI, packaging, and publication

TO_FILL_RELEASE_EVIDENCE

## Modeling boundary

- Exact artwork is used for the terminal-complete adjacent-gap Maxwell-Y
  network; no rectangular nonuniform one-port correction is added. The model
  remains quasi-static rather than a nonuniform/full-wave plane field solve.
- Trace/Via components prove connectivity, but lateral Trace impedance, Via
  mutual coupling, anti-pad fields, spreading, plane-sheet nonuniform R/L, and
  full-wave radiation are not inferred.
- The result is single-rail `Zii`; inter-rail/site transfer coupling and DC IR
  drop are not modeled.
- PowerSI remains an independent comparison and final sign-off tool.
