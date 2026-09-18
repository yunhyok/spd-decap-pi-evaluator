# W9 — decap 스윕 2단계(Schur): `Model.decap_basis` → `DecapBasis.Z(config)` 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §2-4(2단계), §4 W9(게이트: 전체 재해석 대비 ΔZ < 0.5 %).
원칙 §0(전(全)실장 기본 경로 수치 불변, `tools/research-claude/` 무수정). 2026-09-18.

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/decaps.py` | **신규 227줄** | `DecapBasis`(build/Z/Z_many/resolve/receipt/save/load), `BasisResult` |
| `src/spd_pi_engine/model.py` | +48/−13 (1 018줄) | `_gpu_solver(Y, nrhs)`(기존 `solve`의 cuDSS 래치를 꺼낸 것), `decap_basis(freqs, chunk)` |
| `src/spd_pi_engine/solver.py` | +39/−6 (273줄) | `CudssLU(..., nrhs=1)`, `solve(Y, B=None)` 다중 RHS, `demo_cudss`에 nrhs=3 자체검사 |
| `src/spd_pi_engine/receipt.py` | +23/−3 (316줄) | `mesh_dict(opt)`, `numerics_id_of(model)` 분리(= `Result.receipt`가 쓰던 코드) |
| `src/spd_pi_engine/api.py` | +10 | 모듈 독스트링의 W9 사용법(수치 없음) |
| `src/spd_pi_engine/__init__.py` | +5/−3 | `decaps`, `DecapBasis` 공개 |
| `tests/engine/test_decap_sweep.py` | **신규 189줄** | 데이터 없는 3건 + `gpu` 데이터 1건 |

게이트 스크립트·결과: `WORK_DIR\engine_w9\{w9_gate.py, basis_*.json, basis_*.npz, direct_*.json,
closure_*.json, default_*.json, w9_results.json}`.
`tools/research-claude/`는 건드리지 않았다(`git status --short -- tools/research-claude` 빈 출력).

## 2. 방법

`exp9/run9.py:146-183`의 `multiport()` / `close()`를 엔진 `Model` 위로 옮긴 것이다(연구 트리는
읽기 전용이라 import가 아니라 이식이다).

1. **기저(basis)**: decap을 **전부 뗀** 보드(`set_decaps(모두 None)` = `assemble`이 y = 0을
   스탬프하는 그 구성)를 주파수마다 한 번 인수분해하고, 포트 노드와 각 decap 단자에 단위 전류를
   넣은 RHS를 `chunk`개씩 묶어 푼다. 결과는 주파수당 (1+Nd)×(1+Nd) 복소 행렬
   Z = [[Z_PP, Z_Pd], [Z_dP, Z_dd]].
2. **닫기(closure)**: 구성 하나는 실장된 decap 임피던스로 Schur 보수를 취한다.
   `Z_port = Z_PP − Z_Pd (Z_dd + diag(z_dec))^-1 Z_dP`.
   **미실장(None)은 단자를 열어 두는 것이므로 그 행/열을 닫기에서 빼며**, 이것이 `assemble`의
   y = 0과 정확히 같다(대각에 ∞를 넣지 않는다). 전부 미실장이면 `Z_port = Z_PP`(맨보드).
3. decap 단자 정의는 `run9.port_vectors`와 같다. 이상 GND(`gnd_node = -1`)면 레일 노드만,
   명시 GND(`gnd_node >= 0`)면 노드 쌍(+1/−1 RHS, V[a]−V[b])이다. **두 경우 모두 구현했다**
   (`NotImplementedError` 없음; 명시 GND는 데이터 없는 테스트의 3번 decap이 덮는다).
   프루닝으로 사라진 decap(`map(...) < 0`)은 `assemble`도 스탬프하지 않으므로 기저에서도 빼고
   `unstamped`로 센다(이번 두 포트는 0건).
4. 다중 RHS: `CudssLU`는 `nrhs`를 받아 (N, nrhs) 열우선 RHS로 계획하고 `solve(Y, B)`로 푼다.
   splu는 `lu.solve(B)` 그대로다. 마지막 chunk가 짧으면 0열로 채운다(0 열은 0 해를 준다).

### 2-1. 프로세스 규칙 (cuDSS)

`reset_operands`는 **모양 변경을 거부**하고(nvmath 문서: shapes must match) cuDSS 0.8은 한
프로세스에서 두 번째 `DirectSolver`에 크래시한 전력이 있다. 그래서 **모델의 cuDSS 솔버는 RHS 폭
하나로 고정**이며, `Model._gpu_solver(Y, nrhs)`는 폭이 다른 요청에 `RuntimeError`를 던진다
(조용히 splu로 떨어지거나 크래시하는 대신). 즉 GPU에서 한 모델은 **기저 모델이거나 스윕
모델이지 둘 다는 아니다**. 게이트가 `--task basis`와 `--task direct`를 별도 프로세스로 도는
이유이고, W8이 같은 이유로 쓰던 구조다.

## 3. API

```python
mdl   = rail.build(opt, Backend(solver="cudss", fast=True))
basis = mdl.decap_basis(freqs, chunk=24)      # 주파수당 1회 인수분해 + (1+Nd) RHS
res   = basis.Z({"C1234": None})              # 부분 dict(set_decaps와 같은 규약), ms 단위
res.Z; res.freq; res.receipt()                # BasisResult = Result (breakdown만 금지)
basis.Z_many([cfg1, cfg2, ...])               # 리스트
basis.receipt()                               # numerics_id, freq, Nd, chunk, backend, build_seconds
basis.save(path); DecapBasis.load(path, mdl)  # npz (numerics_id·N 불일치면 ValueError)
```

| 심볼 | 계약 |
|---|---|
| `Model.decap_basis(freqs, chunk=24) -> DecapBasis` | 빌드된 모델 위에서만 돈다. 내부에서 잠깐 전(全)미실장으로 바꾸고 **`finally`로 원래 구성을 복원**한다 |
| `DecapBasis.Z(config=None) -> BasisResult` | `config`는 부분 dict이며 **기저를 만들 때의 구성(`base_config`)에 덮어쓴다**. 모르는 refdes/model_id는 `KeyError`(`set_decaps`와 같은 검증) |
| `BasisResult.receipt()` | `method="decap_basis"`, `decap_config`(전체 해석 결과), `decap_config_sha256`, `closure_seconds`, `decap_basis`(기저 영수증). 기본값 `breakdown_100k=False` |
| `BasisResult.breakdown()` | `NotImplementedError` — 기저는 노드 전압을 갖고 있지 않다. 분해가 필요하면 `set_decaps` + `solve` |
| `DecapBasis.save/load` | npz(`Z`, `freqs`, `ports`, `meta`). `load`는 모델을 요구하고 `numerics_id`·`N`을 검사한다(decap 임피던스는 `ex["models"]`에서 온다) |

## 4. 검증 — 기저 닫기 vs 직접 해석(진실)

`WORK_DIR\engine_w9\w9_gate.py`. 주파수는 W8과 같은 exp5 LADDER 7점을 PowerSI 격자에 스냅한 값
(3.0200e4, 1.0000e5, 3.0200e5, 1.0000e6, 2.5119e6, 1.0000e7, 1.0000e8 Hz), 옵션은 `FLAGS_P` 변종 p,
`chunk=24`. 구성 5종은 W8의 a/b/c에 swap과 all_mounted를 더한 것이다(refdes 순서 고정).

### 4-1. 기본 경로 불변 (§0)

W9는 `model.py`·`solver.py`를 건드렸으므로 전(全)실장 27점 사다리를 다시 쟀다
(`default_*.json`, W8 게이트의 `--task default`를 그대로 import해 출력만 `engine_w9`로 돌린 것).

| 포트 | backend | unknowns | max \|ΔZ\|/\|Z\| vs exp28 | 허용 | 판정 |
|---|---|---|---|---|---|
| 260729 Port14_SITE0 | `Backend()` (splu, fast off) | 34 424 | **3.231881322361924e−12** | 1e−9 | PASS |
| 260729 Port14_SITE0 | cudss + fast | 34 424 | 2.04e−11 | 1e−8 | PASS |
| 260729 Port18_SITE0 | cudss + fast | 275 218 | 8.98e−11 | 1e−8 | PASS |

CPU 값은 W4/W8이 기록한 3.231881322361924e−12과 **자릿수까지 같고**, W4 CPU 영수증 대비
`max |ΔZ|/|Z| = 0.0`, `Z_re`·`Z_im` **배열 비트 동일**이다. 즉 이번 변경은 수치적으로 무해하다.

### 4-2. 닫기 vs 직접 해석

`max |ΔZ|/|Z|`(7점), 게이트 판정선 0.5 %.

| 포트 | backend | 구성 | 미실장 | max \|ΔZ\|/\|Z\| | 최대 지점 | 판정(<0.5 %) |
|---|---|---|---|---|---|---|
| Port14 (35 decap) | splu | all_mounted | 0 | 2.47e−10 | 1.0e6 Hz | PASS |
| Port14 | splu | unmount_1 | 1 | 2.53e−10 | 1.0e6 Hz | PASS |
| Port14 | splu | unmount_10pct | 4 | 2.29e−10 | 1.0e6 Hz | PASS |
| Port14 | splu | swap_all | 0 | 1.30e−10 | 3.02e4 Hz | PASS |
| Port14 | splu | unmount_all | 35 | **0.0**(정확히) | — | PASS |
| Port14 | cudss | all_mounted | 0 | 6.96e−07 | 3.02e4 Hz | PASS |
| Port14 | cudss | unmount_1 | 1 | 6.97e−07 | 3.02e4 Hz | PASS |
| Port14 | cudss | unmount_10pct | 4 | 6.75e−07 | 3.02e4 Hz | PASS |
| Port14 | cudss | swap_all | 0 | 8.50e−07 | 3.02e4 Hz | PASS |
| Port14 | cudss | unmount_all | 35 | 6.86e−07 | 1.0e5 Hz | PASS |
| Port18 (421 decap) | cudss | all_mounted | 0 | 5.81e−07 | 3.02e4 Hz | PASS |
| Port18 | cudss | unmount_1 | 1 | 5.82e−07 | 3.02e4 Hz | PASS |
| Port18 | cudss | unmount_10pct | 43 | 6.11e−07 | 3.02e4 Hz | PASS |
| Port18 | cudss | swap_all | 0 | 4.85e−07 | 3.02e5 Hz | PASS |
| Port18 | cudss | unmount_all | 421 | **1.05e−05** | 3.02e4 Hz | PASS |

15건 모두 0.5 %를 여유 있게 통과한다(최악 1.05e−5 = 0.001 %). 모든 구성에서 기저 쪽 영수증과
직접 쪽 영수증의 `decap_config_sha256`이 일치한다(같은 구성을 계산했다는 증거).

**중요한 관찰 — 기저 경로의 GPU 정확도는 1e−8이 아니라 ~1e−6이다.** 같은 코드가 CPU에서는
2.5e−10인데 GPU에서는 어떤 구성이든 ~7e−7에서 바닥을 친다. 원인은 방법이 아니라 **기저가 항상
맨보드(decap 전부 제거)를 인수분해한다**는 데 있다. W8 §4-2가 이미 기록한 그 행렬이다: decap을
떼면 레일이 사실상 플레인 커패시턴스만 남아 저주파 조건수가 나빠지고, cuDSS 인수분해의 1e−16급
반올림이 3.02e4 Hz에서 1e−6급으로 증폭된다(W8 실측: 전(全)미실장 GPU 1.9e−6 / 5.6e−6). 2단계는
실장 구성을 풀 때도 그 맨보드 분해를 거치므로 **오차 바닥을 통째로 물려받는다**.

- 요청대로 Port14의 전(全)미실장 케이스를 `Backend(solver="splu", fast=True)`로 다시 쟀고,
  결과는 **정확히 0.0**이었다(닫기 = Z_PP, 직접 = 같은 맨보드 해). CPU 경로에서 방법은 무오차다.
- 따라서 계약: **기저 스윕의 정확도는 GPU ≤ 1e−6(전 대역), CPU ≤ 1e−9.** 1e−8이 필요하면
  기저를 CPU로 만들거나 직접 경로(W8)를 쓴다. 0.5 % 게이트에는 아무 영향이 없다.

### 4-3. 데이터 없는 검증(방법 자체)

`tests/engine/test_decap_sweep.py::test_decap_basis_closure_vs_stamping`: 200노드 합성 Y(10×20
격자 + 노드별 션트), decap 3개 — 2개는 이상 기준(-1), 1개는 **명시 GND 노드**(155↔42). 구성 6종
(전(全)실장, 1개 해제, GND쪽 1개 해제, 전부 해제, 전부 모델 교체, 혼합)에서 닫기 vs 직접 스탬프
`max |ΔZ|/|Z| ≤ 1e−12`. 실측은 모두 통과한다(판정선 1e−12).

## 5. 비용과 손익분기

### 5-1. 기저 구축

| 포트 | backend | N | Nd | RHS/주파수 | 기저 구축 | 메모리(기저) | npz | 구축 중 peak RSS |
|---|---|---|---|---|---|---|---|---|
| Port14 | cudss | 34 424 | 35 | 36 | **2.56 s** | 0.145 MB | 0.105 MB | 1 772 MB |
| Port14 | splu | 34 424 | 35 | 36 | 3.00 s | 0.145 MB | 0.087 MB | 365 MB |
| Port18 | cudss | 275 218 | 421 | 422 | **97.9 s** | 19.9 MB | 15.2 MB | 2 387 MB (기저 전 786 MB) |

- Port18은 주파수당 18 chunk(24 RHS) × 7 주파수 = 126회 삼각해 + 7회 인수분해로 98 s,
  주파수당 약 14 s다. 모델 빌드(9.1 s)는 두 경로가 똑같이 낸다.
- 메모리는 계획의 "npz 최대 86 MB"보다 작다(7 주파수 기준 20 MB, 압축 15 MB). RSS 증가분
  약 1.6 GB는 RHS/해 블록(275 218 × 24 × 16 B = 106 MB씩)과 cuDSS 작업 공간이다.

### 5-2. 구성당 비용과 손익분기

| 포트 | backend | 닫기(구성당, 7주파수) | 직접(구성당, 7주파수) | 손익분기 구성 수 |
|---|---|---|---|---|
| Port14 | cudss | **< 1 ms** | 0.213 s (첫 회 1.71 s = cuDSS 분석 포함) | **12.1** |
| Port14 | splu | < 1 ms | 1.764 s | **1.7** |
| Port18 | cudss | **0.12 s** (OPENBLAS_NUM_THREADS=4) | 2.457 s (첫 회 5.93 s) | **41.9** |
| Port18 | cudss | 1.86 s (게이트 프로세스 기본 스레드) | 2.457 s | 164 |

손익분기 = 기저 구축 / (직접 − 닫기). 모델 빌드는 양쪽 공통이라 제외했다.

### 5-3. 이탈 — 닫기 비용은 BLAS 스레드 수에 좌우된다

닫기는 주파수당 **실장 decap 수 크기의 조밀 LU** 하나다(421이면 421³/3 ≈ 2.5e7 복소 flop,
7주파수 합쳐 0.1 s 정도여야 한다). 그런데 게이트의 `--task basis` 프로세스에서는 구성당
**2.0–2.6 s**가 나왔다. 조사 결과 수치가 아니라 **OpenBLAS 스레드 문제**다. 같은 기저 npz를
`--task closure`(cuDSS를 전혀 건드리지 않는 프로세스)에서 다시 쟀다:

| 포트 | OPENBLAS_NUM_THREADS | all_mounted | unmount_1 | unmount_10pct | swap_all | unmount_all |
|---|---|---|---|---|---|---|
| Port18 | 기본(미설정) | 902 ms | 498 ms | 675 ms | 1 434 ms | < 1 ms |
| Port18 | 4 | **119 ms** | 116 ms | 91 ms | 128 ms | < 1 ms |
| Port14 | 기본 / 4 | < 1 ms | < 1 ms | < 1 ms | < 1 ms | < 1 ms |

기본 스레드 수(이 노트북 numpy 2.4.4 + scipy-openblas, MAX_THREADS=24, NO_AFFINITY)에서는
421×421 복소 LU 하나가 39 ms에서 560 ms 사이를 오간다(같은 프로세스 안에서도 5–10배 요동).
`OPENBLAS_NUM_THREADS=4`면 7주파수 합계 40–80 ms로 안정된다.

- 라이브러리는 호출자의 환경 변수를 건드리지 않는다. 대신 `decaps.py` 모듈 독스트링에 이 단서를
  적어 두었고, **구성을 수십 개 이상 쓰는 드라이버는 `OPENBLAS_NUM_THREADS=4`로 실행**하기를
  권한다. 그때 Port18 손익분기는 164 → **42 구성**이다.
- 다르게 보면: 421 decap에서 2단계의 구성당 이득은 20배(2.46 s → 0.12 s), 35 decap에서는
  200배 이상(0.213 s → <1 ms)이다. 다만 Port18은 기저 구축이 98 s라 **42 구성 이상**에서만
  이긴다. 계획 §2-4의 "구성 수십 개 이상일 때만 이득"이 실측으로 확인됐다.

## 6. 영수증

`basis.receipt()` (Port18, cudss):

```json
{"method": "decap_basis", "numerics_id": "2ebf77e1708cdb35…", "freq": [3.02e4 … 1e8],
 "n_decaps": 421, "n_decaps_unstamped": 0, "chunk": 24, "unknowns": 275218,
 "backend": {"solver": "cudss", "fast": true, "ir_steps": 1, "device": "NVIDIA RTX A2000 8GB Laptop GPU"},
 "prune_basis": "all_mounted", "base_config_sha256": "fc7332f0b06e33d8…",
 "build_seconds": 97.95, "basis_MB": 19.95}
