# EXP-40 보고서 — 규칙 격자용 스캔라인 점-포함 판정 (물리 변경 없음)

실행 2026-09-18, 사전 등록 `EXP40_PLAN.md`(§1 규칙·§2 기준 불변). 기준선 exp28/p.
러너 `run11.py --variant p --outdir exp40`(`SPD_PI_SOLVER=cudss SPD_PI_FAST=1`, 4-병렬; Port49 단독).
코드 변경은 `tools/research-claude/exp1/model.py`(+15 / −5줄)과 새 파일
`tools/research-claude/common/scanline.py`(1개) 뿐이다.

**요약**: matplotlib `point_in_path_impl`의 판정을 그대로 옮긴 스캔라인 평가기로 바꾼 결과
**합성 240개 다각형·실제 8케이스 2억 9,678만 셀에서 차이 셀 0개**(정확 변환, §2(a) 성립),
문제의 102,456-정점 다각형은 h = 50에서 **1,910.3 s → 0.302 s(6,330배)**, 같은 세션 단독 대조로
**P18 build 399.9 s → 13.8 s**, **Port49 build 1,129.2 s → 69.6 s**(§2(b) 성립, 문턱 60 s / 5 분).
기본 경로는 불변(smoke 5.83e-12 PASS, `--smoke` 0.00e+00, §2(c) 성립). EXP-39가 남긴 병목
(`points_in_path` O(점 × 정점))은 해소됐다.

## 1. 방법 — matplotlib 규칙의 재현

### 1-1. 원본 확인

설치본은 matplotlib **3.10.9**다. PyPI sdist `matplotlib-3.10.9.tar.gz`
(sha256 `fd66508e8c6877d98e586654b608a0456db8d7e8a546eb1e2600efd957302358`, PyPI JSON API의
digest와 일치)를 SCRATCH에 받아 `src/_path.h`, `src/py_adaptors.h`, `src/path_converters.h`,
`lib/matplotlib/path.py`를 읽었다. `pip download --no-binary :all:`는 meson 백엔드가 없어
메타데이터 단계에서 실패하므로 sdist를 직접 받았다(빌드하지 않음, 설치본은 손대지 않음).

### 1-2. 호출 경로 — `Path(pts)`가 실제로 무엇으로 평가되는가

- `Path(pts)`는 `codes=None`이고 `vertices = _to_unmasked_float_array(pts)`(= float64)다.
  아트워크 배열은 이미 `<f8`이라 값이 바뀌지 않는다.
- `contains_points(points)`는 radius = 0, transform = None(항등 affine)으로
  `_path.points_in_path`를 부른다. 항등 affine은 `x*1 + y*0 + 0`이라 유한값에 대해 정확히 항등이다.
- `mpl::PathIterator::vertex`(`py_adaptors.h`)는 codes가 없으면 **인덱스 0에 MOVETO, 나머지에
  LINETO**를 돌려주고 끝나면 `path_cmd_stop`이다. 즉 **서브패스가 하나**다.
- `has_codes()`가 false이므로 `PathNanRemover`는 "fast path"를 타고, 정점이 전부 유한하면
  **그대로 통과**시킨다(`path_converters.h:296-318`). 곡선 코드가 없으므로 `conv_curve`도 통과다.
- `points_in_path`는 맨 앞에서 `if (path.total_vertices() < 3) return;` — 정점 3개 미만이면 전부 False다.

### 1-3. `point_in_path_impl`의 판정 (전사)

`_path.h:104-228`. (vtx0, vty0)·(vtx1, vty1)은 방금 읽은 정점보다 **한 칸 뒤처져** 유지되므로,
정점 v0..v_{n-1}에 대해 내부 루프가 v0→v1 … v_{n-2}→v_{n-1}을 처리하고, `path_cmd_stop`이
(x, y)를 저장해 둔 서브패스 시작점 (sx, sy)로 되돌린 뒤 루프를 빠져나와 **닫힘 변 v_{n-1}→v0**을
루프 뒤 블록에서 처리한다. 즉 **정점 n개 = 변 n개**이고, 이것이 "`contains_points`는 경로를 항상
닫힌 것으로 본다"의 실체다. (내부 루프의 첫 반복은 vty0 == vty1 == y0인 v0→v0 퇴화 변이라
straddle이 성립하지 않아 아무 기여도 없다.)

