"""SPD Decap PI Evaluator v0.23.1: independent saved P-chain bridge geometry review."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
import shapely
from shapely.affinity import translate
from shapely.geometry import Polygon, box


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-device-power-top-bridges-02/driver-at-run.py":
        "bbef5ca185703088e0fa51a4a0c2f4fc2c505e7512a468b4cdd5bf2b91a8e220",
    "outputs/research/astra-device-power-top-bridges-02/result.json":
        "150cef6607c156e1be6d4c1b97a63d48cdabadb43ed3d7ae25cae430615ffb0c",
    "outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json":
        "583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790",
    "outputs/research/astra-device-power-top-bridges-02/selected-power-top-union.wkb":
        "65a9b8ea8424f9b44c308dd504baec813a37fb71ba1b4c8867a7b777c12b295f",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json":
        "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    "outputs/research/astra-device-top-cut-faces-01/cut-faces.npz":
        "18cfdd8dd1a1bb6b9e1a6f2ae5afdefff139950b5024746fe1cfdc965da8c0d4",
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def polygon_area_vector(vertices: np.ndarray) -> np.ndarray:
    origin = vertices[0]
    return sum((np.cross(vertices[index] - origin, vertices[index + 1] - origin)
                for index in range(1, len(vertices) - 1)), start=np.zeros(3)) / 2


def run(output: Path) -> int:
    started = monotonic()
    if output.exists():
        raise FileExistsError(output)
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative

    result = json.loads((ROOT / "outputs/research/astra-device-power-top-bridges-02/result.json").read_bytes())
    ledger = json.loads((ROOT / "outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json").read_bytes())
    pad_source = json.loads((ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_bytes())
    assert result["program"] == PROGRAM and result["version"] == VERSION
    assert result["status"] == "ASSEMBLED_SELECTED_POWER_TOP_CHAIN_OWNERSHIP_AND_OPPOSING_INTERFACE_GEOMETRY"
    pads = [row for row in pad_source["pads"] if row["role"] == "power"]
    assert len(pads) == 978 and all(row["diameter_pm"] == 100_000_000 for row in pads)
    by_pin = {row["pin_id"]: row for row in pads}
    assert len(by_pin) == 978
    with np.load(ROOT / "outputs/research/astra-device-top-cut-faces-01/cut-faces.npz", allow_pickle=False) as cuts:
        edges = cuts["template_edge_xy_um"]
        face_ids = cuts["template_side_face_ids"]
        face_b = cuts["template_side_b_area_vector_m2"]
        saved_fraction = cuts["contact_area_fraction"]
        all_pad_ids = cuts["pad_ids"].tolist()
    global_pad_index = {pin: index for index, pin in enumerate(all_pad_ids)}

    disc = Polygon(edges[:, 0])
    assert disc.is_valid and not disc.interiors and len(edges) == 96
    rectangle = box(0.0, -22.5, 130.0, 22.5)
    bridge = rectangle.difference(shapely.union_all([disc, translate(disc, xoff=130.0)]))
    saved_bridge = shapely.from_wkb(bytes.fromhex(ledger["canonical_bridge_wkb_hex"]))
    bridge_delta = float(bridge.symmetric_difference(saved_bridge).area)
    assert bridge_delta < 1e-12
    assert bridge.intersection(disc).area < 1e-12
    assert bridge.intersection(translate(disc, xoff=130.0)).area < 1e-12

    instances = ledger["instances"]
    assert len(instances) == 933
    graph = {pin: set() for pin in by_pin}
    translated_bridges = []
    source_rectangles = []
    assembled_fraction = np.zeros_like(saved_fraction)
    mates = ledger["canonical_interface_mates"]
    assert len(mates) == 64
    maximum_b_error = 0.0
    maximum_piece_area_error = 0.0
    for mate in mates:
        side_index = int(mate["side_face_index"])
        assert int(face_ids[side_index]) == int(mate["template_face_id"])
        expected_pad_b = float(mate["area_fraction"]) * face_b[side_index]
        saved_pad_b = np.asarray(mate["pad_b_area_vector_m2"])
        saved_bridge_b = np.asarray(mate["bridge_b_area_vector_m2"])
        maximum_b_error = max(maximum_b_error, float(np.max(np.abs(saved_pad_b - expected_pad_b))),
                              float(np.max(np.abs(saved_pad_b + saved_bridge_b))))
        xyz = np.asarray(mate["shared_polygon_xyz_um"])
        assert xyz.shape[1] == 3 and len(xyz) >= 3
        area_from_vertices = np.linalg.norm(polygon_area_vector(xyz)) * 1e-12
        maximum_piece_area_error = max(maximum_piece_area_error,
                                       abs(area_from_vertices - np.linalg.norm(saved_pad_b)))
    assert maximum_b_error < 1e-24 and maximum_piece_area_error < 1e-24

    for item in instances:
        left = by_pin[item["left_pin"]]
        right = by_pin[item["right_pin"]]
        left_pm = np.array([left["x_pm"], left["y_pm"]], dtype=np.int64)
        right_pm = np.array([right["x_pm"], right["y_pm"]], dtype=np.int64)
        assert np.array_equal(right_pm - left_pm, [130_000_000, 0])
        left_xy = left_pm.astype(float) / 1e6
        right_xy = right_pm.astype(float) / 1e6
        assert np.allclose(np.asarray(item["translation_xy_um"]), left_xy, rtol=0, atol=1e-12)
        graph[item["left_pin"]].add(item["right_pin"])
        graph[item["right_pin"]].add(item["left_pin"])
        expected_indices = [global_pad_index[item["left_pin"]], global_pad_index[item["right_pin"]]]
        assert item["pad_indices"] == expected_indices
        for mate in mates:
            assembled_fraction[expected_indices[int(mate["endpoint"])], int(mate["side_face_index"])] += float(mate["area_fraction"])
        translated_bridges.append(translate(bridge, xoff=left_xy[0], yoff=left_xy[1]))
        source_rectangles.append(translate(rectangle, xoff=left_xy[0], yoff=left_xy[1]))

    seen: set[str] = set()
    component_sizes = []
    component_spans = []
    for seed in graph:
        if seed in seen:
            continue
        stack = [seed]
        seen.add(seed)
        members = []
        while stack:
            node = stack.pop()
            members.append(node)
            for neighbor in graph[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        xy = np.array([[by_pin[pin]["x_pm"], by_pin[pin]["y_pm"]] for pin in members], dtype=float) / 1e6
        assert np.ptp(xy[:, 1]) == 0
        component_sizes.append(len(members))
        component_spans.append(float(np.ptp(xy[:, 0])))
    histogram = Counter(component_sizes)
    assert histogram == Counter({4: 6, 8: 21, 12: 12, 107: 6})
    assert len(component_sizes) == 45 and sum(component_sizes) == 978
    assert sum(len(neighbors) for neighbors in graph.values()) // 2 == 933
    assert max(component_spans) == 13_780.0

    power_indices = np.array([global_pad_index[row["pin_id"]] for row in pads])
    fraction_error = float(np.max(np.abs(assembled_fraction[power_indices] - saved_fraction[power_indices])))
    assert fraction_error < 1e-9
    pad_shapes = [translate(disc, xoff=row["x_pm"] / 1e6, yoff=row["y_pm"] / 1e6) for row in pads]
    pad_union = shapely.union_all(pad_shapes)
    bridge_union = shapely.union_all(translated_bridges)
    owned = shapely.union_all([pad_union, bridge_union])
    original = shapely.union_all([*pad_shapes, *source_rectangles])
    saved_union = shapely.from_wkb((ROOT / "outputs/research/astra-device-power-top-bridges-02/selected-power-top-union.wkb").read_bytes())
    saved_union_delta = float(saved_union.symmetric_difference(original).area)
    pad_bridge_overlap = float(pad_union.intersection(bridge_union).area)
    owned_delta = float(owned.symmetric_difference(original).area)
    assert saved_union_delta < 1e-12
    assert pad_bridge_overlap < 1e-12
    assert owned_delta < 1e-5
    assert len(shapely.get_parts(original)) == 45

    checks = {
        "bridge_template_symmetric_difference_um2": bridge_delta,
        "bridge_template_area_um2": float(bridge.area),
        "bridge_template_volume_um3": float(bridge.area * 25.0),
        "instances": len(instances),
        "components": len(component_sizes),
        "component_size_histogram": {str(key): int(value) for key, value in sorted(histogram.items())},
        "maximum_component_span_um": max(component_spans),
        "maximum_saved_contact_fraction_difference": fraction_error,
        "maximum_mate_b_error_m2": maximum_b_error,
        "maximum_mate_polygon_area_error_m2": maximum_piece_area_error,
        "pad_bridge_overlap_area_um2": pad_bridge_overlap,
        "canonical_piece_vs_original_union_symmetric_difference_um2": owned_delta,
        "saved_vs_reconstructed_original_union_symmetric_difference_um2": saved_union_delta,
        "authoritative_union_components": len(shapely.get_parts(original)),
    }
    assert abs(checks["bridge_template_volume_um3"] - result["canonical_bridge_volume_um3"]) < 1e-9
    assert abs(owned_delta - result["owned_source_union_symmetric_difference_area_um2"]) < 1e-12

    receipt = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "checks": checks,
        "decision": (
            "The saved WKB was independently deserialized and reconstructed from the pinned 96-gon pads, "
            "933 translated 130x45 um rectangles and one disjoint canonical bridge. The original overlapping "
            "45-component union remains authoritative; the measured canonical decomposition delta is numerical "
            "and was not repaired with a snap or buffer. All 64 saved mate polygons carry opposing B vectors."
        ),
        "scope": (
            "This is the selected 978-pad/933-trace Device P-chain subdomain, not the full TOP rail. Six "
            "107-pad chains span 13.78 mm, so global coupling remains material. Current-space conformity, "
            "trace-volume tetrahedra, Green assembly, ground/lower boundaries and board accuracy remain open."
        ),
        "elapsed_s": monotonic() - started,
    }
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": receipt["status"], "checks": checks}))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
