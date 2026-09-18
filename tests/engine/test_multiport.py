"""W10: the multi-port Z(f) matrix of several ports on ONE rail (plan §2-5, §4 W10).

    pytest tests/engine -q -k multiport            the data-free checks
    pytest tests/engine -q -k multiport --gpu      + the 260729 Port18 k=2 case (~40 s, warm cache)

The data case is a k=2 port built by splitting 260729 Port18_SITE0's 978 positive pins into two
interleaved groups.  That is the only k>1 multiport the three frozen designs can express: every
SPD port sits on its own net (92 + 92 + 160 ports, 92 + 92 + 160 rails), so no two SPD ports share
a rail -- see `docs/engine/W10_REPORT.md` §3.  It is also the strongest check available, because
shorting the two groups back together must reproduce the frozen single-port receipt exactly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

from ladder import LADDER

from spd_pi_engine import (Backend, Design, FLAGS_P, ModelOptions, group_ports_by_rail, multiport,
                           rail_groups)
from spd_pi_engine.multiport import MultiRail

FIXTURES = Path(__file__).parent / "fixtures"

#: `exp11/run11` / `exp28` settings -- the frozen variant p mesh, as in `test_reproduction.OPT`.
OPT = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True)


# --------------------------------------------------------------------- data-free
def test_multiport_datafree_grouping():
    """`group_ports_by_rail` is the C19 port enumeration turned into "who can share a model"."""
    g = group_ports_by_rail({"Port1_SITE0": "VDD/0", "Port2_SITE0": "VDD/1",
                             "Port3_SITE0": "VDD/0", "Port4_SITE0": "VSS_AUX/0"})
    assert g == {"VDD/0": ["Port1_SITE0", "Port3_SITE0"], "VDD/1": ["Port2_SITE0"],
                 "VSS_AUX/0": ["Port4_SITE0"]}
    assert list(g) == ["VDD/0", "VDD/1", "VSS_AUX/0"], "rails keep first-seen order"
    assert group_ports_by_rail({}) == {}
    # the shape `rail_groups(spd)` returns; a group of one is an ordinary single-port rail
    assert [r for r, ports in g.items() if len(ports) > 1] == ["VDD/0"]
    assert rail_groups.__module__ == "spd_pi_engine.multiport"


def test_multiport_datafree_short_ports(capsys):
    """`short_ports` re-indexes `map` / `N` / `P` and refuses a group 0 that is not the port."""
    multiport.demo()
    assert "DEMO PASS" in capsys.readouterr().out


# --------------------------------------------------------------------- data
@pytest.mark.gpu
def test_multiport_port18_pin_split(spd_path, cache_dir):
    """260729 Port18_SITE0 as a 2-port: its 978 pins split even/odd.

    Gates (fixed before the run, CLAUDE.md rule 1; measured values in
    `docs/engine/W10_REPORT.md` §4 and `WORK_DIR/engine_w10/w10_results.json`):

    * reciprocity `max |Z_ij - Z_ji| / |Z_ij|` <= 1e-8.  The network is reciprocal exactly, so
      this measures the solver: scipy.splu solves both right-hand sides from one exact LU and
      gives 1.0e-15 (Port14), while cuDSS + 1 refinement step gives 2e-11..8e-11.  1e-8 is the
      project's GPU contract (CLAUDE.md "GPU 정책"); the W10 brief's 1e-12 holds on the CPU only.
    * closure <= 1e-6: shorting the two groups back together (`1 / sum(Z^-1)`) must reproduce the
      frozen exp28/p single-port receipt.  Same rail, same pins, same `fine_box`, so this is an
      identity of the multi-port machinery, not an approximation.  Measured 2.8e-9 -- the loose
      bound is for the cancellation in the closure formula, the two groups being 99.9 % coupled.
    * each diagonal within 5 % of the single-port Z.  A diagonal drives one pin group with the
      other open, so it is NOT the single-port value; on this pin field the two are 0.14 % apart.
    * < 2 min with a warm extraction cache.
    """
    t0 = time.time()
    fx = json.loads((FIXTURES / "exp28" / "result_260729_Port18_SITE0_any_p.json")
                    .read_text(encoding="utf-8"))
    f = np.array(fx["freq"], float)
    idx = sorted({int(np.argmin(abs(f - x))) for x in LADDER})
    freqs = f[idx]
    Zref = (np.array(fx["Z_re"], float) + 1j * np.array(fx["Z_im"], float))[idx]

    rail = Design.open(spd_path("260729")).rail("Port18_SITE0", Path(cache_dir))
    pins = list(rail.ex["port_pos_nodes"])
    assert len(pins) == 978, len(pins)
    mr = MultiRail.from_rail(rail, {"P18#A": pins[0::2], "P18#B": pins[1::2]})
    assert mr.fine_box == tuple(rail.fine_box), "A + B are all the pins, so the box cannot move"

    mp = mr.build(OPT, Backend(solver="cudss", fast=True))
    assert mp.model.N == fx["unknowns"] + 1, (mp.model.N, fx["unknowns"])  # one node became two
    res = mp.solve(freqs, verbose=False)
    assert res.Z.shape == (len(freqs), 2, 2)

    assert res.reciprocity() <= 1e-8, f"reciprocity {res.reciprocity():.3e}"
    closure = np.abs(res.shorted() - Zref) / np.abs(Zref)
    assert closure.max() <= 1e-6, f"closure {closure.max():.3e} at {freqs[closure.argmax()]:.4g} Hz"
    diag = np.abs(res.diagonal() - Zref) / np.abs(Zref)
    assert diag.max() <= 0.05, f"diagonal {diag.max():.3e} vs the single-port Z"

    rec = res.receipt()
    assert rec["method"] == "multiport" and rec["ports"] == ["P18#A", "P18#B"]
    assert np.shape(rec["Z_re"]) == (len(freqs), 2, 2)
    assert rec["fine_box"] == list(rail.fine_box) and len(rec["decap_config"]) == len(rail.decaps)
    assert rec["numerics_id"] and rec["spd_sha256"] == rail.spd_sha256

    assert time.time() - t0 < 120, f"{time.time() - t0:.0f}s"


def test_multiport_site_pair_is_two_rails(spd_path):
    """The SITE0 / SITE1 pair the W10 brief expected to share a rail does not (report §3).

    Header scan only -- no extraction, no cache, no solve.  Asserted here so the day an SPD does
    put two ports on one net, this test fails and the k>1 data case can be built from real ports.
    """
    spd = spd_path("260729")
    rails = rail_groups(spd)
    assert rails["ADC_VDD_075_VTRIP_SRAM/0"] == ["Port18_SITE0"]
    assert rails["ADC_VDD_075_VTRIP_SRAM/1"] == ["Port64_SITE1"]
    assert max(len(p) for p in rails.values()) == 1, \
        {r: p for r, p in rails.items() if len(p) > 1}
    assert len(rails) == len(Design.open(spd).ports()) == 92
