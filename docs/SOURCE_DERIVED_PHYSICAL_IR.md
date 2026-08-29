# SPD Decap PI Evaluator v0.23.1 — Source-derived physical IR

- 문서 버전: **1.9**
- 계약 상태: **ACCEPTED** — source-derived provenance/ownership prerequisite의 기술 기준
- committed 구현: `source-plane-ownership-ir-v1` + Phase 3 shadow consumer + v2 `contact_boundary` (`3b76af4`) + P0 (`4dc855a`) + P1 (`f823a53`) + P2 (`90f6b54`) + P3 (`b8a79f1`) + P4 base cut-set (`39fd4fa`) + P5 shadow rewire plan (`d7e7278`) + P6 scenario commutation audit (`6793bb2`)
- runtime acceptance: **Phase 4와 P0–P6 focused PASS / Sol ACCEPT; P3 semantic STOP, P4 structural CLOSED, P5 PLANNED, P6 PASSED / prerequisite-only**
- production 상태: solver, owner-off, `Y_global`, `Zii` **unchanged**
- 현재 작업 상태: **W7-PHYS-PROSPECTIVE-P7 ACTIVE** — exact 1 GHz atomic replacement recipe의 read-only audit
- 최종 개정: 2026-08-29 (Asia/Seoul)

## 1. 목적

이 IR의 목적은 원본 SPD에서 PowerSI 근접 `Zii` 계산에 필요한 source identity,
plane geometry lineage, stackup/material provenance, terminal footprint와 solver owner
관계를 한 번의 import에서 보존하는 것이다. IR 자체는 정확도 개선이 아니며,
source-derived 물리식 하나를 중복 stamp 없이 교체하기 위한 전제다.

17DV의 `STOP_NO_AUTHORITATIVE_BRIDGE`는 원본 SPD에 정보가 없음을 뜻하지 않는다.
기존 persisted artifact가 import 중 계산된 관계를 보존하지 못했음을 뜻한다.

## 2. 결정

원본 SPD를 solve 때마다 다시 읽거나 raw-spatial v3를 변경하지 않는다. importer가
source graph와 live artwork island resolver를 동시에 가진 시점에 compact SQLite
draft를 만들고, 기존 raw-v3와 compiled-topology identity가 생성된 뒤 hash binding을
완성한다.

```mermaid
flowchart LR
  A[원본 SPD record/span/hash] --> B[ordered plane primitive]
  B --> C[derived artwork island/component]
  C --> D[Node/Via/terminal footprint]
  D --> E[rail PWR/return binding]
  E --> F[compiler-assigned plane owner]
  F --> G[replacement ledger]
  G --> H[Phase 3 shadow patch witness DONE]
  H --> L[Phase 4 all-contact boundary IR v2 DONE]
  L --> M[P0 contact-to-artwork admissibility DONE]
  M --> N[P1 contact-complete shadow N-port DONE]
  N --> O[P2 old-edge identity bijection DONE]
  O --> P[P3 quotient audit DONE / RANK LOSS STOP]
  P --> Q[P4 closed base cut-set DONE]
  Q --> R[P5 shadow contact rewire plan DONE]
  R --> S[P6 scenario commutation audit DONE]
  S --> T[P7 one-frequency atomic recipe audit ACTIVE]
  T -. production seam 전 연결 금지 .-> K[production Y_global / Zii]
  I[raw-v3 geometry/material] -. hash reference .-> B
  J[compiled finite topology] -. hash/owner reference .-> D
```

IR에는 polygon vertex, WKB, 원본 bytes를 복제하지 않는다. geometry와 finite
topology는 기존 hash-bound asset을 참조한다. SPD에 존재하지 않는 legacy solver
owner 또는 PowerSI 내부 mesh/object ID를 source-authored 값으로 주장하지 않는다.
plane owner는 source identity에 결속해 importer/compiler가 결정적으로 부여한다.

## 3. Canonical relation

