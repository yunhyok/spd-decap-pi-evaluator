# SPD Decap PI Evaluator v0.23.1 — Claude 작업 인계

> **GitHub 게시본 — 2026-09-15.** [핵심 자료 목록](docs/handoff/2026-09-15/README.md)과 [파일·해시 manifest](docs/handoff/2026-09-15/manifest.json)를 함께 게시했다. 아래 주요 문서 링크는 이 브랜치의 snapshot으로 연결된다. 원본 기록 안의 절대 경로는 출처이며 GitHub 링크가 아니다. 대용량 SPD/NPZ와 완전한 연구 실행 환경은 로컬에 남아 있다. 이 브랜치는 원격 `main`의 `d5e7df4261a20f84cbf18c2b78cb9db3be4f743c`에서 만든 **인계 자료용 브랜치**이며, 저장소 루트의 제품 코드가 로컬 HQ 전체 상태와 같다는 뜻은 아니다.

작성·자료 확인: **2026-09-15 KST**. 연구 상태 기준: **2026-09-14 HQ 최종 중지 시점**.

## 1. 먼저 읽을 내용

사용자는 당분간 Claude에서 연구를 이어 가기 위해 이 인계를 요청했다. 목표는 **제공된 SPD와 PowerSI Touchstone을 바탕으로, 1 kHz–100 MHz에서 복소 임피던스를 정확히 예측하는 계산 모델과 알고리즘을 확립하는 것**이다. 빠른 pre-design PI 판단이 제품 목적이며 Distribution 호환성은 부차적이다. 1 GHz는 보조 진단이다.

**현재 실제 보드의 인정된 기준 결과는 1 MHz 복소 상대 오차 26.1826839757%다. 최근 정적 계산에서 수치 오차 감소는 확인했지만, 이를 실제 보드 정확도 개선으로 채택하지 않았다. 전 대역 목표는 달성되지 않았다.**

Claude는 아래 HQ 작업 폴더와 저장 자료를 출발점으로 삼아 사용자의 이관 요청 범위에서 후속 연구를 이어 간다. 이번 Codex 작업은 인계 문서 작성에 한정한다. Codex의 기존 연구·예약은 정지 상태로 유지하며, 이를 다시 켜거나 Codex 에이전트를 호출할 필요는 없다. Codex 잔여 40% 중지 규칙을 Claude의 별도 계정 사용량으로 해석하지 않는다.

**가장 중요한 위치 구분:** 원본 인계가 작성된 로컬 `MAIN` 폴더에는 최신 HQ 연구 코드와 결과가 모두 들어 있지 않다. 여러 worktree의 HEAD가 같아도 미커밋 파일과 ignored 산출물은 다르다. `git clone` 또는 `git checkout`만으로 현재 상태가 복원되지 않는다.

처음에는 다음 순서로 읽는다.

1. [HQ 최종 체크포인트](<docs/handoff/2026-09-15/snapshots/HQ/outputs/research/HQ_STOPPED_CHECKPOINT_20260914_USAGE40.md>) — 완료·실패·다음 지점의 기준.
2. [HQ 최종 산출물 해시 목록](<docs/handoff/2026-09-15/snapshots/HQ/outputs/research/HQ_STOPPED_ARTIFACTS_20260914_USAGE40.json>) — 인계 시 10개 항목의 크기와 SHA-256을 재확인했고 모두 일치했다.
3. [최종 정적 비교 결과](<docs/handoff/2026-09-15/snapshots/HQ/outputs/research/astra-static-tighter-inverse-20260914-01/hq-comparison-receipt.json>) 및 이 문서 §5–7.
4. [물리 검토 최종 체크포인트](<docs/handoff/2026-09-15/snapshots/PHYS/outputs/child-port-physics/checkpoint-20260914-1455KST.md>) — 끝부분의 최종 비교·중지 기록까지 읽는다.
5. [Fable 검토 후 수정 계획](<docs/handoff/2026-09-15/snapshots/REVIEW/docs/peer-review/2026-09-10/NEXT_PLAN.md>) — 이미 수행된 단계는 §6의 진행 결과로 갱신해 해석한다.

## 2. 실제 작업 위치와 보존 범위

아래 별칭은 이 문서에서만 사용하는 경로 약칭이다. 실행 코드의 경로를 자동 변경하는 설정이 아니다.

