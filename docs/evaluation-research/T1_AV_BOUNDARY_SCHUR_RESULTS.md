# SPD Decap PI Evaluator v0.22.0 — T1 AV-BS1 boundary-Schur results

## Current verdict

현재 AV-BS1 physics artifact 상태는 **`passed_AV_BS_h2_stage_only_pending_h4_preregistration`**이다. 17.5 µm/100 kHz의 guarded `primary-h2` 한 번이 local mandatory gate를 통과했고 `mandatory_stage_pass=true`, `failure_codes=[]`, factorization/physics `true`다. 그러나 `next_stage_authorized=false`, `fine_analytic_pass=null`, `mesh_convergence_pass=null`, `final_circle_pass=null`이므로 이는 h2 한 mesh의 단계 제한 통과일 뿐 circle oracle 또는 h4 권한이 아니다. H0 negative, H1 coarse-h pass, H2-P0 assembly manifest, H4-P0 assembly certificate와 H4-P0R manifest parent를 그대로 보존한다. 후속 P1 factor-only executable의 두 공개 시도는 모두 claim/factor 전에 중단됐으므로 H4-P0R factorization result가 없고 현재도 `factor_fit_unproven=true`다.

2026-08-15 clean commit `4fa5ec80a261c21c8489ecd8b708a62bba769a7a`에서 17.5 µm, 100 kHz, `h` 한 mesh의 guarded `primary-h`를 처음 실행했다. 실행은 **`BLOCKED_AV_BS_MESH_HASH`**로 fail-closed 됐다. factorization, harmonic extension, boundary-Schur `Y`, modal response와 physics pass/fail 값은 생성되지 않았다. 따라서 이 결과는 AV-BS1 physics negative가 아니라 **사전등록 sparse-pattern 계약의 negative**다.

제품 parser/solver/UI/version/installer, PowerSI reference와 GitHub 원격은 변경하지 않았다. `h2`만 physics-bearing preregistered one-use path로 실행했다. 두 H4-P0R-P1 public invocation은 resource/lifecycle dispatcher만 시작했으며 factor, RHS/solve, `h4` physics, withheld radius와 EQ0는 실행하지 않았다.

## Immutable H0 artifact

로컬 ignored artifact는 `validation-output/av-bs1/av-bs1-primary-h-20260814T183617Z.json`에 보존한다. tracked 기준 문서에는 아래 digest와 판정만 고정한다.

| 항목 | 값 |
|---|---:|
| artifact SHA-256 | `848a2c5a3683f492d84b59be42ea20b9ed5e2e745304cb9ded88131e8bca45f0` |
| final payload SHA-256 | `5f0b185809cfe3fd8cb033c86ff74abfee5b3c7ccf75536227affc1e0b588a46` |
| numerical payload SHA-256 | `448a7c2140eed42be0e4541a2786ad99471437bbaa1dc3fa224ee351fc7c606b` |
| resource report SHA-256 | `cccc33bd2f63585e059830801bf79db1af91aa4011fbc5a158f5860b4b974a74` |
| status / detail | `BLOCKED_AV_BS_MESH_HASH` / `h sparse nnz mismatch` |
| wall / successful samples | `1.0625909 s` / `8` |
| execution-tree peak WS | `179,126,272 B = 170.828125 MiB` |
| execution-tree peak private/commit | `1,455,218,688 B = 1.355278 GiB` |
| minimum system commit headroom | `71.073036 GiB` |
| minimum available physical memory | `46.802578 GiB` |
| resource stop / gate | `null` / passed |

resource 수치는 PowerShell runner와 Python child/descendant를 합한 이 host의 process-tree 측정값이며 8 GB laptop 증명이 아니다. 수치 gate 전 차단이므로 solve timing으로도 사용하지 않는다.

H0 resource report의 `child_exit_code=0`은 canonical failure wrapper가 실제로 exit 2를 반환한 사실과 모순한다. Windows PowerShell `Start-Process`가 redirected child의 native handle을 생존 중 retain하지 않아 manual polling 뒤 `ExitCode`가 `$null`이 됐고 `[int]$null`이 0으로 기록된 runner provenance bug다. `mandatory_stage_pass=false`와 원래 mesh failure code는 보존돼 false pass는 없었지만, H1 runner는 child handle을 즉시 retain하고 success/failure wrapper를 exit `0/2`와 각각 결합한다.

## Root cause

