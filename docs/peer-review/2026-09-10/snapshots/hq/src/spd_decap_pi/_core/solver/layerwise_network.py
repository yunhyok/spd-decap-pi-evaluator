"""Source-derived artwork-island uniform network for production Evaluation.

Every certified connected artwork island remains an independent physical node.
Exact adjacent-gap sparse Maxwell blocks are embedded on those nodes.  Only a
certified same-layer Trace/artwork equivalence component may ideal-coalesce
islands; ownership-safe substrate Vias remain explicit finite R/L branches.
For the production terminal-complete ``external_device_port`` scope, one global
open-port Schur/Kron reduction is the sole driving-point input.  The legacy
rectangular modal matrix is not prepared and no higher-mode one-port difference
is parallel-stamped.  The older surface-pair bridge remains only for explicit
compatibility/research coverage and retains its documented C00 boundary.

This is intentionally not a claim that arbitrary multiport S matrices may be
multiplied.  Branches and shared internal nodes are assembled in Y, then
reduced.  Touchstone data are never read by this module and no coefficient is
fitted from a reference result.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json
from math import isclose, isfinite
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from spd_decap_pi.canonical_json import concrete_canonical_json_bytes
from spd_decap_pi.surface_certificate_asset import (
    SurfaceCertificateAssetError,
    clear_surface_certificate_hydration_cache,
    hydrate_surface_certificate,
    is_frozen_json_view,
    is_surface_certificate_asset_stub,
    is_surface_certificate_compiled_only_stub,
    is_validation_cache_safe_json_view,
    validate_project_topology_storage_envelope,
)

from .modal import DielectricDispersion
from .multilayer_capacitance import (
    CapacitanceArtwork,
    MultilayerCapacitanceError,
    MultilayerCapacitanceModel,
    capacitance_model_from_project,
    extract_sparse_adjacent_gap_island_capacitance,
)
from .layer_surface_network import (
    CompiledLayerSurfaceNetwork,
    LayerSurfaceNetworkError,
    LayerSurfacePort,
    LayerSurfaceSolveResult,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from .layer_surface_termination import (
    CompiledLayerSurfaceTerminationManifest,
    LayerSurfaceTerminationError,
)
from .finite_via_layerwise import (
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
    CompiledFiniteViaBaseTopology,
    FiniteViaCertificateError,
    compile_finite_via_base_topology,
)
from spd_decap_pi.compiled_topology_asset import (
    COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
    CompiledTopologyAssetError,
    _freeze_owned_compact_certificate_view,
    compact_finite_certificate_views,
    load_compiled_topology_asset,
)
from spd_decap_pi.raw_spatial_contact_asset import (
    RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY,
    RAW_SPATIAL_CONTACT_ASSET_SCHEMA,
    RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3,
    RawSpatialContactAssetError,
    load_raw_spatial_contact_asset,
    validate_project_raw_spatial_contact_asset_envelope,
)
from .profiles import (
    LAYERWISE_ADMITTANCE_PROFILE,
    solver_profile_static_identity_sha256,
)
from .uniform_c00 import (
    DispersiveAdjacentGap,
    UniformC00Assembly,
    UniformPortConnectivityEvidence,
)
from ..via_model import ViaModelError, estimate_via_segment_rl


LAYERWISE_COMPILER_VERSION = (
    "layer-surface-adjacent-y-island-finite-via-termination-kron-v8"
)
_SURFACE_CONNECTIVITY_SCHEMA = "spd-layer-surface-connectivity-v3"
_SURFACE_CONNECTIVITY_COMPILER = (
    "powersi-same-layer-trace-island-terminal-via-pair-v3"
)
_TERMINAL_ISLAND_PROOF_SCHEMA = "layerwise-terminal-island-component-proof-v3"
_V4_EXTERNAL_PORT_PROOF_VIEW_SCHEMA = (
    "spd-layerwise-external-port-proof-view-v1"
)
_V4_COMPACT_VIEW_INTEGRITY = "full-canonical-certificate-verified"
_V4_EXTERNAL_PORT_PROOF_VIEW_KEYS = frozenset(
    {
        "view_schema",
        "integrity_validation",
        "schema_version",
        "compiler_id",
        "source_sha256",
        "evidence_sha256",
        "status",
        "rail_anchor_bindings",
        "terminal_contacts",
        "view_evidence_sha256",
    }
)
_V4_EXTERNAL_PORT_ANCHOR_KEYS = frozenset(
    {"rail_id", "branch_id", "role", "pin_id"}
)
_V4_EXTERNAL_PORT_CONTACT_KEYS = frozenset(
    {
        "pin_id",
        "net",
        "exposed_quotient_vertex_id",
        "status",
        "incident_via_id",
        "first_via_quotient_edge_id",
    }
)
_CACHE_LIMIT = 2
_FREQUENCY_CACHE_LIMIT = 8
_SUBSTRATE_CACHE: "OrderedDict[str, LayerwiseNetworkSubstrate]" = OrderedDict()
_SUBSTRATE_CACHE_LOCK = RLock()
_AttachmentSnapshot = tuple[tuple[str, bytes], ...]
_SUBSTRATE_ATTACHMENT_SNAPSHOTS: dict[str, _AttachmentSnapshot] = {}
_FINITE_TOPOLOGY_CACHE: OrderedDict[
    str,
    tuple[
        CompiledFiniteViaBaseTopology,
        Mapping[str, Any],
        Mapping[str, Any],
    ],
] = OrderedDict()
_FINITE_TOPOLOGY_CACHE_LOCK = RLock()
_FINITE_TOPOLOGY_ATTACHMENT_SNAPSHOTS: dict[str, _AttachmentSnapshot] = {}


@dataclass(frozen=True, slots=True)
class _ValidatedCompactViewEntry:
    """One strongly held immutable view plus every validation dependency."""

    view: object
    binding: tuple[str, ...]


_EXTERNAL_PORT_VIEW_VALIDATION_CACHE: OrderedDict[
    int, _ValidatedCompactViewEntry
] = OrderedDict()
_EXTERNAL_PORT_VIEW_VALIDATION_CACHE_LOCK = RLock()


def _immutable_attachment_snapshot(
    attachments: Mapping[str, bytes],
    *,
    names: Sequence[str] | None = None,
    prefix: str | None = None,
) -> _AttachmentSnapshot | None:
    """Capture exact immutable objects so repeat verification needs no rehash.

    Only concrete ``bytes`` are eligible.  Mutable bytearray/memoryview values
    deliberately return ``None`` and therefore pass through full SHA-256
    validation on every use.
    """

    rows: list[tuple[str, bytes]] = []
    if names is not None:
        for name in sorted(set(names), key=str.casefold):
            content = attachments.get(name)
            if type(content) is not bytes:
                return None
            rows.append((name, content))
        return tuple(rows)
    prefix_key = str(prefix or "").casefold()
    for raw_name, content in attachments.items():
        name = str(raw_name)
        if prefix_key and not name.casefold().startswith(prefix_key):
            continue
        if not isinstance(raw_name, str) or type(content) is not bytes:
            return None
        rows.append((name, content))
    rows.sort(key=lambda item: (item[0].casefold(), item[0]))
    return tuple(rows)


def _same_attachment_snapshot(
    first: _AttachmentSnapshot | None,
    second: _AttachmentSnapshot | None,
) -> bool:
    return bool(
        first is not None
        and second is not None
        and len(first) == len(second)
        and all(
            first_name == second_name and first_content is second_content
            for (first_name, first_content), (second_name, second_content) in zip(
                first, second, strict=True
            )
        )
    )


class LayerwiseNetworkUnavailable(ValueError):
    """Actionable source/compiler failure; there is no silent legacy fallback."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "LAYERWISE_NETWORK_UNAVAILABLE"
        super().__init__(f"Layerwise network unavailable [{self.code}]: {message}")


@dataclass(frozen=True, slots=True)
class LayerwiseScenarioNetworkBinding:
    """One hash-bound mutable board state compiled over an immutable substrate.

    Distribution can remove a source terminal contact, replace one finite Via
    route, or split a shared-pad graph.  Those operations change the global
    topology itself, so a termination manifest alone is not sufficient.  This
    small carrier lets the application bind the rebuilt network and its cap
    bodies atomically while retaining the source-substrate identity used to
    prove the transformation.
    """

    network: CompiledLayerSurfaceNetwork
    termination_manifest: CompiledLayerSurfaceTerminationManifest
    base_substrate_identity_sha256: str
    scenario_identity_sha256: str
    plan_sha256: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.network, CompiledLayerSurfaceNetwork):
            raise LayerwiseNetworkUnavailable(
                "SCENARIO_NETWORK_INVALID",
                "scenario binding does not carry a compiled layer-surface network",
            )
        if not isinstance(
            self.termination_manifest,
            CompiledLayerSurfaceTerminationManifest,
        ):
            raise LayerwiseNetworkUnavailable(
                "SCENARIO_TERMINATION_INVALID",
                "scenario binding does not carry a compiled termination manifest",
            )
        for label, value in (
            ("base substrate", self.base_substrate_identity_sha256),
            ("scenario", self.scenario_identity_sha256),
            ("scenario plan", self.plan_sha256),
        ):
            digest = str(value).strip().casefold()
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise LayerwiseNetworkUnavailable(
                    "SCENARIO_IDENTITY_INVALID", f"{label} identity is not SHA-256"
                )
            object.__setattr__(
                self,
                {
                    "base substrate": "base_substrate_identity_sha256",
                    "scenario": "scenario_identity_sha256",
                    "scenario plan": "plan_sha256",
                }[label],
                digest,
            )
        try:
            self.network.termination_reduced_node_mapping(
                self.termination_manifest
            )
        except LayerSurfaceNetworkError as exc:
            raise LayerwiseNetworkUnavailable(
                "SCENARIO_TERMINATION_INVALID", str(exc)
            ) from exc
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


def _key(value: Any) -> str:
    result = str(value).strip().casefold()
    if not result:
        raise LayerwiseNetworkUnavailable(
            "SOURCE_IDENTITY_MISSING", "a source layer/net identity is blank"
        )
    return result


