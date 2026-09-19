# W11-e 보고서 — 제품 테스트 위생 (2026-09-20)

지시: 소유자 승인 범위 4건(A 어댑터 provenance, B GUI 대화상자 행(hang), C 세션
픽스처 누수, D 제품 스위트 재기준선). 선행: `docs/engine/W11B_REPORT.md` §5-4,
§7-1, §7-2, §7-3, §7-6.
저장소: `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator`, 브랜치
`claude/lightweight-hybrid-20260915`, 작업 시작 HEAD `a03efff`.

소유 파일만 수정했다(4개): `src/spd_decap_pi/_core/solver/engine_adapter.py`(+4),
`tests/test_engine_adapter_reproduction.py`(+6), `tests/test_spd_decap_gui.py`(+8),
`tests/test_research_av_bs1_boundary_schur_h4_p1.py`(+4/-1). 여기에 이 보고서와
`ENGINE_PLAN_2026-09-18.md` 3줄을 더했다. `src/spd_pi_engine/`, `gui/main_window.py`,
`accuracy_parse.py`, `scripts/benchmark_raw_spd_powersi_correlation.py`,
`docs/handoff/`, `tools/research-claude/`, untracked Codex 파일은 **0줄**이다.
물리·파라미터 변경은 없다.

핵심 결과: **ERROR 260 → 0**. W11-b §7-1의 "`tmp_path` 고갈은 누수된 세션 픽스처
때문"이라는 가설이 확인됐다. 동시에 그 260건이 **가리고 있던** 기존 실패 7건과
영구 행(hang) 3건이 드러났다.

---

## 1. 실행 환경

모든 pytest 실행에 공통으로 적용한 환경이다.

```
PYTHONUTF8=1  QT_QPA_PLATFORM=offscreen
SPD_PI_DATA_DIR=D:\Downloads\examples
SPD_PI_WORK_DIR=D:\Downloads\examples\analysis\claude-2026-09-15\work
SPD_PI_ENGINE_CACHE=D:\Downloads\examples\analysis\claude-2026-09-15\work\engine_cache
```

인터프리터는 `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe`
(pytest 9.0.3)다. 저장소의 `.venv`에는 pytest가 없다(`No module named pytest`).
로그는 전부 `WORK_DIR\engine_w11e\`에 있다.

---

## 2. Task A — provenance 기본값을 GUI에서 어댑터로 (W11B §7-6)

### 2-1. 변경

`src/spd_decap_pi/_core/solver/engine_adapter.py`, `evaluation_outcome()`의
`provenance` dict(`:539-564`)에 2키를 추가했다.

```python
"source_only": True,
# W11-e: these two defaults belong to the adapter, not to the GUI
# fallbacks in `gui/main_window.py` (W11-b report 7-6).
"status": "source_engine_hybrid",
"validation_status": "engine_validity_notes_bound",
"powersi_used_for_parameters": False,
```

값은 GUI가 지어내던 것과 **문자열이 동일**하다
(`gui/main_window.py:478-484`의 `provenance.get("status") or "source_engine_hybrid"`,
`provenance.get("validation_status") or "engine_validity_notes_bound"`).
지시대로 GUI 폴백은 그대로 두었다 — 이제 `or` 우변에 도달하지 않을 뿐이다.

`solver_provenance` 소비자는 전부 `.get()` 기반이고 키 화이트리스트로 거르는
곳은 없다(`evaluation.py:391-401, 434, 601, 2843-2854`). `evaluation.py:400-401`은
Original/Tuned의 dict 전체를 비교하는데 양쪽 모두 같은 어댑터가 만들므로 상수 2키
추가로 동치 판정이 바뀌지 않는다.

### 2-2. 테스트

`solver_provenance`를 들여다보는 기존 테스트는
`tests/test_engine_adapter_reproduction.py::test_outcome_carries_validity_and_numerics_id`
하나뿐이다(`tests/test_engine_scenario_mapping.py`는 `solver_provenance`를 보지
않는다). 새 파일도 `tmp_path`도 쓰지 않고 그 테스트에 단언을 붙였다.

```python
assert outcome.solver_provenance["status"] == "source_engine_hybrid"
assert (
    outcome.solver_provenance["validation_status"]
    == "engine_validity_notes_bound"
)
```

이 노드는 `engine_reproduction` 마커라 일반 제품 실행에서는 **skip**된다.
마커를 켠 실행 결과는 §6-4에 있다.

---

## 3. Task B — offscreen 동의 대화상자 행(hang) 제거 (W11B §7-2)

### 3-1. 원인 재확인(내 변경 전)

`tests/test_spd_decap_gui.py`를 HEAD 상태로 되돌려 단독 실행하면 영구 블록한다.
`faulthandler_timeout=45`, bash `timeout 90`:

```
python -u -m pytest "tests/test_spd_decap_gui.py::test_evaluation_worker_receives_scenario_model_attachments" \
  -p no:cacheprovider -o faulthandler_timeout=45 -q -rfE        # exit 124 (timeout)
