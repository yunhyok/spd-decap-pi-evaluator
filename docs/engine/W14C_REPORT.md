# W14-c — 격자 수렴 관리자 (엔진 측만, 제품 배선은 보류)

계획: `docs/engine/W14_PLAN_2026-09-20.md` §W14-c. 전제는 W14-a의 `Rail.mesh` → `MeshedRail.solve`
경계(`W14A_REPORT.md`)와 W14-b의 판정 **R3**(`W14B_REPORT.md` §4)다. R3이므로 계획서의 제품 어댑터
부분(`convergence_check` 옵션, W11-b 면제 제거)은 **검토자 판단으로 보류**했고 이번 작업은 엔진
쪽만 만들었다(§6). 원칙 그대로 — 5개 수치 모듈(`geometry, homogenise, reference, model, solver`)은
**한 줄도 건드리지 않았다.** `tools/research-claude/`, `docs/handoff/`,
`scripts/benchmark_raw_spd_powersi_correlation.py`, `src/spd_decap_pi/`도 무수정이다.

## 1. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `src/spd_pi_engine/convergence.py` | 신규 148줄 | `mesh_delta`(순수 지표), `variant_options`, `MeshConvergence`(+`product_dict`), `check_mesh_convergence` |
| `src/spd_pi_engine/cli.py` | +60/−3 | `converge` 서브커맨드, `_build_model`을 `_rail_options_backend`로 분리해 인자 배선 재사용 |
| `src/spd_pi_engine/receipt.py` | +4 | `VALIDITY_NOTES`에 격자 민감도 1줄(6번째) |
| `src/spd_pi_engine/__init__.py` | +9/−4 | `convergence` 모듈, `MeshConvergence`, `check_mesh_convergence` 재노출 |
| `src/spd_pi_engine/README.md` | +16/−2 | §1에 같은 민감도 1줄("다섯 줄"→"여섯 줄"), §2 CLI 목록에 `converge`, §11-1 신설 |
| `tests/engine/test_w14c.py` | 신규 111줄 | 데이터 없는 2개(지표·변형 옵션) + 데이터 기반 1개(Port14 coarse), `tmp_path` 안 씀 |
| `docs/engine/W14C_REPORT.md` | 신규 | 이 문서 |

## 2. API

```python
from spd_pi_engine import check_mesh_convergence
conv = check_mesh_convergence(rail, opt, freqs, pair="coarse",       # "coarse" = h×2, "fine" = h÷2
                              backend=Backend(solver="auto", fast=True),
                              rms_db_tol=0.2, max_db_tol=0.5)
conv.rms_db, conv.max_db, conv.converged, conv.f_res_shift_pct, conv.cost
conv.ref            # 기준 격자의 Result — `rail.mesh(opt).solve(freqs)`와 같은 풀이(재계산 없음)
conv.var            # 변형 격자의 Result (솔버는 풀이 직후 release_solver 로 반납)
conv.product_dict() # 제품의 convergence dict 형식
```

| 이름 | 내용 |
|---|---|
| `mesh_delta(z_var, z_ref)` | `(delta_db, rms_db, max_db)`. Δ_i = 20·log10(\|Z_var,i\|/\|Z_ref,i\|), 점 전체 RMS, max \|Δ\|. `tools/engine_studies/w14b_mesh_sensitivity.metrics`와 **같은 식**(순수 함수라 데이터 없이 테스트한다) |
| `variant_options(opt, pair)` | `dataclasses.replace`. coarse = `h*2`, `sub=(sub_c*2, sub_f, sub_t)` / fine = `h/2`, `sub=(sub_c//2, …)`. 서브타일 물리 크기 h/sub_c 고정(W14-b와 동일), 나머지 옵션(`fine_box` 포함)은 그대로 통과 |
| `MeshConvergence` | `pair, h_ref, h_var, rms_db, max_db, converged, delta_db(주파수별 배열), f_res_shift_pct, ref, var, cost, rms_db_tol, max_db_tol` |
| `MeshConvergence.product_dict()` | `converged`, `frequency_converged`, `modal_converged`, `note`, `frequency_rms/max_delta_db`, `modal_rms/max_delta_db`, `mesh_pair`, `h_ref`, `h_var`, `tolerance_db{rms,max}` |
| CLI `converge` | `--spd --port --cache [--pair coarse\|fine] [--rms-tol 0.2] [--max-tol 0.5] [--out J.json]` + `solve`의 옵션 전부(`--variant/--reference/--freqs\|--ladder/--solver/--fast/--ref-npz`). h_ref/h_var·미지수·벽시계·RMS/max·f_res 이동·converged를 찍고, `--out`이면 `product_dict()` + 양쪽 영수증(light)을 JSON으로 쓴다(`unique_path`, 덮어쓰기 없음) |

