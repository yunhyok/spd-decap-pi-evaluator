# De-cap Distribution trace 회피 개선 지시서

> 적용 프로그램: **SPD Decap PI Evaluator**
>
> GitHub 원격 게시 baseline: **v0.21.0 (`origin/main`)**
>
> 연구에 사용한 로컬 snapshot: **v0.22.0 working tree (미출시)**
>
> 문서 상태: **향후 코드 수정을 위한 연구·개선 지시서**
>
> 작성 기준일: **2026-08-09**
>
> 현재 상태: 이 문서 작성 시점에는 production code에 trace 회피 기능을 구현하지 않았다.

## 1. 목적

De-cap Distribution은 현재 고정된 PWR-via landing XY가 목적 PWR plane의 최종
ordered copper 내부에 있는지를 검사한 뒤, 해당 XY에서 목적층까지 filled-Cu
microvia stack을 재구축한다고 가정한다. 이때 중간 conductor layer에 변경할 수 없는
signal trace가 있으면 새 PWR via stack이 trace copper 또는 요구 clearance를 침범할 수
있다.

향후 구현은 다음 조건을 만족해야 한다.

1. trace 보호 기능을 사용자가 켜고 끌 수 있어야 한다.
2. 보호 기능을 켠 경우 trace 또는 clearance를 침범하는 후보는 optimizer 진입 전에
   물리적으로 제외해야 한다.
3. shielding GND의 positive plane/pour는 장애물로 취급하지 않는다.
4. raw SPD의 모든 `Trace` 레코드를 곧바로 signal copper라고 간주하지 않는다.
5. 물리 근거가 불완전한 후보를 조용히 허용하지 않는다.
6. 원본 SPD와 retained plane artwork는 변경하지 않는다.
7. 보호 기능을 끈 경우 기존 Distribution 결과가 완전히 동일해야 한다.

이 기능은 MILP 목적함수의 soft penalty가 아니다. 수량 목표를 맞추기 위해 trace를
침범할 수 없으므로 **선택 가능한 via–목적 PWR layer 후보를 사전에 제거하는 hard
feasibility filter**로 구현한다.

## 2. 이번 연구의 범위와 비범위

### 2.1 이번 연구에서 확정하는 범위

- SPD Node/Trace/stackup/padstack을 이용한 trace obstacle 추출 규칙
- TOP 또는 BOTTOM 장착면에서 목적 PWR layer까지의 검사 span
- trace와 planned via copper 사이의 거리 판정
- `SAFE`, `BLOCKED`, `UNKNOWN` 상태 계약
- 기존 Distribution projection에 연결할 위치
- 대용량 SPD를 위한 spatial index와 cache 계약
- 실제 SPD 두 개와 synthetic fixture를 이용한 회귀 기준
- 향후 GUI option과 clearance 입력값이 보존되어야 할 metadata

### 2.2 이번 연구에서 구현하지 않는 범위

- production parser, ScenarioSpec, GUI 또는 optimizer 코드 변경
- 실제 SPD artwork 수정 또는 신규 antipad 작성
- signal trace 재배선
- signal via/pin/fanout pad의 완전한 장애물 구현
- 임시 clearance 정책을 production 기본 규칙으로 확정
- installer, version 또는 release asset 변경

초기 코드 수정이 trace-only로 시작된다면 결과 화면과 문서에 signal via, signal pad,
pin/fanout pad 충돌은 아직 검증하지 않는다고 명시해야 한다. 전체 immutable routing
안전을 주장하려면 이 객체들도 같은 obstacle asset에 포함해야 한다.

## 3. 유지해야 하는 현재 Distribution 계약

현재 Distribution의 물리 계약은 다음과 같다.

1. TOP-side decap의 source-classified PWR-via landing XY는 움직이지 않는다.
2. 목적 PWR plane artwork가 해당 XY를 strict interior로 포함해야 한다.
3. plane/void boundary 접촉은 fail-closed다.
4. 기존 via span/path는 목적층 도달 자격으로 사용하지 않는다.
5. landing surface에서 목적 PWR layer까지 새 vertical stack을 retarget/rebuild한다고
   가정한다.
6. shared-pad, isolation gap, rail identity와 수량 최적화 규칙은 계속 적용한다.

따라서 예시 stack이 다음과 같다면:

```text
TOP - PWR1 - PWR2 - SIG1 - PWR3 - PWR4 - BOTTOM
```

| 변경 | 검사할 새 stack | SIG1 검사 |
| --- | --- | --- |
| PWR1 → PWR2 | TOP→PWR2 | 불필요 |
| PWR2 → PWR1 | TOP→PWR1 | 불필요 |
| PWR1 → PWR3 | TOP→PWR3 | 필요 |
| PWR3 → PWR4 | TOP→PWR4 | 필요 |

