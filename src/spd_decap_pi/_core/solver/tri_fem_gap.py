"""Exact P1 projection of one source-owned adjacent-layer capacitance.

The operator in this module is deliberately dormant: it is a bounded,
manufactured-testable bridge between two explicit triangular surface meshes,
not a production-network adapter.  It projects a scalar parallel-plate source
capacitance without collapsing either surface to one equipotential node.

For every positive-area intersection of an upper and lower triangle it
integrates ``psi psi.T``, where ``psi = [phi_upper; -phi_lower]``.  A positive
three-point degree-two rule makes that integral exact for P1 basis functions.
Consequently the matrix is reciprocal, floating, positive semidefinite, and
recollapses to the source pair capacitance when each layer is equipotential.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Final, Mapping, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
import shapely
from scipy.sparse import csc_matrix, coo_matrix
from shapely import STRtree, normalize
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity


EPSILON_0_F_PER_M: Final = 8.854_187_812_8e-12
TRI_FEM_GAP_COMPILER_ID: Final = "source-tri-fem-gap-p1-exact-v2"
TRI_FEM_GAP_INTEGRATION_ID: Final = (
    "strtree-streamed-convex-fan-positive-degree2-chunked-v2"
)
_FLOAT_EPS: Final = np.finfo(np.float64).eps
_DEFAULT_MAX_CANDIDATE_PAIRS: Final = 2_000_000
_DEFAULT_MAX_FAN_TRIANGLES: Final = 4_000_000
_DEFAULT_MAX_NNZ: Final = 20_000_000
_DEFAULT_MAX_TRIPLET_ENTRIES: Final = 5_000_000
_DEFAULT_MAX_TEMPORARY_BYTES: Final = 64 * 1024 * 1024
_MAX_TRIPLET_ENTRIES: Final = 20_000_000
_MAX_TEMPORARY_BYTES: Final = 512 * 1024 * 1024
_TRIPLET_TEMPORARY_BYTES: Final = 64
_PAIR_TRIPLET_COUNT: Final = 36
_MAX_CHUNK_TRIPLETS: Final = 250_000
_MAX_MESH_NODES: Final = 250_000
_MAX_MESH_TRIANGLES: Final = 500_000
_MAX_SELF_CANDIDATES: Final = 4_000_000
_PSD_DENSE_LIMIT: Final = 1_024
_BASIS_RELATIVE_ERROR_BUDGET: Final = 2.0e-10
_BASIS_ROUNDOFF_FACTOR: Final = 16.0
_SOURCE_RELATIVE_TOLERANCE: Final = 2.0e-9
_MATRIX_RELATIVE_TOLERANCE: Final = 2.0e-11


class TriFemGapError(ValueError):
    """Fail-closed mesh, material, geometry, work, or operator error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "TRI_FEM_GAP_INVALID"
        super().__init__(f"{self.code}: {message}")