| 영역 | 보존 대상 | 핵심 경계 |
|---|---|---|
| source | record kind/order, byte span, exact-record SHA | source file SHA와 범위 검증 |
| plane | surface, ordered primitive, polarity/kind, raw-v3 primitive identity | primitive와 island는 일대일이라고 가정하지 않음 |
| island | derived island/component identity와 primitive witness | positive/negative Boolean lineage는 다대다 |
| material | stackup thickness/conductivity, Dk/Df 각 값의 origin과 exact source record | Layer/Material 출처를 필드별로 구분 |
| rail | logical NET, artwork NET, layer, PWR/return surface | layer display token을 NET으로 사용하지 않음 |
| terminal | branch/pin→Node→Via→finite vertex/edge→exact rail island→PadDef+Regular footprint | 전체 chain이 있어야 complete |
| ownership | retained Via/device/terminal owner와 declared plane owner | namespace disjoint, exact-once |
| contact boundary (v2) | selected P/G component incident edge, boundary-side Via, endpoint/rotation/pad provenance, Device/decap/other 보강 | `3b76af4` accepted prerequisite |
| contact admissibility (P0) | v2 contact exact footprint와 selected same-net ordered artwork의 direct full coverage | `4dc855a` accepted shadow prerequisite |
| contact N-port (P1) | P0 ordered contact 전부의 1 GHz finite-port admittance, constraint, diagnostics와 input identity | `f823a53` accepted shadow prerequisite |
| owner-off candidate audit (P2) | P1/raw/substrate identity, contact↔finite-link↔production-port chain, selected incident old-edge fingerprint closed set | `90f6b54` accepted shadow prerequisite; `replacement_ready=false` |
| quotient representability (P3) | N-contact admittance가 현 PWR/GND ideal quotient에서 보존되는지의 projector residual | `b8a79f1` DONE/STOP; `CONTACT_INTERFACE_RANK_LOSS` |
| closed base cut-set (P4) | selected ideal class의 full preimage와 contact/old-Maxwell 외부 adjacency exact closure | `39fd4fa` DONE/CLOSED; `split_ready=false` |
| shadow contact rewire plan (P5) | contact별 interface node, retained finite-link rewire, P2 disable set과 P1 stamp identity | `d7e7278` DONE/PLANNED; plan-only, production topology/stamp 불변 |
| scenario commutation audit (P6) | P5 contact edge와 scenario/termination bound network의 exact structural compatibility | `6793bb2` DONE/PASSED; read-only, production topology/stamp 불변 |
| atomic replacement recipe (P7) | exact 1 GHz old-Maxwell 제거, finite-link rewire와 P1 N-port 추가의 no-double-counting ledger | ACTIVE; read-only, production topology/stamp 불변 |
| replacement | replaced/retained set hash와 상태 | Phase 1은 `prerequisite_only`만 허용 |

## 4. 불변조건

1. 모든 source span은 source size 안에 있고 exact record hash와 일치한다.
2. retained primitive는 surface별 source order와 raw-v3 ordinal에 정확히 한 번 나타난다.
3. island는 surface/component 하나에 속하고 positive primitive witness를 하나 이상 가진다.
4. primitive↔island lineage는 다대다이며, 사라진 primitive는 명시적 no-survivor 상태다.
5. logical rail NET, artwork NET, layer display name은 별도 필드다.
6. material 값마다 source, layer override, material model 또는 unavailable origin과
   그 값의 exact source record를 함께 보존한다. unavailable conductivity는 값과
   source reference가 모두 없다.
7. complete terminal은 rail→pin→Node→Via→finite edge/vertex→rail-bound island→
   PadDef+Regular→raw pad footprint가 완전하다. 불완전한 selected rail은 IR 없이 STOP한다.
8. compiler plane owner는 retained owner namespace와 충돌하지 않는다.
9. `replacement_ready`는 실제 consumer가 동일 owner를 운반하고 owner conservation을
   통과하기 전에는 기록할 수 없다.
10. 모든 table은 row count와 canonical logical SHA를 가진다.

## 5. 생성 수명주기

1. 기존 SPD parser가 필요한 source record span과 hash를 수집한다.
2. connectivity recovery 후 live artwork가 유지되는 동안 primitive/island 관계와
   rail/terminal draft를 만든다.
3. live Shapely geometry를 정상 해제한다.
4. 기존 raw-v3와 compiled topology를 변경 없이 생성한다.
5. draft를 source/project/certificate/topology/raw identity에 결속하고 SQLite를 한 번
   압축해 scenario attachment로 저장한다.
