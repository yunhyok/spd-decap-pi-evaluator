# W10 — 다중 포트 Z(f) 행렬

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §2-5(다중 포트), §1-1 C19(포트 열거원), §4 W10.
원칙 §0(수치 불변, `tools/research-claude/` 무수정). 작업 트리
`C:\Users\User\Documents\ChatGPT\worktrees\spd-engine-w10`(브랜치 `engine-w10`, 기준 5f790a6). 2026-09-18.

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/multiport.py` | **신규 436줄** | `group_ports_by_rail`/`port_rails`/`rail_groups`, `rail_node_indices`, `short_ports`, `MultiRail`, `MultiModel`, `MultiResult`, `demo()` |
| `tests/engine/test_multiport.py` | **신규 120줄** | 데이터 없는 2건 + 데이터 2건(`gpu` 1건) |
| `docs/engine/W10_REPORT.md` | **신규** | 이 문서 |
| `src/spd_pi_engine/api.py` | +15 (82–96줄), 173줄 | `Design.multiport(...)` 한 메서드만 `Design` 클래스 끝에 추가 |
| `src/spd_pi_engine/__init__.py` | +9 (41–49줄), 49줄 | 파일 끝에 `from . import multiport` + 재수출 + `__all__ +=` |

W9가 동시에 고치는 `model.py`, `solver.py`, `receipt.py`, `decaps.py`,
`tests/engine/test_reproduction.py`, `test_datafree.py`, `src/spd_pi_engine/README.md`는 **손대지
않았다**(`git status --short`가 위 4개 파일만 보고한다). `tools/research-claude/`도 읽기만 했다.

게이트 스크립트·결과: `WORK_DIR\engine_w10\{w10_gate.py, groups.json, k1_*.json, split_*.json,
finebox_*.json, single_*.json, w10_results.json}`. 캐시는 `WORK_DIR\engine_cache_w10`(기존
`engine_cache`에 쓰지 않았다. 단 pytest의 `cache_dir` 픽스처는 기존 규약대로 `engine_cache`를
읽는다 — Port18/Port14는 이미 거기 있으므로 새로 쓰는 것은 없다).

## 2. 방법 — `model.py`를 고치지 않고 k 포트를 만드는 법

`Model._build`는 `ex["port_pos_nodes"]`를 **하나의 미지수**로 단락하고 그것을 `self.P`에 적는다.
나머지 레일 노드는 각자 미지수를 가진다. 즉 **포트 A의 추출로 만든 모델에는 같은 레일의 포트 B
핀이 이미 들어 있다** — 포트가 아니라 낱개 노드로. W10은 그 뒷정리만 한다.

1. `rail_node_indices(mdl, nodes)` — `_build`가 지역 변수 `idx`로 버린 "노드 id → 프루닝 전 인덱스"를
   같은 규칙으로 복원한다(시트에 스냅되면 그 셀, 아니면 `ex["rail_nodes"]` 순서의 카운터; 카운터
   시작값은 `mdl.port`에서 역산). 요청한 노드가 모두 스냅되면 전체 레일 노드를 훑지 않는다.
2. `short_ports(mdl, groups)` — 각 그룹을 미지수 하나로 합치고 다시 번호를 매긴 뒤 `mdl.map`,
   `mdl.N`, `mdl.P` **세 개만** 덮어쓴다. `assemble`/`solve`/`breakdown`이 미지수 벡터에 닿는
   경로가 그 셋뿐이므로 메시·참조 탐색·프루닝·스탬프된 모든 소자는 `_build`가 남긴 그대로다.
   그룹 0은 모델 자신의 포트여야 하며, 그것이 `{mdl.P}` 하나로 돌아오는지가 1의 자체 검사다
   (어긋나면 `AssertionError`).
3. 주파수당 인수분해 1회, RHS k개. cuDSS 경로는 `CudssLU`가 만든 `DirectSolver`에
   `reset_operands(b=...)`만 다시 걸어 **같은 인수분해 위에서 삼각 풀이만 k번** 한다(측정:
   Port18 275 k에서 factor 0.13 s + solve 0.013 s/RHS). splu 경로는 `lu.solve(B)`로 한 번에 푼다.

> 브리핑이 제안한 "`mdl.P`를 포트마다 바꿔 `mdl.solve([f])`를 k번 호출"은 GPU에서 동작하지 않는다.
> `CudssLU.__init__`이 `P`로 RHS를 만들어 플랜에 굽고, cuDSS 0.8은 한 프로세스에서 두 번째
> `DirectSolver`에 크래시한다. 위 방식은 솔버 1개·인수분해 1회를 유지한다. W9가 `solver.py`에
> 다중 RHS 경로를 넣으면 `MultiModel.solve`의 그 6줄을 그것으로 바꾸면 된다
> (`multiport.py`의 `# ponytail:` 주석이 위치를 표시한다).