def _required_id(value: object, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise TriFemGapError("IDENTITY_INVALID", f"{label} must be nonblank")
    return text


def _sha256_id(value: object, *, label: str) -> str:
    text = _required_id(value, label=label).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise TriFemGapError(
            "IDENTITY_INVALID",
            f"{label} must be a 64-character hexadecimal SHA-256",
        )
    return text


def _positive(value: object, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TriFemGapError("MATERIAL_INVALID", f"{label} must be numeric") from exc
    if not isfinite(result) or result <= 0.0:
        raise TriFemGapError("MATERIAL_INVALID", f"{label} must be finite and > 0")
    return result


def _work_bound(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise TriFemGapError("WORK_BOUND_INVALID", f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TriFemGapError("WORK_BOUND_INVALID", f"{label} must be an integer") from exc
    if result != value or result < 1 or result > maximum:
        raise TriFemGapError(
            "WORK_BOUND_INVALID",
            f"{label} must be in [1, {maximum}]",
        )
    return result


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _readonly_float(values: ArrayLike) -> NDArray[np.float64]:
    try:
        raw = np.asarray(values)
    except (TypeError, ValueError) as exc:
        raise TriFemGapError(
            "MESH_INVALID",
            "mesh coordinates must form one regular array",
        ) from exc
    if np.iscomplexobj(raw):
        raise TriFemGapError("MESH_INVALID", "mesh coordinates must be real")
    result = np.array(raw, dtype=np.float64, order="C", copy=True)
    result[result == 0.0] = 0.0
    result.setflags(write=False)
    return result


def _readonly_int(values: ArrayLike) -> NDArray[np.int64]:
    try:
        raw = np.asarray(values)
    except (TypeError, ValueError) as exc:
        raise TriFemGapError(
            "MESH_INVALID",
            "triangle indices must form one regular array",
        ) from exc
    checked: list[int] = []
    for value in raw.reshape(-1):
        scalar = value.item() if isinstance(value, np.generic) else value
        if isinstance(scalar, (bool, np.bool_, complex, np.complexfloating)):
            raise TriFemGapError(
                "MESH_INVALID",
                "triangle indices must be exact integers",
            )
        try:
            integer = int(scalar)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TriFemGapError(
                "MESH_INVALID",
                "triangle indices must be exact integers",
            ) from exc
        try:
            exact = scalar == integer
        except Exception as exc:
            raise TriFemGapError(
                "MESH_INVALID",
                "triangle indices must be exact integers",
            ) from exc
        if not isinstance(exact, (bool, np.bool_)) or not bool(exact):
            raise TriFemGapError(
                "MESH_INVALID",
                "fractional triangle indices are forbidden",
            )
        if integer < np.iinfo(np.int64).min or integer > np.iinfo(np.int64).max:
            raise TriFemGapError(
                "MESH_INVALID",
                "a triangle index exceeds int64 range",
            )
        checked.append(integer)
    result = np.asarray(checked, dtype=np.int64).reshape(raw.shape).copy(order="C")
    result.setflags(write=False)
    return result


def _readonly_csc(matrix: csc_matrix) -> csc_matrix:
    try:
        source = csc_matrix(matrix, copy=True)
    except Exception as exc:
        raise TriFemGapError("MATRIX_INVALID", "matrix is not sparse-compatible") from exc
    if np.iscomplexobj(source.data):
        if np.any(np.imag(source.data) != 0.0):
            raise TriFemGapError("MATRIX_INVALID", "capacitance matrix must be real")
        source = csc_matrix(
            (np.real(source.data), source.indices, source.indptr),
            shape=source.shape,
        )
    result = csc_matrix(source, dtype=np.float64, copy=True)
    result.sum_duplicates()
    result.sort_indices()
    result.eliminate_zeros()
    result.data[result.data == 0.0] = 0.0
    result.data.setflags(write=False)
    result.indices.setflags(write=False)
    result.indptr.setflags(write=False)
    return result


def _canonical_bytes(array: NDArray[np.generic], dtype: str) -> bytes:
    return np.asarray(array, dtype=dtype, order="C").tobytes(order="C")


def _content_identity(
    layer_id: str,
    mesh_identity_sha256: str,
    nodes: NDArray[np.float64],
    triangles: NDArray[np.int64],
    owners: Sequence[str],
) -> str:
    manifest = {
        "layer_id": layer_id,
        "mesh_identity_sha256": mesh_identity_sha256,
        "node_count": len(nodes),
        "triangle_count": len(triangles),
        "triangle_owner_ids": list(owners),
    }
    digest = sha256(_canonical_json(manifest))
    digest.update(_canonical_bytes(nodes, "<f8"))
    digest.update(_canonical_bytes(triangles, "<i8"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _AffineP1Basis:
    origin: NDArray[np.float64]
    inverse: NDArray[np.float64]
    condition_number: float

    def evaluate(self, point: NDArray[np.float64]) -> NDArray[np.float64]:
        first_two = self.inverse @ (point - self.origin)
        result = np.asarray(
            (first_two[0], first_two[1], 1.0 - first_two[0] - first_two[1]),
            dtype=np.float64,
        )
        tolerance = (
            _BASIS_ROUNDOFF_FACTOR * _FLOAT_EPS * self.condition_number
        )
        if (
            not np.all(np.isfinite(result))
            or np.min(result) < -tolerance
            or np.max(result) > 1.0 + tolerance
        ):
            raise TriFemGapError(
                "QUADRATURE_UNSTABLE",
                "intersection quadrature point cannot be represented stably",
            )
        return result


def _affine_basis(
    triangle: NDArray[np.float64],
    *,
    error_code: str,
    label: str,
) -> _AffineP1Basis:
    origin = np.asarray(triangle[2], dtype=np.float64)
    transform = np.column_stack(
        (triangle[0] - triangle[2], triangle[1] - triangle[2])
    )
    condition = float(np.linalg.cond(transform))
    error_estimate = _BASIS_ROUNDOFF_FACTOR * _FLOAT_EPS * condition
    if (
        not isfinite(condition)
        or condition <= 0.0
        or error_estimate > _BASIS_RELATIVE_ERROR_BUDGET
    ):
        raise TriFemGapError(
            error_code,
            f"{label} condition {condition:.6g} exceeds the P1 error budget",
        )
    try:
        inverse = np.linalg.inv(transform)
    except np.linalg.LinAlgError as exc:
        raise TriFemGapError(error_code, f"{label} is singular") from exc
    return _AffineP1Basis(
        origin=_readonly_float(origin),
        inverse=_readonly_float(inverse),
        condition_number=condition,
    )


def _triangle_polygons(
    nodes: NDArray[np.float64],
    triangles: NDArray[np.int64],
    *,
    layer_id: str,
) -> tuple[tuple[Polygon, ...], tuple[_AffineP1Basis, ...]]:
    polygons: list[Polygon] = []
    bases: list[_AffineP1Basis] = []
    seen: set[tuple[int, int, int]] = set()
    for index, row in enumerate(triangles):
        key = tuple(sorted(map(int, row)))
        if key in seen:
            raise TriFemGapError(
                "MESH_DUPLICATE_TRIANGLE",
                f"layer {layer_id!r} repeats triangle nodes {key!r}",
            )
        seen.add(key)
        points = nodes[row]
        local_span = float(np.max(np.ptp(points, axis=0), initial=0.0))
        first_edge = points[1] - points[0]
        second_edge = points[2] - points[0]
        determinant = float(
            first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0]
        )
        minimum = 128.0 * _FLOAT_EPS * max(local_span * local_span, np.finfo(float).tiny)
        if abs(determinant) <= minimum:
            raise TriFemGapError(
                "MESH_DEGENERATE_TRIANGLE",
                f"layer {layer_id!r} triangle {index} has zero numerical area",
            )
        polygon = Polygon(points)
        if polygon.is_empty or not polygon.is_valid or polygon.area <= 0.0:
            raise TriFemGapError(
                "MESH_TRIANGLE_INVALID",
                f"layer {layer_id!r} triangle {index}: {explain_validity(polygon)}",
            )
        polygons.append(polygon)
        bases.append(
            _affine_basis(
                points,
                error_code="MESH_ILL_CONDITIONED",
                label=f"layer {layer_id!r} triangle {index}",
            )
        )
    return tuple(polygons), tuple(bases)


def _same_point(left: Sequence[float], right: Sequence[float], tolerance: float) -> bool:
    return bool(np.linalg.norm(np.asarray(left) - np.asarray(right)) <= tolerance)


def _is_full_edge(
    triangle: Polygon,
    start: Sequence[float],
    stop: Sequence[float],
    tolerance: float,
) -> bool:
    coordinates = tuple(triangle.exterior.coords)[:-1]
    for index, first in enumerate(coordinates):
        second = coordinates[(index + 1) % 3]
        direct = _same_point(first, start, tolerance) and _same_point(
            second,
            stop,
            tolerance,
        )
        reverse = _same_point(first, stop, tolerance) and _same_point(
            second,
            start,
            tolerance,
        )
        if direct or reverse:
            return True
    return False


@dataclass(frozen=True, slots=True)
class _ValidatedMesh:
    mesh: P1LayerMesh
    polygons: tuple[Polygon, ...]
    bases: tuple[_AffineP1Basis, ...]
    domain: Polygon
    maximum_condition_number: float


def _validate_mesh_topology(mesh: P1LayerMesh) -> _ValidatedMesh:
    polygons, bases = _triangle_polygons(
        mesh.node_xy_m,
        mesh.triangles,
        layer_id=mesh.layer_id,
    )
    tree = STRtree(polygons)
    candidate_count = 0
    extent = np.ptp(mesh.node_xy_m, axis=0)
    length_tolerance = 512.0 * _FLOAT_EPS * max(float(np.max(extent)), 1.0e-30)
    for left_index, left in enumerate(polygons):
        candidates = sorted(int(item) for item in tree.query(left))
        for right_index in candidates:
            if right_index <= left_index:
                continue
            candidate_count += 1
            if candidate_count > _MAX_SELF_CANDIDATES:
                raise TriFemGapError(
                    "MESH_WORK_LIMIT",
                    f"layer {mesh.layer_id!r} exceeds self-candidate bound",
                )
            right = polygons[right_index]
            intersection = left.intersection(right)
            area_scale = min(float(left.area), float(right.area))
            area_tolerance = 2.0e-11 * area_scale
            if float(intersection.area) > area_tolerance:
                raise TriFemGapError(
                    "MESH_INTERIOR_OVERLAP",
                    f"layer {mesh.layer_id!r} triangles overlap in area",
                )
            if float(intersection.length) <= length_tolerance:
                continue
            lines = (
                (intersection,)
                if intersection.geom_type == "LineString"
                else tuple(
                    item
                    for item in getattr(intersection, "geoms", ())
                    if item.geom_type == "LineString" and item.length > length_tolerance
                )
            )
            if len(lines) != 1:
                raise TriFemGapError(
                    "MESH_NONCONFORMING",
                    f"layer {mesh.layer_id!r} has a nonconforming shared boundary",
                )
            coordinates = tuple(lines[0].coords)
            if len(coordinates) < 2:
                raise TriFemGapError(
                    "MESH_NONCONFORMING",
                    f"layer {mesh.layer_id!r} has an invalid shared edge",
                )
            start, stop = coordinates[0], coordinates[-1]
            if not (
                _is_full_edge(left, start, stop, length_tolerance)
                and _is_full_edge(right, start, stop, length_tolerance)
            ):
                raise TriFemGapError(
                    "MESH_NONCONFORMING",
                    f"layer {mesh.layer_id!r} contains a hanging-node edge",
                )
    domain = unary_union(polygons)
    if not isinstance(domain, Polygon):
        raise TriFemGapError(
            "MESH_DISCONNECTED",
            f"layer {mesh.layer_id!r} must form one connected polygon",
        )
    if len(domain.interiors):
        raise TriFemGapError(
            "MESH_HOLE_UNSUPPORTED",
            f"layer {mesh.layer_id!r} mesh domain contains a hole",
        )
    triangle_area = float(sum(item.area for item in polygons))
    if not np.isclose(float(domain.area), triangle_area, rtol=2.0e-11, atol=0.0):
        raise TriFemGapError(
            "MESH_COVERAGE_INVALID",
            f"layer {mesh.layer_id!r} triangles do not cover their domain exactly",
        )
    return _ValidatedMesh(
        mesh=mesh,
        polygons=polygons,
        bases=bases,
        domain=domain,
        maximum_condition_number=max(item.condition_number for item in bases),
    )


@dataclass(frozen=True, slots=True)
class P1LayerMesh:
    """Immutable explicit P1 mesh view with source-owner provenance."""

    layer_id: str
    node_xy_m: NDArray[np.float64]
    triangles: NDArray[np.int64]
    triangle_owner_ids: tuple[str, ...]
    mesh_identity_sha256: str
    content_identity_sha256: str = field(init=False, default="")

    def __post_init__(self) -> None:
        layer_id = _required_id(self.layer_id, label="layer_id")
        mesh_identity = _sha256_id(
            self.mesh_identity_sha256,
            label="mesh_identity_sha256",
        )
        nodes = _readonly_float(self.node_xy_m)
        triangles = _readonly_int(self.triangles)
        owners = tuple(
            _required_id(value, label=f"triangle_owner_ids[{index}]")
            for index, value in enumerate(self.triangle_owner_ids)
        )
        if nodes.ndim != 2 or nodes.shape[1:] != (2,) or len(nodes) < 3:
            raise TriFemGapError("MESH_INVALID", "node_xy_m must have shape (N, 2)")
        if len(nodes) > _MAX_MESH_NODES or not np.all(np.isfinite(nodes)):
            raise TriFemGapError(
                "MESH_INVALID",
                f"node count must be <= {_MAX_MESH_NODES} and coordinates finite",
            )
        if triangles.ndim != 2 or triangles.shape[1:] != (3,) or not len(triangles):
            raise TriFemGapError("MESH_INVALID", "triangles must have shape (M, 3)")
        if len(triangles) > _MAX_MESH_TRIANGLES:
            raise TriFemGapError(
                "MESH_INVALID",
                f"triangle count must be <= {_MAX_MESH_TRIANGLES}",
            )
        if np.any(triangles < 0) or np.any(triangles >= len(nodes)):
            raise TriFemGapError("MESH_INVALID", "triangle node index is out of range")
        if any(len(set(map(int, row))) != 3 for row in triangles):
            raise TriFemGapError("MESH_INVALID", "a triangle repeats a node")
        if len(owners) != len(triangles):
            raise TriFemGapError(
                "OWNER_COVERAGE_INVALID",
                "triangle_owner_ids must cover every triangle exactly once",
            )
        used = np.unique(triangles)
        if len(used) != len(nodes) or not np.array_equal(used, np.arange(len(nodes))):
            raise TriFemGapError(
                "NODE_COVERAGE_INVALID",
                "every mesh node must belong to at least one triangle",
            )
        identity = _content_identity(
            layer_id,
            mesh_identity,
            nodes,
            triangles,
            owners,
        )
        object.__setattr__(self, "layer_id", layer_id)
        object.__setattr__(self, "mesh_identity_sha256", mesh_identity)
        object.__setattr__(self, "node_xy_m", nodes)
        object.__setattr__(self, "triangles", triangles)
        object.__setattr__(self, "triangle_owner_ids", owners)
        object.__setattr__(self, "content_identity_sha256", identity)

    def verify_current_identity(self) -> None:
        """Reject a stale identity even if an array write flag was bypassed."""

        expected = _content_identity(
            self.layer_id,
            self.mesh_identity_sha256,
            self.node_xy_m,
            self.triangles,
            self.triangle_owner_ids,
        )
        if expected != self.content_identity_sha256:
            raise TriFemGapError(
                "MESH_IDENTITY_MISMATCH",
                f"layer {self.layer_id!r} mesh content changed after construction",
            )


def _snapshot_mesh(mesh: P1LayerMesh) -> P1LayerMesh:
    mesh.verify_current_identity()
    snapshot = P1LayerMesh(
        layer_id=mesh.layer_id,
        node_xy_m=mesh.node_xy_m,
        triangles=mesh.triangles,
        triangle_owner_ids=mesh.triangle_owner_ids,
        mesh_identity_sha256=mesh.mesh_identity_sha256,
    )
    if snapshot.content_identity_sha256 != mesh.content_identity_sha256:
        raise TriFemGapError(
            "MESH_IDENTITY_MISMATCH",
            f"layer {mesh.layer_id!r} changed while it was being snapshotted",
        )
    return snapshot


@dataclass(frozen=True, slots=True)
class TriFemGapSourceEvidence:
    """Required source/material/layer-pair provenance for one gap."""

    source_model_identity_sha256: str
    material_manifest_sha256: str
    layer_pair_identity_sha256: str
    upper_layer_id: str
    lower_layer_id: str

    def __post_init__(self) -> None:
        source = _sha256_id(
            self.source_model_identity_sha256,
            label="source_model_identity_sha256",
        )
        material = _sha256_id(
            self.material_manifest_sha256,
            label="material_manifest_sha256",
        )
        pair = _sha256_id(
            self.layer_pair_identity_sha256,
            label="layer_pair_identity_sha256",
        )
        upper = _required_id(self.upper_layer_id, label="upper_layer_id")
        lower = _required_id(self.lower_layer_id, label="lower_layer_id")
        if upper.casefold() == lower.casefold():
            raise TriFemGapError(
                "IDENTITY_INVALID",
                "source evidence upper and lower layer IDs must differ",
            )
        object.__setattr__(self, "source_model_identity_sha256", source)
        object.__setattr__(self, "material_manifest_sha256", material)
        object.__setattr__(self, "layer_pair_identity_sha256", pair)
        object.__setattr__(self, "upper_layer_id", upper)
        object.__setattr__(self, "lower_layer_id", lower)


def _diagnostic_count(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise TriFemGapError("DIAGNOSTICS_INVALID", f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TriFemGapError(
            "DIAGNOSTICS_INVALID",
            f"{label} must be an integer",
        ) from exc
    if result != value or result < 0:
        raise TriFemGapError(
            "DIAGNOSTICS_INVALID",
            f"{label} must be a nonnegative integer",
        )
    return result


@dataclass(frozen=True, slots=True)
class TriFemGapDiagnostics:
    """Bounded deterministic integration evidence."""

    candidate_pair_count: int
    positive_pair_count: int
    fan_triangle_count: int
    quadrature_point_count: int
    triplet_entry_count: int
    triplet_entry_limit: int
    peak_chunk_triplets: int
    temporary_byte_limit: int
    overlap_component_count: int
    overlap_hole_count: int
    matrix_nnz: int
    minimum_quadrature_weight_m2: float
    maximum_row_sum_abs_f: float
    minimum_eigenvalue_f: float | None
    maximum_mesh_condition_number: float
    maximum_fan_condition_number: float

    def __post_init__(self) -> None:
        counts = {
            field_name: _diagnostic_count(getattr(self, field_name), label=field_name)
            for field_name in (
                "candidate_pair_count",
                "positive_pair_count",
                "fan_triangle_count",
                "quadrature_point_count",
                "triplet_entry_count",
                "triplet_entry_limit",
                "peak_chunk_triplets",
                "temporary_byte_limit",
                "overlap_component_count",
                "overlap_hole_count",
                "matrix_nnz",
            )
        }
        if counts["positive_pair_count"] > counts["candidate_pair_count"]:
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "positive triangle-pair count exceeds candidate count",
            )
        if counts["fan_triangle_count"] < counts["positive_pair_count"]:
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "fan triangle count is smaller than positive pair count",
            )
        if counts["quadrature_point_count"] != 3 * counts["fan_triangle_count"]:
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "quadrature count differs from the degree-two rule",
            )
        if counts["triplet_entry_count"] != (
            _PAIR_TRIPLET_COUNT * counts["positive_pair_count"]
        ):
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "triplet count differs from pair-local assembly",
            )
        if counts["triplet_entry_count"] > counts["triplet_entry_limit"]:
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "triplet count exceeds its declared limit",
            )
        if counts["peak_chunk_triplets"] > counts["triplet_entry_count"]:
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "peak chunk exceeds total triplet count",
            )
        for field_name in counts:
            object.__setattr__(self, field_name, counts[field_name])
        minimum_weight = _positive(
            self.minimum_quadrature_weight_m2,
            label="minimum_quadrature_weight_m2",
        )
        row_sum = float(self.maximum_row_sum_abs_f)
        if not isfinite(row_sum) or row_sum < 0.0:
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "maximum_row_sum_abs_f must be finite and nonnegative",
            )
        minimum_eigenvalue = self.minimum_eigenvalue_f
        if minimum_eigenvalue is not None:
            minimum_eigenvalue = float(minimum_eigenvalue)
            if not isfinite(minimum_eigenvalue):
                raise TriFemGapError(
                    "DIAGNOSTICS_INVALID",
                    "minimum_eigenvalue_f must be finite or None",
                )
        mesh_condition = _positive(
            self.maximum_mesh_condition_number,
            label="maximum_mesh_condition_number",
        )
        fan_condition = _positive(
            self.maximum_fan_condition_number,
            label="maximum_fan_condition_number",
        )
        object.__setattr__(self, "minimum_quadrature_weight_m2", minimum_weight)
        object.__setattr__(self, "maximum_row_sum_abs_f", row_sum)
        object.__setattr__(self, "minimum_eigenvalue_f", minimum_eigenvalue)
        object.__setattr__(self, "maximum_mesh_condition_number", mesh_condition)
        object.__setattr__(self, "maximum_fan_condition_number", fan_condition)


