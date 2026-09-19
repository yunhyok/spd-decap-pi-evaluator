# spd_pi_engine — SPD → Z(f) PI 계산 엔진 (v0.1)

`tools/research-claude/`의 동결 연구 모델(기준선 exp28/p, D7)을 애플리케이션이 호출할 수 있는 패키지로 옮긴 것이다. 수치는 연구 코드와 **동일**하며(영수증 재현으로 증명, 아래 §5), 연구 코드는 손대지 않았다. 설계 근거와 결합 목록은 `docs/engine/ENGINE_PLAN_2026-09-18.md`, 단계별 결과는 `docs/engine/W1..W10_REPORT.md`, `W12A/W12C_REPORT.md`, 앱 시제품 `APP_*_REPORT.md`.

## 1. 무엇을 계산하나
PowerSI SPD 파일 하나와 포트 이름 하나를 받아, 그 레일의 PDN 임피던스 Z(f)(1 kHz–100 MHz)를 2-D plane-pair + 회로 하이브리드 모델로 계산한다. 참조면은 PowerSI 관례(cavity-wall, "powersi-compatible")가 기본이고, 물리적 GND 전용 탐색("physical-gnd")도 옵션이다.

정확도 범위(2026-09-18, `docs/research-claude/2026-09-15/PROGRESS_SUMMARY_2026-09-18.md` §4):
- PCB(s5m6585, 160포트): 1 MHz 오차 중앙값 0.58 %, G3(<10 %) 통과 80 %.
- 패키지 대형 평면 레일: ≤ 3 %.
- 패키지 전 포트: 1 MHz 오차 중앙값 28–32 %, Re Z_ref/Re Z_model @100 kHz 중앙값 1.46 — **급전 경로 R 관례 차이 미해결**.
- f_res 편향 +5–14 %(설계 무관, 원인 미확정).
- microvia는 드릴 지름의 구리 충전 원기둥으로 계산한다(소유자 결정 D9, 2026-09-19; 40 µm via는 어느 분기에서든 이미 충전 면적). core PTH(150 µm)는 도금 배럴. via 길이는 층 중심 간.
영수증의 `validity` 필드가 이 다섯 줄을 항상 담는다. 앱은 이를 표시해야 한다.

## 2. 빠른 사용
```python
from spd_pi_engine import Design, ModelOptions, Backend, FLAGS_P, attach_reference

d    = Design.open(r"D:\Downloads\examples\S4LB002-2Para_260729_1_injected.spd")
d.ports()                                              # SPD .Port 순서
rail = d.rail("Port18_SITE0", cache_dir=r"D:\engine-cache")   # 추출 + 이웃 층(캐시 히트 약 1 s)
opt  = ModelOptions(reference="powersi-compatible", flags=FLAGS_P,
                    h=200.0, fh=50.0, top_h=50.0, sub=(20, 10, 10), fringe=True)
mdl  = rail.build(opt, Backend(solver="cudss", fast=True))    # A2000 있으면 GPU, 없으면 splu 폴백
res  = mdl.solve([1e3, 1e4, 1e5, 1e6, 1e7, 1e8])
res.Z; res.breakdown(1e5); rec = res.receipt()                # 영수증 v1(dict)
```
CLI(`python -m spd_pi_engine …`, `docs/engine/W7_REPORT.md`):
```
ports  --spd PATH                                   # 포트 이름 목록
info   --spd PATH --port NAME --cache DIR            # rail_net, decap 수, 노드/via/trace 수, 비용 추정(JSON)
solve  --spd PATH --port NAME --cache DIR --out R.json [--variant legacy|p|q|pmk] [--reference …]
       [--freqs 1e3,1e4,… | --ladder] [--solver splu|cudss|auto] [--fast] [--ref-npz Zdiag.npz] [--breakdown-100k]
verify --receipt A.json --against B.json [--tol 1e-8]  # 주파수 배열 일치 + max|ΔZ|/|Z|, exit 0/1
sweep  --spd PATH --ports all|a,b --cache DIR --outdir DIR --jobs N [solve 옵션]   # 포트당 1 프로세스, 재개 가능, cudss면 jobs ≤ 4
```
예: 260729 Port18을 `--variant p --ladder --solver cudss --fast --ref-npz …`로 풀면 `unknowns=275218 wall=30 s err_1MHz=0.0081`, `verify`로 exp28 영수증과 5.5e-11.