k = 1이면 재번호가 항등이라 단일 포트 경로와 **완전히 같은 모델**이 된다(§4-1).

## 3. 데이터 현실 — 같은 레일에 있는 SPD 포트는 하나도 없다

W10 브리핑은 `Port18_SITE0`와 그 SITE1 짝이 같은 레일이라고 가정했다. 아니다.

| 설계 | 포트 | 서로 다른 rail_net | 2포트 이상 그룹 |
|---|---|---|---|
| 260729 | 92 | **92** | 0 |
| 260804 | 92 | **92** | 0 |
| s5m6585 | 160 | **160** | 0 |

`rail_groups(spd)`(= `list_ports` + 포트 헤더의 net) 결과가 전부 크기 1이다
(`WORK_DIR\engine_w10\groups.json`). SITE 짝은 net 이름부터 다르다.

- `Port18_SITE0::ADC_VDD_075_VTRIP_SRAM/0`, `Port64_SITE1::ADC_VDD_075_VTRIP_SRAM/1`
- 두 net의 노드 수는 같고(52 001, 층별 분포도 동일) 좌표만 다르다. 예: `Signal$L14(MAIN_POWER4)`의
  노드 bbox가 `/0`은 y ∈ [9 086, 22 920] µm, `/1`은 y ∈ [−23 056, −9 286] µm로 **겹치지 않는다**.
  같은 구리를 나눈 것이 아니라 두 SITE의 별개 사본이다.

`spd_source.extract`는 net 이름으로 추출하므로 두 레일은 미지수를 하나도 공유하지 않고, 동결 모델의
이상 GND(변종 B)에서는 **Z₁₂ ≡ 0**이다. 0으로 채운 행렬을 결과처럼 돌려주지 않도록
`MultiRail.open`이 거부한다 — 포트 헤더만 읽고 0.9 s 만에, 추출을 시작하기 전에
(문구는 `groups.json`의 `site_pair_refused`).

그래서 k > 1 실측 케이스는 **한 SPD 포트의 핀을 두 그룹으로 나눈 것**으로 잡았다. 이것이 현재
데이터가 표현할 수 있는 유일한 동일 레일 다중 포트이고, 동시에 **가장 강한 검증**이다: 두 그룹을
다시 단락하면 동결된 단일 포트 영수증이 정확히 나와야 한다(§4-2). 분할 규칙은 SPD 순서 기준
짝/홀 교차(실행 전에 고정, 튜닝 없음) — 두 그룹이 핀 필드 전체에 섞이므로 결합이 가장 세고 폐합
검사의 여유가 가장 적다.

## 4. 검증

`WORK_DIR\engine_w10\w10_gate.py`. 모델 하나당 프로세스 하나(cuDSS 0.8 제약, W8 §4와 동일).
주파수는 exp28/p 영수증 27점 격자에서 exp5 LADDER 7점
(3.02e4, 1.0e5, 3.02e5, 1.0e6, 2.51e6, 1.0e7, 1.0e8 Hz). 옵션은 변종 p
(`ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200, fh=50, top_h=50, sub=(20,10,10), fringe=True)`).

