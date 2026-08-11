from __future__ import annotations

from threading import Event, Lock, current_thread
from time import monotonic, sleep

import numpy as np
import pytest
from scipy.sparse import csc_matrix, isspmatrix
from shapely.geometry import MultiPolygon, Polygon, box
import shapely.strtree as shapely_strtree

import spd_decap_pi._core.solver.multilayer_capacitance as capacitance_module
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfaceNetworkError,
    LayerSurfacePort,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.modal import DielectricDispersion
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
    CapacitanceArtwork,
    DielectricGap,
    MultilayerCapacitanceModel,
    extract_multilayer_bulk_capacitance,
    extract_sparse_adjacent_gap_island_capacitance,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap


GAP = DielectricGap(100.0e-6, 4.0)


def _model(*artwork: CapacitanceArtwork) -> MultilayerCapacitanceModel:
    return MultilayerCapacitanceModel(("TOP", "BOT"), (GAP,), artwork)


def _dense_in_order(
    partial: AdjacentGapMaxwellPartial,
    names: tuple[str, ...],
) -> np.ndarray:
    source = (
        partial.maxwell_capacitance_f.toarray()
        if isspmatrix(partial.maxwell_capacitance_f)
        else np.asarray(partial.maxwell_capacitance_f)
    )
    positions = {name: index for index, name in enumerate(partial.net_names)}
    order = np.asarray([positions[name] for name in names], dtype=np.int64)
    return source[np.ix_(order, order)]


def test_sparse_island_partial_matches_legacy_dense_small_fixture() -> None:
    model = _model(
        CapacitanceArtwork("TOP", "A", box(0, 0, 500, 1000), "A"),
        CapacitanceArtwork("TOP", "B", box(500, 0, 1000, 1000), "B"),
        CapacitanceArtwork("BOT", "DGND", box(0, 0, 1000, 1000), "DGND"),
    )

    dense = extract_multilayer_bulk_capacitance(model)
    sparse = extract_sparse_adjacent_gap_island_capacitance(model)

    assert len(sparse) == 1
    assert isspmatrix(sparse[0].maxwell_capacitance_f)
    assert sparse[0].separation_m == pytest.approx(GAP.separation_m)
    assert sparse[0].nominal_relative_permittivity == pytest.approx(
        GAP.relative_permittivity
    )
    np.testing.assert_allclose(
        _dense_in_order(sparse[0], dense.net_names),
        dense.adjacent_gap_partials[0].maxwell_capacitance_f,
        rtol=1.0e-13,
        atol=1.0e-24,
    )


def test_disconnected_islands_of_one_logical_net_keep_distinct_nodes() -> None:
    model = _model(
        CapacitanceArtwork(
            "TOP", "VDD", box(0, 0, 400, 1000), "top-vdd-island-1"
        ),
        CapacitanceArtwork(
            "TOP", "VDD", box(600, 0, 1000, 1000), "top-vdd-island-2"
        ),
        CapacitanceArtwork(
            "BOT", "DGND", box(0, 0, 1000, 1000), "bottom-ground-island"
        ),
    )

    (partial,) = extract_sparse_adjacent_gap_island_capacitance(model)
    names = partial.net_names
    matrix = partial.maxwell_capacitance_f.toarray()
    first = names.index("top-vdd-island-1")
    second = names.index("top-vdd-island-2")
    ground = names.index("bottom-ground-island")

    assert len(names) == 3
    assert matrix[first, second] == 0.0
    assert matrix[first, ground] < 0.0
    assert matrix[second, ground] < 0.0
    assert matrix[first, first] > 0.0
    assert matrix[second, second] > 0.0


def test_1500_islands_never_allocate_dense_global_matrix_or_call_eigvalsh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artwork: list[CapacitanceArtwork] = []
    for index in range(750):
        left = float(index * 3)
        geometry = box(left, 0.0, left + 1.0, 1.0)
        artwork.append(
            CapacitanceArtwork(
                "TOP", "PWR", geometry, f"top-island-{index:04d}"
            )
        )
        artwork.append(
            CapacitanceArtwork(
                "BOT", "DGND", geometry, f"bottom-island-{index:04d}"
            )
        )
    model = _model(*artwork)

    original_zeros = np.zeros

    def guarded_zeros(shape: object, *args: object, **kwargs: object) -> np.ndarray:
        dimensions = (
            tuple(int(item) for item in shape)
            if isinstance(shape, tuple)
            else (int(shape),)
        )
        if len(dimensions) >= 2 and min(dimensions[:2]) >= 1500:
            raise AssertionError("dense global island matrix allocation attempted")
        return original_zeros(shape, *args, **kwargs)

    def forbidden_eigvalsh(*_args: object, **_kwargs: object) -> np.ndarray:
        raise AssertionError("dense eigenvalue validation attempted")

    monkeypatch.setattr(capacitance_module.np, "zeros", guarded_zeros)
    monkeypatch.setattr(
        capacitance_module.np.linalg, "eigvalsh", forbidden_eigvalsh
    )

    (partial,) = extract_sparse_adjacent_gap_island_capacitance(model)

    assert partial.maxwell_capacitance_f.shape == (1500, 1500)
    assert partial.maxwell_capacitance_f.nnz == 3000
    assert partial.maxwell_capacitance_f.data.flags.writeable is False
    np.testing.assert_allclose(
        np.asarray(partial.maxwell_capacitance_f.sum(axis=1)).ravel(),
        0.0,
        atol=1.0e-24,
    )


