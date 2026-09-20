# ASTRA global-Y finite-port / sheet interface audit

기준: 2026-09-06, branch codex/astra-evaluation-resume-20260906,
HEAD e2f219e71d8c8a397009f72242cce10d78cfc7ab. 현재 코드의 read-only 증거 감사다.
원본 SPD, C1/WP2/WP3, PowerSI, Triangle, FasterCap 및 accuracy_parse.py는 읽거나
실행하지 않았다.

## 결론

현재 가장 작은 물리 후보는 SurfacePatchPlaneOperator.condense_finite_ports
(src/spd_decap_pi/_core/solver/surface_patch_plane.py:509-653)다. 이는
SurfacePatchFinitePort의 정확한 면적 footprint W를 finite_port_projection
(446-507)에서 만든 뒤, A = null(N.T W), Za = R.T solve(K,R),
Yport = A solve(Za,A.T)를 이미 계산하고 residual, reciprocity, passivity,
gauge를 검증한다. 새 generic B Z^-1 B.T adapter를 만들 이유가 없다.

그러나 이 출력은 paired compatible-current terminal model이며 arbitrary absolute
global-MNA nodal block이 아니다. global_mna 모듈 계약도 current differential MFDM
surface operator의 per-column projected nullity는 NodalAdmittanceBlock과 호환되지
않으며 sheet-current PEEC 또는 explicit balanced projection API가 먼저 필요하다고
명시한다 (src/spd_decap_pi/_core/solver/global_mna.py:24-31).

현재 terminal-complete Evaluation의 실제 route는
LayerwiseNetworkSubstrate._solve_all_ports (layerwise_network.py:624-685) ->
CompiledLayerSurfaceNetwork.solve (layer_surface_network.py:1831-1842) ->
evaluator.compile_evaluation_kernel (evaluator.py:371-453)다.
NodalAdmittanceBlock (global_mna.py:217-270)와
supplemental_nodal_admittance seam은 존재하지만 single-frequency only
(1866-1873)이고 live substrate가 이를 전달하지 않는다.

이미 존재하는 end-to-end 연결은
audit_source_plane_patch_shadow_one_frequency_solve의 1 GHz shadow call
(source_plane_patch_consumer.py:3463-3558, solve:3536)뿐이다. 성공 payload도
shadow_only=true, solve_eligible=false, replacement_ready=false,
production_ready=false다 (3557-3558). 따라서 source-bound sheet/return R/L
production replacement는 현재 **STOP**이고, 확인된 최소 path는 shadow probe다.

## source-bound bounded experiment의 입력과 ledger

| 필요 증거 | 현재 요구/근거 |
|---|---|
| P/G launch | layer, net, 정확한 polygon footprint, source 항목과 terminal/surface/island/reduced closure. partial/타 net overlap은 finite_port_projection이 거부한다 (surface_patch_plane.py:446-507). |
| patch 재료 | 모든 conductor 두께/전도도, dielectric separation/Dk/Df 및 provenance. production compiler는 source 행/provenance 없으면 거부한다 (911-968). |
| byte identity | source SHA, raw-v3 manifest/geometry/logical/plane-sheet SHA, ownership certificate/topology SHA, substrate ID, app version. census는 이를 결속한다 (source_plane_patch_consumer.py:3561-3605). |
| P/G closure | 정확히 power/ground 두 binding, 각각 하나의 surface/component/island/reduced closure, 서로 비중첩 (3606-3641). |
| replacement ledger | candidate/retained/excluded partial fingerprint disjoint partition과 replaced/retained owner set; 두 plane scope가 replaced set과 같아야 한다 (3828-3888). |
| 모델 범위 | baseline G/C, 새 sheet R/L, retained Via R/L, termination, source-unproven field를 분리한다. PowerSI는 fit parameter를 공급하지 않는다. |
| port admissibility | 실제 각 footprint의 N.T W rank, return mode, terminal nullity를 보존한다. co-located launch를 separated terminal의 증거로 쓰지 않는다. |

WORK_EXECUTION_BASELINE.md:377-414의 A2는 raw-spatial v3 + ownership IR v2,
source byte provenance, query seam, owner ledger를 확인한다. output은 여전히
shadow_only/replacement_ready=false/production_ready=false이고 (403-409), 새 schema와
38,920-contact P1 condensation은 금지한다 (383-388).

## stamp와 exact exclusion 경계

