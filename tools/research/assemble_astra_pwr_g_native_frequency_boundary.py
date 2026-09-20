"""SPD Decap PI Evaluator v0.23.1: fixed-1MHz native boundary for PWR+G volumes.

Retire the 107 PWR and two G first-via scalar branches, preserve every other
native finite branch, and expose the sparse outward-current action from both
explicit source volumes to the existing circuit coordinates.  No field L/P or
system solve is performed.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy import sparse


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    R / "astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz":
        "a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b",
    R / "astra-g-window-native-source-binding-20260912-02/native-source-binding.npz":
        "e8278ede386c64bbb8a87382fd25d8fbdf0648bcda3f3b672fed6e5601a3d18a",
    R / "astra-g-native-frequency-boundary-20260912/native-frequency-boundary.npz":
        "2337defbbbd32ad5db5f5987858b7dd4db6f5004225bb5861db874121410955b",
    R / "astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz":
        "72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf",
    R / "astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz":
        "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
    R / "astra-native-selected-first-via-map-02/selected-native-first-vias.npz":
        "aa8349094aae8a27c5f2f05cda41e69bb40171ff76911f3507b499673efe8500",
    R / "astra-native-selected-first-via-map-02/result.json":
        "509f52f940ea43c7869256f4b36eb12adeb7c70dea9ea41e9cb5c56894b17308",
}


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def unpack(z: np.lib.npyio.NpzFile, name: str) -> sparse.csr_matrix:
    return sparse.csr_matrix(
        (z[f"{name}_data"], z[f"{name}_col"], z[f"{name}_row_ptr"]),
        shape=tuple(z[f"{name}_shape"]),
    )


def pack(payload: dict[str, np.ndarray], name: str, matrix: sparse.spmatrix) -> None:
    matrix = matrix.tocsr()
    payload[f"{name}_shape"] = np.asarray(matrix.shape, dtype=np.int64)
    payload[f"{name}_row_ptr"] = matrix.indptr.astype(np.int64, copy=False)
    payload[f"{name}_col"] = matrix.indices.astype(np.int64, copy=False)
    payload[f"{name}_data"] = matrix.data


def run(output: Path) -> None:
    started = monotonic()
    assert not output.exists()
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    pwr_path, g_binding_path, g_native_path, g_coupling_path, hybrid_path, first_via_path, native_result_path = PINS

    native_result = json.loads(native_result_path.read_bytes())
    assert native_result["frequency_hz"] == 1e6
    assert native_result["roles"]["power"]["pin_count"] == 978
    assert native_result["roles"]["power"]["unique_native_top_rows"] == 1
    with np.load(first_via_path, allow_pickle=False) as z:
        power = z["role"].astype("U") == "power"
        all_power_top = z["native_top_row"][power]
    assert len(all_power_top) == 978 and np.array_equal(np.unique(all_power_top), [2699])

    with np.load(pwr_path, allow_pickle=False) as z:
        pwr_retire = z["retire_first_via_final_finite_row"]
        pwr_edge = z["retire_first_via_active_edge"]
        pwr_terminal_native = z["terminal_native_active_index"]
        pwr_terminal_names = z["terminal_names"]
        pwr_h = unpack(z, "terminal_outward")
        pwr_current_count = int(z["face_vertices"].shape[0])
        pwr_selected_terminal = int(z["selected_port_source_terminal_index"][0])
        assert np.array_equal(z["post_pin_id"][pwr_selected_terminal], "SITE0:3576")
    assert pwr_h.shape == (214, pwr_current_count)
    assert np.array_equal(np.unique(pwr_terminal_native[:107]), [2699])
    assert len(np.unique(pwr_terminal_native[107:])) == 107

    with np.load(g_binding_path, allow_pickle=False) as z:
        g_keep = z["kept_final_finite_row"]
        g_first = z["patched_first_active_index"]
        g_second = z["patched_second_active_index"]
        g_removed = z["old_final_finite_row"]
        potential_count = int(z["new_potential_count"][0])
        g_terminals = z["source_terminal_active_coordinate"]
    with np.load(g_native_path, allow_pickle=False) as z:
        g_h = unpack(z, "source_outward_terminal")
        assert np.array_equal(z["retired_finite_row_ids"], g_removed)
    with np.load(g_coupling_path, allow_pickle=False) as z:
        g_current_count = int(z["source_outward_shape"][1])
    assert g_h.shape == (3, g_current_count)
    assert np.array_equal(g_terminals, [2656, 3996022, 3996023])

    with np.load(hybrid_path, allow_pickle=False) as z:
        old_y = z["finite_admittance_s"]
        old_native = z["final_finite_native_active_row"]
        old_first = z["finite_first_active_index"]
        old_second = z["finite_second_active_index"]
        original_potential_count = int(z["potential_count"][0])
        gc_first = z["contact_gc_first_active_index"]
        gc_second = z["contact_gc_second_active_index"]
        gc_y = z["contact_gc_admittance_s"]
    assert original_potential_count == 3996022 and potential_count == 3996024
    assert len(np.unique(np.r_[g_removed, pwr_retire])) == 109
    assert np.array_equal(old_native[pwr_retire], pwr_edge)
    assert np.intersect1d(g_removed, pwr_retire).size == 0

    keep_mask = ~np.isin(g_keep, pwr_retire)
    retained = g_keep[keep_mask]
    first = g_first[keep_mask]
    second = g_second[keep_mask]
    admittance = old_y[retained]
    retired = np.sort(np.r_[g_removed, pwr_retire])
    assert np.array_equal(retained, np.setdiff1d(np.arange(len(old_y)), retired))
    assert len(retained) == len(old_y) - 109
    assert np.count_nonzero(first != old_first[retained]) == 2
    assert np.array_equal(second, old_second[retained])
    assert np.all((first >= 0) & (first < potential_count) & (second >= 0) & (second < potential_count))
    assert np.isfinite(admittance).all() and np.all(admittance.real > 0)

    g_restriction = sparse.coo_matrix(
        (np.ones(3), (g_terminals, np.arange(3))), shape=(potential_count, 3)
    ).tocsr()
    pwr_restriction = sparse.coo_matrix(
        (np.ones(214), (pwr_terminal_native, np.arange(214))), shape=(potential_count, 214)
    ).tocsr()
    h_active = sparse.hstack((g_restriction @ g_h, pwr_restriction @ pwr_h), format="csr")
    assert h_active.shape == (potential_count, g_current_count + pwr_current_count)
    assert h_active.nnz == g_h.nnz + pwr_h.nnz

    rng = np.random.default_rng(20260912)
    touched_edges = np.unique(np.r_[
        np.flatnonzero(np.isin(first, np.unique(np.r_[g_terminals, pwr_terminal_native]))),
        np.flatnonzero(np.isin(second, np.unique(np.r_[g_terminals, pwr_terminal_native]))),
    ])
    touched_nodes = np.unique(np.r_[first[touched_edges], second[touched_edges]])
    v = np.zeros(potential_count, dtype=np.complex128)
    u = np.zeros_like(v)
    v[touched_nodes] = rng.normal(size=len(touched_nodes)) + 1j * rng.normal(size=len(touched_nodes))
    u[touched_nodes] = rng.normal(size=len(touched_nodes)) + 1j * rng.normal(size=len(touched_nodes))

    def action(x: np.ndarray) -> np.ndarray:
        drop = x[first] - x[second]
        branch = admittance * drop
        result = np.zeros(potential_count, dtype=np.complex128)
        np.add.at(result, first, branch)
        np.add.at(result, second, -branch)
        return result

    yv = action(v)
    yu = action(u)
    reciprocity = float(abs(v @ yu - u @ yv) / max(abs(v @ yu), abs(u @ yv), 1e-30))
    drop = v[first] - v[second]
    edge_work = np.vdot(drop, admittance * drop)
    nodal_work = np.vdot(v, yv)
    work_error = float(abs(edge_work - nodal_work) / max(abs(edge_work), 1e-30))
    balance = float(abs(yv.sum()) / max(np.linalg.norm(yv, 1), 1e-30))

    i = rng.normal(size=h_active.shape[1]) + 1j * rng.normal(size=h_active.shape[1])
    left = v @ (h_active @ i)
    right = (h_active.T @ v) @ i
    h_work = float(abs(left - right) / max(abs(left), abs(right), 1e-30))
    assert reciprocity < 1e-10 and work_error < 1e-10 and balance < 1e-12 and h_work < 1e-12

    pwr_endpoint_nodes = np.unique(pwr_terminal_native)
    gc_occurrences = np.flatnonzero(np.isin(gc_first, pwr_endpoint_nodes) | np.isin(gc_second, pwr_endpoint_nodes))
    payload: dict[str, np.ndarray] = dict(
        frequency_hz=np.asarray([1e6]),
        native_potential_count=np.asarray([potential_count]),
        retained_finite_row_ids=retained,
        retained_finite_first_active_index=first,
        retained_finite_second_active_index=second,
        retained_finite_admittance_s=admittance,
        retired_finite_row_ids=retired,
        retired_g_first_via_final_rows=g_removed,
        retired_pwr_first_via_final_rows=pwr_retire,
        retired_pwr_first_via_active_edges=pwr_edge,
        g_terminal_active_coordinate=g_terminals,
        pwr_terminal_active_coordinate=pwr_terminal_native,
        pwr_terminal_names=pwr_terminal_names,
        pwr_selected_source_terminal_index=np.asarray([pwr_selected_terminal]),
        pwr_selected_source_active_coordinate=np.asarray([2699]),
        field_current_block_offset=np.asarray([0, g_current_count, g_current_count + pwr_current_count]),
        pwr_endpoint_contact_gc_stamp_ids=gc_occurrences,
        pwr_endpoint_contact_gc_first_active_index=gc_first[gc_occurrences],
        pwr_endpoint_contact_gc_second_active_index=gc_second[gc_occurrences],
        pwr_endpoint_contact_gc_admittance_s=gc_y[gc_occurrences],
    )
    pack(payload, "source_outward_active", h_active)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    artifact = output / "pwr-g-native-frequency-boundary.npz"
    np.savez_compressed(artifact, **payload)
    result = dict(
        program=PROGRAM,
        version=VERSION,
        status="PASS_EXECUTABLE_PWR_G_NATIVE_FINITE_Y_AND_SOURCE_H",
        frequency_hz=1e6,
        elapsed_s=monotonic() - started,
        driver_sha256=sha(Path(__file__)),
        artifact_sha256=sha(artifact),
        pins={str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        counts=dict(
            retained_finite_branches=len(retained),
            retired_first_vias=109,
            retired_g_first_vias=2,
            retired_pwr_first_vias=107,
            native_potentials=potential_count,
            g_field_currents=g_current_count,
            pwr_field_currents=pwr_current_count,
            source_outward_nnz=h_active.nnz,
            pwr_endpoint_contact_gc_stamps=len(gc_occurrences),
        ),
        metrics=dict(
            complex_y_ordinary_reciprocity_relative=reciprocity,
            edge_nodal_work_relative=work_error,
            normalized_current_balance=balance,
            source_current_voltage_dual_work_relative=h_work,
        ),
        ownership=dict(
            native_port_positive_active_index=2699,
            native_port_negative_active_index=2656,
            pwr_top_terminal_restriction="107 geometric TOP H groups -> declared native DUT PWR coordinate2699",
            pwr_lower_terminal_restriction="107 geometric lower R20 H groups ->107 distinct native active coordinates",
            g_terminal_restriction="existing accepted [2656,3996022,3996023] mapping",
            circuit_equation="Y_native*v - H_active*[i_G;i_PWR] + other_owned_terms = b",
        ),
        scope=(
            "Actual fixed-1MHz native finite admittance and PWR+G source H action after retiring exactly109 "
            "overlapping first-via scalar branches. Every other native finite branch is preserved, including "
            "the existing product port coordinates. Contact-GC rows touching PWR endpoints are enumerated and "
            "left unchanged; this artifact does not claim they are replaced. No field L/P, dielectric model, "
            "RHS, factorization, solve, convergence, or board/PowerSI accuracy claim."
        ),
    )
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "counts": result["counts"]}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.output.resolve())
    except Exception:
        if not args.output.exists():
            args.output.mkdir(parents=True)
        (args.output / "failure.json").write_text(
            json.dumps(dict(program=PROGRAM, version=VERSION, status="STOP_PWR_G_NATIVE_BOUNDARY", traceback=traceback.format_exc()), indent=2),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
