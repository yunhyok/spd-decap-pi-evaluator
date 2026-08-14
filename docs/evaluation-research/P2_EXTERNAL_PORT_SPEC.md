# SPD Decap PI Evaluator v0.22.0 — P2 ExternalPowerSiPort Contract

최종 갱신: 2026-08-14 (Asia/Seoul)

이 문서는 Pair P2의 네 PowerSI port를 raw SPD와 S4P에 묶는 fail-closed 연구 명세다. 현행 `DEVICE_BUMP` 또는 editable top decap 의미로 변환하지 않는다. 구현은 사용자의 별도 승인 전까지 하지 않는다.

## Source identity

- SPD SHA-256: `BF8944C35D433181EBDE2A9D23BDC831CCA1BE847ADCB42B280E7CB435785A31`
- S4P SHA-256: `3DA7197159C94BFBEA725CC7C2F6D0F2AD1C5DAB8CF2747D030074EB4F180CC7`
- Touchstone: `# Hz S RI R 1`, 4 ports, 826 records, DC 및 0.1 Hz–2 GHz
- SPD saved PowerSI: `ReferenceImpedance2=1`, 0–2 GHz Adaptive
- source component: `L25P08085A7_LGA`, origin `(0,0)`, `StartLayer=Signal$BOTTOM`, `AttachLayer=BottomAir`
- Part: 72.6 mm × 72.6 mm, `Tags="IC"`; `IO`가 아님
- package padstack: `TH_0D3CR0D5_Mir`, drill 0.15 mm, material COPPER

## Exact port ordering

| S4P ordinal | SPD port | exact Touchstone/SPD label | positive net |
|---:|---:|---|---|
| 1 | 31 | `ADC_VDD075_NE_VDD_C2C/0` | same |
| 2 | 38 | `ADC_VDD075_NW_VDD_C2C/0` | same |
| 3 | 45 | `ADC_VDD075_SE_VDD_C2C/0` | same |
| 4 | 52 | `ADC_VDD075_SW_VDD_C2C/0` | same |

S4P header의 `Port[1]`–`Port[4]`와 SPD `Port31/38/45/52`의 order를 그대로 보존한다. direction 이름을 좌표로 재정렬하거나 alphabetic sort하지 않는다.

## Physical terminal sets

각 port는 ordered positive package terminal 52개와 동일한 ordered GND reference terminal 6,227개를 가진다.

| ordinal | region | positive count | positive bbox (mm) | ordered positive SHA-256 | direct-via mapping SHA-256 |
|---:|---|---:|---|---|---|
| 1 | NE | 52 | `[10.15, 2.45, 31.15, 31.15]` | `1db711a9ec2414dc49a459fce5211cd8f4eae3d687ea5ca2a0775e995ccdeec3` | `359438089c71b5b8f114578575193c4ac5dea3627be9c16f6fd67bdedb6b7be1` |
| 2 | NW | 52 | `[-30.45, 2.45, -10.15, 31.85]` | `f16d7ec82029c3bfc95b448a61720a6c791044cf5f6fd74aa2ab8ba1b950ce17` | `6fdd189fc9eb3b2b88d9dddbd7b3b192854d5d49c45125dd26875dfe517e0ccc` |
| 3 | SE | 52 | `[10.15, -31.15, 30.45, -2.45]` | `885b5b1ad3f508bf3e4a7d9aa85cef9b002d5610dc8df0c212523334e33b540e` | `40e5e336615a53d532d18c1e9a09c23cdf01a596fc24664c77a421c6b779e125` |
| 4 | SW | 52 | `[-30.45, -32.55, -10.15, -2.45]` | `876803784635032f7abf25e4c65a118d392baa6bf96b94da8799b7bc4716b48a` | `94688d46fca397f8be33912c2d5b3a4870313a45fea60fb8d7ac64d24faba89f` |

공통 reference:

- net: `GND`
- ordered terminal count: 6,227
- bbox: `[-36.05, -36.05, 36.05, 36.05] mm`
- ordered `{node,pin,net}` SHA-256: `f3a58cbe2a96bfcf01952ff170a8f223af8b034229363e38b7e27e7180878715`
- ordered direct-via mapping SHA-256: `84f8f1f345d143e8bcd60554389e6cdfabd2f888fe61cf446e26deeee9d0be61`

네 positive 집합과 공통 GND를 합친 unique package node는 6,435개다. root의 독립 streaming 재검증 결과:

- 6,435/6,435 Node가 모두 발견됨
- 모두 `Signal$BOTTOM`, `TH_0D3CR0D5_Mir`
- 6,435개 모두 source-incident Via가 정확히 1개
- 그 Via의 padstack도 모두 `TH_0D3CR0D5_Mir`
- 네 port의 6,227 GND terminal order가 byte-level parse 기준 동일

hash만 runtime record를 대신할 수 없다. 실제 manifest에는 아래 full ordered record가 들어가고 hash는 integrity check로 사용한다.

```json
{
  "node_id": "Node61798",
  "pin": "LGA_J94",
  "net": "ADC_VDD075_NE_VDD_C2C/0",
  "x_mm": 29.05,
  "y_mm": 30.45,
  "layer": "Signal$BOTTOM",
  "padstack": "TH_0D3CR0D5_Mir",
  "incident_via_id": "Via5488",
  "opposite_node_id": "Node61799"
}
```

대표 source records:

