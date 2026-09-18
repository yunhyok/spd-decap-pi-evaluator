# W3 — 추출·캐시 (`src/spd_pi_engine/{spd_source,cache}.py`) 결과

계획: `docs/engine/ENGINE_PLAN_2026-09-18.md` §4 W3 (결합 C13·C14·C15·C16·C19), 원칙 §0(수치 불변,
동일 연산 순서, `tools/research-claude/` 무수정). 2026-09-18.

## 1. 이식한 것

| 새 파일 | 줄 | 원본 | 내용 |
|---|---|---|---|
| `src/spd_pi_engine/spd_source.py` | 428 | `exp1/extract.py`(전부), `exp1b.load_layer_shapes`, `exp5/pipeline.prepare` | C16 어댑터 + `sha256_of` / `list_ports` / `extract` / `load_layer_shapes` / `prepare` |
| `src/spd_pi_engine/cache.py` | 201 | `exp5/pipeline.py:65-108`(원자적 쓰기·pkl 규약) | 캐시 v2(`CacheDir`), 헤더, `demo()` 셀프체크 |
| `src/spd_pi_engine/__init__.py` | 29 | — | W3 함수 재수출(`Design`은 W4) |

패키지 합계 2,685줄(W1 4모듈 814 + W2 2모듈 1,230 + W3 2모듈 629 + `__init__` 29 무변경분 포함).
W1/W2 모듈은 손대지 않았다.

## 2. 공개 API

```python
from spd_pi_engine import list_ports, sha256_of, extract, load_layer_shapes, prepare, CacheDir

sha256_of(spd_path) -> str                       # 캐시 키
list_ports(spd_path) -> list[str]                # SPD .Port 블록 순서대로 짧은 이름 (C19)
extract(spd_path, port, gnd_net="DGND", reporter=None, log=None) -> dict
load_layer_shapes(spd_path, layers, reporter=None, log=None) -> dict
prepare(spd_path, port, cache_dir, max_layers=3, log=None,
        conventions=DEFAULT_CONVENTIONS, reporter=None) -> (ex, shapes, fine_box)
```

- `prepare`의 반환 구조는 `Model.build(ex, shapes, opt, backend)`가 그대로 먹는 구조다
  (`exp5/pipeline.prepare`와 동일: 추출 → `ReferenceSearch.candidates`로 필요한 이웃 도체층 결정 →
  없는 층만 `load_layer_shapes` → `fine_box` = 포트 + 노드 bbox ± 1000 µm).
- `ex` 키 25개 = 연구 `extract` 24개 + `model_texts`(캐시 v2용 `.SUBCKT` 원문).
- 설계 id 테이블(C13)은 없다. SPD 절대경로가 입력이고, 참조 npz는 게이트 스크립트에서만 읽는다.
- 엔진은 `os.environ`을 읽지 않고, import 시 파일시스템에 쓰지 않으며(C12), `print`가 없다
  (`log=` 콜백, 기본 no-op).

### C16 어댑터 (한 곳)

`spd_source.py` 상단 한 블록에만 제품 사설 API가 있다.

```python
SPD_PARSER_SYMBOLS = ("_parse_materials", "_parse_layers", "_parse_shapes", "_parse_padstacks",
                      "_parse_partial_circuits", "_parse_metadata", "_find_line", "_line_end",
                      "_length_um", "_Reporter")
check_parser_api()   # 모듈 import 시 1회 — 빠진 심볼 이름과 현재 `_parse*` 목록을 담아 RuntimeError
```

공개 API 두 개(`parse_passive_subcircuit`, `SpiceModelError`)와 `_core.domain.StackupLayer`,
`_core.via_model`(모델 쪽)은 사설이 아니므로 어댑터 밖이다.

## 3. 캐시 v2 구조 (C14·C15)

```
<cache_dir>/extract_{port}_{key16}.pkl     key = sha256(spd_sha256 | port | max_layers | 2 | parser_version)[:16]
<cache_dir>/shapes_{layer}_{key16}.pkl     key = sha256(spd_sha256 | layer | 2 | parser_version)[:16]
파일 내용 = pickle((header, payload))
header  = {cache_format: 2, engine_version, numpy_version, parser_version, spd_sha256}
parser_version = (제품 core __version__, sha256/12 of _core/io/spd.py, sha256/12 of _core/models/spice.py)
```

