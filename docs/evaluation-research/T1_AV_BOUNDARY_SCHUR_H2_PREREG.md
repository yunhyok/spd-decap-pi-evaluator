# SPD Decap PI Evaluator v0.22.0 — AV-BS1 H2-P0 assembly preregistration

## Current verdict

현재 상태는 **`preregistered_H2_P0_assembly_only_no_solve`**다. H1 coarse `h` 뒤의 deterministic one-to-four refinement를 재생성해 topology lineage, raw/canonical P1 assembly, partition과 conservative resource preflight를 고정했다. 이 P0 fixture는 factorization, harmonic extension, boundary-Schur response, review token, resource-guard child 또는 result finalizer를 노출하지 않는다.

따라서 다음은 모두 아직 승인되지 않았다.

- `primary-h2` physics solve
- `h→h2` signed-`M9` trend
- h2 reciprocity/passivity/power/residual/condition gate
- `h2→h4` mesh convergence와 fine analytic gate
- final circle, withheld radius, EQ0, product 또는 PowerSI promotion
- 8 GB laptop 실행 가능성

H2-P0는 결과를 보지 않고 조립 계약을 먼저 고정하는 단계다. 실행 가능한 solve stage는 빈 목록이고 `authorization_state=not_authorized`, `factorization_performed=false`, `physics_solve_performed=false`다.

## Frozen implementation bytes

| artifact | SHA-256 |
|---|---|
| H2-P0 fixture [`../../tools/research/av_bs1_boundary_schur_h2.py`](../../tools/research/av_bs1_boundary_schur_h2.py) | `032100623fca51ab22a48493b46f23bc8ce5fd1250203d527062671d47599384` |
| manifest-only runner [`../../tools/research/run_av_bs1_h2_stage.ps1`](../../tools/research/run_av_bs1_h2_stage.ps1) | `6ce0002e9d3790542008d9e1e608168f1e40f51d56c9a8ff1d37003a15f8feb7` |
| bounded test [`../../tests/test_research_av_bs1_boundary_schur_h2.py`](../../tests/test_research_av_bs1_boundary_schur_h2.py) | `039b84ea05ee31c9f9d025d85dd1ec8d4741adc18e6db8883073d93e4d828c69` |
| canonical manifest payload | `68d2e20a471e0e9475dd8e575ffd1e246f4098bb6c0e2b688d0f6f702fb511ba` |

P0가 import하는 H1 연구 도구와 consumed authorization state도 exact bytes로 결합한다.

| H1 dependency | SHA-256 |
|---|---|
| H1 fixture | `e2a1c8efff67873b57dc7a658b013e3e76d0c921988011f4c8e70cd25f00f8a7` |
| H1 runner | `dd880de721b9a688ae953c7363dc4a8b482ec1b26f59871d4ca8f83397eb2b7c` |
| consumed H1 primary-h tombstone | `80ffd8b486dbdd8087eb205f71137663cef0497d8ba8df74253743b302fe6f35` |

P0는 H1 result artifact를 읽지 않는다. H1 file/payload/numerical/mode-view hashes는 executable H2-P1 token과 result schema에서 별도로 결합해야 한다.

## Mesh and lineage freeze

H2 mesh는 H1 `h`의 모든 undirected edge를 endpoint-sort한 뒤 전체 lexicographic order로 정렬하고, midpoint ID를 `2049 + zero-based edge rank`로 부여해 한 번 균일 1→4 refinement한다. 원래 H1 boundary edge의 midpoint만 원 반지름에 radial projection한다. triangle은 CCW이며 original triangle마다 네 child의 순서를 고정한다.

| mesh field | frozen value |
|---|---:|
| nodes / edges / triangles / boundary edges | `8065 / 23936 / 15872 / 256` |
| interior / boundary nodes | `7809 / 256` |
| Euler `V-E+T` | `1` |
| min / max triangle area | `7.33739026502518e-15 / 1.185300461184094e-13 m²` |
| max element `κ2` | `40.83373742906414` |
| `16u κ2`, `u=2^-53` | `7.253528876039209e-14` |
| manifest SHA-256 | `34eb4f9cadcefd0b20cff3ae6c483dbee4e412ca11d0ff1a8c2c6f49ec7896a9` |