```

`basis.Z(config).receipt()`는 `Result` 영수증 전체(그 모델의 `numerics_id`, 플래그, 메시, 빌드
정보, freq/Z)에 다음을 얹는다: `method="decap_basis"`, `decap_config`(해석된 전체 구성),
`decap_config_sha256`, `closure_seconds`, `decap_basis`(위 기저 영수증).

| 구성(Port18) | `decap_config_sha256` |
|---|---|
| all_mounted | `fc7332f0b06e33d8…` (= W8 전(全)실장) |
| unmount_1 | `c8ada8ede98a4e20…` |
| unmount_10pct (43개) | `7e47c5dda62c8883…` |
| swap_all | `9b50340916ab15d2…` |
| unmount_all | `af09070d26f714d5…` (= W8 (c)) |

**이탈: `numerics_id`가 또 바뀐다.** `NUMERIC_MODULES`에 `model.py`·`solver.py` 바이트가 들어
있고 둘 다 수정했다. W8 `f3808b55…` → W9 **`2ebf77e1708cdb350c1ab28991b229b05ac53ac8e06c1be172b2d13222391e87`**.
§4-1이 Z 비트 동일을 증명하므로 수치가 아니라 소스가 바뀐 것이다. 영수증 비교 기준을 갱신한다.

## 7. 테스트

| 테스트 | 프로필 | 시간 | 결과 |
|---|---|---|---|
| `tests\engine -q -k "datafree or decap_sweep"` | 기본 | 1.5 s | 15 passed, 2 skipped(`--gpu`) |
| `tests\engine -q -k "datafree or decap_sweep" --gpu` | `--gpu` | 14.2 s | **17 passed** |
| `test_decap_sweep.py::test_decap_basis_closure_vs_stamping` | 기본 | < 0.5 s | PASS — 6구성, 명시 GND decap 포함, ≤ 1e−12 |
| `test_decap_sweep.py::test_decap_basis_rejects_unknown_names` | 기본 | < 0.1 s | PASS — 모르는 refdes/model_id `KeyError` |
| `test_decap_sweep.py::test_decap_basis_save_load` | 기본 | < 0.5 s | PASS — npz 왕복, 다른 `numerics_id`면 `ValueError` |
| `test_decap_sweep.py::test_decap_basis_port14` | `--gpu` | 13 s | PASS — unmount_1/swap_all 닫기 vs 직접 ≤ 1e−6, 영수증 필드 |
| `test_datafree.py::test_solver_demo_cudss`(nrhs=3 추가) | `--gpu` | 4 s | PASS — 다중 RHS rel 3.7e−14 |
| `test_reproduction.py::test_pcb_s5m6585[Port1_U1_0]`, `test_variant_p_cases[260729-Port14]`, `test_set_decaps_vs_rebuild_port14` | `--gpu` | 8분 53초 | **3 passed** — W9 변경 후 회귀 없음 |

`python -m pytest tests\engine -q -k decap_sweep --gpu` → **4 passed, 34 deselected, 13.2 s**.

## 8. 계획에서 어긋난 점

1. **기저 경로의 GPU 정확도 계약은 1e−6이다**(§4-2). 맨보드 인수분해의 조건수를 물려받기 때문이고
   방법의 오차가 아니다(CPU 2.5e−10, 전(全)미실장 CPU는 정확히 0). 게이트(0.5 %)에는 무관하지만
   앱이 1e−8을 기대하면 안 된다. 필요하면 기저만 CPU로 만든다(Port14 3.0 s, Port18은 미측정 —
   splu로 275 k를 422 RHS × 7 주파수 푸는 비용은 수십 분대로 예상).
2. **GPU에서 한 모델은 기저용이거나 스윕용이다**(§2-1). `_gpu_solver`가 폭이 다른 요청을
   `RuntimeError`로 막는다. 조용한 폴백보다 시끄러운 실패를 택했고, 게이트·테스트는 프로세스를
   나누거나(게이트) 기저는 GPU·직접은 splu로(테스트) 돈다.
3. **닫기 비용이 BLAS 스레드 수에 좌우된다**(§5-3). 라이브러리에서 환경 변수를 만지지 않고
   독스트링·보고서에 단서로 남겼다.
4. **`numerics_id` 변경**(§6). 불가피하다.
5. **`_gpu_solver` 추출로 `Model.solve`의 cuDSS 래치가 3줄 줄었다.** 동작은 같다(모델당 솔버 1개,
   실패 시 splu 폴백, 로그 1줄). §4-1의 비트 동일이 증거다.
6. **`save`/`load`는 계획의 "optional"을 그대로 최소 구현했다**: npz에 Z/freqs/ports/meta만 담고,
   `load`는 모델을 요구하며 `numerics_id`·`N`을 검사한다. decap 임피던스는 저장하지 않는다
   (모델의 `ex["models"]`에서 다시 계산 — 그래야 `assemble`과 같은 값이 나온다).
7. **`demo_cudss`가 이제 한 프로세스에서 `DirectSolver`를 2개 만든다**(nrhs=1 검사 + nrhs=3 검사).
   n=400에서는 두 번 다 정상이었다(크래시 없음, rel 3.7e−14). 큰 행렬에서 cuDSS 0.8이 두 번째
   솔버에 크래시한다는 기존 단서는 유효하므로, 이 자체검사가 언젠가 죽으면 그 8줄을 지우면 된다.

## 9. 남은 것 / 다음

- 앱 쪽 사용 규칙: 구성 수 < 40이면 W8 직접 경로(`set_decaps` + `solve`), 그 이상이면 기저.
  Port14급(decap 수십)은 12구성부터 이득이라 사실상 항상 기저가 낫다.
- 기저를 CPU로 만들 때의 Port18 비용은 미측정이다(§8-1). 필요해지면 재면 된다.
- `chunk`는 24 고정으로만 쟀다. GPU 메모리(8 GB)와 N이 커지면 줄여야 한다(N × chunk × 16 B가
  RHS/해 각각). Port18 chunk=24에서 106 MB씩이었다.
- W10(다중 포트)은 같은 다중 RHS 기계를 쓴다. `CudssLU(nrhs=...)`와 `_gpu_solver(Y, nrhs)`가
  그대로 재사용된다.
