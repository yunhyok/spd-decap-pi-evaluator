from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import zlib
from types import SimpleNamespace

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
    detect_mlo_transition_policy,
    encode_routing_obstacle_asset,
    evaluate_routing_candidate,
    parse_mlo_transition_policy,
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


def _reencode(document: dict) -> bytes:
    raw = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return (
        routing_module.ROUTING_ASSET_MAGIC
        + sha256(raw).hexdigest().encode("ascii")
        + b"\n"
        + zlib.compress(raw, level=9)
    )


@pytest.mark.parametrize("radius_table", ([["L1"]], "ab", [["L1", 1.0, 2.0]]))
def test_malformed_via_radius_table_fails_closed_as_value_error(
    radius_table: object,
) -> None:
    # A short row or a JSON string used to leak IndexError past the decoder's
    # ValueError contract, bypassing the caller's ROUTING_ASSET_STALE handling.
    payload = encode_routing_obstacle_asset(_asset())
    _magic, _digest, compressed = payload.split(b"\n", 2)
    document = json.loads(zlib.decompress(compressed))
    document["via_profiles"][0]["radius_um_by_layer"] = radius_table
    with pytest.raises(ValueError, match="schema is invalid"):
        decode_routing_obstacle_asset(_reencode(document))


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


def test_mlo_transition_policy_detects_qualified_copper_microvia() -> None:
    layers = (
        SimpleNamespace(name="TOP", is_conductor=True, thickness_um=20.0),
        SimpleNamespace(name="D1", is_conductor=False, thickness_um=80.0),
        SimpleNamespace(name="L2", is_conductor=True, thickness_um=20.0),
    )
    landing = SimpleNamespace(
        via_id="V1",
        x_um=100.0,
        y_um=200.0,
        path_evidence=(
            SimpleNamespace(
                trace_hops=0,
                trace_alternate_exit=False,
                segments=(
                    SimpleNamespace(
                        drill_diameter_um=100.0,
                        padstack_material="COPPER",
                        start_layer="TOP",
                        end_layer="L2",
                        end_x_um=100.0,
                        end_y_um=200.0,
                    ),
                ),
            ),
        ),
    )
    policy = detect_mlo_transition_policy((landing,), stackup_layers=layers)
    assert policy.transition_required is True
    assert policy.translated_recipe_validated is False
    assert policy.evidence_codes == ("QUALIFIED_COPPER_MICROVIA",)


def test_mlo_transition_policy_detects_lateral_path_but_not_conventional_through_via() -> None:
    layers = (
        SimpleNamespace(name="TOP", is_conductor=True, thickness_um=20.0),
        SimpleNamespace(name="D1", is_conductor=False, thickness_um=100.0),
        SimpleNamespace(name="L2", is_conductor=True, thickness_um=20.0),
        SimpleNamespace(name="D2", is_conductor=False, thickness_um=100.0),
        SimpleNamespace(name="BOTTOM", is_conductor=True, thickness_um=20.0),
    )
    conventional = SimpleNamespace(
        via_id="V-through",
        x_um=0.0,
        y_um=0.0,
        path_evidence=(
            SimpleNamespace(
                trace_hops=0,
                trace_alternate_exit=False,
                segments=(
                    SimpleNamespace(
                        drill_diameter_um=300.0,
                        padstack_material="COPPER",
                        start_layer="TOP",
                        end_layer="BOTTOM",
                        end_x_um=0.0,
                        end_y_um=0.0,
                    ),
                ),
            ),
        ),
    )
    direct = detect_mlo_transition_policy((conventional,), stackup_layers=layers)
    assert direct.transition_required is False

    lateral = SimpleNamespace(
        via_id="V-mlo",
        x_um=0.0,
        y_um=0.0,
        path_evidence=(
            SimpleNamespace(
                trace_hops=1,
                trace_alternate_exit=False,
                segments=(
                    SimpleNamespace(
                        drill_diameter_um=300.0,
                        padstack_material="COPPER",
                        start_layer="TOP",
                        end_layer="BOTTOM",
                        end_x_um=60.0,
                        end_y_um=0.0,
                    ),
                ),
            ),
        ),
    )
    staggered = detect_mlo_transition_policy((lateral,), stackup_layers=layers)
    assert staggered.transition_required is True
    assert staggered.evidence_codes == (
        "LATERAL_RECOVERED_PATH",
        "STAGGERED_VIA_ENDPOINT",
    )


