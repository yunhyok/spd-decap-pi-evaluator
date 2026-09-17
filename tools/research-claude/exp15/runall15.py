#!/usr/bin/env python
"""EXP-15: run run11.py --variant j over all 92 ports of design 260729 (see EXP15_PLAN.md).

  python runall15.py --dry
  python runall15.py --jobs 14
  python runall15.py --ports Port2_SITE0,Port5_SITE0_1721 --jobs 2
  python runall15.py --tag s5m6585 --variant p --outdir exp30 --dry   # held-out design, ports from its ref npz

Resume-safe: a port whose receipt WORK_DIR/exp15/result_260729_{port}_any_j.json already exists is
skipped. Each port runs `run11.py --variant j --tag {tag} --port {port} --outdir exp15` as a
subprocess (cwd exp11/, stdout+stderr -> WORK_DIR/exp15/logs/run_{port}.log), `--jobs` in parallel via
a thread pool. A non-zero exit or a missing receipt after the run is recorded in failures.json (port,
returncode, last 30 log lines); the file is rewritten after every failure so partial runs keep results.

  --ref any|gnd (default any) : passed through to run11.py --ref; the receipt filename's variant tag
    gets a _gnd suffix (result_260729_{port}_any_{variant}_gnd.json) so it never collides with a
    --ref any receipt.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXP11_DIR = os.path.join(HERE, "..", "exp11")
EXP5_DIR = os.path.join(HERE, "..", "exp5")
sys.path.insert(0, os.path.join(HERE, "..", "common"))
from paths import ref_npz, work_dir  # noqa: E402

TAG = "260729"


def all_ports():
    ref = np.load(ref_npz(TAG), allow_pickle=True)
    return [str(n).split("::")[0] for n in ref["port_names"]]


def vtag_of(variant, ref):
    """Same filename convention as run11.vtag_of: '_gnd' suffix on the variant tag when ref=gnd."""
    return variant if ref == "any" else f"{variant}_gnd"


def receipt_file(out, variant, port, ref="any"):
    return os.path.join(out, f"result_{TAG}_{port}_any_{vtag_of(variant, ref)}.json")


def run_one(port, variant, outdir, out, log_dir, ref="any"):
    os.makedirs(log_dir, exist_ok=True)
    log_fn = os.path.join(log_dir, f"run_{port}.log")
    t0 = time.time()
    env = os.environ.copy()
    if env.get("SPD_PI_SOLVER", "").lower() == "cudss":
        env["PYTHONUNBUFFERED"] = "1"  # a GPU fault kills the process; keep the log line-buffered
    with open(log_fn, "w", encoding="utf-8") as logf:
        p = subprocess.run([sys.executable, "run11.py", "--variant", variant, "--tag", TAG, "--port", port,
                            "--outdir", outdir, "--ref", ref],
                           cwd=EXP11_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT)
    wall = time.time() - t0
    ok = p.returncode == 0 and os.path.exists(receipt_file(out, variant, port, ref))
    return port, ok, p.returncode, wall, log_fn


# --extract-only: P5.prepare in a subprocess per port (same isolation as run_one) -> one STATS json line.
PREP = ("import json,sys,time;sys.path.insert(0,'.');import pipeline as P;"
        "t=time.time();ex,_,_=P.prepare(sys.argv[1],sys.argv[2]);"
        "print('STATS '+json.dumps(dict(rail_net=ex['rail_net'],n_rail_nodes=len(ex['rail_nodes']),"
        "n_rail_vias=len(ex['rail_vias']),n_rail_traces=len(ex['rail_traces']),n_decaps=len(ex['decaps']),"
        "rail_layers=[g['layer'] for g in ex['rail_geoms']],prepare_seconds=round(time.time()-t,1))))")


def extract_one(port, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    log_fn = os.path.join(log_dir, f"extract_{port}.log")
    p = subprocess.run([sys.executable, "-c", PREP, TAG, port], cwd=EXP5_DIR, env=os.environ.copy(),
                       capture_output=True, text=True, errors="replace")
    with open(log_fn, "w", encoding="utf-8") as f:
        f.write(p.stdout + p.stderr)
    line = next((l for l in p.stdout.splitlines() if l.startswith("STATS ")), None)
    return port, (json.loads(line[6:]) if line else dict(error=p.returncode, log=log_fn))


def tail(fn, n=30):
    try:
        return open(fn, encoding="utf-8", errors="replace").readlines()[-n:]
    except OSError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=14)
    ap.add_argument("--ports", default=None, help="comma-separated subset of port names; default every port of --tag")
    ap.add_argument("--dry", action="store_true", help="list the ports that would run and exit")
    ap.add_argument("--variant", default="j", help="run11.py --variant to pass through (default j)")
    ap.add_argument("--outdir", default="exp15", help="run11.py --outdir to pass through (default exp15)")
    ap.add_argument("--ref", default="any", choices=["any", "gnd"], help="run11.py --ref to pass through (default any)")
    ap.add_argument("--extract-only", action="store_true",
                    help="only run pipeline.prepare per port (no model build); write {outdir}/extract_stats.json")
    ap.add_argument("--tag", default="260729", help="design id (default 260729; held-out: 260804, s5m6585) -- port list comes from its reference npz")
    a = ap.parse_args()
    global TAG
    TAG = a.tag

    if os.environ.get("SPD_PI_SOLVER", "").lower() == "cudss" and a.jobs > 4:
        print(f"[warn] SPD_PI_SOLVER=cudss: each process pins ~1.2 GB of CUDA context on the "
              f"8 GB GPU -- clamping --jobs {a.jobs} -> 4", flush=True)
        a.jobs = 4

    out = work_dir(a.outdir)
    log_dir = os.path.join(out, "logs")

    ports = a.ports.split(",") if a.ports else all_ports()
    # cost ordering: no cheap cost estimate available without extracting each port (the expensive
    # step itself), so we keep the reference npz's list order.
    if a.extract_only:
        stats = {}
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for fut in as_completed([pool.submit(extract_one, p, log_dir) for p in ports]):
                port, st = fut.result()
                stats[port] = st
                print(f"[{len(stats)}/{len(ports)}] {port} {st.get('rail_net', 'FAIL')} "
                      f"decaps {st.get('n_decaps', '-')} {st.get('prepare_seconds', '-')}s", flush=True)
        # pipeline.prepare rewrites the shared shapes_{tag}.pkl; a concurrent reader can hit a
        # half-written file.  ponytail: serial retry of the losers, cheap because the file has
        # converged by then -- proper fix would be an atomic replace inside pipeline.prepare.
        for port in [p for p, v in stats.items() if "error" in v]:
            port, st = extract_one(port, log_dir)
            stats[port] = st
            print(f"retry {port} {st.get('rail_net', 'FAIL')} {st.get('prepare_seconds', '-')}s", flush=True)
        fn = os.path.join(out, "extract_stats.json")
        json.dump(dict(sorted(stats.items())), open(fn, "w"), indent=1)
        print("wrote", fn)
        return 1 if any("error" in v for v in stats.values()) else 0

    todo = [p for p in ports if not os.path.exists(receipt_file(out, a.variant, p, a.ref))]
    skipped = [p for p in ports if p not in todo]

    if a.dry:
        print(f"{len(ports)} ports requested, {len(skipped)} already have receipts, {len(todo)} to run:")
        for p in todo:
            print(" ", p)
        return 0

    if skipped:
        print(f"skipping {len(skipped)} ports with existing receipts")
    if not todo:
        print("nothing to do")
        return 0

    failures = []
    lock = threading.Lock()
    fail_fn = os.path.join(out, "failures.json")

    def record_failure(entry):
        with lock:
            failures.append(entry)
            json.dump(failures, open(fail_fn, "w"), indent=1)

    n_ok = n_fail = 0
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_one, p, a.variant, a.outdir, out, log_dir, a.ref): p for p in todo}
        for fut in as_completed(futs):
            port, ok, rc, wall, log_fn = fut.result()
            if ok:
                n_ok += 1
                print(f"[{n_ok + n_fail}/{len(todo)}] OK   {port}  wall {wall:.0f}s", flush=True)
            else:
                n_fail += 1
                record_failure(dict(port=port, returncode=rc, log_tail=tail(log_fn)))
                print(f"[{n_ok + n_fail}/{len(todo)}] FAIL {port}  wall {wall:.0f}s  rc={rc}  see {log_fn}", flush=True)

    print(f"done: {n_ok} ok, {n_fail} failed" + (f" (see {fail_fn})" if n_fail else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