- **제품 객체를 피클하지 않는다(C15).** `stackup_layers_obj`(pydantic `StackupLayer`) →
  `model_dump()` dict 리스트, decap `models` → `.SUBCKT` 원문(`model_texts`). 로드 시
  `StackupLayer(**d)`와 제품의 `parse_passive_subcircuit(text)`로 재구성한다.
  - `StackupLayer`로 되돌리는 이유: `via_model.classify_via_conductor`가 `getattr(layer,"name")`,
    `.is_conductor`, `.thickness_um`로 읽는다(평범한 dict면 조용히 fallback 분기로 빠진다).
    필드 단위 동등성은 게이트 2에서 `model_dump()` 전 필드 비교로 확인했다(차이 0).
  - 모델은 `model_id`·단자·소자·`source_hash`가 전부 원문에서 나오므로 재파싱이 곧 동일 객체다
    (`source_hash = sha256(text)`; 게이트 2에서 소자별 `repr`와 1e3/1e6/1e8 Hz 임피던스까지 비교, 차이 0).
  - 확인: 캐시 파일 바이트에 `spd_decap_pi` 문자열이 없다(`cache.demo()` assert).
- **헤더 불일치·손상은 조용히 miss**: `FileNotFoundError / EOFError / UnpicklingError / ValueError /
  TypeError / AttributeError / ModuleNotFoundError / PermissionError`를 잡아 `None`을 돌려주고 재생성한다.
- **원자적 쓰기**: `tmp{pid}` → `os.replace`, `PermissionError`면 0.05 s 간격 100회 재시도
  (`exp5/pipeline.py:65-80` 그대로).
- **shapes는 (sha, 층) 단위 파일**이다. 연구 캐시는 포트마다 `shapes_{tag}.pkl` 한 덩어리를 통째로
  다시 썼지만, 층별 파일이면 동시 실행 프로세스가 같은 파일을 겹쳐 쓸 일이 거의 없고 겹쳐도
  `os.replace`가 원자적이다(게이트 5).
- 데이터 없는 셀프체크: `python -m spd_pi_engine.cache` → `DEMO PASS` (round-trip, 제품 객체 부재,
  sha 불일치·절단 파일 → miss).
- 실측 크기: 260729 캐시 25파일 164 MB(추출 P18 33 MB·P14 27 MB, 층 shapes 각 ~11 MB).

## 4. 게이트 결과

| # | 게이트 | 결과 |
|---|---|---|
| 1 | `list_ports` vs 참조 npz | **PASS** 92/92/160, 집합·순서 모두 동일 |
| 2 | 캐시 등가성 | **PASS** 차이 0 (연구 `extract.extract` 대비), 캐시 재로드 차이 0 |
| 3 | 새 prepare로 수치 | **PASS** 5.829e-12 (legacy) / 5.584e-09 (804 p GPU) |
| 4 | s5m6585 (PCB) | CPU **PASS** 9.376e-11 / GPU **1.580e-07 > 1e-8 FAIL** (§5-2) |
| 5 | 8프로세스 동시 실행 | **PASS** 예외 0, 순차 실행과 digest 동일 |
| 6 | 연구 코드 무변경 | **PASS** `git status --short -- tools/research-claude` 출력 없음 |

### 게이트 1 — 포트 열거 (`g1_ports.py` → `g1_ports.json`)

| 설계 | SPD 포트 수 | 참조 npz | 집합 동일 | 순서 동일 |
|---|---|---|---|---|
| 260729 | 92 | 92 | True | True |
| 260804 | 92 | 92 | True | True |
| s5m6585 | 160 | 160 | True | True |

SPD의 `.Port` 블록 순서가 참조 npz `port_names`(`"::"` 앞부분) 순서와 그대로 일치한다. 불일치 0.

### 게이트 2 — 캐시 등가성 (`g2_cache.py` → `g2_cache_260729.json`, `g2_cache.log`)

