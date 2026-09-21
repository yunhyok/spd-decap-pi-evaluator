"""Bounded Stage2A C1 preparation for the frozen full production cell258.

This module deliberately stops at a no-mesher preparation boundary.  It verifies
the sealed C0/provenance graph, constructs the C0 PSLG in owned NumPy arrays,
streams the canonical identity, and validates only caller-supplied future result
arrays.  No optional meshing extension is imported or invoked here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO
from types import MappingProxyType

import numpy as np


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"

ARTIFACT_ROOT = Path(
    r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell258-stage2a-c0-cap-02"
)
C0_RECEIPT = ARTIFACT_ROOT / "stage2a_c0_census_receipt.json"
C0_SIZE = 5_448_871
C0_SHA256 = "5ae752680b63686f90dd0d80037b337bba0da71f3bd9d6d755a9e94365f9ac60"

STAGE0_PATH = Path(
    r"C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\tools\research\d117_triangle_quality_mesh.py"
)
STAGE0_SIZE = 25_832
STAGE0_SHA256 = "f43a4dd3ab531b75c2f8a450406ddb70bf92cf47a94e67f2dbcede207b9c4bdd"

D103_PATH = Path(
    r"D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json"
)
D103_SIZE = 204_735
D103_SHA256 = "4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9"

D104_ROOT = Path(
    r"D:\SPD-Decap-PI-Evaluator-W7\fb596d929427d926f091df380b88f162f830483d\260729-d104-source-local-window-geometry-01"
)
D104_PATH = D104_ROOT / "geometry_receipt.json"
D104_SIZE = 19_728
D104_SHA256 = "bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b"
SOURCE_PATH = D104_ROOT / "cell_0258.wkb"
SOURCE_SIZE = 2_426_237
SOURCE_SHA256 = "1d894ff46db6fdf1e1662d1ae9d45cbb6676b5d002c0357c27c74f9f1e4835e1"

WHEEL_PATH = Path(
    r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d117-triangle-w0-research-pilot-01\triangle-20250106-cp312-cp312-win_amd64.whl"
)
WHEEL_NAME = "triangle-20250106-cp312-cp312-win_amd64.whl"
WHEEL_SIZE = 1_426_720
WHEEL_SHA256 = "0327032a7984a7262180ef2ddd78b36dfdcdecbc79f0f9f173732ce7c670b8ed"

ORDINAL = 258
LAYER = "Signal$L28(DGND)"
NET = "DGND"
ISLAND = "spd-surface-island:ea4bc44349ce103beab4dca3"
THICKNESS_UM = 35.0
CONDUCTIVITY_S_PER_M = 59_590_000.0
EXPECTED_AREA_UM2 = 8_476_333_524.145388
EXPECTED_BOUNDS = (-49_700.0, -49_700.0, 49_700.0, 49_700.0)
EXPECTED_RING_COUNT = 2_049
EXPECTED_HOLE_COUNT = 2_048
EXPECTED_EXTERIOR_VERTICES = 8
EXPECTED_HOLE_VERTICES = 149_070
EXPECTED_ORIGINAL_VERTICES = 149_078

SPLIT_STEP_UM = 140.0
EXPECTED_PSLG_VERTICES = 153_246
EXPECTED_PSLG_SEGMENTS = 153_246
EXPECTED_MARKERS = 149_078
EXPECTED_HOLES = 2_048
EXPECTED_PSLG_DISTRIBUTION = {1: 149_070, 16: 3, 32: 1, 1024: 4}
EXPECTED_PSLG_CANONICAL_BYTES = 12_057_453
EXPECTED_PSLG_CANONICAL_SHA256 = "1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b"

PLANAR_VERTEX_CAP = 400_000
PLANAR_TRIANGLE_CAP = 600_000
T_FLOOR = 157_340
CLOSED_VERTEX_CAP = 800_000
CLOSED_FACE_CAP = 1_608_188
FUTURE_PROCESS_CAP_BYTES = 3_435_970_560

STEINER_POINT_CAP = (PLANAR_TRIANGLE_CAP - T_FLOOR) // 2
STEINER_VERTEX_MAX = EXPECTED_PSLG_VERTICES + STEINER_POINT_CAP
STEINER_TRIANGLE_MAX = T_FLOOR + 2 * STEINER_POINT_CAP
STEINER_SEGMENT_MAX = EXPECTED_PSLG_SEGMENTS + STEINER_POINT_CAP
STEINER_CLOSED_VERTEX_MAX = 2 * STEINER_VERTEX_MAX
STEINER_CLOSED_FACE_MAX = 4 * STEINER_VERTEX_MAX + 4 * EXPECTED_HOLES - 4
TRIANGLE_OPTIONS = f"pq15CzS{STEINER_POINT_CAP}"

INPUT_ARRAY_BYTES_CAP = 4_323_656
OUTPUT_ARRAY_BYTES_CAP = 20_032_768
COMBINED_ARRAY_BYTES_CAP = 24_356_424
CHUNK_SIZE_CAP = 8_192
READ_CHUNK_BYTES = CHUNK_SIZE_CAP
UINT64_BYTES = 8
BOUNDARY_TRIANGLE_EDGE_BYTES_CAP = PLANAR_TRIANGLE_CAP * 3 * UINT64_BYTES
BOUNDARY_RECORD_BYTES_CAP = PLANAR_VERTEX_CAP * UINT64_BYTES
BOUNDARY_SCRATCH_BYTES_CAP = CHUNK_SIZE_CAP * 3 * UINT64_BYTES
BOUNDARY_MEMORY_BYTES_CAP = (
    BOUNDARY_TRIANGLE_EDGE_BYTES_CAP
    + BOUNDARY_RECORD_BYTES_CAP
    + BOUNDARY_SCRATCH_BYTES_CAP
)
# The full certifier floor is derived below after all direct certifier terms
# are declared; BOUNDARY_MEMORY_BYTES_CAP remains the orientation-only slice.
CANONICAL_HEADER_LINE_BYTES = 56
CANONICAL_VERTEX_LINE_BYTES = 59
CANONICAL_VERTEX_MARKER_LINE_BYTES = 21
CANONICAL_TRIANGLE_LINE_BYTES = 30
CANONICAL_SEGMENT_LINE_BYTES = 23
CANONICAL_SEGMENT_MARKER_LINE_BYTES = 20
CANONICAL_HOLE_LINE_BYTES = 52
FUTURE_CANONICAL_BYTES_CAP = (
    CANONICAL_HEADER_LINE_BYTES
    + PLANAR_VERTEX_CAP * (CANONICAL_VERTEX_LINE_BYTES + CANONICAL_VERTEX_MARKER_LINE_BYTES)
    + PLANAR_TRIANGLE_CAP * CANONICAL_TRIANGLE_LINE_BYTES
    + PLANAR_VERTEX_CAP * (CANONICAL_SEGMENT_LINE_BYTES + CANONICAL_SEGMENT_MARKER_LINE_BYTES)
    + EXPECTED_HOLES * CANONICAL_HOLE_LINE_BYTES
)

STATIC_OWNED_NDARRAY_BYTES_PASS = "STATIC_OWNED_NDARRAY_BYTES_PASS"
STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE = "STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE"
OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN = "OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN"
STATIC_NATIVE_BOUND = "NOT_PROVABLE_FROM_PINNED_SOURCE_ALONE"
PREPARATION_STATUS = "READY_FOR_NO_TRIANGLE_IMPLEMENTATION"
C1_EXECUTION_STATUS = "C1_EXECUTION_STILL_STOP"
CERTIFICATION_STATUS = "INCOMPLETE_C1_DEFERRED"
FULL_2D_CERT_STATUS = "FULL_2D_CERT_STOP"

CONTROLLED_BUFFER_EXCLUDED_TERMS = (
    "sealed-read copies",
    "ring storage",
    "unique/sort/bincount scratch",
    "chunk gathers",
    "canonical I/O/receipt",
    "interpreter/native/import/allocator",
)

MEMORY_LEDGER_PHASES = (
    "read/parse",
    "PSLG",
    "native-call",
    "result",
    "validation",
    "boundary",
    "quality",
    "canonical",
    "receipt",
)
MEMORY_LEDGER_CLASSES = (
    "formula-bounded",
    "mutually exclusive",
    "concurrently live",
    "job-contained opaque",
)
MEMORY_LEDGER_REQUIRED_TERMS = (
    "sealed_read_buffer",
    "source_parser_copy",
    "parsed_provenance_receipts",
    "parsed_source_geometry",
    "pslg_input_arrays",
    "pslg_ring_storage",
    "native_call_workspace",
    "result_arrays",
    "validation_scratch",
    "boundary_triangle_edges",
    "boundary_records",
    "boundary_chunk_scratch",
    "quality_used_vertex_scan",
    "quality_gather_scratch",
    "quality_numpy_allocator_retention",
    "canonical_stream",
    "receipt_payload",
)
MEMORY_LEDGER_TERM_CONTRACT = MappingProxyType(
    {
        "sealed_read_buffer": MappingProxyType({"phases": ("read/parse",), "classification": "mutually exclusive", "bound_bytes": max(C0_SIZE, D103_SIZE, D104_SIZE, SOURCE_SIZE, STAGE0_SIZE, WHEEL_SIZE)}),
        "source_parser_copy": MappingProxyType({"phases": ("read/parse",), "classification": "formula-bounded", "bound_bytes": SOURCE_SIZE}),
        "parsed_provenance_receipts": MappingProxyType({"phases": ("read/parse",), "classification": "job-contained opaque", "bound_bytes": None}),
        "parsed_source_geometry": MappingProxyType({"phases": ("read/parse", "PSLG"), "classification": "job-contained opaque", "bound_bytes": None}),
        "pslg_input_arrays": MappingProxyType({"phases": ("PSLG", "native-call", "result", "validation", "boundary", "quality", "canonical", "receipt"), "classification": "concurrently live", "bound_bytes": INPUT_ARRAY_BYTES_CAP}),
        "pslg_ring_storage": MappingProxyType({"phases": ("PSLG",), "classification": "job-contained opaque", "bound_bytes": None}),
        "native_call_workspace": MappingProxyType({"phases": ("native-call",), "classification": "job-contained opaque", "bound_bytes": None}),
        "result_arrays": MappingProxyType({"phases": ("native-call", "result", "validation", "boundary", "quality", "canonical", "receipt"), "classification": "concurrently live", "bound_bytes": OUTPUT_ARRAY_BYTES_CAP}),
        "validation_scratch": MappingProxyType({"phases": ("validation", "canonical"), "classification": "job-contained opaque", "bound_bytes": None}),
        "boundary_triangle_edges": MappingProxyType({"phases": ("boundary",), "classification": "concurrently live", "bound_bytes": BOUNDARY_TRIANGLE_EDGE_BYTES_CAP}),
        "boundary_records": MappingProxyType({"phases": ("boundary",), "classification": "concurrently live", "bound_bytes": BOUNDARY_RECORD_BYTES_CAP}),
        "boundary_chunk_scratch": MappingProxyType({"phases": ("boundary",), "classification": "concurrently live", "bound_bytes": BOUNDARY_SCRATCH_BYTES_CAP}),
        "quality_used_vertex_scan": MappingProxyType({"phases": ("quality",), "classification": "formula-bounded", "bound_bytes": PLANAR_VERTEX_CAP}),
        "quality_gather_scratch": MappingProxyType({"phases": ("quality",), "classification": "formula-bounded", "bound_bytes": CHUNK_SIZE_CAP * 97, "formula": "three float64[chunk,2] gathers + six float64 vectors + one bool vector in streamed area check (97*chunk bytes)"}),
        "quality_numpy_allocator_retention": MappingProxyType({"phases": ("quality",), "classification": "job-contained opaque", "bound_bytes": None}),
        "canonical_stream": MappingProxyType({"phases": ("canonical",), "classification": "formula-bounded", "bound_bytes": FUTURE_CANONICAL_BYTES_CAP}),
        "receipt_payload": MappingProxyType({"phases": ("receipt",), "classification": "job-contained opaque", "bound_bytes": None}),
    }
)
MEMORY_LEDGER_FLOOR_COMPONENTS = (
    "pslg_input_arrays",
    "result_arrays",
    "boundary_triangle_edges",
    "boundary_records",
    "boundary_chunk_scratch",
)
MEMORY_LEDGER_REQUIRED_OVERLAPS = (
    ("sealed_read_buffer", "source_parser_copy"),
    ("sealed_read_buffer", "parsed_provenance_receipts"),
    ("sealed_read_buffer", "parsed_source_geometry"),
    ("source_parser_copy", "parsed_source_geometry"),
    ("parsed_source_geometry", "pslg_input_arrays"),
    ("parsed_source_geometry", "pslg_ring_storage"),
    ("pslg_input_arrays", "pslg_ring_storage"),
    ("pslg_input_arrays", "native_call_workspace"),
    ("pslg_input_arrays", "result_arrays"),
    ("result_arrays", "native_call_workspace"),
    ("pslg_input_arrays", "validation_scratch"),
    ("pslg_input_arrays", "boundary_triangle_edges"),
    ("pslg_input_arrays", "boundary_records"),
    ("pslg_input_arrays", "boundary_chunk_scratch"),
    ("pslg_input_arrays", "quality_used_vertex_scan"),
    ("pslg_input_arrays", "quality_gather_scratch"),
    ("pslg_input_arrays", "quality_numpy_allocator_retention"),
    ("pslg_input_arrays", "canonical_stream"),
    ("result_arrays", "boundary_triangle_edges"),
    ("result_arrays", "boundary_records"),
    ("result_arrays", "boundary_chunk_scratch"),
    ("result_arrays", "validation_scratch"),
    ("result_arrays", "quality_used_vertex_scan"),
    ("result_arrays", "quality_gather_scratch"),
    ("result_arrays", "quality_numpy_allocator_retention"),
    ("result_arrays", "canonical_stream"),
    ("result_arrays", "receipt_payload"),
    ("pslg_input_arrays", "receipt_payload"),
    ("boundary_chunk_scratch", "boundary_records"),
    ("boundary_chunk_scratch", "boundary_triangle_edges"),
    ("boundary_records", "boundary_triangle_edges"),
    ("quality_used_vertex_scan", "quality_gather_scratch"),
    ("quality_used_vertex_scan", "quality_numpy_allocator_retention"),
    ("quality_gather_scratch", "quality_numpy_allocator_retention"),
)

# The static C1 certifier owns additional bounded records.  Keep every direct
# ndarray allocation as an explicit ledger term.  NumPy's internal allocations
# remain a separate opaque term because their release/retention is not
# source-provable.
_CERTIFIER_LEDGER_TERM_CONTRACT = MappingProxyType(
    {
        "certifier_coordinate_records": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 16,
                "formula": "PLANAR_VERTEX_CAP * structured coordinate record (2 * float64)",
            }
        ),
        "certifier_coordinate_zero_masks": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP,
                "formula": "one PLANAR_VERTEX_CAP bool mask at a time for signed-zero normalization",
            }
        ),
        "certifier_face_keys": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_TRIANGLE_CAP * 24,
                "formula": "PLANAR_TRIANGLE_CAP * face key record (3 * uint64)",
            }
        ),
        "certifier_face_key_casts": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_TRIANGLE_CAP * 3 * 8,
                "formula": "3 * PLANAR_TRIANGLE_CAP * uint64 casts of triangle columns",
            }
        ),
        "certifier_face_key_expression_scratch": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_TRIANGLE_CAP * 2 * 8,
                "formula": "2 * PLANAR_TRIANGLE_CAP * uint64 ufunc expression temporaries",
            }
        ),
        "certifier_validation_predicate_scratch": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": max(
                    9 * EXPECTED_PSLG_VERTICES,
                    16 * EXPECTED_PSLG_VERTICES,
                    16 * EXPECTED_PSLG_SEGMENTS,
                    8 * (EXPECTED_MARKERS + 1) + 8 * EXPECTED_MARKERS + (EXPECTED_MARKERS + 1),
                    2 * EXPECTED_PSLG_VERTICES,
                    2 * EXPECTED_HOLES,
                    2 * PLANAR_VERTEX_CAP,
                    PLANAR_TRIANGLE_CAP,
                    PLANAR_VERTEX_CAP,
                ),
                "formula": "frozen validation max(pslg bincount 8*V+bool[V], unique vertex 16*V, normalized+unique segments 16*B, marker histogram+unique+one comparison bool 8*(M+1)+8*M+(M+1), pslg hole/finite masks 2*H, result predicates max(2*V,T,B,2*H))",
            }
        ),
        "certifier_canonical_predicate_scratch": MappingProxyType(
            {
                "phases": ("canonical",),
                "classification": "formula-bounded",
                "bound_bytes": max(
                    9 * EXPECTED_PSLG_VERTICES,
                    2 * PLANAR_VERTEX_CAP,
                    PLANAR_TRIANGLE_CAP,
                    PLANAR_VERTEX_CAP,
                    2 * EXPECTED_HOLES,
                ),
                "formula": "canonical validator max(nonfrozen PSLG bincount 8*S+bool[S], future-result predicates max(2*V,T,B,2*H))",
            }
        ),
        "certifier_halfedge_twin": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_TRIANGLE_CAP * 3 * 32,
                "formula": "3 * PLANAR_TRIANGLE_CAP * aligned halfedge/twin record (uint64 key + uint64 face + uint8 direction + uint64 twin)",
            }
        ),
        "certifier_connectivity": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_TRIANGLE_CAP * 8,
                "formula": "PLANAR_TRIANGLE_CAP * int64 connectivity parent",
            }
        ),
        "certifier_vertex_link_boundary_degree": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 4,
                "formula": "PLANAR_VERTEX_CAP * int32 vertex-link boundary degree",
            }
        ),
        "certifier_vertex_link_corner_count": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 8,
                "formula": "PLANAR_VERTEX_CAP * int64 vertex-link corner count",
            }
        ),
        "certifier_vertex_link_outgoing_arcs": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_TRIANGLE_CAP * 3 * 16,
                "formula": "3 * PLANAR_TRIANGLE_CAP * outgoing link arc record (2 * uint64)",
            }
        ),
        "certifier_vertex_link_incoming_arcs": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_TRIANGLE_CAP * 3 * 8,
                "formula": "3 * PLANAR_TRIANGLE_CAP * incoming link uint64 key",
            }
        ),
        "certifier_vertex_link_visitation": MappingProxyType(
            {
                "phases": ("validation",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 8,
                "formula": "PLANAR_VERTEX_CAP * int64 vertex-link visitation stamp",
            }
        ),
        "certifier_boundary_rows_lookup": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 16,
                "formula": "PLANAR_VERTEX_CAP * boundary lookup record (2 * uint64)",
            }
        ),
        "certifier_boundary_rows": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 8,
                "formula": "PLANAR_VERTEX_CAP * int64 boundary row map",
            }
        ),
        "certifier_boundary_starts": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 8,
                "formula": "PLANAR_VERTEX_CAP * int64 induced boundary start",
            }
        ),
        "certifier_boundary_ends": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 8,
                "formula": "PLANAR_VERTEX_CAP * int64 induced boundary end",
            }
        ),
        "certifier_boundary_markers": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 8,
                "formula": "PLANAR_VERTEX_CAP * int64 induced boundary marker",
            }
        ),
        "certifier_boundary_adjacency_sort_records": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 2 * 24,
                "formula": "2 * PLANAR_VERTEX_CAP * boundary adjacency sort record (3 * uint64)",
            }
        ),
        "certifier_boundary_adjacency_maps": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * (8 + 8 + 4 + 8 + 4),
                "formula": "PLANAR_VERTEX_CAP * boundary next/previous/edge/count maps (3 * int64 + 2 * int32)",
            }
        ),
        "certifier_boundary_adjacency_return_maps": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * (8 + 8 + 8 + 4),
                "formula": "PLANAR_VERTEX_CAP * maps returned to cycle evidence before caller deletion (next_vertex + previous_vertex + next_edge + out_count)",
            }
        ),
        "certifier_boundary_adjacency_partition_maps": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * (8 + 8),
                "formula": "PLANAR_VERTEX_CAP * retained next_vertex + next_edge maps after previous_vertex/out_count deletion (2 * int64)",
            }
        ),
        "certifier_boundary_cycle_visitation": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP * 2,
                "formula": "PLANAR_VERTEX_CAP * visited vertex bool + visited edge bool",
            }
        ),
        "certifier_boundary_consumed": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP,
                "formula": "PLANAR_VERTEX_CAP * consumed boundary-edge bool",
            }
        ),
        "certifier_boundary_mask": MappingProxyType(
            {
                "phases": ("boundary", "validation", "quality"),
                "classification": "formula-bounded",
                "bound_bytes": PLANAR_VERTEX_CAP,
                "formula": "PLANAR_VERTEX_CAP * caller-retained boundary mask bool",
            }
        ),
        "certifier_boundary_python_storage": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "job-contained opaque",
                "bound_bytes": None,
                "formula": "Python boundary cycle tuples and object allocator retention are not source-provable",
            }
        ),
        "certifier_boundary_numpy_allocator_retention": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "job-contained opaque",
                "bound_bytes": None,
                "formula": "NumPy boundary sort/search allocator retention is not source-provable",
            }
        ),
        "certifier_winding_bounds": MappingProxyType(
            {
                "phases": ("boundary",),
                "classification": "formula-bounded",
                "bound_bytes": max(2 * 2 * 8, 3 * 8),
                "formula": "max(two co-live float64[2] min/max bound arrays, transient float64[3] triangle gather)",
            }
        ),
        "certifier_quality_scratch": MappingProxyType(
            {
                "phases": ("quality",),
                "classification": "formula-bounded",
                "bound_bytes": CHUNK_SIZE_CAP * (18 * 8),
                "formula": "CHUNK_SIZE_CAP * eighteen float64 vectors (17 persistent vectors + one expression temporary; predicate is a later lifetime)",
            }
        ),
        "certifier_quality_predicate": MappingProxyType(
            {
                "phases": ("quality",),
                "classification": "formula-bounded",
                "bound_bytes": CHUNK_SIZE_CAP * (17 * 8 + 1),
                "formula": "CHUNK_SIZE_CAP * seventeen persistent float64 vectors + one bool predicate after expression scratch is released",
            }
        ),
    }
)

_MEMORY_LEDGER_TERM_CONTRACT_BASE = dict(MEMORY_LEDGER_TERM_CONTRACT)
_MEMORY_LEDGER_TERM_CONTRACT_BASE.update(_CERTIFIER_LEDGER_TERM_CONTRACT)
MEMORY_LEDGER_TERM_CONTRACT = MappingProxyType(_MEMORY_LEDGER_TERM_CONTRACT_BASE)
MEMORY_LEDGER_REQUIRED_TERMS = MEMORY_LEDGER_REQUIRED_TERMS + tuple(_CERTIFIER_LEDGER_TERM_CONTRACT)

# Only arrays that are actually co-live are paired.  A phase is a lifetime
# label, not permission to sum mutually-exclusive subphases into a false peak.
_CERTIFIER_CO_LIVE_GROUPS = (
    ("pslg_input_arrays", "result_arrays"),
    ("pslg_input_arrays", "parsed_source_geometry", "pslg_ring_storage"),
    ("pslg_input_arrays", "native_call_workspace", "result_arrays"),
    ("pslg_input_arrays", "result_arrays", "certifier_coordinate_records", "certifier_coordinate_zero_masks", "validation_scratch"),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_face_keys",
        "certifier_face_key_casts",
        "certifier_face_key_expression_scratch",
        "validation_scratch",
    ),
    ("pslg_input_arrays", "result_arrays", "certifier_validation_predicate_scratch", "validation_scratch"),
    ("pslg_input_arrays", "result_arrays", "certifier_canonical_predicate_scratch", "validation_scratch"),
    (
        "pslg_input_arrays",
        "result_arrays",
        "boundary_triangle_edges",
        "boundary_records",
        "boundary_chunk_scratch",
        "certifier_boundary_numpy_allocator_retention",
    ),
    (
        "pslg_input_arrays",
        "result_arrays",
        "boundary_records",
        "certifier_boundary_rows_lookup",
        "certifier_boundary_rows",
        "certifier_boundary_starts",
        "certifier_boundary_ends",
        "certifier_boundary_markers",
        "certifier_boundary_numpy_allocator_retention",
    ),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_boundary_rows",
        "certifier_boundary_mask",
        "certifier_boundary_starts",
        "certifier_boundary_ends",
        "certifier_boundary_numpy_allocator_retention",
    ),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_boundary_mask",
        "certifier_boundary_adjacency_sort_records",
        "certifier_boundary_adjacency_maps",
        "certifier_boundary_starts",
        "certifier_boundary_ends",
        "certifier_boundary_numpy_allocator_retention",
    ),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_boundary_mask",
        "certifier_boundary_adjacency_return_maps",
        "certifier_boundary_cycle_visitation",
        "certifier_boundary_starts",
        "certifier_boundary_ends",
        "certifier_boundary_python_storage",
        "certifier_boundary_numpy_allocator_retention",
    ),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_boundary_mask",
        "certifier_boundary_adjacency_partition_maps",
        "certifier_boundary_consumed",
        "certifier_boundary_numpy_allocator_retention",
    ),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_boundary_mask",
        "certifier_boundary_adjacency_return_maps",
        "certifier_boundary_cycle_visitation",
        "certifier_boundary_starts",
        "certifier_boundary_ends",
        "certifier_boundary_python_storage",
        "certifier_boundary_numpy_allocator_retention",
    ),
    ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_halfedge_twin", "certifier_connectivity", "validation_scratch"),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_boundary_mask",
        "certifier_vertex_link_boundary_degree",
        "certifier_vertex_link_corner_count",
        "certifier_vertex_link_outgoing_arcs",
        "certifier_vertex_link_incoming_arcs",
        "certifier_vertex_link_visitation",
        "validation_scratch",
    ),
    ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "quality_used_vertex_scan", "quality_gather_scratch", "quality_numpy_allocator_retention"),
    ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_quality_scratch", "quality_numpy_allocator_retention"),
    ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_quality_predicate", "quality_numpy_allocator_retention"),
    ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "quality_used_vertex_scan", "quality_numpy_allocator_retention"),
    ("pslg_input_arrays", "result_arrays", "certifier_boundary_adjacency_return_maps", "certifier_boundary_cycle_visitation", "certifier_boundary_python_storage"),
    (
        "pslg_input_arrays",
        "result_arrays",
        "certifier_boundary_mask",
        "certifier_boundary_adjacency_partition_maps",
        "certifier_boundary_starts",
        "certifier_boundary_ends",
        "certifier_winding_bounds",
        "certifier_boundary_numpy_allocator_retention",
    ),
    ("pslg_input_arrays", "result_arrays", "receipt_payload"),
)
_ledger_overlap_rows: list[tuple[str, str]] = []
for _existing_pair in MEMORY_LEDGER_REQUIRED_OVERLAPS:
    _normalized_pair = tuple(sorted(_existing_pair))
    if _normalized_pair not in _ledger_overlap_rows:
        _ledger_overlap_rows.append(_normalized_pair)
for _group in _CERTIFIER_CO_LIVE_GROUPS:
    for _left_index, _left in enumerate(_group):
        for _right in _group[_left_index + 1 :]:
            _pair = tuple(sorted((_left, _right)))
            if _pair not in _ledger_overlap_rows:
                _ledger_overlap_rows.append(_pair)
MEMORY_LEDGER_REQUIRED_OVERLAPS = tuple(_ledger_overlap_rows)

# The boundary floor is the maximum known co-live boundary subphase after the
# certifier deletes row/cycle evidence at the first safe point.  The overall
# floor is exposed separately because edge-topology validation is larger.
MEMORY_LEDGER_FLOOR_COMPONENTS = (
    "pslg_input_arrays",
    "result_arrays",
    "certifier_boundary_mask",
    "certifier_boundary_adjacency_sort_records",
    "certifier_boundary_adjacency_maps",
    "certifier_boundary_starts",
    "certifier_boundary_ends",
)

MEMORY_LEDGER_PHASE_FLOOR_CANDIDATES = MappingProxyType(
    {
        "read/parse": (("sealed_read_buffer", "source_parser_copy"),),
        "PSLG": (("pslg_input_arrays",),),
        "native-call": (("pslg_input_arrays", "result_arrays"),),
        "result": (("pslg_input_arrays", "result_arrays"),),
        "validation": (
            ("pslg_input_arrays", "result_arrays", "certifier_coordinate_records", "certifier_coordinate_zero_masks"),
            ("pslg_input_arrays", "result_arrays", "certifier_face_keys", "certifier_face_key_casts", "certifier_face_key_expression_scratch"),
            ("pslg_input_arrays", "result_arrays", "certifier_validation_predicate_scratch"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_halfedge_twin", "certifier_connectivity"),
            (
                "pslg_input_arrays",
                "result_arrays",
                "certifier_boundary_mask",
                "certifier_vertex_link_boundary_degree",
                "certifier_vertex_link_corner_count",
                "certifier_vertex_link_outgoing_arcs",
                "certifier_vertex_link_incoming_arcs",
                "certifier_vertex_link_visitation",
            ),
        ),
        "boundary": (
            ("pslg_input_arrays", "result_arrays", "boundary_triangle_edges", "boundary_records", "boundary_chunk_scratch"),
            ("pslg_input_arrays", "result_arrays", "boundary_records", "certifier_boundary_rows_lookup", "certifier_boundary_rows", "certifier_boundary_starts", "certifier_boundary_ends", "certifier_boundary_markers"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_rows", "certifier_boundary_mask", "certifier_boundary_starts", "certifier_boundary_ends"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_boundary_adjacency_sort_records", "certifier_boundary_adjacency_maps", "certifier_boundary_starts", "certifier_boundary_ends"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_boundary_adjacency_return_maps", "certifier_boundary_cycle_visitation", "certifier_boundary_starts", "certifier_boundary_ends"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_boundary_adjacency_partition_maps", "certifier_boundary_consumed"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_boundary_adjacency_partition_maps", "certifier_boundary_starts", "certifier_boundary_ends", "certifier_winding_bounds"),
        ),
        "quality": (
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "quality_used_vertex_scan", "quality_gather_scratch"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_quality_scratch"),
            ("pslg_input_arrays", "result_arrays", "certifier_boundary_mask", "certifier_quality_predicate"),
        ),
        "canonical": (
            ("pslg_input_arrays", "result_arrays"),
            ("pslg_input_arrays", "result_arrays", "certifier_canonical_predicate_scratch"),
        ),
        "receipt": (("pslg_input_arrays", "result_arrays"),),
    }
)

def _pslg_array_bytes(vertex_count: int, hole_count: int) -> int:
    return 28 * int(vertex_count) + 16 * int(hole_count)


def _result_array_bytes(vertex_count: int, triangle_count: int, segment_count: int, hole_count: int) -> int:
    return 20 * int(vertex_count) + 12 * int(triangle_count) + 12 * int(segment_count) + 16 * int(hole_count)


def _validation_predicate_bytes(vertex_count: int, triangle_count: int, segment_count: int, pslg_vertex_count: int, pslg_segment_count: int, pslg_hole_count: int) -> int:
    """Bound one validation predicate family at a time; sequential families are not summed."""
    marker_slots = int(EXPECTED_MARKERS) + 1
    pslg_peak = max(
        9 * int(pslg_vertex_count),
        16 * int(pslg_vertex_count),
        16 * int(pslg_segment_count),
        8 * marker_slots + 8 * int(EXPECTED_MARKERS) + marker_slots,
        2 * int(pslg_vertex_count),
        2 * int(pslg_hole_count),
    )
    result_peak = max(2 * int(vertex_count), int(triangle_count), int(segment_count), 2 * int(pslg_hole_count))
    return max(pslg_peak, result_peak)


def _canonical_predicate_bytes(vertex_count: int, triangle_count: int, segment_count: int, pslg_vertex_count: int, pslg_hole_count: int) -> int:
    """Bound canonical validators after non-frozen PSLG checks release their locals."""
    return max(
        9 * int(pslg_vertex_count),
        2 * int(vertex_count),
        int(triangle_count),
        int(segment_count),
        2 * int(pslg_hole_count),
    )


def certifier_memory_floors(
    vertex_count: int,
    triangle_count: int,
    segment_count: int,
    *,
    chunk_size: int = CHUNK_SIZE_CAP,
    pslg_vertex_count: int = EXPECTED_PSLG_VERTICES,
    pslg_hole_count: int = EXPECTED_HOLES,
) -> dict[str, int]:
    """Derive known ndarray peaks from the certifier's actual array shapes."""
    v, t, b, c, s, h = (int(vertex_count), int(triangle_count), int(segment_count), int(chunk_size), int(pslg_vertex_count), int(pslg_hole_count))
    if not (v >= 0 and t >= 0 and b >= 0 and 1 <= c <= CHUNK_SIZE_CAP and s >= 0 and h >= 0):
        raise ValueError("invalid certifier memory floor counts")
    caller = _pslg_array_bytes(s, h) + _result_array_bytes(v, t, b, h)
    validation_coordinate = caller + 16 * v + v
    validation_face_keys = caller + 64 * t
    validation_predicates = caller + _validation_predicate_bytes(v, t, b, s, s, h)
    validation_edge = caller + b + 104 * t
    validation_vertex_link = caller + b + 20 * v + 72 * t
    canonical = caller + _canonical_predicate_bytes(v, t, b, s, h)
    boundary_orientation = caller + 24 * t + 8 * b + 24 * c
    boundary_rows = caller + 56 * b
    boundary_cycle_construct = caller + 32 * v + 65 * b
    boundary_cycle_visit = caller + 29 * v + 18 * b
    boundary_partition = caller + 16 * v + 2 * b
    validation = max(validation_coordinate, validation_face_keys, validation_predicates, validation_edge, validation_vertex_link)
    quality_area = caller + b + v + c * 97
    quality_stream = caller + b + c * (18 * 8)
    quality_predicate = caller + b + c * (17 * 8 + 1)
    boundary_winding = caller + 16 * v + 17 * b + max(2 * 2 * 8, 3 * 8)
    quality = max(quality_area, quality_stream, quality_predicate)
    boundary = max(boundary_orientation, boundary_rows, boundary_cycle_construct, boundary_cycle_visit, boundary_partition, boundary_winding)
    return {
        "validation": validation,
        "validation_coordinate": validation_coordinate,
        "validation_face_keys": validation_face_keys,
        "validation_predicates": validation_predicates,
        "validation_edge": validation_edge,
        "validation_vertex_link": validation_vertex_link,
        "boundary": boundary,
        "quality": quality,
        "known_peak": max(validation, boundary, quality),
        "boundary_orientation": boundary_orientation,
        "boundary_rows": boundary_rows,
        "boundary_cycle_construct": boundary_cycle_construct,
        "boundary_cycle_visit": boundary_cycle_visit,
        "boundary_partition": boundary_partition,
        "quality_area": quality_area,
        "quality_stream": quality_stream,
        "quality_predicate": quality_predicate,
        "boundary_winding": boundary_winding,
        "canonical": canonical,
    }


