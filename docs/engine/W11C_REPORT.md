# W11-c 보고서 — decap 시나리오 매핑 + preflight 블로커 (2026-09-19)

계획: `docs/engine/W11_PLAN_2026-09-19.md` §3, §6 행 **W11-c**.
저장소: `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator`, 브랜치
`claude/lightweight-hybrid-20260915`, 기준 커밋 `5cbed5a`(커밋하지 않음).

파일 소유권: `evaluation.py`(엔진 헬퍼만)·`tests/test_engine_scenario_mapping.py`(신규)·
`docs/engine/W11C_REPORT.md`만 수정 대상이었다. **`evaluation.py`, `engine_adapter.py`,
`scenario.py`는 0줄 변경했다** — W11-a가 구현한 `decap_config`/`cross_net_decap_refdes`/
`extra_models`/`_engine_preflight`/`_engine_comparison_batch`가 게이트 4개를 그대로
통과했고, 버그를 발견하지 못했다(§6 참고). 작업 중 다른 에이전트(W11-b)가
`engine_adapter.py`의 `solve()`와 `engine_worker.py`(취소/하트비트)를 동시에 수정한 것을
`git status`로 확인했지만 — 그 파일들은 손대지 않았다.

---

## 1. 합성 `ScenarioSpec` 매핑 테스트 (게이트 1)

`tests/test_engine_scenario_mapping.py`(신규 4 테스트). 데이터 없음(`SPD_PI_DATA_DIR`
불필요), `tmp_path` 미사용. `tests/test_spd_decap_evaluation.py`의 `_base_project`/`_decap`/
`_scenario` 헬퍼 스타일을 재사용하되, decap이 레일 사이를 이동하는 경우를 표현하려고
레일을 2개(`RAIL_VDD`/net `VDD`, `RAIL_VDD2`/net `VDD2`, 같은 PWR/GND 레이어 공유)로
확장했다. `connection_analysis=None`으로 두어 모든 decap이
`electrically_connected_refdes`에 포함되게 했다(`scenario.py:2622-2634`).

### 1-1. 상태 6종 — `decap_config` 정확한 dict

`_mixed_fixture_scenario()`: C1(정상 장착) · C2(`enabled=False`) ·
C3(`pad_state=ISOLATION_GAP`) · C4(모델 교체 M1→M2) · C5(RAIL_VDD → RAIL_VDD2로 이동) ·
C6(RAIL_VDD2 → RAIL_VDD로 이동, cross-net).

```python
engine_adapter.decap_config(scenario, "RAIL_VDD") == {
    "C1": "M1", "C2": None, "C3": None, "C4": "M2",
    "C5": None,   # RAIL_VDD 밖으로 이동 -- 엔진은 "장착 해제"로만 표현 가능
    "C6": "M1",   # RAIL_VDD2에서 유입 -- decap_config 자체는 순수 매핑이라 값을 낸다
}
engine_adapter.decap_config(scenario, "RAIL_VDD2") == {
    "C5": "M1",
    "C6": None,
}
```

### 1-2. cross-net 유입 — `cross_net_decap_refdes`

레일을 "떠난" 쪽은 개의치 않고(그냥 장착 해제), "받는" 쪽에서만 표현 불가로 걸린다.
C5(RAIL_VDD→RAIL_VDD2)는 RAIL_VDD2 기준으로, C6(RAIL_VDD2→RAIL_VDD)는 RAIL_VDD 기준으로
각각 cross-net이다:

```python
engine_adapter.cross_net_decap_refdes(scenario, "RAIL_VDD")  == ("C6",)
engine_adapter.cross_net_decap_refdes(scenario, "RAIL_VDD2") == ("C5",)
```

### 1-3. preflight 블로커 + 평가 거부 (게이트: `ENGINE_CROSS_NET_DECAP_ASSIGNMENT`)

