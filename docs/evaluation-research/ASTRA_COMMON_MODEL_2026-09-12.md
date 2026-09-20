# SPD Decap PI Evaluator v0.23.1 — 공통 모델 소유와 소형 대조

2026-09-12 사용자 재개 지시 적용. [수정 계획](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/9078b7ed88a932e592c1b20a51aaeb3b28dc4ebc/docs/peer-review/2026-09-10/NEXT_PLAN.md)과 같은 commit의 Red Team 응답을 로컬 문서와 내용 대조했다(두 파일 일치). 이 문서는 소유·판별용이며 정확도 개선 보고가 아니다.

## 고정 조건과 출처

- 작업 HEAD: `e2f219e71d8c8a397009f72242cce10d78cfc7ab`, branch `codex/astra-evaluation-resume-20260906`. 기존 미커밋 연구 소스는 commit만으로 특정되지 않으므로 아래 frozen driver/artifact를 함께 고정한다.
- rail `ADC_VDD_075_VTRIP_SRAM/0`, port18 `2nd_SITE0-ADC_VDD_075_VTRIP_SRAM/0`, source-enabled decap421개. 저장 전체 Scenario decap11050개. 선택은 참조 오차를 보기 전에 고정한 non-holdout development 조건이다.
- device +node `spd-device-port-node:820d47f6bf1a0a52a37b4706`, −node `spd-finite-via-vertex:1e9e3a9fb6978e92411bce4c`. 1A 여기. 참조는92-port 전체 S→Z 변환의 Zdd, 1Ω는 파동 기준 임피던스이며 모든 포트에 추가하는1Ω 부하가 아니다.
- 보드 우선 확인점1MHz/10MHz, 목표 대역1kHz–100MHz;1GHz 보조. 기존 W5의100kHz–100MHz/241점 및 rail별 정책은 유지한다. 이번 소형 수치 예산은 제품 승인 기준과 구분한다.
- 최신10MHz 모델: `compare_astra_l04_10mhz_forward_closed_gcrotmk.py` SHA `466cd9ef7e10d1d39b3821040c79899690170e5bc17e7d217023565e4683a087`; 결과 SHA `30045d8ac4540b096f9b26c75238c5d9e98e1375efc3fb1c5f4b888141fd2128`. 수렴하지 않은 조건부 모델이다.
- 별도 역사적1MHz 모델: `astra-l02-hybrid-right-correction-01/result.json` SHA `7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3`. 그 전류 census를 최신10MHz의 분포로 사용하지 않는다.
- 선택/참조: `astra_loaded_development_selection_2026-09-07.json` SHA `99d0f77fcceb9d07b97aa65953dd42b8d106b7f2485f42f11ee643134d64b8f9`; `astra_loaded_development_reference_2026-09-07.json` SHA `761d61334678ceaca0db55bc8c5539bb080ee36363eae33a2c419a1ea5c06430`. 원 SPD/Touchstone/Scenario DB를 재파싱하지 않았다.

## 소유·출처 표

I=포함 근거 있음, E=현재 해당 항 제외, U=범위 또는 분해 미확인. I는 물리 정확도나 최신 해의 수렴을 뜻하지 않는다. 표의 부분 계수는 다른 모델에서 중복 합성하지 않는다.

