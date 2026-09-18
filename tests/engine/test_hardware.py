"""W13 — hardware detection and the sizing rules.  Data-free: no SPD, no GPU, no files.

`detect()` runs against this machine (whatever it is) and only has to come back sane.  Everything
else is checked on the two synthetic profiles of `hardware.LAPTOP` / `hardware.WORKSTATION`, which
is the whole point of making the sizing functions pure: the Threadripper/A6000 plan is testable
here, on the laptop.
"""
from __future__ import annotations

import os

import pytest

from spd_pi_engine import hardware as H


# --------------------------------------------------------------------- the real box
def test_detect_returns_a_sane_profile():
    p = H.detect()
    assert p.cpu_physical >= 1
    assert p.cpu_logical >= p.cpu_physical
    assert p.ram_total_GB > 0 and 0 <= p.ram_free_GB <= p.ram_total_GB
    for g in p.gpus:                       # may be empty -- that is a valid machine
        assert {"index", "name", "vram_total_MB", "vram_free_MB"} <= set(g)
        assert g["vram_total_MB"] > 0 and 0 <= g["vram_free_MB"] <= g["vram_total_MB"]
    assert p.describe()


def test_detect_plans_something_runnable_here():
    p = H.detect()
    plan = H.plan_sweep(p, n_ports=92, solver="auto")
    assert plan.jobs >= 1 and plan.threads >= 1
    assert plan.jobs * plan.threads <= max(p.cpu_logical, plan.jobs)


def test_demo():
    H.demo()


# --------------------------------------------------------------------- laptop (unchanged rules)
def test_laptop_sweep_jobs():
    """The A2000 laptop must still plan exactly what W7/plan 5-5 pinned by hand: 4 GPU jobs.

    The rule is `min(cpu, vram, ram, n_ports, max_jobs)` with `cpu = (physical - 1) // threads`
    and `threads = 3` for cudss / `2` for splu -- so 13 // 3 = 4 and 13 // 2 = 6 here.
    """
    assert H.plan_sweep(H.LAPTOP, 92, solver="cudss").jobs == 4
    assert H.plan_sweep(H.LAPTOP, 92, solver="auto").jobs == 4
    assert H.plan_sweep(H.LAPTOP, 92, solver="splu").jobs == 6


def test_laptop_vram_binds_on_the_biggest_port():
    """N = 1 233 161 (the 92-port sweep's biggest rail) does not fit 4 times on 8 GB."""
    small = H.plan_sweep(H.LAPTOP, 92, unknowns_estimate=275_218, solver="cudss")
    big = H.plan_sweep(H.LAPTOP, 92, unknowns_estimate=1_233_161, solver="cudss")
    assert small.limits["cpu"] == 4 and small.jobs == 4
    assert big.limits["vram"] < big.limits["cpu"] and big.jobs == big.limits["vram"]


def test_laptop_basis_and_tiles_keep_the_measured_defaults():
    b = H.plan_basis(H.LAPTOP, 275_218, 421, 27, solver="cudss")
    assert b.chunk == H.CHUNK_RHS_DEFAULT == 24        # W9's A2000 number, unchanged
    assert b.workers == 1                              # one card
    assert H.plan_chunk_tiles(H.LAPTOP) == H.CHUNK_TILES_8GB == 6_000_000


# --------------------------------------------------------------------- workstation
def test_workstation_respects_every_limit():
    w = H.WORKSTATION
    for solver in ("cudss", "splu"):
        p = H.plan_sweep(w, 92, unknowns_estimate=275_218, solver=solver)
        assert p.jobs == min(p.limits.values())
        assert p.jobs * H.THREADS_PER_JOB[solver] <= w.cpu_physical
        assert p.jobs * H.rss_per_process_MB(275_218) <= w.ram_free_GB * 1024
        if solver == "cudss":
            assert p.jobs * H.vram_per_process_MB(275_218, H.plan_chunk_tiles(w)) \
                <= w.gpus[0]["vram_free_MB"]
    assert H.plan_sweep(w, 92, solver="cudss").jobs == 10
    assert H.plan_sweep(w, 92, solver="splu").jobs == 15


def test_workstation_basis_is_frequency_parallel():
    b = H.plan_basis(H.WORKSTATION, 275_218, 421, 27, solver="splu")
    assert b.workers == 15                     # (32 - 1) // 2, below the 27 frequencies
    assert b.chunk == H.CHUNK_RHS_MAX == 256   # 512 GB buys the widest block that still pays
    assert b.chunk <= 421 + 1
    assert H.plan_basis(H.WORKSTATION, 275_218, 421, 27, solver="cudss").workers == 1
    # never more workers than there are frequencies to hand out
    assert H.plan_basis(H.WORKSTATION, 275_218, 421, 7, solver="splu").workers == 7


