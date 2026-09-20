"""Independent saved-only review of the canonical Device pad/first-via fixture."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-device-pad-first-via-geometry-review-01"
PINS = {
    "tools/research/prepare_astra_device_pad_first_via_geometry.py": "5e8d0870aede3bfde2cc6e7f447f5e311e97da5901ef290915c253f18a105ceb",
    "outputs/research/astra-device-pad-first-via-geometry-05/result.json": "f6655f84a74f88e289ef6744c1988f148387d1eb31a04fea69fc3efbe6eaba5d",
    "outputs/research/astra-device-pad-first-via-geometry-05/geometry.npz": "5976729e9e6dc21266980cdb7c74cd5f33d076eead80828c5299b07e1307954d",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    "outputs/research/astra-device-group-port-01/group-port.npz": "bb5ac1fa7f8eec314bc26fa8c993755b2336dd38627b021fdef5cae2214d2a20",
    "outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json": "35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303",
    "outputs/research/astra-device-post-tetra-template-01/mesh.npz": "ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02",
}


def digest(path: Path) -> str: return sha256(path.read_bytes()).hexdigest()
def need(ok: bool, why: str) -> None:
    if not ok: raise AssertionError(why)


def main() -> None:
    actual = {path: digest(ROOT / path) for path in PINS}; need(actual == PINS, "pinned input mismatch")
    result = json.loads((ROOT / "outputs/research/astra-device-pad-first-via-geometry-05/result.json").read_text())
    pads = json.loads((ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_text())["pads"]
    first = json.loads((ROOT / "outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json").read_text())
    by_pin = {x["anchor"]["pin_id"]: x for x in first["records"]}; by_via = {x["via_id"]: x for x in first["source_vias"]}
    with np.load(ROOT / "outputs/research/astra-device-pad-first-via-geometry-05/geometry.npz", allow_pickle=False) as g, np.load(ROOT / "outputs/research/astra-device-group-port-01/group-port.npz", allow_pickle=False) as group, np.load(ROOT / "outputs/research/astra-device-post-tetra-template-01/mesh.npz", allow_pickle=False) as template:
        a = {key: g[key] for key in g.files}; voltage, differential, common, witness = group["terminal_voltage_map"], group["differential_voltage_map"], group["common_voltage_map"], group["global_only_kcl_witness"]
        tv, tc, tf, to, fa = template["vertices_local_m"], template["cells"], template["face_vertices"], template["first_owner_cell"], template["face_area_vector_m2"]
    n = 1956; need(result["status"] == "QUALIFIED_DECLARED_IDEAL_FIXTURE_GEOMETRY", "producer status")
    for key in ("pad_ids", "source_node_ids", "via_ids", "roles", "nets", "centers_xy_m", "first_via_top_endpoint_node", "first_via_l02_endpoint_node"):
        need(len(a[key]) == n, f"geometry {key} count")
    need(Counter(a["roles"]) == {"power": 978, "ground": 978}, "role groups")
    need(len(set(a["pad_ids"])) == len(set(a["source_node_ids"])) == len(set(a["via_ids"])) == n, "one-to-one IDs")
    need(np.array_equal(a["terminal_voltage_map"], voltage) and np.array_equal(a["differential_voltage_map"], differential) and np.array_equal(a["common_voltage_map"], common), "group maps")
    need(np.array_equal(voltage.sum(axis=0), [978, 978]) and np.array_equal(differential, [.5, -.5]) and np.array_equal(common, [1., 1.]), "group values")
    need(abs(np.vdot(voltage @ differential, witness) - np.vdot(differential, voltage.T @ witness)) < 1e-12, "global KCL virtual work")
    pad_by_id = {x["pin_id"]: x for x in pads}
    for i, pin in enumerate(a["pad_ids"]):
        pad, record, via = pad_by_id[str(pin)], by_pin[str(pin)], by_via[str(a["via_ids"][i])]
        need(str(a["source_node_ids"][i]) == pad["source_node_id"] and str(a["roles"][i]) == pad["role"] and str(a["nets"][i]) == pad["net"], "pad row identity")
        need(pad["layer"] == "Signal$TOP" and pad["contact_path_kind"] == "direct_via_landing", "pad source layer/path")
        need(str(a["source_node_record_sha256"][i]) == pad["source_node_record_sha256"] and str(a["pad_shape_record_sha256"][i]) == pad["source_pad_shape_record_sha256"] and str(a["via_record_sha256"][i]) == pad["via_record_sha256"] == via["source_record_sha256"], "source-record hashes")
        need(np.array_equal(a["centers_xy_m"][i], np.array([pad["x_pm"], pad["y_pm"]]) * 1e-12), "pad coordinate")
        ends = {via["start_layer_id"]: (via["start_node_id"], via["start_x_pm"], via["start_y_pm"]), via["end_layer_id"]: (via["end_node_id"], via["end_x_pm"], via["end_y_pm"])}
        need(set(ends) == {"Signal$TOP", "Signal$L02(DGND)"} and all((x, y) == (pad["x_pm"], pad["y_pm"]) for _node, x, y in ends.values()), "vertical endpoint")
        need(pad["source_node_id"].casefold() in {x[0].casefold() for x in ends.values()} and record["contact"]["incident_via_id"] == via["via_id"] and via["net_name"] == pad["net"], "first-via linkage")
        need(str(a["first_via_top_endpoint_node"][i]) == ends["Signal$TOP"][0] and str(a["first_via_l02_endpoint_node"][i]) == ends["Signal$L02(DGND)"][0], "endpoint arrays")
    need(np.array_equal(a["top_pad_z_interval_m"], [0., 25e-6]) and np.array_equal(a["first_via_z_interval_m"], [25e-6, 55e-6]) and np.array_equal(a["l02_pad_z_interval_m"], [55e-6, 75e-6]), "z intervals")
    need(np.all(a["top_electrode_outward_normal"] == [0., 0., -1.]) and set(a["b_boundary_sign"]) == {"B_EQUALS_NEGATIVE_OUTWARD_FLUX"}, "outward/B convention")
    top_ids = a["template_local_top_electrode_face_ids"]; owners, local_faces = a["template_local_top_electrode_owner_cell"], a["template_local_top_electrode_owner_local_face"]
    need(np.array_equal(a["template_local_top_electrode_b_area_vector_m2"], -a["template_local_face_area_vector_m2"][top_ids]), "B face map")
    for face, owner, local in zip(top_ids, owners, local_faces, strict=True):
        owner_vertices, face_vertices = tc[owner], tf[face]
        need(set(face_vertices) == set(np.delete(owner_vertices, local)), "owner/local face map")
    tet = tv[tc]; volume = np.linalg.det(tet[:, 1:] - tet[:, :1]) / 6.0
    tags = a["template_local_boundary_tag"]; need(tv.shape == (1156, 3) and len(tc) == 2592 and np.all(volume > 0) and Counter(tags) == {0: 480, 1: 288, 2: 1152}, "template topology")
    polygon_area = np.linalg.norm(fa[top_ids], axis=1).sum(); disc_area = np.pi * (50e-6)**2
    need(abs(a["ideal_pad_disc_area_m2"][0] - disc_area) < 1e-22 and abs(a["template_top_electrode_face_area_m2"][0] - polygon_area) < 1e-22 and polygon_area < disc_area, "disc/polygon areas")
    centers = a["centers_xy_m"]; minimum = min(float(np.linalg.norm(centers[i+1:] - centers[i], axis=1).min()) for i in range(n-1))
    need(minimum > 100e-6, "r50 template overlap")
    for key in ("bottom_connection_status", "lateral_connection_status", "lower_connection_status", "adjacent_trace_status"):
        need(set(a[key]) == {"RETAINED_EXTERNAL"}, f"{key} retained")
    receipt = {"program": "review_astra_device_pad_first_via_geometry", "version": 1, "status": "ACCEPT_WITH_SCOPE", "reviewer_sha256": digest(Path(__file__)), "pins": actual, "recomputed": {"pad_count": n, "roles": {"power": 978, "ground": 978}, "vertical_top_to_l02_vias": n, "template_cells": 2592, "template_positive_cells": int((volume > 0).sum()), "boundary_tags": {str(k): int(v) for k, v in Counter(tags).items()}, "ideal_disc_area_m2": disc_area, "polygon_area_m2": polygon_area, "polygon_relative_deficit": 1 - polygon_area / disc_area, "minimum_center_separation_um": minimum * 1e6, "minimum_template_clearance_um": (minimum - 100e-6) * 1e6}, "scope": "The whole 100 um TOP face is a declared ideal fixture electrode, not PowerSI contact-area equivalence. The 20 um radius cylinder is a declared source-model convention; no manufactured bore is inferred. Lower/lateral/adjacent interfaces remain RETAINED_EXTERNAL and no solve or field accuracy follows."}
    OUT.mkdir(parents=True, exist_ok=True); target = OUT / "independent-review.json"
    if target.exists(): raise FileExistsError(target)
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "receipt_sha256": digest(target), "minimum_center_separation_um": minimum * 1e6}))


if __name__ == "__main__": main()
