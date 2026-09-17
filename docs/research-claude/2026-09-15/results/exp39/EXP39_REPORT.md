# EXP-39 보고서 — 참조 탐색 래스터화의 레이어 단위 캐시 (물리 변경 없음)

실행 2026-09-17/18, 사전 등록 `EXP39_PLAN.md`(§1 규칙·§2 기준 불변). 기준선 exp28/p 영수증.
러너 `run11.py --variant p --outdir exp39`(`SPD_PI_SOLVER=cudss SPD_PI_FAST=1`, 4-병렬; Port49 단독).
코드 변경은 `tools/research-claude/exp1/model.py`(+41줄)과 `exp1/exp1b.py`(+4 / −1줄) 두 파일뿐이다.

**요약**: 마스크는 8케이스 전 블록에서 **차이 셀 0개**(정확 변환, §2(a) 성립). 그러나 계획 §1이 전제한
"같은 (layer, net, h)의 반복 래스터화"는 **측정으로 기각됐다** — P18에서 그 중복을 전부 없애도 상한이
build의 3 %다. 병목은 중복이 아니라 **정점 10만 개짜리 평면 외곽 다각형 하나**이고,
`points_in_path`가 O(점 수 × 정점 수)라는 데 있다. §2(b) 속도 문턱은 불성립이다(§4, §5).

## 1. 방법 / 측정

### 1-1. 귀속 — 424 s는 어느 호출부인가

같은 세션 단독 build 프로파일(`profbuild39_260729_Port18_SITE0_before.txt`, cudss + FAST=1,
새 캐시만 무력화 = EXP-38 상태). build 395.2 s.

| 호출부 | 호출 수 | cumtime | 비중 |
|---|---|---|---|
| `model3.build` | 1 | 394.8 s | 100 % |
| └ `exp1b.TwoSided.__call__` (블록당 1회) | **9** | 385.1 s | 97.4 % |
| &nbsp;&nbsp;└ `run8.TwoSidedAny.mask` | 548 | 385.0 s | |
| &nbsp;&nbsp;&nbsp;&nbsp;└ `exp1b.TwoSided.mask` | 869 | 385.0 s | |
| &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└ **`model.rasterize`** | **427** | 385.0 s | |
| &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└ **`matplotlib._path.points_in_path`** | **16,093** | **380.1 s** | **96.2 %** |
| `model3.Sheet.__init__` (5 시트) | 5 | 5.3 s | 1.3 % |
| └ `homog.raster_image` (PIL, `rasterize`와 별개 경로) | 9 | 1.11 s | 0.3 % |
| └ `homog.batched_gx` | 22 | 3.4 s | 0.9 % |

즉 **424 s(이 세션 380 s)는 전부 `exp1b.TwoSided.mask` 한 곳**이다. 확인한 다른 래스터화 호출부:

- `model.build_model`의 `rasterize`(model.py:208 / 216) — **build 경로에서 호출되지 않는다**.
  동결 모델은 `model3`을 쓰고 `build_model`은 EXP-1 단독 경로다(프로파일에 부재).
- `run8.py:92 M.rasterize(g, hh)` — `run8.predict()`(`--predict`) 안에만 있다. 영수증 실행 경로가 아니다.
- `exp3/homog.raster_image` + `model3.Sheet` — PIL `ImageDraw`로 서브셀 이미지를 그린다.
  `MplPath`를 쓰지 않으므로 이 실험의 대상이 아니고, P18에서 9회 1.11 s(0.3 %)다.
  (Port49는 19회 18.1 s로 build 1,190 s의 1.5 %.)

### 1-2. 요청 패턴 — 중복이 얼마나 있나 (`probe_requests.py`)

build 1회의 모든 `rasterize` 요청을 (layer, net, h)로 묶었다(`probe_260729_Port18_SITE0.json`,
`probe_260729_Port14_SITE0.json`).

