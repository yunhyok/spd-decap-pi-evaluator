# Reference Dataset Contract

최종 갱신: 2026-08-14

원본 파일은 `D:\Downloads`에 보존하며 Git repository에 복사하지 않는다. 모든 비교 결과는 아래의 absolute path와 SHA-256이 일치할 때만 같은 기준자료로 인정한다. 파일명, PowerSI `Generated from` 주석, port mapping, component state가 다르면 자동으로 같은 pair라고 가정하지 않는다.

## Canonical file manifest

| Pair | 역할 | 파일 | bytes | SHA-256 |
|---|---|---|---:|---|
| P1 | reserved transfer/scale holdout | `D:\Downloads\s5m6585_32p_260414_length3_1.spd` | 139,414,807 | `F774DC06CDCE4A1E0A632EB4A126D06AF1FD57B9F400287E510F233EDE1C0AD9` |
| P1 | PowerSI reference | `D:\Downloads\s5m6585_32p_260414_length3_1_041426_171904_99744_S.s160p` | 699,069,694 | `A3762DBDAF095E6B15E73F001A70A39047ECC835DF9BFAD2C7A4D685ED5EF9F3` |
| P2 | reference integrity / external-port contract | `D:\Downloads\PC-2576_S4LQ015A_B0_1P-L01_251017_V2_PI_DATA_VDD075_NE_VDD.spd` | 595,452,716 | `BF8944C35D433181EBDE2A9D23BDC831CCA1BE847ADCB42B280E7CB435785A31` |
| P2 | PowerSI reference | `D:\Downloads\PC-2576_S4LQ015A_B0_1P-L01_251017_V2_PI_DATA_VDD075_NE_VDD_101925_172246_41448_S.s4p` | 561,701 | `3DA7197159C94BFBEA725CC7C2F6D0F2AD1C5DAB8CF2747D030074EB4F180CC7` |
| P3 | paired perturbation candidate: power-plane mod | `D:\Downloads\S4LB002-2Para_power_plane_mod_0809_1_injected (2).spd` | 1,227,509,179 | `E81FD5F9933A5F8FD6FBD19B4FAAA17CEAABA0A5CA84991F2FFB6508DBDF924D` |
| P3 | PowerSI reference | `D:\Downloads\S4LB002-2Para_power_plane_mod_0809_1_injected_081026_055326_18520_S.s92p` | 230,131,212 | `7FED6F25A6321B628166C09E533FB1C35E66E43994E1739809ABBC9774E3C2CE` |
| P4 | paired perturbation candidate: 260808 base | `D:\Downloads\S4LB002-2Para_260808_1_injected (2).spd` | 1,207,304,655 | `CEEEF0D7377556912513C4EB678E4602DE41B25C78619F140221FC52A2D40042` |
| P4 | PowerSI reference | `D:\Downloads\S4LB002-2Para_260808_1_injected_080926_034030_18520_S.s92p` | 230,070,298 | `592192EF2F9DD130DC81C31CD8A369E4F3C4C0DA49C384814A5A184D9838B3BD` |

## Touchstone contract

네 파일 모두 `# Hz S RI R 1`이다. 즉 Hz 단위, S-parameter, real/imaginary 형식, scalar reference impedance 1 Ω이다.

| Pair | ports | records | frequency range | ground | port 요약 |
|---|---:|---:|---|---|---|
| P1 | 160 | 626 | DC 및 0.1 Hz–1 GHz | `DGND` | 5 rail family × 32 instances, `/0`; Port 1 `ADC_AVDD08_LO/0`, Port 160 `ADC_IO_BUCK13/31` |
| P2 | 4 | 826 | DC 및 0.1 Hz–2 GHz | `GND` | `ADC_VDD075_{NE,NW,SE,SW}_VDD_C2C/0` |
| P3 | 92 | 626 | DC 및 0.1 Hz–2 GHz | `DGND` | 46 `/0` + 46 `/1`; header prefix `3.1_SITE0-`/`3.1_SITE1-` |
| P4 | 92 | 626 | DC 및 0.1 Hz–2 GHz | `DGND` | 46 `/0` + 46 `/1`; header prefix `3rd_SITE0-`/`3rd_SITE1-` |

모든 frequency row는 strictly increasing이고 중복이 없다. P1/P2는 100 kHz index 150, 1 MHz index 175, 1 GHz index 625이다. P3/P4는 100 kHz index 49, 1 MHz index 57, 1 GHz index 425이다. 모든 grid는 500 MHz 이후 5 MHz 간격이다. P3/P4의 frequency grid는 완전히 동일하다.

