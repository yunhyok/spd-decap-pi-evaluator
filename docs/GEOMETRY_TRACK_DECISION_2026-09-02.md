# SPD Decap PI Evaluator v0.23.1 — Geometry track decision

Status: `STOP_IMPLEMENTATION_PENDING_CROSS_ARTIFACT_AND_SIZE_CONTRACT`

## Decision

Do not reread the original SPD and do not rerun the ownership compiler. The shortest
source-derived path is to reuse the two exact geometry assets already stored in the
17DT `.spdpi` bundle, bind them to the D-096 candidate by deterministic island ID,
and take stackup/material/Dk/Df provenance directly from the frozen D-096 report.

No product code was changed because two policy choices are not yet authorized:

1. The 17DT bundle and D-096 have the same source SHA-256 and the exact same selected
   island IDs, but their raw/project/topology identities differ. Accepting the
   deterministic island ID as the cross-artifact geometry join is a new trust
   contract.
2. Exact normalized WKB is larger than the existing fringe-manifest limits:
   `island_g.wkb` is 2,040,201 bytes, `g_only.wkb` is 2,173,032 bytes, and all five
   files total 4,957,709 bytes. The current gate allows 1,048,576 bytes per file and
   4,194,304 bytes total. Do not silently crop, simplify, split, or raise those caps.

## Read-only evidence

- Bundle:
  `D:\SPD-Decap-PI-Evaluator-W7\2928ca73ffa0d0d1421cd393939b6fea1d025f42\260729-17dt-raw-spatial-v3\S4LB002-2Para_260729_1_injected_candidate.spdpi`
  — 911,542,390 bytes, SHA-256
  `fbe6abeb5655918134ecb47235edfd3b81b891ed5ec95545e03c9651937c6bcc`.
- D-096 report:
  `D:\SPD-Decap-PI-Evaluator-W7\23e5d3c6b43064b8fd805c234da5f0ccc86b6d4f\260729-a2-d096-source-block-census-01\source_block_census_report.json`
  — SHA-256
  `bb2ad70bbbb5e39af6a673d29adb2e5543675e680d68491cf906aabc2c16f473`.
- D-101 observation:
  `D:\SPD-Decap-PI-Evaluator-W7\a5dea56b6fd5fe8eb345420dddb16a88c10bc224\260729-d101-source-plane-ownership-reproduction-fringe-01\source_plane_ownership_reproduction_observation.json`
  — SHA-256
  `1b4699e6f067329edcbf397f776afb618dc91d9c3a66b22884de21e5721542f3`;
  it confirms the current ownership identity but contains no WKB/SQLite payload.
- Original source identity on both evidence paths:
  `40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2`.
- L29 ground asset:
  `geometry/0259-a61be7c4ebe09df8.spdgeom.zlib`, SHA-256
  `a61be7c4ebe09df8127ae5630d565b46f90d433ca88a75462aee155c0b4ee164`,
  island `spd-surface-island:2db099ba622781734a17c3e0`.
- L30 power asset:
  `geometry/0264-6d2f6403e2a809e6.spdgeom.zlib`, SHA-256
  `6d2f6403e2a809e63e28e156bb8745f06771c15231a87590f8f9d2ed2907c6e6`,
  island `spd-surface-island:cb8510a79529b7f6f4f4afd4`.

The existing source helpers decoded only those two ZIP members, reconstructed ordered
artwork, reproduced both frozen island IDs, and generated normalized WKB in 3.262 s:

| Geometry | WKB bytes | WKB SHA-256 | Area (um^2) |
|---|---:|---|---:|
| `island_p` | 258,181 | `a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5` | 361,720,074.03946954 |
| `island_g` | 2,040,201 | `a438379bf22b47e412b763eebb49c3aae7252c5872d5ac7c31513499108b9053` | 9,382,278,334.218412 |
| `overlap` | 304,977 | `6c0fd00b03314b5cd9012e08cbffd9daa23c3fe984fd009d5ec5c06d62951a55` | 349,317,409.10857165 |
| `p_only` | 181,318 | `53bc0bd1a5a1a144b6de5f6a16367dc6b0d9a1e926a36870ef361e770554233a` | 12,402,664.930898676 |
| `g_only` | 2,173,032 | `dfbb842c3111cf14de055f87e342f9748c1358b11539aac2c9c4fdb187b7a3c1` | 9,032,960,925.110016 |

D-096 already contains the exact `20 um Cu / 30 um ABF-GL102 / 20 um Cu`
rows, their source-record byte spans and hashes, and all seven Dk/Df rows. Reuse those
rows verbatim; do not reopen raw-v3 SQLite merely to restate them.

## Exact Luna handoff after approval

Create only `tools/research/extract_source_plane_fringe_geometry_reuse.py`; do not
modify product modules, schemas, solver code, P1, PowerSI, or ownership compilation.

The script should contain one `extract_geometry()` function and a small `main()`:

1. Verify the frozen bundle, D-096 report, and source identities above.
2. Read only `manifest.json` and the two named geometry ZIP members.
3. Call `spd_plane_geometry_record_payload()` and `ordered_spd_geometry()`.
4. Call `_spd_surface_islands()` and require the exact D-096 island IDs.
5. Form `intersection`, `power.difference(ground)`, and
   `ground.difference(power)`; serialize with `_geometry_manifest()`.
6. Copy D-096 `selected_stackup_rows`, `dielectric_source_rows`, and only their
   referenced `material_source_records` into the receipt.
7. Write five `.wkb` files plus one canonical JSON receipt to a new absent output
   directory, then re-read every output and verify size/SHA. Fail closed on any
   source, bundle, asset, layer, net, island, WKB, or provenance mismatch.

Required decision before Luna starts: explicitly approve both (a) the same-source plus
deterministic-island-ID cross-artifact join and (b) a gate-local exact-output allowance
of at least 4 MiB per WKB and 8 MiB total. This does not change production attachment
caps. Estimated execution is under 10 seconds, under 512 MiB, with original-SPD reads,
ownership compile, P1, solver, PowerSI, and retries all zero.
