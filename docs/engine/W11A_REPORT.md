# W11-a 보고서 — `spd_pi_engine`의 제품 통합 1단계 (2026-09-19)

계획: `docs/engine/W11_PLAN_2026-09-19.md` §1, §2, §4, §6 행 W11-a.
저장소: `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator`, 브랜치
`claude/lightweight-hybrid-20260915`, 기준 커밋 `3ca93a8`(커밋하지 않음).

규칙 준수:
- `src/spd_pi_engine/` **0줄 변경**(`git status --short src/spd_pi_engine` 빈 출력).
- `tools/research-claude/` **0줄 변경**.
- 기본 솔버 프로파일 불변: `APPLICATION_DEFAULT_SOLVER_PROFILE_KEY = layerwise_admittance_v1`,
  `DEFAULT_SOLVER_PROFILE_KEY = legacy_modal_v017`.
- 기존 프로파일 3개의 코드 경로는 바이트 동일. 분기는 진입점 3곳 + `_evaluation_settings`
  + 베이스라인 캐시 2곳 + `evaluation_model_boundary_disclosure` + `build_evaluation_workspace`
  에서만 `if profile in ENGINE_PROFILES:`로 **앞에서 빠져나가는** 형태로 추가했다.

---

## 1. 변경 파일과 줄 수

| 파일 | +/- | 내용 |
|---|---|---|
| `src/spd_decap_pi/_core/solver/profiles.py` | +44 / -0 | 프로파일 2개, `ENGINE_PROFILES`, `ENGINE_REFERENCE_MODE`, `__all__` |
| `src/spd_decap_pi/_core/solver/engine_adapter.py` | **신규 565줄** | 어댑터(요청/결과, 포트·decap 매핑, 워커 호출, `EvaluationOutcome` 조립) |
| `src/spd_decap_pi/_core/solver/engine_worker.py` | **신규 138줄** | 서브프로세스 워커(prepare → 메모리 가드 → build → 구성별 solve → 영수증) |
| `src/spd_decap_pi/evaluation.py` | +419 / -2 | 진입점 3곳 분기, 엔진 preflight/outcome/view 헬퍼, `_evaluation_settings`, 캐시 off |
| `src/spd_decap_pi/_core/services.py` | +25 / -0 | `evaluation_model_boundary_disclosure` 엔진 분기, `_evaluation_view` 혼합 참조 문구 엔진 분기(+ import 2줄) |
| `src/spd_decap_pi/gui_launcher.py` | +18 / -2 | Qt import 전 `--engine-worker` 분기 |
| `scripts/validate_real_distribution_replay.py` | +17 / -2 | `--solver-profile` 추가(기본값 불변), 하드코딩 프로파일 2곳 교체 |
| `pyproject.toml` | +1 / -0 | 마커 `engine_reproduction` |
| `tests/test_engine_adapter_reproduction.py` | **신규 319줄** | 재현 테스트 4건(마커) + 데이터 없는 테스트 5건 |

합계: 제품 코드 +524 / -6, 신규 파일 3개(1 022줄).

---

## 2. 삽입 지점 (현재 트리 줄번호, 계획의 HEAD 줄번호 재검증 결과 포함)

### 2-1. `profiles.py` (계획 §1-1, 앵커 `profiles.py:82-92` — HEAD에서 확인됨)

| 줄 | 내용 |
|---|---|
| 82-94 | `HYBRID_PLANE_PAIR_PROFILE` (`hybrid_plane_pair_v1`, badge `HYBRID`, `experimental=False`, `compiler_algorithm_id="spd-pi-engine-hybrid-plane-pair-powersi-compatible-v1"`) |
| 96-107 | `HYBRID_PLANE_PAIR_GND_PROFILE` (`hybrid_plane_pair_v1_gnd`, badge `HYBRID-GND`, `...-physical-gnd-v1`) |
| 109-115 | `SOLVER_PROFILES` 5항목으로 확장 |
| 116-121 | `ENGINE_PROFILES = frozenset({...})` |
| 122-126 | `ENGINE_REFERENCE_MODE = {hybrid_plane_pair_v1: "powersi-compatible", hybrid_plane_pair_v1_gnd: "physical-gnd"}` |
| 131-132 | 기본 상수 2개는 **그대로**(`DEFAULT_SOLVER_PROFILE_KEY`, `APPLICATION_DEFAULT_SOLVER_PROFILE_KEY`) |

