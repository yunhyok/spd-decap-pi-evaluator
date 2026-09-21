#!/usr/bin/env python
"""Materialize the approved D116 source-local FasterCap shadow input."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable

from shapely import constrained_delaunay_triangles, wkb
from shapely.geometry import Polygon, MultiPolygon, box


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
EXPECTED_HEAD = "e2f219e71d8c8a397009f72242cce10d78cfc7ab"
EXPECTED_SOURCE = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
EXPECTED_SOURCE_PATH = r"D:\S4LB002-2Para_260729_1_injected.spd"
EXPECTED_D103 = "4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9"
EXPECTED_D104 = "bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b"
EXPECTED_D115B = "c69dce134ca02ff263b75f5df930106a7d8d75d05cccf2acd13b7d9b1919aa49"
EXPECTED_D115C = "0c8daed46719b199ee50b1ec9b94dd5cac0fdedecbc7b58668b7665b5e9a2a80"
WINDOW = (-12000.0, 12000.0, -11000.0, 13000.0)
POSITIVE = {
    259: (779005.9836758123, "8eb2e2b2a48eb74f7b9e59eab6697bcd532beeabc23d7d077bf8807e40be828f", 1089),
    262: (21012.5, "42ba7213b0ebae48073197b095f4ac7fb9ff3c9552fecb4f1f7c9c32dcc106ea", 77),
    264: (926540.7647500002, "f3a2abe59324887bec79aaab38b670f0943cb5cf11936548343a8ef6940cad04", 317),
}
ORDINALS = tuple(range(259, 267))
EMPTY = (260, 261, 263, 265, 266)
PARTITION = {"A": [259, 264], "F": [262]}
CONDUCTORS = (
    ("A_GND_259", 259, "ground", "DGND", "Signal$L29(DGND)", 0.0, 20.0),
    ("A_PWR_264", 264, "power", "ADC_VDD_180_VQPS_SYS_1_AON/0", "Signal$L30(OTHER_POWER1)", 50.0, 70.0),
    ("F_DDRL_262", 262, "floating", "ADC_VDD_105_VAA_DDRL/0", "Signal$L30(OTHER_POWER1)", 50.0, 70.0),
)


def _digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path, expected: str) -> dict[str, Any]:
    if _digest(path) != expected.casefold():
        raise ValueError(f"SHA-256 mismatch for {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def _validate_git(repo: Path) -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo, text=True).strip()
    if head != EXPECTED_HEAD or branch != "main":
        raise ValueError(f"checkout identity mismatch: {branch}@{head}")
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)
    tracked = [line for line in status.splitlines() if line and not line.startswith("??")]
    if tracked:
        raise ValueError("tracked checkout is dirty")


def _validate_d103(d103: dict[str, Any]) -> None:
    if d103.get("program") != PROGRAM or d103.get("version") != VERSION or d103.get("status") != "PASS":
        raise ValueError("D103 identity failed")
    source = d103.get("source", {})
    if source.get("path") != EXPECTED_SOURCE_PATH or source.get("sha256") != EXPECTED_SOURCE or source.get("size_bytes") != 1116717287:
        raise ValueError("D103 source identity failed")
    layers = {row.get("layer_name"): row for row in d103.get("stackup_layers", ()) if isinstance(row, dict)}
    expected = {
        "Medium$DR2829": (1917.0, 1947.0),
        "Medium$DR2930": (1967.0, 1997.0),
        "Medium$DR3031": (2017.0, 2047.0),
        "Signal$L29(DGND)": (1947.0, 1967.0),
        "Signal$L30(OTHER_POWER1)": (1997.0, 2017.0),
    }
    for name, (top, bottom) in expected.items():
        row = layers.get(name)
        depth = row.get("depth_from_stack_top_um", {}) if row else {}
        if not row or row.get("thickness_um") != bottom - top or depth.get("top") != top or depth.get("bottom") != bottom:
            raise ValueError(f"D103 layer identity failed: {name}")
    for name in ("Medium$DR2829", "Medium$DR2930", "Medium$DR3031"):
        points = [row for row in d103.get("dielectric_points", ()) if isinstance(row, dict) and row.get("layer_name") == name and row.get("frequency_hz") == 1_000_000.0]
        if len(points) != 1 or points[0].get("epsilon_r") != 3.4 or points[0].get("loss_tangent") != 0.0041:
            raise ValueError(f"D103 {name} material identity failed")


def _validate_d104(d104: dict[str, Any], root: Path) -> dict[int, dict[str, Any]]:
    if d104.get("schema_version") != "source-local-window-geometry-receipt-v1" or d104.get("program") != PROGRAM or d104.get("version") != VERSION or d104.get("status") != "PASS":
        raise ValueError("D104 identity failed")
    if d104.get("source", {}).get("path") != EXPECTED_SOURCE_PATH or d104.get("source", {}).get("sha256") != EXPECTED_SOURCE or d104.get("source", {}).get("size_bytes") != 1116717287 or d104.get("ordinals") != list(range(258, 274)):
        raise ValueError("D104 source/ordinal identity failed")
    if d104.get("layer_counts") != {"L28": 1, "L29": 1, "L30": 7, "L31": 7}:
        raise ValueError("D104 layer census differs")
    cells = {int(row.get("ordinal")): row for row in d104.get("cells", ()) if isinstance(row, dict)}
    if set(cells) != set(range(258, 274)):
        raise ValueError("D104 cell census differs")
    expected_meta = {
        259: ("Signal$L29(DGND)", "DGND", "spd-surface-island:2db099ba622781734a17c3e0", "cell_0259.wkb", "a438379bf22b47e412b763eebb49c3aae7252c5872d5ac7c31513499108b9053", 2040201),
        260: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRH/0", "spd-surface-island:b0fa08be68439c16ffc58c77", "cell_0260.wkb", "0c7220f4e4e284f0be6857813a9382cb893f30fc25f75d46bad6c0d917b5be9a", 1064485),
        261: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRH/1", "spd-surface-island:b28e75ae9922b1f7756588f4", "cell_0261.wkb", "b1d6025fff224d835e406355c06a7917ddf39d23b520fafc4cbd7a90764fbfa3", 1054101),
        262: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRL/0", "spd-surface-island:8144c5b28d0583106ec2b663", "cell_0262.wkb", "7d490d1ff8c04f352eb181e00c3874845439892a1e3cfba51522c76f3b37defe", 973033),
        263: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRL/1", "spd-surface-island:862ac6d69e684b207043f1e3", "cell_0263.wkb", "162039f4eed00cc5b7c534a559f50ee41b7b458a0d802e1971fc9d1629b74d87", 796561),
        264: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_180_VQPS_SYS_1_AON/0", "spd-surface-island:cb8510a79529b7f6f4f4afd4", "cell_0264.wkb", "a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5", 258181),
        265: ("Signal$L30(OTHER_POWER1)", "ADC_VDD_180_VQPS_SYS_1_AON/1", "spd-surface-island:f1e1df51a7abbeeb63114ac5", "cell_0265.wkb", "89382a6499d5a0d01cbd7561372f6ac347ca31c44fc0afe244a023aec4887bca", 429657),
        266: ("Signal$L30(OTHER_POWER1)", "DGND", "spd-surface-island:e58fb10620c5ba652b665ee3", "cell_0266.wkb", "7aaf6788360eba8929384bb49bbe56633cd67e22883f55c1940fcf53eb85368c", 613),
    }
    for ordinal in ORDINALS:
        row = cells[ordinal]
        geom = row.get("geometry", {})
        source = geom
        meta = expected_meta[ordinal]
        if (row.get("layer"), row.get("net"), row.get("island_id"), source.get("filename"), source.get("wkb_sha256"), source.get("wkb_size_bytes")) != meta:
            raise ValueError(f"D104 row identity failed: {ordinal}")
        path = root / meta[3]
        if not path.is_file() or path.parent != root or path.stat().st_size != meta[5] or _digest(path) != meta[4]:
            raise ValueError(f"D104 WKB identity failed: {ordinal}")
        try:
            loaded = wkb.loads(path.read_bytes())
            bounds = loaded.bounds
            if not loaded.is_valid or len(bounds) != 4 or not all(map(lambda value: abs(float(value)) < float("inf"), bounds)) or abs(loaded.area - float(geom.get("area_um2"))) > 1e-6:
                raise ValueError
        except Exception as exc:
            raise ValueError(f"D104 WKB geometry failed: {ordinal}") from exc
    return cells


def _validate_d115b(d115b: dict[str, Any], d104: dict[int, dict[str, Any]]) -> None:
    if d115b.get("product") != PROGRAM or d115b.get("schema_version") != "d115b-source-plane-ownership-materialization-receipt-v1" or d115b.get("version") != VERSION or d115b.get("status") != "PASS" or d115b.get("disposition") != "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED":
        raise ValueError("D115B identity failed")
    if d115b.get("contract_head") != EXPECTED_HEAD or d115b.get("rail_id") != CONDUCTORS[1][3] or d115b.get("source_path") != EXPECTED_SOURCE_PATH or d115b.get("source", {}).get("path") != EXPECTED_SOURCE_PATH or d115b.get("source", {}).get("sha256") != EXPECTED_SOURCE or d115b.get("source", {}).get("size_bytes") != 1116717287:
        raise ValueError("D115B source/head/rail identity failed")
    rails = d115b.get("candidate", {}).get("rail_rows", [])
    if len(rails) != 2 or {(r.get("role"), r.get("layer"), r.get("net", r.get("logical_net")), r.get("island_id")) for r in rails} != {("power", CONDUCTORS[1][4], CONDUCTORS[1][3], d104[264]["island_id"]), ("ground", CONDUCTORS[0][4], CONDUCTORS[0][3], d104[259]["island_id"])}:
        raise ValueError("D115B rail ownership identity failed")
    terminals = d115b.get("candidate", {}).get("terminal_rows", [])
    candidate = d115b.get("candidate", {})
    cand_path = Path(str(candidate.get("path")))
    inventory = {item.get("path"): item for item in d115b.get("inventory", ()) if isinstance(item, dict)}
    if not candidate.get("present") or candidate.get("rail_count") != 2 or candidate.get("terminal_count") != 6 or not cand_path.is_file() or candidate.get("sha256") != inventory.get(cand_path.name, {}).get("sha256") or candidate.get("size_bytes") != inventory.get(cand_path.name, {}).get("size_bytes") or len(terminals) != 6:
        raise ValueError("D115B terminal cardinality failed")
    roles = {(r.get("role"), r.get("layer"), r.get("island_id")) for r in terminals}
    if roles != {("ground", CONDUCTORS[0][4], d104[259]["island_id"]), ("power", CONDUCTORS[1][4], d104[264]["island_id"])}:
        raise ValueError("D115B terminal ownership failed")


def _validate_d115c(d115c: dict[str, Any], d103_path: Path | None = None, d104_path: Path | None = None, d104_root: Path | None = None, d115b_path: Path | None = None, d115b: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if d115c.get("product") != PROGRAM or d115c.get("version") != VERSION or d115c.get("schema") != "source-local-l29-l30-port-window-receipt-v4" or d115c.get("status") != "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT" or d115c.get("code") != "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT":
        raise ValueError("D115C immutable STOP identity failed")
    git = d115c.get("git", {})
    if git.get("head") != EXPECTED_HEAD or git.get("branch") != "main" or git.get("tracked_clean") is not True or d115c.get("git_head") != EXPECTED_HEAD or d115c.get("git_branch") != "main" or d115c.get("tracked_clean") is not True:
        raise ValueError("D115C git identity failed")
    expected = d115c.get("expected", {})
    if expected.get("head") != EXPECTED_HEAD or expected.get("port_id") != "Port44_SITE0::ADC_VDD_180_VQPS_SYS_1_AON/0" or expected.get("window_pm") != [-12000000000, 12000000000, -11000000000, 13000000000] or expected.get("positive_count") != 3 or expected.get("negative_count") != 10919:
        raise ValueError("D115C Port44 contract failed")
    if d115c.get("empty_ordinals") != list(EMPTY) or d115c.get("coverage_conflict", {}).get("all_eight_evaluated") is not True:
        raise ValueError("D115C eight-row census failed")
    if d115c.get("coverage_conflict", {}).get("required_ordinals") != list(ORDINALS):
        raise ValueError("D115C required ordinal coverage differs")
    w0 = d115c.get("w0", {})
    if (w0.get("gap_pm"), w0.get("gap_um"), w0.get("padding_pm"), w0.get("padding_policy"), w0.get("largest_diameter_pm"), w0.get("raw_bounds_pm"), w0.get("grid_pm"), w0.get("snapped_bounds_pm"), w0.get("footprint_count")) != (30000000, 30, 240000000, "max(8*g,4*largest_pad_diameter)", 60000000, [-11996900000, 12112500000, -11196900000, 12877700000], 1000000000, [-12000000000, 12000000000, -11000000000, 13000000000], 6):
        raise ValueError("D115C W0 governing fields differ")
    scope = d115c.get("scope", {})
    if any(scope.get(key) is not False for key in ("solver_executed", "powersi_executed", "c_res", "generic_four_layer_model")) or scope.get("source_local_shadow_only") is not True:
        raise ValueError("D115C execution scope changed")
    if d115c.get("d103", {}).get("dielectric_point", {}).get("epsilon_r") != 3.4 or d115c.get("d103", {}).get("dielectric_point", {}).get("loss_tangent") != 0.0041:
        raise ValueError("D115C material provenance changed")
    if d103_path is not None:
        inputs = d115c.get("inputs", {})
        expected_inputs = {"d103": (d103_path, EXPECTED_D103, 204735), "d104": (d104_path, EXPECTED_D104, 19728), "d115b": (d115b_path, EXPECTED_D115B, 514208)}
        for key, (path, digest, size) in expected_inputs.items():
            row = inputs.get(key, {})
            if path is None or row.get("path") != str(path) or row.get("sha256") != digest or row.get("size_bytes") != size:
                raise ValueError(f"D115C linked input failed: {key}")
        if inputs.get("source", {}).get("path") != EXPECTED_SOURCE_PATH or inputs.get("source", {}).get("sha256") != EXPECTED_SOURCE or inputs.get("source", {}).get("size_bytes") != 1116717287:
            raise ValueError("D115C linked source failed")
        if d104_root is None or inputs.get("d104_root", {}).get("path") != str(d104_root):
            raise ValueError("D115C linked D104 root failed")
        if d115b is not None and inputs.get("candidate", {}).get("sha256") != d115b.get("candidate", {}).get("sha256"):
            raise ValueError("D115C linked candidate failed")
        if d115b is not None:
            candidate = d115b.get("candidate", {})
            linked = inputs.get("candidate", {})
            if linked.get("path") != candidate.get("path") or linked.get("size_bytes") != candidate.get("size_bytes"):
                raise ValueError("D115C linked candidate identity failed")
            provenance = d115c.get("d115b", {})
            if provenance.get("status") != "PASS" or provenance.get("disposition") != "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED" or provenance.get("contract_head") != EXPECTED_HEAD:
                raise ValueError("D115C D115B provenance failed")
    rows = d115c.get("d104_cells")
    if not isinstance(rows, list) or [row.get("ordinal") for row in rows if isinstance(row, dict)] != list(ORDINALS):
        raise ValueError("D115C exact eight-row table missing")
    for row in rows:
        ordinal = row.get("ordinal")
        expected_row = POSITIVE.get(ordinal)
        if expected_row:
            expected_bounds = [-11205.0, 12795.0, -11000.0, 13000.0] if ordinal == 262 else list(WINDOW)
            if row.get("intersection_wkb_sha256") != expected_row[1] or row.get("intersection_wkb_size_bytes") != expected_row[2] or abs(row.get("intersection_area_um2", -1) - expected_row[0]) > 1e-6 or row.get("intersection_bbox_um") != expected_bounds or row.get("boundary_contact") is not True:
                raise ValueError(f"D115C positive row mismatch: {ordinal}")
        elif row.get("intersection_wkb_sha256") is not None or row.get("intersection_wkb_size_bytes") is not None or row.get("intersection_area_um2") != 0.0 or row.get("intersection_bbox_um") is not None or row.get("boundary_contact") is not False:
            raise ValueError(f"D115C empty row mismatch: {ordinal}")
    return rows


def _components(shape: Polygon | MultiPolygon) -> list[Polygon]:
    if isinstance(shape, Polygon):
        return [shape]
    if isinstance(shape, MultiPolygon):
        return sorted(shape.geoms, key=lambda p: (p.bounds, p.wkb))
    raise ValueError(f"unsupported intersection geometry: {shape.geom_type}")


def _tri_key(coords: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    points = tuple(coords)
    rotations = [points[i:] + points[:i] for i in range(3)]
    return min(rotations)


def _signed_area(points: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))


def extrude_faces(shape: Polygon | MultiPolygon, z0_um: float, z1_um: float) -> list[tuple[str, tuple[tuple[float, float, float], ...]]]:
    """Return deterministic oriented T/Q panels in metre coordinates."""
    if z1_um <= z0_um:
        raise ValueError("extrusion z interval must be positive")
    faces: list[tuple[str, tuple[tuple[float, float, float], ...]]] = []
    top_edges: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for polygon in _components(shape):
        triangles = constrained_delaunay_triangles(polygon)
        tri_geoms = sorted((g for g in triangles.geoms if isinstance(g, Polygon)), key=lambda g: _tri_key(tuple(g.exterior.coords)[:-1]))
        if abs(sum(tri.area for tri in tri_geoms) - polygon.area) > max(1e-9, abs(polygon.area) * 1e-12):
            raise ValueError("constrained triangulation does not cover polygon")
        for tri in tri_geoms:
            xy = tuple((float(x), float(y)) for x, y in list(tri.exterior.coords)[:3])
            if abs(_signed_area(xy)) <= 0:
                raise ValueError("zero-area triangulation panel")
            if _signed_area(xy) < 0:
                xy = (xy[0], xy[2], xy[1])
            top_edges.update(zip(xy, xy[1:] + xy[:1]))
            top = tuple((x * 1e-6, y * 1e-6, z1_um * 1e-6) for x, y in xy)
            bottom = tuple((x * 1e-6, y * 1e-6, z0_um * 1e-6) for x, y in reversed(xy))
            faces.extend((("T", top), ("T", bottom)))
        for ring in (polygon.exterior, *polygon.interiors):
            xy = tuple((float(x), float(y)) for x, y in list(ring.coords)[:-1])
            if len(xy) < 3:
                raise ValueError("degenerate boundary ring")
            for a, b in zip(xy, xy[1:] + xy[:1]):
                if (a, b) in top_edges:
                    u, v = b, a
                elif (b, a) in top_edges:
                    u, v = a, b
                else:
                    raise ValueError("boundary ring is not represented by constrained triangulation")
                faces.append(("Q", ((v[0] * 1e-6, v[1] * 1e-6, z0_um * 1e-6), (u[0] * 1e-6, u[1] * 1e-6, z0_um * 1e-6), (u[0] * 1e-6, u[1] * 1e-6, z1_um * 1e-6), (v[0] * 1e-6, v[1] * 1e-6, z1_um * 1e-6))))
    _validate_faces(faces)
    return faces


def _validate_faces(faces: list[tuple[str, tuple[tuple[float, float, float], ...]]]) -> None:
    def canonical(vertices: tuple[tuple[float, float, float], ...]) -> tuple[tuple[float, float, float], ...]:
        rotations = [vertices[i:] + vertices[:i] for i in range(len(vertices))]
        reverse = tuple(reversed(vertices))
        rotations.extend(reverse[i:] + reverse[:i] for i in range(len(vertices)))
        return min(rotations)
    if len({(kind, canonical(vertices)) for kind, vertices in faces}) != len(faces):
        raise ValueError("duplicate panel")
    edges: dict[tuple[tuple[float, float, float], tuple[float, float, float]], list[tuple[tuple[float, float, float], tuple[float, float, float]]]] = {}
    for kind, vertices in faces:
        if len(vertices) not in (3, 4):
            raise ValueError("invalid panel vertex count")
        a, b, c = vertices[:3]
        cross = tuple((b[i] - a[i]) * (c[(i + 1) % 3] - a[(i + 1) % 3]) - (b[(i + 1) % 3] - a[(i + 1) % 3]) * (c[i] - a[i]) for i in range(3))
        if not any(cross):
            raise ValueError("zero-area panel")
        if len(vertices) == 4:
            d = vertices[3]
            cross2 = tuple((c[i] - a[i]) * (d[(i + 1) % 3] - a[(i + 1) % 3]) - (c[(i + 1) % 3] - a[(i + 1) % 3]) * (d[i] - a[i]) for i in range(3))
            if not any(cross2):
                raise ValueError("zero-area panel")
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            if a == b:
                raise ValueError("zero-length panel edge")
            key = tuple(sorted((a, b)))
            edges.setdefault(key, []).append((a, b))
    if any(len(v) != 2 or v[0] == v[1] or v[0] != (v[1][1], v[1][0]) for v in edges.values()):
        raise ValueError("surface is not a closed oriented manifold")


def _panel_line(name: str, kind: str, vertices: tuple[tuple[float, float, float], ...]) -> str:
    return kind + " " + name + " " + " ".join(format(value, ".17g") for point in vertices for value in point)


def _materialize(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    repo = Path(__file__).resolve().parents[2]
    _validate_git(repo)
    d103_path, d104_path, d115b_path, d115c_path = map(lambda p: Path(p).resolve(), (args.d103, args.d104, args.d115b, args.d115c))
    d104_root = Path(args.d104_root).resolve()
    if d104_path.parent != d104_root:
        raise ValueError("D104 root must equal receipt parent")
    d103 = _load(d103_path, EXPECTED_D103); _validate_d103(d103)
    d104 = _load(d104_path, EXPECTED_D104); cells = _validate_d104(d104, d104_root)
    d115b = _load(d115b_path, EXPECTED_D115B); _validate_d115b(d115b, cells)
    d115c = _load(d115c_path, EXPECTED_D115C); d115c_rows = _validate_d115c(d115c, d103_path, d104_path, d104_root, d115b_path, d115b)
    window = box(*WINDOW)
    geometries: dict[int, Any] = {}
    for ordinal in ORDINALS:
        source_file = d104_root / cells[ordinal]["geometry"]["filename"]
        geometry = wkb.loads(source_file.read_bytes()).intersection(window)
        expected = POSITIVE.get(ordinal)
        if expected:
            if abs(geometry.area - expected[0]) > 1e-6 or geometry.is_empty or geometry.geom_type not in ("Polygon", "MultiPolygon") or geometry.wkb_hex is None or _digest_bytes(bytes(geometry.wkb)) != expected[1] or len(bytes(geometry.wkb)) != expected[2]:
                raise ValueError(f"approved intersection mismatch: {ordinal}")
        elif not geometry.is_empty and geometry.area != 0:
            raise ValueError(f"unexpected positive intersection: {ordinal}")
        row = next(item for item in d115c_rows if item["ordinal"] == ordinal)
        if (row.get("layer"), row.get("net"), row.get("island_id")) != (cells[ordinal].get("layer"), cells[ordinal].get("net"), cells[ordinal].get("island_id")):
            raise ValueError(f"D115C/D104 row identity mismatch: {ordinal}")
        got_hash = None if geometry.is_empty else _digest_bytes(bytes(geometry.wkb))
        got_size = None if geometry.is_empty else len(bytes(geometry.wkb))
        got_bounds = None if geometry.is_empty else list(geometry.bounds)
        source_shape = wkb.loads(source_file.read_bytes())
        boundary_contact = bool(source_shape.boundary.intersects(window.boundary))
        if got_hash != row.get("intersection_wkb_sha256") or got_size != row.get("intersection_wkb_size_bytes") or (0.0 if geometry.is_empty else geometry.area) != row.get("intersection_area_um2") or got_bounds != row.get("intersection_bbox_um") or boundary_contact != bool(row.get("boundary_contact")):
            raise ValueError(f"D115C independent intersection mismatch: {ordinal}")
        geometries[ordinal] = geometry
    payloads: dict[str, bytes] = {}; panels: dict[int, list[tuple[str, tuple[tuple[float, float, float], ...]]]] = {}
    pairwise_clearance = _pairwise_clearance(geometries)
    for name, ordinal, role, net, layer, z0, z1 in CONDUCTORS:
        panels[ordinal] = extrude_faces(geometries[ordinal], z0, z1)
        payloads[f"{name}.qui"] = ("* SPD Decap PI Evaluator v0.23.1 D116-SHADOW raw-W0 L29/L30 gap coupon\n" + "\n".join(_panel_line(name, kind, vertices) for kind, vertices in panels[ordinal]) + "\n").encode()
    list_lines = ["* SPD Decap PI Evaluator v0.23.1 D116-SHADOW raw-W0 L29/L30 gap coupon", *(f"C {name}.qui 3.4 0 0 0" for name, *_ in CONDUCTORS)]
    payloads["d116_shadow_gap_coupon.lst"] = ("\n".join(list_lines) + "\n").encode()
    receipt = {"schema_version": "d116-shadow-gap-coupon-v1", "program": PROGRAM, "version": VERSION, "status": "PASS_D116_SHADOW_GAP_COUPON", "contract": "D116-SHADOW raw-W0 L29/L30 gap coupon; not full-domain production oracle", "scope": {"source_local_shadow_only": True, "full_domain": False, "full_domain_layers": ["L28", "L29", "L30", "L31"], "included_layers": ["L29", "L30"], "omitted_layers": ["L28", "L31"], "included_ordinals": [259, 262, 264], "full_d104_domain_coverage": False, "production_oracle": False, "solver_executed": False, "powersi_executed": False, "source_derived": True, "d104_containment_diagnostic_only": True, "powersi_zii_readiness": False, "powersi_comparable_zii": False, "artificial_w0_boundary_side_closures": True}, "inputs": {"d103": {"path": str(d103_path), "sha256": EXPECTED_D103}, "d104": {"path": str(d104_path), "sha256": EXPECTED_D104, "root": str(d104_root)}, "d115b": {"path": str(d115b_path), "sha256": EXPECTED_D115B}, "d115c": {"path": str(d115c_path), "sha256": EXPECTED_D115C, "status": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT"}, "source": {"sha256": EXPECTED_SOURCE}}, "w0_um": list(WINDOW), "partition": PARTITION, "conductor_order": [name for name, *_ in CONDUCTORS], "expected_solver_labels": ["g1_A_GND_259", "g2_A_PWR_264", "g3_F_DDRL_262"], "material": {"frequency_hz": 1_000_000.0, "epsilon_r": 3.4, "loss_tangent": 0.0041, "description": "homogeneous 1 MHz material snapshot only", "loss_tangent_modeled": False, "dielectric_loss_or_conductance_claim": False}, "pairwise_clearance": pairwise_clearance, "conductors": []}
    for name, ordinal, role, net, layer, z0, z1 in CONDUCTORS:
        geom = geometries[ordinal]; payload = payloads[f"{name}.qui"]
        source_meta = cells[ordinal]["geometry"]
        receipt["conductors"].append({"name": name, "ordinal": ordinal, "role": role, "net": net, "layer": layer, "island_id": cells[ordinal]["island_id"], "source_wkb": {"filename": source_meta["filename"], "sha256": source_meta["wkb_sha256"], "size_bytes": source_meta["wkb_size_bytes"], "bounds_um": source_meta.get("bounds_um", source_meta["bbox_um"]), "area_um2": source_meta["area_um2"]}, "intersection": {"area_um2": geom.area, "bounds_um": list(geom.bounds), "wkb_sha256": _digest_bytes(bytes(geom.wkb)), "wkb_size_bytes": len(bytes(geom.wkb)), "component_count": len(_components(geom)), "hole_count": sum(len(part.interiors) for part in _components(geom)), "boundary_contact": bool(wkb.loads((d104_root / source_meta["filename"]).read_bytes()).boundary.intersects(window.boundary))}, "z_um": [z0, z1], "panel_count": len(panels[ordinal]), "triangle_count": sum(kind == "T" for kind, _ in panels[ordinal]), "quad_count": sum(kind == "Q" for kind, _ in panels[ordinal]), "file": f"{name}.qui", "file_sha256": _digest_bytes(payload)})
    receipt["list_file"] = {"file": "d116_shadow_gap_coupon.lst", "sha256": _digest_bytes(payloads["d116_shadow_gap_coupon.lst"])}
    receipt_bytes = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for filename, payload in payloads.items():
            _write_fsync(temporary / filename, payload)
        _write_fsync(temporary / "d116_shadow_gap_coupon_receipt.json", receipt_bytes)
        for filename, payload in (*payloads.items(), ("d116_shadow_gap_coupon_receipt.json", receipt_bytes)):
            if (temporary / filename).read_bytes() != payload:
                raise ValueError(f"reread verification failed: {filename}")
        os.replace(temporary, output); temporary = None
    finally:
        if temporary is not None: shutil.rmtree(temporary, ignore_errors=True)
    return receipt


def _digest_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _pairwise_clearance(geometries: dict[int, Any]) -> list[dict[str, Any]]:
    intervals = {ordinal: (z0, z1) for _, ordinal, _, _, _, z0, z1 in CONDUCTORS}
    names = {ordinal: name for name, ordinal, *_ in CONDUCTORS}
    evidence: list[dict[str, Any]] = []
    for left, right in ((259, 262), (259, 264), (262, 264)):
        xy = float(geometries[left].distance(geometries[right]))
        z0, z1 = intervals[left]; w0, w1 = intervals[right]
        z_clearance = max(0.0, max(z0, w0) - min(z1, w1))
        if xy <= 0.0 and z_clearance <= 0.0:
            raise ValueError(f"retained conductor volumes touch or overlap: {left}/{right}")
        evidence.append({"left": names[left], "right": names[right], "ordinals": [left, right], "xy_clearance_um": xy, "z_clearance_um": z_clearance, "xy_disjoint": xy > 0.0, "volumes_separated": xy > 0.0 or z_clearance > 0.0})
    return evidence


def _write_fsync(path: Path, payload: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} - approved D116 source-local FasterCap shadow input")
    parser.add_argument("--d103", required=True); parser.add_argument("--d104-root", required=True); parser.add_argument("--d104", required=True); parser.add_argument("--d115b", required=True); parser.add_argument("--d115c", required=True); parser.add_argument("--output", required=True, help="new absent output directory")
    args = parser.parse_args(argv); receipt = _materialize(args); print(json.dumps({"program": PROGRAM, "version": VERSION, "status": receipt["status"], "output": args.output})); return 0


if __name__ == "__main__":
    raise SystemExit(main())