기존 fixture는 `V+2E=2049+2×6016=14,081`을 stiffness `K`와 consistent mass `M`의 공통 post-`eliminate_zeros()` nnz로 요구했다. 이는 `M`에는 맞지만 `K`에는 틀리다.

| 구조 | 값 |
|---|---:|
| local directed triangle contributions | `35,712` |
| unique directed nodal adjacency | `14,081` |
| binary64 raw `K.nnz` after zero elimination | `14,075` |
| `M.nnz` | `14,081` |
| `MΓ.nnz` | `384` |

각 annular cell은 inner/outer chord가 평행하고 radial leg 길이가 같은 isosceles trapezoid이므로 cyclic quadrilateral이다. 대각선 `ij`의 P1 Laplacian weight는 두 맞은편 각 `α,β`에 대해 `-(cot α+cot β)/(2µ)`이고 `α+β=π`이므로 정확히 0이다. frozen mesh에는 `15 bands × 128 sectors = 1,920`개의 triangulation diagonal이 있다.

binary64에서는 세 undirected diagonal만 bitwise 0이 되어 여섯 directed CSC entry가 제거됐고, 나머지 1,917개는 물리 coupling이 아닌 cancellation residue로 남았다. 따라서 runtime 우연값 `K.nnz=14,075`를 physics contract로 승격하지 않는다.

독립 Decimal-90 계산은 1,920개 대각선의 두 local contribution에 대해 최대 상대 잔차 `2.2252682637458195e-86`을 재현했다. binary64 raw assembly의 최대 상대 상쇄 잔차는 `2.1676835831040652e-13`이며 frozen bound `128u·κ2,max = 5.788860430596403e-13` 아래다.

## H1 preregistered correction

H1은 magnitude search로 zero edge를 찾지 않는다. generator의 radial-band parity에서 1,920개 diagonal owner를 직접 만들고 canonical tag SHA-256 `e80c75ed02030cb22b648b39d613abac42bf6a4c4dfb46704789eedcc17f5e91`을 요구한다.

각 tagged edge는 두 incident triangle이어야 하며, unassembled 두 local stiffness contribution의 상대 상쇄가 위 `128u·κ2,max` bound를 통과해야 한다. raw transpose를 평균내지 않는다. raw symmetric residue를 `k=Kij=Kji`라 할 때 다음 graph-Laplacian edge block만 제거한다.

```text
Kij = Kji = 0
Kii <- Kii + k
Kjj <- Kjj + k
```

이 연산은 constant-field row sum을 보존한다. untagged off-diagonal은 바꾸지 않는다. canonical support는 radial/circumferential edge만 남아 `K.nnz=10,241`이어야 한다. `M`은 triangulation diagonal coupling을 유지해 `14,081`, `MΓ`는 `384`다.

| certificate | frozen value |
|---|---:|
| raw `K` SHA-256 | `9c199514e0c1863744d40babe3b218f576f74ebbc909780dbd87046de40e1e71` |
| canonical `K` SHA-256 | `733c83aec575cb28807bebc7a10fb9e05a83ca35c4775677fd347165fb421548` |
| `M` SHA-256 | `2900e4f481fc9d40ce1282e2532f4ce29b0b858a939fbd3644556e60ba0f8bfe` |
| `MΓ` SHA-256 | `4bca013ca53c8ce9473329d85d0d8d6691bdd18ccffa232abac89337e5c58d08` |
| raw/canonical constant-null relative | `1.2640751e-16 / 1.2084099e-16` |
| canonicalization relative Frobenius | `1.3572885e-16` |
| maximum tagged raw magnitude | `4.2346073e-9` |

manufactured rectangle의 두 diagonal은 모두 zero weight를 재현해야 하고, noncyclic control은 zero로 만들지 않는다. 이 계약은 commit `057ed39f6a80dfe05aeab06c8bb8f6e6e3429a93`에 고정됐고, 아래 H1 `primary-h`가 같은 canonical certificate를 재현했다.

H1 static candidate는 `16 passed`, fixture SHA-256 `e2a1c8efff67873b57dc7a658b013e3e76d0c921988011f4c8e70cd25f00f8a7`, runner SHA-256 `dd880de721b9a688ae953c7363dc4a8b482ec1b26f59871d4ca8f83397eb2b7c`를 재현했다. 새 token은 이 두 hash, H0 artifact, cyclic tag, canonical K hash와 세 독립 감사 증거를 검증한다. 이 값은 static approval이며 아직 H1 physics 실행 결과가 아니다.