각 변 (vtx0, vty0)→(vtx1, vty1)과 각 시험점 (tx, ty)에 대해:

```
yflag0 = (vty0 >= ty)                                  // 비엄격 >=
yflag1 = (vty1 >= ty)                                  // 비엄격 >=
if (yflag0 != yflag1)                                  // 수평 변·길이 0 변은 여기서 저절로 탈락
    if (((vty1 - ty) * (vtx0 - vtx1) >= (vtx1 - tx) * (vty0 - vty1)) == yflag1)
        subpath_flag ^= 1                              // even-odd 교차 누적
inside_flag |= subpath_flag                            // 서브패스별 OR
```

- **부호를 풀면**: yflag1이 참(위로 가는 변, vty0 < ty ≤ vty1)일 때 `tx <= xcross`,
  거짓(아래로 가는 변)일 때 `tx < xcross`. 이 비대칭이 두 다각형이 공유하는 변을 한 번만 세게 한다.
- **연산 순서**: 뺄셈 2회 + 곱셈 1회를 각각 반올림한 뒤 `>=` 한 번. 이 모듈은 **이 식을 그대로
  계산**하고 `xcross = x1 + (ty-y1)*(x0-x1)/(y0-y1)`를 쓰지 않는다. 나눗셈 형태는 실수 산술에서는
  같은 질문이지만 반올림이 달라, 셀 중심이 변 위에 정확히 얹히는 경우에 1 ulp 차이로 뒤집힌다.
  계획 §1이 요구한 "부동소수 연산 순서까지 그대로"는 곱셈 형태를 유지해야만 만족된다.

### 1-4. 행 단위 축약 — 왜 값 하나로 줄여도 되는가

고정된 변과 행에서 `fl(vtx1 - tx)`는 tx에 대해 단조(정확 반올림된 단조 함수)이고, 거기에 고정
double `(vty0 - vty1)`을 곱한 것도 단조다. 따라서 위 술어는 tx에 대해 단조이고, **오름차순 xc에서
술어가 참인 칸은 언제나 접두(prefix)**다. 그래서 (행, 변)마다 그 접두 길이 k 하나만 있으면 된다.

k는 **술어 자체를 이분 탐색**해서 구한다(나눗셈 없음 → 위 1-3의 비교가 비트 단위로 보존된다).
그다음 열 i의 교차 수는 k > i인 변의 개수, 즉 k 히스토그램의 접미 합이고, 홀수면 내부다.

비용: straddle 판정 O(행 × 변) + 교차 쌍 O(P × log nx) + 누적 O(행 × 열).
`contains_points`는 O(행 × 열 × 변)이다.

## 2. 변경 (옵트인, 기본 경로 무변경)

| 항목 | 내용 |
|---|---|
| **신규** `common/scanline.py` | `inside(pts, xc, yc)` — 위 규칙의 numpy 구현. 행을 청크로 끊어 `(행 × 변)` 불리언을 8 M개 이하로 유지하고, `np.nonzero`로 straddle 쌍만 뽑아 이분 탐색 → `np.bincount` → 역누적합 → 패리티. 모듈 docstring에 §1-2/1-3/1-4 전사가 그대로 들어 있다. `demo()` 자체 검사 포함. |
| `V0 = 64` | 정점 64개 미만 다각형은 기존 `MplPath.contains_points` 호출 유지. **속도 전용 상수**이며 결과를 보고 조정하지 않았다(정점이 적으면 matplotlib 호출이 이미 싸다). |
| `None` 반환 → 폴백 | 비유한 정점(matplotlib이라면 `PathNanRemover`의 느린 경로), 오름차순이 아닌 `xc`, 정점 3개 미만·형상 불일치. 실제 8케이스에서 폴백은 **0건**이었다(§3-3). |
| `exp1/model.py` `rasterize` (+9 / −5줄) | 다각형이고 `SPD_PI_FAST=1`이고 정점 ≥ V0이면 `scanline.inside`, 그 외에는 **기존 `X, Y = np.meshgrid …` 블록을 글자 그대로** 실행한다. 원 프리미티브 식은 손대지 않았다. |
| `exp1/model.py` 상단 (+6줄) | `sys.path`에 `../common`을 넣고 `import scanline`, `_SCAN_ON = scanline.ON`(= `fast_assemble.ON`). |
| 기본 경로 | `SPD_PI_FAST`가 없으면 `_SCAN_ON = False` → `scanline.inside`는 호출조차 되지 않고 원래 meshgrid + `contains_points` 줄이 실행된다. 계산에 쓰이는 줄은 한 줄도 바뀌지 않았다. |
| EXP-39 `fast_layer_mask` | 변경 없음. 그 경로가 만드는 레이어 래스터도 같은 `rasterize`를 타므로 함께 빨라진다. |

