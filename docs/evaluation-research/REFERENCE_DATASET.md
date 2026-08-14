# Reference Dataset Contract

최종 갱신: 2026-08-14

원본 파일은 `D:\Downloads`에 보존하며 Git repository에 복사하지 않는다. 모든 비교 결과는 아래의 absolute path와 SHA-256이 일치할 때만 같은 기준자료로 인정한다. 파일명, PowerSI `Generated from` 주석, port mapping, component state가 다르면 자동으로 같은 pair라고 가정하지 않는다.

## Canonical file manifest

| Pair | 역할 | 파일 | bytes | SHA-256 |
|---|---|---|---:|---|
| P1 | reserved transfer/scale holdout | `D:\Downloads\s5m6585_32p_260414_length3_1.spd` | 139,414,807 | `F774DC06CDCE4A1E0A632EB4A126D06AF1FD57B9F400287E510F233EDE1C0AD9` |
| P1 | PowerSI reference | `D:\Downloads\s5m6585_32p_260414_length3_1_041426_171904_99744_S.s160p` | 699,069,694 | `A3762DBDAF095E6B15E73F001A70A39047ECC835DF9BFAD2C7A4D685ED5EF9F3` |
| P2 | small canonical development | `D:\Downloads\PC-2576_S4LQ015A_B0_1P-L01_251017_V2_PI_DATA_VDD075_NE_VDD.spd` | 595,452,716 | `BF8944C35D433181EBDE2A9D23BDC831CCA1BE847ADCB42B280E7CB435785A31` |
| P2 | PowerSI reference | `D:\Downloads\PC-2576_S4LQ015A_B0_1P-L01_251017_V2_PI_DATA_VDD075_NE_VDD_101925_172246_41448_S.s4p` | 561,701 | `3DA7197159C94BFBEA725CC7C2F6D0F2AD1C5DAB8CF2747D030074EB4F180CC7` |
| P3 | controlled perturbation: power-plane mod | `D:\Downloads\S4LB002-2Para_power_plane_mod_0809_1_injected (2).spd` | 1,227,509,179 | `E81FD5F9933A5F8FD6FBD19B4FAAA17CEAABA0A5CA84991F2FFB6508DBDF924D` |
| P3 | PowerSI reference | `D:\Downloads\S4LB002-2Para_power_plane_mod_0809_1_injected_081026_055326_18520_S.s92p` | 230,131,212 | `7FED6F25A6321B628166C09E533FB1C35E66E43994E1739809ABBC9774E3C2CE` |
| P4 | controlled perturbation: 260808 base | `D:\Downloads\S4LB002-2Para_260808_1_injected (2).spd` | 1,207,304,655 | `CEEEF0D7377556912513C4EB678E4602DE41B25C78619F140221FC52A2D40042` |
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

아래 수치는 raw record count이며 solver가 보존한 node count가 아니다.

| Pair | conductor shapes | total thickness rows | Node | Trace | Via | PadStackDef | PadDef | Part/PartialCkt | Component/Connect |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P1 | 24 | 47 | 143,406 | 12,544 | 109,203 | 10 | 129 | 8 | 3,137 |
| P2 | 78 | 155 | 1,271,131 | 15,052 | 1,193,766 | 40 | 1,599 | 69 | 7,306 |
| P3 | 48 | 95 | 2,934,889 | 1,451,285 | 1,956,937 | 98 | 187 | 10 | 10,727 |
| P4 | 48 | 95 | 2,934,791 | 1,451,209 | 1,956,909 | 98 | 187 | 10 | 10,727 |

P3와 P4는 layer name/order, shape/pad/component aggregate가 같고 P3가 Node 98, Trace 76, Via 28개 더 많다. 이는 좋은 controlled perturbation 후보지만, 실제 semantic difference가 의도한 plane modification뿐인지는 아직 증명되지 않았다.

## Reference-only observations

### P2: small four-port canonical case

Production Touchstone reader와 S→Z converter를 그대로 사용한 결과:

- max `cond(I-S)`: 1.75896
- max relative conversion residual: 3.54e-16
- max Z reciprocity relative norm: 1.67e-17
- diagonal |Z| at 100 kHz: 64.04–65.80 mΩ
- diagonal |Z| at 1 MHz: 5.91–6.03 mΩ
- diagonal |Z| at 10 MHz: 6.00–7.34 mΩ
- diagonal |Z| at 100 MHz: 76.24–94.58 mΩ

P2는 port 수는 작지만 78 conductor layer와 약 119만 raw via를 갖는다. 따라서 외부 port 수보다 내부 topology가 compile/factor 비용을 지배하는지를 시험하는 좋은 최소 case다.

### P3/P4: controlled differential evidence

두 reference는 차원과 grid가 같지만 첫 DC row부터 값이 다르므로 별개의 simulation이다. 정규화된 physical port order는 일치한다.

| group | metric: median absolute ΔdB P3 vs P4 | 100 kHz | 1 MHz | 10 MHz | 100 MHz |
|---|---|---:|---:|---:|---:|
| non-VQPS 82 ports | median | 0.0010 | 0.0037 | 0.0055 | 0.1000 |
| non-VQPS 82 ports | p95 | 0.0030 | 0.0313 | 0.0768 | 2.0245 |
| VQPS 10 ports | median | 7.097 | 7.098 | 7.144 | 12.684 |
| VQPS 10 ports | maximum | 14.906 | 14.907 | 14.992 | 27.469 |

변화가 VQPS group에 집중되고 다른 port의 low/mid-band는 대체로 안정적이다. 따라서 absolute curve뿐 아니라 `predicted ΔZ`가 PowerSI의 `ΔZ`를 재현하는지를 보는 counterfactual benchmark로 사용한다. 두 파일은 너무 유사하므로 서로를 독립적인 일반화 holdout으로 계산하지 않는다.

Reference-only transform 진단은 P3 max condition 7.41e5/residual 6.92e-16, P4 max condition 1.32e6/residual 7.58e-16이었다. 이 조건수는 record별 비교 domain 선택과 error interpretation에 반드시 포함한다.

### P1: reserved holdout policy

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
| P2 | canonical development 및 빠른 전체 multiport 검사 | 가능 |
| P3/P4 | paired perturbation/counterfactual, 물리 ablation | 가능하되 독립 holdout으로 집계 금지 |
| P1 | transfer/scale reserved holdout | 후보 동결 전 curve 기반 조정 금지 |
| future P5 | final blind validation | code/parameter/threshold/manifest 동결 후 1회 평가 |

모든 실험은 입력 hash, port manifest version, solver profile, material source, component state, frequency set과 metric policy를 함께 기록한다.

## 아직 필요한 PowerSI provenance

- PowerSI 정확한 version/build
- adaptive mesh와 convergence tolerance/order
- 3D EM 또는 quasi-static option과 solver mode
- dielectric dispersion, conductor roughness/skin model
- component enabled/disabled state와 model source
- port geometry, reference conductor, de-embedding/renormalization
- DC row의 생성/해석 규칙
- P3/P4가 같은 설정과 component state로 생성됐다는 증거

이 정보가 없으면 PowerSI curve는 유용한 target reference이지만 절대 오차 floor나 mesh convergence를 규정할 수 없다.