## H1 `primary-h` result

clean commit `057ed39f6a80dfe05aeab06c8bb8f6e6e3429a93`에서 17.5 µm, 100 kHz, `h` 한 mesh를 external execution-tree guard 아래 한 번 실행했다. ignored artifact는 `validation-output/av-bs1/av-bs1-primary-h-20260814T190732Z.json`이다.

| artifact | SHA-256 / value |
|---|---|
| file bytes / SHA-256 | `711,486` / `af17bbcc49cebc7e9ddb88e821ec0338a435fb2bf8ce51b117cfb3019b78b44d` |
| final payload SHA-256 | `3cdef96c8de1585acfe4cc256d63e6815b93be9906df51d9b97ff5d4f51f330b` |
| numerical payload SHA-256 | `a955393d69e22e87d759656b598e71fccee2492eff4c8d305db48623662f9fc4` |
| resource report SHA-256 | `8387d19253279120a116cf0e8b48c67007394a86d140bfb0a1770ad4f8b87d72` |
| review token SHA-256 | `5ad21ccec9cb81e8999441fc43338589ecb92cf0ae08e7bc2b8b4a8c411f8d4e` |
| status / failure codes | `passed_AV_BS_h_stage_only_pending_h2_review` / `[]` |

mesh와 H1 certificate는 preregistration 값을 그대로 재현했다: raw/canonical `K.nnz=14,075/10,241`, `M.nnz=14,081`, `MΓ.nnz=384`, tag `1,920`, maximum cancellation/bound `2.1676835831040652e-13 / 5.788860430596403e-13`, correction relative Frobenius `1.3572884739080543e-16`이다.

| stage-evaluable gate | raw value | verdict |
|---|---:|---|
| max assembly transpose relative | `0` | pass |
| max background/conductor backward residual | `4.181585185007905e-17` | pass |
| max equilibrated `kappa1 u` estimate | `2.5342296831638465e-12` | pass |
| reverse-order relative | `2.2332401790276927e-16` | pass |
| raw full-space reciprocity relative | `1.072085475889738e-15` | pass |
| minimum Hermitian eigenvalue / tolerance | `9.256477814190828e-6 / 4.478026532708378e-13 S·m` | pass |
| max signed-mode power mismatch | `4.7212709501079303e-14` | pass |
| `m↔-m` trend-only relative | `1.5004933643655415e-16` | diagnostic/trend only; not gated at `h` |

background/conductor factor의 `L/U.nnz`는 각각 `54,630/54,682`, `58,747/60,956`이고 두 solve는 각 128 RHS를 batch `4`로 처리했다. coarse analytic trend는 max relative `0.011619408480801386` (`|m|=4`), nine-mode RMS `0.006192419097349362`, max phase `0.00022308014897348525°`다. max relative 값은 `1%`보다 크지만 `h`에서는 preregistration에 따라 trend로만 기록하고 fine analytic gate로 판정하지 않는다.

resource monitor는 child exit `0`, successful tree samples `11`, wall `1.4714704 s`, stop reason `null`을 기록했다. execution-tree peak WS는 `194,289,664 B = 185.2890625 MiB`, private/committed는 `1,516,937,216 B = 1,446.6640625 MiB = 1.4127578735 GiB`다. minimum commit headroom은 `70.969673 GiB`, minimum available physical memory는 `46.753937 GiB`였다. 이는 이 host의 coarse h-stage evidence이며 8 GB laptop proof가 아니다.

authorized primary-h token은 artifact에 SHA-256 `5ad21ccec9cb81e8999441fc43338589ecb92cf0ae08e7bc2b8b4a8c411f8d4e`로 결합돼 있다. 독립 review 뒤 active file은 SHA-256 `80ffd8b486dbdd8087eb205f71137663cef0497d8ba8df74253743b302fe6f35`의 consumed tombstone으로 바꿨다. 따라서 현재 validator는 primary-h를 solve 전에 거부하며 h2 권한은 별도 token에서만 만들 수 있다.

## H2-P0 assembly-only preregistration

H1 result와 실행 권한을 바꾸지 않고 H2 refined topology와 assembly를 별도 manifest-only fixture로 고정했다. canonical payload SHA-256은 `68d2e20a471e0e9475dd8e575ffd1e246f4098bb6c0e2b688d0f6f702fb511ba`이고 static test는 `6 passed`다.