def test_same_layer_overlap_error_is_deterministic_for_unordered_tree_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_tree = shapely_strtree.STRtree

    class ReversedHitTree:
        def __init__(self, geometries: object) -> None:
            self._tree = original_tree(geometries)

        def query(self, geometry: object, *args: object, **kwargs: object) -> object:
            result = self._tree.query(geometry, *args, **kwargs)
            if isinstance(geometry, np.ndarray):
                return result
            return np.asarray(
                sorted((int(item) for item in result), reverse=True),
                dtype=np.intp,
            )

    monkeypatch.setattr(shapely_strtree, "STRtree", ReversedHitTree)
    model = _model(
        CapacitanceArtwork("TOP", "P", box(0, 0, 10, 10), "A"),
        CapacitanceArtwork("TOP", "P", box(1, 1, 2, 2), "B"),
        CapacitanceArtwork("TOP", "P", box(3, 3, 4, 4), "C"),
        CapacitanceArtwork("BOT", "G", box(0, 0, 10, 10), "G"),
    )

    with pytest.raises(
        capacitance_module.MultilayerCapacitanceError,
        match=r"nodes 'A' and 'B' overlap",
    ):
        extract_sparse_adjacent_gap_island_capacitance(model)


def _scalar_reference_edges(
    model: MultilayerCapacitanceModel,
) -> dict[tuple[str, str], float]:
    upper = sorted(
        (
            (str(item.node_name or item.net), item.geometry_um)
            for item in model.artwork
            if item.layer == "TOP"
        ),
        key=lambda item: (item[0].casefold(), item[0]),
    )
    lower = sorted(
        (
            (str(item.node_name or item.net), item.geometry_um)
            for item in model.artwork
            if item.layer == "BOT"
        ),
        key=lambda item: (item[0].casefold(), item[0]),
    )
    coefficient = (
        capacitance_module.EPSILON_0_F_PER_M
        * GAP.relative_permittivity
        / GAP.separation_m
        * 1.0e-12
    )
    edges: dict[tuple[str, str], float] = {}
    for upper_name, upper_geometry in upper:
        for lower_name, lower_geometry in lower:
            area_um2 = float(
                upper_geometry.intersection(lower_geometry).area
            )
            if area_um2 > 1.0e-9:
                edges[(upper_name, lower_name)] = coefficient * area_um2
    return edges


def test_batched_threaded_overlay_is_bitwise_equal_to_scalar_complex_reference() -> None:
    upper_hole = Polygon(
        ((0, 0), (10, 0), (10, 10), (0, 10)),
        holes=(((3, 3), (7, 3), (7, 7), (3, 7)),),
    )
    upper_multi = MultiPolygon(
        (box(12, 0, 16, 4), box(12, 6, 16, 10))
    )
    lower_multi = MultiPolygon(
        (box(12, -1, 15.5, 4.5), box(12, 5.5, 15.5, 11))
    )
    model = _model(
        CapacitanceArtwork("TOP", "P", upper_hole, "U-hole"),
        CapacitanceArtwork("TOP", "P", upper_multi, "U-multi"),
        # It touches both neighbouring upper geometries, which exercises
        # zero-area STRtree candidates without creating a same-layer short.
        CapacitanceArtwork("TOP", "P", box(10, 0, 12, 2), "U-touch"),
        CapacitanceArtwork("BOT", "G", box(-1, -1, 5, 11), "L-left"),
        CapacitanceArtwork("BOT", "G", box(5, -1, 11, 11), "L-right"),
        CapacitanceArtwork("BOT", "G", lower_multi, "L-multi"),
        # This only touches U-multi at x=16 and must not enter the partial.
        CapacitanceArtwork("BOT", "G", box(16, 0, 18, 4), "L-touch"),
    )

    reference_edges = _scalar_reference_edges(model)
    (partial,) = extract_sparse_adjacent_gap_island_capacitance(model)
    expected_names = tuple(
        sorted(
            {name for pair in reference_edges for name in pair},
            key=lambda item: (item.casefold(), item),
        )
    )
    assert partial.net_names == expected_names
    assert "L-touch" not in partial.net_names

    matrix = partial.maxwell_capacitance_f.toarray()
    expected = np.zeros_like(matrix)
    positions = {name: index for index, name in enumerate(expected_names)}
    for (upper_name, lower_name), capacitance_f in sorted(
        reference_edges.items(),
        key=lambda item: (
            item[0][0].casefold(),
            item[0][0],
            item[0][1].casefold(),
            item[0][1],
        ),
    ):
        upper_index = positions[upper_name]
        lower_index = positions[lower_name]
        expected[upper_index, upper_index] += capacitance_f
        expected[upper_index, lower_index] -= capacitance_f
        expected[lower_index, upper_index] -= capacitance_f
        expected[lower_index, lower_index] += capacitance_f
    np.testing.assert_array_equal(matrix, expected)


