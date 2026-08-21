"""Dormant matching-mesh paired-layer P1 FEM lateral operator.

This bounded core represents exactly one adjacent conductor pair on one
validated common triangular mesh.  Its stamp *replaces* independent sheet
stamps over that overlap; adding both models in parallel is forbidden.  The
module is intentionally not connected to the production network compiler.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite, pi
from types import MappingProxyType
from typing import Final, Mapping, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.sparse import csc_matrix, coo_matrix, kron
from scipy.sparse.csgraph import connected_components

from .mfdm import MU_0_H_PER_M, copper_two_face_surface_impedance


TRI_FEM_PAIR_COMPILER_ID: Final = "matching-p1-paired-sheet-replacement-v2"
TRI_FEM_PAIR_STAMP_SEMANTICS: Final = (
    "replaces-independent-overlap-sheet-stamps-single-gap-v1"
)
_MAX_NODES: Final = 250_000
_MAX_TRIANGLES: Final = 500_000
_MAX_WORK: Final = 20_000_000
_PSD_DENSE_LIMIT: Final = 1_024
_FLOAT_EPS: Final = np.finfo(np.float64).eps
_BASIS_RELATIVE_ERROR_BUDGET: Final = 2.0e-10
_BASIS_ROUNDOFF_FACTOR: Final = 16.0


class TriFemPairError(ValueError):
    """Fail-closed identity, mesh, material, or operator error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "TRI_FEM_PAIR_INVALID"
        super().__init__(f"{self.code}: {message}")


