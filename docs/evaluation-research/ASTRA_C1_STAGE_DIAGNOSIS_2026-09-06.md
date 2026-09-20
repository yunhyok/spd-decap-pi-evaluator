# SPD Decap PI Evaluator v0.23.1 — Astra C1 `-07` 단계 진단

- 날짜: 2026-09-06 (Asia/Seoul)
- 상태: `STATIC_DIAGNOSIS_ONLY`
- 대상: Cell258 frozen split PSLG의 C1 한 번 호출 경로
- 비대상: 새 Triangle/FasterCap/solver/PowerSI 실행, raw SPD 재검색, 완료 root 재사용, 코드·릴리스 변경

## 결론

`-07`이 증명한 것은 컨트롤러 시작부터 종료까지 1,800.062초가 지나 wall deadline으로 중단되었고 자식 프로세스가 한 번 시작되었다는 사실뿐이다. Triangle API 진입, Triangle 반환, 인증기 진입 중 어느 것도 영수증으로 증명되지 않았다. 따라서 **지금 대체 mesher를 선택할 근거는 부족하다.** 조건부 후속인 네 표식 120초 fresh-root probe는 그 새 실행의 cutoff 시점에 어느 단계가 진행 중인지 구분할 수 있지만, `-07`의 과거 1,800초 원인을 소급 증명하지는 않는다.

이 판단은 PowerSI Touchstone에 가까운 source-derived Evaluation `Zii`가 주목표라는 현재 계획을 유지한다. C1은 입력/geometry feasibility 단계이며 PowerSI 정확도 개선 증거가 아니다. Distribution은 보조 범위다.

## 증거 경계

읽은 source checkout은 `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator`, HEAD `e2f219e71d8c8a397009f72242cce10d78cfc7ab`이다. 다음 세 실행 파일은 `-07` 컨트롤러 영수증의 pin과 현재 파일의 크기/SHA-256가 일치한다.

| 파일 | 바이트 | SHA-256 |
|---|---:|---|
| `tools/research/run_d117_triangle_cell258_c1_single_call_exact_once.py` | 80,691 | `ef8c332d7d715770feb0f522b98a86c7753489cece499536a73cc13f7bf8b803` |
| `tools/research/d117_triangle_cell258_c1_single_call_runner.py` | 52,673 | `3e9d3dd7d0e742b9e7717f5dcdfcaa54c4526e0875d36aafb7f5f84ce9d2f24e` |
| `tools/research/d117_triangle_cell258_c1.py` | 141,539 | `fe3d6eb82f90d33ac300cde155c919ec6e4b18356f2d3da4dd2fd73e45cee61c` |

주 영수증은 `d117_cell258_c1_single_call_controller_receipt.json` 42,554바이트, SHA-256 `8d5bcabe2b9ab4e93c002f8fc3184eaadde7654bdeb7dda7079c2b11790c5288`로 재확인했다. JSON은 한 줄이므로 아래 필드는 모두 그 파일 `:1`의 사실이다.

- `status=STOP_C1_TRIANGLE_SINGLE_CALL`, `reason=ControllerError: wall deadline exceeded`
- `elapsed_seconds=1800.0620000000054`, `wall_seconds=1800`, `deadline_exceeded=true`
- `launch_count=1`, `attempts=1`, `retries=0`, Job `total_processes=1`
- Job peak memory `766,533,632`바이트, hard limit `3,435,970,560`바이트
- stdout/stderr capture 모두 0바이트
- `after=null`, `final_output=null`, `final_child_receipt=null`, `triangle_loaded_modules=null`
- `triangle_determinism=NOT_EVALUATED`, `native_feasibility=OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN`
- pin은 approval 1개 + 일반 파일 13개 + Triangle site 83개, 총 97개다.

Peak memory는 전 경로의 최대값일 뿐 어느 단계가 사용했는지 보여 주지 않는다. 빈 capture도 무출력을 뜻할 뿐, API 미진입을 뜻하지 않는다. runner는 모든 작업이 끝난 뒤에만 PASS 한 줄을 쓰기 때문이다 (`d117_triangle_cell258_c1_single_call_runner.py:953-958`).

