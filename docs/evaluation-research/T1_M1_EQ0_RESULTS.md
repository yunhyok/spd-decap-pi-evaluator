# SPD Decap PI Evaluator v0.22.0 — T1-M1-EQ0 Finite/Open Results

최종 갱신: 2026-08-15 (Asia/Seoul)

이 문서는 결과를 보기 전에 동결한 M1-EQ0 full-contour 계약을 실제로 실행한 결과와 독립 `A_z–v` smoke를 보존한다. 제품 parser, solver, UI, 버전과 installer는 수정하지 않았다. 이 실행은 PowerSI board correlation이 아니며, 8 GB 노트북 production 성능 증거도 아니다.

## 판정

M1-EQ0의 현재 판정은 **`BLOCKED_SAO_BOUNDARY_POWER_IDENTITY`** 다.

- `N={144,288,576}`, 7개 mandatory frequency, q10/q20, `r0={0.1,1,10} m`에서 complex response, panel convergence, quadrature, residual, condition, integrated current, terminal reciprocity와 scalar passivity는 통과했다.
- 그러나 사전 등록한 SAO signed dissipative-power identity `<=1e-8`은 fine mesh의 7/7 frequency에서 실패했다. worst mismatch는 2 GHz의 `1.58517e-4`다.
- 실패 뒤 C0를 바꾸거나 raw matrix를 대칭화하거나 gate를 완화하지 않았다. 따라서 이 결과를 finite/open M1 pass로 승격하지 않는다.
- independent body-fitted `A_z–v` 2 GHz smoke는 consistent P1 mass power에서 `1.377e-9`를 통과했지만, 한 crop·한 mesh뿐이므로 reference convergence pass가 아니다.

`C0-A1`은 계속 `passed_circle_interior_only`, M0는 `passed_periodic_1d_volume_only`다. T1 전체, global composition, PowerSI accuracy와 product status는 모두 `blocked`다.

## 동결 실행 계약

실행 전에 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)와 [`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)에 다음을 동결했다.

- signal `[-125,+125]×[50,85] µm`, return `[-125,+125]×[-35,0] µm`
- vacuum, `σ=59.6 MS/m`, `b=(+1,-1)^T`, peak `Iloop=1 A`
- exact rational full-contour hashes와 `N={144,288,576}`; symmetry reduction 없음
- positive frequencies `100 kHz, 1/10/100/500 MHz, 1/2 GHz`; DC analytic anchor
- `kp=sqrt(ωµ0(ωε0−jσ))`, `kb=ωsqrt(µ0ε0)`, `C0-A1`, q20 canonical/q10 parity
- analytic straight-panel log exterior `G0`, `r0=1 m` canonical과 `{0.1,10} m` invariance
- deterministic row-max→column-max scaling, LU/condition certificate, ordinary transpose reciprocity와 conjugate-transpose peak power

Panel hashes는 seed `f65cddcf5d45187006ffc5e9eaf1c5624fa14e3849ce435c2601825da579743c`, medium `35d1f81c87cb97372c543db98e2063102b8823d5af0e70b8e5c4432966b77b02`, fine `5b964069b3bae0965ff3bfcc95348b98656fca9a48da3a998a0510892cd17d0e`다.

## Raw q20 response

단위는 `Ω/m`이며 어떤 사후 보정도 적용하지 않았다. DC analytic anchor는 `3.835091083413231 Ω/m`다.

| frequency | N=144 | N=288 | N=576 |
|---:|---:|---:|---:|
| 100 kHz | `3.834053463615 + j0.158184624071` | `3.834975908386 + j0.158639746252` | `3.835212652654 + j0.158756770726` |
| 1 MHz | `3.852408925876 + j1.584907368684` | `3.854401163605 + j1.585652450588` | `3.854909131010 + j1.585848893129` |
| 10 MHz | `4.968287497061 + j15.078809214932` | `4.969808516658 + j15.078947366298` | `4.970190199004 + j15.079085534969` |
| 100 MHz | `14.615612295355 + j128.305918968187` | `14.619424998256 + j128.313864804129` | `14.620192781096 + j128.316888978761` |
| 500 MHz | `32.546162700454 + j601.527411114261` | `32.545448213559 + j601.563107518197` | `32.543625955282 + j601.577988224176` |
| 1 GHz | `46.022127005234 + j1184.051081825892` | `46.018936501741 + j1184.131276657275` | `46.012510898854 + j1184.164433205674` |
| 2 GHz | `65.099077088956 + j2341.157078022503` | `65.108449104912 + j2341.334567603666` | `65.093071185382 + j2341.410241041615` |

## Gate audit

아래 auxiliary residual/reciprocity/current 값은 최초 frozen 실행의 원 solve/per-column 집계다. [`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)의 standalone block은 deterministic equilibrated solve와 matrix-wide norm을 사용해 raw `Z'`, power, q와 `r0`를 독립 재현하지만 auxiliary 숫자는 각각 최대 backward `5.89709e-16`, terminal reciprocity `7.66111e-16`, fine 2 GHz current `2.67497e-15`로 약간 다르다. 두 정의 모두 threshold에 큰 여유가 있으며 mandatory power 실패 판정은 동일하다.

