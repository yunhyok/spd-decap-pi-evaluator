# SPD Decap PI Evaluator v0.22.0 — T1 Trace Oracle Results

> **SPD Decap PI Evaluator v0.22.0 — retry-v10 eleventh-result final
> documentation freeze:** Eleven public `primary-h4-p0r` invocations and token
> lineages are immutable. The eleventh ran exactly once from token-only commit
> `e35ef01214f4bf9ec75e7b428e72b21d38c9161b`, token ID
> `2435fa59edc64efca2fb4665b0854d43`, and exited public stage `0` with
> empty stderr; it was not rerun. Factor producer PID `63268`, finalizer PID
> `39580`, and consumer PID `60012` exited `0`. Exactly two certificates
> and two prefixes were retained in order `A_background_II`,
> `A_conductor_II`; both factor-cap gates and both resource gates passed.
> Strict retained classifications are `consumed_v2_authoritative_sealed_pass`
> and `consumed_v2_authoritative_sealed_pass_no_next_stage_authorization`,
> with `authoritative_terminal_evidence=true`,
> `authoritative_stage_pass=true`, authoritative inner disposition
> `authoritative_inner_pass_observed`, `validation_error=null`, and
> `next_stage_authorized=false`. M-only consumption
> `bf94e1890c489e690801b3d90bcbf50ff61ca233` followed by D-only retirement
> `389ec1e51b87f3eff928278c76bbbf60241af972`; the token is absent.
>
> Inner monitoring retained `243/243` samples in `34.0036179 s`, peak
> working set `510849024` bytes, private bytes `1954021376`, and lifetime
> commit `2358079488`. Outer monitoring retained `677/677` samples and
> `47` nontruncated retries in `94.5411269 s`, peak working set
> `513384448`, private bytes `1958121472`, and lifetime commit
> `2358079488`. All `164/164` outer identities and the broader `207/207`
> identity set were absent after cleanup. Minimum host available physical memory
> was `43885748224` bytes, so this is an authoritative factor-only pass on
> this host, not an 8 GB fit proof. Both certificates have `rhs_count=0`,
> `factor_solve_called=false`, and all forbidden-operation flags false. No
> RHS, solve, extension, boundary `Y`, modal response, H4 physics, or PowerSI
> comparison ran.
>
> Frozen Python/runner/tests SHA-256 remain `46082123fbf98102f3e5995c32bf65d8d04bb835b9c1305de0150349e75e48c7`,
> `f0159061dbd4d1b34881911edfdfb72146a3a23e5cdc75ca8fa4069c08aadb86`,
> and `e5496ceb24c33c835b88ce20a3f67f941c8279e0471708a01022238fc211b109`;
> collection remains `400`. Evidence anchors are numerical raw SHA-256
> `0af448eeea2eb302f6aeebbb04fd01992067ccc55a017eb780507aa34924728f`,
> resource raw SHA-256
> `d6358608dd97c0f245bb781d0bfc63e929a1a74733907a7fa2a1fbdb9dca97de`,
> and terminal-seal raw SHA-256
> `070c86e25aeb982d5ef1c9c99c9c15d9ed3a6d08ce4e521e55262e4586a516a4`.
> The first full exact-document suite on the post-eleventh document bytes
> passed `400/400` in `95.65 s`, exit `0`, with no failure. This is the
> **FINAL DOC FREEZE**. Do not record any later closure runtime. Any H4-P1,
> RHS, or solve work still requires a separate preregistration and
> authorization; this result creates neither a token nor further authority.
>
> **Historical SPD Decap PI Evaluator v0.22.0 — retry-v10 tenth-result final documentation
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
> Every pre-existing retry-v10 tenth-result, future-eleventh, `400/400`,
> retry-v9, ninth-attempt, future-tenth, `397/397`, or **FINAL DOC FREEZE**
> statement later in this file is an immutable historical
> snapshot. Only the retry-v10 eleventh-result block at the top states current authority.


최종 갱신: 2026-08-16 (Asia/Seoul)

이 문서는 직선 finite trace와 명시적 return의 broadband series/distributed physics를 검증하는 T1 연구 결과를 고정한다. 모든 계산은 research-only inline probe이며 제품 Evaluation 경로는 변경하지 않았다.

## 판정 요약