## 3. 수치가 어디에 동결돼 있나
| 모듈 | 내용 | 원본(연구) |
|---|---|---|
| `geometry.py` | 아트워크 래스터화(`rasterize`), 레이어 래스터 캐시, 스캔라인 점-포함 판정(matplotlib 3.10.9 규칙 전사), PIL 서브셀 래스터 | `exp1/model.py`, `common/scanline.py`, `exp3/homog.raster_image` |
| `homogenise.py` | 창 컨덕턴스 균질화(배치 Jacobi-PCG, `face_fix`), 창 중복 제거, CuPy 분기 | `exp3/homog.py` |
| `reference.py` | 참조면 탐색(`ReferenceSearch`, mode 2종), `Stack`/유전율 표, `DesignConventions` | `exp1/exp1b.py`, `exp8/run8.py`, `exp1/model.py` |
| `model.py` | `Sheet`, `Model`(build/assemble/solve/breakdown), `ModelOptions`, 플래그 16개와 프리셋 | `exp3/model3.py` + `exp4/run4.py` + `exp5/pipeline.py` |
| `solver.py` | splu 경로용 패턴 캐시(`YPattern`), cuDSS 래퍼(`CudssLU`, 반복 정제 1단계) | `common/fast_assemble.py`, `common/cudss_solver.py` |
| `spd_source.py` | SPD 파싱 어댑터(제품 파서 사설 API 10개를 한 블록에), 포트 열거, 추출, `prepare` | `exp1/extract.py`, `exp5/pipeline.prepare` |
| `cache.py` | 캐시 v2(제품 객체를 피클하지 않음, 원자적 쓰기, 헤더 불일치 시 재생성) | `exp5/pipeline.py` |
| `api.py`, `receipt.py` | `Design/Rail/Model/Result`, 영수증 v1, `numerics_id`, G1–G5 게이트(`attach_reference`) | `exp11/run11.py`, `exp3/run3.py` |

`numerics_id` = sha256(reference_mode, flags, mesh, conventions, 위 5개 수치 모듈의 소스). 이 값이 같은 영수증끼리만 수치 동일을 요구한다. 수치 모듈을 한 줄이라도 바꾸면 id가 바뀌고, §5의 재현 테스트가 새 값을 증명해야 한다.

## 4. 옵션
`ModelOptions` 기본값 = 연구 동결 경로(EXP-8). 기준선(D7)은 `flags=FLAGS_P`.

| 프리셋 | 플래그 | 의미 |
|---|---|---|
| `FLAGS_LEGACY` | 전부 기본(False) | EXP-8 동결 경로. P18 1 MHz 2.69 % |
| `FLAGS_P` | `c_unit_fix`, `homog_face_fix` | D7 기준선(평면 C 단위 수정, 균질화 경계조건 수정). P18 0.81 % |
| `FLAGS_Q` | p + `via_len_surface` | via 길이 표면 간(EXP-32, 기록만) |
| `FLAGS_PMK` | p + `via_R_skin`, `zs_wall_skin_re` | 표피 R 항(EXP-35, 기록만) |

나머지 플래그(`eps_table, c_all_refs, zs_cell, zs_wall, fringe_no_thresh, fringe_no_cap, homog_L_noG, via_area_exact, via_L_twowire, zs_wall_skin, void_fill_um`)는 EXP-11~36에서 시험해 채택하지 않은 변형이다. 물리는 `docs/research-claude/2026-09-15/MODEL_PHYSICS.md`.

