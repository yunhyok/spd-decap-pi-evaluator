# Evaluation / De-cap Distribution 검증 기록 — 2026-08-06

> **Historical v0.21.0 record.** v0.22.0 supersedes the numerical-solver and
> release conclusions in this document with the separate
> [layer-surface validation record](EVALUATION_LAYER_SURFACE_VALIDATION_2026-08-06.md).
> The measurements and artifact identities below are retained unchanged for audit.

> 대상: **SPD Decap PI Evaluator v0.21.0**
>
> 목적: De-cap Distribution 이후 Evaluation Analysis 연계 오류의 재현, 수정 계약,
> 실제 SPD/PowerSI Touchstone 비교 결과와 출시 전 남은 검증을 감사 가능한 형태로 기록한다.

## 1. 상태 표기

| 상태 | 의미 |
| --- | --- |
| `PASS` | 현재 작업 트리에서 결과와 근거 파일을 확인함 |
| `MEASURED` | 수치를 측정했으나 정확도 합격 기준을 충족했다는 뜻은 아님 |
| `PENDING` | 구현 또는 출시 전에 결과를 채워야 하며, 이 문서에서는 성공을 주장하지 않음 |

## 2. 입력 자료와 무결성

| Case | Source SPD | PowerSI Touchstone | SHA-256 |
| --- | --- | --- | --- |
| 260729 | `D:\S4LB002-2Para_260729_1_injected.spd` | `D:\S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p` | SPD `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2`; S92P `c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11` |
| 260804 | `D:\S4LB002-2Para_260804_1_injected.spd` | `D:\S4LB002-2Para_260804_1_injected_080526_104445_27112_S.s92p` | SPD `45253f438fc7c328c50364fe610a7ecfbf72ca842a032921fbbb8d645e2a4f35`; S92P `cd103f42412c2a63518105d7e10fae8a0538c84982e1eddbfb74829a6972951b` |

두 Touchstone은 모두 92-port, `# Hz S RI R 1`, 826 frequency record
(DC 1 + positive frequency 825)이며 canonical rail 순서와 주파수 grid가 일치한다.
PowerSI header의 두 정확한 형식, 즉 legacy `2nd_SITE0/1-<rail>`과 run-qualified
`SITE0/1_0805-<rail>`을 지원하되, header의 SITE prefix와 rail suffix가 일치하지 않거나
중복되면 fail-closed 한다.

## 3. 장애 재현과 수정 계약

| 점검 항목 | 결과 | 상태 |
| --- | --- | --- |
| 첨부 화면 | 92개 선택 rail 중 72개, 472개 connection classification 차단 | 사용자 관측 |
| 기존 Distribution replay의 수정 전 connectivity-only 재현 | 92 rail, 10,757 decap 중 72 rail의 470개가 `current-rail eligibility is missing`으로 차단 | `PASS` |
| 470개 분류 | 모두 enabled이며 원래 source rail에서 이동하지 않은 `DIRECT` connection | `PASS` |
| source-DIRECT fallback 적용 후 같은 replay | 696 moved, 212 disabled; 전체 92 rail의 **connectivity-only** blocker 0개 | `PASS` |
| fresh 260804 exact connectivity/modelability preflight | 2,010 blocker, 68/92 rail; GND 1,232, PWR 778; DIRECT 1,574, shared 436 | `PASS` |
| fresh 260804 VQPS control | 10/10 rail clear, blocker 0개 | `PASS` |

화면의 472개와 현재 파일 재현의 470개 차이는 입력/실행 시점의 상태 차이로 기록한다.
수정의 핵심은 blocker를 무시하는 것이 아니라 다음 증거 규칙을 preflight와 project builder에
동일하게 적용하는 것이다.

1. exact `RailEligibility`가 있으면 항상 이를 우선 사용한다.
2. exact eligibility가 빠진 경우 fallback은 **원본 source assignment의 unchanged `DIRECT`**에만 허용한다.
3. fallback에는 PWR/GND landing 증거, current/source rail 및 net의 정확한 일치,
   모든 PWR landing net의 source 일치가 필요하다.
