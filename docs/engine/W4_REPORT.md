# W4 — 공개 API + 영수증 v1 (`src/spd_pi_engine/{api,receipt}.py`) 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §2-3(API 스케치)·§2-4·§3(테스트 ii)·§5-5·§5-7,
원칙 §0(수치 불변, `tools/research-claude/` 무수정). 2026-09-18.

## 1. 산출물

| 파일 | 줄 | 내용 |
|---|---|---|
| `src/spd_pi_engine/api.py` | 151 | `Design` / `Rail` / `DecapSite` — W1–W3 위의 얇은 껍데기. 수치 없음 |
| `src/spd_pi_engine/receipt.py` | 296 | 영수증 v1, `numerics_id`, `Result`, G1–G5 게이트(`exp3/run3`+`run8`+`exp1/run_exp1` 이식), `attach_reference`, `validity` |
| `src/spd_pi_engine/__init__.py` | 40 | 재수출(+11줄) |
| `src/spd_pi_engine/model.py` | 943 | `solve()`가 `Result`를 돌려주도록 +3줄(§4 이탈 2). 수치 줄 무변경 |

패키지 합계 3,148줄(W1 4모듈 797 + W2 2모듈 1,235 + W3 2모듈 629 + W4 2모듈 447 + `__init__` 40).
`backend.py`는 손대지 않았다(다른 에이전트가 `ir_steps` 기본값 작업 중).

게이트 스크립트·영수증: `WORK_DIR\engine_w4\{w4_gate.py, receipt_*.json, gate_*.json, logs\}`.

## 2. API 레퍼런스

```python
from spd_pi_engine import Design, ModelOptions, Backend, FLAGS_P, attach_reference

# ---- Design: SPD 파일 하나. open()은 아무것도 읽지 않는다(지연) ----------------------
Design.open(spd_path) -> Design
Design.path            -> pathlib.Path
Design.sha256          -> str                  # 캐시 키·영수증 provenance (cached_property)
Design.ports()         -> list[str]            # SPD .Port 블록 순서 (C19)
Design.rail(port, cache_dir, max_layers=3, conventions=None, log=None) -> Rail

# ---- Rail: 추출된 레일 하나 = 모델 입력 + 앱이 빌드 전에 묻는 것 ----------------------
Rail.ex / Rail.shapes / Rail.fine_box          # spd_source.prepare의 반환 3종
Rail.prepare_seconds   -> float
Rail.spd_path / Rail.spd_sha256 / Rail.port    # provenance (영수증이 읽는다)
Rail.rail_net          -> str
Rail.decaps            -> list[DecapSite]      # ex["decaps"] (cached_property)
Rail.n_nodes / Rail.n_vias / Rail.n_traces -> int
Rail.estimate_cost()   -> dict                 # 계획 §5-5, exp30/extract_stats.json 형태
Rail.build(options: ModelOptions | None = None, backend=DEFAULT, log=None) -> Model

DecapSite(refdes, part, model_id, model_source, node, gnd_node, gnd_net, usage)   # frozen dataclass

# ---- Model: W2의 Model 그대로. build는 Model.build를 부른다 --------------------------
Model.solve(freqs, verbose=True, want_v=False) -> Result
Model.breakdown(f, V) / Model.split_breakdown(f, V)     # 기존 그대로

# ---- Result --------------------------------------------------------------------------
Result.freq -> np.ndarray[float]   Result.Z -> np.ndarray[complex]   Result.stats -> list[dict]
Result.V -> list | None            Result.solve_seconds -> float
Result.breakdown(f, split=True) -> dict        # split=True = run11의 breakdown_100k
Result.receipt(breakdown_100k=None) -> dict    # None = freq에 1e5가 있을 때만

# ---- 영수증 유틸 ---------------------------------------------------------------------
attach_reference(receipt, ref_freq, ref_Z) -> receipt   # Zref_*, ladder_gates, err 스칼라 추가
numerics_id(reference_mode, flags, mesh, conventions) -> str
decap_config_sha256(config: dict[refdes, model_id | None]) -> str
ladder_gates(freq, Z, zr, fres_m, fres_r) / resonance(f, z) / fit_dl(f, dIm) / dielectric_used(ex, eps_table)
RECEIPT_VERSION = 1, VALIDITY_NOTES = [고정 문구 4개]
```

전형적 사용(게이트 스크립트가 쓰는 경로 그대로):