def _json_plain(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        return {str(key): _json_plain(value) for key, value in payload.items()}
    if isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        return [_json_plain(value) for value in payload]
    return payload


def _canonical_json(payload: Any) -> bytes:
    return (
        json.dumps(
            _json_plain(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _readonly(values: NDArray[np.generic], dtype: np.dtype | type) -> NDArray:
    result = np.array(values, dtype=dtype, copy=True)
    result.setflags(write=False)
    return result


def _frequency_identity(frequencies_hz: NDArray[np.float64]) -> str:
    values = np.asarray(frequencies_hz, dtype="<f8")
    return sha256(values.tobytes(order="C")).hexdigest()


@dataclass(frozen=True, slots=True)
class CompositeDielectricDispersion:
    """Series dielectric stack evaluated at the caller's query frequencies.

    Interpolating an already-combined effective Dk/Df table is not generally
    equivalent to interpolating each source material and then applying the
    complex-permittivity harmonic mean.  Retaining the source rows here keeps
    the latter operation exact at every solver frequency.
    """

    layers: tuple[tuple[float, DielectricDispersion], ...]

    def __post_init__(self) -> None:
        layers = tuple(self.layers)
        if not layers or any(
            not np.isfinite(float(thickness_um)) or float(thickness_um) <= 0.0
            for thickness_um, _dispersion in layers
        ):
            raise LayerwiseNetworkUnavailable(
                "DIELECTRIC_GAP_INCOMPLETE",
                "a composite dielectric gap needs positive-thickness source rows",
            )
        object.__setattr__(self, "layers", layers)

    def interpolate(
        self, frequencies_hz: Sequence[float] | NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        frequencies = np.asarray(frequencies_hz, dtype=np.float64)
        if (
            frequencies.ndim != 1
            or not frequencies.size
            or not np.all(np.isfinite(frequencies))
            or np.any(frequencies <= 0.0)
        ):
            raise LayerwiseNetworkUnavailable(
                "FREQUENCY_GRID_INVALID",
                "dielectric query frequencies must be finite and positive",
            )
        total_thickness = sum(float(item[0]) for item in self.layers)
        inverse_permittivity = np.zeros(frequencies.shape, dtype=np.complex128)
        for thickness_um, dispersion in self.layers:
            dk, df = dispersion.interpolate(frequencies)
            inverse_permittivity += float(thickness_um) / (
                dk * (1.0 - 1j * df)
            )
        effective = total_thickness / inverse_permittivity
        dk_values = np.asarray(effective.real, dtype=np.float64)
        df_values = np.asarray(-effective.imag / effective.real, dtype=np.float64)
        if (
            not np.all(np.isfinite(dk_values))
            or not np.all(np.isfinite(df_values))
            or np.any(dk_values <= 0.0)
            or np.any(df_values < -1.0e-12)
        ):
            raise LayerwiseNetworkUnavailable(
                "DIELECTRIC_DISPERSION_NONPASSIVE",
                "query-frequency composite Dk/Df is not finite/passive",
            )
        return dk_values, np.maximum(df_values, 0.0)


@dataclass(frozen=True, slots=True)
class LayerwiseNetworkSubstrate:
    """Immutable layer-surface topology plus a bounded solve cache."""

    network: CompiledLayerSurfaceNetwork
    port_by_rail_key: Mapping[str, LayerSurfacePort]
    selected_net_by_rail_key: Mapping[str, str]
    reference_net_by_rail_key: Mapping[str, str]
    layer_blocks: tuple[tuple[str, ...], ...]
    substrate_identity_sha256: str
    provenance: Mapping[str, Any]
    scenario_certificate_view: Mapping[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    external_port_proof_view: Mapping[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    termination_manifest: CompiledLayerSurfaceTerminationManifest | None = None
    _frequency_cache: OrderedDict[
        str,
        tuple[NDArray[np.float64], LayerSurfaceSolveResult],
    ] = field(default_factory=OrderedDict, repr=False, compare=False)
    _frequency_cache_lock: RLock = field(
        default_factory=RLock, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.network, CompiledLayerSurfaceNetwork):
            raise LayerwiseNetworkUnavailable(
                "LAYER_NETWORK_INVALID", "compiled layer-surface network is absent"
            )
        ports = {_key(key): value for key, value in self.port_by_rail_key.items()}
        selected = {
            _key(key): str(value)
            for key, value in self.selected_net_by_rail_key.items()
        }
        references = {
            _key(key): str(value)
            for key, value in self.reference_net_by_rail_key.items()
        }
        if not ports or set(ports) != set(selected) or set(ports) != set(references):
            raise LayerwiseNetworkUnavailable(
                "PORT_MANIFEST_INVALID",
                "rail ports and selected/reference NET manifests must have identical keys",
            )
        network_ports = {item.port_id for item in self.network.ports}
        if any(port.port_id not in network_ports for port in ports.values()):
            raise LayerwiseNetworkUnavailable(
                "PORT_MANIFEST_INVALID", "a rail port is absent from the compiled network"
            )
        substrate_identity = str(self.substrate_identity_sha256).strip().casefold()
        if len(substrate_identity) != 64 or any(
            character not in "0123456789abcdef"
            for character in substrate_identity
        ):
            raise LayerwiseNetworkUnavailable(
                "SUBSTRATE_IDENTITY_INVALID", "substrate identity is not SHA-256"
            )
        provenance = dict(self.provenance)
        declared_substrate_identity = provenance.get("substrate_identity_sha256")
        if declared_substrate_identity is not None:
            declared_substrate_identity = str(
                declared_substrate_identity
            ).strip().casefold()
            if declared_substrate_identity != substrate_identity:
                raise LayerwiseNetworkUnavailable(
                    "SUBSTRATE_IDENTITY_MISMATCH",
                    "declared substrate identity differs from the exact compiled substrate",
                )
            provenance["substrate_identity_sha256"] = substrate_identity
        manifest = self.termination_manifest
        if manifest is not None:
            if not isinstance(manifest, CompiledLayerSurfaceTerminationManifest):
                raise LayerwiseNetworkUnavailable(
                    "TERMINATION_MANIFEST_INVALID",
                    "termination manifest is not compiled for layer-surface use",
                )
            try:
                self.network.termination_reduced_node_mapping(manifest)
            except LayerSurfaceNetworkError as exc:
                raise LayerwiseNetworkUnavailable(
                    "TERMINATION_MANIFEST_INVALID", str(exc)
                ) from exc
        object.__setattr__(self, "port_by_rail_key", MappingProxyType(ports))
        object.__setattr__(
            self, "selected_net_by_rail_key", MappingProxyType(selected)
        )
        object.__setattr__(
            self, "reference_net_by_rail_key", MappingProxyType(references)
        )
        object.__setattr__(self, "layer_blocks", tuple(self.layer_blocks))
        object.__setattr__(self, "substrate_identity_sha256", substrate_identity)
        object.__setattr__(self, "provenance", MappingProxyType(provenance))
        if self.scenario_certificate_view is not None:
            if not is_frozen_json_view(self.scenario_certificate_view):
                object.__setattr__(
                    self,
                    "scenario_certificate_view",
                    MappingProxyType(dict(self.scenario_certificate_view)),
                )
        if self.external_port_proof_view is not None:
            if not is_frozen_json_view(self.external_port_proof_view):
                object.__setattr__(
                    self,
                    "external_port_proof_view",
                    MappingProxyType(dict(self.external_port_proof_view)),
                )

    def with_termination_manifest(
        self,
        manifest: CompiledLayerSurfaceTerminationManifest,
    ) -> "LayerwiseNetworkSubstrate":
        """Return an isolated substrate view carrying mounted terminations.

        The immutable source substrate identity remains the base-network hash;
        solve-cache identities additionally bind this board-state manifest and
        the exact frequency grid.  Port selection does not change global Y.
        """

        provenance = {
            **dict(self.provenance),
            "termination_manifest_sha256": manifest.manifest_sha256,
            "termination_manifest_status": "bound_optional",
        }
        return LayerwiseNetworkSubstrate(
            network=self.network,
            port_by_rail_key=self.port_by_rail_key,
            selected_net_by_rail_key=self.selected_net_by_rail_key,
            reference_net_by_rail_key=self.reference_net_by_rail_key,
            layer_blocks=self.layer_blocks,
            substrate_identity_sha256=self.substrate_identity_sha256,
            provenance=provenance,
            scenario_certificate_view=self.scenario_certificate_view,
            external_port_proof_view=self.external_port_proof_view,
            termination_manifest=manifest,
            # Bound views are immutable selectors over the same verified base
            # network.  Manifest/base/frequency identities isolate entries, so
            # sharing this bounded cache is safe and lets all open Device ports
            # reuse one board-state factorization.
            _frequency_cache=self._frequency_cache,
            _frequency_cache_lock=self._frequency_cache_lock,
        )

    def with_scenario_network_binding(
        self,
        binding: LayerwiseScenarioNetworkBinding,
    ) -> "LayerwiseNetworkSubstrate":
        """Atomically bind a topology-changing Distribution board state."""

        if not isinstance(binding, LayerwiseScenarioNetworkBinding):
            raise LayerwiseNetworkUnavailable(
                "SCENARIO_NETWORK_INVALID", "scenario network binding is absent"
            )
        if (
            binding.base_substrate_identity_sha256
            != self.substrate_identity_sha256.casefold()
        ):
            raise LayerwiseNetworkUnavailable(
                "SCENARIO_BASE_IDENTITY_MISMATCH",
                "scenario topology was not compiled from this exact base substrate",
            )
        base_ports = {
            item.port_id: (item.positive_node_id, item.negative_node_id)
            for item in self.network.ports
        }
        scenario_ports = {
            item.port_id: (item.positive_node_id, item.negative_node_id)
            for item in binding.network.ports
        }
        if scenario_ports != base_ports:
            raise LayerwiseNetworkUnavailable(
                "SCENARIO_PORT_MANIFEST_MISMATCH",
                "scenario topology changed the external Device port manifest",
            )
        provenance = {
            **dict(self.provenance),
            **dict(binding.provenance),
            "base_substrate_identity_sha256": self.substrate_identity_sha256,
            "substrate_identity_sha256": binding.scenario_identity_sha256,
            "scenario_identity_sha256": binding.scenario_identity_sha256,
            "scenario_plan_sha256": binding.plan_sha256,
            "termination_manifest_sha256": (
                binding.termination_manifest.manifest_sha256
            ),
            "termination_manifest_status": "bound_scenario_topology",
        }
        return LayerwiseNetworkSubstrate(
            network=binding.network,
            port_by_rail_key=self.port_by_rail_key,
            selected_net_by_rail_key=self.selected_net_by_rail_key,
            reference_net_by_rail_key=self.reference_net_by_rail_key,
            layer_blocks=self.layer_blocks,
            substrate_identity_sha256=binding.scenario_identity_sha256,
            provenance=provenance,
            external_port_proof_view=self.external_port_proof_view,
            termination_manifest=binding.termination_manifest,
        )

    def _resolved_termination_manifest(
        self,
        override: CompiledLayerSurfaceTerminationManifest | None,
        *,
        required: bool,
    ) -> CompiledLayerSurfaceTerminationManifest | None:
        if (
            override is not None
            and self.termination_manifest is not None
            and override.manifest_sha256
            != self.termination_manifest.manifest_sha256
        ):
            raise LayerwiseNetworkUnavailable(
                "TERMINATION_MANIFEST_CONFLICT",
                "source-model and substrate termination manifests disagree",
            )
        manifest = override if override is not None else self.termination_manifest
        if required and manifest is None:
            raise LayerwiseNetworkUnavailable(
                "TERMINATION_MANIFEST_REQUIRED",
                "this layerwise solve requires a mounted-termination manifest",
            )
        return manifest

    def _solve_all_ports(
        self,
        frequencies_hz: NDArray[np.float64],
        *,
        selected_rail_id: str,
        termination_manifest: CompiledLayerSurfaceTerminationManifest | None,
        progress: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> LayerSurfaceSolveResult:
        cancelled = is_cancelled or (lambda: False)
        if cancelled():
            raise RuntimeError("evaluation cancelled")
        try:
            # A mounted manifest describes the whole physical board state.  Its
            # cache identity deliberately ignores which already-computed open
            # Device port the caller will read from the multi-port result.
            identity = (
                _frequency_identity(frequencies_hz)
                if termination_manifest is None
                else termination_manifest.cache_identity_sha256(
                    self.substrate_identity_sha256,
                    selected_rail_id,
                    frequencies_hz,
                )
            )
        except LayerSurfaceTerminationError as exc:
            raise LayerwiseNetworkUnavailable(
                "TERMINATION_CACHE_IDENTITY_INVALID", str(exc)
            ) from exc
        with self._frequency_cache_lock:
            cached = self._frequency_cache.get(identity)
            if cached is not None and np.array_equal(cached[0], frequencies_hz):
                self._frequency_cache.move_to_end(identity)
                if progress is not None:
                    progress(int(frequencies_hz.size), int(frequencies_hz.size))
                return cached[1]
        try:
            result = self.network.solve(
                frequencies_hz,
                termination_manifest=termination_manifest,
                selected_rail_id=(
                    selected_rail_id if termination_manifest is not None else None
                ),
                base_network_identity_sha256=(
                    self.substrate_identity_sha256
                ),
                progress=progress,
                is_cancelled=cancelled,
            )
        except LayerSurfaceNetworkError as exc:
            raise LayerwiseNetworkUnavailable(
                "GLOBAL_KRON_REDUCTION_FAILED", str(exc)
            ) from exc
        if cancelled():
            raise RuntimeError("evaluation cancelled")
        frozen_frequencies = _readonly(frequencies_hz, np.float64)
        with self._frequency_cache_lock:
            self._frequency_cache[identity] = (frozen_frequencies, result)
            self._frequency_cache.move_to_end(identity)
            while len(self._frequency_cache) > _FREQUENCY_CACHE_LIMIT:
                self._frequency_cache.popitem(last=False)
        return result

    def assemble(
        self,
        rail_id: str,
        frequencies_hz: Sequence[float] | NDArray[np.float64],
        evidence: UniformPortConnectivityEvidence | None,
        *,
        termination_manifest: CompiledLayerSurfaceTerminationManifest | None = None,
        require_termination_manifest: bool = False,
        progress: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> UniformC00Assembly:
        frequencies = np.asarray(frequencies_hz, dtype=np.float64)
        if (
            frequencies.ndim != 1
            or not frequencies.size
            or not np.all(np.isfinite(frequencies))
            or np.any(frequencies <= 0.0)
        ):
            raise LayerwiseNetworkUnavailable(
                "FREQUENCY_GRID_INVALID", "frequencies must be finite and positive"
            )
        rail_key = _key(rail_id)
        port = self.port_by_rail_key.get(rail_key)
        if port is None:
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_MISSING", f"rail {rail_id!r} has no compiled layer-surface port"
            )
        selected_net = self.selected_net_by_rail_key[rail_key]
        reference_net = self.reference_net_by_rail_key[rail_key]
        if (
            evidence is None
            or not evidence.source_terminal_component_proven
            or not evidence.reference_terminal_component_proven
            or _key(evidence.reference_net) != _key(reference_net)
            or _key(evidence.source_net) != _key(selected_net)
        ):
            return UniformC00Assembly(
                status="blocked_fail_closed",
                reason=(
                    "source and reference terminal landings are not independently "
                    "source-proven for the selected layerwise port"
                ),
                selected_net_names=(selected_net,),
                frequencies_hz=frequencies,
                effective_admittance_s=None,
                raw_admittance_s=None,
                reference_evidence=evidence,
            )
        resolved_manifest = self._resolved_termination_manifest(
            termination_manifest,
            required=require_termination_manifest,
        )
        result = self._solve_all_ports(
            frequencies,
            selected_rail_id=rail_id,
            termination_manifest=resolved_manifest,
            progress=progress,
            is_cancelled=is_cancelled,
        )
        values = result.effective_admittance_by_port[port.port_id][:, None, None]
        return UniformC00Assembly(
            status="ok",
            reason=None,
            selected_net_names=(selected_net,),
            frequencies_hz=frequencies,
            effective_admittance_s=values,
            # The full sparse raw matrix is intentionally not materialized as a
            # dense (frequency,node,node) cube.  The bounded scalar below is the
            # exact open-port projection returned by the one global reduction.
            raw_admittance_s=values,
            reference_evidence=evidence,
        )


@dataclass(frozen=True, slots=True)
class LayerwiseUniformSourceModel:
    substrate: LayerwiseNetworkSubstrate
    rail_id: str
    selected_net: str
    port_connectivity: UniformPortConnectivityEvidence
    evidence_sha256: str
    provenance: Mapping[str, Any]
    uniform_port_scope: str = "surface_pair"
    termination_manifest: CompiledLayerSurfaceTerminationManifest | None = None
    require_termination_manifest: bool = False

    def __post_init__(self) -> None:
        if not self.rail_id.strip() or not self.selected_net.strip():
            raise LayerwiseNetworkUnavailable(
                "RAIL_EVIDENCE_INVALID", "layerwise source rail/NET identity is blank"
            )
        if self.uniform_port_scope not in {
            "surface_pair",
            "external_device_port",
        }:
            raise LayerwiseNetworkUnavailable(
                "RAIL_EVIDENCE_INVALID",
                "layerwise uniform port scope is unsupported",
            )
        if len(self.evidence_sha256) != 64:
            raise LayerwiseNetworkUnavailable(
                "RAIL_EVIDENCE_INVALID", "rail evidence identity is not SHA-256"
            )
        substrate_manifest = self.substrate.termination_manifest
        if (
            self.termination_manifest is not None
            and substrate_manifest is not None
            and self.termination_manifest.manifest_sha256
            != substrate_manifest.manifest_sha256
        ):
            raise LayerwiseNetworkUnavailable(
                "TERMINATION_MANIFEST_CONFLICT",
                "source-model and substrate termination manifests disagree",
            )
        effective_manifest = (
            self.termination_manifest
            if self.termination_manifest is not None
            else substrate_manifest
        )
        if effective_manifest is not None:
            if not isinstance(
                effective_manifest,
                CompiledLayerSurfaceTerminationManifest,
            ):
                raise LayerwiseNetworkUnavailable(
                    "TERMINATION_MANIFEST_INVALID",
                    "source-model termination manifest is not compiled",
                )
            try:
                self.substrate.network.termination_reduced_node_mapping(
                    effective_manifest
                )
            except LayerSurfaceNetworkError as exc:
                raise LayerwiseNetworkUnavailable(
                    "TERMINATION_MANIFEST_INVALID", str(exc)
                ) from exc
        provenance = dict(self.provenance)
        declared_substrate_identity = provenance.get("substrate_identity_sha256")
        if declared_substrate_identity is not None:
            declared_substrate_identity = str(
                declared_substrate_identity
            ).strip().casefold()
            exact_substrate_identity = (
                self.substrate.substrate_identity_sha256.strip().casefold()
            )
            if declared_substrate_identity != exact_substrate_identity:
                raise LayerwiseNetworkUnavailable(
                    "SUBSTRATE_IDENTITY_MISMATCH",
                    "source provenance differs from the exact bound substrate identity",
                )
            provenance["substrate_identity_sha256"] = exact_substrate_identity
        if effective_manifest is not None:
            base_evidence = str(
                provenance.get(
                    "base_layerwise_evidence_sha256", self.evidence_sha256
                )
            ).casefold()
            if len(base_evidence) != 64 or any(
                character not in "0123456789abcdef"
                for character in base_evidence
            ):
                raise LayerwiseNetworkUnavailable(
                    "RAIL_EVIDENCE_INVALID",
                    "base layerwise evidence identity is not SHA-256",
                )
            bound_evidence = sha256(
                _canonical_json(
                    {
                        "base_layerwise_evidence_sha256": base_evidence,
                        "substrate_identity_sha256": (
                            self.substrate.substrate_identity_sha256
                        ),
                        "termination_manifest_sha256": (
                            effective_manifest.manifest_sha256
                        ),
                        "selected_rail_id": _key(self.rail_id),
                    }
                )
            ).hexdigest()
            provenance.update(
                {
                    "base_layerwise_evidence_sha256": base_evidence,
                    "termination_manifest_sha256": (
                        effective_manifest.manifest_sha256
                    ),
                    "bound_substrate_identity_sha256": (
                        self.substrate.substrate_identity_sha256
                    ),
                    "layerwise_identity_sha256": bound_evidence,
                }
            )
            object.__setattr__(self, "evidence_sha256", bound_evidence)
        object.__setattr__(self, "provenance", MappingProxyType(provenance))

    def with_termination_manifest(
        self,
        manifest: (
            CompiledLayerSurfaceTerminationManifest
            | LayerwiseScenarioNetworkBinding
        ),
        *,
        required: bool = True,
    ) -> "LayerwiseUniformSourceModel":
        """Bind one Original/Tuned mounted state without mutating the substrate."""

        substrate = self.substrate
        if isinstance(manifest, LayerwiseScenarioNetworkBinding):
            substrate = substrate.with_scenario_network_binding(manifest)
            effective_manifest = manifest.termination_manifest
        else:
            effective_manifest = manifest
        selected_key = _key(self.rail_id)
        selected_cluster_count = sum(
            _key(item.rail_id) == selected_key
            for item in effective_manifest.clusters
        )
        provenance = {
            **dict(self.provenance),
            **(
                dict(manifest.provenance)
                if isinstance(manifest, LayerwiseScenarioNetworkBinding)
                else {}
            ),
            **(
                {
                    "base_substrate_identity_sha256": (
                        manifest.base_substrate_identity_sha256
                    ),
                    "scenario_identity_sha256": manifest.scenario_identity_sha256,
                }
                if isinstance(manifest, LayerwiseScenarioNetworkBinding)
                else {}
            ),
            "substrate_identity_sha256": substrate.substrate_identity_sha256,
            "termination_manifest_sha256": effective_manifest.manifest_sha256,
            "termination_manifest_required": bool(required),
            "termination_physical_cluster_count": len(effective_manifest.clusters),
            "termination_active_physical_cluster_count": len(
                effective_manifest.clusters
            ),
            "termination_active_selected_rail_cluster_count": (
                selected_cluster_count
            ),
            "termination_active_other_rail_cluster_count": (
                len(effective_manifest.clusters) - selected_cluster_count
            ),
            "termination_excluded_selected_rail_cluster_count": 0,
        }
        return LayerwiseUniformSourceModel(
            substrate=substrate,
            rail_id=self.rail_id,
            selected_net=self.selected_net,
            port_connectivity=self.port_connectivity,
            evidence_sha256=self.evidence_sha256,
            provenance=provenance,
            uniform_port_scope=self.uniform_port_scope,
            termination_manifest=effective_manifest,
            require_termination_manifest=bool(required),
        )

    def assemble(
        self,
        frequencies_hz: Sequence[float] | NDArray[np.float64],
        *,
        progress: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> UniformC00Assembly:
        return self.substrate.assemble(
            self.rail_id,
            frequencies_hz,
            self.port_connectivity,
            termination_manifest=self.termination_manifest,
            require_termination_manifest=self.require_termination_manifest,
            progress=progress,
            is_cancelled=is_cancelled,
        )


def _gap_dispersion(
    project: Any, upper_layer: str, lower_layer: str
) -> CompositeDielectricDispersion:
    positions = {_key(layer.name): index for index, layer in enumerate(project.stackup_layers)}
    upper, lower = positions[_key(upper_layer)], positions[_key(lower_layer)]
    if upper > lower:
        upper, lower = lower, upper
    rows = tuple(project.stackup_layers[upper + 1 : lower])
    if not rows or any(row.is_conductor or row.dk is None for row in rows):
        raise LayerwiseNetworkUnavailable(
            "DIELECTRIC_GAP_INCOMPLETE",
            f"{upper_layer}/{lower_layer} lacks a complete dielectric-only source gap",
        )
    layers: list[tuple[float, DielectricDispersion]] = []
    for row in rows:
        points = tuple(getattr(row, "dielectric_properties", ()))
        if points:
            dispersion = DielectricDispersion(
                frequencies_hz=tuple(float(point.frequency_hz) for point in points),
                relative_permittivities=tuple(float(point.dk) for point in points),
                loss_tangents=tuple(float(point.df) for point in points),
            )
        else:
            dispersion = DielectricDispersion(
                frequencies_hz=(1.0e9,),
                relative_permittivities=(float(row.dk),),
                loss_tangents=(float(row.df or 0.0),),
            )
        layers.append((float(row.thickness_um), dispersion))
    return CompositeDielectricDispersion(tuple(layers))


def _retained_conductor_blocks(
    project: Any, records: Sequence[Mapping[str, Any]]
) -> tuple[tuple[str, ...], ...]:
    retained = {_key(record.get("layer", "")) for record in records}
    blocks: list[tuple[str, ...]] = []
    current: list[str] = []
    for layer in project.stackup_layers:
        if not layer.is_conductor:
            continue
        if _key(layer.name) in retained:
            current.append(layer.name)
        else:
            if len(current) >= 2:
                blocks.append(tuple(current))
            current = []
    if len(current) >= 2:
        blocks.append(tuple(current))
    if not blocks:
        raise LayerwiseNetworkUnavailable(
            "CONTIGUOUS_ARTWORK_BLOCK_MISSING",
            "no two physically adjacent conductor layers have retained artwork",
        )
    return tuple(blocks)


def _surface_node_id(layer: str, net: str) -> str:
    """Return a collision-safe, deterministic physical surface identity."""

    layer_key, net_key = _key(layer), _key(net)
    return f"surface|{len(layer_key)}:{layer_key}|{len(net_key)}:{net_key}"


def _retained_surface_display(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[str, str]]:
    """Index every retained physical artwork surface, including gap-isolated ones."""

    result: dict[str, tuple[str, str]] = {}
    for record in records:
        layer = str(record.get("layer", "")).strip()
        net = str(record.get("net", "")).strip()
        if not layer or not net:
            raise LayerwiseNetworkUnavailable(
                "ARTWORK_RECORD_INVALID",
                "retained plane artwork has a blank layer or NET identity",
            )
        node = _surface_node_id(layer, net)
        display = (layer, net)
        previous = result.get(node)
        if previous is None or tuple(item.casefold() for item in display) < tuple(
            item.casefold() for item in previous
        ):
            result[node] = display
    return result


def _surface_resolved_model(model: MultilayerCapacitanceModel) -> MultilayerCapacitanceModel:
    """Rename only electrical identities; retain exact source geometry/material."""

    return MultilayerCapacitanceModel(
        layer_order=model.layer_order,
        gaps=model.gaps,
        artwork=tuple(
            CapacitanceArtwork(
                item.layer,
                _surface_node_id(item.layer, item.net),
                item.geometry_um,
            )
            for item in model.artwork
        ),
        opening_fill_by_layer=model.opening_fill_by_layer,
        enable_nonadjacent_opening_coupling=model.enable_nonadjacent_opening_coupling,
    )


def _metadata_sha256_without_newline(payload: Any) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _validated_device_terminal_via_certificate(
    project: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Mapping[str, Any]]]:
    """Validate the exact source-Node/first-Via certificate independently.

    The v3 surface certificate identifies the artwork component and physical
    landing.  This older, separately source-bound manifest is still required
    at runtime because it is the evidence that the Device pin itself owns the
    exact first Via ID rather than an arbitrary reusable Via template.
    """

    spd_import = project.metadata.get("spd_import")
    certificate = (
        spd_import.get("layerwise_device_terminal_via_certificate")
        if isinstance(spd_import, Mapping)
        else None
    )
    if not isinstance(certificate, Mapping):
        raise LayerwiseNetworkUnavailable(
            "DEVICE_TERMINAL_VIA_CERTIFICATE_MISSING",
            "the exact Device source-Node/first-Via certificate is absent",
        )
    if (
        certificate.get("schema_version")
        != "spd-layerwise-device-terminal-vias-v1"
        or certificate.get("compiler_id")
        != "powersi-direct-device-top-via-v1"
    ):
        raise LayerwiseNetworkUnavailable(
            "DEVICE_TERMINAL_VIA_CERTIFICATE_UNSUPPORTED",
            "the Device terminal Via certificate schema/compiler is unsupported",
        )
    expected_source = str(spd_import.get("source_sha256", "")).casefold()
    source_sha256 = str(certificate.get("source_sha256", "")).casefold()
    evidence_sha256 = str(certificate.get("evidence_sha256", "")).casefold()
    unsigned = {
        key: certificate[key]
        for key in certificate
        if key != "evidence_sha256"
    }
    if (
        not _is_sha256(source_sha256)
        or source_sha256 != expected_source
        or not _is_sha256(evidence_sha256)
        or _metadata_sha256_without_newline(unsigned) != evidence_sha256
    ):
        raise LayerwiseNetworkUnavailable(
            "DEVICE_TERMINAL_VIA_CERTIFICATE_INTEGRITY_FAILED",
            "the Device terminal Via certificate is not bound to the retained SPD source",
        )
    rows = _certificate_rows(
        certificate.get("terminals"),
        code="DEVICE_TERMINAL_VIA_CERTIFICATE_INVALID",
        label="Device terminal Via manifest",
    )
    by_pin: dict[str, Mapping[str, Any]] = {}
    complete_count = 0
    complete_via_keys: set[str] = set()
    for raw in rows:
        pin_key = _key(raw.get("pin_id", ""))
        status = str(raw.get("status") or "").strip()
        issues = raw.get("issues")
        candidate_hash = str(
            raw.get("candidate_via_ids_sha256") or ""
        ).strip().casefold()
        try:
            candidate_count = int(raw.get("candidate_count"))
        except (TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_TERMINAL_VIA_CERTIFICATE_INVALID",
                "a Device terminal Via row has an invalid candidate count",
            ) from exc
        if (
            pin_key in by_pin
            or not status
            or not isinstance(issues, Sequence)
            or isinstance(issues, (str, bytes))
            or isinstance(raw.get("candidate_count"), bool)
            or candidate_count < 0
            or not _is_sha256(candidate_hash)
        ):
            raise LayerwiseNetworkUnavailable(
                "DEVICE_TERMINAL_VIA_CERTIFICATE_INVALID",
                "a Device terminal Via row has duplicate or malformed source evidence",
            )
        if status == "complete":
            via_id = str(raw.get("incident_via_id") or "").strip()
            via_key = via_id.casefold()
            if (
                candidate_count != 1
                or len(issues) != 0
                or not via_id
                or not str(raw.get("incident_net") or "").strip()
                or not str(raw.get("incident_padstack") or "").strip()
                or not str(raw.get("incident_opposite_node_id") or "").strip()
                or candidate_hash
                != _metadata_sha256_without_newline((via_id,))
                or via_key in complete_via_keys
            ):
                raise LayerwiseNetworkUnavailable(
                    "DEVICE_TERMINAL_VIA_CERTIFICATE_INVALID",
                    "a complete Device terminal Via row lacks exact one-Via ownership evidence",
                )
            complete_via_keys.add(via_key)
            complete_count += 1
        by_pin[pin_key] = raw
    try:
        declared_terminal_count = int(certificate.get("terminal_count"))
        declared_complete_count = int(certificate.get("complete_terminal_count"))
        declared_incomplete_count = int(
            certificate.get("incomplete_terminal_count")
        )
    except (TypeError, ValueError) as exc:
        raise LayerwiseNetworkUnavailable(
            "DEVICE_TERMINAL_VIA_CERTIFICATE_INVALID",
            "Device terminal Via certificate counts are invalid",
        ) from exc
    expected_status = (
        "complete" if rows and complete_count == len(rows) else "incomplete"
    )
    if (
        any(
            isinstance(certificate.get(name), bool)
            for name in (
                "terminal_count",
                "complete_terminal_count",
                "incomplete_terminal_count",
            )
        )
        or declared_terminal_count != len(rows)
        or declared_complete_count != complete_count
        or declared_incomplete_count != len(rows) - complete_count
        or str(certificate.get("status") or "").strip() != expected_status
    ):
        raise LayerwiseNetworkUnavailable(
            "DEVICE_TERMINAL_VIA_CERTIFICATE_INVALID",
            "Device terminal Via certificate counts/status do not match its exact rows",
        )
    return certificate, MappingProxyType(by_pin)


def _device_terminal_ownership_disclosure(
    project: Any,
    surface_certificate: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Disclose exact anchor, surplus-Device, and decap Via ownership sets."""

    terminal_certificate, terminal_by_pin = (
        _validated_device_terminal_via_certificate(project)
    )
    assets = _certificate_rows(
        surface_certificate.get("geometry_assets"),
        code="SURFACE_CONNECTIVITY_CERTIFICATE_INVALID",
        label="geometry-asset manifest",
    )
    retained_net_keys = {_key(item.get("net", "")) for item in assets}
    device_by_key = {
        _key(raw.get("incident_via_id", "")): str(
            raw.get("incident_via_id") or ""
        ).strip()
        for raw in terminal_by_pin.values()
        if str(raw.get("status") or "").strip() == "complete"
        and _key(raw.get("incident_net", "")) in retained_net_keys
    }
    landing_rows = _certificate_rows(
        surface_certificate.get("terminal_landing_contacts"),
        code="TERMINAL_LANDING_CERTIFICATE_MISSING",
        label="terminal landing-contact manifest",
    )
    anchor_device_by_key = {
        _key(raw.get("via_id", "")): str(raw.get("via_id") or "").strip()
        for raw in landing_rows
        if str(raw.get("terminal_owner_kind") or "").strip().casefold()
        == "device"
    }
    decap_by_key = {
        _key(raw.get("via_id", "")): str(raw.get("via_id") or "").strip()
        for raw in landing_rows
        if str(raw.get("terminal_owner_kind") or "").strip().casefold()
        == "decap"
    }
    surplus_by_key = {
        key: display
        for key, display in device_by_key.items()
        if key not in anchor_device_by_key
    }
    union_by_key = {**device_by_key, **decap_by_key}

    def identity(values: Mapping[str, str]) -> str:
        ordered = sorted(values.values(), key=lambda item: (item.casefold(), item))
        return _metadata_sha256_without_newline(ordered)

    return MappingProxyType(
        {
            "device_terminal_via_certificate_evidence_sha256": str(
                terminal_certificate.get("evidence_sha256") or ""
            ).casefold(),
            "exact_device_terminal_owned_via_count": len(device_by_key),
            "exact_device_terminal_owned_via_ids_sha256": identity(device_by_key),
            "anchor_device_terminal_landing_via_count": len(anchor_device_by_key),
            "anchor_device_terminal_landing_via_ids_sha256": identity(
                anchor_device_by_key
            ),
            "surplus_device_terminal_owned_via_count": len(surplus_by_key),
            "surplus_device_terminal_owned_via_ids_sha256": identity(
                surplus_by_key
            ),
            "decap_terminal_owned_via_count": len(decap_by_key),
            "decap_terminal_owned_via_ids_sha256": identity(decap_by_key),
            "terminal_owned_union_via_count": len(union_by_key),
            "terminal_owned_union_via_ids_sha256": identity(union_by_key),
            "surplus_device_terminal_via_model_scope": (
                "exact source-owned non-anchor Via IDs are represented only by the "
                "conservative shared/unequal Device branch abstraction; they have no "
                "individual v3 landing or substrate stamp"
            ),
        }
    )


def _validated_via_certificate(project: Any) -> Mapping[str, Any]:
    spd_import = project.metadata.get("spd_import")
    certificate = (
        spd_import.get("layerwise_via_group_certificate")
        if isinstance(spd_import, Mapping)
        else None
    )
    if not isinstance(certificate, Mapping):
        raise LayerwiseNetworkUnavailable(
            "VIA_GROUP_CERTIFICATE_MISSING",
            "reimport the source SPD so its compact layerwise Via-group certificate is retained",
        )
    if (
        certificate.get("schema_version") != "spd-layerwise-via-groups-v2"
        or certificate.get("compiler_id")
        != "powersi-via-usage-padstack-terminal-ownership-v2"
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_GROUP_CERTIFICATE_UNSUPPORTED",
            "the retained Via-group certificate schema/compiler is not supported",
        )
    source_sha256 = str(certificate.get("source_sha256", "")).casefold()
    expected_source = str(spd_import.get("source_sha256", "")).casefold()
    if len(source_sha256) != 64 or source_sha256 != expected_source:
        raise LayerwiseNetworkUnavailable(
            "VIA_GROUP_SOURCE_MISMATCH",
            "the Via-group certificate is not bound to the retained SPD source SHA-256",
        )
    evidence = str(certificate.get("evidence_sha256", "")).casefold()
    unsigned = {
        key: certificate[key]
        for key in certificate
        if key != "evidence_sha256"
    }
    if len(evidence) != 64 or _metadata_sha256_without_newline(unsigned) != evidence:
        raise LayerwiseNetworkUnavailable(
            "VIA_GROUP_CERTIFICATE_INTEGRITY_FAILED",
            "the compact Via-group certificate evidence SHA-256 does not match",
        )
    terminal_certificate = spd_import.get(
        "layerwise_device_terminal_via_certificate"
    )
    if (
        not isinstance(terminal_certificate, Mapping)
        or terminal_certificate.get("schema_version")
        != "spd-layerwise-device-terminal-vias-v1"
        or terminal_certificate.get("compiler_id")
        != "powersi-direct-device-top-via-v1"
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_GROUP_TERMINAL_CERTIFICATE_MISSING",
            "the Via-group v2 ownership manifest requires its Device-terminal Via certificate",
        )
    terminal_source = str(
        terminal_certificate.get("source_sha256", "")
    ).casefold()
    terminal_evidence = str(
        terminal_certificate.get("evidence_sha256", "")
    ).casefold()
    terminal_unsigned = {
        key: terminal_certificate[key]
        for key in terminal_certificate
        if key != "evidence_sha256"
    }
    if (
        terminal_source != expected_source
        or len(terminal_evidence) != 64
        or _metadata_sha256_without_newline(terminal_unsigned)
        != terminal_evidence
        or str(
            certificate.get("device_terminal_via_evidence_sha256", "")
        ).casefold()
        != terminal_evidence
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_GROUP_TERMINAL_CERTIFICATE_MISMATCH",
            "the Via-group ownership counts do not bind the retained Device-terminal Via evidence",
        )
    groups = certificate.get("groups")
    if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
        raise LayerwiseNetworkUnavailable(
            "VIA_GROUP_CERTIFICATE_INVALID", "Via-group records are missing"
        )
    return certificate


def _certificate_rows(value: Any, *, code: str, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise LayerwiseNetworkUnavailable(code, f"the certificate has no valid {label}")
    rows = tuple(item for item in value if isinstance(item, Mapping))
    if len(rows) != len(value):
        raise LayerwiseNetworkUnavailable(code, f"the certificate {label} contains an invalid row")
    return rows


def _is_sha256(value: Any) -> bool:
    text = str(value).strip().casefold()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _surface_component_identity(
    source_sha256: str,
    net: str,
    layer: str,
    island_ids: Sequence[str],
) -> tuple[str, str]:
    evidence = _metadata_sha256_without_newline(
        {
            "source_sha256": str(source_sha256).casefold(),
            "net": str(net).casefold(),
            "layer": str(layer).casefold(),
            "island_ids": sorted(str(item) for item in island_ids),
        }
    )
    return f"spd-surface-equivalence-component:{evidence[:24]}", evidence


def _stackup_conductor_center_depths(project: Any) -> Mapping[str, float]:
    centers: dict[str, float] = {}
    seen_layers: set[str] = set()
    depth_um = 0.0
    for raw in project.stackup_layers:
        name = str(getattr(raw, "name", "")).strip()
        try:
            thickness_um = float(getattr(raw, "thickness_um"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "VIA_SEGMENT_STACKUP_MISMATCH",
                "the current stackup has a layer with invalid thickness",
            ) from exc
        if (
            not name
            or _key(name) in seen_layers
            or not isfinite(thickness_um)
            or thickness_um <= 0.0
        ):
            raise LayerwiseNetworkUnavailable(
                "VIA_SEGMENT_STACKUP_MISMATCH",
                "the current stackup needs unique named layers with positive finite thickness",
            )
        seen_layers.add(_key(name))
        if bool(getattr(raw, "is_conductor", False)):
            centers[_key(name)] = depth_um + thickness_um / 2.0
        depth_um += thickness_um
    if len(centers) < 2:
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            "the current stackup has fewer than two uniquely located conductor layers",
        )
    return MappingProxyType(centers)


def _validate_segment_stackup_length(
    centers_um: Mapping[str, float],
    start_layer: str,
    end_layer: str,
    certified_length_um: float,
) -> None:
    start = centers_um.get(_key(start_layer))
    end = centers_um.get(_key(end_layer))
    expected = abs(float(end) - float(start)) if start is not None and end is not None else None
    if (
        expected is None
        or expected <= 0.0
        or not isclose(
            float(certified_length_um),
            expected,
            rel_tol=1.0e-12,
            abs_tol=1.0e-9,
        )
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            f"Via segment {start_layer!r}->{end_layer!r} length differs from current conductor-center depth",
        )


def _validate_segment_stackup_chain(
    centers_um: Mapping[str, float],
    segments: Sequence[tuple[int, str, str, float]],
    *,
    start_layer: str,
    end_layer: str,
) -> None:
    """Prove one Via path is the direct monotonic conductor-center span.

    Per-edge lengths alone are insufficient: a forged path may walk past the
    endpoint and return, or form a cycle, while every individual edge still
    has the right length.  Physical Via barrels used by this compiler must
    traverse the current stackup exactly once in one direction.
    """

    if not segments:
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            "a Via path has no conductor-center segment",
        )
    ordered = tuple(sorted(segments, key=lambda item: item[0]))
    if (
        tuple(item[0] for item in ordered) != tuple(range(len(ordered)))
        or _key(ordered[0][1]) != _key(start_layer)
        or _key(ordered[-1][2]) != _key(end_layer)
        or any(
            _key(first[2]) != _key(second[1])
            for first, second in zip(ordered, ordered[1:], strict=False)
        )
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            "Via segments are not one ordered continuous endpoint path",
        )

    layer_keys = [_key(ordered[0][1]), *(_key(item[2]) for item in ordered)]
    if len(layer_keys) != len(set(layer_keys)):
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            "a Via path repeats a conductor layer",
        )
    try:
        depths = [float(centers_um[layer_key]) for layer_key in layer_keys]
    except KeyError as exc:
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            "a Via path references a conductor absent from the current stackup",
        ) from exc
    depth_steps = [second - first for first, second in zip(depths, depths[1:], strict=False)]
    if not depth_steps or not (
        all(step > 0.0 for step in depth_steps)
        or all(step < 0.0 for step in depth_steps)
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            "a Via path is not strictly monotonic through the current stackup",
        )
    for _ordinal, segment_start, segment_end, length_um in ordered:
        _validate_segment_stackup_length(
            centers_um, segment_start, segment_end, length_um
        )
    certified_total = sum(float(item[3]) for item in ordered)
    expected_total = abs(depths[-1] - depths[0])
    if not isclose(
        certified_total,
        expected_total,
        rel_tol=1.0e-12,
        abs_tol=1.0e-9,
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_SEGMENT_STACKUP_MISMATCH",
            "the summed Via path differs from its direct conductor-center span",
        )


def _certificate_island_inventory(
    certificate: Mapping[str, Any],
) -> tuple[
    dict[str, tuple[str, str]],
    dict[tuple[str, str], tuple[str, ...]],
]:
    assets = _certificate_rows(
        certificate.get("geometry_assets"),
        code="SURFACE_CONNECTIVITY_CERTIFICATE_INVALID",
        label="geometry-asset manifest",
    )
    island_display: dict[str, tuple[str, str]] = {}
    islands_by_surface: dict[tuple[str, str], tuple[str, ...]] = {}
    for raw in assets:
        layer = str(raw.get("layer", "")).strip()
        net = str(raw.get("net", "")).strip()
        key = (_key(net), _key(layer))
        raw_ids = raw.get("island_ids")
        if (
            key in islands_by_surface
            or not isinstance(raw_ids, Sequence)
            or isinstance(raw_ids, (str, bytes))
        ):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_ISLAND_INVENTORY_INVALID",
                "each exact layer/NET asset needs one island inventory",
            )
        island_ids = tuple(sorted(str(item).strip() for item in raw_ids))
        if (
            not island_ids
            or len(set(island_ids)) != len(island_ids)
            or any(not item.startswith("spd-surface-island:") for item in island_ids)
        ):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_ISLAND_INVENTORY_INVALID",
                f"the island inventory for {net!r} on {layer!r} is blank, duplicated, or unsupported",
            )
        for island_id in island_ids:
            if island_id in island_display:
                raise LayerwiseNetworkUnavailable(
                    "SURFACE_ISLAND_IDENTITY_DUPLICATED",
                    f"artwork island {island_id!r} is claimed by more than one surface",
                )
            island_display[island_id] = (layer, net)
        islands_by_surface[key] = island_ids
    if not island_display:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_ISLAND_INVENTORY_INVALID", "the certificate has no artwork islands"
        )
    return island_display, islands_by_surface


def _surface_equivalence_partition(
    certificate: Mapping[str, Any],
    islands_by_surface: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[dict[str, tuple[str, str, int]], tuple[tuple[str, ...], ...]]:
    rows = _certificate_rows(
        certificate.get("surface_equivalence_components"),
        code="SURFACE_EQUIVALENCE_PARTITION_MISSING",
        label="surface-equivalence partition",
    )
    component_by_island: dict[str, tuple[str, str, int]] = {}
    components: list[tuple[str, ...]] = []
    component_contact_statuses: list[str] = []
    seen_by_surface: dict[tuple[str, str], set[str]] = {}
    component_ids: set[str] = set()
    source_sha256 = str(certificate.get("source_sha256", "")).casefold()
    for raw in rows:
        net = str(raw.get("net", "")).strip()
        layer = str(raw.get("layer", "")).strip()
        key = (_key(net), _key(layer))
        raw_ids = raw.get("island_ids")
        if (
            key not in islands_by_surface
            or not isinstance(raw_ids, Sequence)
            or isinstance(raw_ids, (str, bytes))
        ):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_EQUIVALENCE_PARTITION_INVALID",
                "an equivalence component has an unknown layer/NET identity or island list",
            )
        island_ids = tuple(sorted(str(item).strip() for item in raw_ids))
        component_id = str(raw.get("component_id", "")).strip()
        representative = str(raw.get("representative_island_id", "")).strip()
        evidence = str(raw.get("component_evidence_sha256", "")).strip().casefold()
        expected_component_id, expected_evidence = _surface_component_identity(
            source_sha256, net, layer, island_ids
        )
        inventory = set(islands_by_surface[key])
        observed = seen_by_surface.setdefault(key, set())
        if (
            not island_ids
            or len(set(island_ids)) != len(island_ids)
            or not set(island_ids) <= inventory
            or observed.intersection(island_ids)
            or component_id != expected_component_id
            or component_id in component_ids
            or representative not in island_ids
            or evidence != expected_evidence
        ):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_EQUIVALENCE_PARTITION_INVALID",
                f"equivalence components do not uniquely and hash-exactly partition {net!r} on {layer!r}",
            )
        component_index = len(components)
        components.append(island_ids)
        component_contact_statuses.append(
            str(raw.get("contact_status") or "").strip().casefold()
        )
        component_ids.add(component_id)
        observed.update(island_ids)
        for island_id in island_ids:
            component_by_island[island_id] = (key[0], key[1], component_index)
    if {
        key: tuple(sorted(values)) for key, values in seen_by_surface.items()
    } != dict(islands_by_surface):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_EQUIVALENCE_PARTITION_INCOMPLETE",
            "same-layer equivalence components do not partition every certified artwork island",
        )

    proofs = _certificate_rows(
        certificate.get("surface_equivalence_proofs"),
        code="SURFACE_EQUIVALENCE_PROOF_MISSING",
        label="surface-equivalence proof manifest",
    )
    proof_keys: set[tuple[str, str]] = set()
    all_contacted_islands: set[str] = set()
    for raw in proofs:
        key = (_key(raw.get("net", "")), _key(raw.get("layer", "")))
        raw_islands = raw.get("island_ids")
        raw_contacted = raw.get("contacted_island_ids")
        if (
            key in proof_keys
            or key not in islands_by_surface
            or not isinstance(raw_islands, Sequence)
            or isinstance(raw_islands, (str, bytes))
            or not isinstance(raw_contacted, Sequence)
            or isinstance(raw_contacted, (str, bytes))
        ):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_EQUIVALENCE_PROOF_INVALID",
                "a surface-equivalence proof identity or island list is invalid",
            )
        island_ids = tuple(sorted(str(item).strip() for item in raw_islands))
        contacted = tuple(sorted(str(item).strip() for item in raw_contacted))
        component_count = raw.get("graph_component_count")
        contacted_component_indices = {
            component_by_island[island_id][2]
            for island_id in contacted
            if island_id in component_by_island
        }
        uncontacted = set(island_ids) - set(contacted)
        uncontacted_are_singletons = all(
            components[component_by_island[island_id][2]] == (island_id,)
            for island_id in uncontacted
            if island_id in component_by_island
        )
        expected_status = (
            "complete"
            if contacted == island_ids and bool(contacted_component_indices)
            else "uncontacted_island"
        )
        if (
            island_ids != islands_by_surface[key]
            or len(set(contacted)) != len(contacted)
            or not set(contacted) <= set(island_ids)
            or component_count != len(contacted_component_indices)
            or not uncontacted_are_singletons
            or str(raw.get("status", "")).strip() != expected_status
        ):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_EQUIVALENCE_PROOF_INVALID",
                f"the source proof does not match the island partition for {key!r}",
            )
        proof_keys.add(key)
        all_contacted_islands.update(contacted)
    if proof_keys != set(islands_by_surface):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_EQUIVALENCE_PROOF_MISSING",
            "surface-equivalence proofs do not exactly cover the geometry manifest",
        )
    for island_ids, observed_status in zip(
        components, component_contact_statuses, strict=True
    ):
        expected_status = (
            "complete"
            if set(island_ids) <= all_contacted_islands
            else "uncontacted"
        )
        if observed_status != expected_status:
            raise LayerwiseNetworkUnavailable(
                "SURFACE_EQUIVALENCE_PARTITION_INVALID",
                "a surface component contact status differs from its exact source proof",
            )
    return component_by_island, tuple(components)


def _surface_component_index(
    certificate: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    rows = _certificate_rows(
        certificate.get("surface_equivalence_components"),
        code="SURFACE_EQUIVALENCE_PARTITION_MISSING",
        label="surface-equivalence partition",
    )
    result = {
        str(raw.get("component_id", "")).strip(): raw
        for raw in rows
    }
    if not result or len(result) != len(rows) or "" in result:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_EQUIVALENCE_PARTITION_INVALID",
            "surface-equivalence component identities are blank or duplicated",
        )
    return MappingProxyType(result)


def _contacted_surface_component_ids(
    certificate: Mapping[str, Any],
    component_by_id: Mapping[str, Mapping[str, Any]],
) -> frozenset[str]:
    """Return only equivalence components proven to touch the source graph."""

    proofs = _certificate_rows(
        certificate.get("surface_equivalence_proofs"),
        code="SURFACE_EQUIVALENCE_PROOF_MISSING",
        label="surface-equivalence proof manifest",
    )
    contacted_islands: set[str] = set()
    for raw in proofs:
        raw_ids = raw.get("contacted_island_ids")
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_EQUIVALENCE_PROOF_INVALID",
                "a surface-equivalence proof has no contacted-island list",
            )
        contacted_islands.update(str(item).strip() for item in raw_ids)

    contacted_components: set[str] = set()
    for component_id, component in component_by_id.items():
        raw_ids = component.get("island_ids")
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_EQUIVALENCE_PARTITION_INVALID",
                "a surface-equivalence component has no island list",
            )
        island_ids = tuple(str(item).strip() for item in raw_ids)
        if island_ids and set(island_ids) <= contacted_islands:
            contacted_components.add(component_id)
    return frozenset(contacted_components)


def _validated_component_reference(
    raw: Mapping[str, Any],
    component_by_id: Mapping[str, Mapping[str, Any]],
    contacted_component_ids: frozenset[str],
    *,
    id_field: str,
    islands_field: str,
    representative_field: str,
    evidence_field: str,
    code: str,
    label: str,
) -> Mapping[str, Any]:
    component_id = str(raw.get(id_field) or "").strip()
    island_ids_raw = raw.get(islands_field)
    representative = str(raw.get(representative_field) or "").strip()
    evidence = str(raw.get(evidence_field) or "").strip().casefold()
    component = component_by_id.get(component_id)
    if (
        component is None
        or component_id not in contacted_component_ids
        or not isinstance(island_ids_raw, Sequence)
        or isinstance(island_ids_raw, (str, bytes))
    ):
        raise LayerwiseNetworkUnavailable(
            code, f"{label} does not identify one certified surface component"
        )
    island_ids = tuple(sorted(str(item).strip() for item in island_ids_raw))
    certified_ids_raw = component.get("island_ids")
    certified_ids = (
        tuple(sorted(str(item).strip() for item in certified_ids_raw))
        if isinstance(certified_ids_raw, Sequence)
        and not isinstance(certified_ids_raw, (str, bytes))
        else ()
    )
    if (
        not island_ids
        or len(set(island_ids)) != len(island_ids)
        or island_ids != certified_ids
        or representative
        != str(component.get("representative_island_id") or "").strip()
        or representative not in island_ids
        or evidence
        != str(component.get("component_evidence_sha256") or "").strip().casefold()
    ):
        raise LayerwiseNetworkUnavailable(
            code,
            f"{label} differs from its complete certified surface component",
        )
    return component


def _validated_terminal_contact_component(
    raw: Mapping[str, Any],
    component_by_id: Mapping[str, Mapping[str, Any]],
    contacted_component_ids: frozenset[str],
) -> Mapping[str, Any]:
    raw_ids = raw.get("contact_component_ids")
    raw_evidence = raw.get("contact_component_evidence_sha256s")
    component_id = str(raw.get("contact_component_id") or "").strip()
    evidence = str(raw.get("contact_component_evidence_sha256") or "").strip().casefold()
    if (
        not isinstance(raw_ids, Sequence)
        or isinstance(raw_ids, (str, bytes))
        or not isinstance(raw_evidence, Sequence)
        or isinstance(raw_evidence, (str, bytes))
    ):
        raise LayerwiseNetworkUnavailable(
            "TERMINAL_CONTACT_CERTIFICATE_INVALID",
            "a Device terminal contact has no aligned component identity/evidence lists",
        )
    component_ids = tuple(str(item).strip() for item in raw_ids)
    evidence_rows = tuple(str(item).strip().casefold() for item in raw_evidence)
    component = component_by_id.get(component_id)
    if (
        len(component_ids) != 1
        or len(evidence_rows) != 1
        or component_ids[0] != component_id
        or evidence_rows[0] != evidence
        or component is None
        or component_id not in contacted_component_ids
        or evidence
        != str(component.get("component_evidence_sha256") or "").strip().casefold()
    ):
        raise LayerwiseNetworkUnavailable(
            "TERMINAL_CONTACT_CERTIFICATE_INVALID",
            "a Device terminal contact does not resolve to exactly one source-contacted certified surface component",
        )
    return component


def _validated_surface_connectivity_certificate(
    project: Any,
    records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Validate the self-contained v3 island/terminal/Via certificate."""

    spd_import = project.metadata.get("spd_import")
    certificate = (
        spd_import.get("layerwise_surface_connectivity_certificate")
        if isinstance(spd_import, Mapping)
        else None
    )
    if not isinstance(certificate, Mapping):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_CERTIFICATE_MISSING",
            "reimport the source SPD so its v3 island connectivity certificate is retained",
        )
    if (
        certificate.get("schema_version") != _SURFACE_CONNECTIVITY_SCHEMA
        or certificate.get("compiler_id") != _SURFACE_CONNECTIVITY_COMPILER
    ):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED",
            "only the exact v3 island/terminal/Via-pair certificate is supported",
        )
    expected_source = str(spd_import.get("source_sha256", "")).casefold()
    source_sha256 = str(certificate.get("source_sha256", "")).casefold()
    if not _is_sha256(source_sha256) or source_sha256 != expected_source:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_SOURCE_MISMATCH",
            "the v3 certificate is not bound to the retained SPD source SHA-256",
        )
    evidence = str(certificate.get("evidence_sha256", "")).casefold()
    unsigned = {
        key: certificate[key] for key in certificate if key != "evidence_sha256"
    }
    if not _is_sha256(evidence) or _metadata_sha256_without_newline(unsigned) != evidence:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INTEGRITY_FAILED",
            "the v3 certificate evidence SHA-256 does not match",
        )
    if str(certificate.get("status", "")) != "complete":
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INCOMPLETE",
            "the v3 island/terminal/Via ownership certificate is incomplete",
        )
    failures = _certificate_rows(
        certificate.get("compile_failures"),
        code="SURFACE_CONNECTIVITY_CERTIFICATE_INVALID",
        label="compile-failure manifest",
    )
    if failures:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INCOMPLETE",
            "the v3 certificate contains unresolved rail-anchor compile failures",
        )

    assets = _certificate_rows(
        certificate.get("geometry_assets"),
        code="SURFACE_CONNECTIVITY_CERTIFICATE_INVALID",
        label="geometry-asset manifest",
    )

    def asset_identity(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
        digest = str(value.get("asset_sha256", "")).strip().casefold()
        if not _is_sha256(digest):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_CONNECTIVITY_GEOMETRY_MISMATCH",
                "a geometry-asset digest is invalid",
            )
        return (
            _key(value.get("layer", "")),
            _key(value.get("net", "")),
            str(value.get("asset", "")).strip(),
            digest,
        )

    expected_assets = sorted(asset_identity(record) for record in records)
    observed_assets = sorted(asset_identity(item) for item in assets)
    if observed_assets != expected_assets:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_GEOMETRY_MISMATCH",
            "the v3 geometry manifest differs from retained exact artwork",
        )
    _terminal_via_certificate, terminal_via_by_pin = (
        _validated_device_terminal_via_certificate(project)
    )
    retained_net_keys = {_key(item.get("net", "")) for item in assets}
    exact_device_owner_via_keys = {
        _key(raw.get("incident_via_id", ""))
        for raw in terminal_via_by_pin.values()
        if str(raw.get("status") or "").strip() == "complete"
        and _key(raw.get("incident_net", "")) in retained_net_keys
    }
    island_display, islands_by_surface = _certificate_island_inventory(certificate)
    component_by_island, _components = _surface_equivalence_partition(
        certificate, islands_by_surface
    )
    component_by_id = _surface_component_index(certificate)
    contacted_component_ids = _contacted_surface_component_ids(
        certificate, component_by_id
    )

    anchor_rows = _certificate_rows(
        certificate.get("rail_anchor_bindings"),
        code="RAIL_ANCHOR_BINDINGS_MISSING",
        label="rail-anchor binding manifest",
    )
    anchor_identities: set[tuple[str, str, str, str]] = set()
    for raw in anchor_rows:
        identity = (
            _key(raw.get("rail_id", "")),
            _key(raw.get("branch_id", "")),
            _key(raw.get("role", "")),
            _key(raw.get("pin_id", "")),
        )
        if identity[2] not in {"power", "ground"} or identity in anchor_identities:
            raise LayerwiseNetworkUnavailable(
                "RAIL_ANCHOR_BINDINGS_INVALID",
                "rail-anchor bindings have an invalid role or duplicate identity",
            )
        anchor_identities.add(identity)
    if not anchor_identities:
        raise LayerwiseNetworkUnavailable(
            "RAIL_ANCHOR_BINDINGS_MISSING", "the v3 certificate has no rail anchors"
        )

    terminal_rows = _certificate_rows(
        certificate.get("terminal_contacts"),
        code="TERMINAL_CONTACT_CERTIFICATE_MISSING",
        label="Device terminal-contact manifest",
    )
    contact_by_pin: dict[str, Mapping[str, Any]] = {}
    direct_contact_landing_keys: set[tuple[str, str]] = set()
    direct_contact_via_keys: set[str] = set()
    for raw in terminal_rows:
        pin_key = _key(raw.get("pin_id", ""))
        path_kind = str(raw.get("contact_path_kind") or "").strip().casefold()
        via_id = str(raw.get("incident_via_id") or "").strip()
        incident_net = str(raw.get("incident_net") or "").strip()
        incident_padstack = str(raw.get("incident_padstack") or "").strip()
        external = str(raw.get("external_endpoint_node_id") or "").strip()
        internal = str(raw.get("internal_endpoint_node_id") or "").strip()
        net = str(raw.get("net", "")).strip()
        component = _validated_terminal_contact_component(
            raw, component_by_id, contacted_component_ids
        )
        component_net = str(component.get("net") or "").strip()
        landing_key = (via_id.casefold(), external.casefold())
        if (
            pin_key in contact_by_pin
            or path_kind not in {"direct_via_landing", "trace_component"}
            or str(raw.get("status", "")) != "complete"
            or _key(net) != _key(component_net)
            or (
                path_kind == "direct_via_landing"
                and (
                    not via_id
                    or via_id.casefold() in direct_contact_via_keys
                    or not incident_net
                    or not incident_padstack
                    or _key(incident_net) != _key(net)
                    or not external
                    or not internal
                    or external.casefold() == internal.casefold()
                    or landing_key in direct_contact_landing_keys
                )
            )
            or (
                path_kind == "trace_component"
                and (
                    via_id
                    or incident_net
                    or incident_padstack
                    or not external
                    or bool(internal)
                )
            )
        ):
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_CONTACT_CERTIFICATE_INVALID",
                f"Device terminal {raw.get('pin_id')!r} lacks one explicit exact component path",
            )
        contact_by_pin[pin_key] = raw
        if path_kind == "direct_via_landing":
            direct_contact_landing_keys.add(landing_key)
            direct_contact_via_keys.add(via_id.casefold())
    if {identity[3] for identity in anchor_identities} - set(contact_by_pin):
        raise LayerwiseNetworkUnavailable(
            "TERMINAL_CONTACT_CERTIFICATE_MISSING",
            "one or more rail anchors have no complete terminal contact",
        )

    # Real terminal Via rows are provenance only in the substrate compiler:
    # DeviceBranch.series_path and the decap termination manifest already own
    # their R/L.  A trace-first Device source Node has no landing row and is
    # accepted only through the explicit hash-bound component path above.
    landing_rows = _certificate_rows(
        certificate.get("terminal_landing_contacts"),
        code="TERMINAL_LANDING_CERTIFICATE_MISSING",
        label="terminal landing-contact manifest",
    )
    conductor_centers = _stackup_conductor_center_depths(project)
    conductor_layers = set(conductor_centers)
    landing_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    landing_via_keys: set[str] = set()
    device_landing_via_keys: set[str] = set()
    decap_landing_via_keys: set[str] = set()
    for raw in landing_rows:
        via_id = str(raw.get("via_id") or "").strip()
        endpoint_node = str(raw.get("endpoint_node_id") or "").strip()
        external = str(raw.get("external_endpoint_node_id") or "").strip()
        internal = str(raw.get("internal_endpoint_node_id") or "").strip()
        owner_kind = str(raw.get("terminal_owner_kind") or "").strip().casefold()
        external_layer = str(raw.get("external_endpoint_layer") or "").strip()
        net = str(raw.get("net") or "").strip()
        padstack = str(raw.get("padstack") or "").strip()
        raw_landing_key = raw.get("landing_key")
        issues = raw.get("physical_model_issues")
        component_issues = raw.get("component_binding_issues")
        segments_raw = raw.get("segments")
        landing_key = (via_id.casefold(), external.casefold())
        component = _validated_component_reference(
            raw,
            component_by_id,
            contacted_component_ids,
            id_field="contact_component_id",
            islands_field="component_island_ids",
            representative_field="representative_island_id",
            evidence_field="component_evidence_sha256",
            code="TERMINAL_LANDING_CERTIFICATE_INVALID",
            label="terminal landing component",
        )
        component_net = str(component.get("net") or "").strip()
        component_layer = str(component.get("layer") or "").strip()
        try:
            drill = float(raw.get("drill_diameter_um"))
        except (TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                "a terminal landing has no positive drill diameter",
            ) from exc
        if (
            not via_id
            or via_id.casefold() in landing_via_keys
            or not endpoint_node
            or endpoint_node.casefold() != external.casefold()
            or not external
            or not internal
            or external.casefold() == internal.casefold()
            or landing_key in landing_by_key
            or not isinstance(raw_landing_key, Sequence)
            or isinstance(raw_landing_key, (str, bytes))
            or tuple(str(item).strip() for item in raw_landing_key) != landing_key
            or owner_kind not in {"device", "decap"}
            or str(raw.get("contact_path_kind") or "").strip().casefold()
            != "direct_via_landing"
            or str(raw.get("endpoint_resolution_kind") or "").strip().casefold()
            != "same_layer_trace_artwork_component"
            or str(raw.get("status") or "").strip() != "complete"
            or str(raw.get("physical_model_status") or "").strip().casefold()
            != "complete"
            or str(raw.get("component_binding_status") or "").strip().casefold()
            != "complete"
            or not isinstance(issues, Sequence)
            or isinstance(issues, (str, bytes))
            or len(issues) != 0
            or not isinstance(component_issues, Sequence)
            or isinstance(component_issues, (str, bytes))
            or len(component_issues) != 0
            or not padstack
            or isinstance(raw.get("drill_diameter_um"), bool)
            or not isfinite(drill)
            or drill <= 0.0
            or not external_layer
            or _key(external_layer) not in conductor_layers
            or _key(net) != _key(component_net)
            or _key(component_layer) not in conductor_layers
            or not isinstance(segments_raw, Sequence)
            or isinstance(segments_raw, (str, bytes))
            or not segments_raw
        ):
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_LANDING_CERTIFICATE_INVALID",
                "a terminal landing lacks exact island, ownership, or physical-path evidence",
            )
        segments: list[tuple[int, str, str, float]] = []
        for segment in segments_raw:
            if not isinstance(segment, Mapping):
                raise LayerwiseNetworkUnavailable(
                    "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                    "a terminal landing physical segment is invalid",
                )
            try:
                ordinal = int(segment.get("ordinal"))
                length_um = float(segment.get("length_um"))
            except (TypeError, ValueError) as exc:
                raise LayerwiseNetworkUnavailable(
                    "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                    "a terminal landing segment has an invalid ordinal or length",
                ) from exc
            start_layer = str(segment.get("start_layer") or "").strip()
            end_layer = str(segment.get("end_layer") or "").strip()
            if (
                isinstance(segment.get("ordinal"), bool)
                or ordinal < 0
                or not start_layer
                or not end_layer
                or _key(start_layer) not in conductor_layers
                or _key(end_layer) not in conductor_layers
                or _key(start_layer) == _key(end_layer)
                or not isfinite(length_um)
                or length_um <= 0.0
            ):
                raise LayerwiseNetworkUnavailable(
                    "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                    "a terminal landing segment is not a finite conductor span",
                )
            segments.append((ordinal, start_layer, end_layer, length_um))
        segments.sort(key=lambda item: item[0])
        if (
            tuple(item[0] for item in segments) != tuple(range(len(segments)))
            or _key(segments[0][1]) != _key(external_layer)
            or _key(segments[-1][2]) != _key(component_layer)
            or any(
                _key(first[2]) != _key(second[1])
                for first, second in zip(segments, segments[1:], strict=False)
            )
        ):
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                "terminal landing segments are not one ordered continuous endpoint path",
            )
        _validate_segment_stackup_chain(
            conductor_centers,
            segments,
            start_layer=external_layer,
            end_layer=component_layer,
        )
        landing_by_key[landing_key] = raw
        landing_via_keys.add(via_id.casefold())
        if owner_kind == "device":
            device_landing_via_keys.add(via_id.casefold())
        else:
            decap_landing_via_keys.add(via_id.casefold())

    if (
        direct_contact_via_keys != device_landing_via_keys
        or not device_landing_via_keys <= exact_device_owner_via_keys
        or exact_device_owner_via_keys.intersection(decap_landing_via_keys)
    ):
        raise LayerwiseNetworkUnavailable(
            "TERMINAL_CONTACT_LANDING_MISMATCH",
            "Device anchor contacts/landings or decap ownership conflict with exact source terminal Via IDs",
        )
    expected_terminal_owner_via_keys = (
        exact_device_owner_via_keys | decap_landing_via_keys
    )

    for pin_key, contact in contact_by_pin.items():
        if str(contact.get("contact_path_kind") or "").strip().casefold() == "trace_component":
            continue
        via_id = str(contact.get("incident_via_id") or "").strip()
        external = str(contact.get("external_endpoint_node_id") or "").strip()
        landing = landing_by_key.get((via_id.casefold(), external.casefold()))
        if (
            landing is None
            or str(landing.get("terminal_owner_kind") or "").strip().casefold()
            != "device"
            or _key(landing.get("internal_endpoint_node_id", ""))
            != _key(contact.get("internal_endpoint_node_id", ""))
            or _key(landing.get("net", "")) != _key(contact.get("net", ""))
            or _key(landing.get("padstack", ""))
            != _key(contact.get("incident_padstack", ""))
            or str(landing.get("contact_component_id") or "").strip()
            != str(contact.get("contact_component_id") or "").strip()
            or str(landing.get("component_evidence_sha256") or "").strip().casefold()
            != str(contact.get("contact_component_evidence_sha256") or "").strip().casefold()
        ):
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_CONTACT_LANDING_MISMATCH",
                f"Device terminal {contact.get('pin_id')!r} is not bound to its exact physical landing proof",
            )

    aggregates = _certificate_rows(
        certificate.get("via_island_pair_aggregates"),
        code="VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
        label="Via island-pair aggregate manifest",
    )
    aggregate_keys: set[tuple[str, str, str, str, str, str]] = set()
    aggregate_via_hashes: set[str] = set()
    paired_count = terminal_owned_count = substrate_count = 0
    for raw in aggregates:
        net = str(raw.get("net", "")).strip()
        padstack = str(raw.get("padstack", "")).strip()
        start_layer = str(raw.get("start_layer", "")).strip()
        end_layer = str(raw.get("end_layer", "")).strip()
        start_island = str(raw.get("start_island_id", "")).strip()
        end_island = str(raw.get("end_island_id", "")).strip()
        via_hash = str(raw.get("via_ids_sha256", "")).strip().casefold()
        start_component = _validated_component_reference(
            raw,
            component_by_id,
            contacted_component_ids,
            id_field="start_component_id",
            islands_field="start_component_island_ids",
            representative_field="start_island_id",
            evidence_field="start_component_evidence_sha256",
            code="VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
            label="Via aggregate start component",
        )
        end_component = _validated_component_reference(
            raw,
            component_by_id,
            contacted_component_ids,
            id_field="end_component_id",
            islands_field="end_component_island_ids",
            representative_field="end_island_id",
            evidence_field="end_component_evidence_sha256",
            code="VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
            label="Via aggregate end component",
        )
        if not all((net, padstack, start_layer, end_layer, start_island, end_island)):
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
                "a Via aggregate has a blank NET, padstack, layer, or island identity",
            )
        key = (
            _key(net), _key(padstack), _key(start_layer), _key(end_layer),
            start_island, end_island,
        )
        try:
            count = int(raw.get("count"))
            owned = int(raw.get("terminal_owned_count"))
            substrate = int(raw.get("substrate_count"))
        except (TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_CERTIFICATE_INVALID", "a Via aggregate count is invalid"
            ) from exc
        if (
            key in aggregate_keys
            or any(isinstance(raw.get(name), bool) for name in ("count", "terminal_owned_count", "substrate_count"))
            or count < 1
            or owned < 0
            or substrate < 0
            or owned + substrate != count
            or not _is_sha256(via_hash)
            or via_hash in aggregate_via_hashes
            or str(raw.get("start_component_id") or "").strip()
            == str(raw.get("end_component_id") or "").strip()
            or start_layer.casefold() == end_layer.casefold()
            or (_key(start_component.get("net", "")), _key(start_component.get("layer", "")))
            != (_key(net), _key(start_layer))
            or (_key(end_component.get("net", "")), _key(end_component.get("layer", "")))
            != (_key(net), _key(end_layer))
            or str(raw.get("component_binding_status") or "").strip().casefold()
            != "complete"
            or not isinstance(raw.get("component_binding_issues"), Sequence)
            or isinstance(raw.get("component_binding_issues"), (str, bytes))
            or len(raw.get("component_binding_issues")) != 0
        ):
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
                "a Via aggregate has invalid identity, ownership, layer, island, padstack, or hash evidence",
            )
        if substrate > 0:
            segments = raw.get("segments")
            issues = raw.get("physical_model_issues")
            drill = raw.get("drill_diameter_um")
            if (
                str(raw.get("physical_model_status", "")) != "complete"
                or not isinstance(segments, Sequence)
                or isinstance(segments, (str, bytes))
                or not segments
                or not isinstance(issues, Sequence)
                or isinstance(issues, (str, bytes))
                or len(issues) != 0
                or isinstance(drill, bool)
            ):
                raise LayerwiseNetworkUnavailable(
                    "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                    "a substrate-bearing Via aggregate lacks complete physical-model evidence",
                )
            try:
                drill_value = float(drill)
            except (TypeError, ValueError) as exc:
                raise LayerwiseNetworkUnavailable(
                    "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                    "a substrate-bearing Via aggregate has no positive drill diameter",
                ) from exc
            if not isfinite(drill_value) or drill_value <= 0.0:
                raise LayerwiseNetworkUnavailable(
                    "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                    "a substrate-bearing Via aggregate has no positive drill diameter",
                )
            ordered_segments: list[tuple[int, str, str, float]] = []
            for segment in segments:
                if not isinstance(segment, Mapping):
                    raise LayerwiseNetworkUnavailable(
                        "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                        "a substrate-bearing Via aggregate has an invalid segment row",
                    )
                try:
                    ordinal = int(segment.get("ordinal"))
                    length_um = float(segment.get("length_um"))
                except (TypeError, ValueError) as exc:
                    raise LayerwiseNetworkUnavailable(
                        "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                        "a substrate-bearing Via aggregate has an invalid segment length",
                    ) from exc
                segment_start = str(segment.get("start_layer") or "").strip()
                segment_end = str(segment.get("end_layer") or "").strip()
                if (
                    isinstance(segment.get("ordinal"), bool)
                    or ordinal < 0
                    or not segment_start
                    or not segment_end
                    or _key(segment_start) not in conductor_layers
                    or _key(segment_end) not in conductor_layers
                    or _key(segment_start) == _key(segment_end)
                    or not isfinite(length_um)
                    or length_um <= 0.0
                ):
                    raise LayerwiseNetworkUnavailable(
                        "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                        "a substrate-bearing Via aggregate has an invalid conductor segment",
                    )
                ordered_segments.append(
                    (ordinal, segment_start, segment_end, length_um)
                )
            _validate_segment_stackup_chain(
                conductor_centers,
                ordered_segments,
                start_layer=start_layer,
                end_layer=end_layer,
            )
        aggregate_keys.add(key)
        aggregate_via_hashes.add(via_hash)
        paired_count += count
        terminal_owned_count += owned
        substrate_count += substrate

    coverage = certificate.get("via_island_pair_coverage")
    if not isinstance(coverage, Mapping):
        raise LayerwiseNetworkUnavailable(
            "VIA_ISLAND_PAIR_COVERAGE_MISSING", "the v3 Via coverage partition is absent"
        )
    count_fields = (
        "raw_target_via_count", "model_relevant_via_count", "paired_via_count",
        "terminal_owned_unpaired_count", "unsupported_missing_endpoint_count",
        "outside_retained_interface_scope_count", "terminal_owned_declared_count",
        "terminal_owned_observed_count", "paired_terminal_owned_count", "paired_substrate_count",
    )
    try:
        coverage_counts = {name: int(coverage.get(name)) for name in count_fields}
    except (TypeError, ValueError) as exc:
        raise LayerwiseNetworkUnavailable(
            "VIA_ISLAND_PAIR_COVERAGE_INVALID", "the v3 Via coverage counts are invalid"
        ) from exc
    empty_ids_sha256 = sha256(b"").hexdigest()
    if (
        any(isinstance(coverage.get(name), bool) or value < 0 for name, value in coverage_counts.items())
        or coverage_counts["raw_target_via_count"]
        != coverage_counts["model_relevant_via_count"]
        + coverage_counts["outside_retained_interface_scope_count"]
        or coverage_counts["model_relevant_via_count"]
        != coverage_counts["paired_via_count"]
        + coverage_counts["terminal_owned_unpaired_count"]
        + coverage_counts["unsupported_missing_endpoint_count"]
        or coverage_counts["paired_via_count"] != paired_count
        or coverage_counts["paired_via_count"]
        != coverage_counts["paired_terminal_owned_count"]
        + coverage_counts["paired_substrate_count"]
        or coverage_counts["paired_terminal_owned_count"] != terminal_owned_count
        or coverage_counts["paired_substrate_count"] != substrate_count
        or coverage_counts["terminal_owned_declared_count"]
        != coverage_counts["terminal_owned_observed_count"]
        or coverage_counts["terminal_owned_observed_count"]
        != coverage_counts["paired_terminal_owned_count"]
        + coverage_counts["terminal_owned_unpaired_count"]
        or coverage_counts["terminal_owned_declared_count"]
        != len(expected_terminal_owner_via_keys)
        or coverage_counts["unsupported_missing_endpoint_count"] != 0
        or not bool(coverage.get("terminal_owned_ids_supplied"))
        or str(coverage.get("status", "")) != "complete"
        or not _is_sha256(coverage.get("terminal_owned_unpaired_via_ids_sha256"))
        or not _is_sha256(coverage.get("unsupported_missing_endpoint_via_ids_sha256"))
        or not _is_sha256(coverage.get("outside_retained_interface_scope_via_ids_sha256"))
        or (
            coverage_counts["terminal_owned_unpaired_count"] == 0
            and str(
                coverage.get("terminal_owned_unpaired_via_ids_sha256", "")
            ).casefold()
            != empty_ids_sha256
        )
        or (
            coverage_counts["unsupported_missing_endpoint_count"] == 0
            and str(
                coverage.get("unsupported_missing_endpoint_via_ids_sha256", "")
            ).casefold()
            != empty_ids_sha256
        )
        or (
            coverage_counts["outside_retained_interface_scope_count"] == 0
            and str(
                coverage.get("outside_retained_interface_scope_via_ids_sha256", "")
            ).casefold()
            != empty_ids_sha256
        )
    ):
        raise LayerwiseNetworkUnavailable(
            "VIA_ISLAND_PAIR_COVERAGE_INVALID",
            "the v3 Via coverage does not exactly partition model-relevant and outside-interface Vias",
        )
    return certificate


