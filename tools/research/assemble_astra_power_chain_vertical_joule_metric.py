"""SPD Decap PI Evaluator v0.23.1: lateral plus vertical power-chain Joule metric.

Combine the five accepted lateral lift coordinates on each of 933 selected
power bridges with one accepted whole-TOP-to-r20-lower transport lift on each
of the 978 physical posts.  Every post and bridge is integrated once.  A
post's vertical/lateral and adjacent lateral/lateral cross terms are retained.
This is a static basis metric and terminal-flux map, not a field solve.
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
    "tools/research/assemble_astra_power_chain_joule_metric.py":
        "012d3802cb27a05bd42d38d59ad9b4d315f4becc3c64460db55c7d34efb173ea",
    "outputs/research/astra-power-chain-joule-metric-01/result.json":
        "d1257d9c6cb6a65fc07a463608c7148d312190a38ea7ac6c79bb7290556fb4b4",
    "outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz":
        "a4ecf2a92ec7ee41b9a1496fc8765a2150bee945fd9d1807300d4e2389e288d4",
    "tools/research/prepare_astra_post_top_to_lower_transport_lift.py":
        "a4386848b1b5f9e7b9c0835435b807015d16178dfe4b16f424199de34fd2c3c1",
    "outputs/research/astra-post-top-to-lower-transport-lift-01/result.json":
        "a644574cba19bbc00af35cd879e350ef944c029ab07da1f1272ec1aabba86e96",
    "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz":
        "b169d79ce8fc61c141efa572ce2aa2c4905fc5efa3905cac139dd07efc165538",
    "outputs/research/astra-post-top-to-lower-transport-lift-review-01/independent-review.json":
        "6b17350b7f5d4b79d406ae6da9e9767603a5b510776e2dcd106c9cf851045c4e",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz":
        "9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def relative(actual: np.ndarray, expected: np.ndarray) -> float:
    return float(
        np.linalg.norm(actual - expected)
        / max(float(np.linalg.norm(expected)), np.finfo(float).tiny)
    )


def centered_rt0_fields(
    vertices_m: np.ndarray,
    cells: np.ndarray,
    local_columns: np.ndarray,
    local_signs: np.ndarray,
    face_flux_basis: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    tetrahedra = vertices_m[cells]
    centers = tetrahedra.mean(axis=1)
    volumes = np.abs(np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])) / 6.0
    variance = np.sum((tetrahedra - centers[:, None, :]) ** 2, axis=(1, 2)) / 20.0
    signed_flux = local_signs[:, :, None] * face_flux_basis[local_columns]
    center_current = np.einsum(
        "cim,cid->cmd", signed_flux, centers[:, None, :] - tetrahedra
    ) / (3.0 * volumes[:, None, None])
    radial = signed_flux.sum(axis=1) / (3.0 * volumes[:, None])
    return center_current, radial, volumes, variance


def body_gram(
    center_a: np.ndarray,
    radial_a: np.ndarray,
    center_b: np.ndarray,
    radial_b: np.ndarray,
    volumes: np.ndarray,
    variance: np.ndarray,
    conductivity: float,
) -> np.ndarray:
    return (
        np.einsum("c,cid,cjd->ij", volumes / conductivity, center_a, center_b)
        + np.einsum(
            "c,ci,cj->ij", volumes * variance / conductivity, radial_a, radial_b
        )
    )


def explicit_single_owned_energy(
    lateral_coefficients: np.ndarray,
    vertical_coefficients: np.ndarray,
    outgoing_edge: np.ndarray,
    incoming_edge: np.ndarray,
    lateral_left_center: np.ndarray,
    lateral_left_radial: np.ndarray,
    lateral_right_center: np.ndarray,
    lateral_right_radial: np.ndarray,
    lateral_bridge_center: np.ndarray,
    lateral_bridge_radial: np.ndarray,
    vertical_center: np.ndarray,
    vertical_radial: np.ndarray,
    post_volumes: np.ndarray,
    post_variance: np.ndarray,
    bridge_volumes: np.ndarray,
    bridge_variance: np.ndarray,
    conductivity: float,
) -> float:
    energy = 0.0
    for post in range(len(vertical_coefficients)):
        center = vertical_coefficients[post] * vertical_center
        radial = vertical_coefficients[post] * vertical_radial
        outgoing = int(outgoing_edge[post])
        incoming = int(incoming_edge[post])
        if outgoing >= 0:
            center = center + np.einsum(
                "cmd,m->cd", lateral_left_center, lateral_coefficients[outgoing]
            )
            radial = radial + lateral_left_radial @ lateral_coefficients[outgoing]
        if incoming >= 0:
            center = center + np.einsum(
                "cmd,m->cd", lateral_right_center, lateral_coefficients[incoming]
            )
            radial = radial + lateral_right_radial @ lateral_coefficients[incoming]
        energy += float(
            np.sum(
                post_volumes
                / conductivity
                * (
                    np.sum(np.abs(center) ** 2, axis=1)
                    + post_variance * np.abs(radial) ** 2
                )
            )
        )
    for edge_coefficients in lateral_coefficients:
        center = np.einsum("cmd,m->cd", lateral_bridge_center, edge_coefficients)
        radial = lateral_bridge_radial @ edge_coefficients
        energy += float(
            np.sum(
                bridge_volumes
                / conductivity
                * (
                    np.sum(np.abs(center) ** 2, axis=1)
                    + bridge_variance * np.abs(radial) ** 2
                )
            )
        )
    return energy


def run(output: Path) -> None:
    started = monotonic()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative_path, expected in PINS.items():
            actual = digest(ROOT / relative_path)
            if actual != expected:
                raise AssertionError(f"pin mismatch {relative_path}: {actual}")

        from scipy.linalg import eigvalsh
        from scipy.sparse import bmat, coo_matrix, csr_matrix, diags

        lateral_path = ROOT / "outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz"
        with np.load(lateral_path, allow_pickle=False) as data:
            lateral = {name: data[name].copy() for name in data.files}
        lateral_shape = tuple(map(int, lateral["resistance_shape"]))
        lateral_metric = csr_matrix(
            (
                lateral["resistance_data_ohm"],
                lateral["resistance_col"],
                lateral["resistance_row_ptr"],
            ),
            shape=lateral_shape,
        )
        if lateral_shape != (4665, 4665):
            raise AssertionError("unexpected lateral coordinate shape")

        with np.load(
            ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz",
            allow_pickle=False,
        ) as data:
            vertices_m = data["vertices_local_um"] * 1.0e-6
            cells = data["cells"].copy()
            cell_body = data["cell_body"].copy()
            face_vertices = data["face_vertices"].copy()
            boundary_face_ids = data["boundary_face_ids"].copy()
            first_owner_cell = data["first_owner_cell"].copy()
            first_owner_local_face = data["first_owner_local_face"].copy()
        with np.load(
            ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz",
            allow_pickle=False,
        ) as data:
            local_columns = data["local_rt0_face_columns"].copy()
            local_signs = data["local_rt0_face_signs"].copy()
            conductivity = float(data["conductivity_s_m"][0])
        with np.load(
            ROOT / "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz",
            allow_pickle=False,
        ) as data:
            lateral_face_flux = data["face_flux_basis"].copy()
            left_top_faces = data["electrode_left_ids"].copy()
            right_top_faces = data["electrode_right_ids"].copy()
        with np.load(
            ROOT / "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz",
            allow_pickle=False,
        ) as data:
            vertical_face_flux = data["face_flux_basis"][:, 0].copy()
            vertical_post_cells = data["post_cell_ids"].copy()
            vertical_top_faces = data["top_patch_face_ids"].copy()
            vertical_lower_faces = data["lower_contact_face_ids"].copy()
            vertical_center = data["cell_current_center_per_m2"].copy()
            vertical_radial = data["cell_rt0_radial_coefficient_per_m3"].copy()
            vertical_volumes = data["cell_volume_m3"].copy()
            vertical_variance = data["cell_radial_variance_m2"].copy()
            saved_vertical_energy = float(data["post_joule_energy_ohm"][0])

        lateral_center, lateral_radial, volumes, variance = centered_rt0_fields(
            vertices_m, cells, local_columns, local_signs, lateral_face_flux
        )
        bodies = [np.flatnonzero(cell_body == body) for body in range(3)]
        if [len(item) for item in bodies] != [2604, 2604, 96]:
            raise AssertionError("unexpected joint body partition")
        left_cells, right_cells, bridge_cells = bodies
        if not np.array_equal(vertical_post_cells, left_cells):
            raise AssertionError("vertical lift is not stored on canonical left-post cells")
        if relative(vertical_volumes, volumes[left_cells]) > 2.0e-14:
            raise AssertionError("vertical and joint post volumes differ")
        if relative(vertical_variance, variance[left_cells]) > 2.0e-14:
            raise AssertionError("vertical and joint post radial variances differ")

        left_center = lateral_center[left_cells]
        right_center = lateral_center[right_cells]
        bridge_center = lateral_center[bridge_cells]
        left_radial = lateral_radial[left_cells]
        right_radial = lateral_radial[right_cells]
        bridge_radial = lateral_radial[bridge_cells]
        post_volumes = volumes[left_cells]
        post_variance = variance[left_cells]
        bridge_volumes = volumes[bridge_cells]
        bridge_variance = variance[bridge_cells]

        vertical_center_column = vertical_center[:, None, :]
        vertical_radial_column = vertical_radial[:, None]
        vertical_energy = float(
            body_gram(
                vertical_center_column,
                vertical_radial_column,
                vertical_center_column,
                vertical_radial_column,
                post_volumes,
                post_variance,
                conductivity,
            )[0, 0]
        )
        vertical_energy_relative = abs(vertical_energy - saved_vertical_energy) / saved_vertical_energy
        vertical_left_cross = body_gram(
            left_center,
            left_radial,
            vertical_center_column,
            vertical_radial_column,
            post_volumes,
            post_variance,
            conductivity,
        )[:, 0]
        vertical_right_cross = body_gram(
            right_center,
            right_radial,
            vertical_center_column,
            vertical_radial_column,
            post_volumes,
            post_variance,
            conductivity,
        )[:, 0]

        edge_left_post = lateral["edge_left_post"].astype(np.int64)
        edge_right_post = lateral["edge_right_post"].astype(np.int64)
        outgoing_edge = lateral["post_left_outgoing_edge"].astype(np.int64)
        incoming_edge = lateral["post_right_incoming_edge"].astype(np.int64)
        post_count = len(lateral["post_pins"])
        edge_count = len(lateral["trace_ids"])
        if (post_count, edge_count) != (978, 933):
            raise AssertionError("unexpected selected power graph")

        cross_rows: list[int] = []
        cross_cols: list[int] = []
        cross_data: list[float] = []
        for edge in range(edge_count):
            for mode in range(5):
                cross_rows.extend((5 * edge + mode, 5 * edge + mode))
                cross_cols.extend((int(edge_left_post[edge]), int(edge_right_post[edge])))
                cross_data.extend((float(vertical_left_cross[mode]), float(vertical_right_cross[mode])))
        lateral_vertical_cross = coo_matrix(
            (cross_data, (cross_rows, cross_cols)), shape=(lateral_shape[0], post_count)
        ).tocsr()
        metric = bmat(
            [
                [lateral_metric, lateral_vertical_cross],
                [lateral_vertical_cross.T, diags(np.full(post_count, vertical_energy))],
            ],
            format="csr",
        )
        metric.sum_duplicates()

        owner_sign = local_signs[first_owner_cell, first_owner_local_face]
        if not np.array_equal(owner_sign, np.ones(len(owner_sign), dtype=owner_sign.dtype)):
            raise AssertionError("global face orientation is not first-owner outward")
        left_top_flux = np.sum(lateral_face_flux[left_top_faces], axis=0)
        right_top_flux = np.sum(lateral_face_flux[right_top_faces], axis=0)
        if np.max(np.abs(left_top_flux - np.array([-1.0, 0, 0, 0, 0]))) > 2.0e-14:
            raise AssertionError("unexpected left TOP lateral flux")
        if np.max(np.abs(right_top_flux - np.array([1.0, 0, 0, 0, 0]))) > 2.0e-14:
            raise AssertionError("unexpected right TOP lateral flux")

        face_z = vertices_m[face_vertices, 2]
        face_body = cell_body[first_owner_cell]
        planar = np.ptp(face_z, axis=1) < 2.0e-18
        lower_left_faces = boundary_face_ids[
            planar[boundary_face_ids]
            & (np.abs(np.mean(face_z[boundary_face_ids], axis=1) - 75.0e-6) < 2.0e-18)
            & (face_body[boundary_face_ids] == 0)
        ]
        lower_right_faces = boundary_face_ids[
            planar[boundary_face_ids]
            & (np.abs(np.mean(face_z[boundary_face_ids], axis=1) - 75.0e-6) < 2.0e-18)
            & (face_body[boundary_face_ids] == 1)
        ]
        if (len(lower_left_faces), len(lower_right_faces)) != (288, 288):
            raise AssertionError("unexpected lower post boundary partition")
        lower_left_flux = np.sum(lateral_face_flux[lower_left_faces], axis=0)
        lower_right_flux = np.sum(lateral_face_flux[lower_right_faces], axis=0)
        if max(np.max(np.abs(lower_left_flux)), np.max(np.abs(lower_right_flux))) > 2.0e-14:
            raise AssertionError("lateral lift acquired lower exterior flux")
        vertical_top_flux = float(np.sum(vertical_face_flux[vertical_top_faces]))
        vertical_lower_flux = float(np.sum(vertical_face_flux[vertical_lower_faces]))
        if abs(vertical_top_flux + 1.0) > 3.0e-14 or abs(vertical_lower_flux - 1.0) > 3.0e-14:
            raise AssertionError("vertical lift terminal flux changed")

        coordinate_count = lateral_shape[0] + post_count
        top_row: list[int] = []
        top_col: list[int] = []
        top_data: list[float] = []
        lower_row: list[int] = []
        lower_col: list[int] = []
        lower_data: list[float] = []
        for edge in range(edge_count):
            for mode in range(5):
                column = 5 * edge + mode
                for row, value in (
                    (int(edge_left_post[edge]), float(left_top_flux[mode])),
                    (int(edge_right_post[edge]), float(right_top_flux[mode])),
                ):
                    if value != 0.0:
                        top_row.append(row)
                        top_col.append(column)
                        top_data.append(value)
                for row, value in (
                    (int(edge_left_post[edge]), float(lower_left_flux[mode])),
                    (int(edge_right_post[edge]), float(lower_right_flux[mode])),
                ):
                    if value != 0.0:
                        lower_row.append(row)
                        lower_col.append(column)
                        lower_data.append(value)
        for post in range(post_count):
            column = lateral_shape[0] + post
            top_row.append(post)
            top_col.append(column)
            top_data.append(vertical_top_flux)
            lower_row.append(post)
            lower_col.append(column)
            lower_data.append(vertical_lower_flux)
        top_flux_map = coo_matrix(
            (top_data, (top_row, top_col)), shape=(post_count, coordinate_count)
        ).tocsr()
        lower_flux_map = coo_matrix(
            (lower_data, (lower_row, lower_col)), shape=(post_count, coordinate_count)
        ).tocsr()
        column_kcl = np.asarray(top_flux_map.sum(axis=0) + lower_flux_map.sum(axis=0)).ravel()
        column_kcl_max = float(np.max(np.abs(column_kcl)))

        symmetry_absolute = float(np.max(np.abs((metric - metric.T).data))) if (metric - metric.T).nnz else 0.0
        diagonal = metric.diagonal()
        if np.any(diagonal <= 0.0):
            raise AssertionError("combined metric has a nonpositive diagonal")
        component_ids = lateral["edge_component"].astype(np.int64)
        component_count = int(component_ids.max()) + 1
        post_component = np.full(post_count, -1, dtype=np.int64)
        for edge, component in enumerate(component_ids):
            for post in (int(edge_left_post[edge]), int(edge_right_post[edge])):
                if post_component[post] >= 0 and post_component[post] != component:
                    raise AssertionError("post belongs to multiple graph components")
                post_component[post] = component
        if np.any(post_component < 0):
            raise AssertionError("unowned post in selected power graph")
        component_min_eigenvalues: list[float] = []
        component_cholesky_min: list[float] = []
        for component in range(component_count):
            edges = np.flatnonzero(component_ids == component)
            posts = np.flatnonzero(post_component == component)
            coordinates = np.concatenate(
                ([5 * edge + mode for edge in edges for mode in range(5)], lateral_shape[0] + posts)
            ).astype(np.int64)
            block = metric[coordinates][:, coordinates].toarray()
            scale = np.sqrt(np.diag(block))
            normalized = block / scale[:, None] / scale[None, :]
            component_min_eigenvalues.append(float(eigvalsh(normalized, subset_by_index=(0, 0))[0]))
            component_cholesky_min.append(float(np.min(np.diag(np.linalg.cholesky(block)))))

        rng = np.random.default_rng(20260909)
        random_coordinates = rng.standard_normal((3, coordinate_count)) + 1j * rng.standard_normal(
            (3, coordinate_count)
        )
        sparse_energy: list[float] = []
        explicit_energy: list[float] = []
        for coordinates in random_coordinates:
            sparse_energy.append(float(np.real(np.vdot(coordinates, metric @ coordinates))))
            explicit_energy.append(
                explicit_single_owned_energy(
                    coordinates[: lateral_shape[0]].reshape(edge_count, 5),
                    coordinates[lateral_shape[0] :],
                    outgoing_edge,
                    incoming_edge,
                    left_center,
                    left_radial,
                    right_center,
                    right_radial,
                    bridge_center,
                    bridge_radial,
                    vertical_center,
                    vertical_radial,
                    post_volumes,
                    post_variance,
                    bridge_volumes,
                    bridge_variance,
                    conductivity,
                )
            )
        sparse_energy_array = np.asarray(sparse_energy)
        explicit_energy_array = np.asarray(explicit_energy)
        random_energy_relative = relative(sparse_energy_array, explicit_energy_array)

        checks = {
            "post_count": post_count,
            "bridge_count": edge_count,
            "component_count": component_count,
            "lateral_coordinate_count": lateral_shape[0],
            "vertical_coordinate_count": post_count,
            "combined_coordinate_count": coordinate_count,
            "combined_metric_nnz": int(metric.nnz),
            "vertical_post_energy_ohm": vertical_energy,
            "vertical_saved_energy_relative": vertical_energy_relative,
            "vertical_left_cross_frobenius_ohm": float(np.linalg.norm(vertical_left_cross)),
            "vertical_right_cross_frobenius_ohm": float(np.linalg.norm(vertical_right_cross)),
            "vertical_left_cross_relative_to_vertical_energy": float(
                np.linalg.norm(vertical_left_cross) / vertical_energy
            ),
            "vertical_right_cross_relative_to_vertical_energy": float(
                np.linalg.norm(vertical_right_cross) / vertical_energy
            ),
            "global_sparse_symmetry_absolute_ohm": symmetry_absolute,
            "global_component_normalized_min_eigenvalue": min(component_min_eigenvalues),
            "global_component_cholesky_min_diagonal_sqrt_ohm": min(component_cholesky_min),
            "random_complex_single_owned_energy_relative": random_energy_relative,
            "top_flux_map_nnz": int(top_flux_map.nnz),
            "lower_flux_map_nnz": int(lower_flux_map.nnz),
            "lateral_left_top_outward_flux_a": left_top_flux.tolist(),
            "lateral_right_top_outward_flux_a": right_top_flux.tolist(),
            "lateral_lower_outward_flux_max_a": float(
                max(np.max(np.abs(lower_left_flux)), np.max(np.abs(lower_right_flux)))
            ),
            "vertical_top_outward_flux_a": vertical_top_flux,
            "vertical_lower_outward_flux_a": vertical_lower_flux,
            "columnwise_top_plus_lower_KCL_max_a": column_kcl_max,
        }
        if vertical_energy_relative > 3.0e-13:
            raise AssertionError("vertical energy does not match accepted post lift")
        if symmetry_absolute > 1.0e-16:
            raise AssertionError("combined metric lost symmetry")
        if checks["global_component_normalized_min_eigenvalue"] <= 0.0:
            raise AssertionError("combined component metric is not positive definite")
        if random_energy_relative > 3.0e-13:
            raise AssertionError("single-owned body energy does not match sparse metric")
        if column_kcl_max > 3.0e-14:
            raise AssertionError("TOP/lower terminal maps violate columnwise KCL")

        artifact = output / "power-chain-vertical-joule-metric.npz"
        coordinate_kind = np.concatenate(
            (np.full(lateral_shape[0], "lateral_interface_lift"), np.full(post_count, "vertical_top_to_r20"))
        )
        np.savez_compressed(
            artifact,
            resistance_shape=np.asarray(metric.shape, dtype=np.int64),
            resistance_row_ptr=metric.indptr,
            resistance_col=metric.indices,
            resistance_data_ohm=metric.data,
            coordinate_kind=coordinate_kind,
            coordinate_order=np.asarray(["933_edges_x_5_modes", "978_posts_x_1_vertical"]),
            lateral_trace_ids=lateral["trace_ids"],
            lateral_mode_names=lateral["mode_names"],
            edge_left_post=edge_left_post,
            edge_right_post=edge_right_post,
            edge_component=component_ids,
            post_pins=lateral["post_pins"],
            post_xy_um=lateral["post_xy_um"],
            post_component=post_component,
            post_outgoing_edge=outgoing_edge,
            post_incoming_edge=incoming_edge,
            local_vertical_post_energy_ohm=np.asarray([vertical_energy]),
            local_vertical_left_lateral_cross_ohm=vertical_left_cross,
            local_vertical_right_lateral_cross_ohm=vertical_right_cross,
            top_outward_flux_shape=np.asarray(top_flux_map.shape, dtype=np.int64),
            top_outward_flux_row_ptr=top_flux_map.indptr,
            top_outward_flux_col=top_flux_map.indices,
            top_outward_flux_data_a=top_flux_map.data,
            lower_outward_flux_shape=np.asarray(lower_flux_map.shape, dtype=np.int64),
            lower_outward_flux_row_ptr=lower_flux_map.indptr,
            lower_outward_flux_col=lower_flux_map.indices,
            lower_outward_flux_data_a=lower_flux_map.data,
            columnwise_top_plus_lower_kcl_a=column_kcl,
            component_normalized_min_eigenvalue=np.asarray(component_min_eigenvalues),
            component_cholesky_min_diagonal_sqrt_ohm=np.asarray(component_cholesky_min),
            random_complex_coordinates=random_coordinates,
            random_sparse_energy_ohm=sparse_energy_array,
            random_explicit_single_owned_energy_ohm=explicit_energy_array,
        )
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "PASS_SELECTED_POWER_CHAIN_VERTICAL_LATERAL_JOULE_METRIC",
            "elapsed_s": monotonic() - started,
            "driver_sha256": digest(Path(__file__)),
            "artifact_sha256": digest(artifact),
            "pins": PINS,
            "checks": checks,
            "flux_convention": (
                "Stored TOP and LOWER maps are integrated outward current for each physical post. "
                "The lateral constant mode is -1 A at its left TOP pin and +1 A at its right TOP "
                "pin; the four zero-net modes have no terminal current. A vertical coordinate is "
                "-1 A at TOP and +1 A at its actual r20 lower contact."
            ),
            "scope": (
                "Static single-owned Joule metric and terminal-flux maps for 4,665 accepted lateral "
                "coordinates plus 978 actual P-post TOP-to-r20-lower coordinates. Every selected post "
                "and bridge is integrated once; all adjacent lateral/lateral and incident vertical/"
                "lateral post cross terms are retained. Zero flux on other faces belongs only to these "
                "basis columns. Complementary fine, boundary-charge/contact, circulation, ground-chain, "
                "lower-via/return and Green spaces remain available. The lower map is not grounded. This "
                "is not a port, field, Z, complete rail/return, board, or PowerSI accuracy claim."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "checks": checks}, allow_nan=False))
    except Exception:
        (output / "failure.json").write_text(
            json.dumps(
                {
                    "program": "SPD Decap PI Evaluator",
                    "version": "0.23.1",
                    "status": "STOP_SELECTED_POWER_CHAIN_VERTICAL_LATERAL_JOULE_METRIC",
                    "elapsed_s": monotonic() - started,
                    "driver_sha256": digest(Path(__file__)),
                    "pins": PINS,
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