`PWR3→PWR4`도 현재 rebuild 계약에서는 SIG1을 다시 검사한다. 기존 TOP→PWR3
stack을 그대로 재사용하고 PWR3 아래만 연장하는 정책은 별도 기능이다. 두 정책을 한
eligibility 계산 안에서 혼합해서는 안 된다.

## 4. 용어와 결과 상태

| 용어 | 정의 |
| --- | --- |
| Landing column | decap PWR terminal에서 시작하는 고정 XY의 물리 via column |
| Destination | 후보 PWR net과 실제 목적 PWR layer의 조합 |
| Routing object | physical Trace와 향후 signal via/pad, pin/fanout pad |
| Plane pour | `Plane` 또는 `PatchSignal` positive copper artwork |
| Planned via profile | 목적층까지 새로 형성할 via stack의 layer별 copper 단면 |
| Clearance source | 사용자 입력, 명시적 DRC rule 또는 검증된 proxy 중 실제 판정 근거 |
| BLOCKED | 해석 완료 obstacle과 copper/clearance 충돌이 증명된 상태 |
| UNKNOWN | width, net role, object provenance, span 또는 via profile이 불완전한 상태 |
| SAFE | 통과 layer 전체의 근거가 완전하고 충돌이 없는 상태 |

Protection ON에서는 `BLOCKED`와 `UNKNOWN`을 모두 optimizer eligibility에서
제외한다. 다만 진단과 통계에서는 두 상태를 반드시 구분한다.

상태 우선순위는 다음과 같다.

```text
BLOCKED > UNKNOWN > SAFE
```

한 span에 충돌과 불완전 근거가 동시에 있으면 최종 상태는 `BLOCKED`이지만,
불완전 근거도 diagnostic evidence에 보존한다.

## 5. 연구용 clearance 정책

이번 연습에서는 사용자의 지시에 따라 trace edge와 새 via copper edge 사이의 요구
clearance를 trace 전체 폭의 두 배로 둔다.

```text
trace width              = w
edge-to-edge clearance   = C = 2w
planned via copper radius on layer l = r_v(l)
```

직선 Trace centerline과 via 중심 사이의 금지 반경은 다음과 같다.

```text
R_forbid(l) = w/2 + C + r_v(l)
            = r_v(l) + 2.5w
```

판정식:

```text
distance(via_center, trace_segment) <= R_forbid(l) + epsilon
    => BLOCKED
```

접촉도 `BLOCKED`다. 연구 시작 tolerance는 현재 plane-boundary 정책과 같은
`epsilon = 1e-6 µm`로 둔다.

| Via 반경 | Trace 폭 | 연구용 금지 반경 |
| ---: | ---: | ---: |
| 30 µm | 20 µm | 80 µm |
| 30 µm | 25 µm | 92.5 µm |
| 50 µm | 20 µm | 100 µm |
| 50 µm | 25 µm | 112.5 µm |
| 50 µm | 100 µm | 300 µm |
| 175 µm | 150 µm | 550 µm |

`C=2w`는 연구용 policy다. production code에 상수로 하드코딩하지 않는다. 실제
구현에서는 사용자가 `Trace-to-via clearance (µm)`를 직접 입력하고, 그 값을
`C`로 사용해야 한다.

## 6. Obstacle 분류 계약

### 6.1 Layer 이름으로 분류하지 않는다

`SIGNAL1`, `S1`, `CORE` 또는 `DGND` 같은 layer 이름은 obstacle 여부의 근거가
아니다. 실제 `Trace`와 그 endpoint Node가 존재하는 conductor layer를 검사한다.

실제 연구 파일에서는 다음이 확인됐다.

- `LQ015B0_MLO_00.spd`의 L16/L39 `CORE` layer에도 많은 Trace가 있다.
- `PC_2116...spd`의 L02/L05/L10/L15 DGND 계열 layer에도 signal routing이 있다.
- 명시적 L11/L13만 검사하면 deep target의 실제 block을 크게 과소평가한다.

### 6.2 Net role로 signal을 분류한다

접두어 `XX_`, `ADC_` 같은 이름 규칙만으로 signal을 확정하지 않는다. 최소한 다음
근거의 union으로 role을 결정한다.

1. selected power-plane nets
2. decap power-terminal nets
3. `.NetList`의 명시적 `PowerNets`
4. selected ground nets와 configured ground aliases
5. device pin/rail classification
6. 명시적 사용자 override

권장 enum:

```text
SIGNAL
POWER
GROUND
UNKNOWN
```