@dataclass(frozen=True, slots=True)
class TriFemGapOperator:
    """Immutable exact P1 capacitance projection across one adjacent gap."""

    upper_mesh: P1LayerMesh
    lower_mesh: P1LayerMesh
    source_evidence: TriFemGapSourceEvidence
    capacitance_matrix_f: csc_matrix
    source_capacitance_f: float
    relative_permittivity: float
    separation_m: float
    overlap_area_m2: float
    identity_sha256: str
    identity_manifest: Mapping[str, object]
    diagnostics: TriFemGapDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.upper_mesh, P1LayerMesh) or not isinstance(
            self.lower_mesh,
            P1LayerMesh,
        ):
            raise TriFemGapError("MESH_INVALID", "operator meshes have wrong type")
        self.upper_mesh.verify_current_identity()
        self.lower_mesh.verify_current_identity()
        if not isinstance(self.source_evidence, TriFemGapSourceEvidence):
            raise TriFemGapError(
                "SOURCE_EVIDENCE_INVALID",
                "operator source evidence has wrong type",
            )
        if not isinstance(self.diagnostics, TriFemGapDiagnostics):
            raise TriFemGapError(
                "DIAGNOSTICS_INVALID",
                "operator diagnostics have wrong type",
            )
        matrix = _readonly_csc(self.capacitance_matrix_f)
        expected = len(self.upper_mesh.node_xy_m) + len(self.lower_mesh.node_xy_m)
        if matrix.shape != (expected, expected):
            raise TriFemGapError("MATRIX_INVALID", "matrix shape differs from meshes")
        source_capacitance = _positive(
            self.source_capacitance_f,
            label="source_capacitance_f",
        )
        relative_permittivity = _positive(
            self.relative_permittivity,
            label="relative_permittivity",
        )
        separation = _positive(self.separation_m, label="separation_m")
        overlap_area = _positive(self.overlap_area_m2, label="overlap_area_m2")
        identity = _sha256_id(self.identity_sha256, label="operator identity_sha256")
        object.__setattr__(self, "capacitance_matrix_f", matrix)
        object.__setattr__(self, "source_capacitance_f", source_capacitance)
        object.__setattr__(self, "relative_permittivity", relative_permittivity)
        object.__setattr__(self, "separation_m", separation)
        object.__setattr__(self, "overlap_area_m2", overlap_area)
        object.__setattr__(self, "identity_sha256", identity)
        _verify_operator_current(self)
        object.__setattr__(
            self,
            "identity_manifest",
            MappingProxyType(dict(self.identity_manifest)),
        )

    def verify_current_identity(self) -> None:
        """Recheck current sparse buffers and provenance before trusted use."""

        _verify_operator_current(self)

    def equipotential_recollapse_f(self) -> NDArray[np.float64]:
        """Return the exact two-electrode capacitance stamp induced by the mesh."""

        self.verify_current_identity()
        upper_count = len(self.upper_mesh.node_xy_m)
        projection = np.zeros((self.capacitance_matrix_f.shape[0], 2), dtype=np.float64)
        projection[:upper_count, 0] = 1.0
        projection[upper_count:, 1] = 1.0
        result = np.asarray(
            projection.T @ self.capacitance_matrix_f @ projection,
            dtype=np.float64,
        )
        result.setflags(write=False)
        return result


