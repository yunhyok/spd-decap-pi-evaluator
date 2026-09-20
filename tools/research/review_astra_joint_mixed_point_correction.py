"""Strict saved review of the actual-joint mixed point/near correction.

No FMM call is made.  The reviewer independently reconstructs all eight source
channels, the 208 target points, the radius-3 per-target ownership, every
analytic static near correction, the regular retarded reference, and the saved
channel metrics.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import pi
from pathlib import Path

import numpy as np
from numpy.polynomial.legendre import leggauss

from qualify_astra_tetra_volume_green import tetra_inner, triangle_moments


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-joint-mixed-point-correction-review-04"
PINS = {
    "outputs/research/astra-joint-mixed-point-correction-01/result.json": "7f7b3eb021d680495af657d446a961db96963067f80dc395a67e9bf6b6780511",
    "outputs/research/astra-joint-mixed-point-correction-01/mixed-point-action.npz": "3a2f6d5a77d2235474907069ef765b4ba30daeb2842a43c5daa38e8de2184a73",
    "outputs/research/astra-joint-mixed-point-correction-01/driver-at-run.py": "a498bbde49974620abb81e6ace23fb566c0ac022f98bbdb4dde21fef7147c82b",
    "outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz": "331f7a8216681b4c7625970b0b3962e120e3ae0bfb6e0e82b0cabc4ce4921eae",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "tools/research/qualify_astra_tetra_volume_green.py": "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb",
    "tools/research/qualify_astra_tetra_charge_green.py": "aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9",
}
C0 = 299_792_458.0


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def relative(actual: np.ndarray, reference: np.ndarray) -> float:
    return float(np.linalg.norm(actual-reference)/max(float(np.linalg.norm(reference)), 1e-300))


def tetra_rule(vertices: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = leggauss(order)
    x, w = (x+1)/2, w/2
    u, v, t = np.meshgrid(x, x, x, indexing="ij")
    bary = np.column_stack(
        (((1-u)*(1-v)*(1-t)).ravel(), u.ravel(), ((1-u)*v).ravel(), ((1-u)*(1-v)*t).ravel())
    )
    normalized = (6*w[:, None, None]*w[None, :, None]*w[None, None, :]*(1-u)**2*(1-v)).ravel()
    return bary@vertices, normalized


def triangle_rule(vertices: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = leggauss(order)
    x, w = (x+1)/2, w/2
    a, b = np.meshgrid(x, x, indexing="ij")
    bary = np.column_stack(((1-a).ravel()*(1-b).ravel(), a.ravel(), ((1-a)*b).ravel()))
    normalized = (2*w[:, None]*w[None, :]*(1-a)).ravel()
    return bary@vertices, normalized


def measures(tetrahedra: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    volumes = np.abs(np.linalg.det(np.stack((
        tetrahedra[:, 1]-tetrahedra[:, 0],
        tetrahedra[:, 2]-tetrahedra[:, 0],
        tetrahedra[:, 3]-tetrahedra[:, 0],
    ), axis=1)))/6
    areas = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=1)/2
    return volumes, areas


def regular_reference(points: np.ndarray, channels: np.ndarray, targets: np.ndarray, frequency: float) -> np.ndarray:
    wave_number = 2*pi*frequency/C0
    result = np.zeros((len(targets), channels.shape[1]), complex)
    for first in range(0, len(points), 2048):
        last = min(first+2048, len(points))
        distance = np.linalg.norm(targets[:, None]-points[None, first:last], axis=2)
        kernel = np.full(distance.shape, -1j*wave_number, complex)
        np.divide(np.expm1(-1j*wave_number*distance), distance, out=kernel, where=distance != 0)
        result += kernel@channels[first:last]
    return result


def run() -> None:
    require(not OUTPUT.exists(), "fresh review output")
    OUTPUT.mkdir(parents=True)
    observed = {name: digest(ROOT/name) for name in PINS}
    require(observed == PINS, "review input pins")
    result = json.loads((ROOT/list(PINS)[0]).read_text(encoding="utf-8"))
    require(result["status"] == "ACCEPT_ALL_SOURCE_MIXED_NEAR_CORRECTED_POINT_ACTION", "producer status")
    with np.load(ROOT/list(PINS)[1], allow_pickle=False) as archive:
        saved = {name: archive[name] for name in archive.files}
    require(all(np.isfinite(value).all() for value in saved.values()), "finite saved arrays")
    with np.load(ROOT/list(PINS)[3], allow_pickle=False) as archive:
        static = {name: archive[name] for name in archive.files}
    with np.load(ROOT/list(PINS)[4], allow_pickle=False) as mesh:
        vertices = mesh["vertices_local_um"]*1e-6
        cells = mesh["cells"]
        boundary = mesh["boundary_face_ids"]
        face_vertices = mesh["face_vertices"]
    with np.load(ROOT/list(PINS)[5], allow_pickle=False) as space:
        columns = space["local_rt0_face_columns"]
        signs = space["local_rt0_face_signs"]

    tetrahedra = vertices[cells]
    triangles = vertices[face_vertices[boundary]]
    volumes, areas = measures(tetrahedra, triangles)
    currents = static["source_face_currents"]
    charge = static["source_charge_coefficients"]
    local_flux = signs[:, :, None]*currents[columns]
    centers = np.einsum(
        "cir,cid->crd", local_flux, tetrahedra.mean(axis=1)[:, None]-tetrahedra
    )/(3*volumes[:, None, None])
    radial = local_flux.sum(axis=1)/(3*volumes[:, None])
    center_error = relative(centers, static["source_current_centers"])
    radial_error = relative(radial, static["source_current_radial"])
    require(max(center_error, radial_error) < 3e-15, "full affine current coefficients")

    volume_points = []
    volume_normalized_weights = []
    volume_channels = []
    for cell, tetrahedron in enumerate(tetrahedra):
        points, normalized = tetra_rule(tetrahedron, 3)
        current_density = centers[cell][None]+radial[cell][None, :, None]*(points[:, None]-tetrahedron.mean(axis=0))
        volume_points.append(points)
        volume_normalized_weights.append(normalized)
        volume_channels.append(np.column_stack((
            volumes[cell]*normalized[:, None]*current_density.reshape(len(points), 6),
            normalized[:, None]*charge[cell][None],
        )))
    face_points = []
    face_channels = []
    for face, triangle in enumerate(triangles):
        points, normalized = triangle_rule(triangle, 3)
        face_points.append(points)
        face_channels.append(np.column_stack((np.zeros((len(points), 6)), normalized[:, None]*charge[len(tetrahedra)+face][None])))
    points = np.vstack(volume_points+face_points)
    channels = np.vstack(volume_channels+face_channels)
    offsets = np.r_[np.arange(len(tetrahedra)+1)*27, len(tetrahedra)*27+np.arange(1, len(triangles)+1)*9]
    source_point_error = relative(points, saved["source_points_m"])
    source_channel_error = relative(channels, saved["source_weighted_channels"])
    require(source_point_error < 2e-15 and source_channel_error < 4e-15, "eight source channels")
    require(np.array_equal(offsets, saved["source_entity_offsets"]), "source entity offsets")
    require(channels.shape == (178092, 8) and len(offsets) == 9181, "source support counts")

    test_cells = static["tested_current_cell_ids"]
    charge_face = int(static["tested_scalar_boundary_face_ids"][0])
    boundary_slot = int(np.flatnonzero(boundary == charge_face)[0])
    target_parts = [tetra_rule(tetrahedra[cell], 4)[0] for cell in test_cells]
    target_parts.append(triangle_rule(triangles[boundary_slot], 4)[0])
    targets = np.vstack(target_parts)
    target_error = relative(targets, saved["target_points_m"])
    require(target_error < 2e-15 and len(targets) == 208, "target geometry/order")
    static_reference = np.column_stack((
        static["vector_potential_order4"].reshape(len(targets), 6),
        static["scalar_potential_order4"],
    ))
    static_error = relative(static_reference, saved["analytic_static_reference"])
    require(static_error == 0, "pinned complete static reference")

    entities = list(tetrahedra)+list(triangles)
    entity_centers = np.array([entity.mean(axis=0) for entity in entities])
    entity_radii = np.array([
        np.linalg.norm(entity-center, axis=1).max()
        for entity, center in zip(entities, entity_centers, strict=True)
    ])
    length = float(np.ptp(np.vstack((points, targets)), axis=0).max())
    threshold = length*np.finfo(float).eps
    correction = np.zeros((len(targets), 8), complex)
    coincident = np.zeros_like(correction)
    near_pairs = 0
    omitted_pairs = 0
    for entity_index, entity in enumerate(entities):
        target_ids = np.flatnonzero(
            np.linalg.norm(targets-entity_centers[entity_index], axis=1) <= 3*entity_radii[entity_index]
        )
        if not len(target_ids):
            continue
        near_pairs += len(target_ids)
        selected_targets = targets[target_ids]
        origin = entity[0]
        exact = np.zeros((len(target_ids), 8), complex)
        if entity_index < len(tetrahedra):
            potential, moment = tetra_inner(entity-origin, selected_targets-origin)
            centered_moment = moment+(selected_targets-entity.mean(axis=0))*potential[:, None]
            exact[:, :6] = (
                potential[:, None, None]*centers[entity_index][None]
                + centered_moment[:, None, :]*radial[entity_index][None, :, None]
            ).reshape(len(target_ids), 6)
            entity_measure = volumes[entity_index]
        else:
            potential = triangle_moments(entity-origin, selected_targets-origin)[0]
            entity_measure = areas[entity_index-len(tetrahedra)]
        exact[:, 6:] = potential[:, None]*(charge[entity_index]/entity_measure)[None]
        first, last = offsets[entity_index:entity_index+2]
        distance = np.linalg.norm(selected_targets[:, None]-points[None, first:last], axis=2)
        nonzero = distance > threshold
        inverse = np.divide(1.0, distance, out=np.zeros_like(distance), where=nonzero)
        correction[target_ids] += exact-inverse@channels[first:last]
        omitted_pairs += int(np.count_nonzero(~nonzero))
        coincident[target_ids] += (~nonzero)@channels[first:last]
    correction_error = relative(correction, saved["static_correction"])
    coincidence_error = relative(coincident, saved["coincident_weighted_density"])
    print(json.dumps({"static_near_correction_relative": correction_error,
                      "coincident_weighted_density_relative": coincidence_error}), flush=True)
    require(correction_error < 1e-14 and coincidence_error == 0, "near correction reconstruction")
    require(near_pairs == 305781 and omitted_pairs == 0, "per-target near/coincident counts")

    frequency = float(result["frequency_hz"])
    regular = regular_reference(points, channels, targets, frequency)
    regular_error = relative(regular, saved["direct_regular_reference"])
    complete_reference = static_reference+regular
    reference_error = relative(complete_reference, saved["complete_reference"])
    wave_number = 2*pi*frequency/C0
    corrected = saved["point_action"]+correction-1j*wave_number*coincident
    corrected_error = relative(corrected, saved["corrected_action"])
    print(json.dumps({"direct_regular_relative": regular_error,
                      "complete_reference_relative": reference_error,
                      "corrected_action_relative": corrected_error}), flush=True)
    require(regular_error < 2e-12, "weak regular-remainder reconstruction")
    require(max(reference_error, corrected_error) < 1e-14, "complete reference/correction algebra")

    relative_by_channel = np.linalg.norm(corrected-complete_reference, axis=0)/np.linalg.norm(complete_reference, axis=0)
    uncorrected_by_channel = np.linalg.norm(saved["point_action"]-complete_reference, axis=0)/np.linalg.norm(complete_reference, axis=0)
    metric_relative_error = max(
        relative(relative_by_channel, saved["relative_error_by_channel"]),
        relative(uncorrected_by_channel, saved["uncorrected_error_by_channel"]),
    )
    metric_absolute_error = max(
        float(np.max(np.abs(relative_by_channel-saved["relative_error_by_channel"]))),
        float(np.max(np.abs(uncorrected_by_channel-saved["uncorrected_error_by_channel"]))),
    )
    require(metric_absolute_error < 2e-14, "channel metrics")
    require(float(relative_by_channel.max()) < result["fixed_relative_gate"], "producer fixed gate")

    driver = (ROOT/list(PINS)[2]).read_text(encoding="utf-8")
    formula_checks = {
        "positive_k_conjugated_fmm": all(token in driver for token in (
            "zk=complex(k)", "charges=np.asfortranarray(channels.conj().T)", "np.asarray(answer.pottarg).conj()",
        )),
        "static_near_replacement_sign": "correction[ids] += exact-inverse @ channels" in driver,
        "finite_minus_ik_coincident_remainder": "corrected = point_action+correction-1j*k*coincident" in driver,
        "regular_zero_distance_limit": "kernel = np.full(distance.shape, -1j*k, complex)" in driver,
    }
    require(all(formula_checks.values()), "frozen formula contract")

    receipt = {
        "program": "review_astra_joint_mixed_point_correction",
        "version": 1,
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": observed,
        "recomputed": {
            "source_point_relative": source_point_error,
            "source_channel_relative": source_channel_error,
            "current_center_relative": center_error,
            "current_radial_relative": radial_error,
            "target_point_relative": target_error,
            "static_reference_relative": static_error,
            "static_near_correction_relative": correction_error,
            "direct_regular_relative": regular_error,
            "complete_reference_relative": reference_error,
            "corrected_action_relative": corrected_error,
            "reported_metric_relative": metric_relative_error,
            "reported_metric_absolute": metric_absolute_error,
            "near_point_entity_pairs": near_pairs,
            "omitted_point_pairs": omitted_pairs,
            "maximum_channel_relative_error": float(relative_by_channel.max()),
            "maximum_uncorrected_channel_relative_error": float(uncorrected_by_channel.max()),
        },
        "formula_checks": formula_checks,
        "supports": {
            "current_volumes": 5304,
            "charge_volumes": 5304,
            "charge_faces": 3876,
            "source_points": len(points),
            "target_points": len(targets),
            "channels": ["J_smooth_x", "J_smooth_y", "J_smooth_z", "J_fine_x", "J_fine_y", "J_fine_z", "q_from_smooth", "q_from_fine"],
        },
        "scope": (
            "Matched q3 source and q4 target point action at 100 MHz with source-radius-3 analytic "
            "static near replacement. The review recomputes all source channels, all near corrections "
            "and the full direct regular remainder without calling FMM. Coincident count is zero, so "
            "the saved run checks the sign/algebra but not a nonzero finite -ik diagonal restoration. "
            "No outer quadrature convergence, all-row operator, physical charge/contact relation, "
            "field solve, 933-chain or board accuracy is approved."
        ),
    }
    path = OUTPUT/"independent-review.json"
    path.write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "recomputed": receipt["recomputed"]}))


if __name__ == "__main__":
    run()
