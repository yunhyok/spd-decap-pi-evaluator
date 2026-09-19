# SPD→Z(f) 연구 모델의 "PI 계산 엔진" 패키지화 계획 (2026-09-18)

목적: `tools/research-claude/`의 동결 모델 사슬(exp8→exp5→exp4→exp3→exp1, 기준선 exp28/p)을 여러 애플리케이션(decap 배치 최적화, PowerSI 사전 예측, SITE 간 decap 실장 판단)이 호출하는 재사용 엔진으로 포장한다. 엔진은 연구가 진전될 때 독립적으로 업그레이드되고, 그 위의 모든 앱이 함께 정확해진다. 작성 근거: `CLAUDE.md`, `docs/research-claude/2026-09-15/NEXT_SESSION_HANDOFF.md` §1–3·§9–11, `PROGRESS_SUMMARY_2026-09-18.md`, `MODEL_PHYSICS.md`, `DECISIONS.md` D2/D5/D7, 동결 코드 사슬 전체와 `src/spd_decap_pi` 통독.

## 0. 원칙
- 수치를 바꾸지 않는다. "리팩터링"이 아니라 **암묵 상태를 명시 인자로 승격하는 기계적 변환**이며, 단계마다 영수증 재현(§3)이 게이트다.
- 동일 수식이 아니라 **동일 연산 순서**를 유지한다. `ok` 마스크·COO 중복합·상수 접기 순서를 바꾸면 최저 주파수에서 1e-16 → 8e-9로 증폭된다(`common/fast_assemble.py:18-21,38-45`).
- `tools/research-claude/`는 손대지 않는다(연구 재현 경로와 D7 기준선의 증거). 엔진이 통과한 뒤 연구 러너를 엔진 호출로 얇게 바꾸는 것은 선택.

## 1. 타당성 — 가능하다
- 물리·수치는 전부 `Model3.build()/assemble()/solve()`(`exp3/model3.py:271-700`)에 있고, 변형은 생성자 키워드 플래그 16개로 이미 명시화돼 있다(`exp3/model3.py:236-266`). 변형 p = `c_unit_fix=True, homog_face_fix=True`(`exp11/run11.py:63`).
- 실행 스크립트(run8/run11)가 하는 일은 (a) 몽키패치 2개, (b) 캐시 준비, (c) 참조 npz에서 주파수 선택, (d) 영수증 직렬화뿐이다.
- 정확 변환 전례: EXP-37/38/40의 `SPD_PI_FAST=1` 경로(Y·G 비트 동일, smoke 5.83e-12 불변).

### 1-1. 명시 파라미터/엔진 상태로 승격해야 할 결합 (C1–C20)

