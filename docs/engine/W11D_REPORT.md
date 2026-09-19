# W11-d 보고서 — CPU 전용 프리즈 빌드 패키징 (2026-09-19)

계획: `docs/engine/W11_PLAN_2026-09-19.md` §5, §6 행 **W11-d**.
저장소: `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator`, 브랜치
`claude/lightweight-hybrid-20260915`, 기준 커밋 `5cbed5a`(커밋하지 않음).
소유 파일만 수정: `SPDDecapPIEvaluator.spec`(gitignore 대상, 아래 §1 참고),
`scripts/build_spd_decap_pi.ps1`, `src/spd_decap_pi/gui_launcher.py`(MPLBACKEND=Agg만),
`README.md`(엔진 프로파일 절), 이 보고서. `gui/**`, `engine_adapter.py`/`engine_worker.py`,
`evaluation.py`/tests는 건드리지 않았다(다른 에이전트가 같은 체크아웃에서 동시 작업 중인 것을
`git status`로 확인함 — `main_window.py`, `engine_adapter.py`, `engine_worker.py`,
`tests/test_spd_decap_gui*.py`가 이 세션 동안 이미 변경돼 있었다).

핵심 결과: 스펙/스크립트를 지시대로 채운 뒤 실제로 프리즈 빌드해 `--engine-worker`까지 돌려보니
**진짜 버그 2건**이 나왔다(둘 다 "정적으로 exclude 목록만 보고는 못 잡고, 실행해봐야 잡히는" 종류).
둘 다 고쳐서 최종적으로 CPU 전용 빌드가 260729 Port14_SITE0을 `solver="auto"`로 풀고 CPU splu
기준 영수증과 **7개 공통 주파수에서 완전히 비트 동일**(상대차 0.0)한 Z를 낸다.

---

## 1. 스펙 · 빌드 스크립트 변경

### 1-1. `SPDDecapPIEvaluator.spec`은 git 추적 대상이 아니다

`.gitignore:18`에 `*.spec`이 있다(`git check-ignore -v` 확인). 게다가 `python -m PyInstaller`를
스펙 파일이 아니라 CLI 인자로 호출하면(빌드 스크립트가 하는 방식) PyInstaller가 **매 빌드마다
`SPDDecapPIEvaluator.spec`을 그 인자들로부터 다시 써버린다**(`INFO: wrote ...\SPDDecapPIEvaluator.spec`
로그가 매번 찍힘). 즉 이 파일은 빌드 산출물이지 손으로 유지하는 소스가 아니다. 그래도 지시대로
스펙과 스크립트를 같은 내용으로 동기화해 두었다 — 다음 빌드가 스크립트의 CLI 인자로부터 스펙을
다시 쓰면 자동으로 계속 일치한다.

### 1-2. 두 파일에 채운 것 (지시 §1)

| 항목 | 값 |
|---|---|
| `--collect-data` | `matplotlib` (mpl-data: 폰트/스타일) |
| `--collect-submodules` | `spd_pi_engine` (기존 scipy array_api 2개에 추가) |
| `--hidden-import` | `matplotlib.path`, `PIL.Image`, `PIL.ImageDraw` (기존 scipy cython 2개에 추가) |
| `--exclude-module` | `tkinter`, `cupy`, `nvmath`, `cuda`, `cupyx` |
| `MPLBACKEND=Agg` | `gui_launcher.py`의 `--engine-worker` 분기, `engine_worker` import 전 (§3) |

`matplotlib.backends`는 **처음에 지시대로 exclude했다가 §2-1의 버그로 되돌렸다** — 최종본에는
없다. 아래 §2에 이유를 자세히 적는다.

### 1-3. 지시에 없던 추가: 소스 파일 7개를 `--add-data`로 번들 (§2-2)