| 경로/모델 | R·내부 L | 외부 self/mutual L | G/C | 접점·귀환 경계 / 미확인·중복 위험 | 담당 식·코드·저장 근거 |
|---|---|---|---|---|---|
| 최신10MHz L25, Signal$L25(MAIN_POWER4) | I: RT0 DC R. 내부 확산 E | I: L25 self+shared-edge 보정+centroid 상호; L04와 양방향 상호. 다른 층/비아와의 벡터 상호 E | I: 분산 G/C category, native partial의 동등 비율 검증 | 보드 RT0 전류+원 회로 incidence. 선택 rail의 정확한 island별 역할은 이 표에서 U | `probe_astra_l25_l04_joint_magnetic_action.py::joint_action`; `assemble_astra_full_contact_frequency_operators.py:78–144` |
| 최신10MHz L04 sheet | I: R와 H=CᵀRC. 내부 확산 E | I: L04 self+centroid 상호+L25 cross. L04 shared-edge/기타 near 적분 보정 E | I: 주파수별 delta/U/D bridge. 완전한 전역 charge/비인접 전기장 U | q04=P g+Cψ; B P=E, B C=0, 접점전류와 순환공간 유지. root contact는 gauge이며 이상적인 전체 return plane 대체가 아님 | `prepare_astra_l04_contact_ntd_action.py`; forward driver `a_apply`; joint `joint_action` |
| 최신10MHz L02 cell 및 접점 | I: retained hybrid cell/contact 모델. 내부·외부 L 분해 U | L25/L04 joint의 대상 E. 다른 source scalar L 존재와 구별 | I: frequency-matched cell/contact Y |20개 circuit/contact junction binding. 각 junction의 최신 전체 pad/via/return 물리 완전성 U | `assemble_astra_full_contact_frequency_operators.py::worker`, categories/`binding`/`cell_result` PINS |
| 최신10MHz L14 및 retained partials | I: unchanged DC sheet R / native finite scalar R | retained finite scalar L은 I; full vector self/mutual 소유 U, joint 대상 E | I: retained native dispersion, L14 distributed G/C | layer/net/island별 정확한 분해와 비인접 상호 U | 위 frequency assembler의 `retained_gc`, `l14_sheet_dc`, `l14_distributed_gc` |
| finite Via/trace 및 composite 경로 | I: Y=n/(R+jωL), saved source R/L. scalar L의 내부/외부 분할 U | scalar L I; 이것을 full vector self/mutual로 간주하지 않음. 새 PEEC 추가 시 중복 위험 | 별도 native/분산 category가 소유; branch scalarRL에 임의 C 추가하지 않음 |20 split-leg0와 source Via/trace ownership 연결. segment별 최신 전류 및 pad/antipad/spreading U | frequency assembler `source_r/source_l/source_count`; 역사적 `astra-all-finite-current-l-ownership-01/result.json`, 단위 정정 `…-02/result.json` |
| port18 및 loaded terminations | 기존 저장 source-mounted component model I; 종류별 ESR/ESL 소유는 component provenance 유지 | mounted ESL과 새 접속 L의 중복 여부 U | I: 저장11050 termination의 주파수별 stamp | 여기 +/−node 고정. source pad 면적·연결과 참조의 완전한 동등성 U;421은 선택 rail 수이며 전체 부하 수가 아님 | `astra_native_loaded_development_rail_2026-09-07.json`, `astra_loaded_component_models_2026-09-07-02.json`, frequency assembler |
| 기타 net/layer/island/귀환 | native source 소유를 일괄 제거하지 않음; 세부 U | L25/L04 두 sheet 외 전역 상호·귀환 완전성 U | retained source partials I, 전역 완전성 U | saved census의 큰 전류/90%만으로 에너지 또는 오차 지배성 주장 금지 | frozen source ownership/census와 최신 A의 범위를 분리 |
| 기존 MFDM 재사용 후보(최신 A와 별도 모델) | I: shared-sheet loss, two-face Zc·coth/csch 확산 | I: 인접 gap의4단자 loop. 비인접 슬롯 결합 experimental | I: supplied cut-cell dielectric gaps | 두 conductor 모두 유한. 새 partial L과 gap loop를 더하면 동일 외부 에너지 중복 가능 | `src/spd_decap_pi/_core/solver/mfdm.py:1–18,735–825`; 실제 호출·전역 연결 없이 현재 A의 소유로 주장하지 않음 |
| 기존 modal 재사용 후보(별도 모델) | I: one-face Zs=√(jωμ/σ)coth(t√(jωμσ)) | I: rectangular cavity의 별도 외부 gap 항 | I: constant mode Cplane, finite-port/decap stamp | 직사각 기하/finite port 조건. 임의 보드와 동일하다고 간주하지 않음 | `src/spd_decap_pi/_core/solver/modal.py:1–8,44–102` |
| 기존 via_peec 기준 블록(별도 모델) | I: solid-cylinder 내부 Z | I: finite parallel z-filament Neumann partial L | pads/antipads/planes E | straight vertical solid-filled만. `global_mna_composable=False`; 실제 exact−core 빼기 없이 전역 합성 금지 | `src/spd_decap_pi/_core/solver/via_peec.py:1–12,41–99` |

`astra-step4-basis-01/ownership-basis.json`의 예시 surfaces/contact는 VQPS_SYS_1_AON/0의 L30/L29이다. 이를 VTRIP port18의 island/contact 증거로 재사용하지 않는다. 해당 rail의 미확인 소유를 이 자료로 채우지 않았다.

## 판별할 질문과 소형 대조 계약