**설계 메모 3개.**

1. **기준 결과는 재계산하지 않는다.** `conv.ref`는 관리자가 첫 번째로 부른 `rail.mesh(opt).solve(freqs)`
   그 자체다. 그래서 Z(f)가 어차피 필요한 호출자에게 검사 비용은 mesh+solve **1회 추가**이고,
   테스트가 이 성질을 비트 동일(`np.array_equal`)로 고정한다(§4-(1)).
2. **`modal_converged`는 격자 검사를 그대로 복사한다.** 엔진에는 모달 차수가 없다(2-D 격자 하나 +
   직접 희소 LU, 자를 모드 기저가 없다). 계획서가 요구한 대로 값은 같게 두고 그 사실을 dict의
   `note` 키에 적는다 — 제품이 나중에 이 dict를 읽더라도 "모달 검사를 했다"고 오해하지 않는다.
3. **변형 솔버만 반납한다**(`release_solver`). 기준 쪽 모델은 `conv.ref`를 통해 호출자가 계속 들고
   있는 것이라 건드리지 않는다. cuDSS는 `free()`를 부르지 않으므로(W12-b) 실제로 카드에서 메모리가
   즉시 돌아오지는 않지만, 슬롯을 비워 두는 것이 이 클래스가 할 수 있는 전부다.

## 3. `numerics_id` — 변경 전/후 동일

`receipt.py`는 5개 수치 모듈이 아니므로 `VALIDITY_NOTES` 한 줄 추가로 id가 바뀌면 안 된다.
W14A_REPORT §3과 **같은 호출**(`numerics_id(opt.reference, opt.flags, mesh_dict(opt),
conventions_dict(opt.conventions))`, `ModelOptions(reference="powersi-compatible", flags=FLAGS_P,
h=200, fh=50, top_h=50, sub=(20,10,10), fringe=True)`)로 확인했다:

| 시점 | `numerics_id` |
|---|---|
| 변경 전 (HEAD `0d19a09`) | `27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0` |
| 변경 후 | `27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0` |

**동일.** 5개 모듈 소스 해시도 그대로다(`geometry.py e354e49ad8c76445`, `homogenise.py
9dbf066a88f991be`, `solver.py ee2917998f5b47dc`, `reference.py abecefafa5b655d1`,
`model.py d4e3eed73cd6f263`). CLI 스모크 영수증에서도 기준 격자 `27d81996e38f3180…`(h=200,
sub=(20,10,10))이고 변형만 `6292f76ed4bdecdd…`로 갈린다 — mesh 설정이 id의 입력이니 의도대로다.

## 4. validity 노트 6번째 줄

```
Mesh sensitivity (W14-b): |Z| at 3-100 MHz moves up to 1.8 dB on package rails (1.4 dB on PCB)
when h goes 200 -> 100 um, and <= 0.15 dB below 1 MHz; h=400 stays within 0.13 dB of h=200 except
on one sparse-decap rail (1.0 dB). h=200 um is the frozen default, not a converged grid.
```

근거는 W14B_REPORT §3이다 — (100 vs 200) max \|Δ\|는 패키지 1.477–1.845 dB(Port19만 0.225), PCB
1.256/1.438 dB; f < 1 MHz의 \|Δ\|는 아홉 케이스 두 쌍 전부 ≤ 0.154 dB; (400 vs 200) max는 Port19
1.014 dB를 빼면 ≤ 0.13 dB. README §1에도 같은 내용을 한국어로 한 줄 넣고 "이 다섯 줄" → "이 여섯
줄"로 고쳤다.

**제품 테스트 수정은 필요 없었다.** `VALIDITY_NOTES`와 노트 본문을 `src/spd_decap_pi`와 `tests/`에서
grep한 결과:

- `_core/services.py::evaluation_model_boundary_disclosure`는 `" ".join(VALIDITY_NOTES)`로 **동적**
  인용한다(제품 소스 무수정).
- `tests/test_spd_decap_gui_engine.py::test_engine_boundary_disclosure_quotes_the_engine_validity_notes`는
  `for note in VALIDITY_NOTES`로 돌며 포함 여부만 본다 → 6줄에서도 통과. 같은 파일 117/149/152행의
  노트 문자열은 **가짜 영수증 픽스처**(자체 1줄 리스트)라 엔진 상수와 무관하다.