`Backend(solver="splu"|"cudss"|"auto", fast=False, host_nthreads=4, ir_steps=1)`:
- `fast=True`: assemble 패턴 캐시·균질화 창 중복 제거·스캔라인 판정. **Y와 G가 비트 동일**(EXP-37/38/40).
- `solver="cudss"`: NVIDIA cuDSS로 주파수별 인수분해(A2000). `auto`는 가능하면 GPU, 실패 시 splu.
- 표준 실행: `Backend(solver="auto", fast=True)`. 포트 벽시계 P18 1095 → 33 s.

**GPU 정확도 계약**(CPU splu 대비 max |ΔZ|/|Z|): 패키지 레일 7케이스 ≤ 1e-8(실측 ≤ 5.6e-9). PCB 레일은 저주파 행렬 조건수 때문에 더 벌어진다 — Port1 100 kHz 1.6e-7, Port50 1 MHz 1.7e-8 — 반복 정제 단계를 늘려도 불변(`docs/engine/IR_REPORT.md`). **E3(사전 등록, `docs/engine/ENGINE_PLAN_2026-09-18.md` 2026-09-18 소유자 결정)**: 레일 유형별 계약을 결과를 본 뒤 조정하는 대신 고정한다 — 패키지 레일은 CPU ≤ 1e-9 / GPU ≤ 1e-8(불변), PCB 레일(`tests/engine/fixtures/exp30`, s5m6585)은 CPU ≤ 2e-9, GPU는 f ≥ 1 MHz ≤ 1e-7·전 대역 ≤ 1e-6. 이 계약으로 옛 strict xfail 2건(PCB Port50 CPU 1.016e-9, GPU 1 MHz 1.688e-8)이 통과로 해소됐다 — 값이 계약을 넘으면 조정이 아니라 조사 대상이다.

## 5. 재현 테스트
`tests/engine/`(W5, `docs/engine/W5_REPORT.md`). 데이터가 없으면 skip, SPD sha256이 `tests/engine/fixtures/spd_sha256.json`과 다르면 fail. 프로파일: 데이터 없음 `pytest tests/engine -k datafree`(1.3 s) / 기본 `pytest tests/engine`(P14·P18·PCB Port1 CPU, 약 27 분) / GPU 회귀 `pytest tests/engine -m gpu --gpu`(7케이스+PCB, 약 4 분) / 전량 `--slow --gpu`(약 2.6 h, 릴리스 전 1회). 상시 회귀는 기본 + GPU를 권한다.
- (i) 260729 Port18, `FLAGS_LEGACY`, splu, 1 MHz 최근접 1점 → `docs/research-claude/2026-09-15/results/exp8/result_260729_Port18_SITE0_any.json` 대비 5.83e-12(한도 1e-9), unknowns 261124.
- (ii) 변형 p 7케이스 → `tests/engine/fixtures/exp28/` 대비 CPU ≤ 1e-9, GPU ≤ 1e-8.
- (iii) PCB s5m6585 → `tests/engine/fixtures/exp30/` 대비 CPU ≤ 2e-9(E3), GPU f ≥ 1 MHz ≤ 1e-7·전 대역 ≤ 1e-6(E3).
- 데이터 없는 셀프체크: `homogenise.selfcheck_face_fix()`, `solver.demo()`, `geometry.demo()`, `cache.demo()`, `receipt.demo()`, `spd_source.check_parser_api()`.

연구 코드 쪽 재현 게이트는 그대로 `python tools\research-claude\common\smoke_port18.py`(5.83e-12)다.

