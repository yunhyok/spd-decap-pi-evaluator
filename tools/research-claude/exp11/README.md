# EXP-11 / EXP-12 — reverting the [implementation differences]

Plans: `WORK_DIR/exp11/EXP11_PLAN.md` (a/b), `WORK_DIR/exp12/EXP12_PLAN.md` (c/d). Four flags on the frozen chain, default off (default results are
bit-for-bit unchanged; `common/smoke_port18.py` and `run11.py --variant none --smoke` prove it):

- `eps_table` (variant **a**) — `exp1b.TwoSided` keeps, per C assignment, the cells and the dielectric rows,
  and exposes `c_tand_at(f)`; `model3.Model3.assemble(f)` then uses `sheet_c_tand(sh, f)` so eps_r and tand
  come from the material table at each frequency instead of the stackup `dk` and the 1 MHz tand.
- `c_all_refs` (variant **b**) — cells whose reference is a non-adjacent candidate layer (k > 0) also get the
  series dielectric C of their own gap, instead of C = 0.
- `zs_cell` (variant **c**, EXP-12) — the two-sided sheet impedance Zs2 is chosen per cell instead of per layer:
  `exp1b.TwoSided.__call__` returns the per-cell `two_sided` mask, `model3` scatters it to `sh.two_edge`
  (an edge is two-sided iff both endpoint cells are), and `run4.Model4.edge_z` uses Zs2 on those edges and
  Zs1 on the rest. The frozen layer-majority rule of `ModelB.zs_plane` is bypassed for rail sheets.
- `zs_wall` (variant **d**, EXP-12) — the return plane is no longer an ideal conductor: `TwoSided` also returns
  `wall_up`/`wall_dn` (stackup row of the conductor assigned on each side, -1 = none), `model3` keeps them per
  edge as `sh.wall_rows_edge`, and `edge_z` adds `zw` to the rail edge Zs — one wall: that layer's Zs1, two
  walls: the parallel combination, no wall: 0, and the edge takes the mean of its two endpoint cells. Complex,
  so both R and the internal L grow.

All four flags travel `Model3 -> Model4 -> ModelB` as plain `__init__` kwargs.

- `c_unit_fix` (variants **j**, EXP-13, and **jab** = j+a+b) — fixes the missing gap um->m conversion in the frozen cell C (`* 1e6`); writes to `WORK_DIR/exp13`, see `EXP13_PLAN.md`.
- EXP-14 flags (all run on top of `c_unit_fix`, variants **e1/e2/f/g/h**, receipts in `WORK_DIR/exp14`, plan `WORK_DIR/exp14/EXP14_PLAN.md`):
  - `fringe_no_thresh` (**e1**) — `Model4.edge_z` applies the fringing widening to every rail edge instead of only
    those with `weff < 5 d`; the `min(., wid)` cap stays.
  - `fringe_no_cap` (**e2**) — the 5 d threshold stays but the cap goes: `weff -> weff + 2 d` (unphysical, sensitivity only).
  - `homog_L_noG` (**f**) — the external L (and the fringing test) uses the full cell width `wid` instead of `G*wid`;
    R keeps its `1/G`.
  - `via_area_exact` (**g**) — rail-via R on plated barrels (`via_model.classify_via_conductor` -> `HOLLOW_PLATED_BARREL`)
    is scaled by `D/(D - t_p)`, `t_p = min(20 um, D/4)`, i.e. `pi D t_p -> pi (D t_p - t_p^2)`; filled microvias unchanged.
    `info["via_area_exact_scaled"]` / `info["via_total"]` count the scaled vias.
  - `via_L_twowire` (**h**) — the coax via L becomes the two-wire value `mu0 l/pi * ln(s/r)` (2x); pad links unchanged.
  - Both fringing flags and `homog_L_noG` go through `Model4.weff_of(sh)` (effective width + fringing mask), which
    `edge_z` calls; `run11.py --smoke`/receipts report `fringe_edge_fraction` and `weff_over_wid_mean` per sheet.
