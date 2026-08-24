"""Layer-resolved uniform-mode network algebra.

This module keeps every physical ``(layer, net)`` surface as a distinct node.
Adjacent dielectric-gap Maxwell admittances are embedded on those nodes, and
source-observed vertical links are applied explicitly before a single global
open-port Kron reduction.  It deliberately contains no Touchstone fitting and
does not claim non-uniform in-plane current, via mutual coupling, or full-wave
S-parameter cascading.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite
import os
import sys
from threading import Condition, Event, RLock
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csc_matrix, diags, issparse
from scipy.sparse.linalg import LinearOperator, norm as sparse_norm, onenormest, splu

from .modal import DielectricDispersion
from .layer_surface_termination import (
    CompiledLayerSurfaceTerminationManifest,
    EvaluatedLayerSurfaceTerminationStamp,
    EvaluatedLayerSurfaceTerminations,
    LayerSurfaceTerminationError,
    _canonical_json as _termination_canonical_json,
    impedance_model_identity_sha256,
    scoped_impedance_model_identity_cache,
)
from .uniform_c00 import DispersiveAdjacentGap


_TINY = 1.0e-30
_SYMMETRY_REL_LIMIT = 1.0e-10
_ROW_SUM_REL_LIMIT = 1.0e-10
_RESIDUAL_REL_LIMIT = 1.0e-9
_MAX_FACTOR_PIVOT_RATIO = 1.0e13
_PORT_RHS_BATCH_SIZE = 4
_TERMINATION_MAPPING_CACHE_LIMIT = 4
# Two simultaneous SuperLU factors are an intentional memory-aware ceiling for
# the supported 64 GiB workstation.  Frequency points are mathematically
# independent, but each board-scale factor can retain a substantial sparse
# fill-in.  Small grids/networks stay sequential so executor overhead never
# dominates interactive or unit-test workloads.
_FREQUENCY_SOLVE_MAX_WORKERS = 2
_FREQUENCY_PARALLEL_MIN_POINTS = 8
_FREQUENCY_PARALLEL_MIN_ACTIVE_NODES = 64
_FREQUENCY_PARALLEL_POLL_SECONDS = 0.05
# Real-board measurement showed roughly 9 GiB of additional resident/private
# memory per SuperLU factor.  Round that estimate up and retain 12 GiB for the
# compiled board, Python/UI, OS, and sparse assembly transients.  Thus a second
# factor is permitted only with at least 32 GiB currently available.  A normal
# 16/32 GiB workstation remains sequential; the supported 64 GiB validation
# host had ~43 GiB available at solve start.  Unknown memory is fail-safe 1x.
_ESTIMATED_BOARD_FACTOR_BYTES = 10 * 1024**3
_FREQUENCY_PARALLEL_MEMORY_RESERVE_BYTES = 12 * 1024**3
_FREQUENCY_RESULT_CACHE_ENTRY_LIMIT = 2048
_FREQUENCY_RESULT_CACHE_BYTE_LIMIT = 16 * 1024**2
_FREQUENCY_RESULT_CACHE_STATE_RETAIN_CHARGE_BYTES = 1024**2
_FREQUENCY_RESULT_CACHE_STATE_LIMIT = 2
_FACTOR_RESERVATION_CONDITION = Condition()
_ACTIVE_FACTOR_RESERVATIONS = 0


def _available_physical_memory_bytes() -> int | None:
    """Best-effort available-RAM query without adding a runtime dependency."""

    if os.name == "nt":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = (
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                )

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(status)
            ):
                return None
            available = int(status.ullAvailPhys)
            return available if available > 0 else None
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    try:
        page_count = int(os.sysconf("SC_AVPHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        available = page_count * page_size
        return available if available > 0 else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _frequency_solve_worker_count(
    frequency_count: int,
    active_node_count: int,
) -> int:
    """Select 1x/2x solving with a conservative runtime RAM gate."""

    if (
        frequency_count < _FREQUENCY_PARALLEL_MIN_POINTS
        or active_node_count < _FREQUENCY_PARALLEL_MIN_ACTIVE_NODES
        or _FREQUENCY_SOLVE_MAX_WORKERS < 2
    ):
        return 1
    available = _available_physical_memory_bytes()
    required = (
        2 * _ESTIMATED_BOARD_FACTOR_BYTES
        + _FREQUENCY_PARALLEL_MEMORY_RESERVE_BYTES
    )
    if available is None or available < required:
        return 1
    return min(_FREQUENCY_SOLVE_MAX_WORKERS, frequency_count)


def _factor_capacity_now() -> int:
    available = _available_physical_memory_bytes()
    required = (
        2 * _ESTIMATED_BOARD_FACTOR_BYTES
        + _FREQUENCY_PARALLEL_MEMORY_RESERVE_BYTES
    )
    return 2 if available is not None and available >= required else 1


def _acquire_factor_reservation(
    stop_requested: Event,
    is_cancelled: Callable[[], bool],
) -> None:
    """Reserve one process-global factor slot with live RAM rechecks."""

    global _ACTIVE_FACTOR_RESERVATIONS
    while True:
        if stop_requested.is_set() or is_cancelled():
            raise RuntimeError("layer-surface solve cancelled")
        with _FACTOR_RESERVATION_CONDITION:
            capacity = min(_FREQUENCY_SOLVE_MAX_WORKERS, _factor_capacity_now())
            if _ACTIVE_FACTOR_RESERVATIONS < max(1, capacity):
                _ACTIVE_FACTOR_RESERVATIONS += 1
                return
            _FACTOR_RESERVATION_CONDITION.wait(
                timeout=_FREQUENCY_PARALLEL_POLL_SECONDS
            )


def _release_factor_reservation() -> None:
    global _ACTIVE_FACTOR_RESERVATIONS
    with _FACTOR_RESERVATION_CONDITION:
        if _ACTIVE_FACTOR_RESERVATIONS < 1:
            raise RuntimeError("layer-surface factor reservation underflow")
        _ACTIVE_FACTOR_RESERVATIONS -= 1
        _FACTOR_RESERVATION_CONDITION.notify_all()


class LayerSurfaceNetworkError(ValueError):
    """Raised when a source topology or a raw reduction is unsafe."""


def _identity(value: str, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise LayerSurfaceNetworkError(f"{name} must not be blank")
    return text


def _readonly(values: object, dtype: np.dtype | type) -> NDArray:
    result = np.asarray(values, dtype=dtype).copy()
    result.setflags(write=False)
    return result


def _readonly_canonical_csc(values: csc_matrix) -> csc_matrix:
    """Return an owned canonical CSC matrix with immutable numerical buffers."""

    result = csc_matrix(values, copy=True)
    result.sum_duplicates()
    result.eliminate_zeros()
    result.sort_indices()
    # SciPy may expose buffer views whose hidden owning base remains writable
    # even after the public view is locked. Assign independent owning arrays so
    # no caller-held alias can mutate a compiled network after construction.
    result.data = np.array(result.data, copy=True)
    result.indices = np.array(result.indices, copy=True)
    result.indptr = np.array(result.indptr, copy=True)
    result.data.setflags(write=False)
    result.indices.setflags(write=False)
    result.indptr.setflags(write=False)
    return result


def _identity_sha256(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def _canonical_dispersion_snapshot(dispersion: object) -> tuple[object, ...]:
    """Snapshot every numerical source row of a supported dispersion.

    ``CompositeDielectricDispersion`` lives in ``layerwise_network``, which
    imports this module, so importing that class here would create a cycle.
    Its exact production type is identified by module/qualname and then
    recursively validated.  Unknown interpolate-compatible objects remain
    fail closed; a cache identity must never depend on opaque mutable state.
    """

    dispersion_type = type(dispersion)
    type_identity = (
        dispersion_type.__module__,
        dispersion_type.__qualname__,
    )
    if dispersion_type is DielectricDispersion:
        frequencies = tuple(float(value) for value in dispersion.frequencies_hz)
        permittivities = tuple(
            float(value) for value in dispersion.relative_permittivities
        )
        loss_tangents = tuple(float(value) for value in dispersion.loss_tangents)
        if (
            not frequencies
            or len(permittivities) != len(frequencies)
            or len(loss_tangents) != len(frequencies)
            or any(not isfinite(value) or value <= 0.0 for value in frequencies)
            or any(not isfinite(value) or value <= 0.0 for value in permittivities)
            or any(not isfinite(value) or value < 0.0 for value in loss_tangents)
            or any(
                right <= left
                for left, right in zip(frequencies, frequencies[1:])
            )
        ):
            raise LayerSurfaceNetworkError(
                "tabulated dielectric dispersion is invalid"
            )
        return (
            "tabulated-dielectric-v1",
            *type_identity,
            frequencies,
            permittivities,
            loss_tangents,
        )

    if type_identity == (
        "spd_decap_pi._core.solver.layerwise_network",
        "CompositeDielectricDispersion",
    ):
        layers = getattr(dispersion, "layers", None)
        if type(layers) is not tuple or not layers:
            raise LayerSurfaceNetworkError(
                "composite dielectric dispersion has no immutable source rows"
            )
        rows: list[tuple[object, ...]] = []
        for raw_row in layers:
            if type(raw_row) is not tuple or len(raw_row) != 2:
                raise LayerSurfaceNetworkError(
                    "composite dielectric dispersion row is malformed"
                )
            thickness_um = float(raw_row[0])
            if not isfinite(thickness_um) or thickness_um <= 0.0:
                raise LayerSurfaceNetworkError(
                    "composite dielectric thickness is invalid"
                )
            rows.append(
                (
                    thickness_um,
                    _canonical_dispersion_snapshot(raw_row[1]),
                )
            )
        return (
            "series-composite-dielectric-v1",
            *type_identity,
            tuple(rows),
        )

    raise LayerSurfaceNetworkError(
        "unsupported dielectric dispersion type "
        f"{dispersion_type.__module__}.{dispersion_type.__qualname__}"
    )


@dataclass(frozen=True, slots=True)
class LayerSurfaceViaLink:
    """One source-observed vertical connection between two surface nodes.

    ``mode='finite_parallel_rl'`` represents ``count`` identical, independent
    self-R/L branches in parallel.  ``mode='topology_only_ideal'`` is reserved
    for an observed connection whose impedance ownership cannot be separated
    safely from an external terminal model; it contributes topology but no
    second R/L energy term.  The latter approximation is always visible in
    provenance and is never silently converted into invented R/L values.
    """

    link_id: str
    first_node_id: str
    second_node_id: str
    count: int
    mode: str
    resistance_ohm_per_via: float | None = None
    inductance_h_per_via: float | None = None
    owner_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "link_id", _identity(self.link_id, name="link_id"))
        first = _identity(self.first_node_id, name="first_node_id")
        second = _identity(self.second_node_id, name="second_node_id")
        if first == second:
            raise LayerSurfaceNetworkError("a vertical link cannot join one node to itself")
        if not isinstance(self.count, int) or self.count < 1:
            raise LayerSurfaceNetworkError("vertical link count must be a positive integer")
        if self.mode not in {"finite_parallel_rl", "topology_only_ideal"}:
            raise LayerSurfaceNetworkError(f"unsupported vertical link mode {self.mode!r}")
        if self.mode == "finite_parallel_rl":
            resistance = float(self.resistance_ohm_per_via)  # type: ignore[arg-type]
            inductance = float(self.inductance_h_per_via)  # type: ignore[arg-type]
            if (
                not isfinite(resistance)
                or not isfinite(inductance)
                or resistance < 0.0
                or inductance < 0.0
                or (resistance == 0.0 and inductance == 0.0)
            ):
                raise LayerSurfaceNetworkError(
                    "finite vertical-link R/L must be finite, passive, and non-zero"
                )
        elif self.resistance_ohm_per_via is not None or self.inductance_h_per_via is not None:
            raise LayerSurfaceNetworkError(
                "topology-only links must not smuggle an unowned R/L value"
            )
        owners = tuple(sorted({_identity(item, name="owner_id") for item in self.owner_ids}))
        if not owners:
            owners = (f"via-link:{self.link_id}",)
        object.__setattr__(self, "first_node_id", first)
        object.__setattr__(self, "second_node_id", second)
        object.__setattr__(self, "owner_ids", owners)


@dataclass(frozen=True, slots=True)
class LayerSurfacePort:
    port_id: str
    positive_node_id: str
    negative_node_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "port_id", _identity(self.port_id, name="port_id"))
        positive = _identity(self.positive_node_id, name="positive_node_id")
        negative = _identity(self.negative_node_id, name="negative_node_id")
        if positive == negative:
            raise LayerSurfaceNetworkError("a layer-surface port cannot be shorted")
        object.__setattr__(self, "positive_node_id", positive)
        object.__setattr__(self, "negative_node_id", negative)


@dataclass(frozen=True, slots=True)
class LayerSurfaceSolveDiagnostics:
    physical_surface_count: int
    reduced_node_count: int
    structural_component_count: int
    finite_via_link_count: int
    topology_only_link_count: int
    maximum_factor_pivot_ratio: float
    maximum_relative_residual: float
    active_termination_cluster_count: int = 0
    excluded_selected_rail_cluster_count: int = 0
    active_termination_element_count: int = 0
    maximum_termination_kron_relative_residual: float = 0.0
    termination_manifest_sha256: str | None = None
    solve_identity_sha256: str | None = None
    disabled_via_link_count: int = 0
    disabled_via_link_ids: tuple[str, ...] = ()
    disabled_via_link_ids_sha256: str | None = None
    active_reduced_node_count: int = 0
    pruned_portless_node_count: int = 0
    active_structural_component_count: int = 0
    maximum_port_rhs_batch_size: int = 0


@dataclass(frozen=True, slots=True)
class LayerSurfaceSolveResult:
    frequencies_hz: NDArray[np.float64]
    effective_admittance_by_port: Mapping[str, NDArray[np.complex128]]
    diagnostics: LayerSurfaceSolveDiagnostics

    def __post_init__(self) -> None:
        frequencies = _readonly(self.frequencies_hz, np.float64)
        if (
            frequencies.ndim != 1
            or not frequencies.size
            or not np.all(np.isfinite(frequencies))
            or np.any(frequencies <= 0.0)
        ):
            raise LayerSurfaceNetworkError("solve frequencies must be finite and positive")
        values: dict[str, NDArray[np.complex128]] = {}
        for port_id, raw in self.effective_admittance_by_port.items():
            port = _identity(port_id, name="port result ID")
            array = _readonly(raw, np.complex128)
            if array.shape != frequencies.shape or not np.all(np.isfinite(array)):
                raise LayerSurfaceNetworkError("port admittance result has an invalid shape")
            values[port] = array
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(
            self, "effective_admittance_by_port", MappingProxyType(values)
        )


@dataclass(frozen=True, slots=True)
class _FrequencySolveResult:
    """One independently assembled/factored frequency result.

    Workers never publish a sparse matrix, factor, or writable output buffer.
    The coordinator commits these scalar results in exact frequency-index
    order, which keeps output/progress deterministic even when factors finish
    out of order.
    """

    admittance_by_port_index: tuple[complex, ...]
    maximum_factor_pivot_ratio: float
    maximum_relative_residual: float
    maximum_port_rhs_batch_size: int
    point_cache_identity: bytes


@dataclass(frozen=True, slots=True)
class _FrequencyResultCacheBinding:
    """Strong identity guard for one reusable physical board state."""

    state_identity_sha256: str
    base_network_identity_sha256: str | None
    disabled_via_link_ids: tuple[str, ...]
    surface_node_ids: object
    partials: object
    via_links: object
    ports: object
    reduced_node_ids: object
    surface_to_reduced: object
    collapsed_partials: object
    finite_links: object
    components: object
    component_by_node: object
    port_reduced_nodes: object
    port_snapshot: tuple[tuple[object, ...], ...]
    dispersion_snapshot: tuple[tuple[object, ...], ...]
    finite_stamp_sha256: str
    termination_manifest: CompiledLayerSurfaceTerminationManifest | None
    termination_manifest_sha256: str | None
    termination_global_node_ids: object | None
    termination_clusters: object | None
    termination_source_manifest: object | None
    termination_cluster_sources: tuple[object, ...]
    termination_cluster_branches: tuple[object, ...]
    termination_branch_models: tuple[object, ...]
    termination_model_identity_sha256s: tuple[str, ...]
    termination_compiled_snapshot: tuple[tuple[object, ...], ...]
    termination_evidence_sha256: str | None


@dataclass(frozen=True, slots=True)
class _FrequencyResultCacheEntry:
    binding: _FrequencyResultCacheBinding
    point_cache_identity: bytes
    result: _FrequencySolveResult
    estimated_size_bytes: int


def _frequency_result_cache_entry_size(
    point_cache_identity: bytes,
    result: _FrequencySolveResult,
) -> int:
    """Conservatively account result-owned memory (not shared board objects)."""

    values = result.admittance_by_port_index
    return int(
        512
        + sys.getsizeof(point_cache_identity)
        + sys.getsizeof(result)
        + sys.getsizeof(values)
        + sum(sys.getsizeof(value) for value in values)
    )


def _frequency_result_cache_binding_size(
    binding: _FrequencyResultCacheBinding,
) -> int:
    """Charge each canonical retained state once, including owned guards."""

    owned_tuples = (
        binding.dispersion_snapshot,
        binding.port_snapshot,
        binding.termination_cluster_sources,
        binding.termination_cluster_branches,
        binding.termination_branch_models,
        binding.termination_model_identity_sha256s,
        binding.termination_compiled_snapshot,
    )
    return int(
        _FREQUENCY_RESULT_CACHE_STATE_RETAIN_CHARGE_BYTES
        + sys.getsizeof(binding)
        + sum(sys.getsizeof(item) for item in owned_tuples)
        + sum(
            sys.getsizeof(item)
            for snapshot in (
                binding.dispersion_snapshot,
                binding.termination_compiled_snapshot,
            )
            for item in snapshot
        )
    )


class _UnionFind:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, first: int, second: int) -> None:
        left, right = self.find(first), self.find(second)
        if left != right:
            self.parent[right] = left


@dataclass(frozen=True, slots=True)
class _SurfaceNodeIndexCacheEntry:
    """Retain the exact immutable surface inventory behind one O(1) index."""

    surface_node_ids: tuple[str, ...]
    physical_index_by_node: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _TerminationMappingCacheEntry:
    """Strongly retain one exact manifest and its verified node mapping.

    The manifest reference prevents object-ID reuse.  Reuse is deliberately
    limited to the same frozen compiled object rather than trusting a caller-
    supplied SHA alone; a separately constructed manifest therefore still
    runs every surface and physical-owner conflict gate.
    """

    manifest: CompiledLayerSurfaceTerminationManifest
    manifest_sha256: str
    global_node_ids: tuple[str, ...]
    clusters: tuple[object, ...]
    cluster_owner_ids: tuple[tuple[str, ...], ...]
    network_surface_node_ids: tuple[str, ...]
    network_via_links: tuple[LayerSurfaceViaLink, ...]
    network_surface_to_reduced: NDArray[np.int64]
    mapping: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CompiledLayerSurfaceNetwork:
    """Immutable topology and sparse nominal stamps reusable across frequencies."""

    surface_node_ids: tuple[str, ...]
    partials: tuple[DispersiveAdjacentGap, ...]
    via_links: tuple[LayerSurfaceViaLink, ...]
    ports: tuple[LayerSurfacePort, ...]
    _reduced_node_ids: tuple[str, ...]
    _surface_to_reduced: NDArray[np.int64]
    _collapsed_partials: tuple[csc_matrix, ...]
    _finite_links: tuple[tuple[int, int, LayerSurfaceViaLink], ...]
    _components: tuple[tuple[int, ...], ...]
    _component_by_node: tuple[int, ...]
    _port_reduced_nodes: tuple[tuple[int, int], ...]
    _compiled_port_snapshot: tuple[tuple[object, ...], ...] = field(
        default=(),
        init=False,
        repr=False,
        compare=False,
    )
    _compiled_via_snapshot: tuple[tuple[object, ...], ...] = field(
        default=(),
        init=False,
        repr=False,
        compare=False,
    )
    _compiled_dispersion_snapshot: tuple[tuple[object, ...], ...] = field(
        default=(),
        init=False,
        repr=False,
        compare=False,
    )
    _surface_node_index_cache: _SurfaceNodeIndexCacheEntry | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _surface_node_index_cache_lock: RLock = field(
        default_factory=RLock,
        init=False,
        repr=False,
        compare=False,
    )
    _termination_mapping_cache: OrderedDict[
        int, _TerminationMappingCacheEntry
    ] = field(
        default_factory=OrderedDict,
        init=False,
        repr=False,
        compare=False,
    )
    _termination_mapping_cache_lock: RLock = field(
        default_factory=RLock,
        init=False,
        repr=False,
        compare=False,
    )
    _frequency_result_cache: OrderedDict[
        tuple[str, bytes], _FrequencyResultCacheEntry
    ] = field(
        default_factory=OrderedDict,
        init=False,
        repr=False,
        compare=False,
    )
    _frequency_result_cache_bytes: int = field(
        default=0,
        init=False,
        repr=False,
        compare=False,
    )
    _frequency_result_cache_bindings: OrderedDict[
        str, _FrequencyResultCacheBinding
    ] = field(
        default_factory=OrderedDict,
        init=False,
        repr=False,
        compare=False,
    )
    _frequency_result_cache_state_counts: dict[str, int] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _frequency_result_cache_binding_bytes: dict[str, int] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _frequency_result_cache_lock: RLock = field(
        default_factory=RLock,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        # ``frozen=True`` does not recursively freeze SciPy sparse matrices.
        # Own and lock every persisted CSC buffer so an in-place capacitance
        # edit cannot change the physical board beneath an existing solve/cache
        # identity. ``dataclasses.replace`` and compatibility constructors run
        # this same gate. Always owning a fresh copy is deliberate: accepting a
        # merely read-only view could retain a caller-held writable base alias.
        object.__setattr__(
            self,
            "_collapsed_partials",
            tuple(
                _readonly_canonical_csc(matrix)
                for matrix in self._collapsed_partials
            ),
        )
        surface_to_reduced = np.array(
            self._surface_to_reduced,
            dtype=np.int64,
            order="C",
            copy=True,
        )
        if surface_to_reduced.ndim != 1:
            raise LayerSurfaceNetworkError(
                "physical-to-reduced mapping must be one-dimensional"
            )
        surface_to_reduced.setflags(write=False)
        object.__setattr__(self, "_surface_to_reduced", surface_to_reduced)
        physical_by_node = self._surface_node_physical_index_uncached(
            self.surface_node_ids
        )
        object.__setattr__(
            self,
            "_compiled_port_snapshot",
            self._current_port_snapshot(physical_by_node),
        )
        object.__setattr__(
            self,
            "_compiled_via_snapshot",
            self._current_via_snapshot(physical_by_node),
        )
        object.__setattr__(
            self,
            "_compiled_dispersion_snapshot",
            self._current_dispersion_snapshot(),
        )

    def _termination_mapping_cache_dependencies(
        self,
        manifest: CompiledLayerSurfaceTerminationManifest,
    ) -> tuple[
        str,
        tuple[str, ...],
        tuple[object, ...],
        tuple[tuple[str, ...], ...],
    ] | None:
        """Return immutable inputs consumed by the mapping audit.

        Production compiler outputs are frozen tuples and a read-only reduction
        array.  Compatibility/manually-constructed mutable objects still run
        the complete audit, but never qualify for an identity-cache shortcut.
        """

        digest = str(manifest.manifest_sha256).strip().casefold()
        global_node_ids = manifest.global_node_ids
        clusters = manifest.clusters
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or type(global_node_ids) is not tuple
            or type(clusters) is not tuple
            or type(self.surface_node_ids) is not tuple
            or type(self.via_links) is not tuple
            or bool(self._surface_to_reduced.flags.writeable)
            or not bool(self._surface_to_reduced.flags.owndata)
            or self._surface_to_reduced.base is not None
            or not bool(self._surface_to_reduced.flags.c_contiguous)
            or self._surface_to_reduced.dtype != np.dtype(np.int64)
        ):
            return None
        owner_ids: list[tuple[str, ...]] = []
        for cluster in clusters:
            raw_owner_ids = getattr(cluster, "owner_ids", None)
            if type(raw_owner_ids) is not tuple:
                return None
            owner_ids.append(raw_owner_ids)
        return digest, global_node_ids, clusters, tuple(owner_ids)

    def clear_termination_mapping_cache(self) -> None:
        """Release strong manifest references retained by this network."""

        with self._termination_mapping_cache_lock:
            self._termination_mapping_cache.clear()

    def clear_surface_node_index_cache(self) -> None:
        """Release the lazily built exact surface-node lookup index."""

        with self._surface_node_index_cache_lock:
            object.__setattr__(self, "_surface_node_index_cache", None)

    def clear_frequency_result_cache(self) -> None:
        """Release cached per-frequency outputs and strong state guards."""

        with self._frequency_result_cache_lock:
            self._frequency_result_cache.clear()
            self._frequency_result_cache_bindings.clear()
            self._frequency_result_cache_state_counts.clear()
            self._frequency_result_cache_binding_bytes.clear()
            object.__setattr__(self, "_frequency_result_cache_bytes", 0)

    def _surface_node_physical_index_uncached(
        self,
        surface_node_ids: Sequence[str],
    ) -> Mapping[str, int]:
        """Build one exact, case-sensitive surface-to-physical index.

        Production compiler outputs are already normalized immutable tuples,
        but this independent gate keeps manually constructed or adversarially
        replaced networks fail-closed instead of letting a stale shortcut hide
        duplicate or non-canonical surface identities.
        """

        physical_by_node: dict[str, int] = {}
        for physical_index, raw_node_id in enumerate(surface_node_ids):
            node_id = _identity(raw_node_id, name="surface node ID")
            if raw_node_id != node_id or node_id in physical_by_node:
                raise LayerSurfaceNetworkError(
                    "surface node IDs must be non-empty, canonical, and unique"
                )
            physical_by_node[node_id] = physical_index
        if not physical_by_node:
            raise LayerSurfaceNetworkError(
                "surface node IDs must be non-empty, canonical, and unique"
            )
        return MappingProxyType(physical_by_node)

    def _surface_node_physical_index(self) -> Mapping[str, int]:
        """Return an O(1) exact lookup bound to this surface tuple identity."""

        surface_node_ids = self.surface_node_ids
        if type(surface_node_ids) is not tuple:
            # Compatibility/manual objects with mutable inventories never earn
            # a cache shortcut; every lookup re-runs the complete identity gate.
            return self._surface_node_physical_index_uncached(surface_node_ids)

        with self._surface_node_index_cache_lock:
            surface_node_ids = self.surface_node_ids
            if type(surface_node_ids) is not tuple:
                return self._surface_node_physical_index_uncached(surface_node_ids)
            cached = self._surface_node_index_cache
            if cached is not None and cached.surface_node_ids is surface_node_ids:
                return cached.physical_index_by_node
            physical_by_node = self._surface_node_physical_index_uncached(
                surface_node_ids
            )
            object.__setattr__(
                self,
                "_surface_node_index_cache",
                _SurfaceNodeIndexCacheEntry(
                    surface_node_ids=surface_node_ids,
                    physical_index_by_node=physical_by_node,
                ),
            )
            return physical_by_node

    def has_exact_surface_node(self, surface_node_id: str) -> bool:
        """Whether the canonical inventory contains this exact physical ID.

        Unlike :meth:`reduced_node_index`, this membership query deliberately
        does not trim or otherwise normalize the requested identity.  It is the
        O(1) semantic replacement for exact tuple membership checks.
        """

        return surface_node_id in self._surface_node_physical_index()

    def reduced_node_index(self, surface_node_id: str) -> int:
        """Return the ideal-topology node owning one physical surface.

        Finite R/L branches do not coalesce surfaces and therefore cannot
        satisfy terminal attachment proof through this structural query.
        """

        identity = _identity(surface_node_id, name="surface node ID")
        try:
            physical_index = self._surface_node_physical_index()[identity]
        except KeyError as exc:
            raise LayerSurfaceNetworkError(
                f"unknown physical surface node {identity!r}"
            ) from exc
        if physical_index >= len(self._surface_to_reduced):
            raise LayerSurfaceNetworkError(
                "surface-node index exceeds the physical-to-reduced mapping"
            )
        reduced_index = int(self._surface_to_reduced[physical_index])
        if reduced_index < 0 or reduced_index >= len(self._reduced_node_ids):
            raise LayerSurfaceNetworkError(
                "physical surface maps outside the compiled reduced-node inventory"
            )
        return reduced_index

    def surfaces_share_ideal_node(self, first: str, second: str) -> bool:
        """Whether two physical surfaces share source-proven ideal topology."""

        return self.reduced_node_index(first) == self.reduced_node_index(second)

    def termination_reduced_node_mapping(
        self,
        manifest: CompiledLayerSurfaceTerminationManifest,
    ) -> tuple[int, ...]:
        """Map a termination's physical surfaces onto this ideal reduction.

        The termination compiler deliberately owns physical surface IDs while
        this network may have coalesced some of those surfaces through exact
        topology-only links.  Mapping is case-insensitive but must be unique;
        no ordering or nearest-node fallback is allowed.
        """

        if not isinstance(manifest, CompiledLayerSurfaceTerminationManifest):
            raise LayerSurfaceNetworkError(
                "termination manifest is not a compiled layer-surface manifest"
            )
        identity = id(manifest)
        dependencies = self._termination_mapping_cache_dependencies(manifest)
        # One scenario binding is validated repeatedly while it is wrapped as
        # binding -> substrate -> selected-rail source model.  On the real
        # boards that otherwise re-scanned ~790k surfaces and ~1.7M physical
        # Via owners for every wrapper and every rail.  Keep validation and
        # insertion under one lock so concurrent preflights cannot duplicate
        # that board-scale audit.
        with self._termination_mapping_cache_lock:
            cached = self._termination_mapping_cache.get(identity)
            if (
                cached is not None
                and dependencies is not None
                and cached.manifest is manifest
                and cached.manifest_sha256 == dependencies[0]
                and cached.global_node_ids is dependencies[1]
                and cached.clusters is dependencies[2]
                and cached.cluster_owner_ids == dependencies[3]
                and cached.network_surface_node_ids is self.surface_node_ids
                and cached.network_via_links is self.via_links
                and cached.network_surface_to_reduced is self._surface_to_reduced
            ):
                self._termination_mapping_cache.move_to_end(identity)
                return cached.mapping

            result = self._termination_reduced_node_mapping_uncached(manifest)
            if dependencies is not None:
                self._termination_mapping_cache[identity] = (
                    _TerminationMappingCacheEntry(
                        manifest=manifest,
                        manifest_sha256=dependencies[0],
                        global_node_ids=dependencies[1],
                        clusters=dependencies[2],
                        cluster_owner_ids=dependencies[3],
                        network_surface_node_ids=self.surface_node_ids,
                        network_via_links=self.via_links,
                        network_surface_to_reduced=self._surface_to_reduced,
                        mapping=result,
                    )
                )
                self._termination_mapping_cache.move_to_end(identity)
                while (
                    len(self._termination_mapping_cache)
                    > _TERMINATION_MAPPING_CACHE_LIMIT
                ):
                    self._termination_mapping_cache.popitem(last=False)
            return result

    def _termination_reduced_node_mapping_uncached(
        self,
        manifest: CompiledLayerSurfaceTerminationManifest,
    ) -> tuple[int, ...]:
        """Run the complete mapping and physical-owner conflict audit."""

        physical_by_key: dict[str, int] = {}
        for index, node_id in enumerate(self.surface_node_ids):
            key = node_id.casefold()
            if key in physical_by_key:
                raise LayerSurfaceNetworkError(
                    "physical surface IDs are ambiguous under case-insensitive mapping"
                )
            physical_by_key[key] = index
        mapped: list[int] = []
        for node_id in manifest.global_node_ids:
            physical = physical_by_key.get(node_id.casefold())
            if physical is None:
                raise LayerSurfaceNetworkError(
                    f"termination surface {node_id!r} is absent from the layer network"
                )
            mapped.append(int(self._surface_to_reduced[physical]))

        base_owners: dict[str, str] = {}
        for link in self.via_links:
            for owner in link.owner_ids:
                key = owner.casefold()
                previous = base_owners.setdefault(key, link.link_id)
                if previous != link.link_id:
                    raise LayerSurfaceNetworkError(
                        f"physical owner {owner!r} is duplicated in base Via links"
                    )
        for cluster in manifest.clusters:
            for owner in cluster.owner_ids:
                previous = base_owners.get(owner.casefold())
                if previous is not None:
                    raise LayerSurfaceNetworkError(
                        f"termination owner {owner!r} duplicates base link {previous!r}"
                    )
        return tuple(mapped)

    def _evaluated_terminations(
        self,
        frequencies_hz: NDArray[np.float64],
        manifest: CompiledLayerSurfaceTerminationManifest | None,
        selected_rail_id: str | None,
        base_network_identity_sha256: str | None,
    ) -> tuple[
        EvaluatedLayerSurfaceTerminations | None,
        tuple[tuple[int, int, EvaluatedLayerSurfaceTerminationStamp], ...],
    ]:
        if manifest is None:
            if selected_rail_id is not None:
                raise LayerSurfaceNetworkError(
                    "selected rail cannot be supplied without a termination manifest"
                )
            if base_network_identity_sha256 is not None and (
                len(str(base_network_identity_sha256)) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in str(base_network_identity_sha256).casefold()
                )
            ):
                raise LayerSurfaceNetworkError(
                    "base network identity must be a SHA-256 value"
                )
            return None, ()
        if selected_rail_id is None or not str(selected_rail_id).strip():
            raise LayerSurfaceNetworkError(
                "selected_rail_id is required when termination loads are present"
            )
        if base_network_identity_sha256 is None:
            raise LayerSurfaceNetworkError(
                "base network identity is required when termination loads are present"
            )
        physical_to_reduced = self.termination_reduced_node_mapping(manifest)
        try:
            evaluated = manifest.evaluate(
                frequencies_hz,
                selected_rail_id=selected_rail_id,
                base_network_identity_sha256=base_network_identity_sha256,
            )
        except LayerSurfaceTerminationError as exc:
            raise LayerSurfaceNetworkError(str(exc)) from exc
        mapped: list[tuple[int, int, EvaluatedLayerSurfaceTerminationStamp]] = []
        for stamp in evaluated.stamps:
            try:
                positive = physical_to_reduced[stamp.positive_node_index]
                negative = physical_to_reduced[stamp.negative_node_index]
            except IndexError as exc:
                raise LayerSurfaceNetworkError(
                    f"termination cluster {stamp.cluster_id!r} has an invalid surface index"
                ) from exc
            if positive == negative:
                raise LayerSurfaceNetworkError(
                    f"termination cluster {stamp.cluster_id!r} is shorted by ideal topology"
                )
            mapped.append((positive, negative, stamp))
        return evaluated, tuple(mapped)

    def _components_with_terminations(
        self,
        mapped: Sequence[
            tuple[int, int, EvaluatedLayerSurfaceTerminationStamp]
        ],
        active_finite_links: Sequence[
            tuple[int, int, LayerSurfaceViaLink]
        ],
    ) -> tuple[tuple[tuple[int, ...], ...], tuple[int, ...]]:
        if (
            not mapped
            and len(active_finite_links) == len(self._finite_links)
        ):
            return self._components, self._component_by_node
        connectivity = _UnionFind(len(self._reduced_node_ids))
        if len(active_finite_links) == len(self._finite_links):
            for members in self._components:
                anchor = members[0]
                for member in members[1:]:
                    connectivity.union(anchor, member)
        else:
            for matrix in self._collapsed_partials:
                coo = matrix.tocoo()
                for row, column, value in zip(
                    coo.row, coo.col, coo.data, strict=True
                ):
                    if row != column and value != 0.0:
                        connectivity.union(int(row), int(column))
            for first, second, _link in active_finite_links:
                connectivity.union(first, second)
        for positive, negative, _stamp in mapped:
            connectivity.union(positive, negative)
        members_by_root: dict[int, list[int]] = {}
        for node in range(len(self._reduced_node_ids)):
            members_by_root.setdefault(connectivity.find(node), []).append(node)
        components = tuple(
            tuple(members)
            for _root, members in sorted(
                members_by_root.items(), key=lambda item: min(item[1])
            )
        )
        component_by_node = [-1] * len(self._reduced_node_ids)
        for component_index, members in enumerate(components):
            for node in members:
                component_by_node[node] = component_index
        return components, tuple(component_by_node)

    def _resolved_disabled_via_link_ids(
        self,
        disabled_via_link_ids: Sequence[str],
    ) -> tuple[str, ...]:
        if isinstance(disabled_via_link_ids, (str, bytes)):
            raise LayerSurfaceNetworkError(
                "disabled_via_link_ids must be a sequence of exact link IDs"
            )
        requested = tuple(
            _identity(value, name="disabled Via link ID")
            for value in disabled_via_link_ids
        )
        if len(set(requested)) != len(requested):
            raise LayerSurfaceNetworkError(
                "disabled Via link IDs must not contain duplicates"
            )
        link_by_id = {link.link_id: link for link in self.via_links}
        unknown = tuple(sorted(value for value in requested if value not in link_by_id))
        if unknown:
            raise LayerSurfaceNetworkError(
                "disabled Via link IDs are unknown: " + ", ".join(unknown[:8])
            )
        topology_only = tuple(
            sorted(
                value
                for value in requested
                if link_by_id[value].mode != "finite_parallel_rl"
            )
        )
        if topology_only:
            raise LayerSurfaceNetworkError(
                "topology-only Via links cannot be disabled: "
                + ", ".join(topology_only[:8])
            )
        return tuple(sorted(requested))

    def _current_port_snapshot(
        self,
        physical_by_node: Mapping[str, int] | None = None,
    ) -> tuple[tuple[object, ...], ...]:
        if len(self.ports) != len(self._port_reduced_nodes):
            raise LayerSurfaceNetworkError(
                "port metadata and compiled reduced-node inventory disagree"
            )
        physical_by_node = (
            self._surface_node_physical_index()
            if physical_by_node is None
            else physical_by_node
        )
        seen_ids: set[str] = set()
        snapshot: list[tuple[object, ...]] = []
        for index, port in enumerate(self.ports):
            if not isinstance(port, LayerSurfacePort):
                raise LayerSurfaceNetworkError(
                    "port inventory contains a non-layer-surface port"
                )
            port_id = _identity(port.port_id, name="port_id")
            positive_id = _identity(
                port.positive_node_id, name="positive_node_id"
            )
            negative_id = _identity(
                port.negative_node_id, name="negative_node_id"
            )
            if (
                port_id != port.port_id
                or positive_id != port.positive_node_id
                or negative_id != port.negative_node_id
                or port_id in seen_ids
                or positive_id == negative_id
                or positive_id not in physical_by_node
                or negative_id not in physical_by_node
            ):
                raise LayerSurfaceNetworkError(
                    "port metadata is non-canonical, duplicated, or references an unknown/shorted surface"
                )
            seen_ids.add(port_id)
            compiled_pair = tuple(self._port_reduced_nodes[index])
            expected_pair = (
                int(
                    self._surface_to_reduced[
                        physical_by_node[positive_id]
                    ]
                ),
                int(
                    self._surface_to_reduced[
                        physical_by_node[negative_id]
                    ]
                ),
            )
            if (
                len(compiled_pair) != 2
                or tuple(int(value) for value in compiled_pair)
                != expected_pair
                or expected_pair[0] == expected_pair[1]
            ):
                raise LayerSurfaceNetworkError(
                    "live port endpoints disagree with the compiled reduced-node pair"
                )
            snapshot.append(
                (port_id, positive_id, negative_id, expected_pair)
            )
        if not snapshot:
            raise LayerSurfaceNetworkError(
                "one or more unique layer-surface ports are required"
            )
        return tuple(snapshot)

    def _current_via_snapshot(
        self,
        physical_by_node: Mapping[str, int] | None = None,
    ) -> tuple[tuple[object, ...], ...]:
        physical_by_node = (
            self._surface_node_physical_index()
            if physical_by_node is None
            else physical_by_node
        )
        seen_link_ids: set[str] = set()
        seen_owner_ids: set[str] = set()
        snapshot: list[tuple[object, ...]] = []
        expected_finite: list[tuple[int, int, LayerSurfaceViaLink]] = []
        for link in self.via_links:
            if not isinstance(link, LayerSurfaceViaLink):
                raise LayerSurfaceNetworkError(
                    "Via inventory contains a non-layer-surface link"
                )
            link_id = _identity(link.link_id, name="link_id")
            first_id = _identity(link.first_node_id, name="first_node_id")
            second_id = _identity(link.second_node_id, name="second_node_id")
            if (
                link_id != link.link_id
                or first_id != link.first_node_id
                or second_id != link.second_node_id
                or link_id in seen_link_ids
                or first_id == second_id
                or first_id not in physical_by_node
                or second_id not in physical_by_node
                or type(link.owner_ids) is not tuple
            ):
                raise LayerSurfaceNetworkError(
                    "Via metadata is non-canonical, duplicated, or references an unknown/shorted surface"
                )
            seen_link_ids.add(link_id)
            owners = tuple(
                _identity(owner, name="owner_id") for owner in link.owner_ids
            )
            owner_keys = tuple(owner.casefold() for owner in owners)
            if (
                not owners
                or owners != link.owner_ids
                or len(set(owner_keys)) != len(owner_keys)
                or any(owner in seen_owner_ids for owner in owner_keys)
            ):
                raise LayerSurfaceNetworkError(
                    "physical Via owner metadata is empty, non-canonical, or duplicated"
                )
            seen_owner_ids.update(owner_keys)
            first = int(
                self._surface_to_reduced[physical_by_node[first_id]]
            )
            second = int(
                self._surface_to_reduced[physical_by_node[second_id]]
            )
            if link.mode == "finite_parallel_rl":
                resistance = float(link.resistance_ohm_per_via)  # type: ignore[arg-type]
                inductance = float(link.inductance_h_per_via)  # type: ignore[arg-type]
                if (
                    not isinstance(link.count, int)
                    or link.count < 1
                    or not isfinite(resistance)
                    or not isfinite(inductance)
                    or resistance < 0.0
                    or inductance < 0.0
                    or (resistance == 0.0 and inductance == 0.0)
                    or first == second
                ):
                    raise LayerSurfaceNetworkError(
                        "finite Via metadata is invalid or parallel to an ideal path"
                    )
                expected_finite.append((first, second, link))
            elif link.mode == "topology_only_ideal":
                resistance = None
                inductance = None
                if (
                    not isinstance(link.count, int)
                    or link.count < 1
                    or link.resistance_ohm_per_via is not None
                    or link.inductance_h_per_via is not None
                    or first != second
                ):
                    raise LayerSurfaceNetworkError(
                        "topology-only Via metadata disagrees with compiled ideal ownership"
                    )
            else:
                raise LayerSurfaceNetworkError(
                    f"unsupported vertical link mode {link.mode!r}"
                )
            snapshot.append(
                (
                    link_id,
                    link.mode,
                    first_id,
                    second_id,
                    owners,
                    int(link.count),
                    resistance,
                    inductance,
                    first,
                    second,
                )
            )
        if len(expected_finite) != len(self._finite_links):
            raise LayerSurfaceNetworkError(
                "live finite Via inventory disagrees with compiled links"
            )
        for expected, compiled in zip(
            expected_finite, self._finite_links, strict=True
        ):
            if (
                int(compiled[0]) != expected[0]
                or int(compiled[1]) != expected[1]
                or compiled[2] is not expected[2]
            ):
                raise LayerSurfaceNetworkError(
                    "live finite Via metadata disagrees with compiled endpoints"
                )
        return tuple(snapshot)

    def _current_dispersion_snapshot(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                item.partial.upper_layer,
                item.partial.lower_layer,
                float(item.partial.nominal_relative_permittivity),
                tuple(item.partial.net_names),
                (
                    None
                    if item.partial.separation_m is None
                    else float(item.partial.separation_m)
                ),
                _canonical_dispersion_snapshot(item.dispersion),
            )
            for item in self.partials
        )

    def _frequency_result_cache_binding(
        self,
        *,
        termination_manifest: CompiledLayerSurfaceTerminationManifest | None,
        base_network_identity_sha256: str | None,
        disabled_via_link_ids: tuple[str, ...],
        finite_stamp_sha256: str,
    ) -> _FrequencyResultCacheBinding | None:
        """Build a strong, auditable identity for point-result reuse.

        Compatibility objects with mutable manifest inventories do not
        qualify.  A stale/tampered production manifest fails closed rather
        than silently turning a cache miss into a solve under a false SHA.
        """

        tuple_inventories = (
            self.surface_node_ids,
            self.partials,
            self.via_links,
            self.ports,
            self._reduced_node_ids,
            self._collapsed_partials,
            self._finite_links,
            self._components,
            self._component_by_node,
            self._port_reduced_nodes,
        )
        if any(type(item) is not tuple for item in tuple_inventories):
            return None
        if (
            self._surface_to_reduced.dtype != np.dtype(np.int64)
            or not self._surface_to_reduced.flags.c_contiguous
            or not self._surface_to_reduced.flags.owndata
            or self._surface_to_reduced.base is not None
            or self._surface_to_reduced.flags.writeable
            or any(
                matrix.data.flags.writeable
                or matrix.indices.flags.writeable
                or matrix.indptr.flags.writeable
                or not matrix.data.flags.owndata
                or not matrix.indices.flags.owndata
                or not matrix.indptr.flags.owndata
                or matrix.data.base is not None
                or matrix.indices.base is not None
                or matrix.indptr.base is not None
                for matrix in self._collapsed_partials
            )
        ):
            return None

        base_identity = (
            None
            if base_network_identity_sha256 is None
            else str(base_network_identity_sha256).strip().casefold()
        )
        dispersion_snapshot = self._current_dispersion_snapshot()
        port_snapshot = self._current_port_snapshot()
        manifest_sha: str | None = None
        global_node_ids: object | None = None
        clusters: object | None = None
        source_manifest: object | None = None
        cluster_sources: tuple[object, ...] = ()
        cluster_branches: tuple[object, ...] = ()
        branch_models: tuple[object, ...] = ()
        model_identity_sha256s: tuple[str, ...] = ()
        compiled_snapshot: tuple[tuple[object, ...], ...] = ()
        evidence_sha: str | None = None
        if termination_manifest is not None:
            dependencies = self._termination_mapping_cache_dependencies(
                termination_manifest
            )
            if dependencies is None:
                return None
            manifest_sha = dependencies[0]
            global_node_ids = dependencies[1]
            clusters = dependencies[2]
            source_manifest = termination_manifest.source_manifest
            try:
                stored_manifest_sha = sha256(
                    _termination_canonical_json(dict(source_manifest))
                ).hexdigest()
                current_cluster_evidence = [
                    dict(cluster.source.evidence_manifest())
                    for cluster in clusters
                ]
                stored_cluster_evidence = source_manifest["clusters"]
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise LayerSurfaceNetworkError(
                    "termination manifest evidence is invalid"
                ) from exc
            if (
                stored_manifest_sha != manifest_sha
                or current_cluster_evidence != stored_cluster_evidence
            ):
                raise LayerSurfaceNetworkError(
                    "termination manifest evidence changed after compilation"
                )
            evidence_sha = sha256(
                _termination_canonical_json(
                    {"clusters": current_cluster_evidence}
                )
            ).hexdigest()
            cluster_sources = tuple(cluster.source for cluster in clusters)
            if any(
                type(source.branches) is not tuple
                for source in cluster_sources
            ):
                return None
            cluster_branches = tuple(
                source.branches for source in cluster_sources
            )
            branch_models = tuple(
                branch.model
                for branches in cluster_branches
                for branch in branches
            )
            branches_flat = tuple(
                branch
                for branches in cluster_branches
                for branch in branches
            )
            try:
                with scoped_impedance_model_identity_cache():
                    model_identity_sha256s = tuple(
                        impedance_model_identity_sha256(branch.model)
                        for branch in branches_flat
                    )
            except LayerSurfaceTerminationError as exc:
                raise LayerSurfaceNetworkError(str(exc)) from exc
            if any(
                recomputed != branch.model_identity_sha256
                for branch, recomputed in zip(
                    branches_flat,
                    model_identity_sha256s,
                    strict=True,
                )
            ):
                raise LayerSurfaceNetworkError(
                    "termination branch model changed after compilation"
                )
            compiled_snapshot = tuple(
                (
                    cluster.rail_id,
                    int(cluster.positive_global_index),
                    int(cluster.negative_global_index),
                    tuple(cluster.local_node_ids),
                    int(cluster.positive_local_index),
                    int(cluster.negative_local_index),
                    tuple(cluster.branch_local_indices),
                    cluster.direct_retained_branch_index,
                    tuple(cluster.owner_ids),
                )
                for cluster in clusters
            )

        state_identity = _identity_sha256(
            {
                "schema": "layer-surface-frequency-result-state-v1",
                "base_network_identity_sha256": base_identity,
                "disabled_via_link_ids": list(disabled_via_link_ids),
                "finite_stamp_sha256": finite_stamp_sha256,
                "port_snapshot": port_snapshot,
                "dispersion_snapshot": dispersion_snapshot,
                "termination_manifest_sha256": manifest_sha,
                "termination_evidence_sha256": evidence_sha,
                "termination_model_identity_sha256s": model_identity_sha256s,
                "termination_compiled_snapshot": compiled_snapshot,
            }
        )
        return _FrequencyResultCacheBinding(
            state_identity_sha256=state_identity,
            base_network_identity_sha256=base_identity,
            disabled_via_link_ids=disabled_via_link_ids,
            surface_node_ids=self.surface_node_ids,
            partials=self.partials,
            via_links=self.via_links,
            ports=self.ports,
            reduced_node_ids=self._reduced_node_ids,
            surface_to_reduced=self._surface_to_reduced,
            collapsed_partials=self._collapsed_partials,
            finite_links=self._finite_links,
            components=self._components,
            component_by_node=self._component_by_node,
            port_reduced_nodes=self._port_reduced_nodes,
            port_snapshot=port_snapshot,
            dispersion_snapshot=dispersion_snapshot,
            finite_stamp_sha256=finite_stamp_sha256,
            termination_manifest=termination_manifest,
            termination_manifest_sha256=manifest_sha,
            termination_global_node_ids=global_node_ids,
            termination_clusters=clusters,
            termination_source_manifest=source_manifest,
            termination_cluster_sources=cluster_sources,
            termination_cluster_branches=cluster_branches,
            termination_branch_models=branch_models,
            termination_model_identity_sha256s=model_identity_sha256s,
            termination_compiled_snapshot=compiled_snapshot,
            termination_evidence_sha256=evidence_sha,
        )

    def _frequency_result_cache_binding_matches(
        self,
        cached: _FrequencyResultCacheBinding,
        current: _FrequencyResultCacheBinding,
    ) -> bool:
        scalar_fields_match = (
            cached.state_identity_sha256 == current.state_identity_sha256
            and cached.base_network_identity_sha256
            == current.base_network_identity_sha256
            and cached.disabled_via_link_ids == current.disabled_via_link_ids
            and cached.port_snapshot == current.port_snapshot
            and cached.dispersion_snapshot == current.dispersion_snapshot
            and cached.finite_stamp_sha256 == current.finite_stamp_sha256
            and cached.termination_manifest_sha256
            == current.termination_manifest_sha256
            and cached.termination_evidence_sha256
            == current.termination_evidence_sha256
            and cached.termination_model_identity_sha256s
            == current.termination_model_identity_sha256s
            and cached.termination_compiled_snapshot
            == current.termination_compiled_snapshot
        )
        if not scalar_fields_match:
            return False
        exact_objects = (
            "surface_node_ids",
            "partials",
            "via_links",
            "ports",
            "reduced_node_ids",
            "surface_to_reduced",
            "collapsed_partials",
            "finite_links",
            "components",
            "component_by_node",
            "port_reduced_nodes",
            "termination_manifest",
            "termination_global_node_ids",
            "termination_clusters",
            "termination_source_manifest",
        )
        if any(
            getattr(cached, name) is not getattr(current, name)
            for name in exact_objects
        ):
            return False
        identity_sequences = (
            (
                cached.termination_cluster_sources,
                current.termination_cluster_sources,
            ),
            (
                cached.termination_cluster_branches,
                current.termination_cluster_branches,
            ),
            (
                cached.termination_branch_models,
                current.termination_branch_models,
            ),
        )
        if any(
            len(cached_items) != len(current_items)
            for cached_items, current_items in identity_sequences
        ):
            return False
        return all(
            left is right
            for cached_items, current_items in identity_sequences
            for left, right in zip(cached_items, current_items, strict=True)
        )

    def _frequency_result_cache_get(
        self,
        binding: _FrequencyResultCacheBinding | None,
        point_cache_identity: bytes,
    ) -> _FrequencySolveResult | None:
        if binding is None:
            return None
        key = (binding.state_identity_sha256, point_cache_identity)
        with self._frequency_result_cache_lock:
            canonical = self._frequency_result_cache_bindings.get(
                binding.state_identity_sha256
            )
            if canonical is not None and not self._frequency_result_cache_binding_matches(
                canonical, binding
            ):
                self._frequency_result_cache_evict_state_locked(
                    binding.state_identity_sha256
                )
                return None
            entry = self._frequency_result_cache.get(key)
            if entry is None:
                return None
            result = entry.result
            payload_valid = (
                entry.point_cache_identity == point_cache_identity
                and result.point_cache_identity == point_cache_identity
                and len(point_cache_identity) == 40
                and type(result.admittance_by_port_index) is tuple
                and len(result.admittance_by_port_index)
                == len(binding.port_snapshot)
                and all(
                    isfinite(value.real) and isfinite(value.imag)
                    for value in result.admittance_by_port_index
                )
                and isfinite(result.maximum_factor_pivot_ratio)
                and result.maximum_factor_pivot_ratio >= 1.0
                and result.maximum_factor_pivot_ratio <= _MAX_FACTOR_PIVOT_RATIO
                and isfinite(result.maximum_relative_residual)
                and 0.0 <= result.maximum_relative_residual
                <= _RESIDUAL_REL_LIMIT
                and isinstance(result.maximum_port_rhs_batch_size, int)
                and 1 <= result.maximum_port_rhs_batch_size
                <= min(_PORT_RHS_BATCH_SIZE, len(binding.port_snapshot))
                and entry.estimated_size_bytes
                == _frequency_result_cache_entry_size(
                    point_cache_identity, result
                )
            )
            if (
                canonical is None
                or entry.binding is not canonical
                or not self._frequency_result_cache_binding_matches(
                    entry.binding, binding
                )
                or not payload_valid
            ):
                self._frequency_result_cache_evict_state_locked(
                    binding.state_identity_sha256
                )
                return None
            self._frequency_result_cache.move_to_end(key)
            self._frequency_result_cache_bindings.move_to_end(
                binding.state_identity_sha256
            )
            return entry.result

    def _frequency_result_cache_remove_entry_locked(
        self,
        key: tuple[str, bytes],
    ) -> None:
        entry = self._frequency_result_cache.pop(key, None)
        if entry is None:
            return
        state_identity = key[0]
        total_bytes = max(
            0,
            self._frequency_result_cache_bytes - entry.estimated_size_bytes,
        )
        remaining = self._frequency_result_cache_state_counts.get(
            state_identity, 0
        ) - 1
        if remaining <= 0:
            self._frequency_result_cache_state_counts.pop(
                state_identity, None
            )
            self._frequency_result_cache_bindings.pop(state_identity, None)
            total_bytes = max(
                0,
                total_bytes
                - self._frequency_result_cache_binding_bytes.pop(
                    state_identity, 0
                ),
            )
        else:
            self._frequency_result_cache_state_counts[state_identity] = remaining
        object.__setattr__(self, "_frequency_result_cache_bytes", total_bytes)

    def _frequency_result_cache_evict_state_locked(
        self,
        state_identity: str,
    ) -> None:
        for key in tuple(self._frequency_result_cache):
            if key[0] == state_identity:
                self._frequency_result_cache_remove_entry_locked(key)
        # A zero-result binding should not normally exist, but keep repair
        # deterministic if a prior interrupted insertion left one behind.
        if state_identity in self._frequency_result_cache_bindings:
            self._frequency_result_cache_bindings.pop(state_identity, None)
            binding_bytes = self._frequency_result_cache_binding_bytes.pop(
                state_identity, 0
            )
            self._frequency_result_cache_state_counts.pop(state_identity, None)
            object.__setattr__(
                self,
                "_frequency_result_cache_bytes",
                max(0, self._frequency_result_cache_bytes - binding_bytes),
            )

    def _frequency_result_cache_put_many(
        self,
        binding: _FrequencyResultCacheBinding | None,
        results: Sequence[tuple[bytes, _FrequencySolveResult]],
    ) -> None:
        if binding is None or not results:
            return
        binding_size = _frequency_result_cache_binding_size(binding)
        prepared_entries = tuple(
            _FrequencyResultCacheEntry(
                binding=binding,
                point_cache_identity=point_cache_identity,
                result=result,
                estimated_size_bytes=_frequency_result_cache_entry_size(
                    point_cache_identity, result
                ),
            )
            for point_cache_identity, result in results
        )
        if (
            _FREQUENCY_RESULT_CACHE_ENTRY_LIMIT < 1
            or _FREQUENCY_RESULT_CACHE_STATE_LIMIT < 1
            or binding_size >= _FREQUENCY_RESULT_CACHE_BYTE_LIMIT
        ):
            return
        prepared_entries = tuple(
            entry
            for entry in prepared_entries
            if binding_size + entry.estimated_size_bytes
            <= _FREQUENCY_RESULT_CACHE_BYTE_LIMIT
        )
        if not prepared_entries:
            return
        with self._frequency_result_cache_lock:
            state_identity = binding.state_identity_sha256
            canonical = self._frequency_result_cache_bindings.get(state_identity)
            if canonical is not None and not self._frequency_result_cache_binding_matches(
                canonical, binding
            ):
                self._frequency_result_cache_evict_state_locked(state_identity)
                canonical = None
            if canonical is None:
                while (
                    len(self._frequency_result_cache_bindings)
                    >= _FREQUENCY_RESULT_CACHE_STATE_LIMIT
                ):
                    oldest_state = next(
                        iter(self._frequency_result_cache_bindings)
                    )
                    self._frequency_result_cache_evict_state_locked(
                        oldest_state
                    )
                canonical = binding
                self._frequency_result_cache_bindings[state_identity] = canonical
                self._frequency_result_cache_state_counts[state_identity] = 0
                self._frequency_result_cache_binding_bytes[
                    state_identity
                ] = binding_size
                object.__setattr__(
                    self,
                    "_frequency_result_cache_bytes",
                    self._frequency_result_cache_bytes + binding_size,
                )
            else:
                self._frequency_result_cache_bindings.move_to_end(state_identity)
            for prepared in prepared_entries:
                entry = _FrequencyResultCacheEntry(
                    binding=canonical,
                    point_cache_identity=prepared.point_cache_identity,
                    result=prepared.result,
                    estimated_size_bytes=prepared.estimated_size_bytes,
                )
                key = (state_identity, entry.point_cache_identity)
                previous = self._frequency_result_cache.pop(key, None)
                if previous is not None:
                    object.__setattr__(
                        self,
                        "_frequency_result_cache_bytes",
                        max(
                            0,
                            self._frequency_result_cache_bytes
                            - previous.estimated_size_bytes,
                        ),
                    )
                else:
                    self._frequency_result_cache_state_counts[state_identity] += 1
                self._frequency_result_cache[key] = entry
                object.__setattr__(
                    self,
                    "_frequency_result_cache_bytes",
                    self._frequency_result_cache_bytes
                    + entry.estimated_size_bytes,
                )
                while self._frequency_result_cache and (
                    len(self._frequency_result_cache)
                    > _FREQUENCY_RESULT_CACHE_ENTRY_LIMIT
                    or self._frequency_result_cache_bytes
                    > _FREQUENCY_RESULT_CACHE_BYTE_LIMIT
                ):
                    oldest_key = next(iter(self._frequency_result_cache))
                    self._frequency_result_cache_remove_entry_locked(oldest_key)

    def solve(
        self,
        frequencies_hz: Sequence[float] | NDArray[np.float64],
        *,
        disabled_via_link_ids: Sequence[str] = (),
        termination_manifest: CompiledLayerSurfaceTerminationManifest | None = None,
        selected_rail_id: str | None = None,
        base_network_identity_sha256: str | None = None,
        progress: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> LayerSurfaceSolveResult:
        # Detach from a caller-owned ndarray before the first validation,
        # interpolation, identity hash, or worker dispatch.  Otherwise a
        # concurrent writer could make the reported grid disagree with the
        # frequency at which an already-running factor was assembled and could
        # poison exact-frequency cache identity.  Canonical little-endian bits
        # are retained throughout this solve and never exposed writable.
        frequencies = np.array(
            frequencies_hz,
            dtype="<f8",
            order="C",
            copy=True,
        )
        frequencies.setflags(write=False)
        if (
            frequencies.ndim != 1
            or not frequencies.size
            or not np.all(np.isfinite(frequencies))
            or np.any(frequencies <= 0.0)
        ):
            raise LayerSurfaceNetworkError("frequencies must be finite and positive")
        report = progress or (lambda _completed, _total: None)
        cancelled = is_cancelled or (lambda: False)
        if cancelled():
            raise RuntimeError("layer-surface solve cancelled")
        solve_port_snapshot = self._current_port_snapshot()
        solve_port_ids = tuple(str(item[0]) for item in solve_port_snapshot)
        solve_port_reduced_nodes = tuple(
            tuple(int(value) for value in item[3])
            for item in solve_port_snapshot
        )
        if solve_port_snapshot != self._compiled_port_snapshot:
            raise LayerSurfaceNetworkError(
                "live port metadata differs from the compiled network"
            )
        solve_via_snapshot = self._current_via_snapshot()
        if solve_via_snapshot != self._compiled_via_snapshot:
            raise LayerSurfaceNetworkError(
                "live Via metadata differs from the compiled network"
            )
        solve_dispersion_snapshot = self._current_dispersion_snapshot()
        if solve_dispersion_snapshot != self._compiled_dispersion_snapshot:
            raise LayerSurfaceNetworkError(
                "live dielectric metadata differs from the compiled network"
            )

        disabled_ids = self._resolved_disabled_via_link_ids(
            disabled_via_link_ids
        )
        if disabled_ids and base_network_identity_sha256 is None:
            raise LayerSurfaceNetworkError(
                "finite-link suppression requires a hash-bound base network identity"
            )
        disabled_set = frozenset(disabled_ids)
        active_finite_links = (
            self._finite_links
            if not disabled_set
            else tuple(
                item
                for item in self._finite_links
                if item[2].link_id not in disabled_set
            )
        )
        disabled_ids_sha256 = _identity_sha256(
            {
                "schema": "layer-surface-disabled-via-links-v1",
                "disabled_via_link_ids": list(disabled_ids),
            }
        )
        via_snapshot_sha256 = _identity_sha256(
            {
                "schema": "layer-surface-via-snapshot-v1",
                "via_snapshot": solve_via_snapshot,
            }
        )
        frequency_cache_binding = self._frequency_result_cache_binding(
            termination_manifest=termination_manifest,
            base_network_identity_sha256=base_network_identity_sha256,
            disabled_via_link_ids=disabled_ids,
            finite_stamp_sha256=via_snapshot_sha256,
        )
        if (
            frequency_cache_binding is not None
            and (
                frequency_cache_binding.port_snapshot != solve_port_snapshot
                or frequency_cache_binding.dispersion_snapshot
                != solve_dispersion_snapshot
            )
        ):
            raise LayerSurfaceNetworkError(
                "network metadata changed during cache-state capture"
            )

        dispersion_values: list[NDArray[np.complex128]] = []
        for partial in self.partials:
            if cancelled():
                raise RuntimeError("layer-surface solve cancelled")
            dk, df = partial.dispersion.interpolate(frequencies)
            if np.any(df < -1.0e-12):
                raise LayerSurfaceNetworkError("dielectric loss tangent is non-passive")
            ratio = dk * (1.0 - 1j * np.maximum(df, 0.0)) / float(
                partial.partial.nominal_relative_permittivity
            )
            dispersion_values.append(1j * 2.0 * np.pi * frequencies * ratio)

        evaluated_terminations, mapped_terminations = self._evaluated_terminations(
            frequencies,
            termination_manifest,
            selected_rail_id,
            base_network_identity_sha256,
        )
        solve_components, solve_component_by_node = (
            self._components_with_terminations(
                mapped_terminations,
                active_finite_links,
            )
        )

        values = {
            port_id: np.empty(frequencies.size, dtype=np.complex128)
            for port_id in solve_port_ids
        }
        ports_by_component: dict[int, list[int]] = {}
        for port_index, (positive, negative) in enumerate(
            solve_port_reduced_nodes
        ):
            component = solve_component_by_node[positive]
            if component != solve_component_by_node[negative]:
                raise LayerSurfaceNetworkError(
                    f"port {solve_port_ids[port_index]!r} spans disconnected source components"
                )
            ports_by_component.setdefault(component, []).append(port_index)

        # Open measurement ports are the only boundaries of this solve.  A
        # connected component with no port has no path into any requested
        # driving-point impedance, even when mounted terminations are present.
        # Restricting assembly to the union of port-bearing components is
        # therefore an exact block-diagonal prune, not a model approximation.
        total_node_count = len(self._reduced_node_ids)
        active_global_nodes = np.asarray(
            sorted(
                node
                for component_index in ports_by_component
                for node in solve_components[component_index]
            ),
            dtype=np.int64,
        )
        if (
            active_global_nodes.size == 0
            or np.unique(active_global_nodes).size != active_global_nodes.size
        ):
            raise LayerSurfaceNetworkError(
                "port-bearing component partition is empty or overlaps"
            )
        global_to_active = np.full(total_node_count, -1, dtype=np.int64)
        global_to_active[active_global_nodes] = np.arange(
            active_global_nodes.size, dtype=np.int64
        )
        active_node_count = int(active_global_nodes.size)
        if active_node_count == total_node_count:
            active_partials = self._collapsed_partials
        else:
            active_partials = tuple(
                _readonly_canonical_csc(
                    csc_matrix(
                        partial[active_global_nodes, :][:, active_global_nodes]
                    )
                )
                for partial in self._collapsed_partials
            )

        active_component_members: dict[int, NDArray[np.int64]] = {}
        for component_index in ports_by_component:
            members = global_to_active[
                np.asarray(solve_components[component_index], dtype=np.int64)
            ]
            if np.any(members < 0):
                raise LayerSurfaceNetworkError(
                    "a port-bearing component was partially pruned"
                )
            members.setflags(write=False)
            active_component_members[component_index] = members

        # Convert the large finite-link ledger to compact numerical arrays
        # once per solve.  The immutable LayerSurfaceViaLink rows (and every
        # owner ID) remain intact for audit/scenario cut semantics; only the
        # frequency stamp is aggregated by sparse endpoint coordinates.
        finite_link_count = len(active_finite_links)
        if finite_link_count:
            finite_first = global_to_active[
                np.fromiter(
                    (item[0] for item in active_finite_links),
                    dtype=np.int64,
                    count=finite_link_count,
                )
            ]
            finite_second = global_to_active[
                np.fromiter(
                    (item[1] for item in active_finite_links),
                    dtype=np.int64,
                    count=finite_link_count,
                )
            ]
            finite_retained = (finite_first >= 0) & (finite_second >= 0)
            if np.any((finite_first >= 0) != (finite_second >= 0)):
                raise LayerSurfaceNetworkError(
                    "a finite link crosses a pruned connected-component boundary"
                )
            retained_link_count = int(np.count_nonzero(finite_retained))
            finite_first = finite_first[finite_retained]
            finite_second = finite_second[finite_retained]

            def retained_link_values(
                getter: Callable[[LayerSurfaceViaLink], float],
            ) -> NDArray[np.float64]:
                return np.fromiter(
                    (
                        getter(item[2])
                        for item, retained in zip(
                            active_finite_links, finite_retained, strict=True
                        )
                        if retained
                    ),
                    dtype=np.float64,
                    count=retained_link_count,
                )

            finite_counts = retained_link_values(lambda link: float(link.count))
            finite_resistance = retained_link_values(
                lambda link: float(link.resistance_ohm_per_via)
            )
            finite_inductance = retained_link_values(
                lambda link: float(link.inductance_h_per_via)
            )
        else:
            finite_first = np.empty(0, dtype=np.int64)
            finite_second = np.empty(0, dtype=np.int64)
            finite_counts = np.empty(0, dtype=np.float64)
            finite_resistance = np.empty(0, dtype=np.float64)
            finite_inductance = np.empty(0, dtype=np.float64)

        for shared_values in (
            active_global_nodes,
            global_to_active,
            finite_first,
            finite_second,
            finite_counts,
            finite_resistance,
            finite_inductance,
        ):
            shared_values.setflags(write=False)

        active_mapped_terminations: list[
            tuple[int, int, EvaluatedLayerSurfaceTerminationStamp]
        ] = []
        for positive, negative, stamp in mapped_terminations:
            active_positive = int(global_to_active[positive])
            active_negative = int(global_to_active[negative])
            if (active_positive >= 0) != (active_negative >= 0):
                raise LayerSurfaceNetworkError(
                    "a termination crosses a pruned connected-component boundary"
                )
            if active_positive >= 0:
                active_mapped_terminations.append(
                    (active_positive, active_negative, stamp)
                )

        immutable_component_ports = tuple(
            (component_index, tuple(port_indices))
            for component_index, port_indices in ports_by_component.items()
        )
        immutable_component_members = MappingProxyType(
            dict(active_component_members)
        )
        immutable_terminations = tuple(active_mapped_terminations)
        frequency_bits_by_index = tuple(
            frequencies[index : index + 1].tobytes(order="C")
            for index in range(int(frequencies.size))
        )
        node_count = active_node_count
        stop_requested = Event()

        def solve_frequency_reserved(
            frequency_index: int,
        ) -> _FrequencySolveResult:
            if stop_requested.is_set():
                raise RuntimeError("layer-surface solve cancelled")
            frequency = float(frequencies[frequency_index])
            matrix = csc_matrix((node_count, node_count), dtype=np.complex128)
            for scale, partial in zip(
                dispersion_values, active_partials, strict=True
            ):
                if partial.nnz:
                    matrix = matrix + scale[frequency_index] * partial
            omega = 2.0 * np.pi * frequency
            if finite_counts.size:
                finite_admittance = finite_counts / (
                    finite_resistance + 1j * omega * finite_inductance
                )
                if not np.all(np.isfinite(finite_admittance)):
                    raise LayerSurfaceNetworkError(
                        "finite via links produced a non-finite admittance"
                    )
                # One directed sparse entry per physical quotient edge is
                # enough: duplicate/parallel endpoint rows are summed exactly
                # by CSC, then converted to a symmetric graph Laplacian.  This
                # avoids four Python objects per edge per frequency while
                # preserving every owner's frequency-dependent R+jwL term.
                off_diagonal = csc_matrix(
                    (finite_admittance, (finite_first, finite_second)),
                    shape=(node_count, node_count),
                )
                off_diagonal.sum_duplicates()
                diagonal = np.asarray(
                    off_diagonal.sum(axis=0) + off_diagonal.sum(axis=1).T
                ).ravel()
                matrix = (
                    matrix
                    + diags(diagonal, offsets=0, shape=matrix.shape, format="csc")
                    - off_diagonal
                    - off_diagonal.T
                )
            if immutable_terminations:
                rows = []
                columns = []
                data = []
                for positive, negative, stamp in immutable_terminations:
                    admittance = complex(stamp.admittance_s[frequency_index])
                    rows.extend((positive, positive, negative, negative))
                    columns.extend((positive, negative, positive, negative))
                    data.extend(
                        (admittance, -admittance, -admittance, admittance)
                    )
                matrix = matrix + csc_matrix(
                    (data, (rows, columns)), shape=(node_count, node_count)
                )
            matrix.sum_duplicates()
            matrix.eliminate_zeros()
            if not np.all(np.isfinite(matrix.data)):
                raise LayerSurfaceNetworkError("assembled layer network is non-finite")
            difference = matrix - matrix.T
            symmetry_error = (
                float(np.max(np.abs(difference.data), initial=0.0))
                if difference.nnz
                else 0.0
            )
            scale = max(float(np.max(np.abs(matrix.data), initial=0.0)), _TINY)
            if symmetry_error > max(1.0e-18, scale * _SYMMETRY_REL_LIMIT):
                raise LayerSurfaceNetworkError("assembled layer network violates reciprocity")
            row_sum = np.asarray(matrix @ np.ones(node_count, dtype=np.complex128)).ravel()
            if float(np.max(np.abs(row_sum), initial=0.0)) > max(
                1.0e-18, scale * _ROW_SUM_REL_LIMIT
            ):
                raise LayerSurfaceNetworkError("assembled layer network row-sum gate failed")

            matrix.sort_indices()
            matrix_identity = sha256(
                b"layer-surface-frequency-matrix-v1\0"
            )
            matrix_identity.update(frequency_bits_by_index[frequency_index])
            matrix_identity.update(
                np.asarray(matrix.shape, dtype="<i8").tobytes(order="C")
            )
            matrix_identity.update(
                np.asarray(active_global_nodes, dtype="<i8").tobytes(order="C")
            )
            matrix_identity.update(
                np.asarray(matrix.indptr, dtype="<i8").tobytes(order="C")
            )
            matrix_identity.update(
                np.asarray(matrix.indices, dtype="<i8").tobytes(order="C")
            )
            matrix_identity.update(
                np.asarray(matrix.data, dtype="<c16").tobytes(order="C")
            )
            point_cache_identity = (
                frequency_bits_by_index[frequency_index]
                + matrix_identity.digest()
            )
            cached = self._frequency_result_cache_get(
                frequency_cache_binding,
                point_cache_identity,
            )
            if cached is not None:
                return cached

            frequency_pivot_ratio = 1.0
            frequency_residual = 0.0
            frequency_rhs_batch_size = 0
            frequency_values: list[complex | None] = [None] * len(solve_port_ids)
            for component_index, port_indices in immutable_component_ports:
                if stop_requested.is_set():
                    raise RuntimeError("layer-surface solve cancelled")
                members = immutable_component_members[component_index]
                if members.size < 2:
                    raise LayerSurfaceNetworkError("a selected port component has fewer than two nodes")
                gauge = int(members[0])
                retained = members[1:]
                local = csc_matrix(matrix[retained, :][:, retained])
                try:
                    factor = splu(local)
                except Exception as exc:
                    labels = tuple(
                        self._reduced_node_ids[int(active_global_nodes[index])]
                        for index in members[:4]
                    )
                    raise LayerSurfaceNetworkError(
                        f"layer-network Kron block is singular near {labels!r}"
                    ) from exc
                diagonal = np.abs(factor.U.diagonal())
                if (
                    not diagonal.size
                    or not np.all(np.isfinite(diagonal))
                    or np.any(diagonal <= 0.0)
                ):
                    raise LayerSurfaceNetworkError(
                        "layer-network factor forward-reliability pivots are invalid"
                    )
                u_pivot_abs_min = float(np.min(diagonal))
                u_pivot_abs_max = float(np.max(diagonal))
                component_pivot_ratio = u_pivot_abs_max / u_pivot_abs_min
                frequency_pivot_ratio = max(
                    frequency_pivot_ratio,
                    component_pivot_ratio,
                )
                local_norm = float(np.linalg.norm(local.data))
                for batch_start in range(0, len(port_indices), _PORT_RHS_BATCH_SIZE):
                    if stop_requested.is_set():
                        raise RuntimeError("layer-surface solve cancelled")
                    batch_indices = port_indices[
                        batch_start : batch_start + _PORT_RHS_BATCH_SIZE
                    ]
                    frequency_rhs_batch_size = max(
                        frequency_rhs_batch_size, len(batch_indices)
                    )
                    rhs = np.zeros(
                        (retained.size, len(batch_indices)), dtype=np.complex128
                    )
                    incidence: list[tuple[int, int, int | None, int | None]] = []
                    for column, port_index in enumerate(batch_indices):
                        positive_global, negative_global = solve_port_reduced_nodes[
                            port_index
                        ]
                        positive = int(global_to_active[positive_global])
                        negative = int(global_to_active[negative_global])
                        if positive < 0 or negative < 0:
                            raise LayerSurfaceNetworkError(
                                "a requested port was pruned from its component"
                            )

                        def retained_position(node: int) -> int | None:
                            if node == gauge:
                                return None
                            position = int(np.searchsorted(retained, node))
                            if (
                                position >= retained.size
                                or int(retained[position]) != node
                            ):
                                raise LayerSurfaceNetworkError(
                                    "a requested port is absent from its Kron block"
                                )
                            return position

                        positive_position = retained_position(positive)
                        negative_position = retained_position(negative)
                        incidence.append(
                            (
                                positive,
                                negative,
                                positive_position,
                                negative_position,
                            )
                        )
                        if positive_position is not None:
                            rhs[positive_position, column] += 1.0
                        if negative_position is not None:
                            rhs[negative_position, column] -= 1.0
                    solution = np.asarray(
                        factor.solve(rhs), dtype=np.complex128
                    )
                    residual = local @ solution - rhs
                    residual_norms = np.linalg.norm(residual, axis=0)
                    solution_norms = np.linalg.norm(solution, axis=0)
                    rhs_norms = np.linalg.norm(rhs, axis=0)
                    relative_residuals = residual_norms / np.maximum(
                        local_norm * solution_norms + rhs_norms,
                        _TINY,
                    )
                    relative_residual = float(
                        np.max(relative_residuals)
                    )
                    frequency_residual = max(
                        frequency_residual, relative_residual
                    )
                    if (
                        not isfinite(relative_residual)
                        or relative_residual > _RESIDUAL_REL_LIMIT
                    ):
                        raise LayerSurfaceNetworkError(
                            "layer-network Kron solve residual is excessive "
                            f"({relative_residual:.3e})"
                        )
                    if frequency_pivot_ratio > _MAX_FACTOR_PIVOT_RATIO:
                        local_abs = np.abs(local.data)
                        nonzero_local_abs = local_abs[local_abs > 0.0]
                        local_abs_min = (
                            float(np.min(nonzero_local_abs))
                            if nonzero_local_abs.size
                            else 0.0
                        )
                        local_abs_max = (
                            float(np.max(nonzero_local_abs))
                            if nonzero_local_abs.size
                            else 0.0
                        )
                        inverse_one_norm_lower_bound = "unavailable"
                        condition_1_lower_bound = "unavailable"
                        try:
                            matrix_one_norm = float(sparse_norm(local, ord=1))
                            inverse = LinearOperator(
                                local.shape,
                                matvec=lambda x: factor.solve(x),
                                rmatvec=lambda x: factor.solve(x, trans="H"),
                                dtype=np.complex128,
                            )
                            inverse_one_norm = float(
                                onenormest(inverse, t=1, itmax=5)
                            )
                            condition_1 = matrix_one_norm * inverse_one_norm
                            if (
                                not isfinite(matrix_one_norm)
                                or matrix_one_norm <= 0.0
                                or not isfinite(inverse_one_norm)
                                or inverse_one_norm <= 0.0
                                or not isfinite(condition_1)
                                or condition_1 <= 0.0
                            ):
                                raise ValueError("invalid sparse condition estimate")
                            inverse_one_norm_lower_bound = f"{inverse_one_norm:.3e}"
                            condition_1_lower_bound = f"{condition_1:.3e}"
                        except Exception:
                            pass
                        raise LayerSurfaceNetworkError(
                            "layer-network factor forward-reliability pivot ratio is excessive "
                            f"({frequency_pivot_ratio:.3e}; "
                            f"frequency_hz={frequency:.9g}, "
                            f"component_index={component_index}, "
                            f"retained_nodes={retained.size}, "
                            f"local_nnz={local.nnz}, "
                            f"local_abs_min={local_abs_min:.3e}, "
                            f"local_abs_max={local_abs_max:.3e}, "
                            f"u_pivot_abs_min={u_pivot_abs_min:.3e}, "
                            f"u_pivot_abs_max={u_pivot_abs_max:.3e}, "
                            f"pivot_ratio={component_pivot_ratio:.3e}, "
                            f"backward_residual={relative_residual:.3e}, "
                            f"inverse_one_norm_lower_bound={inverse_one_norm_lower_bound}, "
                            f"condition_1_lower_bound={condition_1_lower_bound}, "
                            f"matrix_sha256={matrix_identity.hexdigest()})"
                        )
                    for column, (port_index, endpoints) in enumerate(
                        zip(batch_indices, incidence, strict=True)
                    ):
                        (
                            _positive,
                            _negative,
                            positive_position,
                            negative_position,
                        ) = endpoints
                        positive_v = (
                            0.0j
                            if positive_position is None
                            else solution[positive_position, column]
                        )
                        negative_v = (
                            0.0j
                            if negative_position is None
                            else solution[negative_position, column]
                        )
                        impedance = positive_v - negative_v
                        if (
                            not isfinite(impedance.real)
                            or not isfinite(impedance.imag)
                            or abs(impedance) <= _TINY
                        ):
                            raise LayerSurfaceNetworkError(
                                f"port {solve_port_ids[port_index]!r} produced invalid driving impedance"
                            )
                        admittance = 1.0 / impedance
                        tolerance = max(abs(admittance), _TINY) * 1.0e-9
                        if admittance.real < -tolerance:
                            raise LayerSurfaceNetworkError(
                                f"port {solve_port_ids[port_index]!r} produced active admittance"
                            )
                        frequency_values[port_index] = admittance
            if any(value is None for value in frequency_values):
                raise LayerSurfaceNetworkError(
                    "one or more requested ports were not solved"
                )
            return _FrequencySolveResult(
                admittance_by_port_index=tuple(
                    complex(value) for value in frequency_values
                ),
                maximum_factor_pivot_ratio=frequency_pivot_ratio,
                maximum_relative_residual=frequency_residual,
                maximum_port_rhs_batch_size=frequency_rhs_batch_size,
                point_cache_identity=point_cache_identity,
            )

        def solve_frequency(frequency_index: int) -> _FrequencySolveResult:
            _acquire_factor_reservation(stop_requested, cancelled)
            try:
                return solve_frequency_reserved(frequency_index)
            finally:
                _release_factor_reservation()

        maximum_pivot_ratio = 1.0
        maximum_residual = 0.0
        maximum_rhs_batch_size = 0
        total_frequency_count = int(frequencies.size)

        def commit_frequency(
            frequency_index: int,
            solved: _FrequencySolveResult,
        ) -> None:
            nonlocal maximum_pivot_ratio, maximum_residual, maximum_rhs_batch_size
            for port_index, admittance in enumerate(
                solved.admittance_by_port_index
            ):
                values[solve_port_ids[port_index]][frequency_index] = admittance
            maximum_pivot_ratio = max(
                maximum_pivot_ratio, solved.maximum_factor_pivot_ratio
            )
            maximum_residual = max(
                maximum_residual, solved.maximum_relative_residual
            )
            maximum_rhs_batch_size = max(
                maximum_rhs_batch_size,
                solved.maximum_port_rhs_batch_size,
            )
            report(frequency_index + 1, total_frequency_count)

        worker_count = _frequency_solve_worker_count(
            total_frequency_count,
            active_node_count,
        )
        completed_frequency_cache_results: list[
            tuple[bytes, _FrequencySolveResult]
        ] = []
        if worker_count <= 1:
            for frequency_index in range(total_frequency_count):
                if cancelled():
                    stop_requested.set()
                    raise RuntimeError("layer-surface solve cancelled")
                solved = solve_frequency(frequency_index)
                completed_frequency_cache_results.append(
                    (solved.point_cache_identity, solved)
                )
                commit_frequency(
                    frequency_index,
                    solved,
                )
        else:
            executor = ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="layer-surface-frequency",
            )
            pending: dict[Future[_FrequencySolveResult], int] = {}
            completed: dict[int, _FrequencySolveResult | BaseException] = {}
            next_to_submit = 0
            next_to_commit = 0
            try:
                while next_to_commit < total_frequency_count:
                    if cancelled():
                        stop_requested.set()
                        raise RuntimeError("layer-surface solve cancelled")
                    window_end = min(
                        total_frequency_count,
                        next_to_commit + worker_count,
                    )
                    while next_to_submit < window_end:
                        future = executor.submit(
                            solve_frequency, next_to_submit
                        )
                        pending[future] = next_to_submit
                        next_to_submit += 1
                    while next_to_commit in completed:
                        outcome = completed.pop(next_to_commit)
                        if isinstance(outcome, BaseException):
                            stop_requested.set()
                            raise outcome
                        completed_frequency_cache_results.append(
                            (
                                outcome.point_cache_identity,
                                outcome,
                            )
                        )
                        commit_frequency(next_to_commit, outcome)
                        next_to_commit += 1
                        if cancelled():
                            stop_requested.set()
                            raise RuntimeError("layer-surface solve cancelled")
                    if next_to_commit >= total_frequency_count:
                        break
                    done, _not_done = wait(
                        tuple(pending),
                        timeout=_FREQUENCY_PARALLEL_POLL_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        frequency_index = pending.pop(future)
                        try:
                            completed[frequency_index] = future.result()
                        except BaseException as exc:
                            completed[frequency_index] = exc
            finally:
                stop_requested.set()
                for future in pending:
                    future.cancel()
                executor.shutdown(wait=True, cancel_futures=True)

        if cancelled():
            raise RuntimeError("layer-surface solve cancelled")
        if self._current_port_snapshot() != solve_port_snapshot:
            raise LayerSurfaceNetworkError(
                "port metadata changed during layer-network solve"
            )
        if self._current_via_snapshot() != solve_via_snapshot:
            raise LayerSurfaceNetworkError(
                "Via metadata changed during layer-network solve"
            )
        if self._current_dispersion_snapshot() != solve_dispersion_snapshot:
            raise LayerSurfaceNetworkError(
                "dielectric dispersion changed during layer-network solve"
            )

        base_solve_identity = (
            None
            if evaluated_terminations is None
            else evaluated_terminations.cache_identity_sha256
        )
        solve_identity = base_solve_identity
        if disabled_ids:
            frequency_sha256 = sha256(
                np.asarray(frequencies, dtype="<f8").tobytes(order="C")
            ).hexdigest()
            solve_identity = _identity_sha256(
                {
                    "schema": "layer-surface-solve-with-via-suppression-v1",
                    "base_network_identity_sha256": str(
                        base_network_identity_sha256
                    ).casefold(),
                    "base_solve_identity_sha256": base_solve_identity,
                    "frequency_grid_sha256": frequency_sha256,
                    "disabled_via_link_ids": list(disabled_ids),
                }
            )

        result = LayerSurfaceSolveResult(
            frequencies_hz=frequencies,
            effective_admittance_by_port=values,
            diagnostics=LayerSurfaceSolveDiagnostics(
                physical_surface_count=len(self.surface_node_ids),
                reduced_node_count=len(self._reduced_node_ids),
                structural_component_count=len(solve_components),
                finite_via_link_count=sum(
                    item[1] == "finite_parallel_rl"
                    for item in solve_via_snapshot
                ),
                topology_only_link_count=sum(
                    item[1] == "topology_only_ideal"
                    for item in solve_via_snapshot
                ),
                maximum_factor_pivot_ratio=maximum_pivot_ratio,
                maximum_relative_residual=maximum_residual,
                active_termination_cluster_count=(
                    0
                    if evaluated_terminations is None
                    else evaluated_terminations.diagnostics.active_other_rail_cluster_count
                ),
                excluded_selected_rail_cluster_count=(
                    0
                    if evaluated_terminations is None
                    else evaluated_terminations.diagnostics.excluded_selected_rail_cluster_count
                ),
                active_termination_element_count=(
                    0
                    if evaluated_terminations is None
                    else evaluated_terminations.diagnostics.active_element_count
                ),
                maximum_termination_kron_relative_residual=(
                    0.0
                    if evaluated_terminations is None
                    else evaluated_terminations.diagnostics.maximum_local_kron_relative_residual
                ),
                termination_manifest_sha256=(
                    None
                    if termination_manifest is None
                    else termination_manifest.manifest_sha256
                ),
                solve_identity_sha256=solve_identity,
                disabled_via_link_count=len(disabled_ids),
                disabled_via_link_ids=disabled_ids,
                disabled_via_link_ids_sha256=disabled_ids_sha256,
                active_reduced_node_count=active_node_count,
                pruned_portless_node_count=(
                    total_node_count - active_node_count
                ),
                active_structural_component_count=len(ports_by_component),
                maximum_port_rhs_batch_size=maximum_rhs_batch_size,
            ),
        )
        if (
            completed_frequency_cache_results
            and frequency_cache_binding is not None
        ):
            current_binding = self._frequency_result_cache_binding(
                termination_manifest=termination_manifest,
                base_network_identity_sha256=base_network_identity_sha256,
                disabled_via_link_ids=disabled_ids,
                finite_stamp_sha256=via_snapshot_sha256,
            )
            if (
                current_binding is None
                or not self._frequency_result_cache_binding_matches(
                    frequency_cache_binding, current_binding
                )
            ):
                raise LayerSurfaceNetworkError(
                    "layer-network cache state changed during solve"
                )
            self._frequency_result_cache_put_many(
                current_binding,
                completed_frequency_cache_results,
            )
        return result


def compile_layer_surface_network(
    surface_node_ids: Sequence[str],
    *,
    partials: Sequence[DispersiveAdjacentGap],
    via_links: Sequence[LayerSurfaceViaLink],
    ports: Sequence[LayerSurfacePort],
) -> CompiledLayerSurfaceNetwork:
    """Compile source stamps while collapsing only explicit ideal via links."""

    nodes = tuple(_identity(item, name="surface node ID") for item in surface_node_ids)
    if not nodes or len(set(nodes)) != len(nodes):
        raise LayerSurfaceNetworkError("surface node IDs must be non-empty and unique")
    index = {node: position for position, node in enumerate(nodes)}
    partial_tuple = tuple(partials)
    if not partial_tuple:
        raise LayerSurfaceNetworkError("at least one adjacent-gap partial is required")
    for item in partial_tuple:
        local_names = tuple(
            _identity(name, name="partial surface node ID")
            for name in item.partial.net_names
        )
        if not local_names or len(set(local_names)) != len(local_names):
            raise LayerSurfaceNetworkError(
                "each adjacent-gap partial must name a non-empty unique local surface-node subset"
            )
        unknown = tuple(name for name in local_names if name not in index)
        if unknown:
            raise LayerSurfaceNetworkError(
                "adjacent-gap partial references unknown surface nodes: "
                f"{unknown[:4]!r}"
            )
    links = tuple(via_links)
    if len({item.link_id for item in links}) != len(links):
        raise LayerSurfaceNetworkError("vertical link IDs must be globally unique")
    owner_ledger: set[str] = set()
    for link in links:
        if link.first_node_id not in index or link.second_node_id not in index:
            raise LayerSurfaceNetworkError(
                f"vertical link {link.link_id!r} references an unknown surface node"
            )
        for owner in link.owner_ids:
            if owner in owner_ledger:
                raise LayerSurfaceNetworkError(
                    f"physical via owner {owner!r} is stamped more than once"
                )
            owner_ledger.add(owner)

    union = _UnionFind(len(nodes))
    for link in links:
        if link.mode == "topology_only_ideal":
            union.union(index[link.first_node_id], index[link.second_node_id])
    members_by_root: dict[int, list[int]] = {}
    for position in range(len(nodes)):
        members_by_root.setdefault(union.find(position), []).append(position)
    ordered_members = tuple(
        tuple(values)
        for _root, values in sorted(
            members_by_root.items(), key=lambda item: min(item[1])
        )
    )
    reduced_by_surface = np.empty(len(nodes), dtype=np.int64)
    reduced_names: list[str] = []
    for reduced, members in enumerate(ordered_members):
        for position in members:
            reduced_by_surface[position] = reduced
        reduced_names.append(" = ".join(nodes[position] for position in members))

    collapsed: list[csc_matrix] = []
    reduced_count = len(reduced_names)
    for item in partial_tuple:
        local_names = tuple(item.partial.net_names)
        local_to_reduced = np.asarray(
            [
                int(reduced_by_surface[index[name]])
                for name in local_names
            ],
            dtype=np.int64,
        )
        raw_matrix = item.partial.maxwell_capacitance_f
        # Preserve a sparse island extractor's O(E) representation all the
        # way into the global/reduced topology.  Dense legacy partials remain
        # accepted and are converted only at their already-local dimension.
        source = (
            raw_matrix.tocoo(copy=False)
            if issparse(raw_matrix)
            else csc_matrix(raw_matrix).tocoo(copy=False)
        )
        matrix = csc_matrix(
            (
                source.data,
                (
                    local_to_reduced[source.row],
                    local_to_reduced[source.col],
                ),
            ),
            shape=(reduced_count, reduced_count),
        )
        matrix.sum_duplicates()
        matrix.eliminate_zeros()
        collapsed.append(matrix)

    finite_links: list[tuple[int, int, LayerSurfaceViaLink]] = []
    for link in links:
        if link.mode != "finite_parallel_rl":
            continue
        first = int(reduced_by_surface[index[link.first_node_id]])
        second = int(reduced_by_surface[index[link.second_node_id]])
        if first == second:
            raise LayerSurfaceNetworkError(
                f"finite link {link.link_id!r} is parallel to an ideal ownership path"
            )
        finite_links.append((first, second, link))

    port_tuple = tuple(ports)
    if not port_tuple or len({item.port_id for item in port_tuple}) != len(port_tuple):
        raise LayerSurfaceNetworkError("one or more unique layer-surface ports are required")
    port_nodes: list[tuple[int, int]] = []
    for port in port_tuple:
        if port.positive_node_id not in index or port.negative_node_id not in index:
            raise LayerSurfaceNetworkError(
                f"port {port.port_id!r} references an unknown surface node"
            )
        positive = int(reduced_by_surface[index[port.positive_node_id]])
        negative = int(reduced_by_surface[index[port.negative_node_id]])
        if positive == negative:
            raise LayerSurfaceNetworkError(
                f"port {port.port_id!r} is shorted by ideal vertical topology"
            )
        port_nodes.append((positive, negative))

    connectivity = _UnionFind(reduced_count)
    for matrix in collapsed:
        coo = matrix.tocoo()
        for row, column, value in zip(coo.row, coo.col, coo.data, strict=True):
            if row != column and value != 0.0:
                connectivity.union(int(row), int(column))
    for first, second, _link in finite_links:
        connectivity.union(first, second)
    component_members: dict[int, list[int]] = {}
    for node in range(reduced_count):
        component_members.setdefault(connectivity.find(node), []).append(node)
    components = tuple(
        tuple(items)
        for _root, items in sorted(
            component_members.items(), key=lambda item: min(item[1])
        )
    )
    component_by_node = [-1] * reduced_count
    for component, members in enumerate(components):
        for node in members:
            component_by_node[node] = component
    # Port connectivity is finalized at solve time.  A source-proven mounted
    # termination may be the only passive edge joining two otherwise separate
    # substrate components; rejecting the port here would prevent that exact
    # topology from participating in the gauge/Kron construction.

    frozen_map = reduced_by_surface.copy()
    frozen_map.setflags(write=False)
    return CompiledLayerSurfaceNetwork(
        surface_node_ids=nodes,
        partials=partial_tuple,
        via_links=links,
        ports=port_tuple,
        _reduced_node_ids=tuple(reduced_names),
        _surface_to_reduced=frozen_map,
        _collapsed_partials=tuple(collapsed),
        _finite_links=tuple(finite_links),
        _components=components,
        _component_by_node=tuple(component_by_node),
        _port_reduced_nodes=tuple(port_nodes),
    )


__all__ = [
    "CompiledLayerSurfaceNetwork",
    "LayerSurfaceNetworkError",
    "LayerSurfacePort",
    "LayerSurfaceSolveDiagnostics",
    "LayerSurfaceSolveResult",
    "LayerSurfaceViaLink",
    "compile_layer_surface_network",
]