`evaluate_scenario(scenario, "RAIL_VDD", solver_profile="hybrid_plane_pair_v1")`와
`evaluate_comparison_batch(scenario, ("RAIL_VDD",), solver_profile="hybrid_plane_pair_v1")`
둘 다 `ScenarioEvaluationPreflightError`로 fail-closed 하고, 블로커 목록에 정확히 1건의
`ENGINE_CROSS_NET_DECAP_ASSIGNMENT`(rail=`RAIL_VDD`, refdes=`C6`)가 들어 있다:

```
engine evaluation [ENGINE_CROSS_NET_DECAP_ASSIGNMENT]: this decap was reassigned
from another source net; the engine decap set is fixed by the SPD `.Connect` net
and cannot represent a cross-net move
```

### 1-4. Original vs Tuned — 정확한 dict

```python
# tuned: C4 모델 교체, C7 새로 비활성화(source_mounted=True였음), C5 레일 밖 이동
engine_adapter.decap_config(scenario, "RAIL_VDD") == {
    "C1": "M1", "C2": None, "C4": "M2", "C7": None, "C5": None,
}

original = scenario.with_baseline_captures(("RAIL_VDD",)).original_configuration("RAIL_VDD")
engine_adapter.decap_config(original, "RAIL_VDD") == {
    "C1": "M1", "C2": None, "C4": "M1", "C7": "M1", "C5": "M1",
}
```

`original_configuration()`(`scenario.py:3061`)이 `source_model_id`/`source_mounted`/
`source_rail_id`로 되돌리므로, `decap_config(original, rail)`은 SPD가 준
"as-built" 매핑과 같고 `decap_config(scenario, rail)`은 편집을 그대로 반영한다 —
두 dict가 서로 다름을 확인했다(`assert as_built_config != tuned_config`).

```
python -m pytest tests\test_engine_scenario_mapping.py -q -p no:cacheprovider
4 passed in 1.07s
```

---

## 2. 사용자 SPICE 모델 등록 (게이트 2)

스크립트 `work/engine_w11/w11c_user_model.py`(제품 API만 사용), 캐시된 260729 Port18
시나리오(`work/engine_w11/scenario_port18.json`, 2시간 임포트 1회 재사용) 대상.

`import_cap_spice`(`_core/services.py:4312`)는 프로젝트 전체를 재검증(`_validated_project_copy`)
하는데, 캐시가 시나리오만 저장하고(첨부파일 ~378개는 저장 안 함, `e2e_port18.py` 참고)
mixed-reference 인증서가 요구하는 실제 도형 첨부가 없어 실패했다 —
`rail 'ADC_VDD_075_VTRIP_SRAM/0' certificate geometry asset is not attached`.
그래서 계획의 대안("or by constructing attachments/metadata in the test")대로,
`import_cap_spice`와 엔진 `add_decap_model` 둘 다 쓰는 같은 파서
(`spd_decap_pi._core.models.spice.parse_passive_subcircuit`)로 SPICE 텍스트를 직접
파싱해 `CapModel`/`cap_model_sources`/첨부를 만들고 `model_copy`로 시나리오에 접었다
(`with_baseline_captures`와 같은 이유로 421-decap 시나리오를 재검증하지 않음).

- 대상: rail `ADC_VDD_075_VTRIP_SRAM/0`, refdes `C2001_0`(원래 모델 `CAP_0402_100NF`).
- 새 모델 `W11C_USER_CAP`(RLC 2단자 `.SUBCKT`), 첨부
  `cap_models/W11C_USER_CAP-f838a94cdc1a.lib`.
- 어댑터 단(데이터 없이): `engine_adapter.decap_config(tuned, rail)["C2001_0"] == "W11C_USER_CAP"`,
  `engine_adapter.extra_models(tuned, attachments, config) == {"W11C_USER_CAP": "<SUBCKT 원문>"}`.
