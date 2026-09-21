# SPD Decap PI Evaluator v0.23.1 — D117 WP1 static resource feasibility

**Current gate:** `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / STOP_C1_TRIANGLE_SINGLE_CALL`. The consumed `-02` and `-03` attempts are immutable STOP evidence; the fresh C0 exact-02 re-seal is current, and the C1 prelaunch cross-binding code/test checkpoint is independently `CODE_ACCEPT` for static/synthetic evidence only. No `-04` approval or run exists; `-03` remains immutable, consumed, and no-rerun.

**Decision:** `STOP_MONOLITHIC_FULL_DOMAIN_UNPROVEN`. This is a zero-solver
certificate of the sealed D104 geometry and a bounded-resource analysis. It
does not authorize FasterCap, `-oi`, a version probe, a D116 rerun, a transform,
an injection, or a production/PowerSI solve. Historical D115C evidence remains
immutable.

## 1. Scope and evidence authority

The physical minimum domain is the source-derived D104 L28–L31 window, all 16
cells (ordinals 258–273), with separate cells and no asserted DGND merge. The
table below is independently recomputed from each sealed WKB: Shapely reports
one `Polygon` component per file, and ring/vertex counts are obtained from the
WKB coordinates (the receipt values and SHA-256 are cross-checked). No geometry
operation (merge, difference, simplify, snap, or intersection) is applied.

Authoritative D104 receipt:

`D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01\geometry_receipt.json` — 19,728 B — SHA-256 `bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b`.

The receipt declares `version=0.23.1`, `status=PASS`, exact ordinals 258..273,
and aggregate WKB bytes 13,600,420. `component_count=1` and
`curve=false` hold for every row; `V` below is exterior vertices plus all hole
vertices, and `h` is the hole count.

## 2. D104 per-cell topology and closed-extrusion lower bound

| ordinal | layer / net | geometry (components) | V (ring vertices) | h | top T lower bound | closed all-T lower bound |
|---:|---|---|---:|---:|---:|---:|
| 258 | L28 / DGND | Polygon (1) | 149,078 | 2,048 | 153,172 | 604,500 |
| 259 | L29 / DGND | Polygon (1) | 121,662 | 4,679 | 131,018 | 505,360 |
| 260 | L30 / ADC_VDD_105_VAA_DDRH/0 | Polygon (1) | 62,976 | 2,842 | 68,658 | 263,268 |
| 261 | L30 / ADC_VDD_105_VAA_DDRH/1 | Polygon (1) | 62,387 | 2,794 | 67,973 | 260,720 |
| 262 | L30 / ADC_VDD_105_VAA_DDRL/0 | Polygon (1) | 57,569 | 2,595 | 62,757 | 240,652 |
| 263 | L30 / ADC_VDD_105_VAA_DDRL/1 | Polygon (1) | 47,107 | 2,141 | 51,387 | 196,988 |
| 264 | L30 / ADC_VDD_180_VQPS_SYS_1_AON/0 | Polygon (1) | 15,297 | 670 | 16,635 | 63,864 |
| 265 | L30 / ADC_VDD_180_VQPS_SYS_1_AON/1 | Polygon (1) | 25,438 | 1,131 | 27,698 | 106,272 |
| 266 | L30 / DGND | Polygon (1) | 34 | 2 | 36 | 140 |
| 267 | L31 / ADC_VDD_105_VDD2H_DDRH/0 | Polygon (1) | 62,054 | 2,799 | 67,650 | 259,408 |
| 268 | L31 / ADC_VDD_105_VDD2H_DDRH/1 | Polygon (1) | 55,897 | 2,499 | 60,893 | 233,580 |
| 269 | L31 / ADC_VDD_105_VDD2H_DDRL/0 | Polygon (1) | 58,645 | 2,645 | 63,933 | 245,156 |
| 270 | L31 / ADC_VDD_105_VDD2H_DDRL/1 | Polygon (1) | 52,170 | 2,371 | 56,910 | 218,160 |
| 271 | L31 / ADC_VDD_180_VQPS_SYS_2_AON/0 | Polygon (1) | 14,615 | 639 | 15,891 | 61,012 |
| 272 | L31 / ADC_VDD_180_VQPS_SYS_2_AON/1 | Polygon (1) | 26,253 | 1,168 | 28,587 | 109,680 |
| 273 | L31 / DGND | Polygon (1) | 34 | 2 | 36 | 140 |
| **Grand total** | **all 16 source components** | **Polygon (1 each)** | **811,216** | **31,025** | **873,234** | **3,368,900** |

For one connected polygon with `V` boundary vertices and `h` holes,

```text
top T = V + 2h - 2
closed extrusion = 2(top T) + 2V = 4V + 4h - 4.
```

The first term is the planar triangulation lower bound (each hole contributes
two triangles); the second duplicates top and bottom and splits each of the
`V` side-wall quads into two triangles. Therefore
`4(811,216) + 4(31,025) - 4(16) = 3,368,900` exactly. This assumes no Steiner
points, no quality subdivision, no segment recovery beyond the source rings,
and no adaptive refinement. It is a topology floor, not an engine count or a
resource forecast.

For cell258, the row-258 values above are the **raw source-ring** floor:
`R=149,078`, `H=2,048`, `T_raw=R+2H-2=153,172`, and
`F_raw=4R+4H-4=604,500`. The accepted Stage2A C0 splitter measured
`S=153,246`, so the C0 split floor is instead
`T_C0=S+2H-2=157,340`; these raw- and split-derived quantities must not be
mixed or treated as a Triangle/final-mesh count.

## 3. D116 measured telemetry (sealed, consumed)

D116-SHADOW is only a three-conductor L29/L30 W0 coupon (L28/L31 omitted,
artificial crop walls, homogeneous lossless `epsilon_r=3.4` snapshot). Its
sealed status is `STOP_D116_SHADOW`, stop reason `unexpected solver diagnostic`;
the run is consumed and was not rerun.

| observation | exact value |
|---|---:|
| raw input panels | 250 |
| preregistered triangle-equivalent expectation | 336 |
| panels reported to solver engine | 476 |
| final refined panels | 43,015 |
| runner wall (`wall_seconds`) | 15.629685640335083 s |
| FasterCap stdout `Total time` | 15.048000 s |
| peak process-tree working set | 281,341,952 B |
| peak/final scratch | 177,798 B |
| memory samples | 33 |

The runner wall includes process orchestration and is distinct from FasterCap's
stdout timing. Stdout also records the thin-triangle warning (minimum angle
below 5 degrees), the 336-versus-476 discrepancy, and later refinement to
43,015 panels. The receipt has no sealed sample time series; only the count and
extrema above are available. No sealed D107T-family time series was found in
the referenced evidence roots, so none is substituted or invented.

