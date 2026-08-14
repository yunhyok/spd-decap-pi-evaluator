# SPD Decap PI Evaluator v0.22.0 — Bounded Baseline Protocol

최종 갱신: 2026-08-14 (Asia/Seoul)

이 문서는 raw SPD와 PowerSI Touchstone의 비교를 시작하기 전에 실행 가능성, reference 무결성, port 의미, 자원 한계를 분리해 판정하는 프로토콜이다. `passed`라는 단일 상태는 사용하지 않는다. 구조적 완료, reference 품질, 정확성, 성능은 각각 별도 상태로 기록한다.

## 상태 어휘

| 필드 | 허용 상태 | 의미 |
|---|---|---|
| `reference_integrity` | `passed`, `failed` | Touchstone identity, grid, S→Z conditioning/residual, reciprocity/passivity 검사 |
| `spd_import` | `passed`, `blocked_*`, `not_run` | SPD가 명시된 port/rail 계약으로 fail-closed import 되었는지 |
| `frequency_solve` | `passed`, `blocked_*`, `not_run` | 실제 주파수 해석 실행 여부 |
| `correlation` | `complete`, `blocked_*`, `not_run` | 동결된 port manifest와 동일한 complex Z를 비교했는지 |
| `accuracy_promotion` | `passed`, `failed`, `unassessed` | 사전 등록한 정확성 gate 판정 |
| `performance_promotion` | `passed`, `failed`, `unassessed` | 목표 8 GB 노트북에서 peak RSS/wall time gate 판정 |

`reference_integrity: passed`는 계산 알고리즘의 정확성을 뜻하지 않는다. `spd_import: passed`도 solve 또는 correlation 성공을 뜻하지 않는다.

## Pair P2 bounded baseline v1

Pair P2는 외부 port가 네 개라 Touchstone 전체 행렬을 저비용으로 검사할 수 있지만, SPD 자체는 595 MB, 78 conductor layer, 1,193,902 Via-start records를 포함한다. 이 중 1,193,766개가 현행 net-qualified parser grammar와 일치하고 136개는 `net=absent`다. 또한 네 port 사이의 결합이 약하다. 100 kHz–100 MHz의 최대 정규화 transfer coupling은 0.283%, 전체 band 최대는 1.775 GHz에서 6.91%다. 따라서 pair P2는 첫 reference/port-contract case에는 적합하지만 via mutual, same-net spreading, finite-port 상호작용의 첫 물리 귀속 oracle로는 부족하다.

### 현재 상태

```text
schema_version: p2-bounded-baseline-v1
reference_integrity: passed
p2_physical_terminal_reconstruction: passed
p2_loadable_full_port_manifest: blocked_full_ordered_records_not_registered
p2_operator_semantics: blocked_missing_current_reference_deembedding
p2_import: blocked_no_external_port_contract
p2_frequency_solve: not_run
p2_production_correlation: blocked_no_generic_p2_runner
p2_8gb_raw_import: not_authorized_no_memory_preflight
p2_64gb_import_save_only: blocked_no_external_port_contract
accuracy_promotion: unassessed
performance_promotion: unassessed
```

Reference 진단:

- 826 records: DC 1개와 positive-frequency 825개
- max `cond(I-S)`: 1.7589626
- max S→Z relative residual: 3.54e-16
- S reciprocity error: 0; max Z reciprocity relative norm: 1.67e-17
- max S singular value: 0.99991948, passivity margin 8.05e-5
- minimum eigenvalue of `Hermitian(Z)`: +1.554 mΩ
- diagonal `|Z|`: 100 kHz 64.04–65.80 mΩ, 1 MHz 5.906–6.032 mΩ, 10 MHz 6.000–7.343 mΩ, 100 MHz 76.24–94.58 mΩ, 1 GHz 0.231–0.291 Ω, 2 GHz 0.205–0.280 Ω
- maximum off-diagonal `|Zij|`: 100 kHz 0.320 µΩ, 1 MHz 1.290 µΩ, 10 MHz 14.169 µΩ, 100 MHz 177.894 µΩ, 1 GHz 4.207 mΩ, 2 GHz 7.225 mΩ

Port order NE/NW/SE/SW에 대해 정규직교 current basis를 `common=[1,1,1,1]/2`, `E-W=[1,-1,1,-1]/2`, `N-S=[1,1,-1,-1]/2`, `checker=[1,-1,-1,1]/2`로 고정했다. `QᵀZQ`의 diagonal magnitude와 최대 off-diagonal은 다음과 같다.

