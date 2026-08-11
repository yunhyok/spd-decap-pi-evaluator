# Evaluation Solver Implementation Plan

> Historical implementation plan. The v0.22.0 production status and named-case
> evidence are recorded in the layer-surface validation record.

- Date: 2026-08-04
- Status: planning only; no runtime code is changed by this document
- Target: improve Evaluation Analysis accuracy and responsiveness
- Out of scope: De-cap Distribution behavior, PowerSI-derived calibration,
  installer/release work, and sign-off claims
- Research basis: [synthesized deep-research decision
  record](EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md), including independent
  review of Claude commit
  [`26d7698`](https://github.com/yunhyok/probe-card-mlo-pdn/commit/26d76982ec6965c744f062dd6213e04a9fd94a51)

## 1. Delivery rule

The current v0.17.0 modal solver remains the default and its scenario format
remains readable throughout development. New physics is introduced as a
sequence of independently falsifiable backends. A phase may merge into the
research branch when its own tests pass, but it may not become the product
default until the end-to-end promotion gates pass.

No parameter may be fitted to PowerSI. PowerSI and measurements are untouched
comparison data. Source-derived SPD geometry, stackup, material tables, via
dimensions, model assignments, and explicitly documented MLO filled-microvia
rules are the only production inputs.

The order is deliberately:

```text
comparison contract
  -> source topology certificate
  -> conservative actual-artwork layer kernel
  -> passive global MNA
  -> shared-sheet/material/via physics
  -> conditional homogenization
  -> blind validation
  -> sampling/acceleration/MOR
  -> guarded product rollout
```

The low-band uniform-admittance and causal-material ideas may run earlier as
research experiments. They are not substitutes for the topology and layer
network.

## 2. Compatibility boundary and stable entry points

The following tracked entry points remain stable while their internals acquire
new backend interfaces:

| Existing area | Planned responsibility |
|---|---|
| `src/spd_decap_pi/_core/io/spd.py` | parse source evidence without solver assumptions |
| `src/spd_decap_pi/spd_adapter.py` | build source-domain objects and topology inputs |
| `src/spd_decap_pi/_core/domain.py` | persist source facts, solver profile identity, and audit summaries |
| `src/spd_decap_pi/evaluation.py` | preserve Original/Tuned atomicity, cache identity, and connectivity preflight |
| `src/spd_decap_pi/_core/solver/evaluator.py` | select/compile a backend and return the existing evaluation result contract |
| `src/spd_decap_pi/_core/solver/modal.py` | retain the disclosed legacy rectangular solver and analytic regression cases |
| `src/spd_decap_pi/gui/main_window.py` | expose guarded profiles, progress phases, cancellation, and result provenance |
| `src/spd_decap_pi/gui/worker.py` | keep GUI-thread isolation and coalesced progress delivery |

New persisted fields must have safe defaults. Opening an old `.spdpi` must not
silently select an experimental solver. Cache entries from one backend or
material realization must never be reused by another.

## 3. Architectural contracts to define before kernels

### 3.1 Evidence and comparison contracts

Proposed immutable records:

- `PortManifest`: name, ordered index, rail, physical launch, observation node,
  reference conductor, reference impedance, renormalization, termination/open
  state, and driven-observation convention;
- `ReferenceRunManifest`: Touchstone hash, frequency grid, available PowerSI
  material/mesh/solver/convergence settings, and S-to-Y/Z conditioning report;
- `SourceEvidenceManifest`: SPD hash, parser version, stackup/material table
  hashes, geometry-asset hashes, and source-only assumption IDs;
- `TopologyCertificate`: compiler version, deterministic node/edge/face IDs,
  connectivity components, unresolved evidence, and an ownership ledger.

The validator compares complex S/Y/Z views where numerically meaningful and
reports transform condition number and backward residual. A port-manifest
mismatch fails before any metric is calculated.

### 3.2 Layer/operator contract

Each adjacent conductor gap implements one common contract:

```text
LayerGapOperator
  evidence_hash
  interface_terminal_ids
  conductor_face_ids
  material_descriptor_hash
  assemble_sparse_stamp(frequency_or_descriptor)
  apply(vector, frequency)              # optional query interface
  reduce_to_ports(port_ids)              # sparse solves, no explicit inverse
  energy_and_conservation_diagnostics()
```

The production representation is a passive sparse descriptor/stamp. A query
interface is defined early so a future iterative or matrix-free backend does
not require architectural surgery, but no specific fast method is selected
before scale benchmarks.

Every physical term has exactly one owner:

- a dielectric gap owns its transverse `G/C` field;
- a copper sheet owns its surface `R/L` operator once, even when it bounds two
  gaps;
- a via/pad/anti-pad/local field block is either the core representation, a
  replacement, or a declared `exact - core` defect;
- no block may be added merely because it was computed by a different method.

### 3.3 Fixed substrate and mutable branches

The compiled geometry, material descriptors, layer stamps, vertical
connections, and interface reduction form an immutable
`FixedSubstrateModel`. Decap models and placement states remain mutable branch
stamps. A substrate reduction may eliminate internal nodes only if the complete
set of possible device, decap, load, and observation attachment ports is
preserved.

## 4. Phase plan and exit gates

### Phase 0 — comparison contract and benchmark harness

Planned changes:

1. extend `scripts/validate_powersi_reference.py` to emit the two manifests,
   condition numbers, backward residuals, and consistent complex S/Y/Z metrics;
2. version the benchmark schema and bind every result to source, code, solver
   profile, port manifest, and material assumption hashes;
3. separate retrospective development, manufactured/oracle, and future blind
   reports; reject any report that mixes them;
4. record the current 401-point and adaptively refined 432/433-point curves as
   dense hidden truth for later sampling experiments.

Exit gate:

- a deliberate port reorder, reference change, wrong `Z0`, or source hash
  mismatch fails closed;
- existing correct manifests reproduce the current comparison metrics within
  serialization roundoff;
- no reference value is exposed to a production parameter builder.

### Phase 1 — source-faithful topology compiler

Proposed production modules:

- `_core/io/source_topology.py`: deterministic contact/connectivity compiler;
- `_core/models/topology.py`: immutable nodes, terminals, conductor faces,
  vias, traces, ties, evidence, and certificate records;
- `_core/solver/ownership.py`: one-owner ledger and double-stamp validator.

Compiler work:

1. resolve polygon/circle/trace/via contact using source tolerance and explicit
   provenance;
2. require source trace width or fail closed for electrical trace stamping;
3. resolve DGND components and tie paths, including locations, dimensions,
   layer span, and worst terminal-to-tie distance;
4. preserve shared-pad capacitor terminals and physical shared PWR/GND vias;
5. classify MLO microvias as solid copper only under the documented qualified
   geometry rule; otherwise retain the conservative barrel model or unresolved
   evidence;
6. assign stable IDs to both faces of a physical copper sheet and prove whether
   each face participates in an adjacent gap;
7. report periodic-cell candidates without yet enabling homogenization.

Exit gate:

- the named SPD compiles without losing source terminal identity;
- each modeled terminal has one deterministic path to its owning conductor;
- component counts and unresolved items reconcile to the raw source records;
- randomized record order produces an identical certificate hash;
- ambiguous contact, width, net, face, or fill evidence blocks only the affected
  rail with an actionable message.

### Phase 2 — low-band research bridge and causal-material study

This phase is research-only and contains two independent experiments.

#### 2A. Actual-artwork uniform-admittance bridge

Compare, in order:

1. the legacy rectangular constant mode;
2. scalar actual-artwork `C00` as a deliberately limited control;
3. per-gap, multi-net uniform `Y00(f)` with floating conductors Schur reduced;
4. the complete actual-artwork quasi-static operator on selected cases.

The bridge must quantify missing constant-to-high-mode `K0n` terms, demonstrate
passivity and charge conservation, and report both no-decap and loaded complex
`Zii`. A capacitance improvement alone is not an exit result.

#### 2B. Causal material candidates

Keep the source table and current clamped interpolation as controls. Fit
Djordjevic-Sarkar/wideband-Debye and generalized-Debye candidates using only
the SPD Dk/Df rows. Record fit residual, parameter identifiability,
Kramers-Kronig/positive-real checks, and bounded extrapolation uncertainty.

Exit gate:

- neither experiment reads PowerSI during model construction;
- every candidate is passive/causal over a wider audit band than the requested
  solve band;
- promotion requires loaded end-to-end improvement without worsening phase,
  resonance, or held-out rails; otherwise the experiment stays diagnostic.

### Phase 3 — conservative actual-artwork layer-domain reference path

Proposed modules:

- `_core/solver/layer_domain.py`: common operator and descriptor contracts;
- `_core/solver/triangle_layer.py`: nonuniform Delaunay/Voronoi or mixed-FEM
  assembly with exact source boundaries and finite ports;
- `research/oracles/cim_pair.py`: optional small selected-pair CIM, never
  imported by the application;
- `research/oracles/canonical_cases.py`: analytic and mesh-refined references.

Requirements:

1. mesh actual positive/negative artwork and locally refine at ports, slots,
   necks, and material discontinuities;
2. integrate circular/actual finite ports rather than point sampling;
3. use conservative incidence/dual-cell assembly so charge and energy identities
   hold by construction;
4. expose mesh convergence, reciprocity, passivity, and scaled residuals;
5. cross-check rectangles/circles analytically and selected small irregular
   pairs against at least one independently implemented SIE/FEM/CIM oracle.

CIM begins at approximately 2k-4k boundary unknowns. Its peak memory and
factorization time are measured; the earlier `2k-8k`, `<=1 GiB`, and
"seconds" forecasts are not acceptance facts.

Exit gate:

- analytic/manufactured cases converge at the expected order;
- triangle and independent oracle complex port matrices agree within a
  conditioning-aware tolerance, not only `|Z| <= 0.5 dB`;
- reciprocity, charge conservation, passive energy, and mesh refinement pass;
- the named SPD's real terminal count, mesh count, nonzeros, and memory are
  reported before choosing a production sparse backend.

### Phase 4 — passive global MNA and adjacent-layer composition

Proposed module: `_core/solver/global_mna.py`, promoted only after review of any
local research prototype.

Requirements:

1. stamp each adjacent-gap operator, conductor-face operator, via/tie branch,
   source/load port, and floating conductor into one global sparse system;
2. keep shared conductor and via nodes explicit across layer boundaries;
3. eliminate internal interfaces with sparse solves and Schur/Kron reduction;
4. never compose the branched PDN by scalar addition or an unqualified transfer
   matrix product;
5. prepare independent gaps in bounded Windows worker processes, then assemble
   deterministically in one ownership ledger;
6. separate symbolic topology from frequency-dependent values where the chosen
   sparse backend actually supports safe reuse.

Backend selection is benchmark-driven. SciPy SuperLU is the baseline; no plan
may assume CHOLMOD, PARDISO, MUMPS, or symbolic-only reuse until the dependency,
license, packaging, and Windows behavior are proven.

Exit gate:

- a synthetic multilayer case matches a monolithic reference;
- removing any one sheet/via stamp changes the ledger count exactly once;
- port reduction preserves reciprocity, passivity, and full-system residual;
- 30k/60k/100k/150k *actual assembled matrices*, where attainable, report
  `N`, `nnz(A)`, `nnz(L+U)`, fill ratio, ordering, pivot growth, factor/solve
  time, and peak RSS.

### Phase 5 — shared-sheet and constitutive physics

Planned changes:

1. add a reciprocal two-face finite-thickness copper operator with the coupled
   `coth/csch` limit only for sheets whose two faces are explicit interfaces;
2. preserve the current one-face slab as a regression and isolated-pair limit;
3. stamp the selected passive causal dielectric descriptor per physical gap;
4. model DGND ties as explicit R/L/connectivity paths when an ideal-common
   reference cannot be source-proven;
5. verify DC, thin/thick conductor, one-face, symmetric/antisymmetric face, and
   zero-loss limits.

Exit gate:

- no physical sheet is counted twice;
- limiting cases reduce to independent analytic results;
- the Hermitian loss/energy parts are nonnegative under scale-aware tolerance;
- VQPS C/R/L regime diagnostics improve without reference fitting.

### Phase 6 — intrinsic via-plane and local 3-D replacement

Proposed module: `_core/solver/via_intrinsic.py` behind the same local-block
contract as any PEEC/SIE alternative.

Requirements:

1. define via-domain and plate-domain modal splits, reference planes, port
   voltage/current normalization, pad/anti-pad geometry, and return terminals;
2. include self/mutual effects for single via, via pair, dense cluster, shared
   antipad, and filled microvia canonical cases;
3. replace the core local block or stamp `A_local,exact - A_local,core`;
4. prohibit independent addition of via L, antipad C, and spreading terms when
   the plate or intrinsic kernel already owns them;
5. validate local decomposition against independent 3-D/SIE/measurement
   canonical structures. The 92-port board cannot uniquely identify internal
   self/mutual L.

Exit gate:

- local port matrices pass reciprocity/passivity and reference-plane
  invariance checks;
- single/pair/cluster canonical cases meet the local oracle gates;
- exact-core replacement reproduces the exact block when reassembled;
- loaded board correlation improves without worsening VQPS or phase gates.

### Phase 7 — conditional perforation homogenization

Homogenization is enabled only for a compiler-certified periodic or locally
stationary interior cell family. It is never applied across a port, edge, slot,
net boundary, mixed pitch, or unresolved cell.

Experiment:

1. extract anisotropic effective `R/L/G/C` or a passive descriptor from a
   source-only periodic unit-cell solve;
2. grow the exact halo through `1/2/3/4/6/8` local pitches;
3. compare complex port operators, stored/dissipated energy, and local current
   against a fully explicit mesh;
4. choose the halo from a-posteriori convergence, not a fixed `2-3 pitch`
   assumption;
5. report the fraction of the real SPD actually eligible and the net memory/time
   benefit.

Exit gate:

- scale separation and stationarity are proven for each enabled zone;
- the explicit-vs-homogenized error stays inside the allocated local budget;
- transitions remain passive and do not create artificial resonances;
- a geometry change that invalidates the cell certificate disables
  homogenization and falls back to exact meshing without changing source
  identity.

### Phase 8 — adaptive sampling and measured acceleration choices

Adaptive frequency sampling is a sample selector, not final truth.

Requirements:

1. force band endpoints, decade anchors, metric frequencies, source material
   breakpoints, device SRFs, known/predicted resonances, and antiresonance
   brackets;
2. use Bayesian-VF, vector fitting, AAA-derived disagreement, or another
   uncertainty policy only to request the next expensive solve;
3. use one union grid for Original/Tuned and all compared rails;
4. retain independent midpoint/pole-bracket holdouts and finish with a hidden
   dense sweep before promotion;
5. compare complex error, peak frequency/Q, solve count, fit overhead, wall
   time, and peak memory.

The historic 4,139 s and later approximately 224.95 s runs are different
profiles. Neither supports a `5-10x` promise. Every speed report identifies the
exact source/code/profile hashes.

After measured matrix/operator scale is available, choose among sparse direct,
iterative preconditioning, H2/FMM, matrix-free Ewald/NUFFT, or localized direct
tails. A query/linear-operator interface exists from Phase 3, but implementation
of a particular accelerator is a separate go/no-go decision.

Exit gate:

- dense hidden truth passes all complex and feature gates for every rail and
  state;
- speedup is measured end to end, including fitting and verification;
- missed/narrow resonances or passivity failures automatically fall back to the
  dense adaptive solver.

### Phase 9 — passive MOR, cache, UI, and guarded rollout

Only after the fixed substrate has passed physics and blind gates:

1. realize frequency-dependent material, copper, via, and nonlocal blocks as a
   passive causal descriptor;
2. apply PRIMA or another passive MOR while preserving the complete mutable
   attachment-port subspace;
3. keep all decap branches outside the fixed substrate only when that preserved
   port set is complete;
4. expose `Legacy modal`, `Experimental layer network`, and eventually
   `Validated layer network` profiles with plain-language confidence and
   provenance;
5. retain one-click rollback to the legacy backend and never rewrite a scenario
   merely because a research profile was selected.

Exit gate:

- reduced and unreduced substrates agree over a dense audit grid and preserve
  passivity;
- Original/Tuned cache reuse is identity-safe;
- a frozen candidate passes retrospective and genuinely unseen validation;
- only then are version, installer, and release changes planned.

## 5. Cache and solver-profile identity

Every numerical cache key must include at least:

- SPD and geometry-asset hashes;
- parser/compiler version and `TopologyCertificate` hash;
- port-manifest and attachment-port-set hashes;
- backend algorithm and solver version;
- material source-table and causal-realization hashes;
- mesh policy, realized mesh, exact/homogenized zone, and local-oracle version
  hashes;
- ownership-ledger version;
- sparse backend, ordering/scaling policy, and tolerances;
- requested/realized frequency grid and adaptive-sampling policy;
- decap-model and Original/Tuned branch identity where mutable results are
  cached.

The fixed-substrate cache excludes mutable population state but includes the
complete eligible attachment-port set. Cache objects are immutable, checksummed,
written atomically, and discarded when the application or source identity no
longer matches.

## 6. Validation matrix

| Level | Cases | Required evidence |
|---|---|---|
| Unit/property | constitutive fits, face slab, incidence stamps, Schur reduction, local replacements | dimensions, limiting cases, symmetry, passivity, conservation, scaled residual |
| Manufactured | rectangles, circles, slots, floating islands, two/three gaps, one/pair via | analytic value plus mesh/order convergence |
| Independent oracle | selected small pairs and local via structures | complex matrix agreement with analytic, CIM/BEM/SIE/FEM/measurement truth |
| Source compiler | named SPD plus adversarial contact/width/net cases | deterministic certificate, complete reconciliation, fail-closed diagnostics |
| Retrospective board | all ten VQPS, all 92 ports, VTRIP/VINT/VCPU `/0` and `/1` | broad and regime-specific errors, phase, reciprocity, conditioning, no fitting |
| Blind | frozen code/profile predictions against unseen reference | preregistered hashes and pass/fail report |
| Performance/UI | named SPD, cold/warm cache, load/compile/solve/cancel/render | wall time, CPU/RSS, cache hit, cancellation latency, UI heartbeat |

The existing broad promotion gates remain authoritative. Claude's proposed
`C_eff/R/L/resonance/Q = 5/10/10/5/20%` values begin as diagnostics because
their measurability and reference uncertainty have not yet been established.

## 7. UI and process plan

The present application already runs operations through `FunctionWorker`,
coalesces progress events, validates stale results, and keeps scenario updates
atomic. Preserve those behaviors and add:

1. worker-side parsing, topology compilation, meshing, factorization, frequency
   solve, MOR, serialization, and plot-data preparation;
2. bounded process workers for independent CPU-heavy gap preparation on Windows,
   with BLAS thread count fixed to one inside each process;
3. concurrency chosen from measured per-gap peak RSS, not CPU count alone;
4. named progress phases with monotonic work units and visible cache-hit status;
5. cooperative cancellation checkpoints before/after mesh batches,
   factorization, frequency groups, reductions, and cache commit;
6. operation-generation and source/profile identity checks before accepting a
   result, even when cancellation races with a queued Qt signal;
7. incremental plot/table rendering and deferred optional diagnostics;
8. a visible solver name/version/profile and a clear `research`, `retrospective`,
   or `blind-validated` badge in results and exports.

Product targets remain six selected rails within 120 s after a warm substrate
cache, peak solver memory within 4 GiB, UI heartbeat at or below 250 ms, and
bounded cancellation latency. These are gates, not predictions.

## 8. Planned file-change map

| Area | Future action |
|---|---|
| `scripts/validate_powersi_reference.py` | manifests, conditioning, S/Y/Z and blind-report contract |
| `_core/io/spd.py`, `spd_adapter.py` | expose source contact/face/tie evidence without solver fitting |
| `_core/io/source_topology.py` | new deterministic compiler |
| `_core/models/topology.py` | new immutable topology/certificate schema |
| `_core/domain.py`, `scenario.py`, `scenario_io.py` | backward-compatible solver profile and cache provenance |
| `_core/solver/layer_domain.py` | new common sparse/query operator contract |
| `_core/solver/triangle_layer.py` | new actual-artwork conservative core |
| `_core/solver/materials.py` | new source-table and passive-causal realizations |
| `_core/solver/global_mna.py` | new reviewed global assembly/Schur path |
| `_core/solver/via_intrinsic.py` | new gated exact/replacement local block |
| `_core/solver/sampling.py` | new adaptive sample policy and dense verification |
| `_core/solver/evaluator.py` | backend/profile selection while preserving result API |
| `evaluation.py` | cache keys, fixed-substrate reuse, Original/Tuned atomicity |
| `gui/worker.py`, `gui/main_window.py` | process orchestration, phases, cancellation, provenance, guarded options |
| `research/oracles/*` | non-shipping CIM/FEM/SIE and benchmark tools |
| `tests/*` | unit, property, manufactured, compiler, integration, UI, and regression gates |

Any local untracked research prototype is evidence, not production code. It
must be reviewed against these contracts, rewritten or promoted intentionally,
and staged separately; its mere presence is not implementation progress.

## 9. Rollout and rollback

Planned backend IDs:

- `legacy_modal_v017`: default and regression reference;
- `research_uniform_admittance`: hidden diagnostic only;
- `research_layer_network`: guarded experimental profile;
- `validated_layer_network_v1`: created only after promotion gates.

A new backend first appears in CLI/research reports, then in a hidden developer
profile, then as a visible experimental option, and finally as a candidate
default. Each step has an explicit rollback that deletes only derived cache
objects and restores `legacy_modal_v017`; source and scenario data are never
rewritten.

## 10. Completion checklist for the future implementation task

- [ ] No runtime code is changed before the user authorizes implementation.
- [ ] Comparison and source evidence contracts are frozen first.
- [ ] Compiler failures are resolved or reported before kernel tuning.
- [ ] Every physical contribution has exactly one owner.
- [ ] Actual-artwork C improvement is not extrapolated to loaded `Zii` without
      measurement.
- [ ] Causal-material candidates use SPD data only and expose uncertainty.
- [ ] CIM, homogenization, AFS, matrix-free, and MOR remain optional until their
      individual gates pass.
- [ ] All resource and speed claims come from the named SPD and exact profile.
- [ ] UI heartbeat, cancellation, cache, and stale-result tests run with the
      numerical suite.
- [ ] A frozen candidate is preregistered before unseen validation.
- [ ] Version, installer, and GitHub release work starts only after a product
      backend is actually promoted.

## 11. Present nonclaim

This plan does not assert that the proposed solver already meets sub-milliohm
accuracy, 120 s runtime, 4 GiB memory, or sign-off requirements. It records the
smallest auditable path by which those claims could later be earned or
falsified.
