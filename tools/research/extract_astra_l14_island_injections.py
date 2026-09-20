"""Source-island external current ledger at the saved native 1 MHz field."""
from __future__ import annotations

import argparse
from hashlib import file_digest
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import csc_matrix

from recover_astra_field_run02 import RUN, PINS

ROOT = Path(__file__).resolve().parents[2]
CENSUS = ROOT / "outputs/research/astra-step6e-loaded-boundary-01/l14-endpoint-polygon-census.json"
CENSUS_SHA = "82dcc35354101389509e87a8ef9eb050b0b47af50cb0d1a0156c26d1bd73a152"


def pair(z: complex) -> list[float]:
    return [float(z.real), float(z.imag)]


def incoming_current(first: int, second: int, target: int, current: complex) -> complex:
    if (first == target) == (second == target):
        raise ValueError("edge must have exactly one target endpoint")
    return -current if first == target else current


def run(output: Path) -> None:
    start = time.monotonic()
    inputs = {}
    for path, expected in [(CENSUS, CENSUS_SHA), *[(RUN / name, PINS[name]) for name in (
        "raw-field-snapshot.npz", "derived-field-observation.npz")]]:
        with path.open("rb") as handle:
            actual = file_digest(handle, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"input hash mismatch: {path.name}")
        inputs[path.name] = actual
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    islands = [p["island_id"] for p in census["component"]["polygons"]]
    if len(islands) != 110 or len(set(islands)) != 110:
        raise ValueError("source island inventory mismatch")
    lookup = {name: i for i, name in enumerate(islands)}
    via_in = np.zeros(110, dtype=complex)
    gc_out = np.zeros(110, dtype=complex)
    via_count = np.zeros(110, dtype=int)
    partial_count = np.zeros(110, dtype=int)
    joined = []
    with np.load(RUN / "raw-field-snapshot.npz", allow_pickle=False) as raw, np.load(
        RUN / "derived-field-observation.npz", allow_pickle=False
    ) as derived:
        def text(key: str):
            return json.loads(raw[key].tobytes().decode("utf-8"))

        surface = {name: i for i, name in enumerate(text("surface_node_ids"))}
        source_to_active = raw["global_to_active_indices"][raw["surface_to_reduced_indices"]]
        active = source_to_active[[surface[name] for name in islands]]
        if np.any(active < 0) or np.unique(active).size != 1:
            raise ValueError("islands are not the same active quotient")
        target = int(active[0])
        first, second = raw["finite_first_active_indices"], raw["finite_second_active_indices"]
        incidence = set(np.flatnonzero((first == target) ^ (second == target)).tolist())
        if len(incidence) != 2110:
            raise ValueError("unexpected external finite-link incidence")
        original = raw["finite_active_original_indices"]
        all_ids = text("all_finite_link_ids")
        link_index = {all_ids[int(original[i])]: i for i in incidence}
        if len(link_index) != 2110:
            raise ValueError("duplicate native link IDs")
        currents = derived["finite_current_a_positive_to_negative"].reshape(-1)
        seen = set()
        for row in census["boundary"]["endpoints"]:
            link_id = row["native_link"]["link_id"]
            if link_id in seen or len(row["polygon_matches"]) != 1:
                raise ValueError("ambiguous source boundary")
            seen.add(link_id)
            index = link_index[link_id]
            island = row["polygon_matches"][0]["island_id"]
            value = incoming_current(int(first[index]), int(second[index]), target, currents[index])
            via_in[lookup[island]] += value
            via_count[lookup[island]] += 1
            joined.append({"link_id": link_id, "island_id": island,
                           "active_finite_index": index, "into_l14_a": pair(value)})
        if set(link_index) != seen:
            raise ValueError("incomplete source finite boundary")
        tp, tn = derived["termination_positive_active_indices"], derived["termination_negative_active_indices"]
        if np.any((tp == target) | (tn == target)):
            raise ValueError("termination on quotient needs a source-island ownership map")
        if np.any(raw["solve_port_reduced_nodes"] == raw["active_global_reduced_indices"][target]):
            raise ValueError("direct port on quotient needs source-island drive ownership")
        voltage = raw["active_voltage"].reshape(-1)
        coeff = raw["partial_actual_1mhz_dispersion_admittance_scale_s"]
        partials = []
        for j in range(36):
            prefix = f"partial_{j:02d}"
            names = text(prefix + "_net_names")
            matching = [(k, lookup[name]) for k, name in enumerate(names) if name in lookup]
            if not matching:
                continue
            indices = source_to_active[[surface[name] for name in names]]
            if np.any(indices < 0):
                raise ValueError("partial crosses observed active boundary")
            matrix = csc_matrix((raw[prefix + "_nominal_c_data"], raw[prefix + "_nominal_c_indices"],
                                 raw[prefix + "_nominal_c_indptr"]), shape=tuple(raw[prefix + "_nominal_c_shape"]))
            current = complex(coeff[j]) * (matrix @ voltage[indices])
            for k, island_index in matching:
                gc_out[island_index] += current[k]
                partial_count[island_index] += 1
            partials.append({"index": j, "matched_islands": len(matching),
                             "outgoing_total_a": pair(sum(current[k] for k, _ in matching))})
    injection = via_in - gc_out
    residual = abs(injection.sum())
    tolerance = 1e-9 * max(1.0, float(np.abs(via_in).sum()), float(np.abs(gc_out).sum()))
    if residual > tolerance:
        raise ValueError(f"quotient KCL failure: {residual} > {tolerance}")
    result = {
        "program": "SPD Decap PI Evaluator v0.23.1", "status": "COMPLETED_NATIVE_EXTERNAL_ISLAND_CURRENT_LEDGER",
        "frequency_hz": 1e6, "source_input_sha256": inputs, "script_sha256": __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest(),
        "active_quotient_index": target, "source_island_count": 110, "finite_boundary_count": 2110,
        "partial_rows": partials, "direct_termination_or_port_count": 0,
        "quotient_kcl_residual_a": residual, "quotient_kcl_tolerance_a": tolerance,
        "via_into_total_a": pair(via_in.sum()), "gc_outgoing_total_a": pair(gc_out.sum()),
        "sum_abs_island_injections_a": float(np.abs(injection).sum()),
        "islands": [{"island_id": name, "via_count": int(via_count[i]), "partial_row_count": int(partial_count[i]),
                     "via_into_a": pair(via_in[i]), "gc_outgoing_a": pair(gc_out[i]),
                     "required_lateral_net_outgoing_a": pair(injection[i])} for i, name in enumerate(islands)],
        "finite_boundary": joined, "elapsed_s": time.monotonic() - start,
        "limitations": ["Center-polygon ownership only; pad/electrode footprints are not certified.",
                        "These are external source-island injections within the original equipotential model, not inferred individual ideal trace currents.",
                        "No finite lateral R/L, replacement, accuracy improvement or PowerSI fitting is computed."]}
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, separators=(",", ":"), allow_nan=False)
        handle.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "elapsed_s", "quotient_kcl_residual_a", "via_into_total_a", "gc_outgoing_total_a", "sum_abs_island_injections_a")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path, default=RUN / "l14-island-external-current-ledger.json")
    args = parser.parse_args()
    assert incoming_current(1, 2, 1, 3 + 4j) == -3 - 4j
    assert incoming_current(1, 2, 2, 3 + 4j) == 3 + 4j
    if args.self_test:
        print("current orientation self-test PASS")
    else:
        run(args.output)
