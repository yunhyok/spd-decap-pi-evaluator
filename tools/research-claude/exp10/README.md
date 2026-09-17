# exp10 — is the PowerSI 1 Hz-100 kHz reference a low-order fit or solved data?

`fit_lowfreq.py` vector-fits (Gustavsen-Semlyen, real-coefficient, relative weighting 1/|Z|) each
port's PowerSI reference `Z_ii(f)` over 1 Hz-100 kHz (126 grid points) with n_p in {1,2,3,4} poles,
picks each port's best n_p, and applies the frozen FIT/SOLVED/INDETERMINATE criteria from
`EXP10_PLAN.md` SS5. If the Z-only verdict isn't FIT, it also fits `S_ii(f)` from the raw
`.s92p` Touchstone (residual `|Sfit-S|/|1+S|`) as required by SS8. Port18_SITE0 additionally gets
n_p in {6,8} sensitivity, a sliding one-decade-window residual scan (1 Hz-1 MHz), and a DC
(`Z(0)`) check. Reference data only — no PI model is built or touched.

## Run

```bash
export SPD_PI_DATA_DIR="D:\\Downloads\\examples"
export SPD_PI_WORK_DIR="D:\\Downloads\\examples\\analysis\\claude-2026-09-15\\work"
export PYTHONUTF8=1
python tools/research-claude/exp10/fit_lowfreq.py --selfcheck   # synthetic RLC+pole check, no data files
python tools/research-claude/exp10/fit_lowfreq.py               # real run, design 260729 by default
python tools/research-claude/exp10/fit_lowfreq.py --design 260729 --fit-s   # force the S-fit too
```

### EXP-10b flags (added, defaults above unchanged)

- `--band LO HI` (Hz, default `1.0 1e5`): primary band for the per-port fits. When non-default, the
  receipt is named `result_exp10b_<design>_band<tag>.json` (e.g. `band1k100k` for `1e3 1e5`) and the
  Port18 sliding-window/`Z(0)` diagnostics are skipped.
- `--complex`: also runs a complex-coefficient vector fit (`vectfit_complex`, no conjugate pairing) per
  port alongside the real-coefficient one, judges FIT_1_100k/SOLVED_1_100k/INDETERMINATE per
  `EXP10B_PLAN.md` SS4, and skips the S-fit fallback. Writes `exp10b_residual_real_vs_complex.png`
  (per-port RMS at n_p=4, real vs complex, log-log).
- `--lowfreq-diag`: no VF. Per-port linear least-squares `Y=(G+jB)+jwC` fit over 0.1-2 Hz for all three
  designs (260729, 260804, s5m6585), reporting B/G stats, negative-Re-Z ports, `Z(0)` consistency, and
  each design's `.DC_BBS_Setting` SPD line. Writes `result_exp10b_lowfreq_diag.json`.

```bash
python tools/research-claude/exp10/fit_lowfreq.py --band 1e3 1e5 --complex
python tools/research-claude/exp10/fit_lowfreq.py --lowfreq-diag
```

No `pip install` needed (plain numpy/matplotlib, Agg backend). Real run takes a few seconds and a
couple GB peak RSS (S-fit parses a 300 MB Touchstone file).

## Outputs (`WORK_DIR/exp10/`, never overwritten — a clash appends `_HHMMSS`)

- `result_exp10_<design>.json` — inputs (paths + SHA-256), params, per-port RMS/MAX/poles/verdict,
  Port18 diagnostics, grid regularity, negative-Re ports, wall time and peak RSS.
- `exp10_residual_vs_np.png` — RMS vs n_p for all 92 ports (Port18 highlighted, 1e-6 threshold).
- `exp10_window_residual.png` — Port18 sliding-window RMS vs window start f0 (log-log).
