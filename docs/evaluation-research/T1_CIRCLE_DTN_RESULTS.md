# SPD Decap PI Evaluator v0.22.0 — T1 Circle DtN Results

> **SPD Decap PI Evaluator v0.22.0 — retry-v8 current static boundary:** Eight
> public `primary-h4-p0r` invocations are immutable. In the eighth, run from
> retry-v7 token-only commit `66211ef...`, native `splu` returned for
> `A_background_II`; attempted/performed are `true/true` and its completed name
> is durable, but combined non-canonical factor storage stopped the certificate
> path with zero certificates and prefixes. `A_conductor_II`, RHS, solve, and
> physics never started. The resource artifact passed, while independent
> outer/control monitoring falsely exhausted retries across distinct
> confirmed-dead helpers; no terminal seal or published result exists. Seven
> temporary evidence files were copied byte-identically into ignored quarantine.
> Valid provisional emergency consumption commit `8fce704...` and D-only
> retirement `7964464...` leave the token absent. Retry-v8 records raw L/U hashes,
> raw composite canonical flags (false when unsorted), and zero scans. The pre-sort
> `has_sorted_indices` value is a local sort predicate, not a certificate field;
> chunked column spans and row bounds are validated, nonfinite values and explicit
> zeros fail, and every unsorted L/U is sorted in place. Sorted/canonical format is
> then rechecked, which rejects duplicates; the canonical hash helper enforces that
> format, and finite/zero data is re-scanned. It makes no schema or field bump, L/U
> or whole-factor matrix copy, coalescing, or pruning. Attempts group only within
> one `Get-TreeSample` producer call and state resets per invocation. Cumulative
> report history may therefore contain adjacent same-identity attempt `1/1` entries
> separated by a successful return; only the known one-call close suffix enforces
> adjacency. Within a call, stored event 64 fails before retry continuation; reused
> diagnostics can advance beyond 64 only as failed/truncated evidence, never a
> passing sample. Max3/cap64 fields and schemas remain unchanged, and cleanup-race
> hardening is deferred. Frozen Python/runner/tests SHA-256 are `c26112b0...` /
> `7e675cc3...` / `426f8388...`. Collection is `387`; focused root `23`
> passed / `363` deselected and independent `27` passed / `360` deselected;
> the full exact-document suite passed `387/387` in `94.11 s`.
> `factor_fit_unproven=true`; `next_stage_authorized=false`. Earlier retry-v7
> sections are immutable history. Exact evidence is in
> [results](T1_AV_BOUNDARY_SCHUR_RESULTS.md) and the
> [reproduction appendix](ORACLE_REPRODUCTION.md).


최종 갱신: 2026-08-16 (Asia/Seoul)

이 문서는 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)의 mandatory circular-conductor interior DtN gate를 제품 코드 밖의 bounded prototype으로 실행한 결과다. 제품 parser/solver/UI는 수정하지 않았다.

핵심 결론은 두 판정을 분리하는 것이다.

| candidate | 판정 | 의미 |
|---|---|---|
| `C0-A0 fixed_low_frequency_1e6` | **failed** | 사전 등록한 Patel–Triverio empirical switch를 그대로 사용하면 작은 원 100 kHz가 정확도·mesh·phase·conditioning gate를 모두 위반 |
| `C0-A1 direct_scaled_H2_primary` | **passed_circle_interior_only** | `C0=1` direct/scaled Hankel이 canonical full-condition과 W1/W3 dense withheld를 통과; W2는 analytic/convergence-only 보조 |
| finite-width M1 | G1 exterior-only, G2 pair-only; 100 kHz circle failed | q/analytic/mesh/passivity 통과와 별개로 N256 raw `Yw` reciprocity/cancellation fail; planned G2 2 GHz circle/N512/EQ0 seed 미실행 |
| independent A–v | `2GHz_4Deff_smoke_only`; circle boundary-Schur `passed_AV_BS_h2_stage_only_pending_h4_preregistration` | H1/H2 stage-only pass와 H4 parents를 보존한다. 여덟 번째 P1은 `A_background_II` native return 뒤 combined non-canonical storage로 certificate/prefix 0에서 차단됐고 `A_conductor_II`는 미시도다. Resource artifact pass지만 distinct-helper outer/control false exhaustion으로 seal/published result는 없다. retry-v8 candidate/token absent/factor fit unproven/next=false. `h4` physics, fine analytic/convergence, final circle, withheld radius와 cross-method 미실행 |
| product/global | `blocked` | source owner, exact-minus-core와 balanced global adapter 미해결 |

