# SPD Decap PI Evaluator v0.22.0 — Local Physics Oracle Plan

> **SPD Decap PI Evaluator v0.22.0 — retry-v9 final documentation
> freeze:** Nine public `primary-h4-p0r` invocations are immutable. The ninth ran
> once from retry-v8 token-only commit `0597872...`, token ID `0ec78f53...`; the
> public invocation exited `1`. Factor child PID `51184` exited `0` and produced
> `factor_certificates_complete` with exactly two certificates and two prefix
> checkpoints in order `A_background_II`, `A_conductor_II`; both factor cap gates
> passed. No RHS, solve, extension, boundary `Y`, modal response, or physics ran.
> Inner and outer resource gates passed, and the terminal cleanup audit found all
> `182/182` process identities dead. This factor-only resource evidence is not an
> 8 GB fit claim. The published artifact is a finalizer failure
> wrapper, `BLOCKED_AV_BS_RESULT_SCHEMA` / `monitor-ready child_process_id
> mismatch`: offline verifier PID `37248` was incorrectly used instead of the
> resource-bound producer PID `51184`. A raw terminal seal exists, but strict
> `_validate_outer_terminal_evidence` rejects `terminal control marker chronology
> invalid`; three canonical-JSON helper bootstraps mixed Python
> `time.monotonic_ns()`/GetTickCount64 with PowerShell Stopwatch/QPC. The retained
> tombstone-plus-seal classification is therefore
> `consumed_v2_provisional_invalid_terminal_evidence`, not authoritative. M-only
> consumption `b9c1964...` and D-only retirement `c14f630...` leave the token
> absent. Retry-v9 only threads the explicit resource child PID through offline
> validators and changes embedded bootstrap marker timestamps to
> `time.perf_counter_ns()`/QPC; bootstrap SHA-256 is `0c92a0ec...`. Frozen
> Python/runner/tests SHA-256 are `46082123...` / `229aa91b...` / `2106e533...`.
> Collection is `397`; focused exact selection passed `20` with `377` deselected
> in `2.33 s`, independent same-byte selection passed `20` with `377` deselected
> in `2.37 s`, and compatibility selection passed `7` with `390` deselected in
> `0.95 s`. The first full exact-document suite passed `397/397` in `92.95 s`
> with exit `0` and no failure. This is the **FINAL DOC FREEZE**. Schemas,
> strictness,
> retry/max3/cap64, factor order/caps, resource ceilings, and the RHS/solve/physics
> boundary are unchanged. `factor_fit_unproven=true`;
> `next_stage_authorized=false`. Earlier retry-v8 sections are immutable history;
> any separately authorized future public invocation would be tenth. Evidence is in
> [results](T1_AV_BOUNDARY_SCHUR_RESULTS.md) and the
> [reproduction appendix](ORACLE_REPRODUCTION.md).


최종 갱신: 2026-08-16 (Asia/Seoul)

이 문서는 PowerSI board curve에 맞추기 전에 각 누락 물리의 부호, 크기, scaling law, energy ownership을 작은 canonical coupon에서 반증 가능하게 검증하는 계획이다. 구현 승인이 있기 전에는 제품 solver에 stamp하지 않는다.

## 핵심 조립 계약

모든 국부 보정은 같은 crop과 interface에서 계산한 exact-minus-core여야 한다.

\[
Y_{hybrid}=Y_{global\text{-}core}+P^T\left(Y^\Gamma_{exact}-Y^\Gamma_{core}\right)P
\]

여기서 `Ycore`는 비슷한 analytic 근사가 아니라 실제 global core가 해당 crop에 부여한 연산자다. exact/core는 동일한 geometry crop, interface `Γ`, terminal 순서, current orientation, return conductor, reference/gauge와 **동일한 boundary trace space 및 DtN/Schur boundary operator 정의**를 사용해야 한다.

각 dielectric volume, copper volume/surface, magnetic field, component branch와 crop 경계를 가로지르는 mutual electric/magnetic coupling term은 `retained-core`, `removed-core`, `inserted-exact` 중 정확히 하나의 owner만 가진다. interface에서 signed complex power가 양쪽 조립 전후에 보존되어야 한다. correction 단독은 음의 항을 가질 수 있으므로 correction 자체의 passivity가 아니라 두 raw block과 최종 global network의 passivity를 검사한다. equipotential plane이나 ideal trace equivalence처럼 topology가 바뀌는 항은 행렬을 더하기 전에 node/edge topology를 교체한다.

