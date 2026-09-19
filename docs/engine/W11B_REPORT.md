# W11-b 보고서 — GUI의 엔진 프로파일 통합 (2026-09-19)

계획: `docs/engine/W11_PLAN_2026-09-19.md` §1, §4, §5, §6 행 **W11-b**.
선행: `docs/engine/W11A_REPORT.md`(프로파일 2개·어댑터·워커·`evaluation.py` 분기).
저장소: `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator`, 브랜치
`claude/lightweight-hybrid-20260915`. 작업 시작 시 HEAD는 `5cbed5a`였고 작업 중
다른 에이전트가 `db3a684`(W11-c/W11-d)를 커밋했다. 줄 앵커와 게이트는 모두
`db3a684` + 내 미커밋 변경 기준이다(**나는 커밋하지 않았다**).

소유 파일만 수정했다: `src/spd_decap_pi/gui/main_window.py`,
`src/spd_decap_pi/_core/solver/engine_adapter.py`(`solve()`의 진행률/취소 배선만),
`src/spd_decap_pi/_core/solver/engine_worker.py`(하트비트 출력만),
`tests/test_spd_decap_gui.py`(확장), `tests/test_spd_decap_gui_engine.py`(신규),
이 보고서. `evaluation.py`·`evaluation` 테스트(W11-c), PyInstaller 스펙·빌드
스크립트·`gui_launcher.py`(W11-d), `src/spd_pi_engine/`(0줄), `tools/research-claude/`는
건드리지 않았다.

> 같은 체크아웃에서 W11-c/W11-d가 동시에 작업했고 작업 중 `db3a684`로 커밋되었다
> (`gui_launcher.py`의 `MPLBACKEND=Agg` 포함). 아래 diff 집계는 `db3a684` 대비
> **내 미커밋 변경만**이다.

핵심 결과:

1. 콤보 5항목, 기본값 `layerwise_admittance_v1` 불변.
2. 엔진 결과가 예외 없이 렌더된다 — **계획에 없던 실제 블로커 1건을 발견해 고쳤다**:
   엔진 결과는 `convergence`가 `None`이라 `_rejected_comparison_convergence`의
   fail-closed 게이트에 걸려 **모든 엔진 배치가 무조건 거부**되고 있었다(§2-4).
3. Cancel이 워커 프로세스를 실제로 죽인다(리더 스레드 + 0.2 s 폴링 + `kill()`).
4. 워커가 2 s마다 하트비트 PROGRESS를 찍어 긴 구간에도 진행 표시가 멈추지 않는다.

---

## 1. 변경 요약

| 파일 | +/- | 내용 |
|---|---|---|
| `src/spd_decap_pi/gui/main_window.py` | +301 / -21 | 콤보 항목·화이트리스트·배지/문구·모달 프리셋 비활성·1회 고지·수렴 게이트 예외 |
| `src/spd_decap_pi/_core/solver/engine_adapter.py` | +31 / -7 | `solve()`의 stdout 리더 스레드 + 타이머 취소 폴링 |
| `src/spd_decap_pi/_core/solver/engine_worker.py` | +37 / -2 | stdout 락, `_write`, 2 s 하트비트 |
| `tests/test_spd_decap_gui.py` | +19 / -0 | 콤보 5항목·엔진 2키 라벨 기대치 추가 |
| `tests/test_spd_decap_gui_engine.py` | **신규 428줄** | 엔진 GUI 테스트 8건 |

---

## 2. `gui/main_window.py` — 줄 앵커

계획이 인용한 HEAD 줄번호를 먼저 재검증했다: `160-167`(프로파일 목록),
`293-310`(provenance 화이트리스트), `2575-2579`(기본 선택), `5570-5608`(워커 시작),
`6867-6906`(선택/상태 문구), `7619-7625`(preflight 워커),
`7799-7815`(평가 워커/접두사), `7957-7958`(confidence note 렌더링) — **전부 맞았다**.
아래는 변경 후 현재 트리의 줄번호다.

### 2-1. 프로파일 목록과 상수 (계획 §6 "프로파일 목록")