def test_workstation_tiles_scale_with_the_card():
    assert H.plan_chunk_tiles(H.WORKSTATION) == 6 * H.CHUNK_TILES_8GB     # 48 GB / 8 GB


# --------------------------------------------------------------------- edges
def test_no_gpu_falls_back_to_splu_with_a_note():
    headless = H.HardwareProfile(14, 20, 63.7, 42.5, ())
    p = H.plan_sweep(headless, 92, solver="cudss")
    assert p.solver == "splu" and "vram" not in p.limits
    assert any("no GPU" in n for n in p.notes)
    assert p.jobs == 6                                   # the splu rule, not the cudss one
    assert H.plan_chunk_tiles(headless) == H.CHUNK_TILES_8GB
    assert H.plan_basis(headless, 275_218, 421, 27, solver="cudss").workers == 1


def test_tiny_ram_gives_one_job():
    tiny = H.HardwareProfile(14, 20, 4.0, 1.5, H.LAPTOP.gpus)
    for solver in ("cudss", "splu"):
        p = H.plan_sweep(tiny, 92, solver=solver)
        assert p.jobs == 1 and p.limits["ram"] == 1
    assert H.plan_basis(tiny, 275_218, 421, 27, solver="splu").workers == 1


def test_one_core_one_port_still_plans_one_job():
    p = H.plan_sweep(H.HardwareProfile(1, 1, 8.0, 4.0, ()), n_ports=1, solver="splu")
    assert p.jobs == 1 and p.threads == 1
    assert H.plan_sweep(H.LAPTOP, n_ports=2, solver="splu").jobs == 2
    assert H.plan_sweep(H.LAPTOP, 92, solver="splu", max_jobs=3).jobs == 3


def test_huge_port_still_plans_one_job():
    """A rail that fits neither card nor RAM must not plan zero (or a negative) job."""
    p = H.plan_sweep(H.LAPTOP, 92, unknowns_estimate=50_000_000, solver="cudss")
    assert p.jobs == 1
    b = H.plan_basis(H.LAPTOP, 50_000_000, 421, 27, solver="cudss")
    assert b.chunk >= 1


# --------------------------------------------------------------------- properties
def test_threads_never_oversubscribe_and_never_touch_the_caller():
    before = dict(os.environ)
    for jobs in range(1, 40):
        t = H.plan_threads(H.WORKSTATION, jobs)
        assert 1 <= t.threads <= H.MAX_THREADS_PER_JOB
        assert jobs * t.threads <= H.WORKSTATION.cpu_logical or t.threads == 1
        assert set(t.env) == {"OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"}
        assert set(t.env.values()) == {str(t.threads)}
    assert os.environ == before, "the planner must never set threads for its caller (W12-a 5)"


def test_sizing_is_deterministic():
    for f, args in ((H.plan_sweep, (H.LAPTOP, 92, 275_218, "cudss")),
                    (H.plan_basis, (H.WORKSTATION, 275_218, 421, 27, "splu")),
                    (H.plan_threads, (H.LAPTOP, 4))):
        assert f(*args) == f(*args)


@pytest.mark.parametrize("N,MB", [(34_424, 40.0), (275_218, 245.0)])
def test_vram_fit_hits_the_measured_points(N, MB):
    """W12-a 2: `DirectSolver.free()` gave back 40 MB at N = 34 424 and 245 MB at N = 275 218."""
    lu = H.vram_per_process_MB(N, chunk_tiles=0) - H.VRAM_CONTEXT_MB
    assert lu == pytest.approx(MB, abs=0.5)


def test_rss_fit_matches_the_receipts():
    """W4/W7 receipts: P18 (N = 275 218) 2 085 MB, the 1.23 M port 3 749 MB."""
    assert H.rss_per_process_MB(275_218) == pytest.approx(2085, rel=0.05)
    assert H.rss_per_process_MB(1_233_161) == pytest.approx(3749, rel=0.05)


def test_homogenisation_term_is_the_cupy_batch():
    """12 live float64 work arrays of `chunk_tiles` elements in `homogenise._gx` -> 576 MB at 6e6."""
    assert (H.vram_per_process_MB(0, 6_000_000) - H.vram_per_process_MB(0, 0)) \
        == pytest.approx(576.0, abs=1.0)
