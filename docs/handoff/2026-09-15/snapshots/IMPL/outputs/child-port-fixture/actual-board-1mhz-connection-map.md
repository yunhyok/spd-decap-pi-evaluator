# SPD Decap PI Evaluator v0.23.1 — actual-board 1 MHz connection map

## Decision

There is **no verified direct insertion path** from the saved rows0/75 `local05` correction into the accepted actual-board operator. The board operator statically condenses L02 RT0 currents into `Y`; its explicit current block contains only 604,031 L25 currents. The small fixture instead makes branch 0 a source electrode, while the board contract fixes branch 0 to exterior zero flux.

## Verified baseline

| Item | Frozen evidence | Verified contract |
|---|---|---|
| Accuracy reference | `C:\Users\User\.codex\worktrees\5950\SPD Decap PI Evaluator\docs\evaluation-research\astra_hybrid_r_gc_1mhz_comparison_2026-09-09.json`, SHA-256 `2049cf1033a94250098987f47ed0e3d18ff2cd0dba61a90b2916e730b897584e` | Port 18, rail `ADC_VDD_075_VTRIP_SRAM/0`, 1 MHz. Board `Zdd=0.0005545735011944134-j0.0005883820472006132 ohm`; PowerSI `0.0006915479466520245-j0.0004257504501139287 ohm`; complex error `26.18268397573785%`. |
| Accepted field | `...\astra-l02-hybrid-right-correction-01\field.npz`, SHA-256 `960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b` | One-amp RHS: `+1` at active potential 2699, `-1` at 2656, gauge 0. `Zdd=v[2699]-v[2656]`. |
| A construction | `...\astra-l02-conditional-hybrid-operator-02\conditional-hybrid-operator.npz`, SHA-256 `5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92`; frozen driver SHA-256 `91e792487f371d14c21e9ce6729f17b34b93386f9f1d0c599302b129d2d755a6` | After gauge removal, the driver applies `A(v,i)=[Yv+Bi, B^T v-Ri]`. `i` is L25-only. A is a `LinearOperator`, not a persisted monolithic matrix. |
| Run scale | Same result/external budget | 3,178,104 potentials, 604,031 currents, 3,782,134 unknowns after gauge; accepted LGMRES `info=0`, residual `9.9914e-10`, solve 12.872 s. Guarded worker 70.093 s, peak private 24,628,375,552 bytes, limits 180 s / 24 GiB. |

## `local05` block and exact basis map

Correction artifact: `C:\Users\User\.codex\worktrees\5950\SPD Decap PI Evaluator\outputs\research\astra-l02-rows0-75-current-cross-handoff-20260912-05\rows0-75-cross-delta.npz`, SHA-256 `3c515d9a24a7abe817d5b25a90e39190caee0a4175463f0f89958093f3c6989d`.

The saved symmetric 17-entry `cross_delta` is indexed by global L02 RT0 branches `[0,74,75,81,749]`. Row 0 uses branches/signs `[81,75,0] / [+,+,+]`; row 75 uses `[749,75,74] / [+,-,-]`. Its contract is `existing point action + existing local self delta + cross delta`, not `cross delta` alone.

| RT0 branch | Board classification | Conditional terminal index | Accepted board current |
|---:|---|---:|---:|
| 0 | exterior, constrained zero flux | none | exactly `0 A` |
| 74 | active internal trace | 1,522,223 | `-4.632973901e-9-j1.080108181e-9 A` |
| 75 | active internal trace, shared by rows 0/75 | 1,522,224 | `5.930569242e-9+j1.379791471e-9 A` |
| 81 | active internal trace | 1,522,229 | `-5.928061976e-9-j1.390193290e-9 A` |
| 749 | active internal trace | 1,522,751 | `1.297616684e-9+j2.995951827e-10 A` |

Those four indices are **trace-potential terminals**, not source-graph native currents and not explicit L02 current unknowns. The current values are a readback from `...\astra-l02-hybrid-right-correction-01\l02-reconstructed-field.npz`, SHA-256 `b715867457410d6e2ba5154143b479f4d0868f263a3d487d5780c2c77c6c6f19`.

## One missing connection and minimum next scope

Missing connection: a board-qualified lifting from the four active RT0 branch currents `[74,75,81,749]` into the accepted condensed L02 `Y`, including the matching board physical point/self magnetic action. Branch 0 must remain zero. No saved artifact supplies that dynamic Schur/KKT connection, so the fixture coefficient cannot be transplanted.

Minimum discriminator: derive the four-current lifting from the saved conditional cell-reconstruction contract, recompute the board-consistent rows0/75 physical magnetic increment with complete support, and add it as either four auxiliary current equations or the algebraically identical rank-4 Schur update. Then run only the existing 1 MHz port-18 RHS and compare `delta Z` with the 26.1826839757% baseline gap. The auxiliary form is about **3,782,138 unknowns** and should use the existing 180 s / 24 GiB guard and warm field. Promoting all 3,095,567 L02 currents would produce about 6,877,701 unknowns and is outside this minimum step.

This is only an actual-board one-point discriminator for the user’s 1 kHz–100 MHz PowerSI-accuracy objective; it is not a broadband or accuracy-improvement claim.