### Header compatibility anomaly

현재 production parser의 implicit convention은 legacy `2nd_SITE#-...` 또는 `SITE#_<run>-...`만 허용한다. 따라서 P3의 `3.1_SITE...`와 P4의 `3rd_SITE...`를 자동 의미 해석해서는 안 된다. 추후 benchmark manifest는 port number → exact raw header label → physical rail/site를 명시해야 한다.

2026-08-14의 P3/P4 reference-only 분석은 site와 `/0`/`/1`을 먼저 검증한 뒤 해당 prefix만 임시 정규화한 **research-only adapter**로 수행했다. 이는 production parser 지원 또는 release validation을 의미하지 않는다.

### `Generated from` identity anomaly

- P1/P2의 generated-from basename은 제공 SPD basename과 일치한다.
- P3/P4의 PowerSI 주석은 각각 `S4LB002-2Para_power_plane_mod_0809_1_injected.spd`, `S4LB002-2Para_260808_1_injected.spd`를 가리킨다.
- 다운로드된 SPD의 `(2)`는 보통 중복 다운로드 suffix로 보이지만, hash 또는 provenance가 없는 상태에서 동일 파일이라고 단정하지 않는다.

## Streaming SPD inventory

아래 수치는 source record count이며 solver가 보존한 node count가 아니다. Via는 all `Via...` starts와 현행 net-qualified grammar가 다를 수 있어 둘을 함께 기록한다.

| Pair | conductor shapes | total thickness rows | Node | Trace | Via all / net-qualified | PadStackDef | PadDef | Part/PartialCkt | Component/Connect |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P1 | 24 | 47 | 143,406 | 12,544 | 109,235 / 109,203 | 10 | 129 | 8 | 3,137 |
| P2 | 78 | 155 | 1,271,131 | 15,052 | 1,193,902 / 1,193,766 | 40 | 1,599 | 69 | 7,306 |
| P3 | 48 | 95 | 2,934,889 | 1,451,285 | 1,956,937 / 1,956,937 | 98 | 187 | 10 | 10,727 |
| P4 | 48 | 95 | 2,934,791 | 1,451,209 | 1,956,909 / 1,956,909 | 98 | 187 | 10 | 10,727 |

P3와 P4는 layer name/order, shape/pad/component aggregate가 같고 P3가 Node 98, Trace 76, Via 28개 더 많다. P1/P2의 all/net-qualified Via 차이 32/136은 `::<net>` 없는 source row이며 source-faithful manifest에서 `net=absent`로 보존한다. saved solver option도 다르므로 이 aggregate만으로 controlled perturbation이라고 판정하지 않는다.

## Source parameter availability

전체 trace/material/via/padstack/antipad/roughness streaming 결과와 owner 정책은 [`SOURCE_PARAMETER_MANIFEST.md`](SOURCE_PARAMETER_MANIFEST.md)에 고정한다. 핵심 결손은 다음과 같다.

- P1/P2 Trace width는 100% explicit이다.
- P3/P4는 각각 239,135/239,070 Trace, 약 16.5%에 width가 raw source부터 없다.
- P1/P2에는 padstack anti geometry와 per-via `NoAntiPadLayers`가 있으나 현행 `_VIA_RE`가 후자를 보존하지 않는다.
- P3/P4에는 anti shape와 `NoAntiPadLayers`가 모두 없다.
- 네 파일 모두 plating/fill과 roughness data section은 비어 있다.
- P3/P4 ABF-GL102는 multi-frequency material rows가 있지만 P1/P2 dielectric은 사실상 1 GHz single-point table이다.

이 결손은 PowerSI curve fit이나 design median으로 채우지 않는다. 각 parameter는 `explicit`, `absent`, `parser_not_preserved`, `derived_node_link`로 분류한다.

### T1 Trace/ref screening

bounded two-pass streaming으로 `width explicit + same-layer endpoints + concrete UpperRef/LowerRef 하나 이상`을 metadata screening했다. 이는 return polygon/connectivity pass가 아니다.