| 케이스 | rasterize 호출 | 서로 다른 (layer, net, h) 키 | 호출 1회인 키 | **중복 제거의 시간 상한** |
|---|---|---|---|---|
| 260729 Port18 | 427 | 370 | 331 (시간의 **95.3 %**) | **10.6 s / 413 s = 3 %** |
| 260729 Port14 | 798 | 490 | 202 (시간의 91 %) | **2.6 s / 51 s = 5 %** |

상한은 Σ_key t_key·(1 − 1/calls_key), 즉 *어떤* (layer, net, h) 캐시로도 넘을 수 없는 값이다.

왜 중복이 없는가: ① 레일 평면마다 후보는 위·아래 3개 도체층뿐이라 레일 레이어들의 후보 집합이 거의
겹치지 않는다. ② coarse 블록(h = 200)과 fine 블록(h = 50)은 h가 달라 애초에 다른 키다.
③ 창이 같은 요청(여러 레일 레이어가 공유하는 fine 블록 등)은 **기존 블록 단위 캐시가 이미 잡고 있다**
— P18에서 mask 호출 869회 → rasterize 427회.

### 1-3. 진짜 비용은 다각형 정점 수다 (`bench_pip.py`)

P18의 `rasterize` 413 s 중 **단 4회 호출이 338 s(82 %)**다(전부 calls = 1):

| layer | net | h | 호출 | 시간 | 블록 평가 점수 |
|---|---|---|---|---|---|
| Signal$L11(MAIN_POWER2) | ADC_VDD_055_VTRIP/0 | 50 | 1 | 106.2 s | 110,665 |
| Signal$L12(MAIN_POWER3) | ADC_VDD_055_VTRIP/0 | 50 | 1 | 100.2 s | 89,645 |
| Signal$L11(MAIN_POWER2) | ADC_VDD_055_VTRIP/0 | 200 | 1 | 69.7 s | 89,741 |
| Signal$L12(MAIN_POWER3) | ADC_VDD_055_VTRIP/0 | 200 | 1 | 62.1 s | 82,873 |

점 10만 개에 100 s — 점당 1 ms다. 원인은 정점 수다. 260729의 아트워크 371개 중 **한 다각형이
102,456개 정점**(Signal$L11, 70.2 × 49.7 mm)이고, `matplotlib._path.points_in_path`는
O(점 수 × 정점 수)다:

| 그 다각형에 대한 `contains_points` | 시간 | 처리율 |
|---|---|---|
| 1,936 점 | 0.55 s | 3.5 kpts/s (0.36 G 점·정점 검사/s) |
| 10,000 점 | 2.91 s | 3.4 kpts/s |
| 49,729 점 | 58.2 s | 0.9 kpts/s |
| 199,809 점 | 245.9 s | 0.8 kpts/s |

**그래서 계획 §1을 조건 없이 적용하면 오히려 크게 느려진다.** 레이어 bbox 전체를 평가하면 P18의 총
평가 점수가 5,115,513 → **88,693,406**(17배), P14는 1,410,120 → **90,976,901**(65배)로 늘고,
증가분은 전부 위의 10만 정점 다각형에 걸린다(그 bbox만으로 h = 200에서 86,800 점 ≈ 25 s,
h = 50에서 1,393,179 점 ≈ 1,700 s). 그래서 구현은 **레이어 래스터가 요청 블록 안에 들어갈 때만**
만든다 — 그때는 블록이 어차피 평가했을 점의 부분집합이라 공짜이고, 이후 블록은 슬라이스로 공짜다.
그 밖은 계획이 규정한 대로 그 블록만 기본 경로로 푼다.

## 2. 변경 (옵트인, 기본 경로 무변경)