| subcase | 판정 | 허용되는 주장 | 남은 차단 조건 |
|---|---|---|---|
| T1-E0 Cohn stripline | `passed_canonical_lossless_only` | zero-thickness, homogeneous, lossless centered stripline의 `C'` | finite thickness, conductor/dielectric loss, real return polygon |
| T1-M0 periodic plate pair | `passed_periodic_1d_volume_only` | exact identity와 independent normalized FEM의 periodic `m=0` smooth-copper `R(f), L(f)` | finite-width lateral edge/proximity, free-space exterior, C0-A1 periodic SAO |
| T1-I0 circle interior DtN | `C0-A1 passed_circle_interior_only` | exact Bessel DtN, pulse mesh/self/quadrature, canonical + W1/W3 dense; W2 analytic-only | M1 exterior, corner, independent A–v, full `Z'` |
| T1-M1 finite-width return | `BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY__G2_PAIR_PASSED_CIRCLE_100KHZ_RECIPROCITY_CANCELLATION_FAIL` | immutable collocation record, G1 exterior-only, G2 pair q/symmetry, G2 circle q/analytic/mesh/passivity, A–v 2 GHz smoke, `AV-BS1-CIRCLE` H1 coarse h와 H2 refined h2 stage-only passes, H4-P0/H4-P0R parent와 P1 executable static review | 100 kHz G2 N256 reciprocity/cancellation fail; G1 passivity fail 미해결; AV-BS1 H4 P1 아홉 attempt/token 폐기, 아홉 번째 producer PID `51184`에서 two-factor certificates/prefixes와 resource gates pass지만 published finalizer PID mismatch 및 strict-invalid raw seal로 non-authoritative; retry-v9 provisional candidate, first full exact-document suite `397/397` in `92.95 s`, exit `0`, no failure, FINAL DOC FREEZE; factor fit/physics/fine analytic/final circle/withheld 미실행; planned G2 2 GHz/N512/EQ0 미실행 |
| T1-F finite-length | `not_run` | 없음 | 3-D PEEC/FastHenry length-difference de-embedding |
| T1 source candidates | `geometry_and_net_graph_evidence_ready` | width, endpoint, layer, selected stack/material, selected P1/P2 return artwork/void와 same-net graph | terminal-to-return signed current/field owner와 same-crop core partition |
| T1 global composition | `blocked_balanced_projection_and_return_partition` | reduced differential operator를 곧바로 stamp할 수 없다는 것 | absolute partial operator 또는 explicit local current constraint, same-crop return/core partition |
| **T1 전체** | **`blocked`** | E0, M0 periodic volume과 circle interior만 제한 통과 | M1/F/source-faithful/global gate 전부 통과 필요 |

`passed_canonical_*`은 PowerSI 상관성, 제품 정확성 또는 8 GB production 성능 승격이 아니다.

## Source-derived candidate와 return 증거 등급

`UpperRef`/`LowerRef`가 Trace record에 있어도 해당 polygon의 연결성과 return-current operator까지 증명되지는 않는다. 다음 세 등급을 구분한다.

1. `trace_record_explicit_connectivity_unproved`: Trace record가 return 이름을 명시하지만 polygon/connectivity/current ownership은 미확정
2. `derived_stackup_only`: 인접 stackup으로 return 후보를 찾았지만 Trace record에는 ref가 없음
3. `absent`: width 또는 필요한 physical field가 source에 없음

| pair/case | raw trace evidence | geometry/material evidence | return evidence | 판정 |
|---|---|---|---|---|
| P1 TOP | `Trace4004/4005/4006`, lines 725146–725154; width 914.4/600/500 µm; length 889/800/640 µm | TOP 35 µm `COPPER_1`; 203.2 µm FR-4; `Plane$IN43_DGND` 30.48 µm | record `LowerRef=Plane$IN43_DGND` | `trace_record_explicit_connectivity_unproved` |
| P2 TOP | `Trace9054/9055/9056`, lines 3577641–3577652; width 600 µm; length 900 µm | TOP 35 µm copper; 208 µm MEGTRON-6; `Signal$IN01_GND` 17.5 µm | record `LowerRef=Signal$IN01_GND` | `trace_record_explicit_connectivity_unproved` |
| P2 inner manufactured | `Trace13305`, lines 3553874–3553875; `Node30557→30558`; width 120 µm; length 4.2 mm; IN24 | conductor 17.5 µm; upper/lower media 75/104 µm; `εr=3.49/3.31`, `tanδ=.002` at 1 GHz only; copper `σ20C=59.6 MS/m` | Trace ref 없음; crop에서 IN23/IN25 positive GND artwork와 GND-via graph 확인, signed operator는 미증명 | `return_shape_geometry_present` / `return_net_graph_present`; manufactured coupon only, source-faithful blocked |
| P3 | `Trace109521/22/23`; width 25 µm; length 171.1/160/130 µm; L12 | 20 µm copper; 30 µm ABF-GL102 toward L13 DGND | no Trace ref | `derived_stackup_only`; source-faithful T1 blocked |
| P4 | `Trace109520/21/22`; width 25 µm; length 180/130/130 µm; L11 | 20 µm copper; 30 µm ABF-GL102 toward L10 DGND | no Trace ref | `derived_stackup_only`; source-faithful T1 blocked |

P3/P4에는 width가 source에 없는 Trace도 각각 239,135/239,070개 있다. 이 행들은 인접 record나 PowerSI curve로 채우지 않는다. P1/P2의 selected explicit-ref crop은 실제 GND artwork와 nearby GND Trace/Via-to-plane graph까지 확인했지만, signal Trace/pad를 return owners에 묶는 raw current/field record가 없다. P2 `Trace9054/55/56`의 free endpoint는 IN01 GND negative-circle void 중심과 일치한다. 따라서 net graph를 electromagnetic return operator로 승격하지 않으며 세부 owner는 [`T1_RETURN_CROP_MANIFEST.md`](T1_RETURN_CROP_MANIFEST.md)를 따른다.

