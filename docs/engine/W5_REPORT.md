# W5 — 엔진 테스트·픽스처·CI 프로파일 (`tests/engine/`) 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §3(재현 테스트)·§4 W5, 근거 `docs/engine/{W1..W4,IR}_REPORT.md`.
원칙 §0(수치 불변), CLAUDE.md 규칙 1(튜닝 금지)·2(영수증, 덮어쓰기 금지)·5(연구 코드 무수정). 2026-09-18.

## 1. 산출물

| 파일 | 줄/크기 | 내용 |
|---|---|---|
| `tests/engine/conftest.py` | 115줄 | `spd_path(tag)`·`ref_npz(tag)`·`cache_dir`·`data_dir` 픽스처, `--slow`/`--gpu` 옵션, 마커 스킵 |
| `tests/engine/ladder.py` | 27줄 | `ladder_freqs(fref)`·`ref_of(npz, port)` — `engine_w4/w4_gate.py`의 사다리 스냅 로직 이식 |
| `tests/engine/test_datafree.py` | 100줄 | 데이터 없는 셀프체크 11개 |
| `tests/engine/test_reproduction.py` | 198줄 | 영수증 재현 20개 |
| `tests/engine/fixtures/exp28/*.json` | 7개, 97.7 KB | 변형 p 정본 영수증 |
| `tests/engine/fixtures/exp30/*.json` | 2개, 20.7 KB | held-out PCB 영수증 |
| `tests/engine/fixtures/spd_sha256.json` | 727 B | SPD 3종의 sha256(이번 세션 계산) |
| `tests/engine/fixtures/README.md` | — | 출처·정본 규칙·해시 불일치 정책 |
| `pyproject.toml` | +4줄 | `[tool.pytest.ini_options] markers = ["slow: …", "gpu: …"]` |

- 패키지 모듈(`src/spd_pi_engine/**`)은 한 줄도 건드리지 않았다(다른 에이전트가 `cli.py`·`__init__.py` 작업 중).
- 기존 `tests/*.py`도 수정하지 않았다. 새 폴더 `tests/engine/`만 추가했다.
- 연구 트리 `tools/research-claude/`도 무수정. 테스트는 연구 모듈을 **import 하지 않는다**
  (사다리·참조 npz 접근 로직은 `ladder.py`에 이식).
- 로그·본 보고서: `WORK_DIR\engine_w5\{default.log, full.log, gpu_only.log, port50_xfail.log,
  product_clean.log, product_baseline_headpyproject.log, W5_REPORT.md}`.

## 2. 테스트 목록

### 2-1. 데이터 없는 테스트 (`test_datafree.py`) — 항상 실행, 합계 1.3 s

| 테스트 | 검증 |
|---|---|
| `test_homogenise_selfcheck_face_fix` | `homogenise.selfcheck_face_fix()` == 0, `SELFCHECK-HOMOG PASS` 출력 |
| `test_homogenise_demo` | 위 + `cell_edges`가 solid sheet를 걷는지 |
| `test_solver_demo` | `YPattern`이 `coo_matrix(...).tocsc()`와 비트 동일(중복합 포함) |
| `test_geometry_demo` | scanline vs matplotlib 합성 60폴리곤 — **differing 0** |
| `test_geometry_vertices_on_grid_centres` | 꼭짓점이 셀 중심에 정확히 놓인 오목 폴리곤 1건, `contains_points`와 셀 단위 동일 |
| `test_cache_demo` | 캐시 v2 왕복, 제품 객체 미피클, sha 불일치·절단 → miss |
| `test_receipt_demo` | G1–G5, `attach_reference`, `numerics_id`, `decap_config_sha256` |
| `test_parser_api` | C16 사설 파서 심볼 10개 존재 |
| `test_numerics_id_stable_and_flag_sensitive` | 같은 입력 → 같은 id, 플래그 1개·`reference_mode` 변경 → 다른 id |
| `test_backend_defaults` | `Backend()` == (`splu`, `fast=False`, `ir_steps=1`), `gpu()` is None |
| `test_solver_demo_cudss` (`gpu`) | cuDSS vs `scipy.splu`, 패턴 공유 2계 |

### 2-2. 영수증 재현 테스트 (`test_reproduction.py`) — 데이터 필요