def _surface_connectivity_disclosure_counts(
    surface_nodes: set[str],
    certificate: Mapping[str, Any],
) -> Mapping[str, int]:
    """Validate/disclose raw cross-layer components without stamping them.

    The v2 island proof permits coalescing disconnected artwork islands only
    within one physical ``(layer, NET)`` surface.  A raw Trace/Via component
    spanning layers is useful provenance, but collapsing it as an ideal link
    would bypass the ownership-safe finite Via R/L path and double stamp the
    same conductor.  Source Trace width is unavailable, so no finite Trace R/L
    is inferred here either.
    """

    component_ids: set[str] = set()
    component_shapes: set[tuple[str, tuple[str, ...]]] = set()
    single_surface = 0
    for raw in certificate.get("components", ()):
        if not isinstance(raw, Mapping):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_CONNECTIVITY_COMPONENT_INVALID",
                "a surface-connectivity component is not a mapping",
            )
        component_id = str(raw.get("component_id", "")).strip()
        net = str(raw.get("net", "")).strip()
        layers_raw = raw.get("layers")
        if (
            not component_id
            or component_id in component_ids
            or not net
            or not isinstance(layers_raw, Sequence)
            or isinstance(layers_raw, (str, bytes))
        ):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_CONNECTIVITY_COMPONENT_INVALID",
                "a component has a blank/duplicate identity, NET, or layer list",
            )
        component_ids.add(component_id)
        layers = tuple(str(layer).strip() for layer in layers_raw)
        if any(not layer for layer in layers) or len({_key(layer) for layer in layers}) != len(layers):
            raise LayerwiseNetworkUnavailable(
                "SURFACE_CONNECTIVITY_COMPONENT_INVALID",
                f"component {component_id!r} has blank or duplicate layers",
            )
        shape = (_key(net), tuple(sorted((_key(layer) for layer in layers))))
        if shape in component_shapes:
            raise LayerwiseNetworkUnavailable(
                "SURFACE_CONNECTIVITY_COMPONENT_AMBIGUOUS",
                f"component {component_id!r} duplicates another NET/layer contact set",
            )
        component_shapes.add(shape)
        nodes = tuple(_surface_node_id(layer, net) for layer in layers)
        missing = [node for node in nodes if node not in surface_nodes]
        if missing:
            raise LayerwiseNetworkUnavailable(
                "SURFACE_CONNECTIVITY_SURFACE_MISSING",
                f"component {component_id!r} references non-retained surface(s): {missing[:3]!r}",
            )
        if len(nodes) < 2:
            single_surface += 1
    return MappingProxyType(
        {
            "component_count": len(component_ids),
            "multi_surface_component_count": len(component_ids) - single_surface,
            "single_surface_component_count": single_surface,
            "cross_layer_ideal_link_count": 0,
        }
    )


