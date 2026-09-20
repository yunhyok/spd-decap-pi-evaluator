"""Conditional ideal-plane/one-dimensional source-trace resistance sensitivity."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve

from recover_astra_field_run02 import ROOT, RUN

DIR = ROOT / "outputs/research/astra-step6e-loaded-boundary-01"
FILES = {
    DIR / "l14-source-traces.json": "eec49e0a67fc31f6da1acfa1b28a573e820c5fb9ba0d805a926b7d8426790d7e",
    DIR / "l14-trace-centerline-contacts.json": "4cca09fa80480a6a6061ef82f3f038fa65350ff0dd7c0edbee0b14108f852746",
    DIR / "l14-trace-width-contacts-02.json": "5ff72c8be567383250bd95b2d6a539a0989aa58ad1c8c32cb9a294a0f18e63a7",
    DIR / "loaded-sheet-candidate-final.json": "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d",
    RUN / "l14-island-external-current-ledger.json": "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b",
    ROOT / "docs/evaluation-research/astra_native_loaded_field_reconstruction_2026-09-07.json": "a46dfe30d8c48f1509d5010d93a4169a04a67028d482f7cbd9e55fbd76e092d5",
}


def pair(z):
    return [float(z.real), float(z.imag)]


def solve_laplacian(matrix, current, gauge):
    keep = np.delete(np.arange(current.size), gauge)
    voltage = np.zeros(current.size, dtype=complex)
    voltage[keep] = spsolve(matrix[keep][:, keep].tocsc(), current[keep])
    voltage -= voltage.mean()
    return voltage


def main(output):
    started = time.monotonic()
    inputs = {}
    for path, expected in FILES.items():
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"input mismatch: {path.name}")
        inputs[path.name] = json.loads(data)
    trace_data = inputs["l14-source-traces.json"]
    traces = trace_data["records"]
    centers = inputs["l14-trace-centerline-contacts.json"]
    widths = inputs["l14-trace-width-contacts-02.json"]
    if centers["new_interior_artwork_contact_trace_count"] or widths["counts"] != {
        "trace_pair_round_endcap_only_covered_by_one_island": 804
    }:
        raise ValueError("endpoint-only ideal-island graph has unqualified contacts")
    lengths = {r["trace_id"]: r["outside_artwork_length_um"] for r in centers["records"]}
    ledger = inputs["l14-island-external-current-ledger.json"]
    islands = {r["island_id"]: complex(*r["required_lateral_net_outgoing_a"]) for r in ledger["islands"]}
    material = [r for r in inputs["loaded-sheet-candidate-final.json"]["candidate_component"]["stackup_rows"] if r[1] == trace_data["layer"]]
    if len(material) != 1 or material[0][2] != "conductor" or material[0][3:5] != [20.0, 59590000.0]:
        raise ValueError("unqualified source conductor material")
    thickness_m, sigma = material[0][3] * 1e-6, material[0][4]
    mapping = {}
    duplicate_pairs = Counter()
    for trace in traces:
        duplicate_pairs[tuple(sorted(trace["source_node_ids"]))] += 1
        for name, owners in zip(trace["source_node_ids"], trace["endpoint_artwork_island_ids"], strict=True):
            if len(owners) > 1:
                raise ValueError("multiple endpoint artwork owners")
            mapped = owners[0] if owners else name
            if mapping.setdefault(name, mapped) != mapped:
                raise ValueError("inconsistent source endpoint mapping")
    if max(duplicate_pairs.values()) != 1:
        raise ValueError("duplicate source trace endpoint pairs")
    names = sorted(set(mapping.values()) | set(islands))
    index = {name: i for i, name in enumerate(names)}
    edges = []
    collapsed = 0
    for trace in traces:
        a, b = [index[mapping[name]] for name in trace["source_node_ids"]]
        if a == b:
            collapsed += 1
            continue
        length_m = lengths[trace["trace_id"]] * 1e-6
        width_m = trace["width_um"] * 1e-6
        resistance = length_m / (sigma * thickness_m * width_m)
        if not np.isfinite(resistance) or resistance <= 0:
            raise ValueError("nonpositive finite trace resistance")
        edges.append((a, b, resistance, trace["trace_id"]))
    first, second = np.array([(a, b) for a, b, _, _ in edges], dtype=int).T
    resistance = np.array([r for _, _, r, _ in edges])
    conductance = 1 / resistance
    matrix = coo_matrix((np.concatenate((conductance, conductance, -conductance, -conductance)),
                         (np.concatenate((first, second, first, second)), np.concatenate((first, second, second, first)))),
                        shape=(len(names), len(names))).tocsc()
    if connected_components(matrix, directed=False, return_labels=False) != 1:
        raise ValueError("source trace/artwork graph not connected")
    injection = np.array([islands.get(name, 0j) for name in names])
    correction = injection.sum() / len(islands)
    # Correct only the tiny native quotient KCL residual, never add physical
    # external drive to the internal trace nodes.
    for name in islands:
        injection[index[name]] -= correction
    voltage = solve_laplacian(matrix, injection, 0)
    alternate = solve_laplacian(matrix, injection, len(names) - 1)
    residual = float(np.max(np.abs(matrix @ voltage - injection)))
    slope = complex(injection @ voltage)  # Ordinary transpose for reciprocal Z.
    loss = complex(np.vdot(injection, voltage))
    alternate_slope = complex(injection @ alternate)
    edge_current = (voltage[first] - voltage[second]) / resistance
    edge_slope = complex(np.sum(resistance * edge_current**2))
    if residual > 1e-9 or abs(slope - alternate_slope) > 1e-9 * max(abs(slope), 1e-12):
        raise ValueError("Laplacian solve/gauge failure")
    if abs(slope - edge_slope) > 1e-9 * max(abs(slope), 1e-12) or loss.real < 0:
        raise ValueError("branch sensitivity/passive loss closure failure")
    result = {
        "program": "SPD Decap PI Evaluator v0.23.1", "status": "COMPLETED_CONDITIONAL_TRACE_R_IDEAL_LIMIT_SENSITIVITY",
        "source_input_sha256": {p.name: sha for p, sha in FILES.items()},
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "frequency_of_native_drive_hz": 1e6, "material": {"thickness_m": thickness_m, "conductivity_s_per_m": sigma},
        "graph": {"node_count": len(names), "island_count": len(islands), "finite_trace_count": len(edges), "same_ideal_island_trace_count": collapsed, "connected_components": 1},
        "numerical": {"quotient_balance_correction_per_island_a": pair(correction), "kcl_max_residual_a": residual,
                      "gauge_slope_difference_ohm": abs(slope - alternate_slope), "edge_slope_difference_ohm": abs(slope - edge_slope)},
        "d_zdd_d_epsilon_at_zero_ohm": pair(slope), "unit_drive_rms_joule_coefficient_ohm": pair(loss),
        "formula": "dZdd/depsilon at zero = i.T G^+ i, all source trace DC resistances scaled by epsilon; i.H G^+ i is a different Joule coefficient",
        "elapsed_s": time.monotonic() - started,
        "limitations": ["Conditional 1D outside-artwork trace DC R, ideal artwork islands; no finite-width bend/junction spreading, skin effect, magnetic coupling or return inductance.",
                        "Endpoint/body connectivity was checked under this ideal-island assumption; physical electrode current spreading is not certified.",
                        "This derivative at epsilon=0 is not a finite epsilon=1 impedance correction, global shadow result or PowerSI accuracy improvement.",
                        "PowerSI data is not used to set source resistance or fit parameters."]}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "graph", "numerical", "d_zdd_d_epsilon_at_zero_ohm", "unit_drive_rms_joule_coefficient_ohm", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RUN / "l14-trace-r-ideal-limit-sensitivity.json")
    args = parser.parse_args()
    toy = coo_matrix([[4., -4.], [-4., 4.]]).tocsc()
    drive = np.array([1 + 2j, -1 - 2j])
    v = solve_laplacian(toy, drive, 0)
    assert abs(drive @ v - (-.75 + 1j)) < 1e-12
    assert abs(np.vdot(drive, v) - 1.25) < 1e-12
    main(args.output)