| 항목 | 내용 |
|---|---|
| `exp1/model.py` `fast_layer_mask(owner, key, geom, blk)` (+41줄) | (layer, net, h)마다 `rasterize(geom, h)`(= `window=None`, 원점 `floor(bbox/h)*h`)를 1회 만들어 `owner._layer_rasters`에 담고, 블록 마스크를 그 배열의 슬라이스로 돌려준다. 캐시를 만들 수 없거나 슬라이스가 증명되지 않으면 `None`을 돌려주고 호출자가 기본 경로로 간다. |
| 격자 판정 | `(x0_blk − x0_layer)/h`, `(y0_blk − y0_layer)/h`가 1e-9 안에서 정수가 아니면 `None`. |
| 블록이 레이어 래스터 밖으로 나가는 부분 | `np.zeros`로 채운다. 레이어 bbox 밖 셀 중심은 모든 프리미티브 밖이므로 `rasterize(window=blk)`도 False다(§2 정확성). |
| 비용 가드 | 레이어 bbox가 **요청 블록 안에 들어갈 때만** 래스터를 만든다(§1-3). 아니면 `None`. |
| `exp1/exp1b.py` `TwoSided.mask` (+4 / −1줄) | `m = M.fast_layer_mask(...) if FAST_ON else None` → `if m is None:` 아래에 **기존 `M.rasterize(...)` 줄을 글자 그대로** 둔다. 플래그는 `common/fast_assemble.ON`을 그대로 import. |
| `run8.TwoSidedAny.mask` | 변경 없음. `__ANY__` 합집합이 `super().mask`를 net마다 부르므로 위 경로를 그대로 탄다. |
| 기본 경로 | `SPD_PI_FAST`가 없으면 `FAST_ON = False` → `fast_layer_mask`는 호출조차 되지 않고 원래 `rasterize` 줄이 실행된다. 계산에 쓰이는 줄은 한 줄도 바뀌지 않았다. |

**정확 변환인 이유.** 캐시가 담는 배열은 `rasterize(geom, h)` 그 자체이므로 셀 중심이
`floor(bbox/h)·h + (k+0.5)h`다. 블록 원점이 같은 h-격자 위에 있으면 블록의 셀 중심
`x0_blk + (i+0.5)h`는 그 격자의 한 점과 **같은 부동소수 식으로 계산된 같은 값**이 아니라 *다른 식*이므로
변 위 ±1 ulp에서 뒤집힐 수 있다(계획 §1의 우려). 그래서 값이 아니라 **차이 셀 수를 실측**했다(§3-1).
레이어 래스터 밖은 모든 프리미티브의 bbox 밖이라 두 경로 모두 False이므로 0-채움이 정확하다.

## 3. 검증 표

### 3-1. 마스크 동일성 (§2(a), `check_masks.py` / `runchk.py`)

FAST=1 build 중에 캐시가 서비스한 모든 (layer, net, 블록) 요청에 대해 기본 경로 블록 래스터를 함께
만들어 셀 단위로 비교했다(`maskcheck_{tag}_{port}.json`).

| 케이스 | 아트워크 요청(블록) | 캐시 서비스 | (레이어 래스터 생성 / 재사용) | 기본 경로 폴백 | 비교한 셀 | **차이 블록** | **차이 셀** |
|---|---|---|---|---|---|---|---|
| 260729 Port1 | 394 | 16 | 16 / 0 | 378 | 520,076 | **0** | **0** |
| 260729 Port7 | 1,388 | 432 | 155 / 277 | 956 | 38,954,061 | **0** | **0** |
| 260729 Port14 | 798 | 54 | 32 / 22 | 744 | 606,228 | **0** | **0** |
| 260729 Port16 | 446 | 98 | 96 / 2 | 348 | 9,066,691 | **0** | **0** |
| 260729 Port18 | 427 | 16 | 16 / 0 | 411 | 561,905 | **0** | **0** |
| 260729 Port19 | 190 | 8 | 8 / 0 | 182 | 274,712 | **0** | **0** |
| 260804 Port18 | 427 | 16 | 16 / 0 | 411 | 561,905 | **0** | **0** |
| 260729 Port49_SITE1 | 986 | 247 | 89 / 158 | 739 | 20,899,285 | **0** | **0** |
| **합계** | **5,056** | **887** | 428 / 459 | 4,169 | **71,444,863** | **0** | **0** |