## 3. 검증 표

### 3-1. 합성 검사 (§2(a)(1), `synth40.py` / `synth40.json`)

무작위 격자(원점·간격·크기 무작위, 1/4은 셀 중심이 정수·반정수에 떨어지도록 고정)에서
240개 다각형을 `MplPath.contains_points`와 비교했다.

| 계열 | 개수 | 설명 |
|---|---|---|
| convex | 40 | 각도 정렬 볼록 |
| concave | 40 | 반지름 무작위 오목 |
| selfint | 40 | 정점 순서를 섞은 **자기교차** |
| staircase | 40 | 축 정렬 계단 — **수평 변**이 변의 절반 |
| on_lattice | 40 | 정점의 x·y를 **격자 중심 값에 정확히** 얹음 |
| degenerate | 40 | on_lattice + **중복 정점**(전 정점 2배) + **공선 중점** |
| **합계** | **240** | **279,215 셀 비교 → 차이 셀 0** |

`scanline.demo()`(모듈 자체 검사, 60개 다각형 47,040 셀)도 차이 0 → `DEMO PASS`.

### 3-2. 문제의 다각형 그 자체 (§2(a)(2), `bigpoly40.py`)

`Signal$L11(MAIN_POWER2)` / `ADC_VDD_055_VTRIP`, **102,456 정점**, bbox 70.2 × 49.7 mm.
격자는 `rasterize(window=None)`이 만드는 것과 동일하다(`floor(bbox/h)*h` 원점, 중심 `+(k+0.5)h`).
**h = 200, h = 50 모두 bbox 전체를 끝까지 돌렸다**(sub-window 불필요).

| h | 격자 | 셀 | 내부 셀 | matplotlib | 스캔라인 | 배속 | **차이 셀** |
|---|---|---|---|---|---|---|---|
| 200 µm | 352 × 249 | 87,648 | 72,977 | **94.94 s** | **0.094 s** | 1,009× | **0** |
| 50 µm | 1,403 × 994 | 1,394,582 | 1,168,872 | **1,910.31 s** | **0.302 s** | **6,330×** | **0** |

(EXP-39 §1-3의 추정 25 s / 1,700 s보다 실측이 크다 — 추정은 소규모 벤치의 외삽이었다.)

### 3-3. 실제 build의 마스크 동일성 (§2(a), `check_masks.py` / `runchk.py`)

EXP-39는 새 호출부 하나만 감쌌지만, EXP-40은 `rasterize` 자체를 바꾸므로 **`rasterize` 호출을
전부 두 번** 돌렸다 — 한 번은 스캔라인(FAST build), 한 번은 `model._SCAN_ON = False`로 끈
**기본 경로 그대로** — 뒤 결과는 버리고 셀 단위로 비교했다(`maskcheck_{tag}_{port}.json`).

| 케이스 | `rasterize` 요청 | 스캔라인이 푼 다각형 | matplotlib 폴백 | 비교한 셀 | **차이 블록** | **차이 셀** |
|---|---|---|---|---|---|---|
| 260729 Port1 | 394 | 2,669 | 0 | 14,524,846 | **0** | **0** |
| 260729 Port7 | 1,111 | 18,077 | 0 | 114,722,572 | **0** | **0** |
| 260729 Port14 | 776 | 886 | 0 | 8,625,570 | **0** | **0** |
| 260729 Port16 | 444 | 8,737 | 0 | 40,208,372 | **0** | **0** |
| 260729 Port18 | 427 | 4,429 | 0 | 23,258,305 | **0** | **0** |
| 260729 Port19 | 190 | 1,611 | 0 | 5,736,449 | **0** | **0** |
| 260804 Port18 | 427 | 3,892 | 0 | 23,258,305 | **0** | **0** |
| 260729 Port49_SITE1 | 828 | 14,232 | 0 | 66,444,589 | **0** | **0** |
| **합계** | **4,597** | **54,533** | **0** | **296,779,008** | **0** | **0** |