### 전체 Trace streaming 분류

strict screening은 `width explicit + 두 endpoint가 같은 layer + concrete UpperRef 또는 LowerRef가 하나 이상`으로 정의했다. `N/A`와 field absent는 concrete ref가 아니다. 이 screening은 계산 후보를 고르는 metadata gate일 뿐 return connectivity 통과가 아니다.

| pair | total Trace | width explicit | same-layer endpoints | concrete-ref screening | blocked at screening | unique metadata profiles |
|---|---:|---:|---:|---:|---:|---:|
| P1 | 12,544 | 12,544 | 12,544 | 6,816 | 5,728 | 8 |
| P2 | 15,052 | 15,052 | 15,052 | 2,976 | 12,076 | 57 |
| P3 | 1,451,285 | 1,212,150 | 1,451,285 | 0 | 1,451,285 | 97 |
| P4 | 1,451,209 | 1,212,139 | 1,451,209 | 0 | 1,451,209 | 88 |

P1의 concrete-ref 6,816개는 TOP→IN43 5,600개와 BOTTOM→IN64 1,216개다. P2의 largest eligible profile은 TOP 600 µm→IN01 GND 2,827개다. source record에서 upper/lower ref가 둘 다 concrete인 경우는 네 pair 모두 0이지만, 한 개의 explicit return을 갖는 microstrip을 이유 없이 차단한다는 뜻은 아니다. 실제 polygon과 return path를 별도로 증명한다.

P2 length 분포는 ≥2 mm 7,154개, 1–2 mm 1,651개, 0.5–1 mm 4,554개다. 따라서 전체를 short lumped trace로 가정할 수 없다. P3/P4는 약 1.45M개의 Trace 중 각각 약 39%가 DGND 등 conductor-layer token과 net 이름이 일치하고, raw grammar에는 routed trace와 plane/mesh topology를 구분하는 semantic flag가 없다. 이 자료를 전부 physical rectangular route로 해석하지 않고 `mixed_or_undetermined`로 차단한다.

profile 수가 record 수보다 매우 작다는 점은 SAO cross-section cache의 잠재적 이점이다. 다만 profile key는 layer/width/ref/thickness/material link뿐 아니라 actual return contour/crop, nearby conductors, terminal semantics와 source hash를 포함해야 한다. P1/P2의 material conductivity는 stack layer의 material 이름과 material model을 통해 연결하며, layer row 자체에 숫자 conductivity가 없다는 사실을 보존한다.

## 알고리즘 선택

### Normative 2-D broadband oracle: SAO–CIM

finite thickness, skin effect, proximity, edge current crowding과 arbitrary rectangular/trapezoidal return을 동시에 다루는 기준 후보는 **surface-admittance operator + contour-integral method (SAO–CIM)** 다.

- conductor interior는 longitudinal electric field와 tangential magnetic field를 연결하는 Dirichlet-to-Neumann surface operator로 치환한다.
- exterior field와 모든 signal/return contour를 함께 풀어 partial per-unit-length complex impedance matrix를 얻는다.
- skin depth 방향의 volume mesh 대신 conductor perimeter만 이산화하므로 2 GHz의 약 1.46 µm copper skin depth에서도 bounded local coupon에 유리하다.
- naive local dense work는 panel 수 `p`에 대해 O(p²) storage/O(p³) factor이지만, T1은 작은 cross-section과 반복 profile cache를 대상으로 한다. 실제 host wall/RSS는 prototype에서 별도 측정한다.

Patel–Triverio의 arbitrary-shape formulation은 rectangular/trapezoidal/multiple-return 예제를 FEM과 비교하고 published host에서 frequency당 약 0.04–0.52 s를 보고했다. 이 문헌 timing은 현재 노트북 성능 증거가 아니다.

독립 reference candidate는 skin-depth graded 2-D volume-current A–v FEM이다. H1/H2 stage-only gates와 H4 parents를 보존한다. 아홉 번째 P1은 producer PID `51184`에서 two-factor certificates/prefixes와 resource gates를 통과했지만 published finalizer PID mismatch와 strict-invalid raw seal 때문에 authoritative factor fit/fine analytic/convergence 전에는 oracle이 아니다. retry-v9 no-token provisional contract의 first full exact-document suite는 `397/397` in `92.95 s`, exit `0`, no failure로 통과했다. Exact-byte/audit와 별도 승인/authoritative result audit하기 전에는 H4 physics를 열지 않는다. finite end/bend/launch 3-D PEEC와 SAO 후보는 별도 reference로 유지한다.

### 현행 helper의 제한

`mfdm.copper_surface_impedance`와 two-face variant는 1-D finite-thickness slab의 `coth/csch` skin law와 DC limit를 정확히 제공한다. coextensive plate limit에는 유용하지만 finite trace width의 lateral edge crowding, arbitrary return contour와 proximity를 풀지 않으므로 T1 exact operator로 승격하지 않는다.

