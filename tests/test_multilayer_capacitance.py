from __future__ import annotations

from hashlib import sha256
import json
from types import SimpleNamespace
import zlib

import numpy as np
import pytest
from shapely.geometry import box

from spd_decap_pi._core.solver.multilayer_capacitance import (
    EPSILON_0_F_PER_M,
    CapacitanceArtwork,
    DielectricGap,
    MultilayerCapacitanceError,
    MultilayerCapacitanceModel,
    capacitance_model_from_project,
    extract_multilayer_bulk_capacitance,
    extract_sparse_adjacent_gap_island_capacitance,
    reduce_floating_multilayer_capacitance,
)


AREA_UM2 = 1_000.0 * 1_000.0
GAP = DielectricGap(100.0e-6, 4.0)
CAP = EPSILON_0_F_PER_M * 4.0 * AREA_UM2 * 1.0e-12 / 100.0e-6


def _model(layers: tuple[str, ...], *artwork: CapacitanceArtwork) -> MultilayerCapacitanceModel:
    return MultilayerCapacitanceModel(layers, (GAP,) * (len(layers) - 1), artwork)


def _project_geometry_asset(
    layer: str,
    net: str,
    polygon: list[list[float]],
) -> tuple[dict[str, str], bytes]:
    payload = {
        "format": "powersi-spd-plane-primitives-v1",
        "layer": layer,
        "net": net,
        "positive_polygons_um": [polygon],
        "negative_polygons_um": [],
        "positive_circles_um": [],
        "negative_circles_um": [],
        "primitive_order": [["positive_polygon", 0]],
    }
    content = zlib.compress(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    asset = f"geometry/{layer}-{net}-{sha256(content).hexdigest()[:8]}.spdgeom.zlib"
    return (
        {
            "layer": layer,
            "net": net,
            "asset": asset,
            "asset_sha256": sha256(content).hexdigest(),
        },
        content,
    )


def _project_geometry_asset_with_polygons(
    layer: str,
    net: str,
    polygons: list[list[list[float]]],
) -> tuple[dict[str, str], bytes]:
    payload = {
        "format": "powersi-spd-plane-primitives-v1",
        "layer": layer,
        "net": net,
        "positive_polygons_um": polygons,
        "negative_polygons_um": [],
        "positive_circles_um": [],
        "negative_circles_um": [],
        "primitive_order": [
            ["positive_polygon", index] for index in range(len(polygons))
        ],
    }
    content = zlib.compress(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    asset = f"geometry/{layer}-{net}-{sha256(content).hexdigest()[:8]}.spdgeom.zlib"
    return (
        {
            "layer": layer,
            "net": net,
            "asset": asset,
            "asset_sha256": sha256(content).hexdigest(),
        },
        content,
    )


def _project_with_geometry(
    *items: tuple[dict[str, str], bytes],
) -> tuple[SimpleNamespace, dict[str, bytes]]:
    records = tuple(record for record, _content in items)
    return (
        SimpleNamespace(
            stackup_layers=(
                SimpleNamespace(name="L0", is_conductor=True, thickness_um=35.0),
                SimpleNamespace(
                    name="D0",
                    is_conductor=False,
                    thickness_um=100.0,
                    dk=4.0,
                ),
                SimpleNamespace(name="L1", is_conductor=True, thickness_um=35.0),
            ),
            metadata={"spd_import": {"plane_geometries": records}},
        ),
        {record["asset"]: content for record, content in items},
    )


def test_two_conductor_exact_overlap_builds_maxwell_matrix() -> None:
    result = extract_multilayer_bulk_capacitance(
        _model(
            ("L0", "L1"),
            CapacitanceArtwork("L0", "P", box(0, 0, 1000, 1000)),
            CapacitanceArtwork("L1", "DGND", box(0, 0, 1000, 1000)),
        ),
        selected_nets=("P",),
    )
    np.testing.assert_allclose(result.maxwell_capacitance_f, ((CAP, -CAP), (-CAP, CAP)))
    np.testing.assert_allclose(result.grounded_capacitance_f, ((CAP,),))
    np.testing.assert_allclose(result.selected_capacitance_f, ((CAP,),))
    assert result.maxwell_capacitance_f.sum(axis=1) == pytest.approx((0.0, 0.0), abs=1e-22)
    with pytest.raises(ValueError):
        result.maxwell_capacitance_f[0, 0] = 0.0
    with pytest.raises(TypeError):
        result.pair_capacitance_f[("DGND", "P")] = 0.0  # type: ignore[index]


def test_three_conductor_middle_net_has_two_adjacent_bulk_terms() -> None:
    result = extract_multilayer_bulk_capacitance(
        _model(
            ("L0", "L1", "L2"),
            CapacitanceArtwork("L0", "DGND", box(0, 0, 1000, 1000)),
            CapacitanceArtwork("L1", "P", box(0, 0, 1000, 1000)),
            CapacitanceArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
        ),
        selected_nets=("P",),
    )
    np.testing.assert_allclose(result.selected_capacitance_f, ((2.0 * CAP,),))
    assert result.nearest_visible_pair_count == 0


def test_four_conductors_floating_net_is_schur_complemented_not_grounded() -> None:
    model = _model(
        ("L0", "L1", "L2", "L3"),
        CapacitanceArtwork("L0", "DGND", box(0, 0, 1000, 1000)),
        CapacitanceArtwork("L1", "A", box(0, 0, 1000, 1000)),
        CapacitanceArtwork("L2", "B", box(0, 0, 1000, 1000)),
        CapacitanceArtwork("L3", "DGND", box(0, 0, 1000, 1000)),
    )
    pair = extract_multilayer_bulk_capacitance(model, selected_nets=("A", "B"))
    np.testing.assert_allclose(pair.selected_capacitance_f, ((2 * CAP, -CAP), (-CAP, 2 * CAP)))
    reduced = extract_multilayer_bulk_capacitance(model, selected_nets=("A",))
    np.testing.assert_allclose(reduced.selected_capacitance_f, ((1.5 * CAP,),))
    base = extract_multilayer_bulk_capacitance(model)
    names, cached = reduce_floating_multilayer_capacitance(base, ("A",))
    assert names == ("A",)
    np.testing.assert_allclose(cached, reduced.selected_capacitance_f)


def test_same_layer_disjoint_nets_are_separate_global_unknowns() -> None:
    result = extract_multilayer_bulk_capacitance(
        _model(
            ("TOP", "BOT"),
            CapacitanceArtwork("TOP", "P_LEFT", box(0, 0, 500, 1000)),
            CapacitanceArtwork("TOP", "P_RIGHT", box(500, 0, 1000, 1000)),
            CapacitanceArtwork("BOT", "DGND", box(0, 0, 1000, 1000)),
        ),
        selected_nets=("P_LEFT", "P_RIGHT"),
    )
    half = CAP / 2.0
    np.testing.assert_allclose(result.selected_capacitance_f, ((half, 0.0), (0.0, half)))
    assert set(result.grounded_net_names) == {"P_LEFT", "P_RIGHT"}


def test_true_intermediate_opening_is_disabled_by_default() -> None:
    # The right half is an opening, but the SPD stack does not establish the
    # material through L1's nominal conductor thickness. The safe default is
    # an adjacent-only lower bound, not a guessed two-gap field path.
    result = extract_multilayer_bulk_capacitance(
        _model(
            ("L0", "L1", "L2"),
            CapacitanceArtwork("L0", "P", box(0, 0, 1000, 1000)),
            CapacitanceArtwork("L1", "MID", box(0, 0, 500, 1000)),
            CapacitanceArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
        ),
        selected_nets=("P", "MID"),
    )
    assert result.nearest_visible_pair_count == 0
    assert not result.nonadjacent_opening_coupling_evaluated
    assert ("DGND", "P") not in result.pair_capacitance_f
    np.testing.assert_allclose(result.selected_capacitance_f, ((CAP / 2.0, -CAP / 2.0), (-CAP / 2.0, CAP)))


def test_nonadjacent_opening_fails_closed_without_source_proven_fill() -> None:
    model = MultilayerCapacitanceModel(
        ("L0", "L1", "L2"),
        (GAP, GAP),
        (
            CapacitanceArtwork("L0", "P", box(0, 0, 1000, 1000)),
            CapacitanceArtwork("L1", "MID", box(0, 0, 500, 1000)),
            CapacitanceArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
        ),
        enable_nonadjacent_opening_coupling=True,
    )
    with pytest.raises(MultilayerCapacitanceError, match="no source-proven opening-fill"):
        extract_multilayer_bulk_capacitance(model)


def test_explicit_opening_fill_includes_nominal_conductor_thickness_once() -> None:
    # The right half is an opening. Its dielectric path is 100 + 20 + 100 um,
    # not the incorrect 200 um sum that silently drops L1 thickness.
    model = MultilayerCapacitanceModel(
        ("L0", "L1", "L2"),
        (GAP, GAP),
        (
            CapacitanceArtwork("L0", "P", box(0, 0, 1000, 1000)),
            CapacitanceArtwork("L1", "MID", box(0, 0, 500, 1000)),
            CapacitanceArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
        ),
        opening_fill_by_layer={"L1": DielectricGap(20.0e-6, 4.0)},
        enable_nonadjacent_opening_coupling=True,
    )
    result = extract_multilayer_bulk_capacitance(model, selected_nets=("P", "MID"))
    visible = CAP * 5.0 / 22.0  # 0.5 area * 100 / (100 + 20 + 100) um
    assert result.nearest_visible_pair_count == 1
    assert result.nonadjacent_opening_coupling_evaluated
    assert result.pair_capacitance_f[("DGND", "P")] == pytest.approx(visible)
    np.testing.assert_allclose(result.selected_capacitance_f, ((CAP / 2.0 + visible, -CAP / 2.0), (-CAP / 2.0, CAP)))


def test_same_layer_overlap_fails_closed_and_matrix_is_symmetric_psd() -> None:
    with pytest.raises(MultilayerCapacitanceError, match="ambiguous/shorted artwork"):
        extract_multilayer_bulk_capacitance(
            _model(
                ("TOP", "BOT"),
                CapacitanceArtwork("TOP", "A", box(0, 0, 800, 1000)),
                CapacitanceArtwork("TOP", "B", box(200, 0, 1000, 1000)),
                CapacitanceArtwork("BOT", "DGND", box(0, 0, 1000, 1000)),
            )
        )
    result = extract_multilayer_bulk_capacitance(
        _model(
            ("TOP", "BOT"),
            CapacitanceArtwork("TOP", "A", box(0, 0, 500, 1000)),
            CapacitanceArtwork("TOP", "B", box(500, 0, 1000, 1000)),
            CapacitanceArtwork("BOT", "DGND", box(0, 0, 1000, 1000)),
        )
    )
    np.testing.assert_allclose(result.maxwell_capacitance_f, result.maxwell_capacitance_f.T)
    np.testing.assert_allclose(result.maxwell_capacitance_f.sum(axis=1), 0.0, atol=1e-22)
    assert np.linalg.eigvalsh(result.maxwell_capacitance_f).min() >= -1e-22


def test_progress_checkpoints_preserve_exact_extraction_result() -> None:
    model = _model(
        ("L0", "L1", "L2"),
        CapacitanceArtwork("L0", "DGND", box(0, 0, 1000, 1000)),
        CapacitanceArtwork("L1", "P", box(0, 0, 1000, 1000)),
        CapacitanceArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
    )
    expected = extract_multilayer_bulk_capacitance(model, selected_nets=("P",))
    updates: list[tuple[int, str]] = []

    observed = extract_multilayer_bulk_capacitance(
        model,
        selected_nets=("P",),
        progress=lambda value, message: updates.append((value, message)),
        is_cancelled=lambda: False,
    )

    np.testing.assert_array_equal(
        observed.maxwell_capacitance_f, expected.maxwell_capacitance_f
    )
    np.testing.assert_array_equal(
        observed.selected_capacitance_f, expected.selected_capacitance_f
    )
    assert updates[0][0] == 0
    assert updates[-1][0] == 100
    assert [value for value, _message in updates] == sorted(
        value for value, _message in updates
    )


def test_exact_extraction_honors_cancellation_inside_geometry_work() -> None:
    model = _model(
        ("L0", "L1", "L2"),
        CapacitanceArtwork("L0", "DGND", box(0, 0, 1000, 1000)),
        CapacitanceArtwork("L1", "P", box(0, 0, 1000, 1000)),
        CapacitanceArtwork("L2", "DGND", box(0, 0, 1000, 1000)),
    )
    checks = 0

    def cancel_during_geometry() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 6

    with pytest.raises(RuntimeError, match="evaluation cancelled"):
        extract_multilayer_bulk_capacitance(
            model,
            is_cancelled=cancel_during_geometry,
        )
    assert checks == 6


def test_deferred_project_artwork_validation_preserves_exact_extraction() -> None:
    rectangle = [[0.0, 0.0], [1000.0, 0.0], [1000.0, 1000.0], [0.0, 1000.0]]
    project, attachments = _project_with_geometry(
        _project_geometry_asset("L0", "DGND", rectangle),
        _project_geometry_asset("L1", "P", rectangle),
    )
    validated = capacitance_model_from_project(
        project,
        attachments,
        ("L0", "L1"),
    )
    deferred = capacitance_model_from_project(
        project,
        attachments,
        ("L0", "L1"),
        validate_artwork=False,
    )

    expected = extract_multilayer_bulk_capacitance(validated)
    observed = extract_multilayer_bulk_capacitance(deferred)

    np.testing.assert_array_equal(
        observed.maxwell_capacitance_f,
        expected.maxwell_capacitance_f,
    )
    assert observed.pair_capacitance_f == expected.pair_capacitance_f
    assert len(observed.adjacent_gap_partials) == len(
        expected.adjacent_gap_partials
    )
    for actual, reference in zip(
        observed.adjacent_gap_partials,
        expected.adjacent_gap_partials,
        strict=True,
    ):
        assert actual.upper_layer == reference.upper_layer
        assert actual.lower_layer == reference.lower_layer
        assert actual.net_names == reference.net_names
        np.testing.assert_array_equal(
            actual.maxwell_capacitance_f,
            reference.maxwell_capacitance_f,
        )


def test_project_island_mode_preserves_disconnected_same_net_polygons() -> None:
    left = [[0.0, 0.0], [400.0, 0.0], [400.0, 1000.0], [0.0, 1000.0]]
    right = [
        [600.0, 0.0],
        [1000.0, 0.0],
        [1000.0, 1000.0],
        [600.0, 1000.0],
    ]
    full = [[0.0, 0.0], [1000.0, 0.0], [1000.0, 1000.0], [0.0, 1000.0]]
    project, attachments = _project_with_geometry(
        _project_geometry_asset_with_polygons("L0", "P", [left, right]),
        _project_geometry_asset("L1", "DGND", full),
    )

    model = capacitance_model_from_project(
        project,
        attachments,
        ("L0", "L1"),
        validate_artwork=False,
        island_resolved=True,
    )
    partials = extract_sparse_adjacent_gap_island_capacitance(model)

    power = [item for item in model.artwork if item.net == "P"]
    assert len(power) == 2
    assert len({item.node_name for item in power}) == 2
    assert all(
        item.node_name
        and item.node_name.startswith("spd-surface-island:")
        and len(item.node_name.partition(":")[2]) == 24
        for item in power
    )
    assert len(partials) == 1
    assert set(item.node_name for item in model.artwork) == set(
        partials[0].net_names
    )


def test_deferred_project_artwork_validation_remains_fail_closed() -> None:
    left = [[0.0, 0.0], [800.0, 0.0], [800.0, 1000.0], [0.0, 1000.0]]
    right = [[200.0, 0.0], [1000.0, 0.0], [1000.0, 1000.0], [200.0, 1000.0]]
    full = [[0.0, 0.0], [1000.0, 0.0], [1000.0, 1000.0], [0.0, 1000.0]]
    project, attachments = _project_with_geometry(
        _project_geometry_asset("L0", "A", left),
        _project_geometry_asset("L0", "B", right),
        _project_geometry_asset("L1", "DGND", full),
    )

    with pytest.raises(
        MultilayerCapacitanceError,
        match="ambiguous/shorted artwork",
    ):
        capacitance_model_from_project(project, attachments, ("L0", "L1"))

    deferred = capacitance_model_from_project(
        project,
        attachments,
        ("L0", "L1"),
        validate_artwork=False,
    )
    with pytest.raises(
        MultilayerCapacitanceError,
        match="ambiguous/shorted artwork",
    ):
        extract_multilayer_bulk_capacitance(deferred)