| frequency | common | E-W | N-S | checker | max modal off-diagonal |
|---:|---:|---:|---:|---:|---:|
| 100 kHz | 64.921 mΩ | 64.921 mΩ | 64.921 mΩ | 64.921 mΩ | 0.879 mΩ |
| 1 MHz | 5.968 mΩ | 5.970 mΩ | 5.968 mΩ | 5.970 mΩ | 0.071 mΩ |
| 10 MHz | 6.620 mΩ | 6.594 mΩ | 6.616 mΩ | 6.590 mΩ | 0.597 mΩ |
| 100 MHz | 87.342 mΩ | 87.015 mΩ | 87.334 mΩ | 87.007 mΩ | 6.530 mΩ |
| 1 GHz | 263.836 mΩ | 256.123 mΩ | 263.438 mΩ | 255.675 mΩ | 29.729 mΩ |
| 2 GHz | 240.969 mΩ | 251.802 mΩ | 242.532 mΩ | 253.396 mΩ | 23.292 mΩ |

이 basis는 보고용 고정 pattern이지 Z의 eigenbasis가 아니다. 특히 high band modal off-diagonal은 self-transition 비대칭도 포함하므로 원 port `Zij`와 구분해 보고한다.

### 실제 import 시도와 차단 원인

64 GiB급 현재 host에서 `--import-save-only`를 시도했다. SPD analysis가 약 48 s, plan 단계가 약 59 s에 도달한 뒤 `SPD_NO_RAILS`로 fail-closed 되었으며 frequency solve와 candidate bundle 생성은 없었다. 실행 중 한 시점의 관측치는 working set 약 1.40 GiB, private memory 약 2.05 GiB였지만 monitor 자체가 실패했으므로 **peak가 아닌 하한 표본**이다. 이 결과를 8 GB 실행 가능성의 증거로 사용하지 않는다.

P2의 PowerSI port component는 `L25P08085A7_LGA`, `StartLayer=Signal$BOTTOM`, `AttachLayer=BottomAir`이고 Part에 `Tags=IO`가 없다. 현재 parser는 tagged IO이면서 `TopAir`인 component만 `DEVICE_BUMP`로 만들고, rail derivation은 해당 PWR bump만 사용한다. 이 때문에 bottom port node가 device launch로 보존되지 않고 rail이 생성되지 않는다. 이는 token parse 오류가 아니라 현재 제품의 top-side Device-launch 범위다.

`BottomAir`만 허용하거나 dummy rail을 주입하는 방식은 금지한다. IO tag, bottom node retention, bottom contact/via path, port polarity, open-circuit multiport 의미가 해결되지 않기 때문이다.

2026-08-14 raw port reconstruction으로 physical mapping 자체는 진전됐다. 각 port는 ordered positive terminal 52개와 공통 ordered GND terminal 6,227개를 가지며, 총 6,435 unique package node가 모두 BOTTOM layer에서 source-incident Via 하나에 연결됨을 증명했다. exact hashes, bbox, record schema와 raw line evidence는 [`P2_EXTERNAL_PORT_SPEC.md`](P2_EXTERNAL_PORT_SPEC.md)에 고정한다. 그러나 current weighting, reference mode/plane, de-embedding과 실제 export branch는 여전히 unknown이므로 import/solve/correlation 상태는 바뀌지 않는다.

## 향후 External PowerSI Port 계약

physical terminal 계약은 [`P2_EXTERNAL_PORT_SPEC.md`](P2_EXTERNAL_PORT_SPEC.md)의 `external-powersi-port-manifest-v1`으로 사전 등록했다. 구현 검토 전에 full ordered records를 fixture로 고정하고 다음 operator field를 source evidence로 채워야 한다.

```text
port_id
reference_header_label
positive: {refdes, pin, net, source_node_id, x_um, y_um, source_layer, padstack}
reference: {refdes, pin, net, source_node_id, x_um, y_um, source_layer, padstack}
side: TOP | BOTTOM
excitation_projection: signed Bp column and voltage-observation row
positive_footprint: polygon/pad set and normalized current-weight vector
reference_footprint: polygon/pad set and normalized current-weight vector
reference_conductor_mode: local return | global conductor | differential terminal set
reference_plane: exact physical/electrical plane identifier
deembedding: method and signed length/delay, or explicit none
renormalization_ohm: scalar or per-port value
source_spd_sha256
selection_origin: explicit manifest
```

