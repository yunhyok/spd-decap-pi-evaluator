# Evaluation Algorithm Research Session Log

이 문서는 append-only 연구 일지다. 이미 기록한 세션의 결과를 지우지 않고, 잘못된 판단은 후속 항목에서 정정한다.

## Session entry template

```text
Date/time and agent/model roles:
Starting objective and inherited state:
Input identities and environment:
Read-only inspections/experiments:
Results and raw evidence:
Accepted decisions:
Rejected/deferred alternatives:
Failures/anomalies:
Files changed:
Validation performed:
Exact next starting point:
```

---

## 2026-08-14 — Research initialization and reference triage

### Starting objective

PowerSI-correlated Evaluation PI 계산의 정확성을 최우선으로 개선하고, 그 뒤 i9-12900H/8 GB 노트북에서 실행 가능한 시간·메모리 구조를 연구한다. 이번 세션은 코드 구현 없이 연구 목적, 기준자료 계약, 후보 알고리즘, 세션 지속성 절차를 확정하는 것이 목적이었다.

### Repository and preservation boundary

- 사용자가 제시한 cwd `C:\Users\User\Documents\PI Calculator`는 다른 repository였다.
- 실제 대상은 `C:\Users\User\Documents\SPD Decap PI Evaluator`이며 remote는 `yunhyok/spd-decap-pi-evaluator`이다.
- 실제 checkout은 `codex/v0.23.0-layerwise-checkpoint`, HEAD `bb361687...`였고 사용자의 미완료 source/test 변경이 존재했다.
- 그 checkout을 수정하지 않고 `C:\Users\User\Documents\SPD Decap PI Evaluator-evaluation-research` worktree와 `codex/evaluation-algorithm-research` branch를 같은 HEAD에서 생성했다.
- 제품 코드, version, installer, raw data, GitHub remote는 변경하지 않았다.

### Agent allocation

- Sol, xhigh: 전체 연구 감독, 현행 수학 모델 재구성, 논문 탐색, 후보 순위, 정확성/성능 gate와 feasibility 판단
- Terra, high: 실제 implementation/test/report audit, numerical reliability와 8 GB 위험 검토
- Luna, high: 8개 대용량 파일의 read-only streaming inventory, header/grid/port/section count, parser memory 조사
- Root: repository/source/report 검증, reference-only 계산, 판단 통합, 장기 문서 작성

Sub-agent 결론 중 source/report와 일치하지 않는 내용은 채택하지 않는 정책을 적용했다.

### Data identity and streaming inspection

- 8개 파일의 존재, byte size와 SHA-256을 확인했다.
- 모든 Touchstone이 `# Hz S RI R 1`임을 확인했다.
- P1=160 ports/626 records/1 GHz, P2=4/826/2 GHz, P3/P4=92/626/2 GHz로 확인했다.
- P3/P4의 raw layer aggregate는 같지만 P3가 Node 98, Trace 76, Via 28개 더 많았다.
- P3/P4의 exact port label prefix `3.1_SITE...`/`3rd_SITE...`는 현재 implicit parser convention과 맞지 않았다.
- P3/P4의 generated-from basename에는 다운로드 suffix `(2)`가 없었다. 동일성은 미확정 anomaly로 기록했다.
- P1 full numeric curve는 reserved holdout 보존을 위해 열지 않았다.

### Current implementation and report audit

현행 production path는 adjacent-layer artwork overlap capacitance, via self-R/L, decap/terminal을 sparse global nodal network에 조립하고 frequency별 SuperLU factor를 재사용해 port RHS를 푼다. 다음 누락이 source/doc에 명시돼 있었다.

- secondary-layer lateral spreading과 general multiport field behavior
- trace lateral R/L
- via mutual/return environment
- pad/antipad field
- plane-sheet nonuniform R/L
- full-wave/inter-rail/site behavior

보존된 final correlation report에서 다음을 확인했다.

- 24,434.37 s, 약 6.79 h
- 780,565 reduced nodes, 1,691,081 finite-via links
- critical-band loaded magnitude RMS 16.743 dB, phase RMS 44.28°
- full-band loaded magnitude RMS 15.539 dB
- factor pivot ratio 1.2127e17, backward residual 1.04e-15

따라서 report-level `passed`는 정확성 promotion으로 해석할 수 없다. 작은 backward residual도 ill-conditioned system의 forward error를 보장하지 않는다.

Terra가 다음 focused test를 실행했고, root가 연구 worktree에서 같은 명령을 다시 실행해 현행 runner/parser/solver 회귀 상태를 독립 확인했다.

- correlation/Touchstone 관련: 109 passed
- layer-surface/global-MNA/profile 관련: 90 passed

테스트는 synthetic/mocked runner를 검증하지만 raw production pair의 end-to-end 재현을 제공하지 않는다는 한계도 확인했다. canonical validation 문서에 `TO_FILL` placeholder가 남아 있었다.

### Reference-only calculations

P2 S4P는 production reader/converter로 완전히 분석했다.

- max S→Z condition 1.75896
- max conversion residual 3.54e-16
- max Z reciprocity relative norm 1.67e-17
- four-port diagonal |Z|: 100 kHz 64.04–65.80 mΩ, 1 MHz 5.91–6.03 mΩ, 10 MHz 6.00–7.34 mΩ, 100 MHz 76.24–94.58 mΩ

P3/P4는 exact site와 suffix를 검증한 research-only header adapter로 reference-only 비교했다. production parser를 수정하거나 지원 상태로 선언하지 않았다.

- 두 file은 physical port order와 frequency grid가 동일하지만 numeric result는 다름
- non-VQPS 82 ports의 median |ΔdB|: 100 kHz 0.0010, 1 MHz 0.0037, 10 MHz 0.0055, 100 MHz 0.1000
- VQPS 10 ports의 median |ΔdB|: 7.097, 7.098, 7.144, 12.684
- 변화가 VQPS에 집중되어 controlled ΔZ benchmark 가치가 높음
- 서로 너무 유사해 independent holdout으로는 부적합

### Literature and vendor research

공식 Cadence 자료에서 PowerSI의 full-wave/autoadaptive mesh/signal-plane 범위와 3D EM option의 adaptive FEM, quasi-static/full-wave, model reduction, low-frequency conditioning, dispersive material/roughness 지원을 확인했다.

주요 논문군을 조사했다.

- triangular element 및 Delaunay–Voronoi plane method
- multilayer finite difference와 PDN domain decomposition
- local via/trace/pad physics 및 PEEC
- PRIMA/SPRIM passive model-order reduction
- recycled/hierarchical solve와 adaptive frequency/vector fitting

### Accepted conclusions

1. 주된 정확성 문제는 modal order가 아니라 model-form error다.
2. 1순위 구조는 source-faithful adaptive field domain + local transition replacement + passive global MNA다.
3. exact route-graph reduction은 첫 독립 최적화 후보다.
4. passive MOR/adaptive sampling은 physical accuracy freeze 뒤 적용한다.
5. P2는 small canonical development, P3/P4는 paired counterfactual, P1은 reserved transfer/scale holdout으로 사용한다.
6. 최종 일반화 주장은 algorithm/threshold 동결 뒤 fifth unseen PowerSI pair가 필요하다.
7. 현행 full-board direct SuperLU는 8 GB target에 부적합하다.

### Deferred or rejected as primary path

- legacy modal order만 높이기
- full-board dense PEEC/BEM
- uniform full-board fine-grid FFT/BEM
- full-board 3D FEM/FDTD를 제품 solver로 사용
- PowerSI curve에 per-design RLC/material parameter fitting
- sensitivity proof 없는 decap spatial grouping
- 현행 direct SuperLU factor를 laptop architecture로 유지

### Failures and anomalies

- P2 reference-only inline 분석의 첫 실행에서 `frequency_hz` 대신 dataclass field `frequencies_hz`를 사용해야 해 `AttributeError`가 발생했다. source field를 확인해 수정 후 성공했다. 데이터/제품 코드는 변경하지 않았다.
- P3/P4는 current header convention으로 직접 분석할 수 없어 research-only normalization이 필요했다. 이 결과는 production validation이 아니다.
- PowerSI version, mesh/convergence, material/roughness, de-embedding과 component state가 아직 없다.
- raw pair를 포함한 protected end-to-end regression job이 repository에 없다.

### Files changed

제품 code 변경 없음. 이 연구 worktree에 다음 문서만 추가했다.

- `docs/evaluation-research/README.md`
- `docs/evaluation-research/RESEARCH_STATE.md`
- `docs/evaluation-research/REFERENCE_DATASET.md`
- `docs/evaluation-research/ALGORITHM_CANDIDATES.md`
- `docs/evaluation-research/SESSION_LOG.md`
- root `README.md`의 research restart link

### Exact next starting point

`README.md`의 세션 시작 순서로 문서를 다시 읽은 뒤 다음을 수행한다.

1. PowerSI provenance와 P3/P4 component-state 동일성 확보
2. P2 multiport baseline report schema 사전 등록
3. P3/P4 streaming semantic diff로 perturbation 영역 식별
4. P2 현행 solver의 단계별 time/RSS/conditioning baseline
5. local transition canonical oracle/ablation matrix 확정

8 GB에서 full factor를 바로 시작하지 말고 memory preflight와 bounded 단계부터 수행한다.

---

## 2026-08-14 — Pair P2 bounded baseline, pair P3/P4 confound audit, local oracle preregistration

> **Supersession notice:** 직전 session의 “pair P2 small canonical development” 분류는 reference/external-port contract case로 교체한다. “pair P3/P4 controlled ΔZ benchmark” 분류도 철회하며, geometry/material/component/solver-state가 섞인 localization-hypothesis 자료로만 유지한다. 아래 후속 결론이 이전 provisional 판단보다 우선한다.

### Starting objective and preservation boundary

직전 계획의 R0/R1을 계속 수행하되 제품 코드는 수정하지 않았다. 연구 worktree `C:\Users\User\Documents\SPD Decap PI Evaluator-evaluation-research`, branch `codex/evaluation-algorithm-research`, starting HEAD `ab8550b81537a086ac63df8210b2c49f0455eb7f`가 clean임을 확인했다. 8개 external input의 path/bytes/mtime가 유지됨을 재확인했다. 실제 제품 checkout과 그곳의 사용자 source/test 변경은 건드리지 않았다.

### Agent allocation and root review

- Sol, xhigh: local physics oracle, exact-minus-core ownership, coupon/ablation matrix와 승격 gate
- Terra, high: P2 runner/port/import 경로, bounded baseline status/schema, fail-closed external-port 계약
- Luna, high: P3/P4 1.2 GB SPD streaming semantic diff와 component/material/shape state
- Root: raw/source 재검증, P2 실제 bounded import, full four-port reference/mode 계산, 판단 교정과 문서 통합

채택한 agent 결론:

- board curve fitting 전에 작은 canonical coupon에서 각 물리의 scaling/convergence/ownership을 증명한다.
- P2 bottom LGA를 기존 top editable-decaps나 `DEVICE_BUMP`로 억지 변환하지 않고 별도 explicit external-port 계약이 필요하다.
- P3/P4는 geometry 외 여러 state가 함께 달라 pure controlled geometry pair가 아니다.

Root 검증으로 교정하거나 기각한 결론:

- P2 import-save-only가 성공할 수 있다는 초기 가정은 실제 `SPD_NO_RAILS` 결과로 기각했다.
- Terra 초안 schema의 P2 Touchstone hash 한 글자 전사 오류를 canonical manifest의 `3DA7197159C94BFBEA725CC7C2F6D0F2AD1C5DAB8CF2747D030074EB4F180CC7`로 교정했다.
- Luna가 처음 보고한 10 µF model Connect 76개는 exact `rg` count에서 두 파일 모두 68개였다. P3의 전체 `Usage=0b111000` 76개에는 다른 model 8개가 포함된다.
- P4에만 full 10 µF model body가 있다는 사실은 채택했지만 그것이 VQPS `ΔZ`의 원인이라는 인과 주장은 기각했다. 두 파일의 68개 해당 Connect가 모두 `Usage=0b111000`이고 실제 PowerSI inclusion 의미가 아직 unknown이기 때문이다.