`experimental=False`이므로 `_evaluation_settings`의 연구 증거 해시 요구
(HEAD `evaluation.py:565-598`)에 걸리지 않는다. **계획의 앵커 `evaluation.py:3565-3597`은
오타로 보인다** — HEAD 3565-3597은 trace RL/`fill_provenance` 코드이고, 해당 게이트는
`_evaluation_settings` 안 565-598이다(§5 참조).

### 2-2. `_core/solver/engine_adapter.py` (계획 §1-2, §1-4, §1-5, §1-6, §3)

| 줄 | 심볼 | 비고 |
|---|---|---|
| 57 | `ENGINE_MESH` | `h=200.0, fh=50.0, top_h=50.0, sub=(20,10,10), fringe=True` — `tests/engine/test_reproduction.py:29-30`의 `OPT`와 한 글자도 다르지 않음 |
| 68-81 | `engine_cache_dir()` | `SPD_PI_ENGINE_CACHE` → 없으면 `%LOCALAPPDATA%\SPD Decap PI Evaluator\engine-cache`. import 시 파일시스템 무접촉 |
| 84-87 | `engine_receipt_dir()` | `engine_cache_dir().parent / "outputs" / "engine-receipts"` |
| 90-95 | `engine_frequencies()` | `ladder_freqs()` 28점(제품 401점 sweep 미사용) |
| 102-128 | `engine_port_for_rail(spd, rail_id, *, ports=None)` | `multiport.port_rails`(`multiport.py:68-82`) 역인덱스, 0개/2개 이상 → `ENGINE_RAIL_PORT_NOT_UNIQUE` |
| 131-151 | `decap_config(scenario, rail_id)` | 계획 §3 식 그대로(`scenario.py:964-1016` `ScenarioDecap`) |
| 154-163 | `cross_net_decap_refdes(scenario, rail_id)` | 교차-net 유입 refdes |
| 166-200 | `extra_models(scenario, attachments, config)` | `project.metadata["cap_model_sources"]` → `attachments` 원문 |
| 202-258 | `EngineSolveRequest` | frozen, JSON 왕복. spd_path/spd_sha256/port/reference_mode/freqs/cache_dir/solver/configs/extra_models/**threads** |
| 260-267 | `EngineSolveResult` | receipts / receipt_paths / receipt_sha256 / libraries |
| 270-298 | `_worker_argv`, `_worker_env` | frozen이면 `sys.executable --engine-worker`, 아니면 `-m spd_decap_pi._core.solver.engine_worker`. 스레드 env는 **워커 환경에만**(`plan_threads`) |
| 301-385 | `solve(request, progress, is_cancelled)` | `subprocess.Popen` + stdout `PROGRESS <pct> <msg>` 파싱, 영수증을 `unique_path`로 보존 후 sha256 |
| 388-423 | `solve_request(...)` | 프로파일 → `reference_mode` 결정, 기본 격자 |
| 431-470 | `engine_confidence(freqs, notes)` | `<1e5 / 1e5–1e8 / >1e8` 3밴드, 전부 LOW(계획 §1-2) |
| 473-542 | `evaluation_outcome(receipt, ...)` | `EvaluationOutcome`(`evaluator.py:278-288`) 조립: `ModalSolveResult`(`modal.py:616-634`), `compute_evaluation_metrics`(`metrics.py:139`), `assumptions=receipt["validity"]["notes"]`, `solver_version=f"spd-pi-engine-{engine_version}-{numerics_id[:12]}"`, `solver_provenance` |

`solver_provenance` 키: `profile_key, profile_badge, source_only=True,
powersi_used_for_parameters=False, compiler_algorithm_id,
solver_static_identity_sha256, engine_version, numerics_id, reference_mode, port,
rail_net, receipt_sha256, spd_sha256, decap_config_sha256, freq_grid_sha256,
prune_basis, unknowns, backend, validity_notes, library_versions`
(`library_versions` = matplotlib/numpy/Pillow/scipy, 계획 §5).

### 2-3. `_core/solver/engine_worker.py` (계획 §4)

`main(argv)` → `argv[0]` = `<request.json>`. 순서: SPD 존재/ sha256 → `design.rail(port, cache)`
→ `_memory_guard` → `rail.build(ModelOptions(reference, FLAGS_P, **ENGINE_MESH),
Backend(solver, fast=True))` → 미등록 `extra_models`만 `add_decap_model` →
구성마다 `set_decaps(config, replace=True)` + `solve()` + `receipt()` →
`<request.json>.result.json`. stdout `PROGRESS 5/20/40../99`.

### 2-4. `evaluation.py`

| 줄 | 내용 |
|---|---|
| 56 | import에 `ENGINE_PROFILES` 추가 |
| 84 | import에 `SourceIdentity` 추가(sha256 재계산 재사용) |
| 560-577 | `_evaluation_settings` 엔진 분기(계획 §1-3의 "6줄"): `solver_static_identity_sha256` + `engine={numerics_id, engine_version, reference_mode, freq_grid_sha256, decap_config_sha256}` |
| 2608-2612 | `preflight_evaluation_comparison` 엔진 분기 |
| 3172-3177 | `_load_baseline_evaluation` 베이스라인 캐시 **off**(`or profile in ENGINE_PROFILES`) |
| 3233-3238 | `_cache_baseline_evaluation` 동일 |
| 4504-4509 | `build_evaluation_workspace` 엔진 가드(재수화 시 아트워크 빌더를 돌리지 않음) |
| 4610-4614 | `_engine_rail` |
| 4616-4624 | `_engine_blocker` |
| 4627-4699 | `_engine_preflight` — 검사 3가지 |
| 4702-4750 | `_engine_outcomes` — 워커 1회 호출, 구성별 `EvaluationOutcome` |
| 4753-4792 | `_engine_scenario_evaluation` — `_evaluation_view`(`services.py:4477`) + `ScenarioResultKey` |
| 4795-4801 | `_engine_project` — `scenario.base_project`(+`_project_with_target`) |
| 4804-4843 | `_evaluate_scenario_with_engine` |
| 4846-4955 | `_engine_comparison_batch` — `with_baseline_captures`(`scenario.py:2949`) + `original_configuration`(`scenario.py:3061`), 한 번 build로 Original/Tuned |
| 4989-4998 | `evaluate_scenario` 분기 (`canonical_rail` 확정 직후) |
| 5088-5098 | `evaluate_comparison_batch` 분기 (`resolve_solver_profile` 직후) |

엔진 preflight 검사 3가지(기존 `EvaluationConnectivityPreflight` /
`EvaluationConnectivityBlocker` 타입 사용, `evaluation.py:145/171`):
1. `ENGINE_SPD_MISSING` / `ENGINE_SPD_SHA256_MISMATCH` — 원본 SPD 존재 + sha256 일치
   (`SourceIdentity.from_path`, `scenario.py:274` 재사용).
2. `ENGINE_RAIL_PORT_NOT_UNIQUE` — 레일당 SPD 포트 정확히 1개.
3. `ENGINE_CROSS_NET_DECAP_ASSIGNMENT` — 다른 source net에서 들어온 decap.

### 2-5. `_core/services.py` (계획 §1-6)

- 34-41행 import에 `ENGINE_PROFILES`, `ENGINE_REFERENCE_MODE`.
- `evaluation_model_boundary_disclosure`(HEAD `services.py:258-290`, 현재 260-278) 맨 앞에 엔진 분기 14줄(현재 264-277):
  함수 안에서 `from spd_pi_engine.receipt import VALIDITY_NOTES`(지연 import 관례,
  `services.py:841-843`)로 영수증의 validity 5줄을 그대로 인용한다.
  `_evaluation_view`(`services.py:4527`)가 이 문자열을 `confidence_note` 끝에 자동으로 잇고,
  `assumptions`에는 `EvaluationOutcome.assumptions`가 그대로 들어간다 → 계획 §1-6의 ①②③ 충족.
- `_evaluation_view`의 혼합 참조 문구(현재 `services.py:4579-4587`)에 엔진 분기 8줄. 계획에는
  없던 항목이며, 없으면 엔진 결과에 "continuous rectangular-return approximation"이라는
  **거짓 문장**이 붙는다(§4의 e2e가 잡아냈다).

### 2-6. `gui_launcher.py` (계획 §4-2)

`from spd_decap_pi.gui.app import main`을 함수 안으로 내리고, `sys.argv[1] == "--engine-worker"`
일 때 `engine_worker.main(sys.argv[2:])`를 부른다. **Qt import 전**이다
(`gui/app.py:20-21`의 `--smoke-test` 관례와 같은 자리). `SPDDecapPIEvaluator.spec:10`과
`scripts/build_spd_decap_pi.ps1:44`가 이 파일을 그대로 가리키므로 spec 변경은 없다.

### 2-7. CLI — **계획에서 벗어난 부분(중요)**

계획이 지목한 CLI는 `scripts/benchmark_raw_spd_powersi_correlation.py`이고 이미
`--solver-profile`이 있다(HEAD 622-630). 처음에는 하드코딩된 `choices=(...)` 3개를
`tuple(item.key for item in SOLVER_PROFILES)`로 바꿨는데 **제품 테스트가 21건 새로 실패**했다:

```
scripts/validate_powersi_accuracy.py:182 IntegrityError: benchmark base source hash does not match policy
```

이 스크립트는 **동결 릴리스 자산**이다 — 정확도 정책 JSON의 `benchmark_base_source.sha256`과
`scripts/validate_known_case_nonregression.py:275`가 파일의 (LF 정규화) sha256을 고정한다.
`accuracy_parse.py`와 같은 "수정 금지" 부류이므로 **`git checkout --`로 원복**했다
(현재 트리에서 이 파일은 HEAD와 바이트 동일).

대신 sha가 고정돼 있지 않은 제품 평가 CLI인
`scripts/validate_real_distribution_replay.py`에 `--solver-profile`을 노출했다.
이 스크립트는 이미 `preflight_evaluation_comparison` + `evaluate_comparison_batch`를
`--evaluation-rail`로 호출하고 있었고 프로파일만 하드코딩돼 있었다.

| 줄 | 내용 |
|---|---|
| 68-71 | `profiles`에서 `APPLICATION_DEFAULT_SOLVER_PROFILE_KEY`, `SOLVER_PROFILES` import |
| 680 | `preflight_evaluation_comparison(..., solver_profile=args.solver_profile)` |
| 693 | `evaluate_comparison_batch(..., solver_profile=args.solver_profile)` |
| 814-824 | `--solver-profile` 인자(choices = 등록된 5개, default = `APPLICATION_DEFAULT_SOLVER_PROFILE_KEY`) |

기본값이 예전 하드코딩 값(`layerwise_admittance_v1`)과 같으므로 기존 호출은 동작이 같다.
확인:

```
--solver-profile hybrid_plane_pair_v1  -> resolved: hybrid_plane_pair_v1
(옵션 없음)                             -> default:  layerwise_admittance_v1
```

**소유자 판단 필요**: `benchmark_raw_spd_powersi_correlation.py`로 엔진 프로파일을 돌리려면
정책 JSON의 `benchmark_base_source.sha256`을 갱신해야 한다(동결 승인 자산). W11-a에서는
하지 않았다.

---

## 3. 테스트

### 3-1. `tests/test_engine_adapter_reproduction.py` (계획 §2-3)

- 마커 `engine_reproduction`(`pyproject.toml`에 등록). `-m engine_reproduction`으로 **명시적으로
  선택할 때만** 무거운 케이스가 돈다(그 외에는 skip → `pytest tests`의 FAILED/ERROR 집합 불변).
- 데이터 해석: `SPD_PI_DATA_DIR` 없음/파일 없음 → **skip**, 파일은 있는데 sha256이
  `tests/engine/fixtures/spd_sha256.json`과 다르면 **fail**. 캐시는
  `SPD_PI_ENGINE_CACHE` → `SPD_PI_WORK_DIR/engine_cache` 순, 둘 다 없으면 skip.
  **`tmp_path` 미사용**(이 환경의 260 errors 원인).
- 비교 대상: `tests/engine/fixtures/exp28/result_260729_Port18_SITE0_any_p.json`의 `freq` 27점을
  그대로 요청에 넣는다(npz 불필요).

실행(OPENBLAS_NUM_THREADS 미설정) — **4 passed, 5 deselected in 1140.22s (0:19:00)**
(`work/engine_w11/repro2.log`):

```
python -m pytest tests\test_engine_adapter_reproduction.py -q -m engine_reproduction -p no:cacheprovider
```

| 게이트 | 한도 | 실측 | 판정 |
|---|---|---|---|
| `max \|dZ\|/\|Z\|` vs exp28/p 영수증 | ≤ 1e-9 | **5.858e-12** @ 1.0e5 Hz | PASS |
| `unknowns` | 275 218 | 275 218 | PASS |
| `plane_C_total_nF` 비트 동일 | — | `4.676591517782525` | PASS |
| `two_sided_layers` | 3개 | `Signal$L14(MAIN_POWER4)`, `Signal$L20(DGND)`, `Signal$L25(MAIN_POWER4)` | PASS |
| `freq` 배열 | 영수증 27점 | 동일 | PASS |
| `flags` | 영수증 14키 일치 + 전체가 `FLAGS_P` | 일치 | PASS |
| 워커 서브프로세스 영수증 == 인프로세스 | numerics_id + 수치 필드 다이제스트 동일 | 동일 | PASS |
| 영수증 파일 sha256 == `receipt_sha256` | — | 동일 | PASS |

`numerics_id = 27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0`,
`engine_version = 0.1.0.dev0`, backend `{solver: splu, fast: true, ir_steps: 1}`,
`prune_basis = all_mounted`, decap 421개, 워커 wall 595 s.

**첫 실행에서 버그 1건을 잡았다**(`work/engine_w11/repro.log`, `3 passed / 1 failed`):
`test_worker_receipt_is_kept_and_hashed`가 실패 —
어댑터가 영수증을 `Path.write_text`로 저장해 Windows에서 `
`이 `

`으로 바뀌는 바람에
보존된 파일의 sha256이 보고한 `receipt_sha256`과 달랐다. `write_bytes`로 고쳤고
(`engine_adapter.py:366-376`) Port14 소형 케이스로 확인한 뒤
(`sha256sum` = 보고값 `8e5fe124...`) 전량 재실행했다(`repro2.log`).


### 3-2. 데이터 없는 테스트 5건 (같은 파일)

`decap_config`(레일 내/해제/ISOLATION_GAP/레일 밖 이동), `cross_net_decap_refdes`,
`engine_port_for_rail`(0개·2개 → `ENGINE_RAIL_PORT_NOT_UNIQUE`), `extra_models`,
`EngineSolveRequest` JSON 왕복. 전부 `SimpleNamespace` 합성 객체.

```
python -m pytest tests/test_engine_adapter_reproduction.py -q -p no:cacheprovider
5 passed, 4 skipped in 0.81s
```
(4 skipped = 마커를 요청하지 않은 재현 테스트.)

추가로 데이터 없는 진입점 확인(`work/engine_w11/check_preflight.py`,
`tests/test_solver_profiles.py`의 합성 ScenarioSpec 재사용 — SPD 경로가 존재하지 않는 시나리오):

```
rail: VDD/0 net: VDD
preflight clear: False
  blocker: VDD/0 <spd source> engine evaluation [ENGINE_SPD_MISSING]: the original SPD is required and unreadable: SPD source does not exist
evaluate_scenario:           fail-closed -> engine evaluation [ENGINE_SPD_MISSING]: ...
evaluate_comparison_batch:   fail-closed -> engine evaluation [ENGINE_SPD_MISSING]: ...
settings.engine: {'numerics_id': ..., 'engine_version': '0.1.0.dev0', 'reference_mode': 'powersi-compatible',
                  'freq_grid_sha256': ..., 'decap_config_sha256': ...}
CHECK PASS
```


### 3-3. `tests/engine` 불변

```
python -m pytest tests\engine -q -k "datafree" -p no:cacheprovider
14 passed, 1 skipped, 59 deselected in 1.45s
```
`src/spd_pi_engine/`가 0줄 변경이므로 당연하지만, 게이트로 실행했다.


### 3-4. 제품 테스트 기준선 (W5 §4-3)

```
python -m pytest tests -q --ignore=tests\engine --ignore=tests\test_audit_source_l29_l30_port_window.py -p no:cacheprovider
31 failed, 2561 passed, 5 skipped, 260 errors in 372.61s (0:06:12)
```

| 실행 | failed | passed | skipped | errors | wall |
|---|---|---|---|---|---|
| W5 기준선(`work/engine_w5/product_clean.log`) | 31 | 2 556 | 1 | 260 | 433.59 s |
| W11-a 중간(`product_after2.log`) | **31** | 2 561 | 5 | **260** | 487.92 s |
| **W11-a 최종**(`product_final.log`, `services.py` 문구 수정 포함) | **31** | 2 561 | 5 | **260** | 372.61 s |

**게이트: FAILED/ERROR 노드 이름 집합 diff = 비어 있음(291개 완전 동일).**

```
diff w5_baseline_set.txt w11a_final.txt   ->  (출력 없음)
```

늘어난 5 passed = 새 데이터 없는 테스트 5건, 늘어난 4 skipped = 마커를 요청하지 않은
재현 테스트 4건. 260 errors는 기존 `tmp_path` 환경 문제 그대로다.

**중간에 한 번 깨뜨렸다가 되돌린 기록**: 첫 실행(`product_after.log`)은
`52 failed / 2 540 passed / 260 errors`로 **21건이 새로 실패**했다. 원인은
`scripts/benchmark_raw_spd_powersi_correlation.py` 수정 → 정확도 정책의
`benchmark_base_source.sha256` 불일치(`IntegrityError: benchmark base source hash does not
match policy`). 해당 스크립트를 원복하고 CLI를 `validate_real_distribution_replay.py`로
옮긴 뒤 재실행한 것이 위 결과다(§2-7, §5-1).


---

## 4. end-to-end 증거

제품 API만 사용한다: `spd_decap_pi.spd_adapter.import_spd_scenario`로 260729 SPD를 시나리오로
가져오고(약 2시간 12분, 1회. 결과를 `work/engine_w11/scenario_port18.json`에 캐시),
`spd_decap_pi.evaluation.evaluate_scenario(..., solver_profile="hybrid_plane_pair_v1")`로 평가한다.
스크립트 `work/engine_w11/e2e_port18.py`, 로그 `e2e.log`/`e2e2.log`, 결과 `e2e_port18.json`.

```
port Port18_SITE0 -> rail_net ADC_VDD_075_VTRIP_SRAM/0 -> rail_id ADC_VDD_075_VTRIP_SRAM/0
  [  5%] Extracting Port18_SITE0 from the SPD
  [ 20%] Building the plane-pair model for ADC_VDD_075_VTRIP_SRAM/0
  [ 40%] Solving tuned at 28 frequencies
  [ 99%] Writing the engine receipt
```

| 항목 | 값 |
|---|---|
| 평가 wall | **47.9 s** (`solver="auto"` → A2000 cuDSS, 추출 캐시 히트) |
| `solver_profile_key` / `badge` | `hybrid_plane_pair_v1` / `HYBRID` |
| `solver_version` | `spd-pi-engine-0.1.0.dev0-27d81996e38f` ← **numerics_id 앞 12자 포함** |
| `solver_provenance["numerics_id"]` | `27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0` |
| `solver_provenance["unknowns"]` | 275 218 (재현 테스트와 동일) |
| `frequency_hz` 점 수 | **28** (엔진 사다리, 제품 401점 sweep 아님) |
| `cap_count` / `model_count` | 421 / 3 |
| `confidence` | LOW |
| `receipt_sha256` | `5b6201bb6feb259ee9621493749a2d62149f40e26db8435666a65a034eabcc32` |
| `library_versions` | matplotlib 3.10.9, numpy 2.4.4, Pillow 12.2.0, scipy 1.18.0 |

**1 MHz에서 뷰의 Z == 엔진 영수증의 Z (비트 동일)**

```
view_Z_1MHz    = (0.0006922836603548646, -0.00041919592988476644)
receipt_Z_1MHz = (0.0006922836603548646, -0.00041919592988476644)
abs_diff_1MHz  = 0.0        |Z| = 0.0008093095168267104 ohm
```

**validity 5줄이 뷰에 그대로 들어간다**: `view.assumptions == receipt["validity"]["notes"]`
(스크립트가 `assumptions_equal_receipt_validity: true`로 확인).

```
PCB (s5m6585, held-out, 160 ports): 1 MHz err median 0.58 % ...
Package large-plane rails: err <= 3 % ...
Package overall: 1 MHz err median 31.7 %, Re Z_ref/Re Z_model @100 kHz median 1.46 ...
G4 f_res bias +5-14 % on every case ...
Microvias are modelled as copper-filled cylinders of drill diameter (owner decision D9, 2026-09-19) ...
```

`confidence_note`도 같은 5줄을 담는다(`evaluation_model_boundary_disclosure` 엔진 분기가
`_evaluation_view`에서 자동으로 이어 붙는다):

```
Model Coverage LOW | Hybrid plane-pair engine model boundary (spd_pi_engine, reference
powersi-compatible): 2-D plane-pair cavity plus lumped via/trace/decap circuit, solved from the
original PowerSI SPD on the engine's own 1 kHz-100 MHz ladder; single-rail Zii only, no
inter-rail/site coupling and no full-wave claim. Engine validity notes: <5줄> | Mixed reference
Signal$L14(MAIN_POWER4)/Signal$L13(DGND): overlap 98.88%, dominant 99.03%; the engine rasterizes
the actual artwork of this plane pair and picks the reference per cell (powersi-compatible); no
rectangular-return approximation is used (LOW geometry confidence)
```

마지막 문장은 **처음에 틀렸었다**. `_evaluation_view`의 혼합 참조 문구가 프로파일별
if/elif 사슬이고 엔진이 `else`(= "continuous rectangular-return approximation")로 떨어져,
엔진 결과에 사각형 캐비티라는 거짓 문장이 붙었다. 첫 e2e 실행(`e2e.log`/첫 `e2e_port18.json`)이
그것을 드러냈고, `services.py:4579-4586`에 엔진 분기를 추가한 뒤 재실행해(`e2e2.log`) 위 문구를
얻었다. 수치는 두 실행에서 같다(아래 각주).

각주 — 두 e2e 실행의 1 MHz Z가 마지막 자리에서 다르다
(`...3540457` vs `...3548646`, 상대차 1.2e-12). `solver="auto"`라 두 번 다 A2000 cuDSS로
풀렸고, 엔진 README §7이 적어 둔 cuDSS 수치 인수분해의 실행 간 비결정성이다.
GPU 계약(패키지 레일 CPU 대비 ≤ 1e-8)의 6 000분의 1 수준이라 게이트에 영향이 없다.
재현 테스트는 이 때문에 `solver="splu"`를 쓴다.


---

## 5. 계획에서 벗어난 점 / 판단이 필요한 것

### 5-1. CLI를 다른 스크립트로 옮김 (§2-7) — **소유자 판단 필요**

계획이 지목한 `scripts/benchmark_raw_spd_powersi_correlation.py`는 정확도 정책이 sha256을
고정한 동결 자산이다. 원복했고, `scripts/validate_real_distribution_replay.py`에 노출했다.
엔진 프로파일을 benchmark 러너로도 돌리려면 정책 JSON의 `benchmark_base_source.sha256`
갱신(= 승인 자산 변경)이 필요하다.

### 5-2. `EngineSolveRequest.threads` 필드 추가 (계획 §1-2 필드 목록에 없음)

`None` = `hardware.plan_threads`(계획 §1-5 그대로), `0` = 호출자 환경 상속. 후자가 없으면
§2-3이 요구하는 "워커 영수증 == 인프로세스 영수증" 비트 비교가 성립하지 않는다
(엔진 README §10-4: `OPENBLAS_NUM_THREADS`가 splu 마지막 비트를 2.7e-13 움직인다).
재현 테스트만 `threads=0`을 쓰고, 제품 경로는 기본값(`plan_threads`)이다.

### 5-3. 영수증 저장 위치/경로

워커는 `<request.json>.result.json`만 쓰고, 어댑터가 역할별 영수증을
`outputs/engine-receipts/`에 `unique_path`로 보존한다(계획 §1-6이 어댑터 책임으로 둔 것).
요청 JSON에 `out_path` 필드를 넣지 않았다. `outputs/`의 루트는 계획에 명시가 없어
`engine_cache_dir().parent`로 잡았다 →
`SPD_PI_ENGINE_CACHE`가 `...\work\engine_cache`면 `...\work\outputs\engine-receipts`,
기본값이면 `%LOCALAPPDATA%\SPD Decap PI Evaluator\outputs\engine-receipts`.

### 5-4. preflight는 3가지 (계획 §1-3은 "4가지"라고 쓰되 4번째는 워커 안이라고 함)

메모리 가드는 계획 §4대로 워커 안에 있다(`ENGINE_RAIL_TOO_LARGE`).

### 5-5. 메모리 가드의 미지수 추정 — 정밀하지 않음

`Rail.estimate_cost()`는 build 전이라 **미지수를 알려주지 않는다**(노드/via/trace 수만).
`max(n_rail_nodes, DEFAULT_UNKNOWNS)`를 `rss_per_process_MB`에 넣어 하한으로 쓴다.
"이 기계에 2 GB밖에 안 남았다"는 잡지만 10 % 오차는 못 잡는다. 코드에 `ponytail:` 주석으로
천장과 업그레이드 경로를 적어 두었다.

### 5-6. `preflight_evaluation_comparison` 분기 위치

계획은 `:2582`(= `resolve_solver_profile` 직후)를 지목했으나, 4줄 아래
`_canonical_rail_ids` 직후(현재 2608)에 넣었다 — 기존 정규화 결과를 그대로 쓰기 위해서다.
그 사이 4줄은 `alternate_cache`/`source_project`/`canonical_rails`뿐이라 부작용이 없다.

### 5-7. 계획의 앵커 오류 1건

§1-1의 "`evaluation.py:3565-3597`의 연구 증거 해시 요구"는 HEAD에서 trace RL 코드다.
실제 게이트는 `_evaluation_settings` 안의 **565-598**이다(3000줄 오차, 오타로 보인다).
결론(= `experimental=False`면 걸리지 않는다)은 그대로 맞다.

### 5-8. `SolverDiagnostics`를 0으로 채움

엔진은 모달 전개가 아니라 직접 희소 LU이고, 주파수별 조건수/잔차를 측정하지 않는다.
`mode_count=0`, `condition_numbers=0`, `relative_residuals=0`으로 두고 실제 크기/정체성
값(unknowns, numerics_id, backend)은 `solver_provenance`에 넣었다. **GUI가 이 필드를
"모드 수"로 표시한다면 W11-b에서 엔진 분기 문구가 필요하다.**

### 5-9. 뷰의 `cap_count`/`model_count`

`project.placements`가 아니라 엔진에 실제로 보낸 decap 구성에서 센다
(layerwise가 `_with_layerwise_mounted_view_counts`로 하는 것과 같은 이유).

### 5-10. 계획에 없던 가드 1개: `build_evaluation_workspace`

`rehydrate_scenario_evaluation`(AI 분석 경로)이 엔진 결과를 다시 수화할 때 아트워크/템플릿
빌더를 돌리지 않도록 3줄 가드를 넣었다. 없으면 엔진 결과에 대해 AI 분석이 비싼 경로를
타다가 실패한다.

### 5-11. 취소 granularity

`solve()`는 워커 stdout 줄 사이에서만 `is_cancelled`를 본다. 40 %→99 % 사이(= 실제 solve)
출력이 없으므로 그 구간은 중단되지 않는다. **W11-b의 "Cancel이 워커 종료" 게이트에서
워커에 하트비트를 추가하거나 부모가 타이머로 kill해야 한다.**

### 5-12. 사용자 SPICE 모델의 `.SUBCKT` 선택

`extra_models`는 `.lib` 원문 전체를 넘기고 엔진의 `add_decap_model`이 그 안의 단일
`.SUBCKT`를 파싱한다. 한 파일에 여러 개면 파서가 모호하다고 거절한다
(`cap_model_sources[*]["subckt_name"]`로 블록을 잘라내는 일은 하지 않았다 — W11-c 후보).
P18 케이스는 전부 SPD 내장 모델이라 이 경로를 타지 않는다.

### 5-13. 주파수 격자

`engine_frequencies()` = `ladder_freqs()` **28점**(참조 격자에 스냅하지 않음).
exp28 영수증의 격자는 PowerSI 참조에 스냅된 **27점**이라 재현 테스트는 영수증의 배열을
그대로 요청에 넣는다. 제품 평가(e2e)는 28점 격자를 쓴다.

### 5-14. GUI는 손대지 않았다 (W11-b)

`gui/main_window.py:160-167`의 `_SOLVER_PROFILE_ITEMS`는 그대로 3항목이다. 즉 새 프로파일은
**콤보 박스에 나타나지 않고** CLI/API로만 선택된다. 기본값은 layerwise 그대로이고
`tests/test_spd_decap_gui.py:462-483`의 콤보 기대치도 그대로다.

