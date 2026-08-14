# SPD Decap PI Evaluator v0.22.0 — Source Parameter Manifest

최종 갱신: 2026-08-14 (Asia/Seoul)

이 문서는 네 canonical SPD에서 PI 물리 모델이 사용할 수 있는 source-derived parameter와 명시적 결손을 고정한다. exact file identity는 [`REFERENCE_DATASET.md`](REFERENCE_DATASET.md)의 path/bytes/SHA-256을 따른다. PowerSI curve에서 역추정한 parameter는 포함하지 않는다.

## 조사 방법과 자원 경계

각 파일을 binary line streaming으로 순차 조사했다. 전체 file read, mmap, full hash 재계산, solver compile은 수행하지 않았다. 보존한 상태는 unique material/width/padstack counter와 소수의 evidence sample뿐이다.

| Pair | bytes | lines | semantic pass | trace framing pass |
|---|---:|---:|---:|---:|
| P1 | 139,414,807 | 1,129,348 | 약 3.1 s | 약 0.4 s |
| P2 | 595,452,716 | 5,913,416 | 약 18.4 s | 약 2.0 s |
| P3 | 1,227,509,179 | 10,789,358 | 약 39.1 s | 약 6.1 s |
| P4 | 1,207,304,655 | 10,643,556 | 약 38.7 s | 약 5.8 s |

trace framing 네 파일의 measured total은 약 14.6 s다. trace는 primary row와 바로 이어지는 `+ Width` continuation을 한 record로 묶었다. root 재검증에서도 P1/P2의 모든 width가 복구되고 P3/P4의 아래 결손 수가 동일하게 나왔다.

parameter 상태는 다음 enum으로 기록한다.

- `explicit`: raw source에 값과 owner row가 있음
- `absent`: raw source에 값이 없음; 추정값으로 대체 금지
- `parser_not_preserved`: raw에는 있으나 현행 parser/model이 보존하지 않음
- `derived_node_link`: raw Via row 자체가 아니라 referenced Node에서 layer/coordinate를 유도함
- `derived_material_table`: layer의 explicit material 이름을 같은 source의 usable material table에 연결해 값을 유도함

## Trace width

| Pair | Trace | width explicit | width absent | absent 비율 | explicit width 분포 (µm: count) |
|---|---:|---:|---:|---:|---|
| P1 | 12,544 | 12,544 | 0 | 0% | 500: 5,120; 600: 1,024; 914.4: 6,400 |
| P2 | 15,052 | 15,052 | 0 | 0% | 120: 786; 200: 8,727; 300: 1,876; 500: 93; 600: 3,562; 914.4: 8 |
| P3 | 1,451,285 | 1,212,150 | 239,135 | 16.48% | 23: 8; 25: 330,016; 30: 136; 45: 34,429; 50: 115,789; 60: 207,723; 80: 140; 100: 457,046; 125: 14; 130: 66,849 |
| P4 | 1,451,209 | 1,212,139 | 239,070 | 16.47% | 25: 330,027; 30: 136; 45: 34,429; 50: 115,786; 60: 207,718; 80: 140; 100: 457,043; 130: 66,846 |

P3/P4 결손은 continuation parser 누락이 아니라 `Trace...` 다음에 새 Trace가 바로 시작되는 raw `absent` record다. 예를 들어 P3 `Trace1000000::DGND`에는 130 µm width가 있고, 바로 다음 `Trace1000001::DGND`에는 width가 없다.

알고리즘 정책:

- width가 explicit인 trace만 source-faithful T1 R/L을 만들 수 있다.
- P3/P4의 약 16.5% 결손을 인접 trace, net median, PowerSI curve fit으로 채우지 않는다.
- width가 필요한 topology path에 결손 record가 참여하면 해당 path/coupon을 `blocked_missing_trace_width`로 보고하거나, width-independent topology-only 경로와 분리한다.

### Trace ref와 semantic screening

두 번째 bounded pass에서 endpoint layer, length와 raw `UpperRef`/`LowerRef`를 함께 분류했다.

| Pair | same-layer endpoints | concrete ref ≥1 | no concrete ref | metadata profiles |
|---|---:|---:|---:|---:|
| P1 | 12,544 | 6,816 | 5,728 | 8 |
| P2 | 15,052 | 2,976 | 12,076 | 57 |
| P3 | 1,451,285 | 0 | 1,451,285 | 97 |
| P4 | 1,451,209 | 0 | 1,451,209 | 88 |

concrete ref는 `N/A`가 아닌 explicit `UpperRef` 또는 `LowerRef`다. P1/P2의 concrete ref도 return polygon/connectivity와 current operator를 증명하지 않으므로 상태는 `trace_record_explicit_connectivity_unproved`다. P3/P4의 인접 DGND는 stackup에서만 찾을 수 있어 `derived_stackup_only`이며 source-faithful explicit return으로 부르지 않는다.