실행 전에 고정한 게이트(`w10_gate.py` 헤더):
G-A 상반성 ≤ 1e−12, G-B 폐합 ≤ 1e−6, G-C k=1 항등 ≤ 1e−8.

### 4-1. k = 1 항등 (G-C)

260729 Port18_SITE0의 포트 핀 978개를 그룹 하나로 준 다중 포트 vs `fixtures/exp28` 영수증.

| 항목 | 값 |
|---|---|
| 미지수 | 275 218 = 영수증과 동일 |
| `fine_box` | 단일 포트 빌드와 동일 |
| max \|ΔZ\|/\|Z\| (7점) | **6.97e−11** (한도 1e−8) → **PASS** |
| 주파수별 | 6.97e−11, 3.95e−11, 9.56e−12, 4.21e−11, 2.09e−12, 2.32e−12, 1.72e−12 |

`Design.multiport(["Port14_SITE0"], ...)`(편의 메서드 경로)도 같이 확인했다: 미지수 34 424 =
영수증, max |ΔZ|/|Z| = **2.41e−11**.

`short_ports`의 재번호가 k=1에서 항등임을, 그리고 W8이 측정한 GPU 편차(1.21e−10)와 같은 급임을
보인다. 다중 포트 경로는 k=1에서 단일 포트 경로 그 자체다.

### 4-2. k = 2 (핀 분할) — 폐합·상반성·대각·결합

| 항목 | 260729 Port18_SITE0 (cuDSS) | 260729 Port14_SITE0 (cuDSS) | 260729 Port14_SITE0 (splu) |
|---|---|---|---|
| 핀 수 (A/B) | 489 / 489 | 34 / 34 | 34 / 34 |
| 미지수 | 275 219 = 단일 + 1 | 34 425 = 단일 + 1 | 34 425 |
| **상반성** max\|Z_ij−Z_ji\|/\|Z_ij\| | 1.73e−11 … 8.21e−11 | 2.35e−11 | **1.02e−15** |
| **폐합** `1/Σ(Z⁻¹)` vs exp28/p 영수증 | **2.78e−09** | **9.38e−11** | **1.02e−10** |
| 대각 vs 단일 포트 Z | 0.143 % / 0.130 % | 0.773 % / 0.745 % | 동일 |
| 결합 \|Z₁₂\|/√(\|Z₁₁\|\|Z₂₂\|) @1 MHz | 0.99918 | 0.99946 | 0.99946 |
| 빌드 / 7점×2 RHS 풀이 | 20.1 s / 7.9 s | 58.1 s(추출 포함) / 3.5 s | 45.4 s / 2.2 s |

- **G-B 폐합 PASS.** 두 그룹을 다시 단락하면(= 포트 전체를 구동하면) 동결 단일 포트 영수증이
  1e−9대로 재현된다. 같은 레일·같은 핀·같은 `fine_box`이므로 이것은 근사가 아니라 다중 포트
  기계장치의 **항등**이고, 어긋나면 `short_ports`나 RHS 배치가 틀렸다는 뜻이다.
  1 MHz 실측: \|Z_shorted\| = 8.093095169e−4 vs 영수증 \|Z\| = 8.093095168e−4.
  주파수별 폐합 오차: 2.35e−09, 2.78e−09, 1.39e−10, 2.01e−10, 1.34e−10, 1.48e−10, 1.82e−10.
- **G-A 상반성은 GPU에서 미달(1.73e−11 > 1e−12), CPU에서 통과(1.02e−15).** §5 이탈 2.
- **대각은 단일 포트 Z가 아니다.** 대각은 "한 핀 그룹만 구동하고 다른 그룹은 개방"이므로 정의상
  다른 양이다. 그 차이가 0.13 %(Port18)/0.77 %(Port14)밖에 안 되는 이유는 결합 0.999이기 때문이다
  — BGA 핀 필드는 1 MHz에서 사실상 한 노드다. 브리핑의 "대각 ≤ 5 %"는 이 해석으로 통과한다.
