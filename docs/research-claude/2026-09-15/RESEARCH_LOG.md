# 연구 로그 — SPD→Z(f) 경량 모델 (2026-09-15)

읽기 전용 검토 3건(`review_deep.md`, `proposal_fresh.md`, `review_deep_exp1.md`/`review_fresh_exp1.md`)과 실험 EXP-1~9를 하루 안에 수행한 기록이다. 기존 코드/파일은 수정하지 않았다(모두 새 파일). 모든 수치는 인용 보고서 파일명을 붙였다. 약칭: `S/` = `docs/handoff/2026-09-15/snapshots/`, `PR:` = `origin/codex/peer-review-20260910` 스냅샷, `SOL/` = `src/spd_decap_pi/_core/solver/`.

## 1. 검토 결론 — 폐기/유지 판정

출처: `review_deep.md` §3.

| 대상 | 판정 | 근거 |
|---|---|---|
| **Astra 전장 경로**(3.78M 미지수, FMM3D) | **DISCARD** | 1점·1포트에 2214 s, 최대 24.4 GB, info=1/3 미수렴, KCL 오차 18–50 mA(`handoff.md:130-135`). 이산화(P1/RT0/hybrid)를 바꿔도 오차 26.2–29.4%에서 정체 — 물리 형태 문제 |
| **FMM 런타임** | **DISCARD** | 1–100 MHz PDN 인덕턴스는 plane-pair `μ0h`와 via loop로 국소적으로 얻어짐. 보드 전체 Laplace FMM 불필요 |
| **정적 전처리기 연구** | **DISCARD** | 폐기할 모델(Astra)의 수렴 문제를 푸는 작업. 고쳐도 목표 지표 불변(`HQ_STOPPED_CHECKPOINT:26-30`) |
| **layerwise 등전위 모델**(제품 기본값) | **DISCARD(물리)** | 면 내부 R/L 없음. loaded rail RMS 15.91–16.74 dB(`WORK_EXECUTION_BASELINE.md:464-471`, `RESEARCH_STATE.md:212-223`). decap 모델·Touchstone S→Z·포트 매핑만 재사용 |
| **MFDM 커널**(`mfdm.py`) | **KEEP(커널)/REWRITE(연결)** | 목표 대역 물리(Swaminathan–Engin plane-pair). 실보드 비교 기록 없음, 제품 미연결(`mfdm_adapter.py:3-6`) |
| **Modal(Legacy)** | **KEEP(교차검증)** | 기록된 loaded 정확도 중 최선(median 2.6 dB, `EVALUATION_DISTRIBUTION_VALIDATION_2026-08-06.md:109-113`). 직사각 가정으로 일반해 아님 |
| **via PEEC 적분 함수** | **REWRITE** | 적분식은 유효하나 고립 self-L은 PDN loop L이 아님 |
| **SPD 파서**(`spd.py`) | **KEEP** | mmap, 1.1 GB 저메모리 처리. `.Port` 파서는 신규 작성 필요(제품에 없음, grep 0건) |
| **평가 파이프라인 상위 계층**(proof/certificate) | **DISCARD** | fail-closed 형식 검증에 치우침. hash rotation 등 절차가 연구 속도 저해(커밋 339개 대부분 `docs: locate …`) |
| **정확도 정책 문서** | **REWRITE** | 수치 기준(241점, 복소/dB/위상)은 유지, 절차만 단순화 |
| **holdout 정책**(260804 보류) | **KEEP(원칙)/REWRITE(운영)** | 원칙은 옳으나 260729/260804가 같은 설계 계열이라 약함. 타 설계(s5m6585 등) 등록 필요 |

**신규 결론(proposal_fresh.md §0):** 1 kHz–100 MHz 대역은 업계 표준으로도 2-D plane-pair + 회로(via/pad/decap) 하이브리드로 푼다. PowerSI 자신이 "hybrid solver"(plane/EM + circuit + transmission line, Cadence 포럼 57376)다. 380만 미지수 3-D 적분방정식은 이 대역에서 물리적으로 과잉.

## 2. 데이터 사실

출처: `data_characterization.md`, `review_deep.md` §1.2.

- **SPD 파일 3종**(사용자 PC `D:\Downloads\examples`, 컨테이너 마운트 `~/mnt/examples/`): `S4LB002-2Para_260729_1_injected.spd`(1.1 GB), `…_260804_1_injected.spd`(1.1 GB, 260729의 재작업본), `s5m6585_32p_260414_length3_1.spd`(139 MB, 소형 2층 보드).
- **스택업**(260729/260804 공통): 48개 도전층(TOP~L47~BOTTOM), Cu 20–35 µm, ABF-GL102(260729)/EL190T(260804 전층) 유전체. `L14`(Cu 20 µm, 위 DR1314 30 µm→L13 DGND, 아래 30→L15(타넷 전원면)→30→L16), `L25`(Cu 32 µm, 위 100 µm→L24, 아래 105→L26(타넷)→100→L27). σ_Cu=5.959e7 S/m.
- **포트**: 260729/260804 각 92개(`Port1_SITE0`~`Port92_SITE1`, SITE0/SITE1 46개씩), s5m6585 160개(단일 사이트). Port18_SITE0 = `ADC_VDD_075_VTRIP_SRAM/0`, +단자 978개 DUT 범프, −단자 10,919개 DGND 노드.
- **decap**: 260729 `.Component` 11,173개(디캡 11,162: 1 µF 6,490 / 100 nF 4,410 / 10 µF 254 / 68 nF 8) + DUT/LGA/정렬. 260804는 디캡 10,869개(293개 감소, 1UF −151/100NF −142). Port18 레일 decap 421개. 모델은 Murata SPICE 래더(최대 13단)를 그대로 `Y(f)=1/Z_SPICE(f)`로 사용(`spice.py:185,374`).
- **PowerSI 관례 관련 지시문**(SPD 원문, `sigrity_voids.md`): `.DropShapesOfUnselectedNets`, 층별 `SmallHoleThreshold=0.0015`, `.Configuration SmallHole_Deletion_Factor=0.00333`, `.PowerSI .MaxEdgeLength=4.97e-3`(260729)/`1.5e-2`(s5m6585) m. **260729/260804에는 태그된 special/small-hole void가 0개**다(s5m6585에는 약 16만 개 있음) — 단위·기본 동작은 미검증.
- **Touchstone**(.s92p/.s160p): `# Hz S RI R 1`(Hz, S-param, 실수-허수, 기준임피던스 1Ω). 92포트 826주파수, 160포트 626주파수. Z 변환은 `Z=Z0(I+S)(I−S)^{-1}`(전체 행렬, 포트별 open 조건). 검증: 92p 데이터 줄 수가 2,116×826과 정확히 일치, 상반성(S21=S12) 확인, 1 MHz Z18 목표치(6.915e-4−j4.258e-4 Ω)와 일치. 산출물(재현용, 사용자 PC): `D:\Downloads\examples\analysis\{S4LB002_260729_Zdiag.npz, S4LB002_260804_Zdiag.npz, s5m6585_Zdiag.npz, scripts/}`.
- **저주파 외삽 의심**: 260729 92포트 중 10개, s5m6585 160포트 중 20개에서 f<175 Hz(≤1.1 kHz)에 Re(Z)<0 — Cadence 포럼의 "0 Hz까지 외삽" 보고(66324)와 정합하는 아티팩트로 추정(`TRACKA_REPORT.md` §6-4).

