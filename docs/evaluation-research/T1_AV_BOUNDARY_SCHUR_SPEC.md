# SPD Decap PI Evaluator v0.22.0 — AV-BS1 Boundary-Schur Preregistration

최종 갱신: 2026-08-15 (Asia/Seoul)

## 판정과 범위

현재 상태는 **`AV-BS1-H0_BLOCKED_SPARSE_PATTERN_CONTRACT_BEFORE_FACTOR__H1_CYCLIC_DIAGONAL_CORRECTION_PREREGISTERED_NOT_RUN`**이다. 이 문서는 G2의 100 kHz circle reciprocity/cancellation 실패를 보정하는 문서가 아니라, 그 결과를 보지 않고도 정의할 수 있는 독립 volume-FEM reference candidate를 고정한다. 제품 parser, solver, UI, version과 installer는 수정하지 않는다.

AV-BS1은 아직 oracle이 아니다. 첫 circle solve, mesh convergence, 독립 감사와 withheld radius를 모두 통과해야 circle-interior reference로 제한 승격할 수 있다. 이 문서의 어떤 결과도 finite/open EQ0, full T1, GlobalMNA, PowerSI accuracy 또는 8 GB product performance를 승인하지 않는다.

금지사항은 다음과 같다.

- G2 matrix, G2 response, G2 failure vector를 AV-BS1의 입력·보정·mesh tuning에 사용하지 않는다.
- conductor와 background DtN을 각각 만든 뒤 빼는 canonical 경로를 사용하지 않는다.
- raw matrix를 사후 대칭화하거나 negative eigenvalue를 clipping하지 않는다.
- higher precision만으로 실패를 pass로 바꾸지 않는다.
- circle stage가 실패하면 2 GHz, EQ0 또는 production SAO 후보로 우회하지 않는다.

## Circle primary: 100 kHz interior fixture

이 stage는 conductor interior surface-admittance operator만 검증한다. return conductor, unbounded exterior `G0`, crop boundary, terminal `Z'`, dielectric `C'/G'`는 포함하지 않는다.

```text
case       = AV-BS1-CIRCLE-PRIMARY
radius     = 17.5 µm
frequency  = 100 kHz
sigma      = 59.6 MS/m
mu         = mu0
phasor     = exp(+j omega t)
normal     = conductor outward normal
basis      = continuous nodal P1 volume / P1 boundary trace
modes      = m = 0, ±1, ±2, ±3, ±4
canonical  = complex128; no G2-derived tuning
```

### Volume operators와 subtraction-free Schur identity

동일한 bit-identical disk mesh에서

```text
Kij = integral_Omega mu^-1 grad(Ni)·grad(Nj) dA
Mij = integral_Omega Ni Nj dA

Ab = K
Ap = K + j omega sigma M
C  = Ap - Ab = j omega sigma M
```

를 analytic P1 element matrix로 조립한다. transpose는 bilinear assembly와 Schur identity에, conjugate transpose는 power와 passivity에만 사용한다.

interior node를 `I`, boundary trace node를 `Gamma`라 두고 explicit inverse 없이 solve한다.

```text
Hs = [ -As,II^-1 As,IΓ ]
     [          IΓ       ]

Ss = As,ΓΓ - As,ΓI As,II^-1 As,IΓ
```

연속 weak DtN 차이는 `(Sp-Sb)/(j omega)`지만 canonical 계산에서는 두 Schur matrix를 따로 만들어 빼지 않는다. 같은 discrete space의 exact resolvent identity를 직접 사용한다.

```text
YΓ,w = (1/(j omega)) Hb^T C Hp
     = sigma Hb^T M Hp                 [S·m]

YΓ,w,rev = sigma Hp^T M Hb
```

