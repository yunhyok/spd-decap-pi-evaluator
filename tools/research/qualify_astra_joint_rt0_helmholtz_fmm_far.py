"""SPD Decap PI Evaluator v0.23.1: actual-joint far RT0 Helmholtz FMM control.

This bounded control uses separated tetrahedra from the conforming source joint.
It qualifies only the full-complex far vector-kernel application. Singular self
and near-pair ownership, scalar charge coupling, terminals, and a field solve are
deliberately outside this artifact.
"""
from hashlib import sha256
import argparse
import json
from math import pi
import os
from pathlib import Path
import sys
from time import monotonic
import traceback


ROOT = Path(__file__).resolve().parents[2]
FMM_RUNTIME = ROOT / "outputs/research-fmm-runtime"
sys.path[:0] = [str(FMM_RUNTIME), str(ROOT / "outputs/research-runtime"), str(Path(__file__).resolve().parent)]

import fmm3dpy  # noqa: E402
import numpy as np  # noqa: E402
import qualify_astra_tetra_volume_green as static  # noqa: E402


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
MU0 = 4e-7 * pi
EPS0 = 8.8541878128e-12
FMM_EPS = 1e-12
PINS = {
    "outputs/research/astra-fmm3d-runtime-01/result.json": "1b3f1d2b04b67eb449be6267dc6ea5ec3e8fedff16c5b9c79c9620a6069e27c2",
    "outputs/research/astra-fmm3d-runtime-01/driver-at-run.py": "d753db51643a07114e10ed79694d06ef7eef33a28d63ac27ab587af75365642e",
    "tools/research/qualify_astra_tetra_volume_green.py": "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb",
    "outputs/research/astra-conforming-power-joint-01/joint-template.npz": "6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c",
}


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_runtime():
    observed = {name: digest(ROOT / name) for name in PINS}
    require(observed == PINS, "input pin mismatch")
    receipt = json.loads((ROOT / "outputs/research/astra-fmm3d-runtime-01/result.json").read_text(encoding="utf-8"))
    require(receipt["status"] == "ACCEPT_FMM3D_POINT_KERNEL_ONLY", "FMM runtime status")
    require(fmm3dpy.__version__ == "2.1.0", "FMM version")
    require(Path(fmm3dpy.__file__).resolve().parent == FMM_RUNTIME / "fmm3dpy", "FMM runtime path")
    for relative, expected in receipt["runtime_files"].items():
        require(digest(FMM_RUNTIME / relative) == expected, "FMM runtime file " + relative)
    wheel = Path(receipt["wheel"]["path"])
    require(digest(wheel) == receipt["wheel"]["sha256"], "FMM wheel pin")
    return observed, receipt


def choose_cells(tetrahedra_um, body, volumes_um3):
    centroids = tetrahedra_um.mean(axis=1)
    selected = []
    for body_id, split, direction in ((0, 0.0, -1), (1, 130.0, 1)):
        for low, high in ((0.0, 25.0), (25.0, 55.0), (55.0, 75.000001)):
            in_band = (centroids[:, 2] >= low) & (centroids[:, 2] < high)
            if direction < 0:
                outer_half = tetrahedra_um[:, :, 0].max(axis=1) <= split + 1e-12
            else:
                outer_half = tetrahedra_um[:, :, 0].min(axis=1) >= split - 1e-12
            candidates = np.flatnonzero((body == body_id) & in_band & outer_half)
            require(candidates.size >= 2, "far subset candidates")
            order = np.lexsort((candidates, -volumes_um3[candidates]))
            selected.extend(candidates[order[:2]].tolist())
    return np.asarray(selected[:6], dtype=np.int64), np.asarray(selected[6:], dtype=np.int64)


