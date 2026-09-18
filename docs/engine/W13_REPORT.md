# W13 — 하드웨어 프로파일과 병렬화 (노트북 가정 제거)

소유자 요구: 엔진이 개발 노트북(i9-12900H 14C/20T, 64 GB, RTX A2000 8 GB)뿐 아니라
워크스테이션(Threadripper 32C/64T, 512 GB, RTX A6000)에서도 잘 돌아야 한다. **노트북에 하드코딩된
것이 남아 있으면 안 된다.** A6000의 VRAM은 소유자 메모에 42 GB로 적혀 있으나 스펙은 48 GB다 —
어느 쪽도 코드에 넣지 않고 **실행 시 탐지**한다.

원칙: 수치 불변(게이트 §7), `tools/research-claude/` 무수정, 앱 파일(`apps/**`, `api.py`,
`receipt.py`) 무수정. 2026-09-19.

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/hardware.py` | **신규 356줄** | `HardwareProfile`·`Plan`·`detect()`, 순수 사이징 함수 4개, 실측 상수, `LAPTOP`/`WORKSTATION` 합성 프로필, `demo()` |
| `tests/engine/test_hardware.py` | **신규 165줄** | 19건, 데이터·GPU 없이 도는 단위 테스트 |
| `src/spd_pi_engine/cli.py` | +43/−8 | `sweep --jobs auto`(기본), `--max-jobs`, `--threads`, `--unknowns`; 하드코딩 “cudss → 4” 삭제; 워커 서브프로세스 `env` |
| `src/spd_pi_engine/decaps.py` | +65/−23 | `_basis_block`(주파수 1개의 수치를 한 곳으로), `_basis_block_worker`, `DecapBasis.build(..., workers=)` |
| `src/spd_pi_engine/model.py` | +16/−3 | `decap_basis(freqs, chunk="auto"|int, backend=, workers=)` |
| `src/spd_pi_engine/backend.py` | +7/−1 | `host_nthreads: int | None`(기본 **4 유지**) |
| `src/spd_pi_engine/solver.py` | +9/−1 | `_host_nthreads(backend)` — `None`이면 워커 환경 → 계획 |
| `src/spd_pi_engine/homogenise.py` | +11/−1 | `window_conductance(chunk_tiles="auto")` |
| `src/spd_pi_engine/__init__.py` | +7/−3 | `hardware`, `HardwareProfile`, `Plan`, `plan_*` 공개 |
| `src/spd_pi_engine/README.md` | +62 | 새 절 **§10 하드웨어 프로파일과 병렬화** |

실험·게이트 스크립트와 영수증: `WORK_DIR/engine_w13/` — `w13_gate.py`, `w13_gate_default.json`,
`w13_gate_workers4.json`(Port14), `w13_gate_workers6_Port18_SITE0_ob{4,default}.json`,
`sweep/`(CLI 연기 시험), `obdefault.log`.

## 2. 프로필과 탐지

```python
HardwareProfile(cpu_physical, cpu_logical, ram_total_GB, ram_free_GB,
                gpus=({"index", "name", "vram_total_MB", "vram_free_MB"}, ...))
```

`detect()`는 절대 예외를 던지지 않고, 있는 것부터 쓴다.

| 항목 | 1순위 | 폴백 |
|---|---|---|
| 논리 CPU | `psutil.cpu_count()` | `os.cpu_count()` |
| 물리 코어 | `psutil.cpu_count(logical=False)` | Windows `wmic cpu get NumberOfCores`, Linux `/proc/cpuinfo`의 (physical id, core id) 쌍, 그것도 없으면 `logical // 2` |
| RAM | `psutil.virtual_memory()` | Windows `ctypes` `GlobalMemoryStatusEx`, Linux `/proc/meminfo`, 실패 시 0 |
| GPU | `cuda.bindings.runtime`(`cudaGetDeviceCount`/`cudaGetDeviceProperties`/`cudaMemGetInfo`) | `nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader,nounits`, 그것도 없으면 빈 목록 |