| 줄 | 내용 |
|---|---|
| 161-162 | `_HYBRID_SOLVER_PROFILE_KEY`, `_HYBRID_GND_SOLVER_PROFILE_KEY` |
| 163-166 | `_ENGINE_SOLVER_PROFILE_KEYS`, `_ENGINE_SOLVER_PROFILE_BADGES` |
| 167-182 | `_SOLVER_PROFILE_ITEMS` **5항목**(엔진 2개는 뒤에 추가, layerwise가 그대로 첫 항목) |
| 184-195 | `_ENGINE_FIRST_USE_NOTICE`(계획 §5의 4가지: 원본 SPD 필요·첫 추출 25-30 s·캐시 경로·validity/격자) |
| 2724-2725 | 콤보 채우기(`for label, key in _SOLVER_PROFILE_ITEMS`) — **손대지 않음** |
| 2727-2731 | 기본 선택 `APPLICATION_DEFAULT_SOLVER_PROFILE_KEY` — **손대지 않음** |
| 2739-2744 | 콤보 툴팁에 엔진 2항목 설명 추가 |

### 2-2. provenance 화이트리스트/배지 (계획 §6 "provenance 화이트리스트")

| 줄 | 내용 |
|---|---|
| 242 | `_SolverProvenancePresentation.numerics_id`(신규 필드, 기본값 `""`) |
| 265-276 | `banner_text`: 엔진은 `compiler … / <engine_version> · numerics <12자>…` |
| 277-286 | `banner_text`의 엔진 설명 `spd_pi_engine 2-D plane-pair + circuit hybrid (direct sparse LU)` |
| 260-262 | `validation_status` 표기 `engine_validity_notes_bound` → "bounded by the engine receipt validity notes" |
| 297-307 | `details_text` 엔진 분기(배너 + compiler id + engine version + **numerics_id 전체** + static sha) |
| 357-363 | 프로파일 키 화이트리스트에 `*_ENGINE_SOLVER_PROFILE_KEYS` |
| 364-403 | label/badge/expected_badge 결정에 엔진 분기(배지는 프로파일에서 가져옴 → HYBRID / HYBRID-GND) |
| 438-490 | **엔진 provenance 게이트**: `profile_key`/`profile_badge`/`source_only is True`/`powersi_used_for_parameters is False`/`compiler_algorithm_id` 일치 + `numerics_id`·`solver_static_identity_sha256`·`spd_sha256` 64자리 hex + static sha가 프로파일 것과 일치. 통과하면 `_SolverProvenancePresentation`을 즉시 반환 |

`receipt_sha256`은 **의도적으로 presentation에 넣지 않았다**. Original과 Tuned는 영수증이
다르므로, 넣으면 `_comparison_solver_provenance`(631-694)의 "배치 내 단일 솔버 정체성"
동등성 비교가 실패해 정상 배치가 거부된다. 배치 단위로 비교 가능한 정체성은
`numerics_id`이고, 영수증 해시는 `view.solver_provenance`에 그대로 남아 있다.

### 2-3. 모드 수를 표시하지 않는다 (지시 2)

| 줄 | 내용 |
|---|---|
| 1295-1307 | `_modal_convergence_text` 엔진 분기 — `SolverDiagnostics`의 0 채움(`mode_count=0`)을 **"0 modes"로 그리지 않고** `Direct sparse LU (no modal expansion); frozen 28-point engine ladder to 100 MHz; no modal or frequency-refinement sweep`를 반환 |
| 8302-8305 | 배치 요약의 "Numerical convergence preset" 줄을 엔진일 때 "not applicable …"로 교체 |

`SolverDiagnostics`의 나머지 필드(`max_condition_number`, `max_relative_residual`)는
GUI가 읽는 곳이 없다(`grep solver_diagnostics src/spd_decap_pi/gui` = 0건).
`unknowns`/`backend`/`numerics_id` 같은 실제 크기·정체성 값은 `solver_provenance`에 있고
배너/details에 나온다.

### 2-4. **계획에 없던 실제 블로커 1건 — 수렴 게이트** (지시 2 "예외 없이 렌더")

`_accept_evaluation`은 결과를 그리기 전에 `_rejected_comparison_convergence`(1388-1440)로
**모든 Original/Tuned 결과가 수렴을 보고했는지** 확인하고, 하나라도 아니면 배치 전체를
거부한다(현재 8007행). 게이트는 `_combined_converged`(1379)이고 이것은
`isinstance(convergence, dict)`를 요구한다.

엔진 결과의 `EvaluationOutcome.convergence`는 `None`이다(엔진은 모달 차수 스윕도
주파수 세분화 스윕도 하지 않는다). 즉 **엔진 배치는 100 % 거부**되고 사용자는
"Rejected nonconverged evaluation batch"만 보게 된다. W11-a의 e2e는
`evaluate_scenario` 단일 호출만 했기 때문에 이 경로를 지나지 않아 드러나지 않았다.

