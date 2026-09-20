# SPD Decap PI Evaluator v0.23.1 — Astra Step 3 source candidate

기준: 2026-09-06, branch `codex/astra-evaluation-resume-20260906`, 시작 HEAD
`e2f219e71d8c8a397009f72242cce10d78cfc7ab`. 대상은 A1의
`ADC_VDD_180_VQPS_SYS_1_AON/0`, L30/L29 한 쌍이다. 기존 sealed 기록과 HQ의 bounded
indexed query만 사용했다. raw SPD 재scan, 전체 scenario load, 38,920-contact condensation,
PowerSI fit, 새 schema/backend와 제품 `src` 변경은 수행하지 않았다.

## 판정

`PARTIAL_POWER_AND_GROUND_CONTACT_CHAINS / STOP_PAIRED_SOURCE_COMPOSITION / STOP_LOCAL_SHEET_MESH`

실제 AON TOP launch의 trace/via record chain과 L21 same-net copper-contact bridge,
그리고 L30 방향 record chain은 확인됐다. 이는 `SOURCE_COPPER_CONTACT_ONLY`이며 via
barrel/plating continuity 또는 전체 physical path proof가 아니다. 대응 GND pad도 L18/L20
same-net copper contact까지 확인됐지만 L29까지의 physical traversal은 아직 도출되지 않았고,
TOP power/GND pad의 근접성은 return 선택
권한이 아니다. D096의 reduced closure/owner ledger는 과거 D104가 지목한 17dt bundle의
raw-source discovery index와 binding hash가 다르므로 동일 shadow basis의 native G/C 제외
근거로 이식할 수 없다. 따라서 기존 paired `SeriesBranchBlock`을 실제
P/G source port에 연결하는 single-frequency shadow replacement는 아직 실행 대상이 아니다.

별도의 실제 L21 local-sheet witness는 coarse DC 결과를 냈지만 refinement에서 기존 mesh
guard가 `MESH_CROSSES_VOID`로 멈췄다. 이는 전체 power path 또는 물리 단선 판정이 아니며,
GND return이나 capacitive closure도 검증하지 않는다.

## 직접 확인한 D096 기록과 basis 제한

Sol은 다음 D096 파일의 크기와 SHA-256을 직접 확인하고 17,236-byte JSON을 읽었다.

`D:\SPD-Decap-PI-Evaluator-W7\23e5d3c6b43064b8fd805c234da5f0ccc86b6d4f\260729-a2-d096-source-block-census-01\source_block_census_report.json`

- SHA-256: `bb2ad70bbbb5e39af6a673d29adb2e5543675e680d68491cf906aabc2c16f473`
- source SHA-256: `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2`
- selected pair: `Signal$L30(OTHER_POWER1)` / `Signal$L29(DGND)`
- old D096 closure: power island `spd-surface-island:cb8510a79529b7f6f4f4afd4`, reduced
  2859; ground island `spd-surface-island:2db099ba622781734a17c3e0`, reduced 2854
- candidate native G/C: partial ordinal 18, fingerprint
  `a96399ee2022af38d4060b5b66a1d1c246c4a9fead3ff35737857055a832a41d`,
  capacitance hex `0x1.7613d08aa1b9dp-32`
- numeric actions: candidate 1, retained 9, excluded 0
- candidate owner scopes: `source-plane-owner:ADC_VDD_180_VQPS_SYS_1_AON/0:power`
  및 `:ground`; ledger SHA-256
  `425777101d113e8d66dffc9f423a50641ab6536bd8b4792981464a7cbca9f54e`
- owner ledger retained owner count: 38,926; numeric adjacent partial fingerprint는 후보 1개와
  retained 9개
- 상태: `shadow_only=true`, `replacement_ready=false`, `production_ready=false`

D096 재료는 source-derived다. L29/L30은 각 20 um COPPER, conductivity
`5.959e7 S/m`; 사이 `Medium$DR2930`은 30 um ABF-GL102다. 1 MHz source point는
Dk 3.4, Df 0.0041이고 이후 source frequency row도 보존돼 있다. HQ가 sealed 94-byte
COPPER record를 별도 확인해 20 C의 `5.959000e+07 S/m`를 재검증했다. 이는 D096에 없던
값의 발견이나 기존 product default 오류가 아니라 독립 identity 확인이다. L21은 별도
source layer thickness 35 um이며 같은 COPPER record를 사용한다. 다른 온도 동작은
도출하지 않는다.

