"""Receipt reproduction (plan §3 (i)(ii)(iii)): the public W4 API must reproduce the frozen
research receipts.

    Design.open -> .rail(port, cache_dir) -> .build(ModelOptions) -> .solve(ladder) -> .receipt()

Nothing from `tools/research-claude/` is imported.  The only research artefacts used are the
receipts copied into `fixtures/` and the PowerSI reference npz in `SPD_PI_DATA_DIR` (plan C17/C18
keep the reference grid outside the engine, so the ladder is rebuilt here in `ladder.py`).
"""
from __future__ import annotations

import dataclasses
import functools
import json
from pathlib import Path

import numpy as np
import pytest

from ladder import LADDER, ladder_freqs, ref_of

from spd_pi_engine import Backend, Design, FLAGS_LEGACY, FLAGS_P, ModelOptions, attach_reference

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[2]
EXP8 = REPO / "docs" / "research-claude" / "2026-09-15" / "results" / "exp8"

#: `exp11/run11` / `exp28` settings -- the frozen variant p mesh (plan §2-3).
OPT = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True)

#: (ii) the 7 exp28/p cases; the default profile runs Port14 (34 k unknowns) + Port18 (275 k),
#: the other five are `slow`.
DEFAULT_PKG = {("260729", "Port14_SITE0"), ("260729", "Port18_SITE0")}
PKG_CASES = [("260729", "Port1_SITE0"), ("260729", "Port7_SITE0"), ("260729", "Port14_SITE0"),
             ("260729", "Port16_SITE0"), ("260729", "Port18_SITE0"), ("260729", "Port19_SITE0"),
             ("260804", "Port18_SITE0")]
#: (iii) PCB, held-out design, `Plane$IN43_DGND` layer naming -- the C5 regression case.
#: Port1_U1_0 is the extraction already in `WORK_DIR/engine_cache`; Port50_U1_0 (another rail,
#: ADC_DVDD08_CORE) needs a cold extract, so it is `slow`.
PCB_CASES = [("Port1_U1_0", False), ("Port50_U1_0", True)]

#: Two PCB tolerances that were fixed before the run and that Port50_U1_0 misses -- by 1.6 % on
#: the CPU and by 69 % on the GPU, both at the bottom of the ladder.  They are recorded as strict
#: xfails, NOT widened: CLAUDE.md rule 1 says a gate is not adjusted after seeing the result, and
#: strict means the suite fails again the moment the number moves either way.  Both are W5
#: findings for the owner (see docs/engine/W5_REPORT.md §5), not test bugs.
#:
#: CPU: the engine reproduces the exp30 receipt to 9.4e-11 on Port1_U1_0 (W3 gate 4) but to
#: 1.02e-09 on Port50_U1_0, at the lowest ladder point (3.02e4 Hz).  `unknowns`, `plane_C_total_nF`
#: and the frequency array all match, so the build is identical and this is the low-frequency
#: cancellation the plan §0 warns about (1e-16 -> 8e-9 when an operation order changes), amplified
#: by this rail's conditioning rather than by a code difference.
#: GPU: `IR_REPORT.md` §1 derived "1e-8 holds for f >= 1 MHz" on Port1_U1_0 alone; this rail
#: measures 1.69e-08 at 1.0 MHz, so the narrowed contract is per rail, not PCB-wide.
PCB_CPU_XFAIL = {"Port50_U1_0": "engine vs exp30 receipt is 1.02e-09 at 3.02e4 Hz, just over the "
                                "1e-9 CPU gate (Port1_U1_0 on the same design: 9.4e-11)"}
PCB_GPU_XFAIL = {"Port50_U1_0": "IR_REPORT GPU contract (<=1e-8 above 1 MHz) was derived on "
                                "Port1_U1_0; this rail measures 1.69e-08 at 1.0 MHz"}


def _pcb_params(xfail):
    return [pytest.param(p, id=p, marks=([pytest.mark.slow] if s else [])
                         + ([pytest.mark.xfail(reason=xfail[p], strict=True)] if p in xfail else []))
            for p, s in PCB_CASES]


