from __future__ import annotations

from dataclasses import replace

from spd_decap_pi._core.io.shared_pad import (
    DecapPadEvidence,
    SpdPadShape,
    SpdTopCopperGeometry,
    ViaTopEndpoint,
    _PlacedPad,
    _final_copper_relation_at,
    _segment_boundary_parameters,
    _segment_is_final_copper,
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


def test_tessellated_source_copper_seam_is_not_a_terminal_boundary() -> None:
    """Adjacent source tiles model the split TOP primitives in the real SPD."""

    result = extract_shared_pad_connectivity(
        (_decap("ANCHOR", 0.0), _decap("DUMMY", 450.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            # ANCHOR's PWR centre is exactly on the shared edge.  The two
            # rectangles form one final copper union, so this is not an outer
            # copper boundary and must remain eligible for the shared cluster.
            _top_copper(
                "VDD",
                (
                    _rectangle(-200.0, -200.0, 200.0, 0.0),
                    _rectangle(-200.0, 0.0, 200.0, 650.0),
                ),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 650.0),)),
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].member_refdes == ("ANCHOR", "DUMMY")
    assert {item.refdes: item.kind for item in result.connections} == {
        "ANCHOR": "SHARED_ANCHOR",
        "DUMMY": "SHARED_DUMMY",
    }


def test_edge_touching_tiles_connect_centres_that_are_not_on_the_seam() -> None:
    first = replace(
        _decap("ANCHOR", 0.0),
        power_x_um=-300.0,
        ground_x_um=-300.0,
        ground_y_um=600.0,
    )
    second = replace(
        _decap("DUMMY", 0.0),
        power_x_um=300.0,
        ground_x_um=300.0,
        ground_y_um=600.0,
    )
    result = extract_shared_pad_connectivity(
        (first, second),
        (
            _via("VP", "VDD", -300.0, 0.0),
            _via("VG", "DGND", -300.0, 600.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (
                    _rectangle(-500.0, -200.0, 0.0, 200.0),
                    _rectangle(0.0, -200.0, 500.0, 200.0),
                ),
            ),
            _top_copper("DGND", (_rectangle(-500.0, 400.0, 500.0, 800.0),)),
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].member_refdes == ("ANCHOR", "DUMMY")


def test_negative_strip_splits_one_positive_rectangle_component() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("ANCHOR", -300.0), _decap("OTHER", 300.0)),
        (
            _via("VP", "VDD", 0.0, -300.0),
            _via("VG", "DGND", 600.0, -300.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-200.0, -500.0, 200.0, 500.0),),
                negative=(_rectangle(-250.0, -50.0, 250.0, 50.0),),
            ),
            _top_copper("DGND", (_rectangle(400.0, -500.0, 800.0, 500.0),)),
        ),
    )

    assert result.clusters == ()
    assert all(item.kind not in {"SHARED_ANCHOR", "SHARED_DUMMY"} for item in result.connections)


def test_one_finite_terminal_pad_can_bridge_two_final_fill_components() -> None:
    decaps = tuple(
        replace(
            _decap(refdes, 0.0),
            power_x_um=x_um,
            ground_x_um=x_um,
            ground_y_um=600.0,
        )
        for refdes, x_um in (("LEFT", -450.0), ("BRIDGE", 0.0), ("RIGHT", 450.0))
    )
    result = extract_shared_pad_connectivity(
        decaps,
        (
            _via("VP", "VDD", -450.0, 0.0),
            _via("VG", "DGND", -450.0, 600.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-700.0, -200.0, 700.0, 200.0),),
                negative=(_rectangle(-50.0, -300.0, 50.0, 300.0),),
            ),
            _top_copper("DGND", (_rectangle(-700.0, 400.0, 700.0, 800.0),)),
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].member_refdes == ("BRIDGE", "LEFT", "RIGHT")
    assert result.clusters[0].state == "ANCHORED"