"비교한 셀"은 요청된 래스터 전체 크기의 합이다(EXP-39 표는 캐시가 서비스한 블록만 셌으므로
숫자 성격이 다르다). 폴백 0건 = 정점 ≥ 64인 다각형은 하나도 빠짐없이 새 경로로 풀렸다.

### 3-4. 7케이스 영수증 (`SPD_PI_SOLVER=cudss SPD_PI_FAST=1`, 4-병렬, 기준선 exp28/p)

| 케이스 | N | max \|ΔZ\|/\|Z\| | build exp39 → exp40 | 벽시계 exp28 → exp40 | err_1MHz |
|---|---|---|---|---|---|
| 260729 Port1 | 118,064 | 1.53e-10 | 146 → **11.4 s** | 396 → **19 s** | 0.031540 → 0.031540 |
| 260729 Port7 | 586,481 | 1.52e-09 | 2,181 → **48.4 s** | 2,991 → **77 s** | 0.029101 → 0.029101 |
| 260729 Port14 | 34,424 | 2.52e-11 | 47 → **7.3 s** | 156 → **12 s** | 0.081717 → 0.081717 |
| 260729 Port16 | 334,638 | 3.01e-10 | 724 → **23.6 s** | 1,408 → **46 s** | 0.144651 → 0.144651 |
| 260729 Port18 | 275,218 | 4.38e-11 | 470 → **17.3 s** | 1,095 → **33 s** | 0.008122 → 0.008122 |
| 260729 Port19 | 69,205 | 1.25e-09 | 228 → **5.0 s** | 340 → **13 s** | 0.308930 → 0.308930 |
| 260804 Port18 | 275,177 | 5.59e-09 | 440 → **15.6 s** | 1,088 → **33 s** | 0.065559 → 0.065559 |

`ACCURACY PASS`(`compare40.json`, 최악 **5.59e-09** ≤ 1e-8). `err_1MHz`는 7케이스 모두 소수점
6자리까지 기준선과 같고, `ladder_gates` 상대차는 ≤ **7.1e-06**(exp38/39와 같은 성격의 차분 증폭)으로
게이트 판정은 전부 불변이다. Z의 잔차는 EXP-38부터 있던 cuDSS 기여분이며 EXP-40이 만든 것이
아니다 — 마스크가 비트 동일하므로 Y도 동일하다(exp39와 최악값 5.59e-09까지 같다).

### 3-5. 대형 포트 260729 Port49_SITE1 (N 1,259,238, 단독 실행)

| 항목 | exp28 (CPU splu) | exp39 | **exp40** |
|---|---|---|---|
| max \|ΔZ\|/\|Z\| vs exp28/p | — | 7.10e-10 | **7.31e-10** |
| build_seconds | 2,133 s (35.6 분) | 1,026 s (17.1 분) | **50.0 s** |
| 벽시계 | 4,459 s | 1,131 s | **155 s** |
| err_1MHz | 0.096979 | 0.096979 | **0.096979** |

### 3-6. 같은 세션 단독 대조 (§3, `prof_build40.py`)

`before`는 새 분기 하나만 끈다(`model._SCAN_ON = False`) — 그러면 `rasterize`는 원래
`MplPath.contains_points` 줄을 그대로 실행하므로 EXP-39 상태 그 자체다. 같은 세션, 같은 프로세스
설정, 단독 실행, cProfile.

| 260729 Port18 build (단독, cudss + FAST=1) | build | `rasterize` 호출 | `points_in_path` 호출 | `points_in_path` tottime | `scanline.inside` 호출 | `scanline.inside` tottime / cumtime |
|---|---|---|---|---|---|---|
| **before** (EXP-39 상태) | **399.9 s** | 427 | 16,093 | **385.9 s** | 0 | — |
| **after** (EXP-40) | **13.8 s** | 427 | **11,664** | **0.3 s** | **4,429** | 0.50 s / **1.01 s** |