## 3. 채택 모델 정의 (2026-09-15 EOD 기준, EXP-8 최종형)

2-D plane-pair 하이브리드. 층마다 균일 격자(coarse h, 포트 핀필드 bbox+1 mm 안 fine h, TOP h) 위에 절점 어드미턴스를 조립하고 `scipy.splu`로 주파수마다 직접 해석한다.

- **셀 edge 임피던스**: `Z = [Zs_rail(f) + Zs_wall(f)]·(ℓ/w_eff) + jωμ0·d_eff·(ℓ/w_eff)`.
  - `Zs`는 유한두께 표면임피던스. **양측 대칭 규칙**(EXP-4 (b)): 셀의 위/아래가 모두 금속으로 덮이면 `½·Zc·coth(γt/2)`(저주파 극한 내부 L = `μ0t/12`). 한쪽만이면 기존 `Zc·coth(γt)`(내부 L = `μ0t/3`, 한쪽 귀환 가정).
  - `d_eff = d_up·d_dn/(d_up+d_dn)`(양측 조화평균). **기준면(wall) 탐색 = PowerSI 관례 모드(cavity-wall, EXP-8 채택 기본값)**: 셀 위/아래 3개 도체층 안에서 레일 자신을 제외한 **어느 넷이든** 금속이 셀을 덮는 첫 층을 벽으로 삼는다(셀 단위). **physical 모드(옵션)**: GND(reference net)만 벽으로 인정 — 떠 있는 타넷 전원면은 투명.
  - **fringing**(EXP-3 S2): 유효폭 `w_eff = G·b`(G=방향별 정규화 전도도, b=창 폭)가 `w_eff < 5d`이면 `w_eff+2d`로 대체(trace 규칙 `model.py:347`과 동일 공식을 평면에도 적용).
- **셀 shunt C**: `jωε0εr(f)A/d·(1−j·tanδ)`, εr/tanδ는 로그 f 보간 테이블. 인접 벽마다 이상 기준(C는 wall 규칙과 별도로 인접 GND/전원면에 대해 둠).
- **균질화**(EXP-3 S1): DUT 아래 hex-mesh/줄무늬 shape와 trace를 함께 10 µm(fine)/coarse 서브타일로 래스터화해 방향별 유효 전도도 G를 batched CG로 계산(완전 시트=1).
- **Trace**: `Zs·len/w + jωμ0·d·len/(w+2d)`. Width 없는 trace는 층 기본값.
- **Via**: R = `via_model.estimate_via_segment_rl`(충전 microvia/도금 배럴 판정, `via_model.py:61,132`). L = 동축형 `μ0/(2π)·len·ln(s/r)`, s=같은 층 최근접 DGND 노드 거리(중앙값 130 µm, [2r,1mm] 제한).
- **decap**: SPICE subckt `Y(f)=1/Z_SPICE(f)`를 레일 핀–GND(이상 기준) 사이에 stamp.
- **포트**: +단자(DUT 범프)를 초노드로 short, 1 A 주입. −단자(DGND)는 이상 기준(PowerSI의 pin-group short 관례와 동일, `review_deep_exp1.md` T3).
- **이상 GND**: GND 도체망(via/trace/시트)의 R/L은 stamp하지 않는다 — EXP-2/EXP-3 S3/EXP-9에서 반복 확인된 대로, **명시 GND R을 넣으면 참조보다 항상 커진다**(§4).
- **패드 접촉**: 부품 footprint 안 같은 넷 노드를 패드 노드에 링크(1608 10 µF 등 via-array 위 무-trace 부품에 필수).

## 4. 실험 사다리 EXP-1 ~ 9

각 실험의 게이트: G1 1–100 kHz \|ΔRe\|≤0.05 mΩ, G2 0.1–1 MHz ΔL(f) ±5 pH, G3 1 MHz 복소오차 <10%, G4 1–10 MHz 오차<20%·f_res±10%, G5 10–100 MHz(보고만). 기준 레일은 260729 Port18(`ADC_VDD_075_VTRIP_SRAM/0`) 이며 참조는 PowerSI Touchstone.

### EXP-1 / 1b — 평면쌍+회로 하이브리드 최초 구축
가설: 단일 인접 GND 시트 평면쌍이면 충분하다. 변경: SPD 신규 파서(포트/trace/via), 균일+국소 fine 격자(h=200/50/50 µm, 223,936 미지수), 이상 GND(B) vs 명시 인접시트(A).

| 게이트 | A(primary) | B(이상 GND) | 1b(양측 d_eff) |
|---|---|---|---|
| G1 | FAIL 0.587 mΩ | PASS 0.029 | PASS 0.028 |
| G2 | FAIL +32.7~37.7 pH | FAIL +25.7~37.5 | FAIL +17.8~23.2 |
| G3(1MHz) | FAIL 83.5% | FAIL 23.2% | FAIL 16.3% |
| G4 | FAIL 108%, f_res−40% | FAIL 60%, −31% | FAIL 47%, −28% |

판정: A는 GND 시트 R을 직렬로 이중 계상해 전부 FAIL. B(이상 GND)가 G1만 통과. 1b(양측 d_eff, 이웃 GND shape 실측)는 L 과다를 14 pH 줄였으나 G2/G3 미달 → **EXP-2(다도체)로 진행**.

### EXP-2 — 다도체(stacked-plane) 모델
가설: 명시 GND 도체망이 R/L 분배를 스스로 결정하면 G1/G2가 동시에 닫힌다. 변경: L13/L14/L15/L16/L24/L25/L26/L27을 공통 격자에 명시, `Z=diag(Zs)·sq+jωμ0·G·sq`(다도체 셀).

| 게이트 | 사양(400/200) | 전GND(6주파수) |
|---|---|---|
| G1 | FAIL 0.498 mΩ | FAIL +0.23~0.28 |
| G2 | FAIL +34~135 pH | FAIL +45~136 |
| G3 | FAIL 94.2% | FAIL 73.3% |

**기각(다도체 partial-L gauge 문제)**: `min(z)−z0` 공식이 기준높이 z0에 의존하며, 열 밖 도체(TOP/trace/via)의 수평 전류로 열 전류 합이 0이 아니어서(1MHz Σ\|ΣI\|²/Σ\|I\|²=7–18%) ill-posed. z0를 위/아래로 바꾸면 1MHz ΔL이 30.1↔33.8 pH로 흔들림. 결론: **PowerSI R은 이상 GND와 같게 행동한다**(명시 GND는 항상 R을 키움) → **다도체 방향 중단**, EXP-1b(양측 d_eff, 이상 GND)로 복귀.

### EXP-3 — 소거(ablation) 사다리 S0→S3
가설: 균질화/fringing/명시GND R을 단계적으로 더하면 어디서 갈라지는지 알 수 있다.