```python
d    = Design.open(spd)
rail = d.rail("Port18_SITE0", cache_dir)                       # prepare + 캐시
opt  = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                    sub=(20, 10, 10), fringe=True)             # fine_box는 rail 것이 자동으로 들어간다
mdl  = rail.build(opt, Backend(solver="cudss", fast=True))
res  = mdl.solve(freqs)
rec  = res.receipt()                                            # 영수증 v1
attach_reference(rec, ref_freq, ref_Z)                          # 참조가 있을 때만
```

- `Rail.build`는 `options.fine_box`가 `None`이면 레일의 `fine_box`를 채운다(`dataclasses.replace`).
  빌드한 모델에 `mdl.rail = rail`을 붙여 영수증이 SPD 경로·sha·포트·`prepare_seconds`를 안다.
- `Model.set_decaps`는 W8이다. `Result.receipt()["decap_config_sha256"]`은 지금
  `{refdes: model_id}`(= `ex["decaps"]`)로 계산하므로 W8이 바꾼 config를 그대로 넣으면 된다.

## 3. 영수증 v1 필드표

`Result.receipt()`가 항상 채우는 필드(계획 §2-3 목록 그대로).

| 필드 | 형 | 출처 |
|---|---|---|
| `receipt_version` | int = 1 | `receipt.RECEIPT_VERSION` |
| `engine_version` | str | `spd_pi_engine.__version__` |
| `numerics_id` | str(sha256) | §3-1 |
| `spd_path` / `spd_sha256` / `port` | str | `Rail`(없으면 `ex["spd_path"]`/`ex["port_name"]`) |
| `rail` | str | `ex["rail_net"]` |
| `reference_mode` | str | `ModelOptions.reference` |
| `flags` | dict(16) | `mdl.info`의 16개 플래그(= `FLAGS` 키) |
| `mesh` | dict | `h, fh, top_h, sub, fringe, fringe_wd, fine_box, max_layers` |
| `backend` | dict | `solver, fast, ir_steps, device`(cuDSS를 실제로 만들었을 때 GPU 이름, 아니면 `null`) |
| `conventions` | dict | `top_layer_name, gnd_net, gnd_sheets, is_gnd`(함수명) |
| `decap_config_sha256` | str | `sha256(sorted({refdes: model_id}))` |
| `unknowns` | int | `mdl.N` |
| `nodes_before_prune` | int | `info["nodes_before_prune"]` |
| `decaps_connected` | int | `info["decaps_connected"]` |
| `two_sided_layers` | list[str] | `sorted(mdl.two_sided_layers)` |
| `plane_C_total_nF` | float | `info["plane_C_total_nF"]` |
| `dielectric_used` | dict | `run11.dielectric_used` 이식(`reference.eps_tand`) |
| `reference_search` | dict | 층별 블록 리스트(`sides` 제외) — run11과 동일 |
| `build_info` | dict(41키) | `[model3] {...}` 로그 줄의 dict(`info` 중 `reference_search` 제외) |
| `freq` / `Z_re` / `Z_im` | list[float] | `Result` |
| `breakdown_100k` | dict | `split_breakdown(1e5)`. `freq`에 1e5가 있으면 자동, `breakdown_100k=True`면 강제(그 점만 추가로 푼다) |
| `stats` | list[dict] | 주파수별 `assemble_s/factor_s/nnz_LU/rss_MB/solver` |
| `wall_seconds` | float | `prepare + build + solve` |
| `peak_rss_MB` | float | `model.peak_rss_mb()` |
| `validity` | dict | `{design_class: "unknown", notes: [4개 고정 문구]}` |

`attach_reference(receipt, ref_freq, ref_Z)`가 추가하는 선택 필드:

| 필드 | 내용 |
|---|---|
| `Zref_re` / `Zref_im` | 영수증 주파수에 최근접 매칭한 PowerSI Zdiag |
| `ladder_gates` | `exp3/run3.ladder_gates` 그대로: `G1_max_abs_dRe_mOhm`, `G2_dL_pH_range`, `G3_rel_err_1MHz`, `G4_max_rel_err`, `G4_f_res_model_Hz`, `G4_f_res_rel_err_vs_1.585MHz`, `G5_rel_err[]`, `PASS{G1..G4}` |
| `err_1MHz`, `fit_dL_pH`, `dL_1MHz_pH`, `dRe_100k_mOhm`, `dRe_1MHz_mOhm`, `f_res_model`, `f_res_ref` | `run11.run`의 스칼라(§4 이탈 3) |