**스레드 수와 비트 동일성**(W12-a §5): CPU splu 경로는 `OPENBLAS_NUM_THREADS`에 따라 결과가 2.7e-13 수준으로 달라진다(각 설정 자체는 결정적). 허용치 게이트(1e-9)에는 영향이 없지만, "비트 동일" 비교(W4/W8 CPU 영수증 대조)는 같은 스레드 수에서만 성립한다. 기저 닫힘 속도를 위한 `OPENBLAS_NUM_THREADS=4` 권장(W9)은 유지하되, 비트 비교를 할 때는 영수증을 만든 설정과 맞춘다.

## 6. 캐시
`cache_dir` 아래 `extract_{port}_{key}.pkl`, `shapes_{layer}_{key}.pkl`. 키 = sha256(SPD sha256, 포트/층, max_layers, cache_format=2, parser_version). 제품 객체는 피클하지 않는다(스택업은 dict, decap 모델은 `.SUBCKT` 원문 → 로드 시 재파싱). 손상·버전 불일치는 조용히 재생성. 여러 프로세스가 같은 디렉터리를 써도 안전(원자적 쓰기, 8×8 스트레스 PASS). P18 추출 캐시 33 MB, 층 shapes 각 약 11 MB.

## 7. 제약과 알려진 문제
- 의존: numpy ≥ 2, scipy, matplotlib 3.10.x(**채움 규칙이 수치의 일부**), Pillow, 제품 파서 `spd_decap_pi`. GPU는 `pip install .[gpu]`(nvmath-python, nvidia-cudss-cu12, cuda-bindings 12.*, cupy-cuda12x).
- cuDSS 0.8.0.10: `DirectSolver.free()`와 `SYMMETRIC`이 크래시 → 해제 생략, GENERAL 사용. 오래 사는 프로세스에서 모델을 많이 만들면 핸들이 누적되므로 GPU 계산은 작업자 프로세스에서 돌린다(`sweep`이 그 구조). 동시 프로세스 수는 하드웨어 프로파일에서 산정한다(§10; A2000 8 GB에서는 4, A6000급에서는 CPU가 제한).
- 미지수 130만 포트: 호스트 RSS 약 4 GB, GPU 벽시계 155 s. GUI 스레드에서 직접 부르지 말 것(`Rail.estimate_cost()`로 먼저 판단).
- decap 구성 변경: `mdl.set_decaps({refdes: model_id | None})` → 재빌드 없이 `solve()`(P18 GPU 구성당 약 2 s, 재빌드 약 18 s). 프루닝은 전(全)실장 기준으로 1회 고정(`prune_basis="all_mounted"`; 두 설계에서 재빌드와 N 동일, ΔZ ≤ 1.2e-10). `add_decap_model(id, subckt_text)`로 새 모델 등록. **주의**: decap을 전부(또는 거의 전부) 떼는 극단 구성은 저주파 조건수가 나빠져 GPU 오차가 1e-6급이 되므로 그 경우 저주파는 CPU 경로로 본다(`docs/engine/W8_REPORT.md` §4-2).
- decap 스윕(W9): `basis = mdl.decap_basis(freqs)` → `basis.Z(config)` / `basis.Z_many(configs)`. 맨 보드(decap 전부 제거)를 포트+decap 단자 다중 RHS로 한 번 풀어 두고(P18: 422 RHS × 7점 98 s, 20 MB), 구성은 dense Schur 닫힘으로 0.12 s(`OPENBLAS_NUM_THREADS=4` 권장). 손익분기 P18 42구성, P14 12구성; 그 미만이면 `set_decaps`가 낫다. 계약(W12-a, E4): 직접 풀이 대비 CPU 기저 ≤ 1e-9(정확 경로, `decap_basis(freqs, backend=Backend("splu", fast=True))`, P18 주파수당 20 s = GPU의 1.6–2배), **GPU 기저 ≤ 1e-5이며 비결정적**(cuDSS 수치 인수분해가 실행마다 달라짐, 30 kHz–100 MHz 실측 5.7e-7~1.03e-5; 경합과 무관). 마스크 판정 같은 기능 결과는 영향 없음. `save(path, with_impedances=True)`/`load(path)`는 모델 없이 닫기만 하는 프로세스를 지원한다. 기저와 직접 풀이의 백엔드가 다르면(예: CPU 기저 + GPU 직접 풀이) 유효한 비교 계약은 두 계약 중 느슨한 쪽에 GPU 직접 풀이의 CPU 대비 오차(패키지 ≤ 1e-8)가 더해진 값이다(A1 v2 §9-4 실측 1.2–1.9e-8). 한 프로세스에서 기저와 직접 풀이(`set_decaps`+`solve`)를 같은 모델로 할 수 있다(`decap_basis`가 솔버를 비켜 두었다가 되돌림; `Model.release_solver()`는 참조만 버리고 `free()`는 부르지 않는다 — cuDSS 0.8에서 크래시하는 것은 `free()`뿐, 두 번째 솔버 생성은 안전, 추가 VRAM P18 245 MB).
- 다중 포트(W10): `Design.multiport(ports, cache_dir, …)`/`multiport.MultiRail` — 같은 레일의 포트 k개를 한 모델로 풀어 (nf, k, k) Z 행렬. **현재 세 설계에는 같은 레일을 공유하는 SPD 포트가 없다**(SITE0/SITE1은 별개 net 사본이라 이상 GND에서 Z12 ≡ 0, `open()`이 거부). 검증은 한 포트의 핀을 두 그룹으로 나눈 경우로 했다(상반성 1e-11/1e-15, 재단락 시 단일 포트 영수증 2.8e-9 재현). 한 레일 안의 임의 노드 그룹(예: decap 사이트 묶음, 핀 필드 부분) 간 전달 임피던스에 쓸 수 있다.
- 제품 통합(W11, 2026-09-19): 제품 `spd_decap_pi`의 solver 프로파일 `hybrid_plane_pair_v1`(powersi-compatible)·`hybrid_plane_pair_v1_gnd`(physical-gnd)가 이 엔진을 서브프로세스 워커로 호출한다(`_core/solver/engine_adapter.py`, `engine_worker.py`). 기본 프로파일은 layerwise 그대로이며 전환은 `profiles.py`의 `APPLICATION_DEFAULT_SOLVER_PROFILE_KEY` 한 줄이다. 원본 SPD 파일과 sha256 일치가 필요하고, 교차-net decap 재할당은 거부한다. 영수증은 `outputs/engine-receipts/`에 보존되고 validity 5줄은 결과 뷰의 assumptions/confidence note에 표시된다. CPU 전용 프리즈 빌드가 기본(GPU는 소스 설치 + `.[gpu]`). 상세: `docs/engine/W11_PLAN_2026-09-19.md`, `W11A..D_REPORT.md`.

