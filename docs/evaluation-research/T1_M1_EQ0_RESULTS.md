# SPD Decap PI Evaluator v0.22.0 — T1-M1-EQ0 Finite/Open Results

최종 갱신: 2026-08-15 (Asia/Seoul)

이 문서는 결과를 보기 전에 동결한 M1-EQ0 full-contour 계약을 실제로 실행한 결과와 독립 `A_z–v` smoke를 보존한다. 제품 parser, solver, UI, 버전과 installer는 수정하지 않았다. 이 실행은 PowerSI board correlation이 아니며, 8 GB 노트북 production 성능 증거도 아니다.

## 판정

M1-EQ0의 전체 판정은 계속 **blocked**다. 다만 두 이산화 결과를 분리해 보존한다.

- `N={144,288,576}`, 7개 mandatory frequency, q10/q20, `r0={0.1,1,10} m`에서 complex response, panel convergence, quadrature, residual, condition, integrated current, terminal reciprocity와 terminal scalar `Z'loop` passivity는 통과했다.
- 그러나 사전 등록한 SAO signed dissipative-power identity `<=1e-8`은 fine mesh의 7/7 frequency에서 실패했다. worst mismatch는 2 GHz의 `1.58517e-4`다.
- 실패 뒤 C0를 바꾸거나 raw matrix를 대칭화하거나 gate를 완화하지 않았다. 따라서 이 결과를 finite/open M1 pass로 승격하지 않는다.
- 사전 등록한 `M1-EQ0-G1` direct exterior Galerkin을 같은 contour에서 실행한 결과 exterior structure, quadrature, `r0`, terminal response와 boundary power는 통과했다. 이 부분만 `passed_exterior_galerkin_only`다.
- 그러나 G1이 그대로 사용한 collocation interior `Yw=WYs`는 fine에서 weighted reciprocity `1.69946%–7.12361%`로 전 주파수 실패했고 100 kHz와 1 MHz에서 raw Hermitian passivity도 실패했다. 따라서 전체 M1은 `BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY`로 차단한다.
- G2 pair screen은 `passed_pair_screen_only`지만 100 kHz circle의 mandatory operator gate는 실패했다. `N=128` cancellation condition은 `2.91315e-8`, `N=256` raw `Yw` reciprocity/cancellation은 `1.41197e-8/1.63755e-7`로 각각 `1e-8` gate를 넘었다. runner는 fail-closed로 종료됐고 planned G2 2 GHz circle row, G2 `N=512`, G2 EQ0 seed는 실행하지 않았다.
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
| terminal scalar `Z'loop` passivity | min `Re Z'=3.83405346 Ω/m` | nonnegative floor | pass |
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

## M1-EQ0-G1 direct Galerkin exterior

G1은 결과를 보기 전에 같은 geometry, endpoint hashes, frequency, `N={144,288,576}`, current basis와 gate를 그대로 유지하고 exterior owner만 energy-consistent pulse-Galerkin operator로 교체하도록 동결했다.

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

### G1 raw q20 response

단위는 `Ω/m`이고 canonical `r0=1 m`다. G1은 기존 collocation negative result를 덮어쓰지 않는다.

| frequency | N=144 | N=288 | N=576 |
|---:|---:|---:|---:|
| 100 kHz | `3.834060065211 + j0.157989290887` | `3.834976808358 + j0.158589912102` | `3.835212680631 + j0.158744120667` |
| 1 MHz | `3.852103911757 + j1.582979660193` | `3.854321842237 + j1.585164193881` | `3.854888639403 + j1.585725839089` |
| 10 MHz | `4.961883304978 + j15.071586796513` | `4.968178270343 + j15.077131202310` | `4.969778690848 + j15.078631400726` |
| 100 MHz | `14.596668814106 + j128.274769816497` | `14.614647832186 + j128.306020625090` | `14.618992113881 + j128.314925437738` |
| 500 MHz | `32.500570096067 + j601.434337262636` | `32.534301360113 + j601.539698341439` | `32.540867638474 + j601.572164611766` |
| 1 GHz | `45.956857766409 + j1183.892300997648` | `46.003607212496 + j1184.090917910282` | `46.008833807283 + j1184.154376140652` |
| 2 GHz | `65.004812495836 + j2340.881053914836` | `65.087568409877 + j2341.262625651998` | `65.088435397829 + j2341.392170234187` |

