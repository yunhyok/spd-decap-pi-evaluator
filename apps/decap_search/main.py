"""CLI for A1 (decap placement search).  Run it from the repository root:

    python -m apps.decap_search.main --spd PATH --port NAME --cache DIR --outdir DIR
        [--freqs ladder|1e5,1e6,...] [--mask "1e5:2e-3,1e6:1.5e-3" | --mask-from-full 1.5]
        [--solver cudss|splu] [--basis-solver cudss|splu] [--fast] [--chunk 24]

One process (W12-b, W12A_REPORT §3/§7): `rail.build` once, `mdl.decap_basis(freqs, chunk,
backend=...)`, backward elimination on the basis, then validation with
`mdl.set_decaps(cfg, replace=True)` + `mdl.solve` on the SAME model -- no second build, no
subprocess.  `--basis-solver` lets the basis run on a different backend than the direct solves
(default: same as `--solver`) -- e.g. GPU direct solves with an exact CPU basis, without paying for
a second model.

Writes `search_receipt.json` (engine basis receipt + search log + final configuration + per-config
validation records) and `summary.md` into `--outdir`, plus `basis.npz`.

Set OPENBLAS_NUM_THREADS=4: the closure is one dense LU per frequency and the default thread count
makes it 5-20x slower on this laptop (W9_REPORT §5-3).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from spd_pi_engine import (VALIDITY_NOTES, Backend, Design, FLAGS_P, ModelOptions,
                           decap_config_sha256, ladder_freqs, unique_path)

from .search import backward_eliminate, mask_from_full, parse_mask, validate

APP_VERSION = "0.2"
#: the frozen baseline (D7 / exp28 p), as `w9_gate.py` builds it.  Not a knob: A1 changes decap
#: configurations, never the physics.
OPT = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True)
MASK_LO_HZ = 1e5          # A1: the practical band is 100 kHz - 100 MHz; the ladder's 30 kHz is out
CPU_CHECK_MOUNTED = 2     # W8 §4-2: near-empty configurations are ill-conditioned on the GPU
CPU_CHECK_MAX_N = 150_000  # ponytail: splu on 275 k x 27 frequencies is hours; raise if you wait
#: E4 (W12A_REPORT §4-4): CPU (splu) basis is exact/deterministic, GPU (cuDSS) basis is
#: non-deterministic and floors at 1e-5 -- keyed on the BASIS backend, not the direct-solve one.
BASIS_TOL = {"splu": 1e-9, "cudss": 1e-5}


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                          encoding="utf-8")
    print("wrote", path, flush=True)


def freqs_of(spec) -> np.ndarray:
    if spec == "ladder":
        return np.array([f for f in ladder_freqs() if f >= MASK_LO_HZ])
    return np.array([float(x) for x in spec.split(",")])


def cumulative(removed, k) -> dict:
    return {r: None for r in removed[:k]}


# ------------------------------------------------------------------ the whole run, one process
def run(a) -> dict:
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    freqs = freqs_of(a.freqs)
    t0 = time.time()

    rail = Design.open(a.spd).rail(a.port, a.cache)
    tb = time.time()
    mdl = rail.build(OPT, Backend(solver=a.solver, fast=a.fast))
    build_s = time.time() - tb
    print(f"[search] {rail!r} N={mdl.N} build {build_s:.1f}s", flush=True)

    basis_solver = a.basis_solver or a.solver
    basis = mdl.decap_basis(freqs, chunk=a.chunk, backend=Backend(solver=basis_solver, fast=a.fast))
    basis.save(out / "basis.npz")
    mask = parse_mask(a.mask) if a.mask else mask_from_full(freqs, basis.Z({}).Z, a.mask_from_full)
    print(f"[search] mask: {len(mask)} points, {mask[0][0]:.4g}-{mask[-1][0]:.4g} Hz", flush=True)

    log = backward_eliminate(basis, mask, freqs)
    removed = log["removed"]
    #: what the validation re-solves: the start, three intermediate configurations and the final one.
    picks = sorted({round(q * len(removed)) for q in (0.25, 0.5, 0.75)} | {len(removed)})
    configs = [("all_mounted", {})] + [(f"remove_{k}", cumulative(removed, k)) for k in picks if k]

    tv = time.time()
    rows = validate(mdl, basis, configs, mask)          # same model, same process (W12-b)
    tol = BASIS_TOL[basis_solver]
    for r in rows:
        r["tol"] = tol
        r["pass"] = r["max_rel"] <= tol and r["verdict_equal"] and r["same_config"]
    validate_s = time.time() - tv

    #: W8 §4-2: near-empty configurations are ill-conditioned for a GPU basis -- cross-check them
    #: against an all-CPU model+basis when one wasn't already used.
    cpu_targets = [(n, c) for (n, c), r in zip(configs, rows) if r["n_mounted"] <= CPU_CHECK_MOUNTED]
    cpu_rows, cpu_skipped = [], None
    if cpu_targets and basis_solver != "splu":
        if int(mdl.N) > CPU_CHECK_MAX_N:
            cpu_skipped = f"N={int(mdl.N)} > {CPU_CHECK_MAX_N}: splu would take hours"
            print("[validate] CPU cross-check skipped:", cpu_skipped, flush=True)
        else:
            cmdl = Design.open(a.spd).rail(a.port, a.cache).build(OPT, Backend(solver="splu", fast=True))
            cbasis = cmdl.decap_basis(freqs, chunk=a.chunk, backend=Backend(solver="splu", fast=True))
            cpu_rows = validate(cmdl, cbasis, cpu_targets, mask)
            for r in cpu_rows:
                r["tol"], r["pass"] = 1e-9, r["max_rel"] <= 1e-9 and r["verdict_equal"]

    full = basis.resolve(log["final_config"])
    return dict(
        app="decap_search", app_version=APP_VERSION,
        created=datetime.now().isoformat(timespec="seconds"),
        spd=str(a.spd), port=a.port, rail=rail.rail_net, unknowns=int(mdl.N),
        rail_info=rail.estimate_cost(), freq=[float(f) for f in freqs],
        backend=dict(solver=a.solver, basis_solver=basis_solver, fast=a.fast, chunk=a.chunk),
        openblas_num_threads=os.environ.get("OPENBLAS_NUM_THREADS"),
        basis_receipt=basis.receipt(), search=log,
        final_config=full, final_config_sha256=decap_config_sha256(full),
        validation=dict(tol=tol, rows=rows + cpu_rows, cpu_skipped=cpu_skipped,
                        cpu_check_rule=f"n_mounted <= {CPU_CHECK_MOUNTED} (W8 §4-2)",
                        all_pass=all(r["pass"] for r in rows + cpu_rows)),
        timing=dict(prepare_s=rail.prepare_seconds, build_s=build_s, basis_s=basis.build_seconds,
                    search_s=log["seconds"], validate_s=validate_s, total_s=time.time() - t0),
        validity=dict(design_class="unknown", notes=list(VALIDITY_NOTES)))


# ------------------------------------------------------------------ receipt + summary
def write_report(rec, out: Path) -> int:
    dump(unique_path(out / "search_receipt.json"), rec)

    s, rows = rec["search"], rec["validation"]["rows"]
    top = [d for d in reversed(s["steps"])][:20]             # last ranked = most important
    m = [f"# decap_search — {Path(rec['spd']).name} {rec['port']}", "",
         f"- rail `{rec['rail']}`, sites {s['n_sites']}, unknowns {rec['unknowns']}, "
         f"freqs {len(rec['freq'])} ({rec['freq'][0]:.4g}–{rec['freq'][-1]:.4g} Hz)",
         f"- backend solver=`{rec['backend']['solver']}` basis-solver=`{rec['backend']['basis_solver']}` "
         f"fast={rec['backend']['fast']} chunk={rec['backend']['chunk']}, "
         f"OPENBLAS_NUM_THREADS={rec['openblas_num_threads']}",
         f"- mask: {len(s['mask'])} points, {s['mask'][0][0]:.4g}-{s['mask'][-1][0]:.4g} Hz", "",
         "## 결과", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 시작 여유 (전 실장) | {s['start_margin']:.4g} |",
         f"| 제거 | {len(s['removed'])} / {s['n_sites']} |",
         f"| **최소 실장 개수** | **{s['n_mounted']}** |",
         f"| 최종 여유 | {s['final_margin']:.4g} |",
         f"| 닫기 호출 수 | {s['n_closures']} |",
         f"| 기저 구축 | {rec['timing']['basis_s']:.1f} s |",
         f"| 탐색(순위+제거) | {s['seconds']:.1f} s (순위 {s['rank_seconds']:.1f} s) |",
         f"| 모델 빌드 + 추출 | {rec['timing']['build_s']:.1f} + {rec['timing']['prepare_s']:.1f} s |",
         f"| 검증 | {rec['timing']['validate_s']:.1f} s |",
         f"| **전체(한 프로세스)** | **{rec['timing']['total_s']:.1f} s** |", "",
         "## 중요도 상위 20 (마지막까지 남은 사이트)", "",
         "| # | refdes | 단독 제거 여유 | 제거됨 |", "|---|---|---|---|"]
    m += [f"| {i + 1} | {d['refdes']} | {d['solo_margin']:.4g} | {'예' if d['removed'] else '아니오'} |"
          for i, d in enumerate(top)]
    m += ["", "## 검증 (기저 닫기 vs `set_decaps(replace=True)` + 직접 풀이, 같은 모델)", "",
          "| 구성 | 실장 | solver | max \\|ΔZ\\|/\\|Z\\| | @ f | 허용 | 마스크 판정(기저/직접) | 판정 |",
          "|---|---|---|---|---|---|---|---|"]
    m += [f"| {r['name']} | {r['n_mounted']} | {r['solver']} | {r['max_rel']:.3e} | "
          f"{r['f_at_max']:.4g} Hz | {r['tol']:.0e} | "
          f"{'PASS' if r['verdict_basis'] else 'FAIL'}/{'PASS' if r['verdict_direct'] else 'FAIL'} | "
          f"{'PASS' if r['pass'] else 'FAIL'} |" for r in rows]
    if rec["validation"]["cpu_skipped"]:
        m += ["", f"CPU 교차검증 생략({rec['validation']['cpu_check_rule']}): {rec['validation']['cpu_skipped']}"]
    m += ["", "## validity (엔진 영수증)", ""] + [f"- {n}" for n in rec["validity"]["notes"]] + [""]
    p = unique_path(out / "summary.md")
    p.write_text("\n".join(m), encoding="utf-8")
    print("wrote", p, flush=True)
    print(f"[done] {rec['port']}: {s['n_mounted']}/{s['n_sites']} mounted, "
          f"validation {'PASS' if rec['validation']['all_pass'] else 'FAIL'}, "
          f"{rec['timing']['total_s']:.1f}s", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apps.decap_search.main", description=__doc__.splitlines()[0])
    p.add_argument("--spd", required=True)
    p.add_argument("--port", required=True)
    p.add_argument("--cache", required=True, help="engine extraction cache (reused, never written to by hand)")
    p.add_argument("--outdir", required=True)
    p.add_argument("--freqs", default="ladder",
                   help="'ladder' (engine ladder >= 100 kHz, A1's band) or a comma-separated list")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--mask", default=None, help='piecewise constant, e.g. "1e5:2e-3,1e6:1.5e-3"')
    g.add_argument("--mask-from-full", type=float, default=1.5,
                   help="target = FACTOR x the all-mounted |Z| (default 1.5 = A1's demo mask)")
    p.add_argument("--solver", choices=["splu", "cudss"], default="splu")
    p.add_argument("--basis-solver", choices=["splu", "cudss"], default=None,
                   help="basis-only backend override (W12-b); default: same as --solver. "
                        "splu = exact/deterministic (E4), ~1.6-2x the cudss basis wall time")
    p.add_argument("--fast", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--chunk", type=int, default=24, help="basis right-hand sides per solve")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(argv)
    rec = run(a)
    return write_report(rec, Path(a.outdir))


if __name__ == "__main__":
    sys.exit(main())
