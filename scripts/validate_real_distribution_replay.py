"""Replay a real De-cap Distribution workbook against its raw PowerSI SPD.

This is deliberately an integration verifier, not another planner.  It imports
the raw source, uses the normal public Distribution APIs, then independently
checks every applied conventional/legacy replay move against a source-classified
physical PWR Via landing and exact retained PWR artwork at its landing coordinate.
The planner's MLO/unknown-legacy structural gate remains authoritative; this
verifier does not turn missing transition evidence into permission.  It does not
gate a destination on GND vias.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
import zlib

from spd_decap_pi.distribution import (
    DistributionDistanceMode,
    DistributionPlan,
    DistributionPlanStatus,
    apply_distribution_plan,
    build_distribution_power_projection,
    compute_distribution_plan,
    distribution_csv_rows,
    distribution_inventory_table,
    distribution_present_counts,
    distribution_target_table,
    validate_distribution_targets,
)
from spd_decap_pi.distribution_workbook import load_distribution_targets
from spd_decap_pi.scenario import ScenarioSpec, derive_shared_pad_current_components
from spd_decap_pi.scenario_io import load_scenario_bundle, save_scenario
from spd_decap_pi.spd_adapter import import_spd_scenario, verify_scenario_source
from spd_decap_pi.spreadsheet_export import write_distribution_workbook


_FINGERPRINT_WARNING = (
    "The source SPD matches, but the design fingerprint differs; targets were "
    "revalidated against the current scenario."
)
_GEOMETRY_FORMAT = "powersi-spd-plane-primitives-v1"


class ReplayValidationError(RuntimeError):
    """The replay or an independent post-apply proof did not validate."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _elapsed_seconds(started: float) -> float:
    return round(max(perf_counter() - started, 0.0), 6)


def _plan_analysis(plan: DistributionPlan) -> dict[str, object]:
    cells = []
    for cell in plan.cells:
        if not any(
            (cell.requested_count, cell.fulfilled_count, cell.shortfall_count)
        ):
            continue
        cells.append(
            {
                "rail_id": cell.rail_id,
                "net": cell.net,
                "model_id": cell.model_id,
                "role": cell.role.value,
                "present_count": cell.present_count,
                "target_count": cell.target_count,
                "actual_count": cell.actual_count,
                "requested_count": cell.requested_count,
                "fulfilled_count": cell.fulfilled_count,
                "shortfall_count": cell.shortfall_count,
                "sent_count": cell.sent_count,
                "received_count": cell.received_count,
                "sacrificed_count": cell.sacrificed_count,
            }
        )
    diagnostics = []
    for item in plan.diagnostics:
        requested = item.requested_count
        actual = item.actual_count
        diagnostics.append(
            {
                "code": item.code,
                "message": item.message,
                "rail_id": item.rail_id,
                "model_id": item.model_id,
                "requested_count": requested,
                "actual_count": actual,
                "shortfall_count": (
                    max(requested - actual, 0)
                    if requested is not None and actual is not None
                    else None
                ),
            }
        )
    return {
        "status": plan.status.value,
        "requested_count": plan.requested_count,
        "fulfilled_count": plan.fulfilled_count,
        "shortfall_count": plan.shortfall_count,
        "move_count": len(plan.moves),
        "isolation_gap_count": len(plan.sacrifices),
        "distance_mode": plan.distance_mode.value,
        "nonzero_cells": cells,
        "diagnostics": diagnostics,
    }


def _model_dump(value: object) -> object:
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value


def has_physical_pwr_landing(landing: object) -> bool:
    """Validate a conventional replay's source-classified PWR-via origin.

    This helper checks geometry only after the planner has admitted the landing.
    It is not a permission oracle: the Distribution structural gate separately
    rejects MLO paths without a translated recipe and legacy landings without
    transition evidence.  An admitted conventional path need not already end on
    the requested target layer, and recovered path coordinates cannot move its
    physical landing XY.
    """

    try:
        x_um = float(getattr(landing, "x_um"))
        y_um = float(getattr(landing, "y_um"))
    except (TypeError, ValueError):
        return False
    if not isfinite(x_um) or not isfinite(y_um):
        return False
    return all(
        bool(str(getattr(landing, name, "")).strip())
        for name in ("via_id", "net", "endpoint_node_id", "padstack")
    )


