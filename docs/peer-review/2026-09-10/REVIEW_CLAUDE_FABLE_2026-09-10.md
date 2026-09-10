# SPD Decap PI Evaluator v0.23.1 — 외부 Peer Review 의견서

[검토 시작](README.md) · [검토 요청서](REVIEW_REQUEST.md) · [검토 대상 보고서](REPORT.md) · [근거 자료](EVIDENCE.md)

```text
검토자: Claude Fable 5.1 (Anthropic) — 사용자 요청에 따른 독립 검토, 개발·실행 이력에 관여하지 않음
검토일: 2026-09-10 KST
검토한 commit SHA: 215d7f5a69e3fec285c31778492f360e08e34851
                  ("docs: publish frozen external PI peer review packet"), Release tag peer-review-20260910
총평: 대안으로 전환
```

총평의 뜻을 먼저 한정한다. **버릴 것은 실행 경로와 연구 순서**이지 물리 모델의 계열이 아니다. (1) 4.46백만 미지수의 조건부 A에 대해 보조행렬 M을 바꿔 가며 1-cycle GCROT를 반복하는 현재 실행 경로, (2) 전원·귀환 경로의 자기 소유가 정의되기 전에 수치 수렴을 먼저 추구하는 순서, 이 둘을 중지해야 한다. 주 관심 대역 1 kHz–100 MHz에서 필요한 물리는 **자기준정적(MQS) 부분 인덕턴스 + 집중/평면 G/C 결합**이며, 이는 현재 A가 속한 계열과 같다. 다만 그 물리를 *모든* 전류 경로에 균일하게 부여하고, 수렴이 보장되는 규모에서 먼저 확인한 뒤 확장해야 한다.

가장 중요한 세 가지 판단은 다음과 같다.

1. **최근 10 MHz의 54.59 %는 모델 A의 정확도에 대한 정보를 담고 있지 않다.** 세 GCROT 실험은 warm start의 포트 값을 0.35 nΩ 이내로 그대로 반환했고, 원래 RHS 상대잔차 710은 x=0의 상대잔차(=1)보다 세 자릿수 크다. 이 숫자는 앞으로 어떤 문서에서도 모델 오차로 인용하면 안 된다(§2.2).
2. **참조값의 지배 특성은 10 MHz 이상에서 약 15–17 pH의 루프 인덕턴스이며, 지금까지의 모든 후보는 1.5–3.5 pH다.** 54.59 %의 gap 0.872 mΩ 중 허수부 결손이 0.719 mΩ다. 문제는 "정확도 부족"이 아니라 "루프 인덕턴스 한 자릿수 차이"다(§2.1).
3. **현재 A의 자기 연산자는 두 sheet(L25, L04)만 포함하고, 전원 전류의 상당 부분이 흐르는 L14·L02·TOP·비아에는 자기 항이 없다.** 소유가 균일하지 않은 모델의 수렴해는 루프 인덕턴스를 재현할 수 없고, 이 상태에서 M 개선은 목표에 기여하지 않는다(§2.4). 이 사실은 보고서 §3.3에 이미 적혀 있으나, "수치 작업 중단"이라는 귀결까지는 이어지지 않았다.

---

## 1. 검토 범위와 방법

**읽은 것.** 이 폴더의 README, REVIEW_REQUEST, REPORT, EVIDENCE, DATA, manifest.json; 세 GCROT 실험의 `result.json`; forward 실험의 `driver-at-run.py`; 보조 풀이 `_one_r_direction`과 자기 작용 `joint_action`의 소스; 주대역 비교 JSON 4종; 감독 기록(ASTRA_STEP5_SUPERVISION)의 관련 구간; 문헌 보고서 서두; 저장소 최상위 `docs/EVALUATION_ACCURACY.md`의 허용 오차 정책 부분.

**실행한 것.** `verify_packet.py`(자산 없이): `PASS: 562 snapshots; 612 local document links`. 저장된 JSON 수치에 대한 독립 산술(부록 A). 그 밖의 계산은 없다.

**하지 않은 것.** Release NPZ 7개(2.27 GB) 다운로드·재해시, solver·FMM·SPD 파싱 재실행, PowerSI 재현. 원본 SPD와 Touchstone은 제공되지 않았으므로 보드 형상(층 두께, 판 크기, 포트·decap 위치)은 감독 기록에 적힌 수치 이상으로 알지 못한다. 형상에 의존하는 판단은 그 한계를 명시했다.

**검토 자세.** 보고서의 [사실]/[해석]/[제안] 구분을 그대로 받아들이고, 보고서가 이미 말한 것을 반복하기보다 반대하거나 보완하는 데 집중했다. 요청서가 지정한 여섯 질문에는 §3에서 요청 형식으로 답했다. 한 사람이 읽고 하루에 쓴 검토이므로, §2.4의 소유 판단처럼 SPD 확인이 필요한 항목은 "확인 필요"로 표시했다.

---

## 2. 자료에서 독립적으로 확인한 사실과 그 해석

### 2.1 참조값의 구조: 이 포트는 10 MHz 이상에서 `R(f) + jωL_eff`, `L_eff ≈ 15–17 pH`

[주대역 비교 JSON](snapshots/hq/docs/evaluation-research/astra_primary_band_comparison_2026-09-07.json)의 `reference_zdd_ohm`을 그대로 읽고, 1 kHz·10 kHz 두 점에서 `C = 262.81 µF`를 결정한 뒤 `L_eff = (Im Z + 1/(ωC))/ω`를 계산했다(부록 A).

| f | 참조 Z (mΩ) | 참조 L_eff | native 후보 | two-sheet 후보 | 최신 L25/L04 complete-current 후보 |
|---|---|---:|---:|---:|---:|
| 1 MHz | 0.692 − j0.426 | (SRF 근방, 단일 L로 해석 불가) | — | 3.3 pH | — |
| 10 MHz | 1.335 + j0.876 | **14.9 pH** | 2.2 pH | 3.3 pH | **3.5 pH** (0.843 + j0.157 mΩ) |
| 100 MHz | 1.780 + j10.51 | **16.7 pH** | 1.5 pH | 2.1 pH | 미완 |
| 1 GHz | 18.04 + j109.96 | 17.5 pH | 1.6 pH | 1.7 pH | 미완 |

