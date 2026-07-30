from __future__ import annotations

from dataclasses import replace

from spd_decap_pi._core.io.shared_pad import (
    DecapPadEvidence,
    SpdPadShape,
    SpdTopCopperGeometry,
    ViaTopEndpoint,
    extract_shared_pad_connectivity,
)


TOP = "Signal$TOP"
PADSTACKS = {
    "cap": (SpdPadShape(TOP, "RECTANGLE", 400.0, 400.0),),
    "via": (SpdPadShape(TOP, "CIRCLE", 100.0, 100.0),),
}


def _decap(refdes: str, y_um: float, *, rotation: float = 0.0) -> DecapPadEvidence:
    return DecapPadEvidence(
        refdes=refdes,
        top_side=True,
        layer=TOP,
        power_net="VDD",
        ground_net="DGND",
        power_x_um=0.0,
        power_y_um=y_um,
        power_padstack="CAP",
        power_rotation_degrees=rotation,
        ground_x_um=600.0,
        ground_y_um=y_um,
        ground_padstack="CAP",
        ground_rotation_degrees=rotation,
    )


def _via(via_id: str, net: str, x_um: float, y_um: float) -> ViaTopEndpoint:
    return ViaTopEndpoint(
        via_id=via_id,
        net=net,
        endpoint_node_id=f"Node_{via_id}",
        x_um=x_um,
        y_um=y_um,
        padstack="VIA",
        rotation_degrees=0.0,
    )


def _rectangle(
    x_min: float, y_min: float, x_max: float, y_max: float
) -> tuple[tuple[float, float], ...]:
    return (
        (x_min, y_min),
        (x_max, y_min),
        (x_max, y_max),
        (x_min, y_max),
    )


def _top_copper(
    net: str,
    polygons: tuple[tuple[tuple[float, float], ...], ...],
    *,
    negative: tuple[tuple[tuple[float, float], ...], ...] = (),
) -> SpdTopCopperGeometry:
    return SpdTopCopperGeometry(
        layer=TOP,
        net=net,
        positive_polygons_um=polygons,
        negative_polygons_um=negative,
        primitive_order=tuple(
            [("positive_polygon", index) for index in range(len(polygons))]
            + [("negative_polygon", index) for index in range(len(negative))]
        ),
    )


def test_exact_positive_pad_overlap_forms_anchored_cluster() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("ANCHOR", 0.0), _decap("DUMMY", 350.0)),
        (
            _via("VP", "VDD", 0.0, -100.0),
            _via("VG", "DGND", 600.0, -100.0),
        ),
        PADSTACKS,
        top_layer=TOP,
    )

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.state == "ANCHORED"
    assert cluster.member_refdes == ("ANCHOR", "DUMMY")
    assert cluster.anchor_refdes == ("ANCHOR",)
    assert cluster.dummy_refdes == ("DUMMY",)
    assert cluster.power_edges == (("ANCHOR", "DUMMY"),)
    assert cluster.ground_edges == (("ANCHOR", "DUMMY"),)
    assert {item.refdes: item.kind for item in result.connections} == {
        "ANCHOR": "SHARED_ANCHOR",
        "DUMMY": "SHARED_DUMMY",
    }


def test_aggregate_cluster_accepts_power_and_ground_vias_on_different_members() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("A", 0.0), _decap("B", 350.0)),
        (
            _via("VP", "VDD", 0.0, -100.0),
            _via("VG", "DGND", 600.0, 450.0),
        ),
        PADSTACKS,
        top_layer=TOP,
    )

    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].anchor_refdes == ("A", "B")
    assert {item.kind for item in result.connections} == {"SHARED_ANCHOR"}


def test_one_physical_via_pad_can_bridge_multiple_component_pads() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("A", 0.0), _decap("B", 450.0)),
        (
            _via("VP_SHARED", "VDD", 0.0, 225.0),
            _via("VG_SHARED", "DGND", 600.0, 225.0),
        ),
        PADSTACKS,
        top_layer=TOP,
    )

    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].power_edges == (("A", "B"),)
    assert result.clusters[0].ground_edges == (("A", "B"),)
    by_refdes = {item.refdes: item for item in result.connections}
    assert {
        refdes: tuple(landing.via_id for landing in connection.power_vias)
        for refdes, connection in by_refdes.items()
    } == {"A": ("VP_SHARED",), "B": ("VP_SHARED",)}
    assert {
        refdes: tuple(landing.via_id for landing in connection.ground_vias)
        for refdes, connection in by_refdes.items()
    } == {"A": ("VG_SHARED",), "B": ("VG_SHARED",)}
    assert by_refdes["A"].power_vias == by_refdes["B"].power_vias
    assert by_refdes["A"].ground_vias == by_refdes["B"].ground_vias


