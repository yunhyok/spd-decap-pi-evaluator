# SPD→Z(f) 연구 심층 검토 (2026-09-15, 읽기 전용)

약칭: `S/` = `docs/handoff/2026-09-15/snapshots/`, `PR:` = `git show origin/codex/peer-review-20260910:docs/peer-review/2026-09-10/snapshots/hq/docs/evaluation-research/`, `SOL/` = `src/spd_decap_pi/_core/solver/`. 계산은 저장된 JSON 값의 산술만 했고 solver는 돌리지 않았다. [가설]은 코드·기록에서 직접 확인하지 못한 추론이다.

## 1. 사실 요약

### 1.1 솔버 계열

| 계열 | 방정식·미지수 | 포함 | 제외 | 근거 |
|---|---|---|---|---|
| **Layerwise admittance (제품 기본값)** | (layer,NET) 금속면 하나 = 등전위 노드 하나. `Yglobal = Σ Ekᵀ·jωε(f)Ck·Ek + Yvia`, open-port Kron `Yreduced v = b` | 인접 gap Maxwell C, 유전 분산, via self R/L을 병렬 링크로, decap/Device 종단 | **면 내부의 R/L 없음**. trace R/L, via 상호·귀환 결합, pad/antipad, spreading 없음 | `docs/EVALUATION_ACCURACY.md:20-48`; `docs/EVALUATION_LAYER_SURFACE_VALIDATION_2026-08-06.md:193-198`; `SOL/layer_surface_network.py:1-8`; `docs/evaluation-research/RESEARCH_STATE.md:225` |
| **Via R/L 추정** | 세그먼트마다 `R = l/(σA)`, `L = 0.2·l·(ln(4l/d)+1)` nH | 고립 직선 도선 self L | 귀환 경로, 상호 L | `src/spd_decap_pi/_core/via_model.py:132-162` |
| **Astra 조건부 하이브리드** | `A(v,i)=[Yv+Bi, Bᵀv−Ri]`. 전위 3,178,104개와 명시 전류 604,031개, 합계 3.78M 미지수. FMM3D Laplace 자기 작용, GMRES와 정적 M | native layerwise 위에 L25·L14·L02 일부 sheet의 RT0 DC R과 G/C. 자기 항은 L25/L04, 이후 L14/L25에만 | 나머지 도체·via의 자기 항, 내부 확산(표피) | `handoff.md:107-109`; `S/HQ/docs/evaluation-research/ASTRA_COMMON_MODEL_2026-09-12.md:120-126` |
| **MFDM** (`mfdm.py`) | cut-cell raster. 셀 C=εA/d, 인접 gap마다 4단자 loop `jωμ0·h·(l/w)`와 양면 표면 임피던스 `Zc·[[coth,csch],[csch,coth]]`. sparse LU | plane-pair C, gap loop 인덕턴스, 유한 두께 표피·양면 결합, tanδ | via, decap, 비인접 층 결합(experimental). 포트는 같은 셀의 P/G. 열(column)마다 활성 층이 연속이어야 함 | `SOL/mfdm.py:1-18, 735-825, 836-988, 1067-1158, 991-1010, 1013-1064` |
| **MFDM adapter** | normalized artwork → raster. 한 셀에 net이 둘이면 fail-closed | 층별·net별 label | 제품 경로에 연결되지 않음 | `SOL/mfdm_adapter.py:1-19, 248, 523, 656` |
| **Modal (Legacy)** | 직사각 cavity Galerkin. 모드 임피던스 `Zs/(A(k²−γ²))`, (0,0) 모드는 `1/(jωCplane)`. decap은 low-rank outer product, Device branch는 MNA로 보강 | plane C, `jωμ0h` + coth 표피, 유전 분산, finite-area port, decap·via template 직렬 경로 | 임의 형상(bounding rectangle), 비인접 결합. mode ≤12에서 미수렴 | `SOL/modal.py:1-8, 44-102, 806-962, 1571-1590, 1666-1717`; `docs/EVALUATION_ACCURACY.md:113-135` |
| **via_peec** | 평행 z-필라멘트 Neumann partial L, 원통 내부 Z(Bessel) | self·mutual partial L | pad·antipad·plane. `global_mna_composable=False` | `SOL/via_peec.py:1-12, 41-99, 333-375` |
| 기타 연구 모듈 | FFT 펄스-BEM 정전 C, 축대칭 r-z FV, full multinet MNA | 정전 C 진단 | 평가기에서 import하지 않음 | `src/spd_decap_pi/fft_bem_capacitance.py:1-12`; `research_axisymmetric_electrostatics.py:1-9`; `research_full_multinet_hybrid.py:1-8` |