**이 기계에는 psutil이 없다.** 그래서 폴백 경로가 실제로 쓰이며, 실측 결과가 맞다:
`14C/20T, 42/64 GB RAM free, NVIDIA RTX A2000 8GB Laptop GPU 7562/8589 MB free`
(`wmic` 14/20, `GlobalMemoryStatusEx` 63.68/42.5 GB, `cudaMemGetInfo` 7.56/8.59 GB).

주의로 남겨 둔 것: `cudaMemGetInfo`는 해당 장치의 primary context를 만든다(약 300 MB). 즉 보고되는
free VRAM은 **호출자 자신의 컨텍스트를 이미 뺀 값**이라 계획에는 오히려 맞지만, `detect()`를 부른
sweep 드라이버는 사는 동안 컨텍스트를 하나 붙들고 있다. 한 번만 부르고 프로필을 돌려 쓴다.

## 3. 사이징 규칙과 실측 상수

```
jobs      = min(cpu_jobs, vram_jobs, ram_jobs, n_ports, max_jobs)      # 각 항 최소 1
cpu_jobs  = (cpu_physical - 1) // threads_per_job      # 드라이버·OS 몫으로 코어 1개
vram_jobs = vram_free_MB // vram_per_process_MB(N)     # GPU 솔버일 때만
ram_jobs  = ram_free_MB  // rss_per_process_MB(N)
threads   = min(8, cpu_logical // jobs)
```
`Plan.limits`에 어느 항이 잡았는지, `Plan.notes`에 왜인지가 남는다. 모든 사이징 함수는
**순수**하다 — 프로필을 인자로 받고, `detect()`도 `os.environ`도 부르지 않으며, 카드를 건드리지
않는다. 그래서 워크스테이션 계획을 노트북에서 단위 테스트할 수 있다.

| 상수 | 값 | 출처(실측) |
|---|---|---|
| `VRAM_CONTEXT_MB` | 300 | W12-a §2: Port14에서 컨텍스트+nrhs=1 솔버 283 MB, 그중 솔버 40 MB |
| `vram_lu_MB(N)` | `10.7 + 8.513e-4·N` | W12-a §2 두 점 직선: **N 34 424 → 40 MB**, **N 275 218 → 245 MB**. 교차 검증: P18 `nnz_LU` 8.75e6 × 16 B ≈ 140 MB + 인덱스 ≈ 245 MB. N 1 233 161에서 직선 1 060 MB vs `nnz_LU` 7.63e7 기준 1 220 MB → **상단에서 약 15 % 낙관** |
| `VRAM_HOMOG_BYTES_PER_TILE` | 96 (= float64 12개) | `homogenise._gx`에 동시에 살아 있는 작업 배열(Wf, gh, gv, diag, dinv, bfull, x, r, z, p, Ap, `A()` 내부 임시). `chunk_tiles=6e6`에서 **576 MB**. W12-a 수치에 빠져 있던 항이고 워커의 최대 VRAM 소비원이다 |
| `rss_MB(N)` | `1660 + 1.733e-3·N` | W4/W7 영수증 10건(`stats[].rss_MB`) 최소제곱, N 22 438–1 233 161, 최대 잔차 4.7 %. P18 2 135 MB(실측 2 085), 1.23 M 포트 3 795 MB(실측 3 749) |
| `THREADS_PER_JOB` | splu 2, cudss 3 | **유일한 판단값**(§8-1) |
| `MAX_THREADS_PER_JOB` | 8 | W9 §5-3: 조밀 LU에서 스레드를 8 이상 주면 요동만 커진다 |
| `CHUNK_RHS_DEFAULT` / `MAX` | 24 / 256 | W9 §5-1의 A2000 값 / 그 이상은 이득 없음 |
| `CHUNK_TILES_8GB` | 6e6 | `window_conductance`의 기존 기본값(8 GB 카드 기준) |

