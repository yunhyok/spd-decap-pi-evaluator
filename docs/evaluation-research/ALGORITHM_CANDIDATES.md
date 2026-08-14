# Algorithm Candidates and Literature Map

최종 갱신: 2026-08-15

## 문제 정의

현행 layerwise 경로는 개념적으로 다음 sparse nodal operator를 계산한다.

\[
Y(\omega)=\sum_k E_k^T Y_{gap,k}(\omega)E_k + Y_{via}(\omega)+Y_{term}(\omega)
\]

\[
Y_r(\omega)v=b,\qquad Z_{pp}=b^Tv
\]

동일 layer에서 연결된 artwork를 equipotential component로 축약하고, adjacent-layer overlap Maxwell capacitance와 via self-R/L, termination을 조립한다. 이 구조는 source topology를 보존하지만 다음의 분산 물리를 충분히 표현하지 않는다.

1. plane/trace의 nonuniform lateral current와 spreading
2. 긴/좁은 trace의 distributed RLGC와 smooth-copper skin/proximity
3. via return path, mutual inductance, dense cluster interaction
4. pad/antipad field와 port current crowding
5. conductor roughness와 causal material dispersion
6. full multiport transfer/eigenmode behavior

현재 loaded rail의 15–17 dB 수준 오차는 numerical acceleration보다 이 model-form gap을 먼저 해결해야 함을 보여준다.

## 후보 순위

| 순위 | 후보 | 정확성 잠재력 | laptop 잠재력 | 주된 위험 | 현재 판단 |
|---:|---|---|---|---|---|
| 1 | adaptive triangular/MFDM domain decomposition | 높음 | 높음 | interface rank, mesh ownership | 주 경로 |
| 2 | local via/trace/pad/antipad replacement block | 매우 높음 | 높음 if bounded | double counting, template generality | 주 경로와 결합 |
| 3 | exact route-graph reduction | 동일 물리 | 높음 | terminal/cycle 손실 | 즉시 검증 후보 |
| 4 | passive projection MOR | physics가 맞을 때 높음 | 매우 높음 | mutable decap port explosion | R4 이후 |
| 5 | iterative/recycled/hierarchical field solve | 동일 물리 | 잠재적으로 높음 | resonance/nullspace convergence | subdomain 한정 연구 |
| 6 | adaptive frequency sampling/passive fitting | 동일 물리 | 높음 | peak 누락, nonpassive fit | 마지막 가속 단계 |

### 2026-08-14 oracle evidence update

