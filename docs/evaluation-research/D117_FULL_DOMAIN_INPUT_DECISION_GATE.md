# SPD Decap PI Evaluator v0.23.1 — D117 전체 도메인 입력 결정 게이트

**Current gate:** `STOP_NUMERICAL_EXECUTION`; Cell258 C0 frozen-PSLG clearance is `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA`; C1 remains `ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / STOP_C1_TRIANGLE_SINGLE_CALL`. The consumed `-02` and `-03` attempts are immutable STOP evidence; the fresh C0 exact-02 re-seal is recorded below, and the C1 prelaunch cross-binding code/test checkpoint is independently `CODE_ACCEPT` for static/synthetic evidence only. No `-04` approval or run exists; `-03` remains immutable, consumed, and no-rerun.

The prior `ACCEPT_C0` stage label remains the geometry-only stage context; the
scoped exact receipt below supplies its `PASS` evidence without upgrading C1
or any numerical authority.

WP2 remains `PARTIAL`. The only promoted WP2 statuses are
`static_z_material_loss_slice=PASS`,
`face_z_material_rows=SEALED_SOURCE_ORDER_NOMINAL_ONLY`, and
`loss_convention_1mhz=SEALED`; exactly five `STOP_UNSEALED` blockers remain
(`xy_dielectric_partitions`, `conductor_layer_void_fill`,
`reference_points_and_panel_sides`, `outer_truncation_and_closure`, and
`absolute_source_z_transform`); `STOP_NUMERICAL_EXECUTION` remains in force.

Conservative overall stage wording is **about 35–40%**: geometry/input-feasibility
Stage2 is materially advanced, while solver and PowerSI correlation remain
unopened.

## 1. 결정

**결정: `STOP_NUMERICAL_EXECUTION`.** 이 문서는 D116의 결과를 변경하거나 재실행을 승인하지 않는다. 지금 허용되는 비수치적 범위는 accepted Cell258 C0 증거의 문서화, independently Sol-reviewed exact code/synthetic evidence, C1 memory/resource 설계와 그 준비뿐이다. Aggregate 16-cell geometry/mesh/resource certificate 설계는 그 이후 단계로 남겨 둔다. WP2 material authority와 WP3 via/pad authority 연구는 독립적으로 병행할 수 있지만 solver는 0이다. 소비된 immutable `-02`와 `-03` one-shot은 모두 `STOP_C1_TRIANGLE_SINGLE_CALL` receipt로 종료되었고 재사용/재실행하지 않는다. C0 exact-02 재봉인은 아래에 기록되었고, C1 prelaunch cross-binding code/test bundle is independently `CODE_ACCEPT` for static/synthetic evidence only; no `-04` approval or run exists. 그 전까지 모든 C1/Triangle 동작과 FasterCap 호출, 재시도, `-oi` 사전 실행, 생산 oracle, PowerSI 비교, Zii 변환, `C_res` 주입은 모두 `STOP`이다. 정확한 raw-visit cap **375,962**의 identity/count/hash 조건은 아래 accepted exact C0 receipt에서 충족되지만, 그 범위는 frozen split PSLG에 한정된다. Exact implementation is independently `ACCEPTED` by Sol for code and synthetic evidence only; the exact-production C0 clearance `PASS` is separately scoped in §3A and is not a C1/Triangle/solver/PowerSI approval. Any future C1/Triangle operation remains `STOP` until a new reviewed Windows controller and final implementation/runtime/input hashes, required resource limits, and output path are bound by a separate immutable Sol approval anchor. Solver and PowerSI remain `STOP`.

다음 두 경계를 명시적으로 고정한다.

- **M1.** homogeneous `epsilon_r=3.4` 수치 solve는 production/PowerSI oracle로 승인하지 않는다. WP2의 material/loss convention은 sealed 되었지만 source-derived heterogeneous interface contract는 아직 없으므로, 수치 실행은 그 interface contract 또는 정량적 error-bound 논증이 승인될 때까지 `STOP`이다.
- **M2.** plane-body-only geometry는 input/mesh/resource feasibility 작업에만 허용된다. PowerSI-comparable/oracle 지위에는 source-derived via/pad/access conductor geometry가 필요하다.
- **M3.** Cell258 Stage2A C0 boundary-clearance가
  `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA`로 봉인되었다. Static/no-Triangle
  C1 certifier와 future one-call serializer 준비는 accepted design evidence
  only이며, consumed immutable `-02`와 `-03` one-shot은 모두
  `STOP_C1_TRIANGLE_SINGLE_CALL`로 종료되어 재사용하지 않는다. 다음
  C0 exact-02 재봉인은 아래에 기록되었고, C1 prelaunch cross-binding
  code/test bundle is independently `CODE_ACCEPT` for static/synthetic evidence
  only; no `-04` approval or run exists, and until a separate immutable approval
  anchor is issued, C1/Triangle은 `STOP`이다. WP2 material authority와 WP3 via/pad authority
  연구는 독립적으로 병행할 수 있지만, 어느 단계도 solver invocation을 승인하지
  않는다.

이 게이트의 1차 목적은 original SPD physics에서 PowerSI-comparable single-rail `Zii`를 얻는 것이며, Distribution 호환성은 2차 범위다. 본 문서는 그 어느 쪽의 달성도 주장하지 않는다.

## 2. 증거 원장과 권위 순서

### 2.1 원본 및 봉인된 receipt