def _canonical_polygon_vertices(polygon: Polygon) -> tuple[tuple[float, float], ...]:
    if polygon.is_empty or not polygon.is_valid or len(polygon.interiors):
        raise TriFemGapError("OVERLAP_GEOMETRY_INVALID", "intersection polygon is invalid")
    normal = normalize(polygon)
    vertices = [
        (0.0 if x == 0.0 else float(x), 0.0 if y == 0.0 else float(y))
        for x, y in tuple(normal.exterior.coords)[:-1]
    ]
    if len(vertices) < 3 or len(set(vertices)) != len(vertices):
        raise TriFemGapError(
            "OVERLAP_GEOMETRY_INVALID",
            "intersection has fewer than three unique vertices",
        )
    start = min(range(len(vertices)), key=vertices.__getitem__)
    rotated = vertices[start:] + vertices[:start]
    signed_twice_area = sum(
        left[0] * right[1] - left[1] * right[0]
        for left, right in zip(rotated, rotated[1:] + rotated[:1])
    )
    if signed_twice_area < 0.0:
        rotated = [rotated[0], *reversed(rotated[1:])]
    return tuple(rotated)


def _polygon_parts(geometry: object) -> tuple[Polygon, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,) if geometry.area > 0.0 else ()
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        return tuple(
            item
            for item in geometry.geoms
            if isinstance(item, Polygon) and item.area > 0.0
        )
    return ()