### PowerSI provenance recovered

Touchstone header에서 generator와 host를 확인했다.

- P1/P3/P4: Layout Workbench `25.1.0.09191.616638 000`
- P2: Layout Workbench `23.1.3.12171.472612 300`
- host: `WS202402-003W11`
- P1/P3/P4 SPD material path: SPB 22.1 database; P2: Sigrity 2023.1 database
- 네 Touchstone 모두 `# Hz S RI R 1`

P3/P4 SPD의 공통 PowerSI sweep은 0–2 GHz Adaptive이고 reference 1 Ω이지만 saved state는 동일하지 않았다. P4에만 max-edge, 3DEMCap/Ind directional buffer, dielectric buffer, 3DEMInd 1 kHz–1 GHz log sweep, 일부 PowerDC/Thermal mesh state가 있다. P3/P4 OuterBoxSize도 다르다. 실제 S92P export가 어느 saved block을 사용했는지는 미확정이다.

### Pair P2 reference-only baseline

Production Touchstone reader/S→Z converter로 전체 4×4 matrix를 분석했다.

- 826 records, positive 825, DC 1
- max `cond(I-S)` 1.7589626; max conversion residual 3.54e-16
- S reciprocity 0; max Z reciprocity relative norm 1.67e-17
- max S singular value 0.99991948; minimum `Hermitian(Z)` eigenvalue +1.554 mΩ
- diagonal minimum은 약 2.75–3.02 MHz에서 1.58–1.76 mΩ
- original-port maximum transfer `|Zij|`: 100 kHz 0.320 µΩ, 1 MHz 1.290 µΩ, 10 MHz 14.169 µΩ, 100 MHz 177.894 µΩ, 1 GHz 4.207 mΩ, 2 GHz 7.225 mΩ
- critical-band maximum normalized transfer coupling 0.283%; full-band maximum 6.91% at 1.775 GHz

NE/NW/SE/SW를 common, E-W, N-S, checkerboard orthonormal current pattern으로 변환한 `QᵀZQ` anchor도 계산해 `BASELINE_PROTOCOL.md`에 기록했다. high-band에서 이 고정 basis의 off-diagonal이 커지므로 네 port를 단순히 동일 independent scalar rail로 축약하지 않는다.

### Pair P2 actual bounded import attempt

현재 host는 63.68 GiB RAM이며 목표 8 GB 노트북이 아니다. `benchmark_raw_spd_powersi_correlation.py --import-save-only`를 P2에 적용했다.

첫 실행은 space가 있는 worktree output path가 `Start-Process -ArgumentList`에서 올바르게 quote되지 않아 argparse `unrecognized arguments`로 즉시 실패했다. 두 번째 실행은 space 없는 bounded output path로 진행했다.

- SPD analysis complete: 약 48.0 s
- plan 도달: 약 59.0 s
- 최종: `SPD_NO_RAILS: No rail could be formed from both a selected plane net and device power-pin coordinates.`
- frequency solve: 0
- candidate/report bundle: 생성되지 않음
- 관측 한 시점: working set 약 1,431 MiB, private 약 2,100 MiB

monitor command의 `.ToString('o')` quoting이 깨져 peak JSON을 만들지 못했다. 따라서 memory 수치는 peak가 아닌 하한 표본이며 8 GB 가능성의 증거가 아니다.

Source와 raw SPD를 따라 원인을 확인했다. P2 port component `L25P08085A7_LGA`는 `StartLayer=Signal$BOTTOM`, `AttachLayer=BottomAir`이고 Part에 `Tags=IO`가 없다. `_select_candidates`는 IO tag와 `TopAir`를 모두 요구하고, `_derive_rails`는 `DEVICE_BUMP` PWR pin만 사용한다. bottom node retention과 first-via recovery에도 TOP 전제가 있다. 이는 token parse failure가 아니라 명시적 product-scope mismatch다.

### Pair P3/P4 streaming semantic diff

대용량 file을 전체 materialize하지 않고 streaming했다. raw pass는 약 4.8–7.8 s, Node hash-bucketing은 합계 약 21 s, 관측 working set은 약 70 MB 이하였다.

- P3/P4 Node: 2,934,889 / 2,934,791; Trace: 1,451,285 / 1,451,209; Via: 1,956,937 / 1,956,909
- canonical Node body: P3→P4에서 133 removed, 35 added, net −98
- Node 변화는 대체로 x≈2.1–2.65 mm, y≈9.35–9.90 mm와 VQPS 관련 power/DGND net에 집중
- Shape name/count는 48개로 같지만 root가 다시 계산한 exact block hash는 41/48에서 다름
- `Signal$L21(DGND)` dielectric: P3 `EL190T`, P4 `ABF-GL102`
- `CAP_1608_10UF_NOT_MOUNTED`: P3 empty body, P4 Murata GRM188Z71A106KA73 full small-signal model
- 해당 model Connect는 두 파일 각각 68개이며 모두 `Usage=0b111000`
- 전체 Usage serialization: P3 `0b1000` 10,651 / `0b111000` 76; P4 123 / 68, 나머지 10,536 생략
- all-Connect `Checked=1` count는 10,727로 같지만 omitted Usage 의미와 terminal identity는 미확정

결론적으로 P3/P4는 `geometry + material + component-model/state + saved solver-state`의 multifactor paired-response 자료다. VQPS-localized response는 가설 생성에만 사용하고, pure `ΔZ` attribution은 동일 설정 재해석이나 factor-isolated coupon 뒤로 연기한다.

### Existing research-kernel audit and oracle plan

`tri_fem_*`, `mfdm*`, `surface_patch_plane`, `via_peec`, `pad_augmented_capacitance`, axisymmetric/edge correction, `global_mna`를 조사했다. analytic geometry, mesh refinement, reciprocity, passivity, KCL, PEEC current sharing, pad capacitance와 double-stamp guard가 이미 존재한다. 반면 pair/stack production eligibility는 false이고, via PEEC는 pad/antipad/return/plane을 포함하지 않으며, global ownership과 real-SPD adapter가 미인증이다.

새 solver kernel을 만드는 대신 다음을 사전 등록했다.

- N0 graph identity
- T1 finite trace
- S1 plane spreading/neck/void
- C1 finite-area port
- V1/V2 single/coupled via+return
- A1 pad/drill/antipad
- I1 integrated mounted transition
- D1 dielectric loss/dispersion
- K1 conductor skin/surface model

모든 local block은 같은 crop/interface/terminal/current/return/gauge에서 `Yglobal-core + Pᵀ(Yexact−Ycore)P`로 조립하고 owner ledger를 갖는다. 상세 gate와 비용은 `LOCAL_ORACLE_PLAN.md`에 동결했다.

### Failures and anomalies

- P2 benchmark script는 S92P와 legacy 16-rail/92-port manifest에 hard-coded되어 generic P2 correlation runner가 아니다.
- P2 import는 external-port 계약 부재로 fail-closed 되었다. dummy rail, `BottomAir` 한 줄 허용, legacy modal validator 사용은 기각했다.
- 첫 import command path quoting과 RSS monitor timestamp quoting이 실패했다. 제품/data 변경은 없었다.
- P3/P4 generated-from basename에는 downloaded `(2)` suffix가 없고, port header prefix도 서로 다르다.
- P3/P4 Usage omission, actual component enable state, solver block provenance가 미확정이다.

### Files changed

제품 code/version/installer/release 변경 없음. 연구 문서만 갱신했다.

- `docs/evaluation-research/README.md`
- `docs/evaluation-research/RESEARCH_STATE.md`
- `docs/evaluation-research/REFERENCE_DATASET.md`
- `docs/evaluation-research/ALGORITHM_CANDIDATES.md`
- `docs/evaluation-research/SESSION_LOG.md`
- new `docs/evaluation-research/BASELINE_PROTOCOL.md`
- new `docs/evaluation-research/LOCAL_ORACLE_PLAN.md`

### Validation and exact next starting point