| 증거 | 경로/식별자 | 크기 | SHA-256 | 권위 |
|---|---|---:|---|---|
| raw SPD | `D:\S4LB002-2Para_260729_1_injected.spd` | 1,116,717,287 B | `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2` | source identity |
| D103 receipt | `D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json` | 204,735 B | `4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9` | stackup/material/z |
| D104 receipt | `D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01\geometry_receipt.json` | 19,728 B | `bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b` | `PASS`; plane WKB |
| D115B receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\d115b_source_plane_ownership_materialization_receipt.json` | 514,208 B | `c69dce134ca02ff263b75f5df930106a7d8d75d05cccf2acd13b7d9b1919aa49` | target topology |
| D115B candidate (identity only) | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\source_plane_ownership_candidate.spdpi` | 925,278,361 B | `1ecc6cbd18a5234178c98c8ead79f684c278daf21bdb64a789296dd6543cd7bc` | read-only identity; not rewritten |
| WP2 material/loss generator | `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\tools\research\build_d117_wp2_z_material_loss_decision.py` | 16,232 B | `126e48378752fa78f15a85d5a0116ba21a13e9713fcb58e46b2efd25c1d6264c` | static evidence generator |
| WP2 HQ approval | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp2-z-material-loss-decision-01\d117_wp2_z_material_loss_hq_approval.json` | 6,737 B | `485c482bb3b5176409fe9b14ffed9500684bdac4acede0199ef7137375d5cefc` | static approval |
| WP2 material/loss receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-wp2-z-material-loss-decision-01\d117_wp2_z_material_loss_decision_receipt.json` | 11,453 B | `ae356a088f0f4954ab3087caaf19ef64ca813b84d568193b888f1f1b2d2316f4` | one static attempt; exit 0 |

The accepted WP2 receipt records exactly one static attempt, exit `0`, twelve
source-order transitions, and `executed_components=[]`. The generator's
synthetic self-check is `PASS` and its independent Sol review is `ACCEPT`; this
is static documentation evidence only, not numerical execution or solver
validation.

권위 충돌은 다음 순서로 판정한다: **D103 material/z → D104 raw plane WKB → D115B source-derived target ownership/topology**. D104는 exact cells 258..273의 raw WKB aggregate **13,600,420 B**를 봉인한다. D104의 pad-circle containment는 D115B를 대체하지 않는 진단(diagnostic)이다. 각 receipt와 WKB는 source asset에 묶이고, D104 contract는 raw source island only이며 merge/intersection/difference/simplify/snap을 금지한다.