- 결합비는 30 kHz 0.999998 → 100 MHz 0.99728로 단조 감소한다(주파수가 올라갈수록 핀 사이 위상차).

### 4-3. `fine_box` 효과 (브리핑 (b)의 통제 실험)

다중 포트의 `fine_box`는 포트들 핀 bbox의 **합집합** ±1 mm이므로, 단일 포트 빌드보다 세밀 메시
영역이 넓어진다. 핀 분할 케이스는 A∪B = 포트 전체라 합집합이 단일 포트 상자와 **정확히 같고**
(`fine_box_equal = True`), 그래서 §4-1·§4-2가 통제 실험 그 자체다(차이 0).

합집합이 실제로 커졌을 때의 영향을 따로 쟀다: 260729 Port18_SITE0를 자기 상자 vs
Port14_SITE0(같은 설계의 실제 다른 포트) 상자와의 합집합으로 두 번 빌드.

| | 자기 상자 | 합집합 상자 |
|---|---|---|
| 상자 넓이 | 199.88 mm² | 360.43 mm² (+80 %) |
| 미지수 | 275 218 | 276 294 (+0.39 %) |
| max \|ΔZ\|/\|Z\| (7점) | — | **4.51e−05** |

주파수별: 1.93e−07, 8.24e−07, 5.09e−06, 3.19e−05, 4.51e−05, 2.94e−05, 9.96e−06(최대는 2.5 MHz).
즉 **합집합 상자는 대각을 0.005 % 수준으로 움직인다.** 영수증에 비트 동일을 요구하는 재현
테스트에는 유효한 차이지만, 응용이 보는 Z에는 사실상 영향이 없다. 단일 포트 빌드와 비트 동일이
필요하면 `ModelOptions(fine_box=rail.fine_box)`로 상자를 못박으면 된다.

### 4-4. SITE0 / SITE1 짝 (브리핑 (c))

두 레일이라 Z 행렬을 만들 수 없다(§3). 대신 각각을 단일 포트로 풀어 비교했다.

| | Port18_SITE0 | Port64_SITE1 |
|---|---|---|
| rail_net | `ADC_VDD_075_VTRIP_SRAM/0` | `ADC_VDD_075_VTRIP_SRAM/1` |
| 포트 핀 | 978 | 978 |
| 미지수 | 275 218 | 275 022 |
| \|Z\| @1 MHz | 8.093095e−04 | 8.097384e−04 |

- **\|Z₁₁\| = 8.0931e−4 Ω, \|Z₂₂\| = 8.0974e−4 Ω, \|Z₁₂\| = 0 (구성상 정확히 0),
  \|Z₁₂\|/√(\|Z₁₁\|\|Z₂₂\|) = 0.**
- 두 SITE의 차이는 7점에서 최대 0.345 %(10 MHz), 1 MHz에서 0.097 %다. 미러 배치가 PDN 관점에서
  거의 동일하다는 뜻이고, **SITE0에서 얻은 decap 판단은 SITE1에 그대로 옮겨도 된다**(0.1 % 급).
- 이상 GND에서 SITE 간 결합을 보려면 다중 포트가 아니라 **명시 GND(S3) 경로**가 필요하다. 두 레일이
  공유하는 유일한 도체가 GND이고, 변종 B는 그것을 이상 기준면으로 지워버린다.

### 4-5. 비용 (브리핑 (d))

`Backend(solver="cudss", fast=True)`, 캐시 웜, Port18 275 k 미지수, k=2, 7주파수:

| 단계 | 시간 |
|---|---|
| 추출(캐시 적중) | 1.3 s |
| 빌드(균질화 포함) | 20.1 s |
| 7 × (assemble + factorize + 2 RHS) | 7.9 s |
| **합계(프로세스 전체)** | **약 31 s** |

