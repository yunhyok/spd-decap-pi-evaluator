# EXP-31 — scale-free element regression for the package R deficit

See `WORK_DIR/exp31/EXP31_PLAN.md` for the frozen definitions (Section 1) and hypothesis
thresholds (Section 2). This script does not run the model; it only re-reads existing receipts
from EXP-28 (`exp28`, variant `p`), tag 260729, 92 ports.

## Run

```bash
export SPD_PI_DATA_DIR="D:\Downloads\examples"
export SPD_PI_WORK_DIR="D:\Downloads\examples\analysis\claude-2026-09-15\work"
export PYTHONUTF8=1
python tools/research-claude/exp31/an31.py
```

## Outputs (WORK_DIR/exp31/)

- `exp31.json` — per-port table (y, s_planes/s_vias/s_traces/s_rem at the 100 kHz ladder point),
  the nnls element fit (point coefficients + R²) and its 1000-resample bootstrap (seed 0,
  2.5/50/97.5 percentiles of each c_el and of R²) for subset A (all 92 ports) and subset B
  (excluding the 12 ports with Re Zref/Re Zmodel < 1), the H_via and H_fit verdicts, the top-8
  |residual| ports under the A point-fit, and the share vector for the known cases (P1, P7, P14,
  P16, P18, P19; the "804" case from the plan is not present — this run is 260729 only).
- `exp31_coeffs.png` — violin/box plots of the bootstrap c_el distributions, side by side for A
  and B.

## Result (2026-09-17 run, n=92/92, tag 260729)

### A: all 92 ports

| element | c_el (point) | boot p2.5 | boot p50 | boot p97.5 |
|---|---|---|---|---|
| planes | 0.0000 | 0.0000 | 0.0000 | 0.0912 |
| vias | 1.4818 | 0.9268 | 1.4541 | 1.7579 |
| traces | 0.0000 | 0.0000 | 0.0000 | 0.7243 |
| rem | 0.1207 | 0.0272 | 0.1171 | 0.2782 |

R² (point) = 0.4558; bootstrap R² p2.5/p50/p97.5 = 0.3215/0.4655/0.6099

### B: excl. 12 ports w/ Re Zref/Re Zmodel < 1 (n=80)

| element | c_el (point) | boot p2.5 | boot p50 | boot p97.5 |
|---|---|---|---|---|
| planes | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| vias | 0.0000 | 0.0000 | 0.0000 | 0.2211 |
| traces | 1.5683 | 1.1943 | 1.5582 | 2.0045 |
| rem | 0.8118 | 0.6365 | 0.8037 | 0.9758 |

R² (point) = 0.5095; bootstrap R² p2.5/p50/p97.5 = 0.4147/0.5151/0.6126

**H_via verdict (on A): H_via adopt** — lower95(c_vias)=0.927 ≥ 0.5 and upper95(c_planes)=0.091 ≤
0.3 both hold; lower95(c_planes)=0 < 0.3 and lower95(c_rem)=0.027 < 0.3.

**H_fit verdict (on A): reject** — median bootstrap R² = 0.4655 < 0.5 threshold. Element shares
explain under half the variance of y on the full 92-port set; port-local structure (near-resonance
ports, decap placement) still dominates part of the deficit. (Note: subset B's median bootstrap R²
= 0.5151 clears the same threshold — the fit is markedly better once the 12 ports with
Re Zref/Re Zmodel < 1 are excluded, but the plan's H_fit check is defined on A.)

Interesting asymmetry: on A, `c_vias` dominates (~1.45–1.48, i.e. tripling the via-R term roughly
matches the deficit) while `c_traces` pins at 0; on B, that flips — `c_traces` dominates (~1.57–1.57)
while `c_vias` pins at 0. The 12 excluded ports (Re Zref/Re Zmodel < 1, i.e. the model already
over-predicts R there) are apparently concentrated on rails/ports where the via term carries the
correction; once they're dropped, nnls reassigns the remaining deficit to traces instead. `c_planes`
stays at/near 0 in both subsets, consistent with the plan's prediction (P18, P7 have high
s_planes and near-zero y).

Full per-port numbers, the top-8 |residual| table, and the known-case (P1/P7/P14/P16/P18/P19)
share vectors are in the script's printed markdown summary and in `exp31.json`.
