# APP A2 — SITE 간 decap 미실장 판단 (`apps/site_decision`)

계획: `docs/engine/APPS_PLAN_2026-09-18.md` A2. 엔진(`src/`)·테스트(`tests/`)·연구 트리
(`tools/research-claude/`)는 **읽기만** 했다. 2026-09-18.

## 1. 산출물

| 파일 | 줄 | 내용 |
|---|---|---|
| `apps/site_decision/__init__.py` | 6 | 재수출 |
| `apps/site_decision/decide.py` | 326 | `find_site_pair`, `match_sites`/`match_report`, `capacitance`/`rank_sites`, `ladder`, `mask_limits`, `site_curves`(워커), `evaluate`, `demo()` |
| `apps/site_decision/main.py` | 116 | CLI → `decision_receipt.json` + `summary.md` |
| `apps/site_decision/README.md` | — | 실행법(한국어) |

데모 결과: `WORK_DIR\apps\site_decision\{decision_receipt.json, summary.md}`.
데이터 없는 자체 검사: `python apps/site_decision/decide.py` → `demo OK`(대응 규칙 2종, 랭킹,
마스크, 편차 지표를 가짜 레일로 assert).

## 2. 방법

SITE0/SITE1은 같은 net의 별개 사본이므로(W10 §3) 한 모델에 담을 수 없다. 그래서 SITE마다:

1. `Design.rail(port, cache_dir)` → `Rail.build(OPTIONS, Backend)` **1회**
   (`OPTIONS` = 변종 p: `reference="powersi-compatible"`, `FLAGS_P`, h=200/fh=50/top_h=50,
   sub=(20,10,10), fringe=True, `fine_box`는 각 레일 자기 상자).
2. 전(全)실장 `solve(freqs)` 1회.
3. 대상 사이트마다 `set_decaps({refdes: None})` → `solve` → `reset_decaps()`
   (재빌드·재프루닝 없음, W8 계약: 재빌드 대비 ≤ 1.2e-10).

두 SITE는 **각각 다른 프로세스**에서 돈다(`concurrent.futures.ProcessPoolExecutor(max_workers=1)`
를 SITE마다 새로 만든다). 이유는 cuDSS 0.8이 한 프로세스에서 두 번째 `DirectSolver`에
크래시하기 때문이다(W8 §4, W10 §4와 같은 구조).

지표(주파수 7점 전체에 대한 최댓값):

| 이름 | 식 |
|---|---|
| SITE 편차 `dev_pct` | max_f ‖Z_SITE1(f)\| − \|Z_SITE0(f)‖ / \|Z_SITE0(f)\| |
| SITE 편차(복소) `dev_complex_pct` | max_f \|Z_SITE1 − Z_SITE0\| / \|Z_SITE0\| ← **W10 §4-4가 0.345 %로 보고한 그 양** |
| per-SITE 증가 | max_f (\|Z_미실장\| / \|Z_전실장\| − 1) |

판정(APPS_PLAN A2 그대로): **대상 사이트를 양 SITE에서 떼었을 때 `dev_pct` ≤ δ이고, 마스크를
주었으면 두 SITE 모두 마스크 아래** → `unmount_ok`. 마스크는 선택(상수 또는 계단표).

대상 사이트 선정(`--targets top10`)은 decap 모델의 1 kHz 임피던스에서 환산한 용량
C = −1/(2πf·Im Z) 큰 순이다(동률은 `ex["decaps"]` 순서 유지). 260729에서
CAP_1608_10UF 6.965 µF, CAP_0603_1UF 0.776 µF, CAP_0402_100NF 0.0889 µF — 부품 명목값 순서와
같다(디레이팅된 값).

주파수는 `spd_pi_engine.cli.LADDER`(= `exp5/pipeline.LADDER`) 7점을 `--ref-npz`의 PowerSI 격자에
스냅한 값으로, W8 §4·W10 §4가 구성 비교에 쓴 것과 **같은 7점**이다:
3.02e4, 1.0e5, 3.02e5, 1.0e6, 2.51e6, 1.0e7, 1.0e8 Hz.

