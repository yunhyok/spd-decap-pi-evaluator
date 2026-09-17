# EXP-10 계획 — 1–100 kHz 참조(PowerSI Touchstone) 신뢰성 판정

작성 2026-09-16. 실행 전 사전 등록 문서. 결과를 본 뒤 이 문서의 가설·판정 기준은 바꾸지 않는다.
근거: `NEXT_SESSION_HANDOFF.md` §6-1, `CODEX_HANDOVER_RETROSPECTIVE.md` §8-7, `reviews/sigrity_voids.md` §4.
선행 게이트 G0: `smoke_port18.py` → err 2.69 %, 영수증 대비 상대차 5.83e-12, SMOKE PASS (2026-09-16, wall 239 s, 1262 MB).

## 1. 질문
PowerSI 참조의 저주파 구간(특히 G1 대역 1–100 kHz)은 주파수점마다 해석한 값인가, DC 점과 broadband rational fit으로 생성한 값인가.
SPD에 `.DC_BBS_Setting DCFitted = 1 BBSFitted = 1`이 있고, 참조 grid는 0.1 Hz부터 25점/decade의 규칙적 log grid이며, 10 ports(260729)·20 ports(s5m6585)에서 f ≤ 1.1 kHz에 Re Z < 0이 있다(Track A). 수동 회로의 해석값은 Re Z < 0일 수 없다.

## 2. 가설
- **H_fit**: 저주파 참조는 저차 유리함수(2–4극)로 생성된 값이다. 따라서 1 Hz–100 kHz에서 Z_ii(f) 또는 S_ii(f)를 4극 이하 유리함수로 맞추면 잔차가 파일 정밀도 수준(≈1e-6 이하)이다.
  - 귀결: G1(|ΔRe| ≤ 0.05 mΩ, 1–100 kHz)은 PowerSI의 DC 앵커 + fit 보간과의 일치를 뜻하며, 저주파 AC 해석과의 독립 일치는 아니다. 현재 사다리(exp8)에서 1–100 kHz 구간의 점은 30.2 kHz와 100 kHz 두 개뿐이므로 G1은 사실상 DC R 한 값의 검사다.
- **H_solve**(귀무): 저주파 참조는 점별 해석값이다. 수백 개 decap(각기 다른 C·ESR)과 mesh 잡음 때문에 4극 이하 유리함수 잔차는 1e-4 이상에 머문다.

## 3. 데이터·전처리
- 입력: `ref_npz("260729")` = `D:\Downloads\examples\analysis\S4LB002_260729_Zdiag.npz` (`freq` 826점, `Zdiag` 826×92). 원본 Touchstone `S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p`(`# Hz S RI R 1`, 유효숫자 15자리)를 다시 읽어 S_ii도 쓴다. 두 파일의 SHA-256을 영수증에 기록한다. 원본은 읽기만 한다.
- 정밀도 바닥: S가 15자리이므로 |Z| ≈ 1 mΩ에서 Z의 상대 정밀도 ≈ 1e-12. 판정 문턱 1e-6은 바닥보다 6자리 위다.
- 대역: 1 Hz ≤ f ≤ 100 kHz (0 Hz 점 제외, 126점). 0 Hz 점은 별도 보고.
- 대상: 260729 전 92포트(참조 데이터만 다루며 모델은 쓰지 않는다). 260804·s5m6585는 같은 동결 기준으로 확인용으로만 돌린다(참조 특성 판정이지 모델 튜닝이 아니므로 held-out 규칙과 충돌하지 않는다고 본다. 이견이 있으면 260729만 실행).

## 4. 방법
1. **Vector fitting**(Gustavsen–Semlyen, 상대 가중 1/|Z|): `Z(s) ≈ d + s·e + Σ_k r_k/(s − p_k)`, 실수 극과 켤레 극쌍 허용, 초기 극 대역 내 log 등간격, 반복 ≤ 20회. 극 수 n_p ∈ {1, 2, 3, 4} 주 판정, {6, 8}은 민감도(보고만).
   - 자체 검증: 합성 RLC(2극) + 소량 3번째 극 데이터로 잔차 ≤ 1e-12 확인 후 참조에 적용.
