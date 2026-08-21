# SPD Decap PI Evaluator v0.22.0 — AV-BS1 H4-P0R factor-pilot preregistration

## Status and scope

This document freezes the **contract-only, manifest-only** precursor to the
`AV-BS1-CIRCLE-PRIMARY` H4-P0R factorization pilot.  Its current status is
`preregistered_H4_P0R_contract_only_no_factor` with
`authorization_state=not_authorized`.

The current fixture reconstructs the frozen H4 matrices and their manual
equilibration hashes.  It does **not** import or call `splu`, create a factor,
form an RHS, call a solve, estimate an inverse norm, construct an extension or
boundary Schur operator, evaluate a mode, or report physics.  The PowerShell
runner exposes only `manifest`.

H4-P0R exists to answer one bounded resource question before H4 physics:

> Can the frozen `A_background,II` and `A_conductor,II` matrices each be
> factorized, one at a time, under the existing process-tree limits?

Even a successful H4-P0R pilot is factor-fit evidence only.  It cannot set an
H4 accuracy gate, authorize H4-P1, or change the H2 stage-only result.

## Immutable parents

H4-P0R is a descendant of research commit
`8f40fe5696496edb2cb73086927f833ded5e0d5e`.  The manifest verifies that this
commit is an ancestor and that both its Git blobs and current working bytes
match:

| H4-P0 object | SHA-256 |
|---|---|
| fixture | `331218882d2004d0d97e03378ae8af12b23cb4129a9c062e0592ee590a53e94b` |
| manifest-only runner | `b45c907fb5300e46717f423c8512a3500c7db8b42b647101d64a8a11440116c9` |
| static test | `1539ef4151b8ea416c9ee2f2bf71c1baebd4e83bb2d3684e050437fcb1def40c` |
| preregistration document | `419dfb85ff40a43f2a0c2b1143b8531b16c95402f0c0d454764cfd3f5c03e524` |
| manifest payload | `71f8e902322016541bd9302231fa9965d7dfff67cdce1135e16ee1011a2aa990` |
| mesh subpayload | `4b2463c26b6e0cb3b0452b06a4ee6e372eed531f97740d1c908c9f734795f26d` |
| partition subpayload | `bb45ac79f5e011bcb6fcedec7061695edf4525e0721caae7860ccd8c3507cb87` |
| lineage subpayload | `ba21c8ba262488e8bb928cf6493fda8021bb5a2a4a0d5e8e1598f9f26436b3cb` |
| assembly subpayload | `e9511977d30db8c4cf7ec2961a6df30d3f381fe45d651ec51a6c68e038eb311a` |
| resource subpayload | `292e4da8d7970a0c8f35c0a4dd97fe330f032a9db58e510920cc4c31434c8a9b` |

The tracked consumed H2 token is also checked.  It must retain
`authorization_state=consumed`, `uses_remaining=0`,
`next_stage_authorized=false`, and bind the following successful H2 evidence:

| H2 evidence | SHA-256 |
|---|---|
| result file | `b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55` |
| result payload | `5704f3feb5bb90b6e38f8ed3ec67fc690339e9b7f0c1722d40a977295e82593e` |
| numerical payload | `bd2f6542a93e3a7adc62f4435540752651ddf1cff84226319b4aa5e8dbdc0be5` |
| resource report | `b17468d7383ed5021a783ade4c3b7c1c5e21628580d5f6c298ef1b7b97b26bd2` |
| consumed tombstone file | `81574c1099bdd140004940b7cb768a20298b153445c1b16b9e21de95011c48c2` |

The ignored H2 result file is not required merely to reproduce this
manifest.  A future execution token must require and hash it before claim.

## Frozen matrix inputs

Runtime remains CPython `3.12.10`, NumPy `2.4.4`, SciPy `1.18.0`, Windows
`win32/AMD64`.  The geometry/material point is the primary circle at 100 kHz,
radius 17.5 µm, `sigma=59.6e6 S/m`, `exp(+j omega t)`.  The H4 interior has
31,489 nodes.

The manifest reconstructs the H4 topology, 7,424-tag canonical correction,
volume `K/M`, and partition before slicing.  It then freezes:

| Matrix | Shape | nnz | sparse SHA-256 |
|---|---:|---:|---|
| real `KII` | `31489 x 31489` | 204,545 | `dddee397ee96ff0bd7acc321cd5755dd611f1e3d1f6bfcf9664c388d27184361` |
| real `MII` | `31489 x 31489` | 219,393 | `4dccc26f1a12bbab4a4ee863e509d317cb518075661867da4e0b6980f7dcc6c4` |
| complex `AbII=KII+0j` | `31489 x 31489` | 204,545 | `8c099d2cb5947d1b72bd74f64329879c221b86c569b4100baa47dfea9aeff641` |
| `ApII=KII+j*omega*sigma*MII` | `31489 x 31489` | 219,393 | `5ead3bbdc2fc1b4f7a1a94bb7c9ebf9401c6a7d96ee63335045d64e3b082225f` |

Here `omega=628318.5307179586 rad/s` and
`omega*sigma=37447784430790.33`.  All four nonconjugate-transpose residuals
are exactly zero in the frozen runtime.

The future pilot must use the H1-compatible manual scaling
`E=Dr*A*Dc`: invert each raw row maximum, then invert each row-scaled column
maximum.  Empty or nonfinite rows/columns fail before factorization.

| Input | row-scale SHA | column-scale SHA | equilibrated SHA |
|---|---|---|---|
| `AbII` | `13565a65f7c443cb5aadb9dc01cfd0b9e0c7d71bd5b92d4f62e8a2723120732e` | `879a3dfdcde792239d2d2ad235c36e96c02eeb75f989c45adcaf3eee50cbc633` | `9216cc8938d9efc1e531a4fb301aca45547fad55f01dec57863986622327dd74` |
| `ApII` | `2e7a7b1c7d1bc2b51d33efd4185a2214422aaea0530d3483348c53422311987d` | `0a531cd1ded535c412bb9151298e108a429bc82e29b96f665d8ba1448acba3f4` | `b4efc2d388b2fe5c3280e8481d5afedd497952da3f18b34a86674a7ce97daf54` |

The equilibrated matrices retain 204,545 and 219,393 nonzeros.  Every row
and column maximum is exactly one.  The full matrix-contract SHA-256 is
`89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46`.

## Factorization-only algorithm

The future executable P0R implementation must use exactly this sequence:

1. reconstruct and verify all parent, H2, H4, input, and equilibration hashes;
2. form complex128 equilibrated `AbII`;
3. call `splu(Eb, permc_spec="COLAMD", diag_pivot_thresh=1.0,
   options={"Equil": False})` once;
4. record the factor certificate, then delete the factor, input matrix, and
   scales and call `gc.collect()`;
5. form and factor complex128 equilibrated `ApII` with the same fixed options;
6. record and delete it; do not create any RHS or call `factor.solve`.

Ordering trials, fallback permutations, parameter tuning, and retention of two
factors are forbidden.  `onenormest` is also forbidden because it calls the
factor solve path.

Each factor certificate must record:

- `L.nnz`, `U.nnz`, fill ratio, and canonical sparse SHA-256 of `L` and `U`;
- `perm_r` and `perm_c` SHA-256 plus exact permutation/bijection checks;
- finite, nonzero `U` diagonal checks;
- exact exported CSC/permutation array bytes;
- portable structural bytes
  `24*(L_nnz+U_nnz)+8*(4*n+2)`;
- factor wall seconds and the resource-sample interval covering it;
- proof that no other factor was resident.

The per-factor size gate is
`max(exact_exported_bytes, portable_structural_bytes) <= 2,147,483,648 B`.
SuperLU internal workspace is not inferred from those arrays; the process-tree
monitor owns that evidence.

## Resource contract

The inherited dense diagnostic remains failed and is not used to authorize
execution.  P0R retains the more conservative full prospective H4 envelope
rather than weakening it to the smaller no-RHS pilot estimate.

