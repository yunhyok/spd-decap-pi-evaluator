# EXP-14 계획 — [구현 차이] 되돌리기 (e) fringing, (f) 균질화 L, (g) via 배럴 면적, (h) via L two-wire

작성 2026-09-16, 실행 전 사전 등록(§4 기준은 결과를 본 뒤 바꾸지 않는다). 인계 §6-4 (e)(f)(g)(h). (i) 실장 루프 L은 decap 바디–평면 기하가 extract에 없어 이번에 제외한다(§6).
**기준선**: 소유자 승인(2026-09-16)에 따라 EXP-14 이후 기준선은 "EXP-8 + c_unit_fix"(EXP-13 변형 j 영수증, `WORK_DIR/exp13/result_*_any_j.json`)다. 모든 변형은 `c_unit_fix=True` 위에서 돌리고 j 영수증과 비교한다. 코드 기본값과 `smoke_port18.py`는 바꾸지 않는다.

## 1. 대상과 규칙(고정)
- (e1) fringing 문턱 제거: 현재 `weff = G·w`가 `5d` 미만인 엣지에만 `weff → min(weff+2d, w)`를 적용한다. e1은 문턱 없이 모든 레일 엣지에 적용한다(상한은 유지).
- (e2) fringing 상한 제거: 문턱 5d는 유지하고 `min(·, w)` 상한을 없앤다(`weff → weff+2d`). 연속 금속 안쪽 셀에도 유효폭이 셀 폭을 넘게 되므로 비물리적 상한 실험이며 감도 확인용이다.
- (f) 균질화 L 상한 실험: 외부 L에 1/G를 적용하지 않는다(`XL = ωμ0 d ℓ / w`, R은 그대로 `1/G`). 구멍 ≲ d일 때 자계가 구멍을 메우는 극한(모든 구멍이 작다고 가정)의 경계값이다. fringing 판정의 `weff`도 `G·w` 대신 `w`를 쓴다.
- (g) via 배럴 면적: 도금 배럴(HOLLOW 분류)에서 `π D t_p` → `π(D t_p − t_p²)`, 즉 `R → R·D/(D−t_p)`(t_p = min(20 µm, D/4)). 충전 microvia(SOLID)는 불변. 분류는 `src/spd_decap_pi/_core/via_model.classify_via_conductor`를 그대로 쓴다.
- (h) via L two-wire: `L = μ0ℓ/(2π)·ln(s/r)` → `μ0ℓ/π·ln(s/r)`(2배). antipad 반경 치환은 extract에 antipad가 없어 제외한다.

## 2. 사전 예측 (기준선 j의 100 kHz breakdown, 실행 전 기록)
| 케이스 | 평면 R (mΩ) | 평면 L (pH) | via R (mΩ) | via L (pH) | trace R (mΩ) | trace L (pH) |
|---|---|---|---|---|---|---|
| 260729_Port14_SITE0 | 1.954 | 118.0 | 0.451 | 35.6 | 0.125 | 6.2 |
| 260729_Port16_SITE0 | 0.370 | 19.9 | 0.126 | 11.5 | 0.035 | 1.8 |
| 260729_Port18_SITE0 | 0.490 | 32.9 | 0.079 | 5.3 | 0.018 | 0.8 |
| 260729_Port19_SITE0 | 2.249 | 84.7 | 3.299 | 308.9 | 1.218 | 49.4 |
| 260729_Port1_SITE0 | 2.927 | 208.7 | 0.541 | 41.2 | 0.131 | 6.4 |
| 260729_Port7_SITE0 | 0.353 | 16.7 | 0.028 | 1.6 | 0.006 | 0.3 |
| 260804_Port18_SITE0 | 0.495 | 47.4 | 0.076 | 5.5 | 0.027 | 0.7 |
- (e1): 문턱 5d 아래에 있지 않던 엣지(coarse 셀에서 G > 0.5)에 +2d가 붙지만 상한 w에 막혀 G ≈ 1인 셀은 불변, 0.5 < G < 0.8인 셀만 L이 줄어든다. 예측 |ΔL| ≤ 2 pH, 게이트 불변.
- (e2): fine 블록(50 µm)의 거의 모든 엣지에서 상한이 풀려 유효폭이 최대 90/50 = 1.8배 → 포트 근방 L이 크게 준다. 예측 ΔL −3 ~ −15 pH(P1·P14처럼 평면 L이 큰 레일에서 더 큼). G2가 바뀔 수 있으나 물리적 근거가 없는 상한이므로 채택 대상이 아니다(감도 기록만).
- (f): 평면 L 중 1/G 보정분(G 평균 0.83–0.96)이 사라져 L이 5–15 % 준다. 예측 ΔL −2 ~ −20 pH. P1(ΔL +39 pH)·P14(+15 pH)는 개선, P18(+2.7 pH)은 0 근처를 지나 음수로 갈 수 있다.
- (g): via R이 도금 배럴에서 1.33배(t_p = D/4일 때). 레일 via의 대부분은 60 µm 충전 microvia라 불변이고 core PTH만 영향. 예측 ΔRe ≤ +0.02 mΩ, 게이트 불변.
- (h): via L 2배 → ΔL이 위 표의 via L만큼(P18 +5.3 pH, P1·P14는 더 큼) 늘어 G2가 악화된다. 예측: P18·804-P18에서 G2 PASS→FAIL 가능, 채택 안 됨.

## 3. 방법
플래그 `Model3(fringe_no_thresh, fringe_no_cap, homog_L_noG, via_area_exact, via_L_twowire)` 기본 False. `run11.py --variant e1|e2|f|g|h`(각각 c_unit_fix=True 포함), 출력 `WORK_DIR/exp14/`. `table11.py --exp exp14 --baseline exp13:j`. 기본 경로 불변 검증: `--variant none --smoke` 상대차 ≤ 1e-9, `--variant j --smoke`가 EXP-13 j의 P18 1 MHz 값과 상대차 ≤ 1e-9, `smoke_port18.py` PASS. 7케이스 × 5변형 병렬(2 배치).

## 4. 판정 기준(동결) — EXP-11 §4와 동일, 기준선만 j
- H_null: max |ΔZ|/|Z| (≤ 10 MHz) ≤ 1e-3 이고 PASS 집합 동일.
- 채택: PASS→FAIL 없음 그리고 (FAIL→PASS 하나 이상 또는 G3 중앙값 1 %p 이상 감소). (e2)는 비물리 상한이므로 기준을 만족해도 채택하지 않고 감도로만 기록한다.
- G1–G5 정의 불변.

## 5. 산출물
`result_*_any_{e1,e2,f,g,h}.json` ×35, `compare_*.json` ×5, `EXP14_REPORT.md`, 저장소 사본 `results/exp14/`.

## 6. 제외
(i) 실장 루프 L: decap 바디 높이·단자 간격이 extract에 없다. 소유자가 대표 decap의 실장 기하(바디 높이, 단자 간격, 패드–평면 거리)를 주면 고정식으로 별도 사전 등록한다.
