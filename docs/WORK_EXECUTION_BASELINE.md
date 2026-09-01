# SPD Decap PI Evaluator v0.23.1 — 작업 기준

- 문서 버전: **3.14**
- 기준 branch: **main only**
- current integrated-hardening docs base: `5a270677074868fc3e10ffa26309a208bb151ac3`
- integrated-hardening implementation commit: `eece8ab944a29a9f6c5ddde17e56de8dbbd2ee6a`
- 최종 개정: **2026-09-02 (Asia/Seoul)**
- integrated-hardening lifecycle: **DONE / ACCEPT / COMMITTED @ `eece8ab944a29a9f6c5ddde17e56de8dbbd2ee6a`**
- current accuracy gate: **A1 DONE / ACCEPT — A2 DONE / ACCEPT_NARROW_CORE_EVIDENCE — V1/A2R/A2S DONE / STOP — A2T DONE / STATIC_ACCEPT — V3G DONE / STATIC_ACCEPT — D-087 DONE / STOP_V3_LAUNCHER_COORDINATOR_SHA_CASE — D-088 DONE / STOP_V3R_IMPORT_OWNERSHIP_TERMINAL_CONTRACT — D-089 DONE / ACCEPT_FOCUSED / COMMITTED @ `54c87d1` — D-090 DONE / STOP_D090_OWNERSHIP_TERMINAL_MULTIBRANCH_CARDINALITY — D-091 DONE / ACCEPT_FOCUSED / COMMITTED @ `6b88c3c` — D-092 DONE / STOP_D092_CONTACT_TERMINAL_OWNER_COVERAGE_PARTITION — D-093 DONE / ACCEPT_FOCUSED / COMMITTED @ `bebbb80` — D-094 DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION — D-095 DONE / ACCEPT_FOCUSED — D-096 DONE / ACCEPT — D-097 ACTIVE / not_run**
- D-089: **DONE / ACCEPT_FOCUSED / COMMITTED @ `54c87d1`** — `W7-ACC-TERMINAL-PATH-KIND-OWNERSHIP-IR-01` / `TERMINAL_PATH_KIND_OWNERSHIP_IR_CONTRACT`.
- D-090: **DONE / STOP_D090_OWNERSHIP_TERMINAL_MULTIBRANCH_CARDINALITY** — consumed, no-rerun.
- D-091: **DONE / ACCEPT_FOCUSED / COMMITTED @ `6b88c3c`** — `W7-ACC-OWNERSHIP-TERMINAL-MULTIBRANCH-CARDINALITY-01`.
- D-092: **DONE / STOP_D092_CONTACT_TERMINAL_OWNER_COVERAGE_PARTITION** — consumed, no-rerun.
- D-093: **DONE / ACCEPT_FOCUSED / COMMITTED @ `bebbb80`** — contact/terminal owner coverage partition.
- D-094: **DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION** — consumed, no-rerun.
- D-095: **DONE / ACCEPT_FOCUSED** — synthetic-only coordinator rows-hash projection check; consumed/no-rerun.
- D-096: **DONE / ACCEPT** — source-block census complete; `PASS_SOURCE_BLOCK_CENSUS_COMPLETE / CONSUMED_NO_RERUN`.
- D-097: **ACTIVE / not_run** — source-bound candidate geometry manifest/oracle eligibility.
- current numerical improvement: **0**

## 1. 압축 후 즉시 복구 카드

최초 목적은 원본 SPD의 source-derived physics로 PowerSI에 근접한 `Zii` 정확도를
얻는 것이다. source ownership IR 통합 hardening은 implementation `eece8ab`, docs
closure `caf505d`로 끝났다. 동일 21-node 계약은 재실행하지 않는다.

A1은 새 PowerSI run 없이 기존 W6 report를 한 번 읽어 development rail
`ADC_VDD_180_VQPS_SYS_1_AON/0`, no-decap low-band `C_eff` deficit,
`RAIL_REACHABLE_DIELECTRIC_GAP_MAXWELL_GC` 한 block으로 가설을 고정해
`DONE / ACCEPT`했다. A2는 기존 raw v3/ownership IR을 재사용하는 compile-only
census 하나로 구현됐고 Sol `STATIC_ACCEPT`를 받았다. focused V1은 test collection
전에 launcher Python의 pytest 부재로 STOP했다. 별도 A2R은 Python 3.12.10 /
pytest 9.0.3에서 collection 1 뒤 test-only invalid IR mutation으로 STOP했다. 별도
A2S는 그 block 14줄만 삭제하고 exact node를 한 번 실행했지만 synthetic-alias target이
없어 STOP했다. Sol 재평가 결과 제품 guard는 유지하고 fixture 의존 test block만
51줄 삭제했으며 추가 runtime 없이 A2를 `ACCEPT_NARROW_CORE_EVIDENCE`로 닫았다. 수치
개선은 아직 0이다. D-087은 대문자 SHA가 lowercase-only launcher gate에서 거부되어
source 접근 전 STOP했다. 동일 D-087은 재실행하지 않았다. D-088/V3R도 새 HEAD/root에서
정확히 한 번 소비된 STOP이며, 동일 실행은 재실행하지 않는다. D-089 focused terminal
path-kind ownership IR contract는 완료됐고 D-090은
`DONE / STOP_D090_OWNERSHIP_TERMINAL_MULTIBRANCH_CARDINALITY`로 소비된 STOP이다. D-091
focused ownership-terminal multibranch cardinality contract는 완료됐고 D-092는
contact/terminal owner coverage partition 불일치로 소비된 STOP이다. D-093 focused successor
contract는 `DONE / ACCEPT_FOCUSED / COMMITTED @ bebbb80`로 닫혔다. D-094 source-block
census는 `DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION`으로 소비됐고, D-095 focused
successor는 `DONE / ACCEPT_FOCUSED`로 닫혔다. D-096 original-SPD one-shot은
`DONE / ACCEPT` 및 `PASS_SOURCE_BLOCK_CENSUS_COMPLETE / CONSUMED_NO_RERUN`이며, 현재 D-097
geometry manifest가 `ACTIVE / not_run`이다.

```mermaid
flowchart LR
    H["source-IR hardening<br/>DONE / COMMITTED eece8ab"] --> A1["A1 W6 read-only error budget<br/>DONE / ACCEPT"]
    A1 --> A2["A2 source-block materializer<br/>STATIC_ACCEPT"]
    A2 --> M["minimal read-only materializer<br/>no new DB/schema"]
    M --> V1["focused V1<br/>STOP: launcher Python에 pytest 없음"]
    V1 -->|현재 결과| STOPV1["DONE / STOP_V1_LAUNCHER_NO_PYTEST"]
    STOPV1 --> V1R["A2R launcher recovery<br/>consumed / no rerun"]
    V1R -->|현재 결과| STOPV1R["DONE / STOP_V1R_TEST_FIXTURE_IR_RELATION"]
    STOPV1R --> V1S["A2S test-only successor<br/>consumed / no rerun"]
    V1S -->|현재 결과| STOPV1S["DONE / STOP_V1S<br/>alias fixture target 없음"]
    STOPV1S --> V1T["A2T alias test prune<br/>DONE / STATIC_ACCEPT"]
    V1T --> AN["A2 narrow core evidence<br/>DONE / ACCEPT"]
    AN --> V3G["별도 V3 계약<br/>DONE / STATIC_ACCEPT"]
    V3G --> V3["D-087 single run<br/>STOP: SHA case gate"]
    V3 --> V3R["D-088 recovery<br/>DONE / STOP_V3R_IMPORT_OWNERSHIP_TERMINAL_CONTRACT"]
    V3R --> D89["D-089 focused contract<br/>DONE / ACCEPT_FOCUSED / COMMITTED 54c87d1"]
    D89 --> D90["D-090 source-block census<br/>DONE / STOP_D090_OWNERSHIP_TERMINAL_MULTIBRANCH_CARDINALITY"]
    D90 --> D91["D-091 focused cardinality contract<br/>DONE / ACCEPT_FOCUSED / COMMITTED 6b88c3c"]
    D91 --> D92["D-092 source-block census<br/>DONE / STOP_D092_CONTACT_TERMINAL_OWNER_COVERAGE_PARTITION"]
    D92 --> D93["D-093 contact terminal owner coverage partition<br/>DONE / ACCEPT_FOCUSED / COMMITTED bebbb80"]
    D93 --> D94["D-094 source-block census<br/>DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION"]
    D94 --> D95["D-095 coordinator rows-hash projection<br/>DONE / ACCEPT_FOCUSED"]
    D95 --> D96["D-096 original-SPD source-block census<br/>DONE / ACCEPT<br/>PASS_SOURCE_BLOCK_CENSUS_COMPLETE / CONSUMED_NO_RERUN"]
    D96 --> D97["D-097 source-bound geometry manifest<br/>ACTIVE / not_run"]
    D97 -->|focused acceptance 후| D98["D-098 original-SPD geometry manifest one-shot<br/>separate one-shot"]
    D98 -->|manifest PASS 후| OC["manufactured/source-bound nonzero deltaC oracle gate<br/>number/scope TBD"]
    OC -->|oracle PASS 후| A3["production one-shot consideration"]
    D89 -->|STOP| STOPSRC["static diagnosis/replanning"]
```

현재 금지 사항:

- untracked `accuracy_parse.py`를 읽거나 수정하거나 stage하지 않는다.
- `git status`는 `--untracked-files=no`를 사용한다.
- successor 21-node invocation과 A1 V0 audit는 소모됐다. 같은 계약을 반복하지
  않는다.
- A2 materializer는 정적으로 ACCEPT됐지만 focused V1이 launcher 단계에서
  STOP했고 D-084 exact V1R도 test fixture 관계 오류로 소비됐다. 둘은 재실행하지
  않는다. 원본 SPD V3R 실행은 D-088에서 소비됐고 동일 계약을 재실행하지 않는다.
  D-085에서 허용한 test block 삭제와 exact successor 외에는 P1 condensation,
  global solver/`Y_global`/`Zii`, PowerSI와
  package/release도 실행하지 않는다.
- 기존 W6 report는 read-only/hash-bound evidence이며 보정 parameter 생성에 쓰지
  않는다.
- stage와 commit은 explicit path로만 수행한다.

사용량은 Codex Usage Guard v0.1.1로 phase 전환과 delegation/build 전에 확인한다.
사용자가 지정한 남은 사용량 **30%** 지점에서는 새 expensive phase를 시작하지
않고 현재 상태를 문서화해 멈춘다.

## 2. 문서 권위와 갱신 규칙

- [목적·기술 기준](PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md)은 왜·무엇·합격
  의미와 비주장 경계를 정한다.
- 이 문서는 current candidate, 파일, 검증 예산, 상태 전이와 다음 계획만 관리한다.
- [Source-derived physical IR](SOURCE_DERIVED_PHYSICAL_IR.md)은 schema/technical
  contract다. 그 문서의 live status snapshot이 이 문서와 다르면 이 문서가 우선한다.
- exact source, commit, input/output hash와 log가 요약보다 우선한다.
- 완료 micro-item의 세부 명령과 STOP 연쇄를 current context에 다시 복제하지
  않는다. 필요하면 Git history와 Source-IR historical section에서 읽는다.

상태는 두 축으로 기록한다.