| # | 종류 | 현재 위치 | 승격 후 |
|---|---|---|---|
| C1 | 몽키패치 | `run4.patch_traces(mode)`가 `M3.copper_surface_impedance`를 모듈 전역에 재바인딩(`exp4/run4.py:128-133`; 호출 `exp5/pipeline.py:118`, `exp8/run8.py:130`, `exp11/run11.py:97`, `common/smoke_port18.py:39`). 소비: `model3.py:563,611,645-646,717` | `ModelOptions.trace_zs_mode`(기본 `"one_sided"`) → 인스턴스 속성 `_zs_fn` |
| C2 | 몽키패치 | `M3.TwoSided = TwoSidedAny`(`exp8/run8.py:131`, `exp11/run11.py:99`, `smoke_port18.py:40`; 기본 심볼 `model3.py:32`, 소비 `:293`) | `ModelOptions.reference = "powersi-compatible" \| "physical-gnd"`(D5) |
| C3 | 사후 속성 주입 | `ts.eps_table/c_all_refs/c_unit_fix`를 생성 뒤 주입(`model3.py:294-295`; `TwoSidedAny.__init__` 시그니처 동결 `exp1b.py:71-76`, 소비 `:150-159,185`) | 참조 탐색기 팩토리, 생성자 인자 |
| C4 | 인스턴스 모드 | `Model4.mode/gnd_scale` 클래스 속성(`run4.py:62-63`), `ModelB.build`가 `self.mode="b"`(`pipeline.py:56`), `run_mode` 재풀이(`run4.py:159-160`); 소비 `zs_plane :65-74`, `edge_z :108` | `ModelOptions.zs_mode`(동결 경로 항상 `"b"`) |
| C5 | 층 이름 하드코딩 | `TWO_SIDED={L14,L25}`·`N_GND=13`(`run4.py:44-45`), `GND_SHEETS`(`model3.py:40-41`), `"Signal$TOP"` 특수 처리(`model3.py:306-309,491-498`), `Stack.is_gnd` 휴리스틱(`exp1/model.py:78-80`). s5m6585는 `Plane$IN43_DGND` 규약 | `DesignConventions`(top_layer_name, gnd_name_rule). TWO_SIDED/N_GND는 p 경로 미사용(`pipeline.py:58-62`) → 삭제 가능 |
| C6 | 과반 규칙 문턱 | `b["h"] >= 200.0`(`pipeline.py:48-49`; exp9는 `min(self.h,200)`) | `ModelOptions.two_sided_majority_h`(200.0) |
| C7 | 모듈 전역 캐시 | `homog._GPU` 프로세스 래치(`homog.py:28-49`, 소비 `:109-119`) | `Backend` 소유 |
| C8 | import 시각 환경 플래그 | `fast_assemble.ON`(`common/fast_assemble.py:33`), `scanline` 재수출(`:58`), `exp1/model.py:45 _SCAN_ON` 동결; 소비 `model3.assemble:621,656`, `edge_z:110`, `model.py:156`, `exp1b.py:113`, `homog.py:101` | `Backend(fast=…)` 함수 인자. **런타임 토글 불가가 현재 진실** → 인자화 후 EXP-37/40 비트 동등성 재확인 |
| C9 | 런타임 환경 플래그 | `SPD_PI_SOLVER`(`model3.py:674`, `homog.py:42`) | `Backend(solver="splu"\|"cudss"\|"auto")` |
| C10 | 모델 은닉 캐시 | `_fa_pat`(`model3.py:621,659`), `_cudss`(`:676-684`, 프로세스 수명 누수), `_fa_tr`(`fast_assemble.py:58-62`), `_fa_weff`(`:74-77`), `_layer_rasters`(`model.py:190`), `TwoSided.cache`(`exp1b.py:107-117`) | `Model`의 명시 필드 |
| C11 | 피클 불가 상태 | `self.map` 클로저(`model3.py:452`), `remap`, `N/P` | 캐시는 추출물(ex)까지만 |
| C12 | import 부작용 | `OUT = work_dir(...)` mkdir(`run8.py:40`, `pipeline.py:33`, `run4.py:42`, `run9.py:44`, `paths.py:62-66`) | 엔진은 import 시 파일시스템을 건드리지 않는다 |
| C13 | 설계 id 하드코딩 | `DESIGNS`(`paths.py:40-44`), `SPD/REF` dict(`pipeline.py:34-35`), `runall15.TAG` | SPD 절대경로 입력; 참조 npz는 선택 비교 입력 |
| C14 | 캐시 파일 규약 | `prepare` pkl 경로 + 260729 특수 처리(`pipeline.py:84-108`), 원자적 쓰기(`:65-80`) | `CacheDir` + 키(SPD sha256·레일·max_layers) |
| C15 | 피클 안 src 객체 | `stackup_layers_obj`(pydantic `StackupLayer`, `extract.py:152`, `domain.py:287`), `models`(`PassiveSubcircuitModel`, `extract.py:273`); 소비 `model3.py:392-395,654,729` | 캐시 v2: 평범한 dict + `.SUBCKT` 원문, 로드 시 재파싱 |
| C16 | src 사설 API | `spd._parse_materials/_parse_layers/_parse_shapes/_parse_padstacks/_parse_partial_circuits/_parse_metadata/_find_line/_line_end/_length_um/_Reporter`(`extract.py:28,98-254`, `exp1b.py:35,44-67`) | 얇은 어댑터 모듈 + 심볼 존재 테스트 |
| C17 | 주파수 격자 결합 | 참조 npz 격자에 스냅(`run8.py:136-139`, `run11.py:272-274`, `pipeline.py:124-129`), LADDER(`pipeline.py:36`) | 임의 주파수 배열; 재현 테스트만 `snap_to=ref_freq` |
| C18 | 참조 의존 보고 | `ladder_gates`(`run3.py:37`), `resonance`(`run_exp1.py:47`), `gates`(`:59`), plot | 게이트·플롯은 검증 툴로; Zref는 영수증 선택 필드 |
| C19 | 포트 열거원 | 참조 npz에서(`runall15.py:41-43`, `run11.py:108-112`) | SPD 포트 블록에서 열거(`extract.py:101-116` 파서 재사용) |
| C20 | decap 구성 | `ex["decaps"]`(`extract.py:255-273`) → `self.dec`(`model3.py:427-433`) → 스탬프(`:654-655`); 프루닝 그래프에 decap 간선(`:446`) | `set_decaps()` 설계 제약(§2-4) |

