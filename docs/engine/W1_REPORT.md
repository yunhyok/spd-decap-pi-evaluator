# W1 — 순수 계산 코어 이식 (`src/spd_pi_engine/`) 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §4 W1, 원칙 §0(수치 불변, 동일 연산 순서,
`tools/research-claude/` 무수정). 2026-09-18.

## 1. 이식한 것

| 새 파일 | 줄 | 원본 | 내용 |
|---|---|---|---|
| `src/spd_pi_engine/__init__.py` | 17 | — | `__version__="0.1.0.dev0"`, `Backend`/`DEFAULT`/3모듈 재수출 |
| `src/spd_pi_engine/backend.py` | 53 | (신규) | `Backend` 데이터클래스 + GPU 래치 |
| `src/spd_pi_engine/geometry.py` | 292 | `exp1/model.py`(`geom_bbox`, `rasterize`, `fast_layer_mask`), `common/scanline.py`(`inside`, `V0=64`, matplotlib 규칙 전사 docstring), `exp3/homog.py`(`raster_image`) | 래스터화 + 스캔라인 |
| `src/spd_pi_engine/homogenise.py` | 206 | `exp3/homog.py`(`batched_gx`, `_run`, `_gx`, `window_conductance`, `cell_edges`, `selfcheck_face_fix`) | 창 컨덕턴스 균질화 |
| `src/spd_pi_engine/solver.py` | 246 | `common/fast_assemble.py`(`YPattern`, `trace_zs`, `weff`, `demo`), `common/cudss_solver.py`(`_mtlayer`, `_device_name`, `CudssLU`, `demo`→`demo_cudss`) | 선형대수 |

`raster_image`는 계획 §2-2대로 `geometry.py`에 두었다(PIL 경로 그대로). PIL 임포트가 균질화에서
빠지고, 래스터화 코드가 한 파일에 모인다.

AST 단위로 원본 함수와 비교한 결과 **바뀐 줄은 아래 배선 줄뿐**이다(총 62줄, 그중 수식 줄 0):
`geom_bbox`/`inside`/`_gx`/`selfcheck_face_fix`/`YPattern`/`trace_zs`/`weff`/`_mtlayer`/
`_device_name`/`raster_image` = 0줄 변경. `rasterize`는 지역 변수 `inside`가 모듈 함수
`inside`를 가리게 되어 `hit`으로 이름만 바꿨다(연산 동일).

## 2. 파라미터 매핑 (C7/C8/C9 → `Backend`)

| 계획 | 연구 코드의 암묵 상태 | 엔진 |
|---|---|---|
| C7 | `homog._GPU` 프로세스 전역 래치(`homog._gpu()`) | `Backend.gpu()` — 인스턴스 래치, `solver in ("cudss","auto")`일 때만 cupy 시도 |
| C8 | `fast_assemble.ON` / `scanline.ON` / `model._SCAN_ON` / `homog.FAST` (import 시각 `SPD_PI_FAST`) | `Backend.fast`, 그리고 `geometry.rasterize(..., fast=False)` / `fast_layer_mask(..., fast=False)`의 명시 인자 |
| C9 | `SPD_PI_SOLVER` 런타임 read (`homog._gpu`, `model3.solve`) | `Backend.solver = "splu"(기본) \| "cudss" \| "auto"` |
| (하드코딩) | `cudss_solver`의 `host_nthreads = 4`, `ir_num_steps = 1` | `Backend.host_nthreads`, `Backend.ir_steps` (`CudssLU(..., backend=…)`) |

`Backend()` = 연구 기본 경로(splu, fast=False, GPU 없음). 모듈 기본 인자는 모두 `DEFAULT`
(= `Backend()`)이므로 인자를 주지 않으면 기본 경로가 그대로 재현된다. 엔진 어디에서도
`os.environ`을 읽지 않는다.

함수 시그니처 변경: `batched_gx/_run/window_conductance/cell_edges(..., backend=DEFAULT)`,
`rasterize/fast_layer_mask(..., fast=False)`, `CudssLU(..., backend=DEFAULT)`.

`pyproject.toml`: dependencies에 `matplotlib>=3.10,<3.11`, `Pillow` 추가(계획 5-2),
optional-dependencies에 `gpu = [nvmath-python, nvidia-cudss-cu12, cuda-bindings==12.*,
cupy-cuda12x]` 추가. 그 외 변경 없음(`packages.find where=["src"]`라 새 패키지는 자동 인식).
`pip install -e .` 후 `import spd_pi_engine` 정상.

## 3. 게이트 결과 (전부 통과)

### G1 데이터 없는 셀프체크
- `homogenise.selfcheck_face_fix()` 출력이 `python tools\research-claude\exp3\homog.py`와
  **바이트 동일**(`diff` 차이 0): `G_orig=0.000000000000 / G_face_fix=1.428571428572 /
  analytic 1.428571428571`, hole `0.852772061239` 양쪽, `maxabsdiff=0.000e+00`, SELFCHECK-HOMOG PASS.
- `solver.demo()`(= `fast_assemble.demo()` 이식): `nnz 2194 of 4000 COO entries, bit-identical to
  coo_matrix(...).tocsc()`, DEMO PASS — 연구본 출력과 동일.