폴백 블록은 정의상 기본 경로 그 자체이므로 비교 대상이 아니다. **8케이스 전부 차이 셀 0**이다.

단위 자체 검사 `test_layer_mask.py`(합성 아트워크, 데이터 파일 없음): 포함·걸침·완전 이탈·격자 이탈·
다른 h의 6개 블록에서 슬라이스가 `rasterize(window=blk)`와 배열 동일 → `TEST-LAYER-MASK PASS`.

### 3-2. 7케이스 (`SPD_PI_SOLVER=cudss SPD_PI_FAST=1`, 4-병렬, 기준선 exp28/p)

| 케이스 | N | max \|ΔZ\|/\|Z\| | build exp38 → exp39 | 벽시계 exp28 → exp39 | err_1MHz |
|---|---|---|---|---|---|
| 260729 Port1 | 118,064 | 1.68e-10 | 153 → **146 s** | 396 → 153 s | 0.031540 → 0.031540 |
| 260729 Port7 | 586,481 | 1.60e-09 | 2302 → **2181 s** | 2991 → 2207 s | 0.029101 → 0.029101 |
| 260729 Port14 | 34,424 | 3.89e-11 | 58 → **47 s** | 156 → 51 s | 0.081717 → 0.081717 |
| 260729 Port16 | 334,638 | 3.59e-10 | 761 → **724 s** | 1408 → 738 s | 0.144651 → 0.144651 |
| 260729 Port18 | 275,218 | 8.29e-11 | 484 → **470 s** | 1095 → 484 s | 0.008122 → 0.008122 |
| 260729 Port19 | 69,205 | 1.32e-09 | 235 → **228 s** | 340 → 233 s | 0.308930 → 0.308930 |
| 260804 Port18 | 275,177 | 5.59e-09 | 487 → **440 s** | 1088 → 455 s | 0.065559 → 0.065559 |

`ACCURACY PASS`(`compare39.json`, 최악 **5.59e-09** ≤ 1e-8). `err_1MHz`는 7케이스 모두 소수점
6자리까지 기준선과 같고, `ladder_gates`의 상대차는 ≤ 7.5e-06(exp38과 같은 성격의 차분 증폭)으로
게이트 판정은 전부 불변이다. build가 exp38 대비 3–10 % 짧지만 §3-4가 보여주듯 **이 차이는 캐시가
아니라 세션 변동**이다(같은 세션 단독 대조에서 `points_in_path` 호출 수가 P18에서 완전히 같다).

### 3-3. 대형 포트 260729 Port49_SITE1 (N 1,259,238, 단독 실행)

| 항목 | exp28 (CPU splu) | exp38 | **exp39** |
|---|---|---|---|
| max \|ΔZ\|/\|Z\| vs exp28/p | — | 7.29e-10 | **7.10e-10** |
| build_seconds | 2133 s (35.6 분) | 1366 s (22.8 분) | **1026 s (17.1 분)** |
| 벽시계 | 4459 s | 1476 s | **1131 s** |
| err_1MHz | 0.096979 | 0.096979 | **0.096979** |

### 3-4. 같은 세션 단독 대조 (§3, `prof_build39.py`)

`before`는 새 호출부 하나만 무력화(`model.fast_layer_mask → None`)해 EXP-38 상태를 그대로 재현한다.
같은 세션, 같은 프로세스 설정, 단독 실행, cProfile.

| P18 build (단독, cudss + FAST=1) | build | `model.rasterize` 호출 | `points_in_path` 호출 | `points_in_path` tottime |
|---|---|---|---|---|
| **before** (EXP-38 상태) | 395.4 s | 427 | **16,093** | 380.1 s |
| **after** (EXP-39 캐시) | 361.3 s | 427 | **16,093** | 347.0 s |