- exact route-graph reduction은 independent scalar two-terminal R/L 제조 예제에서 machine-precision parity와 complete owner ledger를 통과했다. 따라서 후보 3은 **검증된 제한 범위의 첫 가속 primitive**로 유지한다. production integration이나 mutual/multiterminal 적용 승인은 아니다.
- 기존 S1/A1/C1 kernel은 부분 invariant를 통과했지만 strict physical correction gate에는 미달했다. 특히 A1은 마지막 mesh 변화가 4.73%다. 따라서 후보 1/2의 방향은 유지하되 현행 discretization을 그대로 production에 연결하지 않는다.
- isolated via PEEC는 빠르고 수치적으로 안정적이지만 exact-minus-core ownership이 없어 additive global stamp로 사용할 수 없다. 후보 2는 explicit return/crop/core subtraction이 있는 replacement block으로만 진행한다.
- source manifest가 P3/P4 missing trace width, 모든 pair의 plating/fill/roughness unknown, P1/P2 antipad preservation gap을 확인했다. 후보 비교에서 이 값을 fit parameter로 숨기는 경로는 기각한다.
- T1의 Cohn lossless `C'`와 periodic plate `R/L`은 independent 1-D FEM까지 manufactured 범위에서 통과했다. finite/open 2-D 후보는 SAO–CIM이며 A–v/3-D PEEC로 교차검증한다. M1 collocation power failure는 immutable이고 G1은 `passed_exterior_galerkin_only`다. G2 pair screen은 `passed_pair_screen_only`지만 circle 100 kHz에서 N128 cancellation `2.91315e-8`, N256 raw `Yw` reciprocity/cancellation `1.41197e-8/1.63755e-7`로 fail-closed 됐다. two-DtN subtraction이 없는 독립 A–v volume-FEM boundary-Schur reference candidate는 [`T1_AV_BOUNDARY_SCHUR_SPEC.md`](T1_AV_BOUNDARY_SCHUR_SPEC.md)에 고정했다. H0 primary-h는 prereg sparse-pattern 오류로 factor 전에 실패했지만, 1,920개 cyclic diagonal을 topology-tagged exact-zero owner로 정규화한 H1 coarse `h`는 `passed_AV_BS_h_stage_only_pending_h2_review`다. fine analytic/mesh convergence/final-circle은 아직 `null`이며 h2는 별도 사전등록 전 금지한다.
- production SAO 후보는 같은 pulse 공간의 `P^-1U` 두 개를 독립 구성해 빼는 G2로 복귀하지 않는다. Knockaert–De Zutter–Lippens–Rogier의 [Hamiltonian Schur DtN](https://doi.org/10.1016/j.wavemoti.2007.07.004)과 Costabel–Stephan의 [strongly elliptic four-operator transmission system](https://doi.org/10.1016/0022-247X(85)90118-0)을 규범 후보로 두고 trace/flux dual space, Gram map, weak hypersingular operator과 discrete inf-sup를 결과 전에 고정한다.
- SAO interior의 사전 등록 `C0-A0` 저주파 `10^6` switch는 작은 원에서 실패했다. `C0-A1` fixed `C0=1` direct/scaled Hankel은 canonical full-condition과 W1/W3 dense withheld를 통과했고 W2 analytic/convergence-only 자료가 이를 지지했다. 판정은 `passed_circle_interior_only`이며 M0/M1 또는 product 승격이 아니다.
- reduced differential line의 4-terminal absolute lift는 gauge 외 common-mode null을 가져 현 global MNA에서 exact singular다. 후보 2의 trace block은 `blocked_balanced_projection_and_return_partition`이며 arbitrary conductance로 null을 숨기지 않는다.
- streaming metadata screening은 P1/P2에 8/57개 profile과 6,816/2,976개 explicit-ref candidate를 찾았다. profile cache 잠재력은 있지만 actual return polygon/connectivity와 source-owner key가 없으므로 production cardinality/속도 주장은 보류한다.

## C1. Source-faithful hybrid domain decomposition

실제 artwork를 adaptive triangular 또는 Delaunay–Voronoi mesh로 표현하되, port, via cluster, void, neck, boundary 주변을 세밀하게 하고 단순 영역은 크게 유지한다. 각 domain의 내부 DOF를 interface로 static condensation한다.

\[
Y_\Gamma(s)=Y_{\Gamma\Gamma}-Y_{\Gamma i}Y_{ii}^{-1}Y_{i\Gamma}
\]

condensed plane/trace domains, exact route graph, mounted components를 passive global MNA로 연결한다. bounded-stencil mesh의 assembly/storage는 대체로 O(n)이며, domain별 factor와 작은 interface는 full-board factor fill을 줄일 가능성이 있다. 달성 비율은 separator geometry와 interface rank로 실측한다.

문헌 근거:

- Choi, Kim, Swaminathan, [Triangular Elements for Power/Ground Plane Analysis](https://doi.org/10.1109/TCPMT.2013.2277659)
- Choi et al., [Delaunay–Voronoi Method With Source-Port Correction](https://doi.org/10.1109/TADVP.2008.920326)
- Swaminathan et al., [Multilayer Finite-Difference Method](https://doi.org/10.1109/TEMC.2007.893331)
- Zhang et al., [Domain Decomposition for Efficient PDN Electromagnetic Analysis](https://doi.org/10.1109/TEMC.2010.2045380)

Repository의 dormant `tri_fem_*`, `mfdm`, `surface_patch_plane`, `global_mna`는 재사용 가능성을 검토할 자산이지 검증된 production solver로 간주하지 않는다.

이 자산들은 production에 바로 연결하지 않고 [`LOCAL_ORACLE_PLAN.md`](LOCAL_ORACLE_PLAN.md)의 N0/T1/S1/C1/V1/V2/A1 coupon과 owner ledger를 먼저 수행한다. 2026-08-14 관련 12개 focused module의 178 tests는 내부 analytic/passivity/reciprocity/KCL 회귀를 확인했지만 global topology, real-SPD adapter와 PowerSI correlation을 증명하지 않는다.

## C2. Local transition replacement

measurement/decap transition 주변에 trace series impedance, via self/return/mutual, pad/antipad capacitance, spreading/current crowding을 포함한 bounded local model을 만든다. core와 중복되는 에너지를 제거하는 exact-minus-core stamp를 사용한다.

\[
Y_{hybrid}=Y_{core}+P^T\left(Y_{local,EM}-Y_{local,core}\right)P
\]

local cluster가 q conductor이면 dense 계산은 O(q²) storage/O(q³) work이므로 radius, coupling threshold와 template reuse를 사전 정의한다. single via, via pair, dense cluster, finite contact, pad/antipad, shared pad마다 analytic 또는 converged FEM/CIM oracle를 둔다.

`Ylocal,core`는 유사한 lumped 식이 아니라 실제 global core가 같은 crop에 부여한 연산자여야 한다. exact/core의 crop, interface trace space, DtN/Schur boundary operator, terminal order, current orientation, return conductor, gauge와 source hash가 하나라도 다르면 조립을 금지한다. crop 경계를 가로지르는 mutual electric/magnetic term도 단일 owner를 가져야 하며 `ΔYΓ`와 interface complex power가 crop 확장에 수렴해야 한다. 수렴하지 않는 field는 local correction이 아니라 global domain이 소유한다. equipotential artwork와 ideal Trace는 additive stamp가 아니라 topology replacement가 필요하다.

문헌 근거:

- Han et al., [Intrinsic Via Circuit Model](https://doi.org/10.1109/TMTT.2010.2052956)
- Ruehli, [Foundational PEEC Formulation](https://doi.org/10.1109/TMTT.1974.1128204)
- [Circular Ports in Parallel-Plate Waveguides](https://doi.org/10.1109/TEMC.2011.2170998)
- [Physics-Based Via and Trace Models](https://doi.org/10.1109/TMTT.2009.2025470)

### C2-T1. Trace SAO–CIM + exact line subpath

straight invariant cross-section에서는 arbitrary conductor contour의 surface-admittance/DtN operator와 exterior contour integral로 full partial `Z'(f)`를 구한다. source-derived `Y'(f)`와 같은 conductor/reference basis를 공유할 때 scalar line은 hyperbolic exact terminal operator를, multiconductor line은 `exp(l[[0,-Z'],[-Y',0]])` block을 사용한다. 작은 dense cross-section은 profile별 cache하고 length는 exact runtime data로 유지한다.

- Cohn lossless stripline과 periodic finite-thickness plate는 analytic anchor다.
- periodic finite-thickness plate는 `passed_periodic_1d_volume_only`다. M0 전용 periodized Helmholtz kernel은 finite/open M1 위험을 거의 줄이지 않으므로 우선 구현하지 않고, M1 실패가 interior face-coupling/sign으로 격리될 때만 별도 diagnostic으로 고려한다.
- SAO–CIM은 finite width, skin, proximity, corner current와 multiple return을 포함하는 normative 2-D oracle다.
- 현재 interior kernel 후보는 `C0-A1`: positive frequency에서 conductor/background 모두 fixed `C0=1`, direct/scaled `H^(2)`, 별도 DC branch다. 실패하면 차단하며 frozen A0 fallback이나 board-curve 기반 tuning을 하지 않는다.
- volume-current A–v FEM과 3-D PEEC/FastHenry length difference는 independent reference다.
- nominal π는 mandatory band `max modal |γl|≤0.1`과 exact terminal-matrix gate를 통과할 때만 fallback으로 허용한다.
- bend/profile change/T junction은 별도 3-D local block 없이 ideal join으로 만들지 않는다.
- reduced relative-return operator는 현 absolute global MNA와 직접 호환되지 않는다. full partial/common-mode operator 또는 explicit balanced-projection primitive와 same-crop return partition이 필요하다.

문헌 근거:

- Cohn, [Exact Zero-Thickness Shielded Stripline](https://doi.org/10.1109/TMTT.1954.1124875)
- Demeester and De Zutter, [Lossy Multiconductor Quasi-TM With DtN](https://doi.org/10.1109/TMTT.2008.925215)
- Patel and Triverio, [Arbitrary-Shape Surface Admittance and CIM](https://doi.org/10.1109/TMTT.2016.2593721), [preprint](https://arxiv.org/abs/1509.08357)
- Kamon, Tsuk, and White, [FastHenry](https://doi.org/10.1109/22.310584)
- Higham, [Scaling and Squaring for the Matrix Exponential](https://doi.org/10.1137/04061101X)

## C3. Exact route-graph reduction

근사 없이 다음을 제거/축약한다.

- boundary/measurement/mutable terminal이 아닌 dangling tree
- boundary가 아닌 degree-2 series chain
- certified equipotential duplicate node

cycle, branch, parallel path, all mutable decap attachment, measurement terminal은 보존한다. 단순 degree-2/tree rule은 서로 독립인 scalar two-terminal branch에만 허용하며 mutual R/L/C, multi-terminal block, controlled source, cross-boundary correction interface에 참여한 node에는 적용하지 않는다. Coupled operator는 전체 행렬의 exact Schur complement로만 제거한다.

예상 graph-scan 복잡도는 O(V+E)지만 Schur fill은 별도 계측한다. unreduced network의 full-port response와 machine-precision parity를 gate로 두고, trace topology, V2 mutual coupling, pad/port correction 등 연산자가 바뀔 때마다 인증을 폐기하고 다시 검증한다. 이 exact reduction은 MOR와 구분되며 현재 물리를 바꾸지 않는 parity가 증명된 범위에서는 accuracy freeze 전 bounded resource 연구에도 사용할 수 있다.

현재 frozen reproduction을 포함한 두 scalar coupon의 최대 relative Z error는 `5.34e-15`, 최대 absolute error는 `1.78e-14 Ω`다. module은 아직 production solve path에서 호출되지 않는다. 다음 결합 단계에서 trace topology replacement, mutual-via block, local correction interface가 추가될 때 full-port parity를 다시 통과하기 전에는 이 인증을 재사용하지 않는다.

## C4. Passive MOR and port compression

물리 모델을 동결한 뒤 PRIMA/SPRIM 또는 rational Krylov projection으로 substrate를 축약한다.

\[
H(s)=B^T(sE-A)^{-1}B+D
\]

\[
E_r=V^TEV,\quad A_r=V^TAV,\quad B_r=V^TB
\]

문제는 외부 4/92/160 ports보다 수천 개의 independently mutable decap terminal이다. block Krylov input dimension이 폭증하지 않도록 domain-level port compression, attachment clustering의 sensitivity proof, a posteriori error estimator가 필요하다.

- Odabasioglu, Celik, Pileggi, [PRIMA](https://doi.org/10.1109/43.712097)
- Freund, [SPRIM](https://doi.org/10.1109/ICCAD.2004.1382547)

ROM은 workstation reference compiler가 생성하고 laptop evaluator가 immutable ROM + low-rank scenario update를 적용하는 2-tier 구조의 핵심 후보다. passivity와 source parameter lock이 필수다.

## C5. Iterative/recycled and hierarchical solve

shifted frequency systems의 subspace recycle, multigrid 또는 HIF/HSS 계열 factor는 잘 구조화된 field subdomain에서 검토한다. full indefinite mixed-RLC MNA의 첫 production oracle을 직접 대체하지 않는다. resonance, gauge/nullspace와 scale 차이에서 convergence가 불규칙할 수 있기 때문이다.

- [Recycling Krylov Methods for Shifted Systems](https://doi.org/10.1016/j.apnum.2014.02.006)
- Ho and Ying, [Hierarchical Interpolative Factorization](https://doi.org/10.1002/cpa.21582)

## C6. Adaptive frequency sampling and passive fitting

필수 anchor와 error-driven sample을 결합하고, withheld dense grid에서 peak/zero-crossing/phase를 검증한다. 이 기법은 expensive board solve 수 F를 m으로 줄일 수 있지만 누락된 물리를 고치지 못한다.

- [Adaptive Bayesian Vector Fitting](https://doi.org/10.1049/el.2018.6668)
- Nakatsukasa, Sete, Trefethen, [AAA Rational Approximation](https://doi.org/10.1137/16M1106122)

## PowerSI reference와의 범위 차이

Cadence는 PowerSI를 full-wave electrical analysis, autoadaptive numerical mesh, simultaneous signal/plane modeling 및 multiprocessing 도구로 설명한다. 3D EM option은 full-wave와 quasi-static solver, adaptive FEM, model reduction, low-frequency conditioning, dispersive material과 roughness modeling을 포함한다고 설명한다.

- [Cadence Sigrity PowerSI datasheet](https://www.cadence.com/en_US/home/resources/datasheets/cadence-sigrity-powersi-ds.html)
- [Cadence PowerSI 3D EM extraction option](https://www.cadence.com/en_US/home/resources/datasheets/sigrity-power-si-3d-em-extraction-option-ds.html)

따라서 현행 quasi-static circuit/overlap 모델에서 PowerSI에 접근하려면 solver tolerance만 조정하는 것이 아니라 dominant distributed/local physics를 선택적으로 복원해야 한다. 제품 목표는 PowerSI의 모든 full-wave 기능을 복제하는 것이 아니라 selected PI Z-metric에 필요한 물리를 훨씬 적은 DOF로 포착하는 것이다.

## 사전 등록할 ablation 순서

Board ablation 전에 [`LOCAL_ORACLE_PLAN.md`](LOCAL_ORACLE_PLAN.md)의 manufactured identity, mesh/crop convergence와 ownership gate를 통과한다.

1. current exact-capacitance core only
2. exact route-graph reduction; unreduced parity 확인
3. finite smooth-copper trace distributed RLGC, including skin/proximity
4. plane sheet resistance/internal impedance
5. nonuniform plane spreading domain
6. via return and mutual inductance
7. pad/antipad capacitance와 local spreading
8. complete local transition replacement
9. dielectric dispersion/loss
10. conductor roughness after source evidence exists

각 단계는 예상한 port class/frequency band에서 사전 등록한 physical signature를 보여야 한다. 단일 block의 board error가 단조 감소할 필요는 없다. 올바른 물리가 기존 model-error cancellation을 깨뜨릴 수 있으므로 canonical/invariant를 통과한 block은 제한 factorial과 integrated leave-one-out까지 평가한다. 최종 integrated model은 held-out accuracy와 unaffected-port regression을 통과해야 한다. parameter는 SPD stackup/geometry/material/component model에서 유도하고 PowerSI curve에 맞춘 design-specific tuning은 금지한다.

## 평가 metric

기본 complex error는 다음과 같다.

\[
e_Z(f)=\hat Z(f)-Z_{ref}(f),\qquad
e_{rel}=\frac{|e_Z|}{\max(|Z_{ref}|,Z_{floor})}
\]

필수 보고:

- complex absolute RMS/median/p95/max in µΩ
- magnitude signed bias/RMS/p95/max in dB
- wrapped phase RMS/p95/max
- decade별 및 100 kHz–100 MHz critical band
- resonance/antiresonance frequency, magnitude, prominence, Q와 assignment-based peak matching
- imaginary-axis zero crossing, low-band effective C/loss, high-band inductive slope
- diagonal Zii와 transfer Zij
- matrix Frobenius/spectral error 및 dominant impedance eigenmode
- reciprocity, passivity, causality 범위, KCL/charge conservation, mesh convergence
- S→Z condition/residual과 비교 domain의 수치 적합성
- parse/compile/assemble/factor/RHS/postprocess wall time, peak RSS, fill, iteration/factor stats

## 주 경로로 기각하거나 후순위로 둔 방법

- legacy modal order만 증가: 현행 model-form gap을 복구하지 못함
- full-board dense PEEC/BEM: O(N²) memory와 O(N³) direct work 위험
- uniform whole-board fine-grid FFT/BEM: 8 GB에서 workspace/geometry fidelity가 부적합
- full-board 3D FEM/FDTD를 product solver로 사용: local oracle에는 유용하나 시간/메모리 목표와 충돌
- 네 PowerSI file에 unconstrained material/RLC fit: overfit 및 새로운 설계 일반화 실패
- sensitivity proof 없는 spatial decap grouping: mutable terminal 정확성 손실 가능
- 현행 780k-node direct SuperLU graph를 laptop architecture로 유지: factor memory부터 목표 초과

## 후보 선택 규칙

최종 선택은 단일 평균 오차가 아니라 다음 Pareto 조건으로 한다.

1. provisional accuracy gate와 물리 invariant를 모두 통과
2. paired perturbation ΔZ를 재현
3. reserved 및 future blind design에서 재학습 없이 통과
4. acceleration 추가오차 budget 통과
5. 8 GB laptop에서 실제 peak RSS/time 통과
6. 실패 원인과 적용 범위를 사용자가 이해할 수 있게 보고 가능

이 문서는 연구 결과가 바뀔 때 후보 순위와 기각 사유를 갱신하며, 성공한 결과만 남기지 않는다.
