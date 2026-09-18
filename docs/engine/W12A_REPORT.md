# W12-a / W12-b — 기저 경로 GPU 계약 조사와 “한 프로세스에서 기저 + 직접 풀이” 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §W12(2026-09-18 사전 등록, 수치 불변), 근거
`APP_decap_search_REPORT.md` §4-2·§6(1–4), `W9_REPORT.md` §2-1·§4-2·§5-3, `W8_REPORT.md` §4-2,
`W5_REPORT.md` §4-2. 원칙 §0(전(全)실장 기본 경로 수치 불변), `tools/research-claude/` 무수정.
2026-09-18.

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/model.py` | +52/−10 (1 060줄) | `set_decaps(config, replace=)`, `release_solver()`, `decap_basis(freqs, chunk, backend=)` |
| `src/spd_pi_engine/decaps.py` | +116/−28 (315줄) | 모듈 독스트링의 **정확도 계약**, `save(path, with_impedances=True)` / `load(path)`(모델 없이), `DecapBasis.model_ids`·`device`·`backend`, `BasisResult.receipt(light=)` |
| `tests/engine/test_w12a.py` | 신규 124줄 | 데이터 없는 3건 + `gpu` 2건 |

실험 스크립트·결과는 `WORK_DIR/engine_w12a/`: `w12_common.py`, `w12b_probe.py`, `w12a_basis.py`,
`w12a_contend.py`, `w12a_compare.py`, `w12a_run_rest.py`, `w12a_gate_default.py`,
`w12b_case*.json`, `w12b_results.json`, `w12a_{gpu,cpu,load}_*.json`, `w12a_summary_*.json`,
`w12a_tables_*.md`, `w12a_gate_default_{obdefault,ob4}.json`, `contend.log`.

---

## 2. W12-b — 한 프로세스에서 두 번째 cuDSS `DirectSolver`

W5 §4-2(“한 프로세스에서 cuDSS 모델 9개 순차 생성 성공”)와 W9 §2-1(“두 번째 생성 시 크래시”)이
모순이었다. 260729 **Port14**(N = 34 424)에서 케이스마다 **새 프로세스**로 돌리고 종료 코드를
기록했다. A = 직접 풀이 솔버(nrhs=1), B = 두 번째 솔버. 모든 해는 같은 행렬의 splu 해와 비교했다.

| # | 절차 | 종료 코드 | 해 vs splu | 판정 |
|---|---|---|---|---|
| 1 | A 풀이 → **파이썬 참조만 해제**(`free()` 없음 + `gc.collect()`) → B(nrhs=1) 생성·풀이 | 0 | B 1.30e−12 | OK |
| 2 | A 풀이 → 참조 해제 → **B(nrhs=24)** 생성·풀이 | 0 | B 1.24e−12 | OK |
| 3 | **A 참조 유지** → B(nrhs=24) 생성·풀이 → **A로 다른 주파수 재풀이** → B 재풀이 | 0 | B 8.83e−13 / A 재풀이 1.31e−11 / B 재풀이 2.50e−11 | OK |
| 4 | A 풀이 → **엔진의 `CudssLU.free()`**(참조만 버림) → B(nrhs=1) | 0 | B 1.44e−12 | OK |
| 5 | A 풀이 → **nvmath `DirectSolver.free()`**(알려진 크래시 경로) → B | **−1073741819 = 0xC0000005** | — | **크래시** |
| 5′ | 케이스 5를 **Port18**(N = 275 218)에서 | 0 | B 1.79e−12 | OK (245 MB 반납) |

- **두 번째 `DirectSolver`는 문제가 아니다.** 한 프로세스에서 nrhs=1 솔버와 nrhs=24 솔버가
  동시에 살아 있고(케이스 3) 번갈아 풀어도 둘 다 splu 대비 1e−11 이내다. W5의 9모델 관찰이 맞고,
  W9 §2-1의 “두 번째 생성 시 크래시”는 **`free()`의 크래시를 일반화한 오해**다.
- **크래시하는 것은 `nvmath.DirectSolver.free()`뿐이며 크기와 무관하게 예측 불가다.** 작은
  Port14에서 0xC0000005, 큰 Port18에서는 정상 종료(CLAUDE.md의 “크기·인수분해 횟수에 따라
  다르다”와 일치). → **엔진은 `free()`를 부르지 않는다.** 기존 `CudssLU.free()`는 참조만 버리므로
  안전하다(케이스 4).
- **참조 해제는 GPU 메모리를 돌려주지 않는다.** nvmath 1.0.0 `DirectSolver`에 finalizer가 없다
  (소스 확인: `__del__`·`weakref.finalize` 없음). 그래서 참조 해제는 **크래시하지도, 해제하지도**
  않는다. 실측 free 메모리:

| 시점 | Port14 | Port18 |
|---|---|---|
| 모델 빌드 후 | 7 545.6 MB | 7 545.6 MB |
| A(nrhs=1) 생성·풀이 후 | 7 262.4 MB (−283, CUDA 컨텍스트 포함) | 7 023.4 MB (−522) |
| 참조 해제 후 | 7 262.4 MB (변화 없음) | — |
| B 생성 후 | 7 222.6 (nrhs=1 추가 −40) / 7 189.0 (nrhs=24 추가 −73) | 7 023.4 MB |
| `DirectSolver.free()` 후 | (크래시) | 7 268.7 MB (**+245 반납**) |
| Port18 기저 솔버(nrhs=24) 2개 누적 | — | 6 822.0 → 6 444.5 MB (개당 약 378 MB) |

**설계 결론**: `Model.release_solver()`는 참조만 버린다(해제 없음). `Model.decap_basis`는 빌드
동안 모델의 솔버를 자동으로 비켜 두었다가 되돌리므로 **한 모델이 기저도 만들고 `set_decaps` +
직접 풀이로 검증도 한다**(§3). 비용은 “포기한 인수분해가 프로세스 종료까지 카드에 남는다”뿐이고
(Port18 기저 솔버 378 MB, 직접 솔버 245 MB) 8 GB 카드에서는 여유가 크다.

---

## 3. W12-b 실증 — 한 프로세스에서 기저 + 직접 풀이 (Port14, Port18)

`w12a_basis.py --task load`: 모델 1회 빌드 → GPU 기저(nrhs=chunk) → 같은 모델에
`set_decaps(cfg, replace=True)` + **GPU 직접 풀이**(nrhs=1) → 추가로 **모델 없이 로드한 기저**로
같은 구성 닫기.

| 포트 | 주파수 | 구성 | 실장 | 기저 vs 직접(같은 프로세스) | 최대 지점 | 모델 없는 기저 vs 기저 |
|---|---|---|---|---|---|---|
| Port14 (35 decap, N 34 424) | 27점 | all_mounted | 35 | 2.909e−07 | 1.0e5 | **0.0** |
| Port14 | 27점 | unmount_1 | 34 | 2.914e−07 | 1.0e5 | 0.0 |
| Port14 | 27점 | unmount_10pct | 31 | 2.749e−07 | 1.0e5 | 0.0 |
| Port14 | 27점 | swap_all | 35 | 2.554e−07 | 1.0e5 | 0.0 |
| Port18 (421 decap, N 275 218) | 7점 | all_mounted | 421 | 2.650e−06 | 3.02e4 | **0.0** |
| Port18 | 7점 | unmount_1 | 420 | 2.652e−06 | 3.02e4 | 0.0 |
| Port18 | 7점 | unmount_10pct | 378 | 2.780e−06 | 3.02e4 | 0.0 |
| Port18 | 7점 | swap_all | 421 | 2.302e−06 | 3.02e4 | 0.0 |

- 두 포트 모두 **한 프로세스, 모델 1회 빌드**로 끝났다(Port18 전체 85.3 s = 빌드 15.1 + 기저 73.1
  + 직접 4구성). 직접 풀이는 `nrhs_direct = 1`인 **새 cuDSS 솔버**로 돌았다(기저 솔버는 비켜 둠).
- 차이는 전부 §4의 GPU 기저 잡음이다(직접 풀이는 CPU 대비 1e−10, A1 §4-2).
- `apps/decap_search`의 2단계(프로세스 분리 + 모델 2회 빌드)는 **더 이상 필요 없다**(§7).

---

## 4. W12-a — 기저 경로 GPU 계약

대상 260729 **Port18**(N = 275 218, decap 421, 기저 422 RHS), `chunk = 24`,
`Backend(solver="cudss", fast=True)`, 비교 대상은 **전(全)실장 구성의 닫힘 Z(f)**.
주파수 집합 두 가지:

- **app 27점** — `apps/decap_search`가 쓰는 스냅 없는 사다리 ≥ 100 kHz(`cli.ladder_freqs()`).
  실은 3e5가 부동소수점으로 두 번 들어가 27점이다(`300000.0`과 `300000.0000000001`).
- **snap 7점** — W9가 쓴 `LADDER`를 PowerSI 격자에 스냅한 7점(최저 30.2 kHz).

### 4-1. 실행 간 변동 (비결정성)

| 주파수 집합 | 비교 | max \|ΔZ\|/\|Z\| | 최대 지점 |
|---|---|---|---|
| app 27 | 같은 프로세스 run0 vs run1 (솔버 새로 생성) | 5.714e−07 | 4.755e5 |
| app 27 | 별도 프로세스 procA vs procB | 1.510e−06 | 1.0e5 |
| app 27 | 같은 프로세스 vs 별도 프로세스(4쌍) | 6.48e−07 … 9.63e−07 | 1.0e5 / 3.8e5 |
| snap 7 | 같은 프로세스 run0 vs run1 | 2.843e−06 | 3.02e4 |
| snap 7 | 별도 프로세스 procA vs procB | 2.265e−06 | 3.02e4 |
| snap 7 | 같은 프로세스 vs 별도 프로세스(4쌍) | 5.19e−06 … **1.029e−05** | 3.02e4 |
| snap 7 | **같은 cuDSS 솔버·플랜을 재사용해 두 번 빌드**(`reuse`) | **2.057e−06** | 1.0e5 |

- **비결정적이다.** 판정선(E4) 1e−7을 5–100배 넘는다.
- 결정적 증거는 마지막 줄이다: **같은 `DirectSolver` 객체·같은 플랜·같은 행렬로 기저를 두 번
  만들어도 2.06e−06 차이**가 난다(GPU 메모리도 6 822 MB로 변화 없음 = 같은 솔버 확인).
  즉 플랜·재정렬·조립(Y는 비트 동일, W8 §4-2)이 아니라 **cuDSS의 수치 인수분해가 실행마다
  다르다**(멀티스레드 리덕션 순서로 추정).
- 같은 기저 안의 두 3e5 점(1 ulp 차이)이 서로 다른 오차(2.05e−07 / 1.04e−07)를 보인 것도 같은 결론.

### 4-2. 다른 GPU 프로세스와 동시 실행 (경합 가설)

`python -m spd_pi_engine solve`(260729 Port14, cudss, 사다리)를 반복 실행하는 프로세스를 띄운 채
같은 측정을 반복했다(측정 중 **44회** 완주, `contend.log`).

| 주파수 집합 | 비교 | max \|ΔZ\|/\|Z\| | 기저 구축 |
|---|---|---|---|
| app 27 | 경합 run0 vs run1 | 1.128e−06 | 322.1 / 321.6 s |
| app 27 | 경합 vs 비경합(8쌍) | 3.59e−07 … 2.844e−06 | — |
| snap 7 | 경합 run0 vs run1 | 2.633e−06 | 86.0 / 84.2 s |
| snap 7 | 경합 vs 비경합(10쌍) | 1.01e−06 … 8.46e−06 | — |
| (참고) 비경합 | 같은 프로세스 / 별도 프로세스 | 5.7e−07 / 1.5e−06 (app), 2.8e−06 / 2.3e−06 (snap) | 275–354 / 72–74 s |

**경합은 원인이 아니다.** 경합 중 변동폭이 비경합 변동폭과 같은 자릿수다. 벽시계만 app 27점에서
약 14 % 늘었다(282 → 322 s). A1 §4-2의 “동시 실행 중이던 다른 GPU 프로세스” 가설은 **기각**한다.

### 4-3. CPU 기저(진실) 대비 오차와 비용

`decap_basis(..., backend=Backend("splu", fast=True))`로 같은 모델에서 CPU 기저를 만들었다
(주파수당 기저 1개 = 인수분해 1회 + 422 RHS).

| 주파수 집합 | CPU 기저 비용 | 주파수당 | GPU 기저 비용 | 주파수당 | 비율 |
|---|---|---|---|---|---|
| snap 7 | **145.0 s** | 20.3–21.1 s | 71.7–86.0 s | 10.2–12.3 s | 1.7–2.0× |
| app 27 | **551.9 s** | 20.2–20.9 s | 275.5–354.4 s | 10.2–13.1 s | 1.6–2.0× |

(모델 빌드 별도 23–29 s. A1이 “splu 1 RHS 1주파수 5–8 s”에서 추정했던 것보다 훨씬 싸다 —
인수분해가 한 번이고 422 RHS의 삼각 풀이는 RHS당 약 31 ms다.)

GPU 기저 8개를 CPU 기저와 비교한 값(전(全)실장):

| 주파수 집합 | 실행 | max \|ΔZ\|/\|Z\| vs CPU | 최대 지점 |
|---|---|---|---|
| app 27 | proc1#0 / proc1#1 | 4.018e−07 / 3.340e−07 | 4.8e5 / 3.8e5 |
| app 27 | procA / procB | 8.301e−07 / 6.802e−07 | 1.0e5 |
| app 27 | 경합 #0 / #1 | 8.863e−07 / **2.014e−06** | 1.0e5 |
| snap 7 | proc1#0 / proc1#1 | 1.700e−06 / 4.543e−06 | 3.02e4 |
| snap 7 | procA / procB | **5.752e−06** / 3.487e−06 | 3.02e4 |
| snap 7 | reuse#0 / reuse#1 | 6.350e−06 / **7.132e−06** | 3.02e4 |
| snap 7 | 경합 #0 / #1 | 2.478e−06 / 2.708e−06 | 1.0e5 / 3.02e4 |

주파수별(실행 2개 예시, 전 대역):

| f | proc1#0 (app) | procA (app) | proc1#0 (snap) | procA (snap) |
|---|---|---|---|---|
| 3.02e4 | — | — | 1.700e−06 | 5.752e−06 |
| 1.0e5 | 3.829e−08 | 8.301e−07 | 7.438e−07 | 3.690e−07 |
| 3.0e5 | 7.433e−08 | 1.266e−07 | 2.712e−07 | 2.028e−07 |
| 4.75e5 | 4.018e−07 | 1.093e−07 | — | — |
| 1.0e6 | 6.673e−08 | 9.960e−08 | 5.631e−08 | 1.220e−07 |
| 2.5e6 | — | — | 1.826e−08 | 7.277e−09 |
| 1.0e7 | — | — | 1.057e−09 | 3.600e−10 |
| 1.0e8 | — | — | 1.742e−11 | 1.517e−11 |

- 오차는 **주파수의 고정 함수가 아니다**: 같은 100 kHz에서 실행에 따라 3.8e−08 ↔ 8.3e−07(22배),
  30.2 kHz에서 1.7e−06 ↔ 5.8e−06이다. 엔벌로프만 주파수의 함수다(저주파일수록 나쁘고 f와 함께
  빠르게 준다 — 맨보드 조건수, W8 §4-2 / W9 §4-2).
- **W9 5.8e−07 vs A1 2.0e−06의 재조정**: 둘 다 같은 잡음 분포의 표본이다. 이번 실측에서
  100 kHz 오차는 3.8e−08–2.5e−06, 30.2 kHz 오차는 1.7e−06–7.1e−06이었다. 주파수 집합이 값을
  “3.5배 움직인다”는 A1의 해석(§6-1)은 **부분적으로만 맞다**: 30.2 kHz가 100 kHz보다 나쁜 것은
  맞지만, 같은 주파수에서의 실행 간 산포가 그 차이만큼 크다.

### 4-4. 판정 (계획 E4)

실행 간 변동이 **5.7e−07 ~ 1.03e−05 ≥ 1e−7**이므로 E4의 전자(前者)를 적용한다.

> **GPU 기저 ≤ 1e−5(30 kHz–100 MHz), 비결정적. 정확 경로는 CPU 기저**
> (`decap_basis(..., backend=Backend("splu", fast=True))`, Port18에서 주파수당 20.5 s = GPU의
> 1.6–2.0배). 직접 경로(`set_decaps` + `solve`)는 영향 없다(GPU도 CPU 대비 1e−10).

이 문구를 `decaps.py` 모듈 독스트링(“ACCURACY CONTRACT”)과 `DecapBasis` 클래스 독스트링에
넣었다. 앱의 기능 기준인 마스크 판정 일치에는 영향이 없다(A1 8건 전부 동일, 엔진 모델 오차
≥ 0.8 %는 네 자릿수 크다). W9 §4-2의 “기저 GPU ≤ 1e−6” 문구는 이 계약으로 대체된다.

---

## 5. 게이트

| 게이트 | 명령 | 결과 |
|---|---|---|
| 테스트 | `python -m pytest tests\engine -q -k "datafree or decap_sweep or set_decaps or w12a" --gpu` | **25 passed**, 30 deselected, 35.3 s |
| 기본 경로 CPU 비트 동일 | `w12a_gate_default.py`(Port14, `Backend()`, W4 영수증 27점) | **PASS — `Z_re`·`Z_im` 비트 동일, max rel 0.0** (OpenBLAS 기본 스레드) |
| 〃 (OPENBLAS_NUM_THREADS=4) | 같은 스크립트 | 비트 동일 아님, **max rel 2.703442407247638e−13**(재현 가능, 두 번 같은 값) |

- **이탈(예상 밖)**: `OPENBLAS_NUM_THREADS=4`가 splu(SuperLU의 BLAS 슈퍼노드 갱신)의 마지막
  비트를 움직인다. 코드 변경 때문이 아니다 — 같은 작업 트리를 기본 스레드 수로 돌리면 W4 영수증과
  **정확히 비트 동일**하다. 두 환경 각각은 결정적이다(같은 2.7034e−13 재현). **비트 동일 비교는
  스레드 수까지 고정해야 한다.** W9 §5-3이 권장한 `OPENBLAS_NUM_THREADS=4`는 닫기 성능에는
  유효하지만 영수증 재현 게이트에는 기본값으로 돌려야 한다(이 단서를 `decaps.py` 독스트링에 추가).
- `numerics_id`는 `a17942b9…`(W4) → `f3808b55…`(W8) → **`db837d16…`**(W12)로 바뀐다. `model.py`
  소스 바이트가 들어가기 때문이며(W8 §4-1 이탈 1과 같은 성격) Z는 비트 동일하다.
- 테스트 25건은 **한 프로세스**에서 cuDSS 모델을 여러 개(기저용 nrhs=12·24, 직접용 nrhs=1) 만들며
  끝난다 — W5 §4-2의 관찰을 다시 확인한다.

---

## 6. API 변경 (수치 무관, 기존 시그니처 유지)

```python
mdl.set_decaps(cfg)                       # 그대로: 부분 dict
mdl.set_decaps(cfg, replace=True)         # SPD 기본 구성으로 되돌린 뒤 cfg 적용(검증 먼저)
mdl.release_solver()                      # cuDSS 슬롯 비우기(해제 아님) → 다음 풀이가 새 폭으로 계획
basis = mdl.decap_basis(freqs, chunk=24)  # 모델의 솔버를 자동으로 비켜 두고 만들고 되돌린다
basis = mdl.decap_basis(freqs, backend=Backend("splu", fast=True))   # 기저만 CPU(정확 경로)
basis.save(path)                          # 기본 with_impedances=True → 모델 없이 로드 가능
basis.save(path, with_impedances=False)   # W9 포맷
DecapBasis.load(path, mdl)                # 그대로(numerics_id·N 검사)
DecapBasis.load(path)                     # 모델 없이: Z(config)·receipt() 동작
```

| 심볼 | 계약 |
|---|---|
| `set_decaps(config, replace=False)` | `replace=True`면 `{refdes: SPD model_id}`를 깔고 `config`를 덮는다. 검증은 합쳐진 구성에 대해 먼저 하므로 **거부된 호출은 아무것도 바꾸지 않는다**(리셋도 안 한다) |
| `release_solver() -> CudssLU \| None \| False` | 슬롯에 있던 것을 반환하고 비운다. `free()` 없음 → 인수분해는 프로세스 종료까지 카드에 남는다. `False`(GPU 사용 불가 래치)는 그대로 둔다 |
| `decap_basis(freqs, chunk=24, backend=None)` | `backend`는 **기저 빌드에만** 적용된다. 모델의 백엔드·솔버는 `finally`에서 원상복구되므로, 기저를 만든 뒤에도 같은 모델로 직접 풀이가 된다. 기저가 쓴 솔버는 버려진다(해제 아님) |
| `DecapBasis.save(path, with_impedances=True)` | npz에 `zdec`(nf × 모델 수)와 `meta.receipt`를 추가로 넣는다. 크기 증가는 수 kB |
| `DecapBasis.load(path, model=None)` | 모델이 있으면 기존대로 `numerics_id`·`N`을 검사한다. 모델이 없으면 `with_impedances=True`로 저장된 npz만 받는다(아니면 `ValueError`) |
| 모델 없는 기저 | `Z(config)`·`Z_many`·`resolve`·`model_ids`·`receipt()`가 동작한다. `receipt()`는 저장 당시 영수증에 `loaded_without_model=True`를 붙여 돌려준다. 실측 닫힘 Z는 모델 있는 기저와 **비트 동일**(§3) |
| `DecapBasis.device` / `.backend` | 기저를 만든 장치와 백엔드. `decap_basis`가 모델 백엔드를 되돌려도 영수증이 “무엇으로 만든 기저인지”를 잃지 않게 한다 |

구현 요약: `model.py`는 `set_decaps` 2줄, `release_solver` 4줄, `decap_basis` 6줄(백업–복구).
`decaps.py`는 `self.model is None` 분기 4곳(`resolve`/`_zdec`/`receipt`/`BasisResult.receipt`)과
`save`/`load`의 `zdec`·`meta.receipt`.

---

## 7. 앱·문서에 대한 함의 (소유자 확인용)

1. **`apps/decap_search`의 2단계 프로세스 구조를 없앨 수 있다**(§3). `--stage search/validate`와
   두 번째 모델 빌드가 필요 없고, `search.validate(model, basis, ...)`는 그대로 쓸 수 있다.
   Port18 기준 모델 빌드 1회(약 9 s)와 추출 1회를 아낀다. **앱 파일은 이번 작업 범위 밖이라
   건드리지 않았다.**
2. **탐색 드라이버는 모델 없이 돌 수 있다**: `DecapBasis.load(basis.npz)`로 추출+빌드(P18 약 10 s,
   2 GB)를 건너뛴다(A1 §6-3 요구사항).
3. **W9 §4-2의 “기저 GPU ≤ 1e−6” 계약과 `apps/decap_search`의 `tol = 1e-6`은 갱신해야 한다**
   (§4-4). 앱이 지금 기록하는 `validation.all_pass=false`는 계약이 틀렸기 때문이지 결과가
   틀린 게 아니다. 새 계약은 GPU 1e−5(비결정)/CPU 정확이다.
4. **README·계획의 “GPU에서 한 모델은 기저 모델이거나 스윕 모델이지 둘 다는 아니다”(W9 §2-1)는
   폐기**한다. 근거는 §2.
5. 남은 A1 요구사항 중 **증분 닫기(§6-4)** 는 이번 범위 밖이다(랭크-1 갱신).

---

## 8. 계획에서 어긋난 점

1. W12-b 케이스 4를 계획대로 “`free()`로 해제”로 읽으면 엔진의 `CudssLU.free()`(참조만 버림)가
   되어 크래시 경로가 아니다. 그래서 **케이스 5(nvmath `DirectSolver.free()`)를 추가**했고,
   거기서 0xC0000005가 재현됐다(Port14). Port18에서는 같은 호출이 정상 종료해 “크기순”이 아님도
   기록했다.
2. CPU 기저 비용이 예상(수 시간)보다 훨씬 작아(§4-3) 27점 전부를 실측했다. 부분 측정 후 외삽할
   필요가 없었다.
3. `OPENBLAS_NUM_THREADS=4`가 기본 경로 비트 동일 게이트를 깬다는 사실은 사전에 등록되지 않은
   발견이다(§5). 코드가 아니라 환경의 문제이며, 게이트는 영수증을 만든 환경에서 통과한다.
4. `decap_basis`는 계획의 `release_solver()`를 **내부에서 자동으로** 수행한다(백업–복구). 앱이
   순서를 외우지 않아도 되게 하려는 것이고, 대가는 기저 빌드마다 솔버 하나를 버리는 것(Port18
   378 MB)이다. 명시적으로 쓰고 싶으면 `release_solver()`가 공개 API로 남아 있다.
