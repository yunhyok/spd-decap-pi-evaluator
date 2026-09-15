# Codex 인계 작업 회고 — 오판, 수정, 개선 결과, 후속 과제

작성일 2026-09-15. 대상 독자는 프로젝트 소유자다. 브랜치는 `claude/lightweight-hybrid-20260915`다.

**약칭과 출처.** `S/`는 `docs/handoff/2026-09-15/snapshots/`, `HQ/`는 `S/HQ/outputs/research/`, `R/`는 `docs/research-claude/2026-09-15/`다. `review_deep.md`, `review_deep_exp1.md`, `review_fresh_exp1.md`, `proposal_fresh.md`, `sigrity_voids.md`는 이 세션의 읽기 전용 검토 산출물이며 `R/reviews/`에 사본을 두었다. 그 요지는 `R/RESEARCH_LOG.md` §1과 `R/DECISIONS.md`에도 있다.

"오판"은 결과를 알고 난 뒤의 판단이다. 당시 Codex가 가진 정보로 내릴 수 있었던 결정인지를 함께 적었다.

## 1. 요약

- **정확도(1 MHz 복소 오차).** 260729 Port18에서 26.18%가 2.7%로 줄었다(`handoff.md:96`, `R/results/exp8/EXP8_REPORT.md:38`). 같은 설계 계열인 260804 Port18에서는 4.9%다(`EXP8_REPORT.md:39`).
- **정확도(100 MHz).** 260729 Port18은 30%에서 7%로 줄었다. 30%는 Claude의 GND-only 모델 값이다. Codex 계열에는 100 MHz 수렴값이 없고, native/two-sheet 기록은 91.4%/86.8%다(`review_deep.md:40`).
- **계산 비용.** Codex 모델은 3.78M 미지수, 1점에 2214 s, 최대 27.55 GiB를 쓰고도 미수렴이었다(`HQ/ASTRA_PROGRESS_20260914.md:27`). 현재 모델은 26만 미지수 sparse LU로 주파수당 인수분해 약 2.8 s, 최대 RSS 1.32 GB이며, 27개 주파수 전 대역을 241 s에 푼다(`R/results/exp8/result_260729_Port18_SITE0_any.json`의 `stats`, `wall_seconds`).
- **남은 문제.** 7개 케이스 중 G3(1 MHz <10%) 통과는 4개다. few-decap 레일의 R 결손, 1–10 MHz 공진역(G4 전 케이스 FAIL), 제품 미통합이 남아 있다.

## 2. Codex가 무엇을 하고 있었나

**목표.** 인계 문서가 밝힌 목표는 SPD와 PowerSI Touchstone으로 1 kHz–100 MHz 복소 임피던스를 정확히 예측하는 모델을 확립하는 것이다. 제품 목적은 "빠른 pre-design PI 판단"이다(`handoff.md:9`). 인계 시점의 인정 기준은 260729 Port18 1 MHz 한 점의 복소 오차 26.1826839757%였다(`handoff.md:11`, `:89-97`).

**모델 규모.** Astra 조건부 하이브리드는 `A(v,i)=[Yv+Bi, Bᵀv−Ri]` 구조다. 전위 3,178,104개와 명시 전류 604,031개로 합계 3,782,134 미지수이며, FMM3D 자기 작용과 GMRES, 정적 전처리기 M을 쓴다(`handoff.md:107-108`). 자기 항은 L14/L25 등 일부 sheet에만 붙어 있고, 나머지는 layerwise 등전위 기반 위에 있다(`review_deep.md:13`).

**마지막 실행.** 결과는 아래 표와 같다(`handoff.md:130-133`, `HQ/HQ_STOPPED_CHECKPOINT_20260914_USAGE40.md:26-30`).

| 실행 | 결과 |
|---|---|
| finite joint-p | 22 actions, 2214 s, info=1, KCL 49.98 mA. left/right 전처리 불일치를 사후에 발견 |
| right probe | 900.8 s timeout, KCL 18.13 mA |
| static nodal leakage | 38.6 s, 노드 보정 18.13 → 18.16 mA로 개선 없음 |
| static tighter inverse | 123 s, info=3, 4.30 mA. 4.96 pA 부호 검사 FAIL 보존 |