def test_finite_pad_overlaps_copper_at_narrow_cutout_vertex() -> None:
    # The wedge lies between the old fixed probe angles; incident source rays
    # must still expose its arbitrarily narrow empty sector.
    narrow_wedge = ((0.0, 0.0), (200.0, 17.0), (200.0, 18.0))
    positive = _rectangle(-200.0, -200.0, 250.0, 200.0)
    primitives = (
        ("positive_polygon", positive),
        ("negative_polygon", narrow_wedge),
    )
    assert _final_copper_relation_at(0.0, 0.0, primitives) == "boundary"

    result = extract_shared_pad_connectivity(
        (_decap("CUTOUT-TIP", 0.0),),
        (_via("VP", "VDD", 0.0, 0.0), _via("VG", "DGND", 600.0, 0.0)),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (positive,),
                negative=(narrow_wedge,),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    assert result.connections[0].kind == "DIRECT"


def test_finite_pad_overlaps_readded_circle_at_tangent_cusp() -> None:
    # The negative R=200 circle and later positive R=100 circle are internally
    # tangent at (200, 0).  Their tangent rays coincide, yet an arbitrarily
    # narrow unfilled cusp remains immediately above/below the terminal.
    geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="VDD",
        positive_polygons_um=(_rectangle(-300.0, -300.0, 300.0, 300.0),),
        negative_polygons_um=(),
        positive_circles_um=((100.0, 0.0, 100.0),),
        negative_circles_um=((0.0, 0.0, 200.0),),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_circle", 0),
            ("positive_circle", 0),
        ),
    )
    decap = replace(_decap("CUSP", 0.0), power_x_um=200.0)
    primitives = (
        ("positive_polygon", geometry.positive_polygons_um[0]),
        ("negative_circle", geometry.negative_circles_um[0]),
        ("positive_circle", geometry.positive_circles_um[0]),
    )
    # Point/path topology remains deliberately fail-closed at the tangent,
    # while the 400 x 400 um terminal has a provable open filled overlap.
    assert _final_copper_relation_at(200.0, 0.0, primitives) == "boundary"
    result = extract_shared_pad_connectivity(
        (decap,),
        (
            _via("VP", "VDD", 200.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            geometry,
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    assert result.connections[0].kind == "DIRECT"


def test_board_scale_circle_tangent_roundoff_keeps_boundary_parameter() -> None:
    circle = (
        45_603.427188924936,
        44_782.74870593494,
        1.477930811151975,
    )
    first = (45_450.30801478851, 44_784.22663674609)
    second = (45_740.44703866278, 44_784.22663674609)

    parameters = _segment_boundary_parameters(
        first,
        second,
        "negative_circle",
        circle,
    )

    assert len(parameters) == 2
    assert 0.0 < parameters[0] == parameters[1] < 1.0
    first_terminal = _PlacedPad(
        0, "PWR", "vdd", *first, "CIRCLE", 10.0, 10.0, 0.0
    )
    second_terminal = _PlacedPad(
        1, "PWR", "vdd", *second, "CIRCLE", 10.0, 10.0, 0.0
    )
    primitives = (
        (
            "positive_polygon",
            _rectangle(first[0] - 10.0, first[1] - 10.0, second[0] + 10.0, second[1] + 10.0),
        ),
        ("negative_circle", circle),
    )
    assert not _segment_is_final_copper(
        first_terminal,
        second_terminal,
        primitives,
    )


def test_ordered_readd_island_does_not_rejoin_the_original_base_group() -> None:
    geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="VDD",
        positive_polygons_um=(
            _rectangle(-650.0, -600.0, 650.0, 600.0),
            _rectangle(-40.0, -300.0, 40.0, 300.0),
        ),
        # Keep a real 50 um clearance between the 400 um terminal and each
        # base-copper side, so this test remains about ordered re-add isolation
        # rather than the finite pad itself bridging the cutout.
        negative_polygons_um=(_rectangle(-250.0, -650.0, 250.0, 650.0),),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_polygon", 0),
            ("positive_polygon", 1),
        ),
    )
    first = replace(_decap("BASE", 0.0), power_x_um=-450.0)
    island_a = replace(_decap("ISLAND-A", -225.0), power_x_um=0.0)
    island_b = replace(_decap("ISLAND-B", 225.0), power_x_um=0.0)
    result = extract_shared_pad_connectivity(
        (first, island_a, island_b),
        (),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            geometry,
            _top_copper("DGND", (_rectangle(400.0, -500.0, 800.0, 500.0),)),
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].member_refdes == ("ISLAND-A", "ISLAND-B")
    assert result.clusters[0].state == "FLOATING"
    assert {
        item.refdes: item.kind
        for item in result.connections
        if item.refdes.startswith("ISLAND")
    } == {"ISLAND-A": "FLOATING_DUMMY", "ISLAND-B": "FLOATING_DUMMY"}


def test_many_disjoint_rectangles_do_not_create_a_dense_global_cell_grid() -> None:
    rectangles = tuple(
        _rectangle(index * 1000.0, -200.0, index * 1000.0 + 400.0, 200.0)
        for index in range(1200)
    )
    first = replace(_decap("A", 0.0), power_x_um=0.0)
    last = replace(_decap("B", 0.0), power_x_um=1_199_000.0)
    result = extract_shared_pad_connectivity(
        (first, last),
        (),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(_top_copper("VDD", rectangles),),
    )

    assert result.clusters == ()


def test_rectangular_geometry_never_claims_an_overlapping_other_net_terminal() -> None:
    first = replace(
        _decap("VDD-A", 0.0),
        power_x_um=-300.0,
        ground_x_um=-300.0,
        ground_y_um=600.0,
    )
    second = replace(
        _decap("VDD2-B", 0.0),
        power_net="VDD2",
        power_x_um=300.0,
        ground_x_um=300.0,
        ground_y_um=600.0,
    )
    result = extract_shared_pad_connectivity(
        (first, second),
        (),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-500.0, -200.0, 500.0, 200.0),)),
            _top_copper("VDD2", (_rectangle(100.0, -200.0, 500.0, 200.0),)),
            _top_copper("DGND", (_rectangle(-500.0, 400.0, 500.0, 800.0),)),
        ),
    )

    assert result.clusters == ()
    assert all(item.kind not in {"SHARED_ANCHOR", "SHARED_DUMMY"} for item in result.connections)