| 축 | 값 |
|---|---|
| lifecycle | `PLANNED`, `ACTIVE`, `DONE`, `BLOCKED`, `DEFERRED` |
| outcome | `TEST_ONLY_SUCCESSOR_PLANNED`, `IMPLEMENTED_AWAITING_SOL_STATIC_REVIEW`, `STATIC_ACCEPT`, `ACCEPT`, `STOP_<CAUSE>`, `not_run` |

`DONE/STOP_*`은 lifecycle과 outcome을 합친 표기이지 별도 lifecycle 값이 아니다.

## 3. 현재 working tree와 candidate receipt

branch는 `main`이다. base HEAD
`5a270677074868fc3e10ffa26309a208bb151ac3`에서 만든 implementation commit
`eece8ab944a29a9f6c5ddde17e56de8dbbd2ee6a`은 다음 아홉 파일로 제한된다.

- `docs/PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md`
- `docs/SOURCE_DERIVED_PHYSICAL_IR.md`
- `docs/WORK_EXECUTION_BASELINE.md`
- `src/spd_decap_pi/raw_spatial_contact_compiler.py`
- `src/spd_decap_pi/source_plane_ownership_ir.py`
- `src/spd_decap_pi/spd_adapter.py`
- `tests/test_raw_spatial_contact_compiler.py`
- `tests/test_source_plane_ownership_ir.py`
- `tests/test_source_plane_ownership_ir_producer.py`

Implementation stream의 predecessor와 final **accepted** candidate SHA-256은
다음과 같다. final identity는 Sol `STATIC_ACCEPT`와 successor 21-node PASS를 얻은
뒤 `eece8ab944a29a9f6c5ddde17e56de8dbbd2ee6a`로 commit됐다.

| 파일 | predecessor SHA-256 | final candidate SHA-256 | 상태 |
|---|---|---|---|
| raw compiler product | `5B02CC51677FEB6BCBE537071C7A13342E2036C0F2D7910103118865C3A45BA2` | 동일 | frozen / successor 21 PASS 포함 |
| source IR | `1027E694F6994B4F6B109933617D149A7A5184CD6951050D2C93F67B145079DE` | `7F9AA899BD3F117316AC5949E41B45144CC309B2A3D07B585C4196BC84BF6522` | static accepted / successor 21 PASS |
| adapter | `F8E81D7B702E155C51766A8CC0CAC2B3FE3E189450BEB43DAC0811FA9A213D14` | `8AE7C31936125C028620FAB1FA217026B5CAFB47D51813FBFE9A15D01E7AC931` | static accepted / successor 21 PASS |
| source IR test | `56AAECB2A83F9E96E81D2F3F77D7EDC67F896E7AAD63BE38999AFAC06FDA8756` | `1A60C5B52B7E57BC04A2AAF2243FDA829D5C832FE247B4E0FE476FBB405A1362` | static accepted / successor 21 PASS |
| producer test | `FD9C1E698DEE0B254EB76960CC890D39C509E5926055C159DC5EF9427F87AFC9` | `BD8B813C0ED5125F741E130E65ECC26088F5BD10E057C1BEA110EDFF51C06FA4` | static accepted / successor 21 PASS |
| raw compiler test | `D51D4E45E0182DC99F60E12D7DB48528F1F38567EBD2A4E245DE416C4EF102BE` | `B04C3D50E4862868C161EBA85DA1ABEE1A4BE22D2B97F21E0A9694E9CC5A46FC` | static accepted / successor 21 PASS |

### 3.1 구현 A — source validator

허용 파일은 `source_plane_ownership_ir.py`와 해당 test뿐이다.

- NULL retained-owner edge/rail/island provenance를 명시적으로 거부한다.
- PadDef/Regular wildcard `LIKE`를 literal casefold-prefix 검사로 바꾼다.
- scope ID의 Python-casefold collision을 거부한다.
- surface lineage, dielectric layer와 ledger member validation을 indexed TEMP
  relation으로 제한한다.
- persisted v2 schema와 cap을 바꾸지 않는다.

신규 acceptance node:
`test_spool_validation_balanced_relations_use_indexed_lookups`.

### 3.2 구현 B — adapter handoff

허용 파일은 `spd_adapter.py`, producer test와 raw compiler test뿐이다.

- exact compiler source ID와 primitive primary key를 사용한다.
- Python-casefold island key와 TEMP edge indexes를 사용한다.
- canonical `ownership_source_stage` key로 pad row를 찾는다.
- logical identity의 SQLite ASCII `NOCASE` 의존을 제거한다.
- 38,939 selected rows에서 Via endpoint expansion 후 116,791 rows가 되는 것과
  derive-time cap overflow를 분리해 검증한다.
- raw compiler product, persisted schema와 cap을 바꾸지 않는다.

신규 acceptance nodes:

- `test_source_plane_ownership_streamed_handoff_preserves_unicode_casefold`
- `test_source_plane_ownership_streamed_handoff_uses_indexed_lookups`

## 4. 현재 통합 정적 review gate

Sol은 stream별 부분 판정을 합성하지 않고 현재 아홉 파일의 cumulative tracked
diff를 한 번에 검토한다.

Acceptance:

1. raw compiler product SHA가 frozen hash와 같다.
2. A/B implementation whitelist 밖의 code/test 변경이 없다.
3. nullable provenance, literal prefix, scope casefold collision, Unicode handoff,
   bounded indexed plan과 Via expansion 요구가 모두 닫혔다.
4. persisted schema/caps, solver, owner-off, `Y_global`과 `Zii`가 바뀌지 않았다.
5. test가 실제 변경 경로를 검증하며 source-string 존재 확인만으로 의미를 대신하지
   않는다.
6. P0–P3 finding이 없다.
7. final candidate SHA receipt와 `git diff --check`를 기록한다.

finding이 있으면 같은 ACTIVE item 안에서 Luna가 해당 whitelist만 수정한다.
정적 재검토 전 Python은 금지한다. whitelist 밖 설계 변경이 필요하면
`DONE / STOP_SCOPE_CHANGE`로 닫고 사용자 검토 후 새 계획을 작성한다.

## 5. 단일 21-node 실행 계약

Sol이 `STATIC_ACCEPT`한 뒤에만 Python 3.12의 새 process 하나에서
`-x -vv --tb=long`으로 다음 21개 unique node를 순서대로 실행한다.

1. `tests/test_source_plane_ownership_ir.py::test_spool_sql_validation_rejects_provenance_mutations`
2. `tests/test_source_plane_ownership_ir.py::test_spool_sql_contact_identity_and_edge_owner_rejections`
3. `tests/test_source_plane_ownership_ir.py::test_spool_sql_rejects_origin_scalar_casefold_and_ordinal_holes`
4. `tests/test_source_plane_ownership_ir.py::test_spool_validation_balanced_relations_use_indexed_lookups`
5. `tests/test_source_plane_ownership_ir_producer.py::test_source_plane_ownership_streamed_handoff_preserves_unicode_casefold`
6. `tests/test_source_plane_ownership_ir_producer.py::test_source_plane_ownership_streamed_handoff_uses_indexed_lookups`
7. `tests/test_raw_spatial_contact_compiler.py::test_ownership_expanded_selection_116791_and_global_cap`
8. `tests/test_source_plane_ownership_ir_producer.py::test_source_plane_ownership_provisional_request_aggregate_does_not_consume_final_ir_row_cap`
9. `tests/test_source_plane_ownership_ir_producer.py::test_source_plane_ownership_component_layer_is_authoritative_for_mismatched_endpoint`
10. `tests/test_raw_spatial_contact_compiler.py::test_ownership_selection_duplicate_is_sql_unique_failure`
11. `tests/test_raw_spatial_contact_compiler.py::test_ownership_selection_cap_exact_pass_plus_one_fails`
12. `tests/test_raw_spatial_contact_compiler.py::test_ownership_source_staging_keeps_original_spelling_and_canonical_lookup`
13. `tests/test_source_plane_ownership_ir.py::test_v2_mapping_and_spool_are_logically_equivalent_and_exclude_internal_tables`
14. `tests/test_source_plane_ownership_ir.py::test_spool_caps_and_cancellation_fail_closed`
15. `tests/test_source_plane_ownership_ir.py::test_spool_sql_rejects_contact_opposite_external_node_chain`
16. `tests/test_source_plane_ownership_ir.py::test_spool_temp_artifacts_are_removed_after_cancel_and_exception`
17. `tests/test_source_plane_ownership_ir_producer.py::test_source_plane_ownership_filters_global_quotient_before_selected_row_bound[unrelated_global]`
18. `tests/test_source_plane_ownership_ir_producer.py::test_source_plane_ownership_filters_global_quotient_before_selected_row_bound[projected_over_cap]`
19. `tests/test_source_plane_ownership_ir_producer.py::test_source_plane_ownership_producer_roundtrip_and_atomic_failure`
20. `tests/test_source_plane_ownership_ir.py::test_v2_exact_section_and_total_caps[150001-2-True-None]`
21. `tests/test_source_plane_ownership_ir.py::test_v2_exact_section_and_total_caps[150000-149978-True-None]`

실행 조건:

- 외부 wall limit **300 s**
- collection **21**, exit **0**, `21 passed`가 PASS의 필요조건
- stdout/stderr의 absolute path, byte count와 SHA-256 기록
- 이전 §12.60의 18-node run을 별도로 다시 실행하지 않는다.
- pressure, exact-300K, redundant cap cases, unchanged/full suite, 원본 SPD,
  solver, PowerSI와 release는 `not_run/not_required`다.

결과 전이:

| 결과 | 상태 | 다음 행동 |
|---|---|---|
| Sol finding | `ACTIVE / REVIEW_CHANGES_REQUIRED` | 같은 item·whitelist에서 수정, Python 금지 |
| Sol P0–P3 none | `ACTIVE / STATIC_ACCEPT / READY_FOR_SINGLE_INTEGRATED_RUN` | exact 21 nodes 한 번 |
| 21 PASS | `DONE / ACCEPT / READY_FOR_EXPLICIT_STAGE_AND_COMMIT` | nine paths만 stage하고 final diff 검토 |
| collection/failure/timeout/interrupt | `DONE / STOP_<CAUSE>` | partial PASS 재사용·rerun·자동 successor 금지 |
| explicit commit 완료 | `DONE / ACCEPT / COMMITTED @ <SHA>` | accuracy plan의 첫 gate로 전환 |

실제 단일 invocation은 Python 3.12.10/pytest 9.0.3에서 21개를 수집했고 앞의
4개가 PASS한 뒤 node 5
`test_source_plane_ownership_streamed_handoff_preserves_unicode_casefold`에서
`SPD_NO_RAILS`로 실패했다. fixture의 `Node1`→`Straße1` 치환 뒤 import plan이
유효한 rail을 만들지 못했으며 ownership callback에는 도달하지 않았다. 결과는
`1 failed, 4 passed in 8.66s`, child exit 1, wrapper wall 9.767968 s다.

- stdout: `C:\Users\User\AppData\Local\Temp\spd-pi-integrated-21-c3c867347ed443df9a9b5693f672aafd.stdout.log`, 7,571 bytes, SHA-256 `6C4CE1E243D1CB19D7BB179D84D9EE689E514BB9249D1942814237ADDB352B29`
- stderr: `C:\Users\User\AppData\Local\Temp\spd-pi-integrated-21-c3c867347ed443df9a9b5693f672aafd.stderr.log`, 0 bytes, SHA-256 `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`