4. fallback template은 import metadata provenance를 우선하고, 없을 때만 layer-pair 근거를 사용한다.
5. Distribution으로 이동한 connection은 exact eligibility가 없으면 계속 fail-closed 한다.

따라서 470개 누락 기록의 복구와 2,010개 실제 geometry/modelability blocker는 서로 다른
결과다. 원본 import 증거 누락은 복구하지만, 재배치된 decap의 모델 가능성을 추정으로
승인하지 않는다. Exact finite-port footprint gate는 project builder와 같은 terminal 중심,
크기 및 plane bound를 사용하며 좌표 clamp, cavity 임의 확장 또는 blocked port 삭제를
허용하지 않는다.

Comparison preflight는 UI worker에서 선택한 rail의 **Original baseline**과
**Tuned/current**를 모두 실제 build-time 계약으로 검사한다. Original에서 mounted였지만
현재 disabled인 De-cap의 Original footprint, 누락된 model ID, exact eligibility 및 source
fallback까지 양쪽에 동일하게 적용한다. 한쪽이라도 build 불가이면 그 rail은 blocked다.
Clear/blocked가 섞인 경우 전체 manifest를 보여주고 기본값 `No`인 확인을 받은 뒤에만 clear
rail을 실행한다. 결과는 `PARTIAL`이며 blocked rail은 `NOT evaluated`로 남는다. Clear rail이
없으면 Evaluation을 시작하지 않는다.

### 3.1 Active-checkout fresh post-Distribution replay

현재 active checkout에서 fresh 260804 candidate와 target workbook을 다시 불러와 수행한
post-Distribution replay를 authoritative end-to-end evidence로 사용한다. 이는 위의 과거
connectivity-only replay(696 moved, 212 disabled)와 별도 실행이며, 이번 fresh plan의
isolation sacrifice는 208개다.

| Stage / result | 측정 결과 | 상태 |
| --- | --- | --- |
| Scenario load | 5.569초 | `MEASURED` |
| Target workbook load/validation | 2.340초 | `MEASURED` |
| Eligibility proof projection + validation | 53.167초 | `MEASURED` |
| `NEAREST` planner | 157.501초 | `MEASURED` |
| Atomic Apply | 8.345초 | `MEASURED` |
| Distribution result | `FULL`; requested 696, fulfilled 696, shortfall 0 | `PASS` |
| Physical changes | assignment move 696, isolation sacrifice 208 | `PASS` |
| Route metadata after Apply (historical v0.21 TOP/L02 rail inventory) | recovered/requested 11,874/117,810, fallback 105,936; fresh source와 동일하며 current v0.22 multilayer target inventory와 직접 비교하지 않음 | `HISTORICAL PASS` |
| Post-Apply Original/Tuned preflight | blocker 2,807개, 73/92 rail blocked, 19 rail clear | `PASS` |
| Blocker provenance | fresh-source와 공통 2,010개, Tuned-only 797개 | `PASS` |
| Blocker class | geometry 2,802개, connectivity 5개 | `PASS` |
| VQPS control | 10/10 clear | `PASS` |

Fresh source 상태의 2,010 blocker/68 blocked rail은 Apply 전 exact geometry/modelability 결과다.
Distribution 후 comparison preflight에서는 Original에 그 source 상태를 보존하면서 Tuned/current의
이동 assignment를 함께 build하므로, 공통 2,010개에 Tuned-only 797개가 추가되어 2,807개/73
blocked rail이 된다. 따라서 `FULL` Distribution은 수량/topology 목표 696개를 전부 충족했다는
뜻이지, 92개 rail 모두의 Evaluation이 가능하다는 뜻이 아니다. 19개 clear rail만 mixed-selection
partial 실행 대상이고 VQPS 10개는 그 clear set에 유지된다.