### 1.2 SPD에서 실제로 읽는 것 (`src/spd_decap_pi/_core/io/spd.py`)

- **읽는 것:** mmap 스트리밍(`:1-8`)으로 `.NetList` PowerNets/GND(`:2871`), `.Shape` Polygon·PolygonTrace·Circle·Box(`:2962-2990`), Material·DielectricModel·MetalModel(`:3292`), 층 Thickness·Material·Conductivity(`:3378-3411`), PadStackDef(`:3473`), `.PartialCkt`의 2단자 RLC(K) subckt(`:3576-3644`, `_core/models/spice.py:1-6, 374`), `.Part`·`.Component`(StartLayer/AttachLayer)·`.Connect`($Package.Node, `:3647-3712`), Node·Trace·Via 구간(`:4048, 2424, 8959-8966`).
- **읽지 않는 것:** `PortBegin`/`PositiveTerminal`/`.Port` 포트 정의는 `src/` 전체에서 grep 결과가 0건이다. 포트는 SITE Device `.Connect`에서 추론한다. 지원하지 않는 PartialCkt는 경고만 남기고 건너뛴다(`spd.py:3633-3634`). Touchstone은 비교에만 쓴다(`_core/io/touchstone.py:1, 273, 301`).
- 대상 보드는 MLO다. 층 높이는 L02 55–75 µm, L14 655–675 µm, L25 1511–1543 µm이고(PR `ASTRA_STEP5_SUPERVISION_2026-09-07.md.txt:1426`), SPD 파일 크기는 1.12 GB다(`docs/EVALUATION_ACCURACY.md:233`, snapshot 기준).

## 2. PowerSI 대비 기록된 정확도

| 보드/rail | 주파수 | 방법 | 오차 | 출처 |
|---|---|---|---|---|
| 260729 loaded 6 rail | 100 kHz–100 MHz | Legacy modal m6 (`modal-mvp-0.7.0`) | RMS 1.20 / **2.60** / 4.25 dB (min/median/max), 0/6 modal-converged | `docs/EVALUATION_DISTRIBUTION_VALIDATION_2026-08-06.md:109-113` |
| 260729 VQPS bare | 같음 | Legacy modal m6 | RMS 4.24–6.47 dB | 같은 파일 `:111-112` |
| 260729 loaded | — | modal m6/8/12 | RMS 3.19 / 3.54 / 4.01 dB. 모드를 늘릴수록 악화 | `docs/EVALUATION_ACCURACY.md:113-135` |
| 260729 VINT0/VINT1 | — | modal-selection 개선 | RMS 0.84 / 0.55 dB, 위상 RMS 4.93° / 3.72° | `docs/EVALUATION_ACCURACY.md:121-130` |
| 260729 loaded mean | — | Legacy → hybrid layer-surface m6 | 2.583 → 2.979 dB | `docs/EVALUATION_LAYER_SURFACE_VALIDATION_2026-08-06.md:171-180` |
| 260729 W6 (layerwise v8) | 241점 | 제품 layerwise | bare macro 1.71 dB, **loaded macro 15.91 dB**. loaded 오차는 1 MHz −3.1~−6.0 dB, 10 MHz −22.8~−27.5 dB | `docs/WORK_EXECUTION_BASELINE.md:464-471` |
| 260804 r4 (layerwise) | 100 kHz–100 MHz | 제품 layerwise | loaded RMS **16.74 dB**, 위상 44.3°. 실행 시간 6.79 h | `docs/evaluation-research/RESEARCH_STATE.md:212-223` |
| 260729 port18 | 1 kHz / 10 kHz / 100 kHz | native layerwise | 0.09% / 0.90% / 8.75% | PR `astra_primary_band_comparison_2026-09-07.json:26-114` |
| 같음 | 같음 | two-sheet (L14/L25 DC sheet) | 0.025% / 0.24% / 2.38% | 같음 |
| 260729 port18 | 1 MHz | native / two-sheet / RT0 / hybrid | 76.1% / 29.4% / 26.47% / **26.18%** (위상 −15.1°) | PR `astra_two_sheet_four_frequency_comparison_2026-09-07.json:30-58`; PR `astra_rt0_board_1mhz_comparison_2026-09-07.json:8-26`; `S/HQ/docs/evaluation-research/astra_hybrid_r_gc_1mhz_comparison_2026-09-09.json:8-20` |
| 같음 | 10 / 100 MHz / 1 GHz | native / two-sheet | 92.6/65.1%, 91.4/86.8%, 90.9/90.5% | PR `astra_two_sheet_four_frequency_comparison_2026-09-07.json:59-140` |
| 같음 | 10 MHz | Astra L04 GCROT (미수렴) | 54.59%. 모델 정확도로 인용할 수 없음 | Fable §2.2 (`REVIEW_CLAUDE_FABLE_2026-09-10.md:52-63`) |

