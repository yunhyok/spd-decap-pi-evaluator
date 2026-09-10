"""Exact same-triangle RT0 partial L via three edge integrals; research only."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.integrate import quad_vec

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "stress": (ROOT/"outputs/research/astra-l25-shape-kernel-stress-01/result.json", "c4450535687d9d69e8215179e81a85355e67cb285c711b0c3cc87165bf765120"),
    "refined": (ROOT/"outputs/research/astra-l25-sliver-kernel-refined-01/result.json", "cb2751c17ed1233ca9ed5673011379ba83ec1e73c3b71301161faa91ea09200a"),
}
MU_OVER_4PI = 1e-7


def triangle_self_inductance(vertices, *, numerical_edge_reference=False):
    """3x3 H for w_i=(r-v_i)/(2A), without prescribing facet-current signs.

    The three positive difference cones d=s(v_i-B(t)) and their negatives
    partition T-T. Their overlap area is A(1-s)^2, with means
    (1-s)c+sB and (1-s)c+s v_i and covariance trace (1-s)^2 S.
    Jacobian/kernel/basis factors reduce to 1/2 times (1-s)^2/|v_i-B|.
    Integrating s gives H=P0/3+P1/12+P2/30, affine in t. The negative
    cone is its transpose; adding it is the exact domain decomposition.
    """
    vertices = np.asarray(vertices, dtype=float)
    if vertices.shape != (3, 2) or not np.all(np.isfinite(vertices)):
        raise ValueError("three finite planar vertices required")
    vertices = vertices-vertices[0]
    length = float(np.max(np.linalg.norm(vertices[[1, 2, 0]]-vertices, axis=1)))
    if not np.isfinite(length) or length <= 0:
        raise ValueError("positive finite triangle length required")
    vertices = vertices/length
    twice_area = abs(vertices[1, 0]*vertices[2, 1]-vertices[1, 1]*vertices[2, 0])
    if not np.isfinite(twice_area) or twice_area <= 0:
        raise ValueError("nondegenerate triangle required")
    center = vertices.mean(axis=0)
    x = center-vertices
    covariance_trace = np.sum(x*x)/12
    p0 = x@x.T+covariance_trace
    result = np.zeros((3, 3))
    for i, j, k in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        edge = vertices[k]-vertices[j]
        edge_length = float(np.linalg.norm(edge))
        along = float((vertices[j]-vertices[i])@(edge/edge_length))
        altitude = twice_area/edge_length
        if not np.isfinite(altitude) or altitude*altitude <= 0:
            raise ValueError("edge altitude underflow")
        h_values = []
        for end in (vertices[j], vertices[k]):
            u, z = end-center, vertices[i]-center
            p1 = (x@u)[None, :]+(x@z)[:, None]-2*covariance_trace
            p2 = float(u@z)+covariance_trace
            h_values.append(p0/3+p1/12+p2/30)
        h0, delta_h = h_values[0], h_values[1]-h_values[0]
        if numerical_edge_reference:
            # ponytail: adaptive 1D oracle only; the production candidate uses the closed integrals below.
            closest = float(np.clip(-along/edge_length, 0, 1))
            integral, _, info = quad_vec(
                lambda t: (h0+t*delta_h)/np.hypot(altitude, along+t*edge_length),
                0., 1., epsabs=1e-12, epsrel=1e-11, points=[closest], full_output=True,
            )
            if not info.success:
                raise ValueError(f"edge reference did not converge: {info.message}")
        else:
            f0 = (np.arcsinh((along+edge_length)/altitude)-np.arcsinh(along/altitude))/edge_length
            radius_sum = np.hypot(altitude, along+edge_length)+np.hypot(altitude, along)
            f1 = ((2*along+edge_length)/radius_sum-along*f0)/edge_length
            integral = h0*f0+delta_h*f1
        result += .5*(integral+integral.T)
    return MU_OVER_4PI*length*result


def run_probe():
    started = perf_counter()
    for path, sha in PINS.values():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError(f"input hash mismatch: {path}")
    stress = json.loads(PINS["stress"][0].read_text(encoding="utf-8"))
    refined = json.loads(PINS["refined"][0].read_text(encoding="utf-8"))
    if stress["status"] != "STOP_L25_SHAPE_KERNEL_GATE" or refined["status"] != "ACCEPT_L25_SLIVER_REFINED_KERNEL_ONLY":
        raise ValueError("expected preserved stress STOP and accepted refinement")
    cases = []
    angle = .731
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    for case in stress["cases"]:
        vertices = np.array(case["vertices_m"])
        reference = np.array(refined["self_partial_L_256_h"] if case["triangle_index"] == 457250 else case["self_partial_L_h"])
        local = vertices-vertices[0]
        value = triangle_self_inductance(vertices)
        scale = np.linalg.norm(value)
        permutations = []
        for permutation in itertools.permutations(range(3)):
            indices = list(permutation)
            permuted = triangle_self_inductance(local[indices])
            permutations.append(np.linalg.norm(permuted-value[np.ix_(indices, indices)])/scale)
        edge_reference = triangle_self_inductance(vertices, numerical_edge_reference=True)
        metrics = {
            "saved_outer_quadrature_relative_difference": float(np.linalg.norm(value-reference)/np.linalg.norm(reference)),
            "independent_edge_quadrature_relative_difference": float(np.linalg.norm(value-edge_reference)/scale),
            "all_vertex_permutations_relative_difference": float(max(permutations)),
            "translation_relative_difference": float(np.linalg.norm(triangle_self_inductance(local)-value)/scale),
            "rotation_relative_difference": float(np.linalg.norm(triangle_self_inductance(local@rotation)-value)/scale),
            "length_scaling_relative_difference": float(max(np.linalg.norm(triangle_self_inductance(local*factor)/factor-value)/scale for factor in (1e-6, 1e6))),
            "minimum_eigenvalue_h": float(np.linalg.eigvalsh(value)[0]),
        }
        gates = {key: bool(np.isfinite(metric) and (metric > 0 if key == "minimum_eigenvalue_h" else metric < (5e-5 if key.startswith("saved_outer") else 1e-9))) for key, metric in metrics.items()}
        cases.append(dict(triangle_index=case["triangle_index"], vertices_m=vertices.tolist(), self_partial_L_h=value.tolist(), reference_order=256 if case["triangle_index"] == 457250 else 64, metrics=metrics, gates=gates, passed=all(gates.values())))
    rejected = 0
    for invalid in (np.zeros((3, 2)), [[0, 0], [1, 0], [2, 0]], [[0, 0], [1, 0], [0, np.nan]]):
        try:
            triangle_self_inductance(invalid)
        except ValueError:
            rejected += 1
    return dict(program="SPD Decap PI Evaluator", version="0.23.1", status="ACCEPT_THREE_SHAPE_CLOSED_SELF_L_ONLY" if all(case["passed"] for case in cases) and rejected == 3 else "STOP_CLOSED_SELF_L_GATE", inputs={key: {"path": str(path), "sha256": sha} for key, (path, sha) in PINS.items()}, cases=cases, invalid_inputs_rejected=rejected, elapsed_s=perf_counter()-started, scope="Three actual source shapes: exact zero-thickness static same-triangle RT0 kernel reduction, independently compared with saved outer integration and adaptive edge quadrature. No inter-triangle mutual kernel, full-board assembly/solve, finite-thickness/internal/return composition, speedup benchmark, or PowerSI accuracy claim.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    source = Path(__file__).read_bytes()
    (args.output/"driver-at-run.py").write_bytes(source)
    report = run_probe()
    report["script_sha256"] = hashlib.sha256(source).hexdigest()
    (args.output/"result.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False))
