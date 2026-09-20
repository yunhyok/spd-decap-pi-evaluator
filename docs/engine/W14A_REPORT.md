# W14-a — mesh/solve 경계를 API로 분리 (수치 불변, 파일 이동 없음)

계획: `docs/engine/W14_PLAN_2026-09-20.md` §W14-a(+§배경). 원칙 그대로 — 5개 수치 모듈
(`geometry, homogenise, reference, model, solver`)은 **한 줄도 건드리지 않았다.** 변경은
`api.py`, `__init__.py`, `README.md`, 테스트 1개뿐이다. `tools/engine_studies/`(W14-b, 다른 세션),
`tools/research-claude/`, `docs/handoff/`는 손대지 않았다.

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/api.py` | +64/−6 | `Rail.mesh(options, backend, log) -> MeshedRail`, `MeshedRail`(dataclass: `model`/`options`/`summary` + `solve`/`set_decaps`/`decap_basis`/`release_solver` 위임), `Rail.build`는 `self.mesh(...).model` 얇은 래퍼, 모듈 독스트링 W14-a 단락, `__all__` |
| `src/spd_pi_engine/__init__.py` | +3/−3 | `MeshedRail` 재노출(import, `__all__`) |
| `src/spd_pi_engine/README.md` | +19/−2 | §2 예제를 `mesh()` → `solve()`로(축약형 `build()` 병기), §11 "구조 — mesh 단계와 solve 단계" 신설 |
| `tests/engine/test_w14a.py` | 신규 56줄 | Port14 비트 동일 게이트 + `summary` 검증(테스트 1개, `tmp_path` 안 씀) |
| `docs/engine/W14A_REPORT.md` | 신규 | 이 문서 |

## 2. API

```python
mesh = rail.mesh(opt, Backend(solver="splu", fast=False))   # 빌드 단계만(래스터→균질화→참조면→맵)
mesh.summary      # {'h':200.0,'fh':50.0,'top_h':50.0,'sub':(20,10,10),'unknowns':34424,
                  #  'cells_per_sheet':{'Signal$L12(MAIN_POWER3)':15583, ..., 'Signal$TOP':2332}}