수정(1401-1410): 게이트의 뷰 루프 첫머리에서 엔진 프로파일이면 `continue`.
증상이 난 호출자 한 곳이 아니라 **모든 호출자가 지나는 공유 게이트 함수**에서 한 번
빠져나간다. 근거 주석도 같은 자리에 달았다 — 엔진의 정확도 경계는 수렴 델타가 아니라
영수증 validity 노트이고, 그것은 모든 엔진 결과의 `assumptions`/`confidence_note`에 있다.

회귀 테스트: `test_engine_batch_keeps_one_identity_and_clears_the_convergence_gate`.

### 2-5. 선택/상태 문구 (계획 §6 "선택", "상태 문구")

| 줄 | 내용 |
|---|---|
| 7087-7096 | `_selected_evaluation_solver_profile` 화이트리스트에 엔진 2키 |
| 7098-7115 | `_update_evaluation_solver_profile_help` 엔진 분기(배지·참조 모드·원본 SPD 필요·28 pt 격자·direct LU·validity, 보라색 `#c98fd6`) |
| 7168-7175 | `_update_evaluation_notes` 첫 줄 `Selected physics model: [HYBRID(-GND)] …` |
| 7185-7195 | `cache_note` — 엔진 Original은 시나리오 베이스라인 캐시에 쓰지 않는다(W11-a가 캐시를 끔) |
| 7198-7208 | `profile_note` — 원본 SPD/sha256, 레일당 SPD 포트 1개, 교차-net 거부, 28점 격자, 프리셋 미적용 |
| 7232-7236 | `convergence_note` — "엔진은 모달 기저도 세분화 스윕도 없다" |
| 8302-8305 | 결과 배치 요약의 프리셋 줄 |

`_update_evaluation_notes` 끝은 `evaluation_model_boundary_disclosure(profile_key)`를
그대로 붙인다 → 엔진일 때 `_core/services.py:258-276`의 엔진 분기가
`spd_pi_engine.receipt.VALIDITY_NOTES` 5줄을 그대로 인용한다(W11-a §2-5). 즉
**선택만 해도 노트 패널에 validity 5줄이 보인다**.

### 2-6. 모달 프리셋 비활성 (지시 1)

| 줄 | 내용 |
|---|---|
| 2354-2356 | `_engine_notice_shown`, `_modal_control_tooltips`, `_evaluation_controls_enabled` 초기화 |
| 5602-5603 | `_set_loaded_state` 끝에서 기준 상태 기록 + 재적용 |
| 5644-5645 | `_set_busy` 끝에서 동일 |
| 7030-7042 | `_evaluation_solver_profile_changed`: 제어 게이트를 **맨 앞**으로(§7-8) |
| 7046-7066 | `_update_engine_profile_controls` — `evaluation_modal_preset_combo`, `evaluation_alternate_pair_checkbox`를 `기준 상태 and not 엔진`으로 설정하고 툴팁을 교체/복원 |

`setEnabled(False)`만 하면 layerwise로 되돌렸을 때 다시 켜지지 않는다. 그래서
`_set_loaded_state`/`_set_busy`가 쓰던 "로드됨 그리고 바쁘지 않음" 기준값을
`_evaluation_controls_enabled`에 기록하고 헬퍼가 그것과 AND 한다. 원래 툴팁은
`_modal_control_tooltips`에 최초 1회만 저장해 복원한다(비활성 문구가 눌러붙지 않게).

### 2-7. 1회 고지 (계획 §5, 지시 1 "non-blocking")

| 줄 | 내용 |
|---|---|
| 7068-7085 | `_show_engine_first_use_notice` — 세션당 1회, `evaluation_summary`(읽기 전용 텍스트 패널) + 상태줄에 쓴다 |

`QMessageBox`를 쓰지 않는다(지시: non-blocking). 캐시 경로는
`engine_adapter.engine_cache_dir()`를 **함수 안에서 지연 import**해 넣는다
(`engine_cache_dir()`는 경로만 만들고 파일시스템을 건드리지 않는다, W11-a §2-2).
두 번째 엔진 프로파일을 골라도 다시 뜨지 않는다.

### 2-8. 워커 접두사·실패 문구 (계획 §6 "접두사")

