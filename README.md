# SPD Decap PI Evaluator

> 프로그램: **SPD Decap PI Evaluator v0.10.0**
> 저장소: 기존 PI Calculator와 분리된 독립 프로그램
> 해석 경계: 선택한 PWR rail의 pre-design `Zii`; 최종 PowerSI/SIwave 검증을 대체하지 않음

완성된 Cadence PowerSI `.spd` 도면을 읽어 Top-side decap의 PWR NET assignment,
모델, enabled/disabled 상태를 바꾸면서 PI Evaluation 결과를 비교하는 Windows
desktop 프로그램이다. Stackup, MLO 크기 또는 plane을 새로 작성하는 기능과
Optimization Mode는 포함하지 않는다.

## 주요 기능

- 대용량 SPD를 memory-mapped 방식으로 읽고 원본 파일은 수정하지 않음
- SPD의 Top layer 도면, PWR plane, decap 및 Device bump 위치 표시
- `T`, `P1`, `P2`… 체크박스로 PWR plane layer를 단독 또는 복수 중첩 표시
- 왼쪽 클릭 단일 선택, `Ctrl+클릭` toggle 다중 선택, 드래그 다중 선택, 오른쪽 context menu 편집
- 마우스 wheel 확대/축소, `Shift+drag` 도면 이동
- REFDES 또는 PWR NET 문자열 검색·선택
- PWR NET별 사용자 지정 색상; Evaluation에서 선택하지 않은 NET 도면·decap·bump는 회색으로 강조 완화
- decap PWR NET, component/model, REFDES, footprint, enabled 상태 hover 표시
- Device bump는 NET별 색상으로 표시하고 hover에는 NET 이름만 표시
- disabled decap을 회색 marker와 빨간 `X`로 표시
- 선택 decap의 PWR NET/model assignment 변경 및 enabled/disabled 전환
- short pad 클러스터의 일부 NET을 변경할 때 원본 TOP copper가 입증한 경계 cell을 `Isolation Gap`으로 함께 제거하여 서로 다른 NET의 pad를 물리적으로 분리
- 공여 수량은 실제 이동된 cap과 경계 분리에 희생된 cap을 모두 차감하고, 수신 수량에는 실제 이동된 cap만 가산
- VIA가 없는 dummy decap이 단독 구간으로 고립되는 변경, 한 physical PWR VIA를 서로 다른 NET 구간이 공유하는 변경, gap 없이 서로 다른 활성 NET이 맞닿는 변경은 차단
- shared-pad 해석은 복수 decap·복수 PWR VIA를 각 derived PWR 구간에 반영하고 원본 클러스터의 복수 GND VIA는 하나의 공통 GND supernode에 중복 없이 반영
- 실제 power pad 수직 아래에 대응 PWR/DGND plane pair가 있을 때만 assignment 허용
- assign 가능한 rail은 SPD `.NetList PowerNets`에 명시된 net으로 제한
- 별도 passive two-terminal SPICE decap model 추가
- 여러 PWR NET을 선택해 한 번에 순차 Evaluation
- 최초 Original decap 구성과 모델 binding을 불변 baseline으로 캡처
- 최초 실행 시 Original과 Tuned를 함께 해석하고 Original 결과를 `.spdpi`에 자동 저장
- 이후 실행은 hash 검증된 Original 결과를 재사용하고 Tuned 결과와 비교
- 하나의 impedance plot에 모든 PWR NET의 Original(파선), Tuned(실선), Target(점선)을 중첩 표시
- plot은 모든 PWR NET을 기본 표시하고 체크박스로 채널별 표시를 전환하며, 점선 X/Y marker와 곡선 교차점 bubble로 주파수·임피던스 값을 확인
- `Open Result Plot` 버튼으로 확대 plot과 비교 table을 함께 제공하는 별도 창 표시
- `Export Tuned CSV...` 버튼으로 평가한 PWR NET의 활성 Decap을 `Component`, `REFDES`, `NET Name` 열로 출력
- Evaluation PWR NET 선택 목록에 도면 색상과 동기화된 color box를 표시하고 우클릭으로 색상 변경
- 비교 표에서 decap 수, 1 MHz/10 MHz/100 MHz 임피던스, target violation의 Original/Tuned 변화 표시
- De-cap Distribution 표에서 PWR NET·Component별 현재 수량과 목표 수량을 지정하고, 수치 공급량을 사전 검수한 뒤 실제 PWR plane·bump·shared-pad 조건을 만족하는 최대 수량을 자동 재배정
- 현재치와 목표치가 같은 PWR NET도 `Tolerance (%)`가 양수이면 최종 수량을 유지한 채 `floor(현재 수량 × tolerance / 100)`개까지 주고받는 교환 경로로 참여; 0%이면 기존처럼 연산에서 제외
- Distribution의 Target/Tolerance 셀은 캐시된 수량으로 즉시 검증하며, `Ctrl`/`Shift`로 같은 종류의 셀을 여러 개 선택한 뒤 숫자를 한 번 입력해 동일 값으로 일괄 변경
- Distribution 후보를 수신 PWR NET bump에서 가까운 순서 또는 먼 순서로 선택하고, 물리 제약으로 목표에 미달해도 가능한 변경과 `Actual Δ`·`Actual Changed`·`Isolation Gaps`·shortfall을 표시
- shared-pad 대형 문제에서 수량·이동 assignment를 먼저 고정한 뒤 separator pad를 재최적화하고, 원자적 topology 검증을 통과한 불필요 gap을 복원하여 서로 다른 NET 경계에 실제로 필요한 isolation gap만 남김
- 이전 Distribution Excel의 절대 `Target`·`Tolerance (%)`를 `Import Targets...`로 재사용하며, `Present`는 현재 SPD에서 즉시 다시 계산하고 기록되지 않은 후보 순서는 사용자가 명시적으로 선택
- Distribution 결과의 전체 Decap을 `Component`, `REFDES`, `Before NET`, `After NET`, `X`, `Y` 열 CSV 또는 Excel로 내보내며, 희생 cell은 `UNUSED (ISOLATION GAP)`으로 기록하고 Excel의 두 번째 sheet에는 계산 당시 `PWR NET Distribution Targets` 표와 input inventory reconciliation을 보존
- 새 Distribution Excel은 source SPD SHA-256, design fingerprint, revision, 후보 순서와 프로그램 버전을 두 번째 sheet에 함께 기록하며, 변경 대상 rail의 기존 unresolved connection은 해석 차단 경고로 별도 표시
- 원본 source TOP copper 경로가 없는 구형 V2 scenario에서는 Distribution을 fail-closed로 차단하고 원본 SPD 재열기를 안내하며, 변경된 배치는 별도 `.spdpi`로 저장
- De-cap Distribution의 수량·PWR plane/VIA·shared-pad/dummy·isolation-gap·부분 충족·Apply·입출력 규칙은 [`docs/DECAP_DISTRIBUTION_RULES.md`](docs/DECAP_DISTRIBUTION_RULES.md)에 명시
- Selection, Evaluation, AI Assist, De-cap Distribution의 내부 section 높이를 선명한 가로 splitter bar로 조절
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
- `installer-output\SPDDecapPIEvaluatorSetup-0.10.0.exe`
- `installer-output\SPDDecapPIEvaluatorSetup-0.10.0.exe.sha256`

프로그램명과 버전은 title bar와 installer metadata에 함께 표시된다.