## 3. SITE 대응 규칙과 매칭 결과

두 후보 규칙을 260729 Port18_SITE0(`ADC_VDD_075_VTRIP_SRAM/0`) /
Port64_SITE1(`…/1`), 각 421 사이트에서 실측했다(`match_report`, 영수증 `match` 필드).

| 규칙 | 정의 | 매칭 | 미매칭 | model_id 불일치 | 단사 |
|---|---|---|---|---|---|
| **refdes 접미사**(채택) | refdes에서 레일 net과 같은 SITE 접미사(`_0`/`_1`)를 떼고 어간을 맞춘다: `C2001_0` ↔ `C2001_1` | **421 / 421** | **0** | **0** | 예 |
| 기하 | 같은 `model_id` + 중심 이동 후 최근접 (x, y) | 421 중 **35**만 refdes 규칙과 일치 | — | — | **아니오**(충돌 24) |

- refdes 규칙이 **모든 사이트를 짝짓고**(미매칭 0), 짝의 `model_id`가 전부 같다. net 이름의
  `/0`·`/1`과 refdes의 `_0`·`_1`이 같은 관례라는 것이 근거이고, model_id 일치 421/421이 그
  방증이다.
- 기하 규칙이 실패하는 이유: **SITE1의 decap 배치는 SITE0의 강체 사본이 아니다.** refdes로 짝지은
  421쌍의 (Δx, Δy)가 **20종**이다(최빈 (−690, −32143) 79쌍, 그 다음 (350, −32448) 43쌍 …).
  x 범위는 두 SITE가 [18331, 44361] µm로 같고 y만 [9186, 22957] ↔ [−22957, −9186]으로 갈라진다.
  중심 이동은 (0, −32386)으로 잡히지만 개별 쌍이 그 이동을 따르지 않으므로 최근접 매칭이
  어긋나고 24개가 같은 상대를 집는다.
- **결론**: 이 데이터에서는 refdes 접미사 규칙을 쓴다. 접미사가 없는 설계를 만나면
  `match_report`로 두 규칙을 먼저 재보고(한쪽이 미매칭 0 + 단사인지), 기하 규칙을 쓸 때는
  per-쌍 오프셋이 한 종류인지 확인한다.

## 4. 데모 (260729, Port18_SITE0 / Port64_SITE1, 10 사이트, δ = 5 %)

미지수 275 218 / 275 022, `Backend(solver="cudss", fast=True)`, 캐시 웜.

**전(全)실장 SITE 편차** = |Z| 0.3105 %, **복소 0.3452 %**(최대 지점 10 MHz).
W10 §4-4의 0.345 %와 일치한다. 주파수별(복소, %): 0.0024, 0.0079, 0.0238, 0.097, 0.1179,
**0.3452**, 0.2408 — 1 MHz 0.097 %도 W10과 같다.

| # | refdes (SITE0) | 짝 (SITE1) | 모델 | C [µF] | 미실장 SITE 편차 [%] | SITE0 Z 증가 [%] | SITE1 Z 증가 [%] | 판정 |
|---|---|---|---|---|---|---|---|---|
| 1 | C7201_0 | C7201_1 | CAP_1608_10UF | 6.965 | 0.309 | 2.67 | 2.67 | 미실장 가능 |
| 2 | C7202_0 | C7202_1 | CAP_1608_10UF | 6.965 | 0.308 | 2.67 | 2.67 | 미실장 가능 |
| 3 | C7203_0 | C7203_1 | CAP_1608_10UF | 6.965 | 0.309 | 2.67 | 2.67 | 미실장 가능 |
| 4 | C7204_0 | C7204_1 | CAP_1608_10UF | 6.965 | 0.307 | 2.67 | 2.67 | 미실장 가능 |
| 5 | C7205_0 | C7205_1 | CAP_1608_10UF | 6.965 | 0.311 | 2.67 | 2.67 | 미실장 가능 |
| 6 | C7206_0 | C7206_1 | CAP_1608_10UF | 6.965 | 0.311 | 2.67 | 2.67 | 미실장 가능 |
| 7 | C7207_0 | C7207_1 | CAP_1608_10UF | 6.965 | 0.311 | 2.67 | 2.67 | 미실장 가능 |
| 8 | C7208_0 | C7208_1 | CAP_1608_10UF | 6.965 | 0.311 | 2.67 | 2.67 | 미실장 가능 |
| 9 | C5801_0 | C5801_1 | CAP_0603_1UF | 0.776 | 0.304 | 0.30 | 0.30 | 미실장 가능 |
| 10 | C5802_0 | C5802_1 | CAP_0603_1UF | 0.776 | 0.300 | 0.30 | 0.30 | 미실장 가능 |

