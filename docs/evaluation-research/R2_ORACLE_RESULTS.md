# SPD Decap PI Evaluator v0.22.0 — R2 Local Oracle Results

최종 갱신: 2026-08-15 (Asia/Seoul)

이 문서는 [`LOCAL_ORACLE_PLAN.md`](LOCAL_ORACLE_PLAN.md)에 사전 등록한 국부 물리 oracle을 기존 research kernel로 실행한 결과다. 제품 solver, parser, UI, version, installer는 수정하지 않았다. `pass`는 아래에 명시한 coupon과 constitutive scope에서만 유효하며 PowerSI 정확성 승격이나 global-MNA 조립 승인을 뜻하지 않는다.

## 판정 요약

| ID | 현재 판정 | 통과한 증거 | 남은 차단 조건 |
|---|---|---|---|
| N0 | **통과 — independent scalar branch 한정** | reduced/unreduced Z가 analytic 및 direct solve와 machine precision에서 일치 | mutual/multiterminal block, topology replacement 뒤 재인증, production 연결 |
| T1 | **차단 — G2 pair 제한 통과, 100 kHz circle 실패** | Cohn lossless `C'`, periodic plate 1-D FEM, `C0-A1` circle, G1 exterior와 G2 pair q/symmetry gate; `AV-BS1-CIRCLE` manifest/metric preregistration | immutable collocation power fail; G1 interior failure 미해결. G2 circle N256 raw reciprocity `1.41e-8`, cancellation `1.64e-7`; planned G2 2 GHz circle/N512/EQ0 seed, AV-BS1 physics/convergence, finite-length 3-D, 실제 return polygon/connectivity, balanced projection과 same-crop owner partition |
| S1 | **차단 — rectangle refinement 부분 통과** | 유한 면적 contact, reciprocity/nullspace/passivity; 마지막 level 6→7 변화 0.299% | annulus refinement 오류, 전체 h/h/2/h/4·crop corpus, neck/void/L-shape, interface/owner certificate |
| V1 | **차단 — constitutive law 통과** | solid-cylinder DC R, internal/external L limit, skin trend, numerical invariant | 명시적 coaxial return loop와 exact-minus-core block 없음 |
| V2 | **차단 — isolated PEEC 통과** | mutual L, signed return, current sharing, reciprocity/passivity/KCL | 3-D coupon, pad/antipad/plane return, 실제 core subtraction; API가 global composition을 명시적으로 금지 |
| A1 | **차단 — crop/invariant만 통과** | full-rz-minus-column identity, crop 안정성, Maxwell-C invariant | mesh 변화 4.73% 이상으로 0.5%/1% gate 실패, rectangular 3-D·owner/interface-power 없음 |
| C1 | **차단 — scalar circular-disk C 부분 통과** | BEM mesh 변화와 footprint 면적은 수치상 양호 | circular launch spreading Z, crop, `ln(1/a)`, triangular/MFDM 교차 검증, point-core replacement, residual gate 없음 |

따라서 현재 R2에서 global 조립과 연결해 승격 가능한 것은 N0의 **독립 scalar two-terminal R/L graph identity**뿐이다. T1-E0, M0 periodic volume과 `C0-A1` circle interior는 명시된 manufactured scope에서만 통과했으며, 물리 correction block은 하나도 global 조립 승인을 받지 않았다. T1의 전체 수치와 소유권 판정은 [`T1_TRACE_ORACLE_RESULTS.md`](T1_TRACE_ORACLE_RESULTS.md), M0 경계 정정과 volume 결과는 [`T1_M0_SLAB_RESULTS.md`](T1_M0_SLAB_RESULTS.md), frozen A0 실패와 A1 circle-only 결과는 [`T1_CIRCLE_DTN_RESULTS.md`](T1_CIRCLE_DTN_RESULTS.md), finite/open negative result는 [`T1_M1_EQ0_RESULTS.md`](T1_M1_EQ0_RESULTS.md)에 고정한다.

## N0 — exact finite-route reduction

두 개의 독립 제조 예제로 확인했다.

### 다중 topology 제조 예제

- 입력: 11 nodes, 12 independent scalar cross-layer R/L edges, boundary 3개
- 포함 topology: dangling edges, 두 series chain, parallel pair, cycle
- 축약: dangling node 2개와 edge 2개 제거, degree-2 node 3개 contraction, retained node 6개와 reduced edge 7개
- owner ledger: 12/12 source owner가 정확히 한 번 보존됨
- 0 Hz, 100 kHz, 1/10/100/500 MHz, 1/2 GHz에서 direct full nodal solve와 비교
- frozen reproduction v1 최대 relative Frobenius Z error: `5.3338e-15`
- frozen reproduction v1 최대 absolute Z error: `1.7790e-14 Ω`
- 최대 reciprocity relative error: 약 `6.05e-17`
- 최소 `λmin(H(Z))`: `+4.14917e-2 Ω`
- `cond(Z)`: `1.723`