role을 확정할 수 없는 physical Trace는 Protection ON에서 `UNKNOWN` 근거가 된다.

### 6.3 Plane pour와 routed Trace를 구분한다

- shielding GND의 positive plane/pour: obstacle에서 제외
- negative GND hole: 보조 evidence 또는 검증된 proxy로만 사용
- routed `Trace::DGND`: plane 면제를 자동 적용하지 않음
- routed PWR Trace: 다른 net의 신규 via와 short될 수 있으므로 자동 면제하지 않음
- destination과 동일 net인 routed Trace: 의도된 중간 접속임을 topology로 증명한
  경우에만 whitelist 가능

`DoglegHole_A`, `SmallHole_A` 같은 negative hole은 trace-to-GND void이지 새로운
PWR via에 적용할 DRC clearance와 동일하다고 보장할 수 없다. global clearance로
역산하거나 하드코딩하지 않는다.

### 6.4 Raw Trace와 physical routed copper를 구분한다

SPD의 raw `Trace`에는 physical routing 외에 plane mesh, via-cluster connection 또는
topology edge가 포함될 수 있다. 따라서 다음 세 분류가 필요하다.

```text
PHYSICAL_ROUTING
NON_COPPER_TOPOLOGY_OR_MESH
UNRESOLVED
```

`RAW_ALL_TRACE`를 그대로 obstacle로 만드는 구현은 금지한다. 반대로 physical
routing인데 width가 없다는 이유로 버리는 것도 금지한다.

### 6.5 보호 scope를 명시한다

결과와 metadata에는 어떤 routing scope를 적용했는지 반드시 남긴다.

| Scope | 계약 |
| --- | --- |
| `SIGNAL_NET_ONLY` | 이번 사용자 요구의 1차 구현 범위. role이 SIGNAL로 증명된 physical Trace만 보호한다. 다른 PWR/GND routed copper와의 short를 완전히 검증하지 않는다는 한계를 표시한다. |
| `ALL_IMMUTABLE_ROUTING` | 향후 물리 안전 확장. 모든 physical routed Trace와 signal via/pad/pin을 보호하고, same-target-net 접속이 topology로 증명된 object만 면제한다. |
| `RAW_ALL_TRACE` | 사용 금지. mesh/topology edge와 physical copper를 혼동한다. |

초기 production 구현은 `SIGNAL_NET_ONLY`로 시작하되, asset schema와 진단 구조는
`ALL_IMMUTABLE_ROUTING`을 추가할 수 있도록 object type과 net role을 분리해
저장한다.

## 7. SPD parsing 및 obstacle asset

### 7.1 Streaming parse

대용량 SPD를 preview마다 다시 읽지 않는다. Import 또는 별도 compile 단계에서 한
번만 다음 정보를 streaming parse한다.

```text
Trace ID
Net
StartingNode
EndingNode
Layer
X1, Y1, X2, Y2
Width
Width source
Net role
Physical object provenance
```

Trace 자체에는 layer와 좌표가 없을 수 있으므로 `StartingNode`와 `EndingNode`를 Node
section에 조인한다. endpoint 누락 또는 서로 다른 layer endpoint는 해당 object를
object-level `UNRESOLVED` diagnostic으로 기록하고, 이 object가 포함된 후보 상태를
`UNKNOWN`으로 매핑한다. `UNRESOLVED`는 네 번째 candidate 상태가 아니다.

### 7.2 Width 해석 우선순위

```text
1. Trace 첫 줄의 inline Width
2. 동일 Trace의 `+ Width = ...` continuation
3. 명시적으로 검증된 exact copper geometry
4. 해당 SPD 버전/문법에서 inheritance semantics가 입증된 source-layer default Width
5. 그 외 UNKNOWN
```

Layer row에 `Width`가 있다는 이유만으로 Trace가 자동 상속한다고 가정하지 않는다.
PowerSI/SPD format 근거 또는 실파일 대조로 inheritance를 검증한 경우에만 4번을
사용한다.

다음 문법을 모두 회귀 테스트한다.

- 일반 Trace
- continuation Width
- Width 미지정
- Thermal Trace
- zero-length/endpoint Trace
- 대각선 Trace

### 7.3 Compact attachment

수십만 개 Python/Pydantic 객체를 ScenarioSpec에 직접 중첩하지 않는다. 다음을
포함한 versioned compact attachment를 사용한다.

- layer별 numeric segment arrays
- object/net/provenance dictionary table
- layer별 completeness summary
- source SPD SHA-256
- stackup fingerprint
- asset schema version
- compressed payload SHA-256