- **10/10 미실장 가능**(δ = 5 %). per-SITE |Z| 증가는 10 µF 사이트 2.67 %, 1 µF 사이트 0.30 %,
  둘 다 최대 지점은 최저 주파수 30 kHz다.
- 읽는 법: 미실장 시 SITE 편차(0.300–0.311 %)가 전(全)실장 편차(0.3105 %)와 **사실상 같다.**
  한 사이트를 떼는 효과가 두 SITE에서 거의 동일하게 나타나므로(증가율이 소수점 둘째 자리까지
  같다), 이 판정에서 δ를 좌우하는 것은 미실장이 아니라 **두 SITE의 기저 불일치**다.
  → §5의 단서를 함께 읽어야 한다.

### 검증

| 항목 | 실측 | 기준 |
|---|---|---|
| 전(全)실장 SITE 편차(복소) | 0.3452 % | W10 §4-4 0.345 % — **일치** |
| 전(全)실장 Z vs W10 `single_260729_Port18_SITE0.json` | max \|ΔZ\|/\|Z\| **3.54e−10** | GPU 계약 1e−8 — PASS |
| 전(全)실장 Z vs W10 `single_260729_Port64_SITE1.json` | **2.78e−11** | 1e−8 — PASS |
| 미지수 | 275 218 / 275 022 | W10과 동일 |
| 주파수 격자 | 7점 비트 동일 | W10과 동일 |
| `set_decaps` vs 재빌드 | (측정 안 함) | W8 §4-2 계약 ≤ 1.2e−10이 이미 보증 |

영수증의 `numerics_id`는 `7053d7fa5745…`로 W8 보고서의 `f3808b55…`와 다르다 — 그 뒤 W9가
`model.py`를 바꿨기 때문이고(소스 해시), Z가 W10 영수증과 1e−10급으로 같다는 위 표가 수치
불변의 증거다.

## 5. 소요 시간

| 단계 | SITE0 (Port18) | SITE1 (Port64) |
|---|---|---|
| 추출 `prepare`(캐시 적중) | 1.2 s | 1.3 s |
| `Rail.build` | 14.5 s | 18.5 s |
| 첫 `solve`(7점, cuDSS 플랜 생성 포함) | 7.5 s | 8.7 s |
| 구성당 `set_decaps`+`solve`(7점, 10회 평균) | 3.42 s | 3.19 s |
| 프로세스 합 | 57.4 s | 60.6 s |

**전체 벽시계 122.2 s = 빌드 2회 + solve 22회(2 × (1 + 10))** + 프로세스 2개 기동.
구성당 3.2–3.4 s는 W8 §4-4의 2.1 s보다 크다(같은 7점·같은 포트). 차이는 이 앱이 구성마다
`reset_decaps()`로 421개 전부를 다시 스탬프하고 매 solve마다 assemble을 다시 도는 데서 오는
것으로 보이며, 재빌드(17–18 s)에 비하면 여전히 5배 이득이다. W9 `decap_basis`의 손익분기는
P18 42구성이라 11구성인 이 앱에는 `set_decaps`가 맞다.

## 6. 판정 규칙에 붙는 단서 (결과를 보고 규칙을 바꾸지 않았다)