All-rail Original/Tuned comparison preflight는 ProjectSpec/design fingerprint 재사용과 무관 rail
cluster 유도 조기 생략을 적용한 뒤 다시 측정했다. candidate load 4.174초, 92-rail shared
preflight 10.083초, total 14.256초였고 blocker manifest(2,010개/68 rail)는 보존됐다.

## 4. VQPS control 및 PowerSI 비교

`VQPS`가 포함된 rail은 두 파일에서 정확히 10개이며 Touchstone port 42–46, 88–92에 해당한다.
이 10개 rail에는 decap이 0개이고 Original/Tuned exact preflight blocker도 0개이므로 no-decap
control로 사용한다. 현재 production solver (`modal-mvp-0.7.0`, mode 6)의 측정 결과는 다음과 같다.

| Case / Group | Completed | Modal-converged | RMS error dB (min / median / max) | p95 absolute error dB median | 상태 |
| --- | ---: | ---: | ---: | ---: | --- |
| 260729 VQPS development | 5/5 | 5/5 | 4.242 / 4.353 / 6.466 | 4.795 | `MEASURED` |
| 260729 VQPS holdout | 5/5 | 5/5 | 4.221 / 4.431 / 6.650 | 5.078 | `MEASURED` |
| 260729 loaded final set | 6/6 | 0/6 | 1.199 / 2.604 / 4.253 | 4.091 | `MEASURED` |
| 260804 VQPS development | 5/5 | 2/5 | 4.1521 / 4.1920 / 6.1884 | 4.3909 | `MEASURED` |
| 260804 VQPS holdout | 5/5 | 2/5 | 4.09845 / 4.31205 / 6.20376 | 4.32034 | `MEASURED` |
| 260804 loaded final set | 0/6 | n/a | preflight에서 전부 blocked | n/a | `PASS` |

근거 report:

- `validation-output\v0.21.0-correlation-260729-mode6-current\correlation_report.json`
- `validation-output\v0.21.0-correlation-260804-fresh-route-current\correlation_report.json`

Fresh 260804 candidate benchmark 실행 시간은 39.117초였고 execution status는 `partial`이었다.
Loaded 6개 rail은 VTRIP site 0/1에서 166/124개, VINT site 0/1에서 56/64개,
VCPU site 0/1에서 32/48개로 합계 490개의 exact modelability blocker가 있어 실행하지 않았다.
반면 VQPS development/holdout 10개 rail은 모두 완료되었다.

이 수치는 기능 연계가 정상화되었음을 검증하는 자료이지 numerical accuracy 합격 선언이 아니다.
VQPS에서 모델 impedance가 PowerSI보다 약 4–6 dB 높게 나타나는 체계적 한계가 남아 있다.
bulk-C prototype도 약 1.7–2.1 dB까지 개선했지만 capacitance 오차가 16–22%로 목표 `<=5%`를
충족하지 못했다. 따라서 이 release에는 fitted scalar나 실험 solver를 승격하지 않는다.
260804는 stack, plane pair, geometry가 다른 독립 holdout으로 유지했다. 결과를 본 뒤 fitted
수치 tuning에 사용하지 않았으며, fresh report에서도 loaded rail을 조용히 생략하거나 성공으로
간주하지 않고 blocked entry와 `partial` scope를 보존했다.

## 5. Shared-pad 대형 cluster 자원 계약

260804의 최대 cluster `SPDCL:0775fe3a08d7ca37`는 enabled member 153개,
PWR path 162개와 GND path 162개, 합계 324개 path를 가진다. 일반 shared-pad network는
주파수별 dense `N x N` terminal admittance를 만들므로 기존 128-path 제한을 유지한다.

324-path cluster는 다음 조건을 모두 만족할 때만 exact aggregate arrowhead stamp를 쓰는
batched 경로(상한 512)를 허용한다: PWR component 1개, GND component 1개, 모든 terminal이
동일한 fallback rail template을 사용하고 source R/L path 증거가 없음. 조건이 하나라도 다르면
128 초과를 거부한다. Leave-one-out sensitivity는 populated/physical dense matrix 두 개가
필요하므로 128-port 초과를 allocation 전에 명시적으로 거부한다.

