#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.22.0 AV-BS1 H4-P0 assembly fixture.

This fixture deterministically refines the frozen H2 mesh and validates only
topology, sparse P1 assembly, the inherited cyclic-edge correction, and two
resource envelopes.  It exposes no factorization, physics, or token path.
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
import av_bs1_boundary_schur_h2 as h2


PROGRAM = h1.PROGRAM
CASE_ID = h1.CASE_ID
FIXTURE_SCHEMA = "AV-BS1-h4-p0-manifest-v1"
H2_FIXTURE_SHA256 = "032100623fca51ab22a48493b46f23bc8ce5fd1250203d527062671d47599384"
H2_RUNNER_SHA256 = "6ce0002e9d3790542008d9e1e608168f1e40f51d56c9a8ff1d37003a15f8feb7"
H2_PAYLOAD_SHA256 = "68d2e20a471e0e9475dd8e575ffd1e246f4098bb6c0e2b688d0f6f702fb511ba"

EXPECTED_MESH = (
    32001,
    95488,
    63488,
    512,
    "a91b4bf147628a34d1a29144ae353a83110b1756c699c71e2b57e9c36822835b",
)
EXPECTED_H4_ASSEMBLY: Mapping[str, object] = {
    "candidate_sha256": "3902a43ddd16f2cfce16b2892b908f45d02f4464c5157a5c42b5b3ac5cb9d98d",
    "mapping_sha256": "561059787bc8b48a9e9c5963c69a3cceed2278779a22bf8ac37f619cee977b97",
    "tag_sha256": "3902a43ddd16f2cfce16b2892b908f45d02f4464c5157a5c42b5b3ac5cb9d98d",
    "excluded_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "full_support_sha256": "bae1059a74cd662c6bac6e027beb8b54f1df865693939735dc1247a161c23240",
    "canonical_support_sha256": "4416afcc176bce3cda2a18e3f73a53c2b08c21d270cc3cff1c78293a7450fa52",
    "raw_stiffness_sha256": "8a020c809634a9794292f49198ff1bede84328b6e2c5ef988255cbff32cfe93b",
    "canonical_stiffness_sha256": "a510df2ab39cb85640720f863341d1fe468562442ecb074bafcaf70d9846f2e7",
    "mass_sha256": "66bc7da7edfae88a53220ce553c4b8ab0cea51b5222d354139d377c4d34c05ac",
    "trace_mass_sha256": "cac37897c0c7910ae64ba15c3307ad2ba7bf109f7832a0b692ed02bb5ff8771d",
    "gamma_sha256": "749dbbb6b7f58565c757a1042dd9344f165c35afb23e77022222e21ba260c0b9",
    "interior_sha256": "b91ec9e63cc180c30daf3b61c6789b221ad9429ae9cc6bfc6d0b1d51e07e205c",
    "boundary_edges_sha256": "2453b9035480c5c13925b8e156d42fd7c2769f1b7e7b71f8aa1c6ae7ec1f66e7",
    "trace_support_sha256": "db4112208523ce834f03fc9b10f27595fd17f3f7ce3b06c831ce30f5bc6a3b59",
    "raw_stiffness_nnz": 222977,
    "canonical_stiffness_nnz": 208129,
    "mass_nnz": 222977,
    "trace_mass_nnz": 1536,
    "canonical_block_nnz": {"II": 204545, "IG": 1024, "GI": 1024, "GG": 1536},
    "mass_block_nnz": {"II": 219393, "IG": 1024, "GI": 1024, "GG": 1536},
    "cancellation_bound": 1.1605646201662821e-12,
    "inherited_128u_kappa_bound": 5.802823100831411e-13,
    "inherited_128u_kappa_exceeding_tag_count": 16,
    "maximum_cancellation_ratio": 8.540375354048666e-13,
    "cancellation_margin": 1.358915237391882,
    "cancellation_absolute_slack": 3.0652708476141557e-13,
    "cancellation_relative_slack": 0.26411892921352137,
    "minimum_untagged_two_triangle_ratio": 0.19705186275527375,
    "raw_constant_null_relative": 2.2860092193173166e-16,
    "canonical_constant_null_relative": 2.2860092193173166e-16,
    "raw_transpose_relative": 0.0,
    "canonical_transpose_relative": 0.0,
    "correction_relative_frobenius": 2.556936522664663e-16,
    "minimum_tagged_binary64_abs": 1.8189894035458565e-12,
    "maximum_tagged_binary64_abs": 1.6683770809322596e-08,
    "sparse_base_arrays_bytes": 5449772,
}

