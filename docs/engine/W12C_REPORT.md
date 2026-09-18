# W12-c — API 인체공학(수치 무관): DecapSite 좌표/용량, SITE 짝/매칭, ladder_freqs·unique_path 공개, attach_mask, receipt(light=True)

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §W12-c. 근거: `docs/engine/APP_decap_search_REPORT.md`
§6(항목 5–8), `docs/engine/APP_site_decision_REPORT.md` §7(항목 1–3, 5–6). 원칙: 수치 모듈
(`geometry, homogenise, reference, model, solver`)은 손대지 않았다 — 이번 작업은
`api.py`/`receipt.py`/`cli.py`/`__init__.py`/`README.md` + 테스트 1개뿐이다. `Model.set_decaps(replace=True)`와
`DecapBasis.save/load(with_impedances=True)`는 이번 작업 범위가 아니다(다른 세션이 `model.py`/`decaps.py`를
동시에 고치는 중이라 파일 소유권으로 배제됨).

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/api.py` | +154/−2 | `DecapSite.xy/layer/capacitance_F/impedance()`, `Rail.site(refdes)`, `find_site_pair`, `match_sites`, `LADDER`/`ladder_freqs`/`unique_path`(이식) |
| `src/spd_pi_engine/receipt.py` | +54 | `mask_margin`, `attach_mask`, `Result.receipt(light=...)` |
| `src/spd_pi_engine/cli.py` | +27/−49(net −22) | `LADDER`/`ladder_freqs`/`unique_path` 정의 삭제, `api`에서 import(동작 동일) |
| `src/spd_pi_engine/__init__.py` | +18/−9 | 신규 심볼 8개(`LADDER`, `attach_mask`, `find_site_pair`, `ladder_freqs`, `mask_margin`, `match_sites`, `unique_path`, 및 기존 재노출) `__all__`에 추가 |
| `src/spd_pi_engine/README.md` | +18 | §9 "앱용 유틸" 신설(기존 절 번호는 안 바꿈 — §1/§4를 참조하는 외부 문서가 있어서) |
| `tests/engine/test_w12c.py` | 신규 144줄 | 데이터 없는 테스트 7개 + 데이터 기반 1개(260729, 캐시 히트) |

## 2. API 표

| 이름 | 시그니처 | 대체하는 것 |
|---|---|---|
| `DecapSite.xy` | 필드, `(x, y)` µm 또는 `None` | `rail.ex["rail_nodes"][site.node][:2]` 직접 접근 (`decide._xy`) |
| `DecapSite.layer` | 필드, `str` 또는 `None` | `rail.ex["rail_nodes"][site.node][2]` |
| `DecapSite.capacitance_F` | 필드, `float` 또는 `None`(1 kHz에서 비용량성이면 `None`) | `rail.ex["models"][mid].impedance([1e3])` 직접 접근 (`decide.capacitance`) |
| `DecapSite.impedance(freqs)` | 메서드 → `ex["models"][model_id].impedance(freqs)` 위임 | 동일 |
| `Rail.site(refdes)` | `(refdes) -> DecapSite`, 없으면 `KeyError` | `next(d for d in rail.decaps if d.refdes == refdes)` |
| `api.find_site_pair(spd_path, port)` | `-> str \| None` | `apps.site_decision.decide.find_site_pair`(SITE 쌍 없음/모호하면 예외 대신 `None`) |
| `api.match_sites(rail0, rail1, rule="refdes-suffix")` | `-> {"mapping": dict, "unmatched": list}` | `decide.match_sites`(A2가 채택한 규칙만; `unmatched`가 리스트 — `KeyError` 아님) |
| `api.LADDER` / `ladder_freqs(ref_freq=None)` | 상수 / `-> np.ndarray` | `cli.LADDER`/`cli.ladder_freqs`(이제 `__all__`에 있음; `cli`는 이걸 가져다 씀) |
| `api.unique_path(path)` | `-> Path` | `cli.unique_path`(동일 규칙, 공개) |
| `receipt.mask_margin(freq, Z, mask)` | 순수 함수, `min(Zmax(f)/|Z(f)|)` | `apps.decap_search.search.Mask.margin`과 동일 규칙, 영수증 없이도 사용 |
| `receipt.attach_mask(receipt, mask)` | in place, `mask`/`mask_ratio`/`mask_margin`/`mask_pass` 추가 | 앱마다 마스크 판정을 자기 형식으로 넣던 것 |
| `Result.receipt(breakdown_100k=None, light=False)` | `light=True`면 `decap_config`/`reference_search`/`build_info`/`stats` 생략 | 구성 스윕 앱이 매 solve마다 421개 `decap_config`를 받던 것; sha/summary(`decap_config_sha256`, `unknowns`, `wall_seconds`)는 유지, 기본값(`light=False`)은 이전과 100 % 동일 |

## 3. 설계 메모

- **`DecapSite`는 그대로 얼려 둔다.** `Rail`로의 역참조를 추가하는 대신(순환 참조, pickling·비교
  의미 변경 위험), 새 필드는 전부 기본값 `None`이고 `Rail.decaps`가 `ex["rail_nodes"]`/`ex["models"]`를
  이미 들고 있는 김에 채운다. `_model`(비교·repr 제외 private 필드)만 예외로, `impedance()`가 위임할
  대상을 들고 있으려고 넣었다 — 원래 8개 필드로 만든 동등성·해시는 바뀌지 않는다
  (`site_decision.decide`의 자체 fake 객체들은 실제 `DecapSite`를 안 쓰므로 영향 없음, `grep DecapSite(`
  결과 `api.py` 자체 말고는 생성 지점이 없음을 확인).
- **`Rail.site(refdes)`**가 과제가 말한 "Rail의 작은 헬퍼"다 — `decaps`가 이미 만든 리스트를
  `functools.cached_property` 딕셔너리로 한 번 더 인덱싱만 한다.
- **`match_sites`는 `"refdes-suffix"` 규칙 하나만 이식했다.** `decide.py`의 `"geometry"` 폴백은
  이 설계에서 421개 중 35개만 맞고 단사도 아니라고 그 파일 docstring이 직접 적어 놨다 — 엔진
  규칙이 아니라 앱이 리스크를 지고 쓰는 대안이므로 옮기지 않았다(YAGNI, 요청 범위에도 없음).
  반환을 `{"mapping":..., "unmatched":...}`로 나눈 것은 사소한 선택이지만, refdes 키와 문자열
  `"unmatched"`가 같은 dict에 섞이는 충돌 가능성을 원천 차단한다.
- **`find_site_pair`는 모호한 경우(파트너 net에 포트가 0개 또는 2개 이상)도 `None`으로 접는다.**
  과제 문구가 "없으면 None"이라고만 했지만, `decide.py`처럼 예외를 던지면 어차피 앱이 try/except로
  감싸야 하므로 한 가지 신호(`None`)로 합쳤다 — 앱이 "쌍 없음"과 "쌍이 이상함"을 구분하고 싶으면
  이 함수가 아니라 `multiport.port_rails`를 직접 봐야 한다(둘 다 드문 경우).
- **`attach_mask`/`mask_margin`은 `apps.decap_search.search.Mask`를 import하지 않는다** — 방향이
  거꾸로면(엔진이 앱을 참조) 안 되므로, 같은 규칙(피스와이즈 상수, 첫 구간 이전은 무제한)을
  `receipt.py`에 독립적으로 다시 적었다. 두 구현이 갈라지지 않는지는 `test_w12c.py`의
  `test_mask_margin`이 손으로 계산한 값(0.75)으로 고정한다.
- **`light=True`는 `_extra` 갱신 다음, 맨 마지막에 4개 키를 `pop`한다** — `DecapBasis.receipt()`
  (다른 세션 소유, `decaps.py`)가 `super().receipt(breakdown_100k)`를 위치 인자로 호출하므로
  `light`는 새 키워드 인자라 그 호출과 충돌하지 않는다.

## 4. 앱 쪽 반영 여부

과제 지시(§6): "한 줄 import 교체"만 앱에 적용하고, 아니면 앱은 그대로 두고 보고서에 적으라고 함.
**이번 세션의 파일 소유권이 `apps/`를 포함하지 않으므로(엔진 파일 + 테스트만 허용) 앱 파일은
전혀 건드리지 않았다** — 아래는 다음에 앱을 고칠 때 참고할 목록이다.

| 앱 | 가능한 교체 | 한 줄인가 |
|---|---|---|
| `apps/decap_search/main.py` | `from spd_pi_engine.cli import ladder_freqs, unique_path` → `from spd_pi_engine import ladder_freqs, unique_path` | **예** — 동작 동일(`cli`도 이제 같은 함수를 씀) |
| `apps/site_decision/decide.py` | `find_site_pair`/`match_sites`/`_xy`/`capacitance` | **아니오** — `decide.find_site_pair`는 예외를 던지고 `api.find_site_pair`는 `None`을 반환, `decide.match_sites`는 평평한 dict인데 `api.match_sites`는 `{"mapping","unmatched"}`; `evaluate()`/`match_report()`가 그 모양에 의존해 같이 고쳐야 한다 |
| `apps/decap_search/search.py` | `Mask.margin` → `receipt.mask_margin` | 아니오 — `Mask`는 `target()`/`to_dict()`도 쓰고 있어 클래스 전체를 대체해야 함 |

`README.md` §9에 같은 표를 한국어로 넣었다.

## 5. 테스트

```
$env:SPD_PI_DATA_DIR="D:\Downloads\examples"
$env:SPD_PI_WORK_DIR="D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8="1"
python -m pytest tests\engine -q -k "datafree or w12c"
```
결과: **22 passed, 1 skipped(--gpu 없음), 27 deselected, 3.9 s**. `tests/engine` 전체
`--collect-only`도 50건 정상 수집(다른 세션이 동시에 고치는 `model.py`/`decaps.py`/`solver.py`/
`backend.py`와의 import 충돌 없음).

`test_w12c.py` 8개:
- `test_ladder_freqs_shape_and_snap` — 정렬·중복 제거된 배열, 합성 그리드 스냅.
- `test_unique_path` — 없으면 그대로, 있으면 `_HHMMSS` 접미사.
- `test_match_sites_refdes_suffix` — 가짜 rail 2개, `mapping`/`unmatched` 검증.
- `test_match_sites_rejects_unknown_rule` — `"geometry"`는 `ValueError`(이식 안 함).
- `test_mask_margin` — 손 계산 0.75, 첫 구간 이전은 `inf`.
- `test_attach_mask` — `mask`/`mask_ratio`/`mask_margin`/`mask_pass` 필드, PASS/FAIL 둘 다.
- `test_result_receipt_light_field_set` — 합성 `Model`(SimpleNamespace)로 `Result.receipt()`
  풀 세트 vs `light=True`(4개 키 없음, sha/summary는 있음), 기본값 불변 확인.
- `test_site_and_find_site_pair_260729` — **데이터 기반, 캐시 히트, 솔브 없음.**
  `find_site_pair(spd, "Port18_SITE0") == "Port64_SITE1"`(문서 §W10/APP_site_decision와 일치),
  `Design.open(spd).rail("Port14_SITE0", cache).site(refdes)`의 `xy`(예: `C2401_0` →
  `(-14000.0, 4820.0)` µm, layer `Signal$TOP`), `capacitance_F`(예: `8.89e-08` F), `impedance([1e3,1e6])`
  2점 반환, 없는 refdes는 `KeyError`. 벽시계 약 1.1 s(추출 캐시 히트).

`apps/site_decision/decide.py`, `apps/decap_search/search.py`의 기존 `demo()` 자체검사도 재실행해
회귀가 없음을 확인했다(`python -m apps.site_decision.decide` → `demo OK`, `python -m
apps.decap_search.search` → `demo OK`) — 앱 코드는 건드리지 않았지만 `cli.LADDER` 등 간접 의존이
깨지지 않았는지 보려고 실행함.

## 6. 남은 것

- W12-a(기저 GPU 계약)·W12-b(기저/직접 풀이 공존)·`Model.set_decaps(replace=True)`·
  `DecapBasis.save/load(with_impedances=True)`는 다른 세션이 `model.py`/`decaps.py`/`solver.py`/
  `backend.py`에서 동시에 진행 중(같은 `docs/engine/ENGINE_PLAN_2026-09-18.md` §W12).
- 앱 쪽 반영(§4의 "예")은 다음에 `apps/` 파일 소유권이 있는 세션이 한 줄만 바꾸면 된다.
