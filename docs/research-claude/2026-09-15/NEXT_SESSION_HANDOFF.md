# 다음 세션 인계 — 로컬 Windows PC에서 연구 이어가기 (2026-09-16 작성)

이 파일은 클라우드 세션 없이 소유자 PC의 새 Claude Code 세션이 연구를 이어받기 위한 문서다. 세부 근거는 같은 폴더의 `RESEARCH_LOG.md`, `DECISIONS.md`, `MODEL_PHYSICS.md`, `CODEX_HANDOVER_RETROSPECTIVE.md`에 있다.

## 1. 현재 상태

Astra 3-D 경로는 폐기했다(D1). 2-D plane-pair + 회로 하이브리드(D2)를 EXP-1~9로 쌓았다. 채택 모델은 EXP-5 동결형(S2 + EXP-4 (b))에 EXP-8 cavity-wall 기준면 규칙을 더한 것이다. 코드 사슬은 `exp8/run8.py` → `exp5/pipeline.ModelB` → `exp4/run4.Model4` → `exp3/model3.Model3`이다. 260729 Port18의 1 MHz 오차는 26.18%(Astra)에서 **2.69%**로 줄었고, 260804 Port18은 4.9%다. 7케이스 중 G3 PASS는 4개다. G4(f_res)는 전 케이스 FAIL이고, port16/19에는 R 결손이 남았다. 10–100 MHz는 아직 착수하지 않았다. 코드는 `tools/research-claude/`에 있다. 2026-09-16에 경로를 이식 가능하게 바꿨다(`common/paths.py`). 수치와 물리는 바꾸지 않았다. 스모크 테스트에서 영수증과의 상대차가 0이었다.

## 2. 환경 구축 (Windows)

```powershell
cd "C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator"
git fetch; git switch claude/lightweight-hybrid-20260915   # 클라우드 쪽 커밋과 이 인계 변경분이 push된 뒤에만 가능
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1          # 막히면: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -U pip
pip install -e .                      # numpy>=2, scipy, shapely, pydantic 등 (PySide6 포함)
pip install numpy scipy shapely matplotlib psutil

$env:SPD_PI_DATA_DIR = "D:\Downloads\examples"
$env:SPD_PI_WORK_DIR = "D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8 = "1"                 # 스크립트가 µ/Ω를 출력한다(cp949 콘솔 오류 방지)
python tools\research-claude\common\paths.py   # 모든 항목이 ok인지 확인
```

영구 설정은 `setx SPD_PI_DATA_DIR "D:\Downloads\examples"` 등으로 한다(새 터미널부터 적용).

