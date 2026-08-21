"""Read-only raw-SPD / PowerSI correlation and ablation runner.

The PowerSI Touchstone data are comparison-only.  This program deliberately
does not adjust any imported model coefficient from that data: it creates a
fresh scenario bundle from the raw SPD, verifies the complete 92-port header,
then scores the predeclared development and holdout rail groups on both the
fixed 100 kHz--100 MHz critical grid and the complete 1 kHz--1 GHz Evaluation
band.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter
from collections.abc import Callable
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping, NamedTuple
from zipfile import ZipFile

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_SOURCE_ROOT = (_REPOSITORY_ROOT / "src").resolve()
_EXPECTED_PACKAGE_ROOT = (_REPOSITORY_SOURCE_ROOT / "spd_decap_pi").resolve()
sys.path.insert(0, str(_REPOSITORY_SOURCE_ROOT))

import spd_decap_pi as _runtime_package

_runtime_package_file = getattr(_runtime_package, "__file__", None)
_runtime_package_root = (
    Path(_runtime_package_file).resolve().parent
    if _runtime_package_file is not None
    else None
)
if _runtime_package_root != _EXPECTED_PACKAGE_ROOT:
    raise RuntimeError(
        "active-checkout import guard failed: expected spd_decap_pi from "
        f"{_EXPECTED_PACKAGE_ROOT}, imported {_runtime_package_root!s}. "
        "A stale editable install or preloaded package from another worktree "
        "must not run this validation."
    )

import numpy as np

from spd_decap_pi._core.io.spd import analyze_spd
from spd_decap_pi._core.io.touchstone import (
    TouchstoneNetwork,
    open_circuit_zpp,
    powersi_rail_from_header_label,
    read_touchstone,
    s_to_z,
    validate_port_manifest,
)
from spd_decap_pi._core.services import (
    _spd_layer_center_depths,
    _spd_uncalibrated_loop_estimate,
    _spd_via_leg_estimate,
    create_workspace_state,
    scoped_blas_threads,
)
from spd_decap_pi._core.solver.evaluator import (
    CONVERGENCE_POLICY_VERSION,
    DEFAULT_MAX_NEW_FREQUENCY_POINTS,
    DEFAULT_MAX_REFINEMENT_ITERATIONS,
    DEFAULT_MAX_TOLERANCE_DB,
    DEFAULT_PEAK_SHIFT_TOLERANCE_PERCENT,
    DEFAULT_RMS_TOLERANCE_DB,
    EvaluationError,
    SOLVER_VERSION,
    compile_project_evaluation_template,
    evaluate_project_rail_converged,
)
from spd_decap_pi._core.solver.profiles import (
    APPLICATION_DEFAULT_SOLVER_PROFILE_KEY,
    DEFAULT_SOLVER_PROFILE_KEY,
    LAYERWISE_ADMITTANCE_PROFILE,
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
    solver_profile as resolve_solver_profile,
    solver_profile_static_identity_sha256,
)
from spd_decap_pi._core.solver.modal import (
    ModalSolverError,
    copper_slab_surface_impedance_per_square,
)
from spd_decap_pi._core.solver.frequency import DEFAULT_CURVATURE_THRESHOLD_DB
from spd_decap_pi.compiled_topology_asset import (
    validate_compiled_topology_asset_envelope,
)
from spd_decap_pi.evaluation import (
    ScenarioEvaluationBuildError,
    build_evaluation_project,
)
from spd_decap_pi.raw_spatial_contact_asset import (
    RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY,
    RawSpatialSectionCoverageRow,
    RawSpatialSourceCoverageRow,
    load_raw_spatial_contact_asset,
)
from spd_decap_pi.scenario import DecapPadState, ScenarioSpec, SourceIdentity
from spd_decap_pi.scenario_io import ScenarioFormatError, load_scenario_bundle, save_scenario
from spd_decap_pi.spd_adapter import import_spd_scenario
from spd_decap_pi.version import __version__ as APP_VERSION

# These are deliberately exact strings copied from the 92-port PowerSI header,
# rather than a fuzzy VQPS/net search.  They form the predeclared score split.
VQPS_DEVELOPMENT_RAILS = (
    "ADC_VDD_180_VQPS_OTP_TOP_AON/0",
    "ADC_VDD_180_VQPS_SYS_0_AON/0",
    "ADC_VDD_180_VQPS_SYS_1_AON/0",
    "ADC_VDD_180_VQPS_SYS_2_AON/0",
    "ADC_VDD_180_VQPS_SYS_3_AON/0",
)
VQPS_HOLDOUT_RAILS = tuple(item[:-1] + "1" for item in VQPS_DEVELOPMENT_RAILS)
LOADED_FINAL_HOLDOUT_RAILS = (
    "ADC_VDD_055_VTRIP/0",
    "ADC_VDD_055_VTRIP/1",
    "ADC_VDD_070_VINT/0",
    "ADC_VDD_070_VINT/1",
    "ADC_VDD_075_VCPU/0",
    "ADC_VDD_075_VCPU/1",
)
SELECTED_RAILS = VQPS_DEVELOPMENT_RAILS + VQPS_HOLDOUT_RAILS + LOADED_FINAL_HOLDOUT_RAILS
GROUP_BY_RAIL = {
    **{rail: "vqps_development" for rail in VQPS_DEVELOPMENT_RAILS},
    **{rail: "vqps_holdout" for rail in VQPS_HOLDOUT_RAILS},
    **{rail: "loaded_final_holdout" for rail in LOADED_FINAL_HOLDOUT_RAILS},
}

SCORE_LOW_HZ = 1.0e5
SCORE_HIGH_HZ = 1.0e8
SCORE_POINTS = 241
EVALUATION_LOW_HZ = 1.0e3
EVALUATION_HIGH_HZ = 1.0e9
EVALUATION_POINTS = 481
ANCHORS_HZ = (1.0e5, 1.0e6, 1.0e7, 1.0e8)
CORRELATION_REPORT_SCHEMA_VERSION = "powersi-correlation-report-v5"
CORRELATION_REPORT_VERSION = 5
IMPORT_SAVE_REPORT_SCHEMA_VERSION = "candidate-import-save-validation-v1"
LAYERWISE_DIAGNOSTIC_REPORT_SCHEMA_VERSION = (
    "layerwise-single-frequency-diagnostic-v1"
)
TERMINAL_COMPLETE_BATCH_REUSE_VERSION = "terminal-complete-batch-reuse-v1"


class _TerminalCompleteReuseError(ValueError):
    """Raised when a terminal-complete result cannot be reused exactly."""


def _is_frozen_dataclass_instance(value: Any) -> bool:
    parameters = getattr(type(value), "__dataclass_params__", None)
    return bool(parameters is not None and getattr(parameters, "frozen", False))


def _array_identity_sha256(
    values: Any,
    *,
    dtype: str,
    label: str,
) -> str:
    """Return a shape/dtype/value identity for one finite numerical vector."""

    array = np.asarray(values, dtype=np.dtype(dtype))
    if array.ndim != 1 or not array.size:
        raise _TerminalCompleteReuseError(f"{label} must be one non-empty vector")
    if not np.all(np.isfinite(array.real)) or not np.all(np.isfinite(array.imag)):
        raise _TerminalCompleteReuseError(f"{label} must be finite")
    header = json.dumps(
        {
            "identity_schema": "numpy-vector-identity-v1",
            "dtype": array.dtype.str,
            "shape": list(array.shape),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return sha256(header + b"\0" + array.tobytes(order="C")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    text = str(value).strip().casefold()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


class _TerminalCompleteBoardGroup(NamedTuple):
    """Strong references proving one exact immutable mounted-board state."""

    binding: Any
    substrate: Any
    network: Any
    termination_manifest: Any
    solver_profile_key: str
    solver_static_identity_sha256: str
    source_sha256: str
    substrate_identity_sha256: str
    scenario_identity_sha256: str
    termination_manifest_sha256: str
    rail_port_manifest_sha256: str
    port_selector_identity_sha256: str


class _TerminalCompleteBoardReuse:
    """Share all-port board solves only across exact immutable bindings.

    Hash equality alone is intentionally insufficient.  A group match requires
    Python ``is`` identity for the scenario binding, compiled network, and
    compiled termination manifest, plus every stable solver/source identity.
    The first bound substrate is retained strongly and reused so its bounded
    frequency cache serves all rail-specific port projections.
    """

    def __init__(self, solver_profile_key: str) -> None:
        profile = resolve_solver_profile(solver_profile_key)
        if profile != LAYERWISE_ADMITTANCE_PROFILE:
            raise _TerminalCompleteReuseError(
                "board solve reuse is restricted to the terminal-complete profile"
            )
        self.solver_profile_key = profile.key
        self.solver_static_identity_sha256 = solver_profile_static_identity_sha256(
            profile.key
        )
        self._groups: list[_TerminalCompleteBoardGroup] = []
        self.reused_rail_binding_count = 0

    @property
    def group_count(self) -> int:
        return len(self._groups)

    @staticmethod
    def _validated_network_ports(network: Any) -> Mapping[str, Any]:
        """Return the exact immutable compiled-port inventory or fail closed."""

        network_ports = getattr(network, "ports", None)
        if type(network_ports) is not tuple or not network_ports:
            raise _TerminalCompleteReuseError(
                "terminal-complete compiled network port inventory is not immutable"
            )
        by_id: dict[str, Any] = {}
        for port in network_ports:
            if not _is_frozen_dataclass_instance(port):
                raise _TerminalCompleteReuseError(
                    "terminal-complete compiled network port is not frozen"
                )
            port_id = str(getattr(port, "port_id", ""))
            positive = str(getattr(port, "positive_node_id", ""))
            negative = str(getattr(port, "negative_node_id", ""))
            if not port_id or not positive or not negative or positive == negative:
                raise _TerminalCompleteReuseError(
                    "terminal-complete compiled network port identity is invalid"
                )
            if port_id in by_id:
                raise _TerminalCompleteReuseError(
                    "terminal-complete compiled network has duplicate port IDs"
                )
            by_id[port_id] = port

        collapsed_partials = getattr(network, "_collapsed_partials", None)
        if type(collapsed_partials) is not tuple:
            raise _TerminalCompleteReuseError(
                "terminal-complete compiled sparse partial inventory is not immutable"
            )
        for matrix in collapsed_partials:
            for buffer_name in ("data", "indices", "indptr"):
                buffer = getattr(matrix, buffer_name, None)
                if (
                    not isinstance(buffer, np.ndarray)
                    or buffer.flags.writeable
                    or not buffer.flags.owndata
                    or buffer.base is not None
                ):
                    raise _TerminalCompleteReuseError(
                        "terminal-complete compiled sparse partial buffers must be "
                        "owned, unaliased, and read-only"
                    )
        return MappingProxyType(by_id)

    @classmethod
    def _port_selector_identity(cls, substrate: Any) -> str:
        ports = getattr(substrate, "port_by_rail_key", None)
        selected = getattr(substrate, "selected_net_by_rail_key", None)
        references = getattr(substrate, "reference_net_by_rail_key", None)
        if not all(isinstance(item, Mapping) for item in (ports, selected, references)):
            raise _TerminalCompleteReuseError(
                "terminal-complete substrate port selector maps are missing"
            )
        if not all(
            isinstance(item, MappingProxyType)
            for item in (ports, selected, references)
        ):
            raise _TerminalCompleteReuseError(
                "terminal-complete substrate port selector maps must be immutable"
            )
        if not ports or set(ports) != set(selected) or set(ports) != set(references):
            raise _TerminalCompleteReuseError(
                "terminal-complete substrate port selector maps disagree"
            )
        network_ports = cls._validated_network_ports(
            getattr(substrate, "network", None)
        )
        rows = []
        for rail_key in sorted(ports):
            port = ports[rail_key]
            if not _is_frozen_dataclass_instance(port):
                raise _TerminalCompleteReuseError(
                    "terminal-complete compiled rail port is not frozen"
                )
            port_id = str(getattr(port, "port_id", ""))
            network_port = network_ports.get(port_id)
            selector_nodes = (
                str(getattr(port, "positive_node_id", "")),
                str(getattr(port, "negative_node_id", "")),
            )
            network_nodes = (
                str(getattr(network_port, "positive_node_id", "")),
                str(getattr(network_port, "negative_node_id", "")),
            )
            if network_port is None or selector_nodes != network_nodes:
                raise _TerminalCompleteReuseError(
                    "terminal-complete rail selector port/node pair differs from the "
                    "compiled network port"
                )
            if port is not network_port:
                raise _TerminalCompleteReuseError(
                    "terminal-complete rail selector must reference the exact compiled "
                    "network port object"
                )
            rows.append(
                {
                    "rail_key": str(rail_key),
                    "port_id": port_id,
                    "positive_node_id": str(
                        getattr(port, "positive_node_id", "")
                    ),
                    "negative_node_id": str(
                        getattr(port, "negative_node_id", "")
                    ),
                    "selected_net": str(selected[rail_key]),
                    "reference_net": str(references[rail_key]),
                }
            )
        return _json_identity_sha256(rows, label="rail port selector manifest")

    def _validated_group_candidate(
        self,
        bound_source: Any,
        binding: Any,
    ) -> _TerminalCompleteBoardGroup:
        substrate = getattr(bound_source, "substrate", None)
        network = getattr(substrate, "network", None)
        manifest = getattr(bound_source, "termination_manifest", None)
        substrate_manifest = getattr(substrate, "termination_manifest", None)
        if not all(
            _is_frozen_dataclass_instance(item)
            for item in (bound_source, binding, substrate, network, manifest)
        ):
            raise _TerminalCompleteReuseError(
                "terminal-complete reuse requires frozen source, binding, substrate, "
                "network, and termination objects"
            )
        if (
            getattr(bound_source, "uniform_port_scope", None)
            != "external_device_port"
            or getattr(bound_source, "require_termination_manifest", None) is not True
            or manifest is not substrate_manifest
            or network is not getattr(binding, "network", None)
            or manifest is not getattr(binding, "termination_manifest", None)
        ):
            raise _TerminalCompleteReuseError(
                "terminal-complete source is not bound to the exact scenario network/manifest"
            )
        provenance = getattr(bound_source, "provenance", None)
        if not isinstance(provenance, Mapping):
            raise _TerminalCompleteReuseError("terminal-complete source provenance is missing")
        profile_key = str(provenance.get("profile_key", ""))
        static_identity = str(
            provenance.get("static_compiler_algorithm_sha256", "")
        ).casefold()
        source_sha256 = str(provenance.get("source_sha256", "")).casefold()
        substrate_identity = str(
            getattr(substrate, "substrate_identity_sha256", "")
        ).casefold()
        scenario_identity = str(
            getattr(binding, "scenario_identity_sha256", "")
        ).casefold()
        manifest_identity = str(getattr(manifest, "manifest_sha256", "")).casefold()
        rail_port_manifest = str(
            provenance.get("rail_port_manifest_sha256", "")
        ).casefold()
        port_selector_identity = self._port_selector_identity(substrate)
        if (
            profile_key != self.solver_profile_key
            or static_identity != self.solver_static_identity_sha256
            or scenario_identity != substrate_identity
            or str(
                provenance.get("bound_substrate_identity_sha256", "")
            ).casefold()
            != substrate_identity
            or str(provenance.get("scenario_identity_sha256", "")).casefold()
            != scenario_identity
            or str(
                provenance.get("termination_manifest_sha256", "")
            ).casefold()
            != manifest_identity
            or not all(
                _valid_sha256(value)
                for value in (
                    static_identity,
                    source_sha256,
                    substrate_identity,
                    scenario_identity,
                    manifest_identity,
                    rail_port_manifest,
                    port_selector_identity,
                )
            )
        ):
            raise _TerminalCompleteReuseError(
                "terminal-complete solver/source/binding identity is incomplete or inconsistent"
            )
        return _TerminalCompleteBoardGroup(
            binding=binding,
            substrate=substrate,
            network=network,
            termination_manifest=manifest,
            solver_profile_key=profile_key,
            solver_static_identity_sha256=static_identity,
            source_sha256=source_sha256,
            substrate_identity_sha256=substrate_identity,
            scenario_identity_sha256=scenario_identity,
            termination_manifest_sha256=manifest_identity,
            rail_port_manifest_sha256=rail_port_manifest,
            port_selector_identity_sha256=port_selector_identity,
        )

    @staticmethod
    def _same_group(
        left: _TerminalCompleteBoardGroup,
        right: _TerminalCompleteBoardGroup,
    ) -> bool:
        return (
            left.binding is right.binding
            and left.network is right.network
            and left.termination_manifest is right.termination_manifest
            and left.solver_profile_key == right.solver_profile_key
            and left.solver_static_identity_sha256
            == right.solver_static_identity_sha256
            and left.source_sha256 == right.source_sha256
            and left.substrate_identity_sha256 == right.substrate_identity_sha256
            and left.scenario_identity_sha256 == right.scenario_identity_sha256
            and left.termination_manifest_sha256
            == right.termination_manifest_sha256
            and left.rail_port_manifest_sha256
            == right.rail_port_manifest_sha256
            and left.port_selector_identity_sha256
            == right.port_selector_identity_sha256
        )

    def bind(self, source_model: Any, binding: Any) -> tuple[Any, int]:
        bound_source = source_model.with_termination_manifest(binding, required=True)
        candidate = self._validated_group_candidate(bound_source, binding)
        for index, group in enumerate(self._groups):
            if not self._same_group(group, candidate):
                continue
            # Retain rail-specific port evidence/provenance while replacing only
            # the otherwise duplicate selector substrate with the exact first
            # immutable object that owns the shared all-port frequency cache.
            shared = replace(
                bound_source,
                substrate=group.substrate,
                termination_manifest=group.termination_manifest,
            )
            verified = self._validated_group_candidate(shared, binding)
            if (
                verified.substrate is not group.substrate
                or not self._same_group(group, verified)
            ):
                raise _TerminalCompleteReuseError(
                    "canonical board-state rebinding failed exact-object validation"
                )
            self.reused_rail_binding_count += 1
            return shared, index
        self._groups.append(candidate)
        return bound_source, len(self._groups) - 1

    def audit_for(self, source_model: Any, group_index: int) -> dict[str, Any]:
        try:
            group = self._groups[group_index]
        except IndexError as exc:
            raise _TerminalCompleteReuseError("unknown board reuse group") from exc
        manifest = getattr(source_model, "termination_manifest", None)
        substrate = getattr(source_model, "substrate", None)
        if (
            substrate is not group.substrate
            or getattr(substrate, "network", None) is not group.network
            or manifest is not group.termination_manifest
        ):
            raise _TerminalCompleteReuseError(
                "rail source no longer references its exact board reuse group"
            )
        rail_id = str(getattr(source_model, "rail_id", "")).strip()
        rail_key = rail_id.casefold()
        ports = substrate.port_by_rail_key
        selected = substrate.selected_net_by_rail_key
        references = substrate.reference_net_by_rail_key
        port = ports.get(rail_key)
        connectivity = getattr(source_model, "port_connectivity", None)
        source_provenance = getattr(source_model, "provenance", {})
        if (
            not rail_id
            or not _is_frozen_dataclass_instance(connectivity)
            or not isinstance(source_provenance, MappingProxyType)
            or port is None
            or str(getattr(source_model, "selected_net", "")).casefold()
            != str(selected.get(rail_key, "")).casefold()
            or str(getattr(connectivity, "source_net", "")).casefold()
            != str(selected.get(rail_key, "")).casefold()
            or str(getattr(connectivity, "reference_net", "")).casefold()
            != str(references.get(rail_key, "")).casefold()
            or str(source_provenance.get("rail_id", "")).casefold()
            != rail_key
            or str(source_provenance.get("selected_net", "")).casefold()
            != str(selected.get(rail_key, "")).casefold()
            or str(
                source_provenance.get("rail_port_manifest_sha256", "")
            ).casefold()
            != group.rail_port_manifest_sha256
            or self._port_selector_identity(substrate)
            != group.port_selector_identity_sha256
        ):
            raise _TerminalCompleteReuseError(
                "rail source port selector/provenance does not match its board group"
            )
        return {
            "guard_version": TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
            "board_group_index": group_index,
            "solver_profile_key": group.solver_profile_key,
            "solver_static_identity_sha256": group.solver_static_identity_sha256,
            "source_sha256": group.source_sha256,
            "substrate_identity_sha256": group.substrate_identity_sha256,
            "scenario_identity_sha256": group.scenario_identity_sha256,
            "termination_manifest_sha256": group.termination_manifest_sha256,
            "rail_port_manifest_sha256": group.rail_port_manifest_sha256,
            "port_selector_identity_sha256": group.port_selector_identity_sha256,
            "rail_id": rail_id,
            "selected_net": str(selected[rail_key]),
            "reference_net": str(references[rail_key]),
            "port_id": str(port.port_id),
            "port_positive_node_id": str(port.positive_node_id),
            "port_negative_node_id": str(port.negative_node_id),
            "exact_binding_object": True,
            "exact_network_object": True,
            "exact_termination_manifest_object": True,
            "exact_canonical_substrate_object": True,
        }


class _TerminalCompleteSolvedRail(NamedTuple):
    rail_id: str
    source_model: Any
    outcome: Any
    board_frequency_result: Any
    board_audit: Mapping[str, Any]
    source_mode: int
    frequency_grid_sha256: str
    impedance_sha256: str
    source_model_evidence_sha256: str
    solver_provenance_sha256: str
    board_solve_identity_sha256: str
    board_maximum_factor_pivot_ratio: float
    board_maximum_relative_residual: float
    board_maximum_termination_kron_relative_residual: float


def _json_identity_sha256(value: Any, *, label: str) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _TerminalCompleteReuseError(
            f"{label} is not canonical JSON: {exc}"
        ) from exc
    return sha256(encoded).hexdigest()



def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spd", required=True, type=Path, help="raw, undistributed SPD")
    parser.add_argument(
        "--touchstone",
        type=Path,
        help=(
            "PowerSI .s92p reference; required for correlation and intentionally "
            "unused by bounded validation modes"
        ),
    )
    parser.add_argument(
        "--out-dir", required=True, type=Path,
        help="new empty directory for a fresh candidate .spdpi and JSON report",
    )
    parser.add_argument(
        "--modal-max-index", action="append", type=int, choices=(6, 8, 10, 12),
        default=None, help="repeatable modal index; default runs 6 and 12",
    )
    parser.add_argument(
        "--modal-ceiling-index",
        type=int,
        choices=(6, 8, 10, 12),
        default=None,
        help=(
            "optional adaptive ceiling shared by each requested start index; "
            "when omitted every --modal-max-index run is fixed at that index"
        ),
    )
    parser.add_argument(
        "--solver-profile",
        choices=(
            "legacy_modal_v017",
            "layerwise_admittance_v1",
            "research_uniform_admittance",
        ),
        default=APPLICATION_DEFAULT_SOLVER_PROFILE_KEY,
        help="evaluation physics profile (default: application default)",
    )
    parser.add_argument(
        "--legacy-via-ablation", action="store_true",
        help="also solve a temporary in-memory legacy long-barrel via-template variant",
    )
    parser.add_argument(
        "--legacy-via-only",
        action="store_true",
        help=(
            "skip the rejected candidate solve and run only the legacy via ablation; "
            "requires --legacy-via-ablation"
        ),
    )
    parser.add_argument(
        "--reuse-candidate", type=Path,
        help=(
            "resume from an existing candidate .spdpi after verifying its source identity; "
            "stale baseline captures are discarded, never reused"
        ),
    )
    parser.add_argument(
        "--reuse-candidate-import-report",
        type=Path,
        help=(
            "fresh import_save_validation_report.json that binds a reused "
            "candidate to the named raw SPD; mandatory for a release-gated reuse"
        ),
    )
    parser.add_argument(
        "--rail",
        action="append",
        choices=SELECTED_RAILS,
        default=None,
        help=(
            "repeatable predeclared rail subset for a checkpoint/smoke run; "
            "default evaluates all 16 fixed validation rails"
        ),
    )
    parser.add_argument(
        "--require-all-converged",
        action="store_true",
        help=(
            "write the report, then return exit code 2 unless every selected "
            "rail in every requested candidate run completed with combined "
            "frequency/modal convergence"
        ),
    )
    parser.add_argument(
        "--require-terminal-complete-reuse",
        action="store_true",
        help=(
            "fail closed if any requested higher modal run cannot reuse the "
            "terminal-complete source result; never fall back to an independent solve"
        ),
    )
    bounded_mode = parser.add_mutually_exclusive_group()
    bounded_mode.add_argument(
        "--import-save-only",
        action="store_true",
        help=(
            "freshly import the raw SPD, atomically save/reload the candidate, "
            "write import_save_validation_report.json, and execute no frequency solve"
        ),
    )
    bounded_mode.add_argument(
        "--layerwise-diagnostic-frequency-hz",
        type=float,
        help=(
            "execute exactly one layerwise frequency for one --rail (or the first "
            "VQPS development rail) and write layerwise_diagnostic_report.json"
        ),
    )
    args = parser.parse_args(argv)
    if args.legacy_via_only and not args.legacy_via_ablation:
        parser.error("--legacy-via-only requires --legacy-via-ablation")
    diagnostic_requested = args.layerwise_diagnostic_frequency_hz is not None
    bounded_requested = args.import_save_only or diagnostic_requested
    if not bounded_requested and args.touchstone is None:
        parser.error("--touchstone is required unless a bounded validation mode is selected")
    if args.import_save_only and args.reuse_candidate is not None:
        parser.error("--import-save-only requires a fresh import and cannot use --reuse-candidate")
    if (
        args.reuse_candidate_import_report is not None
        and args.reuse_candidate is None
    ):
        parser.error("--reuse-candidate-import-report requires --reuse-candidate")
    if (
        args.require_all_converged
        and args.reuse_candidate is not None
        and args.reuse_candidate_import_report is None
    ):
        parser.error(
            "--require-all-converged with --reuse-candidate requires "
            "--reuse-candidate-import-report"
        )
    if bounded_requested and (
        args.legacy_via_ablation
        or args.legacy_via_only
        or args.require_all_converged
        or args.require_terminal_complete_reuse
        or args.modal_max_index is not None
        or args.modal_ceiling_index is not None
    ):
        parser.error(
            "bounded validation modes cannot be combined with modal, ablation, or "
            "release-convergence options"
        )
    if args.import_save_only and args.rail is not None:
        parser.error("--rail is not used by --import-save-only")
    if diagnostic_requested:
        if (
            not np.isfinite(args.layerwise_diagnostic_frequency_hz)
            or args.layerwise_diagnostic_frequency_hz <= 0.0
        ):
            parser.error("--layerwise-diagnostic-frequency-hz must be finite and positive")
        if args.solver_profile != LAYERWISE_ADMITTANCE_PROFILE.key:
            parser.error(
                "--layerwise-diagnostic-frequency-hz requires "
                f"--solver-profile {LAYERWISE_ADMITTANCE_PROFILE.key}"
            )
        if args.rail is not None and len(dict.fromkeys(args.rail)) != 1:
            parser.error("the layerwise diagnostic accepts exactly one unique --rail")
    if args.require_all_converged:
        selected = tuple(dict.fromkeys(args.rail or SELECTED_RAILS))
        if len(selected) != len(SELECTED_RAILS) or set(selected) != set(
            SELECTED_RAILS
        ):
            parser.error(
                "--require-all-converged requires exactly all 16 predeclared rails"
            )
    return args


def fixed_grid() -> np.ndarray:
    """The predeclared critical-band grid used for release scoring."""

    return np.geomspace(SCORE_LOW_HZ, SCORE_HIGH_HZ, SCORE_POINTS)


def evaluation_grid() -> np.ndarray:
    """The complete application Evaluation band, at the same points/decade."""

    return np.geomspace(
        EVALUATION_LOW_HZ,
        EVALUATION_HIGH_HZ,
        EVALUATION_POINTS,
    )


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _positive_frequency_network(
    network: TouchstoneNetwork,
) -> tuple[TouchstoneNetwork, int]:
    """Drop DC before S-to-Z; a singular DC matrix must not block AC scoring."""

    frequencies = np.asarray(network.frequencies_hz, dtype=np.float64)
    if (
        frequencies.ndim != 1
        or network.s_parameters.shape[0] != frequencies.size
        or not np.all(np.isfinite(frequencies))
        or np.any(frequencies < 0.0)
    ):
        raise ValueError("Touchstone frequencies must be finite and non-negative")
    positive = frequencies > 0.0
    discarded_dc_count = int(np.count_nonzero(~positive))
    if not np.any(positive):
        raise ValueError("Touchstone reference has no positive-frequency records")
    if discarded_dc_count == 0:
        return network, 0
    filtered_frequencies = frequencies[positive].copy()
    filtered_parameters = np.asarray(network.s_parameters)[positive].copy()
    filtered_frequencies.setflags(write=False)
    filtered_parameters.setflags(write=False)
    return (
        TouchstoneNetwork(
            frequencies_hz=filtered_frequencies,
            s_parameters=filtered_parameters,
            reference_ohm=network.reference_ohm,
            port_mapping=dict(network.port_mapping),
            data_format=network.data_format,
        ),
        discarded_dc_count,
    )


def _interpolate_complex(frequencies: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    frequencies = np.asarray(frequencies, dtype=np.float64)
    values = np.asarray(values, dtype=np.complex128)
    if (
        frequencies.ndim != 1
        or values.ndim != 1
        or frequencies.size != values.size
        or frequencies.size < 2
        or not np.all(np.isfinite(frequencies))
        or np.any(frequencies < 0.0)
        or not np.all(np.isfinite(values.real))
        or not np.all(np.isfinite(values.imag))
    ):
        raise ValueError("data must be finite, non-negative, paired complex samples")
    # Touchstone references may include one or more DC samples.  They have no
    # logarithmic coordinate, so remove them before validating/interpolating
    # the positive-frequency trace.  The validation below deliberately still
    # rejects duplicate or decreasing *positive* samples.
    positive = frequencies > 0.0
    frequencies, values = frequencies[positive], values[positive]
    if (
        frequencies.size < 2
        or np.any(frequencies <= 0.0)
        or np.any(np.diff(frequencies) <= 0.0)
        or frequencies[0] > grid[0]
        or frequencies[-1] < grid[-1]
    ):
        raise ValueError("positive-frequency data must be strictly increasing and cover the fixed score grid")
    coordinate = np.log(frequencies)
    target = np.log(grid)
    return np.interp(target, coordinate, values.real) + 1j * np.interp(target, coordinate, values.imag)


def _percentile(values: np.ndarray, percentile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def _crossings(frequencies: np.ndarray, values: np.ndarray) -> list[float]:
    """Log-frequency interpolation of all finite real-part sign crossings."""

    result: list[float] = []
    for index in range(len(frequencies) - 1):
        left, right = float(values[index]), float(values[index + 1])
        if not np.isfinite(left) or not np.isfinite(right):
            continue
        if left == 0.0:
            result.append(float(frequencies[index]))
        elif right == 0.0:
            result.append(float(frequencies[index + 1]))
        elif (left < 0.0) != (right < 0.0):
            fraction = abs(left) / (abs(left) + abs(right))
            result.append(float(np.exp(np.log(frequencies[index]) + fraction * np.log(frequencies[index + 1] / frequencies[index]))))
    return list(dict.fromkeys(result))


def _resonance_candidates(frequencies: np.ndarray, impedance: np.ndarray) -> list[dict[str, float]]:
    magnitude = np.abs(impedance)
    candidates = [
        index for index in range(1, len(frequencies) - 1)
        if magnitude[index] >= magnitude[index - 1] and magnitude[index] >= magnitude[index + 1]
    ]
    return [
        {"frequency_hz": float(frequencies[index]), "magnitude_ohm": float(magnitude[index])}
        for index in candidates
    ]


def _anchor_errors(
    grid: np.ndarray,
    model: np.ndarray,
    reference: np.ndarray,
    *,
    anchors_hz: tuple[float, ...] = ANCHORS_HZ,
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for frequency in anchors_hz:
        index = int(np.argmin(np.abs(np.log(grid / frequency))))
        model_value, reference_value = model[index], reference[index]
        error = model_value - reference_value
        result[f"{frequency / 1e6:g}MHz"] = {
            "model_magnitude_ohm": float(abs(model_value)),
            "reference_magnitude_ohm": float(abs(reference_value)),
            "absolute_complex_error_uohm": float(abs(error) * 1.0e6),
            "signed_magnitude_error_uohm": float((abs(model_value) - abs(reference_value)) * 1.0e6),
            "signed_magnitude_error_db": float(20.0 * np.log10(abs(model_value) / abs(reference_value))),
            "phase_error_deg": float(np.rad2deg(np.angle(model_value / reference_value))),
        }
    return result


def _correlation_metrics_on_grid(
    frequencies: np.ndarray,
    impedance: np.ndarray,
    reference_frequencies: np.ndarray,
    reference_impedance: np.ndarray,
    *,
    grid: np.ndarray,
    anchors_hz: tuple[float, ...] = ANCHORS_HZ,
) -> dict[str, Any]:
    """Compute no-fit complex, magnitude, phase, resonance and sub-mΩ metrics."""

    grid = np.asarray(grid, dtype=np.float64)
    if (
        grid.ndim != 1
        or grid.size < 2
        or not np.all(np.isfinite(grid))
        or np.any(grid <= 0.0)
        or np.any(np.diff(grid) <= 0.0)
    ):
        raise ValueError("correlation grid must be finite, positive, and increasing")
    model = _interpolate_complex(frequencies, impedance, grid)
    reference = _interpolate_complex(reference_frequencies, reference_impedance, grid)
    magnitude_error_db = 20.0 * np.log10(np.abs(model) / np.abs(reference))
    phase_error_deg = np.rad2deg(np.angle(model / reference))
    absolute_error_uohm = np.abs(model - reference) * 1.0e6
    sub_milliohm = np.abs(reference) < 1.0e-3
    sub_errors = absolute_error_uohm[sub_milliohm]
    return {
        "grid": {
            "low_hz": float(grid[0]),
            "high_hz": float(grid[-1]),
            "points": int(grid.size),
        },
        "complex": {
            "rms_uohm": float(np.sqrt(np.mean(absolute_error_uohm**2))),
            "median_uohm": _percentile(absolute_error_uohm, 50.0),
            "p95_uohm": _percentile(absolute_error_uohm, 95.0),
            "max_uohm": float(np.max(absolute_error_uohm)),
        },
        "magnitude_db": {
            "signed_mean_db": float(np.mean(magnitude_error_db)),
            "rms_db": float(np.sqrt(np.mean(magnitude_error_db**2))),
            "median_abs_db": _percentile(np.abs(magnitude_error_db), 50.0),
            "p95_abs_db": _percentile(np.abs(magnitude_error_db), 95.0),
            "max_abs_db": float(np.max(np.abs(magnitude_error_db))),
        },
        "phase_deg": {
            "rms_deg": float(np.sqrt(np.mean(phase_error_deg**2))),
            "p95_abs_deg": _percentile(np.abs(phase_error_deg), 95.0),
            "max_abs_deg": float(np.max(np.abs(phase_error_deg))),
        },
        "anchors": _anchor_errors(
            grid,
            model,
            reference,
            anchors_hz=anchors_hz,
        ),
        "resonances": {
            "model_local_peaks": _resonance_candidates(grid, model),
            "reference_local_peaks": _resonance_candidates(grid, reference),
            "model_imaginary_zero_crossings_hz": _crossings(grid, model.imag),
            "reference_imaginary_zero_crossings_hz": _crossings(grid, reference.imag),
        },
        "sub_1_mohm": {
            "reference_grid_samples": int(np.count_nonzero(sub_milliohm)),
            "complex_rms_uohm": (float(np.sqrt(np.mean(sub_errors**2))) if sub_errors.size else None),
            "complex_p95_uohm": (_percentile(sub_errors, 95.0) if sub_errors.size else None),
            "complex_max_uohm": (float(np.max(sub_errors)) if sub_errors.size else None),
        },
    }


def complete_92_port_manifest(network: TouchstoneNetwork) -> dict[str, int]:
    """Convert only the documented PowerSI header convention into an exact map."""

    ports = network.s_parameters.shape[1]
    if ports != 92:
        raise ValueError(f"expected a 92-port reference, found {ports}")
    if set(network.port_mapping) != set(range(1, ports + 1)):
        raise ValueError("92-port PowerSI header is incomplete")
    result: dict[str, int] = {}
    expected_labels: dict[str, str] = {}
    for port, label in sorted(network.port_mapping.items()):
        try:
            rail = powersi_rail_from_header_label(label)
        except ValueError as exc:
            if "does not match" in str(exc):
                raise ValueError(
                    f"PowerSI header site mismatch at port {port}: {label!r}"
                ) from exc
            raise ValueError(
                f"port {port} does not use the exact PowerSI SITE header convention"
            ) from exc
        if rail in result:
            raise ValueError(f"92-port header maps multiple ports to {rail!r}")
        result[rail] = port
        expected_labels[rail] = label
    validate_port_manifest(
        network,
        result,
        expected_header_labels=expected_labels,
        require_complete_header=True,
    )
    return result


def validate_selected_port_manifest(
    network: TouchstoneNetwork,
    selected_ports: Mapping[str, int],
) -> None:
    """Revalidate a selected subset against its already-proven exact labels."""

    validate_port_manifest(network, selected_ports)


def _source_identity_report(spd: Path, scenario: Any) -> dict[str, Any]:
    actual = SourceIdentity.from_path(spd)
    if scenario.source.sha256 != actual.sha256 or scenario.source.size_bytes != actual.size_bytes:
        raise ValueError("candidate bundle source identity does not match the raw SPD")
    return {
        "basename": actual.name,
        "size_bytes": actual.size_bytes,
        "sha256": actual.sha256,
        "embedded_in_bundle": False,
    }


def _source_state_mismatches(scenario: Any) -> tuple[str, ...]:
    """Return mutable decap edits that make a reuse unsafe for raw-SPD scoring."""

    mismatches: list[str] = []
    for decap in scenario.decaps:
        fields: list[str] = []
        if decap.current_net.casefold() != decap.source_net.casefold():
            fields.append("current_net")
        if decap.current_rail_id.casefold() != decap.source_rail_id.casefold():
            fields.append("current_rail_id")
        if decap.enabled is not decap.source_mounted:
            fields.append("enabled")
        if decap.pad_state != DecapPadState.NORMAL:
            fields.append("pad_state")
        current_model = (
            decap.model_id.casefold() if decap.model_id is not None else None
        )
        source_model = (
            decap.source_model_id.casefold()
            if decap.source_model_id is not None
            else None
        )
        if current_model != source_model:
            fields.append("model_id")
        if fields:
            mismatches.append(f"{decap.refdes}({','.join(fields)})")
    return tuple(mismatches)


def _validate_pristine_reusable_candidate(scenario: Any) -> dict[str, Any]:
    mismatches = _source_state_mismatches(scenario)
    if mismatches:
        examples = ", ".join(mismatches[:8])
        suffix = f", +{len(mismatches) - 8} more" if len(mismatches) > 8 else ""
        raise ValueError(
            "reused candidate is not a pristine raw-SPD import; mutable decap "
            f"state differs from its immutable source: {examples}{suffix}"
        )
    return {
        "status": "validated",
        "checked_decap_count": len(scenario.decaps),
        "mutable_fields": [
            "current_net",
            "current_rail_id",
            "enabled",
            "pad_state",
            "model_id",
        ],
        "mismatch_count": 0,
    }


def _compiled_topology_asset_identity(
    scenario: Any,
    attachments: Mapping[str, bytes],
    *,
    required: bool,
) -> dict[str, Any]:
    """Validate and disclose the persisted topology envelope used by reuse."""

    manifest = validate_compiled_topology_asset_envelope(
        scenario.normalized_project,
        attachments,
    )
    if manifest is None:
        if required:
            raise ValueError(
                "release-gated layerwise correlation requires a compiled topology asset"
            )
        return {"status": "absent", "required": False, "manifest": None}
    identity_fields = (
        "storage_schema",
        "payload_schema",
        "compiler_id",
        "surface_schema_version",
        "surface_compiler_id",
        "source_sha256",
        "certificate_evidence_sha256",
        "surface_asset_uncompressed_sha256",
        "project_binding_sha256",
        "topology_identity_sha256",
        "logical_rows_sha256",
        "asset_name",
        "compression",
        "compressed_size_bytes",
        "compressed_sha256",
        "uncompressed_size_bytes",
        "uncompressed_sha256",
    )
    disclosed = {field: manifest[field] for field in identity_fields}
    manifest_identity_sha256 = sha256(
        json.dumps(
            disclosed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "status": "validated",
        "required": bool(required),
        "validation_scope": "manifest_project_binding_and_compressed_envelope",
        "manifest_identity_sha256": manifest_identity_sha256,
        "manifest": disclosed,
    }


def _raw_spatial_contact_asset_identity(
    scenario: Any,
    attachments: Mapping[str, bytes],
    *,
    compiled_topology_asset: Mapping[str, Any],
) -> dict[str, Any]:
    """Fully load and disclose the raw asset from one reloaded bundle."""

    project = scenario.normalized_project
    metadata = (
        project.get("metadata")
        if isinstance(project, Mapping)
        else getattr(project, "metadata", None)
    )
    spd_import = (
        metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    )
    raw_present = (
        isinstance(spd_import, Mapping)
        and RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY in spd_import
    )
    raw_manifest = (
        spd_import[RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY]
        if raw_present
        else None
    )
    compiled_validated = compiled_topology_asset.get("status") == "validated"
    if not raw_present:
        if compiled_validated:
            raise ValueError(
                "reloaded compiled-topology candidate lacks raw spatial metadata"
            )
        return {
            "status": "absent",
            "required": False,
            "full_loader_status": "not_applicable",
            "manifest": None,
            "counts": None,
        }
    if not compiled_validated:
        raise ValueError(
            "raw spatial metadata requires a validated compiled topology asset"
        )
    if not isinstance(raw_manifest, Mapping):
        raise ValueError("reloaded raw spatial metadata is not a manifest")
    compiled_manifest = compiled_topology_asset.get("manifest")
    if not isinstance(compiled_manifest, Mapping):
        raise ValueError("validated compiled topology report lacks its manifest")
    if not isinstance(spd_import, Mapping):
        raise ValueError("reloaded project lacks SPD import metadata")

    expected_source = compiled_manifest.get("source_sha256")
    if (
        not _valid_sha256(expected_source)
        or str(spd_import.get("source_sha256", "")).casefold()
        != str(expected_source).casefold()
        or str(scenario.source.sha256).casefold()
        != str(expected_source).casefold()
    ):
        raise ValueError("raw spatial source binding differs after reload")
    expected = {
        "expected_source_sha256": str(expected_source),
        "expected_project_binding_sha256": str(
            compiled_manifest.get("project_binding_sha256", "")
        ),
        "expected_certificate_evidence_sha256": str(
            compiled_manifest.get("certificate_evidence_sha256", "")
        ),
        "expected_compiled_topology_identity_sha256": str(
            compiled_manifest.get("topology_identity_sha256", "")
        ),
        "expected_geometry_identity_sha256": str(
            raw_manifest.get("geometry_identity_sha256", "")
        ),
    }
    if any(not _valid_sha256(value) for value in expected.values()):
        raise ValueError("raw spatial persisted binding identity is invalid")
    raw_binding_fields = {
        "source_sha256": expected["expected_source_sha256"],
        "project_binding_sha256": expected[
            "expected_project_binding_sha256"
        ],
        "certificate_evidence_sha256": expected[
            "expected_certificate_evidence_sha256"
        ],
        "compiled_topology_identity_sha256": expected[
            "expected_compiled_topology_identity_sha256"
        ],
        "geometry_identity_sha256": expected[
            "expected_geometry_identity_sha256"
        ],
    }
    if any(
        str(raw_manifest.get(field, "")).casefold() != value.casefold()
        for field, value in raw_binding_fields.items()
    ):
        raise ValueError("raw spatial persisted binding differs after reload")

    with load_raw_spatial_contact_asset(
        raw_manifest,
        attachments,
        **expected,
    ) as loaded:
        disclosed = dict(loaded.manifest)
        loaded_counts = loaded.manifest.get("counts")
        if not isinstance(loaded_counts, Mapping):
            raise ValueError("raw spatial loaded counts are invalid")
        disclosed["counts"] = dict(loaded_counts)
        source_coverage_rows = tuple(loaded.iter_source_coverage())
        section_coverage_rows = tuple(loaded.iter_section_coverage())
    if (
        len(source_coverage_rows) != 1
        or type(source_coverage_rows[0]) is not RawSpatialSourceCoverageRow
    ):
        raise ValueError("raw spatial source coverage inventory differs")
    if (
        len(section_coverage_rows) != 3
        or any(type(row) is not RawSpatialSectionCoverageRow for row in section_coverage_rows)
        or {row.section_name for row in section_coverage_rows} != {"Node", "Trace", "Via"}
    ):
        raise ValueError("raw spatial section coverage inventory differs")
    source_coverage = asdict(source_coverage_rows[0])
    section_coverage = {
        row.section_name: asdict(row)
        for row in sorted(section_coverage_rows, key=lambda item: item.ordinal)
    }
    persisted = dict(raw_manifest)
    persisted_counts = persisted.get("counts")
    if not isinstance(persisted_counts, Mapping):
        raise ValueError("raw spatial persisted counts are invalid")
    persisted["counts"] = dict(persisted_counts)
    counts = dict(disclosed["counts"])
    if counts != dict(persisted_counts):
        raise ValueError("raw spatial loaded counts drifted after reload")
    disclosed_identity = dict(disclosed)
    persisted_identity = dict(persisted)
    del disclosed_identity["counts"]
    del persisted_identity["counts"]
    if disclosed_identity != persisted_identity:
        raise ValueError("raw spatial loaded manifest identity drifted after reload")
    manifest_identity_sha256 = sha256(
        json.dumps(
            disclosed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "status": "validated",
        "required": True,
        "validation_scope": (
            "persisted_bindings_compressed_envelope_and_full_sqlite_loader"
        ),
        "full_loader_status": "validated",
        "manifest_identity_sha256": manifest_identity_sha256,
        "asset_name": disclosed["asset_name"],
        "geometry_identity_sha256": disclosed["geometry_identity_sha256"],
        "logical_rows_sha256": disclosed["logical_rows_sha256"],
        "counts": counts,
        "source_coverage": source_coverage,
        "section_coverage": section_coverage,
        "loaded_manifest_matches_persisted": True,
        "loaded_counts_match_persisted": True,
        "manifest": disclosed,
    }


def _validate_fresh_import_report_binding(
    report_path: Path,
    *,
    source: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
    compiled_topology_asset: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind release reuse to one exact, freshly imported candidate artifact."""

    if not report_path.is_file():
        raise ValueError("--reuse-candidate-import-report must name an existing file")
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate import report is not readable valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("candidate import report root must be an object")
    expected_scalars = {
        "schema_version": IMPORT_SAVE_REPORT_SCHEMA_VERSION,
        "report_version": 1,
        "app_version": APP_VERSION,
        "mode": "import_save_only",
        "status": "passed",
        "frequency_solves_executed": 0,
        "touchstone_read": False,
    }
    for field, expected in expected_scalars.items():
        if raw.get(field) != expected:
            raise ValueError(
                f"candidate import report {field} {raw.get(field)!r} != {expected!r}"
            )
    imported = raw.get("import")
    if not isinstance(imported, Mapping) or imported.get("mode") != "fresh_import":
        raise ValueError("candidate import report does not describe a fresh import")
    reported_source = raw.get("source")
    if not isinstance(reported_source, Mapping) or (
        str(reported_source.get("sha256", "")).casefold()
        != str(source.get("sha256", "")).casefold()
        or int(reported_source.get("size_bytes", -1))
        != int(source.get("size_bytes", -1))
    ):
        raise ValueError("candidate import report raw-SPD identity does not match")
    reported_candidate = raw.get("candidate_bundle")
    if not isinstance(reported_candidate, Mapping) or (
        reported_candidate.get("basename") != candidate_bundle.get("basename")
        or str(reported_candidate.get("sha256", "")).casefold()
        != str(candidate_bundle.get("sha256", "")).casefold()
        or int(reported_candidate.get("size_bytes", -1))
        != int(candidate_bundle.get("size_bytes", -1))
    ):
        raise ValueError("candidate import report bundle identity does not match")
    atomic = raw.get("atomic_save_load_validation")
    atomic_fields = (
        "archive_manifest_and_member_hashes_validated",
        "scenario_schema_validated_after_reload",
        "source_identity_validated_after_reload",
        "attachment_hashes_validated_after_reload",
    )
    if not isinstance(atomic, Mapping) or any(
        atomic.get(field) is not True for field in atomic_fields
    ):
        raise ValueError("candidate import report lacks atomic reload validation")
    reported_compiled = raw.get("compiled_topology_asset")
    if (
        compiled_topology_asset.get("status") == "validated"
        and reported_compiled is not None
        and (
            not isinstance(reported_compiled, Mapping)
            or reported_compiled.get("status") != "validated"
            or reported_compiled.get("manifest_identity_sha256")
            != compiled_topology_asset.get("manifest_identity_sha256")
        )
    ):
        raise ValueError("candidate import report compiled topology identity does not match")
    return {
        "status": "validated",
        "basename": report_path.name,
        "sha256": _hash(report_path),
        "fresh_import_mode": True,
        "source_identity_match": True,
        "candidate_bundle_identity_match": True,
        "compiled_topology_identity_match": (
            compiled_topology_asset.get("status") == "validated"
        ),
        "compiled_topology_report_identity_present": isinstance(
            reported_compiled, Mapping
        ),
        "compiled_topology_binding": (
            "explicit_import_report_manifest"
            if isinstance(reported_compiled, Mapping)
            else "exact_candidate_sha256_plus_current_envelope"
        ),
    }


