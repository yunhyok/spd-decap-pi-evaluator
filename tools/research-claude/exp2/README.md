# EXP-2 — multiconductor stacked-plane model (research prototype)

| file | purpose |
|---|---|
| `extract_gnd.py` | DGND Node/Via/Trace records inside the model window (port bumps, decaps, core vias + 2 mm) → `$SPD_PI_WORK_DIR/exp2/extract_gnd.pkl` (~9 s). |
| `exp2.py` | builder + solver. Explicit column conductors (default L13, L14, L15, L16, L24, L25, L26, L27; `--all-gnd` = every DGND plane L02–L28 plus rails and L15/L26) on one grid; per-edge `Z = diag(Zs)·sq + jωμ0·G·sq`. `G_kl = s_B − max(s_k,s_l)` is the default bottom gauge, and `Builder(gauge="top"/"mid")` selects the others. `--decouple-groups` splits L13–L16 from L24–L27. It also includes the DGND R network (degree-2 chain reduction), the TOP DGND sheet, and the rail traces/vias/pads/decaps of EXP-1. Writes gates G1–G5, an R/L breakdown (column R per layer, magnetic energy per gap) and plots. |
| `probe.py` | one-frequency (1 MHz) build/factor probe used for the refinement and gauge checks: `python probe.py H FINE_H TOP_H COLAMD DECOUPLE(0/1) [subset/all] [bottom/top]`. |

It depends on `../exp1` (`model.py`, `exp1b.py`, `run_exp1.py`), the EXP-1 extract pickle and `neighbour_shapes.pkl`.

Runs used for `EXP2_REPORT.md`:

```bash
python extract_gnd.py
python exp2.py --h 400 --fine-h 200 --top-h 100 --tag _h400
python exp2.py --h 400 --fine-h 200 --top-h 100 --decouple-groups --tag _h400_decoupled
python exp2.py --all-gnd --h 400 --fine-h 200 --top-h 100 --freqs 1e3,1e4,1e5,3.02e5,1e6,1e7 --tag _allgnd_h400
```

Run under `ulimit -v 6000000`. SuperLU fill is the limit. 200/100 µm (the true h/2 of 400/200) does not fit in about 6 GB.