def test_self_intersecting_positive_polygon_lobes_are_not_false_joined() -> None:
    bowtie = (
        (-500.0, -500.0),
        (500.0, 500.0),
        (-500.0, 500.0),
        (500.0, -500.0),
    )
    result = extract_shared_pad_connectivity(
        (_decap("BOTTOM", -400.0), _decap("TOP", 400.0)),
        (),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (bowtie,)),
            _top_copper("DGND", (_rectangle(400.0, -600.0, 800.0, 600.0),)),
        ),
    )

    assert result.clusters == ()
    assert all(item.kind not in {"SHARED_ANCHOR", "SHARED_DUMMY"} for item in result.connections)


def test_generic_path_budget_uses_exact_final_component_proof() -> None:
    decaps = tuple(
        replace(_decap(f"C{index}", 0.0), power_x_um=index * 500.0)
        for index in range(64)
    )
    remote_voids = tuple(
        _rectangle(index * 30.0, 5000.0, index * 30.0 + 10.0, 5010.0)
        for index in range(1001)
    )
    result = extract_shared_pad_connectivity(
        decaps,
        (),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-500.0, -1000.0, 32_000.0, 6000.0),),
                negative=remote_voids,
            ),
        ),
    )

    # 2,016 candidate terminal pairs x 1,002 ordered primitives exceeds the
    # pairwise path budget.  The ordered final copper still proves that every
    # PWR terminal belongs to one component; the remote holes do not split it.
    assert len(result.clusters) == 1
    assert result.clusters[0].state == "FLOATING"
    assert result.clusters[0].member_refdes == tuple(
        sorted((f"C{index}" for index in range(64)), key=str.casefold)
    )
    assert len(result.clusters[0].power_edges) == 63


def test_finite_pads_on_outer_and_cutout_boundaries_keep_area_membership() -> None:
    outer = extract_shared_pad_connectivity(
        (_decap("OUTER", 0.0),),
        (_via("VP", "VDD", 0.0, 0.0), _via("VG", "DGND", 600.0, 0.0)),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 0.0, 200.0),)),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )
    cutout = extract_shared_pad_connectivity(
        (_decap("CUTOUT", 0.0),),
        (_via("VP", "VDD", 0.0, 0.0), _via("VG", "DGND", 600.0, 0.0)),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-200.0, -200.0, 200.0, 200.0),),
                negative=(_rectangle(0.0, -100.0, 100.0, 100.0),),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    for result in (outer, cutout):
        assert result.connections[0].kind == "DIRECT"