def _profile_compiler_version(solver_profile_key: str) -> str:
    """Return the concrete compiler implementation identity for one profile."""

    profile = resolve_solver_profile(solver_profile_key)
    if profile == LAYERWISE_ADMITTANCE_PROFILE:
        from spd_decap_pi._core.solver.layerwise_network import (
            LAYERWISE_COMPILER_VERSION,
        )

        return LAYERWISE_COMPILER_VERSION
    if profile == RESEARCH_UNIFORM_ADMITTANCE_PROFILE:
        from spd_decap_pi._core.solver.research_uniform_profile import (
            PROFILE_COMPILER_VERSION,
        )

        return PROFILE_COMPILER_VERSION
    # The legacy profile has no source compiler separate from its immutable
    # regression algorithm identity.
    return profile.compiler_algorithm_id


def _solver_identity_report(solver_profile_key: str) -> dict[str, str]:
    """Build the immutable solver/profile/compiler part of report v4."""

    profile = resolve_solver_profile(solver_profile_key)
    return {
        "solver_version": SOLVER_VERSION,
        "solver_profile_key": profile.key,
        "compiler_algorithm_id": profile.compiler_algorithm_id,
        "compiler_version": _profile_compiler_version(profile.key),
        "static_compiler_algorithm_sha256": (
            solver_profile_static_identity_sha256(profile)
        ),
    }


