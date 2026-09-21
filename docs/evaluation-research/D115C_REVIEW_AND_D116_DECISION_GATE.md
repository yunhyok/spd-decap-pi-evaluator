# SPD Decap PI Evaluator v0.23.1 — D115C 검토 및 D116 결정 게이트

## 현재 결정/상태

**결정: `STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT`. D116와 solver는 실행하지 않았고, 현재 acceptance contract를 변경할 권한도 없다.**

D115C 구현은 소스 로컬 검증을 성공적으로 수행하고, 현재 소스의 여덟 행을 문자 그대로 요구하는 계약과 실제 geometry가 충돌함을 fail-closed로 기록했다. 따라서 이는 코드 실패가 아니라 과학적 모델/승인 계약에 대한 결정 정지다. PowerSI와 비교 가능한 계산 정확성이 1차 목표이고 Distribution 호환성은 2차 목표다.

| 구분 | 판정 |
|---|---|
| 검증된 사실 | D115B ownership materialization, D103/D104 입력 identity, D115C 구현/focused test, 두 번의 production stop, W0 수치와 provenance |
| 주장하지 않음 | PowerSI 정확도, solver/D116 결과, 8-GB 성능, generic four-layer 모델, `c_res` |
| 미결정 | 여덟 행을 D116 acceptance matrix에 어떻게 포함할지, A/F mapping, D115B와 D104의 authority 경계 |
| 권고(승인 아님) | source-local W0는 보존하고, 양의 면적 행만 D116 후보로 삼는 방안을 결정 게이트에 상정 |

## 보존 경계와 실행 범위

- 권위 checkout: `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator`
- `main` HEAD: `e2f219e71d8c8a397009f72242cce10d78cfc7ab`
- 새 D115C code/test/docs를 만들기 전 tracked state는 clean이었다. `accuracy_parse.py`는 사용자 소유로 제외했다.
- 이 문서 작성 외에 code, test, frozen research docs, version, installer, release, git history, remote를 변경하지 않았다.
- D116, 어떤 solver, PowerSI, generic four-layer model, `c_res`도 실행하지 않았다.

## D115B·D103·D104 사실

D115B가 `PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED`로 materialized ownership candidate와 rail/terminal topology를 보존했다. D103은 L29 20 um, DR2930 30 um (`epsilon_r=3.4` at 1 MHz), L30 20 um을 고정한다. D104는 source-local window와 plane WKB geometry를 제공한다. 이 결과는 각각의 입력 사실이며 D116 acceptance authority를 자동으로 부여하지 않는다.

## D115C 구현 및 focused test 증거

검토한 구현은 `tools/research/audit_source_l29_l30_port_window.py`, 테스트는 `tests/test_audit_source_l29_l30_port_window.py`다.

- 구현 SHA-256: `FC18D720ABC6D05D9FB5441DFF7522865ECC79AA7C3AED90204F361516DE5EBF`
- 테스트 SHA-256: `291C32AD4499FA0E5ED99552A3B2C97D4855EEB9394CC44FE52C76866BF43829`
- `python -m py_compile tools/research/audit_source_l29_l30_port_window.py`: 통과
- `python -m pytest -q tests/test_audit_source_l29_l30_port_window.py`: **8 passed** (`0.70 s`)

핵심 수정은 Port block을 개별 `.EndPort`에 의존하지 않고 다음 Port header 직전까지의 next-header-exclusive frame으로 닫는 것이다. 이는 production SPD가 연속 Port header를 가지며 Port별 `.EndPort`가 없고, 마지막 `.EndPort`가 Port92 뒤에만 있는 경우를 다룬다.

## Production attempt 1 — `STOP_PORT_FRAME`

Root: `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-01`

