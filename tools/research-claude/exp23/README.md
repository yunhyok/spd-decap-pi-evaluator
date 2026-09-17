# EXP-23 — Anti-resonance position, height, width vs the reference (1-30 MHz)

See `WORK_DIR/exp23/EXP23_PLAN.md` for the frozen definitions (Section 1) and hypothesis
thresholds (Section 2). This script does not run the model; it only re-reads existing receipts
from EXP-15 (`exp15`, variant `j`, set A) and EXP-20 (`exp20`, variant `mk`, set B), 92 ports
each (the `exp20` directory also holds a 260804-design copy of the `*_any_mk.json` glob; the
design-id regex, same pattern as `exp19/an19.py`, filters those out).

## Run

```powershell
$env:SPD_PI_DATA_DIR = "D:\Downloads\examples"
$env:SPD_PI_WORK_DIR = "D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8 = "1"
python tools\research-claude\exp23\an23.py
```

## Method

Per port and per set (independently for model and reference): within the window
`f_s,ref < f <= 3e7` restricted to `f >= 3e6`, find the interior ladder point whose `|Z|` exceeds
both neighbours (the highest such point if several) — that is the anti-resonance peak. Width is
the `|Z| >= peak/sqrt2` interval, found by linear interpolation of `|Z|` in log10(f) between the
bracketing ladder points on each side, searched only within the same window; a side that never
drops below the threshold before the window edge gives a null width. A port is eligible when
`f_s,ref <= 5 MHz` and both model and reference have a peak in the window.

## Outputs (WORK_DIR/exp23/)

- `exp23.json` — per-port table (peak position/height/width for model and reference, the three
  ratios, `f_res_model/f_res_ref`, eligibility) for sets A and B, plus quartile stats and
  hypothesis verdicts.
- `exp23_antires.png` — three histograms (position/height/width ratio) for set A, set B overlaid
  as an outline histogram, medians marked.

## Result (2026-09-17 run, n=92/92 both sets, n_eligible=54/92 both sets)

| hypothesis | stat (set A, exp15/j) | verdict |
|---|---|---|
| H_pos: median(pos_ratio) in [0.9, 1.1] | median=1.096, Q1/Q3=1.00/1.20 (n=54) | **adopt** |
| H_h: height_ratio over/under | median=1.020, Q1/Q3=0.928/1.161 (n=54) | **indeterminate** |
| H_w: width_ratio narrow/wide | median=0.918, Q1/Q3=0.788/1.019 (n=17; width undefined for the other 37 eligible ports — one side never crosses peak/sqrt2 inside the window) | **indeterminate** |
| H_skin: height_ratio deviation B vs A | dev_A=0.0204, dev_B=0.0020 (A n=54, B n=54) | **adopt** (90% reduction, well past the 20% bar) |

Anti-resonance position is essentially correct on median (H_pos adopts), but height and width
land in the plan's indeterminate bands rather than the predicted "over"/"narrow" — the frozen
prediction in EXP23_PLAN.md Section 2 does not hold for this data. H_skin adopts strongly: the
skin term in `exp20/mk` collapses the height-ratio deviation from the reference by ~90%, far more
than the 20% bar.

The 8 ports with the largest height_ratio (set A) and their pos_ratio/width_ratio/f_s,ref are in
the script's printed markdown summary and in `exp23.json`.