**[해석]** 참조의 L_eff가 10 MHz–1 GHz에서 15→17.5 pH로 거의 일정하다는 것은, 이 값이 공진 잔재가 아니라 형상으로 정해지는 실제 루프 인덕턴스(decap ESL의 병렬합 + 실장/비아/평면 spreading)라는 뜻이다. 421개 decap의 ESL 병렬합만으로는 1–3 pH 수준이므로, 참조의 15 pH 중 대부분은 **평면·비아 경로의 인덕턴스**다. 모든 후보가 1.5–3.5 pH에 머무는 것은 후보들이 대체로 decap ESL 병렬합만 재현하고 있다는 신호다. 1 MHz의 [26.18 % 비교](snapshots/hq/docs/evaluation-research/astra_hybrid_r_gc_1mhz_comparison_2026-09-09.json)도 같은 방향이다(Re −0.137 mΩ, Im −0.163 mΩ; 후보가 더 용량성). 실수부 결손(10 MHz에서 −0.493 mΩ)은 두 번째 문제이며, 참조 Re가 1 MHz→10 MHz→100 MHz에서 0.69→1.34→1.78 mΩ으로 증가하는 것은 DC sheet 저항으로는 재현할 수 없는 표피 효과의 징후다(§3 Q4).

이 표는 보고서의 어느 절에도 없다. 보고서는 26.18 %와 54.59 %를 병기하지만, 두 숫자를 물리량으로 분해하면 "무엇이 빠져 있는가"가 바로 보인다는 점에서 보고서의 서술 방식을 수정할 것을 권한다(§7).

### 2.2 세 GCROT 실험은 후보 Z를 warm start에서 사실상 움직이지 않았다

| 상태 | 복소 오차 | Z_raw (mΩ) | 원래 RHS 상대잔차 |
|---|---:|---|---:|
| 공통 THIRD warm start | 54.594380 % | — | 1394.88 |
| real-H 기준 cycle | 54.595639 % | 0.842519 + j0.156772 | 988.01 |
| diagonal Hω cycle | 54.593165 % | 0.842514 + j0.156823 | 689.07 |
| forward Hω cycle | 54.593151 % | 0.842513 + j0.156824 | 710.52 |

출처: 세 [result.json](snapshots/hq/outputs/research/astra-l04-10mhz-forward-closed-gcrotmk-01/result.json)의 `raw_z_unvalidated`, `metrics.norms`; [REPORT §4.2–4.3](REPORT.md).

