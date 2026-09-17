# EXP-38 보고서 — build 가속 1단계: `homog.batched_gx` 중복 창 제거 + A2000 이식 (물리 변경 없음)

실행 2026-09-17, 사전 등록 `EXP38_PLAN.md`(§1 규칙·§2 기준 불변). 기준선 exp28/p 영수증.
러너 `run11.py --variant p --outdir exp38`(`SPD_PI_SOLVER=cudss SPD_PI_FAST=1`, 4-병렬).
코드 변경은 `tools/research-claude/exp3/homog.py` 한 파일(+89 / −17줄)뿐이다.

## 1. 방법 / 측정

### 1-1. 창 중복 측정 (구현 전)

`batched_gx`를 감싸 한 번의 build에서 들어오는 모든 배치를 덤프하고(`dump_windows.py`),
배치마다 `np.packbits`한 비트패턴으로 `np.unique`했다. 덤프는 `windows_{tag}_{port}.npz`.

| 케이스 | N | batched_gx 호출 | 창 수 | 고유 창 | 중복 비율 |
|---|---|---|---|---|---|
| 260729 Port14 | 34,424 | 20 | 25,864 | 5,783 | **77.6 %** |
| 260729 Port18 | 275,218 | 22 | 204,291 | 11,811 | **94.2 %** |

호출별 중복률은 0 %(창 2개짜리 인터페이스 배치)에서 99.1 %까지 분포하고, **시간을 지배하는
20×20 대형 배치가 특히 높다**(P18 call 13/15: 15,000창 → 348/351 고유, 각각 29 s).
문턱 20 %를 크게 넘으므로 (A)를 구현했다.

### 1-2. 청크와 VRAM

계획서가 걱정한 "6e6 tiles"는 창 수가 아니라 **타일 수**다. `window_conductance(chunk_tiles=6e6)`는
20×20 창이면 한 배치가 15,000창 = 6e6 double = **배열당 48 MB**이므로, 기존 청크를 그대로 GPU에
올려도 된다. 이것이 중요한 이유는 정확도 쪽이다 — 배치 구성을 바꾸면 배치 전체에 걸리는 수렴 판정
`np.all(rn ≤ tol·bnorm)` 때문에 반복 횟수가 달라지고, tol 1e-7에서 겨우 수렴한 창의 G가 최대 1e-7
수준으로 움직일 수 있다. **CPU가 풀었을 배치를 그대로 GPU가 푼다**(§3-1에서 반복 횟수 전부 일치).

실측 VRAM(단일 프로세스, 중복 제거 없이 P18 전 배치): 프로세스 점유 고점 **2,931 MB**(cupy 풀
1,897 MB) — 계획의 ≤ 3 GB 안. 중복 제거를 켜면 GPU가 보는 최대 배치가 P18에서 2,640창으로 줄어
훨씬 작아지고, 4-병렬 7케이스 실행 중 `nvidia-smi` 전체 사용량은 **378 MB**였다. 추가로
`window_conductance` 끝에서 cupy 풀을 반납해(2줄) 시트 사이에 카드를 cuDSS에 돌려준다.

## 2. 변경 (옵트인, 기본 경로 무변경)

| 항목 | 내용 |
|---|---|
| (A) `SPD_PI_FAST=1` | `batched_gx` 진입부에서 `np.packbits`+`np.unique`로 동일 비트패턴 창을 1회만 풀고 `inv`로 되돌린다(6줄). 플래그는 `common/fast_assemble.ON`을 그대로 import. |
| (B) `SPD_PI_SOLVER=cudss` | 커널을 `_gx(W, tol, maxit, face_fix, xp)`로 분리하고 `np.` → `xp.`로 바꿨다. `_gpu()`가 cupy를 1회 확인해 캐시하고, 없거나 장치가 실패하면 로그 1줄 남기고 numpy로 간다. |
| 폴백 | `_run()`이 `MemoryError`(= `cupy.cuda.memory.OutOfMemoryError`)를 잡아 그 배치만 numpy로 다시 푼다 — 8 GB 카드를 4개 build가 공유하기 때문. |
| 정밀도 | float64 유지. float32 혼합 정밀도는 쓰지 않았다. |
| 기본 경로 | 플래그가 없으면 `FAST=False`, `_gpu() is None` → `_gx(..., np)`가 원본과 같은 호출을 같은 순서로 한다. |

