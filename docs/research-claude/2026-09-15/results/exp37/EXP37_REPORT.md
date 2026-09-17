# EXP-37 보고서 — assemble 가속 (SPD_PI_FAST=1, 물리 변경 없음)

실행 2026-09-17, 사전 등록 `EXP37_PLAN.md`(§1 규칙·§2 기준 불변). 기준선 exp28/p 영수증. 러너 `run11.py --variant p --outdir exp37`(SPD_PI_SOLVER=cudss, SPD_PI_FAST=1, 4-병렬).

## 1. 방법 / 프로파일

`python -m cProfile`로 P14(N 34,424)와 P18(N 275,218) 전체 실행을 프로파일했다(`profile_Port14.txt`, `profile_Port18.txt`). run11은 `mdl.solve()`를 두 번 부르므로(27점 ladder + breakdown용 1e5 Hz 1점) assemble 호출은 28회다.

| 구간 | P14 | P18 |
|---|---|---|
| 전체 | 48.8 s | 268.7 s |
| **build** | **40.9 s (84 %)** | **234.5 s (87 %)** |
|  └ Sheet 균질화 `homog.batched_gx` | 22.1 s | 127.4 s |
|  └ 참조 탐색 `rasterize` → matplotlib `points_in_path` | 17.8 s | 102.9 s |
| **assemble × 28** | **2.49 s (0.089 s/f)** | **23.74 s (0.848 s/f)** |
|  └ off-plane trace `copper_surface_impedance` 파이썬 루프 | 1.98 s (80 %) | 19.92 s (84 %) |
|  └ `coo_matrix(...).tocsc()`(중복 합산) | 0.17 s (7 %) | 1.21 s (5 %) |
|  └ assemble 자체(concatenate + `ok` 마스크) | 0.24 s (9 %) | 1.76 s (7 %) |
|  └ `edge_z`(그중 `weff_of`) | 0.04 s (0.01) | 0.43 s (0.15) |
|  └ `st_`(그중 `self.map`) | 0.04 s (0.02) | 0.40 s (0.23) |
|  └ decap `impedance()` / `via_R` | 0.02 s / 0 | 0.02 s / 0 |
| cuDSS plan(1회) + 28회 factor+solve | 2.14 + 0.60 s | 2.42 + 3.80 s |

즉 주파수당 비용의 80–84 %는 **off-plane trace 1,258개(P14) / 14,777개(P18)마다 스칼라 `copper_surface_impedance`를 부르는 리스트 컴프리헨션**이고, 나머지는 주파수와 무관한 인덱스 작업(`self.map`, `ok` 마스크, COO 재구성)이다.

## 2. 변경 (옵트인 `SPD_PI_FAST=1`, 기본 경로 무변경)

새 모듈 `tools/research-claude/common/fast_assemble.py`(102줄, self-check `demo()` 포함) + 호출부 3곳.

| 항목 | 내용 |
|---|---|
| `YPattern` | Y의 (row, col)과 범위 마스크는 f에 무관 → 모델당 1회 저장. 주파수마다 값 벡터만 다시 만들고 **scipy가 그대로 COO→CSC 변환**한다. `assemble()`의 `self.map`·`np.where`·`ok` 재계산이 사라진다. |
| `trace_zs` | `copper_surface_impedance`는 스칼라 (sigma, t)만 받는다. 서로 다른 (sigma, t) 쌍은 한 줌뿐이므로 쌍마다 1회 호출 후 gather. 같은 스칼라 호출이라 값은 비트 동일. |
| `weff` | `run4.Model4.weff_of`(엣지별 fringing 분기)에 f가 없는데 매 주파수 재계산 → 1회 캐시. `edge_z`의 산술은 원본 그대로 유지. |
| 호출부 | `exp3/model3.py`: import 1줄, `assemble()`에서 `pat`이 있으면 rows/cols 생략 + FAST 분기 반환(+12줄). `exp4/run4.py`: `edge_z`의 weff 한 줄(+1줄). 기본 경로 줄은 한 줄도 바뀌지 않았다. |