Scenario/design/request fingerprint에는 attachment hash가 반드시 포함되어야 한다.
저장된 scenario bundle은 원본 SPD가 없어도 같은 판정을 재현할 수 있어야 한다.

## 8. Planned via profile

충돌 단면은 drill 중심이나 drill 직경이 아니다. 각 통과 layer에서 실제로 형성될
copper의 union을 사용한다.

우선순위:

1. 해당 layer의 pad shape
2. 겹치는 adjacent microvia pad들의 union
3. pad가 없는 barrel 구간의 plated outer diameter
4. 해석 불가 시 `VIA_PROFILE_UNRESOLVED`

원형 근사만 가능한 경우에도 해당 layer에서 겹치는 모든 pad 중 최대 외곽 반경을
사용한다. 한 stack 전체에 하나의 반경을 적용하지 않는다.

`PC_2116...spd`의 연구 profile:

- 일반 DRxx microvia pad 반경: 50 µm
- `_40` 계열 pad 반경: 30 µm
- L15–L24 core transition `DR1524` pad 반경: 175 µm

따라서 TOP→L16+ 경로의 L15 판정에는 50 µm가 아니라 175 µm를 사용한다.

## 9. Span 계산

현재 rebuild 계약에서 검사 span은 source PWR layer에서 destination까지가 아니라
**장착 surface에서 destination PWR layer까지**다.

```text
TOP mounted    => conductor stack prefix
BOTTOM mounted => conductor stack suffix
```

규칙:

1. canonical stack order를 사용한다.
2. surface와 destination 사이의 모든 conductor layer를 조사한다.
3. destination layer에 routing object가 공존하면 destination도 조사한다.
4. 완전히 재사용되는 surface landing copper만 source obstacle 검사에서 제외할 수 있다.
5. stack order 누락/중복/alias ambiguity는 `UNKNOWN`이다.

`PC_2116...spd`의 연구 span:

| 목적 PWR layer | 통과 signal-role Trace layer |
| --- | --- |
| L03, L04 | L02 |
| L06, L07, L09 | L02, L05 |
| L16, L17, L19, L20, L22 | L02, L05, L10, L11, L13, L15 |

## 10. Collision algorithm

### 10.1 Layer별 index

모든 Trace를 미리 Shapely polygon으로 buffer하지 않는다. 다음 방식으로 계산한다.

1. segment bbox를 layer별 STRtree 또는 tile/grid index에 저장한다.
2. 후보 via 중심 주변을 최대 금지 반경으로 확장하여 nearby segment만 조회한다.
3. 조회된 segment에만 exact point-to-segment distance를 계산한다.
4. bend는 연결된 각 segment와 endpoint round cap을 모두 검사한다.
5. 수치 경계와 tangency는 fail-closed다.

### 10.2 Research policy pseudocode

```text
for each landing column v:
    prefix_state = SAFE

    for each conductor layer l from surface toward board interior:
        if routing asset/net role/via profile on l is incomplete:
            prefix_state = max(prefix_state, UNKNOWN)

        for each nearby physical signal Trace t on l:
            w = resolved_trace_width(t)
            C = 2 * w                  # research policy only
            R = via_radius(v, l) + w/2 + C

            if point_segment_distance(v.xy, t) <= R + epsilon:
                prefix_state = BLOCKED
                record_collision_evidence(v, l, t)
                break

        state[v, l] = prefix_state

    for each destination layer d:
        candidate_state[v, d] = state[v, d]
```

TOP-mounted 후보는 layer별 결과를 prefix로 누적하므로, 한 layer에서 block되면 더
깊은 모든 destination에 같은 obstacle evidence를 재사용할 수 있다. BOTTOM은
suffix 누적을 사용한다.

### 10.3 Cache key

최소 cache key:

```text
source SPD SHA
obstacle asset schema/version/SHA
stackup fingerprint
net-role policy version
clearance policy/value
via-profile hash
mount side
```

## 11. Distribution 통합 위치

기존 흐름은 유지한다.

```text
exact destination PWR-plane containment
    -> intermediate routing clearance filter
    -> component/shared-pad eligibility
    -> existing MILP allowed labels
    -> preview/apply validation
```

권장 수정 지점:

- `spd.py`: routing obstacle source parse/compile
- `spd_adapter.py`: compact attachment와 completeness metadata 보존
- `scenario.py`: avoidance policy, asset reference와 proof model
- `distribution.py::_distribution_batch_via_eligibility`: plane 통과 후보의 routing 검사
- `distribution.py::build_distribution_power_projection`: blocked/unknown evidence 보존
- `main_window.py`: option, clearance 입력, worker 전달과 preview invalidation

