"""Independently review the saved source-trace/return-neighborhood artifact."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import struct


ROOT = Path(__file__).resolve().parents[2]
PRODUCER = "tools/research/inspect_astra_source_trace_return_neighborhood.py"
RESULT = "outputs/research/astra-source-trace-return-neighborhood-01/result.json"
WKB = {
    "artwork_flat_trace": "outputs/research/astra-source-trace-return-neighborhood-01/artwork_flat_trace-viewport.wkb",
    "pad_augmented_no_drill_subtraction": "outputs/research/astra-source-trace-return-neighborhood-01/pad_augmented_no_drill_subtraction-viewport.wkb",
}
PINS = {
    PRODUCER: "2091c49493ec8db2be3717d1498bab0601c5a8d9259e1967871b4ef9cf785c44",
    RESULT: "d59b1c5d98853bdcd260bc4f5c008cc4416367b680b3a91f16a1eb4b7ac1e2f2",
    WKB["artwork_flat_trace"]: "c753667ca1a4c63c2cbf1183f4e88717c9c2cf56f8d13d0864305e3c1ba04efd",
    WKB["pad_augmented_no_drill_subtraction"]: "c753667ca1a4c63c2cbf1183f4e88717c9c2cf56f8d13d0864305e3c1ba04efd",
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def close(actual, expected):
    assert abs(actual - expected) < 1e-8, (actual, expected)


def signed_area(points):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])) / 2


def clip(points, inside, crossing):
    result = []
    for left, right in zip(points, points[1:] + points[:1]):
        left_inside, right_inside = inside(left), inside(right)
        if left_inside and right_inside:
            result.append(right)
        elif left_inside:
            result.append(crossing(left, right))
        elif right_inside:
            result.extend((crossing(left, right), right))
    return result


def rectangle_intersection(points, xmin, ymin, xmax, ymax):
    for inside, crossing in (
        (lambda point: point[0] >= xmin, lambda left, right: (xmin, left[1] + (right[1] - left[1]) * (xmin - left[0]) / (right[0] - left[0]))),
        (lambda point: point[0] <= xmax, lambda left, right: (xmax, left[1] + (right[1] - left[1]) * (xmax - left[0]) / (right[0] - left[0]))),
        (lambda point: point[1] >= ymin, lambda left, right: (left[0] + (right[0] - left[0]) * (ymin - left[1]) / (right[1] - left[1]), ymin)),
        (lambda point: point[1] <= ymax, lambda left, right: (left[0] + (right[0] - left[0]) * (ymax - left[1]) / (right[1] - left[1]), ymax)),
    ):
        points = clip(points, inside, crossing) if points else []
    return points


def polygon_wkb(path):
    data = path.read_bytes()
    assert data[0] == 1 and struct.unpack_from("<I", data, 1)[0] == 3
    offset = 9
    rings = []
    for _ in range(struct.unpack_from("<I", data, 5)[0]):
        count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        ring = [struct.unpack_from("<dd", data, offset + 16 * index) for index in range(count)]
        offset += 16 * count
        assert len(ring) >= 4 and ring[0] == ring[-1]
        rings.append(ring[:-1])
    assert offset == len(data)
    return rings


def review(output):
    assert not output.exists()
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative
    saved = json.loads((ROOT / RESULT).read_bytes())
    assert saved["status"] == "VERIFIED_CACHED_NEIGHBORHOOD__RECTANGULAR_RETURN_NOT_SOURCE_GEOMETRY"
    trace = saved["source_trace"]
    assert {key: trace[key] for key in ("trace_id", "start_node_id", "end_node_id", "width_pm", "net_name")} == {
        "trace_id": "Trace463597", "start_node_id": "Node29655", "end_node_id": "Node29656",
        "width_pm": 45_000_000, "net_name": "ADC_VDD_075_VTRIP_SRAM/0"}
    nodes = {node["node_id"]: node for node in saved["source_nodes"]}
    endpoints = [nodes[trace[key]] for key in ("start_node_id", "end_node_id")]
    points = [(node["x_pm"] / 1e6, node["y_pm"] / 1e6) for node in endpoints]
    assert points[0][1] == points[1][1]
    xmin, xmax = sorted(point[0] for point in points)
    half_width = trace["width_pm"] / 2e6
    ymin, ymax = points[0][1] - half_width, points[0][1] + half_width
    footprint_area = (xmax - xmin) * (ymax - ymin)
    close(footprint_area, 5850.)
    assert len(saved["adjacent_source_records"]["traces"]) == 3
    vias = saved["adjacent_source_records"]["vias"]
    assert len(vias) == 2 and all(via["net_fold"] == trace["net_fold"] for via in vias)
    assert all("signal$l02(dgnd)" in (via["start_layer_id_fold"], via["end_layer_id_fold"]) for via in vias)
    pads = [pad for pad in saved["pad_shapes"] if pad["padstack_id_fold"] == "dut" and pad["layer_id_fold"] == "signal$top"]
    assert len(pads) == 1 and pads[0]["shape_kind"] == "CIRCLE" and pads[0]["width_pm"] == pads[0]["height_pm"] == 100_000_000
    bounds = saved["viewport_bounds_um"]
    close(bounds[2] - bounds[0], 600.); close(bounds[3] - bounds[1], 400.)
    variants = {variant["variant"]: variant for variant in saved["ground_variants"]}
    wkb_checks = {}
    for name, relative in WKB.items():
        rings = polygon_wkb(ROOT / relative)
        viewport_area = abs(sum(signed_area(ring) for ring in rings))
        overlap_area = abs(sum(signed_area(rectangle_intersection(ring, xmin, ymin, xmax, ymax)) for ring in rings))
        close(viewport_area, variants[name]["viewport_area_um2"])
        close(overlap_area, 1125.)
        close(overlap_area / footprint_area, 5. / 26.)
        assert overlap_area < footprint_area
        wkb_checks[name] = dict(viewport_area_um2=float(viewport_area), overlap_um2=float(overlap_area),
            coverage_fraction=float(overlap_area / footprint_area), wkb_sha256=digest(ROOT / relative))
    output.mkdir(parents=True)
    receipt = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="ACCEPT_WITH_SCOPE", pins=PINS,
        reviewer_sha256=digest(Path(__file__)), source_trace=dict(trace_id=trace["trace_id"], net_name=trace["net_name"],
            endpoint_node_ids=[trace["start_node_id"], trace["end_node_id"]], length_um=130., width_um=45., projection_um2=float(footprint_area)),
        neighborhood=dict(additional_incident_traces=2, same_power_vias_to_l02=2, dut_pad_diameter_um=100.,
            viewport_size_um=[600., 400.], wkb_checks=wkb_checks),
        scope="Saved cached artifact only. Signal$L02(DGND) is a layer label, not electrical DGND ownership; the two vias retain the source power net. The viewport is display/extraction-only. Flat trace-end convention, pad tessellation, and no-drill-subtraction treatment remain conditional; no port, return path, field, board, or PowerSI claim is reviewed.")
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
    print(json.dumps(dict(status=receipt["status"], receipt_sha256=digest(output / "independent-review.json"))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    review(parser.parse_args().output.resolve())
