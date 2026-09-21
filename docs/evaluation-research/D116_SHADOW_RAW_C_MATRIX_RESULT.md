# SPD Decap PI Evaluator v0.23.1 D116-SHADOW — Raw C-Matrix Result

## Disposition

The sealed receipt records **`STOP_D116_SHADOW`**, with the exact stop reason **`unexpected solver diagnostic`**. This is not a PASS and is not an acceptance or promotion result. The historical D115C decision **`STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT`** remains immutable ([D115C review and decision gate](D115C_REVIEW_AND_D116_DECISION_GATE.md)).

The approved runner invocation was:

```text
python tools/research/run_d116_shadow_fastercap.py --approval-sha256 aa85b464e067f72b8c406d77550bbfccb19bb82d4b51e6710bd14a793ae3559b
```

The frozen solver `argv` was exactly:

```text
D:\SPD-Decap-PI-Evaluator-W7\831000e8d5e874d4ec155f0a6dda5cc93b68125c\260729-d107t-fastercap-filtered-extraction-01\payload\app\FasterCap\FasterCap.exe -b D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\input\d116_shadow_gap_coupon.lst -a0.01 -mc3 -d1 -f5 -pj -e -i
```

`numerical_invocations=1`; there was no retry or rerun.

## Solver evidence and the blocking diagnostic

FasterCap stdout contains these two consecutive lines verbatim:

```text
Warning: thin triangles (min angle less than 5 degrees) found in the input file
         This may impact the precision of the result due to numerical rounding errors
```

The warning is a numerical-precision risk, not a harmless informational note. The sealed runner is fail-closed for non-whitelisted solver warnings, so it blocks promotion. Stdout also contains the registry pair `Error : Cannot find FastFieldSolvers settings in the Registry` / `Please try installing the software again`; the sealed receipt records `registry_diagnostic_whitelisted=false` and does not whitelist the nonzero return. The receipt’s exact disposition/reason above is authoritative.

The preregistered expected solver-panel count was **336**, while the solver actually reported **476 input panels to the solver engine**. The final refinement reached **43015 panels** and stdout reported the exact weighted Frobenius value **`0.00405198`**, below the frozen auto threshold `0.01`. These are observations only; the threshold result does not override the STOP.

## Raw matrix (F)

Conductor order and solver-label order are identical: `A_GND_259` → `g1_A_GND_259`, `A_PWR_264` → `g2_A_PWR_264`, `F_DDRL_262` → `g3_F_DDRL_262`.

| | g1_A_GND_259 | g2_A_PWR_264 | g3_F_DDRL_262 |
|---|---:|---:|---:|
| **g1_A_GND_259** | `1.02124e-012` | `-9.15755e-013` | `-3.59125e-014` |
| **g2_A_PWR_264** | `-9.15559e-013` | `1.00756e-012` | `-1.32761e-014` |
| **g3_F_DDRL_262** | `-3.60498e-014` | `-1.38467e-014` | `5.56285e-014` |

The receipt has empty `matrix_stats` and `maxwell_gates` because the run stopped. The following are independent forensic calculations from the sealed final CSV/stdout matrix, using the runner’s `_maxwell` formulas; they are not receipt-certified gates:

| quantity | value |
|---|---:|
| row sums (F), in conductor order | `6.95725e-014`, `7.87249e-014`, `5.732e-015` |
| symmetric eigenminimum (F) | `3.599415977755e-014` |
| reciprocity norm `||C-Cᵀ||F / ||C||F` | `4.524134413621416e-04` |
| absolute reciprocity numerator `||C-Cᵀ||F` (for audit) | `8.750447417133e-16` |
| scale (F) | `1.02124e-012` |
| tau = `1e-6 * scale` (F) | `1.02124e-018` |

## Resource, process, and return evidence

| field | sealed value |
|---|---:|
| receipt `wall_seconds` | `15.629685640335083` |
| UTC interval | `2026-09-03T02:14:10Z` → `2026-09-03T02:14:26Z` |
| FasterCap stdout total time | `15.048000s` |
| memory samples | `33` |
| peak working set | `281341952` bytes |
| peak/final scratch | `177798` bytes |
| maximum active Job processes | `1` |
| final/last active Job processes | `0` |
| descendants | `descendants_ok=true` |
| job drained | `true` |
| job close | attempted and succeeded; no close error/backstop |
| final job-active error | `null` |
| termination/taskkill | `termination_attempted=false`; taskkill/TerminateJobObject fields `null`; `primary_termination_confirmed=false`, `tree_termination_confirmed=false`, `termination_confirmed=false` (expected/not applicable because normal execution drained the Job from active `1` to `0`; these are not successful-termination claims) |
| post-close wait/backstop | `post_close_wait_attempted=false`; `job_close_backstop_used=false`; `backstop_used=false` |
| return code | `7340129` |
| registry handling | diagnostic present; `registry_diagnostic_whitelisted=false`, `nonzero_return_whitelisted=false` |

## Artifact ledger

All paths below are the sealed evidence paths (sizes are bytes; SHA-256 is lowercase).