네 실행 모두 새 보드 Z를 내지 않았다. 따라서 26.18%는 한 번도 갱신되지 않았다(`HQ/ASTRA_PROGRESS_20260914.md:5`).

**중지와 분산.** 연구는 "잔여 사용량 40% 이하 관측 시 STOP latch, 명시적 재개 전 금지" 정책으로 멈췄다(`HQ/HQ_USAGE_STOP_POLICY_20260914.json:6-7,16`). 작업은 MAIN·HQ·IMPL·PHYS·RED·REVIEW 6개 위치에 흩어져 있었다(`handoff.md:29-36`). HQ에만 미커밋·untracked 경로가 795개였다(`handoff.md:40`).

**인계된 다음 단계.** 최우선 과제는 "정적 보조해법이 놓치는 노드 모드를 특정"하는 것이었다. 즉 M 전처리기 또는 coarse correction 개선이다(`handoff.md:141-145`, `HQ_STOPPED_CHECKPOINT:49`). 목표 장비로는 512 GB RAM급 워크스테이션을 들었고, "자원 효율 때문에 물리를 먼저 단순화하지 않는다"고 적었다(`handoff.md:153`).

## 3. 오판으로 판단되는 점

### (a) 출발 모델 선택

기록상 이력은 세 단계였다. 처음에는 modal cavity를 썼다. 260729 loaded 6레일에서 RMS median 2.60 dB였다(`docs/EVALUATION_DISTRIBUTION_VALIDATION_2026-08-06.md:113`). 다음에는 면을 등전위로 두는 layerwise로 바꿨다. loaded macro 15.91 dB로 크게 나빠졌고(`docs/WORK_EXECUTION_BASELINE.md:464-471`), 오차는 주파수가 오를수록 음수로 커졌다(1 MHz −3~−6 dB, 10 MHz −23~−28 dB). 마지막으로 그 위에 Astra로 sheet R/L을 하나씩 되붙였다(`review_deep.md:45`).

layerwise에는 면 내부 R/L이 없다(`review_deep.md:11`). 이 결손은 1–100 MHz PDN 인덕턴스의 주성분인 plane-pair spreading L을 구조적으로 뺀 것이다. 저장소에는 이 항을 가진 MFDM 커널이 이미 있었다(`src/spd_decap_pi/_core/solver/mfdm.py:1-18`). 그러나 MFDM을 실제 보드에서 PowerSI와 비교한 기록은 없고, 버린 이유를 적은 기록도 없다(`review_deep.md:45`).

공정하게 보면 modal 2.6 dB는 dB RMS 지표다. 6레일 모두 modal 미수렴이었고(`EVALUATION_DISTRIBUTION_VALIDATION_2026-08-06.md:113`, Modal-converged 0/6), 직사각 가정도 있어 일반해로 채택하기 어려웠다. 그렇더라도 "등전위 면 + 일부 sheet 복원"보다 "평면쌍 전체 + 회로"를 먼저 비교하는 편이 합리적이었다. 2026-09-10 외부 검토(Fable)가 MQS/plane-pair 전환을 제안했지만, 계획에서는 "후보이지 입증된 해법이 아니다"로만 남았다(`handoff.md:194`).

### (b) 문제 규모 확대

PowerSI는 공개 자료상 plane/EM + circuit + transmission-line "hybrid solver"다. via·pad는 closed-form 회로 모델로 처리한다(`proposal_fresh.md:18-19`, Cadence 포럼 57376; Farrahi 외, DesignCon 2019). 목표 대역의 참조 도구가 2-D 평면 + 회로 등급인데, Codex는 3.78M 미지수 3-D 적분방정식과 FMM으로 갔다.