### G1 exterior gate audit

| gate | result | threshold | status |
|---|---:|---:|---|
| fine `GG` q10→q20 pair-normalized max | `2.02993e-15` | `<=1e-10` | pass |
| fine `GG` q10→q20 Frobenius | `2.10724e-16` | `<=1e-10` | pass |
| independent reversed-pair max | `6.31236e-16` | `<=1e-12` | pass |
| fine raw `GG` transpose defect | `9.95287e-17` | `<=1e-12` | pass |
| fine `r0` rank-one identity max | `6.33326e-16` | `<=1e-8` | pass |
| medium→fine complex relative RMS | `0.0104184%` | `<=0.5%` | pass |
| medium→fine complex relative max | `0.0191430%` | `<=1%` | pass |
| medium→fine max phase | `0.00417655°` | `<=0.25°` | pass |
| fine final `Z'` q10→q20 relative max | `9.75625e-9` | `<=0.1%` | pass |
| fine final `Z'` q10→q20 max phase | `5.58821e-7°` | `<=0.25°` | pass |
| fine boundary dissipative-power mismatch | max `1.06982e-14` | `<=1e-8` | pass |
| fine terminal raw reciprocity | max `8.60120e-16` | `<=1e-8` | pass |
| fine integrated-current residual | max `3.27623e-15` | `<=1e-10` | pass |
| fine exterior `AE κ1u` | max `1.03733e-12` | `<=1e-8` | pass |
| fine terminal scalar `Z'loop` passivity | min `Re Z'=3.83521 Ω/m` | nonnegative floor | pass |

Fine `GG_q20` checksum은 `f85a15e7325fc0bc33b040261b5c03455f7f2e1f881d8283695e78efeb654121`이다. G1과 collocation fine response의 complex relative 차이는 RMS `2.06108e-5`, max `3.86006e-5`이고 phase max `0.00145710°`다. 작은 response 변화가 collocation의 전력 결함을 정당화하지 않으며, G1은 energy-consistent exterior owner를 독립적으로 확정한 결과다.

### G1 interior prospective gate

Exterior를 교체해도 interior는 frozen collocation `Ys`를 사용했다. `Yw=WYs`의 단위는 `S·m`이고 아래 수치는 어떤 대칭화나 eigenvalue clipping 전 raw 값이다.

| frequency | weighted reciprocity | raw min `λ(H(Yw))` (`S·m`) | tolerance (`S·m`) | reciprocity | passivity |
|---:|---:|---:|---:|---|---|
| 100 kHz | `7.12361e-2` | `-2.96377e-5` | `7.22576e-12` | fail | fail |
| 1 MHz | `4.08228e-2` | `-1.79591e-6` | `7.22230e-12` | fail | fail |
| 10 MHz | `3.07258e-2` | `+3.85126e-7` | `6.45942e-12` | fail | pass |
| 100 MHz | `2.91663e-2` | `+5.63067e-7` | `2.58488e-12` | fail | pass |
| 500 MHz | `2.47707e-2` | `+5.86124e-7` | `1.22951e-12` | fail | pass |
| 1 GHz | `2.10899e-2` | `+5.88976e-7` | `1.00000e-12` | fail | pass |
| 2 GHz | `1.69946e-2` | `+5.89977e-7` | `1.00000e-12` | fail | pass |

이 결함은 N=144/288/576에서 0으로 단조 수렴하지 않는다. 예를 들어 2 GHz weighted reciprocity는 `1.06919e-2 → 1.38727e-2 → 1.69946e-2`다. reduced terminal `2×2`가 reciprocal/passive라는 사실로 hidden interior mode를 승인하지 않는다. G1 판정은 **`passed_exterior_galerkin_only`**, 전체 상태는 **`BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY`** 다.