**(A)가 비트 동일인 이유**: `_gx`의 모든 연산은 elementwise이거나 축 (1, 2)에 대한 합이다. 즉 창
하나의 CG는 다른 창과 완전히 독립이고, 중복 창은 비트 동일한 궤적을 따른다. 중복을 빼도 *서로 다른*
잔차의 집합이 바뀌지 않으므로 배치 전체 수렴 판정과 반복 횟수가 그대로다. §3-1에서 실측으로 확인했다.

## 3. 검증 표

### 3-1. 단위 검사 — 덤프한 Port14 / Port18 창 집합 (`test_windows.py`)

같은 프로세스에서 4경로를 돌려 덤프 시점의 기본 경로 G와 비교했다(`unit_P14.log`, `unit_P18.log`).

| 창 집합 | 경로 | max \|ΔG\| (절대) | 비트 동일 | Σ 반복 | Σ batched_gx |
|---|---|---|---|---|---|
| Port14 (20호출, 25,864창) | cpu(기본) | 0 | — | 1,158 | 21.84 s |
| | **cpu+dedup** | **0.000e+00** | **True** | **1,158** | 4.00 s |
| | gpu | 1.465e-14 | False | 1,158 | 3.39 s |
| | **gpu+dedup** | **1.465e-14** | False | **1,158** | **1.53 s** |
| Port18 (22호출, 204,291창) | cpu(기본) | 0 | — | 1,348 | 151.93 s |
| | **cpu+dedup** | **0.000e+00** | **True** | **1,348** | 13.96 s |
| | gpu | 1.554e-14 | False | 1,348 | 12.72 s |
| | **gpu+dedup** | **1.554e-14** | False | **1,348** | **2.96 s** |

- **반복 횟수는 42개 호출 전부에서 4경로가 동일하다.** 배치 구성을 바꾸지 않았다는 §1-2의 설계가
  그대로 확인된다.
- (A)는 **비트 동일**(max \|ΔG\| = 0.000e+00) → 채택.
- (B)의 편차는 축 합의 리덕션 순서 차이뿐이며 **1.6e-14**, 계획의 1e-9 기준보다 5자리 여유.
- 속도: Port18에서 151.9 → 2.96 s(**51배**), Port14 21.8 → 1.53 s(14배).

`python tools\research-claude\exp3\homog.py`(`selfcheck_face_fix`) 출력은 플래그 없음 / FAST=1 /
FAST+cudss 세 조건에서 **한 글자도 다르지 않다**:
`G_orig=0.000000000000 G_face_fix=1.428571428572 … maxabsdiff=0.000e+00 SELFCHECK-HOMOG PASS`.

### 3-2. 7케이스 (`SPD_PI_SOLVER=cudss SPD_PI_FAST=1`, 4-병렬, 기준선 exp28/p)

| 케이스 | N | max \|ΔZ\|/\|Z\| | Σ sheet exp28 → exp37 → exp38 | build exp28 → exp37 → exp38 | 벽시계 exp28 → exp38 |
|---|---|---|---|---|---|
| 260729 Port1 | 118,064 | 1.42e-10 | 185.7 → 69.0 → **4.9 s** | 343 → 117 → 153 s | 396 → 160 s |
| 260729 Port7 | 586,481 | 1.59e-09 | 611.3 → 204.9 → **16.3 s** | 2616 → 735 → 2302 s | 2991 → 2328 s |
| 260729 Port14 | 34,424 | 4.42e-11 | 67.1 → 25.3 → **3.2 s** | 132 → 48 → 58 s | 156 → 63 s |
| 260729 Port16 | 334,638 | 2.24e-10 | 603.0 → 231.6 → **11.9 s** | 1245 → 459 → 761 s | 1408 → 776 s |
| 260729 Port18 | 275,218 | 1.31e-10 | 350.7 → 164.4 → **8.9 s** | 765 → 311 → 484 s | 1095 → 497 s |
| 260729 Port19 | 69,205 | 1.33e-09 | 128.9 → 47.1 → **2.6 s** | 345 → 108 → 235 s | 340 → 240 s |
| 260804 Port18 | 275,177 | 5.59e-09 | 363.3 → 125.4 → **7.6 s** | 793 → 242 → 487 s | 1088 → 502 s |

`ACCURACY PASS`(`compare38.json`, 최악 5.59e-09 ≤ 1e-8). `err_1MHz`는 7케이스 모두 소수점 6자리까지
기준선과 같다. `ladder_gates`의 모든 수치도 상대차 ≤ 7.6e-06인데, 이는 ΔL·ΔRe처럼 **차이를 취해
상쇄가 큰 항목**에서 1.6e-09이 상대적으로 증폭된 것이고 게이트 판정은 전부 불변이다.

