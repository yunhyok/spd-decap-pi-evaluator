#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.22.0 AV-BS1 H2-P0 topology fixture.

This is deliberately assembly-only.  It imports the frozen H1 research fixture
for shared P1 kernels but exposes no H2 physics CLI or review-token path.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from typing import Mapping

import numpy as np
from scipy.sparse import csc_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
import av_bs1_boundary_schur as h1


PROGRAM = h1.PROGRAM
CASE_ID = h1.CASE_ID
FIXTURE_SCHEMA = "AV-BS1-h2-p0-manifest-v1"
H1_FIXTURE_SHA256 = "e2a1c8efff67873b57dc7a658b013e3e76d0c921988011f4c8e70cd25f00f8a7"
H1_RUNNER_SHA256 = "dd880de721b9a688ae953c7363dc4a8b482ec1b26f59871d4ca8f83397eb2b7c"
H1_TOMBSTONE_SHA256 = "80ffd8b486dbdd8087eb205f71137663cef0497d8ba8df74253743b302fe6f35"

EXPECTED_MESH = (8065, 23936, 15872, 256,
                 "34eb4f9cadcefd0b20cff3ae6c483dbee4e412ca11d0ff1a8c2c6f49ec7896a9")
EXPECTED_H2_ASSEMBLY: Mapping[str, object] = {
    "candidate_sha256": "dcfe48a3a7084da1486c5110236ba340b5ce46d88142594b9b170b1962352e7c",
    "mapping_sha256": "ed2360d1fccc80fea4b5ea8486454d5dcec2a2d11b5b5a94f443f9b9cb6ea97e",
    "tag_sha256": "287feee5d6fb4299895455b870de0eda484e07617cbc6db43bddd0d11b5b5968",
    "excluded_sha256": "08e40fe370ef2bac1a17a41f06bbe703d35582c6ab1f804e682495375acdbe0e",
    "full_support_sha256": "0da930b2a7cda09619fa64b631dfd469a49bf22dd8507cb2602ce866ea3b055a",
    "canonical_support_sha256": "1c70062f84c4c8d75c64a02b50b5b0e2fa8167efee817350c4e9b460d2f4e756",
    "raw_stiffness_sha256": "ba0589aff655f2f4c39eac20b361446556d5c8ee65f0d0154e984574e961eb2c",
    "canonical_stiffness_sha256": "8fcbbc2bd18c9ba098382cb61c45bf6880cb47886ed2578ee48def0f7a34eef9",
    "mass_sha256": "69aefda6d301e9c7174aae8d1d557439749d288ddd57301291c21c7da5f581fc",
    "trace_mass_sha256": "2de9980d426f88a1422588bae3bbd57cba2e2f7eccc216e2509e7a83e62ca45b",
    "gamma_sha256": "7c2b7c2553066861d448488c00aa5bc83bf451daff059b3841d12cdb829c1b44",
    "interior_sha256": "b55abc01b1d350be6105d6b2fbc5aaf51141570dd2ec3d9164838aaa8a55cf78",
    "boundary_edges_sha256": "44fe10a4bf79cb64617828d9608cb647bdaef8e7212baa4af42beb364339414d",
    "trace_support_sha256": "40252a4b29b83d51f54faf94cb7d9b9c12c4984e613898754b498af9decd1ed6",
    "raw_stiffness_nnz": 55937,
    "canonical_stiffness_nnz": 48513,
    "mass_nnz": 55937,
    "trace_mass_nnz": 768,
    "sparse_base_arrays_bytes": 1328172,
    "resource_preflight_raw_total_bytes": 2043070476,
    "resource_preflight_with_margin_bytes": 2553838095,
    "cancellation_bound": 5.802823100831367e-13,
    "maximum_cancellation_ratio": 3.6286352763558246e-13,
    "cancellation_margin": 1.5991750779260026,
    "cancellation_absolute_slack": 2.1741878244755427e-13,
    "cancellation_relative_slack": 0.374677598592321,
}