Manufactured `Yexact=Ycore` case에서 원 baseline이 machine precision으로 복원되지 않으면 어떤 board correlation에도 사용하지 않는다. crop `R`, `2R`, `4R`에서 raw block 각각뿐 아니라 correction `ΔYΓ=YexactΓ−YcoreΓ`와 interface complex power가 수렴해야 한다. cross-boundary coupling 때문에 `ΔYΓ`가 국소적으로 수렴하지 않으면 local correction으로 채택하지 않고 해당 field를 더 큰 global domain이 소유한다.

## 사전 등록 canonical matrix

| ID | 물리와 oracle | 예상 signature | double-counting 차단 | 비용 목표 |
|---|---|---|---|---|
| N0 | reciprocal RLC graph의 analytic Z | reduced/unreduced port Z machine-precision parity | 물리 stamp 없음 | <1 s |
| T1 | 직선 trace + 명시적 return; Cohn/periodic-plate analytic anchor와 converged 2-D SAO–CIM | DC/skin/proximity, M0 width scaling, deembedded length increment, distributed `γl` | ideal short와 동일 crop의 trace/return electric·magnetic core를 교체 | analytic <1 s, local 2-D 수십 s 목표 |
| S1 | annular spreading `ln(b/a)/(2πσt)`; rectangle/neck/void/L-shape triangular sheet | same-artwork `Zii/Zij`, neck/void 인접 port 변화 | equipotential artwork node를 distributed sheet로 교체 | 1–5 min/case |
| C1 | circular finite launch의 parallel-plate/CIM 및 triangular/MFDM 수렴 | source-derived finite radius를 고정한 mesh/crop 수렴; 별도 radius sweep에서 예상 `ln(1/a)` spreading scaling | 기존 point launch와 동일 footprint/return 제거; zero-radius limit 금지 | 수초–수분 |
| V1 | central via + coaxial return manufactured case | radius/length/conductivity/return-radius scaling | copper internal Z와 external return field를 한 loop가 소유 | <1 s |
| V2 | PWR/return via pair, bundle, GND ring; PEEC + 3-D coupon | 10 MHz–2 GHz loop L, current sharing, transfer Z | 기존 via self R/L을 crop에서 제거; isolated partial-L stamp 금지 | PEEC <1 s at q≤64 |
| A1 | circular pad/drill/antipad axisymmetric r-z; rectangular 3-D coupon | pad/antipad 크기에 따른 C와 high-band resonance/phase | `ΔC=Cfull-rz-Cvertical-columns`, 동일 terminal/hash | <60 s, ≤120k cells |
| I1 | mounted device pad→trace→PWR via/plane + GND return bounded crop | T/S/C/V/A signature를 유지하며 board error 감소 | 모든 field/conductor owner ledger 필수 | workstation 수십 min–수 h |
| D1 | lossless/lossy plane/trace coupon, source dielectric table | resonance Q/Re(Z), source-consistent dispersion | 같은 gap의 lossless C 중복 금지 | field solve와 동급 |
| K1 | finite-thickness/solid-cylinder skin oracle | skin crossover 이후 `R∼√f`, damping | volume 또는 surface conductor model 중 하나만 사용 | O(Nsheet) |

Pair P2는 N0–A1을 통과한 뒤 diagonal/local-transition sanity case로 사용한다. Pair P3/P4는 provenance confound를 닫은 뒤 spatial `ΔZ` attribution에 사용한다. Pair P1 full numeric curve는 후보와 parameter policy 동결 전까지 열지 않는다.

## Oracle 수치 gate

### 주파수와 reporting floor

Positive-frequency anchor는 100 kHz, 1/10/100/500 MHz, 1/2 GHz다. skin crossover, first cavity resonance, zero crossing의 양옆을 adaptive하게 추가한다. DC는 저항/정전용량처럼 유한한 DC QoI에만 사용하고 phase, log-weight RMS와 singular open-circuit impedance에서는 제외한다. sweep RMS는 각 decade가 동일 총가중치를 갖도록 log-frequency trapezoid weight를 사용한다.

수치 reporting floor는 `Zfloor=1 µΩ`, `Yfloor=1 nS`, `Cfloor=1 fF`로 사전 고정한다. 이는 물리값을 대체하거나 fit하는 값이 아니라 relative normalization의 분모만 제한한다. phase는 해당 complex entry의 magnitude가 `10×floor` 이상인 점에서만 평가하고 나머지는 absolute complex error로 평가한다.

### Mesh/crop convergence