| 별칭 | 절대 경로 | 용도 / Git 상태 |
|---|---|---|
| `MAIN` | `C:/Users/User/Documents/ChatGPT/SPD Decap PI Evaluator` | 이 인계 파일, 제품 기본 코드, 이전 D115–D117 자료. branch `main` |
| `HQ` | `C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator` | **최신 연구의 기준 작업 폴더**. branch `codex/astra-evaluation-resume-20260906` |
| `IMPL` | `C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator` | 소형 포트 모델·정적 비교 구현. detached HEAD |
| `PHYS` | `C:/Users/User/.codex/worktrees/353f/SPD Decap PI Evaluator` | 독립 물리·대수 검토. detached HEAD |
| `RED` | `C:/Users/User/.codex/worktrees/1f13/SPD Decap PI Evaluator` | 독립 레드팀 보고서. detached HEAD |
| `REVIEW` | `C:/Users/User/.codex/worktrees/peer-review-followup-20260910/SPD Decap PI Evaluator` | 외부 검토 대응·수정 계획. branch `codex/fable-review-plan-20260910` |

`MAIN`, `HQ`, `IMPL`, `PHYS`, `RED`의 확인된 HEAD는 모두 `e2f219e71d8c8a397009f72242cce10d78cfc7ab`다. `REVIEW`는 `9078b7ed88a932e592c1b20a51aaeb3b28dc4ebc`다. 저장소 origin은 `https://github.com/yunhyok/spd-decap-pi-evaluator.git`이다. 인계 시 원격 fetch/push는 하지 않았다.

인계 작성 전 `git status --porcelain --untracked-files=all`에서, 접근 제외 파일을 뺀 변경 경로는 MAIN **38개**(tracked 2, untracked 36), HQ **795개**(tracked 2, untracked 793)였다. 9월 14일 저장 당시의 **788개**는 별도의 과거 snapshot이다. 개수만으로 변경 원인을 단정하지 않는다. `outputs`의 ignored 파일은 이 개수에 포함되지 않는다.

- [HQ 저장 당시 작업트리 목록](<docs/handoff/2026-09-15/snapshots/HQ/outputs/research/hq-stopped-working-tree-20260914-usage40.txt>)을 보존한다. 기존 변경 전체를 이번 작업의 결과로 취급하지 않는다.
- **`accuracy_parse.py`는 내용 읽기·검색·실행에서 제외한다.** 파일이 untracked여도 열거나 지우지 않는다. 전역 검색에는 `-g '!accuracy_parse.py' -g '!**/accuracy_parse.py'`를 넣는다.
- `git reset --hard`, `git clean`, 강제 checkout, 전체 stage, worktree 정리는 하지 않는다. 기존 실패 결과나 frozen 실행 디렉터리도 덮어쓰지 않는다.
- 새 실험은 새 출력 디렉터리를 사용하고, 변경한 입력·코드·모델 항목을 남긴다. 이전 receipt의 source hash를 새 코드에 맞춰 고쳐서 과거 실행을 재해석하지 않는다.
- 다른 PC 또는 Claude 웹으로 이전한다면 이 문서만으로 실행할 수 없다. HQ의 미커밋 소스와 `outputs/research`, 필요한 `outputs/research-fmm-runtime`, IMPL/PHYS의 해당 `outputs`, 아래 D: 입력·외부 산출물을 함께 옮겨야 한다. **이 GitHub 게시본에는 핵심 문서·결과 JSON·일부 진단 소스가 포함된다. 전체 실행 환경과 대용량 SPD·NPZ는 포함하지 않으며, 정확한 게시 목록은 아래 자료 목록을 따른다.**

## 3. 현재 코드와 환경에 들어가는 방법

처음에는 읽기만 수행한다. 아래 명령은 연구 계산을 실행하지 않는다.

```powershell
$researchRoot = 'C:\Users\User\.codex\worktrees\5950\SPD Decap PI Evaluator'
Set-Location -LiteralPath $researchRoot
git branch --show-current
git rev-parse HEAD
git status --short --untracked-files=normal
Get-Content -LiteralPath '.\outputs\research\HQ_STOPPED_CHECKPOINT_20260914_USAGE40.md'
Get-Content -LiteralPath '.\outputs\research\HQ_USAGE_STOP_POLICY_20260914.json'
```

