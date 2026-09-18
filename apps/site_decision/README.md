# apps/site_decision — SITE 간 decap 미실장 판단 (A2)

`docs/engine/APPS_PLAN_2026-09-18.md` A2 시제품. SITE0/SITE1은 같은 net의 **별개 사본**(`…/0`,
`…/1`, W10 §3)이라 SITE마다 자기 레일 모델을 쓴다. 이 앱은 두 SITE를 각각 1회 빌드하고,
대상 사이트를 양쪽에서 하나씩 떼어(`set_decaps`, 재빌드 없음) **두 SITE의 |Z|가 δ 안에서
여전히 일치하는지**로 미실장 가능 여부를 판정한다.

## 실행 (저장소 루트에서)

```powershell
$env:SPD_PI_DATA_DIR = "D:\Downloads\examples"
$env:SPD_PI_WORK_DIR = "D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8 = "1"
python -m apps.site_decision.main `
  --spd "$env:SPD_PI_DATA_DIR\S4LB002-2Para_260729_1_injected.spd" --port Port18_SITE0 `
  --partner auto --cache "$env:SPD_PI_WORK_DIR\engine_cache" `
  --outdir "$env:SPD_PI_WORK_DIR\apps\site_decision" `
  --targets top10 --delta 0.05 --freqs ladder `
  --ref-npz "$env:SPD_PI_DATA_DIR\analysis\S4LB002_260729_Zdiag.npz" --solver cudss --fast
```

`--partner auto` = net이 `/0`↔`/1`만 다른 짝 포트를 헤더에서 찾는다. `--targets top10` = 용량
큰 순 10개(1 kHz 임피던스에서 환산), `--targets C7201_0,C5801_0`처럼 직접 줘도 된다.
`--freqs ladder` = LADDER 7점, `--ref-npz`를 주면 PowerSI 격자에 스냅(W8/W9/W10과 같은 7점).
`--mask 0.01` 또는 `--mask 1e5:0.005,1e8:0.02`(계단)는 선택이다.
산출물: `decision_receipt.json`(엔진 영수증 2개 포함), `summary.md`(표).

## 라이브러리

```python
from apps.site_decision import decide
decide.find_site_pair(spd, "Port18_SITE0")      # -> "Port64_SITE1" (spd_pi_engine.find_site_pair, W12-c)
decide.match_sites(rail0, rail1)                # {"mapping": {refdes SITE0: refdes SITE1}, "unmatched": [...]}
decide.match_report(rail0, rail1)               # 두 대응 규칙을 실측 비교
decide.evaluate(spd, p0, p1, targets=10, cache_dir=CACHE)["sites"]
```

## 알아둘 것 (v2, W12 API)

- **엔진 API로 옮긴 것**(2026-09-19, `docs/engine/APP_site_decision_REPORT.md` §8): SITE 짝 찾기와
  대응 규칙은 `spd_pi_engine.find_site_pair`/`match_sites`(refdes 접미사 규칙), 좌표/용량은
  `DecapSite.xy`/`.capacitance_F` — 이 앱이 `rail.ex["rail_nodes"]`/`rail.ex["models"]`를 직접
  여는 코드는 없다. `find_site_pair`는 짝이 없으면 예외 대신 `None`을 돌려준다(`evaluate()`가
  `ValueError`로 다시 감싼다). `site_curves`의 엔진 영수증은 `light=True`(구성 스윕 요약만).
  기하 기반 대응 규칙(`"geometry"`, `match_report`의 교차검증용)은 엔진에 없어 앱에 남아 있다 —
  이 설계에서는 틀린 규칙임을 이미 실측했다(아래).
- **대응 규칙**: refdes에서 레일 net과 같은 SITE 접미사(`_0`/`_1`)를 떼고 어간을 맞춘다.
  260729에서 421/421 매칭, model_id 불일치 0. 좌표 기반 규칙(같은 model_id + 최근접)은 SITE1
  배치가 SITE0의 강체 사본이 아니라서 421 중 35만 맞고 단사도 아니다 — 새 설계에서는
  `match_report`로 먼저 확인한다.
- SITE마다 **별도 프로세스**로 돈다(cuDSS 0.8은 프로세스당 `DirectSolver` 1개, W8 §4) — 이건
  `apps/decap_search`가 W12-b로 없앤 것과 다른 제약이다: 여기는 SITE0/SITE1이 **서로 다른 두
  GPU 모델**이라 여전히 프로세스를 나눠야 한다.
- 판정 규칙은 APPS_PLAN 그대로다. 마스크가 없으면 per-SITE |Z| 증가율은 **보고만** 하고
  판정에는 쓰지 않는다(`docs/engine/APP_site_decision_REPORT.md` §5).
- 데이터 없는 자체 검사: `python apps/site_decision/decide.py`.
- 엔진 정확도 범위는 영수증 `validity`가 담고 `summary.md` 끝에 그대로 찍는다.
