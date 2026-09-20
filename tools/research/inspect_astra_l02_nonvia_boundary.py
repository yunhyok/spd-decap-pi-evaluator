"""Bind the saved L02 non-via boundary before any distributed-sheet replacement."""
from collections import defaultdict
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
TARGET = 349710
PINS = {
    "raw": (R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz", "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "derived": (R / "astra-native-loaded-vtrip-field-02/derived-field-observation.npz", "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0"),
    "inventory": (R / "astra-l02-source-inventory-02/result.json", "0af954600a5070f8f2fa27a81ba941889b46ac9f26ac2b76d8669f4ce3af93f2"),
    "helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
}


def target_rows(matrix, active, target):
    """Keep source row/column identity even when ideal aliases cancel entries."""
    local = np.flatnonzero(active == target)
    selected = matrix.tocsr()[local].tocoo()
    return local[selected.row], selected.col, selected.data


def main(output):
    started = time.monotonic()
    # Two source aliases collapse to one target: their internal edge cancels.
    c = sparse.csc_matrix([[5., -2., -3.], [-2., 6., -4.], [-3., -4., 7.]])
    active = np.array([0, 0, 1])
    rows, cols, data = target_rows(c, active, 0)
    assert np.array_equal(np.bincount(active[cols], weights=data), [7., -7.])
    assert len(data) == 6 and set(rows) == {0, 1}
    for name, (path, expected) in PINS.items():
        assert recon._sha256_file(path) == expected, name
    inventory = json.loads(PINS["inventory"][0].read_bytes())
    assert inventory["active_index"] == TARGET
    expected_islands = set(inventory["component"]["island_ids"])
    parts, gc_row = [], defaultdict(complex)
    with np.load(PINS["raw"][0], allow_pickle=False) as raw, np.load(PINS["derived"][0], allow_pickle=False) as derived:
        g2a = raw["global_to_active_indices"]
        names = recon._decode_text_vector(raw["surface_node_ids"], "surface")
        lookup = dict(zip(names, g2a[raw["surface_to_reduced_indices"]]))
        assert len(lookup) == len(names)
        # The saved surface table also carries this compiled finite-via quotient alias.
        target_aliases = {name for name, index in lookup.items() if index == TARGET}
        extra_aliases = target_aliases - expected_islands
        assert expected_islands <= target_aliases and extra_aliases == {"spd-finite-via-vertex:75e70fea61021703aa9311b5"}
        saved_voltage = raw["active_voltage"]
        assert saved_voltage.shape == (756889, 1) and np.isfinite(saved_voltage).all()
        v = saved_voltage[:, 0]
        frequency = float(raw["frequency_hz"][0])
        assert frequency == 1e6
        scales = raw["partial_actual_1mhz_dispersion_admittance_scale_s"]
        assert len(scales) == 36 and np.isfinite(scales).all()
        for ordinal in range(36):
            assert time.monotonic() - started < 60
            prefix = f"partial_{ordinal:02d}"
            local_names = recon._decode_text_vector(raw[prefix + "_net_names"], prefix)
            active = np.array([lookup[name] for name in local_names], dtype=np.int64)
            if TARGET not in active:
                continue
            c = recon._csc_from_snapshot(raw, prefix)
            rows, cols, data = target_rows(c, active, TARGET)
            tr, tc, td = target_rows(c.T, active, TARGET)
            assert np.array_equal(rows, tr) and np.array_equal(cols, tc) and np.allclose(data, td, rtol=1e-12, atol=1e-25)
            entries = []
            for i, j, value in zip(rows, cols, data):
                other = int(active[j])
                entries.append({"source_row": int(i), "source_column": int(j), "source_row_id": local_names[i], "source_column_id": local_names[j], "column_active_index": other, "nominal_c_f": float(value), "retained_native": other >= 0})
                if other >= 0:
                    gc_row[other] += scales[ordinal] * value
            parts.append({"ordinal": ordinal, "scale_s_per_nominal_f": [float(scales[ordinal].real), float(scales[ordinal].imag)], "source_target_row_count": int(np.count_nonzero(active == TARGET)), "directed_target_row_entries": entries})
            assert time.monotonic() - started < 60
        first, second = raw["finite_first_active_indices"], raw["finite_second_active_indices"]
        incident = (first == TARGET) ^ (second == TARGET)
        internal = (first == TARGET) & (second == TARGET)
        assert int(incident.sum()) == 76139 and not internal.any()
        current = raw["finite_count"][incident] * (v[first[incident]] - v[second[incident]]) / (raw["finite_resistance_ohm_per_via"][incident] + 2j*np.pi*frequency*raw["finite_inductance_h_per_via"][incident])
        assert current.shape == (76139,) and np.isfinite(current).all()
        finite_current = np.sum(np.where(first[incident] == TARGET, current, -current))
        tp, tn = derived["termination_positive_active_indices"], derived["termination_negative_active_indices"]
        term_indices = np.flatnonzero((tp == TARGET) | (tn == TARGET))
        term_current = derived["termination_admittance_s"][term_indices] * (v[tp[term_indices]] - v[tn[term_indices]])
        term_current = np.sum(term_current * ((tp[term_indices] == TARGET).astype(int) - (tn[term_indices] == TARGET).astype(int)))
        batch = raw["batch_port_indices"]
        assert batch.shape == (1,)
        device = g2a[raw["solve_port_reduced_nodes"][int(batch[0])]]
        injection = int(device[0] == TARGET) - int(device[1] == TARGET)
        gc_current = sum(value * v[index] for index, value in gc_row.items())
        closure = finite_current + gc_current + term_current - injection
        assert abs(closure) < 1e-7
        pair = lambda z: [float(np.real(z)), float(np.imag(z))]
        result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_SAVED_NONVIA_BOUNDARY", "active_index": TARGET,
                  "inputs": {name: {"path": str(path), "sha256": expected} for name, (path, expected) in PINS.items()}, "script_sha256": recon._sha256_file(Path(__file__)),
                  "source_island_count": len(expected_islands), "additional_saved_surface_aliases": sorted(extra_aliases), "native_incident_finite_links": int(incident.sum()), "native_internal_finite_links": int(internal.sum()),
                  "frequency_hz": frequency, "device_active_indices": device.tolist(), "target_is_gauge": int(raw["gauge_active_index"][0]) == TARGET,
                  "direct_termination_indices": term_indices.tolist(), "source_partials": parts,
                  "collapsed_gc_row": [{"column_active_index": index, "admittance_s": pair(value)} for index, value in sorted(gc_row.items())],
                  "native_saved_current_a": {"finite_outward": pair(finite_current), "gc_outward": pair(gc_current), "termination_outward": pair(term_current), "device_injection": injection, "closure": pair(closure)},
                  "elapsed_s": time.monotonic() - started,
                  "scope": "Saved native 1MHz boundary only, with all directed source C entries retained before ideal-alias cancellation. These entries are not yet geometric G/C ownership or current-basis weights. No source scan, geometry, assembly/LU, changed operator, or unique-return/accuracy claim."}
    recon._atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "partial_count": len(parts), "source_entries": sum(len(p["directed_target_row_entries"]) for p in parts), "external_active_neighbors": len([i for i in gc_row if i != TARGET]), "termination_count": len(term_indices), "closure_abs_a": abs(closure), "elapsed_s": result["elapsed_s"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    main(output)