res  = mesh.solve(freqs)                                    # 풀이 단계(주파수별 조립 + LU)
mdl  = rail.build(opt, backend)                             # == rail.mesh(opt, backend).model
```

| 이름 | 시그니처 | 메모 |
|---|---|---|
| `Rail.mesh` | `(options=None, backend=DEFAULT, log=None) -> MeshedRail` | 기존 `build`의 본문 그대로. `options.fine_box`가 `None`이면 레일 자기 박스로 채우는 동작도 동일 |
| `Rail.build` | `(options=None, backend=DEFAULT, log=None) -> Model` | `self.mesh(...).model` **한 줄**. 시그니처·반환형 불변이라 기존 호출자(`cli.py`, 제품 `engine_worker.py`, `apps/*`, `tests/engine/*`)는 수정 없음 |
| `MeshedRail.model/options/summary` | 필드 | `summary`는 요청 복사본이 아니라 **빌드된 `Model`에서 읽은 값**(`mdl.N`, `mdl.sheets[L].cells`) |
| `MeshedRail.solve` | `(freqs, backend=None, **kw) -> Result` | `**kw`는 `Model.solve`의 `verbose`/`want_v` 통과용 |
| `MeshedRail.set_decaps` | `(config, replace=False) -> MeshedRail` | `Model.set_decaps` 위임(재빌드 없음). 체이닝 편의로 `self`를 돌려준다(`Model`은 자기 자신을 돌려줌) |
| `MeshedRail.decap_basis` / `release_solver` | `Model`에 그대로 위임 | |

**판단 1건(작은 것):** `Model.solve`에는 호출별 백엔드 인자가 없다. 그래서
`MeshedRail.solve(backend=...)`는 `self.model.backend`를 **바꿔서 그대로 둔다**(기존 게이트
스크립트들이 하는 `mdl.backend = ...` 대입과 같은 의미). 호출 단위로 되돌리려면
`try/finally`가 필요한데, 그러면 `_cudss` 래치(`decap_basis`가 park/restore 하는 그것)까지
같이 다뤄야 해서 W14-a 범위를 넘는다. 독스트링과 README §11에 명시했다.

## 3. `numerics_id` — 변경 전/후 동일

영수증과 같은 경로(`receipt.numerics_id(opt.reference, flags, mesh_dict(opt), conventions_dict(...))`,
`source_hashes()`가 5개 수치 모듈 바이트를 LF 정규화해 해시)로 `ModelOptions(reference=
"powersi-compatible", flags=FLAGS_P, h=200, fh=50, top_h=50, sub=(20,10,10), fringe=True)`에 대해 계산:

| 시점 | `numerics_id` |
|---|---|
| 변경 전 (HEAD `d495751`) | `27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0` |
| 변경 후 | `27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0` |

**동일.** 5개 모듈 소스 해시도 그대로다(`geometry.py e354e49ad8c76445`, `homogenise.py
9dbf066a88f991be`, `solver.py ee2917998f5b47dc`, `reference.py abecefafa5b655d1`,
`model.py d4e3eed73cd6f263`). `api.py`/`__init__.py`/README는 `numerics_id`의 입력이 아니다.

## 4. 게이트 — 두 경로 비트 동일

`tests/engine/test_w14a.py::test_mesh_then_solve_equals_build_then_solve`
— 260729 Port14_SITE0, CPU `Backend(solver="splu", fast=False)`, `FLAGS_P`, 영수증 격자
(h=200, fh=50, top_h=50, sub=(20,10,10), fringe, `powersi-compatible`), 사다리 3점 `[1e5, 1e6, 1e7]`:

- `rail.build(...).solve(freqs).Z` vs `rail.mesh(...).solve(freqs).Z` → `np.array_equal` **True**,
  **max |ΔZ| = 0.0**(허용치 비교가 아니라 비트 동일).
- `summary["h"] == 200`, `summary["unknowns"] == model.N == built.N == 34424`(exp28/p 영수증의
  `unknowns`와 일치), `cells_per_sheet` 키 = `model.sheets` 키, 전부 > 0.

## 5. 테스트

```
PYTHONUTF8=1 SPD_PI_DATA_DIR=D:\Downloads\examples
SPD_PI_WORK_DIR=D:\Downloads\examples\analysis\claude-2026-09-15\work
SPD_PI_ENGINE_CACHE=...\work\engine_cache
python -u -m pytest tests/engine -q -p no:cacheprovider          # 기본 프로파일, CPU만(--gpu 안 씀)
```
결과: **53 passed, 22 skipped, 1762.6 s(29분 22초)**, exit 0. 수집 75건이고
`--ignore=tests/engine/test_w14a.py`로 세면 74건 — 늘어난 것은 이번 테스트 1건뿐이다(그 1건이
passed로 들어갔으니 변경 전 기준은 52 passed, 22 skipped). skip 22건은 전부 `--slow`/`--gpu` 미지정 때문이다
(GPU는 다른 세션이 쓰는 중이라 `--gpu` 없이 CPU로만 돌렸다). 느린 순서: Port18 1123 s, PCB
Port1 364 s, **W14-a 178 s**, Port14 90 s. 로그
`D:\Downloads\examples\analysis\claude-2026-09-15\work\w14a\pytest_engine_default.log`.

제품 쪽(엔진 import 경로):
```
QT_QPA_PLATFORM=offscreen ... python -u -m pytest tests/test_engine_scenario_mapping.py tests/test_spd_decap_gui_engine.py -q -p no:cacheprovider
```
결과: **12 passed, 2.8 s**.

비용 측정(260729 Port14, CPU splu, fast off): 추출(캐시 히트) 1.1 s, **mesh 90.7 s**,
solve 3점 1.0 s(**0.3 s/점**). 즉 이 레일에서 두 단계의 비용비는 약 300:1이고, W14-b/c가 격자를
두 번 만드는 비용이 어디서 나오는지도 이 숫자가 말해 준다.

## 6. 판단 필요

1. **`test_w14a.py`를 기본 프로파일에 두었다(`slow` 미표시).** 기준은 "약 2분 미만이면 기본,
   아니면 `slow`"였는데 실측이 **단독 143 s(2분 23초), 전체 스위트 안에서 178 s**로 약간 넘는다.
   그래도 기본에 둔 이유: (a) 계획 §W14-a 게이트 (1)(2)가 "`tests/engine`에 추가 + 기본 스위트
   통과"라서 `slow`면 기본 실행에서 skip 되어 게이트가 서지 않는다, (b) 기본 스위트가 이미 29분
   이라 +178 s는 +11 %다, (c) 비용은 전부 **빌드 2회**(빌드 약 90 s, 솔브 3점 합계 1 s)라 주파수를
   줄여도 안 줄어든다 — 두 경로를 각각 빌드하는 것이 테스트의 내용 자체다. 소유자가 반대하면
   `@pytest.mark.slow` 한 줄이면 된다.
2. 그 외 계획 §W14-a에서 벗어난 것 없음. `MeshedRail.solve(backend=...)`의 의미는 §2의 "판단
   1건" 참고(계획서는 `backend=None` 인자만 적고 수명은 적지 않았다).

## 7. 남은 것

- W14-b(격자 민감도)는 `tools/engine_studies/`에서 별도 진행 중이며 이 작업과 파일이 겹치지 않는다.
- W14-c(수렴 관리자)는 W14-b 판정 후. `Rail.mesh`가 그 전제였으므로 W14-c는 `mesh` 두 번 +
  `solve` 두 번의 순수 오케스트레이션으로 짜면 된다(수치 모듈 불변).