`plan_basis(profile, N, n_decaps, n_freqs, solver)`:
- `chunk` = `24 × max(1, free_MB // 8192)`를 `n_decaps+1`, 256, “두 개의 (N, chunk) complex128
  블록이 여유 메모리의 1/4을 넘지 않을 것”으로 자른다. GPU면 free VRAM, CPU 기저면 free RAM.
- `workers` = `min(n_freqs, (cpu_physical-1)//2, ram_free//rss(N))`, cuDSS면 1(카드 1장).

`plan_chunk_tiles(profile)` = `6e6 × max(1, vram_total_MB // 8192)` — 8 GB 카드에서 정확히 기본값.

## 4. 노트북 vs 워크스테이션 계획

`hardware.LAPTOP`(실측 프로필) / `hardware.WORKSTATION`(문서·테스트용 합성: 32C/64T, 512 GB
(free 480), A6000 48 GB(free 48 000 MB)). 소유자의 42 GB 메모는 반영하지 않았다 — 실행 시 탐지가
진짜 값을 쓴다.

| 항목 | 노트북 (A2000 8 GB) | 워크스테이션 (A6000 48 GB) |
|---|---|---|
| sweep jobs, cudss/auto | **4** (cpu 4 / vram 6 / ram 19) | **10** (cpu 10 / vram 41 / ram 234) |
| sweep jobs, splu | **6** (cpu 6 / ram 19) | **15** (cpu 15 / ram 234) |
| 워커당 BLAS 스레드 | 5 (jobs 4), 3 (jobs 6) | 6 (jobs 10), 4 (jobs 15) |
| `decap_basis(chunk="auto")` GPU / CPU | 24 / 120 | 120 / 256 |
| `decap_basis(workers="auto")` splu, 27주파수 | 6 | **15** |
| `plan_chunk_tiles` | 6e6 (기본 그대로) | 36e6 |
| N=1.23 M 포트에서 cudss jobs | **3** (VRAM이 잡는다) | 10 (여전히 CPU가 잡는다) |

- **노트북 값은 W13 이전과 같다.** `auto`가 A2000에서 4를 내는 것은 설계 제약이고
  `test_laptop_sweep_jobs`가 고정한다.
- A6000에서는 VRAM이 구속 조건에서 빠지고 호스트 CPU가 잡는다.
- GPU가 없으면 `cudss`/`auto`는 **splu로 계획하고 `notes`에 남긴다**(모델별 폴백은 그대로).
  RAM이 모자라면 jobs 1.

### 4-1. 워크스테이션 예상 벽시계(외삽, 가정 명시)

| 작업 | 노트북 실측 | 워크스테이션 예상 |
|---|---|---|
| 92포트 sweep (cudss+fast) | 23분 (jobs 4; 연구 드라이버, 계획 §4) | **9–10분** (jobs 10) |
| P18 CPU 기저 27주파수, 직렬 | **9.4분** (주파수당 21.0 s, `OPENBLAS_NUM_THREADS=4` 실측) | 9.4분 (동일 가정) |
| P18 CPU 기저 27주파수, 주파수 병렬 | 약 **2.3분** (workers 6, 실측 2.62배) | **약 1분** (workers 15, 2 웨이브) |

가정: (1) 포트당·주파수당 계산 시간이 두 기계에서 같다 — A6000은 SM 수·대역폭이 A2000보다 크고
Threadripper 코어는 클럭이 낮지만 IPC가 비슷하므로 **보수적인 가정**이다. (2) sweep 묶음 효율
80 %를 그대로 쓴다. 하한은 가장 큰 포트 1개의 벽시계(N 1.23 M, 실측 226 s ≈ 4분)다. (3) 기저는
`ceil(n_freqs / workers)` 웨이브로 돈다 — 27주파수·15워커면 2 웨이브라 양자화 손실이 작다.
(4) 워크스테이션은 15워커 × 4스레드 = 60/64 논리 CPU로 과다구독이 없다(노트북 실측은 6 × 4 = 24/20
으로 **과다구독 상태**에서 잰 값이라 더 보수적이다).

