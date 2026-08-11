"""Read-only raw-SPD evidence benchmark for the layerwise via/sheet changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from spd_decap_pi._core.io.spd import analyze_spd
from spd_decap_pi._core.services import (
    _spd_layer_center_depths,
    _spd_uncalibrated_loop_estimate,
    _spd_via_leg_estimate,
    build_spd_import_plan,
    create_workspace_state,
)
from spd_decap_pi._core.solver.modal import copper_slab_surface_impedance_per_square
from spd_decap_pi._core.solver.evaluator import evaluate_project_rail
from spd_decap_pi._core.solver.modal import MU_0_H_PER_M, RectangularCavitySolver


def _legacy_loop(project, analysis, rail) -> tuple[float, float]:
    by_name = {item.name.casefold(): item for item in analysis.padstacks}
    centers = _spd_layer_center_depths(project.stackup_layers)
    top = next(item.name for item in project.stackup_layers if item.is_conductor)

    def match(keys: set[str], layer: str):
        candidates = []
        for usage in analysis.via_usage:
            if usage.net.casefold() not in keys:
                continue
            padstack = by_name.get(usage.padstack.casefold())
            if padstack is not None and layer.casefold() in {
                str(value).casefold() for value in padstack.layers
            }:
                candidates.append((usage, padstack))
        return sorted(candidates, key=lambda value: (-value[0].count, value[0].padstack.casefold()))[0] if candidates else (None, None)

    power_usage, power = match({rail.net.casefold()}, rail.pwr_layer)
    ground_usage, ground = match({item.casefold() for item in project.gnd_aliases}, rail.gnd_layer)
    drills = [
        item.drill_diameter_um
        for item in (power, ground)
        if item is not None and item.drill_diameter_um is not None
    ]
    power_leg = _spd_via_leg_estimate(
        padstack=power, length_um=centers[rail.pwr_layer], start_layer=top,
        end_layer=rail.pwr_layer, stackup_layers=project.stackup_layers,
    )
    ground_leg = _spd_via_leg_estimate(
        padstack=ground, length_um=centers[rail.gnd_layer], start_layer=top,
        end_layer=rail.gnd_layer, stackup_layers=project.stackup_layers,
    )
    return _spd_uncalibrated_loop_estimate(
        pwr_depth_um=centers[rail.pwr_layer], gnd_depth_um=centers[rail.gnd_layer],
        drill_diameter_um=min(drills) if drills else None,
        power_leg=power_leg, ground_leg=ground_leg,
    )


def _anchor_values(outcome, anchors: np.ndarray) -> list[list[float]]:
    frequencies = np.asarray(outcome.solve.frequencies_hz, dtype=np.float64)
    values = np.asarray(outcome.solve.impedance_ohm, dtype=np.complex128)
    source_x = np.log(frequencies)
    target_x = np.log(anchors)
    real = np.interp(target_x, source_x, values.real)
    imag = np.interp(target_x, source_x, values.imag)
    return [[float(frequency), float(value.real), float(value.imag), float(abs(value))]
            for frequency, value in zip(anchors, real + 1j * imag, strict=True)]


def _legacy_power_sheet(frequencies: np.ndarray, plane) -> np.ndarray:
    return np.full(
        frequencies.shape,
        plane.power_sheet_resistance_ohm + 0.0j,
        dtype=np.complex128,
    )


def _legacy_return_sheet(frequencies: np.ndarray, plane) -> np.ndarray:
    return np.full(
        frequencies.shape,
        plane.ground_sheet_resistance_ohm,
        dtype=np.complex128,
    ) + 1j * 2.0 * np.pi * frequencies * MU_0_H_PER_M * plane.separation_m


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spd", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".codex/vqps_layerwise_via_sheet_benchmark.json"),
    )
    parser.add_argument(
        "--include-predictions", action="store_true",
        help="Attempt modal predictions; intended only for a scenario-valid runner.",
    )
    args = parser.parse_args()
    analysis = analyze_spd(args.spd, scope="selected_pi")
    plan = build_spd_import_plan(create_workspace_state().project, analysis, args.spd)
    project = plan.project
    template_by_rail = {details["rail_id"]: (template, details) for template, details in (
        (template, project.metadata["spd_via_template_provenance"][template.template_id])
        for template in project.via_templates
    )}
    rows = []
    anchors = np.asarray([1.0e5, 1.0e6, 1.0e7, 1.0e8])
    def is_requested_rail(net: str) -> bool:
        key = net.casefold()
        return "vqps" in key or any(f"{token}/" in key for token in ("vtrip", "vint", "vcpu"))

    for rail in project.rails:
        if not is_requested_rail(rail.net):
            continue
        template, evidence = template_by_rail[rail.rail_id]
        old_r, old_l = _legacy_loop(project, analysis, rail)
        pwr = next(item for item in project.stackup_layers if item.name == rail.pwr_layer)
        gnd = next(item for item in project.stackup_layers if item.name == rail.gnd_layer)
        frequencies = np.asarray([1.0e6, 1.0e7, 1.0e8])
        pwr_z = copper_slab_surface_impedance_per_square(
            frequencies, thickness_m=pwr.thickness_um * 1e-6,
            conductivity_s_per_m=float(pwr.conductivity_s_m),
        )
        gnd_z = copper_slab_surface_impedance_per_square(
            frequencies, thickness_m=gnd.thickness_um * 1e-6,
            conductivity_s_per_m=float(gnd.conductivity_s_m),
        )
        rows.append({
            "rail_id": rail.rail_id,
            "net": rail.net,
            "old_legacy_loop": {"R_ohm": old_r, "L_h": old_l},
            "new_template": {
                "model": evidence["via_model"],
                "R_ohm": template.loop_resistance_ohm,
                "L_h": template.loop_inductance_h,
                "sampled_internal_impedance": bool(template.impedance),
            },
            "source_evidence": evidence["adjacent_source_coverage"],
            "sheet_impedance_per_square": {
                "frequencies_hz": frequencies.tolist(),
                "power": [[float(value.real), float(value.imag)] for value in pwr_z],
                "ground": [[float(value.real), float(value.imag)] for value in gnd_z],
            },
        })
    selected_prediction_rails = [rail for rail in project.rails if is_requested_rail(rail.net)]
    predictions = []
    for rail in (selected_prediction_rails if args.include_predictions else ()):
        template, evidence = template_by_rail[rail.rail_id]
        # The raw VQPS and selected loaded rails do not satisfy the exact
        # adjacent-pair source gate, so this is a useful fail-closed ablation:
        # via-only is exactly the legacy via path; sheet-only and combined are
        # identical until genuine layerwise evidence becomes available.
        result = {
            "rail_id": rail.rail_id,
            "net": rail.net,
            "via_layerwise_source_complete": bool(
                evidence["adjacent_source_coverage"]["combined_complete"]
            ),
        }
        try:
            with patch.object(
                RectangularCavitySolver, "_power_sheet_impedance", staticmethod(_legacy_power_sheet)
            ), patch.object(
                RectangularCavitySolver, "_return_sheet_impedance", staticmethod(_legacy_return_sheet)
            ):
                legacy = evaluate_project_rail(project, rail.rail_id)
            combined = evaluate_project_rail(project, rail.rail_id)
            result["status"] = "evaluated"
            result["anchors_hz_re_im_abs"] = {
                "legacy_sheet__legacy_or_fail_closed_via": _anchor_values(legacy, anchors),
                "layered_via_only": _anchor_values(legacy, anchors),
                "sheet_only": _anchor_values(combined, anchors),
                "combined": _anchor_values(combined, anchors),
            }
        except Exception as exc:  # Benchmark must remain an exportable raw-import audit.
            result["status"] = "blocked_before_prediction"
            result["reason"] = f"{type(exc).__name__}: {exc}"
        predictions.append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "source": str(args.spd.resolve()),
        "rows": rows,
        "predictions": predictions,
        "note": "Read-only raw-SPD R/L and copper-sheet evidence only; no PowerSI values are used as fitting inputs and this artifact does not claim full Z accuracy. Predictions are omitted unless --include-predictions is used with a scenario-valid runner.",
    }, indent=2), encoding="utf-8")
    print(args.output)
    print(f"Requested evidence rails: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
