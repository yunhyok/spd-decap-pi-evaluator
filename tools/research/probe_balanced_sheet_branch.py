"""One synthetic paired-port composition check; no product or source-board adapter."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from spd_decap_pi._core.solver.global_mna import (  # noqa: E402
    DifferentialPort, SeriesBranchBlock, compile_global_mna,
)
from spd_decap_pi._core.solver.surface_patch_plane import (  # noqa: E402
    SurfacePatchArtwork, SurfacePatchDielectric, SurfacePatchFinitePort,
    SurfacePatchMesh, compile_surface_patch_plane,
)


def probe() -> dict:
    start = perf_counter()
    frequency = 2e6
    nodes = ("P0", "G0", "P1", "G1")
    strip = box(0, 0, 3000, 1000)
    mesh = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"), cell_um=1000,
        artwork=(SurfacePatchArtwork("TOP", "P", strip), SurfacePatchArtwork("BOT", "G", strip)),
    )
    sheet = compile_surface_patch_plane(
        mesh, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4, 0.01),),
        synthetic_material_defaults=True,
    )
    footprints = tuple(
        SurfacePatchFinitePort(name, layer, net, box(x, 0, x + 1000, 1000), "synthetic paired launch")
        for name, layer, net, x in (("P0", "TOP", "P", 0), ("G0", "BOT", "G", 0),
                                     ("P1", "TOP", "P", 2000), ("G1", "BOT", "G", 2000))
    )
    condensed = sheet.condense_finite_ports(frequency, footprints)
    # Paired currents i_terminal=B*i_loop, measured voltages v_loop=B.T*v_terminal.
    # ponytail: this fixed B covers two co-located pairs only; offset launches need a physical return model.
    incidence = np.array(((1, 0), (-1, 0), (0, 1), (0, -1)), dtype=float)
    terminal_y = condensed.terminal_admittance_s
    loop_y = incidence.T @ terminal_y @ incidence / 4
    loop_z = np.linalg.solve(loop_y, np.eye(2))
    reconstruction = float(np.linalg.norm(terminal_y - incidence @ loop_y @ incidence.T) / np.linalg.norm(terminal_y))
    assert reconstruction < 1e-10
    assert np.linalg.norm(condensed.terminal_constraint_matrix.T @ incidence) < 1e-10
    assert abs(loop_z[0, 1]) > 1e-6, "must retain coupling between the two launches"

    omega = 2 * np.pi * frequency
    load_z = 0.08 + 1j * omega * 10e-9 + 1 / (1j * omega * 1e-6)
    blocks = (
        SeriesBranchBlock(("sheet0", "sheet1"), ("P0", "P1"), ("G0", "G1"), loop_z,
                          "paired-sheet", owner_ids=("synthetic:whole-sheet-GC-and-RL",)),
        SeriesBranchBlock(("load",), ("P1",), ("G1",), np.array(((load_z,),)),
                          "remote-rlc", owner_ids=("synthetic:remote-load",)),
    )
    ports = (DifferentialPort("drive", "P0", "G0"), DifferentialPort("remote", "P1", "G1"))
    result = compile_global_mna(nodes, branch_blocks=blocks, ports=ports).solve(frequency)
    reordered = compile_global_mna(tuple(reversed(nodes)), branch_blocks=blocks, ports=ports).solve(frequency)

    # Independent composition reference: load the mesh before any terminal condensation.
    projection = sheet._projection_metadata.projection
    full_y = sheet.assemble_differential_admittance(frequency).nodal_admittance_s
    weights = sheet.finite_port_projection(footprints).node_weights
    rhs = np.asarray(projection.T @ weights @ incidence)
    mesh_k = np.asarray((projection.T @ full_y @ projection).toarray())
    loaded_mesh_k = mesh_k + np.outer(rhs[:, 1], rhs[:, 1]) / load_z
    reference = rhs.T @ np.linalg.solve(loaded_mesh_k, rhs)
    error = float(np.linalg.norm(result.impedance_ohm - reference) / np.linalg.norm(reference))
    gauge_error = float(np.linalg.norm(result.impedance_ohm - reordered.impedance_ohm) / np.linalg.norm(reference))
    assert result.diagnostics.component_count == reordered.diagnostics.component_count == 2
    assert result.diagnostics.gauge_node_ids != reordered.diagnostics.gauge_node_ids
    assert error < 1e-8 and gauge_error < 1e-10
    elapsed = perf_counter() - start
    assert elapsed < 55
    return {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_PAIRED_CANONICAL_ONLY", "product_ready": False,
        "frequency_hz": frequency, "elapsed_s": elapsed,
        "geometry": "continuous 3x1 mm TOP P / BOT G, 100 um dielectric, three cells",
        "material": "synthetic material defaults; epsilon_r=4, tan_delta=0.01",
        "ports": "P0/G0 co-located in left cell, P1/G1 co-located in right cell",
        "remote_load": {"r_ohm": 0.08, "l_h": 10e-9, "c_f": 1e-6},
        "terminal_y_reconstruction_relative": reconstruction,
        "terminal_y_nullity": 4 - int(np.linalg.matrix_rank(terminal_y)),
        "loop_mutual_impedance_abs_ohm": float(abs(loop_z[0, 1])),
        "mesh_loaded_reference_relative_error": error,
        "reordered_gauge_relative_error": gauge_error,
        "impedance_ohm": {"real": result.impedance_ohm.real.tolist(), "imag": result.impedance_ohm.imag.tolist()},
        "reference_impedance_ohm": {"real": reference.real.tolist(), "imag": reference.imag.tolist()},
        "diagnostics": asdict(result.diagnostics),
        "reordered_gauge_node_ids": reordered.diagnostics.gauge_node_ids,
        "scope": "Existing SeriesBranchBlock preserves paired loop coupling and two gauges. No ideal G0/G1 short, extra base G/C, fitted R/L, source board, or live Layerwise forwarding.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error(f"refusing to overwrite existing evidence: {args.output}")
    report = probe()
    encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