def _partial_overlap_model(count: int) -> MultilayerCapacitanceModel:
    upper_artwork: list[CapacitanceArtwork] = []
    lower_pieces = []
    for index in range(count):
        x = float(index * 4)
        upper_artwork.append(
            CapacitanceArtwork(
                "TOP", "P", box(x, 0, x + 2, 2), f"U-{index:04d}"
            )
        )
        lower_pieces.append(box(x + 1, -1, x + 2.5, 1))
    return _model(
        *upper_artwork,
        CapacitanceArtwork(
            "BOT", "G", MultiPolygon(lower_pieces), "L-partial"
        ),
    )


def test_unresolved_overlays_run_on_bounded_worker_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _partial_overlap_model(32)
    original = capacitance_module._scalar_intersection_area_um2
    worker_names: set[str] = set()
    lock = Lock()

    def observed(left: object, right: object) -> float:
        sleep(0.005)
        with lock:
            worker_names.add(current_thread().name)
        return original(left, right)

    monkeypatch.setattr(
        capacitance_module, "_scalar_intersection_area_um2", observed
    )
    extract_sparse_adjacent_gap_island_capacitance(model)

    assert 2 <= len(worker_names) <= 8
    assert all(name.startswith("spd-decap-overlap") for name in worker_names)
    assert capacitance_module._ADJACENT_INTERSECTION_WORKERS == 8


def test_threaded_overlay_exception_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_overlay(_left: object, _right: object) -> float:
        raise RuntimeError("synthetic GEOS failure")

    monkeypatch.setattr(
        capacitance_module,
        "_scalar_intersection_area_um2",
        failed_overlay,
    )
    with pytest.raises(
        capacitance_module.MultilayerCapacitanceError,
        match="exact adjacent-layer island intersection failed",
    ):
        extract_sparse_adjacent_gap_island_capacitance(
            _partial_overlap_model(12)
        )


def test_threaded_overlay_cancellation_does_not_wait_for_work_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    release = Event()

    def blocked_overlay(_left: object, _right: object) -> float:
        started.set()
        release.wait(timeout=2.0)
        return 1.0

    monkeypatch.setattr(
        capacitance_module,
        "_scalar_intersection_area_um2",
        blocked_overlay,
    )
    begin = monotonic()
    try:
        with pytest.raises(RuntimeError, match="evaluation cancelled"):
            extract_sparse_adjacent_gap_island_capacitance(
                _partial_overlap_model(32),
                is_cancelled=started.is_set,
            )
    finally:
        release.set()
    assert monotonic() - begin < 1.0


def test_adjacent_query_and_overlay_batches_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 600
    artwork = [
        CapacitanceArtwork(
            "TOP",
            "P",
            box(float(index * 2), 0, float(index * 2 + 1), 1),
            f"U-{index:04d}",
        )
        for index in range(count)
    ]
    artwork.append(
        CapacitanceArtwork(
            "BOT", "G", box(-1, -1, float(count * 2), 2), "L"
        )
    )
    model = _model(*artwork)

    original_tree = shapely_strtree.STRtree
    adjacent_query_sizes: list[int] = []

    class CountingTree:
        def __init__(self, geometries: object) -> None:
            self._tree = original_tree(geometries)

        def query(self, geometry: object, *args: object, **kwargs: object) -> object:
            if isinstance(geometry, np.ndarray):
                adjacent_query_sizes.append(len(geometry))
            return self._tree.query(geometry, *args, **kwargs)

    overlay_batch_sizes: list[int] = []
    original_threaded = capacitance_module._threaded_intersection_areas_um2

    def counted_threaded(
        executor: object,
        left: np.ndarray,
        right: np.ndarray,
        *,
        is_cancelled: object,
    ) -> np.ndarray:
        overlay_batch_sizes.append(len(left))
        return original_threaded(
            executor, left, right, is_cancelled=is_cancelled
        )

    monkeypatch.setattr(shapely_strtree, "STRtree", CountingTree)
    monkeypatch.setattr(
        capacitance_module,
        "_threaded_intersection_areas_um2",
        counted_threaded,
    )

    (partial,) = extract_sparse_adjacent_gap_island_capacitance(model)

    expected_query_count = (
        count + capacitance_module._ADJACENT_QUERY_BATCH_SIZE - 1
    ) // capacitance_module._ADJACENT_QUERY_BATCH_SIZE
    assert len(adjacent_query_sizes) == expected_query_count
    assert max(adjacent_query_sizes) <= capacitance_module._ADJACENT_QUERY_BATCH_SIZE
    assert sum(adjacent_query_sizes) == count
    assert max(overlay_batch_sizes) <= capacitance_module._ADJACENT_INTERSECTION_BATCH_SIZE
    assert sum(overlay_batch_sizes) == count
    assert partial.maxwell_capacitance_f.shape == (count + 1, count + 1)