The 24 GiB process-tree cap is 25,769,803,776 B, the scratch cap is
8,589,934,592 B, and the wall cap is 14,400 s (4 h). A one-point coupon does
not bound the full 16-cell run. The following are linear sensitivities only,
never measurements, predictions, estimates, or bounds:

* Topology-floor/final-panel ratio `3,368,900 / 43,015 = 78.31919098`:
  ~20.52 GiB peak working set, ~20.4 min wall, and ~13.9 MB scratch.
* Topology-floor/engine-panel ratio `3,368,900 / 476 = 7,077.52101`, retaining
  D116 refinement/engine behavior: ~1,854.45 GiB peak working set, ~30.73 h
  wall, and ~1.172 GiB scratch.
* Boundary-conditioned count `4,255,132`: final-panel-density sensitivity is
  ~25.92 GiB peak working set (>24 GiB), ~25.77 min wall, and ~16.8 MiB
  scratch; engine-ratio sensitivity is ~2,342 GiB and ~38.81 h (with ~1.480
  GiB scratch).

Adaptive refinement, near-field hierarchy construction, boundary subdivision,
and panel interactions are nonlinear; the conflicting sensitivities are why a
hard-cap PASS cannot be claimed.

| scope/cap | strict assessment | reason |
|---|---|---|
| static topology census | **PASS (static only)** | sealed WKB census and exact topology arithmetic reproduce 3,368,900 |
| generator/materializer feasibility | **PARTIAL** | no full-domain peak-memory or wall receipt; quality subdivision is unsealed |
| 8 GiB scratch | **PARTIAL / UNKNOWN** | D116 scratch is tiny, but full-domain out-of-core behavior is unbounded |
| 24 GiB solver process tree | **STOP_EXECUTION (unproven)** | conditional sensitivities conflict, including 25.92 GiB boundary-conditioned value |
| 4 h solver wall | **STOP_EXECUTION (unproven)** | engine-ratio sensitivity is ~30.73–38.81 h; no bounded full-domain timing |

Consequently a monolithic full-domain D117 execution is **STOP**, not a
resource PASS.

## 4. Static quality-boundary census (ephemeral, not a lower bound)

An independent, read-only census of the sealed WKB boundary segments applies
this reproducible side-wall rule at `theta=7.5°` (XY edge splitting is dyadic;
vertical slabs use integer ceilings). For each cell of thickness `t` and
shortest source boundary edge `l_min`, choose
`m=max(1, ceil(t*tan(theta)/l_min))`, `hz=t/m`; for each original XY edge of
length `l`, choose `n=1` when `l*tan(theta)<=hz`, otherwise
`n=2^ceil(log2(l*tan(theta)/hz))`. Side triangles are
`2*m*sum(n)`. Only row 259 needs `m=3`; all other cells use `m=1`. This gives
**811,216 raw boundary segments**, **900,740 subdivided segments**, and
**2,508,664 side triangles**. The rule only controls rectangular side-wall
aspect/min-angle under this strategy: it is not a mathematical lower bound and
is not a full top-face quality mesh. It applies no boundary simplification and
adds no top-face Steiner refinement. Long boundary edges relative to 20-µm
walls drive the subdivision; L28's 35-µm thickness is retained as-is.

Keeping the top/bottom topology floor unchanged gives

```text
top + bottom = 2 × 873,234 = 1,746,468
boundary-conditioned triangle-equivalent panels
  = 1,746,468 + 2,508,664 = 4,255,132
factor over topology floor = 4,255,132 / 3,368,900 = 1.26306
```

The census is a sizing sensitivity only. A future certificate must materialize
and quality-check the actual all-T mesh; these counts do not prove quality,
coverage, memory, or solver acceptance.

## 5. Serialized input bytes and resource split

The repository serializer [`build_source_l29_l30_fastercap_input.py`](../../tools/research/build_source_l29_l30_fastercap_input.py)
emits one ASCII `T`/`Q` line per panel with nine `.17g` coordinates. The
unconditional grammar floor uses only nonempty tokens: `T` + space + a
one-byte conductor token + space + nine one-byte numeric tokens + eight
internal spaces + newline = **22 B/panel**. For the topology floor this is

```text
3,368,900 × 22 = 74,115,800 B = 70.6823 MiB   (panel text only).
```

Headers, list file, and receipt add bytes. As a conditional serializer-density
check, the sealed D116 input has 250 panels and 44,353 B across its three `.qui`
files; panel lines alone are 44,140 B (176.56 B/panel). Applying that observed
density to the topology floor gives 594,812,984 B (567.258 MiB), **a linear
sensitivity, not a measurement, estimate, prediction, or bound**. The separate
T-only sensitivity is 25,883/164 = 157.823 B/T, or about 507.06 MiB at the
topology-floor count. Actual coordinates, conductor-label lengths, quality
subdivision, segment recovery, and adaptive refinement can move either value
substantially; no full-domain byte count is sealed.

Generator/materialization resources are separate from solver resources. The
generator must parse 13,600,420 B of WKB and materialize at least 3,368,900
triangles plus topology/indices; no full-domain peak-memory or wall receipt
exists. The solver would additionally allocate hierarchy, interaction, and
refinement state; D116's 281,341,952 B and 177,798 B are coupon observations,
not transferable bounds.

## 6. Evidence ledger (hash-bound)

### D104 WKB files