- mesh `h`, `h/2`, `h/4`; crop `R`, `2R`, `4R`
- finite port 원주 최소 16 element; narrow neck/antipad clearance 최소 4 element
- matrix QoI `Q`의 weighted RMS는 `sqrt(Σw||Qa−Qb||F² / Σw max(||Qb||F, sqrt(mn)Qfloor)²) ≤0.5%`
- eligible element의 `max |Qa−Qb|/max(|Qb|,Qfloor) ≤1%`
- eligible phase difference ≤0.25°; matched resonance frequency shift ≤0.5%
- raw `YexactΓ`, `YcoreΓ`뿐 아니라 `ΔYΓ`와 interface complex power가 같은 기준을 통과해야 함

### Invariant와 linear-solve gate

- reciprocity: `||Z−Zᵀ||F/max(||Z||F,nZfloor) ≤1e-8`; Y도 동일
- pointwise dissipativity: `λmin(H(Z)) ≥ −τZ`, `τZ=max(1 nΩ, 1e-10||H(Z)||2)`; Y는 `τY=max(1 nS, 1e-10||H(Y)||2)`
- floating Maxwell C: symmetry relative error ≤1e-10, `||C1||2/max(||C||F,Cfloor) ≤1e-10`, `λmin(C) ≥ −max(1 aF,1e-10||C||2)`
- normalized KCL residual `||A i||2/max(||i||2,1 nA) ≤1e-10`; interface voltage는 `||v+−v−||2/max(||v||2,1 nV) ≤1e-10`, signed complex-power imbalance는 `|Pin−Pout|/max(|Pin|+|Pout|,1 fW) ≤1e-10`
- backward residual `rb=||Ax−b||2/(||A||2||x||2+||b||2) ≤1e-10`
- float64 reliability: `κ2(A)ε64 ≤1e-8` 및 `κ2(A)rb ≤1e-3`; 넘으면 higher precision 또는 독립 formulation과 일치하기 전 차단

위 Hermitian eigenvalue 검사는 sampled-frequency **discrete dissipativity** 검사일 뿐 broadband causality/positive-real 증명이 아니다. direct model은 source material law의 causal/passive form과 withheld dense-frequency Kramers–Kronig/energy consistency를 확인한다. rational fit/ROM은 stable poles와 broadband positive-real/passivity certificate를 별도로 통과해야 한다.

현재 일부 helper의 기본 convergence 1.5–8%는 탐색에는 사용할 수 있지만 최종 oracle 승격 기준으로는 사용하지 않는다.

## 기존 연구 자산 readiness

2026-08-14에 관련 12개 focused test module의 178 tests가 통과했다. 이는 구현의 내부 invariant가 유지된다는 증거이지 production 정확성 승격은 아니다.

| 자산 | 현재 활용 | 아직 차단된 이유 |
|---|---|---|
| `tri_fem_sheet.py` | finite-contact sheet, analytic/refinement/convergence oracle | global topology와 stamp ownership attestation 미완료 |
| `tri_fem_gap.py`, `tri_fem_pair.py`, `tri_fem_stack.py` | parallel-plate/gap/pair/stack canonical 연구 | pair/stack production eligibility가 명시적으로 false |
| `mfdm.py`, `mfdm_adapter.py` | multilayer/slot/aperture sensitivity 연구 | full-wave 대체가 아니며 nonadjacent coupling은 experimental |
| `surface_patch_plane.py` | finite-area unit-current port 보존 | real SPD port footprint/return mapping 미확정 |
| `via_peec.py` | straight circular via cluster, mutual/current-sharing oracle | pad/antipad/plane/bend/return을 포함하지 않으며 exact-minus-core owner proof 없음 |
| `research_axisymmetric_electrostatics.py`, `edge_cell_capacitance.py` | full-rz-minus-column 및 edge-detail-minus-bulk oracle | SPD geometry adapter와 동일 crop/terminal hash 계약 미완료 |
| `global_mna.py` | passivity/reciprocity/KCL 및 ownership harness | production domain assembly와 convergence certificate 미연결 |

따라서 새 커널을 먼저 발명하지 않는다. 기존 자산으로 canonical manifest, owner ledger, convergence certificate, SPD adapter를 구성한 뒤 실제 board에 연결한다.

## 2026-08-14 실행 판정

상세 수치와 실패 기록은 [`R2_ORACLE_RESULTS.md`](R2_ORACLE_RESULTS.md), [`T1_TRACE_ORACLE_RESULTS.md`](T1_TRACE_ORACLE_RESULTS.md), [`T1_CIRCLE_DTN_RESULTS.md`](T1_CIRCLE_DTN_RESULTS.md), [`T1_M0_SLAB_RESULTS.md`](T1_M0_SLAB_RESULTS.md)를 따른다.