수치에 영향 가능: C1·C2·C4·C6·C8·C9·C17·C20. 별도 검증 실험 필요: C8(비트 동등성), C20(프루닝). 나머지는 기계적 승격.

## 2. 제안 패키지

### 2-1. 위치 — (a) 같은 저장소 `src/spd_pi_engine/` 권고

| 기준 | (a) 같은 저장소 | (b) 별도 저장소 |
|---|---|---|
| SPD 파서 공유 | `spd_decap_pi._core.io.spd` 그대로. 사설 API(C16) 의존이라 경계를 넘으면 버전 핀 지옥 | 파서 복제 또는 제품을 의존 패키지로(비공개 인덱스 필요) |
| 영수증 고정 | 기준 영수증이 같은 트리에 있음 | 복제·동기화 필요 |
| 여러 앱 | `pip install -e .` 하나 | 앱마다 둘 핀 |
| 버전 관리 | `__version__` + `numerics_id` 해시로 충분 | 저장소 태그(지금 불필요) |
| 나중 분리 | `git subtree split --prefix=src/spd_pi_engine` 한 줄 | 되돌리기 어려움 |

(b)를 택할 경우 소유자 절차: ① `gh repo create yunhyok/spd-pi-engine --private` ② `git subtree split --prefix=src/spd_pi_engine -b engine-split` → push ③ 엔진 `pyproject.toml`(deps numpy/scipy/Pillow/matplotlib, extras `gpu`, `spd`) ④ 파서 문제: (i) 제품에 `spd_decap_pi.spd_parse_api` 공개 모듈 신설 후 git 의존, 또는 (ii) 파서를 엔진으로 옮기고 제품이 엔진 의존(장기적으로 옳으나 제품 테스트 104개 영향) ⑤ Deploy key/CI ⑥ 픽스처 복사(SPD/npz 원본은 복사하지 않고 `SPD_PI_DATA_DIR` 참조).

### 2-2. 구조
```
src/spd_pi_engine/
  __init__.py     # Design, Rail, Model, ModelOptions, Backend, Receipt 재수출 + __version__
  spd_source.py   # C16 어댑터: 포트 열거 + extract() (exp1/extract.py + exp1b.load_layer_shapes)
  geometry.py     # rasterize / raster_image / scanline / fast_layer_mask
  homogenise.py   # exp3/homog.py (face_fix; GPU 분기는 Backend 인자)
  reference.py    # TwoSided / TwoSidedAny 통합
  model.py        # Sheet + Model3/Model4/ModelB → Model 하나 (플래그는 ModelOptions)
  solver.py       # splu / CudssLU + YPattern
  decaps.py       # set_decaps, multiport/close (exp9/run9.py:146-183)
  cache.py        # CacheDir: 추출 캐시 v2, shapes 캐시, 원자적 쓰기
  receipt.py      # 영수증 v1 + numerics_id
  cli.py          # python -m spd_pi_engine solve/ports/sweep/verify
tests/engine/fixtures/   # §3
```
모델 코드 약 1,900줄 → 약 1,500줄 예상.