판정은 `DONE / STOP_TEST_UNICODE_FIXTURE_RAIL_FORMATION`이다. 부분 PASS는 재사용하지
않고 동일 21-node 계약을 재실행하지 않으며 자동 fix successor를 만들지 않는다.

이 계약에서 21 PASS가 발생했을 경우에만 증명했을 범위는
fail-closed/casefold/indexed semantics와 named synthetic/MINI-SPD v2/v3
transport뿐이다. 실제 invocation은 STOP이며 production owner join, `Y_global`,
`Zii`, PowerSI 정확성 또는 성능을 증명하지 않는다.

## 6. 현재까지의 결과 압축

| 묶음 | lifecycle/outcome | 현재 의미 |
|---|---|---|
| G0, W0–W5 | DONE / ACCEPT | 목적 통제, source/I/O 안전성, solver guard와 accuracy policy 기반 |
| W6-BASE 260729 | DONE / NUMERICAL_FAIL | bare macro `1.707111 dB`, loaded macro `15.910647 dB`, failure `57`; unseen unknown |
| W7 Source IR Phase 1–4 | DONE / ACCEPT | canonical source/provenance와 contact ownership prerequisite |
| W7 prospective P0–P10 | DONE / mixed prerequisite outcomes | shadow contact/N-port/owner/assembly evidence; production 정확성 아님 |
| P11 | DONE / SHADOW_SOLVE_NUMERICAL_FAILURE | forward-reliability STOP; trusted solve 아님 |
| actual SPD/DSU/owner evidence | DONE / STOP chain | source 규모·identity·ownership 문제를 분류; 기존 one-shot은 rerun하지 않음 |
| streamed-IR chain | DONE / mixed STOP/ACCEPT | disk-backed handoff와 validator/index 경로 구현; 수치 개선 0 |
| provisional-cap oracle fix | DONE / ACCEPT | sole run `18 passed in 14.80s`; narrow synthetic/MINI scope |
| integrated hardening | DONE / STOP_TEST_UNICODE_FIXTURE_RAIL_FORMATION | Sol static P0–P3 없음; single run은 21 collected, node 5 실패; uncommitted/no-rerun |
| Unicode fixture successor | DONE / ACCEPT / COMMITTED @ `eece8ab` | Sol `STATIC_ACCEPT`, P0–P3 0; grammar-valid `NodeStraße2`; new-candidate `21 passed in 15.09s`; product frozen |
| W8 release | BLOCKED | accuracy와 product gate 뒤에만 진행 |

§12.60 historical run의 log는
`C:\Users\User\AppData\Local\Temp\spd-pi-provisional-cap-fix-18node-440ebbdd5e7540a4a9d8e300c3b6a166.log`,
2,773 bytes, SHA-256
`20B0444750B8D6F125F898AC120DD8FA4AEDE433F5D7004AADEFE513A88F47A6`다.
그 invocation은 consumed/no-rerun이다.

base commit `5a270677074868fc3e10ffa26309a208bb151ac3`의 이 문서는 D-001–D-076과
§12.60의 frozen predecessor 계약까지 보존한다. 이후 provisional-cap PASS와
integrated-review 결정의 핵심 사실은 위 표와
[Source-derived physical IR](SOURCE_DERIVED_PHYSICAL_IR.md)의 §31–§32에 보존한다.
그 상세를 current context로 자동 복원하거나 과거 partial PASS를 acceptance로
재사용하지 않는다.

## 7. hardening 뒤 정확성 우선 계획

다음 계획은 current hardening이 `DONE / ACCEPT / COMMITTED`일 때만 순서대로 연다.
각 단계는 한 번에 하나만 ACTIVE다.

predecessor hardening은 `DONE / STOP`이고 승인된 test-only successor는
`DONE / ACCEPT / COMMITTED @ eece8ab`다. A1은 phase checkpoint 뒤 한 번 수행해
`DONE / ACCEPT`했다. A2 candidate는 Sol `STATIC_ACCEPT`를 받았지만 V1/A2R/A2S
실행은 각각 소비된 STOP이다. A2T는 runtime 없이 `DONE / STATIC_ACCEPT`했고 A2는 좁은
core evidence만으로 종료됐다. D-087은 launcher-only STOP으로 닫혔고 D-088/V3R도
별도 hash-bound single run을 소비한 STOP으로 닫혔다. D-089 focused terminal path-kind
ownership IR contract는 완료됐고 D-090은
`DONE / STOP_D090_OWNERSHIP_TERMINAL_MULTIBRANCH_CARDINALITY`로 소비된 STOP이다. D-091
focused ownership-terminal multibranch cardinality contract는
`DONE / ACCEPT_FOCUSED / COMMITTED @ 6b88c3c`로 닫혔고 D-092는
`DONE / STOP_D092_CONTACT_TERMINAL_OWNER_COVERAGE_PARTITION`으로 소비됐다. D-093은
`DONE / ACCEPT_FOCUSED / COMMITTED @ bebbb80`로 닫혔다. D-094는
`DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION`으로 소비됐고 D-095는
`DONE / ACCEPT_FOCUSED`로 닫혔다. D-096은 `DONE / ACCEPT`이며 D-097이 `ACTIVE / not_run`다.

### A1 — W7-ACC-ERROR-BUDGET-01 — DONE / ACCEPT

목적: 새 PowerSI run 없이 기존 hash-bound W6 report로 저주파 offset, resistance
floor, inductive slope, resonance 위치·진폭/Q, phase/complex error와 bare-loaded
차이를 분리한다.

Acceptance: 한 development rail, 한 사전 지정 error component와 하나의 source-owned
physical block을 연결한 no-fit 가설이 있어야 한다. 연결이 모호하면
`STOP_NOT_READY`; code 변경과 후보 동시 구현은 없다.

검증 rung: **V0/read-only evidence audit 한 번**.

실제 audit는 다음 immutable evidence를 읽었고 파일을 변경하지 않았다.

- `D:\SPD-Decap-PI-Evaluator-W6\fb36288781dcc0b884950ef5a486c474090ceebd\260729\run_manifest.json` — SHA-256 `2B14F90E762ABC49833518137812E8FC97FCDE0E9C145795B7384210CFD9F5DE`
- 같은 root의 `accuracy_sidecar.json` — SHA-256 `0D103E0AD47DF80641FAC0952A35A6EAA56CC9FDB71BE24661903E451926E932`
- `correlation\correlation_report.json` — SHA-256 `969E40046E3A099D09557BA7500693460362336D76B067962436BD3B5177ABC4`

선택 규칙은 approved `vqps_development` 5 rails 중 frozen low-frequency offset이
가장 큰 rail이며 manifest order로 tie-break한다. 결과는
`ADC_VDD_180_VQPS_SYS_1_AON/0`이다. mode-12 report의 0.1/1 MHz error는
`+1.5552251578/+1.5542171886 dB`, low offset은 `1.5547211732 dB`; phase error는
`+0.013004/+0.010752 deg`다. model/reference equivalent `C_eff`는 약
`0.5381/0.6436 nF`, ratio는 약 `0.8361`이다. frequency/modal convergence는 true,
global-Y maximum relative residual은 `1.4816082829448757e-15`다.

| 고정 항목 | A1 결정 |
|---|---|
| error component | no-decap low-band `C_eff` deficit |
| source-owned block | `RAIL_REACHABLE_DIELECTRIC_GAP_MAXWELL_GC` |
| selected pair | `Signal$L30(OTHER_POWER1)` / `Signal$L29(DGND)` |
| expected direction | source-only transverse `G/C`가 low-band capacitive admittance를 늘리고 model `|Zii|`를 낮춤 |
| existing owner seam | source/P1 contact와 old-Maxwell row fingerprint, per-rail exact-one projection, replaced/retained ledger |
| analytic limit | uniform two-plate `C=eps0*epsr*A/d`, `[[C,-C],[-C,C]]`, row sum 0, passive energy, `G=omega*C*Df >= 0` |

`0.8361`은 보정계수가 아니다. Dk, Df, area, thickness, fringe나 scale을 PowerSI에
맞추지 않는다. R/L은 거의 `1/f`인 magnitude와 약 0° phase error 때문에, resonance/Q는
bare rail에서 N/A이므로, loaded error는 decap/termination/loss interaction을 섞으므로
선택하지 않았다. 100 MHz와 broadband error도 A1 claim 밖이다. A1은 retrospective
one-rail hypothesis만 확정하며 production replacement, holdout/unseen 또는 PowerSI
수치 개선을 증명하지 않는다.

### A2 — W7-ACC-SOURCE-BLOCK-CONTRACT-01 — DONE / STOP_V1_LAUNCHER_NO_PYTEST

목적: 선택 block에 필요한 geometry, stack-up, dielectric, Trace, Via,
pad/anti-pad, plane artwork와 port/owner relation은 기존 raw v3/ownership IR DB에서
재사용하고, compile 결과의 G/C census만 한 번 materialize할 계약을 고정한다.

Sol 판정은 `A2_CONTRACT_ACCEPT_MINIMAL_MATERIALIZER`다. raw-spatial v3와
source-plane ownership IR v2는 필요한 geometry/material/source byte provenance와
owner ledger를 이미 가진다. 새 DB/table/schema/dependency는 만들지 않는다. 실제
production Maxwell partial과 reduced-node mapping만 compile 뒤에 존재하므로
source-only compile은 필요하지만, 약 38,920-contact P1 condensation은 A2에
불필요하므로 금지한다.

Acceptance: selected rail/pair와 위 세 evidence hash를 동결하고 다음 V0 contract를
모두 만족한다.

| 계약 항목 | 고정 기준 |
|---|---|
| generic product materializer | `audit_source_plane_source_block_census(...)` 한 함수; report-returning/read-only |
| persisted data | 기존 raw v3 + ownership IR v2만 사용; 새 asset/schema 0 |
| generic query-key identity | block, rail, L30/L29 pair, source/raw geometry/logical/plane-sheet, ownership/certificate/compiled-topology/substrate hash, P/G surface/component/island/reduced closure, policy version |
| one-run receipt identity | generic query hash와 L30/L29 선택을 manifest/sidecar/correlation의 세 frozen W6 hash에 결속; product 함수에 W6 hardcode 금지 |
| rail-complete scan | selected P/G reduced closure 중 하나에 incident한 모든 negative off-diagonal production Maxwell row |
| stable identity | 기존 `SHA256(substrate_identity, upper_layer, lower_layer, upper_island_id, lower_island_id, capacitance_f_hex)` fingerprint를 유지하고 partial ordinal/reduced coordinates/classification을 별도 row hash에 결속 |
| classification | 각 row가 `adjacent`, `source-proven-nonlocal`, `missing-source-excluded` 중 정확히 하나 |
| G/C source law | source Dk/Df record를 보존하고 `G(f)=2*pi*f*C(f)*Df(f)`만 기록; A2에서 수치 평가·fitting 금지 |
| owner partition | selected P↔G candidate fingerprints와 retained/excluded fingerprints를 분리하고 IR replaced/retained ledger와 disjoint hash 결속 |
| output flags | `shadow_only=true`, `replacement_ready=false`, `production_ready=false` |

duplicate/casefold collision, nonfinite/asymmetric/non-Laplacian matrix, source-record
누락, unclassified row, raw/collapsed aggregation 불일치, ledger overlap 또는 owner
scope가 candidate/retained를 구분하지 못하면 `STOP_NOT_READY`다. 외부 전자기 효과가
원본에 없는데 magnitude를 발명하거나 PowerSI로 보정해서도 안 된다.

