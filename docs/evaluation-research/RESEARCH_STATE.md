# Evaluation Algorithm Research State

최종 갱신: 2026-08-14 (Asia/Seoul)

## 상태 요약

| 항목 | 현재 상태 |
|---|---|
| 프로그램 기준 | SPD Decap PI Evaluator v0.22.0 |
| 연구 branch | `codex/evaluation-algorithm-research` |
| 기준 commit | `bb361687c0bf976d5d04faf26bc243bcf3d52006` |
| 현재 단계 | R0 reference/provenance contract 진행 중, R2 local-oracle 계획 사전 등록 |
| 제품 코드 변경 | 없음 |
| GitHub 원격 변경 | 없음 |
| 정확성 승격 | 미달성 |
| 8 GB 성능 승격 | 미달성 |

## 동결된 현행 baseline 해석

보존된 `v0.22.0-final-correlation-260804-mode10-12-r4` 결과는 구조적/runner 완료 증거이지 PowerSI 정확성 승격 증거가 아니다.

| 지표 | 값 | 해석 |
|---|---:|---|
| wall time | 24,434.37 s (약 6.79 h) | 목표 노트북용 경로로 부적합 |
| reduced nodes | 780,565 | direct sparse factor의 fill 위험이 큼 |
| finite-via links | 1,691,081 | 내부 topology가 port 수보다 비용을 지배 |
| VQPS development critical-band \|Z\| RMS | 1.738 dB | provisional gate 미달 |
| VQPS holdout critical-band \|Z\| RMS | 2.286 dB | provisional gate 미달 |
| loaded rail critical-band \|Z\| RMS | 16.743 dB | 심각한 model-form failure |
| loaded rail critical-band phase RMS | 44.28° | 심각한 model-form failure |
| loaded rail full-band \|Z\| RMS | 15.539 dB | 국부/분산 물리 누락 가능성 큼 |
| SuperLU U-pivot ratio | 1.2127e17 | forward reliability 경고 |
| backward residual | 1.04e-15 | 작은 residual만으로 forward accuracy 보장 불가 |

현행 `layerwise_admittance_v1`은 source-derived adjacent-layer overlap capacitance, finite via self-R/L, termination을 sparse nodal system에 조립한다. 그러나 secondary-layer lateral spreading, trace series R/L, via return/mutual coupling, pad/antipad field, plane sheet nonuniform R/L, general multiport field behavior를 충분히 표현하지 않는다.

## 결정 기록

| ID | 결정 | 상태/근거 |
|---|---|---|
| D-001 | 정확성 gate를 통과하기 전 성능 최적화를 주된 연구로 승격하지 않는다. | 확정; 빠른 잘못된 모델을 방지 |
| D-002 | 현재 report-level `passed`와 accuracy promotion을 분리한다. | 확정; loaded error가 15–17 dB 수준 |
| D-003 | 1순위는 adaptive triangular/MFDM domain decomposition + local transition replacement이다. | 잠정 채택; 논문 근거와 현행 gap이 일치 |
| D-004 | exact route-graph reduction은 admissible scalar branch에서 먼저 검증하되 coupling/topology 변경마다 재인증한다. | 잠정 채택; graph scan O(V+E), final-operator parity 필수 |
| D-005 | passive MOR/adaptive frequency sampling은 physics freeze 이후에만 사용한다. | 확정; 누락된 물리를 복구할 수 없음 |
| D-006 | per-design fitted RLC/material parameter로 네 파일을 맞추지 않는다. | 확정; overfit 및 일반화 실패 방지 |
| D-007 | pair P3/P4는 독립 holdout이 아니라 multifactor paired-response 자료로만 사용한다. | 정정; geometry, L21 material, partial-circuit body와 saved solver/component state가 함께 다름 |
| D-008 | pair P1의 전체 Z curve 분석은 후보 동결 전까지 보류한다. | 확정; reserved transfer/scale holdout 보존 |
| D-009 | 최종 일반화 주장은 동결 후 확보한 다섯 번째 unseen design이 필요하다. | 확정 |
| D-010 | full-board direct SuperLU를 8 GB architecture로 간주하지 않는다. | 확정; factor 하나가 약 10 GiB 추정 |
| D-011 | pair P2는 reference/explicit external-port operator contract case로 재분류한다. | 확정; bottom untagged LGA가 현행 범위 밖이며 label 외 signed operator/footprint/reference/de-embedding 필요 |
| D-012 | board ablation 전에 canonical coupon과 exact-minus-core boundary/owner 계약을 통과한다. | 확정; 동일 DtN/Schur interface, cross-boundary coupling owner와 crop convergence 필요 |
| D-013 | 기존 research FEM/MFDM/PEEC/MNA 모듈은 새 커널보다 먼저 oracle harness로 활용한다. | 확정; 12개 focused module의 178 tests 통과, production eligibility는 아직 차단 |