| P0 field | frozen value |
|---|---|
| mesh `V/E/T/B`, interior/boundary | `8065/23936/15872/256`, `7809/256` |
| candidate / excluded / canonical tags | `3840 / 128 / 3712` |
| tag SHA-256 | `287feee5d6fb4299895455b870de0eda484e07617cbc6db43bddd0d11b5b5968` |
| raw / canonical `K.nnz` | `55937 / 48513` |
| `M / MΓ.nnz` | `55937 / 768` |
| cancellation maximum / bound | `3.6286352763558246e-13 / 5.802823100831367e-13` |
| minimum untagged two-triangle ratio | `0.19705186275542091` |
| sparse base / raw preflight / +25% | `1,328,172 / 2,043,070,476 / 2,553,838,095 B` |
| factorization / physics / authorization | `false / false / not_authorized` |

projected outer-boundary midpoint를 포함한 child 128개는 cyclic zero edge가 아니므로 제외한다. raw/canonical K, M, MΓ, support/partition exact hashes와 arithmetic details는 [`T1_AV_BOUNDARY_SCHUR_H2_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H2_PREREG.md)에 고정한다. 이는 h2 result artifact가 아니고 coarse H1의 `next_stage_authorized=false`도 바꾸지 않는다.

## H2-P1 frozen execution contract

H2-P0와 H1 artifact를 결합하는 별도 research-only P1 fixture, native process-tree runner, result/failure finalizer, atomic token-consumption path와 bounded regression suite를 작성했다. 현 manifest payload SHA-256은 `05a21364deeb123432a0f52af9a6dbb818f9c9aa2814c34665dfc8da6b8463c4`이고 P1 suite는 `31 passed`, P0+P1 정적 suite는 `37 passed`다. Python compile, PowerShell AST, manifest와 diff check도 통과했다.

| P1 candidate object | SHA-256 |
|---|---|
| fixture | `0600604cf7ab7b2a1d2b67c240ed1c659001a989482ce4f94c4b97c305ca3ca4` |
| runner | `f54d2645bb01dcd287a7836af8a99990055803c793f45ffd9f91e06426624dc6` |
| tests | `01f0203009087a97f00b58461fb6fda339a2866700399be8d5453bdff47c3199` |
| preregistration document | `adbd6756343eafa426b54a90aa2dfa56c053c56b145331f803a3e7ce4338b868` |

P1은 signed M9 `h→h2`를 trend-only로 유지한다. 성공 결과도 fine analytic/mesh convergence/final circle을 `null`로 두고 `next_stage_authorized=false`여야 한다. Power certificate는 `7809×9` complex128 interior modal-field blob을 보존하고 finalizer가 frozen H2 `K/M`, conductor `Ap`와 trace mass를 assembly-only로 재구성해 각 field의 PDE backward residual, volume integral, boundary power와 mismatch를 독립 재계산한다. 위조된 pass/failure/resource/field 증거는 token을 재사용 가능하게 남기지 않고 consumed tombstone으로 fail-closed한다.

이 절과 [`T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md)는 실행 전 계약의 immutable snapshot이다. preregistration commit `defbd6dda2e62637f41c1a8fc8f8f422aaba0e8b`와 token-only child commit `9f3d36b106cbad033ae35a716e21d47b2e178aa1` 뒤 아래 one-use result가 생성됐다. preregistration 문서 자체는 token binding SHA-256을 보존하기 위해 사후 수정하지 않는다.

## H2 `primary-h2` result

ignored artifact [`../../validation-output/av-bs1/av-bs1-primary-h2-20260814T222208Z.json`](../../validation-output/av-bs1/av-bs1-primary-h2-20260814T222208Z.json)은 17.5 µm, 100 kHz, `h2` 한 mesh의 guarded result다. wrapper, numerical payload, claim, guard, raw resource report와 one-use token consumption을 세 독립 감사가 read-only로 확인했다.

