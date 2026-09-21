from __future__ import annotations

import hashlib
import json
import sys
import ast
from types import SimpleNamespace
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from tools.research import d117_triangle_cell264 as module


D103 = Path(r"D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json")
D104_ROOT = Path(r"D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01")


def test_visible_product_version_and_immutable_caps():
    assert module.PRODUCT == "SPD Decap PI Evaluator"
    assert module.VERSION == "0.23.1"
    assert module.ARTIFACT_ROOT == Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell264-stage1ae-q15-replay-01")
    assert module.CAPS == {"max_original_vertices": 16384, "max_source_vertices": 16384, "max_pslg_vertices": 32768, "max_mesh_vertices": 262144, "max_mesh_faces": 524288}
    assert module.MAX_OUTPUT_BYTES == 201326592


def test_actual_sealed_inputs_read_only_no_triangle_mesh():
    before = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    shape, thickness, identity = module._validate_c0_and_inputs(D103, D104_ROOT)
    after = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    assert before == after
    assert shape.geom_type == "Polygon" and thickness == 20.0
    assert identity["ordinal"] == 264 and identity["layer"] == module.LAYER
    assert D103.resolve() == module.D103_PATH.resolve()
    assert D104_ROOT.resolve() == module.D104_ROOT.resolve()


def test_cap_application_isolated_stage0_import():
    before = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    isolated = module._load_stage0_with_caps()
    after = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    assert before == after
    assert isolated.MAX_ORIGINAL_VERTICES == 16384
    assert isolated.MAX_SOURCE_VERTICES == 16384
    assert isolated.MAX_PSLG_VERTICES == 32768
    assert isolated.MAX_MESH_VERTICES == 262144
    assert isolated.MAX_MESH_FACES == 524288


def test_static_sidewall_precheck_actual_full_cell_without_triangle():
    before = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    shape, thickness, _ = module._validate_c0_and_inputs(D103, D104_ROOT)
    isolated = module._load_stage0_with_caps()
    metrics = module._static_sidewall_precheck(isolated, shape, thickness)
    after = {name for name in sys.modules if name == "triangle" or name.startswith("triangle.")}
    assert before == after
    assert metrics["vertex_count"] == metrics["segment_count"] == 18052
    assert metrics["marker_count"] == 15297 and metrics["hole_count"] == 670
    assert metrics["max_segment_length_um"] == pytest.approx(76.29000000000087, abs=1e-9)
    assert metrics["minimum_sidewall_angle_deg"] > 7.5 and metrics["maximum_sidewall_aspect"] <= 8.0
    print("SIDEWALL_METRICS=" + json.dumps(metrics, sort_keys=True, separators=(",", ":")))


def test_header_relabel_is_deterministic_and_preserves_prelabel_hash():
    old = f"{module.PRODUCT} v{module.VERSION} D117 W0 geometry-only\n".encode("ascii")
    prelabel = old + b"v 0 0 0\nf 0 0 0\n"
    canonical, digest, size = module._relabel_canonical(prelabel)
    assert canonical.startswith(f"{module.PRODUCT} v{module.VERSION} D117 Stage1 full-cell264 geometry-only\n".encode("ascii"))
    assert digest == hashlib.sha256(prelabel).hexdigest() and size == len(prelabel)
    assert module._relabel_canonical(prelabel) == (canonical, digest, size)
    with pytest.raises(module.Refusal):
        module._relabel_canonical(b"wrong\n")


def test_payload_scope_supply_and_quality_contract():
    payload = module._payload({"ordinal": 264}, {"minimum_angle_deg": 8.0}, {"implementation": "CPython"}, "abc", 10)
    assert payload["status"] == "PASS_STAGE1_TRIANGLE_CELL264_RESEARCH_PILOT"
    assert payload["geometry_only"] and payload["research_only"] and payload["non_oracle"] and payload["nonproduction"]
    assert payload["solver_executed"] is False and payload["shipped_dependency"] is False
    assert payload["stage1_success_does_not_authorize_cell258"] is True
    assert payload["supply_chain"]["wheel_sha256"] == module.WHEEL_SHA256
    assert payload["license_boundary"]["legal_review_required"] is True


