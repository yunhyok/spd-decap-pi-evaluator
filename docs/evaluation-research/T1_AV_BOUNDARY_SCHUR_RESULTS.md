# SPD Decap PI Evaluator v0.22.0 — T1 AV-BS1 boundary-Schur results

## Current verdict

현재 AV-BS1 artifact 상태는 **`passed_AV_BS_h_stage_only_pending_h2_review`**다. `mandatory_stage_pass=true`지만 `next_stage_authorized=false`, `fine_analytic_pass=null`, `mesh_convergence_pass=null`, `final_circle_pass=null`이다. 따라서 이는 17.5 µm/100 kHz의 coarse `h` 단계 제한 통과이며 circle oracle 또는 h2 권한이 아니다.

2026-08-15 clean commit `4fa5ec80a261c21c8489ecd8b708a62bba769a7a`에서 17.5 µm, 100 kHz, `h` 한 mesh의 guarded `primary-h`를 처음 실행했다. 실행은 **`BLOCKED_AV_BS_MESH_HASH`**로 fail-closed 됐다. factorization, harmonic extension, boundary-Schur `Y`, modal response와 physics pass/fail 값은 생성되지 않았다. 따라서 이 결과는 AV-BS1 physics negative가 아니라 **사전등록 sparse-pattern 계약의 negative**다.

제품 parser/solver/UI/version/installer, PowerSI reference와 GitHub 원격은 변경하지 않았다. `h2`, `h4`, withheld radius와 EQ0도 실행하지 않았다.

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

## Exact next starting point

1. H0 negative와 H1 h-stage artifact를 모두 immutable하게 보존한다.
2. H1 artifact의 수학·checksum·resource 독립 감사를 기준 문서와 session log에 고정한다.
3. `h2` mesh/fixture/result schema/resource estimate와 one-stage review token을 결과값과 무관하게 별도 preregistration commit으로 고정한다.
4. 그 clean commit과 새 token 전에는 `h2`를 구현하거나 실행하지 않는다. `h2`를 통과해도 h4는 또 다른 preregistration 전 금지한다.
