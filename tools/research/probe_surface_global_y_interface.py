"""Bounded synthetic probe for the surface finite-port to global-Y seam."""

from __future__ import annotations

import argparse
import json
from math import pi
from pathlib import Path
import sys
from time import monotonic

import numpy as np
from shapely.geometry import box


_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str((_ROOT / "src").resolve()))

from spd_decap_pi._core.models.impedance import SeriesRLCModel  # noqa: E402
from spd_decap_pi._core.solver.global_mna import NodalAdmittanceBlock  # noqa: E402
from spd_decap_pi._core.solver.layer_surface_network import (  # noqa: E402
    LayerSurfaceNetworkError,
    LayerSurfacePort,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layer_surface_termination import (  # noqa: E402
    LayerSurfaceTerminationBranch,
    LayerSurfaceTerminationCluster,
    compile_layer_surface_termination_manifest,
)
from spd_decap_pi._core.solver.multilayer_capacitance import AdjacentGapMaxwellPartial  # noqa: E402
from spd_decap_pi._core.solver.modal import DielectricDispersion  # noqa: E402
from spd_decap_pi._core.solver.surface_patch_plane import (  # noqa: E402
    SurfacePatchArtwork,
    SurfacePatchDielectric,
    SurfacePatchFinitePort,
    SurfacePatchMesh,
    SurfacePatchPlaneError,
    compile_surface_patch_plane,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap  # noqa: E402


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
STUDY = "Astra Surface Global-Y Interface Probe"
FREQUENCY_HZ = 2.0e6
MAX_RUNTIME_S = 55.0
NODES = ("P0", "G0", "P1", "G1")
BASE_CAPACITANCE_F = 0.75e-9
REMOTE_LOAD_CAPACITANCE_F = 1.0e-6
REMOTE_LOAD_ESR_OHM = 0.08
REMOTE_LOAD_ESL_H = 10.0e-9
REMOTE_LOAD_MODEL = SeriesRLCModel(
    "synthetic-remote-series-rlc",
    capacitance_f=REMOTE_LOAD_CAPACITANCE_F,
    esr_ohm=REMOTE_LOAD_ESR_OHM,
    esl_h=REMOTE_LOAD_ESL_H,
)
BASE_OWNER = "synthetic:base-body"
SURFACE_OWNER = "synthetic:surface-global-y"
TERMINATION_OWNER = "synthetic:termination"
BASE_NETWORK_IDENTITY = "b" * 64


def _laplacian(admittance: complex) -> np.ndarray:
    return np.asarray(((admittance, -admittance), (-admittance, admittance)), dtype=np.complex128)


def _base_matrix(admittance: complex) -> np.ndarray:
    matrix = np.zeros((4, 4), dtype=np.complex128)
    matrix[np.ix_((0, 1), (0, 1))] += _laplacian(admittance)
    matrix[np.ix_((2, 3), (2, 3))] += _laplacian(admittance)
    return matrix


def _remote_load_impedance() -> complex:
    return complex(REMOTE_LOAD_MODEL.impedance((FREQUENCY_HZ,))[0])


def _artwork(*items: tuple[str, str, object]) -> tuple[SurfacePatchArtwork, ...]:
    return tuple(SurfacePatchArtwork(layer, net, geometry) for layer, net, geometry in items)


def _surface_operator(*, separated: bool = False):
    if not separated:
        strip = box(0, 0, 3_000, 1_000)
        artwork = _artwork(
            ("TOP", "P", strip), ("BOT", "G", strip),
        )
        cell_um = 1_000.0
    else:
        left = box(0, 0, 900, 1_000)
        right = box(1_100, 0, 2_000, 1_000)
        strip = box(0, 0, 2_000, 1_000)
        artwork = _artwork(
            ("TOP", "P", strip),
            ("BOT", "G", strip),
        )
        cell_um = 1_000.0
    mesh = SurfacePatchMesh.uniform(layer_order=("TOP", "BOT"), artwork=artwork, cell_um=cell_um)
    return compile_surface_patch_plane(
        mesh,
        dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0, 0.01),),
        synthetic_material_defaults=True,
    )


