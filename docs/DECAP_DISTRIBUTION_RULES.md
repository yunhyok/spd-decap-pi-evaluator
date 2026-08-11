# De-cap Distribution 변동 규칙

> 적용 프로그램: **SPD Decap PI Evaluator v0.22.0**
>
> 문서 상태: 현재 구현 및 회귀 테스트에 대응하는 동작 규칙
>
> 적용 범위: `De-cap Distribution`의 Target 입력, 후보 선정, shared-pad 변경,
> isolation gap, 결과 수량, Apply 및 파일 입출력

이 문서는 De-cap Distribution이 어떤 De-cap을 다른 PWR NET으로 변경할 수
있는지, shared-pad cluster에서 어떤 cell을 사용할 수 없게 만들어야 하는지,
그 결과를 어떻게 수량으로 계산하는지를 정의한다. 구현이나 테스트가 이 문서의
규칙을 변경하는 경우에는 같은 변경에서 이 문서도 함께 갱신해야 한다.

## 1. 기본 원칙

1. 원본 `.spd`는 직접 수정하거나 덮어쓰지 않는다.
2. Preview 계산은 현재 scenario를 변경하지 않는다.
3. 수량 목표보다 실제 전기 연결의 유효성이 우선한다.
4. 계산 결과는 PWR NET assignment와 필요한 isolation gap을 하나의 최종 상태로
   검증한다.
5. 유효한 변경을 일부만 찾은 경우 임의의 잘못된 변경으로 목표를 채우지 않고
   `PARTIAL` 결과를 반환한다.
6. 원자적 topology 검증을 통과하지 못한 Preview 또는 Apply는 fail-closed로
   거부한다.
7. Distribution의 `FULL/PARTIAL`은 수량/topology 상태이고 Evaluation의
   connectivity/modelability 상태와 동일하지 않다. Apply 후 Evaluation은 Original과
   Tuned/current 양쪽을 별도 preflight한다.

## 2. 용어와 수량 정의

| 용어 | 정의 |
| --- | --- |
| Physical Present | 현재 SPD에서 활성화되어 있고 PWR rail과 Component model을 식별할 수 있는 물리 De-cap 수 |
| Assignable | 현재 PWR NET을 변경하거나 허용된 isolation gap으로 사용할 수 있는 Physical Present의 부분집합 |
| Fixed | Present에는 포함되지만 floating dummy, unresolved, out-of-scope 등의 이유로 이동할 수 없는 De-cap |
| Target | 사용자가 지정한 최종 희망 수량 |
| Actual | 물리 및 topology 규칙을 모두 적용한 Preview의 실제 최종 수량 |
| Sent | 해당 PWR NET에서 다른 PWR NET으로 실제 이동한 De-cap 수 |
| Received | 다른 PWR NET에서 해당 PWR NET으로 실제 이동해 온 De-cap 수 |
| Sacrificed | 서로 다른 활성 PWR NET을 분리하기 위해 `ISOLATION_GAP`으로 제거한 De-cap cell 수 |
| Assignment Failed | receiver의 미충족 수요 `max(Target - Actual, 0)`; donor의 미사용 여유와 선택적인 exchange 회전은 실패로 세지 않음 |
| Shortfall | `Assignment Failed`와 같은 계산 결과 필드 |
| Anchor De-cap | 실제 PWR VIA를 가진 shared-pad De-cap |
| Dummy De-cap | 자체 PWR VIA가 없고 동일 활성 pad 구간의 anchor VIA를 사용해야 하는 De-cap |
| Isolation gap | source TOP copper가 분리 가능하다고 입증한 pad cell을 비활성화하여 PWR pad 연결을 실제로 끊은 상태 |

Physical Present 계산 시 disabled/DNP는 제외한다. 다만 일반 disabled/DNP pad는
전기적으로 계속 연결되어 있으며 isolation gap을 대신하지 않는다. Component 또는
rail을 식별할 수 없는 row도 Present에서 제외한다. 반대로 활성 floating dummy,
unresolved 및 out-of-scope row는 물리 수량인 Present에는 포함하지만 Fixed로
분류하여 공여 가능 수량에서는 제외한다.

최종 수량은 모든 PWR NET·Component cell에 대해 다음 항등식을 만족한다.

```text
Actual = Present - Sent - Sacrificed + Received
```

즉 donor가 잃는 수량에는 실제 NET 이동과 경계 pad 희생이 모두 포함된다.
receiver가 얻는 수량에는 실제로 이동해 온 De-cap만 포함되며 isolation gap은
receiver 수량으로 계산하지 않는다.