구현 whitelist는 `src/spd_decap_pi/source_plane_patch_consumer.py`와
`tests/test_source_plane_patch_consumer.py` 한 node뿐이다. 새 module/abstraction은
금지한다. focused test는 P1/solve API를 호출하면 즉시 실패하도록 하고 deterministic
census와 fail-closed tamper를 함께 확인한다.

검증 rung은 Luna 구현 뒤 Sol static review, exact focused **V1 한 번**, 그 뒤
production **V3 한 번**이다. Luna candidate는 source
`63BEA32E1184154539ABD7DFE6B54555131888393FC04E5E12892E9EB730C710`, test
`69449B7FB5FF96A507F1A56C615680DACCDD8247E10201ACFD16D615C6416B79`로 동결됐고,
Sol 최종 판정은 `STATIC_ACCEPT`, P0/P1/P2 0이다.

2026-09-01T13:12:32+09:00 V1 영수증:

```powershell
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest -x -vv --tb=long 'tests/test_source_plane_patch_consumer.py::test_source_plane_source_block_census_is_deterministic_and_fail_closed'
```

- fresh process / wall time `0.496 s` / exit `1`
- stdout/stderr: `No module named pytest`
- pytest collection과 제품 import: **not_started**
- 판정: **STOP_V1_LAUNCHER_NO_PYTEST** — code FAIL도 PASS도 아님
- consumed: 이 launcher를 사용한 동일 V1 계약; 즉시 다른 Python으로 반복 금지
- blocked: V3 original SPD import, source-only compile, materializer와 report/receipt

V3는 당시 focused PASS 전까지 차단됐다. 이후 A2S의 STOP은 그대로 보존하고 A2T 정적
삭제 뒤 A2를 좁은 core evidence로 종료했다. D-088의 fresh import 1회는 import-stage
fail-closed STOP으로 소비됐고 compile/materializer와 canonical report는 실행되지 않았다.
P0/P1 condensation, global solve, `Y_global`, `Zii`, PowerSI, full suite, retry와
partial reuse는 금지한다.

### A3 — W7-ACC-ONE-BLOCK-IMPLEMENTATION-01

목적: 기존 IR/owner relation을 재사용해 선택한 physical block 하나만 research
path에 구현한다. 후보는 오차 증거에 따라 current-spreading/plane-sheet R/L,
via/pad/anti-pad local replacement, Trace R/L 또는 causal dielectric 중 하나다.

Acceptance: limiting case, passivity, reciprocity, conservation/row sum,
conditioning, deterministic stamp와 double-counting 방지를 analytic/synthetic case로
확인한다. 새 범용 abstraction, 두 번째 물리 block, schema/cap redesign이 필요하면
현재 후보를 STOP한다.

검증 rung: focused **V1 한 번**, 묶음 종료 시 bounded subsystem **V2 한 번**.

### A4 — W7-ACC-BOUNDED-DEVELOPMENT-COMPARE-01

목적: 앞 단계를 통과한 frozen candidate를 260729의 사전 지정 development
bare/loaded rail에 한해 한 번 비교한다.

Acceptance: W5 scorer로 low-frequency offset, critical-band magnitude, phase,
resonance와 complex error를 기록한다. 사전 지정 error component가 예상 방향으로
개선되고 catastrophe/integrity gate를 유지해야 한다. PowerSI fitting, global
scaling과 threshold 완화는 금지한다.

검증 rung: known-case V3 후 **frozen development V4 한 번**. 실패하면 후보를
STOP하며 다른 block으로 자동 전환하지 않는다.

### A5 — W7-ACC-HOLDOUT-AND-UNSEEN-01

260729 development가 통과한 candidate만 260804 retrospective holdout으로 간다.
두 retrospective board가 통과해도 final generalization은 새 unseen design이
필수다. unseen 자료가 없으면 상태는 `BLOCKED / DATA_REQUIRED`이며 release로 가지
않는다.

## 8. 검증 사다리와 낭비 방지

| rung | 범위 | 기본 최대 횟수 |
|---|---|---:|
| V0 | 문서 link/structure, diff, hash와 정적 source review | 변경 묶음당 1 |
| V1 | 한 root cause의 focused test/analytic oracle | 구현 묶음당 1 |
| V2 | 관련 subsystem selection | 모든 V1 green 뒤 1 |
| V3 | bounded product-core/known-case 또는 production source import | frozen phase당 1 |
| V4 | frozen development/holdout PowerSI correlation | 승인 candidate와 partition당 1 |
| V5 | package, installed smoke, release artifact/CI | exact release commit당 1 |

- 하위 rung이 red이면 상위 rung을 디버깅 수단으로 사용하지 않는다.
- 동일 계약·동일 원인으로 고비용 실행을 반복하지 않는다.
- test 수정이 product 결함을 숨기지 않는지 먼저 정적으로 검토한다.
- full suite는 정확성 milestone 또는 release 직전처럼 사전 지정한 시점에만 연다.
- predecessor closure turn은 Sol `STATIC_ACCEPT` 뒤 §5 invocation 하나만 수행해
  STOP했다. 승인된 §11 successor는 새 test hash로 정적 동결한 뒤 §5의 ordered
  21 nodes를 새 process 한 번만 실행해 PASS했다. 별도 focused 예행 test는 하지
  않았고 해당 invocation은 consumed/no-rerun이다.

## 9. 현재 결정

### D-079 — integrated candidate 구현 완료와 단일 closure

두 disjoint Luna stream은 predecessor에서 구현을 완료했다. 첫 누적 review는
일반 retained owner의 NULL provenance와 제품 경로에 결속되지 않은 두
indexed-plan test를 P1/P2로 발견했다. 같은 item 안에서 validator와 기존 test
node를 수정했고, 두 plan test를 실제 validator/import SQL의 runtime
`EXPLAIN QUERY PLAN` 계측으로 교체했다.

두 번째 review는 앞당겨진 error code 기대와 SQLite covering-index 표현을 P2로
발견했다. 동일 기존 node 안에서 기대값과 plan 정규화만 수정한 뒤 Sol 최종
재검토는 `STATIC_ACCEPT`, P0/P1/P2/P3 모두 0이었다. 이후 §5 invocation은 21개를
수집했지만 Unicode fixture의 rail formation 실패로 node 5에서 exit 1이 됐다.
따라서 최종 상태는 `DONE / STOP_TEST_UNICODE_FIXTURE_RAIL_FORMATION`; stage/commit,
A1, 원본 SPD, solver와 PowerSI는 진행하지 않는다.

### D-081 — Unicode fixture successor acceptance

사용자 승인 별도 successor는 제품 코드를 동결하고 producer test 한 함수의
fixture grammar만 복원했다. Sol 최종 판정은 `STATIC_ACCEPT`, P0/P1/P2/P3 모두
0이고, 새 candidate의 exact 21-node invocation은 `21 passed in 15.09s`, exit 0으로
끝났다. 상태는 `DONE / ACCEPT / COMMITTED @ eece8ab`이며 동일 계약을
재실행하지 않는다.

### D-080 — 정확성 우선 pivot

integrated hardening 뒤의 목적은 transport/ownership micro-hardening 반복이 아니라
source-derived physical-model accuracy다. 기존 W6 증거로 하나의 error component와
하나의 owning physical block을 먼저 선택한다. PowerSI는 fitting에 사용하지 않고,
source provenance, deterministic replacement stamp, disjoint owner ledger와 no-fit
limiting-case gate를 통과한 candidate만 bounded development comparison으로 보낸다.

### D-082 — A2는 기존 DB를 유지하고 compile-only census만 추가

정적 schema/flow 감사에서 raw v3와 ownership IR v2의 source data 결손은 발견되지
않았다. actual production old-Maxwell row는 source-only compile 뒤에만 존재하므로
compile을 생략할 수 없지만, source contract를 위해 P1 N-port를 만들 이유도 없다.
따라서 persistence redesign 대신 기존 consumer에 read-only report 함수 하나와 test
node 하나만 추가한다. 이 결정은 G/C completeness 증거의 수집 방법만 고정하며
solver/PowerSI 수치 개선은 계속 0이다.

### D-083 — A2 V1 launcher failure와 V3 차단

A2 source/test candidate는 Sol `STATIC_ACCEPT`를 받았지만, 지정된 번들 Python에는
pytest가 없어 focused V1이 collection 전에 exit 1로 종료됐다. 제품 코드는 실행되지
않았으므로 이를 code FAIL 또는 PASS로 해석하지 않는다. 동일 V1을 다른 interpreter로
즉시 반복하지 않고 `STOP_V1_LAUNCHER_NO_PYTEST`로 닫으며, V3는 열지 않는다.

다음 허용 작업은 read-only로 기존 pytest-capable interpreter와 dependency identity를
확인하고, command/interpreter/version을 포함한 별도 V1 recovery 계약을 이 문서에
먼저 동결하는 것이다. 그 계약의 PASS 전에는 원본 SPD import를 시작하지 않는다.

### D-084 — A2R pytest-capable launcher recovery — DONE / STOP_V1R_TEST_FIXTURE_IR_RELATION

read-only filesystem 확인으로 과거 21-node PASS와 같은 Python/pytest 버전의 기존
설치를 찾았다. Python이나 pytest를 실행해 예행하지 않았으며 다음 identity만
동결했다.

| 항목 | 동결 값 |
|---|---|
| product/test base commit | `dee9055f10543d29ce3590a10672500bf106350d` |
| source SHA-256 | `63BEA32E1184154539ABD7DFE6B54555131888393FC04E5E12892E9EB730C710` |
| test SHA-256 | `69449B7FB5FF96A507F1A56C615680DACCDD8247E10201ACFD16D615C6416B79` |
| Python | `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe` / file version `3.12.10` |
| Python SHA-256 | `4D6F5F81A4BCA11191C4C7C6B43632694D0A4CE74E068619D8FDC161D469859A` |
| pytest metadata | `pytest-9.0.3.dist-info/METADATA` / version `9.0.3` |
| pytest metadata SHA-256 | `C3966F28791686477BAE35E518736D4CBEA5B626D3A79011215854E2BC670207` |

exact V1R command:

```powershell
& 'C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -x -vv --tb=long 'tests/test_source_plane_patch_consumer.py::test_source_plane_source_block_census_is_deterministic_and_fail_closed'
```

실행 전 조건은 main, 위 source/test hash, 범위 밖 tracked 변경 0이다. fresh process
한 번, external wall 120 s, collection 1, exit 0과 `1 passed`만 PASS다. 별도
`--version`, collection-only, 예행 node, retry, full suite, 원본 SPD와 solver는
실행하지 않는다. PASS면 결과를 두 기준 문서에 기록하고 V3 계약을 다시 열 수 있다.
collection/failure/timeout/interrupt면 `DONE / STOP_V1R_<CAUSE>`로 닫고 재실행하지
않는다.

실제 V1R은 contract commit `8668bece9230227105d5f526e6048fd4adbcf023`의
깨끗한 main에서 실행됐다. Python 3.12.10 / pytest 9.0.3, collection 1, exit 1,
pytest wall `1.78 s`, process wall `2.669 s`다. 제품 census 두 번과 그 앞의
deterministic/report assertions는 통과했지만, 이후
`build_source_plane_ownership_ir(missing_witness)`가
`SOURCE_PLANE_OWNERSHIP_IR_CONTACT_INVALID: contact island/component/layer relation is invalid`
로 실패했다. 이 builder 호출은 의도한 `pytest.raises` 밖에 있어 product
`selected conductor layers are absent` guard에는 도달하지 않았다.

