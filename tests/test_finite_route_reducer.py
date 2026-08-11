from __future__ import annotations

from math import pi

import pytest

from spd_decap_pi._core.solver.finite_route_reducer import (
    FiniteRouteEdge,
    FiniteRouteNode,
    FiniteRouteReductionError,
    reduce_finite_route_csr,
    reduce_finite_route_graph,
)
from spd_decap_pi._core.solver.layer_surface_network import LayerSurfaceViaLink


def _node(
    node_id: str,
    layer: str,
    *roles: str,
) -> FiniteRouteNode:
    return FiniteRouteNode(node_id, layer, tuple(roles))


def _edge(
    edge_id: str,
    first: FiniteRouteNode,
    second: FiniteRouteNode,
    resistance: float,
    inductance: float,
    *owners: str,
) -> FiniteRouteEdge:
    return FiniteRouteEdge(
        edge_id=edge_id,
        first_node_id=first.node_id,
        second_node_id=second.node_id,
        first_layer=first.layer,
        second_layer=second.layer,
        resistance_ohm=resistance,
        inductance_h=inductance,
        length_um=100.0,
        owner_ids=tuple(owners or (f"Via-{edge_id}",)),
    )


def test_boundary_to_boundary_bridge_chain_contracts_to_exact_series_rl() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2")
    c = _node("C", "L3")
    d = _node("D", "L4", "artwork")
    edges = (
        _edge("e1", a, b, 0.01, 0.2e-9, "Via1"),
        _edge("e2", b, c, 0.02, 0.3e-9, "Via2"),
        _edge("e3", c, d, 0.04, 0.7e-9, "Via3"),
    )

    result = reduce_finite_route_graph(
        (a, b, c, d), edges, source_identity="source-sha"
    )

    assert tuple(node.node_id for node in result.retained_nodes) == ("A", "D")
    assert len(result.reduced_edges) == 1
    reduced = result.reduced_edges[0]
    assert reduced.mode == "contracted_series"
    assert reduced.route_node_ids == ("A", "B", "C", "D")
    assert reduced.route_layers == ("L1", "L2", "L3", "L4")
    assert reduced.source_edge_ids == ("e1", "e2", "e3")
    assert reduced.owner_ids == ("Via1", "Via2", "Via3")
    assert reduced.resistance_ohm == pytest.approx(0.07)
    assert reduced.inductance_h == pytest.approx(1.2e-9)
    frequency = 83.0e6
    observed = reduced.resistance_ohm + 1j * 2.0 * pi * frequency * reduced.inductance_h
    expected = sum(
        edge.resistance_ohm + 1j * 2.0 * pi * frequency * edge.inductance_h
        for edge in edges
    )
    assert observed == pytest.approx(expected, rel=1e-14, abs=1e-18)
    assert result.statistics["degree2_contracted_node_count"] == 2
    assert result.statistics["raw_owner_count"] == 3
    assert {item.kind for item in result.owner_ledger.values()} == {
        "contracted_series"
    }


def test_external_terminal_supernode_maps_directly_to_layer_surface_link() -> None:
    terminal = _node("terminal:SITE0:1", "TOP", "device_supernode", "port")
    l02 = _node("quotient:L02:2260476", "L02")
    l03 = _node("quotient:L03:2260475", "L03")
    artwork = _node("surface:L04:rail-island", "L04", "artwork")
    edges = (
        _edge("Via611048", terminal, l02, 0.003, 0.08e-9, "Via611048"),
        _edge("Via611047", l02, l03, 0.004, 0.09e-9, "Via611047"),
        _edge("Via611046", l03, artwork, 0.005, 0.10e-9, "Via611046"),
    )

    result = reduce_finite_route_graph((terminal, l02, l03, artwork), edges)

    assert tuple(node.node_id for node in result.retained_nodes) == (
        "surface:L04:rail-island",
        "terminal:SITE0:1",
    )
    assert len(result.reduced_edges) == 1
    reduced = result.reduced_edges[0]
    assert {
        reduced.first_node_id,
        reduced.second_node_id,
    } == {"terminal:SITE0:1", "surface:L04:rail-island"}
    assert reduced.owner_ids == ("Via611046", "Via611047", "Via611048")

    link = LayerSurfaceViaLink(**reduced.layer_surface_via_link_kwargs())

    assert link.link_id == reduced.reduced_edge_id
    assert {link.first_node_id, link.second_node_id} == {
        "terminal:SITE0:1",
        "surface:L04:rail-island",
    }
    assert link.count == 1
    assert link.mode == "finite_parallel_rl"
    assert link.resistance_ohm_per_via == pytest.approx(0.012)
    assert link.inductance_h_per_via == pytest.approx(0.27e-9)
    assert link.owner_ids == ("Via611046", "Via611047", "Via611048")


