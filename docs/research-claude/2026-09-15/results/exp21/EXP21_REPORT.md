# EXP-21 보고서 — GND-only 기준면 모드 92포트 (EXP-16 H_b′ 판별)

실행 2026-09-17, 사전 등록 `EXP21_PLAN.md`(§2 문턱 불변). `run11.py --ref gnd --variant j`(c_unit_fix, 기준면 검색 DGND 전용 = EXP-5 규칙), 92포트 성공(실패 0). 집계 `summary15.py --dir exp21 --ref gnd`, `corr16.py --dir exp21 --ref gnd`(`exp16_corr_exp21.json`). 스모크: P18 GND-only 1 MHz 오차 9.14 % = EXP-5 영수증 9.14 %(c_unit_fix 차이만).

| 지표 (92포트) | cavity (EXP-15/j) | GND-only (exp21) |
|---|---|---|
| G1 / G2 / G3 / G4 PASS | 8 / 10 / 30 / 0 | 8 / 4 / 21 / 0 |
| err_1MHz 중앙값(사분위) | 24.3 % (8.5–35.7) | 29.5 % (10.7–42.1) |
| R 결손형 포트 dRe_100k 중앙값 | −6.22 mΩ (n 67) | −6.21 mΩ (n 67) |

**H_b′ 기각**(예측대로): 기준면을 DGND로 한정해도 R 결손 중앙값은 0.1 % 차이로 같다. 타 전원넷을 이상 기준면으로 두는 cavity-wall 규칙은 R 결손의 원인이 아니다. 예측대로 L 오차는 넓어져 G2 PASS가 10 → 4, G3 30 → 21로 나빠졌다(cavity가 EXP-8에서 채택된 이유 재확인).

판정: 기준선(exp13/j, cavity) 유지. G1–G5 정의 불변. 영수증 92개는 `WORK_DIR/exp21/`(저장소에는 집계만).