```
```
Timeout (0:00:45)!
  File "...\src\spd_decap_pi\gui\main_window.py", line 7973 in _accept_evaluation_preflight
  File "...\src\spd_decap_pi\gui\main_window.py", line 7892 in <lambda>
  File "...\tests\test_spd_decap_gui.py", line 2099 in run_through_preflight
```
(`engine_w11e/taskB_hang_confirm.log`. 확인 후 편집본을 바이트 그대로 복원했고
`git diff --stat`으로 8 insertions만 남은 것을 재확인했다.)

`main_window.py:7973`은 "Run a partial Evaluation Analysis?" `QMessageBox`의
`prompt.exec()`다. 테스트가 만든 두 번째 레일 `RAIL_SECOND`가 preflight에서
블록되기 때문에 이 경로로 들어간다(§3-3).

### 3-2. 수정 (테스트 안에서만)

같은 파일의 기존 패턴(`:1976-1979`, `:2227-2231`의 `monkeypatch.setattr(QMessageBox,
"question", ...)`)을 따라 클래스 속성 `exec`를 패치했다. `main_window`와 테스트가
`PySide6.QtWidgets.QMessageBox` **같은 클래스 객체**를 import한다. `monkeypatch`라
함수 스코프에서 자동 복원되고 다른 테스트로 새지 않는다.

```python
monkeypatch.setattr(window, "_run_worker", capture)
# The second rail is blocked, so `_accept_evaluation_preflight` opens the
# "Run a partial Evaluation Analysis?" consent box.  Offscreen there is
# nobody to click it and `exec()` blocks forever (W11-b report 7-2).
monkeypatch.setattr(
    QMessageBox,
    "exec",
    lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
)
```

`main_window.py`는 0줄 변경이다.

### 3-3. 검증 — 행은 사라졌지만 **테스트는 여전히 실패한다**

```
python -u -m pytest "tests/test_spd_decap_gui.py::test_evaluation_worker_receives_scenario_model_attachments" \
  -p no:cacheprovider -o faulthandler_timeout=300 -q -rfE
1 failed in 5.38s          # engine_w11e/taskB_single.log
```

영구 블록 → **5.4 s 만에 종료**로 바뀌었다(300 s 창 안). 다만 지시가 기대한
"pass"는 아니다. 남은 실패는 내 패치가 만든 것이 아니라 패치가 **드러낸** 것이다.

```
tests\test_spd_decap_gui.py:2120: AssertionError
assert ('VDD_CORE/0',) == ('VDD_CORE/0', 'RAIL_SECOND')
```

제품 preflight를 직접 호출해 블로커를 찍어 보면:

```
BLOCKER rail_id='RAIL_SECOND' kind=UNRESOLVED
  reason='SOURCE_GRAPH_PROVENANCE_INVALID: selected rail has no matching
          rail-id/net plane-pair proof; re-import matching raw SPD'
