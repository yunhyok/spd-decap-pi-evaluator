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

## 9. 2026-09-16 로컬 세션 진행 상황 (추가)
- G0 재현: `smoke_port18.py` PASS(상대차 5.83e-12, 239 s, 1262 MB). 환경변수는 세션 안에서만 설정(setx 미사용), venv 없이 전역 Python 3.12.
- §6-1 완료: EXP-10/10b(`results/exp10/`). 참조 ≤1 kHz는 켤레 비대칭 항(B≠0, G<0, Z(0) 불일치)이 있는 생성값(Adaptive sweep 보간). 1–100 kHz는 INDETERMINATE. G1·G2 해석 불변. 소유자 확인: `_S` 파일은 DCFitted 미적용, BBSFitted 비활성.
- §6-4 완료(i 제외): EXP-11~14(`results/exp11..14/`, 러너 `tools/research-claude/exp11/run11.py --variant ...`, 비교 `table11.py`). 평면 C 단위 오류(1e6배, `exp1b.py`) 발견 → 플래그 `c_unit_fix`. **기준선은 소유자 승인으로 exp13/j(EXP-8 + c_unit_fix)**; 코드 기본값·EXP-8 영수증·smoke는 그대로다. 다른 변형은 전부 기본값 유지.
- 남은 과제 우선순위: §6-2(PowerSI MaxEdgeLength, 소유자 실행), §6-3(port19 R 결손 — (g) via 배럴은 원인 아님, (d) 벽 R은 P16/P19만 개선), §6-5(92포트 일반화, exp13/j 기준선), §6-6(10–100 MHz — (h) via L 2배가 G5 개선하는 단서), §6-4(i)(decap 실장 기하 필요).
- 참조 재해석 요청(급하지 않음): 저주파 0.1 Hz–1 MHz Log/Linear 이산 스윕, 전 포트, 대상 `E:\Work\20260724 S4LB002_DC MLO PI\S4LB002-2Para_260729_1_injected.spd`.
- §6-5 완료(EXP-15, `results/exp15/`): 92포트 G3 PASS 30/92, G1 8/92. **채택 모델은 일반화되지 않는다.** 영수증 92개는 `WORK_DIR/exp15/`(저장소에는 summary만). 러너 `tools/research-claude/exp15/runall15.py`(재개 가능), 집계 `summary15.py`.
- §6-3·§6-6 착수(EXP-16/17, 보유 자료만): R 결손은 핀필드·포트 국소 경로 R 관례가 유력(SITE 쌍 부호 반전, via+trace R 비중과 ρ 0.79). 10–100 MHz는 90/92포트에서 모델 R 부족(중앙값 25 %). 다음 사전 등록 후보: 고주파 전용 벽 표피 R, GND-only 92포트, ΔL 지표 재정의. 소유자 확인 실험은 Port19_SITE0/Port65_SITE1 PowerDC 요소별 전류 한 쌍.
- §6-6 첫 정식화(EXP-18b, `results/exp18/`): 벽 표피 R(실수부, 자유 계수 없음)은 게이트를 해치지 않으나 92포트 고주파 R 부족을 0.254 → 0.234밖에 못 줄여 채택 문턱 미달. 남은 부족은 via·trace 급전 경로 쪽. 92포트 k2 영수증은 `WORK_DIR/exp18/`.
- 오늘 기준 소유자 결정 대기: 없음(기준선 exp13/j 유지). 소유자 실행 요청: (1) Port19_SITE0/Port65_SITE1 PowerDC 요소별 전류, (2) 저주파 Log/Linear 이산 스윕 전 포트 재해석(급하지 않음), (3) §6-4(i)용 대표 decap 실장 기하.
- EXP-19(`results/exp19/`): §6-3과 §6-6은 같은 급전 경로 R 관례 문제(ρ 0.75). 고주파 L 부족은 없고 오히려 모델 L이 f에 따라 줄지 않는 것이 차이. EXP-20(via R 표피효과 m/mk, `WORK_DIR/exp20/`) 진행 중.
- EXP-20(`results/exp20/`): via 표피 R + 벽 표피 R(mk, 자유 계수 없음)으로 92포트 고주파 R 부족 0.254 → 0.203. 문턱(0.15) 미달이라 기록만. 남은 부족은 주파수 무관한 급전 경로 R 관례 → 소유자 PowerDC 자료(Port19_SITE0/Port65_SITE1) 필요. EXP-21(GND-only 92포트, `WORK_DIR/exp21/`) 진행.
- 2026-09-17 EXP-21~27: cavity 규칙·기본폭 trace·via 배럴은 R 결손 원인 아님. **모델 연결성 결함 발견**: 기준면을 제외한 소자 그래프에서 포트와 끊긴 decap이 92포트 중 44포트(56개, 전부 TOP 층 패드 노드), R 오차와 강한 상관(p 1e-6). P65 10 µF 고립이 사이트 부호 반전의 원인. 결함 기구 추적과 수정 플래그(EXP-28)가 다음 과제.
- **2026-09-17 결론**: 균질화 경계조건 결함(EXP-27)을 `homog_face_fix`(EXP-28, 변형 p)로 수정. 고립 decap 0, 파국 오차 제거, P18 0.81 %. 게이트 PASS는 줄었지만(30 → 22) 이는 결함이 가리던 균일 R 결손(참조/모델 1.46배)이 드러난 것. **소유자 결정: exp28/p를 새 기준선으로 채택할지**(권고: 채택; 코드 기본값·smoke는 그대로 두고 후속 변형은 `homog_face_fix=True` 포함, `table11.py --baseline exp28:p`).
- 균일 R 결손의 원인 후보는 PowerSI decap별 직렬 항(실장 R), 도전율·두께 관례, 경로 R 산정. 보유 자료로는 더 못 가림(EXP-29). 소유자 확인: (1) PowerSI decap 부품 모델의 실장 기생 자동 추가 여부, (2) PowerDC 요소별 전류·전압(Port19_SITE0/Port65_SITE1), (3) MetalModel 도전율·두께, (4) MaxEdgeLength 재해석(§6-2).
- 92포트 영수증 세트: `WORK_DIR/exp15`(j), `exp18`(k2), `exp20`(mk), `exp21`(j_gnd), `exp28`(p). 감사 `exp27/exp27.json`(j), `exp27_p.json`(p).
- 2026-09-17 후반: 소유자 지시로 실장 기생 옵션 없음 가정, PowerDC 후순위. **s5m6585(PCB) 160포트에서 모델이 맞음**(err 중앙값 0.58 %, G3 80 %, R 비 1.03) → 260729의 R 결손은 패키지 급전 구조(충전 microvia 스택·패키지 trace) 관례 차이. EXP-31: 평면 ×1, 경로 ×2–2.5. EXP-32(via 길이 표면 간 ×1.4, 변형 q) 실행 중(`WORK_DIR/exp32/`). 소유자 GUI 확인 항목: PowerSI microvia 모델(도금 두께 기본값·충전 여부·길이 정의·원추형). s5m6585 실행에는 `run11.py --tag s5m6585`, `runall15.py --tag s5m6585` 사용.
- 2026-09-17 마감 상태: 기준선 exp28/p(소유자 명시 승인은 아직 없음; 결함 수정이라 후속 실험은 p 위에서 진행). EXP-32(q)는 방향 맞고 문턱 근소 미달(기록). 보유 자료로 가능한 판별은 사실상 소진. **다음 진행 조건**: (1) PowerSI microvia 모델 정의(도금 두께 기본값·충전 여부·길이 정의·원추형)의 GUI 확인 → 폐형식으로 옮겨 사전 등록, (2) f_res 지표 대체(Im Z 영교차 f0 또는 위상 기준)로 G4 재정의 여부 결정(소유자), (3) PCB L 분해 재정의(f_res_ref < 10 MHz, 30 MHz 평가)로 §6-4(i) 실장 L 판정. 92포트 영수증 세트 추가: `WORK_DIR/exp32`(q), PCB: `exp30`(p).
- **결정 확정(2026-09-17, 소유자 위임)**: D7 기준선 = exp28/p(코드 기본값·smoke 불변, 변형은 p 플래그 포함, `--baseline exp28:p`). D8 G4 유지. q(via 길이)는 플래그 보존. 진행 중: EXP-35(p + via·벽 표피 R, 변형 pmk, `WORK_DIR/exp35/`).
- **소유자 확인용 파일**: PowerSI microvia 모델 정의는 `E:\Work\20260724 S4LB002_DC MLO PI\S4LB002-2Para_260729_1_injected.spd`(및 `E:\Work\20260804 S4LB002_DC MLO\S4LB002-2Para_260804_1_injected.spd`)에서 padstack `DR-0102_60 … DR-2930_60`(드릴 40 µm, 패드 60 µm, COPPER, 2층; 레일 via의 약 95 %)와 core PTH `DR-2128_350`(드릴 150 µm, 8층)을 대상으로: 도금 두께 기본값·충전(filled) 여부·via 길이 정의·원추형 옵션.
- 2026-09-17: FICT MLO 설계 규칙(2024-01) 대조 → SPD 기하는 규칙과 일치(LVH 40/60, ABF 30 µm, Cu 20 µm, IVH 150/350). 문서는 LVH 충전·원추 여부를 명시하지 않으나 스택 via 규칙상 충전 via가 전제. 남는 물음은 PowerSI의 microvia 모델링(도금 두께 기본값·충전 해석·길이 정의). 메모 `reviews/mlo_design_rules_2024_note.md`.

