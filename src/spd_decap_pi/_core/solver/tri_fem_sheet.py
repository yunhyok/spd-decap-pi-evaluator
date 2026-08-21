"""Source-derived triangular FEM sheet-conduction operator.

The low-level fixed-refinement compiler creates reciprocal floating sheet
stamps, but those results are deliberately production-ineligible.  The
convergence compiler compares finite-contact QoIs across refinements and is
the only API in this module that can issue a convergence attestation.

Only copper sheet resistance and finite-thickness internal impedance are
modeled.  External loop inductance, plane-pair cavity modes, and mutual
coupling remain outside this bounded core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite, pi
from types import MappingProxyType
from typing import Final, Literal, Mapping, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.sparse import csc_matrix, coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu
import shapely
from shapely import normalize
from shapely.geometry import Polygon
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree
from shapely.validation import explain_validity

try:  # Shapely 2.1 API; keep importing this dormant module safe on 2.0.
    from shapely import constrained_delaunay_triangles as _constrained_delaunay_triangles
except ImportError:  # pragma: no cover - exercised only in a Shapely 2.0 runtime.
    _constrained_delaunay_triangles = None


TRI_FEM_SHEET_COMPILER_ID: Final = "source-tri-fem-sheet-cdt-internal-z-v2"
MU_0_H_PER_M: Final = 4.0e-7 * pi
_MAX_REFINEMENT_LEVELS: Final = 8
_DEFAULT_MAX_NODES: Final = 250_000
_DEFAULT_MAX_TRIANGLES: Final = 500_000
_DEFAULT_MAX_CONTACTS: Final = 10_000
_DEFAULT_MAX_CONTACT_WORK: Final = 5_000_000
_DEFAULT_MAX_CONVERGENCE_CONTACTS: Final = 256
_DEFAULT_MAX_CONVERGENCE_WORK: Final = 5_000_000
_PSD_DENSE_CHECK_LIMIT: Final = 512
_FLOAT_EPS: Final = np.finfo(np.float64).eps
_ATTESTATION_ISSUERS: Final[dict[int, tuple[object, str]]] = {}
_CONTRACTION_ISSUER: Final = object()

SheetImpedanceModel = Literal["dc", "common_mode"]


class TriFemSheetError(ValueError):
    """Fail-closed geometry, material, mesh, or operator validation error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "TRI_FEM_SHEET_INVALID"
        super().__init__(f"{self.code}: {message}")