Windows 10.0.19045, Python 3.12.10, pytest 9.0.3, research HEAD `ab8550b8...`에서 source 변경 없이 다음 12개 module을 함께 실행해 178 tests가 5.27 s에 통과했다.

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
pytest tests/test_tri_fem_gap.py tests/test_tri_fem_pair.py tests/test_tri_fem_sheet.py tests/test_tri_fem_stack.py tests/test_mfdm_solver.py tests/test_mfdm_adapter.py tests/test_surface_patch_plane.py tests/test_via_peec.py tests/test_pad_augmented_capacitance.py tests/test_research_axisymmetric_electrostatics.py tests/test_edge_cell_capacitance.py tests/test_global_mna.py -q
```

최종 Sol/Terra review에서 발견한 blocker도 문서에 반영했다.

- research phase를 `R0`–`R7`, dataset pair를 `P1`–`P4`로 분리했다.
- finite sheet port의 잘못된 zero-radius point-limit 표현을 제거하고 fixed physical footprint와 `ln(1/a)` scaling 검증으로 교체했다.
- external port 계약에 signed excitation/projection, terminal footprint/current weight, reference mode/plane, de-embedding, renormalization을 추가했다.
- exact-minus-core에 identical DtN/Schur boundary operator, cross-boundary mutual-term ownership, interface power와 `ΔYΓ` crop convergence를 요구했다.
- convergence floor/norm, dissipativity tolerance, KCL/solve/conditioning, broadband causality/positive-real gate를 수치로 정의했다.
- exact graph reduction의 scalar-branch admissibility와 operator 변경 후 parity 재인증을 명시했다.
- single-block correlation의 단조 개선 요구를 철회하고 error cancellation을 고려한 factorial/integrated holdout 판정으로 바꿨다.
- 8 GB gate를 process-tree commit/working set, mapped residency, OS/system commit headroom까지 확장했다.

최종 Sol scientific review와 Terra factual audit는 모두 `APPROVED`였다. 7개 research Markdown의 relative link와 trailing whitespace 검사를 통과했고 `git diff --check`도 오류 없이 통과했다. 표시된 LF→CRLF 메시지는 repository의 Windows line-ending warning이며 content 오류가 아니다. Git status는 아래에 기록한 research Markdown 7개 외의 변경이 없음을 확인했다.

다음 세션은 아래 순서로 시작한다.

1. N0 exact-reduction identity와 S1/V1/V2/A1/C1 canonical manifest/report를 코드 변경 없이 기존 research module로 실행한다.
2. 네 SPD의 trace width, dielectric point count, via plating/fill, antipad, roughness와 owner ID를 streaming parameter manifest로 만든다.
3. P2 explicit `ExternalPowerSiPort` manifest와 fail-closed fixture/test specification을 완성한다. 구현은 별도 승인 전까지 하지 않는다.
4. P3/P4의 PowerSI Usage/default 의미, enabled component state와 실제 export solver block을 확보한다.
5. repeatability와 mesh convergence가 있는 factor-isolated PowerSI coupon 요구사항을 작성한다.

P2 raw solve는 external-port 계약과 memory preflight가 승인되기 전 다시 실행하지 않는다. P1 full numerical curve는 후보 동결 전 계속 reserved 상태로 유지한다.

---

## 2026-08-14 — R2 oracle execution, source parameter manifest, P2 physical port contract

### Starting objective and preservation boundary

사용자가 중지를 지시할 때까지 기준 문서를 갱신하며 연구를 계속하라는 지시를 active goal로 유지했다. 연구 worktree `C:\Users\User\Documents\SPD Decap PI Evaluator-evaluation-research`, branch `codex/evaluation-algorithm-research`, starting HEAD `3411c5a9d7766198a8a8bacd618fc0615798ab8d`가 clean임을 확인했다. 제품 code, parser, version, installer, release와 실제 제품 checkout은 수정하지 않았다.

이번 cycle의 범위는 다음 세 가지였다.

1. 사전 등록한 N0/S1/V1/V2/A1/C1 oracle을 기존 research module로 실제 실행
2. 네 raw SPD의 source parameter와 결손을 bounded streaming manifest로 고정
3. P2의 BOTTOM multi-terminal external PowerSI port를 raw source에 묶는 명세 작성

### Agent allocation and root verification

- Sol xhigh: N0/S1/V1/V2/A1/C1 독립 실행과 preregistered gate 감사
- Luna high: P1–P4 trace/material/via/padstack/antipad/roughness streaming 조사
- Terra high: P2 raw port block, package terminal/direct-via reconstruction과 fail-closed contract
- Root: N0/V1/V2/A1 독립 계산, P2 6,435-node/direct-via 재검증, trace-width framing 재검증, source/test 검토와 문서 통합

root는 P2 port block을 별도 parser로 다시 읽어 각 port의 positive 52개, 공통 GND 6,227개와 order 동일성을 확인했다. 두 번째 streaming pass에서 unique 6,435 Node가 모두 `Signal$BOTTOM`, `TH_0D3CR0D5_Mir`이고 source-incident Via가 정확히 하나씩임을 확인했다. trace continuation을 독립적으로 다시 묶어 P1/P2 missing 0, P3/P4 missing 239,135/239,070을 재현했다.

### R2 oracle verdict

상세 수치는 새 [`R2_ORACLE_RESULTS.md`](R2_ORACLE_RESULTS.md)에 고정했다.

- **N0 pass, scalar-only:** frozen reproduction을 포함한 두 제조 예제에서 최대 relative Z error `5.34e-15`, 최대 absolute error `1.78e-14 Ω`, owner ledger 완전. production path에는 아직 미연결이며 mutual/multiterminal/topology change 때 재인증한다.
- **S1 blocked:** rectangular finite-contact sheet는 level 6→7 변화 0.299%까지 수렴했지만 전체 h/h/2/h/4/crop/corpus/ownership이 없다. circular annulus refinement는 `MESH_CROSSES_VOID`로 fail-closed 됐다.
- **V1 blocked:** solid-cylinder `Rdc`, internal/external L과 skin trend는 analytic limit를 통과했다. explicit coaxial return loop가 없다.
- **V2 blocked:** five-via P/G array에서 exact 1/3, −1/2 current sharing, 2 GHz condition 2.89, 약 1e-16 invariant residual을 확인했다. `global_mna_composable=False`, 3-D/exact-minus-core가 없다.
- **A1 blocked:** crop 300→600→1,200 µm는 약 1e-13 relative로 안정적이나 mesh 10→5, 5→2.5, 2.5→1.25 µm 변화는 11.97%, 10.52%, 4.734%다. current cell-centred formulation은 0.5%/1% gate에 미달했다.
- **C1 blocked:** circular-disk BEM scalar C의 mesh 변화와 exact footprint area는 양호했지만 required backward residual은 미보고이며 기존 relative residual은 약 1.6–1.74e-8이다. circular launch spreading Z, crop, `ln(1/a)`, point-core replacement ownership도 없다.

N0만 명시된 constitutive scope에서 통과했다. helper의 internal `production_eligible`나 test pass를 제품 accuracy promotion으로 확대하지 않았다.

### Source parameter manifest

새 [`SOURCE_PARAMETER_MANIFEST.md`](SOURCE_PARAMETER_MANIFEST.md)에 다음을 고정했다.

- P1/P2 trace width complete; P3/P4 약 16.5% raw width absent
- conductor thickness/material/conductivity와 trapezoid-angle coverage
- P1/P2 single-frequency dielectric와 P3/P4 ABF/EL190T frequency tables의 범위 차이
- 모든 Via의 node/padstack resolution, padstack drill/material 결손
- P1/P2 all Via-start와 net-qualified Via의 32/136 record 차이; no-net row를 `net=absent` owner로 보존
- P1/P2 raw anti geometry와 per-via `NoAntiPadLayers`; 현행 `_VIA_RE`가 후자를 보존하지 않는 parser gap
- P3/P4 anti shape와 per-via antipad attribute 모두 absent
- 네 pair 모두 plating/fill와 roughness data absent
- owner ID와 `explicit/absent/parser_not_preserved/derived_node_link` 상태 enum

결손 trace width, plating/fill, roughness와 antipad를 PowerSI curve fit 또는 인접 record default로 채우지 않기로 결정했다.

### P2 ExternalPowerSiPort physical contract

새 [`P2_EXTERNAL_PORT_SPEC.md`](P2_EXTERNAL_PORT_SPEC.md)에 S4P ordinal 1–4와 SPD Port31/38/45/52를 exact raw order로 묶었다.

- 각 positive set: 52 ordered terminals
- common GND set: 6,227 ordered terminals
- physical mapping: BOTTOM layer, one direct Via, common padstack
- full runtime record requirement와 ordered hash/bbox integrity checks
- component `Tags="IC"`, `BottomAir`; 기존 `DEVICE_BUMP`/top decap으로 coercion 금지
- file/header/terminal/direct-via mismatch의 fail-closed test specification

physical terminal reconstruction은 `passed`로 진전됐지만 ordered runtime record를 등록한 loadable full manifest는 아직 없다. excitation weighting, reference mode/plane, de-embedding, renormalization과 actual export solver branch도 unknown이다. 따라서 `p2_loadable_full_port_manifest`, `p2_operator_semantics`와 numerical correlation은 계속 blocked다.

### Failures and anomalies

- 첫 annular S1 refinement는 circular void strict coverage 때문에 `MESH_CROSSES_VOID`로 종료됐다.
- 첫 V1/V2 inline Python probe는 `PYTHONPATH`가 없어 `ModuleNotFoundError`가 발생했고 `PYTHONPATH=src`로 바로잡아 성공했다. source 변경은 없었다.
- visualization preview의 첫 Playwright 실행은 bundled Chromium executable이 없어 실패했다. installed Edge를 explicit executable로 사용해 736 px/360 px layout과 A1 selection update를 검증했다.
- 단순 `rg -c NoAntiPadLayers`는 continuation/반복 line count이므로 Via-record count가 아니다. manifest는 continuation을 parent Via에 귀속한 streaming record count를 사용한다.

### Documents and visualization

제품 code 변경 없음. 다음 research 기준 문서를 추가했다.

- `docs/evaluation-research/R2_ORACLE_RESULTS.md`
- `docs/evaluation-research/ORACLE_REPRODUCTION.md`
- `docs/evaluation-research/SOURCE_PARAMETER_MANIFEST.md`
- `docs/evaluation-research/P2_EXTERNAL_PORT_SPEC.md`

README, RESEARCH_STATE, REFERENCE_DATASET, BASELINE_PROTOCOL, LOCAL_ORACLE_PLAN, ALGORITHM_CANDIDATES를 새 판정과 링크로 갱신했다. thread visualization directory에는 N0/S1/V1/V2/A1/C1 판정을 선택해 볼 수 있는 `oracle-gate-status.html`을 만들고 736 px/360 px에서 검증했다. visualization은 repository에 포함하지 않는다.

### Independent final audit

- Sol은 여섯 oracle 재현 block, residual 정의, scaled 1-norm estimate 해석, N0 scope와 V1/V2 다음 단계를 재실행·교차검증하고 `APPROVED`했다.
- Terra는 P2 ordinal/status 분리와 추가된 N0 analytic-chain fixture를 독립 실행해 `APPROVED`했다.
- Luna는 all/net-qualified Via count, `NoAntiPadLayers` parent aggregation과 trace framing timing을 다시 streaming evidence와 대조해 `APPROVED`했다.
- 세 감사 모두 제품 코드나 연구 문서를 수정하지 않았다. 최종 판단과 문서 통합은 root가 수행했다.

### Validation

Windows 10.0.19045, Python 3.12.10, pytest 9.0.3에서 다음을 확인했다.

```text
oracle reproduction appendix: 6/6 custom blocks passed; first five batch 66.9 s
focused FEM/MFDM/PEEC/MNA suite: 178 passed in 6.59 s
finite-route + FFT-BEM suite: 22 passed in 1.86 s
research Markdown files: 11; UTF-8/relative links/trailing whitespace docs_ok
```

두 pytest suite는 wall time을 줄이기 위해 병렬 실행했으므로 위 timing은 성능 benchmark가 아니라 회귀 상태 확인값이다.

`git diff --check`와 final clean-scope 검사는 session commit 직전에 다시 수행한다.

### Exact next starting point

1. T1 finite trace R/L coupon을 source-derived width/thickness/conductivity와 explicit return geometry로 실행한다.
2. V1/V2 explicit coax/ring-return 2-D/3-D reference와 same-crop exact/core owner·DtN matrix를 정의한다.
3. S1 circular/void boundary-conforming refinement와 A1 body-fitted/higher-order axisymmetric 후보를 비교한다.
4. C1 finite circular launch spreading Z, radius scaling, crop와 point-core replacement coupon을 정의한다.
5. P2 PowerSI port current weighting/reference plane/de-embedding/export branch 증거 요구사항을 완성한다.
6. P3/P4 component enabled state와 export solver-state confound를 계속 조사한다.
7. factor-isolated PowerSI coupon과 repeatability/mesh-convergence reference를 정의한다.

P1 full numerical curve는 계속 reserved 상태로 유지한다. 제품 구현과 P2 solve는 별도 승인 전까지 실행하지 않는다.

---

## 2026-08-14 — T1 finite-trace canonical oracle와 distributed/global composition contract

### Starting objective and inherited state

사용자가 중지를 지시할 때까지 정확성 우선 연구와 기준 문서 갱신을 계속하라는 active goal을 유지했다. 연구 worktree는 `C:\Users\User\Documents\SPD Decap PI Evaluator-evaluation-research`, branch `codex/evaluation-algorithm-research`, starting HEAD `84d92331b0dc8dd246dc8df74afe7183584a0c7a`였다. 제품 code, parser, solver, UI, version, installer, release와 실제 제품 checkout은 수정하지 않았다.

이번 cycle은 사전 등록한 T1 finite trace의 다음 항목을 분리해서 판정했다.

1. lossless cross-section canonical identity
2. smooth-copper finite-thickness periodic identity
3. source Trace/return provenance와 계산 후보 screening
4. finite-length distributed terminal operator
5. current GlobalMNA의 absolute-node composition 가능성
6. 8 GB 노트북을 위한 향후 cross-section cache/merge 구조

### Agent allocation and root verification

- Sol xhigh: T1 finite-width/broadband 알고리즘, analytic plate identity, exact distributed line, GlobalMNA composition과 exact-minus-core owner 감사
- Luna high: 네 SPD의 Trace width/endpoints/ref/stack/material streaming 분류와 source-derived candidate 증거
- Terra high: body-fitted Cohn stripline 독립 구현·재실행, sparse residual/RSS 감사
- Root: M0 exact 수치와 finite-length coefficient error 독립 재계산, lifted differential operator의 GlobalMNA expected failure 재현, source/test 검토, 문서·visualization 통합

### Source Trace와 return-evidence verdict

strict metadata screen을 `width explicit + same-layer endpoints + at least one concrete UpperRef/LowerRef`로 정의했다. `N/A`와 field absent는 concrete ref가 아니다. 이 screen은 return connectivity pass가 아니다.

| pair | total Trace | width explicit | concrete-ref screen | blocked | metadata profiles |
|---|---:|---:|---:|---:|---:|
| P1 | 12,544 | 12,544 | 6,816 | 5,728 | 8 |
| P2 | 15,052 | 15,052 | 2,976 | 12,076 | 57 |
| P3 | 1,451,285 | 1,212,150 | 0 | 1,451,285 | 97 |
| P4 | 1,451,209 | 1,212,139 | 0 | 1,451,209 | 88 |

P1/P2의 raw Trace ref는 `trace_record_explicit_connectivity_unproved`, P2 inner와 P3/P4의 인접 GND stack은 `derived_stackup_only`로 분리했다. P3/P4 raw Trace grammar에는 routed route와 plane/mesh topology를 구분하는 semantic flag가 없고 약 39%는 net 이름이 conductor-layer token과 일치하므로 `mixed_or_undetermined`로 차단했다. layer row의 numeric conductivity token은 absent지만 explicit Material link를 usable `.MetalModel`에 연결하는 경우 `resolved_sigma=derived_material_table`로 기록했다.

### T1-E0 body-fitted Cohn oracle

ground `y=±h`, zero-thickness strip `y=0, |x|≤w/2`, homogeneous Neumann lateral boundary를 사용했다. strip edge를 exact grid node로 포함하고 padding은 strip edge부터 `8h`로 정의했다. `h=100 µm`, `h/64`와 `h/128`, width `120/500/914.4 µm`를 Cohn exact 식과 비교했다.

| width | exact `C'` | h/64 | h/128 | Richardson | Richardson relative error |
|---:|---:|---:|---:|---:|---:|
| 120 µm | 36.8128510 pF/m | 37.0112662 | 36.9119306 | 36.8125951 | `−6.951e-6` |
| 500 µm | 104.1702700 pF/m | 104.3664877 | 104.2682374 | 104.1699871 | `−2.716e-6` |
| 914.4 µm | 177.5537791 pF/m | 177.7499747 | 177.6517423 | 177.5535098 | `−1.516e-6` |