### 2-3. 공개 API 스케치
```python
from spd_pi_engine import Design, ModelOptions, Backend, FLAGS_P
d = Design.open(r"...\S4LB002-2Para_260729_1_injected.spd"); d.ports(); d.sha256
rail = d.rail("Port18_SITE0", cache=r"...\engine-cache")      # 추출 + 이웃 층 shapes(캐시)
opt = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True, fine_box="ports+1mm")
mdl = rail.build(opt, backend=Backend(solver="auto", fast=True))
res = mdl.solve([1e3, 1e4, 1e5, 1e6, 1e7, 1e8]); res.freq; res.Z; res.breakdown(1e5); res.receipt()
mdl.set_decaps({"C1234": None, "C1235": "GRM155R61A106M"}); res2 = mdl.solve(res.freq)  # 재조립+refactorize만
```
플래그 프리셋: `FLAGS_LEGACY`(전부 False = EXP-8 동결), `FLAGS_P`(D7), `FLAGS_Q`/`FLAGS_PMK`(보존).
영수증 v1 = `run11.run()` 출력(`run11.py:283-295`)에서 참조 의존 필드를 선택화: `engine_version, numerics_id, spd_path, spd_sha256, port, rail, reference_mode, flags{16}, mesh{…}, backend{…}, decap_config_sha256, unknowns, two_sided_layers, plane_C_total_nF, dielectric_used, reference_search{}, freq[], Z_re[], Z_im[], breakdown_100k{}, stats[], wall_seconds, peak_rss_MB, [optional] Zref_re[], Zref_im[], ladder_gates{}, validity{}`. `numerics_id` = sha256(reference_mode + flags + mesh + 수치 모듈 소스 해시).

### 2-4. decap 구성 변경 — 전체 재빌드 없이
- decap은 `assemble()`에서 한 줄로 스탬프된다(`model3.py:654-655`); 이상 GND에서 gnd_node=-1이라 대각 원소 하나 → unmount = 그 대각 기여 0. **Y 희소 패턴 불변** → `YPattern` 값만 재계산, cuDSS refactorize만(구성당 7주파수 약 2초, P18 GPU).
- 1단계(권장): `set_decaps(config)`는 build를 건드리지 않고 `assemble()`만 활성/모델 id를 읽는다. **프루닝은 항상 전(全) 실장 구성으로 한 번 계산하고 고정**(`prune_basis="all_mounted"` 기록). 재빌드 대비 ΔZ를 1포트×3구성으로 측정해 보고.
- 2단계(§6-7 최적화): `exp9/run9.py:146-183`의 `multiport()/close()`(Schur 닫기, Rx=0에서 EXP-8 재현 확인)로 `mdl.decap_basis(freqs).Z(config)`. P18 decap 421개 → 주파수당 422 RHS, npz 최대 86 MB. 구성 수십 개 이상일 때만 이득.

### 2-5. 다중 포트 / GPU
- 다중 포트는 v0.2 이후. 지금은 포트당 1 프로세스 병렬(92포트 23분)로 충분.
- GPU는 `Backend(solver="cudss", fast=True)` 명시 옵션, 기본 `"auto"`(실패 시 splu 폴백). 정확도 계약: CPU 대비 max|ΔZ|/|Z| ≤ 1e-8(반복정제 1단계).

## 3. 수치를 고정하는 재현 테스트
- (i) 260729 Port18, `FLAGS_LEGACY`, splu, fast off, 1 MHz 최근접 1점 → `results/exp8/result_260729_Port18_SITE0_any.json`(저장소에 있음). 판정 rel ≤ 1e-9(기대 5.83e-12), `unknowns` 일치.
- (ii) 변형 p 7케이스 → `WORK_DIR/exp28/result_*_any_p.json`(개당 12–14 KB, 합 약 90 KB → `tests/engine/fixtures/exp28/`에 복사; 타임스탬프 접미사 사본은 정본 아님). 전 주파수 max|ΔZ|/|Z| ≤ 1e-9(CPU) / 1e-8(GPU). CI 기본은 P18+P14, `--slow`에서 7건.
- (iii) PCB s5m6585 1–3포트 → `WORK_DIR/exp30/result_s5m6585_*_any_p.json`(약 10 KB씩). 다른 층 이름 규약으로 C5 회귀를 잡는다.
- 데이터 부재 시 `pytest.skip`; SPD sha256이 영수증과 다르면 fail.
- 데이터 없이 항상 도는 테스트: `fast_assemble.demo()`, `homog.selfcheck_face_fix()`, `run11 --selfcheck-via/--selfcheck-void`, scanline-vs-matplotlib 합성 240개. 픽스처 총량 약 120 KB.

