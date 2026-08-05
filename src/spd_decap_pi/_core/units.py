"""Strict unit parsing helpers used by all tabular importers."""

from __future__ import annotations

import re
from math import isfinite
from numbers import Real

from .domain import CoordinateUnit


class UnitParseError(ValueError):
    """Raised when a value cannot be converted without guessing its unit."""


_NUMBER_AND_UNIT = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s]+)?\s*$"
)

_UNIT_ALIASES: dict[str, str] = {
    "um": "um",
    "µm": "um",
    "μm": "um",
    "micron": "um",
    "microns": "um",
    "micrometer": "um",
    "micrometers": "um",
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "cm": "cm",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "mil": "mil",
    "mils": "mil",
    "thou": "mil",
    "in": "in",
    "inch": "in",
    "inches": "in",
    '"': "in",
}

_TO_UM: dict[str, float] = {
    "um": 1.0,
    "mm": 1.0e3,
    "cm": 1.0e4,
    "m": 1.0e6,
    "mil": 25.4,
    "in": 25_400.0,
}


def normalize_length_unit(unit: str | CoordinateUnit) -> str:
    """Return the canonical unit token used by the conversion table."""

    raw = unit.value if isinstance(unit, CoordinateUnit) else str(unit)
    key = raw.strip().casefold().replace("μ", "µ")
    try:
        return _UNIT_ALIASES[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_TO_UM))
        raise UnitParseError(
            f"unsupported length unit {raw!r}; expected one of {supported}"
        ) from exc


def length_scale_to_um(unit: str | CoordinateUnit) -> float:
    return _TO_UM[normalize_length_unit(unit)]


def parse_length_um(
    value: object,
    *,
    default_unit: str | CoordinateUnit | None = None,
    require_unit: bool = False,
) -> float:
    """Parse a finite length and normalize it to micrometres.

    Numeric spreadsheet cells do not contain a unit.  They are accepted only
    when ``default_unit`` is explicit.  Text cells such as ``"35 um"`` and
    ``"0.035 mm"`` carry their own unit and therefore need no default.
    """

    if isinstance(value, bool) or value is None:
        raise UnitParseError(f"length value {value!r} is not numeric")

    number: float
    explicit_unit: str | None
    if isinstance(value, Real):
        number = float(value)
        explicit_unit = None
    else:
        match = _NUMBER_AND_UNIT.fullmatch(str(value))
        if match is None:
            raise UnitParseError(f"invalid length value {value!r}")
        number = float(match.group(1))
        explicit_unit = match.group(2)

    if not isfinite(number):
        raise UnitParseError("length must be finite")
    if explicit_unit is None:
        if require_unit:
            raise UnitParseError(f"length {value!r} must include its unit")
        if default_unit is None:
            raise UnitParseError(
                f"length {value!r} has no unit and no default_unit was supplied"
            )
        unit = normalize_length_unit(default_unit)
    else:
        unit = normalize_length_unit(explicit_unit)
    return number * _TO_UM[unit]


def convert_length(
    value: float,
    *,
    from_unit: str | CoordinateUnit,
    to_unit: str | CoordinateUnit,
) -> float:
    if isinstance(value, bool) or not isfinite(float(value)):
        raise UnitParseError("length must be finite")
    source = length_scale_to_um(from_unit)
    destination = length_scale_to_um(to_unit)
    return float(value) * source / destination


def format_length_um(value_um: float, *, precision: int = 6) -> str:
    if not isfinite(value_um):
        raise UnitParseError("length must be finite")
    return f"{value_um:.{precision}g} um"


__all__ = [
    "UnitParseError",
    "convert_length",
    "format_length_um",
    "length_scale_to_um",
    "normalize_length_unit",
    "parse_length_um",
]
