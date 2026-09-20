# ASTRA Step6L 저대역 PowerSI reference inventory

Program: **SPD Decap PI Evaluator v0.23.1**  
Date: 2026-09-07  
Scope: 기존 PowerSI reference producer/manifest와 원본 Touchstone의 주파수·헤더 metadata만 확인했다. raw SPD/scenario 재읽기, LU, geometry, solver/model 실행, 새 S→Z 추출은 하지 않았다.

## 결속된 source와 port18 경로

| 항목 | 경로/식별자 | SHA-256 또는 고정값 |
|---|---|---|
| 공통 Touchstone | `D:\S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p` (92-port, 303,974,090 B) | `c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11` |
| Touchstone metadata | `# Hz S RI R 1`; `! Port18_SITE0::ADC_VDD_075_VTRIP_SRAM/0`; Generated from `E:\Work\20260724 S4LB002_DC MLO PI\S4LB002-2Para_260729_1_injected.spd` | header evidence |
| source-only selection producer | `tools/research/select_astra_loaded_development_reference.py` | `8AE2DA3CF8788FA906A26E3855E28C34C49FD6D1559EB1CD2507C6C47664B2D5` |
| generic extraction reader/producer | `tools/research/extract_astra_powersi_1mhz.py` | `F941A4BE31D0B0D1DB2874AFEC05C3B4599132550E4A7A131D82BDE6F494A281` |
| source selection receipt | `docs/evaluation-research/astra_loaded_development_selection_2026-09-07.json` | `99D0F77FCCEB9D07B97AA65953DD42B8D106B7F2485F42F11EE643134D64B8F9` |
| port18 reference receipt | `docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json` | `761D61334678CEACA0DB55BC8C5539BB080EE36363EAE33A2C419A1EA5C06430` |
| saved source-frequency inputs | `outputs/research/astra-native-frequency-stamps-01/source-frequency-inputs.json` | `61390C0EF9C7554B83D55F91B2E81A59F9453690F783B747F2906C475BE5F6DC` |
| source-frequency receipt | `outputs/research/astra-native-frequency-stamps-01/receipt.json` | `4D93C4A89A02C7BC6295C27E8AA430CE3711EDB2D2102576FBC7CF7535A52856` |

`select_astra_loaded_development_reference.py` selects `ADC_VDD_075_VTRIP_SRAM/0`, proves `port_one_based=18`, validates the complete 92-port header, then reads the same Touchstone and extracts the diagonal `z_parameters[..., 17, 17]` at exact frequencies. The resulting existing points are:

| frequency | source index | `reference_zdd_ohm` |
|---:|---:|---:|
| 1 MHz | 175 | `0.0006915479466520245 - 0.0004257504501139287j` |
| 10 MHz | 200 | `0.0013351332705258876 + 0.0008761129830389292j` |
| 100 MHz | 325 | `0.001779666643964065 + 0.010512997908483828j` |
| 1 GHz | 625 | `0.0180428754633343 + 0.10995723900122274j` |

The direct `extract_astra_powersi_1mhz.py` default constants are for a different rail/port (`ADC_VDD_180_VQPS_SYS_1_AON/0`, port 44); the port18 extraction is the `select_astra_loaded_development_reference.py` path above, which reuses its reader and source hash but supplies the selected rail and port.

## Actual low-band samples

The original Touchstone metadata/data-record framing is 92-port width `1 + 2*92*92 = 16929` numeric fields per record. A bounded frequency-only stream (discarding all S values) found 826 total records in the existing receipts; records 0–175 span 0 through 1 MHz. The requested inclusive 1 kHz–1 MHz interval therefore contains **76 exact samples**, source indices **100–175**:

```text
[1000.0, 1096.47819614319, 1202.26443461741, 1318.25673855641,
 1445.43977074593, 1584.89319246111, 1737.80082874938, 1905.46071796325,
 2089.29613085404, 2290.86765276777, 2511.88643150958, 2754.22870333817,
 3019.95172040202, 3311.31121482591, 3630.78054770101, 3981.07170553497,
 4365.15832240166, 4786.30092322638, 5248.07460249772, 5754.39937337157,
 6309.57344480193, 6918.30970918936, 7585.77575029184, 8317.63771102671,
 9120.1083935591, 10000.0, 10964.7819614319, 12022.6443461741,
 13182.5673855641, 14454.3977074593, 15848.9319246111, 17378.0082874938,
 19054.6071796325, 20892.9613085404, 22908.6765276777, 25118.8643150958,
 27542.2870333817, 30199.5172040202, 33113.1121482591, 36307.8054770102,
 39810.7170553497, 43651.5832240166, 47863.0092322638, 52480.7460249772,
 57543.9937337157, 63095.7344480193, 69183.0970918936, 75857.7575029184,
 83176.3771102671, 91201.083935591, 100000.0, 109647.819614319,
 120226.443461741, 131825.673855641, 144543.977074593, 158489.319246111,
 173780.082874938, 190546.071796325, 208929.613085404, 229086.765276777,
 251188.643150958, 275422.870333817, 301995.172040202, 331131.121482591,
 363078.054770102, 398107.170553497, 436515.832240166, 478630.092322638,
 524807.460249772, 575439.937337157, 630957.344480193, 691830.970918936,
 758577.575029184, 831763.771102671, 912010.83935591, 1000000.0]
```

`source-frequency-inputs.json` is a different source-model grid: 3 sampled capacitor models, 401 model frequencies from 1 kHz to 1 GHz, with 201 model samples in 1 kHz–1 MHz. It is not a substitute for the 92-port Touchstone frequency grid. Its source scenario binding is `scenario_sha256=2f5ae107221857f7f093ab8d6cbcff4c286a0c6cee5b6a9994de1c384cf39c83` and its source bundle is the D115b materialized `.spdpi` recorded in the receipt.

## Smallest additional verification candidate

Use **100 kHz, source index 150, port 18** as the first additional low-band extraction point. It is an exact Touchstone sample, sits at a decade anchor between the existing 1 MHz point and the 1 kHz lower bound, and requires no interpolation. A future producer should preserve the existing full-92-port header validation, source hash, port18 mapping, and S→Z diagnostics; this memo does not claim a 100 kHz impedance value because no new extraction was run.

## Gates and limitations

- P1: none found in the source/hash/port/frequency inventory.
- P2: the existing port18 receipt stores 1/10/100/1000 MHz Zdd values only; it does not store low-band Zdd values. The 100 kHz item is a precise candidate, not a measured result.
- No low-band accuracy, broadband correlation, physical return-path, geometry, LU, PowerSI rerun, or product claim is made.