`YΓ,w,rev`는 독립 반대 순서로 계산하고 `YΓ,w,rev ≈ YΓ,w^T`만 검사한다. 두 결과를 평균하지 않는다. `Ab,II`와 `Ap,II`는 순차 factor하고 동시에 한 sparse factor만 resident로 둔다. 각 rectangular harmonic extension `Hb`, `Hp`는 boundary-column batch `<=4`로 solve해 보존한 뒤 두 bilinear product를 각각 계산한다. full dense interior matrix, explicit `inverse`, `Sp-Sb`와 `Dp-Db`는 canonical 경로에서 금지한다.

### Stable analytic target

```text
delta = sqrt(2/(omega mu sigma))
kp    = (1-j)/delta, Re(kp)>0, Im(kp)<0
z     = kp a
```

ordinary-Bessel recurrence를 사용해 큰 Laplace 항을 만들지 않는다.

```text
Ys,m = -kp/(j omega mu) * J_(m+1)(z)/J_m(z),  m>=0   [S]
```

`J_(m+1)/J_m`은 same-scale `jve` ratio를 canonical로 사용한다. 이 MQS target은 `Ab=K`, `mup=mub=mu0`, `kb=0` 계약에만 적용한다. permeability contrast가 생기면 공통 `m/a` 항을 임의로 상쇄하지 않는다. vacuum displacement를 포함한 direct `Dp-Db`는 safe-point diagnostic일 뿐 pass target이 아니다. 17.5 µm, 100 kHz의 frozen analytic anchors는 다음과 같다.

| m | `Ys,m` (`S`) |
|---:|---:|
| 0 | `521.497743503956 - j0.939450347511` |
| 1 | `260.749858968260 - j0.156575853973` |
| 2 | `173.833308261008 - j0.052191983317` |
| 3 | `130.374992948407 - j0.023486396062` |
| 4 | `104.299997421132 - j0.012526078558` |

boundary node `n`의 각도는 refinement ID 순서가 아니라 좌표에서 `theta_n=atan2(y_n,x_n) mod 2pi`로 정한다. boundary edge 길이가 `ell_e`일 때 consistent trace mass는

```text
MΓ,e = ell_e/6 [[2,1],[1,2]]
```

로 조립한다. 이 `MΓ`와 `em,n=exp(j m theta_n)`으로

```text
Yhat_m = (em^H YΓ,w em) / (em^H MΓ em)    [S]
```

을 계산한다. `m`과 `-m`은 별도로 계산한다.

### Power identity

각 mode의 conductor harmonic extension `Ep=Hp em`에 대해 peak-phasor power를 비교한다.

```text
Pboundary = 0.5 Re(em^H YΓ,w em)
Pvolume   = 0.5 sigma Re(Ep^H M Ep)
```

분모는 `max(|Pboundary|,|Pvolume|,1e-18 W/m)`다. mass-lumped 또는 centroid field power는 canonical certificate가 아니다.

## Frozen circle mesh lineage

mesh generator identity는 `AV-BS1-CIRCLE|manifest=v1|generator=radial-p1-v1`이다.

- seed는 angular ray `128`, radial ring `16`, `rj=j a/16`이다.
- node 0은 center이고 각 ring은 angle index `k=0…127` 순서다.
- center fan 뒤 15개 annular band를 조립한다. diagonal 방향은 angular sector가 아니라 **radial band index만으로** 번갈아 선택해 128-fold rotational symmetry를 보존한다.
- 모든 triangle은 CCW로 정규화한다.
- refinement는 unique undirected edge를 lexicographic order로 정렬한 뒤 midpoint ID를 부여하는 deterministic 1-to-4 subdivision이다.
- boundary-edge midpoint만 radius `a`로 radial projection한다. conductor/background는 동일 node, triangle, `K`, `M`, `MΓ`를 공유한다.
- coordinate serialization은 Python binary64 `float.hex()`, UTF-8, LF, no trailing newline이다. node, CCW triangle, sorted boundary-edge row를 모두 hash한다.