## 10. 2026-09-17 세션 마감 — 새 세션 인계 (context 한계로 종료)

### 상태 요약
- 기준선: **exp28/p**(D7). 코드 기본값·`smoke_port18.py`·EXP-8 영수증 불변. 모든 후속 변형은 p 플래그(`c_unit_fix, homog_face_fix`) 포함, 비교는 `table11.py --exp expNN --baseline exp28:p`.
- 모델은 PCB(s5m6585)에서 맞고(EXP-30, err 중앙값 0.58 %), 패키지(260729)의 남은 오차는 급전 경로(microvia 스택·trace) R 관례(참조/모델 1.46, 평면 ×1, 경로 ×2–2.5; EXP-31). via 길이 표면 간(q, EXP-32)은 1/3 설명(기록만, 플래그 보존).
- 소유자 제공 자료 반영: FICT MLO 설계 규칙(기하 일치, `reviews/mlo_design_rules_2024_note.md`), Allegro padstack pxml(DR-1011-60 40/60 plating Y), PowerSI 옵션 화면(`reviews/powersi_options_2026-09-17.md`). 도금 두께 0의 의미와 Mesh 설정은 미확인.
- 소유자 지시: 실장 기생 옵션 없음 가정, PowerDC 경로 R은 후순위(선형 보증 없음), 결정은 정확도·효율 유리 방향(D7·D8).