빈 캐시 디렉터리(`WORK_DIR\engine_cache`)로 시작해서 `prepare()` → 연구 `exp5.pipeline.prepare` 및
오늘 새로 돌린 연구 `exp1/extract.extract`와 비교. 비교 규칙: numpy 배열 `np.array_equal`(+shape·dtype),
스칼라·문자열 `==`, `StackupLayer`는 `model_dump()` 전 필드, decap 모델은 `model_id`/단자/`source_hash`/
소자 `repr`/1e3·1e6·1e8 Hz 임피던스.

| 포트 | 비교한 잎(leaf) 수 | 연구 pkl 대비 차이 | 오늘 연구 `extract` 대비 차이 | 캐시 재로드 대비 차이 |
|---|---|---|---|---|
| 260729 Port18_SITE0 | 872,747 (ex 키 24 + shapes 19층) | 421 (§5-1) | **0** (잎 464,240) | **0** (잎 875,699) |
| 260729 Port14_SITE0 | 568,256 (ex 키 24 + shapes 19층) | 35 (§5-1) | **0** (잎 92,570) | **0** (잎 568,506) |

- `fine_box`, decap 수(421 / 35), 모델 id 수(3 / 3) 일치.
- 벽시계: cold(추출+shapes 파싱+캐시 쓰기) 38.2 s / 25.4 s, **캐시 히트 1.31 s / 1.20 s**
  (연구 pkl 로드 0.6 s / 0.5 s — 연구 pkl은 포트당 1파일, 엔진은 층별 파일 19개를 더 읽는다).
- shapes 층 집합: 엔진 = 그 포트에 실제로 필요한 층만(19개). 연구는 누적 pkl이라 47개 중 28개가
  더 들어 있다(엔진에만 있는 층 0 → 모델 입력에는 차이 없음, `Model`은 층 이름으로만 조회한다).

### 게이트 3 — 새 prepare로 만든 모델의 수치 (`g34_numerics.py`)

| 케이스 | 백엔드 | 미지수 (영수증) | max rel vs 영수증 | 한도 | 판정 |
|---|---|---|---|---|---|
| 260729 Port18_SITE0, `FLAGS_LEGACY`, 1 MHz 1점 vs `results/exp8/...any.json` | `Backend()` | 261124 (261124) | **5.829e-12** | 1e-9 | PASS |
| 260804 Port18_SITE0, `FLAGS_P`, 사다리 27점 vs `exp28/result_..._any_p.json` | `Backend(solver="cudss", fast=True)` | 275177 (275177) | **5.584e-09** | 1e-8 | PASS |
| (대조) 260804 동일 케이스 | `Backend()` | 275177 | **1.915e-11** | 1e-9 | PASS |

- legacy 5.829e-12는 W2의 값과 **동일**하고 기대값 5.83e-12와 같다 → 새 `prepare`가 추출물을
  한 비트도 바꾸지 않았다.
- 260804 CPU 1.915e-11도 W2 보고서의 1.915e-11과 **동일**하다(W2는 연구 `pipeline.prepare` 입력,
  W3은 엔진 `prepare` 입력). GPU 5.584e-09는 cuDSS 몫이며 계약(1e-8) 안이다.
- `plane_C_total_nF`, `two_sided_layers`도 영수증과 일치.
- 벽시계: legacy build 278 s(+prepare 1.1 s, 캐시 히트), 260804 GPU prepare 34.5 s(cold) + build 10 s + 27점 solve → 총 54 s.

### 게이트 4 — s5m6585 (PCB, 다른 층 이름 규약) (`g34_numerics.py --tag s5m6585`)

`Port1_U1_0`(rail `ADC_AVDD08_LO/0`, decap 7개), `FLAGS_P`, 사다리 27점,
영수증 `WORK_DIR\exp30\result_s5m6585_Port1_U1_0_any_p.json`.

| 백엔드 | 미지수 (영수증) | plane_C_total_nF | two_sided_layers | max rel | 한도 | 판정 |
|---|---|---|---|---|---|---|
| `Backend()` (splu) | 189397 (189397) | 2.6851619031546035 = 영수증 | `['Plane$IN51_VCC31']` = 영수증 | **9.376e-11** | 1e-9 | PASS |
| `Backend(fast=True)` (splu+FAST) | 189397 | 동일 | 동일 | **9.376e-11** | 1e-9 | PASS (CPU와 비트 동일) |
| `Backend(solver="cudss", fast=True)` | 189397 | 동일 | 동일 | **1.580e-07** | 1e-8 | **FAIL** (§5-2) |