| pair | total | width explicit | concrete-ref screening | unique metadata profiles | source-faithful return status |
|---|---:|---:|---:|---:|---|
| P1 | 12,544 | 12,544 | 6,816 | 8 | explicit ref name, connectivity unproved |
| P2 | 15,052 | 15,052 | 2,976 | 57 | explicit ref name, connectivity unproved |
| P3 | 1,451,285 | 1,212,150 | 0 | 97 | ref absent; stackup-derived only |
| P4 | 1,451,209 | 1,212,139 | 0 | 88 | ref absent; stackup-derived only |

P1의 concrete refs는 TOP→`Plane$IN43_DGND` 5,600개와 BOTTOM→`Plane$IN64_DGND` 1,216개다. P2 largest eligible profile은 TOP 600 µm→`Signal$IN01_GND` 2,827개다. P2에는 length ≥2 mm Trace가 7,154개라 broadband lumped-short 가정을 전체에 적용할 수 없다.

P3/P4의 모든 endpoint는 같은 Signal layer로 해석되지만 raw Trace grammar에 routed/plane-mesh semantic flag가 없다. 약 39%의 Trace net이 DGND 등 conductor-layer token과 일치하는 사실은 mixed topology 가능성을 보일 뿐, 모든 Trace가 plane mesh 또는 physical route라는 판정 근거가 아니다. T1 세부 owner와 candidate raw line은 [`T1_TRACE_ORACLE_RESULTS.md`](T1_TRACE_ORACLE_RESULTS.md)에 고정한다.

P1/P2 selected explicit-ref crop에서는 named return artwork와 nearby same-net GND Trace/Via-to-plane graph가 source에 존재한다. 그러나 raw SPD에는 candidate signal Trace/pad와 그 return owners를 묶는 signed current/field owner가 없다. P2 `Trace9054/55/56` free endpoint는 IN01 GND negative-circle void 중심과 일치하므로 endpoint 아래 plane copper도 없다. 이 단계는 `return_shape_geometry_present`와 `return_net_graph_present`일 뿐 `signal_to_return_operator_unproved`; exact crop owner와 line/byte evidence는 [`T1_RETURN_CROP_MANIFEST.md`](T1_RETURN_CROP_MANIFEST.md)에 고정한다.

layer row에 numeric conductivity token은 없지만 explicit `Material=COPPER/...`가 usable `.MetalModel`에 연결되면 `resolved_sigma=derived_material_table`로 분류한다. 이를 conductivity wholly absent 또는 fitted default로 부르지 않는다.

## Recovered PowerSI provenance and confounds

Touchstone header와 SPD의 saved option에서 다음을 복구했다. `Layout Workbench` build와 host는 provenance 증거지만 실제 export에 어떤 internal solver block이 사용됐는지까지 증명하지 않는다.

| Pair | Touchstone generator | host | SPD material database | saved sweep/reference |
|---|---|---|---|---|
| P1 | Layout Workbench `25.1.0.09191.616638 000` | `WS202402-003W11` | `C:\Cadence\SPB_22.1\share\pcb\text\material.cmx` | Touchstone 0–1 GHz, `R 1` |
| P2 | Layout Workbench `23.1.3.12171.472612 300` | `WS202402-003W11` | `C:\Cadence\Sigrity2023.1\share\pcb\text\material.cmx` | PowerSI 0–2 GHz Adaptive, `ReferenceImpedance2=1` |
| P3 | Layout Workbench `25.1.0.09191.616638 000` | `WS202402-003W11` | SPB 22.1 material path | PowerSI 0–2 GHz Adaptive, `ReferenceImpedance2=1` |
| P4 | Layout Workbench `25.1.0.09191.616638 000` | `WS202402-003W11` | SPB 22.1 material path | PowerSI 0–2 GHz Adaptive, `ReferenceImpedance2=1` |

P3/P4에는 다음 saved-state 차이가 있다.

- P4에 `.MaxEdgeLength=4.970000e-03`이 있으나 P3에는 없다.
- P3/P4 OuterBoxSize가 약 `0.01496719`/`0.01491`로 다르다.
- P4 3DEMCap에는 0.140603 크기의 `dxp/dxm/dyp/dym/dzp/dzm`가 있으나 P3에는 없다.
- P4 3DEMInd에는 0.3 크기의 동일 방향 buffer와 `DielectricBufferSize=0.140603`이 있고, P3의 `DielectricBufferSize`는 0이다.
- P4 3DEMInd에는 `1 kHz–1 GHz Log` sweep이 저장되어 있으나 P3의 해당 sweep은 비어 있다. 공통 core option에는 100 MHz solution frequency, mesh algorithm 4, adaptive ratio 0.3, error epsilon 0.02 등이 있다.
- P4 PowerDC/Thermal block에도 P3에 없는 mesh state가 있다.