**호출 수가 한 건도 줄지 않았다.** P18에서 캐시가 서비스한 16개 요청은 전부 "그 블록이 처음 만든
래스터"라 재사용이 0이고(§3-1), 래스터 1회 + 절약 1회로 정확히 상쇄된다. build 395 → 361 s는 같은
16,093회가 380 → 347 s로 측정된 **세션 변동**이다(EXP-38 §3-2가 기록한 것과 같은 현상).

| Port49 build (단독, cudss + FAST=1) | build | `model.rasterize` 호출 | `points_in_path` 호출 | `points_in_path` tottime |
|---|---|---|---|---|
| **before** | 1287.3 s | 986 | 87,914 | 1217.9 s |
| **after** | 1191.7 s | **828** (−16 %) | **81,176** (−7.7 %) | **1123.4 s** (−7.8 %) |

Port49는 레일 평면이 많아(블록 19개) 후보 레이어가 겹치므로 재사용이 158회 생긴다 —
이것이 이 변경의 **실측 최대 효과**다: 래스터화 호출 −16 %, build −7.4 %.

### 3-5. 기본 경로 불변 (§2(c), 환경변수 없음, splu)

| 검사 | 결과 |
|---|---|
| `common/smoke_port18.py` | **SMOKE PASS**, rel diff **5.83e-12**, err vs PowerSI 2.69 %, unknowns 261124, 1436 MB, 576 s |
| `run11.py --variant p --smoke --smoke-baseline exp28:p --tag 260729 --port Port14_SITE0` | **SMOKE PASS**, rel diff **0.00e+00**, unknowns 34424 |

## 4. 판정 (§2 기준 대비)

- **(a) 정확도**: 8케이스(7 + Port49) **5,056개 블록 요청, 7,144만 셀 비교, 차이 셀 0개** →
  계획 §2(a)의 "차이 셀 수 = 0" 조건을 만족하므로 **정확 변환으로 성립**. 부수적으로 7케이스 Z는
  exp28/p 대비 최악 **5.59e-09**, Port49는 **7.10e-10**으로 1e-8 예산 안이다(이 값들은 EXP-38과
  같은 cuDSS 기여분이며 EXP-39가 만든 것이 아니다 — 마스크가 비트 동일하므로 Y도 동일하다).
- **(b) 속도**: **불성립**.
  - P18 build ≤ 150 s: **470 s(4-병렬) / 361 s(단독)** — 미달. 같은 세션 단독 대조에서
    `points_in_path` 호출 수가 **16,093으로 완전히 동일**하므로 이 변경의 P18 기여는 **0**이다.
  - Port49 build ≤ 10 분: **17.1 분** — 미달. 단독 대조 기준 기여분은 build −7.4 %다.
  - 미달의 원인은 계획 §1의 전제가 틀렸다는 것이다: P18에서 (layer, net, h) 중복을 **전부** 없애도
    상한이 build의 3 %(§1-2)이고, 시간의 82 %는 정점 10만 개 다각형 하나에 대한 **단 4회**의
    래스터화다(§1-3). 계획이 기대한 "호출 수 16,093 → 레이어 수 수준"은 이 코드베이스에서는
    성립할 수 없다(요청 427회 중 370개가 서로 다른 키).
- **(c) 기본 경로 불변**: smoke 5.83e-12 PASS, `--variant p --smoke` 0.00e+00 → **성립**.
- **채택**: 변경 자체는 **정확 변환이고 어떤 케이스에서도 손해가 없으므로 유지**한다(Port49 −7.4 %,
  Port7 재사용 277회). 다만 **EXP-39의 가설은 기각됐고 build 가속은 사실상 미해결**이다. 표준 실행
  플래그 `SPD_PI_SOLVER=cudss SPD_PI_FAST=1`는 그대로다. 기준선 exp28/p, 물리·게이트 정의 불변.

## 5. 남은 항목 — 병목은 `points_in_path`의 O(점 × 정점)

