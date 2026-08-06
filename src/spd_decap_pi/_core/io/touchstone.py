"""Fail-closed Touchstone v1 S-parameter reader for external correlation only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

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
_LEGACY_POWER_SI_HEADER_LABEL = re.compile(r"^2nd_SITE([01])-(.+/([01]))$")
_RUN_QUALIFIED_POWER_SI_HEADER_LABEL = re.compile(
    r"^SITE([01])_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*-(.+/([01]))$"
)
_SUFFIX = re.compile(r"\.s(\d+)p$", re.IGNORECASE)
_FREQUENCY_SCALE = {"hz": 1.0, "khz": 1.0e3, "mhz": 1.0e6, "ghz": 1.0e9}
_NUMERIC_FIELD = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[EeDd][+-]?\d+)?"
_NUMERIC_LINE = re.compile(rf"\s*{_NUMERIC_FIELD}(?:\s+{_NUMERIC_FIELD})*\s*\Z")
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


def powersi_header_label_for_rail(rail: str) -> str:
    """Return PowerSI's deterministic site-prefixed label for a scenario rail.

    This is deliberately not a substring matcher.  A scenario rail ending in
    ``/0`` or ``/1`` maps only to ``2nd_SITE0-<rail>`` or
    ``2nd_SITE1-<rail>``, respectively.  Other naming schemes must be passed
    as an explicit expected header-label map to :func:`validate_port_manifest`.
    """

    if not isinstance(rail, str):
        raise TouchstoneError("scenario rail manifest contains an invalid rail label")
    stem, separator, site = rail.rpartition("/")
    if not separator or not stem or site not in {"0", "1"}:
        raise TouchstoneError("PowerSI port-label convention requires a rail ending in /0 or /1")
    return f"2nd_SITE{site}-{rail}"


def powersi_rail_from_header_label(label: str) -> str:
    """Return the exact rail encoded by a supported PowerSI port label.

    PowerSI exports observed in production use either the legacy
    ``2nd_SITE0-<rail>`` spelling or a run-qualified spelling such as
    ``SITE0_0805-<rail>``.  Qualifiers remain part of the exact header evidence;
    this parser only canonicalizes the rail after proving that the prefix site
    matches the rail's terminal ``/0`` or ``/1``.
    """

    if not isinstance(label, str):
        raise TouchstoneError("PowerSI header label must be text")
    matched = _LEGACY_POWER_SI_HEADER_LABEL.fullmatch(label)
    if matched is None:
        matched = _RUN_QUALIFIED_POWER_SI_HEADER_LABEL.fullmatch(label)
    if matched is None:
        raise TouchstoneError("unsupported PowerSI SITE header label")
    header_site, rail, rail_site = matched.groups()
    if header_site != rail_site:
        raise TouchstoneError("PowerSI header SITE does not match the rail terminal")
    return rail


def validate_port_manifest(
    network: TouchstoneNetwork,
    rail_to_port: Mapping[str, int],
    *,
    expected_header_labels: Mapping[str, str] | None = None,
    require_complete_header: bool = False,
) -> dict[int, str]:
    """Validate exact ``rail -> 1-based port`` labels embedded in a file header.

    PowerSI exports use ``! Port[n] = <rail>`` comments.  This guard keeps a
    comparison from silently assigning a scenario rail to the wrong reference
    port.  It intentionally compares labels byte-for-byte after surrounding
    whitespace is removed; aliases and fuzzy net-name matching are unsafe for
    correlation data.

    Unless explicitly overridden by ``expected_header_labels``, the permitted
    translations are PowerSI's exact legacy ``2nd_SITE{0|1}-<rail>`` and exact
    run-qualified ``SITE{0|1}_<run>-<rail>`` conventions. The encoded rail and
    site must match exactly. ``require_complete_header`` additionally requires
    one unique label for every matrix port, which is useful when a full
    scenario manifest is known. The returned copy is ordered by one-based port
    number for report storage.
    """

    ports = network.s_parameters.shape[1]
    header = dict(network.port_mapping)
    if require_complete_header and set(header) != set(range(1, ports + 1)):
        raise TouchstoneError("Touchstone port-label header is incomplete")
    if len(set(header.values())) != len(header):
        raise TouchstoneError("Touchstone port-label header contains duplicate labels")
    overrides = {} if expected_header_labels is None else dict(expected_header_labels)
    unknown_overrides = set(overrides).difference(rail_to_port)
    if unknown_overrides:
        raise TouchstoneError("expected Touchstone port-label map contains a rail outside the scenario manifest")

    seen_ports: set[int] = set()
    for rail, port in rail_to_port.items():
        if not isinstance(rail, str) or not rail:
            raise TouchstoneError("scenario rail manifest contains an invalid rail label")
        if isinstance(port, bool) or not isinstance(port, (int, np.integer)):
            raise TouchstoneError("scenario rail manifest contains a non-integer port")
        number = int(port)
        if not 1 <= number <= ports or number in seen_ports:
            raise TouchstoneError("scenario rail manifest contains an invalid or duplicate port")
        seen_ports.add(number)
        actual = header.get(number)
        if rail in overrides:
            expected = overrides[rail]
            if not isinstance(expected, str) or not expected:
                raise TouchstoneError("expected Touchstone port-label map contains an invalid header label")
            if actual == expected:
                continue
        else:
            try:
                parsed_rail = powersi_rail_from_header_label(actual)  # type: ignore[arg-type]
            except TouchstoneError:
                parsed_rail = None
            if parsed_rail == rail:
                continue
            expected = powersi_header_label_for_rail(rail)
        raise TouchstoneError(
            f"Touchstone port-label mismatch at port {number}: expected {expected!r}, got {actual!r}"
        )
    return dict(sorted(header.items()))


def read_touchstone(path: str | Path) -> TouchstoneNetwork:
    """Read a v1 S-parameter file, preserving its documented ordering rules.

    Touchstone 1.x two-port data retains its legacy S11,S21,S12,S22 ordering.
    N>=3 data uses row-major matrix order as specified by IBIS Touchstone 2.0.
    """

    source = Path(path)
    ports = _port_count(source)
    option: list[str] | None = None
    mapping: dict[int, str] = {}
    rows: np.ndarray | None = None
    record: np.ndarray | None = None
    record_fill = 0
    row_count = 0
    width = 1 + 2 * ports * ports
    try:
        handle = source.open("r", encoding="utf-8", errors="strict", newline=None)
    except (OSError, UnicodeError) as exc:
        raise TouchstoneError(f"cannot read Touchstone file {source}") from exc
    try:
        with handle:
            for raw in handle:
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
                if option is None:
                    raise TouchstoneError("Touchstone option line must precede numeric data")
                # np.fromstring keeps the numeric payload in a contiguous C
                # buffer.  A full lexical check prevents its otherwise silent
                # partial parse from accepting malformed fields.
                if not _NUMERIC_LINE.fullmatch(text):
                    raise TouchstoneError("non-numeric Touchstone data record")
                parsed = np.fromstring(text.replace("D", "E").replace("d", "e"), sep=" ", dtype=np.float64)
                if parsed.size == 0 or not np.all(np.isfinite(parsed)):
                    raise TouchstoneError("Touchstone values must be finite")
                offset = 0
                while offset < parsed.size:
                    if record is None:
                        record = np.empty(width, dtype=np.float64)
                    take = min(width - record_fill, parsed.size - offset)
                    record[record_fill : record_fill + take] = parsed[offset : offset + take]
                    record_fill += take
                    offset += take
                    if record_fill != width:
                        continue
                    if rows is None:
                        rows = np.empty((64, width), dtype=np.float64)
                    elif row_count == rows.shape[0]:
                        grown = np.empty((rows.shape[0] * 2, width), dtype=np.float64)
                        grown[:row_count] = rows
                        rows = grown
                    rows[row_count] = record
                    row_count += 1
                    record = None
                    record_fill = 0
    except (OSError, UnicodeError) as exc:
        raise TouchstoneError(f"cannot read Touchstone file {source}") from exc
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
    if record_fill:
        raise TouchstoneError("Touchstone numeric field count does not form complete records")
    if rows is None or row_count == 0:
        raise TouchstoneError("Touchstone file contains no numeric records")
    rows = rows[:row_count]
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


__all__ = ["SToZResult", "TouchstoneError", "TouchstoneNetwork", "open_circuit_zpp", "powersi_header_label_for_rail", "read_touchstone", "s_to_z", "validate_port_manifest"]