주파수당 내역(30 kHz): assemble 0.23 s, factorize 0.135 s, RHS 1개 풀이 0.013 s, nnz(LU) 8.74 M,
RSS 2.05 GB. **RHS 하나 추가 비용은 인수분해의 10 %에 불과하다** — k가 커져도 비용은 거의 늘지
않는다(k×0.013 s). pytest `gpu` 케이스 전체가 28.6 s(한도 2분).

### 4-6. 테스트

```
python -m pytest tests\engine -q -k multiport          3 passed, 1 skipped (4.4 s)
python -m pytest tests\engine -q -k multiport --gpu    4 passed (32.5 s)
python -m pytest tests\engine -q -k datafree           14 passed, 1 skipped (1.4 s; 기존 12 + W10 2)
```

- `test_multiport_datafree_grouping` — `group_ports_by_rail`을 가짜 포트 목록으로.
- `test_multiport_datafree_short_ports` — `multiport.demo()`: 가짜 모델에서 `map`/`N`/`P` 재번호와
  "그룹 0이 모델의 포트가 아니면 거부" 불변식.
- `test_multiport_site_pair_is_two_rails` — 헤더만 읽어 92포트 = 92레일과 SITE 짝의 분리를 못박는다.
  **같은 net에 두 포트가 있는 SPD가 들어오면 이 테스트가 깨지고, 그때 진짜 포트로 k>1 케이스를
  만들면 된다.**
- `test_multiport_port18_pin_split`(`gpu`) — §4-2의 게이트 4개(상반성 ≤ 1e−8, 폐합 ≤ 1e−6,
  대각 ≤ 5 %, 2분) + 영수증 필드.

## 5. 이탈

1. **브리핑의 (c) 케이스를 그대로 수행할 수 없다.** SITE0/SITE1은 다른 레일이다(§3). 세 설계
   344 포트 전부가 1포트 = 1레일이다. 다중 포트 코드는 일반형으로 만들고(같은 rail_net의 SPD 포트
   k개를 그대로 받는다), 실측은 핀 분할로 했다. 진짜 다중 포트 SPD가 생기면 코드 변경 없이
   `Design.multiport([...])`로 돌아간다.
2. **G-A 상반성 1e−12가 GPU에서 미달(1.73e−11 ~ 8.21e−11).** 상반성은 네트워크의 정확한 항등이라
   실제로 재는 것은 **솔버 잔차**다. 같은 인수분해를 쓰는 splu는 1.02e−15(브리핑이 기대한 1e−15
   그대로)이고, cuDSS + 반복정제 1단계는 2e−11대다. CLAUDE.md의 GPU 계약(CPU 대비 ≤ 1e−8)
   안쪽이므로 수치 문제는 아니지만, **사전 등록한 1e−12를 사후에 넓히지 않았다**: 게이트 문서에는
   미달로 남기고, pytest의 GPU 게이트는 프로젝트가 이미 사전 등록한 1e−8을 쓴다(CPU에서 1e−12를
   원하면 `Backend()`로 돌리면 된다).
3. **대각 vs 단일 포트의 의미.** 브리핑은 "다중 포트 대각 ≈ 단일 포트 Z, 차이는 `fine_box` 때문"을
   기대했지만, 핀 분할에서는 `fine_box`가 같고(차이 0) 대각은 정의상 다른 양이다(§4-2). 그래서
   `fine_box` 효과는 §4-3에서 따로 쟀고, 기계장치 검증은 폐합 항등으로 했다. 폐합이 더 강한 검사다.
