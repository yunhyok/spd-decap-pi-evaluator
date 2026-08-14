# SPD Decap PI Evaluator v0.22.0 — T1 Return Crop Manifest

최종 갱신: 2026-08-14 (Asia/Seoul)

이 문서는 P1/P2 raw Trace의 explicit `LowerRef`가 실제 source-faithful return operator를 구성하기에 충분한지 bounded streaming으로 조사한 결과다. 제품 parser/solver와 raw SPD는 수정하지 않았고 field solve나 whole-file hash를 실행하지 않았다. 결론은 **referenced return geometry와 same-net GND graph는 존재하지만 candidate signal trace의 return-current owner는 source record만으로 증명되지 않는다**는 것이다.

## 상태 계약

다음 상태를 서로 대체하지 않는다.

1. `trace_record_explicit_connectivity_unproved`: Trace record가 `LowerRef`/`UpperRef` 이름을 갖는다.
2. `return_shape_geometry_present`: named return layer의 positive artwork와 local void geometry를 source에서 찾았다.
3. `return_net_graph_present`: 같은 GND net의 Trace/Via가 named plane node까지 연결되는 source graph가 있다.
4. `signal_to_return_operator_unproved`: signal Trace/pad owner와 return shape/Trace/Via owner를 묶는 raw SPD record, signed current basis 또는 electromagnetic coupling owner가 없다.
5. `source_faithful_return_blocked`: 1–3이 참이어도 4가 남으면 board trace replacement에 사용할 수 없다.

geometry containment, 가까운 GND via 또는 net-name equality는 return-current distribution과 magnetic/electric field ownership을 증명하지 않는다.

## Manifest schema

각 crop은 다음 owner를 보존한다.

```text
crop_id
pair_id
crop_bbox_mm
signal_trace_owner_ids
signal_endpoint_node_ids
signal_padstack_owner_ids
return_shape_owner_id
return_void_owner_ids
nearby_return_trace_owner_ids
nearby_return_via_owner_ids
stackup_dielectric_owner
geometric_status
electrical_status
raw_line_and_byte_evidence
```

crop은 원본 좌표를 유지하고 trace/pad/void를 잘라내지 않는 source-inspection 범위다. 이 bbox 자체를 향후 DtN `Γ`로 자동 승격하지 않는다. field crop은 별도의 R/2R/4R convergence로 정한다.

## P1 — TOP traces to `Plane$IN43_DGND`

return layer는 raw line 28, byte 710의 `.Shape Plane$IN43_DGNDpkgshape`다. line 29의 positive `Circle01225::DGND+`는 center `(0,0) mm`, radius `227.9996 mm`이고 세 route projection을 모두 포함한다. stackup row는 IN43 copper thickness `30.48 µm`이며 TOP과의 medium은 `203.2 µm FR-4`다.

### P1-T4004

| field | source evidence |
|---|---|
| crop bbox | `[157.5,160.5] × [87.0,90.0] mm` |
| signal trace | `Trace4004::ADC_AVDD08_LO/9`, line 725146, byte 87,962,974; width `914.4 µm`; TOP |
| endpoints | `Node16612` line 624200, byte 78,798,383, `(158.496,88.265) mm` → `Node2979!!A1` line 696641, byte 85,030,992, `(159.385,88.265) mm` |
| signal pad | `SC_1_27`; PadStackDef line 860069; TOP regular circle raw radius `0.635 mm`, interpreted diameter `1.27 mm` |
| return void owners | `Polygon0149980`, line 50397, byte 6,435,553; `Polygon0144828`, line 50484, byte 6,447,321; `Polygon0153220`, line 50487, byte 6,447,723 |
| nearby return graph | `Trace4637::DGND`, line 726828, byte 88,104,153; `Via292773::DGND`, line 808346, byte 96,364,318 to plane `Node107120`; follow-on `Via11601`, line 751225, byte 89,912,582 |
| geometry verdict | outer plane contains route; listed negative-void bboxes do not intersect its y=`88.265 mm` projection; nearest listed void has about `0.745 mm` y gap |
| electrical verdict | `return_net_graph_present`; `signal_to_return_operator_unproved`; `source_faithful_return_blocked` |

### P1-T4005

| field | source evidence |
|---|---|
| crop bbox | `[96,99] × [41,46] mm` |
| signal trace | `Trace4005::ADC_AVDD08_LO/9`, line 725149, byte 87,963,177; width `600 µm`; TOP |
| endpoints | `Node2980!!1` line 696738, byte 85,040,893, `(97.5,44.6) mm` → `Node16613` line 624206, byte 78,798,922, `(97.5,43.8) mm` |
| signal pad | `SR1_0X1_2`; PadStackDef lines 860268–860271; TOP regular box `1.0 × 1.2 mm` |
| return void owners | no negative shape bbox within 1 mm in the bounded scan |
| nearby return graph | `Trace8334::DGND`, line 739577, byte 88,912,800; `Via300507::DGND`, line 816587, byte 97,311,206 to IN43 plane `Node147591` |
| geometry verdict | outer plane contains route; absence of a nearby scanned void is not a no-void proof outside the declared crop |
| electrical verdict | `return_net_graph_present`; `signal_to_return_operator_unproved`; `source_faithful_return_blocked` |