P3/P4 raw Trace grammar에는 routed trace와 plane/mesh topology를 구분하는 semantic flag가 없다. 약 39%의 net이 conductor-layer token과 일치하지만 이것만으로 모든 Trace를 route 또는 plane mesh라고 분류하지 않는다. strict T1 screening과 candidate line evidence는 [`T1_TRACE_ORACLE_RESULTS.md`](T1_TRACE_ORACLE_RESULTS.md)를 따른다.

## Stackup와 conductor

### Thickness와 material

| Pair | thickness rows / conductor rows | 전체 thickness 범위 | conductor thickness | layer material count |
|---|---:|---|---|---|
| P1 | 47 / 24 | 30.48–203.2 µm | 30.48, 35 µm | FR-4 23; COPPER 22; COPPER_1 2 |
| P2 | 155 / 78 | 17.5–208 µm | 17.5, 35 µm | COPPER 78; MEGTRON-6 family 77 |
| P3 | 95 / 48 | 10–300 µm | 10, 20 µm | COPPER 48; ABF-GL102 40; EL190T 7 |
| P4 | 95 / 48 | 10–300 µm | 10, 20 µm | COPPER 48; ABF-GL102 40; EL190T 7 |

모든 thickness token은 `u` 단위다. P1은 `.TrapezoidalTraceAngle`이 TOP/BOTTOM 두 row에만 90°로 있고 내부 22 conductor layer에는 없다. P2/P3/P4는 모든 conductor row에 90°가 명시돼 있다.

### Metal conductivity

- P1/P2 `COPPER`: 20–140°C의 8 points, `40,599,460–59,600,000 S/m`
- P1 `COPPER_1`: 20°C 한 point, `59,590,000 S/m`
- P3/P4 `COPPER`: 20°C 한 point, `59,590,000 S/m`

한 온도 point를 broadband skin/roughness law로 해석하지 않는다. temperature interpolation은 source point 범위 안에서만 별도 정책으로 정의한다.

layer row에는 numeric conductivity token이 없지만 `Material=COPPER/COPPER_1`가 같은 file의 usable `.MetalModel`에 명시적으로 연결된다. 이 경우 `layer_conductivity_token=absent`, `material_link=explicit`, `resolved_sigma=derived_material_table`로 기록한다. 이를 fitted/default conductivity 또는 physical conductivity absent로 부르지 않는다.

## Dielectric model

raw header는 `*Frequency(MHz) Permittivity LossTangent`다.

| Pair | material | source rows | frequency 범위 | Dk / loss tangent |
|---|---|---:|---|---|
| P1 | AIR | 1 | 1 GHz | 1 / 0 |
| P1 | FR-4 | 1 | 1 GHz | 4.5 / 0.035 |
| P2 | AIR | 1 | 1 GHz | 1 / 0 |
| P2 | MEGTRON-6, `_1`, `_2`, `_3` | 각 1 | 1 GHz | 3.31/3.46/3.29/3.49; loss 0.002 |
| P3/P4 | ABF-GL102 | 72 | 1 MHz–80 GHz | Dk 3.4–3.56; loss 0.0041–0.0121 |
| P3/P4 | EL190T | 3 | 1 MHz–10 GHz | Dk 4.7→4.1; loss 0.010–0.012 |
| P3/P4 | AIR | 1 | 1 GHz | 1 / 0 |
| P3/P4 | BU-DIELECTRIC / BU_DIELECTRIC | 각 1 | 1 GHz | 3.3 / 0.004 |
| P3/P4 | ML_DIELECTRIC | 1 | 1 GHz | 4.3 / 0.011 |

ABF-GL102의 72 rows에는 temperature variant가 포함된다. material name, frequency, temperature와 raw row를 함께 owner key로 보존해야 한다. P1/P2의 single-frequency row는 broadband causal dispersion을 증명하지 않으므로 constant-property sensitivity 이상으로 승격하지 않는다.

## Via와 padstack

| Pair | all Via-start / net-qualified Via | used/defined padstack | drill explicit | material explicit | PadDef |
|---|---:|---:|---:|---:|---:|
| P1 | 109,235 / 109,203 | 5 / 10 | 10 / 10 | 9 / 10 | 129 |
| P2 | 1,193,902 / 1,193,766 | 8 / 40 | 21 / 40 | 39 / 40 | 1,599 |
| P3 | 1,956,937 / 1,956,937 | 59 / 98 | 97 / 98 | 97 / 98 | 187 |
| P4 | 1,956,909 / 1,956,909 | 59 / 98 | 96 / 98 | 97 / 98 | 187 |

모든 Via-start row에는 `UpperNode`, `LowerNode`, `PadStack`가 있다. P1/P2에는 각각 32/136개의 `::<net>` 없는 Via가 있어 현행 `_VIA_RE`와 기존 net-qualified inventory에서 제외된다. 예는 P1 `Via206409`, P2 `Via1193891`이다. source-faithful owner manifest는 이 row를 버리지 않고 `net=absent`로 보존한다. net-qualified Via의 referenced padstack name은 모두 정의에 resolve됐다. Via row에는 explicit start/end layer, barrel plating thickness, fill state 또는 material이 없다. layer endpoint는 Node link에서만 유도할 수 있다.