## 8. 다음 세션 진입점
1. `docs/engine/ENGINE_PLAN_2026-09-18.md` §4의 W 항목 순서대로. 각 항목은 §5의 재현 테스트가 게이트다.
2. 연구 문서: `docs/research-claude/2026-09-15/NEXT_SESSION_HANDOFF.md`(§11), `PROGRESS_SUMMARY_2026-09-18.md`.
3. 규칙: 수치 모듈 변경은 사전 등록 + 영수증(연구 규칙과 동일). 연구 트리는 읽기 전용.

## 9. 앱용 유틸 (W12-c)
`apps/decap_search`, `apps/site_decision`가 엔진을 쓰며 직접 짜야 했던 것들(수치 무관, 순수 인체공학) — `docs/engine/APP_decap_search_REPORT.md` §6, `docs/engine/APP_site_decision_REPORT.md` §7 근거. 전부 `from spd_pi_engine import ...`로 바로 쓴다.

| 이름 | 시그니처 | 뭘 대신하나 |
|---|---|---|
| `DecapSite.xy` / `.layer` / `.capacitance_F` | 필드, `rail.decaps`/`rail.site(refdes)`가 채움 | `rail.ex["rail_nodes"][site.node]`를 직접 여는 것(`decide._xy`) |
| `DecapSite.impedance(freqs)` | 메서드 | `rail.ex["models"][site.model_id].impedance(freqs)`를 직접 여는 것(`decide.capacitance`) |
| `Rail.site(refdes)` | `Rail.site(refdes) -> DecapSite` | `next(d for d in rail.decaps if d.refdes == refdes)` |
| `find_site_pair(spd_path, port)` | `-> str \| None` | `apps.site_decision.decide.find_site_pair`(SITE 쌍 없으면 예외 대신 `None`) |
| `match_sites(rail0, rail1, rule="refdes-suffix")` | `-> {"mapping": {...}, "unmatched": [...]}` | `apps.site_decision.decide.match_sites`(A2가 채택한 규칙 하나만; `unmatched`가 리스트라 `KeyError` 대신 한 번에 확인) |
| `ladder_freqs(ref_freq=None)` | `-> np.ndarray` | 예전엔 `spd_pi_engine.cli`를 import해야 했다(`__all__`에 없었음). 지금은 `cli.py`도 여기서 가져다 쓴다 |
| `unique_path(path)` | `-> Path` | 위와 동일(덮어쓰기 금지 규칙) |
| `receipt.attach_mask(receipt, mask)` | in place, `mask`/`mask_ratio`/`mask_margin`/`mask_pass` 추가 | 앱마다 마스크·마진을 자기 형식으로 넣던 것(`apps.decap_search.search.Mask`) |
| `receipt.mask_margin(freq, Z, mask)` | 순수 함수 | `Mask.margin`과 같은 규칙, 영수증 없이도 씀 |
| `Result.receipt(light=True)` | `decap_config`/`reference_search`/`build_info`/`stats` 생략(sha·summary는 유지) | 구성 스윕 앱이 매 solve마다 421개 `decap_config`를 받던 것(기본값 `light=False`는 이전과 동일) |

