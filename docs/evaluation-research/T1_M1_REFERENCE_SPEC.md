# SPD Decap PI Evaluator v0.22.0 — T1-M1 SAO–CIM / A–v Reference Specification

최종 갱신: 2026-08-15 (Asia/Seoul)

이 문서는 finite-width straight conductor와 explicit return의 smooth-copper broadband series operator를 검증할 T1-M1 실행 계약을 고정한다. 제품 parser/solver 코드는 변경하지 않았다. mandatory circle interior prototype은 [`T1_CIRCLE_DTN_RESULTS.md`](T1_CIRCLE_DTN_RESULTS.md)에서 `C0-A1 passed_circle_interior_only`, lateral-periodic slab은 [`T1_M0_SLAB_RESULTS.md`](T1_M0_SLAB_RESULTS.md)에서 `passed_periodic_1d_volume_only`로 판정됐다. 첫 M1 collocation power failure는 immutable이고 G1은 `passed_exterior_galerkin_only`다. G2 pair screen은 `passed_pair_screen_only`지만 100 kHz circle에서 raw reciprocity/cancellation gate를 실패해 현재 전체 T1-M1은 **`BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY__G2_PAIR_PASSED_CIRCLE_100KHZ_RECIPROCITY_CANCELLATION_FAIL`**, T1 전체와 global composition은 계속 `blocked`다. planned G2 2 GHz circle row, G2 `N=512`, G2 EQ0 seed는 fail-closed로 미실행이며 raw 결과는 [`T1_M1_EQ0_RESULTS.md`](T1_M1_EQ0_RESULTS.md)에 보존한다.

## 범위와 독립성

| 항목 | 1차 범위 | 현재 상태 |
|---|---|---|
| normative oracle | dense pulse SAO–CIM; homogeneous, nonmagnetic, lossless background; simply connected copper contours | G1 exterior-only pass; G2 pair screen only pass, 100 kHz circle reciprocity/cancellation fail; overall blocked |
| independent reference | 2-D volume-current `A_z–v` magnetoquasistatic P1 FEM | `2GHz_4Deff_smoke_only`; consistent mass power pass, mesh/crop/condition 미실행 |
| authoritative output | 동일한 conductor order와 balanced current basis의 complex `Z'(f)` | 없음; collocation raw table은 negative evidence only |
| 별도 electrostatic block | transverse `C'`; lossy dielectric이면 causal `G'/C'` | T1-E0 외 미실행 |
| source-derived P2 | `Trace13305` 치수의 artificial continuous-return coupon | manufactured-only |
| source-faithful board | actual return polygon, transition, same-crop core/DtN owner | blocked |
| product/global stamp | absolute partial operator 또는 explicit balanced adapter | blocked |

1차 SAO–CIM에 stratified Green function, lossy dielectric, semiconductor longitudinal current 또는 multiply connected conductor를 몰래 근사해서 넣지 않는다. 이들은 각각 `LAYERED_BACKGROUND_UNSUPPORTED`, `LOSSY_BACKGROUND_UNSUPPORTED`, `LONGITUDINAL_CURRENT_DEFINITION_UNSUPPORTED`, `CONTOUR_TOPOLOGY_UNSUPPORTED`로 차단한다.

## 공통 convention과 terminal basis

- peak phasor와 `e^{jωt}`를 사용한다.
- conductor와 current의 양의 방향은 모두 `+z`다.
- 모든 단순연결 contour는 counter-clockwise다. panel tangent가 `t=(tx,ty)`이면 outward normal은 `n=(ty,-tx)`다.
- reciprocity에는 ordinary transpose `T`, power에는 conjugate transpose `H`를 사용한다.
- raw conductor current는 반드시 balanced group basis `Bg` 또는 prescribed-current return incidence `T`로 구동한다. 각 열의 합이 0이 아니면 `UNRESOLVED_EXTERNAL_RETURN`이다.
- 동일한 geometry라도 conductor order, contour orientation, terminal grouping과 current basis가 다르면 같은 oracle case가 아니다.

## SAO–CIM interior operator

conductor `p`의 내부와 동일 contour를 background로 채운 equivalent problem의 wavenumber는 각각

```text
kp = sqrt(ω μp (ω εp - j σp))
kb = ω sqrt(μb εb)
```

다. `e^{jωt}`, `ω>0`에서 passive conductor의 `kp`는 `Re(kp)>0`, `Im(kp)<0`인 제곱근 branch를 쓴다. lossless positive-real background의 `kb`는 positive-real branch다. 뒤의 Bessel 함수와 `ln(kp a/2)`는 모두 이 `kp`와 같은 analytic continuation의 principal complex log를 사용한다. branch 조건을 만족하지 않거나 branch-cut 근처에서 연속성이 깨지면 `BESSEL_BRANCH_UNRESOLVED`로 차단한다.

contour를 straight pulse panel로 나누고 panel midpoint의 longitudinal electric field `E`, tangential magnetic field `H`, equivalent surface current `J`를 coefficient로 둔다. Patel–Triverio convention은

```text
J = H - H_tilde = Ys E
U E = P H
```

다. panel `n`의 길이는 `ℓn`, source point는 `r'`, collocation midpoint는 `rm`, `d=|r'-rm|`다. `m≠n`에서

```text
Umn = (j k / 2) ∫γn ((r'-rm)·n')/d
      [C0 J1(kd) - j Y1(kd)] ds'

Pmn = (ω μ / 2) ∫γn [C0 J0(kd) - j Y0(kd)] ds'
```

이고 straight pulse의 jump term은 `Umm=1`이다. off-diagonal integral은 최소 5-point Gauss로 시작하되 5→10 또는 10→20 parity를 보고한다.

`Pmm`의 Neumann singularity를 일반 quadrature로 통과시키지 않는다. `a=ℓm/2`,

```text
g(s)     = C0 J0(ks) - j Y0(ks)
g_reg(s) = g(s) + (2j/π) ln(s/a)

Pmm = ω μ [ ∫0^a g_reg(s) ds + 2j a/π ]
```

로 singular subtraction하고, 작은 `|ka|`에서는

```text
Pmm ~ ω μ a [ C0 - (2j/π)(ln(ka/2) + γE - 1) ]
```

과 비교한다. inverse를 만들지 않고

```text
Din  = solve(P, U)
Dout = solve(Pout, Uout)
Ys_p = Din - Dout
```

를 계산한다. 전체 `Ys`는 conductor별 block diagonal이다.

## Homogeneous unbounded exterior와 complex `Z'`

전체 panel 수 `N`, conductor 수 `Pc`에 대해 `W=diag(ℓ1,…,ℓN)`이고 `Q[N,Pc]`는 해당 conductor panel row에 1을 둔다.

```text
Iabs = Qᵀ W J
```

homogeneous magnetoquasistatic exterior kernel은

```text
g0(r,r') = (1/2π) ln(|r-r'|/r0)
G0mn     = ∫γn g0(rm,r') ds'
```

다. `r0=1 m`를 manifest에 고정한다. straight segment는 analytic log integral을 사용하고 self term은