| 줄 | 내용 |
|---|---|
| 8071-8091 | 평가 시작 요약의 접두사 `HYBRID · spd_pi_engine subprocess · …`와 "frozen 28-point engine ladder (direct sparse LU; …)" — 엔진일 때 모달 프리셋 이름을 쓰지 않는다 |
| 5812-5826 | `_evaluation_worker_error` 엔진 분기 — 폴백 없음을 명시하고 SPD 경로/sha256, 레일↔포트 1:1, 교차-net 재할당을 점검하라고 안내 |
| 8221-8226 | 결과 표의 베이스라인 열: 엔진은 `Transient / not cached` |
| 8255-8268 | 요약의 baseline/save 문구: 엔진은 세션 한정, 영수증 JSON만 보존 |

---

## 3. 주파수 축 (지시 2)

엔진 격자는 `ladder_freqs()` 28점(1 kHz–100 MHz)이고 제품 sweep은 401점(–1 GHz)이다.
GUI 플롯 경로를 통독한 결과 **고정 축 범위가 없다**:

- `gui/comparison_plot.py`는 `view.frequency_hz`를 그대로 `plot()`에 넘기고
  (244/264/271행) `enableAutoRange()`+`autoRange()`로 축을 잡는다(294-295행).
  `setXRange`는 사용자 상태 복원 경로(292행)에서만 쓰인다.
- 타깃 곡선(530-545행)은 상수 타깃이면 `[min, max]` 2점으로 축약한다 — 격자 길이와 무관.
- 결과 표의 1/10/100 MHz 열은 `log_log_interpolate_impedance`로 보간하고
  범위 밖이면 `N/A`를 낸다(`gui/results_window.py:67-125`). 엔진 격자는 100 MHz가
  마지막 점이므로 세 열 모두 값이 나온다.

회귀 테스트: `test_engine_ladder_plots_all_28_points_up_to_100_mhz` —
28점 뷰로 `MultiRailComparisonPlot.set_comparisons`를 돌려 Original/Tuned 곡선이
28점 그대로 들어갔는지 확인한다.

---

## 4. 취소 메커니즘 (지시 3)

### 4-1. 문제 (W11-a §5-11)

`engine_adapter.solve()`는 `for line in process.stdout:`로 돌았다. 워커는 40 %에서
99 % 사이(= 실제 build + solve, P18 CPU 기준 수백 초)에 아무것도 찍지 않으므로
그 구간에서 부모는 `stdout.readline()`에 블록돼 있고 `is_cancelled`를 **한 번도 보지 않는다**.
Cancel을 눌러도 워커는 끝까지 돈다.

### 4-2. 수정 — `engine_adapter.py:336-370`

```
스레드 A (_drain)         : for raw in process.stdout: queue.put(raw)   … EOF면 put(None)
스레드 B (solve 본체)      : while True:
                              if cancelled(): process.kill(); wait(); raise RuntimeError
                              try: line = queue.get(timeout=0.2)     ← _CANCEL_POLL_S (62행)
                              except queue.Empty: continue
                              if line is None: break
                              … PROGRESS 파싱 …
```

- 파이프는 리더 스레드가 계속 비운다(원래도 비워야 했다). 부모는 **stdout과 무관하게**
  최대 0.2 s 안에 취소를 본다.
- `FunctionWorker`(`gui/worker.py:60-102`) 스레드 구조는 그대로다. Cancel →
  `FunctionWorker.cancel()` → `is_cancelled()` True → 0.2 s 내 `kill()`.
- `Popen.kill()`은 Windows에서 이미 죽은 프로세스의 `ERROR_ACCESS_DENIED`를
  CPython이 처리한다(`subprocess.Popen.terminate`) → 추가 가드 불필요.

### 4-3. 하트비트 — `engine_worker.py:30-63, 161`

`_start_heartbeat()`가 데몬 스레드를 띄워 2 s(`HEARTBEAT_INTERVAL_S`)마다
**마지막 PROGRESS 단계를 경과 시간과 함께 다시 찍는다**:

```
PROGRESS 20 Building the plane-pair model
PROGRESS 20 Building the plane-pair model (0 s elapsed)
PROGRESS 20 Building the plane-pair model (1 s elapsed)
PROGRESS 40 Solving tuned at 28 frequencies
PROGRESS 40 Solving tuned at 28 frequencies (1 s elapsed)
```

