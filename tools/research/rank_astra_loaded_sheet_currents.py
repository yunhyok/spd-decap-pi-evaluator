"""Rank existing target-rail/GND source components by observed current throughput."""
from collections import defaultdict
from hashlib import file_digest, sha256
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import csc_matrix, triu

from recover_astra_field_run02 import ROOT, RUN, PINS
from query_astra_loaded_sheet_candidate import _open_db, RAIL
from probe_astra_native_loaded_voltage_field import _write_json


def add_branches(balance, throughput, first, second, current):
    first, second, current = np.asarray(first), np.asarray(second), np.asarray(current)
    keep = first != second
    first, second, current = first[keep], second[keep], current[keep]
    np.add.at(balance, first, -current)
    np.add.at(balance, second, current)
    np.add.at(throughput, first, np.abs(current))
    np.add.at(throughput, second, np.abs(current))


def main():
    started = time.monotonic()
    topology = ROOT / "outputs/research/astra-step4-basis-01/indexes/compiled-topology.sqlite"
    pins = {RUN / name: PINS[name] for name in ("raw-field-snapshot.npz", "derived-field-observation.npz")}
    pins[topology] = "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"
    for path, expected in pins.items():
        with path.open("rb") as handle:
            assert file_digest(handle, "sha256").hexdigest() == expected, path.name
    with _open_db(topology) as db:
        surface = json.loads(db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0])
    candidates = [row for row in surface["surface_equivalence_components"]
                  if row["net"].casefold() in (RAIL.casefold(), "dgnd")]
    with np.load(RUN / "raw-field-snapshot.npz", allow_pickle=False) as raw, np.load(
        RUN / "derived-field-observation.npz", allow_pickle=False) as derived:
        text = lambda key: json.loads(raw[key].tobytes())
        voltage = raw["active_voltage"].reshape(-1)
        n = len(voltage)
        surface_ids = {name: i for i, name in enumerate(text("surface_node_ids"))}
        surface_active = raw["global_to_active_indices"][raw["surface_to_reduced_indices"]]
        balance = np.zeros(n, dtype=complex)
        parts = {key: np.zeros(n) for key in ("finite", "termination", "gc", "port")}
        first, second = raw["finite_first_active_indices"], raw["finite_second_active_indices"]
        current = derived["finite_current_a_positive_to_negative"].reshape(-1)
        assert first.min() >= 0 and second.min() >= 0
        add_branches(balance, parts["finite"], first, second, current)
        finite_count = np.bincount(np.r_[first, second], minlength=n)
        add_branches(balance, parts["termination"], derived["termination_positive_active_indices"],
                     derived["termination_negative_active_indices"], derived["termination_current_a_positive_to_negative"].reshape(-1))
        coefficients = raw["partial_actual_1mhz_dispersion_admittance_scale_s"]
        for j in range(36):
            prefix = f"partial_{j:02d}"
            names = text(prefix + "_net_names")
            active = surface_active[[surface_ids[name] for name in names]]
            assert active.min() >= 0
            matrix = csc_matrix((raw[prefix + "_nominal_c_data"], raw[prefix + "_nominal_c_indices"],
                raw[prefix + "_nominal_c_indptr"]), shape=tuple(raw[prefix + "_nominal_c_shape"]))
            edges = triu(matrix, k=1).tocoo()
            assert np.all(edges.data < 0)
            a, b = active[edges.row], active[edges.col]
            edge_current = -edges.data * complex(coefficients[j]) * (voltage[a] - voltage[b])
            add_branches(balance, parts["gc"], a, b, edge_current)
        port = raw["global_to_active_indices"][raw["solve_port_reduced_nodes"]].reshape(-1)
        assert len(port) == 2 and np.all(port >= 0)
        balance[port] += [1., -1.]
        parts["port"][port] += 1.
        maximum_kcl = float(np.max(np.abs(balance)))
        assert maximum_kcl < 1e-9, maximum_kcl
        groups = defaultdict(list)
        unavailable = []
        for row in candidates:
            indices = surface_active[[surface_ids[name] for name in row["island_ids"]]]
            unique = np.unique(indices)
            if len(unique) != 1 or unique[0] < 0:
                unavailable.append({"component_id": row["component_id"], "layer": row["layer"],
                                    "net": row["net"], "active_indices": unique.tolist()})
                continue
            groups[int(unique[0])].append({"component_id": row["component_id"], "layer": row["layer"],
                "net": row["net"], "island_count": len(row["island_ids"]), "contact_status": row["contact_status"]})
        records = []
        for active, components in groups.items():
            records.append({"active_index": active, "source_components": components,
                "shared_active_component_count": len(components),
                "half_sum_absolute_external_current_a": .5 * sum(float(values[active]) for values in parts.values()),
                "absolute_current_sums_by_type_a": {key: float(values[active]) for key, values in parts.items()},
                "finite_incident_count": int(finite_count[active]), "kcl_residual_a": float(abs(balance[active]))})
        records.sort(key=lambda row: (-row["half_sum_absolute_external_current_a"], row["active_index"]))
    result = {"program": "SPD Decap PI Evaluator v0.23.1", "status": "COMPLETED_NATIVE_SHEET_CURRENT_CENSUS",
        "rail_id": RAIL, "frequency_hz": 1e6, "device_drive_a": 1.,
        "inputs": {str(path): expected for path, expected in pins.items()},
        "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_component_count": len(candidates), "active_group_count": len(records),
        "unavailable_components": unavailable, "maximum_all_node_kcl_residual_a": maximum_kcl,
        "groups": records, "elapsed_s": time.monotonic() - started,
        "limitations": ["Half the sum of external branch-current magnitudes is a current-throughput proxy, not impedance sensitivity, Joule loss or a mandatory source-to-device cut.",
            "Several source components sharing an active node are grouped; current is not arbitrarily split between them.",
            "Only target-rail and DGND components are listed; the original complete board field and all its finite, termination and GC currents enter KCL.",
            "No geometry decode, native recompile, solve, PowerSI fitting or accuracy claim."]}
    _write_json(RUN / "source-sheet-current-ranking.json", result)
    print(json.dumps({key: result[key] for key in ("status", "source_component_count", "active_group_count", "maximum_all_node_kcl_residual_a", "elapsed_s")}))
    print(json.dumps(records[:8]))


if __name__ == "__main__":
    balance, total = np.zeros(3, dtype=complex), np.zeros(3)
    add_branches(balance, total, [0, 1, 2], [1, 2, 2], [3+4j, 3+4j, 99j])
    assert np.array_equal(balance, [-3-4j, 0j, 3+4j]) and np.array_equal(total, [5., 10., 5.])
    main()
