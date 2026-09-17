# EXP-27 — model connectivity audit (92 ports, design 260729)

Plan: `WORK_DIR/exp27/EXP27_PLAN.md` (§1 definitions, §2 thresholds — frozen).
Code: `audit27.py` (the only file; build-only, nothing is solved and no model file is touched).

## What it does

Per port it builds the exp13/j baseline model exactly like `run11.py --variant j` — via
`run11.setup()`, i.e. `R4.patch_traces("b")`, `model3.TwoSided = run8.TwoSidedAny`,
`pipeline.prepare`, `pipeline.ModelB(h=200, fh=50, top_h=50, sub 20/10/10, fringe, c_unit_fix=True)`
— and then, **without assembling or factorising anything**, builds the reference-excluded element
graph:

* edges from `mdl.sheets[*].edges` (plane cells), `mdl.traces`, `mdl.vias` (+ pad links) and
  `mdl.gnd_r` (empty in ideal-GND mode); decap stamps are *not* edges,
* endpoints mapped through `mdl.map`; `map < 0` = the reference (or a pruned node), so the edge is
  dropped — the same entries `model3.assemble` drops,
* `scipy.sparse.csgraph.connected_components` on the result.

A decap (`mdl.dec`, rail node via `mdl.map`) is **isolated** when its component differs from the
port's (`mdl.P`). This reproduces the EXP-26 reachability test on `Y - Y_dec` without a solve.

Per decap: matrix index, component id, `C = -1/(ω Im Z)` at 1 kHz from the SPICE model, ESR = `Re Z`
at 100 kHz. Per port: `n_decaps`, `n_isolated`, `C_total`, `C_isolated`, `c_iso = C_iso/C_tot`.

For every isolated decap the extract is traced (plan §1): rail node layer / xy / padstack, other rail
nodes, via endpoints (`rail_vias` up/lo) and trace endpoints (`rail_traces` s/e) within 300 µm — all
`rail_nodes` are the rail net, so every neighbour is same-net — whether the layer is a plane sheet
(`mdl.sheets`) and whether `Sheet.snap(x, y)` snaps it. Class (plan §2 H_mech):

| condition | class |
|---|---|
| via or trace endpoint within 300 µm | `missing_element` |
| else on a sheet layer but not snapped | `snap_fail` |
| else | `layout_isolated` |

Verdicts: **H_freq** (ports with ≥ 1 isolated decap, ≥ 19 → adopt), **H_corr**
(`|dRe_100k_mOhm| / Re Zref(100 kHz)` from `WORK_DIR/exp15/result_260729_{port}_any_j.json`,
c_iso > 0 vs c_iso = 0, `mannwhitneyu(..., alternative="greater")`, p < 0.05 → adopt),
**H_mech** (class histogram + the full Port65_SITE1 / C10105_1 trace).

## Usage

    export SPD_PI_DATA_DIR=... SPD_PI_WORK_DIR=... PYTHONUTF8=1 OMP_NUM_THREADS=1
    python audit27.py --selfcheck                 # graph-logic self test, no build
    python audit27.py --one Port65_SITE1          # one worker -> WORK_DIR/exp27/cache/{port}.json
    python audit27.py --jobs 7                    # 92 ports, 7 worker subprocesses, then aggregate
    python audit27.py --dry                       # what would run
    python audit27.py --ports A,B --jobs 2        # subset
    python audit27.py --component-trace Port65_SITE1   # why is that component cut off?

    python audit27.py --raster-probe Port65_SITE1 'Signal$L10(DGND)' 36062 -33480 1200

`--raster-probe PORT LAYER X Y R` builds the port and dumps the rasterised sheet `LAYER` within `R`
µm of (X, Y): every cell with its centre, h, fill, patch (components of the sheet's *own* edges) and
each incident edge's G; every rail node of that layer with the cell `Sheet.snap` gave it; a
cell-by-cell walk between the cells of nodes that ended up in different patches, flagging each step
that has no edge in the model and why (`fill == 0` raster gap vs `G <= 1e-6`); and, for the first
broken steps, a re-raster of the artwork under the two cells at the sheet's own sub-tile size with
the **fill of the two window end faces**. That last number is the one that matters:
`homog.cell_edges` computes G on the centre-to-centre window and `batched_gx` drives current between
its two end faces, so a partially filled cell whose copper does not reach its own centre line gives
an empty end face, I = 0 and G exactly 0 — the edge is dropped although the cell has metal and the
artwork is continuous. Output: `WORK_DIR/exp27/raster_probe_{port}_{layer}.json` + markdown.

`--component-trace PORT` builds the port once and dumps, for every isolated decap, its *whole*
component: node count, every node with its layer/xy (nodes with no `rail_nodes` entry are plane
sheet cells), the element count per type inside the component (sheet edges per layer / off-plane
traces / vias / pad links), every via of the component with, for each endpoint that lies on a rail
sheet layer, whether `Sheet.snap` snapped it plus the raster fill of the containing and the nearest
cell, and whether `mdl._pad_links` lists the decap's rail node at all. It reproduces
`model3.build`'s raw node numbering read-only (`rebuild_index` / `rebuild_elements`, both asserted
against `mdl.dec`, `mdl.traces`, `mdl.vias`) — no model file is touched. Output:
`WORK_DIR/exp27/component_trace_{port}.json` + a printed markdown report, ~1.5 min per port.

Resume-safe: a port with a cache file is skipped (`--force` rebuilds). Workers are
`python audit27.py --one PORT` subprocesses, logs in `WORK_DIR/exp27/logs/audit_{port}.log`.
~2 min and ~1.3 GB per port build (extracts come from the `WORK_DIR/exp5` pickles), 7 jobs is the
cap for this machine.

Output: `WORK_DIR/exp27/exp27.json` (per-port results + verdicts) and a printed markdown summary.
