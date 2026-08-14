# SPD Decap PI Evaluator v0.22.0 — T1-M0 Periodic Slab Results

최종 갱신: 2026-08-14 (Asia/Seoul)

이 문서는 lateral-periodic two-plate manufactured coupon의 smooth-copper series operator를 독립 1-D volume FEM으로 검증한 결과와, free-space finite-contour SAO를 같은 문제로 오인하지 않기 위한 경계 조건을 고정한다. 제품 parser/solver/UI는 수정하지 않았다.

## 판정

| subcase | 판정 | 허용되는 주장 | 차단된 주장 |
|---|---|---|---|
| exact `coth` identity | `passed_analytic_identity_only` | periodic infinite-width slab의 exact conductor/gap 식과 DC/skin limit | 독립 numerical recovery |
| M0-V1 normalized 1-D FEM | **`passed_periodic_1d_volume_only`** | 동일 periodic `m=0` slab interior의 canonical + withheld numerical recovery | finite-width edge/proximity, free-space exterior |
| M0 gap exterior | `analytic_m0_only` | balanced `+I/-I`에서 uniform gap field `jωμh/w` | general exterior CIM 또는 open-return field |
| C0-A1 periodic SAO | `blocked_periodic_green_not_implemented` | 없음 | free-space `H2`를 periodic kernel로 간주하는 것 |
| finite-width M1 | `specified_not_run` | 실행 계약만 고정 | SAO–CIM/A–v 정확도 |
| product/global/PowerSI | `blocked` | 없음 | source-faithful 또는 제품 정확도 승격 |

이번 cycle의 정확한 승격은 **M0-V1 periodic 1-D volume-only pass**다. [`T1_CIRCLE_DTN_RESULTS.md`](T1_CIRCLE_DTN_RESULTS.md)의 `C0-A1 passed_circle_interior_only`를 M0 SAO pass로 확대하지 않는다.

## 경계값 문제의 정정

M0의 폭 `w=5 mm`는 finite rectangular conductor width가 아니라 x 방향 **period**다. 양쪽 seam은 translationally identified되고 물리 side face나 corner가 없다. 반면 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)의 free-space SAO–CIM은 simply connected finite contour와 unbounded exterior를 푼다. finite rectangle에는 side-face current crowding과 edge magnetic energy가 있으므로 그 total `Z'`를 periodic `coth` 식과 직접 비교할 수 없다.

따라서 다음 비교는 금지한다.

1. finite rectangle + unbounded `H2` exterior를 periodic exact target과 비교해 M0 pass를 주는 것
2. side Neumann 조건만 두고 exact translational periodicity라고 부르는 것
3. 기존 `copper_surface_impedance` 결과를 numerical unknown에 다시 넣고 independent recovery라고 부르는 것
4. full loop의 큰 gap inductance가 conductor error를 가리도록 loop error만 보고하는 것

