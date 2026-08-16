# SPD Decap PI Evaluator v0.22.0 — AV-BS1 Boundary-Schur Preregistration

> **SPD Decap PI Evaluator v0.22.0 — retry-v10 final documentation
> freeze:** Ten public attempts are immutable. The tenth ran once and exited
> public stage `2`; producer/finalizer/consumer PIDs `54044`/`25200`/`41812`
> exited `0`, exactly two factor certificates/prefixes were retained in order
> `A_background_II`, `A_conductor_II`, factor/resource gates passed, and all
> `205/205` identities were absent after cleanup. Inner/outer monitoring used
> `245`/`664` samples over `34.1760023`/`91.722828 s`; peak working set was
> `511868928`/`495939584`, private bytes `1994665984`/`1993838592`, lifetime
> commit `2322489344`/`2335375360`, with `39` nontruncated outer retries. Host
> availability was about 45 GB, so no 8 GB claim follows. No RHS, solve,
> extension, `Y`, modal response, H4 physics, or PowerSI ran. Three order-only
> claim/guard/resource JSON mismatches made the raw seal non-authoritative.
> Strict classification is `consumed_v2_provisional_invalid_terminal_evidence`,
> `authoritative_terminal_evidence=false`, disposition `null`, exact terminal
> detail `BLOCKED_AV_BS_RESULT_SCHEMA: terminal pre-exit
> normal_pass_outer_evidence_reconciliation_pass mismatch`. M-only
> `785f5e9c0a38ad1851c4ba620f520db9087aaf74` then D-only
> `a139d867bbc727b58f9a7a4cdbad604fc03070cf` leave token absent/next false.
> Retry-v10 changes only strict JSON: strict UTF-8, duplicate/nonfinite reject,
> object root/depth32/16 MiB bounds, ordinal O(n) key/string/field/evidence
> equality, and lowercase SHA-256 hex guards. Schemas, factor math,
> retry/resource policy, and physics boundary are unchanged. Bindings: Python
> `46082123fbf98102f3e5995c32bf65d8d04bb835b9c1305de0150349e75e48c7`,
> runner `f0159061dbd4d1b34881911edfdfb72146a3a23e5cdc75ca8fa4069c08aadb86`
> (`484795` bytes), tests
> `e5496ceb24c33c835b88ce20a3f67f941c8279e0471708a01022238fc211b109`
> (`488591` bytes). Collection `400`; current-byte focused `5/5` in `2.73 s`;
> independent same-byte `5/5` in `2.71 s`. A provisional full attempt reached
> `398 passed, 2 failed in 94.17 s` from two corrected static contract drifts;
> the first successful full exact-document suite then passed `400/400` in
> `93.89 s`, exit `0`, with no failure. This is the **FINAL DOC FREEZE**; any
> separately authorized run would be eleventh.
>
> **Historical retry-v9 final documentation
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
>
> Every pre-existing retry-v9, ninth-attempt, future-tenth, `397/397`, or
> **FINAL DOC FREEZE** statement later in this file is an immutable historical
> snapshot. Only the retry-v10 block above states current authority.


최종 갱신: 2026-08-16 (Asia/Seoul)

## 판정과 범위

현재 physics artifact 상태는 **`passed_AV_BS_h2_stage_only_pending_h4_preregistration`**이다. H1 17.5 µm/100 kHz coarse `h`와 H2 refined `h2`가 각각 stage-evaluable gate를 통과했지만 fine analytic/mesh convergence/final circle은 `null`이고 `next_stage_authorized=false`다. H4-P0와 H4-P0R parent를 보존한다. 아홉 번째 P1은 factor producer PID `51184`에서 `A_background_II`, `A_conductor_II` 두 certificate/prefix와 factor/resource gates를 통과했지만 offline verifier PID `37248`를 producer로 잘못 사용한 published finalizer failure로 끝났다. Raw seal은 strict chronology-invalid라 authoritative하지 않고 retained classification은 `consumed_v2_provisional_invalid_terminal_evidence`다. RHS, solve, H4 physics는 미실행이다. 현재 P1 token은 없고 retry-v9 provisional candidate만 있으며 first full exact-document suite는 `397/397` in `92.95 s`, exit `0`, no failure로 통과했다. 문서는 FINAL DOC FREEZE이고 `factor_fit_unproven=true`; PowerSI도 승인하지 않는다. 이 문서는 독립 volume-FEM reference candidate를 고정하며 제품 parser, solver, UI, version과 installer는 수정하지 않는다.

