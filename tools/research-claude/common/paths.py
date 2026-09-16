"""Portable data / work / repo locations for the research-claude tools (Linux container and Windows PC).

Resolution (first hit wins):
  DATA_DIR : $SPD_PI_DATA_DIR  ->  /home/claude/data (if it exists)  ->  <repo>/data
             holds the SPD files.  Reference Z npz files are looked up in DATA_DIR, then DATA_DIR/analysis
             (the owner's PC layout: D:\\Downloads\\examples\\analysis\\*_Zdiag.npz), unless $SPD_PI_REF_DIR is set.
  WORK_DIR : $SPD_PI_WORK_DIR  ->  /home/claude/work (if it exists)  ->  <repo>/work
             intermediates (pkl/npz) and outputs, one sub-directory per experiment (exp1 .. exp9).
  REPO_DIR : the git checkout containing this file.

Usage from a script in tools/research-claude/expN/:
    sys.path.insert(0, os.path.join(HERE, "..", "common"))
    from paths import spd_path, ref_npz, work_dir

This module only locates files; it must not change any model parameter or numeric.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[3]


def _pick(env: str, container_default: str, repo_default: str) -> Path:
    v = os.environ.get(env)
    if v:
        return Path(v).expanduser()
    c = Path(container_default)
    if c.is_dir():
        return c
    return REPO_DIR / repo_default


DATA_DIR = _pick("SPD_PI_DATA_DIR", "/home/claude/data", "data")
WORK_DIR = _pick("SPD_PI_WORK_DIR", "/home/claude/work", "work")

# design id -> (SPD file name, reference Zdiag npz file name)
DESIGNS = {
    "260729": ("S4LB002-2Para_260729_1_injected.spd", "S4LB002_260729_Zdiag.npz"),
    "260804": ("S4LB002-2Para_260804_1_injected.spd", "S4LB002_260804_Zdiag.npz"),
    "s5m6585": ("s5m6585_32p_260414_length3_1.spd", "s5m6585_Zdiag.npz"),
}


def spd_path(design: str) -> Path:
    """SPD file for a design id ("260729", "260804", "s5m6585")."""
    return DATA_DIR / DESIGNS[design][0]


def ref_npz(design: str) -> Path:
    """Reference PowerSI Zdiag npz for a design id: $SPD_PI_REF_DIR, else DATA_DIR, else DATA_DIR/analysis."""
    name = DESIGNS[design][1]
    cands = ([Path(os.environ["SPD_PI_REF_DIR"])] if os.environ.get("SPD_PI_REF_DIR") else []) + [DATA_DIR, DATA_DIR / "analysis"]
    for d in cands:
        if (d / name).is_file():
            return d / name
    return cands[0] / name  # not found: return the primary location so the error message names it


def work_dir(exp: str) -> Path:
    """WORK_DIR/<exp>, created if missing."""
    p = WORK_DIR / exp
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_file(exp: str, name: str) -> Path:
    return work_dir(exp) / name


def local_spd(recorded: str | os.PathLike) -> Path:
    """Map an SPD path recorded inside a cached pickle (e.g. /home/claude/data/x.spd) to this machine."""
    p = Path(recorded)
    if p.is_file():
        return p
    return DATA_DIR / Path(str(recorded).replace("\\", "/")).name


def peak_rss_mb() -> float:
    """Peak resident memory of this process in MB (resource on POSIX, psutil/ctypes on Windows, NaN otherwise)."""
    try:
        import resource  # POSIX only
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / (1024.0 * 1024.0) if sys.platform == "darwin" else r / 1024.0
    except ImportError:
        pass
    try:
        import psutil  # type: ignore
        mi = psutil.Process().memory_info()
        return getattr(mi, "peak_wset", mi.rss) / (1024.0 * 1024.0)
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32"); psapi = ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return c.PeakWorkingSetSize / (1024.0 * 1024.0)
    return float("nan")


if __name__ == "__main__":
    print("REPO_DIR", REPO_DIR)
    print("DATA_DIR", DATA_DIR, "exists" if DATA_DIR.is_dir() else "MISSING")
    print("WORK_DIR", WORK_DIR, "exists" if WORK_DIR.is_dir() else "MISSING")
    for k in DESIGNS:
        s, r = spd_path(k), ref_npz(k)
        print(f"  {k:8s} spd {'ok ' if s.is_file() else 'MISSING'} {s}")
        print(f"  {'':8s} ref {'ok ' if r.is_file() else 'MISSING'} {r}")
    print("peak_rss_MB", round(peak_rss_mb(), 1))
