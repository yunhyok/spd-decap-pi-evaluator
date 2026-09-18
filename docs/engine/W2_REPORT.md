# W2 — Model 병합 (`src/spd_pi_engine/{reference,model}.py`) 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §4 W2, 원칙 §0(수치 불변, 동일 연산 순서,
`tools/research-claude/` 무수정). 2026-09-18.

## 1. 클래스 병합 지도

| 새 심볼 | 줄 | 원본 | 승격된 암묵 상태 |
|---|---|---|---|
| `reference.ReferenceSearch` | 292 (파일 전체) | `exp1/exp1b.TwoSided` + `exp8/run8.TwoSidedAny` | C2 `mode`, C3 `eps_table/c_all_refs/c_unit_fix` 생성자 인자, C8 `fast` 명시 인자, C10 `cache`·`_layer_rasters` |
| `reference.Stack` / `eps_tand` / `diel_rows` | 〃 | `exp1/model.py` (W1에 없었음) | C5 `is_gnd` 규칙 주입 |
| `reference.DesignConventions` | 〃 | `model3.GND_SHEETS`, `"Signal$TOP"`, `TwoSided(gnd_net="DGND")`, `Stack.is_gnd` | C5 |
| `model.Sheet` | 938 (파일 전체) | `exp3/model3.Sheet` | `backend`, C10 `_fa_weff` |
| `model.Model` | 〃 | `model3.Model3` + `run4.Model4` + `pipeline.ModelB` | C1·C4·C6·C8·C9·C10 (아래) |
| `model.Model.split_breakdown` | 〃 | `run4.split_breakdown`(모듈 함수) | 메서드로 |
| `model.zs_two` / `pad_traces` / `fill_small_voids` / `reduce_series` / `peak_rss_mb` | 〃 | `run4.zs_two`, `model3.*`, `common/paths.peak_rss_mb` | — |
| `FLAGS`, `FLAGS_LEGACY/P/Q/PMK` | 〃 | `exp11/run11.py:44-47,63,65,74` | 프리셋 |

메서드 대응: `Model._build` = `Model3.build` + `ModelB.build`의 과반 후처리(`_two_sided_majority`),
`Model.zs_plane` = `ModelB.zs_plane`(+`Model4`의 orig/a), `edge_z`/`weff_of`/`wall_zs` = `Model4`,
`assemble`/`solve`/`breakdown`/`via_R`/`sheet_c_tand`/`_pad_links`/`_build_gnd` = `Model3`.
수식 줄은 전부 축자 복사이고 연산 순서도 그대로다(게이트 2·3에서 비트 동일로 확인).

승격 결과:

| 계획 | 연구 코드 | 엔진 |
|---|---|---|
| C1 | `run4.patch_traces(mode)`가 `M3.copper_surface_impedance` 재바인딩 | `ModelOptions.trace_zs_mode` → `Model._zs_fn` (소비: off-plane trace Zs, `via_R` hollow, `breakdown`) |
| C2 | `M3.TwoSided = R8.TwoSidedAny` | `ModelOptions.reference` |
| C3 | `ts.eps_table/c_all_refs/c_unit_fix` 사후 주입 | `ReferenceSearch(...)` 생성자 인자 |
| C4 | `Model4.mode`/`gnd_scale` 클래스 속성, `ModelB.build`의 `self.mode="b"` | `ModelOptions.zs_mode` (동결 "b") |
| C5 | `GND_SHEETS`, `"Signal$TOP"`, `"DGND"`, `Stack.is_gnd` | `DesignConventions` (260729 기본값 그대로) |
| C6 | `b["h"] >= 200.0` | `ModelOptions.two_sided_majority_h=200.0` |
| C8 | `fast_assemble.ON` (import 시각 `SPD_PI_FAST`) | `Backend.fast` (W1) |
| C9 | `os.environ["SPD_PI_SOLVER"]` | `Backend.solver` ("cudss"/"auto" 둘 다 GPU, 실패 시 splu) |
| C10 | `_fa_pat`·`_fa_tr`·`_cudss`(Model), `_fa_weff`(Sheet), `_layer_rasters`·`cache`(ref) | 명시 필드 |

엔진은 `os.environ`을 읽지 않고, import 시 파일시스템을 건드리지 않으며, `print`가 없다
(`Model(..., log=…)`, 기본값은 no-op).

## 2. 옵션 / 플래그 표 (기본값)

`ModelOptions` (기본값 = 동결 경로):