**`Σ sheet`(시트별 `[sheet]` 로그 시간 합)가 이 실험이 건드린 부분의 직접 측정치다** — 래스터화 +
창 CG + 인터페이스가 들어 있고, exp37 대비 **13–19배**, exp28 대비 **22–50배** 줄었다.

**build / 벽시계가 exp37보다 나쁜 이유(측정 결과, EXP-38 탓이 아님)**: 같은 프로파일 안에서
numpy `batched_gx`는 exp37과 같은 속도(Port14 22.10 → 21.86 s)인데 참조 탐색의
`matplotlib._path.points_in_path`만 같은 호출 수(6,331회)에 **15.04 → 59.65 s**로 4배 느리다.
cupy를 import하지 않는 기본 경로 프로파일에서도 같고, homog를 import하지 않는 맨 프로세스
마이크로벤치도 0.87 Mpts/s다(`env_note.log`). i9-12900H(6P+8E) + 균형 조정 전원 구성표에서 스칼라 C
루프가 E-코어로 스케줄되는 세션 편차로 보인다. **같은 세션 대조 실험**은 다음과 같다(모두 단독 실행):

| Port18 build (단독) | batched_gx | 나머지(대부분 참조 탐색) | build 합계 |
|---|---|---|---|
| 기본 경로(numpy, 플래그 없음) | 157 s | 398 s | **555 s** |
| `cudss` + `FAST=1` | **4.1 s** | 437 s | **441 s** |

| Port14 build (단독) | build 합계 |
|---|---|
| 기본 경로 | **87.8 s** |
| `cudss` + `FAST=1` | **47.2 s** |

### 3-3. 대형 포트 260729 Port49_SITE1 (N 1,259,238, 단독 실행)

| 항목 | exp28 (CPU splu) | exp38 | 배율 |
|---|---|---|---|
| max \|ΔZ\|/\|Z\| | — | **7.29e-10** | — |
| Σ sheet | 1085.3 s | **32.3 s** | **34x** |
| build_seconds | 2133 s (35.6 분) | **1366 s (22.8 분)** | 1.56x |
| Σ assemble (27점) | 112.6 s | 15.0 s | 7.5x |
| Σ factor (27점) | 2120.4 s | 71.2 s | 30x |
| 벽시계 | 4459 s | **1476 s** | **3.0x** |
| peak RSS | 5680 MB | 3954 MB | — |

`err_1MHz` 0.09697916 → 0.09697916(8자리 일치).

### 3-4. 기본 경로 불변 (환경변수 없음, splu)

| 검사 | 결과 |
|---|---|
| `common/smoke_port18.py` | **SMOKE PASS**, rel diff **5.83e-12**, err vs PowerSI 2.69 %, unknowns 261124, 1437 MB, 638 s |
| `run11.py --variant p --smoke --smoke-baseline exp28:p --tag 260729 --port Port14_SITE0` | **SMOKE PASS**, rel diff **0.00e+00**, unknowns 34424 |
| `python tools\research-claude\exp3\homog.py` | 출력 불변, `SELFCHECK-HOMOG PASS` |

## 4. 판정 (§2 기준 대비)

- **(a) 정확도**: (A)는 **G 비트 동일**(두 창 집합, 42배치, max \|ΔG\| = 0.000e+00) → **성립, 채택**.
  (B)는 max \|ΔG\| **1.6e-14** ≤ 1e-9 → **성립**. 7케이스 Z는 exp28/p 대비 최악 **5.59e-09**,
  Port49는 **7.29e-10** — 1e-6 문턱은 물론 GPU 정책의 1e-8도 만족한다(1e-8 초과 없음).
- **(b) 속도**: 이 실험이 건드린 구간은 **Port18 균질화 152 → 3.0 s(51배)**, 케이스별 `Σ sheet`가
  exp37 대비 13–19배·exp28 대비 22–50배다. 계획의 문턱은 build 합계로 적혀 있는데,
  - P18 build ≤ 300 s: **불성립(484 s, 4-병렬 / 441 s, 단독)**. 다만 남은 시간의 **96 %가
    참조 탐색**이고(§5), 그 구간이 이번 세션에 4배 느렸다. 같은 세션 대조로는 555 → 441 s.
  - N 1.3M Port49 build ≤ 20 분: **경계에서 불성립(22.8 분)**, exp28 35.6 분 대비 1.56배 단축.
    벽시계는 4459 → 1476 s로 **3.0배**.
  → **속도 문턱은 build 합계 기준으로는 미달**이지만, 미달분은 전적으로 EXP-37 §5-2가 별건으로
    남겨 둔 래스터 참조 탐색이다. 균질화 자체는 사실상 build에서 사라졌다.
