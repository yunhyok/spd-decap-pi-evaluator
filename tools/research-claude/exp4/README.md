# EXP-4 — internal-inductance audit of the sheet surface impedance

`run4.py` subclasses `exp3/model3.Model3` and replaces only `edge_z` and the trace Zs:

- `--which ab`: builds the S2 model once and solves modes `orig`, `a` (Re Zs only), and `b` (symmetric two-sided ½·Zc·coth(γt/2) on L14/L25) at the ladder frequencies → `/home/claude/work/exp4/result_S2_*.json`
- `--which c`: S3 (explicit GND sheets, 400/100 µm) with `b` on the rails, GND sheets R only × 6/13 → `result_S3_c.json` (~40 min, 4.2 GB)
- `--which s4b|s4c`: 1 MHz grid check with the fine box and TOP at 25 µm → `result_S4_*.json`

Report: `/home/claude/work/exp4/EXP4_REPORT.md`.