현 production topology는 finite Trace를 ideal union으로 취급하고 `finite_trace_rl_link_count=0`, `source_trace_width_available=False`다. dormant `source_trace.py`는 exact rectangle geometry와 source identity만 제공하며 전기 `R/L/G/C` 소비자가 아니다.

## T1-E0: body-fitted Cohn stripline

ground plane은 `y=±h`, zero-thickness centered strip은 `y=0`, `|x|≤w/2`에 둔다. vacuum capacitance의 Cohn exact 식은 다음과 같다.

\[
k=\tanh\left(\frac{\pi w}{4h}\right),\qquad
C'_0=4\epsilon_0\frac{K(k^2)}{K(1-k^2)}
\]

SciPy `ellipk`의 인수는 modulus가 아니라 parameter `m`이다. body-fitted finite-volume grid는 `x=±w/2`를 exact node로 포함하고 lateral padding을 strip edge부터 잰다. top/bottom ground는 0 V, strip은 1 V, lateral boundary는 homogeneous Neumann이다. 각 orthogonal edge conductance는 `g=ε·dual_width/distance`, capacitance는 `C'=Σg(ΔV)²`로 계산한다.

독립 재현 조건은 `h=100 µm`, edge padding `8h`, mesh `h/64`, `h/128`이다.

| width | Cohn `C'0` | h/64 | h/128 | `2C128−C64` | Richardson relative error |
|---:|---:|---:|---:|---:|---:|
| 120 µm | 36.8128510 pF/m | 37.0112662 | 36.9119306 | 36.8125951 | −6.951e-6 |
| 500 µm | 104.1702700 pF/m | 104.3664877 | 104.2682374 | 104.1699871 | −2.716e-6 |
| 914.4 µm | 177.5537791 pF/m | 177.7499747 | 177.6517423 | 177.5535098 | −1.516e-6 |

모든 폭에서 `C64>C128>Cexact`이고, h/64→h/128 변화는 0.2684/0.0941/0.0553%다. raw h/128 exact error의 최대는 `0.2691442%`, 즉 보수적으로 `≤0.2692%`이고, first-order Richardson exact error의 최대는 0.00070%다. 별도의 h/128 crop sweep에서 측정한 padding 4h→8h 변화는 최대 `2.12459e-6` relative로 mesh error보다 작았다.

검증 invariant:

- sparse matrix symmetry: exact zero
- energy와 independent strip charge relative mismatch: 최대 1.50e-12
- induced Maxwell matrix `[[C,-C],[-C,C]]`: reciprocity와 row sum은 construction상 exact
- exact appendix 재실행의 one-process peak working set: 약 1,856 MiB; 앞선 독립 run은 약 1,852 MiB
- 별도 per-grid timing run의 h/64/h128 wall: 폭에 따라 1.23–1.85 s / 5.88–8.42 s

RSS와 timing은 host-dependent reference다. 결과는 homogeneous lossless zero-thickness 2-D cross-section `C'`만 검증한다. finite copper 또는 product field solver를 검증하지 않는다.

## T1-M0: periodic two-plate smooth-copper identity

제조해 geometry는 length `l=10 mm`, signal/return width `w=5 mm`, 각 copper thickness `t=35 µm`, facing-surface gap `h=50 µm`, `σ=59.6 MS/m`, lateral periodic symmetry다. 두 conductor terminal current는 `+I/-I`다.

\[
\gamma_c=\sqrt{j\omega\mu\sigma},\qquad
Z_s=\sqrt{\frac{j\omega\mu}{\sigma}}\coth(\gamma_c t)
\]

\[
Z'_{loop}(\omega)=\frac{2Z_s}{w}+j\omega\mu\frac{h}{w},qquad
Z_{loop}=lZ'_{loop}
\]

정확한 limit:

\[
R_{dc}=\frac{2l}{\sigma wt},\quad
L_{dc}=\frac{\mu l}{w}\left(h+\frac{2t}{3}\right),\quad
L_{HF}=\frac{\mu lh}{w},\quad
R_{HF}\sim\frac{2l}{w}\sqrt{\frac{\pi f\mu}{\sigma}}
\]

| frequency | R | `Im(Z)/ω` |
|---:|---:|---:|
| DC | 1.917546 mΩ | limit 0.184307 nH |
| 100 kHz | 1.917687 mΩ | 0.184306 nH |
| 1 MHz | 1.931661 mΩ | 0.184183 nH |
| 10 MHz | 2.999005 mΩ | 0.175031 nH |
| 100 MHz | 10.294225 mΩ | 0.142049 nH |
| 500 MHz | 23.019810 mΩ | 0.132991 nH |
| 1 GHz | 32.554927 mΩ | 0.130845 nH |
| 2 GHz | 46.039620 mΩ | 0.129327 nH |

100 MHz→2 GHz의 successive log slope `d ln R/d ln f`는 0.500033에서 0.500000으로 접근한다. 이는 periodic 1-D identity이며 finite-width M1의 width-inverse 법칙이나 corner loss를 승인하지 않는다.