def _decode_geometry_asset(record: Mapping[str, object], attachments: Mapping[str, bytes]) -> dict[str, object]:
    asset = str(record.get("asset", ""))
    expected_hash = str(record.get("asset_sha256", "")).casefold()
    compressed = attachments.get(asset)
    if not asset or compressed is None or not expected_hash:
        raise ReplayValidationError(f"retained plane asset is missing: {asset or '<unnamed>'}")
    if sha256(compressed).hexdigest() != expected_hash:
        raise ReplayValidationError(f"retained plane asset hash mismatch: {asset}")
    try:
        payload = json.loads(zlib.decompress(compressed).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, zlib.error) as exc:
        raise ReplayValidationError(f"retained plane asset cannot be decoded: {asset}") from exc
    if not isinstance(payload, dict) or payload.get("format") != _GEOMETRY_FORMAT:
        raise ReplayValidationError(f"retained plane asset has unsupported format: {asset}")
    if (
        str(payload.get("layer", "")).casefold() != str(record.get("layer", "")).casefold()
        or str(payload.get("net", "")).casefold() != str(record.get("net", "")).casefold()
    ):
        raise ReplayValidationError(f"retained plane asset identity mismatch: {asset}")
    return payload


def _ordered_artwork_shape(payload: Mapping[str, object]) -> object:
    """Build one ordered PowerSI boolean shape without planner helpers."""

    try:
        from shapely.geometry import Point, Polygon
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - runtime dependency is mandatory
        raise ReplayValidationError("Shapely is required for exact plane-artwork replay") from exc
    raw_collections = {
        "positive_polygon": payload.get("positive_polygons_um"),
        "negative_polygon": payload.get("negative_polygons_um"),
        "positive_circle": payload.get("positive_circles_um"),
        "negative_circle": payload.get("negative_circles_um"),
    }
    order = payload.get("primitive_order")
    if not isinstance(order, list) or any(not isinstance(items, list) for items in raw_collections.values()):
        raise ReplayValidationError("retained plane artwork has malformed primitive collections")
    shape: object | None = None
    runs: list[tuple[bool, list[object]]] = []
    for reference in order:
        if not isinstance(reference, list) or len(reference) != 2:
            raise ReplayValidationError("retained plane artwork has malformed primitive order")
        kind, index = reference
        if not isinstance(kind, str) or not isinstance(index, int) or isinstance(index, bool):
            raise ReplayValidationError("retained plane artwork has malformed primitive reference")
        values = raw_collections.get(kind)
        if values is None or index < 0 or index >= len(values):
            raise ReplayValidationError("retained plane artwork references an unknown primitive")
        raw = values[index]
        try:
            primitive = Polygon(raw) if kind.endswith("polygon") else Point(float(raw[0]), float(raw[1])).buffer(float(raw[2]), quad_segs=64)
        except (IndexError, TypeError, ValueError) as exc:
            raise ReplayValidationError("retained plane artwork contains an invalid primitive") from exc
        if primitive.is_empty or not primitive.is_valid or primitive.area <= 0:
            raise ReplayValidationError("retained plane artwork contains a degenerate primitive")
        positive = kind.startswith("positive_")
        if runs and runs[-1][0] == positive:
            runs[-1][1].append(primitive)
        else:
            runs.append((positive, [primitive]))
    for positive, primitives in runs:
        batch = unary_union(primitives)
        if shape is None:
            if not positive:
                raise ReplayValidationError(
                    "retained plane artwork begins with a negative primitive"
                )
            shape = batch
        else:
            shape = shape.union(batch) if positive else shape.difference(batch)
    if shape is None or shape.is_empty or not shape.is_valid:
        raise ReplayValidationError("retained plane artwork produced no valid final copper")
    return shape


def _shape_strictly_contains(shape: object, x_um: float, y_um: float) -> bool:
    """Match the planner's strict interior and 1e-6 um boundary exclusion."""

    from shapely.geometry import Point

    point = Point(x_um, y_um)
    return bool(
        shape.contains(point)
        and point.distance(shape.boundary) > 1.0e-6
    )


def _ordered_artwork_contains(payload: Mapping[str, object], x_um: float, y_um: float) -> bool:
    """Build and query ordered copper for focused independent tests."""

    return _shape_strictly_contains(_ordered_artwork_shape(payload), x_um, y_um)