```text
G0mm = ℓm/(2π) [ln(ℓm/(2r0)) - 1]
```

이다. 이 Green function은 무한공간 operator이므로 SAO–CIM에 인공 magnetic outer crop을 추가하지 않는다.

arXiv v1의 식 (43)–(44)는 `Ys`와 `Re/Im`이 누락되어 `R`과 `L`이 같은 식처럼 보인다. 구현의 authoritative complex 식은 출판본과 동일 저자 선행 유도에 따라 다음으로 고정한다.

```text
A = I - jω μb Ys G0
X = solve(A, Ys Q)
Kc = Qᵀ W X
Z'_partial = solve(Kc, I_Pc)

R' = Re(Z'_partial)
L' = Im(Z'_partial)/ω
```

복소 `Z'_partial`이 원본 결과이며 `R'`, `L'`는 reporting view다. 여기서 `X`는 panel response이고 current basis 기호와 공유하지 않는다. reference conductor `r`에 대한 prescribed loop-current incidence `T`의 각 열을 `es-er`로 두면

```text
Iabs    = T Iloop
Vloop   = Tᵀ Vabs
Z'_loop = Tᵀ Z'_partial T
```

이다. 이 식은 conductor별 current가 이미 지정된 one-reference mode에만 사용한다.

여러 return conductor를 양 끝에서 묶는 manufactured fixture는 모든 conductor를 먼저 보존한다. `Hg[Pc,Ng]`를 conductor-to-equipotential-group map, `Bg[Ng,Nm]`를 zero-sum group-current basis라 두고 다음 saddle solve로 return current split을 구한다.

```text
[ Z'_partial  -Hg ] [Iabs] = [  0   ]
[    Hgᵀ       0 ] [ vg ]   [ Bg q ]

Z'_Bg = Bgᵀ vg
```

첫 행은 `Vabs=Z'_partial Iabs=Hg vg`, 둘째 행은 group별 total current constraint다. return current를 50:50으로 지정하지 않는다. `ω=0`에는 아래 `diag(1/(σA))`를 `Z'_partial`에 넣으며, 동일 단면·전도도를 가진 두 P2 artificial return의 DC parallel resistance를 재현해야 한다. 75/104 µm face gap이 다른 AC case의 equal split을 가정하지 않는다. `TᵀZ'_partial T`는 prescribed-current mode의 별도 결과이고 이 equipotential 축약을 대신하지 않는다.

`r0` 변경은 partial common mode에는 영향을 줄 수 있지만 zero-sum `Z'_loop`와 `Z'_Bg`에서는 condition-aware roundoff 범위로 소거돼야 한다. conductor permutation, reference choice와 mirror transform도 같은 물리 basis로 되돌린 뒤 invariant여야 한다. 시험 집합은 `r0={0.1,1,10} m`이며 mode matrix dimension을 `nm`이라 할 때

```text
εinv = ||Z'(r0)-Z'(1 m)||F
       / max(||Z'(1 m)||F, sqrt(nm) Z'floor)
```

로 정규화한다. `εinv<=τinv=max(1e-12,50 max(κ1 u))`여야 한다. 여기의 `κ1 u`는 아래 condition certificate의 관련 solve 전체 최댓값이고 `Z'floor`도 아래 frequency별 정의를 사용한다. `τinv>1e-8`이면 invariance pass를 주지 않고 conditioning blocker로 남긴다.

## DC, `C0`와 quasi-TM 범위

정확한 `ω=0`을 CIM 식에 대입하지 않는다. 별도 DC branch는

```text
R'_dc,partial = diag(1/(σp Ap))
R'_dc,loop    = Tᵀ R'_dc,partial T
```

를 사용한다. arbitrary-shape `Ldc`는 A–v magnetostatic energy가 소유한다. positive-frequency SAO 결과는 `R→Rdc`, finite `Im(Z)/ω→Ldc`로 수렴해야 한다.

Patel–Triverio의 empirical conditioning switch를 frozen candidate `C0-A0`로 다음과 같이 사전 등록했다.

```text
C0 = 1e6  if Δp/δp <= 0.5
C0 = 1    otherwise
```

같은 conductor의 interior와 equivalent-background operator에 같은 `C0`를 쓰는 이 정책은 작은 원 100 kHz에서 정확도·mesh·phase·conditioning gate를 실패했다. 이 결과는 [`T1_CIRCLE_DTN_RESULTS.md`](T1_CIRCLE_DTN_RESULTS.md)의 immutable negative baseline이며 결과를 삭제하거나 선택 후보의 통과로 덮어쓰지 않는다.

현재 positive-frequency research candidate `C0-A1`은 conductor와 filled-background operator 모두 `C0=1`을 사용하고, `Jν-jYν`를 따로 계산하지 않은 direct/scaled `Hν^(2)` kernel을 사용한다. DC는 위 analytic branch로 분리한다. A1 선택에는 downstream `Z'`, PowerSI 또는 board curve를 사용하지 않고 finite/branch, self/quadrature, residual, equilibrated condition과 mesh gate만 사용했다. A1이 finite/open M1에서 실패하면 `BLOCKED_C0_A1`로 남기며 A0로 자동 복귀하거나 frequency별로 `C0`를 tune하지 않는다. high-`C0` 재시도는 별도 `C0-A2` preregistration과 dynamic-range certificate가 있을 때만 허용한다.

homogeneous log exterior는 magnetoquasistatic/quasi-TM model이다. conductor union의 최대 transverse span을 `Deff`라 두고 각 frequency에서

```text
|kb| Deff <= 0.3
```

을 engineering preregistration으로 사용한다. 이를 넘는 case는 결과를 clip하지 않고 `BLOCKED_QUASI_TM_EXTENT`로 남긴다. unbounded SAO exterior에는 별도의 current edge가 없으므로 edge-tail gate를 만들지 않는다. finite A–v reference에서는 `8Deff` solution의 magnetic energy로 `Wm(Ω8\Ω4)/Wm(Ω8)<0.1%`를 별도 검사한다. `ΩR`는 conductor union의 bounding box를 모든 방향으로 `R Deff` 확장한 영역이다.

## M0 boundary amendment와 independent result

M0의 `w=5 mm`는 finite conductor width가 아니라 lateral period다. seam은 translationally identified되고 physical side face/corner가 없다. 반면 이 문서의 M1 SAO는 simply connected finite rectangle과 unbounded exterior를 푼다. 따라서 finite rectangle의 free-space `H2`/log operator를 periodic `coth` target과 직접 비교하지 않는다.

제품 helper를 import하지 않는 normalized 1-D volume FEM은 periodic slab의 `m=0` exact target을 통과했다. canonical 12 frequencies의 max fine error/mesh/phase는 `0.073301%/0.219901%/0.041998°`, W0 material/thickness withheld 12 cases는 `0.126811%/0.380419%/0.072657°`다. 상태는 `passed_periodic_1d_volume_only`다. gap exterior `jωμh/w`는 analytic M0 term이며 general exterior CIM pass가 아니다.