4. **`numerics_id`가 체크아웃마다 다르다(W10에서 발견, W10 밖 문제).** `receipt.source_hashes()`는
   수치 모듈의 **원시 바이트**를 해싱하므로 줄바꿈이 CRLF인 체크아웃과 LF인 체크아웃이 같은 커밋인데
   다른 id를 낸다. 이 작업 트리(전부 CRLF)의 id는
   `236070091c02155382dd11ff5fcd2de89d33736f9499f8e4ebc380a1680ef9a6`이고, 같은 소스를 LF로
   정규화하면 `2e54a63d…`, W8이 기록한 메인 체크아웃 값은 `f3808b55…`다(메인은 model.py/reference.py가
   LF, 나머지가 CRLF인 혼재 상태). Z는 §4-1에서 보듯 바뀌지 않았다. 영수증의 "같은 numerics_id =
   같은 수치" 계약이 clone 설정에 좌우되므로, `source_hashes()`에서
   `read_bytes().replace(b"\r\n", b"\n")`로 정규화해야 한다. `receipt.py`는 W9가 쓰고 있어 건드리지
   않았다 — **소유자/W11 결정 사항.**

## 6. W11(제품 통합)에 필요한 것

- **다중 포트는 지금 제품에 필요 없다.** 이 설계들에서는 포트 = 레일이므로 92포트 스윕은 여전히
  "포트당 프로세스"가 맞다(계획 §2-5의 판단 그대로). `multiport.py`가 실제로 쓰이는 곳은
  (a) 한 net에 여러 포트가 있는 새 SPD, (b) 핀 그룹/커넥터 필드 단위 해석, (c) W9의 Schur decap
  스윕이 필요로 하는 "레일 위 임의 노드 집합 = 포트" 표현이다. `MultiRail.from_rail(rail, groups)`가
  그 표현이다.
- **SITE 간 decap 실장 판단(엔진 프로젝트의 목적 중 하나)은 다중 포트로 풀리지 않는다.** 두 SITE는
  별개 레일이고 이상 GND에서 결합이 0이다. 필요한 것은 명시 GND(S3) 경로이며, 그 전에는 §4-4처럼
  "두 레일은 0.1 % 이내로 같다"가 실무적 답이다.
- W9가 `solver.py`에 다중 RHS를 넣으면 `MultiModel.solve`의 cuDSS 블록 6줄을 교체한다. 인터페이스
  (`MultiResult`, 영수증)는 그대로다.
- `numerics_id` 줄바꿈 정규화(§5-4)는 영수증 비교를 하는 모든 앱에 영향을 준다.

## 7. API

```python
from spd_pi_engine import Backend, Design, FLAGS_P, ModelOptions, rail_groups
from spd_pi_engine.multiport import MultiRail

rail_groups(spd)            # {rail_net: [port, ...]}  -- 어떤 포트들이 한 모델에 들어갈 수 있나
                            # (헤더 스캔만, 추출 없음. 92포트 설계에서 0.1 s)

# (a) 같은 레일의 SPD 포트 여러 개
mp = Design.open(spd).multiport(["PortA", "PortB"], cache_dir,
                                ModelOptions(flags=FLAGS_P, fringe=True),
                                Backend(solver="cudss", fast=True))

# (b) 이미 추출한 레일 위의 임의 노드 그룹 (핀 분할, decap 사이트, 커넥터 필드)
rail = Design.open(spd).rail("Port18_SITE0", cache_dir)
pins = rail.ex["port_pos_nodes"]
mp = MultiRail.from_rail(rail, {"A": pins[0::2], "B": pins[1::2]}).build(OPT, Backend("cudss", fast=True))

res = mp.solve(freqs)       # res.Z 는 (nf, k, k)
res.reciprocity()           # max |Z_ij - Z_ji| / |Z_ij|
res.diagonal()              # (k, nf)  포트별 자기 Z (나머지 개방)
res.coupling()              # (nf, k, k)  |Z_ij| / sqrt(|Z_ii||Z_jj|)
res.shorted()               # (nf,)  전 포트 단락 등가 = 1 / sum(Z^-1)
res.receipt()               # method="multiport", ports, port_groups, numerics_id, freq,
                            # Z_re/Z_im (nf,k,k), fine_box, decap_config, reciprocity_max_rel …
```

