# EXP-16 — R-deficit correlation diagnostics

Plan: `WORK_DIR/exp16/EXP16_PLAN.md` (frozen definitions/thresholds, do not edit).

```powershell
python tools\research-claude\exp16\corr16.py
```

Reads every `WORK_DIR/exp15/result_260729_*_any_j.json` (the 92-port cavity-wall/variant-j run; resume
duplicates like `*_any_j_HHMMSS.json` are skipped), computes per port: `dRe_100k` and the R-deficit flag
(`dRe_100k < -0.05`), the via+trace R share of total 100kHz R (`breakdown_100k`), a non-GND-reference flag,
decap count (from the cached `WORK_DIR/exp5/extract_{tag}_{port}.pkl`, `None` if not cached), and the
Re(Zref)/Re(Zmodel) ratio at 100 kHz. Writes `WORK_DIR/exp16/exp16_corr.json` (per-port table + the four
hypothesis verdicts) and `exp16_scatter.png` (2x2), and prints a markdown summary. Runs on however many
EXP-15 receipts currently exist (n as low as 2); verdicts below `MIN_N=5` (3 for H_c/H_d) report
`"indeterminate (insufficient n)"` instead of a number.

Schema note: `reference_search`'s keys are the candidate plane layers themselves (e.g.
`"Signal$L15(MAIN_POWER5)"` vs `"Signal$L20(DGND)"`, `"Signal$TOP"` with no net = not a plane) — there is
no separate up/down "sides" list in the actual receipts, contrary to the a-priori guess. The non-GND flag
is derived from the parenthesised net suffix of each `reference_search` key.