`Assignment Failed`는 특정 후보 RefDes에 대한 실패 목록이 아니다. Optimizer는
유효한 최대 부분집합만 선택하므로 receiver cell에서 채우지 못한 최종 수량을
표시한다. `0`은 해당 receiver 요청이 충족되었거나 receiver 요청이 없다는 뜻이다.

## 3. Target 표의 cell 역할

Target은 0 이상의 정수여야 한다. Tolerance는 0 이상 100 이하의 유한 실수여야
한다. 입력되지 않은 Target cell은 현재 Present로, Tolerance는 0%로 취급한다.

| 조건 | 역할 | 최종 수량 규칙 | 계산 참여 규칙 |
| --- | --- | --- | --- |
| `Target < Present` | `DONOR` | `Actual >= Target` | `Present - Target` 범위에서 공여할 수 있으나 전부 공여할 의무는 없음 |
| `Target > Present` | `RECEIVER` | `Actual <= Target` | `Target - Present`만큼 수신 요청 |
| `Target == Present`, `Tolerance = 0` | `UNCHANGED` | `Actual == Present` | 이동·교환에서 제외 |
| `Target == Present`, `Tolerance > 0` | `EXCHANGE` | `Actual == Present` | 최종 수량을 유지하며 제한된 자리 바꿈에 참여 |

Tolerance는 `Target == Present`인 cell에서만 의미가 있다. whole-De-cap 교환
허용 수량은 다음과 같이 내림 계산한다.

```text
Tolerance Count = floor(Present x Tolerance (%) / 100)
```

Tolerance Count가 0이면 실제 교환에는 참여하지 않으며
`TOLERANCE_ROUNDS_TO_ZERO` 진단을 표시한다.

EXCHANGE cell은 최종 수량이 Present와 같아야 하므로 다음 관계가 성립한다.

```text
Received = Sent + Sacrificed
Sent + Sacrificed <= Tolerance Count
Received <= Tolerance Count
```

따라서 EXCHANGE는 다른 receiver로 일부를 내어주고 다른 donor 또는 exchange에서
같은 수량을 받아오는 자리 바꿈 경로가 될 수 있다. receiver 요구가 전혀 없는
상태에서 의미 없는 exchange cycle을 새로 만들지는 않는다. `Target != Present`인
DONOR/RECEIVER cell에서는 입력된 Tolerance를 계산에 사용하지 않는다.

## 4. 계산 시작 전 수량 사전검사

수량 사전검사는 Component model별로 독립 수행한다.

```text
Donor Supply = sum(min(max(Present - Target, 0), Assignable Count))
Receiver Demand = sum(max(Target - Present, 0))
```

- `Donor Supply < Receiver Demand`이면 `NUMERIC_SUPPLY_SHORTAGE`로 계산을 중단한다.
- 이 단계에서는 scenario를 변경하지 않으며 PWR plane/VIA/거리 최적화를 시작하지
  않는다.
- Fixed row는 Present에는 포함되지만 Assignable Count에는 포함되지 않는다.
- EXCHANGE tolerance는 count-neutral이므로 이 사전검사의 Donor Supply에 더하지
  않는다.

수량 사전검사를 통과했다는 사실은 물리적으로 목표를 전부 채울 수 있다는 뜻이
아니다. PWR plane, PWR VIA, bump, shared-pad 및 isolation-gap 조건을 적용한 뒤
실제 가능한 수량이 더 작을 수 있다. 이 경우 계산을 중단하지 않고 가능한 최대
유효 결과를 `PARTIAL`로 반환한다.

## 5. 변경 후보의 물리 적격성

### 5.1 PWR plane 및 VIA

NET 변경 가능 여부는 De-cap 중심 좌표가 아니라 source SPD 연결 분석으로 확인된
고유한 물리 TOP-side PWR VIA의 정확한 landing 좌표에서 판정한다. 대상 NET의
retained PWR conductor layer 중 하나에 대해 다음을 모두 만족할 때에만
destination으로 허용한다.

- source-classified physical PWR VIA landing이 존재한다. 기존 VIA column이 대상
  layer까지 이미 span할 필요는 없다.
- immutable PWR-via landing XY가 대상 NET의 final ordered copper 내부에 strict하게
  포함된다. void, boundary contact, 누락/손상 artwork는 허용하지 않는다.