def test_finite_rectangle_pad_edge_only_copper_contact_fails_closed() -> None:
    decap = replace(
        _decap("EDGE-ONLY", 0.0),
        power_x_um=200.0,
    )
    result = extract_shared_pad_connectivity(
        (decap,),
        (
            _via("VP", "VDD", 200.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 0.0, 200.0),)),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    assert result.connections[0].kind == "UNRESOLVED"
    assert "boundary contact" in str(result.connections[0].reason)


def test_positive_component_membership_overrides_other_component_edge_contact() -> None:
    decap = replace(_decap("DIRECT", 0.0), power_x_um=0.0)
    result = extract_shared_pad_connectivity(
        (decap,),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (
                    # The terminal has positive area on this isolated island.
                    _rectangle(-100.0, -200.0, 100.0, 200.0),
                    # Its right edge only touches this separate island.
                    _rectangle(200.0, -200.0, 400.0, 200.0),
                ),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    # The boundary-only island is not connected through the terminal, but it
    # cannot erase the terminal's proven positive-area membership in the first
    # island.  This is the production DIRECT-owner regression.
    assert result.connections[0].kind == "DIRECT"


def test_tangent_positive_circle_does_not_bridge_separate_power_component() -> None:
    padstacks = {
        "cap": (SpdPadShape(TOP, "RECTANGLE", 100.0, 100.0),),
        "via": PADSTACKS["via"],
    }
    first = replace(
        _decap("A", 0.0),
        power_x_um=0.0,
        ground_x_um=600.0,
    )
    second = replace(
        _decap("B", 0.0),
        power_x_um=200.0,
        ground_x_um=800.0,
    )
    power_geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="VDD",
        positive_polygons_um=(_rectangle(-50.0, -50.0, 50.0, 50.0),),
        negative_polygons_um=(),
        positive_circles_um=((200.0, 0.0, 150.0),),
        primitive_order=(
            ("positive_polygon", 0),
            ("positive_circle", 0),
        ),
    )
    result = extract_shared_pad_connectivity(
        (first, second),
        (),
        padstacks,
        top_layer=TOP,
        top_copper_geometries=(
            power_geometry,
            _top_copper("DGND", (_rectangle(550.0, -50.0, 850.0, 50.0),)),
        ),
    )

    # A overlaps its rectangle by area but only touches circle B at (50, 0).
    # B is inside that circle, and the 100 um terminal gap cannot be bridged by
    # closure-only contact between A and B's distinct copper component.
    assert result.clusters == ()
    assert all(
        item.kind not in {"SHARED_ANCHOR", "SHARED_DUMMY"}
        for item in result.connections
    )


def test_pad_wholly_inside_later_nonrectangular_void_is_not_boundary() -> None:
    containing_void = (
        (-500.0, -500.0),
        (500.0, -500.0),
        (500.0, 500.0),
        (300.0, 500.0),
        (300.0, 300.0),
        (-300.0, 300.0),
        (-300.0, 500.0),
        (-500.0, 500.0),
    )
    result = extract_shared_pad_connectivity(
        (_decap("DIRECT-IN-VOID", 0.0),),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-1000.0, -1000.0, 1000.0, 1000.0),),
                negative=(containing_void,),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    # The terminal's own pad/Via branch remains valid even though a later
    # simple 8-vertex void wholly removes the board copper below it.
    assert result.connections[0].kind == "DIRECT"


def test_partial_nonrectangular_void_keeps_positive_pad_membership() -> None:
    corner_void = (
        (100.0, 100.0),
        (500.0, 100.0),
        (500.0, 500.0),
        (300.0, 500.0),
        (300.0, 300.0),
        (100.0, 300.0),
    )
    result = extract_shared_pad_connectivity(
        (_decap("PARTIAL-VOID", 0.0),),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-1000.0, -1000.0, 1000.0, 1000.0),),
                negative=(corner_void,),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    assert result.connections[0].kind == "DIRECT"


