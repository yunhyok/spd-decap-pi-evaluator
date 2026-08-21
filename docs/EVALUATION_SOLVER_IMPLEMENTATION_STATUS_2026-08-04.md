# Evaluation Solver Implementation Status

> Historical v0.18.1 status. v0.22.0 supersedes the production-path statements
> below with the source-derived layer-surface global Schur/Kron profile. See
> `EVALUATION_LAYER_SURFACE_VALIDATION_2026-08-06.md` for the current release
> identity, convergence policy, and evidence.

- Date: 2026-08-04
- Release: v0.18.1 (prerelease)
- Scope: Evaluation Analysis only; De-cap Distribution behavior is unchanged.
- Claim boundary: this document records implemented guards and validation
  evidence. It does not promote an accuracy claim, a sub-milliohm claim, or a
  sign-off claim.

## Release decision

The default and rollback backend is **Legacy modal** (`legacy_modal_v017`). Its
v0.17 physical model and resulting evaluation curve are unchanged in v0.18.1.
It remains the only default product evaluation path.

v0.18.1 exposes **Research: actual-artwork uniform mode**
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
in v0.18.1.

## Loading hardening and parser parity

The v0.18.1 prerelease hardens loading without changing the Legacy modal or
Research solver equations. On the named 1.116 GB raw SPD benchmark, the final
guarded end-to-end import measured 272.536 seconds, rounded to 272.5 seconds.
This is a 53.9% reduction from the 591.6-second baseline. The previously
recorded 210.5-second result was an intermediate measurement before the final
safety guard and is not the release performance claim. Observed peak private
memory decreased from about 4.7 GiB to 2.42 GiB; 2.42 GiB is an observed gate,
not a universal maximum-memory guarantee.

The final guarded raw-SPD phase timings were:

| Phase | Time (s) |
|---|---:|
| Analyze | 91.848 |
| Build import plan | 103.381 |
| Build compact index | 6.090 |
| Recover Via paths | 3.658 |
| Resolve mixed connectivity | 12.685 |
| Recover GND paths | 39.157 |
| Build eligibility | 13.530 |
| Finalize | 1.617 |

The actual `.spdpi` app-equivalent load, including source SHA verification and
prepared-view construction, measured 26.304 seconds for 11,050 decaps and 92
rails, with design fingerprint prefix `2fe31c68a0e7`. The 6.09-second scenario
validation and approximately 8.03-second bundle decode/validation results are
separate component measurements. They must not be reported as complete
app-equivalent load times.

The before/after parser audit tuples remained exactly count-identical: Via
`182,928 / 0 / 182,928`; GND `18,416 / 11,130 / 7,286`; component count
`102,575`. The resource guard is fail-closed: an exceeded or unavailable
resource budget cannot produce a partially trusted loaded result. These are
loading-performance and data-parity results only. They do not change the
Legacy/research profile boundary or establish an evaluation-accuracy gain.

## Installed UI validation

The installed v0.18.1 application opened the named raw SPD and displayed a
visible total of 211.5 seconds. During that operation, 793 `WM_NULL` probes at
a 250 ms cadence recorded zero timeouts, zero `Responding=False` observations,
and a maximum response time of 83.7 ms. Loading completed with 11,050 decaps;
Via provenance reported `0 / 182,928` recovered, with all Via paths using the
fallback. This is a hot installed-UI observation, distinct from the conservative
272.5-second final guarded release benchmark. The observed 3,218.6 MiB peak
private memory occurred while replacing an already-open `.spdpi` and therefore
is not comparable to the fresh-process 2.42 GiB observed gate.

## Validation record

The v0.18.1 prerelease retains the v0.18 legacy comparison against the supplied
PowerSI Touchstone for six rails using the legacy m6 configuration. The stored
local artifact is
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

## Example audit limits for MNA, MFDM, and PEEC

The experimental solver prototypes remain unverified on the named example:

- The conductor topology graph is not electrically ready because 1,016 Trace
  records have no usable width and polygon connectivity is not represented.
- The 1 mm and 2 mm MFDM rasters contain mixed-net cells that the present cell
  model cannot represent, so no certified MFDM solve is available.
- The local filled-microvia PEEC result is a diagnostic only. It is not
  composable with the global MNA until exact-minus-core ownership is verified.
- No prototype has both an end-to-end impedance curve and a blinded loaded
  holdout result. MNA, MFDM, and PEEC therefore are not verified accuracy paths.

## Investigated but excluded from the v0.18.1 prerelease

The source-only uniform `C00` scaffold described above is the shipped,
experimental Research profile. Separate conductor-graph, global MNA,
multilayer finite-difference network (MFDM), artwork-capacitance, via-PEEC, and
related ownership/passivity prototypes were investigated but excluded from the
v0.18.1 prerelease. They are not selected by either released Evaluation
Analysis profile and must not be described as an installed PowerSI-equivalent
path.

The next promotion decision requires a source-faithful topology certificate,
independent analytic/manufactured tests, passivity/reciprocity/conservation
checks, and blinded loaded-complex validation that demonstrates improvement
without hiding a non-converged solve.

v0.18.1 remains a prerelease until those documented accuracy gates are passed.