(`HEARTBEAT_INTERVAL_S=0.3`으로 낮춘 실측.) 하트비트 스레드와 본 스레드가 stdout을
공유하므로 `print`(텍스트+개행 2회 write) 대신 락을 건 단일 `sys.stdout.write`
(`_write`, 37-42행)를 쓴다 — 안 그러면 PROGRESS 줄이 섞여 깨질 수 있다.
취소는 하트비트에 의존하지 않는다(부모가 타이머로 죽인다). 하트비트는 순전히
진행 표시가 멈춰 보이지 않게 하는 용도다.

### 4-4. 진행률

`evaluation.py:4876-4892`(W11-a)가 이미 레일별로 `5 + 95*index/n` 구간에 매핑해
`report(...)`로 넘긴다 → `FunctionWorker`의 `_CoalescedProgress`(75 ms) →
`MainWindow._worker_progress` → `progress_bar.setValue` + `status_text`.
W11-b에서 추가 배선은 필요 없었다(확인만 했다).

---

## 5. 테스트

### 5-1. `tests/test_spd_decap_gui_engine.py`(신규 8건)

`tmp_path` 미사용, 데이터 불필요. 취소 테스트만 `SPD_PI_ENGINE_CACHE`/`SPD_PI_WORK_DIR`가
없으면 skip한다.

| 테스트 | 무엇을 막는가 |
|---|---|
| `…combo_offers_both_engine_profiles_without_moving_the_default` | 콤보 5항목, 기본값 `layerwise_admittance_v1`, 엔진 2키 라벨 |
| `…disables_modal_presets_and_notices_once` | 엔진 선택 시 프리셋/대체 페어 비활성 + 툴팁, 고지 1회, 되돌리면 재활성 |
| `…notes_replace_the_modal_preset_and_cache_wording` | 노트 패널 첫 줄 `[HYBRID]`, 프리셋/캐시 문구, validity 인용 |
| `…renders_a_badge_and_never_a_mode_count` | HYBRID/HYBRID-GND 배지, numerics_id, `"modes"` 미출현 |
| `…keeps_one_identity_and_clears_the_convergence_gate` | §2-4 회귀(배치 거부) + Original/Tuned 단일 정체성 |
| `…boundary_disclosure_quotes_the_engine_validity_notes` | 두 프로파일 모두 `VALIDITY_NOTES` 전 줄 포함 |
| `…ladder_plots_all_28_points_up_to_100_mhz` | 28점 격자가 플롯에 그대로 |
| `test_cancel_kills_the_engine_worker_process` | `_worker_argv`를 `python -c "import time; time.sleep(60)"`로 바꿔, Cancel 후 **3 s 안에** `solve()`가 `RuntimeError("evaluation cancelled")`로 돌아오고 `Popen.poll()`이 `None`이 아님 |

### 5-2. `tests/test_spd_decap_gui.py:462-502` 확장

기존 기대치(기본값/레거시/리서치 라벨)는 그대로 두고 `count() == 5`와
엔진 2키의 라벨을 추가했다.

### 5-3. 부수 검증 (영수증 로그)

- `work/engine_w11/` 스크립트로 `solve()` 정상 경로(취소 아님)를 확인:
  PROGRESS 2줄이 순서대로 콜백에 도달하고 영수증이 보존·해시된다
  (`progress [(5, 'Extracting'), (40, 'Solving tuned … (2 s elapsed)')]`, `HAPPY PASS`).
- 하트비트 실측 출력은 §4-3.

### 5-4. 누수된 세션 픽스처에 대한 방어 (§7-3)

`tests/test_research_av_bs1_boundary_schur_h4_p1.py:255-378`은 **세션 스코프
autouse** 픽스처로 `builtins.open`(쓰기), `io.open`, `os.open`,
`os.{remove,unlink,rename,replace,mkdir,makedirs,rmdir}`,
`Path.{write_text,write_bytes,touch,mkdir,unlink,rename,replace,rmdir}`,
`subprocess.run`, `scipy.sparse.linalg.{splu,spsolve,factorized}`를 fail-closed
스텁으로 바꾸고 **세션 teardown에서야** 되돌린다. 알파벳순으로 그 뒤에 오는
`tests/test_spd_decap_*`는 전부 "파일 쓰기 금지" 상태에서 돌게 된다.

처음 전체 실행에서 내 테스트 2건이 이것 때문에 깨졌다:

- `…disables_modal_presets_and_notices_once`: `_update_evaluation_notes`가
  `evaluation_model_boundary_disclosure` → `spd_pi_engine` **첫 import** →
  matplotlib이 `~/.matplotlib`를 `mkdir` → 금지 스텁이 `AssertionError`. Qt 시그널
  핸들러 안이라 예외가 stderr로만 찍히고 이후 단계가 통째로 건너뛰어졌다.