def _pwr_landings_for_move(scenario: ScenarioSpec, refdes: str) -> tuple[object, ...]:
    analysis = scenario.connection_analysis
    if analysis is None:
        raise ReplayValidationError("applied scenario has no shared-pad connection analysis")
    key = refdes.casefold()
    connection = next((item for item in analysis.connections.values() if item.refdes.casefold() == key), None)
    if connection is None:
        raise ReplayValidationError(f"move {refdes} has no persisted connection record")
    if connection.cluster_id is None:
        if not connection.power_vias:
            raise ReplayValidationError(f"move {refdes} has no physical PWR Via landing")
        return tuple(connection.power_vias)
    cluster = next((item for item in analysis.clusters if item.cluster_id.casefold() == connection.cluster_id.casefold()), None)
    if cluster is None:
        raise ReplayValidationError(f"move {refdes} references an unknown shared-pad cluster")
    decaps = {item.refdes: item for item in scenario.decaps}
    connections = {item.refdes: item for item in analysis.connections.values()}
    derived = derive_shared_pad_current_components(cluster, decaps, connections, analysis_version=analysis.version)
    component = next((item for item in derived.components if key in {member.casefold() for member in item.member_refdes}), None)
    if component is None or not component.power_vias:
        raise ReplayValidationError(f"move {refdes} is a dummy-only PWR component")
    # Every member of a current-label shared-pad component is served by the
    # component's aggregate physical PWR roots.  Restricting an anchor to only
    # its own Via tuple would contradict the planner's approved ANY-root rule
    # and falsely reject valid anchor-to-anchor current sharing.
    return tuple(component.power_vias)


def _invariant_snapshot(scenario: ScenarioSpec, attachments: Mapping[str, bytes]) -> dict[str, str]:
    project = scenario.base_project
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    spd_import = metadata.get("spd_import", {}) if isinstance(metadata, dict) else {}
    connection_analysis = scenario.connection_analysis
    physical = [
        {
            "refdes": decap.refdes,
            "center": _model_dump(decap.center),
            "pwr_pad": _model_dump(decap.pwr_pad),
            "gnd_pad": _model_dump(decap.gnd_pad),
            "side": str(decap.side),
            "footprint": decap.footprint,
            "source_net": decap.source_net,
            "source_rail_id": decap.source_rail_id,
            "source_model_id": decap.source_model_id,
            "source_mounted": decap.source_mounted,
        }
        for decap in scenario.decaps
    ]
    return {
        "normalized_project": _digest(_model_dump(project)),
        "stackup": _digest([_model_dump(layer) for layer in project.stackup_layers]),
        "plane_metadata": _digest(spd_import.get("plane_geometries", [])),
        "attachment_hashes": _digest({name: sha256(data).hexdigest() for name, data in sorted(attachments.items(), key=lambda pair: pair[0].casefold())}),
        "physical_coordinates": _digest(physical),
    }


def _assert_invariants(before: Mapping[str, str], after: Mapping[str, str]) -> None:
    changed = [name for name, digest in before.items() if after.get(name) != digest]
    if changed:
        raise ReplayValidationError("immutable replay evidence changed: " + ", ".join(changed))


def _validate_existing_rules(scenario: ScenarioSpec) -> int:
    """Re-run public ScenarioSpec validation, including dummy/gap/shared-Via rules."""

    ScenarioSpec.model_validate(scenario.model_dump(mode="python"))
    analysis = scenario.connection_analysis
    if analysis is None:
        raise ReplayValidationError("shared-pad rules cannot be verified without connection analysis")
    for cluster in analysis.clusters:
        decaps = {item.refdes: item for item in scenario.decaps}
        connections = {item.refdes: item for item in analysis.connections.values()}
        derive_shared_pad_current_components(cluster, decaps, connections, analysis_version=analysis.version)
    return len(analysis.clusters)


