# EXP-10b 보고서 — 1–100 kHz 한정 유리함수 판정(복소계수) + 저주파 비물리 항 전 설계 진단

실행 2026-09-16. 사전 등록 `EXP10B_PLAN.md`(§4 기준은 결과를 본 뒤 바꾸지 않았다). 참조 데이터만 사용, 모델 미실행. 영수증 `result_exp10b_260729_band1k100k.json`, `result_exp10b_lowfreq_diag.json`. 코드 `tools/research-claude/exp10/fit_lowfreq.py`(`--band`, `--complex`, `--lowfreq-diag`; 기본 동작은 EXP-10과 동일함을 재실행으로 확인, 차이 0.0). 자체 검증: 합성 `Y=(G+jB)+jωC`에서 복소 VF n_p=1 RMS 2.0e-15 PASS(실계수 n_p=4는 4.6e-2).

## 1. Q1 — 1–100 kHz(51점), 260729 92포트: 판정 **INDETERMINATE**

| n_p=4 | 실계수 VF | 복소계수 VF |
|---|---|---|
| RMS 중앙값 | 1.55e-4 | 2.54e-6 |
| RMS 범위 | 7.7e-5 – 1.4e-3 | 2.3e-6 – 3.8e-4 |
| fit 수준 포트(RMS ≤ 1e-6, MAX ≤ 1e-5) | 0 | 0 |

FIT_1_100k(≥ 83포트) 불충족, SOLVED_1_100k(둘 다 중앙값 > 1e-4) 불충족(복소 2.5e-6) → INDETERMINATE. 보조 지표 RMS_real/RMS_complex 중앙값 **61** → "이 대역에서도 켤레 비대칭 항이 실계수 잔차를 지배"(그림 `exp10b_residual_real_vs_complex.png`, 전 포트가 대각선 아래).

Port18 RMS: 실계수 n_p 1/2/3/4/6/8 = 8.7e-3 / 2.0e-3 / 5.0e-4 / 8.6e-5 / 4.4e-6 / 4.1e-6, 복소 = 8.4e-4 / 8.0e-5 / 2.0e-5 / 2.5e-5 / **2.8e-7** / 4.4e-7.

**사후 민감도(판정에 미사용, 사전 등록 범위 밖)**: 복소 VF를 전 포트에 n_p=6·8로 확장하면 RMS 중앙값 8.2e-8 / 2.5e-8, fit 수준 포트 75 / 80(≥ 83에는 미달). 극 반복은 20회 안에 tol 1e-10에 못 미쳐 `converged=False`가 대부분이지만 잔차는 위와 같다.

**해석**: 1–100 kHz 참조는 6–8극 복소계수 유리함수로 ~1e-7까지 기술된다. 그러나 이 대역에서는 물리 자체가 저차에 가깝다(decap은 전부 SRF 아래, 평면은 R+L). decap 종류가 수 개면 해석값도 6–8극으로 1e-8 수준까지 근사될 수 있으므로 **유리함수 잔차만으로는 이 대역에서 fit/해석을 가릴 수 없다**. 계획 §5의 INDETERMINATE 해석대로 G1 해석은 바꾸지 않는다. 판별에 남은 수단은 (a) 비물리 항의 존재(§2)와 (b) 소유자의 DCFitted 해제 재해석이다.

## 2. Q2 — 저주파(0.1–2 Hz) 비물리 항, 세 설계

| 설계 | 포트 | B 중앙값 (mS) | \|B\| > 0.01 mS | G < 0 | Re Z < 0 포트 (최대 f) | Z(0) 파일 vs 1/(G+jB) 상대차 중앙값 | 선형모델 최대잔차 중앙값 |
|---|---|---|---|---|---|---|---|
| 260729 | 92 | 0.092 | 80 (87 %) | 8 | 10 (174 Hz) | 0.58 | 6.7e-6 |
| 260804 | 92 | 0.115 | 82 (89 %) | 0 | 0 | 0.60 | 4.7e-6 |
| s5m6585 | 160 | 0.0016 | 0 (0 %) | 17 | 20 (1.1 kHz) | 0.44 | 2.2e-6 |

세 SPD 모두 `.DC_BBS_Setting DCFitted = 1 BBSFitted = 1 PDCEqualPotential = 0`(소유자는 BBSFitted를 끈 것으로 기억하므로 파일값이 실제 실행 설정인지 확인 필요).

H_common(세 설계 모두 |B| 비율 ≥ 50 %)은 **기각**(s5m6585 0 %). 다만 s5m6585에는 B 대신 G < 0(17포트)와 Re Z < 0(20포트, ≤ 1.1 kHz)가 있고, Z(0) 불일치(44 %)는 세 설계 공통이다. 즉 **≤ 1 kHz 참조가 물리적으로 자기모순인 것은 세 설계 공통**이며 형태만 다르다(S4LB002 두 설계: 상수 서셉턴스, s5m6585: 음의 컨덕턴스). 0.1–2 Hz가 `(G+jB)+jωC` 선형모델에 ~5e-6으로 맞는 것도 세 설계 공통이다.

## 3. G1·G2 영향

- 켤레 비대칭 항의 1–100 kHz 기여는 실계수 잔차 1.5e-4(중앙값) 수준이다. Port18 30.2 kHz·100 kHz 사다리 점의 Re Z 영향은 EXP-10 §3과 같이 2.5e-5·7.6e-6이라 G1 문턱 0.05 mΩ, G2 ±5 pH에 영향 없다. 게이트·해석 변경 없음.
- ≤ 1 kHz 참조는 어느 설계에서도 검증 근거로 쓰지 않는다(이미 게이트 밖).

## 4. 남은 판별 수단 (소유자)

PowerSI에서 DCFitted(및 BBSFitted) 해제 후 **전 포트** 재해석·Touchstone export(부분 포트 해석은 전 포트 결과와 다르다는 소유자 지적에 따름). 대상 파일은 아래. 재export 후 비교 스크립트는 `fit_lowfreq.py --lowfreq-diag`에 새 npz 경로만 추가하면 된다(B·G<0·Z(0) 불일치가 사라지는지가 판별점).
- 1순위: `E:\Work\20260724 S4LB002_DC MLO PI\S4LB002-2Para_260729_1_injected.spd` (현 참조 `S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p`)
- 확인용: `E:\Work\20260804 S4LB002_DC MLO\S4LB002-2Para_260804_1_injected.spd`, `E:\Work\20260414 S5M6585 PCB PI\s5m6585_32p_260414_length3_1.spd`

## 5. 산출물
`EXP10B_PLAN.md`, 이 보고서, `result_exp10b_260729_band1k100k.json`, `result_exp10b_lowfreq_diag.json`, `exp10b_residual_real_vs_complex.png`. 저장소 사본 `docs/research-claude/2026-09-15/results/exp10/`.

## 6. 정정 (2026-09-16, 소유자 확인)
§2의 `DCFitted = 1 BBSFitted = 1` 언급과 §4의 "DCFitted 해제 후 재해석" 제안을 정정한다. 참조 `_S` 파일은 DCFitted 미적용 파일이고 BBSFitted는 비활성이었다(자세한 내용은 `EXP10_REPORT.md` §7). 세 설계 공통의 ≤ 1 kHz 자기모순은 Adaptive sweep 유리함수 보간의 산물로 본다. 재해석 요청은 "저주파 Log/Linear 이산 스윕, 전 포트"로 바꾼다. 대상 파일명은 §4와 같다.
