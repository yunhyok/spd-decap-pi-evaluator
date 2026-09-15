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