현재 manifest runtime lock은 CPython `3.12.10`, NumPy `2.4.4`, SciPy `1.18.0`, Windows `win32/AMD64`다. runtime 또는 hash가 달라지면 solve를 시작하지 않고 `BLOCKED_AV_BS_MESH_HASH`로 남긴다.

| level | nodes | triangles | boundary edges | max `kappa2(Te)` | `16u kappa2(Te)` | SHA-256 |
|---|---:|---:|---:|---:|---:|---|
| `h` | 2,049 | 3,968 | 128 | `40.7354838721` | `7.23608e-14` | `cf5c7740449d40c74665543680c2c96d848e546a3e52d27b8254ce099f3335d0` |
| `h2` | 8,065 | 15,872 | 256 | `40.8337374291` | `7.25353e-14` | `34eb4f9cadcefd0b20cff3ae6c483dbee4e412ca11d0ff1a8c2c6f49ec7896a9` |
| `h4` | 32,001 | 63,488 | 512 | `40.8337374291` | `7.25353e-14` | `a91b4bf147628a34d1a29144ae353a83110b1756c699c71e2b57e9c36822835b` |

각 level은 disk Euler characteristic `V-E+T=1`, boundary edge `128/256/512`, positive area, exact connectivity lineage를 검사한다. P1 local quality gate는 `16u kappa2(Te)<=2e-10`이고 binary64 unit roundoff는 `u=2^-53`이다.

## Mandatory numerical gates

모든 raw 값은 repair 전에 기록한다.

signed mode 집합은 `M9={-4,-3,-2,-1,0,1,2,3,4}`이고 analytic target은 `Ys,|m|`이다. 아래 식에서 `A`는 각각 `Ab,II`, `Ap,II`, `Y`는 raw `YΓ,w`, `NΓ`는 boundary node 수다. 각 RHS column `k`의 backward residual은

```text
rb(A,xk,bk) = ||A xk-bk||inf / (||A||inf ||xk||inf + ||bk||inf)
```

이며 두 operator와 모든 RHS의 maximum을 보고한다. zero denominator 또는 nonfinite 값은 pass가 아니라 solve failure다. row-max 뒤 column-max equilibration은 zero row/column을 fail-closed한 뒤 `Aeq=Dr A Dc`를 만들고, LU solve를 감싼 1-norm inverse estimator로 `kappa1(Aeq) u`, `u=2^-53`을 계산한다.

| gate | threshold |
|---|---:|
| max `Ap,II`/`Ab,II` RHS `rb` | `<=1e-10` |
| deterministic row-max then column-max equilibrated `kappa1 u` | `<=1e-8` |
| raw `||Ap-Ap^T||F/||Ap||F`, `||Ab-Ab^T||F/||Ab||F` | `<=1e-12` |
| independent order `||Yrev-Y^T||F/max(||Y||F,NΓ Yfloor)` | `<=1e-12` |
| raw full-space `||Y-Y^T||F/max(||Y||F,NΓ Yfloor)` | `<=1e-8` |
| raw passivity `lambda_min((Y+Y^H)/2)` | `>=-max(Yfloor,1e-9||Y||2)` |
| max mode power mismatch over `M9` | `<=1e-8` |
| fine analytic complex error, each signed `m in M9` | `<=0.5%` |
| `h2→h4` complex RMS / max | `<=0.5% / <=1%` |
| fine analytic eligible phase | `<=0.25 deg` |
| `h2→h4` eligible mesh phase | `<=0.25 deg` |
| `m↔-m` relative mismatch | `<=1e-8` |

여기서

```text
Yfloor = max(1e-18 S·m, 1e-10 max_mn |Ymn|)
Ymode_floor = max(1e-12 S, 1e-10 max_m |Ys,m|)

eana(m) = |Yhat_h4,m-Ys,|m|| / max(|Ys,|m||,Ymode_floor)
emesh(m) = |Yhat_h4,m-Yhat_h2,m| / max(|Yhat_h4,m|,Ymode_floor)
mesh_RMS = sqrt((1/9) sum_(m in M9) emesh(m)^2)
mesh_max = max_(m in M9) emesh(m)
edeg(m) = |Yhat_m-Yhat_-m| / max(|Yhat_m|,|Yhat_-m|,Ymode_floor), m=1…4
```