- direct De-cap 또는 변경 후의 shared-pad 활성 PWR component에 적어도 하나의
  위 조건을 만족하는 실제 PWR-via root가 있다. Dummy는 독립 root를 만들지 않는다.
- 적격성이 불명확하면 허용으로 추정하지 않고 fail-closed로 제외한다.

이는 고정된 동일 XY에서 filled-Cu microvia stack을 target NET에 맞춰
retarget/rebuild할 수 있다는 제작 계획 가정이다. preview/apply는 plane artwork,
stack-up, attachment를 변경하지 않는다. Trace/path evidence는 landing을 새로
만들거나 destination copper를 허용하거나 query XY를 옆으로 이동시키는 근거가 될 수
없다. Distribution proof는 Evaluation의 PWR/GND pair 선택과 solver 계약을 변경하지
않는다.

GND-side via/layer는 destination의 위치·layer 적격성 gate가 아니다. GND는 이
PWR channel reassignment에서 destination 판정 대상이 아니며, Evaluation Analysis의
별도 PWR/GND 조건을 Distribution에 역으로 요구하지 않는다.

이 proof projection은 직접 RECEIVER뿐 아니라 실제 whole-De-cap allowance가 있는
EXCHANGE chain의 destination에도 동일하게 수행한다. 반대로 `Target == Present`와
`Tolerance = 0`인 중립 cell은 exchange 후보를 만들지 않는다.

### 5.2 수신 PWR NET bump

Device bump는 NEAREST/FARTHEST ranking에 사용한다. destination PWR NET에 bump가
없어도 위의 physical-landing/exact-copper proof를 만족하면 수신 가능하다. 이 경우
deterministic canonical order를 사용하고 진단을 남긴다.

후보 거리는 bump가 있을 때 De-cap 좌표와 destination PWR NET의 각 bump 사이
Euclidean XY 거리 중 최솟값이다.

```text
Candidate Distance = min(distance(Decap, each destination PWR bump))
```

### 5.3 최신 연결 분석 요구

Distribution은 현재 형식의 source TOP shared-pad 연결 분석과 source-classified
physical PWR VIA landing을 필요로 한다. 분석 버전이 오래되거나 PWR landing이 없는
경우에는 추정 계산을 하지 않고 hash-matched 원본 SPD를 다시 열어 분석하도록 요구한다.

## 6. Shared-pad cluster 및 dummy 규칙

### 6.1 Cluster 연결 근거

Shared-pad cluster는 다음과 같이 source SPD에서 입증된 TOP PWR 연결만 사용한다.

- positive pad overlap
- VIA-pad overlap
- source TOP PWR copper 경로

경계에만 닿는 접촉, 불명확한 padstack/geometry 또는 상충 NET은 연결로 추정하지
않고 unresolved로 처리한다. GND copper는 서로 분리된 PWR strip을 하나의 PWR
cluster로 합치는 근거로 사용하지 않는다.

### 6.2 Dummy는 anchor VIA를 사용

Dummy De-cap에는 가상 VIA 또는 독립 eligibility를 생성하지 않는다. Dummy는 변경
후에도 동일한 활성 PWR-NET component 안에 있는 적격 anchor De-cap의 실제 PWR
VIA를 통해서만 연결될 수 있다.

따라서 다음 상태는 금지한다.

- dummy만 단독으로 NET을 변경하는 상태
- isolation gap 때문에 dummy-only 활성 구간이 남는 상태
- 바뀐 NET 구간에 적격 PWR VIA anchor가 하나도 없는 상태

모든 최종 활성 same-NET component에는 해당 NET에 적격인 실제 PWR VIA anchor가
최소 한 개 있어야 한다. 이 규칙은 사용자가 제시한 Case 3과 같은 고립 dummy를
명시적으로 차단한다.

### 6.3 서로 다른 NET 경계와 isolation gap

Shared-pad의 서로 인접한 활성 cell은 같은 NET/rail label을 가져야 한다. Cluster의
일부만 다른 NET으로 변경하여 경계가 생기면, 두 활성 NET 사이에 실제 separator
pad가 필요하다.

Isolation gap으로 사용할 수 있는 cell은 다음 조건을 모두 만족해야 한다.

1. anchored shared-pad cluster의 member이다.
2. source TOP copper가 해당 cell을 제거하면 collinear PWR path를 분리할 수 있다고
   입증하여 cluster의 `isolation_gap_refdes`에 포함했다.
