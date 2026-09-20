"""SPD Decap PI Evaluator v0.23.1: selected-G post to L02-sheet binding.

Build one exact RT0 minimum-Joule transport coordinate on the source-model
TOP-pad plus via-stem volume and instance it at all 978 selected G pads.  The
existing L02 sheet keeps sole ownership of the complete L02 copper thickness,
including each r30 pad.  Each new coordinate terminates at the exact r20,
z=55 stem face and is coupled only by aggregate current to the already saved
L02 sheet contact ordinal.

This is a sparse static assembly input.  The aggregate contact assumes the
existing contracted sheet contact is equipotential through the 20 um L02
thickness.  It is not a conforming 3-D trace map, a Green operator, or a board
solution.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SIGMA_S_M = 59.59e6
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/prepare_astra_conforming_power_joint.py":
        "ec1485a02019bbd04cd17a084c17a0ebbca683fb25cdec63361817ff69b8eb8b",
    "tools/research/qualify_astra_conforming_power_joint_sparse_current.py":
        "dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e",
    "outputs/research/astra-selected-g-l02-junction-05/result.json":
        "60647a1f511431b64dcd207c76b48ac4558ad8f917ff0db879969cc8ecb52049",
    "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz":
        "00afa62dabc05a825be3255cfc9888da628403d9ef783cba09ec6d88514bfe59",
    "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json":
        "5157020fd21232c25a236bf0a9892503a927218c5d164c1b0364372c7deb1bb0",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/post-interface-instances.npz":
        "d4c5a28a20267c0977a1893b411973632e37f7c8de5a96c412870b9e3ab67726",
    "outputs/research/astra-l02-pad-conductor-domain-01/l02-pad-drill-support-map.npz":
        "3774bb012f5769be006ca27f463d6dcb58d07c4c611ffbf8072bc80754d20a47",
    "outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz":
        "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9",
    "outputs/research/astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz":
        "61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020",
    "outputs/research/astra-device-group-port-01/result.json":
        "d1704635c305e8c35a6f426f76d42bf18f7f12a3cae948f82d08a2c1acffc383",
    "outputs/research/astra-device-group-port-01/group-port.npz":
        "bb5ac1fa7f8eec314bc26fa8c993755b2336dd38627b021fdef5cae2214d2a20",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not bool(condition):
        raise AssertionError(message)


def csr_payload(matrix, prefix: str) -> dict[str, np.ndarray]:
    matrix = matrix.tocsr()
    matrix.sum_duplicates()
    matrix.sort_indices()
    return {
        f"{prefix}_shape": np.asarray(matrix.shape, dtype=np.int64),
        f"{prefix}_row_ptr": matrix.indptr.astype(np.int64),
        f"{prefix}_col": matrix.indices.astype(np.int64),
        f"{prefix}_data": matrix.data,
    }


def cell_face_map(cells: np.ndarray, faces: np.ndarray, first_owner: np.ndarray):
    lookup = {tuple(map(int, face)): face_id for face_id, face in enumerate(faces)}
    face_ids = np.empty((len(cells), 4), dtype=np.int64)
    signs = np.empty((len(cells), 4), dtype=np.int8)
    for cell_id, cell in enumerate(cells):
        for local_id in range(4):
            key = tuple(sorted(map(int, np.delete(cell, local_id))))
            face_id = lookup[key]
            face_ids[cell_id, local_id] = face_id
            signs[cell_id, local_id] = 1 if first_owner[face_id] == cell_id else -1
    return face_ids, signs


def quadrature_mass(vertices_m: np.ndarray, cells: np.ndarray, volume_m3: np.ndarray):
    tetrahedra = vertices_m[cells]
    a = (5.0 + 3.0 * np.sqrt(5.0)) / 20.0
    b = (5.0 - np.sqrt(5.0)) / 20.0
    barycentric = np.full((4, 4), b)
    np.fill_diagonal(barycentric, a)
    points = np.einsum("qa,nad->nqd", barycentric, tetrahedra)
    basis = (points[:, :, None, :] - tetrahedra[:, None, :, :]) / (
        3.0 * volume_m3[:, None, None, None]
    )
    return volume_m3[:, None, None] * np.einsum(
        "nqid,nqjd->nij", basis, basis
    ) / 4.0


def run(output: Path) -> None:
    started = monotonic()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative, expected in PINS.items():
            require(digest(ROOT / relative) == expected, f"pin differs: {relative}")

        from scipy.sparse import bmat, coo_matrix, csr_matrix, diags, vstack
        from scipy.sparse.linalg import splu

        from prepare_astra_conforming_power_joint import topology
        from qualify_astra_conforming_power_joint_sparse_current import local_mass

        post_path = ROOT / "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz"
        with np.load(post_path, allow_pickle=False) as saved:
            full_vertices_um = saved["vertices_local_um"].copy()
            full_cells = saved["cells"].copy()
            full_zone = saved["cell_zone"].copy()

        # Zone 2 (z=55..75) is the L02 r30 pad prism.  The global sheet already
        # owns that copper, so retain only TOP pad (zone0) and via stem (zone1).
        selected_old_cells = np.flatnonzero(full_zone < 2)
        require(len(selected_old_cells) == 1764, "truncated post cell count differs")
        old_cells = full_cells[selected_old_cells]
        used_vertices = np.unique(old_cells)
        old_to_new = np.full(len(full_vertices_um), -1, dtype=np.int64)
        old_to_new[used_vertices] = np.arange(len(used_vertices), dtype=np.int64)
        vertices_um = full_vertices_um[used_vertices]
        cells = old_to_new[old_cells]
        mesh, connected, closure = topology(vertices_um, cells)
        faces = mesh["face_vertices"]
        first_owner = mesh["first_owner_cell"]
        internal = mesh["internal_face_ids"]
        boundary = mesh["boundary_face_ids"]
        require(connected == 1, "truncated post disconnected")

        vertices_m = vertices_um * 1.0e-6
        tetrahedra = vertices_m[cells]
        signed_det = np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])
        require(float(signed_det.min()) > 0.0, "nonpositive truncated tetrahedron")
        volume_m3 = signed_det / 6.0
        face_ids, face_signs = cell_face_map(cells, faces, first_owner)
        triangles = vertices_um[faces[boundary]]
        top_mask = np.max(np.abs(triangles[:, :, 2]), axis=1) < 1.0e-12
        lower_mask = np.max(np.abs(triangles[:, :, 2] - 55.0), axis=1) < 1.0e-12
        top_faces = boundary[top_mask]
        lower_faces = boundary[lower_mask]
        other_boundary = boundary[~(top_mask | lower_mask)]
        require(len(top_faces) == 492, "TOP patch face count differs")
        require(len(lower_faces) == 96, "z55 r20 contact face count differs")
        lower_xy = vertices_um[np.unique(faces[lower_faces]), :2]
        lower_radius = np.linalg.norm(lower_xy, axis=1)
        require(float(lower_radius.max()) <= 20.0 + 1.0e-11, "lower contact exceeds r20")

        local_blocks = np.asarray(
            [local_mass(vertices_m[cell], volume_m3[i]) for i, cell in enumerate(cells)]
        )
        independent_blocks = quadrature_mass(vertices_m, cells, volume_m3)
        mass_block_relative = float(
            np.max(np.abs(local_blocks - independent_blocks)) / np.max(np.abs(local_blocks))
        )
        require(mass_block_relative < 2.0e-12, "local mass quadrature differs")
        row = np.repeat(face_ids, 4, axis=1).ravel()
        column = np.tile(face_ids, (1, 4)).ravel()
        data = (
            face_signs[:, :, None] * local_blocks * face_signs[:, None, :]
        ).ravel()
        mass = coo_matrix((data, (row, column)), shape=(len(faces), len(faces))).tocsr()
        mass.sum_duplicates()
        mass.sort_indices()
        resistance = mass / SIGMA_S_M
        divergence = csr_matrix(
            (
                face_signs.ravel().astype(float),
                face_ids.ravel(),
                np.arange(0, 4 * len(cells) + 1, 4, dtype=np.int64),
            ),
            shape=(len(cells), len(faces)),
        )

        active = np.unique(np.r_[internal, top_faces, lower_faces])
        top_position = np.searchsorted(active, top_faces)
        top_constraint = coo_matrix(
            (
                np.ones(len(top_faces)),
                (np.zeros(len(top_faces), dtype=np.int64), top_position),
            ),
            shape=(1, len(active)),
        ).tocsr()
        constrained_d = divergence[:, active]
        constraints = vstack((constrained_d, top_constraint), format="csc")
        metric = resistance[active][:, active].tocsc()
        scale = float(np.median(metric.diagonal()))
        saddle = bmat(
            [[metric / scale, constraints.T], [constraints, None]], format="csc"
        )
        rhs = np.zeros(len(active) + len(cells) + 1)
        rhs[-1] = -1.0
        solution = splu(saddle).solve(rhs)
        flux = np.zeros(len(faces))
        flux[active] = solution[: len(active)]

        kkt_relative = float(np.linalg.norm(saddle @ solution - rhs) / np.linalg.norm(rhs))
        divergence_max = float(np.max(np.abs(divergence @ flux)))
        top_total = float(np.sum(flux[top_faces]))
        lower_total = float(np.sum(flux[lower_faces]))
        other_max = float(np.max(np.abs(flux[other_boundary])))
        sparse_energy = float(flux @ (resistance @ flux))

        local_flux = face_signs * flux[face_ids]
        centers_m = tetrahedra.mean(axis=1)
        center_current = np.einsum(
            "ci,cid->cd", local_flux, centers_m[:, None, :] - tetrahedra
        ) / (3.0 * volume_m3[:, None])
        radial = local_flux.sum(axis=1) / (3.0 * volume_m3)
        variance = np.sum(
            (tetrahedra - centers_m[:, None, :]) ** 2, axis=(1, 2)
        ) / 20.0
        independent_energy = float(
            np.sum(
                volume_m3 / SIGMA_S_M
                * (np.sum(center_current**2, axis=1) + variance * radial**2)
            )
        )
        energy_relative = abs(independent_energy - sparse_energy) / sparse_energy
        require(kkt_relative < 1.0e-11, "KKT residual")
        require(divergence_max < 1.0e-11, "cell divergence")
        require(abs(top_total + 1.0) < 1.0e-11, "TOP flux")
        require(abs(lower_total - 1.0) < 1.0e-11, "lower flux")
        require(other_max == 0.0, "other exterior flux")
        require(sparse_energy > 0.0 and energy_relative < 3.0e-13, "Joule energy")

        ledger = json.loads(
            (ROOT / "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json").read_text()
        )
        pad_rows = ledger["pad_records"]
        require(len(pad_rows) == 978, "selected G post count")
        with np.load(
            ROOT / "outputs/research/astra-selected-g-l02-single-owned-partition-02/post-interface-instances.npz",
            allow_pickle=False,
        ) as instances:
            instance_pin = instances["pin_id"].copy()
            instance_center_um = instances["center_um"].copy()
            instance_first_via = instances["source_first_via_id"].copy()
            instance_next_via = instances["next_via_id"].copy()
        require(len(instance_pin) == 978 and len(np.unique(instance_pin)) == 978, "G instance pins")
        ledger_by_pin = {row["pin_id"]: row for row in pad_rows}
        require(set(instance_pin.tolist()) == set(ledger_by_pin), "G pin sets differ")
        for index, pin in enumerate(instance_pin):
            row0 = ledger_by_pin[str(pin)]
            require(row0["first_via_id"] == instance_first_via[index], "first via differs")
            require(row0["next_via_id"] == instance_next_via[index], "next via differs")
            require(
                np.array_equal(np.asarray(row0["center_pm"], dtype=np.int64),
                               np.rint(instance_center_um[index] * 1.0e6).astype(np.int64)),
                "instance center differs",
            )

        with np.load(
            ROOT / "outputs/research/astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz",
            allow_pickle=False,
        ) as binding:
            via_ids = json.loads(binding["via_ids_json_utf8"].tobytes().decode())
            via_index = {via_id: i for i, via_id in enumerate(via_ids)}
            first_rows = np.asarray([via_index[str(v)] for v in instance_first_via], dtype=np.int64)
            next_rows = np.asarray([via_index[str(v)] for v in instance_next_via], dtype=np.int64)
            first_contact = binding["native_contact_ordinal"][first_rows].copy()
            next_contact = binding["native_contact_ordinal"][next_rows].copy()
            first_support = binding["native_drill_support_index"][first_rows].copy()
            next_support = binding["native_drill_support_index"][next_rows].copy()
            contact_support = binding["contact_support_index"].copy()
            binding_x_pm = binding["x_pm"][first_rows].copy()
            binding_y_pm = binding["y_pm"][first_rows].copy()
        require(np.array_equal(first_contact, next_contact), "first/next sheet contact differs")
        require(np.array_equal(first_support, next_support), "first/next drill support differs")
        require(len(np.unique(first_contact)) == 978, "selected G contacts are not unique")
        require(np.array_equal(contact_support[first_contact], first_support), "contact support mapping")
        center_pm = np.rint(instance_center_um * 1.0e6).astype(np.int64)
        require(np.array_equal(np.c_[binding_x_pm, binding_y_pm], center_pm), "binding center differs")

        with np.load(
            ROOT / "outputs/research/astra-l02-pad-conductor-domain-01/l02-pad-drill-support-map.npz",
            allow_pickle=False,
        ) as supports:
            support_xy = np.c_[supports["support_x_pm"], supports["support_y_pm"]]
            support_diameter = supports["support_diameter_pm"]
            support_is_drill = supports["support_is_drill"]
        require(np.array_equal(support_xy[first_support], center_pm), "r20 support center differs")
        require(np.all(support_diameter[first_support] == 40_000_000), "r20 support diameter")
        require(np.all(support_is_drill[first_support]), "contact support is not drill")

        with np.load(
            ROOT / "outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz",
            allow_pickle=False,
        ) as sheet:
            sheet_contact_support = sheet["contact_support_index"].copy()
            contact_node_indptr = sheet["contact_node_indptr"].copy()
            contact_triangle_indptr = sheet["contact_triangle_indptr"].copy()
        require(np.array_equal(sheet_contact_support, contact_support), "sheet/binding contacts differ")
        contact_node_count = np.diff(contact_node_indptr)[first_contact]
        contact_triangle_count = np.diff(contact_triangle_indptr)[first_contact]
        require(np.all(contact_node_count == 16), "sheet contact-node count differs")
        require(np.all(contact_triangle_count == 14), "sheet contact-triangle count differs")

        with np.load(
            ROOT / "outputs/research/astra-device-group-port-01/group-port.npz",
            allow_pickle=False,
        ) as port:
            port_xy = port["source_pad_xy_pm"].copy()
            terminal_voltage_map = port["terminal_voltage_map"].copy()
            branch_pairs = port["source_branch_pair_indices"].copy()
        xy_to_port = {tuple(map(int, xy)): i for i, xy in enumerate(port_xy)}
        group_rows = np.asarray([xy_to_port[tuple(map(int, xy))] for xy in center_pm], dtype=np.int64)
        require(len(np.unique(group_rows)) == 978, "G port rows are not unique")
        require(np.all(terminal_voltage_map[group_rows] == [0, 1]), "G group voltage map differs")
        require(np.array_equal(np.sort(group_rows), np.sort(branch_pairs[:, 1])), "G branch-pair rows differ")

        # Logical node ordering is [P-group, G-group, sheet-contact ordinals].
        # Positive coordinate current leaves the G group and enters its L02
        # sheet contact.  Every column is a separate unknown even though the
        # TOP potential is shared.
        coordinate_count = len(instance_pin)
        incidence = coo_matrix(
            (
                np.r_[np.ones(coordinate_count), -np.ones(coordinate_count)],
                (
                    np.r_[np.full(coordinate_count, 1), 2 + first_contact],
                    np.r_[np.arange(coordinate_count), np.arange(coordinate_count)],
                ),
            ),
            shape=(2 + len(contact_support), coordinate_count),
        ).tocsr()
        require(np.max(np.abs(np.asarray(incidence.sum(axis=0)).ravel())) == 0.0, "branch KCL")
        resistance_instances = diags(
            np.full(coordinate_count, sparse_energy), format="csr"
        )
        rng = np.random.default_rng(20260909)
        current = rng.standard_normal(coordinate_count)
        potential = rng.standard_normal(incidence.shape[0])
        virtual_work_error = abs(
            float(potential @ (incidence @ current))
            - float(current @ (incidence.T @ potential))
        )
        energy_instance_relative = abs(
            float(current @ (resistance_instances @ current))
            - sparse_energy * float(current @ current)
        ) / (sparse_energy * float(current @ current))
        require(virtual_work_error < 1.0e-12, "virtual work")
        require(energy_instance_relative < 1.0e-15, "instanced Joule energy")

        artifact = output / "g-sheet-hybrid-post-binding.npz"
        np.savez_compressed(
            artifact,
            truncated_vertices_local_um=vertices_um,
            truncated_cells=cells,
            truncated_source_cell_ids=selected_old_cells,
            truncated_face_vertices=faces,
            truncated_face_area_vector_um2=mesh["face_area_vector_um2"],
            truncated_first_owner_cell=first_owner,
            truncated_internal_face_ids=internal,
            truncated_boundary_face_ids=boundary,
            truncated_cell_face_ids=face_ids,
            truncated_cell_face_signs=face_signs,
            top_patch_face_ids=top_faces,
            lower_r20_z55_contact_face_ids=lower_faces,
            complementary_exterior_face_ids=other_boundary,
            template_face_flux_basis=flux[:, None],
            template_cell_current_center_per_m2=center_current,
            template_cell_rt0_radial_coefficient_per_m3=radial,
            template_cell_volume_m3=volume_m3,
            template_cell_radial_variance_m2=variance,
            template_resistance_ohm=np.asarray([sparse_energy]),
            template_metric_scale_ohm=np.asarray([scale]),
            template_lagrange_multipliers_scaled=solution[len(active):],
            instance_pin_id=instance_pin,
            instance_center_um=instance_center_um,
            instance_group_port_row=group_rows,
            instance_first_via_id=instance_first_via,
            instance_next_via_id=instance_next_via,
            instance_l02_contact_ordinal=first_contact,
            instance_l02_drill_support_index=first_support,
            instance_sheet_contact_node_count=contact_node_count,
            instance_sheet_contact_triangle_count=contact_triangle_count,
            **csr_payload(resistance, "template_resistance"),
            **csr_payload(divergence, "template_volume_d"),
            **csr_payload(resistance_instances, "instance_resistance"),
            **csr_payload(incidence, "logical_node_coordinate_incidence"),
        )

        checks = {
            "truncated_zone_contract": "zone0_TOP_pad_plus_zone1_via_stem; zone2_L02_pad_excluded",
            "truncated_cell_count": len(cells),
            "truncated_face_count": len(faces),
            "truncated_internal_face_count": len(internal),
            "truncated_boundary_face_count": len(boundary),
            "top_patch_face_count": len(top_faces),
            "lower_r20_z55_face_count": len(lower_faces),
            "complementary_exterior_face_count": len(other_boundary),
            "minimum_signed_tetra_volume_m3": float(signed_det.min() / 6.0),
            "boundary_closure_relative": float(closure),
            "local_mass_quadrature_relative": mass_block_relative,
            "kkt_relative_residual": kkt_relative,
            "cell_divergence_max_a": divergence_max,
            "top_inward_flux_a": top_total,
            "lower_outward_flux_a": lower_total,
            "other_exterior_flux_max_a": other_max,
            "template_joule_resistance_ohm": sparse_energy,
            "independent_centered_joule_relative": energy_relative,
            "selected_g_instance_count": coordinate_count,
            "unique_l02_sheet_contact_count": len(np.unique(first_contact)),
            "sheet_contact_nodes_per_instance": int(contact_node_count[0]),
            "sheet_contact_triangles_per_instance": int(contact_triangle_count[0]),
            "logical_incidence_shape": list(incidence.shape),
            "logical_incidence_nnz": int(incidence.nnz),
            "instance_resistance_nnz": int(resistance_instances.nnz),
            "column_kcl_max_a_per_a": float(
                np.max(np.abs(np.asarray(incidence.sum(axis=0)).ravel()))
            ),
            "virtual_work_absolute_error_w": virtual_work_error,
            "instanced_energy_relative": energy_instance_relative,
        }
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "PASS_SELECTED_G_TRUNCATED_POST_TO_EXISTING_L02_SHEET_STATIC_BINDING",
            "elapsed_s": monotonic() - started,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifact_sha256": digest(artifact),
            "checks": checks,
            "ownership_contract": (
                "The new 3-D coordinate owns only the TOP-pad and via-stem copper through z=55. "
                "The existing 2-D L02 sheet keeps sole ownership of the complete 20 um L02 copper, "
                "including each r30 pad and the residual plane.  The old selected first-via branch "
                "must be removed exactly once by the separate native-edge replacement ledger; every "
                "next-via branch and every unselected G continuation stays present."
            ),
            "coupling_contract": (
                "Each of 978 independent current coordinates connects the shared G terminal-potential "
                "row to one exact saved L02 contact ordinal.  Positive coordinate current flows from "
                "the TOP group through the truncated post into that sheet contact."
            ),
            "conditional_assumption": (
                "The existing 16-node contracted r20 sheet contact is used as an aggregate equipotential "
                "interface for the 96-face z=55 r20 post disk.  This treats the contact potential as "
                "constant through the L02 thickness; it is a declared hybrid dimensional coupling, "
                "not a conforming 3-D r30 interface proof."
            ),
            "scope": (
                "Static sparse assembly inputs for the selected G first-post replacement only. "
                "Other exterior post flux, circulation and charge spaces remain available; zero flux "
                "there defines only this one transport column.  The shared native G node and all "
                "unselected return branches remain external to this artifact.  No native-edge removal "
                "is performed here.  No magnetic/scalar Green, finite-frequency solve, terminal Z, "
                "full G completion, board response or PowerSI accuracy is claimed."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "checks": checks}, indent=2))
    except Exception:
        (output / "failure.json").write_text(
            json.dumps({"status": "STOP_SELECTED_G_SHEET_HYBRID_BINDING", "traceback": traceback.format_exc()}, indent=2),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.output.resolve())