**Y는 비트 단위로 동일하다.** 캐시는 전부 같은 산술의 *입력*이지 산술의 재결합이 아니다. 중간 설계(`np.add.reduceat`로 중복을 직접 합산, `(ell/wid)/G` 선계산)는 2배 더 빨랐으나 Y가 1e-16 어긋났고, 최저 주파수에서 그 1e-16이 Z에서 **8e-9**로 증폭됐다(260804 P18 @ 3.02e4 Hz 실측). scipy `csr_sort_indices`가 불안정 정렬이라 중복 합산 순서는 재현할 수도 없다 → 채택하지 않음. 1e-8 예산은 cuDSS가 이미 절반을 쓰고 있어 여유가 없다.

## 3. 검증 표

(a) 7케이스, `SPD_PI_FAST=1 SPD_PI_SOLVER=cudss`, 기준선 exp28/p(CPU splu). 두 조건 모두 4-병렬 부하.

| 케이스 | N | max \|ΔZ\|/\|Z\| | 벽시계 base → new | Σ assemble base → new | assemble/f base → new | peak RSS MB |
|---|---|---|---|---|---|---|
| 260729 Port1 | 118,064 | 2.50e-09 | 395 → 123 s (3.2x) | 15.1 → 1.27 s (12x) | 0.557 → 0.047 | 1156 → 2064 |
| 260729 Port7 | 586,481 | 3.28e-08 → **1.77e-09**(IR) | 2977 → 756 s (3.9x) | 73.0 → 5.41 s (14x) | 2.705 → 0.200 | 2187 → 2782 |
| 260729 Port14 | 34,424 | 4.80e-10 | 150 → 52 s (2.9x) | 6.9 → 0.40 s (17x) | 0.257 → 0.015 | 782 → 1929 |
| 260729 Port16 | 334,638 | 2.11e-10 | 1493 → 472 s (3.2x) | 36.4 → 3.63 s (10x) | 1.348 → 0.135 | 1500 → 2392 |
| 260729 Port18 | 275,218 | 3.03e-09 | 1074 → 324 s (3.3x) | 57.9 → 3.46 s (17x) | 2.145 → 0.128 | 1437 → 2278 |
| 260729 Port19 | 69,205 | 8.26e-09 | 378 → 113 s (3.4x) | 4.5 → 0.73 s (6x) | 0.167 → 0.027 | 1325 → 1981 |
| 260804 Port18 | 275,177 | 8.25e-09 → **2.95e-10**(IR) | 1088 → 252 s (4.3x) | 61.2 → 2.75 s (22x) | 2.268 → 0.102 | 1131 → 1974 |

Port7·260804 P18은 IR=1 재실행 값(§3-1), 나머지 5개는 IR 도입 전 값이다 — 이 5개는 IR 없이 이미 ≤ 8.3e-09이고 IR은 정확도를 낮추지 않으므로 재실행하지 않았다. `ACCURACY PASS`(`compare37.json`). `err_1MHz`는 7케이스 모두 소수점 6자리까지 기준선과 같다. RSS 증가는 cuDSS CUDA 컨텍스트(+약 1.1 GB/프로세스, §11 기록치)이며 패턴 캐시가 아니다.

ΔZ 귀속 — 같은 모델을 1회 build한 뒤 최악 주파수(둘 다 최저점 3.02e4 Hz)에서 4경로를 비교했다(`attribution_260729_P7.log`).

| 260729 Port7 (N 586,481) @ 3.02e4 Hz | rel vs exp28 영수증 |
|---|---|
| Y 비트 동일성(fast vs 기본): indptr / indices / data | **True / True / True** |
| splu + 기본 assemble (기준선 재현) | 3.65e-14 |
| splu + fast assemble | 3.65e-14 |
| cudss + 기본 assemble | 3.280e-08 |
| cudss + fast assemble (영수증 경로) | 3.278e-08 |
| **assemble 기여분**(splu fast − splu 기본) | **0.000e+00** |
| **solver 기여분**(cudss − splu, 같은 Y) | **3.280e-08** |

즉 Port7의 3.28e-08은 **전부 cuDSS 인수분해**이고 assemble 가속의 기여는 정확히 0이다. 최저 주파수에서 계가 가장 나쁜 조건수를 가지므로 7케이스 모두 최악점이 3.02e4 Hz에 나타나며, 편차는 N과 함께 커진다(34k: 4.8e-10 → 586k: 3.3e-08).

### 3-1. cuDSS 반복 개선(IR) — 후속 조치, 채택