| artifact / lineage | SHA-256 / value |
|---|---|
| file bytes / SHA-256 | `4,319,474` / `b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55` |
| result payload SHA-256 | `5704f3feb5bb90b6e38f8ed3ec67fc690339e9b7f0c1722d40a977295e82593e` |
| numerical payload SHA-256 | `bd2f6542a93e3a7adc62f4435540752651ddf1cff84226319b4aa5e8dbdc0be5` |
| raw resource report SHA-256 | `b17468d7383ed5021a783ade4c3b7c1c5e21628580d5f6c298ef1b7b97b26bd2` |
| preflight / claim / guard SHA-256 | `6eafea2fe291a7d013c02ee560570fa0afc4acecff78b1145ac89250d9387452` / `f6837c2957704e836fedd5db2e64a0bb4a276c4e5cfc98c4f6a5f0c07101b051` / `57b70d0f96c558d6650fdf2e9c0a9d29e89d5ff45464ce64ff3a5b648c40af14` |
| original authorized token SHA-256 / id | `f8a1aa5804b0edfd58f485faf83f7c6f73c284bc04b1a4abffbd8d0260aca4df` / `f4a717b50a854973b1ddcfe2776eb021` |
| consumed tombstone SHA-256 | `81574c1099bdd140004940b7cb768a20298b153445c1b16b9e21de95011c48c2` |
| execution git head / preregistration parent | `9f3d36b106cbad033ae35a716e21d47b2e178aa1` / `defbd6dda2e62637f41c1a8fc8f8f422aaba0e8b` |
| status / failure codes | `passed_AV_BS_h2_stage_only_pending_h4_preregistration` / `[]` |

mesh는 preregistration 값을 그대로 재현했다: `V/E/T/B=8065/23936/15872/256`, interior/boundary `7809/256`, raw/canonical `K.nnz=55937/48513`, `M.nnz=55937`, `MΓ.nnz=768`, mesh SHA-256 `34eb4f9cadcefd0b20cff3ae6c483dbee4e412ca11d0ff1a8c2c6f49ec7896a9`다. signed-M9 conductor interior field certificate는 little-endian complex128 `7809×9`, raw `1,124,496 B`이고 finalizer가 frozen `K/M/MΓ`에서 conductor PDE residual과 volume power를 다시 계산했다.

| h2 stage-evaluable gate | raw value | verdict |
|---|---:|---|
| full assembly transpose relative | `0` | pass |
| background / conductor solve residual | `5.981236336103848e-17 / 5.383112747661412e-17` | pass |
| background / conductor `κ1u` | `1.0244351010046593e-11 / 1.0244323279051323e-11` | pass |
| signed-M9 PDE residual max | `4.421492019510585e-16` | pass |
| reverse-order relative | `2.465264212368452e-16` | pass |
| raw full-space reciprocity relative | `1.8831838353064343e-15` | pass |
| minimum Hermitian eigenvalue / tolerance | `2.3635694288740116e-6 / 2.2396879199202253e-13 S·m` | pass |
| max signed-mode power mismatch | `1.1134002810265329e-13` | pass |
| `m↔-m` relative | `1.6448277478488274e-16` | diagnostic/trend only; not a final gate at `h2` |

독립 감사의 다른 assembly evaluation order는 power mismatch `1.29614e-13`을 재현했다. artifact 값과의 차이는 binary64 roundoff 범위이며 둘 다 `1e-8` gate보다 충분히 작다.

| trend-only comparison | RMS relative | max relative | max eligible phase |
|---|---:|---:|---:|
| `h→h2` signed M9 | `0.00462796342945947` (`0.462796%`) | `0.008680146425905828` (`0.868015%`) | `0.00016639608723203282°` |
| h2 analytic | `0.0015525356105053729` (`0.155254%`) | `0.0029139683841210083` (`0.291397%`) | `0.00005682250879185882°` |

두 행은 모두 `gate_applied=false`, `gate_pass=null`인 진단값이다. mandatory mesh convergence와 fine analytic 판정은 별도 h4에서 처음 수행한다.

resource runner는 child exit `0`, successful tree sample `71`, wall `8.6075797 s`, stop reason `null`과 mandatory resource gate `true`를 기록했다. runner-inclusive execution-tree peak WS는 `348,827,648 B = 332.667969 MiB`, private/commit은 `1,704,058,880 B = 1.587029 GiB`, minimum available physical memory는 `45.138222 GiB`, minimum system commit headroom은 `69.289360 GiB`였다. 이는 이 host의 h2-stage evidence이며 8 GB product proof가 아니다.

active token file은 SHA-256 `81574c1099bdd140004940b7cb768a20298b153445c1b16b9e21de95011c48c2`의 `AV-BS1-h2-p1-consumed-review-token-v1`로 교체됐다. `uses_remaining=0`, `consumption_validated_pass=true`, result/resource/guard evidence `true`, `next_stage_authorized=false`이므로 같은 one-use token은 재사용할 수 없다.