MILP에 새 collision binary constraint를 추가하지 않는다. `allowed_labels`를 만들기
전에 `(via_id, destination_layer)` 후보를 제거한다.

### 11.1 여러 PWR landing column

판정은 column별로 수행한다. 같은 terminal에 물리적으로 남는 모든 column을 새
destination으로 재구축한다면 모든 column이 안전해야 한다.

현재의 “한 root라도 eligibility가 있으면 component 허용” 의미를 유지하려면 실제로
사용할 root를 선택하고 나머지 column을 제거/격리한다는 물리 동작까지 명시적으로
지원해야 한다. 그렇지 않으면 Protection ON의 안전 계약은 all-retained-columns를
요구한다.

## 12. 진단과 evidence

차단/불명 후보마다 최소 다음을 보존한다.

```text
RefDes / shared cluster
landing via ID and XY
source rail/net
destination rail/net/layer
blocking layer
obstacle ID/type/net/net role
Trace width and width source
planned via radius/profile source
centerline distance
actual copper edge gap
required clearance
clearance source
asset/policy/profile versions
```

권장 diagnostic code:

- `IMMUTABLE_SIGNAL_CLEARANCE_BLOCKED`
- `TRACE_WIDTH_UNRESOLVED`
- `TRACE_NET_ROLE_UNRESOLVED`
- `TRACE_PHYSICAL_PROVENANCE_UNRESOLVED`
- `ROUTING_ASSET_REQUIRED`
- `ROUTING_ASSET_STALE`
- `VIA_PROFILE_UNRESOLVED`
- `STACKUP_SPAN_UNRESOLVED`

결과 요약에는 filter 전/후 후보 수, destination/layer별 block 수, UNKNOWN 수,
trace 제약으로 발생한 capacity shortfall을 표시한다.

## 13. 향후 GUI 계약

기본값은 기존 PI-first 동작과 호환되는 OFF로 둔다.

```text
[ ] Protect immutable signal routing clearances
    Trace-to-via clearance: [      ] µm
```

- OFF: 현재 Distribution 결과와 bit-for-bit 동일
- ON: routing copper/clearance 충돌과 UNKNOWN 후보를 hard-block
- clearance 입력: finite, `>= 0`, 단위 µm
- 값 미입력/오류: 계산 시작 전에 명시적으로 차단
- 연구용 `C=2w`: production UI 기본값으로 저장하지 않음

다음 데이터에 option과 clearance 값을 포함한다.

- Distribution request fingerprint
- worker request와 stale-result check
- DistributionPlan
- preview/apply validation
- XLSX metadata와 format version
- result export/replay metadata
- user-visible status/log

option 또는 clearance 변경은 기존 preview를 즉시 무효화해야 한다.

## 14. 실제 SPD 연구 근거

### 14.1 Source provenance

| 파일 | 크기 | SHA-256 |
| --- | ---: | --- |
| `LQ015B0_MLO_00.spd` | 930,082,060 B | `14987a4af01bd4d2933502b1f1eb885027c1d138de41389482a9850fe9882ab8` |
| `PC_2116_S5I5600X08_1P_260606_final_1.spd` | 241,269,544 B | `5eb8e34fc9bf3813e3d7b2a48a9b8b0c0a4b429ee647b3f8558ae49d23e261e6` |

원본 파일은 읽기 전용으로 사용했다.

### 14.2 LQ015B0 parser/coverage oracle

- raw Trace: 782,439개
- 일반 Trace: 782,385개
- Thermal Trace: 54개
- L16(CORE): 12,329개
- L39(CORE): 41,498개
- 명시적 S1–S6: 합계 139,097개

이 파일은 Thermal/continuation/검증 대상 layer-default Width와 layer-name-independent extraction
회귀에 적합하다. 명시적 S1–S6은 모든 PWR destination보다 아래에 있으므로
`TOP→deep PWR`의 대표 collision golden file로만 사용해서는 안 된다. L16의 실제
Trace가 상부/하부 PWR 그룹 사이를 통과하는 별도 회귀 근거다.

### 14.3 PC_2116 parser oracle

- stack rows: 75 = conductor 38 + dielectric 37
- TOP/mounted decap: 4,668개
- via-backed direct/anchor decap: 2,448개
- 고유 PWR landing column: 9,792개
- Node: 618,414개
- Trace: 903,114개
- Via: 434,937개
- inline Width: 84,496개
- continuation Width: 1,680개
- Width 미지정: 816,938개
- endpoint 누락: 0개
- cross-layer Trace: 0개

Width 없는 raw Trace의 대부분은 mesh/topology edge로 보이지만 provenance 없이
non-copper라고 단정하지 않는다.