| 테스트 | 계획 | 케이스 | 허용치 |
|---|---|---|---|
| `test_legacy_port18_1mhz` (`slow`) | §3 (i) | 260729 P18, `FLAGS_LEGACY`, `Backend()`, 1 MHz 최근접 1점 | rel ≤ 1e-9, `unknowns` == 261124 |
| `test_variant_p_cases[7]` | §3 (ii) | exp28/p 7건, CPU | max rel ≤ 1e-9 + `unknowns`·`plane_C_total_nF`·`two_sided_layers` 일치 |
| `test_variant_p_cases_gpu[7]` (`gpu`) | §3 (ii) | 같은 7건, `Backend("cudss", fast=True)` | max rel ≤ 1e-8 |
| `test_pcb_s5m6585[2]` | §3 (iii) | s5m6585 Port1/Port50, CPU | max rel ≤ 1e-9 |
| `test_pcb_s5m6585_gpu[2]` (`gpu`) | §3 (iii) + IR_REPORT | 같은 2건, GPU | f ≥ 1 MHz ≤ 1e-8, 전 대역 ≤ 1e-6 |
| `test_attach_reference_port14` | W5 항목 5 | P14 영수증 + 참조 npz | `ladder_gates.PASS` dict 동등, \|Δerr_1MHz\| ≤ 1e-9 |

모두 W4 공개 API만 쓴다: `Design.open → .rail(port, cache_dir) → .build(ModelOptions) → .solve(freqs) → .receipt()`.
주파수는 참조 npz 격자에 스냅한 사다리 27점이고, **비교 전에 영수증의 `freq` 배열과 `array_equal`을 assert**한다.
빌드+solve 결과는 세션 `lru_cache`로 공유하므로 같은 케이스를 여러 테스트가 물어도 한 번만 푼다
(`test_attach_reference_port14` 0.01 s).

## 3. 프로파일

| 프로파일 | 명령 | 도는 것 |
|---|---|---|
| 데이터 없음 | `pytest tests/engine -q -m "not slow and not gpu" -k datafree` | 10 passed, 1 skipped, **1.3 s** |
| 기본 | `pytest tests\engine -q` | 데이터 없는 10 + P14·P18(CPU) + PCB Port1(CPU) + attach_reference |
| 전체 | `pytest tests\engine -q --slow --gpu` | 31건 전부 |

`--slow`/`--gpu`가 없으면 해당 마커는 스킵된다(`conftest.pytest_collection_modifyitems`).
마커 선언은 `pyproject.toml`에 있고(기존에 `markers` 항목이 없었다), 옵션은 `tests/engine/conftest.py`에 있다.

데이터 해석(계획 §3): `SPD_PI_DATA_DIR` 미설정/SPD 없음 → **skip**, SPD가 있는데 sha256이
`fixtures/spd_sha256.json`과 다르면 → **FAIL**. 캐시는 `SPD_PI_WORK_DIR\engine_cache`(있으면 재사용,
없으면 세션 tmp).

## 4. 실행 결과

### 4-1. 기본 프로파일 — `python -m pytest tests\engine -q`

```
14 passed, 17 skipped in 1643.64s (0:27:23)
```

| 테스트 | wall |
|---|---|
| `test_variant_p_cases[260729-Port18_SITE0]` | 1143.85 s |
| `test_pcb_s5m6585[Port1_U1_0]` | 378.76 s |
| `test_variant_p_cases[260729-Port14_SITE0]` | 118.92 s |
| `test_attach_reference_port14` | 0.11 s (캐시 재사용) |
| 데이터 없는 10건 | ≤ 0.02 s each, 합계 1.3 s |

### 4-2. 전체 프로파일 — `python -m pytest tests\engine -q --slow --gpu`

측정 실행: `1 failed, 29 passed, 1 xfailed in 9535.76s (2:38:55)`.
실패한 `test_pcb_s5m6585[Port50_U1_0]`을 §5-1에 따라 strict xfail로 기록한 뒤의 최종 상태는
**29 passed, 2 xfailed, 0 failed**(두 Port50 케이스만 재실행해 확인: `2 xfailed in 366.54s`).