- `homog_face_fix` (EXP-28, variant **p** = `c_unit_fix` + `homog_face_fix`, receipts in `WORK_DIR/exp28`,
  plan `WORK_DIR/exp28/EXP28_PLAN.md`) — `homog.batched_gx` injects/extracts on the first/last column of the
  window that has any metal (k0/k1) instead of columns 0 / nx-1, so a cell-edge window whose centre-line faces
  are copper-free no longer reads G = 0 and strands the edge cells.  Windows with metal on both faces
  (k0 = 0, k1 = nx-1) are bit-identical to the frozen path.  G_rel = I*nx/ny is unchanged, so a full-height strip
  spanning m < nx columns gives nx/m, not 1.  Self-check: `run11.py --selfcheck-homog`
  (or `python exp3/homog.py`), no data files.

- `via_R_skin` (EXP-20, variants **m** = `c_unit_fix`+`via_R_skin` and **mk** = m + `zs_wall_skin_re`, receipts in
  `WORK_DIR/exp20`, plan `WORK_DIR/exp20/EXP20_PLAN.md`) — rail-via R becomes frequency dependent.
  `model3.Model3.build` keeps, per via, the conductor kind (`classify_via_conductor`: SOLID/HOLLOW, pad links NONE),
  drill D, length l, `t_p = min(20 um, D/4)` and the DC R in `self.via_skin`; `Model3.via_R(f)` returns
  `l*Re[gamma/(2 pi a sigma) J0(gamma a)/J1(gamma a)]` for SOLID (a = D/2, gamma = (1+j)/delta),
  `Re[Zs1(f, sigma, t_p)]*l/(pi D)` for HOLLOW (times the `via_area_exact` factor `D/(D-t_p)` when that flag is on)
  and the DC value for NONE; `assemble`/`breakdown` call it. Flag off -> the frozen DC array object, bit-identical.
  via L is unchanged. `--smoke`/receipts report `via_kind_counts` and `via_R_ratio_median` at 1e6/1e7/1e8 Hz.
  `run11.py --selfcheck-via` checks two synthetic vias (DC limit and 100 MHz ratio) without touching any data file.
- `via_len_surface` (EXP-32, variant **q** = `c_unit_fix` + `homog_face_fix` + `via_len_surface`, i.e. exp28/p plus the
  new flag; receipts in `WORK_DIR/exp32`, plan `WORK_DIR/exp32/EXP32_PLAN.md`) — the rail-via length convention
  changes from layer centres to layer surfaces: `ell' = |z_c(L_lower) - z_c(L_upper)| + (t_upper + t_lower)/2`.
  One length everywhere: the DC R (`estimate_via_segment_rl(length_um=...)`, its cache key carries the length),
  the coax L term and `via_skin`'s `ell_um` (so `via_R(f)` agrees). DGND stitch vias and pad links are unchanged.
  `--smoke`/receipts report `via_len_ratio` (median/mean `ell'/ell` over rail vias) and `via_kind_counts`.
  Flag off -> byte-identical to the frozen path.