- 제품 버전은 `pyproject.toml`의 **0.23.1**, Python 요구 사항은 `>=3.12`다. 과거 문서의 `v3.21`은 기술 기준 문서 버전이며 제품 버전과 다르다.
- 마지막 연구 실행이 사용한 Python은 `C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`다. Codex 도구 없이도 로컬 실행 파일로 접근할 수 있다. 이 경로는 설치 환경에 종속되므로 이동 시 실제 존재 여부를 확인한다.
- 제품 의존성은 `pyproject.toml`에 있으며 핵심은 NumPy, SciPy, Shapely, PySide6다. 연구 FMM은 별도 경로 `HQ/outputs/research-fmm-runtime/fmm3dpy`의 **2.1.0**을 사용했다. 제품 의존성 설치만으로 같은 연구 런타임이 재현되지는 않는다.
- [FMM 런타임 확인 기록](<docs/handoff/2026-09-15/snapshots/HQ/outputs/research/astra-l14-l25-fmm-nd2-runtime-20260914-01/result.json>)을 참조한다. 인계 작업에서 패키지 import, 설치, FMM 또는 solver 재실행은 하지 않았다.
- [최근 HQ actor](<docs/handoff/2026-09-15/snapshots/HQ/tools/research/run_astra_static_tighter_actor.py>)는 독립적인 제품 CLI가 아니다. IMPL의 exact driver, 저장 source/runtime와 기존 launcher가 제공하는 전역 경로에 의존한다. 과거 `external-budget.json`의 명령을 그대로 실행해 기존 출력 폴더를 재사용하지 않는다.
- 구현과 검토는 독립적으로 나눌 수 있지만 같은 파일의 동시 편집은 피한다. 과거 Codex task ID나 Sol/Astra 호칭은 소유자 이력이며 Claude가 그 모델을 반드시 호출해야 한다는 조건이 아니다.

## 4. 입력, 포트와 정확도 기준

### 실제 비교 입력

현재 개발 대상은 다음 260729 쌍이다. 두 파일의 존재를 인계 시 확인했다. 아래 해시는 저장된 선택·참조 기록의 값이며, 대용량 원본 전체를 이번 인계에서 다시 해싱하거나 파싱하지 않았다.

| 입력 | 경로 | 저장된 SHA-256 |
|---|---|---|
| SPD | `D:/S4LB002-2Para_260729_1_injected.spd` | `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2` |
| PowerSI | `D:/S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p` | `c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11` |

다른 확인된 예제는 `D:/S4LB002-2Para_260804_1_injected.spd`와 같은 이름의 `_080526_104445_27112_S.s92p`다. `D:/Downloads`에도 여러 SPD가 있고, `s5m6585_32p_260414_length3_1.spd`의 `.s160p` 및 `PC-2576_S4LQ015A_B0_1P-L01_251017_V2_PI_DATA_VDD075_NE_VDD.spd`의 `.s4p`가 있다. 일반적으로 `[name].spd`와 `[name]_*[date]_*[numbers].s[port count]p`가 대응하지만 **파일명만으로 동일 조건을 확정하지 않는다**. 260804 및 다른 설계가 자유로운 튜닝 데이터라고 가정하지 않는다.

- [개발 대상 선택 기록](<docs/handoff/2026-09-15/snapshots/HQ/docs/evaluation-research/astra_loaded_development_selection_2026-09-07.json>): rail `ADC_VDD_075_VTRIP_SRAM/0`, one-based port **18**, 참조 헤더 `2nd_SITE0-ADC_VDD_075_VTRIP_SRAM/0`. 참조 오차를 보기 전에 선택된 개발 대상이며 holdout이 아니다. 제외된 holdout rail 목록도 이 JSON에 있다.
- source-enabled decap은 선택 rail에서 **421개**, 저장 전체 Scenario termination은 **11,050개**다. 421개를 전체 회로 부하 수로 바꾸지 않는다.
- [참조 추출 기록](<docs/handoff/2026-09-15/snapshots/HQ/docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json>): **전체 92-port S→Z 변환**의 해당 Zdd다. **1 Ω는 파동 기준 임피던스**이며 모든 포트에 추가하는 1 Ω 부하가 아니다. S18,18만 따로 변환한 결과로 대체하지 않는다.
- 여기 전류 1 A, active potential index `+2699`, `−2656`, gauge `0`; `Zdd = v[2699] - v[2656]`. 포트·회로 종단·재료·주파수·모델을 모두 고정해서 비교한다.

### 현재 인정된 보드 기준

[1 MHz 비교 JSON](<docs/handoff/2026-09-15/snapshots/HQ/docs/evaluation-research/astra_hybrid_r_gc_1mhz_comparison_2026-09-09.json>) 기준:

| 항목 | 값 |
|---|---|
| 계산 Z | `0.0005545735011944134 - j0.0005883820472006132 Ω` |
| PowerSI Z | `0.0006915479466520245 - j0.0004257504501139287 Ω` |
| 복소 절대 차이 | `0.00021262886699454942 Ω` |
| 복소 상대 오차 | `26.18268397573785%` |
| 크기 오차 / 위상 오차 | `−0.03807608 dB` / `−15.07584406°` |