보편적인 R-only 실패나 L 누락은 확정하지 않는다. 우선 동일 A에서 직접/반복 해의 포트 차이가 수치 오차임을 검사할 수 있는 **실제 접속·귀환을 유지한 기존 소형 구조**를 고른다(Sol가 경로 확인 중). 분리된 via 또는2×2 장난감 행렬은 전체 경로의 대표 문제로 승격하지 않는다.

선정 후 source geometry/material/port/termination, branch 및 charge 공간, 내부/외부 소유를 고정한다. 직접/반복 대조에서는 A,b,scaling,port가 완전히 같아야 한다. 같은 물리의 두 해상도 비교 뒤에만 한 물리 항을 바꾼다. 예상 수치 예산: backward error≤1e-10 및 복소 port 차이≤max(1nΩ,1e-6·|Zdirect|). 공간 차이≤1%는 이 대표 구조를 선별하는 연구 기준이며 제품 W5 완화가 아니다. 작은 구조에서도 기준해가 없거나 물리 경계가 다르면 큰 보드로 확대하지 않는다.

초기120초/2GiB는 저비용 후보 탐색 참고치이며 사용자 제약이 아니다. 이 한도를 맞추려고 필요한 물리를 제거하지 않는다. 실제512GB·Threadripper/GPU 장비 확대는 공통 모델을 유지하며 측정 후 적용하되 현재 노트북의 최소 기준해를 먼저 구한다. 사용자는 수정 계획 전체의 실행 재개를 승인했다. guard checkpoint는 저장·사용량 확인 경계이며 재개 승인을 반복해서 요구하거나 작업을 중단하는 근거로 쓰지 않는다. 추가 Reset/구매 없음. 현재 turn의 실제 service tier는 노출되지 않아 확인 불가.

## 소형 대표 구조 탐색 결과와 실행 경계

Sol/high의 제한된 소스 탐색과 HQ의 원문 확인 결과, 조사한 기존 예제 중 요구 결합을 모두 갖춘 실행 가능한 소형 문제를 찾지 못했다. 가장 가까운 실제 기하 seed는 다음과 같다.

| 기존 후보 | 확인한 기능 | Step2에 부족한 것 / 이번 실행 |
|---|---|---|
| `prepare_astra_conforming_power_joint.py` → `astra-boundary-conforming-power-joint-01/joint-template.npz` | source PWR 두 post/bridge,5304 tetra. 저장 artifact SHA `14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb` | lower contact conforming split, finite GND return, port closure 및 complete all-row vector/scalar Green이 미완. 이번에 조립/solve하지 않음 |
| `qualify_astra_conforming_power_joint_sparse_current.py` → `astra-boundary-joint-current-space-01/current-space.npz` |12546 RT0 current, volume5304+boundary3876 charge supports, circulation 가능한 공간 | 전류 공간 생성은 full port A의 생성이 아님. complete neutral-charge/contact constrained system은 미완 |
| `tests/test_mfdm_solver.py::test_three_conductor_cell_matches_hand_dense_mna` | 같은 slab MNA의 독립 직접 oracle, finite dielectric charge | pad/via 접속과 해당 current/charge field 공간을 포함하지 않음. 대표 문제로 실행·보고하지 않음 |
| `tests/test_tri_fem_sheet.py::test_contact_stamp_matches_dense_pseudoinverse_and_global_mna_oracle` | single sheet finite-contact/port oracle | via/return pair 없음. 다른 모델과 임의 합성하지 않음 |
| `tests/test_via_peec.py`의 unequal P/G array | via/return current 방향과 부분 L | isolated/non-composable, pad/plane/charge 미포함 |
| `diagnose_astra_3d_current_charge_field.py` |6tet,24current와23독립 neutral-charge, full retarded field, loop/range | plane-wave 여기의 단일 box. 실제 port–pad–via–PWR–return, h refinement 없음. excitation만 바꿔 대표 port 해로 부르지 않음 |

**실제 기하 재사용 seed:** PWR 두 post/bridge. 이는 조사된 실제 기하 중 가까운 출발점이며21k 이상의3D 구현을 기본 경로로 확정한 것은 아니다. 이 seed를 사용하는 대조 사양은 lower R20 contact의 partial triangle을 conforming하게 분할하고, 하나의 finite GND post와 return patch 및 differential1A port를 연결하는 것이다. 두 conductor의 모든 RT0 current/circulation, neutral volume/boundary charge, contact KCL·potential을 유지한다. top/bottom contact 면에서 port 주입과 boundary charge의 중복 계상을 제거하는 공통 식을 먼저 확정해야 한다. 내부 sigma·외부 L·scalar Green을 한 번씩 소유한다. isolated via_peec를 그대로 stamp하지 않는다. 더 작은 canonical 구조로 갈 경우에도 동일 결합·물리·경계를 두 풀이 모두에 고정해야 한다.