| 테스트 | wall | max rel (관측) |
|---|---|---|
| `test_variant_p_cases[260729-Port7_SITE0]` (CPU, 미지수 586,481) | 4197.38 s | ≤ 1e-9 PASS |
| `test_variant_p_cases[260804-Port18_SITE0]` | 1154.05 s | ≤ 1e-9 PASS |
| `test_variant_p_cases[260729-Port18_SITE0]` | 963.63 s | ≤ 1e-9 PASS |
| `test_variant_p_cases[260729-Port16_SITE0]` | 952.55 s | ≤ 1e-9 PASS |
| `test_legacy_port18_1mhz` | 667.48 s | ≤ 1e-9 PASS, unknowns 261124 |
| `test_variant_p_cases[260729-Port19_SITE0]` | 361.25 s | ≤ 1e-9 PASS |
| `test_pcb_s5m6585[Port50_U1_0]` | 345.06 s | **1.016e-09 @ 3.02e4 Hz → xfail** |
| `test_pcb_s5m6585[Port1_U1_0]` | 335.67 s | ≤ 1e-9 PASS |
| `test_variant_p_cases[260729-Port1_SITE0]` | 325.53 s | ≤ 1e-9 PASS |
| `test_variant_p_cases[260729-Port14_SITE0]` | 47.61 s | ≤ 1e-9 PASS |
| `test_variant_p_cases_gpu[*]` 7건 | 7.8–44.8 s | ≤ 1e-8 PASS 7/7 |
| `test_pcb_s5m6585_gpu[Port1_U1_0]` | 11.43 s | PASS |
| `test_pcb_s5m6585_gpu[Port50_U1_0]` | 10.38 s | **1.688e-08 @ 1.0 MHz → xfail** |
| `test_solver_demo_cudss` | 1.61 s | PASS |

GPU 전용 부분집합(`-m gpu --slow --gpu`)만 따로: `214.78 s`에 10건(9 passed + Port50 xfail).
**한 프로세스 안에서 cuDSS 모델 9개를 순차 생성해도 OOM·0xC0000005 없이 끝난다**
(계획 §5-3이 우려한 핸들 누적이 8 GB A2000에서 이 규모까지는 문제되지 않음을 확인).

### 4-3. 제품 테스트 무회귀 — `python -m pytest tests -q -x --ignore=tests\engine`

지시대로 실행하면 **수집 단계에서 중단**된다:

```
ERROR tests/test_audit_source_l29_l30_port_window.py
  ModuleNotFoundError: No module named 'spd_decap_pi.source_plane_ownership_ir'
1 error in 1.10s
```

이 파일은 이번 작업과 무관한 **untracked 연구 파일**이고(`git status` ??), 임포트 대상 모듈이
작업 트리에 존재하지 않는다. 이 파일만 제외하고 `-x` 없이 전량을 돌린 결과와, **HEAD 버전의
`pyproject.toml`로 되돌려 돌린 대조 결과**가 완전히 같다:

| 실행 | pyproject | 결과 | wall |
|---|---|---|---|
| 현재 | 변경본(deps + `gpu` extras + `markers`) | **31 failed, 2556 passed, 1 skipped, 260 errors** | 433.59 s |
| 대조 | `git show HEAD:pyproject.toml` | **31 failed, 2556 passed, 1 skipped, 260 errors** | 488.54 s |

`FAILED`/`ERROR` 목록도 `diff` 결과 **완전 동일**하다 → **pyproject 변경(matplotlib·Pillow 선언,
`gpu` extras, `markers`)으로 인한 회귀는 0건**이다. 참고로 260 errors는 전부
`OSError: could not create numbered dir …`(pytest `tmp_path` 픽스처)로, 환경 문제이며 두 실행에서
동일하게 발생한다. 31 failed는 D116/D117 계열 진행 중 작업의 기존 실패다.

`pyproject.toml`은 대조 실행 직후 원래 내용으로 복원했다(`git diff --stat` 12 insertions 확인).

## 5. 계획에서 어긋난 점 / 소유자 판단이 필요한 발견

### 5-1. PCB Port50_U1_0이 CPU 1e-9 게이트를 1.6 % 초과 — strict xfail로 기록

- 관측: `max rel 1.016e-09 @ 3.02e4 Hz`(사다리 최저 주파수). 같은 설계 Port1_U1_0은
  W3 게이트4에서 **9.376e-11**, 이번 실행에서도 PASS.
- `unknowns`(189,283)·`plane_C_total_nF`·`freq` 배열은 영수증과 일치 → **빌드는 동일**, 차이는
  solve 뒤 저주파 소거에서만 나타난다. 계획 §0이 경고한 "최저 주파수에서 1e-16 → 8e-9 증폭"과
  같은 현상이며, 레일 조건수 차이로 보인다.
- 조치: **허용치를 넓히지 않았다**(CLAUDE.md 규칙 1). `pytest.mark.xfail(strict=True)`로 수치를
  이유 문자열에 박아 두었다. strict이므로 값이 어느 방향으로든 움직이면 다시 실패한다.
- 소유자 결정 필요: (a) PCB 저주파 CPU 게이트를 사전 등록으로 2e-9로 고칠지, (b) 3.02e4 Hz에서
  엔진과 `run11`의 연산 순서 차이를 추적할지.

### 5-2. IR_REPORT의 GPU 계약(≥1 MHz에서 1e-8)이 PCB 전체 성질이 아니다 — strict xfail로 기록

