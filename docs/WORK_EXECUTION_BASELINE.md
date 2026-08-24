# SPD Decap PI Evaluator 작업 기준

- 적용 제품: **SPD Decap PI Evaluator v0.23.0**
- 문서 버전: **1.3**
- 상위 기준: [목적·기술 기준](PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md) v1.2
- 현행 source 기준: `main` commit `9ce0d65d4190d55c8abb6ee157bd30f1031f2309`
- 상태: **ACTIVE CONTROL DOCUMENT — W5-GATE DRAFT; 사용자 승인 대기**
- 최종 개정: 2026-08-24 (Asia/Seoul)

## 1. 압축 후 즉시 복구 카드

context가 압축되거나 새 session에서 작업을 재개하면 다른 연구 문서를 먼저
읽지 말고 이 표부터 확인한다.

| 항목 | 현재 값 |
|---|---|
| 변하지 않는 목적 | source-derived single-rail `Zii`의 PowerSI 근접 정확성과 일반화 |
| 현재 branch | `main`만 사용; 정리된 과거 branch를 다시 조사하지 않음 |
| 현재 active work item | `W5-GATE` — docs-only DRAFT; numeric/partition/manifest 승인 대기 |
| 다음 권장 묶음 | `W6-BASE`는 W5 승인 후에만 검토 |
| 코드 수정 권한 | docs-only DRAFT; production/runtime code 수정 금지 |
| 고비용 검증 권한 | `W5-GATE` 사용자 승인 전 numerical/PowerSI 실행 금지 |
| 현재 정확성 상태 | `current / unknown / not_run` |
| 현재 release 계산 증거 | import/save/solver-entry만 통과; frequency solve `0` |
| 현재 working tree | W5 DRAFT threshold/partition/manifest만 문서화; W6 candidate 없음 |