HQ가 복원한 raw-spatial DB는 과거 D104가 지목한 17dt bundle의 source discovery index다.
live/current product normalized basis로 확인된 artifact가 아니다. source SHA는 같지만
project, compiled-topology, ownership-certificate, raw-geometry, logical-row와 plane-sheet
binding이 D096 query key와 다르다. 예를 들어 D096 compiled topology는
`0e242c069a55707b554eb632ea7a0747a29b6ad1451453c6496e8dd4cc1ac8cf`인 반면 복원 DB
meta는 `a12a76a1...` 계열이다. 따라서 D096의 reduced 2854/2859와 ledger는 source 사실을
설명할 수는 있어도 복원 discovery index와 직접 조합할 exact exclusion token이 아니다.
17dt bundle manifest의 비-geometry attachment 7개에는 source-plane ownership IR member가
없고, compiled-topology table의 node/link/owner/port만으로 numeric G/C를 재구성할 수도 없다.

## D104 whole-island geometry

Sol의 D104 직접 read는 sandbox에서 거부됐고, precise read-only escalation은 turn
interrupt로 완료되지 않았다. 아래 D104 사실은 Astra HQ가 receipt/hash와 지목 파일만
별도로 검증한 결과다.

- receipt: 19,728 bytes, SHA-256
  `bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b`
- L29 DGND `cell_0259.wkb`: D096과 같은 island `2db099ba622781734a17c3e0`,
  2,040,201 bytes, 4,679 holes, SHA-256
  `a438379bf22b47e412b763eebb49c3aae7252c5872d5ac7c31513499108b9053`
- L30 AON power `cell_0264.wkb`: 258,181 bytes, 670 holes, SHA-256
  `a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5`
- power island은 D096과 같은 `cb8510a79529b7f6f4f4afd4`이며 bbox는
  `[-40600, 125, -10765, 13435] um`, area `361720074.03947 um^2`다.

이 geometry들은 약 30 x 13 mm power island와 더 큰 DGND whole island다. receipt의
`16 cells`는 numerical mesh cell 수나 작은 launch 후보 수가 아니다. whole-island WKB를
finite terminal footprint로 사용하는 것은 금지한다.

## 실제 launch와 power bridge

HQ는 D104 bundle의 기존 compressed raw-v3 SQLite member만 byte-identical하게 ignored
output으로 복원했다. 새 DB/schema/import가 아니다. compressed member는 680,683,143 bytes,
SHA-256 `c5f7085edcf9d0e638f01602633f9df158472eb8e2959989d9fa7693e504947a`;
decoded DB는 2,752,458,752 bytes, SHA-256
`6f5532ccb1d0cde4b62984e70c49ac69c7d171bc7585d908cdf23240dce1cfeb`다.
복원은 10.281 s였다. DB는 read-only immutable URI, query-only/trusted-schema-off와 bounded
SQL progress handler로 조회했다.

root-owned [selected query](../../outputs/research/astra-step3-source-index-01/selected-launches.json)는
0.094 s에 AON 266 nodes, 144 traces, 209 vias만 처리했다. 실제 TOP DUT pad는 직경
100 um, rotation 0이며 다음 세 개다.

| node | x (um) | y (um) |
|---:|---:|---:|
| 19551 | -11726.9 | 12495.1 |
| 19552 | -11596.9 | 12495.1 |
| 19553 | -11466.9 | 12495.1 |

가까운 L30 AON node는 2140544 `(-11750,12503) um`, 2140545
`(-11650,12503) um`다. 좌표가 가깝다는 이유만으로 nearest-node bind하지 않았다.
가까운 GND DUT pad와의 center spacing은 `130.01446 um`, pad edge gap은
`30.01446 um`이지만 이것도 proximity 진단일 뿐 physical return 선택이 아니다.

explicit trace/via BFS만 사용하면 TOP 세 pad에서 76 nodes, 최대 L21까지 도달하고 L30
node나 L30 통과 via에는 닿지 않았다. 이 결과는 plane contact를 graph edge로 포함하지
않은 한계였으며 open-circuit 증거가 아니다. 실제 L21 검사는 누락된 bridge를 찾았다.

- L21 AON copper primitive 109688: 8-vertex positive polygon,
  `geometry/0219-b0ab5dda526415e8.spdgeom.zlib`
- TOP에서 닿는 L21 nodes 2140547/2140578/2140579는 `DR-2021_60`으로 L20→L21
- 같은 polygon 안의 node 2140546은 Via336239 `DR-2128_350`으로 L21→L28
- 이후 exact chain:
  Via336239 → Trace311305 → Via336237 → Trace311298 → Via336236 → L30 Node2140545