| artifact | path | size | SHA-256 |
|---|---|---:|---|
| receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\d116_shadow_fastercap_receipt.json` | 7594 | `476569f97dfddef128b05339eb4cc37d130b03c60f93896a83e32eb624b9b365` |
| receipt SHA sidecar | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\d116_shadow_fastercap_receipt.json.sha256` | 101 | `35c25336f3c7db61c2c82705965a9c6759a9ab4fd6405ea73cce040f4073b49e` (sidecar records receipt SHA `476569f97dfddef128b05339eb4cc37d130b03c60f93896a83e32eb624b9b365`) |
| stdout | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\input\stdout.txt` | 40956 | `07da7101334b2490ef11568b758cba02b3ea916b534fed56af928d6a27687949` |
| stderr | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\input\stderr.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| raw CSV | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\input\d116_shadow_gap_coupon.csv` | 132 | `ad60d7e0a03f8a0324324611b38908390ebb926ad3d4618e0b4da69ecf06eeca` |
| invocation token | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\invocation_token.json` | 1909 | `d698f9e26698e75da5d1e6a60213e7062e65bbfc4d990dcca26d2e1da979694d` |
| copied input receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\input\d116_shadow_gap_coupon_receipt.json` | 5480 | `5a43383a24083e8acffe3b78066e65e475bdadba264f176422ac47066a22c06c` |
| approval manifest | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\evidence\tools\research\d116_shadow_fastercap_approval_manifest.json` | 777 | `aa85b464e067f72b8c406d77550bbfccb19bb82d4b51e6710bd14a793ae3559b` |
| runner evidence copy | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\evidence\tools\research\run_d116_shadow_fastercap.py` | 50998 | `57ed814a19e8454d7e427f7efa64bb7602b9aaffd186620520547c1f16bbaf16` |
| test evidence copy | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\evidence\tests\test_run_d116_shadow_fastercap.py` | 33043 | `dd6550a761367ed065fa5d7c61472eee2971816df1d912b4276ada25739a80af` |

Copied input identities include `A_GND_259.qui` (32652; `3bc8230edf765a4c5a0c0f8a8b484e249e5d652e2d3c0f9cf3c3ae0f9c78d7b2`), `A_PWR_264.qui` (10449; `22f2119e2f1377c8554a88af89636002f889aa9e4f5f16adf23a4df202677a87`), `F_DDRL_262.qui` (1252; `ab4c920091fc7e5f3d83ac2833ee39c490865bbc0795898cb360643cc871c90e`), and `d116_shadow_gap_coupon.lst` (150; `b6ea0b3d322532f124d9c3da23fca153ed945a1c19cb79f2639af2fff24c39d6`) under the same `...\input\` directory.

The solver identity is FasterCap 6.0.7 at `D:\SPD-Decap-PI-Evaluator-W7\831000e8d5e874d4ec155f0a6dda5cc93b68125c\260729-d107t-fastercap-filtered-extraction-01\payload\app\FasterCap\FasterCap.exe` (7211520 bytes; SHA-256 `02806e5b185d3cf86f9ed95e7e732fdec1bae0fdc9344f064d90550f5861c9b9`). The validated sibling DLL identity is `...\FasterCap\libgomp_64-1.dll` (92160 bytes; SHA-256 `68d15bf2180388c396e01d3ef3a3e5987e04d544e1a2a39e04f51e75f76984c9`); the receipt does not prove that it was loaded.

## Scope and interpretation

This is **D116-SHADOW raw W0 L29/L30 three-conductor coupon only**: `full_domain=false`, `production_oracle=false`, and artificial W0 crop walls. It includes L29/L30 and omits L28/L31; it is a homogeneous, lossless 1 MHz `epsilon_r=3.4` snapshot. The recorded loss tangent `0.0041` was not modeled and no dielectric-loss/conductance claim is made. There is no PowerSI/Zii readiness or comparable Zii, no transform, and no injection. The authoritative minimum physical domain is L28-L31 across all 16 cells with adaptive/global multi-conductor BEM ([product purpose and technical baseline](../PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md), [work execution baseline](../WORK_EXECUTION_BASELINE.md)); this coupon is therefore not the D104 production oracle.

The matrix demonstrates that one sealed FasterCap execution produced a finite 3×3 raw output and a refinement/convergence trace on this artificial coupon. It cannot establish production accuracy, PowerSI correlation, generic four-layer behavior, Distribution compatibility, or a valid acceptance gate; the STOP, warning, 336-versus-476 panel discrepancy, and empty receipt gate fields must remain visible. This is consistent with the separate-state baseline protocol ([`BASELINE_PROTOCOL.md`](BASELINE_PROTOCOL.md)), which does not collapse reference, solve, accuracy, and performance into one PASS state.

The narrow next decision is geometry/contract evidence, not a solver rerun: decide whether the preregistered 336-panel expectation is authoritative when the solver engine reports 476, and specify how the thin-triangle condition is remediated or explicitly accepted. This invocation/output is consumed and must never be rerun. Any future solve would require a new explicitly authorized phase, output directory, and approval anchor; this memo authorizes no retry, rerun, transform, injection, PowerSI comparison, or production solve.