H1 cyclic parent tag 1,920개를 parent-sort하고 각 parent `[p0,p1]`의 midpoint `m`으로 `[p0,m]`, `[m,p1]` child를 만든다. 총 3,840 candidate 중 rings 1–14에서는 두 child 모두, outer band ring 15에서는 homothetic inner child만 canonical zero-coupling tag로 채택한다. 원 boundary midpoint projection을 포함한 outer child 128개는 cyclic이 아니므로 명시적으로 제외한다. magnitude search로 tag를 발견하는 것은 금지한다.

| lineage payload | count | SHA-256 |
|---|---:|---|
| parent→midpoint→children mapping | `1920` | `ed2360d1fccc80fea4b5ea8486454d5dcec2a2d11b5b5a94f443f9b9cb6ea97e` |
| all child candidates | `3840` | `dcfe48a3a7084da1486c5110236ba340b5ce46d88142594b9b170b1962352e7c` |
| excluded projected outer children | `128` | `08e40fe370ef2bac1a17a41f06bbe703d35582c6ab1f804e682495375acdbe0e` |
| canonical cyclic tags | `3712` | `287feee5d6fb4299895455b870de0eda484e07617cbc6db43bddd0d11b5b5968` |

pair-list와 mapping은 `sort_keys=true`, compact separators, UTF-8/no BOM/no newline canonical JSON으로 hash한다. `Γ`/interior index는 ascending ID의 contiguous little-endian signed int64 raw bytes로 별도 hash한다.

## Assembly certificate

P1 `K/M`와 boundary `MΓ`는 H1과 같은 raw element 식을 사용한다. canonicalization gate의 local stiffness는 `np.linalg.det`가 아니라 fixture에 적힌 explicit 2×2 determinant evaluation order를 authoritative arithmetic으로 사용한다. 다른 evaluation order에서 얻은 `3.607219158481519e-13`은 gate eligible 값이 아니다.

| assembly | nnz | SHA-256 |
|---|---:|---|
| raw `K` | `55937` | `ba0589aff655f2f4c39eac20b361446556d5c8ee65f0d0154e984574e961eb2c` |
| canonical `K` | `48513` | `8fcbbc2bd18c9ba098382cb61c45bf6880cb47886ed2578ee48def0f7a34eef9` |
| consistent volume `M` | `55937` | `69aefda6d301e9c7174aae8d1d557439749d288ddd57301291c21c7da5f581fc` |
| boundary trace `MΓ` | `768` | `2de9980d426f88a1422588bae3bbd57cba2e2f7eccc216e2509e7a83e62ca45b` |

| support / partition | SHA-256 |
|---|---|
| full/raw directed support | `0da930b2a7cda09619fa64b631dfd469a49bf22dd8507cb2602ce866ea3b055a` |
| canonical directed support | `1c70062f84c4c8d75c64a02b50b5b0e2fa8167efee817350c4e9b460d2f4e756` |
| boundary-edge list | `44fe10a4bf79cb64617828d9608cb647bdaef8e7212baa4af42beb364339414d` |
| `MΓ` support | `40252a4b29b83d51f54faf94cb7d9b9c12c4984e613898754b498af9decd1ed6` |
| `Γ` index bytes | `7c2b7c2553066861d448488c00aa5bc83bf451daff059b3841d12cdb829c1b44` |
| interior index bytes | `b55abc01b1d350be6105d6b2fbc5aaf51141570dd2ec3d9164838aaa8a55cf78` |

Canonical tagged edge는 incident triangle이 정확히 둘이어야 하며 다음 local cancellation gate를 통과해야 한다.

```text
ratio = abs(k1+k2)/(abs(k1)+abs(k2))
ratio <= 128*u*kappa2,max
```