완성된 동일 `A_h,b,port,scale`를 한 번 조립해 pivoted LU 기준해와 동일 A의 iterative 해를 비교한다. 다음으로 동일 물리의 h 또는 구적 수준 두 개를 각각 수렴시킨다. 그 뒤에만 한 물리 항의 효과를 평가한다. 기존 선택2current+2charge row 검증이나17개 action column은 full A 기준해를 대신하지 못한다. 이 seed의3D h hierarchy도 아직 없어 기존2D sheet refinement로 대체할 수 없다.

현재 seed만도 constraint 전21726 field DOF이며 dense complex matrix 하나는7,552,305,216B(약7.03GiB)이다. return과 constraint/LU/workspace는 추가된다. 이 산술은 Sol의 약7.55GB 표현을 bytes로 독립 확인한 것이며 메모리 적합성 판정이 아니다. 이번 호스트는 실제 확인 시 i9-12900H/20 logical CPU, RAM68,374,552,576B, 가용44,173,881,344B였다.512GB Threadripper 호스트가 아니다. 목표 장비의 접속·실행 위치는 이 세션에서 확인되지 않았다.

**단계1 경계 판정:** 소유 표와 대표 대조의 부족 조건/사양은 작성 완료. 이 기록 시점에는 same-A 직접/반복 결과, 메시/구적 차이, 물리 수정 효과가 없으며 정확도 개선은 미측정/입증 없음이다. 이 경계를 작업 종료로 해석했던 것을 정정한다. 단계2에서 필요한 port/contact/return 공통 식과 기하 closure를 구현하고 실제 기준해까지 진행한다.3D RT0를 미리 필수로 고정하지 않고 기존 구현으로 가능한 최소 표현을 선택한다. 과거 Hω 변형은 재개하지 않는다.

## 단계2 실행 결과 — 2026-09-12

앞의 source joint seed를 확대하는 대신 독립 레드팀 제안의 최소 cuboid canonical을 구현했다. 96 tetrahedra / 260 RT0 currents / 220 volume 및 free-surface charges / 4 electrode voltages = 484 DOF다. 유한 PWR/return patch, 서로 분리된 post와 wide pad, 두 source 및 두 load electrode, 사전 지정 series R10mΩ/L1nH/C1µF를 포함한다. 전극 면은 free charge에서 제외하고 H와 회로 전압에 소유시켰으며, 내부 shared face는 하나의 signed current를 가진다. 모든 vector/scalar Green 결합과 유한 전도도를 유지한다. 두 도체 전체의 중성 및 전하/KCL/복소 전력 보존 검사를 통과했다. source 전체 보드의 대표성은 별도 한계다.

`outputs/research/astra-minimal-port-return-q4-01`에서 최초 bare GMRES가 잘못된 수렴 표시를 반환한 경우를 발견했다. 1MHz info0에도 원래 A의 componentwise backward=.947이고 포트 오차9.63Ω였다. 원본 실패를 보존했다. 같은 저장 A/b/port에 DC conduction+charge+electrode closure 전처리와 원래 방정식 잔차 보정을 적용한 `astra-minimal-port-return-q4-saved-numerics-01`은 직접해와 약1e-16Ω로 일치했다. 정련한 ordinary transpose dual 및 원래 A backward 검사를 통과했다. 내부 GMRES info20은 기록을 유지하며, 실제 원래 방정식 수렴을 판정 기준으로 사용한다. 이 전처리의 board 규모 계산 성능은 입증하지 않았다.

같은 n1 물리·기하·메시를 유지한 q8 실행도 통과했다. q4→q8 복소 Z 변화는 1MHz0.000717023574%, 10MHz0.0192221513264%였다. q8 Z는 각각 `0.012161532973305356-j0.1487426390085606`Ω 및 `0.013427505984999902+j0.08304995553998437`Ω다. 비교 기록은 `outputs/research/astra-minimal-port-return-quadrature-20260912.json`. 두 차수의 민감도 검사이며 완전한 점근 수렴/보드 정확도 증거는 아니다. 다음으로 동일 q8 물리의 n1→n2 공간 분할 비교를 Sol/high 감독으로 진행한다. 실제 보드 PowerSI 정확도 개선은 여전히 미측정이다.