- `tests/test_engine_adapter_reproduction.py`는 `receipt["validity"]["notes"]`와 비교한다(동적).
- 개수를 박아 둔 테스트는 없었다(`grep len(VALIDITY_NOTES)` 0건).

따라서 이번 작업에서 **수정한 제품 테스트는 없다.** 다만 `_core/services.py` 267행 주석의 "the same
five lines"는 이제 여섯 줄이다 — 제품 소스는 손대지 말라는 지시라 고치지 않았다(§6-3).

## 5. 테스트

환경: 시스템 Python 3.12.10, `PYTHONUTF8=1 QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg`,
`SPD_PI_DATA_DIR=D:\Downloads\examples`,
`SPD_PI_WORK_DIR=D:\Downloads\examples\analysis\claude-2026-09-15\work`,
`SPD_PI_ENGINE_CACHE=…\work\engine_cache`.

```
python -u -m pytest tests/engine/test_w14c.py tests/engine/test_w14a.py \
       tests/engine/test_datafree.py tests/engine/test_w12c.py -q -p no:cacheprovider
```
→ **24 passed, 1 skipped, 233.6 s**, exit 0. skip 1건은 `test_datafree.py:97`의 `--gpu` 미지정.
느린 순서: W14-a 170.0 s, **W14-c 60.2 s**, W12-c Port14 site 2.1 s.

```
python -u -m pytest tests/test_engine_scenario_mapping.py tests/test_spd_decap_gui_engine.py \
       tests/test_engine_adapter_reproduction.py -m "not engine_reproduction" -q -p no:cacheprovider
```
→ **17 passed, 4 deselected, 2.4 s**, exit 0. 수정한 제품 테스트가 없으므로(§4) 27-노드 기준선
(`W11E_REPORT.md` §9)과 대조할 신규 실패도 없다.

`test_w14c.py`는 **기본 프로파일**(`slow` 미표시)에 뒀다 — 단독 **62.2 s**로 기준(약 3분) 아래다.
비용은 빌드 3회(검사의 2회 + 비트 동일 비교용 기준 1회)이고 `Backend(solver="splu", fast=True)`다
(fast 경로는 Y·G 비트 동일, EXP-37/38/40). 계획 §W14-c의 게이트 중 "기본 프로파일 `layerwise_admittance_v1`
불변"은 제품을 안 건드렸으므로 자동 성립, "영수증 비트 동일"은 §4-(1)의 `np.array_equal`이다.

## 6. CLI 스모크 — W14-b Port14와 대조

```
python -m spd_pi_engine converge --spd …S4LB002-2Para_260729_1_injected.spd --port Port14_SITE0 \
  --cache …\engine_cache --pair coarse --variant p --reference powersi-compatible \
  --ladder --ref-npz …\S4LB002_260729_Zdiag.npz --solver auto --fast \
  --out …\work\w14c\port14_coarse.json
```
```
pair=coarse h_ref=200 h_var=400 points=27
unknowns ref=34424 var=22566  wall ref=6.4s var=7.7s
RMS=0.026 dB (tol 0.2) max=0.047 dB (tol 0.5)
f_res shift=+0.0%  converged=True
```

| 값 | W14-c CLI(GPU cuDSS, fast) | W14-b(`summary.json`) | 차이 |
|---|---|---|---|
| RMS dB | 0.026094318782 | 0.026094318781 | **1.5e−13** |
| max dB | 0.046547026844 | 0.046547026842 | **1.7e−12** |
| Δ(f) 27점 | — | — | max \|차이\| **1.1e−11 dB** |
| 미지수 (200 / 400) | 34 424 / 22 566 | 34 424 / 22 566 | 일치 |

요구 허용치 ~1e−6 dB를 크게 밑돈다. 남은 1e−11급 차이는 cuDSS 수치 인수분해가 실행마다 마지막
비트에서 달라지기 때문이며(README §4 GPU 계약 ≤ 1e−8 상대), 같은 사다리·같은 지표·같은 격자라는
증거로 충분하다. `f_res shift=+0.0 %`도 W14B §3 보조 지표의 Port14 400열(+0.0)과 같다. 벽시계가
W14-b(13.2 s)보다 짧은 것은 이 숫자가 mesh+solve만 재기 때문이다(추출은 `Rail` 생성 시점, 캐시 히트).