3. 해당 source cell의 역할이 DONOR 또는 허용량이 남은 EXCHANGE이다.
4. 같은 cell에 NET assignment와 isolation gap을 동시에 적용하지 않는다.

Isolation gap을 적용한 cell은 `enabled = false`, `pad_state = ISOLATION_GAP`이 되고
그 cell과 incident PWR edge를 topology에서 제거한다. 단순 disabled/DNP는 pad
연결을 제거하지 않으므로 separator로 계산할 수 없다.

기존 isolation gap은 보존한다. 계산 중 선택된 새 gap이 실제로 서로 다른 NET
context를 분리하지 않는 불필요한 gap이면 복원할 수 있지만, 복원 후 전체 scenario가
원자적 topology 검증을 통과할 때만 허용한다.

### 6.4 Shared physical PWR VIA 및 rail identity

- 하나의 물리 PWR VIA가 여러 De-cap pad와 겹치면 같은 활성 same-label
  component 안에서 하나의 VIA로 중복 없이 공유할 수 있다.
- NET 변경이나 gap 적용 후 그 VIA의 owner들을 서로 다른 활성 component 또는
  서로 다른 NET으로 나눌 수 없다.
- 동일한 NET 문자열의 rail alias가 여러 개 존재하더라도, 물리적으로 short된 한
  활성 component 내부에서 서로 다른 rail ID를 혼용할 수 없다.

### 6.5 Partial cluster 변경과 원자 검증

Shared-pad cluster 전체를 무조건 한 NET으로 변경할 필요는 없다. 일부 anchor와
dummy만 변경하는 partial cluster 변경도 가능하다. 단, assignment와 필요한 gap을
모두 반영한 cluster 전체 최종 상태가 다음 조건을 동시에 만족해야 한다.

- 서로 다른 활성 NET이 gap 없이 맞닿지 않는다.
- 모든 활성 same-NET component에 적격 PWR VIA anchor가 있다.
- 하나의 물리 PWR VIA가 여러 component로 분할되지 않는다.
- 각 활성 component의 실제 PWR-via root에 대한 target destination proof가 유효하다.
- 같은 물리 short component에서 rail identity가 하나로 일치한다.

NET assignment와 isolation gap은 한 revision에서 함께 검증하고 commit한다. 중간
상태를 순차 적용하여 일시적으로 invalid한 cluster를 만드는 방식은 사용하지 않는다.

## 7. 후보 선정 및 최적화 우선순위

물리적으로 가능한 후보에 대해 다음 우선순위를 순서대로 적용한다.

1. receiver 요구 충족 수량 최대화
2. isolation-gap 희생 수량 최소화
3. 활성 PWR NET relabel 수 최소화
4. 선택한 bump 거리 순서 적용

거리 option은 다음과 같다.

| Option | 규칙 |
| --- | --- |
| `NEAREST` | destination PWR NET bump와의 거리가 작은 후보를 우선 |
| `FARTHEST` | destination PWR NET bump와의 거리가 큰 후보를 우선 |

거리 option은 안전 규칙, receiver 최대 충족, gap 최소화 및 relabel 최소화보다
우선하지 않는다. 즉 가까운 후보가 topology를 위반하거나 더 많은 gap을 요구하면
거리만을 이유로 선택하지 않는다. 동률은 REFDES/rail의 canonical 순서로
결정하여 결과를 재현 가능하게 한다.

### 시간 제한과 최적성 표시

- receiver 최대 충족을 확립할 수 없으면 fail-closed로 종료한다.
- gap 최소화가 시간 제한에 도달해도 topology-safe incumbent가 있으면 이를
  사용하고 `GAP_OPTIMIZATION_FALLBACK`을 표시한다. 이때 최소 gap임이 증명된 것은
  아니다.
- shared-pad distance에서 유효 incumbent만 얻은 경우 선택 mode를 만족하는 최선의
  유효 incumbent를 사용하되 joint optimum 미증명을 진단으로 표시한다.
- separator 위치를 고정한 뒤의 distance 최적값은 그 separator 위치에 조건부인
  결과다. 앞 단계의 joint assignment/separator 최적값까지 증명되지 않았다면
  전역 joint optimum으로 표현하지 않는다.
- fixed-separator distance에서 유효한 mode-specific incumbent도 만들 수 없으면
  임의 assignment를 내보내지 않고 `DISTANCE_OPTIMIZER_FAILED`로 종료한다.