native layerwise는 1 MHz 한 점을 푸는 데 1095 s, 최대 13.1 GB를 썼다(PR `astra_native_loaded_development_rail_2026-09-07.json:69-73`).

**MFDM은 실제 보드에서 PowerSI와 비교된 기록이 없다.** 문서상 `mfdm`은 surface-impedance 재사용 추적과 oracle harness 후보로만 등장한다(`docs/WORK_EXECUTION_BASELINE.md:147`, `RESEARCH_STATE.md:233,243`, `docs/evaluation-research/SESSION_LOG.md:254`). 제품 경로에 연결되지 않은 채 방치됐다(`SOL/mfdm_adapter.py:3-6`). "왜 버렸는가"를 적은 기록은 없다. 실제 이력은 **modal(2.6 dB) → 모든 면을 등전위로 두는 layerwise(15.9–16.7 dB)로 교체 → 그 위에 Astra로 sheet를 하나씩 되붙이는 과정**이었다.

## 3. 판정

- **Astra 전장 경로 — DISCARD.** 3.78M 미지수, 최대 24.4 GB 메모리, 2214 s를 쓰고도 info=1/3으로 미수렴이고 KCL 오차가 18–50 mA다(`handoff.md:130-135`). 이 비용이 주파수 1점·포트 1개의 값이다. 노트북 VM의 3 GB와 1 kHz–100 MHz sweep 요구와는 구조적으로 양립할 수 없다. 물리도 불균일하다. 자기 항은 2개 sheet에만 있고 나머지는 등전위 기반 위에 얹혀 있다(`ASTRA_COMMON_MODEL:120-126`). 마지막 1주는 전처리기 결함 진단에 쓰였고 PowerSI 오차는 한 번도 줄지 않았다. P1·RT0·hybrid 이산화를 바꿔도 26.2–29.4% 사이에서 거의 움직이지 않았다는 점은 수치가 아니라 모델 형태가 원인이라는 증거다. field.npz 등은 기록으로만 보존한다.
- **FMM 런타임 — DISCARD.** 반복마다 FMM을 호출하는 구조 자체가 문제다. 1–100 MHz PDN에서 필요한 인덕턴스는 plane-pair의 `μ0·h`와 via loop로 해석적·국소적으로 얻을 수 있다. 보드 전체 Laplace FMM이 필요하지 않다.
- **정적 전처리기 연구 — DISCARD.** 폐기할 모델의 수렴 문제를 푸는 작업이다. 이를 고쳐도 목표 지표는 개선되지 않는다(`HQ_STOPPED_CHECKPOINT:26-30`).
- **MFDM — KEEP(커널) / REWRITE(연결).** 목표 대역에 맞는 물리 계열이다(Swaminathan–Engin 평면쌍 방법). layerwise에 빠진 면 내부 L/R이 바로 이 커널의 핵심 stamp다(`mfdm.py:1134-1154`). 다만 3개 도체 raster, 열마다 연속 층 요구, 같은 셀 포트 요구, via·decap 부재 때문에 MLO 전체에 그대로 쓸 수 없다. 권고는 plane-pair별 2D sparse 해를 decap·via·DUT 포트 위치에서 Z-행렬로 축약한 뒤 via loop L과 decap 모델로 회로를 결합하는 방식이다. 이는 PowerSI 계열이 쓰는 평면쌍+해석적 via+회로 하이브리드와 같은 구조다.
- **Modal — KEEP(교차검증·fallback).** 기록된 loaded 정확도 중 가장 좋은 값(median 2.6 dB)이다. 직사각 가정과 모드 절단 때문에 일반해는 아니다. 해석적 기준으로 두고 decap low-rank 갱신 구조(`modal.py:1571-1590`)를 재사용한다.
- **via PEEC — REWRITE.** 적분 함수(`via_peec.py:333-375`)는 쓸 만하다. 그러나 고립 self L은 PDN loop L이 아니다. 필요한 것은 plane 사이 via 쌍·배열의 loop L이다(동축/cavity 공식, 또는 via-to-via partial L을 뺀 값). `via_model.py:152-158`의 고립 self L을 병렬로 stamp하는 현재 방식도 같은 문제를 안고 있다.
- **SPD adapter — KEEP(파서) / REWRITE(상위 계층).** mmap 파서는 1.1 GB 파일을 저메모리로 읽을 수 있는 자산이다. 반면 `raw_spatial_contact_compiler.py`(3380줄), `compiled_topology_asset.py`(2444줄), 각종 proof·certificate 계층은 fail-closed 형식 검증에 치우쳐 있다. 반드시 보완할 것은 SPD 포트 정의(`PortBegin`) 파싱과 trace R/L이다.
- **Evaluation pipeline(layerwise) — DISCARD(물리) / KEEP(종단 stamp 일부).** 등전위 면 모델은 loaded rail에서 체계적으로 −20 dB 이상의 오차를 낸다(`WORK_EXECUTION_BASELINE.md:468-470`). 누락 항목은 문서에 자백되어 있다(`RESEARCH_STATE.md:225`). decap 모델(`CapModel`, `spice.py`), Touchstone S→Z, 포트 매핑만 가져간다.
- **정확도 정책 문서 — REWRITE.** 수치 기준(241점, 크기·위상·complex µΩ, 공진)은 합리적이다(`EVALUATION_ACCURACY.md:163-197`). 그러나 hash rotation, one-shot controller, "consumed" 같은 절차가 연구 속도를 죽였다. 커밋 339개 대부분이 `docs: locate …`다(`git log`). 1쪽짜리 정책으로 줄이고, 1 kHz–100 MHz 전 곡선에서 복소 오차와 dB·위상을 보고하게 한다.
- **Holdout 정책 — KEEP(원칙) / REWRITE(운영).** PowerSI를 fitting에 쓰지 않고 260804를 holdout으로 두는 원칙은 옳다(`EVALUATION_ACCURACY.md:224-235`). 그러나 260729/260804는 같은 설계 계열이라 holdout으로서 약하다. `s5m6585 .s160p`, `PC-2576 .s4p` 등 다른 설계를 등록해야 한다(`handoff.md:80`).