raw h/128 exact error 최대는 `0.2691442%`(`≤0.2692%`), first-order Richardson 최대는 0.00070%였다. 별도 h/128 crop sweep의 padding 4h→8h 변화 최대는 `2.12459e-6`였다. sparse matrix symmetry는 exact zero, energy/charge mismatch 최대 `1.50e-12`, preregistered-form backward residual 최대 `4.13e-19`였다. exact appendix의 sequential solve를 한 process에서 측정한 peak working set은 약 `1,856 MiB`; 앞선 독립 run은 약 `1,852 MiB`였고 둘 다 개별 solve나 production benchmark가 아닌 allocator/high-water 참고값이다. 판정은 `T1-E0 passed_canonical_lossless_only`다.

### T1-M0 periodic smooth-copper identity

length 10 mm, width 5 mm, thickness 35 µm, face gap 50 µm, `σ=59.6 MS/m`의 periodic two-plate coupon을 exact slab `coth` law로 계산했다.

- `Rdc=1.917545542 mΩ`
- `Ldc=0.184306769 nH`
- high-frequency external `L=0.125663706 nH`
- 100 MHz: `R=10.294225474 mΩ`, `L=0.142048851 nH`
- 2 GHz: `R=46.039619707 mΩ`, `L=0.129327423 nH`
- 100 MHz→2 GHz successive resistance log slope: `0.500032982 → 0.500000000`

판정은 `T1-M0 passed_analytic_identity_only`다. exact slab law는 through-thickness broadside redistribution/proximity를 포함하지만 finite-width lateral edge/proximity current crowding, arbitrary return contour, finite end 또는 board correlation을 승인하지 않는다. smooth-copper skin/proximity는 0–2 GHz T1 자체에 포함하고 source에 없는 roughness는 별도 blocker로 유지한다.

### Selected oracle와 independent references

finite-width 2-D broadband normative oracle 후보는 Patel–Triverio의 surface-admittance operator + contour-integral method(SAO–CIM)로 정했다. finite thickness, skin, proximity, edge current crowding과 coupled return contour를 한 번에 풀어 p.u.l. complex `Z'(f)` matrix를 만든다. independent small-case reference는 skin-depth graded 2-D volume-current A-phi FEM, finite end/bend reference는 3-D PEEC/FastHenry다. Hammerstad/Jensen류 식은 screening/asymptotic anchor로만 사용한다.

2 GHz, 59.6 MS/m copper skin depth는 약 1.458 µm이므로 volume mesh보다 boundary method가 작은 반복 cross-section에 유리하다. 그러나 문헌 timing은 이 노트북 성능 증거가 아니며 실제 panel convergence와 peak RSS를 별도로 측정한다.

### Finite-length exact line과 lumped screen

scalar line은 `z'(f), y'(f)`에서 exact distributed terminal admittance를 만들고, DC/small argument에서는 exact-π 형태의 stable series를 사용한다. 일반 multiconductor는 `H=[[0,−Z'],[−Y',0]]`의 scaling-and-squaring matrix exponential과 block solve를 사용하며 `Z'Y'` eigenvector continuity를 가정하지 않는다. `[v0;i0]→[vl;il]`과 양 끝 inward current convention에서 `Y00=−B⁻¹A`, `Y01=B⁻¹`, `Y10=−C+DB⁻¹A`, `Y11=−DB⁻¹`이며 inverse를 만들지 않고 solve한다. coupled `γk=sqrt(eigenvalue(Z'Y'))` screen은 nonnormal system에서 necessary condition일 뿐 full-matrix gate가 authoritative하다.

homogeneous `εr=4`, 2 GHz에서 0.889/0.900/4.2/10 mm의 `|βl|`은 `0.07453/0.07545/0.35210/0.83834`다. exact terminal coefficient 대비 nominal-π 최대 error는 `0.0927/0.0950/2.120/13.975%`, series-only는 `0.1856/0.1902/4.348/32.633%`다. 따라서 exact line을 기본으로 하고, lumped 후보는 `max modal |γl|≤0.1`을 necessary screen으로 만족한 뒤 exact terminal/full-port 0.5%/1%/0.25° gate도 통과해야 한다. exact adapter 전 fallback π section 수는 최소 `ceil(θmax/0.1)`이다.

P2 dielectric table은 material별 1 GHz 한 점뿐이므로 causal 0–2 GHz `G'/C'` law로 외삽하지 않는다. 1 GHz point sensitivity 또는 lossless-static manufactured screen으로만 사용한다.

### GlobalMNA expected failure와 owner contract

incidence `D=[[1,−1,0,0],[0,0,1,−1]]`로 reduced differential `Y2`를 `(S0,R0,S1,R1)`에 `Y4=DᵀY2D`로 lift했다. root와 Sol이 full-rank `Y2`인 1 GHz M0 operator에서 rank 2, global gauge `[1,1,1,1]`와 terminal-plane common mode `[1,1,−1,−1]` null을 독립 재현했다. DC pure-series case는 rank 1과 추가 null을 가지므로 rank 2를 broadband 일반 명제로 쓰지 않는다. compile은 통과하지만 solve는 다음으로 fail-closed 됐다.

```text
GlobalMnaError: saddle system is singular; topology has an unresolved island
```

이를 임의의 common-mode conductance로 숨기지 않는다. full physical partial/common-mode operator 또는 explicit balanced-projection/current-constraint adapter와 core return operator가 필요하다. ideal Trace union을 먼저 제거하고, exact/core가 같은 signal/return copper, terminal basis, signed currents, crop `Γ`와 DtN trace space를 공유해야 한다. retained core와 correction을 먼저 합쳐 하나의 passive replacement를 검증하며 일반적으로 indefinite인 raw `ΔY`를 현 MNA에 독립 passive block으로 stamp하지 않는다.

### Laptop-oriented architecture decision

정확성 gate 뒤의 우선 구조는 다음이다.

1. canonical cross-section key별 passive/causal `(Z',Y')` cache
2. 안전한 collinear degree-2 chain만 exact length로 merge
3. scalar exact line 또는 작은 coupled `expm` terminal block 평가
4. exact adapter가 없을 때만 `θsection≤0.1` passive π ladder
5. rational ROM은 dense withheld-frequency positive-real/causality 검증 뒤에만 추가

cache key에는 conductor/return contour와 role/reference basis, nearby conductors, material rows와 temperature, roughness status, `Γ`/DtN, mesh certificate, owner/source hashes를 포함한다. length는 반올림하지 않는다. bend/profile/return/material change, branch, pad/via/decap, mutable/measurement node와 crop boundary를 가로질러 merge하지 않는다.

### Failures and anomalies

- 첫 uniform-x Cohn probe는 strip edge가 grid node와 일치하지 않아 mesh parity error가 섞였다. body-fitted piecewise grid로 폐기·교체했다.
- 초기 crop 정의는 domain center 기준 폭과 strip-edge padding을 혼동했다. 최종 식은 strip edge부터 4h/8h를 재서 crop 변화를 다시 계산했다.
- lifted differential operator는 compile됐지만 solve에서 singular island로 종료됐다. 이는 수치 실패를 보정한 것이 아니라 필요한 common-mode 계약이 없음을 보여주는 expected fail-closed 결과다.
- Windows peak working set은 six table solves와 three h/128 crop solves를 한 process에서 순차 실행한 allocator high-water다. per-case memory 또는 제품 성능으로 해석하지 않는다.

### Files changed

제품 code 변경 없음. 새 기준 문서 [`T1_TRACE_ORACLE_RESULTS.md`](T1_TRACE_ORACLE_RESULTS.md)를 추가하고 다음 연구 문서를 갱신했다.

- `README.md`
- `RESEARCH_STATE.md`
- `LOCAL_ORACLE_PLAN.md`
- `ALGORITHM_CANDIDATES.md`
- `REFERENCE_DATASET.md`
- `SOURCE_PARAMETER_MANIFEST.md`
- `R2_ORACLE_RESULTS.md`
- `ORACLE_REPRODUCTION.md`
- `SESSION_LOG.md`

thread visualization `oracle-gate-status.html`에는 T1 tile과 E0/M0 partial-pass/global-blocked 설명을 추가했다. visualization은 repository에 포함하지 않는다.

### Validation performed

- MFDM surface-impedance, two-face owner, GlobalMNA fail-closed ownership/passivity, finite-route endpoint invariant focused suite: 최종 root 재실행 `7 passed in 0.77 s`
- `ORACLE_REPRODUCTION.md` 자체에서 T1 세 PowerShell/Python block을 추출해 root가 순서대로 실행: E0/M0 table과 lifted-line expected failure 전부 재현
- T1-M0 limits/frequency table/electrical-length coefficient error: root exact command 재현
- lifted differential GlobalMNA expected failure: frozen 1 GHz에서 rank 2, 두 null residual exact zero와 singular-island error 재현; DC rank 1 범위 별도 명시
- T1-E0 table six sparse solves: Terra 독립 run `wall≈29.9 s`; root의 최종 문서 추출 run은 같은 six table solves와 세 h/128 crop solves를 합쳐 `wall≈40.5 s`; one-process peak working set 약 `1,856 MiB`
- visualization T1 selection: 736 px와 360 px에서 확인; 360 px document overflow `328/328`

timing은 회귀·bounded feasibility 참고값이며 성능 benchmark가 아니다. 문서/link/diff와 독립 final audit은 이 entry의 후속 검증에서 완료한다.

### Independent final audit

- Luna는 P1–P4 count, candidate raw lines, return-evidence label, P3/P4 `mixed_or_undetermined`와 material-table conductivity 상태를 streaming evidence와 대조했다. `derived_material_table` 명칭 정정 뒤 `APPROVED`했다.
- Terra는 E0 body-fitted command를 독립 재실행하고 Cohn table, energy/charge, backward residual, sparse symmetry와 RSS 범위를 감사했다. appendix가 six 8h table solves와 three h/128 4h crop solves를 정확히 기술하고 8 GB production gate와 분리한 뒤 `APPROVED`했다.
- Sol은 M0 slab law 범위, exact distributed-line sign convention, nonnormal coupled screen, 1 GHz rank-2와 DC rank-1 nullspace 범위, crop/RSS 재현성을 감사했다. 모든 지적을 반영한 최종 문서를 `APPROVED`했다.
- 세 감사 모두 제품 code와 연구 문서를 직접 수정하지 않았다. root가 결과를 재현하고 문서에 통합했다.

최종 docs validation은 research Markdown 12개에서 strict UTF-8, trailing whitespace 0, broken relative link 0이었다. `git diff --check`와 scope 검사도 통과했고 변경 10개가 모두 `docs/evaluation-research/` 아래임을 확인했다.

### Exact next starting point

1. T1-M1의 finite-width one-return `w/h={5,10,20,50}`, return-width ratio `{1,5,20}`, crop `{2,4,8}`를 SAO–CIM과 A-phi FEM으로 비교할 실행 명세를 고정한다.
2. perimeter panel/corner grading과 volume skin mesh의 h/h2/h4 convergence, reciprocity/passivity/current/power gate를 실행한다.
3. P2 `Trace13305`는 source-derived manufactured asymmetric stripline으로만 실행하고 actual IN23/IN25 polygons/connectivity 전에는 source-faithful이라 부르지 않는다.
4. P1/P2 explicit-ref candidate의 actual return polygon, terminal-to-return continuity와 same-crop core/DtN owner를 streaming crop으로 증명한다.
5. finite-length 3-D reference는 동일 fixture의 `Z(2l)−Z(l)`로 end effect를 de-embed한다.
6. full partial/common-mode operator 또는 balanced-projection adapter가 없으면 global composition을 계속 차단한다.
7. T1 smooth-copper 0–2 GHz gate 전에는 roughness fitting이나 board-wide PowerSI correlation로 넘어가지 않는다.