```

테스트는 `base.rails[0]`를 복사해 `RAIL_SECOND`(net `VDD_SECOND`)를 **합성**하는데,
그 net에는 raw SPD 기원의 plane-pair 증명이 없다. 제품의 fail-closed 게이트가
정상 동작한 결과다. 히스토리도 일치한다 — 테스트의 `RAIL_SECOND`는 `c0b3723`
(2026-07-29)에서 들어왔고 `_accept_evaluation_preflight` 블로커 경로는 `b0aaaaf`
(2026-08-06)에서 들어왔다. 그 사이 이 노드는 전체 실행에서 `tmp_path` ERROR로
가려져 있어서 아무도 갱신하지 않았다.

**따라서 단언 2120·2124·2127(레일 2개 기대)을 고치는 것은 "제품 게이트가 맞고
테스트가 낡았다"는 판정이다. 소유자 판정 사항으로 남겼다(§7-1).**

### 3-4. GUI 파일 단독

```
python -u -m pytest tests/test_spd_decap_gui.py -p no:cacheprovider \
  -o faulthandler_timeout=300 -q -rfE
4 failed, 50 passed in 6.59s        # engine_w11e/gui_file_after.log
```

행 없이 끝난다. 4건 중 1건은 W5 기준선 FAILED
(`test_combined_convergence_text_and_gate_reject_frequency_only_failure`),
3건은 기준선에서 ERROR였던 노드다(§6-3 표).

---

## 4. Task C — 세션 스코프 autouse 픽스처 누수 (W11B §7-3)

### 4-1. 변경

`tests/test_research_av_bs1_boundary_schur_h4_p1.py:257`

```python
# W11-e: module scope, not session.  At session scope the guards below stayed
# installed for the rest of the run and every later test that needs a writable
# file -- including pytest's own `tmp_path` lock -- failed (W11-b report 7-3).
@pytest.fixture(scope="module", autouse=True)
def no_write_no_factor_and_repository_sentinel() -> object:
```

teardown은 이미 올바르다 — `finally: patch.undo()`(`:356-360`)가
`builtins.open`, `io.open`, `os.open`, `subprocess.run`, `os.*`, `Path.*`,
`scipy.sparse.linalg.{splu,spsolve,factorized}`를 전부 되돌린다. 손댈 필요가
없었고, 손대지 않았다. **바꾼 것은 `scope` 한 단어뿐**이다.

### 4-2. fail-closed 의도 보존 확인

동일 파일 단독 실행, 변경 전/후:

| | 명령 | 결과 |
|---|---|---|
| 전 | `pytest tests/test_research_av_bs1_boundary_schur_h4_p1.py -p no:cacheprovider -o faulthandler_timeout=300 -q -rfE` | **32 passed in 12.91 s** (`h4p1_before.log`) |
| 후 | 같은 명령 | **32 passed in 12.21 s** (`h4p1_after.log`) |

픽스처 **순서**도 확인했다. 같은 파일의 `wrapper`는 세션 스코프인데
(`:379`), 모듈 스코프 가드보다 나중에 생성되므로 `_run_cli`는 여전히
`guarded_subprocess_run`을 통과한다.

```
pytest ... --setup-show
line 1 : SETUP    M no_write_no_factor_and_repository_sentinel
line 16: SETUP    S wrapper
```

가드가 먼저다 → 모듈 안의 fail-closed 계약은 그대로다. `wrapper`는 건드리지 않았다.

### 4-3. 효과

전체 제품 실행의 **ERROR 260 → 0**(§6-1). W11-b §7-1의 가설이 확인됐다:
`OSError: could not create numbered dir ... after 10 tries`는 환경 문제가 아니라
이 픽스처가 `os.open`의 `O_CREAT`를 막고 있어서 pytest의
`make_numbered_dir_with_cleanup`이 10회 재시도 끝에 포기한 것이다.
알파벳 순서상 `tests/test_research_av_*` 뒤에 오는 `tests/test_spd_*`,
`tests/test_surface_*`, `tests/test_touchstone*`, `tests/test_validate_*`,
`tests/test_run_d11*`가 전부 피해자였다.

---

## 5. Task D — 제품 스위트 재기준선

### 5-1. 명령

W5 기준선과 같은 명령 형태를 썼다(W11B §6-3에 적힌 정확한 invocation).
지시의 `pytest tests`를 그대로 쓰면 **수집 단계에서 중단**된다 —
untracked Codex 파일 `tests/test_audit_source_l29_l30_port_window.py`가
`ModuleNotFoundError: No module named 'spd_decap_pi.source_plane_ownership_ir'`로
collection error를 낸다(W5 §4-3과 동일, 내 변경과 무관): `1 error in 4.71s`.
그 시도의 로그는 이후 실행이 같은 경로에 덮어썼다.

```
python -u -m pytest tests -q \
  --ignore=tests/engine \
  --ignore=tests/test_audit_source_l29_l30_port_window.py \
  -p no:cacheprovider -o faulthandler_timeout=300 -rfE \
  --deselect "tests/test_spd_decap_distribution_gui.py::test_detached_distribution_import_applies_targets_without_mutating_scenario" \
  --deselect "tests/test_spd_decap_distribution_gui.py::test_legacy_target_import_refreshes_present_requires_distance_and_invalidates_preview" \
  --deselect "tests/test_spd_decap_distribution_gui.py::test_current_target_import_restores_recorded_distance_mode"