def _param(tag, port):
    slow = (tag, port) not in DEFAULT_PKG
    return pytest.param(tag, port, id=f"{tag}-{port}",
                        marks=[pytest.mark.slow] if slow else [])


def fixture_receipt(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def _solve(spd: Path, port: str, cache_dir: Path, freq_key: tuple, gpu: bool,
           legacy: bool = False) -> dict:
    """Build + solve one case once per session (several tests ask for the same case)."""
    backend = Backend(solver="cudss", fast=True) if gpu else Backend()
    opt = dataclasses.replace(OPT, flags=FLAGS_LEGACY) if legacy else OPT
    rail = Design.open(spd).rail(port, cache_dir)
    mdl = rail.build(opt, backend)
    res = mdl.solve(np.array(freq_key, float), verbose=False)
    return dict(freq=res.freq, Z=res.Z, unknowns=mdl.N, receipt=res.receipt(),
                two_sided=sorted(mdl.two_sided_layers), plane_C=mdl.info["plane_C_total_nF"])


def _case(spd_path, ref_npz, cache_dir, tag, port, fixture, gpu=False, legacy=False):
    """Resolve the data, rebuild the receipt's frequency grid, solve.  Returns (got, fref, zref)."""
    spd = spd_path(tag)
    fref, zref = ref_of(ref_npz(tag), port)
    if legacy:
        freqs = np.array([fref[int(np.argmin(abs(fref - 1e6)))]])
    else:
        freqs = ladder_freqs(fref)
        assert np.array_equal(freqs, np.array(fixture["freq"])), \
            "the ladder does not match the fixture freq array -- the reference grid changed"
    got = _solve(spd, port, Path(cache_dir), tuple(float(f) for f in freqs), gpu, legacy)
    if not legacy:
        assert np.array_equal(got["freq"], np.array(fixture["freq"]))
    return got, fref, zref


def _rel(Z, fixture):
    Zrec = np.array(fixture["Z_re"]) + 1j * np.array(fixture["Z_im"])
    return np.abs(Z - Zrec) / np.abs(Zrec)


# --------------------------------------------------------------------- (i) EXP-8 legacy baseline
@pytest.mark.slow
def test_legacy_port18_1mhz(spd_path, ref_npz, cache_dir):
    """Plan §3 (i): 260729 Port18, `FLAGS_LEGACY`, splu, fast off, the single reference frequency
    nearest 1 MHz, against the committed `results/exp8/result_260729_Port18_SITE0_any.json`.

    This is the frozen EXP-8 model (S2 + (b) + cavity wall).  `slow`: the build is ~5 min on CPU.
    """
    fixture = json.loads((EXP8 / "result_260729_Port18_SITE0_any.json").read_text(encoding="utf-8"))
    got, _, _ = _case(spd_path, ref_npz, cache_dir, "260729", "Port18_SITE0", fixture, legacy=True)
    k = int(np.argmin(abs(np.array(fixture["freq"]) - 1e6)))
    z_rec = complex(fixture["Z_re"][k], fixture["Z_im"][k])
    rel = abs(got["Z"][0] - z_rec) / abs(z_rec)
    assert got["unknowns"] == 261124 == fixture["unknowns"]
    assert rel <= 1e-9, f"rel {rel:.3e} vs the EXP-8 receipt"


# ------------------------------------------------------------------- (ii) variant p, 7 fixtures
@pytest.mark.parametrize("tag,port", [_param(t, p) for t, p in PKG_CASES])
def test_variant_p_cases(spd_path, ref_npz, cache_dir, tag, port):
    """Plan §3 (ii): the exp28/p receipts on the CPU (splu, fast off).  max |dZ|/|Z| <= 1e-9.

    Also gates the structural numbers a wrong mesh would move: `unknowns`, `plane_C_total_nF`
    (bit equality) and `two_sided_layers`.
    """
    fixture = fixture_receipt(f"exp28/result_{tag}_{port}_any_p.json")
    got, _, _ = _case(spd_path, ref_npz, cache_dir, tag, port, fixture)
    assert got["unknowns"] == fixture["unknowns"]
    assert got["plane_C"] == fixture["plane_C_total_nF"]
    assert got["two_sided"] == list(fixture["two_sided_layers"])
    rel = _rel(got["Z"], fixture)
    assert rel.max() <= 1e-9, f"max rel {rel.max():.3e} at f={got['freq'][rel.argmax()]:.4g} Hz"


@pytest.mark.gpu
@pytest.mark.parametrize("tag,port", [_param(t, p) for t, p in PKG_CASES])
def test_variant_p_cases_gpu(spd_path, ref_npz, cache_dir, tag, port):
    """The same 7 cases through `Backend(solver="cudss", fast=True)` on the A2000.

    Tolerance 1e-8 is the GPU accuracy contract (CLAUDE.md GPU policy, plan §2-5): cuDSS with one
    step of iterative refinement, against the CPU receipt.  Measured in W4: 4.5e-11 .. 5.6e-09.
    """
    fixture = fixture_receipt(f"exp28/result_{tag}_{port}_any_p.json")
    got, _, _ = _case(spd_path, ref_npz, cache_dir, tag, port, fixture, gpu=True)
    assert got["unknowns"] == fixture["unknowns"]
    rel = _rel(got["Z"], fixture)
    assert rel.max() <= 1e-8, f"max rel {rel.max():.3e} at f={got['freq'][rel.argmax()]:.4g} Hz"


# ------------------------------------------------------------------------- (iii) PCB, held-out
@pytest.mark.parametrize("port", _pcb_params(PCB_CPU_XFAIL))
def test_pcb_s5m6585(spd_path, ref_npz, cache_dir, port):
    """Plan §3 (iii): the held-out PCB (`Plane$IN43_DGND` naming) on the CPU, <= 1e-9.

    A different layer-name convention is what catches a C5 regression (hardcoded package layer
    names) that the 260729/260804 cases cannot see.

    W5 finding: Port50_U1_0 misses the 1e-9 gate by 1.6 % -- see `PCB_CPU_XFAIL`.
    """
    fixture = fixture_receipt(f"exp30/result_s5m6585_{port}_any_p.json")
    got, _, _ = _case(spd_path, ref_npz, cache_dir, "s5m6585", port, fixture)
    assert got["unknowns"] == fixture["unknowns"]
    assert got["plane_C"] == fixture["plane_C_total_nF"]
    rel = _rel(got["Z"], fixture)
    assert rel.max() <= 1e-9, f"max rel {rel.max():.3e} at f={got['freq'][rel.argmax()]:.4g} Hz"


@pytest.mark.gpu
@pytest.mark.parametrize("port", _pcb_params(PCB_GPU_XFAIL))
def test_pcb_s5m6585_gpu(spd_path, ref_npz, cache_dir, port):
    """The PCB on cuDSS.  Two tolerances, because the 1e-8 contract does not hold everywhere.

    `docs/engine/IR_REPORT.md`: on s5m6585 the cuDSS path stays at ~1.6e-7 near 100 kHz no matter
    how many iterative-refinement steps are taken (k = 1..4 all land on 1.56-1.59e-7), so the
    excess is not solver residual -- it is low-frequency cancellation in the assembled system that
    the GPU and the CPU round differently.  Above 1 MHz every point is <= 6.1e-9, inside the
    contract.  Hence <= 1e-8 for f >= 1 MHz (the band the model is validated in, gates G3/G4) and
    <= 1e-6 overall as a regression bound on the low-frequency band.  Use the CPU path when
    100 kHz accuracy matters.

    W5 finding: that narrowed contract was measured on Port1_U1_0 and does not carry to every PCB
    rail -- see `PCB_GPU_XFAIL`.
    """
    fixture = fixture_receipt(f"exp30/result_s5m6585_{port}_any_p.json")
    got, _, _ = _case(spd_path, ref_npz, cache_dir, "s5m6585", port, fixture, gpu=True)
    rel = _rel(got["Z"], fixture)
    hi = got["freq"] >= 1e6
    assert rel[hi].max() <= 1e-8, f"f >= 1 MHz: max rel {rel[hi].max():.3e}"
    assert rel.max() <= 1e-6, \
        f"all bands: max rel {rel.max():.3e} at {got['freq'][rel.argmax()]:.4g} Hz"


# ------------------------------------------------------- attach_reference against the receipt
def test_attach_reference_port14(spd_path, ref_npz, cache_dir):
    """Plan §4 W5 item 5: the engine receipt plus the PowerSI npz must reproduce the fixture's
    G1-G5 verdict and 1 MHz error without any research code."""
    tag, port = "260729", "Port14_SITE0"
    fixture = fixture_receipt(f"exp28/result_{tag}_{port}_any_p.json")
    got, fref, zref = _case(spd_path, ref_npz, cache_dir, tag, port, fixture)
    rec = dict(got["receipt"])
    attach_reference(rec, fref, zref)
    assert rec["ladder_gates"]["PASS"] == fixture["ladder_gates"]["PASS"]
    assert abs(rec["err_1MHz"] - fixture["err_1MHz"]) <= 1e-9


# ------------------------------------------------------------------------- (W8) set_decaps
@pytest.mark.gpu
def test_set_decaps_vs_rebuild_port14(spd_path, ref_npz, cache_dir):
    """W8: unmounting one decap with `set_decaps` must equal a build that never saw it.

    260729 Port14 (35 decaps, 34 k unknowns) on the 7 LADDER points.  Gated: the Y sparsity
    pattern (and with it the cached `YPattern` and the cuDSS plan) survives the unmount, pruning
    does not move (N stays on the all-mounted basis), and Z matches the rebuild to 1e-9.

    `gpu`, for the build only: the homogenisation of this rail is 5 s on the A2000 against 38 s on
    the CPU and the test builds twice.  Both models are then *solved* through splu -- two live
    cuDSS `DirectSolver`s in one process fault on 0.8.0.10, and the GPU path's own accuracy budget
    (1e-8) is wider than this gate.  `WORK_DIR/engine_w8` runs the cuDSS variant, one per process.
    """
    from spd_pi_engine.model import Model

    build_be = Backend(solver="cudss", fast=True)      # GPU homogenisation
    solve_be = Backend(solver="splu", fast=True)       # deterministic solve, no cuDSS plan
    rail = Design.open(spd_path("260729")).rail("Port14_SITE0", Path(cache_dir))
    fref, _ = ref_of(ref_npz("260729"), "Port14_SITE0")
    freqs = fref[sorted({int(np.argmin(abs(fref - x))) for x in LADDER})]
    victim = rail.decaps[0].refdes

    mdl = rail.build(OPT, build_be)
    mdl.backend = solve_be
    Y0 = mdl.assemble(float(freqs[0]))
    mdl.set_decaps({victim: None})
    assert mdl.decap_config[victim] is None and len(mdl.dec) == len(rail.decaps)
    Y1 = mdl.assemble(float(freqs[0]))
    assert Y1.nnz == Y0.nnz and np.array_equal(Y1.indices, Y0.indices), "the Y pattern moved"
    z_set = mdl.solve(freqs, verbose=False).Z

    ex = dict(rail.ex)
    ex["decaps"] = [d for d in rail.ex["decaps"] if d["refdes"] != victim]
    mdl2 = Model.build(ex, rail.shapes, dataclasses.replace(OPT, fine_box=rail.fine_box), build_be)
    mdl2.backend = solve_be
    z_reb = mdl2.solve(freqs, verbose=False).Z

    assert mdl2.N == mdl.N, f"pruning moved: {mdl2.N} vs {mdl.N}"
    rel = np.abs(z_set - z_reb) / np.abs(z_reb)
    assert rel.max() <= 1e-9, f"max rel {rel.max():.3e} at {freqs[rel.argmax()]:.4g} Hz"