| ID | 판정 | 핵심 근거 |
|---|---|---|
| N0 | pass, scalar-only | frozen reproduction 포함 두 제조 예제 reduced/unreduced max relative Z error `5.34e-15`; owner ledger 완전 |
| T1 | blocked, limited subcase pass | Cohn/periodic manufactured passes와 M1/G1/G2 frozen limits를 보존한다. `AV-BS1-CIRCLE` H1/H2는 stage-only pass다. 아홉 번째 P1은 producer PID `51184`에서 두 factor certificate/prefix와 factor/resource gates를 통과했지만 offline verifier PID `37248`를 잘못 바인딩한 published finalizer failure로 끝났다. Raw seal은 strict chronology-invalid이며 authoritative하지 않다. 현재 retry-v9 provisional candidate, token absent, `factor_fit_unproven=true`, `next_stage_authorized=false`; first full exact-document suite는 `397/397` in `92.95 s`, exit `0`, no failure로 통과했다. Fine analytic/convergence/final circle, H4 physics/PowerSI는 미실행 |
| S1 | blocked | rectangle 최종 refinement 0.299%는 부분 통과했으나 annulus refinement fail, 전체 corpus/crop/ownership 없음 |
| V1 | blocked | solid-cylinder R/L/skin law는 통과했으나 명시적 coax return loop 없음 |
| V2 | blocked | PEEC current sharing/invariant는 통과했으나 `global_mna_composable=False`, 3-D/exact-minus-core 없음 |
| A1 | blocked | crop과 invariant는 양호하지만 2.5→1.25 µm mesh RMS 변화 `4.734%` |
| C1 | blocked | scalar circular-disk C와 footprint area는 양호하지만 spreading Z/crop/ownership 및 `1e-10` residual certificate 없음 |

N0만 현재 constitutive scope 전체에서 gate를 통과했다. T1-E0, `M0 passed_periodic_1d_volume_only`와 `C0-A1 passed_circle_interior_only`는 T1 전체 또는 global composition 통과가 아니다. 기존 helper가 반환하는 `production_eligible` 같은 내부 상태를 제품 accuracy promotion으로 해석하지 않는다.

## T1 추가 gate

- smooth copper 0–2 GHz T1에는 skin/proximity/edge current를 포함한다. DC-only series R/L은 `T1a` screening이며 broadband pass가 아니다.
- Cohn E0는 body edge를 exact node로 갖는 mesh h/32,h/64,h/128과 strip-edge padding 2h/4h/8h를 사용한다.
- M0의 lateral-periodic slab과 finite/open rectangle을 같은 exact target으로 비교하지 않는다. M0 independent normalized 1-D FEM은 canonical과 material/thickness withheld를 통과했지만 `volume_only`다. free-space finite contour에는 side face/corner가 있으므로 periodic `coth`는 exact gate가 아니라 width-asymptotic 보조 지표뿐이다.
- finite-width M1의 기존 panel/crop 계약과 independent A–v H1/H2 stage-only evidence를 보존한다. 아홉 번째 P1은 certified two-factor result와 resource gates까지 도달했지만 published finalizer PID mismatch와 strict-invalid raw seal 때문에 authoritative factor result가 아니다. retry-v9 provisional no-token contract의 first full exact-document suite는 `397/397` in `92.95 s`, exit `0`, no failure로 통과했다. Exact-byte/독립 audit와 별도 승인 전에는 future tenth factor pilot을 만들지 않고, P1 result audit와 별도 H4-P1 전에는 physics를 열지 않는다. 두 radius circle 통과 뒤에만 EQ0 crop/skin-mesh 계약을 연다.
- circle interior에서 frozen `C0-A0` 저주파 switch는 실패했고 `C0-A1` fixed `C0=1` direct/scaled `H^(2)`가 제한 통과했다. finite/open M1도 이 정책으로 시작하며 실패 시 A0로 자동 복귀하거나 주파수별로 tune하지 않는다. M0-only periodized kernel은 M1 실패가 interior sign/coupling으로 격리될 때 별도 time-boxed diagnostic으로만 사전 등록할 수 있다. 수치 범위는 operator-floor proof가 없으면 fail-closed다.
- finite-length F는 동일 end fixture의 `Z(2l)−Z(l)` increment로 2-D p.u.l operator와 비교한다. raw finite 3-D Z의 exact length proportionality는 요구하지 않는다.
- symmetric lumped π는 mandatory band 전체 `|γl|≤0.1`과 exact line matrix 0.5%/1% 비교를 모두 통과해야 한다. exact distributed stamp가 기본이다.
- Trace ref 이름은 metadata screening일 뿐이다. actual return polygon, continuity, current split, terminal footprint와 same-crop field owner가 없으면 source-faithful 상태를 부여하지 않는다.
- positive return artwork나 nearby same-net GND via graph가 확인돼도 signal trace/pad와 return terminal을 묶는 signed basis와 field owner가 없으면 `signal_to_return_operator_unproved`다. endpoint가 return void에 있으면 그 launch/antipad field는 straight 2-D trace template에서 분리한다. selected evidence는 [`T1_RETURN_CROP_MANIFEST.md`](T1_RETURN_CROP_MANIFEST.md)를 따른다.
- reduced differential operator를 absolute nodal block으로 lift했을 때 gauge 외 null mode가 남으면 global MNA 조립을 금지한다. full partial/common-mode operator 또는 explicit local current constraint가 필요하다.

