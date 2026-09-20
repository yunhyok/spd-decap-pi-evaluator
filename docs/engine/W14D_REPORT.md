# W14-d — G4 게이트의 f_res 항 정정 (D10)

2026-09-20. 소유자 결정 **D10**(`docs/research-claude/2026-09-15/DECISIONS.md`)의 구현과 검증이다.
결함은 W14-b §10-2에서 드러났다: `receipt.ladder_gates(freq, Z, zr, fres_m, fres_r)`가 케이스의 참조
공진 `fres_r`를 **인자로 받고도 쓰지 않고**, G4의 f_res 항을 `|f_res_model − 1.585 MHz| / 1.585 MHz`
로 계산했다. 1.585 MHz는 `exp3/run3.py`에 박힌 260729 Port18 한 레일의 참조값이라(다른 레일은
1.5–7.2 MHz) 나머지 레일에서는 의미 없는 수였고, G4가 **틀린 이유로** 27영수증 전부 실패했다.

## 1. 변경 (`src/spd_pi_engine/receipt.py` 한 함수)

| 항목 | 전 | 후 |
|---|---|---|
| G4 PASS 규칙 | `G4_max_rel_err < 0.20` **and** `G4_f_res_rel_err_vs_1.585MHz < 0.10` | `G4_max_rel_err < 0.20` **and** `G4_f_res_rel_err_vs_ref < 0.10` |
| 새 키 | — | `G4_f_res_ref_Hz` = `fres_r`, `G4_f_res_rel_err_vs_ref` = `|fres_m − fres_r| / fres_r` |
| 옛 키 | `G4_f_res_rel_err_vs_1.585MHz` | **그대로 기록**(연구 영수증과 비교용), 판정에는 쓰지 않음 |
| `fres_r` 없음/비유한/0 | (인자 무시) | `G4_f_res_rel_err_vs_ref = NaN`, G4 = False |
| 독스트링 | "`exp3/run3.ladder_gates` verbatim" | "`exp3/run3.ladder_gates` with the D10 correction" |

진폭항(1–10 MHz 오차 < 20 %)과 G1·G2·G3·G5는 손대지 않았다. `attach_reference`는 이미
`f_res_ref`를 계산해 `fres_r`로 넘기고 있었으므로 호출부 변경은 없다.

자기검사(`python -m spd_pi_engine.receipt` → `DEMO PASS`): 기존 주장(1.585 MHz를 양쪽에 주면
G1–G4 전부 True)은 그대로 두고, `fres_m=3.0 MHz / fres_r=3.1 MHz`에서 `_vs_ref ≈ 0.0323`,
`_vs_1.585MHz ≈ 0.8927`이 되는지 한 줄 추가했다.

## 2. `numerics_id` — 변경 전/후 동일

`receipt.py`는 5개 수치 모듈(`geometry, homogenise, solver, reference, model`)이 아니므로 id의
입력이 아니다. W14-a와 같은 호출
(`numerics_id(opt.reference, opt.flags, mesh_dict(opt), conventions_dict(DEFAULT_CONVENTIONS))`,
`ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200, fh=50, top_h=50, sub=(20,10,10), fringe=True)`):

| 시점 | `numerics_id` |
|---|---|
| 변경 전 | `27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0` |
| 변경 후 | `27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0` |

**동일.** 소스 해시 5개도 그대로다(`geometry.py e354e49ad8c76445`, `homogenise.py 9dbf066a88f991be`,
`solver.py ee2917998f5b47dc`, `reference.py abecefafa5b655d1`, `model.py d4e3eed73cd6f263`).
로그: `WORK_DIR/w14d/numerics_id_{before,after}.log`.

## 3. 테스트

```
PYTHONUTF8=1 MPLBACKEND=Agg python -u -m pytest tests/engine/test_datafree.py \
    tests/engine/test_w14c.py tests/engine/test_w12c.py -q -p no:cacheprovider
```
→ **24 passed, 1 skipped**(skip은 `test_solver_demo_cudss`, `--gpu` 필요) in 61.8 s.
로그 `WORK_DIR/w14d/pytest_w14d.log`. 새 테스트는 `tests/engine/test_datafree.py::
test_g4_f_res_term_uses_the_case_reference` 하나다(데이터 없음, 합성 `freq`/`Z`/`zr`):
`fres_r = fres_m·1.03`이면 f_res 항 0.0291로 통과하고 옛 키는 1.52로 멀며, `fres_r = 1.585 MHz`면
두 키가 같고, `fres_r = None`이면 NaN·G4 False.

영수증 고정구(`tests/engine/fixtures/`)는 손대지 않았다. 다만 **게이트를 비교하는 테스트가 하나
있다** — `tests/engine/test_reproduction.py::test_attach_reference_port14`가 새로 계산한 영수증의
`ladder_gates["PASS"]`를 exp28 고정구의 `PASS`와 비교한다. 260729 Port14는 정정 전후 모두 G4가
False라(진폭항 0.264 > 0.20) 이 테스트는 그대로 통과한다: **1 passed, 20 deselected** in 82.9 s
(`WORK_DIR/w14d/pytest_reproduction_attach_reference.log`).

