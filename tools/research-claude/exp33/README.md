# EXP-33 — decomposing the f_res bias into ΔC (low-f) and ΔL (high-f)

`an33.py` re-reads existing receipts only (no model run). Package = `WORK_DIR/exp28/result_260729_*_any_p.json`
(92 ports); PCB = `WORK_DIR/exp30/result_s5m6585_*_any_p.json` (160 ports). Same glob + timestamp-skip
pattern as `exp29/an29.py`. PCB decap counts (N_d) come from `WORK_DIR/exp30/extract_stats.json`
(`n_decaps` per port, keyed by the same port string as the receipt's `"port"` field); package N_d is not
loaded (the plan only needs N_d for the PCB).

Per port: C_eff at the freq-ladder point nearest 100 kHz (model and ref) = −1/(2πf·Im Z), only defined
when Im Z < 0 there; L_eff at the point nearest 10 MHz (model and ref) = Im Z/(2πf). A port is eligible
for L_eff/r_L when f_res_ref < 5 MHz and Im Z(10 MHz) > 0 for both model and ref (must sit between series
resonance and anti-resonance). r_C, r_L, r_f = model/ref; consistency check
`|r_f·sqrt(r_L·r_C) − 1| <= 0.2`. PCB only: ΔL_abs = L_mod − L_ref (H), N_d·ΔL_abs (nH).

Run:

    python an33.py

Outputs `WORK_DIR/exp33/exp33.json` (per-port tables, medians/quartiles per set and per PCB rail,
consistency counts, verdicts) and `WORK_DIR/exp33/exp33_rL_rC.png` (r_L vs r_C scatter, package vs PCB,
reference lines at 1); prints a markdown summary.

## Result (2026-09-17 run)

| set | n | r_C median | r_L median (n eligible) | r_f median |
|---|---|---|---|---|
| package (260729) | 92 | 0.9997 | 0.9605 (n=80) | 1.1036 |
| pcb (s5m6585) | 160 | 1.0000 | 0.6675 (**n=2**) | 1.1358 |

**PCB L_eff eligibility is the headline caveat**: 158/160 PCB ports fail `f_res_ref < 5 MHz` (PCB
f_res_ref clusters 5–9 MHz per the EXP-30 trial receipts), so only 2 ports qualify for r_L / ΔL_abs —
far below the n≈5 floor other exp scripts (e.g. `exp16/corr16.py`) treat as the "indeterminate, not
computed" line. The PCB r_L / N_d·ΔL_abs numbers below are reported as required by the plan but are not
statistically meaningful at n=2.

**Verdicts** (frozen thresholds, EXP33_PLAN.md §2):
- **H_C: adopt.** Both sets' median r_C in [0.95, 1.05] (package 0.9997, pcb 1.0000) — C is not the
  source of the f_res bias in either design.
- **H_Lpcb: indeterminate** (n=2, see caveat above). r_L median 0.6675 < 0.85 (condition met) but
  median N_d·ΔL_abs = −3.14 nH, outside the plan's [−1.5, −0.3] nH band (condition failed).
- **H_Lpkg: reject.** Package median r_L = 0.9605 ≤ 1.0 (model L deficit, not excess) — the opposite
  direction from the plan's prediction (adopt required r_L median > 1.1).

Full per-port and per-rail numbers, the 7-case regression table (P1/P7/P14/P16/P18/P19/804-P18), and the
consistency-check counts are in `exp33.json`; see the script's printed markdown for the same in table
form.