def test_positive_readd_after_containing_void_restores_pad_membership() -> None:
    geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="VDD",
        positive_polygons_um=(
            _rectangle(-1000.0, -1000.0, 1000.0, 1000.0),
            _rectangle(-100.0, -100.0, 100.0, 100.0),
        ),
        negative_polygons_um=(
            (
                (-500.0, -500.0),
                (500.0, -500.0),
                (500.0, 500.0),
                (300.0, 500.0),
                (300.0, 300.0),
                (-300.0, 300.0),
                (-300.0, 500.0),
                (-500.0, 500.0),
            ),
        ),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_polygon", 0),
            ("positive_polygon", 1),
        ),
    )
    result = extract_shared_pad_connectivity(
        (_decap("READDED", 0.0),),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            geometry,
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    assert result.connections[0].kind == "DIRECT"


def test_later_positive_with_overlapping_bbox_prevents_void_none_proof() -> None:
    # The triangle is diagonally clear of the terminal, but its bounding box
    # overlaps the terminal bbox.  Generic polygon overlap is not proven by a
    # centre test, so the fully-subtracted shortcut must remain fail-closed.
    geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="VDD",
        positive_polygons_um=(
            _rectangle(-1000.0, -1000.0, 1000.0, 1000.0),
            ((190.0, 220.0), (220.0, 190.0), (220.0, 220.0)),
        ),
        negative_polygons_um=(
            (
                (-500.0, -500.0),
                (500.0, -500.0),
                (500.0, 500.0),
                (300.0, 500.0),
                (300.0, 300.0),
                (-300.0, 300.0),
                (-300.0, 500.0),
                (-500.0, 500.0),
            ),
        ),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_polygon", 0),
            ("positive_polygon", 1),
        ),
    )
    result = extract_shared_pad_connectivity(
        (_decap("CONSERVATIVE-READD", 0.0),),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            geometry,
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    assert result.connections[0].kind == "UNRESOLVED"
    assert "unresolved overlap" in str(result.connections[0].reason)


def test_finite_rectangle_pad_corner_only_copper_contact_fails_closed() -> None:
    decap = replace(
        _decap("POINT-ONLY", 200.0),
        power_x_um=200.0,
    )
    result = extract_shared_pad_connectivity(
        (decap,),
        (
            _via("VP", "VDD", 200.0, 200.0),
            _via("VG", "DGND", 600.0, 200.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 0.0, 0.0),)),
            _top_copper("DGND", (_rectangle(400.0, 0.0, 800.0, 400.0),)),
        ),
    )

    assert result.connections[0].kind == "UNRESOLVED"
    assert "boundary contact" in str(result.connections[0].reason)


def test_finite_rectangle_pad_tangent_to_circle_fails_closed() -> None:
    geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="VDD",
        positive_polygons_um=(),
        negative_polygons_um=(),
        positive_circles_um=((0.0, 0.0, 100.0),),
        primitive_order=(("positive_circle", 0),),
    )
    decap = replace(_decap("CIRCLE-TANGENT", 0.0), power_x_um=300.0)
    result = extract_shared_pad_connectivity(
        (decap,),
        (
            _via("VP", "VDD", 300.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            geometry,
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 200.0),)),
        ),
    )

    assert result.connections[0].kind == "UNRESOLVED"
    assert "boundary contact" in str(result.connections[0].reason)


def test_c1008_style_terminal_at_rectangle_vertex_has_area_membership() -> None:
    padstacks = {
        "cap": (SpdPadShape(TOP, "RECTANGLE", 200.0, 200.0),),
        "via": PADSTACKS["via"],
    }
    decap = replace(
        _decap("C1008_0", 27_653.0),
        power_x_um=12_988.0,
        ground_x_um=13_588.0,
    )
    result = extract_shared_pad_connectivity(
        (decap,),
        (
            _via("VP", "VDD", 12_988.0, 27_653.0),
            _via("VG", "DGND", 13_588.0, 27_653.0),
        ),
        padstacks,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(12_788.0, 24_233.0, 12_988.0, 27_653.0),),
            ),
        ),
    )

    # The centre is the source rectangle's upper-right vertex, but the finite
    # terminal shares a 100 x 100 um open region with final copper.
    assert result.connections[0].kind == "DIRECT"


