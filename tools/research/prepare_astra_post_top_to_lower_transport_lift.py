"""SPD Decap PI Evaluator v0.23.1: TOP-to-r20 lower post transport lift.

Build one minimum-Joule RT0 basis column on the accepted source-model post.
The whole TOP pad carries 1 A inward and the exact r20 lower contact carries
1 A outward.  Other exterior flux is zero only in this basis column; all
complementary current and charge spaces remain available downstream.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/result.json":
        "44fe27c9326105143ca7b608fae681f93b3b80e6a271f3582829f9a7f64ff186",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-boundary-post-lower-contact-02/result.json":
        "622f738819b93b186453d491b078fe351cd152903f2336d6e4f5e5de6b41ee3d",
    "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz":
        "3f8b47b136bc32c8dcba503911de42495853c8b4c24f2962daee957b3d643975",
    "outputs/research/astra-device-l02-via-contacts-03/result.json":
        "24b32ddef4854bf80d63de9583ae5afd97ed5664e3e0d256a4ae9c5a658cd43a",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def face_coordinate_key(triangle: np.ndarray) -> tuple[tuple[float, float, float], ...]:
    return tuple(sorted(tuple(np.round(point, 12)) for point in triangle))


def run(output: Path) -> None:
    start = monotonic()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative, expected in PINS.items():
            assert digest(ROOT / relative) == expected, relative

        from scipy.sparse import bmat, coo_matrix, csr_matrix, vstack
        from scipy.sparse.linalg import splu

        with np.load(
            ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz",
            allow_pickle=False,
        ) as mesh:
            vertices_um = mesh["vertices_local_um"]
            cells = mesh["cells"]
            cell_body = mesh["cell_body"]
            face_vertices = mesh["face_vertices"]
            first_owner = mesh["first_owner_cell"]
            internal_face_ids = mesh["internal_face_ids"]
            internal_owners = mesh["internal_owner_cells"]
            boundary_face_ids = mesh["boundary_face_ids"]
            area_vectors_um2 = mesh["face_area_vector_um2"]
        with np.load(
            ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz",
            allow_pickle=False,
        ) as current:
            resistance = csr_matrix(
                (
                    current["resistance_data_ohm"],
                    current["mass_col"],
                    current["mass_row_ptr"],
                ),
                shape=tuple(current["mass_shape"]),
            )
            divergence = csr_matrix(
                (
                    current["volume_b_data"],
                    current["volume_b_col"],
                    current["volume_b_row_ptr"],
                ),
                shape=tuple(current["volume_b_shape"]),
            )
            local_columns = current["local_rt0_face_columns"]
            local_signs = current["local_rt0_face_signs"]
            pad_electrode_face_ids = current["pad_electrode_face_ids"]
            conductivity = float(current["conductivity_s_m"][0])
        with np.load(
            ROOT / "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz",
            allow_pickle=False,
        ) as contact:
            post_contact_face_vertices = contact["contact_face_vertices"]

        post_cells = np.flatnonzero(cell_body == 0)
        assert len(post_cells) == 2604
        post_internal = internal_face_ids[
            np.all(cell_body[internal_owners] == 0, axis=1)
        ]
        top_faces = pad_electrode_face_ids[
            cell_body[first_owner[pad_electrode_face_ids]] == 0
        ]
        assert len(post_internal) == 4240 and len(top_faces) == 484

        # Map the independently certified post-template contact to joint face IDs
        # by exact local triangles, rather than assuming face-number equality.
        post_template = np.load(
            ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz",
            allow_pickle=False,
        )
        try:
            post_vertices = post_template["vertices_local_um"]
            certified_triangles = post_vertices[post_contact_face_vertices]
        finally:
            post_template.close()
        certified_keys = {face_coordinate_key(triangle) for triangle in certified_triangles}
        joint_boundary_triangles = vertices_um[face_vertices[boundary_face_ids]]
        lower_candidates = boundary_face_ids[
            np.all(np.abs(joint_boundary_triangles[:, :, 2] - 75.0) < 1.0e-12, axis=1)
            & (cell_body[first_owner[boundary_face_ids]] == 0)
        ]
        lower_contact_faces = np.asarray(
            [
                face
                for face in lower_candidates
                if face_coordinate_key(vertices_um[face_vertices[face]]) in certified_keys
            ],
            dtype=np.int64,
        )
        assert len(lower_candidates) == 288
        assert len(lower_contact_faces) == 96
        assert {
            face_coordinate_key(vertices_um[face_vertices[face]])
            for face in lower_contact_faces
        } == certified_keys

        active_faces = np.unique(
            np.concatenate((post_internal, top_faces, lower_contact_faces))
        )
        assert len(active_faces) == 4820
        post_boundary = boundary_face_ids[
            cell_body[first_owner[boundary_face_ids]] == 0
        ]
        other_exterior = np.setdiff1d(
            post_boundary, np.concatenate((top_faces, lower_contact_faces))
        )
        interbody_faces = internal_face_ids[
            np.any(cell_body[internal_owners] != 0, axis=1)
            & np.any(cell_body[internal_owners] == 0, axis=1)
        ]

        top_columns = np.searchsorted(active_faces, top_faces)
        top_constraint = coo_matrix(
            (
                np.ones(len(top_faces)),
                (np.zeros(len(top_faces), dtype=np.int64), top_columns),
            ),
            shape=(1, len(active_faces)),
        ).tocsr()
        constrained_divergence = divergence[post_cells][:, active_faces]
        constraints = vstack(
            (constrained_divergence, top_constraint), format="csc"
        )
        metric = resistance[active_faces][:, active_faces].tocsc()
        metric_scale = float(np.median(metric.diagonal()))
        saddle = bmat(
            [[metric / metric_scale, constraints.T], [constraints, None]],
            format="csc",
        )
        right_hand_side = np.zeros(len(active_faces) + len(post_cells) + 1)
        right_hand_side[-1] = -1.0
        solution = splu(saddle).solve(right_hand_side)
        face_flux = np.zeros(resistance.shape[0])
        face_flux[active_faces] = solution[: len(active_faces)]

        divergence_error = float(np.max(np.abs(divergence[post_cells] @ face_flux)))
        top_total = float(np.sum(face_flux[top_faces]))
        lower_total = float(np.sum(face_flux[lower_contact_faces]))
        other_exterior_error = float(np.max(np.abs(face_flux[other_exterior])))
        interbody_error = float(np.max(np.abs(face_flux[interbody_faces])))
        kkt_relative = float(
            np.linalg.norm(saddle @ solution - right_hand_side)
            / np.linalg.norm(right_hand_side)
        )

        vertices_m = vertices_um * 1.0e-6
        tetrahedra = vertices_m[cells[post_cells]]
        centers = tetrahedra.mean(axis=1)
        volumes = np.abs(
            np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])
        ) / 6.0
        variance = np.sum(
            (tetrahedra - centers[:, None, :]) ** 2, axis=(1, 2)
        ) / 20.0
        signed_local_flux = (
            local_signs[post_cells]
            * face_flux[local_columns[post_cells]]
        )
        center_current = np.einsum(
            "ci,cid->cd",
            signed_local_flux,
            centers[:, None, :] - tetrahedra,
        ) / (3.0 * volumes[:, None])
        radial = signed_local_flux.sum(axis=1) / (3.0 * volumes)
        sparse_energy = float(face_flux @ (resistance @ face_flux))
        centered_energy = float(
            np.sum(
                volumes
                / conductivity
                * (
                    np.sum(center_current**2, axis=1)
                    + variance * radial**2
                )
            )
        )
        energy_relative = abs(sparse_energy - centered_energy) / sparse_energy

        boundary_active = np.concatenate((top_faces, lower_contact_faces))
        interior_current = face_flux[post_internal]
        boundary_current = face_flux[boundary_active]
        interior_boundary_cross = float(
            2.0
            * interior_current
            @ (
                resistance[post_internal][:, boundary_active]
                @ boundary_current
            )
        )
        cross_fraction = interior_boundary_cross / sparse_energy
        top_area = area_vectors_um2[top_faces].sum(axis=0)
        lower_area = area_vectors_um2[lower_contact_faces].sum(axis=0)
        assert top_area[2] < 0.0 and lower_area[2] > 0.0

        checks = {
            "post_cells": len(post_cells),
            "active_face_dofs": len(active_faces),
            "post_internal_faces": len(post_internal),
            "top_patch_faces": len(top_faces),
            "lower_r20_contact_faces": len(lower_contact_faces),
            "other_exterior_faces_retained_but_zero_in_this_column": len(other_exterior),
            "interbody_faces_retained_but_zero_in_this_column": len(interbody_faces),
            "saddle_unknowns": saddle.shape[0],
            "saddle_nnz": saddle.nnz,
            "metric_scale_ohm": metric_scale,
            "kkt_relative_residual": kkt_relative,
            "cell_divergence_max_a": divergence_error,
            "top_inward_flux_a": top_total,
            "lower_outward_flux_a": lower_total,
            "top_flux_error_a": abs(top_total + 1.0),
            "lower_flux_error_a": abs(lower_total - 1.0),
            "other_exterior_flux_max_a": other_exterior_error,
            "interbody_flux_max_a": interbody_error,
            "post_joule_energy_ohm": sparse_energy,
            "independent_centered_energy_relative": energy_relative,
            "interior_boundary_cross_energy_ohm": interior_boundary_cross,
            "interior_boundary_cross_fraction": cross_fraction,
        }
        assert kkt_relative < 1.0e-11
        assert divergence_error < 1.0e-11
        assert abs(top_total + 1.0) < 1.0e-11
        assert abs(lower_total - 1.0) < 1.0e-11
        assert other_exterior_error == 0.0
        assert interbody_error == 0.0
        assert sparse_energy > 0.0
        assert energy_relative < 3.0e-13

        artifact = output / "top-to-lower-transport-lift.npz"
        np.savez_compressed(
            artifact,
            face_flux_basis=face_flux[:, None],
            active_face_ids=active_faces,
            post_cell_ids=post_cells,
            post_internal_face_ids=post_internal,
            top_patch_face_ids=top_faces,
            lower_contact_face_ids=lower_contact_faces,
            other_post_exterior_face_ids=other_exterior,
            post_bridge_interface_face_ids=interbody_faces,
            top_area_vector_um2=top_area,
            lower_contact_area_vector_um2=lower_area,
            metric_scale_ohm=np.asarray([metric_scale]),
            divergence_dual=solution[len(active_faces) : -1],
            top_flux_dual=solution[-1:],
            cell_current_center_per_m2=center_current,
            cell_rt0_radial_coefficient_per_m3=radial,
            cell_volume_m3=volumes,
            cell_radial_variance_m2=variance,
            post_joule_energy_ohm=np.asarray([sparse_energy]),
        )
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "PASS_POST_TOP_TO_ACTUAL_R20_LOWER_TRANSPORT_LIFT",
            "elapsed_s": monotonic() - start,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifact_sha256": digest(artifact),
            "checks": checks,
            "source_instance_mapping": {
                "selected_device_posts": 1956,
                "geometry": "translate one accepted centered post; preserve P/G group provenance",
                "current_assignment": "individual post coefficients remain unknown",
            },
            "scope": (
                "One source-compatible static basis column on one accepted post template. "
                "It transports one ampere from the whole TOP pad to the exact r20 lower-via "
                "contact with minimum post-owned Joule energy. Zero flux on other exterior "
                "and post-bridge faces defines only this column; those fine/contact/charge "
                "and lateral/circulation spaces remain available. Translation to 1,956 "
                "source posts does not impose equal post currents, equipotential lower cuts, "
                "or a complete terminal. No Green, field, Z, rail, return, board or PowerSI "
                "accuracy claim."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "checks": checks}))
    except Exception:
        (output / "failure.json").write_text(
            json.dumps(
                {
                    "status": "STOP_POST_TOP_TO_LOWER_TRANSPORT_LIFT",
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.output.resolve())