6. 실패하면 draft와 임시 DB를 폐기하고 부분 attachment를 남기지 않는다.

## 6. 단계와 검증 예산

| Phase | 구현 위치 | 증거 | 현재 판정 | production 의미 |
|---|---|---|---|---|
| 1 | committed `82370b6` | focused storage contract PASS | DONE | source/provenance storage prerequisite만 |
| 2 | committed `75ac0a0` | focused end-to-end producer PASS | DONE | import-time atomic binding만 |
| 3 | committed `5d2c353` | analytic/deterministic shadow gate PASS | DONE | `Y_global`/`Zii` 미연결 |
| 4 | committed `3b76af4` | focused `1 passed in 1.39s`; Sol ACCEPT | DONE | finite boundary provenance prerequisite만; 정확도 주장 금지 |
| P0 | committed `4dc855a` | focused `1 passed in 1.51s`; Sol ACCEPT | DONE | direct artwork full-coverage prerequisite만; production 연결 없음 |
| P1 | committed `f823a53` | focused `1 passed in 1.58s`; Sol identity review 반영 | DONE | exact 1 GHz shadow N-port만; production owner-off 없음 |
| P2 | committed `90f6b54` | 최초 contract FAIL 뒤 fixture 유지·identity 교정; focused `1 passed in 1.49s`; Sol ACCEPT | DONE | candidate old-edge closed set만; `replacement_ready=false` |
| P3 | committed `b8a79f1` | focused `1 passed in 1.51s`; Sol ACCEPT; semantic STOP | DONE | `CONTACT_INTERFACE_RANK_LOSS`; topology/production 불변 |
| P4 | committed `39fd4fa` | 최초 negative fixture contract FAIL 뒤 corrected focused `1 passed in 1.53s`; Sol ACCEPT | DONE | base cut-set CLOSED; `split_ready=false`; production 불변 |
| P5 | committed `d7e7278` | focused `1 passed in 1.73s`; Sol ACCEPT | DONE | deterministic plan-only; topology/stamp/solve 불변 |
| P6 | committed `6793bb2` | final focused `1 passed in 1.42s`; Sol ACCEPT | DONE | scenario/termination 구조 호환성만; production 불변 |
| P7 | consumer/test whitelist | one-frequency atomic replacement recipe 지정 node | ACTIVE | read-only; core network/compiler/solver 불변 |

`DONE`은 해당 Phase의 선언 범위가 종료됐다는 뜻이며 current release, production
acceptance 또는 PowerSI 정확성을 뜻하지 않는다. Phase 4의 exact 실행 이력과 검증
예산은 작업 기준 1장과 12.8–12.9가 권위 있다.

### Phase 1 — storage contract

DONE. importer와 solver를 수정하지 않고 deterministic SQLite writer/loader,
schema validation과 synthetic round-trip을 구현했다. 선택 rail 관계 전체에
100,000행 상한을 두고 terminal은 complete-only로 닫았다.

Whitelist:

- `docs/PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md`
- `docs/WORK_EXECUTION_BASELINE.md`
- `docs/SOURCE_DERIVED_PHYSICAL_IR.md`
- `src/spd_decap_pi/source_plane_ownership_ir.py`
- `tests/test_source_plane_ownership_ir.py`

Validation budget:

- V0: `git diff --check`와 문서/schema 정적 확인 1회
- V1: `tests/test_source_plane_ownership_ir.py` 1회
- V2 이상, 원본 SPD, raw-v3 재생성, solver, Touchstone, W6/PowerSI 비교 금지

Closure evidence: `python -m pytest -q tests/test_source_plane_ownership_ir.py`
→ `7 passed in 0.84s`. 이 결과는 storage contract만 검증한다.

### Phase 2 — importer producer seam

DONE. source span 수집, cleanup 전 draft 생성, raw/compiled identity finalization과
scenario envelope 검증을 작업 기준 12.6의 whitelist로 완료했다. 두 full-file
실행에서 공통 guard 3개는 PASS했고 fixture 결함을 순차 수정했다. 이후 단일
end-to-end producer node로 축소해 certificate terminal-owner projection, source size
hand-off와 Pydantic envelope 변환을 바로잡았으며 최종 결과는
`1 passed in 1.39s`다. 이 단계는 `Zii`를 변경하지 않았다.