크기만 거의 맞아도 복소 응답은 상당히 다르다. 이후 미수렴 후보의 Z를 이 기준 대신 사용하지 않는다. 과거 10 MHz 결과도 수렴 기준해가 아니다. 1 MHz 개발점의 판별 성공을 1 kHz–100 MHz 완료, holdout 통과 또는 제품 출하 근거로 확대하지 않는다.

기존 정확도 정책은 [EVALUATION_ACCURACY.md](<docs/handoff/2026-09-15/snapshots/HQ/docs/EVALUATION_ACCURACY.md>)와 [레드팀 정정](<docs/handoff/2026-09-15/snapshots/REVIEW/docs/peer-review/2026-09-10/RED_TEAM_RESPONSE_2026-09-10.md>)을 따른다. 기존 등록 범위는 100 kHz–100 MHz의 241점, 저주파 offset ≤1 dB, magnitude max ≤2 dB, phase RMS ≤7°/max ≤15°, 적용되는 `|Zref|<1 mΩ` 표본에서 complex RMS ≤100 µΩ/p95 ≤200 µΩ, 지배 공진 위치 ≤10%/높이 ≤2 dB 등이다. 설계별 집계와 N/A 조건을 원문에서 함께 읽는다. 이는 **판정 규칙이지 현재 통과 결과가 아니다**. 사용자의 1 kHz 확장 목표와 Fable이 제안한 새 10/20%·시간 목표를 이미 승인·달성한 계약으로 혼동하지 않는다.

## 5. 현재 모델과 수치 문제

[공통 모델 소유 표](<docs/handoff/2026-09-15/snapshots/HQ/docs/evaluation-research/ASTRA_COMMON_MODEL_2026-09-12.md>)에 R, 내부·외부 자기, 상호 자기, G/C, 포트·접점·귀환의 포함/제외/미확인 범위가 있다. 이 표의 앞부분은 당시 10 MHz 모델이며, **9월 14일 L14/L25 비교와 동일한 모델이라고 읽으면 안 된다**. I(포함)는 정확성·완전성의 증거가 아니다.

- 인정된 1 MHz 조건부 회로의 기본 구조는 `A(v,i) = [Yv + Bi, Bᵀv − Ri]`다. `Y`에는 축약된 L02 RT0 전류 등이 포함되고 explicit `i`는 L25 전류다. 단일 dense A 파일이 아니라 sparse block과 `LinearOperator`로 구성된다.
- 기준 규모는 potentials 3,178,104개, explicit currents 604,031개, gauge 제거 후 3,782,134 unknowns다. 이후 L14 보조 전류 공간을 붙인 실험과 규모·식을 구별한다.
- 최근 finite 실험의 joint magnetic 대상은 **L14/L25**다. L04는 저장 전류의 고정 경계 진단에서는 쓸 수 있었으나, 같은 회로의 접점 전압과 호환되지 않아 이 finite 비교에서 제외됐다. 귀환이 물리적으로 완전하다고 결론 내리지 않는다.
- 동일 재질·역할의 plane을 위치에 따라 임의로 R/L 또는 다른 물리로 바꾸지 않는다. 정확한 공통 모델을 먼저 정한 뒤, 충분히 검증된 축약·공간 해상도 선택으로 비용을 줄인다. 사용자의 가까운 층/먼 층 예시는 구현 요구가 아니었다.
- MFDM, modal, via PEEC는 이미 구현되어 있다. 재사용 전에 가정·경계·소유를 확인한다. 새 partial-L을 기존 loop/scalar-L과 겹쳐 더하거나 isolated via 모델을 곧바로 전역 stamp하지 않는다.

### 이번 정적 진단의 질문

최근 증거는 현재 보조해법 `M`이 노드 전류 보존을 충분히 유지하지 못한다는 방향을 가리킨다. 이것이 유일한 원인 또는 전체 물리 오차의 설명으로 증명된 것은 아니다.

`P`가 노드 행을 선택하고 `A = A0 + E`, `P E = 0`이면 정확한 정적 역연산에서는 `P A A0⁻¹ = P`다. 실제 근사 `M`의 `P(A0 M − I)z`가 노드 오차를 만들 수 있다. 기존 seed 자체의 노드 오차도 별개로 보존된다. 부호는 `r = A*x − b`; 순수 노드 오차 보정은 `x_repair = x_latest − M*r_node`다.

