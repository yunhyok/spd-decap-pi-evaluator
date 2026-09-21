# D117 quality-mesher backend decision

**Product:** SPD Decap PI Evaluator v0.23.1  
**Verification date:** 2026-09-04 (Asia/Seoul)  
**Scope:** technical suitability only. This is a zero-solver research decision; it does not authorize FasterCap, PowerSI, solver, aggregate, production, or shipping execution.

**Current gate:** `ACCEPT_C0 / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / C1_NOT_AUTHORIZED`.

## Decision

**CONDITIONAL SELECT = Triangle 1.6 for a separately authorized, research-only, zero-solver pilot.**

This is **REJECTED as a shipped/runtime/installer dependency** until upstream commercial-use/distribution permission, licensing, and wrapper provenance are resolved by the appropriate owner. This is not legal advice. The preferred minimal future adapter is `triangle==20250106` (the Triangle 1.6 wrapper, CPython 3.12 Windows wheel), rather than MeshPy. MeshPy bundles unused TetGen functionality and associated licensing/provenance baggage; no dependency is added now. The wrapper's PyPI LGPL metadata does not by itself resolve upstream Triangle's restrictive terms, so direct permission/arrangement and review are required.

Triangle is selected only for a bounded pilot because its primary sources document exact PSLG segments, point-defined holes, conforming constrained Delaunay refinement, quality-angle requests, and robust predicates. A pilot must prove the gates below on the real inputs before any product decision. No silent backend switch or threshold relaxation is permitted.

## Non-relaxable acceptance gates

- Postcheck minimum angle **strictly >7.5 degrees**; 6 degrees is diagnostic floor only.
- Aspect ratio <=8; finite, nonzero elements; no duplicate vertices.
- Every source segment recovered as a collinear chain; exterior and every hole preserved.
- Edge incidence/orientation, manifoldness, Euler/genus, and `volume = area * thickness` certificates.
- Clearance and non-intersection certificates.
- Deterministic canonical bytes, hashes, counts, and three clean serial replays; any mismatch is STOP.

The historical Cell264 Stage1AB/Stage1AC pilot invocation was equivalent to
`-p -q15 -C -z` (`pq15Cz`) and its historical contract said never use `-X`,
`-Y`, or `-S`; that restriction is not a Cell258 C1 contract. Any future
Cell258 C1 must use a separately HQ-reconciled explicit `S<number>` cap after
the `V`/`T` caps are settled; no `S` value is frozen here. See the [WP1
Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint)
for the source-boundary and memory details. `-D` is unnecessary. `-q15`
remains a request, not a waiver of the hard postcheck: source angles that are
too small can force quality violations and must fail closed.

