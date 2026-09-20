"""Review frozen L21 DC meshes without another mesh/solver execution."""

from hashlib import sha256
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "docs/evaluation-research"
INPUTS = {
    "uniform": ("astra_l21_four_terminal_dc_2026-09-07.json", "76bbb152f89b0e8086128f34a489f06c77ba10b2dfae0b952fc245723881ce3b"),
    "triangle_original": ("astra_l21_triangle_mesh_2026-09-07.json", "dc9b2816fb8dd109db55e4080bfe049a73a2ac65c2fd1af3c7c2ed6800bc1589"),
    "boundary20": ("astra_l21_triangle_boundary20_2026-09-07.json", "16629500c2c725a4faf5fdfb8e18f4a2dd14aa82b6f0da183e55e2dbd3c11bdb"),
    "boundary10": ("astra_l21_triangle_boundary10_2026-09-07.json", "547f06c5c41d4f4a1d8ee19c70ba449d931546a5b3e6891efc4c74ed0c4c9b3d"),
}


def relative(a, b):
    """Maximum pair change relative to the later/finer estimate b."""
    return float(np.max(np.abs(np.array(b) - a) / np.array(b)))


def diagnostics(row, basis):
    y, r = np.array(row["y_s"]), np.array(row["balanced_z_ohm"])
    off_diagonal = y[~np.eye(4, dtype=bool)]
    norm = np.linalg.norm(y)
    d = {
        "row_sum_relative": float(np.linalg.norm(y @ np.ones(4)) / norm),
        "reciprocity_relative": float(np.linalg.norm(y - y.T) / norm),
        "minimum_y_eigenvalue_s": float(np.linalg.eigvalsh(y).min()),
        "maximum_off_diagonal_y_s": float(off_diagonal.max()),
        "minimum_balanced_z_entry_ohm": float(r.min()),
        "minimum_balanced_z_eigenvalue_ohm": float(np.linalg.eigvalsh(r).min()),
        "balanced_reconstruction_relative": float(np.linalg.norm(basis @ np.linalg.solve(r, basis.T) - y) / norm),
    }
    assert d["row_sum_relative"] < 1e-10 and d["reciprocity_relative"] < 1e-10
    assert d["minimum_y_eigenvalue_s"] >= -norm * 1e-10
    assert d["minimum_balanced_z_eigenvalue_ohm"] > 0
    assert d["balanced_reconstruction_relative"] < 1e-10
    return d


def main():
    output = FOLDER / "astra_l21_dc_acceptance_2026-09-07.json"
    if output.exists():
        raise FileExistsError(output)
    data = {}
    for name, (file, digest) in INPUTS.items():
        raw = (FOLDER / file).read_bytes()
        assert sha256(raw).hexdigest() == digest, file
        data[name] = json.loads(raw)
    a, b, old = data["boundary20"], data["boundary10"], data["triangle_original"]
    for name in ("source_bridge_sha256", "source_asset_sha256", "coordinate_origin_um",
                 "dyadic_quantum_um", "electrode_radii_um", "balanced_basis"):
        assert a[name] == b[name] == old[name] == data["uniform"][name], name
    for case, edges in ((a, 200), (b, 400)):
        evidence = case["backend_evidence"]
        assert evidence["identical_domain"] and evidence["outer_original_edges"] == 8
        assert evidence["outer_subdivided_edges"] == edges
        assert case["failure"] is None
        assert all(c["missing_boundary_edges"] == 0 for c in case["triangulator_calls"])
    assert a["backend_evidence"]["original_artwork_wkb_sha256"] == b["backend_evidence"]["original_artwork_wkb_sha256"]
    finest_a, finest_b = a["sheet_rows"][-1], b["sheet_rows"][-1]
    assert finest_a["circle_sides"] == finest_b["circle_sides"] == 128
    assert finest_a["area_target_spacing_um"] == finest_b["area_target_spacing_um"] == 5
    basis = np.array(a["balanced_basis"])
    signs = {name: diagnostics(data[name]["sheet_rows"][-1], basis)
             for name in ("triangle_original", "boundary20", "boundary10")}
    assert signs["triangle_original"]["maximum_off_diagonal_y_s"] > 0
    for name in ("boundary20", "boundary10"):
        assert signs[name]["maximum_off_diagonal_y_s"] < 0
        assert signs[name]["minimum_balanced_z_entry_ohm"] > 0
    changes = dict(a["relative_changes"])
    changes["outer_boundary_20_to_10_max_pair_relative"] = relative(finest_a["pair_r_ohm"], finest_b["pair_r_ohm"])
    assert len(changes) == 3 and all(0 <= v < .02 for v in changes.values())
    r_a, r_b = np.array(finest_a["balanced_z_ohm"]), np.array(finest_b["balanced_z_ohm"])
    result = {
        "program": a["program"], "version": a["version"],
        "status": "ACCEPT_CONDITIONAL_LOCAL_DC_WITH_SAMPLED_REFINEMENT_ONLY",
        "inputs": {k: {"file": f, "sha256": h, "frozen_status": data[k]["status"]} for k, (f, h) in INPUTS.items()},
        "governing_original_triangle_status": "STOP_INDEPENDENT_MESH_DISAGREEMENT_AND_DC_SIGN",
        "diagnostics": signs, "relative_changes": changes, "sampled_gate": .02,
        "boundary_balanced_matrix_frobenius_relative": float(np.linalg.norm(r_b - r_a) / np.linalg.norm(r_b)),
        "selected_input": INPUTS["boundary10"][0], "selected_pair_r_ohm": finest_b["pair_r_ohm"],
        "scope": a["scope"],
        "limitations": [
            "Area meshes are nonnested. Small sampled changes are not a rigorous error bound or a general mesher certification.",
            "Uniform L3 still failed its own refinement gate; it supplies independent discrepancy evidence, not an independently converged truth.",
            "The old positive off-diagonal contact Y violates the scalar DC maximum-principle sign expectation. A negative transfer entry alone is not nonpassivity; all three matrices retain positive balanced-R eigenvalues.",
            "Only the declared four-electrode local DC approximation is accepted. No native fifth artwork/G-C attachment, board current, AC sheet impedance or PowerSI agreement is established.",
        ],
        "execution": "Frozen receipts only; no mesh, native solve or historical execution rerun.",
    }
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "relative_changes": changes}))


if __name__ == "__main__":
    main()