## 4. 왜 26%인가 (1 MHz: 0.555−j0.588 mΩ vs 0.692−j0.426 mΩ)

**저주파에서 이미 보이는 결정적 단서.** port18 두 모델의 PowerSI 대비 차이(모델 − 참조, mΩ)는 PR `astra_primary_band_comparison_2026-09-07.json:26-114`와 `astra_two_sheet_four_frequency_comparison_2026-09-07.json:30-116`에서 계산했다.

| f | native ΔRe | two-sheet ΔRe | two-sheet ΔIm | −ΔIm/ω |
|---|---|---|---|---|
| 1 kHz | −0.553 | −0.151 | −0.003 (참조 대비 5 ppm) | — |
| 10 kHz | −0.551 | −0.150 | −0.010 | — |
| 100 kHz | −0.550 | −0.149 | −0.018 | **29.0 pH** |
| 1 MHz | −0.591 | −0.178 | −0.159 | **25.3 pH** |
| 10 MHz | −1.243 | −0.739 | −0.732 | 11.6 pH |

해석은 세 가지다.

- (a) 1 kHz에서 허수부가 5 ppm 이내로 일치한다. 참조의 C_eff는 262.8 µF다(Fable `:39`, 부록 A). 따라서 **decap 용량·derating·포함 여부는 맞다.**
- (b) 1–100 kHz에서 ΔRe가 주파수와 무관하게 일정하다. native는 0.55 mΩ, two-sheet는 0.15 mΩ다. 이는 **직렬 DC 저항 누락**의 특징이다. L14/L25 sheet R을 넣자 0.40 mΩ가 회복됐고 0.15 mΩ가 남았다.
- (c) 100 kHz에서 얻은 R=0.1485 mΩ, L=29.0 pH를 1 MHz two-sheet 값에 직렬로 더하면 오차가 29.4%에서 **4.6%**로 떨어진다. hybrid에 더하면 2.8%다(fitting 없이 저주파 값만 사용). 같은 값을 10 MHz에 적용하면 78%로 틀린다. 즉 누락 항은 **분포형 spreading L/R**이다. 저주파에서는 C 가중으로 먼 bulk cap까지의 L(~27 pH)을 보고, 고주파에서는 가까운 cap만 보므로(~12–17 pH) 값이 줄어든다[가설이지만 정성적으로 표준 거동].

