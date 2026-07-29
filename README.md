# SPD Decap PI Evaluator

> 프로그램: **SPD Decap PI Evaluator v0.5.0**
> 저장소: 기존 PI Calculator와 분리된 독립 프로그램
> 해석 경계: 선택한 PWR rail의 pre-design `Zii`; 최종 PowerSI/SIwave 검증을 대체하지 않음

완성된 Cadence PowerSI `.spd` 도면을 읽어 Top-side decap의 PWR NET assignment,
모델, enabled/disabled 상태를 바꾸면서 PI Evaluation 결과를 비교하는 Windows
desktop 프로그램이다. Stackup, MLO 크기 또는 plane을 새로 작성하는 기능과
Optimization Mode는 포함하지 않는다.

## 주요 기능

- 대용량 SPD를 memory-mapped 방식으로 읽고 원본 파일은 수정하지 않음
- SPD의 Top layer 도면, PWR plane, decap 위치와 REFDES 표시
- 왼쪽 클릭 선택, 드래그 다중 선택, 오른쪽 context menu 편집
- 마우스 wheel 확대/축소, `Shift+drag` 도면 이동
- REFDES 또는 PWR NET 문자열 검색·선택
- PWR NET별 사용자 지정 색상
- decap PWR NET, component/model, REFDES, footprint, enabled 상태 hover 표시
- disabled decap을 회색 marker와 빨간 `X`로 표시
- 선택 decap의 PWR NET/model assignment 변경 및 enabled/disabled 전환
- 실제 power pad 수직 아래에 대응 PWR/DGND plane pair가 있을 때만 assignment 허용
- assign 가능한 rail은 SPD `.NetList PowerNets`에 명시된 net으로 제한
- 별도 passive two-terminal SPICE decap model 추가
- 여러 PWR NET을 선택해 한 번에 순차 Evaluation
- 최초 Original decap 구성과 모델 binding을 불변 baseline으로 캡처
- 최초 실행 시 Original과 Tuned를 함께 해석하고 Original 결과를 `.spdpi`에 자동 저장
- 이후 실행은 hash 검증된 Original 결과를 재사용하고 Tuned 결과와 비교
- 하나의 impedance plot에 모든 PWR NET의 Original(파선), Tuned(실선), Target(점선)을 중첩 표시
- plot은 모든 PWR NET을 기본 표시하고 체크박스로 채널별 표시를 전환하며, 점선 X/Y marker와 곡선 교차점 bubble로 주파수·임피던스 값을 확인
- 결과 plot을 더블클릭하면 확대 plot과 비교 table을 함께 제공하는 별도 창 표시
- Evaluation PWR NET 선택 목록에 도면 색상과 동기화된 color box 표시
- 비교 표에서 decap 수, 1 MHz/10 MHz/100 MHz 임피던스, target violation의 Original/Tuned 변화 표시
- Selection, Evaluation, AI Assist의 내부 section 높이를 splitter handle로 조절
- 선택한 Tuned PWR NET 한 개를 명시적으로 분석하는 evidence-grounded Local AI Plot Analyst
- 원본 SPD를 포함하지 않는 hash 검증 `.spdpi` scenario 저장/재열기

## 의도적으로 제외한 기능

- Stackup 또는 MLO 크기 신규 입력
- Plane 생성·편집
- Optimization Mode
- 원본 SPD 수정 또는 덮어쓰기
- inter-rail/site transfer coupling 및 DC IR drop
- AI가 수치 solver 결과나 design state를 직접 변경하는 기능

## 독립성

이 저장소는 `probe-card-mlo-pdn`을 runtime dependency로 설치하거나 참조하지 않는다.
필요한 SPD import와 Evaluation foundation은 `spd_decap_pi._core`에 내부 snapshot으로
포함되어 있다. 기존 프로그램의 CLI/GUI entry point는 포함하지 않으며 설치되는
프로그램은 `SPD Decap PI Evaluator` 하나뿐이다. 세부 경계는
[`docs/CORE_EXTRACTION.md`](docs/CORE_EXTRACTION.md)에 기록한다.

## 개발 실행

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
spd-decap-pi-evaluator
```

## Windows 빌드

```powershell
powershell.exe -ExecutionPolicy Bypass -NoProfile -File .\scripts\build_spd_decap_pi.ps1
powershell.exe -ExecutionPolicy Bypass -NoProfile -File .\scripts\build_spd_decap_pi_installer.ps1
```

산출물:

- `dist\SPDDecapPIEvaluator\SPDDecapPIEvaluator.exe`
- `installer-output\SPDDecapPIEvaluatorSetup-0.5.0.exe`

프로그램명과 버전은 title bar와 installer metadata에 함께 표시된다.