## 4. 작업 분해 (agent-session 단위)

| # | 작업 | 산출물 | 게이트 | 추정 |
|---|---|---|---|---|
| W1 | 순수 계산 코어 이식(geometry/homogenise/reference/solver), C7·C8·C9 승격 | 4 모듈 | 데이터 없는 셀프체크 + EXP-38 창 세트 비트 동일 + EXP-40 maskcheck 8케이스 차이 0 | 1.5 |
| W2 | Model 병합(Model3/4/B → 1클래스), C1–C6 승격 | `model.py`, `ModelOptions` | 테스트 (i) 5.83e-12 | 2 |
| W3 | 추출·캐시(C13–C16·C19): SPD 경로 인자화, 포트 열거, 캐시 v2, 원자적 쓰기, sha256 키 | `spd_source.py`, `cache.py` | (i) + s5m6585 추출, 8×8 동시 쓰기 | 1.5 |
| W4 | API + 영수증 v1 + `numerics_id` + `validity` | `__init__.py`, `receipt.py` | 테스트 (ii) | 1 |
| W5 | 테스트·픽스처, CI 프로파일 | `tests/engine/` | (i)(ii)(iii) | 1 |
| W6 | 문서(README: 재현 방법, 수치 동결 위치, 플래그 표, 정확도 범위, 다음 세션 진입점) | README + CLAUDE.md 절 | 리뷰 | 0.5 |
| W7 | CLI(solve/ports/verify) | `cli.py` | 92포트 드라이버 재현 | 0.5 |
| — | **v0.1 소계** | | | **8** |
| W8 | `set_decaps` 1단계 + 프루닝 규칙 검증 | `decaps.py` | 재빌드 대비 ΔZ 보고 | 1 |
| W9 | decap 스윕 2단계(Schur, exp9 이식) | | 전체 재해석 대비 ΔZ < 0.5 % | 1.5 |
| W10 | 다중 포트 | | | 1.5 |
| W11 | 앱 어댑터(제품 `evaluation.py`에 solver profile `hybrid_plane_pair_v1`) | `profiles.py` | 제품 테스트 104개 무회귀 | 2+ |

순서 근거: W1→W2는 매 세션 끝에 영수증으로 수치 불변을 증명할 수 있는 최소 단위. W3을 W2 뒤에 두는 이유는 캐시 포맷 변경이 기존 5.4 GB pkl을 무효화하기 때문(W2까지는 기존 pkl 읽기 전용 재사용). **W11은 패키지 급전 경로 R 관례가 해결되기 전에는 시작하지 않는다**(D5 모드 노출 방식이 그 결론에 의존). 그때까지 엔진은 "PCB·대형 평면 레일 우선" 범위를 영수증 `validity`에 명시.

## 5. 리스크와 완화
- **5-1 피클 안 src 객체(버전 결합)**: 캐시 v2는 stackup을 dict로, decap 모델은 `.SUBCKT` 원문으로 저장하고 로드 시 재파싱(`extract.py:83-85`). 헤더 `cache_format=2, parser_version, numpy_version`, 불일치 시 재생성.
- **5-2 matplotlib/PIL이 수치의 일부**: `contains_points`(`model.py:34,161`), `PIL.ImageDraw`(`homog.py:23,52-81`). `scanline.py`는 matplotlib 3.10.9 규칙 전사. `matplotlib>=3.10,<3.11`, Pillow 하한 핀 + 합성 테스트 CI. 현재 제품 `pyproject.toml`에 matplotlib·Pillow 미선언 → 엔진화 시 선언.
- **5-3 GPU 스택**: `cuda-bindings 12.*` 핀, cuDSS `free()`/`SYMMETRIC` 크래시, `CUDA_VISIBLE_DEVICES` 비움 금지. 오래 사는 프로세스에서 모델당 cuDSS 핸들 누적(`model3.py:684`) → GPU 경로는 작업자 서브프로세스 기본, 모델/프로세스 상한(예 8). 동시 4 상한(8 GB).
- **5-4 Windows 가정**: `peak_rss_mb` psapi(`paths.py:95-111`), `os.replace` 재시도(`pipeline.py:65-80`), pkl 안 백슬래시 경로. 엔진은 `pathlib`, 절대경로 미저장, psutil 선택.
- **5-5 N≈1.3M 메모리**: `estimate_cost(port)` 제공(`exp30/extract_stats.json` 형태), 앱은 워커 프로세스에서 호출, GPU `--jobs` 4 클램프 승계.
- **5-6 라이선스/공개**: 저장소 proprietary. nvmath-python/cuDSS는 NVIDIA 라이선스 확인 필요, `extras_require["gpu"]` 분리. 엔진 단독 공개는 비권장(파서 내부·PowerSI 관례 추정 포함).
- **5-7 정확도 범위 오해**: 영수증 `validity`(설계 유형, R 결손 배수, 미통과 게이트)를 API 계약에 포함해 앱이 표시.
- **5-8 `SPD_PI_FAST` import-time 동결(C8)**: W1에서 인자화, 게이트는 EXP-40 maskcheck 재현. 어려우면 테스트 프로세스 분리.