C0-A1 periodized pulse SAO를 만들려면 free-space kernel과 별개의 periodic Helmholtz/diffusion Green function, seam identification과 spectral/image-tail certificate가 필요하다. 이 M0-only kernel은 finite/open M1 code path를 직접 검증하지 않으므로 우선 구현하지 않는다. M1 실패가 interior face-coupling/sign으로 격리되면 그때 별도 time-boxed diagnostic으로 preregister할 수 있지만, 사후 promotion evidence로 소급 사용하지 않는다.

## 독립 2-D volume-current `A_z–v` FEM

`a=A_z(x,y)`이고 group `g`의 longitudinal scalar-potential drop gradient를 `v_g=-∂φ_g/∂z` `[V/m]`라 둔다. conductor region `m`이 group `g(m)`에 속하면

```text
Ez = v_g(m) - jω a
Jz = σm Ez
∇t·(μ^-1 ∇t a) = -Jz
```

다. P1 basis `Ni`, conductor-region-to-group map `Hg`에 대해

```text
Kij = ∫Ω μ^-1 ∇Ni·∇Nj dA
Mij = Σm ∫Ωm σm Ni Nj dA
Fim = ∫Ωm σm Ni dA
Gmm = ∫Ωm σm dA

(K + jωM) a - F Hg v = 0
i = Hgᵀ(G Hg v - jω Fᵀ a) = Bg q
```

를 푼다. 실제 block system은

```text
[ K+jωM          -F Hg ] [a] = [  0   ]
[ jωHgᵀFᵀ  -HgᵀG Hg ] [v]   [-Bg q ]
```

다. outer boundary의 `a=0`은 `A_z` reference를 고정하는 동시에 artificial magnetic truncation boundary를 부과한다. 이 경계는 `{2,4,8} Deff` crop sequence가 수렴할 때만 허용한다. `Bg`의 모든 열은 zero-sum이어야 한다. unit modal excitation의 authoritative result는 `Z'_Bg=Bgᵀv`이고 conductor별 current share와 `Hg`, `Bg`, crop을 함께 보존한다.

SAO와 A–v가 같은 conductor order/group/current basis가 아니면 cross-method error를 계산하지 않는다. A–v는 conductor `R'+jωL'`만 주며 `C'/G'`는 별도의 transverse electroquasistatic solve가 소유한다.

DC에서는 `ω=0`을 직접 풀어 uniform-current analytic `Rdc`와 비교한다. magnetostatic unit-current solution `k,l`의 field로

```text
L'Bg[k,l] = ∫Ω μ Hk·Hl dA
```

를 계산한다. peak-phasor power certificate는

```text
Re(0.5 iᴴv) = 0.5 ∫Cu σ|E|² dA
Im(0.5 iᴴv) = 2ω Wm,
Wm = 0.25 ∫Ω μ|H|² dA
```

다.

SAO의 peak-phasor dissipative power certificate는 independent current column마다

```text
Pterm = 0.5 Iabsᴴ Z'_partial Iabs
Pbdry = 0.5 Eᴴ W J

εP = |Re(Pterm)-Re(Pbdry)|
     / max(|Re(Pterm)|, |Re(Pbdry)|, 1e-18 W/m)
```

로 고정한다. `εP<=1e-8`이어야 하며 complex power 전체나 크기만 비교해서 부호 오류를 숨기지 않는다.

## Canonical M1 fixture

background는 `εb=ε0`, `μb=μ0`, lossless다. return top face를 `y=0`, signal bottom face를 `y=h`에 둔다.

```text
h = 50 µm
ts = tr = 35 µm
σs = σr = 59.6 MS/m
signal = [-w/2,w/2] × [h,h+ts]
return = [-Wr/2,Wr/2] × [-tr,0]
w/h = {5,10,20,50}
Wr/w = {1,5,20}
f = {DC anchor, 100 kHz, 1,10,100,500 MHz,1,2 GHz}
```

DC analytic loop resistance over 10 mm과 2 GHz `|kb|Deff` screening은 다음과 같다.

| w/h | Wr/w | w / Wr (µm) | 10 mm Rdc (mΩ) | 2 GHz `|kb|Deff` | 2 GHz status |
|---:|---:|---:|---:|---:|---|
| 5 | 1 | 250 / 250 | 38.350911 | 0.010479 | eligible |
| 5 | 5 | 250 / 1,250 | 23.010547 | 0.052396 | eligible |
| 5 | 20 | 250 / 5,000 | 20.134228 | 0.209585 | eligible |
| 10 | 1 | 500 / 500 | 19.175455 | 0.020958 | eligible |
| 10 | 5 | 500 / 2,500 | 11.505273 | 0.104792 | eligible |
| 10 | 20 | 500 / 10,000 | 10.067114 | 0.419169 | blocked at 2 GHz |
| 20 | 1 | 1,000 / 1,000 | 9.587728 | 0.041917 | eligible |
| 20 | 5 | 1,000 / 5,000 | 5.752637 | 0.209585 | eligible |
| 20 | 20 | 1,000 / 20,000 | 5.033557 | 0.838338 | blocked at 2 GHz |
| 50 | 1 | 2,500 / 2,500 | 3.835091 | 0.104792 | eligible |
| 50 | 5 | 2,500 / 12,500 | 2.301055 | 0.523961 | blocked at 2 GHz |
| 50 | 20 | 2,500 / 50,000 | 2.013423 | 2.095845 | blocked at 2 GHz |

blocked geometry도 `f<=0.3c/(2πDeff)`의 저주파 trend에는 사용할 수 있다. threshold는 각각 1.4314 GHz, 715.70 MHz, 1.1451 GHz, 286.28 MHz다. 이 screening은 solver pass가 아니다.

### M1-EQ0 first-run freeze

첫 finite/open 실행은 결과를 보기 전에 다음 단일 geometry로 고정한다.

```text
id         = M1-EQ0
background = vacuum, homogeneous, lossless, nonmagnetic
signal     = [-125,+125] µm × [50,85] µm
return     = [-125,+125] µm × [-35,0] µm
ts = tr    = 35 µm
σs = σr    = 59.6 MS/m
order      = (signal,return)
b          = (+1,-1)ᵀ, Iloop=1 A peak, +z signal current
frequency  = DC analytic anchor; positive solve at
             {100 kHz,1,10,100,500 MHz,1,2 GHz}
Deff       = 250 µm
```

2 GHz에서 `|kb|Deff=0.010479225107`, `δ=1.457746488493 µm`이고 10 mm DC loop resistance는 `38.3509108341 mΩ`다. authoritative electrical output은 `Z'loop=bᵀVabs/Iloop`인 **one-dimensional balanced scalar**다. 여기서 `Vabs`는 A–v 절의 conductor longitudinal voltage-gradient vector `v`와 같은 `[V/m]` terminal quantity다. 외부 return이 없는 `(1,0)` 또는 `(0,1)` current column, r0-dependent common mode와 rank-one artificial lift는 `UNRESOLVED_EXTERNAL_RETURN`이며 physical `2×2` partial operator나 GlobalMNA evidence로 보고하지 않는다.

