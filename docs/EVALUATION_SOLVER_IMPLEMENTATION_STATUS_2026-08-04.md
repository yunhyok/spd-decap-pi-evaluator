# Evaluation Solver Implementation Status

- Date: 2026-08-04
- Release: v0.18.0
- Scope: Evaluation Analysis only; De-cap Distribution behavior is unchanged.
- Claim boundary: this document records implemented guards and validation
  evidence. It does not promote an accuracy claim, a sub-milliohm claim, or a
  sign-off claim.

## Release decision

The default and rollback backend is **Legacy modal** (`legacy_modal_v017`). Its
v0.17 physical model and resulting evaluation curve are unchanged in v0.18.0.
It remains the only default product evaluation path.

v0.18.0 exposes **Research: actual-artwork uniform mode**
(`research_uniform_admittance`) as an explicit, experimental selection. It is
not selected when opening an old scenario, and profile identity participates in
evaluation/cache/result identity so an Original result cannot be compared with
a Tuned result produced by another profile.

Research curves remain transient even after a successful evidence gate. The
Original curve is recomputed for each run and is not persisted to or reused from
the scenario baseline cache. Legacy retains its established baseline-cache
behavior.

## Shipped guarded research bridge

The research profile is deliberately narrow:

1. it replaces only the source-side uniform `C00` modal contribution;
2. it does not add a parallel capacitance contribution and does not alter
   non-uniform legacy modes;
3. it consumes source SPD artwork/stackup/connectivity evidence only;
4. it never reads, fits, or calibrates parameters from PowerSI Touchstone data;
5. it requires a source-hash-bound topology/connectivity certificate, including
   independent PWR-launch and DGND-launch component evidence;
6. missing, unresolved, mixed, or stale evidence produces an actionable
   readiness error. There is no automatic fallback to Legacy modal and no
   partial result curve.

This is a fail-closed scaffold for later validation, not a promotion of a new
production solver.

## Current source limitation

The named SPD whose SHA-256 begins `40cb44b2376f` does not yet contain the
required topology certificate. The research profile therefore blocks before
solving with `TOPOLOGY_CERTIFICATE_MISSING`. This is intentional: accepting
incomplete artwork/connectivity as an actual-artwork model would create a
misleading curve. No evaluation-accuracy improvement is claimed for this SPD
in v0.18.0.

## Validation record

The v0.18 legacy comparison was run against the supplied PowerSI Touchstone for
six rails using the legacy m6 configuration. The stored local artifact is
`.codex/v018_legacy_powersi_validation.json`; it is comparison/regression
evidence only and is not a promotion gate. All six rails reported
`modal_converged=false`, so this comparison is explicitly **not** an accuracy
pass or improvement claim.

| Rail | Complex RMS error (uOhm) | RMS magnitude error (dB) | RMS phase error (deg) |
|---|---:|---:|---:|
| VTRIP0 | 174.0 | 3.426 | 13.53 |
| VINT0 | 408.3 | 2.261 | 6.76 |
| VCPU0 | 851.5 | 2.019 | 9.29 |
| VTRIP1 | 175.3 | 3.474 | 13.78 |
| VINT1 | 387.8 | 2.046 | 6.69 |
| VCPU1 | 2143.7 | 4.889 | 17.11 |

The outer comparison runtime was 101.4 seconds. These numbers are recorded to
prevent an unqualified interpretation of the release as an external-correlation
improvement.

## Investigated but excluded from the v0.18 product release

The source-only uniform `C00` scaffold described above is the shipped,
experimental Research profile. Separate conductor-graph, global MNA,
multilayer finite-difference network (MFDM), artwork-capacitance, via-PEEC, and
related ownership/passivity prototypes were investigated but excluded from the
v0.18 product release. They are not selected by either released Evaluation
Analysis profile and must not be described as an installed PowerSI-equivalent
path.

The next promotion decision requires a source-faithful topology certificate,
independent analytic/manufactured tests, passivity/reciprocity/conservation
checks, and blinded loaded-complex validation that demonstrates improvement
without hiding a non-converged solve.
