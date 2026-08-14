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
