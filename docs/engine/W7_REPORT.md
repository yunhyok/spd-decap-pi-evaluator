# W7 — CLI (`python -m spd_pi_engine`, `src/spd_pi_engine/{cli,__main__}.py`) 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §4 W7("CLI(solve/ports/verify)", 0.5인일),
원칙 §0(수치 불변, `tools/research-claude/` 무수정). 2026-09-18.

## 1. 산출물

| 파일 | 줄 | 내용 |
|---|---|---|
| `src/spd_pi_engine/cli.py` | 273 | `ports/info/solve/verify/sweep` 5개 서브커맨드. stdlib(`argparse`)만 사용, 수치 없음 |
| `src/spd_pi_engine/__main__.py` | 3 | `python -m spd_pi_engine` 진입점 |

`__init__.py`는 건드리지 않았다 — `python -m spd_pi_engine`은 `__main__.py`가 `cli.main`을
직접 부르므로 패키지 재수출이 필요 없다(W4 보고서 §5-7과 같은 이유로 CLI는 W4/`__init__` 범위 밖).
`tests/engine/`은 다른 에이전트가 동시에 작성 중이라 손대지 않았다.

## 2. 사용법

```
python -m spd_pi_engine ports  --spd PATH
python -m spd_pi_engine info   --spd PATH --port NAME --cache DIR
python -m spd_pi_engine solve  --spd PATH --port NAME --cache DIR --out receipt.json
                                [--variant legacy|p|q|pmk] [--reference powersi-compatible|physical-gnd]
                                [--freqs 1e3,1e4,... | --ladder] [--solver splu|cudss|auto]
                                [--fast|--no-fast] [--ref-npz PATH] [--breakdown-100k]
python -m spd_pi_engine verify --receipt A.json --against B.json [--tol 1e-8]
python -m spd_pi_engine sweep  --spd PATH --ports all|a,b,c --cache DIR --outdir DIR --jobs N
                                [같은 solve 옵션]
```

- `solve`는 W4 API 그대로다: `Design.open(spd).rail(port, cache).build(ModelOptions(reference=...,
  flags=VARIANTS[variant], fringe=True), Backend(solver=..., fast=...)).solve(freqs)` →
  `res.receipt()` → (`--ref-npz`가 있으면) `attach_reference()`. `fringe=True`는 옵션으로 노출하지
  않고 항상 켠다 — `docs/engine/W4_REPORT.md`의 게이트 스크립트(`w4_gate.py`)가 exp28/p baseline을
  재현할 때 쓰는 값이라 CLI도 그 값을 그대로 고정했다(다른 값은 `numerics_id`가 바뀌는 실험이라
  API를 직접 쓰는 쪽이 맞다).
- **덮어쓰지 않는다(CLAUDE.md 규칙 2).** `--out`이 이미 있으면 `_HHMMSS`를 파일명에 붙이고 실제
  경로를 표준출력에 찍는다.
- `--freqs`와 `--ladder`는 상호 배타적이고, 둘 다 생략하면 `--ladder`와 같다(`exp5/pipeline.LADDER`
  7점 + `logspace(3e5, 3e7, 21)`). `--ref-npz`가 있으면 그 npz의 `freq` 배열에 각 점을 최근접
  스냅한다(`tests/engine/ladder.py`·`w4_gate.ladder_freqs`와 동일 로직); 없으면 스냅 없이 원값을
  정렬해서 쓴다.
- `--ref-npz`는 `freq`(주파수), `port_names`(`"Port::net"` 형식), `Zdiag`(포트별 복소 임피던스
  열) 세 키를 읽는다(실측: `S4LB002_260729_Zdiag.npz` 확인).
- `sweep`은 `tools/research-claude/exp15/runall15.py`의 서브프로세스-포트당-1개 패턴을
  `python -m spd_pi_engine solve`로 재호출하는 형태로 옮긴 것(연구 코드는 import하지 않는다):
  포트별 `{outdir}/receipt_{port}.json`이 이미 있으면 건너뛰고(재개 안전), 실패는
  `{outdir}/failures.json`에 쌓는다(로그 30줄 tail 포함). `--solver cudss|auto`면 계획 §5-5대로
  `--jobs`를 4로 클램프한다.
