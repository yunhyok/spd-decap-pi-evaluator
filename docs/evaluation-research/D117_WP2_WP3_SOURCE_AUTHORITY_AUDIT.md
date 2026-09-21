# SPD Decap PI Evaluator v0.23.1 — D117 WP2/WP3 source-authority audit

## Scope and decision

This is a read-only source-evidence audit. It does not authorize FasterCap,
`-oi`, a numerical D117 run, PowerSI comparison, a transform, or a
Distribution result. Raw/receipt evidence has priority over narrative
documents when wording differs.

**Current status:** Cell258 frozen split-PSLG C0 clearance is
`PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA`; C1 remains
`ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / STOP_C1_TRIANGLE_SINGLE_CALL`. The consumed `-02` and `-03` attempts are immutable STOP evidence; the fresh C0 exact-02 re-seal is current, and the C1 prelaunch cross-binding code/test checkpoint is independently `CODE_ACCEPT` for static/synthetic evidence only. No `-04` approval or run exists; `-03` remains immutable, consumed, and no-rerun.
Conservative overall stage wording is **about 35–40%**: geometry/input-feasibility
Stage2 is materially advanced, while solver and PowerSI correlation remain
unopened.

**WP2 verdict: `PARTIAL`.** The static material/loss slice is accepted as
`static_z_material_loss_slice=PASS`, `face_z_material_rows=SEALED_SOURCE_ORDER_NOMINAL_ONLY`,
and `loss_convention_1mhz=SEALED`. This
seals only receipt-relative source-order rows and the material convention. It
does not seal actual XY/3-D dielectric-interface surfaces, conductor-layer void
fill, reference points or physical panel sides, outer closure, or an absolute
source-z transform. Exactly five `STOP_UNSEALED` blockers remain:
`xy_dielectric_partitions`, `conductor_layer_void_fill`,
`reference_points_and_panel_sides`, `outer_truncation_and_closure`, and
`absolute_source_z_transform`; current evidence does not derive them.
`STOP_NUMERICAL_EXECUTION` remains in force.

**WP3 verdict: `PARTIAL`.** D104 and D115B prove source-bound plane geometry,
rail ownership, terminal provenance, and finite Via/vertex/edge topology. The
accepted one-shot `-05` evidence is recorded below, but it still stops on the
eight-row coverage conflict and does not prove a complete source-derived 3-D
via/barrel/antipad/access-conductor solid set with clearance. Two
pad-containment conflicts remain an explicit acceptance stop.

## C1 synchronization checkpoint (2026-09-06)

The separate Cell258 C1 one-call root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-cell258-c1-single-call-01`
was consumed exactly once and is immutable; it contains exactly three files:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_c1_single_call_hq_approval.json` | **18050 B / `e3ab65812efdd9c71fd927297c3a63759e3d705a57a3e0cf450d6645ed72b026`** |
| `d117_cell258_c1_single_call_attempt_token.json` | **1339 B / `0d50a89dfbf8553c4b5a90089fb09c4abf82ea8c4714eeec6b07f366932df9e2`** |
| `d117_cell258_c1_single_call_controller_receipt.json` | **42767 B / `2a240832d2ab4af3328f22cea677723a6d0b2924b900f1de1ee2fc9042cf16df`** |

The receipt status is `STOP_C1_TRIANGLE_SINGLE_CALL` with reason
`ControllerError: terminal cleanup failed`. The actual child outcome was
`STOP child Job limits do not match approval`: the approved Job limit was
`3435973836 B`, but Windows returned `3435970560 B`, so strict equality failed;
the child therefore stopped at terminal cleanup. There was one launch and no
retry (`attempts=1`, `retries=0`, `launch_count=1`), with no canonical/exact
receipt. Triangle activity is inferred as zero from the pinned pre-import
failure path plus the absent outputs; the receipt field is
`triangle_loaded_modules=null`, and no direct Triangle-call counter is claimed.
Mark `-01`
immutable, consumed, and do not rerun it. This is controller/resource
evidence only and does not alter the C0, WP2, WP3, or numerical STOPs.

The cap correction occurred after `-01` and before `-02`: it aligns the
operational cap exactly to `3435970560 B` while retaining strict equality. The
only post--02 change is the **51930 B** runner/test `_canonical_path` fix. The
current exact suites are **71 passed** for the certifier and **30 passed** for
the one-call preparation (**101 passed total**); all three safe selfchecks
`PASS`. The Win32 create/query regression is a pytest case, not a selfcheck. Sol ACCEPT records the runner /
controller samefile/different-file/shared-role regressions. The historical
post--02 runner is **51930 B /
`592ea02c6a17c63ca6b5b35113726126e6280a717078269084fa76d1cf1136b1`**; the
controller remains **71996 B /
`d6407cae741b1ab06ff2229db086bbb1c49adbf062cc99209c98c4add33faafc`**; and
the single-call test is **27658 B /
`fa827a644a1e2c22d0e8cfda4570b835b11cbd83537a97a51b57f4e6f7105533`**. The
certifier and second focused-test identity are recorded in the [full-domain
gate §3A](D117_FULL_DOMAIN_INPUT_DECISION_GATE.md). The frozen `-02` attempt
used the pre-fix runner **51918 B /
`f827455647aad4e425012cad3e38df538c000c379aecf6e61c78e65667b00c9d`**. The
historical post--02 runner's canonicalization fix is static/synthetic evidence
only, has never received C1 execution evidence, and is not represented,
pinned, or executed by `-02`. The immutable `-02` root below was consumed
exactly once and is not reusable.

### C1 single-call attempt `-02` (consumed immutable STOP)

The immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-c1-single-call-02`
contains exactly three consumed artifacts:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_c1_single_call_hq_approval.json` | **18050 B / `3c62a556ad175a5f8f7514946ab8fe73e530a60b6b22af6a450d7ffd41ffde1f`** |
| `d117_cell258_c1_single_call_attempt_token.json` | **1339 B / `7255dc93c43fb6726277cb5133da6f8d97149c812e8ef656e93e9fd0f4c87008`** |
| `d117_cell258_c1_single_call_controller_receipt.json` | **42731 B / `91f4e74469702caf63c90e214695a246163caf0d2cef23a5497168df1baba4ec`** |