- **`DesignConventions`는 기본값 그대로다.** exp30/run11에 태그별 분기가 없다는 것을 확인했다
  (`grep s5m6585` 결과: `paths.DESIGNS` 항목과 주석뿐). s5m6585를 받아들이는 두 규약은 이미
  일반 규칙으로 들어와 있다: `default_is_gnd`가 `Plane$IN43_DGND` 형태를 인식하고
  (`"(DGND)" in name or name.endswith("_DGND") or name.split("$")[-1]=="DGND"`),
  `gnd_net="DGND"`는 이 SPD에서도 실제 GND 네트 이름이며, `top_layer_name="Signal$TOP"`는
  이 설계에 존재하지 않는 이름이라 특수 분기가 자연히 꺼진다(연구 코드와 같은 동작).
  즉 C5는 W2에서 이미 노출됐고 W3에서 하드코딩을 추가할 필요가 없었다.
- 이웃 층 shapes 6개(`Plane$IN48_VCC29` … `Plane$IN54_VCC33`), prepare cold 5.4 s.

### 게이트 5 — 동시 실행 (`g5_concurrency.py` → `g5_concurrency.json`, `g5_concurrency.log`)

260804의 8개 포트(Port10–Port17_SITE0)를 8개 프로세스로 **같은 빈 캐시 디렉터리**에 동시 `prepare()`.

| 항목 | 결과 |
|---|---|
| 예외 / 비정상 종료 | **0 / 8** (returncode 전부 0) |
| 워커별 wall | 65.5–73.2 s, 전체 76.4 s (순차 표본 2포트만으로도 77.0 s) |
| 공유 shapes 층 수 | 포트당 17–23층, 캐시 44파일 370 MB |
| 표본 2포트(Port10, Port14) `ex` | 순차 실행(별도 빈 캐시)과 **digest 동일**, 필드 23개 전부 동일(차이 0) |

digest는 `ex`(`extract_seconds` 제외)+`shapes`+`fine_box` 전체를 배열 바이트까지 넣은 sha256이다.

### 게이트 6 — 연구 코드 무변경

`git status --short -- tools/research-claude` → 출력 없음.

## 5. 계획에서 어긋난 점 / 발견

1. **연구 pkl 캐시가 현재 연구 코드보다 낡았다.** `exp5/extract_260729_Port*.pkl`에는 `decaps[i]`의
   `model_source` 키가 없다(= `extract.py`가 그 필드를 갖기 전에 만들어진 파일). 그래서 게이트 2의
   "연구 pkl 대비 차이 421/35"는 전부 decap당 `keys differ: only_a=['model_source']`이고,
   그 외 차이는 `spd_path`(Port14 pkl은 Linux 컨테이너에서 만들어져 `/home/claude/data/...`)와
   `extract_seconds`(벽시계)뿐이다. **판정 기준은 오늘 새로 돌린 연구 `extract.extract` 대비 차이
   0**으로 했고, `spd_path`·`extract_seconds`·`model_texts` 세 키는 provenance로 비교에서 제외했다
   (모델은 이 셋 중 무엇도 읽지 않는다). 이 사실 자체가 캐시 헤더·키 버전 관리의 근거다.
2. **s5m6585 GPU가 GPU 계약(1e-8)을 넘는다 — 엔진 원인이 아니다.** 같은 `prepare`·같은 빌드에서
   미지수·`plane_C`·`two_sided_layers`가 영수증과 정확히 같고, CPU(splu)는 9.376e-11이며,
   `Backend(fast=True)`(assemble 가속만)도 CPU와 **비트 동일**하다. 초과분은 전적으로 cuDSS 몫이고
   가장 낮은 두 주파수에 몰려 있다(100 kHz 1.58e-7, 30.2 kHz 4.2e-8; 1 MHz 이상은 ≤1e-9, 10 MHz
   이상은 1e-11대). exp30 영수증도 CPU splu로 만들어진 것이다(`factor_s` 2.9 s, `nnz_LU` 21.0M =
   splu; GPU는 9.2M). 계획 §0이 말한 "최저 주파수에서 1e-16 → 8e-9 증폭"과 같은 성질이며,
   이 PCB 레일이 패키지 레일보다 저주파에서 더 나쁜 조건수를 가진다. **조치 제안(W5/W8 영역):
   GPU 계약을 "1 MHz 이상 1e-8, 그 미만은 설계별 보고"로 좁히거나, 저주파 점에 cuDSS 반복 정제
   단계를 늘린다(`Backend.ir_steps`).** 수치 변경이 필요한 사항이라 W3에서는 손대지 않았다.
