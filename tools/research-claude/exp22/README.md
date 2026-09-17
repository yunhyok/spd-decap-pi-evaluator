# EXP-22 — L(f) decomposition: reference vs model, and decap origin

See `WORK_DIR/exp22/EXP22_PLAN.md` for the frozen definitions (Section 1) and hypothesis
thresholds (Section 2). This script does not run the model; it only re-reads existing receipts
from EXP-15 (`exp15`, variant `j`, 92 ports) and decap counts cached by EXP-5
(`exp5/extract_260729_{port}.pkl`).

## Run

```bash
export SPD_PI_DATA_DIR="D:\Downloads\examples"
export SPD_PI_WORK_DIR="D:\Downloads\examples\analysis\claude-2026-09-15\work"
export PYTHONUTF8=1
python tools/research-claude/exp22/an22.py
```

## Outputs (WORK_DIR/exp22/)

- `exp22.json` — per-port table (f1, f2, L_ref/L_mod at each, rho_ref, rho_mod, dL_ref, dL_mod,
  eligibility, decap count), n_eligible, hypothesis stats/verdicts, rho_ref/rho_mod quartiles.
- `exp22_Lratio.png` — scatter of rho_ref (x) vs rho_mod (y) over eligible ports, dashed lines at
  1 on both axes, medians in the title.

## Result (2026-09-17 run, n=92, n_eligible=81)

| hypothesis | stat | verdict |
|---|---|---|
| H_r: reference L decreasing (median rho_ref < 0.9, frac<1 >= 0.7) | median=0.996, frac<1=0.519 (n=81) | **reject** |
| H_m: model L ~ constant (0.97 <= median rho_mod <= 1.03) | median=1.119 (n=81) | **reject** |
| H_dec: \|rho\|(dL_ref, decap count) < 0.3 adopt / >= 0.5 reject | rho=-0.472, p=8.6e-06 (n=81) | indeterminate |

rho_ref quartiles (Q1/median/Q3): 0.919 / 0.996 / 1.066
rho_mod quartiles (Q1/median/Q3): 0.806 / 1.119 / 1.406

This is the opposite of the plan's prediction (H_r adopt, H_m adopt): reference L is
~flat/mixed between f1 and f2 (median ratio ~1.00, roughly half the ports go up and half down),
while it is the **model** L that swings the most between f1 and f2 (median rho_mod=1.12, IQR
0.81-1.41 vs. reference IQR 0.92-1.07). Per EXP22_PLAN.md Section 3, H_r reject means EXP-19's D'
is not well explained by reference-L roll-off; the D' metric's antiresonance-proximity instability
noted there is the more likely driver, and it should not be relied on as a measure of reference-L
frequency dependence.