오른쪽 전처리와 왼쪽 전처리를 구별한다. `Ahat0(Mz)`를 명시적으로 만들어 푸는 경로는 SciPy GMRES에 `M=None`을 전달한다. `gmres(A,b,M=M)`은 그 경로와 같지 않다. 입력에 따라 중단하는 inner iteration을 외부 반복법에 사용할 때는 고정 선형 M으로 가정하지 말고 flexible outer formulation의 필요성을 검토한다.

KCL 기준 `1e−7 A = 0.0001 mA`와 수치 재현의 pA 차이를 분리한다. 5–6 pA의 algebra check 초과를 숨기거나 PASS로 바꾸지 않되, 수 mA–A 단위의 실패 원인으로 확대하지 않는다. 이를 해결하기 위해 같은 대형 계산이나 검토 순환을 반복하는 것은 다음 과제가 아니다.

## 6. 완료된 판별과 반복하지 않을 계산

| 작업 | 실제 결과 | 남은 한계 / 후속 처리 |
|---|---|---|
| 실제 source 기반 소형 포트 대조 | 직접/반복, 구적, 한 단계 공간 분할을 구분한 비교를 수행했다. IMPL의 `result.json`, `return-mesh-refinement/result.json`, PHYS 기록에 근거가 있다. | 대표 구조의 변화가 실제 보드의 변화와 같지 않다. 소형 결과를 처음부터 다시 만들지 않는다. |
| 원격 L02 rows0/75 등 7개 쌍 | 해당 보드 전류가 nA 수준이고 저장 기준장에 대한 자기 민감도가 작았다. | 소형 fixture에서 1 A인 branch 0은 실제 보드에서는 외부 zero-flux다. [접속 지도](<docs/handoff/2026-09-15/snapshots/IMPL/outputs/child-port-fixture/actual-board-1mhz-connection-map.md>)의 옛 후속 lifting 제안보다 나중의 [전류 판별 결과](<docs/handoff/2026-09-15/snapshots/IMPL/outputs/child-port-fixture/actual-board-current-screen/result.json>)를 우선한다. 이 7개 쌍 확장은 중단했다. |
| L14/L25 및 PWR–L04 고정 전류 진단 | 단방향·상호 결합, self 항과 return 효과를 저장 전류에서 평가했다. L14 finite-thickness self 및 임의 전류 action API 확인도 완료했다. | frozen-current quadratic/1차 민감도는 새로운 회로 Z 또는 finite 정확도 개선이 아니다. L04 접점 호환성은 별도 실패다. |
| `astra-finite-joint-p-20260914-01` | 22 joint actions, **2214.156 s**. scaled residual **1.7996732**, KCL **49.9774 mA**, `info=1`. | 수렴 실패. left/right 구현 차이를 발견했으나 유일 원인으로 확정하지 않았다. raw field와 magnetic action을 보존했다. |
| `astra-finite-right-probe-20260914-01` | 7 actions 후 8번째 최종 확인 action 중 **900.843 s timeout**. 저장 v/q/eta의 KCL **18.1286 mA**. | 최종 magnetic force/flux·전체 residual·power·info가 없다. KCL도 실패하므로 마지막 FMM만 반복할 이유가 없다. |
| `astra-static-nodal-leakage-20260914-01` | **38.578 s**, 기존 factor 1회, M 2회, FMM 0회. zero-node magnetic RHS가 **4.9339749 A** 노드 누설 생성. 노드 보정 **18.1286 → 18.1634 mA**. | 개선 없음. 약 **6.0523 pA**의 부호 재현 차이에 대한 기존 gate FAIL을 보존했다. |
| `astra-static-tighter-inverse-20260914-01` | **123.171 s**, 같은 2 RHS, 각각 M/A0 28회, FMM 0회. 누설 **4.934 → 0.369224 A**; 노드 보정 **18.1286 → 4.30261 mA**. 각각 약 43.535/41.827 s. | 수치 오차는 감소했지만 둘 다 `info=3`, 미수렴. 약 **4.9621 pA** gate FAIL. 새 보드 Z는 없으며 더 비싼 inverse가 채택된 것도 아니다. |

마지막 실행은 180 s / 32 GiB 제한 안에서 종료됐고 peak private memory는 **24,434,507,776 B**였다. 이 제한은 당시 노트북 실험의 보호 설정이며 미래 워크스테이션의 제품 한도가 아니다.

**마지막 tighter STATIC 실행은 표본별 JSON만 저장했고, 풀린 표본 벡터는 저장하지 않았다.** combined success report가 없는 것은 저장 후 assertion 실패 때문이다. 없는 벡터를 재사용할 수 있다고 계획하지 않는다. 앞선 right probe의 raw v/q/eta는 남아 있다. 원본 실패 receipt를 유지하고 인계 문서만으로 성공 상태를 만들지 않는다.