AvBsError = h1.AvBsError
canonical_bytes = h1.canonical_bytes


def seed_mesh_h4() -> tuple[
    list[tuple[float, float]],
    list[tuple[int, int, int]],
    Mapping[tuple[int, int], int],
]:
    """Refine frozen H2 once, using sorted H2-edge midpoint IDs."""
    base_nodes, base_triangles, _ = h2.seed_mesh_h2()
    edge_counts, boundary = h1.edge_data(base_triangles)
    boundary_set = set(boundary)
    nodes = list(base_nodes)
    midpoint: dict[tuple[int, int], int] = {}
    for edge in sorted(edge_counts):
        first, second = edge
        x = 0.5 * (base_nodes[first][0] + base_nodes[second][0])
        y = 0.5 * (base_nodes[first][1] + base_nodes[second][1])
        if edge in boundary_set:
            norm = math.hypot(x, y)
            if norm == 0.0:
                raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 boundary midpoint has zero radius")
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
            h1._ccw(nodes, triangle)
            for triangle in ((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca))
        )
    return nodes, triangles, midpoint


def mesh_manifest(
    nodes: list[tuple[float, float]], triangles: list[tuple[int, int, int]]
) -> tuple[Mapping[str, object], list[tuple[int, int]]]:
    edges, boundary = h1.edge_data(triangles)
    boundary_nodes = {node for edge in boundary for node in edge}
    lines = [
        "AV-BS1-CIRCLE|manifest=v1|generator=radial-p1-v1|level=h4"
        f"|a_m={h1.RADIUS_M.hex()}|n_theta={h1.N_THETA}|n_radial={h1.N_RING}"
        "|subdivide=4|diag=alternate_by_radial_band|coordinates=float.hex|triangles=ccw"
    ]
    for index, (x, y) in enumerate(nodes):
        role = "center" if index == 0 else ("boundary" if index in boundary_nodes else "interior")
        lines.append(f"n|{index}|{x.hex()}|{y.hex()}|{role}")
    for index, triangle in enumerate(triangles):
        lines.append(f"t|{index}|{triangle[0]}|{triangle[1]}|{triangle[2]}")
    for index, edge in enumerate(boundary):
        lines.append(f"b|{index}|{edge[0]}|{edge[1]}")
    xy = np.asarray(nodes, dtype=np.float64)
    areas: list[float] = []
    conditions: list[float] = []
    for first, second, third in triangles:
        transform = np.column_stack((xy[second] - xy[first], xy[third] - xy[first]))
        areas.append(abs(float(np.linalg.det(transform))) / 2.0)
        conditions.append(float(np.linalg.cond(transform)))
    result = {
        "level": "h4",
        "nodes": len(nodes),
        "edges": len(edges),
        "triangles": len(triangles),
        "boundary_edges": len(boundary),
        "interior_nodes": len(nodes) - len(boundary_nodes),
        "euler": len(nodes) - len(edges) + len(triangles),
        "min_area_m2": min(areas),
        "max_area_m2": max(areas),
        "max_element_kappa2": max(conditions),
        "quality_16u_kappa": 16.0 * h1.UROUND * max(conditions),
        "sha256": sha256("\n".join(lines).encode("utf-8")).hexdigest(),
    }
    observed = (
        result["nodes"],
        result["edges"],
        result["triangles"],
        result["boundary_edges"],
        result["sha256"],
    )
    if observed != EXPECTED_MESH or result["euler"] != 1 or result["min_area_m2"] <= 0.0:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"H4 manifest mismatch {observed!r}")
    if result["quality_16u_kappa"] > 2.0e-10:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 quality gate failed")
    return result, boundary


