# EXP-35 계획 — 표피 손실 항(via + 벽)을 새 기준선 p 위에서 재시험 (변형 pmk)

작성 2026-09-17, 실행 전 사전 등록. D7로 기준선이 exp28/p가 되었으므로, EXP-20에서 j 위에서 기록만 했던 자유 계수 없는 두 고주파 손실 항(via 표피 R `via_R_skin`, 벽 표피 R 실수부 `zs_wall_skin_re`)을 p 위에서 다시 판정한다. p에서는 10–100 MHz R 부족이 −0.379(중앙값)로 j(−0.254)보다 커졌다.

## 1. 규칙(고정)
- 변형 pmk = `c_unit_fix, homog_face_fix, via_R_skin, zs_wall_skin_re` 전부 True. 기준선 exp28/p. 7케이스와 92포트를 동시에 실행한다(효율).

## 2. 사전 예측
- ≤ 1 MHz: |ΔZ|/|Z| ≤ 1e-3, G1·G2·G3 PASS 집합 불변(EXP-20과 같은 이유).
- 10–100 MHz: 92포트 ΔR/Re Z_ref 중앙값 −0.379 → −0.30 부근(EXP-20의 개선 폭 0.05와 같은 자릿수), ΔR < 0 비율 1.00 → 0.9.

## 3. 판정 기준(동결) — EXP-20 §3과 동일, 기준선만 p
- (a) 7케이스 PASS→FAIL 없음, ≤ 1 MHz max |ΔZ|/|Z| ≤ 1e-3.
- (b) 92포트 H3 지표: ΔR < 0 비율 ≤ 0.70 또는 중앙값 |ΔR|/Re Z_ref ≤ 0.15 → 채택 제안. 그 외 기록만.
- 결과가 EXP-20과 같은 방향(부분 개선)이면 "고주파 손실 항은 물리적으로 타당하나 단독으로 문턱 미달"로 종결하고, 최종 정식화 시점에 급전 경로 관례와 함께 포함할지 결정한다.

## 4. 산출물
`WORK_DIR/exp35/result_*_any_pmk.json`(7 + 92), `compare_pmk.json`(기준선 exp28:p), `compare92_pmk_vs_p.json`, `EXP35_REPORT.md`, 저장소 사본 `results/exp35/`.
