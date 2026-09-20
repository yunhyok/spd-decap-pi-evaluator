"""Strict saved-array review of the seven-coordinate source P-joint basis."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy.sparse import bmat, coo_matrix, csr_matrix, vstack


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-joint-seven-basis-extension-review-03"
SIGMA = 59.59e6

PINS = {
    "tools/research/prepare_astra_joint_seven_basis_extension.py": "1866378ba3e4355ad76a48eae06b4c2050ef71a4d84d88ccd0c4ae15cbe16109",
    "outputs/research/astra-joint-seven-basis-extension-01/result.json": "a13d8339234a5f1fbe816f22f13f19e6dbd0d928d307878bfb6b41720af83e4f",
    "outputs/research/astra-joint-seven-basis-extension-01/seven-basis.npz": "36a9ea47941403b655f68a75460345f11171fa11b3f4cee99f618ae9cd70780c",
    "outputs/research/astra-shared-interface-interior-lifts-04/result.json": "2df58fa459a0e016916b8c6f3e23c8c44cedd58d191b69111a2ad6688e131eb8",
    "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz": "9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92",
    "outputs/research/astra-post-top-to-lower-transport-lift-01/result.json": "a644574cba19bbc00af35cd879e350ef944c029ab07da1f1272ec1aabba86e96",
    "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz": "b169d79ce8fc61c141efa572ce2aa2c4905fc5efa3905cac139dd07efc165538",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def coordinate_key(points_m: np.ndarray) -> tuple[int, ...]:
    rows = np.rint(points_m * 1.0e15).astype(np.int64)
    rows = rows[np.lexsort((rows[:, 2], rows[:, 1], rows[:, 0]))]
    return tuple(rows.ravel())


def relative(actual: np.ndarray, expected: np.ndarray) -> float:
    scale = max(float(np.linalg.norm(expected)), np.finfo(float).tiny)
    return float(np.linalg.norm(actual - expected) / scale)


def main() -> None:
    started = monotonic()
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name

    with np.load(
        ROOT / "outputs/research/astra-joint-seven-basis-extension-01/seven-basis.npz",
        allow_pickle=False,
    ) as saved:
        seven = {key: saved[key] for key in saved.files}
    with np.load(
        ROOT / "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz",
        allow_pickle=False,
    ) as saved:
        old = saved["face_flux_basis"]
    with np.load(
        ROOT / "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz",
        allow_pickle=False,
    ) as saved:
        vertical = saved["face_flux_basis"][:, 0]
        source_active = saved["active_face_ids"]
        source_cells = saved["post_cell_ids"]
        source_dual = np.r_[saved["divergence_dual"], saved["top_flux_dual"]]
        source_scale = float(saved["metric_scale_ohm"][0])
        source_energy = float(saved["post_joule_energy_ohm"][0])
    with np.load(
        ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz",
        allow_pickle=False,
    ) as saved:
        vertices = saved["vertices_local_um"] * 1.0e-6
        cells = saved["cells"]
        body = saved["cell_body"]
        faces = saved["face_vertices"]
        first_owner = saved["first_owner_cell"]
        internal = saved["internal_face_ids"]
        internal_owners = saved["internal_owner_cells"]
        boundary = saved["boundary_face_ids"]
    with np.load(
        ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz",
        allow_pickle=False,
    ) as saved:
        saved_r = csr_matrix(
            (
                saved["resistance_data_ohm"],
                saved["mass_col"],
                saved["mass_row_ptr"],
            ),
            shape=tuple(saved["mass_shape"]),
        )

    assert cells.shape == (5304, 4) and faces.shape == (12546, 3)
    left_cells = np.flatnonzero(body == 0)
    right_cells = np.flatnonzero(body == 1)
    assert np.array_equal(source_cells, left_cells)

    # Build incidence and physical resistance independently from mesh geometry.
    face_lookup = {tuple(face): i for i, face in enumerate(faces)}
    local_columns = np.empty((len(cells), 4), dtype=np.int64)
    local_signs = np.empty((len(cells), 4), dtype=np.int8)
    for cell_id, cell in enumerate(cells):
        for local_id in range(4):
            face_id = face_lookup[tuple(sorted(np.delete(cell, local_id)))]
            local_columns[cell_id, local_id] = face_id
            local_signs[cell_id, local_id] = 1 if first_owner[face_id] == cell_id else -1
    divergence = coo_matrix(
        (
            local_signs.ravel(),
            (np.repeat(np.arange(len(cells)), 4), local_columns.ravel()),
        ),
        shape=(len(cells), len(faces)),
    ).tocsr()

    tetrahedra = vertices[cells]
    signed_six_volume = np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])
    assert float(signed_six_volume.min()) > 0.0
    volumes = signed_six_volume / 6.0
    qa = (5.0 + 3.0 * np.sqrt(5.0)) / 20.0
    qb = (5.0 - np.sqrt(5.0)) / 20.0
    barycentric = np.full((4, 4), qb)
    np.fill_diagonal(barycentric, qa)
    points = np.einsum("qi,cid->cqd", barycentric, tetrahedra)
    basis = (
        points[:, :, None, :] - tetrahedra[:, None, :, :]
    ) / (3.0 * volumes[:, None, None, None])
    local_mass = np.einsum(
        "c,cqid,cqjd->cij", volumes / 4.0, basis, basis
    )
    signed_mass = (
        local_mass
        * local_signs[:, :, None]
        * local_signs[:, None, :]
        / SIGMA
    )
    row = np.broadcast_to(local_columns[:, :, None], signed_mass.shape).ravel()
    col = np.broadcast_to(local_columns[:, None, :], signed_mass.shape).ravel()
    resistance = coo_matrix(
        (signed_mass.ravel(), (row, col)), shape=saved_r.shape
    ).tocsr()
    resistance.sum_duplicates()
    resistance_relative = float(
        np.linalg.norm((resistance - saved_r).data)
        / np.linalg.norm(saved_r.data)
    )

    # Derive the rigid left-to-right post map from coordinates, not saved IDs.
    left_vertices = np.unique(cells[left_cells])
    right_vertices = np.unique(cells[right_cells])
    translation = vertices[right_vertices].mean(axis=0) - vertices[left_vertices].mean(axis=0)
    assert float(np.linalg.norm(translation[1:])) < 1.0e-15
    face_coordinate_lookup = {
        coordinate_key(vertices[face]): face_id for face_id, face in enumerate(faces)
    }
    face_map = np.full(len(faces), -1, dtype=np.int64)
    left_faces = np.unique(local_columns[left_cells])
    for face_id in left_faces:
        face_map[face_id] = face_coordinate_lookup.get(
            coordinate_key(vertices[faces[face_id]] + translation), -1
        )
    assert np.all(face_map[left_faces] >= 0)
    assert len(np.unique(face_map[left_faces])) == len(left_faces)

    cell_coordinate_lookup = {
        coordinate_key(vertices[cell]): cell_id for cell_id, cell in enumerate(cells)
    }
    right_cell_map = np.asarray(
        [
            cell_coordinate_lookup[
                coordinate_key(vertices[cells[cell_id]] + translation)
            ]
            for cell_id in left_cells
        ],
        dtype=np.int64,
    )
    assert np.array_equal(np.sort(right_cell_map), right_cells)

    right_vertical = np.zeros_like(vertical)
    nonzero_left = np.flatnonzero(vertical != 0.0)
    right_vertical[face_map[nonzero_left]] = vertical[nonzero_left]
    independently_rebuilt_raw = np.column_stack((old, vertical, right_vertical))

    # Derive physical terminal faces directly from the mesh.
    boundary_points = vertices[faces[boundary]]
    boundary_owner_body = body[first_owner[boundary]]
    top_groups: list[np.ndarray] = []
    lower_groups: list[np.ndarray] = []
    for body_id in (0, 1):
        body_vertices = np.unique(cells[np.flatnonzero(body == body_id)])
        xy_center = (
            vertices[body_vertices, :2].min(axis=0)
            + vertices[body_vertices, :2].max(axis=0)
        ) / 2.0
        top = boundary[
            (boundary_owner_body == body_id)
            & np.all(np.abs(boundary_points[:, :, 2]) < 1.0e-15, axis=1)
        ]
        bottom_mask = (
            (boundary_owner_body == body_id)
            & np.all(
                np.abs(boundary_points[:, :, 2] - 75.0e-6) < 1.0e-15,
                axis=1,
            )
        )
        bottom = boundary[bottom_mask]
        radii = np.linalg.norm(vertices[faces[bottom], :2] - xy_center, axis=2)
        lower = bottom[np.max(radii, axis=1) <= 20.0e-6 + 1.0e-15]
        assert len(top) == 484 and len(bottom) == 288 and len(lower) == 96
        top_groups.append(top)
        lower_groups.append(lower)
    terminal_faces = [top_groups[0], top_groups[1], lower_groups[0], lower_groups[1]]
    terminal_raw = np.vstack(
        [independently_rebuilt_raw[group].sum(axis=0) for group in terminal_faces]
    )
    expected_terminal_raw = np.zeros((4, 7))
    expected_terminal_raw[0, 0] = -1.0
    expected_terminal_raw[1, 0] = 1.0
    expected_terminal_raw[0, 5] = -1.0
    expected_terminal_raw[2, 5] = 1.0
    expected_terminal_raw[1, 6] = -1.0
    expected_terminal_raw[3, 6] = 1.0
    terminal_raw_absolute = float(np.max(np.abs(terminal_raw - expected_terminal_raw)))
    terminal_kcl_absolute = float(np.max(np.abs(terminal_raw.sum(axis=0))))
    all_terminal_faces = np.unique(np.concatenate(terminal_faces))
    other_boundary = np.setdiff1d(boundary, all_terminal_faces)
    other_boundary_vertical_absolute = float(
        np.max(np.abs(independently_rebuilt_raw[other_boundary, 5:]))
    )

    # Reconstruct both post KKT systems, including the full R_ib contribution.
    left_internal = internal[np.all(body[internal_owners] == 0, axis=1)]
    left_active = np.unique(np.r_[left_internal, top_groups[0], lower_groups[0]])
    assert np.array_equal(left_active, source_active)
    right_active = face_map[left_active]
    assert np.all(right_active >= 0) and len(np.unique(right_active)) == len(right_active)

    def kkt_residual(
        cell_ids: np.ndarray,
        active_faces: np.ndarray,
        top_faces: np.ndarray,
        current: np.ndarray,
    ) -> tuple[float, float]:
        top_constraint = coo_matrix(
            (
                np.ones(len(top_faces)),
                (np.zeros(len(top_faces), dtype=np.int64), np.searchsorted(active_faces, top_faces)),
            ),
            shape=(1, len(active_faces)),
        ).tocsr()
        constraints = vstack(
            (divergence[cell_ids][:, active_faces], top_constraint), format="csr"
        )
        primal = resistance[active_faces][:, active_faces] @ current[active_faces] / source_scale
        stationarity = primal + constraints.T @ source_dual
        target = np.r_[np.zeros(len(cell_ids)), -1.0]
        constrained = constraints @ current[active_faces]
        stationarity_relative = float(
            np.linalg.norm(stationarity)
            / max(np.linalg.norm(primal), np.linalg.norm(constraints.T @ source_dual))
        )
        constraint_absolute = float(np.max(np.abs(constrained - target)))
        return stationarity_relative, constraint_absolute

    left_kkt, left_constraint = kkt_residual(
        left_cells, left_active, top_groups[0], vertical
    )
    right_kkt, right_constraint = kkt_residual(
        right_cell_map, right_active, top_groups[1], right_vertical
    )

    raw_gram = independently_rebuilt_raw.T @ (resistance @ independently_rebuilt_raw)
    old_gram = raw_gram[:5, :5]
    projection = np.linalg.solve(old_gram, raw_gram[:5, 5:])
    projected = independently_rebuilt_raw[:, 5:] - old @ projection
    projected_gram = projected.T @ (resistance @ projected)
    chol = np.linalg.cholesky((projected_gram + projected_gram.T) / 2.0)
    new_transform = np.linalg.inv(chol.T)
    independent_transform = np.eye(7)
    independent_transform[:5, 5:] = -projection @ new_transform
    independent_transform[5:, 5:] = new_transform
    independently_rebuilt_final = independently_rebuilt_raw @ independent_transform
    final_gram = independently_rebuilt_final.T @ (
        resistance @ independently_rebuilt_final
    )
    terminal_final = terminal_raw @ independent_transform

    # Independent exact degree-two cell quadrature of a complex random field.
    random = np.random.default_rng(20260909)
    coefficients = random.normal(size=7) + 1j * random.normal(size=7)
    fine_flux = independently_rebuilt_final @ coefficients
    local_flux = local_signs * fine_flux[local_columns]
    current_at_points = np.einsum("ci,cqid->cqd", local_flux, basis)
    quadrature_energy = float(
        np.real(
            np.einsum(
                "c,cqd,cqd->",
                volumes / (4.0 * SIGMA),
                current_at_points.conj(),
                current_at_points,
            )
        )
    )
    reduced_energy = float(np.real(coefficients.conj() @ final_gram @ coefficients))
    random_energy_relative = abs(quadrature_energy - reduced_energy) / reduced_energy

    checks = {
        "independent_resistance_relative": resistance_relative,
        "translation_m": translation.tolist(),
        "mapped_left_face_count": int(len(left_faces)),
        "mapped_left_cell_count": int(len(left_cells)),
        "saved_raw_basis_max_absolute": float(
            np.max(np.abs(independently_rebuilt_raw - seven["raw_face_flux_basis"]))
        ),
        "old_five_unchanged_max_absolute": float(
            np.max(np.abs(seven["raw_face_flux_basis"][:, :5] - old))
        ),
        "terminal_raw_max_absolute": terminal_raw_absolute,
        "terminal_column_kcl_max_a": terminal_kcl_absolute,
        "other_boundary_vertical_max_a": other_boundary_vertical_absolute,
        "raw_divergence_max_a": float(
            np.max(np.abs(divergence @ independently_rebuilt_raw))
        ),
        "left_kkt_stationarity_relative": left_kkt,
        "right_kkt_stationarity_relative": right_kkt,
        "left_constraint_max_absolute": left_constraint,
        "right_constraint_max_absolute": right_constraint,
        "left_energy_relative": abs(raw_gram[5, 5] - source_energy) / source_energy,
        "right_energy_relative": abs(raw_gram[6, 6] - source_energy) / source_energy,
        "saved_transform_max_absolute": float(
            np.max(np.abs(independent_transform - seven["raw_to_final_transform"]))
        ),
        "saved_final_basis_max_absolute": float(
            np.max(
                np.abs(
                    independently_rebuilt_final - seven["final_face_flux_basis"]
                )
            )
        ),
        "old_new_orthogonality_max_ohm": float(np.max(np.abs(final_gram[:5, 5:]))),
        "new_identity_max_absolute": float(
            np.max(np.abs(final_gram[5:, 5:] - np.eye(2)))
        ),
        "final_gram_min_eigenvalue_ohm": float(
            np.linalg.eigvalsh((final_gram + final_gram.T) / 2.0).min()
        ),
        "terminal_final_saved_max_absolute": float(
            np.max(np.abs(terminal_final - seven["terminal_flux_final_a"]))
        ),
        "random_complex_cell_quadrature_energy_relative": random_energy_relative,
    }

    gates = {
        "independent_resistance": checks["independent_resistance_relative"] < 2.0e-12,
        "rigid_map_complete": checks["mapped_left_face_count"] > 0
        and checks["mapped_left_cell_count"] == 2604,
        "saved_raw_basis": checks["saved_raw_basis_max_absolute"] < 2.0e-13,
        "old_five_exact": checks["old_five_unchanged_max_absolute"] == 0.0,
        "raw_terminal_flux": checks["terminal_raw_max_absolute"] < 5.0e-13,
        "raw_terminal_kcl": checks["terminal_column_kcl_max_a"] < 5.0e-13,
        "other_boundary_zero_for_vertical_columns": checks[
            "other_boundary_vertical_max_a"
        ]
        < 5.0e-13,
        "raw_divergence": checks["raw_divergence_max_a"] < 5.0e-13,
        "left_full_kkt": checks["left_kkt_stationarity_relative"] < 2.0e-12
        and checks["left_constraint_max_absolute"] < 5.0e-13,
        "right_full_kkt": checks["right_kkt_stationarity_relative"] < 2.0e-12
        and checks["right_constraint_max_absolute"] < 5.0e-13,
        "vertical_energy": max(
            checks["left_energy_relative"], checks["right_energy_relative"]
        )
        < 2.0e-12,
        "saved_transform": checks["saved_transform_max_absolute"] < 2.0e-13,
        "saved_final_basis": checks["saved_final_basis_max_absolute"] < 2.0e-13,
        "new_R_orthonormality": checks["old_new_orthogonality_max_ohm"] < 2.0e-12
        and checks["new_identity_max_absolute"] < 2.0e-12,
        "final_spd": checks["final_gram_min_eigenvalue_ohm"] > 0.0,
        "terminal_final_reconstruction": checks[
            "terminal_final_saved_max_absolute"
        ]
        < 2.0e-13,
        "independent_complex_cell_energy": checks[
            "random_complex_cell_quadrature_energy_relative"
        ]
        < 2.0e-12,
    }
    gates = {key: bool(value) for key, value in gates.items()}
    assert all(gates.values()), {key: value for key, value in gates.items() if not value}

    OUT.mkdir(parents=False, exist_ok=False)
    artifact = OUT / "review-arrays.npz"
    np.savez_compressed(
        artifact,
        independently_rebuilt_raw=independently_rebuilt_raw,
        independently_rebuilt_final=independently_rebuilt_final,
        independent_transform=independent_transform,
        independently_rebuilt_raw_gram_ohm=raw_gram,
        independently_rebuilt_final_gram_ohm=final_gram,
        independently_rebuilt_terminal_raw_a=terminal_raw,
        independently_rebuilt_terminal_final_a=terminal_final,
        left_to_right_face_map=face_map,
        left_to_right_cell_map=right_cell_map,
    )
    receipt = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE",
        "pins": PINS,
        "checks": checks,
        "gates": gates,
        "elapsed_s": monotonic() - started,
        "reviewer_sha256": digest(Path(__file__)),
        "artifact_sha256": digest(artifact),
        "supersedes": (
            "astra-joint-seven-basis-extension-review-01, whose saved-matrix "
            "checks did not independently rebuild the translated vertical currents, "
            "and incomplete review-02, whose JSON serialization stopped after the "
            "numerical gates"
        ),
        "scope": (
            "Seven basis columns on one actual two-post/one-bridge P joint only. "
            "Zero other-boundary flux is the definition of the two raw vertical "
            "basis columns. Complementary fine/exterior/circulation/contact-charge "
            "spaces remain. No Green, field, port, return, full-rail, or board claim."
        ),
    }
    (OUT / "independent-review.json").write_text(
        json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
