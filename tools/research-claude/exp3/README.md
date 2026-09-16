# EXP-3 — ablation ladder on the EXP-1b base (research prototype)

| file | purpose |
|---|---|
| `homog.py` | PIL rasterisation of ordered PowerSI booleans, with traces and pads drawn as metal. Batched Jacobi-CG conductance of centre-to-centre windows, giving the per-edge G (full sheet = 1). |
| `model3.py` | `Model3`, built from homogenised `Sheet`s (S1). Takes `fringe=True` (S2) and `gnd=extract` for the explicit R-only DGND sheets and network (S3). Also provides breakdown by layer/class. |
| `extract_gnd3.py` | DGND nodes/vias/traces over the whole rail-plane extent → `$SPD_PI_WORK_DIR/exp3/extract_gnd3.pkl`. |
| `run3.py` | Runs `--step S0\|S1\|S2\|S3` at the ladder frequencies. `--sweep` gives the standard sweep plus gates; `--freqs 1e6 --h/--fine-h/--top-h` runs the S4 grid checks. |
| `summarize.py` | Prints the ablation markdown tables. |

It reuses `../exp1` (`model.py`, `exp1b.py`, `run_exp1.py`) plus the EXP-1 pickles (`extract_port18.pkl`, `neighbour_shapes.pkl`). S0 is `model.build_model` with the EXP-1b `TwoSided` reference.

```bash
python extract_gnd3.py
for s in S0 S1 S2; do python run3.py --step $s; done
python run3.py --step S3                                   # ~35 min, 4.2 GB (GND sheets 400/100 um)
python run3.py --step S2 --sweep
python run3.py --step S2 --freqs 1e6 --fine-h 25 --top-h 25 --tag _S4_fine25
python run3.py --step S2 --freqs 1e6 --h 100 --fine-h 25 --top-h 25 --tag _S4_h100_fine25
```

Run S3 alone and under `ulimit -v 6000000`. GND sheets at 200/50 µm (1.31 M unknowns) exceed SuperLU memory.