- direct-only 문제의 distance timeout에서는 최대 충족 및 최소 turnover는
  보존하지만 primary feasible selection을 반환할 수 있다. 이 경우
  `DISTANCE_OPTIMIZATION_FALLBACK`이 표시되며 NEAREST/FARTHEST optimum은 보장하지
  않는다.

기본 계산 시간 제한은 solver group별 120초다. 내부 gap/distance 단계에는 더 짧은
제한이 적용될 수 있다. Cancel은 solver phase 또는 group 사이의 안전 지점에서
처리하며, 실행 중인 HiGHS 호출 자체를 중간에 강제 종료하지 않는다.

## 8. 결과 수량 및 상태

각 cell의 결과 수량은 다음과 같이 표시한다.

| 역할 | Requested | Fulfilled | Shortfall |
| --- | --- | --- | --- |
| DONOR | `Present - Target` | `Present - Actual` | 0 |
| RECEIVER | `Target - Present` | `Actual - Present` | `Target - Actual` |
| EXCHANGE | `Tolerance Count` | `Sent + Sacrificed` | 0 |
| UNCHANGED | 0 | 0 | 0 |

Plan 전체의 Requested/Fulfilled/Shortfall 합계는 receiver demand만 집계한다.

- 모든 receiver shortfall이 0이면 `FULL`이다.
- receiver shortfall이 하나라도 있으면 `PARTIAL`이다.
- DONOR가 공여 가능량을 모두 사용하지 않았거나 EXCHANGE allowance가 남았다는
  이유만으로 `PARTIAL`이 되지는 않는다.
- `PARTIAL`도 포함된 assignment와 gap이 물리적으로 유효하면 Apply, Export 및
  별도 `.spdpi` 저장이 가능하다.

수량/topology plan이 유효하더라도 변경 대상 rail에 기존 unresolved 또는
out-of-scope 연결이 있으면 PDN Evaluation은 별도로 차단될 수 있다. 이 경우
`PREEXISTING_UNRESOLVED_EVALUATION_RAILS` 진단을 표시하며, Distribution의
`FULL/PARTIAL`과 `PDN evaluation: BLOCKED`를 서로 다른 상태로 취급한다.

### 8.1 Evaluation Analysis handoff

Evaluation 시작 전 background comparison preflight는 선택한 각 rail의 **Original
baseline**과 **Tuned/current**를 모두 검사한다. 이 검사는 실제 project builder와 같은
connectivity/modelability 계약을 사용하며, 한쪽이라도 build할 수 없으면 해당 rail 전체를
blocked로 분류한다. 따라서 Original에서는 mounted였지만 Tuned에서 disabled가 된
De-cap도 Original footprint 검사를 생략하지 않고, model ID가 없거나 유한 port footprint가
선택 cavity를 벗어나는 경우도 UI preflight와 builder가 동일하게 fail-closed 한다.

Eligibility가 없는 unchanged source `DIRECT` connection은 import가 보존한 PWR/GND landing,
source rail/net 일치 및 rail-template binding이 모두 확인될 때만 source fallback을 사용할 수
있다. Distribution으로 이동한 assignment는 exact destination eligibility가 계속 필요하다.
이 fallback은 누락된 connectivity record를 복구하는 규칙이지 terminal 좌표를 clamp하거나,
cavity를 확장하거나, off-cavity port를 제외하는 geometry 예외가 아니다.

- 모든 선택 rail이 clear이면 전체 비교를 실행한다.
- clear와 blocked가 섞이면 전체 blocker manifest를 보여주고, 기본값 `No`인 명시적 확인 후에만
  clear rail을 실행한다.
- 이 mixed 실행은 `PARTIAL`로 표시하고 blocked rail을 `NOT evaluated`로 남긴다. blocked rail을
  성공으로 간주하거나 조용히 생략하지 않는다.
- clear rail이 하나도 없으면 Evaluation을 시작하지 않는다.
- no-decap control도 같은 검사를 받는다. 지정된 두 92-port case의 `VQPS` 10개 rail은
  Original/Tuned exact preflight에서 모두 clear다.

## 9. Preview 및 Apply 규칙

1. Preview는 입력 scenario의 design fingerprint와 revision을 기록한다.
2. Optimizer 결과를 전체 scenario에 먼저 원자적으로 적용해 topology를 재검증한다.
3. Target, Tolerance 또는 distance option이 바뀌면 기존 Preview와 Apply/Export/Save
   가능 상태를 무효화한다.
4. Apply 시 현재 scenario의 fingerprint 또는 revision이 Preview 입력과 다르면
   `PLAN_STALE`로 거부한다.