The controller receipt status is `STOP_C1_TRIANGLE_SINGLE_CALL` with
`reason="ControllerError: child marker/line ending mismatch"`; the exact
stderr bytes decoded from receipt `capture[1].prefix` are
`SPD Decap PI Evaluator v0.23.1 STOP_C1_TRIANGLE_SINGLE_CALL: StopError: attempt token approval binding mismatch\r\n`.
Its cleanup object records `cleanup.cleanup_ok=true`, `captures_drained=true`,
`errors=[]`, `terminal=true`, and `terminal_confirmed=true`. Job fields are
`active_process_limit=1`, `active_processes=0`,
`job_memory_limit=3435970560`, `limit_flags=8712`,
`peak_job_memory_used=14716928`, `total_processes=1`, and
`total_terminated_processes=0`; top-level `job_memory_bytes=3435970560` is
separate. No canonical/exact receipt was produced. Triangle activity is
inferred as zero from the pinned pre-import failure path plus the absent
outputs; the receipt field is `triangle_loaded_modules=null`, and no direct
Triangle-call counter is claimed.
Mark `-02` immutable, consumed, and do not rerun it; this is
controller/resource evidence only. The subsequent `-03` attempt and its
terminal STOP receipt are recorded below; no C1/Triangle execution or solver/
FasterCap/PowerSI work is authorized from this consumed attempt.

### C1 single-call attempt `-03` (consumed immutable STOP)

The immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-c1-single-call-03`
was consumed exactly once and contains exactly three files:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_c1_single_call_hq_approval.json` | **18050 B / `142f0ec35f50e754991be59f0edfebcae4ab0ee4ecad5822b3576defec9e9693`** |
| `d117_cell258_c1_single_call_attempt_token.json` | **1339 B / `2387006a6780bc1c26788347ef355786a247de055208443790160102547b4017`** |
| `d117_cell258_c1_single_call_controller_receipt.json` | **42721 B / `62a2f91f09f2e52051d04e33552a4167cd7d07823eabe1a1dc73ac154dc84612`** |

The exact approval seal check passed; the outer invocation exited `2` after
about `1.399 s`, with no retry. Receipt status/overall status is
`STOP_C1_TRIANGLE_SINGLE_CALL`, with top reason
`ControllerError: child marker/line ending mismatch`. Decoded
`capture[1].prefix` is exactly
`SPD Decap PI Evaluator v0.23.1 STOP_C1_TRIANGLE_SINGLE_CALL: StopError: clearance helper identity mismatch\r\n`.
It records `attempts=1`, `launch_count=1`, and `retries=0`; the controller
receipt hierarchy gives top-level `job_memory_bytes=3435970560` separately and
`job.active_process_limit=1`, `job.active_processes=0`,
`job.job_memory_limit=3435970560`, `job.limit_flags=8712`,
`job.peak_job_memory_used=15753216`, `job.total_processes=1`, and
`job.total_terminated_processes=0`. Cleanup fields are
`cleanup.captures_drained=true`, `cleanup.cleanup_ok=true`,
`cleanup.errors=[]`, `cleanup.terminal=true`, and
`cleanup.terminal_confirmed=true`; `final_child_receipt=null` and
`final_output=null`. Triangle activity is inferred as zero from the pinned
pre-import failure path plus the absent outputs; the receipt field is
`triangle_loaded_modules=null`, and no direct Triangle-call counter is claimed.
`triangle_determinism=NOT_EVALUATED` and `triangle_replay=false`; no canonical
output or exact child receipt exists. Mark `-03` immutable, consumed, and do
not rerun it.

Root cause is a helper identity cross-binding failure: path and size match
(`139525 B`), but the sealed clearance helper SHA
`e1bec661639eb51aba7f36435ad0c6c75352ba21c42415c6c6e168764f5d53b3` differs
from current/`-03` C1 SHA
`821aed9ac82138181bc319e77d6e1508c9871a0e51c21e51607d709e51bdc1ec`. A sole
reverse-byte change of `FUTURE_PROCESS_CAP_BYTES` from `3435970560` back to
`3435973836` reproduces the sealed historical helper SHA
`e1bec661639eb51aba7f36435ad0c6c75352ba21c42415c6c6e168764f5d53b3`; do not
revert. Approval
validators missed this cross-binding, and the existing real test is
tautological.

The C0 exact-02 re-seal is recorded below and is complete. The next gate is not
yet created, reviewed, or executed: add a controller prelaunch bundle check and
regressions for current-helper cross-binding, then create a separately approved
new C1 root/attempt. Until then all C1/Triangle and numerical
solver/FasterCap/PowerSI activity remains `STOP`.

### Cell258 C0 exact-production boundary-clearance PASS (exact-02 re-seal, after C1 `-03` STOP)

The fresh exact-one C0 re-seal uses the immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-boundary-clearance-exact-02`.
It follows the C1 `-03` stale-helper STOP recorded immediately above.
The controller receipt records `invocation_elapsed_seconds=19.375 s` and
`process_elapsed_seconds=19.25 s`; the exact receipt records scan
`elapsed_seconds=11.199562800000422 s`. It exited **0** and launched exactly
once. The root contains exactly four regular non-reparse files:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_boundary_clearance_exact_hq_approval.json` | **3568 B / `efc6450d85b34273c0c32bee53afd14479807347ad4e35e82479eebe7ff20370`** |
| `d117_cell258_boundary_clearance_exact_attempt.json` | **833 B / `5d8acc61ad84f4b4d93b62d9d62616d16a8982a957e7ec689b759957e867c10f`** |
| `d117_cell258_boundary_clearance_exact_receipt.json` | **4493 B / `7a8d007e2e9c6331eb7d1a2754dbc826ebfc44d96c279fa2ff645814165d7c6a`** |
| `d117_cell258_boundary_clearance_exact_controller_receipt.json` | **5296 B / `40ca3c34d45dd02f46658e8894a035908fdae8a651ebcedc7e1da6a684559c56`** |

