"""What this box is, and how many of it to use (W13).

Nothing in the engine may assume the development laptop (i9-12900H 14C/20T, 64 GB, RTX A2000
8 GB) any more: the same code has to plan sensibly on a Threadripper 32C/64T with 512 GB and an
RTX A6000.  So the hardware is *detected* (`HardwareProfile.detect()`) and every size is a pure
function of the profile plus a job description (`plan_sweep`, `plan_basis`, `plan_threads`,
`plan_chunk_tiles`).  The sizing functions never call `detect()` themselves, never read the
environment and never touch a card, which is what makes them unit-testable with synthetic
profiles (`tests/engine/test_hardware.py`).

    prof = HardwareProfile.detect()
    plan = plan_sweep(prof, n_ports=92, unknowns_estimate=275_218, solver="cudss")
    plan.jobs, plan.threads, plan.env        # 4, 5, {"OPENBLAS_NUM_THREADS": "5", ...}

THE CONSTANTS ARE MEASURED, NOT GUESSED
---------------------------------------
* `VRAM_CONTEXT_MB = 300` — one CUDA context per process.  W12-a (`W12A_REPORT.md` §2) measured
  283 MB for the context + an nrhs=1 solver on Port14 (N = 34 424), of which 40 MB was the solver.
* `vram_lu_MB(N) = 10.7 + 8.51e-4 N` — the cuDSS factorization on the card, a straight line
  through the two points W12-a gave back with `DirectSolver.free()`: **40 MB at N = 34 424** and
  **245 MB at N = 275 218**.  It is a proxy for `nnz_LU`, which is what really scales: the P18
  receipt has nnz_LU = 8.75e6, i.e. 140 MB of complex128 values + indices ≈ the 245 MB measured.
  At N = 1 233 161 (the 92-port sweep's biggest port) the line says 1 060 MB and nnz_LU = 7.63e7
  says 1 220 MB, so the line is ~15 % optimistic at the top end.
* `VRAM_HOMOG_BYTES_PER_TILE = 96` — the CuPy homogenisation batch, 12 live float64 work arrays
  of `chunk_tiles` elements in `homogenise._gx` (Wf, gh, gv, diag, dinv, bfull, x, r, z, p, Ap and
  one temporary inside `A()`).  576 MB at the A2000 default `chunk_tiles = 6e6`.  It is the
  largest single VRAM consumer in a worker and it is *not* in the W12-a numbers, which were taken
  after the build.
* `rss_MB(N) = 1660 + 1.73e-3 N` — least squares over the 10 W4/W7 receipts' `stats[].rss_MB`
  (N from 22 438 to 1 233 161, worst residual 4.7 %).  P18 → 2 135 MB, the 1.23 M port → 3 795 MB,
  matching the README's "약 4 GB".
* `THREADS_PER_JOB = {"splu": 2, "cudss": 3}` — the one judgement call.  A splu worker is
  SuperLU + BLAS bound; 2 BLAS threads each keeps a 14-core box from the oversubscription W9 §5-3
  measured (a 421x421 dense LU swinging 39-560 ms at the default 24 threads).  A cuDSS worker
  spends host CPU on `assemble` *and* on cuDSS's host-side reordering (`Backend.host_nthreads`,
  4) while it must keep the card fed; 3 is **calibrated so that this laptop reproduces the
  measured-safe clamp of 4 concurrent GPU workers** that plan §5-3/§5-5 and `runall15.py` fixed by
  hand.  That is the knob to turn if a box behaves differently -- it is a constant here, not an
  `if` on the machine name.

THE RULE
--------
    jobs = min(cpu_jobs, vram_jobs, ram_jobs, n_ports, max_jobs)
    cpu_jobs  = (cpu_physical - 1) // threads_per_job      # one core left for the driver + OS
    vram_jobs = vram_free_MB // vram_per_process_MB(N)     # GPU solvers only
    ram_jobs  = ram_free_MB  // rss_MB(N)
    every term is floored at 1, and `Plan.limits` records which one bound.

Worked out (see README §10 for the table): this laptop gives **4** for cudss and **6** for splu,
i.e. the sweep behaves exactly as it did before W13; the Threadripper/A6000 box gives 10 and 15.
On the A6000 VRAM stops being the binding constraint and the host CPU takes over.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field

# --- measured constants (see the module docstring for the receipt each one comes from) ---
VRAM_CONTEXT_MB = 300.0
VRAM_LU_BASE_MB, VRAM_LU_PER_UNKNOWN_MB = 10.7, 8.513e-4
VRAM_HOMOG_BYTES_PER_TILE = 96
RSS_BASE_MB, RSS_PER_UNKNOWN_MB = 1660.0, 1.733e-3
THREADS_PER_JOB = {"splu": 2, "cudss": 3, "auto": 3}
MAX_THREADS_PER_JOB = 8          # OpenBLAS past ~8 threads on one dense LU is noise (W9 §5-3)

#: `homogenise.window_conductance`'s default, and the card it was chosen for.
CHUNK_TILES_8GB, VRAM_REF_MB = 6_000_000, 8192
#: `DecapBasis` RHS per solve: the A2000 default and the point beyond which more does not pay.
CHUNK_RHS_DEFAULT, CHUNK_RHS_MAX = 24, 256
#: `plan_sweep` when the caller cannot say how big the ports are (P18-class package rail).
DEFAULT_UNKNOWNS = 275_218


@dataclass(frozen=True)
class HardwareProfile:
    """One box.  `gpus` is a list of `{index, name, vram_total_MB, vram_free_MB}` (may be empty)."""

    cpu_physical: int
    cpu_logical: int
    ram_total_GB: float
    ram_free_GB: float
    gpus: tuple = ()

    @property
    def gpu(self):
        """The card the engine would use, or None.  The biggest one by free VRAM."""
        return max(self.gpus, key=lambda g: g["vram_free_MB"]) if self.gpus else None

    def describe(self) -> str:
        g = self.gpu
        return (f"{self.cpu_physical}C/{self.cpu_logical}T, "
                f"{self.ram_free_GB:.0f}/{self.ram_total_GB:.0f} GB RAM free, "
                + (f"{g['name']} {g['vram_free_MB']:.0f}/{g['vram_total_MB']:.0f} MB free"
                   if g else "no GPU"))

    @classmethod
    def detect(cls) -> "HardwareProfile":
        """Read the real machine.  psutil when it is installed, stdlib otherwise; never raises."""
        phys = log = None
        tot = free = 0.0
        try:
            import psutil
            phys, log = psutil.cpu_count(logical=False), psutil.cpu_count(logical=True)
            vm = psutil.virtual_memory()
            tot, free = vm.total / 2 ** 30, vm.available / 2 ** 30
        except Exception:
            pass
        log = log or os.cpu_count() or 1
        phys = phys or _physical_cores() or max(1, log // 2)
        if not tot:
            tot, free = _ram_GB()
        return cls(int(phys), int(log), float(tot), float(free), tuple(_gpus()))


@dataclass(frozen=True)
class Plan:
    """What to run and how wide.  `limits` says which cap bound, `notes` why."""

    jobs: int = 1
    threads: int = 1              # BLAS threads per process
    workers: int = 1              # frequency-parallel CPU basis processes
    chunk: int = CHUNK_RHS_DEFAULT
    solver: str = "splu"
    env: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    notes: tuple = ()


# ----------------------------------------------------------------------------- sizing (pure)
def vram_per_process_MB(unknowns: int, chunk_tiles: int = CHUNK_TILES_8GB) -> float:
    """CUDA context + cuDSS factorization + the CuPy homogenisation batch, for one worker."""
    return (VRAM_CONTEXT_MB + VRAM_LU_BASE_MB + VRAM_LU_PER_UNKNOWN_MB * unknowns
            + VRAM_HOMOG_BYTES_PER_TILE * chunk_tiles / 1e6)


def rss_per_process_MB(unknowns: int) -> float:
    """Host peak RSS of one `solve` worker (fit to the W4/W7 receipts)."""
    return RSS_BASE_MB + RSS_PER_UNKNOWN_MB * unknowns


def plan_sweep(profile: HardwareProfile, n_ports: int = 1, unknowns_estimate: int = DEFAULT_UNKNOWNS,
               solver: str = "splu", max_jobs: int | None = None) -> Plan:
    """How many `solve` subprocesses to run at once, and with how many BLAS threads each.

    `unknowns_estimate` is the size of the *largest* port in the sweep, because those are the ones
    that will be resident together.  Without it the P18-class default is used.
    """
    notes, limits = [], {}
    if solver in ("cudss", "auto") and profile.gpu is None:
        notes.append(f"no GPU detected -- solver {solver!r} plans as 'splu' (the engine itself "
                     f"falls back per model in Model._gpu_solver)")
        solver = "splu"
    limits["cpu"] = max(1, (profile.cpu_physical - 1) // THREADS_PER_JOB.get(solver, 2))
    limits["ram"] = max(1, int(profile.ram_free_GB * 1024 // rss_per_process_MB(unknowns_estimate)))
    if solver in ("cudss", "auto"):
        per = vram_per_process_MB(unknowns_estimate, plan_chunk_tiles(profile))
        limits["vram"] = max(1, int(profile.gpu["vram_free_MB"] // per))
    limits["ports"] = max(1, int(n_ports))
    if max_jobs:
        limits["max_jobs"] = max(1, int(max_jobs))
    jobs = min(limits.values())
    bound = [k for k, v in limits.items() if v == jobs]
    notes.append(f"jobs={jobs} bound by {'+'.join(bound)} (" +
                 ", ".join(f"{k} {v}" for k, v in limits.items()) + ")")
    t = plan_threads(profile, jobs)
    return Plan(jobs=jobs, threads=t.threads, chunk=CHUNK_RHS_DEFAULT, solver=solver,
                env=t.env, limits=limits, notes=tuple(notes))


def plan_basis(profile: HardwareProfile, N: int, n_decaps: int, n_freqs: int,
               solver: str = "splu") -> Plan:
    """`Model.decap_basis`: RHS per solve (`chunk`) and frequency-parallel CPU processes.

    chunk   24 is what the 8 GB A2000 runs (W9: 106 MB per (N, 24) complex block at N = 275 k).
            It scales with whole 8 GB units of free device memory (GPU) or free RAM (CPU basis),
            and is capped by the number of right-hand sides there are (1 + Nd), by
            `CHUNK_RHS_MAX`, and by a quarter of free memory for the two (N, chunk) blocks.
    workers Every frequency of the basis is an independent factorization, so an splu basis is
            embarrassingly parallel over frequencies.  cuDSS gets 1: one card, and a second
            context per process would cost more VRAM than the parallelism buys.
    """
    notes = []
    gpu = solver in ("cudss", "auto") and profile.gpu is not None
    free_MB = profile.gpu["vram_free_MB"] if gpu else profile.ram_free_GB * 1024
    chunk = CHUNK_RHS_DEFAULT * max(1, int(free_MB // VRAM_REF_MB))
    fits = max(1, int(0.25 * free_MB * 1e6 // (2 * max(N, 1) * 16)))   # B and V, complex128
    chunk = max(1, min(chunk, CHUNK_RHS_MAX, n_decaps + 1, fits))
    if solver in ("cudss", "auto"):
        workers = 1
        notes.append("workers=1: the basis is on one card, one cuDSS context per process")
    else:
        workers = min(max(1, int(n_freqs)),
                      max(1, (profile.cpu_physical - 1) // THREADS_PER_JOB["splu"]),
                      max(1, int(profile.ram_free_GB * 1024 // rss_per_process_MB(N))))
        notes.append(f"workers={workers} splu processes over {n_freqs} frequencies "
                     f"(the parent assembles Y, each worker factorizes one frequency)")
    t = plan_threads(profile, max(workers, 1))
    return Plan(jobs=1, threads=t.threads, workers=workers, chunk=chunk, solver=solver,
                env=t.env, limits=dict(chunk_fits=fits), notes=tuple(notes))


def plan_threads(profile: HardwareProfile, jobs: int) -> Plan:
    """BLAS threads per worker process, so `jobs x threads <= cpu_logical`, and the env to set.

    The env belongs to the *spawned* process (`subprocess.run(env=...)`).  A library never sets
    these for its caller: W12-a §5 measured that `OPENBLAS_NUM_THREADS` moves the last bits of the
    splu path (2.7e-13), so a bit-for-bit receipt comparison has to fix the thread count.
    """
    t = max(1, min(MAX_THREADS_PER_JOB, profile.cpu_logical // max(1, jobs)))
    env = {k: str(t) for k in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS")}
    return Plan(jobs=max(1, jobs), threads=t, env=env)


def plan_chunk_tiles(profile: HardwareProfile) -> int:
    """`homogenise.window_conductance(chunk_tiles=...)` for this box: 6e6 per 8 GB of the card.

    NOT numerics-neutral: `batched_gx` stops when *every* window in the batch has converged, so a
    different batch size gives a different CG iteration count and therefore different last bits of
    G.  Which is why `window_conductance`'s default stays 6e6 and this is opt-in
    (`chunk_tiles="auto"`).  On this laptop the formula returns exactly the default.
    """
    g = profile.gpu
    return CHUNK_TILES_8GB * (max(1, int(g["vram_total_MB"] // VRAM_REF_MB)) if g else 1)


# ----------------------------------------------------------------------------- detection guts
def _physical_cores():
    """Physical cores without psutil: `wmic` on Windows, `/proc/cpuinfo` on Linux, else None."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["wmic", "cpu", "get", "NumberOfCores"], capture_output=True,
                                 text=True, timeout=20).stdout
            n = sum(int(x) for x in out.split() if x.isdigit())
            return n or None
        seen = set()
        pid = None
        for line in open("/proc/cpuinfo", encoding="utf-8"):
            k, _, v = line.partition(":")
            if k.strip() == "physical id":
                pid = v.strip()
            elif k.strip() == "core id":
                seen.add((pid, v.strip()))
        return len(seen) or None
    except Exception:
        return None