- 실제 엔진 경로: `evaluate_scenario(tuned, rail, solver_profile="hybrid_plane_pair_v1")`
  (`solver="auto"` 기본값 → A2000 cuDSS), **wall 50.9 s**(<5분 예산 안).
- 워커가 `add_decap_model`로 등록하고 `set_decaps`가 성공했다는 증거 = 저장된 영수증:

| 항목 | 값 |
|---|---|
| `receipt["decap_config"]["C2001_0"]` | `"W11C_USER_CAP"` |
| `receipt_sha256` | `6196da9cb491fc3c505d5426a024dab6629d094562eaeaed88d18dbabf756d45` |
| `receipt["decap_config_sha256"]` | `5e2bc8441d42acea4641b628e8de8a6105309b93f4c948ff50ee934ead2e137f` |
| `view.cap_count` / `view.model_count` | 421 / 4 (SPD 내장 모델 3개 + 사용자 모델 1개) |

원문: `work/engine_w11/w11c_user_model.py`(스크립트), `w11c_user_model.json`(결과),
영수증 `outputs/engine-receipts/receipt_Port18_SITE0_tuned_27d81996e38f_105124.json`.

---

## 3. Build-once 증거 (게이트 3)

스크립트 `work/engine_w11/w11c_build_once.py`. `evaluate_comparison_batch`는
`_validated_scenario_attachments` + `original_configuration()`(전체 `ScenarioSpec`
재검증)를 거치므로, 이번에도 캐시에 없는 첨부가 필요했다 — 이번엔 이 보드에 mixed-reference
인증서가 있는 레일 12개(전체 92개 중) 전부의 도형 첨부가 필요했다. 실제 엔진 계산에는
쓰이지 않는(엔진은 SPD 원본을 직접 읽는다) 순수 제품 측 장부이므로, 다른 91개 레일을
버리는 대신(그러면 `partitions`/`topology_maps`가 참조하는 domain/rail_id를 전부 정리해야
함) 12개 레일 전부의 인증서/witness를 **같은 두 개의 고정 픽스처 도형**(임의 바이트,
sha256만 교차 참조가 맞으면 됨)으로 self-consistent 하게 바꿔치기했다.

대상: rail `ADC_VDD_075_VTRIP_SRAM/0`, Tuned에서 `C2002_0` 1개만 비활성화.

**워커 로그 — build 1회, config 2회** (`(N s elapsed)` 하트비트 접미사는 W11-b가 추가한
동일 단계 재통지이므로 접미사를 벗기고 중복 제거해서 셌다):

```
[ 10%] Extracting Port18_SITE0 from the SPD
[ 24%] Building the plane-pair model for ADC_VDD_075_VTRIP_SRAM/0   <- 1회
[ 43%] Solving baseline at 28 frequencies                           <- 2회 중 1
[ 70%] Solving tuned at 28 frequencies                              <- 2회 중 2
[ 99%] Writing the engine receipt
```

`build_message_count=1`, `solve_message_count=2`.

| 항목 | Original(all-mounted) | Tuned(`C2002_0` 장착 해제) |
|---|---|---|
| `decap_config_sha256` | `fc7332f0b06e33d83b9410ddac1ee1441f23541c5c39173e16f4194dc8a51ee5` | `a31076a9f7cc76b1283ba807eea930209abe5f227eefe5d8bece494924fbe2c9` |
| W11-a as-built 영수증(`outputs/engine-receipts/receipt_Port18_SITE0_as_built_27d81996e38f.json`, `tests/test_engine_adapter_reproduction.py` `configs={"as_built": {}}`) | **동일** | 다름 |
| \|Z\| @ 1 MHz (Ω) | 0.00080930951683 | 0.00080951584188 |

