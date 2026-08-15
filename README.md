# SPD Decap PI Evaluator v0.22.0

> **Ongoing Evaluation algorithm research:** start every research session from the [accuracy-first research restart document](docs/evaluation-research/README.md). Isolated research-only fixtures, runners, tests, and evidence may exist on the research branch, but the product parser, solver, UI, version, and installer remain unchanged unless implementation is explicitly authorized.

> **Current AV-BS1 H4-P0R-P1 boundary:** four public P1 invocations stopped before claim and factor work, and all four one-use tokens were retired without reuse. The fourth invocation used token-only commit `9b4854d0cc7ae21e5e9eafc394a9474cb53ea689`, reached outer/control ready and start release, then failed closed when PID `40224` was identity-bound as exited with the expected birth while the first complete Toolhelp snapshot still contained it. Its close SHA-256 is `73073432faa0525f560b6536ea2a5fd54329ba3a30f04ab05ab8cef59460e758`; the exact token remained unclaimed but was semantically spent and removed by retirement commit `f1aeeac018a96cbd82341db36efbf5e5a9a55431`. The token-absent retry-v4 candidate changes only explicit outer/control `MaximumAttempts>1` sampling: an exited, positive, same-birth descendant that remains in the initial snapshot receives at most two 25 ms rechecks, three complete snapshots total. Final absence emits the existing confirmed-disappearance event; `not_found`/87 plus presence, default-one factor sampling, PID reuse, root/query/access failures remain fatal. Frozen SHA-256 bindings are Python `95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234`, runner `7893cd57fd4b1686addd434fa0103d9f3b0e0087f4783f2c82e459661abfb645`, and tests `18aabcdf7b32c6b013aec65497d31f4d908f3085f53f64cb3791f38a2e79381d`; focused `11/11` and full no-cache `322/322` passed. No H4-P0R factorization, RHS, solve, H4 physics, PowerSI, or 8 GiB result is claimed; `next_stage_authorized=false`. See the [results](docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_RESULTS.md) and [P1 preregistration](docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md).

> **v0.22.0 implements the source-derived multilayer layer-surface network and binds its release decision to both named SPD/PowerSI cases.** Exact adjacent-gap artwork Maxwell-Y blocks share physical `(layer, NET)` surface nodes, exact same-NET Trace/Via components provide only source-observed connectivity, and all internal interfaces are eliminated by one global sparse Schur/Kron solve. Touchstone remains comparison-only. Distribution reports `Assignment Failed`, and the main board can switch between Source SPD and Current cap assignments at their fixed physical XY. The title bar identifies the application as **SPD Decap PI Evaluator v0.22.0**.

> 프로그램: **SPD Decap PI Evaluator v0.22.0**
> 저장소: 기존 PI Calculator와 분리된 독립 프로그램
> 해석 경계: 선택한 PWR rail의 pre-design `Zii`; 최종 PowerSI/SIwave 검증을 대체하지 않음

완성된 Cadence PowerSI `.spd` 도면을 읽어 Top-side decap의 PWR NET assignment,
모델, enabled/disabled 상태를 바꾸면서 PI Evaluation 결과를 비교하는 Windows
desktop 프로그램이다. Stackup, MLO 크기 또는 plane을 새로 작성하는 기능과
Optimization Mode는 포함하지 않는다.

## v0.22.0 multilayer Evaluation