이 방향은 "빠른 pre-design" 목적(`handoff.md:9`)과 노트북 실행 환경 모두와 맞지 않았다. 게다가 P1·RT0·hybrid로 이산화를 바꿔도 1 MHz 오차는 26.2–29.4%에 머물렀다(`review_deep.md:39,49`). 이는 수치 문제가 아니라 모델 형태의 문제라는 신호였는데, 규모를 더 키우는 쪽으로 해석됐다.

### (c) 검증 지표

Astra 단계의 판정은 Port18 1 MHz 한 점 복소 오차에 집중됐다(`handoff.md:87-99`). 비교에는 참조 7점만 쓰였다(`review_deep.md:98`). 한 점 복소값으로는 R·L·C 오차를 분리할 수 없다. Touchstone에는 826개 주파수가 있고(`review_deep.md:87`), 정책 문서도 241점 곡선 기준을 이미 정의하고 있었다(`docs/EVALUATION_ACCURACY.md:172-173`).

Codex의 저장 JSON만으로도 원인을 가를 수 있었다. two-sheet 모델은 1 kHz 허수부가 참조와 5 ppm 이내로 일치했으므로 decap 용량은 맞았다. 1–100 kHz ΔRe는 −0.15 mΩ로 주파수에 무관했으므로 직렬 R 누락이었다. 100 kHz–1 MHz의 −ΔIm/ω는 25–29 pH였으므로 L 누락이었다(`review_deep.md:62-76`). 이 두 값을 직렬로 더하기만 해도 1 MHz 오차는 29.4%에서 4.6%로 떨어진다(`review_deep.md:76`). 다시 말해 26%의 원인이 면 내부 R/L 결손이라는 증거가 기록 안에 있었는데, 곡선 분해를 하지 않아 보지 못했다.

### (d) 수치 수렴을 물리 오차보다 앞세움

마지막 1주의 계산은 KCL mA 오차, 전처리 방향, pA 단위 부호 검사에 쓰였다(`handoff.md:113-121,130-133`). Codex도 5–6 pA 차이를 mA 실패의 원인으로 확대하지 말라고 적었다(`handoff.md:121`). 문제는 인계된 최우선 과제가 여전히 전처리기 개선이었다는 점이다(`handoff.md:141-145`). 이 작업이 성공해도 같은 물리의 26%가 수렴값으로 확정될 뿐이다(`review_deep.md:51`).

### (e) 프로세스

main 이력(`git log d5e7df4`)의 338개 커밋 중 140개가 `docs:` 커밋이다. 병합 직전 40개 중 37개가 `docs:` 커밋(`locate`, `trace`, `bound`, `isolate` 등)이고, 2026-08-26 하루에만 104개 커밋이 있다. 참고로 `review_deep.md:57`의 "339개 대부분이 docs: locate"는 과장이다. 정확한 비율은 41%이며, 이 커밋들은 8월 loader 작업 시기의 것이다.

더 큰 문제는 9월 Astra 연구가 전혀 커밋되지 않았다는 점이다. 연구는 6개 작업 위치, 미커밋 파일, ignored `outputs`, 별도 FMM 런타임에 흩어져 있었다(`handoff.md:46,64`). 인계 문서 스스로 "`git clone` 또는 `git checkout`만으로 현재 상태가 복원되지 않는다"고 명시했다(`handoff.md:15`). 사용량 기반 STOP latch(`HQ_USAGE_STOP_POLICY_20260914.json:7`)와 proof/manifest 계층(`review_deep.md:55`)도 연구 주기를 늦췄다. 그 결과 Claude는 Codex 계열 모델을 재실행하지 못했다.

### Codex 책임이 아닌 것