All paths are under
`D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01\`.

| file | bytes | SHA-256 |
|---|---:|---|
| cell_0258.wkb | 2,426,237 | `1d894ff46db6fdf1e1662d1ae9d45cbb6676b5d002c0357c27c74f9f1e4835e1` |
| cell_0259.wkb | 2,040,201 | `a438379bf22b47e412b763eebb49c3aae7252c5872d5ac7c31513499108b9053` |
| cell_0260.wkb | 1,064,485 | `0c7220f4e4e284f0be6857813a9382cb893f30fc25f75d46bad6c0d917b5be9a` |
| cell_0261.wkb | 1,054,101 | `b1d6025fff224d835e406355c06a7917ddf39d23b520fafc4cbd7a90764fbfa3` |
| cell_0262.wkb | 973,033 | `7d490d1ff8c04f352eb181e00c3874845439892a1e3cfba51522c76f3b37defe` |
| cell_0263.wkb | 796,561 | `162039f4eed00cc5b7c534a559f50ee41b7b458a0d802e1971fc9d1629b74d87` |
| cell_0264.wkb | 258,181 | `a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5` |
| cell_0265.wkb | 429,657 | `89382a6499d5a0d01cbd7561372f6ac347ca31c44fc0afe244a023aec4887bca` |
| cell_0266.wkb | 613 | `7aaf6788360eba8929384bb49bbe56633cd67e22883f55c1940fcf53eb85368c` |
| cell_0267.wkb | 1,048,873 | `3247feb29fd847f61c9bd0a21601af2dff46c4e20a578d6b9ab763f5e6ccd074` |
| cell_0268.wkb | 944,361 | `94f1971644cfd43dfbe70238b57dd78e03a15c8d1aaab3726e97ffaae47c448f` |
| cell_0269.wkb | 991,249 | `1d713b63c8f34421642a37cc903c8fbceca9f2d9dd320b6b54bd94561e5223d5` |
| cell_0270.wkb | 882,169 | `4a3a284c686663db4a9d066d4fe9cbd001420ba694dcfb1b99c42a7c34c88ac4` |
| cell_0271.wkb | 246,649 | `75f575969c8521b459be033e4a7828b3bfa9061de1bc3add1ee20d014928a1cb` |
| cell_0272.wkb | 443,437 | `ea55ea900ae93bf5b2334f6738cc2ba6206a3774f458bc110f72625af47bed1e` |
| cell_0273.wkb | 613 | `7aaf6788360eba8929384bb49bbe56633cd67e22883f55c1940fcf53eb85368c` |

### D116 sealed run and input

| artifact | path | bytes | SHA-256 |
|---|---|---:|---|
| D116 receipt | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\d116_shadow_fastercap_receipt.json` | 7,594 | `476569f97dfddef128b05339eb4cc37d130b03c60f93896a83e32eb624b9b365` |
| D116 stdout | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01\input\stdout.txt` | 40,956 | `07da7101334b2490ef11568b758cba02b3ea916b534fed56af928d6a27687949` |
| A_GND_259.qui | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-source-l29-l30-fastercap-input-02\A_GND_259.qui` | 32,652 | `3bc8230edf765a4c5a0c0f8a8b484e249e5d652e2d3c0f9cf3c3ae0f9c78d7b2` |
| A_PWR_264.qui | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-source-l29-l30-fastercap-input-02\A_PWR_264.qui` | 10,449 | `22f2119e2f1377c8554a88af89636002f889aa9e4f5f16adf23a4df202677a87` |
| F_DDRL_262.qui | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-source-l29-l30-fastercap-input-02\F_DDRL_262.qui` | 1,252 | `ab4c920091fc7e5f3d83ac2833ee39c490865bbc0795898cb360643cc871c90e` |
| d116_shadow_gap_coupon.lst | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-source-l29-l30-fastercap-input-02\d116_shadow_gap_coupon.lst` | 150 | `b6ea0b3d322532f124d9c3da23fca153ed945a1c19cb79f2639af2fff24c39d6` |
| d116_shadow_gap_coupon_receipt.json | `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-source-l29-l30-fastercap-input-02\d116_shadow_gap_coupon_receipt.json` | 5,480 | `5a43383a24083e8acffe3b78066e65e475bdadba264f176422ac47066a22c06c` |

Repository sources read for serializer/telemetry contracts: [`build_source_l29_l30_fastercap_input.py`](../../tools/research/build_source_l29_l30_fastercap_input.py), 30,063 B, SHA-256 `e9b76e5e72d300a19f1e5faaa120e2f2500082767b753318b29877cfa8f6872b`; [`run_d116_shadow_fastercap.py`](../../tools/research/run_d116_shadow_fastercap.py), 50,998 B, SHA-256 `57ed814a19e8454d7e427f7efa64bb7602b9aaffd186620520547c1f16bbaf16`; [`D116_SHADOW_RAW_C_MATRIX_RESULT.md`](D116_SHADOW_RAW_C_MATRIX_RESULT.md), 10,211 B, SHA-256 `bc7d07185036a76fb6cc3b559a9a329c887fc99bae4ba00f469320b54b60232b`; [`PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md`](../PRODUCT_PURPOSE_AND_TECHNICAL_BASELINE.md), 55,602 B, SHA-256 `340396bcdd82775e217a2814a480a62825c3e5f09de0efb4f584d7af63b5351c`; [`WORK_EXECUTION_BASELINE.md`](../WORK_EXECUTION_BASELINE.md), 93,926 B, SHA-256 `8c45f2ab69a78f16627491e5635861c52c7d34a5c9b1927d3b4800b5de4e781d`.

## Cell258 C1 preparation checkpoint (WP1 authority)

The current gate is `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / STOP_C1_TRIANGLE_SINGLE_CALL`.
The former `STOP_C1_MEMORY_MODEL_UNPROVEN` label was refined, not cleared: the
frozen nine-phase lifetime ledger closes the prior lifetime-omission
prerequisite, while opaque native/interpreter/allocator feasibility remains
unknown. The exact reviewed `-02` call is consumed immutable
`STOP_C1_TRIANGLE_SINGLE_CALL` evidence and is not reusable or rerunnable. The
consumed `-03` STOP receipt is recorded below; the fresh C0 exact-02 re-seal is
recorded below and supersedes the stale exact-01 C0 bundle for future planning.
The C1 prelaunch cross-binding code/test checkpoint is independently
`CODE_ACCEPT` for static/synthetic evidence only; no `-04` approval or run
exists. All C1/Triangle activity remains `STOP` pending separate immutable
approval.
Cap-02 is an accepted, geometry-only census and boundary-chain result; it does
not contain a Triangle mesh, extrusion, solver, FasterCap, or shipping
authorization. The immutable root is
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell258-stage2a-c0-cap-02`.

The exact C0 identities are:

| artifact | bytes | SHA-256 |
|---|---:|---|
| `stage2a_c0_census.py` | 40,274 | `169d70e2d4aaf23bfdb3b9fbd209380ca77ed7ed09e09f5c89c75f32a30f3452` |
| `stage2a_c0_controller.py` | 114,544 | `fb9278f099eddcf363f36ee76f99eb647525a8fa6931b67546486f37a6fa4c02` |
| `stage2a_c0_static_approval.json` | 9,030 | `3d02662d59aa514bfbf64710119509e25d951b08bf4f3bb0cc6f21805378159e` |
| C0 JSON | 5,448,871 | `5ae752680b63686f90dd0d80037b337bba0da71f3bd9d6d755a9e94365f9ac60` |
| C0 text | 142 | `b4b93f13737c460f5c76272afb614312021904f679419c1fd652fed17e226d20` |
| run receipt | 26,767 | `ceba8bce28329ac858c0466c948839a87ba0d35ef4934277ef06e7d2c6ea1f57` |