실행 뒤 정적 회귀의 세 executable-stage case는 pre-run의 “token missing”과 post-run의 “consumed token schema mismatch”를 모두 fail-closed success로 인정하도록 lifecycle assertion만 갱신했다. post-run P1 test SHA-256은 `9d266ab5819b30bcae4d269f8c064d496b79290f509fbef7e4a95ab2f7f98654`, P0+P1 결과는 `37 passed`다. artifact가 결합한 preregistration test SHA-256 `01f0203009087a97f00b58461fb6fda339a2866700399be8d5453bdff47c3199`는 commit `defbd6d...`의 역사적 실행 입력으로 그대로 보존한다.

## H4-P0 assembly-only preregistration

H2 result와 consumed token을 바꾸지 않고 별도 H4-P0 fixture가 H2를 한 번 더 deterministic 1-to-4 refine했다. 이 stage는 factorization, RHS, harmonic extension, boundary operator 또는 physics를 실행하지 않으며 `authorization_state=not_authorized`, `primary_h4_authorized=false`다.

| H4-P0 object | SHA-256 |
|---|---|
| fixture | `331218882d2004d0d97e03378ae8af12b23cb4129a9c062e0592ee590a53e94b` |
| manifest-only runner | `b45c907fb5300e46717f423c8512a3500c7db8b42b647101d64a8a11440116c9` |
| static tests | `1539ef4151b8ea416c9ee2f2bf71c1baebd4e83bb2d3684e050437fcb1def40c` |
| preregistration document | `419dfb85ff40a43f2a0c2b1143b8531b16c95402f0c0d454764cfd3f5c03e524` |
| manifest payload | `71f8e902322016541bd9302231fa9965d7dfff67cdce1135e16ee1011a2aa990` |

manifest는 `V/E/T/B=32001/95488/63488/512`, interior/boundary `31489/512`, mesh SHA-256 `a91b4bf147628a34d1a29144ae353a83110b1756c699c71e2b57e9c36822835b`를 재현했다. H2 canonical zero edge 3,712개의 두 child를 모두 소유하므로 tag는 7,424개이고 exclusion은 0이다. candidate/tag SHA-256은 `3902a43ddd16f2cfce16b2892b908f45d02f4464c5157a5c42b5b3ac5cb9d98d`다.

H2 `128uκ`를 복사하면 16 tags가 넘으므로 H4는 추가 midpoint arithmetic을 반영한 `256uκ`를 별도 고정했다. maximum/bound/margin은 `8.540375354048666e-13 / 1.1605646201662821e-12 / 1.358915237391882`다. raw/canonical K는 `222977/208129` nnz와 SHA-256 `8a020c809634a9794292f49198ff1bede84328b6e2c5ef988255cbff32cfe93b` / `a510df2ab39cb85640720f863341d1fe468562442ecb074bafcaf70d9846f2e7`를 재현했고 M/MΓ는 `222977/1536` nnz다. null/transpose/support/correction과 partition hashes도 독립 replay와 일치했다.

resource는 두 의미를 분리한다. all-dense factor upper의 25% margin은 `40,448,792,335 B`로 4 GiB ceiling을 실패한다. one resident factor를 2 GiB hard cap으로 둔 prospective sparse envelope는 `3,470,862,055 B`, 4 GiB slack `824,105,241 B`지만 실제 factor fit을 측정하지 않았으므로 `factor_fit_unproven=true`다. 이 때문에 factorization보다 먼저 별도 H4-P0R contract-only manifest를 고정했다.

정적 결과는 `6 passed`; Python compile, PowerShell AST, UTF-8/LF/fence/link와 diff checks가 통과했다. 이 결과는 H4 physics, h2-to-h4 convergence, fine analytic, final circle 또는 withheld pass가 아니다.

## H4-P0R factor-pilot contract-only preregistration

H4-P0R은 H4 physics 전 실제 sparse factor fit을 측정하기 위한 별도 pilot의 **계약만** 고정한다. 현재 status는 `preregistered_H4_P0R_contract_only_no_factor`, `authorization_state=not_authorized`, `available_solve_stages=[]`, `factorization_performed=false`, `physics_solve_performed=false`, `next_stage_authorized=false`다. manifest runner는 `manifest`만 노출하며 token, claim, guard child, finalizer 또는 factorization 경로가 없다.

