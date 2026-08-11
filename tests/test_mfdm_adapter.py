from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import box

from spd_decap_pi._core.solver.mfdm import MfdmMaterial, compile_mfdm_operator
from spd_decap_pi._core.solver.mfdm_adapter import (
    MfdmArtwork,
    MfdmArtworkAdapterError,
    MfdmWeightedPort,
    build_mfdm_artwork_raster,
    solve_mfdm_weighted_ports,
)


def _material() -> MfdmMaterial:
    return MfdmMaterial(
        100e-6,
        100e-6,
        4.0,
        4.0,
        gap_separations_m=(100e-6,),
        gap_relative_permittivities=(4.0,),
        gap_loss_tangents=(0.0,),
    )


def test_same_layer_nets_are_quantified_and_fail_closed_instead_of_unioned() -> None:
    result = build_mfdm_artwork_raster(
        (
            MfdmArtwork("L1", "PWR_A", box(0, 0, 800, 1000)),
            MfdmArtwork("L1", "PWR_B", box(200, 0, 1000, 1000)),
            MfdmArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
        ),
        layer_order=("L1", "L2"),
        cell_um=1000.0,
    )
    assert not result.is_representable
    assert result.diagnostics.mixed_cell_count == 1
    assert result.diagnostics.mixed_area_um2_per_layer[0] == pytest.approx(800_000.0)
    with pytest.raises(MfdmArtworkAdapterError, match="do not union different nets"):
        result.require_geometry()


def test_cut_cell_area_and_weighted_port_are_exact_for_common_artwork() -> None:
    result = build_mfdm_artwork_raster(
        (
            MfdmArtwork("TOP", "P", box(0, 0, 2000, 1000)),
            MfdmArtwork("BOT", "G", box(0, 0, 2000, 1000)),
        ),
        layer_order=("TOP", "BOT"),
        cell_um=1000.0,
    )
    geometry = result.require_geometry()
    np.testing.assert_allclose(geometry.gap_overlap_fractions, np.ones((1, 1, 2)))
    operator = compile_mfdm_operator(geometry, _material())
    weighted = solve_mfdm_weighted_ports(
        operator,
        result,
        10e6,
        (MfdmWeightedPort("whole", 0, 1, box(0, 0, 2000, 1000)),),
    )
    assert weighted.local_port_count == 2
    assert weighted.support_fractions == pytest.approx((1.0,))
    assert weighted.impedance_ohm.shape == (1, 1)
    assert np.isfinite(weighted.impedance_ohm[0, 0])
    assert not result.cell_bounds_um[0].flags.writeable
    assert not weighted.impedance_ohm.flags.writeable
    with pytest.raises(TypeError):
        result.artwork_labels[("TOP", "other")] = 9  # type: ignore[index]


def test_weighted_port_does_not_count_a_nominal_cell_when_common_artwork_is_a_sliver() -> None:
    # TOP and BOT both occupy the raster cell, but their exact overlap is only
    # 1% of the requested footprint.  A fractions-only implementation would
    # falsely report 100% P/G terminal support.
    result = build_mfdm_artwork_raster(
        (
            MfdmArtwork("TOP", "P", box(0, 0, 100, 1000)),
            MfdmArtwork("BOT", "G", box(90, 0, 1000, 1000)),
        ),
        layer_order=("TOP", "BOT"),
        cell_um=1000.0,
    )
    operator = compile_mfdm_operator(result.require_geometry(), _material())
    with pytest.raises(MfdmArtworkAdapterError, match="only 1.000000% co-located P/G artwork support"):
        solve_mfdm_weighted_ports(
            operator,
            result,
            10e6,
            (MfdmWeightedPort("bad", 0, 1, box(0, 0, 1000, 1000)),),
        )


def test_multilayer_adapter_retains_noncoincident_shared_gap_edge_widths_exactly() -> None:
    # The middle sheet crosses the cell face, but its upper and lower gap
    # fields occupy different portions of it.  A one-scalar sheet factor would
    # needs the exact common-support term rather than a scalar min(coverage).
    result = build_mfdm_artwork_raster(
        (
            MfdmArtwork("L0", "UP", box(0, 0, 2000, 500)),
            MfdmArtwork("L1", "MID", box(0, 0, 2000, 1000)),
            MfdmArtwork("L2", "LOW", box(0, 500, 2000, 1000)),
        ),
        layer_order=("L0", "L1", "L2"),
        cell_um=1000.0,
    )
    assert result.is_representable
    assert result.diagnostics.shared_edge_check_performed
    geometry = result.require_geometry()
    np.testing.assert_allclose(geometry.horizontal_gap_edge_overlap_fractions[:, 0, 0], (0.5, 0.5))
    np.testing.assert_allclose(geometry.horizontal_adjacent_gap_overlap_fractions[:, 0, 0], (0.0,))
    strips = geometry.horizontal_edge_strips[0][0]
    assert [strip.width_fraction for strip in strips] == pytest.approx((0.5, 0.5))
    assert strips[0].active_gap_ids == (0,)
    assert strips[1].active_gap_ids == (1,)


def test_adapter_partitions_multisegment_boundary_without_merging_disjoint_support() -> None:
    # TOP has two separated crossings of the same boundary; the partition
    # keeps the empty interval rather than collapsing its total 50% width.
    result = build_mfdm_artwork_raster(
        (
            MfdmArtwork("TOP", "P", box(0, 0, 2000, 250).union(box(0, 750, 2000, 1000))),
            MfdmArtwork("BOT", "G", box(0, 0, 2000, 1000)),
        ),
        layer_order=("TOP", "BOT"),
        cell_um=1000.0,
    )
    geometry = result.require_geometry()
    strips = geometry.horizontal_edge_strips[0][0]
    assert [strip.width_fraction for strip in strips] == pytest.approx((0.25, 0.5, 0.25))
    assert [strip.active_gap_ids for strip in strips] == [(0,), (), (0,)]


def test_one_sided_polygon_boundaries_are_not_lateral_crossings() -> None:
    # Both TOP pieces touch x=1000, but neither crosses it: the left piece ends
    # below y=400 and the right piece begins above y=600.  Line-intersection
    # ``covers`` used to invent 80% edge support here.
    top = box(0, 0, 1000, 400).union(box(1000, 600, 2000, 1000))
    result = build_mfdm_artwork_raster(
        (
            MfdmArtwork("TOP", "P", top),
            MfdmArtwork("BOT", "G", box(0, 0, 2000, 1000)),
        ),
        layer_order=("TOP", "BOT"),
        cell_um=1000.0,
    )
    geometry = result.require_geometry()
    assert geometry.horizontal_edge_fractions[0, 0, 0] == 0.0
    assert geometry.horizontal_gap_edge_overlap_fractions[0, 0, 0] == 0.0
    assert all(strip.active_gap_ids == () for strip in geometry.horizontal_edge_strips[0][0])