[L21 bridge receipt](../../outputs/research/astra-step3-source-index-01/l21-plane-bridge.json)의
bounded containment check는 0.078 s였다. 네 pad disk가 모두 8-vertex copper 안에 완전히
들어가며 boundary margin은 각각 `24.96/5.16/13.36/13.36 um`다. 이로써 AON power의
TOP 쪽과 L30 쪽 trace/via record chain 사이에 들어가는 L21 same-net copper 접촉 후보가
기하적으로 확인됐다. receipt 자체도 zero resistance, complete via/material proof와 전체
physical path를 명시적으로 부정한다. padstack/via record의 존재를 실제 barrel 도금 및 모든
layer 접속의 완결 증명으로 확장하지 않는다.

## GND와 paired-port 비호환 상태

기존 WP3 certificate의 두 인접 GND site는 이번 raw query의 GND pad와 일치한다.

- `SITE0:20612`: Node73624 TOP `(-11.5319,12.6077) mm`, 마지막 확인 node는 L18
  Node2452693 `(-11531900000,12547700000) pm`, 20 edges; endpoint Via1360614,
  padstack `DR-1718_60`
- `SITE0:19973`: Node79348 TOP `(-11.6619,12.6077) mm`, 마지막 확인 node는 L20
  Node2543231 `(-11661900000,12547700000) pm`, 22 edges; endpoint Via1468539,
  padstack `DR-1920_60`

둘 다 기존 certificate에서는 `physical_traversal_proven=false`,
`l29_l30_physical_pad_proven=false`, `CANNOT_DERIVE`였다. 이를 물리 단선으로 해석하지
않았다. HQ가 exact source member를 로컬 materialize한 뒤 Luna가 각 frontier의 현재 층
same-net copper contact를 추가 확인했다.

[GND membership receipt](../../outputs/research/astra-step3-source-index-01/ground-source-01/ground-contact-membership.json)은
706,078 bytes, SHA-256
`ebd82d0416da2cb2a2d566b5c88e4df52161d997a75b93f24b22ae0026f03ae4`다.
8,076-byte `inputs.json` SHA-256은
`4f849763518eebaea3603359659b8b7ff0e11bf80046f11a42400a0574563b5b`다.

- L18 member SHA-256 `e7434a66217be7fd8238635022b79a03dc6395921cd4b377a2bdfe79267f1c21`,
  decoded 7,339,782 bytes. Primitive order 5,542개는 positive polygon 13,
  negative circle 1,084, negative polygon 4,445와 정확히 일치한다.
- L20 member SHA-256 `e0660ac40d99091f6c230fd72316e07437c08e3fbb5f9e3e44af1cd4f73b96f6`,
  decoded 3,267,411 bytes. Primitive order 4,520개는 positive polygon 2,
  negative circle 192, negative polygon 4,326과 정확히 일치한다.
- 두 source order는 interleaving을 포함해 순서대로 적용됐다. 두 pad는 직경 60 um이며
  center-to-boundary는 각각 `70.1020113834 um`, disk margin은
  `40.1020113834 um`다. 둘 다 entire disk가 해당 층 DGND copper 안에 있다.

최종 계산은 내부 0.703 s, shell 0.9372621 s였다. 앞선 계산 4.0705848 s는 contact
결과까지 성공한 뒤 잘못된 `newline` 인자로 serialization만 실패했고, 생성된 0-byte
placeholder는 제거했다. 최종 receipt는 exclusive write와 `allow_nan=false`로 기록했다.
Terra의 read-only review도 manifest/source hash chain, primitive count/order와 margin 산술의
내부 모순을 찾지 않았다. Synthetic hole/outer-copper boolean check는 최소 판별력만 가지며
개별 hole ID/좌표를 독립 재구성하는 증거는 아니다.

이 판정은 `ACCEPT_LOCAL_SOURCE_COPPER_CONTACT_ONLY`다. Via barrel/plating, L18/L20 이후
L29까지의 path, ground-return 선택이나 current sharing을 증명하지 않는다.

[재현 script](../../tools/research/query_astra_ground_contacts.py)는 같은 두 compressed
member와 `inputs.json`으로 이 충분조건 witness를 다시 만들 수 있게 보존했다. SHA-256은
`fe48b0a490493d60db318880f79a59b566d951200991471442f90ba60c77f7b8`다. 하나의 positive
primitive가 full disk를 덮고 모든 ordered negative가 disk와 비접촉이라는 보수적 충분조건만
판정하며, 여러 positive primitive의 합집합을 완전 판정한다고 주장하지 않는다. Format v1,
`_um` contract, via/padstack/end-node와 attachment/artwork association, hash/size, 최대 두
endpoint, runtime과 output overwrite를 fail-closed한다.