### analytic chain 제조 예제

- 41 points, 100 kHz–2 GHz
- reduced/unreduced relative Frobenius error: `7.09e-16`
- 최대 absolute error: `2.83e-16 Ω`
- closed-form analytic relative error: `9.19e-16`
- 최대 `GlobalMnaDiagnostics.scaled_saddle_condition_estimate`: `62.11`. 이는 `onenormest` 기반 scaled 1-norm estimate이며 사전 등록한 `κ2(A)`가 아니다. `κest·ε64≈1.38e-14`, `κest·rb≈1.77e-15`도 forward bound가 아니라 surrogate product다.
- reciprocity: `5.92e-16`; KCL: `1.37e-16`
- frozen reference-only graph reduction time: `0.000379 s`; host-dependent 참고값

`tests/test_finite_route_reducer.py`의 17 tests도 통과했다. 단, 이 reducer는 현재 production solve path에 연결되지 않고 test에서만 호출된다. mutual R/L/C, controlled source, correction interface 또는 multiterminal block이 들어오면 단순 series/tree rule은 더 이상 인증 범위가 아니다. 그런 변경마다 최종 full-port reduced/unreduced parity를 다시 계산한다.

첫 제조 시도에서 모든 node를 같은 layer로 둬 `finite-route edge ... does not cross conductor layers`가 발생했다. 이는 오류를 우회하지 않고 source topology 계약을 fail-closed 한 정상 동작이다.

## T1 — finite trace와 explicit return

T1 전체 판정은 `blocked`다. 단, 다음 manufactured subcase는 좁은 범위에서 독립 재현됐다.

- **T1-E0 `passed_canonical_lossless_only`:** zero-thickness centered stripline의 body-fitted finite-volume `C'`를 Cohn exact 식과 비교했다. `h/128` raw exact error 최대 `0.2691442%`(`≤0.2692%`), first-order Richardson exact error 최대 `0.00070%`, 별도 h/128 crop sweep의 padding 4h→8h 변화 최대 `2.12459e-6`, energy/charge mismatch 최대 `1.50e-12`였다. exact appendix 재실행의 one-process working-set reference는 약 `1,856 MiB`로 8 GiB보다 작았지만 process tree, private commit와 safety margin을 포함한 8 GB target gate 결과는 아니다.
- **T1-M0 `passed_periodic_1d_volume_only`:** periodic two-plate smooth-copper coupon에서 exact slab `coth` law를 제품 helper와 독립인 normalized linear FEM으로 재현했다. 12-frequency canonical의 fine raw surface error/mesh/phase max는 `0.073301%/0.219901%/0.041998°`, W0 12-case material/thickness withheld는 `0.126811%/0.380419%/0.072657°`였다. 10 mm exact coupon의 `Rdc=1.917545542 mΩ`, `Ldc=0.184306769 nH`, 2 GHz `R=46.039620 mΩ`, `L=0.129327423 nH`다. 이는 lateral-periodic `m=0` volume-only pass다. finite rectangle의 free-space `H2` contour는 side face와 edge field가 있어 이 target과 직접 비교하지 않으며, M0-only periodized kernel을 구현해 C0-A1을 억지로 승격하지 않는다.

source streaming metadata screen은 P1/P2에서 각각 `6,816/2,976`개 concrete-ref 후보를 찾았지만 return polygon connectivity와 terminal-to-return current path를 증명하지 않는다. P3/P4는 concrete Trace ref가 0이고 raw grammar가 routed trace와 plane/mesh topology를 구분하지 못하므로 source-faithful T1 후보로 승격하지 않았다.

finite length는 exact distributed line을 기본으로 한다. homogeneous `εr=4`, 2 GHz에서 0.889/0.900 mm는 `|βl|≈0.075`지만 4.2/10 mm는 `0.352/0.838`이다. nominal-π 최대 terminal-coefficient error는 각각 `0.093/0.095/2.120/13.975%`다. lumped replacement는 `max modal |γl|≤0.1`을 necessary screen으로 만족하고 exact terminal/full-port response gate도 별도로 통과해야 한다.

reduced differential two-port를 `(S0,R0,S1,R1)` absolute terminals로 단순 lift하면 global gauge 외에 terminal-plane common-mode null이 생긴다. frozen 1 GHz full-rank `Y2` probe의 lifted `Y4`는 rank 2였고 compile 뒤 solve에서 정확히 `saddle system is singular; topology has an unresolved island`로 차단됐다. DC pure-series case는 rank 1과 추가 null을 가지므로 rank 2를 broadband 일반 명제로 쓰지 않는다. 따라서 full partial common-mode operator 또는 명시적 balanced-projection/current-constraint adapter와 same-crop return/core partition이 없으면 T1을 global MNA에 stamp하지 않는다.