def _boundary_strip_result_with_later_negative_circle(
    circle: tuple[float, float, float],
):
    return extract_shared_pad_connectivity(
        (_decap("ANCHOR", 0.0), _decap("DUMMY", 450.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 200.0, 650.0),)),
            SpdTopCopperGeometry(
                layer=TOP,
                net="DGND",
                positive_polygons_um=(
                    _rectangle(400.0, -200.0, 600.0, 650.0),
                ),
                negative_polygons_um=(),
                negative_circles_um=(circle,),
                primitive_order=(
                    ("positive_polygon", 0),
                    ("negative_circle", 0),
                ),
            ),
        ),
    )


def test_remote_later_negative_bbox_keeps_whole_primitive_connectivity() -> None:
    result = _boundary_strip_result_with_later_negative_circle(
        (2_000.0, 2_000.0, 100.0)
    )

    # Both GND terminal centres lie on the strip's right boundary, so their
    # zero-width centre path is ambiguous.  The finite pads nevertheless have
    # positive area on one simple primitive, and the later void is bbox-remote.
    assert len(result.clusters) == 1
    assert result.clusters[0].member_refdes == ("ANCHOR", "DUMMY")
    assert {item.refdes: item.kind for item in result.connections} == {
        "ANCHOR": "SHARED_ANCHOR",
        "DUMMY": "SHARED_DUMMY",
    }


def test_touching_later_negative_bbox_keeps_whole_primitive_fail_closed() -> None:
    result = _boundary_strip_result_with_later_negative_circle(
        (700.0, 225.0, 100.0)
    )

    # The circle bbox touches the strip bbox at x=600.  Closure contact is not
    # enough to prove the source primitive survived whole, so no join is made.
    assert len(result.clusters) == 1
    assert result.clusters[0].state == "UNRESOLVED"
    assert result.clusters[0].ground_edges == ()


def test_overlapping_later_negative_bbox_keeps_whole_primitive_fail_closed() -> None:
    result = _boundary_strip_result_with_later_negative_circle(
        (650.0, 225.0, 100.0)
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].state == "UNRESOLVED"
    assert result.clusters[0].ground_edges == ()


def test_c1008_style_local_readd_connects_boundary_center_terminals() -> None:
    padstacks = {
        "cap": (SpdPadShape(TOP, "RECTANGLE", 200.0, 200.0),),
        "via": PADSTACKS["via"],
    }
    y_values = tuple(27_653.0 - 230.0 * index for index in range(8))
    decaps = tuple(
        replace(
            _decap(f"C{1008 + index}_0", y_um),
            power_x_um=12_988.0,
            ground_x_um=13_338.0,
        )
        for index, y_um in enumerate(y_values)
    )
    vias = tuple(
        via
        for index, y_um in enumerate(y_values)
        if index % 2 == 1
        for via in (
            _via(f"VP{index}", "VDD", 12_988.0, y_um),
            _via(f"VG{index}", "DGND", 13_338.0, y_um),
        )
    )
    ground_geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="DGND",
        positive_polygons_um=(
            _rectangle(0.0, 0.0, 30_000.0, 30_000.0),
            _rectangle(13_138.0, 24_233.0, 13_338.0, 27_653.0),
        ),
        negative_polygons_um=(
            _rectangle(13_000.0, 25_000.0, 14_000.0, 29_000.0),
        ),
        negative_circles_um=((40_000.0, 40_000.0, 100.0),),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_polygon", 0),
            ("positive_polygon", 1),
            ("negative_circle", 0),
        ),
    )
    result = extract_shared_pad_connectivity(
        decaps,
        vias,
        padstacks,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(12_788.0, 25_800.0, 12_988.0, 27_853.0),),
            ),
            ground_geometry,
        ),
    )

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.member_refdes == tuple(f"C{index}_0" for index in range(1008, 1016))
    assert cluster.state == "ANCHORED"
    assert len(cluster.ground_edges) == 7
    assert {item.kind for item in result.connections} == {
        "SHARED_ANCHOR",
        "SHARED_DUMMY",
    }