| 심볼 | 계약 |
|---|---|
| `group_ports_by_rail({port: rail}) -> {rail: [port]}` | 순수 함수, 입력 순서 보존 |
| `port_rails(spd, ports=None) -> {port: rail_net}` | `.Port` 헤더 정규식 1회 스캔. `extract`의 `ex["rail_net"]`과 **같은 캡처 그룹**이라 값이 동일하다(추출 k회를 피한다) |
| `MultiRail.open(design, ports, cache_dir)` | 포트별 `prepare`, rail_net 불일치 시 `ValueError`. `fine_box` = 포트 상자 합집합 |
| `MultiRail.from_rail(rail, groups)` | 이미 추출한 레일 위의 노드 그룹. 그룹 0이 모델의 포트가 된다 |
| `MultiModel(...)` | `Model`을 **소유**한다(`map`/`N`/`P`가 k 포트용으로 바뀐다). 그룹이 겹치거나 프루닝으로 사라진 노드를 담으면 `ValueError` |
| `MultiResult.Z` | `Z[f, i, j]` = 포트 j에 1 A를 넣었을 때 포트 i의 전압 |

## 보충 (2026-09-18, 병합 후)
- `numerics_id`가 체크아웃의 줄바꿈(CRLF/LF)에 따라 달라지는 문제를 `receipt.source_hashes`에서 LF로 정규화해 고쳤다(수치 모듈 바이트가 아니라 해시 함수의 변경이므로 Z는 불변). 이후 영수증의 id는 LF 기준 값이다.

## §7 후속 (다중 RHS 교체, 2026-09-18)

§6/ENGINE_PLAN "진행 상태"가 예고한 후속: `MultiModel.solve`의 cuDSS 블록이 하던 "1회 `gpu.solve(Y)` +
나머지 k-1개는 `gpu._solver.reset_operands(b=...)`로 개별 삼각 풀이"를 W9가 추가한 진짜 다중 RHS
경로(`Model._gpu_solver(Y, nrhs=k)` / `CudssLU.solve(Y, B)`)로 교체했다. `model.py`/`solver.py`는
손대지 않았다 — 둘 다 W9가 이미 만들어 둔 것을 부르기만 한다. `multiport.py` 외 파일은 수정하지 않았다.

### 변경 (`src/spd_pi_engine/multiport.py`, 28줄 → 4줄 순감)

```diff
-from . import solver as SOL
 from .backend import DEFAULT
@@ MultiModel.solve
-        rhs = np.zeros((int(mdl.N), k), complex)
+        rhs = np.zeros((int(mdl.N), k), complex, order="F")   # W9: cuDSS wants the dense RHS column-major
         rhs[self.P, np.arange(k)] = 1.0
         Z = np.zeros((len(freqs), k, k), complex)
         stats = []
-        gpu = None
         t_all = time.time()
         for fi, f in enumerate(freqs):
             t0 = time.time()
             Y = mdl.assemble(f)
             t1 = time.time()
-            if gpu is None and mdl.backend.solver in ("cudss", "auto"):
-                gpu = mdl._cudss
-                if gpu is None:
-                    try:
-                        gpu = SOL.CudssLU(Y, self.P[0], log, backend=mdl.backend)
-                        log(f"[solver] cudss on {gpu.device}")
-                    except Exception as e:
-                        gpu = False
-                        log(f"[solver] cudss unavailable ({type(e).__name__}: {e}) -- using splu")
-                    mdl._cudss = gpu
+            gpu = mdl._gpu_solver(Y, nrhs=k)     # W9: one factorization, k right-hand sides
             if gpu:
-                V, st = gpu.solve(Y)             # refactorize + solve for e_P0
-                Vs = [V]
-                # ponytail: the extra RHS go through CudssLU's own DirectSolver, one triangular
-                # solve each on the SAME factorization -- cuDSS rejects a 2-D b once it was planned
-                # with a 1-D one, and W9 is adding a real multi-RHS path to solver.py.  Leave b at
-                # e_P0 afterwards: CudssLU.solve() does not reset it.
-                for j in range(1, k):
-                    gpu._solver.reset_operands(b=rhs[:, j].copy())
-                    Vs.append(np.asarray(gpu._solver.solve()).copy())
-                if k > 1:
-                    gpu._solver.reset_operands(b=rhs[:, 0].copy())
+                V, st = gpu.solve(Y, rhs)
+                Vs = list(V.T)
                 t2 = time.time()
                 stats.append(dict(f=float(f), assemble_s=t1 - t0, rss_MB=peak_rss_mb(), n_rhs=k, **st))
             else:
```