- `IR_REPORT.md` §1은 s5m6585 **Port1_U1_0 한 포트**에서 "1 MHz 이상은 ≤6.1e-9"를 근거로 계약을
  좁혔다. 두 번째 PCB 레일 Port50_U1_0은 **1.0 MHz에서 1.688e-08**로 1e-8을 넘는다.
- 즉 좁힌 계약은 설계 단위가 아니라 **레일 단위**다. 테스트는 계획이 지시한 그대로
  (≥1 MHz ≤1e-8, 전 대역 ≤1e-6) 두고 이 케이스만 strict xfail로 표시했다.
- 소유자 결정 필요: GPU 계약을 PCB에 대해 다시 유도할지(주파수 경계 상향 또는 허용치 완화),
  아니면 PCB는 CPU 기준 경로로 못 박을지.

### 5-3. 두 번째 PCB 포트는 `slow`

계획 §3 (iii)은 "1–3포트"라고만 적었다. `Port1_U1_0`은 `WORK_DIR\engine_cache`에 추출이 이미
있어 기본 프로파일에 넣고, 다른 레일(`ADC_DVDD08_CORE/17`)인 `Port50_U1_0`은 콜드 추출이 필요해
`slow`로 뺐다. 기본 프로파일을 27분 안에 유지하려는 선택이다.

### 5-4. `homogenise.demo`·`receipt.demo`도 테스트에 포함

계획 §3이 열거한 셀프체크 외에 W1/W4가 남긴 `demo()` 두 개를 같이 묶었다. 비용 0.02 s이고
데이터가 필요 없다.

### 5-5. `--slow` CPU 프로파일이 2시간 39분

`test_variant_p_cases[260729-Port7_SITE0]`(미지수 586,481) 한 건이 4197 s로 전체의 44 %다.
CI에서 매번 돌릴 수 있는 규모가 아니다. GPU 부분집합(`-m gpu`)이 같은 7케이스를 215 s에 덮으므로,
상시 회귀는 **기본 프로파일 + `-m gpu`**, `--slow` 전량은 릴리스 전 1회를 권한다.

## 6. 픽스처 표

| 파일 | 크기 | 미지수 | 주파수 | SPD sha256(앞 16) |
|---|---|---|---|---|
| `exp28/result_260729_Port1_SITE0_any_p.json` | 13,702 B | 118,064 | 27 | `40cb44b2376f59d6` |
| `exp28/result_260729_Port7_SITE0_any_p.json` | 16,660 B | 586,481 | 27 | `40cb44b2376f59d6` |
| `exp28/result_260729_Port14_SITE0_any_p.json` | 13,706 B | 34,424 | 27 | `40cb44b2376f59d6` |
| `exp28/result_260729_Port16_SITE0_any_p.json` | 13,822 B | 334,638 | 27 | `40cb44b2376f59d6` |
| `exp28/result_260729_Port18_SITE0_any_p.json` | 13,790 B | 275,218 | 27 | `40cb44b2376f59d6` |
| `exp28/result_260729_Port19_SITE0_any_p.json` | 12,207 B | 69,205 | 27 | `40cb44b2376f59d6` |
| `exp28/result_260804_Port18_SITE0_any_p.json` | 13,848 B | 275,177 | 27 | `45253f438fc7c328` |
| `exp30/result_s5m6585_Port1_U1_0_any_p.json` | 10,360 B | 189,397 | 27 | `f774dc06cdce4a1e` |
| `exp30/result_s5m6585_Port50_U1_0_any_p.json` | 10,307 B | 189,283 | 27 | `f774dc06cdce4a1e` |
| `spd_sha256.json` | 727 B | — | — | — |
| (제자리 참조) `docs/research-claude/.../exp8/result_260729_Port18_SITE0_any.json` | 12,792 B | 261,124 | 27 | `40cb44b2376f59d6` |

합계 118 KB(계획 §3 예상 120 KB). 전부 시각 접미사 없는 **정본**이다. 상세는
`tests/engine/fixtures/README.md`.

| 설계 | SPD 파일 | 바이트 | sha256 |
|---|---|---|---|
| 260729 | `S4LB002-2Para_260729_1_injected.spd` | 1,116,717,287 | `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2` |
| 260804 | `S4LB002-2Para_260804_1_injected.spd` | 1,120,159,188 | `45253f438fc7c328c50364fe610a7ecfbf72ca842a032921fbbb8d645e2a4f35` |
| s5m6585 | `s5m6585_32p_260414_length3_1.spd` | 139,414,807 | `f774dc06cdce4a1e0a632eb4a126d06af1fd57b9f400287e510f233ede1c0ad9` |