## 현재 가설 순위

1. loaded-port 오차는 trace series impedance, via return/mutual, pad/antipad, current crowding과 spreading을 포함한 **mounted transition physics** 누락이 가장 큰 원인이다.
2. equipotential artwork component는 aperture, neck, local return path와 distributed plane current를 재현하지 못한다.
3. ill-conditioned global matrix에서 backward residual만으로 결과를 승인한 것이 일부 오차를 숨길 수 있다.
4. material dispersion/loss와 conductor surface model도 중요하지만, 먼저 topology/local transition ablation으로 상대 기여를 분리해야 한다.

가설 1의 우선순위는 loaded 결과로 뒷받침되지만, 단일 원인이 확정된 것은 아니다. 각 물리 block을 additive/replacement ablation으로 검증한다.

## 연구 단계와 승격 조건

| 단계 | 산출물 | 종료 조건 | 상태 |
|---|---|---|---|
| R0 Reference contract | 8개 파일 manifest, explicit port map, PowerSI 조건 | identity/hash/grid/state 및 reference quality 기록 | identity/grid 완료, port/provenance 진행 중 |
| R1 Frozen baseline | 현행 solver의 전체 timing/memory/error/numerical report | 대표 pair와 rail별 재현 가능한 baseline | 기존 92-port 결과만 동결; pair P2 import 차단 |
| R2 Local oracle | via/trace/pad/plane canonical corpus | reciprocity/passivity/conservation/mesh convergence | 계획/threshold 사전 등록 완료, 실행 대기 |
| R3 Model attribution | 물리 block별 ablation matrix | 예상 port/band 개선을 원인별로 구분 | R2 oracle 뒤 대기 |
| R4 Hybrid global | condensed domains + passive MNA 후보 | 네 pair provisional accuracy gate | 대기 |
| R5 Acceleration | exact reduction, MOR, adaptive sweep | 추가 오차 budget + 8 GB gate | 대기 |
| R6 Blind validation | 동결 후 새 PowerSI pair | 사전 정의 기준 통과 | 대기 |
| R7 Final report/GitHub | 방법·결과·한계·재현법 | 사용자 검토 및 원격 반영 | 대기 |

## Provisional accuracy promotion gate

PowerSI repeatability와 mesh/order convergence를 측정한 뒤 수치는 조정할 수 있지만, 결과를 본 뒤 편의적으로 완화해서는 안 된다.

- development macro magnitude RMS ≤ 1.0 dB
- design-level holdout macro magnitude RMS ≤ 1.25 dB
- 어떤 rail family도 magnitude RMS > 2.0 dB가 아님
- phase RMS ≤ 7°, maximum ≤ 15°
- resonance/antiresonance frequency shift ≤ 5–10%
- sub-milliohm complex error RMS/p95 ≤ 100/200 µΩ
- reference transform, reciprocity, passivity, KCL/charge conservation, mesh convergence 통과
- hidden failed/blocked rail 없음; pooled average로 rail regression을 숨기지 않음