최초 목적은 [목적·기술 기준 2장](PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md#2-최우선-목적),
현재 증거 상태는 [6장](PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md#6-증거와-상태-표기)이
권위 있다. 이 문서는 그것을 재정의하지 않는다.

## 2. 문서 사용 규칙

### 2.1 두 문서만 기본 context로 사용

작업 시작 시 기본 입력은 다음 두 개뿐이다.

1. `PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md`
2. `WORK_EXECUTION_BASELINE.md`

active work item에 명시된 source/test/subsystem 문서만 추가로 읽는다. 전체
`docs/evaluation-research`, session log, 과거 release note 또는 branch 역사를
일괄 로드하지 않는다.

### 2.2 이 문서가 관리하는 것

- 현재 active work item 하나
- 우선순위와 명시적 제외 범위
- 변경 묶음과 root-cause 경계
- 검증 단계, 실행 조건, 예상 비용, 최대 횟수와 중단 조건
- exact source/input/profile/artifact identity
- 완료 결과와 다음 사용자 결정

목적, 제품 sign-off 범위 또는 PowerSI 수치 합격선은 이 문서에서 임의로 바꾸지
않는다. 변경이 필요하면 작업을 멈추고 사용자 검토 후 상위 기준부터 개정한다.

### 2.3 갱신 시점

이 문서는 다음 시점에만 짧게 갱신한다.

1. 사용자가 active work item을 승인했을 때
2. 변경 묶음이나 검증 예산을 확정했을 때
3. milestone 검증 직전과 직후
4. item 완료, 차단 또는 명시적 보류 시

대화별 진행 로그, 긴 stdout, 반복 설명은 넣지 않는다. raw log/artifact는 별도
경로에 두고 이 문서에는 identity, 요약 판정과 링크만 기록한다.

## 3. 작업 우선순위와 단계

제품 위험 우선순위와 실제 실행 순서를 구분한다. PowerSI 정확성은 최상위 제품
위험이지만, 수정 효과를 판정할 test/evidence 기반부터 최소 비용으로 복구한다.

| 단계 | 목적 | 종료 조건 | 고비용 검증 |
|---|---|---|---|
| `W0` | 두 canonical 문서 고정 | 목적/작업 문서 상호 링크와 문서 검증 | 금지 |
| `W1` | product-core test truth 복원 | stale test 계약 정리, bounded core selection 확정 | 금지 |
| `W2` | 범위가 확정된 SPD/I/O 결함 수정 | focused checks 통과, 실제 결함별 회귀 check 존재 | 금지 |
| `W3` | product-core CI gate 활성화 | W1/W2 묶음이 한 번의 core suite에서 green | production solve 금지 |
| `W4` | solver 수치 신뢰성 선행 문제 해결 | synthetic/analytic focused gate 통과 | full correlation 금지 |
| `W5` | 정확성 계약과 frozen baseline 준비 | 사용자 승인 수치 gate, reference partition, exact run manifest | 실행 전 승인 필요 |
| `W6` | current frozen candidate 1회 baseline | completed solve와 raw hash-bound artifact, rail별 판정 | production/PowerSI 1회 |
| `W7` | model-form error를 한 owning block씩 개선 | 사전 가설과 focused evidence 통과 | 승인된 candidate만 1회 |
| `W8` | completed-solve release gate 및 전달 | 계산·artifact·installer가 exact release commit에 결속 | 최종 1회 |

`W1`부터 `W4`까지는 production SPD 전체 correlation 없이 닫는 것이 원칙이다.
`W6`는 낮은 단계가 모두 통과하고 사용자가 run manifest를 승인한 뒤에만 시작한다.

## 4. 작업 항목 register

상태 어휘는 `READY`, `ACTIVE`, `BLOCKED`, `DEFERRED`, `DONE`만 사용한다. 동시에
`ACTIVE`는 하나만 허용한다.

| ID | 순서 | 상태 | 작업 묶음 | 완료 기준 |
|---|---:|---|---|---|
| `W0-DOC` | 0 | DONE | 목적·기술 기준과 이 작업 기준 작성 | 상호 링크, Markdown/link/diff 검증 |
| `W1-TEST` | 1 | DONE | stale test double·구형 정책 기대를 current v0.23 계약에 맞게 정리하고 product-core selection 고정 | 실제 결함은 red로 남기고 test 자체 오류 제거 |
| `W2-SPD-A` | 2 | DONE | source-graph target contact persistence 복구 | target-layer coordinate가 저장·사용되는 focused regression |
| `W2-SPD-B` | 3 | DONE | graph-contact `source_sha256` 교차 검증 | 다른 source coordinate가 scenario validation에서 차단 |
| `W2-SPD-C` | 4 | DONE | `blocking:false` mixed-reference warning이 import를 중단하는 문제 수정 | warning-only case import 성공, blocking case 차단 유지 |
| `W2-IO-A` | 5 | DONE | Distribution/Tuned CSV atomic replace | write 실패 시 기존 파일 보존 |
| `W2-IO-B` | 6 | DONE | 대형 `.spdpi` load cancellation과 load 중 close 경로 | 기존 loader callback 재사용, 취소 후 stale state 없음 |
| `W3-CI` | 7 | DONE | 짧은 product-core lane을 required CI로 연결 | parser/scenario/solver/Distribution/GUI I/O 핵심 경로 green |
| `W4-FREQ` | 8 | DONE | adaptive frequency가 sample 사이 narrow peak를 보지 않고 converged 처리하는 blind spot | midpoint/coverage focused case가 peak 누락을 검출 |
| `W4-COND` | 9 | DONE | ill-conditioned sparse solve의 결과 신뢰성 gate | residual과 별도 conditioning/forward-reliability 판정 |
| `W5-GATE` | 10 | ACTIVE | 제품 PowerSI 수치 gate와 development/holdout/unseen partition의 DRAFT 승인 준비 | 사용자 numeric/partition/manifest/hash-rotation 승인 필요 |
| `W6-BASE` | 11 | BLOCKED | exact current solver baseline 1회 | W1–W5 완료와 run manifest 승인 필요 |
| `W7-PHYS` | 12 | BLOCKED | 가장 큰 error component의 owning physical block 하나 수정 | W6 rail별 error decomposition 필요 |
| `W8-REL` | 13 | BLOCKED | completed known-case solve를 release gate에 연결하고 최종 전달 | accuracy/product gate와 exact release commit 필요 |
| `D-DIST` | - | DEFERRED | Distribution routing/DRC scope 확대 | 사용자가 implementation-ready/DRC 목표로 승격할 때만 |
| `D-DOC` | - | DEFERRED | README와 동결 연구 배너 정리 | current work를 방해할 때 별도 문서 묶음으로 처리 |

각 항목의 source 위치와 현재 근거는 해당 item을 `ACTIVE`로 바꿀 때만 기록한다.
미리 모든 caller와 연구 문서를 이 파일에 복제하지 않는다.

## 5. 검증 사다리와 실행 예산

아래 단계에서 필요한 가장 낮은 rung만 사용한다. 상위 rung이 green이라고 하위
문제의 root cause를 설명하는 것은 아니며, 하위 rung이 red이면 상위로 가지 않는다.

| 등급 | 내용 | 기본 최대 횟수 | 실행 조건 |
|---|---|---:|---|
| `V0` | 문서 link/structure, `git diff --check`, 정적 source 확인 | 변경 묶음당 1회 | 문서 또는 계획 변경 종료 시 |
| `V1` | 한 root cause를 재현하는 focused test/self-check | 구현 묶음 후 1회 | 해당 item acceptance를 직접 판정할 때 |
| `V2` | 관련 subsystem test selection | item 묶음 종료 후 1회 | 모든 V1이 green일 때 |
| `V3` | bounded product-core suite 또는 small known-case completed solve | frozen phase당 1회 | W2/W4 같은 phase 종료 시 |
| `V4` | production SPD/PowerSI full solve·correlation | 승인된 frozen candidate당 1회 | W5 run manifest 승인 후 |
| `V5` | package, installed smoke, release artifact/CI | exact release commit당 1회 | 기능·정확성 gate 완료 후 |

실패 후 같은 명령을 그대로 반복하지 않는다. `V1–V3` 재실행은 실패 원인을
설명하는 코드·test·fixture 변경이 생긴 경우 한 번만 허용한다. 두 번째 실패는
더 작은 재현으로 돌아간다.

`V4`는 자동 재시도하지 않는다. 실패·중단·자원 초과가 발생하면 raw evidence를
보존하고 원인을 저비용 단계로 축소한 뒤 사용자에게 다음 candidate/run 승인을
받는다. `V5`는 intermediate code나 문서 변경 때문에 실행하지 않는다.

## 6. 변경 유형별 최소 검증

| 변경 유형 | 필수 | 이번 묶음에서 하지 않는 것 |
|---|---|---|
| 목적/작업 문서 | `V0` | pytest, solver, installer |
| stale test 계약만 수정 | 해당 test file `V1` | production input, package |
| SPD import/provenance | focused negative/positive `V1`, 묶음 끝 import subsystem `V2` | PowerSI correlation |
| CSV/save/load/cancel | failure-preservation `V1`, GUI/I/O selection `V2` | solver/installer |
| CI workflow | workflow text/selection 확인 후 product-core `V3` 1회 | full research suite 반복 |
| solver sampling/conditioning | synthetic/analytic `V1`, bounded solver `V2` | 곧바로 production correlation |
| physical model/profile/compiler | local oracle `V1`, known-case `V3`; milestone이면 `V4` | 한 수정마다 full correlation |
| release/installer | `V5` | 미완료 기능을 package green으로 대신 증명 |

전체 2,434개 test를 매 PR에서 반드시 실행하는 것이 목표는 아니다. product-core와
heavy research suite를 분리하고, full suite가 필요한 시점은 active work item에서
사전에 한 번 지정한다.

## 7. Active work item 작성 형식

사용자 지시를 받으면 아래 block 하나를 이 절의 맨 위에 작성한다. 완료 후 짧은
결과 row로 줄이고 다음 item을 자동 시작하지 않는다.

```text
ID / 상태:
사용자 목적과의 연결:
이번 변경 묶음:
명시적 제외 범위:
root-cause 가설:
읽을 source/test/subsystem 문서:
acceptance:
V0–V5 계획과 최대 횟수:
중단 조건:
evidence identity before:
결과 / artifact / diff:
다음 사용자 결정:
```

ID / 상태: `W4-COND / DONE`
사용자 목적과의 연결: sparse factor가 작은 backward residual을 내더라도 극단적인 U-pivot spread로 forward reliability가 무너지는 결과를 product-core 경계에서 fail-closed 한다. 이는 model-form 또는 PowerSI 정확성 증거가 아니다.
이번 변경 묶음: `layer_surface_network.py`의 기존 U-diagonal ratio 계산에 `1.0e13` ceiling과 invalid-pivot rejection을 추가하고 residual gate 이후 forward-reliability rejection을 적용했다. cache payload에도 동일 ceiling을 검증하며 solver identity를 `modal-mvp-0.8.4`로 갱신했다.
명시적 제외 범위: fallback/reordering/pivot tuning/clamp, residual gate 완화, compiler `kron-v8`, convergence `v5`, app `v0.23.0`, model physics, W5 hash-bound PowerSI validation, production SPD/PowerSI, installer/release.
root-cause 가설: 기존 코드는 `factor.U.diagonal()` ratio를 진단에만 기록하고 극단적인 spread를 결과·cache에 허용했다.
읽을 source/test/subsystem 문서: 목적·기술 기준, 이 작업 기준, `layer_surface_network.py` factor/cache 경계, `tests/test_layer_surface_network.py`, `tests/test_layerwise_network.py`, 그리고 W5 승인 전 재생성하지 않는 frozen pre-W4 validator/non-regression scripts.
acceptance: 실제 SuperLU `solve`를 위임하는 wrapper가 backward residual `<=1e-9`인 상태에서 1:1e-17 pivot ratio를 forward-reliability wording으로 거부한다. 1:1e-13(정확히 `1.0e13`)은 admittance/residual/보고 ratio를 보존하며 허용된다. 빈/nonfinite/nonpositive pivot과 초과 cache payload는 fail-closed다.
V0–V5 계획과 최대 횟수: conditioning adversarial V1 red 1회; adversarial+ceiling V1 green `2 passed` 1회; layer-surface/layerwise plus exact solver identity V2 `94 passed` 1회; resulting CI bounded V3 selection 1회; V0 YAML/diff 정적 확인 1회; V4–V5와 production solve 금지.
중단 조건: residual gate와 forward gate를 혼합해야 하거나, fallback/reordering/physics 변경이 필요하거나, W5 numerical/PowerSI run 승인이 필요한 경우.
evidence identity before: `main` / `3b6ed2cc6308179002fee817c1b14f7efebd6cb9` / solver `modal-mvp-0.8.3` / compiler `kron-v8` / convergence `v5`.
결과 / artifact / diff: conditioning red node는 `1 failed`; 최소 solver/cache gate 후 adversarial+ceiling focused는 `2 passed in 0.72s`였다. 최종 `pytest -q tests/test_layer_surface_network.py tests/test_layerwise_network.py tests/test_spd_decap_evaluation.py::test_solver_version_0_8_2_recalculates_0_6_baseline_cache`는 `94 passed in 2.16s`였다. source-before는 `main` / `b2608a260a1f952c9b1f6c11d57b71e060ae575c`이며, 그 상태에서 workflow Test command에 두 W4 conditioning gate를 보존적으로 추가한 resulting bounded V3 selection을 `QT_QPA_PLATFORM=offscreen`으로 1회 실행해 `325 passed, 1 skipped in 18.25s`였다. Required CI command에는 두 W4 gate node가 유지된다. W4-FREQ synthetic closure는 별도 commit에서 완료되었고, 이번 묶음은 forward-reliability gate와 v0.8.4 live identity에 한정한다. W5 hash-bound validator/non-regression scripts와 기존 artifact identity는 pre-W4 값으로 동결해 두었으며 W5 사용자 승인 전 갱신하지 않는다. remote CI/full suite/production SPD/PowerSI/installer/release는 실행하지 않았다. accuracy는 `unknown / not_run`; model-form/PowerSI 수치 합격을 주장하지 않는다.
다음 사용자 결정: W5-GATE를 승인할지 결정.

ID / 상태: `W5-GATE / ACTIVE (docs-only DRAFT)`
사용자 목적과의 연결: PowerSI 근접 정확성과 새로운 설계 일반화를 판정할 수치,
reference partition, exact run manifest를 사용자 승인 가능한 형태로 고정한다.
현재 수치와 분류는 모두 **unapproved policy judgment**이며 historical evidence나
제품 sign-off가 아니다. P5 unseen design이 없어 final accuracy/generalization
promotion은 BLOCKED다.
이번 변경 묶음: 이 문서, `PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md`,
`EVALUATION_ACCURACY.md`에 W5 DRAFT threshold/formula, strata/partition,
W6 manifest, hash-rotation 경계를 기록한다.
명시적 제외 범위: source/runtime/solver/validator/policy JSON/historical fixture
수정, 새 script/JSON/doc 생성, numerical/PowerSI/test 실행, W6 fresh import,
production SPD, installer/release/remote CI.
root-cause 가설: 현재는 승인된 numeric gate와 blind/unseen partition 및
hash-bound manifest가 없어 과거 비교값을 product accuracy로 승격할 수 없다.
읽을 source/test/subsystem 문서: 두 canonical baseline과 `EVALUATION_ACCURACY.md`
만 사용하며, frozen validator/fixture는 identity 확인 외에 변경하지 않는다.
acceptance: low-frequency/critical magnitude/phase/low-Z complex/peak DRAFT
threshold와 260729/260804 strata, P1–P5 blockers, W6 manifest 및 hash rotation이
모두 승인 전 상태로 표시된다. W6는 at most non-blind retrospective baseline이다.
V0–V5 계획과 최대 횟수: 세 문서 cross-document string/status static check 1회와
`git diff --check` 1회; numerical/PowerSI/tests 및 V1–V5 실행 금지.
중단 조건: 사용자가 숫자, strata 경계, manifest identity 또는 hash rotation을
승인하지 않은 상태에서 candidate/import/correlation을 시작해야 하는 경우.
evidence identity before: `main` / `9ce0d65d4190d55c8abb6ee157bd30f1031f2309` /
runtime `modal-mvp-0.8.4` / convergence `adaptive-frequency-modal-v5` /
compiler `kron-v8`.
결과 / artifact / diff: W5 DRAFT 문서만 작성했다. 260729/260804 raw+S92P
path 존재/size 일치 사실은 등록 정책에서 가져오되 SHA는 이 turn에 재계산하지
않았고, candidate `.spdpi`는 absent/fresh import required다. Bare VQPS 10개와
loaded VTRIP/VINT/VCPU 6개는 별도 strata이며 pooled 판정을 하지 않는다. P1–P4
blocker와 P5 missing을 유지하고, W5 hash-bound validators/policy JSON/historical
fixtures는 pre-W4 값으로 frozen 상태다. accuracy `unknown / not_run`.
다음 사용자 결정(정확히 네 그룹): (1) numeric thresholds/formulas, (2) partition/
strata 및 P1–P5 blocker 해석, (3) W6 manifest/runtime/resource/output contract,
(4) hash rotation과 frozen validator/fixture 보존 정책.

## 8. Context 압축·새 session 복구 절차

1. 상위 목적 문서와 이 문서의 `압축 후 즉시 복구 카드`만 읽는다.
2. `git branch --show-current`, `git rev-parse HEAD`, `git status --short`로 실제
   checkout을 확인한다.
3. `main`이 아니거나 recorded source 기준과 예상하지 않은 차이가 있으면 작업을
   시작하지 않고 상태를 보고한다.
4. active item이 `NONE`이면 추측으로 다음 코드를 수정하지 않고 사용자 지시를
   기다린다.
5. active item이 있으면 그 item에 적힌 source/test만 읽고 caller를 end-to-end로
   추적한다.
6. context가 사라졌다는 이유만으로 이전 검증을 다시 실행하지 않는다. exact
   identity가 같은 기존 evidence를 사용하고, 불명확하면 `unknown`으로 표시한다.
7. 구현 전 최초 목적, active item, 제외 범위와 검증 예산을 한 번 재확인한다.
8. 다른 문제가 보이면 backlog 후보로 한 줄 기록할 수 있지만 현재 item을
   확장하지 않는다.

## 9. 중단·사용자 검토 조건

다음 중 하나면 안전한 read-only 확인까지만 하고 멈춘다.

- 목적 또는 제품 PowerSI 합격선을 바꿔야 함
- 한 번에 둘 이상의 physical owning block을 바꿔야 결과를 설명할 수 없음
- 사전 예산에 없는 `V4` 또는 `V5`가 필요함
- 같은 고비용 검증을 원인 변경 없이 다시 실행하려 함
- source/reference/port/profile/compiler identity가 불명확함
- 실제 입력, 외부 권한, release authority 또는 사용자 선택이 필요함
- 사용자 변경과 active item이 같은 파일에서 충돌함

어려움, 긴 runtime 또는 test 수가 많다는 이유만으로 범위를 넓히거나 목적을
바꾸지 않는다.

## 10. 현재 결정 기록

| ID | 결정 | 상태 |
|---|---|---|
| `D-001` | `main`만 대상으로 하며 정리된 branch를 다시 감사하지 않음 | 확정 |
| `D-002` | PowerSI 근접 Evaluation 정확성이 최우선이고 Distribution은 2차 목적 | 확정 |
| `D-003` | PowerSI는 comparison-only이며 fitting 입력이 아님 | 확정 |
| `D-004` | 제품 PowerSI 수치 합격선은 사용자 승인 전 미확정 | 확정 |
| `D-005` | 작은 수정마다 전체 검증하지 않고 frozen milestone에서 1회 실행 | 확정 |
| `D-006` | context 기본 입력은 두 canonical 문서뿐 | 확정 |
| `D-007` | 현재 active code item은 없으며 다음 item을 자동 시작하지 않음 | 확정 |

## 11. 현재 evidence와 비재사용 경계

- `main`, `origin/main`, tag `v0.23.0`은 검토 기준 commit `0f24363c`에서
  일치했다.
- v0.23 production attestation의 import/save/92-rail solver entry는 그 범위에
  한해 사용 가능하다. completed frequency solve 또는 PowerSI 정확성 증거로
  재사용하지 않는다.
- 문서에 남은 historical v0.22 loaded correlation은 model-form failure를
  가리키지만 raw report가 Git에 없어 current baseline 숫자로 재사용하지 않는다.
- W1/W2 focused evidence는 graph-contact persistence/source provenance와
  mixed-reference warning/blocking 경계를 각각 확인했다. 이 evidence는 해당
  commit·노드 범위 밖의 product accuracy 또는 PowerSI 증거로 재사용하지 않는다.
- W4-FREQ synthetic focused solver checks는 해당 acceptance에 한해 사용했으며,
  이를 production SPD/PowerSI 정확성 증거로 재사용하지 않는다. production SPD,
  PowerSI, installer 검증은 실행하지 않는다.

## 12. 변경 기록

| 문서 버전 | 날짜 | 변경 |
|---|---|---|
| 1.0 | 2026-08-24 | 두 문서 기반 작업 통제, 우선순위 register, 검증 사다리·최대 횟수, active-item 형식, context 복구와 중단 조건을 생성. |
| 1.1 | 2026-08-24 | W4-FREQ midpoint coverage gate 완료, v5 identity와 focused evidence를 기록하고 W4-COND를 다음 item으로 지정. |
| 1.2 | 2026-08-24 | W4-COND forward-reliability gate와 v0.8.4 solver identity 완료, W5-GATE 승인 대기로 전환. |
| 1.3 | 2026-08-24 | W5-GATE DRAFT threshold/partition/manifest/hash-rotation을 문서화하고 사용자 승인 전 실행을 차단. |