- **내부 L 결함.** `copper_surface_impedance`(`mfdm.py:782`)는 docstring이 밝힌 대로 one-face 식이다(`:788`). 함수 자체는 결함이 아니다. 이를 양측 귀환 셀에 잘못 적용한 곳은 Claude의 EXP-1~3 코드다(`tools/research-claude/exp1/model.py:508`, `exp3/model3.py:452`, `R/results/exp4/EXP4_REPORT.md` A1). 제품 코드는 이 함수를 호출하지 않는다. `R/DECISIONS.md` D4의 표현은 이에 맞게 수정했다.
- **cavity-wall 관례.** PowerSI가 넷과 무관하게 인접 금속을 기준면으로 쓴다는 관례는 공개 문서로 확인되지 않는다(`sigrity_voids.md` §3). 이를 사전에 알 수 있었다고 보기 어렵다.

## 4. Codex가 잘한 점

Claude 모델의 입력 계층은 대부분 Codex 자산이다. **SPD 파서**(`src/spd_decap_pi/_core/io/spd.py`)는 mmap 스트리밍으로 1.1 GB 파일을 18 s, 최대 1.5 GB에 스캔한다. `_parse_layers/_shapes/_padstacks/_partial_circuits`를 수정 없이 재사용했다(`R/results/exp1/EXP1_REPORT.md:7-9`). **decap subckt 파서**(`_core/models/spice.py:185,374`)는 Murata 13단 래더를 정확히 평가한다. 이는 EXP-9에서 port19 R 결손의 원인이 decap 모델이 아님을 입증하는 근거가 됐다(`R/results/exp9/EXP9_REPORT.md` §2a). **Touchstone 전체 행렬 S→Z 변환**(`_core/io/touchstone.py:273,301`)과 **MFDM 커널**(`mfdm.py` 양면 표면 임피던스 `:801`)도 그대로 썼다.

데이터 위생은 모범적이다. 입력·산출물의 SHA-256을 기록했고(`handoff.md:75-78,177-182`), 260804를 fitting에 쓰지 않는 holdout 원칙을 세웠다(`docs/EVALUATION_ACCURACY.md:226-231`). 실패 receipt와 FAIL 판정을 고치지 않고 보존했으며(`handoff.md:137`), 미수렴 결과를 기준으로 쓰지 않았다(`handoff.md:99`). left/right 전처리 불일치를 스스로 기록한 것(`ASTRA_PROGRESS_20260914.md:29,33`)과 "검토 통과를 정확도 개선으로 부르지 않는다"는 보고 원칙(`handoff.md:206`)도 같은 맥락이다. 인계 문서의 경계 서술 역시 정확하다. 무엇이 복원되지 않는지, 어떤 값이 인정 기준이 아닌지 명확히 적었다.

EVALUATION_ACCURACY의 수치 원칙도 타당하다. 복소 µΩ, 위상 RMS/max, 공진 위치 ±10%, 레일·설계를 섞지 않는 집계를 규정한다(`docs/EVALUATION_ACCURACY.md:165-197`). Claude의 G1–G5 게이트도 같은 사고방식을 따랐다.

## 5. Claude가 수정한 것

**폐기/유지 판정.** 폐기한 것은 Astra 전장 경로, FMM 런타임, 정적 전처리기 연구, layerwise 물리, proof/certificate 상위 계층이다. 유지한 것은 SPD 파서, decap 모델, Touchstone 변환, MFDM 커널이며, modal은 교차검증용으로 남겼다(`R/RESEARCH_LOG.md:9-21`).

**모델과 게이트.** 2-D plane-pair 하이브리드를 채택했다(`R/DECISIONS.md` D1–D2). 모델 정의는 `R/RESEARCH_LOG.md` §3에 있다. 검증은 한 점이 아니라 곡선 게이트 G1–G5로 고정했다. 게이트는 G1 1–100 kHz |ΔRe|≤0.05 mΩ, G2 0.1–1 MHz ΔL ±5 pH, G3 1 MHz <10%, G4 1–10 MHz <20%·f_res ±10%, G5 10–100 MHz(보고만)다. 튜닝 없이 단계를 비교했다(`R/DECISIONS.md` D3).

**실험 사다리.** 기각된 가설도 포함한 요약은 아래와 같다(`R/RESEARCH_LOG.md` §4).

