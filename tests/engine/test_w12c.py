"""W12-c: API ergonomics the apps asked for (plan §W12-c, no numerics changed).

Covers `DecapSite.xy/layer/capacitance_F/impedance`, `Rail.site`, `api.find_site_pair`,
`api.match_sites`, `ladder_freqs`/`unique_path` (moved to `api.py`, public now),
`receipt.mask_margin`/`attach_mask` and `Result.receipt(light=True)`.

All but one test are data-free.  The data-backed one is a cache hit (260729 already extracted by
earlier engine sessions) -- no build, no solve, well under a minute:

    pytest tests/engine -q -k "datafree or w12c"
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import numpy as np
import pytest

from spd_pi_engine import (Backend, Design, FLAGS_P, ModelOptions, attach_mask, find_site_pair,
                           ladder_freqs, match_sites, unique_path)
from spd_pi_engine.receipt import Result, mask_margin

# ---------------------------------------------------------------- ladder_freqs / unique_path
def test_ladder_freqs_shape_and_snap():
    f = ladder_freqs()
    assert f.ndim == 1 and 20 <= len(f) <= 28
    assert np.all(np.diff(f) > 0), "must be sorted, strictly increasing (deduped)"
    assert f[0] == pytest.approx(3.0e4) and f[-1] == pytest.approx(1.0e8)

    grid = np.linspace(1.0e3, 1.0e8, 50_000)         # a synthetic "reference grid" to snap to
    snapped = ladder_freqs(grid)
    assert set(np.round(snapped, 6)) <= set(np.round(grid, 6))
    assert len(snapped) <= len(f)                     # snapping only maps points onto the grid


def test_unique_path(tmp_path):
    p = tmp_path / "receipt.json"
    assert unique_path(p) == p                        # does not exist yet: unchanged

    p.write_text("x", encoding="utf-8")
    q = unique_path(p)
    assert q != p and not q.exists()
    assert q.parent == p.parent and q.suffix == ".json" and q.name.startswith("receipt_")


# ---------------------------------------------------------------- match_sites (fake rails)
def _fake_rail(site: str, refdes_list: list[str]):
    return NS(rail_net=f"SOME_NET/{site}", decaps=[NS(refdes=r) for r in refdes_list])


def test_match_sites_refdes_suffix():
    r0 = _fake_rail("0", ["C1_0", "C2_0", "C3_0"])
    r1 = _fake_rail("1", ["C1_1", "C2_1", "C4_1"])
    out = match_sites(r0, r1)
    assert out["mapping"] == {"C1_0": "C1_1", "C2_0": "C2_1"}
    assert out["unmatched"] == ["C3_0"]


def test_match_sites_rejects_unknown_rule():
    r0 = _fake_rail("0", ["C1_0"])
    r1 = _fake_rail("1", ["C1_1"])
    with pytest.raises(ValueError):
        match_sites(r0, r1, rule="geometry")


# ---------------------------------------------------------------- mask_margin / attach_mask
def test_mask_margin():
    freq = [1e5, 1e6, 1e7]
    Z = [1e-3 + 0j, 2e-3 + 0j, 4e-3 + 0j]
    mask = [(1e5, 2e-3), (1e6, 3e-3)]      # f<1e5: unconstrained; [1e5,1e6): 2e-3; >=1e6: 3e-3
    # ratios: 2e-3/1e-3=2.0, 3e-3/2e-3=1.5, 3e-3/4e-3=0.75 -> min 0.75
    assert mask_margin(freq, Z, mask) == pytest.approx(0.75)
    assert mask_margin([1e4], [1.0], mask) == float("inf")   # below the first breakpoint


def test_attach_mask():
    freq = [1e5, 1e6, 1e7]
    Z = np.array([1e-3, 2e-3, 4e-3])
    mask = [(1e5, 2e-3), (1e6, 3e-3)]
    receipt = dict(freq=freq, Z_re=list(Z), Z_im=[0.0, 0.0, 0.0])
    attach_mask(receipt, mask)
    assert receipt["mask"] == [[1e5, 2e-3], [1e6, 3e-3]]
    assert len(receipt["mask_ratio"]) == 3
    assert receipt["mask_margin"] == pytest.approx(0.75)
    assert receipt["mask_pass"] is False

    receipt2 = dict(freq=freq, Z_re=list(Z), Z_im=[0.0, 0.0, 0.0])
    attach_mask(receipt2, [(1e5, 10.0)])                      # generous mask -> PASS
    assert receipt2["mask_pass"] is True and receipt2["mask_margin"] >= 1.0


# ---------------------------------------------------------------- Result.receipt(light=True)
def _fake_model():
    """Just enough of `Model`'s surface for `Result.receipt()` (data-free)."""
    info = dict(FLAGS_P, nodes_before_prune=10, decaps_connected=1, plane_C_total_nF=1.0,
                reference_search={}, build_seconds=0.1)
    ex = dict(spd_path="fake.spd", port_name="Port1_FAKE", rail_net="NET/0",
              decaps=[{"refdes": "C1", "model_id": "M1"}],
              stackup=[{"conductivity": None, "material": "FR4", "dk": 4.0, "df": 0.02}])
    return NS(options=ModelOptions(flags=dict(FLAGS_P)), ex=ex, info=info,
             backend=Backend(solver="splu", fast=False), _cudss=None, two_sided_layers=set(), N=100)


def test_result_receipt_light_field_set():
    m = _fake_model()
    freq = np.array([1e3, 1e6, 1e7])                  # no 1e5: no breakdown machinery needed
    res = Result(model=m, freq=freq, Z=np.ones_like(freq, dtype=complex), stats=[{"f": 1e3}])

    full = res.receipt()
    dropped = ("decap_config", "reference_search", "build_info", "stats")
    for k in dropped:
        assert k in full, f"{k} missing from the default (light=False) receipt"

    light = res.receipt(light=True)
    for k in dropped:
        assert k not in light, f"{k} should be dropped by light=True"
    for k in ("decap_config_sha256", "unknowns", "wall_seconds", "numerics_id", "freq", "Z_re", "Z_im"):
        assert k in light, f"{k} must survive light=True"

    assert res.receipt() == full, "default (no light kwarg) must stay exactly as before"


# ---------------------------------------------------------------- data-backed (cache hit, <1 min)
def test_site_and_find_site_pair_260729(spd_path, cache_dir):
    """`Design.open(260729).rail("Port14_SITE0", cache)`: no solve, no cold extract (cached
    already), `Rail.site(refdes)`'s xy/capacitance_F/impedance for one refdes, and
    `find_site_pair` for the documented Port18_SITE0 -> Port64_SITE1 pair (header read only)."""
    spd = spd_path("260729")

    partner = find_site_pair(spd, "Port18_SITE0")
    assert partner == "Port64_SITE1"

    rail = Design.open(spd).rail("Port14_SITE0", cache_dir)
    assert rail.decaps, "Port14_SITE0 must have at least one decap for this check to mean anything"
    refdes = rail.decaps[0].refdes
    site = rail.site(refdes)
    assert site is rail.decaps[0]
    assert site.xy is not None and len(site.xy) == 2
    assert site.layer is None or isinstance(site.layer, str)
    assert site.capacitance_F is None or site.capacitance_F > 0
    z = site.impedance([1e3, 1e6])
    assert len(z) == 2
    with pytest.raises(KeyError):
        rail.site("not-a-real-refdes")