def _independently_validate_moves(scenario: ScenarioSpec, plan: DistributionPlan, attachments: Mapping[str, bytes]) -> list[dict[str, object]]:
    project = scenario.base_project
    rails = {rail.rail_id.casefold(): rail for rail in project.rails}
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    spd_import = metadata.get("spd_import", {}) if isinstance(metadata, dict) else {}
    records = spd_import.get("plane_geometries", []) if isinstance(spd_import, dict) else []
    if not isinstance(records, list):
        raise ReplayValidationError("scenario has no retained exact plane metadata")
    decoded: dict[
        tuple[str, str], list[tuple[dict[str, object], object]]
    ] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            continue
        key = (str(record.get("net", "")).casefold(), str(record.get("layer", "")).casefold())
        payload = _decode_geometry_asset(record, attachments)
        decoded[key].append((payload, _ordered_artwork_shape(payload)))
    evidence: list[dict[str, object]] = []
    for move in plan.moves:
        rail = rails.get(move.new_rail_id.casefold())
        if rail is None:
            raise ReplayValidationError(f"move {move.refdes} targets unknown rail {move.new_rail_id}")
        candidates = [
            (layer, payload, shape)
            for (net, layer), payloads in decoded.items()
            if net == rail.net.casefold()
            for payload, shape in payloads
        ]
        if not candidates:
            raise ReplayValidationError(f"move {move.refdes} has no retained exact PWR plane for {rail.net}")
        landed = _pwr_landings_for_move(scenario, move.refdes)
        matched: list[dict[str, object]] = []
        for landing in landed:
            if not has_physical_pwr_landing(landing):
                continue
            for layer, payload, shape in candidates:
                if _shape_strictly_contains(
                    shape, float(landing.x_um), float(landing.y_um)
                ):
                    matched.append({"via_id": landing.via_id, "layer": str(payload["layer"]), "x_um": landing.x_um, "y_um": landing.y_um})
        if not matched:
            raise ReplayValidationError(
                f"move {move.refdes} lacks an independent physical PWR landing and exact-artwork proof for {rail.net}"
            )
        evidence.append({"refdes": move.refdes, "new_rail_id": move.new_rail_id, "new_net": move.new_net, "proofs": matched})
    return evidence


def _write_artifacts(directory: Path, scenario: ScenarioSpec, attachments: Mapping[str, bytes], plan: DistributionPlan) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    scenario_path = save_scenario(scenario, directory / "distribution-replay.spdpi", attachments=attachments)
    headers, target_rows = distribution_target_table(plan)
    inventory_headers, inventory_rows = distribution_inventory_table(plan)
    workbook_path = directory / "distribution-replay.xlsx"
    write_distribution_workbook(
        workbook_path,
        distribution_csv_rows(plan)[1:],
        headers,
        target_rows,
        inventory_headers=inventory_headers,
        inventory_rows=inventory_rows,
        metadata={
            "Format Version": 2,
            "Source SPD SHA-256": scenario.source.sha256,
            "Input Design Fingerprint": plan.input_design_fingerprint,
            "Distance Mode": plan.distance_mode.value,
        },
    )
    return scenario_path, workbook_path


