# W8 — `Model.set_decaps`: 재빌드 없는 decap 구성 변경 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §2-4(decap 구성 변경), §1-1 C20, §4 W8.
원칙 §0(전(全)실장 기본 경로 수치 불변, `tools/research-claude/` 무수정). 2026-09-18.

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/model.py` | +47/−4 (992줄) | `set_decaps` / `decap_config` / `reset_decaps` / `add_decap_model`, `prune_basis`, `assemble`·`breakdown`의 `model_id is None → y = 0` 분기 |
| `src/spd_pi_engine/receipt.py` | +4/−1 (299줄) | 영수증에 `decap_config`·`prune_basis` 추가, `decap_config_sha256`가 **모델의 현재 구성**을 해싱 |
| `src/spd_pi_engine/api.py` | +8/−2 (158줄) | 모듈 독스트링의 W8 사용법(수치 없음) |
| `tests/engine/test_datafree.py` | +51 | 데이터 없는 테스트 2개 |
| `tests/engine/test_reproduction.py` | +42 | 데이터 테스트 1개(`gpu`, 18.6 s) |

게이트 스크립트·결과: `WORK_DIR\engine_w8\{w8_gate.py, setdecaps_*.json, rebuild_*.json,
ycheck_*.json, default_*.json, w8_results.json, w8_drive.log}`.
`tools/research-claude/`는 건드리지 않았다(`git status --short -- tools/research-claude` 빈 출력).

## 2. API

```python
mdl = rail.build(opt, Backend(solver="cudss", fast=True))   # 전(全)실장으로 1회만 빌드
mdl.set_decaps({"C1234": None})                  # 부분 dict: 나열된 refdes만 바뀐다. None = 미실장
mdl.set_decaps({"C1235": "CAP_0402_100NF"})      # 모델 교체(ex["models"]에 있는 id)
mdl.decap_config                                 # 전체 현재 구성 {refdes: model_id | None}
mdl.reset_decaps()                               # SPD 원래 구성으로 복귀
mdl.add_decap_model("MY_CAP", subckt_text)       # .SUBCKT 두 단자 모델 등록(추출과 같은 파서)
res = mdl.solve(freqs)                           # 재빌드·재프루닝 없음. YPattern·cuDSS 플랜 재사용
res.receipt()["decap_config"] / ["decap_config_sha256"] / ["prune_basis"]
```

| 심볼 | 계약 |
|---|---|
| `set_decaps(config: dict[str, str \| None]) -> self` | 부분 dict. 모르는 refdes → `KeyError`, `ex["models"]`에 없는 model_id → `KeyError`. **검증을 먼저 하므로 거부된 호출은 아무것도 바꾸지 않는다.** |
| `decap_config -> dict` | `ex["decaps"]`의 **모든** refdes(레일 노드가 프루닝 전 격자에 없어 스탬프되지 않는 것 포함) → 현재 model_id 또는 `None`. 복사본이라 밖에서 수정해도 모델은 안 바뀐다 |
| `reset_decaps() -> self` | `{refdes: ex["decaps"]의 model_id}`로 되돌린다 |
| `add_decap_model(model_id, subckt_text) -> self` | `spd_decap_pi._core.models.spice.parse_passive_subcircuit`(추출이 쓰는 그 파서)로 파싱 → `ex["models"][model_id]`, `ex["model_texts"][model_id]`. 등록 시 `impedance([1e6])`를 한 번 불러 스윕 중간이 아니라 여기서 실패하게 한다 |

구현은 3줄이 전부다.

1. `_build`가 `self.dec[k]`의 refdes(`_dec_refdes`)와 현재 구성(`_dec_cfg`)을 같이 기록한다.
2. `set_decaps`는 `self.dec`를 **같은 길이·같은 노드쌍**으로 다시 만들고 model_id만 바꾼다.
3. `assemble`/`breakdown`의 어드미턴스 dict가 `mid is None → 0.0`을 준다.

```python
ys = {mid: (0.0 if mid is None else 1.0 / self.ex["models"][mid].impedance([f])[0])
      for mid in {d[2] for d in self.dec}}