| gate | result | threshold | status |
|---|---:|---:|---|
| medium→fine log-weight RMS | `0.00703814%` | `<=0.5%` | pass |
| medium→fine max | `0.0130658%` | `<=1%` | pass |
| medium→fine max phase | `0.00159923°` | `<=0.25°` | pass |
| fine q10→q20 max | `9.71910e-9` | `<=0.1%` | pass |
| fine q10→q20 max phase | `5.56704e-7°` | `<=0.25°` | pass |
| max solve backward residual | `5.91481e-16` | `<=1e-10` | pass |
| max equilibrated `κ1u` | `9.21355e-13` | `<=1e-8` | pass |
| terminal raw reciprocity | `7.86166e-16` | `<=1e-8` | pass |
| integrated-current residual | `6.66257e-16` | `<=1e-10` | pass |
| zero-sum residual | `5.55121e-16` | `<=1e-10` | pass |
| `r0` invariance | `9.59635e-16` | `τinv=4.60678e-11` | pass |
| scalar passivity | min `Re Z'=3.83405346 Ω/m` | nonnegative floor | pass |
| SAO boundary dissipative power | max `1.58517e-4` | `<=1e-8` | **fail** |

Fine dissipative-power mismatch는 frequency 순서대로 다음과 같다.

| frequency | relative mismatch |
|---:|---:|
| 100 kHz | `3.719e-8` |
| 1 MHz | `3.596e-6` |
| 10 MHz | `8.461e-5` |
| 100 MHz | `9.414e-5` |
| 500 MHz | `1.241e-4` |
| 1 GHz | `1.384e-4` |
| 2 GHz | `1.585e-4` |

2 GHz mismatch는 `N=144: 2.26760e-3`, `N=288: 6.180e-4`, `N=576: 1.585e-4`로 약 2차 수렴하지만 mandatory fine gate에는 네 자릿수 이상 부족하다. fine `r0=1 m`의 exterior weighted reciprocity defect `||WG0−(WG0)^T||F/||WG0||F`는 `2.29761e-4`이며 power mismatch와 같은 규모다.

## 실패 원인 분리

현재 exterior는 field collocation operator

`Gc[m,n] = ∫γn g0(rm,r') ds'`, `W=diag(ℓm)`

를 사용한다. `B=WGc=S+K`, `S=(B+B^T)/2`, `K=(B−B^T)/2`로 두면 SAO equation과 `I=Q^T WJ`에서 terminal과 boundary real-power 차이는 skew 항

`Re[-jωµ0 J^H B J] = ωµ0 Im(J^H K J)`

으로 정확히 귀속된다. 즉 `WGc`가 energy-symmetric가 아닌 한 solve residual과 terminal reciprocity가 machine precision이어도 boundary dissipative power가 보존되지 않는다.