def test_mlo_detector_unions_full_and_structural_target_evidence() -> None:
    layers = (
        SimpleNamespace(name="TOP", is_conductor=True, thickness_um=20.0),
        SimpleNamespace(name="D1", is_conductor=False, thickness_um=80.0),
        SimpleNamespace(name="L2", is_conductor=True, thickness_um=20.0),
        SimpleNamespace(name="D2", is_conductor=False, thickness_um=80.0),
        SimpleNamespace(name="BOTTOM", is_conductor=True, thickness_um=20.0),
    )
    landing = SimpleNamespace(
        via_id="V-mixed",
        x_um=0.0,
        y_um=0.0,
        path_evidence=(
            SimpleNamespace(
                trace_hops=0,
                trace_alternate_exit=False,
                segments=(
                    SimpleNamespace(
                        drill_diameter_um=300.0,
                        padstack_material="COPPER",
                        start_layer="TOP",
                        end_layer="BOTTOM",
                        end_x_um=0.0,
                        end_y_um=0.0,
                    ),
                ),
            ),
        ),
        structural_evidence=(
            SimpleNamespace(
                trace_hops=0,
                trace_alternate_exit=False,
                segments=(
                    SimpleNamespace(
                        drill_diameter_um=100.0,
                        padstack_material="COPPER",
                        start_layer="TOP",
                        end_layer="L2",
                        end_x_um=0.0,
                        end_y_um=0.0,
                    ),
                ),
            ),
        ),
    )
    policy = detect_mlo_transition_policy((landing,), stackup_layers=layers)
    assert policy.transition_required is True
    assert "QUALIFIED_COPPER_MICROVIA" in policy.evidence_codes


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("transition_required", "false"),
        ("translated_recipe_validated", "true"),
    ),
)
def test_persisted_mlo_policy_rejects_string_booleans(
    field: str, value: str
) -> None:
    payload: dict[str, object] = {
        "policy_version": "MLO_TRANSITION_RECIPE_GATE_V1",
        "transition_required": True,
        "translated_recipe_validated": False,
        "evidence_codes": ["QUALIFIED_COPPER_MICROVIA"],
        "source_sha256": _SOURCE_SHA,
    }
    payload[field] = value
    with pytest.raises(ValueError, match="JSON booleans"):
        parse_mlo_transition_policy(
            payload,
            expected_source_sha256=_SOURCE_SHA,
        )


def test_persisted_mlo_policy_rejects_unknown_version_and_malformed_root() -> None:
    with pytest.raises(ValueError, match="unsupported MLO transition policy"):
        parse_mlo_transition_policy(
            {
                "policy_version": "MLO_TRANSITION_RECIPE_GATE_V999",
                "transition_required": True,
                "translated_recipe_validated": True,
            }
        )
    with pytest.raises(ValueError, match="must be an object"):
        parse_mlo_transition_policy("false")


def test_persisted_mlo_policy_cannot_self_assert_a_validated_recipe() -> None:
    with pytest.raises(ValueError, match="has no translated recipe schema"):
        parse_mlo_transition_policy(
            {
                "policy_version": "MLO_TRANSITION_RECIPE_GATE_V1",
                "transition_required": True,
                "translated_recipe_validated": True,
                "evidence_codes": ["QUALIFIED_COPPER_MICROVIA"],
                "source_sha256": _SOURCE_SHA,
            },
            expected_source_sha256=_SOURCE_SHA,
        )


def test_persisted_mlo_policy_rejects_stale_source_binding() -> None:
    with pytest.raises(ValueError, match="different source SPD"):
        parse_mlo_transition_policy(
            {
                "policy_version": "MLO_TRANSITION_RECIPE_GATE_V1",
                "transition_required": True,
                "translated_recipe_validated": False,
                "evidence_codes": ["QUALIFIED_COPPER_MICROVIA"],
                "source_sha256": _SOURCE_SHA,
            },
            expected_source_sha256=sha256(b"other source").hexdigest(),
        )