The controller is `PASS` / `accepted_pass`: `attempts=1`, `retries=0`,
`launch_count=1`, and `return_code=0`. Job fields are
`job_memory_limit=1073741824 B (1 GiB)`, `active_process_limit=1`,
`peak_job_memory_used=744714240 B`, `active_processes=0`,
`total_processes=1`, `total_terminated_processes=0`, and `limit_flags=8712`.
The exact receipt is `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA` with
`exact_clearance=true`, `exact_unique_nonincident_pairs=212005`,
`exact_pair_evaluations=212005`, `exact_distance_evaluations=848020`,
`endpoint_distance_evaluations=848020`, `raw_visits=375962`,
`candidate_visit_identity_holds=true`, and `exact_pair_identity_holds=true`.
The current helper
SHA is `821aed9ac82138181bc319e77d6e1508c9871a0e51c21e51607d709e51bdc1ec`;
the current module SHA is
`5747ac2e520b053df4ed30c3a5bbf4235d4bb8bf763532a53ee24589a71d3ef6`.

This PASS is scoped to `frozen_split_pslg_float64` only. Raw-source clearance
was not evaluated; raw-source/global-boundary equivalence remains unknown and
`GLOBAL_BOUNDARY_EQUIVALENCE_STOP` remains in force. Triangle, solver, PowerSI,
and C1 were not run. The prelaunch cross-binding code/test checkpoint below is
independently `CODE_ACCEPT` for static/synthetic evidence only; no `-04`
approval or run exists, so C1 remains `STOP`. The C1 -03 attempt remains immutable, consumed, and no-rerun; this fresh C0 exact-02 supersedes
the stale exact-01 C0 bundle for future planning but does not retroactively make
`-03` pass.

### C1 prelaunch cross-binding CODE_ACCEPT (2026-09-06; code-only)

The independently reviewed prelaunch cross-binding code/test checkpoint is
`CODE_ACCEPT` for static/synthetic evidence only. Its current identities are:

| artifact | bytes / SHA-256 |
|---|---|
| `tools/research/run_d117_triangle_cell258_c1_single_call_exact_once.py` (controller) | **73656 B / `584ac9cf667f1fe9cd12e1467e2f71998e4050b178b9a719d5929ecfd239112d`** |
| `tools/research/d117_triangle_cell258_c1_single_call_runner.py` (runner) | **51930 B / `5cc8e16aa651c4eca172efd6a4e78280bf1dd543ae9969c01c4f6380707b5138`** |
| `tests/test_d117_triangle_cell258_c1_single_call.py` | **32347 B / `cc810c517b0158383b9537481a39a3c5bf3956dd6923067f5281d52a12043101`** |

The independently accepted result is **31 passed**, plus an isolated stale-bundle
no-launch check **1 passed**. Pre-token/pre-Popen checks bind the current-helper,
exact implementation, and controller-receipt path+size+SHA identities. Against
a stale bundle the check returned `exit=2`, `token=absent`, and
`launch_count=0`; the runner retains its independent defense. This is
code/prelaunch evidence only: no C1/Triangle or numerical execution is claimed
or authorized. No `-04` approval or run exists; `-03` remains immutable,
consumed, and no-rerun.

## Evidence ledger (legacy entries re-hashed 2026-09-03; WP2 additions verified 2026-09-05, Asia/Seoul)

| Evidence | Absolute path | Bytes | SHA-256 |
|---|---|---:|---|
| raw SPD | `D:\S4LB002-2Para_260729_1_injected.spd` | 1,116,717,287 | `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2` |
| D103 stackup/material receipt | `D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json` | 204,735 | `4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9` |
| D104 local-window receipt | `D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01\geometry_receipt.json` | 19,728 | `bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b` |
| D115B ownership receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\d115b_source_plane_ownership_materialization_receipt.json` | 514,208 | `c69dce134ca02ff263b75f5df930106a7d8d75d05cccf2acd13b7d9b1919aa49` |
| D115B candidate | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\source_plane_ownership_candidate.spdpi` | 925,278,361 | `1ecc6cbd18a5234178c98c8ead79f684c278daf21bdb64a789296dd6543cd7bc` |
| D115C coverage receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02\d115c_source_local_port_window_receipt.json` | 1,063,056 | `0c8daed46719b199ee50b1ec9b94dd5cac0fdedecbc7b58668b7665b5e9a2a80` |
| D115C audit log | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02\d115c_audit.log` | 67 | `636d33bc3aa0b4efbe9fd5a2873ebd847fb15a85b2dfc7d396817bd9787895e5` |
| WP2 material/loss generator | `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\tools\research\build_d117_wp2_z_material_loss_decision.py` | 16,232 | `126e48378752fa78f15a85d5a0116ba21a13e9713fcb58e46b2efd25c1d6264c` |
| WP2 HQ approval | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp2-z-material-loss-decision-01\d117_wp2_z_material_loss_hq_approval.json` | 6,737 | `485c482bb3b5176409fe9b14ffed9500684bdac4acede0199ef7137375d5cefc` |
| WP2 material/loss receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp2-z-material-loss-decision-01\d117_wp2_z_material_loss_decision_receipt.json` | 11,453 | `ae356a088f0f4954ab3087caaf19ef64ca813b84d568193b888f1f1b2d2316f4` |
| FasterCap C help | `C:\Users\User\AppData\Local\Temp\codex-fastercap-help-d116-20260903\InputFile\3D\InputFile_3D_ch03.htm` | 3,023 | `322c7fdf9d9389f65ad31f08dd8fe43c5d2c6e999156072f3e584c040e8f4c41` |
| FasterCap D help | `C:\Users\User\AppData\Local\Temp\codex-fastercap-help-d116-20260903\InputFile\3D\InputFile_3D_ch04.htm` | 4,309 | `491d6115e8e32273391b935b3ef2861e02bc2ddee0cbe5aab6a0454e06931cdd` |
| FasterCap shell help | `C:\Users\User\AppData\Local\Temp\codex-fastercap-help-d116-20260903\Run\Run_ch02.htm` | 4,689 | `07bda01e29a97920e808d1c73203cf82d609362fa11759537a19946ec25883fd` |
| FasterCap output help | `C:\Users\User\AppData\Local\Temp\codex-fastercap-help-d116-20260903\Output\Output_ch03.htm` | 8,566 | `166ec0b23f62162759ed90755e4a5e006cae35f9285fce8318ea18ecee9d0cd0` |
| FasterCap conductance automation help | `C:\Users\User\AppData\Local\Temp\codex-fastercap-help-d116-20260903\Automation\GetConductanceMethod.htm` | 1,487 | `6270faaf78a0c544d25a1e7dcca1c75708453b63b2338e111dacf6fcbb52cc49` |
| FasterCap original CHM | `D:\SPD-Decap-PI-Evaluator-W7\831000e8d5e874d4ec155f0a6dda5cc93b68125c\260729-d107t-fastercap-filtered-extraction-01\payload\app\FasterCap\FasterCapHelp.chm` | 136,637 | `3f37fa9919eeb13f8ce2a4b16db1a42556f7f04bf0ea49de70affcb074608601` |
| adapter fallback source | `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\src\spd_decap_pi\spd_adapter.py` | 461,503 | `1c0d1488402bd6366739788fb1a75599d94e31cdd7970b9f739bd87ecc2334f3` |