### Phase 3 — shadow-only source-plane patch consumer

DONE. validated IR/raw-v3에서 source-certified PWR/return surface와 complete
terminal footprint 하나를 exact join해 `source-plane-patch-v1` finite-port shadow
witness를 만든다. 선택 rail 이외의 surface/terminal 중간 데이터는 보존하지 않고,
실제 stack corridor와 dielectric global ordinal 증명에 필요한 raw stackup/dielectric
stream만 전체 순서를 유지한다. production stamp를 교체하지 않았으며 owner
inventory, `Y_global`, `Zii`도 변경하지 않았다.

```mermaid
flowchart LR
  A[source-plane ownership IR] --> C{identity + owner ledger exact?}
  B[raw-spatial v3 geometry] --> C
  C -->|아니오| S[STOP]
  C -->|예| D[ordered surface patch]
  D --> E[Rdc / L / C analytic gate]
  E -->|relative error <= 1e-10| F[shadow finite-port witness]
  F -. production 연결 금지 .-> G[Y_global / Zii]
```

Whitelist와 검증 예산은 작업 기준 12.7이 권위 있다. Phase 3 PASS도 analytic
limiting case와 owner hand-off prerequisite만 증명한다. production replacement,
mesh convergence, causal broadband A/B, PowerSI correlation과 holdout은 별도 단계다.

검증은 첫 focused file에서 identity-tamper가 PASS하고 float canonicalization fixture만
실패해 `1 failed, 1 passed in 1.30s`였다. fixture 수정 뒤 exact happy node는
`1 passed in 0.97s`였고 `Rdc`, `L=mu0*d*ell/w`,
`C=epsilon0*epsilon_r*A/d`의 상대오차 `<=1e-10`과 deterministic replay를 닫았다.

### Phase 4 — contact-complete import-time boundary

DONE / prerequisite-only. Phase 3의 두 Device terminal은 analytic witness에는 충분하지만 production
loaded plane replacement의 경계로는 충분하지 않다. target rail의 P/G anchor가
증명한 두 surface-equivalence component를 먼저 고정하고, finite-via quotient에서 그
component vertex에 incident한 모든 edge와 전체 owner를 권위 inventory로 선택한다.
`terminal_landing_contacts`는 Device/decap 종류 보강에만 사용하며 inventory source로
사용하지 않는다. 같은 raw compiler pass에서 각 boundary Via의 양 endpoint Node와
PadDef/Regular/PadShape source provenance를 결속하고 solve 때 SPD나 network를 다시
스캔하지 않는다.

v2는 v1의 `terminal_bindings` 의미를 유지하고 별도 `contact_boundary` relation을
추가한다. relation은 owner kind, plane/opposite endpoint, rail-bound component와 대표
island, finite vertex/edge와 edge 전체 owner, raw Via rotation과 exact footprint source를
포함한다. quotient의 canonical `(component/island, vertex, edge, owner)` 집합과 persisted
집합이 정확히 같지 않으면 부분 attachment 없이 STOP한다. 공유 Node/PadStack은 정상적인
many-to-one 관계로 허용하되 모든 intermediate와 최종 관계는 기존 100,000행 상한 안에
있어야 한다.

여기서 `complete`는 finite equivalence-boundary의 source/provenance가 완전하다는 뜻이다.
Pad footprint가 selected artwork에 직접 겹치는지와 trace-equivalent contact를 production
patch port로 쓸 수 있는지는 후속 단일 physical gate이며 Phase 4가 미리 주장하지 않는다.

Whitelist, 검증 예산과 STOP 조건은 작업 기준 12.8이 권위 있다. 이 단계에서도
replacement ledger는 `prerequisite_only`이고 current patch consumer, adjacent-gap
partial, solver, `Y_global`과 `Zii`는 바꾸지 않는다.

현재 Phase 4 증거는 다음처럼 분리한다.