### G1 resource/provenance

- fine 7-frequency q20/세 `r0` 단독 run wall: `405.2 s`
- fine 7-frequency q10/q20 canonical `r0` parity run wall: `558.7 s`
- observed process-only peak working set/private: `120.906/1348.285 MiB`
- full frequency/mesh/reference matrices는 케이스 사이에 보존하지 않았다.

이는 현재 host의 process-only bounded oracle evidence이며 process-tree, parser/reference coexistence, 8 GB laptop 또는 production 성능 증거가 아니다.

## 다음 후보: M1-EQ0-G2 interior Galerkin

G2는 G1 exterior를 그대로 보존하고 `P/U/Pout/Uout` interior trace operator를 target-tested Galerkin 약형으로 다시 이산화한다. collocation matrix를 사후 평균하거나 negative eigenvalue를 clipping하는 방식은 허용하지 않는다. exact 식, singular quadrature, 독립 pair 방향, raw weighted reciprocity/passivity와 A–v 교차 gate는 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)와 [`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)에 결과를 보기 전에 동결했다.

### G2 pair screen 결과

사전등록 커밋 `ddec4fb` 뒤 100 kHz와 2 GHz에서 q20 canonical/q40 parity pair screen만 실행했다. 한 conductor의 frozen seed `N=72`에서 각 material/frequency마다 self `72`, touching `144`, routed-near `196`, regular tensor `4,772` directed pair가 집계돼 합계 `5,184=72²`였고 maximum recursion depth는 `2<=8`이었다.

| frequency | material | max `P` q-change | max `U` q-change | q gate margin |
|---:|---|---:|---:|---:|
| 100 kHz | conductor | `6.36567e-11` | `8.55154e-14` | `1.5709e7×` |
| 100 kHz | background | `5.31830e-16` | `3.67183e-16` | `1.8803e12×` |
| 2 GHz | conductor | `7.52822e-6` | `1.71021e-9` | `132.834×` |
| 2 GHz | background | `7.40339e-16` | `3.50067e-16` | `1.3507e12×` |

최악값은 2 GHz conductor self-`P`의 `7.52822e-6=0.000752822%`로 `0.1%` gate의 `0.753%`만 사용했다. raw `P` transpose defect의 전체 최대는 `1.88876e-16`, gate margin은 `5,294×`였다. 두 frequency 모두 `mandatory_stage_pass=true`다.

process-only wall/peak working-set/private bytes는 100 kHz `25.4061 s / 58.6055 MiB / 1295.2305 MiB`, 2 GHz `24.9042 s / 58.7656 MiB / 1295.3125 MiB`다. 외부 process-tree/system-headroom을 내장 측정한 값이 아니므로 8 GB 또는 production resource pass로 사용하지 않는다.

pair 단계만 `passed_pair_screen_only`다. q20과 q40가 함께 잘못된 continuous sign/operator로 수렴할 수도 있으므로 analytic circle DtN, `Yw` reciprocity/passivity, cancellation, terminal power 또는 full G2를 승인하지 않는다.

### G2 circle 100 kHz fail-closed 결과

pair replay를 같은 PowerShell session에서 다시 통과시킨 뒤 frozen `N={128,256}`, q20 canonical/q40 parity, 100 kHz circle만 실행했다. planned G2 2 GHz circle row를 포함한 stage command였지만 첫 frequency의 mandatory gate가 실패하자 Python이 nonzero exit했고 G2 `N=512`와 G2 EQ0 seed도 실행되지 않았다.

| N | q | analytic max/RMS | max phase | raw `Yw` reciprocity | raw min `λ(H(Yw))` (`S·m`) | cancellation condition | operator gate |
|---:|---:|---:|---:|---:|---:|---:|---|
| 128 | 20 | `0.390062% / 0.216023%` | `0.0170973°` | `2.40109e-9` | `+7.73614e-6` | `2.91315e-8` | fail |
| 128 | 40 | `0.390062% / 0.216023%` | `0.0170973°` | `2.50717e-9` | `+7.73614e-6` | `2.91315e-8` | fail |
| 256 | 20 | `0.0985103% / 0.0543947%` | `0.00424127°` | `1.41197e-8` | `+1.94722e-6` | `1.63755e-7` | fail |
| 256 | 40 | `0.0985103% / 0.0543947%` | `0.00424127°` | `1.41083e-8` | `+1.94722e-6` | `1.63755e-7` | fail |

analytic comparison, q20→q40 parity, `N=128→256` mesh change, `P/Pout` transpose와 backward residual/condition, 그리고 raw Hermitian passivity는 모두 통과했다. q parity의 worst relative change는 `2.81068e-12`, mesh worst relative/RMS/phase change는 `0.290419%/0.161108%/0.0128560°`다. `N=128`은 cancellation만 실패했고 `N=256`은 raw reciprocity와 cancellation이 함께 실패했다. 따라서 quadrature order나 검증한 analytic low modes를 원인이라고 볼 수 없으며 gate를 완화할 근거도 없다.

process-only wall/peak working-set/private의 row maximum은 `210.401 s / 88.969 MiB / 1328.805 MiB`다. 이는 process-tree 또는 8 GB product proof가 아니다.

저주파에서 `m>=1`인 원형 mode의

`Jm'(z)/Jm(z)=m/z-z/[2(m+1)]+O(z^3)`

때문에 conductor와 background DtN은 각각 큰 공통 Laplace 항을 가진다. G2는 두 큰 값을 별도로 이산화한 뒤 빼므로, 유한한 차이보다 cancellation amplification이 커지고 refinement에서 full-space reciprocity가 악화될 수 있다. 이는 frozen 결과에 대한 원인 가설이며, 사후 대칭화·higher precision·gate 완화로 pass를 만들지 않는다.

현재 전체 상태는 **`BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY__G2_PAIR_PASSED_CIRCLE_100KHZ_RECIPROCITY_CANCELLATION_FAIL`**이다.

## Exact next starting point

1. **완료:** G1 direct exterior Galerkin을 `N={144,288,576}`, 7 frequencies, q10/q20, 세 `r0`에서 실행해 exterior-only pass와 interior blocker를 분리했다.
2. **동결 완료:** G2 interior Galerkin의 약형, self/touching/non-touching singular quadrature, basis/order, raw reciprocity/passivity, q20/q40와 no-retuning rule을 exact reproduction block에 고정했다.
3. **완료/제한 통과:** pair screen은 `passed_pair_screen_only`다.
4. **실패 동결:** 100 kHz circle의 analytic/q/mesh/passivity는 통과했지만 raw reciprocity/cancellation은 실패했다. planned G2 2 GHz circle, G2 `N=512`, G2 EQ0 seed로 진행하지 않는다.
5. 다음 독립 reference candidate는 two-DtN subtraction이 없는 A–v volume-FEM boundary-Schur다. [`T1_AV_BOUNDARY_SCHUR_RESULTS.md`](T1_AV_BOUNDARY_SCHUR_RESULTS.md)의 H0는 pre-factor sparse gate에서 실패했지만 H1 coarse `h`와 H2 refined `h2`는 stage-only pass다. H4-P0 assembly-only certificate와 [`H4-P0R manifest-only contract`](T1_AV_BOUNDARY_SCHUR_H4_P0R_PREREG.md) static review는 통과했지만 executable/token/factorization은 pending이며 H4 physics는 금지된다. H4 physics와 두 circle radius가 모두 통과한 뒤에만 EQ0 crop `2/4/8Deff`를 사전 등록한다.
6. production SAO 후보는 Hamiltonian Schur 또는 four-operator symmetric Calderón/Steklov–Poincaré trace/flux formulation으로 별도 사전 등록한다. raw failure는 보존하며 post-symmetrization, clipping 또는 result-driven tuning을 금지한다.
7. 독립 A–v와 새 SAO가 모두 통과하기 전 T1-F, board source owner 또는 PowerSI correlation으로 우회하지 않는다.