- The desktop default is **Layer-surface terminal-complete network**. Each retained physical `(layer, NET)` surface remains independent until exact raw-SPD same-NET Trace/Via topology proves a connection.
- Every adjacent dielectric gap contributes its exact ordered-artwork complex Maxwell-Y block. The blocks and source-observed vertical topology are embedded in one sparse network, then all internal interfaces are eliminated together by an open-port Schur/Kron solve.
- A branched topology component contributes no invented serial R/L. Device and decap loop R/L remain separately owned external branches. Gap-isolated retained surfaces may carry certified topology, but the evaluator does not synthesize missing capacitance or fringing for them.
- The terminal-complete global-Y result at the external Device port is the **sole** Layerwise driving-point input. The legacy rectangular modal matrix is not prepared, and no aggregate higher-mode one-port difference is added. This remains a source-derived circuit model, not a full-wave S-parameter solver.
- Frequency refinement uses the versioned three-iteration/64-point bounded policy. The shared Balanced/m-index field remains in run provenance for schema compatibility, but it does not stamp rectangular modes into a terminal-complete Layerwise result. Legacy and Research retain their documented modal-order behavior; PowerSI never selects the order.
- PowerSI Touchstone files are never read while building or fitting the model. They are used only by the validation benchmark. The two named 92-port cases, including all ten no-decap `VQPS` controls and six loaded rails per case, are recorded in the [v0.22.0 validation record](docs/EVALUATION_LAYER_SURFACE_VALIDATION_2026-08-06.md).
- Evaluation after De-cap Distribution shares the same evidence/compiler path. Unchanged source DIRECT assignments can use their immutable source template, while moved assignments still require exact destination eligibility and remain fail-closed.
- The Distribution grid reports receiver shortfall as `Assignment Failed`; the board toolbar switches between `Current / distributed` and `Source SPD (read-only)` assignments at fixed physical XY without changing the scenario.
- **Legacy modal** remains an explicit rollback/regression profile. There is no silent fallback when layer-surface source evidence is incomplete.

## v0.21.0 Evaluation / Distribution integration (historical)

- Evaluation resolves a missing DIRECT eligibility only for the immutable source rail/net and only from the imported rail-template binding. Exact eligibility takes precedence; redistributed assignments without destination proof still block preflight and build.
- The comparison preflight runs in the background and applies the same build-time connectivity/modelability contract to both **Original** and **Tuned/current**. A rail is runnable only when both sides build. If a selection mixes runnable and blocked rails, an explicit confirmation (default `No`) offers to run only the clear rails; the summary remains `PARTIAL` and marks every omitted rail `NOT evaluated`.
- The source-DIRECT fallback removed all 470 missing-eligibility blockers in the captured Distribution replay's connectivity-only check. It is not a geometry bypass: a fresh 260804 import still has 2,010 exact finite-port footprint blockers on 68 of 92 rails (1,232 GND and 778 PWR). Coordinates are never clamped, the cavity is never expanded, and blocked ports are never dropped.
- Compact exact route recovery found 11,874 of 117,810 requested source paths in the fresh 260804 import; 105,936 paths retained the disclosed legacy-template fallback. The same exact geometry preflight still reported 2,010 blockers, so route recovery is evidence preservation, not permission to treat an off-cavity terminal as modelable.
- The authoritative fresh 260804 active-checkout Distribution replay completed `NEAREST` as `FULL`: 696/696 receiver assignments, shortfall 0, 696 moves and 208 isolation sacrifices. Measured stages were scenario load 5.569 s, targets 2.340 s, proof projection plus validation 53.167 s, planner 157.501 s and atomic Apply 8.345 s; route metadata remained 11,874/117,810 recovered.
- After that Apply, the two-sided Original/Tuned comparison preflight reported 2,807 blockers on 73 rails and 19 clear rails: the fresh-source 2,010 blockers remained common, while redistribution added 797 Tuned-only blockers. Of the total, 2,802 were geometry and 5 connectivity blockers. This is intentionally different from the fresh-source 2,010 blockers on 68 rails; all 10 VQPS controls remained clear. The v0.21 optimized all-rail preflight rerun completed in 10.083 s and preserved the 2,010-blocker/68-rail source manifest; v0.22 replaces that legacy rectangular geometry gate with exact retained-surface binding only for the Layerwise profile.
- The Distribution grid reports unfulfilled receiver demand as `Assignment Failed`. Requested targets remain visible after Apply so partial results stay auditable.
- A main-board `Show source SPD assignments` control switches the board between `Current / distributed` and `Source SPD (read-only)` at the same physical XY. Search, selection and viewport are preserved, and source view blocks editing.
- PowerSI comparison utilities accept both exact legacy `2nd_SITE#-...` and exact run-qualified `SITE#_<run>-...` headers while continuing to reject site/rail mismatches. Touchstone remains comparison-only and is never used for fitting.
- All 10 no-decap `VQPS` rails are clear in exact preflight and remain bare-PDN controls. Their measured correlation still exposes a systematic accuracy limitation, so this release does not silently promote an experimental numerical solver. See the [v0.21.0 validation record](docs/EVALUATION_DISTRIBUTION_VALIDATION_2026-08-06.md).

