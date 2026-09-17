# EXP-31 계획 — 패키지 R 결손의 요소별 스케일 계수 (92포트 exp28/p 자료만, 규모 무관 회귀)

작성 2026-09-17, 실행 전 사전 등록. EXP-30에서 R 결손이 패키지 특유임이 드러났다. 어느 요소(평면·via·trace·decap 항)를 몇 배 해야 참조가 재현되는지를 92포트로 정량화한다(모델 파라미터를 바꾸는 것이 아니라 관례 차이의 위치를 특정하는 진단). EXP-29의 선형 OLS 실패(규모 지배)를 피하기 위해 규모 무관 형태로 푼다.

## 1. 정의(동결)
- 100 kHz에서 y = Re Z_ref/Re Z_model − 1, 요소 비중 s_el = R_el/Re Z_model, el ∈ {planes, vias, traces, rem}(rem = Re Z_model − 경로 합 = decap ESR 가중합 + 패드 링크). Σ s_el = 1.
- 모델: y = Σ c_el·s_el(원점 통과). c_el은 "그 요소를 (1 + c_el)배 하면 참조와 맞는다"는 의미다. 적합: 비음수 최소제곱(scipy.optimize.nnls)과, 견고성 확인용 부트스트랩(1000회, 포트 재표집)으로 c_el의 95 % 구간.
- 대상: 92포트 전부(주), 그리고 Re Z_ref/Re Z_model < 1인 12포트 제외(보조). 고임피던스 레일이 지배하지 않도록 y와 s가 모두 무차원이다.

## 2. 가설(동결)
- H_via: c_vias의 95 % 구간 하한 ≥ 0.5이고 c_planes 상한 ≤ 0.3 → "충전 microvia R 관례" 채택. c_planes 하한 ≥ 0.3이면 "평면 R 관례". c_rem 하한 ≥ 0.3이면 "decap 항(실장·패드) 관례". 모두 아니면 불확정.
- H_fit: 모델 R² ≥ 0.5(부트스트랩 중앙값). 미만이면 요소 비중만으로는 설명이 안 되며 포트 국소 구조가 지배.
- 예측: H_via 채택(c_vias ≈ 1–2), c_planes ≈ 0(P18·P7가 맞음), c_traces 불확정.

## 3. 판정 후 해석(미리 적음)
- H_via 채택이면 소유자에게 PowerSI의 microvia 모델 설정(도금 두께 기본값, 충전 여부, 원추형 옵션)만 GUI에서 확인해 달라고 요청하고(재시뮬레이션 불필요), 그와 별개로 c_vias의 값과 정합하는 폐형식 후보(예: 도금 두께 t_p 기본값에 따른 hollow 배럴 R)를 다음 실험으로 등록한다.

## 4. 산출물
`exp31.json`, `EXP31_REPORT.md`, 그림 `exp31_coeffs.png`(부트스트랩 분포). 코드 `tools/research-claude/exp31/an31.py`. 저장소 사본 `results/exp31/`.