## 7. Claude가 이어 갈 다음 단계

우선순위는 “현재 M을 더 반복하면 된다”가 아니라 **정적 보조해법이 놓치는 노드 모드 또는 결합을 가장 작은 판별로 특정하고, 같은 물리에서 수렴한 포트 응답까지 연결하는 것**이다. 다른 방법을 선택해도 아래 원인 분리와 실측 기준을 유지한다.

1. **저장 결과와 실제 함수 연결을 확인한다.** 마지막 두 표본, frozen driver, 기존 A0/M 구성 및 물리 검토를 읽는다. 원래 여기·gauge·scaling·전처리 방향·노드 행 선택을 고정한다. 테스트/manifest 수를 늘리는 것을 연구 진전으로 삼지 않는다.
2. **남은 KCL 오차의 위치와 모드를 좁힌다.** 재사용 가능한 right raw field, 기존 incidence/접점·cell 지도와 정적 구조부터 활용한다. 큰 오차가 특정 접점/순환·coarse 공간의 누락인지, 전체 스케일/블록 근사 문제인지 판별할 최소 작업을 정의한다. 재현에 없는 tighter 해 벡터가 필수라면, 이를 얻는 새 계산의 필요성과 저장 대상을 먼저 명시한다.
3. **한 가지 수정만 대조한다.** 노드 보존을 유지하는 projection/coarse correction, 더 적합한 static inverse 또는 접점 결합 개선은 후보이며 채택된 답이 아니다. 같은 A0·같은 RHS·같은 측정 항목에서 원래 단위 오차, 한 번의 적용 비용, 총 반복 비용을 함께 비교한다. 고정 iteration 수 증가만 반복하지 않는다.
4. **효과가 있으면 같은 실제 보드에 연결한다.** 이미 실패한 상태를 재검증하는 FMM 호출을 하지 않는다. 필요한 수치 정확도를 확보한 다음 같은 port18·1 MHz 조건에서 완전한 복소 Z와 방정식/KCL/전력·상호성 검사를 확인한다. 전처리 개선만으로 빠진 물리가 해결됐다고 보지 않는다.
5. **주대역·독립 설계로 확장한다.** 보드에서 효과가 없으면 확대를 멈추고 적용 범위 또는 공통 모델을 다시 검토한다. 효과가 있으면 1 kHz–100 MHz, 공진/반공진, bare/loaded, 보존된 holdout과 decap 제거·이동·값 변경의 ΔZ로 검증한다. 1 GHz 정밀화는 그 뒤다.

대표 문제에서 **동일 A의 풀이 차이 → 동일 물리의 메시/구적 차이 → 한 물리 항 변경의 차이**를 분리한다. 작은 구조에서의 직접 기준해와 실제 보드의 전류 분포·경계를 혼합하지 않는다. 과거 결과를 반복해야 한다면 변경된 입력, 새 가설 또는 회복할 누락 산출물을 구체적으로 적는다.

사용자는 형상 모서리·via·접점 주변을 세밀하게 하고 단순 영역은 크게 두는 adaptive mesh를 원한다. 다만 최근 고정 메시 실험만으로 전 코드의 adaptive 부재를 단정하지 않는다. 형상 기반 국소 정련과 해 기반 오차 추정에 따른 adaptive loop를 구별한다. 최종적으로 SPD/Touchstone 예제로 시작 해상도를 학습·선정하거나 1–2회 추가 정련하는 빠른 경로도 검토하되, 동일 사례에 맞춘 해상도를 보편적 수렴 증거로 쓰지 않는다.

자원 효율 때문에 물리를 먼저 단순화하지 않는다. 목표 장비는 **512 GB RAM / Threadripper, 가능하면 GPU**다. 이 장비의 접속은 확인되지 않았고 지금 경로는 노트북 환경이다. 정확한 공통 모델을 유지한 채 코어·메모리·주파수/여기 병렬화와 GPU 이득을 실측한다. 독립 연구·구현·검토는 병렬 진행하되 같은 대형 계산과 같은 파일 편집을 중복하지 않는다.

## 8. 필요한 코드·결과 빠른 지도

다음 경로의 `HQ/IMPL/PHYS`는 §2 절대 경로를 뜻한다. 절대 경로와 hash가 이미 코드에 박혀 있는 경우가 있으므로 이동만으로 동작할 것으로 가정하지 않는다.