이고 analytic/mesh/degeneracy의 모든 denominator와 mode 집합은 위 정의를 사용한다. fine analytic phase는 `|arg(Yhat_h4,m/Ys,|m|)|`, mesh phase는 `|arg(Yhat_h4,m/Yhat_h2,m)|`를 degree로 계산한다. 각 비교의 양쪽 magnitude가 모두 `>=10 Ymode_floor`인 signed mode에만 해당 phase gate를 적용하며, analytic과 mesh 각각 eligible mode가 하나도 없으면 fail-closed한다. power mismatch는 위 Power identity의 분모를 signed 각 mode에 적용한 maximum이다. Frobenius symmetry denominator가 zero여도 fail-closed한다.

## Fail-closed execution sequence

1. **manifest only:** hash, count, quality와 analytic anchors를 재현한다. physics solve 없음.
2. preregistration commit과 독립 static audit가 끝난 뒤에만 17.5 µm/100 kHz의 `h`를 one-mesh command로 실행한다. 이 stage에서 평가 가능한 residual/condition/assembly-symmetry/reciprocity/passivity/power/resource gate만 검토하고 analytic 값은 trend로 기록하되 fine pass로 판정하지 않는다.
3. `h` review token이 열린 뒤에만 `h2`를 별도 one-mesh command로 실행한다. 같은 stage-evaluable gate와 `h→h2` trend를 검토하지만 fine analytic 또는 final convergence pass를 선언하지 않는다.
4. `h2` review token이 열린 뒤에만 `h4`를 별도 command로 실행한다. 이때 stage-evaluable gate, signed `M9` fine analytic/degeneracy와 mandatory `h2→h4` RMS/max/phase convergence를 처음 판정한다.
5. `h4`와 final convergence가 모두 통과하면 상태는 **`passed_AV_BS_circle_17p5um_100k_only`**다. 이는 circle interior-only다.
6. 그 뒤 같은 generator를 기하학적으로 scale한 `a=0.5 mm`, 100 kHz withheld radius의 mesh hash와 analytic anchor를 결과 전에 별도 고정하고, 동일한 `h` review → `h2` review → `h4` final-convergence chain을 반복한다.
7. 두 radius가 모두 통과하면 상태는 **`passed_AV_BS_circle_two_radius_100k_only`**다. 그 뒤에만 EQ0 A–v contract의 review token을 검토한다.

어느 단계든 실패하면 이후 stage를 시작하지 않는다. failure code는 최소한 `BLOCKED_AV_BS_MESH_HASH`, `BLOCKED_AV_BS_SOLVE`, `BLOCKED_AV_BS_RECIPROCITY`, `BLOCKED_AV_BS_PASSIVITY`, `BLOCKED_AV_BS_POWER`, `BLOCKED_AV_BS_ANALYTIC`, `BLOCKED_AV_BS_RESOURCE`를 구분한다.

## Standalone `h` fixture freeze

연구 전용 구현은 [`../../tools/research/av_bs1_boundary_schur.py`](../../tools/research/av_bs1_boundary_schur.py)와 [`../../tools/research/run_av_bs1_stage.ps1`](../../tools/research/run_av_bs1_stage.ps1)에 고정했다. H0 static checkpoint 뒤 `primary-h` command는 한 번 실행됐지만 sparse-pattern gate에서 factor 전에 차단됐고 physics response는 생성되지 않았다. `h2`, `h4`, withheld radius와 EQ0 CLI는 존재하지 않는다.