## 5. 배선

| 지점 | 전 | 후 |
|---|---|---|
| `cli.sweep` | `--jobs` 기본 4, `solver in (cudss, auto)`면 **무조건 4로 클램프** | `--jobs auto`(기본) → `plan_sweep(detect(), ...)`. 숫자를 주면 계획값을 넘을 때만 경고+클램프. `--max-jobs`로 계획 상한을 조절, `--unknowns`로 계획을 날카롭게, `--threads auto|N|0` |
| sweep 워커 환경 | 상속 | `subprocess.run(env=...)`에 `OPENBLAS/MKL/OMP_NUM_THREADS` = 계획값. **호출자 환경은 건드리지 않는다** |
| `Backend.host_nthreads` | `int = 4` | `int | None = 4`. `None`이면 `OMP_NUM_THREADS`(워커면 부모가 넣어 준 값) → `plan_threads`. 기본값은 4 그대로 |
| `Model.decap_basis` | `(freqs, chunk=24, backend=None)` | `(freqs, chunk=24|"auto", backend=None, workers=1|"auto")` |
| `DecapBasis.build` | 주파수 루프 안에 chunk 루프가 인라인 | `_basis_block(Y, ports, chunk, gpu)` 하나로 뽑아 **직렬·병렬이 같은 코드**를 쓴다 |
| `homogenise.window_conductance` | `chunk_tiles=6_000_000` | 같은 기본값 + `"auto"` 옵트인 |

### 5-1. 주파수 병렬 CPU 기저 설계

계획서가 제시한 “워커가 캐시된 레일에서 모델을 다시 빌드”(P18 15 s × 워커 수) 대신 **더 작고 더
깨끗한 쪽**을 골랐다. 부모가 지금처럼 `assemble(f)`를 하고 `(Y, ports, chunk)`만
`ProcessPoolExecutor`로 보낸다. 워커는 `splu` + 삼각해만 하고 **(K, K) 블록**을 돌려준다.

- **왜 되나**: 기저의 주파수는 서로 독립이고(주파수당 인수분해 1회), 비트 동일해야 하는 것은
  조립된 Y인데 그 Y를 여전히 부모가 만든다. 모델 재빌드도, 캐시 접근도, 추출도 워커에 없다.
- **왜 (N, K) 해가 아니라 (K, K)인가**: P18에서 해는 275 218 × 422 × 16 B = **1.9 GB**,
  블록은 422² × 16 B = **2.8 MB**. 돌려보낼 수 있는 것은 블록뿐이다.
- 주파수를 `workers` 크기 웨이브로 내보내므로 Y 사본이 동시에 최대 `workers`개다.
- 워커는 부모 환경을 상속한다 → BLAS 스레드 수, 따라서 마지막 비트가 부모와 같다(W12-a §5).
- `workers > 1`은 `solver="splu"`에서만 동작한다(카드 1장, 프로세스당 cuDSS 컨텍스트 1개).
  `cudss`/`auto`로 부르면 경고 한 줄과 함께 직렬로 돈다.

## 6. 테스트 — `tests/engine/test_hardware.py` (19건, 데이터 없음, 1.9 s)

