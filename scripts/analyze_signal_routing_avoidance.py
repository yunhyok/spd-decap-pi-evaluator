"""Offline SPD Trace-avoidance research harness.

This intentionally works even when the named SPD cannot form an editable GUI
scenario (for example, the PC_2116 practice file currently fails later rail
planning).  It never modifies the source SPD.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from spd_decap_pi._core.io.spd import analyze_spd
from spd_decap_pi.routing_obstacles import (
    PlannedViaProfile,
    RoutingObstacleAsset,
    SignalTraceAvoidancePolicy,
    evaluate_routing_candidate,
    stackup_fingerprint,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan logical PowerSI Trace records and optionally evaluate one "
            "vertical via-column probe without importing a GUI scenario."
        )
    )
    parser.add_argument("spd", type=Path)
    parser.add_argument("--x-um", type=float)
    parser.add_argument("--y-um", type=float)
    parser.add_argument("--destination-layer")
    parser.add_argument("--mount-side", choices=("TOP", "BOTTOM"), default="TOP")
    parser.add_argument("--via-radius-um", type=float, default=50.0)
    clearance = parser.add_mutually_exclusive_group()
    clearance.add_argument("--clearance-um", type=float)
    clearance.add_argument(
        "--research-two-width",
        action="store_true",
        help="Use the documented research-only edge clearance C = 2w.",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    analysis = analyze_spd(args.spd, scope="decap_scenario")
    extraction = analysis.routing_extraction
    if extraction is None:
        print("No routing extraction was produced.", file=sys.stderr)
        return 2
    conductors = tuple(
        item.name for item in analysis.stackup_layers if item.is_conductor
    )
    result: dict[str, object] = {
        "source": {
            "path": str(analysis.source.path),
            "size_bytes": analysis.source.size_bytes,
            "sha256": analysis.source.sha256,
        },
        "compiler_policy": extraction.compiler_policy,
        "production_ready": extraction.production_ready,
        "statistics": dict(sorted(extraction.statistics.items())),
        "retained_segment_count": len(extraction.segments),
        "conductor_layer_count": len(conductors),
        "incomplete_layer_count": sum(
            not item.complete for item in extraction.layer_completeness
        ),
        "error_diagnostics": [
            {"code": item.code, "message": item.message}
            for item in analysis.diagnostics
            if item.severity == "error"
        ],
    }
    probe_values = (args.x_um, args.y_um, args.destination_layer)
    if any(value is not None for value in probe_values):
        if not all(value is not None for value in probe_values):
            print(
                "--x-um, --y-um and --destination-layer must be supplied together",
                file=sys.stderr,
            )
            return 2
        profile = PlannedViaProfile(
            profile_id="OFFLINE_EXPLICIT_RADIUS",
            radius_um_by_layer=tuple(
                (layer, float(args.via_radius_um)) for layer in conductors
            ),
            provenance="USER_EXPLICIT_OFFLINE_RESEARCH_RADIUS",
        )
        asset = RoutingObstacleAsset(
            source_sha256=analysis.source.sha256,
            stackup_fingerprint=stackup_fingerprint(analysis.stackup_layers),
            conductor_layers=conductors,
            segments=extraction.segments,
            layer_completeness=extraction.layer_completeness,
            via_profiles=(profile,),
            compiler_policy=extraction.compiler_policy,
            production_ready=False,
        )
        policy = (
            SignalTraceAvoidancePolicy.research_width_multiplier()
            if args.research_two_width
            else SignalTraceAvoidancePolicy.fixed(
                0.0 if args.clearance_um is None else args.clearance_um
            )
        )
        proof = evaluate_routing_candidate(
            asset,
            x_um=float(args.x_um),
            y_um=float(args.y_um),
            destination_layer=str(args.destination_layer),
            mount_side=args.mount_side,
            profile_id=profile.profile_id,
            policy=policy,
        )
        result["probe"] = {
            "state": proof.state.value,
            "destination_layer": proof.destination_layer,
            "policy": policy.payload(),
            "evidence": [asdict(item) for item in proof.evidence],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