APPS_PLAN이 사전 등록한 규칙은 "미실장 시 두 SITE의 |Z| 편차 ≤ δ"다. 그대로 구현했고 결과는
10/10 통과다. 다만 **이 규칙은 이 설계에서 변별력이 없다**: §4가 보이듯 미실장은 두 SITE에
거의 동일하게 작용해 편차를 0.31 %에서 움직이지 않고, δ = 5 %는 그보다 16배 크다. 즉 이 규칙이
검증하는 것은 "SITE0의 판단을 SITE1에 옮겨도 되는가"(W10 §4-4의 결론과 같은 질문)이지,
"이 decap을 빼도 PDN이 견디는가"가 아니다. 후자를 판정하려면 **마스크가 필수**다(`--mask`,
A1과 같은 형식). 규칙 변경은 소유자 결정 사항이라 하지 않았고, 대신 per-SITE 증가율을 표에
항상 싣는다.

## 7. 엔진 요구사항 (앱에서 드러난 부족한 API)

1. **decap 사이트의 좌표가 공개 API에 없다.** `DecapSite`에 `node`(노드 id)만 있어서 위치를
   보려면 `rail.ex["rail_nodes"][site.node]`로 내부 dict를 열어야 한다(`decide._xy`). 기하
   매칭·클러스터링·배치 그림을 그리는 앱은 전부 이걸 한다 → `DecapSite.xy`, `DecapSite.layer`.
2. **decap의 용량이 없다.** 사이트를 "큰 것부터" 고르려면 `rail.ex["models"][mid].impedance([1e3])`
   로 제품 파서 객체를 직접 불러야 한다(`decide.capacitance`). 1 kHz에서 용량성이라는 가정도
   앱이 진다 → `DecapSite.capacitance_F`(또는 `Rail.decap_models`로 model_id→Z(f) 공개).
3. **SITE 짝을 찾는 헬퍼가 없다.** `multiport.rail_groups`는 net→포트만 주고, W10은 SITE 짝을
   *거부*하는 쪽만 구현했다. `/0`↔`/1` 짝 찾기는 3줄이지만 규칙(어느 net 접미사가 SITE인지)이
   설계 관례라 엔진에 있어야 한다 → `multiport.site_pairs(spd)`.
4. **프로세스 격리를 앱이 짠다.** cuDSS 0.8 제약 때문에 "모델 1개 = 프로세스 1개"가 강제인데,
   엔진은 그 구조를 CLI `sweep`(포트당 subprocess) 안에만 갖고 있다. 두 레일을 비교하는 모든
   앱이 같은 래퍼를 다시 쓴다 → `spd_pi_engine`에 "한 모델을 워커 프로세스에서 돌리는" 공개
   헬퍼(또는 cuDSS 핸들 해제).
5. **`Result.receipt()`가 조용히 비싸다.** freq에 100 kHz가 들어 있으면 기본값이 breakdown을
   계산한다. 22회 solve 앱에서는 `breakdown_100k=False`를 반드시 줘야 한다는 것을 문서가 아니라
   코드를 읽어야 안다 → 기본을 끄거나 `receipt(light=True)`.
6. **구성 여러 개를 도는 앱에 맞는 영수증이 없다.** 지금은 solve마다 421개 `decap_config`와
   `reference_search` 전체가 들어간 영수증이 나온다. 이 앱은 전(全)실장 2개만 싣고 나머지 20개
   구성은 자체 포맷으로 기록했다 → 구성 스윕용 경량 영수증(구성 해시 + Z만)이 있으면
   앱마다 포맷을 새로 만들지 않는다.

`Model.set_decaps` / `reset_decaps` / `Rail.build` / `Design.rail` / `multiport.port_rails`는
있는 그대로 충분했다. 엔진은 한 줄도 바꾸지 않았다.

---

## 8. v2 (W12 API) — 2026-09-19