`spd_pi_engine`의 캐시/영수증 버전 관리가 `Path(<module>.__file__).read_bytes()`로 특정
모듈의 **소스 바이트**를 해시하는데, 프리즈 빌드에는 그 `.py` 파일이 실제로 존재하지 않는다.
번들에서 그 경로에 원본 그대로 배치해야 한다(§2-2). 코드는 한 줄도 안 건드리고 패키징만 했다.

```
--add-data src\spd_decap_pi\_core\io\spd.py;spd_decap_pi\_core\io
--add-data src\spd_decap_pi\_core\models\spice.py;spd_decap_pi\_core\models
--add-data src\spd_pi_engine\geometry.py;spd_pi_engine
--add-data src\spd_pi_engine\homogenise.py;spd_pi_engine
--add-data src\spd_pi_engine\solver.py;spd_pi_engine
--add-data src\spd_pi_engine\reference.py;spd_pi_engine
--add-data src\spd_pi_engine\model.py;spd_pi_engine
```

두 파일(spec/ps1)의 diff는 실질적으로 동일한 내용이라 스크립트 쪽만 인용한다
(`scripts/build_spd_decap_pi.ps1` 32-90행이 최종본; 각주로 이유를 코드에 남겨 두었다).

---

## 2. 실행해서 잡은 버그 2건 (지시 "test it")

### 2-1. 버그 A — `--exclude-module matplotlib.backends`가 `import matplotlib` 자체를 깨뜨림

**증상**: `--smoke-test`(GUI)는 통과하는데 `--engine-worker <request.json>`을 돌리면 프로세스가
CPU 0에 가깝게, 메모리도 안 늘고 몇 분이고 살아있기만 했다(진단 과정은 §2-3).
`Start-Process ... -RedirectStandardOutput/-RedirectStandardError`로 실제 파일에 표준출력을
연결해서 다시 돌리자 진짜 원인이 나왔다:

```
excluded module named matplotlib.backends - imported by matplotlib.rcsetup (top-level),
matplotlib.mathtext (delayed), matplotlib.figure (delayed, conditional),
matplotlib.pyplot (top-level)
```
(`build\SPDDecapPIEvaluator\warn-SPDDecapPIEvaluator.txt`)

`matplotlib/__init__.py`(3.10.9) 161행이 무조건 `rcsetup`을 import하고, `rcsetup.py` 26행이
`from matplotlib.backends import BackendFilter, backend_registry`를 **자기 자신의 top level**에서
한다. `matplotlib.backends`를 통째로 exclude하면 `import matplotlib`이 곧바로
`ModuleNotFoundError`로 죽는다 — `spd_pi_engine.geometry`가 `from matplotlib.path import Path`를
모듈 스코프에서 하므로 `spd_pi_engine`을 import하는 순간 터진다.

**고친 방법**: `matplotlib.backends`를 exclude 목록에서 뺐다. `matplotlib/backends/__init__.py`는
`from .registry import BackendFilter, backend_registry`뿐이고, `registry.py`는 자기 top level에서
`enum`/`importlib`만 쓴다 — 실제 GUI 백엔드(Qt/Tk/Agg 구현체)는 `importlib.import_module()`로
**런타임에** 골라 불러온다. 이 앱 코드 경로(제품 + `spd_pi_engine`)에는 `pyplot`도, `plt.*` 호출도,
`FigureCanvas`도 없으므로(grep 확인, `src` 전체에서 `matplotlib.pyplot` 사용처 0건) 그 런타임 경로가
전혀 실행되지 않는다. 되돌린 뒤 재빌드하니 PyInstaller 자체의 "automatic discovery of used
backends" 훅이 `QtAgg`를 골라 `matplotlib.backends._backend_agg`(Agg 래스터라이저 C-extension,
수십 KB)만 추가로 담았다 — `backend_qtagg.py`/`backend_tkagg.py`/`backend_gtk*` 같은 구현 모듈은
번들에 없다(§4-2 확인). `tkinter`는 여전히 완전히 빠져 있다.