@dataclass(frozen=True, slots=True)
class _FanTriangle:
    points: NDArray[np.float64]
    area_m2: float
    condition_number: float


def _fan_triangles(polygon: Polygon) -> tuple[_FanTriangle, ...]:
    vertices = _canonical_polygon_vertices(polygon)
    origin = np.asarray(vertices[0], dtype=np.float64)
    output: list[_FanTriangle] = []
    for index in range(1, len(vertices) - 1):
        triangle = np.asarray(
            (origin, vertices[index], vertices[index + 1]),
            dtype=np.float64,
        )
        first_edge = triangle[1] - triangle[0]
        second_edge = triangle[2] - triangle[0]
        twice_area = float(
            first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0]
        )
        if twice_area <= 0.0:
            raise TriFemGapError(
                "OVERLAP_FAN_INVALID",
                "convex intersection fan contains a non-positive triangle",
            )
        basis = _affine_basis(
            triangle,
            error_code="QUADRATURE_UNSTABLE",
            label="overlap fan triangle",
        )
        output.append(
            _FanTriangle(
                points=_readonly_float(triangle),
                area_m2=0.5 * twice_area,
                condition_number=basis.condition_number,
            )
        )
    return tuple(output)


def _update_with_matrix_bytes(digest: object, matrix: csc_matrix) -> None:
    update = getattr(digest, "update")
    update(b"csc-data-f8-le\0")
    update(_canonical_bytes(matrix.data, "<f8"))
    update(b"csc-indices-i8-le\0")
    update(_canonical_bytes(matrix.indices, "<i8"))
    update(b"csc-indptr-i8-le\0")
    update(_canonical_bytes(matrix.indptr, "<i8"))


def _operator_identity(
    manifest: Mapping[str, object],
    matrix: csc_matrix,
) -> tuple[str, Mapping[str, object]]:
    matrix_digest = sha256()
    _update_with_matrix_bytes(matrix_digest, matrix)
    complete = dict(manifest)
    complete["matrix_sha256"] = matrix_digest.hexdigest()
    digest = sha256(_canonical_json(complete))
    _update_with_matrix_bytes(digest, matrix)
    return digest.hexdigest(), complete


def _diagnostics_manifest(diagnostics: TriFemGapDiagnostics) -> Mapping[str, object]:
    return asdict(diagnostics)


def _operator_manifest(
    *,
    upper_mesh: P1LayerMesh,
    lower_mesh: P1LayerMesh,
    source_evidence: TriFemGapSourceEvidence,
    source_capacitance_f: float,
    relative_permittivity: float,
    separation_m: float,
    overlap_area_m2: float,
    matrix: csc_matrix,
    diagnostics: TriFemGapDiagnostics,
) -> Mapping[str, object]:
    return {
        "compiler_id": TRI_FEM_GAP_COMPILER_ID,
        "integration_id": TRI_FEM_GAP_INTEGRATION_ID,
        "shapely_version": shapely.__version__,
        "geos_version": shapely.geos_version_string,
        "epsilon_0_f_per_m": EPSILON_0_F_PER_M,
        "basis_relative_error_budget": _BASIS_RELATIVE_ERROR_BUDGET,
        "upper_layer_id": upper_mesh.layer_id,
        "lower_layer_id": lower_mesh.layer_id,
        "upper_mesh_identity_sha256": upper_mesh.mesh_identity_sha256,
        "lower_mesh_identity_sha256": lower_mesh.mesh_identity_sha256,
        "upper_mesh_content_sha256": upper_mesh.content_identity_sha256,
        "lower_mesh_content_sha256": lower_mesh.content_identity_sha256,
        "source_model_identity_sha256": (
            source_evidence.source_model_identity_sha256
        ),
        "material_manifest_sha256": source_evidence.material_manifest_sha256,
        "layer_pair_identity_sha256": source_evidence.layer_pair_identity_sha256,
        "source_capacitance_f": source_capacitance_f,
        "relative_permittivity": relative_permittivity,
        "separation_m": separation_m,
        "overlap_area_m2": overlap_area_m2,
        "matrix_shape": matrix.shape,
        "matrix_nnz": matrix.nnz,
        "unit_contract": {
            "node_xy": "m",
            "capacitance": "F",
            "separation": "m",
            "area": "m^2",
            "relative_permittivity": "1",
        },
        "diagnostics": _diagnostics_manifest(diagnostics),
    }


