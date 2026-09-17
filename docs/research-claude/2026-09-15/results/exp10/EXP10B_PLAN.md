# EXP-10b 계획 — 1–100 kHz 한정 유리함수 판정(복소계수 허용) + 저주파 비물리 항 전 설계 진단

작성 2026-09-16, 실행 전 사전 등록. 소유자 지시: PowerSI 재시뮬레이션은 전 포트 재해석이 필요해 오래 걸리므로, 보유 자료로 가능한 것은 먼저 진행한다. EXP-10(`EXP10_REPORT.md`)의 후속.

## 1. 질문
Q1. G1 대역 1–100 kHz만 보면 참조 Z_ii는 ≤4극 유리함수인가. EXP-10에서 실계수 fit을 막은 켤레 비대칭 항(상수 서셉턴스 B)을 복소계수 fit으로 허용하면 잔차가 문턱 아래로 내려가는가.
Q2. ≤1 kHz 비물리 항(B≠0, G<0, Z(0) 불일치)은 260804·s5m6585 참조에도 있는가.

## 2. 가설
- H_fit1: 1–100 kHz에서 복소계수 ≤4극 유리함수 fit의 RMS ≤ 1e-6 (실계수 fit은 B 항 잔류로 이보다 크다).
- H_solve1(귀무): 복소계수를 허용해도 n_p=4 RMS 중앙값 > 1e-4.
- H_common: 비물리 항은 같은 PowerSI 설정(`DCFitted=1 BBSFitted=1`, 세 SPD 모두 동일)의 산물이므로 세 설계 모두에 나타난다(|B| > 0.01 mS 포트 비율 ≥ 50 %).

## 3. 방법
1. 데이터: `ref_npz(design)` Zdiag만. 대역 Q1: 1 kHz ≤ f ≤ 100 kHz(51점). 대역 Q2: 0.1 Hz ≤ f ≤ 2 Hz.
2. Q1 fit 두 가지, 260729 92포트, n_p ∈ {1,2,3,4}(Port18은 6·8 추가):
   - real: EXP-10과 같은 실계수 VF(상대 가중).
   - complex: 계수 d, e, r_k와 극 p_k를 모두 복소로 두고 켤레 쌍 제약을 없앤 VF(같은 가중·반복·수렴 조건). 자체 검증: 합성 `Y=(G+jB)+jωC` 데이터에서 complex n_p=1 RMS ≤ 1e-10.
   - 잔차 정의는 EXP-10과 동일(상대 RMS·MAX, 포트별 최적 n_p ≤ 4).
3. Q2 진단, 세 설계 전 포트: 복소 최소제곱 `Y = (G + jB) + jωC`. 보고 항목: B·G 분포, |B| > 0.01 mS 포트 수, G < 0 포트 수, Re Z < 0 포트 수와 최대 주파수, Z(0) 파일값과 1/(G+jB)의 상대차 중앙값, 선형모델 최대 상대잔차 중앙값.

## 4. 판정 기준 (동결)
Q1(1–100 kHz), 포트 fit 수준 = RMS ≤ 1e-6 이고 MAX ≤ 1e-5(n_p ≤ 4), real 또는 complex 중 하나라도.
- FIT_1_100k: ≥ 83 / 92 포트.
- SOLVED_1_100k: real·complex 모두 n_p=4 RMS 중앙값 > 1e-4.
- INDETERMINATE: 그 외.
보조(보고만): 포트별 비 RMS_real/RMS_complex(n_p=4)의 중앙값이 > 10이면 "이 대역에서도 비대칭 항이 실계수 잔차를 지배"로 적는다.
Q2: H_common 채택 = 세 설계 모두 |B| > 0.01 mS 포트 비율 ≥ 50 %. 그 외는 설계별로 보고.

## 5. 해석 (미리 적음)
- FIT_1_100k이면: G1 대역 참조는 저차 유리함수로 기술되는 생성값일 가능성이 높다. G1 게이트는 유지하되 보고서에 "G1은 fit 값과의 일치"라 적고, 확정에는 소유자의 DCFitted 해제 재해석(전 포트)이 필요하다고 남긴다.
- SOLVED_1_100k이면: G1 대역은 저차 fit이 아니다. G1 해석 유지.
- INDETERMINATE이면: 잔차 수준과 n_p 6·8 결과를 보고하고 해석은 바꾸지 않는다.
- 어느 경우든 게이트 G1–G5 정의는 바꾸지 않는다.

## 6. 산출물
`fit_lowfreq.py`에 `--band lo hi`, `--complex`, `--lowfreq-diag` 플래그 추가(기본 동작은 EXP-10과 동일하게 유지). 영수증 `result_exp10b_260729_band1k100k.json`(real·complex 결과), `result_exp10b_lowfreq_diag.json`(세 설계), 그림 `exp10b_residual_real_vs_complex.png`, 보고서 `EXP10B_REPORT.md`. 저장소 사본 `results/exp10/`(기존 파일 덮어쓰지 않음).