- fixture는 `src/spd_decap_pi`를 import하지 않는 standalone NumPy/SciPy 연구 도구다.
- `K`, consistent volume `M`, boundary trace `MΓ`를 raw CCW P1 element에서 조립하고 full dense interior matrix, `inverse`, `Sp-Sb`, `Dp-Db`, 사후 대칭화를 금지한다.
- `Ab,II`와 `Ap,II`를 row-max 뒤 column-max로 equilibrate하고 `COLAMD`, `diag_pivot_thresh=1`, SuperLU `Equil=False`로 순차 factor한다. 동시에 하나의 factor만 resident이고 boundary RHS batch는 `4`다.
- 각 original unscaled system의 RHS별 backward residual과 두 frozen-seed `onenormest(t=4,itmax=10)` condition surrogate를 기록한다. 이 estimate는 rigorous upper bound가 아니다.
- full harmonic extension 대신 interior `Xb`, `Xp`와 implicit boundary identity를 사용해 raw `Y=σ Hb^T M Hp`, `Yrev=σ Hp^T M Hb`를 독립 조립한다.
- numerical artifact는 `AV-BS1-h-numerical-v1`, final artifact는 `AV-BS1-h-result-v1` canonical JSON wrapper다. UTF-8 compact sorted payload의 SHA-256을 wrapper에 두며 `Y/Yrev`는 little-endian complex128 base64와 별도 hash로 보존한다.
- child failure wrapper도 finalizer까지 원래 `BLOCKED_AV_BS_*` code를 보존한다. child stdout, fixture, runner, review token, guard와 resource report hash가 서로 결합되지 않으면 result-schema failure다.

첫 실행 권한은 tracked artifact [`../../tools/research/av_bs1_primary_h_review_token.json`](../../tools/research/av_bs1_primary_h_review_token.json)에만 있다. token은 preregistration commit, h manifest, fixture/runner SHA-256, static test와 세 독립 감사에 결합된다. token과 fixture가 commit된 clean checkout이 아니면 실행은 차단된다. 이 token은 `primary-h`만 허용하며 `h2`를 열지 않는다.

외부 runner는 dedicated PowerShell runner와 Python child/descendant를 하나의 execution tree로 100 ms마다 합산한다. 성공 sample이 최소 한 번 없으면 resource gate는 실패한다. mandatory stop은 tree WS `>4 GiB`, tree private 또는 committed `>5 GiB`, system commit headroom `<2 GiB`, available physical `<1.5 GiB`다. `WorkingSet-PrivateWorkingSet`은 실제 mapped residency가 아니라 `nonprivate_working_set_proxy`로만 기록한다. h preflight는 raw `K/M/MΓ` 외에도 complex operators, block slices, raw/equilibrated solve copies와 RHS를 위한 `16×` sparse-copy allowance, dense-factor upper bound, 두 extension, boundary work, batch work와 추가 25% margin을 합산한다.

정적 gate는 다음으로 고정했다.

```text
python -m pytest -q tests/test_research_av_bs1_boundary_schur.py
# 13 passed; physics solve 없음

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File tools/research/run_av_bs1_stage.ps1 -Stage manifest
# manifest payload SHA-256 e79cd30b88fbf339399b3b059ce958138a5b16ed90bc52cde1ed0f87c2dd9a95
```

위 `13 passed`, fixture `94cce645...`, runner `31da5df...`는 **H0 historical static checkpoint**다. H0 결과와 H1 correction은 [`T1_AV_BOUNDARY_SCHUR_RESULTS.md`](T1_AV_BOUNDARY_SCHUR_RESULTS.md)에 분리했다. `.gitattributes`는 `tools/research`의 Python/PowerShell/JSON과 해당 test를 `eol=lf`로 고정해 `core.autocrlf`가 raw token hash를 바꾸지 못하게 한다. 파일 또는 line-ending policy가 바뀌면 token이 무효가 되며 재감사·새 commit 전에는 실행할 수 없다.

## H1 cyclic-diagonal correction contract

H0의 `V+2E=14,081`은 raw nodal adjacency와 consistent mass pattern이지 post-zero-elimination stiffness nnz가 아니다. 15 annular bands × 128 sectors의 triangulation diagonal 1,920개는 cyclic isosceles trapezoid를 가르므로 exact P1 stiffness weight가 0이다.