## 부록 — 한 문장
동결 사슬은 이미 플래그로 매개변수화돼 있어 엔진화가 가능하다. 실제 일은 몽키패치 2개(C1·C2), 환경 플래그 2개(C8·C9), 설계 하드코딩 4개(C5·C6·C13·C19), 캐시 안 src 객체(C15), 참조 격자 결합(C17)을 명시 인자로 올리는 것이고, 정당성은 이미 있는 영수증 3종(exp8 / exp28-p 7케이스 / exp30 PCB)이 증명한다. 별도 저장소는 지금 이득이 없다.

## 진행 상태 (2026-09-18 마감)
- W1–W5, W7, W8 완료(보고서 `W1..W5_REPORT.md`, `W7_REPORT.md`, `W8_REPORT.md`, `IR_REPORT.md`). W8: `set_decaps` 구성당 2.1 s(P18 GPU), 프루닝 불변 확인, `numerics_id`는 소스 변경으로 `f3808b55…`로 갱신(Z 비트 동일). W6는 `src/spd_pi_engine/README.md` + CLAUDE.md "계산 엔진" 절. v0.1 = 패키지 3,400여 줄 + CLI + `tests/engine`(31건: 데이터 없음 11, 영수증 재현 20).
- 게이트 실측: exp8 P18 legacy 5.829e-12; exp28/p 7케이스 CPU ≤ 1e-9(연구 코드 오늘 실행 대비 비트 동일), GPU ≤ 5.6e-9; PCB Port1 CPU 9.4e-11. strict xfail 2건: PCB Port50 CPU 30 kHz 1.016e-9(한도 1e-9), GPU 1 MHz 1.69e-8(한도 1e-8) — 조건수 기인, 반복 정제로 불변(IR_REPORT).
- 소유자 결정 대기: (1) PCB 저주파 재현 허용치(CPU 2e-9 / GPU 레일별)로 사전 등록해 xfail을 해소할지, (2) 연구 브랜치의 main 병합 여부.
- W9 완료(`W9_REPORT.md`): `DecapBasis` 닫힘 vs 직접 풀이 15/15 ≤ 1.05e-5(문턱 0.5 %), CPU 2.5e-10; P18 기저 98 s, 구성당 0.12 s, 손익분기 42. 기저 경로 GPU 계약 ≤ 1e-6(맨 보드 조건수).
- W10 완료(`W10_REPORT.md`, worktree `engine-w10` 병합 34355ac): 다중 포트 기계장치는 검증됐으나 **세 설계 모두 같은 레일을 공유하는 SPD 포트가 0개**(SITE 짝은 별개 net) — 핀 분할 검증으로 대체(상반성 1e-11, 재단락 2.8e-9). 후속: `MultiModel.solve`의 k회 삼각 풀이를 W9의 다중 RHS 경로로 교체(6줄, `# ponytail:` 표시).
- 남은 항목: W11 제품 통합(패키지 급전 경로 R 관례 해결 후). 그 전에 할 수 있는 것: 앱 시제품(decap 배치 탐색은 `decap_basis` + `Z_many`, SITE 간 실장 판단은 `set_decaps`)을 `src/` 밖 별도 스크립트로 만들어 엔진 API를 검증. W11 제품 통합은 패키지 급전 경로 R 관례 해결 후.