def test_disjoint_local_readds_remain_separate_ground_supernodes() -> None:
    first = _decap("LOWER", 0.0)
    second = _decap("UPPER", 1_000.0)
    ground_geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="DGND",
        positive_polygons_um=(
            _rectangle(0.0, -500.0, 1_000.0, 1_500.0),
            _rectangle(400.0, -200.0, 600.0, 200.0),
            _rectangle(400.0, 800.0, 600.0, 1_200.0),
        ),
        negative_polygons_um=(
            _rectangle(300.0, -400.0, 900.0, 1_400.0),
        ),
        negative_circles_um=((2_000.0, 2_000.0, 100.0),),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_polygon", 0),
            ("positive_polygon", 1),
            ("positive_polygon", 2),
            ("negative_circle", 0),
        ),
    )
    result = extract_shared_pad_connectivity(
        (first, second),
        (
            _via("VP0", "VDD", 0.0, 0.0),
            _via("VG0", "DGND", 600.0, 0.0),
            _via("VP1", "VDD", 0.0, 1_000.0),
            _via("VG1", "DGND", 600.0, 1_000.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 200.0, 1_200.0),)),
            ground_geometry,
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].ground_edges == ()
    assert result.clusters[0].power_edges == (("LOWER", "UPPER"),)
    assert {item.kind for item in result.connections} == {"SHARED_ANCHOR"}


def test_disconnected_ground_component_without_via_anchor_fails_closed() -> None:
    first = _decap("LOWER", 0.0)
    second = _decap("UPPER", 1_000.0)
    ground_geometry = SpdTopCopperGeometry(
        layer=TOP,
        net="DGND",
        positive_polygons_um=(
            _rectangle(0.0, -500.0, 1_000.0, 1_500.0),
            _rectangle(400.0, -200.0, 600.0, 200.0),
            _rectangle(400.0, 800.0, 600.0, 1_200.0),
        ),
        negative_polygons_um=(
            _rectangle(300.0, -400.0, 900.0, 1_400.0),
        ),
        negative_circles_um=((2_000.0, 2_000.0, 100.0),),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_polygon", 0),
            ("positive_polygon", 1),
            ("positive_polygon", 2),
            ("negative_circle", 0),
        ),
    )
    result = extract_shared_pad_connectivity(
        (first, second),
        (
            _via("VP0", "VDD", 0.0, 0.0),
            _via("VG0", "DGND", 600.0, 0.0),
            _via("VP1", "VDD", 0.0, 1_000.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-200.0, -200.0, 200.0, 1_200.0),)),
            ground_geometry,
        ),
    )

    assert len(result.clusters) == 1
    assert result.clusters[0].state == "UNRESOLVED"
    assert "GND TOP component has no source Via anchor: UPPER" in str(
        result.clusters[0].reason
    )


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


def test_disjoint_single_box_components_each_authorize_local_isolation_gaps() -> None:
    decaps = (
        _decap("A0", 0.0),
        _decap("A1", 450.0),
        _decap("A2", 900.0),
        _decap("B0", 2_000.0),
        _decap("B1", 2_450.0),
        _decap("B2", 2_900.0),
    )
    result = extract_shared_pad_connectivity(
        decaps,
        (
            _via("VP-A", "VDD", 0.0, 0.0),
            _via("VG-A", "DGND", 600.0, 0.0),
            _via("VP-B", "VDD", 0.0, 2_000.0),
            _via("VG-B", "DGND", 600.0, 2_000.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (
                    _rectangle(-200.0, -200.0, 200.0, 1_100.0),
                    _rectangle(-200.0, 1_800.0, 200.0, 3_100.0),
                ),
            ),
            _top_copper(
                "DGND",
                (
                    _rectangle(400.0, -200.0, 800.0, 1_100.0),
                    _rectangle(400.0, 1_800.0, 800.0, 3_100.0),
                ),
            ),
        ),
    )

    assert len(result.clusters) == 2
    by_members = {cluster.member_refdes: cluster for cluster in result.clusters}
    assert by_members[("A0", "A1", "A2")].isolation_gap_refdes == (
        "A0",
        "A1",
        "A2",
    )
    assert by_members[("B0", "B1", "B2")].isolation_gap_refdes == (
        "B0",
        "B1",
        "B2",
    )