**경로 규약 (`common/paths.py`)**
- `DATA_DIR`의 우선순위는 `SPD_PI_DATA_DIR` → `/home/claude/data`(있을 때) → `<repo>/data`다. SPD 파일을 여기서 찾는다.
- **참조 npz는 복사하지 않는다.** `ref_npz()`가 `SPD_PI_REF_DIR`(설정했을 때) → `DATA_DIR` → `DATA_DIR\analysis` 순으로 찾는다. 따라서 PC에서는 `D:\Downloads\examples\analysis\*_Zdiag.npz`가 그대로 잡힌다.
- `WORK_DIR`의 우선순위는 `SPD_PI_WORK_DIR` → `/home/claude/work` → `<repo>/work`다. 하위 `exp1..exp9`는 없으면 자동 생성된다.
- 설계 id는 `260729`, `260804`, `s5m6585`다. 헬퍼는 `spd_path(id)`, `ref_npz(id)`, `work_file("exp5", name)`, `local_spd(pkl에 기록된 경로)`, `peak_rss_mb()`(Windows 대응)다.
- `work-cache` 압축은 `D:\Downloads\examples\analysis\claude-2026-09-15\work\`에 풀어 `exp1..exp9`, `trackA`, `review2`가 바로 아래에 오게 한다.

**주의**
- pkl은 numpy 2.4 / Python 3.12 / 이 브랜치의 `src`로 만들었다. `stackup_layers_obj`가 `spd_decap_pi` 객체를 담고 있다. numpy 1.x나 다른 `src` 버전에서는 로드가 실패할 수 있다. 그 경우 pkl을 지우면 SPD에서 재생성된다.
- pkl 안의 `spd_path`는 `/home/claude/data/...`다. 이 경로는 `local_spd()`가 `DATA_DIR`로 바꿔 준다.
- `.sh` 러너는 Windows에서 돌지 않는다. `python tools\research-claude\common\run_chain.py --list`로 같은 단계를 실행한다. `ulimit -v` 메모리 제한은 재현되지 않으므로 작업 관리자로 메모리를 확인한다.
- `work\trackA`, `work\review2`의 분석 스크립트는 저장소에 없다. `/home/claude` 경로도 이식하지 않았으므로 필요할 때만 고친다.

## 3. 데이터·중간 산출물 지도

| 파일 (`WORK_DIR` 기준) | 만드는 스크립트 | 쓰는 스크립트 | 재생성 비용 |
|---|---|---|---|
| `exp1\extract_port18.pkl` (33 MB) | `exp1/extract.py`, `run_exp1.py` | exp1b, exp2, exp3(run3, extract_gnd3), exp4 | SPD mmap 1회 18 s, 1.5 GB |
| `exp1\neighbour_shapes.pkl` (150 MB) | `exp1/exp1b.py`, `exp5/pipeline.prepare` | exp2–4, **exp5–9의 260729 전부** | 필요한 층만 자동 추가. 수십 초로 추정(로그에 따로 기록되지 않음) |
| `exp1\result.json` | `run_exp1.py` | exp2–4의 `fine_box_um` | 저장소 사본 `results/exp1/` |
| `exp2\extract_gnd.pkl` (34 MB) | `exp2/extract_gnd.py` | exp2, probe | 약 9 s |
| `exp3\extract_gnd3.pkl` (46 MB) | `exp3/extract_gnd3.py` | exp4 S3, exp7 variant C, **exp9 `--gnd s3`** | 약 9 s(노드 46만) |
| `exp5\extract_{tag}_{port}.pkl` ×7 (26–44 MB) | `pipeline.prepare`(없으면 자동) | exp5–9, 스모크 테스트 | 레일당 약 18 s |
| `exp5\shapes_260804.pkl` (82 MB) | `pipeline.prepare`(자동) | 260804 케이스 | 수십 초 |
| `exp5\result_*_{sweep,ladder}.json` | `pipeline.py` | `run8 --predict`, `analyze8`, `table9` | 저장소 사본 `results/exp5/` |
| `exp8\result_*_any.json` | `run8.py` | `analyze8`, `analyze9`, `table9`, 스모크(저장소 사본 사용) | 저장소 사본 `results/exp8/` |
| `exp9\mp_*.npz` (최대 86 MB) | `run9.py` | `analyze9` | 레일당 수 분–25 분 |

`work-cache`가 없어도 SPD와 참조 npz만 있으면 전부 재생성된다. JSON만 필요하면 `docs\research-claude\2026-09-15\results\expN\*.json`을 `WORK_DIR\expN\`에 복사해도 된다. 복사한 파일은 원본을 덮어쓰지 말고 사본으로 둔다.

## 4. 실행 절차

모든 명령은 저장소 루트에서 venv를 켜고 환경변수를 설정한 뒤 실행한다.

```powershell
# G0 재현 게이트(새 작업 전 필수): 약 3 분, 1.3 GB
python tools\research-claude\common\smoke_port18.py      # 기대: err vs PowerSI = 2.69 %, SMOKE PASS