def test_atomic_output_confinement_and_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ARTIFACT_ROOT", tmp_path)
    out = tmp_path / "replay-01"
    module._write_atomic(out, b"canonical", b"{}\n")
    assert sorted(p.name for p in out.iterdir()) == ["mesh.canonical.txt", "mesh_receipt.json"]
    with pytest.raises(module.Refusal):
        module._write_atomic(tmp_path / "replay-01", b"x", b"{}")
    with pytest.raises(module.Refusal):
        module._write_atomic(tmp_path / "escape", b"x", b"{}")


def test_no_network_or_child_process_imports_in_wrapper_source():
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "import subprocess" not in text
    assert "import socket" not in text
    assert "os.system" not in text


def test_run_output_preflight_precedes_input_or_triangle_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ARTIFACT_ROOT", tmp_path)
    called = []
    monkeypatch.setattr(module, "_validate_c0_and_inputs", lambda *args: called.append(args))
    args = SimpleNamespace(
        output=str(tmp_path / "escape"),
        d103=str(D103),
        d104_root=str(D104_ROOT),
        triangle_site=str(tmp_path / "site"),
    )
    with pytest.raises(module.Refusal, match="output confinement"):
        module.run(args)
    assert called == []


def test_relabel_rejects_crlf_and_payload_is_byte_deterministic():
    old = f"{module.PRODUCT} v{module.VERSION} D117 W0 geometry-only\n".encode("ascii")
    with pytest.raises(module.Refusal):
        module._relabel_canonical(old.replace(b"\n", b"\r\n"))
    with pytest.raises(module.Refusal):
        module._relabel_canonical(old + b"x\x00\n")
    kwargs = ({"ordinal": 264}, {"minimum_angle_deg": 8.0}, {"implementation": "CPython"}, "abc", 10)
    left = json.dumps(module._payload(*kwargs), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    right = json.dumps(module._payload(*kwargs), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value))
        return set()
    volatile = {"time", "timestamp", "pid", "process_id", "elapsed"}
    assert left == right and not (keys(module._payload(*kwargs)) & volatile)


def test_malformed_stage0_result_refuses_before_any_indexing():
    shape = Polygon([(0, 0), (1, 0), (0, 1)])
    with pytest.raises(module.Refusal, match="malformed Stage0 mesh result"):
        module._validate_mesh_result({"vertices": [[0, 0]], "faces": [[0, 1, 2]]}, shape, 20.0)


def test_c0_contract_and_payload_pin_scaling_and_identity():
    assert module.TRIANGLE_OPTIONS == "pq15Cz"
    assert module.C0_SIZE == 268209
    assert module.C0_SHA256 == "ad6ec33da60f0f5c1dca232423efa3fa6ed90b863046fff8862df252b3df287a"
    assert module.CAP_CONTRACT_SIZE == 4767
    assert module.CAP_CONTRACT_SHA256 == "a2810203bef1199d23854c849acce8a1f5f470d58328c391247e9eef2eaa94de"
    payload = module._payload({}, {}, {}, "x", 1)
    assert payload["c0_census"]["pslg_canonical_bytes"] == 1231835
    assert payload["c0_census"]["pslg_canonical_sha256"].startswith("edc3228f")
    assert payload["quality_resource_contract"]["options"] == module.TRIANGLE_OPTIONS
    assert payload["caps"]["resource_contract"]["artifact_bytes"] == module.MAX_ARTIFACT_BYTES


def test_cap_contract_tamper_and_direct_canonical_path_refusal(monkeypatch, tmp_path):
    bad = {"program": module.PRODUCT, "version": module.VERSION, "status": "CAPS_PREREGISTERED_NOT_EXECUTED", "scope": "bad", "caps": {}}
    monkeypatch.setattr(module, "_load_json", lambda _path: bad)
    with pytest.raises(module.Refusal):
        module._validate_cap_contract()
    with pytest.raises(module.Refusal):
        module._validate_c0_and_inputs(tmp_path / "d103.json", tmp_path)


def test_reparse_attribute_gate_via_lstat_monkeypatch(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_file_attributes=0x400))
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    if hasattr(Path, "is_junction"):
        monkeypatch.setattr(Path, "is_junction", lambda self: False)
    assert module._is_reparse(tmp_path / "entry") is True
    monkeypatch.setattr(module, "_is_reparse", lambda _p: True)
    with pytest.raises(module.Refusal, match="missing/reparse input"):
        module._sha(tmp_path / "entry")