## Source-derived parameter 정책

- trace: endpoint/layer/width와 raw `UpperRef`/`LowerRef` 상태를 사용한다. Pair P3/P4에는 width evidence가 없는 Trace가 다수이고 explicit ref는 전혀 없으므로 누락을 추정값으로 숨기지 않는다.
- conductor: layer thickness, conductivity, trapezoidal angle만 source evidence 범위에서 사용한다.
- dielectric: 한 frequency point만 있는 material은 broadband causal dispersion을 유일하게 정하지 못한다. 추가 metadata 없이는 constant-property sensitivity만 허용한다.
- via: drill, regular pad, layer endpoint, material을 사용한다. plating/fill thickness가 없으면 fitted value를 만들지 않는다.
- antipad: raw row와 parser 보존 여부를 별도로 manifest한다. 부재를 nominal antipad로 대체하지 않는다.
- surface roughness, port footprint, de-embedding, reference conductor가 없으면 unknown으로 유지하고 PowerSI curve에서 역추정하지 않는다.

네 pair의 실제 parameter 범위와 결손 상태는 [`SOURCE_PARAMETER_MANIFEST.md`](SOURCE_PARAMETER_MANIFEST.md)를 따른다. P3/P4의 약 16.5% missing trace width, 네 pair의 plating/fill/roughness 부재, P1/P2의 raw-but-unpreserved `NoAntiPadLayers`는 canonical coupon 또는 board adapter의 명시적 blocker다.

## Ablation과 승격 규칙

1. 결과를 보기 전에 affected port, band, 변화 방향, scaling law를 기록한다.
2. analytic manufactured case와 최소 세 개의 source-range geometry에서 수렴한다.
3. one-at-a-time addition과 leave-one-block-out을 모두 수행한다. plane/port/via/pad interaction은 작은 제한 factorial coupon으로 확인한다.
4. attribution의 detectable-change gate는 단위를 섞지 않는다: magnitude `max(3σmag,0.25 dB)`, phase `max(3σphase,2°)`, complex absolute `max(3σcomplex,25 µΩ)`, matrix-relative `max(3σmatrix,0.5%)`를 각 metric에 따로 적용한다.
5. 단일 block의 board error가 단조 감소할 필요는 없다. 올바른 물리가 기존 model-error cancellation을 깨뜨릴 수 있으므로 canonical/invariant를 통과한 block은 사전 등록된 factorial/I1까지 진행한다. 최종 판단은 integrated held-out accuracy와 unaffected-port regression으로 한다.
6. unaffected-port regression은 integrated model에서 reference uncertainty를 넘지 않아야 한다. 단일 ablation 변화는 예상 signature와 localization이 맞는지 별도로 기록한다.
7. design-specific fit, 추정 return path, source에 없는 roughness/plating, invariant 실패가 하나라도 있으면 기각한다.
8. pair P3/P4는 confound가 닫힌 뒤에만 absolute Z와 VQPS 집중 `ΔZ`의 부호, 크기, spatial localization 검증에 사용한다.

## Exact graph reduction admissibility

Degree-2 series-chain/tree rule은 constitutive law가 서로 독립인 scalar two-terminal branch이고 eliminated node가 measurement/mutable terminal, local-correction interface, controlled source, cross-boundary coupling 또는 mutual R/L/C block에 참여하지 않을 때만 exact다.

Mutual coupling이나 multi-terminal block이 들어오면 단순 series 합을 쓰지 않고 전체 coupled operator의 exact Schur complement를 사용하며 passivity와 sparsity 비용을 다시 평가한다. N0 parity는 최초 core에 한 번만 적용하지 않는다. trace topology replacement, V2 mutual block, pad/port correction과 global domain이 추가될 때마다 최종 연산자의 reduced/unreduced full-port Z를 mandatory anchor와 withheld dense points에서 machine-precision floor 내로 다시 비교한다.