| Gate | Frozen value |
|---|---:|
| process-tree working set stop | 4,294,967,296 B |
| process-tree private stop | 5,368,709,120 B |
| process-tree commit stop | 5,368,709,120 B |
| wall stop | 900 s |
| execution commit-headroom floor | 2,147,483,648 B |
| execution available-physical floor | 1,610,612,736 B |
| pre-spawn available physical minimum | 5,081,474,791 B |
| pre-spawn commit headroom minimum | 5,618,345,703 B |
| one-factor hard cap | 2,147,483,648 B |
| P0R no-RHS estimate with 25% margin | 2,800,162,215 B |
| retained full prospective P0 envelope | 3,470,862,055 B |
| retained 4 GiB slack | 824,105,241 B |

The runner must include itself and every descendant in tree accounting, obtain
at least one successful sample, preserve peak working set/private/commit/page
fault/nonprivate-mapped proxies, recheck system floors, and verify orphan
termination.  Any monitor failure, missing sample, wall/resource stop, PID
reuse ambiguity, or incomplete cleanup fails closed.

## Schemas and one-use lifecycle

The manifest freezes these future schema names:

- `AV-BS1-h4-p0r-review-token-v1`
- `AV-BS1-h4-p0r-token-claim-v1`
- `AV-BS1-h4-p0r-resource-guard-v1`
- `AV-BS1-h4-p0r-factor-report-v1`
- `AV-BS1-h4-p0r-resource-report-v1`
- `AV-BS1-h4-p0r-result-v1`
- `AV-BS1-h4-p0r-failure-v1`
- `AV-BS1-h4-p0r-consumed-review-token-v1`

The executable implementation and token are **not present yet**.  Before any
factor is created, a new isolated implementation must pass independent Sol,
Terra, and Luna audits, be committed cleanly, and receive a tracked token with
`uses_remaining=1`, exact stage `primary-h4-p0r`, exact file/commit/parent
bindings, expiry, reviewer evidence, and `next_stage_authorized=false`.

After validation and immediately before child spawn, the runner atomically
creates an exclusive claim.  Every launched-child outcome—pass, factor error,
resource stop, malformed output, runner/finalizer failure—must atomically
replace the active token with a consumed tombstone (`uses_remaining=0`).  A
consumed token or existing claim rejects replay.  Claim, guard, child stdout,
resource report, result, original token, and tombstone hashes must cross-bind.

The only allowed failure codes are:

- `BLOCKED_AV_BS_MESH_HASH`
- `BLOCKED_AV_BS_FACTOR`
- `BLOCKED_AV_BS_RESOURCE`
- `BLOCKED_AV_BS_RESULT_SCHEMA`

A valid pass status is
`passed_AV_BS_h4_p0r_factor_only_pending_h4_p1_preregistration`, always with
`physics_solve_performed=false`, `next_stage_authorized=false`, and no accuracy
fields.

## Explicitly forbidden output

P0R must emit no RHS, `I-Gamma`/`Gamma-I`/`Gamma-Gamma` extension block,
harmonic extension, `Y`/DtN/Schur operator, M9/modal field, PDE residual,
power, reciprocity/passivity result, analytic comparison, h2-to-h4 convergence,
fine or final-circle gate, withheld-radius result, EQ0 result, product change,
or PowerSI/8 GB promotion.

The finalizer must reject extra fields or booleans suggesting any such work.
`factorization_performed=true` is permitted only in the eventual P0R result;
`physics_solve_performed` must remain false on every path.

## Static reproduction and exact next step

The current safe commands are:

```powershell
python tools/research/av_bs1_boundary_schur_h4_p0r.py --stage manifest
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/research/run_av_bs1_h4_p0r_stage.ps1 -Stage manifest
python -m pytest -q tests/test_research_av_bs1_boundary_schur_h4_p0r.py
```

The static suite must check parent Git blobs, H2 tombstone provenance, all
matrix/equilibration hashes, resource arithmetic, manifest-only AST and
PowerShell stage surface, failure serialization, tamper rejection, UTF-8/LF,
and the complete forbidden-operation set.  The frozen manifest payload SHA-256
is `eaab10df7fb1557490cc75db7e7ff9fca2881013b6a6fb13f02faea43ddf7023`.

The exact next step after this contract is committed and independently audited
is a **separate** H4-P0R executable fixture/runner/test/token family.  No factor
may be created while that implementation or token is absent.  Only an audited
and consumed factor-only result may inform a later, separately preregistered
H4-P1 physics candidate.