`f_res_ref`는 run11과 같이 **참조 전체 격자**의 1e5–1e8 구간에서 구한다(영수증 27점이 아니다).

### 3-1. `numerics_id`

`sha256(reference_mode, sorted(flags), mesh, conventions, 5개 수치 모듈의 소스 바이트)`.
모듈은 `geometry.py, homogenise.py, solver.py, reference.py, model.py`(각 sha256/16).
`mesh`에서 `fine_box`만 뺀다(§4 이탈 1). 게이트 8회 실행 전부 같은 값:
`a17942b94e8555d5…`.

### 3-2. `validity`(계약 필드, 계획 §5-7)

`design_class: "unknown"` + `notes` 4줄(PROGRESS_SUMMARY_2026-09-18 §4 고정 문구):

1. PCB(s5m6585, held-out, 160포트): 1 MHz err 중앙값 0.58 %(IQR 0.34–1.29), G3 PASS 128/160.
2. 패키지 대형 평면 레일: err ≤ 3 %.
3. 패키지 전체: 1 MHz err 중앙값 31.7 %, Re Z_ref/Re Z_model @100 kHz 중앙값 1.46(급전 경로 R 관례 미해결).
4. G4 f_res 편향 +5–14 %(전 케이스, 설계 무관, 원인 미확정).

앱은 이 필드를 표시해야 한다.

## 4. 게이트 결과 — 계획 테스트 (ii)

`WORK_DIR\engine_w4\w4_gate.py --all --jobs 4 --backend gpu` (워커 프로세스 7개, 동시 4).
**새 API만 사용**(`Design.open → rail → build(FLAGS_P) → solve(ladder)`); 연구 트리에서 읽는 것은
SPD 경로표(`common/paths`)와 참조 npz뿐이다(C13·C17/C18은 엔진 밖이라는 계획대로).
주파수는 `LADDER(7점) + 3e5–3e7 log 21점`을 참조 격자에 스냅한 27점(run11 = `engine_w2/w2_gates.ladder_freqs`
동일 로직), 영수증의 `freq`와 배열 동등을 assert한다.

| # | tag | port | 백엔드 | 미지수 (exp28/p) | max ΔZ/\|Z\| | 한도 | plane_C·two_sided | `ladder_gates.PASS` 일치 | \|Δerr_1MHz\| | wall (prep/build/solve) | peak RSS | 판정 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 260729 | Port1_SITE0 | cudss+fast | 118064 (118064) | **1.578e-10** | 1e-8 | 일치 | True | 5.1e-12 | 44.7 s (24.9/9.6/7.4) | 1849 MB | PASS |
| 2 | 260729 | Port7_SITE0 | cudss+fast | 586481 (586481) | **1.531e-09** | 1e-8 | 일치 | True | 1.2e-10 | 94.9 s (30.3/34.7/26.0) | 2806 MB | PASS |
| 3 | 260729 | Port14_SITE0 | cudss+fast | 34424 (34424) | **8.859e-11** | 1e-8 | 일치 | True | 7.7e-13 | 9.7 s (1.2/4.5/2.2) | 1731 MB | PASS |
| 4 | 260729 | Port16_SITE0 | cudss+fast | 334638 (334638) | **3.976e-10** | 1e-8 | 일치 | True | 4.7e-12 | 61.6 s (24.9/17.9/15.3) | 2235 MB | PASS |
| 5 | 260729 | Port18_SITE0 | cudss+fast | 275218 (275218) | **4.459e-11** | 1e-8 | 일치 | True | 2.4e-11 | 28.5 s (1.5/10.8/12.5) | 2085 MB | PASS |
| 6 | 260729 | Port19_SITE0 | cudss+fast | 69205 (69205) | **1.147e-09** | 1e-8 | 일치 | True | 1.4e-11 | 43.5 s (27.8/7.3/6.0) | 1752 MB | PASS |
| 7 | 260804 | Port18_SITE0 | cudss+fast | 275177 (275177) | **5.585e-09** | 1e-8 | 일치 | True | 9.4e-12 | 32.8 s (1.7/13.5/14.0) | 2084 MB | PASS |
| 대조 | 260729 | Port14_SITE0 | splu (CPU) | 34424 (34424) | **3.232e-12** | 1e-9 | 일치 | True | 0.0 | 49.6 s (1.1/38.4/7.9) | 1161 MB | PASS |