## v0.20.0 De-cap Distribution correction

- Distribution uses the actual source-classified PWR Via landing, never the decap center or a lateral Trace/path endpoint, and strictly tests that immutable XY against the target NET's final ordered copper artwork.
- A target may be on any retained PWR conductor layer. GND-side Via/layer evidence is not a destination gate because this workflow changes only the PWR assignment and leaves all plane artwork unchanged.
- Eligibility is a disclosed placement-planning result: `VIA STACK CHANGE REQUIRED` means a filled-Cu microvia stack must be retargeted/rebuilt at that landing. It is not an as-built connectivity claim and the application does not edit the SPD Via or plane artwork.
- Any one real PWR landing can anchor an active PWR component. Dummy caps still cannot create their own root and may move only through the existing shared-pad anchor, separator, shared-Via, and isolation-gap rules.
- Exact destination permissions replace stale Evaluation-derived alternatives. Missing/malformed artwork, voids, and boundary-only contact remain fail-closed.
- The table-adjacent status is limited to per-model Donor/Receiver/Balance; detailed validation, import, planning-assumption, partial-result, and stale-result messages are shown in `Distribution Status / Preview Log`.
- File loading and Distribution calculation remain worker-threaded. v0.20.0 removes the abandoned existing-column recovery pass, so this correction does not add another full Node/Via scan to raw-SPD loading.

## v0.18.1 evaluation solver and loading status (historical)