판정은 `DONE / STOP_V1R_TEST_FIXTURE_IR_RELATION`이다. 이것은 제품 census FAIL이나
PASS가 아니며 부분 통과를 재사용하지 않는다. V1R은 consumed/no-rerun이고 V3는
계속 blocked다. test-only successor를 자동 생성하지 않으며, 다음 허용 작업은 해당
변이의 필요성·최소 수정·새 실행 예산을 정적으로 재평가해 별도 계약으로 동결하는
것뿐이다.

### D-085 — A2S invalid mutation deletion — DONE / STOP_V1S_ALIAS_FIXTURE_NO_TARGET

Sol 정적 재평가 결과 successor는 필요하다. `missing_witness` block은 여러 IR 관계를
동시에 고쳐야만 builder를 통과하므로 focused product guard 하나를 위해 유지할 가치가
없다. `selected conductor layers are absent` guard는 defense-in-depth로 제품에 그대로
남긴다.

허용 diff는 `tests/test_source_plane_patch_consumer.py`의 해당 block 전체 삭제뿐이다.
제품 코드, `ownership_data`/`deepcopy`, altered-dielectric와 synthetic-alias 검사,
helper, fixture, 새 test node는 바꾸지 않는다. Luna 수정 뒤 새 test SHA-256을 이
문서에 기록하고 Sol `STATIC_ACCEPT`를 받은 뒤 별도 exact successor를 한 번만 연다.

successor는 D-084와 같은 Python 3.12.10 / pytest 9.0.3 및 exact one-node command,
fresh process 1회, external wall 120 s를 사용한다. collection 1, exit 0, `1 passed`만
PASS다. preflight, retry, full suite, 제품 변경, V3는 금지한다. 실패하면
`DONE / STOP_V1S_<CAUSE>`로 닫고 부분 결과를 재사용하거나 재실행하지 않는다.
사용자의 standing preapproval은 이 별도 문서 계약에 한해 successor 실행 권한으로
적용한다.

Luna는 contract base `2ad4a6b`에서 지정된 `missing_witness` block 14줄만 삭제했다.
추가 line, 제품/helper/다른 test 변경은 없다. source SHA-256은
`63BEA32E1184154539ABD7DFE6B54555131888393FC04E5E12892E9EB730C710`로 유지되고,
새 test SHA-256은
`84AEF240893DA8906D3B1D01CD276A0BF6FD4575AC3455CEE9D76477F30C668B`다. runtime은
Sol 최종 정적 검토 전까지 `not_run`이었다. 정적 검토는 P0/P1/P2 0으로
`STATIC_ACCEPT`했으며 제품 source
불변, test 14줄 삭제만 포함, 남은 fail-closed 변이와 solve/P1 traps 유지를 확인했다.
후보는 commit `0ed64089764d8aa1cb397279a1fb1bfb4f7b5d34`로 먼저 고정했다.

동결한 Python 3.12.10 / pytest 9.0.3 exact one-node command를 fresh process에서 한 번
실행했다. collection 1, exit 1, pytest `1 failed in 1.72s`, external wall 2.47855 s다.
제품 census 두 번과 그 앞 assertions 및 altered-dielectric fail-closed 검사는 통과했다.
이후 test-only synthetic-alias 후보 탐색이 `alias_target = None`으로 끝나 line 750의
`assert alias_target is not None`에서 실패했다. 제품 guard 실패가 아니며 제품 변경도
없다. 계약대로 재실행하지 않고 V3를 열지 않으며 부분 통과도 acceptance로 재사용하지
않는다. 다음 허용 작업은 이 alias 변이의 필요성과 더 작은 검증 경로를 정적으로
재평가해 별도 계약으로 동결하는 것뿐이며, 그 재평가는 아래 D-086으로 완료됐다.

### D-086 — A2T fixture-dependent alias test prune — DONE / STATIC_ACCEPT

Sol caller 추적 결과 census의 현재 직접 호출자는 focused test 하나이고 함수는 향후
V3용 공개 seam이다. `incident endpoint alias lacks direct layer witness` guard는 reduced
closure의 alias를 다른 physical surface의 selected rail row로 오분류하지 않게 하는
trust-boundary이므로 제품에 유지한다. 현재 synthetic mutation은 MINI-SPD에 특정
비선택 topology가 우연히 존재해야 하므로 적절한 단위 검증이 아니다. helper 추출,
복합 fixture와 새 direct unit은 이 단계의 증명 범위를 넘는다.

허용 diff는 `tests/test_source_plane_patch_consumer.py`에서 `island_by_id`로 시작해
synthetic alias-error `pytest.raises`로 끝나는 동적 탐색·class monkeypatch block 전체
삭제뿐이다. 실제 diff는 추가 0줄, 삭제 51줄이다. 제품 guard/source, fixture, helper,
altered-dielectric 검사, 마지막 raw-manifest tamper와 solve/P1 traps는 바꾸지 않는다.
Luna가 삭제했고 Sol이 제품 source 불변과 exact deletion을 정적으로 확인했다. 제품
source SHA-256은
`63BEA32E1184154539ABD7DFE6B54555131888393FC04E5E12892E9EB730C710`, 새 test SHA-256은
`3F238C94B817900D0D5164C5044F9496BD171D3A8364BBFA12EB0B1E7D2F79D6`다. 이 단계는
test/import/build를 실행하지 않았고 successor runtime도 만들지 않았다.

Sol `STATIC_ACCEPT` 뒤 A2를 `DONE / ACCEPT_NARROW_CORE_EVIDENCE`로 닫았다. claim은 이미
V1S에서 실행된 synthetic MINI-SPD deterministic census, material provenance,
altered-dielectric fail-closed와 no-P1/solve뿐이다. alias guard의 동적 branch, focused
node 전체 PASS, 원본 SPD completeness와 PowerSI 수치 개선은 명시적으로 미증명이다.
그 뒤에만 원본 SPD 1회 source-only compile/census용 별도 V3 계약을 검토·동결한다.

Sol 최종 판정은 `STATIC_ACCEPT`, P0/P1/P2 0이다. 남은 focused test는 구문·논리상
연속이고 마지막 raw-manifest tamper도 보존됐지만 V1S 실패 지점 뒤였으므로 이번 A2
acceptance의 실행 증거로 주장하지 않는다. V1S/A2S 상태는 계속 STOP이며 focused node
전체 PASS로 바꾸지 않는다.

### D-087 — original-SPD source-block census V3 contract — DONE / STOP_V3_LAUNCHER_COORDINATOR_SHA_CASE

목적은 A1의 단일 block `RAIL_REACHABLE_DIELECTRIC_GAP_MAXWELL_GC`에 필요한 원본-SPD
source data와 old-Maxwell owner ledger가 production topology에서 완전한지 한 번 census하는
것이다. PowerSI 근접 정확도를 향한 다음 물리 단계의 입력 gate일 뿐 수치 개선 실행은
아니다.

기존 artifact는 재사용하지 않는다. W6 candidate는 raw-spatial v2이고, 17DT candidate
`D:\SPD-Decap-PI-Evaluator-W7\2928ca73ffa0d0d1421cd393939b6fea1d025f42\260729-17dt-raw-spatial-v3\S4LB002-2Para_260729_1_injected_candidate.spdpi`
(911,542,390 B, frozen SHA-256
`FBE6ABEB5655918134ECB47235EDFD3B81B891ED5EC95545E03C9651937C6BCC`)는 raw v3와
compiled topology만 가지며 target ownership IR이 없다. 현재 raw-v3, ownership-IR,
substrate identity를 같은 import에 결속하려면 원본 fresh import가 필요하다.

입력 identity:

| 항목 | 동결값 |
|---|---|
| SPD | `D:\S4LB002-2Para_260729_1_injected.spd`; 1,116,717,287 B; SHA-256 `40CB44B2376F59D6B606EB9B4D138204FE51B2DC6B3332D3B7C0E7D4202866D2` |
| rail / pair | `ADC_VDD_180_VQPS_SYS_1_AON/0`; `Signal$L30(OTHER_POWER1)` / `Signal$L29(DGND)` |
| W6 manifest | `D:\SPD-Decap-PI-Evaluator-W6\fb36288781dcc0b884950ef5a486c474090ceebd\260729\run_manifest.json`; SHA-256 `2B14F90E762ABC49833518137812E8FC97FCDE0E9C145795B7384210CFD9F5DE` |
| W6 sidecar | 같은 root의 `accuracy_sidecar.json`; SHA-256 `0D103E0AD47DF80641FAC0952A35A6EAA56CC9FDB71BE24661903E451926E932` |
| W6 correlation | 같은 root의 `correlation\correlation_report.json`; SHA-256 `969E40046E3A099D09557BA7500693460362336D76B067962436BD3B5177ABC4` |
| product census source | `src/spd_decap_pi/source_plane_patch_consumer.py`; SHA-256 `63BEA32E1184154539ABD7DFE6B54555131888393FC04E5E12892E9EB730C710` |

외부 coordinator는
`D:\SPD-Decap-PI-Evaluator-W7\_coordinator_temp\a2_v3_source_block_census_once.py`
한 파일만 허용한다. 새 product module/schema/dependency/test와 기존 script 수정은 0이다.
Luna는 검증된 EVIDENCE-05 parent/child receipt·termination 구조만 재사용하되 P1/owner-join을
제거하고 아래 세 API stage만 남긴다. 별도 compile, self-test, import preflight와 원본
접근은 금지한다. 최종 coordinator는 41,601 B, SHA-256
`DC6130ECD6F6954E49B14CBBA0D3F85B02C455B9286B5903EF89960DA772ADA7`이다. Sol 최종
정적 검토는 `STATIC_ACCEPT`, P0/P1/P2/P3 0이며 실행·import·compile·test는 없었다.

D-087 execution contract HEAD는 문서 3.2 커밋
`0652f81924d1c4df1da1c9f21f190e7f0086ae3e`였다. launcher가 시작 시 같은 값을
`--contract-head`와 output root에 결속했고 coordinator terminal도 이 contract를 기록했다.

D-087에 허용됐던 parent 명령은 다음 한 번뿐이었고 이미 소비됐다.

```powershell
$contractHead = git rev-parse HEAD
& 'C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe' -I -B `
  'D:\SPD-Decap-PI-Evaluator-W7\_coordinator_temp\a2_v3_source_block_census_once.py' `
  --repo 'C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator' `
  --contract-head $contractHead `
  --coordinator-sha 'DC6130ECD6F6954E49B14CBBA0D3F85B02C455B9286B5903EF89960DA772ADA7' `
  --root "D:\SPD-Decap-PI-Evaluator-W7\$contractHead\260729-a2-v3-source-block-census-01"
