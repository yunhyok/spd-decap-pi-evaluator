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