Terra가 처음 찾은 negative-circle radius 이중 차감은 script에서 center-to-boundary와
disk clearance를 분리해 고쳤다. 기존 receipt는 polygon boundary가 최단이라 수치 영향이
없었다. Writer나 receipt를 다시 실행하지 않고 `build_result`만 한 번 메모리에서 확인했고,
두 margin과 ACCEPT가 그대로였다(`EXIT=0`, 내부 0.6400000 s, shell 0.8432053 s).
최종 Terra 정적 재검토는 false ACCEPT 경로를 찾지 않았고 script를 ACCEPT했다. Historic
JSON과 byte-exact schema 재현은 아니지만 두 contact의 의미적 판정은 재현한다.

## Source-via mutual coefficient diagnostic

HQ의 [source-via mutual probe](../../tools/research/probe_astra_source_via_mutual.py)와
[JSON](astra_source_via_mutual_2026-09-06.json)은 actual AON Via336239과 source에 존재하는
세 DGND via의 free-space filament mutual coefficient만 계산한다. 네 via는 모두 straight
L21→L28 source axis이며 DGND examples는 Via1468147, Via1497554, Via937851이다.

15개 source stackup thickness의 합은 912 um이다. 첫/마지막 35 um foil의 endpoint convention에
따라 inner-face 길이 842 um, foil-midplane 길이 877 um, outer-face 길이 912 um을 각각
기록했다. Source axis spacing과 midplane coefficient는 다음과 같다.

| DGND via | spacing | midplane mutual |
|---|---:|---:|
| Via1468147 | 3.25 mm | 23.52494246 pH |
| Via1497554 | 4.110960958 mm | 18.63922047 pH |
| Via937851 | 4.110960958 mm | 18.63922047 pH |

기존 private Neumann kernel과 독립 32 x 32 Gauss quadrature의 최대 상대차는
`3.99e-15`, runtime은 0.015 s였다. Terra는 source span/interval/distance/coefficient 사이
모순을 찾지 않았다. 다만 private API와 세 parallel-filament geometry의 수치 확인일 뿐
kernel 안정성이나 다른 geometry를 검증하지 않는다.

이 값들은 GND via를 return으로 선택하거나 current sharing을 정하지 않는다. Radius,
plating/solid-fill과 R, self/internal L, plane screening, G/C, owner replacement와 source
closure도 포함하지 않는다. 따라서 complete return network나 Evaluation 개선값으로 쓰지
않고, 향후 실제 return topology가 닫힐 때 사용할 partial coefficient evidence로만 보존한다.

따라서 실제 P/G terminal은 같은 raster column의 canonical pair로 입증되지 않았다.
서로 다른 실제 pad를 co-locate하거나 virtual ground를 만들거나, GND를 ideal short하거나,
가까운 mesh node로 snap해 통과시키면 안 된다. FastHenry류 RL oracle도 실제
좌표/footprint를 보존해야 하며 nearest reference-plane node alias를 거절해야 한다.
또한 RL만으로 bare PDN의 capacitive return을 만들 수 없으므로 source G/C와 absolute KCL
소유권을 분리해 결합해야 한다.

## L21 local-sheet witness

네 실제 L21 pad 중 Node2140578과 Node2140579의 center separation은 정확히 140 um이고
두 radius의 합도 70+70 um라 서로 접한다. 기존 `tri_fem_sheet`는 touching equipotential
finite contacts를 거절한다. pad를 줄이거나 서로 다른 owner를 합쳐 성공시키지 않았다.

HQ root-owned [probe](../../tools/research/probe_astra_l21_sheet.py)와
[JSON](astra_l21_sheet_2026-09-06.json)은 네 pad full case를 expected input STOP으로 보존하고,
서로 떨어진 실제 Node2140547↔Node2140546 두 pad만 isolated lateral DC sheet subproblem으로
검사했다. 다른 두 pad는 equipotential contraction하지 않았으며 GND return/full-board
모델이라고 주장하지 않는다.

Tangency는 원래 integer-pm 원반으로 판정했다. FEM electrode는 exact curved disk가 아니라
64-side inscribed polygon이고 기록된 최대 sagitta는 `0.210795164 um`다. 따라서 아래 저항은
두 source pad의 근사 equipotential electrode 사이 값이며 via barrel/plating/contact R도
포함하지 않는다.

- refinement level 0: 136 nodes, 262 triangles, DC resistance
  `22.2576227987 micro-ohm`
- refinement level 1: `MESH_CROSSES_VOID`
- 전체 numerical work: 0.109 s; 최초 실패를 포함한 shell 관측은 1.54 s
- overall: `STOP_LOCAL_SHEET_MESH`; convergence unknown