### 2-2. 버그 B — `spd_pi_engine`의 캐시/영수증 버전 관리가 소스 `.py` 파일을 직접 읽음

버그 A를 고친 뒤 다시 `--engine-worker`를 돌리자 **똑같은 증상**(CPU 0 근처로 몇 분간 무응답)이
재발했다. 같은 방법(`-RedirectStandardOutput`/`-RedirectStandardError`를 실제 파일로)으로 다시
진단하니 이번엔 실제 예외가 잡혔다:

```
File "spd_pi_engine\cache.py", line 50, in parser_version
File "spd_pi_engine\cache.py", line 48, in h
File "pathlib.py", line 1019, in read_bytes
File "pathlib.py", line 1013, in open
FileNotFoundError: [Errno 2] No such file or directory:
'...\dist\SPDDecapPIEvaluator\_internal\spd_decap_pi\_core\io\spd.py'
```

원인은 `spd_pi_engine/cache.py`의 `parser_version()`(48-50행)과
`spd_pi_engine/receipt.py`의 `source_hashes()`(50-52행)다. 캐시/영수증의 신원(cache key,
`numerics_id`)에 **소스 바이트의 sha256**을 넣는데(컴파일된 바이트코드가 아니라), 그 방법이
`Path(mod.__file__).read_bytes()` / `Path(__file__).parent / n` 이다. `mod.__file__`은 프리즈
빌드에서도 `..._internal\spd_decap_pi\_core\io\spd.py` 같은 그럴듯한 경로 문자열을 주지만, 그
경로에 실제 `.py` 파일은 없다(PyInstaller onedir는 컴파일된 바이트코드를 `PYZ` 아카이브에 담지,
개별 `.py`를 `_internal`에 풀어두지 않는다). 대상은 정확히 7개다:

- `spd_decap_pi/_core/io/spd.py`, `spd_decap_pi/_core/models/spice.py`
  (`cache.py:44-46`의 `spd_io`, `spice`)
- `spd_pi_engine`의 `NUMERIC_MODULES`(`receipt.py:31`): `geometry.py`, `homogenise.py`,
  `solver.py`, `reference.py`, `model.py`

**고친 방법**: 이 7개 파일을 그대로 `--add-data`로 각 모듈의 `__file__`이 가리키는 경로에 번들에
추가했다(§1-3). 코드는 전혀 안 건드렸다 — 같은 소스 바이트가 그대로 들어가므로 해시값이 소스
체크아웃에서 계산한 것과 **비트 동일**하고, 기존(연구/소스 설치로 만든) 엔진 캐시와 영수증이
프리즈 빌드에서도 그대로 유효하다(§4-3의 0.0 상대오차가 그 증거다 — 다른 해시가 나왔다면
`parser_version`이 달라져 캐시 미스가 났을 것이고, `numerics_id`가 달라져 영수증이 기준과
"다른 수치"로 갈렸을 것이다).

### 2-3. 참고 — 이 두 버그는 처음엔 예외가 아니라 "멈춤"으로 보였다

