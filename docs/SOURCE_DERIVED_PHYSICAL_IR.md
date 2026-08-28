# SPD Decap PI Evaluator v0.23.1 — Source-derived physical IR

- 상태: **ACCEPTED / Phase 1/2/3 DONE; Phase 4 BLOCKED (candidate not accepted)**
- 스키마: `source-plane-ownership-ir-v1` DONE → contact-complete `source-plane-ownership-ir-v2` BLOCKED
- 최종 개정: 2026-08-28 (Asia/Seoul)

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
  H --> L[Phase 4 all-contact boundary IR v2 BLOCKED]
  L -. owner-off/N-port gate 전 연결 금지 .-> K[production Y_global / Zii]
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

BLOCKED. Phase 3의 두 Device terminal은 analytic witness에는 충분하지만 production
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

구현 candidate는 raw Via endpoint/rotation, quotient owner 방향, v1/v2 loader와
edge/owner exact-once까지 Sol 정적 GO를 받았다. 그러나 첫 focused file은 generic Via가
retained quotient에 남지 않아 `3 passed, 1 failed in 2.77s`, 허용된 fixture 수정 뒤
동일 node는 중간 GND-layer 경로가 power anchor representative island를 selected PWR
surface 밖으로 바꿔 `1 failed in 1.42s`로 fail-closed됐다. 작업 기준의 1회 fixture
수정·재실행 예산을 소진했으므로 v2 asset, contact completeness와 Phase 4 PASS를
주장하지 않는다. candidate diff는 승인·커밋된 기준이 아니다.

## 7. 주장 한계

- Phase 1/2/3 PASS와 Phase 4 결과는 source identity, ownership, contact boundary와 shadow analytic prerequisite만 증명한다.
- reciprocity, passivity, deterministic replay는 non-regression이며 PowerSI 정확도
  개선 증거가 아니다.
- PowerSI 데이터는 comparison gate에만 사용하고 parameter fitting 입력으로 쓰지
  않는다.
- 17DU/17DV historical STOP은 재실행하거나 성공으로 재분류하지 않는다.