AvBsError = h1.AvBsError
canonical_bytes = h1.canonical_bytes


def _file_sha256(path: Path) -> str:
    return h1._file_sha256(path)


def _ccw(nodes: list[tuple[float, float]], tri: tuple[int, int, int]) -> tuple[int, int, int]:
    return h1._ccw(nodes, tri)


def seed_mesh_h2() -> tuple[list[tuple[float, float]], list[tuple[int, int, int]], Mapping[tuple[int, int], int]]:
    """Refine the immutable h mesh once, assigning midpoint IDs by sorted edge."""
    base_nodes, base_triangles = h1.seed_mesh()
    edge_counts, boundary = h1.edge_data(base_triangles)
    ordered_edges = sorted(edge_counts)
    boundary_set = set(boundary)
    nodes = list(base_nodes)
    midpoint: dict[tuple[int, int], int] = {}
    for edge in ordered_edges:
        first, second = edge
        x = 0.5 * (base_nodes[first][0] + base_nodes[second][0])
        y = 0.5 * (base_nodes[first][1] + base_nodes[second][1])
        if edge in boundary_set:
            norm = math.hypot(x, y)
            if norm == 0.0:
                raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "boundary midpoint has zero radius")
            scale = h1.RADIUS_M / norm
            x *= scale
            y *= scale
        midpoint[edge] = len(nodes)
        nodes.append((x, y))
    triangles: list[tuple[int, int, int]] = []
    for a, b, c in base_triangles:
        ab = midpoint[tuple(sorted((a, b)))]
        bc = midpoint[tuple(sorted((b, c)))]
        ca = midpoint[tuple(sorted((c, a)))]
        triangles.extend(
            _ccw(nodes, tri)
            for tri in ((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca))
        )
    return nodes, triangles, midpoint


def _h2_tags(
    nodes: list[tuple[float, float]], midpoint: Mapping[tuple[int, int], int]
) -> tuple[list[tuple[int, int]], Mapping[str, object]]:
    parents = sorted(h1._cyclic_diagonal_edges())
    candidate_pairs: list[tuple[int, int]] = []
    mapping: list[list[object]] = []
    for edge in parents:
        mid = midpoint[edge]
        children = sorted((tuple(sorted((edge[0], mid))), tuple(sorted((mid, edge[1])))))
        candidate_pairs.extend(children)
        mapping.append([list(edge), mid, [list(children[0]), list(children[1])]])
    candidate_pairs = sorted(candidate_pairs)
    boundary_h_nodes = set(range(1921, 2049))
    excluded_pairs = [edge for edge in candidate_pairs if boundary_h_nodes.intersection(edge)]
    selected = [edge for edge in candidate_pairs if edge not in set(excluded_pairs)]
    tags = sorted(selected)
    if (
        len(candidate_pairs) != 3840
        or len(excluded_pairs) != 128
        or len(tags) != 3712
        or len(set(tags)) != len(tags)
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 cyclic tag count mismatch")
    tag_hash = sha256(canonical_bytes([list(x) for x in tags])).hexdigest()
    lineage = {
        "candidate_count": len(candidate_pairs),
        "candidate_sha256": sha256(canonical_bytes([list(x) for x in candidate_pairs])).hexdigest(),
        "mapping_count": len(mapping),
        "mapping_sha256": sha256(canonical_bytes(mapping)).hexdigest(),
        "excluded_count": len(excluded_pairs),
        "excluded_sha256": sha256(canonical_bytes([list(x) for x in excluded_pairs])).hexdigest(),
        "tag_count": len(tags),
        "tag_sha256": tag_hash,
        "selection": "rings1_14_both_children_ring15_inner_only_outer_projected_excluded",
    }
    for key in ("candidate_sha256", "mapping_sha256", "excluded_sha256", "tag_sha256"):
        if lineage[key] != EXPECTED_H2_ASSEMBLY[key]:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"h2 {key} mismatch")
    return tags, lineage