AV-BS1은 아직 oracle이 아니다. H2는 local one-mesh gate만 통과했고 `h→h2` RMS/max/phase `0.462796%/0.868015%/0.000166396°`는 trend-only다. 이후 별도 `h4` mesh convergence, fine analytic gate, final circle 판정과 withheld radius를 모두 통과해야 circle-interior reference로 제한 승격할 수 있다. 이 문서의 어떤 결과도 finite/open EQ0, full T1, GlobalMNA, PowerSI accuracy 또는 8 GB product performance를 승인하지 않는다.

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
3. H2-P0 topology/assembly/resource manifest와 H2-P1 fixture/runner/result contract를 별도 clean commits와 static review에 고정한 뒤 one-use token으로 `h2`를 한 번 실행했다. 같은 stage-evaluable gate와 `h→h2` trend를 검토했지만 fine analytic 또는 final convergence pass는 선언하지 않았고 token은 consumed 상태다.
4. H2 result와 consumed tombstone을 review/commit하고 별도 H4-P0 mesh/assembly/dual-resource manifest를 고정했다. prospective 2 GiB factor cap은 fit이 미증명이므로 이 manifest만으로 `h4`를 승인하지 않는다.
5. 별도 H4-P0R manifest-only fixture/runner/test/doc가 `KII/AbII/MII/ApII`, equilibration, factor-only result/resource/lifecycle schema와 forbidden operation을 고정했고 23개 정적 test 및 세 독립 감사를 통과했다. 이 parent 단계에는 token, claim, finalizer, factorization 또는 physics 경로가 없다.
6. 후속 P1 executable/runner/claim/finalizer/outer observer와 one-use lifecycle을 정적 감사했다. 첫 네 fresh-token 공개 시도는 claim/factor 전에, 다섯 번째는 factor monitor에서, 여섯 번째는 claim/factor child 뒤 `_factor_one`/`splu` 전에 중단됐다. 일곱 번째와 여덟 번째의 immutable 경계는 아래 retry-v7/v8 historical sections에 보존한다. 아홉 번째는 producer PID `51184`에서 exactly two factor certificates/prefixes와 inner/outer resource gates를 통과하고 182/182 identities dead를 확인했지만, offline verifier PID `37248`를 producer로 잘못 바인딩한 published finalizer failure 및 strict-invalid raw seal로 비권위 종료했다. 아홉 token은 재사용 없이 폐기했고 현재 token은 없다. retry-v9은 explicit resource child PID를 offline validators에 전달하고 helper bootstrap marker clock을 `time.perf_counter_ns()`/QPC로 통일할 뿐이다. Maximum3/cap64 fields와 schemas는 유지한다. RHS, harmonic extension, boundary `Y`, modal response와 physics result는 만들지 않는다. First full exact-document suite는 `397/397` in `92.95 s`, exit `0`, no failure로 통과했고 현재 문서 상태는 FINAL DOC FREEZE다.
7. P0R result audit 뒤 H4 physics/result contract, clean commit, static audit와 새 one-use token을 고정한 뒤에만 `h4`를 별도 command로 실행한다. 이때 stage-evaluable gate, signed `M9` fine analytic/degeneracy와 mandatory `h2→h4` RMS/max/phase convergence를 처음 판정한다.
8. `h4`와 final convergence가 모두 통과하면 상태는 **`passed_AV_BS_circle_17p5um_100k_only`**다. 이는 circle interior-only다.
9. 그 뒤 같은 generator를 기하학적으로 scale한 `a=0.5 mm`, 100 kHz withheld radius의 mesh hash와 analytic anchor를 결과 전에 별도 고정하고, 동일한 `h` review → `h2` review → `h4` final-convergence chain을 반복한다.
10. 두 radius가 모두 통과하면 상태는 **`passed_AV_BS_circle_two_radius_100k_only`**다. 그 뒤에만 EQ0 A–v contract의 review token을 검토한다.