| certificate field | frozen value |
|---|---:|
| cancellation bound | `5.802823100831367e-13` |
| maximum tagged ratio | `3.6286352763558246e-13` |
| bound / maximum margin | `1.5991750779260026` |
| absolute / relative slack | `2.1741878244755427e-13 / 0.374677598592321` |
| minimum untagged two-triangle ratio | `0.19705186275542091` |
| nonzero tagged stored values | `3712 / 3712` |
| tagged stored absolute min / max | `1.8189894035458565e-12 / 7.088601705618203e-9` |
| raw / canonical constant-null relative | `1.5137986906722262e-16 / 1.616955055742965e-16` |
| raw / canonical transpose relative | `0 / 0` |
| correction relative Frobenius | `1.8326181103987503e-16` |

각 tagged raw symmetric residue `k=Kij=Kji`는 binary64 raw `K`에서 nonzero로 보존된다. 이후 canonicalization만 H1과 같이 off-diagonal을 `Kij=Kji=0`으로 만들고 `Kii+=k`, `Kjj+=k`로 edge-Laplacian residue를 두 diagonal에 이전한다. 이는 row sum을 보존하는 topology-owned correction이며 raw matrix의 사후 평균 대칭화가 아니다. 모든 untagged two-triangle edge가 cancellation bound보다 큰지도 별도 검사한다.

## Resource preflight scope

P0는 factor를 만들지 않는다. 아래 값은 H2-P1 전에 사용하는 conservative arithmetic preflight이며 측정된 factor fill, process-tree peak 또는 8 GB proof가 아니다.

| resource term | bytes |
|---|---:|
| canonical `K` + `M` + `MΓ` sparse base | `1,328,172` |
| `16×` sparse copy allowance | `21,250,752` |
| one-factor dense upper bound | `1,951,375,392` |
| two interior extensions | `63,971,328` |
| boundary dense work | `3,145,728` |
| batch-4 RHS work | `1,999,104` |
| raw total | `2,043,070,476` |
| total with 25% margin | `2,553,838,095` |

계산 method ID는 `h2_p0_dense_factor_upper_plus_sparse_and_rectangular_25pct`다. 값은 4 GiB tree-WS ceiling 아래지만 executable H2-P1에서는 one sparse factor resident, batch `<=4`, tree WS `4 GiB`, private/commit `5 GiB`, system headroom과 별도 wall cap을 다시 사전 등록하고 실제 child tree를 측정해야 한다.

## Bounded reproduction

```powershell
python -m pytest -q tests/test_research_av_bs1_boundary_schur_h2.py
# 6 passed; physics solve 없음

$errors=$null; $tokens=$null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path tools/research/run_av_bs1_h2_stage.ps1),
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File tools/research/run_av_bs1_h2_stage.ps1 -Stage manifest
```

runner의 `ValidateSet`과 Python parser는 `manifest`만 허용한다. `primary-h2`를 전달하면 argparse가 실행 경로에 들어가기 전에 exit 2로 거부한다. P0에는 review token file, authorization parser, LU/factor call, finalizer 또는 physics artifact가 없다.

## Exact next starting point

1. 이 P0 clean commit, independently audited exact bytes와 payload를 H2 실행 계약의 immutable input으로 보존한다.
2. 별도 H2-P1 fixture/runner/result schema를 만든다. P1은 H1 artifact file/payload/numerical/mode-view hashes와 consumed H1 tombstone, 이 P0 commit/manifest/assembly/resource certificate를 모두 결합해야 한다.
3. P1은 `primary-h2` 한 stage만 열고 900 s wall stop, existing 4/5 GiB process-tree stops, one-factor residency, batch `4`, canonical failure/exit binding과 one-use review token lifecycle을 결과 전에 고정한다.
4. P1 static tests와 세 독립 review 뒤 clean preregistration commit이 생기기 전에는 h2 factorization이나 physics solve를 실행하지 않는다.
5. h2가 stage-evaluable gate와 `h→h2` signed-`M9` trend를 통과해도 fine/mesh/final pass는 계속 `null`이며, 별도 H4 preregistration 전에는 h4를 실행하지 않는다.