### P1-T4006

| field | source evidence |
|---|---|
| crop bbox | `[69,72.5] × [31,35.5] mm` |
| signal trace | `Trace4006::ADC_AVDD08_LO/9`, line 725152, byte 87,963,379; width `500 µm`; TOP |
| endpoints | `Node2981!!1` line 696839, byte 85,051,364, `(70.5,34.0) mm` → `Node16614` line 624214, byte 78,799,559, `(70.5,33.36) mm` |
| signal pad | `SS0_5X0_5`; PadStackDef lines 860274–860277; TOP regular square `0.5 mm` |
| return void owners | `Polygon0157907`, line 20076, byte 2,353,315; `Polygon0159435`, line 20079, byte 2,353,657 |
| nearby return graph | `Trace8539::DGND`, line 740192, byte 88,947,445 / `Via300679`, line 816769, byte 97,333,740; `Trace8553::DGND`, line 740234, byte 88,949,811 / `Via300693`, line 816785, byte 97,335,736, both to IN43 plane nodes |
| geometry verdict | outer plane contains route; listed negative-void bboxes do not intersect route; nearest listed y gap is about `0.92 mm` |
| electrical verdict | `return_net_graph_present`; `signal_to_return_operator_unproved`; `source_faithful_return_blocked` |

P1 signal PadStacks above have TOP regular geometry but no raw return-pad owner. No raw record links `Trace4004/5/6` or their signal pad to the listed GND Trace/Via owners.

## P2 — TOP traces to `Signal$IN01_GND`

return artwork begins at raw line 3604, byte 474,114 with `.Shape Signal$IN01_GNDpkgshape`. outer `Polygon02015::GND+` has streamed bbox approximately `[-304.038,304.038] × [-240.538,240.538] mm` and contains all three route projections. TOP-to-IN01 medium is `208 µm MEGTRON-6`; TOP/IN01 copper thicknesses are `35/17.5 µm`.

세 route의 free endpoint는 return-plane negative circle 중심과 정확히 일치한다.

| crop | bbox mm | signal owner/endpoints | return void | nearby GND graph | verdict |
|---|---|---|---|---|---|
| P2-T9054 | `[52.5,56.5] × [-205.5,-202.5]` | `Trace9054`, line 3577641, byte 343,733,421; `Node3863!!1`, line 2863927, byte 283,532,850, `(55.05,-204)` → `Node26000`, line 2723120, byte 271,754,614, `(54.15,-204)`; width `600 µm` | `Circle01990643::GND-`, line 16230, byte 1,657,854; center `(54.15,-204)`, radius token `0.3502 mm` | `Trace14114::GND`, line 3556014, byte 341,950,627; `Via57787`, line 4969183, byte 497,691,773 to IN01 `Node129367`; follow-on `Via57788` | endpoint is void center; `signal_to_return_operator_unproved` |
| P2-T9055 | `[59.5,63.5] × [-205.5,-202.5]` | `Trace9055`, line 3577645, byte 343,733,660; `Node3864`, line 2864038, byte 283,542,321, `(62.05,-204)` → `Node26001`, line 2723131, byte 271,755,515, `(61.15,-204)` | `Circle01990645::GND-`, line 16229, byte 1,657,765; center `(61.15,-204)`, radius token `0.3502 mm` | `Trace14115`, line 3556017, byte 341,950,794; `Via57821`, line 4969778, byte 497,752,036 to `Node129401`; follow-on `Via57822` | endpoint is void center; `signal_to_return_operator_unproved` |
| P2-T9056 | `[66.5,70.5] × [-205.5,-202.5]` | `Trace9056`, line 3577649, byte 343,733,899; `Node3865`, line 2864149, byte 283,551,786, `(69.05,-204)` → `Node26002`, line 2723142, byte 271,756,414, `(68.15,-204)` | `Circle01990647::GND-`, line 16228, byte 1,657,676; center `(68.15,-204)`, radius token `0.3502 mm` | `Trace14116`, line 3556020, byte 341,950,961; `Via57855`, line 4970361, byte 497,810,424 to `Node129435`; follow-on `Via57856` | endpoint is void center; `signal_to_return_operator_unproved` |

signal pad owner `SM_RE0D9X0D8_DNI`는 PadStackDef line 5,713,698과 TOP regular box `0.9 × 0.8 mm`를 갖고 Anti row가 없다. nearby return vias는 `VIA_0D3CR0D6`; IN01 PadDef의 regular/anti raw radius token은 `0.3/0.35 mm`이고 parser 해석 diameter는 `0.6/0.7 mm`다. negative shape circle의 raw radius `0.3502 mm`와 anti radius가 대응한다.