| 단계 | G1 | G2(0.1–1MHz) | G3(1MHz) | G4 | 미지수 |
|---|---|---|---|---|---|
| S0(1b) | PASS 0.029 | FAIL +17.8~23.4 | FAIL 16.3% | FAIL 43%/1.198MHz | 223,936 |
| S1 균질화 | PASS 0.009 | FAIL +18.5~23.9 | FAIL 15.6% | FAIL 44%/1.199 | 261,124 |
| S2 +fringing | PASS 0.009 | FAIL +15.2~19.1 | FAIL **12.7%** | FAIL 40%/1.231 | 261,124 |
| S3 +GND R시트 | **FAIL 0.104** | FAIL +15.2~19.2 | FAIL 20.5% | FAIL 33%/1.218 | 664,899 |

**기각(균질화, S1)**: 10/5 µm 연결성 균질화는 L14 R·L25 L을 사실상 바꾸지 않음(리뷰의 −12%/−12~22% 과소전도 가설 기각). fringing(S2)은 L25에서 ΔL을 −4.3 pH만 줄임(초과의 약 1/5). **명시 GND R(S3)은 실재하는 +0.095 mΩ**(층별 0.021~0.007 mΩ 고른 분포)이지만 넣으면 G1·G3 모두 **악화** — R과 L이 동시에 참조보다 커짐. 격자수렴은 균질화 이후 1% 미만(S4, 200/25/25 µm).

### EXP-4 — 도체 내부 인덕턴스 감사
가설(코디네이터 제기): 한쪽 귀환식 `μ0t/3`을 양측 귀환 셀에 쓰면 내부 L을 과대 계상한다. 코드 확인: `copper_surface_impedance`(mfdm.py:782)는 one-face 식(docstring 명시)이며, EXP-1~3 전부 이를 양측 셀에도 사용 — **의심이 사실로 확인됨**.

| 변형 | G1 | G2 | G3 | G4 |
|---|---|---|---|---|
| S2 orig(한쪽식) | PASS | FAIL +15.2~19.1 | FAIL 12.7% | FAIL 40%/1.231 |
| (a) 내부L=0 | PASS | FAIL +8.5~10.9 | **PASS 7.7%** | FAIL 31%/1.285 |
| **(b) 양측대칭 ½Zc·coth(γt/2)** | PASS 0.009 | FAIL +10.5~13.3 | **PASS 9.1%** | FAIL 36%/1.268 |
| (c)(b)+GND R×6/13 | FAIL 0.060 | FAIL +9.9~12.8 | FAIL 13.0% | FAIL 32%/1.273 |

판정: 물리적으로 정당한 (b)를 채택(내부 L 초과 19→13 pH, G3 12.7→9.1% PASS, 격자에도 견고 9.0%@fine25). G2는 여전히 미달(+12~14 pH 잔존, L25 외부 경로에 집중). **내부 L 가설은 부분적으로만 성립**(19 pH 중 약 6~9 pH를 설명). (c)에서 GND R을 넣으면 다시 FAIL — EXP-3 결론 재확인.

### EXP-5 — held-out 260804 + 다중 레일 (S2+(b) 동결, 튜닝 없음)

| 게이트 | 260729(개발) | 260804(held-out) |
|---|---|---|
| G1 | PASS 0.008 | PASS 0.045 |
| G3(1MHz) | PASS 9.14% | FAIL 10.03%(경계) |
| 공진비(±3%) | — | PASS(모델 0.909 vs 참조 0.895/0.912) |

7레일 상관: ΔL fit vs **모델 평면 L의 Pearson 0.994**, 원점통과 기울기 **0.31**, R²=0.98. port7(decap 1034개)만 비율 0.08로 이탈, 나머지(core/buildup-only/decap적음 무관) 0.21~0.33에 수렴. **결론: 초과 L은 평면쌍 L 공식 자체가 전 레일에서 약 25~30% 과대인 전역 비례 항**(기하 위치·decap 수 무관). R 편차는 레일마다 부호·크기가 달라 decap별 실장 저항의 개별 결손으로 별도 분류.

### EXP-6 — 평면 void(천공) 단순화 감사
가설: PowerSI가 anti-pad/small hole을 채워서(구리로 단순화) L·R이 함께 작아진다. 사실 확인: 260729/260804에는 태그된 special/small-hole void가 0개(모델은 export된 void를 그대로 씀 → V1=V0). 진단으로 크기기준(<200/400/1500 µm) void를 강제로 채움.

| 모드 | ΔL/L_plane(port18) | 1MHz 오차 |
|---|---|---|
| V0 | 0.251 | 9.14% |
| D<1500(전부 채움) | 0.233 | 15.4%(R이 참조보다 낮아짐) |

**기각(void 단순화)**: 530 µm까지 모든 void를 채워도 비율은 0.25→0.23으로 거의 불변(0으로 가려면 −90% 필요, 실제 −7~10%). R은 오히려 참조보다 낮아짐(ΔRe −0.13~−0.96 mΩ) — "구멍을 지워 R이 낮다"는 설명과 부호가 반대. port1·port19도 동일 패턴(0.33→0.30).

### EXP-7 — 커널 해석해 검증 + 거친 mesh 모사
(i) 무한 평면쌍 해석해(`ln(s²/r1r2)/2π`)와 격자 커널을 비교 — **커널 자체는 옳다**(1–8% 이내 일치, 격자 분산). via 접촉을 노드 1개로 두는 데서 오는 접촉 초과는 fine box 세분(50→25 µm, ΔZ 0.55%)에 견고해 무관.

(ii) **기각(거친 mesh 가설)**: h=0.25~5 mm로 PowerSI mesh(`MaxEdgeLength`)를 흉내내도 port18 ΔL은 +9.4~15.7 pH로 비단조 진동만 할 뿐 0에 접근하지 않음. port1은 거칠게 할수록 ΔL(+94→+181 pH)·ΔRe(+0.02→+0.55 mΩ) 둘 다 반대 방향으로 **악화**. "mesh가 핀필드를 해상 못해 R·L이 함께 준다"는 서명(동시 0 교차)은 재현되지 않음.

(iii) **사용자 확인 실행 요청**(§6에서 재정리).

### EXP-8 — "cavity-wall(PowerSI 관례)" 기준면 규칙
가설(EXP-1c 재해석): 저Q 떠 있는 타넷 전원면이 물리적으로 자기 투명해도, **PowerSI가 넷과 무관하게 인접 금속을 벽으로 계산할 가능성**이 있다. §5 결과 참조. 예측(d_eff 비 × EXP-5 층별 L) 대 실측 상관 0.95~0.97, 5/7 케이스에서 ΔL이 ±3.3 pH로 붕괴. **EXP-1c는 물리 주장으로는 여전히 기각**(§4 EXP-1c 문단 참조)이나 **도구 관례로는 채택**.