- **(c) 기본 경로 불변**: smoke 5.83e-12 PASS, `--variant p --smoke` 0.00e+00, `selfcheck_face_fix`
  출력 불변 → **성립**.
- **채택**: (A)·(B) 모두 채택한다. 표준 실행 플래그 `SPD_PI_SOLVER=cudss SPD_PI_FAST=1`는 그대로이며
  이제 균질화까지 A2000에서 돈다. 기준선 exp28/p, 물리·게이트 정의는 불변이다.

## 5. 남은 build 항목 — 참조 탐색 `rasterize` → `points_in_path`

EXP-37 §5-2가 별건으로 남긴 항목이 이제 **build의 거의 전부**다. 같은 세션 단독 build 프로파일:

| Port18 build 441 s (cudss + FAST=1) | 시간 | 비중 |
|---|---|---|
| `model.rasterize` (427회) | 429.7 s | **97.4 %** |
|  └ `matplotlib._path.points_in_path` (16,093회) | 423.8 s | **96.1 %** |
| `homog.batched_gx` (22회) | 4.1 s | **0.9 %** |
| 그 밖의 전부 | 약 7 s | 1.6 % |

Port14도 같다: build 47.2 s 중 `points_in_path` 39.8 s(**84 %**), `batched_gx` 2.4 s(5 %).
EXP-37 프로파일에서 build의 84–87 %를 둘이 반씩 나눠 가졌던 구도가, 이제 **참조 탐색 단독**이 됐다.

다음 단계는 EXP-37 §5-2 그대로다: (layer, net)별 아트워크를 **레이어 전체로 1회 래스터화하고 블록은
슬라이스로 뽑는다**. 호출 수가 16,093 → 레이어 수 수준으로 줄어 수십 배가 기대되지만, 블록마다 원점과
격자 정렬이 달라 **부동소수 격자 중심 좌표가 정확히 일치함을 증명**해야 하므로 별건 실험이다.
(참고: 이번 세션의 `points_in_path` 절대 시간은 EXP-37 대비 4배 느렸다 — §3-2의 환경 주석.)

## 6. 산출물

`WORK_DIR/exp38/`:
- 창 통계·덤프: `dump_windows.py`, `dump_P18.log`, `windows_260729_Port14_SITE0.npz`,
  `windows_260729_Port18_SITE0.npz`
- 단위 검사: `test_windows.py`, `unit_P14.log`, `unit_P18.log`, `bench_gx.py`
- 7케이스 영수증 `result_{tag}_{port}_any_p.json` 7건 + `chain0..3.log`, `run7.py`, `run7.log`
- Port49: `result_260729_Port49_SITE1_any_p.json`, `Port49_SITE1.log`, `run49.log`
- 비교: `compare38.py`, `compare38.json`, `compare38.log`
- build 프로파일: `prof_build.py`, `profbuild_P14_default.txt`, `profbuild_P14_gpu.txt`,
  `profbuild_P18_gpu.txt`, `profbuild_P18_gpu.log`
- 기본 경로: `smoke_port18.log`, `smoke_p14_default.log`
- 환경: `env_note.log`
- `EXP38_PLAN.md`, `EXP38_REPORT.md`. 저장소 사본 `docs/research-claude/2026-09-15/results/exp38/`.

코드: 수정 `tools/research-claude/exp3/homog.py`(+89 / −17줄) 단 한 파일. 커밋 없음.

환경: 전역 Python 3.12.10에 `cupy-cuda12x 14.2.0`(+`cupy-cuda12x[ctk]`의 CUDA 12.9 헤더·런타임 휠)
추가 설치. **numpy 2.4.4 / scipy 1.18.0 / cuda-bindings 12.9.8 불변**, nvmath-python 1.0.0 및
nvidia-cudss-cu12 0.8.0.10도 그대로다. 드라이버 528.79(CUDA 12.0)에서 minor-version 호환으로 동작.
`cupy-cuda12x[ctk]` 없이는 NVRTC가 CUDA 헤더를 못 찾아 커널 컴파일이 실패한다.
