"""Bind the saved G-window source faces to the two exact native L02 starts.

This is a sparse ownership change only.  It deliberately does not assemble a
board operator or alter any L/P data.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUT = R / "astra-g-window-native-source-binding-20260912-02"
PINS = {
    R / "astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz": "72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf",
    R / "astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz": "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
    R / "astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz": "61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020",
    R / "astra-device-first-via-sources-01/selected-device-first-via-sources.json": "35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303",
    R / "astra-device-l02-via-contacts-03/lower-via-contact-ledger.json": "7d2477d09ab5e50cbea2397eeb19385389c7722fdd05d682987c08736a02affa",
    R / "astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz": "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f",
}
OLD = (("Via1430784", 243016, 243018, 9987), ("Via1424286", 1135337, 1135347, 18068))
NEXT = (("Via1430783", 1305220, 9987), ("Via1424285", 471316, 18068))


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def put_csr(payload: dict, name: str, rows: np.ndarray, cols: np.ndarray,
            data: np.ndarray, shape: tuple[int, int]) -> None:
    """Store a sorted, duplicate-free CSR selector without a SciPy runtime."""
    order = np.lexsort((cols, rows)); rows, cols, data = rows[order], cols[order], data[order]
    assert len(rows) == 0 or not np.any((rows[1:] == rows[:-1]) & (cols[1:] == cols[:-1]))
    ptr = np.zeros(shape[0] + 1, dtype=np.int64); ptr[1:] = np.cumsum(np.bincount(rows, minlength=shape[0]))
    payload.update({name + "_shape": np.asarray(shape, dtype=np.int64), name + "_row_ptr": ptr,
                    name + "_col": cols.astype(np.int64), name + "_data": data})


def run() -> dict:
    started = monotonic()
    assert not OUT.exists(), OUT
    for path, expected in PINS.items():
        assert digest(path) == expected, path
    coupling, hybrid, circuit, first, lower, sheet_space = list(PINS)
    with np.load(coupling, allow_pickle=False) as z:
        face_ids = z["source_face_ids"]
        pins = z["source_pin_ids"].astype("U")
        kinds = z["source_kinds"].astype("U")
        vertices = z["source_face_vertices_um"]
        retained_sheet_charge_rows = z["retained_sheet_charge_row_ids"]
    with np.load(hybrid, allow_pickle=False) as z:
        finite_first = z["finite_first_active_index"]
        finite_second = z["finite_second_active_index"]
        finite_admittance = z["finite_admittance_s"]
        native = z["final_finite_native_active_row"]
        contact_global = z["contact_global_active_index"]
        original_potential_count = int(z["potential_count"][0])
        contact_gc_first = z["contact_gc_first_active_index"]
        contact_gc_second = z["contact_gc_second_active_index"]
    with np.load(sheet_space, allow_pickle=False) as z:
        sheet_free_count = len(z["free_triangle_indices"])
    with np.load(circuit, allow_pickle=False) as z:
        active = z["active_finite_index"]
        cfirst, csecond = z["first_active_index"], z["second_active_index"]
        ccontact = z["native_contact_ordinal"]
        cside = z["target_side"]
    records = json.loads(first.read_text(encoding="utf-8"))["records"]
    via = {r["contact"]["incident_via_id"]: r for r in records}
    entities = {r["via_id"]: r for r in json.loads(lower.read_text(encoding="utf-8"))["entities"]}

    top = kinds == "TOP_ELECTRODE"
    lower_mask = kinds == "LOWER_R20_NEXT_VIA"
    assert len(face_ids) == 1176 and int(top.sum()) == 984 and int(lower_mask.sum()) == 192
    source_pins = ["SITE0:24245", "SITE0:24264"]
    assert set(pins) == set(source_pins)
    groups = [top, lower_mask & (pins == source_pins[0]), lower_mask & (pins == source_pins[1])]
    assert [int(g.sum()) for g in groups] == [984, 96, 96]

    old_final = np.asarray([row[1] for row in OLD], dtype=np.int64)
    old_native = np.asarray([row[2] for row in OLD], dtype=np.int64)
    assert np.array_equal(native[old_final], old_native)
    assert np.array_equal(finite_first[old_final], [2656, 2656])
    contact = np.asarray([row[3] for row in OLD], dtype=np.int64)
    assert np.array_equal(finite_second[old_final], contact_global[contact])
    assert np.array_equal(contact_global[contact], [1493282, 1501363])

    next_final = []
    for _, active_row, contact_ordinal in NEXT:
        hit = np.flatnonzero(native == active_row)
        assert len(hit) == 1
        row = int(hit[0]); next_final.append(row)
        ci = np.flatnonzero(active == active_row)
        assert len(ci) == 1 and int(ccontact[ci[0]]) == contact_ordinal and int(cside[ci[0]]) == 0
        assert int(cfirst[ci[0]]) == 349710 and int(csecond[ci[0]]) != 349710
        assert finite_first[row] == contact_global[contact_ordinal] and finite_second[row] == csecond[ci[0]]
    next_final = np.asarray(next_final, dtype=np.int64)

    # Allocate in the hybrid active-node namespace.  The legacy common node
    # 349710 is not a valid identity for either physical lower contact.
    old_contact_active = contact_global[contact]
    assert original_potential_count > max(finite_first.max(), finite_second.max(), contact_global.max())
    new_node_base = original_potential_count
    terminal_active = np.asarray([2656, new_node_base, new_node_base + 1], dtype=np.int64)
    active_node_count_after = new_node_base + 2
    sheet_contact_rows = sheet_free_count + contact
    assert not np.any(np.isin(sheet_contact_rows, retained_sheet_charge_rows))
    restriction_rows = np.concatenate([np.full(g.sum(), i, dtype=np.int64) for i, g in enumerate(groups)])
    restriction_cols = np.concatenate([np.flatnonzero(g) for g in groups])
    # Incidence convention is + at finite_first, - at finite_second.  The old
    # first-via columns are deleted separately.  Rewire only each selected
    # next-via occurrence: remove +1 from its old sheet-contact active node and
    # add +1 at its new, distinct lower terminal.
    delta_rows = np.asarray([old_contact_active[0], terminal_active[1],
                             old_contact_active[1], terminal_active[2]], dtype=np.int64)
    delta_cols = np.asarray([next_final[0], next_final[0],
                             next_final[1], next_final[1]], dtype=np.int64)
    delta_data = np.asarray([-1., 1., -1., 1.])
    source_node_rows = np.concatenate([np.full(g.sum(), terminal_active[i], dtype=np.int64)
                                       for i, g in enumerate(groups)])
    kept_mask = np.ones(len(finite_first), dtype=bool); kept_mask[old_final] = False
    kept_final = np.flatnonzero(kept_mask)
    patched_first = finite_first[kept_mask].copy()
    patched_second = finite_second[kept_mask].copy()
    patched_admittance = finite_admittance[kept_mask].copy()
    next_kept_position = np.searchsorted(kept_final, next_final)
    assert np.array_equal(kept_final[next_kept_position], next_final)
    patched_first[next_kept_position] = terminal_active[1:]
    untouched = np.ones(len(kept_final), dtype=bool); untouched[next_kept_position] = False
    assert np.array_equal(patched_first[untouched], finite_first[kept_mask][untouched])
    assert np.array_equal(patched_second, finite_second[kept_mask])
    assert np.array_equal(patched_admittance, finite_admittance[kept_mask])
    assert not np.isin(old_contact_active, np.r_[patched_first, patched_second]).any()
    old_contact_gc_rows = [np.flatnonzero((contact_gc_first == node) | (contact_gc_second == node))
                           for node in old_contact_active]
    assert np.bincount(restriction_rows, minlength=3).tolist() == [984, 96, 96]
    assert via["Via1430784"]["contact"]["pin_id"] == source_pins[0]
    assert via["Via1424286"]["contact"]["pin_id"] == source_pins[1]
    assert entities["Via1430783"]["start_node_id"] == entities["Via1430784"]["end_node_id"]
    assert entities["Via1424285"]["start_node_id"] == entities["Via1424286"]["end_node_id"]

    payload = dict(source_face_ids=face_ids, source_pin_ids=pins, source_kinds=kinds,
                   source_face_vertices_um=vertices, source_terminal_active_coordinate=terminal_active,
                   terminal_active_coordinate=terminal_active,
                   terminal_kind=np.asarray(["DECLARED_TOP_2656", "NEW_LOWER_VIA1430783", "NEW_LOWER_VIA1424285"]),
                   terminal_source_face_count=np.asarray([984, 96, 96], dtype=np.int64),
                   old_final_finite_row=old_final, old_native_active_finite_row=old_native,
                   old_contact_ordinal=contact, old_sheet_contact_distributional_row=sheet_contact_rows,
                   old_contact_active_index=old_contact_active, next_final_finite_row=next_final,
                   kept_final_finite_row=kept_final, patched_first_active_index=patched_first,
                   patched_second_active_index=patched_second, patched_finite_admittance_s=patched_admittance,
                   next_native_active_finite_row=np.asarray([1305220, 471316], dtype=np.int64),
                   next_l02_start_active_index=contact_global[contact],
                   old_contact_gc_row_ids=np.concatenate(old_contact_gc_rows),
                   old_contact_gc_row_offsets=np.asarray([0, len(old_contact_gc_rows[0]),
                                                          len(old_contact_gc_rows[0])+len(old_contact_gc_rows[1])], dtype=np.int64),
                   old_contact_gc_status=np.asarray(["UNRESOLVED_REQUIRES_SEPARATE_OWNERSHIP_REPLACEMENT"]),
                   legacy_common_active_index=np.asarray([349710], dtype=np.int64),
                   original_potential_count=np.asarray([original_potential_count], dtype=np.int64),
                   new_potential_count=np.asarray([active_node_count_after], dtype=np.int64),
                   incidence_sign_convention=np.asarray(["+finite_first,-finite_second"]),
                   source_face_node_sign_convention=np.asarray(["node KCL receives -local outward H face current"]))
    put_csr(payload, "h_face_to_terminal_restriction", restriction_rows, restriction_cols,
            np.ones(len(face_ids)), (3, len(face_ids)))
    put_csr(payload, "old_native_first_row_remove_selector", np.arange(2), old_final,
            np.ones(2), (2, len(finite_first)))
    put_csr(payload, "source_face_to_active_node_incidence", source_node_rows,
            np.arange(len(face_ids), dtype=np.int64), -np.ones(len(face_ids)),
            (active_node_count_after, len(face_ids)))
    put_csr(payload, "exterior_incidence_delta", delta_rows, delta_cols, delta_data,
            (active_node_count_after, len(finite_first)))
    OUT.mkdir(parents=True)
    (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(OUT / "native-source-binding.npz", **payload)
    result = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="PASS_G_WINDOW_NATIVE_SOURCE_BINDING_NO_SOLVE",
                  elapsed_s=monotonic() - started, driver_sha256=digest(Path(__file__)),
                  artifact_sha256=digest(OUT / "native-source-binding.npz"), pins={str(k): v for k, v in PINS.items()},
                  counts=dict(source_h_faces=1176, top_faces=984, lower_faces_per_terminal=96,
                              terminals=3, old_first_via_rows_removed=2, old_l02_contact_reservoirs_retired=2,
                              next_via_l02_start_occurrences=2, restriction_nonzeros=len(face_ids),
                              source_face_node_incidence_nonzeros=len(face_ids),
                              next_via_endpoint_rewire_nonzeros=4),
                  gates=dict(coupling_hash=True, exact_old_final_native_rows=True, exact_old_contact_reservoirs=True,
                             h_faces_first_then_terminal_restriction=True, distinct_lower_terminals=True,
                             active_node_namespace_collision_free=bool(new_node_base > max(finite_first.max(), finite_second.max(), contact_global.max())),
                             next_via_old_endpoint_removed_and_new_endpoint_inserted=True,
                             patched_finite_endpoints_materialized=True,
                             patched_finite_admittance_preserved=True,
                             old_contact_endpoints_absent_from_kept_finite=True,
                             old_sheet_contact_rows_absent_from_retained_charge_space=True,
                             legacy_349710_occurrences_preserved_except_selected_exact_endpoints=True,
                             no_board_solve_no_green_no_lp_replacement=True),
                  source_metadata=dict(pins=source_pins, old_first_vias=[x[0] for x in OLD], next_vias=[x[0] for x in NEXT],
                                       old_native_rows=old_native.tolist(), next_native_rows=[1305220, 471316]),
                  scope="Saved-only sparse binding. H source faces remain facewise; the restriction aggregates only the declared TOP electrode and keeps the two lower contacts independent. The old two native first-via columns and L02 sheet contact rows are selected for removal. Each exact next-via occurrence is rewired from its old hybrid contact-active node to a collision-free new lower terminal. No solve, Green, L/P replacement, SPD/Touchstone read, or accuracy claim.")
    (OUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    run()