def _stackup_ground_net(project: Any, rail: Any, surface_nodes: set[str]) -> str:
    configured = getattr(getattr(rail, "mixed_reference_certificate", None), "gnd_net", None)
    aliases = {_key(item): str(item) for item in project.gnd_aliases}
    layer = next(
        (
            item
            for item in project.stackup_layers
            if _key(item.name) == _key(rail.gnd_layer)
        ),
        None,
    )
    layer_nets = tuple(getattr(layer, "pwr_nets", ())) if layer is not None else ()
    candidates = [
        aliases[_key(net)]
        for net in layer_nets
        if _key(net) in aliases
        and (configured is None or _key(net) == _key(configured))
        and _surface_node_id(rail.gnd_layer, net) in surface_nodes
    ]
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) == 1:
        return candidates[0]
    # Some persisted projects predate per-layer ``pwr_nets`` completeness.  The
    # exact retained surface index is still authoritative when it identifies a
    # single configured reference on the selected physical layer.
    retained_candidates = [
        display
        for key, display in aliases.items()
        if (configured is None or key == _key(configured))
        and _surface_node_id(rail.gnd_layer, display) in surface_nodes
    ]
    retained_candidates = list(dict.fromkeys(retained_candidates))
    if len(retained_candidates) == 1:
        return retained_candidates[0]
    raise LayerwiseNetworkUnavailable(
        "GROUND_SURFACE_UNRESOLVED",
        f"rail {rail.rail_id!r} needs exactly one retained configured-GND surface on {rail.gnd_layer!r}",
    )


def _resolved_rail_port_manifest(
    project: Any,
    surface_nodes: set[str],
    *,
    required_rail_id: str | None = None,
) -> tuple[tuple[Mapping[str, str], ...], tuple[str, ...]]:
    """Bind supported ports while keeping the requested rail fail-closed.

    A project can retain historical or otherwise unrelated rail declarations
    whose exact artwork was not imported.  They must not block evaluation of a
    different, fully supported rail.  The requested rail remains strict; every
    omitted unrelated identity is returned for cache/provenance disclosure.
    """

    manifest: list[Mapping[str, str]] = []
    omitted: list[str] = []
    seen: set[str] = set()
    required_key = _key(required_rail_id) if required_rail_id is not None else None
    rails = tuple(getattr(project, "rails", ()))
    if not rails:
        raise LayerwiseNetworkUnavailable(
            "RAIL_PORT_MISSING", "the project has no rail ports to compile"
        )
    for rail in sorted(
        rails,
        key=lambda item: (
            str(getattr(item, "rail_id", "")).casefold(),
            str(getattr(item, "rail_id", "")),
        ),
    ):
        rail_id = str(getattr(rail, "rail_id", "")).strip()
        rail_key = _key(rail_id)
        if rail_key in seen:
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_AMBIGUOUS", f"rail identity {rail_id!r} is duplicated"
            )
        seen.add(rail_key)
        try:
            net = str(getattr(rail, "net", "")).strip()
            pwr_layer = str(getattr(rail, "pwr_layer", "")).strip()
            gnd_layer = str(getattr(rail, "gnd_layer", "")).strip()
            positive = _surface_node_id(pwr_layer, net)
            if positive not in surface_nodes:
                raise LayerwiseNetworkUnavailable(
                    "PWR_SURFACE_MISSING",
                    f"rail {rail_id!r} has no retained {pwr_layer}/{net} surface",
                )
            reference_net = _stackup_ground_net(project, rail, surface_nodes)
            negative = _surface_node_id(gnd_layer, reference_net)
            if negative not in surface_nodes:  # Defensive: resolver already checks.
                raise LayerwiseNetworkUnavailable(
                    "GROUND_SURFACE_UNRESOLVED",
                    f"rail {rail_id!r} has no retained {gnd_layer}/{reference_net} surface",
                )
        except LayerwiseNetworkUnavailable:
            if rail_key == required_key:
                raise
            omitted.append(rail_id)
            continue
        manifest.append(
            MappingProxyType(
                {
                    "rail_id": rail_id,
                    "net": net,
                    "pwr_layer": pwr_layer,
                    "gnd_layer": gnd_layer,
                    "reference_net": reference_net,
                }
            )
        )
    if required_key is not None and not any(
        _key(item["rail_id"]) == required_key for item in manifest
    ):
        raise LayerwiseNetworkUnavailable(
            "RAIL_PORT_MISSING",
            f"requested rail {required_rail_id!r} has no compiled layer-surface port",
        )
    return tuple(manifest), tuple(sorted(omitted, key=lambda item: (item.casefold(), item)))


def _stackup_ground_net_v3(
    project: Any,
    rail: Any,
    islands_by_surface: Mapping[tuple[str, str], tuple[str, ...]],
) -> str:
    configured = getattr(
        getattr(rail, "mixed_reference_certificate", None), "gnd_net", None
    )
    aliases = {_key(item): str(item) for item in project.gnd_aliases}
    layer = next(
        (
            item
            for item in project.stackup_layers
            if _key(item.name) == _key(rail.gnd_layer)
        ),
        None,
    )
    layer_nets = tuple(getattr(layer, "pwr_nets", ())) if layer is not None else ()
    candidates = [
        aliases[_key(net)]
        for net in layer_nets
        if _key(net) in aliases
        and (configured is None or _key(net) == _key(configured))
        and (_key(net), _key(rail.gnd_layer)) in islands_by_surface
    ]
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) == 1:
        return candidates[0]
    retained = [
        display
        for key, display in aliases.items()
        if (configured is None or key == _key(configured))
        and (key, _key(rail.gnd_layer)) in islands_by_surface
    ]
    retained = list(dict.fromkeys(retained))
    if len(retained) == 1:
        return retained[0]
    raise LayerwiseNetworkUnavailable(
        "GROUND_SURFACE_UNRESOLVED",
        f"rail {rail.rail_id!r} needs exactly one certified reference NET on {rail.gnd_layer!r}",
    )


def _resolved_v3_rail_port_manifest(
    project: Any,
    certificate: Mapping[str, Any],
    component_by_island: Mapping[str, tuple[str, str, int]],
    islands_by_surface: Mapping[tuple[str, str], tuple[str, ...]],
    *,
    required_rail_id: str | None = None,
) -> tuple[tuple[Mapping[str, str], ...], tuple[str, ...]]:
    anchors = _certificate_rows(
        certificate.get("rail_anchor_bindings"),
        code="RAIL_ANCHOR_BINDINGS_MISSING",
        label="rail-anchor binding manifest",
    )
    contacts = _certificate_rows(
        certificate.get("terminal_contacts"),
        code="TERMINAL_CONTACT_CERTIFICATE_MISSING",
        label="Device terminal-contact manifest",
    )
    component_by_id = _surface_component_index(certificate)
    contacted_component_ids = _contacted_surface_component_ids(
        certificate, component_by_id
    )
    contact_by_pin = {_key(item.get("pin_id", "")): item for item in contacts}
    anchors_by_rail: dict[str, list[Mapping[str, Any]]] = {}
    for raw in anchors:
        anchors_by_rail.setdefault(_key(raw.get("rail_id", "")), []).append(raw)

    required_key = _key(required_rail_id) if required_rail_id is not None else None
    manifest: list[Mapping[str, str]] = []
    omitted: list[str] = []
    seen_rails: set[str] = set()
    for rail in sorted(
        tuple(getattr(project, "rails", ())),
        key=lambda item: (
            str(getattr(item, "rail_id", "")).casefold(),
            str(getattr(item, "rail_id", "")),
        ),
    ):
        rail_id = str(getattr(rail, "rail_id", "")).strip()
        rail_key = _key(rail_id)
        if rail_key in seen_rails:
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_AMBIGUOUS", f"rail identity {rail_id!r} is duplicated"
            )
        seen_rails.add(rail_key)
        try:
            net = str(getattr(rail, "net", "")).strip()
            pwr_layer = str(getattr(rail, "pwr_layer", "")).strip()
            gnd_layer = str(getattr(rail, "gnd_layer", "")).strip()
            if (_key(net), _key(pwr_layer)) not in islands_by_surface:
                raise LayerwiseNetworkUnavailable(
                    "PWR_SURFACE_MISSING",
                    f"rail {rail_id!r} has no certified {pwr_layer}/{net} islands",
                )
            reference_net = _stackup_ground_net_v3(
                project, rail, islands_by_surface
            )
            anchor_rows = anchors_by_rail.get(rail_key, ())
            if not anchor_rows:
                raise LayerwiseNetworkUnavailable(
                    "RAIL_ANCHOR_BINDINGS_MISSING",
                    f"rail {rail_id!r} has no v3 Device anchor bindings",
                )
            by_branch: dict[str, dict[str, Mapping[str, Any]]] = {}
            for raw in anchor_rows:
                branch_key = _key(raw.get("branch_id", ""))
                role = _key(raw.get("role", ""))
                branch = by_branch.setdefault(branch_key, {})
                if role in branch:
                    raise LayerwiseNetworkUnavailable(
                        "RAIL_ANCHOR_BINDINGS_INVALID",
                        f"rail {rail_id!r} branch {raw.get('branch_id')!r} duplicates {role!r}",
                    )
                branch[role] = raw
            if any(set(branch) != {"power", "ground"} for branch in by_branch.values()):
                raise LayerwiseNetworkUnavailable(
                    "RAIL_ANCHOR_BINDINGS_INVALID",
                    f"rail {rail_id!r} does not have one PWR/GND anchor pair per branch",
                )

            role_contacts: dict[str, list[Mapping[str, Any]]] = {
                "power": [], "ground": []
            }
            for branch in by_branch.values():
                for role in ("power", "ground"):
                    pin_key = _key(branch[role].get("pin_id", ""))
                    contact = contact_by_pin.get(pin_key)
                    if contact is None:
                        raise LayerwiseNetworkUnavailable(
                            "TERMINAL_CONTACT_CERTIFICATE_MISSING",
                            f"rail {rail_id!r} anchor {branch[role].get('pin_id')!r} has no contact",
                        )
                    role_contacts[role].append(contact)

            expected = {
                "power": (_key(net), _key(pwr_layer)),
                "ground": (_key(reference_net), _key(gnd_layer)),
            }
            representative_islands: dict[str, str] = {}
            selected_component_ids: dict[str, str] = {}
            selected_component_evidence: dict[str, str] = {}
            for role, rows in role_contacts.items():
                component_ids: set[str] = set()
                for contact in rows:
                    contact_net = str(contact.get("net", "")).strip()
                    certified = _validated_terminal_contact_component(
                        contact, component_by_id, contacted_component_ids
                    )
                    component_id = str(
                        contact.get("contact_component_id") or ""
                    ).strip()
                    component_net = str(certified.get("net") or "").strip()
                    component_layer = str(certified.get("layer") or "").strip()
                    representative = str(
                        certified.get("representative_island_id") or ""
                    ).strip()
                    if (
                        _key(contact_net) != _key(component_net)
                        or (_key(component_net), _key(component_layer))
                        != expected[role]
                    ):
                        raise LayerwiseNetworkUnavailable(
                            "RAIL_PORT_TERMINAL_IDENTITY_MISMATCH",
                            f"rail {rail_id!r} {role} terminal does not match its logical NET/layer",
                        )
                    component = component_by_island.get(representative)
                    if component is None or component[:2] != expected[role]:
                        raise LayerwiseNetworkUnavailable(
                            "RAIL_PORT_TERMINAL_IDENTITY_MISMATCH",
                            f"rail {rail_id!r} {role} representative island is not certified",
                        )
                    component_ids.add(component_id)
                if len(component_ids) != 1:
                    raise LayerwiseNetworkUnavailable(
                        "RAIL_PORT_MULTI_ISLAND_UNSUPPORTED",
                        f"rail {rail_id!r} {role} anchors land on more than one same-layer equivalence component",
                    )
                selected_component_id = next(iter(component_ids))
                selected_component = component_by_id[selected_component_id]
                representative_islands[role] = str(
                    selected_component.get("representative_island_id") or ""
                ).strip()
                selected_component_ids[role] = selected_component_id
                selected_component_evidence[role] = str(
                    selected_component.get("component_evidence_sha256") or ""
                ).strip().casefold()
            manifest.append(
                MappingProxyType(
                    {
                        "rail_id": rail_id,
                        "net": net,
                        "pwr_layer": pwr_layer,
                        "gnd_layer": gnd_layer,
                        "reference_net": reference_net,
                        "positive_node_id": representative_islands["power"],
                        "negative_node_id": representative_islands["ground"],
                        "positive_island_id": representative_islands["power"],
                        "negative_island_id": representative_islands["ground"],
                        "positive_component_id": selected_component_ids["power"],
                        "negative_component_id": selected_component_ids["ground"],
                        "positive_component_evidence_sha256": selected_component_evidence["power"],
                        "negative_component_evidence_sha256": selected_component_evidence["ground"],
                    }
                )
            )
        except LayerwiseNetworkUnavailable:
            if rail_key == required_key:
                raise
            omitted.append(rail_id)
    if required_key is not None and not any(
        _key(item["rail_id"]) == required_key for item in manifest
    ):
        raise LayerwiseNetworkUnavailable(
            "RAIL_PORT_MISSING",
            f"requested rail {required_rail_id!r} has no compiled v3 Device port",
        )
    return tuple(manifest), tuple(
        sorted(omitted, key=lambda item: (item.casefold(), item))
    )