제품 helper를 import하지 않는 normalized 1-D linear FEM으로 이 identity를 독립 재현했다. canonical 12 frequencies, uniform `N={64,128,256}`의 fine raw `Zs` max error/mesh/phase는 `0.073301%/0.219901%/0.041998°`, log-weight RMS error/mesh는 `0.017973%/0.053917%`였다. backward residual `2.220e-16`, equilibrated `κ1u=6.336e-10`, current residual `1.005e-11`, dissipative-power residual `7.574e-15`도 통과했다. 두께·전도도·주파수를 바꾼 W0 withheld 12 cases의 worst error/mesh/phase는 `0.126811%/0.380419%/0.072657°`다. 상세 식과 resource는 [`T1_M0_SLAB_RESULTS.md`](T1_M0_SLAB_RESULTS.md)를 따른다.

M0는 lateral-periodic seam을 가진 slab이고 physical side face가 없다. finite rectangle + unbounded `H2` contour에는 side current와 edge magnetic energy가 있으므로 periodic `coth`를 exact target으로 직접 사용할 수 없다. C0-A1 periodized SAO는 별도 periodic Green/lattice-sum kernel이 없어 `blocked_periodic_green_not_implemented`다. 이 M0-only kernel은 finite/open M1 위험을 거의 줄이지 않으므로 우선 구현하지 않고, C0-A1은 circle-only 상태로 M1에서 검증한다.

## Finite length와 distributed ownership

SAO–CIM이 `z'(f)=R'(f)+jωL'(f)`를 주더라도 2 GHz에서 trace를 항상 한 개의 lumped series branch로 바꿀 수는 없다. source-derived `y'(f)=G'(f)+jωC'(f)`와 같은 return/reference를 쓸 때 uniform scalar line은 다음 exact two-end differential operator를 갖는다.

\[
\gamma=\sqrt{z'y'},\qquad Y_0=\sqrt{y'/z'}
\]

\[
Y_{line}=Y_0
\begin{bmatrix}
\coth(\gamma l)&-\operatorname{csch}(\gamma l)\\
-\operatorname{csch}(\gamma l)&\coth(\gamma l)
\end{bmatrix}
\]

small `|γl|`에서는 common/differential eigenvalue `Y0·tanh(γl/2)`와 `Y0·coth(γl/2)` 또는 series expansion을 사용해 cancellation을 피한다. multiconductor case는 full coupled state transition/Schur operator를 사용한다.

scalar 구현은 다음 exact-π decomposition이 DC와 small `x=γl`에서 더 직접적이다.