Streaming semantic diff는 solver option 외의 차이도 확인했다.

- canonical Node body 기준 P3에서 133개가 제거되고 35개가 추가되어 net −98이다. 변화는 주로 x≈2.1–2.65 mm, y≈9.35–9.90 mm와 VQPS 관련 power/DGND net에 집중된다.
- 48개 Shape name은 같지만 exact text-block hash는 41/48에서 다르다. 일부 차이는 polygon order/annotation일 수 있으므로 실제 geometry 변화량으로 바로 환산하지 않지만, text 차이가 한 block에만 국한되지는 않는다.
- `Signal$L21(DGND)`의 dielectric은 P3 `EL190T`, P4 `ABF-GL102`다.
- `CAP_1608_10UF_NOT_MOUNTED` PartialCkt는 P3에서 빈 body이고 P4에는 Murata `GRM188Z71A106KA73` 10 µF small-signal model이 들어 있다. 두 파일 모두 이 model name을 68개 Connect에서 참조하고 `Usage=0b111000`로 기록한다. 이름과 usage 때문에 실제 solve 포함 여부는 PowerSI 의미가 확인될 때까지 unknown이다.
- P3는 전체 Connect 중 `Usage=0b1000` 10,651개와 `0b111000` 76개를 명시하지만, P4는 각각 123개와 68개만 명시하고 10,536개는 Usage를 생략한다. default serialization인지 실제 state 차이인지 미확정이다.
- 10,727 Connect의 `Checked=1` aggregate는 둘 다 같지만 terminal/node 의미 동일성은 아직 exhaustive하게 증명되지 않았다.

따라서 같은 92-port/grid와 VQPS 집중 response 차이는 유용하지만, 현재는 **geometry + material + component-model/state + solver-state가 섞인 multifactor pair**다. P4의 10 µF model body가 response 차이의 원인이라는 주장은 채택하지 않는다. `Usage=0b111000`의 의미와 실제 export inclusion을 모르는 상태이기 때문이다. 동일 설정 재해석 또는 factor별 coupon이 확보되기 전에는 predicted `ΔZ`의 엄격한 물리 검증에 사용하지 않는다.

## Reference-only observations

### Pair P2: four-port reference and port-contract case

Production Touchstone reader와 S→Z converter를 그대로 사용한 결과:

- max `cond(I-S)`: 1.75896
- max relative conversion residual: 3.54e-16
- max Z reciprocity relative norm: 1.67e-17
- max S singular value: 0.99991948; minimum `Hermitian(Z)` eigenvalue: +1.554 mΩ
- diagonal |Z| at 100 kHz: 64.04–65.80 mΩ
- diagonal |Z| at 1 MHz: 5.91–6.03 mΩ
- diagonal |Z| at 10 MHz: 6.00–7.34 mΩ
- diagonal |Z| at 100 MHz: 76.24–94.58 mΩ
- diagonal |Z| at 1 GHz: 0.231–0.291 Ω
- diagonal |Z| at 2 GHz: 0.205–0.280 Ω

P2는 port 수는 작지만 78 conductor layer와 약 119만 raw via를 갖는다. 100 kHz–100 MHz 최대 정규화 transfer coupling은 0.283%에 불과하므로 첫 물리 귀속 coupon으로는 약하지만, external-port 의미와 내부 topology 비용을 시험하는 후속 sanity case로 가치가 있다.

현행 import는 `L25P08085A7_LGA`가 bottom-attached이고 IO tag가 없어 `DEVICE_BUMP`와 rail을 만들지 못해 `SPD_NO_RAILS`로 차단됐다. 상태는 `reference_integrity: passed`, `p2_import: blocked_no_external_port_contract`, `p2_frequency_solve: not_run`, `p2_production_correlation: blocked_no_generic_p2_runner`, `performance_promotion: unassessed`다. 세부 상태와 향후 port 계약은 [`BASELINE_PROTOCOL.md`](BASELINE_PROTOCOL.md)에 고정한다.