어느 단계든 실패하면 이후 stage를 시작하지 않는다. physics stage failure code는 최소한 `BLOCKED_AV_BS_MESH_HASH`, `BLOCKED_AV_BS_SOLVE`, `BLOCKED_AV_BS_RECIPROCITY`, `BLOCKED_AV_BS_PASSIVITY`, `BLOCKED_AV_BS_POWER`, `BLOCKED_AV_BS_ANALYTIC`, `BLOCKED_AV_BS_RESOURCE`를 구분한다. H4-P0R factor-only pilot은 이 목록을 임의 확장하지 않고 정확히 `BLOCKED_AV_BS_FACTOR`, `BLOCKED_AV_BS_MESH_HASH`, `BLOCKED_AV_BS_RESOURCE`, `BLOCKED_AV_BS_RESULT_SCHEMA`의 네 code만 허용한다.

## Standalone `h` fixture freeze

연구 전용 H1 구현은 [`../../tools/research/av_bs1_boundary_schur.py`](../../tools/research/av_bs1_boundary_schur.py)와 [`../../tools/research/run_av_bs1_stage.ps1`](../../tools/research/run_av_bs1_stage.ps1)에 고정했다. H0 `primary-h`는 sparse-pattern gate에서 factor 전에 차단됐고 H1은 같은 h를 다시 실행해 stage-evaluable gate를 통과했다. H2-P0에는 [`../../tools/research/av_bs1_boundary_schur_h2.py`](../../tools/research/av_bs1_boundary_schur_h2.py)와 manifest-only runner가 있다. H2-P1 [`../../tools/research/av_bs1_boundary_schur_h2_p1.py`](../../tools/research/av_bs1_boundary_schur_h2_p1.py)와 [`../../tools/research/run_av_bs1_h2_p1_stage.ps1`](../../tools/research/run_av_bs1_h2_p1_stage.ps1)은 clean preregistration와 one-use token 뒤 `primary-h2`를 실행했고 consumed tombstone으로 재실행을 막는다. H4-P0에는 [`../../tools/research/av_bs1_boundary_schur_h4_p0.py`](../../tools/research/av_bs1_boundary_schur_h4_p0.py)와 manifest-only runner만 있다. H4-P0R parent에는 [`../../tools/research/av_bs1_boundary_schur_h4_p0r.py`](../../tools/research/av_bs1_boundary_schur_h4_p0r.py)와 manifest-only runner가 있다. 후속 P1 [`../../tools/research/av_bs1_boundary_schur_h4_p0r_p1.py`](../../tools/research/av_bs1_boundary_schur_h4_p0r_p1.py)와 [`../../tools/research/run_av_bs1_h4_p0r_p1_stage.ps1`](../../tools/research/run_av_bs1_h4_p0r_p1_stage.ps1)은 token-gated `primary-h4-p0r` factor-only path와 outer observer를 구현했지만 현재 token이 없고 여덟 prior token commit은 폐기됐다. `primary-h4`, withheld radius와 EQ0 solve CLI는 여전히 없다.