| 항목 | 현재 stamp | replacement 경계 |
|---|---|---|
| patch vertical G/C | surface patch exact overlap two-terminal stamp (surface_patch_plane.py:656-675) | block가 포함하면 native G/C row를 exact exclusion해야 한다. |
| patch lateral sheet R/L | coupled strip solve (676-715), two-face copper impedance와 dielectric loop term (728-776) | retained Via/loop R/L owner와 절대 겹치지 않는다. |
| production plane G/C | DispersiveAdjacentGap는 partial+dispersion만 가진다 (uniform_c00.py:27-35); partial collapse (layer_surface_network.py:2874-2906), solve stamp (2213-2219) | partial owner ID가 없으므로 ledger-bound partial exclusion 없이는 replacement 불가다. |
| production Via R/L | finite_parallel_rl Laplacian (2200-2246); compile duplicate check는 Via owner만 한다 (2841-2852) | sheet owner와 retained Via owner overlap은 STOP이다. |
| termination | two-terminal stamp (2247-2260) | supplemental owner check도 Via/termination만 본다 (1892-1897, 2011-2022). |
| supplemental | base G/C, Via, termination 후 matrix에 add (2261-2275) | additive shadow 외에는 exact prior exclusion이 필요하다. |

### P1 — same plane G/C double stamp

DispersiveAdjacentGap에 owner_ids가 없고 base partial은 항상 stamp된 뒤 supplemental이
add된다. 따라서 same-plane G/C를 포함한 sheet block은 current seam에서 native G/C와
중복된다. 이는 source-bound replacement의 구체적 정확도 결함 위험이다.

최소 수정/검사: generic adapter가 아니라 source ledger의
fingerprint/partial-ordinal/reduced endpoint를 compile-time base partial selection에 결속해
교체 대상만 exclude하고, Via/termination은 retained 집합으로 fail-closed 처리한다.
native-only와 native-excluded+supplemental의 Y가 같고, native-kept+supplemental가
double stamp임을 보여야 한다.

### P1 — separated P/G finite footprint는 general terminal operator가 아님

condense_finite_ports는 M=N.T W rank가 port 수와 같으면
no admissible return mode로 STOP한다 (surface_patch_plane.py:524-565).
현 positive test는 한 cell의 TOP P/BOT G co-located pair뿐이다
(tests/test_surface_patch_plane.py:306-350). 넓은 P/G plane이어도 P footprint가
column 0, G footprint가 column 1이면 각각의 local nullspace가 W와 독립적으로
만나 rank=2가 될 수 있다. real board lateral ground return을 자동으로 만들지 않는다.

최소 수정/검사: virtual GND, common-mode R/L, ideal P/G short를 추가하지 않는다.
scope를 paired compatible finite-port model로 고정하고 source-proven return mapping 전에는
separated footprint를 STOP하는 negative test를 고정한다.

### P2 — multi-launch nullity와 global gauge가 검증되지 않음

두 co-located launch pair P0,G0,P1,G1의 condensed Y는 두 local pair common-mode
null을 남길 수 있다. global MNA의 structural components는 실제 nonzero Y edge/branch/
constraint로 만들고 (global_mna.py:607-638), component마다 gauge 하나만 추가한다
(641-677). Dense off-diagonal로 하나 component처럼 보이면서 nullity=2이면 hidden
singularity/redundant gauge가 될 수 있다. 단일 2-terminal capacitor PASS는
distributed interface 증거가 아니다.

기존 수학 primitive인 SeriesBranchBlock은 coupled series impedance와
positive-to-negative endpoint incidence를 보존한다 (global_mna.py:274-303).
따라서 future design은 terminal Y absolute stamp를 강제하는 대신
(P0,G0),(P1,G1)의 coupled loop-impedance Za를 branch form으로 전달하거나
explicit balanced-projection constraint metadata를 소비하는 방향을 검토해야 한다.
현재 layerwise supplemental API는 NodalAdmittanceBlock만 받으므로 구현 준비됨으로
주장하지 않는다.

### P2 — live Evaluation은 shadow seam을 전달하지 않음

LayerwiseNetworkSubstrate._solve_all_ports는 network.solve에 supplemental을 넘기지 않고
(layerwise_network.py:624-672), assemble도 input을 노출하지 않는다 (687-758).
그러므로 shadow consumer call은 current product Evaluation replacement가 아니다.
P1 partial exclusion/owner ledger와 P1/P2 port-gauge probe가 통과한 뒤에만 hash-bound
bounded replacement request propagation을 검토한다.

## MFDM과 absolute global-Y의 경계

MFDM은 raster column마다 common-voltage coordinate 하나를 제거하는 relative transform이며
ground나 scalar layer merge가 아니다 (mfdm.py:836-853).
_relative_projection은 u_k=V_k-V_(k+1) local spanning-tree coordinate를 만들고
local reference는 common mode만 정한다 (1013-1064). _port_incidence는 P/G가 다른
raster column이면 명시적으로 거부한다 (991-1010).

필요한 수학 interface는 **source-proven finite-terminal current/voltage congruence map in
a floating quotient**다: local differential coordinate, physical footprint W,
compatible A, source-proven absolute surface-node mapping, component/nullity metadata를
함께 보존해야 한다. local nullspace를 absolute node로 재해석하거나 reference를
global ground로 쓰는 naive MNA는 금지다. surface patch의 naive absolute adapter도
명시적으로 block되어 있고 test가 있다 (surface_patch_plane.py:414-434;
tests/test_surface_patch_plane.py:263-275).

## 최소 canonical interface test