def quadrature_data(tetrahedra, order):
    points = []
    weights = []
    values = []
    spans = []
    moments = []
    centroid_values = []
    for tetra in tetrahedra:
        start = len(points)
        volume, _ = static.faces(tetra)
        p, w = static.tetra_quadrature(tetra, order)
        basis = (p[:, None, :] - tetra[None, :, :]) / (3 * volume)
        points.extend(p)
        weights.extend(w)
        values.append(basis)
        spans.append(slice(start, start + len(p)))
        moments.append((tetra.mean(axis=0)[None, :] - tetra) / 3)
        centroid_values.append((tetra.mean(axis=0)[None, :] - tetra) / (3 * volume))
    points = np.asarray(points)
    weights = np.asarray(weights)
    moments = np.asarray(moments)
    numerical_moments = np.asarray(
        [np.einsum("p,pid->id", weights[span], basis) for span, basis in zip(spans, values, strict=True)]
    )
    moment_error = float(np.linalg.norm(numerical_moments - moments) / np.linalg.norm(moments))
    dofs = 4 * len(tetrahedra)
    weighted = np.zeros((dofs, 3, len(points)))
    for cell, (span, basis) in enumerate(zip(spans, values, strict=True)):
        weighted[4 * cell : 4 * cell + 4, :, span] = np.einsum("p,pid->idp", weights[span], basis)
    variation = max(
        float(np.max(np.linalg.norm(basis - center[None, :, :], axis=2)))
        for basis, center in zip(values, centroid_values, strict=True)
    )
    return {
        "points": points,
        "weighted": weighted,
        "moments": moments.reshape(dofs, 3),
        "moment_error": moment_error,
        "maximum_affine_basis_variation_per_m2": variation,
        "centroids": tetrahedra.mean(axis=1),
    }


def dense_block(target, source, wave_number):
    delta = target["points"][:, None, :] - source["points"][None, :, :]
    distance = np.linalg.norm(delta, axis=2)
    require(float(distance.min()) > 0, "separated source and target points")
    kernel = (1.0 + np.expm1(-1j * wave_number * distance)) / (4 * pi * distance)
    source_weighted = source["weighted"]
    potentials = source_weighted.reshape(-1, source_weighted.shape[2]) @ kernel.T
    potentials = potentials.reshape(source_weighted.shape[0], 3, target["points"].shape[0])
    block = MU0 * np.einsum("idp,jdp->ij", target["weighted"], potentials)
    return block


def fmm_block(target, source, wave_number):
    source_weighted = source["weighted"]
    charges = np.asfortranarray(source_weighted.reshape(-1, source_weighted.shape[2]).astype(complex))
    answer = fmm3dpy.hfmm3d(
        eps=FMM_EPS,
        zk=complex(-wave_number),
        sources=np.asfortranarray(source["points"].T),
        charges=charges,
        targets=np.asfortranarray(target["points"].T),
        pgt=1,
        nd=charges.shape[0],
    )
    require(answer.ier == 0, "hfmm3d ier")
    require(np.isfinite(answer.pottarg).all(), "hfmm3d finite")
    potentials = np.asarray(answer.pottarg).reshape(source_weighted.shape[0], 3, target["points"].shape[0])
    return MU0 * np.einsum("idp,jdp->ij", target["weighted"], potentials)


def centroid_block(target, source, wave_number):
    distance = np.linalg.norm(target["centroids"][:, None, :] - source["centroids"][None, :, :], axis=2)
    kernel = (1.0 + np.expm1(-1j * wave_number * distance)) / (4 * pi * distance)
    local = MU0 * np.einsum(
        "aid,ab,bjd->aibj",
        target["moments"].reshape(-1, 4, 3),
        kernel,
        source["moments"].reshape(-1, 4, 3),
    )
    return local.reshape(target["moments"].shape[0], source["moments"].shape[0])


def relative(actual, reference):
    return float(np.linalg.norm(actual - reference) / np.linalg.norm(reference))


def self_check():
    sources = np.array([[0.0, 0.0, 0.0], [2e-4, 0.0, 0.0]]).T
    targets = np.array([[1e-4, 1e-4, 0.0], [3e-4, -1e-4, 0.0]]).T
    charges = np.array([1.0 + 0.25j, -0.3 + 0.1j])
    wave_number = 1.75
    out = fmm3dpy.hfmm3d(
        eps=FMM_EPS, zk=-wave_number, sources=sources, charges=charges, targets=targets, pgt=1
    )
    distance = np.linalg.norm(targets.T[:, None, :] - sources.T[None, :, :], axis=2)
    exact = ((1 + np.expm1(-1j * wave_number * distance)) / (4 * pi * distance)) @ charges
    require(out.ier == 0 and relative(out.pottarg, exact) < 1e-9, "Helmholtz sign/normalization self-check")
    print("PASS_ACTUAL_JOINT_RT0_HELMHOLTZ_FMM_FAR_SELF_CHECK")