def _matrix_properties(
    matrix: csc_matrix,
    *,
    source_capacitance: float,
    upper_count: int,
) -> tuple[float, float | None]:
    if not np.all(np.isfinite(matrix.data)):
        raise TriFemGapError("MATRIX_INVALID", "capacitance matrix is non-finite")
    difference = matrix - matrix.T
    symmetry_error = float(np.max(np.abs(difference.data), initial=0.0))
    matrix_scale = max(
        float(np.max(np.abs(matrix.data), initial=0.0)),
        source_capacitance,
    )
    if symmetry_error > _MATRIX_RELATIVE_TOLERANCE * matrix_scale:
        raise TriFemGapError(
            "MATRIX_NONRECIPROCAL",
            "capacitance matrix is not symmetric",
        )
    row_sums = np.asarray(matrix.sum(axis=1)).ravel()
    maximum_row_sum = float(np.max(np.abs(row_sums), initial=0.0))
    if maximum_row_sum > _MATRIX_RELATIVE_TOLERANCE * matrix_scale:
        raise TriFemGapError(
            "MATRIX_NOT_FLOATING",
            "capacitance matrix row sums are nonzero",
        )
    minimum_eigenvalue: float | None = None
    if matrix.shape[0] <= _PSD_DENSE_LIMIT:
        dense = np.asarray(matrix.toarray(), dtype=np.float64)
        minimum_eigenvalue = float(np.linalg.eigvalsh(0.5 * (dense + dense.T))[0])
        if minimum_eigenvalue < -_MATRIX_RELATIVE_TOLERANCE * matrix_scale:
            raise TriFemGapError("MATRIX_NON_PSD", "capacitance matrix is indefinite")
    projection = np.zeros((matrix.shape[0], 2), dtype=np.float64)
    projection[:upper_count, 0] = 1.0
    projection[upper_count:, 1] = 1.0
    recollapsed = np.asarray(projection.T @ matrix @ projection)
    expected_stamp = source_capacitance * np.asarray(((1.0, -1.0), (-1.0, 1.0)))
    if not np.allclose(recollapsed, expected_stamp, rtol=2.0e-12, atol=0.0):
        raise TriFemGapError(
            "RECOLLAPSE_INVALID",
            "equipotential recollapse does not preserve source capacitance",
        )
    return maximum_row_sum, minimum_eigenvalue


def _same_float(left: float, right: float) -> bool:
    return bool(np.isclose(left, right, rtol=2.0e-15, atol=0.0))


def _verify_operator_current(operator: TriFemGapOperator) -> None:
    operator.upper_mesh.verify_current_identity()
    operator.lower_mesh.verify_current_identity()
    if operator.upper_mesh.layer_id.casefold() == operator.lower_mesh.layer_id.casefold():
        raise TriFemGapError(
            "IDENTITY_INVALID",
            "upper and lower layer IDs differ only by case",
        )
    evidence = operator.source_evidence
    if (
        evidence.upper_layer_id.casefold() != operator.upper_mesh.layer_id.casefold()
        or evidence.lower_layer_id.casefold()
        != operator.lower_mesh.layer_id.casefold()
    ):
        raise TriFemGapError(
            "SOURCE_EVIDENCE_MISMATCH",
            "source evidence layer pair differs from operator meshes",
        )
    expected_capacitance = (
        EPSILON_0_F_PER_M
        * operator.relative_permittivity
        * operator.overlap_area_m2
        / operator.separation_m
    )
    if not np.isclose(
        operator.source_capacitance_f,
        expected_capacitance,
        rtol=_SOURCE_RELATIVE_TOLERANCE,
        atol=0.0,
    ):
        raise TriFemGapError(
            "SOURCE_CAPACITANCE_MISMATCH",
            "operator source capacitance is stale",
        )
    maximum_row_sum, minimum_eigenvalue = _matrix_properties(
        operator.capacitance_matrix_f,
        source_capacitance=operator.source_capacitance_f,
        upper_count=len(operator.upper_mesh.node_xy_m),
    )
    diagnostics = operator.diagnostics
    if diagnostics.matrix_nnz != operator.capacitance_matrix_f.nnz:
        raise TriFemGapError(
            "DIAGNOSTICS_INVALID",
            "diagnostic matrix_nnz differs from the matrix",
        )
    if not _same_float(diagnostics.maximum_row_sum_abs_f, maximum_row_sum):
        raise TriFemGapError(
            "DIAGNOSTICS_INVALID",
            "diagnostic row sum differs from the matrix",
        )
    if minimum_eigenvalue is None:
        eigenvalue_matches = diagnostics.minimum_eigenvalue_f is None
    else:
        eigenvalue_matches = (
            diagnostics.minimum_eigenvalue_f is not None
            and _same_float(diagnostics.minimum_eigenvalue_f, minimum_eigenvalue)
        )
    if not eigenvalue_matches:
        raise TriFemGapError(
            "DIAGNOSTICS_INVALID",
            "diagnostic minimum eigenvalue differs from the matrix",
        )
    if diagnostics.overlap_component_count != 1 or diagnostics.overlap_hole_count != 0:
        raise TriFemGapError(
            "DIAGNOSTICS_INVALID",
            "operator overlap diagnostics are not production-eligible",
        )
    expected_manifest = _operator_manifest(
        upper_mesh=operator.upper_mesh,
        lower_mesh=operator.lower_mesh,
        source_evidence=evidence,
        source_capacitance_f=operator.source_capacitance_f,
        relative_permittivity=operator.relative_permittivity,
        separation_m=operator.separation_m,
        overlap_area_m2=operator.overlap_area_m2,
        matrix=operator.capacitance_matrix_f,
        diagnostics=diagnostics,
    )
    expected_identity, complete_manifest = _operator_identity(
        expected_manifest,
        operator.capacitance_matrix_f,
    )
    if not isinstance(operator.identity_manifest, Mapping):
        raise TriFemGapError(
            "OPERATOR_MANIFEST_MISMATCH",
            "operator identity manifest is not a mapping",
        )
    try:
        manifest_matches = _canonical_json(dict(operator.identity_manifest)) == (
            _canonical_json(complete_manifest)
        )
    except (TypeError, ValueError) as exc:
        raise TriFemGapError(
            "OPERATOR_MANIFEST_MISMATCH",
            "operator identity manifest is not canonical JSON",
        ) from exc
    if not manifest_matches:
        raise TriFemGapError(
            "OPERATOR_MANIFEST_MISMATCH",
            "operator identity manifest is stale",
        )
    if operator.identity_sha256 != expected_identity:
        raise TriFemGapError(
            "OPERATOR_IDENTITY_MISMATCH",
            "operator identity is stale",
        )


