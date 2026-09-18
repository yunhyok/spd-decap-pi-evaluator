# decap_search — decap 배치 탐색 (엔진 시제품 A1)

계획 `docs/engine/APPS_PLAN_2026-09-18.md` §A1. 결과 보고서 `docs/engine/APP_decap_search_REPORT.md`.
엔진(`src/spd_pi_engine`)은 import만 한다 — 물리도 수치도 건드리지 않는다.

## 1. 무엇을 하나
포트 하나의 레일에서 **마스크 |Z(f)| ≤ Z_target(f)를 만족하는 최소 decap 실장 집합**과 **사이트
중요도 순위**를 찾는다.

1. `Model.decap_basis(freqs)`로 기저를 **한 번** 만든다(W9). 이후 구성 하나 = 조밀 Schur 닫기.
2. 전(全)실장에서 시작해 **후진 제거**: 각 사이트를 혼자 뺐을 때 남는 마스크 여유
   (`min over f of Z_target/|Z|`)로 순위를 매기고(중요도), 그 순서대로 하나씩 빼 보며 마스크를
   계속 만족하면 확정한다.
3. 최종 구성과 중간 구성을 `set_decaps` + 직접 풀이로 다시 풀어 검증한다(W8).

`search.py`가 라이브러리(`Mask`, `backward_eliminate`, `validate`), `main.py`가 CLI다.
새 의존성은 없다(numpy + 엔진).

## 2. 사용법
저장소 루트에서:

```powershell
$env:SPD_PI_DATA_DIR = "D:\Downloads\examples"
$env:PYTHONUTF8 = "1"
$env:OPENBLAS_NUM_THREADS = "4"      # 닫기가 조밀 LU다. 기본 스레드면 5–20배 느리다(W9 §5-3)

python -m apps.decap_search.main `
  --spd "$env:SPD_PI_DATA_DIR\S4LB002-2Para_260729_1_injected.spd" --port Port18_SITE0 `
  --cache "...\work\engine_cache" --outdir "...\work\apps\decap_search\260729_Port18_SITE0" `
  --freqs ladder --mask-from-full 1.5 --solver cudss --fast
```

| 옵션 | 뜻 |
|---|---|
| `--freqs ladder` | 엔진 사다리(28점) 중 **100 kHz 이상 27점**(A1의 실용 구간). 또는 `--freqs 1e5,1e6,…` |
| `--mask "1e5:2e-3,1e6:1.5e-3"` | 구간별 상수 마스크(각 값은 그 주파수부터 다음 구간까지). 첫 구간 아래는 무제약 |
| `--mask-from-full 1.5` | 표 마스크 = 전(全)실장 \|Z\|의 1.5배(기본값, A1 데모) |
| `--solver cudss\|splu`, `--fast` | 엔진 `Backend`. GPU면 `--solver cudss --fast` |
| `--chunk 24` | 기저의 주파수당 RHS 묶음 크기. GPU 메모리가 모자라면 줄인다 |

산출물(`--outdir`): `search_receipt.json`(기저 영수증 + 탐색 로그 + 최종 구성 + 검증 수치),
`summary.md`, 중간물 `basis.npz`·`search_state.json`·`validate.json`. 기존 영수증은 덮어쓰지
않는다(엔진 `cli.unique_path`).

라이브러리로 쓸 때:

```python
from apps.decap_search.search import Mask, backward_eliminate, validate
basis = mdl.decap_basis(freqs)
mask  = Mask.from_full(freqs, basis.Z({}).Z, 1.5)      # 또는 Mask.parse("1e5:2e-3,…")
log   = backward_eliminate(basis, mask, freqs)          # log["final_config"] → set_decaps용 부분 dict
rows  = validate(mdl, basis, [("final", log["final_config"])], mask)
```

`python apps/decap_search/search.py`는 데이터·엔진 없이 도는 자체검사다.

## 3. 검증 계약
매 실행이 **최종 구성 + 중간 구성들 + 전(全)실장**을 `set_decaps` + 직접 풀이로 다시 풀어
기저 닫기와 비교한다(직접 풀이가 진실, W8/W9).

| 항목 | 기준 |
|---|---|
| `max \|ΔZ\|/\|Z\|` | GPU(cudss) ≤ **1e-6**, CPU(splu) ≤ **1e-9** — W9 계약(기저가 맨보드를 인수분해하므로 GPU는 1e-6에서 바닥) |
| 마스크 판정 | 두 경로의 PASS/FAIL이 같아야 한다 |
| 구성 동일성 | 두 영수증의 `decap_config_sha256`이 같아야 한다 |
| 실장이 2개 이하인 구성 | GPU 저주파 조건수 경고(W8 §4-2) → `splu`로 한 번 더 검증(미지수 15만 이하일 때, 아니면 영수증에 생략 사유를 남긴다) |

GPU에서 한 모델은 **기저용이거나 스윕용**이다(cuDSS는 모델당 RHS 폭 하나, W9 §2-1). 그래서
CLI는 탐색과 검증을 **별도 프로세스**로 돌린다(`--stage`는 내부용).

## 4. 한계
- **재순위를 하지 않는다.** 매 단계 남은 후보를 전부 다시 평가하는 정통 후진 제거는 N²/2번
  닫기다(421 사이트 ≈ 88 000회, 1.5–2시간). 여기서는 순위 1회 + 제거 1회 = **2N번**이다.
  decap 간 상호작용을 무시하므로 **최소 집합의 상계**만 준다(더 줄일 여지가 있을 수 있다).
  진짜 최소를 원하면 재순위 버전을 쓰되 비용을 감수한다.
- 비용 1은 무시한다(사이트별 비용 가중은 A1의 옵션 항목, 미구현). 필요하면 `rank` 정렬 키에
  비용을 곱하면 된다.
- 마스크는 **|Z| 상한**만 본다. 위상·공진 Q·시간영역 판정은 없다.
- 정확도는 엔진의 것이다. 영수증의 `validity`(패키지 전 포트 1 MHz 오차 중앙값 28–32 % 등)를
  그대로 싣는다 — 절대값 판정을 이 마스크로 하기 전에 그 줄을 읽어야 한다.
- 기저는 `freqs`에서만 유효하다. 마스크 평가도 같은 점에서만 한다(점 사이는 보지 않는다).