def h4_tags(
    midpoint: Mapping[tuple[int, int], int]
) -> tuple[list[tuple[int, int]], Mapping[str, object]]:
    h2_nodes, h2_triangles, h2_midpoint = h2.seed_mesh_h2()
    parents = sorted(h2._h2_tags(h2_nodes, h2_midpoint)[0])
    _, h2_boundary = h1.edge_data(h2_triangles)
    h2_boundary_nodes = {node for edge in h2_boundary for node in edge}
    candidates: list[tuple[int, int]] = []
    mapping: list[list[object]] = []
    parent_boundary_touch_count = 0
    for edge in parents:
        if h2_boundary_nodes.intersection(edge):
            parent_boundary_touch_count += 1
        middle = midpoint[edge]
        children = sorted(
            (tuple(sorted((edge[0], middle))), tuple(sorted((middle, edge[1]))))
        )
        candidates.extend(children)
        mapping.append([list(edge), middle, [list(children[0]), list(children[1])]])
    candidates = sorted(candidates)
    tags = list(candidates)
    lineage = {
        "parent_count": len(parents),
        "parent_boundary_touch_count": parent_boundary_touch_count,
        "candidate_count": len(candidates),
        "candidate_sha256": sha256(canonical_bytes([list(edge) for edge in candidates])).hexdigest(),
        "mapping_count": len(mapping),
        "mapping_sha256": sha256(canonical_bytes(mapping)).hexdigest(),
        "excluded_count": 0,
        "excluded_sha256": sha256(canonical_bytes([])).hexdigest(),
        "tag_count": len(tags),
        "tag_sha256": sha256(canonical_bytes([list(edge) for edge in tags])).hexdigest(),
        "selection": "all_children_of_frozen_h2_canonical_tags_topology_only",
    }
    if (
        len(parents) != 3712
        or parent_boundary_touch_count != 0
        or len(candidates) != 7424
        or len(tags) != 7424
        or len(set(tags)) != len(tags)
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 cyclic lineage count mismatch")
    for key in ("candidate_sha256", "mapping_sha256", "excluded_sha256", "tag_sha256"):
        if lineage[key] != EXPECTED_H4_ASSEMBLY[key]:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"H4 {key} mismatch")
    return tags, lineage


def partition(
    node_count: int, boundary: list[tuple[int, int]]
) -> tuple[np.ndarray, np.ndarray, dict[int, int]]:
    gamma = np.asarray(sorted({node for edge in boundary for node in edge}), dtype=np.int64)
    boundary_set = set(int(value) for value in gamma)
    interior = np.asarray(
        [index for index in range(node_count) if index not in boundary_set], dtype=np.int64
    )
    if len(gamma) != 512 or len(interior) != 31489:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 partition count mismatch")
    if h1._array_sha256(gamma, dtype="<i8") != EXPECTED_H4_ASSEMBLY["gamma_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 gamma hash mismatch")
    if h1._array_sha256(interior, dtype="<i8") != EXPECTED_H4_ASSEMBLY["interior_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 interior hash mismatch")
    return interior, gamma, {int(value): index for index, value in enumerate(gamma)}


