"""Fail-closed mounted-termination stamps for a layer-surface network.

The layer-surface solver owns board artwork and source-observed inter-layer
connectivity.  This module supplies a deliberately separate ownership boundary
for *internal* mounted loads (decaps today, other passive two-terminal loads in
the future).  It does not terminate measurement ports: an external multiport
therefore remains open unless a source-derived mounted cluster is explicitly
present in this manifest.

Each physical top-pad cluster is represented as a small two-terminal circuit.
Its R/L/C elements may contain internal nodes, so shared Via paths are stamped
once and Kron-reduced locally before one scalar branch is embedded in the
global layer-surface Y matrix.  Every mounted physical cluster, including those
owned by the selected rail, is stamped before the global reduction.  External
Device measurement ports remain open incidence vectors; they are not physical
loads and are never terminated by rail selection.

No Touchstone data, fitted parameter, rail-population approximation, or
implicit reference impedance is accepted here.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from ..models.impedance import ImpedanceModel, frequency_array


TERMINATION_MANIFEST_SCHEMA = "spd-layer-surface-terminations-v1"
TERMINATION_COMPILER_ID = "cluster-two-terminal-local-kron-v1"
_TINY = np.finfo(np.float64).tiny
_SYMMETRY_REL_LIMIT = 1.0e-10
_ROW_SUM_REL_LIMIT = 1.0e-10
_PASSIVITY_REL_LIMIT = 1.0e-10
_KRON_RESIDUAL_REL_LIMIT = 1.0e-10
_MODEL_IDENTITY_CACHE: ContextVar[dict[int, tuple[object, str]] | None] = (
    ContextVar("layer_surface_termination_model_identity_cache", default=None)
)


class LayerSurfaceTerminationError(ValueError):
    """Actionable source/ownership/numerical failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "TERMINATION_INVALID"
        super().__init__(f"Layer-surface termination [{self.code}]: {message}")