```
로그: `engine_w11e/product_after_w11e.log`.

### 5-2. 첫 실행은 행(hang)했다 — 3건 deselect

`--deselect` 없는 첫 실행은 72 % 지점에서 영구 블록했고 300 s faulthandler가
지점을 찍었다(`product_after_w11e_attempt1_hung.log`).

```
Timeout (0:05:00)!
  File "...\src\spd_decap_pi\gui\main_window.py", line 4705 in _import_distribution_targets
  File "...\tests\test_spd_decap_distribution_gui.py", line 990 in
       test_detached_distribution_import_applies_targets_without_mutating_scenario
```

덤프 후에도 로그가 180 s 동안 1바이트도 자라지 않아 실행을 죽였다(orphan 없음을
`tasklist`로 확인). `main_window.py:4705`는 `_import_distribution_targets`의
`except (OSError, ValueError)` 분기에 있는 **`QMessageBox.critical(...)`**이다.
워크북 임포트가 실패 → 제품이 에러 모달을 띄움 → offscreen에서 아무도 닫지 않음.

같은 파일을 짧은 벽시계로 반복 실행해 같은 종류를 전부 찾았다(3건, 모두 기준선
ERROR 노드였고 셋 다 `import_distribution_targets_button.click()`을 거쳐 같은
모달에 걸린다):

| 행(hang) 노드 (`tests/test_spd_decap_distribution_gui.py`) | 블록 지점 |
|---|---|
| `test_detached_distribution_import_applies_targets_without_mutating_scenario` | `main_window.py:4705` `QMessageBox.critical` |
| `test_legacy_target_import_refreshes_present_requires_distance_and_invalidates_preview` | 〃 |
| `test_current_target_import_restores_recorded_distance_mode` | 〃 |

3건을 빼면 파일이 정상 종료한다: `2 failed, 48 passed, 3 deselected in 5.53s`.

모달을 띄우는 원인 예외는 §6-3의 spreadsheet 3건과 같다 —
`DistributionWorkbookError: legacy Distribution workbook requires re-export as
format 5 with source-proven target-layer transition metadata`. 즉 **기존 실패가
headless에서 모달로 나타난 것**이지 행 자체가 별개 버그는 아니다.

Qt 대화상자를 건드리는 테스트 파일은 3개뿐이라(`grep -ln "MainWindow\|QMessageBox\|QFileDialog" tests/*.py`)
나머지 파일에는 같은 종류의 행이 없다. 실제로 `test_spd_decap_gui.py`(§3-4)와
`test_spd_decap_gui_engine.py`(§6-4)는 행 없이 끝난다.

---

## 6. 결과

### 6-1. 전/후 표

| 실행 | failed | passed | skipped | deselected | errors | wall |
|---|---|---|---|---|---|---|
| W5 기준선 `engine_w5/product_clean.log` | 31 | 2 556 | 1 | 0 | **260** | 433.59 s |
| W11-b `engine_w11/product_w11b_final.log` | 31 | 2 573 | 5 | 0 | **260** | 401.92 s |
| **W11-e** `engine_w11e/product_after_w11e.log` | **25** | **2 836** | 5 | 3 | **0** | 403.06 s |

- **ERROR 260 → 0.** Task C의 가설이 맞았다.
- passed 2 573 → 2 836(+263). 대부분 ERROR였던 260건이 실제로 본문을 실행해
  통과한 것이다.
- FAILED 31 → 25: 기준선 FAILED 13건이 통과로 바뀌고(§6-2), 가려져 있던 기존
  실패 7건이 드러났다(§6-3).

노드 집합 비교(`engine_w5/product_clean.log` 대비):

```
baseline: 31 FAILED, 260 ERROR  (291 nodes)
new     : 25 FAILED, 0 ERROR    (25 nodes)
새로 생긴 ERROR: 없음
기준선 ERROR 중 여전히 ERROR: 0
```

### 6-2. 기준선 FAILED → 이제 통과 (13건)

전부 "쓰기가 필요한데 누수된 가드가 막고 있던" 테스트다.

```
tests/test_run_d116_shadow_fastercap.py::test_cli_help_is_cp949_safe
tests/test_run_d116_shadow_fastercap.py::test_cli_requires_manifest_approval_sha
tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_version_banner_and_positive_selfcheck_fixture
tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_normal_mode_requires_all_external_pins[arguments0..4]   (5건)
tests/test_run_d117_wp3_selected_pin_source_path_absence_once.py::test_selfcheck_exercises_create_new_inventory_timeout_and_capture_guards
tests/test_spd_decap_spd_adapter.py::test_retained_surface_artwork_is_boundary_inclusive_and_released
tests/test_surface_certificate_asset.py::test_compiled_topology_module_import_order_is_fresh_process_safe[양쪽 순서 2건]
tests/test_validate_powersi_accuracy.py::test_frozen_historical_validation_assets_remain_byte_identical
```

### 6-3. 기준선에 없던 새 FAILED (7건) — **전부 기준선 ERROR 노드**

기준선에 아예 없던 노드는 0건이다. 7건 모두 W5에서 `tmp_path` ERROR로 가려져
있던 **기존 실패**이고, 원인은 전부 내가 A–C에서 건드린 코드 밖이다.
지시대로 고치지 않았다.

| 노드 | 한 줄 원인 |
|---|---|
| `tests/test_spd_decap_gui.py::test_evaluation_worker_receives_scenario_model_attachments` | 테스트가 합성한 `RAIL_SECOND`가 preflight의 `SOURCE_GRAPH_PROVENANCE_INVALID`로 블록돼 워커에 레일이 1개만 간다 — 테스트 기대(2개)가 `b0aaaaf`의 블로커 게이트보다 낡았다(§3-3) |
| `tests/test_spd_decap_gui.py::test_layerwise_baseline_consent_and_capture_cover_unselected_board_rails` | `AttributeError: 'types.SimpleNamespace' object has no attribute 'evaluation_policy'` — 테스트가 만든 가짜 `request`에 `main_window.py:8068`이 읽는 필드가 없다 |
| `tests/test_spd_decap_gui.py::test_background_evaluation_preflight_uses_original_and_tuned_gate` | `TypeError: _job_preflight_evaluation() takes from 2 to 3 positional arguments but 4 ... were given` — 시그니처 변경에 테스트 호출이 안 맞춰졌다 |
| `tests/test_spd_decap_distribution_gui.py::test_applied_distribution_reloads_clear_into_layerwise_evaluation_workers` | `DistributionError: exact source-proven target-layer transition evidence is required before Distribution planning`(`distribution.py:3954`) |
| `tests/test_spd_decap_spreadsheet_export.py::test_distribution_workbook_optionally_writes_candidate_audit_sheet` | `DistributionWorkbookError: legacy Distribution workbook requires re-export as format 5 with source-proven target-layer transition metadata`(`distribution_workbook.py:508`) |
| `tests/test_spd_decap_spreadsheet_export.py::test_writer_refuses_the_policy_penalty_pairings_the_loader_rejects` | 〃 |
| `tests/test_spd_decap_spreadsheet_export.py::test_failed_export_leaves_the_previous_workbook_intact` | 〃 |

독립성 확인: `pytest tests/test_spd_decap_spreadsheet_export.py` 단독에서도
`3 failed, 7 passed in 1.26s`로 같은 3건이 깨진다(`spreadsheet_alone.log`).
`test_spd_decap_gui.py` 3건은 §3-4의 단독 실행에서 같은 원인으로 깨진다.
`test_spd_decap_distribution_gui.py` 1건은 §5-2의 단독 실행에서 같다.
어느 것도 `engine_adapter.py`·`test_engine_adapter_reproduction.py`·
`test_research_av_bs1_boundary_schur_h4_p1.py`와 접점이 없다.

나머지 18건은 W5 기준선 FAILED 그대로다(h2_p1 12, benchmark_raw 2,
build_source_l29_l30 1, distribution_gap_policy 1, distribution_gui 1, gui 1).

### 6-4. 엔진 테스트 3파일

```
python -u -m pytest tests/test_engine_adapter_reproduction.py \
  tests/test_engine_scenario_mapping.py tests/test_spd_decap_gui_engine.py \
  -p no:cacheprovider -o faulthandler_timeout=300 -q -rfE
17 passed, 4 skipped in 2.42s        # engine_w11e/engine_three_files.log
```

skip 4건은 `engine_reproduction` 마커 노드다. Task A의 새 단언이 실제로 도는
마커 실행(260729 Port18, 275 218 unknowns, CPU splu, 추출 캐시 웜):

```
python -u -m pytest tests/test_engine_adapter_reproduction.py -p no:cacheprovider \
  -o faulthandler_timeout=3600 -q -rfE -m engine_reproduction
4 passed, 5 deselected in 1113.05s (0:18:33)   # engine_w11e/engine_reproduction.log
```

`test_outcome_carries_validity_and_numerics_id`가 **실제 영수증**으로
`solver_provenance["status"] == "source_engine_hybrid"`,
`["validation_status"] == "engine_validity_notes_bound"`를 통과한다.
나머지 3건(워커=in-process 동일, 영수증 보존·해시, `numerics_id`/`unknowns`)도
그대로 통과하므로 2키 추가는 수치·영수증에 영향이 없다.

---

## 7. 판단 필요 (고치지 않은 것)

### 7-1. `test_evaluation_worker_receives_scenario_model_attachments`의 단언이 낡았다

§3-3. 행은 없앴지만 테스트는 여전히 실패한다. 선택지는 둘이다.

- (a) 제품이 맞다고 보고 테스트를 `('VDD_CORE/0',)` 한 레일로 고친다 — 다만
  이 테스트가 원래 검증하던 "선택한 레일이 조용히 누락되지 않는다"가 약해진다.
- (b) 합성 레일도 통과하도록 픽스처를 raw SPD 증명이 있는 레일로 바꾼다 —
  `MINI_SPD`에 두 번째 실제 PWR net을 넣어야 해서 픽스처 자산 변경이다.

어느 쪽도 "제품 게이트 vs 테스트 자산" 판정이라 소유자 몫으로 남겼다.

### 7-2. 모달로 영구 블록하는 3건을 `--deselect`했다

§5-2. `_import_distribution_targets`의 실패 경로가 offscreen에서 닫히지 않는
`QMessageBox.critical`을 띄운다. 근본 원인은 워크북 format 5 요구(§6-3)이고,
행은 그 실패가 headless에서 드러나는 방식이다. 두 가지 중 하나가 필요하다.

- 테스트 쪽: 세 테스트에 §3-2와 같은 `QMessageBox.exec` 패치를 넣는다
  (그러면 행 대신 명시적 FAILED가 된다).
- 제품 쪽: 헤드리스에서 모달을 띄우지 않는 경로. 이건 `main_window.py` 변경이라
  내 범위 밖이다.

지시가 B의 패치를 "그 테스트 안에서만"으로 못박았으므로 확장하지 않았다.

### 7-3. 새 게이트 기준

이제 "W5 기준선 291노드와 동일"은 더 이상 쓸 수 없다(ERROR 260이 사라졌다).
새 게이트는 **`25 FAILED + 3 deselected` 집합 동일**이다. 이 25건 중 18건은
W5 기준선 FAILED 그대로이고 7건은 새로 드러난 기존 실패다. 7건을 고칠지,
기준선으로 받아들일지는 소유자 결정이다.

### 7-4. `tests/test_audit_source_l29_l30_port_window.py`

untracked Codex 파일이고 import 대상 모듈이 트리에 없어 `pytest tests`를
수집 단계에서 중단시킨다. 지시에 따라 건드리지 않고 W5와 같은 `--ignore`로
제외했다. 이 파일이 정리되기 전까지 제품 명령에는 `--ignore`가 계속 필요하다.

### 7-5. `.venv`에 pytest가 없다

`CLAUDE.md` §실행은 `.\.venv\Scripts\Activate.ps1` 후 `pytest tests`를 안내하지만
현재 `.venv`에는 pytest가 설치돼 있지 않다. 시스템 Python 3.12.10 / pytest 9.0.3으로
돌렸다(W5·W11-b도 같은 인터프리터로 보인다 — 기준선 로그의 경로가 같다).
`pip install`은 금지라 손대지 않았다.

### 7-6. 모듈 스코프도 "누수 0"은 아니다

가드는 이제 자기 모듈 끝에서 풀리지만, 같은 모듈 안에서 실행되는 동안에는 여전히
전역(`builtins.open` 등)을 막는다. `-p xdist`나 실행 순서 변경으로 이 모듈이
다른 모듈과 인터리브되면 같은 문제가 재발할 수 있다. 근본적으로는 함수 스코프
+ `monkeypatch`가 맞지만, 그러면 세션 sentinel(`_repository_sentinel()` 전/후 비교)의
의미가 바뀌므로 최소 변경(`scope` 한 단어)에서 멈췄다.

---

## 8. 로그 경로

전부 `D:\Downloads\examples\analysis\claude-2026-09-15\work\engine_w11e\`:

| 파일 | 내용 |
|---|---|
| `h4p1_before.log` / `h4p1_after.log` | Task C 전/후 단독 (32 passed 동일) |
| `taskB_hang_confirm.log` | Task B 패치 **없이** 행 재현(faulthandler 45 s 덤프) |
| `taskB_single.log` | Task B 패치 후 단독 (1 failed in 5.38 s) |
| `gui_file_after.log` | `tests/test_spd_decap_gui.py` 단독 (4 failed, 50 passed) |
| `distgui_probe1/2.log`, `hangprobe.log`, `hangs_*.txt` | 모달 행 3건 탐색 |
| `spreadsheet_alone.log` | spreadsheet 3건이 단독에서도 깨짐 |
| `engine_three_files.log` | 엔진 3파일 (17 passed, 4 skipped) |
| `engine_reproduction.log` | `-m engine_reproduction` 실행 |
| `product_after_w11e_attempt1_hung.log` | deselect 없는 첫 전체 실행(행) |
| `product_after_w11e.log` | **최종 전체 실행** (25 failed, 2 836 passed, 0 errors) |
| `baseline_failed.txt` / `baseline_error.txt` / `after_failed.txt` | 노드 집합 비교 입력 |