The receipt is `PASS`, elapsed **2.813 s**, child exit **0**, Job total/active
**1/0**, peak commit **285,188,096 B**, observed working-set peak
**264,499,200 B**, and maximum observed artifact **5,612,916 B / 7 items**;
all governed pre/post, output, hash, and resource gates passed. Inputs were
Stage0 **25,832 B / `f43a4dd3ab531b75c2f8a450406ddb70bf92cf47a94e67f2dbcede207b9c4bdd`**,
D103 **204,735 B / `4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9`**,
D104 receipt **19,728 B / `bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b`**, and
`cell_0258.wkb` **2,426,237 B / `1d894ff46db6fdf1e1662d1ae9d45cbb6676b5d002c0357c27c74f9f1e4835e1`**.

Cell258 is `Signal$L28(DGND)` (source layer raw/ordinal **54**, conductor), island
`spd-surface-island:ea4bc44349ce103beab4dca3`, area
**8,476,333,524.145388 um2**, bbox **[-49700,-49700,49700,49700]**, one
Polygon component (exterior ring=1), **2,049 rings**, `H=2,048`, thickness **35 um**, and raw
`R=149,078` vertices (`8` exterior + `149,070` holes); conductivity is
**59,590,000**. Step **140 um** yielded
`S=153,246` split vertices and segments, marker count **149,078**, marker sum
`S`, distribution `{1:149070,16:3,32:1,1024:4}`, and **2,048** strict interior
hole points; edge flags were `corner=true, curve=false, edge=true`. Therefore
`T_C0=S+2H-2=157,340`, `Vclosed=306,492`, and
`Fclosed=621,172`; these fit planar V/T **400,000/600,000** and derived closed
V/F **800,000/1,608,188** caps. Canonical PSLG identity is
**12,057,453 B / `1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b`**
with lower-bound estimate **7,421,359 B**; bytes were not retained separately,
so it must be recomputed before C1. Geometry/research/non-shipped flags were
true; Triangle, extrusion, solver, FasterCap, and network counts were zero.

The C0 input arrays are source-provably **4,323,656 B**:
`float64[S,2]=2,451,936`, `int32[S,2]=1,225,968`,
`int32[S,1]=612,984`, and `float64[H,2]=32,768`.

### Bounded no-Triangle preparation

The prepared implementation and focused tests are the exact files below:

| artifact | bytes | SHA-256 |
|---|---:|---|
| `tools/research/d117_triangle_cell258_c1.py` | 139,525 | `821aed9ac82138181bc319e77d6e1508c9871a0e51c21e51607d709e51bdc1ec` |
| `tests/test_d117_triangle_cell258_c1.py` | 39,812 | `1682d4c44f723cc246c18f224bc4dd63154b71ea3c8bb88d3e4bd79b108fde15` |

The current exact suites are **71 passed** for the certifier and **30 passed**
for the one-call preparation (**101 passed total**) with verdict **ACCEPT**;
all three safe selfchecks `PASS`. The Win32 create/query regression is a pytest
case, not a selfcheck. The exact-term contract rejected all **68 table-driven
contract-field mutations across 17 terms** (phase, classification, invalid
numeric bound, and boolean bound), as well as paired term/phase-projection
removal and the opaque-to-formula mutation.
An independent production `prepare_no_triangle()` reproduction matched the
canonical C0 identity **12,057,453 B / `1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b`**.
The Triangle module census was `()` before and `()` after, with
`triangle_extension_loaded=false`; no Triangle, extrusion, replay, solver,
network, install, staging, or commit occurred.

The future result trust contract is exactly six arrays:
`vertices`, `vertex_markers`, `triangles`, `segments`, `segment_markers`, and
`holes`. The `holes` array is `<f8[2048,2]`, finite, C-contiguous, aligned,
owned, and has `base is None`; structural validation returns `hole_count` and
can require exact equality to the frozen input hole array after validation.
The owned-array status is `STATIC_OWNED_NDARRAY_BYTES_PASS`; the controlled-
buffer status is `STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE`; native
feasibility remains `OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN`; C1 status is
`STOP_C1_TRIANGLE_SINGLE_CALL`; and full 2-D summarization is
`INCOMPLETE_C1_DEFERRED`.

The frozen read/parse path is sequential and fail-closed: each sealed file is
read once into exactly one expected-size `bytearray` using `readinto`/SHA-256
with `chunk_size` type `int` in `1..8192`, with pre-open, open-handle, and
post-read identity/size/digest checks. Read views are released. C0, D103, and
D104 parsed objects and buffers are deleted before the next receipt is read;
the source buffer and any immutable parser copy, then the Stage0 buffer, wheel
buffer, and PSLG ring references are released/deleted in sequence. `del`
releases Python references only; synchronous return of Python/native allocator
memory is not proven.

### Historical/superseded 17-term ledger (not current)

The following ledger and its **42,153,032 B** co-live floor are retained for
traceability only: they are historical, superseded, and **not current**. The
current root-fixed ledger is the **48-term** contract with known co-live floor
**87,156,424 B**, as summarized in the full-domain gate §3A; no value below
this heading is an authoritative current cap.

The completed machine-checkable lifetime ledger has these nine exact phases:
`read/parse`, `PSLG`, `native-call`, `result`, `validation`, `boundary`,
`quality`, `canonical`, and `receipt`. Its exact 17-term contract is:

| term | phases | class | bound_bytes |
|---|---|---|---:|
| `sealed_read_buffer` | read/parse | mutually exclusive | 5,448,871 |
| `source_parser_copy` | read/parse | formula-bounded | 2,426,237 |
| `parsed_provenance_receipts` | read/parse | job-contained opaque | `None` |
| `parsed_source_geometry` | read/parse; PSLG | job-contained opaque | `None` |
| `pslg_input_arrays` | PSLG; native-call; result; validation; boundary; quality; canonical; receipt | concurrently live | 4,323,656 |
| `pslg_ring_storage` | PSLG | job-contained opaque | `None` |
| `native_call_workspace` | native-call | job-contained opaque | `None` |
| `result_arrays` | native-call; result; validation; boundary; quality; canonical; receipt | concurrently live | 20,032,768 |
| `validation_scratch` | validation | job-contained opaque | `None` |
| `boundary_triangle_edges` | boundary | concurrently live | 14,400,000 |
| `boundary_records` | boundary | concurrently live | 3,200,000 |
| `boundary_chunk_scratch` | boundary | concurrently live | 196,608 |
| `quality_used_vertex_scan` | quality | formula-bounded | 400,000 |
| `quality_gather_scratch` | quality | formula-bounded | 794,624 |
| `quality_numpy_allocator_retention` | quality | job-contained opaque | `None` |
| `canonical_stream` | canonical | formula-bounded | 67,306,552 |
| `receipt_payload` | receipt | job-contained opaque | `None` |