3. **`extract`의 모델 dict 순서를 결정적으로 바꿨다.** 연구 코드는
   `{mid: models[mid] for mid in {x["model_id"] for x in decaps}}` — 집합 리터럴이라 문자열 해시
   랜덤화 때문에 프로세스마다 dict 순서가 달랐다. 캐시 파일이 재현 가능해야 하므로
   `list(dict.fromkeys(...))`(decap 등장 순서)로 바꿨다. 내용은 동일하고 소비 측은 전부 키 조회다
   (게이트 5의 첫 실행이 이 비결정성을 잡아냈고, 수정 후 digest·필드 모두 동일).
4. **`extract`는 `SystemExit` 대신 `KeyError`를 낸다**(라이브러리이므로). 메시지에 SPD 경로를 넣었다.
5. **`extract`/`load_layer_shapes`의 `print`를 `log=` 콜백으로 바꿨다**(기본 no-op). W2의 `Model(log=…)`과 같은 규약.
6. **`prepare`의 `ReferenceSearch`는 `mode="physical-gnd"`로 만든다.** 연구 `pipeline.prepare`가
   `exp1b.TwoSided`를 쓰기 때문인데, 여기서 쓰는 `candidates()`는 스택업만 걷고 mode·gnd 네트를
   보지 않으므로 어느 모드든 결과가 같다(주석으로 명시).
7. **`parser_version`에 소스 해시를 넣었다.** 계획은 "parser_version 튜플"만 요구했지만 제품 버전
   문자열만으로는 릴리스 내 파서 변경을 못 잡는다 → `(core __version__, sha256/12 of spd.py,
   sha256/12 of spice.py)`. `functools.lru_cache`로 지연 계산해 import 시 파일을 읽지 않는다.
8. **`max_layers`는 추출 키에 들어가지만 추출 결과를 바꾸지 않는다**(계획이 지정한 키 구성 그대로
   유지). 실제로 `max_layers`가 바꾸는 것은 `prepare`가 필요로 하는 이웃 층 집합이다. 키에 남긴
   덕분에 향후 `extract`가 층 수에 의존하게 되어도 캐시가 안전하다.
9. **`Design` 클래스는 만들지 않았다**(계획 §4 W4). 지금은 함수만 `__init__.py`에 노출한다.

## 6. 산출물

- 패키지: `src/spd_pi_engine/spd_source.py`(428줄), `cache.py`(201줄), `__init__.py`(29줄).
  W1/W2 모듈 6개는 무변경. 패키지 합계 2,685줄.
- 게이트 스크립트: `WORK_DIR\engine_w3\{g1_ports.py, g2_cache.py, g34_numerics.py, g5_concurrency.py}`
- 영수증/로그: `WORK_DIR\engine_w3\{g1_ports.json, g2_cache_260729.json, g2_cache.log,
  g34_legacy_260729_Port18_SITE0_cpu.json, g34_p_260804_Port18_SITE0_{gpu,cpu}.json,
  g34_p_s5m6585_Port1_U1_0_{gpu,cpu,cpufast}.json, g5_concurrency.json, g5_par_*.json, g5_seq_*.json,
  g3_legacy.log, g3_p_804.log, g3_p_804_cpu.log, g4_s5m6585*.log, g5_concurrency.log}`
- 캐시: `WORK_DIR\engine_cache`(260729 게이트 2), `engine_cache_par` / `engine_cache_seq`(게이트 5).
- 이 보고서와 사본: `docs/engine/W3_REPORT.md`.