원인이 GPU 인수분해로 확정됐으므로 cuDSS의 반복 개선을 켰다. nvmath 1.0에 `DirectSolver.solution_config.ir_num_steps`로 노출돼 있다. 같은 Port7 모델·같은 Y·최악 주파수에서 splu 해 대비(3회 중 최솟값 시간, `ir_test_P7.log`):

| ir_num_steps | rel \|ΔZ\|/\|Z\| vs splu | factor | solve | factor+solve | 추가 시간 |
|---|---|---|---|---|---|
| 0 (기본) | 3.335e-08 | 0.263 s | 0.0203 s | 0.283 s | — |
| **1** | **1.047e-09** | 0.257 s | 0.0539 s | **0.311 s** | **+9.9 %** |
| 2 | 1.381e-10 | 0.262 s | 0.0775 s | 0.339 s | +19.8 % |
| 0 (재확인) | 3.331e-08 | 0.259 s | 0.0299 s | 0.289 s | — |

1스텝이 32배 개선을 +9.9 %(문턱 < 20 %)에 준다. **`cudss_solver.py`의 기본값을 `ir_num_steps = 1`로 설정**(1줄, GENERAL 타입·`free()` 생략 불변). 2스텝은 예산이 필요로 하지 않아 채택하지 않았다.

IR=1로 두 대형 케이스만 재실행한 결과(영수증은 시간 접미사로 추가, 기존 것 보존):

| 케이스 | max \|ΔZ\|/\|Z\| IR=0 → IR=1 | 벽시계 | Σ assemble | Σ factor |
|---|---|---|---|---|
| 260729 Port7 | 3.278e-08 → **1.772e-09** | 882 → 756 s | 5.38 → 5.41 s | 7.2 → 7.3 s |
| 260804 Port18 | 8.255e-09 → **2.953e-10** | 328 → 252 s | 3.29 → 2.75 s | 3.4 → 3.0 s |

포트 전체 시간에서 IR 비용은 측정 한계 이하다(Σ factor P7 7.2 → 7.3 s; 벽시계 감소는 동시 실행 개수가 4 → 2로 줄어든 부하 차이). **7케이스 전부 ≤ 1.8e-09.**

(b) 기본 경로 불변(환경변수 없음, splu):

| 검사 | 결과 |
|---|---|
| `common/smoke_port18.py` | **SMOKE PASS**, rel diff **5.83e-12**, err vs PowerSI 2.69 %, unknowns 261124, 1437 MB |
| `run11.py --variant p --smoke --smoke-baseline exp28:p --tag 260729 --port Port14_SITE0` | **SMOKE PASS**, rel diff **0.00e+00**, unknowns 34424 |

(c) 단위 검사 — Port14, f = 1e6 및 1e8 Hz, Y_fast vs Y_default(CSC):

| f | nnz | indptr/indices | max \|Δdata\| 상대 | assemble 기본 → fast |
|---|---|---|---|---|
| 1e6 | 159,840 | 동일 | **0.000e+00**(비트 동일) | 0.051 → 0.010 s |
| 1e8 | 159,840 | 동일 | **0.000e+00**(비트 동일) | 0.039 → 0.012 s |

`python tools/research-claude/common/fast_assemble.py` → `DEMO PASS`(합성 COO에서 `coo_matrix(...).tocsc()`와 비트 동일).

## 4. 판정 (§2 대비)

- **(a) 정확도 ≤ 1e-8**: **7케이스 전부 성립**(≤ 1.8e-09). 중간 과정: IR 없는 첫 판정에서 Port7(N 586k)만 3.28e-08로 초과했고, 귀속 실험이 원인을 확정했다 — 같은 모델에서 **assemble 기여분 = 0.000e+00**(Y 비트 동일, splu로 풀면 fast·기본 모두 3.65e-14), **cuDSS 기여분 = 3.280e-08**. 즉 초과는 EXP-37이 만든 것이 아니라 **GPU 경로의 기존 허용오차가 최대 포트에서 1e-8을 넘는다는 사실**이 드러난 것이었다. cuDSS 반복 개선 1스텝(§3-1)으로 Port7 1.77e-09, 260804 P18 2.95e-10이 되어 기준을 회복했다. CLAUDE.md §GPU 정책의 "실측 ≤ 5e-9"는 중형 포트(N ≤ 3e5) 기준이었고, N ≈ 5.9e5에서는 IR 없이는 유지되지 않는다 — 이제 IR=1이 기본이므로 문턱은 다시 유효하다.
- **(b) 속도**: P18 주파수당 assemble **2.145 → 0.128 s**(문턱 ≤ 1 s 성립, 17배). 포트 전체 벽시계 **3.3배 단축**(문턱 2배 성립). 7케이스 벽시계 2.9–3.4배, Σ assemble 6–19배. → **성립**.
- **(c) 기본 경로 불변**: smoke 5.83e-12 PASS, `--variant p --smoke` 0.00e+00. → **성립**.
- **채택**: `SPD_PI_FAST=1`을 92포트 sweep·대형 포트의 표준 실행 플래그로 쓴다. 기준선 exp28/p는 그대로 두며 물리·게이트 정의는 불변이다.