### 실행 중(마감 시점, 결과는 WORK_DIR에 쌓임)
- EXP-35 변형 pmk(p + via 표피 R + 벽 표피 R): 7케이스 재실행(`exp35/logs/*_pmk.log`) + 92포트 드라이버(`exp35/logs/runall35_pmk_driver2.log`). 첫 실행은 `VARIANTS["pmk"]` 키 누락(KeyError eps_table)으로 풀이 후 영수증 저장에 실패 → 정의 수정 후 재실행. 판정 기준 `exp35/EXP35_PLAN.md` §3.
- EXP-36 변형 pv(p + PowerSI Special Void 1500 µm 채움): 7케이스 실행 중(`exp36/logs/*_pv.log`), 92포트는 미실행. 판정 `exp36/EXP36_PLAN.md` §3(관례 일치 플래그로 기록, 기준선 변경 없음).

### 새 세션 첫 프롬프트
```text
이 저장소는 SPD→Z(f) 경량 PDN 모델 연구를 이어가는 중이다. docs/research-claude/2026-09-15/NEXT_SESSION_HANDOFF.md 전체(특히 §9–§10)와 CLAUDE.md, DECISIONS.md D7·D8을 읽어라.
환경: 전역 Python 3.12(venv 없음), 세션 환경변수 SPD_PI_DATA_DIR=D:\Downloads\examples, SPD_PI_WORK_DIR=D:\Downloads\examples\analysis\claude-2026-09-15\work, PYTHONUTF8=1. python tools\research-claude\common\paths.py에 MISSING이 없고 smoke_port18.py가 PASS(2.69 %, 상대차 ≤1e-6)인지 확인하라.
그다음 (1) WORK_DIR\exp35(pmk)·exp36(pv)의 완료된 영수증을 table11.py로 판정해 EXP35/36 보고서를 쓰고 results/에 복사하라(미완료 포트가 있으면 runall15.py로 재개, 기존 영수증 덮어쓰기 금지). (2) 남은 후보는 PowerSI microvia 모델 정의(도금 두께 0의 의미, Mesh 설정) 확인 후 정식화이며, 그 전까지는 보유 자료 분석만 한다.
규칙: 참조에 맞춘 튜닝 금지, 실행 전 사전 등록(WORK_DIR\expNN\EXPNN_PLAN.md), 모든 실행은 JSON 영수증 + 보고서, 기존 결과·docs/handoff 스냅샷·accuracy_parse.py 수정 금지, 커밋은 소유자 요청 시에만. 코드 작성은 Opus/Sonnet 서브에이전트에 위임하고 Fable은 계획·검토만 한다.
```
- **추가(마감 직후)**: EXP-36 pv 7케이스 완료 — 1500 µm 미만 void를 채우면 전 케이스 악화(G3 중앙값 6.6 → 15.9 %, P18 0.81 → 15.9 %, G1·G3 PASS→FAIL 다수). PowerSI "Special Void 제외" 설정은 참조 평면이 void 없이 계산됐음을 뜻하지 않거나 다른 처리와 상쇄된다. **pv 기각, 92포트 불필요**(`table11.py --exp exp36 --baseline exp28:p --variant pv`). 모델의 void 포함 평면이 맞다는 반증이기도 하다. EXP-35 pmk 92포트는 마감 시점 73/92 진행 중.
- **추가(마감 직후 2)**: EXP-35 pmk 7케이스 완료 — (a) 게이트 PASS 집합 불변·≤1 MHz 변화 없음, (b) 10–100 MHz ΔR/Re Z_ref 중앙값 −0.161 → −0.056(65 % 감소) → 7케이스 기준 모두 성립. 92포트(83/92 진행 중)에서 H3 문턱(ΔR<0 비율 ≤ 0.70 또는 중앙값 |ΔR|/Re Z_ref ≤ 0.15) 판정이 남았다: `python tools\research-claude\exp17\dl17.py --dir exp35 --variant pmk` 와 `compare92` 계산(EXP-20 §2 표 형식)으로 EXP35_REPORT.md를 쓸 것.
- **추가(마감 직후 3)**: EXP-35 pmk 92포트 완료·판정 완료 — 게이트 불변, 10–100 MHz 중앙값 |ΔR|/Re Z_ref 0.379 → 0.339, ΔR<0 비율 1.00 → 0.95 → 채택 문턱 미달, **기록만**(EXP-20과 같은 결론). 보고서 `results/exp35/EXP35_REPORT.md`. 이제 실행 중인 작업은 없다. 새 세션은 §10 첫 프롬프트의 (1)에서 EXP-35/36 판정을 건너뛰고(완료), 남은 후보(PowerSI microvia 모델 정의·Mesh 설정 확인 후 정식화)로 바로 간다.