**EXP-1c 재검토 메모**(`review_fresh_exp1.md` Q1): 물리적으로 L15(20µm Cu, R□=0.84mΩ/□)는 차폐비 |α|≈0.27로 약한 차폐, L26(32µm)은 |α|≈0.83으로 강한 차폐지만, 둘 다 (1) 레일 전류가 대부분 비회전(irrotational)이라 유도전류로 상쇄할 몫이 작고, (2) 갈랍닉/용량 폐로 임피던스(약 80Ω~1kΩ@1MHz)가 이득(0.09 mΩ)보다 6자리 커서, (3) 필요 보정 방향(저주파에 크고 고주파에 작음)이 ωα에 비례하는 물리적 차폐와 반대라서 **물리적 "떠 있는 판=귀환면" 가설은 기각**이 유지된다. EXP-8의 성공은 이 물리를 재현하는 게 아니라 **PowerSI가 벽을 어떻게 정의하는지에 대한 도구 관례를 추정**한 것이다.

### EXP-9 — port19 R 결손 판별
가설 a~d(decap 모델/경로 누락/24 mΩ균일R/포트 정의)를 전부 검정 — 모두 기각(§6).

### EXP-10 — 1–100 kHz 참조 신뢰성 판정 (2026-09-16, 참조 데이터만, 모델 미사용)
가설: 저주파 참조가 DC 점+rational fit 생성값이면 1 Hz–100 kHz Z_ii/S_ii가 ≤4극 실계수 유리함수에 RMS ≤1e-6으로 맞는다(H_fit). 방법: vector fitting(상대 가중), 92포트, 사전 등록 `results/exp10/EXP10_PLAN.md`. 판정(동결 기준): **SOLVED** — Z fit 수준 0/92, S 10/92, n_p=4 RMS 중앙값 Z 5.8e-2·S 2.3e-4. 단 사후 진단에서 0.1–2 Hz 어드미턴스가 `Y=(G+jB)+jωC`로 잔차 ~1e-5에 맞고 **상수 서셉턴스 B≠0(80/92포트, Port18 1.26 mS)**, G<0(8포트), 파일 Z(0)이 AC 극한과 불일치. 켤레 비대칭 항이라 실계수 유리함수로 맞을 수 없어 SOLVED로 떨어진 것이며, ≤1 kHz 참조는 비물리 생성값으로 본다. G1 대역(1–100 kHz)에서 B 항 기여 ≤7.6e-4(1 kHz), 30.2 k/100 kHz 사다리 점에서 2.5e-5/7.6e-6 → G1·G2 해석 변경 없음. 상세 `results/exp10/EXP10_REPORT.md`.

**EXP-10b (같은 날 후속, 사전 등록 `results/exp10/EXP10B_PLAN.md`)**: 1–100 kHz만(51점) 실계수 vs 복소계수 VF → **INDETERMINATE**(n_p=4 RMS 중앙값 실 1.55e-4 / 복소 2.5e-6, fit 수준 0/92, 비 61 → 비대칭 항이 이 대역 실계수 잔차도 지배). 사후 민감도: 복소 n_p 6/8이면 중앙값 8e-8/2.5e-8, fit 수준 75/80포트. 물리가 이 대역에서 저차에 가까워 유리함수 잔차만으로는 fit/해석 판별 불가. 저주파 진단(0.1–2 Hz, 세 설계): S4LB002 두 설계는 |B|>0.01 mS 포트 87–89 %, s5m6585는 B≈0이나 G<0 17포트·Re Z<0 20포트; Z(0) 불일치 44–60 %는 세 설계 공통 → ≤1 kHz 참조 자기모순은 공통, 형태만 다름. 세 SPD 모두 `DCFitted=1 BBSFitted=1`. G1·G2 해석 변경 없음. 상세 `results/exp10/EXP10B_REPORT.md`.
**정정(2026-09-16, 소유자 확인)**: 참조 `_S` 파일은 DCFitted 미적용, BBSFitted 비활성. ≤1 kHz 비물리 항은 DC 합성이 아니라 Adaptive sweep 유리함수 보간(AFS) 산물로 본다. 재해석 요청은 "저주파 Log/Linear 이산 스윕, 전 포트"로 변경(`EXP10_REPORT.md` §7).

### EXP-11 / EXP-12 — [구현 차이] 되돌리기 (a)(b) / (c)(d) (2026-09-16, 7케이스, 플래그, 기본값 불변)
러너 `tools/research-claude/exp11/run11.py --variant a|b|c|d`, 비교 `table11.py`, 사전 등록 `results/exp11/EXP11_PLAN.md`, `results/exp12/EXP12_PLAN.md`. 기본 경로 불변은 `--variant none --smoke` 상대차 5.83e-12로 확인.
- (a) εr/tanδ 주파수 테이블, (b) 비인접 기준면 C 복원: 7케이스 전부 |ΔZ|/|Z| ≤ 2e-9, 게이트 불변 → 기본값 유지. **원인은 새로 발견한 단위 오류**: 채택 경로 `exp1b.TwoSided.__call__`의 셀 C가 `EPS0*1e-12/Σ(t_um/εr)`로 µm→m 환산 1e-6이 빠져 평면 C가 1e6배 작다(모델 총 4.7e-6 nF, 올바른 값 약 4.7 nF; 비채택 A/B `model.py:525`는 올바름). EXP-13(변형 j, jab)에서 수정 효과를 시험한다.
- (c) 양측 Zs 셀 단위(엣지 양 끝 셀 모두 양측일 때 Zs2): 게이트 불변, ΔL +0.2~+1.7 pH(엣지 기준 양측 비율 L14 0.886·L25 0.903으로 층 과반 규칙보다 Zs2 적용이 줄어 내부 L 증가), G3 중앙값 6.32→6.39 % → 기본값 유지.
- (d) 벽 Zs 포함(한쪽 Zs1, 양측 병렬): P1·P7·P18·804-P18에서 G1 PASS→FAIL(ΔRe +0.16~+1.50 mΩ), G3 중앙값 6.32→23.7 % → 기본값 유지. 보고만: R 결손 케이스 P16(14.0→5.6 %, G3 FAIL→PASS)·P19(29.9→23.7 %)는 개선. 레일 발자국에 갇힌 벽 R은 실제 GND 귀환 R(EXP-3 S3 +0.095 mΩ)을 2배 이상 과대평가한다.
상세 `results/exp11/EXP11_REPORT.md`, `results/exp12/EXP12_REPORT.md`.

### EXP-13 — 평면 C 단위 오류 수정 (2026-09-16, 7케이스, 플래그 `c_unit_fix`)
사전 등록 `results/exp13/EXP13_PLAN.md`. (j) 수정만: 총 평면 C 0.8–6.9 nF(이전 1e6배 작음), |ΔZ|/|Z| ≤ 3.6e-4(≤10 MHz, P19 제외 5e-3), 100 MHz 0.2–1.8 %(P19 35 %: 22+266j → 44+352j mΩ, 참조 111+367j, G5 오차 0.35→0.18). 게이트 PASS 집합 7케이스 불변, G3 중앙값 6.32 % 불변 → 계획 §4(산술 오류 수정은 PASS→FAIL 없음이면 채택 후보)에 따라 **새 기본값 후보로 제안**(소유자 승인 필요; 권장은 코드 기본값·EXP-8 영수증 유지 + EXP-14 이후 기준선을 exp13/j로 정의). (jab) = (j)+(a)+(b): (j)와 ≤3e-4 차이, 채택 안 함. 상세 `results/exp13/EXP13_REPORT.md`.