## 4. W14-b-2 게이트 재집계 (§10-6)

같은 영수증 27개를 다시 읽기만 했다(**새 풀이 없음**):
`python w14b2_reference_compare.py --out w14b2_summary_d10.json --no-figures` →
`WORK_DIR/w14b/w14b2_summary_d10.json`(원본 `w14b2_summary.json`·그림은 보존; 도구에 `--out`과
`--no-figures` 두 옵션만 추가했다). 표 전체는 `docs/engine/W14B_REPORT.md` §10-6.

| | h=400 | h=200 | h=100 |
|---|---|---|---|
| G4 PASS (9케이스) | 0 | 0 | **2** (260729 Port1, Port7) |
| G1–G4 통과 수 합 | 12 | 13 | 12 |

- **G4 전체 2/27**(정정 전 0/27). 통과 수가 바뀐 칸은 Port1 h=100(1→2)과 Port7 h=100(3→4) 둘뿐이고,
  나머지 25영수증은 G4가 f_res 항이 아니라 **진폭항**에서 떨어지므로 §10-2와 같다.
- **h=100은 h=200 대비 G4를 얻는다**(잃지 않는다): Port1 f_res 오차 14.9 → 8.5 %, Port7 13.3 → 2.2 %.
  단 Port1의 진폭항은 0.19986으로 문턱 0.20 바로 아래다. 전체 통과 수로 보면 h=200 → h=100은
  2를 얻고(둘 다 G4) 3을 잃어(전부 G2 ΔL) §10-5 1번의 방향은 뒤집히지 않는다.
- §10-3 판정(규칙 (i)), e_h·RMS, 저주파 불변 확인은 이 정정과 무관하다(게이트만 바뀐다).

## 5. `verify` 스모크 — 260729 Port14_SITE0

```
python -m spd_pi_engine solve --spd <260729.spd> --port Port14_SITE0 --cache <ENGINE_CACHE> \
    --out WORK_DIR/w14d/260729_Port14_SITE0_d10.json --variant p --ladder --solver auto --fast \
    --ref-npz <S4LB002_260729_Zdiag.npz>
  -> unknowns=34424 wall=8.6s err_1MHz=0.0817 (8.17%)
python -m spd_pi_engine verify --receipt WORK_DIR/w14d/260729_Port14_SITE0_d10.json \
    --against WORK_DIR/w14b/260729_Port14_SITE0_h200.json --tol 1e-6
  -> 2.165386011921719e-11        (exit 0)
```

W14-b가 같은 옵션·같은 백엔드로 남긴 h=200 영수증과 max |ΔZ|/|Z| = **2.2e-11**이다(같은 GPU 경로
재실행이므로 cuDSS 비결정성 수준). 즉 정정은 Z를 움직이지 않는다. 새 영수증의 `ladder_gates`:

```
G4_max_rel_err               = 0.2641040730364942
G4_f_res_model_Hz            = 19658066.05137535
G4_f_res_ref_Hz              = 3479134.4587431834      <- 새 키
G4_f_res_rel_err_vs_ref      = 4.650274884310367       <- 새 키 (판정에 쓰임)
G4_f_res_rel_err_vs_1.585MHz = 11.402565332098014      <- 옛 키 (기록만)
PASS = {'G1': False, 'G2': False, 'G3': True, 'G4': False}
numerics_id = 27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0
```
Port14 h=200은 W14-b §3 각주의 그 케이스다 — 전역 min|Z|가 19.7 MHz의 다른 골에 잡혀 있어 f_res
오차가 465 %다. 정정은 이 병리를 고치지 않는다(고칠 대상도 아니다). 로그:
`WORK_DIR/w14d/{solve,verify}_port14_d10.log`.

## 6. 하지 않은 것 / 확인하지 못한 것

- **연구 결과는 재판정하지 않았다.** `tools/research-claude/`(읽기 전용)와 `results/expN`·
  `EXPn_REPORT.md`의 G4 수치는 상수 규칙으로 계산된 역사 기록 그대로다. D8의 "f_res +5~14 % 편향"은
  EXP-33/34가 참조 f_res와 직접 비교해 얻은 것이라 이 게이트와 무관하다.
- 고정구 영수증(`tests/engine/fixtures/`)은 옛 키만 가진 채로 둔다. 이 커밋 **이전** 영수증도 마찬가지다
  (`src/spd_pi_engine/README.md` §7에 명시).
- `validity` 문구의 "G4 f_res bias +5–14 %"는 EXP-33/34 근거라 손대지 않았다.
- 제품(`spd_decap_pi`) 쪽 게이트 표시는 이 작업 범위가 아니다. 제품은 `ladder_gates`를 직접 읽지
  않는다(엔진 영수증만 저장한다).
- 27영수증 전체 재집계는 **기존 영수증을 다시 읽어서** 했다. 다시 풀지 않았으므로 h=100/400 영수증의
  Z 자체는 W14-b 시점 그대로다.