def _compile_surface_equivalence_links(
    certificate: Mapping[str, Any],
    island_nodes: set[str],
) -> tuple[tuple[LayerSurfaceViaLink, ...], Mapping[str, int]]:
    _display, islands_by_surface = _certificate_island_inventory(certificate)
    _component_by_island, components = _surface_equivalence_partition(
        certificate, islands_by_surface
    )
    if set(_component_by_island) != island_nodes:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_EQUIVALENCE_PARTITION_INCOMPLETE",
            "the equivalence partition and compiled island-node inventory differ",
        )
    links: list[LayerSurfaceViaLink] = []
    multi_island_components = 0
    for component in components:
        if len(component) < 2:
            continue
        multi_island_components += 1
        anchor = component[0]
        component_sha256 = sha256(_canonical_json(list(component))).hexdigest()
        for ordinal, island_id in enumerate(component[1:], start=1):
            links.append(
                LayerSurfaceViaLink(
                    link_id=f"same-layer-equivalence:{component_sha256[:24]}:{ordinal}",
                    first_node_id=anchor,
                    second_node_id=island_id,
                    count=1,
                    mode="topology_only_ideal",
                    owner_ids=(
                        f"surface-equivalence:{component_sha256}:{ordinal}",
                    ),
                )
            )
    return tuple(links), MappingProxyType(
        {
            "component_count": len(components),
            "multi_island_component_count": multi_island_components,
            "singleton_component_count": len(components) - multi_island_components,
            "same_layer_ideal_link_count": len(links),
            "cross_layer_ideal_link_count": 0,
        }
    )


def _compile_via_links(
    project: Any,
    surface_nodes: set[str],
    certificate: Mapping[str, Any],
) -> tuple[tuple[LayerSurfaceViaLink, ...], Mapping[str, int]]:
    """Compile only ownership-safe finite Via R/L between retained surfaces.

    Consecutive padstack segments whose intermediate conductor has no retained
    same-NET artwork are combined in series between the nearest retained
    surfaces.  No raw Trace/Via graph component or failed numerical estimate is
    converted to an ideal cross-layer short.
    """

    links: list[LayerSurfaceViaLink] = []
    skipped_missing_surface = 0
    skipped_invalid = 0
    skipped_incomplete_group = 0
    skipped_unresolved_ownership = 0
    skipped_zero_substrate = 0
    finite_count = 0
    finite_group_count = 0
    groups = certificate.get("groups", ())
    for group in groups:
        if not isinstance(group, Mapping):
            skipped_invalid += 1
            continue
        net = str(group.get("net", ""))
        try:
            source_count = int(group.get("count"))
        except (TypeError, ValueError):
            skipped_invalid += 1
            continue
        if not net.strip() or source_count < 1:
            skipped_invalid += 1
            continue
        if str(group.get("status", "")) != "complete":
            skipped_incomplete_group += 1
            continue
        ownership_complete = str(group.get("ownership_status", "")) == "complete"
        if not ownership_complete:
            skipped_unresolved_ownership += 1
            continue
        substrate_count_raw = group.get("substrate_count")
        if (
            not isinstance(substrate_count_raw, int)
            or isinstance(substrate_count_raw, bool)
            or substrate_count_raw < 0
            or substrate_count_raw > source_count
        ):
            skipped_invalid += 1
            continue
        substrate_count = int(substrate_count_raw)
        if substrate_count == 0:
            skipped_zero_substrate += 1
            continue
        drill = group.get("drill_diameter_um")
        material = group.get("material")
        segments = group.get("segments", ())
        group_id = str(group.get("group_id", "")).strip()
        group_owner_id = str(group.get("owner_id", "")).strip()
        if (
            not group_id
            or not group_owner_id
            or not isinstance(segments, Sequence)
            or isinstance(segments, (str, bytes))
            or not segments
        ):
            skipped_invalid += 1
            continue
        ordered_segments: list[
            tuple[int, str, str, str, float, float]
        ] = []
        group_invalid = False
        for segment in segments:
            if not isinstance(segment, Mapping):
                group_invalid = True
                break
            try:
                ordinal = int(segment.get("ordinal"))
            except (TypeError, ValueError):
                group_invalid = True
                break
            start_layer = str(segment.get("start_layer", "")).strip()
            end_layer = str(segment.get("end_layer", "")).strip()
            segment_id = str(segment.get("segment_id", "")).strip()
            if ordinal < 0 or not start_layer or not end_layer or not segment_id:
                group_invalid = True
                break
            try:
                estimate = estimate_via_segment_rl(
                    length_um=segment.get("length_um"),
                    drill_diameter_um=drill,
                    padstack_material=(None if material is None else str(material)),
                    start_layer=start_layer,
                    end_layer=end_layer,
                    stackup_layers=project.stackup_layers,
                )
            except (ViaModelError, TypeError, ValueError):
                group_invalid = True
                break
            ordered_segments.append(
                (
                    ordinal,
                    start_layer,
                    end_layer,
                    segment_id,
                    float(estimate.resistance_ohm),
                    float(estimate.inductance_h),
                )
            )
        ordered_segments.sort(key=lambda item: item[0])
        if (
            group_invalid
            or tuple(item[0] for item in ordered_segments)
            != tuple(range(len(ordered_segments)))
            or any(
                _key(first[2]) != _key(second[1])
                for first, second in zip(
                    ordered_segments, ordered_segments[1:], strict=False
                )
            )
        ):
            skipped_invalid += 1
            continue

        layer_path = [ordered_segments[0][1]] + [
            item[2] for item in ordered_segments
        ]
        retained_positions = [
            index
            for index, layer in enumerate(layer_path)
            if _surface_node_id(layer, net) in surface_nodes
        ]
        if len(retained_positions) < 2:
            skipped_missing_surface += 1
            continue
        group_links: list[LayerSurfaceViaLink] = []
        for first_position, second_position in zip(
            retained_positions, retained_positions[1:], strict=False
        ):
            if second_position <= first_position:
                group_invalid = True
                break
            run = ordered_segments[first_position:second_position]
            group_links.append(
                LayerSurfaceViaLink(
                    link_id=(
                        f"finite:{group_id}:{first_position}-{second_position}"
                    ),
                    first_node_id=_surface_node_id(
                        layer_path[first_position], net
                    ),
                    second_node_id=_surface_node_id(
                        layer_path[second_position], net
                    ),
                    count=substrate_count,
                    mode="finite_parallel_rl",
                    resistance_ohm_per_via=sum(item[4] for item in run),
                    inductance_h_per_via=sum(item[5] for item in run),
                    owner_ids=tuple(
                        f"{group_owner_id}|{item[3]}" for item in run
                    ),
                )
            )
        if group_invalid or not group_links:
            skipped_invalid += 1
            continue
        links.extend(group_links)
        finite_count += len(group_links)
        finite_group_count += 1
    if not links:
        raise LayerwiseNetworkUnavailable(
            "FINITE_VIA_TOPOLOGY_UNAVAILABLE",
            "no complete ownership-safe Via group joins two retained physical layer surfaces with finite R/L",
        )
    return tuple(links), MappingProxyType(
        {
            "finite_parallel_rl": finite_count,
            "finite_group_count": finite_group_count,
            "topology_only_ideal": 0,
            "skipped_missing_surface": skipped_missing_surface,
            "skipped_invalid": skipped_invalid,
            "skipped_incomplete_group": skipped_incomplete_group,
            "skipped_unresolved_ownership": skipped_unresolved_ownership,
            "skipped_zero_substrate": skipped_zero_substrate,
        }
    )


def _compile_v3_via_island_links(
    project: Any,
    island_nodes: set[str],
    certificate: Mapping[str, Any],
) -> tuple[tuple[LayerSurfaceViaLink, ...], Mapping[str, int]]:
    """Stamp only v3 ownership-resolved substrate Vias between exact islands."""

    aggregates = _certificate_rows(
        certificate.get("via_island_pair_aggregates"),
        code="VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
        label="Via island-pair aggregate manifest",
    )
    conductor_centers = _stackup_conductor_center_depths(project)
    conductor_layers = set(conductor_centers)
    links: list[LayerSurfaceViaLink] = []
    zero_substrate_count = 0
    terminal_owned_via_count = 0
    substrate_via_count = 0
    seen_hashes: set[str] = set()
    for raw in aggregates:
        start_island = str(raw.get("start_island_id", "")).strip()
        end_island = str(raw.get("end_island_id", "")).strip()
        start_layer = str(raw.get("start_layer", "")).strip()
        end_layer = str(raw.get("end_layer", "")).strip()
        via_hash = str(raw.get("via_ids_sha256", "")).strip().casefold()
        try:
            count = int(raw.get("count"))
            terminal_owned = int(raw.get("terminal_owned_count"))
            substrate_count = int(raw.get("substrate_count"))
        except (TypeError, ValueError) as exc:  # validator should already catch
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
                "a v3 Via island-pair aggregate has an invalid count",
            ) from exc
        if (
            start_island not in island_nodes
            or end_island not in island_nodes
            or start_island == end_island
            or via_hash in seen_hashes
            or terminal_owned + substrate_count != count
        ):
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_CERTIFICATE_INVALID",
                "a v3 Via island-pair aggregate duplicates ownership or references an unknown island",
            )
        seen_hashes.add(via_hash)
        terminal_owned_via_count += terminal_owned
        if substrate_count == 0:
            zero_substrate_count += 1
            continue
        if str(raw.get("physical_model_status", "")) != "complete":
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                "a substrate-bearing Via aggregate has incomplete physical evidence",
            )
        segments_raw = _certificate_rows(
            raw.get("segments"),
            code="VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
            label="Via physical segment manifest",
        )
        if not segments_raw:
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                "a substrate-bearing Via aggregate has no physical segment",
            )
        ordered: list[tuple[int, str, str, float]] = []
        for segment in segments_raw:
            try:
                ordinal = int(segment.get("ordinal"))
                length_um = float(segment.get("length_um"))
            except (TypeError, ValueError) as exc:
                raise LayerwiseNetworkUnavailable(
                    "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                    "a Via physical segment has an invalid ordinal or length",
                ) from exc
            segment_start = str(segment.get("start_layer", "")).strip()
            segment_end = str(segment.get("end_layer", "")).strip()
            if (
                isinstance(segment.get("ordinal"), bool)
                or ordinal < 0
                or not isfinite(length_um)
                or length_um <= 0.0
                or _key(segment_start) not in conductor_layers
                or _key(segment_end) not in conductor_layers
                or _key(segment_start) == _key(segment_end)
            ):
                raise LayerwiseNetworkUnavailable(
                    "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                    "a Via physical segment is not a finite cross-layer conductor span",
                )
            ordered.append((ordinal, segment_start, segment_end, length_um))
            _validate_segment_stackup_length(
                conductor_centers, segment_start, segment_end, length_um
            )
        ordered.sort(key=lambda item: item[0])
        if (
            tuple(item[0] for item in ordered) != tuple(range(len(ordered)))
            or _key(ordered[0][1]) != _key(start_layer)
            or _key(ordered[-1][2]) != _key(end_layer)
            or any(
                _key(first[2]) != _key(second[1])
                for first, second in zip(ordered, ordered[1:], strict=False)
            )
        ):
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                "Via physical segments are not one ordered continuous endpoint path",
            )
        _validate_segment_stackup_chain(
            conductor_centers,
            ordered,
            start_layer=start_layer,
            end_layer=end_layer,
        )
        try:
            drill = float(raw.get("drill_diameter_um"))
        except (TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INCOMPLETE",
                "a substrate-bearing Via aggregate has no drill diameter",
            ) from exc
        material = str(raw.get("material") or "").strip() or None
        resistance = 0.0
        inductance = 0.0
        try:
            for _ordinal, segment_start, segment_end, length_um in ordered:
                estimate = estimate_via_segment_rl(
                    length_um=length_um,
                    drill_diameter_um=drill,
                    padstack_material=material,
                    start_layer=segment_start,
                    end_layer=segment_end,
                    stackup_layers=project.stackup_layers,
                )
                resistance += float(estimate.resistance_ohm)
                inductance += float(estimate.inductance_h)
        except (ViaModelError, TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "VIA_ISLAND_PAIR_PHYSICAL_MODEL_INVALID",
                "source Via physical evidence cannot produce a finite passive R/L path",
            ) from exc
        identity = sha256(
            _canonical_json(
                {
                    "net": _key(raw.get("net", "")),
                    "padstack": _key(raw.get("padstack", "")),
                    "start_layer": _key(start_layer),
                    "end_layer": _key(end_layer),
                    "start_component_id": str(raw.get("start_component_id") or ""),
                    "end_component_id": str(raw.get("end_component_id") or ""),
                    "start_component_evidence_sha256": str(
                        raw.get("start_component_evidence_sha256") or ""
                    ).casefold(),
                    "end_component_evidence_sha256": str(
                        raw.get("end_component_evidence_sha256") or ""
                    ).casefold(),
                    "start_island_id": start_island,
                    "end_island_id": end_island,
                    "via_ids_sha256": via_hash,
                }
            )
        ).hexdigest()
        links.append(
            LayerSurfaceViaLink(
                link_id=f"finite-island-pair:{identity[:24]}",
                first_node_id=start_island,
                second_node_id=end_island,
                count=substrate_count,
                mode="finite_parallel_rl",
                resistance_ohm_per_via=resistance,
                inductance_h_per_via=inductance,
                owner_ids=(f"v3-substrate-vias:{via_hash}",),
            )
        )
        substrate_via_count += substrate_count
    return tuple(links), MappingProxyType(
        {
            "aggregate_count": len(aggregates),
            "finite_parallel_rl": len(links),
            "finite_group_count": len(links),
            "topology_only_ideal": 0,
            "substrate_via_count": substrate_via_count,
            "terminal_owned_via_count": terminal_owned_via_count,
            "zero_substrate_aggregate_count": zero_substrate_count,
        }
    )


def _substrate_identity(
    project: Any,
    records: Sequence[Mapping[str, Any]],
    blocks: Sequence[Sequence[str]],
    *,
    rail_port_manifest: Sequence[Mapping[str, str]] | None = None,
    omitted_rail_ids: Sequence[str] | None = None,
    raw_spatial_manifest_sha256: str | None = None,
) -> tuple[str, str, str, str]:
    spd_import = project.metadata.get("spd_import", {})
    source_sha256 = str(spd_import.get("source_sha256", "")).lower()
    if len(source_sha256) != 64:
        raise LayerwiseNetworkUnavailable(
            "SOURCE_HASH_MISSING", "the retained SPD source SHA-256 is absent"
        )
    geometry_manifest = sorted(
        (
            _key(record.get("layer", "")),
            _key(record.get("net", "")),
            str(record.get("asset_sha256", "")).lower(),
        )
        for record in records
    )
    geometry_sha256 = sha256(_canonical_json(geometry_manifest)).hexdigest()
    material_manifest = [
        {
            "name": _key(layer.name),
            "thickness_um": float(layer.thickness_um),
            "conductivity_s_m": (
                float(layer.conductivity_s_m)
                if layer.conductivity_s_m is not None
                else None
            ),
            "dk": float(layer.dk) if layer.dk is not None else None,
            "df": float(layer.df) if layer.df is not None else None,
            "properties": [
                (float(point.frequency_hz), float(point.dk), float(point.df))
                for point in getattr(layer, "dielectric_properties", ())
            ],
        }
        for layer in project.stackup_layers
    ]
    material_sha256 = sha256(_canonical_json(material_manifest)).hexdigest()
    static_sha256 = solver_profile_static_identity_sha256(
        "layerwise_admittance_v1"
    )
    ground_aliases = tuple(_key(alias) for alias in project.gnd_aliases)
    if not ground_aliases or len(ground_aliases) != len(set(ground_aliases)):
        raise LayerwiseNetworkUnavailable(
            "GROUND_ALIAS_IDENTITY_INVALID",
            "configured GND aliases must be non-empty and unique",
        )
    ground_alias_manifest = {
        "reference": ground_aliases[0],
        "members": sorted(ground_aliases),
    }
    surface_certificate = _validated_surface_connectivity_certificate(
        project, records
    )
    surface_evidence_sha256 = str(
        surface_certificate.get("evidence_sha256", "")
    ).casefold()
    terminal_via_certificate, _terminal_via_by_pin = (
        _validated_device_terminal_via_certificate(project)
    )
    terminal_via_evidence_sha256 = str(
        terminal_via_certificate.get("evidence_sha256", "")
    ).casefold()
    if rail_port_manifest is None:
        _island_display, islands_by_surface = _certificate_island_inventory(
            surface_certificate
        )
        component_by_island, _components = _surface_equivalence_partition(
            surface_certificate, islands_by_surface
        )
        rail_port_manifest, resolved_omitted = _resolved_v3_rail_port_manifest(
            project,
            surface_certificate,
            component_by_island,
            islands_by_surface,
        )
        if omitted_rail_ids is None:
            omitted_rail_ids = resolved_omitted
    canonical_port_manifest = [dict(item) for item in rail_port_manifest]
    if omitted_rail_ids is None:
        retained_keys = {_key(item["rail_id"]) for item in rail_port_manifest}
        omitted_rail_ids = tuple(
            str(getattr(rail, "rail_id", "")).strip()
            for rail in getattr(project, "rails", ())
            if _key(getattr(rail, "rail_id", "")) not in retained_keys
        )
    canonical_omitted = sorted(
        {str(item).strip() for item in omitted_rail_ids if str(item).strip()},
        key=lambda item: (item.casefold(), item),
    )
    identity = sha256(
        _canonical_json(
            {
                "compiler": LAYERWISE_COMPILER_VERSION,
                "static_sha256": static_sha256,
                "source_sha256": source_sha256,
                "geometry_sha256": geometry_sha256,
                "material_sha256": material_sha256,
                "blocks": [list(block) for block in blocks],
                "gnd_aliases": ground_alias_manifest,
                # Compatibility alias: v3 is a single self-contained
                # surface/terminal/Via-pair certificate.
                "via_group_evidence_sha256": surface_evidence_sha256,
                "surface_connectivity_evidence_sha256": surface_evidence_sha256,
                "device_terminal_via_evidence_sha256": terminal_via_evidence_sha256,
                "rail_ports": canonical_port_manifest,
                "omitted_rail_ids": canonical_omitted,
                **(
                    {"raw_spatial_v3_manifest_sha256": raw_spatial_manifest_sha256}
                    if raw_spatial_manifest_sha256 is not None
                    else {}
                ),
            }
        )
    ).hexdigest()
    return identity, source_sha256, geometry_sha256, material_sha256


def _finite_via_substrate_identity(
    project: Any,
    records: Sequence[Mapping[str, Any]],
    blocks: Sequence[Sequence[str]],
    topology: CompiledFiniteViaBaseTopology,
    *,
    raw_spatial_manifest_sha256: str | None = None,
) -> tuple[str, str, str, str]:
    """Bind the v4 quotient, artwork, stack-up and external port manifest."""

    source_sha256 = topology.source_sha256
    geometry_manifest = sorted(
        (
            _key(record.get("layer", "")),
            _key(record.get("net", "")),
            str(record.get("asset_sha256", "")).lower(),
        )
        for record in records
    )
    geometry_sha256 = sha256(_canonical_json(geometry_manifest)).hexdigest()
    material_manifest = [
        {
            "name": _key(layer.name),
            "thickness_um": float(layer.thickness_um),
            "conductivity_s_m": (
                float(layer.conductivity_s_m)
                if layer.conductivity_s_m is not None
                else None
            ),
            "dk": float(layer.dk) if layer.dk is not None else None,
            "df": float(layer.df) if layer.df is not None else None,
            "properties": [
                (float(point.frequency_hz), float(point.dk), float(point.df))
                for point in getattr(layer, "dielectric_properties", ())
            ],
        }
        for layer in project.stackup_layers
    ]
    material_sha256 = sha256(_canonical_json(material_manifest)).hexdigest()
    ground_aliases = tuple(_key(alias) for alias in project.gnd_aliases)
    if not ground_aliases or len(ground_aliases) != len(set(ground_aliases)):
        raise LayerwiseNetworkUnavailable(
            "GROUND_ALIAS_IDENTITY_INVALID",
            "configured GND aliases must be non-empty and unique",
        )
    port_manifest = [
        {
            "rail_id": item.rail_id,
            "selected_net": item.selected_net,
            "reference_net": item.reference_net,
            "positive_node_id": item.positive_node_id,
            "negative_node_id": item.negative_node_id,
            "positive_pin_ids": list(item.positive_pin_ids),
            "negative_pin_ids": list(item.negative_pin_ids),
        }
        for item in topology.rail_ports
    ]
    identity = sha256(
        _canonical_json(
            {
                "compiler": LAYERWISE_COMPILER_VERSION,
                "static_sha256": solver_profile_static_identity_sha256(
                    "layerwise_admittance_v1"
                ),
                "source_sha256": source_sha256,
                "geometry_sha256": geometry_sha256,
                "material_sha256": material_sha256,
                "blocks": [list(block) for block in blocks],
                "gnd_aliases": {
                    "reference": ground_aliases[0],
                    "members": sorted(ground_aliases),
                },
                "finite_via_topology_identity_sha256": (
                    topology.topology_identity_sha256
                ),
                "surface_connectivity_evidence_sha256": (
                    topology.certificate_evidence_sha256
                ),
                "rail_ports": port_manifest,
                "omitted_rail_ids": list(topology.omitted_rail_ids),
                **(
                    {"raw_spatial_v3_manifest_sha256": raw_spatial_manifest_sha256}
                    if raw_spatial_manifest_sha256 is not None
                    else {}
                ),
            }
        )
    ).hexdigest()
    return identity, source_sha256, geometry_sha256, material_sha256


def _finite_topology_cache_identity(
    project: Any,
    records: Sequence[Mapping[str, Any]],
    certificate: Mapping[str, Any],
) -> str:
    """Hash only inputs consumed by the expensive v4 quotient decoder."""

    geometry_manifest = sorted(
        (
            _key(record.get("layer", "")),
            _key(record.get("net", "")),
            str(record.get("asset", "")).strip(),
            str(record.get("asset_sha256", "")).strip().casefold(),
            tuple(
                sorted(
                    str(item).strip()
                    for item in (
                        record.get("island_ids", ())
                        if isinstance(record.get("island_ids"), Sequence)
                        and not isinstance(record.get("island_ids"), (str, bytes))
                        else ()
                    )
                )
            ),
        )
        for record in records
    )
    rail_manifest = sorted(
        (
            str(getattr(rail, "rail_id", "")).strip(),
            str(getattr(rail, "net", "")).strip(),
        )
        for rail in getattr(project, "rails", ())
    )
    ground_alias_manifest = sorted(
        (
            str(alias).strip().casefold(),
            str(alias).strip(),
        )
        for alias in getattr(project, "gnd_aliases", ())
    )
    storage_manifest = (
        dict(certificate)
        if is_surface_certificate_asset_stub(certificate)
        else None
    )
    spd_import = (
        project.metadata.get("spd_import")
        if isinstance(getattr(project, "metadata", None), Mapping)
        else None
    )
    compiled_storage_manifest = (
        spd_import.get(COMPILED_TOPOLOGY_ASSET_METADATA_KEY)
        if isinstance(spd_import, Mapping)
        and isinstance(
            spd_import.get(COMPILED_TOPOLOGY_ASSET_METADATA_KEY), Mapping
        )
        else None
    )
    inline_certificate_sha256 = (
        None
        if storage_manifest is not None
        else sha256(_canonical_json(certificate)).hexdigest()
    )
    return sha256(
        _canonical_json(
            {
                "identity_schema": "finite-via-topology-cache-v3",
                "schema_version": str(certificate.get("schema_version", "")),
                "compiler_id": str(certificate.get("compiler_id", "")),
                "source_sha256": str(certificate.get("source_sha256", ""))
                .strip()
                .casefold(),
                "project_source_sha256": str(
                    spd_import.get("source_sha256", "")
                    if isinstance(spd_import, Mapping)
                    else ""
                )
                .strip()
                .casefold(),
                "evidence_sha256": str(certificate.get("evidence_sha256", ""))
                .strip()
                .casefold(),
                "geometry": geometry_manifest,
                "rails": rail_manifest,
                "gnd_aliases": ground_alias_manifest,
                # The small envelope validator checks that the compressed
                # member matches these fields, but only hydration proves its
                # canonical payload/evidence hash.  Binding the complete stub
                # storage identity prevents a different member with the same
                # claimed evidence from reaching a prior topology cache hit.
                "surface_certificate_storage": storage_manifest,
                "inline_certificate_sha256": inline_certificate_sha256,
                "compiled_topology_storage": compiled_storage_manifest,
            }
        )
    ).hexdigest()