| 실험 | 가설 | 결과(260729 P18) | 판정 |
|---|---|---|---|
| EXP-1/1b | 평면쌍+회로, 이상 GND로 충분 | G1 PASS 0.028 mΩ, G3 16.3% | 방향 채택, L 과다 잔존 |
| EXP-2 | 다도체 명시 GND가 R/L 분배 결정 | G3 73–94%, ΔL이 기준높이 z0에 따라 30.1↔33.8 pH | **기각**(partial-L gauge ill-posed) |
| EXP-3 | 균질화·fringing·GND R | S1 균질화 효과 없음, S2 fringing 12.7%, S3 GND R 20.5%로 악화 | 균질화 **기각**, fringing 채택 |
| EXP-4 | 내부 L을 한쪽 식으로 과대 계상 | 양측 대칭식으로 12.7→9.1% | 채택(Claude 자체 결함 수정) |
| EXP-5 | held-out + 7레일 | 260804 10.0%. ΔL은 평면 L에 비례(r=0.994) | 전역 비례 L 초과 확인 |
| EXP-6 | PowerSI의 void 단순화 | 모두 채워도 ΔL/L 0.25→0.23, R 부호 반대 | **기각** |
| EXP-7 | 커널 오류 / 거친 mesh 모사 | 커널은 해석해와 1–8% 일치, 거친 mesh는 비단조·악화 | 커널 정상, 거친 mesh **기각** |
| EXP-8 | 넷 무관 인접 금속 = 벽(EXP-1c 재해석) | 예측-실측 상관 0.965, 5/7에서 ΔL ±3.3 pH, P18 2.7% | 도구 관례로 채택 |
| EXP-9 | port19 R 결손(a–d) | 네 가설 모두 기각, GND R은 +0.72 mΩ만 설명 | 미해결 |

EXP-1c("떠 있는 타넷 전원면이 귀환면")는 물리 가설로는 기각을 유지한다. 비회전 전류, 폐로 임피던스가 이득보다 10⁶배 큰 점, 필요한 보정의 주파수 방향이 반대인 점이 근거다(`review_fresh_exp1.md:27-44`). EXP-8의 성공은 PowerSI 관례의 추정일 뿐, 물리를 입증한 것이 아니다(`EXP8_REPORT.md:10-17,73-78`).

Claude 쪽 시행착오도 기록해 둔다. EXP-1 사전 지정 primary(A)는 전부 FAIL이었다. EXP-2는 OOM으로 두 번 실패했다(`R/results/exp2/EXP2_REPORT.md:26,33`). EXP-9는 예산을 약 1 h 초과했다(`EXP9_REPORT.md:144`).

## 6. 개선 결과

모든 수치는 `EXP8_REPORT.md:36-44`의 cavity-wall 모드 값이며, 화살표 왼쪽은 GND-only다. Codex 계열의 복소 기준값은 P18의 26.18% 하나뿐이다.

| 케이스 | G1 max\|ΔRe\| (mΩ) | ΔL fit (pH) | G3 1 MHz | 100 MHz |
|---|---|---|---|---|
| 729 P18 (Codex 26.18%) | 0.008 PASS | +10.9 → +2.1 | 9.1 → **2.7%** | 30 → 7% |
| 804 P18 (held-out) | 0.042 PASS | +15.9 → +1.9 | 10.0 → **4.9%** | 31 → 8% |
| 729 P16 | 0.290 FAIL | +8.0 → +1.9 | 13.9 → 14.0% | 10 → 20% |
| 729 P1 | 0.013 PASS | +88.5 → +36.4 | 7.8 → **3.4%** | 38 → 11% |
| 729 P14 | 0.484 FAIL | +45.4 → +12.5 | 7.0 → 6.8% | 20 → 7% |
| 729 P7 | 0.024 PASS | +1.7 → 0.0 | 8.6 → **6.3%** | 7 → 7% |
| 729 P19 | 5.99 FAIL | +42.2 → −3.3 | 29.8 → 29.9% | 31 → 35% |