```

한 product child에서 호출 순서는 다음과 같고 각각 정확히 한 번이다.

1. `import_spd_scenario(source, include_plane_sheet_payload=True, source_plane_ownership_rail_id=rail, ...)`
2. `compile_layerwise_substrate(project, attachments, required_rail_id=rail, require_plane_sheet_payload=True, ...)`
3. `audit_source_plane_source_block_census(ownership_manifest, attachments, raw_manifest, substrate, rail_id=rail)`

call ledger는 import/compile/census `1/1/1`, P1/solve/PowerSI `0/0/0`, retry 0만 PASS다.
scenario save/reload와 `.spdpi` 생성도 금지한다. interpreter는
`C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe -I -B`, parent launcher
1개와 product child 1개다. parent wall 14,400 s, child peak working set 24 GiB, exclusive
TMP/scratch 8 GiB, 시작 temp free 16 GiB, product report 1 MiB, receipt 64 KiB가 상한이다.

path/size/hash 검사는 별도 preflight가 아니라 같은 V3 stage 0이다. SPD SHA는 시작 때 한
번만 계산하고 종료 때 size/mtime 불변을 확인한다. W6 세 파일, exact main HEAD/tracked
clean, product source hash와 output 부재도 stage 0에서 확인한다. 하나라도 다르면 V3
예산은 소비되고 STOP이다.

새 absent output root는
`D:\SPD-Decap-PI-Evaluator-W7\<execution-contract-head>\260729-a2-v3-source-block-census-01`
이다. PASS 때만 canonical product report `source_block_census_report.json`을 만들고,
root 소유권을 얻고 terminalization에 성공한 PASS/STOP은 sibling
`source_block_census_receipt.json` 하나를 남긴다. receipt는 schema, status/stage/contract,
source/W6/block/rail/pair, call ledger, raw/ownership/substrate/query/report identities, row
count와 rows/candidate/retained/excluded/ledger hash, wall/peak/scratch cap, report path/size/SHA
또는 STOP exception type/message를 포함한다. parent가 timeout/resource kill하고 child 종료가
확인된 경우에는 STARTED receipt를 terminal STOP으로 종결한다.

PASS는 census `status=complete`, shadow-only true, replacement/production-ready false,
정확한 pair, nonempty candidate, count/hash 자체일관성, owner ledger와 replaced scopes,
1 MiB report 및 call ledger를 모두 만족할 때뿐이다. identity/resource/product guard 실패는
`DONE / STOP_V3_<CAUSE>`이며 재실행·부분 재사용·현장 수정은 없다.

잘못된 output-root binding 또는 이미 존재하는 root는 coordinator가 그 root의 소유권을
얻기 전 stdout-only `STOP_PREFLIGHT`로 거부하며 기존 경로를 건드리지 않는다. 소유한
STARTED receipt의 terminal 교체가 실패하면 우회 overwrite 없이 STARTED를 보존하고
stdout `STOP_REPORT_FINALIZATION_FAILED`로 닫는다. 종료되지 않은 child가 남으면 report,
scratch와 receipt를 건드리지 않고 즉시 STOP한다. 이 세 경우는 durable terminal receipt를
강제로 만들기보다 외부 소유물과 실행 중 자료를 보존하는 fail-closed 경계 예외다.

PASS claim은 선택 rail/pair의 원본 raw-v3, ownership IR, source-only compiled Maxwell row
census, Dk/Df provenance와 replaced/retained ledger completeness뿐이다. G/C replacement,
analytic accuracy, solver/`Y_global`/`Zii`, PowerSI 개선, holdout/generalization과 release는
미증명이고 현재 수치 개선은 0이다. fresh ownership import 완료 여부, 24 GiB/4시간 내
compile 완료, 실제 alias-witness guard와 1 MiB report 적합성은 이 한 번의 결과로만
판정한다.

D-087 exact execution HEAD는 `0652f81924d1c4df1da1c9f21f190e7f0086ae3e`였다. parent
invocation 1회는 약 0.70 s 뒤 exit 1, stdout `STOP_PREFLIGHT`로 끝났다. exception은
`ArgumentError: --contract-head and --coordinator-sha are required`였지만 terminal의 contract는
정확한 HEAD였다. 실제 원인은 문서 명령의 대문자
`DC6130ECD6F6954E49B14CBBA0D3F85B02C455B9286B5903EF89960DA772ADA7`가 coordinator
`_hex64`의 lowercase-only 검사를 통과하지 못한 것이다.

관측 call ledger는 import/compile/census/P1/solve/PowerSI/report 모두 0이다. parent argument
gate에서 끝났으므로 source 접근, output-root 소유권 획득, receipt/report 생성은 없었고 exact
root도 absent다. 따라서 제품/source-census FAIL이 아니며 raw/ownership/substrate/ledger,
성능과 수치 정확성에 관한 새 증거는 0이다. 동일 D-087 명령과 동일 execution budget은
소비됐고 재실행하지 않는다.

### D-088 — V3R lowercase-SHA launcher recovery — DONE / STOP_V3R_IMPORT_OWNERSHIP_TERMINAL_CONTRACT

`W7-ACC-SOURCE-BLOCK-ORIGINAL-SPD-V3R-01`은 D-087 retry가 아닌 별도 one-shot으로
정확히 한 번 소비됐다. execution HEAD는 `d53ca9cccae7b824fb0cf3076c103d0fee08ab73`,
root는 `D:\SPD-Decap-PI-Evaluator-W7\d53ca9cccae7b824fb0cf3076c103d0fee08ab73\260729-a2-v3-source-block-census-01`이다.
`source_block_census_receipt.json`은 2,685 bytes, SHA-256
`9c415b58839a3a588c02cadb6adcc3a28f5cc389a4bc8e5314399e2012a7fb35`이며
status/stage/disposition은 `STOP`/`STOP_UNEXPECTED`/`STOP_UNEXPECTED`다.

예외는 import-stage fail-closed의 `SpdImportError` —
`SOURCE_PLANE_OWNERSHIP_IR_TERMINAL_INCOMPLETE: finite edge owner set is absent` —
다. call ledger는 import=1, report=1, compile=0, census=0, p1=0, solve=0,
powersi=0, retry=0, `report.present=false`다. 원본 SPD source-block census는 import에서
중단되어 numerical/product/data completeness 증거와 PowerSI 격차는 변하지 않았다.
동일 D-088은 재실행하지 않는다. 종료 직후 수행한 정적 diagnosis/replanning에서 Sol
verdict는 **D-088 acceptance REJECT**이며
receipt는 유효한 fail-closed STOP으로 인정됐다. P1 evidence는
`src/spd_decap_pi/spd_adapter.py:8149-8210, 9146-9152`에서 projected certificate가
target-anchor first edge를 생략할 수 있는데 callback은 이후 이를 요구하는 점과,
`tests/test_finite_via_layerwise.py:328-350`의 upstream finite-via가 direct-trace
source-node null edge/owner를 허용하는 반면 `source_plane_ownership_ir.py:359-384,
742-747`의 ownership IR은 Via/edge/owner를 강제하는 cross-IR mismatch다. 이는
parser/IR representation mismatch이며 원본 SPD data 부재의 증거가 아니다. receipt에는
role/pin/edge/path-kind가 없어 특정 production row를 어느 P1이 만들었는지는 주장하지
않으며, observed message는 projected-edge absence와 일관될 뿐이다.

D-089 `W7-ACC-TERMINAL-PATH-KIND-OWNERSHIP-IR-01` / `TERMINAL_PATH_KIND_OWNERSHIP_IR_CONTRACT`는
`DONE / ACCEPT_FOCUSED / COMMITTED @ 54c87d1`로 닫혔다. 기존 `via_record_required`로
conventional=1의 strict Via/edge/retained-owner와 direct-trace=0의 exact NULL
Via/edge/owner를 결속했고 authoritative landing identity는
`(via_id, external_endpoint_node_id)`다. target-only conventional first edge/endpoints/
owner-series projection과 boundary coverage ledger 불변을 확인했다. product 3개와
focused test 3개 파일만 변경했으며 schema/version/dependency/solver/P1/PowerSI 변경은
없다. producer 2 nodes는 `2 passed in 1.57s`, 관련 4 focused nodes도 PASS했다.
현재 수치 개선은 0이고 solver/P1/PowerSI는 실행되지 않았다. D-090에서 원본 SPD import
1회가 실행됐지만 source-block census는 fail-closed STOP으로 종료됐다. D-090은
`DONE / STOP_D090_OWNERSHIP_TERMINAL_MULTIBRANCH_CARDINALITY`로 종료됐다. acceptance는
REJECT이나 receipt는 valid fail-closed STOP이다. execution HEAD는
`7d7bac4f82546f27a84b6b3d7d4ae7c5bd5a00e8`, root는
`D:\SPD-Decap-PI-Evaluator-W7\7d7bac4f82546f27a84b6b3d7d4ae7c5bd5a00e8\260729-a2-d090-source-block-census-01`,
receipt `source_block_census_receipt.json`은 2,696 bytes/SHA
`5d6eb3fded5f53491a6d0601c67932e9f92ddbf5a4599ea32a1f7274a0d53f1d`이며
status/stage/disposition은 `STOP`/`STOP_UNEXPECTED`/`STOP_UNEXPECTED`다. 예외는
`SourcePlaneOwnershipIRError: target rail terminal chain must contain complete power and ground`이고
call ledger는 import=1/report=1, compile/census/P1/solve/PowerSI=0, retry=0,
`report.present=false`다. Producer는 모든 Device branch의 power/ground를 보존하고 mapping
validator는 복수를 허용하지만 spool line 753–755가 exact `(2,1,1)`만 허용해 생긴
representation/validator mismatch이며 원본 SPD data 부재가 아니다. receipt에 actual N은
없으므로 주장하지 않는다. 동일 D-090은 재실행하지 않는다.

### D-091 — W7-ACC-OWNERSHIP-TERMINAL-MULTIBRANCH-CARDINALITY-01 — DONE / ACCEPT_FOCUSED / COMMITTED @ 6b88c3c

whitelist는 `src/spd_decap_pi/source_plane_ownership_ir.py`와
`tests/test_source_plane_ownership_ir.py`뿐이었다. streamed spool exact-two를 mapping과
같은 casefold nonempty/power≥1/ground≥1/total=p+g로 정렬하고 rows/order를 보존했으며
기존 fail-closed/schema/adapter/consumer는 불변이다. product 1개와 test 1개 파일만
변경했다. 첫 exact node는 `1 failed in 0.67s`였는데 negative fixture role 삭제 뒤 ordinal
gap으로 spool `ORDER_INVALID`가 cardinality보다 먼저 발생한 test-only 문제였고 positive
4-row mapping/spool 경로는 해당 지점까지 통과했다. ordinal 재열거만 수정한 뒤 동일 node는
`1 passed in 0.82s`였고 Sol은 `ACCEPT_D091_FOCUSED`, P0–P3는 0이다. 원본 SPD/full
suite/import/solver/P1/PowerSI는 실행하지 않았고 수치 개선은 0이며 D-090은 no-rerun이다.
후속 D-092 production one-shot successor contract는 contact/terminal owner coverage
partition 불일치로 `DONE / STOP_D092_CONTACT_TERMINAL_OWNER_COVERAGE_PARTITION`으로
소비됐다. D-093 focused successor는 `DONE / ACCEPT_FOCUSED / COMMITTED @ bebbb80`로
닫혔다. D-094 source-block census는
`DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION`으로 소비됐고 D-095 focused
successor는 `DONE / ACCEPT_FOCUSED`로 닫혔다. D-096 source-block census는
`DONE / ACCEPT`로 완료됐고 D-097 geometry manifest가 `ACTIVE / not_run`으로 열린다.

### D-092 — W7-ACC-SOURCE-BLOCK-ORIGINAL-SPD-D092-01 — DONE / STOP_D092_CONTACT_TERMINAL_OWNER_COVERAGE_PARTITION

Coordinator는 `D:\SPD-Decap-PI-Evaluator-W7\_coordinator_temp\a2_d092_source_block_census_once.py`이며
41,620 bytes, SHA-256 `d204d383b8d3f6bb1edb2c9918b00e2589ed7c24f628139413ba3ddd1be378ec`이다.
Sol 판정은 `STATIC_ACCEPT_D092_COORDINATOR`다. P0–P3는 0이다. 현재 source hashes는 adapter
`c8c3b284801e3ef91649fea70db97dad1a561f37008f95d894ea81fa4afa146b`, ownership IR
`dea3c94d05033ec3c5691ad0b51e41c708eacdf49767f7011a167e65c8847571`, consumer
`21ecabbe53d9135706ce853143306976a4a652e1be42accbd77671af10938ca4`다.

D-087의 source/W6/rail/pair/budget/call-ledger/fail-closed 계약을 상속한다. 실행 계약 값은
GATE/OUTPUT_SUFFIX만 변경했고, identity label인 docstring/prog까지 포함해 총 4줄만 변경했다.
D-090 coordinator/root는 불변이며 재사용하지
않는다. exact contract-head는 당시 READY 문서를 commit한 뒤의 main tracked-clean HEAD이고
root는 `D:\SPD-Decap-PI-Evaluator-W7\<contract-head>\260729-a2-d092-source-block-census-01`이다.
PowerShell 단일 명령은 다음과 같다.

```powershell
$contractHead = (git rev-parse HEAD).Trim()
$coordinatorSha = 'd204d383b8d3f6bb1edb2c9918b00e2589ed7c24f628139413ba3ddd1be378ec'
& 'C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe' -I -B `
  'D:\SPD-Decap-PI-Evaluator-W7\_coordinator_temp\a2_d092_source_block_census_once.py' `
  --repo 'C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator' `
  --contract-head $contractHead `
  --coordinator-sha $coordinatorSha `
  --root "D:\SPD-Decap-PI-Evaluator-W7\$contractHead\260729-a2-d092-source-block-census-01"
```

