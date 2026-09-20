"""Independent saved-artifact review for the refined L25 GC transfer and RC bar."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy import sparse


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator v0.23.1"
GC = R / "astra-l25-refined-gc-areas-01"
RC = R / "astra-rt0-p0-rc-bar-01"
PINS = {
    "gc_result": (GC / "result.json", "98787c9bd4aa9799ac8041b779a0600870f8f976b3fcaf77fa5682f52ba589e4"),
    "gc_npz": (GC / "owner-cell-area.npz", "b42677ea3574c41f74733bd732d476797fab516672bdbd116c472e2a9023c07b"),
    "gc_driver": (GC / "driver-at-run.py", "20cbc3ee387d13eea74cecc1b13fa25437a302f4734a6d8c70e4e54b0510c93f"),
    "old_areas": (R / "astra-l25-dual-cell-inputs-02" / "owner-cell-area.npz", "5c6b1f1a0d71ec7d8688e80f0aff192a6f9849b200e70e8ccf98ee0149515239"),
    "mesh": (R / "astra-l25-sheet-mesh-preflight-01" / "mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
    "selection": (R / "astra-l25-rt0-p1-gap-inputs-02" / "cell-gap-selection.npz", "2f749e9623af96816a1d56ae6fee402789e0539d1e5d77761e586150183dff6f"),
    "refiner": (ROOT / "tools" / "research" / "refine_astra_l25_pair_mesh.py", "2ceb44814ec1d9655a0a3e70d3cbd16f354c6a8b757a35868b1ef29db89f7393"),
    "rc_result": (RC / "result.json", "3a39ce2966583c444708ecd106fffa7f8708ede2bc06dc510a0f8c33f85f87ab"),
    "rc_driver": (RC / "driver-at-run.py", "cefef482b82a660d39937536b3262dc09379c6dc573ba5bfc655285870d187ea"),
}


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pin(name: str) -> Path:
    path, expected = PINS[name]
    if not path.is_file() or sha(path) != expected:
        raise ValueError(f"pinned input mismatch: {name}")
    return path


def npz(name: str) -> dict[str, np.ndarray]:
    with np.load(pin(name), allow_pickle=False) as archive:
        return {key: np.asarray(archive[key]) for key in archive.files}


def load_json(name: str) -> dict:
    value = json.loads(pin(name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {name}")
    return value


def write(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def review_gc() -> dict:
    result = load_json("gc_result")
    pin("gc_driver")
    new, old, mesh, selection = npz("gc_npz"), npz("old_areas"), npz("mesh"), npz("selection")
    if result.get("status") != "COMPLETED_SOURCE_CLIPPED_L25_REFINED_GC_AREAS":
        raise ValueError("refined GC producer did not complete")
    old_m = sparse.csr_matrix((old["data_um2"], old["indices"], old["indptr"]), shape=tuple(old["shape"]))
    new_m = sparse.csr_matrix((new["data_um2"], new["indices"], new["indptr"]), shape=tuple(new["shape"]))
    parent = np.asarray(new["parent_triangle_index"], dtype=np.int64)
    if old_m.shape != (4, 542091) or new_m.shape != (4, 542355) or parent.shape != (542355,):
        raise ValueError("refined GC sparse dimensions differ")
    vertices = np.asarray(mesh["node_xy_um"], dtype=np.float64)[np.asarray(mesh["triangles"], dtype=np.int64)]
    edge0, edge1 = vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]
    area = np.abs(edge0[:, 0] * edge1[:, 1] - edge0[:, 1] * edge1[:, 0]) / 2.0
    max_parent_relative = 0.0
    for owner in range(4):
        collapsed = np.bincount(parent, weights=new_m.getrow(owner).toarray().ravel(), minlength=old_m.shape[1])
        expected = old_m.getrow(owner).toarray().ravel()
        max_parent_relative = max(max_parent_relative, float(np.max(np.abs(collapsed - expected) / np.maximum(area, 1e-30))))
    if max_parent_relative > 2e-9:
        raise ValueError("independent parent-area collapse failed")
    if not np.array_equal(new["triangle_contact_index"], old["triangle_contact_index"][parent]):
        raise ValueError("refined contact tags do not follow parent cells")
    if not np.array_equal(new["owner_bindings_json_utf8"], old["owner_bindings_json_utf8"]):
        raise ValueError("owner fingerprint bytes changed")
    density = np.asarray(new["owner_density_f_per_um2"], dtype=np.float64)
    bindings = json.loads(new["owner_bindings_json_utf8"].tobytes().decode("utf-8"))
    capacitance = np.asarray(new_m.sum(axis=1)).ravel() * density
    expected_c = np.asarray([row["capacitance_f"] for row in bindings], dtype=np.float64)
    c_relative = float(np.max(np.abs(capacitance - expected_c) / np.abs(expected_c)))
    if c_relative > 2e-12:
        raise ValueError("independent capacitance collapse failed")

    spec = importlib.util.spec_from_file_location("astra_review_refiner", pin("refiner"))
    refiner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(refiner)
    points, triangles, expected_parent, _, coverage = refiner.refine_marked_edges(
        mesh["node_xy_um"], mesh["triangles"], selection["marked_mesh_edges_90"],
    )
    geometry_hashes = {
        "node_xy_um": hashlib.sha256(points.tobytes()).hexdigest(),
        "triangles": hashlib.sha256(triangles.tobytes()).hexdigest(),
    }
    if not np.array_equal(parent, expected_parent) or np.any(coverage != 2):
        raise ValueError("refined parent mapping differs from the pinned selection")
    if geometry_hashes != result["geometry_array_sha256"]:
        raise ValueError("refined geometry-array hash differs")
    return {
        "program": PROGRAM,
        "status": "ACCEPT_INDEPENDENT_REFINED_GC_AREA_REVIEW",
        "pins": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items() if name.startswith("gc_") or name in {"old_areas", "mesh", "selection", "refiner"}},
        "checks": {
            "owner_count": 4,
            "old_triangle_count": old_m.shape[1],
            "refined_triangle_count": new_m.shape[1],
            "max_parent_area_relative_to_triangle_area": max_parent_relative,
            "capacitance_relative_error": c_relative,
            "contact_tags_equal_parent_tags": True,
            "owner_binding_bytes_unchanged": True,
            "refined_geometry_hashes_match": True,
        },
        "limitations": ["Saved sparse-area and parent-collapse review only; source intersections were not recomputed and no G/C matrix or solve was run."],
    }


def review_rc() -> dict:
    result = load_json("rc_result")
    pin("rc_driver")
    if result.get("status") != "ACCEPT_SYNTHETIC_RT0_P0_RC_ONLY" or len(result.get("points", [])) != 12:
        raise ValueError("synthetic RC result contract differs")
    g, length, width = (float(result[key]) for key in ("sheet_conductance_s", "length_m", "width_m"))
    groups: dict[float, list[float]] = {}
    reference_error = relative_error_error = 0.0
    max_kcl = max_power = 0.0
    for row in result["points"]:
        alpha = float(row["alpha"])
        omega_c = alpha * g / length**2
        k = np.sqrt(1j * omega_c / g)
        reference = 2.0 * np.tanh(k * length / 2.0) / (g * width * k)
        stored_reference = complex(*row["reference_ohm"])
        z = complex(*row["z_ohm"])
        relative = abs(z - reference) / abs(reference)
        reference_error = max(reference_error, abs(reference - stored_reference) / abs(reference))
        relative_error_error = max(relative_error_error, abs(relative - float(row["relative_error"])))
        max_kcl = max(max_kcl, float(row["kcl_max_a"]), float(row["constitutive_max_v"]))
        max_power = max(max_power, float(row["power_identity_relative_error"]))
        groups.setdefault(alpha, []).append(relative)
    levels = [int(row["triangles"]) for row in result["points"]]
    if sorted(set(levels)) != [32, 128, 512, 2048] or any(len(values) != 4 for values in groups.values()):
        raise ValueError("synthetic RC refinement levels differ")
    decreasing = all(np.all(np.diff(values) < 0) for values in groups.values())
    finest = max(values[-1] for values in groups.values())
    winding = max(float(value) for value in result["winding_relative_errors"])
    if not (decreasing and finest < 1e-3 and reference_error < 1e-14 and relative_error_error < 1e-14 and max_kcl < 1e-9 and max_power < 1e-9 and winding < 1e-10):
        raise ValueError("independent synthetic RC arithmetic gate failed")
    return {
        "program": PROGRAM,
        "status": "ACCEPT_INDEPENDENT_SYNTHETIC_RT0_P0_RC_REVIEW",
        "pins": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items() if name.startswith("rc_")},
        "checks": {
            "point_count": 12,
            "triangle_levels": [32, 128, 512, 2048],
            "alpha_values": sorted(groups),
            "reference_formula": "2*tanh(sqrt(j*omega*c/g)*L/2)/(g*W*sqrt(j*omega*c/g))",
            "reference_recompute_relative_error_max": reference_error,
            "stored_relative_error_absolute_difference_max": relative_error_error,
            "refinement_errors_strictly_decrease": decreasing,
            "finest_relative_error_max": finest,
            "kcl_or_constitutive_max": max_kcl,
            "power_identity_relative_error_max": max_power,
            "winding_relative_error_max": winding,
        },
        "limitations": ["Synthetic uniform-strip arithmetic and sign review only; no source-board, magnetic, return-path, or PowerSI validation."],
    }


if __name__ == "__main__":
    gc_review = review_gc()
    rc_review = review_rc()
    write(GC / "independent-review.json", gc_review)
    write(RC / "independent-review.json", rc_review)
    print(json.dumps({"gc": gc_review["status"], "rc": rc_review["status"]}, sort_keys=True))