N0 high-precision canonical parity는 `||ΔZ||F/max(||Z||F,nZfloor) ≤1e-12` 및 `max|ΔZij| ≤ max(1 pΩ,1e-12 max|Zij|)`를 모두 요구한다. production float64 비교는 두 solve의 계산된 forward-error bound를 tolerance에 더해 보고하되, 그 합이 oracle complex-error budget의 0.1%를 넘으면 exact 인증을 부여하지 않는다.

이 exact reduction은 MOR/vector fitting과 구분한다. 현재 물리를 바꾸지 않는 parity가 증명된 버전은 R2/R3의 bounded resource 실험에 사용할 수 있지만, 결합 구조가 바뀔 때마다 인증은 무효화되고 재검증한다.

## Retry-v4 gate amendment

Preserve all four public H4-P0R-P1 attempts as pre-claim/pre-factor failures.
The fourth attempt (`9b4854d0...`, once from
`2026-08-15T13:37:16.0468244Z` through
`2026-08-15T13:37:20.5004777Z`, exit `2`) reached outer/control ready and start
release but stopped in control preflight on
`NONROOT_DISAPPEARANCE_NOT_CONFIRMED` for exited same-birth PID `40224`. Its
token was semantically spent, never reused, and removed by `f1aeeac...`; there
was no claim, factor, RHS, solve, physics, PowerSI, or 8 GiB result.

Retry-v4 permits only this identity-bound exited/snapshot-present transition to
settle through at most three complete snapshots, with two 25 ms waits, and only
where outer/control callers already pass `MaximumAttempts>1`. All default-one
factor call sites and fail-closed root/reuse/query/access/not-found rules remain
unchanged. Python/schema are unchanged. Static bindings are Python
`95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234`, runner
`7893cd57fd4b1686addd434fa0103d9f3b0e0087f4783f2c82e459661abfb645`, tests
`18aabcdf7b32c6b013aec65497d31f4d908f3085f53f64cb3791f38a2e79381d`;
focused `11/11` and full no-cache `322/322` passed. No fresh token or public run
is authorized by this documentation cycle; `next_stage_authorized=false`.

## Retry-v5 gate amendment

Preserve the fifth public attempt separately from the four pre-claim
interruptions. Commit `5ba4b693...` created the claim and factor-only child
PID `55552`; `108/108` samples saw that child before the factor monitor hit the
same bounded disappearance class at an active call that still used default
maximum `1`. No prefix or certificate completed, so factor attempt/performance
remain `null`, completed factor count is zero, and factor fit remains unproven.
RHS, solve, H4 physics, PowerSI, and EQ0 work did not run. Commit `46c08d405...`
preserves the consumed tombstone and `71d3dab...` retires the token.

Outer close `9d0c0aab...` independently counted `27` confirmed disappearances
but stored `16`; truncation alone made the mandatory outer gate false and
prevented the seal. Retry-v5 therefore makes exactly two bounded corrections:
explicit maximum `3` at the four active factor sample calls and shared
outer/control retry-event cap `64`. The default-one caught-cleanup call and all
retry predicates/fatal cases remain unchanged. The zero-byte stdout schema
overlay is recorded but deferred outside this run-enabling scope. Frozen
Python/runner/tests hashes are `54aa7da9daa013cec41585ae07757e6c54e8e9d15da7f4f16d4f6546b6fb35aa` /
`3888524877f7a90966fb932f7f9fc4c9a48480134eb6295712eaa0ef548416a3` /
`b51498ebe27a0210a09b3a12b26e0146d39c1249906469bcb1add1d2f2443f08`;
focused/full tests passed `29/29` and `324/324`. Token absent and
`next_stage_authorized=false` remain mandatory.

## Retry-v6 gate amendment

Preserve the sixth attempt separately. Commit `82775327...` created claim and
factor child PID `55380`, but direct child output stopped before
`_factor_one`/`splu` on `claimed preflight payload mismatch`; attempted and
performed are `false`, and completed/certified factors remain zero. Independent
outer close `d2777207...` exhausted max3, retained `30/30` events without
truncation, verified cleanup, and wrote no seal. Resource limits were not the
cause. No RHS, solve, H4 physics, PowerSI, or EQ0 work ran.

Provisional record `43bb34b2...` is exact and honest but invalid under the
strict tombstone contract because nested `bindings.resource_policy_sha256` is
absent. Commits `06061a2...` and `c001b44...` preserve then delete-retire that
state. Retry-v6 changes only three redundant Python lines and the missing runner
binding key. Frozen Python/runner/tests hashes are `2373a13f...` / `852ce8a0...`
/ `f1d0b044...`; focused `8/8` passed and full no-cache `330/330` passed in
`81.83 s`. Retry policy/cap and every physics gate remain unchanged.