## 단계 지도

| 단계 | 실제 코드 경로 | `-07`에서 관찰됨 | 여전히 알 수 없음 |
|---|---|---|---|
| 컨트롤러 승인·pin | wall clock은 모든 검증보다 먼저 시작한다 (`run_d117_triangle_cell258_c1_single_call_exact_once.py:1181-1188`). 승인/limits/argv/site/scope를 검사하고 (`:998-1048`), 13개 파일과 83개 site 파일을 각각 읽어 SHA-256 pin한다 (`:1220-1247`, `:503-546`). | 승인 identity, 97개 stable input, `launch_count=1` | launch까지 걸린 시간 |
| 컨트롤러 launch | fresh token을 만들고 Job/capability/capture를 구성한 뒤 suspended child를 Job에 붙여 resume한다 (`:1250-1301`). 50 ms polling으로 capture cap과 전체 wall을 감시한다 (`:1302-1307`). | child 1회, retry 0, terminal cleanup | child가 어느 함수에 있었는지 |
| runner 승인·재검증 | approval/capability/token을 다시 검사하고 모든 13개 파일을 다시 읽어 hash한다 (`d117_triangle_cell258_c1_single_call_runner.py:851-880`). pin된 C1/Stage0 source를 compile/exec하고 API 상수를 확인한다 (`:881-885`). | 오류 receipt와 stderr가 없으므로 완료 여부 불명 | 이 구간 소요시간 |
| frozen PSLG 준비 | `prepare_no_triangle()`이 입력 graph 검증, WKB load, two-pass PSLG 생성, 배열/marker/hole 검증, canonical hash를 수행한다 (`d117_triangle_cell258_c1.py:2613-2665`). runner는 그 canonical을 즉시 다시 계산하고 site tree도 다시 hash한다 (`d117_triangle_cell258_c1_single_call_runner.py:886-898`). | 자식 stage receipt가 없어 완료 여부 불명 | Triangle import 직전 도달 여부 |
| Triangle import·진입 | Stage0가 wheel/site payload를 다시 대조하고 Triangle을 import한다 (`d117_triangle_quality_mesh.py:29-54`). 그 뒤 유일한 native 호출은 `site_module.triangulate(pslg, "pq15CzS221330")`이다 (`d117_triangle_cell258_c1_single_call_runner.py:770-800`). `call_count`는 호출이 **반환된 뒤** 증가한다 (`:796-800`). | 없음 | import 완료, API 진입, API 반환 여부 모두 불명 |
| 결과 인증 | raw result가 반환되면 accepted C1 certifier를 호출한다 (`d117_triangle_cell258_c1_single_call_runner.py:801-826`). 인증기는 중복, boundary, winding, topology, quality, canonical을 순차 검사한다 (`d117_triangle_cell258_c1.py:2183-2318`). | 없음 | Triangle은 끝났지만 인증 중 timeout이었는지 여부 |
| child·controller publication | 인증이 끝난 뒤에야 child canonical identity/receipt를 만든다 (`d117_triangle_cell258_c1_single_call_runner.py:909-958`). 컨트롤러는 그 뒤 child receipt와 output을 검증·bind하고 (`run_d117_triangle_cell258_c1_single_call_exact_once.py:1314-1331`), 마지막에 controller receipt를 no-clobber publish한다 (`:1395-1446`). | STOP controller receipt만 존재 | child publication까지 접근했는지 여부. `final_* = null`은 미완료만 증명 |

## 정적 비용 후보 — 측정치 아님

### Triangle 진입 전