def _required_id(value: object, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise TriFemPairError("IDENTITY_INVALID", f"{label} must be nonblank")
    return text


def _sha256_id(value: object, *, label: str) -> str:
    text = _required_id(value, label=label).casefold()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise TriFemPairError("IDENTITY_INVALID", f"{label} must be SHA-256")
    return text


def _positive(value: object, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TriFemPairError("MATERIAL_INVALID", f"{label} must be numeric") from exc
    if not isfinite(result) or result <= 0.0:
        raise TriFemPairError("MATERIAL_INVALID", f"{label} must be finite and > 0")
    return result


def _bound(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise TriFemPairError("WORK_BOUND_INVALID", f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TriFemPairError("WORK_BOUND_INVALID", f"{label} must be an integer") from exc
    if result != value or result < 1 or result > maximum:
        raise TriFemPairError("WORK_BOUND_INVALID", f"{label} must be in [1, {maximum}]")
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
        raise TriFemPairError("IDENTITY_INVALID", "manifest is not canonical JSON") from exc


def _plain_manifest(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_manifest(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_manifest(item) for item in value]
    return value


def _freeze_manifest(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_manifest(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_manifest(item) for item in value)
    return value


def _readonly_float(values: ArrayLike) -> NDArray[np.float64]:
    try:
        raw = np.asarray(values)
    except Exception as exc:
        raise TriFemPairError("MESH_INVALID", "mesh coordinates are not array-like") from exc
    if np.iscomplexobj(raw):
        raise TriFemPairError("MESH_INVALID", "mesh coordinates must be real, not complex")
    try:
        result = np.array(raw, dtype=np.float64, order="C", copy=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TriFemPairError("MESH_INVALID", "mesh coordinates must be real numbers") from exc
    result[result == 0.0] = 0.0
    result.setflags(write=False)
    return result


def _readonly_int(values: ArrayLike) -> NDArray[np.int64]:
    raw = np.asarray(values)
    checked: list[int] = []
    for value in raw.reshape(-1):
        scalar = value.item() if isinstance(value, np.generic) else value
        if isinstance(scalar, (bool, np.bool_, complex, np.complexfloating)):
            raise TriFemPairError("MESH_INVALID", "triangle indices must be exact integers")
        try:
            integer = int(scalar)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TriFemPairError(
                "MESH_INVALID",
                "triangle indices must be exact integers",
            ) from exc
        if scalar != integer:
            raise TriFemPairError("MESH_INVALID", "triangle indices must be exact integers")
        checked.append(integer)
    result = np.asarray(checked, dtype=np.int64).reshape(raw.shape)
    result.setflags(write=False)
    return result


def _readonly_real_csc(value: object) -> csc_matrix:
    try:
        source = csc_matrix(value, copy=True)
    except Exception as exc:
        raise TriFemPairError("STIFFNESS_INVALID", "stiffness is not sparse-compatible") from exc
    if np.iscomplexobj(source.data) and np.any(np.imag(source.data) != 0.0):
        raise TriFemPairError("STIFFNESS_INVALID", "stiffness must be real")
    result = csc_matrix(np.real(source), dtype=np.float64)
    result.sum_duplicates()
    result.sort_indices()
    result.eliminate_zeros()
    if not np.all(np.isfinite(result.data)):
        raise TriFemPairError("STIFFNESS_INVALID", "stiffness must be finite")
    result.data[result.data == 0.0] = 0.0
    result.data.setflags(write=False)
    result.indices.setflags(write=False)
    result.indptr.setflags(write=False)
    return result


def _csc_sha256(matrix: csc_matrix) -> str:
    canonical = _readonly_real_csc(matrix)
    digest = sha256(_canonical_json({"shape": canonical.shape}))
    digest.update(np.asarray(canonical.data, dtype="<f8").tobytes())
    digest.update(np.asarray(canonical.indices, dtype="<i8").tobytes())
    digest.update(np.asarray(canonical.indptr, dtype="<i8").tobytes())
    return digest.hexdigest()


def _assemble_stiffness(
    nodes: NDArray[np.float64], triangles: NDArray[np.int64]
) -> csc_matrix:
    rows: list[int] = []
    columns: list[int] = []
    values: list[float] = []
    scale = max(float(np.ptp(nodes[:, 0])), float(np.ptp(nodes[:, 1])))
    area_floor = np.finfo(np.float64).eps * scale * scale * 32.0
    for triangle_index, triangle in enumerate(triangles):
        points = nodes[triangle]
        transform = np.column_stack((points[1] - points[0], points[2] - points[0]))
        determinant = float(np.linalg.det(transform))
        area = 0.5 * abs(determinant)
        if not isfinite(area) or area <= area_floor:
            raise TriFemPairError("MESH_DEGENERATE", "triangle area is numerically zero")
        condition = float(np.linalg.cond(transform))
        if (
            not isfinite(condition)
            or condition <= 0.0
            or _BASIS_ROUNDOFF_FACTOR * _FLOAT_EPS * condition
            > _BASIS_RELATIVE_ERROR_BUDGET
        ):
            raise TriFemPairError(
                "MESH_ILL_CONDITIONED",
                f"triangle {triangle_index} condition {condition:.6g} exceeds the P1 error budget",
            )
        b = np.asarray((
            points[1, 1] - points[2, 1],
            points[2, 1] - points[0, 1],
            points[0, 1] - points[1, 1],
        ))
        c = np.asarray((
            points[2, 0] - points[1, 0],
            points[0, 0] - points[2, 0],
            points[1, 0] - points[0, 0],
        ))
        for local_index, node in enumerate(triangle):
            rows.extend((2 * triangle_index, 2 * triangle_index + 1))
            columns.extend((int(node), int(node)))
            factor = np.sqrt(area) / (2.0 * area)
            values.extend((float(factor * b[local_index]), float(factor * c[local_index])))
    gram = coo_matrix(
        (values, (rows, columns)),
        shape=(2 * len(triangles), len(nodes)),
    ).tocsc()
    return _readonly_real_csc(gram.T @ gram)


def _validate_stiffness(matrix: csc_matrix) -> None:
    if matrix.shape[0] != matrix.shape[1] or matrix.shape[0] < 3 or matrix.nnz == 0:
        raise TriFemPairError("STIFFNESS_INVALID", "stiffness must be nonempty and square")
    scale = max(1.0, float(np.max(np.abs(matrix.data), initial=0.0)))
    asymmetry = matrix - matrix.T
    if asymmetry.nnz and np.max(np.abs(asymmetry.data), initial=0.0) > 1.0e-12 * scale:
        raise TriFemPairError("STIFFNESS_NON_RECIPROCAL", "stiffness is not symmetric")
    row_sum = np.asarray(matrix.sum(axis=1)).ravel()
    if np.max(np.abs(row_sum), initial=0.0) > 5.0e-12 * scale:
        raise TriFemPairError("STIFFNESS_NOT_FLOATING", "stiffness row sums are not zero")
    graph = matrix.copy()
    graph.setdiag(0.0)
    graph.eliminate_zeros()
    graph.data = np.ones_like(graph.data)
    if connected_components(graph, directed=False, return_labels=False) != 1:
        raise TriFemPairError("STIFFNESS_DISCONNECTED", "mesh stiffness is disconnected")
    if len(row_sum) <= _PSD_DENSE_LIMIT:
        minimum = float(np.linalg.eigvalsh(matrix.toarray())[0])
        if minimum < -5.0e-10 * scale:
            raise TriFemPairError("STIFFNESS_NON_PASSIVE", "stiffness is indefinite")


def _mesh_manifest(
    *,
    nodes: NDArray[np.float64],
    triangles: NDArray[np.int64],
    stiffness: csc_matrix,
    source_mesh_identity_sha256: str,
    mesh_convergence_evidence_sha256: str | None,
    assembly_work: int,
    assembly_work_limit: int,
) -> Mapping[str, object]:
    node_digest = sha256(np.asarray(nodes, dtype="<f8").tobytes()).hexdigest()
    triangle_digest = sha256(np.asarray(triangles, dtype="<i8").tobytes()).hexdigest()
    return {
        "compiler_id": TRI_FEM_PAIR_COMPILER_ID,
        "node_count": len(nodes),
        "triangle_count": len(triangles),
        "node_xy_sha256": node_digest,
        "triangles_sha256": triangle_digest,
        "stiffness_sha256": _csc_sha256(stiffness),
        "source_mesh_identity_sha256": source_mesh_identity_sha256,
        "mesh_convergence_evidence_sha256": mesh_convergence_evidence_sha256,
        "assembly_work": assembly_work,
        "assembly_work_limit": assembly_work_limit,
    }


@dataclass(frozen=True, slots=True)
class MatchingP1MeshEvidence:
    """One canonical P1 mesh/stiffness view shared by both conductors."""

    node_xy_m: NDArray[np.float64]
    triangles: NDArray[np.int64]
    stiffness: csc_matrix
    source_mesh_identity_sha256: str
    mesh_convergence_evidence_sha256: str | None
    assembly_work: int
    assembly_work_limit: int
    mesh_identity_sha256: str
    identity_manifest: Mapping[str, object]

    def __post_init__(self) -> None:
        nodes = _readonly_float(self.node_xy_m)
        triangles = _readonly_int(self.triangles)
        stiffness = _readonly_real_csc(self.stiffness)
        source_identity = _sha256_id(
            self.source_mesh_identity_sha256,
            label="source_mesh_identity_sha256",
        )
        convergence = (
            None
            if self.mesh_convergence_evidence_sha256 is None
            else _sha256_id(
                self.mesh_convergence_evidence_sha256,
                label="mesh_convergence_evidence_sha256",
            )
        )
        work = _bound(self.assembly_work, label="assembly_work", maximum=_MAX_WORK)
        limit = _bound(self.assembly_work_limit, label="assembly_work_limit", maximum=_MAX_WORK)
        if work > limit:
            raise TriFemPairError("WORK_BOUND_EXCEEDED", "mesh assembly work exceeds its bound")
        if nodes.ndim != 2 or nodes.shape[1:] != (2,) or len(nodes) < 3:
            raise TriFemPairError("MESH_INVALID", "node_xy_m must have shape (N, 2)")
        if not np.all(np.isfinite(nodes)) or len({tuple(row) for row in nodes}) != len(nodes):
            raise TriFemPairError("MESH_INVALID", "mesh nodes must be finite and unique")
        if triangles.ndim != 2 or triangles.shape[1:] != (3,) or not len(triangles):
            raise TriFemPairError("MESH_INVALID", "triangles must have shape (M, 3)")
        if np.any(triangles < 0) or np.any(triangles >= len(nodes)):
            raise TriFemPairError("MESH_INVALID", "triangle index is out of range")
        if stiffness.shape != (len(nodes), len(nodes)):
            raise TriFemPairError("STIFFNESS_INVALID", "stiffness shape differs from mesh")
        expected = _assemble_stiffness(nodes, triangles)
        if _csc_sha256(stiffness) != _csc_sha256(expected):
            raise TriFemPairError(
                "STIFFNESS_IDENTITY_MISMATCH",
                "stiffness differs from the common P1 mesh Gram assembly",
            )
        _validate_stiffness(stiffness)
        manifest = _mesh_manifest(
            nodes=nodes,
            triangles=triangles,
            stiffness=stiffness,
            source_mesh_identity_sha256=source_identity,
            mesh_convergence_evidence_sha256=convergence,
            assembly_work=work,
            assembly_work_limit=limit,
        )
        if _canonical_json(_plain_manifest(self.identity_manifest)) != _canonical_json(manifest):
            raise TriFemPairError("MESH_MANIFEST_MISMATCH", "mesh manifest is stale")
        identity = sha256(_canonical_json(manifest)).hexdigest()
        if _sha256_id(self.mesh_identity_sha256, label="mesh_identity_sha256") != identity:
            raise TriFemPairError("MESH_IDENTITY_MISMATCH", "mesh identity is stale")
        object.__setattr__(self, "node_xy_m", nodes)
        object.__setattr__(self, "triangles", triangles)
        object.__setattr__(self, "stiffness", stiffness)
        object.__setattr__(self, "source_mesh_identity_sha256", source_identity)
        object.__setattr__(self, "mesh_convergence_evidence_sha256", convergence)
        object.__setattr__(self, "identity_manifest", _freeze_manifest(manifest))

    @property
    def production_eligible(self) -> bool:
        # A caller-provided digest is provenance only, not proof that any
        # bounded adjacent-mesh QoI comparison actually ran.  A future
        # convergence compiler must issue a typed, internally verified
        # attestation before this dormant core may become production eligible.
        return False

    def revalidate(self) -> None:
        """Reject stale mutable array/sparse buffers before a stamp is used."""

        manifest = _mesh_manifest(
            nodes=np.asarray(self.node_xy_m),
            triangles=np.asarray(self.triangles),
            stiffness=csc_matrix(self.stiffness, copy=False),
            source_mesh_identity_sha256=self.source_mesh_identity_sha256,
            mesh_convergence_evidence_sha256=self.mesh_convergence_evidence_sha256,
            assembly_work=self.assembly_work,
            assembly_work_limit=self.assembly_work_limit,
        )
        if _canonical_json(_plain_manifest(self.identity_manifest)) != _canonical_json(manifest):
            raise TriFemPairError(
                "MESH_MANIFEST_MISMATCH",
                "mesh buffers or provenance changed after compilation",
            )
        identity = sha256(_canonical_json(manifest)).hexdigest()
        if identity != self.mesh_identity_sha256:
            raise TriFemPairError("MESH_IDENTITY_MISMATCH", "mesh identity is stale")


def compile_matching_p1_mesh(
    node_xy_m: ArrayLike,
    triangles: ArrayLike,
    *,
    source_mesh_identity_sha256: str,
    mesh_convergence_evidence_sha256: str | None = None,
    max_nodes: int = _MAX_NODES,
    max_triangles: int = _MAX_TRIANGLES,
    max_assembly_work: int = _MAX_WORK,
) -> MatchingP1MeshEvidence:
    """Canonicalize one shared mesh and compile its exact scalar P1 stiffness."""

    node_bound = _bound(max_nodes, label="max_nodes", maximum=_MAX_NODES)
    triangle_bound = _bound(
        max_triangles,
        label="max_triangles",
        maximum=_MAX_TRIANGLES,
    )
    work_limit = _bound(max_assembly_work, label="max_assembly_work", maximum=_MAX_WORK)
    nodes = _readonly_float(node_xy_m)
    raw_triangles = _readonly_int(triangles)
    if nodes.ndim != 2 or nodes.shape[1:] != (2,) or len(nodes) < 3:
        raise TriFemPairError("MESH_INVALID", "node_xy_m must have shape (N, 2)")
    if len(nodes) > node_bound:
        raise TriFemPairError("MESH_BOUND_EXCEEDED", "node count exceeds its bound")
    if raw_triangles.ndim != 2 or raw_triangles.shape[1:] != (3,):
        raise TriFemPairError("MESH_INVALID", "triangles must have shape (M, 3)")
    if not len(raw_triangles) or len(raw_triangles) > triangle_bound:
        raise TriFemPairError("MESH_BOUND_EXCEEDED", "triangle count exceeds its bound")
    if not np.all(np.isfinite(nodes)) or len({tuple(row) for row in nodes}) != len(nodes):
        raise TriFemPairError("MESH_INVALID", "mesh nodes must be finite and unique")
    if np.any(raw_triangles < 0) or np.any(raw_triangles >= len(nodes)):
        raise TriFemPairError("MESH_INVALID", "triangle index is out of range")
    order = np.lexsort((nodes[:, 1], nodes[:, 0]))
    inverse = np.empty(len(nodes), dtype=np.int64)
    inverse[order] = np.arange(len(nodes), dtype=np.int64)
    canonical_nodes = _readonly_float(nodes[order])
    canonical_triangles = np.sort(inverse[raw_triangles], axis=1)
    rows = sorted({tuple(int(value) for value in row) for row in canonical_triangles})
    if len(rows) != len(raw_triangles):
        raise TriFemPairError("MESH_INVALID", "duplicate triangles are forbidden")
    canonical_triangles = _readonly_int(np.asarray(rows, dtype=np.int64))
    work = 18 * len(canonical_triangles)
    if work > work_limit:
        raise TriFemPairError("WORK_BOUND_EXCEEDED", "mesh assembly work exceeds its bound")
    stiffness = _assemble_stiffness(canonical_nodes, canonical_triangles)
    _validate_stiffness(stiffness)
    source_identity = _sha256_id(
        source_mesh_identity_sha256,
        label="source_mesh_identity_sha256",
    )
    convergence = (
        None
        if mesh_convergence_evidence_sha256 is None
        else _sha256_id(
            mesh_convergence_evidence_sha256,
            label="mesh_convergence_evidence_sha256",
        )
    )
    manifest = _mesh_manifest(
        nodes=canonical_nodes,
        triangles=canonical_triangles,
        stiffness=stiffness,
        source_mesh_identity_sha256=source_identity,
        mesh_convergence_evidence_sha256=convergence,
        assembly_work=work,
        assembly_work_limit=work_limit,
    )
    identity = sha256(_canonical_json(manifest)).hexdigest()
    return MatchingP1MeshEvidence(
        canonical_nodes,
        canonical_triangles,
        stiffness,
        source_identity,
        convergence,
        work,
        work_limit,
        identity,
        manifest,
    )


@dataclass(frozen=True, slots=True)
class PairConductor:
    layer_id: str
    conductivity_s_per_m: float
    thickness_m: float
    source_evidence_sha256: str
    permeability_h_per_m: float = MU_0_H_PER_M

    def __post_init__(self) -> None:
        object.__setattr__(self, "layer_id", _required_id(self.layer_id, label="layer_id"))
        object.__setattr__(
            self,
            "conductivity_s_per_m",
            _positive(self.conductivity_s_per_m, label="conductivity_s_per_m"),
        )
        object.__setattr__(
            self,
            "thickness_m",
            _positive(self.thickness_m, label="thickness_m"),
        )
        object.__setattr__(
            self,
            "permeability_h_per_m",
            _positive(self.permeability_h_per_m, label="permeability_h_per_m"),
        )
        object.__setattr__(
            self,
            "source_evidence_sha256",
            _sha256_id(self.source_evidence_sha256, label="conductor source evidence"),
        )


@dataclass(frozen=True, slots=True)
class PairGap:
    gap_id: str
    upper_layer_id: str
    lower_layer_id: str
    separation_m: float
    source_evidence_sha256: str
    permeability_h_per_m: float = MU_0_H_PER_M

    def __post_init__(self) -> None:
        gap_id = _required_id(self.gap_id, label="gap_id")
        upper = _required_id(self.upper_layer_id, label="upper_layer_id")
        lower = _required_id(self.lower_layer_id, label="lower_layer_id")
        if upper.casefold() == lower.casefold():
            raise TriFemPairError("LAYER_PAIR_INVALID", "gap layers must be distinct")
        object.__setattr__(self, "gap_id", gap_id)
        object.__setattr__(self, "upper_layer_id", upper)
        object.__setattr__(self, "lower_layer_id", lower)
        object.__setattr__(
            self,
            "separation_m",
            _positive(self.separation_m, label="separation_m"),
        )
        object.__setattr__(
            self,
            "permeability_h_per_m",
            _positive(self.permeability_h_per_m, label="gap permeability_h_per_m"),
        )
        object.__setattr__(
            self,
            "source_evidence_sha256",
            _sha256_id(self.source_evidence_sha256, label="gap source evidence"),
        )


@dataclass(frozen=True, slots=True)
class TriFemPairSourceEvidence:
    source_model_identity_sha256: str
    material_manifest_sha256: str
    upper_conductor_sha256: str
    lower_conductor_sha256: str
    gap_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "source_model_identity_sha256",
            "material_manifest_sha256",
            "upper_conductor_sha256",
            "lower_conductor_sha256",
            "gap_sha256",
        ):
            object.__setattr__(self, name, _sha256_id(getattr(self, name), label=name))


def _material_manifest(material: PairConductor) -> Mapping[str, object]:
    return {
        "layer_id": material.layer_id,
        "conductivity_s_per_m": material.conductivity_s_per_m,
        "thickness_m": material.thickness_m,
        "permeability_h_per_m": material.permeability_h_per_m,
        "source_evidence_sha256": material.source_evidence_sha256,
    }


def pair_material_manifest_sha256(
    upper: PairConductor,
    lower: PairConductor,
    gap: PairGap,
) -> str:
    """Hash the exact material values consumed by the paired operator."""

    if not isinstance(upper, PairConductor) or not isinstance(lower, PairConductor):
        raise TriFemPairError("MATERIAL_INVALID", "two typed conductors are required")
    if not isinstance(gap, PairGap):
        raise TriFemPairError("MATERIAL_INVALID", "one typed gap is required")
    payload = {
        "upper": _material_manifest(upper),
        "lower": _material_manifest(lower),
        "gap": {
            "gap_id": gap.gap_id,
            "upper_layer_id": gap.upper_layer_id,
            "lower_layer_id": gap.lower_layer_id,
            "separation_m": gap.separation_m,
            "permeability_h_per_m": gap.permeability_h_per_m,
            "source_evidence_sha256": gap.source_evidence_sha256,
        },
    }
    return sha256(_canonical_json(payload)).hexdigest()


def _operator_manifest(
    *,
    mesh: MatchingP1MeshEvidence,
    upper: PairConductor,
    lower: PairConductor,
    gap: PairGap,
    evidence: TriFemPairSourceEvidence,
    stamp_work: int,
    stamp_work_limit: int,
) -> Mapping[str, object]:
    return {
        "compiler_id": TRI_FEM_PAIR_COMPILER_ID,
        "stamp_semantics": TRI_FEM_PAIR_STAMP_SEMANTICS,
        "parallel_independent_overlap_stamps_forbidden": True,
        "active_gap_count": 1,
        "mesh_identity_sha256": mesh.mesh_identity_sha256,
        "upper": _material_manifest(upper),
        "lower": _material_manifest(lower),
        "gap": {
            "gap_id": gap.gap_id,
            "upper_layer_id": gap.upper_layer_id,
            "lower_layer_id": gap.lower_layer_id,
            "separation_m": gap.separation_m,
            "permeability_h_per_m": gap.permeability_h_per_m,
            "source_evidence_sha256": gap.source_evidence_sha256,
        },
        "source_evidence": {
            "source_model_identity_sha256": evidence.source_model_identity_sha256,
            "material_manifest_sha256": evidence.material_manifest_sha256,
            "upper_conductor_sha256": evidence.upper_conductor_sha256,
            "lower_conductor_sha256": evidence.lower_conductor_sha256,
            "gap_sha256": evidence.gap_sha256,
        },
        "stamp_work": stamp_work,
        "stamp_work_limit": stamp_work_limit,
        "production_eligible": mesh.production_eligible,
    }


@dataclass(frozen=True, slots=True)
class TriFemPairEvaluation:
    frequency_hz: float
    impedance_ohm_per_square: NDArray[np.complex128]
    admittance_s_per_square: NDArray[np.complex128]
    nodal_admittance_s: csc_matrix
    impedance_condition_number: float
    impedance_passivity_minimum: float
    admittance_passivity_minimum: float
    gauge_relative_residual: float


@dataclass(frozen=True, slots=True)
class TriFemPairOperator:
    """Single-gap paired-layer operator on a matching common P1 mesh."""

    mesh: MatchingP1MeshEvidence
    upper: PairConductor
    lower: PairConductor
    gap: PairGap
    source_evidence: TriFemPairSourceEvidence
    stamp_work: int
    stamp_work_limit: int
    identity_sha256: str
    identity_manifest: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.mesh, MatchingP1MeshEvidence):
            raise TriFemPairError("MESH_INVALID", "mesh evidence has the wrong type")
        self.mesh.revalidate()
        if not isinstance(self.upper, PairConductor) or not isinstance(self.lower, PairConductor):
            raise TriFemPairError("MATERIAL_INVALID", "two typed conductors are required")
        if not isinstance(self.gap, PairGap):
            raise TriFemPairError("MATERIAL_INVALID", "one typed gap is required")
        if not isinstance(self.source_evidence, TriFemPairSourceEvidence):
            raise TriFemPairError("IDENTITY_INVALID", "typed source evidence is required")
        if self.upper.layer_id.casefold() == self.lower.layer_id.casefold():
            raise TriFemPairError("LAYER_PAIR_INVALID", "conductor layers must be distinct")
        expected_layers = (
            self.upper.layer_id.casefold(),
            self.lower.layer_id.casefold(),
        )
        actual_layers = (
            self.gap.upper_layer_id.casefold(),
            self.gap.lower_layer_id.casefold(),
        )
        if actual_layers != expected_layers:
            raise TriFemPairError("LAYER_PAIR_MISMATCH", "gap orientation differs from conductors")
        evidence = self.source_evidence
        if (
            evidence.upper_conductor_sha256 != self.upper.source_evidence_sha256
            or evidence.lower_conductor_sha256 != self.lower.source_evidence_sha256
            or evidence.gap_sha256 != self.gap.source_evidence_sha256
        ):
            raise TriFemPairError("SOURCE_EVIDENCE_MISMATCH", "material source hashes disagree")
        expected_material_manifest = pair_material_manifest_sha256(
            self.upper,
            self.lower,
            self.gap,
        )
        if evidence.material_manifest_sha256 != expected_material_manifest:
            raise TriFemPairError(
                "SOURCE_EVIDENCE_MISMATCH",
                "material manifest hash does not bind the consumed material values",
            )
        work = _bound(self.stamp_work, label="stamp_work", maximum=_MAX_WORK)
        limit = _bound(self.stamp_work_limit, label="stamp_work_limit", maximum=_MAX_WORK)
        if work > limit or work != 4 * self.mesh.stiffness.nnz:
            raise TriFemPairError("WORK_BOUND_EXCEEDED", "paired stamp work is invalid")
        manifest = _operator_manifest(
            mesh=self.mesh,
            upper=self.upper,
            lower=self.lower,
            gap=self.gap,
            evidence=evidence,
            stamp_work=work,
            stamp_work_limit=limit,
        )
        if _canonical_json(_plain_manifest(self.identity_manifest)) != _canonical_json(manifest):
            raise TriFemPairError("OPERATOR_MANIFEST_MISMATCH", "operator manifest is stale")
        identity = sha256(_canonical_json(manifest)).hexdigest()
        if _sha256_id(self.identity_sha256, label="identity_sha256") != identity:
            raise TriFemPairError("OPERATOR_IDENTITY_MISMATCH", "operator identity is stale")
        object.__setattr__(self, "identity_manifest", _freeze_manifest(manifest))

    @property
    def production_eligible(self) -> bool:
        return self.mesh.production_eligible

    def require_production_eligible(self) -> None:
        if not self.production_eligible:
            raise TriFemPairError(
                "MESH_CONVERGENCE_UNATTESTED",
                "paired operator needs an internally issued convergence attestation",
            )

    def revalidate(self) -> None:
        """Recheck buffer, material, provenance, and identity bindings."""

        self.mesh.revalidate()
        if (
            self.source_evidence.upper_conductor_sha256
            != self.upper.source_evidence_sha256
            or self.source_evidence.lower_conductor_sha256
            != self.lower.source_evidence_sha256
            or self.source_evidence.gap_sha256 != self.gap.source_evidence_sha256
            or self.source_evidence.material_manifest_sha256
            != pair_material_manifest_sha256(self.upper, self.lower, self.gap)
        ):
            raise TriFemPairError("SOURCE_EVIDENCE_MISMATCH", "material source hashes disagree")
        manifest = _operator_manifest(
            mesh=self.mesh,
            upper=self.upper,
            lower=self.lower,
            gap=self.gap,
            evidence=self.source_evidence,
            stamp_work=self.stamp_work,
            stamp_work_limit=self.stamp_work_limit,
        )
        if _canonical_json(_plain_manifest(self.identity_manifest)) != _canonical_json(manifest):
            raise TriFemPairError(
                "OPERATOR_MANIFEST_MISMATCH",
                "operator inputs or manifest changed after compilation",
            )
        if sha256(_canonical_json(manifest)).hexdigest() != self.identity_sha256:
            raise TriFemPairError("OPERATOR_IDENTITY_MISMATCH", "operator identity is stale")

    def assert_stamp_policy(self, *, independent_overlap_sheet_stamps_active: bool) -> None:
        if independent_overlap_sheet_stamps_active:
            raise TriFemPairError(
                "PARALLEL_SHEET_STAMP_FORBIDDEN",
                "paired stamp must replace independent overlap sheet stamps",
            )

    def impedance_matrix_ohm_per_square(
        self, frequency_hz: float
    ) -> NDArray[np.complex128]:
        self.revalidate()
        try:
            frequency = float(frequency_hz)
        except (TypeError, ValueError) as exc:
            raise TriFemPairError("FREQUENCY_INVALID", "frequency_hz must be numeric") from exc
        if not isfinite(frequency) or frequency < 0.0:
            raise TriFemPairError("FREQUENCY_INVALID", "frequency_hz must be finite and >= 0")
        upper_face = copper_two_face_surface_impedance(
            frequency,
            self.upper.conductivity_s_per_m,
            self.upper.thickness_m,
            self.upper.permeability_h_per_m,
        )
        lower_face = copper_two_face_surface_impedance(
            frequency,
            self.lower.conductivity_s_per_m,
            self.lower.thickness_m,
            self.lower.permeability_h_per_m,
        )
        upper_self = complex(upper_face[0, 0])
        lower_self = complex(lower_face[0, 0])
        upper_common = 0.5 * complex(upper_face[0, 0] + upper_face[0, 1])
        lower_common = 0.5 * complex(lower_face[0, 0] + lower_face[0, 1])
        upper_delta = upper_self - upper_common
        lower_delta = lower_self - lower_common
        omega = 2.0 * pi * frequency
        gap_inductance = self.gap.permeability_h_per_m * self.gap.separation_m
        differential = (
            upper_delta + lower_delta + 1j * omega * gap_inductance
        ) / 4.0
        result = np.diag((upper_common, lower_common)).astype(np.complex128)
        result += differential * np.asarray(((1.0, -1.0), (-1.0, 1.0)))
        _validate_positive_real(result, label="paired impedance")
        result.setflags(write=False)
        return result

    def evaluate(self, frequency_hz: float) -> TriFemPairEvaluation:
        impedance = self.impedance_matrix_ohm_per_square(frequency_hz)
        condition = float(np.linalg.cond(impedance))
        if not isfinite(condition) or condition > 1.0e12:
            raise TriFemPairError(
                "IMPEDANCE_ILL_CONDITIONED",
                f"paired impedance condition {condition:.3e} exceeds 1e12",
            )
        try:
            admittance = np.linalg.inv(impedance)
        except np.linalg.LinAlgError as exc:
            raise TriFemPairError("IMPEDANCE_SINGULAR", "paired impedance is singular") from exc
        z_min = _validate_positive_real(impedance, label="paired impedance")
        y_min = _validate_positive_real(admittance, label="paired inverse admittance")
        nodal = csc_matrix(kron(admittance, self.mesh.stiffness, format="csc"))
        nodal.sum_duplicates()
        nodal.eliminate_zeros()
        if not np.all(np.isfinite(nodal.data)):
            raise TriFemPairError("STAMP_INVALID", "paired nodal stamp is non-finite")
        reciprocity = nodal - nodal.T
        scale = max(1.0, float(np.max(np.abs(nodal.data), initial=0.0)))
        if reciprocity.nnz and np.max(np.abs(reciprocity.data), initial=0.0) > 1e-11 * scale:
            raise TriFemPairError("STAMP_NON_RECIPROCAL", "paired nodal stamp is not symmetric")
        count = len(self.mesh.node_xy_m)
        gauges = np.zeros((2 * count, 2), dtype=np.complex128)
        gauges[:count, 0] = 1.0
        gauges[count:, 1] = 1.0
        residual = np.asarray(nodal @ gauges)
        relative = float(np.max(np.abs(residual), initial=0.0) / scale)
        if relative > 2.0e-11:
            raise TriFemPairError("STAMP_GAUGE_INVALID", "layer gauge residual is too large")
        nodal.data.setflags(write=False)
        nodal.indices.setflags(write=False)
        nodal.indptr.setflags(write=False)
        admittance.setflags(write=False)
        return TriFemPairEvaluation(
            float(frequency_hz),
            impedance,
            admittance,
            nodal,
            condition,
            z_min,
            y_min,
            relative,
        )

    def nodal_admittance_s(self, frequency_hz: float) -> csc_matrix:
        return self.evaluate(frequency_hz).nodal_admittance_s


def _validate_positive_real(matrix: NDArray[np.complex128], *, label: str) -> float:
    if matrix.shape != (2, 2) or not np.all(np.isfinite(matrix)):
        raise TriFemPairError("PAIR_MATRIX_INVALID", f"{label} must be finite 2x2")
    scale = max(1.0, float(np.max(np.abs(matrix), initial=0.0)))
    if np.max(np.abs(matrix - matrix.T), initial=0.0) > 2.0e-12 * scale:
        raise TriFemPairError("PAIR_MATRIX_NON_RECIPROCAL", f"{label} is not symmetric")
    hermitian = 0.5 * (matrix + matrix.conj().T)
    minimum = float(np.linalg.eigvalsh(hermitian)[0])
    tolerance = 5.0e-12 * scale
    if not isfinite(minimum) or minimum < -tolerance:
        raise TriFemPairError("PAIR_MATRIX_NON_PASSIVE", f"{label} is not positive real")
    return minimum


def compile_tri_fem_pair(
    mesh: MatchingP1MeshEvidence,
    upper: PairConductor,
    lower: PairConductor,
    gap: PairGap,
    *,
    source_evidence: TriFemPairSourceEvidence,
    participating_gap_ids: Sequence[str],
    independent_overlap_sheet_stamps_active: bool = False,
    max_stamp_work: int = _MAX_WORK,
) -> TriFemPairOperator:
    """Compile one replacement paired-sheet stamp; two-gap participation fails closed."""

    if not isinstance(mesh, MatchingP1MeshEvidence):
        raise TriFemPairError("MESH_INVALID", "typed matching mesh evidence is required")
    gap_ids = tuple(
        _required_id(value, label="participating gap id")
        for value in participating_gap_ids
    )
    if len(gap_ids) != 1 or gap_ids[0].casefold() != gap.gap_id.casefold():
        raise TriFemPairError(
            "ACTIVE_GAP_COUNT_UNSUPPORTED",
            "MVP requires exactly its one source-owned active gap",
        )
    if independent_overlap_sheet_stamps_active:
        raise TriFemPairError(
            "PARALLEL_SHEET_STAMP_FORBIDDEN",
            "paired operator replaces independent overlap sheet stamps",
        )
    limit = _bound(max_stamp_work, label="max_stamp_work", maximum=_MAX_WORK)
    work = 4 * mesh.stiffness.nnz
    if work > limit:
        raise TriFemPairError("WORK_BOUND_EXCEEDED", "paired stamp work exceeds its bound")
    provisional = {
        "mesh": mesh,
        "upper": upper,
        "lower": lower,
        "gap": gap,
        "evidence": source_evidence,
        "work": work,
        "limit": limit,
    }
    manifest = _operator_manifest(
        mesh=provisional["mesh"],
        upper=provisional["upper"],
        lower=provisional["lower"],
        gap=provisional["gap"],
        evidence=provisional["evidence"],
        stamp_work=provisional["work"],
        stamp_work_limit=provisional["limit"],
    )
    identity = sha256(_canonical_json(manifest)).hexdigest()
    operator = TriFemPairOperator(
        mesh,
        upper,
        lower,
        gap,
        source_evidence,
        work,
        limit,
        identity,
        manifest,
    )
    operator.impedance_matrix_ohm_per_square(0.0)
    return operator


__all__ = [
    "MatchingP1MeshEvidence",
    "PairConductor",
    "PairGap",
    "TRI_FEM_PAIR_COMPILER_ID",
    "TRI_FEM_PAIR_STAMP_SEMANTICS",
    "TriFemPairError",
    "TriFemPairEvaluation",
    "TriFemPairOperator",
    "TriFemPairSourceEvidence",
    "compile_matching_p1_mesh",
    "compile_tri_fem_pair",
    "pair_material_manifest_sha256",
]
