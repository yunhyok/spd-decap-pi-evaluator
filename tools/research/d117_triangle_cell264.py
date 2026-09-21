"""D117 Stage1 full-cell264 research wrapper around the sealed Stage0 engine."""
from __future__ import annotations
import argparse, hashlib, importlib.metadata, importlib.util, json, math, os, platform, sys
from pathlib import Path

PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
TRIANGLE_OPTIONS = "pq15Cz"
ARTIFACT_ROOT = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell264-stage1ae-q15-replay-01")
STAGE0_PATH = Path(r"C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\tools\research\d117_triangle_quality_mesh.py")
STAGE0_SIZE = 25832
STAGE0_SHA256 = "f43a4dd3ab531b75c2f8a450406ddb70bf92cf47a94e67f2dbcede207b9c4bdd"
D103_PATH = Path(r"D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json")
D104_ROOT = Path(r"D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01")
C0_RECEIPT = ARTIFACT_ROOT / "stage1_c0_census_receipt.json"
C0_SIZE = 268209
C0_SHA256 = "ad6ec33da60f0f5c1dca232423efa3fa6ed90b863046fff8862df252b3df287a"
CAP_CONTRACT = ARTIFACT_ROOT / "stage1_cap_contract.json"
CAP_CONTRACT_SIZE = 4767
CAP_CONTRACT_SHA256 = "a2810203bef1199d23854c849acce8a1f5f470d58328c391247e9eef2eaa94de"
D103_SIZE, D103_SHA256 = 204735, "4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9"
D104_SIZE, D104_SHA256 = 19728, "bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b"
SOURCE_SIZE, SOURCE_SHA256 = 258181, "a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5"
LAYER = "Signal$L30(OTHER_POWER1)"
NET = "ADC_VDD_180_VQPS_SYS_1_AON/0"
ISLAND = "spd-surface-island:cb8510a79529b7f6f4f4afd4"
WHEEL_NAME = "triangle-20250106-cp312-cp312-win_amd64.whl"
WHEEL_SIZE = 1426720
WHEEL_SHA256 = "0327032a7984a7262180ef2ddd78b36dfdcdecbc79f0f9f173732ce7c670b8ed"
PYPI_URL = "https://pypi.org/pypi/triangle/20250106/json"
RELEASE_URL = "https://pypi.org/project/triangle/20250106/"
WHEEL_URL = "https://files.pythonhosted.org/packages/a1/a5/4a09c3f9d2687d8752c912a97f2c5086cdd83721b3b13f8288f13b771fa7/triangle-20250106-cp312-cp312-win_amd64.whl"
WRAPPER_URL = "https://github.com/drufat/triangle/blob/master/LICENSE"
UPSTREAM_URL = "https://www.cs.cmu.edu/~quake/triangle.html"
CAPS = {"max_original_vertices": 16384, "max_source_vertices": 16384, "max_pslg_vertices": 32768, "max_mesh_vertices": 262144, "max_mesh_faces": 524288}
MAX_OUTPUT_BYTES = 201326592
MAX_ARTIFACT_BYTES = 536870912


class Refusal(RuntimeError):
    pass