Stage1AD remains an immutable diagnostic STOP after one q15 full-cell264 geometry run passed the substantive geometry/resource gates but its controller accepted only LF while the correct Windows success marker ended CRLF. Stage1AE applied the reviewed exact LF/CRLF marker allowlist in a new sealed root and completed three serial replays, all PASS with matching canonical and mesh-receipt hashes. Stage1AE is accepted as bounded q15 full-cell264 geometry-only evidence; this does not authorize a solver, production, shipping, or Triangle distribution/legal clearance. See the [Stage1AD diagnostic and CRLF STOP evidence](D117_TRIANGLE_STAGE0_STAGE1_RESULT.md#stage1ad-q15-replay-01-diagnostic-and-crlf-stop-evidence) and [Stage1AE three-replay acceptance evidence](D117_TRIANGLE_STAGE0_STAGE1_RESULT.md#stage1ae-q15-three-replay-acceptance-evidence).

## Staged no-solver validation

0. **Actual D115C W0-clipped target ordinal 264 intersection (not full D104 cell 264).** The sealed receipt is `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02\d115c_source_local_port_window_receipt.json` (1,063,056 bytes, SHA-256 `0C8DAED46719B199EE50B1EC9B94DD5CAC0FDEDECBC7B58668B7665B5E9A2A80`), status `STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT`. Its snapped bounds are conventional `[min_x,min_y,max_x,max_y]` `[-12000000000,12000000000,-11000000000,13000000000]` pm (`[-12000,12000,-11000,13000]` um). Ordinal 264 is island `spd-surface-island:cb8510a79529b7f6f4f4afd4`, layer `Signal$L30(OTHER_POWER1)`, net `ADC_VDD_180_VQPS_SYS_1_AON/0`; source `cell_0264.wkb` is 258,181 bytes, SHA-256 `a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5`; sealed clipped intersection area is 926540.7647500002 um2, bbox `[-12000.0,12000.0,-11000.0,13000.0]`, and intersection WKB is 317 bytes, SHA-256 `f3a2abe59324887bec79aaab38b670f0943cb5cf11936548343a8ef6940cad04`. The receipt seals no ring-vertex or hole counts. A newly observed (not sealed) Shapely 2.1.2 read-only intersection reproduced that WKB/hash and observed one Polygon component/exterior, 18 total ring vertices excluding duplicate closure, and 0 holes; the derived topology floor `4V+4H-4=68` cannot become a sealed receipt fact without a future receipt. The receipt has active intersections 259/262/264 overall; this stage exercises target 264 only and must not imply W0 is one full cell. This stage required all certificates and a bounded replay; this tiny intersection is not a resource predictor.
1. **Full unclipped D104 cell 264 — PASS (Stage1AE):** 15,297 ring vertices, 670 holes, and a closed all-triangle lower bound of 63,864. The accepted q15 geometry-only result passed all gates in three identical serial replays; do not extrapolate W0 caps.
2. **Cell258 Stage2A C0 — ACCEPT_C0:** the cap-02 receipt records the full unclipped D104 cell258 census: `R=149,078` raw ring vertices, `H=2,048` holes, and `S=153,246` split vertices/segments at a 140-um step. Its split-derived floors are `T=S+2H-2=157,340`, `Vclosed=306,492`, and `Fclosed=621,172`; the raw source-ring floor remains `T=153,172` / `F=604,500`. The geometry-only root and exact output identities are recorded below. No Triangle mesh, extrusion, solver, or shipping result follows; the controlled-buffer model remains incomplete and C1 is unauthorized.
3. **Future candidate only; not authorized here — aggregate 16 cells:** 811,216 boundary vertices, 31,025 holes, and a closed all-triangle lower bound of 3,368,900. Cell258 C0 acceptance is insufficient: the aggregate may be considered only after the C1 memory/resource design passes, a separately approved one-shot 2-D C1 passes, and a separately approved extrusion/replay package passes, all under the aggregate's own preregistration; still no solver.

Any failure is STOP. The 811,216/31,025 counts are the 16-cell aggregate, not either individual cell.

### Accepted Cell258 Stage2A C0 evidence

The immutable cap-02 root is
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell258-stage2a-c0-cap-02`.
The C0 JSON is **5,448,871 B / `5ae752680b63686f90dd0d80037b337bba0da71f3bd9d6d755a9e94365f9ac60`**,
text is **142 B / `b4b93f13737c460f5c76272afb614312021904f679419c1fd652fed17e226d20`**,
and the run receipt is **26,767 B / `ceba8bce28329ac858c0466c948839a87ba0d35ef4934277ef06e7d2c6ea1f57`**.
The pinned controller/helper/approval are respectively **114,544 B /
`fb9278f099eddcf363f36ee76f99eb647525a8fa6931b67546486f37a6fa4c02`**,
**40,274 B / `169d70e2d4aaf23bfdb3b9fbd209380ca77ed7ed09e09f5c89c75f32a30f3452`**,
and **9,030 B / `3d02662d59aa514bfbf64710119509e25d951b08bf4f3bb0cc6f21805378159e`**.

The receipt status is `PASS`, elapsed **2.813 s**, child exit **0**, Job
total/active **1/0**, peak commit **285,188,096 B**, observed working-set peak
**264,499,200 B**, and max observed artifact **5,612,916 B / 7 items**. It
binds `Signal$L28(DGND)` (source layer raw/ordinal **54**, conductor), island
`spd-surface-island:ea4bc44349ce103beab4dca3`, area
**8,476,333,524.145388 um2**, bbox **[-49700,-49700,49700,49700]**, one
Polygon component (exterior ring=1), **2,049 rings**, `R=149,078` (`8+149,070`), `H=2,048`, and
thickness **35.0 um**, conductivity **59,590,000**. Step **140 um** produced `S=153,246` vertices=segments,
marker count **149,078**, marker sum `S`, distribution
`{1:149070,16:3,32:1,1024:4}`, and **2,048** strict interior hole points;
edge flags were `corner=true, curve=false, edge=true`.
All Triangle/extension/module, extrusion, solver, FasterCap, and network counts
were zero. The canonical PSLG identity is **12,057,453 B /
`1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b`** with
lower-bound estimate **7,421,359 B**; bytes were not retained separately and
must be recomputed before C1. The outer wrapper's sole PID **26700** / no-retry
record is not sealed for outer exit/stdout/stderr because a read-only PowerShell
`$PID` assignment interrupted observation; do not infer those fields.

The accepted no-Triangle C1-preparation checkpoint, exact implementation/test
identities, bounded-array statuses, derived canonical cap, and remaining
prerequisites are maintained in the [WP1 Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint).
The former `STOP_C1_MEMORY_MODEL_UNPROVEN` label was refined, not cleared: the
current gate remains `ACCEPT_C0 / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / C1_NOT_AUTHORIZED`. This document does not authorize C1 or any solver.

## Compact technical comparison

| Backend | Verified technical evidence | Determinism, scale, and I/O | D117 disposition |
|---|---|---|---|
| **Triangle 1.6 / `triangle` wrapper** | `.poly` explicitly carries vertices, segments, and hole points; `-p` builds a (possibly conforming) CDT; `-q` requests a minimum angle; adaptive exact predicates are documented. **Verified:** [main](https://www.cs.cmu.edu/~quake/triangle.html), [quality](https://www.cs.cmu.edu/~quake/triangle.quality.html), [.poly](https://www.cs.cmu.edu/~quake/triangle.poly.html), [help](https://www.cs.cmu.edu/~quake/triangle.help.html), [exact](https://www.cs.cmu.edu/~quake/triangle.exact.html). | Serial C/CLI and fixed canonical input are an **inference** toward repeatability, not an upstream guarantee; enforce three replay hashes. Generation is in-memory; `.node/.ele/.poly` or arrays can be converted/exported linewise afterward, not streamed meshing. Official old evidence includes 1,000,000 random vertices and a 624,101-vertex quality mesh ([performance](https://www.cs.cmu.edu/~quake/triangle.time.html)); this is not proof for 31,025 holes/current hardware. [PyPI wrapper 20250106](https://pypi.org/project/triangle/), [upstream submodule](https://github.com/drufat/triangle/blob/master/.gitmodules). | **Conditional research pilot only; primary technical candidate.** |
| **MeshPy 2026.1.1** | Python `MeshInfo` exposes points, facets, and holes; `meshpy.triangle.build(... quality_meshing=True, min_angle=...)` wraps Triangle. **Verified:** [docs](https://documen.tician.de/meshpy/tri-tet.html#meshpy.triangle.build), [PyPI](https://pypi.org/project/meshpy/), [license](https://github.com/inducer/meshpy/blob/main/LICENSE). | In-memory `ForeignArray` ownership; no documented streaming or deterministic seed/guarantee. CPython 3.12 Windows availability is shown by current package metadata, but bundled TetGen is unused baggage. | **Do not select; use the thinner `triangle` adapter if pilot is authorized.** |
| **Gmsh 4.15.2** | Plane-surface curve loops model an exterior plus inner loops (holes); 2-D Delaunay recovery inserts/reconnects 1-D edges. **Verified:** [manual](https://gmsh.info/doc/texinfo/gmsh.html), [product/license](https://gmsh.info/), [PyPI](https://pypi.org/project/gmsh/). | Reproducibility knobs are documented: `Mesh.RandomFactor=0`, fixed `Mesh.RandomSeed`, `Mesh.Reproducible=1`, and `-nt 1`; still require hash replay. `Mesh.QualityInf/Sup` are display filters, not hard generation constraints. MSH4 supports block-wise reading, but generation is in-memory; no 811k/31k evidence. | **Reject as primary:** no documented hard minimum-angle generation gate. |
| **JIGSAW 1.1.0 / jigsawpy** | 2-D Delaunay/DelFront, topology preservation (`MESH_TOP1`), and radius-edge bound (`MESH_RAD2`) are documented; `NUMTHREAD=1` is available. **Verified:** [repo](https://github.com/dengwirda/jigsaw), [version](https://github.com/dengwirda/jigsaw/blob/master/version.txt), [Python setup](https://github.com/dengwirda/jigsaw-python/blob/master/setup.py). | Quality is radius-edge, not a direct hard angle; no random-seed/reproducibility guarantee. Inner-loop/hole semantics, CPython 3.12 Windows wheel, streaming, and 811k/31k behavior are **unknown**; setup indicates source/external build burden. | **Reject as primary:** unresolved hole semantics and hard-angle/determinism gaps. |
| **Netgen/NGSolve 6.2.2606** | Spline/CSG geometry can express domains and subtracted holes. **Verified:** [Netgen repo/license](https://github.com/NGSolve/netgen), [Netgen PyPI](https://pypi.org/project/netgen-mesher/), [NGSolve PyPI](https://pypi.org/project/ngsolve/), [2-D geometry](https://ngsolve.org/ngsolve/docs/i-tutorials/unit-4.1.1-geom2d/geom2d.html). | Windows/Python wheels exist, but no documented hard min-angle criterion, exact PSLG constraint-recovery contract, deterministic controls, streaming, or D117-scale evidence. | **Reject.** |
| **CGAL Mesh_2 6.2** | Constrained Delaunay triangulation, seeded in-domain components/holes, and `refine_Delaunay_mesh_2` are documented. Shape criterion is radius/shortest-edge `B`; `B = sqrt(2)` gives the documented ~20.7° termination guarantee. An 8° request is looser (`B ≈ 3.59`) and is within that regime when input-angle assumptions hold; source incident angles can still force local violations. **Verified:** [Mesh_2 manual](https://doc.cgal.org/latest/Mesh_2/index.html), [criteria](https://doc.cgal.org/latest/Mesh_2/classMeshingCriteria__2.html), [functions](https://doc.cgal.org/latest/Mesh_2/group__PkgMesh2Functions.html), [package list](https://doc.cgal.org/latest/Manual/packages.html), [release](https://github.com/CGAL/cgal/releases), [license](https://www.cgal.org/license.html). | No official Python 3.12/Windows wheel or standalone CLI for Mesh_2; C++ template build and memory/streaming behavior at D117 scale are **unknown**. Deterministic replay controls are not documented. | **Technical fallback only, conditional on a separately authorized C++ pilot; not shipped.** |
| **Existing Shapely/SciPy baseline** | Shapely constrained Delaunay preserves polygon edges but has no quality refinement; SciPy uses Qhull Delaunay, and Qhull explicitly lacks constrained Delaunay. **Verified:** [Shapely](https://shapely.readthedocs.io/en/2.1.2/reference/shapely.constrained_delaunay_triangles.html), [SciPy](https://scipy.github.io/devdocs/tutorial/spatial.html), [Qhull](https://www.qhull.org/). | Therefore a true constrained-quality backend evaluation is technically justified; adding one before the pilot proves all gates is not. | **Baseline only; not sufficient for D117.** |

## License and delivery boundary

Official sources checked 2026-09-03. This classification is technical delivery guidance, **not legal advice**.

| Backend/package | Official licensing and delivery classification | Technical delivery status |
|---|---|---|
| **Triangle 1.6 upstream** | Free for private, research, and institutional use; commercial-system distribution requires direct arrangement with the author. Sources: [Triangle main/license notice](https://www.cs.cmu.edu/~quake/triangle.html) and [fuller reproduced notice](https://github.com/inducer/meshpy/blob/main/LICENSE). | Research-only pilot; shipped proprietary dependency rejected pending permission/provenance review. |
| **`triangle==20250106`** | PyPI metadata says LGPL, but the wrapper embeds/references upstream Triangle; that metadata conflict does not clear the upstream commercial restriction. A CPython 3.12 Windows x86-64 wheel exists. Sources: [PyPI](https://pypi.org/project/triangle/), [wrapper LICENSE](https://github.com/drufat/triangle/blob/master/LICENSE), and [upstream-reference submodule](https://github.com/drufat/triangle/blob/master/.gitmodules). | Research-only candidate; shipped proprietary dependency rejected pending permission/provenance review. |
| **MeshPy 2026.1.1** | Wrapper is MIT, but the same [LICENSE](https://github.com/inducer/meshpy/blob/main/LICENSE) includes the bundled Triangle restriction and unused TetGen AGPLv3-or-commercial terms. A CPython 3.12 Windows wheel exists. | Do not choose. |
| **Gmsh 4.15.2** | GPLv2+ with an exception for external-library linking; the [official page](https://gmsh.info/) explicitly says the public version cannot be integrated into distributed closed-source software. Windows binary/SDK and a PyPI Windows x64 Python 2/3 wheel exist. | Research/external tool possible; shipped integration rejected absent a separate license path. Sources: [product/license](https://gmsh.info/), [PyPI](https://pypi.org/project/gmsh/). |
| **JIGSAW 1.1.0** | The [official repository](https://github.com/dengwirda/jigsaw) and its [LICENSE.md](https://github.com/dengwirda/jigsaw/blob/master/LICENSE.md) state custom terms: private, research, and institutional use is free; commercial-system distribution requires direct arrangement with the author. Source/CLI/C++ and `jigsaw-python` setup are available, but no official CPython 3.12 Windows wheel was identified. | Reject shipped; research use remains technically conditional. |
| **Netgen/NGSolve 6.2.2606** | Netgen's [official LICENSE](https://github.com/NGSolve/netgen/blob/master/LICENSE) is LGPL-2.1. Both [netgen-mesher](https://pypi.org/project/netgen-mesher/) and [ngsolve](https://pypi.org/project/ngsolve/) have CPython 3.12 Windows x86-64 wheels. Proprietary distribution may be possible only with LGPL compliance/product legal review; this is not automatic approval. | Technically rejected for D117; no delivery decision inferred. |
| **CGAL Mesh_2 6.2** | The [package list](https://doc.cgal.org/latest/Manual/packages.html) marks Mesh_2 GPL; public-license distribution requires GPL compliance/source release. A [commercial license](https://www.cgal.org/license.html) is offered by CGAL. No official Python wheel or standalone CLI for Mesh_2; C++ Windows build/demo is required. | Technical fallback/research only unless separately licensed. |

## Recommendation boundary

Keep the research selection separate from shipped dependency selection. Stage1AE is accepted for the bounded full-cell264 geometry-only pilot, and Cell258 Stage2A C0 plus no-Triangle preparation are accepted as bounded evidence. The next action and exact current gate are maintained in the [WP1 Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint); the frozen nine-phase lifetime ledger closes the prior lifetime-omission prerequisite, while opaque native/interpreter/allocator feasibility and full 2-D topology/quality certification remain outstanding, and no C1 execution is authorized by these documents. This does not authorize a solver, FasterCap, aggregate, network access, global install, production, shipping, or Triangle distribution. If a future Triangle pilot fails any gate or resource cap, STOP; do not lower the angle, switch silently, or infer full D104 cell 264/full-cell success from the actual D115C W0 intersection. Consider CGAL only as an explicitly authorized C++ fallback after the same evidence requirements.
