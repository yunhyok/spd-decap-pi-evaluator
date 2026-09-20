"""Saved-only native binding selectors for the physical Trace464278 PWR joint."""
import json
from hashlib import sha256
from pathlib import Path
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUT = R / "astra-pwr-trace464278-native-binding-20260912"
PINS = {
    R / "astra-source-joint-charge-closure-20260912/charge-terminal-closure.npz": "00897aa2094d1488ed1cac3c0404b62279ffaf2ccd3269973e79d110dc01f533",
    R / "astra-power-joint-source-boundary-partition-20260912/source-boundary-partition.npz": "c9ec2371ea45695d6b32c4c1d9f3c7ee2eb8c2501b8e9eed446cd346a1ab70bd",
    R / "astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz": "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
    R / "astra-native-selected-first-via-map-02/selected-native-first-vias.npz": "aa8349094aae8a27c5f2f05cda41e69bb40171ff76911f3507b499673efe8500",
}


def sha(path): return sha256(path.read_bytes()).hexdigest()


def csr(payload, name, rows, cols, data, shape):
    order = np.lexsort((cols, rows)); rows, cols, data = rows[order], cols[order], data[order]
    assert not np.any((rows[1:] == rows[:-1]) & (cols[1:] == cols[:-1]))
    ptr = np.zeros(shape[0] + 1, dtype=np.int64); ptr[1:] = np.cumsum(np.bincount(rows, minlength=shape[0]))
    payload.update({name + "_shape": np.asarray(shape, dtype=np.int64), name + "_row_ptr": ptr,
                    name + "_col": cols.astype(np.int64), name + "_data": data})


def run():
    started = monotonic(); assert not OUT.exists(), OUT
    for path, expected in PINS.items(): assert sha(path) == expected, path
    closure, partition, hybrid, native_map = PINS
    with np.load(closure, allow_pickle=False) as z:
        names = z["terminal_names"].astype("U"); face_terminal = z["terminal_face_index"]
        terminal_faces = z["terminal_face_ids"]; translation = z["translation_xy_um"]
    with np.load(partition, allow_pickle=False) as z:
        assert np.array_equal(names, z["terminal_names"].astype("U"))
        assert np.array_equal(translation, z["translation_xy_um"])
    with np.load(native_map, allow_pickle=False) as z:
        pin = z["pin_id"].astype("U"); active_edge = z["active_edge_index"]
        top_row, lower_row = z["native_top_row"], z["native_lower_row"]
        incidence_rows, incidence_values = z["native_incidence_rows"], z["native_incidence_values"]
    with np.load(hybrid, allow_pickle=False) as z:
        native = z["final_finite_native_active_row"]
    selected_pins = np.asarray(["SITE0:3576", "SITE0:3574"])
    selected = np.asarray([np.flatnonzero(pin == p) for p in selected_pins], dtype=np.int64).ravel()
    assert len(selected) == 2 and np.array_equal(pin[selected], selected_pins)
    first_edge = active_edge[selected]
    final_hits = [np.flatnonzero(native == edge) for edge in first_edge]
    assert all(len(x) == 1 for x in final_hits), "native first-via rows must have one final row"
    final = np.asarray([int(x[0]) for x in final_hits], dtype=np.int64)
    assert np.all(top_row[selected] == 2699) and np.all(incidence_values[:, selected] == [[1, 1], [-1, -1]])
    declared = np.asarray(["SITE0:3576:TOP_DEVICE_PAD", "SITE0:3574:TOP_DEVICE_PAD",
                           "SITE0:3576:L02_R20_CONTINUATION", "SITE0:3574:L02_R20_CONTINUATION"])
    assert np.array_equal(names[:4], declared)
    outer = np.arange(4, len(names), dtype=np.int64)
    assert len(names) == 68 and len(outer) == 64 and np.all(np.bincount(face_terminal, minlength=68)[outer] == 1)
    left = np.flatnonzero(np.char.startswith(names, "Trace464279:CONTINUATION_FACE:"))
    right = np.flatnonzero(np.char.startswith(names, "Trace464277:CONTINUATION_FACE:"))
    assert len(left) + len(right) == 64 and len(left) > 0 and len(right) > 0
    payload = dict(terminal_names=names, terminal_face_ids=terminal_faces, terminal_face_index=face_terminal,
                   translation_xy_um=translation, declared_terminal_index=np.arange(4, dtype=np.int64),
                   outer_trace_terminal_index=outer, outer_left_terminal_index=left, outer_right_terminal_index=right,
                   physical_pin_id=selected_pins, first_via_active_edge=first_edge,
                   first_via_final_finite_row=final, first_via_native_top_row=top_row[selected],
                   first_via_native_lower_row=lower_row[selected],
                   first_via_native_incidence_rows=incidence_rows[:, selected],
                   first_via_native_incidence_values=incidence_values[:, selected],
                   central_trace_id=np.asarray(["Trace464278"]),
                   outer_trace_id=np.asarray(["Trace464279", "Trace464277"]),
                   outer_trace_continuation_status=np.asarray(["STOP_MISSING_OUTER_TRACE_R_D_H_OPERATOR"]),
                   stop_reason=np.asarray(["Cached artifacts retain 64 exact outer cut faces but no matching outer Trace464279/Trace464277 volume R/D/H continuation operator."]))
    csr(payload, "retire_first_via_final_row_selector", np.arange(2), final, np.ones(2), (2, len(native)))
    csr(payload, "retire_first_via_native_edge_selector", np.arange(2), first_edge, np.ones(2), (2, int(native.max()) + 1))
    # Trace464278 is replaced by the closure geometry; no scalar native trace-edge
    # identity is exposed by the cached finite map, so there is no fake selector.
    OUT.mkdir(parents=True); (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(OUT / "pwr-native-binding.npz", **payload)
    result = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="STOP_MISSING_OUTER_TRACE_CONTINUATION_OPERATOR",
                  elapsed_s=monotonic() - started, driver_sha256=sha(Path(__file__)), artifact_sha256=sha(OUT / "pwr-native-binding.npz"),
                  pins={str(k): v for k, v in PINS.items()},
                  counts=dict(source_terminals=68, declared_physical_terminals=4, independent_outer_cut_terminals=64,
                              selected_first_vias=2, central_native_trace_scalar_rows=0),
                  bindings=dict(central_trace="Trace464278 SITE0:3576->SITE0:3574", outer_left="Trace464279 SITE0:3588->SITE0:3576",
                                outer_right="Trace464277 SITE0:3574->SITE0:3587", first_via_final_rows=final.tolist(),
                                first_via_active_edges=first_edge.tolist()),
                  gates=dict(actual_pins=True, exact_first_via_native_rows=True, first_vias_retired_by_selector=True,
                             outer_cut_voltages_remain_independent=True, arbitrary_ground_not_introduced=True,
                             outer_trace_operator_missing=True),
                  scope="Executable saved-only selectors for exact physical PWR source ownership. The 64 outer cut terminals stay independent and this artifact deliberately stops before any outer-trace continuation, port equation, Green operator, or solve.")
    (OUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__": run()