`C0-A1`의 통과는 원형 도체의 **interior surface-admittance operator**만 인증한다. M1, P2, PowerSI correlation, product 정확성 또는 8 GB production 성능을 승인하지 않는다.

## Exact circular reference

CCW circular contour, outward radial normal, `e^{jωt}`에서 mode `m`의 exact solution은

```text
Ez(r,θ) = Em Jm(kr)/Jm(ka) exp(jmθ)

Dm(k) = k/(jωμ) · Jm'(ka)/Jm(ka)
Ys,m  = Dm(kp) - Dm(kb)
```

다. `kp`는 `Re(kp)>0, Im(kp)<0`, `kb`는 positive-real branch다. `Jm'/Jm`은 같은 scale의 `jve` adjacent-order ratio로 계산했다. canonical 70 complex values의 serialization checksum은

```text
ed91cad8f9c481bcb0c7749ea05d75fd9d26ab33fde258b23c6a66c60bc623e0
```

이다. direct `jvp/jv` safe-point cross-check와 최대 상대 차이는 `8.96e-16`, root-square residual은 `1.68e-16`이었다.

## Pulse discretization certificate

uniform angular chord panel의 첫 행을 `ur=U[0,r]`, `pr=P[0,r]`라 하고

```text
U[i,j] = u[(j-i) mod N]
P[i,j] = p[(j-i) mod N]
em,n   = exp(+j2πmn/N)
```

로 고정한다. regular polygon symmetry 때문에 `U`, `P`는 circulant이고 mode symbol은

```text
Ûm = Σr ur exp(+j2πmr/N) = N·ifft(u)[m]
P̂m = Σr pr exp(+j2πmr/N) = N·ifft(p)[m]

D̂m    = Ûm/P̂m
Ŷs,m  = D̂p,m - D̂b,m
```

이다. `fft(first_row)[m]`은 반대 mode를 선택하므로 사용하지 않는다. 이는 이 원형 case에서 모든 chord 길이가 같아 `W=ℓI`인 경우에만 full dense `P H=U E` solve 뒤 weighted Rayleigh quotient와 그대로 동일하다. nonuniform/corner M1에는 이 circulant shortcut을 일반화하지 않는다. prototype은 다음 두 경로를 모두 검사했다.

1. circulant symbol로 전체 mesh/quadrature corpus를 빠르게 screening
2. first row에서 full dense matrix를 구성하고 equilibrated LU로 `P H=U E`를 풀어 residual/condition/Rayleigh를 독립 확인

`Pmm`은 Neumann singular subtraction을 사용하고 `|kℓ/2|<=1e-3`에서는 문서의 complex-log small-argument anchor를 사용했다. off-diagonal은 20-point Gauss, parity는 10→20 또는 독립 holdout의 8→16으로 검사했다.

## Frozen negative result — `C0-A0`

사전 등록 정책은

```text
C0 = 1e6  if Δ/δ <= 0.5
C0 = 1    otherwise
```

였다. `a=17.5 µm`, `f=100 kHz`, `m=2`, `C0=1e6`에서 frozen 결과는 다음이다.

```text
analytic Ys = 173.833308261 - j0.052191983 S
N512 Ys     = 173.564355046 + j5.364668010 S

fine analytic error       = 3.119961651%
N256 -> N512 change       = 9.323458492%
fine phase error          = 1.787583411 deg
equilibrated max κ1 u     = 8.9144e-8
backward residual         <= 4.6283e-16
10 -> 20 quadrature       = 7.0874e-7 relative
```

한 mode의 error sequence는 `N=128/256/512/1024/2048/4096`에서 `49.36344/12.43344/3.11996/0.78145/0.19552/0.04891%`로 약 2차 수렴했다. 따라서 식이나 solve residual 문제가 아니라 큰 `C0`가 straight-chord geometry error를 증폭한 case다. N512 gate를 완화하거나 A0 실패를 A1 성공으로 덮어쓰지 않는다.

연속 kernel에서

```text
C0 Jν - jYν = (C0-1)/2 Hν^(1) + (C0+1)/2 Hν^(2)
```

다. fourth-quadrant `kd`에서 `H^(1)` 성분은 지수적으로 커질 수 있다. `C0`는 물성이 아니라 수치 representation 선택이므로 큰 값 자체를 정확성 기본값으로 취급하지 않는다.

## Selected research candidate — `C0-A1`

후속 circle 연구에 사용할 policy는 다음으로 고정했다.