| 축 | 현재 사실 | 주장 금지 |
|---|---|---|
| committed 구현 | v2 schema/loader, quotient-authoritative boundary selection, raw endpoint/rotation/pad provenance와 authority coverage가 commit `3b76af4`의 3 production + 1 focused test 파일에 존재 | release 또는 production solver acceptance |
| causal closure | R2 read-only 추적으로 C1의 isolated Node3 때문에 Trace11 quotient union이 생기지 않아 Via11이 leaf-prune된 fixture/acceptance 부정합으로 분류 | production enumerator/join 결함으로 재분류 |
| runtime 증거 | decap Via7/Node8을 복원하고 Trace11을 non-isolated Node1에서 시작한 fixture에서 Via11 `other`, Node11/Node12, 4.5도 rotation/padstack, canonical authority list/set/count/SHA와 atomic failure가 PASS; 최종 `1 passed in 1.39s` | direct artwork coverage, replacement readiness |
| validation hardening | v2 Node/Via/PadDef/Regular identity, net/layer, endpoint alias, normalized rotation을 교차 결속하고 landing enrichment를 `O(B+L)`로 인덱싱; Sol 재검토 ACCEPT | PowerSI accuracy 또는 성능 benchmark |
| production | solver, current patch consumer, owner-off, `Y_global`, `Zii` 변경 없음 | 정확도 개선 또는 PowerSI 상관 개선 |

R4의 첫 검증은 `spd_adapter.py` 들여쓰기 오류로 collection 전에 중단됐고 같은 노드에서
재실행하지 않았다. 별도 R5 문법 교정 뒤 지정 node를 한 번 실행해 `1 passed in 1.39s`를
얻었고 R6 Sol 재검토가 ACCEPT했다.

### P0 — contact-to-artwork finite-port admissibility

DONE / shadow prerequisite-only. `evaluate_source_plane_contact_admissibility()`는 manifest rail,
v2 contact identity, raw endpoint layer와 PadShape provenance를 다시 결속한 뒤 각 exact footprint가
selected same-net ordered artwork에 전면 피복되는지 직접 판정한다. production Phase 3 consumer의
출력과 의미는 바꾸지 않았다.

초기 구현은 irregular artwork를 기존 analytic rectangle 경로에 결합해 `_rectangle()`에서
중단됐으므로 폐기했다. 별도 read-only 판정기로 분리한 뒤 첫 음성 fixture의 Node11을 30 mm로
옮기자 upstream P4가 contact 자체를 제외한다는 원인을 확인했고 재실행하지 않았다. Node11 중심을
PWR 경계 안 3.995 mm에 두되 pad가 경계를 넘도록 고친 단일 node는 `1 passed in 1.52s`였다.
Sol trust-boundary 검토에 따라 manifest target rail 인증과 external source-record layer↔opposite
Via layer 교차 결속을 추가했고 최종 지정 node는 `1 passed in 1.51s`였다. 뒤의 annotation-only
교정은 runtime 의미를 바꾸지 않아 재실행하지 않았으며 Sol이 ACCEPT했다. 기술 commit은
`4dc855a`다.

### P1 — contact-complete shadow N-port condensation

DONE / shadow prerequisite-only. `evaluate_source_plane_contact_condensation()`은 P0 accepted
contact 순서와 `(contact_id, owner_kind)`를 다시 대조하고 selected ordered artwork, IR↔raw
stackup/material provenance와 exact source-tabulated frequency point를 기존 surface-patch
operator에 전달한다. 1 GHz, 1000 um fixed mesh의 지정 node는 모든 `device/decap/other`
contact를 포함한 유한 N×N admittance, terminal constraint, gauge/solve/reciprocity/passivity/
condition diagnostics와 deterministic replay를 `1 passed in 1.58s`로 닫았다.

Sol 정적 검토는 최초 `input_sha256`가 authenticated ownership logical-row identity를 누락해
서로 다른 유효 sidecar가 같은 hash를 만들 수 있다고 REJECT했다. Luna가
`ownership_logical_rows_sha256` 한 필드만 input identity에 추가했고 계산/행렬/port 의미가
바뀌지 않아 node를 재실행하지 않았다. 나머지 항목은 Sol이 ACCEPT했으며 기술 commit은
`f823a53`다.