## 11. 2026-09-17 후반 세션 — EXP-35/36 판정, A2000 GPU 경로 도입

### 결과
- G0: `smoke_port18.py` PASS(상대차 5.83e-12, 부하 중 846 s, 1439 MB).
- **EXP-35(pmk = p + via 표피 R + 벽 표피 R 실수부)**: 7케이스 (a) 성립(PASS→FAIL 0, ≤ 1 MHz max |ΔZ|/|Z| ≤ 6.5e-4; P1·P7·P18의 10–100 MHz R 부족 소멸). 92포트 (b) 불성립(포트별 중앙값 |ΔR|/Re Z_ref 0.379 → 0.339, ΔR<0 비율 1.00 → 0.946; 문턱 0.15 / 0.70). **기록만, 기준선 exp28/p 유지.** `results/exp35/`.
- **EXP-36(pv = p + Special Void 1500 µm 채움)**: 7케이스 PASS→FAIL 5건(G1 2, G3 5; P18 0.81 → 15.9 %). 채움은 평면 R을 12–19 % 낮춰 R 결손을 키운다 → **기록만**, 관례 일치 플래그로도 채택하지 않음. 92포트는 계획대로 GPU 경로로 실행(§ 아래). `results/exp36/`(§4는 92포트 완료 후 추가).
- PowerSI Mesh 설정 확인(소유자 스크린샷): MaxEdgeLength 4970 µm 기본값, Simplify geometry ON, Coarse mesh OFF → `reviews/powersi_options_2026-09-17.md`에 추가. §6-2(H1 참조 미수렴) 가설은 여전히 열려 있다.
- 재사용 스크립트 `tools/research-claude/exp35/compare92.py --exp expNN --variant V --baseline exp28:p`(92포트 비교 JSON 생성; `--selftest`가 exp32 JSON을 바이트 단위로 재현). rb/rq 필드는 100 kHz 한 점의 Re Z_ref/Re Z_model이다(보고서의 "중앙값"은 포트 간 중앙값).

