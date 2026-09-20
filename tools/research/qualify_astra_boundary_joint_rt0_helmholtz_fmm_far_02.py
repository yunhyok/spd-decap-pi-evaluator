"""SPD Decap PI Evaluator v0.23.1: actual 5304-tetra far-action control.

This control keeps every point-quadrature source, uses three tetrahedra already
selected by the independent static self control as target test rows, and keeps
the radius-2 point-near contribution separate.  It qualifies the outgoing FMM
point action and its identical-quadrature near subtraction, not the analytic
near replacement or a mixed current/charge field operator.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import pi
from pathlib import Path
from time import monotonic
import traceback

import numpy as np

import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as apply


ROOT = Path(__file__).resolve().parents[2]
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
MU0 = 4e-7 * pi
FREQUENCIES_HZ = np.array([1e3, 1e6, 1e8])
PINS = {
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz": "3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1",
    "outputs/research/astra-source-joint-self-green-01/result.json": "d2b12c074accb74ba80d7c14c7e0b53ab265251edfeff3881b2e0702258e9795",
    "outputs/research/astra-helmholtz-fmm-complex-adapter-01/result.json": "e46bbf936ec4f1b6b2b26c2d62264bc75536e0ad28001b862cc06ea4ce1304fb",
    "outputs/research/astra-helmholtz-fmm-complex-adapter-01/complex-adapter.npz": "42c6d5cc4e363bb88ffce81acb09d74645a7dfd21c43ad9a0bda54c31c4bb43f",
    "outputs/research/astra-helmholtz-fmm-complex-adapter-01/driver-at-run.py": "3d09e36dfce15aeb15e5392dc1cc640392119974e0efc1a4f60225778a1d4e84",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def relative(actual: np.ndarray, reference: np.ndarray) -> float:
    return float(np.linalg.norm(actual - reference) / max(float(np.linalg.norm(reference)), 1e-300))


def component_metrics(actual: np.ndarray, reference: np.ndarray) -> dict[str, float]:
    return {
        "full": relative(actual, reference),
        "real": relative(actual.real, reference.real),
        "imaginary": relative(actual.imag, reference.imag),
    }


def target_rule(
    joint: dict[str, np.ndarray], target_cell_ids: np.ndarray, order: int
) -> dict[str, np.ndarray]:
    tetrahedra = joint["tetrahedra_m"][target_cell_ids]
    volumes = joint["cell_volumes_m3"][target_cell_ids]
    barycentric, reference_weights = apply.reference_tetra_rule(order)
    points = np.einsum("qa,tad->tqd", barycentric, tetrahedra)
    weights = 6.0 * volumes[:, None] * reference_weights[None, :]
    local = (points[:, :, None, :] - tetrahedra[:, None, :, :]) / (
        3.0 * volumes[:, None, None, None]
    )
    signs = joint["local_face_signs"][target_cell_ids]
    global_test = signs[:, None, :, None] * local
    return {
        "points_m": points,
        "weights_m3": weights,
        "global_test_per_m2": global_test,
        "points_per_cell": np.array([points.shape[1]], dtype=np.int64),
        "order": np.array([order], dtype=np.int64),
    }


def integrate_rows(target: dict[str, np.ndarray], potential: np.ndarray) -> np.ndarray:
    count, points_per_cell = target["points_m"].shape[:2]
    reshaped = potential.reshape(potential.shape[0], count, points_per_cell, 3)
    return MU0 * np.einsum(
        "tq,tqid,rtqd->rti",
        target["weights_m3"],
        target["global_test_per_m2"],
        reshaped,
        optimize=True,
    )


def subset_sources(sources: dict[str, np.ndarray], cell_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keep = np.isin(sources["source_cell_ids"], cell_ids, assume_unique=False)
    return sources["source_points_m"][keep], sources["source_weighted_current_a_m"][keep]


def self_check() -> None:
    apply.verify_inputs()
    apply.self_check()
    dummy_target = {
        "points_m": np.zeros((1, 1, 3)),
        "weights_m3": np.ones((1, 1)),
        "global_test_per_m2": np.ones((1, 1, 4, 3)),
    }
    potential = np.arange(6, dtype=float).reshape(2, 1, 3)
    rows = integrate_rows(dummy_target, potential)
    require(rows.shape == (2, 1, 4), "test-row shape")
    print("PASS_BOUNDARY_JOINT_5304_RT0_FMM_FAR_SELF_CHECK")


def run(output: Path, max_runtime_s: float) -> int:
    started = monotonic()
    deadline = started + max_runtime_s
    require(not output.exists(), "fresh output directory")
    output.mkdir(parents=True)
    driver_copy = output / "driver-at-run.py"
    helper_copy = output / "apply-helper-at-run.py"
    driver_copy.write_bytes(Path(__file__).read_bytes())
    helper_copy.write_bytes(Path(apply.__file__).read_bytes())
    try:
        observed = {name: digest(ROOT / name) for name in PINS}
        require(observed == PINS, "qualifier input pin mismatch")
        helper_pins, runtime_receipt = apply.verify_inputs()
        self_check()
        joint = apply.load_joint()
        with np.load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz", allow_pickle=False) as data:
            modes = data["energy_orthonormal_face_flux"]
            gram = data["scaled_gram"]
        require(modes.shape == (12546, 14), "energy mode shape")
        require(relative(modes.T @ np.zeros((12546, 14)), np.zeros((14, 14))) == 0.0, "finite mode sentinel")
        require(np.isfinite(modes).all() and np.isfinite(gram).all(), "finite modes")
        coefficients = np.array(
            [
                [1+.2j, -.4+.1j, .25-.3j, .12+.27j, -.21+.08j, .16-.11j, .07+.18j,
                 -.13-.09j, .19+.04j, -.06+.12j, .14-.16j, .09+.05j, -.17+.03j, .11-.07j],
                [.18-.31j, .27+.14j, -.22+.09j, .35-.06j, .08+.21j, -.11-.18j, .24+.02j,
                 .05-.17j, -.09+.23j, .13+.15j, -.19-.04j, .16-.12j, .07+.19j, -.15+.08j],
            ],
            dtype=complex,
        )
        coefficients /= np.linalg.norm(coefficients, axis=1)[:, None]
        face_current = modes @ coefficients.T
        require(face_current.shape == (12546, 2), "complex current mixtures")
        self_result = json.loads((ROOT / "outputs/research/astra-source-joint-self-green-01/result.json").read_text(encoding="utf-8"))
        target_cell_ids = np.asarray(
            [entry["cell_id"] for entry in self_result["selected_tetrahedra"]], dtype=np.int64
        )
        require(np.array_equal(target_cell_ids, np.array([5215, 2290, 5220])), "pinned target cells")
        with np.load(ROOT / "outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz", allow_pickle=False) as data:
            near_row_ptr = data["near_row_ptr"]
            near_col = data["near_col"]
            cell_count = int(data["cell_count"][0])
        require(cell_count == 5304, "pair current-cell count")
        near_lists = []
        near_ptr = [0]
        for target_cell in target_cell_ids:
            cells = near_col[near_row_ptr[target_cell]:near_row_ptr[target_cell + 1]]
            cells = cells[cells < cell_count].astype(np.int64)
            require(target_cell in cells, "target self is near")
            near_lists.append(cells)
            near_ptr.append(near_ptr[-1] + len(cells))
        near_cell_ids = np.concatenate(near_lists)
        near_ptr = np.asarray(near_ptr, dtype=np.int64)

        source_rules = {order: apply.build_rt0_volume_sources(face_current, order, joint) for order in (2, 3)}
        target_rules = {order: target_rule(joint, target_cell_ids, order) for order in (2, 3)}
        require(max(float(rule["moment_relative_error"][0]) for rule in source_rules.values()) < 2e-13,
                "source RT0 moments")

        direct_full = {}
        direct_near = {}
        direct_far_rows = {}
        omitted_counts = {}
        for order in (2, 3):
            source = source_rules[order]
            target = target_rules[order]
            full_by_frequency = []
            near_by_frequency = []
            row_by_frequency = []
            omitted_by_frequency = []
            for frequency in FREQUENCIES_HZ:
                require(monotonic() < deadline, "deadline before direct reference")
                full, omitted = apply.direct_outgoing_point_action(
                    source["source_points_m"], source["source_weighted_current_a_m"],
                    target["points_m"].reshape(-1, 3), float(frequency)
                )
                near_parts = []
                near_omitted = 0
                points_per_cell = int(target["points_per_cell"][0])
                for target_index, cells in enumerate(near_lists):
                    near_points, near_weighted = subset_sources(source, cells)
                    first = target_index * points_per_cell
                    last = first + points_per_cell
                    part, part_omitted = apply.direct_outgoing_point_action(
                        near_points, near_weighted, target["points_m"].reshape(-1, 3)[first:last],
                        float(frequency)
                    )
                    near_parts.append(part)
                    near_omitted += part_omitted
                near = np.concatenate(near_parts, axis=1)
                full_by_frequency.append(full)
                near_by_frequency.append(near)
                row_by_frequency.append(integrate_rows(target, full - near))
                omitted_by_frequency.append([omitted, near_omitted])
            direct_full[order] = np.stack(full_by_frequency, axis=1)
            direct_near[order] = np.stack(near_by_frequency, axis=1)
            direct_far_rows[order] = np.stack(row_by_frequency, axis=1)
            omitted_counts[order] = np.asarray(omitted_by_frequency, dtype=np.int64)

        source = source_rules[3]
        target = target_rules[3]
        fmm_full_frequency = []
        fmm_far_rows_frequency = []
        for frequency_index, frequency in enumerate(FREQUENCIES_HZ):
            require(monotonic() < deadline, "deadline before FMM")
            potential = apply.apply_outgoing_point_action(
                source, target["points_m"].reshape(-1, 3), float(frequency)
            )
            fmm_full_frequency.append(potential)
            fmm_far_rows_frequency.append(
                integrate_rows(target, potential - direct_near[3][:, frequency_index])
            )
        fmm_full = np.stack(fmm_full_frequency, axis=1)
        fmm_far_rows = np.stack(fmm_far_rows_frequency, axis=1)

        metric_names = ("full", "real", "imaginary")
        point_metrics = {name: np.zeros((2, 3)) for name in metric_names}
        far_row_metrics = {name: np.zeros((2, 3)) for name in metric_names}
        quadrature_metrics = {name: np.zeros((2, 3)) for name in metric_names}
        near_fraction = np.zeros((2, 3))
        for mixture in range(2):
            for frequency_index in range(3):
                point = component_metrics(
                    fmm_full[mixture, frequency_index], direct_full[3][mixture, frequency_index]
                )
                row = component_metrics(
                    fmm_far_rows[mixture, frequency_index], direct_far_rows[3][mixture, frequency_index]
                )
                quadrature = component_metrics(
                    direct_far_rows[2][mixture, frequency_index],
                    direct_far_rows[3][mixture, frequency_index],
                )
                for name in metric_names:
                    point_metrics[name][mixture, frequency_index] = point[name]
                    far_row_metrics[name][mixture, frequency_index] = row[name]
                    quadrature_metrics[name][mixture, frequency_index] = quadrature[name]
                near_rows = integrate_rows(
                    target,
                    direct_near[3][mixture:mixture + 1, frequency_index],
                )[0]
                full_rows = integrate_rows(
                    target,
                    direct_full[3][mixture:mixture + 1, frequency_index],
                )[0]
                near_fraction[mixture, frequency_index] = np.linalg.norm(near_rows) / max(
                    float(np.linalg.norm(full_rows)), 1e-300
                )

        gates = {
            "input_and_runtime_pins": True,
            "saved_static_target_identity": bool(np.array_equal(target_cell_ids, [5215, 2290, 5220])),
            "all_5304_source_cells_present": bool(
                np.array_equal(np.unique(source["source_cell_ids"]), np.arange(5304))
            ),
            "affine_rt0_source_moments": bool(
                max(float(rule["moment_relative_error"][0]) for rule in source_rules.values()) < 2e-13
            ),
            "fmm_point_full": bool(np.max(point_metrics["full"]) < 1e-8),
            "fmm_point_real": bool(np.max(point_metrics["real"]) < 1e-8),
            "fmm_point_imaginary": bool(np.max(point_metrics["imaginary"]) < 1e-8),
            "fmm_far_row_full_after_identical_near_subtraction": bool(np.max(far_row_metrics["full"]) < 1e-7),
            "fmm_far_row_real_after_identical_near_subtraction": bool(np.max(far_row_metrics["real"]) < 1e-7),
            "fmm_far_row_imaginary_after_identical_near_subtraction": bool(np.max(far_row_metrics["imaginary"]) < 1e-7),
            "deadline": bool(monotonic() < deadline),
        }
        artifact = output / "far-action.npz"
        with artifact.open("xb") as stream:
            np.savez_compressed(
                stream,
                frequencies_hz=FREQUENCIES_HZ,
                target_cell_ids=target_cell_ids,
                target_tetrahedra_m=joint["tetrahedra_m"][target_cell_ids],
                target_q3_points_m=target["points_m"],
                target_q3_weights_m3=target["weights_m3"],
                target_q3_global_rt0_test_per_m2=target["global_test_per_m2"],
                mixture_coefficients=coefficients,
                mixture_face_current_a=face_current,
                source_q3_points_m=source["source_points_m"],
                source_q3_weights_m3=source["source_weights_m3"],
                source_q3_current_density_a_per_m2=source["source_current_density_a_per_m2"],
                source_q3_cell_ids=source["source_cell_ids"],
                near_source_row_ptr=near_ptr,
                near_source_cell_ids=near_cell_ids,
                fmm_full_point_action=fmm_full,
                direct_full_point_action=direct_full[3],
                direct_near_point_action=direct_near[3],
                fmm_far_rt0_rows_h=fmm_far_rows,
                direct_far_q3_rt0_rows_h=direct_far_rows[3],
                direct_far_q2_rt0_rows_h=direct_far_rows[2],
                q2_q3_point_omission_counts=np.stack((omitted_counts[2], omitted_counts[3])),
                fmm_point_full_relative=point_metrics["full"],
                fmm_point_real_relative=point_metrics["real"],
                fmm_point_imaginary_relative=point_metrics["imaginary"],
                fmm_far_row_full_relative=far_row_metrics["full"],
                fmm_far_row_real_relative=far_row_metrics["real"],
                fmm_far_row_imaginary_relative=far_row_metrics["imaginary"],
                direct_far_q2_q3_full_relative=quadrature_metrics["full"],
                direct_far_q2_q3_real_relative=quadrature_metrics["real"],
                direct_far_q2_q3_imaginary_relative=quadrature_metrics["imaginary"],
                point_near_row_fraction=near_fraction,
            )
        maxima = {
            "fmm_point_full_relative": float(np.max(point_metrics["full"])),
            "fmm_point_real_relative": float(np.max(point_metrics["real"])),
            "fmm_point_imaginary_relative": float(np.max(point_metrics["imaginary"])),
            "fmm_far_row_full_relative": float(np.max(far_row_metrics["full"])),
            "fmm_far_row_real_relative": float(np.max(far_row_metrics["real"])),
            "fmm_far_row_imaginary_relative": float(np.max(far_row_metrics["imaginary"])),
            "direct_far_q2_q3_full_relative": float(np.max(quadrature_metrics["full"])),
            "direct_far_q2_q3_real_relative": float(np.max(quadrature_metrics["real"])),
            "direct_far_q2_q3_imaginary_relative": float(np.max(quadrature_metrics["imaginary"])),
            "point_near_row_fraction": float(np.max(near_fraction)),
            "source_rt0_moment_relative": max(
                float(rule["moment_relative_error"][0]) for rule in source_rules.values()
            ),
        }
        failed = [name for name, passed in gates.items() if not passed]
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "PASS_BOUNDARY_JOINT_5304_RT0_FMM_FAR_CONTROL" if not failed else "STOP_BOUNDARY_JOINT_5304_RT0_FMM_FAR_CONTROL",
            "driver_sha256": digest(driver_copy),
            "apply_helper_sha256": digest(helper_copy),
            "artifact_sha256": digest(artifact),
            "pins": observed,
            "apply_helper_pins": helper_pins,
            "runtime_version": runtime_receipt["fmm3d_version"],
            "kernel_contract": "conj(hfmm3d(zk=+k, charges=conj(q))) = outgoing exp(-ikR)/(4piR) q",
            "supports": {
                "source_tetrahedra": 5304,
                "source_q3_points": int(len(source["source_points_m"])),
                "target_tetrahedra": target_cell_ids.tolist(),
                "target_local_rt0_rows": 12,
                "complex_current_mixtures": 2,
            },
            "maxima": maxima,
            "per_mixture_frequency": {
                "fmm_point_full_relative": point_metrics["full"].tolist(),
                "fmm_point_real_relative": point_metrics["real"].tolist(),
                "fmm_point_imaginary_relative": point_metrics["imaginary"].tolist(),
                "fmm_far_row_full_relative": far_row_metrics["full"].tolist(),
                "fmm_far_row_real_relative": far_row_metrics["real"].tolist(),
                "fmm_far_row_imaginary_relative": far_row_metrics["imaginary"].tolist(),
                "direct_far_q2_q3_full_relative": quadrature_metrics["full"].tolist(),
                "direct_far_q2_q3_real_relative": quadrature_metrics["real"].tolist(),
                "direct_far_q2_q3_imaginary_relative": quadrature_metrics["imaginary"].tolist(),
            },
            "gates": gates,
            "failed_gates": failed,
            "elapsed_s": monotonic() - started,
            "scope": (
                "All 5304 tetrahedra contribute q3 affine-RT0 point sources for two complex combinations "
                "of the 14 saved energy-normalized seed currents. Three independently selected actual "
                "tetrahedra provide all 12 local RT0 test rows. Radius-2 near point action is subtracted "
                "identically and saved, but no analytic near block is added. q2-to-q3 far-row differences "
                "are diagnostics, not convergence gates. No scalar/contact solve, all-row operator, mesh "
                "convergence, 933-chain assembly, impedance, board model or PowerSI accuracy is qualified."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "maxima": maxima}))
        return 0 if not failed else 2
    except Exception as error:
        failure = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "STOP_BOUNDARY_JOINT_5304_RT0_FMM_FAR_CONTROL",
            "error": str(error),
            "traceback": traceback.format_exc(),
            "elapsed_s": monotonic() - started,
        }
        (output / "failure.json").write_text(
            json.dumps(failure, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-runtime-s", type=float, default=180.0)
    arguments = parser.parse_args()
    if arguments.self_check:
        self_check()
    elif arguments.output:
        raise SystemExit(run(arguments.output.resolve(), arguments.max_runtime_s))
    else:
        parser.error("choose --self-check or --output")
