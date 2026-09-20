"""SPD Decap PI Evaluator v0.23.1 saved material trial/test review.

Reconstruct the 72-to-80 maps, projected equations, work defect, real-J
Galerkin residual, power, and interface-jump metric from frozen arrays.  This
does not solve a system or repeat a Green/Fourier quadrature.
"""
from hashlib import sha256
from pathlib import Path
import json

import numpy as np


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
EPS0 = 8.8541878128e-12
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/diagnose_astra_box_material_trial_test.py":
        "00a19017d36dfa8b14629c9ed0aae0645c9377ee8f661146995ad2bdcc83e14e",
    "outputs/research/astra-box-material-trial-test-01/result.json":
        "1d9728587ac6242897e8d545bacf1763cade7617222093094882ef965123bcf4",
    "outputs/research/astra-box-material-trial-test-01/fields.npz":
        "180524ff829a0848c944c635463e245aabbc71fbbca2a30ed1c3371712c1402d",
    "tools/research/diagnose_astra_box_material_current_field.py":
        "ca681a29d65001d15bc62c8436694320f5480daafa80fcf136e73727daa66b64",
    "outputs/research/astra-box-material-current-field-01/result.json":
        "95342078e29cca2c424b5e81fa7ed8134a7bff6a0c27ab4b6dae155b0bf2d0b6",
    "outputs/research/astra-box-material-current-field-01/fields.npz":
        "c1601ae576084a720b4ef9407059d6e6e84c77f88f0bb98c72dd410d206e158e",
    "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz":
        "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02",
}
VARIANTS = ("equal_copper", "near_equal_copper", "source_copper_abf")
DRIVES = np.array([[1, 0, 1], [0, 1, 1j]], complex)


def digest(path):
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def load_npz(path):
    with np.load(path, allow_pickle=False) as saved:
        return {name: saved[name] for name in saved.files}


def relative(actual, reference):
    return float(
        np.linalg.norm(actual - reference)
        / max(np.linalg.norm(reference), np.finfo(float).tiny)
    )


def componentwise_backward(matrix, solution, rhs):
    residual = matrix @ solution - rhs
    scale = np.abs(matrix) @ np.abs(solution) + np.abs(rhs)
    return float(np.max(
        np.abs(residual) / np.maximum(scale, np.finfo(float).tiny)
    ))


def tetra_volume(tetrahedron):
    a, b, c, d = tetrahedron
    return abs(np.linalg.det(np.stack((b - a, c - a, d - a)))) / 6