The accepted WP2 receipt records exactly one static attempt, exit `0`, twelve
source-order transitions, and `executed_components=[]`. The generator's
synthetic self-check is `PASS` and its independent Sol review is `ACCEPT`; this
is static documentation evidence only and is not numerical execution or solver
validation.

Cell258 C0 exact-clearance, including the fresh exact-02 re-seal, accepted
static/no-Triangle C1 certifier, and future one-call serializer facts are
maintained in the [full-domain decision gate §3A](D117_FULL_DOMAIN_INPUT_DECISION_GATE.md).
The current C0 `PASS` is frozen split-PSLG-only; the current exact suites are **71
passed** for the certifier and **30 passed** for the one-call preparation
(**101 passed total**), with all three safe selfchecks `PASS` and no Triangle
execution. The Win32 create/query regression is a pytest case, not a selfcheck.
The historical post--02 runner is static/synthetic evidence only and has never
received C1 execution evidence; it is not represented, pinned, or executed by
the reviewed `-02` one-shot. That one-shot is consumed immutable
`STOP_C1_TRIANGLE_SINGLE_CALL` evidence and is not reusable; the consumed
`-03` STOP receipt is recorded above. The prelaunch cross-binding code/test
checkpoint is independently `CODE_ACCEPT` for static/synthetic evidence only;
no `-04` approval or run exists.

The existing parser/provenance path was inspected: `tools/research/extract_source_stackup_material_receipt.py` (10,322 bytes, SHA `2e0d145acdd01cb91d87246aaeaea2ec693127ed7f3678b1222465a75fe6ce70`), `tools/research/extract_source_local_window_geometry.py` (13,648 bytes, SHA `071638997110ba214eae68d36b48c38d7ca7073760e7be61ebe1152b383e2481`), `tools/research/extract_source_plane_fringe_geometry_reuse.py` (15,476 bytes, SHA `6244a7f2b7b642e815331cc1c60e1574bebf13f957547a4d1299d49fbcb2e9`), and `src/spd_decap_pi/raw_spatial_contact_compiler.py` (166,543 bytes, SHA `5b02cc51677feb6bcbe537071c7a13342e2036c0f2d7910103118865c3a45ba2`). The minimum WP3 auditor correction is accepted and bound to `tools/research/audit_source_l29_l30_port_window.py` (60,136 bytes, SHA `0a15b5c17d4fa5f3bb8ae77b1c47f098fc256ab43dfc0cd74333054fd5d2e5ff`) and `tests/test_audit_source_l29_l30_port_window.py` (21,035 bytes, SHA `69e372e5e1d07dda303516bd98febed188d762c99d013eed159fcb5838af5043`). Nine focused synthetic tests passed; this is not production validation. No `accuracy_parse.py` operation was performed.