def _require_id(value: object, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise TriFemSheetError("IDENTITY_INVALID", f"{label} must be nonblank")
    return text


def _require_sha256(value: object, *, label: str) -> str:
    text = str(value).strip().casefold()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise TriFemSheetError("IDENTITY_INVALID", f"{label} must be SHA-256")
    return text


def _positive(value: object, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError("MATERIAL_INVALID", f"{label} must be numeric") from exc
    if not isfinite(result) or result <= 0.0:
        raise TriFemSheetError("MATERIAL_INVALID", f"{label} must be finite and > 0")
    return result


def _nonnegative(value: object, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError("CONVERGENCE_INVALID", f"{label} must be numeric") from exc
    if not isfinite(result) or result < 0.0:
        raise TriFemSheetError("CONVERGENCE_INVALID", f"{label} must be finite and >= 0")
    return result


def _bounded_integer(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        raise TriFemSheetError("MESH_BOUND_INVALID", f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError("MESH_BOUND_INVALID", f"{label} must be an integer") from exc
    if result != value or result < minimum or result > maximum:
        raise TriFemSheetError(
            "MESH_BOUND_INVALID",
            f"{label} must be in [{minimum}, {maximum}]",
        )
    return result


def _canonical_coordinate(value: float) -> float:
    result = float(value)
    return 0.0 if result == 0.0 else result


def _canonical_ring(coordinates: object) -> tuple[tuple[float, float], ...]:
    return tuple(
        (_canonical_coordinate(x), _canonical_coordinate(y))
        for x, y in coordinates  # type: ignore[misc]
    )


def _normalised_polygon(value: object, *, label: str) -> Polygon:
    if not isinstance(value, Polygon):
        raise TriFemSheetError("POLYGON_INVALID", f"{label} must be one Polygon")
    if value.has_z:
        raise TriFemSheetError("POLYGON_INVALID", f"{label} must be two-dimensional")
    if value.is_empty or not value.is_valid or value.area <= 0.0:
        raise TriFemSheetError(
            "POLYGON_INVALID",
            f"{label} is empty, zero-area, or invalid ({explain_validity(value)})",
        )
    bounds = np.asarray(value.bounds, dtype=np.float64)
    if bounds.shape != (4,) or not np.all(np.isfinite(bounds)):
        raise TriFemSheetError("POLYGON_INVALID", f"{label} coordinates must be finite")
    rebuilt = Polygon(
        _canonical_ring(value.exterior.coords),
        holes=tuple(_canonical_ring(ring.coords) for ring in value.interiors),
    )
    normalised = normalize(rebuilt)
    result = Polygon(
        _canonical_ring(normalised.exterior.coords),
        holes=tuple(_canonical_ring(ring.coords) for ring in normalised.interiors),
    )
    result = normalize(result)
    if result.is_empty or not result.is_valid or result.area <= 0.0:
        raise TriFemSheetError("POLYGON_INVALID", f"{label} cannot be normalised")
    return result


def _polygon_sha256(polygon: Polygon) -> str:
    return sha256(_normalised_polygon(polygon, label="identity polygon").wkb).hexdigest()


def _readonly_int_array(values: ArrayLike) -> NDArray[np.int64]:
    try:
        raw = np.asarray(values)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError("INDEX_INVALID", "indices must form one regular array") from exc
    checked: list[int] = []
    for value in raw.reshape(-1):
        scalar = value.item() if isinstance(value, np.generic) else value
        if isinstance(scalar, (bool, np.bool_, complex, np.complexfloating)):
            raise TriFemSheetError("INDEX_INVALID", "indices must be exact integers")
        try:
            integer = int(scalar)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TriFemSheetError("INDEX_INVALID", "indices must be exact integers") from exc
        try:
            exact = scalar == integer
        except Exception as exc:
            raise TriFemSheetError("INDEX_INVALID", "indices must be exact integers") from exc
        if not isinstance(exact, (bool, np.bool_)) or not bool(exact):
            raise TriFemSheetError("INDEX_INVALID", "fractional indices are forbidden")
        if integer < np.iinfo(np.int64).min or integer > np.iinfo(np.int64).max:
            raise TriFemSheetError("INDEX_INVALID", "an index exceeds int64 range")
        checked.append(integer)
    result = np.asarray(checked, dtype=np.int64).reshape(raw.shape).copy(order="C")
    result.setflags(write=False)
    return result


def _readonly_float_array(values: ArrayLike) -> NDArray[np.float64]:
    try:
        raw = np.asarray(values)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError(
            "MESH_INVALID",
            "mesh coordinates must form one regular array",
        ) from exc
    if np.iscomplexobj(raw):
        raise TriFemSheetError("MESH_INVALID", "mesh coordinates must be real")
    result = np.array(raw, dtype=np.float64, order="C", copy=True)
    result[result == 0.0] = 0.0
    result.setflags(write=False)
    return result


def _readonly_real_csc(matrix: object, *, label: str) -> csc_matrix:
    try:
        source = csc_matrix(matrix, copy=True)
    except Exception as exc:
        raise TriFemSheetError("STIFFNESS_INVALID", f"{label} is not sparse-compatible") from exc
    if np.iscomplexobj(source.data):
        if np.any(np.imag(source.data) != 0.0):
            raise TriFemSheetError("STIFFNESS_INVALID", f"{label} must be real")
        source = csc_matrix(
            (np.real(source.data), source.indices, source.indptr),
            shape=source.shape,
        )
    result = csc_matrix(source, dtype=np.float64, copy=True)
    result.sum_duplicates()
    result.sort_indices()
    result.eliminate_zeros()
    if not np.all(np.isfinite(result.data)):
        raise TriFemSheetError("STIFFNESS_INVALID", f"{label} must be finite")
    result.data[result.data == 0.0] = 0.0
    result.data.setflags(write=False)
    result.indices.setflags(write=False)
    result.indptr.setflags(write=False)
    return result


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError(
            "IDENTITY_INVALID",
            "identity manifest is not canonical JSON",
        ) from exc


def _array_sha256(values: NDArray[np.int64]) -> str:
    return sha256(np.asarray(values, dtype="<i8", order="C").tobytes(order="C")).hexdigest()


def _csc_sha256(matrix: csc_matrix) -> str:
    canonical = _readonly_real_csc(matrix, label="identity CSC")
    digest = sha256(_canonical_json({"shape": canonical.shape}))
    digest.update(np.asarray(canonical.data, dtype="<f8").tobytes(order="C"))
    digest.update(np.asarray(canonical.indices, dtype="<i8").tobytes(order="C"))
    digest.update(np.asarray(canonical.indptr, dtype="<i8").tobytes(order="C"))
    return digest.hexdigest()


def _float_matrix_sha256(values: ArrayLike, *, label: str) -> str:
    try:
        matrix = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError("CONVERGENCE_INVALID", f"{label} must be numeric") from exc
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        raise TriFemSheetError("CONVERGENCE_INVALID", f"{label} must be a finite matrix")
    canonical = np.array(matrix, dtype="<f8", order="C", copy=True)
    canonical[canonical == 0.0] = 0.0
    digest = sha256(_canonical_json({"shape": canonical.shape}))
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


def _validate_floating_psd(
    matrix: csc_matrix,
    *,
    label: str,
    allow_single_zero: bool = False,
    structural_gram_attested: bool = False,
) -> None:
    if matrix.shape[0] != matrix.shape[1] or matrix.shape[0] == 0:
        raise TriFemSheetError("STIFFNESS_INVALID", f"{label} must be nonempty and square")
    count = matrix.shape[0]
    if count == 1 and allow_single_zero:
        if matrix.nnz and np.max(np.abs(matrix.data)) > 0.0:
            raise TriFemSheetError("STIFFNESS_NOT_FLOATING", f"{label} row sum is not zero")
        return
    if matrix.nnz == 0:
        raise TriFemSheetError("STIFFNESS_NON_PASSIVE", f"{label} has no sheet energy")
    scale = max(1.0, float(np.max(np.abs(matrix.data), initial=0.0)))
    asymmetry = matrix - matrix.T
    if asymmetry.nnz:
        error = float(np.max(np.abs(asymmetry.data), initial=0.0))
        if error > 1.0e-12 * scale:
            raise TriFemSheetError("STIFFNESS_NON_RECIPROCAL", f"{label} is not symmetric")
    row_sum = np.asarray(matrix.sum(axis=1), dtype=np.float64).ravel()
    if float(np.max(np.abs(row_sum), initial=0.0)) > 5.0e-12 * scale:
        raise TriFemSheetError("STIFFNESS_NOT_FLOATING", f"{label} row sums are not zero")
    graph = matrix.copy()
    graph.setdiag(0.0)
    graph.eliminate_zeros()
    graph.data = np.ones_like(graph.data)
    component_count = connected_components(graph, directed=False, return_labels=False)
    if component_count != 1:
        raise TriFemSheetError("STIFFNESS_DISCONNECTED", f"{label} graph is disconnected")
    tolerance = 5.0e-10 * scale
    if count <= _PSD_DENSE_CHECK_LIMIT:
        minimum = float(np.linalg.eigvalsh(matrix.toarray())[0])
    elif structural_gram_attested:
        return
    else:
        raise TriFemSheetError(
            "STIFFNESS_PSD_UNATTESTED",
            f"{label} needs a canonical Gram construction attestation",
        )
    if not isfinite(minimum) or minimum < -tolerance:
        raise TriFemSheetError("STIFFNESS_NON_PASSIVE", f"{label} is indefinite")


@dataclass(frozen=True, slots=True)
class FiniteSheetContact:
    """One exact, finite, source-owned contact footprint on the island."""

    contact_id: str
    owner_id: str
    footprint: Polygon

    def __post_init__(self) -> None:
        contact_id = _require_id(self.contact_id, label="contact_id")
        owner_id = _require_id(self.owner_id, label="contact owner_id")
        footprint = _normalised_polygon(
            self.footprint,
            label=f"contact {contact_id!r}",
        )
        object.__setattr__(self, "contact_id", contact_id)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "footprint", footprint)


@dataclass(frozen=True, slots=True)
class CompiledFiniteSheetContact:
    """Canonical mesh incidence for one finite equipotential contact."""

    contact_id: str
    owner_id: str
    footprint_area_m2: float
    node_indices: NDArray[np.int64]
    triangle_indices: NDArray[np.int64]
    footprint_sha256: str

    def __post_init__(self) -> None:
        contact_id = _require_id(self.contact_id, label="compiled contact_id")
        owner_id = _require_id(self.owner_id, label="compiled contact owner_id")
        area = _positive(self.footprint_area_m2, label="footprint_area_m2")
        footprint_hash = _require_sha256(
            self.footprint_sha256,
            label="contact footprint_sha256",
        )
        nodes = _readonly_int_array(self.node_indices)
        triangles = _readonly_int_array(self.triangle_indices)
        if (
            nodes.ndim != 1
            or len(nodes) < 3
            or np.any(nodes < 0)
            or not np.array_equal(nodes, np.unique(nodes))
        ):
            raise TriFemSheetError(
                "CONTACT_MESH_INVALID",
                f"contact {contact_id!r} needs sorted unique nonnegative nodes",
            )
        if (
            triangles.ndim != 1
            or not len(triangles)
            or np.any(triangles < 0)
            or not np.array_equal(triangles, np.unique(triangles))
        ):
            raise TriFemSheetError(
                "CONTACT_MESH_INVALID",
                f"contact {contact_id!r} needs sorted unique nonnegative triangles",
            )
        object.__setattr__(self, "contact_id", contact_id)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "footprint_area_m2", area)
        object.__setattr__(self, "footprint_sha256", footprint_hash)
        object.__setattr__(self, "node_indices", nodes)
        object.__setattr__(self, "triangle_indices", triangles)


def _mesh_identity_manifest(
    *,
    nodes: NDArray[np.float64],
    triangles: NDArray[np.int64],
    contacts: Sequence[CompiledFiniteSheetContact],
    island_area_m2: float,
    refinement_levels: int,
    island_wkb_sha256: str,
    contact_candidate_work: int,
    contact_work_limit: int,
) -> Mapping[str, object]:
    return {
        "compiler_id": TRI_FEM_SHEET_COMPILER_ID,
        "shapely_version": shapely.__version__,
        "geos_version": shapely.geos_version_string,
        "island_wkb_sha256": island_wkb_sha256,
        "island_area_m2": island_area_m2,
        "refinement_levels": refinement_levels,
        "node_count": len(nodes),
        "triangle_count": len(triangles),
        "contact_candidate_work": contact_candidate_work,
        "contact_work_limit": contact_work_limit,
        "contacts": tuple(
            {
                "contact_id": item.contact_id,
                "owner_id": item.owner_id,
                "footprint_area_m2": item.footprint_area_m2,
                "footprint_sha256": item.footprint_sha256,
                "node_indices_sha256": _array_sha256(item.node_indices),
                "triangle_indices_sha256": _array_sha256(item.triangle_indices),
            }
            for item in contacts
        ),
    }


def _mesh_identity_sha256(
    manifest: Mapping[str, object],
    nodes: NDArray[np.float64],
    triangles: NDArray[np.int64],
) -> str:
    digest = sha256(_canonical_json(manifest))
    canonical_nodes = np.asarray(nodes, dtype="<f8", order="C").copy()
    canonical_nodes[canonical_nodes == 0.0] = 0.0
    digest.update(canonical_nodes.tobytes(order="C"))
    digest.update(np.asarray(triangles, dtype="<i8", order="C").tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TriFemSheetMesh:
    """Canonical constrained-Delaunay mesh and finite-contact incidence."""

    node_xy_m: NDArray[np.float64]
    triangles: NDArray[np.int64]
    contacts: tuple[CompiledFiniteSheetContact, ...]
    island_area_m2: float
    refinement_levels: int
    island_wkb_sha256: str
    contact_candidate_work: int
    contact_work_limit: int
    mesh_identity_sha256: str

    def __post_init__(self) -> None:
        nodes = _readonly_float_array(self.node_xy_m)
        triangles = _readonly_int_array(self.triangles)
        contacts = tuple(self.contacts)
        area = _positive(self.island_area_m2, label="island_area_m2")
        levels = _bounded_integer(
            self.refinement_levels,
            label="refinement_levels",
            minimum=0,
            maximum=_MAX_REFINEMENT_LEVELS,
        )
        island_hash = _require_sha256(
            self.island_wkb_sha256,
            label="island_wkb_sha256",
        )
        work = _bounded_integer(
            self.contact_candidate_work,
            label="contact_candidate_work",
            minimum=0,
            maximum=1_000_000_000,
        )
        work_limit = _bounded_integer(
            self.contact_work_limit,
            label="contact_work_limit",
            minimum=0,
            maximum=1_000_000_000,
        )
        if work > work_limit:
            raise TriFemSheetError(
                "CONTACT_WORK_BOUND_EXCEEDED",
                "contact candidate work exceeds its declared bound",
            )
        if nodes.ndim != 2 or nodes.shape[1:] != (2,) or len(nodes) < 3:
            raise TriFemSheetError("MESH_INVALID", "node_xy_m must have shape (N, 2)")
        if not np.all(np.isfinite(nodes)):
            raise TriFemSheetError("MESH_INVALID", "mesh nodes must be finite")
        node_rows = [tuple(map(float, row)) for row in nodes]
        if node_rows != sorted(set(node_rows)):
            raise TriFemSheetError("MESH_NOT_CANONICAL", "mesh nodes are not canonical")
        if triangles.ndim != 2 or triangles.shape[1:] != (3,) or not len(triangles):
            raise TriFemSheetError("MESH_INVALID", "triangles must have shape (M, 3)")
        if np.any(triangles < 0) or np.any(triangles >= len(nodes)):
            raise TriFemSheetError("MESH_INVALID", "triangle indices are out of range")
        triangle_rows = [tuple(map(int, row)) for row in triangles]
        if (
            any(tuple(sorted(row)) != row for row in triangle_rows)
            or triangle_rows != sorted(set(triangle_rows))
        ):
            raise TriFemSheetError("MESH_NOT_CANONICAL", "triangles are not canonical")
        points = nodes[triangles]
        determinants = (
            (points[:, 1, 0] - points[:, 0, 0])
            * (points[:, 2, 1] - points[:, 0, 1])
            - (points[:, 2, 0] - points[:, 0, 0])
            * (points[:, 1, 1] - points[:, 0, 1])
        )
        triangle_areas = 0.5 * np.abs(determinants)
        if not np.all(np.isfinite(triangle_areas)) or np.any(triangle_areas <= 0.0):
            raise TriFemSheetError("MESH_TRIANGLE_DEGENERATE", "mesh has zero-area triangles")
        area_tolerance = max(area * 2.0e-11, _FLOAT_EPS * area * 512.0)
        if abs(float(np.sum(triangle_areas)) - area) > area_tolerance:
            raise TriFemSheetError("MESH_COVERAGE_INVALID", "triangle areas differ from island")
        if len(np.unique(triangles)) != len(nodes):
            raise TriFemSheetError("MESH_INVALID", "mesh contains an unused node")
        edges = np.concatenate(
            (
                triangles[:, (0, 1)],
                triangles[:, (1, 2)],
                triangles[:, (0, 2)],
            )
        )
        adjacency = coo_matrix(
            (
                np.ones(2 * len(edges), dtype=np.int8),
                (
                    np.concatenate((edges[:, 0], edges[:, 1])),
                    np.concatenate((edges[:, 1], edges[:, 0])),
                ),
            ),
            shape=(len(nodes), len(nodes)),
        ).tocsr()
        if connected_components(adjacency, directed=False, return_labels=False) != 1:
            raise TriFemSheetError("MESH_DISCONNECTED", "mesh node graph is disconnected")
        if any(not isinstance(item, CompiledFiniteSheetContact) for item in contacts):
            raise TriFemSheetError("CONTACT_MESH_INVALID", "compiled contacts have wrong type")
        expected_contacts = tuple(
            sorted(contacts, key=lambda item: (item.contact_id.casefold(), item.contact_id))
        )
        observed_contact_ids = tuple(item.contact_id for item in contacts)
        expected_contact_ids = tuple(item.contact_id for item in expected_contacts)
        if observed_contact_ids != expected_contact_ids:
            raise TriFemSheetError("CONTACT_MESH_INVALID", "compiled contacts are not sorted")
        contact_keys = [item.contact_id.casefold() for item in contacts]
        owner_keys = [item.owner_id.casefold() for item in contacts]
        if len(set(contact_keys)) != len(contact_keys):
            raise TriFemSheetError("CONTACT_ID_DUPLICATED", "compiled contact IDs repeat")
        if len(set(owner_keys)) != len(owner_keys):
            raise TriFemSheetError("CONTACT_OWNER_DUPLICATED", "compiled owners repeat")
        assigned_nodes: set[int] = set()
        assigned_triangles: set[int] = set()
        for contact in contacts:
            if (
                np.any(contact.node_indices >= len(nodes))
                or np.any(contact.triangle_indices >= len(triangles))
            ):
                raise TriFemSheetError(
                    "CONTACT_MESH_INVALID",
                    f"contact {contact.contact_id!r} indices are out of range",
                )
            node_set = set(map(int, contact.node_indices))
            triangle_set = set(map(int, contact.triangle_indices))
            if node_set & assigned_nodes or triangle_set & assigned_triangles:
                raise TriFemSheetError(
                    "CONTACT_MESH_AMBIGUOUS",
                    "compiled contacts share nodes or triangles",
                )
            expected_nodes = set(map(int, triangles[contact.triangle_indices].ravel()))
            if node_set != expected_nodes:
                raise TriFemSheetError(
                    "CONTACT_MESH_INVALID",
                    f"contact {contact.contact_id!r} node incidence is stale",
                )
            contact_area = float(np.sum(triangle_areas[contact.triangle_indices]))
            tolerance = max(
                contact.footprint_area_m2 * 2.0e-11,
                _FLOAT_EPS * area * 512.0,
            )
            if abs(contact_area - contact.footprint_area_m2) > tolerance:
                raise TriFemSheetError(
                    "CONTACT_MESH_INVALID",
                    f"contact {contact.contact_id!r} area incidence is stale",
                )
            assigned_nodes.update(node_set)
            assigned_triangles.update(triangle_set)
        manifest = _mesh_identity_manifest(
            nodes=nodes,
            triangles=triangles,
            contacts=contacts,
            island_area_m2=area,
            refinement_levels=levels,
            island_wkb_sha256=island_hash,
            contact_candidate_work=work,
            contact_work_limit=work_limit,
        )
        expected_identity = _mesh_identity_sha256(manifest, nodes, triangles)
        observed_identity = _require_sha256(
            self.mesh_identity_sha256,
            label="mesh_identity_sha256",
        )
        if observed_identity != expected_identity:
            raise TriFemSheetError("MESH_IDENTITY_MISMATCH", "mesh identity is stale")
        object.__setattr__(self, "node_xy_m", nodes)
        object.__setattr__(self, "triangles", triangles)
        object.__setattr__(self, "contacts", contacts)
        object.__setattr__(self, "island_area_m2", area)
        object.__setattr__(self, "refinement_levels", levels)
        object.__setattr__(self, "island_wkb_sha256", island_hash)
        object.__setattr__(self, "contact_candidate_work", work)
        object.__setattr__(self, "contact_work_limit", work_limit)
        object.__setattr__(self, "mesh_identity_sha256", observed_identity)

    def verify_current_identity(self) -> None:
        """Recompute the canonical mesh identity before trusted use.

        The mesh arrays are exposed as read-only buffers, but callers can still
        bypass that flag through low-level buffer APIs.  Rebuilding the
        canonical manifest from the current buffers makes those mutations
        fail closed instead of allowing stale contact incidence or geometry to
        be used with the original identity.
        """

        nodes = _readonly_float_array(self.node_xy_m)
        triangles = _readonly_int_array(self.triangles)
        contacts = tuple(self.contacts)
        manifest = _mesh_identity_manifest(
            nodes=nodes,
            triangles=triangles,
            contacts=contacts,
            island_area_m2=self.island_area_m2,
            refinement_levels=self.refinement_levels,
            island_wkb_sha256=self.island_wkb_sha256,
            contact_candidate_work=self.contact_candidate_work,
            contact_work_limit=self.contact_work_limit,
        )
        expected = _mesh_identity_sha256(manifest, nodes, triangles)
        observed = _require_sha256(
            self.mesh_identity_sha256,
            label="mesh_identity_sha256",
        )
        if expected != observed:
            raise TriFemSheetError(
                "MESH_IDENTITY_MISMATCH",
                "mesh buffers changed after construction",
            )


def _convergence_payload(
    *,
    contact_ids: tuple[str, ...],
    coarse_level: int,
    fine_level: int,
    relative_rms_change: float,
    relative_max_change: float,
    relative_rms_tolerance: float,
    relative_max_tolerance: float,
    operator_identity_sha256: tuple[str, str],
    contact_qoi_sha256: tuple[str, str],
    total_work: int,
    work_limit: int,
) -> Mapping[str, object]:
    return {
        "compiler_id": TRI_FEM_SHEET_COMPILER_ID,
        "qoi": "contact_reduced_stiffness",
        "contact_ids": contact_ids,
        "coarse_level": coarse_level,
        "fine_level": fine_level,
        "relative_rms_change": relative_rms_change,
        "relative_max_change": relative_max_change,
        "relative_rms_tolerance": relative_rms_tolerance,
        "relative_max_tolerance": relative_max_tolerance,
        "operator_identity_sha256": operator_identity_sha256,
        "contact_qoi_sha256": contact_qoi_sha256,
        "total_work": total_work,
        "work_limit": work_limit,
    }


@dataclass(frozen=True, slots=True)
class _MeshConvergenceAttestation:
    """Internally issued bounded contact-QoI convergence proof."""

    contact_ids: tuple[str, ...]
    coarse_level: int
    fine_level: int
    relative_rms_change: float
    relative_max_change: float
    relative_rms_tolerance: float
    relative_max_tolerance: float
    operator_identity_sha256: tuple[str, str]
    contact_qoi_sha256: tuple[str, str]
    total_work: int
    work_limit: int
    attestation_sha256: str
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        issued = _ATTESTATION_ISSUERS.get(id(self._issuer))
        if issued is None or issued[0] is not self._issuer:
            raise TriFemSheetError(
                "CONVERGENCE_ATTESTATION_PRIVATE",
                "convergence attestations are issued only by the convergence compiler",
            )
        contact_ids = tuple(
            _require_id(item, label="attested contact_id")
            for item in self.contact_ids
        )
        if (
            len(contact_ids) < 2
            or len({item.casefold() for item in contact_ids}) != len(contact_ids)
            or contact_ids != tuple(sorted(contact_ids, key=lambda item: (item.casefold(), item)))
        ):
            raise TriFemSheetError(
                "CONVERGENCE_INVALID",
                "attestation needs canonical unique contacts",
            )
        coarse = _bounded_integer(
            self.coarse_level,
            label="coarse_level",
            minimum=0,
            maximum=_MAX_REFINEMENT_LEVELS - 1,
        )
        fine = _bounded_integer(
            self.fine_level,
            label="fine_level",
            minimum=1,
            maximum=_MAX_REFINEMENT_LEVELS,
        )
        if fine != coarse + 1:
            raise TriFemSheetError("CONVERGENCE_INVALID", "attested levels must be adjacent")
        rms_change = _nonnegative(self.relative_rms_change, label="relative_rms_change")
        max_change = _nonnegative(self.relative_max_change, label="relative_max_change")
        rms_tolerance = _nonnegative(
            self.relative_rms_tolerance,
            label="relative_rms_tolerance",
        )
        max_tolerance = _nonnegative(
            self.relative_max_tolerance,
            label="relative_max_tolerance",
        )
        if rms_change > rms_tolerance or max_change > max_tolerance:
            raise TriFemSheetError("CONVERGENCE_INVALID", "attestation exceeds tolerance")
        identities = tuple(
            _require_sha256(item, label="level operator identity")
            for item in self.operator_identity_sha256
        )
        qoi_hashes = tuple(
            _require_sha256(item, label="contact QoI identity")
            for item in self.contact_qoi_sha256
        )
        if len(identities) != 2 or len(qoi_hashes) != 2:
            raise TriFemSheetError(
                "CONVERGENCE_INVALID",
                "attestation needs exactly one adjacent operator/QoI pair",
            )
        if identities[0] == identities[1]:
            raise TriFemSheetError(
                "CONVERGENCE_INVALID",
                "adjacent fixed-level operator identities must differ",
            )
        total_work = _bounded_integer(
            self.total_work,
            label="total_work",
            minimum=1,
            maximum=1_000_000_000,
        )
        work_limit = _bounded_integer(
            self.work_limit,
            label="work_limit",
            minimum=1,
            maximum=1_000_000_000,
        )
        if total_work > work_limit:
            raise TriFemSheetError("CONVERGENCE_INVALID", "attested work exceeds its bound")
        payload = _convergence_payload(
            contact_ids=contact_ids,
            coarse_level=coarse,
            fine_level=fine,
            relative_rms_change=rms_change,
            relative_max_change=max_change,
            relative_rms_tolerance=rms_tolerance,
            relative_max_tolerance=max_tolerance,
            operator_identity_sha256=identities,
            contact_qoi_sha256=qoi_hashes,
            total_work=total_work,
            work_limit=work_limit,
        )
        expected = sha256(_canonical_json(payload)).hexdigest()
        observed = _require_sha256(
            self.attestation_sha256,
            label="attestation_sha256",
        )
        if observed != expected:
            raise TriFemSheetError(
                "CONVERGENCE_IDENTITY_MISMATCH",
                "convergence attestation is stale",
            )
        if issued[1] != expected:
            raise TriFemSheetError(
                "CONVERGENCE_ATTESTATION_FORGED",
                "convergence payload differs from its internally issued evidence",
            )
        object.__setattr__(self, "contact_ids", contact_ids)
        object.__setattr__(self, "coarse_level", coarse)
        object.__setattr__(self, "fine_level", fine)
        object.__setattr__(self, "relative_rms_change", rms_change)
        object.__setattr__(self, "relative_max_change", max_change)
        object.__setattr__(self, "relative_rms_tolerance", rms_tolerance)
        object.__setattr__(self, "relative_max_tolerance", max_tolerance)
        object.__setattr__(self, "operator_identity_sha256", identities)
        object.__setattr__(self, "contact_qoi_sha256", qoi_hashes)
        object.__setattr__(self, "total_work", total_work)
        object.__setattr__(self, "work_limit", work_limit)
        object.__setattr__(self, "attestation_sha256", observed)
        object.__setattr__(self, "_issuer", issued[0])


def _contraction_identity(
    *,
    contact_ids: tuple[str, ...],
    full_to_contracted: NDArray[np.int64],
    projection: csc_matrix,
    stiffness: csc_matrix,
    source_stiffness_sha256: str,
) -> str:
    manifest = {
        "compiler_id": TRI_FEM_SHEET_COMPILER_ID,
        "contact_ids": contact_ids,
        "full_to_contracted_sha256": _array_sha256(full_to_contracted),
        "projection_sha256": _csc_sha256(projection),
        "stiffness_sha256": _csc_sha256(stiffness),
        "source_stiffness_sha256": source_stiffness_sha256,
    }
    return sha256(_canonical_json(manifest)).hexdigest()


@dataclass(frozen=True, slots=True)
class EquipotentialElectrodeContraction:
    """Canonical sparse equality contraction ``Vfull = P @ Vcontracted``."""

    contact_ids: tuple[str, ...]
    full_to_contracted: NDArray[np.int64]
    contact_dof_indices: NDArray[np.int64]
    voltage_projection: csc_matrix
    stiffness: csc_matrix
    source_stiffness_sha256: str
    contraction_identity_sha256: str
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _CONTRACTION_ISSUER:
            raise TriFemSheetError(
                "CONTRACTION_PRIVATE",
                "electrode contractions must be issued by a verified sheet operator",
            )
        contact_ids = tuple(
            _require_id(item, label="electrode contact_id")
            for item in self.contact_ids
        )
        if len({item.casefold() for item in contact_ids}) != len(contact_ids):
            raise TriFemSheetError("CONTACT_ID_DUPLICATED", "electrode contact IDs repeat")
        mapping = _readonly_int_array(self.full_to_contracted)
        contact_dofs = _readonly_int_array(self.contact_dof_indices)
        projection = _readonly_real_csc(
            self.voltage_projection,
            label="electrode voltage projection",
        )
        stiffness = _readonly_real_csc(
            self.stiffness,
            label="contracted stiffness",
        )
        source_hash = _require_sha256(
            self.source_stiffness_sha256,
            label="source_stiffness_sha256",
        )
        if mapping.ndim != 1 or not len(mapping) or np.any(mapping < 0):
            raise TriFemSheetError("CONTRACTION_INVALID", "full-to-contracted map is invalid")
        dof_count = int(np.max(mapping)) + 1
        if not np.array_equal(np.unique(mapping), np.arange(dof_count)):
            raise TriFemSheetError("CONTRACTION_INVALID", "contracted DOFs are not contiguous")
        expected_contacts = np.arange(len(contact_ids), dtype=np.int64)
        if not np.array_equal(contact_dofs, expected_contacts):
            raise TriFemSheetError("CONTRACTION_INVALID", "contact DOFs are not canonical")
        if projection.shape != (len(mapping), dof_count):
            raise TriFemSheetError("CONTRACTION_INVALID", "projection shape differs from map")
        projection_csr = projection.tocsr()
        if (
            not np.all(np.diff(projection_csr.indptr) == 1)
            or not np.array_equal(projection_csr.indices, mapping)
            or not np.all(projection_csr.data == 1.0)
        ):
            raise TriFemSheetError("CONTRACTION_INVALID", "projection is not canonical one-hot")
        free_nodes = np.flatnonzero(mapping >= len(contact_ids))
        expected_free = len(contact_ids) + np.arange(len(free_nodes), dtype=np.int64)
        if not np.array_equal(mapping[free_nodes], expected_free):
            raise TriFemSheetError("CONTRACTION_INVALID", "free-node DOF order is not canonical")
        if stiffness.shape != (dof_count, dof_count):
            raise TriFemSheetError("CONTRACTION_INVALID", "contracted stiffness shape differs")
        _validate_floating_psd(
            stiffness,
            label="contracted stiffness",
            allow_single_zero=dof_count == 1,
            structural_gram_attested=True,
        )
        expected_identity = _contraction_identity(
            contact_ids=contact_ids,
            full_to_contracted=mapping,
            projection=projection,
            stiffness=stiffness,
            source_stiffness_sha256=source_hash,
        )
        observed_identity = _require_sha256(
            self.contraction_identity_sha256,
            label="contraction_identity_sha256",
        )
        if observed_identity != expected_identity:
            raise TriFemSheetError(
                "CONTRACTION_IDENTITY_MISMATCH",
                "electrode contraction identity is stale",
            )
        object.__setattr__(self, "contact_ids", contact_ids)
        object.__setattr__(self, "full_to_contracted", mapping)
        object.__setattr__(self, "contact_dof_indices", contact_dofs)
        object.__setattr__(self, "voltage_projection", projection)
        object.__setattr__(self, "stiffness", stiffness)
        object.__setattr__(self, "source_stiffness_sha256", source_hash)
        object.__setattr__(self, "contraction_identity_sha256", observed_identity)
        object.__setattr__(self, "_issuer", _CONTRACTION_ISSUER)


def _operator_manifest(
    *,
    island_id: str,
    mesh: TriFemSheetMesh,
    stiffness: csc_matrix,
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float,
    convergence_attestation: _MeshConvergenceAttestation | None,
) -> Mapping[str, object]:
    return {
        "compiler_id": TRI_FEM_SHEET_COMPILER_ID,
        "island_id": island_id,
        "mesh_identity_sha256": mesh.mesh_identity_sha256,
        "stiffness_sha256": _csc_sha256(stiffness),
        "conductivity_s_per_m": conductivity_s_per_m,
        "thickness_m": thickness_m,
        "permeability_h_per_m": permeability_h_per_m,
        "sheet_scope": "copper_resistance_and_internal_impedance_only",
        "external_inductance_modeled": False,
        "contact_ids": tuple(item.contact_id for item in mesh.contacts),
        "contact_owner_ids": tuple(item.owner_id for item in mesh.contacts),
        "production_eligible": convergence_attestation is not None,
        "convergence_attestation_sha256": (
            None
            if convergence_attestation is None
            else convergence_attestation.attestation_sha256
        ),
    }


@dataclass(frozen=True, slots=True)
class TriFemSheetOperator:
    """Immutable floating FEM sheet block with identity and convergence state."""

    island_id: str
    mesh: TriFemSheetMesh
    stiffness: csc_matrix
    conductivity_s_per_m: float
    thickness_m: float
    permeability_h_per_m: float
    convergence_attestation: _MeshConvergenceAttestation | None
    identity_sha256: str
    identity_manifest: Mapping[str, object]

    def __post_init__(self) -> None:
        island_id = _require_id(self.island_id, label="operator island_id")
        if not isinstance(self.mesh, TriFemSheetMesh):
            raise TriFemSheetError("MESH_INVALID", "operator mesh has wrong type")
        conductivity = _positive(
            self.conductivity_s_per_m,
            label="conductivity_s_per_m",
        )
        thickness = _positive(self.thickness_m, label="thickness_m")
        permeability = _positive(
            self.permeability_h_per_m,
            label="permeability_h_per_m",
        )
        attestation = self.convergence_attestation
        if attestation is not None:
            if not isinstance(attestation, _MeshConvergenceAttestation):
                raise TriFemSheetError("CONVERGENCE_INVALID", "attestation has wrong type")
            if attestation.contact_ids != tuple(item.contact_id for item in self.mesh.contacts):
                raise TriFemSheetError(
                    "CONVERGENCE_INVALID",
                    "attested contacts differ from final mesh",
                )
            if attestation.fine_level != self.mesh.refinement_levels:
                raise TriFemSheetError(
                    "CONVERGENCE_INVALID",
                    "attested level differs from final mesh",
                )
        stiffness = _readonly_real_csc(self.stiffness, label="operator stiffness")
        count = len(self.mesh.node_xy_m)
        if stiffness.shape != (count, count):
            raise TriFemSheetError("STIFFNESS_INVALID", "stiffness shape differs from mesh")
        expected_stiffness = _compile_stiffness(self.mesh)
        if _csc_sha256(stiffness) != _csc_sha256(expected_stiffness):
            raise TriFemSheetError(
                "STIFFNESS_ASSEMBLY_MISMATCH",
                "operator stiffness differs from the canonical mesh Gram assembly",
            )
        _validate_floating_psd(
            stiffness,
            label="operator stiffness",
            structural_gram_attested=True,
        )
        if attestation is not None:
            fixed_manifest = _operator_manifest(
                island_id=island_id,
                mesh=self.mesh,
                stiffness=stiffness,
                conductivity_s_per_m=conductivity,
                thickness_m=thickness,
                permeability_h_per_m=permeability,
                convergence_attestation=None,
            )
            fixed_identity = sha256(_canonical_json(fixed_manifest)).hexdigest()
            if attestation.operator_identity_sha256[1] != fixed_identity:
                raise TriFemSheetError(
                    "CONVERGENCE_IDENTITY_MISMATCH",
                    "attestation does not bind the final fixed-level operator",
                )
            fine_qoi_hash = _float_matrix_sha256(
                self.contact_reduced_stiffness(),
                label="fine contact QoI",
            )
            if attestation.contact_qoi_sha256[1] != fine_qoi_hash:
                raise TriFemSheetError(
                    "CONVERGENCE_QOI_MISMATCH",
                    "attestation does not bind the final contact QoI matrix",
                )
        expected_manifest = _operator_manifest(
            island_id=island_id,
            mesh=self.mesh,
            stiffness=stiffness,
            conductivity_s_per_m=conductivity,
            thickness_m=thickness,
            permeability_h_per_m=permeability,
            convergence_attestation=attestation,
        )
        if not isinstance(self.identity_manifest, Mapping):
            raise TriFemSheetError("OPERATOR_MANIFEST_MISMATCH", "manifest is not a mapping")
        if _canonical_json(dict(self.identity_manifest)) != _canonical_json(expected_manifest):
            raise TriFemSheetError("OPERATOR_MANIFEST_MISMATCH", "operator manifest is stale")
        expected_identity = sha256(_canonical_json(expected_manifest)).hexdigest()
        observed_identity = _require_sha256(
            self.identity_sha256,
            label="operator identity_sha256",
        )
        if observed_identity != expected_identity:
            raise TriFemSheetError("OPERATOR_IDENTITY_MISMATCH", "operator identity is stale")
        object.__setattr__(self, "island_id", island_id)
        object.__setattr__(self, "stiffness", stiffness)
        object.__setattr__(self, "conductivity_s_per_m", conductivity)
        object.__setattr__(self, "thickness_m", thickness)
        object.__setattr__(self, "permeability_h_per_m", permeability)
        object.__setattr__(self, "identity_sha256", observed_identity)
        object.__setattr__(
            self,
            "identity_manifest",
            MappingProxyType(dict(expected_manifest)),
        )

    def verify_current_identity(self) -> None:
        """Revalidate mesh, stiffness, attestation, and operator identity."""

        self.mesh.verify_current_identity()
        stiffness = _readonly_real_csc(self.stiffness, label="operator stiffness")
        expected_stiffness = _compile_stiffness(self.mesh)
        if _csc_sha256(stiffness) != _csc_sha256(expected_stiffness):
            raise TriFemSheetError(
                "STIFFNESS_ASSEMBLY_MISMATCH",
                "operator stiffness differs from the canonical mesh Gram assembly",
            )
        attestation = self.convergence_attestation
        if attestation is not None:
            # Re-run the private constructor checks so a low-level mutation of
            # an attestation field cannot leave its original hash trusted.
            type(attestation)(
                contact_ids=attestation.contact_ids,
                coarse_level=attestation.coarse_level,
                fine_level=attestation.fine_level,
                relative_rms_change=attestation.relative_rms_change,
                relative_max_change=attestation.relative_max_change,
                relative_rms_tolerance=attestation.relative_rms_tolerance,
                relative_max_tolerance=attestation.relative_max_tolerance,
                operator_identity_sha256=attestation.operator_identity_sha256,
                contact_qoi_sha256=attestation.contact_qoi_sha256,
                total_work=attestation.total_work,
                work_limit=attestation.work_limit,
                attestation_sha256=attestation.attestation_sha256,
                _issuer=attestation._issuer,
            )
        expected_manifest = _operator_manifest(
            island_id=self.island_id,
            mesh=self.mesh,
            stiffness=stiffness,
            conductivity_s_per_m=self.conductivity_s_per_m,
            thickness_m=self.thickness_m,
            permeability_h_per_m=self.permeability_h_per_m,
            convergence_attestation=attestation,
        )
        if not isinstance(self.identity_manifest, Mapping):
            raise TriFemSheetError("OPERATOR_MANIFEST_MISMATCH", "manifest is not a mapping")
        if _canonical_json(dict(self.identity_manifest)) != _canonical_json(expected_manifest):
            raise TriFemSheetError("OPERATOR_MANIFEST_MISMATCH", "operator manifest is stale")
        expected_identity = sha256(_canonical_json(expected_manifest)).hexdigest()
        observed_identity = _require_sha256(
            self.identity_sha256,
            label="operator identity_sha256",
        )
        if expected_identity != observed_identity:
            raise TriFemSheetError(
                "OPERATOR_IDENTITY_MISMATCH",
                "operator buffers changed after construction",
            )

    @property
    def contact_ids(self) -> tuple[str, ...]:
        return tuple(contact.contact_id for contact in self.mesh.contacts)

    @property
    def production_eligible(self) -> bool:
        return self.convergence_attestation is not None

    def require_production_eligible(self) -> None:
        if not self.production_eligible:
            raise TriFemSheetError(
                "MESH_CONVERGENCE_UNATTESTED",
                "fixed-level sheet is production-ineligible without convergence",
            )

    def sheet_impedance_ohm_per_square(
        self,
        frequency_hz: float | ArrayLike,
        *,
        model: SheetImpedanceModel = "common_mode",
    ) -> complex | NDArray[np.complex128]:
        """Return DC or symmetric two-face internal sheet impedance."""

        if model == "dc":
            frequencies = _frequency_array(frequency_hz)
            result = np.full(
                frequencies.shape,
                1.0 / (self.conductivity_s_per_m * self.thickness_m),
                dtype=np.complex128,
            )
            if np.ndim(frequency_hz) == 0:
                return complex(result.item())
            return result
        if model != "common_mode":
            raise TriFemSheetError(
                "SHEET_MODEL_UNSUPPORTED",
                f"unsupported sheet impedance model {model!r}",
            )
        return common_mode_copper_sheet_impedance(
            frequency_hz,
            self.conductivity_s_per_m,
            self.thickness_m,
            self.permeability_h_per_m,
        )

    def nodal_admittance_s(
        self,
        frequency_hz: float,
        *,
        model: SheetImpedanceModel = "common_mode",
    ) -> csc_matrix:
        """Return the reciprocal floating nodal sheet stamp at one frequency."""

        self.verify_current_identity()

        impedance = self.sheet_impedance_ohm_per_square(frequency_hz, model=model)
        if not isinstance(impedance, complex):
            raise TriFemSheetError("FREQUENCY_INVALID", "nodal stamp requires one frequency")
        admittance = 1.0 / impedance
        if (
            not np.isfinite(admittance)
            or admittance.real < -1.0e-13 * max(1.0, abs(admittance))
        ):
            raise TriFemSheetError("SHEET_NON_PASSIVE", "sheet admittance is invalid")
        result = csc_matrix(self.stiffness.astype(np.complex128) * admittance)
        result.data.setflags(write=False)
        result.indices.setflags(write=False)
        result.indptr.setflags(write=False)
        return result

    def equipotential_electrode_contraction(
        self,
    ) -> EquipotentialElectrodeContraction:
        """Contract every finite contact to one canonical sparse electrode DOF."""

        self.verify_current_identity()

        node_count = len(self.mesh.node_xy_m)
        contact_count = len(self.mesh.contacts)
        mapping = np.full(node_count, -1, dtype=np.int64)
        for contact_index, contact in enumerate(self.mesh.contacts):
            if np.any(mapping[contact.node_indices] >= 0):
                raise TriFemSheetError(
                    "CONTACT_MESH_AMBIGUOUS",
                    "compiled finite contacts share mesh nodes",
                )
            mapping[contact.node_indices] = contact_index
        free_nodes = np.flatnonzero(mapping < 0)
        mapping[free_nodes] = contact_count + np.arange(len(free_nodes), dtype=np.int64)
        projection = coo_matrix(
            (
                np.ones(node_count, dtype=np.float64),
                (np.arange(node_count, dtype=np.int64), mapping),
            ),
            shape=(node_count, contact_count + len(free_nodes)),
        ).tocsc()
        contracted = csc_matrix(projection.T @ self.stiffness @ projection)
        source_hash = _csc_sha256(self.stiffness)
        identity = _contraction_identity(
            contact_ids=self.contact_ids,
            full_to_contracted=mapping,
            projection=projection,
            stiffness=contracted,
            source_stiffness_sha256=source_hash,
        )
        return EquipotentialElectrodeContraction(
            contact_ids=self.contact_ids,
            full_to_contracted=mapping,
            contact_dof_indices=np.arange(contact_count, dtype=np.int64),
            voltage_projection=projection,
            stiffness=contracted,
            source_stiffness_sha256=source_hash,
            contraction_identity_sha256=identity,
            _issuer=_CONTRACTION_ISSUER,
        )

    def contact_reduced_stiffness(self) -> NDArray[np.float64]:
        """Kron-reduce the sparse equality-contracted sheet to its contacts."""

        self.verify_current_identity()

        contact_count = len(self.mesh.contacts)
        if not contact_count:
            return _readonly_float_array(np.empty((0, 0), dtype=np.float64))
        contraction = self.equipotential_electrode_contraction()
        stiffness = contraction.stiffness
        contact = np.arange(contact_count, dtype=np.int64)
        interior = np.arange(contact_count, stiffness.shape[0], dtype=np.int64)
        reduced = np.asarray(stiffness[contact, :][:, contact].toarray())
        if len(interior):
            k_ii = stiffness[interior, :][:, interior].tocsc()
            k_ic = stiffness[interior, :][:, contact].tocsc()
            try:
                solved = splu(k_ii).solve(k_ic.toarray())
            except Exception as exc:
                raise TriFemSheetError(
                    "CONTACT_REDUCTION_SINGULAR",
                    "finite-contact interior stiffness is singular",
                ) from exc
            reduced -= np.asarray(k_ic.T @ solved, dtype=np.float64)
        reduced = 0.5 * (reduced + reduced.T)
        off_diagonal = reduced.copy()
        np.fill_diagonal(off_diagonal, 0.0)
        np.fill_diagonal(reduced, -np.sum(off_diagonal, axis=1))
        scale = max(1.0, float(np.max(np.abs(reduced), initial=0.0)))
        if not np.all(np.isfinite(reduced)):
            raise TriFemSheetError("CONTACT_REDUCTION_INVALID", "contact reduction is invalid")
        if np.linalg.eigvalsh(reduced)[0] < -5.0e-10 * scale:
            raise TriFemSheetError(
                "CONTACT_REDUCTION_NON_PASSIVE",
                "contact reduction is indefinite",
            )
        return _readonly_float_array(reduced)

    def contact_admittance_s(
        self,
        frequency_hz: float,
        *,
        model: SheetImpedanceModel = "common_mode",
    ) -> NDArray[np.complex128]:
        """Return the locally Kron-reduced contact admittance matrix."""

        impedance = self.sheet_impedance_ohm_per_square(frequency_hz, model=model)
        if not isinstance(impedance, complex):
            raise TriFemSheetError("FREQUENCY_INVALID", "contact stamp requires one frequency")
        result = np.asarray(self.contact_reduced_stiffness(), dtype=np.complex128)
        result /= impedance
        result.setflags(write=False)
        return result


def _frequency_array(frequency_hz: float | ArrayLike) -> NDArray[np.float64]:
    try:
        raw = np.asarray(frequency_hz)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError("FREQUENCY_INVALID", "frequency_hz must be numeric") from exc
    if np.iscomplexobj(raw):
        raise TriFemSheetError("FREQUENCY_INVALID", "frequency_hz must be real")
    try:
        result = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise TriFemSheetError("FREQUENCY_INVALID", "frequency_hz must be numeric") from exc
    if not np.all(np.isfinite(result)) or np.any(result < 0.0):
        raise TriFemSheetError("FREQUENCY_INVALID", "frequency_hz must be finite and >= 0")
    return result


def common_mode_copper_sheet_impedance(
    frequency_hz: float | ArrayLike,
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float = MU_0_H_PER_M,
) -> complex | NDArray[np.complex128]:
    """Return ``(Zself + Ztransfer)/2`` internal impedance in ohm/square.

    The final-impedance small-argument series avoids the ``0 * inf`` failure
    of separately evaluating the characteristic impedance and ``coth`` at
    positive subnormal frequencies.
    """

    conductivity = _positive(conductivity_s_per_m, label="conductivity_s_per_m")
    thickness = _positive(thickness_m, label="thickness_m")
    permeability = _positive(permeability_h_per_m, label="permeability_h_per_m")
    frequencies = _frequency_array(frequency_hz)
    flat = frequencies.reshape(-1)
    output = np.empty(flat.shape, dtype=np.complex128)
    dc = flat == 0.0
    sheet_resistance = 1.0 / (conductivity * thickness)
    output[dc] = sheet_resistance + 0.0j
    if np.any(~dc):
        with np.errstate(under="ignore", over="raise", invalid="raise"):
            omega = 2.0 * np.pi * flat[~dc]
            x_squared = 1j * omega * permeability * conductivity * thickness**2
        small = np.abs(x_squared) < 1.0e-6
        values = np.empty_like(x_squared, dtype=np.complex128)
        if np.any(small):
            xs = x_squared[small]
            with np.errstate(under="ignore", over="raise", invalid="raise"):
                values[small] = sheet_resistance * (
                    1.0
                    + xs / 12.0
                    - xs**2 / 720.0
                    + xs**3 / 30_240.0
                )
        if np.any(~small):
            x = np.sqrt(x_squared[~small])
            characteristic = np.sqrt(
                1j * omega[~small] * permeability / conductivity
            )
            values[~small] = 0.5 * characteristic / np.tanh(0.5 * x)
        output[~dc] = values
    if not np.all(np.isfinite(output)) or np.any(output.real <= 0.0):
        raise TriFemSheetError("SHEET_NON_PASSIVE", "common-mode copper impedance is invalid")
    shaped = output.reshape(frequencies.shape)
    if np.ndim(frequency_hz) == 0:
        return complex(shaped.item())
    return shaped


def dc_sheet_conductance_s_per_square(
    conductivity_s_per_m: float,
    thickness_m: float,
) -> float:
    """Return the exact uniform DC sheet conductance ``sigma*t``."""

    return _positive(conductivity_s_per_m, label="conductivity_s_per_m") * _positive(
        thickness_m,
        label="thickness_m",
    )


def _triangle_key(polygon: Polygon) -> tuple[tuple[float, float], ...]:
    coordinates = tuple(
        (_canonical_coordinate(x), _canonical_coordinate(y))
        for x, y in tuple(polygon.exterior.coords)[:-1]
    )
    if len(coordinates) != 3 or len(set(coordinates)) != 3:
        raise TriFemSheetError("MESH_TRIANGLE_INVALID", "CDT emitted a non-triangle")
    return tuple(sorted(coordinates))


def _subdivide_triangles(
    triangles: Sequence[tuple[tuple[float, float], ...]],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    children: set[tuple[tuple[float, float], ...]] = set()
    for triangle in triangles:
        a, b, c = triangle

        def midpoint(
            left: tuple[float, float],
            right: tuple[float, float],
        ) -> tuple[float, float]:
            return (
                _canonical_coordinate((left[0] + right[0]) * 0.5),
                _canonical_coordinate((left[1] + right[1]) * 0.5),
            )

        ab = midpoint(a, b)
        bc = midpoint(b, c)
        ca = midpoint(c, a)
        for child in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)):
            children.add(tuple(sorted(child)))
    return tuple(sorted(children))


@dataclass(slots=True)
class _ContactWorkBudget:
    limit: int
    used: int = 0

    def consume(self, count: int, *, label: str) -> None:
        self.used += int(count)
        if self.used > self.limit:
            raise TriFemSheetError(
                "CONTACT_WORK_BOUND_EXCEEDED",
                f"{label} candidate work exceeds {self.limit:,}",
            )


def _prepare_contacts(
    island: Polygon,
    contacts: Sequence[FiniteSheetContact],
    *,
    max_contacts: int,
    budget: _ContactWorkBudget,
) -> tuple[FiniteSheetContact, ...]:
    checked: list[FiniteSheetContact] = []
    for item in contacts:
        if not isinstance(item, FiniteSheetContact):
            raise TriFemSheetError(
                "CONTACT_INVALID",
                "contacts must be FiniteSheetContact values",
            )
        checked.append(item)
    if len(checked) > max_contacts:
        raise TriFemSheetError(
            "CONTACT_COUNT_BOUND_EXCEEDED",
            f"contact count {len(checked):,} exceeds {max_contacts:,}",
        )
    canonical = tuple(
        sorted(checked, key=lambda item: (item.contact_id.casefold(), item.contact_id))
    )
    contact_keys = [item.contact_id.casefold() for item in canonical]
    owner_keys = [item.owner_id.casefold() for item in canonical]
    if len(set(contact_keys)) != len(contact_keys):
        raise TriFemSheetError("CONTACT_ID_DUPLICATED", "contact IDs must be unique")
    if len(set(owner_keys)) != len(owner_keys):
        raise TriFemSheetError("CONTACT_OWNER_DUPLICATED", "contact owners must be unique")
    for contact in canonical:
        if not island.covers(contact.footprint):
            raise TriFemSheetError(
                "CONTACT_NOT_FULLY_COVERED",
                f"contact {contact.contact_id!r} is not fully covered by the island",
            )
        if island.boundary.intersects(contact.footprint):
            raise TriFemSheetError(
                "CONTACT_BOUNDARY_AMBIGUOUS",
                f"contact {contact.contact_id!r} touches an island or void boundary",
            )
    if canonical:
        tree = STRtree(tuple(item.footprint for item in canonical))
        for left_index, left in enumerate(canonical):
            candidates = sorted(
                {
                    int(candidate)
                    for candidate in tree.query(left.footprint)
                    if int(candidate) > left_index
                }
            )
            budget.consume(len(candidates), label="contact/contact")
            for right_index in candidates:
                right = canonical[right_index]
                if left.footprint.intersects(right.footprint):
                    raise TriFemSheetError(
                        "CONTACT_OVERLAP_AMBIGUOUS",
                        f"contacts {left.contact_id!r}/{right.contact_id!r} overlap",
                    )
    return canonical


def _compile_mesh(
    island: Polygon,
    contacts: tuple[FiniteSheetContact, ...],
    *,
    refinement_levels: int,
    max_nodes: int,
    max_triangles: int,
    budget: _ContactWorkBudget,
) -> TriFemSheetMesh:
    if _constrained_delaunay_triangles is None:
        raise TriFemSheetError(
            "SHAPELY_CDT_UNAVAILABLE",
            "constrained-Delaunay meshing requires Shapely >= 2.1",
        )
    linework = unary_union(
        (island.boundary, *(item.footprint.boundary for item in contacts))
    )
    faces = [
        face
        for face in polygonize(linework)
        if island.covers(face.representative_point())
    ]
    if not faces:
        raise TriFemSheetError("MESH_FACE_MISSING", "constraint linework made no face")
    faces.sort(key=lambda item: _normalised_polygon(item, label="mesh face").wkb)
    triangle_keys: set[tuple[tuple[float, float], ...]] = set()
    for face in faces:
        result = _constrained_delaunay_triangles(face)
        for geometry in result.geoms:
            if not isinstance(geometry, Polygon):
                raise TriFemSheetError("MESH_TRIANGLE_INVALID", "CDT emitted non-polygon")
            if not island.covers(geometry):
                raise TriFemSheetError("MESH_CROSSES_VOID", "CDT triangle exits artwork")
            triangle_keys.add(_triangle_key(geometry))
    coordinate_triangles = tuple(sorted(triangle_keys))
    if not coordinate_triangles:
        raise TriFemSheetError("MESH_TRIANGLE_MISSING", "CDT emitted no triangles")
    predicted = len(coordinate_triangles) * (4**refinement_levels)
    if predicted > max_triangles:
        raise TriFemSheetError(
            "MESH_BOUND_EXCEEDED",
            f"refined triangle count {predicted:,} exceeds {max_triangles:,}",
        )
    for _ in range(refinement_levels):
        coordinate_triangles = _subdivide_triangles(coordinate_triangles)
    coordinates = sorted(
        {point for triangle in coordinate_triangles for point in triangle}
    )
    if len(coordinates) > max_nodes or len(coordinate_triangles) > max_triangles:
        raise TriFemSheetError(
            "MESH_BOUND_EXCEEDED",
            f"mesh {len(coordinates):,}/{len(coordinate_triangles):,} exceeds bounds",
        )
    node_by_coordinate = {point: index for index, point in enumerate(coordinates)}
    node_xy = np.asarray(coordinates, dtype=np.float64)
    triangle_indices = np.asarray(
        [
            tuple(node_by_coordinate[point] for point in triangle)
            for triangle in coordinate_triangles
        ],
        dtype=np.int64,
    )
    scale = max(
        island.bounds[2] - island.bounds[0],
        island.bounds[3] - island.bounds[1],
    )
    area_tolerance = max(
        island.area * 2.0e-11,
        _FLOAT_EPS * scale * scale * 512.0,
    )
    triangle_polygons: list[Polygon] = []
    triangle_areas: list[float] = []
    for row in triangle_indices:
        triangle = Polygon(node_xy[row])
        if not triangle.is_valid or triangle.area <= area_tolerance * 1.0e-6:
            raise TriFemSheetError(
                "MESH_TRIANGLE_DEGENERATE",
                "mesh contains a degenerate triangle",
            )
        if not island.covers(triangle):
            raise TriFemSheetError("MESH_CROSSES_VOID", "refined triangle exits artwork")
        triangle_polygons.append(triangle)
        triangle_areas.append(float(triangle.area))
    covered = unary_union(triangle_polygons)
    if (
        abs(sum(triangle_areas) - island.area) > area_tolerance
        or covered.symmetric_difference(island).area > area_tolerance
    ):
        raise TriFemSheetError("MESH_COVERAGE_INVALID", "mesh does not cover island")
    compiled_contacts: list[CompiledFiniteSheetContact] = []
    triangle_tree = STRtree(tuple(triangle_polygons))
    for contact in contacts:
        contact_area_tolerance = max(
            contact.footprint.area * 2.0e-11,
            _FLOAT_EPS * scale * scale * 512.0,
        )
        candidate_indices = sorted(
            {int(candidate) for candidate in triangle_tree.query(contact.footprint)}
        )
        budget.consume(len(candidate_indices), label="contact/triangle")
        covered_triangles: list[int] = []
        contact_nodes: set[int] = set()
        covered_area = 0.0
        for triangle_index in candidate_indices:
            triangle = triangle_polygons[triangle_index]
            if contact.footprint.covers(triangle):
                covered_triangles.append(triangle_index)
                contact_nodes.update(map(int, triangle_indices[triangle_index]))
                covered_area += triangle.area
                continue
            if triangle.intersection(contact.footprint).area > contact_area_tolerance:
                raise TriFemSheetError(
                    "CONTACT_MESH_AMBIGUOUS",
                    f"triangle crosses contact {contact.contact_id!r} boundary",
                )
        if abs(covered_area - contact.footprint.area) > contact_area_tolerance:
            raise TriFemSheetError(
                "CONTACT_MESH_INCOMPLETE",
                f"mesh does not cover contact {contact.contact_id!r}",
            )
        compiled_contacts.append(
            CompiledFiniteSheetContact(
                contact_id=contact.contact_id,
                owner_id=contact.owner_id,
                footprint_area_m2=float(contact.footprint.area),
                node_indices=np.asarray(sorted(contact_nodes), dtype=np.int64),
                triangle_indices=np.asarray(covered_triangles, dtype=np.int64),
                footprint_sha256=_polygon_sha256(contact.footprint),
            )
        )
    canonical_nodes = _readonly_float_array(node_xy)
    canonical_triangles = _readonly_int_array(triangle_indices)
    island_hash = _polygon_sha256(island)
    manifest = _mesh_identity_manifest(
        nodes=canonical_nodes,
        triangles=canonical_triangles,
        contacts=tuple(compiled_contacts),
        island_area_m2=float(island.area),
        refinement_levels=refinement_levels,
        island_wkb_sha256=island_hash,
        contact_candidate_work=budget.used,
        contact_work_limit=budget.limit,
    )
    identity = _mesh_identity_sha256(manifest, canonical_nodes, canonical_triangles)
    return TriFemSheetMesh(
        node_xy_m=canonical_nodes,
        triangles=canonical_triangles,
        contacts=tuple(compiled_contacts),
        island_area_m2=float(island.area),
        refinement_levels=refinement_levels,
        island_wkb_sha256=island_hash,
        contact_candidate_work=budget.used,
        contact_work_limit=budget.limit,
        mesh_identity_sha256=identity,
    )


def _compile_stiffness(mesh: TriFemSheetMesh) -> csc_matrix:
    rows: list[int] = []
    columns: list[int] = []
    values: list[float] = []
    scale = max(
        float(np.ptp(mesh.node_xy_m[:, 0])),
        float(np.ptp(mesh.node_xy_m[:, 1])),
    )
    area_floor = _FLOAT_EPS * scale * scale * 32.0
    for triangle_index, triangle in enumerate(mesh.triangles):
        points = mesh.node_xy_m[triangle]
        determinant = (
            (points[1, 0] - points[0, 0])
            * (points[2, 1] - points[0, 1])
            - (points[2, 0] - points[0, 0])
            * (points[1, 1] - points[0, 1])
        )
        area = 0.5 * abs(float(determinant))
        if not isfinite(area) or area <= area_floor:
            raise TriFemSheetError(
                "MESH_TRIANGLE_DEGENERATE",
                "triangle area is numerically zero",
            )
        b = np.asarray(
            (
                points[1, 1] - points[2, 1],
                points[2, 1] - points[0, 1],
                points[0, 1] - points[1, 1],
            ),
            dtype=np.float64,
        )
        c = np.asarray(
            (
                points[2, 0] - points[1, 0],
                points[0, 0] - points[2, 0],
                points[1, 0] - points[0, 0],
            ),
            dtype=np.float64,
        )
        gradient_scale = 1.0 / (2.0 * area)
        gram_scale = np.sqrt(area)
        for local_index, global_node in enumerate(triangle):
            rows.extend((2 * triangle_index, 2 * triangle_index + 1))
            columns.extend((int(global_node), int(global_node)))
            values.extend(
                (
                    float(gram_scale * b[local_index] * gradient_scale),
                    float(gram_scale * c[local_index] * gradient_scale),
                )
            )
    count = len(mesh.node_xy_m)
    gram = coo_matrix(
        (values, (rows, columns)),
        shape=(2 * len(mesh.triangles), count),
    ).tocsc()
    stiffness = csc_matrix(gram.T @ gram)
    stiffness = _readonly_real_csc(stiffness, label="compiled FEM stiffness")
    _validate_floating_psd(
        stiffness,
        label="compiled FEM stiffness",
        structural_gram_attested=True,
    )
    return stiffness


def _build_operator(
    *,
    island_id: str,
    mesh: TriFemSheetMesh,
    stiffness: csc_matrix,
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float,
    convergence_attestation: _MeshConvergenceAttestation | None,
) -> TriFemSheetOperator:
    manifest = _operator_manifest(
        island_id=island_id,
        mesh=mesh,
        stiffness=stiffness,
        conductivity_s_per_m=conductivity_s_per_m,
        thickness_m=thickness_m,
        permeability_h_per_m=permeability_h_per_m,
        convergence_attestation=convergence_attestation,
    )
    identity = sha256(_canonical_json(manifest)).hexdigest()
    return TriFemSheetOperator(
        island_id=island_id,
        mesh=mesh,
        stiffness=stiffness,
        conductivity_s_per_m=conductivity_s_per_m,
        thickness_m=thickness_m,
        permeability_h_per_m=permeability_h_per_m,
        convergence_attestation=convergence_attestation,
        identity_sha256=identity,
        identity_manifest=manifest,
    )


def compile_tri_fem_sheet(
    island_id: str,
    island_polygon: Polygon,
    *,
    contacts: Sequence[FiniteSheetContact] = (),
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float = MU_0_H_PER_M,
    refinement_levels: int = 0,
    max_nodes: int = _DEFAULT_MAX_NODES,
    max_triangles: int = _DEFAULT_MAX_TRIANGLES,
    max_contacts: int = _DEFAULT_MAX_CONTACTS,
    max_contact_work: int = _DEFAULT_MAX_CONTACT_WORK,
) -> TriFemSheetOperator:
    """Low-level fixed-refinement compile; result is production-ineligible.

    No geometry repair, point fallback, clipping, or convergence claim is
    performed.  Call :func:`compile_converged_tri_fem_sheet` when a bounded
    contact-QoI convergence attestation is required.
    """

    canonical_island_id = _require_id(island_id, label="island_id")
    island = _normalised_polygon(island_polygon, label="island_polygon")
    conductivity = _positive(conductivity_s_per_m, label="conductivity_s_per_m")
    thickness = _positive(thickness_m, label="thickness_m")
    permeability = _positive(permeability_h_per_m, label="permeability_h_per_m")
    levels = _bounded_integer(
        refinement_levels,
        label="refinement_levels",
        minimum=0,
        maximum=_MAX_REFINEMENT_LEVELS,
    )
    node_bound = _bounded_integer(
        max_nodes,
        label="max_nodes",
        minimum=3,
        maximum=50_000_000,
    )
    triangle_bound = _bounded_integer(
        max_triangles,
        label="max_triangles",
        minimum=1,
        maximum=100_000_000,
    )
    contact_bound = _bounded_integer(
        max_contacts,
        label="max_contacts",
        minimum=0,
        maximum=1_000_000,
    )
    work_limit = _bounded_integer(
        max_contact_work,
        label="max_contact_work",
        minimum=0,
        maximum=1_000_000_000,
    )
    budget = _ContactWorkBudget(work_limit)
    canonical_contacts = _prepare_contacts(
        island,
        contacts,
        max_contacts=contact_bound,
        budget=budget,
    )
    mesh = _compile_mesh(
        island,
        canonical_contacts,
        refinement_levels=levels,
        max_nodes=node_bound,
        max_triangles=triangle_bound,
        budget=budget,
    )
    stiffness = _compile_stiffness(mesh)
    return _build_operator(
        island_id=canonical_island_id,
        mesh=mesh,
        stiffness=stiffness,
        conductivity_s_per_m=conductivity,
        thickness_m=thickness,
        permeability_h_per_m=permeability,
        convergence_attestation=None,
    )


def _make_attestation(
    *,
    coarse_operator: TriFemSheetOperator,
    fine_operator: TriFemSheetOperator,
    coarse_qoi: NDArray[np.float64],
    fine_qoi: NDArray[np.float64],
    relative_rms_change: float,
    relative_max_change: float,
    relative_rms_tolerance: float,
    relative_max_tolerance: float,
    total_work: int,
    work_limit: int,
) -> _MeshConvergenceAttestation:
    if coarse_operator.production_eligible or fine_operator.production_eligible:
        raise TriFemSheetError(
            "CONVERGENCE_INVALID",
            "attestation levels must be fixed-refinement operators",
        )
    contact_ids = coarse_operator.contact_ids
    if fine_operator.contact_ids != contact_ids:
        raise TriFemSheetError(
            "CONVERGENCE_INVALID",
            "adjacent operators have different contact identities",
        )
    coarse_level = coarse_operator.mesh.refinement_levels
    fine_level = fine_operator.mesh.refinement_levels
    if fine_level != coarse_level + 1:
        raise TriFemSheetError(
            "CONVERGENCE_INVALID",
            "attestation operators are not adjacent refinement levels",
        )
    observed_qoi = (
        _float_matrix_sha256(coarse_qoi, label="coarse contact QoI"),
        _float_matrix_sha256(fine_qoi, label="fine contact QoI"),
    )
    recomputed_qoi = (
        _float_matrix_sha256(
            coarse_operator.contact_reduced_stiffness(),
            label="recomputed coarse contact QoI",
        ),
        _float_matrix_sha256(
            fine_operator.contact_reduced_stiffness(),
            label="recomputed fine contact QoI",
        ),
    )
    if observed_qoi != recomputed_qoi:
        raise TriFemSheetError(
            "CONVERGENCE_QOI_MISMATCH",
            "adjacent contact QoI matrices changed during attestation",
        )
    operator_identities = (
        coarse_operator.identity_sha256,
        fine_operator.identity_sha256,
    )
    payload = _convergence_payload(
        contact_ids=contact_ids,
        coarse_level=coarse_level,
        fine_level=fine_level,
        relative_rms_change=relative_rms_change,
        relative_max_change=relative_max_change,
        relative_rms_tolerance=relative_rms_tolerance,
        relative_max_tolerance=relative_max_tolerance,
        operator_identity_sha256=operator_identities,
        contact_qoi_sha256=recomputed_qoi,
        total_work=total_work,
        work_limit=work_limit,
    )
    identity = sha256(_canonical_json(payload)).hexdigest()
    issuer = object()
    _ATTESTATION_ISSUERS[id(issuer)] = (issuer, identity)
    return _MeshConvergenceAttestation(
        contact_ids=contact_ids,
        coarse_level=coarse_level,
        fine_level=fine_level,
        relative_rms_change=relative_rms_change,
        relative_max_change=relative_max_change,
        relative_rms_tolerance=relative_rms_tolerance,
        relative_max_tolerance=relative_max_tolerance,
        operator_identity_sha256=operator_identities,
        contact_qoi_sha256=recomputed_qoi,
        total_work=total_work,
        work_limit=work_limit,
        attestation_sha256=identity,
        _issuer=issuer,
    )


def compile_converged_tri_fem_sheet(
    island_id: str,
    island_polygon: Polygon,
    *,
    contacts: Sequence[FiniteSheetContact],
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float = MU_0_H_PER_M,
    starting_refinement_level: int = 0,
    max_refinement_level: int = 5,
    relative_rms_tolerance: float = 0.015,
    relative_max_tolerance: float = 0.03,
    max_nodes: int = _DEFAULT_MAX_NODES,
    max_triangles: int = _DEFAULT_MAX_TRIANGLES,
    max_contacts: int = _DEFAULT_MAX_CONTACTS,
    max_contact_work: int = _DEFAULT_MAX_CONTACT_WORK,
    max_convergence_contacts: int = _DEFAULT_MAX_CONVERGENCE_CONTACTS,
    max_convergence_work: int = _DEFAULT_MAX_CONVERGENCE_WORK,
) -> TriFemSheetOperator:
    """Compile until adjacent-level contact-reduced stiffness QoIs converge."""

    contact_values = tuple(contacts)
    convergence_contact_bound = _bounded_integer(
        max_convergence_contacts,
        label="max_convergence_contacts",
        minimum=2,
        maximum=10_000,
    )
    if len(contact_values) < 2:
        raise TriFemSheetError(
            "MESH_QOI_CONTACTS_REQUIRED",
            "contact-QoI convergence requires at least two contacts",
        )
    if len(contact_values) > convergence_contact_bound:
        raise TriFemSheetError(
            "MESH_QOI_CONTACT_BOUND_EXCEEDED",
            "contact-QoI convergence contact count exceeds its bound",
        )
    start = _bounded_integer(
        starting_refinement_level,
        label="starting_refinement_level",
        minimum=0,
        maximum=_MAX_REFINEMENT_LEVELS - 1,
    )
    maximum = _bounded_integer(
        max_refinement_level,
        label="max_refinement_level",
        minimum=1,
        maximum=_MAX_REFINEMENT_LEVELS,
    )
    if maximum <= start:
        raise TriFemSheetError(
            "CONVERGENCE_INVALID",
            "max_refinement_level must exceed starting_refinement_level",
        )
    rms_tolerance = _nonnegative(
        relative_rms_tolerance,
        label="relative_rms_tolerance",
    )
    max_tolerance = _nonnegative(
        relative_max_tolerance,
        label="relative_max_tolerance",
    )
    work_limit = _bounded_integer(
        max_convergence_work,
        label="max_convergence_work",
        minimum=1,
        maximum=1_000_000_000,
    )
    previous: NDArray[np.float64] | None = None
    previous_operator: TriFemSheetOperator | None = None
    total_work = 0
    last_metrics: tuple[float, float] | None = None
    for level in range(start, maximum + 1):
        operator = compile_tri_fem_sheet(
            island_id,
            island_polygon,
            contacts=contact_values,
            conductivity_s_per_m=conductivity_s_per_m,
            thickness_m=thickness_m,
            permeability_h_per_m=permeability_h_per_m,
            refinement_levels=level,
            max_nodes=max_nodes,
            max_triangles=max_triangles,
            max_contacts=max_contacts,
            max_contact_work=max_contact_work,
        )
        current = operator.contact_reduced_stiffness()
        total_work += (
            len(operator.mesh.node_xy_m)
            + len(operator.mesh.triangles)
            + operator.mesh.contact_candidate_work
        )
        if total_work > work_limit:
            raise TriFemSheetError(
                "MESH_CONVERGENCE_BOUND_EXCEEDED",
                f"convergence work {total_work:,} exceeds {work_limit:,}",
            )
        if previous is not None:
            assert previous_operator is not None
            difference = current - previous
            rms_scale = max(float(np.linalg.norm(current)), np.finfo(float).tiny)
            max_scale = max(
                float(np.max(np.abs(current), initial=0.0)),
                np.finfo(float).tiny,
            )
            rms_change = float(np.linalg.norm(difference) / rms_scale)
            max_change = float(
                np.max(np.abs(difference), initial=0.0) / max_scale
            )
            last_metrics = (rms_change, max_change)
            if rms_change <= rms_tolerance and max_change <= max_tolerance:
                attestation = _make_attestation(
                    coarse_operator=previous_operator,
                    fine_operator=operator,
                    coarse_qoi=previous,
                    fine_qoi=current,
                    relative_rms_change=rms_change,
                    relative_max_change=max_change,
                    relative_rms_tolerance=rms_tolerance,
                    relative_max_tolerance=max_tolerance,
                    total_work=total_work,
                    work_limit=work_limit,
                )
                return _build_operator(
                    island_id=operator.island_id,
                    mesh=operator.mesh,
                    stiffness=operator.stiffness,
                    conductivity_s_per_m=operator.conductivity_s_per_m,
                    thickness_m=operator.thickness_m,
                    permeability_h_per_m=operator.permeability_h_per_m,
                    convergence_attestation=attestation,
                )
        previous = current
        previous_operator = operator
    rms_change, max_change = last_metrics or (float("inf"), float("inf"))
    raise TriFemSheetError(
        "MESH_QOI_NOT_CONVERGED",
        f"contact K change rms={rms_change:.3e}, max={max_change:.3e}",
    )


__all__ = [
    "CompiledFiniteSheetContact",
    "EquipotentialElectrodeContraction",
    "FiniteSheetContact",
    "MU_0_H_PER_M",
    "SheetImpedanceModel",
    "TRI_FEM_SHEET_COMPILER_ID",
    "TriFemSheetError",
    "TriFemSheetMesh",
    "TriFemSheetOperator",
    "common_mode_copper_sheet_impedance",
    "compile_converged_tri_fem_sheet",
    "compile_tri_fem_sheet",
    "dc_sheet_conductance_s_per_square",
]