def _surface_condensation():
    left = box(0, 0, 1_000, 1_000)
    right = box(2_000, 0, 3_000, 1_000)
    return _surface_operator().condense_finite_ports(
        FREQUENCY_HZ,
        (
            SurfacePatchFinitePort("P0", "TOP", "P", left, "synthetic co-located P0 footprint"),
            SurfacePatchFinitePort("G0", "BOT", "G", left, "synthetic co-located G0 footprint"),
            SurfacePatchFinitePort("P1", "TOP", "P", right, "synthetic co-located P1 footprint"),
            SurfacePatchFinitePort("G1", "BOT", "G", right, "synthetic co-located G1 footprint"),
        ),
    )


def _base_partial() -> DispersiveAdjacentGap:
    capacitance = BASE_CAPACITANCE_F * np.asarray(
        ((1.0, -1.0, 0.0, 0.0), (-1.0, 1.0, 0.0, 0.0),
         (0.0, 0.0, 1.0, -1.0), (0.0, 0.0, -1.0, 1.0))
    )
    partial = AdjacentGapMaxwellPartial(
        upper_layer="BASE",
        lower_layer="BASE",
        nominal_relative_permittivity=4.0,
        net_names=NODES,
        maxwell_capacitance_f=capacitance,
    )
    return DispersiveAdjacentGap(
        partial=partial,
        dispersion=DielectricDispersion((FREQUENCY_HZ,), (4.0,), (0.0,)),
    )


def _network(via_links: tuple[LayerSurfaceViaLink, ...] = ()):
    return compile_layer_surface_network(
        NODES,
        partials=(_base_partial(),),
        via_links=via_links,
        ports=(LayerSurfacePort("drive", "P0", "G0"), LayerSurfacePort("remote", "P1", "G1")),
    )


def _termination_manifest(owner: str = TERMINATION_OWNER):
    branch = LayerSurfaceTerminationBranch(
        "load-branch",
        "LOAD_P",
        "LOAD_G",
        REMOTE_LOAD_MODEL,
        owner_ids=(owner,),
    )
    cluster = LayerSurfaceTerminationCluster(
        cluster_id="load-cluster",
        positive_surface_node_id="P1",
        negative_surface_node_id="G1",
        positive_terminal_node_id="LOAD_P",
        negative_terminal_node_id="LOAD_G",
        branches=(branch,),
        rail_owner_ids=("synthetic:rail",),
    )
    return compile_layer_surface_termination_manifest(NODES, (cluster,))


def _block(condensation, owner: str = SURFACE_OWNER) -> NodalAdmittanceBlock:
    return NodalAdmittanceBlock(
        NODES,
        condensation.terminal_admittance_s,
        "surface-global-y",
        owner_ids=(owner,),
    )


def _matrix_metrics(matrix: np.ndarray) -> dict[str, float]:
    scale = max(float(np.linalg.norm(matrix)), np.finfo(float).tiny)
    hermitian = (matrix + matrix.conj().T) * 0.5
    return {
        "reciprocity_relative": float(np.linalg.norm(matrix - matrix.T) / scale),
        "row_sum_max_abs": float(np.max(np.abs(matrix @ np.ones(matrix.shape[0])))),
        "passivity_min_hermitian_eigenvalue_s": float(np.min(np.linalg.eigvalsh(hermitian))),
    }


def _direct_differential_reference(condensation) -> tuple[complex, complex, np.ndarray, np.ndarray]:
    """Solve the independent four-terminal nodal reference in balanced coordinates."""
    omega = 2.0 * pi * FREQUENCY_HZ
    base_y = 1j * omega * BASE_CAPACITANCE_F
    incidence = np.asarray(
        ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)),
        dtype=np.complex128,
    )
    terminal_y = np.asarray(condensation.terminal_admittance_s)
    surface_pair_y = 0.25 * incidence.T @ terminal_y @ incidence
    pair_y = surface_pair_y + base_y * np.eye(2, dtype=np.complex128)
    pair_z = np.linalg.inv(pair_y)
    load_z = _remote_load_impedance()
    load_y = 1.0 / load_z
    loaded_pair_y = pair_y.copy()
    loaded_pair_y[1, 1] += load_y
    differential_impedance = np.linalg.inv(loaded_pair_y)
    direct_matrix = _base_matrix(base_y) + terminal_y
    direct_matrix[np.ix_((2, 3), (2, 3))] += _laplacian(load_y)
    nodal_differential_impedance = incidence.T @ np.linalg.pinv(direct_matrix) @ incidence
    schur_drive_impedance = pair_z[0, 0] - pair_z[0, 1] * pair_z[1, 0] / (pair_z[1, 1] + load_z)
    return (
        complex(1.0 / differential_impedance[0, 0]),
        complex(1.0 / differential_impedance[1, 1]),
        direct_matrix,
        np.asarray(
            (
                differential_impedance[0, 0],
                differential_impedance[1, 1],
                differential_impedance[0, 1],
                schur_drive_impedance,
                nodal_differential_impedance[0, 0],
            ),
            dtype=np.complex128,
        ),
    )


