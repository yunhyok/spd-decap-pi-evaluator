# spd_pi_engine — SPD → Z(f) PI 계산 엔진 (v0.1)

`tools/research-claude/`의 동결 연구 모델(기준선 exp28/p, D7)을 애플리케이션이 호출할 수 있는 패키지로 옮긴 것이다. 수치는 연구 코드와 **동일**하며(영수증 재현으로 증명, 아래 §5), 연구 코드는 손대지 않았다. 설계 근거와 결합 목록은 `docs/engine/ENGINE_PLAN_2026-09-18.md`, 단계별 결과는 `docs/engine/W1..W10_REPORT.md`.

## 1. 무엇을 계산하나
PowerSI SPD 파일 하나와 포트 이름 하나를 받아, 그 레일의 PDN 임피던스 Z(f)(1 kHz–100 MHz)를 2-D plane-pair + 회로 하이브리드 모델로 계산한다. 참조면은 PowerSI 관례(cavity-wall, "powersi-compatible")가 기본이고, 물리적 GND 전용 탐색("physical-gnd")도 옵션이다.

정확도 범위(2026-09-18, `docs/research-claude/2026-09-15/PROGRESS_SUMMARY_2026-09-18.md` §4):
- PCB(s5m6585, 160포트): 1 MHz 오차 중앙값 0.58 %, G3(<10 %) 통과 80 %.
- 패키지 대형 평면 레일: ≤ 3 %.
- 패키지 전 포트: 1 MHz 오차 중앙값 28–32 %, Re Z_ref/Re Z_model @100 kHz 중앙값 1.46 — **급전 경로 R 관례 차이 미해결**.
- f_res 편향 +5–14 %(설계 무관, 원인 미확정).
영수증의 `validity` 필드가 이 네 줄을 항상 담는다. 앱은 이를 표시해야 한다.

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

## 6. 캐시
`cache_dir` 아래 `extract_{port}_{key}.pkl`, `shapes_{layer}_{key}.pkl`. 키 = sha256(SPD sha256, 포트/층, max_layers, cache_format=2, parser_version). 제품 객체는 피클하지 않는다(스택업은 dict, decap 모델은 `.SUBCKT` 원문 → 로드 시 재파싱). 손상·버전 불일치는 조용히 재생성. 여러 프로세스가 같은 디렉터리를 써도 안전(원자적 쓰기, 8×8 스트레스 PASS). P18 추출 캐시 33 MB, 층 shapes 각 약 11 MB.

## 7. 제약과 알려진 문제
- 의존: numpy ≥ 2, scipy, matplotlib 3.10.x(**채움 규칙이 수치의 일부**), Pillow, 제품 파서 `spd_decap_pi`. GPU는 `pip install .[gpu]`(nvmath-python, nvidia-cudss-cu12, cuda-bindings 12.*, cupy-cuda12x).
- cuDSS 0.8.0.10: `DirectSolver.free()`와 `SYMMETRIC`이 크래시 → 해제 생략, GENERAL 사용. 오래 사는 프로세스에서 모델을 많이 만들면 핸들이 누적되므로 GPU 계산은 작업자 프로세스에서 돌린다(`sweep`이 그 구조). 8 GB 카드에서 동시 4 프로세스.
- 미지수 130만 포트: 호스트 RSS 약 4 GB, GPU 벽시계 155 s. GUI 스레드에서 직접 부르지 말 것(`Rail.estimate_cost()`로 먼저 판단).
- decap 구성 변경: `mdl.set_decaps({refdes: model_id | None})` → 재빌드 없이 `solve()`(P18 GPU 구성당 약 2 s, 재빌드 약 18 s). 프루닝은 전(全)실장 기준으로 1회 고정(`prune_basis="all_mounted"`; 두 설계에서 재빌드와 N 동일, ΔZ ≤ 1.2e-10). `add_decap_model(id, subckt_text)`로 새 모델 등록. **주의**: decap을 전부(또는 거의 전부) 떼는 극단 구성은 저주파 조건수가 나빠져 GPU 오차가 1e-6급이 되므로 그 경우 저주파는 CPU 경로로 본다(`docs/engine/W8_REPORT.md` §4-2).
- decap 스윕(W9): `basis = mdl.decap_basis(freqs)` → `basis.Z(config)` / `basis.Z_many(configs)`. 맨 보드(decap 전부 제거)를 포트+decap 단자 다중 RHS로 한 번 풀어 두고(P18: 422 RHS × 7점 98 s, 20 MB), 구성은 dense Schur 닫힘으로 0.12 s(`OPENBLAS_NUM_THREADS=4` 권장). 손익분기 P18 42구성, P14 12구성; 그 미만이면 `set_decaps`가 낫다. 계약: 직접 풀이 대비 CPU ≤ 1e-9, GPU ≤ 1e-6(맨 보드 조건수 때문). `save/load`로 npz 보관. GPU에서는 한 모델이 기저용 또는 스윕용 중 하나다(cuDSS 플랜의 RHS 폭 고정).
- 다중 포트(W10): `Design.multiport(ports, cache_dir, …)`/`multiport.MultiRail` — 같은 레일의 포트 k개를 한 모델로 풀어 (nf, k, k) Z 행렬. **현재 세 설계에는 같은 레일을 공유하는 SPD 포트가 없다**(SITE0/SITE1은 별개 net 사본이라 이상 GND에서 Z12 ≡ 0, `open()`이 거부). 검증은 한 포트의 핀을 두 그룹으로 나눈 경우로 했다(상반성 1e-11/1e-15, 재단락 시 단일 포트 영수증 2.8e-9 재현). 한 레일 안의 임의 노드 그룹(예: decap 사이트 묶음, 핀 필드 부분) 간 전달 임피던스에 쓸 수 있다.
- 아직 없는 것(계획 W11): 제품 통합.

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
