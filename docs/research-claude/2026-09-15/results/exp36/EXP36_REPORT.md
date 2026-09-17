# EXP-36 보고서 — PowerSI "Special Void" 1500 µm 채움을 모델에 반영 (변형 pv, 기준선 exp28/p)

실행 2026-09-17, 사전 등록 `EXP36_PLAN.md`(§1 규칙·§3 판정 불변). 러너 `exp11/run11.py --variant pv`(플래그 p + `void_fill_um=1500`), 비교 `table11.py --exp exp36 --variant pv --baseline exp28:p`. 자유 계수는 PowerSI 설정값 1500 µm뿐이다. 이번 세션에서 확인된 참조 조건: PowerSI Mesh는 MaxEdgeLength 4970 µm(기본값), Simplify geometry ON, Coarse mesh OFF(`reviews/powersi_options_2026-09-17.md`).

## 1. 7케이스 (p → pv)

| 케이스 | err 1 MHz | Re Z_model@100 kHz (mΩ) | Re Z_ref/Re Z_model @100 kHz | 10–100 MHz ΔR/Re Z_ref 중앙값 | PASS 변화 |
|---|---|---|---|---|---|
| P1 | 3.15 → 12.91 % | 5.195 → 4.282 | 1.016 → 1.232 | −0.161 → −0.259 | **G3 PASS→FAIL** |
| P7 | 2.91 → 10.27 % | 0.433 → 0.368 | 0.979 → 1.151 | −0.072 → −0.138 | **G1·G3 PASS→FAIL** |
| P14 | 8.17 → 13.70 % | 4.121 → 3.669 | 1.141 → 1.281 | −0.197 → −0.254 | **G3 PASS→FAIL** |
| P16 | 14.5 → 18.3 % | 1.054 → 0.962 | 1.283 → 1.406 | −0.302 → −0.354 | 없음 |
| P18 | 0.81 → 15.93 % | 0.718 → 0.585 | 1.009 → 1.237 | −0.086 → −0.165 | **G1·G3 PASS→FAIL** |
| P19 | 30.9 → 31.9 % | 12.69 → 12.48 | 1.497 → 1.523 | −0.352 → −0.358 | 없음 |
| 804-P18 | 6.56 → 19.93 % | 0.728 → 0.595 | 1.078 → 1.319 | −0.132 → −0.186 | **G3 PASS→FAIL** |

G3 중앙값 6.56 → 15.93 %. ≤ 10 MHz max |ΔZ|/|Z|는 P19 2.0e-2, 나머지 8.7e-2~1.7e-1. ΔL(1 MHz)은 P1 +30 pH, P14 +21 pH로 늘었고(구멍 제거로 평면 C·L 분포가 바뀜) 나머지는 ±1 pH 이내다.

## 2. 판정
- 사전 예측대로 채움은 평면 R을 12–19 % 낮춰(P19만 −2 %) 참조와의 R 결손을 키운다. 7케이스 PASS→FAIL 5건(G1 2, G3 5) → §3 (a) **불성립**. pv는 "관례 일치 플래그"로도 채택하지 않고 **기록만** 한다. 기준선 exp28/p 유지, G1–G5 정의 불변.
- 해석: PowerSI의 Special Void 제외(1500 µm 미만 구멍을 시뮬레이션에서 뺌)를 "구멍을 금속으로 채움"으로 옮기면 참조에서 멀어진다. 따라서 참조는 (i) 이 옵션이 레일 평면의 via 구멍·anti-pad에 실제로는 적용되지 않았거나(옵션이 signal/plane void 종류를 구분), (ii) 적용됐더라도 그 효과가 R을 낮추는 방향이 아니거나 둘 중 하나다. 어느 쪽이든 R 결손의 원인은 아니며, 오히려 export된 void를 그대로 쓰는 현재 모델(EXP-6 V0)이 참조와 더 가깝다.
- 92포트: 계획 §3은 7케이스 결과와 무관하게 실행하도록 정했다. CPU로는 약 6–8 시간이 걸리므로 이 세션에서 도입한 A2000 cuDSS 경로(`SPD_PI_SOLVER=cudss`)의 검증이 끝난 뒤 그 경로로 실행하고, 결과는 이 보고서 §4에 추가한다(영수증 `WORK_DIR/exp36/result_*_any_pv.json`).

## 3. 산출물
`result_*_any_pv.json`(7), `compare_pv.json`, 로그 `logs/*_pv.log`(P7 3293 s, P16 1332 s, P18 1136 s — 20개 병렬 실행 중 측정). 저장소 사본 `results/exp36/`: 계획, 이 보고서, `compare_pv.json`.

## 4. 92포트 (p → pv) — 결손 확대 확인, 기록만

`runall15.py --jobs 4 --variant pv --outdir exp36`를 A2000 cuDSS 경로(`SPD_PI_SOLVER=cudss`)로 실행: 86포트 성공·실패 0, 합계 벽시계 112 분(4 병렬, 약 45 분; 6포트는 앞선 CPU 실행). 첫 GPU 드라이버는 cuDSS `free()` 결함으로 소형 포트 6개가 크래시했고(`failures_driver1_cudss_crash.json`, `logs/runall36_pv_cudss_driver.log`), 수정 후 두 번째 드라이버(`logs/runall36_pv_cudss_driver2.log`)로 완주. GPU 영수증은 `stats[].solver == "cudss"`로 구분되며 CPU 대비 정확도는 max |ΔZ|/|Z| ≤ 5e-9(exp37gpu 검증). 비교 `compare92_pv_vs_p.json`, H3 `dl17.py --dir exp36 --variant pv`.

| 지표 (92포트) | 기준선 p | pv |
|---|---|---|
| err_1MHz 중앙값(사분위) | 31.7 % (10.4–38.0) | **32.8 % (17.1–39.5)**, 개선 2 / 악화 90 |
| Re Z_ref/Re Z_model @100 kHz 중앙값 | 1.464 | **1.491** |
| G1 / G2 / G3 / G4 PASS | 6 / 12 / 22 / 0 | 2 / 10 / 5 / 0 (PASS→FAIL G1 4, G2 3, G3 17) |
| 10–100 MHz ΔR < 0 비율 / 중앙값 \|ΔR\|/Re Z_ref (dl17) | 1.00 / 0.379 | 1.00 / 0.387 |

92포트에서도 §2와 같은 방향이다: 구멍 채움은 전 대역에서 모델 R을 낮추고 참조와의 결손을 키운다. **판정 불변(기록만, 기준선 exp28/p 유지).** PowerSI의 Special Void 제외는 참조 결과를 "구멍 채움" 방향으로 바꾸지 않았다고 보는 것이 가장 단순한 해석이며, 제품의 PowerSI-compatible 모드(D5)에도 이 플래그를 넣지 않는다.
