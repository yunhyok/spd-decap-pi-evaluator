"""CLI for A1 (decap placement search).  Run it from the repository root:

    python -m apps.decap_search.main --spd PATH --port NAME --cache DIR --outdir DIR
        [--freqs ladder|1e5,1e6,...] [--mask "1e5:2e-3,1e6:1.5e-3" | --mask-from-full 1.5]
        [--solver cudss|splu] [--fast] [--chunk 24]

Writes `search_receipt.json` (engine basis receipt + search log + final configuration + validation)
and `summary.md` into `--outdir`, plus the intermediates `basis.npz`, `search_state.json`,
`validate.json`.

Two stages, each its own process (`--stage`, set by the driver): on the GPU one model is either a
basis model or a sweep model, never both, because cuDSS plans one RHS width per model
(W9_REPORT §2-1).  Stage 1 builds the basis and searches, stage 2 rebuilds the model and validates
the chosen configurations with `set_decaps` + a direct solve.

Set OPENBLAS_NUM_THREADS=4: the closure is one dense LU per frequency and the default thread count
makes it 5-20x slower on this laptop (W9_REPORT §5-3).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from spd_pi_engine import (VALIDITY_NOTES, Backend, DecapBasis, Design, FLAGS_P, ModelOptions,
                           decap_config_sha256)
from spd_pi_engine.cli import ladder_freqs, unique_path   # not in the package's __all__ (§요구사항)

from .search import Mask, backward_eliminate, validate

APP_VERSION = "0.1"
#: the frozen baseline (D7 / exp28 p), as `w9_gate.py` builds it.  Not a knob: A1 changes decap
#: configurations, never the physics.
OPT = ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=200.0, fh=50.0, top_h=50.0,
                   sub=(20, 10, 10), fringe=True)
MASK_LO_HZ = 1e5          # A1: the practical band is 100 kHz - 100 MHz; the ladder's 30 kHz is out
CPU_CHECK_MOUNTED = 2     # W8 §4-2: near-empty configurations are ill-conditioned on the GPU
CPU_CHECK_MAX_N = 150_000  # ponytail: splu on 275 k x 27 frequencies is hours; raise if you wait


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                          encoding="utf-8")
    print("wrote", path, flush=True)


def freqs_of(spec) -> np.ndarray:
    if spec == "ladder":
        return np.array([f for f in ladder_freqs() if f >= MASK_LO_HZ])
    return np.array([float(x) for x in spec.split(",")])


def build(a):
    rail = Design.open(a.spd).rail(a.port, a.cache)
    t = time.time()
    mdl = rail.build(OPT, Backend(solver=a.solver, fast=a.fast))
    return rail, mdl, time.time() - t


def cumulative(removed, k) -> dict:
    return {r: None for r in removed[:k]}


# ------------------------------------------------------------------ stage 1: basis + search
def stage_search(a) -> int:
    out = Path(a.outdir)
    freqs = freqs_of(a.freqs)
    t0 = time.time()
    rail, mdl, build_s = build(a)
    print(f"[search] {rail!r} N={mdl.N} build {build_s:.1f}s", flush=True)
    basis = mdl.decap_basis(freqs, chunk=a.chunk)
    basis.save(out / "basis.npz")
    mask = (Mask.parse(a.mask) if a.mask else
            Mask.from_full(freqs, basis.Z({}).Z, a.mask_from_full))
    print(f"[search] {mask!r}", flush=True)
    log = backward_eliminate(basis, mask, freqs)
    removed = log["removed"]
    #: what the validation re-solves: the start, three intermediate configurations and the final one.
    picks = sorted({round(q * len(removed)) for q in (0.25, 0.5, 0.75)} | {len(removed)})
    configs = [("all_mounted", {})] + [(f"remove_{k}", cumulative(removed, k)) for k in picks if k]
    full = basis.resolve(log["final_config"])
    dump(out / "search_state.json",
         dict(app="decap_search", app_version=APP_VERSION, stage="search",
              created=datetime.now().isoformat(timespec="seconds"),
              spd=str(a.spd), port=a.port, rail=rail.rail_net, unknowns=int(mdl.N),
              rail_info=rail.estimate_cost(), freq=[float(f) for f in freqs],
              backend=dict(solver=a.solver, fast=a.fast, chunk=a.chunk),
              openblas_num_threads=os.environ.get("OPENBLAS_NUM_THREADS"),
              basis_receipt=basis.receipt(), search=log,
              final_config=full, final_config_sha256=decap_config_sha256(full),
              validate_configs=[[n, c] for n, c in configs],
              timing=dict(prepare_s=rail.prepare_seconds, build_s=build_s,
                          basis_s=basis.build_seconds, search_s=log["seconds"],
                          stage_s=time.time() - t0)))
    return 0


# ------------------------------------------------------------------ stage 2: direct-solve check
def stage_validate(a) -> int:
    out = Path(a.outdir)
    st = json.loads((out / "search_state.json").read_text(encoding="utf-8"))
    t0 = time.time()
    rail, mdl, build_s = build(a)
    basis = DecapBasis.load(out / "basis.npz", mdl)          # checks numerics_id and N
    mask = Mask(st["search"]["mask"]["points"], st["search"]["mask"]["source"])
    configs = [(n, c) for n, c in st["validate_configs"]]
    rows = validate(mdl, basis, configs, mask)
    tol = 1e-6 if a.solver == "cudss" else 1e-9              # W9 contract: GPU basis floors at 1e-6
    for r in rows:
        r["tol"] = tol
        r["pass"] = r["max_rel"] <= tol and r["verdict_equal"] and r["same_config"]

    cpu = [(n, c) for (n, c), r in zip(configs, rows) if r["n_mounted"] <= CPU_CHECK_MOUNTED]
    cpu_rows, cpu_skipped = [], None
    if cpu and a.solver != "splu":                            # W8 §4-2 caveat, on the CPU path
        if int(mdl.N) > CPU_CHECK_MAX_N:
            cpu_skipped = f"N={int(mdl.N)} > {CPU_CHECK_MAX_N}: splu would take hours"
            print("[validate] CPU cross-check skipped:", cpu_skipped, flush=True)
        else:
            _, cmdl, _ = build(argparse.Namespace(**{**vars(a), "solver": "splu", "fast": True}))
            cpu_rows = validate(cmdl, DecapBasis.load(out / "basis.npz", cmdl), cpu, mask)
            for r in cpu_rows:
                r["tol"], r["pass"] = 1e-9, r["max_rel"] <= 1e-9 and r["verdict_equal"]

    dump(out / "validate.json",
         dict(stage="validate", backend=dict(solver=a.solver, fast=a.fast), tol=tol, rows=rows,
              cpu_rows=cpu_rows, cpu_skipped=cpu_skipped,
              cpu_check_rule=f"n_mounted <= {CPU_CHECK_MOUNTED} (W8 §4-2)",
              validity=dict(design_class="unknown", notes=list(VALIDITY_NOTES)),
              timing=dict(build_s=build_s, stage_s=time.time() - t0)))
    return 0


# ------------------------------------------------------------------ receipt + summary
def write_report(a) -> int:
    out = Path(a.outdir)
    st = json.loads((out / "search_state.json").read_text(encoding="utf-8"))
    va = json.loads((out / "validate.json").read_text(encoding="utf-8"))
    s, rows = st["search"], va["rows"] + va["cpu_rows"]
    rec = dict(app="decap_search", app_version=APP_VERSION,
               created=datetime.now().isoformat(timespec="seconds"),
               spd=st["spd"], port=st["port"], rail=st["rail"], unknowns=st["unknowns"],
               rail_info=st["rail_info"], backend=st["backend"],
               openblas_num_threads=st["openblas_num_threads"], freq=st["freq"], mask=s["mask"],
               basis_receipt=st["basis_receipt"],
               search={k: v for k, v in s.items() if k != "mask"},
               final_config=st["final_config"], final_config_sha256=st["final_config_sha256"],
               validation=dict(tol=va["tol"], rows=rows, cpu_skipped=va["cpu_skipped"],
                               cpu_check_rule=va["cpu_check_rule"],
                               all_pass=all(r["pass"] for r in rows)),
               timing={**st["timing"], "validate_s": va["timing"]["stage_s"],
                       "total_s": st["timing"]["stage_s"] + va["timing"]["stage_s"]},
               validity=va["validity"])
    dump(unique_path(out / "search_receipt.json"), rec)

    top = [d for d in reversed(s["steps"])][:20]             # last ranked = most important
    m = [f"# decap_search — {Path(st['spd']).name} {st['port']}", "",
         f"- rail `{st['rail']}`, sites {s['n_sites']}, unknowns {st['unknowns']}, "
         f"freqs {len(st['freq'])} ({st['freq'][0]:.4g}–{st['freq'][-1]:.4g} Hz)",
         f"- backend `{st['backend']['solver']}` fast={st['backend']['fast']} chunk={st['backend']['chunk']}, "
         f"OPENBLAS_NUM_THREADS={st['openblas_num_threads']}",
         f"- mask: {s['mask']['source']}", "",
         "## 결과", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 시작 여유 (전 실장) | {s['start_margin']:.4g} @ {s['start_f_bind']:.4g} Hz |",
         f"| 제거 | {len(s['removed'])} / {s['n_sites']} |",
         f"| **최소 실장 개수** | **{s['n_mounted']}** |",
         f"| 최종 여유 | {s['final_margin']:.4g} @ {s['final_f_bind']:.4g} Hz |",
         f"| 닫기 호출 수 | {s['n_closures']} |",
         f"| 기저 구축 | {st['timing']['basis_s']:.1f} s |",
         f"| 탐색(순위+제거) | {s['seconds']:.1f} s (순위 {s['rank_seconds']:.1f} s) |",
         f"| 모델 빌드 + 추출 | {st['timing']['build_s']:.1f} + {st['timing']['prepare_s']:.1f} s |",
         f"| **탐색 합계** | **{st['timing']['stage_s']:.1f} s** |",
         f"| 검증 | {va['timing']['stage_s']:.1f} s |",
         f"| 전체 | {rec['timing']['total_s']:.1f} s |", "",
         "## 중요도 상위 20 (마지막까지 남은 사이트)", "",
         "| # | refdes | 단독 제거 여유 | 제거됨 |", "|---|---|---|---|"]
    m += [f"| {i + 1} | {d['refdes']} | {d['solo_margin']:.4g} | {'예' if d['removed'] else '아니오'} |"
          for i, d in enumerate(top)]
    m += ["", "## 검증 (기저 닫기 vs `set_decaps` + 직접 풀이)", "",
          "| 구성 | 실장 | solver | max \\|ΔZ\\|/\\|Z\\| | @ f | 허용 | 마스크 판정(기저/직접) | 판정 |",
          "|---|---|---|---|---|---|---|---|"]
    m += [f"| {r['name']} | {r['n_mounted']} | {r['solver']} | {r['max_rel']:.3e} | "
          f"{r['f_at_max']:.4g} Hz | {r['tol']:.0e} | "
          f"{'PASS' if r['verdict_basis'] else 'FAIL'}/{'PASS' if r['verdict_direct'] else 'FAIL'} | "
          f"{'PASS' if r['pass'] else 'FAIL'} |" for r in rows]
    if va["cpu_skipped"]:
        m += ["", f"CPU 교차검증 생략({va['cpu_check_rule']}): {va['cpu_skipped']}"]
    m += ["", "## validity (엔진 영수증)", ""] + [f"- {n}" for n in va["validity"]["notes"]] + [""]
    p = unique_path(out / "summary.md")
    p.write_text("\n".join(m), encoding="utf-8")
    print("wrote", p, flush=True)
    print(f"[done] {st['port']}: {s['n_mounted']}/{s['n_sites']} mounted, "
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
    p.add_argument("--fast", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--chunk", type=int, default=24, help="basis right-hand sides per solve")
    p.add_argument("--stage", choices=["search", "validate"], default=None, help=argparse.SUPPRESS)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(argv)
    if a.stage:
        return dict(search=stage_search, validate=stage_validate)[a.stage](a)
    Path(a.outdir).mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    for stage in ("search", "validate"):                     # one process each (cuDSS, W9 §2-1)
        print(">>", stage, flush=True)
        rc = subprocess.run([sys.executable, "-m", "apps.decap_search.main", *argv, "--stage", stage],
                            cwd=root, env={**os.environ, "PYTHONUNBUFFERED": "1"}).returncode
        if rc:
            return rc
    return write_report(a)


if __name__ == "__main__":
    sys.exit(main())