- `test_cancel_kills_the_engine_worker_process`: `solve()`의
  `receipt_dir.mkdir(...)`가 같은 이유로 실패.

대응 2가지:

1. `tests/test_spd_decap_gui_engine.py` 맨 위에서 **import 시점**(= 컬렉션,
   어떤 픽스처보다 먼저)에 진짜 콜러블들을 스냅샷해 두고, 이 모듈 전용
   autouse `monkeypatch` 픽스처로 복원한다. 함수 스코프라 이 모듈 밖으로 새지 않는다.
2. `_evaluation_solver_profile_changed`의 **호출 순서**를 바꿨다(§7-8):
   제어 비활성 + 1회 고지가 먼저, `spd_pi_engine`를 import하는 노트 렌더링이 마지막.
   엔진 패키지 import가 실패하는 설치에서도 모달 프리셋 비활성은 반드시 적용된다.

확인:

```
python -m pytest tests\test_research_av_bs1_boundary_schur_h4_p1.py tests\test_spd_decap_gui_engine.py -q -p no:cacheprovider
40 passed in 17.04s
```

---

## 6. 게이트

### 6-1. 엔진 GUI 테스트 (신규 파일 단독)

```
python -m pytest tests\test_spd_decap_gui_engine.py -q -p no:cacheprovider
8 passed in 2.51s
```

### 6-2. 지시의 GUI 명령

```
python -m pytest tests\test_spd_decap_gui.py tests\test_spd_decap_gui_engine.py -q -p no:cacheprovider
  --deselect tests/test_spd_decap_gui.py::test_evaluation_worker_receives_scenario_model_attachments
3 failed, 58 passed, 1 deselected in 7.32s
```

- deselect 1건의 이유는 §7-2(**W11-b와 무관한 사전 존재 행**, HEAD에서 재현 확인).
- 남은 3 failed는 **전부 기준선 FAILED/ERROR 집합에 이미 있는 노드**다(기계 확인):

| 노드 | 기준선에 존재 |
|---|---|
| `test_layerwise_baseline_consent_and_capture_cover_unselected_board_rails` | 예(ERROR) |
| `test_background_evaluation_preflight_uses_original_and_tuned_gate` | 예(ERROR) |
| `test_combined_convergence_text_and_gate_reject_frequency_only_failure` | 예(FAILED) |

세 번째는 `_rejected_comparison_convergence`가 "modal RMS …, max …" 문장을
**두 번** 내보내는 기존 버그다(`prefix`가 이미 넣고 `else` 가지가 또 붙인다).
내 변경은 그 함수에 엔진 `continue`만 추가했고 문구는 손대지 않았다. 전체 실행에서도
같은 노드가 FAILED이고 기준선과 일치한다.

### 6-3. 제품 기준선 (지시의 게이트)

```
python -m pytest tests -q --ignore=tests\engine --ignore=tests\test_audit_source_l29_l30_port_window.py -p no:cacheprovider
31 failed, 2573 passed, 5 skipped, 260 errors in 389.47s (0:06:29)
```
(`work/engine_w11/product_w11b.log`)

| 실행 | failed | passed | skipped | errors | wall |
|---|---|---|---|---|---|
| W5 기준선 `engine_w5/product_clean.log` | 31 | 2 556 | 1 | 260 | 433.59 s |
| W11-a 최종 `engine_w11/product_final.log` | 31 | 2 561 | 5 | 260 | 372.61 s |
| **W11-b** `engine_w11/product_w11b.log` | **31** | **2 573** | 5 | **260** | 389.47 s |

**게이트: FAILED/ERROR 노드 이름 집합 diff = 양방향 모두 비어 있음(291개 완전 동일).**

```
baseline nodes: 291
new nodes:      291
--- only in baseline (newly passing) ---      (없음)
--- only in new (REGRESSIONS) ---             (없음)
```

