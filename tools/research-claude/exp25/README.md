# EXP-25: element-wise R breakdown of discordant SITE0/SITE1 twin pairs

Frozen definitions/thresholds: `WORK_DIR/exp25/EXP25_PLAN.md`. No model is run; this only
re-reads existing exp24 output, exp15/j receipts, and exp5 extract pickles.

    export SPD_PI_DATA_DIR=... SPD_PI_WORK_DIR=... PYTHONUTF8=1
    python an25.py

## What it does

1. Loads `WORK_DIR/exp24/exp24.json` pairs, computes `|ln(s_mod/s_ref)|` per pair (skipping
   missing/nonpositive `s_mod`/`s_ref`), takes the top 8 as **discordant** and the 4 smallest
   among pairs with `|ln(s_mod/s_ref)| <= 0.105` and `decaps_S0 <= 10` as **control**.
2. Per port of each selected pair: reads `WORK_DIR/exp15/result_260729_{port}_any_j.json`
   `breakdown_100k` for R at 100 kHz (planes = sum of per-layer `R_mOhm`, vias = `rail_vias.mOhm`,
   traces = `rail_traces.mOhm`), and `WORK_DIR/exp5/extract_260729_{port}.pkl` for trace/via/node
   counts, no-width trace lengths (computed from node coordinates), and port-node layer
   distribution.
3. Per pair: ΔR_el = R_el(S1) − R_el(S0) for planes/vias/traces, contribution share, dominant
   element (argmax |ΔR_el|).
4. H_geo: folds SITE0 port-pos nodes through the bounding-box centre of all rail nodes of both
   ports (x-only, y-only, both), compares median nearest-neighbour distance to SITE1 port nodes
   against the median SITE0 self-spacing.
5. Verdicts H_tr / H_w / H_geo per EXP25_PLAN.md Section 2, evaluated over the 8 discordant pairs.

## Outputs (all under `WORK_DIR/exp25/`)

- `exp25.json` — params, selected pairs (discordant + control) with per-port R/extract stats,
  per-pair ΔR/contribution/dominant/geo, and the three verdicts.
- `exp25_breakdown.png` — grouped bar chart of ΔR (planes/vias/traces) per pair, discordant vs
  control panels.
- markdown summary (pair table + verdicts) printed to stdout.