\[
y_s=\frac{1}{z'l}\frac{x}{\sinh x},\qquad
y_p=y'l\frac{\tanh(x/2)}{x},\qquad
Y_{line}=\begin{bmatrix}y_s+y_p&-y_s\\-y_s&y_s+y_p\end{bmatrix}
\]

`|x|<1e-3`에서는 `x/sinh(x)=1−x²/6+7x⁴/360−…`, `tanh(x/2)/x=1/2−x²/24+x⁴/240−…`를 쓴다. DC의 `y'(0)=0`은 `ys=1/(R'dc·l)`, `yp=0`으로 별도 처리한다.

일반 multiconductor `Z',Y'`는 commuting 또는 eigenvector continuity를 가정하지 않는다. `H=[[0,−Z'],[−Y',0]]`, `T=exp(lH)=[[A,B],[C,D]]`를 scaling-and-squaring Padé로 구한다. `T`가 `[v0;i0]→[vl;il]`를 매핑하고 두 terminal plane의 port current를 모두 line 안쪽으로 들어오는 방향으로 정의하면 terminal block은 다음과 같다.

\[
Y_{00}=-B^{-1}A,\quad Y_{01}=B^{-1},\quad
Y_{10}=-C+DB^{-1}A,\quad Y_{11}=-DB^{-1}
\]

`B^{-1}`를 materialize하지 않고 모두 solve로 계산한다. `B` solve의 residual/condition과 최종 passivity를 보고하며 `sinh` pole 부근을 clipping하지 않는다. coupled-line screen의 `γk`는 passive branch를 택한 `eigenvalue(Z'Y')`의 square root로 정의한다. 다만 nonnormal coupled system에서는 이 eigenvalue screen은 necessary condition일 뿐이며 full-matrix exact-versus-lumped response gate가 최종 판정이다.

M0의 homogeneous `εr=4` 예에서 2 GHz electrical length와 lossless terminal-admittance coefficient error는 다음과 같다. exact normalized diagonal/off-diagonal은 `θ cot θ`, `θ csc θ`이고, 표는 exact coefficient를 분모로 한 최대 상대오차다.

| physical length | `|βl|` at 2 GHz | angle | nominal-π max error | series-only max error |
|---:|---:|---:|---:|---:|
| 0.889 mm | 0.07453 | 4.27° | 0.0927% | 0.1856% |
| 0.900 mm | 0.07545 | 4.32° | 0.0950% | 0.1902% |
| 4.2 mm | 0.35210 | 20.17° | 2.120% | 4.348% |
| 10 mm | 0.83834 | 48.03° | 13.975% | 32.633% |

따라서 exact line stamp를 기본으로 한다. nominal-π는 mandatory band 전체에서 `max modal |γl|≤0.1`이고 exact matrix 비교가 0.5%/1% gate를 통과할 때만 허용한다. exact terminal stamp가 아직 없을 때의 fallback section 수는 `ceil(θmax/0.1)` 이상이며 4.2/10 mm 예는 4/9 section이다. series-only R/L은 동일 trace의 source-proven shunt `C/G`가 core에 남고 topology/owner가 정확히 대응한다는 증거가 추가로 필요하다. core가 shunt를 소유하지 않으면 short trace에서도 series-only가 아니다.

P2 dielectric은 각 material에 1 GHz 한 점만 있으므로 causal broadband `G'/C'` law를 만들 수 없다. 그 한 점은 1 GHz sensitivity 또는 lossless-static screening에만 사용하며 0–2 GHz dispersive law로 외삽하지 않는다.

### Bend, profile change와 branch

SAO p.u.l template은 translationally invariant straight interior에만 적용한다. bend, width/thickness/trapezoid/return/material change에서 segment를 분리하고, bend/step/taper에는 별도의 finite 3-D local block과 de-embedding plane을 둔다. 근거가 없으면 zero-length ideal join으로 숨기지 않고 `blocked_nonuniform_trace`로 남긴다.

T junction은 explicit junction terminal을 보존한다. 세 line endpoint를 단순히 같은 node에 붙이면 junction crowding/fringe가 빠지므로 3+-port local block 또는 사전 등록한 negligible-correction proof가 필요하다. pad/via/decap, measurement/mutable terminal, crop `Γ`, degree≠2, return change, source anomaly를 가로질러 merge하지 않는다.

### Laptop-oriented cache/merge architecture

정확성 gate 뒤의 우선 구조는 다음과 같다.

1. canonical cross-section key별 causal/passive `(Z',Y')`를 cache하고, length는 runtime exact data로 유지한다.
2. 안전하게 압축된 straight chain마다 scalar exact line 또는 작은 multiconductor `expm` block을 평가한다.
3. exact terminal adapter가 준비되지 않은 초기 단계만 `θsection≤0.1` passive π ladder를 fallback으로 사용한다.
4. frequency-domain exact evaluation을 먼저 측정하고, rational shared-template ROM은 dense withheld-frequency positive-real 검증 뒤에만 추가한다.

cache key는 solver/formula version, ordered conductor/return polygon과 role/reference basis, thickness/width/gap, material table와 temperature owner, roughness status, `Γ`/DtN, mesh certificate, frequency policy와 source hashes를 포함한다. length를 반올림해 cache hit를 만들지 않는다.

동일 profile의 collinear degree-2 chain은 template/basis/orientation/return/owner 상태가 모두 같을 때만 합친다. `T(l1+l2)=T(l2)T(l1)` semigroup parity를 machine-precision gate로 두고 ordered source Trace owners와 exact summed length를 보존한다. P1/P2의 8/57개 metadata profile은 cache 가능성을 보여주지만, return polygon을 포함한 최종 key cardinality는 아직 측정하지 않았다. P3/P4는 Trace semantics와 ref가 미확정이므로 merge 후보 수를 아직 선언하지 않는다.

## Global MNA composition failure와 요구 계약

reduced differential two-port `Y2`를 네 absolute terminal `(S0,R0,S1,R1)`로 lift하는 incidence를 `D=[[1,−1,0,0],[0,0,1,−1]]`라 하면 `Y4=DᵀY2D`다. frozen 1 GHz probe처럼 `Y2`가 full rank이면 `Y4`는 rank 2이고 다음 두 null vector를 가진다.

```text
[1, 1, 1, 1]       global gauge
[1, 1, -1, -1]      independent terminal-plane common mode
```

research probe에서 이 `Y4`를 현 `NodalAdmittanceBlock`으로 compile하는 단계는 통과했지만 solve는 정확히 다음으로 차단됐다.

```text
GlobalMnaError: saddle system is singular; topology has an unresolved island
```

이는 tolerance 문제가 아니다. 현 global MNA는 structural component마다 한 absolute gauge를 기대하며 reduced differential line의 추가 current constraint/common-mode semantics를 알지 못한다.

DC에서 `y'(0)=0`인 pure series line은 `Y2`와 `Y4`가 rank 1이며 `[1,−1,1,−1]`도 추가 null이 된다. 따라서 rank 2는 broadband 보편 명제가 아니라 frozen finite-frequency case의 판정이고, 어느 경우든 단순 absolute lift의 singularity 결론은 변하지 않는다.

허용되는 향후 경로는 둘뿐이다.

1. outer/reference field를 포함한 full partial conductor operator를 만들어 absolute nodal/series block으로 조립한다.
2. differential current constraint와 terminal projection을 물리적으로 명시하는 balanced-projection adapter를 만들고 global core의 common mode/return operator와 함께 증명한다.

board crop에서는 signal copper, return copper, magnetic/electric field, terminal footprint, cross-boundary mutual term을 exact/core가 동일 `Γ`, terminal order, signed current, gauge와 DtN trace space로 공유해야 한다. ideal Trace union을 먼저 제거하고 topology를 교체한다. trace prism이 retained Polygon/plane asset과 disjoint임을 source hash로 증명하지 못하면 기존 adjacent-gap C를 spatial outside/inside owner로 분할하고 inside core를 제거해야 한다. aggregate core partial을 분할할 수 없으면 line C를 병렬 추가하지 않고 차단한다. 일반적으로 indefinite인 raw `ΔY=Yexact−Ycore`를 현 global MNA의 독립 passive block으로 stamp하지 않는다. retained core와 correction을 먼저 합친 하나의 passive absolute replacement operator를 검증한다.

## Retry-v4 historical non-result record

네 번째 public P1 invocation은 token-only commit `9b4854d0cc7ae21e5e9eafc394a9474cb53ea689` (clean contract parent `4d39eab9c464f67e8684e2d539ac3a2b092142a2`)에서 `2026-08-15T13:37:16.0468244Z`부터 `2026-08-15T13:37:20.5004777Z`까지 실행되고 exit 2로 끝났다. token ID `4c209c8a4dac49b89c59dd69bf68c4c2`, raw SHA-256 `d3bb3a42c83848678f0b99c0c669fffb5ab02e594cfba31ee9251c8216d05d64`는 semantic하게 spent됐으며 deletion-only retirement `f1aeeac018a96cbd82341db36efbf5e5a9a55431` 뒤 현재 token은 없다.

outer close `validation-output/av-bs1/outer-observer/session-2085628c53894c8eade7a735ec60f36c/outer-resource-envelope-close.json`의 SHA-256은 `73073432faa0525f560b6536ea2a5fd54329ba3a30f04ab05ab8cef59460e758`이다. 정확한 root cause는 attempt 1 PID 40224에 대한 same-birth metric `exited` 뒤에도 initial complete snapshot에 PID가 남은 상황을 default retry가 재검사하지 않고 `NONROOT_DISAPPEARANCE_NOT_CONFIRMED`로 fatal 처리한 것이다. cleanup은 검증됐고 현재 live matching process/temp directory는 없다. control target-complete/report/close/index, claim, factor child/prefix, RHS, solve, H4 physics, numerical result, tombstone, quarantine, seal은 모두 없으므로 이는 T1 trace physics 또는 8 GB/PowerSI 증거가 아니다.

retry-v4는 explicit `MaximumAttempts>1` outer/control에서만 same-birth positive `exited` + initial complete snapshot present를 최대 25 ms 간격 두 번, complete snapshot 총 3개까지 확인한다. absent면 기존 confirmation event와 whole-sample retry, 계속 present면 기존 fatal이다. `not_found`/87 + present, default-one factor sample, live/reuse/root/query/access/malformed/incomplete cases는 모두 fatal이며 schema/Python/physics는 그대로다. frozen static binding은 runner `7893cd57fd4b1686addd434fa0103d9f3b0e0087f4783f2c82e459661abfb645`, Python `95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234`, tests `18aabcdf7b32c6b013aec65497d31f4d908f3085f53f64cb3791f38a2e79381d`; focused 11/11, full no-cache P1 322/322 in 82.01 s, PowerShell AST 47,623/0, Python AST clean이다.

## Frozen retry-v5 non-result record

The fifth P1 invocation (`5ba4b693...`) is the first to create the claim and
reach factor child PID `55552`, but it is not a trace oracle result. After
`108/108` child-visible samples, an active factor tree sample using default
maximum `1` stopped on `NONROOT_DISAPPEARANCE_NOT_CONFIRMED`. No prefix,
certificate, RHS, solve, H4 physics, trace response, or PowerSI correlation was
created; factor attempted/performed remain `null` and completed count is zero.

Outer close `9d0c0aab...` counted/stored `27/16`, marked truncation, and wrote
no seal. Consumed/retired commits `46c08d405...`/`71d3dab...` leave token absent.
Retry-v5 changes only active-four max3 and shared outer/control cap64. The
zero-byte stdout schema overlay is deferred. Frozen runner/Python/tests are
`3888524877f7a90966fb932f7f9fc4c9a48480134eb6295712eaa0ef548416a3` /
`54aa7da9daa013cec41585ae07757e6c54e8e9d15da7f4f16d4f6546b6fb35aa` /
`b51498ebe27a0210a09b3a12b26e0146d39c1249906469bcb1add1d2f2443f08`;
focused/full tests passed `29/29`/`324/324`. Trace gates and next false remain.

## Historical retry-v6 non-result record

The sixth P1 invocation (`82775327...`) created claim and factor child PID
`55380`, but direct child evidence stopped before `_factor_one`/`splu` on
`claimed preflight payload mismatch`. Attempted/performed are `false/false`;
completed factors, prefixes, certificates, RHS, solve, H4 physics, trace
response, and PowerSI correlation are absent. Independent outer close
`d2777207...` exhausted max3 for PID `53404`, retained `30/30` events without
truncation, verified cleanup, and wrote no seal. Resource ceilings were not the
cause.

Emergency record `43bb34b2...` is honest but strict-validator-invalid because
nested `bindings.resource_policy_sha256` is absent. Commits `06061a2...` and
`c001b44...` preserve then delete-retire the token. Retry-v6 removes only the
redundant three-line Python comparison and adds the missing runner binding key.
Frozen runner/Python/tests hashes are `852ce8a0...` / `2373a13f...` /
`f1d0b044...`; focused `8/8` passed and full no-cache `330/330` passed in
`81.83 s`. Trace gates and next false remain.

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

The eighth public P1 invocation ran once from retry-v7 token-only commit
`66211ef...`. Native `splu` returned for `A_background_II`, with
attempted/performed `true/true` and one completed name. Combined non-canonical
L/U storage left zero certificates and prefixes; `A_conductor_II`, RHS, solve,
and physics never started. The resource artifact passed. Independent
outer/control monitoring falsely exhausted across distinct confirmed-dead
helpers, leaving no terminal seal or published result. Seven temporary evidence
files were copied byte-identically into ignored quarantine. Valid provisional
consumption `8fce704...`, then D-only retirement `7964464...`, leave no token.

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

## 다음 실행 순서

1. M0 periodic 1-D volume pass를 독립 slab anchor로 동결한다. finite/open contour를 periodic `coth`와 직접 비교하지 않는다.
2. M1-EQ0/G1/G2 failures, AV-BS1 H0/H1/H2 artifacts, H4 parents와 아홉 P1 interruption/retirement를 보존한다. 현재 token absent/factor fit unproven이며 retry-v9 no-token provisional contract의 first full exact-document suite `397/397` in `92.95 s`, exit `0`, no failure와 FINAL DOC FREEZE를 보존한다. Exact-byte/audit와 별도 승인/authoritative result audit 전에는 H4 physics를 실행하지 않는다. 새 승인 run은 열 번째다. circle 두 radius 통과 전 outer crop `{2,4,8}Deff` EQ0를 실행하지 않는다.
3. perimeter panel `N,2N,4N`, singular self integral, corner/opposing-projection grading과 volume skin mesh `δ/2,δ/4,δ/8`에서 raw `Z'`, loss, reciprocity, passivity, current conservation을 0.5%/1% gate로 검사한다.
4. P2 `Trace13305`는 source-derived manufactured asymmetric stripline으로만 실행한다. 실제 board case는 exact finite-width polygon/void boolean tolerance, signed signal-to-return current/field owner와 same-crop core/DtN partition이 증명될 때까지 차단한다.
5. P1/P2 selected crop의 actual return artwork/net graph 증거에서 terminal-to-return signed current basis와 same-crop core/DtN owner를 만든다. 가까운 via를 return으로 강제하지 않는다.
6. finite-length 3-D reference는 동일 end fixture의 `Z(2l)−Z(l)`로 p.u.l increment를 구해 2-D 결과와 비교한다.
7. full partial/common-mode operator 또는 explicit differential-constraint adapter가 없으면 global MNA promotion을 차단한다.
8. smooth-copper T1을 0–2 GHz에서 통과하기 전 roughness fitting을 시작하지 않는다. roughness source가 없는 네 pair에는 fitted roughness를 주입하지 않는다.

## Primary literature

- Cohn, [Characteristic Impedance of the Shielded-Strip Transmission Line](https://doi.org/10.1109/TMTT.1954.1124875)
- Demeester and De Zutter, [Quasi-TM Transmission Line Parameters of Coupled Lossy Lines Based on the DtN Boundary Operator](https://doi.org/10.1109/TMTT.2008.925215), [author PDF](https://tdmeeste.github.io/files/pubs/QuasiTM_MTT_Demeester2008.pdf)
- Patel and Triverio, [Skin Effect Modeling Through a Surface Admittance Operator and CIM](https://doi.org/10.1109/TMTT.2016.2593721), [author preprint](https://arxiv.org/abs/1509.08357)
- Dienstfrey, Hang, and Huang, [Lattice Sums and the Two-dimensional, Periodic Green's Function for the Helmholtz Equation](https://www.nist.gov/publications/lattice-sums-and-two-dimensional-periodic-green-s-function-helmholtz-equation)
- Kamon, Tsuk, and White, [FastHenry: A Multipole-Accelerated 3-D Inductance Extraction Program](https://doi.org/10.1109/22.310584)
- Ruehli, [Foundational PEEC Formulation](https://doi.org/10.1109/TMTT.1974.1128204)
- Norgren and He, [Exact Field Representation for Centered Zero-Thickness Stripline](https://www.ursi.org/Publications/RadioScienceLetters/Volume3/RSL21-0052-final.pdf)