raw port block의 physical terminal reconstruction은 [`P2_EXTERNAL_PORT_SPEC.md`](P2_EXTERNAL_PORT_SPEC.md)에 고정했다. 각 port는 ordered positive terminal 52개와 동일한 ordered GND terminal 6,227개를 갖는다. 합계 6,435 unique package node가 모두 `Signal$BOTTOM`, `TH_0D3CR0D5_Mir`이며 source-incident Via가 정확히 하나씩 있다는 것을 독립 streaming으로 재검증했다. physical mapping은 완료됐지만 terminal current weighting, reference mode/plane, de-embedding과 solver branch는 unknown이므로 numerical correlation은 계속 차단한다.

### Pair P3/P4: paired response evidence with solver-state confound

두 reference는 차원과 grid가 같지만 첫 DC row부터 값이 다르므로 별개의 simulation이다. 정규화된 physical port order는 일치한다.

| group | metric: median absolute ΔdB P3 vs P4 | 100 kHz | 1 MHz | 10 MHz | 100 MHz |
|---|---|---:|---:|---:|---:|
| non-VQPS 82 ports | median | 0.0010 | 0.0037 | 0.0055 | 0.1000 |
| non-VQPS 82 ports | p95 | 0.0030 | 0.0313 | 0.0768 | 2.0245 |
| VQPS 10 ports | median | 7.097 | 7.098 | 7.144 | 12.684 |
| VQPS 10 ports | maximum | 14.906 | 14.907 | 14.992 | 27.469 |

변화가 VQPS group에 집중되고 다른 port의 low/mid-band는 대체로 안정적이다. 하지만 saved solver-state가 다르므로 현재는 localization 가설 생성에만 사용한다. 동일 export 설정이 증명된 뒤에야 `predicted ΔZ` counterfactual benchmark로 승격한다. 두 파일은 서로를 독립적인 일반화 holdout으로 계산하지 않는다.

Reference-only transform 진단은 P3 max condition 7.41e5/residual 6.92e-16, P4 max condition 1.32e6/residual 7.58e-16이었다. 이 조건수는 record별 비교 domain 선택과 error interpretation에 반드시 포함한다.

### Pair P1: reserved holdout policy

P1은 port/header/grid와 SPD inventory만 조사했다. 후보의 구조와 parameter policy가 동결되기 전에는 full S→Z curve, rail별 peak, 오차 최적화를 열람하지 않는다. 이 정책은 완전한 blind test가 아니라 leakage를 줄인 reserved holdout이다. 최종 주장은 동결 후 별도로 확보할 fifth unseen design이 필요하다.

## Memory-safe reference preprocessing

현행 reader는 text를 순차적으로 읽지만 모든 float row와 complex S matrix를 메모리에 보존한다.

- S160P final complex S: 약 244.5 MiB
- S160P dynamic row buffer와 complex matrix가 겹치는 parser peak: 약 0.63 GiB
- full S→Z는 같은 크기의 complex Z를 추가하고 record별 O(p³) solve를 수행
- S92P parser peak: 약 0.21 GiB, 이후 Z 약 80.9 MiB 추가

따라서 8 GB 환경에서는 reference parse, board compile, sparse factor를 동시에 수행하지 않는다. P1은 주파수 block streaming과 사전 등록한 matrix element/eigenmetric만 보존하는 방법을 먼저 연구한다.

## Dataset split and leakage rules

| 데이터 | 용도 | 최적화에 사용 가능? |
|---|---|---|
| P2 | reference integrity, explicit external-port 계약, 후속 diagonal sanity | reference-only 가능; production correlation은 계약 전 차단 |
| P3/P4 | paired response/localization 및 향후 counterfactual | solver-state confound 해소 전 parameter 선택에 사용 금지 |
| P1 | transfer/scale reserved holdout | 후보 동결 전 curve 기반 조정 금지 |
| future pair P5 | final blind validation | code/parameter/threshold/manifest 동결 후 1회 평가 |

모든 실험은 입력 hash, port manifest version, solver profile, material source, component state, frequency set과 metric policy를 함께 기록한다.

## 아직 필요한 PowerSI provenance

- 알려진 Layout Workbench build와 실제 export solver branch의 대응
- adaptive mesh convergence log와 최종 mesh/order
- 3D EM 또는 quasi-static option과 solver mode
- dielectric dispersion, conductor roughness/skin model
- component enabled/disabled state와 model source
- port geometry, reference conductor, de-embedding/renormalization
- DC row의 생성/해석 규칙
- P3/P4 중 어떤 saved block이 실제 S92P export에 사용됐는지와 component state 동일성

이 정보가 없으면 PowerSI curve는 유용한 target reference이지만 절대 오차 floor나 mesh convergence를 규정할 수 없다.