늘어난 12 passed = 내 엔진 테스트 8건 + W11-c가 커밋한 테스트 4건
(작업 중 HEAD가 `5cbed5a` → `db3a684` "W11-c scenario mapping tests, W11-d CPU-only
frozen packaging"으로 이동했다).

### 6-4. 라이브 확인 (260729 Port18, 엔진 캐시 웜)

`work/engine_w11/gui_live_port18.py` — 캐시된 시나리오(`scenario_port18.json`)를 읽어
제품 `evaluate_scenario(..., solver_profile="hybrid_plane_pair_v1")`로 **실제 엔진 결과**를
만들고, 그 `EvaluationView`를 GUI 렌더링 헬퍼와 offscreen `MainWindow`의 노트 패널에
통과시킨다. 로그 `gui_live_port18.log`, 결과 `gui_live_port18.json`.

| 항목 | 값 |
|---|---|
| 평가 wall | **52.6 s**(`solver="auto"`, 추출 캐시 히트) |
| rail / profile / badge | `ADC_VDD_075_VTRIP_SRAM/0` / `hybrid_plane_pair_v1` / **HYBRID** |
| 배치 정체성(`_comparison_solver_provenance`) | HYBRID(Original/Tuned 동일 뷰로 수용) |
| 수렴 게이트 거부 | `[]` (§2-4 수정 없으면 배치 거부됨) |
| 주파수 | **28점**, 최대 **100.0 MHz** |
| confidence | LOW |
| 모달 프리셋 위젯 | **비활성** |

```
banner_text:
[HYBRID] Hybrid plane-pair (engine) · compiler
spd-pi-engine-hybrid-plane-pair-powersi-compatible-v1 / 0.1.0.dev0 ·
numerics 27d81996e38f… · spd_pi_engine 2-D plane-pair + circuit hybrid (direct sparse LU)
spd-pi-engine-0.1.0.dev0-27d81996e38f · Source-only: Yes (source_engine_hybrid) ·
bounded by the engine receipt validity notes · PowerSI parameter fitting: never

convergence 열:
Direct sparse LU (no modal expansion); frozen 28-point engine ladder to 100 MHz;
no modal or frequency-refinement sweep

상태줄:
HYBRID · spd_pi_engine 2-D plane-pair + circuit hybrid · PowerSI-compatible cavity wall ·
original SPD required · frozen 1 kHz-100 MHz ladder (28 pt) · direct sparse LU ·
bounded by the engine validity notes
```

검사 11개 전부 통과(`LIVE PASS`):

| 검사 | 결과 |
|---|---|
| `badge_is_hybrid` / `batch_identity_accepted` | PASS |
| `convergence_gate_clear` | PASS |
| `no_mode_count_rendered` | PASS |
| `validity_notes_in_confidence_note` | PASS (`VALIDITY_NOTES` 5줄 전부 `confidence_note`에) |
| `validity_notes_in_assumptions` | PASS (`view.assumptions == VALIDITY_NOTES`) |
| `validity_notes_in_notes_pane` / `badge_in_notes_pane` | PASS (노트 패널 텍스트에 5줄 + `[HYBRID]`) |
| `ladder_points` (28) / `ladder_top_100MHz` | PASS |
| `first_use_notice_shown` | PASS |

---

## 7. 계획에서 벗어난 점 / 판단이 필요한 것

### 7-1. 기준선 로그의 260 errors는 `tmp_path` 고갈이며 **전체 실행에서만** 재현된다

원인은 pytest `make_numbered_dir`가
`C:\Users\User\AppData\Local\Temp\pytest-of-최윤혁\pytest-N` 안에 10회 시도로
디렉터리를 못 만드는 것이다(`engine_w5/product_clean.log:12370-12401`).
파일 1~2개만 돌리면 `tmp_path`가 **정상 동작**해서, 기준선에서 ERROR였던 테스트들이
실제로 본문을 실행한다. 그래서 지시의 GUI 전용 명령(§6-2)과 제품 전체 명령(§6-3)은
서로 다른 집합을 낸다. 게이트는 §6-3으로 판정했고, §6-2는 "기준선 집합의 부분집합"으로만
확인했다.

### 7-2. 사전 존재 행(hang) 1건 — `test_evaluation_worker_receives_scenario_model_attachments`

GUI 파일만 단독으로 돌리면(= `tmp_path`가 살아 있으면) 이 테스트가
`_accept_evaluation_preflight`의 **부분 실행 동의 `QMessageBox.exec()`**에서
offscreen 상태로 영구 블록한다(faulthandler 덤프로 줄 확인).

**W11-b와 무관함을 증명했다**: `src/spd_decap_pi/gui/main_window.py`를 HEAD(`db3a684`)
버전으로 잠깐 바꿔 끼우고 같은 테스트를 돌렸더니 **같은 함수의 같은 지점**에서 멈췄다
(`work/engine_w11/hang_dump.txt`). 그 뒤 내 파일을 바이트 그대로 복원했다
(`git diff --stat` 재확인). 제품 전체 실행에서는 `tmp_path` ERROR로 먼저 빠지므로
게이트에 영향이 없다. **소유자 판단**: 이 테스트는 offscreen에서 동의 대화상자를
띄우므로 `QMessageBox`를 패치하도록 고치는 게 맞다(내 소유 파일이 아니라 두었다).

### 7-3. 세션 스코프 autouse 픽스처 누수 — 소유자 판단

§5-4 참조. `tests/test_research_av_bs1_boundary_schur_h4_p1.py:255`의
`@pytest.fixture(scope="session", autouse=True)`가 파일시스템 진입점을 세션 끝까지
금지 상태로 둔다. 내 파일에서만 복원했고, 원인 파일은 소유 밖이라 고치지 않았다.
같은 이유로 깨지는 다른 테스트가 더 있을 수 있다.

### 7-4. 수렴 게이트 예외 — 동작 변경

§2-4. `_rejected_comparison_convergence`는 fail-closed 게이트다. 엔진 결과를
면제시키는 것은 **정책 변경**이다(근거: 엔진은 수렴 스윕이 없고 정확도 경계가
영수증 validity 노트다). 소유자 확인 사항.

### 7-5. `receipt_sha256`은 presentation에 넣지 않았다

§2-2. Original/Tuned가 다르므로 넣으면 배치 정체성 비교가 깨진다. 영수증 해시는
`view.solver_provenance["receipt_sha256"]`에 그대로 있고 결과 표의 툴팁에서 볼 수 있는
`details_text`에는 `numerics_id` 전체만 넣었다.

### 7-6. `status` / `validation_status` 기본값을 GUI에서 지어냈다 — 소유자 판단

W11-a의 `solver_provenance`에는 이 두 키가 없다. GUI는
`source_only_status="source_engine_hybrid"`,
`validation_status="engine_validity_notes_bound"`를 기본값으로 쓴다(배너에
"bounded by the engine receipt validity notes"로 표기). 어댑터에 키를 넣는 편이
정석이지만 내 소유 범위가 `engine_adapter.solve()`의 진행률/취소 배선뿐이라
건드리지 않았다.

### 7-7. 결과 창 배너 색

`ComparisonResultsWindow.set_provenance(research=...)`는 research일 때만 호박색이다.
엔진은 기본(파랑) 스타일로 나오고 배지/정체성은 문구에 있다. 색을 따로 주지 않았다.

### 7-8. `_evaluation_solver_profile_changed` 호출 순서 변경

제어 비활성 → 상태 문구 → (배치 무효화) → 1회 고지 → 노트 렌더링. 노트 렌더링만
`spd_pi_engine`를 import하므로 마지막에 둔다(§5-4). 부작용: 완료된 배치가 있는 상태에서
엔진을 처음 고르면 무효화 메시지가 요약 패널을 먼저 채우고 그 위에 고지가 덮인다
(고지가 최종적으로 보인다).

### 7-9. 라이브 확인의 범위

`MainWindow._accept_evaluation` 전체가 아니라 실제 엔진 결과를 GUI 렌더링 헬퍼
(`_solver_provenance_for_view`, `_comparison_solver_provenance`,
`_modal_convergence_text`, `_rejected_comparison_convergence`)와 노트 패널까지 통과시켰다.
`_accept_evaluation`의 끝은 `_refresh_all()`이고 260729 패키지 전체 보드 뷰를 다시
그리므로 수 분이 걸린다 — 그 부분은 W11-b가 바꾼 코드가 아니다.

### 7-10. 취소 granularity와 하트비트

하트비트는 **취소 수단이 아니다**(계획이 허용한 "kill이 본질" 쪽). solve 구간은 여전히
메시지로는 중단되지 않고, 부모가 0.2 s 폴링으로 프로세스를 죽인다. 하트비트 주기
`HEARTBEAT_INTERVAL_S = 2.0`은 상수이며 노출하지 않았다.

### 7-11. 하트비트가 영수증에 영향을 주지 않는다

stdout만 바뀐다. W11-a의 재현 테스트(`-m engine_reproduction`, 약 19분)는 이번에 다시
돌리지 않았다 — 워커의 수치 경로는 한 줄도 바뀌지 않았고(`run()` 본문 무변경),
`solve()`도 stdout 소비 방식만 바뀌었다. 정상 경로 영수증 보존·해시는 §5-3에서 확인했다.
