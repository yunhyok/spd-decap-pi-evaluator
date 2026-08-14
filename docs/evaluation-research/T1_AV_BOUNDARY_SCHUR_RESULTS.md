# SPD Decap PI Evaluator v0.22.0 — T1 AV-BS1 boundary-Schur results

## Current verdict

현재 상태는 **`AV-BS1-H0_BLOCKED_SPARSE_PATTERN_CONTRACT_BEFORE_FACTOR__H1_CYCLIC_DIAGONAL_CORRECTION_PREREGISTERED_NOT_RUN`**이다.

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

manufactured rectangle의 두 diagonal은 모두 zero weight를 재현해야 하고, noncyclic control은 zero로 만들지 않는다. H1 fixture/static tests/result schema/runner/token이 새 hash로 독립 감사되고 clean commit되기 전에는 `primary-h`를 재실행하지 않는다.

H1 static candidate는 `16 passed`, fixture SHA-256 `e2a1c8efff67873b57dc7a658b013e3e76d0c921988011f4c8e70cd25f00f8a7`, runner SHA-256 `dd880de721b9a688ae953c7363dc4a8b482ec1b26f59871d4ca8f83397eb2b7c`를 재현했다. 새 token은 이 두 hash, H0 artifact, cyclic tag, canonical K hash와 세 독립 감사 증거를 검증한다. 이 값은 static approval이며 아직 H1 physics 실행 결과가 아니다.

## Exact next starting point

1. H0 artifact와 이 원인 분석을 immutable하게 보존한다.
2. 완료된 topology/static/exit 감사와 증거 결합을 포함한 fixture, runner, tests, token, 기준 문서를 clean commit한다.
3. 그 뒤에만 동일 17.5 µm/100 kHz `primary-h` 한 mesh를 다시 실행한다.
4. H1도 어느 gate든 실패하면 결과를 동결한다. 통과하더라도 별도 h2 preregistration/token 전에는 h2를 구현하거나 실행하지 않는다.