P2는 P1 N-port가 대체할 production adjacent-gap Maxwell old edge를 exact fingerprint로
전수 식별했다. 그러나 현 production은 각 P/G artwork component를 ideal node 하나로 축약한다.
P3 결과 현 quotient는 contact-space mode를 보존하지 못했고 P4는 contact-interface node 분리의
base-network cut-set 전제를 닫았다. P5는 exact rewire/disable/stamp shadow plan까지 결속했다.
P6는 실제 scenario/termination binding이 그 contact basis를 보존함을 닫았다. 이제 exact
1 GHz에서 production old-Maxwell 계수 제거, finite-link rewire와 P1 N-port 추가가 하나의
atomic no-double-counting recipe로 결속되는지 P7에서 닫기 전에는 owner-off나 production
replacement stamp를 만들지 않는다.

### P2 — incident old-edge identity/bijection

DONE / shadow prerequisite-only. `audit_source_plane_patch_owner_off()`는 P1/raw/ownership/
substrate identity를 결속하고, physical artwork component→contact plane-side quotient vertex→
retained finite R/L edge/owner→external terminal vertex→production port chain을 검증한다.
선택 component incident sparse Maxwell edge를 전수 스캔해 canonical fingerprint closed set을
만들며 production network는 수정하지 않는다.

최초 지정 node는 physical island와 external production port를 동일시한 audit contract 때문에
`1 failed in 1.77s`였다. fixture의 retained Via 경계가 옳아 fixture를 완화하지 않고 위 identity
chain으로 교정했다. 재실행은 `1 passed in 1.49s`, Sol 최종 정적 검토는 ACCEPT였고 기술
commit은 `90f6b54`다. 결과는 계속 `shadow_only=true`, `replacement_ready=false`다.

### P3 — contact quotient representability

DONE / shadow falsification-only. `audit_source_plane_patch_contact_quotient_representability()`은
contact one-hot map `B`, projector `Q = B.T @ diag(1 / contact_count_per_node) @ B`와
`R = Y - Q @ Y @ Q`를 exact P1/P2 identity에 결속했다. 지정 node는 `1 passed in 1.51s`,
Sol 최종 검토는 ACCEPT, 기술 commit은 `b8a79f1`이다. 실행 성공의 semantic result는
`CONTACT_INTERFACE_RANK_LOSS` STOP이며, 현 PWR/GND two-node ideal quotient가 P1의
current-spreading mode를 보존하지 못함을 확정한다. tolerance 완화, contact 병합, topology
분할은 수행하지 않았고 `replacement_ready=false`다.

### P4 — selected base cut-set closure

DONE / shadow structural prerequisite-only. termination/scenario 없는 base
`compile_layerwise_substrate` network에서 role별 full `reduced_node_index()` preimage가 P2
component islands와 P3 contact `finite_vertex_id`의 합집합과 정확히 같은지 판정한다. 이 class에
닿는 ideal link는 같은 role 내부에서 닫혀야 하며, class를 가로지르는 finite link는 P2 contact
edge와, sparse Maxwell adjacency는 P2 incident fingerprint와 각각 exact equality여야 한다.
base port 직접 부착, extra vertex/edge/owner, cross-role ideal link는 STOP한다. PASS여도
`split_ready=false`이며 scenario/termination adjacency는 split 설계 뒤 별도 bound-network gate다.

최초 node는 본체의 정상 `closed` 경로 뒤 negative fixture에 extra `via_links`만 추가하고 compiled
`_finite_links`를 함께 갱신하지 않아 `1 failed in 1.90s`였다. 이는 frozen network constructor가
fixture 불변조건을 차단한 test-contract 오류다. 두 inventory를 함께 구성하도록 fixture만 교정한
재실행은 `1 passed in 1.53s`, Sol 최종 검토는 ACCEPT, 기술 commit은 `39fd4fa`다. 결과는
`status=closed`, `split_ready=false`, production topology/solver/`Zii` 불변이다.

### P5 — shadow contact rewire plan