def _identity(value: Any, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise LayerSurfaceTerminationError(
            "IDENTITY_MISSING", f"{name} must not be blank"
        )
    return text


def _key(value: Any, *, name: str = "identity") -> str:
    return _identity(value, name=name).casefold()


def _canonical_json(payload: Any) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LayerSurfaceTerminationError(
            "EVIDENCE_NOT_CANONICAL", f"termination evidence is not canonical JSON: {exc}"
        ) from exc
    return (encoded + "\n").encode("utf-8")


def _evidence_value(value: Any, *, path: str) -> Any:
    """Return a deterministic JSON value or fail on opaque model state."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Enum):
        return _evidence_value(value.value, path=path)
    if isinstance(value, np.generic):
        return _evidence_value(value.item(), path=path)
    if isinstance(value, float):
        if not isfinite(value):
            raise LayerSurfaceTerminationError(
                "MODEL_EVIDENCE_INVALID", f"{path} contains a non-finite float"
            )
        return value
    if isinstance(value, complex):
        if not isfinite(value.real) or not isfinite(value.imag):
            raise LayerSurfaceTerminationError(
                "MODEL_EVIDENCE_INVALID", f"{path} contains a non-finite complex value"
            )
        return {"real": float(value.real), "imag": float(value.imag)}
    if isinstance(value, np.ndarray):
        if not np.all(np.isfinite(value)):
            raise LayerSurfaceTerminationError(
                "MODEL_EVIDENCE_INVALID", f"{path} contains a non-finite array"
            )
        return {
            "dtype": str(value.dtype),
            "shape": list(value.shape),
            "values": _evidence_value(value.tolist(), path=f"{path}.values"),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": {
                item.name: _evidence_value(
                    getattr(value, item.name), path=f"{path}.{item.name}"
                )
                for item in fields(value)
            },
        }
    if isinstance(value, Mapping):
        pairs: list[tuple[str, Any]] = []
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            pairs.append(
                (key, _evidence_value(raw_value, path=f"{path}[{key!r}]"))
            )
        if len({key for key, _item in pairs}) != len(pairs):
            raise LayerSurfaceTerminationError(
                "MODEL_EVIDENCE_INVALID",
                f"{path} contains mapping keys that collide after string conversion",
            )
        return {key: item for key, item in sorted(pairs)}
    if isinstance(value, (tuple, list)):
        return [
            _evidence_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise LayerSurfaceTerminationError(
        "MODEL_EVIDENCE_OPAQUE",
        f"{path} uses unsupported opaque type {type(value).__name__!r}",
    )


def impedance_model_identity_sha256(model: ImpedanceModel) -> str:
    """Bind a supported immutable impedance model to exact parameter state."""

    cache = _MODEL_IDENTITY_CACHE.get()
    key = id(model)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None and cached[0] is model:
            return cached[1]
    evidence = _evidence_value(model, path="model")
    digest = sha256(_canonical_json(evidence)).hexdigest()
    if cache is not None:
        # Keep a strong reference for this bounded compile scope so CPython
        # cannot recycle an object ID into an unrelated model.
        cache[key] = (model, digest)
    return digest


@contextmanager
def scoped_impedance_model_identity_cache():
    """Reuse exact model evidence only within one synchronous compile.

    No process-global result is retained: a later call re-reads the complete
    model state, while tens of thousands of branches that deliberately share
    one immutable runtime model avoid repeating the same JSON walk and SHA.
    ContextVar isolation keeps concurrent Evaluation workers independent.
    """

    existing = _MODEL_IDENTITY_CACHE.get()
    if existing is not None:
        yield
        return
    token = _MODEL_IDENTITY_CACHE.set({})
    try:
        yield
    finally:
        _MODEL_IDENTITY_CACHE.reset(token)


@dataclass(frozen=True, slots=True)
class LayerSurfaceTerminationBranch:
    """One explicitly owned two-node element inside a physical cluster."""

    branch_id: str
    first_node_id: str
    second_node_id: str
    model: ImpedanceModel = field(repr=False, compare=False)
    owner_ids: tuple[str, ...] = ()
    model_identity_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        branch_id = _identity(self.branch_id, name="termination branch ID")
        first = _identity(self.first_node_id, name="branch first node")
        second = _identity(self.second_node_id, name="branch second node")
        if _key(first) == _key(second):
            raise LayerSurfaceTerminationError(
                "BRANCH_SELF_LOOP", f"branch {branch_id!r} joins one local node to itself"
            )
        if not isinstance(self.model, ImpedanceModel):
            raise LayerSurfaceTerminationError(
                "BRANCH_MODEL_INVALID",
                f"branch {branch_id!r} does not provide the impedance-model contract",
            )
        raw_owners = tuple(
            _identity(item, name="physical owner ID") for item in self.owner_ids
        )
        owners = tuple(
            sorted(raw_owners, key=lambda item: (item.casefold(), item))
        )
        if not owners:
            raise LayerSurfaceTerminationError(
                "PHYSICAL_OWNER_MISSING",
                f"branch {branch_id!r} has no explicit physical owner",
            )
        if len({_key(item) for item in owners}) != len(owners):
            raise LayerSurfaceTerminationError(
                "PHYSICAL_OWNER_DUPLICATED",
                f"branch {branch_id!r} repeats a case-insensitive owner ID",
            )
        object.__setattr__(self, "branch_id", branch_id)
        object.__setattr__(self, "first_node_id", first)
        object.__setattr__(self, "second_node_id", second)
        object.__setattr__(self, "owner_ids", owners)
        object.__setattr__(
            self, "model_identity_sha256", impedance_model_identity_sha256(self.model)
        )

    def evidence_manifest(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "branch_id": _key(self.branch_id),
                "first_node_id": _key(self.first_node_id),
                "second_node_id": _key(self.second_node_id),
                "owner_ids": sorted(_key(item) for item in self.owner_ids),
                "model_identity_sha256": self.model_identity_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class LayerSurfaceTerminationCluster:
    """One physical two-terminal top-pad/load cluster.

    ``rail_owner_ids`` deliberately permits the source compiler to represent
    an unresolved cross-rail observation.  Compilation below accepts exactly
    one rail and fails closed for zero or multiple owners; it never chooses an
    owner by ordering or majority.
    """

    cluster_id: str
    positive_surface_node_id: str
    negative_surface_node_id: str
    positive_terminal_node_id: str
    negative_terminal_node_id: str
    branches: tuple[LayerSurfaceTerminationBranch, ...]
    rail_owner_ids: tuple[str, ...]
    ownership_status: str = "complete"
    topology_owner_ids: tuple[str, ...] = ()
    source_classification: str = "SOURCE_PROVEN"
    source_reason: str | None = None
    source_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        cluster_id = _identity(self.cluster_id, name="termination cluster ID")
        positive_surface = _identity(
            self.positive_surface_node_id, name="positive surface node"
        )
        negative_surface = _identity(
            self.negative_surface_node_id, name="negative surface node"
        )
        positive_terminal = _identity(
            self.positive_terminal_node_id, name="positive local terminal"
        )
        negative_terminal = _identity(
            self.negative_terminal_node_id, name="negative local terminal"
        )
        if _key(positive_surface) == _key(negative_surface):
            raise LayerSurfaceTerminationError(
                "SURFACE_TERMINALS_SHORTED",
                f"cluster {cluster_id!r} has identical global surface terminals",
            )
        if _key(positive_terminal) == _key(negative_terminal):
            raise LayerSurfaceTerminationError(
                "LOCAL_TERMINALS_SHORTED",
                f"cluster {cluster_id!r} has identical local terminals",
            )
        branches = tuple(self.branches)
        if not branches:
            raise LayerSurfaceTerminationError(
                "CLUSTER_EMPTY", f"cluster {cluster_id!r} has no circuit elements"
            )
        if len({_key(item.branch_id) for item in branches}) != len(branches):
            raise LayerSurfaceTerminationError(
                "BRANCH_ID_DUPLICATED",
                f"cluster {cluster_id!r} has duplicate branch IDs",
            )
        raw_rails = tuple(
            _identity(item, name="rail owner ID") for item in self.rail_owner_ids
        )
        rails = tuple(
            sorted(raw_rails, key=lambda item: (item.casefold(), item))
        )
        if len({_key(item) for item in rails}) != len(rails):
            raise LayerSurfaceTerminationError(
                "RAIL_OWNER_DUPLICATED",
                f"cluster {cluster_id!r} repeats a case-insensitive rail owner ID",
            )
        status = _identity(self.ownership_status, name="ownership status").casefold()
        topology_owners = tuple(
            sorted(
                (
                    _identity(item, name="topology owner ID")
                    for item in self.topology_owner_ids
                ),
                key=lambda item: (item.casefold(), item),
            )
        )
        if len({_key(item) for item in topology_owners}) != len(topology_owners):
            raise LayerSurfaceTerminationError(
                "PHYSICAL_OWNER_DUPLICATED",
                f"cluster {cluster_id!r} repeats a case-insensitive topology owner ID",
            )
        classification = _identity(
            self.source_classification, name="source classification"
        )
        reason = None if self.source_reason is None else str(self.source_reason).strip()
        if self.source_reason is not None and not reason:
            raise LayerSurfaceTerminationError(
                "SOURCE_REASON_INVALID",
                f"cluster {cluster_id!r} has a blank source reason",
            )
        evidence = self.source_evidence_sha256
        if evidence is not None:
            evidence = str(evidence).strip().casefold()
            if len(evidence) != 64 or any(
                character not in "0123456789abcdef" for character in evidence
            ):
                raise LayerSurfaceTerminationError(
                    "SOURCE_EVIDENCE_INVALID",
                    f"cluster {cluster_id!r} source evidence must be SHA-256",
                )
        object.__setattr__(self, "cluster_id", cluster_id)
        object.__setattr__(self, "positive_surface_node_id", positive_surface)
        object.__setattr__(self, "negative_surface_node_id", negative_surface)
        object.__setattr__(self, "positive_terminal_node_id", positive_terminal)
        object.__setattr__(self, "negative_terminal_node_id", negative_terminal)
        object.__setattr__(self, "branches", branches)
        object.__setattr__(self, "rail_owner_ids", rails)
        object.__setattr__(self, "ownership_status", status)
        object.__setattr__(self, "topology_owner_ids", topology_owners)
        object.__setattr__(self, "source_classification", classification)
        object.__setattr__(self, "source_reason", reason)
        object.__setattr__(self, "source_evidence_sha256", evidence)

    def evidence_manifest(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "cluster_id": _key(self.cluster_id),
                "positive_surface_node_id": _key(self.positive_surface_node_id),
                "negative_surface_node_id": _key(self.negative_surface_node_id),
                "positive_terminal_node_id": _key(self.positive_terminal_node_id),
                "negative_terminal_node_id": _key(self.negative_terminal_node_id),
                "rail_owner_ids": sorted(_key(item) for item in self.rail_owner_ids),
                "ownership_status": self.ownership_status,
                "topology_owner_ids": sorted(
                    _key(item) for item in self.topology_owner_ids
                ),
                "source_classification": self.source_classification,
                "source_reason": self.source_reason,
                "source_evidence_sha256": self.source_evidence_sha256,
                "branches": [
                    dict(item.evidence_manifest())
                    for item in sorted(
                        self.branches,
                        key=lambda branch: (
                            branch.branch_id.casefold(),
                            branch.branch_id,
                        ),
                    )
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class EvaluatedLayerSurfaceTerminationStamp:
    cluster_id: str
    rail_id: str
    positive_node_index: int
    negative_node_index: int
    admittance_s: NDArray[np.complex128]
    owner_ids: tuple[str, ...]
    maximum_local_kron_relative_residual: float

    def __post_init__(self) -> None:
        values = np.asarray(self.admittance_s, dtype=np.complex128).copy()
        if values.ndim != 1 or not values.size or not np.all(np.isfinite(values)):
            raise LayerSurfaceTerminationError(
                "STAMP_INVALID", f"cluster {self.cluster_id!r} produced an invalid stamp"
            )
        values.setflags(write=False)
        object.__setattr__(self, "admittance_s", values)
        residual = float(self.maximum_local_kron_relative_residual)
        if not isfinite(residual) or residual < 0.0:
            raise LayerSurfaceTerminationError(
                "STAMP_DIAGNOSTIC_INVALID",
                f"cluster {self.cluster_id!r} has an invalid Kron residual",
            )
        object.__setattr__(self, "maximum_local_kron_relative_residual", residual)

    def add_to(self, matrix: NDArray[np.complex128], frequency_index: int) -> None:
        """Add this passive two-terminal branch to one dense global Y matrix."""

        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
            raise LayerSurfaceTerminationError(
                "GLOBAL_MATRIX_INVALID", "global admittance matrix must be square"
            )
        p, n = self.positive_node_index, self.negative_node_index
        if p < 0 or n < 0 or p >= matrix.shape[0] or n >= matrix.shape[0]:
            raise LayerSurfaceTerminationError(
                "GLOBAL_NODE_INDEX_INVALID", "termination stamp lies outside global Y"
            )
        try:
            admittance = complex(self.admittance_s[int(frequency_index)])
        except (IndexError, TypeError, ValueError) as exc:
            raise LayerSurfaceTerminationError(
                "FREQUENCY_INDEX_INVALID", "termination frequency index is invalid"
            ) from exc
        matrix[p, p] += admittance
        matrix[n, n] += admittance
        matrix[p, n] -= admittance
        matrix[n, p] -= admittance


@dataclass(frozen=True, slots=True)
class TerminationStampDiagnostics:
    physical_cluster_count: int
    # Compatibility names retained for serialized/provenance consumers.  The
    # production layerwise solve now stamps every physical cluster, including
    # clusters owned by the selected rail, before global Kron reduction.
    active_other_rail_cluster_count: int
    excluded_selected_rail_cluster_count: int
    active_element_count: int
    maximum_local_kron_relative_residual: float
    external_port_termination_count: int = 0


@dataclass(frozen=True, slots=True)
class EvaluatedLayerSurfaceTerminations:
    frequencies_hz: NDArray[np.float64]
    selected_rail_id: str
    stamps: tuple[EvaluatedLayerSurfaceTerminationStamp, ...]
    diagnostics: TerminationStampDiagnostics
    cache_identity_sha256: str

    def __post_init__(self) -> None:
        frequencies = frequency_array(self.frequencies_hz).copy()
        frequencies.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)

    def dense_matrix(self, frequency_index: int, node_count: int) -> NDArray[np.complex128]:
        """Materialize a test/debug global stamp; production may add records sparsely."""

        if not isinstance(node_count, int) or node_count < 1:
            raise LayerSurfaceTerminationError(
                "GLOBAL_NODE_COUNT_INVALID", "global node count must be positive"
            )
        matrix = np.zeros((node_count, node_count), dtype=np.complex128)
        for stamp in self.stamps:
            stamp.add_to(matrix, frequency_index)
        return matrix


@dataclass(frozen=True, slots=True)
class _CompiledCluster:
    source: LayerSurfaceTerminationCluster
    rail_id: str
    positive_global_index: int
    negative_global_index: int
    local_node_ids: tuple[str, ...]
    positive_local_index: int
    negative_local_index: int
    branch_local_indices: tuple[tuple[int, int], ...]
    direct_retained_branch_index: int | None
    owner_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledLayerSurfaceTerminationManifest:
    global_node_ids: tuple[str, ...]
    clusters: tuple[_CompiledCluster, ...]
    manifest_sha256: str
    source_manifest: Mapping[str, Any]

    def cache_identity_sha256(
        self,
        base_network_identity_sha256: str,
        selected_rail_id: str,
        frequencies_hz: Sequence[float] | NDArray[np.float64],
    ) -> str:
        base = str(base_network_identity_sha256).strip().casefold()
        if len(base) != 64 or any(character not in "0123456789abcdef" for character in base):
            raise LayerSurfaceTerminationError(
                "BASE_NETWORK_IDENTITY_INVALID", "base network identity must be SHA-256"
            )
        # Keep the selected-rail argument as a compatibility/input-validation
        # boundary for callers that also select the returned measurement port.
        # It must not enter the board-state solve identity: every mounted
        # physical termination is stamped and the global solve returns every
        # open Device measurement port from the same matrix factorization.
        _key(selected_rail_id, name="selected rail ID")
        frequencies = frequency_array(frequencies_hz)
        frequency_sha = sha256(
            np.asarray(frequencies, dtype="<f8").tobytes(order="C")
        ).hexdigest()
        return sha256(
            _canonical_json(
                {
                    "base_network_identity_sha256": base,
                    "termination_manifest_sha256": self.manifest_sha256,
                    "termination_application": "all_mounted_physical_clusters_v1",
                    "frequency_grid_sha256": frequency_sha,
                }
            )
        ).hexdigest()

    def evaluate(
        self,
        frequencies_hz: Sequence[float] | NDArray[np.float64],
        *,
        selected_rail_id: str,
        base_network_identity_sha256: str,
    ) -> EvaluatedLayerSurfaceTerminations:
        """Evaluate every physical mounted cluster; keep measurement ports open.

        ``selected_rail_id`` identifies the result port requested by the caller,
        not a class of physical loads to remove.  Device measurement ports are
        represented by the base network's incidence vectors and remain open;
        decaps mounted on the selected rail are internal board terminations and
        therefore participate in the same global Schur/Kron reduction.
        """

        frequencies = frequency_array(frequencies_hz)
        _key(selected_rail_id, name="selected rail ID")
        stamps: list[EvaluatedLayerSurfaceTerminationStamp] = []
        active_elements = 0
        maximum_residual = 0.0
        for compiled in self.clusters:
            admittance, residual = _cluster_admittance(compiled, frequencies)
            maximum_residual = max(maximum_residual, residual)
            active_elements += len(compiled.source.branches)
            stamps.append(
                EvaluatedLayerSurfaceTerminationStamp(
                    cluster_id=compiled.source.cluster_id,
                    rail_id=compiled.rail_id,
                    positive_node_index=compiled.positive_global_index,
                    negative_node_index=compiled.negative_global_index,
                    admittance_s=admittance,
                    owner_ids=compiled.owner_ids,
                    maximum_local_kron_relative_residual=residual,
                )
            )
        return EvaluatedLayerSurfaceTerminations(
            frequencies_hz=frequencies,
            selected_rail_id=selected_rail_id,
            stamps=tuple(stamps),
            diagnostics=TerminationStampDiagnostics(
                physical_cluster_count=len(self.clusters),
                active_other_rail_cluster_count=len(stamps),
                excluded_selected_rail_cluster_count=0,
                active_element_count=active_elements,
                maximum_local_kron_relative_residual=maximum_residual,
                # This invariant is intentional: the API has no measurement-
                # port termination input and cannot silently add Z0 or 1 ohm.
                external_port_termination_count=0,
            ),
            cache_identity_sha256=self.cache_identity_sha256(
                base_network_identity_sha256, selected_rail_id, frequencies
            ),
        )


def _cluster_admittance(
    compiled: _CompiledCluster,
    frequencies: NDArray[np.float64],
) -> tuple[NDArray[np.complex128], float]:
    node_count = len(compiled.local_node_ids)
    retained = (compiled.positive_local_index, compiled.negative_local_index)
    internal = tuple(index for index in range(node_count) if index not in retained)
    branch_admittances: list[NDArray[np.complex128]] = []
    for branch in compiled.source.branches:
        try:
            impedance = np.asarray(
                branch.model.impedance(frequencies), dtype=np.complex128
            )
        except Exception as exc:
            raise LayerSurfaceTerminationError(
                "BRANCH_MODEL_EVALUATION_FAILED",
                f"cluster {compiled.source.cluster_id!r} branch {branch.branch_id!r}: {exc}",
            ) from exc
        if (
            impedance.shape != frequencies.shape
            or not np.all(np.isfinite(impedance))
            or np.any(np.abs(impedance) <= _TINY)
        ):
            raise LayerSurfaceTerminationError(
                "BRANCH_IMPEDANCE_INVALID",
                f"cluster {compiled.source.cluster_id!r} branch "
                f"{branch.branch_id!r} returned invalid impedance",
            )
        tolerance = np.maximum(np.abs(impedance), _TINY) * _PASSIVITY_REL_LIMIT
        if np.any(impedance.real < -tolerance):
            raise LayerSurfaceTerminationError(
                "BRANCH_IMPEDANCE_ACTIVE",
                f"cluster {compiled.source.cluster_id!r} branch {branch.branch_id!r} is active",
            )
        branch_admittances.append(1.0 / impedance)

    direct_branch_index = compiled.direct_retained_branch_index
    if direct_branch_index is not None:
        # Scenario-topology v2 compiles every mounted capacitor body as one
        # branch directly between the two retained terminals.  The generic
        # local-Kron path below used to allocate and validate a 2x2 matrix for
        # every frequency of every capacitor (millions of tiny matrices on a
        # production board), even though compilation has already proven this
        # exact topology.
        #
        # Preserve the generic reducer's arithmetic order rather than replacing
        # it with the merely algebraic shortcut ``Y = 1 / Z``.  This keeps the
        # same overflow/non-finite boundary and bitwise result for ordinary
        # finite values while evaluating the complete frequency-dependent model
        # as one vector.  Reciprocity and zero row sum are constructive for a
        # single incidence branch; the final finite/non-zero/passivity gate is
        # still applied below.
        branch_admittance = branch_admittances[direct_branch_index]
        reduced_pp = branch_admittance
        reduced_pn = -branch_admittance
        reduced_np = -branch_admittance
        reduced_nn = branch_admittance
        output = np.asarray(
            0.25
            * (
                reduced_pp
                - reduced_pn
                - reduced_np
                + reduced_nn
            ),
            dtype=np.complex128,
        )
        output_magnitude = np.abs(output)
        tolerance = np.maximum(output_magnitude, _TINY) * _PASSIVITY_REL_LIMIT
        if (
            not np.all(np.isfinite(output))
            or np.any(output_magnitude <= _TINY)
            or np.any(output.real < -tolerance)
        ):
            raise LayerSurfaceTerminationError(
                "CLUSTER_ADMITTANCE_INVALID",
                f"cluster {compiled.source.cluster_id!r} produced "
                "invalid/passivity-violating admittance",
            )
        output.setflags(write=False)
        return output, 0.0

    output = np.empty(frequencies.shape, dtype=np.complex128)
    maximum_relative_residual = 0.0
    retained_array = np.asarray(retained, dtype=np.int64)
    internal_array = np.asarray(internal, dtype=np.int64)
    for frequency_index in range(frequencies.size):
        matrix = np.zeros((node_count, node_count), dtype=np.complex128)
        for (first, second), values in zip(
            compiled.branch_local_indices, branch_admittances, strict=True
        ):
            value = values[frequency_index]
            matrix[first, first] += value
            matrix[second, second] += value
            matrix[first, second] -= value
            matrix[second, first] -= value
        scale = max(float(np.max(np.abs(matrix), initial=0.0)), _TINY)
        if not np.allclose(
            matrix,
            matrix.T,
            rtol=_SYMMETRY_REL_LIMIT,
            atol=max(1.0e-18, scale * _SYMMETRY_REL_LIMIT),
        ):
            raise LayerSurfaceTerminationError(
                "CLUSTER_NONRECIPROCAL",
                f"cluster {compiled.source.cluster_id!r} violates reciprocity",
            )
        if float(np.max(np.abs(matrix.sum(axis=1)), initial=0.0)) > max(
            1.0e-18, scale * _ROW_SUM_REL_LIMIT
        ):
            raise LayerSurfaceTerminationError(
                "CLUSTER_ROW_SUM_FAILED",
                f"cluster {compiled.source.cluster_id!r} violates two-terminal KCL",
            )
        if internal:
            y_rr = matrix[np.ix_(retained_array, retained_array)]
            y_ri = matrix[np.ix_(retained_array, internal_array)]
            y_ir = matrix[np.ix_(internal_array, retained_array)]
            y_ii = matrix[np.ix_(internal_array, internal_array)]
            try:
                internal_solution = np.linalg.solve(y_ii, y_ir)
            except np.linalg.LinAlgError as exc:
                raise LayerSurfaceTerminationError(
                    "CLUSTER_KRON_SINGULAR",
                    f"cluster {compiled.source.cluster_id!r} has a singular internal block",
                ) from exc
            residual = y_ii @ internal_solution - y_ir
            relative_residual = float(
                np.linalg.norm(residual)
                / max(
                    float(
                        np.linalg.norm(y_ii) * np.linalg.norm(internal_solution)
                        + np.linalg.norm(y_ir)
                    ),
                    _TINY,
                )
            )
            if (
                not isfinite(relative_residual)
                or relative_residual > _KRON_RESIDUAL_REL_LIMIT
            ):
                raise LayerSurfaceTerminationError(
                    "CLUSTER_KRON_RESIDUAL_FAILED",
                    f"cluster {compiled.source.cluster_id!r} local Kron residual "
                    f"is {relative_residual:.3e}",
                )
            maximum_relative_residual = max(
                maximum_relative_residual, relative_residual
            )
            reduced = y_rr - y_ri @ internal_solution
        else:
            reduced = matrix[np.ix_(retained_array, retained_array)]
        reduced_scale = max(float(np.max(np.abs(reduced), initial=0.0)), _TINY)
        if not np.allclose(
            reduced,
            reduced.T,
            rtol=_SYMMETRY_REL_LIMIT,
            atol=max(1.0e-18, reduced_scale * _SYMMETRY_REL_LIMIT),
        ) or not np.allclose(
            reduced.sum(axis=1),
            0.0,
            rtol=_ROW_SUM_REL_LIMIT,
            atol=max(1.0e-18, reduced_scale * _ROW_SUM_REL_LIMIT),
        ):
            raise LayerSurfaceTerminationError(
                "CLUSTER_REDUCTION_INVALID",
                f"cluster {compiled.source.cluster_id!r} did not reduce to a "
                "reciprocal floating two-terminal",
            )
        admittance = complex(
            0.25
            * (
                reduced[0, 0]
                - reduced[0, 1]
                - reduced[1, 0]
                + reduced[1, 1]
            )
        )
        tolerance = max(abs(admittance), _TINY) * _PASSIVITY_REL_LIMIT
        if (
            not isfinite(admittance.real)
            or not isfinite(admittance.imag)
            or abs(admittance) <= _TINY
            or admittance.real < -tolerance
        ):
            raise LayerSurfaceTerminationError(
                "CLUSTER_ADMITTANCE_INVALID",
                f"cluster {compiled.source.cluster_id!r} produced "
                "invalid/passivity-violating admittance",
            )
        output[frequency_index] = admittance
    output.setflags(write=False)
    return output, maximum_relative_residual


def compile_layer_surface_termination_manifest(
    global_node_ids: Sequence[str],
    clusters: Sequence[LayerSurfaceTerminationCluster],
) -> CompiledLayerSurfaceTerminationManifest:
    """Validate physical ownership and compile local/global node mappings."""

    global_nodes = tuple(
        _identity(item, name="global layer-surface node") for item in global_node_ids
    )
    if not global_nodes or len({_key(item) for item in global_nodes}) != len(global_nodes):
        raise LayerSurfaceTerminationError(
            "GLOBAL_NODE_MANIFEST_INVALID",
            "global surface node IDs must be non-empty and case-insensitively unique",
        )
    global_position = {_key(item): index for index, item in enumerate(global_nodes)}
    cluster_tuple = tuple(clusters)
    if len({_key(item.cluster_id) for item in cluster_tuple}) != len(cluster_tuple):
        raise LayerSurfaceTerminationError(
            "CLUSTER_ID_DUPLICATED", "termination cluster IDs must be unique"
        )
    owner_ledger: dict[str, tuple[str, str]] = {}
    compiled: list[_CompiledCluster] = []
    for cluster in sorted(
        cluster_tuple, key=lambda item: (item.cluster_id.casefold(), item.cluster_id)
    ):
        if cluster.ownership_status != "complete":
            raise LayerSurfaceTerminationError(
                "OWNERSHIP_UNRESOLVED",
                f"cluster {cluster.cluster_id!r} ownership status is {cluster.ownership_status!r}",
            )
        rail_keys = {_key(item, name="rail owner ID") for item in cluster.rail_owner_ids}
        if len(rail_keys) != 1:
            raise LayerSurfaceTerminationError(
                "CROSS_RAIL_OWNERSHIP_UNRESOLVED",
                f"cluster {cluster.cluster_id!r} requires exactly one proven rail "
                f"owner; observed {tuple(cluster.rail_owner_ids)!r}",
            )
        positive_global = global_position.get(_key(cluster.positive_surface_node_id))
        negative_global = global_position.get(_key(cluster.negative_surface_node_id))
        if positive_global is None or negative_global is None:
            raise LayerSurfaceTerminationError(
                "TERMINATION_SURFACE_MISSING",
                f"cluster {cluster.cluster_id!r} references a non-retained surface",
            )
        local_display: dict[str, str] = {}
        for node in (
            cluster.positive_terminal_node_id,
            cluster.negative_terminal_node_id,
            *(item.first_node_id for item in cluster.branches),
            *(item.second_node_id for item in cluster.branches),
        ):
            local_display.setdefault(_key(node), node)
        local_nodes = tuple(
            local_display[key]
            for key in sorted(local_display)
        )
        local_position = {_key(item): index for index, item in enumerate(local_nodes)}
        adjacency = [set() for _item in local_nodes]
        branch_indices: list[tuple[int, int]] = []
        cluster_owners: set[str] = set()
        for owner in cluster.topology_owner_ids:
            owner_key = _key(owner, name="topology owner ID")
            previous = owner_ledger.setdefault(
                owner_key, (cluster.cluster_id, "<topology>")
            )
            if previous != (cluster.cluster_id, "<topology>"):
                raise LayerSurfaceTerminationError(
                    "PHYSICAL_OWNER_DUPLICATED",
                    f"topology owner {owner!r} is stamped by {previous!r} and "
                    f"{(cluster.cluster_id, '<topology>')!r}",
                )
            cluster_owners.add(owner)
        for branch in cluster.branches:
            first = local_position[_key(branch.first_node_id)]
            second = local_position[_key(branch.second_node_id)]
            adjacency[first].add(second)
            adjacency[second].add(first)
            branch_indices.append((first, second))
            for owner in branch.owner_ids:
                owner_key = _key(owner, name="physical owner ID")
                previous = owner_ledger.setdefault(
                    owner_key, (cluster.cluster_id, branch.branch_id)
                )
                if previous != (cluster.cluster_id, branch.branch_id):
                    raise LayerSurfaceTerminationError(
                        "PHYSICAL_OWNER_DUPLICATED",
                        f"physical owner {owner!r} is stamped by {previous!r} "
                        f"and {(cluster.cluster_id, branch.branch_id)!r}",
                    )
                cluster_owners.add(owner)
        start = local_position[_key(cluster.positive_terminal_node_id)]
        stop = local_position[_key(cluster.negative_terminal_node_id)]
        reached = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor not in reached:
                    reached.add(neighbor)
                    pending.append(neighbor)
        if len(reached) != len(local_nodes) or stop not in reached:
            raise LayerSurfaceTerminationError(
                "CLUSTER_TOPOLOGY_DISCONNECTED",
                f"cluster {cluster.cluster_id!r} contains a "
                "floating/disconnected circuit component",
            )
        direct_retained_branch_index = (
            0
            if len(local_nodes) == 2
            and len(branch_indices) == 1
            and {
                branch_indices[0][0],
                branch_indices[0][1],
            }
            == {start, stop}
            else None
        )
        compiled.append(
            _CompiledCluster(
                source=cluster,
                rail_id=cluster.rail_owner_ids[0],
                positive_global_index=positive_global,
                negative_global_index=negative_global,
                local_node_ids=local_nodes,
                positive_local_index=local_position[
                    _key(cluster.positive_terminal_node_id)
                ],
                negative_local_index=stop,
                branch_local_indices=tuple(branch_indices),
                direct_retained_branch_index=direct_retained_branch_index,
                owner_ids=tuple(
                    sorted(cluster_owners, key=lambda item: (item.casefold(), item))
                ),
            )
        )

    source_manifest = {
        "schema_version": TERMINATION_MANIFEST_SCHEMA,
        "compiler_id": TERMINATION_COMPILER_ID,
        "external_measurement_ports": "open",
        "clusters": [
            dict(item.evidence_manifest())
            for item in sorted(
                cluster_tuple,
                key=lambda cluster: (cluster.cluster_id.casefold(), cluster.cluster_id),
            )
        ],
    }
    manifest_sha = sha256(_canonical_json(source_manifest)).hexdigest()
    return CompiledLayerSurfaceTerminationManifest(
        global_node_ids=global_nodes,
        clusters=tuple(compiled),
        manifest_sha256=manifest_sha,
        source_manifest=MappingProxyType(source_manifest),
    )


__all__ = [
    "CompiledLayerSurfaceTerminationManifest",
    "EvaluatedLayerSurfaceTerminationStamp",
    "EvaluatedLayerSurfaceTerminations",
    "LayerSurfaceTerminationBranch",
    "LayerSurfaceTerminationCluster",
    "LayerSurfaceTerminationError",
    "TERMINATION_COMPILER_ID",
    "TERMINATION_MANIFEST_SCHEMA",
    "TerminationStampDiagnostics",
    "compile_layer_surface_termination_manifest",
    "impedance_model_identity_sha256",
    "scoped_impedance_model_identity_cache",
]