EQ0 owner는 다음처럼 고정한다.

- conductor interior/background subtraction, skin과 proximity: `C0-A1` SAO `Din-Dout` 단독 owner
- exterior magnetic field: SAO는 unbounded `G0`, independent A–v는 crop-converged full exterior의 단독 owner
- dielectric `C'/G'`, finite end, pad/via/bend, roughness, layered dielectric, board return polygon과 exact-minus-core: out of scope
- M0 periodic analytic gap term `jωμh/w`: EQ0에 추가하지 않음

#### EQ0 exact panel manifest

기존 문구의 물리 panel-size gate는 **두 번 이등분한 fine `4N` level**에 적용하도록 사전 정정한다. seed는 pre-refinement geometry이며 seed 자체가 `δ/4` 또는 `h/8`을 만족한다고 주장하지 않는다. fine에서 corner/interaction start panel `<=δ2GHz/4`, facing/projection maximum `<=h/8`을 만족해야 한다.

각 anchor interval은 아래 geometric half-interval 식을 쓰고, 모든 seed panel을 그대로 이등분해 medium/fine을 만든다.

```text
g = 3/2
Δi = (L/2)(g-1)g^i/(g^M-1), i=0..M-1

facing: anchors x={-125,0,+125} µm,
        each L=125 µm interval uses M=8 per half
outer:  one L=250 µm interval uses M=10 per half
right/left vertical: each L=35 µm interval uses M=5 per half
```

각 conductor는 facing `32`, outer `20`, vertical `10+10`, 합계 `72` panel이다. 두 full CCW contour의 nested sequence는 **`N={144,288,576}`**이고 symmetry/half-domain reduction을 사용하지 않는다. signal은 facing `(-125,50)→(125,50)`, right, outer, left 순서이고 return은 outer `(-125,-35)→(125,-35)`, right, facing, left 순서다.

| level | N | min panel (µm) | max anchor-start (µm) | max panel (µm) | max facing (µm) | SHA-256 |
|---|---:|---:|---:|---:|---:|---|
| seed | 144 | `1.102972857` | `1.327014218` | `42.401981904` | `21.679222839` | `f65cddcf5d45187006ffc5e9eaf1c5624fa14e3849ce435c2601825da579743c` |
| medium | 288 | `0.551486428` | `0.663507109` | `21.200990952` | `10.839611420` | `35d1f81c87cb97372c543db98e2063102b8823d5af0e70b8e5c4432966b77b02` |
| fine | 576 | `0.275743214` | `0.331753555` | `10.600495476` | `5.419805710` | `5b964069b3bae0965ff3bfcc95348b98656fca9a48da3a998a0510892cd17d0e` |

fine의 모든 anchor-start panel 최대는 `0.331753555 µm = 0.910319(δ/4)`, fine facing maximum은 `0.867169(h/8)`이다. global minimum 하나로 다른 corner를 숨기지 않고 모든 physical corner와 interaction anchor 양쪽을 검사한다. interval 내부와 physical corner를 포함한 인접 panel ratio는 모두 `<=1.5`다. 이 count는 full contour에서 해당 fine gates와 global growth를 만족하는 현재 geometric formula의 최소 정수 조합이며, 기존 planning count `168/176`을 결과 선택에 사용하지 않는다.

