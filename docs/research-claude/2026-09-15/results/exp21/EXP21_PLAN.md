# EXP-21 계획 — GND-only 기준면 모드로 92포트 재실행 (EXP-16 H_b 판별)

작성 2026-09-16, 실행 전 사전 등록. EXP-16 H_b(타 전원넷을 이상 기준면으로 두는 cavity-wall 규칙이 R 결손을 만든다)는 cavity 모드 자료만으로는 판별할 수 없었다(92포트 전부가 타넷 기준 후보를 포함). 기준선 exp13/j와 같은 모델·플래그(c_unit_fix)에서 기준면 검색만 DGND 전용(EXP-5 규칙, `exp1b.TwoSided(gnd_net="DGND")`)으로 바꾼다. 파라미터 튜닝 없음.

## 1. 규칙(고정)
- 변형 j_gnd = `c_unit_fix=True`, 기준면 검색 = DGND만(`run11.py --ref gnd`: `M3.TwoSided`를 `TwoSidedAny`로 바꾸지 않음). 나머지는 exp13/j와 동일. 출력 `WORK_DIR/exp21/result_260729_{port}_any_j_gnd.json`.
- 기존 GND-only 결과(EXP-5, 7케이스, c_unit_fix 없음)는 참고만 한다.

## 2. 가설(동결)
- H_b′: R 결손형 포트(EXP-15 기준 dRe_100k < −0.05 mΩ, 67개)에서 GND-only 모드의 dRe_100k 중앙값이 cavity 모드(−2.15 mΩ... 실제 중앙값은 EXP-15 `summary15.json`의 값)보다 절대값으로 30 % 이상 줄면 채택("타넷 이상 기준면이 R 결손의 주요 원인"). 10 % 이내면 기각. 그 외 불확정.
- 예측: EXP-8에서 cavity 전환은 주로 L(ΔL fit)을 바꾸고 R은 거의 바꾸지 않았으므로 **기각을 예측**한다. 대신 L 오차(fit_dL)의 분포가 넓어질 것으로 본다(cavity가 L을 줄여 참조에 가까워졌던 효과의 반대).
- 보조(보고만): G1–G4 PASS 수, err_1MHz 중앙값, fit_dL 분포를 cavity(EXP-15)와 나란히 표시.

## 3. 방법
1. `run11.py --ref gnd|any`(기본 any = 기존 동작), 영수증에 `ref_mode` 기록. 기본 경로 불변 검증(`--variant none --smoke` ≤ 1e-9). `--ref gnd --variant j --smoke --smoke-baseline exp13:j`는 EXP-5 GND-only P18 결과(err 1 MHz, exp5 영수증 `results/exp5/`)와 같은 자릿수여야 한다(c_unit_fix 차이만).
2. `runall15.py --variant j --outdir exp21 --ref gnd` 92포트(약 1.5 h). `summary15.py --dir exp21 --variant j`로 집계.
3. 비교 스크립트 `exp16/corr16.py --dir exp21`(H_b′ 통계 추가)로 dRe 중앙값 비교.

## 4. 판정
H_b′ 채택/기각/불확정 보고. 어느 쪽이든 기준선(exp13/j, cavity)은 유지한다(cavity 모드는 EXP-8에서 L 게이트 때문에 채택된 것이며 R 판별과 무관). G1–G5 정의 불변.

## 5. 산출물
`result_260729_*_any_j_gnd.json` ×92(WORK_DIR), `summary15_exp21.json`, `exp21_hb.json`, `EXP21_REPORT.md`, 저장소 사본 `results/exp21/`.