def _ram_GB():
    """(total, available) in GB without psutil: GlobalMemoryStatusEx / /proc/meminfo / (0, 0)."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in
                    ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                     "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]

            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2 ** 30, m.ullAvailPhys / 2 ** 30
        info = {}
        for line in open("/proc/meminfo", encoding="utf-8"):
            k, _, v = line.partition(":")
            info[k] = int(v.split()[0]) / 1024 ** 2          # kB -> GB
        return info.get("MemTotal", 0.0), info.get("MemAvailable", info.get("MemFree", 0.0))
    except Exception:
        return 0.0, 0.0


def _gpus():
    """[{index, name, vram_total_MB, vram_free_MB}] via cuda-bindings, else nvidia-smi, else [].

    `cudaMemGetInfo` initialises the primary context on the device it asks about (~250-300 MB),
    so the free VRAM it reports already excludes the caller's own context -- which is the number
    a planner wants, but it does mean a sweep *driver* that calls `detect()` holds a context for
    as long as it lives.  Call it once and pass the profile around.
    """
    try:
        from cuda.bindings import runtime as cudart

        err, n = cudart.cudaGetDeviceCount()
        if int(err) == 0 and n:
            out = []
            for i in range(n):
                cudart.cudaSetDevice(i)
                e1, props = cudart.cudaGetDeviceProperties(i)
                e2, free, total = cudart.cudaMemGetInfo()
                if int(e1) or int(e2):
                    continue
                name = props.name
                out.append(dict(index=i, name=(name.decode() if isinstance(name, bytes)
                                               else str(name)).strip("\x00"),
                                vram_total_MB=total / 1e6, vram_free_MB=free / 1e6))
            if out:
                return out
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
        gpus = []
        for line in out.stdout.splitlines():
            f = [x.strip() for x in line.split(",")]
            if len(f) == 4 and f[0].isdigit():
                gpus.append(dict(index=int(f[0]), name=f[1],
                                 vram_total_MB=float(f[2]) * 1.048576,   # MiB -> MB
                                 vram_free_MB=float(f[3]) * 1.048576))
        return gpus
    except Exception:
        return []


def detect() -> HardwareProfile:
    """`HardwareProfile.detect()`, as a module-level function."""
    return HardwareProfile.detect()


def demo():
    """Self-check: this box detects, and the two reference profiles plan the documented numbers."""
    prof = HardwareProfile.detect()
    print(f"[hardware] {prof.describe()}")
    assert prof.cpu_physical >= 1 and prof.cpu_logical >= prof.cpu_physical
    assert prof.ram_total_GB > 0
    for name, p in (("laptop  ", LAPTOP), ("worksta.", WORKSTATION)):
        for solver in ("cudss", "splu"):
            s = plan_sweep(p, n_ports=92, solver=solver)
            b = plan_basis(p, 275_218, 421, 27, solver=solver)
            print(f"[hardware] {name} {solver:5s} sweep jobs {s.jobs} x {s.threads} threads | "
                  f"basis chunk {b.chunk} workers {b.workers} | tiles {plan_chunk_tiles(p):,}")
    assert plan_sweep(LAPTOP, 92, solver="cudss").jobs == 4, "the A2000 clamp must still be 4"
    assert plan_sweep(LAPTOP, 92, solver="splu").jobs == 6
    assert plan_chunk_tiles(LAPTOP) == CHUNK_TILES_8GB
    print("DEMO PASS")


#: The development laptop and the target workstation, as profiles -- the two rows of README §10.
LAPTOP = HardwareProfile(14, 20, 63.7, 42.5,
                         ({"index": 0, "name": "NVIDIA RTX A2000 8GB Laptop GPU",
                           "vram_total_MB": 8589.0, "vram_free_MB": 7545.0},))
WORKSTATION = HardwareProfile(32, 64, 512.0, 480.0,
                              ({"index": 0, "name": "NVIDIA RTX A6000",
                                "vram_total_MB": 51539.0, "vram_free_MB": 48000.0},))

__all__ = ["CHUNK_RHS_DEFAULT", "CHUNK_TILES_8GB", "DEFAULT_UNKNOWNS", "HardwareProfile", "LAPTOP",
           "Plan", "THREADS_PER_JOB", "WORKSTATION", "detect", "plan_basis", "plan_chunk_tiles",
           "plan_sweep", "plan_threads", "rss_per_process_MB", "vram_per_process_MB"]

if __name__ == "__main__":
    demo()