5. Apply 시 assignment와 isolation gap을 다시 한 번 원자적으로 검증한다.
6. 적용 결과 identity가 Preview의 output fingerprint/revision과 다르면
   `PLAN_TAMPERED`로 거부한다.
7. 성공한 Apply는 한 revision으로 commit하고 기존 PDN Evaluation 결과를
   무효화한다.

## 10. Target 입력 및 다중 cell 편집

- `Present`와 `Assignment Failed`는 읽기 전용이다.
- `Target`과 `Tolerance (%)`만 편집할 수 있다.
- `Ctrl`/`Shift`로 여러 cell을 선택한 뒤 숫자를 입력하면 같은 종류의 편집 가능
  cell에 동일 값을 적용한다.
- Target과 Tolerance가 섞인 selection 또는 읽기 전용 cell에는 일괄 입력하지
  않는다.
- 값은 캐시된 Present를 이용해 즉시 검증하며, 매 keystroke마다 물리 최적화를
  실행하지 않는다.
- 메인 Distribution 표의 cell을 더블클릭하면 동일 내용을 읽기 전용으로 보여주는
  비모달 분리창을 연다. 상태의 단일 소유자는 메인 창이며 분리창은 별도 사본을
  편집하지 않는다.
- 메인 창의 `Show source SPD assignments`와 분리창의 동기화된 control은 표시 label을
  `Current / distributed` 또는 `Source SPD (read-only)`로 전환한다. 이는 도면 렌더링만
  바꾸며 scenario, revision, dirty 상태, target 및 plan을 변경하지 않는다. Source SPD view는
  source NET/rail뿐 아니라 Distribution이 만든 isolation-gap의 enabled/pad 상태도 source
  상태로 복원해 표시하고, context edit를 허용하지 않는다. Search, selection 및 viewport는
  전환 전후에 보존한다.

## 11. Excel Target 가져오기

Target Workbook은 `PWR NET Distribution Targets` sheet를 사용하며 A1은
`PWR NET`이어야 한다.

분리창에서 현재 Target 표를 계산 전후 언제든 XLSX template으로 내보낼 수 있다.
사용자는 XLSX의 Target/Tolerance만 수정한 뒤 가져오며, 성공한 가져오기는 메인 표에
즉시 반영되고 기존 Preview는 무효화된다.

- 가져오는 값은 절대 수량 `Target`과 `Tolerance (%)`뿐이다.
- Workbook의 Present는 감사용이며, 현재 열린 SPD에서 다시 계산한 Present가 항상
  우선한다.
- Workbook에 없는 현재 rail/Component cell은 `Target = 현재 Present`,
  `Tolerance = 0`으로 초기화한다.
- 현재 format에 기록된 NEAREST/FARTHEST는 복원한다.
- 구형 Workbook에 distance mode가 없으면 자동 추정하지 않고 사용자가 명시적으로
  선택할 때까지 계산을 비활성화한다.
- source SPD SHA-256이 기록되어 있고 현재 SPD와 다르면 가져오기를 차단한다.
- SHA가 없으면 경고 후 rail/Component ID로 검증한다.
- SHA가 같지만 design fingerprint가 다르면 경고하고 현재 scenario를 기준으로
  target을 다시 검증한다.
- 현재 SPD에 없는 active target은 거부한다. 다만 `Target == Present`이고
  `Tolerance = 0`인 중립 cell은 무시할 수 있다.
- 중복 rail/열, 수식 cell, 범위 밖 숫자, 미지원 format 또는 잘못된 distance mode는
  fail-closed로 거부한다.

Workbook의 source name과 revision은 추적용 metadata이며 import 일치 조건은 아니다.
Source SHA-256은 일치 조건이고 design fingerprint mismatch는 현재 scenario에서
재검증해야 하는 경고 조건이다.

## 12. CSV 및 Excel 출력

CSV와 Excel의 `Decap Changes` 데이터는 변경분만이 아니라 scenario의 전체
De-cap을 다음 순서로 출력한다.

```text
Component, REFDES, Before NET, After NET, X (um), Y (um)
```

새 isolation gap의 `After NET`은 `UNUSED (ISOLATION GAP)`으로 기록한다.

Excel은 정확히 두 sheet를 생성한다.

1. `Decap Changes`
   - 전체 De-cap의 6개 결과 열