실제 153-member cluster의 324 path project build는 2.688초에 완료되었다. 같은 규모의 dense
sensitivity 두 matrix 추정치는 약 1.285 GiB인 반면, exact batched population은 약 0.12 MiB,
modal stamp는 약 14.69 MiB였다. 이 측정은 제한된 동일-template aggregate case의 자원 계약을
검증할 뿐 일반 324-port dense sensitivity 허용 근거가 아니다.

최종 full-suite 회귀 결과는 아래 출시 checklist가 완료될 때 갱신한다.

## 6. 260804 off-cavity / root route-recovery 이슈

대형 cluster 제한을 통과시킨 뒤 260804 `VTRIP0` evaluation에서
`finite GND port ... C6906_0 ... lies outside the rectangular plane`이 재현되었다.
해당 PWR cavity bbox는 x `-22933..42061 um`, y `200..41363 um`이고,
shared/direct landing 중 166개가 이 cavity 밖에 있었다. 대표적으로 왼쪽 GND via는
x `-23333 um`, C6906 계열은 x `42550 um`이다.

Raw-SPD route recovery의 기존 250,000 relevant-Trace fail-all guard는 compact exact DSU로
교체했다. Canonical `Node<number>`는 dense numeric index를 사용하고, noncanonical/sparse
node와 cross-net collision은 exact sparse map으로 전환한다. Alternate-exit semantics와
start-node identity를 보존하며 MemoryError 또는 index overflow 시 전체 path를 legacy template로
fail-safe fallback한다.

| 확인 항목 | 결과 | 상태 |
| --- | --- | --- |
| historical v0.21 fresh 260804 source paths (TOP/L02 rail inventory) | requested 117,810; recovered 11,874; fallback 105,936; recovered segments 11,874; recovered path는 모두 `Signal$L02(DGND)` target | `HISTORICAL PASS` |
| recovery 입력 규모 | relevant Trace 1,405,367; Trace node 1,915,758; directed trace-incident Via edge 2,235,995; retained node 2,293,666 | `MEASURED` |
| historical v0.21 fallback 사유 | ambiguous branch 91,770; target pad unsupported 11,526; no monotonic Via 2,640 | `HISTORICAL PASS` |
| current v0.22 r4 multilayer compatibility paths | requested 200,928; recovered 0; rail-template fallback 200,928; ambiguous trace branch 194,064; no monotonic Via 6,864 | `MEASURED` |
| fresh raw import runtime | 총 360.07초; analyze 122.61초, plan 85.32초, index 1.16초, recovery 130.52초, eligibility 3.94초 | `MEASURED` |
| route-recovery focused test | `tests/test_io_spd.py`: 55 passed | `PASS` |
| fresh 260804 exact preflight | 2,010 blocker, 68/92 rail; route recovery 전후 blocker 수 감소 없음 | `PASS` |
| optimized Original/Tuned preflight runtime | candidate load 4.174초; 92-rail shared preflight 10.083초; total 14.256초 | `PASS` |
| six loaded benchmark rail | exact blocker 490개; 6/6 preflight blocked | `PASS` |
| VQPS control | 10/10 clear | `PASS` |

근거 candidate와 report:

- `validation-output\v0.21.0-correlation-260804-fresh-route-current\S4LB002-2Para_260804_1_injected_candidate.spdpi`
- `validation-output\v0.21.0-correlation-260804-fresh-route-current\correlation_report.json`

위 11,874개 source path 복구는 당시 v0.21 TOP/L02 rail inventory의 historical result이며,
복구된 path는 모두 `Signal$L02(DGND)`를 target으로 했다. Current v0.22 multilayer rail inventory는
해당 L02 target을 요청하지 않으므로 r4의 0/200,928 compatibility recovery와 직접 비교할 수 없다.
v0.21 범위에서는 이 복구가 실제 route provenance를 늘렸지만 2,010개 finite-port geometry
blocker를 제거하지 않았다. Route evidence는 현재 선택된 단일 rectangular cavity 밖의
terminal을 자동으로 다른 cavity에 연결했다는 증거가 아니다. v0.21.0은 좌표 clamp, cavity 확장,
port 삭제를 하지 않고 PWR/GND footprint overrun을 structured blocker로 유지한다. 이 때문에
mixed-selection partial Evaluation이 필수이며, blocked loaded rail에 numerical accuracy 성공을
주장하지 않는다.