Width completeness는 물리 파일 한 줄이 아니라 `Trace`와 뒤따르는 `+` continuation을
합친 **logical record**를 만든 뒤 판정한다. PC_2116의 L11/L13 signal-like Trace 중
993개는 continuation Width에 의존하므로 line-by-line parser는 이를 잘못
`UNKNOWN`으로 분류한다.

### 14.4 PC_2116 signal-role Trace oracle

다음 수치는 POWER role을 selected plane, decap terminal과 명시적 `PowerNets`의
union으로 분류하고 GROUND role을 제외한 연구 결과다.

이 union은 접두어 heuristic보다 강하지만, PC_2116의 scenario rail 생성이
`SPD_NO_RAILS`로 중단되므로 production의 authoritative net-class manifest는 아니다.
향후 rail/device/user override 근거가 완성되면 동일 oracle을 다시 산출한다.

초기 `XX_*` prefix 기반 연습에서 `XX_VDDWL/0`과 이름에 VDD가 포함된 net을
일괄 제외했을 때의 deep-span 재현치는 1,619개였다. 아래 1,627개는 selected-plane,
decap-terminal과 명시적 `PowerNets`를 합친 role-union으로 다시 분류한 최신
acceptance oracle이며, +8 차이는 net-role scope 차이다. 향후 회귀 기준은 1,627을
사용하고 1,619는 이전 heuristic 재현치로만 보존한다.

이 research harness는 width가 해석되고 signal role로 분류된 `Trace`를 provisional
`PHYSICAL_ROUTING` proxy로 취급했다. 개별 object의 physical/mesh provenance를
production 수준으로 증명한 결과는 아니다. 따라서 아래 `BLOCKED/UNKNOWN/SAFE`는
이 research proxy 범위의 상태이며 PCB/PowerSI sign-off를 의미하지 않는다.

| Layer | PWR Trace | GND Trace | Signal width 확인 | Signal width 미해결 | Signal 폭 | 개별 known block / 9,792 |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| L02 | 17,262 | 19,014 | 90 | 0 | 25 µm | 0 |
| L05 | 33,465 | 50,829 | 311 | 16 | 60×277, 25×34 µm | 0 |
| L10 | 15,214 | 42,935 | 309 | 16 | 60 µm | 0 |
| L11 | 5,178 | 11,717 | 1,249 | 0 | 25×1,120, 20×129 µm | 708 |
| L13 | 5,172 | 11,699 | 946 | 0 | 25×797, 20×149 µm | 382 |
| L15 | 3,221 | 7,875 | 293 | 0 | 150 µm | 1,060 |

L05/L10의 signal Trace 각 16개는 Width와 layer default Width가 모두 없다. 이
layer를 통과하는 후보는 known collision이 없어도 완전한 `SAFE`로 인증할 수 없다.

### 14.5 PC_2116 collision oracle

연구 조건:

- `C = 2w`
- L02/L05/L10/L11/L13 via radius = 50 µm
- L15 `DR1524` via pad radius = 175 µm
- tangency block
- known-width signal Trace만 known collision 판정
- 동일한 9,792 PWR landing 분모

| Target depth | BLOCKED | UNKNOWN | SAFE | Certification note |
| --- | ---: | ---: | ---: | --- |
| L03, L04 | 0 | 0 | 9,792 | 이번 signal-trace evidence 범위에서 complete |
| L06, L07, L09 | 0 | 9,792 | 0 | L05 unresolved Width 16개 |
| L16, L17, L19, L20, L22 | 1,627 | 8,165 | 0 | L05/L10 unresolved Width 32개 |

L11/L13만 검사하면 known block은 779개다. L15의 175 µm pad envelope까지
포함하면 1,627개가 된다. 이 차이는 layer 이름만으로 obstacle을 고르는 구현이
부정확함을 보여준다.

Strict fail-closed 연구 상태는 deep span에서 `BLOCKED=1,627`, `UNKNOWN=8,165`,
`SAFE=0`이다. L05/L10의 unresolved Width에 승인된 upper bound가 없기 때문이다.

참고 비교로 L11/L13 두 nominal signal layer의 모든 net Trace 35,961개를 같은
`C=2w`, via radius 50 µm와 same-net 면제 없이 검사하면 `epsilon=1e-6 µm`에서
4,082개 landing이 known block된다. `epsilon=0`이면 4,080개다. 이 값은 L02/L05/
L10/L15를 포함한 full-stack all-routing 결과가 아니며, signal-role deep-span 1,627과
직접적인 개선율로 비교해서는 안 된다.

### 14.6 Exact PWR-plane 후보에 대한 영향

