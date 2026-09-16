# CLAUDE.md — SPD Decap PI Evaluator

## 목적
- PowerSI SPD에서 decap 배치와 PDN 임피던스 Z(f)(1 kHz–100 MHz)를 평가하는 데스크톱 도구다(`src/spd_decap_pi`, v0.23.1).
- 현재 연구 목표는 PowerSI Touchstone과 맞는 경량 2-D plane-pair + 회로 하이브리드 모델이다. 3-D Astra 경로는 폐기했다.
- 제품 코드와 연구 코드는 분리되어 있다. 제품 코드는 `src/`, `tests/`에 있고, 연구 코드는 `tools/research-claude/expN/`에 있다.

## 현재 연구 상태
- **새 세션은 먼저 `docs/research-claude/2026-09-15/NEXT_SESSION_HANDOFF.md`를 읽는다.** 환경, 데이터 지도, 실행 절차, 과제 우선순위, 첫 프롬프트가 들어 있다.
- 근거 문서
  - `RESEARCH_LOG.md`: 전체 기록.
  - `DECISIONS.md`: D1–D6.
  - `MODEL_PHYSICS.md`: 식과 [구현 차이].
  - `CODEX_HANDOVER_RETROSPECTIVE.md`: 회고와 후속 과제.
  - `results/expN/`: 보고서와 JSON 영수증.
- 기준 수치: 260729 Port18의 1 MHz 오차 2.69%(EXP-8 cavity-wall). 새 작업 전에 이 값이 재현되는지 확인한다.

## 규칙
1. **튜닝 금지.** 참조(PowerSI) 결과를 보고 파라미터를 맞추지 않는다. 가설, 변경점, 판정 기준은 실행 전에 문서로 고정한다. held-out 설계(260804, s5m6585)는 규칙을 동결한 뒤에만 연다.
2. **영수증.** 모든 실험은 결과 JSON을 남긴다. JSON에는 입력, 파라미터, freq/Z/Zref, 게이트, stats, wall, 메모리가 들어간다. 여기에 `EXPn_REPORT.md`를 더한다. 기존 결과는 덮어쓰지 않고 새 실험 폴더(`exp10`…)를 쓴다.
3. **게이트 G1–G5 고정**(D3).
   - G1: 1–100 kHz에서 |ΔRe| ≤ 0.05 mΩ.
   - G2: 0.1–1 MHz에서 ΔL ±5 pH.
   - G3: 1 MHz 오차 < 10%.
   - G4: 1–10 MHz 오차 < 20%, f_res ±10%, f0 병행.
   - G5: 10–100 MHz는 보고만 한다.
   - 결과를 본 뒤 게이트를 바꾸지 않는다.
4. 동결 모델(`exp8/run8.py` → `exp5/pipeline.ModelB` → `exp4` → `exp3/model3`)의 기본 동작은 바꾸지 않는다. 변형은 플래그나 서브클래스로 추가한다.
5. 연구 코드는 `src/`를 import만 한다. 제품 코드 변경은 별도 작업으로 명시적 요청이 있을 때만 한다.
6. **수정 금지**: `docs/handoff/`(스냅샷), `accuracy_parse.py`, 원본 SPD/Touchstone/참조 npz.
7. 경로를 하드코딩하지 않는다. `tools/research-claude/common/paths.py`의 `spd_path`, `ref_npz`, `work_file`을 쓴다.
8. 커밋과 push는 소유자가 요청할 때만 한다.

## 실행 (Windows PowerShell, 저장소 루트)
```powershell
.\.venv\Scripts\Activate.ps1                     # py -3.12 -m venv .venv; pip install -e .; pip install matplotlib psutil
$env:SPD_PI_DATA_DIR = "D:\Downloads\examples"   # SPD, 참조 npz는 DATA_DIR 또는 DATA_DIR\analysis
$env:SPD_PI_WORK_DIR = "D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8 = "1"
python tools\research-claude\common\paths.py        # 경로 점검
python tools\research-claude\common\smoke_port18.py # 재현 게이트(약 3분, 1.3 GB) → SMOKE PASS
python tools\research-claude\common\run_chain.py --list   # .sh 체인의 크로스플랫폼 대체
```
- 제품 테스트는 `pytest tests`로 돌린다. 연구 스크립트는 대부분 해당 `expN` 폴더에서 `python <script> --help`로 사용법을 확인한다.
- 모델 크기는 레일당 미지수 3만–55만, 메모리 ≤ 2.1 GB다. `--gnd s3` 진단은 3.3 GB 이상이다.