- The 2026-08-04 [evaluation-solver deep-research decision record](docs/EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md) documents the 92-port PowerSI evidence, actual-artwork capacitance experiments, layer-network composition, matrix-free residual go/no-go criteria, selected sparse-MNA architecture, validation gates, and staged implementation order. It is a research record only; no experimental solver is production-enabled by that document.
- The companion [evaluation-solver implementation plan](docs/EVALUATION_SOLVER_IMPLEMENTATION_PLAN_2026-08-04.md) and the [v0.18.1 implementation status](docs/EVALUATION_SOLVER_IMPLEMENTATION_STATUS_2026-08-04.md) distinguish shipped guarded infrastructure from research prototypes. The legacy modal backend remains the default production path.
- v0.18.1 loading hardening reduced the final guarded, named 1.116 GB raw-SPD import from 591.6 s to 272.5 s (53.9%) and observed peak private memory from about 4.7 GiB to 2.42 GiB. The actual `.spdpi` app-equivalent path, including source SHA verification and prepared-view construction, loaded 11,050 decaps and 92 rails in 26.304 s with design fingerprint prefix `2fe31c68a0e7`. Separate component measurements were 6.09 s for scenario validation and about 8.03 s for bundle decode/validation; they are not complete app-load times. Exact parser audit counts were retained, the resource guard fails closed, and this loading work does not change the solver/research status.
- The explicit research profile replaces only the source-side uniform `C00` contribution; it does not add a parallel capacitance term and it leaves non-uniform modal terms unchanged. It requires a SHA-bound topology certificate and independent PWR/DGND port-connectivity evidence. If that proof is absent, evaluation stops with an actionable readiness error and never falls back silently or emits a partial curve.
- Research Original/Tuned curves are transient: Original is recomputed for every run and is neither persisted to nor reused from the scenario baseline cache. Legacy retains its existing `Saved now`/cache-reuse behavior.
- The named source SPD with SHA-256 prefix `40cb44b2376f` currently lacks the required topology certificate. Consequently the research option is intentionally blocked for that source and no accuracy-improvement claim is made for it.
- The source-only uniform `C00` scaffold above is the only shipped research profile. Separate global MNA, MFDM, PEEC, and associated artwork/via prototypes were investigated but excluded from the v0.18.1 prerelease. PowerSI Touchstone remains comparison-only and is never used for parameter fitting. v0.18.1 remains a prerelease until the documented accuracy-promotion gates, including end-to-end and blinded holdout evidence, are passed.
- The default remains **Balanced** (max index 8, 81 modes). **Experimental m12 check** keeps the existing max index 12 / 169-mode API for a comparison-only m10-to-m12 check. In the 2026-07-29 loaded benchmark, VTRIP1 changed by up to 1.346 dB in maximum magnitude from m10 to m12, 3 of 6 loaded configurations still did not converge, and solver runtime was 4,139 s. More modes worsened external correlation in that case, but this does not justify selecting a lower order; PowerSI remains comparison-only.
- The evaluator can combine only the immediately adjacent, opposite-side conductor when it contains configured GND aliases and valid dielectric rows. It uses the disclosed shared-PWR ideal-common-reference equivalent; it does not enable a general layer cascade. A true layer cascade is a full complex multiport Y-matrix Schur/Kron reduction, not scalar-Z or scalar-admittance merging. See [Evaluation accuracy](docs/EVALUATION_ACCURACY.md).
- A mixed PWR/DGND return layer is accepted only through a versioned certificate tied to SHA-256-verified exact plane artwork. The certificate requires at least 90% PWR-area overlap with the configured DGND artwork and a 99% dominant overlap component; it is fail-closed if geometry verification is unavailable. Result confidence remains **LOW** and displays the overlap evidence plus the continuous rectangular-return approximation.
- When a source terminal reaches the selected plane through a same-net Trace, or an alternate Trace-to-Via exit exists, evaluation uses the complete legacy terminal template instead of adding an incomplete source-Via R/L contribution. Compact node indexing avoids repeated full-SPD Node scans during this recovery.

## v0.12 raw-SPD terminal provenance