def test_touching_rectangular_tiles_do_not_authorize_isolation_gaps() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("A", 0.0), _decap("B", 450.0), _decap("C", 900.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (
                    _rectangle(-200.0, -200.0, 200.0, 450.0),
                    _rectangle(-200.0, 450.0, 200.0, 1_100.0),
                ),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 1_100.0),)),
        ),
    )

    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].member_refdes == ("A", "B", "C")
    assert result.clusters[0].isolation_gap_refdes == ()


def test_rectangular_component_with_local_void_does_not_authorize_gaps() -> None:
    result = extract_shared_pad_connectivity(
        (_decap("A", 0.0), _decap("B", 450.0), _decap("C", 900.0)),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 600.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper(
                "VDD",
                (_rectangle(-200.0, -200.0, 200.0, 1_100.0),),
                negative=(_rectangle(100.0, 300.0, 150.0, 350.0),),
            ),
            _top_copper("DGND", (_rectangle(400.0, -200.0, 800.0, 1_100.0),)),
        ),
    )

    assert result.clusters[0].state == "ANCHORED"
    assert result.clusters[0].member_refdes == ("A", "B", "C")
    assert result.clusters[0].isolation_gap_refdes == ()


def test_single_box_branched_component_does_not_authorize_isolation_gaps() -> None:
    center = replace(_decap("CENTER", 0.0), ground_x_um=2_000.0)
    left = replace(
        _decap("LEFT", 0.0),
        power_x_um=-450.0,
        ground_x_um=1_550.0,
    )
    right = replace(
        _decap("RIGHT", 0.0),
        power_x_um=450.0,
        ground_x_um=2_450.0,
    )
    upper = replace(_decap("UPPER", 450.0), ground_x_um=2_000.0)
    result = extract_shared_pad_connectivity(
        (center, left, right, upper),
        (
            _via("VP", "VDD", 0.0, 0.0),
            _via("VG", "DGND", 2_000.0, 0.0),
        ),
        PADSTACKS,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-650.0, -200.0, 650.0, 650.0),)),
            _top_copper(
                "DGND",
                (_rectangle(1_350.0, -200.0, 2_650.0, 650.0),),
            ),
        ),
    )

    cluster = result.clusters[0]
    assert cluster.state == "ANCHORED"
    assert set(cluster.member_refdes) == {"CENTER", "LEFT", "RIGHT", "UPPER"}
    assert cluster.isolation_gap_refdes == ()


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
                # The void fully covers DUMMY's 400 um terminal footprint in
                # Y; unlike a centre-only model, a smaller cutout would be
                # physically bridged by the terminal pad itself.
                negative=(_rectangle(-250.0, 200.0, 250.0, 700.0),),
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


def test_lower_left_box_strip_bridges_separated_pads_and_promotes_dummy() -> None:
    small = {
        "cap": (SpdPadShape(TOP, "RECTANGLE", 200.0, 200.0),),
        "via": (SpdPadShape(TOP, "CIRCLE", 100.0, 100.0),),
    }
    evidence = tuple(
        replace(
            _decap(refdes, y),
            power_padstack="CAP",
            ground_padstack="CAP",
        )
        for refdes, y in (("C1", 0.0), ("C2", 230.0), ("C3", 460.0))
    )
    result = extract_shared_pad_connectivity(
        evidence,
        (_via("VP1", "VDD", 0.0, 0.0), _via("VP3", "VDD", 0.0, 460.0),
         _via("VG1", "DGND", 600.0, 0.0), _via("VG3", "DGND", 600.0, 460.0)),
        small,
        top_layer=TOP,
        top_copper_geometries=(
            _top_copper("VDD", (_rectangle(-150.0, -150.0, 150.0, 610.0),)),
            _top_copper("DGND", (_rectangle(450.0, -150.0, 750.0, 610.0),)),
        ),
    )
    assert result.connections[1].kind == "SHARED_DUMMY"
    assert result.connections[0].cluster_id == result.connections[1].cluster_id
    assert result.connections[2].cluster_id == result.connections[1].cluster_id