H1은 generator lineage로 만든 undirected tag 1,920개와 SHA-256 `e80c75ed02030cb22b648b39d613abac42bf6a4c4dfb46704789eedcc17f5e91`을 요구한다. 각 tag는 incident triangle이 정확히 둘이어야 하고 두 unassembled local off-diagonal contribution의 상대 상쇄는

```text
abs(k1+k2)/(abs(k1)+abs(k2)) <= 128*u*kappa2,max
```

를 통과해야 한다. h에서 우변은 `5.788860430596403e-13`, 관측 최대는 `2.1676835831040652e-13`다. tag는 magnitude로 발견하지 않는다.

raw symmetric residue `k=Kij=Kji`의 edge Laplacian block만 제거해 `Kij=Kji=0`, `Kii+=k`, `Kjj+=k`로 행합을 보존한다. 사후 평균 대칭화가 아니다. untagged off-diagonal은 바꾸지 않으며 canonical support는 `K.nnz=10,241`, `M.nnz=14,081`, `MΓ.nnz=384`여야 한다. raw/canonical K, M, MΓ value+pattern hash, constant-null residual, correction Frobenius, tag incidence와 manufactured cyclic/noncyclic control을 16개 static test에 고정한다.

H0에서 runner가 redirected child handle을 retain하지 않아 failure process의 exit code를 0으로 기록한 provenance 오류도 H1에서 수정한다. child handle을 poll 전에 획득하고 missing exit code는 fail-closed한다. success wrapper는 exit 0, canonical failure wrapper는 exit 2만 허용하며 불일치는 original failure code와 `BLOCKED_AV_BS_RESULT_SCHEMA`를 함께 보존한다.

H1 static freeze는 `16 passed`, fixture SHA-256 `e2a1c8efff67873b57dc7a658b013e3e76d0c921988011f4c8e70cd25f00f8a7`, runner SHA-256 `dd880de721b9a688ae953c7363dc4a8b482ec1b26f59871d4ca8f83397eb2b7c`다. review token은 H0 artifact SHA-256, cyclic tag hash, canonical K hash와 세 독립 감사 증거까지 검증한다. 이 checkpoint가 clean commit되기 전 H1 primary-h는 계속 차단된다.

## Conditional EQ0 A–v contract

현재 상태는 **`AV-BS1-EQ0 conditional_not_preregistered_circle_pending`**이다. circle 두 radius가 통과하기 전에는 EQ0 solve를 허용하지 않는다. 아래 항목은 후속 freeze의 경계를 정하며, exact EQ0 mesh hash와 executable fixture가 별도 commit에서 고정돼야 `preregistered_not_run`으로 바뀐다.

- frozen signal/return geometry는 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)의 `M1-EQ0`와 동일하다.
- conductor group order는 `(signal,return)`, `Hg=I2`, balanced `Bg=(+1,-1)^T`, peak `q=1 A`다.
- outer boundary는 `a=A_z=0`이고 crop은 `{2,4,8}Deff`다. authoritative output은 balanced scalar `Z'loop=Bg^T v`뿐이다.
- full `Yg` common mode는 crop-dependent diagnostic이며 physical partial operator로 보고하지 않는다.
- matrix inverse를 만들지 않고 `A X=F Hg`, `Yg=Hg^T G Hg-j omega Hg^T F^T X`, `Yg v=Bg q`와 full saddle을 독립 계산한다.
- 첫 frequency는 100 kHz다. `4Deff×{h,h2,h4}`가 통과한 뒤에만 `{2Deff,8Deff}×h4`, 그 뒤에만 2 GHz와 나머지 mandatory frequency를 연다.
- mesh/crop convergence, current, raw reciprocity/passivity, consistent-mass loss, magnetic power, DC/magnetostatic anchor와 shell energy를 모두 통과해야 한다.