def main():
    for relative_path, expected in PINS.items():
        actual = digest(ROOT / relative_path)
        assert actual == expected, (relative_path, actual)

    result = json.loads(
        (ROOT / "outputs/research/astra-box-material-trial-test-01/result.json")
        .read_bytes()
    )
    old_result = json.loads(
        (ROOT / "outputs/research/astra-box-material-current-field-01/result.json")
        .read_bytes()
    )
    assert result["status"] == "PASS_MATERIAL_TRIAL_TEST_DIAGNOSTIC"
    assert result["script_sha256"] == PINS[
        "tools/research/diagnose_astra_box_material_trial_test.py"
    ]
    assert result["fields_sha256"] == PINS[
        "outputs/research/astra-box-material-trial-test-01/fields.npz"
    ]
    assert old_result["status"] == "STOP_MATERIAL_COUPLING_DIAGNOSTIC"

    fields = load_npz(
        ROOT / "outputs/research/astra-box-material-trial-test-01/fields.npz"
    )
    old = load_npz(
        ROOT / "outputs/research/astra-box-material-current-field-01/fields.npz"
    )
    with np.load(
        ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz",
        allow_pickle=False,
    ) as kernels:
        tetrahedra = kernels["tetrahedra_m"]
        triangles = kernels["triangles_m"]
        bmat = kernels["distributional_divergence"][48:]
        frequencies = kernels["frequencies_hz"]

    transform = fields["real_current_transform"]
    divergence = fields["real_face_divergence"]
    dmap = fields["real_total_test_map"]
    emap = fields["right_region_test_map"]
    mass = fields["real_current_mass"]
    old_transform = old["total_current_transform"]
    old_h = old["cell_integrated_total_current_map"]
    material_ids = old["cell_material_id"].astype(bool)
    assert transform.shape == (192, 80)
    assert dmap.shape == emap.shape == (80, 72)
    assert np.array_equal(dmap, np.rint(dmap))
    assert np.array_equal(emap, np.rint(emap))
    assert np.array_equal(transform @ dmap, old_transform)
    right_target = old_transform.reshape(48, 4, 72).copy()
    right_target[~material_ids] = 0
    assert np.array_equal(transform @ emap, right_target.reshape(192, 72))
    assert np.array_equal(divergence, bmat @ transform)

    volumes = np.array([tetra_volume(tetrahedron) for tetrahedron in tetrahedra])
    moments = np.array([
        (tetrahedron.mean(axis=0) - tetrahedron) / 3
        for tetrahedron in tetrahedra
    ])
    hmap = np.einsum(
        "tid,tin->tdn", moments, transform.reshape(48, 4, 80)
    )
    rebuilt_mass = np.einsum("tdi,tdj,t->ij", hmap, hmap, 1 / volumes)
    map_checks = {
        "mass_relative": relative(rebuilt_mass, mass),
        "old_cell_moment_relative": relative(
            np.einsum("tdi,ij->tdj", hmap, dmap), old_h
        ),
        "divergence_relative": relative(bmat @ transform, divergence),
    }

    result_cases = {
        (float(case["frequency_hz"]), case["variant"]): case
        for case in result["cases"]
    }
    old_cases = {
        (float(case["frequency_hz"]), case["variant"]): case
        for case in old_result["cases"]
    }
    cases = []
    for index, frequency in enumerate(frequencies):
        omega = 2 * np.pi * float(frequency)
        for variant in VARIANTS:
            key = f"{variant}_{index:02d}"
            report = result_cases[(float(frequency), variant)]
            old_report = old_cases[(float(frequency), variant)]
            operator = fields[key + "_operator"]
            rhs = fields[key + "_incident_rhs"]
            cmap = fields[key + "_material_source_map"]
            old_modal = old[key + "_total_modal_current"]
            old_lifted = fields[key + "_saved72_lifted_current"]
            old_residual = fields[key + "_omitted_work_residual"]
            modal = fields[key + "_modal_current"]
            reaction = rhs.T @ modal

            old_operator = old[key + "_operator"]
            old_rhs = old[key + "_incident_rhs_total"]
            old_rhs_star = old[key + "_incident_rhs_conjugated_contrast"]
            projection = {
                "operator_relative": relative(
                    dmap.T @ operator @ cmap, old_operator
                ),
                "incident_rhs_relative": relative(dmap.T @ rhs, old_rhs),
                "physical_extinction_rhs_relative": relative(
                    cmap.conj().T @ rhs, old_rhs_star
                ),
                "lifted_current_relative": relative(cmap @ old_modal, old_lifted),
            }
            old_driven = old_lifted @ DRIVES
            rebuilt_old_residual = operator @ old_driven - rhs @ DRIVES
            projection["omitted_residual_relative"] = relative(
                rebuilt_old_residual, old_residual
            )
            old_work = np.real(np.sum(old_driven.conj() * old_residual, axis=0))
            old_loss = np.asarray(old_report["absorption_w"])
            old_radiation = np.asarray(old_report["radiation_w"])
            old_extinction = np.asarray(old_report["extinction_w"])
            old_scale = np.abs(old_loss) + np.abs(old_radiation) + np.abs(old_extinction)
            projection["physical_work_defect_match_scaled_max"] = float(np.max(
                np.abs(old_work - (old_loss + old_radiation - old_extinction))
                / np.maximum(old_scale, np.finfo(float).tiny)
            ))

            driven = modal @ DRIVES
            driven_rhs = rhs @ DRIVES
            residual = operator @ driven - driven_rhs
            equation_backward = componentwise_backward(operator, modal, rhs)
            work = np.real(np.sum(driven.conj() * residual, axis=0))
            absorption = np.asarray(report["absorption_w"])
            radiation = np.asarray(report["radiation_w"])
            extinction = np.asarray(report["extinction_w"])
            scale = np.abs(absorption) + np.abs(radiation) + np.abs(extinction)
            operator_work = np.diag(driven.conj().T @ operator @ driven).real
            rhs_work = np.real(np.sum(driven.conj() * driven_rhs, axis=0))

            gamma = old[key + "_cell_gamma"]
            kappa = gamma - 1j * omega * EPS0
            cell_j = np.einsum("tdi,ij->tdj", hmap, driven) / volumes[:, None, None]
            cell_u = cell_j * (gamma / kappa)[:, None, None]
            local_u = (transform @ driven).reshape(48, 4, 3)
            local_u *= (gamma / kappa)[:, None, None]
            flat_u = local_u.reshape(192, 3)
            jumps = []
            jump_energy = 0.0
            areas = np.linalg.norm(
                np.cross(triangles[:, 1] - triangles[:, 0],
                         triangles[:, 2] - triangles[:, 0]), axis=1
            ) / 2
            for face, row in enumerate(bmat):
                local_indices = np.flatnonzero(row)
                if len(local_indices) == 2:
                    jump = flat_u[local_indices].sum(axis=0)
                    jumps.append(jump)
                    jump_energy += np.sum(np.abs(jump) ** 2) / areas[face]
            jumps = np.asarray(jumps)
            norm = np.einsum(
                "t,tdj,tdj->", volumes, cell_u.conj(), cell_u
            ).real
            jump_metric = float(np.sqrt(
                np.ptp(tetrahedra.reshape(-1, 3), axis=0).max()
                * jump_energy / norm
            ))

            delta = modal - old_lifted
            current_change = np.sqrt(
                np.diag(delta.conj().T @ mass @ delta).real
                / np.diag(modal.conj().T @ mass @ modal).real
            )
            case = {
                "frequency_hz": float(frequency),
                "variant": variant,
                "projection": projection,
                "new80_componentwise_backward": equation_backward,
                "saved_reaction_relative": relative(
                    fields[key + "_reaction"], reaction
                ),
                "reported_backward_absolute": abs(
                    equation_backward - report["backward"][-1]
                ),
                "reported_current_change_relative": relative(
                    np.asarray(report["current_mass_change_relative_new"]),
                    current_change,
                ),
                "reported_jump_relative": abs(
                    jump_metric - report["total_normal_current_jump_fixed_body_relative"]
                ) / report["total_normal_current_jump_fixed_body_relative"],
                "saved_jump_relative": relative(
                    fields[key + "_total_normal_jumps"], jumps
                ),
                "operator_work_vs_loss_radiation_scaled_max": float(np.max(
                    np.abs(operator_work - (absorption + radiation))
                    / np.maximum(scale, np.finfo(float).tiny)
                )),
                "rhs_work_vs_extinction_scaled_max": float(np.max(
                    np.abs(rhs_work - extinction)
                    / np.maximum(scale, np.finfo(float).tiny)
                )),
                "residual_work_vs_power_defect_scaled_max": float(np.max(
                    np.abs(work - (absorption + radiation - extinction))
                    / np.maximum(scale, np.finfo(float).tiny)
                )),
                "reported_power_relative_absolute": abs(
                    float(np.max(np.abs(absorption + radiation - extinction)
                        / np.maximum(scale, np.finfo(float).tiny)))
                    - report["power_relative"]
                ),
                "operator_transpose_relative": relative(operator, operator.T),
                "total_normal_current_jump_fixed_body_relative": jump_metric,
            }
            cases.append(case)

    maximums = {}
    for case in cases:
        for key, value in case.items():
            if key in ("frequency_hz", "variant", "projection"):
                continue
            maximums[key] = max(maximums.get(key, 0.0), float(value))
        for key, value in case["projection"].items():
            maximums["projection_" + key] = max(
                maximums.get("projection_" + key, 0.0), float(value)
            )

    gates = {
        "saved_maps": max(map_checks.values()) < 1e-12,
        "petrov_projection": max(
            maximums["projection_operator_relative"],
            maximums["projection_incident_rhs_relative"],
            maximums["projection_physical_extinction_rhs_relative"],
            maximums["projection_lifted_current_relative"],
            maximums["projection_omitted_residual_relative"],
        ) < 1e-12,
        "petrov_work_defect_identity":
            maximums["projection_physical_work_defect_match_scaled_max"] < 1e-8,
        "real_j_equations": maximums["new80_componentwise_backward"] < 1e-12,
        "real_j_saved_metrics": max(
            maximums["saved_reaction_relative"],
            maximums["reported_backward_absolute"],
            maximums["reported_current_change_relative"],
            maximums["reported_jump_relative"],
            maximums["saved_jump_relative"],
            maximums["reported_power_relative_absolute"],
        ) < 1e-10,
        "real_j_power_identity": max(
            maximums["operator_work_vs_loss_radiation_scaled_max"],
            maximums["rhs_work_vs_extinction_scaled_max"],
            maximums["residual_work_vs_power_defect_scaled_max"],
        ) < 1e-8,
    }
    gates = {key: bool(value) for key, value in gates.items()}
    assert all(gates.values()), (gates, map_checks, maximums)

    output = ROOT / "outputs/research/astra-box-material-trial-test-review-01"
    assert not output.exists()
    output.mkdir()
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_SAVED_MATERIAL_TRIAL_TEST_ALGEBRA_WITH_SCOPE",
        "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)),
        "map_checks": map_checks,
        "maximum_checks": maximums,
        "gates": gates,
        "cases": cases,
        "scope": (
            "No-solve reconstruction of the exact integer D/E maps, C-weighted "
            "72-coordinate Petrov projection, omitted physical-work residual, "
            "and the saved 80-current real-J equation, reaction, power, and "
            "total-normal U jump metric. No Green or Fourier quadrature is "
            "repeated. The original 72-coordinate physical-power STOP remains. "
            "The 80-current power identity does not qualify its nonzero interface "
            "jump, continuum convergence, source solid, port/board Z, or PowerSI "
            "accuracy."
        ),
    }
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
    print(json.dumps({
        "status": review["status"],
        "reviewer_sha256": review["reviewer_sha256"],
        "receipt_sha256": digest(output / "independent-review.json"),
        "maximum_checks": maximums,
    }))


if __name__ == "__main__":
    main()