### EXP-14 — [구현 차이] 되돌리기 (e)(f)(g)(h) (2026-09-16, 7케이스, 기준선 exp13/j)
사전 등록 `results/exp14/EXP14_PLAN.md`. (e1) fringing 문턱 제거: ΔL −0.05~−1 pH, 게이트 불변. (e2) 상한 제거(비물리 감도): G2 FAIL ×3, 채택 불가. (f) 외부 L 1/G 미적용: ΔL −0.2~−3 pH, G3 중앙값 불변. (g) via 배럴 정확 면적: core PTH 351/30,520개만 영향, ΔRe ≤ +0.005 mΩ — **port19 R 결손 원인 아님**. (h) via L 2배: G2 FAIL ×3이지만 **G5는 크게 개선**(P18 0.279→0.165, P14 0.405→0.220) → via 유효 L의 주파수 의존(귀환 전류 집중) 단서, §6-6 참고. 전 변형 기본값 유지. (i) 실장 루프 L은 decap 실장 기하 부재로 미착수. 상세 `results/exp14/EXP14_REPORT.md`.

### EXP-15 / 16 / 17 — 92포트 일반화와 진단 (2026-09-16, 기준선 exp13/j)
- **EXP-15**(`results/exp15/`): 260729 92포트 전부 실행(실패 0, CPU 10.3 h, 미지수 11k–1.2M). 6포트 재현 상대차 0. **G1 8/92, G2 10/92, G3 30/92(32.6 %), G4 0/92**, err_1MHz 중앙값 24 %(사분위 8.5–36 %, 최대 382 %). 사전 예측 G3 ≥ 50 % **기각**. G3 FAIL 62 중 R 결손형 53. 같은 레일의 SITE0/SITE1에서 dRe 부호가 바뀌는 쌍이 과반(P19 −6.0 vs P65 +10.1 mΩ). 채택 모델은 7케이스 밖으로 일반화되지 않는다.
- **EXP-16**(`results/exp16/`): R 결손 상관. H_a(via+trace R 비중, ρ 0.79) 채택, H_d(참조/모델 R 비 IQR 0.29, 중앙값 1.17) 채택, H_c(SITE 쌍 일치 43 %) 기각, H_b(비GND 기준면)는 전 포트가 타넷 기준을 포함해 판별 불가. 원인 후보는 핀필드·포트 국소 경로 R 관례. 소유자 확인 대상은 Port19_SITE0/Port65_SITE1 한 쌍(PowerDC 요소별 전류).
- **EXP-17**(`results/exp17/`): ΔL(f)·ΔR(f). **H3 채택: 92포트 중 90에서 10–100 MHz 모델 R이 참조보다 작고 부족분 중앙값 25 %**(귀환면 손실·표피효과 미포함 서명). H1(고주파 L 부족) 정의상 기각이나 ΔL 지표가 용량성 레일에서 ΔC를 흡수하는 결함이 있어 재정의 필요. H2(|D| ~ via L, ρ 0.64) 채택(상관, 인과 아님).

### EXP-18 / 18b — 귀환면 표피 손실을 고주파 전용으로 추가 (2026-09-16, 기준선 exp13/j)
사전 등록 `results/exp18/EXP18_PLAN.md`(k: `Zs1(f)−1/(σt)` 복소 전체), `EXP18B_PLAN.md`(k2: 실수부만; k 스모크에서 벽 내부 L 8.4 pH/□가 1 MHz를 2.6 % 바꾸는 것을 보고 7케이스 실행 전 등록). 자유 계수 없음. k: G2 FAIL ×3 → 기록만. k2: 7케이스 (a) ≤1 MHz 6e-4·PASS 불변, (b) 10–100 MHz ΔR/Re Z_ref 중앙값 −0.144 → −0.038 → 92포트 실행. **92포트에서는 ΔR<0 비율 0.98 → 0.92, 중앙값 |ΔR|/Re Z_ref 0.254 → 0.234로 채택 문턱(≤0.70, ≤0.15) 미달 → 기록만, 기준선 유지.** 대형 평면 레일만 개선되고 대다수 포트의 고주파 R 부족은 via·trace 급전 경로 쪽으로 좁혀짐(EXP-16 H_a와 정합). 상세 `results/exp18/EXP18_REPORT.md`.

### EXP-19 — 저주파 R 결손 vs 고주파 R 부족 상관, ΔL 재정의 (2026-09-16, 92포트 자료만)
사전 등록 `results/exp19/EXP19_PLAN.md`. **H_e 채택: ρ(r_LF, r_HF) = 0.75(p 1e-17)** — 저주파 결손과 10–100 MHz 부족이 같은 포트에서 함께 크다 → §6-3·§6-6을 "급전 경로(via·trace) R 관례" 한 과제로 합침. H1′(ΔL을 2·f_res 위에서 재정의) 기각: D′ 중앙값 +135 pH — 참조 L은 주파수 상승에 따라 줄고 모델 L은 일정. via L 2배 정식화 폐기. 후속 EXP-20(via R 표피효과, 자유 계수 없음). 상세 `results/exp19/EXP19_REPORT.md`.

### EXP-20 — via R 표피효과 (m), 벽 표피 R 결합 (mk) (2026-09-16/17, 기준선 exp13/j)
사전 등록 `results/exp20/EXP20_PLAN.md`. 자유 계수 없음(SOLID via Bessel 정확해, HOLLOW Zs 폐형식; DC 극한 일치). m 단독은 7케이스 r_HF −0.144 → −0.141로 (b) 불성립(대형 평면 레일은 via R 비중이 작음). mk(+벽 표피 R)는 7케이스 −0.031로 (a)(b) 성립 → 92포트 실행. **92포트: 중앙값 |ΔR|/Re Z_ref 0.254 → 0.203, ΔR<0 비율 0.92, 게이트 불변 → 채택 문턱(≤0.15, ≤0.70) 미달, 기록만.** 두 표피 항으로는 고주파 R 부족의 1/5만 메워지며, 남은 부족은 주파수 무관한 급전 경로 R 관례(EXP-19 H_e). 상세 `results/exp20/EXP20_REPORT.md`.

### EXP-22 — 유효 L의 주파수 의존 분해 (2026-09-17, 92포트 자료만)
사전 등록 `results/exp22/EXP22_PLAN.md`. 예측(참조 L 감소·모델 L 일정)이 둘 다 **기각**: 5–30 MHz에서 참조 유효 L은 평탄(ρ_ref 중앙값 0.996)하고 모델 유효 L이 흔들린다(ρ_mod 중앙값 1.12, 사분위 0.81–1.41). EXP-19 D′는 모델 쪽 공진 구조 차이에서 온 것이며 ΔL·D′ 지표는 고주파 L 판별용으로 폐기. 문제는 G4와 같은 1–30 MHz 공진 구조(반공진 위치·높이) → EXP-23. 상세 `results/exp22/EXP22_REPORT.md`.