`# ponytail:` 표시가 있던 우회로(`reset_operands`로 RHS를 하나씩 다시 꽂는 것)와 그 앞의 임시
`gpu = None` / `mdl._cudss` 캐시 관리 코드가 통째로 사라지고, `decaps.py`가 이미 쓰는 것과 같은 패턴
(`model._gpu_solver(Y, nrhs=chunk)` → `gpu.solve(Y, B)[0]`)으로 정리됐다. splu 분기(else)는 그대로다.

### 검증

1. `pytest tests\engine -q -k "multiport or datafree" --gpu` — **17 passed** (기존 스위트 그대로, 새 실패 없음).
2. Port18_SITE0 핀 분할(k=2, cuDSS, `--gpu`)을 새 코드로 재현 — `w10_gate.py`가 가리키는 워크트리
   (`...\worktrees\spd-engine-w10`)는 이미 없으므로, 같은 `OPT`/`GPU`/`LADDER`/짝수-홀수 분할 규칙으로
   현재 저장소를 가리키는 동등 스크립트를 새로 돌렸다(`split_260729_Port18_SITE0_cudss.json`이 구
   결과, `split_260729_Port18_SITE0_cudss_w9rhs.json`이 신 결과):

   | 지표 | 구 (k회 삼각 풀이, W10 리포트) | 신 (다중 RHS) | 차이 |
   |---|---|---|---|
   | reciprocity `max|Z_ij-Z_ji|/|Z_ij|` | 8.207150525903876e-11 | 5.062260486661473e-11 | 3.14e-11 |
   | closure `|shorted - exp28/p|/|exp28/p|` (max over 7 f) | 2.7773932486488007e-09 | 2.769342341289102e-09 | 7.99e-12 |

   둘 다 사전 등록 게이트(G-A reciprocity <= 1e-8, G-B closure <= 1e-6, `test_multiport.py`)를 3
   자릿수 이상 여유 있게 통과하지만, 이 절 앞머리에서 목표로 잡았던 "1e-12 이내 일치"는 아니다 —
   `test_multiport_port18_pin_split`의 docstring이 이미 적어 둔 대로 cuDSS + 1회 반복정제는 **같은
   코드를 두 번 돌려도** reciprocity가 2e-11~8e-11 사이에서 흔들린다(GPU 비결정성, CLAUDE.md "GPU
   정책"). k회 개별 `reset_operands`+삼각 풀이에서 진짜 배치 다중 RHS 1회 factorize+solve로 수치
   경로 자체가 바뀌었으니, 이 흔들림 폭 안에서 구/신이 다른 것은 회귀가 아니라 예상된 결과다.
   `Z`가 아니라 `Y`(어셈블) 쪽은 전혀 손대지 않았으므로 재현 오차의 근원은 오직 이 GPU 솔버 잡음.
3. 주파수당 처리 시간(k=2, 같은 웜 `engine_cache_w10`, 7주파수): `solve_seconds` 합계가 7.87 s(구,
   1.12 s/f) → 4.74 s(신, 0.68 s/f) — **회귀 없음, 오히려 ~40 % 단축**(RHS 2개를 한 번의
   factorize로 같이 풀기 때문에 당연한 방향).

원시 수치: `WORK_DIR\engine_w10\split_260729_Port18_SITE0_cudss.json`(구, 기존 파일 그대로 보존)와
`WORK_DIR\engine_w10\split_260729_Port18_SITE0_cudss_w9rhs.json`(신, 이번에 추가).