| 자료 | 위치 | 읽는 이유 |
|---|---|---|
| 최근 전체 경과 | `HQ/outputs/research/ASTRA_PROGRESS_20260914.md` | 최종 상태와 물리·수치 판별 흐름 |
| 긴 실행 이력 | `HQ/outputs/research/HQ_ACTIVE_CHECKPOINT_20260914.md` | 최신 항목을 먼저 읽고 과거 ACTIVE/PREPARING 상태와 구분 |
| 기존 정적 inverse 진단 | `IMPL/outputs/child-port-fixture/static-nodal-leakage-01/static_nodal_leakage_driver.py` | frozen worker에서 실제 M을 구성하고 GMRES 진입 전 가로채는 방식 |
| 더 정확한 inverse 비교 | `IMPL/outputs/child-port-fixture/static-tighter-inverse-01/static_tighter_inverse_driver.py` | 오른쪽 전처리, 2 RHS, 원래 단위 측정, 표본별 save-before-assert |
| finite / right 코드 | `IMPL/outputs/child-port-fixture/l14-l25-finite-driver-01/finite_l14_l25_driver.py`; `IMPL/outputs/child-port-fixture/l14-l25-right-probe-01/` | 앞선 실패 원인과 올바른 적용 경로의 비교 |
| 소형 모델 | `IMPL/outputs/child-port-fixture/build_and_check.py`, `child_port_fixture.npz`, `result.json`, `return-mesh-refinement/result.json` | 이미 완성된 대표 구조·수치/공간 비교 재사용 |
| 물리·자기 contract | `PHYS/outputs/child-port-physics/PHYSICAL_CONTRACT.md`, `L14_MAGNETIC_CONTRACT.md`, `FOLLOWUP_PORT_REVIEW.md` | 부호·단위·경계·소유 근거 |
| L14/L25 자기 action | `HQ/tools/research/probe_astra_joint_p_action.py` | 임의 전류에서의 qualified action 연결. 실제 내부 구현·pins를 따라갈 것 |
| 기존 오른쪽 보정 | `HQ/tools/research/prepare_astra_hybrid_right_correction.py` | 기준 RHS와 오른쪽 전처리/보정 경로 |
| 제품 solver 계열 | `HQ/src/spd_decap_pi/_core/solver/mfdm.py`, `modal.py`, `via_peec.py` | 이미 있는 물리 구현과 적용 범위 |
| 레드팀 | `RED/outputs/red-team/20260914-120025-KST-review.md`, `STATUS.md` | 지엽적 정밀화·중복 실험·모델 완전성에 대한 독립 판단. 12시 보고서는 이후 결과를 포함하지 않음 |

### 재사용할 큰 배열

다음은 **최종 체크포인트에 기록된 해시**다. 인계 시 파일 존재와 크기는 확인했으며, 큰 배열을 전부 로드하거나 재해싱하지 않았다. 실제로 재사용할 때 해당 실행의 manifest와 필요한 입력 hash를 확인한다.

| HQ의 `outputs/research/` 아래 경로 | 저장된 SHA-256 | 용도 |
|---|---|---|
| `astra-l02-hybrid-right-correction-01/field.npz` | `960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b` | 인정된 1 MHz 기준장 |
| `astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz` | `5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92` | 같은 조건부 A0 구성 |
| `astra-finite-joint-p-20260914-01/finite/raw-finite-magnetic-field-before-gates.npz` | `38207bc8c0dbe9d9886dcb61d64ed83b00b7dcc46a76046f88ebc2a93b61e78c` | 실패한 left 해와 그 해에 대응하는 자기 작용 |
| `astra-finite-right-probe-20260914-01/finite/raw-finite-field-before-gates.npz` | `2569d379d05dd99d1a86506fbc6bfb03f768dbb18025419cf5cfcac78e7db520` | timeout 직전 right 해의 v/q/eta |

다른 모델·해의 force/flux를 섞어 위 raw field의 누락 데이터를 채우지 않는다. reciprocity/전력 식에서 ordinary transpose와 conjugate transpose를 구분하고 해당 물리 contract의 정의를 따른다.

## 9. 외부 peer review와 과거 경로

외부 검토의 순서와 commit은 다음과 같다. 최신 미커밋 HQ 연구는 이 GitHub 문서만으로 복원되지 않는다.