class _ChunkedTripletAssembly:
    """Bounded numeric COO staging without unbounded Python object lists."""

    def __init__(
        self,
        *,
        node_count: int,
        nnz_bound: int,
        triplet_bound: int,
        temporary_byte_bound: int,
    ) -> None:
        capacity = min(
            _MAX_CHUNK_TRIPLETS,
            triplet_bound,
            temporary_byte_bound // _TRIPLET_TEMPORARY_BYTES,
        )
        if capacity < _PAIR_TRIPLET_COUNT:
            raise TriFemGapError(
                "WORK_BOUND_INVALID",
                "max_temporary_bytes cannot hold one pair-local contribution",
            )
        self.node_count = node_count
        self.nnz_bound = nnz_bound
        self.triplet_bound = triplet_bound
        self.capacity = capacity
        self.rows = np.empty(capacity, dtype=np.int64)
        self.columns = np.empty(capacity, dtype=np.int64)
        self.values = np.empty(capacity, dtype=np.float64)
        self.used = 0
        self.total = 0
        self.peak = 0
        self.matrix: csc_matrix | None = None

    def append_pair(
        self,
        global_indices: NDArray[np.int64],
        local_matrix: NDArray[np.float64],
    ) -> None:
        if self.total + _PAIR_TRIPLET_COUNT > self.triplet_bound:
            raise TriFemGapError(
                "ASSEMBLY_WORK_LIMIT",
                f"pair-local triplets exceed {self.triplet_bound}",
            )
        diagonal_lower_bound = len(np.unique(global_indices))
        if diagonal_lower_bound > self.nnz_bound:
            raise TriFemGapError(
                "NNZ_WORK_LIMIT",
                "one positive triangle pair already exceeds max_nnz",
            )
        if self.used + _PAIR_TRIPLET_COUNT > self.capacity:
            self.flush()
        stop = self.used + _PAIR_TRIPLET_COUNT
        self.rows[self.used : stop] = np.repeat(global_indices, 6)
        self.columns[self.used : stop] = np.tile(global_indices, 6)
        self.values[self.used : stop] = local_matrix.ravel(order="C")
        self.used = stop
        self.total += _PAIR_TRIPLET_COUNT
        self.peak = max(self.peak, self.used)

    def flush(self) -> None:
        if not self.used:
            return
        chunk = coo_matrix(
            (
                self.values[: self.used],
                (self.rows[: self.used], self.columns[: self.used]),
            ),
            shape=(self.node_count, self.node_count),
            dtype=np.float64,
        ).tocsc()
        chunk.sum_duplicates()
        chunk.sort_indices()
        chunk.eliminate_zeros()
        if self.matrix is None:
            combined = chunk
        else:
            combined = csc_matrix(self.matrix + chunk)
            combined.sum_duplicates()
            combined.sort_indices()
            combined.eliminate_zeros()
        if combined.nnz > self.nnz_bound:
            raise TriFemGapError(
                "NNZ_WORK_LIMIT",
                f"assembled matrix has {combined.nnz} nonzeros; "
                f"max_nnz is {self.nnz_bound}",
            )
        self.matrix = combined
        self.used = 0

    def finish(self) -> csc_matrix:
        self.flush()
        if self.matrix is None:
            raise TriFemGapError(
                "OVERLAP_MISSING",
                "mesh domains have no positive-area overlap",
            )
        return self.matrix