def test_nonboundary_dangling_tree_is_pruned_before_series_contraction() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2")
    d = _node("D", "L3", "artwork")
    x = _node("X", "L4")
    y = _node("Y", "L5")
    edges = (
        _edge("main-a", a, b, 0.01, 0.1e-9, "ViaMainA"),
        _edge("main-b", b, d, 0.02, 0.2e-9, "ViaMainB"),
        _edge("tail-a", b, x, 0.03, 0.3e-9, "ViaTailA"),
        _edge("tail-b", x, y, 0.04, 0.4e-9, "ViaTailB"),
    )

    result = reduce_finite_route_graph((a, b, d, x, y), edges)

    assert tuple(item.source_edge_id for item in result.pruned_edges) == (
        "tail-a",
        "tail-b",
    )
    assert result.pruned_node_ids == ("X", "Y")
    assert len(result.reduced_edges) == 1
    assert result.reduced_edges[0].source_edge_ids == ("main-a", "main-b")
    assert result.reduced_edges[0].resistance_ohm == pytest.approx(0.03)
    assert result.owner_ledger["ViaTailA"].kind == "pruned_dangling"
    assert result.owner_ledger["ViaTailA"].reduced_edge_id is None
    assert result.owner_ledger["ViaTailB"].kind == "pruned_dangling"
    assert result.statistics["pruned_edge_count"] == 2
    assert result.statistics["pruned_owner_count"] == 2


def test_parallel_edges_remain_distinct_explicit_mna_branches() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2", "artwork")
    first = _edge("parallel-a", a, b, 0.02, 0.5e-9, "ViaA")
    second = _edge("parallel-b", a, b, 0.03, 0.8e-9, "ViaB")

    result = reduce_finite_route_graph((a, b), (first, second))

    assert len(result.reduced_edges) == 2
    assert all(item.mode == "retained_explicit" for item in result.reduced_edges)
    assert {item.source_edge_ids for item in result.reduced_edges} == {
        ("parallel-a",),
        ("parallel-b",),
    }
    assert result.statistics["parallel_reduced_pair_count"] == 1
    assert result.statistics["bridge_edge_count"] == 0
    frequency = 31.0e6
    impedances = [
        edge.resistance_ohm + 1j * 2.0 * pi * frequency * edge.inductance_h
        for edge in result.reduced_edges
    ]
    observed = 1.0 / sum(1.0 / value for value in impedances)
    expected = 1.0 / (
        1.0 / (0.02 + 1j * 2.0 * pi * frequency * 0.5e-9)
        + 1.0 / (0.03 + 1j * 2.0 * pi * frequency * 0.8e-9)
    )
    assert observed == pytest.approx(expected, rel=1e-14, abs=1e-18)


def test_cycle_degree_two_vertices_and_edges_remain_explicit() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2")
    c = _node("C", "L3")
    edges = (
        _edge("ab", a, b, 0.01, 0.1e-9),
        _edge("bc", b, c, 0.02, 0.2e-9),
        _edge("ca", c, a, 0.03, 0.3e-9),
    )

    result = reduce_finite_route_graph((a, b, c), edges)

    assert tuple(node.node_id for node in result.retained_nodes) == ("A", "B", "C")
    assert len(result.reduced_edges) == 3
    assert all(item.mode == "retained_explicit" for item in result.reduced_edges)
    assert result.statistics["cycle_or_parallel_edge_count"] == 3
    assert result.statistics["cycle_or_parallel_node_count"] == 3
    assert result.statistics["degree2_contracted_node_count"] == 0


def test_degree_three_branch_vertex_is_not_contracted() -> None:
    center = _node("CENTER", "L1")
    leaves = (
        _node("A", "L2", "port"),
        _node("B", "L3", "artwork"),
        _node("C", "L4", "termination"),
    )
    edges = tuple(
        _edge(f"e-{leaf.node_id}", center, leaf, 0.01, 0.1e-9)
        for leaf in leaves
    )

    result = reduce_finite_route_graph((center, *leaves), edges)

    assert {node.node_id for node in result.retained_nodes} == {
        "CENTER",
        "A",
        "B",
        "C",
    }
    assert len(result.reduced_edges) == 3
    assert result.statistics["degree_gt2_node_count"] == 1
    assert result.statistics["degree2_contracted_node_count"] == 0


def test_raw_owner_must_have_one_global_input_edge() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2")
    c = _node("C", "L3", "artwork")
    with pytest.raises(FiniteRouteReductionError, match="assigned to both"):
        reduce_finite_route_graph(
            (a, b, c),
            (
                _edge("ab", a, b, 0.01, 0.1e-9, "ViaDuplicate"),
                _edge("bc", b, c, 0.01, 0.1e-9, "viaduplicate"),
            ),
        )