def _compact_finite_certificate_views(
    certificate: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    """Drop multi-million-row quotient data after its strict decode.

    The substrate compiler still needs surface partition/ownership evidence,
    while the mutable Scenario adapter needs only terminal contacts and exact
    retarget destinations.  The source-model final gate needs only rail anchors
    and the terminal contacts named by those anchors.  None of these consumers
    needs to retain the decoded raw quotient vertex/edge rows once
    ``CompiledFiniteViaBaseTopology`` exists.
    """

    try:
        surface, scenario, external = compact_finite_certificate_views(certificate)
    except CompiledTopologyAssetError as exc:
        raise LayerwiseNetworkUnavailable(exc.code, str(exc)) from exc
    return (
        _freeze_owned_compact_certificate_view(surface),
        _freeze_owned_compact_certificate_view(scenario),
        _freeze_owned_compact_certificate_view(external),
    )


def compile_layerwise_substrate(
    project: Any,
    attachments: Mapping[str, bytes],
    *,
    required_rail_id: str | None = None,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    require_plane_sheet_payload: bool = False,
) -> LayerwiseNetworkSubstrate:
    """Compile/cache one explicit layer-surface network from a retained SPD."""

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    spd_import = project.metadata.get("spd_import")
    records_raw = (
        spd_import.get("plane_geometries") if isinstance(spd_import, Mapping) else None
    )
    if not isinstance(records_raw, Sequence) or isinstance(records_raw, (str, bytes)):
        raise LayerwiseNetworkUnavailable(
            "ARTWORK_INDEX_MISSING", "the project has no retained SPD plane artwork index"
        )
    records = tuple(item for item in records_raw if isinstance(item, Mapping))
    if len(records) != len(records_raw):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_GEOMETRY_MANIFEST_INVALID",
            "the retained SPD plane-artwork manifest contains a non-mapping row",
        )
    if not records or not attachments:
        raise LayerwiseNetworkUnavailable(
            "ARTWORK_ATTACHMENTS_MISSING", "retained SPD geometry attachments are required"
        )
    if type(require_plane_sheet_payload) is not bool:
        raise LayerwiseNetworkUnavailable(
            "RAW_SPATIAL_PLANE_SHEET_REQUIRED",
            "require_plane_sheet_payload must be boolean",
        )
    raw_spatial_manifest_sha256: str | None = None
    validated_raw_manifest: Mapping[str, Any] | None = None
    raw_spatial_expected_bindings: Mapping[str, str] | None = None
    raw_manifest = (
        spd_import.get(RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY)
        if isinstance(spd_import, Mapping)
        else None
    )
    if require_plane_sheet_payload:
        if not (
            isinstance(raw_manifest, Mapping)
            and raw_manifest.get("storage_schema") == RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3
        ):
            raise LayerwiseNetworkUnavailable(
                "RAW_SPATIAL_PLANE_SHEET_REQUIRED",
                "canonical v3 raw-spatial manifest is required",
            )
        try:
            validated_raw_manifest = validate_project_raw_spatial_contact_asset_envelope(
                project, attachments
            )
            compiled_manifest = spd_import.get(COMPILED_TOPOLOGY_ASSET_METADATA_KEY)
            if not isinstance(validated_raw_manifest, Mapping) or not isinstance(
                compiled_manifest, Mapping
            ):
                raise RawSpatialContactAssetError(
                    "RAW_SPATIAL_MANIFEST_INVALID",
                    "validated v3 raw-spatial bindings are unavailable",
                )
            raw_spatial_manifest_sha256 = sha256(
                concrete_canonical_json_bytes(dict(validated_raw_manifest))
            ).hexdigest()
            raw_spatial_expected_bindings = {
                "source_sha256": str(compiled_manifest["source_sha256"]),
                "project_binding_sha256": str(
                    compiled_manifest["project_binding_sha256"]
                ),
                "certificate_evidence_sha256": str(
                    compiled_manifest["certificate_evidence_sha256"]
                ),
                "compiled_topology_identity_sha256": str(
                    compiled_manifest["topology_identity_sha256"]
                ),
                "geometry_identity_sha256": str(
                    validated_raw_manifest["geometry_identity_sha256"]
                ),
            }
        except RawSpatialContactAssetError as exc:
            raise LayerwiseNetworkUnavailable(exc.code, str(exc)) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "RAW_SPATIAL_MANIFEST_INVALID",
                "validated v3 raw-spatial bindings are unavailable",
            ) from exc
    raw_surface_certificate = (
        spd_import.get("layerwise_surface_connectivity_certificate")
        if isinstance(spd_import, Mapping)
        else None
    )
    finite_topology: CompiledFiniteViaBaseTopology | None = None
    scenario_certificate_view: Mapping[str, Any] | None = None
    external_port_proof_view: Mapping[str, Any] | None = None
    is_finite_v4 = bool(
        isinstance(raw_surface_certificate, Mapping)
        and raw_surface_certificate.get("schema_version")
        == FINITE_VIA_SURFACE_SCHEMA
    )
    if is_finite_v4:
        artwork_node_ids = tuple(
            str(island_id).strip()
            for record in records
            for island_id in (
                record.get("island_ids", ())
                if isinstance(record.get("island_ids"), Sequence)
                and not isinstance(record.get("island_ids"), (str, bytes))
                else ()
            )
        )
        topology_cache_key = _finite_topology_cache_identity(
            project, records, raw_surface_certificate
        )
        topology_attachment_snapshot = _immutable_attachment_snapshot(
            attachments, prefix="topology/"
        )
        with _FINITE_TOPOLOGY_CACHE_LOCK:
            cached_topology = _FINITE_TOPOLOGY_CACHE.get(topology_cache_key)
            envelope_verified = _same_attachment_snapshot(
                _FINITE_TOPOLOGY_ATTACHMENT_SNAPSHOTS.get(topology_cache_key),
                topology_attachment_snapshot,
            )
            if not envelope_verified:
                try:
                    validate_project_topology_storage_envelope(project, attachments)
                except SurfaceCertificateAssetError as exc:
                    raise LayerwiseNetworkUnavailable(exc.code, str(exc)) from exc
                envelope_verified = True
                if topology_attachment_snapshot is not None:
                    _FINITE_TOPOLOGY_ATTACHMENT_SNAPSHOTS[
                        topology_cache_key
                    ] = topology_attachment_snapshot
            if cached_topology is None:
                try:
                    persisted = load_compiled_topology_asset(
                        project,
                        attachments,
                        artwork_node_ids,
                        is_cancelled=cancelled,
                        storage_envelope_verified=envelope_verified,
                    )
                    if persisted is not None:
                        finite_topology = persisted.topology
                        scenario_certificate_view = (
                            persisted.scenario_certificate_view
                        )
                        external_port_proof_view = (
                            persisted.external_port_proof_view
                        )
                    else:
                        if is_surface_certificate_compiled_only_stub(
                            raw_surface_certificate
                        ):
                            raise LayerwiseNetworkUnavailable(
                                "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED",
                                "compiled-only surface evidence cannot fall back to hydration",
                            )
                        hydrated_surface_certificate = hydrate_surface_certificate(
                            project, attachments
                        )
                        if not isinstance(hydrated_surface_certificate, Mapping):
                            raise LayerwiseNetworkUnavailable(
                                "SURFACE_CONNECTIVITY_CERTIFICATE_MISSING",
                                "the v4 finite-Via certificate attachment is unavailable",
                            )
                        compiled_topology = compile_finite_via_base_topology(
                            project,
                            records,
                            artwork_node_ids,
                            # Decode all rail outcomes once. A selected omitted rail
                            # is rejected against the compact cached result below.
                            required_rail_id=None,
                            certificate=hydrated_surface_certificate,
                            certificate_verified=True,
                        )
                        (
                            surface_view,
                            scenario_certificate_view,
                            external_port_proof_view,
                        ) = _compact_finite_certificate_views(
                            hydrated_surface_certificate
                        )
                        finite_topology = replace(
                            compiled_topology,
                            certificate=surface_view,
                        )
                    cached_topology = (
                        finite_topology,
                        scenario_certificate_view,
                        external_port_proof_view,
                    )
                    _FINITE_TOPOLOGY_CACHE[topology_cache_key] = cached_topology
                    _FINITE_TOPOLOGY_CACHE.move_to_end(topology_cache_key)
                    while len(_FINITE_TOPOLOGY_CACHE) > _CACHE_LIMIT:
                        evicted_key, _evicted = _FINITE_TOPOLOGY_CACHE.popitem(
                            last=False
                        )
                        _FINITE_TOPOLOGY_ATTACHMENT_SNAPSHOTS.pop(
                            evicted_key, None
                        )
                    if persisted is None:
                        del compiled_topology
                        del hydrated_surface_certificate
                except CompiledTopologyAssetError as exc:
                    raise LayerwiseNetworkUnavailable(exc.code, str(exc)) from exc
                except FiniteViaCertificateError as exc:
                    raise LayerwiseNetworkUnavailable(exc.code, str(exc)) from exc
                finally:
                    # The cached topology and adapter view contain no quotient
                    # vertex/edge inventory. Release the 13--18 GiB parsed
                    # certificate as soon as those compact products exist.
                    clear_surface_certificate_hydration_cache()
            else:
                _FINITE_TOPOLOGY_CACHE.move_to_end(topology_cache_key)
            (
                finite_topology,
                scenario_certificate_view,
                external_port_proof_view,
            ) = cached_topology
        if required_rail_id is not None and not any(
            item.rail_id.casefold() == required_rail_id.casefold()
            for item in finite_topology.rail_ports
        ):
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_MISSING",
                f"required rail {required_rail_id!r} has no complete external Device port",
            )
        surface_certificate = finite_topology.certificate
    else:
        surface_certificate = _validated_surface_connectivity_certificate(
            project, records
        )
    island_display, islands_by_surface = _certificate_island_inventory(
        surface_certificate
    )
    component_by_island, _equivalence_components = (
        _surface_equivalence_partition(surface_certificate, islands_by_surface)
    )
    blocks = _retained_conductor_blocks(project, records)
    if finite_topology is not None:
        rail_by_key = {
            _key(getattr(rail, "rail_id", "")): rail
            for rail in getattr(project, "rails", ())
        }
        rail_port_manifest = tuple(
            MappingProxyType(
                {
                    "rail_id": item.rail_id,
                    "net": item.selected_net,
                    "pwr_layer": str(
                        getattr(rail_by_key[_key(item.rail_id)], "pwr_layer", "")
                    ),
                    "gnd_layer": str(
                        getattr(rail_by_key[_key(item.rail_id)], "gnd_layer", "")
                    ),
                    "reference_net": item.reference_net,
                    "positive_node_id": item.positive_node_id,
                    "negative_node_id": item.negative_node_id,
                }
            )
            for item in finite_topology.rail_ports
        )
        omitted_rail_ids = finite_topology.omitted_rail_ids
        identity, source_sha256, geometry_sha256, material_sha256 = (
            _finite_via_substrate_identity(
                project,
                records,
                blocks,
                finite_topology,
                raw_spatial_manifest_sha256=raw_spatial_manifest_sha256,
            )
        )
    else:
        rail_port_manifest, omitted_rail_ids = _resolved_v3_rail_port_manifest(
            project,
            surface_certificate,
            component_by_island,
            islands_by_surface,
            required_rail_id=required_rail_id,
        )
        identity, source_sha256, geometry_sha256, material_sha256 = _substrate_identity(
            project,
            records,
            blocks,
            rail_port_manifest=rail_port_manifest,
            omitted_rail_ids=omitted_rail_ids,
            raw_spatial_manifest_sha256=raw_spatial_manifest_sha256,
        )
    geometry_attachment_snapshot = _immutable_attachment_snapshot(
        attachments,
        names=tuple(str(record.get("asset", "")) for record in records),
    )
    with _SUBSTRATE_CACHE_LOCK:
        cached = _SUBSTRATE_CACHE.get(identity)
        if cached is not None and _same_attachment_snapshot(
            _SUBSTRATE_ATTACHMENT_SNAPSHOTS.get(identity),
            geometry_attachment_snapshot,
        ):
            _SUBSTRATE_CACHE.move_to_end(identity)
            report(70, "Reusing verified layer-surface substrate")
            return cached

    verified_assets: set[tuple[str, str]] = set()
    for record in records:
        if cancelled():
            raise RuntimeError("evaluation cancelled")
        asset = str(record.get("asset", ""))
        digest = str(record.get("asset_sha256", "")).lower()
        asset_identity = (asset, digest)
        if asset_identity in verified_assets:
            continue
        content = attachments.get(asset)
        if (
            not asset
            or len(digest) != 64
            or content is None
            or sha256(content).hexdigest() != digest
        ):
            raise LayerwiseNetworkUnavailable(
                "ARTWORK_ASSET_INTEGRITY_FAILED",
                f"retained geometry asset {asset!r} is missing or fails SHA-256",
            )
        verified_assets.add(asset_identity)

    with _SUBSTRATE_CACHE_LOCK:
        cached = _SUBSTRATE_CACHE.get(identity)
        if cached is not None:
            if geometry_attachment_snapshot is not None:
                _SUBSTRATE_ATTACHMENT_SNAPSHOTS[
                    identity
                ] = geometry_attachment_snapshot
            _SUBSTRATE_CACHE.move_to_end(identity)
            report(70, "Reusing verified layer-surface substrate")
            return cached

    if require_plane_sheet_payload:
        if validated_raw_manifest is None or raw_spatial_expected_bindings is None:
            raise LayerwiseNetworkUnavailable(
                "RAW_SPATIAL_MANIFEST_INVALID",
                "validated v3 raw-spatial bindings are unavailable",
            )
        try:
            with load_raw_spatial_contact_asset(
                validated_raw_manifest,
                attachments,
                expected_source_sha256=raw_spatial_expected_bindings[
                    "source_sha256"
                ],
                expected_project_binding_sha256=raw_spatial_expected_bindings[
                    "project_binding_sha256"
                ],
                expected_certificate_evidence_sha256=raw_spatial_expected_bindings[
                    "certificate_evidence_sha256"
                ],
                expected_compiled_topology_identity_sha256=(
                    raw_spatial_expected_bindings[
                        "compiled_topology_identity_sha256"
                    ]
                ),
                expected_geometry_identity_sha256=raw_spatial_expected_bindings[
                    "geometry_identity_sha256"
                ],
                require_plane_sheet_payload=True,
                is_cancelled=cancelled,
            ) as loaded_raw_spatial:
                if loaded_raw_spatial.manifest_sha256 != raw_spatial_manifest_sha256:
                    raise RawSpatialContactAssetError(
                        "RAW_SPATIAL_MANIFEST_INVALID",
                        "loaded v3 raw-spatial manifest differs from its validated envelope",
                    )
        except RawSpatialContactAssetError as exc:
            raise LayerwiseNetworkUnavailable(exc.code, str(exc)) from exc

    report(20, f"Compiling {len(blocks)} physical conductor-layer block(s)")
    local_partials: list[DispersiveAdjacentGap] = []
    # Every certified connected artwork island is one physical network node,
    # including islands on retained layers without an adjacent-gap overlap.
    surface_display = dict(island_display)
    block_layer_keys = {_key(layer) for block in blocks for layer in block}
    expected_decoded_islands = {
        island_id
        for island_id, (layer, _net) in island_display.items()
        if _key(layer) in block_layer_keys
    }
    decoded_islands: set[str] = set()
    for block_index, block in enumerate(blocks):
        if cancelled():
            raise RuntimeError("evaluation cancelled")
        block_start = 20 + round(45 * block_index / max(len(blocks), 1))
        block_stop = 20 + round(45 * (block_index + 1) / max(len(blocks), 1))
        block_span = max(block_stop - block_start, 1)
        last_block_progress = block_start

        def block_progress(value: int, message: str) -> None:
            nonlocal last_block_progress
            overall = min(
                block_stop,
                block_start
                + round(block_span * min(max(value, 0), 100) / 100),
            )
            if overall <= last_block_progress:
                return
            last_block_progress = overall
            report(overall, f"{block[0]} to {block[-1]}: {message}")

        report(
            block_start,
            f"Extracting exact artwork {block[0]} to {block[-1]}",
        )
        try:
            source_model = capacitance_model_from_project(
                project,
                attachments,
                block,
                validate_artwork=False,
                island_resolved=True,
                progress=lambda value, message: block_progress(
                    round(value * 0.5), message
                ),
                is_cancelled=cancelled,
            )
            for artwork in source_model.artwork:
                island_id = str(artwork.node_name or "").strip()
                if (
                    not island_id
                    or island_display.get(island_id)
                    != (artwork.layer, artwork.net)
                    or island_id in decoded_islands
                ):
                    raise MultilayerCapacitanceError(
                        "decoded artwork islands differ from the v3 certificate"
                    )
                decoded_islands.add(island_id)
            partials = extract_sparse_adjacent_gap_island_capacitance(
                source_model,
                progress=lambda value, message: block_progress(
                    50 + round(value * 0.5), message
                ),
                is_cancelled=cancelled,
            )
        except (MultilayerCapacitanceError, KeyError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "ARTWORK_CAPACITANCE_EXTRACTION_FAILED",
                f"{block[0]} to {block[-1]}: {exc}",
            ) from exc
        local_partials.extend(
            DispersiveAdjacentGap(
                partial=partial,
                dispersion=_gap_dispersion(
                    project, partial.upper_layer, partial.lower_layer
                ),
            )
            for partial in partials
        )
    if decoded_islands != expected_decoded_islands:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_ISLAND_GEOMETRY_MISMATCH",
            "decoded exact artwork islands do not match the v3 asset-bound island inventory",
        )
    if not local_partials:
        raise LayerwiseNetworkUnavailable(
            "ADJACENT_GAPS_MISSING", "no adjacent-gap Maxwell partial was extracted"
        )

    layer_position = {
        _key(layer.name): index for index, layer in enumerate(project.stackup_layers)
    }
    conductor_by_layer = {
        _key(layer.name): bool(layer.is_conductor) for layer in project.stackup_layers
    }
    invalid_record_layers = sorted(
        {
            display[0]
            for display in surface_display.values()
            if _key(display[0]) not in layer_position
            or not conductor_by_layer.get(_key(display[0]), False)
        },
        key=lambda value: (value.casefold(), value),
    )
    if invalid_record_layers:
        raise LayerwiseNetworkUnavailable(
            "ARTWORK_LAYER_INVALID",
            "retained plane artwork references an unknown or non-conductor layer: "
            f"{invalid_record_layers[:3]!r}",
        )
    artwork_names = tuple(
        sorted(
            surface_display,
            key=lambda node: (
                layer_position[_key(surface_display[node][0])],
                _key(surface_display[node][1]),
                node,
            ),
        )
    )
    global_names = (
        (
            *artwork_names,
            *finite_topology.quotient_vertex_ids,
            *finite_topology.external_port_node_ids,
        )
        if finite_topology is not None
        else artwork_names
    )
    global_position = {name: index for index, name in enumerate(global_names)}
    gap_participating_nodes = {
        name for item in local_partials for name in item.partial.net_names
    }
    if finite_topology is not None:
        equivalence_links = finite_topology.topology_links
        finite_via_links = finite_topology.finite_links
        surface_link_counts = MappingProxyType(
            {
                "cross_layer_ideal_link_count": 0,
                "component_count": len(_equivalence_components),
                "multi_island_component_count": sum(
                    len(component) > 1 for component in _equivalence_components
                ),
                "singleton_component_count": sum(
                    len(component) == 1 for component in _equivalence_components
                ),
            }
        )
        via_link_counts = MappingProxyType(
            {
                "finite_parallel_rl": len(finite_via_links),
                "finite_group_count": len(finite_via_links),
                "raw_via_owner_count": len(finite_topology.owner_edge_by_id),
                "contracted_series_link_count": sum(
                    len(item.owner_ids) > item.count for item in finite_via_links
                ),
            }
        )
    else:
        equivalence_links, surface_link_counts = _compile_surface_equivalence_links(
            surface_certificate, set(global_names)
        )
        finite_via_links, via_link_counts = _compile_v3_via_island_links(
            project, set(global_names), surface_certificate
        )
    via_links = (*equivalence_links, *finite_via_links)
    ports: list[LayerSurfacePort] = []
    port_by_key: dict[str, LayerSurfacePort] = {}
    selected_by_key: dict[str, str] = {}
    reference_by_key: dict[str, str] = {}
    for item in rail_port_manifest:
        rail_id = item["rail_id"]
        net = item["net"]
        pwr_layer = item["pwr_layer"]
        gnd_layer = item["gnd_layer"]
        reference_net = item["reference_net"]
        rail_key = _key(rail_id)
        positive = item["positive_node_id"]
        if positive not in global_position:
            raise LayerwiseNetworkUnavailable(
                "PWR_SURFACE_MISSING",
                f"rail {rail_id!r} has no retained {pwr_layer}/{net} surface",
            )
        negative = item["negative_node_id"]
        if negative not in global_position:
            raise LayerwiseNetworkUnavailable(
                "GROUND_SURFACE_UNRESOLVED",
                f"rail {rail_id!r} has no retained {gnd_layer}/{reference_net} surface",
            )
        port = LayerSurfacePort(rail_id, positive, negative)
        ports.append(port)
        port_by_key[rail_key] = port
        selected_by_key[rail_key] = net
        reference_by_key[rail_key] = reference_net
    try:
        network = compile_layer_surface_network(
            global_names,
            partials=local_partials,
            via_links=via_links,
            ports=ports,
        )
    except LayerSurfaceNetworkError as exc:
        raise LayerwiseNetworkUnavailable(
            "LAYER_SURFACE_NETWORK_COMPILE_FAILED", str(exc)
        ) from exc

    static_sha256 = solver_profile_static_identity_sha256(
        "layerwise_admittance_v1"
    )
    terminal_ownership_disclosure = dict(
        _device_terminal_ownership_disclosure(project, surface_certificate)
    )
    provenance = {
        "profile_key": "layerwise_admittance_v1",
        "profile_badge": "LAYERWISE",
        "status": "source_layerwise_production",
        "source_only": True,
        "powersi_used_for_parameters": False,
        "validation_status": "two_named_case_validation_required",
        "compiler_version": LAYERWISE_COMPILER_VERSION,
        "compiler_algorithm_id": LAYERWISE_ADMITTANCE_PROFILE.compiler_algorithm_id,
        "static_compiler_algorithm_sha256": static_sha256,
        **terminal_ownership_disclosure,
        "source_sha256": source_sha256,
        **(
            {"raw_spatial_v3_manifest_sha256": raw_spatial_manifest_sha256}
            if raw_spatial_manifest_sha256 is not None
            else {}
        ),
        "geometry_manifest_sha256": geometry_sha256,
        "material_manifest_sha256": material_sha256,
        "ground_alias_manifest_sha256": sha256(
            _canonical_json(
                {
                    "reference": _key(next(iter(project.gnd_aliases))),
                    "members": sorted({_key(alias) for alias in project.gnd_aliases}),
                }
            )
        ).hexdigest(),
        # Compatibility aliases. v3 carries Via pairs and surface topology in
        # one self-contained evidence object.
        "via_group_evidence_sha256": str(
            surface_certificate.get("evidence_sha256", "")
        ),
        "via_group_certificate_status": str(
            surface_certificate.get("status", "")
        ),
        "via_island_pair_evidence_sha256": str(
            surface_certificate.get("evidence_sha256", "")
        ),
        "surface_connectivity_evidence_sha256": str(
            surface_certificate.get("evidence_sha256", "")
        ),
        "surface_connectivity_certificate_status": str(
            surface_certificate.get("status", "")
        ),
        "substrate_identity_sha256": identity,
        "rail_port_manifest_sha256": sha256(
            _canonical_json([dict(item) for item in rail_port_manifest])
        ).hexdigest(),
        "configured_gnd_aliases": list(project.gnd_aliases),
        "layer_blocks": [list(block) for block in blocks],
        "adjacent_gap_count": len(local_partials),
        "physical_surface_node_count": len(global_names),
        "topology_only_surface_node_count": len(
            set(global_names) - gap_participating_nodes
        ),
        "reduced_surface_node_count": len(network._reduced_node_ids),
        "rail_port_count": len(ports),
        "omitted_rail_port_count": len(omitted_rail_ids),
        "omitted_rail_port_ids": list(omitted_rail_ids),
        "finite_via_rl_link_count": int(
            via_link_counts["finite_parallel_rl"]
        ),
        "finite_via_group_count": int(via_link_counts["finite_group_count"]),
        "topology_only_via_link_count": len(equivalence_links),
        "same_layer_equivalence_ideal_link_count": len(equivalence_links),
        "finite_trace_rl_link_count": 0,
        "source_trace_width_available": False,
        "via_group_compile_statistics": dict(via_link_counts),
        "source_cross_layer_ideal_link_count": int(
            surface_link_counts["cross_layer_ideal_link_count"]
        ),
        "source_surface_component_count": int(
            surface_link_counts["component_count"]
        ),
        "source_multi_surface_component_count": int(
            surface_link_counts["multi_island_component_count"]
        ),
        "source_single_surface_component_count": int(
            surface_link_counts["singleton_component_count"]
        ),
        "source_artwork_island_count": len(island_display),
        "source_multi_island_surface_count": sum(
            len(island_ids) > 1 for island_ids in islands_by_surface.values()
        ),
        "source_surface_equivalence_status": "complete_partitioned_v3",
        "network_math": (
            "independent exact artwork-island nodes; sparse adjacent-gap Maxwell Y; "
            "same-layer certified equivalence only; ownership-safe finite island-pair "
            "Via R/L; one global open-port Schur/Kron reduction"
        ),
        "nonuniform_correction": (
            "omitted for the terminal-complete external port; no arbitrary legacy "
            "higher-mode one-port difference is parallel-stamped"
        ),
        "topology_assumption": (
            "every certified artwork island remains a physical node; only islands in one "
            "same-layer Trace/artwork equivalence partition ideal-coalesce; Via and all "
            "cross-layer edges never establish ideal equivalence"
        ),
        "via_rl_scope": (
            "v3 exact island-pair aggregates exclude terminal-owned Device/decap Vias; "
            "only positive ownership-resolved substrate_count populations with complete "
            "source drill/material/segment evidence stamp finite parallel R/L"
        ),
        "trace_rl_scope": (
            "source Trace width is unavailable; Trace connectivity is used only "
            "for same-layer artwork-island equivalence proof and stamps no finite R/L"
        ),
        "spatial_scope": (
            "uniform potential per certified artwork equivalence component; no secondary-layer lateral "
            "spreading, via mutual, pad/antipad exact-core, or arbitrary S-matrix multiplication"
        ),
        "dense_raw_matrix_materialized": False,
    }
    substrate = LayerwiseNetworkSubstrate(
        network=network,
        port_by_rail_key=port_by_key,
        selected_net_by_rail_key=selected_by_key,
        reference_net_by_rail_key=reference_by_key,
        layer_blocks=blocks,
        substrate_identity_sha256=identity,
        provenance=provenance,
        scenario_certificate_view=scenario_certificate_view,
        external_port_proof_view=external_port_proof_view,
    )
    with _SUBSTRATE_CACHE_LOCK:
        _SUBSTRATE_CACHE[identity] = substrate
        if geometry_attachment_snapshot is not None:
            _SUBSTRATE_ATTACHMENT_SNAPSHOTS[
                identity
            ] = geometry_attachment_snapshot
        _SUBSTRATE_CACHE.move_to_end(identity)
        while len(_SUBSTRATE_CACHE) > _CACHE_LIMIT:
            evicted_key, _evicted = _SUBSTRATE_CACHE.popitem(last=False)
            _SUBSTRATE_ATTACHMENT_SNAPSHOTS.pop(evicted_key, None)
    report(
        70,
        f"Compiled {len(local_partials)} sparse adjacent gaps, {len(global_names)} islands, "
        f"{len(equivalence_links)} same-layer ideal links, and "
        f"{len(finite_via_links)} ownership-safe finite Via links",
    )
    return substrate