## Reproducible read-only checks

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath <each ledger path>
Get-Content -Raw <D103 receipt> | ConvertFrom-Json
Get-Content -Raw <D104 receipt> | ConvertFrom-Json
Get-Content -Raw <D115B receipt> | ConvertFrom-Json
Get-Content -Raw <D115C receipt> | ConvertFrom-Json
Get-Content <each FasterCap help file>  # lines 20-30 and 164-167 as cited below
Get-Content -LiteralPath 'C:\Users\User\AppData\Local\Temp\codex-fastercap-help-d116-20260903\Automation\GetConductanceMethod.htm' | Select-Object -First 20
rg -a -n 'Node73753!!19958|Node73624!!20612|Node79348!!19973|Node19552!!20122|Node19553!!20576|Node19551!!20589|Via1362725::|Via1360630::|Via1468557::|Via336278::|Via336299::|Via336259::' D:\S4LB002-2Para_260729_1_injected.spd
rg -a -n -A 6 -B 1 '^\.PadStackDef DR-0102_60' D:\S4LB002-2Para_260729_1_injected.spd
Get-Content -LiteralPath 'C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\src\spd_decap_pi\spd_adapter.py' | Select-Object -Skip 366 -First 83
Get-Content -LiteralPath 'C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\tools\research\audit_source_l29_l30_port_window.py' | Select-Object -Skip 811 -First 19
git ls-remote https://github.com/ediloren/FasterCap.git refs/heads/master
```

The CHM and extracted HTML files were present and rehashed.

The candidate is a ZIP container (read-only `Format-Hex` showed `PK`); its
manifest reports 379 attachments (371 geometry, three capacitor libraries, and
one each under `inputs/`, `ownership/`, `routing/`, `spatial/`, and `topology/`),
`raw_spd_embedded=false`, and scenario/source SHA equal to the raw SPD SHA. A
read-only `zipfile` inspection of
`scenario.json` reported 95 stackup layers, 92 via templates, 44,560 pins,
1,810,200 vias, 72,797 top-via endpoints, 98 padstacks, 371 selected-plane
geometry groups, 847 anchored shared-pad clusters, and 0 unresolved nodes.

## WP2 — material and dielectric-interface authority

### Facts from D103/raw source

D103 is `PASS` and binds the source identity above. Its `derived_depth_basis`
is explicitly **cumulative source-order thickness, not absolute source z**.
Therefore the following coordinates are receipt coordinates; the conflicting
“absolute z” wording in a narrative D117 table must not be used without a
separately sealed offset.

| D103 ordinal | source row | material | kind | thickness | receipt top..bottom (µm) |
|---:|---|---|---|---:|---:|
| 51 | Medium$91 | EL190T | dielectric | 100 | 1680..1780 |
| 52 | L27 DGND | COPPER | conductor | 32 | 1780..1812 |
| 53 | Medium$93 | EL190T | dielectric | 70 | 1812..1882 |
| 54 | L28 DGND | COPPER | conductor | 35 | 1882..1917 |
| 55 | DR2829 | ABF-GL102 | dielectric | 30 | 1917..1947 |
| 56 | L29 DGND | COPPER | conductor | 20 | 1947..1967 |
| 57 | DR2930 | ABF-GL102 | dielectric | 30 | 1967..1997 |
| 58 | L30 OTHER_POWER1 | COPPER | conductor | 20 | 1997..2017 |
| 59 | DR3031 | ABF-GL102 | dielectric | 30 | 2017..2047 |
| 60 | L31 OTHER_POWER2 | COPPER | conductor | 20 | 2047..2067 |
| 61 | DR3132 | ABF-GL102 | dielectric | 30 | 2067..2097 |
| 62 | L32 DGND | COPPER | conductor | 20 | 2097..2117 |
| 63 | DR3233 | ABF-GL102 | dielectric | 30 | 2117..2147 |

The receipt seals these twelve receipt-relative source-order transitions. Each
value is the boundary between the listed preceding and following source rows;
none is an absolute source z or a physical plus/minus side:

| transition ordinals | preceding source row | following source row | receipt-relative transition z (µm) |
|---|---|---|---:|
| 51 → 52 | 51 `Medium$91` | 52 `Signal$L27(DGND)` | 1780 |
| 52 → 53 | 52 `Signal$L27(DGND)` | 53 `Medium$93` | 1812 |
| 53 → 54 | 53 `Medium$93` | 54 `Signal$L28(DGND)` | 1882 |
| 54 → 55 | 54 `Signal$L28(DGND)` | 55 `Medium$DR2829` | 1917 |
| 55 → 56 | 55 `Medium$DR2829` | 56 `Signal$L29(DGND)` | 1947 |
| 56 → 57 | 56 `Signal$L29(DGND)` | 57 `Medium$DR2930` | 1967 |
| 57 → 58 | 57 `Medium$DR2930` | 58 `Signal$L30(OTHER_POWER1)` | 1997 |
| 58 → 59 | 58 `Signal$L30(OTHER_POWER1)` | 59 `Medium$DR3031` | 2017 |
| 59 → 60 | 59 `Medium$DR3031` | 60 `Signal$L31(OTHER_POWER2)` | 2047 |
| 60 → 61 | 60 `Signal$L31(OTHER_POWER2)` | 61 `Medium$DR3132` | 2067 |
| 61 → 62 | 61 `Medium$DR3132` | 62 `Signal$L32(DGND)` | 2097 |
| 62 → 63 | 62 `Signal$L32(DGND)` | 63 `Medium$DR3233` | 2117 |

The eight core L28-L31 transitions are the 1882, 1917, 1947, 1967, 1997,
2017, 2047, and 2067 µm rows above; the adjacent L27/L32 context supplies the
1780, 1812, 2097, and 2117 µm rows. These are source-order nominal rows or z
discontinuities, not sealed actual C/D interfaces, reference sides, or physical
panel sides, and they are not automatically FasterCap `D` panels. D104 supplies
conductor-plane WKB only, not dielectric-solid/interface XY boundaries.

FasterCap `D` panels must be materialized from the actual dielectric/dielectric
XY partitions (including apertures/non-conductor regions and sidewalls) and
their z offsets. The L27/L32 outer faces also require an explicit truncation or
closure decision beyond Medium$91/DR3233; that decision is a separate evidence
gap, not an implicit boundary condition.

At 1 MHz D103 records ABF-GL102 `epsilon_r=3.4`, `tan_delta=0.0041`, and
EL190T `epsilon_r=4.7`, `tan_delta=0.01`; COPPER conductivity is
59,590,000 S/m. These values are source material points, not a completed
FasterCap material card.

### Facts from official FasterCap help

* `C <file> <outperm> ... [+]` accepts complex `outperm` in `ere-j eim`
  form; `+` collates the current and next conductor as one conductor
  (`InputFile_3D_ch03.htm`, lines 20–24).
* `D <file> <outperm> <inperm> ... <xref> <yref> <zref> [-]` is the
  dielectric/dielectric-interface statement: it accepts complex
  `outperm`/`inperm`; the reference point selects the out-permittivity side,
  and `-` reverses it (`InputFile_3D_ch04.htm`, lines 20–27). `C` is the
  conductor-surface statement, not a dielectric interface. FasterCap evaluates
  panels independently and requires the reference point to be on the same side
  for every panel; it has no inferred external/internal topology.
* The help gives no `tan_delta` or conductor-conductivity field in these 3-D
  statements. The sealed 1 MHz convention is
  `exp(+j*omega*t)` with `epsilon*=er*(1-j*tan_delta)` and
  `G=omega*C_lossless*tan_delta>=0`. Thus FasterCap's textual `eRe-j eIm`
  uses the nonnegative magnitudes ABF `eim=0.01394` (`er=3.4`,
  `tan_delta=0.0041`) and EL190T `eim=0.047` (`er=4.7`, `tan_delta=0.01`).
  Copper `59,590,000 S/m` is conductivity only; no FasterCap C/D field is
  inferred. The Automation help (`Automation/GetConductanceMethod.htm`, line
  20) defines the imaginary complex-capacitance array as conductance divided by
  `2*pi*f`. Because the `C`/`D` input statements and shell usage expose no
  solver-frequency argument, `1 MHz` is caller-selected material-point and
  conductance-interpretation metadata, not a FasterCap solver-frequency input.
  This convention does not seal actual C/D interfaces or reference sides, and
  raw complex-matrix sign/reference interpretation remains `STOP`.
* Primary-source corroboration is pinned to FasterCap upstream `master` commit
  `b42179a8fdd25ab42fe45527282b4a738d7e7f87`:
  [repository](https://github.com/ediloren/FasterCap/tree/b42179a8fdd25ab42fe45527282b4a738d7e7f87),
  [SolveCapacitance.cpp](https://github.com/ediloren/FasterCap/blob/b42179a8fdd25ab42fe45527282b4a738d7e7f87/Solver/SolveCapacitance.cpp#L709-L895)
  (real/imaginary output handling), and
  [IFasterCap.idl](https://github.com/ediloren/FasterCap/blob/b42179a8fdd25ab42fe45527282b4a738d7e7f87/IFasterCap.idl#L20-L23)
  (`GetCapacitance`/`GetConductance`). `git ls-remote` verified that commit for
  `refs/heads/master` on 2026-09-03.
* FasterCap's shell contract requires `-b` and an explicit `-a<tolerance>` for
  automatic solving (`Run/Run_ch02.htm`, lines 20–30). This is recorded only
  for a future manifest. Output help confirms that engine-panel count may
  differ after quadrilateral conversion, degenerate removal, or thin-panel
  regularization (`Output/Output_ch03.htm`, lines 161–167).

### WP2 authority gap and minimum contract

The static material/loss slice is complete, but WP2 remains `PARTIAL`. Exactly
these source-authority items remain `STOP_UNSEALED`:

- `xy_dielectric_partitions`
- `conductor_layer_void_fill`
- `reference_points_and_panel_sides`
- `outer_truncation_and_closure`
- `absolute_source_z_transform`

The receipt-relative rows above are nominal source-order evidence only. They do
not seal actual C/D interfaces, reference sides, physical panel sides, or an
absolute z transform. The next bounded work is therefore:

1. materialize source-bound XY conductor `C` surfaces and applicable
   aperture/non-conductor dielectric `D` partitions, including holes, sidewalls,
   extent, z offset, and conductor-layer void fill, with bytes and SHA-256;
2. prove one reference point or per-panel side assignment valid for every
   resulting panel, with no implicit topology assumptions;
3. provide a truncation/closure certificate beyond Medium$91/DR3233; and
4. bind the source-order stack to an approved absolute source-z transform.

### WP2 reconnaissance checkpoint (2026-09-06)

The read-only `WP2-XYD-01` positive-materialization proposal was rejected as
premature: the required source-bound XY dielectric partitions and conductor
layer void-fill evidence are not represented, so it could not close any
authority gap. None of the five blockers is closed; all remain
`STOP_UNSEALED`. The minimal next package is read-only
`WP2-SRC-XY-AUTH-00` explicit-source acquisition, with status
`STOP_NOT_REPRESENTED` until the source partitions, void fill, panel-side
references, closure, and absolute-z binding are present. This reconnaissance
does not authorize a transform, FasterCap, or numerical execution.

The 1 MHz material convention and copper conductivity-only interpretation above
are sealed and must not be re-requested. Raw complex-matrix sign/reference
interpretation remains `STOP`. Until the five named blockers are resolved, a
homogeneous `epsilon_r=3.4` coupon is not source-comparable and no material
claim may be promoted to PowerSI/full-stack equivalence.

## WP3 — via, pad, and access-conductor authority

### Facts establishing what is source-derived/materialized

The raw SPD records all six selected source pins at source endpoint
`Signal$TOP`; all six current opposite-endpoint `source_records`, regardless of
logical net, have layer exactly `Signal$L02(DGND)`. Selected L29/L30 ownership
remains distinct from both endpoint layers. Each selected record is a single finite via edge from
that source endpoint to its lower endpoint; the source does not name a
technology type. These edges span only the adjacent source-to-opposite layer
pair; they do not directly pass through L28–L31.
The following table is the raw Node/Via linkage (coordinates are mm):

| pin | source endpoint | via | opposite endpoint | x, y (mm) | span |
|---|---|---|---|---:|---|
| `SITE0:19958` | `Node73753` (`Signal$TOP`; DGND) | `Via1362725` | `Node2454433` (`Signal$L02(DGND)`) | -11.53190, 12.38250 | `Signal$TOP` → `Signal$L02(DGND)` |
| `SITE0:20612` | `Node73624` (`Signal$TOP`; DGND) | `Via1360630` | `Node2452705` (`Signal$L02(DGND)`) | -11.53190, 12.60770 | `Signal$TOP` → `Signal$L02(DGND)` |
| `SITE0:19973` | `Node79348` (`Signal$TOP`; DGND) | `Via1468557` | `Node2543245` (`Signal$L02(DGND)`) | -11.66190, 12.60770 | `Signal$TOP` → `Signal$L02(DGND)` |
| `SITE0:20122` | `Node19552` (`Signal$TOP`; ADC_VDD_180_VQPS_SYS_1_AON/0) | `Via336278` | `Node2140577` (`Signal$L02(DGND)`; ADC_VDD_180_VQPS_SYS_1_AON/0) | -11.59690, 12.49510 | `Signal$TOP` → `Signal$L02(DGND)` |
| `SITE0:20576` | `Node19553` (`Signal$TOP`; ADC_VDD_180_VQPS_SYS_1_AON/0) | `Via336299` | `Node2140594` (`Signal$L02(DGND)`; ADC_VDD_180_VQPS_SYS_1_AON/0) | -11.46690, 12.49510 | `Signal$TOP` → `Signal$L02(DGND)` |
| `SITE0:20589` | `Node19551` (`Signal$TOP`; ADC_VDD_180_VQPS_SYS_1_AON/0) | `Via336259` | `Node2140562` (`Signal$L02(DGND)`; ADC_VDD_180_VQPS_SYS_1_AON/0) | -11.72690, 12.49510 | `Signal$TOP` → `Signal$L02(DGND)` |

Raw `PadStackDef DR-0102_60` is `0.020 mm` drill, `Material = COPPER`. Its
exact `PadDef` records are `.PadDef Signal$L02(DGND)` at byte offset
`1068972948`, followed by `.PadDef Signal$TOP` at byte offset `1068973014`;
each is `Regular Circle 0.030 mm`. There is no L29 or L30 `PadDef`. Accordingly, a D115B terminal row
whose `layer=L29/L30` is selected-plane attachment/ownership, not the source
Node layer; its PadDef/Regular provenance points to the `Signal$TOP` source
PadDef.

* D104 is `PASS` for exact ordinals 258–273, with the contract
  `raw source island only; no merge/intersection/difference/simplify/snap`.
  The target plane rows are cell 259 (`Signal$L29(DGND)`) and cell 264
  (`Signal$L30(OTHER_POWER1)`, net
  `ADC_VDD_180_VQPS_SYS_1_AON/0`), each with deterministic island IDs and
  source-asset hashes.
* D115B is `PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED`. Its two rail rows
  bind the exact L30 power island and L29 ground island to the selected rail.
  Six complete terminal rows retain Node, Via, finite vertex/edge, component,
  PadDef, Regular, PadStack, and raw PadShape provenance. The selected-plane
  attachment examples are power pins `SITE0:20122`, `SITE0:20576`,
  `SITE0:20589` on L30 and ground pins `SITE0:19958`, `SITE0:20612`,
  `SITE0:19973` on L29; their raw source Nodes are `Signal$TOP` as shown in the table
  above. Their Via owners are `Via336278`, `Via336299`, `Via336259`,
  `Via1362725`, `Via1360630`, and `Via1468557`, respectively. Every D115B terminal row has
  `status=complete`, `issues_json=[]`, and exact source-record IDs.
  D115B component/equipotential topology establishes logical reachability and
  ownership to the selected L29/L30 planes; a terminal row's single
  `via_owner` is not a target-layer physical via and is not evidence that the
  source-to-opposite endpoint edge itself traverses L28–L31.
* The materialized candidate's normalized project binds the selected rail to
  L30/L29 and provides a `STACKED` analytical via template with a 100 µm by
  100 µm finite-port size. This is a model/template fact, not proof of a
  physical barrel solid.
* The candidate's routing-obstacle attachment explicitly says
  `production_ready=false` and its scope excludes routed PWR/GND, signal
  vias, pins, pads, and fanout pads. Thus it cannot be treated as complete
  access-conductor geometry.

The fallback gap is visible in `src/spd_decap_pi/spd_adapter.py:367-449`:
the adapter collects true regular-shape envelopes when a PadDef exists
(`:387-411`), then computes `drill/2 + plating_um` as an explicit analytical
plated-barrel fallback (`:412-432`) and labels the result
`SOURCE_PADSTACK_REGULAR_SHAPES_WITH_ANALYTICAL_BARREL_V1` (`:441-445`). With
no raw L29/L30 PadDef, this is a declared assumption, not source-derived pad
geometry or a physical clearance certificate.

### WP3 source-authority checkpoint (2026-09-05)

The first immutable production attempt used only this exact root:
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp3-selected-via-source-authority-01`.
It ran exactly once and exited 1 after 941.787198 seconds, with sampled peak
working set 8,677,024 KiB and sampled peak page-file usage 10,122,920 KiB. It
returned the unexpected `STOP_W0_VIA_SOURCE_EVIDENCE_MISMATCH`, produced zero
artifacts and no receipt, made no authority upgrade, and was not retried.

