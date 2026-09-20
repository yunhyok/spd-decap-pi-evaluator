"""Independent saved-array review of the actual-5304 RT0 FMM far control.

This reviewer never calls fmm3d.  It rebuilds geometry/current ordering, near
membership, target testing, selected direct outgoing sums, and every reported
relative metric from the frozen artifact.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import pi
from pathlib import Path

import numpy as np
from numpy.polynomial.legendre import leggauss


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-boundary-joint-rt0-helmholtz-fmm-far-review-02"
PINS = {
    "outputs/research/astra-boundary-joint-rt0-helmholtz-fmm-far-02/result.json": "35d203be77887af2899c5d76a00a9e5435e4a4491ec3c65ec845bcef98cda257",
    "outputs/research/astra-boundary-joint-rt0-helmholtz-fmm-far-02/far-action.npz": "9d2596583562133e8f137a3490376cf572ffe6f2565033c6ec90fbe3bda116ea",
    "outputs/research/astra-boundary-joint-rt0-helmholtz-fmm-far-02/driver-at-run.py": "c5888cca4b0d372ae9bfcddd41016bda85005a416031d960de2502088624dfa2",
    "outputs/research/astra-boundary-joint-rt0-helmholtz-fmm-far-02/apply-helper-at-run.py": "3ad0e14937943f4c7941828f1e1bae345b05b5c88b812ddbdb68e69c83ca60d4",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz": "3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1",
    "outputs/research/astra-source-joint-self-green-01/result.json": "d2b12c074accb74ba80d7c14c7e0b53ab265251edfeff3881b2e0702258e9795",
}
MU0 = 4e-7 * pi
C0 = 299_792_458.0


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def relative(actual: np.ndarray, reference: np.ndarray) -> float:
    return float(np.linalg.norm(actual - reference) / max(float(np.linalg.norm(reference)), 1e-300))


def reference_rule(order: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = leggauss(order)
    x, w = (x + 1) / 2, w / 2
    u, v, t = np.meshgrid(x, x, x, indexing="ij")
    bary = np.column_stack(
        (((1-u)*(1-v)*(1-t)).ravel(), u.ravel(), ((1-u)*v).ravel(), ((1-u)*(1-v)*t).ravel())
    )
    weights = (w[:, None, None]*w[None, :, None]*w[None, None, :]*(1-u)**2*(1-v)).ravel()
    return bary, weights


def direct_subset(points: np.ndarray, weighted: np.ndarray, targets: np.ndarray, frequency: float) -> np.ndarray:
    result = np.zeros((weighted.shape[1], len(targets), 3), complex)
    k = 2*pi*frequency/C0
    for first in range(0, len(points), 4096):
        last = min(first+4096, len(points))
        distance = np.linalg.norm(targets[:, None]-points[None, first:last], axis=2)
        nonzero = distance > 0
        kernel = np.zeros(distance.shape, complex)
        radius = distance[nonzero]
        kernel[nonzero] = (1+np.expm1(-1j*k*radius))/(4*pi*radius)
        result += np.einsum("ts,srd->rtd", kernel, weighted[first:last], optimize=True)
    return result


def integrate(weights: np.ndarray, basis: np.ndarray, potential: np.ndarray) -> np.ndarray:
    nrhs = potential.shape[0]
    return MU0*np.einsum(
        "tq,tqid,rtqd->rti", weights, basis,
        potential.reshape(nrhs, weights.shape[0], weights.shape[1], 3), optimize=True,
    )


def run() -> None:
    require(not OUTPUT.exists(), "fresh review output")
    OUTPUT.mkdir(parents=True)
    observed = {name: digest(ROOT/name) for name in PINS}
    require(observed == PINS, "review input pin mismatch")
    result = json.loads((ROOT/list(PINS)[0]).read_text(encoding="utf-8"))
    require(result["status"] == "PASS_BOUNDARY_JOINT_5304_RT0_FMM_FAR_CONTROL", "producer status")
    with np.load(ROOT/list(PINS)[1], allow_pickle=False) as stored:
        data = {name: stored[name] for name in stored.files}
    require(all(np.isfinite(value).all() for value in data.values()), "finite saved arrays")

    with np.load(ROOT/list(PINS)[4], allow_pickle=False) as mesh:
        vertices = mesh["vertices_local_um"]*1e-6
        cells = mesh["cells"]
    tetrahedra = vertices[cells]
    with np.load(ROOT/list(PINS)[5], allow_pickle=False) as space:
        columns = space["local_rt0_face_columns"]
        signs = space["local_rt0_face_signs"]
        modes = space["energy_orthonormal_face_flux"]
    target_ids = data["target_cell_ids"]
    self_result = json.loads((ROOT/list(PINS)[7]).read_text(encoding="utf-8"))
    require(np.array_equal(target_ids, [x["cell_id"] for x in self_result["selected_tetrahedra"]]), "target provenance")
    require(np.array_equal(data["target_tetrahedra_m"], tetrahedra[target_ids]), "target geometry")

    face_current = modes @ data["mixture_coefficients"].T
    face_error = relative(face_current, data["mixture_face_current_a"])
    require(face_error < 2e-15, "mixture face current")
    bary, reference_weights = reference_rule(3)
    expected_points = np.einsum("qa,tad->tqd", bary, tetrahedra)
    point_error = relative(expected_points.reshape(-1, 3), data["source_q3_points_m"])
    require(point_error < 2e-15, "q3 source geometry/order")
    determinant = np.linalg.det(np.stack((tetrahedra[:, 1]-tetrahedra[:, 0], tetrahedra[:, 2]-tetrahedra[:, 0], tetrahedra[:, 3]-tetrahedra[:, 0]), axis=1))
    volumes = np.abs(determinant)/6
    expected_weights = (6*volumes[:, None]*reference_weights).reshape(-1)
    weight_error = relative(expected_weights, data["source_q3_weights_m3"])
    local_flux = signs[:, :, None]*face_current[columns]
    flux_sum = local_flux.sum(axis=1)
    vertex_sum = np.einsum("tir,tid->trd", local_flux, tetrahedra)
    expected_current = (
        flux_sum[:, None, :, None]*expected_points[:, :, None, :]-vertex_sum[:, None]
    )/(3*volumes[:, None, None, None])
    current_error = relative(expected_current.reshape(-1, 2, 3), data["source_q3_current_density_a_per_m2"])
    require(current_error < 3e-15, "affine RT0 source reconstruction")
    require(np.array_equal(data["source_q3_cell_ids"], np.repeat(np.arange(5304), 27)), "source-cell order")

    target_tetrahedra = tetrahedra[target_ids]
    expected_target_points = np.einsum("qa,tad->tqd", bary, target_tetrahedra)
    target_weights = 6*volumes[target_ids, None]*reference_weights
    target_basis = signs[target_ids, None, :, None]*(
        expected_target_points[:, :, None]-target_tetrahedra[:, None]
    )/(3*volumes[target_ids, None, None, None])
    target_error = max(
        relative(expected_target_points, data["target_q3_points_m"]),
        relative(target_weights, data["target_q3_weights_m3"]),
        relative(target_basis, data["target_q3_global_rt0_test_per_m2"]),
    )
    require(target_error < 3e-15, "target test reconstruction")

    with np.load(ROOT/list(PINS)[6], allow_pickle=False) as ownership:
        row_ptr = ownership["near_row_ptr"]
        near_col = ownership["near_col"]
    expected_near = []
    expected_ptr = [0]
    for target in target_ids:
        ids = near_col[row_ptr[target]:row_ptr[target+1]]
        ids = ids[ids < 5304]
        expected_near.append(ids)
        expected_ptr.append(expected_ptr[-1]+len(ids))
    near_error = int(
        not np.array_equal(np.asarray(expected_ptr), data["near_source_row_ptr"])
        or not np.array_equal(np.concatenate(expected_near), data["near_source_cell_ids"])
    )
    require(near_error == 0, "near ownership reconstruction")

    weighted = data["source_q3_weights_m3"][:, None, None]*data["source_q3_current_density_a_per_m2"]
    selected_target_indices = np.array([0, 13, 40, 80])
    direct_errors = []
    for frequency_index in (0, 2):
        rebuilt = direct_subset(
            data["source_q3_points_m"], weighted,
            data["target_q3_points_m"].reshape(-1, 3)[selected_target_indices],
            float(data["frequencies_hz"][frequency_index]),
        )
        reference = data["direct_full_point_action"][:, frequency_index, selected_target_indices]
        direct_errors.append(relative(rebuilt, reference))
    max_direct_error = max(direct_errors)
    require(max_direct_error < 2e-13, "independent direct subset")

    fmm_rows = np.stack(
        [
            integrate(
                data["target_q3_weights_m3"], data["target_q3_global_rt0_test_per_m2"],
                data["fmm_full_point_action"][:, frequency]-data["direct_near_point_action"][:, frequency],
            )
            for frequency in range(3)
        ],
        axis=1,
    )
    direct_rows = np.stack(
        [
            integrate(
                data["target_q3_weights_m3"], data["target_q3_global_rt0_test_per_m2"],
                data["direct_full_point_action"][:, frequency]-data["direct_near_point_action"][:, frequency],
            )
            for frequency in range(3)
        ],
        axis=1,
    )
    row_error = max(
        relative(fmm_rows, data["fmm_far_rt0_rows_h"]),
        relative(direct_rows, data["direct_far_q3_rt0_rows_h"]),
    )
    require(row_error < 2e-15, "saved row integration")

    recomputed = {}
    pairs = {
        "fmm_point": (data["fmm_full_point_action"], data["direct_full_point_action"]),
        "fmm_far_row": (data["fmm_far_rt0_rows_h"], data["direct_far_q3_rt0_rows_h"]),
        "direct_far_q2_q3": (data["direct_far_q2_rt0_rows_h"], data["direct_far_q3_rt0_rows_h"]),
    }
    saved_metric_names = {
        "fmm_point": "fmm_point",
        "fmm_far_row": "fmm_far_row",
        "direct_far_q2_q3": "direct_far_q2_q3",
    }
    metric_error = 0.0
    for label, (actual, reference) in pairs.items():
        recomputed[label] = {kind: np.zeros((2, 3)) for kind in ("full", "real", "imaginary")}
        for rhs in range(2):
            for fi in range(3):
                for kind, left, right in (
                    ("full", actual[rhs, fi], reference[rhs, fi]),
                    ("real", actual[rhs, fi].real, reference[rhs, fi].real),
                    ("imaginary", actual[rhs, fi].imag, reference[rhs, fi].imag),
                ):
                    value = relative(left, right)
                    recomputed[label][kind][rhs, fi] = value
                    saved = data[f"{saved_metric_names[label]}_{kind}_relative"][rhs, fi]
                    metric_error = max(metric_error, abs(value-saved)/max(abs(saved), 1e-300))
    require(metric_error < 2e-12, "reported metric reconstruction")

    frozen_helper = (ROOT/list(PINS)[3]).read_text(encoding="utf-8")
    adapter_route = (
        "zk=complex(wave_number)" in frozen_helper
        and "np.conj(weighted_current)" in frozen_helper
        and "np.conj(np.asarray(answer.pottarg)" in frozen_helper
        and "zk=-" not in frozen_helper.replace(" ", "")
    )
    require(adapter_route, "frozen positive-k conjugate adapter")

    maxima = {
        "face_current_relative": face_error,
        "source_point_relative": point_error,
        "source_weight_relative": weight_error,
        "source_current_relative": current_error,
        "target_rule_relative": target_error,
        "direct_subset_relative": max_direct_error,
        "saved_row_relative": row_error,
        "reported_metric_relative": metric_error,
        "fmm_point_full_relative": float(recomputed["fmm_point"]["full"].max()),
        "fmm_far_row_full_relative": float(recomputed["fmm_far_row"]["full"].max()),
        "direct_far_q2_q3_full_relative": float(recomputed["direct_far_q2_q3"]["full"].max()),
    }
    receipt = {
        "program": "review_astra_boundary_joint_rt0_helmholtz_fmm_far",
        "version": 1,
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": observed,
        "recomputed": maxima,
        "adapter_positive_k_conjugate_route": adapter_route,
        "supports": {
            "source_tetrahedra": 5304,
            "source_q3_points": 143208,
            "target_tetrahedra": target_ids.tolist(),
            "target_local_rt0_rows": 12,
            "complex_mixtures": 2,
            "direct_subset_target_points": selected_target_indices.tolist(),
            "direct_subset_frequencies_hz": [1e3, 1e8],
        },
        "scope": (
            "Saved point-FAR vector action and identical radius-2 point-near subtraction only. "
            "No fmm3d replay, analytic near replacement, scalar/contact/current-charge solve, "
            "all-row operator, mesh convergence, 933-chain, impedance or board accuracy."
        ),
    }
    receipt_path = OUTPUT/"independent-review.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "maxima": maxima}))


if __name__ == "__main__":
    run()
