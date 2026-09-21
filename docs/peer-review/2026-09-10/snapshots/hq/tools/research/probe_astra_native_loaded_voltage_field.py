#!/usr/bin/env python3
"""Capture one native loaded 1 MHz voltage/current field without changing the solver.

This is a research-only observer around the existing successful VTRIP loaded
development driver.  It deliberately does not assemble or persist a new global
matrix.  Instead it observes the existing scaled sparse solve, reconstructs
the unscaled retained solution with the solver's own row scale, and preserves
the active-node mapping, original gap partials, finite-link ledger, and
evaluated termination stamps in a non-pickle NPZ snapshot.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.sparse import csc_matrix

import probe_astra_native_device_branch_2port as base
import probe_astra_native_loaded_development_rail as loaded
from spd_decap_pi._core.solver import layer_surface_network as _layer_surface_network
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfacePort,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.modal import DielectricDispersion
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap


FREQUENCY_HZ = 1.0e6
TARGET_RAIL_ID = "ADC_VDD_075_VTRIP_SRAM/0"
EXPECTED_SOURCE_MOUNTED_COUNT = 421
EXPECTED_TERMINATION_CLUSTER_COUNT = 11050
EXPECTED_LOADED_DRIVER_SHA256 = (
    "3c1fd9c4cd2c3247e2b1f2ae41c78ed6310d25092a92e6b9751a7593e4fcdf52"
)
EXPECTED_BASE_DRIVER_SHA256 = (
    "5f73464f9bd8aea1eac54bae7c7840134fbf19a63d62b28e1edb57e76ec844eb"
)
EXPECTED_REFERENCE_RECEIPT_SHA256 = (
    "e2c1f16ce4ff3998445e09c6cde2d1b23cdbc4d2c5e102e32c5f3d915e51790d"
)
REFERENCE_ZDD_REL_TOL = 1.0e-10
REFERENCE_ZDD_ABS_TOL = 1.0e-15
FIELD_POWER_REL_TOL = 1.0e-6
FIELD_POWER_ABS_TOL = 1.0e-12
EXPECTED_ORIGINAL_FREQUENCIES_HZ = (1.0e6, 1.0e7, 1.0e8, 1.0e9)
RAW_SNAPSHOT_STATUS = "UNVALIDATED_RAW_SOLVER_FRAME"
TEXT_VECTOR_ENCODING = "utf8_json_uint8"
TEXT_SCALAR_ENCODING = "utf8_json_uint8_scalar"
TEXT_ENCODING_SCHEMA_VERSION = 1


def _complex(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Publish one complete JSON document without replacing an existing one."""

    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and fails if a competing writer won
        # the destination, preserving the output refusal contract.
        os.link(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _start_shutdown_safe_watchdog(budget: Any) -> Any:
    """Keep the inherited watchdog from treating normal shutdown as a timeout."""

    inherited_cancelled = budget.cancelled

    def shutdown_safe_cancelled() -> bool:
        exceeded = bool(inherited_cancelled())
        # _Budget.cancelled() reports stop.set() as True.  During normal
        # teardown no budget reason is set, so suppress only that sentinel;
        # real runtime/RSS violations retain their reason and remain fatal.
        if exceeded and budget.stop.is_set() and budget.reason is None:
            return False
        return exceeded

    budget.cancelled = shutdown_safe_cancelled
    return budget.start_watchdog()


def _copy_array(value: Any, dtype: np.dtype[Any]) -> np.ndarray[Any, Any]:
    result = np.array(value, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


def _text_array(values: Sequence[object]) -> np.ndarray[Any, Any]:
    """Encode a variable-length text vector without NumPy max-width Unicode."""

    payload = json.dumps(
        [str(value) for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return np.frombuffer(payload, dtype=np.uint8).copy()


def _scalar_text(value: object) -> np.ndarray[Any, Any]:
    """Encode one scalar distinctly from the JSON vector representation."""

    payload = json.dumps(
        str(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return np.frombuffer(payload, dtype=np.uint8).copy()


def _decode_text_vector(payload: np.ndarray[Any, Any]) -> tuple[str, ...]:
    raw = np.asarray(payload)
    if raw.dtype != np.dtype(np.uint8) or raw.ndim != 1:
        raise ValueError("text vector payload must be one uint8 array")
    value = json.loads(raw.tobytes().decode("utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("text vector payload is not a JSON string array")
    return tuple(value)


def _decode_scalar_text(payload: np.ndarray[Any, Any]) -> str:
    raw = np.asarray(payload)
    if raw.dtype != np.dtype(np.uint8) or raw.ndim != 1:
        raise ValueError("text scalar payload must be one uint8 array")
    value = json.loads(raw.tobytes().decode("utf-8"))
    if not isinstance(value, str):
        raise ValueError("text scalar payload is not a JSON string")
    return value


def _add_text_encoding_metadata(
    arrays: dict[str, np.ndarray[Any, Any]],
) -> None:
    arrays["text_encoding_schema_version"] = np.asarray(
        [TEXT_ENCODING_SCHEMA_VERSION],
        dtype=np.int64,
    )
    arrays["text_vector_encoding_tag_utf8"] = np.frombuffer(
        TEXT_VECTOR_ENCODING.encode("utf-8"),
        dtype=np.uint8,
    ).copy()
    arrays["text_scalar_encoding_tag_utf8"] = np.frombuffer(
        TEXT_SCALAR_ENCODING.encode("utf-8"),
        dtype=np.uint8,
    ).copy()


def _assert_finite_complex(value: np.ndarray[Any, Any], label: str) -> None:
    if not np.all(np.isfinite(value.real)) or not np.all(np.isfinite(value.imag)):
        raise ValueError(f"{label} contains a non-finite complex value")


def _validate_unscaled_solution(
    row_scale: np.ndarray[Any, Any],
    scaled_solution: np.ndarray[Any, Any],
    unscaled_solution: np.ndarray[Any, Any],
) -> None:
    """Reject a raw factor result accidentally treated as physical voltage."""

    expected = row_scale[:, None] * scaled_solution
    if not np.array_equal(unscaled_solution, expected):
        raise ValueError(
            "observed voltage is not exactly row_scale * scaled_solution"
        )


def _close_complex(
    actual: complex,
    expected: complex,
    *,
    rtol: float,
    atol: float,
) -> bool:
    return bool(
        np.isclose(actual.real, expected.real, rtol=rtol, atol=atol)
        and np.isclose(actual.imag, expected.imag, rtol=rtol, atol=atol)
    )


@dataclass(frozen=True, slots=True)
class _SolveFrameCapture:
    """One direct observation of the existing solve_frequency_reserved frame."""

    network: Any
    frequency_hz: float
    active_global_nodes: np.ndarray[Any, Any]
    global_to_active: np.ndarray[Any, Any]
    retained_active_nodes: np.ndarray[Any, Any]
    gauge_active_node: int
    row_scale: np.ndarray[Any, Any]
    scaled_rhs: np.ndarray[Any, Any]
    scaled_solution: np.ndarray[Any, Any]
    unscaled_solution: np.ndarray[Any, Any]
    active_voltage: np.ndarray[Any, Any]
    batch_port_indices: np.ndarray[Any, Any]
    batch_port_ids: tuple[str, ...]
    solve_port_reduced_nodes: tuple[tuple[int, int], ...]
    active_partials: tuple[Any, ...]
    dispersion_coefficients_s: np.ndarray[Any, Any]
    finite_first_active: np.ndarray[Any, Any]
    finite_second_active: np.ndarray[Any, Any]
    finite_count: np.ndarray[Any, Any]
    finite_resistance_ohm: np.ndarray[Any, Any]
    finite_inductance_h: np.ndarray[Any, Any]
    immutable_terminations: tuple[tuple[int, int, Any], ...]


class _SolveFrameObserver:
    """Capture only a two-dimensional RHS solved by the real Kron frame."""

    def __init__(self) -> None:
        self._captures: list[_SolveFrameCapture] = []

    @property
    def captures(self) -> tuple[_SolveFrameCapture, ...]:
        return tuple(self._captures)

    def record(
        self,
        caller: Any,
        scaled_rhs_argument: Any,
        raw_factor_result: Any,
    ) -> None:
        if caller is None or caller.f_code.co_name != "solve_frequency_reserved":
            return
        values = caller.f_locals
        required = (
            "self",
            "frequency",
            "active_global_nodes",
            "global_to_active",
            "retained",
            "gauge",
            "row_scale",
            "rhs",
            "batch_indices",
            "solve_port_ids",
            "solve_port_reduced_nodes",
            "active_partials",
            "dispersion_values",
            "finite_first",
            "finite_second",
            "finite_counts",
            "finite_resistance",
            "finite_inductance",
            "immutable_terminations",
        )
        if any(name not in values for name in required):
            return
        scaled_rhs = np.asarray(scaled_rhs_argument, dtype=np.complex128)
        if scaled_rhs.ndim != 2:
            # The high-pivot condition estimator invokes factor.solve on a
            # vector.  It is not a port-response field observation.
            return
        raw_rhs = np.asarray(values["rhs"], dtype=np.complex128)
        row_scale = np.asarray(values["row_scale"], dtype=np.float64)
        scaled_solution = np.asarray(raw_factor_result, dtype=np.complex128)
        if (
            raw_rhs.ndim != 2
            or row_scale.ndim != 1
            or scaled_rhs.shape != raw_rhs.shape
            or scaled_solution.shape != scaled_rhs.shape
            or row_scale.shape[0] != raw_rhs.shape[0]
        ):
            raise ValueError("observed sparse-solve frame has incompatible shapes")
        if not np.array_equal(scaled_rhs, row_scale[:, None] * raw_rhs):
            raise ValueError("factor input differs from solver row-scaled RHS")
        unscaled_solution = row_scale[:, None] * scaled_solution
        _validate_unscaled_solution(row_scale, scaled_solution, unscaled_solution)
        _assert_finite_complex(scaled_solution, "scaled_solution")
        _assert_finite_complex(unscaled_solution, "unscaled_solution")

        active_global_nodes = np.asarray(values["active_global_nodes"], dtype=np.int64)
        global_to_active = np.asarray(values["global_to_active"], dtype=np.int64)
        retained = np.asarray(values["retained"], dtype=np.int64)
        gauge = int(values["gauge"])
        if (
            active_global_nodes.ndim != 1
            or global_to_active.ndim != 1
            or retained.ndim != 1
            or active_global_nodes.size != np.unique(active_global_nodes).size
            or retained.size != unscaled_solution.shape[0]
            or gauge < 0
            or gauge >= active_global_nodes.size
            or np.any(retained < 0)
            or np.any(retained >= active_global_nodes.size)
            or gauge in set(int(value) for value in retained)
        ):
            raise ValueError("observed active/gauge/retained mapping is invalid")
        if not np.array_equal(global_to_active[active_global_nodes], np.arange(active_global_nodes.size)):
            raise ValueError("active_global_nodes does not invert global_to_active")
        full_active = np.zeros(
            (active_global_nodes.size, unscaled_solution.shape[1]),
            dtype=np.complex128,
        )
        full_active[retained, :] = unscaled_solution
        if not np.array_equal(full_active[gauge, :], np.zeros(unscaled_solution.shape[1])):
            raise ValueError("the observed gauge is not the zero reference")

        batch_indices = np.asarray(values["batch_indices"], dtype=np.int64)
        solve_port_ids = tuple(str(value) for value in values["solve_port_ids"])
        if (
            batch_indices.ndim != 1
            or batch_indices.size != unscaled_solution.shape[1]
            or np.any(batch_indices < 0)
            or np.any(batch_indices >= len(solve_port_ids))
        ):
            raise ValueError("observed port batch mapping is invalid")
        batch_port_ids = tuple(solve_port_ids[int(index)] for index in batch_indices)
        port_nodes = tuple(
            (int(pair[0]), int(pair[1]))
            for pair in values["solve_port_reduced_nodes"]
        )
        if len(port_nodes) != len(solve_port_ids):
            raise ValueError("observed port endpoint inventory is invalid")

        active_partials = tuple(values["active_partials"])
        coefficients = np.asarray(
            [
                complex(np.asarray(item, dtype=np.complex128)[0])
                for item in values["dispersion_values"]
            ],
            dtype=np.complex128,
        )
        if len(active_partials) != coefficients.size:
            raise ValueError("partial/dispersion inventory is not aligned")

        captured = _SolveFrameCapture(
            network=values["self"],
            frequency_hz=float(values["frequency"]),
            active_global_nodes=_copy_array(active_global_nodes, np.dtype(np.int64)),
            global_to_active=_copy_array(global_to_active, np.dtype(np.int64)),
            retained_active_nodes=_copy_array(retained, np.dtype(np.int64)),
            gauge_active_node=gauge,
            row_scale=_copy_array(row_scale, np.dtype(np.float64)),
            scaled_rhs=_copy_array(scaled_rhs, np.dtype(np.complex128)),
            scaled_solution=_copy_array(scaled_solution, np.dtype(np.complex128)),
            unscaled_solution=_copy_array(unscaled_solution, np.dtype(np.complex128)),
            active_voltage=_copy_array(full_active, np.dtype(np.complex128)),
            batch_port_indices=_copy_array(batch_indices, np.dtype(np.int64)),
            batch_port_ids=batch_port_ids,
            solve_port_reduced_nodes=port_nodes,
            active_partials=active_partials,
            dispersion_coefficients_s=_copy_array(
                coefficients, np.dtype(np.complex128)
            ),
            finite_first_active=_copy_array(
                values["finite_first"], np.dtype(np.int64)
            ),
            finite_second_active=_copy_array(
                values["finite_second"], np.dtype(np.int64)
            ),
            finite_count=_copy_array(values["finite_counts"], np.dtype(np.float64)),
            finite_resistance_ohm=_copy_array(
                values["finite_resistance"], np.dtype(np.float64)
            ),
            finite_inductance_h=_copy_array(
                values["finite_inductance"], np.dtype(np.float64)
            ),
            immutable_terminations=tuple(values["immutable_terminations"]),
        )
        self._captures.append(captured)


class _ObservedFactor:
    """Forward every SuperLU attribute while observing only its real solve call."""

    def __init__(self, factor: Any, observer: _SolveFrameObserver) -> None:
        self._factor = factor
        self._observer = observer

    def solve(self, rhs: Any, *args: Any, **kwargs: Any) -> Any:
        caller = inspect.currentframe().f_back
        try:
            result = self._factor.solve(rhs, *args, **kwargs)
            self._observer.record(caller, rhs, result)
            return result
        finally:
            del caller

    def __getattr__(self, name: str) -> Any:
        return getattr(self._factor, name)


def _install_solution_observer(observer: _SolveFrameObserver) -> Any:
    """Wrap the already-installed timing probe; do not alter the product solve."""

    upstream_splu = _layer_surface_network.splu

    def observed_splu(matrix: Any, *args: Any, **kwargs: Any) -> _ObservedFactor:
        return _ObservedFactor(upstream_splu(matrix, *args, **kwargs), observer)

    _layer_surface_network.splu = observed_splu
    return upstream_splu


def _matrix_to_snapshot(
    arrays: dict[str, np.ndarray[Any, Any]],
    prefix: str,
    matrix: Any,
) -> tuple[int, int, int]:
    # The source CSC may be part of the immutable compiled network.  Snapshot
    # operations below are allowed to canonicalize only our private copy.
    sparse = csc_matrix(matrix, copy=True)
    sparse.sum_duplicates()
    sparse.sort_indices()
    sparse.eliminate_zeros()
    if sparse.dtype.kind not in {"f", "c"} or not np.all(np.isfinite(sparse.data)):
        raise ValueError(f"{prefix} has invalid sparse data")
    arrays[f"{prefix}_shape"] = np.asarray(sparse.shape, dtype=np.int64)
    arrays[f"{prefix}_indptr"] = np.asarray(sparse.indptr, dtype=np.int64)
    arrays[f"{prefix}_indices"] = np.asarray(sparse.indices, dtype=np.int64)
    arrays[f"{prefix}_data"] = np.asarray(sparse.data, dtype=np.float64)
    return int(sparse.shape[0]), int(sparse.shape[1]), int(sparse.nnz)


def _aligned_finite_metadata(
    capture: _SolveFrameCapture,
) -> tuple[
    tuple[tuple[int, int, Any], ...],
    tuple[tuple[int, int, int, Any], ...],
]:
    full = tuple(
        (int(first), int(second), link)
        for first, second, link in capture.network._finite_links
    )
    expected: list[tuple[int, int, int, Any]] = []
    for original_index, (first_global, second_global, link) in enumerate(full):
        first_active = int(capture.global_to_active[int(first_global)])
        second_active = int(capture.global_to_active[int(second_global)])
        if (first_active >= 0) != (second_active >= 0):
            raise ValueError("finite link crosses the observed active boundary")
        if first_active >= 0:
            expected.append((original_index, first_active, second_active, link))
    if len(expected) != capture.finite_first_active.size:
        raise ValueError("finite-link metadata count differs from solve arrays")
    for index, (_original, first, second, link) in enumerate(expected):
        if (
            int(capture.finite_first_active[index]) != first
            or int(capture.finite_second_active[index]) != second
            or float(capture.finite_count[index]) != float(link.count)
            or float(capture.finite_resistance_ohm[index])
            != float(link.resistance_ohm_per_via)
            or float(capture.finite_inductance_h[index])
            != float(link.inductance_h_per_via)
        ):
            raise ValueError(
                "self._finite_links order does not match numeric finite arrays"
            )
    return full, tuple(expected)


def _partial_snapshot(
    capture: _SolveFrameCapture,
    arrays: dict[str, np.ndarray[Any, Any]],
) -> list[dict[str, Any]]:
    original_partials = tuple(capture.network.partials)
    if len(original_partials) != 36:
        raise ValueError(f"expected 36 original partials; found {len(original_partials)}")
    if (
        len(capture.active_partials) != len(original_partials)
        or capture.dispersion_coefficients_s.size != len(original_partials)
    ):
        raise ValueError("original/active partial inventories differ")
    rows: list[dict[str, Any]] = []
    for index, wrapped in enumerate(original_partials):
        partial = wrapped.partial
        prefix = f"partial_{index:02d}"
        net_names = tuple(str(name) for name in partial.net_names)
        if not net_names:
            raise ValueError(f"{prefix} has no original net names")
        rows_count, columns_count, nnz = _matrix_to_snapshot(
            arrays,
            f"{prefix}_nominal_c",
            partial.maxwell_capacitance_f,
        )
        arrays[f"{prefix}_net_names"] = _text_array(net_names)
        arrays[f"{prefix}_upper_layer"] = _scalar_text(partial.upper_layer)
        arrays[f"{prefix}_lower_layer"] = _scalar_text(partial.lower_layer)
        arrays[f"{prefix}_nominal_relative_permittivity"] = np.asarray(
            [float(partial.nominal_relative_permittivity)],
            dtype=np.float64,
        )
        arrays[f"{prefix}_separation_m"] = np.asarray(
            [
                np.nan
                if partial.separation_m is None
                else float(partial.separation_m)
            ],
            dtype=np.float64,
        )
        rows.append(
            {
                "index": index,
                "npz_prefix": prefix,
                "upper_layer": partial.upper_layer,
                "lower_layer": partial.lower_layer,
                "net_name_count": len(net_names),
                "net_names_sha256": _canonical_sha256(list(net_names)),
                "nominal_c_shape": [rows_count, columns_count],
                "nominal_c_nnz": nnz,
                "nominal_relative_permittivity": float(
                    partial.nominal_relative_permittivity
                ),
                "separation_m": (
                    None
                    if partial.separation_m is None
                    else float(partial.separation_m)
                ),
                "actual_1mhz_dispersion_admittance_scale_s": _complex(
                    complex(capture.dispersion_coefficients_s[index])
                ),
            }
        )
    arrays["partial_actual_1mhz_dispersion_admittance_scale_s"] = np.asarray(
        capture.dispersion_coefficients_s,
        dtype=np.complex128,
    )
    return rows


def _termination_snapshot(
    capture: _SolveFrameCapture,
    voltage: np.ndarray[Any, Any],
    arrays: dict[str, np.ndarray[Any, Any]],
) -> tuple[list[dict[str, Any]], complex]:
    records: list[dict[str, Any]] = []
    cluster_ids: list[str] = []
    owners_json: list[str] = []
    positive: list[int] = []
    negative: list[int] = []
    admittances: list[complex] = []
    currents: list[complex] = []
    contributions: list[complex] = []
    for active_positive, active_negative, stamp in capture.immutable_terminations:
        p = int(active_positive)
        n = int(active_negative)
        if (
            p < 0
            or n < 0
            or p >= voltage.size
            or n >= voltage.size
            or p == n
        ):
            raise ValueError("termination stamp has invalid active endpoints")
        admittance_values = np.asarray(stamp.admittance_s, dtype=np.complex128)
        if admittance_values.shape != (1,):
            raise ValueError(
                f"termination {stamp.cluster_id!r} is not one-frequency"
            )
        admittance = complex(admittance_values[0])
        if not np.isfinite(admittance.real) or not np.isfinite(admittance.imag):
            raise ValueError(f"termination {stamp.cluster_id!r} has non-finite Y")
        delta_v = complex(voltage[p] - voltage[n])
        current = admittance * delta_v
        contribution = np.conj(
            np.vdot(
                np.asarray([voltage[p], voltage[n]], dtype=np.complex128),
                np.asarray(
                    [current, -current],
                    dtype=np.complex128,
                ),
            )
        )
        cluster_ids.append(str(stamp.cluster_id))
        owners_json.append(
            json.dumps(tuple(str(owner) for owner in stamp.owner_ids), separators=(",", ":"))
        )
        positive.append(p)
        negative.append(n)
        admittances.append(admittance)
        currents.append(current)
        contributions.append(complex(contribution))
        records.append(
            {
                "cluster_id": str(stamp.cluster_id),
                "owner_ids": tuple(str(owner) for owner in stamp.owner_ids),
                "positive_active_index": p,
                "negative_active_index": n,
            }
        )
    arrays["termination_cluster_ids"] = _text_array(cluster_ids)
    arrays["termination_owner_ids_json"] = _text_array(owners_json)
    arrays["termination_positive_active_indices"] = np.asarray(
        positive, dtype=np.int64
    )
    arrays["termination_negative_active_indices"] = np.asarray(
        negative, dtype=np.int64
    )
    arrays["termination_admittance_s"] = np.asarray(admittances, dtype=np.complex128)
    arrays["termination_current_a_positive_to_negative"] = np.asarray(
        currents, dtype=np.complex128
    )
    arrays["termination_complex_contribution_ohm_at_1a"] = np.asarray(
        contributions, dtype=np.complex128
    )
    return records, sum(contributions, 0.0j)


def _numeric_capture_checkpoint_arrays(
    capture: _SolveFrameCapture,
) -> dict[str, np.ndarray[Any, Any]]:
    """Return the non-text frame needed to recover voltages after later failure.

    This deliberately precedes any node-id, owner-id, or partial serialization.
    It makes a successful native factor result durable even if rich provenance
    capture later encounters malformed or unexpectedly large text.
    """

    if not _close_complex(
        complex(capture.frequency_hz),
        complex(FREQUENCY_HZ),
        rtol=0.0,
        atol=0.0,
    ):
        raise ValueError("observer captured an unexpected frequency")
    if capture.active_voltage.shape[1] != 1 or capture.scaled_solution.shape[1] != 1:
        raise ValueError("observer captured a multi-column field")
    _validate_unscaled_solution(
        capture.row_scale,
        capture.scaled_solution,
        capture.unscaled_solution,
    )
    if not np.array_equal(
        capture.active_voltage[capture.retained_active_nodes, :],
        capture.unscaled_solution,
    ):
        raise ValueError("active voltage does not contain the retained solution")
    surface_to_reduced = np.asarray(
        capture.network._surface_to_reduced,
        dtype=np.int64,
    )
    if (
        surface_to_reduced.ndim != 1
        or np.any(surface_to_reduced < 0)
        or np.any(surface_to_reduced >= capture.global_to_active.size)
        or capture.gauge_active_node < 0
        or capture.gauge_active_node >= capture.active_global_nodes.size
    ):
        raise ValueError("numeric capture mapping is invalid")
    return {
        "numeric_checkpoint_schema_version": np.asarray([1], dtype=np.int64),
        "frequency_hz": np.asarray([FREQUENCY_HZ], dtype=np.float64),
        "active_global_reduced_indices": np.asarray(
            capture.active_global_nodes,
            dtype=np.int64,
        ),
        "global_to_active_indices": np.asarray(
            capture.global_to_active,
            dtype=np.int64,
        ),
        "retained_active_indices": np.asarray(
            capture.retained_active_nodes,
            dtype=np.int64,
        ),
        "retained_global_reduced_indices": np.asarray(
            capture.active_global_nodes[capture.retained_active_nodes],
            dtype=np.int64,
        ),
        "gauge_active_index": np.asarray([capture.gauge_active_node], dtype=np.int64),
        "gauge_global_reduced_index": np.asarray(
            [capture.active_global_nodes[capture.gauge_active_node]],
            dtype=np.int64,
        ),
        "row_scale": np.asarray(capture.row_scale, dtype=np.float64),
        "scaled_rhs": np.asarray(capture.scaled_rhs, dtype=np.complex128),
        "scaled_solution": np.asarray(capture.scaled_solution, dtype=np.complex128),
        "unscaled_solution_retained": np.asarray(
            capture.unscaled_solution,
            dtype=np.complex128,
        ),
        "active_voltage": np.asarray(capture.active_voltage, dtype=np.complex128),
        "batch_port_indices": np.asarray(capture.batch_port_indices, dtype=np.int64),
        "solve_port_reduced_nodes": np.asarray(
            capture.solve_port_reduced_nodes,
            dtype=np.int64,
        ),
        "surface_to_reduced_indices": np.asarray(surface_to_reduced, dtype=np.int64),
    }


def _raw_snapshot(
    capture: _SolveFrameCapture,
    *,
    observer_driver_sha256: str,
    loaded_driver_sha256: str,
    base_driver_sha256: str,
) -> tuple[dict[str, Any], dict[str, np.ndarray[Any, Any]]]:
    """Persist the captured source frame before any response/power promotion."""

    if not _close_complex(
        complex(capture.frequency_hz),
        complex(FREQUENCY_HZ),
        rtol=0.0,
        atol=0.0,
    ):
        raise ValueError("observer captured an unexpected frequency")
    if len(capture.batch_port_ids) != 1:
        raise ValueError("observer did not capture exactly one port batch")
    if capture.active_voltage.shape[1] != 1:
        raise ValueError("observer captured a multi-column field")
    _validate_unscaled_solution(
        capture.row_scale,
        capture.scaled_solution,
        capture.unscaled_solution,
    )
    if not np.array_equal(
        capture.active_voltage[capture.retained_active_nodes, :],
        capture.unscaled_solution,
    ):
        raise ValueError("active voltage does not contain the retained solution")

    active_node_ids = tuple(
        str(capture.network._reduced_node_ids[int(global_index)])
        for global_index in capture.active_global_nodes
    )
    all_reduced_node_ids = tuple(
        str(value) for value in capture.network._reduced_node_ids
    )
    surface_node_ids = tuple(str(value) for value in capture.network.surface_node_ids)
    surface_to_reduced = np.asarray(
        capture.network._surface_to_reduced,
        dtype=np.int64,
    )
    if (
        len(active_node_ids) != capture.active_global_nodes.size
        or len(all_reduced_node_ids) != capture.global_to_active.size
        or len(surface_node_ids) != surface_to_reduced.size
        or np.any(surface_to_reduced < 0)
        or np.any(surface_to_reduced >= len(all_reduced_node_ids))
    ):
        raise ValueError("source physical/reduced mapping is invalid")

    arrays: dict[str, np.ndarray[Any, Any]] = {
        "raw_snapshot_status": _scalar_text(RAW_SNAPSHOT_STATUS),
        "frequency_hz": np.asarray([FREQUENCY_HZ], dtype=np.float64),
        "observer_driver_sha256": _scalar_text(observer_driver_sha256),
        "loaded_driver_sha256": _scalar_text(loaded_driver_sha256),
        "base_driver_sha256": _scalar_text(base_driver_sha256),
        "active_global_reduced_indices": np.asarray(
            capture.active_global_nodes,
            dtype=np.int64,
        ),
        "global_to_active_indices": np.asarray(
            capture.global_to_active,
            dtype=np.int64,
        ),
        "active_reduced_node_ids": _text_array(active_node_ids),
        "all_reduced_node_ids": _text_array(all_reduced_node_ids),
        "surface_node_ids": _text_array(surface_node_ids),
        "surface_to_reduced_indices": np.asarray(
            surface_to_reduced,
            dtype=np.int64,
        ),
        "retained_active_indices": np.asarray(
            capture.retained_active_nodes,
            dtype=np.int64,
        ),
        "retained_global_reduced_indices": np.asarray(
            capture.active_global_nodes[capture.retained_active_nodes],
            dtype=np.int64,
        ),
        "gauge_active_index": np.asarray(
            [capture.gauge_active_node],
            dtype=np.int64,
        ),
        "gauge_global_reduced_index": np.asarray(
            [capture.active_global_nodes[capture.gauge_active_node]],
            dtype=np.int64,
        ),
        "gauge_reduced_node_id": _scalar_text(
            active_node_ids[capture.gauge_active_node]
        ),
        "row_scale": np.asarray(capture.row_scale, dtype=np.float64),
        "scaled_rhs": np.asarray(capture.scaled_rhs, dtype=np.complex128),
        "scaled_solution": np.asarray(capture.scaled_solution, dtype=np.complex128),
        "unscaled_solution_retained": np.asarray(
            capture.unscaled_solution,
            dtype=np.complex128,
        ),
        "active_voltage": np.asarray(capture.active_voltage, dtype=np.complex128),
        "batch_port_indices": np.asarray(
            capture.batch_port_indices,
            dtype=np.int64,
        ),
        "batch_port_ids": _text_array(capture.batch_port_ids),
        "solve_port_reduced_nodes": np.asarray(
            capture.solve_port_reduced_nodes,
            dtype=np.int64,
        ),
    }
    partial_rows = _partial_snapshot(capture, arrays)
    all_finite_records, finite_records = _aligned_finite_metadata(capture)
    arrays["finite_active_original_indices"] = np.asarray(
        tuple(item[0] for item in finite_records),
        dtype=np.int64,
    )
    arrays["finite_first_active_indices"] = np.asarray(
        capture.finite_first_active,
        dtype=np.int64,
    )
    arrays["finite_second_active_indices"] = np.asarray(
        capture.finite_second_active,
        dtype=np.int64,
    )
    arrays["finite_count"] = np.asarray(capture.finite_count, dtype=np.float64)
    arrays["finite_resistance_ohm_per_via"] = np.asarray(
        capture.finite_resistance_ohm,
        dtype=np.float64,
    )
    arrays["finite_inductance_h_per_via"] = np.asarray(
        capture.finite_inductance_h,
        dtype=np.float64,
    )
    # Preserve the original compiled finite-link order as well as the active
    # subset whose numerical arrays the solver actually stamped.  The direct
    # comparison in _aligned_finite_metadata above proves their correspondence
    # without assuming any source-SQL row is a voltage-vector index.
    arrays["all_finite_link_ids"] = _text_array(
        tuple(item[2].link_id for item in all_finite_records)
    )
    arrays["all_finite_link_owner_ids_json"] = _text_array(
        tuple(
            json.dumps(tuple(str(owner) for owner in item[2].owner_ids), separators=(",", ":"))
            for item in all_finite_records
        )
    )
    arrays["all_finite_first_global_reduced_indices"] = np.asarray(
        tuple(item[0] for item in all_finite_records),
        dtype=np.int64,
    )
    arrays["all_finite_second_global_reduced_indices"] = np.asarray(
        tuple(item[1] for item in all_finite_records),
        dtype=np.int64,
    )
    arrays["all_finite_first_node_ids"] = _text_array(
        tuple(item[2].first_node_id for item in all_finite_records)
    )
    arrays["all_finite_second_node_ids"] = _text_array(
        tuple(item[2].second_node_id for item in all_finite_records)
    )
    arrays["all_finite_count"] = np.asarray(
        tuple(int(item[2].count) for item in all_finite_records),
        dtype=np.int64,
    )
    arrays["all_finite_resistance_ohm_per_via"] = np.asarray(
        tuple(float(item[2].resistance_ohm_per_via) for item in all_finite_records),
        dtype=np.float64,
    )
    arrays["all_finite_inductance_h_per_via"] = np.asarray(
        tuple(float(item[2].inductance_h_per_via) for item in all_finite_records),
        dtype=np.float64,
    )
    _add_text_encoding_metadata(arrays)
    raw = {
        "raw_snapshot_status": RAW_SNAPSHOT_STATUS,
        "text_vector_encoding": TEXT_VECTOR_ENCODING,
        "text_scalar_encoding": TEXT_SCALAR_ENCODING,
        "frequency_hz": FREQUENCY_HZ,
        "active_reduced_node_count": int(capture.active_global_nodes.size),
        "total_reduced_node_count": int(capture.global_to_active.size),
        "pruned_reduced_node_count": int(
            capture.global_to_active.size - capture.active_global_nodes.size
        ),
        "gauge": {
            "active_index": capture.gauge_active_node,
            "global_reduced_index": int(
                capture.active_global_nodes[capture.gauge_active_node]
            ),
            "node_id": active_node_ids[capture.gauge_active_node],
            "voltage_is_exact_zero": bool(
                np.array_equal(
                    capture.active_voltage[capture.gauge_active_node, :],
                    np.zeros(1, dtype=np.complex128),
                )
            ),
        },
        "capture_counts": {
            "factor_solve_frame_count": 1,
            "batch_port_count": len(capture.batch_port_ids),
            "original_partial_count": len(partial_rows),
            "finite_parallel_rl_count": len(finite_records),
            "original_finite_parallel_rl_count": len(all_finite_records),
        },
        "partial_snapshot": partial_rows,
        "finite_metadata_order_verified_against_numeric_arrays": True,
        "no_assembled_global_matrix_saved": True,
        "mapping_rule": (
            "active_global_nodes indexes self._reduced_node_ids; no source SQLite "
            "flat/ordinal value is interpreted as a voltage-vector offset"
        ),
    }
    return raw, arrays


def _field_snapshot(
    capture: _SolveFrameCapture,
    *,
    device_port_id: str,
    zdd_ohm: complex,
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, np.ndarray[Any, Any]]]:
    if len(capture.batch_port_ids) != 1 or capture.batch_port_ids[0] != device_port_id:
        raise ValueError("observer did not capture exactly the Device port batch")
    device_port_index = int(capture.batch_port_indices[0])
    positive_global, negative_global = capture.solve_port_reduced_nodes[
        device_port_index
    ]
    positive_active = int(capture.global_to_active[positive_global])
    negative_active = int(capture.global_to_active[negative_global])
    if positive_active < 0 or negative_active < 0:
        raise ValueError("Device port was pruned from the captured field")
    voltage = np.asarray(capture.active_voltage[:, 0], dtype=np.complex128)
    _assert_finite_complex(voltage, "active voltage")
    port_voltage = complex(voltage[positive_active] - voltage[negative_active])
    if not _close_complex(
        port_voltage,
        zdd_ohm,
        rtol=REFERENCE_ZDD_REL_TOL,
        atol=REFERENCE_ZDD_ABS_TOL,
    ):
        raise ValueError("field Device voltage does not recover solved Zdd")

    arrays: dict[str, np.ndarray[Any, Any]] = {}
    partial_contributions: list[complex] = []
    for coefficient, partial in zip(
        capture.dispersion_coefficients_s,
        capture.active_partials,
        strict=True,
    ):
        active_matrix = csc_matrix(partial, copy=False)
        if active_matrix.shape != (voltage.size, voltage.size):
            raise ValueError("active partial shape differs from active field")
        current = complex(coefficient) * (active_matrix @ voltage)
        partial_contributions.append(complex(np.conj(np.vdot(voltage, current))))
    arrays["partial_complex_contribution_ohm_at_1a"] = np.asarray(
        partial_contributions,
        dtype=np.complex128,
    )
    partial_total = sum(partial_contributions, 0.0j)

    _all_finite_records, finite_records = _aligned_finite_metadata(capture)
    omega = 2.0 * np.pi * FREQUENCY_HZ
    finite_admittance = capture.finite_count / (
        capture.finite_resistance_ohm + 1j * omega * capture.finite_inductance_h
    )
    _assert_finite_complex(finite_admittance, "finite Via admittance")
    finite_delta_v = (
        voltage[capture.finite_first_active]
        - voltage[capture.finite_second_active]
    )
    finite_current = finite_admittance * finite_delta_v
    finite_contributions = np.conj(finite_admittance) * np.abs(finite_delta_v) ** 2
    arrays["finite_admittance_s"] = np.asarray(finite_admittance, dtype=np.complex128)
    arrays["finite_delta_v"] = np.asarray(finite_delta_v, dtype=np.complex128)
    arrays["finite_current_a_positive_to_negative"] = np.asarray(
        finite_current,
        dtype=np.complex128,
    )
    arrays["finite_complex_contribution_ohm_at_1a"] = np.asarray(
        finite_contributions,
        dtype=np.complex128,
    )
    finite_total = complex(np.sum(finite_contributions))

    termination_records, termination_total = _termination_snapshot(
        capture,
        voltage,
        arrays,
    )
    total = partial_total + finite_total + termination_total
    total_error = total - zdd_ohm
    total_tolerance = max(FIELD_POWER_ABS_TOL, FIELD_POWER_REL_TOL * abs(zdd_ohm))
    if abs(total_error) > total_tolerance:
        raise ValueError(
            "field power does not recover Device Zdd: "
            f"error={abs(total_error):.6e}, tolerance={total_tolerance:.6e}"
        )
    drive = np.zeros(voltage.size, dtype=np.complex128)
    drive[positive_active] = 1.0
    drive[negative_active] = -1.0
    source_contribution = complex(np.conj(np.vdot(voltage, drive)))
    if not _close_complex(
        source_contribution,
        zdd_ohm,
        rtol=REFERENCE_ZDD_REL_TOL,
        atol=REFERENCE_ZDD_ABS_TOL,
    ):
        raise ValueError("one-amp source contribution does not recover Device Zdd")

    field = {
        "frequency_hz": FREQUENCY_HZ,
        "text_vector_encoding": TEXT_VECTOR_ENCODING,
        "text_scalar_encoding": TEXT_SCALAR_ENCODING,
        "device_port": {
            "port_id": device_port_id,
            "positive_global_reduced_index": positive_global,
            "negative_global_reduced_index": negative_global,
            "positive_active_index": positive_active,
            "negative_active_index": negative_active,
            "voltage_ohm_at_1a": _complex(port_voltage),
        },
        "capture_counts": {
            **dict(raw["capture_counts"]),
            "active_termination_stamp_count": len(termination_records),
        },
        "field_power": {
            "formula": "conj(vH Y_k v) at the native one-amp Device drive",
            "partial_complex_contribution_ohm_at_1a": _complex(partial_total),
            "finite_parallel_rl_complex_contribution_ohm_at_1a": _complex(
                finite_total
            ),
            "termination_complex_contribution_ohm_at_1a": _complex(
                termination_total
            ),
            "sum_complex_contribution_ohm_at_1a": _complex(total),
            "device_zdd_ohm": _complex(zdd_ohm),
            "sum_minus_device_zdd_ohm": _complex(total_error),
            "absolute_error_ohm": float(abs(total_error)),
            "tolerance_ohm": float(total_tolerance),
            "pass": True,
            "finite_current_formula": "I = y * (V_first - V_second)",
        },
        "finite_active_order_count": len(finite_records),
        "no_assembled_global_matrix_saved": True,
    }
    _add_text_encoding_metadata(arrays)
    return field, arrays


def _write_snapshot(
    path: Path,
    arrays: Mapping[str, np.ndarray[Any, Any]],
    *,
    required: Sequence[str],
) -> dict[str, Any]:
    if path.exists():
        raise ValueError(f"snapshot path already exists: {path}")
    if not arrays:
        raise ValueError("snapshot array inventory is empty")
    if any(
        value.dtype.hasobject or value.dtype.kind == "U"
        for value in arrays.values()
    ):
        raise ValueError("snapshot contains an object or max-width Unicode array")
    np.savez_compressed(path, **dict(arrays))
    with np.load(path, allow_pickle=False) as archive:
        keys = tuple(sorted(archive.files))
        if set(keys) != set(arrays):
            raise ValueError("snapshot key inventory differs after non-pickle reload")
        if any(
            archive[key].dtype.hasobject or archive[key].dtype.kind == "U"
            for key in keys
        ):
            raise ValueError(
                "non-pickle snapshot reload exposed an object or Unicode array"
            )
        if any(key not in archive for key in required):
            raise ValueError("snapshot misses one or more required field arrays")
        has_text_metadata = "text_encoding_schema_version" in archive
        if has_text_metadata:
            expected_schema = np.asarray([TEXT_ENCODING_SCHEMA_VERSION], dtype=np.int64)
            if not np.array_equal(
                archive["text_encoding_schema_version"], expected_schema
            ):
                raise ValueError("snapshot text-encoding schema version differs")
            if (
                archive["text_vector_encoding_tag_utf8"].tobytes().decode("utf-8")
                != TEXT_VECTOR_ENCODING
            ):
                raise ValueError("snapshot text-vector encoding tag differs")
            if (
                archive["text_scalar_encoding_tag_utf8"].tobytes().decode("utf-8")
                != TEXT_SCALAR_ENCODING
            ):
                raise ValueError("snapshot text-scalar encoding tag differs")
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "array_count": len(arrays),
        "allow_pickle_required": False,
        "text_vector_encoding": (
            TEXT_VECTOR_ENCODING if "text_encoding_schema_version" in arrays else None
        ),
        "text_scalar_encoding": (
            TEXT_SCALAR_ENCODING if "text_encoding_schema_version" in arrays else None
        ),
    }


def _verify_loaded_driver_pins() -> tuple[str, str]:
    loaded_hash = _sha256_file(Path(loaded.__file__).resolve())
    base_hash = _sha256_file(Path(base.__file__).resolve())
    if loaded_hash != EXPECTED_LOADED_DRIVER_SHA256:
        raise ValueError("pinned loaded development driver has changed")
    if base_hash != EXPECTED_BASE_DRIVER_SHA256:
        raise ValueError("pinned base 2-port driver has changed")
    if loaded.EXPECTED_BASE_DRIVER_SHA256 != EXPECTED_BASE_DRIVER_SHA256:
        raise ValueError("loaded driver no longer pins the expected base driver")
    if tuple(loaded.FREQUENCIES_HZ) != EXPECTED_ORIGINAL_FREQUENCIES_HZ:
        raise ValueError("loaded driver frequency grid differs from its pinned basis")
    return loaded_hash, base_hash


def _required_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _receipt_complex(value: object, label: str) -> complex:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(item, (int, float)) for item in value)
    ):
        raise ValueError(f"{label} must be a [real, imag] numeric pair")
    result = complex(float(value[0]), float(value[1]))
    if not np.isfinite(result.real) or not np.isfinite(result.imag):
        raise ValueError(f"{label} must be finite")
    return result


def _load_reference_receipt(path: Path) -> dict[str, Any]:
    receipt_bytes = path.read_bytes()
    receipt_sha256 = sha256(receipt_bytes).hexdigest()
    if receipt_sha256 != EXPECTED_REFERENCE_RECEIPT_SHA256:
        raise ValueError("reference loaded-development receipt differs from pin")
    try:
        receipt = json.loads(receipt_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("reference loaded-development receipt is not JSON") from exc
    root = _required_mapping(receipt, "reference receipt")
    point = _required_mapping(
        _required_mapping(root.get("points"), "reference receipt points").get(
            str(int(FREQUENCY_HZ))
        ),
        "reference 1 MHz point",
    )
    population = _required_mapping(
        root.get("scenario_population"),
        "reference scenario population",
    )
    mounted_by_rail = _required_mapping(
        population.get("enabled_source_mounted_by_rail"),
        "reference source-mounted rail population",
    )
    identities = _required_mapping(root.get("identities"), "reference identities")
    device_port = _required_mapping(root.get("device_port"), "reference Device port")
    if (
        root.get("status")
        != "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVELOPMENT_RAIL_FOUR_POINT"
        or root.get("rail_id") != TARGET_RAIL_ID
        or root.get("state") != "source_mounted_scenario_manifest"
        or root.get("termination_cluster_count") != EXPECTED_TERMINATION_CLUSTER_COUNT
        or mounted_by_rail.get(TARGET_RAIL_ID) != EXPECTED_SOURCE_MOUNTED_COUNT
        or tuple(root.get("frequencies_hz", ()))
        != EXPECTED_ORIGINAL_FREQUENCIES_HZ
        or point.get("status") != "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVICE_POINT"
        or float(point.get("frequency_hz", 0.0)) != FREQUENCY_HZ
        or identities.get("driver_code_sha256") != EXPECTED_LOADED_DRIVER_SHA256
        or identities.get("base_driver_sha256") != EXPECTED_BASE_DRIVER_SHA256
        or device_port.get("port_id") != TARGET_RAIL_ID
    ):
        raise ValueError("reference receipt does not describe the pinned VTRIP case")
    return {
        "path": str(path),
        "sha256": receipt_sha256,
        "size_bytes": len(receipt_bytes),
        "state": str(root["state"]),
        "rail_id": str(root["rail_id"]),
        "source_mounted_count": int(mounted_by_rail[TARGET_RAIL_ID]),
        "termination_cluster_count": int(root["termination_cluster_count"]),
        "zdd_ohm": _receipt_complex(
            point.get("device_zdd_ohm"),
            "reference 1 MHz Device Zdd",
        ),
        "device_port": {
            "port_id": str(device_port["port_id"]),
            "positive_node_id": str(device_port["positive_node_id"]),
            "negative_node_id": str(device_port["negative_node_id"]),
        },
        "scenario_sha256": str(
            _required_mapping(root.get("bundle"), "reference bundle")[
                "scenario_sha256"
            ]
        ),
        "source_sha256": str(
            _required_mapping(root.get("bundle"), "reference bundle")[
                "source_sha256"
            ]
        ),
        "loaded_scenario_identity_sha256": str(
            identities["loaded_scenario_identity_sha256"]
        ),
        "termination_manifest_sha256": str(
            identities["termination_manifest_sha256"]
        ),
    }


def run(
    args: argparse.Namespace,
    budget: base._Budget,
    observer: _SolveFrameObserver,
) -> dict[str, Any]:
    reference = _load_reference_receipt(args.reference_receipt)
    loaded_driver_sha256, base_driver_sha256 = _verify_loaded_driver_pins()
    observer_driver_sha256 = _sha256_file(Path(__file__).resolve())
    original_frequencies = loaded.FREQUENCIES_HZ
    loaded.FREQUENCIES_HZ = (FREQUENCY_HZ,)
    try:
        underlying = loaded.run(args, budget)
    finally:
        loaded.FREQUENCIES_HZ = original_frequencies

    captures = observer.captures
    if len(captures) != 1:
        raise ValueError(
            f"expected one direct sparse-solve frame; observed {len(captures)}"
        )
    capture = captures[0]
    # This checkpoint intentionally contains no source IDs, owner IDs, or
    # partial metadata.  It is written before those variable-length values are
    # materialized, so a later provenance-snapshot error cannot discard the
    # successful physical voltage frame.
    numeric_checkpoint = _write_snapshot(
        args.output_dir / "numeric-capture-checkpoint.npz",
        _numeric_capture_checkpoint_arrays(capture),
        required=(
            "numeric_checkpoint_schema_version",
            "active_voltage",
            "row_scale",
            "scaled_solution",
            "unscaled_solution_retained",
            "active_global_reduced_indices",
            "global_to_active_indices",
            "retained_active_indices",
            "gauge_active_index",
            "surface_to_reduced_indices",
        ),
    )
    try:
        raw, raw_arrays = _raw_snapshot(
            capture,
            observer_driver_sha256=observer_driver_sha256,
            loaded_driver_sha256=loaded_driver_sha256,
            base_driver_sha256=base_driver_sha256,
        )
        raw_snapshot = _write_snapshot(
            args.output_dir / "raw-field-snapshot.npz",
            raw_arrays,
            required=(
                "raw_snapshot_status",
                "text_encoding_schema_version",
                "text_vector_encoding_tag_utf8",
                "text_scalar_encoding_tag_utf8",
                "active_voltage",
                "active_global_reduced_indices",
                "global_to_active_indices",
                "retained_active_indices",
                "gauge_active_index",
                "active_reduced_node_ids",
                "all_reduced_node_ids",
                "surface_node_ids",
                "surface_to_reduced_indices",
                "partial_actual_1mhz_dispersion_admittance_scale_s",
                "finite_active_original_indices",
                "all_finite_link_ids",
            ),
        )
    except Exception as exc:
        return {
            **dict(underlying),
            "status": "STOP_ACTUAL_SOURCE_MOUNTED_ONE_POINT_FIELD",
            "scope": (
                "ONE_POINT_FIELD numeric solver-frame checkpoint was persisted "
                "before full raw provenance serialization failed."
            ),
            "reference_receipt": {
                **reference,
                "zdd_ohm": _complex(reference["zdd_ohm"]),
            },
            "numeric_capture_checkpoint": numeric_checkpoint,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }

    def stopped_after_raw(exc: Exception) -> dict[str, Any]:
        return {
            **dict(underlying),
            "status": "STOP_ACTUAL_SOURCE_MOUNTED_ONE_POINT_FIELD",
            "scope": (
                "ONE_POINT_FIELD raw observation was persisted before a "
                "reference-response or derived field-power gate failed."
            ),
            "reference_receipt": {
                **reference,
                "zdd_ohm": _complex(reference["zdd_ohm"]),
            },
            "numeric_capture_checkpoint": numeric_checkpoint,
            "raw_snapshot": raw_snapshot,
            "raw_capture": raw,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }

    key = str(int(FREQUENCY_HZ))
    point = underlying.get("points", {}).get(key)
    try:
        if (
            not isinstance(point, Mapping)
            or point.get("status")
            != "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVICE_POINT"
            or underlying.get("frequencies_hz") != [FREQUENCY_HZ]
            or not str(underlying.get("status", "")).startswith("COMPLETED_")
            or underlying.get("rail_id") != reference["rail_id"]
            or underlying.get("state") != reference["state"]
            or underlying.get("termination_cluster_count")
            != reference["termination_cluster_count"]
            or underlying.get("scenario_population", {}).get(
                "enabled_source_mounted_by_rail", {}
            ).get(TARGET_RAIL_ID)
            != reference["source_mounted_count"]
        ):
            raise ValueError("one-point result differs from the pinned VTRIP basis")
        current_device_port = _required_mapping(
            underlying.get("device_port"),
            "one-point Device port",
        )
        if dict(current_device_port) != reference["device_port"]:
            raise ValueError("one-point Device port differs from reference receipt")
        current_bundle = _required_mapping(underlying.get("bundle"), "one-point bundle")
        current_identities = _required_mapping(
            underlying.get("identities"),
            "one-point identities",
        )
        if (
            current_bundle.get("scenario_sha256") != reference["scenario_sha256"]
            or current_bundle.get("source_sha256") != reference["source_sha256"]
            or current_identities.get("loaded_scenario_identity_sha256")
            != reference["loaded_scenario_identity_sha256"]
            or current_identities.get("termination_manifest_sha256")
            != reference["termination_manifest_sha256"]
        ):
            raise ValueError("one-point scenario/source identity differs from receipt")
        zdd = _receipt_complex(point.get("device_zdd_ohm"), "one-point Device Zdd")
        if not _close_complex(
            zdd,
            reference["zdd_ohm"],
            rtol=REFERENCE_ZDD_REL_TOL,
            atol=REFERENCE_ZDD_ABS_TOL,
        ):
            raise ValueError(
                "native 1 MHz Device Zdd does not reproduce reference receipt: "
                f"{zdd!r}"
            )
        device_port_id = str(current_device_port["port_id"])
        field, derived_arrays = _field_snapshot(
            captures[0],
            device_port_id=device_port_id,
            zdd_ohm=zdd,
            raw=raw,
        )
        derived_snapshot = _write_snapshot(
            args.output_dir / "derived-field-observation.npz",
            derived_arrays,
            required=(
                "text_encoding_schema_version",
                "text_vector_encoding_tag_utf8",
                "text_scalar_encoding_tag_utf8",
                "partial_complex_contribution_ohm_at_1a",
                "finite_current_a_positive_to_negative",
                "termination_current_a_positive_to_negative",
            ),
        )
    except Exception as exc:
        return stopped_after_raw(exc)
    return {
        **dict(underlying),
        "status": "COMPLETED_ACTUAL_SOURCE_MOUNTED_ONE_POINT_FIELD",
        "scope": (
            "ONE_POINT_FIELD: actual D115b source-mounted VTRIP 1 MHz native "
            "G-C-R-L solve with observed reduced-node voltage, finite-R/L "
            "currents, evaluated termination currents, and complex-power "
            "partition. No altered product solver, new matrix, parameter fit, "
            "PowerSI comparison, source-bound sheet replacement, or broadband "
            "claim is made."
        ),
        "frequencies_hz": [FREQUENCY_HZ],
        "adapter": {
            "loaded_driver_frequency_override_hz": [FREQUENCY_HZ],
            "original_loaded_driver_frequency_grid_hz": list(
                EXPECTED_ORIGINAL_FREQUENCIES_HZ
            ),
            "observer_driver_sha256": observer_driver_sha256,
            "loaded_driver_sha256": loaded_driver_sha256,
            "base_driver_sha256": base_driver_sha256,
            "product_numerical_guards_passed_before_field_promotion": True,
            "reference_receipt_sha256": reference["sha256"],
            "pinned_1mhz_device_zdd_ohm": _complex(reference["zdd_ohm"]),
            "reproduced_1mhz_device_zdd_ohm": _complex(zdd),
        },
        "reference_receipt": {
            **reference,
            "zdd_ohm": _complex(reference["zdd_ohm"]),
        },
        "numeric_capture_checkpoint": numeric_checkpoint,
        "raw_snapshot": raw_snapshot,
        "raw_capture": raw,
        "derived_field_snapshot": derived_snapshot,
        "field": field,
        "limitations": [
            "The NPZ raw snapshot is retained as UNVALIDATED_RAW_SOLVER_FRAME; the JSON field checks independently bind its active mapping and field power to the returned one-amp Device Zdd.",
            "Active reduced-node IDs come only from active_global_nodes -> self._reduced_node_ids. Source SQLite ordinals are never treated as voltage offsets.",
            "Ideal-topology-collapsed links have no inferred current. Only finite_parallel_rl links use I = y * delta-V.",
            "Original partial net names, layer pairs, nominal-C CSC data, and actual 1 MHz dispersion scales are preserved for later source G/C analysis, but this observer neither replaces nor re-owns any partial.",
            "This one 1 MHz observation does not establish AC convergence, PowerSI agreement, current sharing beyond the compiled model, or source-bound replacement readiness.",
        ],
    }


def _self_test() -> None:
    """Exercise the real solve closure on a tiny native layer-surface fixture."""

    row_scale = np.asarray([2.0, 0.5], dtype=np.float64)
    scaled_solution = np.asarray(
        [[1.0 + 2.0j], [3.0 - 4.0j]],
        dtype=np.complex128,
    )
    physical_solution = row_scale[:, None] * scaled_solution
    _validate_unscaled_solution(row_scale, scaled_solution, physical_solution)
    try:
        _validate_unscaled_solution(row_scale, scaled_solution, scaled_solution)
    except ValueError:
        pass
    else:
        raise AssertionError(
            "a fake scaled voltage unexpectedly passed the observer guard"
        )

    long_text = "x" * 98_831
    text_values = (long_text, *("s" for _ in range(1_024)))
    packed_vector = _text_array(text_values)
    packed_scalar = _scalar_text(long_text)
    total_utf8_bytes = sum(len(value.encode("utf-8")) for value in text_values)
    if (
        packed_vector.dtype != np.dtype(np.uint8)
        or packed_vector.ndim != 1
        or _decode_text_vector(packed_vector) != text_values
        or _decode_scalar_text(packed_scalar) != long_text
    ):
        raise AssertionError("packed UTF-8 text codec does not round-trip")
    # This input has no JSON escapes, so the compact array framing is bounded
    # exactly by payload plus quotes, commas, and brackets.  It specifically
    # rejects NumPy's former max-width-Unicode allocation.
    if packed_vector.nbytes > total_utf8_bytes + 3 * len(text_values) + 2:
        raise AssertionError("packed text storage is not proportional to UTF-8 size")
    if packed_vector.nbytes >= len(long_text.encode("utf-8")) * len(text_values):
        raise AssertionError("packed text storage still scales by maximum string width")
    text_arrays = {
        "long_vector": packed_vector,
        "long_scalar": packed_scalar,
    }
    _add_text_encoding_metadata(text_arrays)
    with TemporaryDirectory() as temporary_directory:
        text_snapshot = Path(temporary_directory) / "packed-text.npz"
        _write_snapshot(
            text_snapshot,
            text_arrays,
            required=(
                "long_vector",
                "long_scalar",
                "text_encoding_schema_version",
            ),
        )
        with np.load(text_snapshot, allow_pickle=False) as archive:
            if (
                _decode_text_vector(archive["long_vector"]) != text_values
                or _decode_scalar_text(archive["long_scalar"]) != long_text
            ):
                raise AssertionError("non-pickle packed text reload does not round-trip")

    nodes = ("toy::P", "toy::VIA", "toy::G")
    capacitance = 2.0e-9
    matrix = np.zeros((3, 3), dtype=np.float64)
    matrix[0, 0] = capacitance
    matrix[2, 2] = capacitance
    matrix[0, 2] = -capacitance
    matrix[2, 0] = -capacitance
    partial = DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer="toy::L1",
            lower_layer="toy::L2",
            nominal_relative_permittivity=4.0,
            net_names=nodes,
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(FREQUENCY_HZ,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )
    network = compile_layer_surface_network(
        nodes,
        partials=(partial,),
        via_links=(
            LayerSurfaceViaLink(
                "toy-via",
                nodes[1],
                nodes[2],
                1,
                "finite_parallel_rl",
                resistance_ohm_per_via=0.02,
                inductance_h_per_via=1.0e-9,
                owner_ids=("toy-via-owner",),
            ),
        ),
        ports=(LayerSurfacePort("toy-device", nodes[0], nodes[1]),),
    )
    observer = _SolveFrameObserver()
    original_splu = _install_solution_observer(observer)
    try:
        result = network.solve([FREQUENCY_HZ])
    finally:
        _layer_surface_network.splu = original_splu
    captures = observer.captures
    if len(captures) != 1:
        raise AssertionError(f"toy solve captured {len(captures)} frames, expected one")
    capture = captures[0]
    if (
        capture.network is not network
        or capture.finite_first_active.size != 1
        or capture.finite_second_active.size != 1
        or capture.active_voltage.shape
        != (capture.active_global_nodes.size, 1)
        or np.array_equal(capture.row_scale, np.ones_like(capture.row_scale))
    ):
        raise AssertionError("toy factor-frame capture omitted physical solve state")
    full_finite, active_finite = _aligned_finite_metadata(capture)
    if (
        len(full_finite) != 1
        or len(active_finite) != 1
        or active_finite[0][3].link_id != "toy-via"
        or active_finite[0][3].owner_ids != ("toy-via-owner",)
    ):
        raise AssertionError("toy finite metadata is not aligned to the solve arrays")
    port_global = capture.solve_port_reduced_nodes[
        int(capture.batch_port_indices[0])
    ]
    port_active = tuple(int(capture.global_to_active[index]) for index in port_global)
    observed_z = complex(
        capture.active_voltage[port_active[0], 0]
        - capture.active_voltage[port_active[1], 0]
    )
    solved_z = 1.0 / complex(
        result.effective_admittance_by_port["toy-device"][0]
    )
    if not _close_complex(observed_z, solved_z, rtol=1.0e-12, atol=1.0e-18):
        raise AssertionError("toy frame voltage does not recover its native port Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--score-contract", type=Path)
    parser.add_argument("--selection-receipt", type=Path)
    parser.add_argument("--reference-receipt", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-runtime-s", type=float, default=1800.0)
    parser.add_argument("--max-rss-gib", type=float, default=24.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        print("scaled-voltage observer self-test passed", flush=True)
        return 0
    required_paths = (
        ("--bundle", args.bundle),
        ("--score-contract", args.score_contract),
        ("--selection-receipt", args.selection_receipt),
        ("--reference-receipt", args.reference_receipt),
        ("--output-dir", args.output_dir),
    )
    missing = [name for name, path in required_paths if path is None]
    if missing:
        parser.error("required without --self-test: " + ", ".join(missing))
    assert args.bundle is not None
    assert args.score_contract is not None
    assert args.selection_receipt is not None
    assert args.reference_receipt is not None
    assert args.output_dir is not None
    args.bundle = args.bundle.resolve()
    args.score_contract = args.score_contract.resolve()
    args.selection_receipt = args.selection_receipt.resolve()
    args.reference_receipt = args.reference_receipt.resolve()
    args.output_dir = args.output_dir.resolve()
    if args.output_dir.exists():
        parser.error("--output-dir must be a new directory")
    if not all(
        path.is_file()
        for path in (
            args.bundle,
            args.score_contract,
            args.selection_receipt,
            args.reference_receipt,
        )
    ):
        parser.error("bundle, score-contract, and selection-receipt must be files")
    if args.max_runtime_s <= 0.0 or args.max_runtime_s > 1800.0:
        parser.error("--max-runtime-s must be in (0, 1800]")
    if args.max_rss_gib <= 0.0 or args.max_rss_gib > 24.0:
        parser.error("--max-rss-gib must be in (0, 24]")
    args.output_dir.mkdir(parents=True)
    args.run_dir = args.output_dir / "checkpoints"
    args.run_dir.mkdir()
    args.output = args.output_dir / "loaded-adapter-unused.json"

    print(
        f"{base.APP_DISPLAY_NAME} - Astra native loaded one-point voltage field",
        flush=True,
    )
    budget = base._Budget(
        args.max_runtime_s,
        int(args.max_rss_gib * 2**30),
        args.run_dir / "progress.jsonl",
    )
    budget.emit("start", pid=base.os.getpid(), python=sys.version)
    watchdog = _start_shutdown_safe_watchdog(budget)
    observer = _SolveFrameObserver()
    original_splu = base._install_factor_probe(budget)
    _install_solution_observer(observer)
    try:
        result = run(args, budget, observer)
    except BaseException as exc:
        result = {
            "program": base.APP_DISPLAY_NAME,
            "version": base.__version__,
            "status": "STOP_ACTUAL_SOURCE_MOUNTED_ONE_POINT_FIELD",
            "error": {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(exc),
            },
            "resource": {
                "elapsed_s": budget.elapsed(),
                "peak_working_set_bytes": budget.peak_working_set,
                "peak_private_bytes": budget.peak_private,
                "max_runtime_s": budget.runtime_s,
                "max_rss_bytes": budget.rss_bytes,
            },
        }
        budget.emit("stopped", error_type=type(exc).__name__, message=str(exc))
    finally:
        # Signal and join before any final publication.  The wrapper above
        # distinguishes this intentional stop from a real budget violation.
        budget.stop.set()
        watchdog.join(timeout=5.0)
        _layer_surface_network.splu = original_splu
        base.clear_layerwise_scenario_binding_cache()
        base.clear_layerwise_substrate_cache()
    output = args.output_dir / "field.json"
    _write_json(output, result)
    print(f"{result['status']} -> {output}", flush=True)
    return 0 if result["status"].startswith("COMPLETED_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