### EXP-23 — 반공진 위치·높이·폭 비교 (2026-09-17, 92포트 자료만)
사전 등록 `results/exp23/EXP23_PLAN.md`. 대상 54포트. 반공진 위치비 중앙값 1.10(채택), 높이비 1.02(체계적 편향 없음, 불확정), 폭비 0.92(n 17, 불확정). 표피 항(mk)은 높이 중앙값을 바꾸지 않음. 높이비 상위 8포트 중 7개가 SITE1 → 1–30 MHz 오차의 주된 산포원은 **사이트(핀필드·포트 정의) 국소 구조**(EXP-15/16 SITE 쌍 부호 반전과 정합). 예측("과대·좁음")은 빗나감. 상세 `results/exp23/EXP23_REPORT.md`.

### EXP-21 / 24 / 25 / 26 — GND-only 92포트, 사이트 비대칭, 경로 분해, decap 고립 발견 (2026-09-17)
- **EXP-21**(`results/exp21/`): GND-only 기준면 92포트. R 결손 중앙값 −6.22 → −6.21 mΩ(H_b′ 기각, 예측대로), G3 30 → 21. cavity 규칙은 R 결손 원인 아님. 기준선 유지.
- **EXP-24**(`results/exp24/`): 쌍둥이 넷 46쌍. 예측과 반대로 **참조는 사이트 대칭(P19/P65 s_ref 1.02), 모델 R이 2.27배 비대칭**. 4-decap 소형 레일에서 모델·참조 비대칭이 어긋남(24/46쌍만 10 % 내 일치).
- **EXP-25**(`results/exp25/`): 어긋난 8쌍의 요소 분해. 사이트 차의 지배 요소 via(5/8), 기본폭 trace 가설 기각. P65 모델 Re Z 29.6 = 경로 9.2 + **나머지(decap ESR 가중합) 20.3 mΩ**(P19는 6.2) → 전류 배분 왜곡 추정.
- **EXP-26**(`results/exp26/`): 모델 진단 풀이. 나머지 항 재현(20.26 mΩ). 원인은 배분 왜곡이 아니라 **P65 모델에서 10 µF decap C10105_1이 포트와 갈바닉으로 끊겨 있음**(기준면 제외 그래프에서 다른 성분, 전류 0). `decaps_connected` 지표는 기준면 경유 연결도 세어 이를 못 잡음. → EXP-27 92포트 연결성 감사.

### EXP-27 — 모델 연결성 감사와 결함 기구 (2026-09-17, 92포트 build)
사전 등록 `results/exp27/EXP27_PLAN.md`. 기준면을 제외한 소자 그래프에서 포트와 끊긴 decap: **44/92포트, 56개**(전부 TOP 패드 노드; 10 µF 8개), R 오차와 상관 p 1.2e-6(둘 다 채택). 추적: 패드 링크·via 스택은 정상이고 L10 레일 시트에서 끊김. L10 artwork는 한 폴리곤인데 래스터 시트가 65패치로 부서짐. 원인은 **`homog.batched_gx`의 경계조건**: 창 양 끝 열(셀 중심선)에 금속이 없으면 주입 전류 0 → G = 0(정확히 0). 폴리곤 가장자리 셀이 전부 고립되어 decap via 스택(내부 패치)과 하향 via(테두리 패치)가 끊긴다. 추출 문제 아님. 모든 평면 가장자리에 영향. 수정 플래그 `homog_face_fix` → EXP-28. 상세 `results/exp27/EXP27_REPORT.md`.

### EXP-28 / 29 — 균질화 결함 수정(변형 p)과 결손 구조 (2026-09-17)
- **EXP-28**(`results/exp28/`): `homog_face_fix`(창 끝 열 대신 금속이 있는 첫/마지막 열에서 주입·인출, 자유 계수 없음). 고립 decap **56 → 0**, 파국 오차 제거(최대 382 % → 54 %), P18 2.69 → **0.81 %**, P7 6.3 → 2.9 %. 그러나 92포트 G3 PASS 30 → 22, err 중앙값 24 → 32 %, 7케이스 G1 PASS→FAIL 2건(P1, 804-P18). 끊겼던 가장자리 셀이 붙어 모델 R이 내려가면서 결함이 가리던 R 결손이 드러남: 참조/모델 R 비 중앙값 1.17 → **1.46(IQR 1.15–1.58, 균일)**. 동결 규칙상 자동 채택 없음 → **소유자 결정 대기(권고: 채택)**.
- **EXP-29**(`results/exp29/`): 결손이 경로 R에 비례하는가 — 선형 OLS는 고임피던스 포트에 지배되어 무의미(설계 한계), H_path 기각. 강건 비 ΔR/R_path 중앙값 0.6–0.7, IQR 넓음. 균일 비는 decap 항을 포함한 총 Re Z에 대한 것이라 PowerSI 쪽 decap별 직렬 항(실장 R 등, §6-4(i)의 R 판) 가능성 → 소유자 확인 필요.

### EXP-30 / 31 — held-out s5m6585(PCB) 160포트, 요소별 스케일 계수 (2026-09-17)
- **EXP-30**(`results/exp30/`): s5m6585 160포트 전부 성공(형식 보완 2건: `_DGND` 층 이름, `xcall/.SUBCKT` decap 모델 fallback; 260729 비트 불변). **err_1MHz 중앙값 0.58 %, G3 PASS 128/160(80 %), Re Z_ref/Re Z_model 중앙값 1.03(IQR 1.02–1.07)**. FAIL 32개는 전부 NULL 모델 decap뿐인 ADC_IO_BUCK13 레일. G1·G2 절대 문턱은 PCB 규모에 부적절(전부 FAIL, 해석 안 함). f_res +9~16 %는 PCB에도 있음(설계 무관). H_pkg는 절대 문턱 때문에 형식상 불확정이나 규모 무관 지표로는 **R 결손(×1.46)이 패키지 특유**임이 분명.
- **EXP-31**(`results/exp31/`): y = Re Z_ref/Re Z_model − 1을 요소 비중으로 NNLS 회귀. c_planes = 0(평면 R 정확), 급전 경로(via·trace) ×2–2.5 필요(A: c_vias 1.45, B: c_traces 1.56 — 공선성), R² 0.47–0.52. 후보: 충전 microvia R 관례(길이·원추·도금) → EXP-32(via 길이 표면 간, ×1.4).

### EXP-32 / 33 — via 길이 관례(q), f_res 편향 분해 (2026-09-17)
- **EXP-32**(`results/exp32/`): via 길이를 층 중심 간 → 패드 표면 간(ℓ′/ℓ 1.40, 자유 계수 없음). 92포트 err 중앙값 31.7 → 24.4 %(개선 79/악화 13), R 비 1.46 → 1.30(편차 감소 37 %, 문턱 40 % 근소 미달), G3 PASS 22 → 34, 7케이스 P16 G2 PASS→FAIL. PCB 4포트에는 무해(변화 1e-3 이내). 동결 규칙상 **기록만**, 기준선 exp28/p 유지. 결손의 1/3만 설명 — 나머지는 microvia 스택 특유 요소(패드 관통·랜딩 R)로 추정, PowerSI microvia 모델 정의의 소유자 GUI 확인 필요.
- **EXP-33**(`results/exp33/`): C는 두 설계 모두 정확(r_C 1.000), 패키지 10 MHz L도 4 % 이내(r_L 0.96)인데 f_res는 두 설계 모두 모델이 약 10 % 높음 → min|Z| f_res 지표의 조건 불량 재확인(G4 f_res 항 판별력 없음). PCB L 분해는 정의 탓에 n = 2(재정의 필요). §6-4(i) 실장 L은 채택·기각 불가.