def _is_reparse(path: Path) -> bool:
    try:
        st = path.lstat()
        junction = getattr(path, "is_junction", None)
        return path.is_symlink() or (junction is not None and junction()) or bool(getattr(st, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise Refusal(f"unreadable/reparse path: {path}") from exc


def _sha(path: Path):
    if _is_reparse(path) or not path.is_file():
        raise Refusal(f"missing/reparse input: {path}")
    h = hashlib.sha256(); n = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block); n += len(block)
    return n, h.hexdigest()


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Refusal(f"invalid JSON: {path}") from exc


def _validate_cap_contract():
    if _sha(CAP_CONTRACT) != (CAP_CONTRACT_SIZE, CAP_CONTRACT_SHA256):
        raise Refusal("cap contract identity mismatch")
    contract = _load_json(CAP_CONTRACT)
    caps = contract.get("caps", {})
    expected_caps = {"MAX_ORIGINAL_VERTICES": 16384, "MAX_PSLG_VERTICES": 32768, "MAX_CLOSED_VERTICES": 262144, "MAX_CLOSED_FACES": 524288, "guard_active_process_limit": 1, "monitored_working_set_ceiling_bytes": 6442450944, "job_process_memory_limit_bytes": 8589934592, "job_peak_process_memory_ceiling_bytes": 8589934592, "wall_time_seconds": 1800, "per_replay_output_bytes": 201326592, "total_stage1_artifact_bytes": 536870912}
    if contract.get("program") != PRODUCT or contract.get("version") != VERSION or contract.get("status") != "CAPS_PREREGISTERED_NOT_EXECUTED" or contract.get("scope") != "D104 full unclipped cell264 research-only zero-solver pilot" or any(caps.get(k) != v for k, v in expected_caps.items()):
        raise Refusal("cap contract semantics mismatch")
    rationale = contract.get("rationale", {})
    if rationale.get("census_original_vertices") != 15297 or rationale.get("census_split_pslg_vertices") != 18052 or rationale.get("source_ring_floor") != {"vertices": 30594, "faces": 63864} or rationale.get("split_pslg_floor") != {"vertices": 36104, "faces": 74884} or rationale.get("no_final_cap_relaxation_on_failure") is not True:
        raise Refusal("cap contract scaling semantics mismatch")
    engine = contract.get("engine_contract", {})
    if engine.get("stage0_module_sha256") != STAGE0_SHA256 or engine.get("stage0_module_size_bytes") != STAGE0_SIZE or engine.get("triangle_version") != "20250106" or engine.get("triangle_tag") != "cp312-cp312-win_amd64" or engine.get("options") != TRIANGLE_OPTIONS or engine.get("minimum_angle_strictly_greater_than_deg") != 7.5 or engine.get("aspect_max") != 8.0 or engine.get("aspect_definition") != "longest edge / shortest altitude" or engine.get("no_d115_w0_intersection") is not True:
        raise Refusal("cap contract engine semantics mismatch")
    supply = contract.get("supply_chain", {}); license_boundary = contract.get("license_boundary", {})
    baseline = contract.get("resource_baseline", {})
    if supply.get("metadata_url") != PYPI_URL or supply.get("release_url") != RELEASE_URL or supply.get("wheel_url") != WHEEL_URL or supply.get("wheel_filename") != WHEEL_NAME or supply.get("wheel_size") != WHEEL_SIZE or supply.get("wheel_sha256") != WHEEL_SHA256 or supply.get("artifact_local") is not True or supply.get("shipped_dependency") is not False or baseline.get("working_set_distinct_from_job_commit_counter") is not True or engine.get("coverage_holes_marker_chains_oriented_incidence_euler_signed_volume_finite_duplicate_gates") is not True or license_boundary.get("wrapper_license_url") != WRAPPER_URL or license_boundary.get("upstream_notice_url") != UPSTREAM_URL or license_boundary.get("upstream_terms") != "restrictive private/research/institutional use versus commercial distribution by direct arrangement" or license_boundary.get("research_only_external_tool") is not True or license_boundary.get("legal_review_required") is not True or license_boundary.get("not_legal_advice") is not True or license_boundary.get("shipped_dependency") is not False:
        raise Refusal("cap contract supply/license semantics mismatch")
    return {"path": "stage1_cap_contract.json", "size_bytes": CAP_CONTRACT_SIZE, "sha256": CAP_CONTRACT_SHA256, "status": contract["status"], "scope": contract["scope"], "caps": caps, "engine_contract": engine, "supply_chain": supply, "license_boundary": license_boundary}


def _validate_c0_and_inputs(d103: Path, d104_root: Path):
    try:
        d103 = d103.resolve(strict=True); d104_root = d104_root.resolve(strict=True)
        canonical_d103 = D103_PATH.resolve(strict=True); canonical_d104_root = D104_ROOT.resolve(strict=True)
    except OSError as exc:
        raise Refusal("canonical D103/D104 path unavailable") from exc
    if d103 != canonical_d103 or d104_root != canonical_d104_root:
        raise Refusal("canonical D103/D104 path mismatch")
    if _sha(STAGE0_PATH) != (STAGE0_SIZE, STAGE0_SHA256) or _sha(C0_RECEIPT) != (C0_SIZE, C0_SHA256):
        raise Refusal("sealed Stage0/C0 identity mismatch")
    if _sha(d103) != (D103_SIZE, D103_SHA256):
        raise Refusal("D103 identity mismatch")
    d104 = d104_root / "geometry_receipt.json"
    if _sha(d104) != (D104_SIZE, D104_SHA256):
        raise Refusal("D104 identity mismatch")
    c0 = _load_json(C0_RECEIPT); j3 = _load_json(d103); j4 = _load_json(d104)
    if c0.get("program") != PRODUCT or c0.get("version") != VERSION or c0.get("status") != "PASS_STAGE1_C0_CENSUS":
        raise Refusal("C0 status mismatch")
    if c0.get("research_only") is not True or c0.get("non_shipped") is not True or c0.get("solver_executed") is not False or c0.get("triangle_extension_loaded") is not False or c0.get("triangle_modules_before") != [] or c0.get("triangle_modules_after") != []:
        raise Refusal("C0 scope/extension mismatch")
    sm = c0.get("stage0_module", {})
    if sm.get("path") != str(STAGE0_PATH) or sm.get("size_bytes") != STAGE0_SIZE or sm.get("sha256") != STAGE0_SHA256:
        raise Refusal("C0 Stage0 identity mismatch")
    ci = c0.get("inputs", {})
    if ci.get("d103", {}).get("path") != str(D103_PATH) or ci.get("d103", {}).get("size_bytes") != D103_SIZE or ci.get("d103", {}).get("sha256") != D103_SHA256 or ci.get("d104", {}).get("root") != str(D104_ROOT) or ci.get("d104", {}).get("path") != str(D104_ROOT / "geometry_receipt.json") or ci.get("d104", {}).get("size_bytes") != D104_SIZE or ci.get("d104", {}).get("sha256") != D104_SHA256 or ci.get("source", {}).get("path") != str(D104_ROOT / "cell_0264.wkb") or ci.get("source", {}).get("filename") != "cell_0264.wkb" or ci.get("source", {}).get("size_bytes") != SOURCE_SIZE or ci.get("source", {}).get("sha256") != SOURCE_SHA256 or ci.get("ordinal") != 264 or ci.get("layer") != LAYER or ci.get("net") != NET or ci.get("island") != ISLAND or ci.get("thickness_um") != 20.0:
        raise Refusal("C0 input identity mismatch")
    sf = c0.get("topology_floor", {}).get("source_ring_floor", {}); pf = c0.get("topology_floor", {}).get("split_pslg_floor", {})
    if (sf.get("minimum_2d_triangles"), sf.get("minimum_extruded_vertices"), sf.get("minimum_extruded_faces")) != (16635, 30594, 63864) or (pf.get("minimum_2d_triangles"), pf.get("minimum_extruded_vertices"), pf.get("minimum_extruded_faces")) != (19390, 36104, 74884):
        raise Refusal("C0 floor mismatch")
    if c0.get("split_rule", {}).get("step_um") != 80.0 or c0.get("proof_scope", {}).get("pslg_boundary_chain_proof") is not True or c0.get("proof_scope", {}).get("side_wall_outward_oriented_manifold_signed_volume_deferred") is not True:
        raise Refusal("C0 split/proof scope mismatch")
    pr = c0.get("pslg_returned", {})
    geom = c0.get("geometry", {})
    if geom.get("geom_type") != "Polygon" or geom.get("area_um2") != 361720074.03946954 or geom.get("bounds_um") != [-40600.0, 125.0, -10765.0, 13435.0] or geom.get("exact_coordinate_bounds_um") != [-40600.0, 125.0, -10765.0, 13435.0] or geom.get("ring_count") != 671 or geom.get("original_vertex_count") != 15297 or geom.get("hole_count") != 670 or pr.get("vertex_count") != 18052 or pr.get("segment_count") != 18052 or pr.get("marked_segment_count") != 18052 or pr.get("marker_count") != 15297 or pr.get("hole_point_count") != 670 or pr.get("canonical_bytes") != 1231835 or pr.get("canonical_sha256") != "edc3228fb0894c37ff458233d20db7fccb0f35a8e83ca68687c4620788795077" or abs(float(pr.get("max_returned_segment_length_um", 0)) - 76.29000000000087) > 1e-9 or any(pr.get(k) is not True for k in ("finite_unique_vertices", "segments_valid_unique_nonzero", "marker_chains_complete", "hole_points_strictly_inside")):
        raise Refusal("C0 census mismatch")
    if c0.get("topology_floor", {}).get("split_pslg_floor", {}).get("expected_genus") != 670 or c0.get("topology_floor", {}).get("split_pslg_floor", {}).get("expected_euler_characteristic") != -1338:
        raise Refusal("C0 topology floor mismatch")
    if j3.get("status") != "PASS":
        raise Refusal("D103 status mismatch")
    layers = [x for x in j3.get("stackup_layers", []) if x.get("layer_name") == LAYER]
    if len(layers) != 1:
        raise Refusal("D103 L30 cardinality")
    l30 = layers[0]
    if (l30.get("ordinal"), l30.get("raw_layer_ordinal"), l30.get("layer_kind"), l30.get("thickness_origin"), l30.get("thickness_um")) != (58, 58, "conductor", "source", 20.0):
        raise Refusal("D103 L30 identity")
    if j4.get("status") != "PASS":
        raise Refusal("D104 status mismatch")
    rows = [x for x in j4.get("cells", []) if x.get("ordinal") == 264]
    if len(rows) != 1:
        raise Refusal("D104 ordinal264 cardinality")
    row = rows[0]; g = row.get("geometry")
    expected_bbox = [-40600.0, 125.0, -10765.0, 13435.0]
    if row.get("layer") != LAYER or row.get("net") != NET or row.get("island_id") != ISLAND or not isinstance(g, dict):
        raise Refusal("D104 row identity")
    if (g.get("filename"), g.get("wkb_size_bytes"), g.get("wkb_sha256"), g.get("component_count"), g.get("exterior_ring_count"), g.get("ring_count"), g.get("hole_count"), g.get("vertex_count"), g.get("hole_vertex_count"), g.get("area"), g.get("area_um2"), g.get("bbox"), g.get("bbox_um"), g.get("curve"), g.get("layer"), g.get("net"), g.get("island_id")) != ("cell_0264.wkb", SOURCE_SIZE, SOURCE_SHA256, 1, 1, 671, 670, 557, 14740, 361720074.03946954, 361720074.03946954, expected_bbox, expected_bbox, False, LAYER, NET, ISLAND):
        raise Refusal("D104 geometry semantics")
    source = d104_root / "cell_0264.wkb"
    if _sha(source) != (SOURCE_SIZE, SOURCE_SHA256):
        raise Refusal("source WKB identity mismatch")
    from shapely import wkb
    shape = wkb.loads(source.read_bytes())
    if shape.geom_type != "Polygon" or shape.is_empty or not shape.is_valid or len(shape.exterior.coords) - 1 != 557 or len(shape.interiors) != 670 or sum(len(r.coords) - 1 for r in shape.interiors) != 14740 or shape.area != 361720074.03946954 or tuple(shape.bounds) != tuple(expected_bbox):
        raise Refusal("full-cell Polygon semantics")
    return shape, 20.0, {"d103": {"path": str(d103), "size_bytes": D103_SIZE, "sha256": D103_SHA256}, "d104": {"path": str(d104), "root": str(d104_root), "size_bytes": D104_SIZE, "sha256": D104_SHA256}, "source": {"path": str(source), "filename": "cell_0264.wkb", "size_bytes": SOURCE_SIZE, "sha256": SOURCE_SHA256}, "ordinal": 264, "layer": LAYER, "net": NET, "island": ISLAND, "thickness_um": 20.0}


def _load_stage0_with_caps():
    if _sha(STAGE0_PATH) != (STAGE0_SIZE, STAGE0_SHA256):
        raise Refusal("Stage0 changed before import")
    spec = importlib.util.spec_from_file_location("d117_stage0_stage1_verified", STAGE0_PATH)
    if spec is None or spec.loader is None:
        raise Refusal("Stage0 loader unavailable")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    for name, value in CAPS.items():
        setattr(module, name.upper(), value)
    if _sha(STAGE0_PATH) != (STAGE0_SIZE, STAGE0_SHA256) or any(getattr(module, name.upper(), None) != value for name, value in CAPS.items()):
        raise Refusal("Stage0 changed or caps not applied")
    if getattr(module, "TRIANGLE_OPTIONS", None) != TRIANGLE_OPTIONS or "longest edge / shortest altitude" not in module._certify_3d.__code__.co_consts:
        raise Refusal("Stage0 quality/options contract mismatch")
    return module


def _static_sidewall_precheck(stage0, shape, thickness):
    try:
        pslg, originals = stage0.build_pslg(shape, thickness)
        vertices, segments, markers, holes = pslg["vertices"], pslg["segments"], pslg["segment_markers"], pslg["holes"]
    except Exception as exc:
        raise Refusal("sealed Stage0 PSLG precheck failed") from exc
    if len(vertices) != 18052 or len(segments) != 18052 or len(markers) != 18052 or len(originals) != 15297 or len(holes) != 670:
        raise Refusal("sealed Stage0 PSLG count mismatch")
    if set(markers) != set(range(1, 15298)) or len({tuple(x) for x in vertices}) != len(vertices):
        raise Refusal("sealed Stage0 PSLG uniqueness/markers mismatch")
    lengths = []; seen_edges = set()
    for index, edge in enumerate(segments):
        if len(edge) != 2 or any(type(i) is not int or i < 0 or i >= len(vertices) for i in edge) or edge[0] == edge[1]:
            raise Refusal("sealed Stage0 PSLG segment mismatch")
        key = tuple(sorted(edge))
        if key in seen_edges:
            raise Refusal("sealed Stage0 PSLG duplicate segment")
        seen_edges.add(key)
        lengths.append(math.dist(vertices[edge[0]], vertices[edge[1]]))
    if any(not math.isfinite(x) or x <= 0 for x in lengths):
        raise Refusal("nonfinite Stage0 PSLG segment")
    if not math.isfinite(float(thickness)) or float(thickness) <= 0:
        raise Refusal("invalid sidewall thickness")
    max_length = max(lengths)
    if not math.isclose(max_length, 76.29000000000087, rel_tol=0.0, abs_tol=1e-9):
        raise Refusal("sealed Stage0 PSLG maximum segment mismatch")
    side_angles = [math.degrees(math.atan(min(length / thickness, thickness / length))) for length in lengths]
    side_aspects = [length / thickness + thickness / length for length in lengths]
    min_angle = min(side_angles)
    max_aspect = max(side_aspects)
    min_index = min(range(len(lengths)), key=lengths.__getitem__); max_index = max(range(len(lengths)), key=lengths.__getitem__)
    if min_angle <= 7.5 or max_aspect > 8.0:
        raise Refusal(f"Stage1 sidewall quality precheck failed min_angle={min_angle:.17g} max_aspect={max_aspect:.17g} min_length={lengths[min_index]:.17g} min_index={min_index} min_marker={markers[min_index]} max_length={max_length:.17g} max_index={max_index} max_marker={markers[max_index]}")
    return {"vertex_count": len(vertices), "segment_count": len(segments), "marker_count": len(set(markers)), "hole_count": len(holes), "max_segment_length_um": max_length, "max_segment_index": max_index, "max_segment_marker": markers[max_index], "min_segment_length_um": lengths[min_index], "min_segment_index": min_index, "min_segment_marker": markers[min_index], "minimum_sidewall_angle_deg": min_angle, "maximum_sidewall_aspect": max_aspect, "thickness_um": float(thickness), "proof": "input split PSLG side triangles only; Triangle boundary subdivision remains post-mesh gated"}


def _payload(inputs, cert, runtime, prelabel_sha, prelabel_bytes, sidewall_precheck=None, cap_contract=None):
    return {
        "program": PRODUCT, "version": VERSION,
        "status": "PASS_STAGE1_TRIANGLE_CELL264_RESEARCH_PILOT",
        "d115c_overall_status_remains": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
        "three_serial_replays_required": True,
        "caps_not_valid_for_cell258_or_aggregate": True,
        "stage1_success_does_not_authorize_cell258": True,
        "stage1_success_does_not_authorize_aggregate": True,
        "stage1_success_does_not_authorize_production_or_shipping": True,
        "geometry_only": True, "full_cell_unclipped": True, "w0_clipped": False,
        "research_only": True, "non_oracle": True, "nonproduction": True,
        "solver_executed": False, "shipped_dependency": False,
        "cap_contract": cap_contract or {"path": "stage1_cap_contract.json", "size_bytes": CAP_CONTRACT_SIZE, "sha256": CAP_CONTRACT_SHA256},
        "c0_census": {"path": "stage1_c0_census_receipt.json", "size_bytes": C0_SIZE, "sha256": C0_SHA256, "pslg_canonical_bytes": 1231835, "pslg_canonical_sha256": "edc3228fb0894c37ff458233d20db7fccb0f35a8e83ca68687c4620788795077", "split_step_um": 80.0},
        "source_counts": {"original_vertices": 15297, "exterior_vertices": 557, "hole_vertices": 14740, "holes": 670, "split_pslg_vertices": 18052, "split_pslg_segments": 18052, "source_ring_floor_faces": 63864, "split_pslg_floor_faces": 74884},
        "inputs": inputs, "runtime": runtime,
        "caps": {**CAPS, "effective_final_closed_vertices_cap": CAPS["max_mesh_vertices"], "effective_final_closed_faces_cap": CAPS["max_mesh_faces"], "effective_2d_vertex_cap": CAPS["max_mesh_vertices"] // 2, "resource_contract": {"artifact_bytes": MAX_ARTIFACT_BYTES, "per_output_bytes": MAX_OUTPUT_BYTES}},
        "quality_resource_contract": {"options": TRIANGLE_OPTIONS, "minimum_angle_strictly_greater_than_deg": 7.5, "aspect_max": 8.0, "aspect_definition": "longest edge / shortest altitude", "coverage_holes_marker_chains_oriented_incidence_euler_signed_volume_finite_duplicate_gates": True, "no_cap_relaxation_on_failure": True},
        "certificate": cert, "sidewall_precheck": sidewall_precheck, "prelabel_canonical": {"sha256": prelabel_sha, "bytes": prelabel_bytes},
        "supply_chain": {"metadata_url": PYPI_URL, "release_url": RELEASE_URL, "wheel_url": WHEEL_URL, "wheel_filename": WHEEL_NAME, "wheel_size": WHEEL_SIZE, "wheel_sha256": WHEEL_SHA256, "wheel_tag": "cp312-cp312-win_amd64", "artifact_local": True, "shipped_dependency": False},
        "license_boundary": {"wrapper_license_url": WRAPPER_URL, "upstream_notice_url": UPSTREAM_URL, "wrapper": "LGPL-3.0 metadata", "upstream_terms": "restrictive private/research/institutional use versus commercial distribution by direct arrangement", "research_only_external_tool": True, "shipped_dependency": False, "legal_review_required": True, "not_legal_advice": True},
    }


def _artifact_bytes() -> int:
    if _is_reparse(ARTIFACT_ROOT):
        raise Refusal("artifact root reparse")
    total = 0
    for p in ARTIFACT_ROOT.rglob("*"):
        if _is_reparse(p):
            raise Refusal(f"artifact reparse entry: {p}")
        if p.is_file():
            total += p.stat().st_size
    return total


def _preflight_output(out: Path) -> None:
    root = ARTIFACT_ROOT.resolve(strict=True)
    if _is_reparse(ARTIFACT_ROOT) or any(_is_reparse(parent) for parent in out.parents if parent.exists()):
        raise Refusal("output ancestor reparse")
    if out.parent != root or out.name not in {"replay-01", "replay-02", "replay-03"}:
        raise Refusal("output confinement/name")
    temp = root / (out.name + ".tmp")
    if any(p.exists() or _is_reparse(p) for p in (out, temp)):
        raise Refusal("output final/temp already exists")
    if _artifact_bytes() > MAX_ARTIFACT_BYTES:
        raise Refusal("artifact total cap exceeded")


def _validate_mesh_result(result: dict, shape, thickness: float) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("canonical"), bytes) or not isinstance(result.get("certificate"), dict):
        raise Refusal("malformed Stage0 mesh result")
    cert = result["certificate"]; canonical = bytes(result["canonical"])
    if b"\r" in canonical or not canonical.isascii():
        raise Refusal("canonical encoding")
    required = ("vertex_count", "face_count", "edge_count", "minimum_angle_deg", "maximum_aspect_ratio", "source_area_um2", "triangle_area_sum_um2", "triangle_area_error_um2", "coverage_tolerance", "symmetric_difference_area_um2", "signed_volume", "expected_volume", "volume_error", "volume_tolerance", "volume_origin_um", "coordinate_tolerance", "parameter_tolerance", "canonical_sha256", "canonical_bytes", "original_pslg_vertex_count", "original_pslg_segment_count", "split_pslg_vertex_count", "split_pslg_segment_count", "marker_count", "boundary_edge_count", "marked_segment_count", "hole_count", "expected_genus", "euler_characteristic", "closed_face_estimate", "closed_vertex_estimate", "two_d_vertex_count", "two_d_triangle_count", "boundary_side_outward_proof")
    presence = required + ("options", "aspect_definition", "all_faces_triangles", "finite_vertices_and_faces", "nonzero_face_areas", "edge_incidence_all_two", "edge_incidence_all_two_opposite", "top_bottom_opposite", "holes_preserved", "boundary_marker_chains_complete", "geometry_only", "research_only", "non_oracle", "nonproduction", "solver_executed", "duplicate_2d_vertices", "duplicate_2d_faces", "duplicate_3d_vertices", "duplicate_3d_faces")
    if any(key not in cert for key in presence):
        raise Refusal("incomplete Stage0 certificate")
    int_fields = ("vertex_count", "face_count", "edge_count", "original_pslg_vertex_count", "original_pslg_segment_count", "split_pslg_vertex_count", "split_pslg_segment_count", "marker_count", "boundary_edge_count", "marked_segment_count", "hole_count", "expected_genus", "euler_characteristic", "closed_face_estimate", "closed_vertex_estimate", "two_d_vertex_count", "two_d_triangle_count", "canonical_bytes")
    if any(type(cert[key]) is not int for key in int_fields):
        raise Refusal("certificate count types")
    real_fields = ("minimum_angle_deg", "maximum_aspect_ratio", "source_area_um2", "triangle_area_sum_um2", "triangle_area_error_um2", "coverage_tolerance", "symmetric_difference_area_um2", "signed_volume", "expected_volume", "volume_error", "volume_tolerance", "coordinate_tolerance", "parameter_tolerance")
    if any(type(cert[key]) not in (int, float) or isinstance(cert[key], bool) or not math.isfinite(float(cert[key])) for key in real_fields):
        raise Refusal("certificate numeric types")
    if cert.get("options") != TRIANGLE_OPTIONS or cert.get("aspect_definition") != "longest edge / shortest altitude" or cert.get("original_pslg_vertex_count") != 15297 or cert.get("original_pslg_segment_count") != 15297 or cert.get("split_pslg_vertex_count") != 18052 or cert.get("split_pslg_segment_count") != 18052 or cert.get("marker_count") != 15297 or cert.get("marked_segment_count") != cert.get("boundary_edge_count") or cert.get("marked_segment_count", 0) < 18052 or cert.get("hole_count") != 670 or cert.get("expected_genus") != 670 or cert.get("euler_characteristic") != -1338 or cert.get("two_d_vertex_count", 0) < 18052 or cert.get("two_d_triangle_count", 0) < 19390 or cert.get("two_d_vertex_count", 0) > CAPS["max_mesh_vertices"] // 2 or cert.get("vertex_count", 0) < 36104 or cert.get("vertex_count", 0) > CAPS["max_mesh_vertices"] or cert.get("closed_vertex_estimate", 0) < 36104 or cert.get("closed_face_estimate", 0) < 74884 or cert.get("face_count", 0) > CAPS["max_mesh_faces"] or cert.get("face_count") != 2 * cert.get("two_d_triangle_count", 0) + 2 * cert.get("boundary_edge_count", 0):
        raise Refusal("Stage1 census certificate mismatch")
    if cert["minimum_angle_deg"] <= 7.5 or cert["maximum_aspect_ratio"] > 8.0:
        raise Refusal("quality threshold")
    numbers = [cert[key] for key in required if isinstance(cert[key], (int, float))]
    source_area = float(shape.area)
    tri_sum = float(cert["triangle_area_sum_um2"])
    area_residual = abs(source_area - tri_sum)
    expected_coverage_tolerance = max(1e-7, source_area * 1e-12)
    expected_volume_tolerance = max(1e-7, abs(float(cert["expected_volume"])) * 1e-9)
    if any(not math.isfinite(float(value)) for value in numbers) or not math.isfinite(source_area) or not math.isfinite(tri_sum) or not math.isfinite(float(cert["source_area_um2"])) or cert["coverage_tolerance"] != expected_coverage_tolerance or cert["volume_tolerance"] != expected_volume_tolerance or cert["coverage_tolerance"] <= 0 or cert["volume_tolerance"] <= 0 or cert["symmetric_difference_area_um2"] < 0 or abs(float(cert["source_area_um2"]) - source_area) > cert["coverage_tolerance"] or area_residual > cert["coverage_tolerance"] or cert["triangle_area_error_um2"] != tri_sum - source_area or abs(cert["triangle_area_error_um2"]) > cert["coverage_tolerance"] or cert["symmetric_difference_area_um2"] > cert["coverage_tolerance"] or cert["minimum_angle_deg"] <= 7.5 or cert["minimum_angle_deg"] > 180.0 or cert["maximum_aspect_ratio"] <= 0 or cert["maximum_aspect_ratio"] > 8.0 or cert["coordinate_tolerance"] <= 0 or cert["parameter_tolerance"] <= 0 or not isinstance(cert["canonical_sha256"], str) or len(cert["canonical_sha256"]) != 64:
        raise Refusal("coverage numeric gate")
    expected = float(shape.area) * float(thickness)
    try:
        origin = tuple(float(x) for x in cert["volume_origin_um"])
    except (TypeError, ValueError) as exc:
        raise Refusal("volume origin malformed") from exc
    expected_origin = (float(shape.bounds[0]), float(shape.bounds[1]), 0.0)
    if cert["signed_volume"] <= 0 or abs(cert["expected_volume"] - expected) > cert["volume_tolerance"] or abs(cert["signed_volume"] - cert["expected_volume"]) > cert["volume_tolerance"] or cert["volume_error"] != cert["signed_volume"] - cert["expected_volume"] or abs(cert["volume_error"]) > cert["volume_tolerance"] or len(origin) != 3 or any(not math.isfinite(x) for x in origin) or origin != expected_origin:
        raise Refusal("volume numeric gate")
    booleans = ("all_faces_triangles", "finite_vertices_and_faces", "nonzero_face_areas", "edge_incidence_all_two", "edge_incidence_all_two_opposite", "top_bottom_opposite", "holes_preserved", "boundary_marker_chains_complete", "geometry_only", "research_only", "non_oracle", "nonproduction")
    if any(cert.get(key) is not True for key in booleans) or cert.get("solver_executed") is not False or cert["boundary_side_outward_proof"] != "constructed right-hand boundary sides" or any(cert.get(key) is not False for key in ("duplicate_2d_vertices", "duplicate_2d_faces", "duplicate_3d_vertices", "duplicate_3d_faces")) or cert["closed_face_estimate"] != cert["face_count"] or cert["closed_vertex_estimate"] != cert["vertex_count"] or cert["vertex_count"] != 2 * cert["two_d_vertex_count"] or cert["two_d_triangle_count"] != 2 * cert["two_d_vertex_count"] + 2 * cert["hole_count"] - 2 - cert["boundary_edge_count"] or cert["vertex_count"] - cert["edge_count"] + cert["face_count"] != cert["euler_characteristic"]:
        raise Refusal("certificate boolean/topology gate")
    if len(result.get("vertices", [])) != cert["vertex_count"] or len(result.get("faces", [])) != cert["face_count"]:
        raise Refusal("certificate count mismatch")
    try:
        lines = canonical.decode("ascii").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise Refusal("canonical encoding") from exc
    old_header = f"{PRODUCT} v{VERSION} D117 W0 geometry-only\n"
    if not canonical.endswith(b"\n") or not lines or lines[0] != old_header or any(not line.endswith("\n") for line in lines) or any(any(ord(ch) < 32 and ch != "\n" or ord(ch) == 127 for ch in line) for line in lines):
        raise Refusal("canonical header/line encoding")
    body = lines[1:]; vlines = body[:cert["vertex_count"]]; flines = body[cert["vertex_count"]:]
    if len(vlines) != cert["vertex_count"] or len(flines) != cert["face_count"] or len(vlines) + len(flines) != len(body) or not vlines or not flines or any(not line.startswith("v ") for line in vlines) or any(not line.startswith("f ") for line in flines):
        raise Refusal("canonical count/prefix mismatch")
    if hashlib.sha256(canonical).hexdigest() != cert.get("canonical_sha256") or len(canonical) != cert.get("canonical_bytes"):
        raise Refusal("prelabel canonical identity mismatch")


def _relabel_canonical(prelabel: bytes) -> tuple[bytes, str, int]:
    old = f"{PRODUCT} v{VERSION} D117 W0 geometry-only\n".encode("ascii")
    new = f"{PRODUCT} v{VERSION} D117 Stage1 full-cell264 geometry-only\n".encode("ascii")
    if not isinstance(prelabel, bytes) or not prelabel.isascii() or b"\r" in prelabel or not prelabel.endswith(b"\n") or any((byte < 32 and byte != 10) or byte == 127 for byte in prelabel) or not prelabel.startswith(old) or prelabel.count(old) != 1:
        raise Refusal("unexpected Stage0 canonical header")
    body = bytes(prelabel[len(old):]).splitlines(keepends=True)
    if not body or any(not line.endswith(b"\n") or not (line.startswith(b"v ") or line.startswith(b"f ")) for line in body):
        raise Refusal("malformed Stage0 canonical body")
    first_face = next((i for i, line in enumerate(body) if line.startswith(b"f ")), None)
    if first_face is None or any(line.startswith(b"v ") for line in body[first_face:]) or any(not line.startswith(b"v ") for line in body[:first_face]):
        raise Refusal("canonical vertex/face ordering")
    canonical = new + prelabel[len(old):]
    return canonical, hashlib.sha256(prelabel).hexdigest(), len(prelabel)


def _write_atomic(out: Path, canonical: bytes, receipt: bytes):
    root = ARTIFACT_ROOT.resolve(strict=True)
    if out.parent != root or out.name not in {"replay-01", "replay-02", "replay-03"}:
        raise Refusal("output confinement/name")
    if out.exists() or _is_reparse(out):
        raise Refusal("output already exists")
    if len(canonical) + len(receipt) > MAX_OUTPUT_BYTES:
        raise Refusal("per-replay output cap exceeded")
    temp = root / (out.name + ".tmp")
    if temp.exists() or _is_reparse(temp):
        raise Refusal("output temporary already exists")
    temp.mkdir()
    (temp / "mesh.canonical.txt").write_bytes(canonical)
    (temp / "mesh_receipt.json").write_bytes(receipt)
    entries = list(temp.iterdir())
    if any(_is_reparse(p) or not p.is_file() for p in entries):
        raise Refusal("atomic output reparse/nonregular entry")
    files = sorted(p.name for p in entries)
    if files != ["mesh.canonical.txt", "mesh_receipt.json"]:
        raise Refusal("atomic output manifest")
    if (temp / "mesh.canonical.txt").read_bytes() != canonical or (temp / "mesh_receipt.json").read_bytes() != receipt or _artifact_bytes() > MAX_ARTIFACT_BYTES:
        raise Refusal("atomic output manifest/cap")
    os.replace(temp, out)


def run(args):
    raw_out = Path(args.output)
    if raw_out.exists() or _is_reparse(raw_out) or any(_is_reparse(parent) for parent in raw_out.parents if parent.exists()):
        raise Refusal("output reparse/existing path")
    out = raw_out.resolve(strict=False)
    _preflight_output(out)
    if not sys.dont_write_bytecode:
        raise Refusal("bytecode disabled requirement")
    cap_contract = _validate_cap_contract()
    d103 = Path(args.d103).resolve(strict=True)
    d104_root = Path(args.d104_root).resolve(strict=True)
    if d103 != D103_PATH.resolve(strict=True) or d104_root != D104_ROOT.resolve(strict=True):
        raise Refusal("canonical D103/D104 path mismatch")
    shape, thickness, inputs = _validate_c0_and_inputs(d103, d104_root)
    impl, machine = platform.python_implementation(), platform.machine().upper()
    if impl != "CPython" or sys.version_info[:2] != (3, 12) or machine != "AMD64" or sys.platform != "win32" or sys.maxsize <= 2**32:
        raise Refusal("runtime compatibility mismatch before Triangle load")
    module = _load_stage0_with_caps()
    sidewall_precheck = _static_sidewall_precheck(module, shape, thickness)
    if Path(args.triangle_site).resolve(strict=True) != Path(module.AUTHORIZED_SITE).resolve(strict=True):
        raise Refusal("Triangle artifact-local path mismatch")
    tri = module.load_triangle_site(Path(args.triangle_site).resolve(strict=True))
    if getattr(tri, "__version__", None) != "20250106":
        raise Refusal("Triangle version mismatch before mesh")
    result = module.mesh_polygon(shape, tri, thickness)
    _validate_mesh_result(result, shape, thickness)
    canonical, prelabel_sha, prelabel_bytes = _relabel_canonical(result["canonical"])
    cert = dict(result["certificate"]); cert.update({"prelabel_canonical_sha256": prelabel_sha, "prelabel_canonical_bytes": prelabel_bytes, "canonical_sha256": hashlib.sha256(canonical).hexdigest(), "canonical_bytes": len(canonical), "stage1_header_relabelled": True})
    runtime = {"implementation": impl, "python": sys.version.split()[0], "platform": sys.platform, "machine": machine, "numpy": importlib.metadata.version("numpy"), "shapely": importlib.metadata.version("shapely"), "triangle": tri.__version__}
    payload = _payload(inputs, cert, runtime, cert["prelabel_canonical_sha256"], cert["prelabel_canonical_bytes"], sidewall_precheck, cap_contract)
    _write_atomic(out, canonical, (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"))
    print(f"{PRODUCT} v{VERSION} PASS_STAGE1_TRIANGLE_CELL264_RESEARCH_PILOT")


def main(argv=None):
    p = argparse.ArgumentParser(description=f"{PRODUCT} v{VERSION} Stage1 full-cell264 geometry-only research pilot")
    p.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    for name in ("--d103", "--d104-root", "--triangle-site", "--output"):
        p.add_argument(name, required=True)
    try:
        run(p.parse_args(argv)); return 0
    except (Refusal, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"{PRODUCT} v{VERSION} STOP_STAGE1: {type(exc).__name__}: {exc}", file=sys.stderr); return 2
    except Exception as exc:
        print(f"{PRODUCT} v{VERSION} STOP_STAGE1: {type(exc).__name__}: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