- `void_fill_um` (EXP-36, variant **pv** = `c_unit_fix` + `homog_face_fix` + `void_fill_um=1500`, i.e. exp28/p plus
  the new flag; receipts in `WORK_DIR/exp36`, plan `WORK_DIR/exp36/EXP36_PLAN.md`) — reproduces PowerSI's
  Simulation (Basic) -> Special Void setting ("via hole smaller than 1500 um" excluded from the simulation, i.e.
  filled with metal).  `model3.fill_small_voids(geom, dmax)` returns a filtered copy of a layer geometry whose
  `order` no longer lists the negative primitives smaller than `dmax`; `Model3.build` applies it to every rail
  plane geometry before it goes to the `Sheet` rasteriser.  **Size measure = EXP-6 `run6.py --dmax`'s, i.e. the
  area-equivalent diameter `2*sqrt(A/pi)`** — for a circle that is exactly its diameter `2r`, for a polygon it is
  *not* the bbox max side (the plan's wording); EXP-6's rule wins because the plan calls the two identical.
  Only `order` is filtered, never the `neg_*` lists, so `homog.raster_image`'s indices stay valid.  `ex` is not
  mutated: the `TwoSided` reference-plane search still sees the real voids (the reference is an ideal conductor).
  `info["void_fill_removed"]` / the receipts and `--smoke` report the removed count per layer; `sheet_patches`
  gives the resulting cells/edges.  Flag 0 (default) -> the frozen path, bit-identical.
  Self-check: `run11.py --selfcheck-void`, no data files.
- `run11.py --ref any|gnd` (default `any`, unchanged behaviour) — `gnd` leaves model3's own `TwoSided`
  (`exp1b.TwoSided`, DGND-only search, the EXP-5 rule) instead of reassigning it to `TwoSidedAny`. Receipt
  gets `ref_mode`; the output filename's variant tag gets a `_gnd` suffix, e.g.
  `result_260729_Port18_SITE0_any_j_gnd.json` (`_any_` stays in the name -- it names the port-search
  mode, not the reference mode). `--smoke --ref gnd` prints Z and the rel diff vs `--smoke-baseline` with
  no PASS/FAIL (the baseline is a different ref mode, so bit-for-bit reproduction isn't expected).
- `run11.py --smoke-baseline exp8|exp13:j` (default `exp8`) — `exp13:j` compares against
  `WORK_DIR/exp13/result_{tag}_{port}_any_j.json` (the EXP-14 baseline) instead of the frozen exp8 receipt.
  SMOKE PASS/FAIL is printed when the variant is compared with its own receipt (`--variant none`, or
  `--variant j --smoke-baseline exp13:j`).
- `table11.py --baseline exp8|exp13:j|exp28:p` (default `exp8`) — same choice for the 7-case table, e.g.
  `table11.py --exp exp14 --variant f --baseline exp13:j`.
- `model3.Model3.info["plane_C_total_nF"]` reports the resulting total shunt plane C; `run11.py` puts it in every receipt and `--smoke` printout.

```powershell
python tools\research-claude\exp11\run11.py --variant none --smoke          # rel diff <= 1e-9 vs the exp8 receipt
python tools\research-claude\exp11\run11.py --tag 260729 --port Port18_SITE0 --variant a
python tools\research-claude\exp11\table11.py --variant a                   # markdown table + compare_a.json
python tools\research-claude\exp11\run11.py --tag 260729 --port Port18_SITE0 --variant c   # writes to WORK_DIR/exp12
python tools\research-claude\exp11\table11.py --exp exp12 --variant c       # compare_c.json in WORK_DIR/exp12
```

`run11.py` reproduces `exp8/run8.py run()` (same frequencies, gates and receipt schema) and adds `variant`,
`flags` and `dielectric_used`; it writes `result_{tag}_{port}_any_{variant}.json` to `WORK_DIR/exp11`
(none/a/b/ab) or `WORK_DIR/exp12` (c/d) and never overwrites (an existing file gets a `_HHMMSS` suffix).
Variant c adds `two_sided_edge_fraction` per rail sheet, variant d adds `wall_edge_fraction` and
`wall_thickness_um_median`; `--smoke` prints them too  Every variant adds `sheet_patches`
(per rail sheet `[cells, edges, edge-connected patches]`, reference-excluded, sheet edges only). `table11.py --exp exp11|exp12` (default `exp11`) picks the
results directory, compares the 7 cases against `docs/research-claude/2026-09-15/results/exp8/` (the baseline is
the same for both) and evaluates the plan's frozen `h_null_confirmed` / `adopt` rules.