## 7. UI/UX 변경 계약

- Distribution table의 `Actual Change` 표시를 receiver 기준 `Assignment Failed = max(Target - Actual, 0)`로 교체한다. donor/exchange row는 실패로 오인하지 않도록 0으로 둔다. Workbook format 3은 감사용 `Actual Delta`를 유지하되 기존 `Actual Changed` 열을 `Assignment Failed`로 교체하고 legacy workbook import는 계속 허용한다.
- Apply 후 requested target을 보존하여 partial 결과의 실패 수량을 계속 확인할 수 있게 한다.
- main board와 detached view에 `Show source SPD assignments`를 제공하고
  `Current / distributed`와 `Source SPD (read-only)`를 명시한다.
- source 보기에서는 context edit를 차단하고 search/selection/viewport를 표시 상태와 동기화한다.
- Evaluation preflight는 background worker에서 Original/Tuned 양쪽을 검사한다. Mixed selection은
  전체 blocker manifest와 default `No` 확인을 보여주며, 승인 시 clear rail만 실행하고
  `PARTIAL`/`NOT evaluated` scope를 Evaluation Summary와 status에 유지한다.
- preflight blocker dialog는 compact 요약과 `Show Details`의 전체 manifest로 분리해 첨부
  화면처럼 거대한 message box가 되지 않게 한다.

## 8. 출시 전 검증 checklist

| 항목 | 증거 | 상태 |
| --- | --- | --- |
| Route-recovery focused tests | `tests/test_io_spd.py`: 55 passed | `PASS` |
| Touchstone/benchmark focused tests | strict manifest 및 partial execution 포함 38 passed | `PASS` |
| Fresh active-checkout Distribution replay | `NEAREST FULL` 696/696, shortfall 0, move 696, isolation sacrifice 208, Apply 후 blocker manifest 확인 | `PASS` |
| Final Original/Tuned comparison-preflight focused rerun | Evaluation/GUI 104 passed; worker-chain regression 5 passed | `PASS` |
| Optimized all-rail comparison-preflight performance rerun | 10.083초; 2,010 blocker/68 rail manifest 보존 | `PASS` |
| 전체 pytest suite | direct rerun 765 passed in 47.65초; packaging rerun 765 passed in 41.35초 | `PASS` |
| 실제 UI: v0.21.0 title, source/current toggle, read-only, Assignment Failed, compact error UX | 실제 10,757-decap replay를 로드하여 Current/Source 양방향 전환, source read-only label, 모든 Distribution 모델의 Assignment Failed header, 18 clear/74 blocked compact partial dialog와 Show Details manifest, default No 취소 후 solver worker 미시작을 확인 | `PASS` |
| Built EXE smoke | `dist\SPDDecapPIEvaluator\SPDDecapPIEvaluator.exe`; File/Product version 0.21.0; `--smoke-test` exit 0 | `PASS` |
| Installer build/install/smoke | `installer-output\SPDDecapPIEvaluatorSetup-0.21.0.exe`; 85,936,608 bytes; SHA-256 `2e04203b2837c6d4dc9e59d14a0198f2e3fd153a896c777558dbb29843d679d8`; custom install의 File/Product version 0.21.0 및 `--smoke-test` exit 0 | `PASS` |
| GitHub branch/tag/release/assets | private repository release `v0.21.0`; installer와 checksum asset 재다운로드 및 SHA-256 일치 확인 | `PASS` |

v0.21.0 publication gate는 완료되었다. 이 문서는 v0.21 기록이며 v0.22.0
release evidence를 대신하지 않는다.