Independent diagnosis confirmed an exact fixture/validator literal defect:
immutable ownership SQLite opposite-endpoint rows included the `(DGND)`
qualifier while the validator and synthetic fixture expected the older
unqualified form. The
minimum correction is accepted and bound to the auditor and focused-test
artifacts identified above; nine focused synthetic tests passed. This is not
production validation.

Production retry at that checkpoint remained `STOP` pending separate immutable
HQ approval. The previously named `-02` root remains a historical planned root;
no execution or authority upgrade is inferred from it. The separately approved
one-shot `-05` run is recorded next. WP3 remains `PARTIAL`: the flags for 3-D
geometry, barrel, antipad, land, intermediate access/traversal, target-layer
traversal, and L29/L30 physical pad proof remain false. This creates no solver,
FasterCap, PowerSI, or numerical authority.

### WP3 accepted one-shot `-05` checkpoint (2026-09-05)

The separately approved one-shot used only this exact root:
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp3-selected-via-source-authority-05`.
Its immutable evidence identities are:

| evidence | bytes / SHA-256 |
|---|---|
| approval | **7173 B / `1a39856a26ffaea17f7a297284192316b5fa3b19a6edf502ecb2797e2d5c9c80`** |
| HQ | **6399 B / `bb5eb014d5bd684c9971abd191f6d43d0f308d874bf2f1f4c91cd6b0c43252d8`** |
| token | **3432 B / `2ecc186f09e802e0c734b91e512d893051081ae1a4f1795b7d4ffc45ddd909f3`** |
| receipt | **1321085 B / `5800f801467680af8e21f8638650e238df0394296d3aa8d3caf6084a39183371`** |

The outer/controller result is `PASS_EXPECTED_STOP` with exit `0`; the raw
auditor exited `1` after **1247.7464785 s** with
`STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT`. Empty rows are
`[260,261,263,265,266]`; required rows are `259..266` with `all_eight=true`.
The selection was via source authority with `row_count=6`, and
`target_layer_traversal=false`. This accepted one-shot is evidence of the
expected stop only: WP3 remains `PARTIAL`, and no solver, Triangle, FasterCap,
or PowerSI work is authorized or claimed.

These facts establish source-derived ownership/topology and plane artwork;
they do not establish a complete 3-D conductor volume for every Via, pad,
anti-pad, or access trace crossing L28–L31.

### WP3 read-only candidate validation (2026-09-06; not production)

The old in-memory candidate (**19377 B / SHA-256 `0f251e…`**) had **87/87**
source pointers, but validation marks it `INVALID` for production. It assumes a
synthetic mandatory `PadStack` on every Node, a single Via `PadStack`, and
unqualified terminal layers. The source instead has only **2 TOP Nodes** with
`DUT`; **42 lower Nodes** legitimately omit `PadStack`; **36 Vias** reference
**19 layer-specific PadStacks**; and the qualified terminal layers are
`Signal$L18(DGND)` and `Signal$L20(DGND)`. The four reversed-`Trace` fixes
passed. The WP3 builder/test correction is under independent review; the old
candidate must not be written, approved, or run. WP3 remains `PARTIAL` and
selected-chain-only, with no global or nonconnection claim.

### Exact impact of the two pad-containment conflicts

D115C evaluated all eight required rows and stopped with
`STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT`. The only containment violations are:

| pin | role/layer | D104 target | result | source Via owner (D115B) |
|---|---|---:|---|---|
| `SITE0:20612` | GND / L29 | 259 | `target_center_covered=false`, `target_proven=false`, reason `TARGET_ISLAND_CONTAINMENT`; boundary-distance field 21.6333281102 µm | `Via1360630` |
| `SITE0:19973` | GND / L29 | 259 | `target_center_covered=false`, `target_proven=false`, reason `TARGET_ISLAND_CONTAINMENT`; boundary-distance field 125.2600000000 µm | `Via1468557` |

Both remain complete D115B terminal-ownership rows; the conflict is between
their 60,000,000 pm diameter footprint checks and D104 cell-259 island
containment. D115C's 60 µm diagnostic footprints are projected circles, not
interior-layer source pads; their distances are unsigned center-to-boundary
diagnostics:
21.6333281102 µm and 125.2600000000 µm. The existing audit predicate is the
30 µm diagnostic disk's complete-containment contract
`target.covers(point) AND distance_to_boundary + 1e-9 >= radius_um`
(`audit_source_l29_l30_port_window.py:812-830`). Both centers are outside the
target island, so the predicate is immediately false. The 21.633<30 and
125.260>30 comparisons are diagnostic projections only: the 30 µm radius comes
from the raw `Signal$TOP` `Regular Circle 0.030 mm` projected onto the selected plane,
not from an L29 source PadDef. They must not be promoted to physical contact or
non-contact conclusions. The conflicts do not discard D115B topology ownership;
they block physical attachment/equipotential proof against the D104 WKB and
the literal eight-row acceptance contract. D115C also records positive source
intersections for row 259 (779,005.9836758123 µm²), row 262 (21,012.5 µm²), and
row 264 (926,540.7647500002 µm²); those positive areas do not cure the failed
containment predicates.

### WP3 authority gap and minimum additional evidence

The minimum next evidence is:

1. a raw-record receipt for every selected target/nearby Via and access path,
   including exact endpoint layers/z spans, diameter/barrel/anti-pad/land
   geometry, and source Node/Via/PadDef/Regular/PadShape hashes. It must
   explicitly address the absent L29/L30 PadDefs rather than silently treating
   the TOP regular circle or analytical barrel as an interior source pad;
   it must also enumerate every intermediate Node/Via/Trace/PadStack/access
   segment from each TOP source pin to its selected L29/L30 plane ownership
   before claiming a complete path;
2. a materialized 3-D conductor/clearance certificate proving nonintersection
   between target, neighboring pads/vias/access conductors, and the four
   L28–L31 planes;
3. a hash-bound one-to-one ownership ledger from each finite terminal to its
   physical Via, pad footprint, and selected plane island; and
4. an explicit physical-attachment/equipotential decision for the two L29
   containment failures and for D115B topology versus D104 plane WKB authority.
   Any changed inclusion semantics requires approval and a new receipt; no
   fallback geometry is permitted.

Until this evidence exists, WP3 remains `PARTIAL` and plane-body-only data is
feasibility evidence, not a PowerSI-comparable/full-stack geometry authority.

## Minimal next work (separate and non-numerical)

* **Completed C0 (exact-02):** the fresh frozen split-PSLG exact-clearance
  `PASS` is complete; do not rerun it. It supersedes the stale exact-01 C0
  bundle for future planning, while raw-source clearance remains unknown.
* **C1/Triangle STOP:** the immutable `-02` and `-03` one-shots are consumed
  `STOP_C1_TRIANGLE_SINGLE_CALL` evidence and are not reusable; the accepted
  static/no-Triangle certifier and one-call serializer remain design evidence
  only. The prelaunch cross-binding code/test checkpoint is independently
  `CODE_ACCEPT` for static/synthetic evidence only; no `-04` approval or run
  exists; C1/Triangle remain `STOP` pending separate immutable approval.
* **WP2:** do not re-request the completed static material/loss slice. The
  rejected `WP2-XYD-01` proposal closed none of the five blockers; the minimal
  next read-only package is `WP2-SRC-XY-AUTH-00` explicit-source acquisition,
  currently `STOP_NOT_REPRESENTED`. Resolve exactly
  `xy_dielectric_partitions`, `conductor_layer_void_fill`,
  `reference_points_and_panel_sides`, `outer_truncation_and_closure`, and
  `absolute_source_z_transform`; no FasterCap call is implied.
* **WP3:** the accepted `-05` one-shot is complete and remains `PARTIAL`;
  continue exact Via/pad/access 3-D geometry, clearance, ownership, and the two
  containment decisions. Do not rerun WP3 or run FasterCap, Triangle, or any
  D117 numerical solver.
* **Later:** after the C1 gate, prove 2-D output, deterministic replay,
  extrusion/aggregate evidence, and only then solver/PowerSI correlation; that
  correlation remains unopened.

Long jobs are checked only at or after **10 minutes** unless a terminal,
failure, or resource event occurs; later polls are bounded to at least 60
seconds and report only meaningful changes.

## Self-check and remaining STOPs

All locally present ledger byte counts and SHA-256 values above were recomputed
with `Get-FileHash -Algorithm SHA256`; receipt field values and conflict fields
were re-read with `ConvertFrom-Json`/`json.load`, raw Node/Via/PadStackDef lines
were checked with the listed `rg -a` commands; the five extracted FasterCap
HTML files were read at the stated lines; and the original CHM was
rehash-checked at its ledger path. `git ls-remote` returned the pinned upstream
commit. This three-document synchronization modified only the three named
documentation files; it did not modify source, receipt, candidate, or
`accuracy_parse.py` files, and it did not execute production/helper/audit/test/
solver/Triangle/FasterCap/PowerSI work. WP2 `STOP_UNSEALED` blockers are exactly
`xy_dielectric_partitions`, `conductor_layer_void_fill`,
`reference_points_and_panel_sides`, `outer_truncation_and_closure`, and
`absolute_source_z_transform`; raw complex-matrix sign/reference interpretation
also remains `STOP`. WP3 retains source-bound plane/ownership evidence but not a
complete 3-D via/pad/access certificate; two unresolved containment decisions
remain. No numerical D117 run is proposed or authorized.
