"""SPD Decap PI Evaluator v0.23.1: exact lower r20 contact-face census."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-boundary-conforming-power-joint-01/result.json":
        "322b6ded469ae8b4510689706d8297cf89061f07f36cafa7b82a4240542a7bf9",
    "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz":
        "5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913",
    "outputs/research/astra-device-l02-via-contacts-03/result.json":
        "24b32ddef4854bf80d63de9583ae5afd97ed5664e3e0d256a4ae9c5a658cd43a",
    "outputs/research/astra-device-l02-via-contacts-03/lower-via-contact-ledger.json":
        "7d2477d09ab5e50cbea2397eeb19385389c7722fdd05d682987c08736a02affa",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def run(output: Path) -> None:
    start = monotonic()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative, expected in PINS.items():
            assert digest(ROOT / relative) == expected, relative

        source_ledger = json.loads(
            (ROOT / "outputs/research/astra-device-l02-via-contacts-03/lower-via-contact-ledger.json")
            .read_text(encoding="utf-8")
        )
        assert len(source_ledger["pads"]) == 1956
        assert not source_ledger["unsupported_candidates"]
        assert not source_ledger["foreign_net_contacts"]
        source_contact_areas = []
        for pad in source_ledger["pads"]:
            assert len(pad["other_via_contacts"]) == 1
            contact = pad["other_via_contacts"][0]
            assert contact["same_net"] and contact["shares_source_endpoint"]
            source_contact_areas.append(contact["lower_solid_barrel_contact_um2"])

        with np.load(
            ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz",
            allow_pickle=False,
        ) as mesh:
            vertices = mesh["vertices_local_um"]
            face_vertices = mesh["face_vertices"]
            boundary_face_ids = mesh["boundary_face_ids"]
            first_owner = mesh["first_owner_cell"]
            area_vectors = mesh["face_area_vector_um2"]

        boundary_triangles = vertices[face_vertices[boundary_face_ids]]
        is_lower = np.all(
            np.abs(boundary_triangles[:, :, 2] - 75.0) < 1.0e-12, axis=1
        )
        lower_face_ids = boundary_face_ids[is_lower]
        lower_triangles = boundary_triangles[is_lower]
        assert len(lower_face_ids) == 288

        angle = np.arange(96) * (2.0 * np.pi / 96.0)
        source_xy = np.column_stack((20.0 * np.cos(angle), 20.0 * np.sin(angle)))
        source_contact = Polygon(source_xy)
        polygons = [Polygon(triangle[:, :2]) for triangle in lower_triangles]
        triangle_areas = np.asarray([polygon.area for polygon in polygons])
        intersection_areas = np.asarray(
            [polygon.intersection(source_contact).area for polygon in polygons]
        )
        fractions = intersection_areas / triangle_areas
        fraction_tolerance = 1.0e-12
        inside_local = np.flatnonzero(fractions >= 1.0 - fraction_tolerance)
        outside_local = np.flatnonzero(fractions <= fraction_tolerance)
        partial_local = np.flatnonzero(
            (fractions > fraction_tolerance) & (fractions < 1.0 - fraction_tolerance)
        )
        assert len(inside_local) == 96
        assert len(outside_local) == 192
        assert len(partial_local) == 0

        contact_union = unary_union([polygons[index] for index in inside_local])
        complement_union = unary_union([polygons[index] for index in outside_local])
        lower_union = unary_union(polygons)
        outer_xy = np.column_stack((30.0 * np.cos(angle), 30.0 * np.sin(angle)))
        source_lower_pad = Polygon(outer_xy)
        contact_xor = float(contact_union.symmetric_difference(source_contact).area)
        contact_hausdorff = float(
            contact_union.boundary.hausdorff_distance(source_contact.boundary)
        )
        lower_xor = float(lower_union.symmetric_difference(source_lower_pad).area)
        complement_xor = float(
            complement_union.symmetric_difference(
                source_lower_pad.difference(source_contact)
            ).area
        )

        r20_vertices = vertices[
            np.isclose(vertices[:, 2], 75.0, atol=1.0e-12)
            & np.isclose(
                np.linalg.norm(vertices[:, :2], axis=1), 20.0, atol=1.0e-10
            )
        ][:, :2]
        assert len(r20_vertices) == 96
        coordinate_error = max(
            min(float(np.linalg.norm(point - candidate)) for candidate in r20_vertices)
            for point in source_xy
        )

        contact_face_ids = lower_face_ids[inside_local]
        complement_face_ids = lower_face_ids[outside_local]
        contact_area_vectors = area_vectors[contact_face_ids]
        assert np.all(contact_area_vectors[:, 2] > 0.0)
        assert np.max(np.abs(contact_area_vectors[:, :2])) < 1.0e-12
        source_area = float(source_contact.area)
        vector_area_error = float(
            np.linalg.norm(
                contact_area_vectors.sum(axis=0) - [0.0, 0.0, source_area]
            )
            / source_area
        )
        source_area_readback = np.asarray(source_contact_areas)
        source_ledger_area_relative = float(
            np.max(np.abs(source_area_readback - source_area)) / source_area
        )
        max_zero_dust = float(np.max(fractions[outside_local]))
        max_one_dust = float(np.max(np.abs(1.0 - fractions[inside_local])))

        checks = {
            "lower_boundary_faces": len(lower_face_ids),
            "contact_faces": len(contact_face_ids),
            "complement_faces": len(complement_face_ids),
            "genuine_partial_faces": len(partial_local),
            "fraction_tolerance": fraction_tolerance,
            "maximum_outside_fraction_dust": max_zero_dust,
            "maximum_inside_one_minus_fraction_dust": max_one_dust,
            "contact_union_xor_area_um2": contact_xor,
            "contact_boundary_hausdorff_um": contact_hausdorff,
            "lower_pad_union_xor_area_um2": lower_xor,
            "complement_union_xor_area_um2": complement_xor,
            "r20_source_to_mesh_vertex_max_distance_um": coordinate_error,
            "contact_area_um2": source_area,
            "contact_area_vector_relative_error": vector_area_error,
            "source_ledger_contact_area_relative_error": source_ledger_area_relative,
        }
        assert max_zero_dust < fraction_tolerance
        assert max_one_dust < fraction_tolerance
        assert contact_xor < 1.0e-12
        assert contact_hausdorff < 1.0e-12
        assert lower_xor < 1.0e-12
        assert complement_xor < 1.0e-12
        assert coordinate_error < 1.0e-12
        assert vector_area_error < 1.0e-13
        assert source_ledger_area_relative < 1.0e-13

        artifact = output / "lower-contact-faces.npz"
        np.savez_compressed(
            artifact,
            source_contact_polygon_xy_um=source_xy,
            lower_boundary_face_ids=lower_face_ids,
            contact_face_ids=contact_face_ids,
            complement_face_ids=complement_face_ids,
            contact_owner_cells=first_owner[contact_face_ids],
            complement_owner_cells=first_owner[complement_face_ids],
            contact_face_vertices=face_vertices[contact_face_ids],
            complement_face_vertices=face_vertices[complement_face_ids],
            contact_area_vector_um2=contact_area_vectors,
            raw_intersection_area_fraction=fractions,
        )
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "PASS_EXISTING_POST_HAS_CONFORMING_R20_LOWER_CONTACT",
            "elapsed_s": monotonic() - start,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifact_sha256": digest(artifact),
            "checks": checks,
            "supersedes": (
                "astra-boundary-post-lower-contact-01 used exact floating equality "
                "for polygon areas, turning <=6.46e-16 fractional roundoff dust into "
                "157 false partial faces. Its remesh conclusion is rejected."
            ),
            "scope": (
                "The existing boundary-only post mesh already has 96 exact lower faces "
                "whose union is the accepted centered r20 source-model 96-gon contact. "
                "The other 192 lower faces are retained as an independent complement. "
                "This identifies reusable contact geometry only; it imposes no current, "
                "zero-flux, equipotential, charge, via-continuation, Green, field, port, "
                "board or PowerSI condition."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "checks": checks}))
    except Exception:
        (output / "failure.json").write_text(
            json.dumps(
                {
                    "status": "STOP_BOUNDARY_POST_LOWER_CONTACT_CENSUS",
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.output.resolve())