def _partial(
    names: tuple[str, ...],
    matrix: np.ndarray | csc_matrix,
    *,
    upper: str,
    lower: str,
) -> DispersiveAdjacentGap:
    return DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer=upper,
            lower_layer=lower,
            nominal_relative_permittivity=4.0,
            net_names=names,
            maxwell_capacitance_f=matrix,
            separation_m=100.0e-6,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )


def _two_node_laplacian(capacitance_f: float) -> csc_matrix:
    return csc_matrix(
        (
            (capacitance_f, -capacitance_f, -capacitance_f, capacitance_f),
            ((0, 0, 1, 1), (0, 1, 0, 1)),
        ),
        shape=(2, 2),
    )


def test_local_sparse_partial_subset_solve_matches_global_dense_fixture() -> None:
    nodes = ("P", "FLOAT", "GND", "UNUSED")
    first_capacitance = 2.0e-9
    second_capacitance = 3.0e-9
    local = (
        _partial(
            ("P", "FLOAT"),
            _two_node_laplacian(first_capacitance),
            upper="L0",
            lower="L1",
        ),
        _partial(
            ("FLOAT", "GND"),
            _two_node_laplacian(second_capacitance),
            upper="L1",
            lower="L2",
        ),
    )
    dense_partials: list[DispersiveAdjacentGap] = []
    for first, second, capacitance, upper, lower in (
        (0, 1, first_capacitance, "L0", "L1"),
        (1, 2, second_capacitance, "L1", "L2"),
    ):
        matrix = np.zeros((len(nodes), len(nodes)), dtype=np.float64)
        matrix[first, first] += capacitance
        matrix[second, second] += capacitance
        matrix[first, second] -= capacitance
        matrix[second, first] -= capacitance
        dense_partials.append(
            _partial(nodes, matrix, upper=upper, lower=lower)
        )

    port = (LayerSurfacePort("VQPS", "P", "GND"),)
    frequencies = np.asarray((1.0e6, 20.0e6, 100.0e6))
    sparse_result = compile_layer_surface_network(
        nodes, partials=local, via_links=(), ports=port
    ).solve(frequencies)
    dense_result = compile_layer_surface_network(
        nodes, partials=tuple(dense_partials), via_links=(), ports=port
    ).solve(frequencies)

    np.testing.assert_allclose(
        sparse_result.effective_admittance_by_port["VQPS"],
        dense_result.effective_admittance_by_port["VQPS"],
        rtol=1.0e-12,
        atol=1.0e-18,
    )


def test_local_partial_unknown_node_and_sparse_progress_cancel_fail_closed() -> None:
    unknown = _partial(
        ("P", "MISSING"),
        _two_node_laplacian(1.0e-9),
        upper="TOP",
        lower="BOT",
    )
    with pytest.raises(LayerSurfaceNetworkError, match="unknown surface nodes"):
        compile_layer_surface_network(
            ("P", "GND"),
            partials=(unknown,),
            via_links=(),
            ports=(LayerSurfacePort("VQPS", "P", "GND"),),
        )

    model = _model(
        CapacitanceArtwork("TOP", "P", box(0, 0, 1, 1), "P"),
        CapacitanceArtwork("BOT", "G", box(0, 0, 1, 1), "G"),
    )
    updates: list[tuple[int, str]] = []
    result = extract_sparse_adjacent_gap_island_capacitance(
        model,
        progress=lambda value, message: updates.append((value, message)),
        is_cancelled=lambda: False,
    )
    assert result
    assert updates[0][0] == 0
    assert updates[-1][0] == 100
    assert [value for value, _message in updates] == sorted(
        value for value, _message in updates
    )

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 5

    with pytest.raises(RuntimeError, match="evaluation cancelled"):
        extract_sparse_adjacent_gap_island_capacitance(
            model, is_cancelled=cancelled
        )