1. **중복 pin/hash.** 컨트롤러가 97개 identity를 읽고, runner가 일반 파일 13개를 다시 읽는다. C1 input validator는 그중 C0 5.45 MB, D103, D104, WKB 2.43 MB, Stage0, wheel을 다시 읽고 hash한다 (`d117_triangle_cell258_c1.py:1160-1203`). 각 바이트량은 작지만 동일 evidence graph를 여러 번 순회한다.
2. **큰 C0 JSON의 Python 검증.** 5.45 MB C0 receipt를 strict parse한 뒤 149,078개 marker mapping 두 세트를 Python loop로 대조한다 (`d117_triangle_cell258_c1.py:950-1080`, 특히 `:1051-1061`).
3. **PSLG 재구성과 반복 검사.** 149,078 source edge를 두 번 순회해 153,246 vertices/segments를 만든다 (`d117_triangle_cell258_c1.py:1230-1294`). 이어 unique/sort, marker-chain 재순회, 2,048개 hole polygon/point 포함 검사를 수행한다 (`:1305-1366`).
4. **PSLG canonical 두 번.** 한 번의 canonical pass는 153,246 vertices + 153,246 segments + 153,246 markers + 2,048 holes, 즉 header 포함 461,787개 ASCII line과 12,057,453바이트를 Python에서 생성한다 (`d117_triangle_cell258_c1.py:1393-1419`). `prepare_no_triangle()` 내부와 runner의 즉시 recheck로 최소 두 번 수행된다 (`:2622`, `d117_triangle_cell258_c1_single_call_runner.py:890-891`).
5. **site/wheel 중복 검증.** runner의 `_tree_identity()`는 83개/4,361,013바이트 site tree를 hash한다 (`d117_triangle_cell258_c1_single_call_runner.py:552-575`, `:897`). 이어 Stage0 loader가 wheel payload를 읽고 설치 파일과 바이트 비교한다 (`d117_triangle_quality_mesh.py:29-45`).

이 항목들은 모두 plausible cost일 뿐 `-07` 영수증에는 단계별 시간이 없다. 특히 C0 exact clearance pair scan 자체는 `-07`에서 다시 실행되지 않는다. runner는 기존 clearance receipt의 필드만 검증한다 (`d117_triangle_cell258_c1_single_call_runner.py:680-733`, `:874-880`).

### Triangle 반환 뒤에도 1,800초 후보가 있다

대체 mesher 결정을 보류해야 하는 가장 강한 정적 이유는 인증기다.

- hole winding은 inside 1개 + outside 1개 + 2,048 holes 각각에 대해 모든 boundary row를 Python loop로 돈다 (`d117_triangle_cell258_c1.py:2164-2180`, 호출 `:2271-2283`). 최저 boundary count 153,246에서도 **314,154,300 row iterations**다. Steiner boundary가 늘면 더 커진다.
- edge topology와 vertex-link 검사는 최대 `3*T` structured records를 Python에서 만들고 heapsort/union/search한다 (`d117_triangle_cell258_c1.py:1856-2000`). `T` 허용 상한은 600,000이다.
- quality는 모든 triangle에 대해 Python 내부 loop에서 세 각도를 계산한다 (`d117_triangle_cell258_c1.py:2089-2153`).
- 결과 canonical은 hash 두 번, 파일 write 한 번, readback 한 번을 수행한다 (`d117_triangle_cell258_c1.py:2305-2314`).

따라서 `-07`의 wall 초과를 Triangle native 비용이라고 단정할 수 없다. mesher를 바꿔도 이 인증 비용은 남는다.

## C0 clearance가 말하지 않는 것

C0 exact-04는 frozen split PSLG에서만 통과했다. 해당 census는 결과에 `raw_source_segments_clearance_evaluated=false`, `raw_source_segments_clearance_result=unknown`을 명시한다 (`d117_cell258_boundary_clearance_census.py:714-749`, 특히 `:728-729`). C1 runner도 바로 그 제한을 필수로 검사한다 (`d117_triangle_cell258_c1_single_call_runner.py:689-703`).

그러므로 C0 PASS는 raw source clearance, C1 mesh, solver 또는 PowerSI 정확도를 열지 않는다. 현재 governing checkpoint의 동일 결론(`docs/EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md:171-182`)을 유지한다.

## Mesher 결정

**현재 결정: 대체 mesher로 교체하지 않는다. Triangle도 채택으로 승격하지 않는다.**