| H4-P0R object | SHA-256 |
|---|---|
| contract-only fixture | `f6c4149e425021a9133d7ac98fbea403ba048e48f9c70d6e88171e3436374461` |
| manifest-only runner | `ff6623280a728b2dfe6ad219e965d4c21d2392020b6d0490dcc7e63f43a9e50f` |
| static tests | `d071584cf6843ca9d2b75cac348ddfa343ac6f173646fe4c2575af68d6d73129` |
| preregistration document | `5db1b047ca72c72be338b6003507e04598c0b0b89b992302d3aea108a8d2e1f4` |
| manifest payload | `eaab10df7fb1557490cc75db7e7ff9fca2881013b6a6fb13f02faea43ddf7023` |
| matrix/equilibration contract | `89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46` |
| resource policy | `13df68af8b9008824c09653bdb32a56c618107858cdb71b987bf6f4375915680` |

manifest는 H4-P0 commit `8f40fe5696496edb2cb73086927f833ded5e0d5e`과 그 exact fixture/runner/test/doc/payload, H2-P0 및 H2-P1 artifact/consumed tombstone provenance를 재검증한다. frozen interior matrices는 `I=31489`, `KII.nnz=204545`, `MII.nnz=219393`, `AbII.nnz=204545`, `ApII.nnz=219393`다. SHA-256은 차례로 `dddee397ee96ff0bd7acc321cd5755dd611f1e3d1f6bfcf9664c388d27184361`, `4dccc26f1a12bbab4a4ee863e509d317cb518075661867da4e0b6980f7dcc6c4`, `8c099d2cb5947d1b72bd74f64329879c221b86c569b4100baa47dfea9aeff641`, `5ead3bbdc2fc1b4f7a1a94bb7c9ebf9401c6a7d96ee63335045d64e3b082225f`다. manual row/column equilibration과 fixed SuperLU options도 factor 결과를 보기 전에 동결했다.

향후 executable은 `AbII` factor를 먼저 만들고 완전히 해제한 뒤 `ApII` factor만 만들며 동시에 factor 하나만 resident로 둔다. `factor.solve`, RHS, harmonic extension, boundary operator `Y`, signed modes, power와 physics result는 모두 금지한다. process-tree ceiling은 wall `900 s`, WS `4 GiB`, private/commit `5 GiB`이고 pre-spawn physical/commit-headroom floor도 H4-P0 baseline보다 약화하지 않는다. pass/fail 모두 one-use token을 consume하며 pass도 H4-P1을 자동 승인하지 않는다.

정적 결과는 `23 passed`; direct/Powershell manifest가 같은 payload를 재현했고 세 독립 read-only 감사가 승인했다. 이는 factor fit 또는 8 GB proof가 아니다. 다음은 이 frozen 계약과 분리한 executable/runner/claim/finalizer/token family의 static audit 및 clean commit이며, 그 뒤에만 factorization-only pilot 한 번을 실행할 수 있다.

## H4-P0R-P1 public attempts and retry-v2 corrective candidate

H4-P0R-P1은 parent matrix/equilibration/factor 순서를 바꾸지 않고 public outer observer, bounded control-plane, one-use claim/guard/result/tombstone/seal-v2와 factor-only child를 구현했다. 두 fresh token-only commit을 각각 정확히 한 번 호출했지만 둘 다 factor child에 도달하지 않았다.

첫 시도는 token-only commit `b6c8615639a8fb283909bd6613ab5bf5b0e09cb2`에서 `2026-08-15T08:49:35Z`에 시작해 0.83 s 뒤 종료됐다. public dispatcher의 첫 root-only process enumeration이 PowerShell pipeline에서 scalar `Int32`로 축약되어 strict-mode `$ids.Count`가 실패했다. 이는 token read, observer session, inner spawn, claim과 factor보다 앞이었다. token ID `92e6edd1bf01428199c5a492e64858ea`는 untouched `uses_remaining=1`이었지만 재시도하지 않았고 deletion-only commit `412e88049ec1461dd15ae324b583c114fb890393`에서 제거했다.

두 번째 corrective contract `2b7e302aa4ef5abedcd22cda0003380cb8765a42`와 token-only child `d9e064fee6822d8f39318a55646860e24b502f35`도 한 번만 호출했다. token ID는 `7a26f8ea4f89493a95fa78bc62b2d431`이다. observer session `e017a79e5ce345959d8e77f46b8d90b2`는 `2026-08-15T09:16:22.8172642Z`–`09:16:24.0712914Z`, `1.2522612 s`를 기록했다. close-v1 SHA-256은 `3de68a75e4e1fa23876b63f1843ad217f7ba0290f8a86b491bac09124f5ea39c`이고 다음만 보존한다.

