# SPD Decap PI Evaluator v0.22.0 — AV-BS1 H4-P0 assembly preregistration

## Current scope and status

This file preregisters the **assembly-only** H4-P0 stage for `AV-BS1-CIRCLE-PRIMARY`.
The stage deterministically refines the frozen H2 mesh, assembles sparse P1
`K/M/MΓ`, and validates an H4-specific cyclic-edge certificate. It does not
factor a matrix, solve a PDE, form a boundary operator, issue a review token,
or authorize `primary-h4`.

Until these exact bytes are independently reviewed and committed from a clean
checkout, the working-tree status is
`candidate_preregistered_H4_P0_assembly_only_no_solve`. The fixture payload
itself uses `preregistered_H4_P0_assembly_only_no_solve`, always reports
`authorization_state=not_authorized`, and exposes only `--stage manifest`.

Frozen dependencies:

| item | SHA-256 |
|---|---|
| H2-P0 fixture | `032100623fca51ab22a48493b46f23bc8ce5fd1250203d527062671d47599384` |
| H2-P0 runner | `6ce0002e9d3790542008d9e1e608168f1e40f51d56c9a8ff1d37003a15f8feb7` |
| H2-P0 manifest payload | `68d2e20a471e0e9475dd8e575ffd1e246f4098bb6c0e2b688d0f6f702fb511ba` |

The H2 physics artifact and consumed H2 token are not inputs to assembly P0;
any later H4 execution token must bind both separately.

## Deterministic H2 to H4 refinement

Starting from the frozen H2 topology:

1. Sort every undirected H2 edge lexicographically.
2. Assign midpoint ID `8065 + zero_based_edge_ordinal`.
3. Average the two endpoint coordinates in binary64.
4. Radially project only boundary-edge midpoints to `a=17.5 µm`.
5. For each CCW parent `(a,b,c)`, emit children in this order:
   `(a,mab,mca)`, `(b,mbc,mab)`, `(c,mca,mbc)`, `(mab,mbc,mca)`.
6. Recheck every child orientation as CCW.

The manifest serialization is exactly the existing `AV-BS1-CIRCLE manifest=v1`
contract: UTF-8/LF/no trailing newline, full header with `subdivide=4`, binary64
`float.hex()` coordinates, node role (`center/boundary/interior`), CCW triangle
rows, and sorted boundary-edge rows.

| metric | H4 freeze |
|---|---:|
| nodes / edges / triangles | `32001 / 95488 / 63488` |
| boundary / interior nodes | `512 / 31489` |
| Euler characteristic | `1` |
| minimum / maximum area | `1.834347566256282e-15 / 2.976516054282241e-14 m²` |
| maximum element `κ₂` | `40.83373742906445` |
| `16uκ₂`, `u=2^-53` | `7.253528876039263e-14` |
| manifest SHA-256 | `a91b4bf147628a34d1a29144ae353a83110b1756c699c71e2b57e9c36822835b` |

Partition hashes:

- boundary `Γ` ascending `<i8`: `749dbbb6b7f58565c757a1042dd9344f165c35afb23e77022222e21ba260c0b9`
- interior ascending `<i8`: `b91ec9e63cc180c30daf3b61c6789b221ad9429ae9cc6bfc6d0b1d51e07e205c`
- sorted boundary-edge list: `2453b9035480c5c13925b8e156d42fd7c2769f1b7e7b71f8aa1c6ae7ec1f66e7`

## Cyclic-edge ownership and the H4 bound amendment

Tags are topology-derived, never found by a numerical threshold. Each of the
3,712 frozen H2 canonical zero edges owns its two midpoint children. No parent
or child touches the H2 boundary-node set, so H4 has no projected-boundary
exclusion.

| lineage item | count | SHA-256 |
|---|---:|---|
| H2 parents | `3712` | inherited H2 tag set |
| H4 candidates | `7424` | `3902a43ddd16f2cfce16b2892b908f45d02f4464c5157a5c42b5b3ac5cb9d98d` |
| parent/midpoint/children map | `3712` | `561059787bc8b48a9e9c5963c69a3cceed2278779a22bf8ac37f619cee977b97` |
| exclusions | `0` | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| canonical H4 tags | `7424` | `3902a43ddd16f2cfce16b2892b908f45d02f4464c5157a5c42b5b3ac5cb9d98d` |

Every tag has exactly two incident triangles. Uniform midpoint refinement
preserves the two opposite angles of the parent cyclic quadrilateral, so the
zero cotangent weight is inherited by both children.

The H2 arithmetic certificate `128uκ₂` is **not** copied silently. At H4 the
normative explicit P1 determinant evaluation gives:

| certificate | value |
|---|---:|
| maximum tagged cancellation ratio | `8.540375354048666e-13` |
| inherited `128uκ₂` | `5.802823100831411e-13` — fail, 16 tags exceed |
| H4-specific `256uκ₂` | `1.1605646201662821e-12` — pass |
| bound / maximum margin | `1.358915237391882` |
| absolute / relative slack | `3.0652708476141557e-13 / 0.26411892921352137` |
| minimum untagged two-triangle ratio | `0.19705186275527375` |

The extra factor of two accounts for one additional midpoint-arithmetic level.
It certifies only the frozen topology-owned tags; it never authorizes searching
for or removing an untagged coupling.

For each tagged raw symmetric residue `k=Kij=Kji`, canonicalization sets the
off-diagonal pair to zero and adds `k` to both corresponding diagonal entries,
preserving symmetry and the constant row-sum null. All 7,424 raw tagged CSC
values are nonzero in binary64; their absolute range is
`1.8189894035458565e-12 ... 1.6683770809322596e-08`.

## Sparse assembly freeze

| operator | nnz | SHA-256 |
|---|---:|---|
| raw `K` | `222977` | `8a020c809634a9794292f49198ff1bede84328b6e2c5ef988255cbff32cfe93b` |
| canonical `K` | `208129` | `a510df2ab39cb85640720f863341d1fe468562442ecb074bafcaf70d9846f2e7` |
| volume `M` | `222977` | `66bc7da7edfae88a53220ce553c4b8ab0cea51b5222d354139d377c4d34c05ac` |
| trace `MΓ` | `1536` | `cac37897c0c7910ae64ba15c3307ad2ba7bf109f7832a0b692ed02bb5ff8771d` |

Support hashes:

- full P1 support: `bae1059a74cd662c6bac6e027beb8b54f1df865693939735dc1247a161c23240`
- canonical `K` support: `4416afcc176bce3cda2a18e3f73a53c2b08c21d270cc3cff1c78293a7450fa52`
- trace support: `db4112208523ce834f03fc9b10f27595fd17f3f7ce3b06c831ce30f5bc6a3b59`

Partitioned nnz are:

| matrix | `II` | `IΓ` | `ΓI` | `ΓΓ` |
|---|---:|---:|---:|---:|
| canonical `K` | `204545` | `1024` | `1024` | `1536` |
| `M` | `219393` | `1024` | `1024` | `1536` |

Raw/canonical transpose residuals are zero; raw/canonical constant-null
residuals are both `2.2860092193173166e-16`; the canonical correction has
relative Frobenius norm `2.556936522664663e-16`.

## Resource envelopes

H4-P0 freezes two deliberately distinct calculations. Neither authorizes a
solve.

The inherited all-dense factor upper is retained as a diagnostic and fails the
4 GiB tree-working-set ceiling:

| dense diagnostic component | bytes |
|---|---:|
| one factor dense upper | `31729827872` |
| raw total | `32359033868` |
| total with 25% margin | `40448792335` |
| `primary_h4_resource_preflight_pass` | `false` |

A prospective guarded sparse envelope substitutes a **hard cap**, not an
estimate or a pass claim, of 2 GiB for the one resident factor:

| guarded component | bytes |
|---|---:|
| canonical `K + M + MΓ` sparse base | `5449772` |
| 16× sparse-copy allowance | `87196352` |
| one-factor hard cap | `2147483648` |
| two interior extensions | `515915776` |
| boundary dense work | `12582912` |
| batch-4 RHS work | `8061184` |
| raw total | `2776689644` |
| total with 25% margin | `3470862055` |
| slack below 4 GiB WS stop | `824105241` |

The candidate envelope fits arithmetically, but `factor_fit_unproven=true` and
`primary_h4_authorized=false`. Any later resource decision requires a separate
preregistration that retains one-factor residency, batch `<=4`, no dense
interior materialization, process-tree monitoring, WS/private/commit stops of
`4/5/5 GiB`, and pre-spawn available-physical/commit-headroom minima of
`5081474791 / 5618345703 B`.

## Manifest command and exact next step

Only this command is exposed:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/research/run_av_bs1_h4_p0_stage.ps1 -Stage manifest
```

Before any H4 factorization or physics:

1. run the H4-P0 static suite and independent assembly replay;
2. commit the fixture, manifest-only runner, tests, and this document cleanly;
3. bind their exact hashes in a separate reviewed H4 resource/execution contract;
4. issue a distinct one-use H4 token only after that review.

No H4 result, h2-to-h4 convergence pass, fine analytic pass, final-circle pass,
withheld-radius pass, EQ0 pass, or product-solver validation exists at P0.