- `solver.demo_cudss()` (GPU 있음): pass0 rel 8.65e-14, pass1 rel 2.89e-14, nnz_LU 82140,
  DEMO PASS on NVIDIA RTX A2000 8GB Laptop GPU.
- `geometry.demo()`(scanline 60 폴리곤): 47,040 cells, differing 0. 추가로 정사각형에 대해
  `rasterize` fast on/off 동일, `raster_image` 400/400.
- 스캔라인 합성 240개: `engine_w1\synth40_engine.py`(EXP-40 `synth40.py` 로직 그대로, 새
  `geometry.inside`를 봄) → **240 polygons, 279,215 cells compared, differing 0**, SYNTH40-ENGINE PASS
  (`engine_w1\synth40_engine.json`).

### G2 EXP-38 창 세트 (`engine_w1\test_windows_engine.py`, 4경로 = Backend 조합)

| 세트 | cpu max\|ΔG\| | cpu+dedup | gpu | gpu+dedup | iters sum (4경로 동일) |
|---|---|---|---|---|---|
| 260729 Port14_SITE0 | 0.000e+00 (bit-identical) | 0.000e+00 (bit-identical) | 1.465e-14 | 1.465e-14 | 1158 |
| 260729 Port18_SITE0 | 0.000e+00 (bit-identical) | 0.000e+00 (bit-identical) | 1.554e-14 | 1.554e-14 | 1348 |

EXP-38 연구 로그(`exp38\unit_P14.log`, `unit_P18.log`)의 숫자와 완전히 일치(콜별 iteration
수까지). 벽시계: P18 cpu 105.7 s → gpu+dedup 1.75 s. 로그 `engine_w1\unit_P14.log`, `unit_P18.log`.

### G3 EXP-40 마스크 동일성 (`engine_w1\check_masks_engine.py`)
새 `geometry.rasterize(..., fast=True)`가 만든 블록 마스크 vs 연구 기본 경로
(`exp1/model.rasterize`, `_SCAN_ON=False` = matplotlib `contains_points`). 요청 열거는 실제 빌드
(`run11.setup(tag, port, "p")`, `SPD_PI_FAST=1`)에서 `model.rasterize`를 감싸 얻었다(= EXP-40과 같은 방식,
연구 파일은 읽기만 함). 빌드는 **엔진이 만든 마스크로 진행**했고 정상 완료(unknowns P14 34,424 /
P18 275,218 — 연구 영수증과 동일).

| 케이스 | requests | scanline polys | mpl polys | cells compared | differing cells |
|---|---|---|---|---|---|
| 260729 Port14_SITE0 | 776 | 886 | 0 | 8,625,570 | **0** |
| 260729 Port18_SITE0 | 427 | 4,429 | 0 | 23,258,305 | **0** |

EXP-40 `maskcheck_260729_Port14_SITE0.json` / `_Port18_SITE0.json`과 네 수치 모두 동일.

### G4 연구 기본 경로 무변경
- `python tools\research-claude\common\smoke_port18.py` → `Z model = +0.708336 -0.411816j mOhm`,
  `rel diff 5.83e-12`, `err vs PowerSI 2.69 %`, wall 225 s, peak RSS 1438 MB, **SMOKE PASS**.
- `git status --short -- tools/research-claude` → 출력 없음(수정 파일 0).

## 4. 계획에서 어긋난 점

1. **C8은 "런타임 토글 불가"가 아니었다.** 계획 §1-1 C8은 런타임 토글 불가를 현재 진실로
   적었는데, 인자화 후에는 같은 프로세스에서 fast on/off를 번갈아 호출해도 비트 동일이다
   (G2의 4경로, G3의 요청당 2회 호출이 그 증거). 프로세스 분리(§5-8 대안)는 불필요했다.
2. **이름 충돌 1건.** `scanline.inside`를 `geometry.inside`로 올리면 `rasterize` 안의 지역
   변수 `inside`와 충돌한다 → 지역 변수를 `hit`으로 개명(연산 불변). 계획에는 없던 항목.
3. **`fast_assemble.MU0`는 죽은 상수**(참조 0건)라 이식하지 않았다.
4. GPU 경로의 `max|ΔG|`는 계획이 말한 1e-13 한도 안이지만 0이 아니다(1.5e-14) — EXP-38과 동일,
   기대대로다.
5. W2 예고: `geometry.fast_layer_mask`의 `owner.__dict__["_layer_rasters"]`와 `solver`의
   `mdl._fa_tr` / `sh._fa_weff`는 C10대로 아직 숨은 캐시다. W2에서 `Model` 명시 필드로 올린다.

## 5. 산출물
- 패키지: `src/spd_pi_engine/{__init__,backend,geometry,homogenise,solver}.py` (814줄)
- 게이트 스크립트/로그: `WORK_DIR\engine_w1\{synth40_engine.py, test_windows_engine.py,
  check_masks_engine.py, synth40_engine.json, maskcheck_engine_*.json, unit_P1*.log,
  maskchk_P1*.log, smoke_port18.log}`
- 이 보고서와 사본: `docs/engine/W1_REPORT.md`