| 필드 | 기본값 | 비고 |
|---|---|---|
| `reference` | `"powersi-compatible"` | `"physical-gnd"` = DGND 전용 탐색(EXP-5 규칙) |
| `flags` | `FLAGS`(전부 off) | 아래 16개 |
| `h` / `fh` / `top_h` | 200.0 / 50.0 / 50.0 | µm |
| `sub` | `(20, 10, 10)` | coarse/fine/top 서브타일 수 |
| `fringe` / `fringe_wd` | `False` / 5.0 | 게이트는 `fringe=True` |
| `fine_box` | `None` | 포트 bbox + 1 mm |
| `max_layers` | 3 | 참조 탐색 층 수 |
| `gnd` / `gnd_h` / `gnd_fh` | `None` / `None` / `None` | S3 명시 GND (동결 경로는 이상 GND) |
| `zs_mode` | `"b"` | 동결. `"orig"`, `"a"`도 지원 |
| `trace_zs_mode` | `"one_sided"` | 동결(= `patch_traces("b")`). `"real_only"` = run4 모드 a |
| `two_sided_majority_h` | 200.0 | |
| `conventions` | `DesignConventions()` | `top_layer_name="Signal$TOP"`, `gnd_net="DGND"`, `gnd_sheets=[L02,L13,L16,L18,L24,L27 (DGND)]`, `is_gnd=default_is_gnd` |

플래그 16개(`run11.FLAGS`와 같은 이름·기본값): `eps_table=False, c_all_refs=False, zs_cell=False,
zs_wall=False, c_unit_fix=False, fringe_no_thresh=False, fringe_no_cap=False, homog_L_noG=False,
via_area_exact=False, via_L_twowire=False, zs_wall_skin=False, zs_wall_skin_re=False,
via_R_skin=False, homog_face_fix=False, via_len_surface=False, void_fill_um=0.0`.
프리셋: `FLAGS_LEGACY`(전부 기본), `FLAGS_P`(`c_unit_fix`+`homog_face_fix`, D7 기준선),
`FLAGS_Q`(p+`via_len_surface`), `FLAGS_PMK`(p+`via_R_skin`+`zs_wall_skin_re`).

## 3. 게이트 결과 (전부 통과)

게이트 스크립트 `engine_w2/w2_gates.py`(113줄). 연구 코드는 `pipeline.prepare`(ex/shapes/fine_box)와
`run11.ref_of`(참조 주파수 격자)만 **읽기 전용**으로 쓴다. 모델은 전부 엔진 `Model`이다.

### (i) 동결 legacy — 260729 Port18, `FLAGS_LEGACY`, `Backend()`, 1 MHz 최근접 1점

| 항목 | 값 |
|---|---|
| unknowns | **261124** (영수증과 동일) |
| rel vs `results/exp8/result_260729_Port18_SITE0_any.json` | **5.829e-12** (기대 5.83e-12, 한도 1e-9) |
| build | 450 s, wall 477 s |
| `[model3] {...}` 한 줄 | 연구 `smoke_port18.py` 출력과 `build_seconds`만 빼고 **문자열 동일**, `[sheet]` 5줄도 동일 |

### (ii) 변형 p, 전체 사다리(27점, run11 격자) — `FLAGS_P`

CPU = `Backend()`, GPU = `Backend(solver="cudss", fast=True)`. "research-today"는 같은 환경에서
`run11.py --variant p --outdir engine_w2`로 오늘 다시 돌린 연구 코드다.

| 케이스 | unknowns | 엔진 CPU vs **research-today** | 엔진 CPU vs exp28 영수증 | research-today vs exp28 | GPU vs 영수증 | wall CPU / GPU |
|---|---|---|---|---|---|---|
| 260729 Port14_SITE0 | 34424 | **0.0** (27점 전부 비트 동일) | 3.232e-12 | 3.232e-12 | 4.544e-11 | 40 s / **13.6 s** |
| 260729 Port18_SITE0 | 275218 | **0.0** | 5.858e-12 | 5.858e-12 | 1.454e-10 | 1208 s / **30.8 s** |
| 260804 Port18_SITE0 (게이트 iii) | 275177 | **0.0** | 1.915e-11 | 1.915e-11 | — | 1196 s |

- 한도: CPU 1e-9, GPU 1e-8 → 전부 통과.
- `plane_C_total_nF`(0.8160737417762662 / 4.676591517782525 / 5.285299188734243), `two_sided_layers`,
  `unknowns`가 영수증과 정확히 일치. `breakdown_100k`(`split_breakdown` 1e5 Hz) 최대 절대차
  3.5e-11 / 3.0e-11 / 4.7e-11 (mΩ·pH 단위).