아래 수치는 current ordered positive/negative PWR geometry와 `1e-6 µm` boundary
fail-closed를 먼저 적용한 research harness 결과다. PC_2116은 현재 GUI import가
`SPD_NO_RAILS`로 중단되므로 성공한 end-to-end Distribution 결과가 아니다.

`Known-block-only survivor`는 `SAFE` 또는 Protection ON eligibility와 동의어가
아니다. 다음 표는 known-width collision의 영향만 비교한 연구 표다.

| Target | Exact-plane eligible columns | Known signal block | Known-block-only survivors | Block % | Known-block-only any-root components 전→후 |
| --- | ---: | ---: | ---: | ---: | ---: |
| L03 VDDI | 1,548 | 0 | 1,548 | 0.00% | 430→430 |
| L04 DATA | 2,650 | 0 | 2,650 | 0.00% | 677→677 |
| L06 CLK | 3,525 | 0 | 3,525 | 0.00% | 1,168→1,168 |
| L06 DIG | 3,735 | 0 | 3,735 | 0.00% | 1,224→1,224 |
| L07 CMN | 8,015 | 0 | 8,015 | 0.00% | 2,414→2,414 |
| L09 VDDH | 4,260 | 0 | 4,260 | 0.00% | 1,124→1,124 |
| L09 VDDO | 4,550 | 0 | 4,550 | 0.00% | 1,201→1,201 |
| L16 VDDI | 7,660 | 1,285 | 6,375 | 16.78% | 2,120→1,801 |
| L17 DATA | 7,456 | 1,274 | 6,182 | 17.09% | 2,081→1,762 |
| L19 CLK | 3,752 | 399 | 3,353 | 10.63% | 1,041→940 |
| L19 DIG | 3,909 | 866 | 3,043 | 22.15% | 1,074→857 |
| L20 CMN | 7,483 | 1,281 | 6,202 | 17.12% | 2,079→1,762 |
| L22 VDDH | 3,770 | 401 | 3,369 | 10.64% | 1,038→941 |
| L22 VDDO | 4,013 | 892 | 3,121 | 22.23% | 1,105→882 |

Deep target 합계는 38,043개의 exact-plane eligible column-target pair 중 6,398개가
known block이고 31,645개가 known-block-only survivor다. Deep span의 unresolved
evidence를 적용하면 이 survivor를 Protection ON의 eligible component로 부를 수 없다.

### 14.7 실제 collision sample

| Layer | RefDes / Via | XY µm | Trace | Width | Center distance | Copper edge gap | Required clearance | 결과 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| L11 | C1078/0 / Via86418 | (4,950, -12,300) | Trace902651 | 25 µm | 70.004 µm | 7.504 µm | 50 µm | BLOCKED |
| L13 | C1072/0 / Via86466 | (4,850, -10,200) | Trace902682 | 25 µm | 70.004 µm | 7.504 µm | 50 µm | BLOCKED |
| L15 | C107/0 / Via103453 | (-3,650, 9,500) | Trace898858 | 150 µm | 429.536 µm | 179.536 µm | 300 µm | BLOCKED |

## 15. 현재 PC_2116 end-to-end import 제한

현재 `analyze_spd(scope="decap_scenario")`는 약 14초에 raw 분석을 완료하지만
`import_spd_scenario()`는 다음 사유로 중단된다.

- selected device PWR/GND pin이 없어 rail을 만들 수 없음
- nested `.SUBCKT` CAP wrapper를 현재 model parser가 지원하지 않음
- 결과 diagnostic: `SPD_NO_RAILS`

따라서 이 파일은 현재 상태에서 GUI Import→Distribution golden file은 아니다. 다음
목적으로 사용한다.

- offline routing obstacle extractor 성능/정확성
- exact-plane 후보와 trace filter 결합 연구
- shallow/deep span 회귀
- 실제 collision evidence 수동 대조

향후 end-to-end fixture로 승격하려면 rail/pin mapping, CAP model mapping과
`Signal$Lxx`/`Plane$Lxx` alias 정규화 근거를 먼저 확보한다.

## 16. 필수 회귀 테스트

### 16.1 Parser/asset

- inline/continuation/검증된 layer-default Width inheritance
- Thermal Trace
- endpoint 누락과 cross-layer endpoint
- layer 이름이 CORE/DGND여도 physical signal Trace 보존
- role classification과 사용자 override
- raw mesh/topology edge가 physical obstacle로 잘못 승격되지 않음
- source/asset hash mismatch fail-closed
- 동일 입력의 deterministic asset hash

### 16.2 Geometry