def _convergence_policy_report(
    *,
    modal_start_index: int | None = None,
    modal_ceiling_index: int | None = None,
) -> dict[str, Any]:
    """Return the production adaptive-frequency budget used by every run."""

    policy: dict[str, Any] = {
        "version": CONVERGENCE_POLICY_VERSION,
        "max_refinement_iterations": DEFAULT_MAX_REFINEMENT_ITERATIONS,
        "max_new_frequency_points_per_iteration": (
            DEFAULT_MAX_NEW_FREQUENCY_POINTS
        ),
        "curvature_threshold_db": DEFAULT_CURVATURE_THRESHOLD_DB,
        "rms_tolerance_db": DEFAULT_RMS_TOLERANCE_DB,
        "max_tolerance_db": DEFAULT_MAX_TOLERANCE_DB,
        "peak_shift_tolerance_percent": DEFAULT_PEAK_SHIFT_TOLERANCE_PERCENT,
    }
    if modal_start_index is not None or modal_ceiling_index is not None:
        if modal_start_index is None or modal_ceiling_index is None:
            raise ValueError("modal start and ceiling must be reported together")
        if modal_ceiling_index < modal_start_index:
            raise ValueError("modal convergence ceiling cannot be below its start")
        policy.update(
            {
                "modal_start_index": modal_start_index,
                "modal_ceiling_index": modal_ceiling_index,
            }
        )
    return policy