- fixture는 `src/spd_decap_pi`를 import하지 않는 standalone NumPy/SciPy 연구 도구다.
- `K`, consistent volume `M`, boundary trace `MΓ`를 raw CCW P1 element에서 조립하고 full dense interior matrix, `inverse`, `Sp-Sb`, `Dp-Db`, 사후 대칭화를 금지한다.
- `Ab,II`와 `Ap,II`를 row-max 뒤 column-max로 equilibrate하고 `COLAMD`, `diag_pivot_thresh=1`, SuperLU `Equil=False`로 순차 factor한다. 동시에 하나의 factor만 resident이고 boundary RHS batch는 `4`다.
- 각 original unscaled system의 RHS별 backward residual과 두 frozen-seed `onenormest(t=4,itmax=10)` condition surrogate를 기록한다. 이 estimate는 rigorous upper bound가 아니다.
- full harmonic extension 대신 interior `Xb`, `Xp`와 implicit boundary identity를 사용해 raw `Y=σ Hb^T M Hp`, `Yrev=σ Hp^T M Hb`를 독립 조립한다.
- numerical artifact는 `AV-BS1-h-numerical-v1`, final artifact는 `AV-BS1-h-result-v1` canonical JSON wrapper다. UTF-8 compact sorted payload의 SHA-256을 wrapper에 두며 `Y/Yrev`는 little-endian complex128 base64와 별도 hash로 보존한다.
- child failure wrapper도 finalizer까지 원래 `BLOCKED_AV_BS_*` code를 보존한다. child stdout, fixture, runner, review token, guard와 resource report hash가 서로 결합되지 않으면 result-schema failure다.

H1 실행 권한은 당시 tracked [`../../tools/research/av_bs1_primary_h_review_token.json`](../../tools/research/av_bs1_primary_h_review_token.json)에만 있었다. authorized token SHA-256 `5ad21ccec9cb81e8999441fc43338589ecb92cf0ae08e7bc2b8b4a8c411f8d4e`는 commit `057ed39`과 H1 artifact에 보존된다. 독립 review 뒤 active token 파일은 SHA-256 `80ffd8b486dbdd8087eb205f71137663cef0497d8ba8df74253743b302fe6f35`의 `AV-BS1-review-token-consumed-v1`, `next_stage_authorized=false` tombstone으로 교체하므로 이후 clean descendant commit에서도 `primary-h` 재실행은 fail-closed한다. h2는 별도 filename/schema/token/commit만 허용한다.

H2 실행 권한은 tracked [`../../tools/research/av_bs1_h2_p1_review_token.json`](../../tools/research/av_bs1_h2_p1_review_token.json)의 authorized SHA-256 `f8a1aa5804b0edfd58f485faf83f7c6f73c284bc04b1a4abffbd8d0260aca4df` 한 번에만 있었다. artifact SHA-256 `b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55`의 독립 review 뒤 active file은 SHA-256 `81574c1099bdd140004940b7cb768a20298b153445c1b16b9e21de95011c48c2`의 `AV-BS1-h2-p1-consumed-review-token-v1`, `uses_remaining=0`, `next_stage_authorized=false`로 교체됐다. h4는 또 다른 filename/schema/commit/token만 허용한다.

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

H1 static freeze는 `16 passed`, fixture SHA-256 `e2a1c8efff67873b57dc7a658b013e3e76d0c921988011f4c8e70cd25f00f8a7`, runner SHA-256 `dd880de721b9a688ae953c7363dc4a8b482ec1b26f59871d4ca8f83397eb2b7c`다. authorized token은 H0 artifact, cyclic tag, canonical K와 세 독립 감사 증거를 검증했고 H1 artifact 생성 뒤 consume됐다. 실제 H1 수치와 범위는 [`T1_AV_BOUNDARY_SCHUR_RESULTS.md`](T1_AV_BOUNDARY_SCHUR_RESULTS.md)에 고정한다.

