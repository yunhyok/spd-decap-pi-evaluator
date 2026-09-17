# EXP-30 — s5m6585 (held-out, 160 ports) first contact

Goal: get the frozen EXP-28 `p` model (`c_unit_fix` + `homog_face_fix`) to build on the third
design, `s5m6585_32p_260414_length3_1.spd`, and survey what its ports actually contain.
Outputs: `WORK_DIR/exp30/`.

## Code change

`exp1/model.py` `Stack.is_gnd` only matched `"(DGND)" in name`, which is the 260729/260804
naming (`Signal$L02(DGND)`). s5m6585 names conductor layers `Plane$IN43_DGND` /
`Plane$IN44_VCC26`, so no layer was GND: `Stack.nearest_gnd` returned `[]` and
`exp1b.TwoSided.fallback` died on `min(())`. `is_gnd` now also accepts a `_DGND` suffix and a
`$`-tail equal to `DGND`; the original test is still first and no 260729/260804 layer name
changes classification (checked over both stackups), so those receipts are bit-identical
(`common/smoke_port18.py` → SMOKE PASS, rel diff 5.8e-12).

`exp15/runall15.py --extract-only` runs only `pipeline.prepare(tag, port)` per port (one
subprocess each, `--jobs` in parallel) and writes `{outdir}/extract_stats.json`:
`rail_net, n_rail_nodes, n_rail_vias, n_rail_traces, n_decaps, rail_layers, prepare_seconds`
per port. No model build. Failed ports are retried serially — `prepare` rewrites the shared
`shapes_{tag}.pkl` and a concurrent reader can hit a half-written file (8/160 on the cold pass).

`exp1/extract.py` gained `subckt_fallback_models()` — see *Decap models* below.

## Decap models: the `xcall` + nested `.SUBCKT` flavour

`spd._parse_partial_circuits` wraps a `.PartialCkt` body in `.SUBCKT name n1 n2 … .ENDS` and
hands it to `models/spice.py:parse_passive_subcircuit`. s5m6585 writes its Murata ladders one
level deeper:

```
.PartialCkt CAP_1005_0603-100N_100N ExtNode = 1 2
xcall 1 2 sub_GRM155R61E104KA87_DC0V_25degC
.SUBCKT sub_GRM155R61E104KA87_DC0V_25degC port1 port2
 C1 port1 11 9.67e-8
 …
.ENDS
.EndPartialCkt
```

so the wrap produces a nested `.SUBCKT` and the product parser rejects it
(`PARTIAL_MODEL_UNSUPPORTED: nested .SUBCKT is unsupported`). 2432 of the 2560 two-port
components on this board referenced one of those two names, so every port extracted 0 decaps.

`extract.subckt_fallback_models(data, start, end, canonical, empty)` re-scans the PartialCkt
region for names the product parser produced no model for, and for each one that is
`xcall a b SUB` + a matching `.SUBCKT SUB p1 p2 … .ENDS` it lifts the **inner** subcircuit out,
renames it to the PartialCkt name and orders its two ports to follow the PartialCkt `ExtNode`
order (`xcall` arg *i* ↔ inner port *i*), then feeds that to the **same**
`parse_passive_subcircuit`. So the element grammar, SPICE suffixes, leak resistors
(`R100 port1 11 5.00e+9`), validation and the MNA `impedance()` solver are all the product's —
no second evaluator, and the result is a `PassiveSubcircuitModel`, already picklable.
Anything the fallback cannot read is left skipped and printed. Empty bodies
(`CAP_2012-NULL_NULL`) are not touched: `empty` is passed in and they stay `no_2port_model`.

The other s5m6585 flavour, a single ideal element (`C 1 2 1u`, `C 1 2 220n`), already parses on
the standard path — no code needed, it just was not on any of the 5 rails under test.

Each decap row now carries `model_source`: `subckt` (fallback), `ideal` (one R/L/C between the
ext nodes) or `standard` (flat multi-element ladder parsed by the product path).

Checks:

* 260729 `Port18_SITE0` re-extracted fresh — 421 decaps, same 3 model ids, `impedance([1e5,1e6])`
  bit-identical to the cached `exp5/extract_260729_Port18_SITE0.pkl`, decap rows identical apart
  from the new `model_source` key (all `standard`). `common/smoke_port18.py` → SMOKE PASS,
  rel diff 5.8e-12, err 2.69 %.
* Cross-check: the 45 element lines of 260729 `CAP_1608_10UF` re-wrapped as an `xcall` +
  nested-`.SUBCKT` PartialCkt and run through the fallback give |ΔZ|/|Z| ≤ 5.3e-15 at
  1e5/1e6/1e7 Hz vs the model the standard path builds.

## What s5m6585 looks like

160 ports, 5 rails × 32 (`ADC_AVDD08_LO`, `ADC_DVDD08_CORE`, `ADC_DVDDQ12`, `ADC_DVDDQ18`,
`ADC_IO_BUCK13`), all on one `Plane$IN5x_VCCnn` layer each, 10 distinct rail planes ×16 ports.
Per port: 28–53 rail nodes, 18–32 rail vias, 1–9 rail traces.

Decaps after the fallback (`--extract-only`, 160/160 ok, 2m12 wall at `--jobs 8`):

| rail | ports | decaps/port |
|---|---|---|
| ADC_AVDD08_LO | 32 | 7 |
| ADC_DVDDQ18 | 32 | 5 |
| ADC_DVDD08_CORE | 32 | 3 |
| ADC_DVDDQ12 | 32 | 3 |
| ADC_IO_BUCK13 | 32 | 0 |

576 decap rows total, `model_source` **576 subckt / 0 ideal / 0 standard**
(448 × `CAP_1005_0603-100N_100N`, 128 × `CAP_1005_0603-100P_100P`). Still skipped as
`no_2port_model`: `CAP_2012-NULL_NULL` (128, empty body — correct), `FLEX_POGO200` and
`S5M6585_32P_LGA_10875` (connector / package, not two-port). `ADC_IO_BUCK13` genuinely carries
only NULL caps.

## Trial receipts

| port | rail | decaps conn. | err@1 MHz | dRe@100k (mΩ) | f_res model/ref (MHz) | wall | peak RSS |
|---|---|---|---|---|---|---|---|
| Port1_U1_0 | ADC_AVDD08_LO/0 | 7/7 | 0.93 % | −1.33 | 7.285 / 6.272 | 164 s | 772 MB |
| Port10_U1_0 | ADC_AVDD08_LO/9 | 7/7 | 1.52 % | −2.20 | 5.953 / 4.922 | 357 s | 1384 MB |

Port1_U1_0 before the fallback (0 decaps): purely capacitive, no resonance in band, err@1 MHz
≈ 212×. Nothing was tuned; the `p` variant flags are unchanged.