- **영수증 대비 잔차는 엔진 탓이 아니다**: 세 케이스 모두 엔진과 연구 코드의 오늘 결과가 비트 동일(0.0)이고,
  잔차는 연구 코드 자체가 exp28 영수증(2026-09-15 환경)에 대해 갖는 값과 소수점까지 같다.
  27점 중 대부분은 0.0이고 3~5점만 1e-13~3e-12로 어긋난다. (exp28 영수증의 `stats`에는 `solver` 키가
  없다 = 그 키가 추가되기 전 실행이다.) GPU 잔차(1e-10)는 W1에서 측정한 균질화 GPU 차(1.5e-14)와
  cuDSS 인수분해 차가 합쳐진 것으로, GPU 계약 1e-8 안이다.
- CPU wall 1208/1196 s는 네 작업을 동시에 돌린 값이다(영수증 단독 실행은 1074/1088 s). GPU는 단독 실행.

### (iv) 연구 경로 무변경
- `git status --short -- tools/research-claude` → 출력 없음.
- `python tools\research-claude\common\smoke_port18.py` → `Z model = +0.708336 -0.411816j mOhm`,
  `rel diff 5.83e-12`, `err vs PowerSI 2.69 %`, wall 470 s, peak RSS 1439 MB, **SMOKE PASS**.

## 4. 계획에서 어긋난 점

1. **`zs_mode="c"`를 버렸다.** `run4`의 `TWO_SIDED`/`N_GND`/`gnd_scale`은 mode "c"에서만 읽히고,
   동결·p 경로는 `ModelB.zs_plane`이 `Model4.zs_plane`을 통째로 대체하므로 절대 닿지 않는다(통독 확인).
   `"b"`(동결), `"orig"`, `"a"`만 남겼다. `edge_z(rail=False)`의 `R * gnd_scale`도 `R`로(값 동일).
2. **`Stack`/`eps_tand`가 W1에 없었다.** `exp1/model.py`의 것이라 계획 §2-2 모듈 표에 항목이 없다 →
   `reference.py`에 축자 이식하고 `model.py`가 거기서 가져온다(순환 없음). `Stack.is_gnd`는
   `DesignConventions.is_gnd` 주입형으로 바꿨다(C5).
3. **`peak_rss_mb`를 `model.py`에 축자 이식했다.** 영수증 `stats.rss_MB`를 동일하게 유지하려면 필요하고
   (이 머신에 psutil이 없어 ctypes psapi 분기가 쓰인다), 연구 `common/paths.py`를 런타임에 import할 수 없다.
4. **연구 코드의 미정의 이름 1건.** `model3._build_gnd`의 `scaled_keys`는 선언이 없어
   `via_area_exact=True` + 명시 GND 조합에서 `NameError`가 난다(동결·p 경로는 도달 불가).
   엔진에서는 지역 `set()`으로 선언했다.
5. **게이트 2·3의 "기대 0 또는 ~1e-15"는 영수증이 아니라 오늘의 연구 코드 기준이었다**(§3 (ii)).
   영수증 자체가 환경 차로 최대 1.9e-11 움직인다. 엔진 판정은 영수증 기준 1e-9로 통과하고,
   동시에 연구 코드 대비 0.0이다.
6. `ModelOptions.flags`는 16개 개별 필드가 아니라 `run11.FLAGS`와 같은 모양의 dict다(프리셋 재사용,
   미지 키는 `ValueError`). `Model.build(...)`는 클래스메서드이고 실제 빌드는 `_build()`다.
7. 데이터 없는 셀프체크는 W2에 추가하지 않았다(`Model`은 `ex`가 있어야 성립). 검증은 영수증 게이트다.

## 5. 산출물
- 패키지: `src/spd_pi_engine/reference.py`(292줄), `model.py`(938줄), `__init__.py`(22줄, 재수출 갱신).
  W1 모듈 4개는 무변경. 패키지 합계 2049줄.
- 게이트 스크립트/영수증/로그: `WORK_DIR/engine_w2/{w2_gates.py, w2_legacy_260729_Port18_SITE0_cpu.json,
  w2_p_260729_Port14_SITE0_{cpu,gpu}.json, w2_p_260729_Port18_SITE0_{cpu,gpu}.json,
  w2_p_260804_Port18_SITE0_cpu.json, result_260729_Port14_SITE0_any_p.json(연구 대조),
  result_260729_Port18_SITE0_any_p.json(연구 대조), result_260804_Port18_SITE0_any_p.json(연구 대조),
  g1_legacy.log, g2_*.log, g3_*.log, ctrl_*.log, smoke_port18.log}`
- 이 보고서와 사본: `docs/engine/W2_REPORT.md`.