Original의 `decap_config_sha256`이 W11-a 재현 테스트의 as-built 영수증과 **정확히 일치**한다
(421개 decap 전부 SPD 그대로 장착된 as-built 매핑이 같은 해시로 재현됨). Tuned는 예상대로
다르다. 1 MHz에서 \|Z\| 상대 차이는 **2.57e-4(0.0257 %)** — 421개 decap 중 100 nF 1개를
빼는 정도로는 딱 이 크기의 작은 변화가 나는 게 물리적으로 타당하다(디커플링 네트워크가
크고 병렬 개수가 많을수록 decap 1개의 영향은 작다).

원문: `work/engine_w11/w11c_build_once.py`, `w11c_build_once.json`.

---

## 4. 제품 테스트 기준선 (게이트 4)

```
python -m pytest tests\test_engine_scenario_mapping.py -q -p no:cacheprovider
4 passed in 1.07s
```

```
python -m pytest tests -q --ignore=tests\engine --ignore=tests\test_audit_source_l29_l30_port_window.py -p no:cacheprovider
33 failed, 2571 passed, 5 skipped, 260 errors in 421.31s (0:07:01)
```
(`work/engine_w11/w11c_product_baseline.log`)

FAILED/ERROR 노드 이름 집합을 `work/engine_w5/product_clean.log`(W5 기준선, 31 failed +
260 errors = 291개, `work/engine_w11/w5_baseline_nodes.txt`) 대비 diff:

```
$ comm -13 w5_baseline_nodes.txt w11c_current_nodes.txt   # 기준선에 없던 새 노드
tests/test_spd_decap_gui_engine.py::test_cancel_kills_the_engine_worker_process
tests/test_spd_decap_gui_engine.py::test_selecting_an_engine_profile_disables_modal_presets_and_notices_once

$ comm -23 w5_baseline_nodes.txt w11c_current_nodes.txt   # 기준선에 있었는데 사라진 노드
(출력 없음)
```

**두 개 차이 모두 내 소유 파일이 아니다.** `tests/test_spd_decap_gui_engine.py`는 git에
전혀 커밋된 적 없는 완전히 새 파일이고(`git log` 빈 출력, `git status` `??`), 이번 작업
동안 W11-b가 동시에 편집 중인 `gui/**`·`engine_adapter.py`의 `solve()`·`engine_worker.py`
(취소/하트비트, §0 참고)와 같은 계열의 GUI-엔진 통합 테스트다(이름도 계획 §6 행 W11-b의
게이트 "콤보 4항목", "Cancel이 워커 종료"와 그대로 대응). 이 저장소는 에이전트마다 격리된
워크트리가 아니라 **공유 체크아웃**이라, 내 실행 시점에 W11-b 작업이 중간 상태로 걸렸다.
**내가 추가한 파일(`tests/test_engine_scenario_mapping.py`, 4건)은 FAILED/ERROR
집합에 전혀 나타나지 않는다** — 4 passed 전부. 사라진 노드가 없다는 것도 확인했다(기존
실패가 우연히 통과로 바뀌는, 더 걱정스러운 부작용은 없었다).

---

## 5. 계획에서 벗어난 점

- **소스 파일 변경 없음.** `evaluation.py`(엔진 헬퍼)·`engine_adapter.py`·`scenario.py`
  모두 0줄 변경 — W11-a의 구현이 게이트 4개를 그대로 통과했다.
- 게이트 2·3에서 `import_cap_spice`/`evaluate_comparison_batch`의 전체
  `ScenarioSpec`/`ProjectSpec` 재검증이 캐시된 시나리오(첨부파일 없음)와 부딪혀,
  계획이 명시적으로 허용한 대안("or by constructing attachments/metadata in the test")을
  택했다 — 엔진이 실제로 읽는 것(원본 SPD 파일, `scenario.source.path`/`sha256`)은
  건드리지 않고, 엔진이 전혀 쓰지 않는 제품 측 장부(mixed-reference 인증서의 도형
  첨부 sha256, routing obstacle 참조)만 self-consistent 픽스처로 바꿨다.