하나의 pytest fixture에 다음 두 part를 둔다.

1. **compatible co-located equivalence.** 2-layer, 2-column mesh에서 양 column에 P/G를
   두고 한 column의 co-located footprint P/G를 condense한다. W, A, row/column zero sum,
   reciprocity/passivity/residual을 assert한다. ledger로 matching native partial을
   제외한 test network에 같은 NodalAdmittanceBlock을 한 번만 넣어 native-only Y와
   동일함을 assert한다. 이 part는 multi-launch proof가 아니다.
2. **separated P/G expected STOP.** 같은 artwork에서 P footprint는 column 0,
   G footprint는 column 1로 한다. condense_finite_ports의
   no admissible return mode raise가 PASS다. virtual ground, G 이동, common-mode R/L,
   ideal short를 넣으면 FAIL이다.

추가 P2 gate는 두 co-located pair(P0,G0,P1,G1)를 actual structural topology와
factor하여 component count, Y nullity, factor/residual을 기록한다. G0/G1 ideal-short는
사용하지 않고 common-reference approximation이면 별도 모델로 표기한다.

## source-bound readiness

| 범위 | 판정 | 근거 |
|---|---|---|
| source-bound G/C census/query seam | PARTIAL | audit_source_plane_source_block_census는 source/raw/substrate/ledger binding과 deterministic census를 제공한다 (source_plane_patch_consumer.py:3561-3906). D-096은 shadow-only/nonreplacement consumed result다 (WORK_EXECUTION_BASELINE.md:952-982). |
| source-bound sheet/R/L global-Y replacement | NO | HQ의 별도 read-only 검증으로 external D104 sealed geometry가 있음을 확인했다: D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01\geometry_receipt.json (19,728 B, SHA256 bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b), cell_0258.wkb (L28 DGND, 2,426,237 B, 2,048 holes, SHA256 1d894ff46db6fdf1e1662d1ae9d45cbb6676b5d002c0357c27c74f9f1e4835e1), cell_0259.wkb (L29 DGND, 2,040,201 B, 4,679 holes, SHA256 a438379bf22b47e412b763eebb49c3aae7252c5872d5ac7c31513499108b9053)다. 두 WKB는 whole-island bbox 약 ±49.7 mm라 작지 않으며, valid composition/source-terminal/material/ownership-complete binding을 제공하지 않는다. 이 감사는 external root를 열지 않았다. 기존 D104 guard는 올바르게 supplemental injection STOP 및 exact bulk-pair replacement 필요를 명시한다 (WORK_EXECUTION_BASELINE.md:1104-1110); P1 double-stamp boundary와 paired-terminal mapping도 여전히 없다. |
| current one-frequency shadow solve | PARTIAL | implementation/test seam은 존재하지만 current test asserted outcome도 numerical STOP shadow output이다 (tests/test_source_plane_patch_consumer.py:640-678). production/replacement evidence가 아니다. |

전체 판정은 **PARTIAL**이다. census provenance와 shadow seam은 확인됐지만,
source-bound bounded sheet/R/L replacement experiment는 현재 **NO/STOP**이다.

## 검토 및 실행 기록

검토 source: surface_patch_plane.py:157-204, 312-434, 446-776, 911-1013, 1263-1318;
mfdm.py:538-678, 836-1064; layer_surface_network.py:1831-1935, 2195-2294, 2834-2940;
layerwise_network.py:399-758, 3689-4421, 5560-5751; evaluator.py:371-626, 1270-1517,
1947-2341, 2360-2735; global_mna.py:20-31, 217-303, 607-677; uniform_c00.py:27-35;
source_plane_patch_consumer.py:3463-3906. 관련 tests:
test_surface_patch_plane.py:263-350, test_mfdm_solver.py:541-548,
test_layerwise_network.py, test_layer_surface_network.py,
test_source_plane_patch_consumer.py:640-748.

실행:
- bundled Python으로 source/test 11개를 utf-8-sig read+compile: syntax=ok files=11.
- 계획한 focused pytest는 surface patch condensation, MFDM cross-column STOP,
  layerwise external-device-port test였다. system Python/py launcher에 interpreter가
  없고 bundled Python에는 pytest module이 없어 실행하지 않았다. install/retry는 하지 않았다.
- docs/tests/src만 대상으로 sealed .sqlite/.sqlite3/.db/.wkb/receipt/manifest를
  검색했고 in-checkout 후보는 없었다. HQ가 별도 read-only로 검증한 external D104
  geometry receipt/WKB는 위 readiness 표에 반영했으나, 이 감사는 external/consumed
  root를 열지 않았다.

거절한 설계: generic B Z^-1 B.T adapter, naive absolute MNA, co-located virtual ground,
G0/G1 ideal-short, common-mode R/L 추가, 새 DB/schema, 38,920-contact condensation.
이들은 current finite-port/nullspace 또는 source/owner evidence를 우회하거나 G/C,
loop/Via loss를 중복할 수 있다.