- 최초 외부 자료 기준: `215d7f5a69e3fec285c31778492f360e08e34851`.
- [Claude Fable 의견](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/REVIEW_CLAUDE_FABLE_2026-09-10.md): `origin/claude/peer-review-fable-20260910`, commit `3929d312a69778bee8e895fcda0a7dd09bdcab20`.
- [검토 후 수정 계획](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/9078b7ed88a932e592c1b20a51aaeb3b28dc4ebc/docs/peer-review/2026-09-10/NEXT_PLAN.md) / [레드팀 응답](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/9078b7ed88a932e592c1b20a51aaeb3b28dc4ebc/docs/peer-review/2026-09-10/RED_TEAM_RESPONSE_2026-09-10.md): 위 §1의 로컬 REVIEW 복사본을 이번에 읽었다. 원격 내용은 새로 조회하지 않았다.

채택한 것은 **공통 소유 확인 → 대표 구조에서 원인 분리 → 한 가지 수정 → 실제 보드**라는 판별 순서다. Fable의 near-L, 압축 L, MQS/plane-pair 전환은 후보이지 이미 입증된 해법이 아니다. 15–17 pH의 단자 등가 진단을 특정 plane/via의 누락 L로 단정하거나 보정 상수로 더하지 않는다. 문헌의 저항 기반 전처리에 관한 특정 예제 결론을 보편적 실패로 일반화하지 않는다. 추가 논문 조사가 필요하면 기존 검토의 원문 링크와 근거를 출발점으로 사용한다.

MAIN의 `docs/evaluation-research/D117_*`와 일부 README에는 이전 연구의 “current”, `STOP_NUMERICAL_EXECUTION`, manifest-only H4 상태가 남아 있다. 확인한 D117 문서 수정 시각은 9월 4–6일이며 **9월 14일 HQ의 후속 작업이 아니다**. D117은 별도의 geometry/material/via 입력 검증 경로로 보존한다. `D:/SPD-Decap-PI-Evaluator-W7/`에 해당 산출물이 있다. 그 문서의 consumed one-shot 결과를 재실행하거나 geometry PASS를 solver/PowerSI PASS로 바꾸지 않는다. 최신 HQ 문제를 계속하기 위해 D117의 미완료 controller·Triangle 절차부터 다시 만들 필요는 없다.

과거 문서의 단계별 “승인 대기/연구 중지” 문구는 작성 당시 범위와 후속 사용자 지시를 함께 읽는다. 현재 사용자의 Claude 이관 요청을 무시하고 매 단편마다 재승인을 요구하지 않는다. 다만 immutable 실패 증거, 물리적 유효성 조건, 실제 데이터 보존은 계속 지킨다.

## 10. 인계 완료 상태와 보고 원칙

- Codex는 공식 잔여 40%에서 연구를 중지했다. [중지 정책](<docs/handoff/2026-09-15/snapshots/HQ/outputs/research/HQ_USAGE_STOP_POLICY_20260914.json>)은 `stop_latched=true`, `all_research_stopped=true`다. 9월 15일 인계 시 Codex 잔여량은 36%로 확인했으며, 이번 문서 작업은 사용자의 별도 요청으로 수행했다.
- HQ 계산 PID 36804/39536/18080/43968과 exec sessions는 최종 중지 기록상 종료됐고, 연구 task·native agents도 완료 상태다. 옛 PID를 새 프로세스의 정체로 사용하지 않는다.
- `hq-9-7-08`(보고·사용량 감시), `pi-6`(6시간 레드팀) 모두 인계 시 **PAUSED**를 재확인했다. 이 문서 작성으로 예약을 재개하지 않았다. Reset 1회 승인은 이미 사용했으며 추가 Reset·구매 권한은 없다.
- 이번 인계는 문서·작은 JSON·파일 경로·Git 상태 확인과 저장 manifest 10개 hash 재검증이다. solver, FMM, mesh, 원본 SPD 파싱, 새 구현, 전체 테스트, 설치·빌드·배포는 하지 않았다.
- 보고는 **새 보드 복소 Z / 원인 판별 / 미수렴·실패 / 실제 총 시간 / 다음 한 가지 판단**을 중심으로 한다. 준비를 실행으로, 검토 통과를 정확도 개선으로, 작은 내부 오차 감소를 제품 성공으로 부르지 않는다.
- 사용자가 지적한 표현을 존중한다. 한국어 설명에서 낯선 “전차”를 쓰지 말고, 필요한 경우 “방정식 불일치”, “잔차(residual)”, “전류 보존 오차”처럼 뜻을 함께 쓴다.

**Claude의 첫 결과물은 위 자료를 읽고 현재 원인 가설과 한 가지 판별 작업을 명시하는 짧은 실행 판단이어야 한다. 이미 완료된 인계·검토·자료 수집을 또 다른 장기 과제로 만들지 말고, 사용자의 정확도 목표에 직접 연결되는 작업을 이어 간다.**