---

## 2026-08-14 — T1-M1 실행 계약과 source return crop 고정

### 시작 목적과 변경 경계

사용자가 중지를 지시할 때까지 정확성 우선 연구와 기준 문서 갱신을 계속한다는 active goal을 유지했다. 연구 worktree는 `C:\Users\User\Documents\SPD Decap PI Evaluator-evaluation-research`, branch는 `codex/evaluation-algorithm-research`, 시작 HEAD는 `9a3027c`다. 이 cycle도 제품 parser/solver/UI/version/installer/release를 변경하지 않았고, raw reference 파일도 수정·복사하지 않았다.

이번 목표는 T1-M1을 곧바로 구현하는 것이 아니라 다음을 재현 가능한 실행 계약으로 고정하는 것이었다.

1. P1/P2 explicit Trace ref가 실제 return-current operator를 증명하는지 source crop으로 판정
2. homogeneous dense-pulse SAO–CIM의 부호, branch, self integral과 terminal reduction 고정
3. independent `A_z–v` volume-current FEM의 동일 current basis와 crop/energy gate 고정
4. M1 및 P2 manufactured fixture의 치수, DC anchor, quasi-TM 적용 범위와 8 GB resource stop rule 고정

### Agent 배치와 독립 감사

- Sol: Patel–Triverio complex SAO–CIM, finite-return equipotential reduction, branch/conditioning/power/circle-DtN 계약 감사
- Luna: 네 SPD의 bounded streaming parameter와 P1/P2 selected crop의 node/pad/shape/void/via line·byte provenance 감사
- Terra: independent A–v weak form, artificial boundary 의미와 8 GB process-tree resource 계약 감사
- Root: raw evidence 재확인, analytic screen 재실행, 문서·visualization 통합과 focused regression

### P1/P2 return crop 판정

새 [`T1_RETURN_CROP_MANIFEST.md`](T1_RETURN_CROP_MANIFEST.md)에 P1 `Trace4004/4005/4006`, P2 `Trace9054/9055/9056`와 inner `Trace13305`의 source owner를 고정했다.

- P1의 큰 positive `Plane$IN43_DGNDpkgshape`는 세 route projection을 포함하고, 주변에 explicit GND Trace/Via-to-plane graph가 있다. 그러나 raw SPD에는 signal Trace와 그 return-current/field owner를 연결하는 record가 없다.
- P2 `Trace9054/55/56`의 free endpoint는 각각 `Signal$IN01_GNDpkgshape` negative circle의 중심이다. 주변 GND pad/via graph는 존재하지만 signal return-current coupling을 증명하지 않는다.
- P2 `Trace13305`는 line 3,553,874, byte 341,810,617의 IN24 4.2 mm route이며 width 120 µm는 다음 continuation line/byte가 소유한다. IN23/IN25 positive artwork는 route를 포함하지만 nearby void와 finite-width edge의 nominal clearance는 약 0.2 µm로 near-tangent다. exact polygon boolean tolerance와 signal-to-return operator는 미증명이다.

따라서 공통 status는 `return_shape_geometry_present`, `return_net_graph_present`, `signal_to_return_operator_unproved`, `source_faithful_return_blocked`다. `UpperRef`/`LowerRef` 이름이나 가까운 GND via를 signed return-current owner로 승격하지 않는다.

### T1-M1 SAO–CIM 계약

새 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)의 1차 범위는 homogeneous, nonmagnetic, lossless background와 simply connected copper contour다. stratified/lossy background, semiconductor longitudinal current와 multiply connected contour는 각각 explicit blocker로 남겼다.

- `e^{jωt}`에서 passive copper의 `kp`는 `Re(kp)>0, Im(kp)<0` branch를 사용하고 Bessel 함수와 self-anchor의 complex log도 같은 analytic continuation을 쓴다.
- straight-pulse `U/P`, `Umm=1`, singular-subtracted `Pmm`, analytic exterior log self term을 고정했다.
- authoritative complex operator는 `X=solve(I-jωμb Ys G0,Ys Q)`, `Kc=QᵀWX`, `Z'_partial=solve(Kc,I)`다. arXiv v1에 누락된 `Ys`와 `Re/Im` 때문에 축약식을 그대로 구현하지 않는다.
- prescribed one-reference current는 `TᵀZ'_partial T`를 쓴다. tied multi-return은 conductor-to-group `Hg`와 balanced group-current `Bg`의 saddle system으로 equipotential voltage와 current split을 함께 풀며 50:50 split을 강제하지 않는다.
- `r0={0.1,1,10} m` invariance는 `τinv=max(1e-12,50 max κ1u)<=1e-8`로 측정한다.

M1 전에 solid circular copper 두 radius `17.5 µm/0.5 mm`, 일곱 frequency, Fourier mode `m=0…4`, `N=128/256/512`의 exact Bessel DtN eigenvalue를 mandatory gate로 추가했다. fine complex error `<=0.5%`, meaningful phase `<=0.25°`와 mesh convergence를 모두 통과해야 한다.

### Independent A–v와 measurable gate

`A_z–v` P1 FEM은 SAO와 같은 conductor order, equipotential group `Hg`, balanced current `Bg`를 사용한다. `a=0` outer boundary는 gauge reference이면서 artificial magnetic truncation이므로 `{2,4,8}Deff` crop과 `Wm(Ω8\Ω4)/Wm(Ω8)<0.1%` far-field shell gate가 필요하다. A–v는 conductor `R'+jωL'`만 소유하고 `C'/G'`를 함께 주장하지 않는다.

SAO dissipative power는 `Re(0.5 IᴴZI)`와 `Re(0.5 EᴴWJ)`의 normalized mismatch로 고정했다. condition certificate는 binary64 `u`, deterministic row/column max-norm equilibration과 1-norm inverse estimator를 사용해 `P/Pout/A/Kc/equipotential saddle/A–v saddle` 각각 `κ1u<=1e-8`을 요구한다. `Z'floor`를 Ω/m 단위로 정의해 meaningful element와 phase gate를 측정 가능하게 했다.

### M1/P2 analytic screening

canonical M1은 vacuum, `h=50 µm`, signal/return thickness 35 µm, copper 59.6 MS/m, `w/h={5,10,20,50}`, `Wr/w={1,5,20}`다. exact reproduction block을 다시 실행한 결과:

- 2 GHz copper skin depth `1.457746488493 µm`
- 12 geometry 중 8개가 `|kb|Deff<=0.3` 통과
- `(w/h,Wr/w)=(10,20),(20,20),(50,5),(50,20)`은 2 GHz에서 각각 `0.419169004/0.838338009/0.523961255/2.095845022`로 차단
- 각 low-frequency cutoff는 약 `1.4314 GHz/715.70 MHz/1.1451 GHz/286.28 MHz`

P2 manufactured fixture는 signal `120×17.5 µm`, length 4.2 mm, artificial top/bottom return `1.2 mm×17.5 µm`, face gap 75/104 µm다. DC signal `33.557046980 mΩ`, bundled return `1.677852349 mΩ`, loop `35.234899329 mΩ`; vacuum 2 GHz extent `0.050300281`을 재현했다. 이는 source-derived manufactured input이며 actual board return 또는 PowerSI correlation pass가 아니다.

### Resource와 상태

dense SAO 1차 ceiling은 total panel `N<=800`, A–v는 250,000 nodes/500,000 triangles다. preflight와 측정은 전체 process tree의 working set/private/committed/mapped residency/page faults, OS commit headroom과 available RAM을 포함한다. peak working set 4.0 GiB 목표, private/committed 5.0 GiB 절대 상한, system commit headroom 2.0 GiB 또는 available RAM 1.5 GiB 미만이면 새 단계를 시작하지 않고 안전 취소한다.

현재 status는 `T1-M1 specified_not_run`, `source_faithful_return_blocked`, `global_composition blocked_balanced_projection_and_return_partition`다. 정확성, PowerSI correlation 또는 8 GB production 성능 승격은 없다.

### Files changed

제품 code 변경 없음. 새 문서는 다음 둘이다.

- `T1_RETURN_CROP_MANIFEST.md`
- `T1_M1_REFERENCE_SPEC.md`

다음 기준 문서를 함께 갱신했다.

- `README.md`
- `RESEARCH_STATE.md`
- `LOCAL_ORACLE_PLAN.md`
- `ALGORITHM_CANDIDATES.md`
- `REFERENCE_DATASET.md`
- `T1_TRACE_ORACLE_RESULTS.md`
- `ORACLE_REPRODUCTION.md`
- `SESSION_LOG.md`

thread visualization `oracle-gate-status.html`의 T1 tile은 `E0/M0 통과, M1 specified, overall blocked`와 8/12 quasi-TM screen을 표시하도록 갱신했다. repository에는 포함하지 않는다.

### Validation

- `ORACLE_REPRODUCTION.md`의 T1-M1 analytic screen 및 equipotential saddle exact command 재실행: `np.block` tuple 예제 오류를 list-of-lists로 정정한 뒤 skin depth, 8/12 eligibility, P2 `Iabs=[1,-0.5,-0.5]`, `ZBg=35.234899328859 mΩ`, residual 0 재현
- 기존 physical owner/fail-closed invariant focused suite: `7 passed in 0.89 s`
- research Markdown 14개: strict UTF-8 error 0, trailing whitespace 0, broken relative link 0
- `git diff --check`: error 0; 변경 scope 전부 `docs/evaluation-research/`
- Luna source/crop audit: 최종 provenance와 geometry/semantic status `APPROVED`
- Terra A–v/resource audit: boundary와 8 GB stop contract `APPROVED`
- Sol 1차 수학 감사에서 multi-return reduction, branch, measurable gate와 circle exact 기준 누락을 발견했다. 이어 invariance 분모, `10×floor` phase eligibility와 P2 DC/AC equal-split 문구까지 정정했다. equipotential saddle의 `Iabs=[1,-0.5,-0.5]`, `ZBg=35.234899329 mΩ`, residual exact zero를 독립 재현한 뒤 최종 `APPROVED`했다.
- Terra 최종 cross-document audit에서 `Trace13305`의 과거 `stackup_only` 문구와 불완전한 board blocker를 발견했다. positive artwork/GND-via graph evidence와 미증명 signed operator를 분리하고 exact boolean/current-field owner/core-DtN blocker로 고친 뒤 최종 `APPROVED`했다.

### Exact next starting point

1. 제품 code가 아닌 bounded research prototype으로 circle DtN two-radius/seven-frequency/five-mode gate를 먼저 실행한다.
2. circle branch/self/conditioning이 통과한 뒤 M0 wide-plate `coth` limit를 회복한다.
3. eligible M1 subset에서 `N,2N,4N`, quadrature, `C0` overlap과 same-basis A–v `h,h/2,h/4`, crop `2/4/8Deff`를 비교한다.
4. symmetric two-return에서 equipotential saddle이 equal split을 결과로만 회복하고, P2 artificial asymmetric coupon에서는 split을 직접 푼다.
5. finite-length T1-F 3-D length-difference와 balanced/global adapter는 2-D oracle 통과 뒤에 진행한다.
6. actual return polygon의 signed operator와 same-crop core/DtN owner가 없으면 source-faithful/global status를 계속 차단한다.
7. layered/lossy background와 roughness는 homogeneous smooth-copper gate 전에는 추가하지 않는다.

## 2026-08-14 — T1 circular interior DtN gate와 `C0` 정책 판정

### 시작 목적과 변경 경계

직전 checkpoint에서 mandatory로 고정한 two-radius circular conductor DtN gate를 제품 코드 밖의 bounded prototype으로 실행했다. 목적은 M1 사각형·return exterior를 풀기 전에 다음을 분리 판정하는 것이었다.