기존 2 GHz `4Deff` consistent-mass smoke는 `2GHz_4Deff_smoke_only`로 보존하며 이 conditional contract를 통과한 결과가 아니다.

## Resource contract

- first circle stage는 한 mesh와 한 frequency에서 `Ab,II`, `Ap,II` 두 factorization을 순차 수행하고 동시에 한 sparse factor만 resident로 둔다. `Hb`, `Hp` rectangular extension은 boundary RHS batch `<=4`로 계산해 보존한다.
- circle interior/boundary node count는 `h 1921/128`, `h2 7809/256`, `h4 31489/512`다. fine dense complex interior `31489×31489` matrix는 약 `14.78 GiB`, all-node `32001×32001` matrix는 약 `15.26 GiB`이므로 둘 다 materialization을 금지하고 sparse assembly/factor만 허용한다. fine dense boundary Schur `512×512 complex128`은 `4.00 MiB`다.
- hard ceiling은 `250,000 nodes / 500,000 triangles`지만 이는 laptop-safe 보증이 아니다.
- preflight는 sparse matrix, assembly copy, 한 resident factor/preconditioner, 두 rectangular harmonic extension, RHS, dense boundary operator와 25% margin을 합산한다.
- process-tree peak working set 목표는 `<=4.0 GiB`다.
- private/committed bytes가 `>5.0 GiB`이면 즉시 중단한다.
- system commit headroom `<2.0 GiB` 또는 available physical memory `<1.5 GiB`이면 시작하거나 계속하지 않는다.
- wall, process-tree peak WS/private/commit, mapped residency, page fault와 OS baseline을 보존한다. process-only 수치를 8 GB proof로 승격하지 않는다.

## Production SAO 후보와의 분리

[Hamiltonian Schur DtN](https://doi.org/10.1016/j.wavemoti.2007.07.004)은 AV-BS1의 내부 알고리즘이나 truth source가 아니다. A–v가 독립 reference로 승격된 뒤 이를 상대로 검증할 future production candidate `T1-M1-HS1_production_candidate_not_preregistered`다. four-operator symmetric transmission formulation도 같은 후속 범위이며 AV-BS1 결과를 보고 operator space나 sign을 조정하지 않는다.

## Exact next starting point

1. H0 artifact를 immutable하게 보존하고 H1 fixture, runner, 16개 bounded test, result schema와 새 tracked primary-h review token을 함께 commit한다. physics solve는 이 commit에 포함하지 않는다.
2. clean committed checkout에서 외부 runner로 17.5 µm/100 kHz `h` 한 mesh만 재실행한다.
3. cyclic canonicalization certificate와 stage-evaluable raw residual/condition/reciprocity/passivity/power, execution-tree resource artifact를 독립 검토해 실패면 즉시 동결한다.
4. `h` 결과 review 뒤 별도 preregistration·token·fixture commit이 생기기 전에는 `h2`를 구현하거나 실행하지 않는다.

## Primary literature

- A. Piwonski et al., “Finite Element Modeling of Power Cables using Coordinate Transformations,” [author preprint](https://arxiv.org/abs/2307.00814), [IEEE TMAG DOI](https://doi.org/10.1109/TMAG.2023.3318292). A–v volume-FEM family의 독립 근거다.
- U. R. Patel and P. Triverio, “Skin Effect Modeling in Conductors of Arbitrary Shape Through a Surface Admittance Operator and the Contour Integral Method,” [author preprint](https://arxiv.org/abs/1509.08357), [IEEE T-MTT DOI](https://doi.org/10.1109/TMTT.2016.2593721). 비교할 SAO continuous operator의 근거다.
- L. Knockaert et al., “On the Schur complement form of the Dirichlet-to-Neumann operator,” [DOI](https://doi.org/10.1016/j.wavemoti.2007.07.004). 후속 Hamiltonian-Schur production candidate의 근거이며 AV-BS1의 승격 근거로 사용하지 않는다.