def test_many_to_many_cluster_retains_all_unique_terminal_via_evidence() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("A", 0.0), _decap("B", 350.0), _decap("C", 700.0)),
        (
            _via("VP_AB", "VDD", 0.0, 175.0),
            _via("VP_C", "VDD", 0.0, 700.0),
            _via("VG_A", "DGND", 600.0, 0.0),
            _via("VG_BC", "DGND", 600.0, 525.0),
            _via("VG_C", "DGND", 600.0, 700.0),
        ),
        PADSTACKS,
        top_layer=TOP,
    )

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.state == "ANCHORED"
    assert cluster.anchor_refdes == ("A", "B", "C")
    assert cluster.dummy_refdes == ()

    by_refdes = {item.refdes: item for item in result.connections}
    power_ownership = {
        refdes: tuple(landing.via_id for landing in connection.power_vias)
        for refdes, connection in by_refdes.items()
    }
    ground_ownership = {
        refdes: tuple(landing.via_id for landing in connection.ground_vias)
        for refdes, connection in by_refdes.items()
    }
    assert power_ownership == {
        "A": ("VP_AB",),
        "B": ("VP_AB",),
        "C": ("VP_C",),
    }
    assert ground_ownership == {
        "A": ("VG_A",),
        "B": ("VG_BC",),
        "C": ("VG_BC", "VG_C"),
    }
    assert {
        landing.via_id
        for connection in result.connections
        for landing in connection.power_vias
    } == {"VP_AB", "VP_C"}
    assert {
        landing.via_id
        for connection in result.connections
        for landing in connection.ground_vias
    } == {"VG_A", "VG_BC", "VG_C"}
    assert all(
        len(connection.power_vias)
        == len({landing.via_id.casefold() for landing in connection.power_vias})
        and len(connection.ground_vias)
        == len({landing.via_id.casefold() for landing in connection.ground_vias})
        for connection in result.connections
    )


def test_cluster_identity_and_edges_do_not_depend_on_input_order() -> None:
    decaps = (_decap("A", 0.0), _decap("B", 350.0))
    vias = (
        _via("VP", "VDD", 0.0, -100.0),
        _via("VG", "DGND", 600.0, -100.0),
    )

    forward = extract_shared_pad_connectivity(
        decaps, vias, PADSTACKS, top_layer=TOP
    )
    reverse = extract_shared_pad_connectivity(
        tuple(reversed(decaps)), tuple(reversed(vias)), PADSTACKS, top_layer=TOP
    )

    assert forward.clusters == reverse.clusters


def test_singleton_with_only_one_terminal_via_fails_closed() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("C1", 0.0),),
        (_via("VP", "VDD", 0.0, 0.0),),
        PADSTACKS,
        top_layer=TOP,
    )

    assert result.connections[0].kind == "UNRESOLVED"
    assert "only one terminal" in str(result.connections[0].reason)


def test_cluster_without_any_via_is_floating_not_direct() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("C1", 0.0), _decap("C2", 350.0)),
        (),
        PADSTACKS,
        top_layer=TOP,
    )

    assert result.clusters[0].state == "FLOATING"
    assert {item.kind for item in result.connections} == {"FLOATING_DUMMY"}


def test_boundary_only_pad_contact_is_unresolved_and_not_clustered() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("C1", 0.0), _decap("C2", 400.0)),
        (),
        PADSTACKS,
        top_layer=TOP,
    )

    assert result.clusters == ()
    assert {item.kind for item in result.connections} == {"UNRESOLVED"}
    assert all("boundary-only" in str(item.reason) for item in result.connections)


