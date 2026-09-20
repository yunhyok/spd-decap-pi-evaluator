"""Independent saved-field review of the first refined L25 RT0/P1 pair."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from scipy import sparse


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PAIR_ROOT = R / "astra-l25-rt0-p1-refined-pair-01"
PROGRAM = "SPD Decap PI Evaluator v0.23.1"
PINS = {
    "result": (PAIR_ROOT / "result.json", "3d744e4e125b805aeac197b4580fb029c7aa18993778919046b5fd6153c44047"),
    "driver": (PAIR_ROOT / "driver-at-run.py", "604d11bb57861e71c6cd24f93a63843d4ee2a4599e61dc19826d82c4f0003075"),
    "prelu": (PAIR_ROOT / "prelu-controls.json", "9d5e572f90201368941ad44ef3396cf10e1a1177e57a72807e145d8671e0f29f"),
    "mesh": (PAIR_ROOT / "refined-mesh.npz", "3ae0cf0dbd5644fa0477ab46cf2a6fd6a8ad9f52f6fcaafdf18948050d9f8ee0"),
    "topology": (PAIR_ROOT / "refined-topology.npz", "eeb362dd1e8377245684112ae1536cc2aa258ec2d2411de17effe7e2e90ffe8f"),
    "drive": (PAIR_ROOT / "refined-sheet-drive.npz", "acc4670cc8b90fbd208b1a4ae98cdbb826bfc19ae7a36bae72c84eb392b337b3"),
    "resistance": (PAIR_ROOT / "refined-rt0-resistance.npz", "2a2042b906951ddfe883614c5f3af5c59781c29f679d89966f5ca1ccdb457c60"),
    "rt0_field": (PAIR_ROOT / "rt0-field.npz", "f787a3234a1e66f0f5eda82f99e88b466f2f3e9578f48ac130bb0f7b76c6d903"),
    "p1_field": (PAIR_ROOT / "p1-field.npz", "2cb370f6429ef70727d4b0bf1cd063069d068d930e5a5666a0b3551a91d48b2a"),
    "base_result": (R / "astra-l25-rt0-p1-pair-01" / "result.json", "2111aeac692b1f8b7a991331264dc4c35fb434c5e846ccf7031f9228c5da4777"),
    "gc_result": (R / "astra-l25-refined-gc-areas-01" / "result.json", "98787c9bd4aa9799ac8041b779a0600870f8f976b3fcaf77fa5682f52ba589e4"),
    "gc_npz": (R / "astra-l25-refined-gc-areas-01" / "owner-cell-area.npz", "b42677ea3574c41f74733bd732d476797fab516672bdbd116c472e2a9023c07b"),
    "gc_review": (R / "astra-l25-refined-gc-areas-01" / "independent-review.json", "cb6d90aee5cf3ec1340e962ca87944f37b7c4de35b0e6d7d61532b1468bb2344"),
    "rc_review": (R / "astra-rt0-p0-rc-bar-01" / "independent-review.json", "36853ca7e5e0d509152b27427c5ab394ef87fbadf32f2d70bee8bbebb4f6a81e"),
}


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pin(name: str) -> Path:
    path, expected = PINS[name]
    if not path.is_file() or sha(path) != expected:
        raise ValueError(f"pinned input mismatch: {name}")
    return path


def load_json(name: str) -> dict:
    value = json.loads(pin(name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object JSON: {name}")
    return value


def load_npz(name: str) -> dict[str, np.ndarray]:
    with np.load(pin(name), allow_pickle=False) as archive:
        value = {key: np.asarray(archive[key]) for key in archive.files}
    if any(array.dtype.kind == "O" for array in value.values()):
        raise ValueError(f"object array in {name}")
    return value


def csc(value: dict, prefix: str) -> sparse.csc_matrix:
    return sparse.csc_matrix(
        (value[f"{prefix}_data"], value[f"{prefix}_indices"], value[f"{prefix}_indptr"]),
        shape=tuple(value[f"{prefix}_shape"]),
    )


def relative(actual: float, expected: float) -> float:
    return abs(actual - expected) / max(abs(expected), np.finfo(float).tiny)


def main() -> int:
    result, prelu, base = load_json("result"), load_json("prelu"), load_json("base_result")
    gc_result, gc_review, rc_review = load_json("gc_result"), load_json("gc_review"), load_json("rc_review")
    for name in ("driver", "gc_npz"):
        pin(name)
    if result.get("status") != "COMPLETED_CONDITIONAL_L25_ADAPTIVE_RT0_P1_PAIR":
        raise ValueError("refined pair did not complete")
    if prelu.get("status") != "PRE_LU_CONTROLS_PASS":
        raise ValueError("pre-LU controls did not pass")
    if gc_review.get("status") != "ACCEPT_INDEPENDENT_REFINED_GC_AREA_REVIEW" or rc_review.get("status") != "ACCEPT_INDEPENDENT_SYNTHETIC_RT0_P0_RC_REVIEW":
        raise ValueError("supporting independent review is not accepted")

    mesh, topology, drive, resistance = (
        load_npz("mesh"), load_npz("topology"), load_npz("drive"), load_npz("resistance")
    )
    rt0_field, p1_field, gc_areas = load_npz("rt0_field"), load_npz("p1_field"), load_npz("gc_npz")
    free_count = len(topology["free_triangle_indices"])
    branch_count = len(topology["branch_first_node"])
    if (len(mesh["node_xy_um"]), len(mesh["triangles"]), free_count, branch_count, int(drive["conductance_shape"][0])) != (535281, 542355, 539905, 545083, 532656):
        raise ValueError("refined dimensions differ")
    if int(topology["connected_component_count"][0]) != 1 or not np.all(topology["contact_branch_degree"] == 16):
        raise ValueError("refined topology connectivity/rim degree differs")

    r = csc(resistance, "r")
    q = np.asarray(rt0_field["branch_current_a"], dtype=np.float64)
    vr = np.asarray(rt0_field["potential_v"], dtype=np.float64)
    jr = np.asarray(rt0_field["source_j_a"], dtype=np.float64)
    first = np.asarray(topology["branch_first_node"], dtype=np.int64)
    second = np.asarray(topology["branch_second_node"], dtype=np.int64)
    constitutive = np.asarray(r @ q) - (vr[first] - vr[second])
    flux = np.bincount(first, weights=q, minlength=vr.size) - np.bincount(second, weights=q, minlength=vr.size)
    rt0_kcl = flux - jr
    rt0_energy = float(q @ (r @ q))
    rt0_pair = float(vr[free_count] - vr[free_count + 1])

    g = csc(drive, "conductance")
    vp = np.asarray(p1_field["contracted_voltage_v"], dtype=np.float64)
    jp = np.asarray(p1_field["source_j_a"], dtype=np.float64)
    p1_flux = np.asarray(g @ vp)
    p1_kcl = p1_flux - jp
    p1_energy = float(vp @ p1_flux)
    p1_pair = float(vp[0] - vp[1])

    stored_gap = float(result["gap"]["gap_ohm"])
    energy_gap = rt0_energy - p1_energy
    old_gap = float(base["gap"]["gap_ohm"])
    contraction = (old_gap - stored_gap) / old_gap
    gap_over_p1 = stored_gap / p1_pair
    numeric = {
        "rt0_constitutive_max_v": float(np.max(np.abs(constitutive))),
        "rt0_kcl_max_a": float(np.max(np.abs(rt0_kcl))),
        "rt0_energy_pair_relative_error": relative(rt0_energy, rt0_pair),
        "p1_kcl_max_a": float(np.max(np.abs(p1_kcl))),
        "p1_energy_pair_relative_error": relative(p1_energy, p1_pair),
        "gap_vs_energy_difference_relative_error": relative(stored_gap, energy_gap),
        "base_gap_ohm": old_gap,
        "refined_gap_ohm": stored_gap,
        "gap_contraction_fraction": contraction,
        "refined_gap_over_p1_resistance": gap_over_p1,
        "p1_resistance_ohm": p1_pair,
        "rt0_resistance_ohm": rt0_pair,
    }
    if numeric["rt0_constitutive_max_v"] > 1e-8 or numeric["rt0_kcl_max_a"] > 1e-8 or numeric["p1_kcl_max_a"] > 1e-8:
        raise ValueError("independent physical residual gate failed")
    if max(numeric["rt0_energy_pair_relative_error"], numeric["p1_energy_pair_relative_error"], numeric["gap_vs_energy_difference_relative_error"]) > 1e-8:
        raise ValueError("independent energy identity gate failed")
    if not (p1_pair >= float(base["p1"]["pair_voltage_ohm"]) and rt0_pair <= float(base["rt0"]["pair_voltage_ohm"]) and 0.0 < stored_gap < old_gap):
        raise ValueError("nested refined bracket direction failed")

    geometry_hashes = {
        "node_xy_um": hashlib.sha256(mesh["node_xy_um"].tobytes()).hexdigest(),
        "triangles": hashlib.sha256(mesh["triangles"].tobytes()).hexdigest(),
    }
    if geometry_hashes != gc_result["geometry_array_sha256"]:
        raise ValueError("refined pair and refined GC geometry hashes differ")
    if not np.array_equal(gc_areas["parent_triangle_index"], mesh["parent_triangle_index"]):
        raise ValueError("refined pair and GC parent maps differ")
    if not np.array_equal(gc_areas["triangle_contact_index"], mesh["triangle_contact_tag"]):
        raise ValueError("refined pair and GC contact tags differ")
    for name, record in prelu["artifacts"].items():
        if sha(PAIR_ROOT / name) != record["sha256"]:
            raise ValueError(f"pre-LU artifact receipt mismatch: {name}")
    if any(not bool(result["gates"][key]) for key in (
        "rt0_physical_residual", "p1_physical_residual", "rt0_energy_equals_pair",
        "p1_energy_equals_pair", "rt0_quadrature_equals_matrix",
        "p1_gradient_equals_matrix", "cross_integral_equals_negative_p1",
        "p1_le_rt0", "gap_nonnegative", "gap_identity", "finite",
    )) or not all(bool(value) for value in result["nested_gates"].values()):
        raise ValueError("reported governing gate is not accepted")

    review = {
        "program": PROGRAM,
        "status": "ACCEPT_INDEPENDENT_L25_REFINED_RT0_P1_PAIR_REVIEW",
        "pins": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items()},
        "dimensions": {
            "node_count": len(mesh["node_xy_um"]),
            "triangle_count": len(mesh["triangles"]),
            "free_cell_count": free_count,
            "branch_count": branch_count,
            "p1_contracted_count": int(drive["conductance_shape"][0]),
        },
        "numeric_recalculation": numeric,
        "supporting_controls": {
            "refined_gc_geometry_and_parent_map_match": True,
            "refined_gc_independent_review_sha256": PINS["gc_review"][1],
            "synthetic_rc_independent_review_sha256": PINS["rc_review"][1],
            "topology_connected_components": 1,
            "all_175_rim_degrees": 16,
            "pre_lu_artifact_hashes_match": True,
        },
        "assessment": {
            "first_refinement_reduced_gap": True,
            "gap_remains_material": True,
            "bounded_relocalize_and_refine_loop_is_numerically_motivated": True,
            "convergence_or_board_accuracy_established": False,
        },
        "limitations": [
            "Saved-field and sparse-operator review only; no LU, source intersection, or native-board solve was repeated.",
            "The 34.4-percent first-step gap contraction leaves a 61.3-percent gap/P1 ratio, so this field is not yet a qualified current discretization.",
            "Any further bounded loop must relocalize the current gap, preserve fields per step, retain resource and monotonicity stops, and transfer source G/C before board reuse.",
        ],
    }
    output = PAIR_ROOT / "independent-review.json"
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(review, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": review["status"], **numeric}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