1. `f>0`의 conductor와 filled-background operator 모두 `C0=1`을 먼저 사용한다. DC는 별도 analytic branch다.
2. `C0=1`에서는 `Jν-jYν`를 분리 계산하지 않고 `Hν^(2)` direct/scaled representation으로 계산한다.
3. 선택은 downstream `Z'`나 PowerSI error를 보지 않고 finite/branch, self/quadrature parity, backward residual `<=1e-10`, equilibrated `κ1u<=1e-8`, mesh convergence만 사용한다.
4. A1이 실패하면 `BLOCKED_C0_A1`이다. frozen A0로 자동 복귀하거나 frequency별 `C0`를 tune하지 않는다.
5. high-`C0` 재시도는 별도 `C0-A2` candidate와 새 preregistration, exponent headroom, full gate가 있을 때만 허용한다.
6. policy version, `C0`, branch/self implementation, precision, condition/residual과 source/case hash를 cache key에 보존한다.

이는 Patel–Triverio가 Green-function constant `C0`를 임의 complex number로 두고 `C0=1`을 outgoing Hankel kernel로 정의한 연속식과 일치한다. 논문의 `1e6` switch는 frozen empirical baseline으로 남기되 현재 discretization의 mandatory truth로 취급하지 않는다.

## A1 canonical result

canonical corpus는 `a={17.5,500} µm`, frequency `{100 kHz,1,10,100,500 MHz,1,2 GHz}`, `m=0…4`, `N={128,256,512}`다.

| metric | worst result | gate | 판정 |
|---|---:|---:|---|
| N512 analytic complex error | `0.118541%` | `0.5%` | pass |
| N256→512 max change | `0.158276%` | `1%` | pass |
| log-weight RMS | `0.01418–0.02461%` | `0.5%` | pass |
| phase error | `0.019939°` | `0.25°` | pass |
| quadrature change | `1.0561e-6` relative | `0.1%` | pass |
| per-column normwise-infinity dense backward residual | `1.2030e-15` | `1e-10` | pass |
| exact-circulant equilibrated `κ1u` | `9.7539e-13` | `1e-8` | pass |

root의 finalized scaled-Hankel/small-self serialization checksum은

```text
a2d61666e01884eb8560dc6fb35789be88cbaea9fa52f99d70964fdc20387390
```

이다. A0의 100 kHz failure point에서 A1 N512 max mode error는 `0.018641%`, N256→512 변화는 `0.047429%`, condition proxy는 `8.44e-13`이었다.

## Withheld validation

### Root preregistered withheld W1

A1 결과를 보기 전에 대화에 고정한 W1은 다음 24 geometry/frequency case와 216 modal points다.

```text
radius = {5,50,250,1000} µm
f      = {173 kHz,1.73,17.3,173 MHz,730 MHz,1.73 GHz}
m      = {0,...,8}
N      = {128,256,512}
q      = {10,20}
```

전 case가 통과했다.

| metric | worst |
|---|---:|
| analytic error | `0.111661%` |
| N256→512 change | `0.230599%` |
| phase | `0.028207°` |
| quadrature | `6.800e-6` relative |
| spectral condition proxy `κ2u` | `8.78e-13` |

result checksum `94411e3db852684dfd0554f344490412ffbda6034b9fde9d69b2cb34f167f472`는 original archived run identity다. range-guard replay의 네 full-dense spot은 per-column normwise-infinity residual 최대 `9.5414e-16`, max `κ1u=1.014945e-12`, dense/symbol discrepancy `4.270e-10`이었다. archived batched residual `1.269e-16`과 정규화가 다른 값을 하나의 exact 수치로 섞지 않는다. process-only peak working set은 `94.06 MiB`, private bytes는 `1322.04 MiB`였고 private 수치는 SciPy/BLAS reservation을 포함한다.

### Independent withheld W2

Terra가 별도로 제안한 다음 집합을 A1 formula/threshold를 바꾸지 않고 실행했다.

```text
radius = {12.5,50} µm
f      = {250 kHz,2.5,25,250 MHz,750 MHz,1.5 GHz}
m      = {±5,±7,±11}
N      = {96,192,384}
q      = {8,16}
```

12 geometry/frequency case, 72 signed modal point가 analytic/convergence gate를 통과했다. worst analytic error `0.152520%`, mesh change `0.383233%`, phase `0.012746°`, quadrature `7.00e-9`였고 spectral `κ2u=6.116e-13`은 screening proxy다. 보고된 `m↔−m discrepancy=3.339e-10`은 정규화와 단위가 보존되지 않은 diagnostic이므로 gate evidence로 사용하지 않는다. checksum은 `30a8782fd4e2f627f7549f684f72cc92d6321362fa2a108706036bd3e356f036`다.

W2는 **analytic/convergence-only withheld**다. required equilibrated P/Pout `κ1u`와 full-dense backward residual을 실행하지 않았으므로 A1 full-condition pass의 근거에 포함하지 않는다.