1. exact Bessel DtN과 pulse-panel `U/P` interior operator의 sign, branch, Fourier mode와 self term
2. 사전 등록 `C0-A0` empirical switch와 새 후보 `C0-A1`의 정확도·수렴·conditioning
3. Hankel/Bessel dynamic range, underflow와 fail-closed contract
4. 8 GB 장비에서 작은 dense cross-section oracle을 순차 실행할 수 있는지의 resource preflight

제품 parser/solver/UI/version/installer는 수정하지 않았다. board/PowerSI curve를 후보 선택에 사용하지 않았고 raw SPD/Touchstone도 이 cycle에서 solver 입력으로 읽지 않았다.

### Agent 배치와 root 검증

- Sol: Patel–Triverio 식, C0 항등식, W3 independent withheld와 canonical condition/residual 감사
- Terra: W2 blind parameter set, promotion boundary와 상위 계약 일관성 감사
- Luna: SciPy/Bessel/Hankel 환경, 22,599-point range stress, dense memory/condition preflight 감사
- Root: A0/A1 canonical·W1/W2 실행, exact reproduction block 작성·재실행, 문서/visualization 통합

### Exact circle와 pulse convention

`e^{jωt}`, CCW contour, outward normal에서 analytic eigenvalue는 `Dm(k)=k/(jωμ)·Jm'(ka)/Jm(ka)`, `Ys,m=Dp,m−Db,m`로 고정했다. `kp`는 fourth quadrant, `kb`는 positive-real branch다. 같은 scale의 `jve` adjacent ratio는 direct `jvp/jv` safe points와 최대 `8.96e-16`, root-square residual `1.68e-16`으로 일치했다. 70 exact complex value serialization checksum은 `ed91cad8f9c481bcb0c7749ea05d75fd9d26ab33fde258b23c6a66c60bc623e0`이다.

regular N-chord circle은 `U[i,j]=u[(j−i) mod N]`으로 고정하고 `em,n=exp(+j2πmn/N)`, `Ûm=N·ifft(u)[m]`를 사용한다. `fft(first_row)[m]`은 반대 mode를 선택한다. full dense Rayleigh 등가는 모든 chord 길이가 같아 `W=ℓI`인 이 circle에만 적용하며 corner/nonuniform M1에 일반화하지 않는다.

### Frozen `C0-A0` 실패

사전 등록한 `C0=10^6 if Δ/δ<=0.5 else 1`을 immutable negative baseline으로 실행했다. `a=17.5 µm`, 100 kHz, mode 2에서:

- analytic `173.833308261−j0.052191983 S`
- N512 `173.564355046+j5.364668010 S`
- fine analytic error `3.11996%`
- N256→512 change `9.32346%`
- phase `1.78758°`
- equilibrated max `κ1u=8.9144e-8`

으로 정확도·mesh·phase·condition gate를 모두 실패했다. N128→4096 error sequence는 `49.36344/12.43344/3.11996/0.78145/0.19552/0.04891%`여서 linear solve 실패가 아니라 큰 `C0`가 straight-chord geometry error를 증폭한 2차 수렴 case다. 특히 `C0=10^6`에서 `|kℓ/2|<=1e-3`만 보고 leading complex-log self asymptotic을 쓰면 생략된 `C0 z²` 항이 작지 않다. frozen A0는 regularized self integral을 그대로 사용했다.

### Selected `C0-A1` circle candidate

positive frequency에서 conductor/background 모두 `C0=1`, direct/scaled `Hν^(2)`를 쓰고 DC를 분리하는 A1을 downstream Z/PowerSI 없이 선택했다. canonical `a={17.5,500} µm`, 7 frequencies, `m=0…4`, N128/256/512에서:

- N512 worst analytic error `0.118541%`
- N256→512 max change `0.158276%`
- phase `0.019939°`
- quadrature q10→20 `1.05602e-6` relative
- exact-circulant max `κ1u=9.753904e-13`
- per-column normwise-infinity dense backward residual `1.2030e-15`

로 통과했다. result serialization checksum은 `a2d61666e01884eb8560dc6fb35789be88cbaea9fa52f99d70964fdc20387390`이다. 판정은 `C0-A1 passed_circle_interior_only`이며 M0/M1/A–v/exterior/PowerSI/global/product 승격이 아니다.

### Withheld와 수치 범위

- W1: 24 geometry/frequency, 216 modal points 모두 통과. worst analytic/mesh/phase `0.111661%/0.230599%/0.028207°`; range-guard replay 네 dense spot의 per-column residual `9.5414e-16`, `κ1u=1.014945e-12`; original checksum `94411e3db852684dfd0554f344490412ffbda6034b9fde9d69b2cb34f167f472`.
- W2: 12 geometry/frequency, 72 signed modal points의 analytic/convergence-only corroboration. worst analytic/mesh/phase `0.152520%/0.383233%/0.012746°`; spectral `κ2u=6.116e-13`은 screening proxy다. full-dense P/Pout residual/`κ1u`가 없어 full-condition pass 근거에서 제외했다. `m↔−m=3.339e-10`은 정규화/단위가 보존되지 않아 gate evidence가 아니다.
- W3: Sol independent geometry를 range guard로 replay한 96 modal points 전부 통과. worst analytic/mesh/phase `0.113044%/0.150516%/0.028220°`; 24 dense case residual `1.1541e-15`, `κ1u=9.164853e-13`, dense/Fourier discrepancy `1.470e-11`. original unguarded 수치는 range certificate로 사용하지 않는다.

0.1 Hz–2 GHz, radius 1 µm–1 mm, order 0…8의 22,599 safe-overlap grid(`|Im z|<=685.99`)에서 direct/scaled reconstruction max relative difference는 `6.14e-14`였다. 별도 `z=y(1−j)` scan에서 SciPy/AMOS direct `hankel2`는 이 corpus의 `y≈693.9`부터 false exact zero를 반환했고 scaled path는 subnormal log limit 약 `−744.44`까지 유지됐다. 최종 W1/W3 replay는 `ln(tiny)+4` 아래 표본의 dropped contribution을 log-sum해 retained U/P row 1-norm의 `1e-30` 이하일 때만 zero로 뒀다. 40,448 dropped order-sample contribution의 worst bound는 `10^-305.50`이었다. 이 proof가 없으면 `BLOCKED_HANKEL_RANGE`다.

### Resource와 anomaly

N512 complex128 matrix 하나는 4.00 MiB다. 순차 U/P/LU/RHS prototype의 process-only peak working set은 W1 spot 약 `94.06 MiB`, W3 independent run 약 `137.83 MiB`; private bytes는 SciPy/BLAS reservation을 포함해 약 1.3 GiB였다. 이는 process-tree 또는 8 GB production 성능 승격이 아니다.

W1 inline run에서 `onenormest`가 near-zero complex sign에 overflow warning을 낸 case가 있었다. exact reproduction은 row-max→column-max equilibration 뒤 LU의 LAPACK `gecon`을 사용하며 explicit inverse를 만들지 않는다. sparse M1에 zero-safe estimator가 없으면 차단한다. 감사 과정에서 canonical `κ1u`와 W3 `κ1u`, 서로 다른 residual normalization이 섞인 문구를 발견해 exact-circulant condition과 per-column normwise-infinity residual로 다시 분리했다.

### 문서와 visualization

제품 code 변경 없음. 새 기준 문서 [`T1_CIRCLE_DTN_RESULTS.md`](T1_CIRCLE_DTN_RESULTS.md)를 추가하고 다음을 갱신했다.

- `README.md`
- `RESEARCH_STATE.md`
- `R2_ORACLE_RESULTS.md`
- `LOCAL_ORACLE_PLAN.md`
- `ALGORITHM_CANDIDATES.md`
- `T1_TRACE_ORACLE_RESULTS.md`
- `T1_M1_REFERENCE_SPEC.md`
- `ORACLE_REPRODUCTION.md`
- `SESSION_LOG.md`

thread visualization의 T1 tile은 `E0/M0/A1 circle interior 제한 통과, M1 specified, overall blocked`와 frozen A0 failure를 표시하도록 갱신했다. repository에는 포함하지 않는다.

### Validation과 독립 감사

- `ORACLE_REPRODUCTION.md`의 circle block을 Markdown에서 그대로 추출·실행: A0 failure/condition, A1 canonical spectral, exact-circulant condition, 14 canonical dense, 네 W1 dense와 24 W3 dense case를 range guard와 함께 재현. dropped contribution 40,448개, worst retained-row-relative log10 bound `-305.5016`
- Sol은 C0 항등식/W3를 독립 확인하고 canonical condition/residual 혼동과 Fourier orientation 표기 누락을 발견했다. exact `N·ifft` convention과 수치 provenance를 분리해 반영했다.
- Terra는 A0/A1 상위 계약 충돌, W2 full-dense certificate 부재와 Hankel stress range 문구를 발견했다. reference spec을 amendment하고 W2를 analytic/convergence-only로 낮췄다.
- Luna는 `hankel2e` sign, logabs, 22,599 stress 수치를 확인하고 `gecon` prior-run provenance와 process-only resource label을 요구해 반영했다.
- physical owner/fail-closed focused regression: `7 passed in 0.97 s`
- research Markdown 15개: strict UTF-8 error 0, trailing whitespace 0, odd fence 0, broken relative link 0
- Sol 최종 재감사는 A0 sequence/condition, A1 Fourier orientation·dense residual, dropped-row certificate와 promotion boundary를 모두 `APPROVED`했다.
- Terra 최종 재감사는 A0/A1 stdout, W1/W3 range proof, W2 analytic-only 범위와 A1-M0→M1→A–v 순서를 모두 `APPROVED`했다.
- Luna 최종 재감사는 scaled-Hankel convention, `ln(tiny)+4` materialization, `1e-30` dropped-row 기준, 40,448개 drop과 `-305.5016` bound를 모두 `APPROVED`했다.
- visualization은 Playwright의 실제 CSS viewport로 736×520과 360×640을 재렌더링했다. inner width/scroll width는 각각 `704/704`, `328/328`이고 두 화면 모두 7개 gate tile을 보존했다.
- checkpoint 직전 재검증: focused regression `7 passed in 0.85 s`, research Markdown 15개 error 0, `git diff --check` error 0, 변경 범위 전부 `docs/evaluation-research/`.

### Exact next starting point (superseded by the M0 boundary amendment below)

1. amended `C0-A1` policy로 M0 wide coextensive plate의 analytic `coth` operator를 pulse-panel SAO에서 회복한다.
2. raw U/P, interior/exterior split, signed complex power, panel N/2N/4N, self q10/20, condition과 operator-floor를 모두 보고한다.
3. M0가 통과한 뒤에만 eligible finite-width M1을 실행하고 same-basis independent A–v `h,h/2,h/4`, crop `2/4/8Deff`와 비교한다.
4. A1이 M0/M1에서 실패하면 `BLOCKED_C0_A1`로 남기며 A0 fallback 또는 frequency tuning을 하지 않는다.
5. source-faithful return, exact-minus-core/global adapter, PowerSI correlation과 8 GB production 승격은 계속 차단한다.

## 2026-08-14 — T1-M0 periodic slab independent volume gate

### 시작점과 목적

직전 circle checkpoint를 local commit `6248e0b`로 동결한 뒤, T1-M0 wide coextensive plate의 `coth` law를 독립 numerical method로 회복하고 C0-A1 SAO의 다음 승격 경계를 판정했다. 제품 parser/solver/UI/version은 수정하지 않았다.

### 경계값 문제 정정

M0의 `w=5 mm`는 finite conductor width가 아니라 lateral period다. seam이 translationally identified되므로 physical side face와 corner가 없다. finite rectangle + free-space/unbounded `H2` contour에는 side current crowding과 edge magnetic energy가 생긴다. 따라서 이를 periodic `coth` target과 직접 비교하면 다른 boundary-value problem을 같은 것으로 취급하게 된다.

Root, Sol, Terra의 독립 검토는 다음 결론에 일치했다.

- M0-only periodized Helmholtz/diffusion Green kernel을 만들려면 seam identification과 spectral/image-tail convergence가 별도로 필요하다.
- 이 kernel은 finite/open M1 production candidate의 free-space contour/exterior/corner 경로와 달라 M1 위험을 거의 줄이지 않는다.
- M0는 analytic + independent 1-D volume-only로 동결하고 C0-A1은 circle-only 상태로 유지한다.
- M1 실패가 interior face-coupling/sign으로 격리될 때만 periodic diagnostic을 별도 preregister하며 사후 promotion evidence로 소급하지 않는다.

