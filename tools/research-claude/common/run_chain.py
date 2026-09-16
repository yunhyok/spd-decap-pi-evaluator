#!/usr/bin/env python
"""Cross-platform replacement for the expN/run*.sh chains (Windows has no sh / ulimit).

    python tools/research-claude/common/run_chain.py --list
    python tools/research-claude/common/run_chain.py exp8            # = exp8/runall8.sh
    python tools/research-claude/common/run_chain.py exp9 --dry-run

Runs each step sequentially with cwd = the experiment directory, the current interpreter, and
PYTHONPATH = <repo>/src prepended (so a non-editable install also works).  Output is teed to
WORK_DIR/<exp>/chain_<name>.log.  `ulimit -v` guards from the .sh files are NOT reproduced: watch memory
yourself (Task Manager); the .sh capped the --gnd s3 runs at 6.0-6.5 GB virtual memory.  The PID waits in exp9/chain2.sh are dropped.
The steps are copied verbatim from the .sh files; do not edit them to tune anything.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO_DIR, work_dir  # noqa: E402

P18, P19 = "Port18_SITE0", "Port19_SITE0"
CHAINS: dict[str, tuple[str, list[list[str]]]] = {
    # name: (experiment dir, steps)                                     source .sh
    "exp5": ("exp5", [["pipeline.py", "--tag", "260729", "--port", P18, "--freqset", "sweep"],          # exp5/runall.sh
                      ["pipeline.py", "--tag", "260804", "--port", P18, "--freqset", "sweep"]]
             + [["pipeline.py", "--tag", "260729", "--port", p, "--freqset", "ladder"]
                for p in ("Port16_SITE0", P19, "Port14_SITE0", "Port7_SITE0", "Port1_SITE0")]),
    "exp6": ("exp6", [["run6.py", "--port", P18, "--stats"], ["run6.py", "--port", P18]]                 # exp6/runall6.sh
             + [["run6.py", "--port", P18, "--dmax", d] for d in ("200", "400", "1500")]),
    "exp6b": ("exp6", [["run6.py", "--port", p, "--stats"] for p in (P18, "Port1_SITE0", P19)]          # exp6/runall6b.sh
              + [s for p in ("Port1_SITE0", P19) for s in (["run6.py", "--port", p], ["run6.py", "--port", p, "--dmax", "400"],
                                                           ["run6.py", "--port", p, "--dmax", "1500"])]),
    "exp7B": ("exp7", [["coarse.py", "--port", P18, "--h", h, "--variant", "B"] for h in ("5000", "2000", "1000", "500", "250")]),  # runB.sh
    "exp7P": ("exp7", [["coarse.py", "--port", p, "--h", h, "--variant", "B"] for p in ("Port1_SITE0", P19) for h in ("1000", "5000")]  # runP.sh
              + [["coarse.py", "--port", P18, "--h", "1000", "--variant", "C"]]),
    "exp8": ("exp8", [["run8.py", "--tag", "260729", "--port", P18], ["run8.py", "--tag", "260804", "--port", P18]]  # runall8.sh
             + [["run8.py", "--tag", "260729", "--port", p] for p in ("Port14_SITE0", "Port1_SITE0", P19, "Port16_SITE0", "Port7_SITE0")]),
    "exp9": ("exp9", [["run9.py", "--port", P18, "--freqs", "few"], ["run9.py", "--port", "Port7_SITE0", "--freqs", "few"],  # runall9.sh
                      ["run9.py", "--port", "Port14_SITE0", "--freqs", "ladder", "--loops"], ["run9.py", "--port", "Port16_SITE0", "--freqs", "few"],
                      ["run9.py", "--port", P19, "--freqs", "few", "--gnd", "s3", "--loops"],
                      ["run9.py", "--port", P19, "--freqs", "few", "--gnd", "s3scaled", "--loops"]]
             + [["run9.py", "--port", P19, "--freqs", "ladder", "--nonewidth", w] for w in ("10", "60", "drop")]),
    "exp9chain2": ("exp9", [["run9.py", "--port", P19, "--freqs", "few", "--nonewidth", "60"],             # chain2.sh
                            ["run9.py", "--port", P19, "--freqs", "few", "--nonewidth", "drop"],
                            ["run9.py", "--port", P19, "--freqs", "few", "--gnd", "s3", "--loops"],
                            ["run9.py", "--port", P19, "--freqs", "few", "--gnd", "s3scaled", "--loops"],
                            ["run9.py", "--port", "Port14_SITE0", "--freqs", "few", "--loops"],
                            ["run9.py", "--port", "Port16_SITE0", "--freqs", "few"]]),
    "exp9mesh": ("exp9", [["run9.py", "--port", P19, "--freqs", "few", "--mesh", "h100_isl50"],            # mesh9.sh
                          ["run9.py", "--port", P19, "--freqs", "few", "--mesh", "h100"],
                          ["run9.py", "--port", P19, "--freqs", "few", "--loops"]]),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chain", nargs="?", choices=sorted(CHAINS))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-going", action="store_true", help="continue after a failing step")
    a = ap.parse_args()
    if a.list or not a.chain:
        for k, (d, steps) in CHAINS.items():
            print(f"{k:11s} ({d}, {len(steps)} steps)")
            for s in steps:
                print("    python", " ".join(s))
        return 0
    exp, steps = CHAINS[a.chain]
    cwd = Path(__file__).resolve().parents[1] / exp
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")  # children print µ/Ω; Windows pipes default to the ANSI code page
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_DIR / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    log = work_dir(exp) / f"chain_{a.chain}.log"
    rc_all = 0
    with open(log, "a", encoding="utf-8") as lf:
        for s in steps:
            cmd = [sys.executable, *s]
            line = f"+ ({cwd.name}) python {' '.join(s)}"
            print(line, flush=True); lf.write(line + "\n"); lf.flush()
            if a.dry_run:
                continue
            t0 = time.time()
            with subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace") as pr:
                for out in pr.stdout:  # type: ignore[union-attr]
                    sys.stdout.write(out); lf.write(out)
                rc = pr.wait()
            msg = f"= rc {rc} wall {time.time()-t0:.0f}s"
            print(msg, flush=True); lf.write(msg + "\n"); lf.flush()
            if rc:
                rc_all = rc
                if not a.keep_going:
                    break
    print("log:", log)
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