2. `PWR NET Distribution Targets`
   - 계산 당시의 immutable Present/Target/Tolerance
   - Actual Delta, Assignment Failed, Isolation Gaps
   - 전체 inventory reconciliation
   - application/format version, source SPD 이름 및 SHA-256
   - 입력 fingerprint/revision과 distance mode

Apply 후 GUI의 Present가 새 최종 수량으로 바뀌더라도 export의 두 번째 sheet는
해당 Preview를 계산했을 당시의 입력 표를 보존한다. 식으로 해석될 수 있는 식별자는
문자열로 기록하여 spreadsheet formula injection을 방지한다.

## 13. 주요 진단과 처리 방식

| 코드 | 의미 | 처리 |
| --- | --- | --- |
| `NUMERIC_SUPPLY_SHORTAGE` | Component별 수치 공여량이 수신 요구보다 작음 | 물리 계산 전 중단 |
| `TOLERANCE_ROUNDS_TO_ZERO` | 양수 tolerance가 whole-De-cap 0개로 내림됨 | 해당 exchange 비활성, 계산 계속 |
| `PHYSICAL_CAPACITY_SHORTAGE` | 수치 검사는 통과했지만 물리 후보가 부족함 | 유효 최대 결과를 `PARTIAL`로 반환 |
| `SHARED_PAD_DUMMY_ISLAND` | 변경 후 VIA 없는 dummy-only 구간 발생 | 변경 거부 |
| `SHARED_PAD_ACTIVE_SHORT` | gap 없이 서로 다른 활성 NET이 맞닿음 | 변경 거부 |
| `SHARED_PAD_PWR_VIA_SPLIT` | 하나의 물리 PWR VIA가 여러 component로 분할됨 | 변경 거부 |
| `RAIL_INELIGIBLE_AT_PWR_VIA` | 구간의 물리 VIA에서 target rail이 적격하지 않음 | 변경 거부 |
| `ISOLATION_GAP_NOT_PROVEN` | source TOP copper가 해당 separator cell을 입증하지 않음 | gap 적용 거부 |
| `PREEXISTING_UNRESOLVED_EVALUATION_RAILS` | 기존 연결 문제로 touched rail의 PDN Evaluation 불가 | Preview는 유지, Evaluation은 차단 |
| `GAP_OPTIMIZATION_FALLBACK` | 유효 gap incumbent는 있으나 최소 개수 미증명 | 진단과 함께 유효 결과 사용 |
| `DISTANCE_OPTIMIZATION_FALLBACK` | distance optimum 미증명 | 진단에 명시된 fallback 사용 |
| `DISTANCE_OPTIMIZER_FAILED` | 유효한 mode-specific distance 결과 없음 | 임의 결과 없이 중단 |
| `PLAN_STALE` | Preview 후 scenario가 변경됨 | Apply 거부, 재계산 필요 |
| `PLAN_TAMPERED` | 적용 결과 identity가 Preview와 불일치 | Apply 거부 |

## 14. 대표 사례

### Case A: Direct De-cap 이동

PWR_A의 Component X가 10개를 공여할 수 있고 PWR_B가 4개를 요구한다. PWR_B의
bump와 PWR plane/VIA eligibility가 유효한 후보가 4개 이상이면, 다른 우선순위가
같을 때 NEAREST 또는 FARTHEST option에 따라 4개를 선정한다.

### Case B: Cluster 경계에 gap 한 개가 필요한 이동

PWR_A로 short된 cluster 끝부분을 PWR_B로 변경하면서 두 NET 사이의 source-proven
pad cell 하나를 제거해야 한다면 다음과 같이 계산한다.

```text
PWR_A Sent = PWR_B로 이동한 수
PWR_A Sacrificed = 1
PWR_A Actual 감소 = Sent + 1
PWR_B Received = Sent
```

따라서 PWR_B가 1개를 받아도 PWR_A는 이동 1개와 gap 1개를 합쳐 2개를 잃을 수
있다. Donor의 Target lower bound를 위반하면 이 후보는 사용할 수 없다.

### Case C: Dummy-only island

일부 anchor를 다른 NET으로 변경하거나 중간 cell을 gap으로 만들었을 때 원래 NET의
dummy만 남고 실제 PWR VIA에 도달할 수 없다면 변경을 허용하지 않는다. 총 수량이
맞거나 dummy가 다른 De-cap과 인접해 보이더라도 적격 anchor와 같은 활성
same-NET component에 속하지 않으면 invalid다.

### Case D: Tolerance exchange