# 전체 파이프라인
python tools\research-claude\common\run_chain.py exp5    # 추출 + GND-only 동결 모델 7케이스
cd tools\research-claude\exp8; python run8.py --predict; cd ..\..\..
python tools\research-claude\common\run_chain.py exp8    # cavity-wall 7케이스
cd tools\research-claude\exp8; python analyze8.py; cd ..\exp9; python table9.py
```

**예상 시간·메모리** (클라우드 2 vCPU 기준, 출처는 각 결과 JSON의 `wall_seconds`와 `stats`)
- exp5 체인: P14 40 s, P19 98, P1 89, P18 328, 804-P18 347, P16 364, P7 638 → 약 32 분. 최대 2.0 GB.
- exp8 체인: P14 21 s, P1 76, P19 79, 804-P18 232, P18 241, P16 344, P7 601 → 약 27 분. 최대 2.1 GB(P7, 미지수 55만).
- 모델 build는 P18에서 155 s가 걸리고, 이후 주파수당 인수분해가 약 2.8 s다.
- exp9: `--gnd s3`는 1090 s, 3.3 GB다. Port7 `--freqs few`는 1525 s, 2.2 GB다.
- 제외된 경로: EXP-3 S3는 build 1090 s에 4.2 GB다. EXP-2 초기 설정은 OOM으로 실패했다.

## 5. 검증 게이트와 규칙

게이트는 D3에서 고정했고, 바꾸지 않는다.
- **G1**: 1–100 kHz에서 |ΔRe| ≤ 0.05 mΩ.
- **G2**: 0.1–1 MHz에서 ΔL(f)가 ±5 pH 이내.
- **G3**: 1 MHz 복소 상대오차 < 10%.
- **G4**: 1–10 MHz 오차 < 20%, f_res ±10%. Im Z 영교차 f0도 함께 보고한다(D6).
- **G5**: 10–100 MHz는 보고만 한다.

규칙은 다음과 같다.
1. **튜닝 금지.** 참조(PowerSI)를 보고 파라미터를 맞추지 않는다. 가설, 변경점, 판정 기준은 실행 **전에** 보고서에 적는다. held-out(260804, s5m6585)은 규칙을 동결한 뒤에만 연다.
2. **영수증 보존.** 모든 실행은 결과 JSON 영수증을 남긴다. 영수증에는 입력, 파라미터, freq/Z/Zref, 게이트, stats, wall, 메모리가 들어간다. 여기에 `EXPn_REPORT.md`를 더한다. 기존 `docs/research-claude/2026-09-15/results/`와 `WORK_DIR` 원본은 덮어쓰지 않는다. 새 실험은 `exp10`, `exp11` … 새 폴더에 쓴다.
3. 동결 모델의 기본 동작은 바꾸지 않는다. 변형은 플래그나 서브클래스로 추가하고, 기본값은 기존 동작으로 둔다.
4. 연구 코드(`tools/research-claude/`)와 제품 코드(`src/`)를 분리한다. `src`는 import만 한다.
5. `docs/handoff/` 스냅샷과 `accuracy_parse.py`는 수정하지 않는다. 커밋은 소유자가 요청할 때만 한다.

## 6. 남은 과제 (우선순위)

1. **1–100 kHz 참조 신뢰성 판정**(회고 §8-7, 1 h 이내, 로컬만으로 가능). 0.001–100 kHz 구간에 2–4극 유리함수를 맞춘다. 잔차가 약 1e-6이면 fit 데이터로 판정하고, G1 해석을 보고서에 적는다. SPD의 `DCFitted=1 BBSFitted=1`이 근거다.
2. **PowerSI `MaxEdgeLength` 1 mm·0.5 mm 재해석**(소유자 실행). P18 Touchstone export를 요청한다. 세분하면 L이 +10~13 pH 늘고 f_res가 약 1.3 MHz로 내려가는 경우를 H1(참조 미수렴)으로 본다. 변화가 <1–2 pH이면 H2(우리 L 공식이 약 30% 과대)다. 비교 스크립트는 미리 준비해 둔다.
3. **port19 R 결손**(≈5.3 mΩ, 미확정). PowerDC 요소별 전류·전압이나 decap 1개 bare-rail DC R이 필요하다. P14와 P16에도 같은 분해를 적용한다.
4. **[구현 차이] 되돌리기**(`MODEL_PHYSICS.md`). 한 번에 하나씩 플래그로 교과서 형태를 켠다. 매번 7케이스의 G1–G5를 다시 확인하고 새 영수증을 남긴다. 개선이 없어도 결과를 기록한다.
   - (a) εr/tanδ를 1 MHz 고정에서 주파수 테이블 보간으로 되돌린다(`1b:131-134`).
   - (b) 인접 도체가 벽이 아닐 때 생략하던 C를 복원한다(`1b:129`).
   - (c) 양측 Zs 판정을 층 단위 과반에서 셀 단위로 바꾼다(`P5.ModelB.build`).
   - (d) 벽 쪽 Zs를 포함한다(교과서 M-FDM `2/(σt)+2√(jωμ/σ)`, `M3:452-453`, `R4:77`).
   - (e) fringing의 `min(·,w)` 상한과 문턱 5d(`M3:205`)를 확인한다.
   - (f) 균질화 L에 1/G를 적용하는 방식을 구멍 ≲ d일 때 보정한다(`HG`).
   - (g) via 배럴 면적을 얇은 껍질 근사에서 정확한 고리 면적으로 바꾼다(`VM:79`).
   - (h) via L의 반경 b를 최근접 GND 거리 대신 antipad 반경으로, 동축 절반값 대신 two-wire로 바꾼다.
   - (i) 부품 바디–평면 사이 실장 루프 L을 추가한다.
5. **일반화 검증**: 260729 92포트 전체와 s5m6585(160포트, small-hole void 약 16만 개). 게이트와 holdout은 결과를 보기 전에 동결한다.
6. **10–100 MHz**: 반공진 9–15 MHz 오차 < 15%, 100 MHz Re Z ±20%. GND 손실을 넣되 G1이 유지되는 정식화가 필요하다.
7. **속도**: 보드는 한 번만 인수분해하고 decap을 Schur 보정으로 처리한다(EXP-9 다중포트 방식 확장). 전체 재해석과의 ΔZ가 0.5% 미만이어야 한다.
8. **제품 통합**: layerwise를 대체한다. "PowerSI-compatible(cavity-wall)"과 "Physical return(GND-only)"을 명시 모드로 둔다(D5).
9. `tools/research-claude/exp9/README.md`를 작성한다.

## 7. 문서 위치 지도

- `docs/research-claude/2026-09-15/`
  - `README.md`: 인덱스.
  - `RESEARCH_LOG.md`: 전체 기록. 미해결 항목은 §6, 재현은 §8.
  - `DECISIONS.md`: D1–D6.
  - `MODEL_PHYSICS.md`: 식, 코드 위치, [구현 차이].
  - `CODEX_HANDOVER_RETROSPECTIVE.md`: 회고와 후속 과제(§8).
  - `results/expN/`: 보고서, JSON 영수증, png.
  - `reviews/`, `figures/`, `SPD_PI_연구보고서_2026-09-15.docx`.
  - 이 파일.
- `tools/research-claude/expN/`: 실험 코드와 README.
- `tools/research-claude/common/`: `paths.py`, `smoke_port18.py`, `run_chain.py`.
- `docs/handoff/2026-09-15/`: Codex 인계 스냅샷(읽기 전용).
- 참조 npz 생성 스크립트는 `D:\Downloads\examples\analysis\scripts\process_s92p_*.py`다.
- claude.ai Project "Peer review"에도 같은 문서 사본이 있다(`claude/spd-pi-*.md`).

## 8. 첫 프롬프트 (새 세션에 붙여넣기)

```text
이 저장소는 SPD→Z(f) 경량 PDN 모델 연구를 이어가는 중이다. 먼저 docs/research-claude/2026-09-15/NEXT_SESSION_HANDOFF.md 전체와 CLAUDE.md를 읽어라.
1) 환경 확인: .venv(Python 3.12)가 활성인지, SPD_PI_DATA_DIR / SPD_PI_WORK_DIR / PYTHONUTF8 가 설정됐는지 확인하고
   `python tools\research-claude\common\paths.py` 결과를 보여라. MISSING이 있으면 멈추고 나에게 물어라.
2) 재현 게이트: `python tools\research-claude\common\smoke_port18.py` 를 실행해 260729 Port18 1 MHz 오차 2.69 %,
   영수증 대비 상대차 ≤1e-6, SMOKE PASS 를 확인하라. 실패하면 새 작업을 시작하지 말고 원인만 보고하라.
3) 통과하면 인계 문서 §6 과제 #1(1–100 kHz 참조 신뢰성 판정)을 시작하라. 실행 전에 가설·방법·판정 기준을
   WORK_DIR\exp10\EXP10_PLAN.md 에 먼저 쓰고 나에게 보여라.
규칙: 참조에 맞춘 파라미터 튜닝 금지, 모든 실행은 JSON 영수증 + 보고서, 기존 결과 덮어쓰기 금지,
docs/handoff 스냅샷·accuracy_parse.py 수정 금지, 커밋은 내가 요청할 때만.
```