`apps/decap_search/main.py`는 `from spd_pi_engine.cli import ladder_freqs, unique_path`를 쓰는데, 이제 `from spd_pi_engine import ladder_freqs, unique_path`로 한 줄만 바꾸면 된다(동작은 동일 — `cli.py`가 같은 함수를 가져다 쓰므로). `apps/site_decision/decide.py`의 `find_site_pair`/`match_sites`/`_xy`/`capacitance`는 반환 형태가 달라(이쪽은 예외를 던지고, `match_sites`는 평평한 dict를 돌려준다) 한 줄 교체가 아니다 — 바꾸려면 `evaluate()`/`match_report()`도 같이 고쳐야 하므로 이번 작업(W12-c, 엔진 파일만 수정)에서는 앱을 건드리지 않았다.

## 10. 하드웨어 프로파일과 병렬화 (W13)
엔진은 더 이상 개발 노트북(i9-12900H 14C/20T, 64 GB, RTX A2000 8 GB)을 가정하지 않는다. `hardware.py`가 이 기계가 무엇인지 **탐지**하고(`HardwareProfile.detect()`), 그 프로필과 작업 설명만 받는 **순수 함수**가 크기를 정한다(`plan_sweep`, `plan_basis`, `plan_threads`, `plan_chunk_tiles`). 순수하므로 노트북에서 워크스테이션 계획을 단위 테스트할 수 있다(`tests/engine/test_hardware.py`, 데이터 없음). 근거와 실측은 `docs/engine/W13_REPORT.md`.

```python
from spd_pi_engine import HardwareProfile, plan_sweep, plan_basis
prof = HardwareProfile.detect()          # psutil 있으면 psutil, 없으면 os.cpu_count + wmic/ctypes
                                         # (/proc/*), GPU는 cuda-bindings → nvidia-smi → 없음
plan_sweep(prof, n_ports=92, unknowns_estimate=275_218, solver="cudss").jobs
plan_basis(prof, N=275_218, n_decaps=421, n_freqs=27, solver="splu").workers
```