| 묶음 | 내용 |
|---|---|
| 실제 기계 | `detect()`가 cpu ≥ 1, logical ≥ physical, ram > 0, GPU 목록은 비어도 됨(필드·범위 검사). 이 기계에서 실제로 계획이 나오고 `jobs × threads ≤ logical`. `demo()` |
| 노트북 합성 프로필 | cudss **4**, auto **4**, splu **6**. N=1.23 M이면 VRAM이 잡아 내려간다. `chunk` 24, `workers` 1, `chunk_tiles` 6e6 |
| 워크스테이션 합성 프로필 | jobs가 모든 한계를 실제로 만족(코어·RAM·VRAM 곱셈 검사), cudss 10 / splu 15, `workers` 15(주파수 7개면 7), `chunk` 256, `chunk_tiles` 36e6 |
| 경계 | GPU 없음 → splu로 계획 + note, `limits`에 vram 없음 / RAM 1.5 GB → jobs 1 / 1코어·1포트 → jobs 1 / `max_jobs` / 미지수 5천만 → jobs 1, chunk ≥ 1 |
| 성질 | `plan_threads`가 과다구독하지 않고 **`os.environ`을 건드리지 않는다**, 사이징이 결정적(같은 입력 → 같은 `Plan`), VRAM 직선이 실측 두 점을 맞춘다, RSS 식이 영수증 두 점을 5 % 안에서 맞춘다, 균질화 항 = 576 MB @ 6e6 |

## 7. 게이트

| 게이트 | 명령 | 결과 |
|---|---|---|
| 테스트 | `python -m pytest tests\engine -q -k "datafree or decap_sweep or set_decaps or w12a or hardware" --gpu` | **44 passed**, 30 deselected, 65.1 s (W12-a의 25건 + 신규 19건) |
| 기본 경로 CPU 비트 동일 | `w13_gate.py default` (260729 Port14, `Backend()`, W4 영수증 27점, `OPENBLAS_NUM_THREADS` **미설정**) | **PASS — `Z_re`·`Z_im` 비트 동일, max rel 0.0** |
| 주파수 병렬 기저 (요구 게이트) | `w13_gate.py workers 4` (Port14, CPU 기저 workers=4 vs 1, 구성 4종 닫기) | **PASS — 기저 배열·닫기 모두 비트 동일(max rel 0.0)**, 요구치 1e-12보다 좋다 |
| 〃 대형 포트 | `w13_gate.py workers 6 Port18_SITE0`, `OPENBLAS_NUM_THREADS=4` | **PASS — 비트 동일(0.0)**, 직렬 146.9 s → 병렬 56.2 s, **2.62배** |
| 〃 기본 스레드 | 같은 명령, `OPENBLAS_NUM_THREADS` 미설정 | **PASS — 비트 동일(0.0)**, 직렬 308.3 s → 병렬 141.6 s, **2.18배** |
| CLI 연기 시험 | `sweep --ports Port14_SITE0,Port19_SITE0 --solver cudss --fast --ladder`(`--jobs` 미지정) | `jobs=2 bound by ports (cpu 4, ram 19, vram 6, ports 2)`, `2 jobs x 8 BLAS threads`, 2 ok |

`numerics_id`는 `db837d16…`(W12) → **`27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0`**로
바뀐다. `NUMERIC_MODULES`에 `model.py`·`solver.py`·`homogenise.py` 소스 바이트가 들어가고 셋 다
고쳤기 때문이며, 같은 게이트가 **Z 비트 동일**을 증명한다(W8 §4-1, W9 §6, W12-a §5와 같은 성격).
영수증 비교 기준을 갱신한다.

## 8. 계획에서 어긋난 점 / 판단

1. **`threads_per_job`(splu 2, cudss 3)은 실측이 아니라 보정값이다.** VRAM 실측 상수만으로는
   A2000에서 jobs가 6이 나오는데(P18급 포트 기준), 계획 §5-3/§5-5와 `runall15.py`가 손으로 박아
   둔 안전값은 4다. cuDSS 워커가 `assemble`과 cuDSS 호스트 재배열(`host_nthreads`)에 호스트 CPU를
   쓰면서 카드를 계속 먹여야 한다는 사실이 근거지만, **3이라는 숫자 자체는 알려진 4를 재현하도록
   맞춘 것**이다. 문서에 그렇게 적었다. 기계가 다르게 굴면 이 상수를 고친다 — 기계 이름으로
   분기하지 않는다.