_CERTIFIER_CAP_FLOORS = certifier_memory_floors(PLANAR_VERTEX_CAP, PLANAR_TRIANGLE_CAP, PLANAR_VERTEX_CAP)
FULL_CERTIFIER_ARRAY_BOUNDARY_FLOOR_BYTES = _CERTIFIER_CAP_FLOORS["boundary"]
FULL_CERTIFIER_ARRAY_KNOWN_PEAK_BYTES = _CERTIFIER_CAP_FLOORS["known_peak"]
CONTROLLED_RELEASE_NOTE = (
    "del releases Python references only; Python/native allocator memory is not proven "
    "synchronously freed."
)

_PSLG_KEYS = frozenset(("vertices", "segments", "segment_markers", "holes"))
_FUTURE_RESULT_KEYS = frozenset(("vertices", "vertex_markers", "triangles", "segments", "segment_markers", "holes"))


class Refusal(RuntimeError):
    """Raised for any failed fail-closed contract."""


def _is_reparse(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError as exc:
        raise Refusal(f"unreadable path: {path}") from exc
    return path.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _stat_identity(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(st.st_mode),
        int(st.st_dev),
        int(st.st_ino),
        int(st.st_size),
        int(st.st_mtime_ns),
    )


def _bounded_chunk_size(value: int, label: str) -> int:
    _require(type(value) is int and 1 <= value <= CHUNK_SIZE_CAP, f"invalid {label}")
    return value


def _read_verified(path: Path, size: int, digest: str, chunk_size: int = READ_CHUNK_BYTES) -> bytearray:
    """Read one sealed file once into one bounded bytearray."""
    if type(size) is not int or size < 0 or _is_reparse(path):
        raise Refusal(f"missing or reparse input: {path}")
    chunk_size = _bounded_chunk_size(chunk_size, "read chunk size")
    try:
        before = path.stat()
    except OSError as exc:
        raise Refusal(f"missing input: {path}") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size != size:
        raise Refusal(f"sealed size mismatch: {path}")
    identity = _stat_identity(before)
    payload = bytearray(size)
    view = memoryview(payload)
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_identity(opened) != identity:
                raise Refusal(f"sealed input changed after open: {path}")
            offset = 0
            while offset < size:
                stop = min(offset + chunk_size, size)
                block = view[offset:stop]
                try:
                    count = stream.readinto(block)
                    if type(count) is not int or count <= 0 or count > stop - offset:
                        raise Refusal(f"sealed input shortened: {path}")
                    digest_block = block[:count]
                    try:
                        hasher.update(digest_block)
                    finally:
                        digest_block.release()
                finally:
                    block.release()
                offset += count
            if stream.read(1):
                raise Refusal(f"sealed input grew while reading: {path}")
    except OSError as exc:
        raise Refusal(f"sealed input read failed: {path}") from exc
    finally:
        view.release()
    try:
        after = path.stat()
    except OSError as exc:
        raise Refusal(f"sealed input disappeared: {path}") from exc
    if _is_reparse(path) or _stat_identity(after) != identity:
        raise Refusal(f"sealed input changed while reading: {path}")
    if hasher.hexdigest() != digest:
        raise Refusal(f"sealed identity mismatch: {path}")
    return payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_json(raw: bytes | bytearray) -> object:
    try:
        return json.loads(
            raw.decode("ascii"),
            parse_float=_finite_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Refusal("strict ASCII JSON contract failed") from exc


def _same(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return len(left) == len(right) and all(key in right and _same(value, right[key]) for key, value in left.items())  # type: ignore[index]
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))  # type: ignore[arg-type]
    return left == right


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Refusal(message)


def _canonical_path(path: Path, expected: Path) -> None:
    try:
        actual = path.resolve(strict=True)
        target = expected.resolve(strict=True)
    except OSError as exc:
        raise Refusal(f"canonical path unavailable: {path}") from exc
    _require(actual == target, f"canonical path mismatch: {path}")


def _validate_c0(c0: dict[str, object]) -> None:
    _require(
        _same(
            (c0.get("program"), c0.get("version"), c0.get("status")),
            (PRODUCT, VERSION, "PASS_STAGE2A_C0_CENSUS"),
        ),
        "C0 product/version/status mismatch",
    )
    _require(
        _same(
            (c0.get("research_only"), c0.get("non_shipped"), c0.get("geometry_only"), c0.get("solver_executed"), c0.get("triangle_extension_loaded"), c0.get("triangle_modules_before"), c0.get("triangle_modules_after")),
            (True, True, True, False, False, [], []),
        ),
        "C0 scope or extension boundary mismatch",
    )
    stage0 = c0.get("stage0_module")
    _require(
        isinstance(stage0, dict)
        and _same(
            (stage0.get("path"), stage0.get("size_bytes"), stage0.get("sha256")),
            (str(STAGE0_PATH), STAGE0_SIZE, STAGE0_SHA256),
        ),
        "C0 Stage0 identity mismatch",
    )
    inputs = c0.get("inputs")
    _require(isinstance(inputs, dict), "C0 inputs object missing")
    expected_inputs = {
        "ordinal": ORDINAL,
        "layer": LAYER,
        "net": NET,
        "island": ISLAND,
        "thickness_um": THICKNESS_UM,
        "conductivity_s_per_m": CONDUCTIVITY_S_PER_M,
    }
    for key, value in expected_inputs.items():
        _require(_same(inputs.get(key), value), f"C0 input {key} mismatch")
    expected_input_records = {
        "d103": (str(D103_PATH), D103_SIZE, D103_SHA256),
        "d104": (str(D104_PATH), D104_SIZE, D104_SHA256),
        "source": (str(SOURCE_PATH), SOURCE_SIZE, SOURCE_SHA256),
        "stage0": (str(STAGE0_PATH), STAGE0_SIZE, STAGE0_SHA256),
    }
    for key, expected in expected_input_records.items():
        row = inputs.get(key)
        if key == "d104":
            _require(isinstance(row, dict) and _same((row.get("path"), row.get("size_bytes"), row.get("sha256"), row.get("root")), (expected[0], expected[1], expected[2], str(D104_ROOT))), f"C0 input {key} identity mismatch")
        else:
            _require(isinstance(row, dict) and _same((row.get("path"), row.get("size_bytes"), row.get("sha256")), expected), f"C0 input {key} identity mismatch")
    geometry = c0.get("geometry")
    _require(isinstance(geometry, dict), "C0 geometry object missing")
    for key, value in {
        "geom_type": "Polygon",
        "component_count": 1,
        "exterior_ring_count": 1,
        "ring_count": EXPECTED_RING_COUNT,
        "hole_count": EXPECTED_HOLE_COUNT,
        "original_vertex_count": EXPECTED_ORIGINAL_VERTICES,
        "exterior_vertex_count": EXPECTED_EXTERIOR_VERTICES,
        "hole_vertex_count": EXPECTED_HOLE_VERTICES,
        "area_um2": EXPECTED_AREA_UM2,
        "bounds_um": list(EXPECTED_BOUNDS),
        "bbox_um": list(EXPECTED_BOUNDS),
        "exact_coordinate_bounds_um": list(EXPECTED_BOUNDS),
        "layer": LAYER,
        "net": NET,
        "island": ISLAND,
        "edge_flags": {"has_corner": True, "has_curve": False, "has_edge": True},
    }.items():
        _require(_same(geometry.get(key), value), f"C0 geometry {key} mismatch")
    split = c0.get("split_rule")
    _require(isinstance(split, dict), "C0 split rule missing")
    _require(
        _same(
            (split.get("step_um"), split.get("S"), split.get("split_vertex_count"), split.get("split_segment_count"), split.get("split_vertex_count_equals_segment_count"), split.get("distribution_parts_to_original_edges")),
            (SPLIT_STEP_UM, EXPECTED_PSLG_VERTICES, EXPECTED_PSLG_VERTICES, EXPECTED_PSLG_SEGMENTS, True, {str(k): v for k, v in EXPECTED_PSLG_DISTRIBUTION.items()}),
        ),
        "C0 split rule mismatch",
    )
    pslg = c0.get("pslg_returned")
    _require(isinstance(pslg, dict), "C0 PSLG receipt missing")
    for key, value in {
        "vertex_count": EXPECTED_PSLG_VERTICES,
        "segment_count": EXPECTED_PSLG_SEGMENTS,
        "split_vertex_count": EXPECTED_PSLG_VERTICES,
        "split_segment_count": EXPECTED_PSLG_SEGMENTS,
        "marker_count": EXPECTED_MARKERS,
        "marked_segment_count": EXPECTED_PSLG_SEGMENTS,
        "hole_point_count": EXPECTED_HOLES,
        "canonical_bytes": EXPECTED_PSLG_CANONICAL_BYTES,
        "canonical_sha256": EXPECTED_PSLG_CANONICAL_SHA256,
        "split_step_um": SPLIT_STEP_UM,
        "proof_complexity": "O(V+S+H)",
        "coordinate_tolerance": 4.656612873077393e-10,
        "max_returned_segment_length_um": 107.88000000000466,
        "finite_unique_vertices": True,
        "segments_valid_unique_nonzero": True,
        "marker_chains_complete": True,
        "hole_points_strictly_inside": True,
        "marker_distribution_exact": True,
    }.items():
        _require(_same(pslg.get(key), value), f"C0 PSLG {key} mismatch")
    def check_chain_counts(chain_counts: object, label: str) -> None:
        _require(isinstance(chain_counts, dict) and len(chain_counts) == EXPECTED_MARKERS, f"{label} marker chain cardinality mismatch")
        histogram: dict[int, int] = {}
        for marker in range(1, EXPECTED_MARKERS + 1):
            parts = chain_counts.get(str(marker))
            _require(type(parts) is int and parts > 0 and parts & (parts - 1) == 0, f"{label} marker chain value mismatch")
            histogram[parts] = histogram.get(parts, 0) + 1
        _require(histogram == EXPECTED_PSLG_DISTRIBUTION, f"{label} marker chain distribution mismatch")

    check_chain_counts(pslg.get("returned_marker_chain_counts"), "C0 returned")
    check_chain_counts(split.get("marker_chain_counts"), "C0 split")
    topology = c0.get("topology_floor")
    _require(isinstance(topology, dict), "C0 topology floor missing")
    for key, value in {
        "S": EXPECTED_PSLG_VERTICES,
        "H": EXPECTED_HOLE_COUNT,
        "T_floor": T_FLOOR,
        "minimum_2d_triangles": T_FLOOR,
        "Vclosed_floor": 306_492,
        "minimum_extruded_vertices": 306_492,
        "Fclosed_floor": 621_172,
        "minimum_extruded_faces": 621_172,
        "within_caps": True,
        "caps": {"planar_vertex": PLANAR_VERTEX_CAP, "planar_triangle": PLANAR_TRIANGLE_CAP, "closed_vertex": CLOSED_VERTEX_CAP, "closed_face": CLOSED_FACE_CAP},
        "source_ring_floor": {"minimum_2d_triangles": 153_172, "minimum_extruded_vertices": 298_156, "minimum_extruded_faces": 604_500},
    }.items():
        _require(_same(topology.get(key), value), f"C0 topology {key} mismatch")
    proof = c0.get("proof_scope")
    _require(isinstance(proof, dict) and _same(proof, {"pslg_boundary_chain_proof": True, "split_count_range_proven": True, "side_wall_outward_oriented_manifold_signed_volume_deferred": True}), "C0 proof scope mismatch")
    _require(c0.get("canonical_lower_bound_bytes_estimate") == 7_421_359, "C0 canonical lower bound mismatch")


def _validate_d103(d103: dict[str, object]) -> None:
    _require(_same((d103.get("program"), d103.get("version"), d103.get("status")), (PRODUCT, VERSION, "PASS")), "D103 product/status mismatch")
    raw_layers = d103.get("stackup_layers")
    _require(isinstance(raw_layers, list), "D103 stackup layer list missing")
    layers = [row for row in raw_layers if isinstance(row, dict) and _same(row.get("layer_name"), LAYER)]
    _require(len(layers) == 1, "D103 L28 cardinality mismatch")
    row = layers[0]
    _require(_same((row.get("ordinal"), row.get("raw_layer_ordinal"), row.get("layer_kind"), row.get("thickness_origin"), row.get("thickness_um"), row.get("conductivity_s_per_m")), (54, 54, "conductor", "source", THICKNESS_UM, CONDUCTIVITY_S_PER_M)), "D103 L28 identity mismatch")


def _validate_d104(d104: dict[str, object]) -> None:
    _require(_same((d104.get("program"), d104.get("version"), d104.get("status")), (PRODUCT, VERSION, "PASS")), "D104 product/status mismatch")
    linked = d104.get("d103_receipt")
    _require(isinstance(linked, dict) and _same((linked.get("path"), linked.get("sha256")), (str(D103_PATH), D103_SHA256)), "D104 linked D103 identity mismatch")
    raw_cells = d104.get("cells")
    _require(isinstance(raw_cells, list), "D104 cell list missing")
    rows = [row for row in raw_cells if isinstance(row, dict) and _same(row.get("ordinal"), ORDINAL)]
    _require(len(rows) == 1, "D104 ordinal258 cardinality mismatch")
    row = rows[0]
    _require(_same((row.get("layer"), row.get("net"), row.get("island_id")), (LAYER, NET, ISLAND)), "D104 cell identity mismatch")
    geom = row.get("geometry")
    _require(isinstance(geom, dict), "D104 cell geometry missing")
    expected = {
        "filename": "cell_0258.wkb",
        "wkb_size_bytes": SOURCE_SIZE,
        "wkb_sha256": SOURCE_SHA256,
        "component_count": 1,
        "exterior_ring_count": 1,
        "ring_count": EXPECTED_RING_COUNT,
        "hole_count": EXPECTED_HOLE_COUNT,
        "vertex_count": EXPECTED_EXTERIOR_VERTICES,
        "hole_vertex_count": EXPECTED_HOLE_VERTICES,
        "area": EXPECTED_AREA_UM2,
        "area_um2": EXPECTED_AREA_UM2,
        "bbox": list(EXPECTED_BOUNDS),
        "bbox_um": list(EXPECTED_BOUNDS),
        "curve": False,
        "edge_flags": {"has_corner": True, "has_curve": False, "has_edge": True},
        "layer": LAYER,
        "net": NET,
        "island_id": ISLAND,
        "source_asset": "geometry/0258-8e480a1536404adb.spdgeom.zlib",
        "source_asset_sha256": "8e480a1536404adbd742bd2e5c6ffd137acc52b1733f142a83b641657b7fac51",
    }
    for key, value in expected.items():
        _require(_same(geom.get(key), value), f"D104 geometry {key} mismatch")
    _require(_same((row.get("asset"), row.get("compressed_bytes")), (expected["source_asset"], 672_494)), "D104 compressed asset mismatch")


def _load_wkb_buffer(source_buffer: bytearray) -> object:
    """Load a verified bytearray; copy only for Shapely contracts requiring bytes."""
    from shapely import wkb

    try:
        return wkb.loads(source_buffer)
    except TypeError:
        immutable_buffer = bytes(source_buffer)
        try:
            return wkb.loads(immutable_buffer)
        finally:
            del immutable_buffer


def _identity_summary() -> MappingProxyType:
    return MappingProxyType(
        {
            "c0": MappingProxyType({"path": str(C0_RECEIPT), "size_bytes": C0_SIZE, "sha256": C0_SHA256}),
            "stage0": MappingProxyType({"path": str(STAGE0_PATH), "size_bytes": STAGE0_SIZE, "sha256": STAGE0_SHA256}),
            "d103": MappingProxyType({"path": str(D103_PATH), "size_bytes": D103_SIZE, "sha256": D103_SHA256}),
            "d104": MappingProxyType({"path": str(D104_PATH), "root": str(D104_ROOT), "size_bytes": D104_SIZE, "sha256": D104_SHA256}),
            "source": MappingProxyType({"path": str(SOURCE_PATH), "filename": SOURCE_PATH.name, "size_bytes": SOURCE_SIZE, "sha256": SOURCE_SHA256}),
            "wheel": MappingProxyType({"path": str(WHEEL_PATH), "filename": WHEEL_NAME, "size_bytes": WHEEL_SIZE, "sha256": WHEEL_SHA256}),
            "cell": MappingProxyType({"ordinal": ORDINAL, "layer": LAYER, "net": NET, "island": ISLAND, "thickness_um": THICKNESS_UM, "area_um2": EXPECTED_AREA_UM2, "bounds_um": EXPECTED_BOUNDS, "ring_count": EXPECTED_RING_COUNT, "hole_count": EXPECTED_HOLE_COUNT, "original_vertex_count": EXPECTED_ORIGINAL_VERTICES}),
        }
    )


def validate_frozen_inputs(
    *,
    c0_path: Path = C0_RECEIPT,
    d103_path: Path = D103_PATH,
    d104_path: Path = D104_PATH,
    source_path: Path = SOURCE_PATH,
    stage0_path: Path = STAGE0_PATH,
    wheel_path: Path = WHEEL_PATH,
) -> tuple[object, MappingProxyType]:
    """Validate the frozen C0/D103/D104/source/Stage0/wheel graph once."""
    supplied = (Path(c0_path), Path(d103_path), Path(d104_path), Path(source_path), Path(stage0_path), Path(wheel_path))
    expected_paths = (C0_RECEIPT, D103_PATH, D104_PATH, SOURCE_PATH, STAGE0_PATH, WHEEL_PATH)
    for path, expected in zip(supplied, expected_paths):
        _canonical_path(path, expected)
    _canonical_path(D104_ROOT, D104_ROOT)
    c0_buffer = _read_verified(Path(c0_path), C0_SIZE, C0_SHA256)
    c0 = _strict_json(c0_buffer)
    del c0_buffer
    _require(isinstance(c0, dict), "C0 root must be an object")
    _validate_c0(c0)
    del c0
    d103_buffer = _read_verified(Path(d103_path), D103_SIZE, D103_SHA256)
    d103 = _strict_json(d103_buffer)
    del d103_buffer
    _require(isinstance(d103, dict), "D103 root must be an object")
    _validate_d103(d103)
    del d103
    d104_buffer = _read_verified(Path(d104_path), D104_SIZE, D104_SHA256)
    d104 = _strict_json(d104_buffer)
    del d104_buffer
    _require(isinstance(d104, dict), "D104 root must be an object")
    _validate_d104(d104)
    del d104
    source_buffer = _read_verified(Path(source_path), SOURCE_SIZE, SOURCE_SHA256)
    try:
        shape = _load_wkb_buffer(source_buffer)
    except Exception as exc:
        del source_buffer
        raise Refusal("source WKB load failed") from exc
    del source_buffer
    stage0_buffer = _read_verified(Path(stage0_path), STAGE0_SIZE, STAGE0_SHA256)
    del stage0_buffer
    wheel_buffer = _read_verified(Path(wheel_path), WHEEL_SIZE, WHEEL_SHA256)
    del wheel_buffer
    _require(shape.geom_type == "Polygon" and not shape.is_empty and shape.is_valid and math.isfinite(shape.area) and shape.area > 0, "source is not one valid Polygon")
    rings = (shape.exterior, *shape.interiors)
    _require(len(rings) == EXPECTED_RING_COUNT and len(shape.exterior.coords) - 1 == EXPECTED_EXTERIOR_VERTICES and sum(len(r.coords) - 1 for r in shape.interiors) == EXPECTED_HOLE_VERTICES, "source ring census mismatch")
    _require(shape.area == EXPECTED_AREA_UM2 and tuple(shape.bounds) == EXPECTED_BOUNDS, "source geometry identity mismatch")
    del rings
    return shape, _identity_summary()


def _ring_coords(ring: object) -> np.ndarray:
    coords = np.asarray(getattr(ring, "coords"), dtype=np.float64)
    _require(coords.ndim == 2 and coords.shape[1] == 2 and coords.shape[0] >= 4, "invalid ring coordinate shape")
    open_coords = coords[:-1]
    _require(np.isfinite(open_coords).all() and np.unique(open_coords, axis=0).shape[0] == open_coords.shape[0], "invalid or duplicate ring")
    return open_coords


def _edge_parts(a: np.ndarray, b: np.ndarray, step: float) -> int:
    length = math.hypot(float(b[0] - a[0]), float(b[1] - a[1]))
    _require(math.isfinite(length) and length > 0, "invalid source edge")
    parts = 1
    while parts * step < length:
        parts *= 2
        _require(parts <= PLANAR_VERTEX_CAP, "split PSLG cap exceeded")
    return parts


def build_pslg_arrays(shape: object, thickness: float = THICKNESS_UM) -> dict[str, np.ndarray]:
    """Build Stage0-compatible PSLG arrays with two-pass owned storage."""
    _require(getattr(shape, "geom_type", None) == "Polygon" and not shape.is_empty and shape.is_valid, "only a valid Polygon is accepted")
    _require(math.isfinite(float(thickness)) and float(thickness) > 0, "invalid thickness")
    step = 4.0 * float(thickness)
    rings = (shape.exterior, *shape.interiors)
    ring_coords = tuple(_ring_coords(ring) for ring in rings)
    original_count = sum(int(coords.shape[0]) for coords in ring_coords)
    _require(original_count > 0, "empty polygon rings")

    # Pass one computes the exact split allocation without storing a list mesh.
    split_count = 0
    for coords in ring_coords:
        n = int(coords.shape[0])
        for i in range(n):
            split_count += _edge_parts(coords[i], coords[(i + 1) % n], step)
    _require(split_count <= PLANAR_VERTEX_CAP, "split PSLG vertex cap exceeded")

    vertices = np.empty((split_count, 2), dtype=np.float64, order="C")
    segments = np.empty((split_count, 2), dtype=np.int32, order="C")
    markers = np.empty((split_count, 1), dtype=np.int32, order="C")
    holes = np.empty((len(ring_coords) - 1, 2), dtype=np.float64, order="C")
    vertex_cursor = 0
    segment_cursor = 0
    marker = 1
    for coords in ring_coords:
        n = int(coords.shape[0])
        ring_start = vertex_cursor
        previous = ring_start
        vertices[vertex_cursor] = coords[0]
        vertex_cursor += 1
        for i in range(n):
            a = coords[i]
            b = coords[(i + 1) % n]
            parts = _edge_parts(a, b, step)
            edge_marker = marker + i
            for j in range(1, parts + 1):
                if j == parts:
                    if i == n - 1:
                        current = ring_start
                    else:
                        current = vertex_cursor
                        vertices[current] = b
                        vertex_cursor += 1
                else:
                    fraction = j / parts
                    current = vertex_cursor
                    vertices[current] = (float(a[0] + (b[0] - a[0]) * fraction), float(a[1] + (b[1] - a[1]) * fraction))
                    vertex_cursor += 1
                segments[segment_cursor] = (previous, current)
                markers[segment_cursor, 0] = edge_marker
                segment_cursor += 1
                previous = current
        marker += n
    _require(vertex_cursor == split_count and segment_cursor == split_count, "two-pass PSLG allocation mismatch")

    if holes.shape[0]:
        from shapely.geometry import Polygon

        for index, ring in enumerate(rings[1:]):
            point = Polygon(ring).representative_point()
            holes[index] = (float(point.x), float(point.y))
            del point
    del ring_coords, rings
    return {"vertices": vertices, "segments": segments, "segment_markers": markers, "holes": holes}


def _array_contract(array: object, dtype: np.dtype, ndim: int, shape_tail: tuple[int, ...], name: str) -> np.ndarray:
    _require(type(array) is np.ndarray, f"{name} must be an ndarray")
    result = array
    _require(result.dtype == dtype and result.ndim == ndim and result.shape[1:] == shape_tail, f"{name} dtype/rank/shape mismatch")
    _require(bool(result.flags.c_contiguous and result.flags.aligned and result.flags.owndata), f"{name} storage flags mismatch")
    return result


def _validate_pslg_arrays(pslg: object, shape: object | None = None, require_frozen: bool = False, thickness: float = THICKNESS_UM) -> dict[str, object]:
    _require(isinstance(pslg, dict) and frozenset(pslg) == _PSLG_KEYS, "PSLG key set mismatch")
    vertices = _array_contract(pslg["vertices"], np.dtype(np.float64), 2, (2,), "PSLG vertices")
    segments = _array_contract(pslg["segments"], np.dtype(np.int32), 2, (2,), "PSLG segments")
    markers = _array_contract(pslg["segment_markers"], np.dtype(np.int32), 2, (1,), "PSLG markers")
    holes = _array_contract(pslg["holes"], np.dtype(np.float64), 2, (2,), "PSLG holes")
    _require(vertices.shape[0] == segments.shape[0] == markers.shape[0], "PSLG count mismatch")
    _require(0 < vertices.shape[0] <= PLANAR_VERTEX_CAP and 0 < segments.shape[0] <= PLANAR_VERTEX_CAP, "PSLG planar cap mismatch")
    _require(np.isfinite(vertices).all() and np.isfinite(holes).all(), "PSLG nonfinite coordinate")
    if segments.shape[0]:
        _require(int(segments.min()) >= 0 and int(segments.max()) < vertices.shape[0], "PSLG index bounds mismatch")
        _require(bool(np.all(segments[:, 0] != segments[:, 1])), "PSLG zero-length index")
        used = np.bincount(segments.reshape(-1), minlength=vertices.shape[0])
        _require(bool(np.all(used > 0)), "PSLG contains unused vertex")
        del used
    _require(int(markers.min()) > 0, "PSLG marker bounds mismatch")
    if require_frozen:
        _require(_same((vertices.shape[0], segments.shape[0], markers.shape[0], holes.shape[0]), (EXPECTED_PSLG_VERTICES, EXPECTED_PSLG_SEGMENTS, EXPECTED_PSLG_SEGMENTS, EXPECTED_HOLES)), "frozen PSLG count mismatch")
        _require(int(markers.max()) == EXPECTED_MARKERS, "frozen PSLG marker maximum mismatch")
        histogram = np.bincount(markers[:, 0], minlength=EXPECTED_MARKERS + 1)
        _require(int(np.count_nonzero(histogram[1:])) == EXPECTED_MARKERS, "frozen PSLG marker set mismatch")
        observed = {int(count): int(np.count_nonzero(histogram == count)) for count in np.unique(histogram[1:])}
        _require(observed == EXPECTED_PSLG_DISTRIBUTION, "frozen PSLG marker distribution mismatch")
        del histogram
        _require(np.unique(vertices, axis=0).shape[0] == vertices.shape[0], "frozen PSLG duplicate vertex")
        normalized = np.sort(segments, axis=1)
        _require(np.unique(normalized, axis=0).shape[0] == segments.shape[0], "frozen PSLG duplicate segment")
        del normalized
    if shape is not None:
        _validate_marker_chains(shape, pslg, thickness)
    return {"vertex_count": int(vertices.shape[0]), "segment_count": int(segments.shape[0]), "marker_count": int(np.unique(markers).shape[0]), "hole_count": int(holes.shape[0])}


def _validate_marker_chains(shape: object, pslg: dict[str, np.ndarray], thickness: float = THICKNESS_UM) -> None:
    vertices, segments, markers = pslg["vertices"], pslg["segments"], pslg["segment_markers"][:, 0]
    rings = tuple(_ring_coords(ring) for ring in (shape.exterior, *shape.interiors))
    cursor = 0
    marker = 1
    for coords in rings:
        n = int(coords.shape[0])
        for i in range(n):
            a, b = coords[i], coords[(i + 1) % n]
            parts = _edge_parts(a, b, 4.0 * float(thickness))
            start = cursor
            end = cursor + parts
            _require(end <= segments.shape[0] and np.all(markers[start:end] == marker + i), "marker chain ordering mismatch")
            _require(np.array_equal(vertices[segments[start, 0]], a) and np.array_equal(vertices[segments[end - 1, 1]], b), "marker chain endpoint mismatch")
            if parts > 1:
                _require(bool(np.all(segments[start + 1 : end, 0] == segments[start : end - 1, 1])), "marker chain adjacency mismatch")
            for row in range(start, end):
                u, v = segments[row]
                length = math.hypot(float(vertices[v, 0] - vertices[u, 0]), float(vertices[v, 1] - vertices[u, 1]))
                _require(math.isfinite(length) and length > 0 and length <= 4.0 * float(thickness) + 1e-7, "marker chain segment length mismatch")
            cursor = end
        marker += n
    expected_cursor = marker == EXPECTED_ORIGINAL_VERTICES + 1 if len(rings) == EXPECTED_RING_COUNT else cursor == segments.shape[0]
    _require(cursor == segments.shape[0] and expected_cursor, "marker chain cursor mismatch")
    if pslg["holes"].shape[0]:
        from shapely.geometry import Point, Polygon

        for ring, point in zip((shape.interiors), pslg["holes"]):
            _require(Polygon(ring).contains(Point(float(point[0]), float(point[1]))), "hole point is not strictly inside")


def _clean(value: float) -> float:
    return 0.0 if float(value) == 0.0 else float(value)


def _emitter(sink: BinaryIO | None, byte_cap: int):
    _require(type(byte_cap) is int and byte_cap > 0, "invalid canonical byte cap")
    hasher = hashlib.sha256()
    byte_count = 0

    def emit(line: str) -> None:
        nonlocal byte_count
        data = line.encode("ascii")
        _require(byte_count + len(data) <= byte_cap, "canonical byte cap exceeded")
        hasher.update(data)
        byte_count += len(data)
        if sink is not None:
            sink.write(data)

    def finish() -> tuple[int, str]:
        return byte_count, hasher.hexdigest()

    return emit, finish


def stream_pslg_canonical(
    pslg: dict[str, np.ndarray],
    sink: BinaryIO | None = None,
    require_frozen: bool = False,
    byte_cap: int = FUTURE_CANONICAL_BYTES_CAP,
) -> tuple[int, str]:
    """Hash/write canonical PSLG lines incrementally under a hard byte cap."""
    _validate_pslg_arrays(pslg)
    emit, finish = _emitter(sink, byte_cap)
    emit(f"{PRODUCT} v{VERSION} Stage2A C0 PSLG\n")
    vertices, segments, markers, holes = pslg["vertices"], pslg["segments"], pslg["segment_markers"], pslg["holes"]
    for index in range(vertices.shape[0]):
        emit(f"v {index} {_clean(vertices[index, 0]):.17g} {_clean(vertices[index, 1]):.17g}\n")
    for index in range(segments.shape[0]):
        emit(f"s {index} {int(segments[index, 0])} {int(segments[index, 1])}\n")
    for index in range(markers.shape[0]):
        emit(f"m {index} {int(markers[index, 0])}\n")
    for index in range(holes.shape[0]):
        emit(f"h {_clean(holes[index, 0]):.17g} {_clean(holes[index, 1]):.17g}\n")
    result = finish()
    if require_frozen:
        _require(result == (EXPECTED_PSLG_CANONICAL_BYTES, EXPECTED_PSLG_CANONICAL_SHA256), "frozen PSLG canonical identity mismatch")
    return result


def recompute_c0_pslg_canonical(pslg: dict[str, np.ndarray]) -> tuple[int, str]:
    return stream_pslg_canonical(pslg, require_frozen=True)


def _stream_file_digest(path: Path, byte_cap: int | None = None, chunk_size: int = READ_CHUNK_BYTES) -> tuple[int, str]:
    _require(path.is_file() and not _is_reparse(path), "canonical readback path mismatch")
    chunk_size = _bounded_chunk_size(chunk_size, "file digest chunk size")
    hasher = hashlib.sha256()
    count = 0
    scratch = bytearray(chunk_size)
    view = memoryview(scratch)
    with path.open("rb") as stream:
        try:
            while True:
                read_count = stream.readinto(view)
                _require(type(read_count) is int and 0 <= read_count <= chunk_size, "canonical readback read failed")
                if read_count == 0:
                    break
                digest_block = view[:read_count]
                try:
                    count += read_count
                    if byte_cap is not None:
                        _require(count <= byte_cap, "canonical readback byte cap exceeded")
                    hasher.update(digest_block)
                finally:
                    digest_block.release()
        finally:
            view.release()
    return count, hasher.hexdigest()


def _atomic_stream_write(path: Path, stream_function, byte_cap: int) -> tuple[int, str]:
    path = Path(path)
    _require(path.parent.exists() and not _is_reparse(path.parent), "canonical output parent unavailable")
    _require(not path.exists() and not path.is_symlink(), "canonical output already exists")
    temp = path.with_name(path.name + ".tmp")
    _require(not temp.exists() and not temp.is_symlink(), "canonical temporary output already exists")
    try:
        with temp.open("xb") as stream:
            expected = stream_function(stream, byte_cap)
            stream.flush()
            os.fsync(stream.fileno())
        observed = _stream_file_digest(temp, byte_cap)
        _require(observed == expected, "canonical streamed readback mismatch")
        _require(not path.exists() and not path.is_symlink(), "canonical output appeared during write")
        os.rename(temp, path)
        return observed
    except Exception:
        try:
            if temp.exists() and not temp.is_symlink():
                temp.unlink()
        except OSError:
            pass
        raise


def write_streamed_canonical(
    path: Path,
    pslg: dict[str, np.ndarray],
    require_frozen: bool = False,
    byte_cap: int = FUTURE_CANONICAL_BYTES_CAP,
) -> tuple[int, str]:
    return _atomic_stream_write(path, lambda sink, cap: stream_pslg_canonical(pslg, sink=sink, require_frozen=require_frozen, byte_cap=cap), byte_cap)


def readback_canonical(path: Path, expected: tuple[int, str] | None = None, byte_cap: int = FUTURE_CANONICAL_BYTES_CAP) -> tuple[int, str]:
    observed = _stream_file_digest(Path(path), byte_cap)
    if expected is not None:
        _require(observed == expected, "canonical readback identity mismatch")
    return observed


def stream_future_result_canonical(
    result: dict[str, np.ndarray],
    sink: BinaryIO | None = None,
    byte_cap: int = FUTURE_CANONICAL_BYTES_CAP,
    expected_holes: np.ndarray | None = None,
) -> tuple[int, str]:
    """Stream/hash all six future result arrays under a hard byte cap."""
    validate_future_triangle_result(result, expected_holes=expected_holes)
    emit, finish = _emitter(sink, byte_cap)
    emit(f"{PRODUCT} v{VERSION} Stage2A C1 future result\n")
    vertices = result["vertices"]
    vertex_markers = result["vertex_markers"]
    triangles = result["triangles"]
    segments = result["segments"]
    segment_markers = result["segment_markers"]
    for index in range(vertices.shape[0]):
        emit(f"v {index} {_clean(vertices[index, 0]):.17g} {_clean(vertices[index, 1]):.17g}\n")
    for index in range(vertex_markers.shape[0]):
        emit(f"vm {index} {int(vertex_markers[index, 0])}\n")
    for index in range(triangles.shape[0]):
        emit(f"t {index} {int(triangles[index, 0])} {int(triangles[index, 1])} {int(triangles[index, 2])}\n")
    for index in range(segments.shape[0]):
        emit(f"s {index} {int(segments[index, 0])} {int(segments[index, 1])}\n")
    for index in range(segment_markers.shape[0]):
        emit(f"m {index} {int(segment_markers[index, 0])}\n")
    holes = result["holes"]
    for index in range(holes.shape[0]):
        emit(f"h {float(holes[index, 0]):.17g} {float(holes[index, 1]):.17g}\n")
    return finish()


def write_future_result_canonical(
    path: Path,
    result: dict[str, np.ndarray],
    byte_cap: int = FUTURE_CANONICAL_BYTES_CAP,
    expected_holes: np.ndarray | None = None,
) -> tuple[int, str]:
    return _atomic_stream_write(path, lambda sink, cap: stream_future_result_canonical(result, sink=sink, byte_cap=cap, expected_holes=expected_holes), byte_cap)


def validate_future_triangle_result(result: object, expected_holes: np.ndarray | None = None) -> dict[str, int]:
    """Apply the immediate, exact future-result ndarray contract."""
    _require(isinstance(result, dict) and frozenset(result) == _FUTURE_RESULT_KEYS, "future result key set mismatch")
    vertices = _array_contract(result["vertices"], np.dtype(np.float64), 2, (2,), "future vertices")
    vertex_markers = _array_contract(result["vertex_markers"], np.dtype(np.int32), 2, (1,), "future vertex markers")
    triangles = _array_contract(result["triangles"], np.dtype(np.int32), 2, (3,), "future triangles")
    segments = _array_contract(result["segments"], np.dtype(np.int32), 2, (2,), "future segments")
    markers = _array_contract(result["segment_markers"], np.dtype(np.int32), 2, (1,), "future segment markers")
    holes = _array_contract(result["holes"], np.dtype("<f8"), 2, (2,), "future holes")
    _require(holes.base is None and holes.shape[0] == EXPECTED_HOLES, "future hole shape/storage mismatch")
    v_count, t_count, b_count = vertices.shape[0], triangles.shape[0], segments.shape[0]
    _require(
        EXPECTED_PSLG_VERTICES <= v_count <= PLANAR_VERTEX_CAP
        and T_FLOOR <= t_count <= PLANAR_TRIANGLE_CAP
        and EXPECTED_PSLG_SEGMENTS <= b_count <= v_count,
        "future result count floor/cap mismatch",
    )
    _require(vertex_markers.shape[0] == v_count, "future vertex marker count mismatch")
    _require(markers.shape[0] == b_count, "future marker count mismatch")
    _require(np.isfinite(vertices).all(), "future vertices contain nonfinite values")
    _require(int(triangles.min()) >= 0 and int(triangles.max()) < v_count, "future triangle index bounds mismatch")
    _require(int(segments.min()) >= 0 and int(segments.max()) < v_count, "future segment index bounds mismatch")
    _require(bool(np.all(triangles[:, 0] != triangles[:, 1]) and np.all(triangles[:, 1] != triangles[:, 2]) and np.all(triangles[:, 0] != triangles[:, 2])), "future zero-area index triangle")
    _require(bool(np.all(segments[:, 0] != segments[:, 1])), "future zero-length index segment")
    _require(int(vertex_markers.min()) >= 0, "future vertex marker bounds mismatch")
    _require(int(markers.min()) > 0, "future marker bounds mismatch")
    _require(bool(np.isfinite(holes).all()), "future holes contain nonfinite values")
    if expected_holes is not None:
        frozen_holes = _array_contract(expected_holes, np.dtype("<f8"), 2, (2,), "expected future holes")
        _require(frozen_holes.shape[0] == EXPECTED_HOLES and bool(np.isfinite(frozen_holes).all()), "expected future holes contract mismatch")
        _require(np.array_equal(holes, frozen_holes), "future holes do not match frozen input")
    return {"vertex_count": int(v_count), "vertex_marker_count": int(vertex_markers.shape[0]), "triangle_count": int(t_count), "segment_count": int(b_count), "marker_count": int(markers.shape[0]), "hole_count": int(holes.shape[0])}


def _oriented_boundary_records(result: object, *, chunk_size: int = 8192) -> np.ndarray:
    """Return the bounded C1 oriented boundary certificate slice."""
    counts = validate_future_triangle_result(result)
    chunk_size = _bounded_chunk_size(chunk_size, "boundary chunk size")
    triangles = result["triangles"]
    segments = result["segments"]
    markers = result["segment_markers"][:, 0]
    vertex_count = counts["vertex_count"]
    triangle_count = counts["triangle_count"]
    segment_count = counts["segment_count"]
    _require(vertex_count < (1 << 19), "future vertex count exceeds boundary encoding")
    for marker in markers:
        _require(0 < int(marker) < (1 << 18), "future segment marker exceeds boundary encoding")

    triangle_edges = np.empty(triangle_count * 3, dtype=np.uint64)
    left = np.empty(chunk_size, dtype=np.uint64)
    right = np.empty(chunk_size, dtype=np.uint64)
    scratch = np.empty(chunk_size, dtype=np.uint64)
    edge_cursor = 0
    for start in range(0, triangle_count, chunk_size):
        stop = min(start + chunk_size, triangle_count)
        tri = triangles[start:stop]
        width = stop - start
        for first_column, second_column in ((0, 1), (1, 2), (2, 0)):
            left[:width] = tri[:, first_column]
            right[:width] = tri[:, second_column]
            destination = triangle_edges[edge_cursor : edge_cursor + width]
            np.minimum(left[:width], right[:width], out=scratch[:width])
            np.left_shift(scratch[:width], 19, out=destination)
            np.maximum(left[:width], right[:width], out=scratch[:width])
            np.bitwise_or(destination, scratch[:width], out=destination)
            np.left_shift(destination, 1, out=destination)
            np.greater(left[:width], right[:width], out=scratch[:width])
            np.bitwise_or(destination, scratch[:width], out=destination)
            edge_cursor += width
    triangle_edges.sort()

    boundary_count = 0
    cursor = 0
    while cursor < triangle_edges.size:
        first = int(triangle_edges[cursor])
        undirected = first >> 1
        cursor += 1
        if cursor < triangle_edges.size and (int(triangle_edges[cursor]) >> 1) == undirected:
            second = int(triangle_edges[cursor])
            cursor += 1
            _require((first ^ second) & 1 == 1, "future shared edge direction mismatch")
            _require(cursor == triangle_edges.size or (int(triangle_edges[cursor]) >> 1) != undirected, "future edge incidence exceeds two")
        else:
            triangle_edges[boundary_count] = np.uint64(first)
            boundary_count += 1
    _require(boundary_count == segment_count, "future boundary/segment count mismatch")

    boundary_records = np.empty(segment_count, dtype=np.uint64)
    for start in range(0, segment_count, chunk_size):
        stop = min(start + chunk_size, segment_count)
        width = stop - start
        seg = segments[start:stop]
        left[:width] = seg[:, 0]
        right[:width] = seg[:, 1]
        destination = boundary_records[start:stop]
        np.minimum(left[:width], right[:width], out=scratch[:width])
        np.left_shift(scratch[:width], 19, out=destination)
        np.maximum(left[:width], right[:width], out=scratch[:width])
        np.bitwise_or(destination, scratch[:width], out=destination)
        np.left_shift(destination, 18, out=destination)
        left[:width] = markers[start:stop]
        np.bitwise_or(destination, left[:width], out=destination)
    boundary_records.sort()

    previous_undirected = -1
    for index in range(segment_count):
        record = int(boundary_records[index])
        undirected = record >> 18
        _require(undirected > previous_undirected, "future duplicate or non-unique segment")
        previous_undirected = undirected
        oriented = int(triangle_edges[index])
        _require(oriented >> 1 == undirected, "future boundary segment set mismatch")
        marker = record & ((1 << 18) - 1)
        boundary_records[index] = np.uint64((oriented << 18) | marker)
    _require(
        boundary_records.dtype == np.dtype(np.uint64)
        and boundary_records.ndim == 1
        and boundary_records.flags.c_contiguous
        and boundary_records.flags.aligned
        and boundary_records.flags.owndata
        and boundary_records.base is None,
        "future boundary records storage mismatch",
    )
    return boundary_records


def summarize_positive_area_streaming(result: object, polygon: object | None = None, chunk_size: int = 8192) -> dict[str, object]:
    """Summarize finite positive triangle areas with bounded chunks; C1 remains incomplete."""
    counts = validate_future_triangle_result(result)
    chunk_size = _bounded_chunk_size(chunk_size, "certification chunk size")
    vertices = result["vertices"]
    triangles = result["triangles"]
    used = np.zeros(vertices.shape[0], dtype=np.bool_)
    gather_a = np.empty((chunk_size, 2), dtype=np.float64)
    gather_b = np.empty((chunk_size, 2), dtype=np.float64)
    gather_c = np.empty((chunk_size, 2), dtype=np.float64)
    dx_ab = np.empty(chunk_size, dtype=np.float64)
    dy_ab = np.empty(chunk_size, dtype=np.float64)
    dx_ac = np.empty(chunk_size, dtype=np.float64)
    dy_ac = np.empty(chunk_size, dtype=np.float64)
    cross = np.empty(chunk_size, dtype=np.float64)
    areas = np.empty(chunk_size, dtype=np.float64)
    valid = np.empty(chunk_size, dtype=np.bool_)
    area_sum = 0.0
    minimum_area = math.inf
    for start in range(0, triangles.shape[0], chunk_size):
        tri = triangles[start : start + chunk_size]
        width = tri.shape[0]
        np.take(vertices, tri[:, 0], axis=0, out=gather_a[:width])
        np.take(vertices, tri[:, 1], axis=0, out=gather_b[:width])
        np.take(vertices, tri[:, 2], axis=0, out=gather_c[:width])
        np.subtract(gather_b[:width, 0], gather_a[:width, 0], out=dx_ab[:width])
        np.subtract(gather_b[:width, 1], gather_a[:width, 1], out=dy_ab[:width])
        np.subtract(gather_c[:width, 0], gather_a[:width, 0], out=dx_ac[:width])
        np.subtract(gather_c[:width, 1], gather_a[:width, 1], out=dy_ac[:width])
        np.multiply(dx_ab[:width], dy_ac[:width], out=cross[:width])
        np.multiply(dy_ab[:width], dx_ac[:width], out=areas[:width])
        np.subtract(cross[:width], areas[:width], out=cross[:width])
        np.isfinite(cross[:width], out=valid[:width])
        _require(bool(np.all(valid[:width])), "future 2-D triangle orientation/area mismatch")
        np.greater(cross[:width], 0, out=valid[:width])
        _require(bool(np.all(valid[:width])), "future 2-D triangle orientation/area mismatch")
        np.multiply(cross[:width], 0.5, out=areas[:width])
        area_sum += float(np.sum(areas[:width], dtype=np.float64))
        minimum_area = min(minimum_area, float(np.min(areas[:width])))
        used[tri.reshape(-1)] = True
    _require(bool(np.all(used)), "future 2-D result has unused vertices")
    del used, gather_a, gather_b, gather_c, dx_ab, dy_ab, dx_ac, dy_ac, cross, areas, valid
    checks_performed = (
        "future_result_array_contract",
        "finite_vertex_coordinates",
        "triangle_index_bounds",
        "positive_signed_triangle_area",
        "referenced_vertex_scan",
        "streamed_area_sum",
    )
    checks_deferred = (
        "geometric_coverage",
        "triangle_overlap_and_duplicate_analysis",
        "mesh_connectivity_and_incidence",
        "boundary_and_hole_preservation",
        "Euler_or_manifold_topology",
        "angle_and_aspect_quality",
    )
    result_summary: dict[str, object] = {
        **counts,
        "triangle_area_sum_um2": area_sum,
        "minimum_triangle_area_um2": minimum_area,
        "bounded_streaming": True,
        "area_sum_consistency": None,
        "coverage_certified": False,
        "certification_status": CERTIFICATION_STATUS,
        "full_2d_cert_status": FULL_2D_CERT_STATUS,
        "checks_performed": checks_performed,
        "checks_deferred": checks_deferred,
    }
    if polygon is not None:
        _require(getattr(polygon, "geom_type", None) == "Polygon" and math.isfinite(float(polygon.area)) and float(polygon.area) > 0, "certification polygon mismatch")
        tolerance = max(1e-7, float(polygon.area) * 1e-12)
        result_summary.update({"source_area_um2": float(polygon.area), "area_sum_tolerance": tolerance, "area_sum_consistency": abs(area_sum - float(polygon.area)) <= tolerance, "checks_performed": checks_performed + ("source_area_sum_comparison",)})
    return result_summary


def _reject_duplicate_coordinate_records(vertices: np.ndarray) -> None:
    """Reject numeric coordinate duplicates, including signed zero."""
    records = np.empty(
        vertices.shape[0],
        dtype=np.dtype([("x", "<f8"), ("y", "<f8")]),
    )
    records["x"] = vertices[:, 0]
    records["y"] = vertices[:, 1]
    records["x"][records["x"] == 0.0] = 0.0
    records["y"][records["y"] == 0.0] = 0.0
    records.sort(order=("x", "y"), kind="heapsort")
    for index in range(1, records.shape[0]):
        _require(
            records[index]["x"] != records[index - 1]["x"]
            or records[index]["y"] != records[index - 1]["y"],
            "future duplicate numeric coordinate",
        )


def _reject_duplicate_face_keys(triangles: np.ndarray) -> None:
    """Reject duplicate unordered triangle faces using an owning sort buffer."""
    keys = np.empty(
        triangles.shape[0],
        dtype=np.dtype([("a", "<u8"), ("b", "<u8"), ("c", "<u8")]),
    )
    first = triangles[:, 0].astype(np.uint64, copy=False)
    second = triangles[:, 1].astype(np.uint64, copy=False)
    third = triangles[:, 2].astype(np.uint64, copy=False)
    keys["a"] = np.minimum(np.minimum(first, second), third)
    keys["c"] = np.maximum(np.maximum(first, second), third)
    keys["b"] = first + second + third - keys["a"] - keys["c"]
    keys.sort(order=("a", "b", "c"), kind="heapsort")
    for index in range(1, keys.shape[0]):
        _require(
            keys[index]["a"] != keys[index - 1]["a"]
            or keys[index]["b"] != keys[index - 1]["b"]
            or keys[index]["c"] != keys[index - 1]["c"],
            "future duplicate unordered triangle face",
        )


def _boundary_adjacency(starts: np.ndarray, ends: np.ndarray, vertex_count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build one-in/one-out maps from induced boundary endpoints."""
    _require(type(starts) is np.ndarray and type(ends) is np.ndarray and starts.ndim == 1 and ends.shape == starts.shape, "boundary endpoint arrays mismatch")
    segment_count = int(starts.shape[0])
    out_records = np.empty(
        segment_count,
        dtype=np.dtype([("key", "<u8"), ("other", "<u8"), ("row", "<u8")]),
    )
    for row in range(segment_count):
        out_records[row] = (int(starts[row]), int(ends[row]), row)
    out_records.sort(order="key", kind="heapsort")
    next_vertex = np.full(vertex_count, -1, dtype=np.int64)
    next_edge = np.full(vertex_count, -1, dtype=np.int64)
    out_count = np.zeros(vertex_count, dtype=np.int32)
    cursor = 0
    while cursor < segment_count:
        key = int(out_records[cursor]["key"])
        end = cursor + 1
        while end < segment_count and int(out_records[end]["key"]) == key:
            end += 1
        _require(end - cursor == 1, "boundary vertex has multiple outgoing edges")
        next_vertex[key] = int(out_records[cursor]["other"])
        next_edge[key] = int(out_records[cursor]["row"])
        out_count[key] = 1
        cursor = end

    in_records = np.empty(
        segment_count,
        dtype=np.dtype([("key", "<u8"), ("other", "<u8"), ("row", "<u8")]),
    )
    for row in range(segment_count):
        in_records[row] = (int(ends[row]), int(starts[row]), row)
    in_records.sort(order="key", kind="heapsort")
    previous_vertex = np.full(vertex_count, -1, dtype=np.int64)
    in_count = np.zeros(vertex_count, dtype=np.int32)
    cursor = 0
    while cursor < segment_count:
        key = int(in_records[cursor]["key"])
        end = cursor + 1
        while end < segment_count and int(in_records[end]["key"]) == key:
            end += 1
        _require(end - cursor == 1, "boundary vertex has multiple incoming edges")
        previous_vertex[key] = int(in_records[cursor]["other"])
        in_count[key] = 1
        cursor = end
    for vertex in range(vertex_count):
        _require(int(out_count[vertex]) == int(in_count[vertex]), "boundary vertex in/out mismatch")
    return next_vertex, previous_vertex, next_edge, out_count


def _boundary_cycle_evidence(
    vertices: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, tuple[tuple[float, int], ...]]:
    next_vertex, previous_vertex, next_edge, out_count = _boundary_adjacency(starts, ends, int(vertices.shape[0]))
    visited_vertices = np.zeros(vertices.shape[0], dtype=np.bool_)
    visited_edges = np.zeros(starts.shape[0], dtype=np.bool_)
    cycles: list[tuple[float, int]] = []
    for start in range(vertices.shape[0]):
        if not out_count[start] or visited_vertices[start]:
            continue
        current = start
        area_twice = 0.0
        length = 0
        while not visited_vertices[current]:
            visited_vertices[current] = True
            edge = int(next_edge[current])
            _require(edge >= 0 and not visited_edges[edge], "boundary cycle edge lookup mismatch")
            visited_edges[edge] = True
            following = int(next_vertex[current])
            _require(following >= 0, "boundary cycle successor missing")
            area_twice += float(vertices[current, 0]) * float(vertices[following, 1]) - float(vertices[following, 0]) * float(vertices[current, 1])
            current = following
            length += 1
            _require(length <= starts.shape[0], "boundary cycle does not close")
        _require(current == start, "boundary cycle is not closed")
        cycles.append((area_twice * 0.5, length))
    _require(int(np.count_nonzero(visited_edges)) == starts.shape[0], "boundary cycle leaves an edge unused")
    return next_vertex, previous_vertex, next_edge, out_count, tuple(cycles)


def _edge_topology(result: dict[str, np.ndarray], vertex_count: int, triangle_count: int) -> tuple[int, int]:
    """Check edge incidence/directions and prove connected triangle dual."""
    edge_count_total = triangle_count * 3
    records = np.empty(
        edge_count_total,
        dtype=np.dtype([("key", "<u8"), ("face", "<u8"), ("direction", "u1"), ("twin", "<u8")], align=True),
    )
    triangles = result["triangles"]
    cursor = 0
    for face in range(triangle_count):
        a, b, c = (int(value) for value in triangles[face])
        for left, right in ((a, b), (b, c), (c, a)):
            minimum, maximum = sorted((left, right))
            records[cursor] = ((minimum << 19) | maximum, face, int(left > right), np.iinfo(np.uint64).max)
            cursor += 1
    records.sort(order="key", kind="heapsort")
    parent = np.arange(triangle_count, dtype=np.int64)

    def find(face: int) -> int:
        root = face
        while int(parent[root]) != root:
            root = int(parent[root])
        while int(parent[face]) != face:
            following = int(parent[face])
            parent[face] = root
            face = following
        return root

    boundary_edges = 0
    unique_edges = 0
    cursor = 0
    while cursor < edge_count_total:
        key = int(records[cursor]["key"])
        end = cursor + 1
        while end < edge_count_total and int(records[end]["key"]) == key:
            end += 1
        incidence = end - cursor
        _require(incidence in (1, 2), "triangle edge incidence is not one or two")
        unique_edges += 1
        if incidence == 1:
            boundary_edges += 1
        else:
            _require(int(records[cursor]["direction"]) != int(records[cursor + 1]["direction"]), "paired internal edge directions are not opposite")
            records[cursor]["twin"] = cursor + 1
            records[cursor + 1]["twin"] = cursor
            _require(
                int(records[int(records[cursor]["twin"])]["twin"]) == cursor
                and int(records[int(records[cursor + 1]["twin"])]["twin"]) == cursor + 1,
                "halfedge twin pairing mismatch",
            )
            left_root = find(int(records[cursor]["face"]))
            right_root = find(int(records[cursor + 1]["face"]))
            if left_root != right_root:
                parent[right_root] = left_root
        cursor = end
    root = find(0)
    for face in range(1, triangle_count):
        _require(find(face) == root, "triangle dual is disconnected")
    return unique_edges, boundary_edges


def _vertex_link_topology(
    result: dict[str, np.ndarray],
    boundary_rows: np.ndarray,
    vertex_count: int,
    triangle_count: int,
) -> None:
    """Require one cycle link at interior vertices and one path at boundary vertices."""
    segments = result["segments"]
    triangles = result["triangles"]
    boundary_degree = np.zeros(vertex_count, dtype=np.int32)
    for row in range(segments.shape[0]):
        if boundary_rows[row]:
            boundary_degree[int(segments[row, 0])] += 1
            boundary_degree[int(segments[row, 1])] += 1
    corner_count = np.zeros(vertex_count, dtype=np.int64)
    corner_count_total = triangle_count * 3
    outgoing = np.empty(
        corner_count_total,
        dtype=np.dtype([("key", "<u8"), ("other", "<u8")]),
    )
    incoming = np.empty(corner_count_total, dtype=np.uint64)
    cursor = 0
    for face in range(triangle_count):
        a, b, c = (int(value) for value in triangles[face])
        for center, previous, following in ((a, c, b), (b, a, c), (c, b, a)):
            outgoing[cursor] = (center * vertex_count + previous, following)
            incoming[cursor] = np.uint64(center * vertex_count + following)
            corner_count[center] += 1
            cursor += 1
    outgoing.sort(order="key", kind="heapsort")
    incoming.sort(kind="heapsort")
    for index in range(1, outgoing.shape[0]):
        _require(outgoing[index]["key"] != outgoing[index - 1]["key"], "vertex link has multiple outgoing neighbor arcs")
        _require(incoming[index] != incoming[index - 1], "vertex link has multiple incoming neighbor arcs")

    visit_stamp = np.zeros(vertex_count, dtype=np.int64)
    token = 0
    for center in range(vertex_count):
        degree = int(corner_count[center])
        if degree == 0:
            _require(False, "future result has vertex without triangle link")
        boundary = int(boundary_degree[center])
        _require(boundary in (0, 2), "vertex boundary degree is not zero or two")
        low_key = center * vertex_count
        high_key = (center + 1) * vertex_count
        out_low = int(np.searchsorted(outgoing["key"], low_key, side="left"))
        out_high = int(np.searchsorted(outgoing["key"], high_key, side="left"))
        in_low = int(np.searchsorted(incoming, low_key, side="left"))
        in_high = int(np.searchsorted(incoming, high_key, side="left"))
        _require(out_high - out_low == degree and in_high - in_low == degree, "vertex link arc count mismatch")
        missing_out = -1
        missing_in = -1
        for position in range(out_low, out_high):
            key = int(outgoing[position]["key"])
            if int(np.searchsorted(incoming, key, side="left")) == in_high or int(incoming[np.searchsorted(incoming, key, side="left")]) != key:
                _require(missing_out < 0, "vertex link has multiple path starts")
                missing_out = key % vertex_count
        for position in range(in_low, in_high):
            key = int(incoming[position])
            position_out = int(np.searchsorted(outgoing["key"], key, side="left"))
            if position_out == out_high or int(outgoing[position_out]["key"]) != key:
                _require(missing_in < 0, "vertex link has multiple path ends")
                missing_in = key % vertex_count
        if boundary == 0:
            _require(missing_out < 0 and missing_in < 0, "interior vertex link is a path")
            start = int(outgoing[out_low]["key"]) % vertex_count
        else:
            _require(missing_out >= 0 and missing_in >= 0, "boundary vertex link is not a single path")
            start = missing_out
        token += 1
        current = start
        for _ in range(degree):
            _require(visit_stamp[current] != token, "vertex link revisits a neighbor before closure")
            visit_stamp[current] = token
            key = center * vertex_count + current
            position = int(np.searchsorted(outgoing["key"], key, side="left"))
            _require(position < out_high and int(outgoing[position]["key"]) == key, "vertex link outgoing arc missing")
            current = int(outgoing[position]["other"])
        if boundary == 0:
            _require(current == start, "interior vertex link is not one cycle")
        else:
            _require(current == missing_in and visit_stamp[current] != token, "boundary vertex link path closure mismatch")
            visit_stamp[current] = token


def _boundary_rows_from_records(
    result: dict[str, np.ndarray],
    boundary_records: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    segments = result["segments"]
    segment_count = int(segments.shape[0])
    lookup = np.empty(segment_count, dtype=np.dtype([("key", "<u8"), ("row", "<u8")]))
    for row in range(segment_count):
        minimum, maximum = sorted((int(segments[row, 0]), int(segments[row, 1])))
        lookup[row] = ((minimum << 19) | maximum, row)
    lookup.sort(order="key", kind="heapsort")
    rows = np.empty(boundary_records.shape[0], dtype=np.int64)
    starts = np.empty(segment_count, dtype=np.int64)
    ends = np.empty(segment_count, dtype=np.int64)
    markers = np.empty(segment_count, dtype=np.int64)
    for index in range(boundary_records.shape[0]):
        record = int(boundary_records[index])
        oriented = record >> 18
        undirected = oriented >> 1
        minimum = undirected >> 19
        maximum = undirected & ((1 << 19) - 1)
        direction = oriented & 1
        marker = record & ((1 << 18) - 1)
        position = int(np.searchsorted(lookup["key"], undirected, side="left"))
        _require(position < segment_count and int(lookup[position]["key"]) == undirected, "boundary record segment lookup mismatch")
        row = int(lookup[position]["row"])
        rows[index] = row
        starts[row] = maximum if direction else minimum
        ends[row] = minimum if direction else maximum
        markers[row] = marker
        _require(int(result["segment_markers"][row, 0]) == marker, "boundary segment marker mismatch")
    return rows, starts, ends, markers


def _partition_frozen_boundary(
    pslg: dict[str, np.ndarray],
    result: dict[str, np.ndarray],
    boundary_rows: np.ndarray,
    next_vertex: np.ndarray,
    next_edge: np.ndarray,
) -> int:
    """Consume each result boundary subedge along its exact frozen PSLG edge."""
    frozen_vertices = pslg["vertices"]
    frozen_segments = pslg["segments"]
    frozen_markers = pslg["segment_markers"][:, 0]
    vertices = result["vertices"]
    result_markers = result["segment_markers"][:, 0]
    consumed = np.zeros(result["segments"].shape[0], dtype=np.bool_)
    consumed_count = 0
    for index in range(frozen_segments.shape[0]):
        start = int(frozen_segments[index, 1])
        target = int(frozen_segments[index, 0])
        _require(start != target, "frozen PSLG segment is zero length")
        source_a = frozen_vertices[start]
        source_b = frozen_vertices[target]
        dx = float(source_b[0] - source_a[0])
        dy = float(source_b[1] - source_a[1])
        denominator = dx * dx + dy * dy
        _require(math.isfinite(denominator) and denominator > 0.0, "frozen PSLG segment geometry is invalid")
        current = start
        previous_t = 0.0
        steps = 0
        while current != target:
            edge = int(next_edge[current])
            _require(edge >= 0 and boundary_rows[edge] and not consumed[edge], "frozen PSLG segment partition is missing")
            _require(int(result_markers[edge]) == int(frozen_markers[index]), "frozen PSLG marker was not retained")
            following = int(next_vertex[current])
            _require(following >= 0, "frozen PSLG partition successor is missing")
            point = vertices[current]
            following_point = vertices[following]
            cross = dx * float(point[1] - source_a[1]) - dy * float(point[0] - source_a[0])
            following_cross = dx * float(following_point[1] - source_a[1]) - dy * float(following_point[0] - source_a[0])
            _require(cross == 0.0 and following_cross == 0.0, "frozen PSLG partition is not exact-collinear")
            parameter = (float(following_point[0] - source_a[0]) * dx + float(following_point[1] - source_a[1]) * dy) / denominator
            _require(math.isfinite(parameter) and parameter > previous_t and parameter <= 1.0, "frozen PSLG partition is not strictly monotone")
            if following != target:
                _require(parameter < 1.0, "frozen PSLG partition crosses its endpoint")
            consumed[edge] = True
            consumed_count += 1
            previous_t = parameter
            current = following
            steps += 1
            _require(steps <= result["segments"].shape[0], "frozen PSLG partition does not terminate")
    _require(consumed_count == result["segments"].shape[0] and bool(np.all(consumed)), "result boundary subedge was not consumed exactly once")
    return consumed_count


def _stream_quality(result: dict[str, np.ndarray], chunk_size: int) -> dict[str, object]:
    """Stream strict angle/aspect checks without materializing triangle geometry."""
    chunk_size = _bounded_chunk_size(chunk_size, "quality chunk size")
    vertices = result["vertices"]
    triangles = result["triangles"]
    gather_a = np.empty((chunk_size, 2), dtype=np.float64)
    gather_b = np.empty((chunk_size, 2), dtype=np.float64)
    gather_c = np.empty((chunk_size, 2), dtype=np.float64)
    dx_ab = np.empty(chunk_size, dtype=np.float64)
    dy_ab = np.empty(chunk_size, dtype=np.float64)
    dx_bc = np.empty(chunk_size, dtype=np.float64)
    dy_bc = np.empty(chunk_size, dtype=np.float64)
    dx_ca = np.empty(chunk_size, dtype=np.float64)
    dy_ca = np.empty(chunk_size, dtype=np.float64)
    cross = np.empty(chunk_size, dtype=np.float64)
    side_ab = np.empty(chunk_size, dtype=np.float64)
    side_bc = np.empty(chunk_size, dtype=np.float64)
    side_ca = np.empty(chunk_size, dtype=np.float64)
    quality = np.empty(chunk_size, dtype=np.float64)
    minimum_angle = math.inf
    maximum_aspect = 0.0

    def angle(adjacent_a: float, adjacent_b: float, opposite: float) -> float:
        cosine = (adjacent_a + adjacent_b - opposite) / (2.0 * math.sqrt(adjacent_a * adjacent_b))
        return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

    for start in range(0, triangles.shape[0], chunk_size):
        tri = triangles[start : start + chunk_size]
        width = int(tri.shape[0])
        np.take(vertices, tri[:, 0], axis=0, out=gather_a[:width])
        np.take(vertices, tri[:, 1], axis=0, out=gather_b[:width])
        np.take(vertices, tri[:, 2], axis=0, out=gather_c[:width])
        np.subtract(gather_b[:width, 0], gather_a[:width, 0], out=dx_ab[:width])
        np.subtract(gather_b[:width, 1], gather_a[:width, 1], out=dy_ab[:width])
        np.subtract(gather_c[:width, 0], gather_b[:width, 0], out=dx_bc[:width])
        np.subtract(gather_c[:width, 1], gather_b[:width, 1], out=dy_bc[:width])
        np.subtract(gather_a[:width, 0], gather_c[:width, 0], out=dx_ca[:width])
        np.subtract(gather_a[:width, 1], gather_c[:width, 1], out=dy_ca[:width])
        np.multiply(dx_ab[:width], dy_bc[:width], out=cross[:width])
        scratch = dy_ab[:width] * dx_bc[:width]
        np.subtract(cross[:width], scratch, out=cross[:width])
        del scratch
        np.multiply(dx_ab[:width], dx_ab[:width], out=side_ab[:width])
        side_ab[:width] += dy_ab[:width] * dy_ab[:width]
        np.multiply(dx_bc[:width], dx_bc[:width], out=side_bc[:width])
        side_bc[:width] += dy_bc[:width] * dy_bc[:width]
        np.multiply(dx_ca[:width], dx_ca[:width], out=side_ca[:width])
        side_ca[:width] += dy_ca[:width] * dy_ca[:width]
        np.maximum(np.maximum(side_ab[:width], side_bc[:width]), side_ca[:width], out=quality[:width])
        np.divide(quality[:width], cross[:width], out=quality[:width])
        _require(bool(np.isfinite(quality[:width]).all()) and bool(np.all(quality[:width] <= 8.0)), "future triangle aspect exceeds eight")
        maximum_aspect = max(maximum_aspect, float(np.max(quality[:width])))
        for index in range(width):
            ab = float(side_ab[index])
            bc = float(side_bc[index])
            ca = float(side_ca[index])
            _require(ab > 0.0 and bc > 0.0 and ca > 0.0, "future triangle has zero-length edge")
            try:
                angles = (angle(ab, ca, bc), angle(ab, bc, ca), angle(bc, ca, ab))
            except (OverflowError, ValueError) as exc:
                raise Refusal("future triangle angle calculation is non-finite") from exc
            quality[index] = min(angles)
            _require(quality[index] > 7.5, "future triangle minimum angle is not strictly greater than 7.5 degrees")
            minimum_angle = min(minimum_angle, float(quality[index]))
    del gather_a, gather_b, gather_c, dx_ab, dy_ab, dx_bc, dy_bc, dx_ca, dy_ca, cross, side_ab, side_bc, side_ca, quality
    return {
        "quality_pass": True,
        "minimum_angle_deg": minimum_angle,
        "minimum_angle_strict_lower_bound_deg": 7.5,
        "maximum_aspect": maximum_aspect,
        "maximum_aspect_allowed": 8.0,
        "quality_streamed": True,
    }


def _boundary_winding(point: tuple[float, float], vertices: np.ndarray, starts: np.ndarray, ends: np.ndarray, boundary_rows: np.ndarray) -> int:
    px, py = point
    winding = 0
    for row in range(starts.shape[0]):
        if not boundary_rows[row]:
            continue
        left = int(starts[row])
        right = int(ends[row])
        x0, y0 = float(vertices[left, 0]), float(vertices[left, 1])
        x1, y1 = float(vertices[right, 0]), float(vertices[right, 1])
        if y0 <= py < y1:
            if (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0) > 0.0:
                winding += 1
        elif y1 <= py < y0:
            if (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0) < 0.0:
                winding -= 1
    return winding


def certify_full_2d_result(
    pslg: dict[str, np.ndarray],
    result: dict[str, np.ndarray],
    *,
    canonical_output_path: Path | None = None,
    chunk_size: int = CHUNK_SIZE_CAP,
) -> dict[str, object]:
    """Certify a caller-supplied static mesh while retaining every C1 STOP."""
    chunk_size = _bounded_chunk_size(chunk_size, "certifier chunk size")
    pslg_proof = _validate_pslg_arrays(pslg, require_frozen=True)
    pslg_canonical = recompute_c0_pslg_canonical(pslg)
    result_counts = validate_future_triangle_result(result, expected_holes=pslg["holes"])
    vertices = result["vertices"]
    triangles = result["triangles"]
    segments = result["segments"]
    vertex_count = int(vertices.shape[0])
    triangle_count = int(triangles.shape[0])
    segment_count = int(segments.shape[0])
    frozen_vertex_count = int(pslg["vertices"].shape[0])
    frozen_segment_count = int(pslg["segments"].shape[0])
    _require(vertex_count >= frozen_vertex_count and segment_count >= frozen_segment_count, "future result is below frozen PSLG floor")
    _require(
        np.array_equal(vertices[:frozen_vertex_count].view(np.uint64), pslg["vertices"].view(np.uint64)),
        "future vertices do not preserve the frozen PSLG prefix",
    )
    steiner_count = vertex_count - frozen_vertex_count
    boundary_steiner_count = segment_count - frozen_segment_count
    interior_steiner_count = steiner_count - boundary_steiner_count
    _require(0 <= boundary_steiner_count <= steiner_count <= STEINER_POINT_CAP, "future Steiner count model mismatch")
    expected_triangle_count = T_FLOOR + 2 * interior_steiner_count + boundary_steiner_count
    _require(triangle_count == expected_triangle_count, "future triangle count does not match Euler subdivision model")
    closed_vertex_count = 2 * vertex_count
    closed_face_count = 4 * vertex_count + 4 * int(pslg["holes"].shape[0]) - 4
    _require(closed_vertex_count <= CLOSED_VERTEX_CAP and closed_face_count <= CLOSED_FACE_CAP, "future closed cap exceeded")
    _require(vertex_count < (1 << 19), "future vertex count exceeds bounded edge encoding")

    _reject_duplicate_coordinate_records(vertices)
    _reject_duplicate_face_keys(triangles)

    # Validate the frozen boundary convention before allocating any output
    # boundary evidence.  The frozen exterior is clockwise (negative area),
    # followed by counterclockwise holes, and its signed sum is the source
    # area used by every later coverage proof.
    (
        pslg_next_vertex,
        pslg_previous_vertex,
        pslg_next_edge,
        pslg_out_count,
        pslg_cycle_areas,
    ) = _boundary_cycle_evidence(pslg["vertices"], pslg["segments"][:, 0], pslg["segments"][:, 1])
    expected_cycles = int(pslg["holes"].shape[0]) + 1
    _require(len(pslg_cycle_areas) == expected_cycles, "frozen boundary component count mismatch")
    _require(
        pslg_cycle_areas[0][0] < 0.0
        and sum(1 for area, _ in pslg_cycle_areas if area < 0.0) == 1
        and sum(1 for area, _ in pslg_cycle_areas if area > 0.0) == expected_cycles - 1,
        "frozen boundary orientation component mismatch",
    )
    source_area = abs(math.fsum(area for area, _ in pslg_cycle_areas))
    _require(math.isfinite(source_area) and source_area > 0.0, "frozen boundary area is not finite and positive")
    del pslg_next_vertex, pslg_previous_vertex, pslg_next_edge, pslg_out_count, pslg_cycle_areas

    boundary_records = _oriented_boundary_records(result, chunk_size=chunk_size)
    boundary_rows, boundary_starts, boundary_ends, boundary_markers = _boundary_rows_from_records(result, boundary_records)
    # Marker identity was checked while mapping induced records to raw rows;
    # neither the encoded records nor marker copy is needed by later proofs.
    del boundary_records, boundary_markers
    boundary_mask = np.zeros(segment_count, dtype=np.bool_)
    for row in boundary_rows:
        _require(not boundary_mask[int(row)], "future boundary segment appears twice")
        boundary_mask[int(row)] = True
    _require(int(np.count_nonzero(boundary_mask)) == segment_count, "future result contains a non-boundary segment record")
    del boundary_rows
    next_vertex, previous_vertex, next_edge, out_count, output_cycle_areas = _boundary_cycle_evidence(vertices, boundary_starts, boundary_ends)
    del previous_vertex, out_count
    _require(len(output_cycle_areas) == expected_cycles, "boundary component count mismatch")
    _require(
        output_cycle_areas[0][0] > 0.0
        and sum(1 for area, _ in output_cycle_areas if area > 0.0) == 1
        and sum(1 for area, _ in output_cycle_areas if area < 0.0) == expected_cycles - 1,
        "boundary orientation component mismatch",
    )
    boundary_component_count = len(output_cycle_areas)
    del output_cycle_areas

    # Winding consumes the induced endpoint arrays while the reduced
    # next/edge maps from cycle evidence are still retained.  Delete all
    # endpoint and bound arrays before partition/topology allocations.
    first_triangle = triangles[0]
    inside_point = tuple(float(np.mean(vertices[first_triangle, column])) for column in (0, 1))
    bounds_min = np.min(vertices, axis=0)
    bounds_max = np.max(vertices, axis=0)
    span = max(float(bounds_max[0] - bounds_min[0]), float(bounds_max[1] - bounds_min[1]), 1.0)
    outside_point = (float(bounds_max[0]) + span, float(bounds_max[1]) + span)
    inside_degree = _boundary_winding(inside_point, vertices, boundary_starts, boundary_ends, boundary_mask)
    outside_degree = _boundary_winding(outside_point, vertices, boundary_starts, boundary_ends, boundary_mask)
    hole_degrees = tuple(
        _boundary_winding(tuple(float(value) for value in hole), vertices, boundary_starts, boundary_ends, boundary_mask)
        for hole in pslg["holes"]
    )
    _require(inside_degree == 1 and outside_degree == 0 and all(value == 0 for value in hole_degrees), "winding/degree coverage is not single inside and zero outside/holes")
    del boundary_starts, boundary_ends, bounds_min, bounds_max, first_triangle

    partition_consumed = _partition_frozen_boundary(pslg, result, boundary_mask, next_vertex, next_edge)
    _require(partition_consumed == segment_count, "result boundary partition receipt mismatch")
    del next_vertex, next_edge

    unique_edges, boundary_edge_count = _edge_topology(result, vertex_count, triangle_count)
    _require(boundary_edge_count == segment_count, "triangle singleton boundary count mismatch")
    _vertex_link_topology(result, boundary_mask, vertex_count, triangle_count)
    euler_characteristic = vertex_count - unique_edges + triangle_count
    expected_euler = 1 - int(pslg["holes"].shape[0])
    _require(euler_characteristic == expected_euler, "Euler characteristic mismatch")

    area_summary = summarize_positive_area_streaming(result, chunk_size=chunk_size)
    area_tolerance = max(1e-7, abs(source_area) * 1e-12)
    area_sum = float(area_summary["triangle_area_sum_um2"])
    area_consistent = abs(area_sum - source_area) <= area_tolerance
    _require(area_consistent, "streamed triangle area does not equal frozen boundary area")
    quality = _stream_quality(result, chunk_size)
    del boundary_mask

    canonical = stream_future_result_canonical(result, expected_holes=pslg["holes"], byte_cap=FUTURE_CANONICAL_BYTES_CAP)
    canonical_repeat = stream_future_result_canonical(result, expected_holes=pslg["holes"], byte_cap=FUTURE_CANONICAL_BYTES_CAP)
    _require(canonical == canonical_repeat, "future canonical serializer is not deterministic")
    canonical_write_readback: dict[str, object] = {"performed": False}
    if canonical_output_path is not None:
        output_path = Path(canonical_output_path)
        written = write_future_result_canonical(output_path, result, expected_holes=pslg["holes"], byte_cap=FUTURE_CANONICAL_BYTES_CAP)
        observed = readback_canonical(output_path, written, FUTURE_CANONICAL_BYTES_CAP)
        _require(written == canonical == observed, "future canonical write/readback identity mismatch")
        canonical_write_readback = {"performed": True, "path": str(output_path), "bytes": observed[0], "sha256": observed[1]}

    ledger = validate_controlled_lifetime_memory_ledger(controlled_lifetime_memory_ledger())
    _require(ledger["full_2d_cert_status"] == FULL_2D_CERT_STATUS and ledger["c1_execution_status"] == C1_EXECUTION_STATUS, "accepted ledger status mismatch")
    _require(ledger["native_feasibility"] == OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN and ledger["solver_status"] == "STOP" and ledger["powersi_status"] == "STOP", "accepted ledger STOP identity mismatch")
    return {
        "program": PRODUCT,
        "version": VERSION,
        "preparation_status": PREPARATION_STATUS,
        "full_2d_cert_status": FULL_2D_CERT_STATUS,
        "certification_status": FULL_2D_CERT_STATUS,
        "authorization_status": "C1_NOT_AUTHORIZED",
        "c1_execution_status": C1_EXECUTION_STATUS,
        "controlled_buffer_status": STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE,
        "native_feasibility": OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN,
        "solver_status": "STOP",
        "powersi_status": "STOP",
        "triangle_extension_loaded": False,
        "triangle_options": {
            "options": TRIANGLE_OPTIONS,
            "exact_controller_binding": False,
            "controller_binding_required": True,
        },
        "counts": {
            **result_counts,
            "steiner_count": steiner_count,
            "boundary_steiner_count": boundary_steiner_count,
            "interior_steiner_count": interior_steiner_count,
            "expected_triangle_count": expected_triangle_count,
            "closed_vertex_count": closed_vertex_count,
            "closed_face_count": closed_face_count,
        },
        "topology": {
            "unique_edge_count": unique_edges,
            "boundary_edge_count": boundary_edge_count,
            "boundary_components": boundary_component_count,
            "euler_characteristic": euler_characteristic,
            "expected_euler_characteristic": expected_euler,
            "genus": 0,
            "triangle_dual_connected": True,
            "vertex_links_manifold": True,
            "boundary_partition_subedges_consumed": segment_count,
            "winding_degree": {"inside": inside_degree, "outside": outside_degree, "holes": hole_degrees},
            "proof_receipt": {
                "induced_boundary_direction_match": True,
                "paired_internal_edge_directions_opposite": True,
                "all_faces_strictly_positive_signed_area": True,
                "triangle_dual_connected": True,
                "vertex_links_connected_manifold": True,
                "boundary_partition_exact": partition_consumed == segment_count,
                "winding_one_zero": {"inside": inside_degree, "outside": outside_degree, "holes": hole_degrees},
            },
            "coverage_certified": True,
        },
        "quality": quality,
        "area": {
            "triangle_area_sum_um2": area_sum,
            "frozen_boundary_area_um2": source_area,
            "tolerance_um2": area_tolerance,
            "area_sum_consistency": area_consistent,
            "streamed": True,
        },
        "canonical": {
            "future_result": {"bytes": canonical[0], "sha256": canonical[1], "streamed": True},
            "pslg": {"bytes": pslg_canonical[0], "sha256": pslg_canonical[1], "streamed": True},
            "serializer_determinism_only": True,
            "triangle_replay": False,
            "write_readback": canonical_write_readback,
        },
        "frozen_pslg_identity": {"counts": pslg_proof, "canonical": {"bytes": pslg_canonical[0], "sha256": pslg_canonical[1]}},
        "pslg_proof": pslg_proof,
        "memory_ledger": ledger,
        "ledger_identity": {
            "program": ledger["program"],
            "version": ledger["version"],
            "required_terms": ledger["required_terms"],
            "full_2d_cert_status": ledger["full_2d_cert_status"],
            "c1_execution_status": ledger["c1_execution_status"],
        },
        "ledger_validated": True,
    }


def controlled_buffer_accounting(pslg: dict[str, np.ndarray], future_result: object | None = None) -> dict[str, object]:
    """Account only owned array buffers; native/interpreter feasibility stays unknown."""
    _validate_pslg_arrays(pslg)
    memory_ledger = controlled_lifetime_memory_ledger()
    input_bytes = sum(int(pslg[key].nbytes) for key in ("vertices", "segments", "segment_markers", "holes"))
    output_bytes = 0
    output_counts: dict[str, int] = {}
    if future_result is not None:
        output_counts = validate_future_triangle_result(future_result)
        output_bytes = sum(int(future_result[key].nbytes) for key in ("vertices", "vertex_markers", "triangles", "segments", "segment_markers", "holes"))
    _require(input_bytes <= INPUT_ARRAY_BYTES_CAP and output_bytes <= OUTPUT_ARRAY_BYTES_CAP and input_bytes + output_bytes <= COMBINED_ARRAY_BYTES_CAP, "controlled array buffer cap exceeded")
    return {
        "status": STATIC_OWNED_NDARRAY_BYTES_PASS,
        "controlled_buffer_status": STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE,
        "excluded_terms": CONTROLLED_BUFFER_EXCLUDED_TERMS,
        "native_feasibility": OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN,
        "input_array_bytes": input_bytes,
        "output_array_bytes": output_bytes,
        "combined_array_bytes": input_bytes + output_bytes,
        "input_array_bytes_cap": INPUT_ARRAY_BYTES_CAP,
        "output_array_bytes_cap": OUTPUT_ARRAY_BYTES_CAP,
        "combined_array_bytes_cap": COMBINED_ARRAY_BYTES_CAP,
        "future_process_cap_bytes": FUTURE_PROCESS_CAP_BYTES,
        "future_process_cap_operational_only": True,
        "future_process_cap_is_proof": False,
        "future_result_counts": output_counts,
        "memory_ledger": memory_ledger,
        "overall_status": PREPARATION_STATUS,
        "c1_execution_status": C1_EXECUTION_STATUS,
    }


def validate_controlled_lifetime_memory_ledger(ledger: object) -> dict[str, object]:
    """Fail closed when a required phase, term, lifetime, or overlap is omitted."""
    _require(isinstance(ledger, dict), "memory ledger must be an object")
    _require(tuple(ledger.get("phases", ())) == MEMORY_LEDGER_PHASES, "memory ledger phase set mismatch")
    _require(tuple(ledger.get("required_terms", ())) == MEMORY_LEDGER_REQUIRED_TERMS, "memory ledger required term set mismatch")
    terms = ledger.get("terms")
    _require(isinstance(terms, dict), "memory ledger terms missing")
    _require("owned_array_boundary_floor" not in terms, "memory ledger aggregate pseudo-term is forbidden")
    _require(tuple(MEMORY_LEDGER_REQUIRED_TERMS) == tuple(MEMORY_LEDGER_TERM_CONTRACT), "memory ledger contract term set mismatch")
    for name in MEMORY_LEDGER_REQUIRED_TERMS:
        row = terms.get(name)
        _require(isinstance(row, dict), f"memory ledger term missing: {name}")
        expected = MEMORY_LEDGER_TERM_CONTRACT[name]
        _require(type(row.get("phases")) is tuple and row["phases"] == expected["phases"], f"memory ledger exact phases mismatch: {name}")
        _require(type(row.get("classification")) is str and row["classification"] == expected["classification"], f"memory ledger exact classification mismatch: {name}")
        if "formula" in expected:
            _require(type(row.get("formula")) is str and row["formula"] == expected["formula"], f"memory ledger exact formula mismatch: {name}")
        expected_bound = expected["bound_bytes"]
        observed_bound = row.get("bound_bytes")
        if expected_bound is None:
            _require(observed_bound is None, f"memory ledger exact opaque bound mismatch: {name}")
        else:
            _require(type(observed_bound) is int and observed_bound == expected_bound, f"memory ledger exact bound mismatch: {name}")
        phases = row.get("phases")
        _require(isinstance(phases, (tuple, list)) and phases and all(phase in MEMORY_LEDGER_PHASES for phase in phases), f"memory ledger lifetime missing: {name}")
        classification = row.get("classification")
        _require(classification in MEMORY_LEDGER_CLASSES, f"memory ledger classification missing: {name}")
        bound = row.get("bound_bytes")
        if classification == "job-contained opaque":
            _require(bound is None or type(bound) is int and bound >= 0, f"memory ledger opaque bound malformed: {name}")
        else:
            _require(type(bound) is int and bound >= 0, f"memory ledger formula bound missing: {name}")
        overlap_with = row.get("overlap_with")
        _require(isinstance(overlap_with, (tuple, list)) and name not in overlap_with, f"memory ledger overlap list missing: {name}")
        for other in overlap_with:
            _require(other in terms and name in terms[other].get("overlap_with", ()), f"memory ledger asymmetric overlap: {name}->{other}")
    phase_ledger = ledger.get("phase_ledger")
    _require(isinstance(phase_ledger, dict), "memory ledger phase ledger missing")
    _require(set(phase_ledger) == set(MEMORY_LEDGER_PHASES), "memory ledger phase key set mismatch")
    flattened: set[str] = set()
    for phase in MEMORY_LEDGER_PHASES:
        names = phase_ledger.get(phase)
        _require(isinstance(names, (tuple, list)), f"memory ledger phase missing: {phase}")
        expected = tuple(name for name, row in terms.items() if phase in row["phases"])
        _require(len(names) == len(set(names)) and set(names) == set(expected), f"memory ledger phase projection mismatch: {phase}")
        for name in names:
            _require(name in terms and phase in terms[name]["phases"], f"memory ledger phase lifetime mismatch: {phase}->{name}")
            flattened.add(name)
    _require(flattened == set(MEMORY_LEDGER_REQUIRED_TERMS), "memory ledger phase term omission")
    phase_floor_candidates = ledger.get("phase_floor_candidates")
    _require(isinstance(phase_floor_candidates, dict), "memory ledger phase floor candidates missing")
    _require(set(phase_floor_candidates) == set(MEMORY_LEDGER_PHASES), "memory ledger phase floor candidate key set mismatch")
    phase_floor_components = ledger.get("phase_floor_components")
    _require(isinstance(phase_floor_components, dict), "memory ledger phase floor components missing")
    _require(set(phase_floor_components) == set(MEMORY_LEDGER_PHASES), "memory ledger phase floor key set mismatch")
    phase_floor_bytes: dict[str, int] = {}
    for phase in MEMORY_LEDGER_PHASES:
        candidates = phase_floor_candidates.get(phase)
        _require(isinstance(candidates, (tuple, list)) and candidates, f"memory ledger phase floor candidates malformed: {phase}")
        candidate_values: list[int] = []
        for candidate in candidates:
            _require(isinstance(candidate, (tuple, list)) and len(candidate) == len(set(candidate)), f"memory ledger phase floor candidate malformed: {phase}")
            _require(set(candidate).issubset(set(phase_ledger[phase])), f"memory ledger phase floor candidate membership mismatch: {phase}")
            _require(all(type(terms[name].get("bound_bytes")) is int for name in candidate), f"memory ledger phase floor candidate opaque term: {phase}")
            candidate_values.append(sum(int(terms[name]["bound_bytes"]) for name in candidate))
        components = phase_floor_components.get(phase)
        _require(isinstance(components, (tuple, list)) and len(components) == len(set(components)), f"memory ledger phase floor malformed: {phase}")
        _require(set(components).issubset(set(phase_ledger[phase])), f"memory ledger phase floor membership mismatch: {phase}")
        _require(all(type(terms[name].get("bound_bytes")) is int for name in components), f"memory ledger phase floor opaque term: {phase}")
        _require(tuple(components) in {tuple(candidate) for candidate in candidates}, f"memory ledger selected floor candidate missing: {phase}")
        selected_sum = sum(int(terms[name]["bound_bytes"]) for name in components)
        _require(selected_sum == max(candidate_values), f"memory ledger phase floor is not the candidate maximum: {phase}")
        phase_floor_bytes[phase] = selected_sum
    _require(tuple(phase_floor_components["boundary"]) == MEMORY_LEDGER_FLOOR_COMPONENTS, "memory ledger boundary floor components missing")
    _require(phase_floor_bytes["boundary"] == FULL_CERTIFIER_ARRAY_BOUNDARY_FLOOR_BYTES, "memory ledger boundary floor mismatch")
    _require(ledger.get("phase_floor_bytes") == phase_floor_bytes, "memory ledger phase floor bytes mismatch")
    known_peak = max(phase_floor_bytes.values())
    _require(ledger.get("known_peak_floor_bytes") == known_peak, "memory ledger known peak floor mismatch")
    _require(ledger.get("known_concurrent_floor_bytes") == known_peak, "memory ledger concurrent floor mismatch")
    overlap_rows = ledger.get("overlaps")
    _require(isinstance(overlap_rows, (tuple, list)), "memory ledger overlap ledger missing")
    overlap_set = {tuple(sorted(edge)) for edge in overlap_rows if isinstance(edge, (tuple, list)) and len(edge) == 2}
    _require(len(overlap_set) == len(overlap_rows), "memory ledger overlap duplicate or malformed")
    # A phase is a coarse lifetime label and may contain mutually-exclusive
    # subphases.  Co-live requirements are the explicit overlap rows above.
    derived_overlap_set = {
        tuple(sorted((name, other)))
        for name, row in terms.items()
        for other in row["overlap_with"]
    }
    _require(derived_overlap_set == overlap_set, "memory ledger overlap projection mismatch")
    for left, right in MEMORY_LEDGER_REQUIRED_OVERLAPS:
        _require(tuple(sorted((left, right))) in overlap_set, f"memory ledger required overlap missing: {left}->{right}")
    for name, row in terms.items():
        _require(name in MEMORY_LEDGER_REQUIRED_TERMS, f"memory ledger unknown term: {name}")
    _require(ledger.get("operational_job_cap_bytes") == FUTURE_PROCESS_CAP_BYTES, "memory ledger operational cap mismatch")
    _require(ledger.get("operational_job_cap_is_proof") is False, "memory ledger operational cap proof mismatch")
    _require(ledger.get("release_note") == CONTROLLED_RELEASE_NOTE, "memory ledger release note mismatch")
    _require(ledger.get("omitted_terms") == (), "memory ledger omitted terms mismatch")
    _require(ledger.get("owned_array_accounting_exclusions") == CONTROLLED_BUFFER_EXCLUDED_TERMS, "memory ledger exclusion tuple mismatch")
    _require(ledger.get("native_feasibility") == OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN, "memory ledger native status mismatch")
    _require(ledger.get("full_2d_cert_status") == FULL_2D_CERT_STATUS, "memory ledger full 2-D status mismatch")
    _require(ledger.get("c1_execution_status") == C1_EXECUTION_STATUS, "memory ledger C1 status mismatch")
    _require(ledger.get("triangle_extension_loaded") is False, "memory ledger Triangle boundary mismatch")
    _require(ledger.get("solver_status") == "STOP" and ledger.get("powersi_status") == "STOP", "memory ledger solver status mismatch")
    return ledger


def controlled_lifetime_memory_ledger() -> dict[str, object]:
    """Return the static phase/lifetime ledger without claiming allocator release."""
    terms: dict[str, dict[str, object]] = {
        "sealed_read_buffer": {"phases": ("read/parse",), "classification": "mutually exclusive", "bound_bytes": max(C0_SIZE, D103_SIZE, D104_SIZE, SOURCE_SIZE, STAGE0_SIZE, WHEEL_SIZE), "formula": "max(sealed input sizes)"},
        "source_parser_copy": {"phases": ("read/parse",), "classification": "formula-bounded", "bound_bytes": SOURCE_SIZE, "formula": "immutable Shapely parser bridge, at most SOURCE_SIZE"},
        "parsed_provenance_receipts": {"phases": ("read/parse",), "classification": "job-contained opaque", "bound_bytes": None, "formula": "Python JSON parser allocation is not source-provable"},
        "parsed_source_geometry": {"phases": ("read/parse", "PSLG"), "classification": "job-contained opaque", "bound_bytes": None, "formula": "Shapely geometry allocation is not source-provable"},
        "pslg_input_arrays": {"phases": ("PSLG", "native-call", "result", "validation", "boundary", "quality", "canonical", "receipt"), "classification": "concurrently live", "bound_bytes": INPUT_ARRAY_BYTES_CAP, "formula": "float64[S,2] + int32[S,2] + int32[S,1] + float64[H,2]"},
        "pslg_ring_storage": {"phases": ("PSLG",), "classification": "job-contained opaque", "bound_bytes": None, "formula": "ring storage and geometry allocator are not source-provable"},
        "native_call_workspace": {"phases": ("native-call",), "classification": "job-contained opaque", "bound_bytes": None, "formula": "Triangle/native/import/allocator ownership is unknown", "status": OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN},
        "result_arrays": {"phases": ("native-call", "result", "validation", "boundary", "quality", "canonical", "receipt"), "classification": "concurrently live", "bound_bytes": OUTPUT_ARRAY_BYTES_CAP, "formula": "float64[V,2] + int32[V,1] + int32[T,3] + int32[B,2] + int32[B,1] + float64[H,2]"},
        "validation_scratch": {"phases": ("validation", "canonical"), "classification": "job-contained opaque", "bound_bytes": None, "formula": "unique/sort/bincount allocator retention in validation and canonical validators is not source-provable"},
        "boundary_triangle_edges": {"phases": ("boundary",), "classification": "concurrently live", "bound_bytes": BOUNDARY_TRIANGLE_EDGE_BYTES_CAP, "formula": "3 * PLANAR_TRIANGLE_CAP * uint64"},
        "boundary_records": {"phases": ("boundary",), "classification": "concurrently live", "bound_bytes": BOUNDARY_RECORD_BYTES_CAP, "formula": "PLANAR_VERTEX_CAP * uint64"},
        "boundary_chunk_scratch": {"phases": ("boundary",), "classification": "concurrently live", "bound_bytes": BOUNDARY_SCRATCH_BYTES_CAP, "formula": "3 * CHUNK_SIZE_CAP * uint64"},
        "quality_used_vertex_scan": {"phases": ("quality",), "classification": "formula-bounded", "bound_bytes": PLANAR_VERTEX_CAP, "formula": "PLANAR_VERTEX_CAP * bool"},
        "quality_gather_scratch": {"phases": ("quality",), "classification": "formula-bounded", "bound_bytes": CHUNK_SIZE_CAP * 97, "formula": "three float64[chunk,2] gathers + six float64 vectors + one bool vector in streamed area check (97*chunk bytes)"},
        "quality_numpy_allocator_retention": {"phases": ("quality",), "classification": "job-contained opaque", "bound_bytes": None, "formula": "NumPy/interpreter allocator retention is not source-provable"},
        "canonical_stream": {"phases": ("canonical",), "classification": "formula-bounded", "bound_bytes": FUTURE_CANONICAL_BYTES_CAP, "formula": "derived canonical line cap"},
        "receipt_payload": {"phases": ("receipt",), "classification": "job-contained opaque", "bound_bytes": None, "formula": "receipt serialization/I/O ownership is not source-provable"},
    }
    for name, contract in _CERTIFIER_LEDGER_TERM_CONTRACT.items():
        terms[name] = dict(contract)
    overlaps = MEMORY_LEDGER_REQUIRED_OVERLAPS
    overlap_map = {name: set() for name in terms}
    for left, right in overlaps:
        overlap_map[left].add(right)
        overlap_map[right].add(left)
    for name, row in terms.items():
        row["overlap_with"] = tuple(sorted(overlap_map[name]))
    phase_ledger = {phase: tuple(name for name, row in terms.items() if phase in row["phases"]) for phase in MEMORY_LEDGER_PHASES}
    phase_floor_candidates = {
        phase: tuple(tuple(candidate) for candidate in candidates)
        for phase, candidates in MEMORY_LEDGER_PHASE_FLOOR_CANDIDATES.items()
    }
    phase_floor_components = {
        phase: max(
            candidates,
            key=lambda candidate: sum(int(terms[name]["bound_bytes"]) for name in candidate),
        )
        for phase, candidates in phase_floor_candidates.items()
    }
    phase_floor_bytes = {
        phase: sum(int(terms[name]["bound_bytes"]) for name in components)
        for phase, components in phase_floor_components.items()
    }
    ledger: dict[str, object] = {
        "program": PRODUCT,
        "version": VERSION,
        "phases": MEMORY_LEDGER_PHASES,
        "phase_ledger": phase_ledger,
        "required_terms": MEMORY_LEDGER_REQUIRED_TERMS,
        "terms": terms,
        "overlaps": overlaps,
        "phase_floor_candidates": phase_floor_candidates,
        "phase_floor_components": phase_floor_components,
        "phase_floor_bytes": phase_floor_bytes,
        "known_concurrent_floor_bytes": max(phase_floor_bytes.values()),
        "known_concurrent_floor_formula": "max(phase_floor_bytes) over explicit co-live phase candidates",
        "known_peak_floor_bytes": max(phase_floor_bytes.values()),
        "known_peak_floor_formula": "max(phase_floor_bytes) over explicit co-live phase components",
        "operational_job_cap_bytes": FUTURE_PROCESS_CAP_BYTES,
        "operational_job_cap_is_proof": False,
        "release_note": CONTROLLED_RELEASE_NOTE,
        "native_feasibility": OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN,
        "full_2d_cert_status": FULL_2D_CERT_STATUS,
        "c1_execution_status": C1_EXECUTION_STATUS,
        "triangle_extension_loaded": False,
        "solver_status": "STOP",
        "powersi_status": "STOP",
        "omitted_terms": (),
        "owned_array_accounting_exclusions": CONTROLLED_BUFFER_EXCLUDED_TERMS,
    }
    return validate_controlled_lifetime_memory_ledger(ledger)


def prepare_no_triangle() -> dict[str, object]:
    """Perform bounded C1 preparation and stop before any optional extension."""
    before = tuple(sorted(name for name in sys.modules if name == "triangle" or name.startswith("triangle.")))
    _require(not before, "optional extension preloaded before preparation")
    try:
        shape, identities = validate_frozen_inputs()
        pslg = build_pslg_arrays(shape, THICKNESS_UM)
        pslg_proof = _validate_pslg_arrays(pslg, shape, require_frozen=True)
        del shape
        canonical = recompute_c0_pslg_canonical(pslg)
        buffers = controlled_buffer_accounting(pslg)
        after = tuple(sorted(name for name in sys.modules if name == "triangle" or name.startswith("triangle.")))
        _require(not after and before == after, "optional extension loaded during preparation")
        return {
            "program": PRODUCT,
            "version": VERSION,
            "status": PREPARATION_STATUS,
            "c1_execution_status": C1_EXECUTION_STATUS,
            "owned_array_status": STATIC_OWNED_NDARRAY_BYTES_PASS,
            "controlled_buffer_status": STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE,
            "native_feasibility": OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN,
            "geometry_only": True,
            "research_only": True,
            "non_shipped": True,
            "solver_executed": False,
            "triangle_extension_loaded": False,
            "triangle_modules_before": before,
            "triangle_modules_after": after,
            "inputs": identities,
            "pslg": pslg,
            "pslg_proof": pslg_proof,
            "canonical": {"bytes": canonical[0], "sha256": canonical[1], "streamed": True},
            "buffers": buffers,
            "quality_contract": {
                "options": TRIANGLE_OPTIONS,
                "minimum_angle_strictly_greater_than_deg": 7.5,
                "aspect_max": 8.0,
                "aspect_definition": "longest edge / shortest altitude",
                "count_model": "V=V0+ni+nb; B=B0+nb; T=T0+2ni+nb",
                "steiner_point_cap": STEINER_POINT_CAP,
                "steiner_vertex_max": STEINER_VERTEX_MAX,
                "steiner_triangle_max": STEINER_TRIANGLE_MAX,
                "steiner_segment_max": STEINER_SEGMENT_MAX,
                "steiner_closed_vertex_max": STEINER_CLOSED_VERTEX_MAX,
                "steiner_closed_face_max": STEINER_CLOSED_FACE_MAX,
                "all_interior_maximizes_triangle_count": "all-interior Steiner points maximize T",
                "static_native_bound": STATIC_NATIVE_BOUND,
            },
        }
    finally:
        after = tuple(sorted(name for name in sys.modules if name == "triangle" or name.startswith("triangle.")))
        _require(not after and before == after, "optional extension loaded during preparation refusal")


def _self_check() -> dict[str, object]:
    from shapely.geometry import Polygon

    shape = Polygon(((0, 0), (10, 0), (10, 10), (0, 10)))
    pslg = build_pslg_arrays(shape, 1.0)
    _validate_pslg_arrays(pslg, shape, thickness=1.0)
    digest = stream_pslg_canonical(pslg)
    _require(digest[0] > 0 and len(digest[1]) == 64, "synthetic canonical self-check failed")
    fake = {"vertices": np.array(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)), dtype=np.float64), "vertex_markers": np.array(((1,), (2,), (3,)), dtype=np.int32), "triangles": np.array(((0, 1, 2),), dtype=np.int32), "segments": np.array(((0, 1), (1, 2), (2, 0)), dtype=np.int32), "segment_markers": np.array(((1,), (2,), (3,)), dtype=np.int32), "holes": np.array(((0.25, 0.25),), dtype=np.float64)}
    saved_floors = (EXPECTED_PSLG_VERTICES, EXPECTED_PSLG_SEGMENTS, EXPECTED_HOLES, T_FLOOR)
    globals()["EXPECTED_PSLG_VERTICES"] = 3
    globals()["EXPECTED_PSLG_SEGMENTS"] = 3
    globals()["EXPECTED_HOLES"] = 1
    globals()["T_FLOOR"] = 1
    try:
        summary = summarize_positive_area_streaming(fake, Polygon(((0, 0), (1, 0), (0, 1))))
        _require(summary["coverage_certified"] is False and summary["area_sum_consistency"] is True and summary["full_2d_cert_status"] == FULL_2D_CERT_STATUS, "synthetic coverage boundary self-check failed")
        boundary_records = _oriented_boundary_records(fake)
        _require(boundary_records.shape == (3,) and boundary_records.base is None, "synthetic oriented boundary self-check failed")
        _require(stream_future_result_canonical(fake)[0] > 0, "synthetic future canonical self-check failed")
        accounting = controlled_buffer_accounting(pslg, fake)
    finally:
        globals()["EXPECTED_PSLG_VERTICES"], globals()["EXPECTED_PSLG_SEGMENTS"], globals()["EXPECTED_HOLES"], globals()["T_FLOOR"] = saved_floors
    _require(accounting["status"] == STATIC_OWNED_NDARRAY_BYTES_PASS and accounting["controlled_buffer_status"] == STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE and accounting["native_feasibility"] == OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN, "synthetic buffer self-check failed")
    return {"status": PREPARATION_STATUS, "canonical_streaming": True, "fake_result_contract": True, "owned_array_status": accounting["status"], "buffer_status": accounting["controlled_buffer_status"], "native_feasibility": accounting["native_feasibility"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} d117 cell258 C1 preparation")
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    parser.add_argument("--self-check", action="store_true")
    try:
        if parser.parse_args(argv).self_check:
            result = _self_check()
            print(f"{PRODUCT} v{VERSION} {result['status']} self-check PASS")
        else:
            result = prepare_no_triangle()
            print(f"{PRODUCT} v{VERSION} {result['status']}; {result['c1_execution_status']}")
        return 0
    except (Refusal, OSError, ValueError, KeyError) as exc:
        print(f"{PRODUCT} v{VERSION} STOP_C1_PREPARATION: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
