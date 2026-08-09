from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import zlib

import pytest

import spd_decap_pi.routing_obstacles as routing_module
from spd_decap_pi.routing_obstacles import (
    PlannedViaProfile,
    RoutingCandidateState,
    RoutingLayerCompleteness,
    RoutingNetRole,
    RoutingObjectProvenance,
    RoutingObstacleAsset,
    RoutingTraceSegment,
    SignalTraceAvoidancePolicy,
    TraceWidthSource,
    decode_routing_obstacle_asset,
    encode_routing_obstacle_asset,
    evaluate_routing_candidate,
    point_segment_distance,
)


_SOURCE_SHA = sha256(b"source").hexdigest()
_STACK_SHA = sha256(b"stack").hexdigest()


def _asset(*, incomplete_layer: str | None = None) -> RoutingObstacleAsset:
    layers = ("TOP", "PWR1", "SIG1", "PWR3", "BOTTOM")
    return RoutingObstacleAsset(
        source_sha256=_SOURCE_SHA,
        stackup_fingerprint=_STACK_SHA,
        conductor_layers=layers,
        segments=(
            RoutingTraceSegment(
                trace_id="Trace1",
                net="SIG_A",
                layer="SIG1",
                x1_um=-100.0,
                y1_um=0.0,
                x2_um=100.0,
                y2_um=0.0,
                width_um=20.0,
                width_source=TraceWidthSource.INLINE,
                net_role=RoutingNetRole.SIGNAL,
                provenance=RoutingObjectProvenance.PHYSICAL_ROUTING,
            ),
        ),
        layer_completeness=tuple(
            RoutingLayerCompleteness(
                layer=layer,
                unresolved_width_count=(1 if layer == incomplete_layer else 0),
                unresolved_codes=(
                    ("TRACE_WIDTH_UNRESOLVED",)
                    if layer == incomplete_layer
                    else ()
                ),
            )
            for layer in layers
        ),
        via_profiles=(
            PlannedViaProfile(
                profile_id="VIA_A",
                radius_um_by_layer=tuple((layer, 50.0) for layer in layers),
            ),
        ),
    )


def _proof(
    asset: RoutingObstacleAsset,
    *,
    y_um: float,
    destination: str = "PWR3",
    side: str = "TOP",
):
    return evaluate_routing_candidate(
        asset,
        x_um=0.0,
        y_um=y_um,
        destination_layer=destination,
        mount_side=side,
        profile_id="VIA_A",
        policy=SignalTraceAvoidancePolicy.fixed(15.0),
    )


def test_fixed_clearance_blocks_exact_tangency_and_keeps_evidence() -> None:
    # 50 um via radius + 10 um trace half-width + 15 um clearance = 75 um.
    proof = _proof(_asset(), y_um=75.0)

    assert proof.state == RoutingCandidateState.BLOCKED
    evidence = proof.evidence[0]
    assert evidence.code == "IMMUTABLE_SIGNAL_CLEARANCE_BLOCKED"
    assert evidence.trace_id == "Trace1"
    assert evidence.center_distance_um == pytest.approx(75.0)
    assert evidence.copper_edge_gap_um == pytest.approx(15.0)
    assert evidence.required_clearance_um == pytest.approx(15.0)


def test_trace_outside_clearance_or_outside_span_is_safe() -> None:
    assert _proof(_asset(), y_um=75.000_01).state == RoutingCandidateState.SAFE
    assert (
        _proof(_asset(), y_um=0.0, destination="PWR1").state
        == RoutingCandidateState.SAFE
    )


def test_bottom_mount_uses_destination_to_bottom_suffix() -> None:
    blocked = _proof(_asset(), y_um=0.0, destination="PWR1", side="BOTTOM")
    outside = _proof(_asset(), y_um=0.0, destination="PWR3", side="BOTTOM")

    assert blocked.state == RoutingCandidateState.BLOCKED
    assert outside.state == RoutingCandidateState.SAFE


def test_known_collision_outranks_unknown_and_unknown_is_fail_closed() -> None:
    collision = _proof(_asset(incomplete_layer="TOP"), y_um=0.0)
    assert collision.state == RoutingCandidateState.BLOCKED

    unknown = _proof(_asset(incomplete_layer="TOP"), y_um=500.0)
    assert unknown.state == RoutingCandidateState.UNKNOWN
    assert unknown.evidence[0].code == "TRACE_WIDTH_UNRESOLVED"


def test_missing_profile_or_ambiguous_span_is_unknown() -> None:
    asset = _asset()
    missing = evaluate_routing_candidate(
        asset,
        x_um=0.0,
        y_um=0.0,
        destination_layer="PWR3",
        mount_side="TOP",
        profile_id="missing",
        policy=SignalTraceAvoidancePolicy.fixed(0.0),
    )
    assert missing.state == RoutingCandidateState.UNKNOWN
    assert missing.evidence[0].code == "VIA_PROFILE_UNRESOLVED"

    bad_span = evaluate_routing_candidate(
        asset,
        x_um=0.0,
        y_um=0.0,
        destination_layer="missing",
        mount_side="TOP",
        profile_id="VIA_A",
        policy=SignalTraceAvoidancePolicy.fixed(0.0),
    )
    assert bad_span.state == RoutingCandidateState.UNKNOWN
    assert bad_span.evidence[0].code == "STACKUP_SPAN_UNRESOLVED"