def _positive_probe(condensation) -> dict[str, object]:
    manifest = _termination_manifest()
    block = _block(condensation)
    direct, direct_remote, direct_matrix, differential_impedance = _direct_differential_reference(condensation)
    network_error = None
    solved = None
    try:
        solved = _network().solve(
            (FREQUENCY_HZ,),
            termination_manifest=manifest,
            selected_rail_id="synthetic:rail",
            base_network_identity_sha256=BASE_NETWORK_IDENTITY,
            supplemental_nodal_admittance=block,
        )
    except LayerSurfaceNetworkError as exc:
        network_error = {"type": type(exc).__name__, "message": str(exc)}
    observed = None if solved is None else complex(solved.effective_admittance_by_port["drive"][0])
    observed_remote = None if solved is None else complex(solved.effective_admittance_by_port["remote"][0])
    direct_metrics = _matrix_metrics(direct_matrix)
    incidence = np.asarray(
        ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)),
        dtype=np.float64,
    )
    constraint = np.asarray(condensation.terminal_constraint_matrix, dtype=np.float64)
    constraint_incidence_residual = float(np.linalg.norm(constraint.T @ incidence))
    drive_schur_relative = float(
        abs(differential_impedance[0] - differential_impedance[3])
        / max(abs(differential_impedance[0]), np.finfo(float).tiny)
    )
    drive_nodal_pinv_relative = float(
        abs(differential_impedance[0] - differential_impedance[4])
        / max(abs(differential_impedance[0]), np.finfo(float).tiny)
    )
    source_cluster = manifest.source_manifest["clusters"][0]
    source_owner_ids = sorted(
        set(source_cluster["rail_owner_ids"])
        | set(source_cluster["topology_owner_ids"])
        | {
            owner
            for branch in source_cluster["branches"]
            for owner in branch["owner_ids"]
        }
    )
    return {
        "frequency_hz": FREQUENCY_HZ,
        "terminals": ["P0", "G0", "P1", "G1"],
        "observed_effective_admittance_s": None if observed is None else {"real": observed.real, "imag": observed.imag},
        "observed_driving_impedance_ohm": None if observed is None else {"real": (1.0 / observed).real, "imag": (1.0 / observed).imag},
        "direct_reference_admittance_s": {"real": direct.real, "imag": direct.imag},
        "direct_reference_driving_impedance_ohm": {"real": (1.0 / direct).real, "imag": (1.0 / direct).imag},
        "relative_response_error": None if observed is None else float(abs(observed - direct) / abs(direct)),
        "remote_observed_effective_admittance_s": None if observed_remote is None else {"real": observed_remote.real, "imag": observed_remote.imag},
        "remote_direct_reference_admittance_s": {"real": direct_remote.real, "imag": direct_remote.imag},
        "remote_relative_response_error": None if observed_remote is None else float(abs(observed_remote - direct_remote) / abs(direct_remote)),
        "differential_reference_impedance_ohm": {
            "drive_drive_loaded": {"real": differential_impedance[0].real, "imag": differential_impedance[0].imag},
            "remote_remote_loaded": {"real": differential_impedance[1].real, "imag": differential_impedance[1].imag},
            "drive_remote_loaded": {"real": differential_impedance[2].real, "imag": differential_impedance[2].imag},
            "drive_schur_from_pair_z": {"real": differential_impedance[3].real, "imag": differential_impedance[3].imag},
            "drive_nodal_pinv": {"real": differential_impedance[4].real, "imag": differential_impedance[4].imag},
        },
        "differential_reference_consistency": {
            "loaded_z00_vs_schur_relative": drive_schur_relative,
            "loaded_z00_vs_nodal_pinv_relative": drive_nodal_pinv_relative,
        },
        "network_solve": {"status": "PASS" if solved is not None else "STOP", "error": network_error},
        "direct_reference_metrics": direct_metrics,
        "condensation": {
            "port_ids": condensation.port_ids,
            "terminal_constraint_matrix": condensation.terminal_constraint_matrix.tolist(),
            "solve_residual": condensation.solve_residual,
            "compatible_current_residual": condensation.compatible_current_residual,
            "gauge_residual": condensation.gauge_residual,
            "reciprocity_relative": condensation.reciprocity_relative,
            "passivity_min_eigenvalue_s": condensation.passivity_min_eigenvalue_s,
            "passivity_tolerance_s": condensation.passivity_tolerance_s,
            "terminal_condition": condensation.terminal_condition,
            "terminal_row_sum_max_abs": float(np.max(np.abs(condensation.terminal_admittance_s @ np.ones(4)))),
            "terminal_constraint_rank": int(np.linalg.matrix_rank(condensation.terminal_constraint_matrix)),
            "terminal_constraint_nullity": int(4 - np.linalg.matrix_rank(condensation.terminal_constraint_matrix)),
            "constraint_transpose_incidence_residual": constraint_incidence_residual,
        },
        "stamp_manifest": {
            "base": {"owner_id": BASE_OWNER, "numeric_stamp_count": 1},
            "supplemental": {"block_id": block.block_id, "owner_ids": block.owner_ids, "numeric_stamp_count": 1},
            "termination": {
                "manifest_sha256": manifest.manifest_sha256,
                "source_owner_ids": source_owner_ids,
                "numeric_stamp_count": 1,
                "network_active_cluster_count": None if solved is None else solved.diagnostics.active_termination_cluster_count,
                "network_active_element_count": None if solved is None else solved.diagnostics.active_termination_element_count,
            },
        },
        "network_diagnostics": {
            "structural_component_count": None if solved is None else solved.diagnostics.structural_component_count,
            "active_structural_component_count": None if solved is None else solved.diagnostics.active_structural_component_count,
            "maximum_factor_pivot_ratio": None if solved is None else solved.diagnostics.maximum_factor_pivot_ratio,
            "maximum_relative_residual": None if solved is None else solved.diagnostics.maximum_relative_residual,
            "termination_manifest_sha256": None if solved is None else solved.diagnostics.termination_manifest_sha256,
            "solve_identity_sha256": None if solved is None else solved.diagnostics.solve_identity_sha256,
        },
    }


