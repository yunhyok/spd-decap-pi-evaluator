from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon


ROOT = Path(__file__).parents[1]
EVIDENCE = (
    Path(r"D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json"),
    Path(r"D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01\geometry_receipt.json"),
    Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\d115b_source_plane_ownership_materialization_receipt.json"),
    Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d115c-source-local-port-window-audit-02\d115c_source_local_port_window_receipt.json"),
)
SPEC = importlib.util.spec_from_file_location("d116", ROOT / "tools/research/build_source_l29_l30_fastercap_input.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _normal(a, b, c):
    u = tuple(b[i] - a[i] for i in range(3))
    v = tuple(c[i] - a[i] for i in range(3))
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def test_hole_extrusion_is_deterministic_closed_and_oriented():
    shape = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(2, 2), (8, 2), (8, 8), (2, 8)]])
    first = MODULE.extrude_faces(shape, 0, 2)
    second = MODULE.extrude_faces(shape, 0, 2)
    assert first == second
    assert first and all(len(vertices) in (3, 4) for _, vertices in first)
    for kind, vertices in first:
        if kind == "T":
            nz = _normal(*vertices)
            assert nz[2] != 0
    top = [_normal(*vertices)[2] for kind, vertices in first if kind == "T" and vertices[0][2] > 0]
    bottom = [_normal(*vertices)[2] for kind, vertices in first if kind == "T" and vertices[0][2] == 0]
    assert all(value > 0 for value in top)
    assert all(value < 0 for value in bottom)
    assert sum(abs(_normal(*vertices)[2]) / 2 for kind, vertices in first if kind == "T") == pytest.approx(shape.area * 2e-12)
    edges = {}
    for _, vertices in first:
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            key = tuple(sorted((a, b)))
            edges.setdefault(key, []).append((a, b))
    assert all(len(values) == 2 and values[0] == (values[1][1], values[1][0]) for values in edges.values())
    assert len(edges) > 0


def test_serialized_inputs_have_comments_and_stable_bytes():
    shape = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    faces = MODULE.extrude_faces(shape, 0, 1)
    payload = ("* SPD Decap PI Evaluator v0.23.1 D116-SHADOW raw-W0 L29/L30 gap coupon\n" + "\n".join(MODULE._panel_line("X", kind, vertices) for kind, vertices in faces) + "\n").encode()
    assert payload == ("* SPD Decap PI Evaluator v0.23.1 D116-SHADOW raw-W0 L29/L30 gap coupon\n" + "\n".join(MODULE._panel_line("X", kind, vertices) for kind, vertices in MODULE.extrude_faces(shape, 0, 1)) + "\n").encode()
    assert payload.splitlines()[0].startswith(b"*")
    listing = b"* SPD Decap PI Evaluator v0.23.1 D116-SHADOW raw-W0 L29/L30 gap coupon\nC X.qui 3.4 0 0 0\n"
    assert listing.splitlines()[0].startswith(b"*")


def test_tampered_governing_receipt_fails_closed(tmp_path):
    layers = [{"layer_name": n, "thickness_um": b - a, "depth_from_stack_top_um": {"top": a, "bottom": b}} for n, a, b in [("Medium$DR2829", 1917.0, 1947.0), ("Signal$L29(DGND)", 1947.0, 1967.0), ("Medium$DR2930", 1967.0, 1997.0), ("Signal$L30(OTHER_POWER1)", 1997.0, 2017.0), ("Medium$DR3031", 2017.0, 2047.0)]]
    receipt = {"program": MODULE.PROGRAM, "version": MODULE.VERSION, "status": "PASS", "source": {"path": MODULE.EXPECTED_SOURCE_PATH, "sha256": MODULE.EXPECTED_SOURCE, "size_bytes": 1116717287}, "stackup_layers": layers, "dielectric_points": [{"layer_name": n, "frequency_hz": 1_000_000.0, "epsilon_r": 3.4, "loss_tangent": 0.0041} for n in ("Medium$DR2829", "Medium$DR2930", "Medium$DR3031")]}
    MODULE._validate_d103(receipt)
    receipt["dielectric_points"][1]["epsilon_r"] = 3.3
    with pytest.raises(ValueError, match="DR2930"):
        MODULE._validate_d103(receipt)


@pytest.mark.skipif(not all(path.is_file() for path in EVIDENCE), reason="authoritative D: evidence unavailable")
def test_production_receipts_materialize(tmp_path):
    droot = Path(r"D:\SPD-Decap-PI-Evaluator-W7")
    args = type("Args", (), {
        "d103": droot / "8177f7a82715979652d7dcb3cd7bfd2770746133/260729-d103-source-stackup-material-receipt-02/stackup_material_receipt.json",
        "d104_root": droot / "fb596d929427d926f091df380b88f162f830483d/260729-d104-source-local-window-geometry-01",
        "d104": droot / "fb596d929427d926f091df380b88f162f830483d/260729-d104-source-local-window-geometry-01/geometry_receipt.json",
        "d115b": droot / "e2f219e71d8c8a397009f72242cce10d78cfc7ab/260902-d115b-source-plane-ownership-materialization-04/d115b_source_plane_ownership_materialization_receipt.json",
        "d115c": droot / "e2f219e71d8c8a397009f72242cce10d78cfc7ab/260903-d115c-source-local-port-window-audit-02/d115c_source_local_port_window_receipt.json",
        "output": tmp_path / "materialized",
    })()
    receipt = MODULE._materialize(args)
    assert receipt["status"] == "PASS_D116_SHADOW_GAP_COUPON"
    assert receipt["partition"] == {"A": [259, 264], "F": [262]}
    assert sorted(p.name for p in args.output.iterdir()) == ["A_GND_259.qui", "A_PWR_264.qui", "F_DDRL_262.qui", "d116_shadow_gap_coupon.lst", "d116_shadow_gap_coupon_receipt.json"]
    assert receipt["scope"]["full_domain"] is False and receipt["scope"]["artificial_w0_boundary_side_closures"] is True and receipt["scope"]["powersi_zii_readiness"] is False and receipt["scope"]["production_oracle"] is False
    assert receipt["conductors"][0]["source_wkb"]["area_um2"] > 0 and receipt["conductors"][0]["intersection"]["hole_count"] == 1
    assert receipt["expected_solver_labels"] == ["g1_A_GND_259", "g2_A_PWR_264", "g3_F_DDRL_262"]
    pair = next(item for item in receipt["pairwise_clearance"] if item["ordinals"] == [262, 264])
    assert pair["xy_disjoint"] and pair["xy_clearance_um"] == pytest.approx(49.9995205, abs=1e-6)
    assert receipt["material"]["description"] == "homogeneous 1 MHz material snapshot only" and receipt["material"]["loss_tangent_modeled"] is False
    assert receipt["scope"]["omitted_layers"] == ["L28", "L31"]
    second_args = type("Args", (), {"d103": args.d103, "d104_root": args.d104_root, "d104": args.d104, "d115b": args.d115b, "d115c": args.d115c, "output": tmp_path / "materialized_again"})()
    MODULE._materialize(second_args)
    for path in args.output.iterdir():
        assert path.read_bytes() == (second_args.output / path.name).read_bytes()
