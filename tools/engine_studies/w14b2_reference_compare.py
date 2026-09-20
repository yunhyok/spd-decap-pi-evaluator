#!/usr/bin/env python
"""W14-b-2 -- 격자 변형의 참조(PowerSI) 대비 (사전 등록: `docs/engine/W14_PLAN_2026-09-20.md` §W14-b-2).

W14-b 가 남긴 27개 영수증(`WORK_DIR/w14b/<tag>_<port>_h<h>.json`, 9케이스 x h in {400,200,100})을
**다시 풀지 않고** 읽어서, 각 h 의 |Z| 를 PowerSI 참조와 비교한다.  새 풀이 없음, 파라미터 변경 없음.

    e_h(f) = 20 * log10(|Z_h(f)| / |Z_ref(f)|)

Z_ref 의 사다리 점 정렬은 **엔진의 `attach_reference`** 가 하는 그대로다(전체 PowerSI 격자에서
최근접 점).  새 보간을 만들지 않는다 -- 같은 함수를 호출하므로 영수증 게이트 G1-G5 도 여기서 같이
나온다.  케이스 목록·참조 npz 해석·경로는 `w14b_mesh_sensitivity` 를 그대로 import 해서 쓴다.

    python w14b2_reference_compare.py               # -> WORK_DIR/w14b/w14b2_summary.json + 그림
    python w14b2_reference_compare.py --out w14b2_summary_d10.json --no-figures   # 새 이름으로
    python w14b2_reference_compare.py --self-check  # 데이터 없이 지표/판정 규칙 자기검사

환경: `SPD_PI_DATA_DIR`(참조 npz), `SPD_PI_WORK_DIR`(영수증).
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from spd_pi_engine import attach_reference

from w14b_mesh_sensitivity import CASES, FIGDIR, H_VALUES, PCB_CASES, PKG_CASES, kind, ref_freq, work_dir

#: 계획 §W14-b-2 의 대역 경계.  사다리에는 정확히 1e6 점이 있고, 계획이 "f >= 1 MHz" 와
#: "f <= 1 MHz" 로 적었으므로 그 점은 양쪽에 모두 든다(고주파 19점 / 저주파 9점).
F_SPLIT = 1e6

#: 저주파 불변 확인 임계(계획 §W14-b-2): h 사이 RMS|e| 차이가 이 값 미만이어야 "격자 무관".
LF_INVARIANCE_TOL_DB = 0.1


def rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(np.asarray(x, float)))))


def case_entry(tag: str, port: str) -> dict:
    """한 케이스의 세 h 를 참조와 비교한다.  영수증이 없는 h 는 `missing` 에 남기고 건너뛴다."""
    work = work_dir()
    fref, zall = ref_freq(tag, port)
    entry = dict(design_class=kind(tag), missing=[], per_h={})
    freqs = None
    for h in H_VALUES:
        p = work / f"{tag}_{port}_h{h}.json"
        if not p.is_file():
            entry["missing"].append(h)
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        attach_reference(rec, fref, zall)          # 영수증 게이트와 같은 정렬 규칙
        f = np.array(rec["freq"], float)
        if freqs is None:
            freqs = f
        assert np.array_equal(f, freqs), f"{tag}:{port} h={h}: frequency grid differs"
        Z = np.array(rec["Z_re"], float) + 1j * np.array(rec["Z_im"], float)
        zr = np.array(rec["Zref_re"], float) + 1j * np.array(rec["Zref_im"], float)
        e = 20.0 * np.log10(np.abs(Z) / np.abs(zr))
        hi, lo = f >= F_SPLIT, f <= F_SPLIT
        g = rec["ladder_gates"]
        entry["per_h"][str(h)] = dict(
            rms_abs_e_hf_db=rms(e[hi]), rms_abs_e_lf_db=rms(e[lo]),
            max_abs_e_hf_db=float(np.abs(e[hi]).max()), mean_e_hf_db=float(e[hi].mean()),
            err_1MHz=float(rec["err_1MHz"]),
            f_res_model=float(rec["f_res_model"]), f_res_ref=float(rec["f_res_ref"]),
            f_res_rel_err=float(abs(rec["f_res_model"] - rec["f_res_ref"]) / rec["f_res_ref"]),
            gates=dict(g["PASS"], G5_max_rel_err=float(max(g["G5_rel_err"]))),
            gates_passed=int(sum(bool(v) for v in g["PASS"].values())),
            e_db=[float(x) for x in e])
    entry["freq"] = [float(x) for x in freqs] if freqs is not None else []
    entry["n_hf"], entry["n_lf"] = int((freqs >= F_SPLIT).sum()), int((freqs <= F_SPLIT).sum())

    p100, p200 = entry["per_h"].get("100"), entry["per_h"].get("200")
    if p100 and p200:
        e100 = np.array(p100["e_db"])[freqs >= F_SPLIT]
        e200 = np.array(p200["e_db"])[freqs >= F_SPLIT]
        entry["closer_points_hf"] = int(np.sum(np.abs(e100) < np.abs(e200)))
        entry["h100_better_hf"] = bool(p100["rms_abs_e_hf_db"] < p200["rms_abs_e_hf_db"])
        entry["rms_gain_hf_db"] = float(p100["rms_abs_e_hf_db"] - p200["rms_abs_e_hf_db"])
    lf = [v["rms_abs_e_lf_db"] for v in entry["per_h"].values()]
    if lf:
        entry["lf_spread_db"] = float(max(lf) - min(lf))
        entry["lf_invariant"] = bool(entry["lf_spread_db"] < LF_INVARIANCE_TOL_DB)
    return entry


def verdict_of(cases: dict) -> dict:
    """동결 해석 규칙 (i)/(ii)/(iii).  `cases` = case_entry 결과 표."""
    pkg = [cases[f"{t}:{p}"] for t, p in PKG_CASES if "h100_better_hf" in cases[f"{t}:{p}"]]
    pcb = [cases[f"{t}:{p}"] for t, p in PCB_CASES if "h100_better_hf" in cases[f"{t}:{p}"]]
    n_pkg_better = sum(c["h100_better_hf"] for c in pkg)
    n_pkg_worse = len(pkg) - n_pkg_better
    n_pcb_better = sum(c["h100_better_hf"] for c in pcb)
    h1 = n_pkg_better >= 6 and len(pcb) == 2 and n_pcb_better == 2
    if h1:
        rule, text = "i", ("H1 성립 -> 고주파 결손의 일부는 격자 미해상. 기본 격자 변경 여부를 "
                           "W14-b §6 비용과 함께 소유자에게 올린다.")
    elif n_pkg_worse >= 6:
        rule, text = "ii", ("h=100 이 패키지 >= 6/7 에서 더 나쁨 -> h=100 이동은 참조에서 멀어지는 "
                            "이산화 산물. h=200 유지.")
    else:
        rule, text = "iii", "혼재 -> 기록만 한다."
    return dict(rule=rule, text=text, H1=bool(h1), package_cases=len(pkg),
                package_h100_better=int(n_pkg_better), package_h100_worse=int(n_pkg_worse),
                pcb_cases=len(pcb), pcb_h100_better=int(n_pcb_better))


def figures(cases: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGDIR.mkdir(parents=True, exist_ok=True)
    keys = [f"{t}:{p}" for t, p in CASES if cases[f"{t}:{p}"]["per_h"]]
    fig, axes = plt.subplots(3, 3, figsize=(13, 9), sharex=True)
    for ax, key in zip(axes.ravel(), keys):
        c = cases[key]
        f = np.array(c["freq"], float)
        for h, col in ((400, "tab:red"), (200, "k"), (100, "tab:blue")):
            v = c["per_h"].get(str(h))
            if v:
                ax.semilogx(f, v["e_db"], color=col, marker=".", ms=3, lw=1.1,
                            label=f"h={h} (RMS|e| HF {v['rms_abs_e_hf_db']:.2f} dB)")
        ax.axhline(0.0, color="grey", lw=1.4)             # 참조 = 0 dB
        ax.axvline(F_SPLIT, color="grey", ls=":", lw=0.8)
        ax.set_title(f"{key}  ({c['design_class']})", fontsize=9)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=6)
    for ax in axes.ravel()[len(keys):]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("f [Hz]")
    for ax in axes[:, 0]:
        ax.set_ylabel(r"$e_h$ = 20 log10(|Z_h| / |Z_ref|) [dB]")
    fig.suptitle("W14-b-2: mesh variants vs the PowerSI reference (27-point ladder, 0 dB = reference)")
    fig.tight_layout()
    fig.savefig(FIGDIR / "w14b2_ref_error.png", dpi=130)
    plt.close(fig)
    print(f"[w14b2] figure -> {FIGDIR / 'w14b2_ref_error.png'}")


def compare(out_name: str = "w14b2_summary.json", figs: bool = True) -> int:
    cases = {f"{t}:{p}": case_entry(t, p) for t, p in CASES}
    v = verdict_of(cases)
    lf = {k: c.get("lf_spread_db") for k, c in cases.items()}
    doc = dict(
        generated=time.strftime("%Y-%m-%d %H:%M:%S"),
        source="W14_PLAN_2026-09-20.md §W14-b-2 (analysis only, no new solves)",
        metric=dict(definition="e_h(f) = 20*log10(|Z_h(f)|/|Z_ref(f)|)",
                    reference_alignment="spd_pi_engine.receipt.attach_reference (nearest point of "
                                        "the full PowerSI grid)",
                    f_split_Hz=F_SPLIT, lf_invariance_tol_db=LF_INVARIANCE_TOL_DB),
        verdict=v,
        lf_invariance=dict(spread_db=lf, tol_db=LF_INVARIANCE_TOL_DB,
                           max_spread_db=max((x for x in lf.values() if x is not None), default=None),
                           all_invariant=all(c.get("lf_invariant", False) for c in cases.values())),
        missing={k: c["missing"] for k, c in cases.items() if c["missing"]},
        cases=cases)
    out = work_dir() / out_name
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    if figs:
        figures(cases)
    print(json.dumps({k: doc[k] for k in ("verdict", "lf_invariance", "missing")},
                     indent=1, ensure_ascii=False))
    for k, c in cases.items():
        h = c["per_h"]
        print(f"{k:28s} HF RMS|e| 400/200/100 = "
              + " / ".join(f"{h[str(x)]['rms_abs_e_hf_db']:6.3f}" if str(x) in h else "  ----"
                           for x in (400, 200, 100))
              + f"  closer={c.get('closer_points_hf')}/{c['n_hf']}"
              + f"  gatesPASS " + "/".join(str(h[str(x)]["gates_passed"]) for x in (400, 200, 100)
                                           if str(x) in h))
    print(f"-> {out}")
    return 0


def self_check() -> int:
    """데이터 없이: 지표 부호, 대역 분할, 판정 규칙 세 갈래."""
    f = np.array([1e5, 1e6, 1e7])
    e = 20 * np.log10(np.abs(np.array([2.0, 2.0, 2.0])) / np.abs(np.array([1.0, 1.0, 1.0])))
    assert abs(rms(e) - 6.0206) < 1e-3, rms(e)
    assert int((f >= F_SPLIT).sum()) == 2 and int((f <= F_SPLIT).sum()) == 2   # 1 MHz 는 양쪽

    def fake(better_pkg, better_pcb):
        c = {}
        for i, (t, p) in enumerate(PKG_CASES):
            c[f"{t}:{p}"] = dict(h100_better_hf=i < better_pkg)
        for i, (t, p) in enumerate(PCB_CASES):
            c[f"{t}:{p}"] = dict(h100_better_hf=i < better_pcb)
        return c

    assert verdict_of(fake(7, 2))["rule"] == "i"
    assert verdict_of(fake(6, 2))["rule"] == "i"
    assert verdict_of(fake(6, 1))["rule"] == "iii"      # 패키지는 되는데 PCB 가 안 되면 혼재
    assert verdict_of(fake(1, 0))["rule"] == "ii"       # 7 중 6 이 더 나쁨
    assert verdict_of(fake(2, 0))["rule"] == "iii"
    print("SELF-CHECK PASS (metric sign, band split incl. 1 MHz in both, rules i/ii/iii)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true", help="규칙 자기검사만 하고 끝낸다")
    ap.add_argument("--out", default="w14b2_summary.json",
                    help="요약 JSON 이름(WORK_DIR/w14b 안). 기존 요약을 덮어쓰지 않으려면 새 이름을 준다")
    ap.add_argument("--no-figures", action="store_true", help="그림을 다시 그리지 않는다")
    a = ap.parse_args(argv)
    return self_check() if a.self_check else compare(a.out, not a.no_figures)


if __name__ == "__main__":
    raise SystemExit(main())