def _negative_duplicate_base(condensation) -> dict[str, object]:
    del condensation
    network = _network()
    base_y = 1j * 2.0 * pi * FREQUENCY_HZ * BASE_CAPACITANCE_F
    duplicate = NodalAdmittanceBlock(NODES, _base_matrix(base_y), "duplicate-base", owner_ids=(BASE_OWNER,))
    solved = network.solve((FREQUENCY_HZ,), supplemental_nodal_admittance=duplicate)
    observed = complex(solved.effective_admittance_by_port["drive"][0])
    observed_remote = complex(solved.effective_admittance_by_port["remote"][0])
    expected_once = base_y
    return {
        "status": "STOP_EXPECTED_DUPLICATION",
        "base_owner_id": BASE_OWNER,
        "supplemental_owner_id": duplicate.owner_ids[0],
        "base_numeric_stamp_count": 1,
        "supplemental_numeric_stamp_count": 1,
        "observed_admittance_s": {"real": observed.real, "imag": observed.imag},
        "one_stamp_reference_admittance_s": {"real": expected_once.real, "imag": expected_once.imag},
        "observed_to_one_stamp_ratio": float(abs(observed / expected_once)),
        "remote_observed_to_one_stamp_ratio": float(abs(observed_remote / expected_once)),
        "same_owner_duplicate_not_rejected": True,
    }


def _error_check(callback, expected: str) -> dict[str, str]:
    try:
        callback()
    except Exception as exc:  # noqa: BLE001 - exact fail-closed evidence
        message = str(exc)
        if message != expected:
            raise AssertionError(f"expected {expected!r}, got {message!r}") from exc
        return {"type": type(exc).__name__, "message": message}
    raise AssertionError(f"expected fail-closed message: {expected}")