| second-attempt evidence | value |
|---|---|
| stop/gate | `OUTER_RESOURCE_EXCEPTION`; `mandatory_outer_resource_gate_pass=false` |
| samples | outer `2`; inner-visible `2` |
| retained inner exit | `-1`; outer cleanup verified |
| streams | stdout/stderr both empty |
| claim/recovery | no claim; `no_claim_no_recovery_required` |
| absent | ready/start-release/complete/exit-release, control report, claim, guard, result, prefix, quarantine, journal, tombstone, seal |

Inner는 `inner-ready.json`을 쓰고 outer start-release를 받아야 preflight, claim, factor child로 진행한다. 두 marker가 모두 없으므로 factorization, RHS, solve와 physics는 불가능했다. v1 close는 caught exception의 exact PID/API/message를 저장하지 않았다. PID timing은 short-lived inner `Add-Type` compiler/bootstrap descendant가 Toolhelp enumeration과 PSAPI metric probe 사이에서 종료된 race를 강하게 시사하지만, 이는 정확한 원인이 아니라 high-confidence inference다. Untouched second token도 재시도하지 않고 deletion-only retirement commit `7dd1db501b505d1a6a36f0d1f99df23fca655a93`에서 제거했다.

현재 corrective contract는 outer observer의 tree sample만 최대 3 total attempts로 제한한다. native retained handle의 exited 상태 또는 exact not-found 87, fresh complete Toolhelp snapshot의 target 부재, 전후 root PID/birth 동일성을 모두 확인한 non-root disappearance만 whole sample을 재시작한다. failed attempt의 모든 합계와 returned membership은 폐기하고 identity-bound PID만 cleanup/evidence에 유지한다. root loss/reuse, live/access/query failure, target reappearance, incomplete snapshot, malformed metric과 exhaustion은 fatal이다. instrumented outer scope 밖의 call은 default 1 attempt다. close-v2는 bounded retry events와 nullable typed failure를 고정된 ASCII code/정수로 기록하며 raw localized exception을 저장하지 않는다.

현재 static corrective evidence는 Python SHA-256 `8c497cb1d0926600b2ddd974df6fd8d40b09471c617869294a01c0e018ce5acf`, runner `4272d6725cb0e16b7ce39b083985965f792da5893f222ba07cf91695fba8f30c`, tests `b44366596690d6d55d18a89d8df2370faca767fca722affd5e07268da3195235`다. 187-test suite는 real `splu` tripwire 아래 exact function-slice race, partial-sum discard, live failure, root/reuse failure, bounded exhaustion, incomplete snapshot, cleanup-sample failure preservation, operation allowlist와 retry-event/sampled-identity close-v2 tamper를 검사한다. 이는 static approval candidate일 뿐 실제 factor fit, PowerSI 정확성 또는 8 GB proof가 아니다.

## Exact next starting point

1. H0 negative와 H1 h-stage artifact를 모두 immutable하게 보존한다.
2. H2-P0 manifest-only fixture/runner/test/docs commit `4c1e3fce8aac659dd0aedb06c2b6d274fff73a12`를 보존한다.
3. H2 result digest, 독립 audit와 consumed tombstone commit `ad12df1`을 보존한다.
4. H4-P0 mesh lineage, canonical assembly와 dual resource envelope commit `8f40fe5696496edb2cb73086927f833ded5e0d5e`를 보존한다.
5. 완료한 H4-P0R parent와 두 P1 pre-factor 실패, validation-output 및 두 deletion-only token retirement를 보존한다. `b6c8615...`와 `d9e064f...`를 재실행하지 않는다.
6. retry-v2 fixture/runner/tests/docs를 token-absent clean contract와 독립 audit에 고정하고 committed safe manifest를 재검증한 뒤, 그 commit만 부모로 하는 fresh token-only child를 만든다.
7. 새 token에서 `KII/ApII` factor fill과 process-tree resource만 한 번 측정한다. RHS/extensions/Y/modal physics는 금지한다. P0R terminal result를 독립 audit한 뒤에만 h2→h4/fine analytic gates와 H4 result schema를 별도 사전등록한다. clean H4-P1 audit와 fresh one-use token 전에는 h4 physics를 실행하지 않으며, h4가 통과해도 withheld radius/EQ0는 각각의 후속 계약 전까지 금지한다.