2. **잔차 정의**(포트 i, 극 수 n_p): 대역 내 점에서 ε_k = |Z_fit(f_k) − Z(f_k)| / |Z(f_k)|. RMS_i(n_p) = sqrt(mean ε_k²), MAX_i(n_p) = max ε_k. 포트별 최적 = min over n_p ≤ 4.
   - S_ii 보조 fit: 잔차는 |S_fit − S| / |1 + S| (1차에서 Z 상대오차와 동등)로 정의.
3. **경계 탐지**(보고만): 폭 1 decade의 창 [f, 10f]을 1 Hz–10 MHz에서 옮기며 n_p=4 fit 잔차를 기록. 잔차가 10배 이상 뛰는 첫 창 하단을 fit 영역 경계 f_b로 보고한다. G2 대역(0.1–1 MHz)이 경계 안쪽인지 함께 적는다.
4. **보조 진단**(보고만): (a) 참조 grid가 규칙적 log grid에서 adaptive로 바뀌는 주파수, (b) Re Z < 0인 포트와 최대 주파수, (c) 0 Hz 점 Z(0)과 fit의 DC 극한 비교.

## 5. 판정 기준 (동결)
포트별 최적 RMS(n_p ≤ 4)를 Z_ii와 S_ii 각각 계산한다. 포트가 "fit 수준"이라 함은 둘 중 하나라도 RMS ≤ 1e-6 이고 MAX ≤ 1e-5 인 것이다.
- **FIT**: 92포트 중 ≥ 90 %(≥ 83개)가 fit 수준.
- **SOLVED**: Z_ii·S_ii 모두 n_p = 4에서 RMS의 포트 중앙값 > 1e-4.
- **INDETERMINATE**: 그 외. 이때 n_p 6·8 결과와 경계 탐지 결과를 참고 자료로만 보고한다.

## 6. G1 해석 (결과별로 미리 적음)
- FIT이면: G1 게이트 자체는 D3대로 유지한다(변경 없음). 단 보고서와 `RESEARCH_LOG.md` §6에 "G1은 PowerSI DC 앵커·fit 보간과의 일치이며 사다리 점 30.2 k/100 kHz는 DC R 검사와 동등"이라 적는다. f_b가 100 kHz 위이면 G2(ΔL) 추출 구간 일부도 fit 값임을 명시한다. 과제 #2(MaxEdgeLength 재해석) 판정 H1/H2에는 영향 없다(1 MHz 이상).
- SOLVED이면: G1을 실제 AC 저주파 일치로 그대로 해석한다.
- INDETERMINATE이면: 해석을 바꾸지 않고, 소유자가 PowerSI에서 DCFitted/BBSFitted를 끄고 P18을 재export하는 확인 실험을 제안한다.

## 7. 산출물
- 코드: `tools/research-claude/exp10/fit_lowfreq.py`(VF + 판정 + 그림), `README.md`. `src/`는 쓰지 않는다. 경로는 `common/paths.py`만 쓴다.
- 영수증: `WORK_DIR/exp10/result_exp10_{design}.json` — inputs(파일 경로·SHA-256), params(대역, n_p 목록, VF 반복·가중, 문턱), 포트별 {best n_p, RMS, MAX, poles}, Port18의 freq/Z/Zfit(대역 내), 경계 f_b, 보조 진단, judgement, stats, wall_seconds, peak_rss_mb.
- 보고서: `WORK_DIR/exp10/EXP10_REPORT.md`, 그림 `exp10_residual_vs_np.png`, `exp10_window_residual.png`(Port18).
- 저장소 사본: `docs/research-claude/2026-09-15/results/exp10/`(새 폴더, 기존 폴더는 손대지 않음).
- 예상 비용: 92포트 × 6개 n_p × VF ≈ 1분 이내, 메모리 < 500 MB.

## 8. 승인 범위 (2026-09-16, 소유자 승인 "깎은 버전")
§5 판정 기준과 §6 해석은 그대로다. 실행 범위만 줄인다.
- 주 판정은 Z_ii(260729, 92포트, n_p 1–4). S_ii fit은 Z 판정이 FIT이 아닐 때만 자동 실행한다(§5의 "둘 중 하나" 규칙 유지).
- n_p 6·8 민감도, 1-decade 슬라이딩 창 경계 탐지, Z(0) 비교는 Port18에만 적용한다.
- 260804·s5m6585는 이번 실행에서 제외한다. 필요하면 같은 스크립트를 `--design` 플래그로 나중에 돌린다.