설계 → rail family → site → port의 계층적 macro/micro metric을 모두 보고한다. 주파수 샘플을 독립 표본처럼 취급하지 않으며 design/rail 단위 block bootstrap과 leave-one-design-out을 사용한다.

## 성능 promotion gate

- target laptop: i9-12900H, 8 GB RAM
- solver/process peak RSS 목표: 4.0 GiB, 허용 상한 5.0 GiB
- warm scenario evaluation 목표: 120 s 이하
- acceleration이 dense/direct oracle에 추가하는 오차: < 0.1 dB, < 1°
- paging/OOM에 의존하지 않으며 단계별 parse/compile/assemble/factor/RHS/postprocess 시간을 기록
- cold raw-SPD compile 목표는 R2/R3 prototype profiling 뒤 동결한다. 잠정 연구 목표는 30 min 이하이나 아직 약속 또는 승격 기준이 아니다.

현재 현실적인 첫 제품 구조는 workstation/server의 validated reference compiler와 laptop의 immutable passive ROM evaluator를 분리하는 것이다. 동시에 laptop local compile 가능성을 계속 계측한다. 어느 쪽도 실제 8 GB 측정 없이 성능 승격으로 선언하지 않는다.

## 수치 신뢰성 필수 항목

- backward residual 외에 condition/forward-error surrogate 또는 검증된 pivot-growth gate
- 문제 frequency/component의 fail-closed 보고
- reference S→Z condition/residual과 S/Z reciprocity/passivity 검사
- sampled-frequency dissipativity와 broadband causal/positive-real 검증을 분리
- scaled MNA와 nullspace/gauge 검증
- 직접 oracle 대비 domain condensation/MOR의 a posteriori error

## 열린 질문

1. 알려진 Layout Workbench build 외에 실제 export에 사용된 solver branch, adaptive mesh convergence log, material/roughness와 port de-embedding 조건은 무엇인가?
2. pair 3과 4에서 서로 다른 saved PowerSI/3DEM option 중 실제 S92P export를 지배한 설정은 무엇이며, omitted `Usage`와 `0b111000`의 PowerSI 의미 및 component enabled state는 무엇인가?
3. loaded error 중 trace, via return/mutual, pad/antipad, plane spreading 각각의 기여는 얼마인가?
4. mutable decap attachment terminal 수를 passive ROM이 감당할 수 있도록 어떤 domain/port compression이 가능한가?
5. cold compiler를 8 GB에서 실행할 수 있는 separator/interface-rank 구조가 가능한가, 아니면 reference compiler가 필수인가?
6. Pair P2 bottom-side LGA의 terminals/contact/path뿐 아니라 signed excitation/projection, footprint/current weighting, reference mode/plane와 de-embedding을 어떤 explicit manifest로 증명할 것인가?

## 다음 세션의 우선 작업

1. N0 exact reduction identity와 S1/V1/V2/A1/C1 canonical manifest를 작성하고 기존 research module로 oracle 결과를 생성한다.
2. 네 SPD의 trace/material/via/antipad parameter 범위와 missing/owner ID를 streaming manifest로 동결한다.
3. Pair P2 `ExternalPowerSiPort` manifest와 fail-closed test specification을 완성한다. 구현은 별도 승인 전까지 하지 않는다.
4. Pair P3/P4의 component enabled state와 실제 export solver setting을 확보해 solver-state confound를 닫는다.
5. PowerSI canonical coupon과 repeatability/mesh-convergence reference를 요청·정의한다.

## 변경 금지선

- 사용자의 구현 승인 전 제품 코드, version, installer, release를 수정하지 않는다.
- raw reference 파일을 rename/copy/commit하지 않는다.
- reserved holdout의 전체 curve를 후보 선택 전에 최적화에 사용하지 않는다.
- reference에서 역추정한 design-specific parameter를 source-derived 물성처럼 사용하지 않는다.
- 구조적 성공을 정확성/성능 성공으로 보고하지 않는다.
