"""Source axial via mutual/native-diagonal compatibility; no board stamping."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from time import perf_counter
import numpy as np
from scipy.integrate import quad
from spd_decap_pi._core.solver.via_peec import _finite_parallel_neumann_integral
from probe_astra_l25_saved_current_moments import checked

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
SOURCE = R / "astra-l25-source-sheet-01/receipt.json"
SOURCE_SHA = "a45f6601bae33dc72f9c56190cd85a42037c4046c460bedbdd7cecbc9e85851b"
BINDING = R / "astra-l25-dual-cell-source-binding-01/native-source-binding.npz"
BINDING_SHA = "c9d41f1e2b870dd854affa1db2c31bb86f24c472866fcd2125f313cb29981c6a"
KERNEL = ROOT / "src/spd_decap_pi/_core/solver/via_peec.py"
KERNEL_SHA = "61003c9a87911edebc1c1132aebd041f63bc54d5bf480f9c0a037fb36619b9e4"


def axial_integral(a, b, c, d, rho):
    if not a < b or not c < d or rho < 0:
        raise ValueError("ordered finite intervals and nonnegative spacing required")
    if rho > 0:
        return _finite_parallel_neumann_integral(a, b, c, d, rho)
    if max(a, c) < min(b, d):
        raise ValueError("coincident overlapping filaments need finite cross sections")
    length = max(b-a, d-c, abs(d-a), abs(b-c))
    def h(value):
        u = abs(value/length)
        return 0. if u == 0 else u*np.log(u)-u
    return length*(h(b-c)-h(a-c)-h(b-d)+h(a-d))


def self_check():
    a, b = .0005045, .0003725
    value = axial_integral(0., a, a, a+b, 0.)
    reference = quad(lambda x: np.log1p(a/x), 0., b, epsabs=1e-14, epsrel=1e-12)[0]
    assert abs(value/reference-1) < 1e-12
    x, w = np.polynomial.legendre.leggauss(32)
    rho = .00065
    direct = a*b/4*np.sum(w[:, None]*w[None, :]/np.sqrt(rho*rho+(a*(x[:, None]+1)/2-b*(x[None, :]+1)/2)**2))
    separated = axial_integral(0., a, 0., b, rho)
    assert abs(separated/direct-1) < 1e-12
    return {"touching_collinear_relative": float(abs(value/reference-1)), "separated_gauss_relative": float(abs(separated/direct-1))}


def run(output):
    start = perf_counter()
    checked(KERNEL, KERNEL_SHA)
    source = json.loads(checked(SOURCE, SOURCE_SHA))
    checked(BINDING, BINDING_SHA)
    with np.load(BINDING, allow_pickle=False) as archive:
        binding = {key: archive[key] for key in archive.files}
    by_index = {row["active_finite_index"]: row for row in source["via_rows"]}
    rows = [by_index[int(index)] for index in binding["native_active_finite_index"]]
    if len(rows) != 350 or len(by_index) != 350 or any(row["native_count"] != 1 for row in rows):
        raise ValueError("350 individual source vias required")
    for key, native in (("native_resistance_ohm_per_via", "native_resistance_ohm"), ("native_inductance_h_per_via", "native_inductance_h")):
        if not np.array_equal(binding[key], [row[native] for row in rows]):
            raise ValueError("native source/binding values differ")
    database = R / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
    with sqlite3.connect(database.as_uri()+"?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(perf_counter()-start > 30), 10000)
        meta = dict(db.execute("SELECT key,value FROM meta"))
        if meta != source["raw_index_meta"]:
            raise ValueError("saved raw index metadata differs")
        stack = list(db.execute("SELECT layer_ordinal,layer_name,layer_kind,thickness_um FROM stackup_layers ORDER BY layer_ordinal"))
    centers, height = {}, 0.
    for ordinal, name, kind, thickness in stack:
        if not np.isfinite(thickness) or thickness < 0:
            raise ValueError("invalid source thickness")
        centers[name] = (height+thickness/2)*1e-6
        height += thickness
    xy, spans = [], []
    for row in rows:
        via = row["source_via"]
        if via["start_x_pm"] != via["end_x_pm"] or via["start_y_pm"] != via["end_y_pm"]:
            raise ValueError("only straight source axial segments allowed")
        xy.append([via["start_x_pm"]*1e-12, via["start_y_pm"]*1e-12])
        spans.append(sorted([centers[via["start_layer_id"]], centers[via["end_layer_id"]]]))
    xy, spans = np.asarray(xy), np.asarray(spans)
    native_l = binding["native_inductance_h_per_via"]
    matrix = np.diag(native_l)
    touching, distinct_min = 0, np.inf
    for i in range(350):
        for j in range(i):
            rho = float(np.linalg.norm(xy[i]-xy[j]))
            if rho == 0:
                touching += 1
            else:
                if rho <= (rows[i]["barrel_radius_um"]+rows[j]["barrel_radius_um"])*1e-6:
                    raise ValueError("distinct axial barrels overlap")
                distinct_min = min(distinct_min, rho)
            matrix[i, j] = matrix[j, i] = 1e-7*axial_integral(*spans[i], *spans[j], rho)
        if perf_counter()-start > 30:
            raise TimeoutError("small via diagnostic exceeded30s")
    eigen = np.linalg.eigvalsh(matrix)
    normalized = matrix/np.sqrt(native_l[:, None]*native_l[None, :])
    normalized_eigen = np.linalg.eigvalsh(normalized)
    output.mkdir(parents=True, exist_ok=False)
    frozen = Path(__file__).read_bytes()
    (output/"driver-at-run.py").write_bytes(frozen)
    np.savez_compressed(output/"axial-native-l.npz", matrix_h=matrix, xy_m=xy, spans_m=spans, native_active_finite_index=binding["native_active_finite_index"])
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_CONDITIONAL_AXIAL_VIA_NATIVE_DIAGONAL_COMPATIBILITY",
              "script_sha256": hashlib.sha256(frozen).hexdigest(), "source_sha256": SOURCE_SHA, "binding_sha256": BINDING_SHA, "kernel_sha256": KERNEL_SHA,
              "stackup_rows": stack, "raw_index_meta": meta, "via_ids": [row["via_id"] for row in rows],
              "self_check": self_check(), "via_count": len(rows), "same_axis_disjoint_pairs": touching,
              "minimum_distinct_axis_spacing_m": distinct_min, "eigenvalue_range_h": [float(eigen[0]), float(eigen[-1])],
              "native_diagonal_scaled_eigenvalue_range": [float(normalized_eigen[0]), float(normalized_eigen[-1])],
              "conditional_positive_definite": bool(eigen[0] > 0), "elapsed_s": perf_counter()-start,
              "matrix_sha256": hashlib.sha256((output/"axial-native-l.npz").read_bytes()).hexdigest(),
              "scope": "350 selected L25 source axial centerline segments with original native scalar L diagonals and full filament mutual. All matrix currents point in increasing stackup z, not compiled branch orientation. Metal-center endpoints are declared geometry conventions. Positive spectrum only checks compatibility of this finite subset and chosen filament model; it does not establish correct native internal/external L partition, plated-barrel current distribution, other vias, planar return, induced loops, complete passivity, Device response or PowerSI accuracy. No self-L replacement, coefficient fitting or board stamp."}
    with (output/"result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("status", "eigenvalue_range_h", "native_diagonal_scaled_eigenvalue_range", "conditional_positive_definite", "same_axis_disjoint_pairs", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
    elif args.output is None:
        parser.error("--output required")
    else:
        run(args.output)