The known concurrent full-certifier floor is exactly **42,153,032 B**:
**24,356,424 B** of concurrently live input/output arrays plus **17,796,608
B** of explicit boundary work (`14,400,000 + 3,200,000 + 196,608`). The
ledger closes the prior lifetime-omission prerequisite; opaque
native/interpreter/allocator feasibility and full 2-D topology/quality
certification remain unresolved.

The first bounded C1 certificate slice is the private
`_oriented_boundary_records(result, chunk_size=8192)` helper. It reuses the
six-array validator, then fail-closes unless `V < 2^19` and every segment
marker satisfies `0 < marker < 2^18` (the reused validator's positive-marker
contract). It chunk-fills one `uint64` directed
triangle-edge array (`3T` entries, **24T B**), sorts that array in place,
rejects incidence above two or same-direction shared edges, and compacts
singleton oriented records into its front. It then chunk-encodes one output
`uint64` boundary-record array (`B` entries, **8B**), sorts it in place,
rejects missing, extra, duplicate, and long-vs-split T-junction segments,
and mutates that same owning array to final `(oriented_edge << 18) | marker`
records. The caller chunk size is bounded to `1..8192`; three reusable
`uint64[chunk_size]` arrays are the only explicit chunk scratch (**24 ×
chunk_size B**, at most **196,608 B**). During output fill and comparison the
explicit peak is therefore **24T + 8B + 24×chunk_size B** (at the declared
caps, **17,796,608 B**); triangle-edge storage lives through final comparison,
output/final records live through return, and chunk scratch lives across both
fills. No `argsort` or additional B-sized temporary/copy is used. Python/NumPy
allocator overhead and native sort stack are excluded.
The helper returns an owning C-contiguous `uint64` array with no retained
triangle-edge base. This slice does not certify geometry: status remains
`INCOMPLETE_C1_DEFERRED`, full-2D status remains `FULL_2D_CERT_STOP`, and
full geometry coverage, robust boundary collinearity/order, cycle/hole
topology, angle/aspect, and connectivity remain deferred P0s.

The fixed no-execution quality option is `pq15CzS221330`. With
`B0=V0=153,246`, `H=2,048`, and `T0=157,340`, the frozen count model is
`V=V0+ni+nb`, `B=B0+nb`, and `T=T0+2ni+nb`. The derived cap is
`STEINER_POINT_CAP=(600,000-157,340)//2=221,330`; all-interior points maximize
`T`, giving `V=374,576`, `B=153,246`, `T=600,000`, closed `V=749,152`, and
closed `F=1,506,492`, while the all-boundary case gives `B=374,576` and
`T=378,670` under the same closed caps. `n=221,331` all-interior points give
`T=600,002` and are rejected. The vertex-limited all-interior candidate
`n=400,000-153,246=246,754` reaches `V=400,000` but gives `T=650,848`,
exceeding the triangle cap by `50,848`. The contract records
`STATIC_NATIVE_BOUND=NOT_PROVABLE_FROM_PINNED_SOURCE_ALONE`: `S221330` is only
a count/resource prerequisite, not a mesh-quality or native-memory proof.

The bounded array accounting is: input owned ndarray **4,323,656 B**; output
array cap **20,032,768 B** (`vertices` 6,400,000 B, `vertex_markers`
1,600,000 B, `triangles` 7,200,000 B, `segments` 3,200,000 B,
`segment_markers` 1,600,000 B, `holes` 32,768 B); and combined input/output
cap **24,356,424 B**. The future canonical stream includes unindexed
`h {x:.17g} {y:.17g}\n` lines with a **52 B** maximum. Its exact derived cap is
`56 + 400,000*(59+21) + 600,000*30 + 400,000*(23+20) + 2,048*52`
`= 67,306,552 B`.

The accepted operational Job process cap is exactly **3,435,970,560 B** with
strict equality; it is fail-closed safety only, never a completion proof or a
static-memory proof. The
controlled-lifetime accounting explicitly excludes: **sealed-read copies; ring
storage; unique/sort/bincount scratch; chunk gathers; canonical I/O/receipt;
interpreter/native/import/allocator** from the narrow owned-array subtotal;
each is represented in the completed lifetime ledger as a bounded term or
job-contained opaque term. Full 2-D topology/quality certification and opaque
native/interpreter/allocator feasibility remain prerequisites. This checkpoint
does not execute C1; the consumed `-02` attempt is immutable
`STOP_C1_TRIANGLE_SINGLE_CALL` evidence, and the consumed `-03` STOP receipt is
recorded below. The fresh C0 exact-02 re-seal is recorded below; the prelaunch
cross-binding code/test checkpoint is independently `CODE_ACCEPT` for
static/synthetic evidence only, but no `-04` approval or run exists.

### Upstream hole ownership and Steiner-cap conclusion

