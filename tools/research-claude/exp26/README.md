# EXP-26 — per-decap current split and port→decap path R

Diagnostic for the EXP-25 question: why is the 100 kHz "remainder" (Re Z minus the receipt
breakdown) 20.3 mΩ at Port65_SITE1 but ~6.2 mΩ at Port19_SITE0?

`paths26.py` builds the exp13/j model (run11 variant j: cavity-wall reference, `c_unit_fix=True`,
`R11.setup()` — no model file is touched) and measures, at f = 100 kHz, per port:

* **(a)** normal solve (1 A into the port node): per-decap branch current `I_d = (V[i_d] − V_gnd)/Z_d`,
  current share `|I_d|/Σ|I_d|`, and the remainder `Σ|I_d|²·Re Z_d`, compared with
  `Re Z_port − (planes+vias+traces)` of `WORK_DIR/exp15/result_260729_{port}_any_j.json`.
* **(b)** the same Y with the decap stamps subtracted (`Y_nodecap = Y − Y_dec`), then one
  two-terminal solve per decap (+1 A at the port, −1 A at the decap rail node):
  `R_path,d = Re(V[P] − V[i_d])`. `Y_nodecap` stays non-singular through the plane-cell shunt C;
  a 1e-9 S shunt at the port is added and flagged only if a factorisation ever fails (it did not).
* **isolation test** — with the decap stamps removed, `connected_components` on the off-diagonal
  structure of `Y_nodecap` says whether each decap rail node is still in the port's component.
  A decap that is not carries ~0 A and its `R_path` (10³–10⁸ Ω) is a plane-C leakage artefact,
  so `spread` is reported both with (`spread`) and without (`spread_non_isolated`) those decaps.
* the worst non-isolated decap's solution is split into planes / vias / traces / pad links via
  `mdl.breakdown` and `run4.split_breakdown` (H_elem).

## Run

```bash
export SPD_PI_DATA_DIR="D:\\Downloads\\examples" \
       SPD_PI_WORK_DIR="D:\\Downloads\\examples\\analysis\\claude-2026-09-15\\work" \
       PYTHONUTF8=1 OMP_NUM_THREADS=4
python tools/research-claude/exp26/paths26.py            # all four ports
python tools/research-claude/exp26/paths26.py --ports Port65_SITE1 --force
```

Build is 85–145 s per port (50k–70k unknowns, ~1.3 GB peak); the five solves are ~2 s per port.
Each port is cached as `WORK_DIR/exp26/_port_{tag}_{port}.json` so the four builds can be split
over several runs; `--force` rebuilds. Outputs (only these): `WORK_DIR/exp26/exp26.json`,
the per-port caches, and a markdown summary on stdout.

## Result (tag 260729, variant j, 100 kHz)

| port | Re Z (mΩ) | remainder (a) | receipt rem. | path R total | receipt bd sum | top-1 share | spread (non-isolated) | isolated |
|---|---|---|---|---|---|---|---|---|
| Port19_SITE0 | 13.012 | 6.135 | 6.247 | 6.877 | 6.765 | 0.804 | 2.27 | — |
| Port65_SITE1 | 29.565 | 20.256 | 20.338 | 9.309 | 9.228 | 0.473 | 1.48 | C10105_1 (10 µF) |
| Port29_SITE0 | 15.637 | 6.242 | 6.356 | 9.396 | 9.281 | 0.813 | 1.44 | C10030_0 (100 nF) |
| Port75_SITE1 | 26.641 | 6.137 | 6.248 | 20.505 | 20.393 | 0.804 | 1.14 | — |

The (a) remainder reproduces the receipt remainder to 0.11–0.12 mΩ on every port; that residue is
`rail_pad_links` (0.168 mΩ), which `run4.split_breakdown` does not report and which therefore sits
in the receipt's "remainder" by construction.

Verdicts: **H_split rejected** (P65 remainder is reproduced, 20.26 mΩ, but its top-1 share is 0.47,
not ≥ 0.6, and P19's is 0.80, not ≤ 0.4), **H_spread rejected** (P19 spread 2.27 > 2; the
non-isolated spreads are 1.14–2.27 everywhere, so path R is *not* unevenly distributed).
**H_elem: rail_vias** dominates the worst non-isolated path on every port including P65 and P75.

The actual cause is neither: at Port65_SITE1 the 10 µF C10105_1 has no galvanic path to the port in
the model and draws 0 A, so the two 1 µF parts (ESR 44 mΩ) carry the whole current instead of the
8.3 mΩ 10 µF, and `Σ|I|²·ESR` rises from ~6.1 to 20.3 mΩ. Port29_SITE0 has the same defect on a
100 nF, which changes nothing because that part carried ~1 % of the current anyway. The next
pre-registration should be about why that rail node is disconnected (extraction / pad-link / via
reach), not about the path-R rules.