PASS ledger는 import/compile/census/report `1/1/1/1`, P1/solve/PowerSI `0/0/0`, retry `0`이며
STOP이면 no-rerun이다. 이 계약은 이후 아래의 단일 실행으로 소비됐고 수치 개선은 0이다.
장시간 실행 확인은 1분 polling 없이 최소 5분 long wait 또는 종료·오류 이벤트 때만 한다.

D-092 one-shot은 exact contract HEAD `ebd143b13a80056d15701b536ea7e4993176229a`에서
정확히 한 번 실행됐고 root는
`D:\SPD-Decap-PI-Evaluator-W7\ebd143b13a80056d15701b536ea7e4993176229a\260729-a2-d092-source-block-census-01`이다.
`source_block_census_receipt.json`은 2,688 bytes, SHA-256
`c5fe8e22abc079d059eaf8f232cce650083a91b2be0dd3032f93d6de4899427e`이며
status/stage/disposition은 `STOP`/`STOP_UNEXPECTED`/`STOP_UNEXPECTED`다. 예외는
`SourcePlaneOwnershipIRError: contact owner coverage differs from retained finite-via owners`다.
call ledger는 import=1/report=1, compile/census/P1/solve/PowerSI=0, retry=0,
`report.present=false`, elapsed는 약 4993.576 s다. Acceptance는 REJECT지만 receipt는
유효한 fail-closed STOP으로 인정한다. 실제 owner ID/count는 claim하지 않으며 원본 data
부재나 coordinator 문제도 아니다. producer retained set
`R = actual contact-owner set B ∪ terminal-only first-edge owner series T`를
보존했으나 mapping+spool은 `R==B`를 요구했다. Contact row는 실제 physical port이므로 synthetic
추가는 금지되고 naive
subset은 fail-open이다. 동일 D-092는 consumed/no-rerun이다.

### D-093 — W7-ACC-CONTACT-TERMINAL-OWNER-COVERAGE-PARTITION-01 — DONE / ACCEPT_FOCUSED / COMMITTED @ bebbb80

whitelist는 `src/spd_decap_pi/source_plane_ownership_ir.py`와
`tests/test_source_plane_ownership_ir.py`뿐이다. invariant는
persisted finite-via invariant는 `R = B ∪ T`이며 `T`는 terminal-edge-proven retained
owners다. terminal proof는 persisted
`terminal_bindings.via_record_required=1`, persisted casefold
`terminal_bindings.(finite_edge_id, rail_id, island_id) == retained_owner_refs.(edge_id, rail_id, island_id)`,
retained `retained_owner_refs.owner_kind=via`에 bound된 모든 `T`만 인정한다. 즉
conventional `via_record_required=1` terminal의 exact casefold triple에 bound된
`owner_kind=via` retained refs만 `T`이며 direct-trace `via_record_required=0`는 proof가 아니다. contact checks,
uniqueness/schema/adapter/consumer/caps/deps/version은 불변이다. 기존 schema는
persisted owner accounting을 증명하지만 ordered upstream quotient series는 증명하지
않는다. implementation은 IR와 focused test만 변경했다. 첫 exact node는
`1 failed in 0.51s`였으나 SQLite compressed/uncompressed asset를 byte-identical로
요구한 test-only assertion 오류였고 product logic은 성공했으며 manifest 차이는
physical artifact hash/size뿐이었다. assertion을 `logical_rows_sha256`와 counts parity로
바꾼 뒤 같은 node를 재실행해 `1 passed in 1.40s`를 얻었다. Sol static review는
ACCEPT다.

focused one-node mapping+spool parity는
`tests/test_source_plane_ownership_ir.py::test_v2_contact_coverage_partitions_terminal_series_owners`다.
positive overlap/contact와 별도 2-owner terminal edge, negative orphan,
edge/island/rail/kind mismatch 및 direct-trace를 포함한다. 원본 SPD/full suite/P1/
solver/PowerSI는 실행하지 않는다. 수치 개선은 0이며 새 diagram은 만들지 않는다.

### D-094 — W7-ACC-SOURCE-BLOCK-ORIGINAL-SPD-D094-01 — DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION

소비된 original-SPD one-shot이며 재실행하지 않는다.

Coordinator는 `D:\SPD-Decap-PI-Evaluator-W7\_coordinator_temp\a2_d094_source_block_census_once.py`;
41,620 bytes, SHA-256 `c05c8f19810e8f0b3a8ee552d5b981af665114f62b78627af408bd56105ff43c`다.
Contract HEAD는 `b46eed9fa50fe06ddaca30c5002f933407b40668`, root는
`D:\SPD-Decap-PI-Evaluator-W7\b46eed9fa50fe06ddaca30c5002f933407b40668\260729-a2-d094-source-block-census-01`이다.
Receipt `source_block_census_receipt.json`은 2,545 bytes,
SHA-256 `475b3fe4430c54ccfd41342e17c8697792933427387036915dcef79f4f1abef4`다.

Receipt는 `STOP / STOP_CENSUS / STOP_CENSUS`, elapsed `8058.36384 s`, exception
`rows hash differs`를 기록한다. Call ledger는 import/compile/census/report `1/1/1/1`,
P1/solve/PowerSI/retry `0/0/0/0`이고 report는 persisted되지 않았다. 따라서 실제 row
내용·개수·fingerprint는 주장하지 않는다. Acceptance는 REJECT지만 receipt는 유효한
fail-closed STOP이다. 수치 개선은 0이다.

정적 원인은 coordinator가 full row mapping을 hash한 반면, 기존 consumer contract는
`fingerprint`, `partial_ordinal`, `upper_reduced_index`, `lower_reduced_index`,
`classification`, `action`, `aggregation_count`의 7-field projection을 `rows_sha256`에
사용하기 때문이다. 전체 row evidence는 `final_report_sha256`에 계속 커밋된다. D-094
coordinator와 receipt는 immutable하게 보존한다.

### D-095 — W7-ACC-SOURCE-BLOCK-COORDINATOR-ROWS-HASH-PROJECTION-01 — DONE / ACCEPT_FOCUSED

D-095는 synthetic-only focused successor로 소비됐으며 재실행하지 않는다. Coordinator는
`D:\SPD-Decap-PI-Evaluator-W7\_coordinator_temp\a2_d095_source_block_census_once.py`;
42,277 bytes, SHA-256 `29e2ec27462a6382acea28b04a6f6e802cc2704816e2c0f04c6fee23074d743f`다.
단일 check는 `a2_d095_rows_hash_projection_check.py`; 4,883 bytes, SHA-256
`d8d9e60726cabf63cfea28dc0eab5be0df61bd359fe419f3a34ec67905c538c1`다. Sol은 세 label
수정 후 `STATIC_ACCEPT`했고, Python 3.12 단일 실행은 exit 0, `0.3282879 s`,
`D095 rows-hash projection contract: PASS`였다. predecessor D-094의 valid projection
digest reject, D-095 accept, full-row digest reject, missing/non-mapping `STOP_CENSUS`,
projected mutation rows-hash 실패, nonprojected mutation stale final-hash 실패를 확인했다.
Coordinator main/original SPD/product import/compile/census/P1/solve/PowerSI는 모두 0이며
수치 개선은 0이다. repo product/source 변경과 schema/adapter/consumer/caps/deps/version
변경은 없고 D-095 check는 consumed/no-rerun이다.

### D-096 — W7-ACC-SOURCE-BLOCK-ORIGINAL-SPD-D096-01 — DONE / ACCEPT

Readiness qualifiers: `PASS_SOURCE_BLOCK_CENSUS_COMPLETE / CONSUMED_NO_RERUN`.
Contract HEAD는 `23e5d3c6b43064b8fd805c234da5f0ccc86b6d4f`이며 root는
`D:\SPD-Decap-PI-Evaluator-W7\23e5d3c6b43064b8fd805c234da5f0ccc86b6d4f\260729-a2-d096-source-block-census-01`이다.
Receipt `source_block_census_receipt.json`은 3,895 B, SHA-256
`4ab8562d9c307b3aaedd54c2839f232dea492467f9fd44bd0b7881975bc9840e`; report는 17,236 B,
SHA-256 `bb2ad70bbbb5e39af6a673d29adb2e5543675e680d68491cf906aabc2c16f473`다. Elapsed는
`8286.58578 s` (138m6.59s), disposition은 `PASS_SOURCE_BLOCK_CENSUS_COMPLETE`다.
Call ledger는 import/compile/census/report `1/1/1/1`, P1/solve/PowerSI `0/0/0`, retry `0`이며
최종 report `e09f7e20b50c993cc9e540589cc543ec5a651469e0240ff1cb4f1852c7ce42d7`, query
`9fd6c843765ff80789e293958b0ac5a3d7ccaa24c7144b9fcfba3c538e8f9d31`, rows
`eebfdb29b2586d77f7486eb77d8c42880e5c93d9bf87b1d134f39ea24425e5f2`, candidate set
`d00b6f92ec064224030e77cd50bd763502ca2caf0def862f0149655d784c7fb4`, retained
`53d5171dc735c40cb1b17e2bb47cc3993a41fece5e0d129bd5d11586b5e5dec7`, excluded empty
`4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`, ledger
`425777101d113e8d66dffc9f423a50641ab6536bd8b4792981464a7cbca9f54e`다.

