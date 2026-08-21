"""Dormant matching-mesh N-layer P1 FEM lateral stack operator.

The stack has ``N >= 2`` conductors and exactly ``N - 1`` adjacent gaps.  One
controlled, conforming triangular mesh is shared by every layer.  The dense
absolute-layer impedance is the unique candidate used here whose projection
onto adjacent gap-loop coordinates reproduces the exact two-face strip model.
This replacement stamp is deliberately disconnected from production wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite, pi
from types import MappingProxyType
from typing import Final, Mapping, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
import shapely
from scipy.sparse import csc_matrix, kron
from shapely import STRtree
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .mfdm import copper_two_face_surface_impedance
from .tri_fem_pair import (
    MatchingP1MeshEvidence,
    PairConductor,
    PairGap,
    TriFemPairError,
    compile_matching_p1_mesh,
)


TRI_FEM_STACK_COMPILER_ID: Final = "matching-p1-n-layer-stack-replacement-v1"
TRI_FEM_STACK_STAMP_SEMANTICS: Final = (
    "replaces-all-independent-overlap-sheet-stamps-exhaustive-adjacent-gaps-v1"
)
_MAX_LAYERS: Final = 64
_MAX_WORK: Final = 50_000_000
_MAX_CANDIDATES: Final = 4_000_000
_MAX_STAMP_NNZ: Final = 20_000_000
_MAX_TEMPORARY_BYTES: Final = 512 * 1024 * 1024
_DEFAULT_TEMPORARY_BYTES: Final = 256 * 1024 * 1024
_POLYGON_TEMPORARY_BYTES: Final = 512
_STAMP_TEMPORARY_BYTES: Final = 40
_GAUGE_TEMPORARY_BYTES_PER_NODE: Final = 24
_PROJECTION_TOLERANCE: Final = 2.0e-11
_GAUGE_TOLERANCE: Final = 3.0e-11
_CONDITION_LIMIT: Final = 1.0e12
_MESH_ISSUERS: Final[dict[int, tuple[object, str]]] = {}


class TriFemStackError(ValueError):
    """Fail-closed stack mesh, topology, source, or operator error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "TRI_FEM_STACK_INVALID"
        super().__init__(f"{self.code}: {message}")