### 10-1. 프로필과 규칙
`HardwareProfile(cpu_physical, cpu_logical, ram_total_GB, ram_free_GB, gpus=[{index, name, vram_total_MB, vram_free_MB}])`.

```
jobs      = min(cpu_jobs, vram_jobs, ram_jobs, n_ports, max_jobs)      # 각 항은 최소 1
cpu_jobs  = (cpu_physical - 1) // threads_per_job     # 드라이버·OS 몫으로 코어 1개를 남긴다
vram_jobs = vram_free_MB // vram_per_process_MB(N)    # GPU 솔버일 때만
ram_jobs  = ram_free_MB  // rss_per_process_MB(N)
threads   = min(8, cpu_logical // jobs)               # 워커 프로세스의 BLAS 스레드
```
상수는 전부 기존 영수증에서 나온 실측이다(`hardware.py` 독스트링에 출처별로 적어 두었다).

| 상수 | 값 | 출처 |
|---|---|---|
| CUDA 컨텍스트 | 300 MB / 프로세스 | W12-a §2(Port14 컨텍스트+솔버 283 MB 중 솔버 40 MB) |
| cuDSS 인수분해 | `10.7 + 8.51e-4·N` MB | W12-a §2 두 점(N 34 424 → 40 MB, N 275 218 → 245 MB) 직선. P18 nnz_LU 8.75e6 × 16 B ≈ 245 MB와 일치, N 1.23 M에서는 약 15 % 낙관 |
| CuPy 균질화 배치 | `96 B × chunk_tiles` (6e6에서 576 MB) | `homogenise._gx`의 float64 작업 배열 12개. W12-a 수치에는 없는 항목이며 워커의 최대 VRAM 소비원 |
| 호스트 RSS | `1660 + 1.73e-3·N` MB | W4/W7 영수증 10건 최소제곱(N 22 438–1 233 161, 최대 잔차 4.7 %). P18 2 135 MB, 1.23 M 포트 3 795 MB |
| `threads_per_job` | splu 2, cudss 3 | **유일한 판단값**. splu는 W9 §5-3의 과다구독(기본 24스레드에서 421×421 LU가 39–560 ms로 요동)을 피하는 값, cudss 3은 **이 노트북이 plan §5-3/§5-5와 `runall15.py`가 손으로 박아 둔 “동시 4”를 그대로 재현하도록 보정**한 값이다. 기계마다 다르면 이 상수를 바꾼다(기계 이름으로 분기하지 않는다) |

### 10-2. 두 기계의 계획 (`hardware.LAPTOP` / `hardware.WORKSTATION` 합성 프로필)
A6000의 VRAM은 소유자 메모에 42 GB로 적혀 있지만 스펙은 48 GB다 — **엔진은 어느 쪽도 하드코딩하지 않고 실행 시 탐지한다.** 아래 표의 48 GB는 문서용 합성 프로필 값이다.

| 항목 | 노트북 14C/20T, 64 GB, A2000 8 GB | 워크스테이션 32C/64T, 512 GB, A6000 48 GB |
|---|---|---|
| `sweep --jobs auto` (cudss/auto) | **4** (cpu 4, vram 6, ram 19 중 최소) | **10** (cpu 10, vram 41, ram 234) |
| `sweep --jobs auto` (splu) | **6** | **15** |
| 워커당 BLAS 스레드 | 5 (jobs 4) / 3 (jobs 6) | 6 (jobs 10) / 4 (jobs 15) |
| `decap_basis(chunk="auto")` GPU / CPU | 24 / 120 | 120 / 256 |
| `decap_basis(workers="auto")` splu, 27주파수 | 6 | **15** |
| `plan_chunk_tiles` | 6e6(기본 그대로) | 36e6 |