def canonicalize_h4(
    raw: csc_matrix,
    nodes: list[tuple[float, float]],
    triangles: list[tuple[int, int, int]],
    tags: list[tuple[int, int]],
    manifest: Mapping[str, object],
) -> tuple[csc_matrix, Mapping[str, object]]:
    tag_set = set(tags)
    incidence = {edge: 0 for edge in tags}
    contributions: dict[tuple[int, int], list[float]] = {}
    full_support: set[tuple[int, int]] = set()
    xy = np.asarray(nodes, dtype=np.float64)
    for triangle in triangles:
        points = xy[np.asarray(triangle)]
        area = (
            (points[1, 0] - points[0, 0]) * (points[2, 1] - points[0, 1])
            - (points[1, 1] - points[0, 1]) * (points[2, 0] - points[0, 0])
        ) / 2.0
        if area <= 0.0 or not math.isfinite(area):
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 non-positive triangle")
        b = np.asarray(
            (points[1, 1] - points[2, 1], points[2, 1] - points[0, 1], points[0, 1] - points[1, 1])
        )
        c = np.asarray(
            (points[2, 0] - points[1, 0], points[0, 0] - points[2, 0], points[1, 0] - points[0, 0])
        )
        local = (np.outer(b, b) + np.outer(c, c)) / (4.0 * area * h1.MU0_H_PER_M)
        for row, global_row in enumerate(triangle):
            for column, global_column in enumerate(triangle):
                full_support.add((global_row, global_column))
                if row < column:
                    edge = tuple(sorted((global_row, global_column)))
                    contributions.setdefault(edge, []).append(float(local[row, column]))
                    if edge in tag_set:
                        incidence[edge] += 1
    if any(value != 2 for value in incidence.values()):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 tag incidence mismatch")
    full_support_hash = sha256(
        canonical_bytes([list(edge) for edge in sorted(full_support)])
    ).hexdigest()
    if full_support_hash != EXPECTED_H4_ASSEMBLY["full_support_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 full support hash mismatch")
    ratios: list[float] = []
    for edge in tags:
        values = contributions[edge]
        scale = sum(abs(value) for value in values)
        if len(values) != 2 or scale <= 0.0:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 tag contribution mismatch")
        ratios.append(abs(sum(values)) / scale)
    bound = 256.0 * h1.UROUND * float(manifest["max_element_kappa2"])
    inherited_bound = 128.0 * h1.UROUND * float(manifest["max_element_kappa2"])
    maximum = max(ratios)
    absolute_slack = bound - maximum
    margin = bound / maximum
    relative_slack = absolute_slack / bound
    untagged_ratios: list[float] = []
    for edge, values in contributions.items():
        if edge in tag_set or len(values) != 2:
            continue
        scale = sum(abs(value) for value in values)
        if not math.isfinite(scale) or scale <= 0.0:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 untagged contribution mismatch")
        untagged_ratios.append(abs(sum(values)) / scale)
    minimum_untagged = min(untagged_ratios)
    exact_certificate = {
        "cancellation_bound": bound,
        "inherited_128u_kappa_bound": inherited_bound,
        "inherited_128u_kappa_exceeding_tag_count": sum(
            ratio > inherited_bound for ratio in ratios
        ),
        "maximum_cancellation_ratio": maximum,
        "cancellation_margin": margin,
        "cancellation_absolute_slack": absolute_slack,
        "cancellation_relative_slack": relative_slack,
        "minimum_untagged_two_triangle_ratio": minimum_untagged,
    }
    for key, value in exact_certificate.items():
        if value != EXPECTED_H4_ASSEMBLY[key]:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"H4 {key} mismatch")
    if maximum > bound or minimum_untagged <= bound:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 cancellation separation gate failed")
    raw = csc_matrix(raw, copy=True)
    raw.sum_duplicates()
    raw.sort_indices()
    raw.eliminate_zeros()
    if (
        raw.nnz != EXPECTED_H4_ASSEMBLY["raw_stiffness_nnz"]
        or h1._sparse_sha256(raw) != EXPECTED_H4_ASSEMBLY["raw_stiffness_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 raw stiffness mismatch")
    canonical = raw.tolil(copy=True)
    tagged_values: list[float] = []
    for first, second in tags:
        value = float(raw[first, second])
        reverse = float(raw[second, first])
        if value != reverse:
            raise AvBsError("BLOCKED_AV_BS_RECIPROCITY", "H4 tagged edge is not symmetric")
        tagged_values.append(value)
        canonical[first, first] = float(canonical[first, first]) + value
        canonical[second, second] = float(canonical[second, second]) + value
        canonical[first, second] = 0.0
        canonical[second, first] = 0.0
    canonical = canonical.tocsc()
    canonical.sum_duplicates()
    canonical.sort_indices()
    canonical.eliminate_zeros()
    if (
        canonical.nnz != EXPECTED_H4_ASSEMBLY["canonical_stiffness_nnz"]
        or h1._sparse_sha256(canonical) != EXPECTED_H4_ASSEMBLY["canonical_stiffness_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 canonical stiffness mismatch")
    canonical_support_hash = sha256(
        canonical_bytes([list(edge) for edge in sorted(zip(*canonical.nonzero()))])
    ).hexdigest()
    if canonical_support_hash != EXPECTED_H4_ASSEMBLY["canonical_support_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 canonical support mismatch")
    raw_null = h1._constant_null_relative(raw)
    canonical_null = h1._constant_null_relative(canonical)
    raw_transpose = h1._sparse_transpose_relative(raw)
    canonical_transpose = h1._sparse_transpose_relative(canonical)
    correction = csc_matrix(canonical - raw)
    correction_relative = h1._sparse_frobenius(correction) / h1._sparse_frobenius(raw)
    observed_metrics = {
        "raw_constant_null_relative": raw_null,
        "canonical_constant_null_relative": canonical_null,
        "raw_transpose_relative": raw_transpose,
        "canonical_transpose_relative": canonical_transpose,
        "correction_relative_frobenius": correction_relative,
        "minimum_tagged_binary64_abs": min(abs(value) for value in tagged_values),
        "maximum_tagged_binary64_abs": max(abs(value) for value in tagged_values),
    }
    for key, value in observed_metrics.items():
        if value != EXPECTED_H4_ASSEMBLY[key]:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"H4 {key} mismatch")
    if max(raw_null, canonical_null, raw_transpose, canonical_transpose) > h1.MAX_ASSEMBLY_TRANSPOSE:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 null/transpose gate failed")
    return canonical, {
        "tag_count": len(tags),
        "tag_sha256": EXPECTED_H4_ASSEMBLY["tag_sha256"],
        "tag_incidence_two_count": sum(value == 2 for value in incidence.values()),
        "cancellation_formula": "256u_kappa_explicit_P1_determinant_expression",
        "inherited_128u_kappa_pass": False,
        **exact_certificate,
        "full_support_sha256": full_support_hash,
        "canonical_support_sha256": canonical_support_hash,
        "raw_stiffness_nnz": int(raw.nnz),
        "canonical_stiffness_nnz": int(canonical.nnz),
        "raw_stiffness_sha256": h1._sparse_sha256(raw),
        "canonical_stiffness_sha256": h1._sparse_sha256(canonical),
        **observed_metrics,
        "nonzero_tagged_binary64_values": sum(value != 0.0 for value in tagged_values),
    }


def _block_nnz(matrix: csc_matrix, interior: np.ndarray, gamma: np.ndarray) -> Mapping[str, int]:
    return {
        "II": int(matrix[interior, :][:, interior].nnz),
        "IG": int(matrix[interior, :][:, gamma].nnz),
        "GI": int(matrix[gamma, :][:, interior].nnz),
        "GG": int(matrix[gamma, :][:, gamma].nnz),
    }


def resource_preflight(sparse_bytes: int, interior_count: int, boundary_count: int) -> Mapping[str, object]:
    if sparse_bytes != EXPECTED_H4_ASSEMBLY["sparse_base_arrays_bytes"]:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H4 sparse byte count mismatch")
    copy_bytes = 16 * sparse_bytes
    dense_upper = 2 * interior_count * interior_count * 16
    extensions = 2 * interior_count * boundary_count * 16
    boundary_dense = 3 * boundary_count * boundary_count * 16
    batch_rhs = 4 * interior_count * h1.BATCH_SIZE * 16
    dense_raw = sparse_bytes + copy_bytes + dense_upper + extensions + boundary_dense + batch_rhs
    dense_margin = math.ceil(1.25 * dense_raw)
    factor_cap = 2 * 1024**3
    guarded_raw = sparse_bytes + copy_bytes + factor_cap + extensions + boundary_dense + batch_rhs
    guarded_margin = math.ceil(1.25 * guarded_raw)
    ws_slack = h1.TREE_WS_STOP_BYTES - guarded_margin
    observed = (
        copy_bytes,
        dense_upper,
        extensions,
        boundary_dense,
        batch_rhs,
        dense_raw,
        dense_margin,
        guarded_raw,
        guarded_margin,
        ws_slack,
    )
    expected = (
        87196352,
        31729827872,
        515915776,
        12582912,
        8061184,
        32359033868,
        40448792335,
        2776689644,
        3470862055,
        824105241,
    )
    if observed != expected:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"H4 resource envelope mismatch {observed!r}")
    if dense_margin <= h1.TREE_WS_STOP_BYTES or guarded_margin > h1.TREE_WS_STOP_BYTES:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "H4 resource classification mismatch")
    return {
        "dense_upper_diagnostic": {
            "method": "dense_factor_upper_diagnostic_only",
            "one_factor_dense_upper_bytes": dense_upper,
            "raw_total_bytes": dense_raw,
            "total_with_25pct_margin_bytes": dense_margin,
            "primary_h4_resource_preflight_pass": False,
        },
        "guarded_one_factor_prospective": {
            "method": "one_factor_hard_cap_plus_sparse_and_rectangular_25pct",
            "sparse_base_arrays_bytes": sparse_bytes,
            "sparse_copy_allowance_bytes": copy_bytes,
            "sparse_copy_allowance_multiplier_of_base": 16,
            "one_factor_hard_cap_bytes": factor_cap,
            "two_interior_extensions_bytes": extensions,
            "boundary_dense_work_bytes": boundary_dense,
            "batch_rhs_work_bytes": batch_rhs,
            "raw_total_bytes": guarded_raw,
            "total_with_25pct_margin_bytes": guarded_margin,
            "tree_working_set_stop_bytes": h1.TREE_WS_STOP_BYTES,
            "tree_private_stop_bytes": h1.TREE_PRIVATE_STOP_BYTES,
            "tree_commit_stop_bytes": h1.TREE_COMMIT_STOP_BYTES,
            "tree_working_set_slack_bytes": ws_slack,
            "minimum_available_physical_before_spawn_bytes": guarded_margin + 1610612736,
            "minimum_commit_headroom_before_spawn_bytes": guarded_margin + 2147483648,
            "candidate_envelope_with_cap_pass": True,
            "factor_fit_unproven": True,
        },
        "primary_h4_authorized": False,
        "next_resource_decision_requires_separate_preregistration": True,
    }


