"""Bounded scalar/linear triangle 1/R kernel probe; no board or magnetic solve."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.integrate import quad_vec

SOURCE = "https://iris.polito.it/retrieve/e384c433-3202-d4b2-e053-9f05fe0a1d67/09099568.pdf"
TOL = 1e-10


def triangle_potential(vertices, observation_xy, height):
    """Return integral[1, x'-x, y'-y]/R dA using Wilton2020 equations9–11.

    Coordinates are metres. Entries have units m, m², m². The vector moment
    is relative to the observation projection, not to a source vertex.
    """
    vertices = np.asarray(vertices, dtype=float)
    point = np.asarray(observation_xy, dtype=float)
    if vertices.shape != (3, 2) or point.shape != (2,) or not np.all(np.isfinite(vertices)) or not np.all(np.isfinite(point)) or not np.isfinite(height):
        raise ValueError("finite planar triangle and observation required")
    edges = vertices[[1, 2, 0]] - vertices
    determinant = np.linalg.det(np.stack((edges[0], -edges[2])))
    if determinant == 0 or not np.isfinite(determinant):
        raise ValueError("degenerate triangle")
    result = np.zeros(3)
    d = abs(float(height))
    for start, edge in zip(vertices, edges, strict=True):
        tangent = edge / np.linalg.norm(edge)
        normal = np.sign(determinant) * np.array((tangent[1], -tangent[0]))
        h = float((start - point) @ normal)
        lower = float((start - point) @ tangent)
        upper = lower + float(np.linalg.norm(edge))
        r0 = np.hypot(h, d)
        if r0 == 0:
            continue  # Exact in-plane edge/vertex limit of all three differences.
        values = []
        for ell in (lower, upper):
            p = np.hypot(h, ell)
            radius = np.hypot(r0, ell)
            a = np.arcsinh(ell / r0)
            if d == 0:
                d_cardinal = 0.0
            elif p == 0:
                d_cardinal = d
            else:
                x = p / d
                cardinal = (1 - x*x/6 + 3*x**4/40 - 5*x**6/112 + 35*x**8/1152) if x < 1e-2 else np.arcsinh(x) / x
                d_cardinal = d * cardinal
            scalar = h*a - d*np.arctan(h*ell / (r0*r0 + d*radius))
            u_moment = 0.5*(r0*r0*a - ell*d_cardinal)
            ell_moment = 0.5*h*(radius + d_cardinal)
            values.append(np.r_[scalar, normal*u_moment + tangent*ell_moment])
        result += values[1] - values[0]
    return result


def duffy_reference(vertices, point, height):
    """Independent signed radial subtriangle integration; adaptive angular quad."""
    length = float(np.max(np.linalg.norm(vertices[[1, 2, 0]]-vertices, axis=1)))
    origin = vertices[0].copy()
    vertices, point, height = (vertices-origin)/length, (point-origin)/length, height/length
    orientation = np.sign(np.linalg.det(np.stack((vertices[1]-vertices[0], vertices[2]-vertices[0]))))
    total = np.zeros(3)
    for a, b in zip(vertices - point, vertices[[1, 2, 0]] - point, strict=True):
        determinant = orientation * (a[0]*b[1] - a[1]*b[0])
        if determinant == 0:
            continue
        def angular(s):
            vector = (1-s)*a + s*b
            rho = np.linalg.norm(vector)
            d = abs(height)
            radius = np.hypot(rho, d)
            radial0 = 1/(radius+d)
            radial1 = 1/(2*rho) if d == 0 else (radius-d*d*np.arcsinh(rho/d)/rho)/(2*rho*rho)
            return determinant*np.r_[radial0, vector*radial1]
        value, error, info = quad_vec(angular, 0, 1, epsabs=1e-13, epsrel=1e-12, full_output=True)
        if not info.success or not np.all(np.isfinite(value)) or not np.isfinite(error):
            raise ValueError("independent integration failed")
        total += value
    return total*np.array((length, length*length, length*length))


def parent_reference(vertices, point, height):
    """Direct parent-triangle tensor quadrature for separated observations."""
    nodes, weights = leggauss(48)
    nodes, weights = (nodes+1)/2, weights/2
    u, v = np.meshgrid(nodes, nodes, indexing="ij")
    w = np.outer(weights, weights)
    a, b = vertices[1]-vertices[0], vertices[2]-vertices[0]
    delta = vertices[0] + u[..., None]*a + ((1-u)*v)[..., None]*b - point
    factor = abs(a[0]*b[1]-a[1]*b[0])*(1-u)*w / np.sqrt(np.sum(delta*delta, axis=-1)+height*height)
    return np.r_[np.sum(factor), np.sum(delta*factor[..., None], axis=(0, 1))]


def run_probe():
    started = perf_counter()
    vertices = np.array(((0., 0.), (1., 0.), (0.2, 0.7)))
    observations = [
        ("interior_self", (0.3, 0.2), 0.),
        ("edge_self", (0.5, 0.), 0.),
        ("vertex_self", (0., 0.), 0.),
        ("outside_in_plane", (1.5, 0.3), 0.),
        ("edge_extension", (1.5, 0.), 0.),
        ("near_above", (0.3, 0.2), 1e-6),
        ("near_below", (0.3, 0.2), -1e-6),
        ("separated", (0.3, 0.2), 0.2),
        ("far", (2., 1.), 2.),
    ]
    cases = []
    def errors(actual, reference, triangle):
        length = float(np.max(np.linalg.norm(triangle[[1, 2, 0]]-triangle, axis=1)))
        # Scalar denominator is m; moment denominator is m², including zero moments.
        scalar_scale = abs(reference[0])
        moment_scale = max(length*scalar_scale, np.linalg.norm(reference[1:]))
        return {"scalar_relative": float(abs(actual[0]-reference[0])/scalar_scale),
                "moment_scaled_relative": float(np.linalg.norm(actual[1:]-reference[1:])/moment_scale)}
    for name, xy, height in observations:
        point = np.array(xy)
        actual = triangle_potential(vertices, point, height)
        reference = duffy_reference(vertices, point, height)
        error = errors(actual, reference, vertices)
        transformations = [
            ("winding", vertices[[0, 2, 1]], point, height, 1.),
            ("translation", vertices+np.array((3., -2.)), point+np.array((3., -2.)), height, 1.),
            ("micrometre", vertices*1e-6, point*1e-6, height*1e-6, 1e-6),
            ("height_reflection", vertices, point, -height, 1.),
        ]
        controls = []
        for label, tri, obs, d, factor in transformations:
            transformed = triangle_potential(tri, obs, d)
            oracle = duffy_reference(tri, obs, d)
            back = transformed/np.array((factor, factor*factor, factor*factor))
            controls.append({"name": label, "independent_reference_errors": errors(transformed, oracle, tri), "invariance_errors": errors(back, actual, vertices)})
        parent_error = {}
        if name in ("separated", "far"):
            parent_error = errors(actual, parent_reference(vertices, point, height), vertices)
        all_errors = list(error.values())+list(parent_error.values())
        for control in controls:
            all_errors.extend(control["independent_reference_errors"].values())
            all_errors.extend(control["invariance_errors"].values())
        passed = bool(np.all(np.isfinite(actual)) and actual[0] > 0 and all(np.isfinite(e) and e < TOL for e in all_errors))
        cases.append(dict(name=name, actual=actual.tolist(), reference=reference.tolist(), independent_reference_errors=error, transformation_controls=controls, direct_parent_errors=parent_error, passed=passed))
    return dict(program="SPD Decap PI Evaluator", version="0.23.1", status="ACCEPT_TRIANGLE_STATIC_POTENTIAL_ONLY" if all(c["passed"] for c in cases) else "STOP_TRIANGLE_STATIC_POTENTIAL_GATE", source=SOURCE, tolerance=TOL, cases=cases, elapsed_s=perf_counter()-started, scope="Planar scalar/linear inner 1/R integrals only; no double surface integral, L matrix, finite thickness, board solve, or PowerSI accuracy claim.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.self_check:
        if args.output is None:
            parser.error("--output required")
        args.output.mkdir(exist_ok=False)
        source_bytes = Path(__file__).read_bytes()
        (args.output / "driver-at-run.py").write_bytes(source_bytes)
    result = run_probe()
    if args.self_check:
        assert result["status"] == "ACCEPT_TRIANGLE_STATIC_POTENTIAL_ONLY", result
    else:
        result["script_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        (args.output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False))