- `verify`는 엔진 자신의 smoke test다: 두 영수증의 `freq` 배열이 완전히 같아야 하고(다르면
  `FAIL: frequency grids differ`, 종료코드 1), 같으면 `max|ΔZ|/|Z|`를 한 줄 출력하고 `--tol`
  이하면 종료코드 0, 아니면 1. 연구 영수증(run11 계열, `exp28/p` 등)도 `freq`/`Z_re`/`Z_im`
  키를 그대로 가지고 있어서 영수증 v1과 같은 키로 바로 비교된다.

## 3. 검증

환경: `$env:SPD_PI_DATA_DIR="D:\Downloads\examples"`, `$env:SPD_PI_WORK_DIR="D:\Downloads\examples\analysis\claude-2026-09-15\work"`,
`$env:PYTHONUTF8="1"`, 캐시 `WORK_DIR\engine_cache`(W3/W4가 채운 것 재사용, 새로 지우지 않음).

### 3-1. `ports` — 260729

```
python -m spd_pi_engine ports --spd "...\S4LB002-2Para_260729_1_injected.spd"
```
92줄 출력(`wc -l` = 92, W3 게이트 1의 92포트와 일치).

### 3-2. `info` — 260729 Port18_SITE0

```
rail_net=ADC_VDD_075_VTRIP_SRAM/0
decaps=421
n_nodes=52001 n_vias=30520 n_traces=28647
{
  "rail_net": "ADC_VDD_075_VTRIP_SRAM/0",
  "n_rail_nodes": 52001,
  "n_rail_vias": 30520,
  "n_rail_traces": 28647,
  "n_decaps": 421,
  "rail_layers": ["Signal$L14(MAIN_POWER4)", "Signal$L20(DGND)", "Signal$L21(DGND)",
                  "Signal$L25(MAIN_POWER4)", "Signal$TOP"],
  "prepare_seconds": 1.4
}
```
캐시 히트(W4 게이트가 채운 `engine_cache`)라 1.4 s. decap 421개는 W4 보고서 표의 Port18과 같다.

### 3-3. `solve` — 260729 Port18_SITE0, `p`+ladder+cudss+fast+ref-npz

```
python -m spd_pi_engine solve --spd "...\S4LB002-2Para_260729_1_injected.spd" --port Port18_SITE0 \
  --cache "...\engine_cache" --out "...\engine_w7\receipt_260729_Port18.json" \
  --variant p --ladder --solver cudss --fast \
  --ref-npz "D:\Downloads\examples\analysis\S4LB002_260729_Zdiag.npz"
```
출력:
```
unknowns=275218 wall=30.1s err_1MHz=0.0081 (0.81%)
wrote ...\engine_w7\receipt_260729_Port18.json
```
기대값(0.81 %)과 일치. `unknowns=275218`은 W4 보고서 표의 Port18과 같다.

### 3-4. `verify` — 위 영수증 vs `exp28/result_260729_Port18_SITE0_any_p.json`

```
python -m spd_pi_engine verify --receipt ...\engine_w7\receipt_260729_Port18.json \
  --against ...\exp28\result_260729_Port18_SITE0_any_p.json
```
출력 `5.463916979541979e-11`, 종료코드 0 (한도 1e-8 이하). freq 27점 배열이 완전히 같다.
(참고: 실패 경로도 확인 — 다른 설계 영수증끼리 비교하면 `0.912...`, 종료코드 1.)

### 3-5. `sweep` — 260804, 6포트, `--jobs 4 --solver cudss`

포트: `Port1_SITE0, Port2_SITE0, Port3_SITE0, Port4_SITE0, Port5_SITE0_1721, Port6_SITE0_19216`
(`--ref-npz S4LB002_260804_Zdiag.npz` 동봉).

```
[1/6] OK   Port4_SITE0  wall 28s
[2/6] OK   Port2_SITE0  wall 29s
[3/6] OK   Port1_SITE0  wall 30s
[4/6] OK   Port5_SITE0_1721  wall 19s
[5/6] OK   Port6_SITE0_19216  wall 50s
[6/6] OK   Port3_SITE0  wall 226s
done: 6 ok, 0 failed
```
전체 wall 3 m 47 s(4개 동시 실행, `--jobs 4`; Port3_SITE0은 decap 2,739개·미지수 1,233,161로
나머지보다 훨씬 크다). 6개 영수증 전부 기록:

| 포트 | unknowns | wall_seconds | err_1MHz |
|---|---|---|---|
| Port1_SITE0 | 118038 | 25.4 | 0.0775 |
| Port2_SITE0 | 100436 | 24.3 | 0.1585 |
| Port3_SITE0 | 1233161 | 209.9 | 0.1723 |
| Port4_SITE0 | 63078 | 23.0 | 0.1574 |
| Port5_SITE0_1721 | 22438 | 15.0 | 0.1696 |
| Port6_SITE0_19216 | 31285 | 45.6 | 0.1953 |

(이 6포트는 소형 레일 위주 표본이라 `err_1MHz`가 영수증 `validity` 3번 문구의 "패키지 전체 중앙값
31.7 %" 범위 안에 있다 — engine 자체 버그가 아니라 계약된 한계다.)

재실행(동일 명령):
```
skipping 6 ports with existing receipts
nothing to do
```
1.9 s, 종료코드 0 — 재개 안전(6개 receipt 파일 모두 그대로) 확인.

### 3-6. `--help`

5개 서브커맨드 전부 `--help`가 사용법과 옵션 설명을 출력한다(최상위 31줄, `ports` 5줄, `info` 7줄,
`solve` 26줄, `verify` 8줄, `sweep` 28줄). 발췌(`solve`):
```
usage: python -m spd_pi_engine solve [-h] --spd SPD --port PORT --cache CACHE
                                     --out OUT [--variant {legacy,p,pmk,q}]
                                     [--reference {powersi-compatible,physical-gnd}]
                                     [--freqs FREQS | --ladder]
                                     [--solver {splu,cudss,auto}]
                                     [--fast | --no-fast] [--ref-npz REF_NPZ]
                                     [--breakdown-100k]
```

## 4. 계획에서 어긋난 점

1. **`__init__.py`에 `cli`를 추가하지 않았다.** `python -m spd_pi_engine`은 `__main__.py`가
   `from .cli import main`으로 직접 부르므로 재수출이 필요 없다(W4 보고서 §5-7 결정과 같은 이유).
2. **`fringe=True`를 옵션으로 노출하지 않고 고정했다.** 계획·과제 설명 어디에도 `--fringe`가
   없고, `w4_gate.py`가 exp28/p를 재현할 때 쓰는 값이 `fringe=True`라서(§2) CLI의 유일한 목적인
   "표준 실행 재현"에 맞춰 상수로 박았다. 다른 mesh 옵션(`h/fh/top_h/sub`)도 `ModelOptions` 기본값과
   같아서 노출하지 않았다 — 필요해지면 옵션을 추가한다.
3. **`--freqs`/`--ladder` 둘 다 생략하면 `--ladder`로 취급한다.** 과제 설명의 `[--freqs ... |
   --ladder]`를 "정확히 하나 필수"로 읽을 수도 있었지만, 검증 예시(`solve`)가 항상 `--ladder`를
   명시하므로 상호배타 + 편한 기본값으로도 그 계약을 깨지 않는다.
4. **sweep 영수증 파일명은 `receipt_{port}.json`**(고정, `--out` 없음) — `runall15.py`의
   `result_{tag}_{port}_any_{variant}.json` 규칙 대신 더 단순한 이름을 썼다. 포트 이름이 이미
   설계 안에서 유일하므로 태그·variant를 파일명에 넣지 않아도 충돌하지 않는다(디렉터리 자체가
   한 설계·한 옵션 집합의 결과물).

## 5. 산출물

- `src/spd_pi_engine/cli.py`(273줄), `src/spd_pi_engine/__main__.py`(3줄).
- 영수증: `WORK_DIR\engine_w7\receipt_260729_Port18.json`,
  `WORK_DIR\engine_w7\sweep\receipt_{Port1_SITE0,Port2_SITE0,Port3_SITE0,Port4_SITE0,
  Port5_SITE0_1721,Port6_SITE0_19216}.json`, 로그 `WORK_DIR\engine_w7\sweep\logs\*.log`.
- 이 보고서와 사본: `docs/engine/W7_REPORT.md`.