def run_replay(args: argparse.Namespace) -> dict[str, object]:
    total_started = perf_counter()
    timings: dict[str, float] = {}
    phase_started = perf_counter()
    imported = import_spd_scenario(args.spd)
    source = imported.scenario
    verify_scenario_source(source, args.spd)
    source_snapshot = _invariant_snapshot(source, imported.attachments)
    timings["import"] = _elapsed_seconds(phase_started)

    phase_started = perf_counter()
    rail_ids = tuple(rail.rail_id for rail in source.base_project.rails)
    model_ids = tuple(model.model_id for model in source.base_project.cap_models)
    present = distribution_present_counts(source)
    target_import = load_distribution_targets(
        args.targets,
        rail_ids=rail_ids,
        model_ids=model_ids,
        current_present=present,
        current_source_sha256=source.source.sha256,
        current_design_fingerprint=source.design_fingerprint,
    )
    unexpected_warnings = [warning for warning in target_import.warnings if warning != _FINGERPRINT_WARNING]
    if unexpected_warnings:
        raise ReplayValidationError("target workbook emitted unsupported warning(s): " + " | ".join(unexpected_warnings))
    mode = args.distance_mode or target_import.distance_mode or DistributionDistanceMode.NEAREST.value
    timings["workbook"] = _elapsed_seconds(phase_started)

    phase_started = perf_counter()
    projection = build_distribution_power_projection(
        source,
        imported.attachments,
        targets=target_import.targets,
        tolerances=target_import.tolerances,
    )
    validate_distribution_targets(source, target_import.targets, target_import.tolerances, power_projection=projection)
    timings["projection"] = _elapsed_seconds(phase_started)

    phase_started = perf_counter()
    plan = compute_distribution_plan(
        source,
        target_import.targets,
        mode,
        tolerances=target_import.tolerances,
        power_projection=projection,
        time_limit_s=args.time_limit_s,
    )
    if args.expect_status and plan.status.value != args.expect_status:
        raise ReplayValidationError(f"expected plan status {args.expect_status}, got {plan.status.value}")
    if args.expect_requested is not None and plan.requested_count != args.expect_requested:
        raise ReplayValidationError(f"expected requested {args.expect_requested}, got {plan.requested_count}")
    if args.expect_fulfilled is not None and plan.fulfilled_count != args.expect_fulfilled:
        raise ReplayValidationError(f"expected fulfilled {args.expect_fulfilled}, got {plan.fulfilled_count}")
    if plan.status == DistributionPlanStatus.PARTIAL and not args.allow_partial:
        raise ReplayValidationError("Distribution returned PARTIAL; pass --allow-partial only when that is expected")
    timings["planner"] = _elapsed_seconds(phase_started)

    phase_started = perf_counter()
    applied = apply_distribution_plan(source, plan, power_projection=projection)
    _assert_invariants(source_snapshot, _invariant_snapshot(applied, imported.attachments))
    cluster_count = _validate_existing_rules(applied)
    timings["apply"] = _elapsed_seconds(phase_started)

    phase_started = perf_counter()
    move_proofs = _independently_validate_moves(applied, plan, imported.attachments)
    timings["independent_proof"] = _elapsed_seconds(phase_started)

    def save_reopen_export(directory: Path) -> tuple[Path, Path, object]:
        scenario_path, workbook_path = _write_artifacts(directory, applied, imported.attachments, plan)
        reopened = load_scenario_bundle(scenario_path)
        verify_scenario_source(reopened.scenario, args.spd)
        _assert_invariants(source_snapshot, _invariant_snapshot(reopened.scenario, reopened.attachments))
        reimported = load_distribution_targets(
            workbook_path,
            rail_ids=rail_ids,
            model_ids=model_ids,
            current_present=distribution_present_counts(reopened.scenario),
            current_source_sha256=reopened.scenario.source.sha256,
            current_design_fingerprint=reopened.scenario.design_fingerprint,
        )
        if reimported.targets != target_import.targets or reimported.tolerances != target_import.tolerances:
            raise ReplayValidationError("exported target workbook did not round-trip Target/Tolerance values")
        if any(warning != _FINGERPRINT_WARNING for warning in reimported.warnings):
            raise ReplayValidationError("export/reimport emitted a warning other than the known source-fingerprint warning")
        return scenario_path, workbook_path, reimported

    phase_started = perf_counter()
    if args.keep_artifacts is not None:
        scenario_path, workbook_path, reimported = save_reopen_export(args.keep_artifacts)
        artifact_paths: dict[str, str] = {"scenario": str(scenario_path.resolve()), "workbook": str(workbook_path.resolve())}
    else:
        with TemporaryDirectory(prefix="distribution-replay-") as temporary:
            _scenario_path, _workbook_path, reimported = save_reopen_export(Path(temporary))
        artifact_paths = {}
    # Saving/reopening/exporting must be observational: the raw-SPD import and
    # its in-memory attachment payloads are the immutable replay baseline.
    _assert_invariants(source_snapshot, _invariant_snapshot(source, imported.attachments))
    timings["roundtrip"] = _elapsed_seconds(phase_started)
    timings["total"] = _elapsed_seconds(total_started)
    return {
        "ok": True,
        "source_spd": str(Path(args.spd).resolve()),
        "targets_workbook": str(Path(args.targets).resolve()),
        "elapsed_seconds": timings,
        "plan": _plan_analysis(plan),
        "independent_move_proofs": move_proofs,
        "invariants": source_snapshot,
        "rules": {
            "scenario_revalidated": True,
            "shared_clusters_checked": cluster_count,
            "gnd_destination_gating": False,
            "destination_proof": "physical_pwr_landing_plus_exact_ordered_copper",
        },
        "workbook_warnings": list(target_import.warnings),
        "round_trip_warnings": list(reimported.warnings),
        "artifacts": artifact_paths,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay and independently validate a real De-cap Distribution workbook.")
    parser.add_argument("--spd", type=Path, required=True, help="raw PowerSI SPD source")
    parser.add_argument("--targets", type=Path, required=True, help="Distribution Targets XLSX workbook")
    parser.add_argument("--time-limit-s", type=float, default=120.0, help="planner time limit in seconds (default: 120)")
    parser.add_argument("--expect-status", choices=("FULL", "PARTIAL"), help="expected Distribution status")
    parser.add_argument("--expect-requested", type=int, help="expected total requested count")
    parser.add_argument("--expect-fulfilled", type=int, help="expected total fulfilled count")
    parser.add_argument("--allow-partial", action="store_true", help="permit a PARTIAL result")
    parser.add_argument("--distance-mode", choices=("NEAREST", "FARTHEST"), help="override workbook distance mode")
    parser.add_argument("--keep-artifacts", type=Path, help="directory for the reopened .spdpi and export/reimport XLSX")
    parser.add_argument("--json-report", type=Path, help="write the JSON report to this path (otherwise stdout)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, object]
    try:
        report = run_replay(args)
    except Exception as exc:
        report = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
