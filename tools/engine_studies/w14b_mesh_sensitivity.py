#!/usr/bin/env python
"""W14-b -- 격자 민감도 측정 (사전 등록: `docs/engine/W14_PLAN_2026-09-20.md` §W14-b).

재현 영수증 9케이스를 h in {400, 200, 100} um 로 각각 풀고(다른 옵션은 영수증과 동일:
`FLAGS_P`, reference `powersi-compatible`, `fh=50`, `top_h=50`, `fringe=True`), h=200 기준으로
|Z| 의 dB 차이를 잰다.  서브타일 물리 크기는 10 um 로 고정하므로 `sub=(h/10, 10, 10)`.

    run     --case <tag>:<port> --h <400|200|100>    # 1케이스 1격자 -> WORK/w14b/<tag>_<port>_h<h>.json
    summary                                          # 영수증 집계 -> summary.json + 그림 2장

규칙:
* 엔진 공개 API(`Design.open().rail().build().solve()`)만 쓴다.  `src/` 는 건드리지 않는다.
* **덮어쓰기 금지**: 영수증 파일이 이미 있으면 건너뛰고 exit 0 (그래서 재개 가능하다).
* h=200 은 커밋된 exp28/exp30 영수증과 대조해 GPU 계약(패키지 1e-8, PCB 1e-6) 안에 드는지
  **assert** 한다 -- 이 하네스가 재현 경로와 같은 것을 계산하고 있다는 증거.
* 캐시: 엔진의 디스크 캐시는 **추출 캐시뿐**이고(`cache.CacheDir`: 키 = SPD sha256, 포트,
  max_layers, cache_format, parser_version) 빌드 결과는 캐시하지 않는다.  `prepare()` 는
  `ModelOptions` 를 보지 않으므로 h/sub 가 키에 없어도 격자 변형끼리 충돌할 수 없다 -> 세 h 가
  같은 캐시 디렉터리(`SPD_PI_ENGINE_CACHE`)를 공유한다.

환경: `SPD_PI_DATA_DIR`(SPD + analysis/*.npz), `SPD_PI_WORK_DIR`, `SPD_PI_ENGINE_CACHE`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from spd_pi_engine import Backend, Design, FLAGS_P, ModelOptions, ladder_freqs

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "engine" / "fixtures"
SPD_SHA256 = json.loads((FIXTURES / "spd_sha256.json").read_text(encoding="utf-8"))
FIGDIR = REPO / "docs" / "engine" / "figures"

#: 설계 id -> PowerSI Zdiag npz (tests/engine/conftest.REF_NPZ 와 같은 표)
REF_NPZ = {"260729": "S4LB002_260729_Zdiag.npz", "260804": "S4LB002_260804_Zdiag.npz",
           "s5m6585": "s5m6585_Zdiag.npz"}

#: 계획 §W14-b 의 9케이스 (tests/engine/test_reproduction.PKG_CASES + PCB_CASES)
PKG_CASES = [("260729", "Port1_SITE0"), ("260729", "Port7_SITE0"), ("260729", "Port14_SITE0"),
             ("260729", "Port16_SITE0"), ("260729", "Port18_SITE0"), ("260729", "Port19_SITE0"),
             ("260804", "Port18_SITE0")]
PCB_CASES = [("s5m6585", "Port1_U1_0"), ("s5m6585", "Port50_U1_0")]
CASES = PKG_CASES + PCB_CASES
H_VALUES = (400, 200, 100)

#: h=200 하네스 검증 허용치 = GPU 정확도 계약(README §4 / E3): 패키지 1e-8, PCB 전 대역 1e-6.
RECEIPT_TOL = {"package": 1e-8, "pcb": 1e-6}

#: 판정 규칙(동결, 계획 §W14-b): 제품 허용치를 27점 전체에 적용.
RMS_TOL_DB, MAX_TOL_DB = 0.2, 0.5


def kind(tag: str) -> str:
    return "pcb" if tag == "s5m6585" else "package"


def data_dir() -> Path:
    v = os.environ.get("SPD_PI_DATA_DIR")
    if not v:
        sys.exit("SPD_PI_DATA_DIR is not set")
    return Path(v)


def work_dir() -> Path:
    v = os.environ.get("SPD_PI_WORK_DIR")
    if not v:
        sys.exit("SPD_PI_WORK_DIR is not set")
    p = Path(v) / "w14b"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    v = os.environ.get("SPD_PI_ENGINE_CACHE")
    return Path(v) if v else Path(os.environ["SPD_PI_WORK_DIR"]) / "engine_cache"


def spd_path(tag: str) -> Path:
    p = data_dir() / SPD_SHA256[tag]["file"]
    if not p.is_file():
        sys.exit(f"SPD not found: {p}")
    return p


def ref_freq(tag: str, port: str):
    """(PowerSI 전체 주파수 격자, 이 포트의 Zdiag 열) -- tests/engine/ladder.ref_of 와 같은 규칙."""
    for d in (data_dir(), data_dir() / "analysis"):
        if (d / REF_NPZ[tag]).is_file():
            ref = np.load(d / REF_NPZ[tag], allow_pickle=True)
            names = [str(x) for x in ref["port_names"]]
            col = [i for i, n in enumerate(names) if n.split("::")[0] == port][0]
            return ref["freq"], ref["Zdiag"][:, col]
    sys.exit(f"reference npz not found: {REF_NPZ[tag]}")


def fixture(tag: str, port: str) -> dict:
    sub = "exp30" if kind(tag) == "pcb" else "exp28"
    return json.loads((FIXTURES / sub / f"result_{tag}_{port}_any_p.json").read_text(encoding="utf-8"))


def options(h: int) -> ModelOptions:
    """영수증과 같은 옵션, h 와 sub_c 만 변형(서브타일 물리 크기 h/sub_c = 10 um 고정)."""
    return ModelOptions(reference="powersi-compatible", flags=FLAGS_P, h=float(h), fh=50.0,
                        top_h=50.0, sub=(h // 10, 10, 10), fringe=True)


# ------------------------------------------------------------------ run
def run(tag: str, port: str, h: int) -> int:
    out = work_dir() / f"{tag}_{port}_h{h}.json"
    if out.exists():                                   # 덮어쓰기 금지(CLAUDE.md 규칙 2)
        print(f"SKIP (exists): {out}", flush=True)
        return 0

    fix = fixture(tag, port)
    fref, _ = ref_freq(tag, port)
    freqs = ladder_freqs(fref)
    assert len(freqs) == 27, f"ladder has {len(freqs)} points, not 27"
    assert np.array_equal(freqs, np.array(fix["freq"])), \
        "the ladder does not match the fixture freq array -- the reference grid changed"

    opt = options(h)
    t_all = time.time()
    rail = Design.open(spd_path(tag)).rail(port, cache_dir())
    gpu_error = None
    try:
        mdl = rail.build(opt, Backend(solver="auto", fast=True))
        res = mdl.solve(freqs)
    except Exception as e:                             # OOM 등 -> CPU splu 로 재시도(계획 §W14-b)
        gpu_error = f"{type(e).__name__}: {e}"
        print(f"[w14b] GPU path failed ({gpu_error}) -- retrying on splu", flush=True)
        mdl = rail.build(opt, Backend(solver="splu", fast=True))
        res = mdl.solve(freqs)
    rec = res.receipt(light=False)
    wall = time.time() - t_all

    solvers = sorted({str(s.get("solver")) for s in rec["stats"]})
    factor_s = [float(s["factor_s"]) for s in rec["stats"]]
    study = dict(
        case=f"{tag}:{port}", tag=tag, port=port, design_class=kind(tag), h=h,
        options=dict(reference=opt.reference, flags_preset="FLAGS_P", h=opt.h, fh=opt.fh,
                     top_h=opt.top_h, sub=list(opt.sub), fringe=opt.fringe,
                     sub_tile_um=opt.h / opt.sub[0]),
        backend_requested="auto" if gpu_error is None else "auto->splu",
        backend_used=solvers, gpu_used=(solvers == ["cudss"]), gpu_error=gpu_error,
        device=rec["backend"].get("device"),
        unknowns=int(rec["unknowns"]),
        build_seconds=float(rec["build_info"].get("build_seconds", float("nan"))),
        prepare_seconds=float(rail.prepare_seconds),
        solve_seconds=float(res.solve_seconds),
        factor_seconds=factor_s,
        factor_seconds_total=float(sum(factor_s)),
        factor_seconds_median=float(np.median(factor_s)),
        wall_seconds=float(wall),
        peak_rss_MB=float(rec["peak_rss_MB"]),
        nnz_LU_max=int(max(int(s["nnz_LU"]) for s in rec["stats"])),
    )

    if h == 200:                                       # 하네스 검증: 커밋된 영수증 재현
        Zf = np.array(fix["Z_re"]) + 1j * np.array(fix["Z_im"])
        rel = np.abs(res.Z - Zf) / np.abs(Zf)
        tol = RECEIPT_TOL[kind(tag)]
        study.update(receipt_check=dict(
            fixture=f"{'exp30' if kind(tag) == 'pcb' else 'exp28'}/result_{tag}_{port}_any_p.json",
            max_rel_dZ=float(rel.max()), at_freq=float(freqs[int(rel.argmax())]), tol=tol,
            unknowns_fixture=int(fix["unknowns"]), unknowns_match=int(fix["unknowns"]) == int(rec["unknowns"]),
            passed=bool(rel.max() <= tol)))
        assert int(fix["unknowns"]) == int(rec["unknowns"]), \
            f"unknowns {rec['unknowns']} != fixture {fix['unknowns']} -- the h=200 mesh moved"
        assert rel.max() <= tol, \
            f"h=200 vs the committed receipt: max rel {rel.max():.3e} > {tol:.0e}"

    rec["study"] = study
    out.write_text(json.dumps(rec, indent=1, default=float), encoding="utf-8")
    print(f"[w14b] {tag}:{port} h={h} N={study['unknowns']} wall={wall:.0f}s "
          f"gpu={study['gpu_used']} -> {out}", flush=True)
    return 0


# ------------------------------------------------------------------ summary
def metrics(Zh, Z200, freqs) -> dict:
    """계획 §W14-b 의 지표: dB 차이 27점 RMS/max, f >= 100 kHz 구간의 RMS/max, 100 kHz Re Z 비."""
    d = 20.0 * np.log10(np.abs(Zh) / np.abs(Z200))
    hi = freqs >= 1e5
    k100 = int(np.argmin(np.abs(freqs - 1e5)))
    return dict(
        rms_db=float(np.sqrt(np.mean(d ** 2))), max_db=float(np.abs(d).max()),
        argmax_f=float(freqs[int(np.abs(d).argmax())]),
        rms_db_hi=float(np.sqrt(np.mean(d[hi] ** 2))), max_db_hi=float(np.abs(d[hi]).max()),
        argmax_f_hi=float(freqs[hi][int(np.abs(d[hi]).argmax())]),
        reZ_ratio_100k=float(Zh.real[k100] / Z200.real[k100]),
        delta_db=[float(x) for x in d],
        passed=bool(np.sqrt(np.mean(d ** 2)) <= RMS_TOL_DB and np.abs(d).max() <= MAX_TOL_DB))


def summary() -> int:
    work = work_dir()
    out, missing = {}, []
    for tag, port in CASES:
        case, per_h = f"{tag}:{port}", {}
        for h in H_VALUES:
            p = work / f"{tag}_{port}_h{h}.json"
            if p.is_file():
                per_h[h] = json.loads(p.read_text(encoding="utf-8"))
            else:
                missing.append(f"{case} h={h}")
        entry = dict(design_class=kind(tag), cost={}, pairs={})
        for h, rec in per_h.items():
            s = rec["study"]
            entry["cost"][str(h)] = {k: s[k] for k in ("unknowns", "build_seconds", "solve_seconds",
                                                       "factor_seconds_median", "factor_seconds_total",
                                                       "wall_seconds", "peak_rss_MB", "gpu_used",
                                                       "nnz_LU_max")}
            if h == 200 and "receipt_check" in s:
                entry["receipt_check"] = s["receipt_check"]
        if 200 in per_h:
            f200 = np.array(per_h[200]["freq"], float)
            Z200 = np.array(per_h[200]["Z_re"]) + 1j * np.array(per_h[200]["Z_im"])
            for h in (400, 100):
                if h not in per_h:
                    continue
                fh = np.array(per_h[h]["freq"], float)
                assert np.array_equal(fh, f200), f"{case} h={h}: frequency grid differs from h=200"
                Zh = np.array(per_h[h]["Z_re"]) + 1j * np.array(per_h[h]["Z_im"])
                entry["pairs"][f"{h}_vs_200"] = metrics(Zh, Z200, f200)
        out[case] = entry

    def group_pass(pair, cases):
        got = [out[f"{t}:{p}"]["pairs"].get(pair) for t, p in cases]
        return dict(complete=all(g is not None for g in got),
                    passed=bool(got and all(g is not None and g["passed"] for g in got)),
                    failing=[f"{t}:{p}" for (t, p), g in zip(cases, got) if g is not None and not g["passed"]])

    groups = {pair: dict(all=group_pass(pair, CASES), package=group_pass(pair, PKG_CASES),
                         pcb=group_pass(pair, PCB_CASES))
              for pair in ("400_vs_200", "100_vs_200")}
    if groups["400_vs_200"]["all"]["passed"]:
        verdict, rule = "R1", "(200 vs 400) 9케이스 모두 통과 -> W14-c 기본 검사 = \"coarse\""
    elif groups["100_vs_200"]["all"]["passed"]:
        verdict, rule = "R2", "R1 실패, (100 vs 200) 9케이스 모두 통과 -> 기본 검사 = \"fine\""
    else:
        verdict, rule = "R3", ("둘 다 실패 -> W11-b 면제 유지, 관리자는 convergence_check=\"none\" "
                               "기본, 측정된 민감도를 validity 노트에 기록")

    doc = dict(generated=time.strftime("%Y-%m-%d %H:%M:%S"),
               rule=dict(rms_tol_db=RMS_TOL_DB, max_tol_db=MAX_TOL_DB,
                         applied_to="27 ladder points (all)", source="W14_PLAN_2026-09-20.md §W14-b"),
               verdict=verdict, verdict_rule=rule, groups=groups, missing=missing, cases=out)
    (work / "summary.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    figures(out, work)
    print(json.dumps({k: v for k, v in doc.items() if k != "cases"}, indent=1, ensure_ascii=False))
    print(f"-> {work / 'summary.json'}")
    return 0


def figures(out: dict, work: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGDIR.mkdir(parents=True, exist_ok=True)
    cases = [f"{t}:{p}" for t, p in CASES if out[f"{t}:{p}"]["pairs"]]
    if not cases:
        print("[w14b] no pairs to plot")
        return

    # 1) 케이스별 dB 스펙트럼
    fig, axes = plt.subplots(3, 3, figsize=(13, 9), sharex=True)
    work_f = None
    for ax, case in zip(axes.ravel(), cases):
        rec = json.loads((work / (case.replace(":", "_") + "_h200.json")).read_text(encoding="utf-8"))
        work_f = np.array(rec["freq"], float)
        for h, c in ((400, "tab:red"), (100, "tab:blue")):
            m = out[case]["pairs"].get(f"{h}_vs_200")
            if m:
                ax.semilogx(work_f, m["delta_db"], color=c, marker=".", ms=3,
                            label=f"h={h} (RMS {m['rms_db']:.2f}, max {m['max_db']:.2f} dB)")
        for y in (-MAX_TOL_DB, MAX_TOL_DB):
            ax.axhline(y, color="k", ls="--", lw=0.7)
        ax.axvline(1e5, color="grey", ls=":", lw=0.7)
        ax.set_title(case, fontsize=9)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=6)
    for ax in axes.ravel()[len(cases):]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("f [Hz]")
    for ax in axes[:, 0]:
        ax.set_ylabel(r"$\Delta$ = 20 log10(|Z_h| / |Z_200|) [dB]")
    fig.suptitle("W14-b: mesh sensitivity vs h=200 um (27-point ladder, dashed = +/-0.5 dB)")
    fig.tight_layout()
    fig.savefig(FIGDIR / "w14b_delta_spectra.png", dpi=130)
    plt.close(fig)

    # 2) RMS / max 막대
    x = np.arange(len(cases))
    fig, ax = plt.subplots(figsize=(13, 5))
    for i, (h, c) in enumerate(((400, "tab:red"), (100, "tab:blue"))):
        rms = [out[c2]["pairs"].get(f"{h}_vs_200", {}).get("rms_db", np.nan) for c2 in cases]
        mx = [out[c2]["pairs"].get(f"{h}_vs_200", {}).get("max_db", np.nan) for c2 in cases]
        ax.bar(x + (i - 0.5) * 0.42 - 0.1, rms, 0.2, color=c, label=f"h={h} RMS")
        ax.bar(x + (i - 0.5) * 0.42 + 0.1, mx, 0.2, color=c, alpha=0.45, label=f"h={h} max|$\\Delta$|")
    ax.axhline(RMS_TOL_DB, color="green", ls="--", lw=1, label="RMS tol 0.2 dB")
    ax.axhline(MAX_TOL_DB, color="red", ls="--", lw=1, label="max tol 0.5 dB")
    ax.set_xticks(x)
    ax.set_xticklabels([c.replace(":", "\n") for c in cases], fontsize=8)
    ax.set_ylabel("dB")
    ax.set_yscale("log")
    ax.set_title("W14-b: |Z| deviation vs h=200 um (27 ladder points)")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIGDIR / "w14b_rms_max.png", dpi=130)
    plt.close(fig)
    print(f"[w14b] figures -> {FIGDIR / 'w14b_delta_spectra.png'}, {FIGDIR / 'w14b_rms_max.png'}")


# ------------------------------------------------------------------ cli
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="solve one case at one h")
    r.add_argument("--case", required=True, help="<tag>:<port>, e.g. 260729:Port18_SITE0")
    r.add_argument("--h", required=True, type=int, choices=list(H_VALUES))
    sub.add_parser("summary", help="aggregate the receipts, write summary.json and the figures")
    a = ap.parse_args(argv)
    if a.cmd == "summary":
        return summary()
    tag, _, port = a.case.partition(":")
    if (tag, port) not in CASES:
        sys.exit(f"unknown case {a.case!r}; known: {[f'{t}:{p}' for t, p in CASES]}")
    return run(tag, port, a.h)


if __name__ == "__main__":
    raise SystemExit(main())