관련 repository 문서는 [PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md](../PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md#v321-현재-상태--d102d114), [WORK_EXECUTION_BASELINE.md](../WORK_EXECUTION_BASELINE.md#v321-현재-상태--d102d114), [D115C_REVIEW_AND_D116_DECISION_GATE.md](D115C_REVIEW_AND_D116_DECISION_GATE.md), [D116_SHADOW_RAW_C_MATRIX_RESULT.md](D116_SHADOW_RAW_C_MATRIX_RESULT.md)이다. 공식 FasterCap 도움말은 `C:\Users\User\AppData\Local\Temp\codex-fastercap-help-d116-20260903` 아래 `InputFile/3D/InputFile_3D_ch03.htm` (lines 20–24), `InputFile_3D_ch04.htm` (lines 20–29), `Run/Run_ch02.htm` (lines 25–60), `Output/Output_ch03.htm` (lines 164–167)만 신뢰한다.

## 3. 16-cell raw conductor basis

D104의 exact cells 258..273(16개)를 서로 다른 셀끼리 C `+`로 merge하지 않고 독립 raw conductor로 보존한다. infinity를 DGND에 묶지 않는다. 아래 순서가 geometry, FasterCap input, raw Maxwell C, future CSV/stdout의 행·열·라벨에 동일하게 사용될 deterministic contract이다.

| index | cell | exact deterministic ASCII base label |
|---:|---:|---|
| 1 | 258 | `o258_l28_dgnd` |
| 2 | 259 | `o259_l29_dgnd_target_g` |
| 3 | 260 | `o260_l30_ddrh0` |
| 4 | 261 | `o261_l30_ddrh1` |
| 5 | 262 | `o262_l30_ddrl0` |
| 6 | 263 | `o263_l30_ddrl1` |
| 7 | 264 | `o264_l30_vqps1_aon0_target_p` |
| 8 | 265 | `o265_l30_vqps1_aon1` |
| 9 | 266 | `o266_l30_dgnd` |
| 10 | 267 | `o267_l31_ddrh0` |
| 11 | 268 | `o268_l31_ddrh1` |
| 12 | 269 | `o269_l31_ddrl0` |
| 13 | 270 | `o270_l31_ddrl1` |
| 14 | 271 | `o271_l31_vqps2_aon0` |
| 15 | 272 | `o272_l31_vqps2_aon1` |
| 16 | 273 | `o273_l31_dgnd` |

Future solver labels must be exactly `g1_o258_l28_dgnd` through `g16_o273_l31_dgnd`, in this order, and must be verified before accepting CSV/stdout. These ASCII labels are a safe output contract; no spelling, slash, or ordinal normalization is permitted. Thus the required output is an exact 16×16 finite matrix, not a reordered or merged submatrix. Four DGND cells (258, 259, 266, 273) remain separate; D115B proves target-rail role for cell 259 L29 DGND and cell 264 L30 `ADC_VDD_180_VQPS_SYS_1_AON/0`, but does **not** prove an all-DGND/every-cell equipotential merge.

Official FasterCap C semantics permit `+` to collate consecutive surface files as one conductor. A future source-derived heterogeneous contract may use `+` only to collate multiple material/surface partitions of the **same** D104 cell; it must remain source-proven and preserve exactly these 16 output conductors. `+` may not merge different D104 cells or assert an equipotential grouping.

## 3A. Cell258 Stage2A C0 accepted boundary

Current status is `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / STOP_C1_TRIANGLE_SINGLE_CALL`. The former `STOP_C1_MEMORY_MODEL_UNPROVEN` label was refined, not cleared. The accepted geometry-only census, exact C0 clearance, and no-Triangle preparation are sealed/verified as bounded evidence; the detailed checkpoint is maintained in the [WP1 Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint-wp1-authority). The census is sealed in
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell258-stage2a-c0-cap-02`.
Its C0 JSON is **5,448,871 B / `5ae752680b63686f90dd0d80037b337bba0da71f3bd9d6d755a9e94365f9ac60`**,
text is **142 B / `b4b93f13737c460f5c76272afb614312021904f679419c1fd652fed17e226d20`**,
and run receipt is **26,767 B / `ceba8bce28329ac858c0466c948839a87ba0d35ef4934277ef06e7d2c6ea1f57`**.
The controller/helper/approval identities are **114,544 B /
`fb9278f099eddcf363f36ee76f99eb647525a8fa6931b67546486f37a6fa4c02`**,
**40,274 B / `169d70e2d4aaf23bfdb3b9fbd209380ca77ed7ed09e09f5c89c75f32a30f3452`**,
and **9,030 B / `3d02662d59aa514bfbf64710119509e25d951b08bf4f3bb0cc6f21805378159e`**.

The receipt is `PASS`, elapsed **2.813 s**, child exit **0**, Job total/active
**1/0**, peak commit **285,188,096 B**, observed working-set peak
**264,499,200 B**, and max observed artifact **5,612,916 B / 7 items**. It
binds ordinal **258**, `Signal$L28(DGND)` (source layer raw/ordinal **54**, conductor), island
`spd-surface-island:ea4bc44349ce103beab4dca3`, area
**8,476,333,524.145388 um2**, bbox **[-49700,-49700,49700,49700]**, one
Polygon component (exterior ring=1), **2,049 rings**, `R=149,078` (`8+149,070`), `H=2,048`, and
thickness **35.0 um**, conductivity **59,590,000**. Step **140 um** yielded `S=153,246` vertices=segments,
marker count **149,078**, marker sum `S`, distribution
`{1:149070,16:3,32:1,1024:4}`, and **2,048** strict interior hole points;
edge flags were `corner=true, curve=false, edge=true`.
The split floors are `T=S+2H-2=157,340`, `Vclosed=306,492`, and
`Fclosed=621,172`, within planar V/T **400,000/600,000** and derived closed
V/F **800,000/1,608,188** caps. The raw source-ring floors remain
`T=153,172` and `F=604,500`; they are not split or final-mesh counts. The
canonical PSLG is **12,057,453 B / `1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b`**
with lower-bound estimate **7,421,359 B**; bytes were not retained separately
and must be recomputed before C1. Triangle, extrusion, solver, FasterCap, and
network counts are zero.

An earlier no-Triangle preparation checkpoint is retained in the [WP1 Cell258
C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint-wp1-authority)
for traceability only. Its implementation/test identities, bounded-array
statuses, derived canonical cap, and frozen lifetime-ledger values are
**historical/superseded** and **not current, final, or authoritative**. The
current accepted static/no-Triangle block in §3A remains authoritative; the
superseded values are:
module **77,246 B / `2e9c5b7c2233c5b24448800d0ec81a909c028618741f158c7ef458a85d033a66`**,
tests **27,990 B / `77755a8c63d4a538d9c498c6be88b323f2eb9d73f0506338148d4e02fbf80546`**,
and **55 focused tests in 0.39 s, Sol ACCEPT**. The nine-phase/17-term exact
contract, sequential one-bytearray read/release design, `1..8192` chunk cap,
and known floor **42,153,032 B = 24,356,424 + (14,400,000 + 3,200,000 +
196,608)** are likewise historical/superseded and not current, final, or
authoritative. The lifetime-omission prerequisite is closed; opaque
native/interpreter/allocator feasibility and full 2-D topology/quality
certification remain STOP. The accepted exact-production C0 clearance is
scoped separately below; the fresh exact-02 re-seal is current. The consumed `-02` attempt is immutable
`STOP_C1_TRIANGLE_SINGLE_CALL` evidence, and the consumed `-03` STOP receipt is
recorded below. The prelaunch cross-binding code/test checkpoint is independently
`CODE_ACCEPT` for static/synthetic evidence only; no `-04` approval or run exists.
The outer wrapper's PID **26700** / no-retry note is an orchestration record
only; its outer exit/stdout/stderr were not sealed after a read-only PowerShell
`$PID` assignment and must not be inferred.

#### D117 raw-visit occupancy checkpoint

The detailed [WP1 raw-visit occupancy checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#d117-raw-visit-occupancy-checkpoint-wp1-authority) is authoritative. Its accepted immutable receipt is `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-cell258-boundary-clearance-occupancy-02\d117_cell258_boundary_clearance_occupancy_receipt.json`, **3,448 B**, SHA-256 `7e4e5e4b7b1c0b4d3aba88bcafbc655a05707bbd9f06d58eda283470676d4c51`; it records `153,246` segments, `242,165` records, `79,443` cells, max occupancy `31`, `candidate_visit_upper_bound=375962`, packed SHA-256 `a0c9124c8f59eab11e2bad03a1b177b515157886046428d79f3ea3e24c0bf708`, and actual ndarray bytes **6,260,976 <= cap 9,227,528**. The module/test identities and focused results are recorded below; no pair/distance/Triangle/solver operation occurred.

The exact raw-visit cap **375,962** was statically approved with zero headroom
conditional on receipt identity and a rebuilt count/hash match; the accepted
exact C0 receipt below records `raw_visits=375962`. Exact implementation is
independently **ACCEPTED by Sol** for code and synthetic evidence only: module
**49,139 B** / SHA-256
`241dd81947107d38b770df53c5e350fe6e251a80a8e558f609da592b69fe43a3`, tests
**28,953 B** / SHA-256
`443fdd8f5b98e5a2b1d986380ea0a666ae3055d341a046d2b62cdb7860549ceb`, focused
suite **36 passed in 0.24 s**, synthetic self-check **PASS**. This
code/synthetic ACCEPT is not itself an exact-production `PASS`; the scoped
exact-production C0 `PASS` is documented below.

The accepted corrections are: actual returned vertices/segments/segment-markers
are canonically reverified before scanning; CLI returns `0/1/2` for
`PASS/WITNESS/INCOMPLETE` (refusal `2`); `scan_complete` is distinct from
`decision_complete`; clearance is scoped to frozen split-PSLG segments while
raw-source clearance is unevaluated/unknown and
`GLOBAL_BOUNDARY_EQUIVALENCE_STOP` remains; and intersection witnesses use
exact distance `N/D=0/1`.

The accepted exact-production C0 clearance below is limited to the frozen
float64 split-PSLG scan. No new exact operation is authorized: any future
operation remains `STOP` until a reviewed Windows controller and a new
immutable Sol approval anchor bind final controller/module/helper/runtime/input
hashes, one attempt with no retry, a **1 GiB job-wide commit cap**, one active
process, a **270 s scan deadline**, a **300 s hard wall**, and a no-clobber
 output. All C1/Triangle activity, including the consumed `-02` and `-03`
 one-shots, plus
solver and PowerSI, remain independent `STOP`s; no C1/Triangle execution is
authorized pending a separate new approval. The C0 `PASS` certifies only frozen
float64 split PSLG segments, not raw WKB/source equivalence.

#### Cell258 C0 exact-production boundary-clearance PASS (exact-01; retained historical)

An independently Sol-reviewed exact-production run is sealed at
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-cell258-boundary-clearance-exact-01`.
The approval is **3568 B / `942a82b7c941b0af2537359bb693b5756306cc6896cec4593863ac9a43784177`**;
the one-shot attempt is **833 B / `502baed761219d0f1cfdc255c287e2d83779c473858da8256350750df785070e`**;
the controller receipt is **5321 B / `e31335900047331b25f07a373c691406b81cceaaa192d778a6b0f72d77b84a51`**;
and the exact receipt is **4493 B / `ca375d43691d94bdafa8b0ccd85f4320463e2875e9b5402ed60244338adb3bdc`**.
Status is `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA` with
`decision_complete=true`, `scan_complete=true`, and `exact_clearance=true`:
exact sealed receipt fields are `exact_unique_nonincident_pairs=212005`,
`exact_pair_evaluations=212005`, `exact_distance_evaluations=848020`, and
`endpoint_distance_evaluations=848020`, with `raw_visits=375962` and
`duplicate_visits=10711`. The run held `job_processes=1`,
`job_commit_cap=1073741824 B`, `peak_commit=744730624 B`, and elapsed about
`18.641 s`. This scope is only the frozen float64 split PSLG; raw-source
clearance remains unknown, `GLOBAL_BOUNDARY_EQUIVALENCE_STOP` remains, and
C1/Triangle activity, solver, and PowerSI remain `STOP`. The consumed `-02`
one-shot produced no Triangle or FasterCap solve; no rerun is authorized.

This immutable `exact-01` C0 bundle is stale for future planning because it
pinned historical helper/module identities. It remains retained for chronology
only; the fresh exact-02 re-seal after the C1 `-03` STOP is authoritative below.

#### Static/no-Triangle C1 certifier (accepted)

The accepted root-fixed static/no-Triangle C1 certifier is
`tools/research/d117_triangle_cell258_c1.py`, **139525 B / `821aed9ac82138181bc319e77d6e1508c9871a0e51c21e51607d709e51bdc1ec`**,
with tests
`tests/test_d117_triangle_cell258_c1.py`, **39812 B / `1682d4c44f723cc246c18f224bc4dd63154b71ea3c8bb88d3e4bd79b108fde15`**.
The current exact suites are **71 passed** for the certifier and **30 passed**
for the one-call preparation (**101 passed total**); all three safe selfchecks
`PASS`. The Win32 create/query regression is a pytest case, not a selfcheck.
The review also records a complete **48-term ndarray ledger**. Static caps are
`V400k/T600k/B400k/H2048`; this current accepted block is authoritative for
these preparation identities and counts. The known co-live ndarray floor is
`87156424 B`, the canonical stream cap is `67306552 B`, and the accepted
operational Job cap is exactly `3435970560 B`. Native/Python/allocator behavior
remains opaque. No Triangle was executed. See the [WP1 Cell258 C1 preparation
checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint-wp1-authority)
for the detailed ledger.

#### C1 one-call preparation and consumed `-02` STOP

The historical post--02 accepted serializer-only preparation uses runner
`tools/research/d117_triangle_cell258_c1_single_call_runner.py`,
**51930 B / `592ea02c6a17c63ca6b5b35113726126e6280a717078269084fa76d1cf1136b1`**,
controller
`tools/research/run_d117_triangle_cell258_c1_single_call_exact_once.py`,
**71996 B / `d6407cae741b1ab06ff2229db086bbb1c49adbf062cc99209c98c4add33faafc`**,
and tests
`tests/test_d117_triangle_cell258_c1_single_call.py`,
**27658 B / `fa827a644a1e2c22d0e8cfda4570b835b11cbd83537a97a51b57f4e6f7105533`**.
The post--02 **51930 B** runner/test fix uses the existing `_canonical_path` in
runner `_identity`; Sol ACCEPT records **30 passed**, all three safe selfchecks
`PASS`, and three Windows samefile/different-file/shared-role regressions. The
Win32 create/query regression is a pytest case, not a selfcheck. The frozen
`-02` attempt used the pre-fix runner **51918 B /
`f827455647aad4e425012cad3e38df538c000c379aecf6e61c78e65667b00c9d`**. The
historical **51930 B / `592ea02c6a17c63ca6b5b35113726126e6280a717078269084fa76d1cf1136b1`**
canonicalization fix is post--02 static/synthetic evidence only, has never
received C1 execution evidence, and is not represented, pinned, or executed by
`-02`. The `pq15CzS221330` option is static preparation only; no C1/Triangle
execution is current.

#### C1 single-call attempt `-01` (consumed immutable STOP)

The immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-cell258-c1-single-call-01`
was consumed exactly once and contains exactly these three files:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_c1_single_call_hq_approval.json` | **18050 B / `e3ab65812efdd9c71fd927297c3a63759e3d705a57a3e0cf450d6645ed72b026`** |
| `d117_cell258_c1_single_call_attempt_token.json` | **1339 B / `0d50a89dfbf8553c4b5a90089fb09c4abf82ea8c4714eeec6b07f366932df9e2`** |
| `d117_cell258_c1_single_call_controller_receipt.json` | **42767 B / `2a240832d2ab4af3328f22cea677723a6d0b2924b900f1de1ee2fc9042cf16df`** |

The controller receipt records `status=STOP_C1_TRIANGLE_SINGLE_CALL` and
`reason=ControllerError: terminal cleanup failed`. The actual child outcome was
`STOP child Job limits do not match approval`: the approved Job limit was
`3435973836 B` while Windows returned `3435970560 B`; strict equality therefore
failed. It records one launch and no retry
(`attempts=1`, `retries=0`, `launch_count=1`). No canonical or exact receipt
was produced. Triangle activity is inferred as zero from the pinned pre-import
failure path plus the absent outputs; the receipt field is
`triangle_loaded_modules=null`, and no direct Triangle-call counter is claimed.
Mark `-01` immutable, consumed, and do not rerun it; this is controller/resource evidence only and does not
change the C0, WP2, WP3, or numerical STOPs.

The cap correction occurred after `-01` and before `-02`: it binds the
operational cap exactly to `3435970560 B` while retaining strict equality. Its
five current accepted file identities are the certifier, runner, controller,
and two focused-test files listed above; the independent Sol ACCEPT records
**30 passed**, all three safe selfchecks `PASS`, and three Windows
samefile/different-file/shared-role regressions. The Win32 create/query
regression is a pytest case, not a selfcheck. The immutable `-02` root below
was consumed exactly once and is not reusable.

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

The C0 exact-02 re-seal below is complete. The next gate is not yet created,
reviewed, or executed: add a controller prelaunch bundle check and regressions
for current-helper cross-binding, then create a separately approved new C1
root/attempt. Until then all C1/Triangle and numerical solver/FasterCap/PowerSI
activity remains `STOP`.

WP3's accepted one-shot `-05` source-authority rerun is detailed in the
[WP2/WP3 source-authority audit](D117_WP2_WP3_SOURCE_AUTHORITY_AUDIT.md):
it is `PASS_EXPECTED_STOP` at the outer/controller boundary but
`STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT` at the raw auditor, so WP3 remains
`PARTIAL`. It grants no solver, Triangle, FasterCap, or PowerSI authority.

#### Cell258 C0 exact-production boundary-clearance PASS (exact-02 re-seal, after C1 `-03` STOP)

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

#### C1 prelaunch cross-binding CODE_ACCEPT (2026-09-06; code-only)

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

The read-only WP3 candidate validation is recorded in the [WP2/WP3
source-authority audit](D117_WP2_WP3_SOURCE_AUTHORITY_AUDIT.md): the old
in-memory **19377 B / SHA-256 `0f251e…`** candidate had **87/87** source
pointers but is `INVALID` for production because of synthetic mandatory-Node
`PadStack`, single-Via-`PadStack`, and unqualified-terminal-layer assumptions.
Only 2 TOP Nodes have `DUT`; 42 lower Nodes legitimately omit `PadStack`; 36
Vias reference 19 layer-specific PadStacks; qualified terminals are
`Signal$L18(DGND)` and `Signal$L20(DGND)`; and four reversed-`Trace` fixes passed.
The builder/test correction remains under independent review. The old candidate
must not be written, approved, or run; WP3 remains `PARTIAL` and
selected-chain-only, with no global or nonconnection claim.

## 4. z, thickness, material, and interface semantics

D103 supplies receipt-relative cumulative source-order coordinates, thickness, and material provenance; it explicitly records these coordinates as **not absolute source z**:

| region | receipt-relative cumulative z (µm) | derived local offset relative to L28 top (µm) | thickness |
|---|---:|---:|---:|
| L28 | 1882..1917 | 0..35 | 35 µm |
| DR2829 | 1917..1947 | 35..65 | 30 µm |
| L29 | 1947..1967 | 65..85 | 20 µm |
| DR2930 | 1967..1997 | 85..115 | 30 µm |
| L30 | 1997..2017 | 115..135 | 20 µm |
| DR3031 | 2017..2047 | 135..165 | 30 µm |
| L31 | 2047..2067 | 165..185 | 20 µm |

The second numeric column (derived local offset) is computed by offsetting from
the L28 top, not from a separately sealed absolute transform. The accepted WP2
receipt seals these twelve receipt-relative source-order transitions; each value
is between the listed preceding and following source rows, never an absolute z
or a physical plus/minus side:

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

The sealed 1 MHz convention is `exp(+j*omega*t)` with
`epsilon*=er*(1-j*tan_delta)` and `G=omega*C_lossless*tan_delta>=0`.
ABF-GL102 uses `er=3.4`, `tan_delta=0.0041`, and imaginary-permittivity
magnitude `0.01394`; EL190T uses `er=4.7`, `tan_delta=0.01`, and magnitude
`0.047`. Copper `59,590,000 S/m` is conductivity only. Because FasterCap's
`C`/`D` input and shell expose no solver-frequency argument, `1 MHz` is a
material-point/conductance interpretation, not a FasterCap frequency argument.

The source-order rows are `SEALED_SOURCE_ORDER_NOMINAL_ONLY`; they do not seal
actual C/D interfaces, reference sides, physical panel sides, or an absolute
source-z transform. Exactly these WP2 items remain `STOP_UNSEALED`:
`xy_dielectric_partitions`, `conductor_layer_void_fill`,
`reference_points_and_panel_sides`, `outer_truncation_and_closure`, and
`absolute_source_z_transform`. Raw complex-matrix sign/reference interpretation
also remains `STOP`. A homogeneous `epsilon_r=3.4` coupon cannot stand in for
the unsealed source-derived interface contract, so `STOP_NUMERICAL_EXECUTION`
remains. See the [D117 WP2/WP3 source-authority audit](D117_WP2_WP3_SOURCE_AUTHORITY_AUDIT.md).

## 5. Full-domain boundary (M2)

“Full domain” has a narrow, auditable meaning here: the full D104 raw 16-cell L28–L31 XY extents/apertures, with no W0 crop and no artificial crop walls; open/infinity-reference raw Maxwell `C`. It does **not** imply a completed stackup or via model: `full_spd_stackup=false` and `full_via_pad_geometry=false` until their authorities are sealed. No transform, `Zii`, PowerSI, or Distribution claim follows from this label. Plane-body-only input may be used only to certify parsing, mesh complexity, memory, and resource ceilings; M2 blocks oracle status until source-derived via/pad/access conductors are available.

## 6. Topology and panel lower bound

D104 reports 31,025 holes, 811,216 unique ring vertices, and 16 components. For a Steiner-free all-T closed extrusion, the topology-only lower bound is

```text
N_triangles >= 4n + 4h - 4c
             = 4(811,216) + 4(31,025) - 4(16)
             = 3,368,900 triangles.
```

This is only a topology lower bound. It is neither an engine expected count nor a resource forecast; quality subdivision, segment recovery, and refinement increase it.

The aggregate floor above is raw-source `R` arithmetic. Cell258's accepted C0
split floor is independently `S=153,246`, `H=2,048`,
`T=157,340`, `Vclosed=306,492`, and `Fclosed=621,172`; it must not be
substituted for the aggregate and it is not a final mesh or Triangle count.

## 7. Immutable D116 result (no rerun)

D116-SHADOW is a three-conductor L29/L30 W0 coupon, not the full domain. Its sealed disposition is `STOP_D116_SHADOW` for **unexpected solver diagnostic**; historical D115C `STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT` remains immutable. The one invocation has been consumed (`numerical_invocations=1`) and must not be rerun.

- FasterCap warned: `Warning: thin triangles (min angle less than 5 degrees) found in the input file` followed by `This may impact the precision of the result due to numerical rounding errors`.
- Preregistered expected solver-panel count was 336; engine input reported 476. Final refined panel count was 43,015. Weighted Frobenius was `0.00405198`.
- The finite 3×3 raw matrix is forensic only. Empty `matrix_stats` and `maxwell_gates`, nonzero return, warning, and the 336-versus-476 discrepancy prevent acceptance. No production, PowerSI, generic four-layer, `C_res`, or Distribution inference is permitted.

## 8. Unsealed prototype mesh evidence

The supplied analysis from `/root/d116_input_cell` is explicitly an **unsealed prototype**, not a frozen receipt and not production code. The current D116 builder used boundary-only CDT with no interior Steiner points.

### 8.1 Observed panels and source corners

Raw panels were T/Q = 164/86. Explicit thin T = 80 plus bad-Q equivalent = 32, for 112/336 triangles below 5 degrees. Source minimum material corners are GND/PWR 90° and DDRL 45°; therefore those thin panels were algorithm-induced, not source-forced.

A deterministic segment-recovered Delaunay prototype produced all-T counts and quality:

| conductor | triangles | minimum angle | maximum aspect |
|---|---:|---:|---:|
| GND | 1,548 | 7.594643° | 7.951165 |
| PWR | 208 | 8.342606° | 7.320246 |
| DDRL | 20 | 7.855620° | 7.385817 |
| total | 1,776 | — | — |

Manifoldness, determinism, and coverage passed in that prototype. Observed dependency identities were NumPy 2.4.4, SciPy 1.18.0, and Shapely 2.1.2. The proposed bounded safeguards of 10,000 points, 50,000 triangles, and 10,000 refinement steps are **W0 three-cell prototype-only values**, not D117 full-domain caps. D104 raw topology implies, for single cells, **F=604,500**, **F=505,360**, and **F=63,864** closed-extrusion face floors for cells 258, 259, and 264 respectively; these are not planar `T` counts. Cell258 alone has 149,078 raw ring vertices, with raw planar `T=153,172`; its accepted C0 additionally records split `S=153,246` and split planar `T=157,340`. Neither value is a final Triangle mesh count. A future C1 must derive and pre-register per-cell/global caps from this sealed C0 and a resource estimate before materialization. The dependency identities, W0 safeguards, and prototype measurements require a future sealed certificate; they do not authorize production code or a solve.

### 8.2 Warning-free certificate gates

The future zero-solver certificate must pass all of the following for every conductor and for the cross-conductor assembly:

- all-T output; FasterCap thin-triangle warning avoidance requires minimum angle `> 5°`; a minimum angle `>= 6°` is the certificate's hard diagnostic margin, with a proposed acceptance target of `> 7.5°`; maximum aspect `<= 8`;
- finite, nonzero coordinates/areas, no duplicate faces;
- every edge incidence exactly twice with opposite orientation;
- Euler characteristic/genus checks;
- signed volume = area × thickness;
- cap coverage and hole preservation;
- cross-conductor nonintersection and required clearance;
- deterministic byte/hash/count replay from the same sealed WKB and manifest.

The proposed `> 7.5°` target is stricter than warning avoidance and must be met: if full-domain geometry fails it, the certificate must `STOP`; it must not silently downgrade to `6°`. The `aspect <= 8` cap is intentionally tight against observed GND 7.951165; failure likewise requires `STOP`. A quality PASS is necessary for input feasibility only, never sufficient for material, via/pad, or numerical authority.

## 9. Panel-runtime acceptance

The sealed all-T input count `N_input` is parsed from the certificate's sealed files. Under the warning-free all-T contract, FasterCap “input panels” and “input panels to solver engine” must both equal `N_input`; a mismatch is a STOP condition, while final refined panel count is an observation only. Official help states that engine count may differ when quadrilaterals are converted, degenerate panels removed, or thin panels regularized (`InputFile_3D_ch03.htm`, `InputFile_3D_ch04.htm`, `Output/Output_ch03.htm`). Those transformations must be absent under this contract.

Only exact lines in a separately preapproved diagnostic registry may be tolerated. Any other warning or diagnostic is `STOP`. D116's registry pair (`Error : Cannot find FastFieldSolvers settings in the Registry` / `Please try installing the software again`) was not whitelisted (`registry_diagnostic_whitelisted=false`) and therefore remains a STOP, not a harmless PASS.

## 10. Separate future numerical authorization (not granted here)

A later one-shot authorization requires a new approval anchor and output directory after WP1–WP4 evidence. At minimum it must specify:

1. exact 16×16 finite CSV/stdout with the 16 labels/order in §3;
2. Maxwell checks (finite, reciprocity, passivity/PSD, row-sum interpretation for open infinity reference, conditioning) and an explicit auto-convergence interpretation;
3. resource caps, Windows Job ownership, 5 s monitoring cadence, terminal/no-descendant evidence, and bounded wall/memory/OOC limits;
4. pre/post input immutability and identity checks, receipt plus sidecar hash, one process, and no retry;
5. `-oi` is itself another FasterCap invocation and is not a free preflight.

The official shell contract requires `-b` for GUI-less mode and `-a<tol>` for automatic solving (`Run/Run_ch02.htm`, lines 25–60). Those facts inform a future manifest only; they do not authorize execution under this D117 STOP.

## 11. Efficiency and coordination decision

Earlier parallel delegation was inefficient for this gate because separate agents owned overlapping contract, runner, and mesh evidence. That caused coordination cycles, repeated reads of the same sealed sources, and ambiguity over which agent had integration authority. This is an observed process explanation, not a measured time or cost claim.

The bounded pattern chosen here is **Sol coordinator → one Luna document executor → same Sol independent review**: one writer, exactly three documentation files, explicit evidence authority, and a single reconciliation point. It preserves independent review while avoiding multiple writers and merge/reconciliation cycles. With the C0 exact-02 re-seal and the WP3 `-05` rerun complete, the consumed `-02` and `-03` C1 one-shots are terminal STOPs; the prelaunch cross-binding code/test checkpoint is independently `CODE_ACCEPT` for static/synthetic evidence only, and no `-04` approval or run exists, while WP2/WP3 authority evidence continues independently. This bounded three-document checkpoint does not authorize solver work.

## 12. Long-running policy

For any future long task, check only at or after **10 minutes** unless a terminal,
failure, or resource event occurs. After 10 minutes, use bounded polls of at
least 60 seconds and report only meaningful changes. During the current STOP,
never poll FasterCap because no process may be launched.

## 13. Next bounded work packages

Execute in this minimal order, with no solver invocation:

1. **C0 exact boundary-clearance — complete (exact-02):** the fresh `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA` re-seal in §3A is current; do not rerun it. It supersedes the stale exact-01 C0 bundle for future planning, while its scope remains frozen split-PSLG clearance only and raw-source clearance remains unknown.
2. **C1/Triangle STOP:** the static/no-Triangle certifier and one-call
   serializer in §3A are accepted design evidence, while the immutable `-02`
   and `-03` one-shots are consumed `STOP_C1_TRIANGLE_SINGLE_CALL` evidence
   and are not reusable. The prelaunch cross-binding code/test checkpoint is
   independently `CODE_ACCEPT` for static/synthetic evidence only, but no
   `-04` approval or run exists; C1/Triangle remain `STOP` pending separate
   immutable approval, while native/Python/allocator feasibility and Triangle determinism
   remain opaque/`NOT_EVALUATED`.
3. **Independent WP2 authority evidence:** do not re-request the completed static material/loss slice. The read-only `WP2-XYD-01` positive-materialization proposal was rejected as premature and closed none of the five blockers; the minimal next package is `WP2-SRC-XY-AUTH-00` explicit-source acquisition, currently `STOP_NOT_REPRESENTED`. Resolve exactly `xy_dielectric_partitions`, `conductor_layer_void_fill`, `reference_points_and_panel_sides`, `outer_truncation_and_closure`, and `absolute_source_z_transform`; current evidence does not derive these blockers and no FasterCap call is implied.
4. **Independent WP3 authority evidence:** the accepted one-shot `-05` rerun remains `PARTIAL`; continue source topology, access-conductor, target-ownership, and nonintersection/clearance evidence. No additional WP3 rerun, solver, Triangle, FasterCap, or PowerSI work is implied.
5. **After a future approved C1 review:** only after a new immutable C1
   approval is reviewed and its exact one-shot executes successfully, prove
   deterministic replay, extrusion/manifold/resource behavior, and only
   afterward derive the aggregate 16-cell certificate. No step authorizes a
   solver by implication.
6. **Later correlation:** after all applicable WP1–WP3 evidence and an independent decision review, create a new one-shot numerical manifest/token only if every gate passes; solver and PowerSI correlation remain unopened.

WP4 waits for all applicable WP1–WP3 evidence. No package changes the M1/M2/M3 boundary by implication.

## 14. Explicit criteria and scope labels

| scope label | PASS means | STOP means |
|---|---|---|
| `FEASIBILITY_GEOMETRY_ONLY` | sealed WKB, all-T quality, topology, replay, and bounded resource certificate pass | any geometry, quality, count, cap, or determinism failure |
| `MATERIAL_AUTHORITY` | source-derived heterogeneous interfaces plus the sealed 1 MHz material/loss convention and frequency semantics are sealed | homogeneous substitute, missing side/reference, or unbounded error |
| `VIA_PAD_AUTHORITY` | source-derived via/pad/access ownership and clearance are sealed | plane-body-only geometry or unresolved ownership |
| `NUMERICAL_ORACLE` | separately authorized one-shot run passes exact output, Maxwell, diagnostics, resource, and immutability gates | any unregistered warning/diagnostic, count mismatch, nonfinite output, process/resource violation, retry, or missing authority |
| `POWERSI_ZII` / `DISTRIBUTION` | not evaluated by D117 | no claim may be made from D117 or D116 |

The current combined status is `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / STOP_C1_TRIANGLE_SINGLE_CALL`; the former `STOP_C1_MEMORY_MODEL_UNPROVEN` label was refined, not cleared. The consumed `-02` and `-03` receipts are immutable STOP evidence and not reusable; the prelaunch cross-binding code/test checkpoint is independently `CODE_ACCEPT` for static/synthetic evidence only, and no `-04` approval or run exists. `STOP_NUMERICAL_EXECUTION` still governs all solver/FasterCap work. `FEASIBILITY_GEOMETRY_ONLY` describes the future certificate scope, not an authorization to run it. No sentence in this document authorizes FasterCap now. The raw-visit cap is exercised by the scoped exact-02 C0 `PASS` above; raw-source clearance remains unknown. See the [WP1 raw-visit occupancy checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#d117-raw-visit-occupancy-checkpoint-wp1-authority) and [Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint-wp1-authority) for authoritative details.

## 15. Traceability and validation record

This file preserves the immutable D116 facts, D104 16-cell contract, D103 material/z evidence, D115B target topology identity, the supplied unsealed mesh prototype, and the accepted Cell258 C0 exact-clearance (including the fresh exact-02 re-seal), static certifier, and future-call preparation evidence above. It also records the consumed C1 `-01` STOP, the accepted root-fixed identities and cap correction, the accepted WP2 static generator/approval/receipt hashes, exactly one static attempt with exit `0`, twelve source-order transitions, and the three promoted WP2 statuses; no Triangle, FasterCap, solver, or PowerSI validation is claimed. The coordinator must reread this file and the [WP2/WP3 source-authority audit](D117_WP2_WP3_SOURCE_AUTHORITY_AUDIT.md), verify the 16-row order/count, hash/size arithmetic, topology equation, transition table, internal STOP/PASS consistency, repository-relative links, literal Windows artifact paths, and that the restricted diff contains only these three documentation files.