## 5. build 가속 제안 (보고만, 미구현)

build가 end-to-end의 84–87 %이고 두 함수가 그 전부다. 둘 다 이미 벡터화된 수치 커널이라 "≤ 40줄의 순수 캐싱/벡터화"에 해당하지 않으므로 계획대로 구현하지 않았다.

1. **`homog.batched_gx` (P18 127.4 s, P14 22.1 s)** — 모든 혼합 20×20 서브타일 창에 대한 배치 CG(최대 600회 반복, 내부 `A()`만 51.1 s). 반복마다 (N,20,20) float64 배열을 5~6회 훑는 순수 numpy다. 창당 400 미지수·수십만 창이므로 **A2000에서 그대로 돌리는 것**(cupy 또는 cuBLAS batched)이 정공법이고, 커널 자체는 elementwise + axis 합이라 이식이 직선적이다. 창 크기가 고정(sub×sub)이므로 배치 차원만 커진다. CPU 쪽 즉시 이득으로는 float32 혼합 정밀 + 창 중복 제거(동일 비트패턴 창의 해시 캐싱; 대형 평면은 완전 채움·완전 공백 외에도 반복 패턴이 많다)가 있으나, 정확도 영향 검증이 필요해 별건이다.
2. **참조 탐색 `model.rasterize` → `matplotlib._path.points_in_path` (P18 99.8 s / 16,093회, P14 15.0 s / 6,331회)** — (layer, net)별 아트워크를 coarse 격자 중심에서 평가한다. 프리미티브 bbox마다 meshgrid를 만들어 점-다각형 포함 판정을 호출하는데, 같은 (layer, net)이 여러 블록·여러 후보 레이어에서 반복 조회된다(`exp1b.TwoSided.mask`의 캐시 키가 블록 단위라 블록이 다르면 재래스터화). **레이어 전체를 한 번 래스터화해 놓고 블록은 슬라이스로 뽑는 것**이 같은 결과를 주면서 호출 수를 수십 배 줄인다. 다만 블록 원점/격자 정렬이 레이어마다 달라 동일성 증명이 필요하다(부동소수 격자 중심 좌표가 정확히 일치해야 한다) → 별건 과제.
3. 그 밖의 build 항목은 P18에서 합계 2.1 s(0.9 %)로 무의미하다.

## 6. 산출물

`WORK_DIR/exp37/`: `profile_Port14.txt`, `profile_Port18.txt`(+ `.prof`, 실행 로그), 가속 경로 영수증 `result_{tag}_{port}_any_p.json` 7건 + IR=1 재실행 2건(`..._p_HHMMSS.json`, 기존 것 보존), `compare37.json`, `attribution_260804_P18.log`, `attribution_260729_P7.log`, `ir_test_P7.log`, `ir_run_P7.log`, `ir_run_804P18.log`, `smoke_port18.log`, `smoke_p14_default.log`, `EXP37_REPORT.md`. 중간 설계의 영수증은 `v1_edge_geom/`, `v2_reduceat/`에 보관(판정에 쓰지 않음). 저장소 사본 `docs/research-claude/2026-09-15/results/exp37/`(계획·보고서·프로파일 txt).
코드: 신규 `tools/research-claude/common/fast_assemble.py`, 수정 `tools/research-claude/exp3/model3.py`(+15줄), `tools/research-claude/exp4/run4.py`(+1줄), `tools/research-claude/common/cudss_solver.py`(IR 1줄 + 주석 4줄). 커밋 없음.
