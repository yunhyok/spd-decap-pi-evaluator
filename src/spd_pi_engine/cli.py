"""`python -m spd_pi_engine` (plan W7): a thin argparse shell over the W4 public API.

    ports  --spd PATH
    info   --spd PATH --port NAME --cache DIR
    solve  --spd PATH --port NAME --cache DIR --out receipt.json [options]
    verify --receipt A.json --against B.json [--tol 1e-8]
    sweep  --spd PATH --ports all|a,b,c --cache DIR --outdir DIR [--jobs auto|N] [options]

No numerics live here: `solve` is `Design.open -> .rail -> .build -> .solve -> .receipt()`
(api.py, receipt.py) exactly as `docs/engine/W4_REPORT.md` §2 shows it, and `sweep` is
`tools/research-claude/exp15/runall15.py`'s subprocess-per-port pattern re-pointed at
`python -m spd_pi_engine solve` (stdlib only, no import of the research tree).

W13: `--jobs` defaults to `auto` and the old hardcoded "cudss -> clamp to 4" is gone; the cap now
comes out of `hardware.plan_sweep(HardwareProfile.detect(), ...)`, which still says 4 on this
laptop's A2000 and says 10 on a Threadripper/A6000.  The workers' BLAS thread count is set in the
*subprocess* environment (never this process's), see `hardware.plan_threads`.

The frequency ladder (`LADDER` + 21 log points 3e5..3e7) is `exp5/pipeline.LADDER` /
`tests/engine/ladder.py`. Without `--ref-npz` it is used unsnapped; with `--ref-npz` each point is
snapped to the nearest frequency of the reference grid, matching the reproduction receipts.

`LADDER`, `ladder_freqs` and `unique_path` moved to `api.py` in W12-c (they are public API now,
`from spd_pi_engine import ladder_freqs, unique_path`); this module still has and uses them, just
by importing them from there.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from .api import LADDER, Design, ladder_freqs, unique_path
from .backend import Backend
from .hardware import DEFAULT_UNKNOWNS, HardwareProfile, plan_sweep
from .model import FLAGS_LEGACY, FLAGS_P, FLAGS_PMK, FLAGS_Q, ModelOptions
from .receipt import attach_reference

VARIANTS = {"legacy": FLAGS_LEGACY, "p": FLAGS_P, "q": FLAGS_Q, "pmk": FLAGS_PMK}


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


def read_npz_ref(path, port):
    """(full PowerSI freq grid, this port's Zdiag column) -- `w4_gate.ref_of` / `tests/engine/ladder.ref_of`."""
    ref = np.load(path, allow_pickle=True)
    names = [str(x) for x in ref["port_names"]]
    col = [i for i, n in enumerate(names) if n.split("::")[0] == port][0]
    return ref["freq"], ref["Zdiag"][:, col]


def resolve_freqs(args, ref_freq=None) -> np.ndarray:
    if args.freqs:
        return np.array([float(x) for x in args.freqs.split(",")], dtype=float)
    return ladder_freqs(ref_freq)


def _add_solve_options(sp):
    sp.add_argument("--variant", choices=sorted(VARIANTS), default="p",
                     help="flag preset (default p = exp28/p baseline)")
    sp.add_argument("--reference", choices=["powersi-compatible", "physical-gnd"],
                     default="powersi-compatible")
    freqs = sp.add_mutually_exclusive_group()
    freqs.add_argument("--freqs", default=None, help="comma-separated Hz, e.g. 1e3,1e4,1e5")
    freqs.add_argument("--ladder", action="store_true",
                        help="LADDER(7)+21 log points 3e5-3e7 (default when neither is given); "
                             "snapped to --ref-npz's grid when it is given")
    sp.add_argument("--solver", choices=["splu", "cudss", "auto"], default="splu")
    sp.add_argument("--fast", action=argparse.BooleanOptionalAction, default=False)
    sp.add_argument("--ref-npz", default=None, help="PowerSI Zdiag npz (freq, port_names, Zdiag)")
    sp.add_argument("--breakdown-100k", action="store_true",
                     help="force breakdown_100k in the receipt even if 1e5 Hz is not in freq")


def _build_model(args):
    d = Design.open(args.spd)
    rail = d.rail(args.port, args.cache)
    opt = ModelOptions(reference=args.reference, flags=dict(VARIANTS[args.variant]), fringe=True)
    backend = Backend(solver=args.solver, fast=args.fast)
    return rail, rail.build(opt, backend)


def cmd_ports(args) -> int:
    for p in Design.open(args.spd).ports():
        print(p)
    return 0


def cmd_info(args) -> int:
    rail = Design.open(args.spd).rail(args.port, args.cache)
    print(f"rail_net={rail.rail_net}")
    print(f"decaps={len(rail.decaps)}")
    print(f"n_nodes={rail.n_nodes} n_vias={rail.n_vias} n_traces={rail.n_traces}")
    print(json.dumps(rail.estimate_cost(), indent=2, default=_json_default))
    return 0


def cmd_solve(args) -> int:
    t0 = time.time()
    ref_freq = ref_Z = None
    if args.ref_npz:
        ref_freq, ref_Z = read_npz_ref(args.ref_npz, args.port)
    freqs = resolve_freqs(args, ref_freq)

    rail, mdl = _build_model(args)
    res = mdl.solve(freqs)
    receipt = res.receipt(breakdown_100k=True if args.breakdown_100k else None)

    err = None
    if ref_freq is not None:
        attach_reference(receipt, ref_freq, ref_Z)
        err = receipt["err_1MHz"]

    out_path = unique_path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, indent=1, default=_json_default), encoding="utf-8")

    wall = time.time() - t0
    summary = f"unknowns={receipt['unknowns']} wall={wall:.1f}s"
    if err is not None:
        summary += f" err_1MHz={err:.4f} ({err * 100:.2f}%)"
    print(summary)
    print(f"wrote {out_path}")
    return 0