PWR_B의 Present와 Target이 1,000개이고 Tolerance가 1%이면 최대 10개의 turnover를
허용한다. PWR_B가 PWR_C에 10개를 보내면서 PWR_A에서 10개를 받으면 Actual은
1,000개로 유지된다. 경계 gap이 1개 필요하다면 그 gap도 PWR_B의 10개 allowance와
실제 감소에 포함된다. 따라서 이 allowance 안에서는 최대 `Sent = 9`,
`Sacrificed = 1`, `Received = 10`과 같이 구성해야 하며, 10개를 보내고 gap 1개를
추가하는 11개 turnover는 허용하지 않는다.

## 14A. Filled-Cu microvia stack 재지정 가정

Distribution에서는 source SPD 연결 분석으로 확인된 물리적인 TOP-side PWR VIA
landing의 고정 XY를 모든 retained destination PWR plane에 수직으로 투영한다. 해당
plane의 최종 ordered copper가 이 XY를 strict하게 포함할 때만 destination으로
허용한다(VOID 및 boundary 접촉은 불가).

이 판정은 동일 XY에서 filled-Cu microvia stack을 target NET에 맞춰
retarget/rebuild할 수 있다는 제작 계획 가정에 기반한다. 따라서 기존 VIA column이
destination layer까지 이미 span할 필요는 없다. Trace/path evidence는 PWR landing을
새로 만들거나, destination copper를 허용하거나, query XY를 옆으로 이동시키는 근거가
될 수 없다. PWR plane artwork는 변경하지 않는다. GND는 destination gate가 아니며,
dummy는 독립 PWR root를 만들지 않고 기존 anchored shared-pad rule을 그대로 따른다.

## 15. 구현 및 회귀 테스트 추적

주요 규칙의 구현 위치는 다음과 같다.

- Target/role/tolerance 및 수량 사전검사:
  [`src/spd_decap_pi/distribution.py`](../src/spd_decap_pi/distribution.py)
- 물리 PWR plane/VIA eligibility:
  [`src/spd_decap_pi/eligibility.py`](../src/spd_decap_pi/eligibility.py)
- shared-pad current component와 dummy/VIA 규칙:
  [`src/spd_decap_pi/scenario.py`](../src/spd_decap_pi/scenario.py)
- atomic assignment/isolation-gap 검증:
  [`src/spd_decap_pi/scenario_edits.py`](../src/spd_decap_pi/scenario_edits.py)
- source TOP shared-pad 분석:
  [`src/spd_decap_pi/_core/io/shared_pad.py`](../src/spd_decap_pi/_core/io/shared_pad.py)
- Target Workbook import:
  [`src/spd_decap_pi/distribution_workbook.py`](../src/spd_decap_pi/distribution_workbook.py)
- CSV/Excel export:
  [`src/spd_decap_pi/spreadsheet_export.py`](../src/spd_decap_pi/spreadsheet_export.py)
- GUI validation/Apply/Save:
  [`src/spd_decap_pi/gui/main_window.py`](../src/spd_decap_pi/gui/main_window.py)

관련 회귀 테스트:

- [`tests/test_spd_decap_distribution.py`](../tests/test_spd_decap_distribution.py)
- [`tests/test_spd_decap_distribution_gui.py`](../tests/test_spd_decap_distribution_gui.py)
- [`tests/test_spd_decap_distribution_workbook.py`](../tests/test_spd_decap_distribution_workbook.py)
- [`tests/test_spd_decap_spreadsheet_export.py`](../tests/test_spd_decap_spreadsheet_export.py)
- [`tests/test_spd_decap_scenario_edits.py`](../tests/test_spd_decap_scenario_edits.py)
- [`tests/test_shared_pad_cluster_core.py`](../tests/test_shared_pad_cluster_core.py)
- [`tests/test_spd_shared_pad.py`](../tests/test_spd_shared_pad.py)
- [`tests/test_spd_decap_spd_adapter.py`](../tests/test_spd_decap_spd_adapter.py)
- [`tests/test_spd_decap_eligibility.py`](../tests/test_spd_decap_eligibility.py)
- [`tests/test_spd_decap_evaluation.py`](../tests/test_spd_decap_evaluation.py)

이 문서는 최종 PowerSI/SIwave correlation 또는 PDN 성능 보장을 정의하지 않는다.
Distribution은 현재 SPD에서 전기적으로 유효한 De-cap assignment 후보를 계산하는
기능이며, 변경 후 PI Evaluation과 최종 sign-off 해석은 별도로 수행해야 한다.