def test_research_two_width_policy_is_separate_from_fixed_ui_policy() -> None:
    research = SignalTraceAvoidancePolicy.research_width_multiplier()
    assert research.trace_width_multiplier == 2.0
    assert research.clearance_um is None
    # 50 + 10 + 40 = 100 um under the research oracle.
    proof = evaluate_routing_candidate(
        _asset(),
        x_um=0.0,
        y_um=100.0,
        destination_layer="PWR3",
        mount_side="TOP",
        profile_id="VIA_A",
        policy=research,
    )
    assert proof.state == RoutingCandidateState.BLOCKED


def test_huge_clearance_is_bounded_to_populated_source_tiles() -> None:
    proof = evaluate_routing_candidate(
        _asset(),
        x_um=1.0e9,
        y_um=1.0e9,
        destination_layer="PWR3",
        mount_side="TOP",
        profile_id="VIA_A",
        policy=SignalTraceAvoidancePolicy.fixed(2.0e9),
    )

    assert proof.state == RoutingCandidateState.BLOCKED


def test_long_segment_fallback_is_checked_outside_regular_tile_bounds() -> None:
    base = _asset()
    long_segment = replace(
        base.segments[0],
        trace_id="TraceLong",
        x1_um=100_000_000.0,
        x2_um=200_000_000.0,
    )
    asset = replace(base, segments=(*base.segments, long_segment))
    proof = evaluate_routing_candidate(
        asset,
        x_um=150_000_000.0,
        y_um=0.0,
        destination_layer="PWR3",
        mount_side="TOP",
        profile_id="VIA_A",
        policy=SignalTraceAvoidancePolicy.fixed(0.0),
    )

    assert proof.state == RoutingCandidateState.BLOCKED
    assert proof.evidence[0].trace_id == "TraceLong"


def test_disabled_policy_is_a_complete_geometry_bypass() -> None:
    proof = evaluate_routing_candidate(
        _asset(incomplete_layer="TOP"),
        x_um=float("nan"),
        y_um=float("nan"),
        destination_layer="missing",
        mount_side="UNKNOWN",
        profile_id=None,
        policy=SignalTraceAvoidancePolicy.disabled(),
    )
    assert proof.state == RoutingCandidateState.SAFE


def test_asset_encoding_is_deterministic_and_tamper_checked() -> None:
    asset = _asset()
    first = encode_routing_obstacle_asset(asset)
    second = encode_routing_obstacle_asset(asset)
    assert first == second

    decoded = decode_routing_obstacle_asset(
        first,
        expected_source_sha256=_SOURCE_SHA,
        expected_stackup_fingerprint=_STACK_SHA,
    )
    assert decoded.segments == asset.segments
    assert decoded.via_profiles == asset.via_profiles
    assert decoded.content_sha256 is not None

    tampered = first[:-1] + bytes([first[-1] ^ 1])
    with pytest.raises(ValueError, match="compression|SHA-256"):
        decode_routing_obstacle_asset(tampered)
    with pytest.raises(ValueError, match="different source"):
        decode_routing_obstacle_asset(
            first, expected_source_sha256=sha256(b"other").hexdigest()
        )


def test_asset_decoder_enforces_expansion_limit_and_json_boolean_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = encode_routing_obstacle_asset(_asset())
    monkeypatch.setattr(routing_module, "MAX_ROUTING_ASSET_EXPANDED_BYTES", 128)
    with pytest.raises(ValueError, match="expanded-size limit"):
        decode_routing_obstacle_asset(payload)

    monkeypatch.setattr(
        routing_module, "MAX_ROUTING_ASSET_EXPANDED_BYTES", 256 * 1024 * 1024
    )
    _magic, _digest, compressed = payload.split(b"\n", 2)
    document = json.loads(zlib.decompress(compressed))
    document["production_ready"] = "false"
    raw = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    malformed = (
        routing_module.ROUTING_ASSET_MAGIC
        + sha256(raw).hexdigest().encode("ascii")
        + b"\n"
        + zlib.compress(raw, level=9)
    )
    with pytest.raises(ValueError, match="JSON boolean"):
        decode_routing_obstacle_asset(malformed)


def test_point_segment_distance_handles_diagonal_and_zero_length() -> None:
    assert point_segment_distance(1.0, 0.0, 0.0, 0.0, 2.0, 2.0) == pytest.approx(
        2**-0.5
    )
    assert point_segment_distance(3.0, 4.0, 0.0, 0.0, 0.0, 0.0) == 5.0


def test_policy_rejects_invalid_user_clearance() -> None:
    for value in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite and >= 0"):
            SignalTraceAvoidancePolicy.fixed(value)


def test_segment_coordinate_format_bound_rejects_index_bombs() -> None:
    with pytest.raises(ValueError, match="coordinate exceeds"):
        RoutingTraceSegment(
            trace_id="TraceHuge",
            net="SIG_A",
            layer="SIG1",
            x1_um=-1.0e300,
            y1_um=0.0,
            x2_um=1.0e300,
            y2_um=0.0,
            width_um=20.0,
            width_source=TraceWidthSource.INLINE,
            net_role=RoutingNetRole.SIGNAL,
            provenance=RoutingObjectProvenance.PHYSICAL_ROUTING,
        )