## 결정 기록 (2026-09-18, 소유자)
- E1. 저장소는 PUBLIC으로 유지한다.
- E2. 연구·엔진 브랜치 `claude/lightweight-hybrid-20260915`를 main에 fast-forward 병합한다(수행: `5f790a6` 기준).
- E3. **PCB 저주파 재현 허용치 사전 등록**: 저주파 행렬 조건수 때문에 PCB 레일의 재현 오차가 패키지보다 크다(IR_REPORT, W5 §5). 결과를 본 뒤의 조정이 아니라 레일 유형별 계약으로 고정한다 — PCB 픽스처(`tests/engine/fixtures/exp30`)에 대해 CPU max|ΔZ|/|Z| ≤ 2e-9, GPU f ≥ 1 MHz ≤ 1e-7·전 대역 ≤ 1e-6. 패키지 레일 계약(CPU 1e-9, GPU 1e-8)은 불변. strict xfail 2건은 이 계약으로 해소한다. 이후 새 PCB 포트를 픽스처에 넣을 때도 같은 계약을 적용하며, 이 값을 넘는 결과는 조정 대상이 아니라 조사 대상이다.

## W12. 앱이 드러낸 엔진 요구사항 (2026-09-18 사전 등록, 수치 불변)
근거: `APP_decap_search_REPORT.md` §6, `APP_site_decision_REPORT.md` §7.
- W12-a **기저 경로 GPU 계약 조사**: (1) 같은 입력을 같은 프로세스·별도 프로세스에서 반복해 cuDSS 기저의 실행 간 변동을 측정(비결정성 여부), (2) 다른 GPU 프로세스와 동시 실행 시 변동, (3) 주파수 집합에 따른 100 kHz 오차 차이(W9 5.8e-7 vs A1 2.0e-6). 판정: 변동이 실행 간 재현되지 않으면 계약을 "GPU 기저 ≤ 1e-5, 비결정적"으로 문서화하고 정확 경로로 CPU 기저(`decap_basis(..., backend=Backend("splu", fast=True))`)를 제공하며 P18 CPU 기저 비용을 잰다. 어느 경우든 마스크 판정 일치가 앱의 기능 기준이다(E4).
- W12-b **한 프로세스에서 기저와 직접 풀이 공존**: cuDSS `DirectSolver`를 두 번 만들 때의 실제 조건을 실험으로 확정(W5는 9모델 순차 생성 성공, W9는 "두 번째 생성 시 크래시"라고 기록 — 모순). 가능하면 `Model.release_solver()`(참조 해제, `free()` 호출 없음)와 `decap_basis(backend=…)`로 2단계 프로세스 구조를 없앤다.
- W12-c **API 인체공학(수치 무관)**: `DecapSite`에 `xy, layer, capacitance(1 kHz 기준), impedance(freqs)`; `api.find_site_pair(spd, port)`; `ladder_freqs`·`unique_path`를 공개 API로; `receipt.attach_mask(receipt, mask)`; `Model.set_decaps(cfg, replace=True)`; `DecapBasis.save(..., with_impedances=True)`/`load(path)`가 모델 없이 동작; `Result.receipt(light=True)`(decap_config·reference_search 생략); 기존 동작·영수증 기본 형식은 불변.
- 게이트: `tests/engine` 기본 + `-m gpu` 프로파일 통과, W4/W8/W9 게이트 스크립트 재실행 값 불변(CPU 비트 동일), 두 앱이 새 API로 같은 데모 결과를 재현.