build의 96 %는 여전히 참조 탐색이고, 그 안에서 지배적인 것은 **중복이 아니라 다각형 정점 수**다.
다음 실험(EXP-40)의 후보는 아래 순서로 보인다. 모두 마스크가 바뀔 수 있으므로 별건 사전 등록이 필요하다.

1. **큰 다각형의 점-포함 판정을 O(점 + 정점·행)으로 바꾼다.** 셀 중심은 규칙 격자이므로 다각형을
   y-스캔라인으로 한 번 훑으면 행마다 교차 x를 정렬해 구간을 채울 수 있다. 정점 10만 개짜리 하나에
   대해 현재 1.4 ms/점이 사실상 0이 된다. 단 matplotlib의 채움 규칙(nonzero winding)과 경계 처리를
   **비트 단위로 재현해야** 하므로 동일성 검증(§3-1과 같은 셀 차이 측정)이 실험의 본체가 된다.
2. **`homog.raster_image`의 PIL 경로 재사용.** 같은 아트워크를 이미 PIL `ImageDraw.polygon`으로
   그리고 있고(P18 9회 1.11 s), 비용은 O(면적 + 정점)이다. 다만 PIL은 픽셀 채움 규칙이 달라
   셀 중심 판정과 경계 셀이 어긋난다 — 물리 영향(마스크 차이 셀 수 → ΔZ) 측정이 필요하다.
3. **후보 레이어 아트워크의 사전 단순화**(블록 창 밖 정점 제거/클리핑). 창 밖을 자르면 정점이 크게
   줄지만 클리핑이 경계 셀 판정을 바꾸지 않음을 증명해야 한다.
4. 이 세 가지 중 하나라도 되면 build는 P18 기준 380 s → 수 초 수준이 되고, 그때 비로소
   `batched_gx`(3.4 s)와 `raster_image`(1.1 s)가 다시 보인다.

## 6. 산출물

`WORK_DIR/exp39/`:
- 사전 측정: `probe_requests.py`, `probe_260729_Port14_SITE0.json`, `probe_260729_Port18_SITE0.json`,
  `probe_P18.log`
- 마스크 동일성: `check_masks.py`, `runchk.py`, `maskcheck_{tag}_{port}.json` 8건,
  `chk0..3.log`, `chk_Port49_SITE1.log`, `runchk.log`, `runchk49.log`
- 단위 검사: `test_layer_mask.py`
- 같은 세션 단독 대조 프로파일: `prof_build39.py`,
  `profbuild39_260729_Port18_SITE0_{before,after}.txt`, `profbuild39_260729_Port49_SITE1_{before,after}.txt`,
  `ctl_P18_{before,after}.log`, `ctl_P49_{before,after}.log`
- 영수증: `result_{tag}_{port}_any_p.json` 7건 + `result_260729_Port49_SITE1_any_p.json`,
  `chain0..3.log`, `Port49_SITE1.log`, `run7.log`, `run49.log`, `master.py`, `master.log`
- 비교: `compare39.py`, `compare39.json`, `compare39.log`, `compare39_260729_Port49_SITE1.json`,
  `compare49.log`
- 정점 비용 벤치: `bench_pip.py`, `bench_pip.log`
- 기본 경로: `smoke_port18.log`, `smoke_p14_default.log`
- `EXP39_PLAN.md`, `EXP39_REPORT.md`. 저장소 사본 `docs/research-claude/2026-09-15/results/exp39/`.

코드: 수정 `tools/research-claude/exp1/model.py`(+41줄), `tools/research-claude/exp1/exp1b.py`(+4 / −1줄).
커밋 없음. 환경은 EXP-38과 동일(전역 Python 3.12.10, numpy 2.4.4 / scipy 1.18.0, cupy-cuda12x 14.2.0,
nvmath-python 1.0.0, nvidia-cudss-cu12 0.8.0.10, 드라이버 528.79).
