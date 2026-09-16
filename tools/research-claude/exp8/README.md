# EXP-8 — "cavity-wall (PowerSI convention)" reference rule

- `run8.py --predict`: pre-run prediction table (cell-wise d_eff under the GND-only vs any-net rule × EXP-5 per-layer L) → `$SPD_PI_WORK_DIR/exp8/prediction.json`.
- `run8.py --tag T --port P`: runs the frozen EXP-5 model with `exp1b.TwoSided` replaced by `TwoSidedAny` (nearest metal of any net except the rail itself, cell-wise) → `result_{tag}_{port}_any.json`.
- `runall8.sh`: the seven cases.
- `analyze8.py`: side-by-side table against EXP-5 (GND-only), resonance ratio, per-case overlays → `table.json`, `Z_*.png`.

This is a tool-convention mode, not a physical claim. See `$SPD_PI_WORK_DIR/exp8/EXP8_REPORT.md`.