def run_manifest() -> Mapping[str, object]:
    if h1._runtime() != h1.EXPECTED_RUNTIME:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 runtime mismatch")
    fixture_path = Path(__file__).with_name("av_bs1_boundary_schur_h2.py")
    runner_path = Path(__file__).with_name("run_av_bs1_h2_stage.ps1")
    dependencies = {
        "h2_fixture_sha256": h1._file_sha256(fixture_path),
        "h2_runner_sha256": h1._file_sha256(runner_path),
    }
    if dependencies != {
        "h2_fixture_sha256": H2_FIXTURE_SHA256,
        "h2_runner_sha256": H2_RUNNER_SHA256,
    }:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 dependency mismatch")
    h2_payload = h2.run_manifest()
    observed_h2_payload_sha = h1._wrapper(h2_payload)["payload_sha256"]
    if observed_h2_payload_sha != H2_PAYLOAD_SHA256:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "H2 payload mismatch")
    nodes, triangles, midpoint = seed_mesh_h4()
    manifest, boundary = mesh_manifest(nodes, triangles)
    tags, lineage = h4_tags(midpoint)
    raw, mass = h1._assemble_volume(nodes, triangles)
    canonical, certificate = canonicalize_h4(raw, nodes, triangles, tags, manifest)
    boundary_hash = sha256(canonical_bytes([list(edge) for edge in boundary])).hexdigest()
    if boundary_hash != EXPECTED_H4_ASSEMBLY["boundary_edges_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 boundary hash mismatch")
    interior, gamma, gamma_map = partition(len(nodes), boundary)
    trace_mass = h1._assemble_trace_mass(nodes, boundary, gamma_map)
    trace_mass.sum_duplicates()
    trace_mass.sort_indices()
    trace_mass.eliminate_zeros()
    if (
        mass.nnz != EXPECTED_H4_ASSEMBLY["mass_nnz"]
        or h1._sparse_sha256(mass) != EXPECTED_H4_ASSEMBLY["mass_sha256"]
        or trace_mass.nnz != EXPECTED_H4_ASSEMBLY["trace_mass_nnz"]
        or h1._sparse_sha256(trace_mass) != EXPECTED_H4_ASSEMBLY["trace_mass_sha256"]
    ):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 mass gate mismatch")
    trace_support_hash = sha256(
        canonical_bytes([list(edge) for edge in sorted(zip(*trace_mass.nonzero()))])
    ).hexdigest()
    if trace_support_hash != EXPECTED_H4_ASSEMBLY["trace_support_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 trace support mismatch")
    canonical_blocks = _block_nnz(canonical, interior, gamma)
    mass_blocks = _block_nnz(mass, interior, gamma)
    if canonical_blocks != EXPECTED_H4_ASSEMBLY["canonical_block_nnz"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 canonical block pattern mismatch")
    if mass_blocks != EXPECTED_H4_ASSEMBLY["mass_block_nnz"]:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "H4 mass block pattern mismatch")
    sparse_bytes = sum(
        int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
        for matrix in (canonical, mass, trace_mass)
    )
    preflight = resource_preflight(sparse_bytes, len(interior), len(gamma))
    return {
        "schema": FIXTURE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "manifest",
        "status": "preregistered_H4_P0_assembly_only_no_solve",
        "authorization_state": "not_authorized",
        "dependencies": {**dependencies, "h2_manifest_payload_sha256": observed_h2_payload_sha},
        "mesh": manifest,
        "partition": {
            "interior_nodes": len(interior),
            "boundary_nodes": len(gamma),
            "interior_sha256": EXPECTED_H4_ASSEMBLY["interior_sha256"],
            "gamma_sha256": EXPECTED_H4_ASSEMBLY["gamma_sha256"],
        },
        "cyclic_lineage": lineage,
        "assembly": {
            **certificate,
            "mass_nnz": int(mass.nnz),
            "mass_sha256": h1._sparse_sha256(mass),
            "trace_mass_nnz": int(trace_mass.nnz),
            "trace_mass_sha256": h1._sparse_sha256(trace_mass),
            "boundary_edges_sha256": boundary_hash,
            "trace_support_sha256": trace_support_hash,
            "canonical_block_nnz": canonical_blocks,
            "mass_block_nnz": mass_blocks,
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
    parser = argparse.ArgumentParser(description="SPD Decap PI Evaluator v0.22.0 H4-P0")
    parser.add_argument("--stage", required=True, choices=("manifest",))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(
            json.dumps(
                h1._wrapper(run_manifest()),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return 0
    except AvBsError as error:
        print(
            json.dumps(
                _failure_wrapper(error, args.stage),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
