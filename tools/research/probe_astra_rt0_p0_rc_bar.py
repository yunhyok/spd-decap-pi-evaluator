"""Bounded RT0/P0 distributed-RC strip control, with a closed 1-D reference.

J=-g grad(v), div(J)+j*omega*c*v=0 on [0,L]x[0,W]. Side current is
zero; equipotential end electrodes inject +1/-1 A; distributed C returns
to a prescribed zero-potential conductor. Thus k²=j*omega*c/g and
Zdiff=2*tanh(k*L/2)/(g*W*k). This adds no external magnetic physics.
Mixed-space background: https://docs.fenicsproject.org/dolfinx/v0.10.0.post5/python/demos/demo_mixed-poisson.html
The RC boundary solution and power identity here are derived independently.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "refiner": (ROOT / "tools/research/refine_astra_l25_pair_mesh.py", "2ceb44814ec1d9655a0a3e70d3cbd16f354c6a8b757a35868b1ef29db89f7393"),
    "resistance": (ROOT / "tools/research/assemble_astra_l25_rt0_resistance.py", "ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010"),
}
PROGRAM = "SPD Decap PI Evaluator v0.23.1"
G, LENGTH, WIDTH = 3., 4., 1.
ALPHAS = (.01, 1., 10.)  # alpha=omega*c*L²/g, independent of physical units.
FINEST_REL_TOL = 1e-3


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def helper(name):
    path, expected = PINS[name]
    if sha(path) != expected:
        raise ValueError(f"helper hash differs: {path}")
    spec = importlib.util.spec_from_file_location("astra_rc_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def operators(points, triangles, resistance):
    count = len(triangles)
    half = np.sort(np.concatenate([triangles[:, [1, 2]], triangles[:, [2, 0]], triangles[:, [0, 1]]]), axis=1)
    edges, inverse, copies = np.unique(half, axis=0, return_inverse=True, return_counts=True)
    if np.any(copies > 2):
        raise ValueError("nonmanifold synthetic mesh")
    order = np.argsort(inverse, kind="stable")
    start = np.r_[0, np.cumsum(copies[:-1])]
    cell = np.tile(np.arange(count), 3)[order]
    first, second = cell[start], cell[start + copies - 1].copy()
    left = (copies == 1) & np.all(points[edges, 0] == 0, axis=1)
    right = (copies == 1) & np.all(points[edges, 0] == LENGTH, axis=1)
    second[left], second[right] = count, count + 1
    active = (copies == 2) | left | right
    indices = np.full(len(edges), -1, dtype=int)
    indices[active] = np.arange(np.count_nonzero(active))
    first, second = first[active], second[active]
    local = indices[inverse.reshape(3, count).T]
    safe = np.maximum(local, 0)
    signs = np.where(first[safe] == np.arange(count)[:, None], 1., -1.)
    signs[local < 0] = 0
    r, _, area = resistance._batch_local_rt0(points[triangles], G)
    keep = (local[:, :, None] >= 0) & (local[:, None, :] >= 0)
    rows = np.broadcast_to(safe[:, :, None], (count, 3, 3))
    cols = np.broadcast_to(safe[:, None, :], (count, 3, 3))
    values = r * signs[:, :, None] * signs[:, None, :]
    r = sparse.coo_matrix((values[keep], (rows[keep], cols[keep])), shape=(len(first),) * 2).tocsc()
    branch = np.arange(len(first))
    b = sparse.csc_matrix((np.r_[np.ones(len(first)), -np.ones(len(first))], (np.r_[first, second], np.r_[branch, branch])), shape=(count + 2, len(first)))
    return r, b, np.r_[area, 0., 0.]


def solve(points, triangles, resistance, alpha):
    r, b, area = operators(points, triangles, resistance)
    omega_c = alpha * G / LENGTH**2
    y = sparse.diags(1j * omega_c * area, format="csc")
    matrix = sparse.bmat([[r, -b.T], [b, y]], format="csc")
    source = np.zeros(b.shape[0]); source[-2:] = [1., -1.]
    solution = spsolve(matrix, np.r_[np.zeros(r.shape[0]), source])
    q, v = solution[:r.shape[0]], solution[r.shape[0]:]
    z = v[-2] - v[-1]
    k = np.sqrt(1j * omega_c / G)
    reference = 2 * np.tanh(k * LENGTH / 2) / (G * WIDTH * k)
    kcl = float(np.max(abs(b @ q + y @ v - source)))
    constitutive = float(np.max(abs(r @ q - b.T @ v)))
    energy = np.vdot(q, r @ q) - 1j * omega_c * float(np.vdot(v, area * v).real)
    energy_error = float(abs(z - energy) / abs(z))
    finite = bool(np.all(np.isfinite(solution)))
    if not finite or kcl > 1e-9 or constitutive > 1e-9 or energy_error > 1e-9 or z.real <= 0 or z.imag >= 0:
        raise ValueError("mixed RC physical or power gate failed")
    return {"alpha": alpha, "triangles": len(triangles), "unknowns": len(solution), "z_ohm": [float(z.real), float(z.imag)], "reference_ohm": [float(reference.real), float(reference.imag)], "relative_error": float(abs(z - reference) / abs(reference)), "kcl_max_a": kcl, "constitutive_max_v": constitutive, "power_identity_relative_error": energy_error}


def run(output):
    started = time.perf_counter()
    refiner, resistance = helper("refiner"), helper("resistance")
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    points = np.array([[0., 0.], [LENGTH, 0.], [LENGTH, WIDTH], [0., WIDTH]])
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    rows = []
    # Four uniform levels refine both axes; refining x alone would not control
    # the two-dimensional RT0 current field for distributed shunt current.
    for level in range(1, 6):
        edges = np.unique(np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1), axis=0)
        points, triangles, _, _, _ = refiner.refine_marked_edges(points, triangles, edges)
        if level >= 2:
            rows.extend(solve(points, triangles, resistance, alpha) for alpha in ALPHAS)
        if time.perf_counter() - started > 60:
            raise TimeoutError("bounded RC strip control exceeded60s")
    winding = []
    for alpha in ALPHAS:
        reverse = solve(points, triangles[:, [0, 2, 1]], resistance, alpha)
        normal = [row for row in rows if row["alpha"] == alpha][-1]
        winding.append(float(abs(complex(*reverse["z_ohm"]) - complex(*normal["z_ohm"])) / abs(complex(*normal["z_ohm"]))))
    errors = [[row["relative_error"] for row in rows if row["alpha"] == alpha] for alpha in ALPHAS]
    gates = {"refinement_errors_decrease": all(np.all(np.diff(sequence) < 0) for sequence in errors), "finest_exact_reference": max(sequence[-1] for sequence in errors) < FINEST_REL_TOL, "winding": max(winding) < 1e-10}
    result = {"program": PROGRAM, "status": "ACCEPT_SYNTHETIC_RT0_P0_RC_ONLY" if all(gates.values()) else "STOP_SYNTHETIC_RC_REFINEMENT", "inputs": {name: {"path": str(path), "sha256": expected} for name, (path, expected) in PINS.items()}, "driver_sha256": sha(output / "driver-at-run.py"), "sheet_conductance_s": G, "length_m": LENGTH, "width_m": WIDTH, "finest_relative_error_gate": FINEST_REL_TOL, "points": rows, "winding_relative_errors": winding, "gates": {key: bool(value) for key, value in gates.items()}, "elapsed_s": time.perf_counter() - started, "limitations": ["Uniform synthetic strip with local DC resistance and distributed shunt capacitance only. No board topology, source-GC projection, magnetic coupling, convergence theorem or PowerSI result."]}
    with (output / "result.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "finest_errors": [sequence[-1] for sequence in errors], "gates": result["gates"]}))
    return 0 if all(gates.values()) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=PROGRAM)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/research/astra-rt0-p0-rc-bar-01")
    raise SystemExit(run(parser.parse_args().output_root.resolve()))
