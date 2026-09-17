# EXP-24: site-asymmetry diagnosis (SITE0 vs SITE1 twin nets)

Frozen definitions/thresholds: `WORK_DIR/exp24/EXP24_PLAN.md` (do not edit).

## Run

```bash
export SPD_PI_DATA_DIR="D:\\Downloads\\examples" SPD_PI_WORK_DIR="D:\\Downloads\\examples\\analysis\\claude-2026-09-15\\work" PYTHONUTF8=1
python an24.py
```

No model run; reads existing exp15/j receipts (92) and exp5 extract pickles (92, loaded one at a
time -- only the needed keys are pulled out, then the pickle is dereferenced before the next load).

## Outputs (under `WORK_DIR/exp24/`)

- `exp24.json` -- per-pair table (46 pairs: Port k_SITE0 <-> Port k+46_SITE1) with names, rail
  nets, extract counts for both sites, iso flag + which counts/layers differ, s_mod, s_ref, 1 MHz
  |Z| ratios, dRe_100k (both sites), plus hypothesis verdicts and n_iso.
- `exp24_site_ratio.png` -- log-log scatter of s_mod vs s_ref (iso pairs filled, non-iso hollow,
  reference lines at 1).

## Result (2026-09-17 run)

46 pairs, 12 extract-isomorphic (34 non-iso, mostly differing in rail_nodes/vias/traces counts --
listed in the script's printed summary and in `exp24.json`).

| hypothesis | stat | verdict |
|---|---|---|
| H_sym_mod | frac(\|s_mod-1\|<=0.1) = 0.333 (n=12) | indeterminate |
| H_asym_ref | frac(\|s_ref-1\|>0.2) = 0.417 (n=12) | adopt |
| H_asym_src | 8/10 top \|s_ref-1\| pairs have decaps<=10 | feed-path-dominated small rails |

H_asym_ref adopts as predicted (the reference is site-asymmetric on the isomorphic subset), but
H_sym_mod does **not** reach the pre-registered adopt threshold (it also does not reject -- stuck
indeterminate). The top pairs by \|s_ref-1\| (k=46,42,45,44,43, all 0 decaps) show s_mod nearly as
far from 1 as s_ref (e.g. k=46: s_mod=0.556, s_ref=0.552), i.e. on these particular pairs the
*model* is also asymmetric, not just the reference -- contrary to the plan's prediction that only
the reference would be. This weakens the plan's interpretation in Section 3 (which assumed model
symmetry) and should be flagged before acting on it.