hash serialization은 UTF-8/LF/no trailing newline, rational µm endpoint를 `numerator/denominator`로 쓴다. header는 `M1-EQ0|manifest=v1|level=<seed|medium|fine>|g=3/2|units=um|subdivide=<1|2|4>|loops=signal,return|ordering=ccw`; panel row는 `index|loop|edge|anchor_index|ordinal|x0|y0|x1|y1`, 마지막에 각 loop의 exact closure row를 둔다. 재현 코드는 [`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)에 고정한다.

#### EQ0 A–v crop/resource freeze

`ΩR=bbox(Cu)⊕R Deff`이므로 crop box는 다음과 같다.

| crop | x range (µm) | y range (µm) |
|---|---:|---:|
| `2Deff` | `[-625,+625]` | `[-535,+585]` |
| `4Deff` | `[-1125,+1125]` | `[-1035,+1085]` |
| `8Deff` | `[-2125,+2125]` | `[-2035,+2085]` |

A–v conductor-normal target는 coarse/medium/fine `δ/2,δ/4,δ/8 = 0.728873/0.364437/0.182218 µm`다. 한 frequency/crop/mesh만 순차 실행하고 hard ceiling은 `250,000 nodes`, `500,000 triangles`다. process-tree target working set `<=4.0 GiB`, private/committed `>5.0 GiB`이면 현재 stage를 중단하며 system commit headroom `<2.0 GiB` 또는 available physical memory `<1.5 GiB`이면 시작하지 않는다.

### SAO panel sequence

- 모든 physical corner, opposing-conductor corner의 orthogonal projection, closest-gap point를 edge anchor로 넣는다. wide return의 중앙 interaction region을 corner-only grading의 큰 panel 하나로 덮지 않는다.
- 각 anchor interval의 양 끝에서 midpoint 방향으로 growth `g=1.5` geometric panels를 배치한다.
- half-interval `L/2`, panel count `M`이면 `Δi=(L/2)(g-1)g^i/(g^M-1)`다.
- `N,2N,4N`의 **fine `4N`**에서 corner/interaction start panel은 2 GHz skin depth `δ=1.457746 µm`의 `δ/4` 이하이고, opposing projection 주변 최대 panel은 `h/8` 이하다. seed가 이 fine threshold를 만족한다고 주장하지 않는다.
- seed panel 전체를 이등분한 nested sequence를 사용한다. sharp corner의 uniform-only mesh는 canonical pass가 아니다.

### A–v mesh/crop sequence

- conductor normal-direction target는 `δ/2, δ/4, δ/8`; fine level은 2 GHz에서 skin depth당 8개 이상의 P1 layer다.
- conductor interior와 exterior growth ratio는 `<=1.3`; corner와 closest-gap zone을 함께 refine한다.
- inverted/poor-quality triangle은 차단한다.
- outer Dirichlet boundary는 conductor union에서 `{2,4,8}Deff`만큼 확장한 nested box로 둔다. resource ceiling을 넘으면 `MESH_RESOURCE_BLOCKED`이며 crop이나 skin resolution을 몰래 줄이지 않는다.

## P2 `Trace13305` manufactured fixture

actual board return은 [`T1_RETURN_CROP_MANIFEST.md`](T1_RETURN_CROP_MANIFEST.md)의 near-tangent void/via evidence 때문에 차단돼 있다. 다음은 source 치수를 사용한 artificial continuous-return coupon이다.

```text
signal width/thickness/length = 120 µm / 17.5 µm / 4.2 mm
top/bottom face gap           = 75 µm / 104 µm
top/bottom return width       = 1.2 mm each, artificial
return thickness              = 17.5 µm each
σ20C                          = 59.6 MS/m
return terminal contract      = both returns equipotential at both end planes;
                                current split solved, not forced 50:50
```

analytic DC checks are signal `33.557046980 mΩ`, each return `3.355704698 mΩ`, bundled returns `1.677852349 mΩ`, loop `35.234899329 mΩ`. vacuum `|kb|Deff` at 2 GHz is `0.0503003`. A–v MQS R/L sensitivity는 가능하지만 actual two-dielectric `C'/G'`, layered full quasi-TM와 source-faithful return claim은 허용하지 않는다. P2 material의 1 GHz single-point `Dk/Df`를 0–2 GHz causal law로 늘리지 않는다.

## Mandatory circle DtN validation

M1 사각형을 풀기 전에 isolated solid circular copper contour로 interior `Ys` 자체를 검증한다. background는 vacuum `μb=μ0, εb=ε0`, conductor는 `μp=μ0, εp=ε0, σp=59.6 MS/m`, radius는 `a={17.5 µm,0.5 mm}`, frequency는 `{100 kHz,1,10,100,500 MHz,1,2 GHz}`다. CCW uniform angular pulse mesh `N={128,256,512}`와 Fourier modes `m={0,1,2,3,4}`를 사용한다.

같은 complex branch에서 analytic DtN eigenvalue는

```text
Dm(k)  = k/(jωμ) · Jm'(ka)/Jm(ka)
Ys,m   = Dm(kp) - Dm(kb)
```

다. `Jm'/Jm`는 같은 scale의 Bessel adjacent-order ratio 또는 continued ratio로 평가하고 numerator와 denominator를 따로 unscaled 계산해 overflow를 허용하지 않는다. numerical eigenvalue는 normalized sampled Fourier vector `em,n=exp(jmθn)`에 대한 Rayleigh quotient `emᴴ W Ys em / (emᴴ W em)`로 추출한다. `Ys,floor=max(1e-12 S,1e-10 max_m|Ys,m|)`를 쓰고, fine `N=512`의 normalized complex error `|ŷm-Ys,m|/max(|Ys,m|,Ys,floor)`는 각 mode/frequency에서 `<=0.5%`여야 한다. phase error는 analytic과 numerical magnitude가 모두 `>=10 Ys,floor`인 mode에만 적용하며 `<=0.25°`다. `N=256→512`도 아래 `0.5%/1%/0.25°` convergence gate를 통과해야 한다. branch continuity, `m↔-m` degeneracy와 raw residual을 함께 보존한다.

실행 결과는 `C0-A0 failed_circle_low_frequency`, `C0-A1 passed_circle_interior_only`다. A1 canonical의 N512 worst analytic error `0.118541%`, N256→512 change `0.158276%`, phase `0.019939°`, per-column normwise-infinity dense backward residual `1.2030e-15`, exact-circulant equilibrated `κ1u=9.7539e-13`이다. W1/W3 range-guarded dense withheld가 full-condition 결과를 지지하고 W2는 analytic/convergence-only 보조 자료다. 이는 circle interior만 승인하며 M0/M1 exterior 또는 product를 승인하지 않는다.

## M1-EQ0 실행 후 G1 amendment

동결한 collocation exterior

`Gc[m,n]=∫γn g0(rm,r')ds'`

는 response convergence를 통과했지만 `W=diag(ℓ)`에 대해 fine `||WGc−(WGc)^T||F/||WGc||F=2.29761e-4`였고, mandatory signed dissipative-power mismatch가 2 GHz `1.58517e-4`로 실패했다. 원인과 raw 표는 [`T1_M1_EQ0_RESULTS.md`](T1_M1_EQ0_RESULTS.md)에 고정한다. 사후 weighted symmetrization은 attribution control에만 썼으며 production/research pass operator로 사용하지 않는다.

후속으로 실행한 `M1-EQ0-G1`은 같은 panel endpoint, order, `N`, frequency, branch, current basis와 threshold를 보존하고 exterior만 direct pulse-Galerkin으로 바꿨다.

```text
GG[m,n] = ∫γm∫γn g0(r,r') ds' ds
GE      = W^-1 GG
GG[m,m] = ℓm²/(2π) [ln(ℓm/r0) − 3/2]
```

weak exterior equation은 `W E=jωµ0 GG J+WQV`, `J=YsE`로 두고 `AE=W−jωµ0 GG Ys`, `Eresp=solve(AE,WQ)`, `Jresp=YsEresp`, `Kc=Q^T WJresp` 순서로 조립한다. `GG`를 기존 collocation 식의 `I−jωµ0 Ys GG`에 그대로 넣지 않는다.

non-touching pair는 deterministic q×q tensor Gauss로 계산한다. shared endpoint `c`에서 `ri(u)=c+uℓi ti`, `rj(v)=c+vℓj tj`로 두고 radial coordinate를 analytic 적분한

```text
GGij = ℓiℓj/(4π) ∫0^1 [
          ln(|ℓi ti−ηℓj tj|/r0)
        + ln(|ηℓi ti−ℓj tj|/r0) − 1
       ] dη
```

를 q10/q20로 계산한다. 첫 oracle에서는 두 orientation을 독립 계산해 parity `<=1e-12`를 검사하며 transpose 복사나 matrix 평균을 금지한다. raw weighted symmetry `<=1e-12`를 요구한다. quadrature normalization은 `smn=max(|GG10,mn|,|GG20,mn|,ℓmℓn/(2π))`로 고정하고 `maxmn |GG20−GG10|/smn <=1e-10`와 `||GG20−GG10||F/||GG20||F<=1e-10`을 둘 다 검사한다. `GG(r0')−GG(r0)=−ln(r0'/r0)/(2π)ℓℓ^T`, 기존 terminal/power/`r0` gate도 모두 유지한다.

현재 pulse-collocation interior `Ys`의 weighted ordinary-reciprocity diagnostic은 fine에서 최대 `7.12361e-2`다. frozen mandatory gate는 terminal `Z'` reciprocity였으므로 이를 과거 실행의 추가 mandatory failure로 소급하지 않는다. G1 prospective metric은 `Yw=WYs` `[S·m]`, `Yw,floor=max(1e-12 S·m,1e-10 maxmn|Yw,mn|)`, `||Yw−Yw^T||F/max(||Yw||F,N Yw,floor)<=1e-8`로 동결한다. prospective passivity는 `H(Yw)=(Yw+Yw^H)/2`의 raw `λmin >= -max(Yw,floor,1e-9||Yw||2)`다. Hermitian part의 eigenvalue를 평가하는 것은 matrix를 대칭화해 solver에 넣는 행위가 아니다. G1 exterior가 power identity를 복원해도 이 hidden-mode reciprocity/passivity가 prospective 기준을 넘으면 interior `P/U/Pout/Uout`를 target-tested Galerkin trace space로 다시 이산화하기 전 production promotion을 차단한다.

### G1 실행 판정

G1은 `N={144,288,576}`, 7 frequencies, q10/q20와 `r0={0.1,1,10} m`에서 실행됐다. fine pair-normalized q change `2.02993e-15`, raw `GG` transpose defect `9.95287e-17`, `r0` rank-one residual `6.33326e-16`, boundary dissipative-power mismatch 최대 `1.06982e-14`로 exterior gate를 통과했다. 그러나 fine `Yw` weighted reciprocity는 주파수 순서대로 `7.12361e-2, 4.08228e-2, 3.07258e-2, 2.91663e-2, 2.47707e-2, 2.10899e-2, 1.69946e-2`로 모두 실패했다. raw `λmin H(Yw)`도 100 kHz `-2.96377e-5 S·m`, 1 MHz `-1.79591e-6 S·m`로 tolerance보다 각각 약 `4.10e6`, `2.49e5`배 큰 음수다. 이는 conditioning noise가 아니다. G1을 `passed_exterior_galerkin_only`, active blocker를 `BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY`로 고정한다.

## M1-EQ0-G2 target-tested interior Galerkin amendment

Patel–Triverio의 published implementation은 pulse expansion과 point matching을 사용한다. G2는 그 결과 matrix를 평균하는 절차가 아니라, 같은 continuous contour equation을 target-tested pulse Galerkin 약형으로 별도 이산화하는 연구 후보다. `Pᴳ`는 symmetric Green single-layer지만 double-layer `Uᴳ`나 discrete DtN이 자동으로 algebraic symmetric라고 가정하지 않는다.

각 conductor contour의 pulse basis/test `φn`과 mass matrix를

```text
Wmn = ∫Γ φm φn ds = ℓm δmn
```

로 둔다. frozen `C0-A1`, `hν(z)=Hν^(2)(z)`에서 conductor 또는 replaced-background material마다

```text
Pᴳmn(k,µ) = (ωµ/2) ∫γm∫γn h0(k|r-r'|) ds' ds

Uᴳmn(k)   = Wmn
            + (jk/2) PV ∫γm∫γn
              ((r'-r)·n')/|r-r'| h1(k|r-r'|) ds' ds
```

를 직접 조립한다. jump term은 collocation의 scalar `1`이 아니라 `W`다. explicit inverse 없이

```text
Dp  = solve(Pᴳp, Uᴳp)
Db  = solve(Pᴳb, Uᴳb)
Dwp = W Dp
Dwb = W Db
Yw  = Dwp - Dwb                       [S·m]
```

를 계산하고 `Dwp`, `Dwb`, 차이 `Yw`를 모두 보존한다. 이는 physical owner `H−Htilde`를 유지한다. G1 exterior와의 결합은 panel-integrated flux `j=YwE`를 사용해

```text
AE      = W - jωµb GG W^-1 Yw
Eresp   = solve(AE, WQ)
jresp   = Yw Eresp
Kc      = Q^T jresp
Z'      = solve(Kc, I)
I       = Q^T j
Pbound  = 1/2 E^H j
```

순서로 고정한다. `Yw`를 과거 coefficient-space 연산자 순서에 넣지 않는다. G2는 conductor/background interior DtN subtraction, skin과 proximity만 소유하고 G1 `GG`는 homogeneous exterior magnetic field의 단독 owner다. dielectric `C'/G'`, roughness, finite ends/bends/vias, exact-minus-core와 GlobalMNA ownership은 계속 범위 밖이다.

### G2 singular quadrature

동일 straight panel에서는 double-layer geometric kernel이 0이므로 `Uᴳmm=ℓm`을 exact로 둔다. single-layer self는

```text
Pᴳmm = ωµ ∫0^ℓ (ℓ-u) h0(ku) du
```

이고 다음 singular subtraction으로 계산한다.

```text
Pᴳmm = ωµ [
  ∫0^ℓ (ℓ-u){h0(ku)+(2j/π)ln(u/ℓ)}du
  + 3jℓ²/(2π)
]
```

small-argument anchor는 같은 branch에서

```text
Pᴳmm ~ (ωµℓ²/2)[1-(2j/π){ln(kℓ/2)+γE-3/2}]
```

다. shared endpoint `c`는 outward parameter `r=c+u a`, `r'=c+v b`, `a=ℓm tm`, `b=ℓn tn`와 두 Duffy map `(u,v)=(ρ,ρη),(ρη,ρ)`를 사용한다. `Pᴳ`는 `lnρ`를 analytic 제거한다. `Uᴳ`는

```text
KU(d) = jk/2 · (d·n')/|d| · h1(k|d|)
Ks(d) = -(d·n')/(π|d|²)
```

로 분리해 `KU−Ks`만 수치 적분하고 Duffy-transformed `Ks`의 radial part를 analytic 적분한다. non-touching pair는 deterministic tensor Gauss다. near-but-not-touching pair는 결과가 아니라 geometry만으로 긴 segment를 재귀 이분해 `dmin/max(ℓm,ℓn)>=1`이 될 때까지 적분하며, 사전 고정한 depth ceiling을 넘으면 fail closed한다.

### G2 preregistered gates와 bounded sequence

- q20 canonical/q40 parity를 self, touching, routed-near pair와 final balanced `Z'loop`에 적용한다. pair normalization은 `Pscale=max(|P20|,|P40|,ωµℓmℓn/(2π))`, `Uscale=max(|U20|,|U40|,sqrt(ℓmℓn))`이고 두 relative change 모두 `<=0.1%`다. frozen seed pair screen은 self/touching/routed-near class가 실제로 각각 하나 이상 존재해야 하며 directed-pair count와 maximum recursion depth를 보존한다. final magnitude change `<=0.1%`, phase `<=0.25°`다.
- 첫 oracle은 `(m,n)`/`(n,m)`을 독립 조립한다. `||Pᴳ−Pᴳ^T||F/||Pᴳ||F<=1e-12`; transpose 복사와 사후 평균 금지다. `Uᴳ=Uᴳ^T`는 요구하지 않는다.
- 각 `Pᴳp/Pᴳb/AE/Kc` backward residual `<=1e-10`, `κ1u<=1e-8`을 요구한다.
- circle `a=17.5 µm`, modes `0…4`에서 `Dhat_m=e_m^H(WD)e_m/(e_m^H W e_m)`을 analytic Bessel DtN과 비교한다.
- 기존 `Yw` reciprocity와 passivity gate를 mandatory로 적용한다.
- cancellation amplification `Acancel=(||Dwp||F+||Dwb||F)/max(||Yw||F,N Yw,floor)`과 `Acancel·max(κ1u(Pp),κ1u(Pb))<=1e-8`을 요구한다. 실패하면 higher precision 또는 blocked이며 clipping하지 않는다.
- boundary/terminal power, current, terminal reciprocity/passivity, `r0`, panel convergence와 resource gate는 그대로 유지한다.
- `0.5(Yw+Yw^T)`, eigenvalue clipping, C0 retuning은 금지한다.

실행 순서는 (1) 100 kHz/2 GHz self와 Duffy q20/q40, (2) circle `N={128,256}` analytic modes, medium gate 통과 때만 `N=512`, (3) EQ0 seed `N=144` 두 extreme q20/q40와 G1 exterior, (4) `Yw` raw gate 실패 시 즉시 중단, (5) 두 extreme 통과 뒤에만 `N=288/576`, 7 frequencies와 세 `r0`로 확장한다. 각 단계는 별도 명령으로 실행하고 이전 JSON의 mandatory gate와 외부 process-tree resource를 검토하기 전 다음 명령을 시작하지 않는다. G2가 여전히 `1e-8` hidden-mode gate를 놓치면 matrix를 대칭화하지 않고 four-operator symmetric Calderón/Steklov–Poincaré discretization 또는 volume-FEM boundary Schur complement를 다음 후보로 둔다.

Stage 1 pair screen은 사전등록 뒤 실행됐다. 두 frequency 모두 self/touching/routed-near class coverage와 q20/q40, raw `P` transpose, recursion gate를 통과했다. 최악 q change는 2 GHz conductor self-`P` `7.52822e-6`, raw transpose 최대 `1.88876e-16`, depth 최대 `2`다. 이 단계만 `passed_pair_screen_only`다.

Stage 2 medium circle은 같은 session에서 pair replay/review 뒤 실행됐고 100 kHz의 첫 mandatory operator gate에서 fail-closed 종료됐다. `N=128` q20/q40의 raw `Yw` reciprocity는 `2.40109e-9/2.50717e-9`로 통과했지만 cancellation condition `2.91315e-8`이 실패했다. `N=256`은 raw reciprocity `1.41197e-8/1.41083e-8`과 cancellation `1.63755e-7`이 모두 실패했다. analytic max error는 각각 `0.390062%`와 `0.0985103%`, q parity worst `2.81068e-12`, mesh worst relative/RMS/phase `0.290419%/0.161108%/0.0128560°`였고 raw Hermitian minimum은 양수여서 analytic, q, mesh, residual/condition과 passivity gate는 통과했다. planned G2 2 GHz circle row, G2 `N=512`, G2 EQ0 seed는 실행하지 않았다. 현재 전체 상태는 **`BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY__G2_PAIR_PASSED_CIRCLE_100KHZ_RECIPROCITY_CANCELLATION_FAIL`**다.

다음 실행 후보는 G2 수치를 보정하는 continuation이 아니다. two-DtN subtraction이 없는 independent A–v volume-FEM boundary Schur reference candidate는 H0 pre-factor negative를 보존한 뒤 H1 17.5 µm/100 kHz coarse `h`와 H2 refined `h2`에서 각각 stage-only gate를 통과했다. 최신 physics 상태는 `passed_AV_BS_h2_stage_only_pending_h4_preregistration`이고 h→h2 RMS/max `0.462796%/0.868015%`는 trend-only다. H4-P0/H4-P0R parent와 P1 executable static review도 통과했지만 세 public P1 시도는 claim/factor 전에 중단되고 token 폐기됐다. 세 번째는 ready/start 뒤 control default-one retry exhaustion이었다. 현재 retry-v3 static candidate만 있고 token absent/factor fit unproven이며 fine analytic/mesh convergence/final circle은 `null`, H2 token은 consumed/next=false다. retry-v3 clean no-token contract reread/fresh token/result audit 전에는 H4 physics를 금지하고, 그 뒤에만 `h4`, final circle과 crop/withheld-radius 단계를 순차 확장한다. production SAO 후보는 Hamiltonian Schur 또는 four-operator symmetric Calderón/Steklov–Poincaré discretization으로 별도 비교하며 post-symmetrization, negative-eigenvalue clipping, higher-precision promotion과 gate 완화를 금지한다.

## Numerical certificate와 promotion gate

각 frequency, mesh와 independent current column에서 raw 값을 보존하고 다음을 모두 검사한다.

condition certificate의 machine unit은 binary64 `u=2^-53`이다. 각 `P`, `Pout`, exterior `A`, terminal `Kc`, equipotential saddle와 A–v saddle에 대해 먼저 row max-norm, 이어 column max-norm을 1로 만드는 deterministic diagonal scaling `Aeq=Dr A Dc`를 적용하고 `Dr,Dc`를 기록한다. `κ1=||Aeq||1 ||Aeq^-1||1`은 LU/sparse solve에 대한 1-norm inverse estimator로 구하며 explicit inverse는 만들지 않는다. 보고값은 `κu=κ1 u`이고 관련 solve 각각 `<=1e-8`이어야 한다.

matrix convergence에서 frequency별 `Z'floor=max(1e-9 Ω/m,1e-9 maxij|Z'fine,ij|)`로 둔다. `max(|Z'medium,ij|,|Z'fine,ij|)>=Z'floor`인 entry만 `meaningful element`의 magnitude gate에 포함하고, 그보다 작은 entry는 absolute error와 floor-normalized error만 보고한다. phase gate는 비교 양쪽 magnitude가 모두 `>=10 Z'floor`인 entry에만 적용한다. log-weight RMS는 mandatory positive-frequency set에서 같은 meaningful magnitude mask의 complex relative error로 계산한다.

| gate | threshold |
|---|---|
| interior `PD=U` backward residual | `<=1e-10` |
| exterior/terminal or FEM saddle backward residual | `<=1e-10` |
| equilibrated condition certificate | 위에서 정의한 각 `κ1(Aeq)u<=1e-8`; 아니면 검증된 estimator/higher precision을 사용하거나 blocked |
| integrated current and zero-sum residual | `<=1e-10` |
| reciprocity before symmetrization | relative Frobenius `<=1e-8` |
| passivity | 각 `Z'mode∈{Z'_loop,Z'_Bg}`에서 `λmin(Hermitian(Z'mode)) >= -max(1e-12 Ω/m,1e-9||Z'mode||2)` |
| SAO boundary power identity | relative mismatch `<=1e-8` |
| A–v loss/magnetic power identities | each `<=1e-8` |
| `C0` policy | `C0-A1` fixed; 실패 시 `BLOCKED_C0_A1`. A0 자동 fallback 또는 사후 frequency tuning 금지 |
| quadrature doubling | `<=0.1%` |
| medium→fine panel/mesh | log-weight RMS `<=0.5%`, max meaningful element `<=1%`, phase `<=0.25°` |
| A–v crop 4→8 `Deff` | same `0.5%/1%/0.25°` gate |
| A–v far-field shell | `8Deff` solution에서 `Wm(Ω8\Ω4)/Wm(Ω8)<0.1%` |
| converged SAO vs converged A–v | same basis에서 `0.5%/1%/0.25°` |
| invariance | reference/permutation/mirror와 `r0={0.1,1,10} m`에서 `<=τinv`; `τinv=max(1e-12,50 max κ1u)<=1e-8` |

사전 symmetrization, negative-eigenvalue clipping, pole 건너뛰기, unconverged result 보간은 금지한다.

## Resource preflight와 cache

- dense SAO 1차 ceiling은 total panel `N<=800`이다. 한 complex128 `800×800` matrix는 약 9.77 MiB이지만 실제 peak는 동시 matrix, factor workspace와 copies를 모두 예측·측정한다.
- geometry/panel/Q/W/analytic `G0`는 profile cache, translated identical conductor의 interior DtN은 shape/material/frequency key로 재사용할 수 있다.
- cache key는 solver/formula version, ordered panel endpoints/orientation, corner grading, conductor/background material, frequency, `C0`, Bessel branch, quadrature/self-integral version, `r0`, current incidence와 source hash를 포함한다.
- A–v reference는 existing dormant FEM policy의 250,000 nodes / 500,000 triangles를 hard ceiling으로 재사용한다. preflight는 matrix nnz, factor/preconditioner/work arrays, parser/reference buffers, mapped-file residency와 25% safety margin을 합산한다. 실측은 전체 process tree의 peak working set, private bytes, committed bytes, mapped residency와 page faults뿐 아니라 실행 전 OS baseline, system commit limit/charge/headroom과 available physical memory를 시간축으로 기록한다. 목표 노트북에서는 peak working set 4.0 GiB 목표, private/committed bytes 5.0 GiB 절대 상한을 적용하며, system commit headroom `<2.0 GiB` 또는 available physical memory `<1.5 GiB`이면 새 단계를 시작하지 않고 현재 단계도 안전하게 취소한다.
- ceiling 초과를 coarsening으로 숨기지 않는다. H-matrix/FMM, adaptive panels, MOR은 dense oracle과 withheld-frequency passivity가 동결된 뒤의 가속 단계다.

## 실행 순서와 상태 전이

1. 위 두 radius, 일곱 frequency, 다섯 Fourier mode의 analytic Bessel/Fourier DtN eigenvalue로 SAO interior를 검증한다. **완료:** A0 실패, A1 circle-only 통과.
2. M0 periodic slab의 analytic + independent 1-D volume 결과를 동결한다. **완료:** `passed_periodic_1d_volume_only`; C0-A1 periodic SAO는 미승격.
3. smallest eligible equal-width finite/open M1 collocation은 **완료/실패:** `BLOCKED_SAO_BOUNDARY_POWER_IDENTITY`로 immutable 보존한다.
4. 같은 endpoint의 G1 direct exterior Galerkin은 **완료/제한 통과:** `passed_exterior_galerkin_only`. interior reciprocity와 저주파 passivity 때문에 overall blocked다.
5. G2 target-tested interior Galerkin은 **pair 제한 통과 뒤 100 kHz circle 실패:** raw reciprocity/cancellation failure를 immutable 보존하고 planned G2 2 GHz circle, G2 `N=512`, G2 EQ0 seed로 확장하지 않는다.
6. 같은 geometry/current basis의 A–v는 **2 GHz 4Deff smoke와 H1 coarse `h`, H2 refined `h2` stage-only:** consistent mass power smoke 뒤 17.5 µm/100 kHz boundary-Schur `h`와 `h2`가 각각 local gate를 통과했다. H2 artifact는 signed-M9 PDE/power certificate와 consumed one-use token을 보존한다. H4-P0R parent와 P1 executable/static review는 통과했지만 세 public attempt는 pre-factor 중단/token 폐기됐고 현재 factor fit/H4 physics는 미증명·금지 상태다. retry-v3 clean no-token contract를 감사·reread하고 fresh one-use token을 만든 뒤 factor-only run을 한 번 수행하고, 그 result/resource/tombstone/seal을 독립 감사한다. 이어 별도 H4-P1 physics/result 계약, clean static audit와 새 one-use token을 모두 고정한 뒤에만 `h4`를 실행해 mandatory h2→h4/fine analytic을 처음 판정하며, 그 뒤에만 final circle과 crop `2/4/8 Deff`를 순차 실행한다.
7. symmetric two-return case에서 symmetry로만 equal split이 나오는지 검증한다.
8. P2 artificial `Trace13305` coupon을 실행한다.
9. finite-length T1-F 3-D length-difference reference와 distributed line stamp를 연결한다.
10. actual board crop와 core owner가 준비된 뒤에만 source-faithful/global adapter 연구로 넘어간다.

immutable collocation failure, G1 `passed_exterior_galerkin_only`, G2 `passed_pair_screen_only`는 어느 쪽도 full-M1 `oracle_pass`로 전이하지 않는다. G2 circle failure도 삭제하거나 덮어쓰지 않는다. 후속 Hamiltonian/four-operator SAO의 raw hidden-mode 수치 gate와 converged independent A–v boundary-Schur reference가 모두 통과할 때만 승격을 검토한다. 그 전에는 PowerSI correlation, product accuracy 또는 8 GB production 성능을 주장하지 않는다.

## Primary literature

- U. R. Patel and P. Triverio, “Skin Effect Modeling in Conductors of Arbitrary Shape Through a Surface Admittance Operator and the Contour Integral Method,” [author preprint](https://arxiv.org/html/1509.08357), [IEEE T-MTT DOI](https://doi.org/10.1109/TMTT.2016.2593721).
- A. Cagliero and L. Rahmouni, “Symmetric Galerkin Boundary Element Method for Computing the Quantum States of the Electron in a Piecewise-Uniform Mesoscopic System,” [author preprint](https://arxiv.org/abs/1909.06596). 이 논문의 four Helmholtz boundary-operator symmetric discretization과 singular-integral 처리는 G2 실패 뒤 후보의 수학적 근거이며, 현재 SAO 구현을 자동 승인하지 않는다.
- T. Betcke, E. Burman, and M. W. Scroggs, “Boundary Element Methods for Helmholtz Problems with Weakly Imposed Boundary Conditions,” [author preprint](https://arxiv.org/abs/2004.13424), [SIAM SISC DOI](https://doi.org/10.1137/20M1334802). primal trace와 flux를 함께 근사하는 Calderón weak formulation을 후속 four-operator 후보 근거로 사용한다.
- U. R. Patel, B. Gustavsen, and P. Triverio, “An Equivalent Surface Current Approach for the Computation of the Series Impedance of Power Cables with Inclusion of Skin and Proximity Effects,” [author preprint with the complete complex `Z'` derivation](https://arxiv.org/html/1303.5452v2), [IEEE TPWRD DOI](https://doi.org/10.1109/TPWRD.2013.2267098).
- A. Piwonski et al., “Finite Element Modeling of Power Cables using Coordinate Transformations,” [author preprint](https://arxiv.org/abs/2307.00814), [IEEE TMAG DOI](https://doi.org/10.1109/TMAG.2023.3318292). This supports the independent magnetic-vector-potential `A–v` family; the exact reduced fixture above remains this project’s preregistered formulation.
- A. M. Dienstfrey, F. Hang, and J. Huang, “Lattice Sums and the Two-dimensional, Periodic Green's Function for the Helmholtz Equation,” [NIST primary publication](https://www.nist.gov/publications/lattice-sums-and-two-dimensional-periodic-green-s-function-helmholtz-equation). This supports treating the periodic Green kernel as a separate numerical formulation, not as the free-space `H2` kernel with renamed boundaries.
- T. Demeester and D. De Zutter, “Quasi-TM Transmission Line Parameters of Coupled Lossy Lines Based on the Dirichlet to Neumann Boundary Operator,” [author PDF](https://tdmeeste.github.io/files/pubs/QuasiTM_MTT_Demeester2008.pdf). This motivates the current-definition and layered/lossy-background blockers.
