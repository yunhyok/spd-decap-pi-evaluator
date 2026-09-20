"""W14-a: the mesh stage as an object (plan `docs/engine/W14_PLAN_2026-09-20.md`).

`Rail.build(...)` is now `Rail.mesh(...).model`, so the two spellings are one code path and must
give **bit-identical** Z(f) -- max |dZ| exactly 0, not "within a tolerance".  The five numeric
modules were not touched (`numerics_id` unchanged), so any difference here would come from the API
split itself.

260729 Port14_SITE0 (34 k unknowns, the default-profile package case of `test_reproduction.py`),
CPU `Backend(solver="splu", fast=False)`, the receipt mesh (h=200, fh=50, top_h=50, sub=(20,10,10),
fringe, `powersi-compatible`), 3 LADDER points including the 1 MHz gate frequency.

Cost is the two builds, not the sweep: a build is ~90 s, the cached extraction 1.1 s and a
frequency 0.3 s, so this test is 143 s on its own and 178 s inside the full default suite.  Kept in
the default profile all the same -- that suite is ~29 min (`docs/engine/W14A_REPORT.md` §5-6).

    pytest tests/engine -q -k w14a
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from spd_pi_engine import Backend, Design, FLAGS_P, MeshedRail, Model, ModelOptions

#: the exp28/p receipt mesh (`test_reproduction.OPT`)
OPT = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True)
FREQS = np.array([1.0e5, 1.0e6, 1.0e7])
BACKEND = Backend(solver="splu", fast=False)


def test_mesh_then_solve_equals_build_then_solve(spd_path, cache_dir):
    """One test, two builds: a third build just to look at `summary` would cost another ~40 s."""
    rail = Design.open(spd_path("260729")).rail("Port14_SITE0", Path(cache_dir))

    built = rail.build(OPT, BACKEND)
    assert isinstance(built, Model), "build() must keep returning the Model"
    z_build = built.solve(FREQS, verbose=False).Z

    meshed = rail.mesh(OPT, BACKEND)
    assert isinstance(meshed, MeshedRail)
    assert isinstance(meshed.model, Model) and meshed.model.options is meshed.options
    z_mesh = meshed.solve(FREQS, verbose=False).Z

    dz = np.abs(z_mesh - z_build)
    assert np.array_equal(z_mesh, z_build), f"max |dZ| {dz.max():.3e}, must be exactly 0"
    assert dz.max() == 0.0

    # the summary is what the mesh actually is, not a copy of the request that could drift
    s = meshed.summary
    assert s["h"] == 200
    assert (s["fh"], s["top_h"], s["sub"]) == (50.0, 50.0, (20, 10, 10))
    assert s["unknowns"] == meshed.model.N == built.N == 34424   # exp28/p fixture `unknowns`
    assert set(s["cells_per_sheet"]) == set(meshed.model.sheets)
    assert all(n > 0 for n in s["cells_per_sheet"].values())