## S1 — finite-contact plane spreading

### rectangular coupon

7 mm × 3 mm copper rectangle, 35 µm, `σ=5.959e7 S/m`, 양 끝 1.0 mm × 1.6 mm finite contact를 사용했다.

| refinement | nodes | triangles | DC Z (mΩ) | 이전 level 대비 변화 |
|---:|---:|---:|---:|---:|
| 0 | 12 | 18 | 0.326219 | — |
| 1 | 41 | 72 | 0.440973 | 35.177% |
| 2 | 153 | 288 | 0.542273 | 22.972% |
| 3 | 593 | 1,152 | 0.614325 | 13.287% |
| 4 | 2,337 | 4,608 | 0.650661 | 5.915% |
| 5 | 9,281 | 18,432 | 0.665297 | 2.249% |
| 6 | 36,993 | 73,728 | 0.670758 | 0.821% |
| 7 | 147,713 | 294,912 | 0.672765 | 0.299% |

level 6→7만 보면 0.5% RMS/1% max gate를 통과하고 `compile_converged_tri_fem_sheet`도 `production_eligible=True`를 반환했다. 그러나 이는 helper 내부의 한 geometry attestation일 뿐 제품 승격이 아니다. 전체 h/h/2/h/4 연속 구간, crop, 세 개 이상의 source-range geometry, exact-minus-core interface와 owner ledger가 없으므로 S1 전체는 차단한다. level 7 solve는 약 32.6 s, convergence wrapper 전체는 약 75.6 s였다.

### annular analytic coupon 실패

`ln(b/a)/(2πσt)` 비교를 위한 circular annulus는 coarse level에서 analytic R보다 22.93% 낮았고, 첫 refinement에서 `MESH_CROSSES_VOID: refined triangle exits artwork`로 fail-closed 됐다. circular polygon boundary의 strict coverage/refinement 처리가 해결되기 전에는 annular analytic validation으로 사용할 수 없다.

## V1/V2 — via constitutive law와 isolated PEEC

`tests/test_via_peec.py`의 17 tests가 통과했다.

### V1 single filled via

길이 100 µm, 반지름 10 µm, `σ=5.959e7 S/m`의 단일 solid copper branch:

- analytic `Rdc = 5.341666155 mΩ`; 계산값이 float64 정밀도에서 일치
- analytic external partial L: `41.864707764 pH`
- low-frequency internal L: `5.000000000 pH`
- 1 Hz total L: `46.864707695 pH`
- 2 GHz: `R=19.727261 mΩ`, `L=43.315848 pH`
- 1 PHz total L: `41.866770 pH`, 즉 high-frequency limit에서 external L로 접근
- 모든 anchor에서 condition 1, reciprocity 0, KCL/equipotential/backward residual은 약 `2.3e-16` 이하, Hermitian Z는 양수

이는 solid-cylinder branch law를 검증하지만 return field가 없는 partial-inductance 결과다. 사전 등록한 central-via/coaxial-return **loop** V1을 대체하지 않는다.

### V2 five-via PWR/GND array

PWR 3개와 signed GND return 2개를 80 µm 길이, 12.5 µm 반지름으로 배치했다.

- DC current sharing: PWR 각각 `+1/3 A`, GND 각각 `−1/2 A`
- 100 kHz loop L: `24.3222 pH`; 2 GHz: `21.7522 pH`
- 2 GHz branch/terminal condition: `2.889` / `2.082`
- 2 GHz reciprocity error: 최대 `1.12e-16`; KCL/equipotential/backward residual: 최대 약 `1.81e-16`
- sampled `λmin(H(Z))`는 모두 양수
- compile 약 `0.00016 s`, frequency solve 약 `0.0003–0.0006 s`

그러나 `ViaPeecOwnership.global_mna_composable=False`이며, module은 pad, antipad, plane spreading, 실제 return geometry와 exact/core matrix/hash를 소유하지 않는다. isolated PEEC 결과를 현재 global MNA에 더하는 것은 금지한다.

## A1 — axisymmetric pad/antipad radial correction

동일 geometry에서 `Cfull-rz − Cvertical-columns`를 계산했다. uniform sheet manufactured identity에서는 correction 최대값이 약 `1.62e-27 F`로 0을 복원했다.

circular opening/foreign filled-via fixture의 P-terminal self correction:

| mesh/crop | cells | `ΔCpp` | 전체 실행 시간 |
|---|---:|---:|---:|
| h=10 µm, R=300 µm | 750 | 23.484 fF | 약 0.018 s |
| h=5 µm, R=300 µm | 3,000 | 26.504 fF | 약 0.073 s |
| h=2.5 µm, R=300 µm | 12,000 | 29.582 fF | 약 0.311 s |
| h=1.25 µm, R=300 µm | 48,000 | 31.003 fF | 약 1.365 s |
| h=5 µm, R=600/1,200 µm | 6,000/12,000 | 26.504 fF | 약 0.16/0.32 s |

floor 1 fF의 matrix norm으로 측정한 mesh 변화는 10→5 µm `11.97%`, 5→2.5 µm `10.52%`, 2.5→1.25 µm `4.734%`다. 마지막 eligible-element max도 `5.887%`로 0.5% RMS/1% max gate를 크게 넘는다. 반면 crop 300→600→1,200 µm 변화는 약 `1e-13` relative로 안정적이다.

raw Maxwell-C symmetry, row-sum, solve residual과 sampled PSD gate는 통과했다. 이 결과는 현재 cell-centred discretization의 주된 blocker가 crop이나 linear solve가 아니라 **mesh/model discretization**임을 보여준다. body-fitted/higher-order axisymmetric formulation 또는 독립 3-D reference가 필요하다.

## C1 — finite circular launch

기존 `SurfacePatchFinitePort`는 source footprint를 exact clipped area weights로 투영하고 partial/mixed-net footprint를 fail-closed 한다. circular disk에 대한 FFT pulse-BEM 보조 실험에서는:

- pitch `0.25/0.125/0.0625 µm`의 unknown 수: `2,528/10,048/40,216`
- successive scalar capacitance 변화: `0.0953%`, `0.3325%`
- exact footprint area relative error: `4.21e-16`
- Love small-gap asymptotic error: 세 finest pitch에서 `0.144%/0.239%/0.094%`
- 실행 시간: `0.215/1.546/6.100 s`
- 최대 estimated FFT workspace: 약 `23.47 MB`

required backward residual `||Ax-b||/(||A||||x||+||b||)`은 현재 보고되지 않는다. 기존 `||Ax-b||/max(||b||,1)` relative residual은 약 `1.6–1.74e-8`이며 backward-residual certificate를 대신할 수 없다. 더 중요한 점은 이 계산이 scalar disk capacitance이지 finite circular launch의 spreading/transfer impedance가 아니라는 것이다. fixed radius sweep의 `ln(1/a)` scaling, crop, triangular/MFDM 교차 검증, 기존 point launch 제거와 owner ledger가 없어 C1은 차단한다.

## 검증 명령과 환경

- Windows 10.0.19045
- Python 3.12.10
- pytest 9.0.3
- research starting HEAD: `3411c5a9d7766198a8a8bacd618fc0615798ab8d`

실행한 주요 회귀:

```powershell
python -m pytest tests/test_finite_route_reducer.py -q
# 17 passed

python -m pytest tests/test_via_peec.py -q
# 17 passed

python -m pytest tests/test_research_axisymmetric_electrostatics.py tests/test_surface_patch_plane.py -q
# 24 passed
```

최종 root 병렬 재실행에서 focused 12-module suite는 `178 passed in 6.59 s`, finite-route/FFT-BEM suite는 `22 passed in 1.86 s`였다. 이 wall time은 회귀 상태 확인용이며 성능 benchmark가 아니다. custom table을 재생성하는 exact 명령은 [`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)에 고정한다.

## 다음 R2 연구

1. failed M1-EQ0 collocation, G1 `passed_exterior_galerkin_only`, G2 `passed_pair_screen_only`와 100 kHz circle reciprocity/cancellation failure를 보존한다. 독립 A–v consistent-P1 boundary-Schur **100 kHz circle-only reference candidate**는 [`T1_AV_BOUNDARY_SCHUR_SPEC.md`](T1_AV_BOUNDARY_SCHUR_SPEC.md)에 `AV-BS1-CIRCLE preregistered_not_run`으로 동결했다. 다음은 standalone solver fixture의 별도 static audit·commit이며, h/h2/h4와 withheld radius review token 전에는 EQ0 mesh·crop 또는 planned G2 N512/2 GHz circle/EQ0 seed로 직행하지 않는다.
2. S1은 circular/void boundary-conforming refinement와 h/h/2/h/4 추정 오차를 먼저 해결한다.
3. V1/V2는 명시적 coax/ring return을 가진 2-D/3-D reference와 동일 crop의 exact-minus-core matrix를 만든다.
4. A1은 current cell-centred solver를 승격하지 않고 body-fitted/higher-order 후보를 비교한다.
5. C1은 circular source/return spreading Z와 radius-scaling coupon을 정의한다.
6. PowerSI의 factor-isolated coupon과 mesh/repeatability log가 확보되기 전 board correlation으로 이 blocker를 우회하지 않는다.