The pinned upstream sources establish the future array boundary: v20250106
`tri.py` maps and wraps `holelist` as `holes`
([tri.py](https://github.com/drufat/triangle/blob/v20250106/triangle/tri.py));
`core.pyx` copies each non-null output array into NumPy storage
([core.pyx](https://github.com/drufat/triangle/blob/v20250106/triangle/core.pyx)).
At triangle-c commit `8b9e1046e5cddab1298d3204f10c93665836cf99`,
`triangle.h` documents `holelist` as input-only with its pointer copied out,
and `triangle.c` assigns `out->holelist = in->holelist`
([triangle.h](https://github.com/drufat/triangle-c/blob/8b9e1046e5cddab1298d3204f10c93665836cf99/triangle.h),
[triangle.c](https://github.com/drufat/triangle-c/blob/8b9e1046e5cddab1298d3204f10c93665836cf99/triangle.c)).

The C default Steiner budget is unlimited (`-1`); `S<number>` bounds added
Steiner points for quality and mesh refinement decrements that budget. Segment
intersections (and `-s` recovery, if used) can exceed that bound; this C0 PSLG
has no intersections and does not plan `-s`. The fixed `S221330` cap is
therefore necessary but insufficient for a later static native-memory proof,
and it does not prove mesh quality. This count/resource contract remains a
future design prerequisite, not C1 authorization.

The outer wrapper recorded the sole Process.Start/PID **26700** and no retry,
but its read-only PowerShell `$PID` assignment interrupted sealing of outer
exit/stdout/stderr and launcher-count fields; do not infer those fields. Cap-01
remains immutable STOP evidence, while cap-02 is the accepted C0 evidence.

### C1 single-call attempt `-01` (consumed immutable STOP)

The immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-cell258-c1-single-call-01`
was consumed exactly once and contains exactly three files:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_c1_single_call_hq_approval.json` | **18050 B / `e3ab65812efdd9c71fd927297c3a63759e3d705a57a3e0cf450d6645ed72b026`** |
| `d117_cell258_c1_single_call_attempt_token.json` | **1339 B / `0d50a89dfbf8553c4b5a90089fb09c4abf82ea8c4714eeec6b07f366932df9e2`** |
| `d117_cell258_c1_single_call_controller_receipt.json` | **42767 B / `2a240832d2ab4af3328f22cea677723a6d0b2924b900f1de1ee2fc9042cf16df`** |

The receipt records `status=STOP_C1_TRIANGLE_SINGLE_CALL` with
`reason=ControllerError: terminal cleanup failed`. The actual child outcome was
`STOP child Job limits do not match approval`: the approval requested Job memory
`3435973836 B`, while Windows returned `3435970560 B`; strict equality failed.
It records one launch and no retry (`attempts=1`, `retries=0`,
`launch_count=1`), with no canonical/exact receipt. Triangle activity is
inferred as zero from the pinned pre-import failure path plus the absent
outputs; the receipt field is `triangle_loaded_modules=null`, and no direct
Triangle-call counter is claimed. Mark `-01`
immutable, consumed, and do not rerun it; this is resource/controller evidence
only, not a C1 execution or numerical authority upgrade.

The cap correction occurred after `-01` and before `-02`: it aligns the
operational cap exactly to **3435970560 B** and retains strict equality. The
five current accepted file identities are:

| artifact | bytes / SHA-256 |
|---|---|
| `tools/research/d117_triangle_cell258_c1.py` | **139525 B / `821aed9ac82138181bc319e77d6e1508c9871a0e51c21e51607d709e51bdc1ec`** |
| `tools/research/d117_triangle_cell258_c1_single_call_runner.py` | **51930 B / `592ea02c6a17c63ca6b5b35113726126e6280a717078269084fa76d1cf1136b1`** |
| `tools/research/run_d117_triangle_cell258_c1_single_call_exact_once.py` | **71996 B / `d6407cae741b1ab06ff2229db086bbb1c49adbf062cc99209c98c4add33faafc`** |
| `tests/test_d117_triangle_cell258_c1.py` | **39812 B / `1682d4c44f723cc246c18f224bc4dd63154b71ea3c8bb88d3e4bd79b108fde15`** |
| `tests/test_d117_triangle_cell258_c1_single_call.py` | **27658 B / `fa827a644a1e2c22d0e8cfda4570b835b11cbd83537a97a51b57f4e6f7105533`** |

Only the post--02 **51930 B** runner/test fix uses the existing `_canonical_path`
in runner `_identity`; Sol ACCEPT records **30 passed**, all three safe
selfchecks `PASS`, and three Windows samefile/different-file/shared-role
regressions. The
Win32 create/query regression is a pytest case, not a selfcheck. The frozen
`-02` attempt used the pre-fix runner **51918 B /
`f827455647aad4e425012cad3e38df538c000c379aecf6e61c78e65667b00c9d`**. The
historical post--02 runner **51930 B /
`592ea02c6a17c63ca6b5b35113726126e6280a717078269084fa76d1cf1136b1`** contains
the post--02 canonicalization fix; it has only static/synthetic test evidence,
has never received C1 execution evidence, and is not represented, pinned, or
executed by `-02`. The immutable `-02` root below was consumed exactly once and
is not reusable.

### C1 single-call attempt `-02` (consumed immutable STOP)

The immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-c1-single-call-02`
contains exactly three consumed artifacts:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_c1_single_call_hq_approval.json` | **18050 B / `3c62a556ad175a5f8f7514946ab8fe73e530a60b6b22af6a450d7ffd41ffde1f`** |
| `d117_cell258_c1_single_call_attempt_token.json` | **1339 B / `7255dc93c43fb6726277cb5133da6f8d97149c812e8ef656e93e9fd0f4c87008`** |
| `d117_cell258_c1_single_call_controller_receipt.json` | **42731 B / `91f4e74469702caf63c90e214695a246163caf0d2cef23a5497168df1baba4ec`** |

The controller receipt status is `STOP_C1_TRIANGLE_SINGLE_CALL` with
`reason="ControllerError: child marker/line ending mismatch"`; the exact
stderr bytes decoded from receipt `capture[1].prefix` are
`SPD Decap PI Evaluator v0.23.1 STOP_C1_TRIANGLE_SINGLE_CALL: StopError: attempt token approval binding mismatch\r\n`.
Its cleanup object records `cleanup.cleanup_ok=true`, `captures_drained=true`,
`errors=[]`, `terminal=true`, and `terminal_confirmed=true`. Job fields are
`active_process_limit=1`, `active_processes=0`,
`job_memory_limit=3435970560`, `limit_flags=8712`,
`peak_job_memory_used=14716928`, `total_processes=1`, and
`total_terminated_processes=0`; top-level `job_memory_bytes=3435970560` is
separate. No canonical/exact receipt was produced. Triangle activity is
inferred as zero from the pinned pre-import failure path plus the absent
outputs; the receipt field is `triangle_loaded_modules=null`, and no direct
Triangle-call counter is claimed.
Mark `-02` immutable, consumed, and do not rerun it; this is
controller/resource evidence only. The subsequent `-03` attempt and its
terminal STOP receipt are recorded below; no C1/Triangle execution or solver/
FasterCap/PowerSI work is authorized from this consumed attempt.

### C1 single-call attempt `-03` (consumed immutable STOP)

The immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-c1-single-call-03`
was consumed exactly once and contains exactly three files:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_c1_single_call_hq_approval.json` | **18050 B / `142f0ec35f50e754991be59f0edfebcae4ab0ee4ecad5822b3576defec9e9693`** |
| `d117_cell258_c1_single_call_attempt_token.json` | **1339 B / `2387006a6780bc1c26788347ef355786a247de055208443790160102547b4017`** |
| `d117_cell258_c1_single_call_controller_receipt.json` | **42721 B / `62a2f91f09f2e52051d04e33552a4167cd7d07823eabe1a1dc73ac154dc84612`** |

The exact approval seal check passed; the outer invocation exited `2` after
about `1.399 s`, with no retry. Receipt status/overall status is
`STOP_C1_TRIANGLE_SINGLE_CALL`, with top reason
`ControllerError: child marker/line ending mismatch`. Decoded
`capture[1].prefix` is exactly
`SPD Decap PI Evaluator v0.23.1 STOP_C1_TRIANGLE_SINGLE_CALL: StopError: clearance helper identity mismatch\r\n`.
It records `attempts=1`, `launch_count=1`, and `retries=0`; the controller
receipt hierarchy gives top-level `job_memory_bytes=3435970560` separately and
`job.active_process_limit=1`, `job.active_processes=0`,
`job.job_memory_limit=3435970560`, `job.limit_flags=8712`,
`job.peak_job_memory_used=15753216`, `job.total_processes=1`, and
`job.total_terminated_processes=0`. Cleanup fields are
`cleanup.captures_drained=true`, `cleanup.cleanup_ok=true`,
`cleanup.errors=[]`, `cleanup.terminal=true`, and
`cleanup.terminal_confirmed=true`; `final_child_receipt=null` and
`final_output=null`. Triangle activity is inferred as zero from the pinned
pre-import failure path plus the absent outputs; the receipt field is
`triangle_loaded_modules=null`, and no direct Triangle-call counter is claimed.
`triangle_determinism=NOT_EVALUATED` and `triangle_replay=false`; no canonical
output or exact child receipt exists. Mark `-03` immutable, consumed, and do
not rerun it.

Root cause is a helper identity cross-binding failure: path and size match
(`139525 B`), but the sealed clearance helper SHA
`e1bec661639eb51aba7f36435ad0c6c75352ba21c42415c6c6e168764f5d53b3` differs
from current/`-03` C1 SHA
`821aed9ac82138181bc319e77d6e1508c9871a0e51c21e51607d709e51bdc1ec`. A sole
reverse-byte change of `FUTURE_PROCESS_CAP_BYTES` from `3435970560` back to
`3435973836` reproduces the sealed historical helper SHA
`e1bec661639eb51aba7f36435ad0c6c75352ba21c42415c6c6e168764f5d53b3`; do not
revert. Approval
validators missed this cross-binding, and the existing real test is
tautological.

The C0 exact-02 re-seal below is complete. The next gate is not yet created,
reviewed, or executed: add a controller prelaunch bundle check and regressions
for current-helper cross-binding, then create a separately approved new C1
root/attempt. Until then all C1/Triangle and numerical solver/FasterCap/PowerSI
activity remains `STOP`.

### Cell258 C0 exact-production boundary-clearance PASS (exact-02 re-seal, after C1 `-03` STOP)

The fresh exact-one C0 re-seal uses the immutable root
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-boundary-clearance-exact-02`.
It follows the C1 `-03` stale-helper STOP recorded immediately above.
The controller receipt records `invocation_elapsed_seconds=19.375 s` and
`process_elapsed_seconds=19.25 s`; the exact receipt records scan
`elapsed_seconds=11.199562800000422 s`. It exited **0** and launched exactly
once. The root contains exactly four regular non-reparse files:

| artifact | bytes / SHA-256 |
|---|---|
| `d117_cell258_boundary_clearance_exact_hq_approval.json` | **3568 B / `efc6450d85b34273c0c32bee53afd14479807347ad4e35e82479eebe7ff20370`** |
| `d117_cell258_boundary_clearance_exact_attempt.json` | **833 B / `5d8acc61ad84f4b4d93b62d9d62616d16a8982a957e7ec689b759957e867c10f`** |
| `d117_cell258_boundary_clearance_exact_receipt.json` | **4493 B / `7a8d007e2e9c6331eb7d1a2754dbc826ebfc44d96c279fa2ff645814165d7c6a`** |
| `d117_cell258_boundary_clearance_exact_controller_receipt.json` | **5296 B / `40ca3c34d45dd02f46658e8894a035908fdae8a651ebcedc7e1da6a684559c56`** |

The controller is `PASS` / `accepted_pass`: `attempts=1`, `retries=0`,
`launch_count=1`, and `return_code=0`. Job fields are
`job_memory_limit=1073741824 B (1 GiB)`, `active_process_limit=1`,
`peak_job_memory_used=744714240 B`, `active_processes=0`,
`total_processes=1`, `total_terminated_processes=0`, and `limit_flags=8712`.
The exact receipt is `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA` with
`exact_clearance=true`, `exact_unique_nonincident_pairs=212005`,
`exact_pair_evaluations=212005`, `exact_distance_evaluations=848020`,
`endpoint_distance_evaluations=848020`, `raw_visits=375962`,
`candidate_visit_identity_holds=true`, and `exact_pair_identity_holds=true`.
The current helper
SHA is `821aed9ac82138181bc319e77d6e1508c9871a0e51c21e51607d709e51bdc1ec`;
the current module SHA is
`5747ac2e520b053df4ed30c3a5bbf4235d4bb8bf763532a53ee24589a71d3ef6`.

This PASS is scoped to `frozen_split_pslg_float64` only. Raw-source clearance
was not evaluated; raw-source/global-boundary equivalence remains unknown and
`GLOBAL_BOUNDARY_EQUIVALENCE_STOP` remains in force. Triangle, solver, PowerSI,
and C1 were not run. The prelaunch cross-binding code/test checkpoint below is
independently `CODE_ACCEPT` for static/synthetic evidence only; no `-04`
approval or run exists, so C1 remains `STOP`. The C1 -03 attempt remains immutable, consumed, and no-rerun; this fresh C0 exact-02 supersedes
the stale exact-01 C0 bundle for future planning but does not retroactively make
`-03` pass.

### C1 prelaunch cross-binding CODE_ACCEPT (2026-09-06; code-only)

The independently reviewed prelaunch cross-binding code/test checkpoint is
`CODE_ACCEPT` for static/synthetic evidence only. Its current identities are:

| artifact | bytes / SHA-256 |
|---|---|
| `tools/research/run_d117_triangle_cell258_c1_single_call_exact_once.py` (controller) | **73656 B / `584ac9cf667f1fe9cd12e1467e2f71998e4050b178b9a719d5929ecfd239112d`** |
| `tools/research/d117_triangle_cell258_c1_single_call_runner.py` (runner) | **51930 B / `5cc8e16aa651c4eca172efd6a4e78280bf1dd543ae9969c01c4f6380707b5138`** |
| `tests/test_d117_triangle_cell258_c1_single_call.py` | **32347 B / `cc810c517b0158383b9537481a39a3c5bf3956dd6923067f5281d52a12043101`** |

The independently accepted result is **31 passed**, plus an isolated stale-bundle
no-launch check **1 passed**. Pre-token/pre-Popen checks bind the current-helper,
exact implementation, and controller-receipt path+size+SHA identities. Against
a stale bundle the check returned `exit=2`, `token=absent`, and
`launch_count=0`; the runner retains its independent defense. This is
code/prelaunch evidence only: no C1/Triangle or numerical execution is claimed
or authorized. No `-04` approval or run exists; `-03` remains immutable,
consumed, and no-rerun.

## D117 raw-visit occupancy checkpoint (WP1 authority)

This is the authoritative static checkpoint for the exact implementation.
The implementation is independently **ACCEPTED by Sol** for code and synthetic
evidence only; this checkpoint is not an exact-production `PASS` and grants no
additional boundary-clearance execution. The conditional receipt evidence and
cap remain unchanged.

- **Recovery diagnostic.** The failed recovery root suffix `-01` is absent, has
  no receipt, and produced a single Python exit 2 at the intended receipt
  destination after helper/census gates. There was no retry. This is an
  orchestration diagnostic, not scientific evidence.
- **Accepted immutable occupancy receipt.**
  `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260905-d117-cell258-boundary-clearance-occupancy-02\d117_cell258_boundary_clearance_occupancy_receipt.json`, **3,448 B**, SHA-256
  `7e4e5e4b7b1c0b4d3aba88bcafbc655a05707bbd9f06d58eda283470676d4c51`.
- **Implementation identities.** Module: **49,139 B**, SHA-256
  `241dd81947107d38b770df53c5e350fe6e251a80a8e558f609da592b69fe43a3`;
  tests: **28,953 B**, SHA-256
  `443fdd8f5b98e5a2b1d986380ea0a666ae3055d341a046d2b62cdb7860549ceb`.
  The focused suite passed **36 tests in 0.24 s** and the module synthetic
  self-check was **PASS**.
- **Accepted contract corrections.** The exact path now (1) re-verifies the
  canonical bytes/SHA of the actual returned vertices, segments, and
  segment-markers PSLG before pair scanning; (2) returns CLI codes `0/1/2` for
  `PASS/WITNESS/INCOMPLETE` (refusal remains `2`); (3) separates
  `scan_complete` (only `PASS`) from `decision_complete` (`PASS` or
  `WITNESS`); (4) states clearance only for frozen split-PSLG segments while
  emitting raw-source clearance as unevaluated/unknown and retaining
  `GLOBAL_BOUNDARY_EQUIVALENCE_STOP`; and (5) records intersection witnesses
  with exact distance `N/D=0/1`.
- **Acceptance boundary.** This Sol ACCEPT covers code and synthetic tests
  only, not an exact-production `PASS`. The consumed immutable `-02` attempt is
  `STOP_C1_TRIANGLE_SINGLE_CALL` evidence, is not reusable, and grants no
  current C1 authorization. The consumed `-03` STOP receipt is recorded above;
  the fresh exact-02 C0 re-seal is current, and the prelaunch cross-binding
  code/test checkpoint is independently `CODE_ACCEPT` for static/synthetic
  evidence only. No `-04` approval or run exists. All C1/Triangle activity
  remains `STOP`, as do solver and PowerSI. The separately
  sealed exact-production C0 `PASS` is documented in the full-domain gate §3A
  and remains limited to the frozen split PSLG.
- **Receipt metrics.** `153,246` segments; `242,165` records; `79,443`
  cells; max occupancy `31`; `candidate_visit_upper_bound=375962`; packed
  SHA-256 `a0c9124c8f59eab11e2bad03a1b177b515157886046428d79f3ea3e24c0bf708`;
  actual ndarray bytes **6,260,976 <= cap 9,227,528**. No pair, distance,
  Triangle, or solver operation occurred.
- **Static decision.** Statically approve the exact raw-visit cap **EXACTLY
  375,962** with zero headroom, contingent on the exact receipt identity and a
  rebuilt count/hash match. The implementation is independently `ACCEPTED` by
  Sol for code and synthetic evidence only; it is not an exact-production
  `PASS`. The consumed immutable `-02` attempt is
  `STOP_C1_TRIANGLE_SINGLE_CALL` evidence; its execution authorization is
  consumed and not reusable. The consumed `-03` STOP receipt is recorded above;
  the fresh exact-02 C0 re-seal is current; the prelaunch cross-binding
  code/test checkpoint is independently `CODE_ACCEPT` for static/synthetic
  evidence only, and no `-04` approval or run exists.
- **Scope boundary.** Any future exact `PASS` can certify only frozen float64
  split PSLG segments. It cannot upgrade raw WKB/source-boundary equivalence.
  Retain `GLOBAL_BOUNDARY_EQUIVALENCE_STOP`; the consumed `-02` attempt is
  immutable `STOP_C1_TRIANGLE_SINGLE_CALL` evidence from the consumed `-02` and
  `-03` one-shots; all C1/Triangle activity,
  solver, and PowerSI remain `STOP` pending a separate new approval.

## 7. Exact next gate

`STOP_MONOLITHIC_FULL_DOMAIN_UNPROVEN` remains the full-domain decision, while
the exact Cell258 gate and conditional raw-visit cap are recorded in the [WP1
Cell258 C1 preparation checkpoint](#cell258-c1-preparation-checkpoint-wp1-authority) and the
[D117 raw-visit occupancy checkpoint](#d117-raw-visit-occupancy-checkpoint-wp1-authority).
The next-gate order is now independently reviewed exact code/synthetic evidence
-> a separate new C1 approval; the immutable `-02` and `-03` one-shots are
consumed `STOP_C1_TRIANGLE_SINGLE_CALL` evidence and are not reusable. Any future or
repeat exact operation remains `STOP`; the fresh exact-02 exact-production C0
`PASS` is consumed evidence and is not a rerun authorization. The consumed
  `-03` STOP receipt is recorded above; the prelaunch cross-binding code/test
  checkpoint is independently `CODE_ACCEPT` for static/synthetic evidence only,
  and no `-04` approval or run exists. C1/Triangle remain `STOP` pending a
  separate immutable approval. The separate exact-production C0 `PASS` remains
limited to frozen split-PSLG clearance; raw-source clearance is unknown.
Solver/FasterCap/PowerSI, extrusion, replay, network, install, staging, commit,
production, and shipping activity also remain `STOP`.

**No-solver statement:** this WP1 analysis launched no solver, `-oi`, FasterCap
process, version probe, D116 rerun, or production asset mutation.
