"""SPD Decap PI Evaluator v0.23.1: independent lower-contact topology review."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-boundary-post-lower-contact-review-01"
PINS = {
    "outputs/research/astra-boundary-post-lower-contact-02/driver-at-run.py":
        "9486f82cd0bf4fd0ea86d96df87001c5a1564886d98e352f6bd72cf2f332c8ef",
    "outputs/research/astra-boundary-post-lower-contact-02/result.json":
        "622f738819b93b186453d491b078fe351cd152903f2336d6e4f5e5de6b41ee3d",
    "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz":
        "3f8b47b136bc32c8dcba503911de42495853c8b4c24f2962daee957b3d643975",
    "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz":
        "5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913",
    "outputs/research/astra-device-l02-via-contacts-03/lower-via-contact-ledger.json":
        "7d2477d09ab5e50cbea2397eeb19385389c7722fdd05d682987c08736a02affa",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def edge_counts(faces: np.ndarray) -> Counter[tuple[int, int]]:
    result: Counter[tuple[int, int]] = Counter()
    for face in faces:
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            result[tuple(sorted((int(a), int(b))))] += 1
    return result


start = monotonic()
if OUTPUT.exists():
    raise FileExistsError(OUTPUT)
OUTPUT.mkdir(parents=True)
(OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
for relative, expected in PINS.items():
    assert digest(ROOT / relative) == expected, relative

with np.load(
    ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz",
    allow_pickle=False,
) as mesh:
    vertices = mesh["vertices_local_um"]
    face_vertices = mesh["face_vertices"]
    boundary_face_ids = mesh["boundary_face_ids"]
    area_vectors = mesh["face_area_vector_um2"]
with np.load(
    ROOT / "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz",
    allow_pickle=False,
) as saved:
    contact_ids = saved["contact_face_ids"]
    complement_ids = saved["complement_face_ids"]
    source_xy = saved["source_contact_polygon_xy_um"]

lower = boundary_face_ids[
    np.all(
        np.abs(vertices[face_vertices[boundary_face_ids], 2] - 75.0) < 1.0e-12,
        axis=1,
    )
]
radius = np.linalg.norm(vertices[:, :2], axis=1)
direct_contact = lower[
    np.all(radius[face_vertices[lower]] <= 20.0 + 1.0e-10, axis=1)
]
direct_complement = np.setdiff1d(lower, direct_contact)
assert np.array_equal(np.sort(contact_ids), np.sort(direct_contact))
assert np.array_equal(np.sort(complement_ids), np.sort(direct_complement))

contact_faces = face_vertices[contact_ids]
contact_radii = np.sort(radius[contact_faces], axis=1)
assert np.max(np.abs(contact_radii[:, 0])) < 1.0e-12
assert np.max(np.abs(contact_radii[:, 1:] - 20.0)) < 1.0e-12
contact_edges = edge_counts(contact_faces)
contact_boundary_edges = [edge for edge, count in contact_edges.items() if count == 1]
contact_interior_edges = [edge for edge, count in contact_edges.items() if count == 2]
assert len(contact_boundary_edges) == len(contact_interior_edges) == 96
assert all(
    np.max(np.abs(radius[np.asarray(edge)] - 20.0)) < 1.0e-12
    for edge in contact_boundary_edges
)

complement_faces = face_vertices[complement_ids]
complement_edges = edge_counts(complement_faces)
complement_boundary_edges = [edge for edge, count in complement_edges.items() if count == 1]
inner_edges = [
    edge
    for edge in complement_boundary_edges
    if np.max(np.abs(radius[np.asarray(edge)] - 20.0)) < 1.0e-12
]
outer_edges = [
    edge
    for edge in complement_boundary_edges
    if np.max(np.abs(radius[np.asarray(edge)] - 30.0)) < 1.0e-12
]
assert len(inner_edges) == len(outer_edges) == 96

triangles = vertices[contact_faces]
cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
areas = np.linalg.norm(cross, axis=1) / 2.0
expected_area = 96.0 * 20.0**2 * np.sin(2.0 * np.pi / 96.0) / 2.0
area_relative = float(abs(areas.sum() - expected_area) / expected_area)
area_vector_relative = float(
    np.linalg.norm(area_vectors[contact_ids].sum(axis=0) - [0.0, 0.0, expected_area])
    / expected_area
)

r20_vertices = vertices[np.isclose(radius, 20.0, atol=1.0e-12), :2]
assert len(r20_vertices) == 96 * 4
# The template repeats each planar ring on four z planes; select the lower ring.
r20_lower = vertices[
    np.isclose(radius, 20.0, atol=1.0e-12)
    & np.isclose(vertices[:, 2], 75.0, atol=1.0e-12),
    :2,
]
source_coordinate_error = max(
    min(float(np.linalg.norm(point - candidate)) for candidate in r20_lower)
    for point in source_xy
)

ledger = json.loads(
    (ROOT / "outputs/research/astra-device-l02-via-contacts-03/lower-via-contact-ledger.json")
    .read_text(encoding="utf-8")
)
ledger_areas = []
for pad in ledger["pads"]:
    assert len(pad["other_via_contacts"]) == 1
    contact = pad["other_via_contacts"][0]
    assert contact["same_net"] and contact["shares_source_endpoint"]
    ledger_areas.append(contact["lower_solid_barrel_contact_um2"])
ledger_area_relative = float(
    np.max(np.abs(np.asarray(ledger_areas) - expected_area)) / expected_area
)

metrics = {
    "lower_faces": len(lower),
    "contact_faces": len(contact_ids),
    "complement_faces": len(complement_ids),
    "contact_boundary_edges": len(contact_boundary_edges),
    "contact_radial_spokes": len(contact_interior_edges),
    "complement_inner_edges": len(inner_edges),
    "complement_outer_edges": len(outer_edges),
    "cross_product_area_relative": area_relative,
    "saved_area_vector_relative": area_vector_relative,
    "source_polygon_vertex_relative_um": source_coordinate_error,
    "ledger_contact_area_relative": ledger_area_relative,
}
assert area_relative < 1.0e-13
assert area_vector_relative < 1.0e-13
assert source_coordinate_error < 1.0e-12
assert ledger_area_relative < 1.0e-13

result = {
    "program": "SPD Decap PI Evaluator",
    "version": "0.23.1",
    "status": "ACCEPT_EXISTING_CONFORMING_R20_CONTACT_WITH_SCOPE",
    "elapsed_s": monotonic() - start,
    "reviewer_sha256": digest(Path(__file__)),
    "pins": PINS,
    "metrics": metrics,
    "scope": (
        "Independent coordinate/topology review without polygon intersection. The 96 "
        "center-to-r20 triangles form the accepted lower source-model contact and the "
        "192 annular triangles remain its complement. No boundary condition, via-current "
        "continuity, Green action, field, port, board or PowerSI result is approved."
    ),
}
(OUTPUT / "independent-review.json").write_text(
    json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
)
print(json.dumps({"status": result["status"], "metrics": metrics}))