- 7케이스 전부 `unknowns` 일치, `plane_C_total_nF` 비트 일치, `two_sided_layers` 일치.
- `attach_reference`가 영수증만 가지고 exp28/p 영수증의 `ladder_gates.PASS` dict를 그대로 재현한다
  (7/7). 같은 케이스의 `PASS`는 모두 `{G1: False, G2: False, G3: True, G4: False}` 형태로
  연구 영수증과 dict 동등이다. `err_1MHz`는 최대 차 1.2e-10 ≤ 1e-9(대부분 1e-12대).
- 게이트 스크립트는 `receipt["Zref_*"]`로 `ladder_gates()`를 한 번 더 직접 호출해 같은 `PASS`가
  나오는지도 확인한다(= 연구 코드 없이 영수증만으로 게이팅 가능).
- prepare 25–30 s는 cold(해당 포트 추출이 `engine_cache`에 없던 Port1/7/16/19), 1–2 s는 캐시 히트.
  W3이 채운 260729 P18/P14·260804 P18 캐시는 재사용했고 지우지 않았다.
- 연구 코드 무변경: `git status --short -- tools/research-claude` 출력 없음.
- 데이터 없는 셀프체크: `python -c "from spd_pi_engine.receipt import demo; demo()"` → `DEMO PASS`
  (G1–G5, `attach_reference`, `numerics_id`의 `fine_box` 무관성, `decap_config_sha256` 순서 무관성).

## 5. 계획에서 어긋난 점

1. **`numerics_id`에서 `fine_box`를 뺐다.** `fine_box`는 포트의 기하(포트 bbox ± 1000 µm)여서
   넣으면 id가 포트마다 달라지고 "수치가 바뀌었나"에 답할 수 없다. `mesh` 필드 자체에는 그대로
   들어 있으므로 영수증에서 잃는 정보는 없다. 실측: 게이트 8회 전부 같은 id.
2. **`Model.solve`가 `Result`를 돌려준다(model.py +3줄).** 계획 §2-3이 `res = mdl.solve(...)`,
   `res.freq/res.Z/res.receipt()`를 요구하므로 반환형을 바꿔야 했다. 기존 호출부(W2/W3 게이트
   스크립트, 연구 드라이버)를 깨지 않도록 `Result.__iter__`가 `(Z, stats)` / `(Z, stats, Vs)`를
   그대로 언패킹한다 — `Z, stats = mdl.solve(f)`도 `_, _, Vs = mdl.solve(f, want_v=True)`도 무변경
   동작. 수치 줄은 건드리지 않았다(게이트가 증거).
   `Result`는 `receipt.py`에 둔다(`model` → `receipt` → `reference` 방향, 순환 없음. `receipt`가
   필요로 하는 `FLAGS`/`peak_rss_mb`만 `Result.receipt()` 안에서 지연 import).
3. **`attach_reference`가 `run11`의 오차 스칼라 7개도 채운다**(`err_1MHz`, `fit_dL_pH`,
   `dL_1MHz_pH`, `dRe_100k_mOhm`, `dRe_1MHz_mOhm`, `f_res_model`, `f_res_ref`). 계획은 `Zref_*`와
   `ladder_gates`만 적었지만, 게이트가 `err_1MHz` 재현을 요구하고 이들은 전부 `run11.run`의 같은
   블록에서 나오므로 함께 옮기는 편이 계산 중복이 없다.
4. **CPU 대조 영수증은 `receipt_{tag}_{port}_cpu.json`**으로 쓴다(CLAUDE.md 규칙 2: 덮어쓰기 금지).
   `receipt_{tag}_{port}.json` 7개는 GPU 케이스의 것이다.
5. **`Rail`은 생성 시점에 `prepare()`를 돈다**(지연 아님). `Design.rail(...)`을 부른 시점이 곧
   추출 요청이고, `decaps`·`estimate_cost()`·`build()`가 모두 그 결과를 필요로 하기 때문이다.
   지연은 `Design`(파일을 열지 않음)과 `Design.sha256`(cached_property) 수준에서만 한다.
6. **`backend.device`는 cuDSS 솔버가 실제로 만들어진 뒤에만 채워진다**(`mdl._cudss.device`).
   `solver="cudss"`로 요청했어도 폴백했다면 `null`이 되어 영수증이 실제 실행 장치를 말한다.
7. **`cli.py`·`decaps.py`는 W4 범위가 아니다**(각각 W7, W8). `__init__.py`에도 없다.