### A2000 GPU 경로 (`SPD_PI_SOLVER=cudss`, 옵트인)
- 연구 코드는 원래 CPU 전용(scipy splu, 프로세스당 단일 스레드)이었고 어떤 GPU도 쓰지 않았다. 작업 관리자의 Xe 활동은 화면 표시 부하다.
- 전역 Python에 `nvmath-python 1.0.0`, `nvidia-cudss-cu12 0.8.0.10`, `cuda-bindings 12.9.8`(+CUDA 12.9 런타임 휠, 약 1.1 GB) 설치. numpy 2.4.4 / scipy 1.18.0 불변. CuPy 불필요. 드라이버 528.79(CUDA 12.0)에서 minor-version 호환으로 동작 확인. `cuda-bindings`는 반드시 `12.*`로 고정(13.x는 580+ 드라이버 필요).
- 코드: `common/cudss_solver.py`(CudssLU: 모델당 1회 plan, 주파수마다 값만 갱신·refactorize), `exp3/model3.py Model3.solve()`(환경변수 있을 때만 분기, 실패 시 splu로 폴백, stats에 `solver` 키), `exp15/runall15.py`(cudss일 때 `--jobs` ≤ 4로 클램프, PYTHONUNBUFFERED=1). 기본 경로 불변(smoke 5.83e-12, `--variant p --smoke` 0.0).
- 정확도: P18 smoke 2.66e-10, P14 전 주파수 max |ΔZ|/|Z| 4.7e-10, pv 소형 포트 ≤ 5.1e-9(CPU 동일 변형 대비). 속도: 인수분해 P18 38.7 s(부하 중) → 0.10–0.13 s, nnz(LU) 1/3(nested dissection). **단, 주파수당 assemble 3 s와 모델 build가 남아 포트 전체로는 1.3–4배.** 다음 가속 대상은 assemble/build.
- 결함과 회피: cuDSS 0.8.0.10에서 `DirectSolver.free()`가 소형 N(< 약 7만)에서 0xC0000005로 죽는다 → 해제하지 않고 프로세스 종료에 맡김(포트당 1 프로세스라 무해). `SYMMETRIC` 행렬 타입도 크래시 → GENERAL 사용. `CUDA_VISIBLE_DEVICES`를 비우면 예외가 아니라 하드 크래시. `--variant none --smoke`의 문턱 1e-9에 cudss는 2.7e-10으로 여유가 작으니 smoke는 기본 경로로 돌린다.
- 자원: VRAM은 프로세스당 수백 MB(4개 동시 약 360 MB), 호스트 RSS +1.1 GB/프로세스. 클램프 4는 보수적이며 8–10도 가능할 것으로 보이나 검증 전.
- 스크래치: `WORK_DIR/exp37gpu/`(검증용 영수증, 실험 아님). 세션 scratch의 gpuvenv/leanvenv는 삭제해도 된다.