### EXP-34 — G4 f_res 항 대체 판정 (2026-09-17)
사전 등록 `results/exp34/EXP34_PLAN.md`. f0 존재율 0.80–0.89, 조건성 f0 − f_res = +0.17(260729/p), +0.13(q), **+0.02(PCB)** → 문턱(모든 집합 ≥ 0.1) 미달, **현행 유지(D8)**. 어느 지표로도 G4는 거의 전부 FAIL(f0 기준 7/92, 0/160). 상세 `results/exp34/EXP34_REPORT.md`.

## 5. 현재 결과 요약 (EXP-8 cavity-wall, 이전 기준 26.18% 대비)

이전 기준: Astra hybrid 이산화, 260729 Port18 1 MHz, 26.18%(`S/HQ/docs/evaluation-research/astra_hybrid_r_gc_1mhz_comparison_2026-09-09.json:8-20`, `review_deep.md` §2).

| 케이스 | G1 \|ΔRe\|≤0.05mΩ | ΔL fit(pH, GND-only→cavity) | G3(1MHz 오차, GND-only→cavity) | G5(100MHz) |
|---|---|---|---|---|
| 729 P18 | 0.008 PASS | +10.9→**+2.1** | 9.1%→**2.7%** PASS | 30%→7% |
| **804 P18(held-out)** | 0.042 PASS | +15.9→**+1.9** | 10.0%→**4.9%** PASS | 31%→8% |
| 729 P16 | 0.290 FAIL | +8.0→+1.9 | 13.9%→14.0% FAIL(R결손) | 10%→20% |
| 729 P1 | 0.013 PASS | +88.5→+36.4(부분) | 7.8%→3.4% PASS | 38%→11% |
| 729 P14 | 0.484 FAIL | +45.4→+12.5(부분) | 7.0%→6.8% | 20%→7% |
| 729 P7 | 0.024 PASS | +1.7→0.0 | 8.6%→6.3% PASS | 7%→7% |
| 729 P19 | 5.99 FAIL | +42.2→−3.3 | 29.8%→29.9% FAIL(R결손) | 31%→35% |

7케이스 중 5개(18, 804-18, 16, 7, 19)에서 ΔL이 ±3.3 pH로 붕괴, G3 PASS는 4/7(18, 804-18, 1, 7). G4(f_res)는 **전 케이스 FAIL**(§6). 26.18%(이전 Astra 기준) → 2.7~4.9%(현재, 통과 케이스)로 개선됐으나, port16/19는 R 결손(decap별 실장 저항)이 남아 FAIL.

## 6. 미해결 항목

1. **소수 decap 레일의 R 결손(port19)**: EXP-9 a~d(decap 모델/경로 누락/균일 24 mΩ/포트 정의) 전부 기각. 물리 GND 귀환(S3)도 +0.72 mΩ만 설명, 남은 ≈5.3 mΩ(모델 가중 경로 R 6.9 mΩ의 약 0.8배, 즉 참조 R이 모델의 약 1.8배)은 미확정. 가장 의심되는 곳은 µvia 스택·jog trace의 PowerSI R 처리(via R만으로는 약 2.6배 필요). 판별에는 port19 PowerDC/PowerSI DC 요소별 전류·전압 또는 decap 1개만 둔 bare rail DC R이 필요.
2. **f_res(min\|Z\|) 지표의 조건 불량**: Q≈0.2–0.6인 과감쇠 공진이라 바닥이 평평해 min\|Z\| 주파수가 민감하지 않음(`EXP9_REPORT.md` §1). Im Z 영교차(f0)를 2차 지표로 병행 도입했으나 f0도 케이스에 따라 cavity 모드가 악화시킴(P16 +19%, P14 +13%) — 저주파 ΔL 개선과 공진역 정확도는 **독립적으로 관리해야 함**.
3. **10–100 MHz**: G5는 보고만 하는 상태이고 cavity 모드에서 7~35%로 편차가 큼. 표피효과·근접효과·다중공진 처리 미착수.
4. **물리 모드 vs PowerSI 관례 모드의 노출 방식**: EXP-8 §4 제안 — 기본값 "PowerSI-compatible(cavity-wall)"과 옵션 "Physical return(GND-only)"을 명시적 해석 모드로 노출하고, 결과 화면에 두 모드 차이(Σ층L·(1−d_any/d_gnd))를 함께 표시. 제품 구현은 아직 없음(설계만).
5. **사용자에게 요청할 PowerSI 확인 실행**(EXP-7 §4-iii): 260729를 `.PowerSI MaxEdgeLength=1mm, 0.5mm`(가능하면 0.25 mm 또는 via 국소세분 포함)로 재해석하고 port18 Z(f)를 Touchstone export. 판별: PowerSI L/R이 세분에 따라 +10~13 pH/+0.03~0.1 mΩ 증가하고 f_res가 1.585→약1.3 MHz로 내려가면(H1) 참조 mesh 미수렴, 변화가 <1–2 pH·<0.01 mΩ·f_res<2%면(H2) 우리 평면쌍 L 공식이 실제로 약 30% 과대. EXP-7(ii)에서 우리 커널로는 이 서명(R·L 동시 0 교차)이 재현되지 않아 자체 판별 불가 — PowerSI 재해석이 유일한 판별 수단.
6. exp9에는 README.md가 아직 없다(`tools/research-claude/exp9/`).
7. **≤1 kHz 참조의 비물리 항**(EXP-10): 상수 서셉턴스 B≠0·G<0·Z(0) 불일치. EXP-10b(복소계수 fit) 결과 1–100 kHz는 INDETERMINATE — 6–8극 복소 유리함수로 ~1e-7까지 기술되나 물리도 저차라 판별 불가. s5m6585는 B 대신 G<0 형태. 원인 확정에는 PowerSI 저주파 Log/Linear 이산 스윕 **전 포트** 재해석(DCFitted는 `_S` 파일에 적용된 적이 없음, 소유자 확인)(`E:\Work\20260724 S4LB002_DC MLO PI\S4LB002-2Para_260729_1_injected.spd`)이 필요.
8. **평면 C 단위 오류**(EXP-11에서 발견): 채택 경로의 셀 C가 1e6배 작다(`exp1b.py`). EXP-13: 수정(j)은 7케이스 게이트를 바꾸지 않고(P19 100 MHz는 개선) 새 기본값 후보. **2026-09-16 소유자 승인**: 코드 기본값·EXP-8 영수증은 유지하고 EXP-14 이후 기준선을 exp13/j로 정의(`run11.py`의 후속 변형은 모두 `c_unit_fix=True` 포함, `table11.py --baseline exp13:j`).
9. **§6-4 되돌리기 종결**(EXP-11~14): (a)~(h) 전부 기본값 유지, 채택은 (j) 단위 수정뿐. (i) 실장 루프 L은 소유자가 대표 decap 실장 기하(바디 높이, 단자 간격, 패드–평면 거리)를 주면 사전 등록 후 실행. (h)의 G5 개선은 10–100 MHz 과제의 단서.
10. **92포트 일반화 실패**(EXP-15): G3 PASS 33 %. 우선 원인 후보는 (a) 포트·핀필드 국소 경로 R 관례(EXP-16), (b) 10–100 MHz 귀환면 손실 부재(EXP-17 H3). 고주파 전용 벽 표피 R은 EXP-18b에서 시험했고 92포트 채택 문턱 미달(대형 평면 레일만 개선). EXP-19로 저주파 결손과 고주파 부족이 같은 기원임을 확인(ρ 0.75), ΔL 재정의로도 고주파 L 부족은 없음(참조 L이 f에 따라 감소). EXP-20 via R 표피효과(mk)까지 더해도 92포트 고주파 R 부족 중앙값 0.254 → 0.203(문턱 0.15 미달, 기록만). 진행 중: EXP-21 GND-only 모드 92포트(H_b′ 판별). EXP-22로 "모델 L의 주파수 감소" 후보는 폐기(참조 L은 평탄, 모델 공진 구조가 문제). EXP-23: 반공진 위치·높이는 중앙값에서 맞고 산포는 SITE1에 집중 → EXP-24 사이트 비대칭 진단. 급전 경로 R 관례는 소유자 PowerDC 자료 필요.
11. **균질화 경계조건 결함**(EXP-27): 셀 중심선에 금속이 없는 가장자리 셀의 엣지 G = 0 → decap 고립(44포트)·평면 가장자리 연결 손실. 수정 `homog_face_fix`(EXP-28): 고립 0, 파국 오차 제거, 단 게이트 PASS 감소(가려졌던 균일 R 결손 ×1.46 노출). **소유자 결정 대기(권고: exp28/p를 새 기준선으로)**. 다음은 균일 결손의 원인(PowerSI decap 직렬 항·도전율·PowerDC 실측) 확인.