### M0-V1 normalized 1-D FEM

`ξ=y/t`, `x²=jωμσt²`, `u=σtE/H(0)`로 정규화해

```text
u''-x²u=0,  u'(0)=-x²,  u'(1)=0
(K+x²M)u=x²e0
Zs,FEM=u0/(σt)
```

를 linear FEM으로 풀었다. 제품 `copper_surface_impedance`를 import하지 않았다. row-max→column-max equilibration, LU와 LAPACK `gecon`, consistent mass current/power identity를 사용했다.

canonical은 mandatory 7 anchors와 `δ/t={4,2,1,0.5,0.25}` crossover를 합친 12 frequencies, mesh `N={64,128,256}`이다. 2 GHz fine resolution은 skin depth당 `10.66` elements다.

- fine raw `Zs` log-RMS/max error: `0.017973%/0.073301%`
- medium→fine log-RMS/max: `0.053917%/0.219901%`
- max phase: `0.041998°`
- max full-loop error: `0.002936%`; conductor error를 가리는 보조 지표로만 사용
- max backward residual: `2.220e-16`
- max equilibrated `κ1u`: `6.336e-10`
- max current/power residual: `1.005e-11/7.574e-15`
- FEM high-skin slope: `0.500124/0.500264/0.500528`
- canonical checksum: `d59a770e999fc53c90ca7043cc772badd13220e16ce84912590360fa7e5da5e6`

### W0 withheld

canonical 뒤 수치를 조정하지 않도록 실행 전에 `t={17.5,70} µm`, `σ={29.8,119.2} MS/m`, `f={173 kHz,17.3 MHz,1.73 GHz}`와 adaptive power-of-two mesh rule을 고정했다. 12 combinations의 worst error/mesh/phase는 `0.126811%/0.380419%/0.072657°`, backward `2.220e-16`, `κ1u=1.852e-10`, current/power `7.304e-12/2.147e-15`로 같은 gate를 통과했다.

### Resource와 상태

canonical sequential run은 이 host에서 약 `1.38 s`, process-only peak working set `57.86 MiB`, private bytes 약 `1320.22 MiB`였다. 최대 fine matrix는 `257×257 complex128`이고 case 사이에 보존하지 않았다. process-tree/8 GB product 성능 승격은 아니다.

판정은 `passed_periodic_1d_volume_only`다. analytic gap `jωμh/w`는 M0 전용이며 general exterior pass가 아니다. C0-A1은 `passed_circle_interior_only`, finite/open M1은 `specified_not_run`, T1/global/PowerSI/product는 계속 blocked다.

### 문서 변경

새 [`T1_M0_SLAB_RESULTS.md`](T1_M0_SLAB_RESULTS.md)를 추가하고 README, research state, R2 results, local plan, algorithm candidates, T1 results, M1 spec와 reproduction appendix를 동기화했다. 제품 code는 변경하지 않았다.

### Operator scope와 최종 검증

- positive-frequency branch `Re γ>0, Im γ>0`, CCW contour/outward-normal sign과 full `coth/csch` two-face map을 고정했다.
- independent FEM은 `Ho=0, Hi=1`인 `Zs` 열만 검증했다. `Zx` 또는 임의 two-face excitation을 numerical recovery했다고 주장하지 않는다.
- `b=(1,-1)ᵀ` zero-sum scalar만 유한하며 `(Z'loop/4)bbᵀ` 인공 lift를 physical partial operator나 GlobalMNA stamp로 금지했다.
- terminal peak complex power, two-conductor copper loss, conductor/gap stored-energy identity를 고정하고 FEM mass invariant는 dissipative real-part check임을 분리했다.
- 문서의 standalone reproduction block을 다시 실행해 canonical/W0 전 수치와 checksum이 일치했다.
- physical owner/fail-closed focused regression: `7 passed in 0.79 s`.
- research Markdown 16개: strict UTF-8 error 0, trailing whitespace 0, odd fence 0, broken relative link 0; `git diff --check` error 0.
- visualization은 T1 evidence를 periodic M0 volume-only와 finite/open M1 `specified_not_run`으로 갱신했다. Playwright actual CSS viewport `736×520`과 `360×640`에서 inner/scroll width `704/704`, `328/328`, gate tile 7개를 확인했다.
- Sol은 수학·operator scope·상태를, Terra는 경계/승격과 current next path를, Luna는 reproduction/checksum/resource/문서/시각화를 최종 `APPROVED`했다.

### Exact next starting point

1. smallest eligible equal-width finite/open M1의 perimeter geometry와 nested panel manifest를 먼저 고정한다.
2. C0-A1 interior + unbounded log exterior의 raw partial `Z'`를 `N,2N,4N`, quadrature, residual, condition, current, power와 함께 실행한다.
3. 동일 geometry/current basis의 independent 2-D A–v를 skin mesh와 crop `2/4/8 Deff`에서 실행한다.
4. finite rectangle 결과는 periodic `coth`에 대한 exact error가 아니라 SAO-vs-A–v와 width-asymptotic 보조 trend로 판정한다.
5. M1 통과 전 source return/global/PowerSI 또는 acceleration 단계로 승격하지 않는다.

## 2026-08-14 — T1-M1-EQ0 preregistration

### 시작점과 목적

M0 volume-only 연구를 local checkpoint `55ee9be`로 동결한 뒤, 첫 finite/open result를 보기 전에 geometry, panel endpoint/hash, terminal basis, owner, A–v crop와 8 GB stop rule을 고정했다. 제품 code는 변경하지 않았다.

### Fixture와 terminal scope

M1-EQ0는 vacuum에서 signal `[-125,+125]×[50,85] µm`, return `[-125,+125]×[-35,0] µm`, `t=35 µm`, `σ=59.6 MS/m`, `b=(+1,-1)ᵀ`, peak `Iloop=1 A`다. positive solve는 `100 kHz,1/10/100/500 MHz,1/2 GHz`, DC는 analytic anchor다.

- `Deff=250 µm`, 2 GHz `|kb|Deff=0.010479225107`: quasi-TM screen eligible
- `δ2GHz=1.457746488493 µm`
- `R'dc=3.83509108341 Ω/m`, 10 mm `38.3509108341 mΩ`
- authoritative output은 one-dimensional balanced `Z'loop`; non-zero-sum/common-mode/인공 rank-one lift는 physical partial/global 증거가 아님

### Panel count dispute와 exact manifest

초기 planning count `168/336/672`와 `176/352/704`는 anchor transition과 seed/fine gate가 불명확했다. Sol/Luna와 exact Fraction integer search로 다음을 확인했다.

1. 기존 literal seed `δ/4,h/8` 요구는 `g=1.5` half-interval formula와 과도한 nested refinement를 만든다.
2. 실제 물리 resolution은 authoritative fine `4N`에서 판정하도록 result 전에 정정했다.
3. facing을 `[-125,0]`, `[0,125] µm` 두 interval로 나누고 각각 `M=8/half`, outer는 `M=10/half`, 두 vertical은 각각 `M=5/half`로 고정했다.
4. 한 contour `72`, 두 full contour의 `N={144,288,576}`이며 symmetry reduction을 사용하지 않는다.

fine의 모든 anchor-start panel 최대는 `0.331753555 µm <= δ/4=0.364436622 µm`, max facing은 `5.419805710 µm <= h/8=6.25 µm`, physical corner를 포함한 adjacent ratio는 exact rational에서 `<=3/2`다. endpoint payload hash는:

- seed: `f65cddcf5d45187006ffc5e9eaf1c5624fa14e3849ce435c2601825da579743c`
- medium: `35d1f81c87cb97372c543db98e2063102b8823d5af0e70b8e5c4432966b77b02`
- fine: `5b964069b3bae0965ff3bfcc95348b98656fca9a48da3a998a0510892cd17d0e`