원인 확인용으로만 `W^-1(WGc+(WGc)^T)/2`를 넣은 독립 seed probe에서는 mismatch가 100 kHz `5.20e-7→3.5e-16`, 100 MHz `2.0525e-3→1.1e-15`, 2 GHz `2.0099e-2→3.1e-15`로 사라졌다. 이는 attribution control이지 허용되는 수정이 아니다. 결과를 본 뒤의 matrix symmetrization은 계속 금지한다.

현재 pulse-collocation interior `Ys`의 weighted ordinary-reciprocity diagnostic도 fine에서 최대 `7.12361e-2`다. frozen mandatory reciprocity gate는 authoritative terminal `Z'partial/Z'loop`에 적용됐으므로 이를 과거 gate의 추가 실패로 소급하지 않는다. 판정은 `terminal_reciprocity: pass`, `interior_weighted_reciprocity: diagnostic_fail_non_gated`다. 다만 equal-geometry의 reduced 2×2 terminal reciprocity가 통과한 사실만으로 hidden mode를 승인하지 않으며, exterior correction 뒤 prospective weak-operator reciprocity/passivity 계약을 별도로 재검사한다.

따라서 이 실패는 Hankel range, `C0-A1` circle interior, branch, `r0`, current aggregation 또는 ill-conditioning 실패로 분류하지 않는다. 상태 코드는 `BLOCKED_SAO_BOUNDARY_POWER_IDENTITY`; 수치 원인 후보는 `blocked_sao_discrete_power_collocation`이다.

## Independent A–v smoke

SAO 출력과 독립적으로 body-fitted `4Deff` P1 `A_z–v` saddle을 2 GHz에서 한 번 실행했다.

| item | value |
|---|---:|
| crop | `x=[-1125,+1125] µm`, `y=[-1035,+1085] µm` |
| mesh | `93×206` nodes, `18,564` free `Az`, `37,720` triangles |
| copper normal spacing | `δ/2=0.728873244 µm` |
| central tangential spacing | `5 µm` — smoke only |
| `Z'loop` | `67.0262639174 + j2321.0767997 Ω/m` |
| saddle backward residual | `8.238e-25` |
| current-constraint residual | `8.062e-13` |
| consistent P1 mass dissipative-power mismatch | `1.377e-9` |
| magnetic reactive-power mismatch | `9.694e-13` |
| wall | `35.8 s` |

consistent mass form은 각 copper triangle에서 `0.5 σ E_e^H M_e E_e`, `M_e=A_e/12 [[2,1,1],[1,2,1],[1,1,2]]`를 사용했다. centroid field approximation의 과거 `6.728e-2` mismatch는 폐기했다.

Fine SAO와 이 unconverged A–v smoke의 2 GHz 차이는 A–v를 denominator로 둔 `|ZAv−ZSAO|/|ZAv|=0.8796186%`, `|||ZAv|−|ZSAO|||/|ZAv|=0.8729601%`, phase difference `0.0616254°`다. 그러나 A–v는 condition estimate, `h/h2/h4`, crop `2/4/8Deff`와 resource certificate가 없으므로 SAO accuracy pass 또는 cross-method convergence 근거로 쓰지 않는다.

## Resource evidence

- full 3-level/7-frequency SAO run: `110.842 s`
- fine 2 GHz q20+q10+`r0` replay: `11.895 s`
- largest-level replay process peak working set: `334.219 MiB`
- peak pagefile/commit counter: `1590.961 MiB`; measured private bytes `1453.297 MiB`

이는 현재 host의 process-only evidence다. target i9-12900H/8 GB의 process-tree, parser/reference coexistence와 system headroom을 검증하지 않았으므로 8 GB production pass가 아니다.

## 다음 후보: M1-EQ0-G1 direct Galerkin exterior

다음 실행은 같은 geometry, endpoint hashes, frequency, `N={144,288,576}`, current basis와 gate를 그대로 유지하고 exterior owner만 energy-consistent pulse-Galerkin operator로 교체한다.

`GG[m,n] = ∫γm ∫γn g0(r,r') ds' ds`, `GE=W^-1 GG`.

