"""Fail-closed Touchstone v1 S-parameter reader for external correlation only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np


class TouchstoneError(ValueError):
    """Raised when an external Touchstone file cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class TouchstoneNetwork:
    frequencies_hz: np.ndarray
    s_parameters: np.ndarray
    reference_ohm: float
    port_mapping: dict[int, str]
    data_format: str


@dataclass(frozen=True, slots=True)
class SToZResult:
    z_parameters: np.ndarray
    condition_numbers: np.ndarray
    relative_residuals: np.ndarray


_PORT_COMMENT = re.compile(r"^\s*Port\[(\d+)]\s*=\s*(.*?)\s*$", re.IGNORECASE)
_SUFFIX = re.compile(r"\.s(\d+)p$", re.IGNORECASE)
_FREQUENCY_SCALE = {"hz": 1.0, "khz": 1.0e3, "mhz": 1.0e6, "ghz": 1.0e9}
# These gates reject a conversion whose forward error is no longer meaningful.
# The 1e-8 condition budget is intentionally far above ordinary high-Z cases:
# it permits cond(I-S) up to about 4.5e7 in float64, while the production S24P
# is about 177.  Residual is checked independently after the solve.
_MAX_FORWARD_ERROR = 1.0e-8
_MAX_RELATIVE_RESIDUAL = 1.0e-10


def _port_count(path: Path) -> int:
    matched = _SUFFIX.search(path.name)
    if matched is None:
        raise TouchstoneError("Touchstone filename must use a .sNp suffix")
    count = int(matched.group(1))
    if count < 1:
        raise TouchstoneError("Touchstone port count must be positive")
    return count


def read_touchstone(path: str | Path) -> TouchstoneNetwork:
    """Read a v1 S-parameter file, preserving its documented ordering rules.

    Touchstone 1.x two-port data retains its legacy S11,S21,S12,S22 ordering.
    N>=3 data uses row-major matrix order as specified by IBIS Touchstone 2.0.
    """

    source = Path(path)
    ports = _port_count(source)
    option: list[str] | None = None
    values: list[float] = []
    mapping: dict[int, str] = {}
    try:
        lines = source.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise TouchstoneError(f"cannot read Touchstone file {source}") from exc
    for raw in lines:
        text, _bang, comment = raw.partition("!")
        if comment:
            matched = _PORT_COMMENT.match(comment)
            if matched:
                number = int(matched.group(1))
                label = matched.group(2).strip()
                if not 1 <= number <= ports or not label or number in mapping:
                    raise TouchstoneError("invalid or duplicate ! Port[n] mapping")
                mapping[number] = label
        text = text.strip()
        if not text:
            continue
        if text.startswith("#"):
            if option is not None:
                raise TouchstoneError("Touchstone file has multiple option lines")
            option = text[1:].split()
            continue
        if text.startswith("["):
            raise TouchstoneError("Touchstone v2 keyword blocks are unsupported")
        try:
            parsed = [float(item.replace("D", "E").replace("d", "e")) for item in text.split()]
        except ValueError as exc:
            raise TouchstoneError("non-numeric Touchstone data record") from exc
        if not parsed or not np.all(np.isfinite(parsed)):
            raise TouchstoneError("Touchstone values must be finite")
        values.extend(parsed)
    if option is None:
        raise TouchstoneError("Touchstone option line is missing")
    normalized = [item.casefold() for item in option]
    if len(normalized) != 5 or normalized[0] not in _FREQUENCY_SCALE or normalized[1] != "s" or normalized[2] not in {"ri", "ma", "db"} or normalized[3] != "r":
        raise TouchstoneError("expected '# <Hz|kHz|MHz|GHz> S <RI|MA|DB> R <scalar>'")
    try:
        z0 = float(option[4])
    except ValueError as exc:
        raise TouchstoneError("Touchstone R must be scalar") from exc
    if not np.isfinite(z0) or z0 <= 0:
        raise TouchstoneError("Touchstone reference R must be finite and positive")
    width = 1 + 2 * ports * ports
    if not values:
        raise TouchstoneError("Touchstone file contains no numeric records")
    if len(values) % width:
        raise TouchstoneError("Touchstone numeric field count does not form complete records")
    rows = np.asarray(values, dtype=np.float64).reshape(-1, width)
    frequency_hz = rows[:, 0] * _FREQUENCY_SCALE[normalized[0]]
    if not np.all(np.isfinite(frequency_hz)) or np.any(frequency_hz < 0) or np.any(np.diff(frequency_hz) <= 0):
        raise TouchstoneError("Touchstone frequencies must be finite, nonnegative, and strictly increasing")
    pair = rows[:, 1:].reshape(-1, ports * ports, 2)
    if normalized[2] == "ri":
        flat = pair[..., 0] + 1j * pair[..., 1]
    else:
        magnitude = pair[..., 0] if normalized[2] == "ma" else 10.0 ** (pair[..., 0] / 20.0)
        flat = magnitude * np.exp(1j * np.deg2rad(pair[..., 1]))
    order = "F" if ports == 2 else "C"
    s = flat.reshape(-1, ports, ports, order=order)
    if not np.all(np.isfinite(s.real)) or not np.all(np.isfinite(s.imag)):
        raise TouchstoneError("Touchstone S parameters must be finite")
    frequency_hz.setflags(write=False); s.setflags(write=False)
    return TouchstoneNetwork(frequency_hz, s, z0, mapping, normalized[2].upper())