필수 fail-closed 규칙:

1. exact refdes/pin/net/node와 SPD hash가 일치해야 한다. fuzzy label 또는 Touchstone 값으로 terminal을 추정하지 않는다.
2. positive/reference terminal은 서로 다르고 유일하며 실제 conductor artwork와 접촉해야 한다.
3. TOP/BOTTOM과 `source_layer`를 그대로 보존하고 임의의 TOP fallback이나 수직 경로를 만들지 않는다.
4. first-via 또는 direct-plane contact가 없거나 여러 개로 모호하면 해당 port를 차단한다.
5. signed excitation/projection operator `Bp`, finite terminal footprint와 current weighting의 합이 exact port convention과 일치해야 한다. label만 같고 operator가 다르면 같은 port로 인정하지 않는다.
6. reference conductor/mode, reference plane, de-embedding과 renormalization이 PowerSI export와 같거나 명시적으로 unknown이어야 한다. unknown인 항목은 accuracy promotion을 차단한다.
7. P2의 NE/NW/SE/SW는 네 개의 독립 port로 유지하고, 다른 port open 조건의 `Zii`/`Zij` 의미를 보존한다.
8. legacy top-attached IO와 editable top decap 동작은 회귀 없이 유지한다.

## 실행 사다리와 자원 gate

각 단계가 통과해야만 다음 단계를 실행한다.

1. **Reference-only**: hash/header/grid/S→Z/invariant만 계산한다.
2. **Metadata preflight**: solve 없이 port manifest, selected net, layer/contact/path를 증명한다.
3. **Import-only**: `frequency_solves_executed=0`을 확인하고 단계별 RSS/time을 기록한다.
4. **One-frequency diagnostic**: 가장 작은 production profile로 assembly/factor 예상치와 실제치를 비교한다.
5. **Sparse anchor solve**: DC 제외 100 kHz, 1/10/100/500 MHz, 1/2 GHz를 실행한다.
6. **Full-grid correlation**: 앞 단계의 정확성·수치·자원 gate 통과 뒤에만 실행한다.

목표 노트북에서는 전체 process tree의 peak working set 목표 4.0 GiB, private/committed bytes 절대 상한 5.0 GiB를 적용한다. preflight는 parser buffer, retained graph, assembly triplet, factor fill, RHS/output, reference matrix, mapped-file residency와 25% safety margin을 합산해야 한다. 추정 상한이 5.0 GiB를 넘거나 근거가 없으면 시작 전에 차단한다.

실측은 parent 하나의 RSS만 기록하지 않는다. parent/child process tree의 working set, private bytes, committed bytes, page faults, mapped-file residency, 실행 전 OS baseline, system commit limit/charge/headroom과 available physical memory를 시간축으로 기록한다. 실행 중 system commit headroom <2.0 GiB 또는 available physical memory <1.5 GiB이면 새 단계 시작을 중단하고 현재 단계도 안전하게 취소한다. 이 수치는 provisional safety floor이며 실제 8 GB Windows target에서 paging 없는 반복 측정으로 재검토한다. workstation 측정은 목표 노트북 성능 승격을 대신하지 않는다.

## 필수 report schema

모든 baseline bundle은 다음을 포함한다.

- exact input path, bytes, SHA-256, mtime, parser/solver commit
- PowerSI build, solver mode, mesh/adaptive settings, material/roughness, reference/de-embedding provenance와 unknown 목록
- port number → exact header → physical terminals → signed excitation/projection, footprint/weight, reference mode/plane, de-embedding/renormalization manifest
- frequency source/selected/discarded counts와 DC 처리
- S→Z condition/residual, reciprocity, passivity, singular/eigen diagnostics
- full complex `Zii`와 `Zij`, common/E–W/N–S/checkerboard current-pattern impedance
- absolute complex error, dB/phase error, transfer coupling, resonance/zero-crossing, matrix/eigenmode metric
- parse/compile/assemble/factor/RHS/postprocess wall time, peak RSS 측정법, fill/iteration/pivot/forward-error surrogate
- 단계별 상태와 차단 코드; 실행하지 않은 항목을 0 또는 pass로 쓰지 않음

다음 P2 작업은 제품 코드 변경이 아니라 PowerSI의 current weighting/reference plane/de-embedding/export branch 증거 확보와 memory preflight 사전 등록이다. operator unknown이 해소되고 구현이 별도 승인되기 전에는 P2 production solve를 다시 실행하지 않는다.
