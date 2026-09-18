# tests/engine/fixtures — 엔진 재현 테스트 픽스처

계획 `docs/engine/ENGINE_PLAN_2026-09-18.md` §3(수치를 고정하는 재현 테스트)의 영수증 사본이다.
여기 있는 JSON은 **연구 러너가 만든 정본 영수증의 복사본**이며, 테스트가 엔진 출력과 비교하는
기준값이다. 수정하지 않는다(CLAUDE.md 규칙 2: 기존 결과 덮어쓰기 금지).

## 출처

| 파일 | 원본 | 생성자 | 크기 | 미지수 | 주파수 |
|---|---|---|---|---|---|
| `exp28/result_260729_Port1_SITE0_any_p.json` | `WORK_DIR\exp28\` | `exp11/run11.py` (EXP-28, 변형 p) | 13,702 B | 118,064 | 27 |
| `exp28/result_260729_Port7_SITE0_any_p.json` | 〃 | 〃 | 16,660 B | 586,481 | 27 |
| `exp28/result_260729_Port14_SITE0_any_p.json` | 〃 | 〃 | 13,706 B | 34,424 | 27 |
| `exp28/result_260729_Port16_SITE0_any_p.json` | 〃 | 〃 | 13,822 B | 334,638 | 27 |
| `exp28/result_260729_Port18_SITE0_any_p.json` | 〃 | 〃 | 13,790 B | 275,218 | 27 |
| `exp28/result_260729_Port19_SITE0_any_p.json` | 〃 | 〃 | 12,207 B | 69,205 | 27 |
| `exp28/result_260804_Port18_SITE0_any_p.json` | 〃 | 〃 | 13,848 B | 275,177 | 27 |
| `exp30/result_s5m6585_Port1_U1_0_any_p.json` | `WORK_DIR\exp30\` | `exp11/run11.py` (EXP-30, held-out PCB) | 10,360 B | 189,397 | 27 |
| `exp30/result_s5m6585_Port50_U1_0_any_p.json` | 〃 | 〃 | 10,307 B | 189,283 | 27 |
| `spd_sha256.json` | 이 세션(W5)에서 계산 | — | 727 B | — | — |

합계 약 118 KB(계획 §3의 "픽스처 총량 약 120 KB" 예상과 일치).

`WORK_DIR` = `SPD_PI_WORK_DIR`(소유자 PC: `D:\Downloads\examples\analysis\claude-2026-09-15\work`).

### 정본(canonical) 규칙

- exp28 폴더에는 같은 케이스의 **시각 접미사(6자리 HHMMSS) 사본**이 함께 있다
  (예: `result_260729_Port14_SITE0_any_p_030827.json`). 계획 §3 (ii)에 따라 **접미사 없는 파일이
  정본**이고, 여기에 복사한 7개는 전부 접미사 없는 정본이다.
- exp30은 160포트 전체 중 2포트만 복사했다. `Port1_U1_0`은 `WORK_DIR\engine_cache`에 추출 캐시가
  이미 있어 기본 프로파일에서 돌고, `Port50_U1_0`은 다른 레일(`ADC_DVDD08_CORE/17`)이라 C5
  (층 이름 하드코딩) 회귀 폭을 넓히려고 골랐다. 추출 캐시가 없어 `slow` 표시다.

### 복사하지 않은 것

- **EXP-8 영수증은 복사하지 않고 제자리에서 참조한다**:
  `docs/research-claude/2026-09-15/results/exp8/result_260729_Port18_SITE0_any.json`
  (저장소에 이미 커밋돼 있음, 미지수 261,124). 계획 §3 (i).
- SPD 원본(1.1 GB × 2, 0.14 GB)과 PowerSI 참조 npz는 복사하지 않는다. `SPD_PI_DATA_DIR`에서
  찾고, 없으면 테스트는 skip한다.

## 각 영수증이 만들어진 SPD의 sha256

`spd_sha256.json`에 들어 있다. 2026-09-18에 `SPD_PI_DATA_DIR = D:\Downloads\examples`에서 계산.

| 설계 태그 | SPD 파일 | 바이트 | sha256 |
|---|---|---|---|
| `260729` | `S4LB002-2Para_260729_1_injected.spd` | 1,116,717,287 | `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2` |
| `260804` | `S4LB002-2Para_260804_1_injected.spd` | 1,120,159,188 | `45253f438fc7c328c50364fe610a7ecfbf72ca842a032921fbbb8d645e2a4f35` |
| `s5m6585` | `s5m6585_32p_260414_length3_1.spd` | 139,414,807 | `f774dc06cdce4a1e0a632eb4a126d06af1fd57b9f400287e510f233ede1c0ad9` |

- `exp28/*` 7건: `260729` 6건 + `260804` 1건.
- `exp30/*` 2건: `s5m6585`.
- EXP-8 영수증(제자리 참조): `260729`.

연구 영수증 자체에는 SPD 해시 필드가 없다(영수증 v1에서 `spd_sha256`이 추가됐다). 그래서 위
해시는 **지금 데이터 디렉터리에 있는 SPD**에서 계산한 값이며, 그 SPD가 영수증을 만든 SPD라는
근거는 (a) 파일이 D7 기준선 이후 바뀌지 않았고 (b) 같은 SPD로 돌린 엔진이 영수증을 1e-9 안에서
재현한다는 것이다 — 즉 테스트가 통과하는 동안에는 이 표가 유효하다.

## 해시 불일치 정책 (계획 §3)

`tests/engine/conftest.py`의 `spd_path(tag)`:

- SPD가 **없으면** `pytest.skip` — 데이터 없는 머신에서도 데이터 없는 테스트는 돈다.
- SPD가 **있는데 sha256이 다르면** `assert` **FAIL** — 다른 SPD가 우연히 같은 영수증을 재현하는
  일은 픽스처가 막으려는 바로 그 상황이다. SPD를 의도적으로 교체했다면 영수증부터 다시 만들고
  `spd_sha256.json`을 갱신해야 한다.