def test_source_top_copper_bridges_non_overlapping_anchor_and_dummy_pads() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("ANCHOR", 0.0), _decap("DUMMY", 450.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 200.0, 650.0),)),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 650.0),)),
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].power_edges == (("ANCHOR", "DUMMY"),)
    assert result.clusters[0].ground_edges == (("ANCHOR", "DUMMY"),)
    assert {item.refdes: item.kind for item in result.connections} == {
        "ANCHOR": "SHARED_ANCHOR",
        "DUMMY": "SHARED_DUMMY",
    }
    assert result.source_copper_power_edges == 1
    assert result.source_copper_ground_edges == 1
    assert result.source_copper_member_count == 2


def test_source_copper_path_edges_follow_coordinates_not_refdes_order() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("A", 0.0), _decap("Z", 450.0), _decap("M", 900.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 200.0, 1100.0),)),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 1100.0),)),
        ),
    )

    cluster = result.clusters[0]
    assert cluster.power_edges == (("A", "Z"), ("M", "Z"))
    assert cluster.ground_edges == (("A", "Z"), ("M", "Z"))
    assert cluster.isolation_gap_refdes == ("A", "M", "Z")


def test_non_rectangular_copper_cluster_is_atomic_for_isolation_gaps() -> None:
    polygon = (
        (-200.0, -200.0),
        (200.0, -200.0),
        (250.0, 1100.0),
        (0.0, 1200.0),
        (-200.0, 1100.0),
    )
    result = extract_shared_pad_connectivity(
        (_decap("A", 0.0), _decap("B", 450.0), _decap("C", 900.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (polygon,)),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 1100.0),)),
        ),
    )

    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].isolation_gap_refdes == ()


def test_global_ground_copper_cannot_merge_separate_power_components() -> None:
    result = extract_shared_pad_connectivity(
        (
            _decap("A", 0.0),
            _decap("B", 450.0),
            _decap("C", 1200.0),
        ),
        (
            _via("VP_A", "VDD", 0.0, 0.0),
            _via("VG_A", "DGND", 600.0, 0.0),
            _via("VP_C", "VDD", 0.0, 1200.0),
            _via("VG_C", "DGND", 600.0, 1200.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (
                    _rectangle(-200.0, -200.0, 200.0, 650.0),
                    _rectangle(-200.0, 1000.0, 200.0, 1400.0),
                ),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 1400.0),)),
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].member_refdes == ("A", "B")
    assert {item.refdes: item.kind for item in result.connections} == {
        "A": "SHARED_ANCHOR",
        "B": "SHARED_DUMMY",
        "C": "DIRECT",
    }


def test_negative_top_copper_cutout_does_not_create_a_bridge() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("ANCHOR", 0.0), _decap("DUMMY", 450.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-200.0, -200.0, 200.0, 650.0),),
                negative=(_rectangle(-100.0, 350.0, 100.0, 550.0),),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 650.0),)),
        ),
    )

    assert result.clusters == ()
    assert {item.refdes: item.kind for item in result.connections} == {
        "ANCHOR": "DIRECT",
        "DUMMY": "FLOATING_DUMMY",
    }


def test_rotated_rectangles_use_exact_oriented_overlap() -> None:
    shapes = {
        **PADSTACKS,
        "cap": (SpdPadShape(TOP, "RECTANGLE", 500.0, 100.0),),
    }
    result = extract_shared_pad_connectivity(
        (_decap("H", 0.0), _decap("V", 0.0, rotation=90.0)),
        (
            _via("VP", "VDD", -150.0, 0.0),
            _via("VG", "DGND", 450.0, 0.0),
        ),
        shapes,
        top_layer=TOP,
    )

    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].member_refdes == ("H", "V")


def test_unsupported_source_pad_geometry_never_falls_back_to_distance() -> None:
    shapes = {
        **PADSTACKS,
        "cap": (
            SpdPadShape(
                TOP,
                "UNSUPPORTED",
                None,
                None,
                "Regular Polygon is unsupported",
            ),
        ),
    }
    result = extract_shared_pad_connectivity(
        (_decap("C1", 0.0),),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        shapes,
        top_layer=TOP,
    )

    assert result.connections[0].kind == "UNRESOLVED"
    assert "Polygon" in str(result.connections[0].reason)


def test_missing_terminal_padstack_fails_closed_without_crashing() -> None:
    missing = replace(_decap("C1", 0.0), power_padstack=None)

    result = extract_shared_pad_connectivity(
        (missing,),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
    )

    assert result.connections[0].kind == "UNRESOLVED"
    assert "no source padstack" in str(result.connections[0].reason)