"held-out" 표기에는 한계가 있다. 260804는 EXP-5에서 동결 모델로 평가했지만, EXP-8의 규칙 채택은 260804를 포함한 7케이스 결과를 본 뒤에 이뤄졌다. 또 260729의 재작업본이라 독립 설계가 아니다(`R/RESEARCH_LOG.md:21,29`). 따라서 4.9%는 약한 held-out 증거다.

| 계산 비용 | 미지수 | 시간 | 메모리 | 상태 |
|---|---|---|---|---|
| Codex native layerwise, 1 MHz 1점 | — | 1095 s | 13.1 GB | 26% 계열 이전(`review_deep.md:43`) |
| Codex Astra joint-p, 1 MHz 1점 | 3.78M | 2214 s | 27.55 GiB | info=1 미수렴 |
| Codex static tighter inverse | 3.78M | 123 s | 24.4 GB | info=3, Z 없음(`handoff.md:133-135`) |
| Claude P18, 27주파수 | 261,124 | 241 s(인수분해 2.8 s/점) | 1.32 GB | 직접해 |
| Claude P7(최대), 27주파수 | 554,208 | 601 s(4.5 s/점) | 2.09 GB | 직접해 |

Claude 행의 출처는 `R/results/exp8/result_*_any.json`의 `stats`와 `wall_seconds`다. SPD 추출(18 s, 1.5 GB)은 별도다.

## 7. 남은 문제와 한계

**R 결손.** few-decap 레일의 R이 설명되지 않는다. port19의 ΔRe는 −6.0 mΩ로 주파수에 무관하다. decap 모델, 경로 누락, decap당 24 mΩ, 포트 정의를 모두 기각했고, 명시 GND R은 +0.72 mΩ만 설명한다. 남은 약 5.3 mΩ는 미확인이다(`EXP9_REPORT.md:128-143`). port16(−0.29 mΩ)과 port14(−0.48 mΩ)도 같은 계열로 보이지만 분석하지 못했다.

**공진역.** 9–15 MHz 반공진 부근의 P18 오차는 12.7–27.9%다(9.12/10/12 MHz, `result_260729_Port18_SITE0_any.json`). G4는 전 케이스 FAIL이다(`EXP8_REPORT.md:48`). f_res(min|Z|)는 Q≈0.2–0.6에서 바닥이 평평해 조건이 나쁜 지표다. Im Z 영교차 f0를 병행했지만, cavity 모드는 P16(+19%)·P14(+13%)의 f0를 오히려 악화시킨다(`EXP9_REPORT.md:40-44`).

**100 MHz.** 복소 오차는 7%지만 Re Z가 1.02 mΩ로 참조 1.78 mΩ보다 43% 낮다(같은 JSON). 표피·근접 효과와 GND 손실이 빠진 탓으로 보인다. 그런데 명시 GND R을 넣으면 저주파 게이트가 항상 악화된다(`EXP3_REPORT`, `EXP4_REPORT` (c)). 이 모순은 풀리지 않았다.

**참조 자체의 신뢰도.** SPD에 `DC_BBS_Setting DCFitted=1 BBSFitted=1`과 adaptive sweep이 설정돼 있다. PowerSI의 kHz 대역이 해석값이 아니라 fitting 값일 수 있다(`sigrity_voids.md` §4, `review_deep_exp1.md:94-96`). 그렇다면 G1 0.008 mΩ 일치도 fit 곡선과의 일치일 수 있다. 1 kHz 이하 Re Z<0 샘플도 있다(`R/results/trackA/TRACKA_REPORT.md` §6).

**PowerSI 재현 ≠ 물리 정확도.** cavity-wall 모드는 PowerSI를 재현하는 규칙이다. 물리적으로는 떠 있는 타넷 평면이 투명해야 한다. 또 ΔL 초과가 PowerSI mesh 미수렴 때문인지(H1), 우리 L 공식이 약 30% 과대하기 때문인지(H2)는 아직 판별되지 않았다(`R/RESEARCH_LOG.md:162`).