def compile_tri_fem_gap(
    upper_mesh: P1LayerMesh,
    lower_mesh: P1LayerMesh,
    *,
    source_evidence: TriFemGapSourceEvidence,
    source_capacitance_f: float,
    relative_permittivity: float,
    separation_m: float,
    max_candidate_pairs: int = _DEFAULT_MAX_CANDIDATE_PAIRS,
    max_fan_triangles: int = _DEFAULT_MAX_FAN_TRIANGLES,
    max_nnz: int = _DEFAULT_MAX_NNZ,
    max_triplet_entries: int = _DEFAULT_MAX_TRIPLET_ENTRIES,
    max_temporary_bytes: int = _DEFAULT_MAX_TEMPORARY_BYTES,
) -> TriFemGapOperator:
    """Compile an exact sparse P1 capacitance projection.

    The two mesh domains may differ, but their positive-area intersection must
    be one connected, hole-free polygon.  Scalar source capacitance must agree
    with ``epsilon_0 * epsilon_r * overlap_area / separation``.
    """

    if not isinstance(upper_mesh, P1LayerMesh) or not isinstance(
        lower_mesh,
        P1LayerMesh,
    ):
        raise TriFemGapError(
            "MESH_INVALID",
            "upper_mesh and lower_mesh must be P1LayerMesh",
        )
    upper_mesh = _snapshot_mesh(upper_mesh)
    lower_mesh = _snapshot_mesh(lower_mesh)
    if upper_mesh.layer_id.casefold() == lower_mesh.layer_id.casefold():
        raise TriFemGapError("IDENTITY_INVALID", "upper and lower layer IDs must differ")
    if not isinstance(source_evidence, TriFemGapSourceEvidence):
        raise TriFemGapError(
            "SOURCE_EVIDENCE_INVALID",
            "source_evidence must be TriFemGapSourceEvidence",
        )
    if (
        source_evidence.upper_layer_id.casefold() != upper_mesh.layer_id.casefold()
        or source_evidence.lower_layer_id.casefold()
        != lower_mesh.layer_id.casefold()
    ):
        raise TriFemGapError(
            "SOURCE_EVIDENCE_MISMATCH",
            "source evidence layer pair differs from the supplied meshes",
        )
    source_capacitance = _positive(
        source_capacitance_f,
        label="source_capacitance_f",
    )
    relative_permittivity_value = _positive(
        relative_permittivity,
        label="relative_permittivity",
    )
    separation = _positive(separation_m, label="separation_m")
    candidate_bound = _work_bound(
        max_candidate_pairs,
        label="max_candidate_pairs",
        maximum=_DEFAULT_MAX_CANDIDATE_PAIRS,
    )
    fan_bound = _work_bound(
        max_fan_triangles,
        label="max_fan_triangles",
        maximum=_DEFAULT_MAX_FAN_TRIANGLES,
    )
    nnz_bound = _work_bound(
        max_nnz,
        label="max_nnz",
        maximum=_DEFAULT_MAX_NNZ,
    )
    triplet_bound = _work_bound(
        max_triplet_entries,
        label="max_triplet_entries",
        maximum=_MAX_TRIPLET_ENTRIES,
    )
    temporary_byte_bound = _work_bound(
        max_temporary_bytes,
        label="max_temporary_bytes",
        maximum=_MAX_TEMPORARY_BYTES,
    )
    upper_validated = _validate_mesh_topology(upper_mesh)
    lower_validated = _validate_mesh_topology(lower_mesh)
    overlap_geometry = upper_validated.domain.intersection(lower_validated.domain)
    overlap_parts = _polygon_parts(overlap_geometry)
    if not overlap_parts:
        raise TriFemGapError(
            "OVERLAP_MISSING",
            "mesh domains have no positive-area overlap",
        )
    if len(overlap_parts) != 1:
        raise TriFemGapError(
            "OVERLAP_DISCONNECTED",
            f"overlap must be one polygon; found {len(overlap_parts)} components",
        )
    overlap = overlap_parts[0]
    if len(overlap.interiors):
        raise TriFemGapError(
            "OVERLAP_HOLE_UNSUPPORTED",
            "overlap polygon contains one or more holes",
        )
    overlap_area = float(overlap.area)
    expected_capacitance = (
        EPSILON_0_F_PER_M
        * relative_permittivity_value
        * overlap_area
        / separation
    )
    if not np.isclose(
        source_capacitance,
        expected_capacitance,
        rtol=_SOURCE_RELATIVE_TOLERANCE,
        atol=0.0,
    ):
        relative_error = abs(source_capacitance - expected_capacitance) / (
            expected_capacitance
        )
        raise TriFemGapError(
            "SOURCE_CAPACITANCE_MISMATCH",
            "source capacitance differs from epsilon*A/d "
            f"by {relative_error:.6g} relative",
        )
    upper_count = len(upper_mesh.node_xy_m)
    node_count = upper_count + len(lower_mesh.node_xy_m)
    assembly = _ChunkedTripletAssembly(
        node_count=node_count,
        nnz_bound=nnz_bound,
        triplet_bound=triplet_bound,
        temporary_byte_bound=temporary_byte_bound,
    )
    density = source_capacitance / overlap_area
    lower_tree = STRtree(lower_validated.polygons)
    candidate_count = 0
    positive_pair_count = 0
    fan_count = 0
    quadrature_count = 0
    integrated_area = 0.0
    area_compensation = 0.0
    minimum_weight = float("inf")
    maximum_fan_condition = 0.0
    quadrature_barycentric = (
        np.asarray((2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0)),
        np.asarray((1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0)),
        np.asarray((1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0)),
    )
    upper_indices = sorted(
        range(len(upper_validated.polygons)),
        key=lambda index: (upper_mesh.triangle_owner_ids[index], index),
    )
    for upper_index in upper_indices:
        upper_polygon = upper_validated.polygons[upper_index]
        lower_indices = sorted(
            (int(item) for item in lower_tree.query(upper_polygon)),
            key=lambda index: (lower_mesh.triangle_owner_ids[index], index),
        )
        for lower_index in lower_indices:
            candidate_count += 1
            if candidate_count > candidate_bound:
                raise TriFemGapError(
                    "CANDIDATE_WORK_LIMIT",
                    f"triangle-pair candidates exceed {candidate_bound}",
                )
            intersection = upper_polygon.intersection(
                lower_validated.polygons[lower_index]
            )
            parts = _polygon_parts(intersection)
            if not parts:
                continue
            if len(parts) != 1:
                raise TriFemGapError(
                    "OVERLAP_GEOMETRY_INVALID",
                    "one convex triangle pair produced multiple overlap polygons",
                )
            polygon = parts[0]
            convex = polygon.convex_hull
            if not np.isclose(
                float(convex.area),
                float(polygon.area),
                rtol=2.0e-11,
                atol=0.0,
            ):
                raise TriFemGapError(
                    "OVERLAP_GEOMETRY_INVALID",
                    "triangle intersection is unexpectedly non-convex",
                )
            positive_pair_count += 1
            local_matrix = np.zeros((6, 6), dtype=np.float64)
            for fan in _fan_triangles(polygon):
                fan_count += 1
                if fan_count > fan_bound:
                    raise TriFemGapError(
                        "FAN_WORK_LIMIT",
                        f"overlap fan triangles exceed {fan_bound}",
                    )
                maximum_fan_condition = max(
                    maximum_fan_condition,
                    fan.condition_number,
                )
                y = fan.area_m2 - area_compensation
                new_area = integrated_area + y
                area_compensation = (new_area - integrated_area) - y
                integrated_area = new_area
                weight = fan.area_m2 / 3.0
                minimum_weight = min(minimum_weight, weight)
                for barycentric in quadrature_barycentric:
                    point = np.asarray(
                        barycentric @ fan.points,
                        dtype=np.float64,
                    )
                    upper_basis = upper_validated.bases[upper_index].evaluate(point)
                    lower_basis = lower_validated.bases[lower_index].evaluate(point)
                    psi = np.concatenate((upper_basis, -lower_basis))
                    local_matrix += weight * np.outer(psi, psi)
                    quadrature_count += 1
            global_indices = np.concatenate(
                (
                    upper_mesh.triangles[upper_index],
                    lower_mesh.triangles[lower_index] + upper_count,
                )
            )
            assembly.append_pair(
                np.asarray(global_indices, dtype=np.int64),
                density * local_matrix,
            )
    if not positive_pair_count or integrated_area <= 0.0:
        raise TriFemGapError(
            "OVERLAP_MISSING",
            "mesh domains have no positive-area overlap",
        )
    if not np.isclose(overlap_area, integrated_area, rtol=2.0e-10, atol=0.0):
        raise TriFemGapError(
            "OVERLAP_COVERAGE_INVALID",
            "triangle-pair integration does not cover overlap exactly once",
        )
    matrix = assembly.finish()
    matrix.sum_duplicates()
    matrix.sort_indices()
    matrix.eliminate_zeros()
    # COO duplicate reduction visits transposed entries in a different order.
    # Average the two roundoff-equivalent paths so reciprocity is byte exact.
    matrix = csc_matrix(0.5 * (matrix + matrix.T))
    matrix.sum_duplicates()
    matrix.sort_indices()
    matrix.eliminate_zeros()
    maximum_row_sum, minimum_eigenvalue = _matrix_properties(
        matrix,
        source_capacitance=source_capacitance,
        upper_count=upper_count,
    )
    matrix = _readonly_csc(matrix)
    diagnostics = TriFemGapDiagnostics(
        candidate_pair_count=candidate_count,
        positive_pair_count=positive_pair_count,
        fan_triangle_count=fan_count,
        quadrature_point_count=quadrature_count,
        triplet_entry_count=assembly.total,
        triplet_entry_limit=triplet_bound,
        peak_chunk_triplets=assembly.peak,
        temporary_byte_limit=temporary_byte_bound,
        overlap_component_count=1,
        overlap_hole_count=0,
        matrix_nnz=matrix.nnz,
        minimum_quadrature_weight_m2=minimum_weight,
        maximum_row_sum_abs_f=maximum_row_sum,
        minimum_eigenvalue_f=minimum_eigenvalue,
        maximum_mesh_condition_number=max(
            upper_validated.maximum_condition_number,
            lower_validated.maximum_condition_number,
        ),
        maximum_fan_condition_number=maximum_fan_condition,
    )
    manifest = _operator_manifest(
        upper_mesh=upper_mesh,
        lower_mesh=lower_mesh,
        source_evidence=source_evidence,
        source_capacitance_f=source_capacitance,
        relative_permittivity=relative_permittivity_value,
        separation_m=separation,
        overlap_area_m2=overlap_area,
        matrix=matrix,
        diagnostics=diagnostics,
    )
    identity, complete_manifest = _operator_identity(manifest, matrix)
    return TriFemGapOperator(
        upper_mesh=upper_mesh,
        lower_mesh=lower_mesh,
        source_evidence=source_evidence,
        capacitance_matrix_f=matrix,
        source_capacitance_f=source_capacitance,
        relative_permittivity=relative_permittivity_value,
        separation_m=separation,
        overlap_area_m2=overlap_area,
        identity_sha256=identity,
        identity_manifest=complete_manifest,
        diagnostics=diagnostics,
    )


__all__ = [
    "EPSILON_0_F_PER_M",
    "P1LayerMesh",
    "TRI_FEM_GAP_COMPILER_ID",
    "TRI_FEM_GAP_INTEGRATION_ID",
    "TriFemGapDiagnostics",
    "TriFemGapError",
    "TriFemGapOperator",
    "TriFemGapSourceEvidence",
    "compile_tri_fem_gap",
]