### W12 결과 (2026-09-18)
- W12-a(`W12A_REPORT.md`): cuDSS 기저는 **비결정적**(같은 솔버·플랜으로 두 번 만들어도 2.1e-6, 실행 간 5.7e-7~1.03e-5; 경합 가설 기각). **E4 판정: GPU 기저 ≤ 1e-5·비결정, 정확 경로 = CPU 기저**(P18 27점 552 s, 주파수당 20.5 s; GPU 276–354 s). W9의 "GPU 기저 ≤ 1e-6" 문구는 이 계약으로 대체.
- W12-b: 두 번째 `DirectSolver` 생성은 안전(nrhs 다른 두 솔버 공존·번갈아 풀이 OK), 크래시는 `nvmath.DirectSolver.free()`뿐(크기 무관 예측 불가). 참조 해제는 VRAM을 돌려주지 않음(nvmath 1.0 finalizer 없음; 추가 솔버 비용 P14 40–73 MB, P18 245 MB). `Model.release_solver()`, `decap_basis(freqs, chunk, backend=)`, `set_decaps(cfg, replace=)`, `DecapBasis.save(with_impedances)/load(path)` 추가. 한 프로세스에서 기저+직접 검증 가능(P18 85 s: 빌드 15 + 기저 73 + 직접 4구성).
- W12-c(`W12C_REPORT.md`): `DecapSite.xy/layer/capacitance_F/impedance()`, `Rail.site()`, `find_site_pair`, `match_sites`, 공개 `LADDER/ladder_freqs/unique_path`, `mask_margin/attach_mask`, `Result.receipt(light=True)`.
- 앱 후속(선택): `apps/decap_search`의 2단계 프로세스 구조를 W12-b로 단순화하고 마스크 영수증을 `attach_mask`로 통일; `apps/site_decision`이 `find_site_pair/match_sites/DecapSite.capacitance_F`를 쓰도록 교체.
- W12-a 추가 발견: `OPENBLAS_NUM_THREADS`가 CPU 경로의 비트 동일성을 바꾼다(4 vs 기본값 2.7e-13; 각 설정은 결정적). 허용치 게이트 무관, 비트 비교는 스레드 수 고정 필요(README §5).

## W13·앱 v2·D9 (2026-09-19)
- 소유자 지시 반영: D9 microvia 구리 충전 가정(수치 변경 없음, `validity` 문구 추가), 앱 v2(W12 API로 단순화: decap_search 439→386줄·1프로세스, 결과 동일 — Port18 305/421 sha 동일, PCB 5/7 동일; site_decision 판정 10/10 동일·Z 3.8e-12), W13 하드웨어 프로파일(`hardware.py`: 감지 + 순수 산정 `plan_sweep/plan_basis/plan_threads/plan_chunk_tiles`; 노트북 jobs 4/6, 워크스테이션 32C/64T·512 GB·A6000 프로파일에서 sweep 10(cudss)/15(splu), CPU 기저 워커 15, 92포트 sweep 예상 9–10분; 주파수 병렬 CPU 기저는 비트 동일, P18 147→56 s). `sweep --jobs auto` 기본, `chunk_tiles="auto"`는 옵트인(수치 비중립).
- 드러난 격차: (1) 기저·직접 풀이 백엔드가 다를 때의 계약 문구(README §7에 추가), (2) OpenBLAS 기본 스레드 수에서 CPU 인수분해가 2배 느리고 불안정(워커 env로 고정), (3) A1 CPU-기저+GPU-직접 조합 1.2–1.9e-8은 GPU 직접 계약(1e-8) 경계 — 조사 대상으로 기록.
- 남은 항목: W11 제품 통합. 워크스테이션 실측은 장비 접속 시 `tests/engine/test_hardware.py`의 프로파일 테스트와 92포트 sweep으로 검증.

## W11 착수 (2026-09-19)
- 소유자 지시("다음 단계 진행")로 W11 시작. 계획 `W11_PLAN_2026-09-19.md`(사전 등록): 프로파일 2개 옵트인·기본값 불변, 진입점 3곳 분기, 항상 서브프로세스, CPU 전용 프리즈 빌드, 재현 테스트 ≤1e-9, 제품 테스트 FAILED/ERROR 집합 불변. 미해결 패키지 R 관례는 게이트가 아니라 validity 문구로 처리(소유자 확인 사항).
- W11-a 완료(`W11A_REPORT.md`, 커밋): 프로파일 2개 옵트인, 어댑터/워커, 진입점 분기, 재현 5.858e-12, 제품 테스트 집합 불변, e2e 일치. 진행 중: W11-b(GUI)·W11-c(시나리오 매핑 검증)·W11-d(패키징) 병렬.
