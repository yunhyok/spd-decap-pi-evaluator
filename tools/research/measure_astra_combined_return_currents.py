"""Measure actual first-via and other return currents after a combined field passes."""
import argparse
import json
from pathlib import Path

import numpy as np

from compare_astra_combined_board_1mhz import require_accepted, sha
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "acceptance_helper": (ROOT / "tools/research/compare_astra_combined_board_1mhz.py",
                          "08f6f671d2419ce57816ba278bc71d40ea60bd3b4e875a63ff9cd844a5286c25"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py",
                      "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "native_pin_map": (ROOT / "outputs/research/astra-native-selected-first-via-map-02/selected-native-first-vias.npz",
                       "aa8349094aae8a27c5f2f05cda41e69bb40171ff76911f3507b499673efe8500"),
    "combined_map": (ROOT / "outputs/research/astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz",
                     "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
}


def pair(value):
    return [float(value.real), float(value.imag)]


def unique_join(rows, selected):
    order = np.argsort(rows)
    low, high = (np.searchsorted(rows[order], selected, side) for side in ("left", "right"))
    assert np.all(high-low == 1), "A selected physical first via is missing or split"
    indices = order[low]
    assert np.array_equal(rows[indices], selected)
    return indices


def run(args):
    budget = recon._Budget.create(45, 2)
    assert not args.output.exists()
    assert sha(args.result) == args.result_sha256
    actual = json.loads(args.result.read_bytes())
    require_accepted(actual)
    field_path = Path(actual["field"]["path"])
    assert field_path.resolve() == (args.result.parent / "field.npz").resolve()
    assert sha(field_path) == actual["field"]["sha256"]
    for path, digest in PINS.values():
        assert sha(path) == digest
    with np.load(field_path, allow_pickle=False) as z:
        v = z["active_voltage_v"]
        assert v.shape == (2340069,) and np.all(np.isfinite(v))
        assert np.array_equal(z["source_positive_negative_gauge_active_indices"], (2699, 2656, 0))
        assert np.array_equal(z["source_current_amplitude_a"], (1.0,))
    with np.load(PINS["native_pin_map"][0], allow_pickle=False) as z:
        native, orientation, top = (z[k] for k in ("active_edge_index", "native_orientation_from_top", "native_top_row"))
        role, pin_id, old_current = (z[k] for k in ("role", "pin_id", "native_top_to_lower_current_a"))
        series = z["resistance_ohm"] + 2j*np.pi*1e6*z["inductance_h"]
    with np.load(PINS["combined_map"][0], allow_pickle=False) as z:
        f, s, y, rows, legs = (z[k] for k in ("final_finite_first_active_index",
            "final_finite_second_active_index", "final_finite_admittance_s",
            "final_finite_native_active_row", "final_finite_split_leg"))
    ix = unique_join(rows, native)
    assert len(ix) == len(np.unique(pin_id)) == 1956 and np.all(legs[ix] == -1)
    assert np.array_equal(np.where(orientation == 1, f[ix], s[ix]), top)
    assert np.all(np.abs(1/y[ix]-series) <= np.abs(series)*1e-13)
    current = y*(v[f]-v[s])
    selected = orientation*current[ix]
    lower = np.where(orientation == 1, s[ix], f[ix])
    metrics, arrays = {}, {}
    for name, port, drive in (("power", 2699, 1), ("ground", 2656, -1)):
        mask = role == name
        assert mask.sum() == 978 and np.all(top[mask] == port)
        incident = np.flatnonzero((f == port) ^ (s == port))
        outside = incident[~np.isin(incident, ix)]
        groups = {}
        for label, indices in (("all", incident), ("outside_selected", outside)):
            outward = np.where(f[indices] == port, 1, -1)*current[indices]
            groups[label] = {"edge_count": len(indices), "outward_current_sum_a": pair(outward.sum()),
                             "joule_w": float(np.sum((1/y[indices]).real*abs(current[indices])**2))}
            if label == "all":
                assert abs(outward.sum()-drive) < 1e-7
            else:
                arrays[name+"_outside_final_edge_index"] = indices
                arrays[name+"_outside_top_outward_current_a"] = outward
                assert abs(outward.sum()+selected[mask].sum()-drive) < 1e-7
        metrics[name] = {"selected_pin_count": int(mask.sum()),
            "unique_new_lower_rows": len(np.unique(lower[mask])),
            "selected_outward_current_sum_a": pair(selected[mask].sum()),
            "old_native_selected_outward_current_sum_a": pair(old_current[mask].sum()),
            "selected_rms_current_a": float(np.sqrt(np.mean(abs(selected[mask])**2))),
            "selected_max_abs_current_a": float(abs(selected[mask]).max()),
            "selected_first_via_joule_w": float(np.sum(series[mask].real*abs(selected[mask])**2)),
            "port_incident_finite_edges": groups}
    budget.check("current mapping and port replay")
    args.output.mkdir(parents=True)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    target = args.output / "first-via-currents.npz"
    np.savez_compressed(target, pin_id=pin_id, role=role, native_active_edge_index=native,
        final_active_edge_index=ix, top_active_row=top, lower_active_row=lower,
        top_to_lower_current_a=selected, old_native_top_to_lower_current_a=old_current, **arrays)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "COMPLETED_CONDITIONAL_COMBINED_FIRST_VIA_CURRENT_DIAGNOSTIC",
        "frequency_hz": 1e6, "driver_sha256": sha(Path(__file__)),
        "inputs": {"accepted_result": {"path": str(args.result.resolve()), "sha256": args.result_sha256},
                   **{key: {"path": str(path), "sha256": digest} for key, (path, digest) in PINS.items()}},
        "field": actual["field"], "roles": metrics,
        "artifact": {"path": str(target.resolve()), "sha256": sha(target)}, "budget": budget.receipt(),
        "scope": "Actual currents of the accepted conditional combined R/G/C operator, preserving all 1956 selected pin identities and every other port-incident finite edge. Old native currents are a different baseline, not the earlier magnetic candidate. No new solve, magnetic coupling, calibration, mesh convergence or impedance-error bound."}
    (args.output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    assert np.array_equal(unique_join(np.array([7, 3, 8]), np.array([8, 7])), [2, 0])
    try:
        unique_join(np.array([3, 3]), np.array([3]))
    except AssertionError:
        pass
    else:
        raise AssertionError("Duplicate physical edge must be rejected")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--result-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