**[사실]** 세 실험은 `m=2, k=0, maxiter=1`의 GCROT([driver-at-run.py L322](snapshots/hq/outputs/research/astra-l04-10mhz-forward-closed-gcrotmk-01/driver-at-run.py#L322)), 즉 전처리된 2차원 Krylov 보정 한 번이다. 후보 Z의 변화는 warm start 대비 0.001 %p 이내다. **[해석]** 52분의 계산으로 얻은 것은 "ψ-블록의 M 변형은 접점 블록 잔차를 유의하게 줄이지 못한다"는 한 가지 사실뿐이다(§3 Q3). 상대잔차 710은 x=0의 상대잔차 1보다 세 자릿수 크므로, 잔차 관점에서 이 warm start는 시작점으로서 영(0)벡터보다 나쁘다. 즉 현재 후보는 "A의 근사해"가 아니라 "다른 모델의 해에 미소 보정을 더한 상태"이며, 그 포트 값 54.59 %는 A와 무관하다. README와 REPORT §1은 이 값을 "미수렴"으로 표시하면서도 여전히 비교 축으로 삼고 있는데, 이 값은 비교 축이 될 수 없다.

### 2.3 M의 구조: 잔차의 99.96 %를 차지하는 접점 블록에는 자기 항이 없다

**[사실]** A의 접점 행은 `−d − Z₀₄ᴿg − PᵀR₀₄Cψ − jω Pᵀf₀₄`([REPORT §3.2](REPORT.md#L91))이며 10 MHz에서 `jω Pᵀ L (Pg + Cψ)`가 지배한다. 그런데 M의 base 블록 [`_one_r_direction`](snapshots/hq/tools/research/probe_astra_l04_10mhz_two_direction_complete_current.py#L159)은 L25 분기에만 `resistance + jω·lself`([L177](snapshots/hq/tools/research/probe_astra_l04_10mhz_two_direction_complete_current.py#L177))를 넣고, L04 접점은 저항 NtD 캐시(B1 factor)와 [`lifted_mna_preconditioner`](snapshots/hq/tools/research/probe_astra_l04_10mhz_two_direction_complete_current.py#L208)로만 처리한다. Hω/forward 변형은 ψ 블록만 바꿨다([driver L199–243](snapshots/hq/outputs/research/astra-l04-10mhz-forward-closed-gcrotmk-01/driver-at-run.py#L199)). 결과적으로 접점 잔차는 1.396→0.69–0.99로만 줄고 전체의 99.96 %를 유지했다.

**[해석]** 이는 FMM-가속 MQS 문제에서 잘 알려진 실패 형태다. FASTHENRY(Kamon–Tsuk–White 1994, 참고문헌 [3])가 GMRES에 쓰는 전처리는 **모든** 전류 미지수에 대해 국소(near-field) 부분 인덕턴스를 포함한 블록이며, 저항만으로 만든 전처리는 ωL ≫ R인 주파수에서 작동하지 않는다. 접점 블록에 `jω Sg PᵀL_near P Sg`에 해당하는 항(희소 near 부분 또는 그 대각 근사)이 들어가지 않는 한, ψ 블록을 어떻게 바꿔도 전체 잔차는 크게 줄지 않을 것으로 예상한다. 이것은 가설이며, §4의 D3(소형 dense 대조)에서 M 변형별 GMRES 반복 횟수를 재면 몇 분 안에 확정된다. 4.46백만 문제에서 확인하는 것은 권하지 않는다.

### 2.4 A의 자기 연산자는 전원 전류가 흐르는 도체의 대부분을 포함하지 않는다 (SPD로 확인 필요, 가장 중요)

**[사실]** 자기 작용은 `[f₂₅; f₀₄] = L[i₂₅; Pg + Cψ]`, 즉 L25 분기 전류와 L04 전류에만 적용된다([REPORT §3.2](REPORT.md#L86), [joint_action](snapshots/hq/tools/research/probe_astra_l25_l04_joint_magnetic_action.py#L100)). L14, L02, TOP, 비아는 `Y, ΔY`(저항·G/C·source-owned scalar R/L stamp) 안에만 있다. 감독 기록의 source-current census([ASTRA_STEP5 L3903](snapshots/hq/docs/evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md.txt#L3903))는 native baseline에서 1 A가 **L14(전원) 1.0017 A, L02(DGND) 1.0017 A, TOP(DGND) 1.0000 A**를 통과하고 L04는 0.19 mA, L25 비아는 2.4 µA만 통과함을 기록한다. 이후 L02에 유한 저항을 주자 L04 비아 전류가 9.5 µA→0.96 A로([L2416](snapshots/hq/docs/evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md.txt#L2416)), L14에 유한 저항을 주자 같은 전원망의 L25 비아 전류가 2.4 µA→0.906 A로 바뀌었다. 1 MHz 1차 자기 민감도는 `dZ/dλ = j12.1 mΩ`이며 **"L14↔L25가 지배"**한다([L1268](snapshots/hq/docs/evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md.txt#L1268)).

**[해석]** 세 가지가 따라온다.

- 조건부 모델의 전류 분포는 "어느 층에 유한 저항을 주었는가"에 따라 세 자릿수로 바뀐다. 즉 L25/L04에 계산된 자기 에너지는 실제 보드의 전류 분포가 아니라 **모델 선택이 만든 분포**의 에너지다. 감독 기록 스스로 "계산 비용이나 구현 완료 여부만으로 층별 물리를 달리하지 않는다"([L2412 부근](snapshots/hq/docs/evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md.txt#L2412))는 원칙을 세웠으나, 현재 A는 이 원칙을 지키지 않는다.
- 루프 인덕턴스는 `L_self(전원) + L_self(귀환) − 2M`이다. 전원 측 주 경로(L14)와 귀환 측 주 경로(L02/TOP)에 자기 항이 없는 모델은 아무리 정확히 풀어도 참조의 15 pH를 만들 수 없다. 가장 큰 자기 상호작용 항(L14↔L25)이 A 밖에 있다는 것을 감독 기록이 이미 측정했다.
- 따라서 보고서 H3("A의 물리 누락이 크다")는 "증명되지 않은 가설"이 아니라, 소유 표만 완성하면 solver 없이 판정되는 **구조적 사실**에 가깝다. 이 판정이 나오는 순간 H1/H2/H5(M·부분공간·내부 정확도)는 목표에 대한 우선순위를 잃는다.

이 항목은 SPD 없이 감독 기록에서 읽은 것이므로, D1의 소유 표로 확정하기 바란다. 만약 L14가 port18의 전원 경로가 아니라는 반증이 나오면 §2.4의 결론은 철회한다. 그 경우에도 "자기 소유가 층별로 다르다"는 사실 자체는 남는다.

### 2.5 비용 구조: 현재 경로는 하드웨어와 무관하게 제품 시간 목표와 양립하지 않는다

**[사실]** cycle당 980–1075 s(A 3회, FMM 12회, FMM이 77–79 %), `two_step_residual_factor` 0.49–0.71([result.json](snapshots/hq/outputs/research/astra-l04-10mhz-forward-closed-gcrotmk-01/result.json)). **[산술]** cycle마다 0.5의 감쇠가 유지된다는 낙관적 가정에서도 710→1e−9는 약 40 cycle, 노트북에서 약 11시간, **10 MHz 한 점**이다. 워크스테이션·GPU로 10배를 얻어도 점당 1시간이고, 주대역 5 decade × 10점이면 이틀이 넘는다. 설계 변경마다 반복된다.

**[해석]** 원인은 특정 M이 아니라 "반복마다 2.17백만 점 FMM을 재계산하는 구조"다. Laplace kernel(지연 없음)을 쓰는 MQS의 인덕턴스 연산자 L은 **주파수와 무관**하다. 따라서 한 번 조립·압축(H-matrix/ACA, 참고문헌 [9][10])해 저장하고 모든 주파수·설계 변경에 재사용하는 것이 PEEC 계열의 표준이며, 512 GB RAM은 정확히 이 용도에서 결정적이다. 압축 L에 대한 직접 또는 저비용 반복 풀이가 가능해지면 "포트당·주파수당 몇 분"이 현실적이다. GPU는 조립과 행렬-벡터 곱을 빠르게 하지만 위 구조 문제를 바꾸지 않는다.

---

## 3. 우선 검토 질문별 판정 (요청서 형식)

### Q1. 물리 문제의 정의 — 우선순위 1

```text
판정: 수정
근거: REPORT §3.1–3.3; joint_action L100–123(Laplace lfmm3d, centroid); ASTRA_STEP5 L3903/L1268/L2416; §2.4
```

**현재 근거로 확정할 수 있는 내용.** 현재 조건부 A는 "두 sheet(L25 전원망 조각, L04 DGND)의 자기준정적 sheet 전류 문제(Laplace kernel, midplane, DC sheet R, RT0 + 닫힌 전류 ψ)와 나머지 도체·비아·decap·유전체의 집중 회로(Y, ΔY, G/C stamp)를 접점 g로 결합한 MNA"다. 지연이 없는 Laplace kernel은 전기적으로 작은 구조에서 1 kHz–100 MHz의 결함이 **아니다**(εr≈4에서 100 MHz의 λ/10 ≈ 15 cm; 보드 최대 치수가 이보다 크면 평면쌍의 분포 C를 2D 파동 문제로 넣어야 하나, 그것은 3D full-wave가 아니라 평면쌍 cavity/MFDM 문제다). L03/L05 등 artwork 부재 층의 eddy 귀환은 있다면 L을 **줄이는** 방향이므로 §2.1 결손의 원인이 아니다.

**아직 확정할 수 없는 내용.** port18의 1 A가 실제로 통과하는 전원·귀환 도체와 비아의 완전한 목록, 각각에 대한 (저항 / 자기 / G·C / 없음) 소유. `source-owned scalar L`과 sheet 자기의 중복 여부. L25에 있는 전원망 조각과 L14 주 평면의 관계.

**가장 작은 판별 실험 또는 반증 조건 (D1).** SPD와 census 결과만으로, 선택 rail의 전원 전류가 흐르는 모든 도체(층·net·섬·비아 그룹)에 대해 4열(저항 모델 / 자기 모델 / G·C / 전류 통과량)의 **소유 표** 한 장을 만든다. solver 실행 없이 반나절 작업이다. 전원 측 주 경로가 "자기 모델 없음"이면 §2.4가 확정된다. 반증: 1 A의 90 % 이상이 자기 항을 가진 도체만 통과한다는 census.

**연구 재개에 필요한 증거.** 완성된 소유 표와, 소유가 균일하지 않은 항목을 어떻게 균일하게 할지(sheet 추가, 비아 PEEC, 또는 명시적 제외 근거)의 결정. 이전에는 4.46백만 문제의 수치 실험을 재개하지 않는다.

### Q2. 오차와 판정 — 우선순위 2

```text
판정: 수정
근거: REPORT §5.3–5.4; Higham [11]; Becker–Rannacher [12]; docs/EVALUATION_ACCURACY.md L166–195; Smith 1999 [7]
```

**분리 방법.** 네 오차원을 다음 순서로 분리한다. (i) *같은 A의 수치 오차*: 포트 functional ℓ에 대한 adjoint 가중 잔차 `δZ ≈ zᵀr`. A가 복소 대칭이고 입·출력 포트가 같으면 adjoint 해 z는 primal 해 x*와 같으므로, 보고서의 `J_stat − Z_raw = xᵀr`은 정확히 "adjoint를 현재 반복해로 근사한 1차 보정"이다. 보고서의 반례(§5.3)는 그 근사가 e=x*−x만큼 틀릴 때 2차항 `eᵀAe`가 남는다는 사실의 다른 표현이며, 잔차가 710인 지금은 2차항이 지배하므로 |J−Z|를 오차 상한으로 쓸 수 없다는 보고서 판단에 동의한다. (ii) *이산화 오차*: 같은 물리·같은 소유의 coarse/fine 두 메시에서 **수렴한** Z의 차이. (iii) *모델 오차*: 소유가 완전한 모델과 조건부 모델의 수렴 Z 차이. (iv) *참조 조건 차이*: PowerSI 설정(메시, 재료 분산, 포트 정의) 변화에 대한 참조 Z의 민감도. (i)–(iii)은 D3의 소형 문제에서 모두 측정 가능하다.

**20 %와 1e−9의 대체.** 1e−9 상대잔차는 포트 오차 예산에서 유도된 값이 아니며, 반대로 20 %는 어떤 오차원과도 연결되지 않은 운영 선별값이다. 제안: 정지 기준을 `|zᵀr| ≤ 0.1 × (제품 허용 오차) × |Z_ref|`로, 즉 수치 오차가 전체 예산의 1/10 이하가 되는 지점으로 둔다. 이는 backward error `‖r‖/(‖A‖‖x‖+‖b‖)`(Higham [11])와 함께 기록한다. 기존 판정을 소급 변경하지 말라는 보고서 요구에 동의한다.

**제품 계약 제안(수치).** 저장소 최상위 [EVALUATION_ACCURACY.md L166–195](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/215d7f5a69e3fec285c31778492f360e08e34851/docs/EVALUATION_ACCURACY.md#L166-L195)에는 이미 승인된 정책(저주파 offset ≤ 1.0 dB, 절대 ≤ 2.00 dB, 공진 주파수 ≤ 10 %)이 있다. 요청서가 "수치 계약이 없다"고 쓴 것과 어긋나므로 먼저 정합시켜야 한다. 그 정책을 복소 오차로 확장한 초안은 다음과 같다.

| 항목 | 1 kHz–1 MHz | 1–100 MHz | 근거 |
|---|---|---|---|
| 복소 오차 E_Z | ≤ 10 % (≈0.8 dB) | 초기 ≤ 20 % (≈1.6 dB), 목표 ≤ 10 % | 기존 1.0/2.0 dB 정책과 정합 |
| 위상 Δφ | ≤ 5° | ≤ 10° | decap 선정에서 R/L 분리가 필요 |
| 공진·반공진 | 주파수 ±10 %, 높이 ±2 dB | 동일 | 기존 정책 |
| 절대 floor | 0.05 mΩ | 0.05 mΩ | 1–10 MHz의 |Z_ref| 0.4–1.3 mΩ의 약 5 % |
| 참조 불확실성 | PowerSI 설정 민감도로 측정해 floor에 합산 | 동일 | 현재 packet에 없음 |

근거는 target-impedance 설계법(Smith 등 1999, [7])에서 |Z| 오차가 곧 설계 마진이라는 점이다. 10 %는 참조 도구 자체의 불확실성과 같은 자릿수일 가능성이 높으므로, (iv)를 측정하기 전에는 이보다 조이지 않는다.

**확정할 수 있는 내용.** 현재 어떤 후보도 위 초안을 만족하지 않으며, 26.18 %와 54.59 %는 서로 다른 모델·주파수·수렴 상태의 값이라 "악화율"로 읽으면 안 된다(보고서와 동의). **확정할 수 없는 내용.** 참조의 불확실성; 같은 A의 수렴 Z. **가장 작은 판별.** D3에서 (i)–(iii)을 한 문제로 측정. **재개 증거.** 위 표를 사용자가 확정한 문서 한 장.

### Q3. 최근 개선의 의미 — 우선순위 4

```text
판정: 동의 (보고서의 부정적 결론) + 수정 (원인 진단)
근거: §2.2–2.3; result.json 세 건; _one_r_direction L159–208; Kamon 1994 [3]; de Sturler 1999 / Hicken–Zingg 2010 [4][5]
```

**입증한 것.** 같은 A·같은 시작점에서 ψ 블록의 M 변형(real-H → diagonal Hω → forward Hω)은 접점 블록 잔차를 유의하게 줄이지 못하고, 포트 값을 바꾸지 못한다. **입증하지 못한 것.** A의 Z, GCROT 계열의 수렴성, forward 결합의 가치, "국소 closed 방정식을 정확히 풀면 전체가 좋아진다"의 참·거짓(한 cycle로는 어느 쪽도 아님). 4방향 union이 cap을 못 넘긴 것은 그 4방향에 한정된 사실이며 보고서 §5.1의 한정에 동의한다.

**원인 진단의 수정.** 보고서 H1은 "M의 일부 블록·방향이 부적절"이라 쓰지만 어느 블록인지 특정하지 않는다. §2.3의 소스 확인으로 특정할 수 있다: 접점 블록에 자기 항이 없다. 이는 단방향/양방향 결합의 문제 이전의 문제다.

**다음 보조행렬 변경 전 최소 증거.** (a) D3의 소형 dense 문제에서 저항 M / 접점 near-L M / 블록 Schur M 각각의 GMRES 반복 횟수, (b) cold start(x=0) 대 THIRD warm start의 잔차 이력, (c) k>0(부분공간 재활용)의 효과. 이 셋은 몇 분짜리 실험이다. 4.46백만 문제에서의 어떤 M 실험도 이 증거 전에는 정당화되지 않는다. 복소 대칭 A에는 짧은 점화식의 COCG/QMR(Freund 1992, [6])이 메모리 면에서 GCROT의 대안이 될 수 있으나, 전처리 없이는 어느 방법도 수렴하지 않으므로 방법 선택은 부차적이다.

### Q4. 공간 표현과 adaptive mesh — 우선순위 5

```text
판정: 수정
근거: joint_action L100–123; REPORT §3.1/§3.3 "두께·기하"; ASTRA_STEP5 L660; Ramo–Whinnery–Van Duzer [8]; Becker–Rannacher [12]
```

**적절한 것.** RT0 sheet 전류, 삼각형 centroid 점전하의 Laplace FMM, self·인접 near 보정은 **외부 인덕턴스**에 대해 이 대역에서 적절한 표현이다. 단 near 구적의 정확도가 인덕턴스의 수 % 이내라는 증거(해석적 self/인접 적분과의 대조)가 필요하며, 보고서가 "L04 near 미완"이라 적었으므로 이 대조가 우선이다.

**부적절한 것.** midplane + DC sheet 저항은 ≥1 MHz에서 `R(f)`와 내부 인덕턴스를 놓친다. 구리 표피 깊이는 1/10/100 MHz에서 66/21/6.6 µm이므로 주대역 상단에서 판 두께보다 얇다. 표준 대체는 sheet 표면 임피던스 `Zs(ω) = (1+j)/(σδ)·coth((1+j)t/δ)`(1D 확산 방정식의 정확해, [8])이며, 이는 메시를 바꾸지 않고 저항 행렬만 주파수 의존으로 바꾸는 물리적 수정이다. 임의 계수 fitting이 아니다. 참조 Re의 0.69→1.34→1.78 mΩ 증가와 정합한다. 다만 §2.1의 지배 항은 허수부이므로 이 수정은 D1·D3 이후다.

**adaptive mesh.** 현재는 형상 기반 고정 메시이며 해 기반 적응은 구현·검증되지 않았다는 보고서 진단에 동의한다. 해 기반 적응은 물리 소유가 완성되기 전에는 보류한다. 종료 기준은 포트 functional의 adjoint 가중 오차 지표([12])이어야 하며, 요소 수·전체 잔차·상반성은 종료 기준이 아니다. 별도로: 2.17백만 삼각형은 저항·접점 문제의 유산으로 보인다. 인덕턴스는 적분량이라 훨씬 거친 메시로도 수 % 정확도가 나오므로, 이것이 D3의 근거다.

### Q5. 대안과 연구 순서 — 우선순위 3

```text
판정: 반대 (문헌 보고서의 우선순위) / 수정 (REPORT §7의 선택 표)
근거: pi-literature-method-review §1 표; REPORT §7; Ruehli [1][2]; Swaminathan–Engin [13]; Engin–Bharath–Swaminathan MFDM [14]; 제품 src/_core/solver/mfdm.py·modal.py
```

**반대 이유.** [문헌 보고서](snapshots/literature/pi-literature-method-review-20260908.md.txt)는 full-wave 전류·전하 VIE, 내부 DtN/DSA, 저주파 안정화 FEM을 1–3위에 두고 적층 MFEM/MFDM/plane modal을 4위에 두었다. 이 순위는 주대역이 갖지 않은 문제(지연, 고대비 유전체, 표피 내부의 3D 해상)를 겨냥한다. 1 kHz–100 MHz의 PDN 물리는 **MQS 부분 인덕턴스(도체·비아 전부) + 표면 임피던스 손실 + 집중/평면 C**이며, 이는 Ruehli의 PEEC([1][2])와 Swaminathan–Engin의 평면쌍 방법([13][14])이 다루는 문제 정의 그대로다. 제품 패키지에 이미 `mfdm.py`(평면쌍 4단자 loop 요소)와 `modal.py`(cavity modal, decap을 low-rank outer product로 갱신)가 있다. 즉 필요한 계열은 새로 만들 것이 아니라 "이미 있는 것들을 하나의 소유 표 아래 결합"하는 것이다. D-VIE·DSA·potential-BEM은 1 GHz 보조 검증이나 특정 국소 구조(예: 두꺼운 비아 배럴의 근접 효과)에 한정해 보존한다.

**REPORT §7 표에 대한 수정.** A(유지)는 연구 자료로 보존하되 진행 중지 — 동의. B(같은 A의 수치 재설계)는 D3 이후, 그리고 접점 블록 near-L 전처리를 포함할 때만. C(volume PEEC/VIE)는 이 대역의 필수가 아님. D(BEM/FEM)는 전 보드 전환이 아니라 **소형 canonical 구조의 독립 기준**으로만. E(축약 제품 경로)는 "검증된 범위에서만"이 아니라, 소유가 균일한 MQS+평면쌍 모델로 재정의하면 그것이 **제품 경로 자체**다.

**가장 빨리 원인을 가르는 한 대조 (D3).** 현재 A와 **같은 블록·같은 물리·같은 소유**를 50–100배 거친 메시(sheet당 2–4만 삼각형, 총 미지수 ≤ 10만)에 조립하고, Krylov·M 없이 **직접법**으로 1e−9까지 푼다. dense L(4만²·8 B ≈ 13 GB)은 512 GB는 물론 32 GB 노트북에서도 가능하다. 판정 규칙:

- coarse 수렴해의 Im Z(10 MHz)가 참조와 같은 자릿수(≥ 0.6 mΩ) → A는 물리를 갖고 있고 문제는 순수 수치 → B 진행(접점 L 전처리, cold start, k>0).
- Im Z가 여전히 참조의 1/3 이하 → A에 인덕턴스 물리가 없음 → 어떤 solver 작업도 보류, D1의 소유 수정 후 재조립.
- 이 대조는 Q2의 (i)(ii)(iii)과 Q3의 전처리 반복 횟수를 같은 문제에서 부산물로 준다.

**독립 기준.** 평면쌍 + 비아 쌍 + 포트 + decap 1개의 canonical 구조에서 closed-form 루프 인덕턴스, PowerSI, 현재 A(coarse)의 삼자 비교. 보드 전체가 아니라 이 구조에서 "M을 바꿔야 하는가, 소유를 바꿔야 하는가"가 갈린다.

### Q6. 제품 목표 — 우선순위 6

```text
판정: 수정
근거: §2.5; REPORT §4.3 시간·메모리 표; astra_loaded_development_selection JSON(holdout 목록); modal.py docstring
```

**대표 보드·포트.** 지금의 port18(421 decap, 강한 감쇠, 공진 없음)만으로는 제품 판정이 불가능하다. 최소 셋: (a) port18, (b) holdout 중 decap 수가 적어 1–100 MHz에 반공진이 보이는 rail 1개, (c) bare(decap 0) 조건 1개. (b)(c)는 인덕턴스와 평면 C의 오류를 감쇠 없이 드러낸다.

**설계 변경 판단.** 제품 가치는 절대 정확도보다 **민감도의 정확도**에 있다. decap 1개 제거·이동·값 변경에 대한 ΔZ가 참조의 ΔZ와 부호·크기(±30 %)로 일치하는지를 별도 지표로 둔다.

**시간 계약 제안.** 워크스테이션에서 rail당 첫 전대역 해석 ≤ 10분, decap 변경 재해석 ≤ 1분. 후자는 주파수 무관 L의 재사용과 decap의 low-rank 갱신(Sherman–Morrison–Woodbury; `modal.py`가 이미 이 구조)으로 달성 가능한 수치다. 현재 경로의 "cycle당 17분, 한 주파수, 미수렴"은 이 계약의 두 자릿수 밖이다.

**하드웨어가 푸는 것 / 못 푸는 것.** 푸는 것: 압축 L의 조립·저장 메모리, 주파수·포트 병렬, FMM/ACA 조립 시간. 못 푸는 것: 자기 소유 누락(§2.4), 표피 손실(Q4), 참조 조건 불일치(Q2 iv). 이 셋 중 하나라도 남아 있으면 계산 자원은 오차를 줄이지 못한다.

---

## 4. 권고하는 다음 한 단계 — 순서와 기각 조건

| 순서 | 작업 | 소요(추정) | 필요한 것 | 결정·기각 조건 |
|---|---|---|---|---|
| **D1** | 선택 rail의 전원·귀환 전류 경로 **소유 표**(층·net·섬·비아 그룹 × 저항/자기/G·C/전류 통과량) | 0.5일, solver 없음 | SPD, 저장 census | 전원 측 주 경로에 자기 모델이 없으면 §2.4 확정 → D3 전에 A 재조립 범위 결정. 반증 시 D3로 직행 |
| **D2** | 참조의 **인덕턴스 예산**: PowerSI에서 (a) decap을 이상 단락으로, (b) 도전율을 매우 크게, (c) decap 1개만 남긴 세 변형의 Z; SPD decap 모델의 ESL 병렬합 산술 | 1일 | PowerSI, SPD | 15 pH가 어디에 있는지(비아·평면 spreading·실장) 분해. 이 분해가 없으면 어떤 모델 성과도 "우연한 일치"와 구별되지 않음 |
| **D3** | 같은 A·같은 소유의 **coarse 직접 해**(≤10만 미지수, 1e−9), 10 MHz 한 점 + 1 MHz 한 점 | 1–3일 | 기존 메시 생성기의 거친 설정, dense L 조립 | Q5의 판정 규칙. 부산물로 Q2(i)(ii)(iii), Q3 전처리 반복 횟수 |
| **D4a** (D3에서 "수치 문제"일 때) | 접점 블록 near-L 전처리, cold start, k>0, 압축 L 재사용 | 1주 | D3 문제 | 소형에서 반복 횟수 ≤ 50이 안 되면 4.46백만으로 확대하지 않음 |
| **D4b** (D3에서 "물리 문제"일 때) | 소유가 균일한 MQS 모델 재조립: 모든 전원·귀환 sheet + 비아 PEEC(상호 인덕턴스 포함) + Zs 손실 + 기존 G/C·MFDM | 2–4주 | D1 표, `via_peec.py`, `mfdm.py`, `modal.py` | canonical 구조에서 closed-form·PowerSI와 ±10 % 이내가 안 되면 전 보드로 확대하지 않음 |
| **D5** | 대표 3 rail 전대역, 설계 변경 민감도, 시간 계약 | D4 이후 | — | §3 Q2·Q6 계약 |

D1과 D2는 병렬로 가능하며 둘 다 계산 재개가 아니다(문서·PowerSI 작업). D3가 유일한 새 solver 실행이며, 그 규모는 노트북에서도 안전하다.

## 5. 지금 보류할 작업

- 4.46백만 문제에서의 모든 M 변형·부분공간 재최적화·장기 cycle(보고서 §8과 동의, 이유는 §2.3–2.4로 강화).
- 100 MHz 연산자 조립의 확장, GPU·FMM 런타임 최적화, 새 리뷰/gate 인프라(§2.5: 구조 문제라 국소 최적화의 효과가 없음).
- D-VIE/DSA/potential-BEM의 구현 착수(Q5).
- 26.18 %·54.59 %를 진행 지표로 쓰는 모든 보고(§2.2).
- 미완 `audit_astra_l04_saved_forward_response.py`: 결과로 인용하지 않는다는 보고서 판단에 동의. 완료해도 D3 없이는 해석이 불가능하므로 우선순위 낮음.

## 6. 추가로 필요한 원본 데이터·설정

1. SPD 자체(또는 층 스택: 층 이름·net·두께·유전체·판 외곽 치수), port18과 decap 위치. D1·D2·전기적 크기 판정에 필요하다.
2. PowerSI 해석 설정(메시 옵션, 재료 분산 모델, 포트 정의·de-embedding, decap 모델의 출처와 ESL/ESR 주파수 의존). Q2 (iv)에 필요하다.
3. 참조 Touchstone의 전체 주파수 격자(현재는 decade 앵커 6점만 packet에 있음). 반공진 유무와 L_eff(f)의 연속 추세 확인에 필요하다.
4. 기존 메시 생성기의 거친 설정으로 만든 L25/L04(그리고 D1 결과에 따른 추가 sheet) 메시. D3에 필요하다.
5. 제품 문서의 승인 정책(1.0/2.0 dB, 10 %)과 연구 packet의 "계약 없음" 사이의 정합 결정.

## 7. 보고서(REPORT.md) 자체에 대한 지적

보고서는 사실·해석·제안을 구분하고 실패를 보존한 점에서 신뢰할 만하다. 다만 외부 검토 문서로서 다음은 수정을 권한다.

- **핵심 물리 정보가 본문에 없다.** 어느 층이 어느 net인지, 1 A가 어디로 흐르는지, 자기 연산자가 어느 도체를 덮는지가 표 한 장으로 제시되어야 하는데, 검토자는 4,300행의 감독 기록에서 이를 재구성해야 했다. 요청서 Q1이 묻는 "source-owner 표"는 검토자에게 요구할 것이 아니라 보고서가 제공해야 할 입력이다.
- **두 헤드라인 숫자(26.18 %, 54.59 %)의 위상이 잘못됐다.** 후자는 §2.2대로 모델과 무관한 값이며, 전자도 물리량(Re/Im 결손, L_eff)으로 분해해 제시해야 "무엇이 빠졌는가"가 보인다.
- **gate·receipt 기계장치가 물리보다 앞에 있다.** 세 실험의 산출물 대부분은 구현 일관성(해시, AST 동일성, tiny PASS)의 증거이며, 이는 필요하지만 물리 판단을 대체하지 않는다. 보고서 §8의 "폐기할 해석" 목록에 동의하며, 그 목록이 필요했다는 사실 자체가 운영상의 경고다(H6).
- **"complete-current"라는 이름**은 보고서도 경고하듯 오해를 부른다. 산출물 명명에서 물리적 완전성을 뜻하는 단어는 피하기 바란다.
- **1.1배 시간 cap·20 % screen**은 과학적 기준이 아니라는 보고서 §5.4에 동의한다. 다만 그 기준을 "보완"할 것이 아니라 D3 이전에는 사용하지 않는 것이 맞다.

## 8. 참고문헌

이 검토에서 실제로 판단 근거로 쓴 문헌만 적었다. 링크는 보고서가 이미 인용한 것 외에는 붙이지 않았으며, 서지 사항은 검토자의 지식에 의존하므로 인용 전 원문 확인을 권한다.

1. A. E. Ruehli, "Equivalent circuit models for three-dimensional multiconductor systems," *IEEE Trans. Microwave Theory Tech.*, vol. 22, no. 3, 1974. — PEEC의 원 정의(부분 인덕턴스·부분 커패시턴스의 소유).
2. A. E. Ruehli, G. Antonini, L. Jiang, *Circuit Oriented Electromagnetic Modeling Using the PEEC Techniques*, Wiley-IEEE Press, 2017. — 준정적 PEEC의 적용 범위, 비아·평면·표피 처리.
3. M. Kamon, M. J. Tsuk, J. K. White, "FASTHENRY: A multipole-accelerated 3-D inductance extraction program," *IEEE Trans. Microwave Theory Tech.*, vol. 42, no. 9, 1994. — MQS + FMM + GMRES 구조와, 국소 부분 인덕턴스를 포함하는 전처리(§2.3의 근거).
4. E. de Sturler, "Truncation strategies for optimal Krylov subspace methods," *SIAM J. Numer. Anal.*, vol. 36, no. 3, 1999. — GCROT.
5. J. E. Hicken, D. W. Zingg, "A simplified and flexible variant of GCROT for solving nonsymmetric linear systems," *SIAM J. Sci. Comput.*, vol. 32, no. 3, 2010. — SciPy `gcrotmk`의 알고리즘([공식 문서](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.gcrotmk.html)).
6. R. W. Freund, "Conjugate gradient-type methods for linear systems with complex symmetric coefficient matrices," *SIAM J. Sci. Stat. Comput.*, vol. 13, no. 1, 1992. — 복소 대칭계의 COCG/QMR.
7. L. D. Smith, R. E. Anderson, D. W. Forehand, T. J. Pelc, T. Roy, "Power distribution system design methodology and capacitor selection for modern CMOS technology," *IEEE Trans. Adv. Packag.*, vol. 22, no. 3, 1999. — target impedance 설계법(Q2 오차 계약의 근거).
8. S. Ramo, J. R. Whinnery, T. Van Duzer, *Fields and Waves in Communication Electronics*, 3rd ed., Wiley, 1994. — 유한 두께 도체의 표면 임피던스 `Zs = (1+j)/(σδ)·coth((1+j)t/δ)`.
9. W. Hackbusch, "A sparse matrix arithmetic based on H-matrices. Part I," *Computing*, vol. 62, 1999. — 계층 행렬.
10. M. Bebendorf, "Approximation of boundary element matrices," *Numer. Math.*, vol. 86, 2000. — ACA(적분 연산자의 저계수 압축).
11. N. J. Higham, *Accuracy and Stability of Numerical Algorithms*, 2nd ed., SIAM, 2002. — backward error 정의(Q2).
12. R. Becker, R. Rannacher, "An optimal control approach to a posteriori error estimation in finite element methods," *Acta Numerica*, vol. 10, 2001. — 목표 functional에 대한 adjoint 가중 오차 추정(Q2·Q4).
13. M. Swaminathan, A. E. Engin, *Power Integrity Modeling and Design for Semiconductors and Systems*, Prentice Hall, 2007. — 평면쌍·cavity·TMM 기반 PDN 모델링.
14. A. E. Engin, K. Bharath, M. Swaminathan, "Multilayered finite-difference method (MFDM) for modeling of package and printed circuit board planes," *IEEE Trans. Electromagn. Compat.*, vol. 49, no. 2, 2007. — 제품 `mfdm.py`가 구현한 방법.
15. 보고서가 인용한 Henry 등(D-VIE), Sharma–Triverio(potential BEM), Stysch 등(FEM), XRL: 원문 링크는 [REPORT §7](REPORT.md)과 [문헌 보고서](snapshots/literature/pi-literature-method-review-20260908.md.txt)를 따른다. 본 검토는 이들의 적용 대역이 주대역과 다르다는 이유로 우선순위를 낮췄을 뿐, 그 방법의 타당성을 부정하지 않는다.

---

## 부록 A. 산술 재현

아래는 검토자가 packet의 JSON에서 §2.1·§2.5의 수치를 얻은 방법이다. Python 3, 표준 라이브러리만 사용한다.

```python
import json, math
p = json.load(open("snapshots/hq/docs/evaluation-research/"
                   "astra_primary_band_comparison_2026-09-07.json", encoding="utf-8-sig"))["points"]
ref = {q["frequency_hz"]: q["reference_zdd_ohm"] for q in p}
# C from the 1 kHz / 10 kHz reference points (Im Z = ωL − 1/(ωC), two unknowns)
w1, w2 = 2*math.pi*1e3, 2*math.pi*1e4
x1, x2 = ref[1e3][1], ref[1e4][1]
L = (w1*x1 - w2*x2)/(w1**2 - w2**2)   # ≈ −16 nH: meaningless at 1–10 kHz (capacitive), not used
C = 1/(w1**2*L - w1*x1)               # = 262.81 µF
leff = lambda f, z: (z[1] + 1/(2*math.pi*f*C))/(2*math.pi*f)*1e12   # pH
for f in (1e7, 1e8, 1e9):
    print(f, round(leff(f, ref[f]), 2))                            # 14.91, 16.74, 17.50
z = [0.0008425134786822031, 0.00015682376349129848]                 # forward cycle raw Z
zr = [1.335133270526e-3, 0.876112983039e-3]
print(round(leff(1e7, z), 2))                                       # 3.46 pH
print([(z[i]-zr[i])*1e3 for i in range(2)],                         # −0.4926, −0.7193 mΩ
      math.hypot(z[0]-zr[0], z[1]-zr[1])/math.hypot(*zr)*100)        # 54.593 %
print(math.log(710/1e-9)/math.log(2))                               # 39.4 cycles at factor 0.5
print([round(math.sqrt(1/(math.pi*f*4e-7*math.pi*5.8e7))*1e6, 1)   # Cu skin depth, µm
       for f in (1e6, 1e7, 1e8)])                                   # 66.1, 20.9, 6.6
```

`C = 262.81 µF`는 1 kHz·10 kHz 두 점의 해이며, 10 MHz 이상에서는 `1/(ωC)` 항이 0.06 mΩ 이하로 L_eff 산출에 거의 영향이 없다. 1 MHz는 decap 직렬 공진 대역이므로 단일 L·C 해석에서 제외했다.

## 부록 B. 이 검토의 한계

- 검토자는 SPD·Touchstone·PowerSI에 접근하지 못했다. §2.4의 "L14가 전원 주 경로"는 감독 기록의 census에서 읽은 것이며 D1로 확정해야 한다.
- 검토자는 대형 NPZ를 열지 않았다. 세 실험의 Krylov 방향에 대한 독립 분석(예: 잔차의 블록 분포를 좌표별로 보는 것)은 하지 않았다.
- 참고문헌의 서지 사항은 검토자의 기억에 의존하며, 인용 전 원문 대조가 필요하다.
- 소요 시간 추정(§4)은 이 저장소의 도구 성숙도를 모르는 상태의 추정이다.