- receipt 없음; `d115c_audit.log` 47 bytes, 직접 SHA-256 `9BBCABBC9446406756E657A80B528FE73DC2C14496CFCB4F8BAD4EE2546D2D2C`
- 원인: 실제 SPD에서 연속 Port header 사이에 per-port `.EndPort`가 없고 Port92 뒤에만 최종 `.EndPort`가 있음
- Port44 시작 byte `1094821801`, Port45 시작 byte `1095248648`
- 직접 source 검증: `[1094821801,1095248648)`, `426847` bytes, `2732` lines, section SHA `d07bd48b81804e8934c9724b179b6a578232c78f88a45d85b8565b1290d154d8`, header SHA `9abbe6226f47ae51fa701d21b29f01f5b99c18e9f48cb712201a207774f905d4`, terminal 수 `+3/-10919`, terminator `next_port_header`
- 이 시도의 transferred hash가 잘못되어 있었으며, 위 corrected direct SHA만 사용한다.

## Production attempt 2 — `STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT`

Root: `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02`

- `d115c_source_local_port_window_receipt.json`: `1,063,056` bytes, SHA-256 `0C8DAED46719B199EE50B1EC9B94DD5CAC0FDEDECBC7B58668B7665B5E9A2A80`
- `d115c_audit.log`: 67 bytes, 직접 재해시 SHA-256 `636D33BC3AA0B4EFBE9FD5A2873EBD847FB15A85B2DFC7D396817BD9787895E`
- coordinator/run observation elapsed: `1077.226 s` (receipt/log에 보존된 field가 아님)
- 상태와 code 모두 `STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT`; 여덟 행을 모두 평가한 뒤 양의 면적이 없는 행 때문에 정지했다.

### W0 exact 결과

Raw bounds는 `[-11996900000, 12112500000, -11196900000, 12877700000]` pm, snapped bounds는 `[-12000000000, 12000000000, -11000000000, 13000000000]` pm이다.

| ordinal | layer/net 요약 | exact intersection |
|---:|---|---:|
| 259 | L29 DGND | nonempty `779005.9836758123 um2` |
| 260 | L30 DDRH/0 | empty |
| 261 | L30 DDRH/1 | empty |
| 262 | L30 DDRL/0 | nonempty `21012.5 um2` |
| 263 | L30 DDRL/1 | empty |
| 264 | L30 target AON/0 | nonempty `926540.7647500002 um2` |
| 265 | L30 AON/1 | empty |
| 266 | L30 DGND | empty |

Active rows는 `[259,262,264]`, empty rows는 `[260,261,263,265,266]`이다. Membership는 `evaluated=true`, `passed=false`다. 위반은 `SITE0:20612`와 `SITE0:19973`의 `TARGET_ISLAND_CONTAINMENT`이며 target ordinal은 259다. 나머지 네 target proof는 true이고, 모든 other-net same-layer non-overlap 검사는 통과했다. `source_local_shadow_only=true`; `solver_executed=false`, `powersi_executed=false`, `generic_four_layer_model=false`, `c_res=false`다. D116은 실행하지 않았다.

이 정지는 literal eight-row coverage를 그대로 적용하면 source geometry와 모순되기 때문이다. 어느 행을 D116에 넣을지 고르는 순간 acceptance semantics가 바뀌므로, 이를 코드 green/red 문제로 우회할 수 없다.

## Evidence ledger

| 항목 | 절대 경로 및 확인값 |
|---|---|
| D115B receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\d115b_source_plane_ownership_materialization_receipt.json`; 514208 bytes; SHA `C69DCE134CA02FF263B75F5DF930106A7D8D75D05CCCF2ACD13B7D9B1919AA49` |
| D115B candidate | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\source_plane_ownership_candidate.spdpi`; 925278361 bytes; SHA `1ECC6CBD18A5234178C98C8EAD79F684C278DAF21BDB64A789296DD6543CD7BC` |
| production source | `D:\S4LB002-2Para_260729_1_injected.spd`; 1116717287 bytes; known SHA `40CB44B2376F59D6B606EB9B4D138204FE51B2DC6B3332D3B7C0E7D4202866D2`; multi-GB hash 재계산 안 함 |
| D103 receipt | `D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json`; 204735 bytes; SHA `4EA63CF86F6B1F4E8D56EEAD82033606CD2A2F9B6FAE1B14E857A51E4C0749F9` |
| D104 receipt | `D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01\geometry_receipt.json`; 19728 bytes; SHA `BF3D965281F3FD09CF6BE26CBD49CCA22B4B3085F265BA859349E8091754263B` |
| attempt 1 log | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-01\d115c_audit.log`; 47 bytes; SHA `9BBCABBC9446406756E657A80B528FE73DC2C14496CFCB4F8BAD4EE2546D2D2C` |
| attempt 2 receipt/log | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02\d115c_source_local_port_window_receipt.json` 1063056 bytes, SHA `0C8DAED46719B199EE50B1EC9B94DD5CAC0FDEDECBC7B58668B7665B5E9A2A80`; `d115c_audit.log` 67 bytes, 직접 SHA `636D33BC3AA0B4EFBE9FD5A2873EBD847FB15A85B2DFC7D396817BD9787895E` |
| D115C implementation | `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\tools\research\audit_source_l29_l30_port_window.py`; 50520 bytes; SHA `FC18D720ABC6D05D9FB5441DFF7522865ECC79AA7C3AED90204F361516DE5EBF` |
| D115C focused test | `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\tests\test_audit_source_l29_l30_port_window.py`; 16782 bytes; SHA `291C32AD4499FA0E5ED99552A3B2C97D4855EEB9394CC44FE52C76866BF43829` |