def _negative_controls(condensation) -> dict[str, object]:
    block = _block(condensation)
    via = LayerSurfaceViaLink(
        "surface-via",
        "P0",
        "G0",
        1,
        "finite_parallel_rl",
        0.01,
        1.0e-9,
        (SURFACE_OWNER,),
    )
    via_error = _error_check(
        lambda: _network((via,)).solve((FREQUENCY_HZ,), supplemental_nodal_admittance=block),
        "supplemental nodal owner conflicts with Via owner",
    )
    termination_error = _error_check(
        lambda: _network().solve(
            (FREQUENCY_HZ,),
            termination_manifest=_termination_manifest(SURFACE_OWNER),
            selected_rail_id="synthetic:rail",
            base_network_identity_sha256=BASE_NETWORK_IDENTITY,
            supplemental_nodal_admittance=block,
        ),
        "supplemental nodal owner conflicts with termination owner",
    )
    separated = _surface_operator(separated=True)
    left = box(0, 0, 900, 1_000)
    right = box(1_100, 0, 2_000, 1_000)
    no_return = _error_check(
        lambda: separated.condense_finite_ports(
            FREQUENCY_HZ,
            (
                SurfacePatchFinitePort("P-separated", "TOP", "P", left, "synthetic separated P"),
                SurfacePatchFinitePort("G-separated", "BOT", "G", right, "synthetic separated G"),
            ),
        ),
        "finite-port input has no admissible return mode",
    )
    return {
        "via_owner_conflict": via_error,
        "termination_owner_conflict": termination_error,
        "spatially_separated_p_g": no_return,
    }


def run_probe(max_runtime_s: float = 45.0) -> dict[str, object]:
    if not np.isfinite(max_runtime_s) or not 1.0 <= max_runtime_s <= MAX_RUNTIME_S:
        raise ValueError(f"max runtime must be in [1, {MAX_RUNTIME_S:g}] seconds")
    started = monotonic()
    condensation = _surface_condensation()
    positive = _positive_probe(condensation)
    negative_duplicate = _negative_duplicate_base(condensation)
    negative_controls = _negative_controls(condensation)
    total_s = monotonic() - started
    if total_s >= max_runtime_s:
        raise TimeoutError("bounded interface probe exceeded its wall-time limit")
    checks = {
        "positive_response_matches_direct_reference": (
            positive["relative_response_error"] is not None
            and positive["relative_response_error"] <= 1.0e-10
        ),
        "remote_response_matches_direct_reference": (
            positive["remote_relative_response_error"] is not None
            and positive["remote_relative_response_error"] <= 1.0e-10
        ),
        "terminal_y_is_reciprocal_passive_and_floating": (
            positive["condensation"]["reciprocity_relative"] <= 1.0e-12
            and positive["condensation"]["passivity_min_eigenvalue_s"]
            >= -positive["condensation"]["passivity_tolerance_s"]
            and positive["condensation"]["terminal_row_sum_max_abs"] <= 1.0e-10
            and positive["condensation"]["terminal_constraint_nullity"] == 2
            and positive["condensation"]["constraint_transpose_incidence_residual"] <= 1.0e-12
        ),
        "direct_differential_reference_is_consistent": (
            positive["differential_reference_consistency"]["loaded_z00_vs_schur_relative"] <= 1.0e-10
            and positive["differential_reference_consistency"]["loaded_z00_vs_nodal_pinv_relative"] <= 1.0e-10
        ),
        "base_and_load_each_stamp_once": (
            positive["stamp_manifest"]["base"]["numeric_stamp_count"] == 1
            and positive["stamp_manifest"]["supplemental"]["numeric_stamp_count"] == 1
            and positive["stamp_manifest"]["termination"]["numeric_stamp_count"] == 1
            and positive["stamp_manifest"]["termination"]["network_active_cluster_count"] == 1
            and positive["stamp_manifest"]["termination"]["network_active_element_count"] == 1
        ),
        "base_load_termination_manifest_declared_once": (
            positive["stamp_manifest"]["base"]["numeric_stamp_count"] == 1
            and positive["stamp_manifest"]["supplemental"]["numeric_stamp_count"] == 1
            and positive["stamp_manifest"]["termination"]["numeric_stamp_count"] == 1
        ),
        "network_residual_and_direct_matrix_gates": (
            positive["network_diagnostics"]["maximum_relative_residual"] is not None
            and positive["network_diagnostics"]["maximum_relative_residual"] <= 1.0e-9
            and positive["network_diagnostics"]["structural_component_count"] == 1
            and positive["network_diagnostics"]["active_structural_component_count"] == 1
            and positive["direct_reference_metrics"]["reciprocity_relative"] <= 1.0e-12
            and positive["direct_reference_metrics"]["row_sum_max_abs"] <= 1.0e-18
            and positive["direct_reference_metrics"]["passivity_min_hermitian_eigenvalue_s"] >= -1.0e-18
        ),
        "via_and_termination_owner_conflicts_fail_closed": all(
            key in negative_controls for key in ("via_owner_conflict", "termination_owner_conflict")
        ),
        "separated_p_g_stops_without_return_mode": "spatially_separated_p_g" in negative_controls,
        "duplicate_base_negative_control_is_two_stamps": (
            abs(negative_duplicate["observed_to_one_stamp_ratio"] - 2.0) <= 1.0e-10
            and abs(negative_duplicate["remote_observed_to_one_stamp_ratio"] - 2.0) <= 1.0e-10
        ),
        "expected_multigauge_nodal_stop": (
            positive["network_solve"]["status"] == "STOP"
            and positive["network_solve"]["error"]["message"]
            == "layer-network Kron block is singular near ('P0', 'G0', 'P1', 'G1')"
            and positive["condensation"]["terminal_constraint_nullity"] == 2
        ),
    }
    status = "ACCEPT_CANONICAL_ONLY" if all(checks.values()) else "STOP"
    return {
        "program": PROGRAM,
        "version": VERSION,
        "study": STUDY,
        "status": status,
        "scope": "synthetic interface seam only; no production, source-connectivity, or PowerSI claim",
        "inputs": {
            "frequency_hz": FREQUENCY_HZ,
            "base_capacitance_f": BASE_CAPACITANCE_F,
            "remote_series_rlc": {
                "capacitance_f": REMOTE_LOAD_CAPACITANCE_F,
                "esr_ohm": REMOTE_LOAD_ESR_OHM,
                "esl_h": REMOTE_LOAD_ESL_H,
                "impedance_at_frequency_ohm": {
                    "real": _remote_load_impedance().real,
                    "imag": _remote_load_impedance().imag,
                },
            },
            "max_runtime_s": max_runtime_s,
        },
        "positive_probe": positive,
        "negative_controls": negative_controls,
        "negative_duplicate_base": negative_duplicate,
        "acceptance": {"checks": checks, "unchanged_scope": "canonical-only; replacement support is not demonstrated"},
        "resources": {"total_elapsed_s": total_s, "within_requested_process_limit": total_s < 60.0},
        "nonclaims": [
            "This does not modify or judge the product adapter or live layerwise replacement path.",
            "This does not prove source connectivity, SPD/D117 validity, or PowerSI accuracy.",
            "The duplicate-base result is an expected negative control; owner checks do not prevent replacement-vs-additive duplication.",
        ],
    }