두 번 다 증상이 크래시가 아니라 **CPU 0 근처, 메모리 변화 없이 몇 분이고 살아있는 프로세스**였다.
`SPDDecapPIEvaluator.exe`가 `--windowed`(콘솔 없음) 빌드이고, Python의 `subprocess.run(...,
capture_output=True)`(파이프)나 리다이렉트 없이 `Start-Process`로 띄우면 처리되지 않은 예외가 나도
프로세스가 깔끔하게 종료되지 않는 것으로 보인다. `-RedirectStandardOutput`/
`-RedirectStandardError`로 **실제 파일**에 연결해서 다시 돌리자 그제서야 traceback이 파일에 쓰이고
프로세스가 정상 종료됐다. 이 자체가 별개의 잠재 문제다 — 제품의 `engine_adapter.solve()`는
`subprocess.Popen(..., stdout=subprocess.PIPE, ...)`으로 워커를 띄우므로(§2-2를 고치기 전과 같은
조건), **워커가 실제로 처리되지 않은 예외를 내면 GUI evaluation 호출이 영원히 멈출 수 있다**.
이건 `engine_adapter.py`/`engine_worker.py`(W11-b 소유)의 영역이라 내가 고치지 않고
백그라운드 작업으로 분리해 두었다(`task_9112bac9`, "Fix frozen engine-worker hang on unhandled
exception").

---

## 3. `gui_launcher.py` — `MPLBACKEND=Agg`

```python
if len(sys.argv) > 1 and sys.argv[1] == "--engine-worker":
    os.environ.setdefault("MPLBACKEND", "Agg")
    from spd_decap_pi._core.solver.engine_worker import main as engine_worker_main
    return engine_worker_main(sys.argv[2:])
```

지시대로 `engine_worker` import 전에 설정했다. 실측으로는 §2-1에서 확인했듯 이 앱의 실제 코드
경로가 `matplotlib.pyplot`/백엔드 해석을 전혀 타지 않아 **지금 당장은 이 설정이 아무 것도 바꾸지
않는다**(방어적 조치) — 그래도 향후 `spd_pi_engine`이나 의존 라이브러리가 pyplot을 쓰게 되면
워커가 QApplication 없는 상태에서 GUI 백엔드를 고르려다 실패하는 것을 이 한 줄이 막아준다.

---

## 4. 빌드 실행과 산출물 점검

### 4-1. PyInstaller 확인

`pip show pyinstaller` → **6.20.0**, `pyproject.toml`의 `dev` extra 요구(`pyinstaller>=6.12`)를
이미 만족해 설치하지 않았다. 전역 Python(`C:\...\Python312`)에 `cupy 14.2.0`, `nvmath 1.0.0`이
깔려 있는 것도 확인했다(exclude가 실제로 뭔가를 막아야 하는 상황이라는 뜻).

### 4-2. 빌드 실행

```powershell
$env:SPD_PI_DATA_DIR="D:\Downloads\examples"
$env:SPD_PI_WORK_DIR="D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8="1"
$env:SPD_PI_ENGINE_CACHE="D:\Downloads\examples\analysis\claude-2026-09-15\work\engine_cache"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_spd_decap_pi.ps1 -SkipTests
```

제품 pytest 전체(`-SkipTests` 없이)는 이번 실행에서 돌리지 않았다 — 같은 체크아웃에서 W11-b/c
에이전트가 동시에 코드를 바꾸고 있어(§서두) 그 상태로 전체 스위트를 도는 것은 W11-d 패키징
검증에 필요하지도, 유효하지도 않다고 판단했다. `pip install -e ".[dev]"`와 활성 체크아웃 import
guard는 스크립트 그대로 실행됐다(통과).

세 번 빌드했다(버그 2건을 고치는 과정, §2): 1차 실패(버그 A), 2차 실패(버그 B), 3차 성공.
아래는 **3차(최종, 버그 2건 모두 수정) 빌드**의 실측이다.

| 항목 | 값 |
|---|---|
| 시작 | 2026-09-19T11:00:05.14 |
| 종료 | 2026-09-19T11:02:30.07 |
| 총 wall(pip install + import guard + PyInstaller + 두 smoke gate) | **144.9 s (2분 25초)** |
| PyInstaller 자체(Analysis→PYZ→PKG→EXE→COLLECT) | **119.9 s** (로그 자체 시계) |
| `dist\SPDDecapPIEvaluator` 크기 | **299,316,161 bytes ≈ 285 MiB** |
| 파일 수 | **775** |
| `SPDDecapPIEvaluator.exe`(부트로더, `_internal` 제외) | 23,259,370 bytes ≈ 22.2 MiB |

### 4-3. 번들 내용 점검

```
find dist/SPDDecapPIEvaluator -iregex ".*\(cupy\|nvmath\|cudss\|cuda\|nvidia\).*"
```
→ **0건**(재귀, 대소문자 무시). GPU 스택이 전혀 없다.

```
find dist/SPDDecapPIEvaluator -path "*mpl-data*" | wc -l   -> 209
ls dist/SPDDecapPIEvaluator/_internal/PIL | wc -l          -> 7  (_imaging*.pyd, _webp.pyd 등)
```
matplotlib mpl-data(폰트/스타일)와 PIL 네이티브 확장자 모두 있다.

`matplotlib/backends/`에는 `_backend_agg.cp312-win_amd64.pyd`(+`.pyi`)만 있고
`backend_qtagg.py`/`backend_tkagg.py`/`backend_gtk*`는 없다. `tkinter`/`_tkinter*`는 트리 전체에
0건이다. §1-3의 7개 소스 파일(`geometry.py`, `homogenise.py`, `solver.py`, `reference.py`,
`model.py`, `_core/io/spd.py`, `_core/models/spice.py`)이 정확히 `__file__`이 가리키는 경로에
들어 있는 것도 확인했다.

---

## 5. 프리즈 exe 스모크 게이트

### 5-1. `--smoke-test` (기존 게이트)

```
$smoke = Start-Process -FilePath $builtExe -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
```
ExitCode 0. `gui/app.py`의 `QTimer.singleShot(600, application.quit)`대로 QApplication을 띄웠다
끈다. 최종 빌드에서 통과.

### 5-2. `--engine-worker` CPU 소형 케이스 (신규, 지시 §3-b)

요청(엔진 어댑터의 `EngineSolveRequest.to_json()` 계약 그대로, `engine_adapter.py:203-257`):

```json
{
 "spd_path": "D:\\Downloads\\examples\\S4LB002-2Para_260729_1_injected.spd",
 "spd_sha256": "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2",
 "port": "Port14_SITE0",
 "reference_mode": "powersi-compatible",
 "freqs": [30199.5172040202, 575439.937337157, 1445439.77074593,
           3630780.54770102, 10000000.0, 18800000.0, 100000000.0],
 "cache_dir": "D:\\...\\work\\engine_cache",
 "solver": "auto",
 "configs": {},
 "extra_models": {},
 "threads": null
}
```

`freqs`는 `WORK_DIR\engine_w4\receipt_260729_Port14_SITE0_cpu.json`의 27점 사다리에서 인덱스
`0,5,10,15,20,23,26`(30.2 kHz ~ 100 MHz에 걸치도록)을 그대로 뽑았다 — 값을 그대로 재사용했으므로
"공통 주파수"가 곧 요청한 7점 전부다. `configs`는 비워 뒀다: `engine_worker.py`의
`configs = request.configs or {"as_built": {}}`와 `Model.set_decaps({}, replace=True)`가
`{**as-built 전체, **{}}`로 풀리므로 빈 dict가 "SPD 원본 그대로"를 뜻한다(as-built, decap 35개
연결, 기준 영수증과 동일 구성).

실행 결과(`build_spd_decap_pi.ps1`에 심은 게이트, `[TEMP]\spd_pi_engine_worker_smoke.py`):

```
[engine-worker-smoke] PASS: 7 common frequencies, max relative |dZ|/|Z| = 0.000e+00,
backend.solver='auto' device=None
(device=None => resolved to scipy splu, no cuDSS/cupy in the frozen build)
```

| 게이트 | 기준 | 실측 | 판정 |
|---|---|---|---|
| 예외 없음 / exit code | 0 | 0 | PASS |
| `receipt["backend"]["device"]` | `None` (cuDSS 미사용의 실제 증거) | `None` | PASS |
| `max \|ΔZ\|/\|Z\|` vs `engine_w4/receipt_260729_Port14_SITE0_cpu.json` | ≤ 1e-9 | **0.000e+00** | PASS |
| 공통 주파수 수 | 7 | 7 | PASS |
| 워커 wall(캐시 warm, `fast=True`, 7/27 주파수) | — | **≈ 13 s** | 참고 |

**`backend.solver` 필드에 대한 주의(정확성을 위해 남긴다)**: `receipt.py:303`은
`backend=dict(solver=m.backend.solver, ...)`로 **요청한 값을 그대로** 적는다(`model.py`의
`_gpu_solver`가 cuDSS import에 실패해 splu로 폴백해도 `self.backend`의 `.solver` 필드 자체는
바뀌지 않는다). 그래서 영수증의 `backend.solver`는 문자 그대로 `"auto"`이지 `"splu"`가 아니다.
**실제로 splu로 내려갔다는 증거는 `device is None`이다**(`_gpu_solver`가 `CudssLU`를 실제로
플랜했을 때만 `getattr(m._cudss, "device", None)`가 문자열을 채운다). 이 CPU 전용 빌드에는
`cupy`/`nvmath`/`cuda-bindings`가 전혀 없으므로(§4-3) `model.py:923`의
`SOL.CudssLU(...)`가 `ImportError`로 실패 → `except Exception`에 잡혀 `self._cudss = False` →
`device=None`으로 귀결된다(`model.py:914-929`). 예외 없이 여기까지 왔고 Z가 CPU splu 기준과
비트 동일하다는 것이, "auto가 이 빌드에서 진짜로 splu로 떨어진다"는 실질적 증거다.

`worst=0.0`이 나온 이유: (1) 버그 B를 고쳐 `parser_version()`/`numerics_id()`가 소스 체크아웃과
같은 해시를 내므로 **캐시 히트**(같은 `extract_Port14_SITE0_*.pkl` 재사용), (2)
`engine_worker.run()`은 항상 `Backend(request.solver, fast=True)`를 쓰는데, `fast=True`는
CLAUDE.md §GPU 정책에 적힌 대로 EXP-37 가속으로 **assemble 결과가 비트 동일**하다고 문서화돼
있다(기준 영수증은 `fast=False`로 만들어졌다 — `backend: {solver: splu, fast: false, ...}`) —
이번 실측이 그 비트 동일성 주장을 다시 한번 실증한 셈이다.

### 5-3. 빌드 스크립트에 추가한 게이트 (지시 §3-c)

`scripts/build_spd_decap_pi.ps1`의 `--smoke-test` 확인 직후에 심었다(파일 92-172행 부근).
`$env:SPD_PI_DATA_DIR`가 없으면(이 워크스테이션 밖에서는 항상 그렇다) 다음처럼 건너뛴다:

```
SPD_PI_DATA_DIR not set: skipping the frozen --engine-worker smoke case.
```

참조 영수증(`SPD_PI_WORK_DIR\engine_w4\receipt_260729_Port14_SITE0_cpu.json`)이나 SPD 원본이
없어도 같은 방식으로 SKIP한다(예외로 죽지 않는다).

---

## 6. README

제품 `README.md`에 `## PI 엔진 프로파일(hybrid_plane_pair_v1)` 절을 `## Windows 빌드` 다음에
추가했다(선택 방법 — 현재 CLI `--solver-profile`만, GUI 콤보는 아직 3항목뿐임을 W11A_REPORT
§5-14 그대로 명시 —, SPD 원본 필요/fail-closed, `SPD_PI_ENGINE_CACHE`/`%LOCALAPPDATA%` 캐시
경로, CPU 전용 프리즈 빌드와 `auto`→`splu` 폴백, 소스 설치의 `pip install .[gpu]`, validity 고지).

---

## 7. 남겨둔 것

- `dist\`, `build\`는 `.gitignore`에 이미 있다(`build/`, `dist/` 항목 확인) — 지우지 않고
  그대로 뒀다(285 MiB, 빌드 증거).
- 이 세션 동안 다른 에이전트가 이미 바꾼 파일(`gui/main_window.py`,
  `_core/solver/engine_adapter.py`, `_core/solver/engine_worker.py`,
  `tests/test_spd_decap_gui*.py`)은 전혀 건드리지 않았다.
- (§8에서 직접 고쳤으므로) `task_9112bac9`는 더 이상 필요 없다 — 조정자가 폐기.

---

## 8. 워커 예외 종료 (후속 수정, `gui_launcher.py`만)

§2-3에서 남겨 둔 "프리즈 워커가 처리되지 않은 예외에서 멈춘다"는 문제를 조정자 지시로
직접 고쳤다(소유 파일 `src/spd_decap_pi/gui_launcher.py`만 — `engine_adapter.py`/
`engine_worker.py`는 여전히 손대지 않았다). `task_9112bac9`는 폐기 대상이다.

### 8-1. 무엇을 바꿨나

`gui_launcher.py`의 `--engine-worker` 분기에 두 가지를 추가했다:

1. **표준출력/표준에러 재바인딩** — `--windowed` 빌드는 콘솔도, 부모가 준 실제 핸들도 없으면
   `sys.stdout`/`sys.stderr`가 `None`이다. `_bind_std_stream(stream, fd)`가 `None`일 때만
   `os.fstat(fd)`로 유효성을 먼저 확인한 뒤(부작용 없음) `os.fdopen(fd, "w", encoding="utf-8",
   errors="replace", closefd=False)`로 상속받은 OS 핸들에 다시 연결하고, 핸들이 없거나
   유효하지 않으면 `open(os.devnull, "w")`로 떨어진다. `engine_worker.py`를 import하기 전에,
   `MPLBACKEND` 설정보다도 먼저 한다.
2. **예외 포착과 강제 종료** — `engine_worker_main(...)`(import 포함)을 `try/except
   BaseException`으로 감쌌다. 잡히면 `_fail_engine_worker(request_path, exc)`가
   (a) `<request>.error.json`에 `{"error": repr(exc), "traceback": ...}`을 쓰고(표준출력이
   죽어 있어도 이 파일은 요청 경로만 있으면 항상 시도된다), (b) 표준출력에 `ERROR <repr(exc)>`
   한 줄을 최선을 다해 찍고(`engine_adapter.solve()`의 기존 tail 문자열 매칭과 호환 — 예:
   `ENGINE_SPD_MISSING`이 그대로 부분 문자열로 남는다), (c) `sys.exit()`이 아니라 **`os._exit(1)`**로
   끝낸다 — 인터프리터 종료/atexit 절차를 건너뛴다. 테스트로 확인한 멈춤 현상이 예외 자체가
   아니라 그 뒤(atexit이든 무엇이든)에서 일어난다고 봤기 때문에, 가장 확실한 방법을 썼다.

`SystemExit`도 `BaseException`에 포함되므로 `engine_worker.py`가 이미 쓰던
`raise SystemExit(f"{ENGINE_SPD_MISSING}: ...")` 스타일의 "의도된" 조기 종료도 이제 이
경로로 잡혀 `error.json`을 남긴다 — 코드 문자열이 `repr()` 안에 그대로 남으므로 기존
`engine_adapter.solve()`의 tail 스캔과 계속 호환된다(§8-3 실측 확인).

### 8-2. 왜 멈췄었는지 — 재확인

§2-3에서는 "파이프/무리다이렉트 vs 실제 파일"이 원인처럼 보였는데, 이번 검증(§8-3)에서는
**파이프로도, 무리다이렉트로도 더 이상 멈추지 않는다** — 즉 실제 원인은 "파이프 자체"가 아니라
"처리되지 않은 예외 뒤 인터프리터가 정리(teardown)되는 과정 어딘가"였다는 지시의 가설과
들어맞는다. `os._exit(1)`이 그 정리 과정 전체를 건너뛰므로 근본 원인을 정확히 특정하지 않고도
막힌다.

### 8-3. 검증

**(a) 소스에서, 파이프로(`python -m spd_decap_pi.gui_launcher`)** — 존재하지 않는 SPD 경로로
`ENGINE_SPD_MISSING`을 유도:

```
$ time python -m spd_decap_pi.gui_launcher --engine-worker <bad-request.json> \
      > stdout.log 2> stderr.log
real 0m1.450s
EXIT CODE: 1
stdout: ERROR SystemExit('ENGINE_SPD_MISSING: D:\Downloads\examples\DOES_NOT_EXIST.spd')
<request>.error.json: {"error": "SystemExit('ENGINE_SPD_MISSING: ...')", "traceback": "..."}
```

1.45 s만에 종료, `error.json` 생성 확인 — PASS.

**(b) 프리즈 exe, 파이프로**(`subprocess.run(..., capture_output=True, text=True, timeout=30)`,
이 timeout이 실제로 필요 없었다는 것 자체가 결과다):

```
elapsed: 1.149 s
returncode: 1
stdout: ERROR SystemExit('ENGINE_SPD_MISSING: D:\Downloads\examples\DOES_NOT_EXIST.spd')
```

`engine_adapter.solve()`가 실제로 쓰는 것과 같은 방식(`stdout=PIPE`)이다 — 이 경로가 바로
§2-3에서 처음 멈췄던 경로다. 이제 1.15 s만에 정상 종료. PASS.

**(c) 프리즈 exe, 리다이렉트 전혀 없이**(`Start-Process -Wait`, §2-3의 원래 멈춤 재현 조건):

```
ExitCode: 1  Wall: 00:00:02.07
```

2.07 s만에 종료, `<request>.error.json` 생성 확인 — PASS. 이전에는 이 조건에서 몇 분이고
멈췄다(§2-3 기록: "CPU 0 근처, 메모리 변화 없이 몇 분이고 살아있는 프로세스").

**(d) 회귀 확인 — 정상 케이스가 여전히 통과하는지**: `gui_launcher.py`를 고친 뒤
`scripts\build_spd_decap_pi.ps1`을 다시 빌드해 §5의 두 게이트를 재실행했다.

```
Built and smoke-tested SPD Decap PI Evaluator: ...\SPDDecapPIEvaluator.exe
[engine-worker-smoke] PASS: 7 common frequencies, max relative |dZ|/|Z| = 0.000e+00,
backend.solver='auto' device=None
(device=None => resolved to scipy splu, no cuDSS/cupy in the frozen build)
```

§5-2와 완전히 같은 결과(0.000e+00) — 예외 처리 경로를 추가해도 정상 경로는 그대로다.

| 케이스 | 이전 | 이후 |
|---|---|---|
| 소스, 파이프, 잘못된 요청 | (해당 없음 — 신규 확인) | 1.45 s, exit 1, error.json | 
| 프리즈, 파이프, 잘못된 요청 | 수 분간 멈춤(§2-3) | **1.15 s, exit 1, error.json** |
| 프리즈, 리다이렉트 없음, 잘못된 요청 | 수 분간 멈춤(§2-3) | **2.07 s, exit 1, error.json** |
| 프리즈, 정상 요청(회귀 확인) | PASS, 0.000e+00 | PASS, 0.000e+00 (동일) |

### 8-4. 참고 — `backend.solver`와 마찬가지로, `error.json`의 한계

`_fail_engine_worker`는 `request_path`가 있을 때만(`sys.argv[2]`가 존재할 때만) `error.json`을
쓴다. `--engine-worker`에 인자를 아예 안 준 극단적인 경우는 `error.json` 없이 `ERROR ...` 한 줄과
`os._exit(1)`만 남는다 — `engine_adapter.solve()`는 항상 요청 경로를 만들어 넘기므로 실제
호출 경로에서는 발생하지 않는다.