```

## 3. 프루닝 규칙과 실측 효과

- 빌드의 프루닝 그래프는 decap 간선을 포함해 만들어지고(`model.py` prune 절), 미실장 decap도
  항목이 남으므로 **`N`은 전(全)실장 기준으로 한 번 정해지고 다시 움직이지 않는다.**
  `prune_basis = "all_mounted"`를 모델 속성·`info`·영수증에 남긴다.
- 미실장이 `self.dec`에서 빠지지 않으므로 COO 삼중항 개수가 그대로고, **명시적 0은 scipy의
  COO→CSC에서 살아남는다**(`coo_matrix.tocsc()`는 0을 제거하지 않는다. `tests/engine/test_datafree.py::test_ypattern_keeps_explicit_zero`).
  이상 GND(`gnd_node = -1`)에서 decap은 대각 1개만 남고 그 자리에는 시트·비아 기여가 이미 있어
  중복 합산될 뿐이다 → **CSC nnz·indptr·indices 완전 동일**(실측 §4-2).
- 실측: **두 포트 모두 어떤 구성에서도 프루닝이 바뀌지 않았다.** 미실장 decap을 `ex["decaps"]`에서
  지우고 새로 빌드해도 `N`, `nodes_before_prune`이 같다(Port14 34 424 / 34 492, Port18 275 218).
  decap 레일 노드는 비아·패드 링크로도 플레인에 붙어 있어 decap 간선을 빼도 고립되지 않는다.
  → 이번 두 설계에서는 `prune_basis="all_mounted"` 규칙이 "재빌드와 동일"을 만든다.

## 4. 검증

`WORK_DIR\engine_w8\w8_gate.py`. cuDSS 0.8.0.10이 한 프로세스에서 두 번째 `DirectSolver`에
크래시하므로 (i) set_decaps 실행(모델 1개, 구성 여러 개)과 (ii) 구성별 재빌드는 각각 별도
프로세스로 돈다. 주파수는 exp5 LADDER 7점을 PowerSI 격자에 스냅한 값
(3.02e4, 1.0e5, 3.02e5, 1.0e6, 2.51e6, 1.0e7, 1.0e8 Hz), 옵션은 `FLAGS_P` 변종 p.

### 4-1. 기본 경로 불변 (§0)

전(全)실장·27점 사다리, `tests\engine\fixtures\exp28` 영수증 대비.

| 포트 | backend | unknowns | max \|ΔZ\|/\|Z\| | 허용 | 판정 |
|---|---|---|---|---|---|
| 260729 Port14_SITE0 | `Backend(solver="cudss", fast=True)` | 34 424 = 영수증 | 4.21e−11 | 1e−8 | PASS |
| 260729 Port18_SITE0 | `Backend(solver="cudss", fast=True)` | 275 218 = 영수증 | 1.21e−10 | 1e−8 | PASS |
| 260729 Port14_SITE0 | `Backend()` (splu, fast off) | 34 424 | 3.231881322361924e−12 | 1e−9 | PASS |

CPU 경로는 W4 영수증 `WORK_DIR\engine_w4\receipt_260729_Port14_SITE0_cpu.json`과
**max \|ΔZ\|/\|Z\| = 0.0, `Z_re`·`Z_im` 배열 비트 동일**이고 exp28 대비 값도 W4가 기록한
3.231881322361924e−12과 자릿수까지 같다.

**이탈 1(예상된 것): `numerics_id`가 바뀐다.** `numerics_id`는 `model.py` 소스 바이트를 포함한다
(`NUMERIC_MODULES`). W4 `a17942b9…` → W8 `f3808b55…`. Z는 비트 동일하므로 수치가 바뀐 것이
아니라 소스가 바뀐 것이다(W4도 `solve()` 3줄 추가로 같은 일을 겪었다). 영수증 비교 기준은
`f3808b55fcac8ba5dcdf4c309dce99169befe5a8840e91c937e557c4112b1a3a`로 갱신한다.

### 4-2. set_decaps vs 재빌드

(i) 전(全)실장 빌드에 `set_decaps` / (ii) `ex["decaps"]`에서 해당 항목을 **빌드 전에** 지운 새 빌드.
구성: (a) 1개, (b) 10 %(= refdes 순서 매 10번째), (c) 전부. 둘 다 `Backend(solver="cudss", fast=True)`.

| 포트 | 구성 | 미실장 | N (i) | N (ii) | max \|ΔZ\|/\|Z\| | 최대 지점 | Y 패턴 동일 |
|---|---|---|---|---|---|---|---|
| Port14 (35 decap) | a | 1 | 34 424 | 34 424 | 7.71e−11 | 3.02e4 Hz | 예 |
| Port14 | b | 4 | 34 424 | 34 424 | 1.70e−11 | 3.02e4 Hz | 예 |
| Port14 | c | 35 | 34 424 | 34 424 | 1.94e−06 | 3.02e4 Hz | 예 |
| Port18 (421 decap) | a | 1 | 275 218 | 275 218 | 1.17e−10 | 3.02e4 Hz | 예 |
| Port18 | b | 43 | 275 218 | 275 218 | 6.91e−11 | 3.02e4 Hz | 예 |
| Port18 | c | 421 | 275 218 | 275 218 | 5.62e−06 | 3.02e4 Hz | 예 |

`N`은 어느 구성에서도 움직이지 않았다(고립되는 노드가 없다, §3). 그래서 "N이 다르고 ΔZ > 1e−9"
케이스는 이번 두 포트에서는 발생하지 않는다.

**(c) 전부 미실장의 1.9e−06 / 5.6e−06은 모델 차이가 아니라 cuDSS 해의 잡음이다.** 같은 두 빌드를
CPU(`Backend(solver="splu", fast=True)`)로 붙여 놓고 비교했다(`ycheck_*.json`, 한 프로세스에서
두 모델을 빌드하되 cuDSS 솔버는 만들지 않는다):

| 포트 | 구성 | Y nnz (i) / (ii) | 7점 전부 max \|ΔY\| | CPU max \|ΔZ\|/\|Z\| |
|---|---|---|---|---|
| Port14 | c (35개 전부) | 159 840 / 159 840 | 0.0 | **0.0**(7점 모두 정확히 0) |
| Port18 | c (421개 전부) | 1 258 652 / 1 258 652 | 0.0 | (미측정: N 275 k splu는 주파수당 수 분) |

즉 **조립된 Y는 비트 동일**(값·패턴 모두)하고, CPU splu로 풀면 Z도 비트 동일하다. GPU에서만
차이가 보이는 이유는 decap을 전부 떼면 레일이 사실상 플레인 커패시턴스만 남아
(|Z|(1 MHz): Port14 6.87 mΩ → 195 Ω, Port18 0.81 mΩ → 34.0 Ω) 저주파에서 조건수가 나빠지고,
cuDSS 인수분해의 1e−16급 반올림이 3.02e4 Hz에서 1e−6급으로 증폭되기 때문이다. 실장 구성
((a), (b), 전(全)실장)에서는 같은 GPU 경로가 1e−10 이하다. → GPU 정확도 계약(1e−8)은 실장
구성에서 유지되고, **decap을 전부(또는 거의 전부) 떼는 극단 구성에서 저주파를 볼 때는 CPU 경로를
쓴다**는 단서를 붙인다(PCB 저주파에 대한 `IR_REPORT.md`의 단서와 같은 성격).

sanity: (c) 전부 미실장의 1 MHz \|Z\|는 전(全)실장의 **28 376배**(Port14), **42 050배**(Port18)다.

### 4-3. 모델 교체

Port14의 decap 35개 전부를 `ex["models"]`의 다른 id `CAP_0402_100NF`로 바꾼다
(`set_decaps({refdes: "CAP_0402_100NF"})`) vs `ex["decaps"]` 항목의 `model_id`를 바꿔 새로 빌드.

| 항목 | 값 |
|---|---|
| max \|ΔZ\|/\|Z\| | **2.54e−11** (허용 1e−9, PASS) |
| 최대 지점 | 1.0e5 Hz |
| N | 34 424 = 34 424 |

### 4-4. 비용 (GPU, A2000, 캐시된 추출)

| 포트 | set_decaps 호출 | set_decaps 후 solve(7점) | 재빌드 build | 재빌드 solve(7점) | 재빌드 합(prepare 포함) |
|---|---|---|---|---|---|
| Port14 (34 k) | < 1 ms | 0.28–0.30 s | 4.7–5.6 s | 2.2–3.0 s | ≈ 8–10 s |
| Port18 (275 k) | < 1 ms | **2.08–2.23 s** | 10.1–11.2 s | 5.0–5.2 s | **≈ 17–18 s** |

- 계획 §2-4의 예측("구성당 7주파수 약 2초, P18 GPU")과 일치한다. 재빌드 쪽은 계획이 적은 ~30 s가
  아니라 17–18 s인데, 이는 추출 캐시가 더워져 있고(prepare 1.5 s) W4가 잰 28.5 s에는 27점 스윕과
  영수증 breakdown이 들어 있기 때문이다. **구성 하나당 8배(2.1 s vs 17.7 s) 절약**이다.
- 첫 solve(플랜 생성 포함)만 4.9 s이고 이후 구성은 2.1 s다. 차이가 cuDSS 분석/재정렬 비용이다.
- 재사용 증거(`setdecaps_*.json`): 구성마다 `ypattern_reused=true`(`mdl._fa_pat`가 같은 객체),
  `cudss_reused=true`(`mdl._cudss`가 같은 객체), 주파수당 인수분해 1회(구성당 7회),
  `[solver]` 로그는 프로세스당 `"[solver] cudss on NVIDIA RTX A2000 8GB Laptop GPU"` **1줄뿐**이고
  `"sparsity pattern changed, re-planning"`은 **0회**(`replans: 0`). 재조립된 Y의
  `nnz`/`indptr`/`indices`도 전(全)실장 것과 완전히 같다(`pattern_same=true`).

### 4-5. 영수증

`Result.receipt()`(전(全)실장 → 구성 변경 후):

| 필드 | 전(全)실장 | (c) 전부 미실장 |
|---|---|---|
| `prune_basis` | `"all_mounted"` | `"all_mounted"` |
| `decap_config` | 421개 전부 model_id | 421개 전부 `null` |
| `decap_config_sha256` (Port18) | `fc7332f0b06e33d8…` | `af09070d26f714d5…` |
| `numerics_id` | `f3808b55fcac8ba5…` | `f3808b55fcac8ba5…` (**불변**) |

구성 (a)/(b)/(swap)도 각각 다른 `decap_config_sha256`을 냈고(`w8_results.json`),
`decap_config`의 `null` 개수는 1 / 43 / 0으로 요청과 일치한다.

### 4-6. 테스트

| 테스트 | 프로필 | 시간 | 결과 |
|---|---|---|---|
| `tests\engine -q -k datafree`(기존 10 + 신규 2) | 기본 | 1.4 s | 12 passed, 1 skipped(`--gpu`) |
| `tests\engine -q`(기본 프로필 전체) | 기본 | 27분 21초 | **16 passed, 18 skipped** — Port14/Port18 CPU 재현, PCB Port1_U1_0 포함 전부 통과(회귀 없음) |
| `test_datafree.py::test_set_decaps_bookkeeping` | 기본 | < 0.1 s | PASS — 부분 dict, 미실장 항목 보존, 모르는 refdes/model_id `KeyError`, 거부된 호출의 무변경, `reset_decaps` 왕복 |
| `test_datafree.py::test_ypattern_keeps_explicit_zero` | 기본 | < 0.1 s | PASS — 값 0을 넣어도 `nnz`/`indptr`/`indices` 불변 |
| `test_reproduction.py::test_set_decaps_vs_rebuild_port14` | `--gpu` | 18.6 s | PASS — Port14 1개 unmount, Y 패턴 불변, `N` 동일, max \|ΔZ\|/\|Z\| ≤ 1e−9 |

데이터 없는 두 테스트는 `Model`의 진짜 메서드를 `SimpleNamespace` 대역에 걸어 부른다
(`set_decaps`/`decap_config`/`reset_decaps`가 `dec`, `_dec_refdes`, `_dec_cfg`, `ex`만 건드리므로
SPD 없이 그대로 돈다).

## 5. 계획에서 어긋난 점

1. **`decaps.py`를 만들지 않았다.** 계획 §2-2 파일 지도는 `decaps.py`(set_decaps, multiport/close)를
   두지만, 1단계 `set_decaps`는 `self.dec`와 `assemble`을 읽고 쓰는 47줄이라 `Model`의 메서드가
   아니면 모델 내부를 밖으로 노출해야 한다. 과제 설명도 `Model.set_decaps`를 요구한다. W9의
   Schur(`multiport/close`)는 모델 밖에서 살 수 있으므로 그때 `decaps.py`를 만든다.
2. **`numerics_id`가 바뀐다**(§4-1 이탈 1). 불가피하다(`model.py` 소스 해시).
3. **`assemble`의 decap 값 배열에 `dtype=complex`를 명시했다.** 전부 미실장이면 리스트가 실수 0.0만
   담겨 배열이 float가 되기 때문이다. 실장 구성에서는 원래도 complex128이라 기본 경로는 비트 동일
   (§4-1이 그 증거).
4. **테스트의 데이터 케이스는 GPU로 빌드하고 CPU(splu)로 푼다.** 한 프로세스에서 cuDSS
   `DirectSolver`를 두 개 만들면 0.8.0.10이 크래시하고(기존 코드 주석), GPU 경로의 정확도 예산
   (1e−8)은 요구된 게이트(1e−9)보다 넓다. cuDSS 변종은 `WORK_DIR\engine_w8`가 프로세스를 나눠 돈다.
5. **`add_decap_model`은 model_id를 인자로 받은 이름 그대로 등록한다**(파싱된 모델 자신의
   `model_id`를 쓰지 않는다). `set_decaps`가 부르는 키와 등록 키가 어긋나지 않게 하는 쪽을 택했다.

## 6. 남은 것 / 다음

- W9(2단계, Schur `multiport`/`close`)는 구성 수십 개 이상일 때만 이득이다. 지금 1구성당
  Port18 2.1 s이므로, 손익분기는 "빌드 1회 + 구성당 2.1 s" vs "기저 1회(422 RHS × 7f) + 구성당 거의 0"이다.
- 전(全)미실장 극단 구성의 저주파 GPU 잡음(§4-2)은 **쓰기 전에 알아야 하는 단서**다. decap 배치
  최적화 앱은 보통 부분 미실장만 쓰므로(1e−10) 영향이 없다.
- `prune_basis`는 지금 항상 `"all_mounted"`다. 다른 기준(예: 구성별 재프루닝)이 필요해지면 그때
  값이 늘어나고 영수증이 구분해 준다.
