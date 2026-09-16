# EXP-1 — plane-pair + circuit hybrid Z(f) for one SPD port

Research prototype (not product code). Computes the self impedance of one SPD
port (default `Port18_SITE0`, rail `ADC_VDD_075_VTRIP_SRAM/0`) from the
Cadence Sigrity `.spd` with a 2-D plane-pair (M-FDM/TMM unit cell) model of the
rail planes plus circuit elements for traces, vias, pads and decap SPICE
models, and compares the full 1 kHz–100 MHz curve with the PowerSI Touchstone
reference.

## Files

| file | purpose |
|---|---|
| `extract.py` | one mmap pass over the SPD (≈18 s, ≈1.5 GB peak): port 18 +/− terminals (`.Port` block, new regex), stackup/materials, rail shape primitives (ordered booleans), rail Node/Trace/Via records, padstacks, rail decaps and their PartialCkt models. Cached to a pickle. |
| `model.py` | rasterisation, sparse nodal-admittance assembly, per-frequency `splu` solve, loss/inductance breakdown per element class. |
| `run_exp1.py` | CLI: sweep, gates G1–G5, mesh-halving check, `result.json`, `exp1_Z18.png`. |

Reused product code (read-only imports, `src/spd_decap_pi/…`):
`_core/io/spd.py` `_parse_materials` (3292), `_parse_layers` (3378),
`_parse_shapes` (2962), `_parse_padstacks` (3473), `_parse_partial_circuits`
(3576), `_parse_metadata` (3660), `_length_um` (2818);
`_core/models/spice.py` `parse_passive_subcircuit` (374) and
`PassiveSubcircuitModel.impedance` (185);
`_core/solver/mfdm.py` `copper_surface_impedance` (782, finite-thickness
surface impedance, DC limit 1/(σt));
`_core/via_model.py` `estimate_via_segment_rl` (132, R only; microvia fill
classification at 61).

## Model (what is in / out)

* Rail plane layers = every layer carrying rail shapes (L14, L20, L21, L25, TOP).
  Uniform grid `--h` per layer, a finer grid `--fine-h` inside the port
  pin-field box (port +terminal bbox + `--fine-margin`), `--top-h` for TOP pads.
  Edge impedance per square: `Zs_rail(f) [+ Zs_gnd(f)] + jωμ0·d`, d = dielectric
  gap to the adjacent `(DGND)` layer (parallel if both sides are GND; otherwise
  the nearest GND layer). Cell shunt `jωε0εr(f)A/d (1 − j tanδ)` to adjacent GND
  layers only (tabulated εr/tanδ interpolated in log f).
* Traces: `Zs·len/w + jωμ0·d·len/(w+2d)`; missing Width → layer default Width.
  Traces whose both ends sit on the same plane within 1.5 cells are dropped
  (the plane carries them).
* Vias: R from `estimate_via_segment_rl` × (1 + GND-return factor);
  L = μ0/(2π)·len·ln(s/r), s = nearest DGND node on the upper layer
  (clamped to [2r, 1 mm]).
* Pads: every same-net node inside a component/bump pad footprint is linked
  to the pad node (½ square of pad copper). Needed for the 8 × 10 µF decaps
  whose pads sit on via arrays with no Trace record.
* Decaps: 421 rail decaps, `Y(f)=1/Z_SPICE(f)` to the ideal reference.
* Port: 978 PositiveTerminal nodes shorted into one supernode, 1 A injected,
  NegativeTerminal (10 919 DGND nodes) = ideal reference.
* NOT modelled: GND conductor network (reference is ideal except variant A's
  adjacent-sheet Zs and via R factor), coupling to other power nets' planes
  (e.g. L15/L26), fringing/edge C, via C, skin effect in vias, DUT and LGA
  circuit models (LGA pins open), non-adjacent-plane magnetic coupling.

Variants: `A` = pre-registered primary (M-FDM both plates + GND via R = rail
via R); `B` = diagnosis (ideal GND return).

## Run

```bash
. /home/claude/venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1, see NEXT_SESSION_HANDOFF.md
cd tools/research-claude/exp1
python extract.py                                   # optional, run_exp1 does it if the cache is missing
python run_exp1.py --variant A --variant B --h 200 --fine-h 50 --top-h 50 --h-check 0
python conv_check.py      # mesh-halving check (use this, not run_exp1 --skip-sweep: that path
                          # compares the nearest *sweep* point, e.g. 9.12 MHz vs 10 MHz)
```

Outputs go to `$SPD_PI_WORK_DIR/exp1/` (`result.json`, `exp1_Z18.png`).

`conv_check.py` repeats the h / h/2 check (h=200/50/50 um vs 100/25/25 um) at
1 MHz, 10 MHz and every reference point 6–16 MHz and merges it into
`result.json["convergence"]` (~11 min, 2.9 GB peak).