### 진행 중 / 다음
- EXP-36 pv 92포트 완료(GPU 경로, 86포트 약 45 분, 실패 0): err 중앙값 31.7 → 32.8 %, G3 22 → 5, R 비 1.464 → 1.491 → 결손 확대 확인, 기록만. `results/exp36/` 갱신 완료. 실측 GPU 시간: N 1.33M 포트 951 s(인수분해 합 104 s, 나머지는 build·assemble), 소형 포트 중앙값 34 s.
- 남은 과제는 §10과 같다(PowerSI microvia 모델 정의 확인 후 정식화; 급전 경로 R 관례). GPU로 92포트 회전이 빨라졌으므로 §6-7(1회 인수분해 + Schur) 대신 assemble 가속이 다음 효율 과제다.
- 소유자 지시(2026-09-17): 사용 가능하면 A2000을 적극 사용한다 → CLAUDE.md "GPU 정책" 절에 반영. 다음 과제로 EXP-37(assemble/build 가속, 물리 변경 없음, `WORK_DIR/exp37/EXP37_PLAN.md`) 진행 중.
- **EXP-37 완료(효율, 물리 불변)**: `results/exp37/`. 프로파일: build가 84–87 %(`homog.batched_gx`, 참조 탐색 `rasterize`/`points_in_path`), assemble의 80 %는 off-plane trace별 `copper_surface_impedance` 파이썬 루프. 가속 경로 `SPD_PI_FAST=1`(`common/fast_assemble.py`: 패턴 캐시·(σ,t)쌍별 Zs·weff 캐시, Y 비트 동일)로 assemble/f P18 2.15 → 0.13 s, 7케이스 벽시계 2.9–3.4배. 기본 경로 불변(smoke 5.83e-12, p-smoke 0.0). GPU 오차가 P7(N 5.9e5)에서 3.3e-8로 1e-8을 넘어 cuDSS 반복 정제 1단계를 기본으로 켬(+10 % solve 시간) → P7 1.8e-9, 804-P18 3.0e-10. **표준 실행 플래그: `SPD_PI_SOLVER=cudss SPD_PI_FAST=1`.** build 가속(batched_gx의 GPU 이식, 레이어 단위 래스터 캐시)은 §5 제안으로 기록만.
- 커밋 a62673c(2026-09-17): EXP-10..37 결과·연구 코드·GPU 경로·CLAUDE.md GPU 정책. 커밋 밖에 남긴 것(다른 작업 흐름): `docs/evaluation-research/D115C..D117*`, `docs/EVALUATION_SOLVER_*`(수정), `tests/test_*d117*`, `tools/research/*d116|d117*`, `accuracy_parse.py`, `handoff.local.md` — 소유자 판단.
- **EXP-38 완료(효율, 물리 불변)**: `results/exp38/`. `homog.batched_gx`에 (A) 동일 창 중복 제거(`SPD_PI_FAST=1`, G 비트 동일; P18 창 94 % 중복) + (B) CuPy 이식(`SPD_PI_SOLVER=cudss`, ΔG 1.6e-14, 배치 구성 불변이라 반복 횟수 동일). P18 균질화 152 → 3.0 s(51배), Port49 벽시계 4459 → 1476 s. 7케이스 ΔZ ≤ 5.6e-9. 계획 (b)의 build 합계 문턱은 미달(P18 441 s, Port49 22.8 분)인데 남은 build의 96 %가 참조 탐색 `model.rasterize`→`points_in_path`라서다 → EXP-39. 전역 Python에 `cupy-cuda12x[ctk] 14.2.0` 추가(numpy/scipy/cuda-bindings 불변). 주의: 이 세션에서 `points_in_path`가 EXP-37 대비 4배 느렸다(E-코어 스케줄 추정) — build 절대치 비교 시 같은 세션 대조를 쓴다.
- **EXP-39 완료(가설 기각, 변경은 정확 변환이라 유지)**: `results/exp39/`. (layer, net, h) 레이어 단위 래스터 캐시는 8케이스 7,144만 셀에서 차이 0(정확)이지만, P18에서 (layer, net, h) 중복을 전부 없애도 상한이 build의 3 %. 진짜 병목은 **정점 102,456개 평면 외곽 다각형 하나**에 대한 `matplotlib points_in_path`(O(점×정점), 점당 1 ms) — P18 rasterize 413 s 중 4회 호출이 338 s. Port49만 build −7.4 %(재사용 158회). 다음: EXP-40(규칙 격자용 스캔라인 점-포함 판정으로 matplotlib 규칙을 비트 재현).
- **EXP-40 완료(채택, 정확 변환)**: `results/exp40/`. `common/scanline.py`가 matplotlib `point_in_path_impl` 규칙(비엄격 `>=` straddle, 곱셈 형태 교차 비교, even-odd, 닫힘 변)을 그대로 옮긴 행 단위 판정으로 정점 ≥ 64 다각형을 평가(`SPD_PI_FAST=1`). 합성 240개·실제 8케이스 2.97억 셀 차이 0. 102,456-정점 다각형 h=50: 1910 s → 0.30 s. **같은 세션 단독 대조 P18 build 400 → 13.8 s, Port49 1129 → 70 s; 영수증 벽시계 P18 1095 → 33 s, Port7 2991 → 77 s, Port49 4459 → 155 s.** 이제 92포트 sweep은 수 분 규모. 남은 build 항목은 Port49의 PIL `raster_image` 18 s, `rasterize` 잔여 meshgrid, `batched_gx` CPU 잔여분(보고서 §5)이며 병목은 다시 풀이 쪽.
- **EXP-41 완료(held-out 260804 전 92포트, 기준선 p, 보고만)**: `results/exp41/`. G1/G2/G3/G4 PASS 2/5/14/0, err_1MHz 중앙값 27.7 %(260729 31.7 %), R 비 중앙값 1.47(260729 1.46), 포트별 err 상관 Spearman 0.949(예측 ≥ 0.7). G3 PASS 14는 예측 하한 15에 1건 미달, 나머지 예측 부합 → 두 리비전이 같은 R 결손 패턴을 보여 EXP-31 해석(패키지 급전 구조 관례 차이)이 강화됨. GPU 경로 92포트 22.7 분. 주의: `pipeline.prepare`의 공유 `shapes_{tag}.pkl`을 병렬 프로세스가 동시에 쓰고 읽어 `EOFError`(1/92)가 났고 재실행으로 해결 — 원자적 쓰기로 수정 예정. `dl17.py`는 `--tag` 미지원(260729 하드코딩).
- 커밋 대기(소유자 요청 시): EXP-38~41 변경(`exp3/homog.py`, `exp1/model.py`, `exp1/exp1b.py`, 신규 `common/scanline.py`, results/exp38..41, CLAUDE.md·인계 문서 갱신).
- 진행 정리 문서 `PROGRESS_SUMMARY_2026-09-18.md`(정량 비교·그래프) 추가. `pipeline.prepare`의 공유 pkl 쓰기는 원자적(os.replace + 재시도)으로 수정, 8×8 프로세스 스트레스 PASS.
- **2026-09-18 마감 — 연구 잠정 종료, 엔진 착수**: 소유자 결정으로 연구를 정리하고 계산 엔진 `src/spd_pi_engine/`(v0.1: W1–W5·W7 완료, 계획 `docs/engine/ENGINE_PLAN_2026-09-18.md`, 진입 문서 `src/spd_pi_engine/README.md`)을 만들었다. 수치는 연구 코드와 동일(영수증 게이트로 증명), 연구 트리는 무수정. 저장소 분리는 하지 않음(필요 시 `git subtree split`). 연구 브랜치는 GitHub에 push됨(main 병합은 소유자 판단).
- 2026-09-18 후반: 소유자 결정 E1(공개 유지)·E2(main 병합)·E3(PCB 저주파 재현 계약). 엔진 W8(`set_decaps`)·W9(Schur decap 스윕)·W10(다중 포트) 완료 → v0.1 전 항목 완료, W11(제품 통합)만 남음. 데이터 발견: SPD SITE0/SITE1 포트는 같은 레일이 아니라 별개 net 사본(다중 포트 쌍 없음). 진입 문서 `src/spd_pi_engine/README.md`.
- 2026-09-18 마지막: 앱 시제품 2종(`apps/decap_search` 421사이트 8.4분·305/421 실장, `apps/site_decision` SITE 매칭 421/421·10/10 미실장 가능)과 엔진 W12(cuDSS 비결정성 확정 → E4 계약, 한 프로세스 기저+직접 검증, 앱용 API). 남은 것: W11 제품 통합(패키지 R 관례 해결 후), 앱을 새 API로 단순화(선택).
- 2026-09-19 소유자 지시: (1) microvia 구리 충전 가정(D9, 수치 변경 없음 — 모델은 이미 40 µm via를 충전 원기둥으로 계산), (2) 앱을 W12 API로 단순화해 검증, (3) 워크스테이션(Threadripper 32C/64T, 512 GB, RTX A6000) 대응 — 엔진 W13(하드웨어 프로파일·자동 병렬/청크 산정) 진행.
- 2026-09-19 완료: D9 반영, 앱 v2(결과 동일), W13 하드웨어 프로파일(워크스테이션 대응, 실측은 장비에서). 표준 실행은 `python -m spd_pi_engine sweep --jobs auto`.
- 2026-09-19 W11 제품 통합 완료(옵트인 프로파일, 기본값 불변). 엔진 계획 W1–W13 전부 완료. 다음 세션 진입점: `src/spd_pi_engine/README.md`, `docs/engine/ENGINE_PLAN_2026-09-18.md` 말미 상태.
- **2026-09-20 소유자 결정 D10 — G4 게이트 정정**: 엔진 영수증의 G4 f_res 항이 상수 1.585 MHz(exp3에 박힌 260729 Port18의 참조값) 대신 **케이스별 참조 f_res**와 비교한다 → `DECISIONS.md` D10, 구현·검증 `docs/engine/W14D_REPORT.md`, 재판정한 통과 수 `docs/engine/W14B_REPORT.md` §10-6(G4 0/27 → 2/27). 수치는 불변이다(`numerics_id` 27d81996… 그대로). **연구 트리와 `results/expN`의 G4 수치는 상수 규칙으로 계산된 역사 기록이며 재판정하지 않는다.**