**순위:**

1. **면 내부 인덕턴스(plane-pair `μ0·h` spreading)와 via-stack loop L의 구조적 부재** — 매우 유력. layerwise는 면을 등전위로 둔다(§1). Astra는 L25/L04/L14 일부에만 자기 항을 붙였고, 그것도 반복법이 수렴하지 않았다. 증거는 −ΔIm/ω ≈ 25–29 pH(위 표), 10 MHz 이상 L_eff 3 pH vs 15–17 pH(Fable `:41-46`), layerwise loaded 오차가 주파수가 오를수록 음수로 커지는 것(`WORK_EXECUTION_BASELINE.md:468-470`), `μ0h`를 가진 modal의 2.6 dB다. DUT(top)에서 L14(~0.66 mm), L25(~1.5 mm)까지의 microvia stack 인덕턴스도 여기에 포함된다.
2. **직렬 저항 누락 0.10–0.15 mΩ** — 유력. trace R/L은 모델에 없다(`EVALUATION_LAYER_SURFACE_VALIDATION:196-197`). 기록에 따르면 DUT 주변에 130×45 µm trace가 있다(PR `ASTRA_STEP5…:1971`). [가설] 23 µm 두께 trace 1개는 약 2 mΩ이므로 수십 개가 병렬이면 0.05–0.1 mΩ다. GND 귀환면(L04 등) sheet R, via stack R, pad 수축 저항도 후보다. L02 R을 넣자 1 MHz ΔRe가 0.178에서 0.137로 줄었다(hybrid vs two-sheet).
3. **포트 정의 불일치** — 중간. SPD의 PortBegin을 읽지 않고 +단자는 device supernode, −단자는 via vertex 1개다(`ASTRA_COMMON_MODEL:108`). 두 참조 JSON의 포트 헤더 문자열도 서로 다르다(`S/HQ/docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json:8` vs PR `astra_powersi_lowband_reference_2026-09-07.json:7`). −단자를 단일 vertex로 두면 오히려 R/L이 **커지는** 방향이므로 모델이 작게 나오는 현상의 주원인은 아니다. 다만 pin group 범위가 다르면 수 pH·수십 µΩ 수준의 차이가 날 수 있다.
4. **Decap ESR/ESL 처리** — 낮음. 1 kHz의 C가 맞고, 1–100 kHz에서 ΔRe가 일정하므로 주파수 의존 ESR 네트워크도 일관된다. ESL 오차는 SRF 위(≥10 MHz)에서 드러난다. 1 MHz의 25 pH 결손은 421개 ESL 병렬합(1–3 pH, Fable `:48`)으로 설명되지 않는다.
5. **Touchstone에 decap 미포함** — 기각. 1 kHz 참조가 −j605.7 mΩ(262.8 µF)다.
6. **표피 효과 / plane AC 저항** — 1 MHz에서는 기각. δ(1 MHz)=66 µm가 두께 20–32 µm보다 크고, ΔRe가 1 kHz부터 일정하다. 참조 Re가 1→10→100 MHz에서 0.69→1.34→1.78 mΩ로 오르는 현상은 10 MHz 이상의 과제다.
7. **수치 미수렴** — 낮음. 이산화·기저를 바꾼 네 결과가 26.2–29.4%에 모인다.
8. **비교 자체의 부적절성** — 부분적으로 해당. 1 MHz는 용량성에서 유도성으로 넘어가는 구간이라 위상이 민감하다. 절대 차이는 213 µΩ로 정책의 p95 200 µΩ에 근접한다. 무엇보다 **한 점 복소값으로는 R/L/C를 분리할 수 없다.** Touchstone에는 826개 주파수가 있으므로(PR `astra_powersi_lowband_reference_2026-09-07.json:126`) 곡선 전체로 비교해야 한다. 참조의 S→Z 조건수는 1 MHz에서 373으로 양호하다.

