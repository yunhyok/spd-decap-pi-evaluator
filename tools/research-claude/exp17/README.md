# EXP-17 — DeltaL(f)/DeltaR(f) spectra (10-100 MHz error structure)

Plan: `WORK_DIR/exp17/EXP17_PLAN.md` (frozen definitions/thresholds, do not edit). No model is run; this
only re-reads existing receipts.

```powershell
python tools\research-claude\exp17\dl17.py
```

Reads every `WORK_DIR/exp15/result_260729_*_any_j.json` plus the 7 known EXP-13/j baseline vs EXP-14/h
(`via_L_twowire`, 2x via L) receipt pairs for a side table. Per port: `dL(f) = Im(Zmodel-Zref)/(2 pi f)`
[pH] and `dR(f) = Re(Zmodel-Zref)` [mOhm] over all receipt frequencies; `dL_LF`/`dL_HF` medians over
1e5-1e6 Hz / 1e7-3e7 Hz and `D = dL_HF - dL_LF`; the anti-resonance `|Z|` ratio over 9e6-1.5e7 Hz (max, and
the ratio at the frequency where `|Zref|` peaks in that band); and the `dR/Re(Zref)` fraction median over
1e7-1e8 Hz plus an "R short" flag (median < 0). Via L comes from `breakdown_100k.rail_vias.pH`.

Writes `WORK_DIR/exp17/exp17_spectra.json` (per-port table, H1/H2/H3 verdicts, side table),
`exp17_dL_spectrum.png` (all ports' dL(f), log-x, clipped to +/-50 pH, median bold) and
`exp17_D_vs_viaL.png` (log-log |D| vs via L, Spearman rho in the title); prints a markdown summary. Runs on
however many EXP-15 receipts currently exist (n as low as 2); verdicts below `MIN_N=5` (3 for H2) report
`"indeterminate (insufficient n)"` instead of a number. A missing exp13/j or exp14/h receipt in the side
table is reported as `null`, not fatal.