def s_to_z(network: TouchstoneNetwork) -> SToZResult:
    """Convert full S matrices to Z without materializing an explicit inverse."""

    s = network.s_parameters
    ports = s.shape[1]
    identity = np.eye(ports, dtype=np.complex128)
    z = np.empty_like(s)
    conditions = np.empty(s.shape[0], dtype=np.float64)
    residuals = np.empty(s.shape[0], dtype=np.float64)
    for index, matrix in enumerate(s):
        right = identity + matrix
        left = identity - matrix
        conditions[index] = np.linalg.cond(left)
        if not np.isfinite(conditions[index]) or conditions[index] * np.finfo(float).eps > _MAX_FORWARD_ERROR:
            raise TouchstoneError(f"S-to-Z conversion is numerically unreliable at record {index}")
        try:
            z[index] = network.reference_ohm * np.linalg.solve(left.T, right.T).T
        except np.linalg.LinAlgError as exc:
            raise TouchstoneError(f"S-to-Z solve failed at record {index}") from exc
        residuals[index] = np.linalg.norm((z[index] / network.reference_ohm) @ left - right) / max(np.linalg.norm(right), np.finfo(float).tiny)
        if residuals[index] > _MAX_RELATIVE_RESIDUAL:
            raise TouchstoneError(f"S-to-Z conversion residual is too large at record {index}")
    if not np.all(np.isfinite(z.real)) or not np.all(np.isfinite(z.imag)) or not np.all(np.isfinite(residuals)):
        raise TouchstoneError("S-to-Z conversion produced non-finite data")
    z.setflags(write=False); conditions.setflags(write=False); residuals.setflags(write=False)
    return SToZResult(z, conditions, residuals)


def open_circuit_zpp(result: SToZResult, port_one_based: int) -> np.ndarray:
    """Return Zpp: selected port driven while all other port currents are zero."""

    if isinstance(port_one_based, bool) or not isinstance(port_one_based, (int, np.integer)):
        raise TouchstoneError("requested port must be an integer")
    index = int(port_one_based) - 1
    if not 0 <= index < result.z_parameters.shape[1]:
        raise TouchstoneError("requested port is outside Touchstone matrix")
    values = result.z_parameters[:, index, index].copy()
    values.setflags(write=False)
    return values


__all__ = ["SToZResult", "TouchstoneError", "TouchstoneNetwork", "open_circuit_zpp", "read_touchstone", "s_to_z"]