## H4-P0R retry-v7 historical non-result

The seventh public P1 invocation at `4f60bd5...` changes no physics or oracle
ranking. Native `splu` returned for `A_background_II`, so attempted/performed
are `true/true` and its completed name is durable; the obsolete
native/exported equality gate then failed before certificate/prefix creation.
`A_conductor_II` was not attempted and exact native/exported counts were not
persisted. Inner/outer monitoring retained `174/573` samples and all `31`
outer retry events without truncation. Both resource gates passed and the seal
is complete, but authoritative pass/next remain false; this proves neither
accuracy nor 8 GiB fit. No RHS, solve, H4 physics, PowerSI, or downstream oracle
work ran.

Consumed/retirement commits `51e5069...` / `17414cf...` leave the token
absent. Retry-v7 requires `0 < exported <= native`, validates distinct
native/exported portable-byte formulas and caps both, without changing schema,
retry policy, factor order, or physics gates. Frozen Python/runner/tests SHA-256
are `7a1dba5e...` / `852ce8a0...` / `bd2e3e2c...`; focused `25/25` and full
no-cache `341/341` passed, the latter in `88.40 s`. Detailed evidence is in
[`T1_AV_BOUNDARY_SCHUR_RESULTS.md`](T1_AV_BOUNDARY_SCHUR_RESULTS.md) and
[`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md).
## H4-P0R retry-v8 historical static candidate

The eighth public `primary-h4-p0r` invocation ran once from retry-v7 token-only
commit `66211ef...`. Native `splu` returned for `A_background_II`, making
attempted/performed `true/true` with one completed name, but combined
non-canonical L/U storage left zero certificates and prefixes. `A_conductor_II`,
RHS, solve, and physics never started. The resource artifact passed; independent
outer/control monitoring falsely exhausted across distinct confirmed-dead
helpers, leaving no seal or published result. Seven temporary evidence files are
byte-identical ignored-quarantine copies. Valid provisional emergency consumption
`8fce704...` and D-only retirement `7964464...` leave the token absent.

Retry-v8 records raw L/U storage hashes, raw composite canonical flags (false
when unsorted), and explicit-zero scans before mutation. The pre-sort
`has_sorted_indices` value is a local sort predicate, not a certificate field;
chunked CSC column spans and row-index bounds are validated, nonfinite values
and explicit zeros fail, and every unsorted L/U is sorted in place. Sorted and
canonical format are then rechecked, which rejects duplicates; the canonical
hash helper enforces that format, and finite/zero data is re-scanned without
repeating the span or row-bound scan. There is no schema/field bump, L/U or
whole-factor matrix copy, coalescing, or pruning.

Attempt grouping is local to one `Get-TreeSample` producer call and state resets
per invocation. Within one call, `A,B,A => 1,1,1`; `A,B,B,B => 1,1,2,3`,
with same-identity attempt 3 fatal. Cumulative report history may legitimately
contain adjacent same-identity attempt `1/1` entries separated by a successful
return; report history therefore cannot enforce cross-call adjacency. Only the
close-only suffix, known to come from one call, enforces the exact adjacent-
strong transition.

The cumulative/local cap remains hard at 64 even when diagnostics are null.
Within a call, event 64 is stored and increments the total to 64; that same
64th confirmed retry fails generically with
`TREE_SAMPLE_TOTAL_RETRY_CAP_REACHED` unless simultaneous same-identity attempt
3 takes precedence, and the failing call does not return to its retry loop.
Reused diagnostics in a later cleanup or close call may advance
`confirmed_count` beyond 64 and truncate the stored list, but that evidence is
failed/truncated and can never support a passing sample. Existing max3/cap64
fields and schemas remain unchanged; cleanup-race hardening is deferred.

Frozen current Python/runner/tests SHA-256 are
`c26112b0738224eed4f1401553adf6fc9a3512b37c475f5b52152cab98e6ecff` /
`7e675cc31ab229485719200af8b508dae20bd234bff29428e84628029df2763e` /
`426f83888587dc57b7874a4e8dd47f0965212a0c1d7fabde33e6f3ab2801a2e7`.
Collection is `387`; focused root `23` passed / `363` deselected and
independent `27` passed / `360` deselected. The full exact-document suite passed `387/387` in `94.11 s`. A future
authorized public run would be ninth. Token state is absent, next-stage
authority is false, and factor fit and H4 physics remain unproven.

## H4-P0R retry-v9 provisional static candidate

The ninth public `primary-h4-p0r` invocation ran exactly once from retry-v8
token-only commit `0597872eeaedd359ce5a3d429748dee2b70ec9a7`, token ID
`0ec78f5300cc4a63923e735e81b7e713`, and exited `1`. Factor producer PID
`51184` exited `0` with `factor_certificates_complete`: exactly two
certificates and two prefix checkpoints were published in the frozen order
`A_background_II`, `A_conductor_II`, and both factor cap gates passed. Inner and
outer resource gates passed, and all `182/182` recorded process identities were
dead at terminal cleanup. No RHS, solve, extension, boundary `Y`, modal
response, or physics ran.

The published artifact is nevertheless the finalizer failure
`BLOCKED_AV_BS_RESULT_SCHEMA` / `monitor-ready child_process_id mismatch`.
Offline validation supplied verifier PID `37248` where strict validation needed
the resource-bound producer PID `51184`. A raw terminal seal exists, but it is
not authoritative: `_validate_outer_terminal_evidence` rejects `terminal
control marker chronology invalid`. Three `canonical_json_hash-*` helper
bootstraps mixed Python `time.monotonic_ns()`/GetTickCount64 with PowerShell
Stopwatch/QPC. The retained tombstone-plus-seal classification is therefore
`consumed_v2_provisional_invalid_terminal_evidence`.

M-only consumption `b9c1964e8a445b2d45487894485dc8453bd002d4` and D-only
retirement `c14f6309e6b84effbc5139ed8dcdcf05d5ea61f1` leave the token
absent. Retry-v9 changes only offline identity threading and the embedded
bootstrap clock: validators receive the explicit resource child PID, marker
timestamps use `time.perf_counter_ns()`/QPC, and bootstrap SHA-256 is
`0c92a0ec8fe67868e222647782cb5eedabffcad77319348f55beaeb0dd745404`.
Schemas, strict gates, retry/max3/cap64 policy, factor order/caps, resource
ceilings, and the RHS/solve/physics boundary remain unchanged.

Frozen Python/runner/tests SHA-256 are
`46082123fbf98102f3e5995c32bf65d8d04bb835b9c1305de0150349e75e48c7`
(`579582` bytes),
`229aa91b04bd89e4af03f9a75c52a0a4819381ec5dfa32a272fe35ac7f1d94f9`
(`443675` bytes), and
`2106e5338158d2f61a63d572b6f452bd54b752f38500a566f573759096f2c36d`
(`458913` bytes). Collection is `397`. Focused exact selection passed `20`
with `377` deselected in `2.33 s`; an independent same-byte selection passed
`20` with `377` deselected in `2.37 s`; compatibility selection passed `7`
with `390` deselected in `0.95 s`. The first full exact-document suite passed
`397/397` in `92.95 s` with exit `0` and no failure. This is the **FINAL DOC
FREEZE**.
`factor_fit_unproven=true`; `next_stage_authorized=false`. Retry-v9 creates no
token; any separately authorized future public invocation would be tenth.

## 실행 순서

1. N0과 exact-minus-core identity case를 동결한다.
2. 기존 M0/M1/G1/G2와 AV-BS1 H0/H1/H2 artifacts, H4 parents 및 아홉 public interruption/retirement를 보존한다. retry-v9 token-absent provisional contract의 first full exact-document suite `397/397` in `92.95 s`, exit `0`, no failure와 FINAL DOC FREEZE를 보존한다. Exact-byte/독립 audit와 별도 승인 전에는 factor pilot을 만들지 않는다. 이전 token commits를 재사용하지 않으며 미래 fresh-token run은 열 번째다. P0R authoritative result audit와 별도 H4-P1 전에는 h4 physics를 실행하지 않고, withheld-radius review 전에는 EQ0 mesh/crop으로 확장하지 않는다.
3. P1/P2 explicit-ref trace의 actual return polygon/connectivity와 absolute/core DtN owner를 증명한다.
4. S1, V1/V2, A1, C1의 남은 analytic/mesh/crop/invariant blocker를 해결한다.
5. PowerSI에서 trace-only, plane-neck, via-pair, pad/antipad, finite-port coupon과 반복 해석을 확보한다.
6. T/S/C/V/A 통합 I1을 검증한다.
7. explicit external-port 계약과 memory preflight 뒤 pair P2 anchor baseline으로 올라간다.
8. solver-state confound를 닫은 뒤 pair P3/P4 full-matrix `ΔZ` attribution을 수행한다.
9. 통합 operator에서 exact reduction parity를 다시 증명한다. passive MOR와 adaptive sampling만 physical accuracy 동결 뒤 성능 단계로 승격한다.