2. **주파수 병렬 기저는 “워커가 모델 재빌드” 대신 “부모가 Y를 조립해 넘김”으로 구현했다**(§5-1).
   계획이 허용한 대안이고, 더 짧고 워커당 15 s를 아끼며 Y의 비트 동일성이 구조적으로 보장된다.
3. **`Backend.host_nthreads` 기본값을 4에서 바꾸지 않았다.** cuDSS 호스트 재배열 스레드 수는
   인수분해를 움직일 수 있는데 지금까지의 모든 GPU 영수증이 4에서 나왔다. `None`을 주면 계획에서
   가져오도록만 했다(요구사항의 “`None`일 때 계획에서”).
4. **`chunk_tiles`는 자동이 기본이 아니다.** `batched_gx`는 배치의 모든 창이 수렴할 때까지 돌므로
   배치 크기가 바뀌면 CG 반복 수와 G의 마지막 비트가 바뀐다. 즉 이 값은 **수치 중립이 아니다**.
   `window_conductance`의 기본값은 6e6 그대로 두고 `"auto"`로만 옵트인하게 했다(이 노트북에서는
   auto도 정확히 6e6이라 게이트가 통과한다).
5. **sweep 워커의 스레드 환경 설정은 CPU 영수증의 마지막 비트를 움직인다**(W12-a §5, 2.7e-13).
   허용치 게이트(1e-9)에는 무관하지만 비트 비교에는 영향이 있으므로 `--threads 0`(상속)을 넣었다.
   직접 `solve` 경로는 환경을 전혀 건드리지 않으므로 §7의 비트 동일 게이트와는 무관하다.
6. **예상 밖의 발견 — 기본 OpenBLAS 스레드 수가 CPU 기저를 2배 이상 느리게, 그리고 불안정하게
   만든다.** P18 CPU 기저 직렬이 `OPENBLAS_NUM_THREADS` 미설정에서 주파수당 **49.5 s / 44.0 s**
   (두 번 실행, 같은 코드·같은 입력), `=4`에서 **21.0 s**였다. 후자는 W12-a의 20.5 s/주파수와
   일치한다. W9 §5-3이 닫기에서 본 현상(같은 조밀 LU가 39–560 ms로 요동)이 인수분해에서도 난다 —
   20논리 CPU에 24스레드를 붙이는 기본값이 SuperLU의 BLAS 슈퍼노드 갱신을 망친다. 이는 §5의
   “워커 환경에 스레드 수를 넣는다”는 설계를 사후에 정당화한다. 병렬 가속도 같은 이유로
   기본 스레드에서는 2.18–2.83배로 흔들리고 `=4`에서 2.62배로 안정적이다.
7. **`--jobs`가 문자열이 됐다**(`auto` 기본). 기존 `--jobs 4`는 그대로 동작하며, 계획값보다 크면
   경고 후 클램프한다(예전의 “cudss면 무조건 4”와 달리 이유가 출력된다).

## 9. 남은 것 / 다음

- 워크스테이션 실측이 없다. §4-1은 전부 노트북 실측의 외삽이며 가정을 명시했다. 실기가 생기면
  `python -m spd_pi_engine.hardware`(=`demo()`) 한 줄로 계획을, `w13_gate.py workers N PORT`로
  기저 가속을 그대로 잴 수 있다.
- `vram_lu_MB(N)` 직선은 상단(N > 6e5)에서 약 15 % 낙관이다. 큰 포트만 도는 sweep이 VRAM에
  걸리면 `nnz_LU` 기반(포트별 `estimate_cost()`)으로 바꾸는 것이 다음 개선이다.
- `plan_basis`의 `chunk` 상한 256은 재지 않았다(W9는 24만 쟀다). A6000에서 120 vs 24를 한 번
  재면 상한을 근거 있게 정할 수 있다.
- sweep 드라이버가 `detect()` 때문에 CUDA 컨텍스트를 하나 붙든다(§2). 필요하면 `nvidia-smi`
  경로를 먼저 쓰도록 한 줄 뒤집으면 된다.