def mesh_manifest(nodes: list[tuple[float, float]], triangles: list[tuple[int, int, int]]) -> Mapping[str, object]:
    edges, boundary = h1.edge_data(triangles)
    boundary_nodes = {node for edge in boundary for node in edge}
    lines = [
        "AV-BS1-CIRCLE|manifest=v1|generator=radial-p1-v1|level=h2"
        f"|a_m={h1.RADIUS_M.hex()}|n_theta={h1.N_THETA}|n_radial={h1.N_RING}"
        "|subdivide=2|diag=alternate_by_radial_band|coordinates=float.hex|triangles=ccw"
    ]
    for index, (x, y) in enumerate(nodes):
        tag = "center" if index == 0 else ("boundary" if index in boundary_nodes else "interior")
        lines.append(f"n|{index}|{x.hex()}|{y.hex()}|{tag}")
    for index, tri in enumerate(triangles):
        lines.append(f"t|{index}|{tri[0]}|{tri[1]}|{tri[2]}")
    for index, edge in enumerate(boundary):
        lines.append(f"b|{index}|{edge[0]}|{edge[1]}")
    xy = np.asarray(nodes, dtype=np.float64)
    areas, conditions = [], []
    for i, j, k in triangles:
        transform = np.column_stack((xy[j] - xy[i], xy[k] - xy[i]))
        areas.append(float(np.linalg.det(transform)) / 2.0)
        conditions.append(float(np.linalg.cond(transform)))
    result = {
        "level": "h2", "nodes": len(nodes), "edges": len(edges), "triangles": len(triangles),
        "boundary_edges": len(boundary), "interior_nodes": len(nodes) - len(boundary_nodes),
        "euler": len(nodes) - len(edges) + len(triangles), "min_area_m2": min(areas),
        "max_area_m2": max(areas), "max_element_kappa2": max(conditions),
        "quality_16u_kappa": 16.0 * h1.UROUND * max(conditions),
        "sha256": sha256("\n".join(lines).encode("utf-8")).hexdigest(),
    }
    observed = (result["nodes"], result["edges"], result["triangles"], result["boundary_edges"], result["sha256"])
    if observed != EXPECTED_MESH or result["euler"] != 1 or result["min_area_m2"] <= 0.0:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"h2 manifest mismatch {observed!r}")
    if result["quality_16u_kappa"] > 2.0e-10:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 quality gate failed")
    return result


def _require_h2_freeze() -> None:
    required = {"tag_sha256", "raw_stiffness_sha256", "canonical_stiffness_sha256", "mass_sha256", "trace_mass_sha256", "gamma_sha256", "interior_sha256"}
    if not required.issubset(EXPECTED_H2_ASSEMBLY):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 assembly certificate is not preregistered")


def _partition(node_count: int, boundary: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray, dict[int, int]]:
    gamma = np.asarray(sorted({node for edge in boundary for node in edge}), dtype=np.int64)
    boundary_set = set(int(value) for value in gamma)
    interior = np.asarray([index for index in range(node_count) if index not in boundary_set], dtype=np.int64)
    if len(gamma) != 256 or len(interior) != 7809:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 partition count mismatch")
    if h1._array_sha256(gamma, dtype="<i8") != EXPECTED_H2_ASSEMBLY["gamma_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 gamma hash mismatch")
    if h1._array_sha256(interior, dtype="<i8") != EXPECTED_H2_ASSEMBLY["interior_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 interior hash mismatch")
    return interior, gamma, {int(value): index for index, value in enumerate(gamma)}