periodic pulse SAO를 별도로 만들려면 periodized Helmholtz/diffusion Green function, seam identification, spectral/image-tail doubling과 `m≠0` leakage gate가 필요하다. 2-D periodic Helmholtz Green function의 image sum은 조건부 수렴하며 별도 lattice-sum 알고리즘이 필요하다는 [NIST primary publication](https://www.nist.gov/publications/lattice-sums-and-two-dimensional-periodic-green-s-function-helmholtz-equation)을 따른다. 이는 finite/open M1 production candidate와 다른 kernel이므로, 현재 우선순위에서는 M0만 통과시키기 위해 새 kernel을 구현하지 않고 실제 사용 경로인 M1을 circle interior + independent A–v와 직접 비교한다.

## Exact periodic target

peak phasor `Re{F e^{jωt}}`, propagation/current `+z`에서 각 conductor contour는 `+z` 방향에서 보아 CCW로 둔다. outward normal `n=(n_x,n_y)`에 대해

```text
t = (-n_y,n_x)
Ht = H·t = (1/(jωμ)) ∂Ez/∂n
```

이다. positive frequency의 square-root branch는 `Re γ>0`, `Im γ>0`로 고정한다. lower return은 `y∈[-t,0]`, upper signal은 `y∈[h,h+t]`이고 `x=0,w`는 같은 zero-Bloch periodic seam이다. signal/return inner face의 signed tangential field는 각각 `+I/w`, `-I/w`, 두 outer face는 0이다.

```text
γ = sqrt(jωμσ)
Zc = sqrt(jωμ/σ)
Zs = Zc coth(γt)
Zx = Zc csch(γt)

Z'loop = 2 Zs/w + jωμh/w
Zloop  = l Z'loop
```

다. fixture는 `l=10 mm`, `w=5 mm`, `t=35 µm`, facing-surface gap `h=50 µm`, `σ=59.6 MS/m`, `μ=μ0`이고 conductor current basis는 `(+1,-1)`이다.

한 conductor의 exact outer/inner face map은 face-current orientation에서

```text
[Eo]   [Zs Zx] [Ho]
[Ei] = [Zx Zs] [Hi]
```

이다. 이번 M0-V1은 `Ho=0`, `Hi=1`인 `Zs` 열만 독립 FEM으로 검증했다. `Zx`와 임의의 two-face excitation을 numerical recovery했다고 승격하지 않는다.

```text
Rdc = 2l/(σwt)
Ldc = μl(h+2t/3)/w
LHF = μlh/w
RHF ~ 2l/w sqrt(πfμ/σ)
```

## Terminal basis와 signed complex power

`b=(1,-1)ᵀ`로 두면 `Iabs=b Iloop`, `Vloop=bᵀVabs`이고 보고 가능한 유한 값은 zero-sum scalar `Z'loop=bᵀZ'partial b`뿐이다. 외부 기준이 없는 common mode는 정의되지 않는다. 진단용 `(Z'loop/4)bbᵀ` lift는 common-mode eigenvalue가 0인 인공 행렬이므로 물리 partial operator, GlobalMNA stamp 또는 global-composable 증거로 사용하지 않는다.

peak current convention의 terminal complex power는

```text
S'term = 0.5 |I|² Z'loop
P'cu   = |I|² Re(Zs)/w
W'gap  = μh|I|²/(4w)
W'cu   = μw/2 ∫₀ᵗ |Ht(u)|² du
Im(S'term) = 2ω(W'gap + W'cu)
```

이다. 마지막 `W'cu`는 두 conductor의 합이다. normalized FEM의 mass-matrix power invariant는 dissipative real part만 검증한다. filled-background subtraction이 들어가는 향후 SAO power와 terminal reactive power를 바로 동일시하지 않고, physical conductor field와 gap energy로 reactive part를 별도 재구성한다.

## 독립 normalized 1-D FEM

한 plate의 inner face magnetic field를 `H(0)=1 A/m`, outer face를 `H(t)=0`으로 둔다. normalized coordinate `ξ=y/t`, `x²=jωμσt²`, `u=σt E/H(0)`를 쓰면

```text
u'' - x²u = 0
u'(0) = -x²
u'(1) = 0
```

이고 exact `u(0)=x coth(x)`다. linear finite element weak form은

```text
(K + x²M) u = x² e0
Zs,FEM = u0/(σt)
```

이다. 이 경로는 제품 helper를 import하지 않고 stiffness/mass matrix를 직접 조립한다. row-max 뒤 column-max deterministic equilibration, LU solve와 LAPACK `gecon`을 사용하며 explicit inverse를 만들지 않는다.

동일 consistent mass matrix로 다음 독립 invariant를 검사한다.

```text
current:  1ᵀ M u = 1
power:    Re(u0)/(σt) = uᴴ M u/(σt)
```

DC는 singular Neumann limit를 numerical matrix에 억지로 넣지 않고 `1/(σt)` analytic branch로 분리한다.

## Canonical 결과

positive frequency는 mandatory 7 anchors와 `δ/t={4,2,1,0.5,0.25}`의 다섯 crossover를 합친 12개다. uniform thickness mesh `N={64,128,256}`을 썼고 2 GHz fine mesh는 skin depth당 `10.66` elements다.

| metric | result | gate | 판정 |
|---|---:|---:|---|
| fine raw `Zs` log-weight RMS | `0.017973%` | `<=0.5%` | pass |
| fine raw `Zs` max complex error | `0.073301%` | `<=1%` | pass |
| medium→fine log-weight RMS | `0.053917%` | `<=0.5%` | pass |
| medium→fine max change | `0.219901%` | `<=1%` | pass |
| max phase error | `0.041998°` | `<=0.25°` | pass |
| max full-loop error | `0.002936%` | 보조 | pass |
| max backward residual | `2.220e-16` | `<=1e-10` | pass |
| max equilibrated `κ1u` | `6.336e-10` | `<=1e-8` | pass |
| max current residual | `1.005e-11` | `<=1e-10` | pass |
| max dissipative-power residual | `7.574e-15` | `<=1e-8` | pass |

`Zs`를 먼저 gate하고 full loop는 보조로만 보고했다. gap inductance 때문에 2 GHz full-loop error는 raw conductor error보다 약 25배 작다.

canonical fine-result serialization checksum은 `d59a770e999fc53c90ca7043cc772badd13220e16ce84912590360fa7e5da5e6`이다.

| frequency | exact R, 10 mm | FEM R, 10 mm | exact L | FEM L |
|---:|---:|---:|---:|---:|
| DC | `1.917546 mΩ` | analytic branch | `0.184307 nH` | analytic limit |
| 100 kHz | `1.917687 mΩ` | `1.917687 mΩ` | `0.184306 nH` | `0.184305 nH` |
| 1 MHz | `1.931661 mΩ` | `1.931661 mΩ` | `0.184183 nH` | `0.184183 nH` |
| 10 MHz | `2.999005 mΩ` | `2.999011 mΩ` | `0.175031 nH` | `0.175031 nH` |
| 100 MHz | `10.294225 mΩ` | `10.294603 mΩ` | `0.142049 nH` | `0.142048 nH` |
| 500 MHz | `23.019810 mΩ` | `23.024027 mΩ` | `0.132991 nH` | `0.132990 nH` |
| 1 GHz | `32.554927 mΩ` | `32.566852 mΩ` | `0.130845 nH` | `0.130843 nH` |
| 2 GHz | `46.039620 mΩ` | `46.073330 mΩ` | `0.129327 nH` | `0.129325 nH` |

FEM의 100 MHz→500 MHz→1 GHz→2 GHz successive `d ln R/d ln f`는 `0.500124/0.500264/0.500528`로 preregistered high-skin `0.5±0.02`를 통과했다. exact target의 대응 값은 `0.500033/0.500000/0.500000`이다.

## W0 withheld material/thickness 결과

canonical 결과를 본 뒤 mesh threshold를 바꾸지 않도록 다음 rule과 corpus를 실행 전에 고정했다.

```text
t       = {17.5,70} µm
σ       = {29.8,119.2} MS/m
f       = {173 kHz,17.3 MHz,1.73 GHz}
Nfine   = next_power_of_two(max(64,ceil(8t/δ))), capped at 512
sequence= {Nfine/4,Nfine/2,Nfine}
```

12개 조합의 worst 결과는 fine complex error `0.126811%`, medium→fine `0.380419%`, phase `0.072657°`, backward residual `2.220e-16`, `κ1u=1.852e-10`, current residual `7.304e-12`, power residual `2.147e-15`다. 모두 canonical과 같은 gate를 통과했다. 가장 강한 skin case도 fine skin-depth resolution `8.106` elements를 유지했다.

## Resource와 범위

canonical 12-case sequential run은 이 host에서 약 `1.38 s`였다. process-only peak working set은 `57.86 MiB`, private bytes는 SciPy/BLAS reservation을 포함해 `1320.22 MiB`였다. 이는 process-tree 또는 8 GB product 실행 증거가 아니다. 최대 fine matrix는 `257×257 complex128`이고 case 사이에 보존하지 않았다.

M0-V1은 다음을 검증하지 않는다.

- finite-width lateral edge/proximity/corner current
- arbitrary 또는 multiple return contour와 current sharing
- general free-space exterior, dielectric `C'/G'`, finite end
- source-faithful owner, exact-minus-core, global balanced adapter
- PowerSI correlation 또는 product 성능

## 다음 경로

1. C0-A1의 circle interior certificate를 유지한다.
2. periodic M0 volume pass를 independent slab anchor로 유지한다.
3. finite/open M1의 smallest eligible equal-width case에서 actual C0-A1 perimeter SAO와 same-basis A–v를 직접 비교한다.
4. finite rectangle 결과를 M0 periodic 식과 직접 gate하지 않고, width 증가에 따른 asymptotic trend만 별도 보조 지표로 둔다.
5. M1이 실패하면 `BLOCKED_C0_A1` 또는 geometry/panel/exterior blocker로 원인을 분리하며 M0-only periodic kernel이나 A0 fallback으로 결과를 맞추지 않는다.

## Primary literature

- U. R. Patel and P. Triverio, “Skin Effect Modeling in Conductors of Arbitrary Shape Through a Surface Admittance Operator and the Contour Integral Method,” [author preprint](https://arxiv.org/abs/1509.08357), [IEEE DOI](https://doi.org/10.1109/TMTT.2016.2593721). 이 formulation은 simply connected finite conductor contour의 interior/exterior 문제를 다루며 M0 periodic seam을 자동 제공하지 않는다.
- D. De Zutter, H. Rogier, L. Knockaert, and J. Sercu, “Surface Current Modelling of the Skin Effect for On-Chip Interconnections,” [IEEE DOI](https://doi.org/10.1109/TADVP.2007.895984). `coth/csch` two-face map의 primary reference다.
- T. Demeester and D. De Zutter, “Quasi-TM Transmission Line Parameters of Coupled Lossy Lines Based on the Dirichlet to Neumann Boundary Operator,” [IEEE DOI](https://doi.org/10.1109/TMTT.2008.925215). differential scalar를 임의 absolute-node partial operator로 승격하지 않는 근거다.
- A. M. Dienstfrey, F. Hang, and J. Huang, “Lattice Sums and the Two-dimensional, Periodic Green's Function for the Helmholtz Equation,” [NIST primary publication](https://www.nist.gov/publications/lattice-sums-and-two-dimensional-periodic-green-s-function-helmholtz-equation). periodic image sum과 lattice-sum 평가가 별도 수치 문제임을 뒷받침한다.