이유는 세 가지다. (1) API 진입 자체가 미증명이다. (2) 진입 전 중복 작업이 있고 시간이 없다. (3) API 반환 뒤 certifier에 수억 회 Python 순회가 있다. `D117_QUALITY_MESHER_BACKEND_DECISION.md`와 `D117_WP1_STATIC_RESOURCE_FEASIBILITY.md`는 과거 ledger로만 읽었으며 현재 실행 상태로 사용하지 않았다. 이 문서는 대체 backend 간 기술 비교를 하지 않으므로 upstream 대안 문서의 새 비교도 수행하지 않았다.

Vendor primary literature 검토와 작은 독립 대안 분석은 계속 가능하다. 다만 실제 backend 교체 결정은 comparable fresh measurement가 native Triangle 구간을 병목으로 분리한 뒤에만 내린다. 아래 probe 하나만으로도 `-07`의 역사적 병목을 확정할 수는 없다.

## 조건부 최소 fresh measurement 한 번 — 제안만, 미실행

**제안: `C1-STAGE-TRACE-01`, 120초 exact-once stage probe.** 기존 controller capture를 재사용하고 새 manager/sidecar/certificate 계층을 만들지 않는다. 현재 HQ의 실행 우선순위는 진행 중인 material 및 canonical loaded-sheet 실험이다. 80,691바이트 controller와 97-pin framework를 지금 복사하거나 probe를 실행하지 않는다. C1 stage measurement가 다음 우선순위가 될 때 Astra/HQ가 현재 authorized scope 안에서 이 조건부 제안의 실행 여부를 결정한다.

runner/controller의 fresh pinned 사본에 아래 네 개의 짧은 ASCII line만 순서대로 flush하고, probe 전용 exact transcript를 controller가 허용하게 한다.

1. `RUNNER_START` — `run_approved()` 진입 직후
2. `TRIANGLE_ENTER` — `load_triangle_site()` 완료 후, `triangulate()` 직전
3. `TRIANGLE_RETURN` — `triangulate()` 반환 직후, certifier 직전
4. `CERTIFIER_RETURN` — certifier 반환 직후

이 표는 새 probe가 120초 cutoff에 도달했을 때 마지막으로 통과한 경계를 해석한다.

| fresh probe의 마지막 표식 | 그 probe의 120초 cutoff 당시 위치 |
|---|---|
| 없음 + `launch_count=0` | controller 승인/pin |
| 없음 + `launch_count=1`, 또는 `RUNNER_START`만 | runner 검증/PSLG/site import |
| `TRIANGLE_ENTER` | Triangle native call |
| `TRIANGLE_RETURN` | C1 certifier |
| `CERTIFIER_RETURN` | child/controller publication |

실행 경계는 wall 120초, attempt 1, retry 0, active process 1, 기존 Job memory hard cap 3,435,970,560바이트다. 예상 capture는 256바이트 미만이며 solver/FasterCap/PowerSI/network/install/replay/extrusion은 계속 STOP이다. Probe mode는 `CERTIFIER_RETURN` 뒤에도 canonical/child scientific output을 publish하지 않고 diagnostic STOP으로 끝낸다. PASS나 정확도 결과를 만들 목적이 아니며 120초 이내에 무조건 정리하고 controller diagnostic receipt만 그 새 probe의 단계 위치 판단에 쓴다.

필수 입력은 `-07`과 같은 frozen C0/D103/D104/WKB/clearance/Stage0/Triangle-site identity graph, 새 controller/runner hash, 새 approval/token, **새 output root**다. raw SPD는 필요 없다. `-07` root와 다른 완료/tombstone root는 읽기 외 재사용하지 않는다.

## 한계

- 어떤 코드도 실행하거나 timing하지 않았다. 복잡도와 반복 횟수는 exact pinned source에서 정적으로 계산했다.
- `-07`의 766,533,632바이트 peak는 단계 attribution이 불가능하다.
- API 진입/반환과 certifier 진입은 현재 receipt로 확정할 수 없다.
- 제안한 120초 probe도 미실행이며, 실행되더라도 그 probe의 cutoff 위치만 보여 주고 `-07`의 과거 timeout 원인을 단독으로 증명하지 않는다.
- 이 진단은 geometry feasibility만 다루며 Evaluation `Zii` 또는 PowerSI agreement를 평가하지 않는다.