def _evidence_value(value: Any, *, path: str) -> Any:
    """Return a deterministic JSON value for source/solver evidence.

    Rail evidence must bind the impedance parameters themselves, rather than
    only a model ID or object representation.  Supported production models are
    frozen dataclasses; arrays and nested models are retained losslessly.
    Unknown opaque objects fail closed instead of weakening the certificate.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Enum):
        return _evidence_value(value.value, path=path)
    if isinstance(value, np.generic):
        return _evidence_value(value.item(), path=path)
    if isinstance(value, float):
        if not isfinite(value):
            raise LayerwiseNetworkUnavailable(
                "RAIL_EVIDENCE_INVALID", f"{path} contains a non-finite float"
            )
        return value
    if isinstance(value, complex):
        if not isfinite(value.real) or not isfinite(value.imag):
            raise LayerwiseNetworkUnavailable(
                "RAIL_EVIDENCE_INVALID", f"{path} contains a non-finite complex value"
            )
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, np.ndarray):
        return {
            "dtype": value.dtype.str,
            "shape": list(value.shape),
            "values": _evidence_value(value.tolist(), path=f"{path}.values"),
        }
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise LayerwiseNetworkUnavailable(
                "RAIL_EVIDENCE_INVALID", f"{path} has a non-string mapping key"
            )
        return {
            key: _evidence_value(value[key], path=f"{path}.{key}")
            for key in sorted(value)
        }
    if isinstance(value, (tuple, list)):
        return [
            _evidence_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
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
    raise LayerwiseNetworkUnavailable(
        "RAIL_EVIDENCE_INVALID",
        f"{path} uses unsupported opaque type {type(value).__module__}.{type(value).__qualname__}",
    )


def _pin_evidence(pin: Any) -> Mapping[str, Any]:
    return {
        "pin_id": str(pin.pin_id),
        "refdes": str(pin.refdes),
        "pin": str(pin.pin),
        "net": str(pin.net),
        "x_um": float(pin.x_um),
        "y_um": float(pin.y_um),
        "kind": str(getattr(pin.kind, "value", pin.kind)),
        "terminal": str(getattr(pin.terminal, "value", pin.terminal)),
        "domain": None if pin.domain is None else str(pin.domain),
        "site": None if pin.site is None else str(pin.site),
        "bump_group": (
            None if pin.bump_group is None else str(pin.bump_group)
        ),
        "via_template_id": (
            None if pin.via_template_id is None else str(pin.via_template_id)
        ),
    }


def _rail_port_evidence_manifest(
    project: Any, rail: Any, branches: Sequence[Any]
) -> tuple[list[Any], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    pin_by_key: dict[str, Any] = {}
    for pin in project.pins:
        pin_key = _key(pin.pin_id)
        if pin_key in pin_by_key:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_PORT_EVIDENCE_AMBIGUOUS",
                f"Device pin identity {pin.pin_id!r} is duplicated",
            )
        pin_by_key[pin_key] = pin
    ground_keys = {_key(alias) for alias in project.gnd_aliases}
    selected_key = _key(rail.net)
    used_pin_keys: set[str] = set()
    branch_manifest: list[Mapping[str, Any]] = []
    for branch in branches:
        branch_id = str(branch.branch_id).strip()
        member_keys = {
            _key(token)
            for token in branch_id.split("|")
            if token.strip() and _key(token) in pin_by_key
        }
        members = [pin_by_key[key] for key in sorted(member_keys)]
        power_members = [
            pin
            for pin in members
            if str(getattr(pin.kind, "value", pin.kind)) == "DEVICE_BUMP"
            and str(getattr(pin.terminal, "value", pin.terminal)) == "PWR"
            and _key(pin.net) == selected_key
        ]
        ground_members = [
            pin
            for pin in members
            if str(getattr(pin.kind, "value", pin.kind)) == "DEVICE_BUMP"
            and str(getattr(pin.terminal, "value", pin.terminal)) == "GND"
            and _key(pin.net) in ground_keys
        ]
        source_power_pin_id = str(
            getattr(branch, "source_power_pin_id", "") or ""
        ).strip()
        source_ground_pin_id = str(
            getattr(branch, "source_ground_pin_id", "") or ""
        ).strip()
        source_power_key = (
            _key(source_power_pin_id) if source_power_pin_id else ""
        )
        source_ground_key = (
            _key(source_ground_pin_id) if source_ground_pin_id else ""
        )
        if (
            not branch_id
            or not power_members
            or not ground_members
            or len(members) != len(power_members) + len(ground_members)
        ):
            raise LayerwiseNetworkUnavailable(
                "DEVICE_PORT_EVIDENCE_MISSING",
                f"Device branch {branch_id!r} is not bound to selected-PWR and configured-GND source pins",
            )
        if (
            not source_power_key
            or not source_ground_key
            or source_power_key not in member_keys
            or source_ground_key not in member_keys
        ):
            raise LayerwiseNetworkUnavailable(
                "DEVICE_PORT_ANCHOR_EVIDENCE_MISSING",
                f"Device branch {branch_id!r} does not identify its source PWR/GND anchor pair",
            )
        source_power = pin_by_key[source_power_key]
        source_ground = pin_by_key[source_ground_key]
        if (
            source_power not in power_members
            or source_ground not in ground_members
        ):
            raise LayerwiseNetworkUnavailable(
                "DEVICE_PORT_ANCHOR_EVIDENCE_INVALID",
                f"Device branch {branch_id!r} source anchors do not match its selected PWR/configured-GND members",
            )
        used_pin_keys.update((source_power_key, source_ground_key))
        port = branch.port
        branch_manifest.append(
            {
                "branch_id": branch_id,
                "member_pin_ids": sorted(
                    (str(pin.pin_id) for pin in members), key=str.casefold
                ),
                "source_power_pin_id": source_power_pin_id,
                "source_ground_pin_id": source_ground_pin_id,
                "port": {
                    "port_id": str(port.port_id),
                    "x_m": float(port.x_m),
                    "y_m": float(port.y_m),
                    "width_m": float(port.width_m),
                    "height_m": float(port.height_m),
                },
                "series_path": _evidence_value(
                    branch.series_path, path=f"branch[{branch_id}].series_path"
                ),
            }
        )
    relevant_pin_objects = [pin_by_key[key] for key in sorted(used_pin_keys)]
    relevant_pins = [_pin_evidence(pin) for pin in relevant_pin_objects]
    branch_manifest.sort(key=lambda item: str(item["branch_id"]).casefold())
    return relevant_pin_objects, relevant_pins, branch_manifest


def _validate_v4_external_port_proof_view_uncached(
    view: object,
    retained_certificate: Mapping[str, Any],
    substrate: LayerwiseNetworkSubstrate,
) -> Mapping[str, Any]:
    """Validate the small source-port proof retained after v4 hydration.

    The full canonical certificate is validated before this view is created.
    This gate rechecks the compact view's own hash and binds it to both the
    persisted attachment stub and the compiled substrate identity.  It never
    rehydrates the multi-GiB quotient solely to repeat terminal-port proof.
    """

    if not isinstance(view, Mapping):
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_MISSING",
            "the compiled v4 substrate has no retained external-port proof view",
        )
    if set(view) != _V4_EXTERNAL_PORT_PROOF_VIEW_KEYS:
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_INVALID",
            "the v4 external-port proof view has missing or extra fields",
        )
    if (
        view.get("view_schema") != _V4_EXTERNAL_PORT_PROOF_VIEW_SCHEMA
        or view.get("integrity_validation") != _V4_COMPACT_VIEW_INTEGRITY
        or view.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or view.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
        or str(view.get("status", "")).strip().casefold() != "complete"
    ):
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_INVALID",
            "the v4 external-port proof view schema/compiler/status is unsupported",
        )
    source_sha256 = str(view.get("source_sha256", "")).strip().casefold()
    certificate_sha256 = str(view.get("evidence_sha256", "")).strip().casefold()
    view_sha256 = str(view.get("view_evidence_sha256", "")).strip().casefold()
    if not all(
        _is_sha256(value)
        for value in (source_sha256, certificate_sha256, view_sha256)
    ):
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_INVALID",
            "the v4 external-port proof view is not fully hash-bound",
        )
    for field_name, expected in (
        ("schema_version", FINITE_VIA_SURFACE_SCHEMA),
        ("compiler_id", FINITE_VIA_SURFACE_COMPILER),
        ("source_sha256", source_sha256),
        ("evidence_sha256", certificate_sha256),
    ):
        retained = str(retained_certificate.get(field_name, "")).strip()
        if field_name.endswith("sha256"):
            retained = retained.casefold()
        if retained != expected:
            raise LayerwiseNetworkUnavailable(
                "EXTERNAL_PORT_PROOF_VIEW_CERTIFICATE_MISMATCH",
                "the compact external-port proof differs from retained v4 metadata",
            )
    provenance = substrate.provenance
    provenance_source = str(provenance.get("source_sha256", "")).strip().casefold()
    provenance_certificate = str(
        provenance.get("surface_connectivity_evidence_sha256", "")
    ).strip().casefold()
    if (
        provenance_source != source_sha256
        or provenance_certificate != certificate_sha256
    ):
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_SUBSTRATE_MISMATCH",
            "the compact external-port proof differs from the compiled substrate",
        )
    anchor_rows = _certificate_rows(
        view.get("rail_anchor_bindings"),
        code="EXTERNAL_PORT_PROOF_VIEW_INVALID",
        label="compact v4 rail anchors",
    )
    contact_rows = _certificate_rows(
        view.get("terminal_contacts"),
        code="EXTERNAL_PORT_PROOF_VIEW_INVALID",
        label="compact v4 terminal contacts",
    )
    if any(set(row) != _V4_EXTERNAL_PORT_ANCHOR_KEYS for row in anchor_rows):
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_INVALID",
            "a compact v4 rail-anchor row has missing or extra fields",
        )
    if any(set(row) != _V4_EXTERNAL_PORT_CONTACT_KEYS for row in contact_rows):
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_INVALID",
            "a compact v4 terminal-contact row has missing or extra fields",
        )
    anchor_pin_keys = {_key(row.get("pin_id", "")) for row in anchor_rows}
    contact_pin_keys = {_key(row.get("pin_id", "")) for row in contact_rows}
    if (
        not anchor_pin_keys
        or "" in anchor_pin_keys
        or contact_pin_keys != anchor_pin_keys
    ):
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_INCOMPLETE",
            "the compact v4 proof does not exactly cover its rail-anchor pins",
        )
    payload = {
        key: view[key]
        for key in _V4_EXTERNAL_PORT_PROOF_VIEW_KEYS
        if key != "view_evidence_sha256"
    }
    if sha256(_canonical_json(payload)).hexdigest() != view_sha256:
        raise LayerwiseNetworkUnavailable(
            "EXTERNAL_PORT_PROOF_VIEW_INTEGRITY_FAILED",
            "the compact v4 external-port proof hash does not match its payload",
        )
    return view


def _external_port_view_validation_binding(
    view: Mapping[str, Any],
    retained_certificate: Mapping[str, Any],
    substrate: LayerwiseNetworkSubstrate,
) -> tuple[str, ...]:
    """Return every cheap value consumed by the full compact-view gate."""

    provenance = getattr(substrate, "provenance", {})
    return (
        str(view.get("view_schema", "")).strip(),
        str(view.get("integrity_validation", "")).strip(),
        str(view.get("schema_version", "")).strip(),
        str(view.get("compiler_id", "")).strip(),
        str(view.get("source_sha256", "")).strip().casefold(),
        str(view.get("evidence_sha256", "")).strip().casefold(),
        str(view.get("view_evidence_sha256", "")).strip().casefold(),
        str(view.get("status", "")).strip().casefold(),
        str(retained_certificate.get("schema_version", "")).strip(),
        str(retained_certificate.get("compiler_id", "")).strip(),
        str(retained_certificate.get("source_sha256", "")).strip().casefold(),
        str(retained_certificate.get("evidence_sha256", "")).strip().casefold(),
        str(getattr(substrate, "substrate_identity_sha256", ""))
        .strip()
        .casefold(),
        str(provenance.get("source_sha256", "")).strip().casefold()
        if isinstance(provenance, Mapping)
        else "",
        str(provenance.get("surface_connectivity_evidence_sha256", ""))
        .strip()
        .casefold()
        if isinstance(provenance, Mapping)
        else "",
    )


def _validated_v4_external_port_proof_view(
    view: object,
    retained_certificate: Mapping[str, Any],
    substrate: LayerwiseNetworkSubstrate,
) -> Mapping[str, Any]:
    """Validate once per exact immutable view and validation dependency set."""

    if not isinstance(view, Mapping) or not is_validation_cache_safe_json_view(
        view
    ):
        # Mutable/ad-hoc views never qualify for an integrity shortcut.  This
        # preserves mutation detection for tests, compatibility callers and
        # any future producer that does not transfer private ownership.
        return _validate_v4_external_port_proof_view_uncached(
            view, retained_certificate, substrate
        )
    binding = _external_port_view_validation_binding(
        view, retained_certificate, substrate
    )
    identity = id(view)
    with _EXTERNAL_PORT_VIEW_VALIDATION_CACHE_LOCK:
        cached = _EXTERNAL_PORT_VIEW_VALIDATION_CACHE.get(identity)
        if (
            cached is not None
            and cached.view is view
            and cached.binding == binding
        ):
            _EXTERNAL_PORT_VIEW_VALIDATION_CACHE.move_to_end(identity)
            return view
        validated = _validate_v4_external_port_proof_view_uncached(
            view, retained_certificate, substrate
        )
        # The strong view reference prevents Python object-id reuse while the
        # cache entry exists.  A changed expected hash/source/substrate binding
        # misses even when the exact view object is reused.
        _EXTERNAL_PORT_VIEW_VALIDATION_CACHE[identity] = (
            _ValidatedCompactViewEntry(view=view, binding=binding)
        )
        _EXTERNAL_PORT_VIEW_VALIDATION_CACHE.move_to_end(identity)
        while len(_EXTERNAL_PORT_VIEW_VALIDATION_CACHE) > _CACHE_LIMIT:
            _EXTERNAL_PORT_VIEW_VALIDATION_CACHE.popitem(last=False)
        return validated


def _prove_v4_terminal_quotient_components(
    certificate: Mapping[str, Any],
    rail: Any,
    reference_net: str,
    port: LayerSurfacePort,
    network: CompiledLayerSurfaceNetwork,
    relevant_pins: Sequence[Any],
    branches: Sequence[Any],
) -> Any:
    """Bind runtime Device anchors to v4 external quotient vertices.

    The v4 base network already owns each raw Device escape Via.  The
    measurement port is therefore the exposed source-node quotient pair, not
    an artwork-island pair followed by another modal Device series path.
    """

    from .layerwise_terminal_proof import LayerwiseTerminalArtworkProof

    if certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA:
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED",
            "runtime v4 terminal proof received an unsupported certificate",
        )
    rail_id = str(getattr(rail, "rail_id", "")).strip()
    rail_key = _key(rail_id)
    selected_net = str(getattr(rail, "net", "")).strip()
    pin_by_key: dict[str, Any] = {}
    for pin in relevant_pins:
        key = _key(getattr(pin, "pin_id", ""))
        if key in pin_by_key:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_PORT_EVIDENCE_AMBIGUOUS",
                "runtime Device anchor pin IDs are duplicated",
            )
        pin_by_key[key] = pin
    contact_rows = _certificate_rows(
        certificate.get("terminal_contacts"),
        code="TERMINAL_CONTACT_CERTIFICATE_MISSING",
        label="v4 Device terminal contacts",
    )
    contact_by_pin: dict[str, Mapping[str, Any]] = {}
    for raw in contact_rows:
        key = _key(raw.get("pin_id", ""))
        if key in contact_by_pin:
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_CONTACT_CERTIFICATE_INVALID",
                "v4 Device terminal contact IDs are duplicated",
            )
        contact_by_pin[key] = raw
    anchor_rows = tuple(
        raw
        for raw in _certificate_rows(
            certificate.get("rail_anchor_bindings"),
            code="RAIL_ANCHOR_BINDINGS_MISSING",
            label="v4 rail anchors",
        )
        if _key(raw.get("rail_id", "")) == rail_key
    )
    if not anchor_rows:
        raise LayerwiseNetworkUnavailable(
            "RAIL_ANCHOR_BINDINGS_MISSING",
            f"rail {rail_id!r} has no v4 Device anchors",
        )
    expected_pin_keys = {_key(raw.get("pin_id", "")) for raw in anchor_rows}
    if expected_pin_keys != set(pin_by_key):
        raise LayerwiseNetworkUnavailable(
            "DEVICE_PORT_EVIDENCE_MISMATCH",
            f"rail {rail_id!r} runtime anchors differ from its v4 certificate",
        )
    expected_branch_keys = {
        _key(raw.get("branch_id", "")) for raw in anchor_rows
    }
    runtime_branch_keys = {
        _key(getattr(branch, "branch_id", "")) for branch in branches
    }
    if expected_branch_keys != runtime_branch_keys:
        raise LayerwiseNetworkUnavailable(
            "DEVICE_PORT_EVIDENCE_MISMATCH",
            f"rail {rail_id!r} runtime Device branches differ from source anchors",
        )
    role_port_node = {
        "power": port.positive_node_id,
        "ground": port.negative_node_id,
    }
    try:
        role_reduced = {
            role: network.reduced_node_index(node_id)
            for role, node_id in role_port_node.items()
        }
    except LayerSurfaceNetworkError as exc:
        raise LayerwiseNetworkUnavailable(
            "RAIL_PORT_TERMINAL_IDENTITY_MISMATCH",
            "v4 external Device port references an unknown quotient vertex",
        ) from exc
    expected_net = {
        "power": _key(selected_net),
        "ground": _key(reference_net),
    }
    proof_rows: list[dict[str, Any]] = []
    proven = {"power": 0, "ground": 0}
    for raw in sorted(
        anchor_rows,
        key=lambda item: (
            str(item.get("branch_id", "")).casefold(),
            str(item.get("role", "")).casefold(),
            str(item.get("pin_id", "")).casefold(),
        ),
    ):
        role = _key(raw.get("role", ""))
        pin_id = str(raw.get("pin_id", "")).strip()
        pin_key = _key(pin_id)
        contact = contact_by_pin.get(pin_key)
        runtime_pin = pin_by_key.get(pin_key)
        if role not in {"power", "ground"} or contact is None or runtime_pin is None:
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_CONTACT_CERTIFICATE_MISSING",
                f"rail {rail_id!r} anchor {pin_id!r} has no v4 contact",
            )
        node_id = str(contact.get("exposed_quotient_vertex_id", "")).strip()
        contact_net = str(contact.get("net", "")).strip()
        if (
            str(contact.get("status", "")).strip().casefold() != "complete"
            or not node_id
            or _key(contact_net) != expected_net[role]
            or _key(getattr(runtime_pin, "net", "")) != expected_net[role]
        ):
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_TERMINAL_IDENTITY_MISMATCH",
                f"rail {rail_id!r} {role} anchor {pin_id!r} has stale NET/contact evidence",
            )
        try:
            reduced = network.reduced_node_index(node_id)
        except LayerSurfaceNetworkError as exc:
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_QUOTIENT_VERTEX_MISSING",
                f"rail {rail_id!r} anchor {pin_id!r} quotient vertex is absent",
            ) from exc
        if reduced != role_reduced[role]:
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_MULTI_VERTEX_UNSUPPORTED",
                f"rail {rail_id!r} {role} anchor {pin_id!r} is outside its external port node",
            )
        proven[role] += 1
        proof_rows.append(
            {
                "branch_id": str(raw.get("branch_id", "")).strip(),
                "role": role,
                "pin_id": pin_id,
                "net": contact_net,
                "exposed_quotient_vertex_id": node_id,
                "incident_via_id": contact.get("incident_via_id"),
                "first_via_quotient_edge_id": contact.get(
                    "first_via_quotient_edge_id"
                ),
                "reduced_node_index": int(reduced),
                "raw_via_route_owner": "base_global_finite_quotient",
                "status": "proven",
            }
        )
    source_proven = proven["power"] > 0
    reference_proven = proven["ground"] > 0
    if not source_proven or not reference_proven:
        raise LayerwiseNetworkUnavailable(
            "TERMINAL_SURFACE_CONTACT_PROOF_FAILED",
            f"rail {rail_id!r} lacks a complete external quotient port",
        )
    manifest_value = {
        "schema_version": "layerwise-terminal-finite-quotient-proof-v1",
        "compiler_id": LAYERWISE_COMPILER_VERSION,
        "source_sha256": str(certificate.get("source_sha256", "")).casefold(),
        "surface_connectivity_evidence_sha256": str(
            certificate.get("evidence_sha256", "")
        ).casefold(),
        "rail_id": rail_id,
        "selected_net": selected_net,
        "reference_net": reference_net,
        "positive_quotient_vertex_id": port.positive_node_id,
        "negative_quotient_vertex_id": port.negative_node_id,
        "anchor_contacts": proof_rows,
        "runtime_branch_series_paths": [
            {
                "branch_id": str(getattr(branch, "branch_id", "")),
                "series_path": _evidence_value(
                    getattr(branch, "series_path", None),
                    path=f"branch[{getattr(branch, 'branch_id', '')}].series_path",
                ),
                "numerical_stamp_scope": "evidence_only_external_input_bypasses_legacy_branch",
            }
            for branch in sorted(
                branches,
                key=lambda item: str(getattr(item, "branch_id", "")).casefold(),
            )
        ],
        "source_terminal_artwork_proven": source_proven,
        "reference_terminal_artwork_proven": reference_proven,
        "status": "proven",
        "terminal_via_stamp_scope": (
            "every raw Device escape Via is stamped exactly once by the base "
            "finite quotient; the legacy Device series template is evidence-only"
        ),
    }
    evidence_sha256 = sha256(_canonical_json(manifest_value)).hexdigest()
    return LayerwiseTerminalArtworkProof(
        status="proven",
        source_terminal_artwork_proven=source_proven,
        reference_terminal_artwork_proven=reference_proven,
        evidence_sha256=evidence_sha256,
        manifest=MappingProxyType(manifest_value),
        diagnostics=(),
    )


def _prove_v3_terminal_island_components(
    certificate: Mapping[str, Any],
    rail: Any,
    reference_net: str,
    port: LayerSurfacePort,
    network: CompiledLayerSurfaceNetwork,
    relevant_pins: Sequence[Any],
    branches: Sequence[Any],
    *,
    project: Any,
) -> Any:
    """Bind runtime Device anchors to exact v3 endpoint-island components.

    The imported v3 certificate proves either a direct owned terminal Via path
    or an exact trace-first source-Node path into one hash-bound same-layer
    artwork component.  This final runtime gate proves that the currently
    compiled Device branches are the same anchors and that every component is
    the selected port's ideal-reduction component.  Direct terminal Via R/L is
    deliberately not stamped here; it remains owned by
    ``DeviceBranch.series_path``.
    """

    from .layerwise_terminal_proof import LayerwiseTerminalArtworkProof

    spd_import = project.metadata.get("spd_import")
    records_raw = (
        spd_import.get("plane_geometries")
        if isinstance(spd_import, Mapping)
        else None
    )
    if not isinstance(records_raw, Sequence) or isinstance(
        records_raw, (str, bytes)
    ):
        raise LayerwiseNetworkUnavailable(
            "ARTWORK_INDEX_MISSING",
            "runtime terminal ownership proof needs the retained artwork index",
        )
    records = tuple(item for item in records_raw if isinstance(item, Mapping))
    if len(records) != len(records_raw):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_GEOMETRY_MANIFEST_INVALID",
            "runtime terminal ownership proof found an invalid artwork row",
        )
    validated_certificate = _validated_surface_connectivity_certificate(
        project, records
    )
    if (
        str(validated_certificate.get("evidence_sha256") or "").casefold()
        != str(certificate.get("evidence_sha256") or "").casefold()
    ):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INTEGRITY_FAILED",
            "runtime terminal proof received a different v3 surface certificate",
        )
    certificate = validated_certificate
    terminal_via_certificate, legacy_terminal_by_pin = (
        _validated_device_terminal_via_certificate(project)
    )

    _island_display, islands_by_surface = _certificate_island_inventory(certificate)
    component_by_island, _components = _surface_equivalence_partition(
        certificate, islands_by_surface
    )
    component_by_id = _surface_component_index(certificate)
    contacted_component_ids = _contacted_surface_component_ids(
        certificate, component_by_id
    )
    anchor_rows = _certificate_rows(
        certificate.get("rail_anchor_bindings"),
        code="RAIL_ANCHOR_BINDINGS_MISSING",
        label="rail-anchor binding manifest",
    )
    contact_rows = _certificate_rows(
        certificate.get("terminal_contacts"),
        code="TERMINAL_CONTACT_CERTIFICATE_MISSING",
        label="Device terminal-contact manifest",
    )
    contact_by_pin = {_key(item.get("pin_id", "")): item for item in contact_rows}
    landing_rows = _certificate_rows(
        certificate.get("terminal_landing_contacts"),
        code="TERMINAL_LANDING_CERTIFICATE_MISSING",
        label="terminal landing-contact manifest",
    )
    device_landing_by_via = {
        _key(item.get("via_id", "")): item
        for item in landing_rows
        if str(item.get("terminal_owner_kind") or "").strip().casefold()
        == "device"
    }
    runtime_pin_by_key: dict[str, Any] = {}
    for pin in relevant_pins:
        pin_key = _key(getattr(pin, "pin_id", ""))
        if pin_key in runtime_pin_by_key:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_PORT_EVIDENCE_AMBIGUOUS",
                "runtime Device source pin identities are duplicated",
            )
        runtime_pin_by_key[pin_key] = pin

    branch_evidence_by_key: dict[str, Mapping[str, Any]] = {}
    for branch in branches:
        branch_id = str(getattr(branch, "branch_id", "")).strip()
        branch_key = _key(branch_id)
        if branch_key in branch_evidence_by_key or not hasattr(
            branch, "series_path"
        ):
            raise LayerwiseNetworkUnavailable(
                "DEVICE_TERMINAL_VIA_SERIES_PATH_UNBOUND",
                "each Device branch needs one unique, explicit series-path model",
            )
        series_path = getattr(branch, "series_path")
        if series_path is None:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_TERMINAL_VIA_SERIES_PATH_UNBOUND",
                f"Device branch {branch_id!r} has no series-path model",
            )
        series_evidence = _evidence_value(
            series_path, path=f"branch[{branch_id}].series_path"
        )
        branch_evidence_by_key[branch_key] = MappingProxyType(
            {
                "series_path": series_evidence,
                "series_path_evidence_sha256": sha256(
                    _canonical_json(series_evidence)
                ).hexdigest(),
            }
        )

    def runtime_terminal_owner_row(pin_key: str) -> Mapping[str, Any]:
        runtime_pin = runtime_pin_by_key.get(pin_key)
        raw = legacy_terminal_by_pin.get(pin_key)
        if runtime_pin is None or raw is None:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_TERMINAL_VIA_OWNER_MISSING",
                "a runtime Device source pin lacks exact first-Via source evidence",
            )

        def text_value(value: Any) -> str:
            return "" if value is None else str(value).strip()

        runtime_terminal = text_value(
            getattr(
                getattr(runtime_pin, "terminal", None),
                "value",
                getattr(runtime_pin, "terminal", None),
            )
        )
        expected_endpoint_evidence = _metadata_sha256_without_newline(
            {
                "source_sha256": str(
                    terminal_via_certificate.get("source_sha256") or ""
                ).casefold(),
                "pin_id_key": pin_key,
                "source_node_id_key": (
                    text_value(getattr(runtime_pin, "source_node_id", None)).casefold()
                    or None
                ),
            }
        )
        try:
            source_x_um = float(raw.get("source_x_um"))
            source_y_um = float(raw.get("source_y_um"))
            runtime_x_um = float(getattr(runtime_pin, "x_um"))
            runtime_y_um = float(getattr(runtime_pin, "y_um"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_TERMINAL_VIA_OWNER_MISMATCH",
                "Device terminal source coordinates are invalid",
            ) from exc
        if (
            _key(raw.get("pin_id", "")) != pin_key
            or text_value(raw.get("refdes")).casefold()
            != text_value(getattr(runtime_pin, "refdes", None)).casefold()
            or text_value(raw.get("pin")).casefold()
            != text_value(getattr(runtime_pin, "pin", None)).casefold()
            or text_value(raw.get("terminal")).casefold()
            != runtime_terminal.casefold()
            or _key(raw.get("net", ""))
            != _key(getattr(runtime_pin, "net", ""))
            or text_value(raw.get("source_node_id")).casefold()
            != text_value(
                getattr(runtime_pin, "source_node_id", None)
            ).casefold()
            or text_value(raw.get("source_layer")).casefold()
            != text_value(getattr(runtime_pin, "source_layer", None)).casefold()
            or text_value(raw.get("source_padstack")).casefold()
            != text_value(
                getattr(runtime_pin, "source_padstack", None)
            ).casefold()
            or not isfinite(source_x_um)
            or not isfinite(source_y_um)
            or not isclose(source_x_um, runtime_x_um, rel_tol=0.0, abs_tol=1.0e-12)
            or not isclose(source_y_um, runtime_y_um, rel_tol=0.0, abs_tol=1.0e-12)
            or str(raw.get("endpoint_id") or "").strip()
            != f"spd-device-terminal-via:{expected_endpoint_evidence[:24]}"
        ):
            raise LayerwiseNetworkUnavailable(
                "DEVICE_TERMINAL_VIA_OWNER_MISMATCH",
                f"runtime Device pin {getattr(runtime_pin, 'pin_id', '')!r} differs from its source-bound first-Via row",
            )
        return raw

    rail_id = str(getattr(rail, "rail_id", "")).strip()
    rail_key = _key(rail_id)
    expected_bindings: dict[
        tuple[str, str, str, str], tuple[str, str, str, str]
    ] = {}
    for branch in branches:
        branch_id = str(getattr(branch, "branch_id", "")).strip()
        power_pin = str(getattr(branch, "source_power_pin_id", "") or "").strip()
        ground_pin = str(getattr(branch, "source_ground_pin_id", "") or "").strip()
        if not branch_id or not power_pin or not ground_pin:
            raise LayerwiseNetworkUnavailable(
                "DEVICE_PORT_ANCHOR_EVIDENCE_MISSING",
                "a compiled Device branch has no exact PWR/GND source-anchor pair",
            )
        for role, pin_id in (("power", power_pin), ("ground", ground_pin)):
            identity = (rail_key, _key(branch_id), role, _key(pin_id))
            if identity in expected_bindings:
                raise LayerwiseNetworkUnavailable(
                    "DEVICE_PORT_ANCHOR_EVIDENCE_AMBIGUOUS",
                    f"Device branch {branch_id!r} duplicates a source anchor",
                )
            expected_bindings[identity] = (rail_id, branch_id, role, pin_id)

    observed_bindings: dict[
        tuple[str, str, str, str], Mapping[str, Any]
    ] = {}
    for raw in anchor_rows:
        if _key(raw.get("rail_id", "")) != rail_key:
            continue
        identity = (
            rail_key,
            _key(raw.get("branch_id", "")),
            _key(raw.get("role", "")),
            _key(raw.get("pin_id", "")),
        )
        if identity in observed_bindings:
            raise LayerwiseNetworkUnavailable(
                "RAIL_ANCHOR_BINDINGS_INVALID",
                f"rail {rail_id!r} has a duplicate v3 anchor identity",
            )
        observed_bindings[identity] = raw
    if set(observed_bindings) != set(expected_bindings):
        raise LayerwiseNetworkUnavailable(
            "RAIL_ANCHOR_BINDINGS_MISMATCH",
            f"rail {rail_id!r} runtime Device branches differ from the source-bound v3 anchor manifest",
        )
    relevant_pin_keys = {_key(getattr(pin, "pin_id", "")) for pin in relevant_pins}
    expected_pin_keys = {identity[3] for identity in expected_bindings}
    if relevant_pin_keys != expected_pin_keys:
        raise LayerwiseNetworkUnavailable(
            "DEVICE_PORT_EVIDENCE_MISMATCH",
            f"rail {rail_id!r} relevant Device pins differ from its v3 source anchors",
        )

    expected_role_identity = {
        "power": (
            _key(getattr(rail, "net", "")),
            _key(getattr(rail, "pwr_layer", "")),
        ),
        "ground": (
            _key(reference_net),
            _key(getattr(rail, "gnd_layer", "")),
        ),
    }
    role_port_node = {
        "power": port.positive_node_id,
        "ground": port.negative_node_id,
    }
    try:
        role_port_reduced = {
            role: network.reduced_node_index(node_id)
            for role, node_id in role_port_node.items()
        }
    except LayerSurfaceNetworkError as exc:
        raise LayerwiseNetworkUnavailable(
            "RAIL_PORT_TERMINAL_IDENTITY_MISMATCH",
            "the selected v3 port references an unknown physical artwork island",
        ) from exc

    proof_rows: list[Mapping[str, Any]] = []
    proven_by_role = {"power": 0, "ground": 0}
    claimed_direct_via_ids: set[str] = set()
    for identity in sorted(expected_bindings):
        _rail_display, branch_id, role, pin_id = expected_bindings[identity]
        contact = contact_by_pin.get(identity[3])
        if contact is None or str(contact.get("status", "")).strip() != "complete":
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_CONTACT_CERTIFICATE_MISSING",
                f"rail {rail_id!r} Device anchor {pin_id!r} has no complete v3 terminal contact",
            )
        net = str(contact.get("net", "")).strip()
        certified = _validated_terminal_contact_component(
            contact, component_by_id, contacted_component_ids
        )
        component_id = str(contact.get("contact_component_id") or "").strip()
        component_evidence = str(
            contact.get("contact_component_evidence_sha256") or ""
        ).strip().casefold()
        layer = str(certified.get("layer") or "").strip()
        component_net = str(certified.get("net") or "").strip()
        island_id = str(certified.get("representative_island_id") or "").strip()
        component_island_ids = tuple(
            sorted(str(item).strip() for item in certified.get("island_ids", ()))
        )
        path_kind = str(contact.get("contact_path_kind") or "").strip().casefold()
        external_node_id = str(
            contact.get("external_endpoint_node_id") or ""
        ).strip()
        internal_node_id = str(
            contact.get("internal_endpoint_node_id") or ""
        ).strip()
        via_id = str(contact.get("incident_via_id") or "").strip()
        owner_row = runtime_terminal_owner_row(identity[3])
        branch_evidence = branch_evidence_by_key[_key(branch_id)]
        if (
            path_kind not in {"direct_via_landing", "trace_component"}
            or _key(net) != _key(component_net)
            or (_key(component_net), _key(layer)) != expected_role_identity[role]
        ):
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_TERMINAL_IDENTITY_MISMATCH",
                f"rail {rail_id!r} {role} anchor {pin_id!r} lands on the wrong NET/layer",
            )

        landing_physical_evidence: Mapping[str, Any] | None = None
        landing_physical_evidence_sha256: str | None = None
        if path_kind == "direct_via_landing":
            via_key = _key(via_id)
            landing = device_landing_by_via.get(via_key)
            if (
                via_key in claimed_direct_via_ids
                or str(owner_row.get("status") or "").strip() != "complete"
                or _key(owner_row.get("incident_via_id", "")) != via_key
                or _key(owner_row.get("incident_net", ""))
                != _key(contact.get("incident_net", ""))
                or _key(contact.get("incident_net", "")) != _key(net)
                or _key(owner_row.get("incident_padstack", ""))
                != _key(contact.get("incident_padstack", ""))
                or _key(owner_row.get("incident_opposite_node_id", ""))
                != _key(internal_node_id)
                or str(owner_row.get("source_node_id") or "").strip().casefold()
                != external_node_id.casefold()
                or landing is None
                or _key(landing.get("via_id", "")) != via_key
                or str(landing.get("external_endpoint_node_id") or "")
                .strip()
                .casefold()
                != external_node_id.casefold()
                or str(landing.get("internal_endpoint_node_id") or "")
                .strip()
                .casefold()
                != internal_node_id.casefold()
                or _key(landing.get("net", "")) != _key(net)
                or _key(landing.get("padstack", ""))
                != _key(contact.get("incident_padstack", ""))
                or str(landing.get("terminal_owner_kind") or "")
                .strip()
                .casefold()
                != "device"
                or str(landing.get("physical_model_status") or "")
                .strip()
                .casefold()
                != "complete"
            ):
                raise LayerwiseNetworkUnavailable(
                    "DEVICE_TERMINAL_VIA_OWNER_MISMATCH",
                    f"rail {rail_id!r} Device anchor {pin_id!r} is not the exact source-owned Via landing",
                )
            claimed_direct_via_ids.add(via_key)
            landing_physical_evidence = {
                "via_id": via_id,
                "net": str(landing.get("net") or "").strip(),
                "padstack": str(landing.get("padstack") or "").strip(),
                "drill_diameter_um": float(landing.get("drill_diameter_um")),
                "material": (
                    None
                    if landing.get("material") is None
                    else str(landing.get("material"))
                ),
                "external_endpoint_node_id": external_node_id,
                "internal_endpoint_node_id": internal_node_id,
                "external_endpoint_layer": str(
                    landing.get("external_endpoint_layer") or ""
                ).strip(),
                "component_layer": str(
                    landing.get("component_layer") or ""
                ).strip(),
                "segments": _evidence_value(
                    landing.get("segments"),
                    path=f"terminal_landing[{via_id}].segments",
                ),
            }
            landing_physical_evidence_sha256 = sha256(
                _canonical_json(landing_physical_evidence)
            ).hexdigest()
        else:
            if (
                via_id
                or internal_node_id
                or str(owner_row.get("status") or "").strip()
                != "missing_incident_via"
                or int(owner_row.get("candidate_count", -1)) != 0
                or any(
                    str(owner_row.get(field) or "").strip()
                    for field in (
                        "incident_via_id",
                        "incident_net",
                        "incident_padstack",
                        "incident_opposite_node_id",
                    )
                )
                or str(owner_row.get("source_node_id") or "").strip().casefold()
                != external_node_id.casefold()
                or str(
                    owner_row.get("candidate_via_ids_sha256") or ""
                ).casefold()
                != _metadata_sha256_without_newline(())
            ):
                raise LayerwiseNetworkUnavailable(
                    "DEVICE_TERMINAL_TRACE_OWNER_MISMATCH",
                    f"rail {rail_id!r} trace-first anchor {pin_id!r} has contradictory first-Via evidence",
                )
        component = component_by_island.get(island_id)
        port_component = component_by_island.get(role_port_node[role])
        if (
            component is None
            or port_component is None
            or component != port_component
        ):
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_MULTI_ISLAND_UNSUPPORTED",
                f"rail {rail_id!r} {role} anchor {pin_id!r} is outside the selected port equivalence component",
            )
        try:
            endpoint_reduced = network.reduced_node_index(island_id)
        except LayerSurfaceNetworkError as exc:
            raise LayerwiseNetworkUnavailable(
                "TERMINAL_ENDPOINT_ISLAND_MISSING",
                f"rail {rail_id!r} terminal island {island_id!r} is absent from the compiled network",
            ) from exc
        if endpoint_reduced != role_port_reduced[role]:
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_MULTI_ISLAND_UNSUPPORTED",
                f"rail {rail_id!r} {role} anchor {pin_id!r} does not reduce to the selected port component",
            )
        proven_by_role[role] += 1
        proof_rows.append(
            {
                "branch_id": branch_id,
                "role": role,
                "pin_id": pin_id,
                "contact_path_kind": path_kind,
                "via_id": via_id,
                "external_endpoint_node_id": external_node_id,
                "internal_endpoint_node_id": internal_node_id,
                "net": net,
                "layer": layer,
                "component_id": component_id,
                "component_evidence_sha256": component_evidence,
                "component_island_ids": list(component_island_ids),
                "representative_island_id": island_id,
                "surface_equivalence_component_index": int(component[2]),
                "reduced_node_index": int(endpoint_reduced),
                "device_terminal_owner_endpoint_id": str(
                    owner_row.get("endpoint_id") or ""
                ).strip(),
                "device_terminal_owner_status": str(
                    owner_row.get("status") or ""
                ).strip(),
                "device_terminal_owner_via_id": str(
                    owner_row.get("incident_via_id") or ""
                ).strip(),
                "device_terminal_owner_padstack": str(
                    owner_row.get("incident_padstack") or ""
                ).strip(),
                "terminal_landing_physical_evidence": landing_physical_evidence,
                "terminal_landing_physical_evidence_sha256": (
                    landing_physical_evidence_sha256
                ),
                "branch_series_path": branch_evidence["series_path"],
                "branch_series_path_evidence_sha256": branch_evidence[
                    "series_path_evidence_sha256"
                ],
                "branch_series_path_binding_scope": (
                    "runtime differential template is evidence-bound to this exact "
                    "branch/pin pair; no equality to certified landing R/L is asserted"
                ),
                "status": "proven",
            }
        )
    source_proven = proven_by_role["power"] > 0
    reference_proven = proven_by_role["ground"] > 0
    if not source_proven or not reference_proven:
        raise LayerwiseNetworkUnavailable(
            "TERMINAL_SURFACE_CONTACT_PROOF_FAILED",
            f"rail {rail_id!r} does not have both proven PWR and reference Device anchors",
        )
    manifest_value = {
        "schema_version": _TERMINAL_ISLAND_PROOF_SCHEMA,
        "compiler_id": LAYERWISE_COMPILER_VERSION,
        "source_sha256": str(certificate.get("source_sha256", "")).casefold(),
        "surface_connectivity_evidence_sha256": str(
            certificate.get("evidence_sha256", "")
        ).casefold(),
        "device_terminal_via_evidence_sha256": str(
            terminal_via_certificate.get("evidence_sha256", "")
        ).casefold(),
        "rail_id": rail_id,
        "selected_net": str(getattr(rail, "net", "")).strip(),
        "reference_net": str(reference_net).strip(),
        "positive_island_id": port.positive_node_id,
        "negative_island_id": port.negative_node_id,
        "positive_reduced_node_index": int(role_port_reduced["power"]),
        "negative_reduced_node_index": int(role_port_reduced["ground"]),
        "anchor_contacts": proof_rows,
        "source_terminal_artwork_proven": source_proven,
        "reference_terminal_artwork_proven": reference_proven,
        "status": "proven",
        "terminal_via_stamp_scope": (
            "direct_via_exact_owner_and_landing_bound_to_runtime_branch_series_template;"
            "certified_landing_rl_not_substrate_stamped;trace_component_no_incident_via"
        ),
    }
    evidence_sha256 = sha256(_canonical_json(manifest_value)).hexdigest()
    manifest = MappingProxyType(manifest_value)
    return LayerwiseTerminalArtworkProof(
        status="proven",
        source_terminal_artwork_proven=source_proven,
        reference_terminal_artwork_proven=reference_proven,
        evidence_sha256=evidence_sha256,
        manifest=manifest,
        diagnostics=(),
    )


def build_layerwise_uniform_source_model(
    project: Any,
    attachments: Mapping[str, bytes],
    rail_id: str,
    template: Any,
    *,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> LayerwiseUniformSourceModel:
    rails = {item.rail_id.casefold(): item for item in project.rails}
    rail = rails.get(rail_id.casefold())
    if rail is None:
        raise LayerwiseNetworkUnavailable(
            "RAIL_UNKNOWN", f"rail {rail_id!r} is absent from the project"
        )
    metadata = getattr(project, "metadata", None)
    spd_import = (
        metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    )
    raw_surface_certificate = (
        spd_import.get("layerwise_surface_connectivity_certificate")
        if isinstance(spd_import, Mapping)
        else None
    )
    if not (
        isinstance(raw_surface_certificate, Mapping)
        and raw_surface_certificate.get("schema_version")
        == FINITE_VIA_SURFACE_SCHEMA
    ):
        raise LayerwiseNetworkUnavailable(
            "TERMINAL_COMPLETE_REIMPORT_REQUIRED",
            "production layerwise evaluation requires a v4 finite-Via "
            "terminal-complete certificate; re-import the source SPD with the "
            "current application before running Evaluation",
        )
    raw_spatial_manifest = (
        spd_import.get(RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY)
        if isinstance(spd_import, Mapping)
        else None
    )
    raw_schema = (
        raw_spatial_manifest.get("storage_schema")
        if isinstance(raw_spatial_manifest, Mapping)
        else None
    )
    if raw_spatial_manifest is not None and raw_schema not in {
        RAW_SPATIAL_CONTACT_ASSET_SCHEMA,
        RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3,
    }:
        raise LayerwiseNetworkUnavailable(
            "RAW_SPATIAL_PLANE_SHEET_REQUIRED",
            "raw spatial metadata is not a canonical v3 manifest",
        )
    require_plane_sheet_payload = raw_schema == RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3
    substrate = compile_layerwise_substrate(
        project,
        attachments,
        required_rail_id=rail_id,
        progress=progress,
        is_cancelled=is_cancelled,
        require_plane_sheet_payload=require_plane_sheet_payload,
    )
    rail_key = _key(rail.rail_id)
    if rail_key not in substrate.port_by_rail_key:
        raise LayerwiseNetworkUnavailable(
            "RAIL_PORT_MISSING", f"rail {rail_id!r} has no compiled layer-surface port"
        )
    branches = tuple(getattr(template.device, "branches", ()))
    if not branches:
        raise LayerwiseNetworkUnavailable(
            "DEVICE_PORT_EVIDENCE_MISSING",
            "selected PWR and configured-GND Device launches are required",
        )
    relevant_pin_objects, relevant_pins, branch_manifest = _rail_port_evidence_manifest(
        project, rail, branches
    )
    records_raw = (
        spd_import.get("plane_geometries")
        if isinstance(spd_import, Mapping)
        else None
    )
    if not isinstance(records_raw, Sequence) or isinstance(records_raw, (str, bytes)):
        raise LayerwiseNetworkUnavailable(
            "ARTWORK_INDEX_MISSING",
            "the project has no retained SPD plane artwork index",
        )
    records = tuple(item for item in records_raw if isinstance(item, Mapping))
    if len(records) != len(records_raw):
        raise LayerwiseNetworkUnavailable(
            "SURFACE_GEOMETRY_MANIFEST_INVALID",
            "the retained SPD plane artwork index contains an invalid row",
        )
    surface_certificate = _validated_v4_external_port_proof_view(
        substrate.external_port_proof_view,
        raw_surface_certificate,
        substrate,
    )
    terminal_proof = _prove_v4_terminal_quotient_components(
        surface_certificate,
        rail,
        substrate.reference_net_by_rail_key[rail_key],
        substrate.port_by_rail_key[rail_key],
        substrate.network,
        relevant_pin_objects,
        branches,
    )
    if not terminal_proof.ready:
        diagnostic = terminal_proof.diagnostics[0] if terminal_proof.diagnostics else None
        raise LayerwiseNetworkUnavailable(
            diagnostic.code
            if diagnostic is not None
            else "TERMINAL_SURFACE_CONTACT_PROOF_FAILED",
            (
                diagnostic.message
                if diagnostic is not None
                else "selected Device terminals are not proven by exact source-node surface contacts"
            ),
        )
    manifest = {
        "compiler": LAYERWISE_COMPILER_VERSION,
        "substrate_identity_sha256": substrate.substrate_identity_sha256,
        "rail_id": rail.rail_id,
        "selected_net": rail.net,
        "pwr_layer": rail.pwr_layer,
        "gnd_layer": rail.gnd_layer,
        "omitted_layer_surface_port_count": int(
            substrate.provenance.get("omitted_rail_port_count", 0)
        ),
        "omitted_layer_surface_port_rail_ids": list(
            substrate.provenance.get("omitted_rail_port_ids", ())
        ),
        "relevant_pins": relevant_pins,
        "device_branches": branch_manifest,
        "terminal_surface_contact_proof": terminal_proof.manifest_dict(),
        "terminal_envelope": {
            "origin_um": list(template.origin_um),
            "width_m": float(template.plane.width_m),
            "height_m": float(template.plane.height_m),
        },
    }
    evidence_sha256 = sha256(_canonical_json(manifest)).hexdigest()
    provenance = {
        **dict(substrate.provenance),
        "artwork_evidence_sha256": evidence_sha256,
        "layerwise_identity_sha256": evidence_sha256,
        "selected_net": rail.net,
        "rail_id": rail.rail_id,
        "selected_pair": [rail.pwr_layer, rail.gnd_layer],
        "device_branch_count": len(branches),
        "relevant_device_pin_count": len(relevant_pins),
        "terminal_surface_contact_proof_sha256": terminal_proof.evidence_sha256,
        "terminal_surface_contact_proof_status": terminal_proof.status,
        # Compatibility aliases for pre-v0.22 consumers. New readers and cache
        # identities use the surface-contact names above.
        "terminal_artwork_proof_sha256": terminal_proof.evidence_sha256,
        "terminal_artwork_proof_status": terminal_proof.status,
        "device_port_manifest_sha256": sha256(
            _canonical_json(
                {"pins": relevant_pins, "branches": branch_manifest}
            )
        ).hexdigest(),
        "terminal_envelope_origin_um": list(template.origin_um),
        "terminal_envelope_size_um": [
            float(template.plane.width_m) * 1.0e6,
            float(template.plane.height_m) * 1.0e6,
        ],
    }
    evidence = UniformPortConnectivityEvidence(
        reference_net=substrate.reference_net_by_rail_key[rail_key],
        source_net=rail.net,
        source_terminal_component_proven=(
            terminal_proof.source_terminal_artwork_proven
        ),
        reference_terminal_component_proven=(
            terminal_proof.reference_terminal_artwork_proven
        ),
        evidence=(
            f"{LAYERWISE_COMPILER_VERSION}; source={provenance['source_sha256']}; "
            f"substrate={substrate.substrate_identity_sha256}; "
            f"terminal_contact={terminal_proof.evidence_sha256}; "
            "exact v4 external Device quotient-port evidence"
        ),
    )
    return LayerwiseUniformSourceModel(
        substrate=substrate,
        rail_id=rail.rail_id,
        selected_net=rail.net,
        port_connectivity=evidence,
        evidence_sha256=evidence_sha256,
        provenance=provenance,
        uniform_port_scope="external_device_port",
    )


def clear_layerwise_substrate_cache() -> None:
    """Test/diagnostic hook; production callers normally keep the bounded cache."""

    with _EXTERNAL_PORT_VIEW_VALIDATION_CACHE_LOCK:
        _EXTERNAL_PORT_VIEW_VALIDATION_CACHE.clear()
    with _SUBSTRATE_CACHE_LOCK:
        _SUBSTRATE_CACHE.clear()
        _SUBSTRATE_ATTACHMENT_SNAPSHOTS.clear()
    with _FINITE_TOPOLOGY_CACHE_LOCK:
        _FINITE_TOPOLOGY_CACHE.clear()
        _FINITE_TOPOLOGY_ATTACHMENT_SNAPSHOTS.clear()
    clear_surface_certificate_hydration_cache()


__all__ = [
    "CompositeDielectricDispersion",
    "LAYERWISE_COMPILER_VERSION",
    "LayerwiseNetworkSubstrate",
    "LayerwiseNetworkUnavailable",
    "LayerwiseScenarioNetworkBinding",
    "LayerwiseUniformSourceModel",
    "build_layerwise_uniform_source_model",
    "clear_layerwise_substrate_cache",
    "compile_layerwise_substrate",
]