## 7. 코드·산출물 위치 지도

`/home/claude/work/exp*`, `/home/claude/work/trackA`는 컨테이너 임시 경로(세션 종료 시 소실). 저장소에 복사된 경로는 `docs/research-claude/2026-09-15/results/`이다.

| 실험 | 코드(저장소, 영구) | 원본 산출물(임시) | 저장소 사본 |
|---|---|---|---|
| Track A | — (분석 스크립트만 임시) | `/home/claude/work/trackA/` | `docs/research-claude/2026-09-15/results/trackA/`(보고서, png 6장, csv 3, txt 5) |
| EXP-1/1b | `tools/research-claude/exp1/{extract.py,model.py,run_exp1.py,conv_check.py,exp1b.py}` | `/home/claude/work/exp1/` | `results/exp1/`(보고서 2, png 4, json 3; `.pkl` 2개는 33MB/150MB로 제외) |
| EXP-2 | `tools/research-claude/exp2/{extract_gnd.py,exp2.py,probe.py}` | `/home/claude/work/exp2/` | `results/exp2/`(보고서, png 2, json 4; `.pkl`(34MB) 제외) |
| EXP-3 | `tools/research-claude/exp3/{homog.py,model3.py,extract_gnd3.py,run3.py,summarize.py}` | `/home/claude/work/exp3/` | `results/exp3/`(보고서, png 2, json 7; `.pkl`(46MB) 제외) |
| EXP-4 | `tools/research-claude/exp4/run4.py` | `/home/claude/work/exp4/` | `results/exp4/`(보고서, json 5) |
| EXP-5 | `tools/research-claude/exp5/{survey.py,pipeline.py,analyze.py,replot.py,runall.sh}` | `/home/claude/work/exp5/` | `results/exp5/`(보고서, png 8, json 9; `.pkl`×7(26~82MB), `shapes_260804.pkl`(82MB) 제외) |
| EXP-6 | `tools/research-claude/exp6/{run6.py,runall6.sh,runall6b.sh}` | `/home/claude/work/exp6/` | `results/exp6/`(보고서, json 13) |
| EXP-7 | `tools/research-claude/exp7/{kernel.py,coarse.py,plot_h.py,runB.sh,runP.sh}` | `/home/claude/work/exp7/` | `results/exp7/`(보고서, png 1, json 10) |
| EXP-8 | `tools/research-claude/exp8/{run8.py,analyze8.py,runall8.sh}` | `/home/claude/work/exp8/` | `results/exp8/`(보고서, png 7, json 8) |
| EXP-9 | `tools/research-claude/exp9/{run9.py,analyze9.py,audit9.py,table9.py}` (README 없음) | `/home/claude/work/exp9/` | `results/exp9/`(보고서, json 13; `.npz`×7(7KB~86MB, 2개 14MB/86MB 제외)) |

제외 사유는 모두 "1MB 초과 중간 산출물(pkl/npz — 절점 좌표·형상 캐시)"이며 재현 명령(§8)으로 재생성 가능하다.

## 8. 재현 방법

각 실험 README(`tools/research-claude/exp{1..8}/README.md`)에 정확한 실행 커맨드가 있다. 공통 절차:

```bash
# 1) venv (실험이 실행된 컨테이너 기준, python>=3.12)
python3 -m venv ~/venv && . ~/venv/bin/activate
pip install numpy scipy shapely pydantic  # 평가기 의존성 일부만 필요(solver/spd 모듈)

# 2) SPD 원본 파일 배치 (읽기 전용, 수정 금지)
#    사용자 PC: D:\Downloads\examples\{S4LB002-2Para_260729_1_injected.spd, …_260804_…, s5m6585_…}
#    컨테이너 마운트 기준 예: ~/mnt/examples/*.spd

# 3) 저장소 루트에서 실행 (src/ 를 PYTHONPATH에 추가하거나 -e 설치)
cd /home/claude/spd-decap-pi-evaluator
pip install -e .
cd tools/research-claude/exp1 && python extract.py && python run_exp1.py --variant B --h 200 --fine-h 50 --top-h 50
cd ../exp3 && python extract_gnd3.py && python run3.py --step S2 --sweep
cd ../exp4 && python run4.py --which ab
cd ../exp5 && bash runall.sh          # held-out 260804 + 6레일
cd ../exp8 && python run8.py --predict && bash runall8.sh   # 최종 채택 모델(cavity-wall)
```

각 스크립트는 입력 SPD 경로를 인자로 받는다(정확한 플래그는 각 README 참조). 출력은 `/home/claude/work/exp{N}/`(임시)에 쓰인다 — 영구 보존이 필요하면 §7의 저장소 사본 경로로 복사해야 한다. 원본 SPD/Touchstone 파일은 이번 세션에서 한 번도 수정하지 않았다.