[`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)의 standalone exact-Fraction block을 문서에서 그대로 실행해 closure, CCW area, growth, fine gates와 세 hash를 재현했다.

### Owner, A–v와 resource

- SAO `Din-Dout`은 conductor skin/proximity, unbounded `G0`는 exterior magnetic field의 단독 owner다.
- M0 periodic gap term, `C'/G'`, finite end, roughness, pad/via, layered material와 board return은 EQ0에 포함하지 않는다.
- A–v crop box는 `2/4/8Deff`: x `±625/±1125/±2125 µm`, y `[-535,585]/[-1035,1085]/[-2035,2085] µm`다.
- A–v normal mesh target는 `δ/2,δ/4,δ/8`; hard ceiling `250k nodes/500k triangles`다.
- one case만 순차 실행하고 process-tree WS target `4 GiB`, private/commit `5 GiB` 중단, system commit headroom `2 GiB`와 available RAM `1.5 GiB` 시작 floor를 유지한다.

현재 상태는 `M1-EQ0 preregistered_not_run`, T1/global/PowerSI/product는 blocked다.

### Exact next starting point

1. frozen full-contour endpoint를 그대로 사용해 q20 authoritative/q10 parity의 C0-A1 interior `P/U/Din-Dout`을 실행한다.
2. unbounded analytic-log `G0`, terminal saddle와 `r0={0.1,1,10} m` invariance를 연결한다.
3. raw `Z'loop`, `N→2N→4N`, residual/condition/current/reciprocity/passivity/dissipative-power를 gate한다.
4. SAO가 통과한 뒤 동일 geometry/basis의 independent A–v mesh×crop를 순차 실행한다.
5. 어느 gate든 실패하면 원인을 그대로 기록하고 A0 fallback, symmetrization, clipping 또는 결과 기반 panel tuning을 하지 않는다.

## 2026-08-15 — T1-M1-EQ0 collocation negative result

### 실행 범위

전날 동결한 세 endpoint hash와 full `N={144,288,576}`를 그대로 사용했다. Python 3.12.10, NumPy 2.4.4, SciPy 1.18.0에서 7 positive frequencies를 q20으로 실행하고 fine q10 parity, `r0={0.1,1,10} m` invariance를 별도로 재생했다. 제품 code와 기준 fixture는 수정하지 않았다.

Sol은 root 구현과 독립적으로 같은 raw table과 실패를 재현했다. Luna의 첫 high-frequency smoke가 달랐던 원인은 preregistered regularized C0-A1 `Pmm` 대신 asymptotic self term을 전 frequency에 사용한 것이었다. regularized self로 다시 실행하자 root/Sol과 일치했으므로 차이는 geometry나 exterior가 아니라 self implementation으로 격리됐다.

### SAO response와 mandatory failure

fine q20 `Z'loop`는 100 kHz `3.835212652654+j0.158756770726 Ω/m`에서 2 GHz `65.093071185382+j2341.410241041615 Ω/m`까지다. 전체 raw 3×7 table은 [`T1_M1_EQ0_RESULTS.md`](T1_M1_EQ0_RESULTS.md)에 보존했다.

- medium→fine log-RMS/max/phase: `0.00703814% / 0.0130658% / 0.00159923°`
- fine q10→q20 max/phase: `9.71910e-9 / 5.56704e-7°`
- max backward residual / equilibrated `κ1u`: `5.91481e-16 / 9.21355e-13`
- terminal reciprocity / integrated-current / zero-sum: `7.86166e-16 / 6.66257e-16 / 5.55121e-16`
- `r0` invariance: `9.59635e-16`, tolerance `4.60678e-11`
- minimum real part: `3.83405346 Ω/m`

그러나 mandatory SAO signed dissipative-power mismatch는 fine 7 frequencies에서 `3.719e-8, 3.596e-6, 8.461e-5, 9.414e-5, 1.241e-4, 1.384e-4, 1.585e-4`였다. 모두 `1e-8` gate를 넘었고 2 GHz는 `N=144:2.26760e-3 → N=288:6.180e-4 → N=576:1.585e-4`로 수렴 중이지만 fine pass는 아니다. 상태를 `M1-EQ0 blocked_sao_discrete_power_collocation`, mandatory code를 `BLOCKED_SAO_BOUNDARY_POWER_IDENTITY`로 고정했다.

### 원인 귀속

collocation exterior `Gc[m,n]=∫γn g0(rm,r')ds'`의 fine weighted transpose defect `||WGc−(WGc)^T||/||WGc||`는 `2.29761e-4`다. `B=WGc=S+K`에서 terminal/boundary real-power 차이는 `ωµ0 Im(J^H KJ)`로 정확히 귀속된다. 독립 seed attribution control에서 weighted-symmetric projection은 mismatch를 100 kHz `5.20e-7→3.5e-16`, 100 MHz `2.0525e-3→1.1e-15`, 2 GHz `2.0099e-2→3.1e-15`로 줄였다. 이 projection은 원인 확인에만 사용했으며 pass operator로 채택하거나 결과 matrix를 대칭화하지 않았다.

interior `WYs` weighted ordinary-reciprocity diagnostic은 최대 `7.12361e-2`다. frozen mandatory reciprocity는 authoritative terminal `Z'`에 적용됐으므로 추가 historical gate failure로 소급하지 않고 `diagnostic_fail_non_gated`로 보존한다. 다만 symmetric EQ0 terminal에서 hidden asymmetry가 상쇄될 수 있으므로 prospective weak-operator gate 없이 production promotion하지 않는다.

### Independent A–v smoke

SAO와 독립적으로 body-fitted `4Deff`, 2 GHz P1 `A_z–v`를 실행했다. `18,564` free `Az` nodes, `37,720` triangles, copper normal `δ/2`, central tangential `5 µm` smoke mesh에서

`Z'loop=67.0262639174+j2321.0767997 Ω/m`

를 얻었다. saddle/current residual은 `8.238e-25/8.062e-13`이다. centroid field power의 잘못된 `6.728e-2`는 폐기하고 consistent P1 mass form을 사용해 dissipative/reactive mismatch `1.377e-9/9.694e-13`를 재현했다. fine SAO와의 complex difference는 A–v를 분모로 한 `|ZAv−ZSAO|/|ZAv|=0.8796186%`, `|||ZAv|−|ZSAO|||/|ZAv|=0.8729601%`, phase `0.0616254°`지만 A–v condition, mesh와 crop convergence가 없어 cross-method pass로 사용하지 않는다.

### Resource

full 3-level/7-frequency run은 `110.842 s`, fine 2 GHz q20+q10+`r0` replay는 `11.895 s`였다. largest-level replay process peak working set `334.219 MiB`, peak pagefile/commit counter `1590.961 MiB`, measured private bytes `1453.297 MiB`다. 현재 host process-only 수치이며 8 GB laptop process-tree/parser coexistence 증거가 아니다.

### Next candidate preregistration (historical; superseded by G1 result below)

같은 endpoint와 current basis를 보존하는 `M1-EQ0-G1 exterior_Galerkin_diagnostic`을 선택했다.

`GG[m,n]=∫γm∫γn g0(r,r')ds'ds`, `GE=W^-1 GG`, self `ℓ²/(2π)[ln(ℓ/r0)−3/2]`.

weak equation은 `WE=jωµ0 GGJ+WQV`, `J=YsE`; `AE=W−jωµ0 GG Ys`를 푼다. non-touching tensor Gauss와 shared-endpoint analytic-radial Duffy를 사용하고 첫 oracle에서 pair orientation을 독립 계산한다. transpose copy와 사후 matrix 평균은 금지한다. exact radius shift `−ln(r0'/r0)/(2π)ℓℓ^T`, pair parity, weighted symmetry, q10/q20, raw power와 terminal gate를 결과 전에 고정한다.

### Exact next starting point (historical; superseded by G1 result below)

1. **동결 완료:** G1 self/non-touching/analytic-radial Duffy, independent pair, weak assembly, pair-scale normalization과 prospective interior `Yw` gate를 exact reproduction block에 추가했다.
2. frozen `N={144,288,576}`에서 pair parity, `GG` weighted symmetry, q10/q20와 `r0` identity를 실행한다.
3. 같은 3×7 response/power를 재실행한다. exterior power가 복원돼도 interior hidden-mode reciprocity가 실패하면 `P/U/Pout/Uout` Galerkin화를 별도 사전 등록한다.
4. A–v는 consistent P1 mass로 `h/h2/h4`, crop `2/4/8Deff`, condition과 process-tree resource를 완성한다.
5. 두 2-D 방법이 통과하기 전 T1-F, source/global/PowerSI correlation 또는 acceleration으로 우회하지 않는다.

## 2026-08-15 — T1-M1-EQ0-G1 direct exterior Galerkin result

### Scope and immutable predecessor

G1은 M1-EQ0의 frozen full-contour endpoint/order, balanced current basis, `N={144,288,576}`와 7 positive frequencies를 바꾸지 않고, collocation exterior만 direct double-panel Galerkin `GE=W^-1GG`로 교체했다. 이전 collocation result의 `BLOCKED_SAO_BOUNDARY_POWER_IDENTITY` (`1.58517e-4 > 1e-8`)는 immutable negative result로 남는다. G1은 그 raw operator를 대칭화하거나 C0를 바꾸거나 결과 기반 panel tuning을 하지 않았다.

### Exterior-Galerkin certificate

fine q20, `r0=1 m`의 `Z'loop [Ω/m]`는 100 kHz부터 2 GHz 순서로 다음과 같다.

```text
3.835212680631+j0.158744120667
3.854888639403+j1.585725839089
4.969778690848+j15.078631400726
14.618992113881+j128.314925437738
32.540867638474+j601.572164611766
46.008833807283+j1184.154376140652
65.088435397829+j2341.392170234187
```

- fine structure max q-natural scale: `2.030e-15`; raw weighted transpose defect: `9.953e-17`; `r0` rank-one identity max: `6.333e-16`
- fine boundary-power mismatch max: `1.070e-14`
- `N=288→576` maximum relative/RMS/phase change: `0.019143% / 0.010418% / 0.004177°`
- fine q10→q20 maximum relative/phase change: `9.756e-9 / 5.588e-7°`

따라서 G1의 structural, quadrature, reference-radius, exterior power와 terminal gate는 통과했고 판정은 **`passed_exterior_galerkin_only`**다. 이것은 exterior owner에 한정된 certificate이지 full M1/T1, global composition, PowerSI correlation, product accuracy 또는 8 GB production pass가 아니다.

### Interior blocker and G2

fine pulse-collocation interior `WYs` weighted-reciprocity는 100 kHz→2 GHz에서 `7.12361%, 4.08228%, 3.07258%, 2.91663%, 2.47707%, 2.10899%, 1.69946%`로 모두 frozen `1e-8` gate를 실패했다. raw Hermitian minimum/tolerance는 100 kHz에서 `-2.96377e-5 / 7.22576e-12 S·m`, 1 MHz에서 `-1.79591e-6 / 7.22230e-12 S·m`로 passivity도 실패했고, 10 MHz 이상만 해당 gate를 통과했다. terminal reduction이 이 hidden-mode failure를 상쇄할 수 있으므로 terminal pass로 대체하지 않는다.

다음 상태는 **`M1-EQ0-G2 preregistered_not_run`**이다. G2는 frozen geometry/current basis/frequency/panel levels를 보존하고 interior `P/U/Pout/Uout`를 target-tested Galerkin trace space로 재이산화한다. raw weighted reciprocity, raw Hermitian passivity, terminal, signed power, residual/condition, q/refinement gate를 모두 통과하기 전에는 G2도 M1/T1 승격 근거가 아니다. matrix symmetrization, negative-eigenvalue clipping, C0 변경과 result-driven tuning은 금지한다.

### Independent reference and resource scope

A–v는 G1 선택/보정에 사용하지 않은 independent method이며 기존 2 GHz `4Deff` consistent-P1-mass smoke만 있다. G2가 통과한 뒤에도 A–v `h/h2/h4`, crop `2/4/8Deff`, condition 및 full resource certificate가 별도로 필요하다.

G1 observed resource는 현재 host의 **process-only** level-2 full run wall `405.2 s`, q-parity `558.7 s`, peak working set `120.906 MiB`, private bytes `1348.285 MiB`다. process-tree, parser/reference coexistence, OS headroom 또는 8 GB laptop product performance를 측정하거나 주장한 값이 아니다. 제품 code와 GitHub 상태는 변경하지 않았다.

### Exact next starting point

1. G1 exterior-only certificate와 collocation negative result를 함께 보존한다.
2. **동결 완료:** G2 interior `P/U/Pout/Uout` Galerkin contract, singular pair classes, q20/q40, raw gates와 deterministic scaling record를 exact reproduction fixture로 고정했다.
3. pair screen → circle `N=128→256` → circle `N=256→512` → EQ0 seed를 별도 명령으로 실행하고, 각 단계의 mandatory gate를 검토한 뒤에만 다음 단계와 frozen `N={144,288,576}`·7 frequencies로 확장한다.
4. G2 raw interior reciprocity/passivity와 모든 terminal/power/numerical gate가 통과한 뒤 independent A–v mesh×crop/condition/resource convergence를 완성한다.
5. 두 방법이 모두 통과하기 전 T1-F, board return owner, global adapter, PowerSI correlation, acceleration 또는 8 GB production 승격으로 진행하지 않는다.

## 2026-08-15 — M1-EQ0-G2 target-tested interior Galerkin preregistration freeze

### Scope and no-result boundary

G1의 exterior-only pass와 `BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY`를 변경하지 않고, G2의 weak `P/U/Pout/Uout`, singular quadrature와 실행 gate를 결과 확인 전에 동결했다. 이 절에서는 G2 matrix나 응답을 한 건도 실행하지 않았다. 제품 parser/solver/UI도 수정하지 않았다.

### Independent static audit and corrections

- pulse test/basis mass `W`, `+W` jump, CCW contour의 outward normal, self `3jℓ²/(2π)`, touching Duffy의 `lnρ`/`Ks`, `Yw=W(Dp−Db)`와 `AE=W−jωµ0 GG W^-1Yw`의 부호·단위·배치는 독립 수식 감사와 일치했다.
- 기존 초안이 circle와 EQ0를 연속 실행하던 경로를 폐기하고 `pair → circle 128/256 → circle 256/512 → EQ0 seed`를 서로 다른 명령으로 분리했다. 각 명령은 mandatory gate failure에서 nonzero exit하고 다음 단계는 수동 검토 뒤에만 허용한다.
- self/touching/routed-near/tensor directed-pair count, maximum recursion depth, q20/q40 변화, `Pᴳ` raw transpose, `Yw` reciprocity/passivity/cancellation, terminal/current/power, `r0`와 actual row/column equilibration vector 및 SHA-256을 결과 schema에 고정했다.
- circle normalization은 `Ys,floor=max(1e-12 S,1e-10 maxm|Ys,m|)`를 사용하고 phase mask와 `m↔−m` discrepancy를 보존한다. G2 final parity는 q20/q40이며 G1 q10/q20 구조 결과와 분리했다.
- frozen environment는 mandatory frequencies, q `{20,40}`, circle `N={128,256,512}`, EQ0 level `{0,1,2}`, `r0={0.1,1,10}`만 허용한다. process-local 4 GiB working-set/5 GiB private stop을 두되 이것은 외부 process-tree/system-headroom monitor를 대체하지 않는다.

Python AST, fresh-PowerShell G1-source extraction, UTF-8, Markdown fence, relative link와 `git diff --check`를 통과했다. 상태는 계속 **`M1-EQ0-G2 preregistered_not_run`**이며 full M1/T1/global/PowerSI/product/8 GB는 blocked다.

### Exact next starting point

1. 이 preregistration을 커밋으로 고정한다.
2. `M1_G2_STAGE=pair`, 100 kHz와 2 GHz, q20/q40만 외부 resource guard 아래 실행한다.
3. 두 pair JSON의 class coverage, recursion, quadrature와 range gate가 모두 통과한 뒤에만 circle `N=128→256`을 시작한다.
4. 실패하면 raw 결과를 보존하고 후속 수식/이산화 후보를 별도 사전 등록하며, gate 완화·대칭화·clipping으로 우회하지 않는다.