과제: `docs/engine/W12A_REPORT.md`(§3 한 프로세스 기저+직접, §7)·`W12C_REPORT.md`(API 표) 위에서
`apps/site_decision`을 §6-1/-2/-3/-6이 요청한 엔진 API(`find_site_pair`, `match_sites`,
`DecapSite.xy`/`.capacitance_F`, `Result.receipt(light=True)`)로 옮기고, 판정이 바뀌지 않았음을
증명한다. `apps/`·`docs/engine/APP_*_REPORT.md` 밖은 건드리지 않았다(`src/spd_pi_engine`은 다른
세션이 하드웨어 사이징으로 동시에 고치는 중이라 있는 그대로 import만 했다).

### 8-1. 무엇을 옮겼나 (diff 요약)

| 대체한 앱 코드 | 엔진 API | 비고 |
|---|---|---|
| `decide.find_site_pair`(예외를 던짐) | `spd_pi_engine.find_site_pair`(짝이 없으면 `None`) | `evaluate()`가 `None`을 다시 `ValueError`로 감싼다 — 호출부 계약(예외)은 그대로, 내부만 바뀌었다 |
| `decide.match_sites(rule="refdes")`(평평한 dict) | `spd_pi_engine.match_sites(rule="refdes-suffix")`(`{"mapping","unmatched"}`) | `evaluate()`/`match_report()`가 `["mapping"]`을 읽도록 수정(W12C_REPORT §4가 예고한 그대로, "한 줄 교체 아님") |
| `decide.match_sites(rule="geometry")`, `_xy(rail)`, `_site`/`_stem` 헬퍼 | `_geometry_match(rail0, rail1)`(app-local, `DecapSite.xy` 사용) | 엔진은 `"geometry"` 규칙을 이식하지 않았다(W12C_REPORT §3, 이 설계에서 421 중 35만 맞고 단사가 아님을 이미 실측) — `match_report`의 교차검증 전용으로만 남았고, 이제 `rail.ex["rail_nodes"]`가 아니라 `DecapSite.xy`를 읽는다 |
| `decide.capacitance(rail, f=1e3)`(`rail.ex["models"][mid].impedance([f])` 직접 호출) | `DecapSite.capacitance_F` | 반환이 `dict` 컴프리헨션 한 줄로 줄었다; `None`(비용량성)을 `rank_sites`가 `-(c[r] or 0.0)`로 안전하게 처리 |
| `site_curves`의 `res.receipt(breakdown_100k=False)` | `res.receipt(breakdown_100k=False, light=True)` | §7-5가 지적한 "조용히 비싼 영수증"의 크기 쪽 — `decap_config`(421개 키)·`reference_search`·`build_info`·`stats`를 뺀다 |

`Rail.site(refdes)`는 이 앱에 자연스러운 호출 지점이 없었다(모든 곳이 `rail.decaps` 전체를
순회하지, 단일 refdes를 찾지 않는다) — 억지로 쓰지 않았다.

라인 수 (`git diff --numstat`): `decide.py` 326 → 295줄(순감 −31, +64/−95 — 매칭 로직을
다시 쓴 부분이 많아 순감보다 churn이 크다), `README.md` +11/−6. `main.py`는 변경 없음
(`evaluate()`의 반환 모양이 그대로라 `summary_md`/CLI는 손대지 않았다).

### 8-2. 재현: 260729 Port18_SITE0 / Port64_SITE1, top10, δ=5%

```powershell
python -m apps.site_decision.main --spd "$env:SPD_PI_DATA_DIR\S4LB002-2Para_260729_1_injected.spd" `
  --port Port18_SITE0 --partner auto --cache "$env:SPD_PI_WORK_DIR\engine_cache" `
  --outdir "$env:SPD_PI_WORK_DIR\apps\site_decision_v2" `
  --targets top10 --delta 0.05 --freqs ladder `
  --ref-npz "$env:SPD_PI_DATA_DIR\analysis\S4LB002_260729_Zdiag.npz" --solver cudss --fast
```

옛 산출물(`WORK_DIR\apps\site_decision\decision_receipt.json`, §4의 표)과 새 산출물
(`WORK_DIR\apps\site_decision_v2\decision_receipt.json`)을 코드로 비교했다:

| 항목 | 이전(v1) | v2 | 판정 |
|---|---|---|---|
| 전(全)실장 SITE 편차 \|Z\| | 0.310505497 % | 0.310505497 % | 일치(9자리) |
| 전(全)실장 SITE 편차 복소 | 0.345179865 % | 0.345179865 % | 일치(9자리) |
| 전(全)실장 \|Z\|(SITE0, 7점) | — | — | max rel diff **3.83e−12** (< 1e−9 요구) |
| 사이트 10개의 `dev_pct`/`verdict` | §4 표 | 동일 | **10/10 일치, 차이 0**(diff 스크립트가 하나도 못 찾음) |
| 판정 | 10/10 `unmount_ok` | 10/10 `unmount_ok` | 일치 |
| `engine_receipts[i]` 키 | 25개(decap_config·reference_search·build_info·stats 포함) | 21개(그 4개 없음) | light=True 확인, `validity`는 유지 |
| 소요 | 122.2 s (빌드 2 + solve 22) | 73.4 s (빌드 2 + solve 22, 동일 구조) | 더 빠름 — 캐시/시스템 변동으로 보인다(이 앱은 W12-b의 "한 프로세스" 최적화 대상이 아니다, §8-3) |

Z 값(`absZ_site0`, 7점)과 `dev_pct`를 1e-9 절대 오차로 비교하는 스크립트 결과: 모든 사이트,
모든 주파수에서 **차이 0에 가깝다(최대 상대오차 3.8e-12)** — cuDSS 비결정성(W12A_REPORT §4-1)의
스케일(1e-6~1e-5)보다 3~4자리 작다. 두 실행이 캐시 웜 상태에서 같은 하드웨어로 돌았고 기저를
쓰지 않는 직접 풀이라 W12-a의 비결정성 계약이 애초에 적용되지 않는다(§8-3).

### 8-3. 남은 엔진 격차 / 왜 이 앱은 "한 프로세스"가 안 되는가

- **W12-b는 이 앱에 적용되지 않는다.** `apps/decap_search`가 없앤 2프로세스 구조는 "한 모델이
  기저 빌드와 직접 풀이를 둘 다 하고 싶다"는 제약이었다. `site_decision`은 애초에 기저를 쓰지
  않고(구성이 11개뿐이라 손익분기 미만, README §5), SITE0/SITE1이 **서로 다른 두 레일의 두
  모델**이라 프로세스 분리 이유가 다르다(cuDSS 0.8: 프로세스당 `DirectSolver` 1개, W8 §4). 엔진이
  "한 프로세스에서 여러 cuDSS 모델"을 지원하게 되면(W5 §4-2의 관찰이 이미 맞다고 W12A_REPORT §2가
  확인했다) 이 앱도 프로세스 분리를 없앨 수 있지만, 그 안전성 조사(자유화하지 않은 핸들이 두
  레일 분만큼 쌓인다, VRAM)는 이번 범위 밖이다 — §7-4에 이미 적혀 있던 요구사항이고 아직
  해결되지 않았다.
- **`match_sites`의 `"geometry"` 규칙은 여전히 앱 소유다**(§8-1). 엔진에 올리자는 요청은 없다 —
  이 설계에서 틀린 규칙임을 실측했기 때문에(§3) 다른 설계를 만났을 때만 다시 켜는 진단용이고,
  엔진 규칙으로 승격할 근거가 아직 없다.
- **`Rail.site(refdes)`를 쓸 데가 없었다**(§8-1). 이 앱의 모든 순회가 `rail.decaps` 전체 위에서
  일어난다 — 필요하면 다음에 단일-refdes 조회가 생길 때 쓴다.
- §7의 나머지(4. 프로세스 격리 헬퍼, 6. 스윕용 경량 영수증)는 4는 여전히 미해결(위 항목과 같은
  이유), 6은 `light=True`로 이번에 해결됐다.
