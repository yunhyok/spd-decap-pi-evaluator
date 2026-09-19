"""Four-triangle static partial-L check with exact rectangular-sheet controls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from numpy.polynomial.legendre import leggauss

from probe_astra_rt0_p0_bar import _bar
from probe_astra_triangle_static_potential import duffy_reference, triangle_potential

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "bar": (Path(__file__).with_name("probe_astra_rt0_p0_bar.py"), "7ce14c6113e3c5023fdbe4ebd3f98aecf04c3b6fe98aa3007964ac998a2fbb0a"),
    "potential": (Path(__file__).with_name("probe_astra_triangle_static_potential.py"), "75f55868574d16236b5e6dc14370c3fefffda2d3c171707f330eda1af8561b1e"),
}
MU_OVER_4PI = 1e-7
LENGTH_M, WIDTH_M, HEIGHT_M = 4e-3, 1e-3, 2e-4
TOL = 5e-5
SOURCE_PINS = {
    "mesh": (ROOT/"outputs/research/astra-l25-sheet-mesh-preflight-01/mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
    "topology": (ROOT/"outputs/research/astra-l25-dual-cell-topology-01/dual-cell-topology.npz", "eb33117f14a3e5725e9efeafd2a0a083961ad83abaf022b7fe78b94bc8b5265f"),
}


def rectangle_self_integral(length, width):
    """Exact integral 4∫(L-x)(W-y)/sqrt(x²+y²)dxdy, units m³."""
    radius = np.hypot(length, width)
    i00 = length*np.arcsinh(width/length)+width*np.arcsinh(length/width)
    i10 = 0.5*(width*radius+length**2*np.arcsinh(width/length)-width**2)
    i01 = 0.5*(length*radius+width**2*np.arcsinh(length/width)-length**2)
    i11 = (radius**3-length**3-width**3)/3
    return 4*(length*width*i00-width*i10-length*i01+i11)


def rectangle_mutual_integral(length, width, height, order):
    """Independent tensor quadrature of the convolution of two aligned rectangles."""
    nodes, weights = leggauss(order)
    nodes, weights = (nodes+1)/2, weights/2
    x, y = np.meshgrid(length*nodes, width*nodes, indexing="ij")
    return float(4*length*width*np.sum(np.outer(weights, weights)*(length-x)*(width-y)/np.sqrt(x*x+y*y+height*height)))


def triangle_pair(observer, source, height, order):
    """Nine RT0 Galerkin 1/R reactions; analytic inner, Gaussian outer integral."""
    nodes, weights = leggauss(order)
    nodes, weights = (nodes+1)/2, weights/2
    oa, ob = observer[1]-observer[0], observer[2]-observer[0]
    sa, sb = source[1]-source[0], source[2]-source[0]
    two_observer_area = abs(oa[0]*ob[1]-oa[1]*ob[0])
    two_source_area = abs(sa[0]*sb[1]-sa[1]*sb[0])
    matrix = np.zeros((3, 3))
    # ponytail: scalar O(order²) oracle for four triangles; vectorize only for a validated larger pilot.
    for u, wu in zip(nodes, weights, strict=True):
        for v, wv in zip(nodes, weights, strict=True):
            point = observer[0]+u*oa+(1-u)*v*ob
            potential = triangle_potential(source, point, height)
            inner = ((point-source)*potential[0]+potential[1:])/two_source_area
            outer = (point-observer)/two_observer_area
            matrix += two_observer_area*(1-u)*wu*wv*(outer@inner.T)
    return MU_OVER_4PI*matrix


def assemble(order, flip=False):
    case = _bar(1, flip_winding=flip)
    points = case["points"]*1e-3
    triangles = [points[np.asarray(tri)] for tri in case["triangles"]]
    cells = triangles+triangles
    heights = (0., 0., HEIGHT_M, HEIGHT_M)
    branches = len(case["active"])
    matrix = np.zeros((2*branches, 2*branches))
    for first in range(4):
        for second in range(4):
            local = triangle_pair(cells[first], cells[second], heights[first]-heights[second], order)
            for row, row_local, row_sign in case["cell_active"][first % 2]:
                for col, col_local, col_sign in case["cell_active"][second % 2]:
                    matrix[row+(first//2)*branches, col+(second//2)*branches] += row_sign*col_sign*local[row_local, col_local]
    current = np.zeros(branches)
    for branch, edge, incidence in case["active"]:
        cell, opposite = incidence[0]
        vertices = triangles[cell]
        local_edge = ((1, 2), (2, 0), (0, 1))[opposite]
        tangent = vertices[local_edge[1]]-vertices[local_edge[0]]
        det = np.linalg.det(np.stack((vertices[1]-vertices[0], vertices[2]-vertices[0])))
        integrated_outward_normal = np.sign(det)*np.array((tangent[1], -tangent[0]))
        current[branch] = integrated_outward_normal[0]/WIDTH_M
    return matrix, current


def run_probe():
    started = perf_counter()
    for path, sha in PINS.values():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError(f"helper hash mismatch: {path}")
    coarse, current = assemble(16)
    fine, current_fine = assemble(32)
    flipped, current_flipped = assemble(32, flip=True)
    if not np.array_equal(current, current_fine) or not np.array_equal(current, current_flipped):
        raise ValueError("facet-current orientation mismatch")
    self_ref = MU_OVER_4PI*rectangle_self_integral(LENGTH_M, WIDTH_M)/WIDTH_M**2
    mutual_ref = MU_OVER_4PI*rectangle_mutual_integral(LENGTH_M, WIDTH_M, HEIGHT_M, 96)/WIDTH_M**2
    mutual_low = MU_OVER_4PI*rectangle_mutual_integral(LENGTH_M, WIDTH_M, HEIGHT_M, 48)/WIDTH_M**2
    branches = len(current)
    self_value = float(current@fine[:branches, :branches]@current)
    mutual_value = float(current@fine[:branches, branches:]@current)
    opposite = np.r_[current, -current]
    differential = float(opposite@fine@opposite)
    scale = np.linalg.norm(fine)
    metrics = {
        "rectangle_self_relative_error": abs(self_value-self_ref)/self_ref,
        "rectangle_mutual_relative_error": abs(mutual_value-mutual_ref)/mutual_ref,
        "mutual_reference_refinement_relative_error": abs(mutual_low-mutual_ref)/mutual_ref,
        "outer_quadrature_refinement_relative_error": np.linalg.norm(fine-coarse)/scale,
        "raw_reciprocity_relative_error": np.linalg.norm(fine-fine.T)/scale,
        "winding_relative_error": np.linalg.norm(fine-flipped)/scale,
        "differential_pattern_relative_error": abs(differential-2*(self_ref-mutual_ref))/(2*(self_ref-mutual_ref)),
        "symmetric_part_min_eigenvalue_h": float(np.linalg.eigvalsh(0.5*(fine+fine.T))[0]),
    }
    gates = {key: bool(np.isfinite(value) and (value > 0 if key.endswith("eigenvalue_h") else value < (1e-9 if key.startswith("mutual_reference") else TOL))) for key, value in metrics.items()}
    return dict(program="SPD Decap PI Evaluator", version="0.23.1", status="ACCEPT_SYNTHETIC_RT0_PARTIAL_L_ONLY" if all(gates.values()) else "STOP_RT0_PARTIAL_L_GATE", inputs={key: {"path": str(path), "sha256": sha} for key, (path, sha) in PINS.items()}, dimensions_m={"length": LENGTH_M, "width": WIDTH_M, "separation": HEIGHT_M}, quadrature_orders=[16, 32], tolerance=TOL, raw_partial_L_h=fine.tolist(), one_sheet_current_a=current.tolist(), rectangle_self_h=self_value, rectangle_self_reference_h=self_ref, rectangle_mutual_h=mutual_value, rectangle_mutual_reference_h=mutual_ref, opposite_sheet_pattern_h=differential, metrics={key: float(value) for key, value in metrics.items()}, gates=gates, elapsed_s=perf_counter()-started, scope="Four zero-thickness synthetic triangles, static partial magnetic kernel only. Opposite longitudinal sheet currents omit connecting legs; this is not a complete closed-loop inductance, finite-thickness/internal-impedance composition, source-board solve, or PowerSI accuracy claim.")


def run_source_stress():
    """Three source-shape checks only; no assembly of board magnetic interactions."""
    started = perf_counter()
    pins = PINS | SOURCE_PINS
    for path, sha in pins.values():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError(f"helper/source hash mismatch: {path}")
    with np.load(SOURCE_PINS["mesh"][0], allow_pickle=False) as mesh, np.load(SOURCE_PINS["topology"][0], allow_pickle=False) as topology:
        points = mesh["node_xy_um"]*1e-6
        triangles = mesh["triangles"]
        free = topology["free_triangle_indices"]
        vertices = points[triangles[free]]
        a, b = vertices[:, 1]-vertices[:, 0], vertices[:, 2]-vertices[:, 0]
        areas = abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0])/2
        selected = [("first_free", int(free[0])), ("known_sliver", 457250), ("largest_area", int(free[np.argmax(areas)]))]
        if len(set(index for _, index in selected)) != 3 or 457250 not in free:
            raise ValueError("source stress selection changed")
        del vertices, a, b, areas
    cases = []
    for label, index in selected:
        vertices = points[triangles[index]]
        local = vertices-vertices[0]
        lengths = np.linalg.norm(local[[1, 2, 0]]-local, axis=1)
        determinant = np.linalg.det(np.stack((local[1], local[2])))
        area = abs(determinant)/2
        centroid = np.mean(vertices, axis=0)
        inner = triangle_potential(vertices, centroid, 0.)
        reference = duffy_reference(vertices, centroid, 0.)
        inner_scalar_error = abs(inner[0]-reference[0])/abs(reference[0])
        inner_moment_error = np.linalg.norm(inner[1:]-reference[1:])/(max(lengths)*abs(reference[0]))
        matrices = [triangle_pair(vertices, vertices, 0., order) for order in (16, 32, 64)]
        fine = matrices[-1]
        translated = triangle_pair(local, local, 0., 64)
        permutation = [0, 2, 1]
        flipped = triangle_pair(vertices[permutation], vertices[permutation], 0., 64)
        flux = np.array([np.sign(determinant)*np.array(((local[j]-local[i])[1], -(local[j]-local[i])[0])) for i, j in ((1, 2), (2, 0), (0, 1))])
        energies = [np.diag(flux.T@matrix@flux) for matrix in matrices]
        matrix_scale = np.linalg.norm(fine)
        metrics = dict(
            inner_scalar_relative_error=float(inner_scalar_error),
            inner_moment_scaled_relative_error=float(inner_moment_error),
            refinement_16_to_32=float(np.linalg.norm(matrices[1]-matrices[0])/matrix_scale),
            refinement_32_to_64=float(np.linalg.norm(fine-matrices[1])/matrix_scale),
            uniform_x_y_energy_refinement=float(np.max(abs(energies[2]-energies[1])/np.maximum(abs(energies[2]), np.finfo(float).tiny))),
            raw_reciprocity=float(np.linalg.norm(fine-fine.T)/matrix_scale),
            translation=float(np.linalg.norm(translated-fine)/matrix_scale),
            winding=float(np.linalg.norm(flipped-fine[np.ix_(permutation, permutation)])/matrix_scale),
            min_symmetric_eigenvalue_h=float(np.linalg.eigvalsh(0.5*(fine+fine.T))[0]),
        )
        gates = {key: bool(np.isfinite(value) and (value > 0 if key.endswith("eigenvalue_h") else value < TOL)) for key, value in metrics.items() if key != "refinement_16_to_32"}
        gates["positive_uniform_current_energy"] = bool(np.all(np.isfinite(energies[2])) and np.all(energies[2] > 0))
        cases.append(dict(label=label, triangle_index=index, mesh_node_indices=triangles[index].tolist(), vertices_m=vertices.tolist(), area_m2=area, longest_edge_over_altitude=float(max(lengths)**2/(2*area)), self_partial_L_h=fine.tolist(), orthogonal_sheet_current_density_a_per_m=1.0, twice_magnetic_energy_x_y_j=[energy.tolist() for energy in energies], metrics=metrics, gates=gates, passed=all(gates.values())))
    return dict(program="SPD Decap PI Evaluator", version="0.23.1", status="ACCEPT_THREE_L25_SHAPE_KERNELS_ONLY" if all(case["passed"] for case in cases) else "STOP_L25_SHAPE_KERNEL_GATE", inputs={key: {"path": str(path), "sha256": sha} for key, (path, sha) in pins.items()}, tolerance=TOL, quadrature_orders=[16, 32, 64], cases=cases, elapsed_s=perf_counter()-started, scope="Three actual L25 triangle shapes evaluated with a zero-thickness static kernel only; no inter-triangle source coupling, finite-thickness/internal composition, board solve, accelerator, or PowerSI claim.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-stress", action="store_true")
    args = parser.parse_args()
    if not args.self_check:
        if args.output is None:
            parser.error("--output required")
        args.output.mkdir(exist_ok=False)
        source_bytes = Path(__file__).read_bytes()
        (args.output/"driver-at-run.py").write_bytes(source_bytes)
    report = run_source_stress() if args.source_stress else run_probe()
    if args.self_check:
        assert report["status"].startswith("ACCEPT_"), report
    else:
        report["script_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        (args.output/"result.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False))
