"""Conservative electrical classification for source-derived MLO via segments."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log, pi
from typing import Any, Sequence


COPPER_CONDUCTIVITY_S_PER_M = 5.959e7
SOLID_COPPER_FILLED_MICROVIA = "SOLID_COPPER_FILLED_MICROVIA"
HOLLOW_PLATED_BARREL = "HOLLOW_PLATED_BARREL"
_MICROVIA_MAX_DRILL_UM = 150.0


class ViaModelError(ValueError):
    """Raised when source-proven via dimensions are nonphysical."""


@dataclass(frozen=True, slots=True)
class ViaConductorClassification:
    """The disclosed conductor-area assumption for one vertical via segment."""

    conductor_model: str
    fill_provenance: str
    classification_basis: str
    effective_area_m2: float


@dataclass(frozen=True, slots=True)
class ViaSegmentElectricalModel:
    """Classification and self R/L estimate for one source-derived segment."""

    classification: ViaConductorClassification
    resistance_ohm: float
    inductance_h: float


def _positive_finite(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ViaModelError(f"{name} must be finite and > 0") from exc
    if not isfinite(number) or number <= 0.0:
        raise ViaModelError(f"{name} must be finite and > 0")
    return number


def _stackup_index(stackup_layers: Sequence[Any], layer_name: str | None) -> int | None:
    if not isinstance(layer_name, str) or not layer_name.strip():
        return None
    target = layer_name.casefold()
    matches = [
        index
        for index, layer in enumerate(stackup_layers)
        if str(getattr(layer, "name", "")).casefold() == target
    ]
    return matches[0] if len(matches) == 1 else None


def classify_via_conductor(
    *,
    drill_diameter_um: Any,
    padstack_material: str | None,
    start_layer: str | None,
    end_layer: str | None,
    stackup_layers: Sequence[Any],
) -> ViaConductorClassification:
    """Classify a source segment without guessing unrecorded fabrication data.

    Solid copper is used only for the MLO profile: source ``Material=COPPER``,
    at most 150 um drill, exactly two conductor endpoints with exactly one
    dielectric row between them, and dielectric thickness no greater than the
    drill.  All missing or non-qualifying evidence keeps the legacy plated
    barrel area.
    """

    diameter_um = _positive_finite(drill_diameter_um, name="drill_diameter_um")
    barrel_area_m2 = pi * diameter_um * min(20.0, diameter_um / 4.0) * 1.0e-12
    material_is_copper = isinstance(padstack_material, str) and (
        padstack_material.strip().casefold() == "copper"
    )
    start_index = _stackup_index(stackup_layers, start_layer)
    end_index = _stackup_index(stackup_layers, end_layer)
    if not material_is_copper:
        return ViaConductorClassification(
            HOLLOW_PLATED_BARREL,
            "LEGACY_PLATED_BARREL_FALLBACK",
            "source PadStackDef Material is missing, conflicting, or not COPPER",
            barrel_area_m2,
        )
    if start_index is None or end_index is None:
        return ViaConductorClassification(
            HOLLOW_PLATED_BARREL,
            "LEGACY_PLATED_BARREL_FALLBACK",
            "source material is COPPER but source/stackup endpoint evidence is missing",
            barrel_area_m2,
        )
    lower, upper = sorted((start_index, end_index))
    between = stackup_layers[lower + 1 : upper]
    endpoints = (stackup_layers[lower], stackup_layers[upper])
    topology_matches = (
        len(between) == 1
        and all(bool(getattr(layer, "is_conductor", False)) for layer in endpoints)
        and not bool(getattr(between[0], "is_conductor", False))
    )
    if not topology_matches:
        return ViaConductorClassification(
            HOLLOW_PLATED_BARREL,
            "LEGACY_PLATED_BARREL_FALLBACK",
            "source COPPER padstack does not span exactly two conductors and one dielectric",
            barrel_area_m2,
        )
    dielectric_thickness_um = _positive_finite(
        getattr(between[0], "thickness_um", None), name="dielectric_thickness_um"
    )
    if diameter_um > _MICROVIA_MAX_DRILL_UM or dielectric_thickness_um / diameter_um > 1.0:
        return ViaConductorClassification(
            HOLLOW_PLATED_BARREL,
            "LEGACY_PLATED_BARREL_FALLBACK",
            "source COPPER geometry exceeds the qualified MLO microvia drill/aspect profile",
            barrel_area_m2,
        )
    return ViaConductorClassification(
        SOLID_COPPER_FILLED_MICROVIA,
        "USER_CONFIRMED_MLO_COPPER_FILL_ASSUMPTION_WITH_SOURCE_COPPER_AND_GEOMETRY",
        "COPPER; drill <= 150 um; two conductor layers; one dielectric; dielectric/drill <= 1.0",
        pi * (diameter_um / 2.0) ** 2 * 1.0e-12,
    )


def estimate_via_segment_rl(
    *,
    length_um: Any,
    drill_diameter_um: Any,
    padstack_material: str | None,
    start_layer: str | None,
    end_layer: str | None,
    stackup_layers: Sequence[Any],
) -> ViaSegmentElectricalModel:
    """Return the one-segment R/L estimate under the shared MLO assumptions."""

    length = _positive_finite(length_um, name="length_um")
    diameter = _positive_finite(drill_diameter_um, name="drill_diameter_um")
    classification = classify_via_conductor(
        drill_diameter_um=diameter,
        padstack_material=padstack_material,
        start_layer=start_layer,
        end_layer=end_layer,
        stackup_layers=stackup_layers,
    )
    resistance = length * 1.0e-6 / (
        COPPER_CONDUCTIVITY_S_PER_M * classification.effective_area_m2
    )
    length_for_inductance = max(length, 1.0)
    length_mm = length_for_inductance / 1000.0
    ratio = max(4.0 * length_for_inductance / diameter, 1.0)
    inductance = 0.2 * length_mm * (log(ratio) + 1.0) * 1.0e-9
    if not isfinite(resistance) or not isfinite(inductance):
        raise ViaModelError("source-proven Via segment produced non-finite R/L")
    return ViaSegmentElectricalModel(classification, resistance, inductance)


__all__ = [
    "COPPER_CONDUCTIVITY_S_PER_M",
    "HOLLOW_PLATED_BARREL",
    "SOLID_COPPER_FILLED_MICROVIA",
    "ViaConductorClassification",
    "ViaModelError",
    "ViaSegmentElectricalModel",
    "classify_via_conductor",
    "estimate_via_segment_rl",
]