def _validated_report_identity_fields(
    *,
    source: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
    candidate_source: Any,
    candidate_runs: Mapping[str, Any],
    solver_profile_key: str,
    compiled_topology_asset: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return v4 identity fields after fail-closed cross-provenance checks.

    The raw-SPD identity is checked once while loading the candidate and again
    here against every completed source-derived rail outcome.  A report is not
    written if a solver/profile/compiler/source identity drifts mid-run.
    """

    solver_identity = _solver_identity_report(solver_profile_key)
    convergence_policy = _convergence_policy_report()
    profile = resolve_solver_profile(solver_profile_key)
    source_sha256 = str(source.get("sha256", "")).casefold()
    source_size_bytes = int(source.get("size_bytes", -1))
    candidate_source_sha256 = str(
        getattr(candidate_source, "sha256", "")
    ).casefold()
    candidate_source_size_bytes = int(
        getattr(candidate_source, "size_bytes", -1)
    )
    candidate_bundle_sha256 = str(
        candidate_bundle.get("sha256", "")
    ).casefold()

    def is_sha256(value: str) -> bool:
        return len(value) == 64 and all(character in "0123456789abcdef" for character in value)

    if not is_sha256(source_sha256):
        raise ValueError("raw SPD report identity is not a valid SHA-256")
    if not is_sha256(candidate_source_sha256):
        raise ValueError("candidate source identity is not a valid SHA-256")
    if not is_sha256(candidate_bundle_sha256):
        raise ValueError("candidate bundle identity is not a valid SHA-256")
    if (
        candidate_source_sha256 != source_sha256
        or candidate_source_size_bytes != source_size_bytes
    ):
        raise ValueError("candidate bundle source identity does not match the raw SPD")

    provenance_expected: dict[str, Any] = {"profile_key": profile.key}
    required_provenance_hash_fields: tuple[str, ...] = ()
    if profile == LAYERWISE_ADMITTANCE_PROFILE:
        provenance_expected.update(
            {
                "source_sha256": source_sha256,
                "compiler_algorithm_id": solver_identity["compiler_algorithm_id"],
                "compiler_version": solver_identity["compiler_version"],
                "static_compiler_algorithm_sha256": solver_identity[
                    "static_compiler_algorithm_sha256"
                ],
                "source_only": True,
                "powersi_used_for_parameters": False,
                "termination_manifest_required": True,
                "modal_convergence_applicability": "not_applicable",
                "modal_order_invariance": (
                    "analytic_terminal_complete_external_device_port"
                ),
                "modal_convergence_solve_count": 0,
                "frequency_convergence_pass_count": 1,
            }
        )
        required_provenance_hash_fields = (
            "geometry_manifest_sha256",
            "material_manifest_sha256",
            "substrate_identity_sha256",
            "base_layerwise_evidence_sha256",
            "termination_manifest_sha256",
            "bound_substrate_identity_sha256",
            "layerwise_identity_sha256",
            "surface_connectivity_evidence_sha256",
            "rail_port_manifest_sha256",
        )
        if (
            isinstance(compiled_topology_asset, Mapping)
            and compiled_topology_asset.get("status") == "validated"
        ):
            compiled_manifest = compiled_topology_asset.get("manifest")
            if not isinstance(compiled_manifest, Mapping):
                raise ValueError("compiled topology identity manifest is missing")
            provenance_expected["surface_connectivity_evidence_sha256"] = str(
                compiled_manifest.get("certificate_evidence_sha256", "")
            ).casefold()
    elif profile == RESEARCH_UNIFORM_ADMITTANCE_PROFILE:
        provenance_expected.update(
            {
                "source_sha256": source_sha256,
                "static_compiler_algorithm_sha256": solver_identity[
                    "static_compiler_algorithm_sha256"
                ],
            }
        )

    completed_outcome_count = 0
    mismatches: list[str] = []
    for mode, run in candidate_runs.items():
        run_profile = run.get("solver_profile")
        if run_profile is not None and run_profile != profile.key:
            mismatches.append(
                f"mode {mode}: run solver_profile {run_profile!r} != {profile.key!r}"
            )
        for rail_id, outcome in run.get("rails", {}).items():
            status = outcome.get("status")
            completed = status == "completed" or (
                status is None and "solver_version" in outcome
            )
            if not completed:
                continue
            completed_outcome_count += 1
            prefix = f"mode {mode} rail {rail_id}"
            if outcome.get("solver_version") != solver_identity["solver_version"]:
                mismatches.append(
                    f"{prefix}: solver_version {outcome.get('solver_version')!r} != "
                    f"{solver_identity['solver_version']!r}"
                )
            if outcome.get("solver_profile_key") != profile.key:
                mismatches.append(
                    f"{prefix}: solver_profile_key "
                    f"{outcome.get('solver_profile_key')!r} != {profile.key!r}"
                )
            run_modal_start = int(run.get("modal_max_index", mode))
            run_modal_ceiling = int(
                run.get("modal_convergence_ceiling_index", run_modal_start)
            )
            expected_run_policy = _convergence_policy_report(
                modal_start_index=run_modal_start,
                modal_ceiling_index=run_modal_ceiling,
            )
            if outcome.get("convergence_policy") != expected_run_policy:
                mismatches.append(
                    f"{prefix}: convergence_policy "
                    f"{outcome.get('convergence_policy')!r} != "
                    f"{expected_run_policy!r}"
                )
            provenance = outcome.get("solver_provenance")
            if not isinstance(provenance, Mapping):
                mismatches.append(f"{prefix}: solver_provenance is missing")
                continue
            for field, expected in provenance_expected.items():
                actual = provenance.get(field)
                if isinstance(expected, str) and field.endswith("sha256"):
                    actual = str(actual or "").casefold()
                if actual != expected:
                    mismatches.append(
                        f"{prefix}: solver_provenance.{field} {actual!r} != "
                        f"{expected!r}"
                    )
            for field in required_provenance_hash_fields:
                actual_hash = str(provenance.get(field, "")).casefold()
                if not is_sha256(actual_hash):
                    mismatches.append(
                        f"{prefix}: solver_provenance.{field} is not a SHA-256"
                    )
            if profile == LAYERWISE_ADMITTANCE_PROFILE:
                if (
                    str(provenance.get("rail_id", "")).casefold()
                    != str(rail_id).casefold()
                ):
                    mismatches.append(
                        f"{prefix}: solver_provenance.rail_id does not match the report rail"
                    )
                if not str(provenance.get("selected_net", "")).strip():
                    mismatches.append(
                        f"{prefix}: solver_provenance.selected_net is blank"
                    )
                if (
                    provenance.get("bound_substrate_identity_sha256")
                    != provenance.get("substrate_identity_sha256")
                ):
                    mismatches.append(
                        f"{prefix}: bound substrate identity differs from substrate identity"
                    )
                convergence = outcome.get("convergence")
                if not isinstance(convergence, Mapping):
                    mismatches.append(f"{prefix}: convergence report is missing")
                else:
                    if convergence.get("policy_version") != CONVERGENCE_POLICY_VERSION:
                        mismatches.append(
                            f"{prefix}: convergence policy_version is inconsistent"
                        )
                    frequency_converged = convergence.get("frequency_converged")
                    combined_converged = convergence.get("converged")
                    if (
                        frequency_converged is not True
                        and frequency_converged is not False
                    ):
                        mismatches.append(
                            f"{prefix}: frequency_converged is not boolean"
                        )
                    budget_exhausted = convergence.get(
                        "frequency_budget_exhausted"
                    )
                    if budget_exhausted is not True and budget_exhausted is not False:
                        mismatches.append(
                            f"{prefix}: frequency_budget_exhausted is not boolean"
                        )
                    if convergence.get("modal_converged") is not True:
                        mismatches.append(
                            f"{prefix}: terminal-complete modal invariance is not passing"
                        )
                    if combined_converged is not frequency_converged:
                        mismatches.append(
                            f"{prefix}: combined convergence does not equal frequency convergence"
                        )
                    if (
                        convergence.get("lower_mode_x")
                        != convergence.get("final_mode_x")
                        or convergence.get("lower_mode_y")
                        != convergence.get("final_mode_y")
                    ):
                        mismatches.append(
                            f"{prefix}: terminal-complete modal indices are not invariant"
                        )
                    for field in (
                        "modal_rms_delta_db",
                        "modal_max_delta_db",
                        "modal_peak_shift_percent",
                    ):
                        if convergence.get(field) != 0.0:
                            mismatches.append(
                                f"{prefix}: {field} is not exact zero"
                            )
    if mismatches:
        detail = "; ".join(mismatches[:8])
        if len(mismatches) > 8:
            detail += f"; +{len(mismatches) - 8} more"
        raise ValueError(f"candidate rail outcome identity mismatch: {detail}")

    provenance_fields = list(
        dict.fromkeys(
            [
                "solver_version",
                "solver_profile_key",
                "convergence_policy",
                *(f"solver_provenance.{field}" for field in provenance_expected),
                *(
                    f"solver_provenance.{field}"
                    for field in required_provenance_hash_fields
                ),
                *(
                    (
                        "convergence.policy_version",
                        "convergence.frequency_converged",
                        "convergence.frequency_budget_exhausted",
                        "convergence.modal_converged",
                        "convergence.converged",
                        "convergence.modal_indices_equal",
                        "convergence.modal_deltas_exact_zero",
                        "solver_provenance.rail_id_matches_report_rail",
                        "solver_provenance.selected_net_nonblank",
                    )
                    if profile == LAYERWISE_ADMITTANCE_PROFILE
                    else ()
                ),
            ]
        )
    )
    return {
        "schema_version": CORRELATION_REPORT_SCHEMA_VERSION,
        "report_version": CORRELATION_REPORT_VERSION,
        "app_version": APP_VERSION,
        **solver_identity,
        "convergence_policy": convergence_policy,
        "identity": {
            "validation_status": (
                "validated"
                if completed_outcome_count
                else "input_identity_validated_no_completed_outcomes"
            ),
            "source_sha256": source_sha256,
            "source_size_bytes": source_size_bytes,
            "candidate_bundle_sha256": candidate_bundle_sha256,
            "candidate_source_sha256": candidate_source_sha256,
            "candidate_source_size_bytes": candidate_source_size_bytes,
            "source_candidate_match": True,
            "solver": solver_identity,
            "convergence_policy": convergence_policy,
            "rail_outcome_provenance": {
                "completed_outcome_count": completed_outcome_count,
                "validated_outcome_count": completed_outcome_count,
                "all_completed_outcomes_match": True,
                "checked_fields": provenance_fields,
                "mismatches": [],
            },
        },
    }


def correlation_metrics(
    frequencies: np.ndarray,
    impedance: np.ndarray,
    reference_frequencies: np.ndarray,
    reference_impedance: np.ndarray,
) -> dict[str, Any]:
    """Compute the fixed critical-band release metrics."""

    return _correlation_metrics_on_grid(
        frequencies,
        impedance,
        reference_frequencies,
        reference_impedance,
        grid=fixed_grid(),
    )


def correlation_metric_bands(
    frequencies: np.ndarray,
    impedance: np.ndarray,
    reference_frequencies: np.ndarray,
    reference_impedance: np.ndarray,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return critical-band and complete Evaluation-band metrics without fitting."""

    critical = correlation_metrics(
        frequencies,
        impedance,
        reference_frequencies,
        reference_impedance,
    )
    full_band = _correlation_metrics_on_grid(
        frequencies,
        impedance,
        reference_frequencies,
        reference_impedance,
        grid=evaluation_grid(),
    )
    return critical, full_band


def _load_reusable_candidate(candidate_path: Path) -> tuple[Any, dict[str, Any]]:
    """Load a verified candidate without re-importing an unchanged raw SPD.

    A candidate's stored baseline captures are outputs of an older solver
    fingerprint.  They are not inputs to this correlation run.  If they alone
    prevent current-schema loading, validate every archive member/hash, discard
    only those stale captures, and validate the remaining scenario normally.
    """

    try:
        return load_scenario_bundle(candidate_path), {
            "mode": "validated_bundle", "stale_baseline_captures_discarded": False,
        }
    except ScenarioFormatError as exc:
        if "baseline solver inputs" not in str(exc):
            raise
    with ZipFile(candidate_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        scenario_bytes = archive.read("scenario.json")
        if (
            int(manifest.get("scenario_size", -1)) != len(scenario_bytes)
            or str(manifest.get("scenario_sha256", "")).casefold()
            != sha256(scenario_bytes).hexdigest()
        ):
            raise ValueError("reused candidate scenario.json fails manifest integrity")
        raw = json.loads(scenario_bytes)
        entries = manifest.get("attachments")
        if not isinstance(entries, list):
            raise ValueError("reused candidate attachment manifest is invalid")
        attachments: dict[str, bytes] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("reused candidate attachment entry is invalid")
            name, archive_path = entry.get("name"), entry.get("path")
            if not isinstance(name, str) or not isinstance(archive_path, str):
                raise ValueError("reused candidate attachment identity is invalid")
            content = archive.read(archive_path)
            if (
                int(entry.get("size", -1)) != len(content)
                or str(entry.get("sha256", "")).casefold() != sha256(content).hexdigest()
            ):
                raise ValueError(f"reused candidate attachment {name!r} fails manifest integrity")
            attachments[name] = content
    stale_count = len(raw.get("baseline_captures", {}))
    stale_cache_count = len(raw.get("evaluation_cache", {}))
    raw["baseline_captures"] = {}
    raw["evaluation_cache"] = {}
    scenario = ScenarioSpec.model_validate(raw)
    if set(scenario.attachment_names) != set(attachments) or {
        name: sha256(content).hexdigest() for name, content in attachments.items()
    } != scenario.attachment_hashes:
        raise ValueError("reused candidate attachment declarations do not match verified contents")
    return SimpleNamespace(scenario=scenario, attachments=attachments), {
        "mode": "verified_bundle_stale_baselines_discarded",
        "stale_baseline_captures_discarded": True,
        "discarded_capture_count": stale_count,
        "discarded_cached_evaluation_metadata_count": stale_cache_count,
    }


def _legacy_template_project(project: Any, analysis: Any) -> tuple[Any, dict[str, Any]]:
    """Make a transient source-only legacy R/L variant; never save or fit it."""

    padstacks = {item.name.casefold(): item for item in analysis.padstacks}
    centers = _spd_layer_center_depths(project.stackup_layers)
    top = next(item.name for item in project.stackup_layers if item.is_conductor)
    provenance = project.metadata.get("spd_via_template_provenance", {})
    if not isinstance(provenance, Mapping):
        raise ValueError("fresh SPD project has no via-template provenance")
    rail_by_id = {item.rail_id: item for item in project.rails}
    templates = []
    changed: dict[str, Any] = {}
    for template in project.via_templates:
        evidence = provenance.get(template.template_id)
        rail = rail_by_id.get(str(evidence.get("rail_id", ""))) if isinstance(evidence, Mapping) else None
        if rail is None:
            templates.append(template)
            continue

        def match(keys: set[str], layer: str):
            candidates = []
            for usage in analysis.via_usage:
                if usage.net.casefold() not in keys:
                    continue
                padstack = padstacks.get(usage.padstack.casefold())
                if padstack is not None and layer.casefold() in {str(item).casefold() for item in padstack.layers}:
                    candidates.append((usage, padstack))
            return sorted(candidates, key=lambda item: (-int(item[0].count), item[0].padstack.casefold()))[0] if candidates else (None, None)

        power_usage, power = match({rail.net.casefold()}, rail.pwr_layer)
        ground_usage, ground = match({item.casefold() for item in project.gnd_aliases}, rail.gnd_layer)
        del power_usage, ground_usage
        drills = [item.drill_diameter_um for item in (power, ground) if item is not None and item.drill_diameter_um]
        power_leg = _spd_via_leg_estimate(
            padstack=power, length_um=centers[rail.pwr_layer], start_layer=top,
            end_layer=rail.pwr_layer, stackup_layers=project.stackup_layers,
        )
        ground_leg = _spd_via_leg_estimate(
            padstack=ground, length_um=centers[rail.gnd_layer], start_layer=top,
            end_layer=rail.gnd_layer, stackup_layers=project.stackup_layers,
        )
        resistance, inductance = _spd_uncalibrated_loop_estimate(
            pwr_depth_um=centers[rail.pwr_layer], gnd_depth_um=centers[rail.gnd_layer],
            drill_diameter_um=min(drills) if drills else None, power_leg=power_leg, ground_leg=ground_leg,
        )
        templates.append(template.model_copy(update={"loop_resistance_ohm": resistance, "loop_inductance_h": inductance, "impedance": []}))
        changed[template.template_id] = {
            "rail_id": rail.rail_id, "legacy_R_ohm": resistance, "legacy_L_h": inductance,
            "candidate_R_ohm": template.loop_resistance_ohm, "candidate_L_h": template.loop_inductance_h,
        }
    return project.model_copy(update={"via_templates": templates}), changed


def _build_bound_layerwise_source_model(
    scenario: ScenarioSpec,
    project: Any,
    attachments: Mapping[str, bytes],
    rail_id: str,
    template: Any,
    *,
    board_reuse: _TerminalCompleteBoardReuse | None = None,
    board_audit_sink: dict[str, Mapping[str, Any]] | None = None,
) -> Any:
    """Build the production source model with its exact mounted-board state.

    A bare layerwise substrate intentionally contains no scenario decap
    terminations.  Correlation must exercise the same fail-closed binding as
    the GUI; otherwise the PowerSI comparison silently scores an unloaded
    board and cannot be used as release evidence.
    """

    from spd_decap_pi._core.solver.layerwise_network import (
        build_layerwise_uniform_source_model,
    )
    from spd_decap_pi.layerwise_termination_adapter import (
        LayerwiseScenarioTerminationFactory,
    )

    source_model = build_layerwise_uniform_source_model(
        project,
        attachments,
        rail_id,
        template,
    )
    binding = LayerwiseScenarioTerminationFactory(
        scenario=scenario,
        project=project,
        attachments=attachments,
    )(source_model.substrate, template)
    if board_reuse is None:
        return source_model.with_termination_manifest(binding, required=True)
    bound_source, group_index = board_reuse.bind(source_model, binding)
    if board_audit_sink is not None:
        board_audit_sink[rail_id] = board_reuse.audit_for(
            bound_source, group_index
        )
    return bound_source


def _run_layerwise_single_frequency_diagnostic(
    scenario: ScenarioSpec,
    attachments: Mapping[str, bytes],
    *,
    rail_id: str,
    frequency_hz: float,
) -> dict[str, Any]:
    """Run one production layerwise reduction and expose its bounded diagnostics.

    This path intentionally does not invoke adaptive-frequency evaluation or
    PowerSI conversion.  ``assemble`` first exercises the same connectivity and
    required mounted-termination checks as Evaluation.  The immediately
    following private result lookup is a cache hit used only to expose the
    production reduction diagnostics that ``UniformC00Assembly`` does not carry.
    """

    frequency = float(frequency_hz)
    if not np.isfinite(frequency) or frequency <= 0.0:
        raise ValueError("layerwise diagnostic frequency must be finite and positive")

    total_started = perf_counter()
    build_started = perf_counter()
    project = build_evaluation_project(
        scenario,
        evaluation_rail_id=rail_id,
        solver_profile=LAYERWISE_ADMITTANCE_PROFILE.key,
    )
    template = compile_project_evaluation_template(project, rail_id)
    source_model = _build_bound_layerwise_source_model(
        scenario,
        project,
        attachments,
        rail_id,
        template,
    )
    build_elapsed = perf_counter() - build_started

    frequencies = np.asarray([frequency], dtype=np.float64)
    solve_started = perf_counter()
    assembly = source_model.assemble(frequencies)
    solve_elapsed = perf_counter() - solve_started
    if assembly.status != "ok" or assembly.effective_admittance_s is None:
        raise ValueError(
            "single-frequency layerwise assembly was blocked: "
            f"{assembly.reason or assembly.status}"
        )
    effective = np.asarray(assembly.effective_admittance_s, dtype=np.complex128)
    finite_result = bool(
        effective.size
        and np.all(np.isfinite(effective.real))
        and np.all(np.isfinite(effective.imag))
    )
    if not finite_result:
        raise ValueError("single-frequency layerwise assembly returned non-finite admittance")

    result = source_model.substrate._solve_all_ports(
        frequencies,
        selected_rail_id=rail_id,
        termination_manifest=source_model.termination_manifest,
    )
    diagnostics = result.diagnostics
    selected_value = complex(effective.reshape(-1)[0])
    total_elapsed = perf_counter() - total_started
    return {
        "status": "completed",
        "solver_profile_key": LAYERWISE_ADMITTANCE_PROFILE.key,
        "rail_id": rail_id,
        "frequency_hz": frequency,
        "requested_frequency_count": 1,
        "frequency_solves_executed": 1,
        "assembly": {
            "status": assembly.status,
            "finite_effective_admittance": finite_result,
            "effective_admittance_s": [selected_value.real, selected_value.imag],
        },
        "topology": {
            "physical_surface_count": int(diagnostics.physical_surface_count),
            "reduced_node_count": int(diagnostics.reduced_node_count),
            "active_reduced_node_count": int(
                diagnostics.active_reduced_node_count
            ),
            "pruned_portless_node_count": int(
                diagnostics.pruned_portless_node_count
            ),
            "structural_component_count": int(
                diagnostics.structural_component_count
            ),
            "active_structural_component_count": int(
                diagnostics.active_structural_component_count
            ),
            "finite_via_link_count": int(diagnostics.finite_via_link_count),
            "topology_only_link_count": int(
                diagnostics.topology_only_link_count
            ),
            "disabled_via_link_count": int(
                diagnostics.disabled_via_link_count
            ),
        },
        "linear_solve": {
            "maximum_port_rhs_batch_size": int(
                diagnostics.maximum_port_rhs_batch_size
            ),
            "maximum_factor_pivot_ratio": float(
                diagnostics.maximum_factor_pivot_ratio
            ),
            "maximum_relative_residual": float(
                diagnostics.maximum_relative_residual
            ),
            "maximum_termination_kron_relative_residual": float(
                diagnostics.maximum_termination_kron_relative_residual
            ),
            "solve_identity_sha256": diagnostics.solve_identity_sha256,
        },
        "terminations": {
            "manifest_sha256": diagnostics.termination_manifest_sha256,
            "active_cluster_count": int(
                diagnostics.active_termination_cluster_count
            ),
            "excluded_selected_rail_cluster_count": int(
                diagnostics.excluded_selected_rail_cluster_count
            ),
            "active_element_count": int(
                diagnostics.active_termination_element_count
            ),
        },
        "convergence": {
            "kind": "single_frequency_direct_linear_solve",
            "completed": True,
            "finite_result": finite_result,
            "accepted_by_solver_residual_gate": True,
            "adaptive_frequency_convergence_applicable": False,
            "maximum_relative_residual": float(
                diagnostics.maximum_relative_residual
            ),
            "maximum_termination_kron_relative_residual": float(
                diagnostics.maximum_termination_kron_relative_residual
            ),
        },
        "identity": {
            "source_model_evidence_sha256": source_model.evidence_sha256,
            "substrate_identity_sha256": (
                source_model.substrate.substrate_identity_sha256
            ),
            "termination_manifest_sha256": (
                None
                if source_model.termination_manifest is None
                else source_model.termination_manifest.manifest_sha256
            ),
        },
        "timing_s": {
            "project_template_and_source_build": build_elapsed,
            "single_frequency_assembly_and_solve": solve_elapsed,
            "total": total_elapsed,
        },
    }


def _terminal_complete_reuse_details(
    solved: _TerminalCompleteSolvedRail,
    *,
    status: str,
) -> dict[str, Any]:
    return {
        **dict(solved.board_audit),
        "status": status,
        "source_modal_max_index": solved.source_mode,
        "source_model_evidence_sha256": solved.source_model_evidence_sha256,
        "frequency_grid_sha256": solved.frequency_grid_sha256,
        "impedance_sha256": solved.impedance_sha256,
        "solver_provenance_sha256": solved.solver_provenance_sha256,
        "per_rail_port_projection_reused_exact": status
        == "reused_exact_mode_invariant",
        "per_rail_port_projection_recomputed": False,
        "powersi_metrics_recomputed": status == "reused_exact_mode_invariant",
        "layer_surface_global_y_diagnostics": {
            "solve_identity_sha256": solved.board_solve_identity_sha256,
            "maximum_factor_pivot_ratio": (
                solved.board_maximum_factor_pivot_ratio
            ),
            "maximum_relative_residual": solved.board_maximum_relative_residual,
            "maximum_termination_kron_relative_residual": (
                solved.board_maximum_termination_kron_relative_residual
            ),
        },
    }


def _capture_terminal_complete_solved_rail(
    rail_id: str,
    source_model: Any,
    outcome: Any,
    board_audit: Mapping[str, Any],
    *,
    source_mode: int,
    board_result_override: Any | None = None,
) -> _TerminalCompleteSolvedRail:
    provenance = dict(getattr(outcome, "solver_provenance", {}))
    source_provenance = getattr(source_model, "provenance", None)
    convergence = getattr(outcome, "convergence", None)
    if (
        getattr(outcome, "rail_id", rail_id) != rail_id
        or str(getattr(source_model, "rail_id", "")).casefold()
        != rail_id.casefold()
        or str(board_audit.get("rail_id", "")).casefold()
        != rail_id.casefold()
        or str(provenance.get("rail_id", "")).casefold()
        != rail_id.casefold()
        or str(provenance.get("selected_net", "")).casefold()
        != str(board_audit.get("selected_net", "")).casefold()
        or str(provenance.get("rail_port_manifest_sha256", "")).casefold()
        != str(board_audit.get("rail_port_manifest_sha256", "")).casefold()
        or getattr(outcome, "solver_profile_key", None)
        != LAYERWISE_ADMITTANCE_PROFILE.key
        or not isinstance(source_provenance, Mapping)
        or provenance.get("profile_key") != LAYERWISE_ADMITTANCE_PROFILE.key
        or convergence is None
        or convergence.modal_converged is not True
        or convergence.lower_mode_x != int(source_mode)
        or convergence.lower_mode_y != int(source_mode)
        or convergence.lower_mode_x != convergence.final_mode_x
        or convergence.lower_mode_y != convergence.final_mode_y
        or any(
            float(getattr(convergence, field)) != 0.0
            for field in (
                "modal_rms_delta_db",
                "modal_max_delta_db",
                "modal_peak_shift_percent",
            )
        )
        or provenance.get("modal_convergence_applicability") != "not_applicable"
        or provenance.get("modal_order_invariance")
        != "analytic_terminal_complete_external_device_port"
        or provenance.get("modal_convergence_solve_count") != 0
        or int(getattr(outcome.solve.diagnostics, "mode_count", -1)) != 1
    ):
        raise _TerminalCompleteReuseError(
            f"rail {rail_id!r} did not prove terminal-complete modal invariance"
        )
    source_evidence = str(getattr(source_model, "evidence_sha256", "")).casefold()
    required_equal_fields = (
        "source_sha256",
        "static_compiler_algorithm_sha256",
        "substrate_identity_sha256",
        "termination_manifest_sha256",
        "bound_substrate_identity_sha256",
        "layerwise_identity_sha256",
    )
    if not _valid_sha256(source_evidence) or any(
        str(provenance.get(field, "")).casefold()
        != str(source_provenance.get(field, "")).casefold()
        for field in required_equal_fields
    ):
        raise _TerminalCompleteReuseError(
            f"rail {rail_id!r} solver/source provenance does not match"
        )
    frequencies = getattr(outcome.solve, "frequencies_hz", None)
    impedance = getattr(outcome.solve, "impedance_ohm", None)
    substrate = source_model.substrate
    manifest = source_model.termination_manifest
    board_result = board_result_override
    if board_result is None:
        # This is an immediate exact-cache lookup after the convergence solve,
        # not a second physical factorization.  The returned all-port object is
        # retained strongly for later mode-invariance reuse even if the
        # substrate's bounded LRU subsequently evicts its grid.
        board_result = substrate._solve_all_ports(
            np.asarray(frequencies, dtype=np.float64),
            selected_rail_id=rail_id,
            termination_manifest=manifest,
        )
    if (
        not _is_frozen_dataclass_instance(board_result)
        or not isinstance(
            getattr(board_result, "effective_admittance_by_port", None),
            MappingProxyType,
        )
        or np.asarray(board_result.frequencies_hz).flags.writeable
        or any(
            np.asarray(value).flags.writeable
            for value in board_result.effective_admittance_by_port.values()
        )
    ):
        raise _TerminalCompleteReuseError(
            f"rail {rail_id!r} all-port frequency result is not immutable"
        )
    rail_key = rail_id.casefold()
    port = substrate.port_by_rail_key[rail_key]
    port_admittance = board_result.effective_admittance_by_port[port.port_id]
    projected_impedance = 1.0 / port_admittance
    frequency_identity = _array_identity_sha256(
        frequencies, dtype="<f8", label=f"{rail_id} frequency grid"
    )
    impedance_identity = _array_identity_sha256(
        impedance, dtype="<c16", label=f"{rail_id} impedance"
    )
    if (
        _array_identity_sha256(
            board_result.frequencies_hz,
            dtype="<f8",
            label=f"{rail_id} cached board frequency grid",
        )
        != frequency_identity
        or _array_identity_sha256(
            projected_impedance,
            dtype="<c16",
            label=f"{rail_id} cached board port projection",
        )
        != impedance_identity
    ):
        raise _TerminalCompleteReuseError(
            f"rail {rail_id!r} outcome is not the exact cached all-port projection"
        )
    board_diagnostics = board_result.diagnostics
    board_solve_identity = str(
        getattr(board_diagnostics, "solve_identity_sha256", "")
    ).casefold()
    board_diagnostic_values = (
        float(getattr(board_diagnostics, "maximum_factor_pivot_ratio", np.nan)),
        float(getattr(board_diagnostics, "maximum_relative_residual", np.nan)),
        float(
            getattr(
                board_diagnostics,
                "maximum_termination_kron_relative_residual",
                np.nan,
            )
        ),
    )
    if (
        not _is_frozen_dataclass_instance(board_diagnostics)
        or not _valid_sha256(board_solve_identity)
        or not all(np.isfinite(value) and value >= 0.0 for value in board_diagnostic_values)
    ):
        raise _TerminalCompleteReuseError(
            f"rail {rail_id!r} cached board solve diagnostics are not immutable/valid"
        )
    return _TerminalCompleteSolvedRail(
        rail_id=rail_id,
        source_model=source_model,
        outcome=outcome,
        board_frequency_result=board_result,
        board_audit=dict(board_audit),
        source_mode=int(source_mode),
        frequency_grid_sha256=frequency_identity,
        impedance_sha256=impedance_identity,
        source_model_evidence_sha256=source_evidence,
        solver_provenance_sha256=_json_identity_sha256(
            provenance, label=f"{rail_id} solver provenance"
        ),
        board_solve_identity_sha256=board_solve_identity,
        board_maximum_factor_pivot_ratio=board_diagnostic_values[0],
        board_maximum_relative_residual=board_diagnostic_values[1],
        board_maximum_termination_kron_relative_residual=(
            board_diagnostic_values[2]
        ),
    )


def _validate_terminal_complete_mode_reuse(
    solved: _TerminalCompleteSolvedRail,
    board_reuse: _TerminalCompleteBoardReuse,
) -> None:
    outcome = solved.outcome
    source_model = solved.source_model
    current_audit = board_reuse.audit_for(
        source_model, int(solved.board_audit["board_group_index"])
    )
    if current_audit != dict(solved.board_audit):
        raise _TerminalCompleteReuseError(
            f"rail {solved.rail_id!r} board identity changed after its source solve"
        )
    if (
        str(getattr(source_model, "evidence_sha256", "")).casefold()
        != solved.source_model_evidence_sha256
        or _array_identity_sha256(
            outcome.solve.frequencies_hz,
            dtype="<f8",
            label=f"{solved.rail_id} frequency grid",
        )
        != solved.frequency_grid_sha256
        or _array_identity_sha256(
            outcome.solve.impedance_ohm,
            dtype="<c16",
            label=f"{solved.rail_id} impedance",
        )
        != solved.impedance_sha256
        or _json_identity_sha256(
            dict(outcome.solver_provenance),
            label=f"{solved.rail_id} solver provenance",
        )
        != solved.solver_provenance_sha256
    ):
        raise _TerminalCompleteReuseError(
            f"rail {solved.rail_id!r} numerical/source identity changed before reuse"
        )
    # Re-run the full semantic gate as well as the byte identities so a caller
    # cannot mutate only the convergence/provenance claim and retain old hashes.
    verified = _capture_terminal_complete_solved_rail(
        solved.rail_id,
        source_model,
        outcome,
        current_audit,
        source_mode=solved.source_mode,
        board_result_override=solved.board_frequency_result,
    )
    if (
        verified.board_frequency_result is not solved.board_frequency_result
        or verified.board_solve_identity_sha256
        != solved.board_solve_identity_sha256
        or verified.board_maximum_factor_pivot_ratio
        != solved.board_maximum_factor_pivot_ratio
        or verified.board_maximum_relative_residual
        != solved.board_maximum_relative_residual
        or verified.board_maximum_termination_kron_relative_residual
        != solved.board_maximum_termination_kron_relative_residual
        or
        verified.frequency_grid_sha256 != solved.frequency_grid_sha256
        or verified.impedance_sha256 != solved.impedance_sha256
        or verified.solver_provenance_sha256 != solved.solver_provenance_sha256
    ):
        raise _TerminalCompleteReuseError(
            f"rail {solved.rail_id!r} failed the terminal-complete parity gate"
        )


def _completed_rail_report(
    outcome: Any,
    network: TouchstoneNetwork,
    converted: Any,
    *,
    rail: str,
    port: int,
    modal_index: int,
    modal_ceiling: int,
    runtime_s: float,
    terminal_reuse: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reference_impedance = open_circuit_zpp(converted, port)
    critical_metrics, evaluation_band_metrics = correlation_metric_bands(
        outcome.solve.frequencies_hz,
        outcome.solve.impedance_ohm,
        network.frequencies_hz,
        reference_impedance,
    )
    convergence = (
        asdict(outcome.convergence) if outcome.convergence is not None else None
    )
    if convergence is not None and terminal_reuse is not None:
        # Modal order is a report compatibility coordinate only for this
        # direct external-input solve.  Preserve every measured frequency
        # convergence value and rewrite only the analytically inapplicable
        # lower/final rectangular indices for the requested report mode.
        convergence.update(
            {
                "lower_mode_x": modal_index,
                "lower_mode_y": modal_index,
                "final_mode_x": modal_index,
                "final_mode_y": modal_index,
            }
        )
    report = {
        "group": GROUP_BY_RAIL[rail],
        "status": "completed",
        "runtime_s": float(runtime_s),
        "solver_version": outcome.solver_version,
        "solver_profile_key": outcome.solver_profile_key,
        "solver_provenance": dict(outcome.solver_provenance),
        "mode_max_index": modal_index,
        "mode_count": outcome.solve.diagnostics.mode_count,
        "convergence_policy": _convergence_policy_report(
            modal_start_index=modal_index,
            modal_ceiling_index=modal_ceiling,
        ),
        "convergence": convergence,
        "solver_diagnostics": {
            "semantics": (
                "external_input_passthrough"
                if terminal_reuse is not None
                else "modal_solver"
            ),
            "max_condition": float(
                np.max(outcome.solve.diagnostics.condition_numbers)
            ),
            "max_relative_residual": float(
                np.max(outcome.solve.diagnostics.relative_residuals)
            ),
        },
        "metrics": critical_metrics,
        "evaluation_band_metrics": evaluation_band_metrics,
    }
    if terminal_reuse is not None:
        report["terminal_complete_batch_reuse"] = dict(terminal_reuse)
        report["solver_diagnostics"]["layer_surface_global_y"] = dict(
            terminal_reuse["layer_surface_global_y_diagnostics"]
        )
        report["mode_count_semantics"] = (
            "one compatibility C00 column; rectangular modal order is not "
            "part of the terminal-complete physical solve"
        )
    return report


def _convergence_log_summary(convergence: Any | None) -> str:
    if convergence is None:
        return "convergence unavailable"
    return (
        f"frequency={convergence.frequency_converged}, "
        f"modal={convergence.modal_converged}, "
        f"final=m{convergence.final_mode_x}, "
        f"points={convergence.initial_frequency_points}->"
        f"{convergence.final_frequency_points}, "
        f"refinements={convergence.refinement_iterations}/"
        f"{convergence.max_refinement_iterations}, "
        f"delta_rms={convergence.frequency_rms_delta_db:.6g}/"
        f"{convergence.rms_tolerance_db:.6g}dB, "
        f"delta_max={convergence.frequency_max_delta_db:.6g}/"
        f"{convergence.max_tolerance_db:.6g}dB, "
        f"peak_shift={convergence.frequency_peak_shift_percent:.6g}/"
        f"{convergence.peak_shift_tolerance_percent:.6g}%, "
        f"budget_exhausted={convergence.frequency_budget_exhausted}"
    )


def _run_one(
    scenario: Any, network: TouchstoneNetwork, converted: Any, rail_ports: Mapping[str, int],
    *,
    modal_index: int,
    modal_ceiling_index: int | None = None,
    attachments: Mapping[str, bytes] | None = None,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    project_transform: Callable[[Any], Any] | None = None,
    terminal_board_reuse: _TerminalCompleteBoardReuse | None = None,
    terminal_solved_rails: dict[str, _TerminalCompleteSolvedRail] | None = None,
) -> dict[str, Any]:
    profile = resolve_solver_profile(solver_profile)
    if (
        terminal_board_reuse is not None
        and profile != LAYERWISE_ADMITTANCE_PROFILE
    ):
        raise _TerminalCompleteReuseError(
            "terminal board reuse cannot be applied to a modal/research profile"
        )
    modal_ceiling = modal_index if modal_ceiling_index is None else modal_ceiling_index
    if modal_ceiling < modal_index:
        raise ValueError("modal convergence ceiling cannot be below its start")
    rails: dict[str, Any] = {}
    started = perf_counter()
    total_rails = len(rail_ports)
    for ordinal, (rail, port) in enumerate(rail_ports.items(), start=1):
        rail_started = perf_counter()
        print(
            f"[mode {modal_index}] rail {ordinal}/{total_rails} {rail}: start",
            flush=True,
        )
        try:
            project = build_evaluation_project(
                scenario,
                evaluation_rail_id=rail,
                solver_profile=profile.key,
            )
            if project_transform is not None:
                project = project_transform(project)
            request_options: dict[str, Any] = {
                "max_mode_x": modal_index,
                "max_mode_y": modal_index,
                "worker_count": 1,
                "solver_profile_key": profile.key,
            }
            if profile == LAYERWISE_ADMITTANCE_PROFILE:
                template = compile_project_evaluation_template(project, rail)
                request_options["template"] = template
                board_audit: dict[str, Mapping[str, Any]] = {}
                if terminal_board_reuse is None:
                    request_options["uniform_c00_source"] = (
                        _build_bound_layerwise_source_model(
                            scenario,
                            project,
                            attachments or {},
                            rail,
                            template,
                        )
                    )
                else:
                    request_options["uniform_c00_source"] = _build_bound_layerwise_source_model(
                        scenario,
                        project,
                        attachments or {},
                        rail,
                        template,
                        board_reuse=terminal_board_reuse,
                        board_audit_sink=board_audit,
                    )
            elif profile == RESEARCH_UNIFORM_ADMITTANCE_PROFILE:
                from spd_decap_pi._core.solver.research_uniform_profile import (
                    build_uniform_c00_source_model,
                )

                template = compile_project_evaluation_template(project, rail)
                request_options["template"] = template
                request_options["uniform_c00_source"] = build_uniform_c00_source_model(
                    project, attachments or {}, rail, template
                )
            outcome = evaluate_project_rail_converged(
                project, rail,
                request_options=request_options,
                max_mode_x=modal_ceiling, max_mode_y=modal_ceiling,
                max_refinement_iterations=DEFAULT_MAX_REFINEMENT_ITERATIONS,
                max_new_frequency_points=DEFAULT_MAX_NEW_FREQUENCY_POINTS,
            )
        except (
            ScenarioEvaluationBuildError,
            EvaluationError,
            ModalSolverError,
            ValueError,
        ) as exc:
            # A comparison report must retain every predeclared rail.  A
            # source/modelability blocker is explicit evidence, not a reason to
            # abort the VQPS controls or silently omit the affected loaded rail.
            rails[rail] = {
                "group": GROUP_BY_RAIL[rail],
                "status": "blocked",
                "runtime_s": perf_counter() - rail_started,
                "error_type": type(exc).__name__,
                "reason": str(exc),
            }
            print(
                f"[mode {modal_index}] rail {ordinal}/{total_rails} {rail}: "
                f"blocked after {perf_counter() - rail_started:.1f}s: {exc}",
                flush=True,
            )
            continue
        reuse_details = None
        if terminal_board_reuse is not None:
            source_model = request_options.get("uniform_c00_source")
            audit = board_audit.get(rail)
            if source_model is None or audit is None:
                raise _TerminalCompleteReuseError(
                    f"rail {rail!r} completed without a board reuse identity"
                )
            solved = _capture_terminal_complete_solved_rail(
                rail,
                source_model,
                outcome,
                audit,
                source_mode=modal_index,
            )
            if terminal_solved_rails is not None:
                terminal_solved_rails[rail] = solved
            reuse_details = _terminal_complete_reuse_details(
                solved, status="source_solve"
            )
        rails[rail] = _completed_rail_report(
            outcome,
            network,
            converted,
            rail=rail,
            port=port,
            modal_index=modal_index,
            modal_ceiling=modal_ceiling,
            runtime_s=perf_counter() - rail_started,
            terminal_reuse=reuse_details,
        )
        convergence_summary = _convergence_log_summary(outcome.convergence)
        print(
            f"[mode {modal_index}] rail {ordinal}/{total_rails} {rail}: "
            f"completed in {perf_counter() - rail_started:.1f}s; "
            f"{convergence_summary}",
            flush=True,
        )
    return {
        "solver_profile": profile.key,
        "modal_max_index": modal_index,
        "modal_convergence_ceiling_index": modal_ceiling,
        "runtime_s": perf_counter() - started,
        "rails": rails,
    }


def _run_reused_terminal_complete_mode(
    primary_run: Mapping[str, Any],
    solved_rails: Mapping[str, _TerminalCompleteSolvedRail],
    board_reuse: _TerminalCompleteBoardReuse,
    network: TouchstoneNetwork,
    converted: Any,
    rail_ports: Mapping[str, int],
    *,
    modal_index: int,
    modal_ceiling_index: int | None,
) -> dict[str, Any]:
    modal_ceiling = (
        modal_index if modal_ceiling_index is None else modal_ceiling_index
    )
    if modal_ceiling < modal_index:
        raise _TerminalCompleteReuseError(
            "modal convergence ceiling cannot be below a reused mode"
        )
    started = perf_counter()
    rails: dict[str, Any] = {}
    primary_rails = primary_run.get("rails")
    if not isinstance(primary_rails, Mapping):
        raise _TerminalCompleteReuseError("source mode rail results are missing")
    total_rails = len(rail_ports)
    for ordinal, (rail, port) in enumerate(rail_ports.items(), start=1):
        rail_started = perf_counter()
        primary = primary_rails.get(rail)
        if not isinstance(primary, Mapping):
            raise _TerminalCompleteReuseError(
                f"source mode omitted rail {rail!r}"
            )
        if primary.get("status") == "blocked":
            blocked = dict(primary)
            blocked["runtime_s"] = perf_counter() - rail_started
            blocked["terminal_complete_batch_reuse"] = {
                "guard_version": TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
                "status": "source_mode_blocker_reused",
                "source_modal_max_index": primary_run.get("modal_max_index"),
                "numerical_result_reused": False,
            }
            rails[rail] = blocked
            print(
                f"[mode {modal_index}] rail {ordinal}/{total_rails} {rail}: "
                "source-mode blocker retained without a numerical copy",
                flush=True,
            )
            continue
        if primary.get("status") != "completed" or rail not in solved_rails:
            raise _TerminalCompleteReuseError(
                f"source mode has no reusable completed outcome for rail {rail!r}"
            )
        solved = solved_rails[rail]
        _validate_terminal_complete_mode_reuse(solved, board_reuse)
        details = _terminal_complete_reuse_details(
            solved, status="reused_exact_mode_invariant"
        )
        details.update(
            {
                "requested_modal_max_index": modal_index,
                "numerical_result_reused": True,
                "frequency_grid_identity_equal": True,
                "impedance_identity_equal": True,
                "solver_source_termination_identity_equal": True,
            }
        )
        rails[rail] = _completed_rail_report(
            solved.outcome,
            network,
            converted,
            rail=rail,
            port=port,
            modal_index=modal_index,
            modal_ceiling=modal_ceiling,
            runtime_s=perf_counter() - rail_started,
            terminal_reuse=details,
        )
        print(
            f"[mode {modal_index}] rail {ordinal}/{total_rails} {rail}: "
            f"exact terminal-complete result reused from mode {solved.source_mode}; "
            "port metrics recomputed",
            flush=True,
        )
    return {
        "solver_profile": LAYERWISE_ADMITTANCE_PROFILE.key,
        "modal_max_index": modal_index,
        "modal_convergence_ceiling_index": modal_ceiling,
        "runtime_s": perf_counter() - started,
        "terminal_complete_batch_reuse": {
            "guard_version": TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
            "status": "reused_exact_mode_invariant",
            "source_modal_max_index": primary_run.get("modal_max_index"),
            "board_group_count": board_reuse.group_count,
        },
        "rails": rails,
    }


def _validate_terminal_complete_run_parity(
    runs: Mapping[str, Any],
    rail_ports: Mapping[str, int],
    *,
    source_mode: int,
) -> dict[str, Any]:
    source_run = runs.get(str(source_mode))
    if not isinstance(source_run, Mapping):
        raise _TerminalCompleteReuseError("terminal-complete source run is missing")
    source_rails = source_run.get("rails")
    if not isinstance(source_rails, Mapping):
        raise _TerminalCompleteReuseError("terminal-complete source rails are missing")
    source_run_details = source_run.get("terminal_complete_batch_reuse")
    if (
        not isinstance(source_run_details, Mapping)
        or source_run.get("modal_max_index") != source_mode
        or source_run_details.get("guard_version")
        != TERMINAL_COMPLETE_BATCH_REUSE_VERSION
        or source_run_details.get("status") != "source_solve"
        or source_run_details.get("source_modal_max_index") != source_mode
    ):
        raise _TerminalCompleteReuseError(
            "terminal-complete source run is not marked as an exact source solve"
        )
    compared = 0
    blockers = 0
    reused = 0
    for mode_key, run in runs.items():
        if not isinstance(run, Mapping) or not isinstance(run.get("rails"), Mapping):
            raise _TerminalCompleteReuseError(f"mode {mode_key} run is malformed")
        try:
            mode = int(mode_key)
        except (TypeError, ValueError) as exc:
            raise _TerminalCompleteReuseError(
                f"terminal-complete mode key {mode_key!r} is invalid"
            ) from exc
        run_details = run.get("terminal_complete_batch_reuse")
        allowed_run_statuses = (
            {"source_solve"}
            if mode == source_mode
            else {
                "reused_exact_mode_invariant",
                "identity_mismatch_recomputed",
            }
        )
        if (
            not isinstance(run_details, Mapping)
            or run.get("modal_max_index") != mode
            or run_details.get("guard_version")
            != TERMINAL_COMPLETE_BATCH_REUSE_VERSION
            or run_details.get("status") not in allowed_run_statuses
        ):
            raise _TerminalCompleteReuseError(
                f"mode {mode} has an invalid terminal-complete run status"
            )
        run_status = str(run_details["status"])
        if (
            run_status in {"source_solve", "reused_exact_mode_invariant"}
            and run_details.get("source_modal_max_index") != source_mode
        ):
            raise _TerminalCompleteReuseError(
                f"mode {mode} has an invalid source-mode reuse identity"
            )
        for rail in rail_ports:
            source = source_rails.get(rail)
            candidate = run["rails"].get(rail)
            if not isinstance(source, Mapping) or not isinstance(candidate, Mapping):
                raise _TerminalCompleteReuseError(
                    f"mode {mode} omitted terminal-complete rail {rail!r}"
                )
            if source.get("status") == "blocked":
                blockers += mode != source_mode
                if (
                    candidate.get("status") != "blocked"
                    or candidate.get("error_type") != source.get("error_type")
                    or candidate.get("reason") != source.get("reason")
                ):
                    raise _TerminalCompleteReuseError(
                        f"mode {mode} changed source blocker for rail {rail!r}"
                    )
                blocker_details = candidate.get("terminal_complete_batch_reuse")
                if run_status == "reused_exact_mode_invariant" and (
                    not isinstance(blocker_details, Mapping)
                    or blocker_details.get("guard_version")
                    != TERMINAL_COMPLETE_BATCH_REUSE_VERSION
                    or blocker_details.get("status")
                    != "source_mode_blocker_reused"
                    or blocker_details.get("numerical_result_reused") is not False
                ):
                    raise _TerminalCompleteReuseError(
                        f"mode {mode} rail {rail!r} has invalid retained-blocker evidence"
                    )
                continue
            if source.get("status") != "completed":
                raise _TerminalCompleteReuseError(
                    f"source mode rail {rail!r} has an invalid status"
                )
            if candidate.get("status") != "completed":
                raise _TerminalCompleteReuseError(
                    f"mode {mode} did not complete rail {rail!r}"
                )
            details = candidate.get("terminal_complete_batch_reuse")
            if not isinstance(details, Mapping):
                raise _TerminalCompleteReuseError(
                    f"mode {mode} rail {rail!r} lacks terminal reuse evidence"
                )
            expected_rail_status = (
                "reused_exact_mode_invariant"
                if run_status == "reused_exact_mode_invariant"
                else "source_solve"
            )
            if (
                details.get("guard_version")
                != TERMINAL_COMPLETE_BATCH_REUSE_VERSION
                or details.get("status") != expected_rail_status
            ):
                raise _TerminalCompleteReuseError(
                    f"mode {mode} rail {rail!r} has an invalid terminal reuse status"
                )
            exact_object_fields = (
                "exact_binding_object",
                "exact_network_object",
                "exact_termination_manifest_object",
                "exact_canonical_substrate_object",
            )
            board_diagnostics = details.get("layer_surface_global_y_diagnostics")
            solver_diagnostics = candidate.get("solver_diagnostics")
            if (
                str(details.get("rail_id", "")).casefold() != rail.casefold()
                or not str(details.get("port_id", ""))
                or not str(details.get("port_positive_node_id", ""))
                or not str(details.get("port_negative_node_id", ""))
                or any(details.get(field) is not True for field in exact_object_fields)
                or not isinstance(board_diagnostics, Mapping)
                or not _valid_sha256(board_diagnostics.get("solve_identity_sha256"))
                or not isinstance(solver_diagnostics, Mapping)
                or solver_diagnostics.get("layer_surface_global_y")
                != board_diagnostics
            ):
                raise _TerminalCompleteReuseError(
                    f"mode {mode} rail {rail!r} has invalid board/port evidence"
                )
            convergence = candidate.get("convergence")
            if (
                not isinstance(convergence, Mapping)
                or convergence.get("lower_mode_x") != mode
                or convergence.get("lower_mode_y") != mode
                or convergence.get("final_mode_x") != mode
                or convergence.get("final_mode_y") != mode
                or convergence.get("modal_rms_delta_db") != 0.0
                or convergence.get("modal_max_delta_db") != 0.0
                or convergence.get("modal_peak_shift_percent") != 0.0
            ):
                raise _TerminalCompleteReuseError(
                    f"mode {mode} rail {rail!r} modal-invariance report differs"
                )
            if run_status == "identity_mismatch_recomputed":
                if (
                    details.get("per_rail_port_projection_reused_exact") is not False
                    or details.get("powersi_metrics_recomputed") is not False
                    or details.get("per_rail_port_projection_recomputed") is not False
                ):
                    raise _TerminalCompleteReuseError(
                        f"mode {mode} rail {rail!r} falsely claims numerical reuse"
                    )
                continue
            if run_status == "reused_exact_mode_invariant":
                required_true_reuse_fields = (
                    "numerical_result_reused",
                    "frequency_grid_identity_equal",
                    "impedance_identity_equal",
                    "solver_source_termination_identity_equal",
                    "per_rail_port_projection_reused_exact",
                    "powersi_metrics_recomputed",
                )
                if (
                    any(
                        details.get(field) is not True
                        for field in required_true_reuse_fields
                    )
                    or details.get("per_rail_port_projection_recomputed") is not False
                    or details.get("requested_modal_max_index") != mode
                ):
                    raise _TerminalCompleteReuseError(
                        f"mode {mode} rail {rail!r} has invalid exact-reuse evidence"
                    )
                reused += 1
                source_details = source.get("terminal_complete_batch_reuse")
                if not isinstance(source_details, Mapping):
                    raise _TerminalCompleteReuseError(
                        f"source mode rail {rail!r} lacks reuse evidence"
                    )
                exact_evidence_fields = (
                    "guard_version",
                    "board_group_index",
                    "solver_profile_key",
                    "solver_static_identity_sha256",
                    "source_sha256",
                    "substrate_identity_sha256",
                    "scenario_identity_sha256",
                    "termination_manifest_sha256",
                    "rail_port_manifest_sha256",
                    "port_selector_identity_sha256",
                    "source_model_evidence_sha256",
                    "frequency_grid_sha256",
                    "impedance_sha256",
                    "solver_provenance_sha256",
                    "source_modal_max_index",
                    "rail_id",
                    "selected_net",
                    "reference_net",
                    "port_id",
                    "port_positive_node_id",
                    "port_negative_node_id",
                    "exact_binding_object",
                    "exact_network_object",
                    "exact_termination_manifest_object",
                    "exact_canonical_substrate_object",
                )
                if any(
                    details.get(field) != source_details.get(field)
                    for field in exact_evidence_fields
                ):
                    raise _TerminalCompleteReuseError(
                        f"mode {mode} rail {rail!r} reuse identity differs"
                    )
                sha_fields = (
                    "solver_static_identity_sha256",
                    "source_sha256",
                    "substrate_identity_sha256",
                    "scenario_identity_sha256",
                    "termination_manifest_sha256",
                    "rail_port_manifest_sha256",
                    "port_selector_identity_sha256",
                    "source_model_evidence_sha256",
                    "frequency_grid_sha256",
                    "impedance_sha256",
                    "solver_provenance_sha256",
                )
                if any(not _valid_sha256(details.get(field)) for field in sha_fields):
                    raise _TerminalCompleteReuseError(
                        f"mode {mode} rail {rail!r} reuse SHA-256 evidence is invalid"
                    )
                source_board_diagnostics = source_details.get(
                    "layer_surface_global_y_diagnostics"
                )
                candidate_board_diagnostics = details.get(
                    "layer_surface_global_y_diagnostics"
                )
                if (
                    not isinstance(source_board_diagnostics, Mapping)
                    or candidate_board_diagnostics != source_board_diagnostics
                    or not _valid_sha256(
                        source_board_diagnostics.get("solve_identity_sha256")
                    )
                    or
                    candidate.get("solver_provenance")
                    != source.get("solver_provenance")
                    or candidate.get("metrics") != source.get("metrics")
                    or candidate.get("evaluation_band_metrics")
                    != source.get("evaluation_band_metrics")
                    or candidate.get("solver_diagnostics")
                    != source.get("solver_diagnostics")
                    or candidate.get("mode_count") != 1
                    or source.get("mode_count") != 1
                ):
                    raise _TerminalCompleteReuseError(
                        f"mode {mode} rail {rail!r} numerical/report parity differs"
                    )
                compared += 1
            elif (
                details.get("per_rail_port_projection_reused_exact") is not False
                or details.get("per_rail_port_projection_recomputed") is not False
                or details.get("powersi_metrics_recomputed") is not False
            ):
                raise _TerminalCompleteReuseError(
                    f"mode {mode} rail {rail!r} source solve has invalid reuse evidence"
                )
    return {
        "status": "passed",
        "source_modal_max_index": source_mode,
        "exact_reused_completed_rail_count": reused,
        "exact_parity_comparison_count": compared,
        "retained_blocker_count": blockers,
    }


def _run_candidate_modes(
    scenario: Any,
    network: TouchstoneNetwork,
    converted: Any,
    rail_ports: Mapping[str, int],
    *,
    modes: tuple[int, ...],
    modal_ceiling_index: int | None = None,
    legacy_via_only: bool,
    attachments: Mapping[str, bytes] | None = None,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    require_terminal_complete_reuse: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute candidate modes unless the explicit ablation-only switch skips them."""

    if legacy_via_only:
        return {}, {
            "status": "skipped",
            "reason": (
                "--legacy-via-only requested; the already-rejected candidate solve "
                "was intentionally not repeated"
            ),
            "requested_modal_max_indices": list(modes),
            "completed_modal_max_indices": [],
        }
    profile = resolve_solver_profile(solver_profile)
    if profile == LAYERWISE_ADMITTANCE_PROFILE and modes:
        board_reuse = _TerminalCompleteBoardReuse(profile.key)
        solved_rails: dict[str, _TerminalCompleteSolvedRail] = {}
        source_mode = modes[0]
        source_run = _run_one(
            scenario,
            network,
            converted,
            rail_ports,
            modal_index=source_mode,
            modal_ceiling_index=modal_ceiling_index,
            attachments=attachments,
            solver_profile=profile.key,
            terminal_board_reuse=board_reuse,
            terminal_solved_rails=solved_rails,
        )
        source_run["terminal_complete_batch_reuse"] = {
            "guard_version": TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
            "status": "source_solve",
            "source_modal_max_index": source_mode,
            "board_group_count": board_reuse.group_count,
            "canonical_substrate_reused_rail_binding_count": (
                board_reuse.reused_rail_binding_count
            ),
        }
        runs = {str(source_mode): source_run}
        fallback_modes: dict[str, str] = {}
        for mode in modes[1:]:
            try:
                runs[str(mode)] = _run_reused_terminal_complete_mode(
                    source_run,
                    solved_rails,
                    board_reuse,
                    network,
                    converted,
                    rail_ports,
                    modal_index=mode,
                    modal_ceiling_index=modal_ceiling_index,
                )
            except _TerminalCompleteReuseError as exc:
                if require_terminal_complete_reuse:
                    raise
                # Never turn identity uncertainty into a copied result.  A
                # complete independent mode evaluation is the safe fallback.
                fallback_modes[str(mode)] = str(exc)
                fallback_reuse = _TerminalCompleteBoardReuse(profile.key)
                fallback_run = _run_one(
                    scenario,
                    network,
                    converted,
                    rail_ports,
                    modal_index=mode,
                    modal_ceiling_index=modal_ceiling_index,
                    attachments=attachments,
                    solver_profile=profile.key,
                    terminal_board_reuse=fallback_reuse,
                )
                fallback_run["terminal_complete_batch_reuse"] = {
                    "guard_version": TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
                    "status": "identity_mismatch_recomputed",
                    "reason": str(exc),
                    "board_group_count": fallback_reuse.group_count,
                }
                runs[str(mode)] = fallback_run
        source_run_count = len(rail_ports) * (1 + len(fallback_modes))
        requested_count = len(rail_ports) * len(modes)
        reuse_execution = {
            "guard_version": TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
            "status": (
                "partial_recomputed_identity_mismatch"
                if fallback_modes
                else "exact_terminal_complete_reuse"
            ),
            "requested_rail_mode_evaluation_count": requested_count,
            "executed_rail_evaluation_count": source_run_count,
            "reused_rail_mode_result_count": requested_count - source_run_count,
            "evaluation_call_reduction_count": requested_count - source_run_count,
            "board_group_count": board_reuse.group_count,
            "canonical_substrate_reused_rail_binding_count": (
                board_reuse.reused_rail_binding_count
            ),
            "physical_board_solve_scope": (
                "one exact all-port result per immutable board group and "
                "byte-identical float64 frequency grid"
            ),
            "fallback_modes": fallback_modes,
        }
        reuse_execution["parity_validation"] = (
            _validate_terminal_complete_run_parity(
                runs, rail_ports, source_mode=source_mode
            )
        )
    else:
        runs = {
            str(mode): _run_one(
                scenario,
                network,
                converted,
                rail_ports,
                modal_index=mode,
                modal_ceiling_index=modal_ceiling_index,
                attachments=attachments,
                solver_profile=profile.key,
            )
            for mode in modes
        }
        reuse_execution = {
            "guard_version": TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
            "status": "not_applicable",
            "reason": "solver profile is not terminal-complete",
        }
    blocked_by_mode = {
        mode: [
            rail
            for rail, result in run.get("rails", {}).items()
            if result.get("status") == "blocked"
        ]
        for mode, run in runs.items()
    }
    blocked_by_mode = {
        mode: rails for mode, rails in blocked_by_mode.items() if rails
    }
    return runs, {
        "status": "partial" if blocked_by_mode else "completed",
        "reason": (
            "Source/modelability blockers are retained per rail; clear rails "
            "completed and no selected rail was silently omitted."
            if blocked_by_mode
            else None
        ),
        "requested_modal_max_indices": list(modes),
        "completed_modal_max_indices": list(modes),
        "blocked_rails_by_modal_max_index": blocked_by_mode,
        "terminal_complete_batch_reuse": reuse_execution,
    }


def _sheet_loss_evidence(project: Any) -> dict[str, Any]:
    """Record the source-based finite-slab difference; there is no safe solve toggle."""

    frequencies = np.asarray(ANCHORS_HZ, dtype=np.float64)
    rows: list[dict[str, Any]] = []
    for layer in project.stackup_layers:
        if not layer.is_conductor or layer.thickness_um is None or layer.conductivity_s_m is None:
            continue
        dc = 1.0 / (float(layer.conductivity_s_m) * float(layer.thickness_um) * 1.0e-6)
        finite = copper_slab_surface_impedance_per_square(
            frequencies, thickness_m=float(layer.thickness_um) * 1.0e-6,
            conductivity_s_per_m=float(layer.conductivity_s_m),
        )
        rows.append({
            "layer": layer.name, "dc_ohm_per_square": dc,
            "finite_slab_ohm_per_square": [[float(value.real), float(value.imag)] for value in finite],
        })
    return {
        "solver_ablation_executed": False,
        "reason": "the production request has no supported DC-sheet-loss override; monkey-patching would not be a rigorous read-only ablation",
        "frequencies_hz": list(ANCHORS_HZ), "source_finite_slab_evidence": rows,
    }


def _combined_convergence_gate_failures(
    candidate_runs: Mapping[str, Any],
    selected_rails: tuple[str, ...],
    *,
    requested_modes: tuple[int, ...] | None = None,
    require_full_rail_manifest: bool = False,
    require_terminal_complete_invariance: bool = False,
) -> tuple[str, ...]:
    """Return deterministic fail-closed reasons for a release-gated run."""

    failures: list[str] = []
    if require_full_rail_manifest and (
        len(selected_rails) != len(SELECTED_RAILS)
        or set(selected_rails) != set(SELECTED_RAILS)
    ):
        failures.append(
            "release rail manifest is not exactly the 16 predeclared rails"
        )
    if not candidate_runs:
        failures.append("candidate runs are absent")
        return tuple(failures)
    if requested_modes is not None:
        expected_modes = {str(mode) for mode in requested_modes}
        actual_modes = {str(mode) for mode in candidate_runs}
        for mode in sorted(expected_modes - actual_modes):
            failures.append(f"requested mode {mode}: candidate run is absent")
        for mode in sorted(actual_modes - expected_modes):
            failures.append(f"unexpected candidate mode {mode} is present")
    for mode, run in candidate_runs.items():
        rails = run.get("rails") if isinstance(run, Mapping) else None
        if not isinstance(rails, Mapping):
            failures.append(f"mode {mode}: rail results are absent")
            continue
        for rail in selected_rails:
            outcome = rails.get(rail)
            prefix = f"mode {mode} rail {rail}"
            if not isinstance(outcome, Mapping):
                failures.append(f"{prefix}: result is absent")
                continue
            if outcome.get("status") != "completed":
                failures.append(
                    f"{prefix}: status={outcome.get('status', 'missing')!r}"
                )
                continue
            convergence = outcome.get("convergence")
            if not isinstance(convergence, Mapping):
                failures.append(f"{prefix}: convergence report is absent")
                continue
            if convergence.get("frequency_converged") is not True:
                failures.append(f"{prefix}: frequency convergence failed")
            if convergence.get("frequency_budget_exhausted") is not False:
                failures.append(f"{prefix}: frequency budget was exhausted or unreported")
            if convergence.get("modal_converged") is not True:
                failures.append(f"{prefix}: modal convergence failed")
            if convergence.get("converged") is not True:
                failures.append(f"{prefix}: combined convergence failed")
            if require_terminal_complete_invariance:
                if convergence.get("policy_version") != CONVERGENCE_POLICY_VERSION:
                    failures.append(f"{prefix}: convergence policy version mismatch")
                if (
                    convergence.get("lower_mode_x")
                    != convergence.get("final_mode_x")
                    or convergence.get("lower_mode_y")
                    != convergence.get("final_mode_y")
                ):
                    failures.append(
                        f"{prefix}: terminal-complete modal indices differ"
                    )
                for field in (
                    "modal_rms_delta_db",
                    "modal_max_delta_db",
                    "modal_peak_shift_percent",
                ):
                    if convergence.get(field) != 0.0:
                        failures.append(
                            f"{prefix}: terminal-complete {field} is not zero"
                        )
                provenance = outcome.get("solver_provenance")
                expected_provenance = {
                    "source_only": True,
                    "powersi_used_for_parameters": False,
                    "termination_manifest_required": True,
                    "modal_convergence_applicability": "not_applicable",
                    "modal_order_invariance": (
                        "analytic_terminal_complete_external_device_port"
                    ),
                    "modal_convergence_solve_count": 0,
                    "frequency_convergence_pass_count": 1,
                }
                if not isinstance(provenance, Mapping):
                    failures.append(f"{prefix}: solver provenance is absent")
                else:
                    for field, expected in expected_provenance.items():
                        if provenance.get(field) != expected:
                            failures.append(
                                f"{prefix}: solver_provenance.{field} mismatch"
                            )
    return tuple(failures)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spd = args.spd.resolve()
    touchstone = args.touchstone.resolve() if args.touchstone is not None else None
    out_dir = args.out_dir.resolve()
    if not spd.is_file():
        raise ValueError("--spd must name an existing file")
    reuse_candidate = args.reuse_candidate.resolve() if args.reuse_candidate is not None else None
    reuse_import_report = (
        args.reuse_candidate_import_report.resolve()
        if args.reuse_candidate_import_report is not None
        else None
    )
    if reuse_candidate is None:
        if out_dir.exists():
            raise ValueError(f"--out-dir must be a new directory unless --reuse-candidate is supplied: {out_dir}")
        out_dir.mkdir(parents=True)
        candidate_path = out_dir / f"{spd.stem}_candidate.spdpi"
    else:
        if not reuse_candidate.is_file():
            raise ValueError("--reuse-candidate must name an existing .spdpi bundle")
        if not out_dir.exists():
            out_dir.mkdir(parents=True)
        candidate_path = reuse_candidate
    report_name = (
        "import_save_validation_report.json"
        if args.import_save_only
        else "layerwise_diagnostic_report.json"
        if args.layerwise_diagnostic_frequency_hz is not None
        else "correlation_report.json"
    )
    report_path = out_dir / report_name
    if report_path.exists():
        raise ValueError(f"refusing to overwrite an existing report: {report_path}")

    import_started = perf_counter()
    if reuse_candidate is None:
        def import_progress(value: int, message: str) -> None:
            if not args.import_save_only:
                return
            elapsed = perf_counter() - import_started
            print(
                f"IMPORT_PROGRESS {int(value):03d}% {elapsed:10.1f}s {message}",
                flush=True,
            )

        imported = import_spd_scenario(
            spd,
            progress=import_progress if args.import_save_only else None,
        )
        if args.import_save_only:
            print(
                f"IMPORT_PROGRESS 099% {perf_counter() - import_started:10.1f}s "
                "Saving atomic compiled-only candidate bundle",
                flush=True,
            )
        save_scenario(imported.scenario, candidate_path, attachments=imported.attachments)
        if args.import_save_only:
            print(
                f"IMPORT_PROGRESS 100% {perf_counter() - import_started:10.1f}s "
                "Reloading and validating saved candidate bundle",
                flush=True,
            )
        bundle = load_scenario_bundle(candidate_path)
        import_report = {
            "mode": "fresh_import", "runtime_s": perf_counter() - import_started,
            "stage_timings_s": asdict(imported.timings),
        }
    else:
        bundle, reuse_report = _load_reusable_candidate(candidate_path)
        import_report = {**reuse_report, "runtime_s": perf_counter() - import_started}
    source = _source_identity_report(spd, bundle.scenario)
    source_state_validation = _validate_pristine_reusable_candidate(bundle.scenario)
    candidate_bundle_report = {
        "basename": candidate_path.name,
        "sha256": _hash(candidate_path),
        "size_bytes": candidate_path.stat().st_size,
        "reuse_requested": reuse_candidate is not None,
    }
    compiled_topology_asset = _compiled_topology_asset_identity(
        bundle.scenario,
        bundle.attachments,
        required=bool(
            args.require_all_converged
            and args.solver_profile == LAYERWISE_ADMITTANCE_PROFILE.key
        ),
    )
    if reuse_import_report is not None:
        import_report_binding = _validate_fresh_import_report_binding(
            reuse_import_report,
            source=source,
            candidate_bundle=candidate_bundle_report,
            compiled_topology_asset=compiled_topology_asset,
        )
    elif reuse_candidate is not None:
        import_report_binding = {
            "status": "not_required_checkpoint_or_diagnostic",
            "fresh_import_mode": None,
        }
    else:
        import_report_binding = {
            "status": "validated_same_process_fresh_import",
            "fresh_import_mode": True,
        }

    if args.import_save_only:
        raw_spatial_contact_asset = _raw_spatial_contact_asset_identity(
            bundle.scenario,
            bundle.attachments,
            compiled_topology_asset=compiled_topology_asset,
        )
        report = {
            "schema_version": IMPORT_SAVE_REPORT_SCHEMA_VERSION,
            "report_version": 1,
            "app_version": APP_VERSION,
            "mode": "import_save_only",
            "status": "passed",
            "source": source,
            "candidate_bundle": {
                **candidate_bundle_report,
            },
            "import": import_report,
            "source_state_validation": source_state_validation,
            "compiled_topology_asset": compiled_topology_asset,
            "raw_spatial_contact_asset": raw_spatial_contact_asset,
            "candidate_import_report_binding": import_report_binding,
            "atomic_save_load_validation": {
                "save_api": "save_scenario",
                "save_commit_protocol": "temporary archive plus atomic os.replace",
                "reload_api": "load_scenario_bundle",
                "archive_manifest_and_member_hashes_validated": True,
                "scenario_schema_validated_after_reload": True,
                "source_identity_validated_after_reload": True,
                "attachment_hashes_validated_after_reload": True,
                "raw_spatial_full_loader_status": (
                    raw_spatial_contact_asset["full_loader_status"]
                ),
                "loaded_attachment_count": len(bundle.attachments),
            },
            "frequency_solves_executed": 0,
            "touchstone_read": False,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(candidate_path)
        print(report_path)
        return 0

    if args.layerwise_diagnostic_frequency_hz is not None:
        diagnostic_rail = (
            args.rail[0] if args.rail is not None else VQPS_DEVELOPMENT_RAILS[0]
        )
        available_rails = {
            item.rail_id for item in bundle.scenario.base_project.rails
        }
        if diagnostic_rail not in available_rails:
            raise ValueError(
                "candidate SPD scenario cannot evaluate diagnostic rail: "
                f"{diagnostic_rail}"
            )
        with scoped_blas_threads():
            diagnostic = _run_layerwise_single_frequency_diagnostic(
                bundle.scenario,
                bundle.attachments,
                rail_id=diagnostic_rail,
                frequency_hz=args.layerwise_diagnostic_frequency_hz,
            )
        solver_identity = _solver_identity_report(
            LAYERWISE_ADMITTANCE_PROFILE.key
        )
        report = {
            "schema_version": LAYERWISE_DIAGNOSTIC_REPORT_SCHEMA_VERSION,
            "report_version": 1,
            "app_version": APP_VERSION,
            "mode": "layerwise_single_frequency_diagnostic",
            "status": diagnostic["status"],
            **solver_identity,
            "source": source,
            "candidate_bundle": candidate_bundle_report,
            "import": import_report,
            "source_state_validation": source_state_validation,
            "compiled_topology_asset": compiled_topology_asset,
            "candidate_import_report_binding": import_report_binding,
            "input_identity": {
                "source_candidate_match": True,
                "source_sha256": source["sha256"],
                "source_size_bytes": source["size_bytes"],
                "candidate_bundle_sha256": candidate_bundle_report["sha256"],
                "candidate_source_sha256": bundle.scenario.source.sha256,
                "candidate_source_size_bytes": bundle.scenario.source.size_bytes,
            },
            "touchstone_read": False,
            "adaptive_frequency_sweep_executed": False,
            "diagnostic": diagnostic,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(candidate_path)
        print(report_path)
        return 0

    if touchstone is None or not touchstone.is_file():
        raise ValueError("--touchstone must name an existing file")
    modes = tuple(dict.fromkeys(args.modal_max_index or (6, 12)))
    if args.modal_ceiling_index is not None and any(
        mode > args.modal_ceiling_index for mode in modes
    ):
        raise ValueError("--modal-ceiling-index cannot be below a requested start index")
    selected_rails = tuple(dict.fromkeys(args.rail or SELECTED_RAILS))

    source_network = read_touchstone(touchstone)
    manifest = complete_92_port_manifest(source_network)
    missing = [rail for rail in selected_rails if rail not in manifest]
    if missing:
        raise ValueError(f"92-port manifest is missing predeclared rails: {missing}")
    available_rails = {item.rail_id for item in bundle.scenario.base_project.rails}
    unavailable = [rail for rail in selected_rails if rail not in available_rails]
    if unavailable:
        raise ValueError(f"candidate SPD scenario cannot evaluate predeclared rails: {unavailable}")
    selected_ports = {rail: manifest[rail] for rail in selected_rails}
    # ``complete_92_port_manifest`` already proved the full exact header. Keep
    # the selected-subset guard exact as well, including PowerSI's run-qualified
    # labels (for example ``SITE0_0805-...``), instead of silently translating
    # them back to the legacy ``2nd_SITE0-...`` spelling.
    validate_selected_port_manifest(source_network, selected_ports)
    network, discarded_dc_count = _positive_frequency_network(source_network)
    converted = s_to_z(network)

    full_file_sha256 = _hash(touchstone)

    with scoped_blas_threads():
        candidate_runs, candidate_execution = _run_candidate_modes(
            bundle.scenario,
            network,
            converted,
            selected_ports,
            modes=modes,
            modal_ceiling_index=args.modal_ceiling_index,
            legacy_via_only=args.legacy_via_only,
            attachments=bundle.attachments,
            solver_profile=args.solver_profile,
            require_terminal_complete_reuse=args.require_terminal_complete_reuse,
        )

    identity_fields = _validated_report_identity_fields(
        source=source,
        candidate_bundle=candidate_bundle_report,
        candidate_source=bundle.scenario.source,
        candidate_runs=candidate_runs,
        solver_profile_key=args.solver_profile,
        compiled_topology_asset=compiled_topology_asset,
    )
    report: dict[str, Any] = {
        **identity_fields,
        "contract": "PowerSI is comparison-only; this runner never fits solver parameters from Touchstone values.",
        "source": source,
        "candidate_bundle": candidate_bundle_report,
        "import": import_report,
        "source_state_validation": source_state_validation,
        "compiled_topology_asset": compiled_topology_asset,
        "candidate_import_report_binding": import_report_binding,
        "touchstone": {
            "basename": touchstone.name, "sha256": full_file_sha256,
            "full_file_sha256": full_file_sha256,
            "ports": source_network.s_parameters.shape[1],
            "format": source_network.data_format, "reference_ohm": source_network.reference_ohm,
            "source_record_count": int(source_network.frequencies_hz.size),
            "converted_positive_frequency_record_count": int(network.frequencies_hz.size),
            "discarded_dc_record_count": discarded_dc_count,
            "header_manifest_validated": True, "manifest_92": manifest,
            "selected_rail_ports": selected_ports,
        },
        "score_split": {
            "vqps_development": [
                rail for rail in VQPS_DEVELOPMENT_RAILS if rail in selected_rails
            ],
            "vqps_holdout": [
                rail for rail in VQPS_HOLDOUT_RAILS if rail in selected_rails
            ],
            "loaded_final_holdout": [
                rail for rail in LOADED_FINAL_HOLDOUT_RAILS if rail in selected_rails
            ],
        },
        "run_schema": (
            "runs.candidate is keyed by string modal_max_index; it is empty only "
            "when run_execution.candidate records an explicit skipped status"
        ),
        "solver_profile": args.solver_profile,
        "run_execution": {"candidate": candidate_execution},
        "runs": {"candidate": candidate_runs},
        "ablations": {},
    }

    base_project = build_evaluation_project(
        bundle.scenario, evaluation_rail_id=selected_rails[0]
    )
    report["ablations"]["copper_sheet_loss"] = _sheet_loss_evidence(base_project)
    if args.legacy_via_ablation:
        analysis = analyze_spd(spd, scope="selected_pi")
        legacy_project, templates = _legacy_template_project(base_project, analysis)
        report["ablations"]["legacy_long_barrel_via_template"] = {"templates": templates, "runs": {}}
        for mode in modes:
            report["ablations"]["legacy_long_barrel_via_template"]["runs"][str(mode)] = _run_one(
                bundle.scenario, network, converted, selected_ports, modal_index=mode,
                attachments=bundle.attachments,
                solver_profile="legacy_modal_v017",
                project_transform=lambda project: _legacy_template_project(project, analysis)[0],
            )
    convergence_failures = _combined_convergence_gate_failures(
        candidate_runs,
        selected_rails,
        requested_modes=modes,
        require_full_rail_manifest=bool(args.require_all_converged),
        require_terminal_complete_invariance=bool(
            args.solver_profile == LAYERWISE_ADMITTANCE_PROFILE.key
        ),
    )
    report["release_gate"] = {
        "required": bool(args.require_all_converged),
        "status": (
            "passed"
            if not convergence_failures
            else "failed"
            if args.require_all_converged
            else "diagnostic_failures_present"
        ),
        "selected_rail_count": len(selected_rails),
        "expected_selected_rail_count": len(SELECTED_RAILS),
        "full_rail_manifest_complete": (
            len(selected_rails) == len(SELECTED_RAILS)
            and set(selected_rails) == set(SELECTED_RAILS)
        ),
        "requested_modal_max_indices": list(modes),
        "requested_run_count": len(modes),
        "completed_run_count": len(candidate_runs),
        "failure_count": len(convergence_failures),
        "failures": list(convergence_failures),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(candidate_path)
    print(report_path)
    if args.require_all_converged and convergence_failures:
        print(
            "Release convergence gate failed: "
            + "; ".join(convergence_failures[:8])
            + (
                f"; +{len(convergence_failures) - 8} more"
                if len(convergence_failures) > 8
                else ""
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