- NE: `Node61798!!LGA_J94`, `(29.05,30.45) mm`; `Via5488`
- NW: `Node87599!!LGA_AN38`, `(-10.15,13.65) mm`; `Via27945`
- SE: `Node84520!!LGA_DL96`, `(30.45,-26.95) mm`; `Via25304`
- SW: `Node1198377!!LGA_BT38`, `(-10.15,-2.45) mm`; `Via1118850`
- GND: `Node1029290!!LGA_CG103`; `Via952700`

## Manifest schema v1

```json
{
  "schema_version": "external-powersi-port-manifest-v1",
  "source": {
    "sha256": "BF8944C35D433181EBDE2A9D23BDC831CCA1BE847ADCB42B280E7CB435785A31"
  },
  "touchstone": {
    "sha256": "3DA7197159C94BFBEA725CC7C2F6D0F2AD1C5DAB8CF2747D030074EB4F180CC7",
    "format": "# Hz S RI R 1",
    "expected_port_count": 4
  },
  "component": {
    "refdes": "L25P08085A7_LGA",
    "part_tag": "IC",
    "start_layer": "Signal$BOTTOM",
    "attach_layer": "BottomAir"
  },
  "common_reference_terminal_set": {
    "id": "p2-common-gnd",
    "net": "GND",
    "records": [],
    "ordered_records_sha256": "f3a58cbe2a96bfcf01952ff170a8f223af8b034229363e38b7e27e7180878715"
  },
  "ports": [
    {
      "touchstone_ordinal": 1,
      "touchstone_header_label": "ADC_VDD075_NE_VDD_C2C/0",
      "spd_port_number": 31,
      "spd_port_header": "Port31_L25P08085A7_LGA::ADC_VDD075_NE_VDD_C2C/0",
      "positive_terminals": [],
      "reference_terminal_set_id": "p2-common-gnd",
      "source_contact_side": "BOTTOM",
      "physical_status": "proven",
      "excitation_weighting": null,
      "reference_mode": null,
      "deembedding": null,
      "renormalization": null
    }
  ]
}
```

## Known facts와 unknown operator fields

source geometry로 증명된 것은 node/pin/net/order/coordinate/layer/padstack/direct-via mapping이다. 다음은 SPD/S4P 값만으로 추론하지 않는다.

- 52 positive 및 6,227 GND terminal 사이의 PowerSI excitation/current weighting
- current sign convention과 differential/common reference mode
- field-port footprint와 reference-plane 처리
- de-embedding plane 또는 package 내부 reference shift
- observed `R 1`과 `ReferenceImpedance2=1` 이외의 renormalization
- 실제 export solver branch와 mesh convergence
- `Usage=0b1000`이 PowerSI inclusion에 주는 의미

이 필드가 null인 동안 P2의 S4P와 계산 결과 사이 numerical correlation은 `blocked_missing_external_port_operator_semantics`다. 모든 pin을 uniform equipotential current로 놓는 것은 하나의 연구 가정일 뿐 source fact가 아니므로 reference comparison에 사용하지 않는다.

## Fail-closed validation

manifest loader와 future fixture는 다음을 모두 검사해야 한다.

1. SPD/S4P hash가 exact identity와 일치
2. S4P port count, option line, raw label/order가 일치
3. SPD port number/header/component가 일치
4. positive 52개와 GND 6,227개에 missing/duplicate 없음
5. 네 reference terminal set의 ordered record가 완전히 동일
6. 모든 terminal의 net, coordinate, BOTTOM layer, padstack가 manifest와 일치
7. 모든 6,435 terminal에 source-incident Via가 정확히 1개이며 opposite node와 via padstack가 일치
8. unknown operator field를 임의 default로 바꾸지 않음
9. 이 contract를 `DEVICE_BUMP` 또는 top decap 후보로 coerce하지 않음

하나라도 실패하면 rail이나 dummy port를 만들지 않고 exact mismatch를 보고한다.

## 현행 code와의 경계

- `PinKind`에는 `DEVICE_BUMP`, `DECAP_PAD`만 있고 `ExternalPowerSiPort`가 없다.
- SPD candidate selection은 Part `Tags=IO`와 `AttachLayer=TopAir`를 모두 요구한다.
- endpoint evidence와 decap filtering도 TOP-side 의미를 갖는다.
- P2는 `Tags="IC"`, `BottomAir`이므로 현행 scope 밖에서 `SPD_NO_RAILS`가 발생한 것이 정상이다.

`BottomAir` 한 줄 허용, IO tag 위조, TOP node filter 제거만으로는 port semantics가 완성되지 않는다. future 구현은 side-aware physical terminal certificate 또는 별도 `ExternalPowerSiPort` domain type을 설계하고, 위 manifest와 operator unknown gate를 함께 가져야 한다.

## Raw evidence ranges

P2 SPD line 기준:

- padstack: 5,714,096–5,714,100
- Part/component: 5,805,166–5,805,167; 5,818,320–5,818,321
- saved PowerSI reference/sweep: 5,819,954–5,819,967
- Port31: 5,866,936–5,868,506
- Port38: 5,877,915–5,879,485
- Port45: 5,888,894–5,890,464
- Port52: 5,899,873–5,901,443

S4P header line 2–5에 raw PowerSI port label, line 13–16에 ordinal mapping이 있다. line 12는 `NETSLIST`다. line range는 exact source identity가 일치할 때만 유효하다.