def run(output):
    started = monotonic()
    deadline = started + 60
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        observed, runtime = verify_runtime()
        self_check()
        with np.load(ROOT / "outputs/research/astra-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as z:
            vertices_um = z["vertices_local_um"]
            cells = z["cells"]
            body = z["cell_body"]
            volumes_um3 = z["cell_volume_um3"]
        all_tetrahedra_um = vertices_um[cells]
        source_ids, target_ids = choose_cells(all_tetrahedra_um, body, volumes_um3)
        source_tetrahedra = all_tetrahedra_um[source_ids] * 1e-6
        target_tetrahedra = all_tetrahedra_um[target_ids] * 1e-6
        vertex_distance = np.linalg.norm(
            target_tetrahedra[:, None, :, None, :] - source_tetrahedra[None, :, None, :, :], axis=4
        )
        minimum_separation = float(vertex_distance.min())
        maximum_diameter = float(
            max(
                np.linalg.norm(t[:, None, :] - t[None, :, :], axis=2).max()
                for t in np.concatenate((source_tetrahedra, target_tetrahedra))
            )
        )
        require(minimum_separation > 2 * maximum_diameter, "selected pairs are not far separated")

        orders = (3, 5)
        frequencies = np.array((1e3, 1e6, 1e8))
        wave_numbers = 2 * pi * frequencies * np.sqrt(MU0 * EPS0)
        data = {}
        for order in orders:
            data[("source", order)] = quadrature_data(source_tetrahedra, order)
            data[("target", order)] = quadrature_data(target_tetrahedra, order)
        moment_error = max(item["moment_error"] for item in data.values())
        affine_variation = max(item["maximum_affine_basis_variation_per_m2"] for item in data.values())
        require(moment_error < 1e-13 and affine_variation > 0, "affine RT0 quadrature")

        direct_blocks = np.empty((len(frequencies), len(orders), 24, 24), complex)
        fmm_blocks = np.empty_like(direct_blocks)
        reverse_fmm_blocks = np.empty_like(direct_blocks)
        cases = []
        for fi, (frequency, wave_number) in enumerate(zip(frequencies, wave_numbers, strict=True)):
            for oi, order in enumerate(orders):
                require(monotonic() < deadline, "far FMM control deadline")
                source = data[("source", order)]
                target = data[("target", order)]
                direct = dense_block(target, source, wave_number)
                fmm = fmm_block(target, source, wave_number)
                reverse = fmm_block(source, target, wave_number)
                direct_blocks[fi, oi] = direct
                fmm_blocks[fi, oi] = fmm
                reverse_fmm_blocks[fi, oi] = reverse
                full_error = relative(fmm, direct)
                imaginary_error = relative(fmm.imag, direct.imag)
                reciprocity = relative(fmm, reverse.T)
                imaginary_reciprocity = relative(fmm.imag, reverse.T.imag)
                cases.append(
                    {
                        "frequency_hz": float(frequency),
                        "quadrature_order": order,
                        "source_points": int(source["points"].shape[0]),
                        "target_points": int(target["points"].shape[0]),
                        "fmm_vs_dense_full_relative": full_error,
                        "fmm_vs_dense_imaginary_relative": imaginary_error,
                        "fmm_bilinear_reciprocity_relative": reciprocity,
                        "fmm_imaginary_reciprocity_relative": imaginary_reciprocity,
                    }
                )

        frequency_checks = []
        for fi, (frequency, wave_number) in enumerate(zip(frequencies, wave_numbers, strict=True)):
            low, high = direct_blocks[fi]
            target = data[("target", orders[-1])]
            source = data[("source", orders[-1])]
            leading = -1j * MU0 * wave_number / (4 * pi) * (target["moments"] @ source["moments"].T)
            centroid = centroid_block(target, source, wave_number)
            frequency_checks.append(
                {
                    "frequency_hz": float(frequency),
                    "maximum_kR": float(wave_number * vertex_distance.max()),
                    "q3_to_q5_full_relative": relative(low, high),
                    "q3_to_q5_imaginary_relative": relative(low.imag, high.imag),
                    "exact_minus_ik_leading_imaginary_relative": relative(high.imag, leading.imag),
                    "centroid_constant_kernel_relative_difference": relative(centroid, high),
                }
            )

        maximum_full_fmm = max(case["fmm_vs_dense_full_relative"] for case in cases)
        maximum_imaginary_fmm = max(case["fmm_vs_dense_imaginary_relative"] for case in cases)
        maximum_full_reciprocity = max(case["fmm_bilinear_reciprocity_relative"] for case in cases)
        maximum_imaginary_reciprocity = max(case["fmm_imaginary_reciprocity_relative"] for case in cases)
        maximum_quadrature = max(item["q3_to_q5_full_relative"] for item in frequency_checks)
        maximum_imaginary_quadrature = max(item["q3_to_q5_imaginary_relative"] for item in frequency_checks)
        maximum_leading = max(item["exact_minus_ik_leading_imaginary_relative"] for item in frequency_checks)
        gates = {
            "runtime_pins": True,
            "actual_joint_far_separation": minimum_separation > 2 * maximum_diameter,
            "affine_rt0_volume_moments": moment_error < 1e-13,
            "fmm_vs_same_quadrature_full": maximum_full_fmm < 1e-8,
            "fmm_vs_same_quadrature_imaginary": maximum_imaginary_fmm < 2e-3,
            "fmm_full_bilinear_reciprocity": maximum_full_reciprocity < 1e-8,
            "fmm_imaginary_bilinear_reciprocity": maximum_imaginary_reciprocity < 2e-3,
            "far_quadrature_full_convergence": maximum_quadrature < 1e-5,
            "far_quadrature_imaginary_convergence": maximum_imaginary_quadrature < 1e-5,
            "stable_exact_minus_ik_leading_term": maximum_leading < 1e-6,
        }
        artifact = output / "far-blocks.npz"
        with artifact.open("xb") as stream:
            np.savez_compressed(
                stream,
                source_cell_ids=source_ids,
                target_cell_ids=target_ids,
                source_tetrahedra_m=source_tetrahedra,
                target_tetrahedra_m=target_tetrahedra,
                frequencies_hz=frequencies,
                wave_numbers_per_m=wave_numbers,
                quadrature_orders=np.asarray(orders),
                direct_far_blocks_h=direct_blocks,
                fmm_far_blocks_h=fmm_blocks,
                reverse_fmm_far_blocks_h=reverse_fmm_blocks,
                source_exact_rt0_volume_moments_m=data[("source", orders[-1])]["moments"],
                target_exact_rt0_volume_moments_m=data[("target", orders[-1])]["moments"],
            )
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "PASS_ACTUAL_JOINT_FAR_RT0_HELMHOLTZ_FMM_CONTROL" if all(gates.values()) else "STOP_ACTUAL_JOINT_FAR_RT0_HELMHOLTZ_FMM_CONTROL",
            "driver_sha256": digest(Path(__file__)),
            "artifact_sha256": digest(artifact),
            "pins": observed,
            "runtime_version": runtime["fmm3d_version"],
            "kernel_contract": "fmm3dpy.hfmm3d exp(+i*zk*R)/(4*pi*R), called with zk=-k for exp(-i*k*R)",
            "fmm_requested_relative_precision": FMM_EPS,
            "source_cell_ids": source_ids.tolist(),
            "target_cell_ids": target_ids.tolist(),
            "minimum_source_target_vertex_separation_m": minimum_separation,
            "maximum_selected_tetrahedron_diameter_m": maximum_diameter,
            "minimum_separation_to_maximum_diameter": minimum_separation / maximum_diameter,
            "maximum_affine_basis_variation_per_m2": affine_variation,
            "maximum_rt0_volume_moment_relative_error": moment_error,
            "cases": cases,
            "frequency_checks": frequency_checks,
            "maxima": {
                "fmm_vs_dense_full_relative": maximum_full_fmm,
                "fmm_vs_dense_imaginary_relative": maximum_imaginary_fmm,
                "fmm_full_reciprocity_relative": maximum_full_reciprocity,
                "fmm_imaginary_reciprocity_relative": maximum_imaginary_reciprocity,
                "q3_to_q5_full_relative": maximum_quadrature,
                "q3_to_q5_imaginary_relative": maximum_imaginary_quadrature,
                "exact_minus_ik_leading_imaginary_relative": maximum_leading,
            },
            "gates": gates,
            "elapsed_s": monotonic() - started,
            "scope": (
                "Six source and six target tetrahedra are deterministic, well-separated subsets of the actual "
                "conforming two-post joint. Affine radial RT0 basis values are integrated at every quadrature "
                "point. This qualifies only a full-complex far magnetic vector-kernel application and preserves "
                "the exact -ik leading term; the centroid approximation is diagnostic only. Self/face/edge/vertex "
                "near pairs require separately owned exact corrections. No scalar charge/contact equation, field "
                "solve, isolated boundary condition, 933-chain assembly, impedance, or board/PowerSI accuracy is qualified."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"]}))
        return 0 if all(gates.values()) else 2
    except Exception:
        (output / "failure.json").write_text(
            json.dumps({"traceback": traceback.format_exc(), "elapsed_s": monotonic() - started}, indent=2) + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        verify_runtime()
        self_check()
    elif args.output:
        raise SystemExit(run(args.output.resolve()))
    else:
        parser.error("choose --self-check or --output")