coarse 값은 `b.T @ pinv(Y) @ b`로 얻은 L21 lateral DC sheet 진단일 뿐 calibrated source
path R이나 승인된 sheet impedance가 아니다. via/other-layer R, external/mutual L, G/C,
dielectric/return과 ownership replacement를 모두 제외한다. refined triangle/void guard
failure의 원인을 닫기 전 값을 SeriesBranchBlock이나 Evaluation에 넣지 않는다.

HQ의 [mesh boundary diagnosis](astra_l21_mesh_boundary_2026-09-06.json)는 level-1에서
`exactcovers`가 거절한 triangle이 2개임을 고정했다. GEOS 계산 outside area는 0,
maximum vertex distance도 0이고, local-origin 왕복 Hausdorff distance도 0인데 translated
refinement는 계속 실패했다. 이는 실제 void crossing이 입증된 것이 아니라 strict geometry
predicate의 numerical robustness 문제라는 진단이다. core guard를 느슨하게 하거나 shape를
수정하지 않았고, 22.2576 micro-ohm 값을 converged라고 부르지 않는다. return path와 external
L가 정확도에 더 직접적이므로 이 단계에서 local-R mesher 수정으로 우회하지 않는다.

## native G/C와 다음 한 단계

D096은 어떤 native partial이 A1 후보인지 정확히 알려 주지만 복원 discovery index와 같은
binding에서 그 fingerprint/owner를 제외할 수 있음을 증명하지 않는다. 현재 product seam은 supplemental
sheet block을 더하기 전에 native G/C를 stamp하므로 old D096 ledger를 그대로 사용하면
double count 위험이 있다. retained Via/termination ownership과 plane G/C exclusion도 하나의
일치하는 identity basis에서 닫혀야 한다.

다음 한 단계는 새 framework가 아니다. 확인된 L18/L20 contact에서 실제 source
via/plane chain을 따라 L29 closure를 찾고, 동시에 의도한 shadow에 사용할 기존
topology/raw/ownership artifact들이 서로 같은 identity basis인지 확인한다. 일치하는 기존
query seam에서 A1 native partial의 exact fingerprint/ordinal과 retained owner 집합을
결속한다. 이를 위해 새 compiler full run이 필수라고 가정하지 않는다. 두 조건이 닫히고
실제 P/G mapping이 balanced branch contract를 만족할 때만,
1 MHz 한 점에서 native G/C를 정확히 한 번 제외하고 source G/C와 conductor-current RL을
한 번씩 stamp한 shadow solve를 HQ에 제안한다.

GND가 arbitrary separated-terminal 경로로 남으면 paired MFDM 결과를 억지로 이식하지
않는다. 실제 finite-cross-section conductor current와 mutual L을 다루는 bounded 기존-core
경로 또는 PEEC/FastHenry RL oracle을 검토하되, exact port footprint와 disjoint source G/C를
필수 입력으로 둔다. L21 FEM refinement failure는 그 선택 전에 독립적으로 진단한다.

## 실제 실행과 agent 상태

- Sol 직접 실행: D096 exact size/SHA/content read 성공. D104 default read는 access denied;
  precise escalation은 turn interrupt로 완료되지 않아 그 출력은 사용하지 않았다.
- Astra HQ 실행: D104 receipt/WKB identity, byte-identical raw-v3 restoration, bounded selected
  SQL, L21 bridge containment, source-via mutual coefficient와 L21 FEM probe. 각 시간과
  결과는 위에 기록했다.
- Luna: D096 size/SHA 확인 뒤 GND frontier node/via/padstack을 확인했다. 첫 local-only
  시도에는 geometry materialization이 없었지만, HQ가 exact 두 member를 제공한 뒤 source
  order를 보존한 disk membership을 계산하고 위 ignored receipt와 재현 script를 만들었다.
- Terra: D096 전체와 D104 receipt 반환 내용을 독립 검토해 L29/L30 island identity,
  candidate partial ordinal/fingerprint와 old ledger를 확인했다. thin receipt만으로는 launch
  footprint가 없다는 판정은 이후 HQ indexed query가 power 쪽을 진전시켰지만, GND closure와
  same-basis exact exclusion STOP은 유지된다. L21/contact/mutual artifact의 scope와 수치도
  read-only로 교차검토했으며 새 파일은 만들지 않았다.

C1 1800 s STOP, WP2/WP3 PARTIAL과 기존 W6 accuracy FAIL은 변경하지 않는다. 최종
알고리즘 선택과 다음 bounded 실행 판정은 Astra HQ가 담당한다.