- The load status and tooltip show `Source Via paths: recovered/requested; fallback count`. A zero-recovery import explicitly says that no source segment R/L was applied and legacy rail templates were used.
- A recovered terminal must be one unique, monotonic, same-NET vertical Via chain from the TOP landing to the selected PWR or GND plane. The selected-plane pad shape comes from the final Via segment's padstack, not a Node feature label.
- Recovered non-sampled terminals use full circular copper area only when source `Material=COPPER` and the MLO microvia geometry is qualified (drill <=150 um, exactly two conductor layers with one dielectric, dielectric/drill <=1). The recorded `USER_CONFIRMED_MLO_COPPER_FILL_ASSUMPTION...` provenance is a user-confirmed fabrication assumption, not SPD proof of copper fill. All other source segments retain the 5.959e7 S/m `min(20 um, drill/4)` barrel estimate. Mutual Via, anti-pad, spreading, and lateral Trace inductance are not inferred.
- Missing, branching, overshooting, Trace-required, or unsupported-rotation paths do not infer a source path and explicitly fall back to the legacy rail template. A source-changed path aborts import so mixed-source evidence is never persisted. Sampled differential templates retain their calibrated symmetric terminal representation.
- V5 preserves separate source PWR and GND components for evaluation. Distribution remains PWR-topology based and accepts compatible V4/V5 saved analyses; evaluation still blocks an unresolved terminal component.

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
- Evaluation 전 Original과 Tuned/current를 동일한 builder-time connectivity/modelability 기준으로 검사하고, clear/blocked rail이 섞이면 명시적 확인 후 clear rail만 실행; 결과에는 blocked rail을 `NOT evaluated`로 남기고 `PARTIAL`임을 표시
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
- Distribution은 Evaluation에 선택된 단일 pair와 무관하게 source SPD에 보존된 모든 target PWR layer의 ordered copper를 동일 PWR VIA landing XY에서 검사한다. GND layer는 destination gate가 아니며, 판정은 기존 VIA 깊이 증명이 아닌 filled-Cu microvia-stack retarget/rebuild 계획 가정임을 UI에 명시한다.
- 현재치와 목표치가 같은 PWR NET도 `Tolerance (%)`가 양수이면 최종 수량을 유지한 채 `floor(현재 수량 × tolerance / 100)`개까지 주고받는 교환 경로로 참여; 0%이면 기존처럼 연산에서 제외
- Distribution의 Target/Tolerance 셀은 캐시된 수량으로 즉시 검증하며, `Ctrl`/`Shift`로 같은 종류의 셀을 여러 개 선택한 뒤 숫자를 한 번 입력해 동일 값으로 일괄 변경
- Distribution 후보를 수신 PWR NET bump에서 가까운 순서 또는 먼 순서로 선택하고, 물리 제약으로 목표에 미달해도 가능한 변경과 `Assignment Failed`·`Isolation Gaps`·shortfall을 표시
- shared-pad 대형 문제에서 수량·이동 assignment를 먼저 고정한 뒤 separator pad를 재최적화하고, 원자적 topology 검증을 통과한 불필요 gap을 복원하여 서로 다른 NET 경계에 실제로 필요한 isolation gap만 남김
- 이전 Distribution Excel의 절대 `Target`·`Tolerance (%)`를 `Import Targets...`로 재사용하며, `Present`는 현재 SPD에서 즉시 다시 계산하고 기록되지 않은 후보 순서는 사용자가 명시적으로 선택
- Distribution 표를 더블클릭하면 비모달 분리창을 열고, 계산 전후 Target XLSX를 내보내거나 엄격히 검증해 다시 가져오며, 메인 도면과 분리창에서 `Current / distributed`와 `Source SPD (read-only)` assignment/isolation-gap 상태를 번갈아 확인
- Distribution 결과의 전체 Decap을 `Component`, `REFDES`, `Before NET`, `After NET`, `X`, `Y` 열 CSV 또는 Excel로 내보내며, 희생 cell은 `UNUSED (ISOLATION GAP)`으로 기록하고 Excel의 두 번째 sheet에는 계산 당시 `PWR NET Distribution Targets` 표와 input inventory reconciliation을 보존
- 새 Distribution Excel은 source SPD SHA-256, design fingerprint, revision, 후보 순서와 프로그램 버전을 두 번째 sheet에 함께 기록하며, 변경 대상 rail의 기존 unresolved connection은 해석 차단 경고로 별도 표시
- 원본 source TOP copper 경로가 없는 구형 V2 scenario에서는 Distribution을 fail-closed로 차단하고 원본 SPD 재열기를 안내하며, 변경된 배치는 별도 `.spdpi`로 저장
- De-cap Distribution의 수량·PWR plane/VIA·shared-pad/dummy·isolation-gap·부분 충족·Apply·입출력 규칙은 [`docs/DECAP_DISTRIBUTION_RULES.md`](docs/DECAP_DISTRIBUTION_RULES.md)에 명시하고, 지정된 실파일 검증 결과는 [`docs/DECAP_DISTRIBUTION_VALIDATION_2026-08-06.md`](docs/DECAP_DISTRIBUTION_VALIDATION_2026-08-06.md)에 기록
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
- `installer-output\SPDDecapPIEvaluatorSetup-0.22.0.exe`
- `installer-output\SPDDecapPIEvaluatorSetup-0.22.0.exe.sha256`

프로그램명과 버전은 title bar와 installer metadata에 함께 표시된다.