H2-P0 refined lineage, 3,712-tag 선택, raw/canonical `K/M/MΓ` hash와 conservative resource arithmetic은 [`T1_AV_BOUNDARY_SCHUR_H2_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H2_PREREG.md)에 고정한다. H2-P1의 token/claim/guard/result/tombstone contract와 독립 modal certificate는 [`T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md)에 고정한다. H4-P0 topology/assembly는 [`T1_AV_BOUNDARY_SCHUR_H4_P0_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H4_P0_PREREG.md)에, H4-P0R factor-only parent는 [`T1_AV_BOUNDARY_SCHUR_H4_P0R_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H4_P0R_PREREG.md)에, P1 lifecycle과 아홉 interruption, immutable retry-v8 history 및 현재 retry-v9 provisional 계약은 [`T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md)에 고정한다. 실제 artifacts/gates는 [`T1_AV_BOUNDARY_SCHUR_RESULTS.md`](T1_AV_BOUNDARY_SCHUR_RESULTS.md)에 기록한다. 어느 manifest 통과도 physics를 자동 승인하거나 후속 token을 발급하지 않는다.

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

## Retry-v4 execution-only amendment

The fourth public P1 attempt is preserved as a pre-claim/pre-factor interruption.
Token-only commit `9b4854d0...` ran exactly once and failed in control preflight
because an exited PID with the exact bound birth remained in the initial complete
snapshot. Its outer close SHA-256 is
`73073432faa0525f560b6536ea2a5fd54329ba3a30f04ab05ab8cef59460e758`.
The token was semantically spent and removed by `f1aeeac...`; current token is
absent. No matrix factor, RHS, solve, extension, boundary operator, modal response,
H4 physics, PowerSI, or 8 GiB result was created.

Retry-v4 does not change this mathematical specification, the Python fixture, or
any schema. It adds only two 25 ms complete-snapshot rechecks for the exact
`exited + positive same birth + initial snapshot present` transition, and only
in existing explicit outer/control `MaximumAttempts>1` contexts. The maximum is
three snapshots total. Final absence uses the existing confirmed-disappearance
event; all default-one factor calls and every other fail-closed identity path are
unchanged. Frozen static SHA-256 bindings are Python
`95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234`, runner
`7893cd57fd4b1686addd434fa0103d9f3b0e0087f4783f2c82e459661abfb645`, tests
`18aabcdf7b32c6b013aec65497d31f4d908f3085f53f64cb3791f38a2e79381d`;
focused `11/11` and full no-cache `322/322` passed. `next_stage_authorized=false`.

## Retry-v5 execution-only amendment

The fifth public attempt (`5ba4b693...`, exit `2`) created the claim and reached
factor child PID `55552`, which was visible for `108/108` successful samples.
The monitor then stopped on `NONROOT_DISAPPEARANCE_NOT_CONFIRMED` because the
four active factor samples still used default maximum `1`. There is no prefix,
certificate, completion marker, or release, so actual factor entry is
indeterminate and completed factor count is zero. The mathematical operator,
RHS, extension, boundary response, modal physics, and accuracy gates did not run.

Outer close `9d0c0aab...` counted/stored `27/16` retry events, marked
truncation, and withheld the seal. The attempt is consumed in `46c08d405...`
and its token retired by `71d3dab...`. Retry-v5 only opts the active four
factor samples into maximum `3` and changes the shared bounded outer/control
event cap to `64`; retry predicates and all other fatal/default-one paths are
unchanged. Empty-stdout normalization is deferred. Frozen Python/runner/tests
SHA-256 are `54aa7da9daa013cec41585ae07757e6c54e8e9d15da7f4f16d4f6546b6fb35aa` /
`3888524877f7a90966fb932f7f9fc4c9a48480134eb6295712eaa0ef548416a3` /
`b51498ebe27a0210a09b3a12b26e0146d39c1249906469bcb1add1d2f2443f08`;
focused/full `29/29` and `324/324` passed. This execution amendment does not
change any equation or physics gate; token absent and next false.

## Retry-v6 execution-only amendment

The sixth public attempt (`82775327...`, exit `2`) created claim and factor child
PID `55380`, but direct child evidence failed before `_factor_one`/`splu` on
`claimed preflight payload mismatch`. Attempted/performed are `false/false` and
completed/certified factors are zero. Independent outer close `d2777207...`
exhausted max3 for PID `53404`, retained `30/30` retry events without
truncation, verified cleanup, and wrote no seal. Neither path executed RHS,
extension, boundary response, modal physics, PowerSI, or accuracy work.

Emergency record `43bb34b2...` is exact but strict-validator-invalid because
nested `bindings.resource_policy_sha256` is absent; `06061a2...` preserves it
provisionally and `c001b44...` delete-retires the token. Retry-v6 only deletes
the redundant post-claim Python comparison and adds that runner binding key.
Frozen Python/runner/tests SHA-256 are `2373a13f...` / `852ce8a0...` /
`f1d0b044...`; focused `8/8` passed and full no-cache `330/330` passed in
`81.83 s`. No equation, retry policy/cap, schema, ABI, validator, or physics gate
changes; token absent and next false.

## Retry-v7 native/exported nnz amendment

The seventh attempt at `4f60bd5...` completed the native `splu` return for
`A_background_II` but failed the certificate equality guard. Durable state is
attempted/performed `true/true`, completed-name count one, certificate/prefix
count zero; `A_conductor_II` was not attempted and exact native/exported counts
are absent. Resource/outer/seal SHA-256 `0f738eb5...` / `0e88d08f...` /
`0b2f5623...` bind `174/573` samples, `31` nontruncated outer retry events,
passing resource gates, complete terminal evidence, authoritative pass false,
and next false. This is not accuracy or 8 GiB evidence.

Retry-v7 changes only the existing nnz interpretation: require
`0 < exported <= native`, validate the native and exported portable-byte
formulas separately, and include both in the existing cap. Schema, messages,
retry policy, factor order, equations, and physics gates remain unchanged.
Frozen Python/runner/tests SHA-256 are `7a1dba5e...` / `852ce8a0...` /
`bd2e3e2c...`; focused `25/25` and full no-cache `341/341` passed, the latter in
`88.40 s`. Commits `51e5069...` / `17414cf...` leave the token absent. No RHS,
solve, H4 physics, or PowerSI work ran.

## Retry-v8 historical storage and contiguous-identity amendment

The eighth public attempt at retry-v7 token-only commit `66211ef...` completed
native `splu` for `A_background_II`, so attempted/performed are `true/true` and
the completed-name count is one. Combined non-canonical L/U storage left zero
certificates/prefixes. `A_conductor_II`, RHS, solve, and physics did not start.
The resource artifact passed, while independent outer/control false exhaustion
across distinct confirmed-dead helpers left no terminal seal or published
result. Seven temporary evidence files are byte-identical ignored-quarantine
copies. Valid provisional emergency consumption `8fce704...` and D-only
retirement `7964464...` leave no token.

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

## Retry-v9 offline identity and QPC amendment

The ninth public invocation ran once from retry-v8 token-only commit
`0597872eeaedd359ce5a3d429748dee2b70ec9a7`, token ID
`0ec78f5300cc4a63923e735e81b7e713`, and exited `1`. Producer PID `51184`
exited `0` after `factor_certificates_complete`, with exactly two certificates
and two ordered prefixes (`A_background_II`, `A_conductor_II`), both factor cap
gates passed, inner/outer resource gates passed, and `182/182` process identities
dead. RHS, solve, extension, boundary `Y`, modal response, and physics did not
run.

The published finalizer wrapper is `BLOCKED_AV_BS_RESULT_SCHEMA` /
`monitor-ready child_process_id mismatch`: offline verifier PID `37248` was
used where the resource-bound producer PID `51184` was required. A raw seal
exists, but strict `_validate_outer_terminal_evidence` rejects `terminal control
marker chronology invalid`; it is not authoritative. Three canonical-JSON
helper bootstraps mixed `time.monotonic_ns()`/GetTickCount64 with Stopwatch/QPC,
so retained tombstone-plus-seal classification is
`consumed_v2_provisional_invalid_terminal_evidence`.

Retry-v9 only threads the explicit resource child PID through offline validators
and changes bootstrap marker timestamps to `time.perf_counter_ns()`/QPC;
bootstrap SHA-256 is
`0c92a0ec8fe67868e222647782cb5eedabffcad77319348f55beaeb0dd745404`.
M-only `b9c1964e8a445b2d45487894485dc8453bd002d4` and D-only
`c14f6309e6b84effbc5139ed8dcdcf05d5ea61f1` leave the token absent.
Schemas, strict gates, retry/max3/cap64, factor order/caps, resource ceilings,
and the no-RHS/no-solve/no-physics boundary are unchanged.

Frozen Python/runner/tests SHA-256 are
`46082123fbf98102f3e5995c32bf65d8d04bb835b9c1305de0150349e75e48c7`
(`579582` bytes),
`229aa91b04bd89e4af03f9a75c52a0a4819381ec5dfa32a272fe35ac7f1d94f9`
(`443675` bytes), and
`2106e5338158d2f61a63d572b6f452bd54b752f38500a566f573759096f2c36d`
(`458913` bytes). Collection is `397`; focused exact `20/377` passed/deselected
in `2.33 s`, independent same-byte `20/377` in `2.37 s`, and compatibility
`7/390` in `0.95 s`. The first full exact-document suite passed `397/397` in
`92.95 s` with exit `0` and no failure. This is the **FINAL DOC FREEZE**.
`factor_fit_unproven=true` and
`next_stage_authorized=false`; any separately authorized future invocation is
tenth.

## Exact next starting point

1. H0 negative와 H1 h-stage artifact/checksum/resource 결과를 immutable하게 보존하고, H1 primary-h token을 consumed tombstone으로 유지한다.
2. 완료된 H2-P0 refined lineage/assembly/resource manifest와 exact fixture/runner/test bytes의 commit `4c1e3fce8aac659dd0aedb06c2b6d274fff73a12`를 보존한다.
3. 완료된 H2 artifact, 독립 audit와 consumed tombstone을 research commit에 고정한다.
4. 완료한 H4-P0 assembly manifest와 H4-P0R contract-only manifest를 clean research commit에 고정한다.
5. H4-P0R-P1의 아홉 interruption, token retirement, validation-output와 retained evidence를 보존한다. retry-v9 fixture/runner/tests/docs, first full exact-document `397/397` in `92.95 s`, exit `0`, no failure 및 FINAL DOC FREEZE를 token-absent provisional runtime contract와 독립 audit에 고정한다. retired token commits를 재실행하지 않는다.
6. Exact final-byte reread, 독립 audit와 별도 승인 뒤 fresh token이 생긴 경우에만 열 번째 factorization-only pilot을 검토한다. RHS/solve/extension/Y/modal/physics는 금지하고, authoritative P0R 결과 독립 감사와 별도 H4-P1 사전등록·clean commit·fresh one-use token 전에는 h4 physics solve를 실행하지 않는다. 모든 경우 `next_stage_authorized=false`다.

## Primary literature

- A. Piwonski et al., “Finite Element Modeling of Power Cables using Coordinate Transformations,” [author preprint](https://arxiv.org/abs/2307.00814), [IEEE TMAG DOI](https://doi.org/10.1109/TMAG.2023.3318292). A–v volume-FEM family의 독립 근거다.
- U. R. Patel and P. Triverio, “Skin Effect Modeling in Conductors of Arbitrary Shape Through a Surface Admittance Operator and the Contour Integral Method,” [author preprint](https://arxiv.org/abs/1509.08357), [IEEE T-MTT DOI](https://doi.org/10.1109/TMTT.2016.2593721). 비교할 SAO continuous operator의 근거다.
- L. Knockaert et al., “On the Schur complement form of the Dirichlet-to-Neumann operator,” [DOI](https://doi.org/10.1016/j.wavemoti.2007.07.004). 후속 Hamiltonian-Schur production candidate의 근거이며 AV-BS1의 승격 근거로 사용하지 않는다.