DONE / plan-only. P4를 한 번 호출하고 내부 P3→P2 chain을 재사용한다. 각 contact에 P4 hash와
contact identity로 unique interface node를 만들고, retained `finite_parallel_rl` link는 selected
endpoint만 그 node로 바꾸는 계획을 만든다. link ID, external endpoint, count, R/L, retained Via
owners는 exact 보존한다. P2 old Maxwell fingerprints 전부를 exact-once disable set으로, P1 ordered
N-port와 matrix/constraint identity를 planned stamp로 결속한다. virtual transform 뒤 old selected
class의 external degree가 0이어야 한다. 지정 node는 `1 passed in 1.73s`, Sol 최종 검토는 ACCEPT,
기술 commit은 `d7e7278`이다. 결과는 `status=planned`, `shadow_only=true`,
`production_ready=false`, `replacement_ready=false`이며 실제 graph/matrix는 수정하지 않는다.

### P6 — shadow rewire–scenario commutation audit

DONE / read-only prerequisite. P5 결과, 동일 base substrate와 기존 compiler가 생성한
`LayerwiseScenarioNetworkBinding`을 입력으로 받는다. scenario identity/plan, surface/link manifest,
termination manifest와 P5 `shadow_split_sha256`를 결속하고, 모든 contact finite edge가 exact-once로
남아 endpoint/mode/count/R/L/owner 순서를 보존하는지 판정한다. scenario topology-only link,
termination, base port 또는 partial이 P5 old selected class와 예정 interface 경계를 우회하면 STOP한다.
source contact가 suppress/retarget되어 owner만 새 route로 옮겨진 경우도 P1 terminal basis가 달라지므로
`SCENARIO_REWIRE_SOURCE_EDGE_SUPPRESSED`로 중단한다. PASS여도 구조적 호환성만 뜻하며 production
topology/owner-off/stamp/solve와 `Zii`는 변경하지 않는다.

최초 지정 node는 `1 passed in 2.43s`였으나 Sol이 surface loop의 O(V×K) lookup과 반복
boundary union을 REJECT했다. lookup/boundary set을 한 번만 만들고 전체 surface tuple 복제를
제거한 뒤 최종 지정 node는 `1 passed in 1.42s`, Sol 재검토는 ACCEPT였다. 기술 commit은
`6793bb2`다. 추가 비용은 `O(V + L + P + T + K)` 시간과
`O(V_selected + K + P + T)` 메모리이며 결과는 `status=passed`, `shadow_only=true`,
`production_ready=false`, `replacement_ready=false`다.

### P7 — one-frequency atomic replacement recipe audit

ACTIVE / read-only stamp prerequisite. accepted P1 patch, P5 rewire plan, P6 commutation result,
동일 substrate/scenario binding과 P1 exact 1 GHz source point만 입력으로 사용한다. P5 disabled
fingerprint마다 scenario partial의 old Maxwell 항을 exact-once 재식별하고 production과 같은
`Dk(f)·(1-j·Df(f))/nominal_Dk` 계수로 `y_old`를 기록한다. retained finite branch는 owner/R/L/count와
old/new admittance를 보존하며, P1 contact 순서와 P5 interface 순서를 exact 결속한다.

출력은 ordered `remove_old_maxwell`, `rewire_finite`, `add_p1_nport` ledger와 deterministic recipe
SHA뿐이다. interpolation, broadband, global matrix와 solver를 만들지 않는다. old edge/source point가
없거나 중복되거나 contact order·owner·algebra가 다르면 STOP한다. PASS여도 현재 source point 한
주파수에서 atomic no-double-counting recipe가 존재한다는 뜻만 가지며 production readiness와
PowerSI 정확성을 주장하지 않는다.

## 7. 주장 한계

- Phase 1/2/3/4와 P0/P1/P2/P3/P4/P5/P6 focused PASS는 source identity, ownership, shadow analytic,
  finite-boundary, direct artwork coverage, one-frequency N-port, 구조적 rank-loss, base cut-set과
  shadow rewire plan 및 scenario commutation prerequisite만 증명한다. P3의 PASS는 함수·판정 계약 실행 성공이고 결과 자체는 STOP이다.
- reciprocity, passivity, deterministic replay는 non-regression이며 PowerSI 정확도
  개선 증거가 아니다.
- PowerSI 데이터는 comparison gate에만 사용하고 parameter fitting 입력으로 쓰지
  않는다.
- 17DU/17DV historical STOP은 재실행하거나 성공으로 재분류하지 않는다.