@pytest.mark.parametrize(
    ("resistance", "inductance", "length", "message"),
    (
        (-0.01, 1.0e-9, 10.0, "finite passive"),
        (0.01, -1.0e-9, 10.0, "finite passive"),
        (0.0, 0.0, 10.0, "finite passive"),
        (0.01, 1.0e-9, 0.0, "finite passive"),
    ),
)
def test_nonpassive_or_zero_length_edge_fails_closed(
    resistance: float,
    inductance: float,
    length: float,
    message: str,
) -> None:
    with pytest.raises(FiniteRouteReductionError, match=message):
        FiniteRouteEdge(
            "bad",
            "A",
            "B",
            "L1",
            "L2",
            resistance,
            inductance,
            length,
            ("ViaBad",),
        )


def test_endpoint_layer_continuity_mismatch_fails_closed() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2", "artwork")
    bad = FiniteRouteEdge(
        "bad-layer",
        "A",
        "B",
        "L1",
        "L3",
        0.01,
        0.1e-9,
        100.0,
        ("ViaBad",),
    )
    with pytest.raises(FiniteRouteReductionError, match="layer evidence disagrees"):
        reduce_finite_route_graph((a, b), (bad,))


def test_csr_adapter_matches_stream_api_and_rejects_bad_reciprocity() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2")
    c = _node("C", "L3", "artwork")
    edges = (
        _edge("ab", a, b, 0.01, 0.1e-9),
        _edge("bc", b, c, 0.02, 0.2e-9),
    )
    expected = reduce_finite_route_graph(
        (a, b, c), edges, source_identity="same-source"
    )
    observed = reduce_finite_route_csr(
        (a, b, c),
        edges,
        (0, 1, 3, 4),
        (1, 0, 2, 1),
        (0, 0, 1, 1),
        source_identity="same-source",
    )
    assert observed == expected

    with pytest.raises(FiniteRouteReductionError, match="disagrees|reciprocal"):
        reduce_finite_route_csr(
            (a, b, c),
            edges,
            (0, 1, 3, 4),
            (1, 0, 2, 0),
            (0, 0, 1, 1),
        )


def test_input_permutation_has_identical_certificate_and_routes() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2")
    c = _node("C", "L3", "artwork")
    first = _edge("z-edge", a, b, 0.01, 0.1e-9, "ViaZ")
    second = _edge("a-edge", b, c, 0.02, 0.2e-9, "ViaA")

    ordinary = reduce_finite_route_graph(
        (a, b, c), (first, second), source_identity="source"
    )
    permuted = reduce_finite_route_graph(
        (c, a, b), (second, first), source_identity="source"
    )

    assert ordinary == permuted
    assert ordinary.evidence_sha256 == permuted.evidence_sha256


def test_evidence_hash_covers_raw_edge_values_not_only_equivalent_sum() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2")
    c = _node("C", "L3", "artwork")
    first = reduce_finite_route_graph(
        (a, b, c),
        (
            _edge("ab", a, b, 0.01, 0.1e-9, "ViaA"),
            _edge("bc", b, c, 0.02, 0.2e-9, "ViaB"),
        ),
    )
    redistributed = reduce_finite_route_graph(
        (a, b, c),
        (
            _edge("ab", a, b, 0.015, 0.15e-9, "ViaA"),
            _edge("bc", b, c, 0.015, 0.15e-9, "ViaB"),
        ),
    )

    assert first.reduced_edges[0].resistance_ohm == pytest.approx(
        redistributed.reduced_edges[0].resistance_ohm
    )
    assert first.reduced_edges[0].inductance_h == pytest.approx(
        redistributed.reduced_edges[0].inductance_h
    )
    assert first.evidence_sha256 != redistributed.evidence_sha256


def test_csr_rejects_float_indices_instead_of_truncating_them() -> None:
    a = _node("A", "L1", "port")
    b = _node("B", "L2", "artwork")
    edge = _edge("ab", a, b, 0.01, 0.1e-9)

    with pytest.raises(FiniteRouteReductionError, match="exact integers"):
        reduce_finite_route_csr(
            (a, b),
            (edge,),
            (0, 1, 2),
            (1.5, 0),
            (0, 0),
        )


def test_boundary_isolated_node_is_retained_while_unowned_tree_is_fully_pruned() -> None:
    boundary = _node("PORT", "L1", "port")
    a = _node("A", "L2")
    b = _node("B", "L3")
    edge = _edge("ab", a, b, 0.01, 0.1e-9, "ViaAB")

    result = reduce_finite_route_graph((boundary, a, b), (edge,))

    assert tuple(node.node_id for node in result.retained_nodes) == ("PORT",)
    assert result.reduced_edges == ()
    assert result.pruned_node_ids == ("A", "B")
    assert result.owner_ledger["ViaAB"].kind == "pruned_dangling"
