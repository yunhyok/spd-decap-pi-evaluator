# EXP-34: does Im(Z) zero-crossing f0 beat min|Z| f_res as the G4 gate metric?

See `WORK_DIR/exp34/EXP34_PLAN.md` for the frozen definitions/thresholds (do not edit that
file). This experiment reads existing receipts only; no model is run, no receipts are
modified.

```
export SPD_PI_DATA_DIR="D:\Downloads\examples" SPD_PI_WORK_DIR="D:\Downloads\examples\analysis\claude-2026-09-15\work" PYTHONUTF8=1
python an34.py
```

## Inputs

- Set A: `WORK_DIR/exp28/result_260729_*_any_p.json` (92 ports, package)
- Set B: `WORK_DIR/exp32/result_260729_*_any_q.json` (92 ports, package, variant q)
- Set C: `WORK_DIR/exp30/result_s5m6585_*_any_p.json` (160 ports, PCB)
- Reference full-grid Z: `paths.ref_npz(tag)` per design (`260729` or `s5m6585`), `freq`
  (already Hz -- verified against the npz's own `Zfull_sel_freq`/`Zfull_sel_idx`, e.g.
  `freq[100] == 1e3` Hz), `Zdiag` (complex, freq x port), `port_names` (`"Port..::Rail"`,
  matched on the `Port..` prefix against each receipt's `port` field).

## What it computes (per port, per set)

- `r_fres = f_res_model/f_res_ref` from the receipt's own `f_res_model`/`f_res_ref` fields.
- `f0`: first negative-to-positive crossing of Im Z within [3e5, 3e7] Hz, log-f linear
  interpolation between the bracketing samples -- model on the receipt's ladder
  (`freq`/`Z_im`), reference on the full grid (`ref freq`/`Zdiag.imag`). `r_f0 = f0_mod/f0_ref`.
- Conditioning: `r_C` from 100 kHz C_eff (model/ref, nearest ladder point, same idx for both
  since the receipt's `Zref_im` is already sampled on the ladder); `r_L` from L_eff at the
  ladder point nearest to `min(2*f0_ref, 3e7)` (both model and ref Im Z must be > 0 there);
  `r_pred = (r_L*r_C)^(-1/2)`. Eligible when `f0_ref < 15e6` and both L_eff > 0.
- Conditioning score for X in {fres, f0} = fraction of eligible ports with
  `|r_X - r_pred| <= 0.1`.
- Existence rate = fraction of ports where f0 is defined on both model and ref sides.
- G4 old = the receipt's own `ladder_gates.PASS.G4`. G4 new (informational, always computed) =
  `ladder_gates.G4_max_rel_err < 0.2` AND `|r_f0 - 1| <= 0.1`.

## Verdict rule (frozen, EXP34_PLAN.md Section 2)

Adopt f0 as the G4 f_res-gate stand-in iff all three sets have existence >= 0.8 AND
`cond(f0) - cond(fres) >= 0.1`; otherwise keep the current G4 definition (numbers still
reported).

## Result (this run)

| set | n | existence(f0) | cond(fres) | cond(f0) | gain |
|---|---|---|---|---|---|
| A | 92 | 0.891 | 0.549 | 0.720 | +0.171 |
| B | 92 | 0.891 | 0.634 | 0.768 | +0.134 |
| C | 160 | 0.800 | 0.683 | 0.706 | +0.024 |

**Verdict: keep_current.** Existence clears 0.8 on all three sets, but the conditioning gain
bar (>= 0.1 on all three) fails on set C (PCB, s5m6585/p): only +0.024. f0 is not adopted as
the G4 gate metric; the "G4 PASS new" numbers in `exp34.json` / the printed summary are kept
for reference only (what G4 would be if f0 replaced f_res), not used as the live gate.

## Outputs

- `WORK_DIR/exp34/exp34.json` -- definitions, per-set summary, per-port rows, verdict.
- `WORK_DIR/exp34/exp34_fres_vs_f0.png` -- per set, r_fres and r_f0 vs r_pred scatter with a
  y=x reference line (eligible ports only).
- stdout -- markdown summary table (existence, conditioning, r_fres/r_f0 medians, G4 PASS
  counts old vs. new).