def _required_id(value: object, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise TriFemStackError("IDENTITY_INVALID", f"{label} must be nonblank")
    return text


def _sha256_id(value: object, *, label: str) -> str:
    text = _required_id(value, label=label).casefold()
    if len(text) != 64 or any(item not in "0123456789abcdef" for item in text):
        raise TriFemStackError("IDENTITY_INVALID", f"{label} must be SHA-256")
    return text


def _bound(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise TriFemStackError("WORK_BOUND_INVALID", f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TriFemStackError("WORK_BOUND_INVALID", f"{label} must be an integer") from exc
    if result != value or result < 1 or result > maximum:
        raise TriFemStackError(
            "WORK_BOUND_INVALID",
            f"{label} must be in [1, {maximum}]",
        )
    return result


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TriFemStackError("IDENTITY_INVALID", "manifest is not canonical JSON") from exc


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _readonly_complex(value: ArrayLike) -> NDArray[np.complex128]:
    result = np.array(value, dtype=np.complex128, order="C", copy=True)
    if not np.all(np.isfinite(result)):
        raise TriFemStackError("MATRIX_INVALID", "matrix contains non-finite values")
    result.setflags(write=False)
    return result


def _positive_real(matrix: NDArray[np.complex128], *, label: str) -> float:
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise TriFemStackError("MATRIX_INVALID", f"{label} must be square")
    if not np.all(np.isfinite(matrix)):
        raise TriFemStackError("MATRIX_INVALID", f"{label} is non-finite")
    scale = max(
        np.finfo(np.float64).tiny,
        float(np.max(np.abs(matrix), initial=0.0)),
    )
    if np.max(np.abs(matrix - matrix.T), initial=0.0) > 2.0e-12 * scale:
        raise TriFemStackError("MATRIX_NON_RECIPROCAL", f"{label} is not symmetric")
    hermitian = 0.5 * (matrix + matrix.conj().T)
    minimum = float(np.linalg.eigvalsh(hermitian)[0])
    if not isfinite(minimum) or minimum < -5.0e-12 * scale:
        raise TriFemStackError("MATRIX_NON_PASSIVE", f"{label} is not positive real")
    return minimum


def _revalidate_matching_mesh(mesh: MatchingP1MeshEvidence) -> None:
    try:
        mesh.revalidate()
    except TriFemPairError as exc:
        raise TriFemStackError(exc.code, str(exc)) from exc


def _is_full_edge(
    triangle: Polygon,
    start: Sequence[float],
    stop: Sequence[float],
    tolerance: float,
) -> bool:
    coordinates = tuple(triangle.exterior.coords)[:-1]
    for index, first in enumerate(coordinates):
        second = coordinates[(index + 1) % 3]
        direct = (
            np.linalg.norm(np.asarray(first) - np.asarray(start)) <= tolerance
            and np.linalg.norm(np.asarray(second) - np.asarray(stop)) <= tolerance
        )
        reverse = (
            np.linalg.norm(np.asarray(first) - np.asarray(stop)) <= tolerance
            and np.linalg.norm(np.asarray(second) - np.asarray(start)) <= tolerance
        )
        if direct or reverse:
            return True
    return False


def _validate_conforming_mesh(
    mesh: MatchingP1MeshEvidence,
    *,
    max_candidates: int,
    max_work: int,
    max_temporary_bytes: int,
) -> tuple[int, int, int, float, int]:
    nodes = mesh.node_xy_m
    triangles = mesh.triangles
    temporary = _POLYGON_TEMPORARY_BYTES * len(triangles)
    if temporary > max_temporary_bytes:
        raise TriFemStackError(
            "TEMPORARY_BOUND_EXCEEDED",
            "mesh conformance temporary-byte estimate exceeds its bound",
        )
    polygons = tuple(Polygon(nodes[row]) for row in triangles)
    if any(item.is_empty or not item.is_valid or item.area <= 0.0 for item in polygons):
        raise TriFemStackError("MESH_INVALID", "mesh contains an invalid triangle")
    tree = STRtree(polygons)
    extent = np.ptp(nodes, axis=0)
    length_tolerance = 512.0 * np.finfo(float).eps * max(
        float(np.max(extent)),
        1.0e-30,
    )
    candidates = 0
    for left_index, left in enumerate(polygons):
        for right_index in sorted(int(item) for item in tree.query(left)):
            if right_index <= left_index:
                continue
            candidates += 1
            if candidates > max_candidates or len(polygons) + candidates > max_work:
                raise TriFemStackError(
                    "MESH_WORK_BOUND_EXCEEDED",
                    "mesh conformance candidate work exceeds its bound",
                )
            right = polygons[right_index]
            intersection = left.intersection(right)
            if intersection.is_empty:
                continue
            area_tolerance = 2.0e-11 * min(float(left.area), float(right.area))
            if float(intersection.area) > area_tolerance:
                raise TriFemStackError(
                    "MESH_INTERIOR_OVERLAP",
                    "mesh triangles overlap in positive area",
                )
            shared = set(map(int, triangles[left_index])) & set(
                map(int, triangles[right_index])
            )
            if float(intersection.length) <= length_tolerance:
                if len(shared) != 1:
                    raise TriFemStackError(
                        "MESH_NONCONFORMING",
                        "point contact is not one shared mesh vertex",
                    )
                point = nodes[next(iter(shared))]
                bounds = np.asarray(intersection.bounds).reshape(2, 2)
                if np.max(np.abs(bounds - point)) > length_tolerance:
                    raise TriFemStackError(
                        "MESH_NONCONFORMING",
                        "triangles meet away from their shared vertex",
                    )
                continue
            lines = (
                (intersection,)
                if intersection.geom_type == "LineString"
                else tuple(
                    item
                    for item in getattr(intersection, "geoms", ())
                    if item.geom_type == "LineString"
                    and item.length > length_tolerance
                )
            )
            if len(shared) != 2 or len(lines) != 1:
                raise TriFemStackError(
                    "MESH_NONCONFORMING",
                    "shared boundary is not one full indexed edge",
                )
            coordinates = tuple(lines[0].coords)
            if len(coordinates) < 2 or not (
                _is_full_edge(left, coordinates[0], coordinates[-1], length_tolerance)
                and _is_full_edge(
                    right,
                    coordinates[0],
                    coordinates[-1],
                    length_tolerance,
                )
            ):
                raise TriFemStackError(
                    "MESH_NONCONFORMING",
                    "mesh contains a hanging-node shared boundary",
                )
    domain = unary_union(polygons)
    if not isinstance(domain, Polygon):
        raise TriFemStackError(
            "MESH_DISCONNECTED",
            "controlled matching mesh must form one connected polygon",
        )
    total_area = float(sum(item.area for item in polygons))
    if not np.isclose(float(domain.area), total_area, rtol=2.0e-11, atol=0.0):
        raise TriFemStackError("MESH_COVERAGE_INVALID", "mesh coverage is inconsistent")
    work = len(polygons) + candidates
    return candidates, work, temporary, float(domain.area), len(domain.interiors)


def _mesh_manifest(
    mesh: MatchingP1MeshEvidence,
    *,
    candidate_count: int,
    conformance_work: int,
    conformance_work_limit: int,
    temporary_bytes: int,
    temporary_bytes_limit: int,
    domain_area_m2: float,
    hole_count: int,
) -> Mapping[str, object]:
    return {
        "compiler_id": TRI_FEM_STACK_COMPILER_ID,
        "matching_mesh_identity_sha256": mesh.mesh_identity_sha256,
        "source_mesh_identity_sha256": mesh.source_mesh_identity_sha256,
        "candidate_count": candidate_count,
        "conformance_work": conformance_work,
        "conformance_work_limit": conformance_work_limit,
        "temporary_bytes": temporary_bytes,
        "temporary_bytes_limit": temporary_bytes_limit,
        "domain_area_m2": domain_area_m2,
        "hole_count": hole_count,
        "shapely_version": shapely.__version__,
        "geos_version": shapely.geos_version_string,
        "production_eligible": False,
    }


@dataclass(frozen=True, slots=True)
class ControlledStackMesh:
    """Internally issued conforming matching mesh; never a convergence claim."""

    matching_mesh: MatchingP1MeshEvidence
    candidate_count: int
    conformance_work: int
    conformance_work_limit: int
    temporary_bytes: int
    temporary_bytes_limit: int
    domain_area_m2: float
    hole_count: int
    identity_sha256: str
    identity_manifest: Mapping[str, object]
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        issued = _MESH_ISSUERS.get(id(self._issuer))
        if issued is None or issued[0] is not self._issuer:
            raise TriFemStackError(
                "MESH_EVIDENCE_PRIVATE",
                "controlled mesh evidence must be issued by its compiler",
            )
        _revalidate_matching_mesh(self.matching_mesh)
        if self.matching_mesh.mesh_convergence_evidence_sha256 is not None:
            raise TriFemStackError(
                "MESH_CONVERGENCE_UNTRUSTED",
                "stack mesh forbids arbitrary convergence digests",
            )
        manifest = _mesh_manifest(
            self.matching_mesh,
            candidate_count=self.candidate_count,
            conformance_work=self.conformance_work,
            conformance_work_limit=self.conformance_work_limit,
            temporary_bytes=self.temporary_bytes,
            temporary_bytes_limit=self.temporary_bytes_limit,
            domain_area_m2=self.domain_area_m2,
            hole_count=self.hole_count,
        )
        observed = sha256(_canonical_json(manifest)).hexdigest()
        if issued[1] != observed or self.identity_sha256 != observed:
            raise TriFemStackError("MESH_IDENTITY_MISMATCH", "mesh evidence is stale")
        if _canonical_json(_plain(self.identity_manifest)) != _canonical_json(manifest):
            raise TriFemStackError("MESH_MANIFEST_MISMATCH", "mesh manifest is stale")
        object.__setattr__(self, "identity_manifest", _freeze(manifest))

    @property
    def production_eligible(self) -> bool:
        return False

    def revalidate(self) -> None:
        _revalidate_matching_mesh(self.matching_mesh)
        manifest = _mesh_manifest(
            self.matching_mesh,
            candidate_count=self.candidate_count,
            conformance_work=self.conformance_work,
            conformance_work_limit=self.conformance_work_limit,
            temporary_bytes=self.temporary_bytes,
            temporary_bytes_limit=self.temporary_bytes_limit,
            domain_area_m2=self.domain_area_m2,
            hole_count=self.hole_count,
        )
        observed = sha256(_canonical_json(manifest)).hexdigest()
        issued = _MESH_ISSUERS.get(id(self._issuer))
        if issued != (self._issuer, observed) or observed != self.identity_sha256:
            raise TriFemStackError("MESH_IDENTITY_MISMATCH", "mesh evidence changed")
        if _canonical_json(_plain(self.identity_manifest)) != _canonical_json(manifest):
            raise TriFemStackError("MESH_MANIFEST_MISMATCH", "mesh manifest changed")


def compile_controlled_stack_mesh(
    node_xy_m: ArrayLike,
    triangles: ArrayLike,
    *,
    source_mesh_identity_sha256: str,
    max_nodes: int = 250_000,
    max_triangles: int = 500_000,
    max_assembly_work: int = 20_000_000,
    max_candidates: int = _MAX_CANDIDATES,
    max_conformance_work: int = _MAX_WORK,
    max_temporary_bytes: int = _DEFAULT_TEMPORARY_BYTES,
) -> ControlledStackMesh:
    """Compile and geometrically validate one controlled common mesh."""

    candidate_limit = _bound(
        max_candidates,
        label="max_candidates",
        maximum=_MAX_CANDIDATES,
    )
    work_limit = _bound(
        max_conformance_work,
        label="max_conformance_work",
        maximum=_MAX_WORK,
    )
    temporary_limit = _bound(
        max_temporary_bytes,
        label="max_temporary_bytes",
        maximum=_MAX_TEMPORARY_BYTES,
    )
    try:
        matching = compile_matching_p1_mesh(
            node_xy_m,
            triangles,
            source_mesh_identity_sha256=source_mesh_identity_sha256,
            mesh_convergence_evidence_sha256=None,
            max_nodes=max_nodes,
            max_triangles=max_triangles,
            max_assembly_work=max_assembly_work,
        )
    except TriFemPairError as exc:
        raise TriFemStackError(exc.code, str(exc)) from exc
    candidates, work, temporary, area, holes = _validate_conforming_mesh(
        matching,
        max_candidates=candidate_limit,
        max_work=work_limit,
        max_temporary_bytes=temporary_limit,
    )
    manifest = _mesh_manifest(
        matching,
        candidate_count=candidates,
        conformance_work=work,
        conformance_work_limit=work_limit,
        temporary_bytes=temporary,
        temporary_bytes_limit=temporary_limit,
        domain_area_m2=area,
        hole_count=holes,
    )
    identity = sha256(_canonical_json(manifest)).hexdigest()
    issuer = object()
    _MESH_ISSUERS[id(issuer)] = (issuer, identity)
    return ControlledStackMesh(
        matching,
        candidates,
        work,
        work_limit,
        temporary,
        temporary_limit,
        area,
        holes,
        identity,
        manifest,
        issuer,
    )


@dataclass(frozen=True, slots=True)
class StackTopologyEvidence:
    source_topology_identity_sha256: str
    layer_ids: tuple[str, ...]
    active_gap_ids: tuple[str, ...]
    replacement_owned_layer_ids: tuple[str, ...]
    independent_overlap_sheet_layer_ids: tuple[str, ...] = ()
    identity_sha256: str = field(init=False, default="")

    def __post_init__(self) -> None:
        source = _sha256_id(
            self.source_topology_identity_sha256,
            label="source_topology_identity_sha256",
        )
        layers = tuple(_required_id(item, label="layer id") for item in self.layer_ids)
        gaps = tuple(_required_id(item, label="gap id") for item in self.active_gap_ids)
        owned = tuple(
            _required_id(item, label="replacement-owned layer id")
            for item in self.replacement_owned_layer_ids
        )
        independent = tuple(
            _required_id(item, label="independent overlap layer id")
            for item in self.independent_overlap_sheet_layer_ids
        )
        if len(layers) < 2 or len(layers) > _MAX_LAYERS:
            raise TriFemStackError("LAYER_COUNT_INVALID", "stack layer count is unsupported")
        if len({item.casefold() for item in layers}) != len(layers):
            raise TriFemStackError("TOPOLOGY_INVALID", "layer ids must be unique")
        if len(gaps) != len(layers) - 1:
            raise TriFemStackError("TOPOLOGY_INVALID", "active gaps must be exhaustive")
        if len({item.casefold() for item in gaps}) != len(gaps):
            raise TriFemStackError("TOPOLOGY_INVALID", "active gap ids must be unique")
        if tuple(item.casefold() for item in owned) != tuple(
            item.casefold() for item in layers
        ):
            raise TriFemStackError(
                "STAMP_OWNERSHIP_INVALID",
                "replacement ownership must cover every stack layer in order",
            )
        if independent:
            raise TriFemStackError(
                "PARALLEL_SHEET_STAMP_FORBIDDEN",
                "independent overlap sheet stamps are forbidden",
            )
        payload = {
            "source_topology_identity_sha256": source,
            "layer_ids": layers,
            "active_gap_ids": gaps,
            "replacement_owned_layer_ids": owned,
            "independent_overlap_sheet_layer_ids": independent,
        }
        object.__setattr__(self, "source_topology_identity_sha256", source)
        object.__setattr__(self, "layer_ids", layers)
        object.__setattr__(self, "active_gap_ids", gaps)
        object.__setattr__(self, "replacement_owned_layer_ids", owned)
        object.__setattr__(self, "independent_overlap_sheet_layer_ids", independent)
        object.__setattr__(self, "identity_sha256", sha256(_canonical_json(payload)).hexdigest())

    def revalidate(self) -> None:
        rebuilt = type(self)(
            self.source_topology_identity_sha256,
            self.layer_ids,
            self.active_gap_ids,
            self.replacement_owned_layer_ids,
            self.independent_overlap_sheet_layer_ids,
        )
        if rebuilt.identity_sha256 != self.identity_sha256:
            raise TriFemStackError("TOPOLOGY_IDENTITY_MISMATCH", "topology is stale")


def _conductor_manifest(value: PairConductor) -> Mapping[str, object]:
    return {
        "layer_id": value.layer_id,
        "conductivity_s_per_m": value.conductivity_s_per_m,
        "thickness_m": value.thickness_m,
        "permeability_h_per_m": value.permeability_h_per_m,
        "source_evidence_sha256": value.source_evidence_sha256,
    }


def _gap_manifest(value: PairGap) -> Mapping[str, object]:
    return {
        "gap_id": value.gap_id,
        "upper_layer_id": value.upper_layer_id,
        "lower_layer_id": value.lower_layer_id,
        "separation_m": value.separation_m,
        "permeability_h_per_m": value.permeability_h_per_m,
        "source_evidence_sha256": value.source_evidence_sha256,
    }


def stack_material_manifest_sha256(
    conductors: Sequence[PairConductor],
    gaps: Sequence[PairGap],
) -> str:
    conductor_values = tuple(conductors)
    gap_values = tuple(gaps)
    if not all(isinstance(item, PairConductor) for item in conductor_values):
        raise TriFemStackError("MATERIAL_INVALID", "typed conductors are required")
    if not all(isinstance(item, PairGap) for item in gap_values):
        raise TriFemStackError("MATERIAL_INVALID", "typed gaps are required")
    payload = {
        "conductors": tuple(_conductor_manifest(item) for item in conductor_values),
        "gaps": tuple(_gap_manifest(item) for item in gap_values),
    }
    return sha256(_canonical_json(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class TriFemStackSourceEvidence:
    source_model_identity_sha256: str
    material_manifest_sha256: str
    topology_identity_sha256: str
    mesh_identity_sha256: str
    conductor_source_sha256: tuple[str, ...]
    gap_source_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "source_model_identity_sha256",
            "material_manifest_sha256",
            "topology_identity_sha256",
            "mesh_identity_sha256",
        ):
            object.__setattr__(self, name, _sha256_id(getattr(self, name), label=name))
        conductors = tuple(
            _sha256_id(item, label="conductor source SHA-256")
            for item in self.conductor_source_sha256
        )
        gaps = tuple(
            _sha256_id(item, label="gap source SHA-256")
            for item in self.gap_source_sha256
        )
        object.__setattr__(self, "conductor_source_sha256", conductors)
        object.__setattr__(self, "gap_source_sha256", gaps)


def _operator_manifest(
    mesh: ControlledStackMesh,
    conductors: tuple[PairConductor, ...],
    gaps: tuple[PairGap, ...],
    topology: StackTopologyEvidence,
    source: TriFemStackSourceEvidence,
    *,
    dense_work: int,
    dense_work_limit: int,
    stamp_nnz_bound: int,
    stamp_nnz_limit: int,
    temporary_bytes: int,
    temporary_bytes_limit: int,
) -> Mapping[str, object]:
    return {
        "compiler_id": TRI_FEM_STACK_COMPILER_ID,
        "stamp_semantics": TRI_FEM_STACK_STAMP_SEMANTICS,
        "mesh_identity_sha256": mesh.identity_sha256,
        "conductors": tuple(_conductor_manifest(item) for item in conductors),
        "gaps": tuple(_gap_manifest(item) for item in gaps),
        "topology_identity_sha256": topology.identity_sha256,
        "source_evidence": {
            "source_model_identity_sha256": source.source_model_identity_sha256,
            "material_manifest_sha256": source.material_manifest_sha256,
            "topology_identity_sha256": source.topology_identity_sha256,
            "mesh_identity_sha256": source.mesh_identity_sha256,
            "conductor_source_sha256": source.conductor_source_sha256,
            "gap_source_sha256": source.gap_source_sha256,
        },
        "dense_work": dense_work,
        "dense_work_limit": dense_work_limit,
        "stamp_nnz_bound": stamp_nnz_bound,
        "stamp_nnz_limit": stamp_nnz_limit,
        "temporary_bytes": temporary_bytes,
        "temporary_bytes_limit": temporary_bytes_limit,
        "production_eligible": False,
    }


@dataclass(frozen=True, slots=True)
class TriFemStackEvaluation:
    frequency_hz: float
    incidence: NDArray[np.float64]
    gap_loop_impedance_ohm_per_square: NDArray[np.complex128]
    layer_impedance_ohm_per_square: NDArray[np.complex128]
    layer_admittance_s_per_square: NDArray[np.complex128]
    nodal_admittance_s: csc_matrix
    projection_relative_residual: float
    impedance_condition_number: float
    impedance_passivity_minimum: float
    admittance_passivity_minimum: float
    gauge_relative_residual: float


@dataclass(frozen=True, slots=True)
class TriFemStackOperator:
    mesh: ControlledStackMesh
    conductors: tuple[PairConductor, ...]
    gaps: tuple[PairGap, ...]
    topology: StackTopologyEvidence
    source_evidence: TriFemStackSourceEvidence
    dense_work: int
    dense_work_limit: int
    stamp_nnz_bound: int
    stamp_nnz_limit: int
    temporary_bytes: int
    temporary_bytes_limit: int
    identity_sha256: str
    identity_manifest: Mapping[str, object]

    def __post_init__(self) -> None:
        self.revalidate()
        object.__setattr__(self, "identity_manifest", _freeze(_plain(self.identity_manifest)))

    @property
    def production_eligible(self) -> bool:
        return False

    def require_production_eligible(self) -> None:
        raise TriFemStackError(
            "STACK_PRODUCTION_UNATTESTED",
            "global topology, stamp ownership, and convergence are not internally attested",
        )

    def revalidate(self) -> None:
        _validate_stack_inputs(
            self.conductors,
            self.gaps,
            self.topology,
            self.source_evidence,
            self.mesh,
        )
        manifest = _operator_manifest(
            self.mesh,
            self.conductors,
            self.gaps,
            self.topology,
            self.source_evidence,
            dense_work=self.dense_work,
            dense_work_limit=self.dense_work_limit,
            stamp_nnz_bound=self.stamp_nnz_bound,
            stamp_nnz_limit=self.stamp_nnz_limit,
            temporary_bytes=self.temporary_bytes,
            temporary_bytes_limit=self.temporary_bytes_limit,
        )
        if _canonical_json(_plain(self.identity_manifest)) != _canonical_json(manifest):
            raise TriFemStackError("OPERATOR_MANIFEST_MISMATCH", "operator manifest is stale")
        observed = sha256(_canonical_json(manifest)).hexdigest()
        if observed != self.identity_sha256:
            raise TriFemStackError("OPERATOR_IDENTITY_MISMATCH", "operator identity is stale")

    def evaluate(self, frequency_hz: float) -> TriFemStackEvaluation:
        self.revalidate()
        if isinstance(frequency_hz, (bool, np.bool_)):
            raise TriFemStackError("FREQUENCY_INVALID", "frequency must be a real scalar")
        try:
            frequency = float(frequency_hz)
        except (TypeError, ValueError) as exc:
            raise TriFemStackError("FREQUENCY_INVALID", "frequency must be numeric") from exc
        if not isfinite(frequency) or frequency < 0.0:
            raise TriFemStackError("FREQUENCY_INVALID", "frequency must be finite and >= 0")
        incidence, gap_loop, layer_impedance = _layer_impedance(
            self.conductors,
            self.gaps,
            frequency,
        )
        condition = float(np.linalg.cond(layer_impedance))
        if not isfinite(condition) or condition > _CONDITION_LIMIT:
            raise TriFemStackError(
                "IMPEDANCE_ILL_CONDITIONED",
                f"layer impedance condition {condition:.3e} exceeds {_CONDITION_LIMIT:.1e}",
            )
        impedance_minimum = _positive_real(layer_impedance, label="layer impedance")
        try:
            admittance = np.linalg.inv(layer_impedance)
        except np.linalg.LinAlgError as exc:
            raise TriFemStackError("IMPEDANCE_SINGULAR", "layer impedance is singular") from exc
        admittance_minimum = _positive_real(admittance, label="layer admittance")
        nodal = csc_matrix(
            kron(admittance, self.mesh.matching_mesh.stiffness, format="csc")
        )
        nodal.sum_duplicates()
        nodal.eliminate_zeros()
        if not np.all(np.isfinite(nodal.data)):
            raise TriFemStackError("STAMP_INVALID", "nodal stamp is non-finite")
        node_count = len(self.mesh.matching_mesh.node_xy_m)
        # For Y_layer (x) K, every per-layer constant gauge has residual
        # Y_layer[:, j] (x) (K @ 1).  Evaluate that identity from one bounded
        # mesh-sized vector rather than allocating the previous
        # (layer_count * node_count, layer_count) dense gauge matrix.
        stiffness_gauge = np.asarray(
            self.mesh.matching_mesh.stiffness
            @ np.ones(node_count, dtype=np.float64)
        ).reshape(-1)
        admittance_scale = float(np.max(np.abs(admittance), initial=0.0))
        stiffness_scale = float(
            np.max(
                np.abs(self.mesh.matching_mesh.stiffness.data),
                initial=0.0,
            )
        )
        scale = max(
            np.finfo(np.float64).tiny,
            admittance_scale * stiffness_scale,
        )
        gauge = float(
            admittance_scale
            * np.max(np.abs(stiffness_gauge), initial=0.0)
            / scale
        )
        if gauge > _GAUGE_TOLERANCE:
            raise TriFemStackError("STAMP_GAUGE_INVALID", "layer gauge residual is too large")
        # K is independently identity-checked and symmetric.  Therefore the
        # Kronecker stamp reciprocity residual is exactly the much smaller
        # dense layer-admittance residual; do not materialize nodal - nodal.T.
        layer_scale = max(np.finfo(np.float64).tiny, admittance_scale)
        reciprocity = float(
            np.max(np.abs(admittance - admittance.T), initial=0.0) / layer_scale
        )
        if reciprocity > 2.0e-11:
            raise TriFemStackError("STAMP_NON_RECIPROCAL", "nodal stamp is asymmetric")
        projection = incidence.T @ layer_impedance @ incidence
        projection_scale = max(
            np.finfo(np.float64).tiny,
            float(np.max(np.abs(gap_loop), initial=0.0)),
        )
        projection_residual = float(
            np.max(np.abs(projection - gap_loop), initial=0.0) / projection_scale
        )
        if projection_residual > _PROJECTION_TOLERANCE:
            raise TriFemStackError(
                "GAP_PROJECTION_INVALID",
                "absolute-layer impedance does not reproduce the gap-loop operator",
            )
        for matrix in (incidence, gap_loop, layer_impedance, admittance):
            matrix.setflags(write=False)
        nodal.data.setflags(write=False)
        nodal.indices.setflags(write=False)
        nodal.indptr.setflags(write=False)
        return TriFemStackEvaluation(
            frequency,
            incidence,
            gap_loop,
            layer_impedance,
            admittance,
            nodal,
            projection_residual,
            condition,
            impedance_minimum,
            admittance_minimum,
            gauge,
        )


def _validate_stack_inputs(
    conductors: tuple[PairConductor, ...],
    gaps: tuple[PairGap, ...],
    topology: StackTopologyEvidence,
    source: TriFemStackSourceEvidence,
    mesh: ControlledStackMesh,
) -> None:
    if not isinstance(mesh, ControlledStackMesh):
        raise TriFemStackError("MESH_INVALID", "controlled stack mesh is required")
    if not isinstance(topology, StackTopologyEvidence):
        raise TriFemStackError("TOPOLOGY_INVALID", "typed topology evidence is required")
    if not isinstance(source, TriFemStackSourceEvidence):
        raise TriFemStackError("SOURCE_INVALID", "typed source evidence is required")
    mesh.revalidate()
    topology.revalidate()
    if len(conductors) < 2 or len(conductors) > _MAX_LAYERS:
        raise TriFemStackError("LAYER_COUNT_INVALID", "stack layer count is unsupported")
    if len(gaps) != len(conductors) - 1:
        raise TriFemStackError("GAP_COUNT_INVALID", "stack needs exactly N-1 gaps")
    if not all(isinstance(item, PairConductor) for item in conductors):
        raise TriFemStackError("MATERIAL_INVALID", "typed conductors are required")
    if not all(isinstance(item, PairGap) for item in gaps):
        raise TriFemStackError("MATERIAL_INVALID", "typed gaps are required")
    layer_ids = tuple(item.layer_id.casefold() for item in conductors)
    gap_ids = tuple(item.gap_id.casefold() for item in gaps)
    if layer_ids != tuple(item.casefold() for item in topology.layer_ids):
        raise TriFemStackError("TOPOLOGY_MISMATCH", "topology layer order differs")
    if gap_ids != tuple(item.casefold() for item in topology.active_gap_ids):
        raise TriFemStackError("TOPOLOGY_MISMATCH", "topology gap order differs")
    for index, gap in enumerate(gaps):
        expected = layer_ids[index : index + 2]
        actual = (gap.upper_layer_id.casefold(), gap.lower_layer_id.casefold())
        if actual != expected:
            raise TriFemStackError(
                "GAP_ORDER_INVALID",
                f"gap {gap.gap_id!r} does not bind adjacent layers in order",
            )
    conductor_sources = tuple(item.source_evidence_sha256 for item in conductors)
    gap_sources = tuple(item.source_evidence_sha256 for item in gaps)
    if source.conductor_source_sha256 != conductor_sources:
        raise TriFemStackError("SOURCE_EVIDENCE_MISMATCH", "conductor hashes disagree")
    if source.gap_source_sha256 != gap_sources:
        raise TriFemStackError("SOURCE_EVIDENCE_MISMATCH", "gap hashes disagree")
    if source.material_manifest_sha256 != stack_material_manifest_sha256(
        conductors,
        gaps,
    ):
        raise TriFemStackError("SOURCE_EVIDENCE_MISMATCH", "material hash is stale")
    if source.topology_identity_sha256 != topology.identity_sha256:
        raise TriFemStackError("SOURCE_EVIDENCE_MISMATCH", "topology hash is stale")
    if source.mesh_identity_sha256 != mesh.identity_sha256:
        raise TriFemStackError("SOURCE_EVIDENCE_MISMATCH", "mesh hash is stale")


def _layer_impedance(
    conductors: tuple[PairConductor, ...],
    gaps: tuple[PairGap, ...],
    frequency_hz: float,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.complex128],
    NDArray[np.complex128],
]:
    layer_count = len(conductors)
    gap_count = len(gaps)
    incidence = np.zeros((layer_count, gap_count), dtype=np.float64)
    for gap_index in range(gap_count):
        incidence[gap_index, gap_index] = 1.0
        incidence[gap_index + 1, gap_index] = -1.0
    common = np.zeros((layer_count, layer_count), dtype=np.complex128)
    gap_loop = np.zeros((gap_count, gap_count), dtype=np.complex128)
    for layer, conductor in enumerate(conductors):
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            face = copper_two_face_surface_impedance(
                frequency_hz,
                conductor.conductivity_s_per_m,
                conductor.thickness_m,
                conductor.permeability_h_per_m,
            )
        if not np.all(np.isfinite(face)):
            raise TriFemStackError("MATERIAL_RESPONSE_INVALID", "copper response is non-finite")
        common[layer, layer] = 0.5 * complex(face[0, 0] + face[0, 1])
        mapping = np.zeros((2, gap_count), dtype=np.float64)
        if layer > 0:
            mapping[0, layer - 1] = -1.0
        if layer < gap_count:
            mapping[1, layer] = 1.0
        gap_loop += mapping.T @ face @ mapping
    omega = 2.0 * pi * frequency_hz
    for index, gap in enumerate(gaps):
        gap_loop[index, index] += (
            1j * omega * gap.permeability_h_per_m * gap.separation_m
        )
    gram = incidence.T @ incidence
    inverse_gram = np.linalg.inv(gram)
    correction = gap_loop - incidence.T @ common @ incidence
    layer_impedance = (
        common
        + incidence
        @ inverse_gram
        @ correction
        @ inverse_gram
        @ incidence.T
    )
    _positive_real(gap_loop, label="gap-loop impedance")
    _positive_real(layer_impedance, label="layer impedance")
    return incidence, gap_loop, layer_impedance


def compile_tri_fem_stack(
    mesh: ControlledStackMesh,
    conductors: Sequence[PairConductor],
    gaps: Sequence[PairGap],
    *,
    topology: StackTopologyEvidence,
    source_evidence: TriFemStackSourceEvidence,
    max_dense_work: int = _MAX_WORK,
    max_stamp_nnz: int = _MAX_STAMP_NNZ,
    max_temporary_bytes: int = _DEFAULT_TEMPORARY_BYTES,
) -> TriFemStackOperator:
    """Compile one exhaustive replacement N-layer lateral stack."""

    conductor_values = tuple(conductors)
    gap_values = tuple(gaps)
    _validate_stack_inputs(
        conductor_values,
        gap_values,
        topology,
        source_evidence,
        mesh,
    )
    layers = len(conductor_values)
    dense_limit = _bound(
        max_dense_work,
        label="max_dense_work",
        maximum=_MAX_WORK,
    )
    nnz_limit = _bound(max_stamp_nnz, label="max_stamp_nnz", maximum=_MAX_STAMP_NNZ)
    temporary_limit = _bound(
        max_temporary_bytes,
        label="max_temporary_bytes",
        maximum=_MAX_TEMPORARY_BYTES,
    )
    dense_work = 20 * layers**3
    stamp_nnz_bound = layers * layers * mesh.matching_mesh.stiffness.nnz
    temporary = (
        _STAMP_TEMPORARY_BYTES * stamp_nnz_bound
        + 16 * (6 * layers * layers)
        + _GAUGE_TEMPORARY_BYTES_PER_NODE
        * len(mesh.matching_mesh.node_xy_m)
    )
    if dense_work > dense_limit:
        raise TriFemStackError("WORK_BOUND_EXCEEDED", "dense stack work exceeds its bound")
    if stamp_nnz_bound > nnz_limit:
        raise TriFemStackError("STAMP_NNZ_BOUND_EXCEEDED", "stamp nnz exceeds its bound")
    if temporary > temporary_limit:
        raise TriFemStackError(
            "TEMPORARY_BOUND_EXCEEDED",
            "stack temporary-byte estimate exceeds its bound",
        )
    manifest = _operator_manifest(
        mesh,
        conductor_values,
        gap_values,
        topology,
        source_evidence,
        dense_work=dense_work,
        dense_work_limit=dense_limit,
        stamp_nnz_bound=stamp_nnz_bound,
        stamp_nnz_limit=nnz_limit,
        temporary_bytes=temporary,
        temporary_bytes_limit=temporary_limit,
    )
    identity = sha256(_canonical_json(manifest)).hexdigest()
    operator = TriFemStackOperator(
        mesh,
        conductor_values,
        gap_values,
        topology,
        source_evidence,
        dense_work,
        dense_limit,
        stamp_nnz_bound,
        nnz_limit,
        temporary,
        temporary_limit,
        identity,
        manifest,
    )
    operator.evaluate(0.0)
    return operator


__all__ = [
    "ControlledStackMesh",
    "StackTopologyEvidence",
    "TRI_FEM_STACK_COMPILER_ID",
    "TRI_FEM_STACK_STAMP_SEMANTICS",
    "TriFemStackError",
    "TriFemStackEvaluation",
    "TriFemStackOperator",
    "TriFemStackSourceEvidence",
    "compile_controlled_stack_mesh",
    "compile_tri_fem_stack",
    "stack_material_manifest_sha256",
]