## D116 미결정과 권고

**미결정(사실이 아님):** literal eight-row inclusion, A/F mapping, 그리고 D115B topology ownership과 D104 pad/plane geometry 중 어떤 것이 acceptance authority인지 명시적 승인이 필요하다.

**권고(승인·계약 변경 아님):** source-local W0를 보존하고 여덟 행을 모두 기록하되, positive-area `[259,262,264]`만 D116 matrix 후보로 넣는다. 그 경우 `A=[259,264]`, `F=[262]`로 기록하는 안을 상정한다. D115B topology ownership은 authoritative topology provenance로 유지하고, D104의 pad-circle containment는 diagnostic으로 취급한다. Plane WKB가 pad/thermal connectivity를 모두 직접 encode하지 않을 수 있기 때문이다.

대안의 trade-off는 다음과 같다.

1. **여덟 행 literal 유지:** 원 acceptance semantics를 보존하지만 현재 source geometry와 모순되어 계속 stop한다.
2. **positive-area 행만 사용(권고):** 실행 가능한 W0가 되지만 acceptance semantics가 바뀌므로 사용자 승인과 provenance 기록이 필수다.
3. **D104 geometry를 단독 authority로 승격:** 구현은 단순해지나 D115B ownership와 pad/thermal 의미를 잃을 위험이 있어 근거가 부족하다.

결정권자가 선택하기 전에는 어떤 대안도 D116 authorization으로 해석하지 않는다.

## Coordination retrospective

첫 Luna shallow/false-completion episode는 충분한 artifact/evidence 없이 완료를 선언한 실패한 초기 coordination pattern이었다. 이후 bounded file ownership와 명시적 evidence 요구로 복구했다. 이번에는 Sol이 attempt 1 log의 corrected direct hash를 독립적으로 찾아냈고, transferred hash가 틀렸음을 확인했다. 중첩 위임은 검증 경계를 분명히 했지만, 단일 문서 작업에는 handoff/polling 비용도 있었다. 따라서 증거가 여러 독립 산출물로 분산되거나 adversarial review가 필요할 때만 중첩을 사용하고, 단일 파일·단일 검증 경로는 root Sol이 Luna executor에게 직접 배정하는 편이 효율적이다.

권장 정책: **root Sol → child Sol cell lead → Luna executor**는 artifact provenance, 독립 재검증, 위험한 acceptance gate처럼 중첩의 이득이 overhead보다 클 때 사용한다. 단순 편집/집중 테스트는 root → Luna direct로 둔다. Long-run polling 기본값은 **10분 이상**으로 하고, 완료 임박 또는 attention/input 필요 시에만 짧게 폴링한다.

## Exact next-start gate

D116은 다음 네 항목을 명시적으로 승인한 뒤에만 시작할 수 있다.

1. eight-row inclusion semantics와 `A/F` mapping;
2. D115B 대 D104 authority 및 provenance 기록;
3. W0/source identity 재확인(HEAD, source, receipt identities);
4. 그 결정이 acceptance contract 변경인지 여부와 승인 주체.

하나라도 없으면 **STOP**이며 D116 또는 solver를 실행하지 않는다.