| 260729 Port49_SITE1 build (단독) | build | `rasterize` 호출 | `points_in_path` 호출 | `points_in_path` tottime | `scanline.inside` 호출 | `scanline.inside` tottime / cumtime |
|---|---|---|---|---|---|---|
| **before** | **1,129.2 s** | 828 | 81,176 | **1,059.5 s** | 0 | — |
| **after** | **69.6 s** | 828 | **66,944** | **1.5 s** | **14,232** | 2.30 s / **4.40 s** |

남은 `points_in_path` 호출은 전부 정점 < 64인 작은 다각형이며 합쳐서 P18 0.3 s / Port49 1.5 s다.
`rasterize` 전체는 P18 4.56 s, Port49 22.5 s(cumtime)로 내려왔다.

### 3-7. 기본 경로 불변 (§2(c), 환경변수 없음, splu)

| 검사 | 결과 |
|---|---|
| `common/smoke_port18.py` | **SMOKE PASS**, rel diff **5.83e-12**, err vs PowerSI 2.69 %, unknowns 261124, 1436 MB, 551 s |
| `run11.py --variant p --smoke --smoke-baseline exp28:p --tag 260729 --port Port14_SITE0` | **SMOKE PASS**, rel diff **0.00e+00**, unknowns 34424 |

## 4. 판정 (§2 기준 대비)

- **(a) 정확도**: **성립**.
  - 합성 240개 다각형(볼록·오목·자기교차·수평 변·중복/공선 정점·격자 중심에 얹힌 정점 포함)
    279,215 셀 → **차이 0**.
  - 102,456-정점 다각형 bbox 전체, h = 200과 h = 50 → 1,482,230 셀 **차이 0**.
  - 실제 build 8케이스 4,597개 `rasterize` 요청, **2억 9,678만 셀 → 차이 블록 0, 차이 셀 0**.
  - 계획 §2(a)의 "차이 셀 수 = 0" 조건을 만족하므로 **정확 변환으로 채택**. ΔZ 조항(0이 아닐 때의
    대안 판정)은 적용되지 않는다.
- **(b) 속도**: **성립**.
  - P18 build ≤ 60 s: 같은 세션 단독 대조 **399.9 s → 13.8 s**. 4-병렬 영수증 기준 470 → **17.3 s**.
  - Port49 build ≤ 5 분: 단독 **1,129.2 s → 69.6 s**(1.16 분). 영수증 기준 1,026 → **50.0 s**.
  - 계획이 겨눈 병목(`points_in_path` O(점 × 정점))은 P18에서 385.9 s → 0.3 s로 사라졌다.
- **(c) 기본 경로 불변**: smoke 5.83e-12 PASS, `--variant p --smoke` 0.00e+00 → **성립**.
- **채택**: 정확 변환이고 어떤 케이스에서도 손해가 없으므로 유지한다. 표준 실행 플래그
  `SPD_PI_SOLVER=cudss SPD_PI_FAST=1`는 그대로다. 기준선 exp28/p, 물리·게이트 정의 불변.

## 5. 남은 항목 — build는 더 이상 병목이 아니다

EXP-39가 지목한 참조 탐색(`exp1b.TwoSided.mask` → `rasterize`)은 P18 build의 96 % → **33 %**,
Port49는 **32 %**로 내려왔다. `after` 프로파일 기준 남은 비용은 이렇다.

| 항목 | P18 (build 13.8 s) | Port49 (build 69.6 s) |
|---|---|---|
| `model.rasterize` (전체) | 4.56 s | 22.5 s |
| └ `scanline.inside` | 1.01 s | 4.40 s |
| └ `points_in_path`(정점 < 64) | 0.3 s | 1.5 s |
| └ `np.meshgrid` + 원 판정 + 마스크 합성 | 나머지 약 3.2 s | 나머지 약 16.6 s |
| `homog.raster_image`(PIL `draw_polygon`) | 1.18 s | **18.3 s**(그중 13.5 s가 PIL) |
| `homog.batched_gx`(균질화 CG) | 2.89 s | 10.7 s |
| `model3.build` 자체(tottime) | 1.59 s | 4.52 s |