### Sol independent withheld W3

Sol은 `a={25,75,250,750} µm`, `f={0.2,2,20,200,800,1400} MHz`, `m={5,6,7,8}`, `N={128,256,512}`의 96 modal points를 독립 실행했다. 그 original run은 range guard가 없어 큰 원의 silent underflow를 range certificate로 사용할 수 없다. 같은 frozen geometry를 root가 log/floor guard로 replay한 결과 worst analytic error `0.113044%`, mesh change `0.150516%`, phase `0.028220°`; 24 N512 dense case의 per-column residual `1.1541e-15`, max `κ1u=9.164853e-13`, dense/symbol discrepancy `1.470e-11`로 모두 통과했다. original wall `14.46 s`, peak working set `137.83 MiB`는 process-only 참고값으로만 보존한다.

## Hankel range와 fail-closed contract

SciPy convention은

```text
hankel2e(ν,z) = Hν^(2)(z) exp(jz)
Hν^(2)(z)     = hankel2e(ν,z) exp(z.imag) exp(-j z.real)
```

이다. 0.1 Hz–2 GHz, radius 1 µm–1 mm, `m=0…8`의 22,599-point preregistered grid는 `|Im(z)|<=685.99`였다. 두 값이 representable하고 nonzero인 이 safe-overlap grid에서 direct/scaled reconstruction 최대 **relative** 차이는 `6.14e-14`였다. 이와 별도의 extended `z=y(1−j)` scan에서 SciPy/AMOS direct `hankel2`가 이 corpus의 `y≈693.9`부터 수학적으로 nonzero인 값을 exact zero로 반환하기 시작했고, scaled representation은 subnormal 한계 `ln(min_subnormal)≈-744.44`까지 유지됐다. 이는 보편 임계값이 아니라 해당 환경·scan에서 관찰한 조기 zero onset이며 direct-zero 구간은 앞의 overlap metric에 포함하지 않는다.

따라서:

- `log|H2|=log|hankel2e|+Im(z)`를 먼저 검사한다.
- direct/scaled overlap과 Hankel orders `ν=0,1`의 branch를 함께 기록한다.
- `|z|<=1e-3`은 explicit small-argument series/self anchor를 사용한다.
- full weighted kernel contribution이 operator floor 아래임을 증명할 때만 underflow를 zero로 둔다. 그렇지 않으면 `BLOCKED_HANKEL_RANGE`다.
- root의 W1 dense spot inline run에서는 `onenormest`가 near-zero complex sign에서 overflow warning을 냈고, 같은 equilibrated LU를 explicit inverse 없이 LAPACK `gecon`으로 재평가해 통과했다. 이 prior-run 증거와 재현 명령은 [`ORACLE_REPRODUCTION.md`](ORACLE_REPRODUCTION.md)에 함께 보존한다. 일반 sparse M1은 zero-safe estimator가 준비되지 않으면 차단한다.

최종 replay는 `log|H2|<ln(tiny)+4`인 sample을 바로 0으로 만들지 않고, 각 U/P first-row에서 버릴 absolute quadrature contribution의 log-sum bound를 구했다. retained row 1-norm 대비 `1e-30` 이하일 때만 zero를 허용했으며 W1/W3를 포함한 전체 exact block에서 40,448 order-sample contribution이 이 경로를 사용했고 worst bound는 `10^-305.50`이었다. 이 수치는 dropped-row certificate이며 일반 forward-error bound로 과장하지 않는다.

## H4-P0R retry-v4 non-result

Four public H4-P0R-P1 attempts are now immutable pre-claim/pre-factor failures.
The fourth (`9b4854d0...`, exit `2`) failed during control preflight on an exited
same-birth PID that remained in the first complete snapshot. Its token was
semantically spent and retired by `f1aeeac...`; token state is absent. This did
not generate a circle DtN result, factor, RHS, solve, H4 physics, PowerSI
correlation, or 8 GiB conclusion.

Retry-v4 only adds two 25 ms rechecks for that exact exited transition in
explicit outer/control `MaximumAttempts>1` contexts. Python/schema and all
circle gates are unchanged; default-one factor sampling remains fatal. Frozen
bindings are Python `95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234`,
runner `7893cd57fd4b1686addd434fa0103d9f3b0e0087f4783f2c82e459661abfb645`,
tests `18aabcdf7b32c6b013aec65497d31f4d908f3085f53f64cb3791f38a2e79381d`;
focused `11/11`, full no-cache `322/322`. `next_stage_authorized=false`.

## H4-P0R retry-v5 non-result