이 자료는 GND pad/via가 IN01 plane graph로 이어지고 route의 `LowerRef` 이름이 실제 layer와 일치함을 증명한다. 반면 signal endpoint 아래에는 plane copper가 아니라 void가 있고, raw SPD에는 signal Trace/pad와 GND via/plane을 결합하는 field/current owner가 없다. 따라서 세 crop 모두:

```text
geometric_status = return_shape_and_endpoint_void_explicit
electrical_status = return_net_graph_present_but_signal_to_return_operator_unproved
promotion_status = source_faithful_return_blocked
```

## P2 inner `Trace13305` — IN24 between IN23/IN25 GND

이 case는 [`T1_TRACE_ORACLE_RESULTS.md`](T1_TRACE_ORACLE_RESULTS.md)의 source-derived manufactured asymmetric stripline 입력이다. raw primary owner는 `Trace13305::ADC_VDD075_SE_VDDL_C2C_2/0`, line 3,553,874, byte 341,810,617이고 continuation width owner는 line 3,553,875, byte 341,810,760의 `0.120 mm`다. endpoint는 `Node30557` line 2,774,208, byte 275,948,763의 `(32.2,-20.3) mm`와 `Node30558` line 2,774,219, byte 275,949,653의 `(36.4,-20.3) mm`이며 둘 다 `Signal$IN24_S1`이다. `UpperRef`/`LowerRef`는 없다.

| field | source evidence |
|---|---|
| crop bbox | `[30.0,38.0] × [-21.5,-19.0] mm` |
| upper return artwork | `.Shape Signal$IN23_GNDpkgshape`, line 404372, byte 41,273,219; outer `Polygon01567::GND+`, line 404373, byte 41,273,281 |
| lower return artwork | `.Shape Signal$IN25_GNDpkgshape`, line 434633, byte 44,100,885; outer `Polygon01538::GND+`, line 434634, byte 44,100,947 |
| dielectric owners | `Medium$87`, line 2265754, byte 232,169,606, 75 µm `MEGTRON-6_3`; `Medium$89`, line 2265758, byte 232,169,915, 104 µm `MEGTRON-6` |
| nearby void examples | IN23 `Circle01671572`, line 406055, byte 41,462,607 and `Circle01671582`, line 406102, byte 41,466,908; analogous IN25 `Circle01643073`, line 436316, byte 44,290,273 and `Circle01643082`, line 436315, byte 44,290,182 |
| nearby return graph | `Via955363/955396/955429/955463::GND` between IN23 and IN25; lines 5637891/5637941/5638002/5638053, bytes 567,956,109/567,961,231/567,967,891/567,973,125; `NoAntiPadLayers=Signal$IN24_S1` |
| via PadStack | `TH_0D3CR0D5_Mir`, line 5714096; IN23/IN25 regular/anti raw radius `0.2/0.25 mm`, interpreted diameter `0.4/0.5 mm` |
| geometry verdict | both outer positives contain the route projection. Nearby negative-circle bbox edges are 59.8 µm from its centerline, but the 120 µm trace has 60 µm half-width, leaving only about 0.2 µm nominal edge clearance; exact polygon/finite-width boolean tolerance is unproved and the relation is treated as near-tangent |
| electrical verdict | explicit GND via topology exists, but no local Trace owner joins those via nodes and no raw record binds signal current to either return; `signal_to_return_operator_unproved`; `source_faithful_return_blocked` |

The bounded evidence passes took about 3.4 s for the two shape sections, 4.2 s for selected nodes and 2.9 s for exact via owners on the current host. They retained crop-near records and counters only; no full-file materialization, hash or solve was performed. The manufactured coupon may replace the real artwork with declared continuous return rectangles only if that substitution is labeled artificial. It cannot serve as a board-faithful IN23/IN25 crop.

## 향후 source-faithful 승격 요구

1. crop 안의 complete positive/negative return artwork를 polygon boolean으로 복원하고 topology를 hash한다.
2. signal pad/trace와 return plane/vias를 별도 conductor terminal set로 두고 signed current basis를 선언한다.
3. return-current split은 가까운 via에 강제하지 않고 field/operator solve가 결정하게 한다.
4. exact와 retained core가 같은 terminal order, gauge, crop `Γ`, electric/magnetic field와 DtN trace space를 공유하게 한다.
5. route interior와 기존 Polygon/adjacent-gap owner의 overlap을 증명하고 겹치면 inside core를 제거한다.
6. crop R/2R/4R에서 raw `Z'/Y'`, interface voltage/current와 signed complex power가 0.5%/1% budget에 수렴한다.
7. P2 endpoint void와 via antipad field를 straight invariant trace template에 숨기지 않고 별도의 3-D launch/transition owner로 분리한다.

이 조건 전에는 P1/P2를 source-derived manufactured coupon의 치수 입력으로는 사용할 수 있지만 source-faithful board trace replacement로 사용할 수 없다.