def _canonicalize_h2(
    raw: csc_matrix, nodes: list[tuple[float, float]], triangles: list[tuple[int, int, int]],
    tags: list[tuple[int, int]], manifest: Mapping[str, object],
) -> tuple[csc_matrix, Mapping[str, object]]:
    tag_hash = sha256(canonical_bytes([list(edge) for edge in tags])).hexdigest()
    if tag_hash != EXPECTED_H2_ASSEMBLY["tag_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 tag hash mismatch")
    tag_set, incidence = set(tags), {edge: 0 for edge in tags}
    contributions: dict[tuple[int, int], list[float]] = {}
    full_support: set[tuple[int, int]] = set()
    xy = np.asarray(nodes, dtype=np.float64)
    for tri in triangles:
        points = xy[np.asarray(tri)]
        area = float((points[1, 0] - points[0, 0]) * (points[2, 1] - points[0, 1]) - (points[1, 1] - points[0, 1]) * (points[2, 0] - points[0, 0])) / 2.0
        if area <= 0.0 or not math.isfinite(area):
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 non-positive triangle")
        b = np.asarray((points[1, 1] - points[2, 1], points[2, 1] - points[0, 1], points[0, 1] - points[1, 1]))
        c = np.asarray((points[2, 0] - points[1, 0], points[0, 0] - points[2, 0], points[1, 0] - points[0, 0]))
        local = (np.outer(b, b) + np.outer(c, c)) / (4.0 * area * h1.MU0_H_PER_M)
        for row, global_row in enumerate(tri):
            for column, global_column in enumerate(tri):
                full_support.add((global_row, global_column))
                if row < column:
                    edge = tuple(sorted((global_row, global_column)))
                    contributions.setdefault(edge, []).append(float(local[row, column]))
                    if edge in tag_set:
                        incidence[edge] += 1
    if any(value != 2 for value in incidence.values()):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 tag incidence mismatch")
    full_support_hash = sha256(canonical_bytes([list(edge) for edge in sorted(full_support)])).hexdigest()
    if full_support_hash != EXPECTED_H2_ASSEMBLY["full_support_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 full support hash mismatch")
    ratios = []
    for edge in tags:
        values = contributions[edge]
        scale = sum(abs(value) for value in values)
        if len(values) != 2 or scale <= 0.0:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 tag contribution mismatch")
        ratios.append(abs(sum(values)) / scale)
    bound = 128.0 * h1.UROUND * float(manifest["max_element_kappa2"])
    maximum = max(ratios)
    margin = bound / maximum
    absolute_slack = bound - maximum
    relative_slack = absolute_slack / bound
    untagged_ratios = []
    for edge, values in contributions.items():
        if edge in tag_set or len(values) != 2:
            continue
        scale = sum(abs(value) for value in values)
        if not math.isfinite(scale) or scale <= 0.0:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 untagged contribution mismatch")
        untagged_ratios.append(abs(sum(values)) / scale)
    minimum_untagged = min(untagged_ratios)
    if not math.isfinite(minimum_untagged) or minimum_untagged <= bound:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 untagged edge satisfies zero gate")
    if (
        bound != EXPECTED_H2_ASSEMBLY["cancellation_bound"]
        or maximum != EXPECTED_H2_ASSEMBLY["maximum_cancellation_ratio"]
        or margin != EXPECTED_H2_ASSEMBLY["cancellation_margin"]
        or absolute_slack != EXPECTED_H2_ASSEMBLY["cancellation_absolute_slack"]
        or relative_slack != EXPECTED_H2_ASSEMBLY["cancellation_relative_slack"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 cancellation certificate mismatch")
    raw = csc_matrix(raw, copy=True); raw.sum_duplicates(); raw.sort_indices(); raw.eliminate_zeros()
    if (
        raw.nnz != EXPECTED_H2_ASSEMBLY["raw_stiffness_nnz"]
        or h1._sparse_sha256(raw) != EXPECTED_H2_ASSEMBLY["raw_stiffness_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 raw stiffness hash mismatch")
    canonical = raw.tolil(copy=True)
    tagged_values = []
    for first, second in tags:
        value, reverse = float(raw[first, second]), float(raw[second, first])
        if value != reverse:
            raise AvBsError("BLOCKED_AV_BS_RECIPROCITY", "h2 tagged edge is not symmetric")
        tagged_values.append(value)
        canonical[first, first] = float(canonical[first, first]) + value
        canonical[second, second] = float(canonical[second, second]) + value
        canonical[first, second] = canonical[second, first] = 0.0
    canonical = canonical.tocsc(); canonical.sum_duplicates(); canonical.sort_indices(); canonical.eliminate_zeros()
    if (
        canonical.nnz != EXPECTED_H2_ASSEMBLY["canonical_stiffness_nnz"]
        or h1._sparse_sha256(canonical) != EXPECTED_H2_ASSEMBLY["canonical_stiffness_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 canonical stiffness hash mismatch")
    canonical_support_hash = sha256(canonical_bytes([list(edge) for edge in sorted(zip(*canonical.nonzero()))])).hexdigest()
    if canonical_support_hash != EXPECTED_H2_ASSEMBLY["canonical_support_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 canonical support hash mismatch")
    raw_null = h1._constant_null_relative(raw)
    canonical_null = h1._constant_null_relative(canonical)
    raw_transpose = h1._sparse_transpose_relative(raw)
    canonical_transpose = h1._sparse_transpose_relative(canonical)
    if max(raw_null, canonical_null, raw_transpose, canonical_transpose) > h1.MAX_ASSEMBLY_TRANSPOSE:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 constant null gate failed")
    correction = csc_matrix(canonical - raw)
    correction_relative = h1._sparse_frobenius(correction) / h1._sparse_frobenius(raw)
    return canonical, {"tag_count": len(tags), "tag_sha256": tag_hash, "cancellation_bound": bound,
                       "maximum_cancellation_ratio": maximum,
                       "cancellation_margin": margin,
                       "cancellation_absolute_slack": absolute_slack,
                       "cancellation_relative_slack": relative_slack,
                       "cancellation_formula": "explicit_P1_determinant_expression",
                       "minimum_untagged_two_triangle_ratio": minimum_untagged,
                       "full_support_sha256": full_support_hash,
                       "canonical_support_sha256": canonical_support_hash,
                       "raw_stiffness_nnz": int(raw.nnz),
                       "canonical_stiffness_nnz": int(canonical.nnz),
                       "raw_stiffness_sha256": h1._sparse_sha256(raw),
                       "canonical_stiffness_sha256": h1._sparse_sha256(canonical),
                       "raw_constant_null_relative": raw_null,
                       "canonical_constant_null_relative": canonical_null,
                       "raw_transpose_relative": raw_transpose,
                       "canonical_transpose_relative": canonical_transpose,
                       "nonzero_tagged_binary64_values": sum(value != 0.0 for value in tagged_values),
                       "minimum_tagged_binary64_abs": min(abs(value) for value in tagged_values),
                       "maximum_tagged_binary64_abs": max(abs(value) for value in tagged_values),
                       "correction_relative_frobenius": correction_relative}


def run_manifest() -> Mapping[str, object]:
    if h1._runtime() != h1.EXPECTED_RUNTIME:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "runtime mismatch")
    fixture_path = Path(__file__).with_name("av_bs1_boundary_schur.py")
    runner_path = Path(__file__).with_name("run_av_bs1_stage.ps1")
    tombstone_path = Path(__file__).with_name("av_bs1_primary_h_review_token.json")
    dependencies = {
        "h1_fixture_sha256": _file_sha256(fixture_path),
        "h1_runner_sha256": _file_sha256(runner_path),
        "h1_consumed_tombstone_sha256": _file_sha256(tombstone_path),
    }
    expected_dependencies = {
        "h1_fixture_sha256": H1_FIXTURE_SHA256,
        "h1_runner_sha256": H1_RUNNER_SHA256,
        "h1_consumed_tombstone_sha256": H1_TOMBSTONE_SHA256,
    }
    if dependencies != expected_dependencies:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H1 dependency hash mismatch")
    nodes, triangles, midpoint = seed_mesh_h2()
    manifest = mesh_manifest(nodes, triangles)
    tags, lineage = _h2_tags(nodes, midpoint)
    raw, mass = h1._assemble_volume(nodes, triangles)
    canonical, certificate = _canonicalize_h2(raw, nodes, triangles, tags, manifest)
    _, boundary = h1.edge_data(triangles)
    boundary_hash = sha256(canonical_bytes([list(edge) for edge in boundary])).hexdigest()
    if boundary_hash != EXPECTED_H2_ASSEMBLY["boundary_edges_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 boundary hash mismatch")
    interior, gamma, gamma_map = _partition(len(nodes), boundary)
    trace_mass = h1._assemble_trace_mass(nodes, boundary, gamma_map)
    mass_hash = h1._sparse_sha256(mass)
    trace_mass_hash = h1._sparse_sha256(trace_mass)
    if (
        mass.nnz != EXPECTED_H2_ASSEMBLY["mass_nnz"]
        or mass_hash != EXPECTED_H2_ASSEMBLY["mass_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 mass gate mismatch")
    if (
        trace_mass.nnz != EXPECTED_H2_ASSEMBLY["trace_mass_nnz"]
        or trace_mass_hash != EXPECTED_H2_ASSEMBLY["trace_mass_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 trace mass gate mismatch")
    trace_support_hash = sha256(canonical_bytes([list(edge) for edge in sorted(zip(*trace_mass.nonzero()))])).hexdigest()
    if trace_support_hash != EXPECTED_H2_ASSEMBLY["trace_support_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h2 trace support hash mismatch")
    sparse_bytes = sum(
        int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
        for matrix in (canonical, mass, trace_mass)
    )
    preflight = dict(h1._resource_preflight(len(interior), len(gamma), sparse_bytes))
    preflight["method"] = "h2_p0_dense_factor_upper_plus_sparse_and_rectangular_25pct"
    if (
        sparse_bytes != EXPECTED_H2_ASSEMBLY["sparse_base_arrays_bytes"]
        or preflight["raw_total_bytes"]
        != EXPECTED_H2_ASSEMBLY["resource_preflight_raw_total_bytes"]
        or preflight["total_with_25pct_margin_bytes"]
        != EXPECTED_H2_ASSEMBLY["resource_preflight_with_margin_bytes"]
    ):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "h2 P0 resource preflight mismatch")
    return {
        "schema": FIXTURE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "manifest",
        "status": "preregistered_H2_P0_assembly_only_no_solve",
        "authorization_state": "not_authorized",
        "dependencies": dependencies,
        "mesh": manifest,
        "partition": {
            "interior_nodes": len(interior),
            "boundary_nodes": len(gamma),
            "interior_sha256": EXPECTED_H2_ASSEMBLY["interior_sha256"],
            "gamma_sha256": EXPECTED_H2_ASSEMBLY["gamma_sha256"],
        },
        "cyclic_lineage": lineage,
        "assembly": {
            **certificate,
            "mass_nnz": int(mass.nnz),
            "mass_sha256": mass_hash,
            "trace_mass_nnz": int(trace_mass.nnz),
            "trace_mass_sha256": trace_mass_hash,
            "boundary_edges_sha256": boundary_hash,
            "trace_support_sha256": trace_support_hash,
        },
        "resource_preflight": preflight,
        "factorization_performed": False,
        "physics_solve_performed": False,
        "available_solve_stages": [],
        "unavailable_stages": ["primary-h", "primary-h2", "primary-h4", "withheld", "EQ0"],
    }


def _failure_wrapper(error: AvBsError, stage: str) -> Mapping[str, object]:
    return h1._wrapper(
        {
            "schema": FIXTURE_SCHEMA,
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": stage,
            "status": error.code,
            "authorization_state": "not_authorized",
            "failure_codes": [error.code],
            "detail": error.detail,
            "factorization_performed": False,
            "physics_solve_performed": False,
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AV-BS1 H2-P0 topology fixture")
    parser.add_argument("--stage", required=True, choices=("manifest",))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(json.dumps(h1._wrapper(run_manifest()), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0
    except AvBsError as error:
        print(json.dumps(_failure_wrapper(error, args.stage), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