The fifth public attempt reached the factor child for the first time but did
not create a circle result. PID `55552` was child-visible in `108/108` resource
samples before an active default-one tree sample stopped on
`NONROOT_DISAPPEARANCE_NOT_CONFIRMED`. No factor prefix or certificate exists;
attempted/performed are `null`, completed factors are zero, and RHS/solve/H4
physics are absent. The consumed/retired chain is `46c08d405...` /
`71d3dab...`; current token state is absent.

Outer close `9d0c0aab...` counted `27` confirmed disappearances but stored `16`,
so truncation prevented its seal. Retry-v5 changes only the four active factor
calls to max `3` and shared outer/control event cap to `64`; the zero-byte
stdout schema overlay is deferred. Frozen Python/runner/tests hashes are
`54aa7da9daa013cec41585ae07757e6c54e8e9d15da7f4f16d4f6546b6fb35aa` /
`3888524877f7a90966fb932f7f9fc4c9a48480134eb6295712eaa0ef548416a3` /
`b51498ebe27a0210a09b3a12b26e0146d39c1249906469bcb1add1d2f2443f08`;
focused/full `29/29` and `324/324` passed. Circle gates and next=false do not
change.

## H4-P0R retry-v6 non-result

The sixth public attempt created claim and factor child PID `55380` but did not
create a circle result. Direct child evidence stopped before `_factor_one` or
`splu` on `claimed preflight payload mismatch`, so attempted/performed are
`false/false` and completed/certified factors remain zero. Independent outer
close `d2777207...` exhausted max3 for PID `53404`, retained `30/30` events
without truncation, verified cleanup, and wrote no seal. Resource ceilings were
not approached; no RHS, solve, H4 physics, final circle, or PowerSI work ran.

Emergency record `43bb34b2...`, preserved provisionally by `06061a2...`, is
strict-validator-invalid because nested `bindings.resource_policy_sha256` is
absent; `c001b44...` delete-retires the token. Retry-v6 changes only three
redundant Python lines and that runner binding key. Frozen Python/runner/tests
hashes are `2373a13f...` / `852ce8a0...` / `f1d0b044...`; focused `8/8` passed
and full no-cache `330/330` passed in `81.83 s`. Circle gates and next=false do
not change.

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
## H4-P0R retry-v8 current static candidate

The eighth public P1 invocation ran once from retry-v7 token-only commit
`66211ef...`. Native `splu` returned for `A_background_II`, with
attempted/performed `true/true` and one completed name, but combined
non-canonical L/U storage left zero certificates and prefixes. `A_conductor_II`,
RHS, solve, and physics never started. The resource artifact passed. Independent
outer/control monitoring falsely exhausted across distinct confirmed-dead
helpers, so there is no terminal seal or published result. Seven temporary files
were copied byte-identically into ignored quarantine. Valid provisional emergency
consumption `8fce704...` and D-only retirement `7964464...` leave no token.

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

## Promotion boundary와 다음 단계

현재 승인되는 문장은 “`C0-A1`이 canonical full-condition gate와 W1/W3 dense withheld를 통과했고 W2 analytic/convergence-only withheld가 이를 지지했다”까지다.

이 checkpoint에서 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)를 amendment해 A0 실패를 보존하고 A1을 후속 contour 연구의 primary candidate로 사전 등록했다. 이후 [`T1_M0_SLAB_RESULTS.md`](T1_M0_SLAB_RESULTS.md)에서 M0 lateral-periodic slab과 finite/open contour가 서로 다른 경계값 문제임을 확인했으므로, 현재 A1의 다음 적용 대상은 finite/open M1이다. 이는 M1 통과가 아니며 A1 실패 시 A0 fallback이나 사후 tuning을 허용하지 않는다.

다음 단계는 순서대로:

1. M0 periodic slab의 independent 1-D volume-only pass를 유지한다.
2. corner가 있는 finite/open M1의 `N,2N,4N`과 underflow/operator-floor certificate를 실행한다.
3. 같은 conductor/group/current basis의 A–v reference를 실행한다.
4. exterior `Z'_partial`, equipotential return reduction과 power/passivity gate를 연결한다.
5. noncircular blind fixture와 A–v가 통과하기 전 제품 또는 PowerSI correlation으로 승격하지 않는다.

## Primary sources

- U. R. Patel and P. Triverio, “Skin Effect Modeling in Conductors of Arbitrary Shape Through a Surface Admittance Operator and the Contour Integral Method,” [author preprint](https://arxiv.org/html/1509.08357), [IEEE T-MTT DOI](https://doi.org/10.1109/TMTT.2016.2593721).
- SciPy, [`scipy.special.hankel2e`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.hankel2e.html).