- 직선, 대각선, bend, endpoint round cap
- direct copper overlap
- copper는 피하지만 clearance만 침범
- exact tangency block
- trace outside span은 무시
- shielding GND positive pour만 겹치면 통과
- routed DGND/PWR Trace는 topology proof 없이는 면제하지 않음
- layer별 pad/barrel radius 적용
- L15 `DR1524` 175 µm envelope 적용
- TOP/BOTTOM span 방향

### 16.3 Distribution

- Protection OFF baseline 완전 동일
- Protection ON에서 blocked/unknown label이 optimizer에 들어가지 않음
- 안전한 대체 landing이 있으면 다음 후보 선택
- 안전 capacity 부족 시 PARTIAL과 구체적 trace diagnostic
- shared-pad anchor/dummy/root 사례
- 여러 retained PWR column의 aggregation 계약
- option/clearance 변경 시 preview와 in-flight worker result stale 처리
- workbook/export/replay metadata round-trip
- tampered/stale routing asset 거부

### 16.4 필수 synthetic fixture

다음 최소 stack을 가진 synthetic SPD를 만든다.

```text
TOP - PWR1 - PWR2 - SIG1 - PWR3 - PWR4 - BOTTOM
```

fixture에는 최소 다음 landing을 둔다.

1. SIG1 Trace 중심과 직접 중첩
2. trace copper는 피하지만 `C` 안쪽
3. 정확히 clearance boundary 접촉
4. clearance 바깥
5. Width 누락
6. shielding GND plane만 존재
7. routed DGND Trace가 존재

PWR1/PWR2 target과 PWR3/PWR4 target의 pass/block matrix를 고정한다.

## 17. 향후 구현 순서

### Phase A — Offline extractor/harness

1. Node/Trace/Width streaming parser 작성
2. physical/mesh/unresolved provenance 분류
3. net role 분류
4. layer별 compact index 작성
5. 두 real SPD의 parser oracle 고정
6. synthetic fixture 거리 회귀

이 단계에서는 production Scenario/GUI/optimizer를 수정하지 않는다.

### Phase B — Scenario asset

1. versioned routing obstacle attachment
2. completeness와 source hash
3. planned via-profile manifest
4. `SAFE/BLOCKED/UNKNOWN` proof model
5. bundle save/load/tamper tests

### Phase C — Projection 및 UI

1. exact PWR-plane 통과 후보에 routing filter 적용
2. blocked/unknown reason 보존
3. checkbox와 clearance µm 입력
4. request fingerprint/worker/stale handling
5. XLSX/export/replay metadata

### Phase D — Sign-off

1. Protection OFF full regression
2. synthetic acceptance matrix
3. PC_2116 exact-plane/trace oracle
4. LQ015B0 parser/CORE/Thermal oracle
5. representative collision을 PowerSI 원본에서 수동 대조
6. performance/memory benchmark
7. packaged EXE와 installed UI 확인

## 18. 금지 사항

향후 구현에서 다음 shortcuts는 허용하지 않는다.

- layer 이름에 `SIG`/`S1`가 있는 층만 검사
- `XX_` 같은 net prefix만으로 signal 확정
- Width 없는 physical Trace를 삭제
- raw Trace 전체를 physical signal로 간주
- trace centerline만 검사하고 via pad 반경을 무시
- 모든 layer에 동일한 via 반경 사용
- DGND라는 이유만으로 routed Trace 면제
- negative GND hole에서 전역 clearance를 하드코딩
- UNKNOWN 후보를 SAFE로 승격
- collision을 optimizer soft penalty로 처리
- protection option을 request fingerprint에서 누락
- preview마다 200–900 MB SPD 재스캔

## 19. 완료 판정

향후 기능 구현은 다음 조건을 모두 만족해야 완료로 본다.

1. Protection OFF 결과가 기존 baseline과 동일하다.
2. Protection ON에서 `BLOCKED`와 `UNKNOWN` 후보가 선택되지 않는다.
3. 모든 선택 후보는 surface→destination 전체 span에 대해 `SAFE` evidence가 있다.
4. UI, plan, workbook과 replay에 clearance policy가 재현 가능하게 남는다.
5. 두 real SPD와 synthetic fixture의 고정 oracle이 통과한다.
6. source SPD, plane artwork와 unrelated scenario data가 변경되지 않는다.
7. 결과에 어떤 routing object와 clearance가 후보를 차단했는지 표시된다.
8. 실제 collision sample을 PowerSI에서 대조한 기록이 남는다.

이 문서의 수치 결과는 연구용 `C=2w` 정책과 명시된 via profile에 종속된다. 향후
사용자 입력 clearance를 도입하면 동일 fixture를 새 policy version으로 다시 계산하고,
기존 결과를 덮어쓰지 말고 별도 baseline으로 보존한다.
