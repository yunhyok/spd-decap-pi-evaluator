"""SPD Decap PI Evaluator v0.23.1: G two-post interface current lifts.

Build three minimum-Joule RT0 basis columns on the qualified actual G
two-post neighborhood.  Zero flux on other exterior faces defines these
columns only; the saved fine exterior/current/charge complement is retained.
"""

from __future__ import annotations

import argparse
from collections import deque
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import bmat, csr_matrix, vstack
from scipy.sparse.linalg import splu


ROOT = Path(__file__).resolve().parents[2]
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
PINS = {
    "tools/research/prepare_astra_selected_g_l02_two_post_neighborhood.py":
        "7eb0628b97a82f2035577f71bb90c8a01d94c0e2ef956b332caa4e632be3f433",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/result.json":
        "bb2797dbbeb9f30997113732ce3350f2dba5dd4bc909984520131bcc0e26c0bd",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz":
        "600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz":
        "fee5084bfddc44771a16850eeefef2a2731ec29d4a132c6a03f3f42c40c719f1",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-review-02/independent-review.json":
        "8c69357d65cf7f70a224771eb1dd5e59374abb2810e1bccfd4c757858f4273cf",
    "outputs/research/astra-selected-g-l02-two-post-neighborhood-review-02/independent-metrics.npz":
        "80d2de401c78694c744fcb8bb56be76ad119426588fdb8423565399d1e68be43",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def sparse_from_saved(saved, prefix: str) -> csr_matrix:
    return csr_matrix(
        (
            saved[f"{prefix}_data"],
            saved[f"{prefix}_col"],
            saved[f"{prefix}_row_ptr"],
        ),
        shape=tuple(saved[f"{prefix}_shape"]),
    )


def cycle_witnesses(cell_count, internal_faces, owner_cells, first_owner, count=8):
    graph = [[] for _ in range(cell_count)]
    for face_id, (a, b) in zip(internal_faces, owner_cells, strict=True):
        graph[int(a)].append((int(b), int(face_id)))
        graph[int(b)].append((int(a), int(face_id)))
    witnesses = []
    for offset in np.linspace(0, len(internal_faces) - 1, count * 3, dtype=int):
        excluded = int(internal_faces[offset])
        start, target = map(int, owner_cells[offset])
        parent = {start: None}
        queue = deque([start])
        while queue and target not in parent:
            cell = queue.popleft()
            for other, face_id in graph[cell]:
                if face_id != excluded and other not in parent:
                    parent[other] = (cell, face_id)
                    queue.append(other)
        if target not in parent:
            continue
        values = {excluded: 1.0}
        cell = target
        while cell != start:
            previous, face_id = parent[cell]
            values[face_id] = -(1.0 if first_owner[face_id] == previous else -1.0)
            cell = previous
        witnesses.append(values)
        if len(witnesses) == count:
            break
    assert len(witnesses) == count
    return witnesses


def run(output: Path) -> None:
    started = monotonic()
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative, expected in PINS.items():
            assert digest(ROOT / relative) == expected, relative
        review = json.loads(
            (ROOT / "outputs/research/astra-selected-g-l02-two-post-neighborhood-review-02/independent-review.json").read_text()
        )
        assert review["status"] == "ACCEPT_WITH_SCOPE"

        mesh_path = ROOT / "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz"
        operator_path = ROOT / "outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz"
        with np.load(mesh_path, allow_pickle=False) as mesh:
            vertices_m = mesh["vertices_um"] * 1.0e-6
            cells = mesh["cells"].copy()
            cell_body = mesh["cell_body"].copy()
            face_vertices = mesh["face_vertices"].copy()
            first_owner = mesh["first_owner_cell"].copy()
            internal_faces = mesh["internal_face_ids"].copy()
            owner_cells = mesh["internal_owner_cells"].copy()
            boundary_faces = mesh["boundary_face_ids"].copy()
            boundary_state = mesh["boundary_state"].copy()
            top_faces = mesh["top_electrode_face_ids"].copy()
            lower_faces = mesh["lower_r20_next_via_contact_face_ids"].copy()
            window_cut_faces = mesh["retained_window_cut_face_ids"].copy()
        with np.load(operator_path, allow_pickle=False) as saved:
            resistance = sparse_from_saved(saved, "resistance_ohm")
            divergence = sparse_from_saved(saved, "volume_d")
            cell_face_ids = saved["cell_face_ids"].copy()
            cell_face_signs = saved["cell_face_signs"].copy()
            conductivity = float(saved["conductivity_s_m"][0])

        face_count = len(face_vertices)
        cell_count = len(cells)
        assert (cell_count, face_count) == (6078, 14254)
        assert resistance.shape == (face_count, face_count)
        assert divergence.shape == (cell_count, face_count)
        assert len(top_faces) == 984 and len(lower_faces) == 192

        top_left = top_faces[cell_body[first_owner[top_faces]] == 0]
        top_right = top_faces[cell_body[first_owner[top_faces]] == 1]
        lower_left = lower_faces[cell_body[first_owner[lower_faces]] == 0]
        lower_right = lower_faces[cell_body[first_owner[lower_faces]] == 1]
        patches = [top_left, lower_left, top_right, lower_right]
        patch_names = np.asarray(
            ["LEFT_TOP_GROUP", "LEFT_L03_R20", "RIGHT_TOP_GROUP", "RIGHT_L03_R20"]
        )
        assert list(map(len, patches)) == [492, 96, 492, 96]
        patch_face_ids = np.concatenate(patches)
        patch_face_row = np.concatenate(
            [np.full(len(patch), row, dtype=np.int64) for row, patch in enumerate(patches)]
        )
        patch = csr_matrix(
            (np.ones(len(patch_face_ids)), (patch_face_row, patch_face_ids)),
            shape=(4, face_count),
        )
        assert len(np.unique(patch_face_ids)) == len(patch_face_ids)

        active_faces = np.union1d(internal_faces, patch_face_ids)
        inactive_faces = np.setdiff1d(np.arange(face_count), active_faces)
        assert set(window_cut_faces).issubset(set(inactive_faces))
        metric = resistance[active_faces][:, active_faces].tocsc()
        metric_scale = float(np.median(metric.diagonal()))
        constraints = vstack(
            [divergence[:, active_faces], patch[:3, active_faces]], format="csc"
        )
        targets = np.asarray(
            [
                [-1.0, 0.0, -1.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 1.0],
                [0.0, -1.0, 0.0],
            ]
        )
        # Columns: left TOP->left L03; right L03->right TOP; left TOP->right TOP.
        # The fourth patch total follows from cellwise conservation.
        assert np.array_equal(targets.sum(axis=0), np.zeros(3))
        saddle = bmat(
            [[metric / metric_scale, constraints.T], [constraints, None]],
            format="csc",
        )
        rhs = np.zeros((saddle.shape[0], 3))
        rhs[len(active_faces) + cell_count :] = targets[:3]
        factor_started = monotonic()
        factor = splu(saddle, permc_spec="COLAMD")
        factor_s = monotonic() - factor_started
        solution = factor.solve(rhs)
        flux = np.zeros((face_count, 3))
        flux[active_faces] = solution[: len(active_faces)]

        algebraic = float(np.linalg.norm(saddle @ solution - rhs) / np.linalg.norm(rhs))
        divergence_error = float(np.max(np.abs(divergence @ flux)))
        patch_error = float(np.max(np.abs(patch @ flux - targets)))
        inactive_error = float(np.max(np.abs(flux[inactive_faces]), initial=0.0))
        assert algebraic < 2.0e-10
        assert divergence_error < 2.0e-10
        assert patch_error < 2.0e-10
        assert inactive_error == 0.0

        gram = flux.T @ (resistance @ flux)
        gram = np.asarray(gram)
        gram_eigenvalues = np.linalg.eigvalsh((gram + gram.T) / 2.0)
        gram_symmetry = float(np.linalg.norm(gram - gram.T) / np.linalg.norm(gram))
        assert float(gram_eigenvalues.min()) > 0.0 and gram_symmetry < 1.0e-13

        tetrahedra = vertices_m[cells]
        center = tetrahedra.mean(axis=1)
        volume = np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1]) / 6.0
        assert float(volume.min()) > 0.0
        variance = np.sum((tetrahedra - center[:, None, :]) ** 2, axis=(1, 2)) / 20.0
        local_flux = cell_face_signs[:, :, None] * flux[cell_face_ids]
        current_center = np.einsum(
            "cin,cid->cnd", local_flux, center[:, None, :] - tetrahedra
        ) / (3.0 * volume[:, None, None])
        radial = local_flux.sum(axis=1) / (3.0 * volume[:, None])
        centered_gram = np.einsum(
            "c,cni,cmj,ij->nm",
            volume / conductivity,
            current_center,
            current_center,
            np.eye(3),
        )
        centered_gram += np.einsum(
            "c,cn,cm->nm", volume * variance / conductivity, radial, radial
        )
        centered_relative = float(np.linalg.norm(centered_gram - gram) / np.linalg.norm(gram))
        assert centered_relative < 3.0e-12

        cycle_rows = []
        for values in cycle_witnesses(
            cell_count, internal_faces, owner_cells, first_owner
        ):
            witness = np.zeros(face_count)
            for face_id, value in values.items():
                witness[face_id] = value
            assert np.max(np.abs(divergence @ witness)) == 0.0
            assert np.max(np.abs(patch @ witness)) == 0.0
            energy = float(witness @ (resistance @ witness))
            orthogonality = witness @ (resistance @ flux)
            normalized = np.abs(orthogonality) / np.sqrt(energy * np.diag(gram))
            assert float(normalized.max()) < 2.0e-9
            cycle_rows.append(
                {
                    "face_ids": list(map(int, values)),
                    "face_flux": list(map(float, values.values())),
                    "energy_ohm": energy,
                    "normalized_stationarity": normalized.tolist(),
                }
            )

        artifact = output / "g-two-post-interface-lifts.npz"
        np.savez_compressed(
            artifact,
            face_flux_basis=flux,
            basis_names=np.asarray(
                ["LEFT_TOP_TO_LEFT_L03", "RIGHT_L03_TO_RIGHT_TOP", "LEFT_TOP_TO_RIGHT_TOP"]
            ),
            patch_names=patch_names,
            patch_face_row=patch_face_row,
            patch_face_ids=patch_face_ids,
            patch_flux_targets=targets,
            active_face_ids=active_faces,
            inactive_complement_face_ids=inactive_faces,
            retained_window_cut_face_ids=window_cut_faces,
            metric_scale_ohm=np.asarray([metric_scale]),
            lagrange_multipliers_scaled=solution[len(active_faces) :],
            joule_gram_ohm=gram,
            cell_current_center_per_m2=current_center,
            cell_rt0_radial_coefficient_per_m3=radial,
            cell_volume_m3=volume,
            cell_radial_variance_m2=variance,
        )
        cycle_path = output / "cycle-stationarity.json"
        cycle_path.write_text(json.dumps(cycle_rows, indent=2, allow_nan=False), encoding="utf-8")

        checks = {
            "fine_face_dofs": face_count,
            "basis_count": 3,
            "active_face_dofs": len(active_faces),
            "inactive_complement_face_dofs": len(inactive_faces),
            "window_cut_faces_retained_in_complement": len(window_cut_faces),
            "patch_face_counts": list(map(len, patches)),
            "saddle_shape": list(saddle.shape),
            "saddle_nnz": int(saddle.nnz),
            "factor_nnz": int(factor.L.nnz + factor.U.nnz),
            "factor_s": factor_s,
            "algebraic_relative_residual": algebraic,
            "maximum_cell_integrated_divergence_a": divergence_error,
            "maximum_patch_flux_error_a": patch_error,
            "maximum_inactive_face_flux_a": inactive_error,
            "joule_gram_ohm": gram.tolist(),
            "minimum_joule_gram_eigenvalue_ohm": float(gram_eigenvalues.min()),
            "joule_gram_symmetry_relative": gram_symmetry,
            "independent_centered_joule_relative": centered_relative,
            "cycle_count": len(cycle_rows),
            "maximum_cycle_stationarity_relative": max(
                max(row["normalized_stationarity"]) for row in cycle_rows
            ),
        }
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "PASS_SELECTED_G_TWO_POST_MINIMUM_JOULE_INTERFACE_LIFTS",
            "elapsed_s": monotonic() - started,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifact_sha256": digest(artifact),
            "cycle_receipt_sha256": digest(cycle_path),
            "checks": checks,
            "basis_contract": (
                "Three independent zero-net boundary-flux columns connect the two actual whole-TOP group patches "
                "and their two exact r20 L03 continuations. Aggregate patch currents are constrained while every "
                "individual patch-face current is optimized by the full sparse copper Joule metric."
            ),
            "scope": (
                "Reusable basis-column construction on one qualified actual G neighborhood. Zero flux on all other "
                "exterior faces applies only inside these three columns; the saved inactive fine-face list, window-cut "
                "continuations, boundary charges and circulation spaces remain available. The directions do not ground "
                "L03, prescribe equal branch currents, or make the local window an isolated conductor. No Green, field, "
                "terminal Z, board response, convergence, or PowerSI-accuracy claim."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "checks": checks}))
    except Exception as error:
        (output / "failure.json").write_text(
            json.dumps(
                {
                    "program": PROGRAM,
                    "version": VERSION,
                    "status": "STOP_SELECTED_G_TWO_POST_INTERFACE_LIFTS",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "elapsed_s": monotonic() - started,
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    destination = arguments.output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