## 7. 보류/판단 필요 — 제품 배선

**하지 않은 것**(계획 §W14-c의 제품 어댑터 부분): 프로파일 옵션 `convergence_check ∈ {none,
coarse, fine}` 추가, 어댑터가 `product_dict()`를 결과의 `convergence`에 채우는 배선,
`_rejected_comparison_convergence`의 **W11-b 엔진 면제 제거**. 이유는 검토자 지시이자 W14-b의 판정
구조다:

1. **기본값이 통과하지 못한다.** R3는 "두 쌍 모두 9케이스를 채우지 못함"이다. 지금 면제를 없애고
   게이트를 균일 적용하면, 기본값 `none`이 아닌 한 엔진 결과가 **제품 게이트에서 fail-closed로
   거부**된다. `none`이 기본이면 게이트는 사실상 꺼져 있는 것이므로, 배선의 값어치는 소유자가
   기본 격자를 어떻게 할지 정한 뒤에야 생긴다.
2. **지표 자체가 검토 대상이다**(W14B §8-1). RMS 0.2 / max 0.5 dB는 `evaluator.py`가 **진폭** 차이에
   쓰는 허용치인데, W14-b에서 실패를 만든 점은 거의 전부 깊은 \|Z\| 골(반공진) 근방이다 — f_res가
   3 % 움직이면 dB 비가 1 dB 넘게 튄다. 공진 주파수 이동(G4식 ±10 %)을 함께 보는 판정이 물리적으로
   더 맞을 수 있고, 그 결정 전에 제품 게이트에 이 숫자를 연결하면 잘못된 기준이 고정된다.
3. **비용이 두 배에 가깝다**(W14B §6). 검사 = 기준 풀이 + 변형 풀이이므로 패키지 **+113~129 %**,
   PCB +60 %다. 계획서 R1의 "약 +25 %"는 패키지에서 성립하지 않는다(h=400은 미지수가 29 % 적어도
   셀당 서브타일이 40×40으로 늘어 build가 더 비싸다). 제품 기본 경로에 넣을지는 이 비용을 본
   소유자 결정이다.

**소유자에게 필요한 결정 두 개**(W14B §8, §10-5와 같은 묶음):

- (a) **기본 격자**. W14-b-2 판정은 (i) — h=100이 f ≥ 1 MHz에서 참조에 더 가깝다(패키지 6/7, PCB
  2/2). 반대로 같은 자료의 G1–G4 통과 수는 h=100에서 세 케이스 후퇴한다(전부 G2 ΔL). 비용은 미지수
  2.08배·벽시계 1.39배.
- (b) **수렴 지표**. 진폭 dB(현행 제품 허용치)만 볼지, f_res 이동을 함께 볼지.

(a)·(b)가 정해지면 제품 배선은 `product_dict()`를 어댑터 결과에 넣고 면제 한 줄을 지우는 작업이다
— 엔진 쪽은 이번 작업으로 준비됐고, `src/spd_decap_pi`를 import하지 않으므로 지금 상태에서 제품
동작은 **완전히 불변**이다(§5의 제품 테스트 17 passed가 그 증거).

## 8. 계획에서 벗어난 점

1. 계획 §W14-c의 제품 어댑터·면제 제거를 하지 않았다 — 검토자 지시(R3)이며 §7에 근거를 적었다.
   계획의 엔진 쪽 서술(`MeshConvergence{h_pair, rms_db, max_db, converged, cost}`)에서 필드 이름은
   `h_pair` 대신 `pair`/`h_ref`/`h_var`로 나눴다(작업 지시서의 필드 목록 그대로).
2. `f_res_shift_pct`는 **정보용**이며 판정에 쓰지 않는다. 사다리 점 중 \|Z\|가 최소인 점의 주파수를
   비교한 값이라(포물선 보정 없는 raw argmin, W14B §3 보조 지표와 같은 규칙) 골이 둘인 레일에서는
   "어느 골이 더 깊은가"가 바뀌며 크게 튈 수 있다(W14B §3의 Port14 −80.5 % 각주).
3. CLI `converge`는 `solve`의 `--breakdown-100k`도 같이 받는다(`_add_solve_options` 재사용). 이 명령은
   영수증을 `light=True, breakdown_100k=False`로 쓰므로 그 플래그는 무시된다 — 인자 배선을 나누는
   것보다 짧아서 그대로 뒀다.
4. `_core/services.py`의 "the same five lines" 주석은 고치지 않았다(제품 소스 무수정 지시).