## 5. 재사용 가능 자산

- **SPD 파서:** `src/spd_decap_pi/_core/io/spd.py` — `analyze_spd`(8877), `_parse_layers`(3378), `_parse_materials`(3292), `_parse_padstacks`(3473), `_parse_shapes`(2962), `_parse_netlist`(2871), `_parse_partial_circuits`(3576), `_parse_metadata`(3660), `_parse_vias`(4048), `_parse_spd_trace_record`(2424). 포트 정의 파서는 새로 추가해야 한다.
- **decap 모델:** `src/spd_decap_pi/_core/models/spice.py:374` `parse_passive_subcircuit`; `_core/models/impedance.py` `ImpedanceModel`; `_core/domain.py:577` `CapModel`.
- **Touchstone:** `src/spd_decap_pi/_core/io/touchstone.py` — `read_touchstone`(166), `s_to_z`(273), `open_circuit_zpp`(301), `validate_port_manifest`(100). 기존 스크립트는 `scripts/validate_powersi_reference.py`.
- **평면쌍 물리:** `SOL/mfdm.py` — `copper_two_face_surface_impedance`(801), `_copper_two_face_terms`(735), `compile_mfdm_operator`(836), `_system_matrix`(1067), `_relative_projection`(1013). `SOL/mfdm_adapter.py` — `build_mfdm_artwork_raster`(248), `mfdm_material_from_project`(523), `solve_mfdm_weighted_ports`(656).
- **해석적 기준·유전 분산:** `SOL/modal.py` — `copper_slab_surface_impedance_per_square`(44), `DielectricDispersion`(110), `RectangularCavitySolver`(681), decap low-rank stamp `_base_matrix`(1571), `_device_system_matrix`(1666).
- **via 적분:** `SOL/via_peec.py` — `straight_wire_external_self_inductance`(333), `parallel_finite_wire_mutual_inductance`(356), `solid_cylinder_internal_impedance`(375). `src/spd_decap_pi/_core/via_model.py:61` `classify_via_conductor`(충전 여부 판정)는 쓰되, self L을 그대로 stamp하지 않는다.
- **테스트 oracle:** `tests/test_mfdm_solver.py::test_three_conductor_cell_matches_hand_dense_mna`(`ASTRA_COMMON_MODEL:149`).
- **기준 데이터:** port18 참조 7점(1 kHz–1 GHz; PR `astra_primary_band_comparison…`, `S/HQ/docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json`)과 위 §4의 저주파 R·L 결손표. 새 경량 모델의 첫 합격 조건은 "1–100 kHz ΔRe≈0, 100 kHz–1 MHz −ΔIm/ω≈0"으로 둔다.

**권고 한 줄:** Astra를 멈추고, MFDM 커널로 PWR/GND plane-pair 2D 해를 만든다. 이를 포트·decap·via 위치의 Z-행렬로 축약하고 via loop L과 decap 모델로 결합하는 경량 하이브리드를 port18 곡선 전체(1 kHz–100 MHz)에서 먼저 검증한다.
