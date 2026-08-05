"""Read-only effective PWR/GND plane-pair selection from an SPD stackup."""

from __future__ import annotations

from collections.abc import Iterable

from .domain import MixedReferenceCertificate, PlanePairSuggestion, StackupLayer


def suggest_effective_plane_pairs(
    layers: Iterable[StackupLayer],
    *,
    rail_net: str,
    gnd_aliases: Iterable[str] = ("DGND", "GND"),
    mixed_reference_certificates: Iterable[MixedReferenceCertificate] = (),
) -> list[PlanePairSuggestion]:
    """Rank adjacent PWR/GND conductor pairs by center separation."""

    stack = list(layers)
    if not stack:
        return []
    rail_key = rail_net.casefold()
    gnd_keys = {alias.casefold() for alias in gnd_aliases}
    if not gnd_keys:
        raise ValueError("at least one GND alias is required")
    certificates = {
        (item.rail_net.casefold(), item.pwr_layer.casefold(), item.gnd_layer.casefold()): item
        for item in mixed_reference_certificates
    }

    centers: list[float] = []
    z_um = 0.0
    for layer in stack:
        centers.append(z_um + layer.thickness_um / 2.0)
        z_um += layer.thickness_um

    power_indices = [
        index
        for index, layer in enumerate(stack)
        if layer.is_conductor
        and rail_key in {net.casefold() for net in layer.pwr_nets}
    ]
    suggestions: list[PlanePairSuggestion] = []
    for pwr_index in power_indices:
        for gnd_index, gnd_layer in enumerate(stack):
            if pwr_index == gnd_index:
                continue
            if not gnd_layer.is_conductor:
                continue
            layer_keys = {net.casefold() for net in gnd_layer.pwr_nets}
            pure_ground = bool(layer_keys) and layer_keys.issubset(gnd_keys)
            certificate = certificates.get(
                (rail_key, stack[pwr_index].name.casefold(), gnd_layer.name.casefold())
            )
            if not pure_ground and certificate is None:
                continue
            lower, upper = sorted((pwr_index, gnd_index))
            between = stack[lower + 1 : upper]
            if not between or any(layer.is_conductor for layer in between):
                continue
            if any(layer.dk is None for layer in between):
                continue
            suggestions.append(
                PlanePairSuggestion(
                    rail_net=rail_net,
                    pwr_layer=stack[pwr_index].name,
                    gnd_layer=stack[gnd_index].name,
                    pwr_index=pwr_index,
                    gnd_index=gnd_index,
                    separation_um=abs(centers[pwr_index] - centers[gnd_index]),
                    mixed_reference_certificate=certificate,
                )
            )
    return sorted(
        suggestions,
        key=lambda item: (
            item.separation_um,
            item.pwr_index,
            item.gnd_index,
            item.pwr_layer.casefold(),
            item.gnd_layer.casefold(),
        ),
    )


__all__ = ["suggest_effective_plane_pairs"]