Rows는 10개 모두 adjacent이며 candidate/retained/excluded는 `1/9/0`이다. Candidate
fingerprint는 `a96399ee2022af38d4060b5b66a1d1c246c4a9fead3ff35737857055a832a41d`,
`L29 DGND reduced2854 <-> L30 power reduced2859`, C는
`0x1.7613d08aa1b9dp-32 = 0.340221414118078 nF`다. Stack은 `20um Cu / 30um ABF-GL102 /
20um Cu`이며 equivalent overlap area는 약 `339.043 mm²`다. Source Dk/Df seven points는
`1MHz 3.4/0.0041; 1GHz 3.3/0.004; 5.8GHz 3.3/0.0044; 10GHz 3.2/0.0046;
20GHz 3.4/0.0051; 40GHz 3.3/0.0058; 60GHz 3.3/0.006`이다. numerical improvement는
0이고 flags는 `shadow_only=true`, `replacement_ready=false`, `production_ready=false`다.

판정상 이 candidate는 이미 production bulk `epsilon*A/d`와 같아 제거·재삽입은 low-band
no-op이다. 따라서 G4를 열지 않고 A1 deficit의 31% scaling도 적용하지 않는다. 알려진
fringe/nonadjacent exclusion은 정성적 경계일 뿐 0이라고 주장하지 않는다. D-096 실행과
report는 consumed/no-rerun이며 1분 polling 없이 종료·오류 event만 확인한다.

### D-097 — W7-ACC-D097-SOURCE-BOUND-CANDIDATE-GEOMETRY-MANIFEST-01 — ACTIVE / not_run

D-097 목적은 source-bound candidate geometry manifest와 oracle eligibility를 정하는 것이며
physics/oracle solve와 product network mutation은 하지 않는다. Existing raw-v3와 ownership
IR에 polygon/circle/vertex/stack/material data가 있으므로 새 DB/schema/compiler/loader/deps는
만들지 않는다. D-096은 JSON만 persist했으므로 exact geometry attachment는 D-097 focused
acceptance 뒤 별도 D-098 fresh original-SPD import에서 생성한다.

Whitelist는 `src/spd_decap_pi/source_plane_patch_consumer.py`와
`tests/test_source_plane_patch_consumer.py`뿐이다. Generic read-only
`audit_source_plane_fringe_oracle_readiness(...)`와 최소 helper만 추가하고 D-096 hardcode는
금지한다. 기존 loader/binding/owner/terminal/surface geometry와 core island split을 재사용한다.
출력은 census/fingerprint/retained ledger에 bind된 P/G island·surface·primitive·vertex·circle와
source hashes, normalized island/overlap/P-only/G-only WKB attachment/hash/area/bbox/rings/holes/
edge/corner/curve flags, stackup+Dk/Df, explicit ground-terminal provenance, crop `NOT_SELECTED`,
oracle eligibility/resource reason이다. oracle/product solve/PowerSI/replacement/production은
항상 false로 둔다. ambiguous island, hash/partition drift, unresolved analytic curve, missing
reference, unbound crop/environment, homogeneous-model mismatch, cap 초과 또는 solver call은
보수적으로 STOP/ineligible 처리한다.

Focused node는
`tests/test_source_plane_patch_consumer.py::test_source_plane_fringe_geometry_manifest_binds_exact_candidate_without_solver`이며,
deterministic positive/tamper checks와 solver/P1/PowerSI traps를 포함한다. Sol static accept 전
original SPD/full suite/test를 실행하지 않고, accept 후 이 node만 한 번 실행한다. D-097
accept/commit 뒤 별도 D-098 one-shot에서만 원본 SPD를 한 번 import해 geometry manifest/WKB를
persist하며 oracle solve는 0이다. D-098은 geometry manifest에 한정하고, 이후 번호와 범위를
동결하지 않은 별도 `manufactured/source-bound nonzero deltaC oracle gate`가 PASS할 때만
one-owner production integration을 재검토한다. 사용량 30% threshold와 long-wait 정책을 유지한다.

## 10. 중단·사용자 검토 조건

다음이면 자동 진행을 멈추고 상태와 필요한 결정을 보고한다.

- 최초 목적, W5 threshold, PowerSI fitting 금지 또는 release 범위를 바꿔야 한다.
- whitelist 밖의 architecture/schema/cap 변경이 필요하다.
- 동일 물리 후보가 두 번째 high-cost 실행을 요구하지만 원인 변경이 없다.
- 원본 SPD source data, PowerSI reference partition 또는 unseen design이 없다.
- working tree에 범위 밖 tracked 변경이 생겨 안전하게 분리할 수 없다.
- 사용량이 사용자가 지정한 30% 남음 지점에 도달한다.

D-089 `W7-ACC-TERMINAL-PATH-KIND-OWNERSHIP-IR-01`은 `DONE / ACCEPT_FOCUSED /
COMMITTED @ 54c87d1`다. D-088/V3R은 소비된 STOP이며 동일 실행은 허용하지 않는다.
A2는 `DONE / ACCEPT_NARROW_CORE_EVIDENCE`지만 focused runtime PASS는 아니며
V1/A2R/A2S와 D-087 실행은 각각 소비된 STOP이다. D-091 whitelist 밖 product/test와
coordinator 변경은 금지한다.
D-088은 hash-bound one-shot으로 소비됐고 동일 계약은 재실행하지 않는다. D-090은
`DONE / STOP_D090_OWNERSHIP_TERMINAL_MULTIBRANCH_CARDINALITY`로 소비된 STOP이며 동일
계약은 재실행하지 않는다. D-091 `W7-ACC-OWNERSHIP-TERMINAL-MULTIBRANCH-CARDINALITY-01`은
`DONE / ACCEPT_FOCUSED / COMMITTED @ 6b88c3c`이고, D-092는
`DONE / STOP_D092_CONTACT_TERMINAL_OWNER_COVERAGE_PARTITION`으로 소비된
no-rerun STOP이다. D-093은 `DONE / ACCEPT_FOCUSED / COMMITTED @ bebbb80`이며 D-094는
`DONE / STOP_D094_COORDINATOR_ROWS_HASH_PROJECTION`으로 소비됐다. D-095는
`DONE / ACCEPT_FOCUSED`이며 D-096은 `DONE / ACCEPT`다. D-097은 `ACTIVE / not_run`이다.
whitelist 밖 변경과 원본 SPD/full suite/P1/solver/
PowerSI 실행은 금지한다.
새 schema/cap 또는 계약 밖 runtime이 필요하면 즉시 STOP하고 문서를 갱신한다.

## 11. 승인된 Unicode fixture successor — DONE / ACCEPT / COMMITTED @ eece8ab

### 11.1 항목과 원인

`W7-PHYS-OWNER-JOIN-OWNERSHIP-STREAMED-IR-UNICODE-FIXTURE-RAIL-FORMATION-FIX-01`
은 `DONE / ACCEPT / COMMITTED @ eece8ab944a29a9f6c5ddde17e56de8dbbd2ee6a`다. predecessor의 broad
`Node1`→`Straße1` 치환은 Node 레코드와
`.Connect` port에 필수인 `Node` 접두사를 없애고 `Node10`/`Node11`/`Node12`까지
바꿨다. 따라서 ownership callback 전 rail formation이 실패했다. core parser와
raw compiler는 `Node` 뒤의 strict UTF-8은 허용하므로 제품 결함은 확인되지 않았다.

### 11.2 whitelist와 최소 구현

Luna가 수정할 수 있는 code/test 경로는
`tests/test_source_plane_ownership_ir_producer.py` 한 개뿐이다. predecessor SHA-256은
`0A7A09C67D13156F2AA8EBDB2E88FC287922722FB7688558B05A91935D3510DC`다.
구현 뒤 Sol이 승인한 frozen SHA-256은
`BD8B813C0ED5125F741E130E65ECC26088F5BD10E057C1BEA110EDFF51C06FA4`다.

- `_ownership_fixture_payload()`의 `Node2`가 정확히 3회인지 먼저 assert한다.
- 그 세 DGND terminal chain reference만 `NodeStraße2`로 바꾼다.
- 실제 spool query는 `kind='Node'`, `lookup_a='dgnd'`,
  `lookup_b='nodestrasse2'`를 모두 요구한다.
- exact record ID `node:NodeStraße2:DGND`, Python `record_id.casefold()`과 원본
  byte span SHA-256을 검증한다.
- parser/compiler/adapter/source-IR product, 다른 test, schema/cap과 dependency는
  바꾸지 않는다. monkeypatch로 analysis/rail/callback을 우회하지 않는다.

### 11.3 정적 gate

Luna 구현 뒤 Python 없이 Sol이 다음을 확인한다.

1. 수정 경로와 test node 수가 변하지 않는다.
2. `Node` grammar, DGND rail/net identity와 세 source reference가 보존된다.
3. actual `import_spd_scenario`→raw compiler spool→IR builder 경로를 유지한다.
4. source byte slice hash와 Python casefold oracle을 약화하지 않는다.
5. 나머지 8개 tracked candidate 파일은 §3 receipt와 byte-identical이다.

finding이 없을 때만 `STATIC_ACCEPT / READY_FOR_NEW_21_NODE_RUN`이다.

실제 Sol 최종 정적 검토는 `STATIC_ACCEPT`, P0/P1/P2/P3 모두 0이었다. producer
test 외 제품·다른 test 경로와 §3 frozen receipt는 바뀌지 않았다.

### 11.4 단일 successor 실행 계약

Sol 승인 뒤 Python 3.12 fresh process에서 §5의 ordered 21 unique nodes를
`-x -vv --tb=long`, external wall 300 s로 **한 번** 실행한다. 별도 1-node 예행,
predecessor partial PASS 재사용, full suite, 원본 SPD, solver, PowerSI와 release는
하지 않는다. 같은 node ID 목록이지만 producer test hash와 ACTIVE item이 달라진
사용자 승인 successor이므로 predecessor invocation의 재실행으로 취급하지 않는다.

| 결과 | 상태 | 다음 행동 |
|---|---|---|
| collection 21, exit 0, `21 passed` | `DONE / ACCEPT / READY_FOR_EXPLICIT_STAGE_AND_COMMIT` | 문서 포함 기존 nine paths만 명시적으로 stage/commit |
| collection/failure/timeout/interrupt | `DONE / STOP_<CAUSE>` | rerun·partial reuse·자동 fix 금지 |

실제 새 candidate 실행은 Python 3.12.10/pytest 9.0.3에서 collection 21, exit 0,
`21 passed in 15.09s`, wrapper wall 15.774693 s로 끝났다.

- stdout: `C:\Users\User\AppData\Local\Temp\spd-pi-unicode-successor-21-1b4b6ce5c5474372bbdcaed23a5de443.stdout.log`, 3,158 bytes, SHA-256 `3A1E2C821E265C34E12C39F357118B93CEED7E0F22C6624A19D73B7CF072E9A0`
- stderr: `C:\Users\User\AppData\Local\Temp\spd-pi-unicode-successor-21-1b4b6ce5c5474372bbdcaed23a5de443.stderr.log`, 0 bytes, SHA-256 `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`

invocation은 consumed/no-rerun이다. PASS와 commit 뒤에만 A1을 연다. 이 successor는 test fixture와 named
synthetic/MINI-SPD transport acceptance만 닫으며 production owner join,
`Y_global`, `Zii`, PowerSI 정확성/성능과 수치 개선을 증명하지 않는다.

명시적 nine-path implementation commit은 `eece8ab944a29a9f6c5ddde17e56de8dbbd2ee6a`다.