- 노트북 수치는 **W13 이전과 동일**하다. `auto`가 A2000에서 4를 내는 것이 설계 제약이었고, 테스트가 그것을 고정한다.
- A6000에서는 VRAM이 더 이상 구속 조건이 아니다 — 호스트 CPU가 잡는다. 미지수 1.23 M짜리 포트에서는 노트북이 VRAM으로 3까지 내려가지만(`test_laptop_vram_binds_on_the_biggest_port`) 워크스테이션은 10을 유지한다.
- GPU가 없으면 `cudss`/`auto` 요청은 **splu로 계획하고 그 사실을 `Plan.notes`에 남긴다**(엔진 자체의 모델별 폴백은 그대로). RAM이 모자라면 jobs 1이다.

### 10-3. 워크스테이션 예상 벽시계 (외삽, 가정 명시)
| 작업 | 노트북 실측 | 워크스테이션 예상 |
|---|---|---|
| 92포트 sweep (cudss+fast) | 23분 (jobs 4, 연구 드라이버) | **9–10분** (jobs 10) |
| P18 CPU 기저 27주파수, 직렬 | 9.4분 (주파수당 21.0 s, `OPENBLAS_NUM_THREADS=4`) | 9.4분 (동일 가정) |
| P18 CPU 기저 27주파수, 주파수 병렬 | 약 2.3분 (workers 6, 실측 2.62배 가속) | **약 1분** (workers 15, 2 웨이브) |

가정: (1) 포트당·주파수당 계산 시간이 두 기계에서 같다 — A6000은 A2000보다 SM 수·대역폭이 크고 Threadripper 코어는 클럭이 낮지만 IPC가 비슷하므로 **보수적**이다. (2) sweep 묶음 효율(가장 큰 포트의 꼬리) 80 %를 그대로 쓴다 — 92포트 sweep의 하한은 가장 큰 포트 1개의 벽시계(N 1.23 M, 실측 226 s)다. (3) 기저 워커는 웨이브 단위로 도는데(`ceil(27/workers)`) 워크스테이션은 2 웨이브라 양자화 손실이 작다. (4) 워크스테이션은 15워커 × 4스레드 = 60/64 논리 CPU로 과다구독이 없다(노트북은 6 × 4 = 24/20으로 과다구독 상태에서 잰 값이다).

### 10-4. 주의 (수치)
- **`plan_chunk_tiles`는 비트 동일하지 않다.** `batched_gx`는 배치의 모든 창이 수렴할 때까지 돌므로 배치 크기가 바뀌면 CG 반복 수와 G의 마지막 비트가 바뀐다. 그래서 `window_conductance`의 기본값은 그대로 6e6이고 `chunk_tiles="auto"`로만 옵트인한다(이 노트북에서는 auto도 정확히 6e6이다).
- **워커의 스레드 수는 마지막 비트를 움직인다**(W12-a §5, 2.7e-13). `sweep`은 워커 **서브프로세스의 환경**에만 `OPENBLAS/MKL/OMP_NUM_THREADS`를 넣는다(호출자 환경은 건드리지 않는다). 예전 영수증과 비트 단위로 맞춰야 하면 `--threads 0`으로 상속시킨다.
- `decap_basis(workers=N)`은 `splu`에서만 동작하고(카드 1장, 프로세스당 cuDSS 컨텍스트 1개), 부모가 Y를 조립해 워커에 보내므로 **Y는 직렬 경로와 비트 동일**하다. 실측 Port14 workers=4, P18 workers=6 모두 기저 배열이 **비트 동일**(max rel 0.0)했다.
- `Backend.host_nthreads` 기본값은 **4 그대로**다. `None`을 주면 워커 환경(`OMP_NUM_THREADS`) 또는 `plan_threads`에서 가져온다 — cuDSS 호스트 재배열 스레드 수는 인수분해를 움직일 수 있으므로 API 정돈을 위해 기본값을 바꾸지 않았다.
