# EXP-20 계획 — via R의 표피효과 (m), 벽 표피 R과 결합 (mk)

작성 2026-09-16, 실행 전 사전 등록. EXP-19 H_e(저주파 결손과 고주파 부족이 같은 급전 경로에서 나옴) 후속. 기준선 exp13/j. 자유 계수 없음(폐형식).

## 1. 규칙(고정)
- 현재 via R은 DC 값 `ℓ/(σA)`로 주파수 무관(`estimate_via_segment_rl`, 분류 SOLID/HOLLOW). 변형 m = `c_unit_fix=True` + `via_R_skin=True`:
  - SOLID(충전 microvia, 반지름 a = D/2): 원형 도체 정확해 `Z′(f) = γ/(2πaσ) · J0(γa)/J1(γa)`, γ = (1+j)/δ, δ = 1/√(πfμ0σ). `R_via(f) = ℓ·Re Z′(f)`. DC 극한 = ℓ/(σπa²)(기존 값과 일치).
  - HOLLOW(도금 배럴, 두께 t_p = min(20 µm, D/4)): `R_via(f) = Re[Zs1(f; σ, t_p)]·ℓ/(πD)`(Zs1 = 기존 `copper_surface_impedance` 한 면 유한두께식). DC 극한 = ℓ/(σπD t_p)(기존 값과 일치).
  - via L은 바꾸지 않는다(내부 L을 넣지 않는다 — EXP-18에서 내부 L이 G2를 깼음). 패드 링크 불변.
- 변형 mk = m + `zs_wall_skin_re=True`(EXP-18b k2의 벽 표피 R 결합).

## 2. 사전 예측
- ≤ 1 MHz: 60 µm 충전 via에서 a/δ(1 MHz) ≈ 0.46 → R 증가 ≤ 0.1 % → |ΔZ|/|Z| < 1e-3, 7케이스 G1·G2·G3 PASS 집합 불변.
- 10–100 MHz: 60 µm via는 100 MHz(δ 6.5 µm)에서 R_ac/R_dc ≈ a/(2δ) + 0.25 ≈ 2.6배. via R 비중이 큰 레일(EXP-16 H_a)에서 r_HF가 0 쪽으로 이동. 7케이스 r_HF 중앙값(기준선 −0.144)의 절대값 30 % 이상 감소를 예측하되, 92포트에서는 via R 비중이 높은 포트가 다수이므로 EXP-18b보다 큰 개선(중앙값 |ΔR|/Re Z_ref 0.254 → 0.15 이하)을 기대한다.

## 3. 방법과 판정 기준(동결) — EXP-18b §3과 동일
- (a) 7케이스 G1·G2·G3 PASS 집합 불변, ≤ 1 MHz max |ΔZ|/|Z| ≤ 1e-3.
- (b) 7케이스 10–100 MHz ΔR/Re Z_ref 중앙값 절대값 30 % 이상 감소(기준선 −0.144).
- m과 mk 각각 판정. (a)(b) 성립한 변형은 92포트 실행(`runall15.py --variant m|mk --outdir exp20`), EXP-17 H3 지표로 채택 제안 여부 결정(ΔR < 0 비율 ≤ 0.70 또는 중앙값 |ΔR|/Re Z_ref ≤ 0.15). 둘 다 성립하면 mk를 우선 실행하고 m은 7케이스 결과만 기록한다.
- 기본 경로 불변 검증(`--variant none --smoke` ≤ 1e-9, `--variant j --smoke --smoke-baseline exp13:j` ≤ 1e-9, `smoke_port18.py` PASS). 구현 자체 검증: 합성 via(D 60 µm, ℓ 30 µm)에서 f→1 kHz R이 DC 값과 1e-6 이내 일치, 100 MHz R_ac/R_dc가 2–3 범위.
- G1–G5 정의 불변.

## 4. 산출물
`result_*_any_{m,mk}.json`, `compare_m.json`, `compare_mk.json`, 92포트 시 `compare92_*.json`, `EXP20_REPORT.md`, 저장소 사본 `results/exp20/`.
