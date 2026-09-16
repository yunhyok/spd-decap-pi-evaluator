# EXP-7 — plane kernel analytic check and coarse-mesh emulation

- `kernel.py`: computes the two-via loop N_sq on the unit-conductance grid for the node and disc contacts, compares it with ln(s²/(r1·r2))/(2π) for the physical r and for r_eq = h·e^(−π/2), and converts the result to L25 R and L → `kernel_check.json`.
- `coarse.py --port P --h H --variant B|C`: runs the frozen S2+(b) physics on a uniform grid H with no fine box and TOP at H. `C` adds the explicit GND sheets (EXP-4 (c)) and is very slow at coarse H.
- `runB.sh`: port 18, H = 5, 2, 1, 0.5, 0.25 mm. `runP.sh`: ports 1 and 19 at H = 1 and 5 mm, then port 18 C at 1 mm (stopped).
- `plot_h.py`: plots ΔL(h) and ΔRe(h) → `exp7_dL_dRe_vs_h.png`, `h_series.json`.

Report: `$SPD_PI_WORK_DIR/exp7/EXP7_REPORT.md`.
