# EXP-29 계획 — 균일 R 결손 계수의 구조: 결손이 경로 R에 비례하는가 (92포트 자료만, 모델 실행 없음)

작성 2026-09-17, 실행 전 사전 등록. EXP-28(변형 p)에서 참조/모델 R 비(100 kHz)가 중앙값 1.46, 사분위 1.15–1.58로 균일해졌다. 결손 ΔR = Re Z_ref − Re Z_model이 모델의 어느 부분에 비례하는지를 가려 원인 후보(도전율·두께 관례 vs decap·포트 항)를 좁힌다. 파라미터 변경 없음.

## 1. 정의(동결)
- 입력: exp28/p 92포트 영수증(100 kHz). R_path = breakdown 합(평면+via+trace, mΩ), R_rem = Re Z_model − R_path(decap ESR 가중합 + 패드 링크), ΔR = Re Z_ref − Re Z_model.
- 모델 M1: ΔR = α·R_path(원점 통과 최소제곱). M2: ΔR = β·R_rem. M3: ΔR = α·R_path + β·R_rem. 각 모델의 R²와 계수, 포트별 잔차.
- 요소별: ΔR = a_p·R_planes + a_v·R_vias + a_t·R_traces(원점 통과) — 계수와 R².

## 2. 가설(동결)
- H_path: M1의 R² ≥ 0.8이고 α가 0.3–1.0 → "경로 R의 균일 스케일(도전율·두께·평면 R 관례) 채택". M2가 M1보다 R²가 높으면 기각(decap·포트 항 원인).
- H_elem: 요소별 회귀에서 세 계수가 서로 2배 이내(0.5 ≤ a_i/a_j ≤ 2)이면 "전 요소 균일(도전율·두께)"; via 계수만 크면 "via 관례"; 평면 계수만 크면 "평면 R 관례".
- 예측: H_path 채택(α ≈ 0.5), H_elem은 "via 관례"(EXP-16 H_a·EXP-25의 via 지배)로 예측한다.

## 3. 판정 후 해석(미리 적음)
- "전 요소 균일"이면 소유자 확인 항목은 PowerSI의 구리 도전율·두께(MetalModel, 표면 거칠기·온도 보정)와 SPD 값 비교다. "via 관례"이면 via R 산정(PowerSI의 microvia 충전 모델, 스택 via의 패드 저항 포함 여부) 확인이다. 어느 쪽이든 모델 파라미터를 참조에 맞춰 조정하지 않는다.

## 4. 산출물
`exp29.json`, `EXP29_REPORT.md`, 그림 `exp29_dR_vs_Rpath.png`. 코드 `tools/research-claude/exp29/an29.py`. 저장소 사본 `results/exp29/`.
