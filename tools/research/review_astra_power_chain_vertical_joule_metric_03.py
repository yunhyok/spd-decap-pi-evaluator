"""Strict saved-only review of the 5,643-coordinate power-chain Joule metric.

This reviewer reconstructs every graph owner map and every local body term from
the hash-pinned sparse inputs.  It executes fixed numerical gates before it can
write ACCEPT_WITH_SCOPE.  It performs no Green integration or field solve.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.linalg import eigvalsh
from scipy.sparse import bmat, coo_matrix, csr_matrix, diags


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-power-chain-vertical-joule-metric-review-03"
PINS = {
    "tools/research/assemble_astra_power_chain_vertical_joule_metric.py":
        "0fa416341dd53b99ccc594e6f0066e23f0ff8fe152169e77639050d674c2dd60",
    "outputs/research/astra-power-chain-vertical-joule-metric-01/result.json":
        "9cd7cfdf42af26afd1d0eecdc43a2215f6efe9edc610d280aa4c2e23e7c47893",
    "outputs/research/astra-power-chain-vertical-joule-metric-01/power-chain-vertical-joule-metric.npz":
        "0a5a75a6be25d7e2c83d6c80f204f860b6297e2c0b8af2f12abaed2e91f3e2aa",
    "outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz":
        "a4ecf2a92ec7ee41b9a1496fc8765a2150bee945fd9d1807300d4e2389e288d4",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz":
        "9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92",
    "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz":
        "b169d79ce8fc61c141efa572ce2aa2c4905fc5efa3905cac139dd07efc165538",
}
GATES = {
    "sparse_reconstruction_relative": 3.0e-13,
    "symmetry_absolute_ohm": 1.0e-16,
    "local_block_relative": 3.0e-13,
    "energy_relative": 3.0e-13,
    "flux_absolute_a": 3.0e-14,
    "translation_absolute_um": 1.0e-9,
    "component_normalized_min_eigenvalue": 1.0e-6,
    "component_cholesky_min_sqrt_ohm": 1.0e-12,
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def rel(actual: np.ndarray, expected: np.ndarray) -> float:
    return float(np.linalg.norm(actual - expected) /
                 max(float(np.linalg.norm(expected)), np.finfo(float).tiny))


def sparse_rel(actual: csr_matrix, expected: csr_matrix) -> float:
    delta = (actual - expected).tocsr()
    delta.eliminate_zeros()
    numerator = float(np.sqrt(np.sum(np.abs(delta.data) ** 2)))
    denominator = float(np.sqrt(np.sum(np.abs(expected.data) ** 2)))
    return numerator / max(denominator, np.finfo(float).tiny)


def centered_fields(vertices: np.ndarray, cells: np.ndarray, columns: np.ndarray,
                    signs: np.ndarray, face_flux: np.ndarray):
    tetra = vertices[cells]
    center = tetra.mean(axis=1)
    volume = np.abs(np.linalg.det(tetra[:, 1:] - tetra[:, :1])) / 6.0
    variance = np.sum((tetra - center[:, None, :]) ** 2, axis=(1, 2)) / 20.0
    flux = signs[:, :, None] * face_flux[columns]
    current = np.einsum("cim,cid->cmd", flux, center[:, None, :] - tetra) / (
        3.0 * volume[:, None, None]
    )
    radial = flux.sum(axis=1) / (3.0 * volume[:, None])
    return current, radial, volume, variance, tetra


def gram(ca, ra, cb, rb, volume, variance, conductivity):
    return (np.einsum("c,cid,cjd->ij", volume / conductivity, ca, cb) +
            np.einsum("c,ci,cj->ij", volume * variance / conductivity, ra, rb))


def explicit_energy(lateral, vertical, outgoing, incoming, left_c, left_r,
                    right_c, right_r, bridge_c, bridge_r, vert_c, vert_r,
                    post_v, post_var, bridge_v, bridge_var, conductivity):
    total = 0.0
    for post, coefficient in enumerate(vertical):
        c = coefficient * vert_c
        r = coefficient * vert_r
        edge = int(outgoing[post])
        if edge >= 0:
            c = c + np.einsum("cmd,m->cd", left_c, lateral[edge])
            r = r + left_r @ lateral[edge]
        edge = int(incoming[post])
        if edge >= 0:
            c = c + np.einsum("cmd,m->cd", right_c, lateral[edge])
            r = r + right_r @ lateral[edge]
        total += float(np.sum(post_v / conductivity *
                              (np.sum(np.abs(c) ** 2, axis=1) + post_var * np.abs(r) ** 2)))
    for coefficient in lateral:
        c = np.einsum("cmd,m->cd", bridge_c, coefficient)
        r = bridge_r @ coefficient
        total += float(np.sum(bridge_v / conductivity *
                              (np.sum(np.abs(c) ** 2, axis=1) + bridge_var * np.abs(r) ** 2)))
    return total


def run() -> None:
    started = monotonic()
    if OUT.exists():
        raise FileExistsError(OUT)
    OUT.mkdir(parents=True)
    (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for path, expected in PINS.items():
            actual = digest(ROOT / path)
            if actual != expected:
                raise AssertionError(f"pin mismatch {path}: {actual}")

        with np.load(ROOT / "outputs/research/astra-power-chain-vertical-joule-metric-01/power-chain-vertical-joule-metric.npz", allow_pickle=False) as z:
            saved = {key: z[key].copy() for key in z.files}
        with np.load(ROOT / "outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz", allow_pickle=False) as z:
            lateral = {key: z[key].copy() for key in z.files}
        with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as z:
            vertices = z["vertices_local_um"] * 1.0e-6
            cells = z["cells"].copy()
            body = z["cell_body"].copy()
        with np.load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz", allow_pickle=False) as z:
            columns = z["local_rt0_face_columns"].copy()
            signs = z["local_rt0_face_signs"].copy()
            conductivity = float(z["conductivity_s_m"][0])
        with np.load(ROOT / "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz", allow_pickle=False) as z:
            lateral_flux = z["face_flux_basis"].copy()
            left_top = z["electrode_left_ids"].copy()
            right_top = z["electrode_right_ids"].copy()
        with np.load(ROOT / "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz", allow_pickle=False) as z:
            vertical_flux = z["face_flux_basis"][:, :1].copy()
            saved_vert_center = z["cell_current_center_per_m2"].copy()
            saved_vert_radial = z["cell_rt0_radial_coefficient_per_m3"].copy()
            saved_vert_energy = float(z["post_joule_energy_ohm"][0])
            top_faces = z["top_patch_face_ids"].copy()
            lower_faces = z["lower_contact_face_ids"].copy()

        n_lateral, n_post, n_edge, n_mode = 4665, 978, 933, 5
        shape = tuple(map(int, saved["resistance_shape"]))
        if shape != (5643, 5643):
            raise AssertionError(f"unexpected saved shape {shape}")
        metric = csr_matrix((saved["resistance_data_ohm"], saved["resistance_col"],
                             saved["resistance_row_ptr"]), shape=shape)
        lateral_metric = csr_matrix((lateral["resistance_data_ohm"], lateral["resistance_col"],
                                     lateral["resistance_row_ptr"]), shape=(n_lateral, n_lateral))
        if metric.nnz != 87363 or lateral_metric.nnz != 67725:
            raise AssertionError("unexpected sparse nonzero count")
        if not np.all(np.isfinite(metric.data)):
            raise AssertionError("nonfinite resistance entry")

        current, radial, volume, variance, tetra = centered_fields(
            vertices, cells, columns, signs, lateral_flux
        )
        vertical_current, vertical_radial_all, _, _, _ = centered_fields(
            vertices, cells, columns, signs, vertical_flux
        )
        ids = [np.flatnonzero(body == index) for index in range(3)]
        if [len(index) for index in ids] != [2604, 2604, 96]:
            raise AssertionError("unexpected joint body partition")
        left, right, bridge = ids
        canonical_post_cell_translation_um = float(np.max(np.abs(
            (tetra[right] - tetra[left]) * 1.0e6 - np.array([130.0, 0.0, 0.0])
        )))
        vertical_center_relative = rel(vertical_current[left, 0], saved_vert_center)
        vertical_radial_relative = rel(vertical_radial_all[left, 0], saved_vert_radial)

        left_c, right_c, bridge_c = current[left], current[right], current[bridge]
        left_r, right_r, bridge_r = radial[left], radial[right], radial[bridge]
        vert_c, vert_r = vertical_current[left, 0], vertical_radial_all[left, 0]
        post_v, post_var = volume[left], variance[left]
        bridge_v, bridge_var = volume[bridge], variance[bridge]
        vert_energy = float(gram(vert_c[:, None], vert_r[:, None], vert_c[:, None],
                                 vert_r[:, None], post_v, post_var, conductivity)[0, 0])
        cross_left = gram(left_c, left_r, vert_c[:, None], vert_r[:, None],
                          post_v, post_var, conductivity)[:, 0]
        cross_right = gram(right_c, right_r, vert_c[:, None], vert_r[:, None],
                           post_v, post_var, conductivity)[:, 0]
        vertical_energy_relative = abs(vert_energy - saved_vert_energy) / saved_vert_energy
        saved_local_relative = max(
            abs(vert_energy - float(saved["local_vertical_post_energy_ohm"][0])) / vert_energy,
            rel(cross_left, saved["local_vertical_left_lateral_cross_ohm"]),
            rel(cross_right, saved["local_vertical_right_lateral_cross_ohm"]),
            vertical_center_relative,
            vertical_radial_relative,
        )

        edge_left = lateral["edge_left_post"].astype(np.int64)
        edge_right = lateral["edge_right_post"].astype(np.int64)
        outgoing = lateral["post_left_outgoing_edge"].astype(np.int64)
        incoming = lateral["post_right_incoming_edge"].astype(np.int64)
        translation = lateral["edge_translation_xy_um"]
        post_xy = lateral["post_xy_um"]
        if any((len(edge_left) != n_edge, len(post_xy) != n_post,
                np.any(edge_left == edge_right), len(np.unique(lateral["trace_ids"])) != n_edge)):
            raise AssertionError("invalid saved power graph counts")
        # Every one of 933 edges must own exactly its left/outgoing and right/incoming rows.
        if not np.array_equal(outgoing[edge_left], np.arange(n_edge)):
            raise AssertionError("not every left post owns its outgoing edge")
        if not np.array_equal(incoming[edge_right], np.arange(n_edge)):
            raise AssertionError("not every right post owns its incoming edge")
        degree = (outgoing >= 0).astype(int) + (incoming >= 0).astype(int)
        if np.any((degree < 1) | (degree > 2)) or degree.sum() != 2 * n_edge:
            raise AssertionError("post owner rows do not cover every edge twice")
        if not (np.array_equal(saved["edge_left_post"], edge_left) and
                np.array_equal(saved["edge_right_post"], edge_right) and
                np.array_equal(saved["post_outgoing_edge"], outgoing) and
                np.array_equal(saved["post_incoming_edge"], incoming) and
                np.array_equal(saved["lateral_trace_ids"], lateral["trace_ids"]) and
                np.array_equal(saved["post_pins"], lateral["post_pins"]) and
                np.array_equal(saved["post_xy_um"], post_xy)):
            raise AssertionError("combined artifact graph maps differ from pinned lateral graph")
        edge_endpoint_translation_um = max(
            float(np.max(np.abs(translation - post_xy[edge_left]))),
            float(np.max(np.abs(translation + np.array([130.0, 0.0]) - post_xy[edge_right]))),
        )
        # Explicitly compare all cells on all 888 shared-post placements.
        shared_cell_translation_um = 0.0
        for incoming_edge, outgoing_edge in lateral["adjacent_edge_pairs"]:
            actual_in = tetra[right] * 1.0e6 + np.r_[translation[int(incoming_edge)], 0.0]
            actual_out = tetra[left] * 1.0e6 + np.r_[translation[int(outgoing_edge)], 0.0]
            shared_cell_translation_um = max(
                shared_cell_translation_um, float(np.max(np.abs(actual_in - actual_out)))
            )

        rows, cols, data = [], [], []
        for edge in range(n_edge):
            for mode in range(n_mode):
                rows.extend((n_mode * edge + mode, n_mode * edge + mode))
                cols.extend((int(edge_left[edge]), int(edge_right[edge])))
                data.extend((float(cross_left[mode]), float(cross_right[mode])))
        cross = coo_matrix((data, (rows, cols)), shape=(n_lateral, n_post)).tocsr()
        expected_metric = bmat([[lateral_metric, cross],
                                [cross.T, diags(np.full(n_post, vert_energy))]], format="csr")
        expected_metric.sum_duplicates()
        reconstruction_relative = sparse_rel(metric, expected_metric)
        skew = (metric - metric.T).tocsr(); skew.eliminate_zeros()
        symmetry_absolute = float(np.max(np.abs(skew.data))) if skew.nnz else 0.0

        # Reconstruct every TOP/lower owner row, not a representative edge.
        left_flux = lateral_flux[left_top].sum(axis=0)
        right_flux = lateral_flux[right_top].sum(axis=0)
        vertical_top = float(vertical_flux[top_faces].sum())
        vertical_lower = float(vertical_flux[lower_faces].sum())
        tr, tc, td, lr, lc, ld = [], [], [], [], [], []
        for edge in range(n_edge):
            for mode in range(n_mode):
                column = n_mode * edge + mode
                for row, value in ((int(edge_left[edge]), float(left_flux[mode])),
                                   (int(edge_right[edge]), float(right_flux[mode]))):
                    if value != 0.0:
                        tr.append(row); tc.append(column); td.append(value)
        for post in range(n_post):
            column = n_lateral + post
            tr.append(post); tc.append(column); td.append(vertical_top)
            lr.append(post); lc.append(column); ld.append(vertical_lower)
        top_expected = coo_matrix((td, (tr, tc)), shape=(n_post, shape[0])).tocsr()
        lower_expected = coo_matrix((ld, (lr, lc)), shape=(n_post, shape[0])).tocsr()
        top_saved = csr_matrix((saved["top_outward_flux_data_a"], saved["top_outward_flux_col"],
                                saved["top_outward_flux_row_ptr"]),
                               shape=tuple(saved["top_outward_flux_shape"]))
        lower_saved = csr_matrix((saved["lower_outward_flux_data_a"], saved["lower_outward_flux_col"],
                                  saved["lower_outward_flux_row_ptr"]),
                                 shape=tuple(saved["lower_outward_flux_shape"]))
        top_delta = (top_saved - top_expected).tocsr(); top_delta.eliminate_zeros()
        lower_delta = (lower_saved - lower_expected).tocsr(); lower_delta.eliminate_zeros()
        owner_map_flux_absolute = max(
            float(np.max(np.abs(top_delta.data))) if top_delta.nnz else 0.0,
            float(np.max(np.abs(lower_delta.data))) if lower_delta.nnz else 0.0,
            float(np.max(np.abs(left_flux - np.array([-1.0, 0.0, 0.0, 0.0, 0.0])))),
            float(np.max(np.abs(right_flux - np.array([1.0, 0.0, 0.0, 0.0, 0.0])))),
            abs(vertical_top + 1.0), abs(vertical_lower - 1.0),
        )
        kcl = np.asarray(top_saved.sum(axis=0) + lower_saved.sum(axis=0)).ravel()
        kcl_absolute = float(np.max(np.abs(kcl)))
        saved_kcl_relative = rel(kcl, saved["columnwise_top_plus_lower_kcl_a"])

        component_min, cholesky_min = [], []
        edge_component = lateral["edge_component"].astype(np.int64)
        post_component = saved["post_component"].astype(np.int64)
        for component in range(45):
            edges = np.flatnonzero(edge_component == component)
            posts = np.flatnonzero(post_component == component)
            index = np.r_[(n_mode * edges[:, None] + np.arange(n_mode)).ravel(), n_lateral + posts]
            block = metric[index][:, index].toarray()
            scale = np.sqrt(np.diag(block))
            normalized = block / scale[:, None] / scale[None, :]
            component_min.append(float(eigvalsh(normalized, subset_by_index=(0, 0))[0]))
            cholesky_min.append(float(np.min(np.diag(np.linalg.cholesky(block)))))
        eigen_min = min(component_min)
        chol_min = min(cholesky_min)
        component_saved_relative = max(
            rel(np.asarray(component_min), saved["component_normalized_min_eigenvalue"]),
            rel(np.asarray(cholesky_min), saved["component_cholesky_min_diagonal_sqrt_ohm"]),
        )

        rng = np.random.default_rng(2026090903)
        coordinate = rng.standard_normal(shape[0]) + 1j * rng.standard_normal(shape[0])
        sparse_energy = float(np.real(np.vdot(coordinate, metric @ coordinate)))
        body_energy = explicit_energy(
            coordinate[:n_lateral].reshape(n_edge, n_mode), coordinate[n_lateral:],
            outgoing, incoming, left_c, left_r, right_c, right_r, bridge_c, bridge_r,
            vert_c, vert_r, post_v, post_var, bridge_v, bridge_var, conductivity,
        )
        energy_relative = abs(sparse_energy - body_energy) / body_energy

        metrics = {
            "shape": list(shape), "nnz": int(metric.nnz),
            "all_edge_owner_rows_verified": n_edge,
            "all_post_owner_rows_verified": n_post,
            "canonical_post_cell_translation_max_um": canonical_post_cell_translation_um,
            "edge_endpoint_translation_max_um": edge_endpoint_translation_um,
            "shared_post_all_cell_translation_max_um": shared_cell_translation_um,
            "vertical_saved_field_and_local_block_max_relative": saved_local_relative,
            "vertical_accepted_energy_relative": vertical_energy_relative,
            "sparse_full_reconstruction_relative": reconstruction_relative,
            "symmetry_absolute_ohm": symmetry_absolute,
            "all_owner_flux_map_max_absolute_a": owner_map_flux_absolute,
            "column_kcl_max_absolute_a": kcl_absolute,
            "saved_kcl_vector_relative": saved_kcl_relative,
            "component_normalized_min_eigenvalue": eigen_min,
            "component_cholesky_min_sqrt_ohm": chol_min,
            "component_saved_arrays_max_relative": component_saved_relative,
            "fresh_seed_sparse_energy_ohm": sparse_energy,
            "fresh_seed_explicit_single_owned_energy_ohm": body_energy,
            "fresh_seed_energy_relative": energy_relative,
        }
        gate_results = {
            "sparse_reconstruction": reconstruction_relative <= GATES["sparse_reconstruction_relative"],
            "symmetry": symmetry_absolute <= GATES["symmetry_absolute_ohm"],
            "local_blocks": max(saved_local_relative, vertical_energy_relative,
                                component_saved_relative) <= GATES["local_block_relative"],
            "fresh_cell_energy": energy_relative <= GATES["energy_relative"],
            "owner_flux_and_kcl": max(owner_map_flux_absolute, kcl_absolute) <= GATES["flux_absolute_a"],
            "translations": max(canonical_post_cell_translation_um, edge_endpoint_translation_um,
                                shared_cell_translation_um) <= GATES["translation_absolute_um"],
            "positive_component_metric": eigen_min >= GATES["component_normalized_min_eigenvalue"],
            "component_cholesky": chol_min >= GATES["component_cholesky_min_sqrt_ohm"],
        }
        if not all(gate_results.values()):
            raise AssertionError(f"physical gates failed: {gate_results}")

        receipt = {
            "program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "ACCEPT_WITH_SCOPE", "elapsed_s": monotonic() - started,
            "reviewer_sha256": digest(Path(__file__)), "pins": PINS,
            "fixed_gates": GATES, "gate_results": gate_results, "metrics": metrics,
            "scope": (
                "Saved static basis metric only. All 933 edge and 978 post owner rows, per-cell "
                "shared-post translations, terminal flux maps, sparse metric blocks, positive component "
                "metrics and one fresh complex single-owned cell energy were independently rebuilt and "
                "gated. Lower remains ungrounded; ground/lower-return/charge/fine/circulation/Green spaces "
                "remain complementary. No field, Z, full rail, board, or PowerSI accuracy claim."
            ),
            "supersedes": (
                "Review02 is retained as a numerical diagnostic but had no executed fixed assertions "
                "before its unconditional ACCEPT status."
            ),
        }
        (OUT / "independent-review.json").write_text(
            json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": receipt["status"], "metrics": metrics}, allow_nan=False))
    except Exception:
        (OUT / "failure.json").write_text(json.dumps({
            "program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "STOP_POWER_CHAIN_VERTICAL_JOULE_REVIEW",
            "elapsed_s": monotonic() - started, "reviewer_sha256": digest(Path(__file__)),
            "pins": PINS, "fixed_gates": GATES, "traceback": traceback.format_exc(),
        }, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    run()
