# EXP-5 — frozen adopted model on held-out SPD and other rails

- `survey.py [spd]`: SITE0 rail survey (positive-shape layers, decap count) → `$SPD_PI_WORK_DIR/exp5/survey_*.json`.
- `pipeline.py --tag 260729|260804 --port PortN_SITE0 --freqset sweep|ladder`: extracts the port (exp1/extract.py), loads the missing neighbour-layer shapes, and runs the frozen S2 + (b) model (two-sided Zs on rail planes with a majority two-sided coarse cells). Writes the result JSON, gates and an overlay plot.
- `runall.sh`: the run order used (260729/260804 port 18 sweeps; ports 16, 19, 14, 7, 1 ladder).
- `analyze.py`: Part A (resonance ratio, ΔZ tracking) and Part B table (2-parameter ΔL/ΔC fit, correlations) → `partA_summary.json`, `partB_table.json`, `partA_delta.png`.
- `replot.py`: per-rail overlays with an auto-scaled Re axis.

Report: `$SPD_PI_WORK_DIR/exp5/EXP5_REPORT.md`.
