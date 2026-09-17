# EXP-19 — r_LF vs r_HF correlation, and D' (redefined HF DeltaL deficit)

See `WORK_DIR/exp19/EXP19_PLAN.md` for the frozen definitions (Section 1) and hypothesis
thresholds (Section 2). This script does not run the model; it only re-reads existing receipts
from EXP-15 (`exp15`, variant `j`, set A) and EXP-18b (`exp18`, variant `k2`, set B), 92 ports each.

## Run

```powershell
$env:SPD_PI_DATA_DIR = "D:\Downloads\examples"
$env:SPD_PI_WORK_DIR = "D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8 = "1"
python tools\research-claude\exp19\an19.py
```

## Outputs (WORK_DIR/exp19/)

- `exp19.json` — per-port table (r_LF, r_HF, dL_LF, dL_HF', D', eligibility, via L) for sets A and
  B, plus hypothesis stats/verdicts and `n_eligible` per set.
- `exp19_rLF_vs_rHF.png` — scatter of r_LF vs r_HF, one panel per set, symlog axes when the data
  span is wide, Spearman rho/p in the title.
- `exp19_Dprime_hist.png` — histogram of D' (eligible ports only) with the median marked, one
  panel per set.

## Result (2026-09-16 run, n=92/92 both sets, n_eligible=81 both sets)

| hypothesis | verdict (from set A) |
|---|---|
| H_e: rho(r_LF, r_HF) | **adopt** (A: rho=0.749, p=9.6e-18; B: rho=0.723, p=3.9e-16) |
| H1': D' < 0 frac / median | **reject** (A: frac=0.383, median=+135.0 pH; B: frac=0.383, median=+133.4 pH) |
| H2': rho(\|D'\|, via L) | **adopt** (A: rho=0.683, p=2.2e-12; B: rho=0.683, p=2.2e-12) |

H2' gives numerically identical stats for A and B because `breakdown_100k.rail_vias.pH` (the via
geometry breakdown) is essentially unchanged between the `j` and `k2` decap-model variants for a
given port — confirmed against a sample receipt pair, not a script bug.

Full per-port numbers, and the 10 most-negative-r_HF ports with their r_LF, are in the script's
printed markdown summary and in `exp19.json`.
