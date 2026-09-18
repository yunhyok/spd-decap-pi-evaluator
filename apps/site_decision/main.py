"""CLI for A2: `decision_receipt.json` + `summary.md` for one SITE0 / SITE1 pair.

Run from the repository root (the workers re-import this package):

    python -m apps.site_decision.main --spd D:\\...\\S4LB002-2Para_260729_1_injected.spd \\
        --port Port18_SITE0 --cache %SPD_PI_WORK_DIR%\\engine_cache \\
        --outdir %SPD_PI_WORK_DIR%\\apps\\site_decision \\
        --targets top10 --delta 0.05 --freqs ladder --ref-npz ...\\S4LB002_260729_Zdiag.npz \\
        --solver cudss --fast
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from spd_pi_engine import Backend

from . import decide


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


def parse_mask(text):
    """`--mask 0.01` (flat) or `--mask 1e5:0.005,1e8:0.02` (step: the first f_hi >= f wins)."""
    if not text:
        return None
    if ":" not in text:
        return float(text)
    return [tuple(float(v) for v in part.split(":")) for part in text.split(",")]


def parse_targets(text):
    """`top10` -> 10 (the 10 largest-capacitance sites) | `C2001_0,C2002_0` -> that list."""
    if text.startswith("top"):
        return int(text[3:])
    return [t.strip() for t in text.split(",") if t.strip()]


def summary_md(out) -> str:
    """The report table: one row per target site."""
    t, am, mt = out["timing"], out["all_mounted"], out["match"]
    p0, p1 = out["ports"]
    L = [f"# SITE decap 미실장 판단 — {p0} / {p1}", "",
         f"- SPD: `{Path(out['spd']).name}`",
         f"- 레일: `{out['rails'][0]}` / `{out['rails'][1]}`  (미지수 "
         f"{out['unknowns'][0]:,} / {out['unknowns'][1]:,})",
         f"- 주파수 {len(out['freq'])}점: " + ", ".join(f"{f:.4g}" for f in out["freq"]),
         f"- 허용 편차 δ = {100 * out['delta']:.3g} %, 마스크 = {out['mask']}",
         f"- 백엔드: solver={out['backend']['solver']} fast={out['backend']['fast']}",
         f"- 사이트 대응: 규칙 `{mt['rule']}` — {mt['matched']}/{mt['n_sites_site0']} 매칭, "
         f"미매칭 {len(mt['unmatched'])}, model_id 불일치 {len(mt['model_id_mismatches'])}, "
         f"기하 규칙 일치 {mt['geometry_rule']['agrees_with_refdes']}/{mt['n_sites_site0']}",
         f"- 전(全)실장 SITE 편차: |Z| {am['dev_pct']:.4f} %, 복소 {am['dev_complex_pct']:.4f} % "
         f"(W10 실측 {am['w10_reported_pct']} %)",
         f"- 소요: {t['wall_seconds']:.1f} s = 빌드 {t['builds']}회 + solve {t['solves']}회 "
         f"(SITE0 {t['site0']['wall_seconds']:.1f} s / SITE1 {t['site1']['wall_seconds']:.1f} s)",
         "", f"판정 규칙: {out['rule']}", "",
         "| # | refdes (SITE0) | 짝 (SITE1) | 모델 | C [µF] | 미실장 SITE 편차 [%] | "
         "SITE0 Z 증가 [%] | SITE1 Z 증가 [%] | 판정 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for i, s in enumerate(out["sites"], 1):
        L.append(f"| {i} | {s['refdes']} | {s['partner']} | {s['model_id']} | "
                 f"{1e6 * s['capacitance_F']:.3f} | {s['dev_pct']:.3f} | "
                 f"{s['increase_site0_pct']:.2f} | {s['increase_site1_pct']:.2f} | "
                 f"{'미실장 가능' if s['unmount_ok'] else '유지'} |")
    L += ["", "## validity (엔진 정확도 범위)", ""]
    L += [f"- {n}" for n in out["validity"]["notes"]]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="apps.site_decision", description=__doc__)
    ap.add_argument("--spd", required=True)
    ap.add_argument("--port", required=True, help="the SITE0 port, e.g. Port18_SITE0")
    ap.add_argument("--partner", default="auto", help="auto = the /0 vs /1 twin of --port")
    ap.add_argument("--cache", required=True, help="engine extraction cache directory")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--targets", default="top10", help="top10 | C2001_0,C2002_0,...")
    ap.add_argument("--delta", type=float, default=0.05)
    ap.add_argument("--freqs", default="ladder", help="ladder | 3e4,1e5,...")
    ap.add_argument("--ref-npz", default=None, help="snap the ladder to this PowerSI grid (W8/W10)")
    ap.add_argument("--mask", default=None, help="0.01 | 1e5:0.005,1e8:0.02 (optional)")
    ap.add_argument("--solver", default="auto", choices=["auto", "cudss", "splu"])
    ap.add_argument("--fast", action="store_true", help="engine assemble accelerations (W8 standard)")
    a = ap.parse_args(argv)

    freqs = (decide.ladder(a.ref_npz, a.port) if a.freqs == "ladder"
             else [float(x) for x in a.freqs.split(",")])
    t0 = time.time()
    out = decide.evaluate(a.spd, a.port, None if a.partner == "auto" else a.partner,
                          targets=parse_targets(a.targets), freqs=freqs, delta=a.delta,
                          backend=Backend(solver=a.solver, fast=a.fast), cache_dir=a.cache,
                          mask=parse_mask(a.mask))
    out["cli"] = dict(argv=sys.argv[1:], wall_seconds=time.time() - t0)

    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "decision_receipt.json").write_text(
        json.dumps(out, indent=1, default=_json_default), encoding="utf-8")
    (outdir / "summary.md").write_text(summary_md(out), encoding="utf-8")
    n_ok = sum(1 for s in out["sites"] if s["unmount_ok"])
    print(f"{outdir / 'decision_receipt.json'}\n{outdir / 'summary.md'}")
    print(f"all-mounted SITE deviation |Z| {out['all_mounted']['dev_pct']:.4f} % / complex "
          f"{out['all_mounted']['dev_complex_pct']:.4f} % (W10 0.345 %)")
    print(f"{n_ok}/{len(out['sites'])} sites unmountable at delta={100 * a.delta:g} %, "
          f"{out['timing']['wall_seconds']:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
