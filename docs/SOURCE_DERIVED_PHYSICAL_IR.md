# SPD Decap PI Evaluator v0.23.1 — Source-derived physical IR

- 문서 버전: **1.2**
- 계약 상태: **ACCEPTED** — source-derived provenance/ownership prerequisite의 기술 기준
- committed 구현: `source-plane-ownership-ir-v1` + Phase 3 shadow consumer + `source-plane-ownership-ir-v2` `contact_boundary` (`3b76af4`)
- runtime acceptance: **Phase 4 focused PASS / Sol ACCEPT / prerequisite-only**
- production 상태: solver, owner-off, `Y_global`, `Zii` **unchanged**
- 현재 작업 상태: **W7-PHYS-PROSPECTIVE-P0 ACTIVE** — contact-to-artwork finite-port admissibility만 판정
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
  L --> M[P0 contact-to-artwork admissibility]
  M -. owner-off/N-port gate 전 연결 금지 .-> K[production Y_global / Zii]
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
| contact boundary (v2) | selected P/G component incident edge, boundary-side Via, endpoint/rotation/pad provenance, Device/decap/other 보강 | `3b76af4` accepted prerequisite; direct artwork/production port 의미는 P0 전 금지 |
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
얻었고 R6 Sol 재검토가 ACCEPT했다. 다음 단일 질문은 모든 v2 contact의 exact footprint가
selected same-net artwork의 물리 patch port로 직접 적격한지다. `W7-PHYS-PROSPECTIVE-P0`는
이를 shadow-only로 판정하며 추정 geometry, schema 확대 또는 production 연결이 필요하면 STOP한다.

## 7. 주장 한계

- Phase 1/2/3/4 PASS는 source identity, ownership, shadow analytic과 finite-boundary
  prerequisite만 증명한다. Phase 4의 historical failure는 해당 과거 fixture 결과에만 적용한다.
- reciprocity, passivity, deterministic replay는 non-regression이며 PowerSI 정확도
  개선 증거가 아니다.
- PowerSI 데이터는 comparison gate에만 사용하고 parameter fitting 입력으로 쓰지
  않는다.
- 17DU/17DV historical STOP은 재실행하거나 성공으로 재분류하지 않는다.