## 7. 합격/불합격 집계

| 실행 | passed | failed | xfailed | skipped | errors | wall |
|---|---|---|---|---|---|---|
| 엔진 기본 | 14 | 0 | 0 | 17 | 0 | 27:23 |
| 엔진 전체(`--slow --gpu`) | 29 | 0 | 2 | 0 | 0 | 2:38:55 |
| 엔진 GPU 부분집합(`-m gpu`) | 9 | 0 | 1 | 0 | 0 | 3:35 |
| 데이터 없는 부분 | 10 | 0 | 0 | 1 | 0 | 1.3 s |
| 제품(`--ignore=tests\engine`, 깨진 1파일 제외) | 2556 | 31 | 0 | 1 | 260 | 7:13 |
| 제품 대조(HEAD `pyproject.toml`) | 2556 | 31 | 0 | 1 | 260 | 8:08 |

계획 §3의 (i)(ii)(iii) 게이트는 모두 재현됐다. 유일한 미달은 §5-1·§5-2의 PCB Port50 두 건이며,
두 건 다 허용치를 손대지 않고 strict xfail로 기록했다.

## §7 E3 반영 (2026-09-18)

`ENGINE_PLAN_2026-09-18.md` "결정 기록" E3(소유자, 2026-09-18)에 따라 §5-1·§5-2의 strict xfail
2건을 사전 등록 계약으로 해소했다. 결과를 본 뒤 조정한 것이 아니라, 레일 유형별 계약을 결과와
무관하게 고정한 것이다(CLAUDE.md 규칙 1 유지 — "튜닝 금지"는 임의 조정 금지이지, 사전 등록된
레일 유형 계약 적용을 금하지 않는다).

**새 허용치**(`tests/engine/test_reproduction.py`의 `TOLERANCE` dict, 설계 클래스별):

| 레일 유형 | CPU | GPU |
|---|---|---|
| 패키지(`exp28`, 260729/260804) | ≤ 1e-9 (불변) | ≤ 1e-8 (불변) |
| PCB(`exp30`, s5m6585) | ≤ 2e-9 | f ≥ 1 MHz: ≤ 1e-7, 전 대역: ≤ 1e-6 |

`test_pcb_s5m6585`/`test_pcb_s5m6585_gpu`의 두 strict xfail 마커(Port50 CPU, Port50 GPU)를
제거했다. 패키지 테스트(`test_variant_p_cases`, `test_variant_p_cases_gpu`)와 데이터 없는
테스트는 손대지 않았다.

**실행 결과**:

- `python -m pytest tests\engine -q --gpu -k "s5m6585"`(지시된 정확한 명령 — `--slow` 없음):
  Port50은 두 파라미터화(CPU·GPU) 모두 `slow` 마커가 있어 `--slow` 없이는 스킵된다(수정 전과
  동일한 기존 동작; 이 태스크에서 마커를 바꾸지 않았다):
  ```
  SKIPPED [1] needs --slow   (test_pcb_s5m6585[Port50_U1_0])
  SKIPPED [1] needs --slow   (test_pcb_s5m6585_gpu[Port50_U1_0])
  2 passed, 2 skipped, 30 deselected, 1 warning in 382.72s (0:06:22)
  ```
  통과 2건은 Port1_U1_0 CPU·GPU. xfail 없음.
- E3가 실제로 해소하는 Port50을 검증하기 위해 `--slow`를 추가해 재실행
  (`python -m pytest tests\engine -q --gpu --slow -k "s5m6585"`):
  ```
  4 passed, 30 deselected, 1 warning in 851.06s (0:14:11)
  ```
  4건(Port1/Port50 × CPU/GPU) 전부 통과, xfail 없음. 구 strict xfail 값(Port50 CPU
  1.016e-09 @ 3.02e4 Hz, Port50 GPU 1.688e-08 @ 1.0 MHz)이 새 한도(CPU 2e-9, GPU f≥1MHz 1e-7)
  안에 들어와 통과로 전환됨을 확인.
- `python -m pytest tests\engine -q -k datafree`: `12 passed, 1 skipped, 25 deselected in 3.19s`
  (스킵 1건은 `needs --gpu`, 기존과 동일).

패키지 모듈(`src/spd_pi_engine/**`)은 건드리지 않았다(W9 동시 작업 중). 변경 파일:
`tests/engine/test_reproduction.py`(허용치 dict + 두 xfail 제거 + 관련 docstring),
`docs/engine/W5_REPORT.md`(본 절), `src/spd_pi_engine/README.md` §4(GPU 정확도 계약 문단과
§5의 PCB 재현 문장을 E3 계약으로 갱신).