def _self_check() -> None:
    result = run_probe(45.0)
    assert result["status"] == "STOP"
    assert result["acceptance"]["checks"]["expected_multigauge_nodal_stop"]
    assert result["acceptance"]["checks"]["terminal_y_is_reciprocal_passive_and_floating"]
    assert result["acceptance"]["checks"]["direct_differential_reference_is_consistent"]
    assert result["positive_probe"]["network_solve"]["error"]["message"] == (
        "layer-network Kron block is singular near ('P0', 'G0', 'P1', 'G1')"
    )
    assert result["positive_probe"]["condensation"]["terminal_constraint_nullity"] == 2
    assert result["acceptance"]["checks"]["separated_p_g_stops_without_return_mode"]
    assert result["acceptance"]["checks"]["duplicate_base_negative_control_is_two_stamps"]
    assert result["negative_controls"]["spatially_separated_p_g"]["message"] == "finite-port input has no admissible return mode"
    print("SELF_CHECK PASS")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} - {STUDY}")
    parser.add_argument("--output", type=Path, help="new JSON path; an existing path is never overwritten")
    parser.add_argument("--max-runtime-s", type=float, default=45.0)
    parser.add_argument("--self-check", action="store_true", help="run the bounded seam and fail-closed checks")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - {STUDY}")
    if args.self_check:
        _self_check()
        return 0
    if args.output is None:
        parser.error("--output is required unless --self-check is supplied")
    output = args.output.resolve()
    if output.exists():
        parser.error(f"refusing to overwrite existing output: {output}")
    if not output.parent.is_dir():
        parser.error(f"output directory does not exist: {output.parent}")
    try:
        result = run_probe(args.max_runtime_s)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except (LayerSurfaceNetworkError, SurfacePatchPlaneError, OSError, TimeoutError, ValueError, AssertionError) as exc:
        print(f"{PROGRAM} v{VERSION}: ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