def test_runtime_gate_precedes_stage0_loader(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(module, "_validate_cap_contract", lambda: {})
    monkeypatch.setattr(module, "_validate_c0_and_inputs", lambda *_: (SimpleNamespace(area=1.0, bounds=(0.0, 0.0, 1.0, 1.0)), 20.0, {}))
    monkeypatch.setattr(module, "_load_stage0_with_caps", lambda: (_ for _ in ()).throw(AssertionError("Stage0 loader called")))
    monkeypatch.setattr(module.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(module.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(module.sys, "platform", "linux")
    args = SimpleNamespace(output=str(tmp_path / "replay-01"), d103=str(D103), d104_root=str(D104_ROOT), triangle_site=str(tmp_path / "site"))
    with pytest.raises(module.Refusal, match="runtime compatibility"):
        module.run(args)


def test_triangle_version_gate_precedes_mesh_call(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(module, "_validate_cap_contract", lambda: {})
    monkeypatch.setattr(module, "_validate_c0_and_inputs", lambda *_: (SimpleNamespace(area=1.0), 20.0, {}))
    monkeypatch.setattr(module.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(module.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.sys, "maxsize", 2**63)
    fake = SimpleNamespace(AUTHORIZED_SITE=tmp_path / "site")
    fake.AUTHORIZED_SITE.mkdir()
    fake.load_triangle_site = lambda _site: SimpleNamespace(__version__="tampered")
    fake.mesh_polygon = lambda *_: (_ for _ in ()).throw(AssertionError("mesh called"))
    monkeypatch.setattr(module, "_load_stage0_with_caps", lambda: fake)
    monkeypatch.setattr(module, "_static_sidewall_precheck", lambda *_: {})
    args = SimpleNamespace(output=str(tmp_path / "replay-01"), d103=str(D103), d104_root=str(D104_ROOT), triangle_site=str(fake.AUTHORIZED_SITE))
    with pytest.raises(module.Refusal, match="Triangle version"):
        module.run(args)


def test_output_size_and_reparse_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 1)
    with pytest.raises(module.Refusal, match="per-replay output cap"):
        module._write_atomic(tmp_path / "replay-01", b"canonical", b"{}")
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 201326592)
    monkeypatch.setattr(module, "_is_reparse", lambda p: p == tmp_path / "replay-01")
    with pytest.raises(module.Refusal):
        module._write_atomic(tmp_path / "replay-01", b"x", b"{}")


def test_cli_version_and_ast_forbidden_calls(capsys):
    with pytest.raises(SystemExit) as exc:
        module.main(["--version"])
    assert exc.value.code == 0 and module.VERSION in capsys.readouterr().out
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
    imported.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
    assert not imported.intersection({"socket", "subprocess"}) and "accuracy_parse" not in Path(module.__file__).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def synthetic_floor_result():
    vcount, tcount, boundary, holes = 36104, 19390, 18052, 670
    canonical = (f"{module.PRODUCT} v{module.VERSION} D117 W0 geometry-only\n" + "".join(f"v {i}.0 0.0 0.0\n" for i in range(vcount)) + "".join("f 0 1 2\n" for _ in range(74884))).encode("ascii")
    source_area = 361720074.03946954
    expected_volume = source_area * 20.0
    cert = {"vertex_count": vcount, "face_count": 74884, "edge_count": 112326, "minimum_angle_deg": 8.0, "maximum_aspect_ratio": 7.0, "aspect_definition": "longest edge / shortest altitude", "source_area_um2": source_area, "triangle_area_sum_um2": source_area, "triangle_area_error_um2": 0.0, "coverage_tolerance": max(1e-7, source_area * 1e-12), "symmetric_difference_area_um2": 0.0, "signed_volume": expected_volume, "expected_volume": expected_volume, "volume_error": 0.0, "volume_tolerance": max(1e-7, expected_volume * 1e-9), "volume_origin_um": [0.0, 0.0, 0.0], "coordinate_tolerance": 1e-9, "parameter_tolerance": 1e-12, "original_pslg_vertex_count": 15297, "original_pslg_segment_count": 15297, "split_pslg_vertex_count": 18052, "split_pslg_segment_count": 18052, "marker_count": 15297, "boundary_edge_count": boundary, "marked_segment_count": boundary, "hole_count": holes, "expected_genus": holes, "euler_characteristic": -1338, "closed_face_estimate": 74884, "closed_vertex_estimate": vcount, "two_d_vertex_count": 18052, "two_d_triangle_count": tcount, "options": module.TRIANGLE_OPTIONS, "boundary_side_outward_proof": "constructed right-hand boundary sides", "all_faces_triangles": True, "finite_vertices_and_faces": True, "nonzero_face_areas": True, "edge_incidence_all_two": True, "edge_incidence_all_two_opposite": True, "top_bottom_opposite": True, "holes_preserved": True, "boundary_marker_chains_complete": True, "geometry_only": True, "research_only": True, "non_oracle": True, "nonproduction": True, "solver_executed": False, "duplicate_2d_vertices": False, "duplicate_2d_faces": False, "duplicate_3d_vertices": False, "duplicate_3d_faces": False, "canonical_sha256": hashlib.sha256(canonical).hexdigest(), "canonical_bytes": len(canonical)}
    cert["volume_origin_um"] = [-40600.0, 125.0, 0.0]
    result = {"vertices": [(float(i), 0.0, 0.0) for i in range(vcount)], "faces": [(0, 1, 2)] * 74884, "canonical": canonical, "certificate": cert}
    return SimpleNamespace(area=source_area, bounds=(-40600.0, 125.0, -10765.0, 13435.0)), result


def test_synthetic_floor_certificate_acceptance(synthetic_floor_result):
    shape, result = synthetic_floor_result
    module._validate_mesh_result(result, shape, 20.0)


def test_synthetic_refined_boundary_counts_are_not_fixed_to_input_segments(synthetic_floor_result):
    shape, original = synthetic_floor_result
    vcount, tcount, boundary, fcount = 36108, 19392, 18054, 74892
    canonical = (f"{module.PRODUCT} v{module.VERSION} D117 W0 geometry-only\n" + "".join(f"v {i}.0 0.0 0.0\n" for i in range(vcount)) + "".join("f 0 1 2\n" for _ in range(fcount))).encode("ascii")
    cert = {**original["certificate"], "vertex_count": vcount, "face_count": fcount, "edge_count": 112338, "boundary_edge_count": boundary, "marked_segment_count": boundary, "two_d_vertex_count": 18054, "two_d_triangle_count": tcount, "closed_vertex_estimate": vcount, "closed_face_estimate": fcount, "canonical_sha256": hashlib.sha256(canonical).hexdigest(), "canonical_bytes": len(canonical)}
    result = {"vertices": [(float(i), 0.0, 0.0) for i in range(vcount)], "faces": [(0, 1, 2)] * fcount, "canonical": canonical, "certificate": cert}
    module._validate_mesh_result(result, shape, 20.0)


@pytest.mark.parametrize("field,value", [("minimum_angle_deg", 7.5), ("maximum_aspect_ratio", 8.1), ("marked_segment_count", 18051), ("two_d_triangle_count", 19389), ("triangle_area_error_um2", 1.0), ("volume_error", 8.0), ("volume_origin_um", [float("nan")]), ("euler_characteristic", -1337), ("solver_executed", True), ("duplicate_3d_faces", True), ("boundary_side_outward_proof", "wrong")])
def test_synthetic_certificate_mutations_refuse(synthetic_floor_result, field, value):
    shape, original = synthetic_floor_result
    result = {**original, "certificate": {**original["certificate"], field: value}}
    with pytest.raises(module.Refusal):
        module._validate_mesh_result(result, shape, 20.0)


def test_missing_certificate_key_refuses(synthetic_floor_result):
    shape, original = synthetic_floor_result
    cert = {**original["certificate"]}
    cert.pop("edge_incidence_all_two_opposite")
    with pytest.raises(module.Refusal, match="incomplete Stage0 certificate"):
        module._validate_mesh_result({**original, "certificate": cert}, shape, 20.0)
