# EXP-29 — is the R deficit proportional to path R or to the remainder?

See `WORK_DIR/exp29/EXP29_PLAN.md` for the frozen definitions (Section 1) and hypothesis
thresholds (Section 2). This script does not run the model; it only re-reads existing receipts
from EXP-28 (`exp28`, variant `p`), 92 ports.

## Run

```powershell
$env:SPD_PI_DATA_DIR = "D:\Downloads\examples"
$env:SPD_PI_WORK_DIR = "D:\Downloads\examples\analysis\claude-2026-09-15\work"
$env:PYTHONUTF8 = "1"
python tools\research-claude\exp29\an29.py
```

## Outputs (WORK_DIR/exp29/)

- `exp29.json` — per-port table (Re Zmodel/Zref, R_planes/vias/traces/path/rem, dR at the ladder
  point nearest 1e5 Hz), the M1/M2/M3 and element fits (coefficients + R²), robust dR/R_path
  ratio stats (all ports, and excluding the ports with Re Zref/Re Zmodel < 1), and the H_path /
  H_elem verdicts.
- `exp29_dR_vs_Rpath.png` — dR vs R_path scatter (log-log) with the M1 line. Not colored by decap
  count: no decap-count field exists on the exp28/p receipts (checked), so per the plan's
  "if cheap" fallback the plot is plain.

## Result (2026-09-17 run, n=92/92)

| fit | coefs | R² |
|---|---|---|
| M1: dR = α·R_path | α = -3.058 | 0.0068 |
| M2: dR = β·R_rem | β = -0.0498 | 0.2791 |
| M3: dR = α·R_path + β·R_rem | α = 0.499, β = -0.0507 | 0.2798 |
| element: dR = a_p·R_planes + a_v·R_vias + a_t·R_traces | a_p = 33.58, a_v = -9.17, a_t = -20.53 | 0.0529 |

**H_path: reject** — R²(M1)=0.007 is far below 0.8, and R²(M2)=0.279 > R²(M1). The uniform-scale
hypothesis (α≈0.5, conductivity/thickness convention) does not hold; the deficit is not explained
by path R alone. A handful of ports near a decap/port resonance at the 100 kHz ladder point
(e.g. Port46_SITE0, Port92_SITE1: |dR| > 1 Ω) dominate the OLS fit — see the printed top-8
residual table for the full list.

**H_elem: mixed** — a_v and a_t are both negative (reported explicitly, not scored for
uniformity/dominance per the plan's rule to consider only positive coefficients); a_p is the only
positive coefficient and doesn't meet the "plane" dominance test in the form specified (needs
`max(a_v, a_t) != 0` with a_p/max(a_v,a_t) > 2 — moot here since a_v, a_t < 0). Falls through to
"mixed".

Robust dR/R_path ratio: all 92 ports median 0.609 (Q1 0.213, Q3 0.965, IQR 0.752); excluding the
12 ports with Re Zref/Re Zmodel < 1 (n=80): median 0.738 (Q1 0.293, Q3 0.998, IQR 0.705). Neither
lands in a narrow band, consistent with the OLS rejection of H_path.

Full per-port numbers and the 8 largest-|residual| ports under M1 are in the script's printed
markdown summary and in `exp29.json`.