이는 결과 matrix를 대칭화하는 절차가 아니다. 첫 oracle에서는 symmetric Green kernel의 두 panel-pair orientation을 독립 계산해 raw transpose defect를 검사한다. 그 parity가 통과한 뒤에만 동일 physical owner의 unordered pair cache를 허용한다. self term은

`GG[m,m] = ℓm²/(2π) [ln(ℓm/r0)−3/2]`

를 사용한다. weak exterior equation은

`W E = jωµ0 GG J + W QV`, `J=Ys E`

이며 `AE=W−jωµ0 GG Ys`, `Eresp=solve(AE,WQ)`, `Jresp=Ys Eresp`, `Kc=Q^T W Jresp` 순서로 조립한다. `GG`를 collocation 식의 `I−jωµ0 Ys GG`에 그대로 넣지 않는다.

non-touching pair는 deterministic tensor Gauss를 사용한다. shared endpoint `c`에서 `ri(u)=c+uℓi ti`, `rj(v)=c+vℓj tj`로 두고 Duffy radial coordinate를 analytic 적분한

`GGij = ℓiℓj/(4π) ∫0^1 [ln(|ℓi ti−ηℓj tj|/r0)+ln(|ηℓi ti−ℓj tj|/r0)−1] dη`

를 q10/q20 Gauss로 계산한다. q10→q20을 먼저 실행하고 필요하면 결과를 보기 전에 정한 q40 withheld 확인만 허용한다. reference-radius shift `GG(r0')−GG(r0)=−ln(r0'/r0)/(2π) ℓℓ^T`도 raw identity로 검사한다.

추가 gate는 다음과 같다.

- independently reversed panel-pair integral parity `<=1e-12`; 첫 실행에서 transpose 복사 금지
- raw `||WGE−(WGE)^T||F/||WGE||F <=1e-12`
- exterior quadrature pair scale `smn=max(|GG10,mn|,|GG20,mn|,ℓmℓn/(2π))`; `maxmn |GG20−GG10|/smn <=1e-10`와 Frobenius relative change `<=1e-10`
- 기존 `r0`, response, residual, condition, current, terminal reciprocity/passivity gate 전부 유지
- boundary dissipative-power mismatch `<=1e-8`
- fine q10/q20 final `Z'` magnitude/phase parity도 기존 `0.1%/0.25°` gate로 별도 판정
- prospective `Yw=WYs` `[S·m]`, `Yw,floor=max(1e-12 S·m,1e-10 max|Yw,mn|)`, `||Yw−Yw^T||F/max(||Yw||F,N Yw,floor) <=1e-8`
- prospective `H(Yw)=(Yw+Yw^H)/2`의 raw `λmin >= -max(Yw,floor,1e-9||Yw||2)`; Hermitian part 평가는 operator 수정이 아님. 어느 쪽이든 실패하면 `P/U/Pout/Uout`도 target-tested Galerkin화하기 전 production promotion 차단

G1을 실행하기 전 상태는 `preregistered_G0_galerkin_pending`이다. G1은 먼저 exterior power 원인을 격리하는 diagnostic이며, 모든 mandatory gate, prospective interior weak reciprocity와 converged A–v가 통과하기 전에는 `oracle_pass`로 바꾸지 않는다. dense ceiling `N<=576`과 순차 frequency/level 해제, process-tree 4 GiB 목표·private/commit 5 GiB stop policy는 유지한다.

## Exact next starting point

1. **완료:** M1-EQ0-G1 independent-pair Galerkin log operator의 self/non-touching/analytic-radial Duffy, weak assembly, normalization과 prospective `Yw` gate를 [`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)에 결과 전에 고정했다.
2. 기존 full contour에서 q10/q20 pair-integral parity, weighted symmetry와 `r0` invariance를 실행한다.
3. 같은 3×7 SAO sweep을 재실행하고 raw power와 hidden-mode interior reciprocity를 함께 판정한다.
4. A–v는 consistent P1 mass form으로 `h/h2/h4`, crop `2/4/8Deff`, condition과 process-tree resource를 채운다.
5. 두 방법이 모두 통과하기 전 T1-F, board source owner 또는 PowerSI correlation으로 우회하지 않는다.