다음 후보는 이 순서로 보인다. 모두 마스크가 바뀔 수 있으므로 별건 사전 등록이 필요하다.

1. **Port49의 `homog.raster_image`(PIL) 18.3 s**가 이제 단일 최대 항목이다. 19,763회의
   `ImageDraw.polygon` 호출이며 서브셀 이미지용이라 `rasterize`와는 다른 경로다. 스캔라인을
   서브셀 해상도로 재사용할 수 있는지(그러면 PIL의 픽셀 채움 규칙과 달라진다 — 물리 영향 측정 필요)
   또는 호출을 창 단위로 묶을 수 있는지가 관건이다.
2. **`rasterize`의 남은 3.2 s / 16.6 s**는 이제 다각형 판정이 아니라 프리미티브마다 만드는
   `np.meshgrid`와 마스크 OR/AND-NOT다(P18 30,435회, Port49 121,761회의 meshgrid). 스캔라인은
   meshgrid가 필요 없으므로 다각형 분기에서는 만들지 않도록 이미 피했고, 남은 것은 원 프리미티브와
   V0 미만 다각형 몫이다. `xc`/`yc` 브로드캐스트로 바꾸면 줄지만 원 판정 식의 부동소수 결과가
   바뀌지 않음을 보여야 한다.
3. **`batched_gx`** 2.89 s / 10.7 s. EXP-38에서 이미 CuPy로 옮겼고, 지금은 `_gx`의 CPU 잔여분
   (P18 1.17 s, Port49 6.27 s)이 남아 있다.
4. build가 P18 13.8 s / Port49 69.6 s가 된 지금, 7케이스 벽시계 77–19 s의 대부분은 다시 **풀이**다
   (Port7 77 s 중 build 48 s, Port49 155 s 중 build 50 s). 다음 가속 대상은 build가 아니라
   주파수별 인수분해 쪽으로 옮겨간다.

## 6. 산출물

`WORK_DIR/exp40/`:
- 합성 검사: `synth40.py`, `synth40.json`, `synth40.log`
- 문제 다각형: `bigpoly40.py`, `bigpoly40_h200.json`, `bigpoly40_h50.json`,
  `bigpoly40_h200.log`, `bigpoly40_h50.log`
- 마스크 동일성: `check_masks.py`, `runchk.py`, `maskcheck_{tag}_{port}.json` 8건,
  `chk0..3.log`, `chk_Port49_SITE1.log`, `runchk.log`, `runchk49.log`
- 같은 세션 단독 대조 프로파일: `prof_build40.py`,
  `profbuild40_260729_Port18_SITE0_{before,after}.txt`,
  `profbuild40_260729_Port49_SITE1_{before,after}.txt`,
  `ctl_P18_{before,after}.log`, `ctl_P49_{before,after}.log`
- 영수증: `run7.py`, `result_{tag}_{port}_any_p.json` 7건 +
  `result_260729_Port49_SITE1_any_p.json`, `chain0..3.log`, `Port49_SITE1.log`,
  `run7.log`, `run49.log`, `master40.py`, `master40.log`
- 비교: `compare40.py`, `compare40.json`, `compare40.log`,
  `compare40_260729_Port49_SITE1.json`, `compare49.log`
- 기본 경로: `smoke_port18.log`, `smoke_p14_default.log`
- `EXP40_PLAN.md`, `EXP40_REPORT.md`. 저장소 사본 `docs/research-claude/2026-09-15/results/exp40/`.

코드: 신규 `tools/research-claude/common/scanline.py`, 수정 `tools/research-claude/exp1/model.py`
(+15 / −5줄). 커밋 없음. 환경은 EXP-39와 동일(전역 Python 3.12.10, numpy 2.4.4 / scipy 1.18.0,
matplotlib 3.10.9, cupy-cuda12x 14.2.0, nvmath-python 1.0.0, nvidia-cudss-cu12 0.8.0.10,
드라이버 528.79). numba/cython 미사용(계획 §1).