drill 결손:

- P2: 19 definitions, 주로 `SM_RE...`
- P3: `1998ARROW`
- P4: `1998ARROW`, `DUT`
- `~DefaultPadStack`은 material이 없는 공통 예외다.

### Regular pad와 antipad geometry

| Pair | regular pad shape count | anti shape count | raw via records with one or more `NoAntiPadLayers` |
|---|---|---:|---:|
| P1 | Circle 113; Box 1; Square 2 | Circle 125 | 7,551 |
| P2 | Circle 1,421; RoundedRect_X 158; Box 15; Square 3; Polygon 2 | Circle 1,421; RoundedRect_X 158; Box 4; Polygon 1 | 724,658 |
| P3 | Circle 181; Polygon 1; Square 2; Box 3 | 0 | 0 |
| P4 | Circle 181; Polygon 1; Square 2; Box 3 | 0 | 0 |

P1/P2의 `NoAntiPadLayers`는 same-line 또는 Via continuation에 존재할 수 있다. 위 수치는 all Via-start parent 기준이며 이 파일들에서는 각 occurrence가 서로 다른 parent에 속해 P1/P2 각각 7,551/724,658이다. net-qualified subset만 세면 7,549/724,522이며, 차이 2/136은 `net=absent` Via다. P3/P4는 Via attribute와 PadDef anti shape가 모두 source에서 `absent`다. regular pad를 nominal antipad로 재사용하지 않는다.

현행 `_VIA_RE`는 Via ID/net, upper/lower node, padstack과 optional rotation만 보존하고 `NoAntiPadLayers`를 capture하지 않는다. 따라서 P1/P2의 이 정보는 `parser_not_preserved`다. 구현 승인 뒤 antipad adapter를 만들기 전에는 raw attribute를 소실한 상태로 A1을 조립하지 않는다.

## Plating, fill과 roughness

네 SPD 모두 `* ConstraintSizePlating description lines` marker는 있으나 다음 section 전까지 non-comment data row가 없다. `Plating`, `Plated`, `Fill`, `Filled`, `Barrel`, `CopperThickness` parameter row도 없다. 따라서 via plating/fill은 `absent`다.

네 SPD 모두 `* SurfaceRoughness description lines` marker가 한 번 있으나 다음 package marker 전까지 roughness data row는 0이다. 현재 파일에서 roughness는 parser 손실이 아니라 source `absent`다. 현행 parser에도 roughness field가 없으므로 미래의 populated source에는 별도 preservation이 필요하다.

정책:

- via를 plated hollow barrel 또는 filled solid via라고 임의 지정하지 않는다.
- V1/V2는 source가 명확한 synthetic coupon에서 constitutive sensitivity를 연구하고, board source에는 `unknown_plating_fill`을 남긴다.
- roughness를 PowerSI curve에 맞춰 역추정하지 않는다.

## Source provenance와 owner ID

future manifest의 각 parameter row는 다음 key를 가져야 한다.

```text
(pair_id, source_sha256, source_path, raw_section, raw_id, net, layer_or_site,
 line_number, byte_offset, raw_token, status)
```

권장 raw owner ID:

- trace: `Trace<id>::<net>`
- via: `Via<id>::<net>` + upper/lower Node owner
- pad: `PadStackDef name` + `PadDef layer`
- conductor: layer `Thickness` row + metal model name/temperature row
- dielectric: material model name + frequency/temperature row

derived quantity는 원본 owner 집합, 변환식, 단위와 hash를 함께 가진다. 같은 source row가 `retained-core`, `removed-core`, `inserted-exact` 둘 이상에 중복 소유되지 않도록 owner ledger를 검사한다.

## 알고리즘 선택에 미치는 영향

1. T1 trace R/L은 P1/P2에서 완전한 width corpus를 가질 수 있지만 P3/P4에서는 결손 path를 명시적으로 차단해야 한다.
2. D1 dielectric dispersion은 P3/P4 ABF-GL102에서 source sweep을 사용할 수 있다. P1/P2 single point에는 동일한 broadband 모델을 강제하지 않는다.
3. K1 conductor skin은 conductivity와 geometry가 있는 범위에서 가능하지만 roughness는 네 pair 모두 unknown이다.
4. A1 antipad는 P1/P2 raw geometry와 per-via suppression을 parser가 보존한 뒤에만 source-faithful 조립이 가능하다. P3/P4는 추가 source 없이는 정량 A1 board correction을 만들 수 없다.
5. V1/V2의 plating/fill ambiguity는 민감도 범위로 보고하며 reference curve fitting으로 숨기지 않는다.
6. 이 manifest는 parameter 가용성 계약이지 accuracy certificate가 아니다. 각 사용 block은 [`R2_ORACLE_RESULTS.md`](R2_ORACLE_RESULTS.md)의 별도 canonical gate를 통과해야 한다.