def cmd_verify(args) -> int:
    a = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    b = json.loads(Path(args.against).read_text(encoding="utf-8"))
    fa = np.asarray(a["freq"], dtype=float)
    fb = np.asarray(b["freq"], dtype=float)
    if fa.shape != fb.shape or not np.array_equal(fa, fb):
        print(f"FAIL: frequency grids differ ({fa.shape} vs {fb.shape})")
        return 1
    za = np.asarray(a["Z_re"], dtype=float) + 1j * np.asarray(a["Z_im"], dtype=float)
    zb = np.asarray(b["Z_re"], dtype=float) + 1j * np.asarray(b["Z_im"], dtype=float)
    rel = float(np.max(np.abs(za - zb) / np.abs(zb)))
    print(rel)
    return 0 if rel <= args.tol else 1


def cmd_sweep(args) -> int:
    ports = Design.open(args.spd).ports() if args.ports == "all" else args.ports.split(",")
    profile = HardwareProfile.detect()
    plan = plan_sweep(profile, n_ports=len(ports), solver=args.solver,
                      unknowns_estimate=args.unknowns or DEFAULT_UNKNOWNS,
                      max_jobs=args.max_jobs)
    print(f"[hardware] {profile.describe()}")
    for n in plan.notes:
        print(f"[hardware] {n}")
    if args.jobs == "auto":
        jobs = plan.jobs
    else:
        jobs = max(1, int(args.jobs))
        if jobs > plan.jobs:
            print(f"[warn] --jobs {jobs} is above what this box plans for ({plan.jobs}); "
                  f"clamping (pass --max-jobs to raise the plan's own cap)")
            jobs = plan.jobs
    # W13: the worker env, never the caller's (W12-a 5: OPENBLAS_NUM_THREADS moves the last bits
    # of splu by 2.7e-13).  `--threads 0` inherits, which is what a bit-for-bit rerun wants.
    threads = plan.threads if args.threads == "auto" else int(args.threads)
    env = dict(os.environ)
    if threads:
        env.update({k: str(threads) for k in
                    ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS")})
    print(f"[hardware] {jobs} jobs x {threads or 'inherited'} BLAS threads")

    outdir = Path(args.outdir)
    log_dir = outdir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    def receipt_path(port):
        return outdir / f"receipt_{port}.json"

    def solve_argv(port):
        argv = [sys.executable, "-m", "spd_pi_engine", "solve", "--spd", str(args.spd),
                "--port", port, "--cache", str(args.cache), "--out", str(receipt_path(port)),
                "--variant", args.variant, "--reference", args.reference,
                "--solver", args.solver, "--fast" if args.fast else "--no-fast"]
        argv += ["--ladder"] if not args.freqs else ["--freqs", args.freqs]
        if args.ref_npz:
            argv += ["--ref-npz", str(args.ref_npz)]
        if args.breakdown_100k:
            argv.append("--breakdown-100k")
        return argv

    def run_one(port):
        t0 = time.time()
        log_fn = log_dir / f"{port}.log"
        with open(log_fn, "w", encoding="utf-8") as logf:
            p = subprocess.run(solve_argv(port), stdout=logf, stderr=subprocess.STDOUT,
                               env=env)
        ok = p.returncode == 0 and receipt_path(port).exists()
        return port, ok, p.returncode, time.time() - t0, log_fn

    todo = [p for p in ports if not receipt_path(p).exists()]
    skipped = [p for p in ports if p not in todo]
    if skipped:
        print(f"skipping {len(skipped)} ports with existing receipts")
    if not todo:
        print("nothing to do")
        return 0

    failures = []
    fail_fn = outdir / "failures.json"
    n_ok = n_fail = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(run_one, p): p for p in todo}
        for fut in as_completed(futs):
            port, ok, rc, wall, log_fn = fut.result()
            if ok:
                n_ok += 1
                print(f"[{n_ok + n_fail}/{len(todo)}] OK   {port}  wall {wall:.0f}s", flush=True)
            else:
                n_fail += 1
                tail = log_fn.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
                failures.append(dict(port=port, returncode=rc, log_tail=tail))
                fail_fn.write_text(json.dumps(failures, indent=1), encoding="utf-8")
                print(f"[{n_ok + n_fail}/{len(todo)}] FAIL {port}  wall {wall:.0f}s  rc={rc}  see {log_fn}",
                      flush=True)

    print(f"done: {n_ok} ok, {n_fail} failed" + (f" (see {fail_fn})" if n_fail else ""))
    return 1 if n_fail else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m spd_pi_engine", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("ports", help="print an SPD's port names, one per line")
    sp.add_argument("--spd", required=True)
    sp.set_defaults(func=cmd_ports)

    sp = sub.add_parser("info", help="print one rail's size and estimate_cost()")
    sp.add_argument("--spd", required=True)
    sp.add_argument("--port", required=True)
    sp.add_argument("--cache", required=True)
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("solve", help="build + solve one rail, write receipt v1")
    sp.add_argument("--spd", required=True)
    sp.add_argument("--port", required=True)
    sp.add_argument("--cache", required=True)
    sp.add_argument("--out", required=True)
    _add_solve_options(sp)
    sp.set_defaults(func=cmd_solve)

    sp = sub.add_parser("verify", help="compare two receipts' freq/Z (the engine's own smoke test)")
    sp.add_argument("--receipt", required=True)
    sp.add_argument("--against", required=True)
    sp.add_argument("--tol", type=float, default=1e-8)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("sweep", help="solve every port (or a subset) in subprocesses, resume-safe")
    sp.add_argument("--spd", required=True)
    sp.add_argument("--ports", required=True, help="'all' or a comma-separated list of port names")
    sp.add_argument("--cache", required=True)
    sp.add_argument("--outdir", required=True)
    sp.add_argument("--jobs", default="auto",
                    help="'auto' (default, from hardware.plan_sweep) or a number")
    sp.add_argument("--max-jobs", type=int, default=None,
                    help="cap what 'auto' may choose")
    sp.add_argument("--threads", default="auto",
                    help="BLAS threads per worker: 'auto' (from the plan), a number, or 0 to "
                         "inherit this process's environment (bit-for-bit reruns)")
    sp.add_argument("--unknowns", type=int, default=0,
                    help="size of the biggest port in the sweep; sharpens the 'auto' plan "
                         f"(default {DEFAULT_UNKNOWNS}, a P18-class package rail)")
    _add_solve_options(sp)
    sp.set_defaults(func=cmd_sweep)

    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