**연구 코드 상태.** 모델은 `tools/research-claude/exp*`의 실험 스크립트이고, 결과는 `/home/claude/work`(임시)와 저장소 사본으로 나뉘어 있다(`R/RESEARCH_LOG.md` §7). 제품 `src/spd_decap_pi/evaluation.py`는 여전히 layerwise 계열을 import한다(`evaluation.py:62`). 검증 범위도 1개 설계 계열, 92포트 중 7포트다. Codex 인계와 같은 종류의 재현성 부채를 Claude도 지고 있다.

## 8. 후속 과제 제안

우선순위는 "정확도 주장의 기반을 먼저 확정하고, 그다음 일반화와 제품화"다. 비용은 추정이다.

| 순위 | 과제 | 판별 기준 | 예상 비용 |
|---|---|---|---|
| 1 | PowerSI를 `MaxEdgeLength` 1 mm·0.5 mm로 재해석하고 P18 Touchstone export | H1: 세분화 시 PowerSI L +10~13 pH, R +0.03~0.1 mΩ, f_res 1.585→약 1.3 MHz이면 참조 mesh 미수렴. H2: 변화 <1–2 pH, <0.01 mΩ, f_res <2%이면 우리 평면 L 공식 과대(`RESEARCH_LOG.md:162`) | 사용자 PowerSI 실행 2회, 분석 1 h 이내 |
| 2 | port19 R 결손 | PowerDC 요소별 전류·전압에서 µvia 스택/jog R이 모델의 약 1.8–2.6배이면 via R 관례 차이. 또는 decap 1개 bare-rail DC R 비교(`EXP9_REPORT.md:140-142`). P14/P16에도 같은 분해 적용 | PowerDC 1회 + 2–3 h |
| 3 | 일반화 검증: 260729 92포트 전체, s5m6585(160포트) | 사전 고정 모델로 G1/G3 통과율과 오차 분포를 보고. s5m6585는 small-hole void 약 16만 개 처리와 2층 구조에서 파서·wall 규칙 적용 여부 확인(`RESEARCH_LOG.md:33`) | 레일당 40–640 s(`EXP5_REPORT.md:78`) → 수 시간 계산 + 1–2일 |
| 4 | 제품 통합 | layerwise를 대체하고 "PowerSI-compatible(cavity-wall)"과 "Physical return(GND-only)"을 명시 모드로 노출해 차이를 표시(`EXP8_REPORT.md:71-78`). 연구 스크립트를 테스트 가능한 모듈로 이전 | 수 일 |
| 5 | 10–100 MHz | 반공진 9–15 MHz 오차 <15%, 100 MHz Re Z ±20%. 전 층 GND R을 명시하되 저주파 G1을 유지하는 정식화 필요(현재는 넣으면 악화). 표피·근접 효과와 반공진 감쇠 포함 | 2–4일, 결과 불확실 |
| 6 | 속도 | 보드 인수분해를 한 번만 하고, decap을 사이트 포트의 대각 부하로 Schur 보정해 what-if를 초 단위로(`proposal_fresh.md:123`). 핀필드 중심 적응 격자로 미지수 절감. 판별: 전체 재해석과 ΔZ <0.5% | 2–3일 |
| 7 | 1–100 kHz 참조 신뢰성 | 0.001–100 kHz에 2–4극 유리함수를 맞춰 잔차가 약 1e-6이면 fit 데이터로 판정하고 G1 해석을 조정(`sigrity_voids.md` §4) | 1 h 이내 |

과제 1과 7은 비용이 작고, 결과에 따라 과제 5와 G1/G2 게이트 해석이 바뀐다. 그래서 가장 먼저 수행할 것을 권한다. 과제 3 전에는 1 kHz–100 MHz 곡선 게이트와 holdout 규칙을 결과를 보기 전에 동결해야 한다. 이번 EXP-8에서 260804의 held-out 성격이 약해진 일을 반복하지 않기 위해서다.
