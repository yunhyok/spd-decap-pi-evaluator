from __future__ import annotations

from dataclasses import replace
import threading
import time

import numpy as np
import pytest

import spd_decap_pi._core.solver.layer_surface_network as layer_surface_network
from spd_decap_pi._core.models.impedance import ConstantImpedanceModel
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfaceNetworkError,
    LayerSurfacePort,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationBranch,
    LayerSurfaceTerminationCluster,
    compile_layer_surface_termination_manifest,
)
from spd_decap_pi._core.solver.modal import DielectricDispersion
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap


NODES = ("L1::VDD", "L2::VDD", "L2::FLOAT", "L3::DGND")
BASE_NETWORK_SHA256 = "d" * 64


def _edge(
    first: int,
    second: int,
    capacitance_f: float,
    *,
    upper: str,
    lower: str,
) -> DispersiveAdjacentGap:
    matrix = np.zeros((len(NODES), len(NODES)), dtype=np.float64)
    matrix[first, first] += capacitance_f
    matrix[second, second] += capacitance_f
    matrix[first, second] -= capacitance_f
    matrix[second, first] -= capacitance_f
    return DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer=upper,
            lower_layer=lower,
            nominal_relative_permittivity=4.0,
            net_names=NODES,
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )


def _termination_cluster(
    cluster_id: str,
    rail_id: str,
    positive_surface: str,
    negative_surface: str,
    impedance_ohm: complex,
    owner_id: str,
) -> LayerSurfaceTerminationCluster:
    return LayerSurfaceTerminationCluster(
        cluster_id=cluster_id,
        positive_surface_node_id=positive_surface,
        negative_surface_node_id=negative_surface,
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(
            LayerSurfaceTerminationBranch(
                branch_id=f"{cluster_id}:load",
                first_node_id="P",
                second_node_id="G",
                model=ConstantImpedanceModel(
                    f"{cluster_id}:model", impedance_ohm
                ),
                owner_ids=(owner_id,),
            ),
        ),
        rail_owner_ids=(rail_id,),
    )


def test_layer_surfaces_remain_distinct_and_raw_gaps_merge_before_kron() -> None:
    c1, c2 = 2.0e-9, 3.0e-9
    network = compile_layer_surface_network(
        NODES,
        partials=(
            _edge(0, 2, c1, upper="L1", lower="L2"),
            _edge(2, 3, c2, upper="L2", lower="L3"),
        ),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    assert network._surface_to_reduced[0] != network._surface_to_reduced[1]
    frequency = 5.0e6
    result = network.solve([frequency])
    expected_c = c1 * c2 / (c1 + c2)
    np.testing.assert_allclose(
        result.effective_admittance_by_port["P"][0],
        1j * 2.0 * np.pi * frequency * expected_c,
        rtol=1.0e-11,
        atol=1.0e-18,
    )


def test_compiled_sparse_partials_are_deep_readonly_across_replace() -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = np.asarray([1.0e6, 10.0e6])
    expected = network.solve(frequencies).effective_admittance_by_port["P"]

    compiled = network._collapsed_partials[0]
    assert compiled.has_canonical_format
    for buffer in (compiled.data, compiled.indices, compiled.indptr):
        assert buffer.flags.owndata
        assert buffer.base is None
        assert not buffer.flags.writeable
        with pytest.raises(ValueError, match="read-only"):
            buffer[0] = buffer[0]

    # Reproduce the alias attack that a shallow read-only check misses: create
    # writable views first, then mark only the matrix's exposed buffers
    # read-only. A replacement must copy away from all three external aliases.
    aliased = compiled.copy()
    external_data = aliased.data.view()
    external_indices = aliased.indices.view()
    external_indptr = aliased.indptr.view()
    aliased.data.setflags(write=False)
    aliased.indices.setflags(write=False)
    aliased.indptr.setflags(write=False)
    assert external_data.flags.writeable
    assert external_indices.flags.writeable
    assert external_indptr.flags.writeable
    cloned = replace(network, _collapsed_partials=(aliased,))
    assert cloned._collapsed_partials[0] is not aliased
    frozen_snapshots = tuple(
        buffer.copy()
        for buffer in (
            cloned._collapsed_partials[0].data,
            cloned._collapsed_partials[0].indices,
            cloned._collapsed_partials[0].indptr,
        )
    )
    external_data[0] *= 7.0
    external_indices[0] = external_indices[-1]
    external_indptr[-1] = 0
    for buffer in (
        cloned._collapsed_partials[0].data,
        cloned._collapsed_partials[0].indices,
        cloned._collapsed_partials[0].indptr,
    ):
        assert buffer.flags.owndata
        assert buffer.base is None
        assert not buffer.flags.writeable
    for actual, frozen in zip(
        (
            cloned._collapsed_partials[0].data,
            cloned._collapsed_partials[0].indices,
            cloned._collapsed_partials[0].indptr,
        ),
        frozen_snapshots,
        strict=True,
    ):
        np.testing.assert_array_equal(actual, frozen)
    np.testing.assert_array_equal(
        cloned.solve(frequencies).effective_admittance_by_port["P"],
        expected,
    )


def test_solve_detaches_frequency_grid_before_concurrent_caller_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacitance = 2.0e-9
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    caller_grid = np.asarray([1.0e6, 2.0e6, 3.0e6])
    original_grid = caller_grid.copy()
    entered_factor = threading.Event()
    release_factor = threading.Event()
    original_splu = layer_surface_network.splu

    def blocked_splu(matrix):
        entered_factor.set()
        assert release_factor.wait(timeout=2.0)
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", blocked_splu)
    outcomes: list[object] = []

    def run_solve() -> None:
        try:
            outcomes.append(network.solve(caller_grid))
        except BaseException as exc:  # pragma: no cover - asserted below
            outcomes.append(exc)

    worker = threading.Thread(target=run_solve, name="caller-grid-regression")
    worker.start()
    assert entered_factor.wait(timeout=2.0)
    caller_grid[:] = [101.0e6, 102.0e6, 103.0e6]
    release_factor.set()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert len(outcomes) == 1
    assert not isinstance(outcomes[0], BaseException)
    result = outcomes[0]
    np.testing.assert_array_equal(result.frequencies_hz, original_grid)
    np.testing.assert_allclose(
        result.effective_admittance_by_port["P"],
        1j * 2.0 * np.pi * original_grid * capacitance,
        rtol=1.0e-15,
        atol=1.0e-18,
    )
    cached_grid = tuple(
        float(np.frombuffer(key[1][:8], dtype="<f8")[0])
        for key in network._frequency_result_cache
    )
    assert cached_grid == tuple(original_grid)


def test_surface_reduction_mapping_is_owned_readonly_across_replace() -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    assert network._surface_to_reduced.flags.owndata
    assert network._surface_to_reduced.base is None
    assert network._surface_to_reduced.flags.c_contiguous
    assert not network._surface_to_reduced.flags.writeable

    aliased = network._surface_to_reduced.copy()
    external = aliased.view()
    aliased.setflags(write=False)
    cloned = replace(network, _surface_to_reduced=aliased)
    frozen = cloned._surface_to_reduced.copy()
    external[:] = external[::-1]

    assert cloned._surface_to_reduced.flags.owndata
    assert cloned._surface_to_reduced.base is None
    assert not cloned._surface_to_reduced.flags.writeable
    np.testing.assert_array_equal(cloned._surface_to_reduced, frozen)


def test_source_observed_ideal_via_collapses_only_its_two_surfaces() -> None:
    capacitance = 2.5e-9
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(1, 3, capacitance, upper="L2", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "V1", NODES[0], NODES[1], 7, "topology_only_ideal"
            ),
        ),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    assert network._surface_to_reduced[0] == network._surface_to_reduced[1]
    assert network._surface_to_reduced[2] != network._surface_to_reduced[0]
    assert network.surfaces_share_ideal_node(NODES[0], NODES[1])
    assert not network.surfaces_share_ideal_node(NODES[0], NODES[2])
    assert network.reduced_node_index(NODES[0]) == network.reduced_node_index(
        NODES[1]
    )
    frequencies = np.asarray([1.0e6, 10.0e6])
    result = network.solve(frequencies)
    np.testing.assert_allclose(
        result.effective_admittance_by_port["P"],
        1j * 2.0 * np.pi * frequencies * capacitance,
        rtol=1.0e-11,
        atol=1.0e-18,
    )
    assert result.diagnostics.topology_only_link_count == 1


def test_surface_node_index_cache_builds_once_across_92_rail_lookup_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    original = type(network)._surface_node_physical_index_uncached
    calls = 0

    def counted(instance: object, node_ids: object) -> object:
        nonlocal calls
        calls += 1
        return original(instance, node_ids)  # type: ignore[arg-type]

    monkeypatch.setattr(
        type(network),
        "_surface_node_physical_index_uncached",
        counted,
    )

    expected = tuple(int(item) for item in network._surface_to_reduced)
    for _rail_index in range(92):
        assert tuple(network.reduced_node_index(item) for item in NODES) == expected
    assert calls == 1
    assert network._surface_node_index_cache is not None

    network.clear_surface_node_index_cache()
    assert network._surface_node_index_cache is None
    assert network.reduced_node_index(NODES[0]) == expected[0]
    assert calls == 2

    cloned = replace(network)
    assert cloned._surface_node_index_cache is None
    assert (
        cloned._surface_node_index_cache_lock
        is not network._surface_node_index_cache_lock
    )
    assert cloned.reduced_node_index(NODES[0]) == expected[0]
    # Replacement construction revalidates the private compile-time
    # port/Via snapshots once without populating the lazy runtime cache.
    assert calls == 4


def test_surface_node_index_is_exact_case_sensitive_and_unknown_stays_fatal(
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    expected = tuple(int(item) for item in network._surface_to_reduced)

    assert network.has_exact_surface_node(NODES[0])
    assert not network.has_exact_surface_node(f"  {NODES[0]}  ")
    assert not network.has_exact_surface_node("l1::vdd")
    assert not network.has_exact_surface_node("L9::MISSING")
    assert network.reduced_node_index(f"  {NODES[0]}  ") == expected[0]
    with pytest.raises(
        LayerSurfaceNetworkError,
        match="unknown physical surface node 'l1::vdd'",
    ):
        network.reduced_node_index("l1::vdd")
    with pytest.raises(
        LayerSurfaceNetworkError,
        match="unknown physical surface node",
    ):
        network.reduced_node_index("L9::MISSING")

    case_distinct = replace(
        network,
        surface_node_ids=(NODES[0], "l1::vdd", NODES[2], NODES[3]),
    )
    assert case_distinct.has_exact_surface_node("l1::vdd")
    assert not case_distinct.has_exact_surface_node(NODES[1])
    assert case_distinct.reduced_node_index(NODES[0]) == expected[0]
    assert case_distinct.reduced_node_index("l1::vdd") == expected[1]
    assert expected[0] != expected[1]


def test_surface_node_index_rechecks_replaced_identity_and_duplicate_inventory(
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    network.reduced_node_index(NODES[0])
    cached = network._surface_node_index_cache
    assert cached is not None

    duplicated = (NODES[0], NODES[0], NODES[2], NODES[3])
    object.__setattr__(network, "surface_node_ids", duplicated)
    with pytest.raises(
        LayerSurfaceNetworkError,
        match="surface node IDs must be non-empty, canonical, and unique",
    ):
        network.has_exact_surface_node(NODES[0])
    with pytest.raises(
        LayerSurfaceNetworkError,
        match="surface node IDs must be non-empty, canonical, and unique",
    ):
        network.reduced_node_index(NODES[0])
    assert network._surface_node_index_cache is cached
    assert cached.surface_node_ids is not network.surface_node_ids


def test_mutable_surface_inventory_never_earns_index_cache_shortcut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    object.__setattr__(network, "surface_node_ids", list(NODES))
    original = type(network)._surface_node_physical_index_uncached
    calls = 0

    def counted(instance: object, node_ids: object) -> object:
        nonlocal calls
        calls += 1
        return original(instance, node_ids)  # type: ignore[arg-type]

    monkeypatch.setattr(
        type(network),
        "_surface_node_physical_index_uncached",
        counted,
    )

    expected = int(network._surface_to_reduced[0])
    assert network.reduced_node_index(NODES[0]) == expected
    assert network.reduced_node_index(NODES[0]) == expected
    assert calls == 2
    assert network._surface_node_index_cache is None


def test_surface_node_index_reads_current_reduction_mapping_after_cache_build(
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    original = network.reduced_node_index(NODES[0])
    replacement = network._surface_to_reduced.copy()
    replacement[0], replacement[1] = replacement[1], replacement[0]
    replacement.setflags(write=False)
    object.__setattr__(network, "_surface_to_reduced", replacement)

    assert int(replacement[0]) != original
    assert network.reduced_node_index(NODES[0]) == int(replacement[0])


def test_layer_surface_solve_reports_frequency_boundaries_and_cancels() -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = [1.0e6, 2.0e6, 3.0e6]
    baseline = network.solve(frequencies)
    completed: list[tuple[int, int]] = []
    observed = network.solve(
        frequencies,
        progress=lambda count, total: completed.append((count, total)),
        is_cancelled=lambda: False,
    )
    np.testing.assert_array_equal(
        observed.effective_admittance_by_port["P"],
        baseline.effective_admittance_by_port["P"],
    )
    assert completed == [(1, 3), (2, 3), (3, 3)]

    progress: list[tuple[int, int]] = []

    with pytest.raises(RuntimeError, match="layer-surface solve cancelled"):
        network.solve(
            frequencies,
            progress=lambda completed, total: progress.append((completed, total)),
            is_cancelled=lambda: len(progress) >= 1,
        )

    assert progress == [(1, 3)]


def test_frequency_parallelism_overlaps_with_exact_ordered_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = np.asarray(
        [1.0e6, 2.0e6, 3.0e6, 4.0e6, 5.0e6, 6.0e6, 7.0e6, 8.0e6]
    )
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 1)
    sequential = network.solve(frequencies)
    network.clear_frequency_result_cache()

    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 2)
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_PARALLEL_MIN_POINTS", 2)
    monkeypatch.setattr(
        layer_surface_network, "_FREQUENCY_PARALLEL_MIN_ACTIVE_NODES", 2
    )
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: 128 * 1024**3,
    )
    original_splu = layer_surface_network.splu
    lock = threading.Lock()
    both_active = threading.Event()
    active = 0
    maximum_active = 0
    matrix_ids: set[int] = set()

    def overlapping_splu(matrix):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            matrix_ids.add(id(matrix))
            if active >= 2:
                both_active.set()
        try:
            assert both_active.wait(timeout=2.0)
            return original_splu(matrix)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(layer_surface_network, "splu", overlapping_splu)
    progress: list[tuple[int, int]] = []
    parallel = network.solve(
        frequencies,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert maximum_active == 2
    assert len(matrix_ids) >= 2
    assert progress == [(index, 8) for index in range(1, 9)]
    np.testing.assert_array_equal(
        parallel.effective_admittance_by_port["P"],
        sequential.effective_admittance_by_port["P"],
    )
    assert parallel.diagnostics == sequential.diagnostics


def test_frequency_parallelism_has_bit_parity_with_finite_links_termination_and_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    links = (
        LayerSurfaceViaLink(
            "parallel-a",
            NODES[0],
            NODES[1],
            2,
            "finite_parallel_rl",
            0.01,
            0.2e-9,
            owner_ids=("via:a1", "via:a2"),
        ),
        LayerSurfaceViaLink(
            "parallel-b",
            NODES[0],
            NODES[1],
            3,
            "finite_parallel_rl",
            0.03,
            0.8e-9,
            owner_ids=("via:b1", "via:b2", "via:b3"),
        ),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(
            _edge(0, 1, 1.0e-9, upper="L1", lower="L2"),
            _edge(1, 3, 2.0e-9, upper="L2", lower="L3"),
        ),
        via_links=links,
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        network.surface_node_ids,
        (
            _termination_cluster(
                "other-rail-load",
                "OTHER_RAIL",
                NODES[1],
                NODES[3],
                1.7 + 0.2j,
                "other-owner",
            ),
        ),
    )
    frequencies = np.asarray(
        [1.0e6, 2.0e6, 3.0e6, 4.0e6, 5.0e6, 6.0e6, 7.0e6, 8.0e6]
    )
    solve_kwargs = {
        "disabled_via_link_ids": ("parallel-b",),
        "termination_manifest": manifest,
        "selected_rail_id": "SELECTED_RAIL",
        "base_network_identity_sha256": BASE_NETWORK_SHA256,
    }
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 1)
    sequential = network.solve(frequencies, **solve_kwargs)
    network.clear_frequency_result_cache()

    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 2)
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_PARALLEL_MIN_POINTS", 2)
    monkeypatch.setattr(
        layer_surface_network, "_FREQUENCY_PARALLEL_MIN_ACTIVE_NODES", 2
    )
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: 128 * 1024**3,
    )
    parallel = network.solve(frequencies, **solve_kwargs)

    np.testing.assert_array_equal(
        parallel.effective_admittance_by_port["P"],
        sequential.effective_admittance_by_port["P"],
    )
    assert parallel.diagnostics == sequential.diagnostics
    assert (
        parallel.diagnostics.solve_identity_sha256
        == sequential.diagnostics.solve_identity_sha256
    )


def test_frequency_parallel_failure_is_deterministic_and_releases_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacitance = 2.0e-9
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = np.asarray([1.0e6, 2.0e6, 3.0e6, 4.0e6])
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 2)
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_PARALLEL_MIN_POINTS", 2)
    monkeypatch.setattr(
        layer_surface_network, "_FREQUENCY_PARALLEL_MIN_ACTIVE_NODES", 2
    )
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: 128 * 1024**3,
    )

    def failing_splu(matrix):
        frequency_multiple = int(
            round(
                float(np.max(np.abs(matrix.data)))
                / (2.0 * np.pi * 1.0e6 * capacitance)
            )
        )
        if frequency_multiple == 1:
            time.sleep(0.05)
            raise ValueError("first-frequency failure")
        if frequency_multiple == 2:
            raise ValueError("second-frequency failure")
        raise AssertionError("a later frequency escaped the bounded worker window")

    monkeypatch.setattr(layer_surface_network, "splu", failing_splu)
    with pytest.raises(
        LayerSurfaceNetworkError, match="Kron block is singular"
    ) as captured:
        network.solve(frequencies)

    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "first-frequency failure"
    assert not any(
        thread.name.startswith("layer-surface-frequency")
        for thread in threading.enumerate()
    )


def test_frequency_parallel_cancel_waits_at_most_one_inflight_factor_and_has_no_progress_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacitance = 2.0e-9
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = np.asarray([1.0e6, 2.0e6, 3.0e6, 4.0e6])
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 2)
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_PARALLEL_MIN_POINTS", 2)
    monkeypatch.setattr(
        layer_surface_network, "_FREQUENCY_PARALLEL_MIN_ACTIVE_NODES", 2
    )
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: 128 * 1024**3,
    )
    original_splu = layer_surface_network.splu

    def staggered_splu(matrix):
        frequency_multiple = int(
            round(
                float(np.max(np.abs(matrix.data)))
                / (2.0 * np.pi * 1.0e6 * capacitance)
            )
        )
        if frequency_multiple == 2:
            time.sleep(0.15)
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", staggered_splu)
    progress: list[tuple[int, int]] = []
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="layer-surface solve cancelled"):
        network.solve(
            frequencies,
            progress=lambda completed, total: progress.append((completed, total)),
            is_cancelled=lambda: bool(progress),
        )
    elapsed = time.monotonic() - started

    # SuperLU itself has no native cancellation hook.  The coordinator stops
    # dispatch immediately, workers observe the stop flag between phases, and
    # safe shutdown can wait only for the slowest already-running factor.
    assert progress == [(1, 4)]
    assert elapsed < 0.75
    assert not any(
        thread.name.startswith("layer-surface-frequency")
        for thread in threading.enumerate()
    )


def test_small_frequency_workload_does_not_create_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )

    def forbidden_executor(*_args, **_kwargs):
        raise AssertionError("small workloads must stay sequential")

    monkeypatch.setattr(layer_surface_network, "ThreadPoolExecutor", forbidden_executor)
    result = network.solve([1.0e6, 2.0e6, 3.0e6])
    assert result.effective_admittance_by_port["P"].shape == (3,)


@pytest.mark.parametrize("available_bytes", [None, 16 * 1024**3, 31 * 1024**3])
def test_frequency_parallelism_fails_safe_when_available_memory_is_low_or_unknown(
    monkeypatch: pytest.MonkeyPatch,
    available_bytes: int | None,
) -> None:
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 2)
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_PARALLEL_MIN_POINTS", 2)
    monkeypatch.setattr(
        layer_surface_network, "_FREQUENCY_PARALLEL_MIN_ACTIVE_NODES", 2
    )
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: available_bytes,
    )

    assert layer_surface_network._frequency_solve_worker_count(401, 11_050) == 1


def test_frequency_parallelism_uses_two_workers_only_above_ram_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = (
        2 * layer_surface_network._ESTIMATED_BOARD_FACTOR_BYTES
        + layer_surface_network._FREQUENCY_PARALLEL_MEMORY_RESERVE_BYTES
    )
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: required,
    )
    assert layer_surface_network._frequency_solve_worker_count(401, 11_050) == 2


def test_point_cache_reuses_only_existing_float64_points_across_401_to_465_refinement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    initial = np.geomspace(1.0e6, 1.0e9, 401, dtype=np.float64)
    inserted = np.sqrt(initial[:64] * initial[1:65])
    refined = np.sort(np.concatenate((initial, inserted)))
    assert refined.size == 465
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 1)
    original_splu = layer_surface_network.splu
    calls = 0

    def counted_splu(matrix):
        nonlocal calls
        calls += 1
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", counted_splu)
    first = network.solve(initial)
    assert calls == 401
    cached_refined = network.solve(refined)
    assert calls == 465
    assert len(network._frequency_result_cache) == 465

    network.clear_frequency_result_cache()
    assert network._frequency_result_cache_bytes == 0
    uncached_refined = network.solve(refined)
    assert calls == 930
    np.testing.assert_array_equal(
        cached_refined.effective_admittance_by_port["P"],
        uncached_refined.effective_admittance_by_port["P"],
    )
    assert cached_refined.diagnostics == uncached_refined.diagnostics
    np.testing.assert_array_equal(first.frequencies_hz, initial)


def test_point_cache_isolates_manifest_base_disabled_links_and_network_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = LayerSurfaceViaLink(
        "finite",
        NODES[0],
        NODES[3],
        2,
        "finite_parallel_rl",
        0.01,
        0.2e-9,
        owner_ids=("via:1", "via:2"),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(link,),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )

    def manifest() -> object:
        return compile_layer_surface_termination_manifest(
            network.surface_node_ids,
            (
                _termination_cluster(
                    "load",
                    "OTHER",
                    NODES[0],
                    NODES[3],
                    2.0 + 0.1j,
                    "load-owner",
                ),
            ),
        )

    first_manifest = manifest()
    second_manifest = manifest()
    assert first_manifest is not second_manifest
    assert first_manifest.manifest_sha256 == second_manifest.manifest_sha256
    grid = np.asarray([1.0e6, 2.0e6])
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 1)
    original_splu = layer_surface_network.splu
    calls = 0

    def counted_splu(matrix):
        nonlocal calls
        calls += 1
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", counted_splu)
    common = {
        "selected_rail_id": "SELECTED",
        "base_network_identity_sha256": "a" * 64,
    }
    network.solve(grid, termination_manifest=first_manifest, **common)
    network.solve(grid, termination_manifest=first_manifest, **common)
    assert calls == 2
    network.solve(grid, termination_manifest=second_manifest, **common)
    assert calls == 4
    network.solve(
        grid,
        termination_manifest=second_manifest,
        selected_rail_id="SELECTED",
        base_network_identity_sha256="b" * 64,
    )
    assert calls == 6
    network.solve(
        grid,
        termination_manifest=second_manifest,
        selected_rail_id="SELECTED",
        base_network_identity_sha256="b" * 64,
        disabled_via_link_ids=("finite",),
    )
    assert calls == 8

    cloned = replace(network)
    assert not cloned._frequency_result_cache
    cloned.solve(
        grid,
        termination_manifest=second_manifest,
        selected_rail_id="SELECTED",
        base_network_identity_sha256="b" * 64,
        disabled_via_link_ids=("finite",),
    )
    assert calls == 10


def test_point_cache_entry_lru_byte_state_caps_and_clear_are_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_SOLVE_MAX_WORKERS", 1)
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_RESULT_CACHE_ENTRY_LIMIT", 3)
    network.solve([1.0e6, 2.0e6, 3.0e6, 4.0e6, 5.0e6])
    cached_frequencies = tuple(
        float(np.frombuffer(key[1][:8], dtype="<f8")[0])
        for key in network._frequency_result_cache
    )
    assert cached_frequencies == (3.0e6, 4.0e6, 5.0e6)
    assert tuple(network._frequency_result_cache_state_counts.values()) == (3,)

    network.clear_frequency_result_cache()
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_RESULT_CACHE_ENTRY_LIMIT", 100)
    monkeypatch.setattr(layer_surface_network, "_FREQUENCY_RESULT_CACHE_STATE_LIMIT", 2)
    monkeypatch.setattr(
        layer_surface_network,
        "_FREQUENCY_RESULT_CACHE_BYTE_LIMIT",
        layer_surface_network._FREQUENCY_RESULT_CACHE_STATE_RETAIN_CHARGE_BYTES
        + 32_000,
    )
    for identity in ("a" * 64, "b" * 64, "c" * 64, "d" * 64):
        network.solve([1.0e6], base_network_identity_sha256=identity)
        assert (
            network._frequency_result_cache_bytes
            <= layer_surface_network._FREQUENCY_RESULT_CACHE_BYTE_LIMIT
        )
        assert len(network._frequency_result_cache_bindings) <= 1
    assert len(network._frequency_result_cache_bindings) == 1
    assert len(network._frequency_result_cache) == 1
    assert sum(network._frequency_result_cache_state_counts.values()) == 1
    expected_bytes = sum(
        entry.estimated_size_bytes
        for entry in network._frequency_result_cache.values()
    ) + sum(network._frequency_result_cache_binding_bytes.values())
    assert network._frequency_result_cache_bytes == expected_bytes

    network.clear_frequency_result_cache()
    assert not network._frequency_result_cache
    assert not network._frequency_result_cache_bindings
    assert not network._frequency_result_cache_state_counts
    assert not network._frequency_result_cache_binding_bytes
    assert network._frequency_result_cache_bytes == 0


def test_compile_time_port_via_and_dispersion_snapshots_block_pre_solve_tamper() -> None:
    def make_link() -> LayerSurfaceViaLink:
        return LayerSurfaceViaLink(
            "finite",
            NODES[0],
            NODES[1],
            1,
            "finite_parallel_rl",
            0.01,
            0.2e-9,
            owner_ids=("owner",),
        )

    duplicate_port = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(make_link(),),
        ports=(
            LayerSurfacePort("P1", NODES[0], NODES[3]),
            LayerSurfacePort("P2", NODES[1], NODES[3]),
        ),
    )
    object.__setattr__(duplicate_port.ports[1], "port_id", "P1")
    with pytest.raises(LayerSurfaceNetworkError, match="port metadata"):
        duplicate_port.solve([1.0e6])

    changed_via = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(make_link(),),
        ports=(LayerSurfacePort("P1", NODES[0], NODES[3]),),
    )
    object.__setattr__(changed_via.via_links[0], "mode", "topology_only_ideal")
    with pytest.raises(LayerSurfaceNetworkError, match="topology-only Via metadata"):
        changed_via.solve([1.0e6])

    changed_owner = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(make_link(),),
        ports=(LayerSurfacePort("P1", NODES[0], NODES[3]),),
    )
    object.__setattr__(changed_owner.via_links[0], "owner_ids", (" owner ",))
    with pytest.raises(LayerSurfaceNetworkError, match="owner metadata"):
        changed_owner.solve([1.0e6])

    changed_dielectric = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P1", NODES[0], NODES[3]),),
    )
    object.__setattr__(
        changed_dielectric.partials[0].partial,
        "nominal_relative_permittivity",
        8.0,
    )
    with pytest.raises(LayerSurfaceNetworkError, match="dielectric metadata"):
        changed_dielectric.solve([1.0e6])


def test_composite_dielectric_snapshot_is_canonical_cacheable_and_tamper_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from spd_decap_pi._core.solver.layerwise_network import (
        CompositeDielectricDispersion,
    )

    first_row = DielectricDispersion(
        frequencies_hz=(1.0e6,),
        relative_permittivities=(4.0,),
        loss_tangents=(0.0,),
    )
    second_row = DielectricDispersion(
        frequencies_hz=(1.0e6,),
        relative_permittivities=(8.0,),
        loss_tangents=(0.0,),
    )
    base = _edge(0, 3, 2.0e-9, upper="L1", lower="L3")
    partial = DispersiveAdjacentGap(
        partial=base.partial,
        dispersion=CompositeDielectricDispersion(
            ((50.0, first_row), (50.0, second_row))
        ),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(partial,),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    original_splu = layer_surface_network.splu
    calls = 0

    def counted_splu(matrix):
        nonlocal calls
        calls += 1
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", counted_splu)
    first = network.solve([1.0e6])
    cached = network.solve([1.0e6])

    assert calls == 1
    np.testing.assert_array_equal(
        cached.effective_admittance_by_port["P"],
        first.effective_admittance_by_port["P"],
    )
    effective_dk = 100.0 / (50.0 / 4.0 + 50.0 / 8.0)
    np.testing.assert_allclose(
        first.effective_admittance_by_port["P"],
        [1j * 2.0 * np.pi * 1.0e6 * 2.0e-9 * effective_dk / 4.0],
        rtol=1.0e-12,
        atol=1.0e-18,
    )

    object.__setattr__(second_row, "relative_permittivities", (9.0,))
    with pytest.raises(LayerSurfaceNetworkError, match="dielectric metadata"):
        network.solve([1.0e6])


@pytest.mark.parametrize("mutation", ["port", "via", "dispersion"])
def test_mid_factor_metadata_tamper_fails_explicitly_without_partial_result(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    link = LayerSurfaceViaLink(
        "finite",
        NODES[0],
        NODES[1],
        1,
        "finite_parallel_rl",
        0.01,
        0.2e-9,
        owner_ids=("owner",),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(
            _edge(0, 1, 1.0e-9, upper="L1", lower="L2"),
            _edge(1, 3, 2.0e-9, upper="L2", lower="L3"),
        ),
        via_links=(link,),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    entered = threading.Event()
    release = threading.Event()
    original_splu = layer_surface_network.splu

    def blocked_splu(matrix):
        entered.set()
        assert release.wait(timeout=2.0)
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", blocked_splu)
    outcomes: list[object] = []

    def run() -> None:
        try:
            outcomes.append(network.solve([1.0e6]))
        except BaseException as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=run, name=f"mid-{mutation}-tamper")
    worker.start()
    assert entered.wait(timeout=2.0)
    if mutation == "port":
        object.__setattr__(network.ports[0], "port_id", "MUTATED")
    elif mutation == "via":
        object.__setattr__(network.via_links[0], "count", 9)
    else:
        object.__setattr__(
            network.partials[0].dispersion,
            "relative_permittivities",
            (7.0,),
        )
    release.set()
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], LayerSurfaceNetworkError)
    assert "changed during layer-network solve" in str(outcomes[0])
    assert not isinstance(outcomes[0], KeyError)
    assert not network._frequency_result_cache


def test_same_model_object_parameter_tamper_is_fail_closed_before_cache_hit() -> None:
    model = ConstantImpedanceModel("mutable", 2.0 + 0.0j)
    cluster = LayerSurfaceTerminationCluster(
        cluster_id="load",
        positive_surface_node_id=NODES[0],
        negative_surface_node_id=NODES[3],
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(
            LayerSurfaceTerminationBranch(
                branch_id="load:branch",
                first_node_id="P",
                second_node_id="G",
                model=model,
                owner_ids=("load-owner",),
            ),
        ),
        rail_owner_ids=("OTHER",),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        network.surface_node_ids, (cluster,)
    )
    kwargs = {
        "termination_manifest": manifest,
        "selected_rail_id": "SELECTED",
        "base_network_identity_sha256": BASE_NETWORK_SHA256,
    }
    network.solve([1.0e6], **kwargs)
    object.__setattr__(model, "impedance_ohm", 4.0 + 0.0j)

    with pytest.raises(
        LayerSurfaceNetworkError, match="branch model changed after compilation"
    ):
        network.solve([1.0e6], **kwargs)


def test_actual_matrix_hash_misses_when_stateful_model_output_changes_without_evidence_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ConstantImpedanceModel("stateful", 2.0 + 0.0j)
    cluster = LayerSurfaceTerminationCluster(
        cluster_id="load",
        positive_surface_node_id=NODES[0],
        negative_surface_node_id=NODES[3],
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(
            LayerSurfaceTerminationBranch(
                branch_id="load:branch",
                first_node_id="P",
                second_node_id="G",
                model=model,
                owner_ids=("load-owner",),
            ),
        ),
        rail_owner_ids=("OTHER",),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        network.surface_node_ids, (cluster,)
    )
    current = {"impedance": 2.0 + 0.0j}

    def stateful_impedance(self, frequencies_hz):
        frequencies = np.asarray(frequencies_hz, dtype=np.float64)
        return np.full(
            frequencies.shape,
            current["impedance"],
            dtype=np.complex128,
        )

    monkeypatch.setattr(
        ConstantImpedanceModel, "impedance", stateful_impedance
    )
    original_splu = layer_surface_network.splu
    calls = 0

    def counted_splu(matrix):
        nonlocal calls
        calls += 1
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", counted_splu)
    kwargs = {
        "termination_manifest": manifest,
        "selected_rail_id": "SELECTED",
        "base_network_identity_sha256": BASE_NETWORK_SHA256,
    }
    first = network.solve([1.0e6], **kwargs)
    current["impedance"] = 4.0 + 0.0j
    second = network.solve([1.0e6], **kwargs)

    assert calls == 2
    assert not np.array_equal(
        first.effective_admittance_by_port["P"],
        second.effective_admittance_by_port["P"],
    )


def test_mutable_inventory_never_qualifies_for_point_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    object.__setattr__(network, "ports", list(network.ports))
    original_splu = layer_surface_network.splu
    calls = 0

    def counted_splu(matrix):
        nonlocal calls
        calls += 1
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", counted_splu)
    network.solve([1.0e6, 2.0e6])
    network.solve([1.0e6, 2.0e6])
    assert calls == 4
    assert not network._frequency_result_cache
    network.ports[0] = LayerSurfacePort("Q", NODES[0], NODES[3])
    with pytest.raises(LayerSurfaceNetworkError, match="compiled network"):
        network.solve([1.0e6])


def test_point_cache_payload_tamper_evicts_whole_state_and_recomputes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    original_splu = layer_surface_network.splu
    calls = 0

    def counted_splu(matrix):
        nonlocal calls
        calls += 1
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", counted_splu)
    expected = network.solve([1.0e6, 2.0e6])
    keys = tuple(network._frequency_result_cache)
    first_entry = network._frequency_result_cache[keys[0]]
    second_entry = network._frequency_result_cache[keys[1]]
    network._frequency_result_cache[keys[0]] = replace(
        first_entry,
        result=second_entry.result,
    )

    repaired = network.solve([1.0e6, 2.0e6])
    assert calls == 4
    np.testing.assert_array_equal(
        repaired.effective_admittance_by_port["P"],
        expected.effective_admittance_by_port["P"],
    )
    assert sum(network._frequency_result_cache_state_counts.values()) == 2
    expected_bytes = sum(
        entry.estimated_size_bytes
        for entry in network._frequency_result_cache.values()
    ) + sum(network._frequency_result_cache_binding_bytes.values())
    assert network._frequency_result_cache_bytes == expected_bytes


def test_point_cache_uses_exact_nextafter_frequency_bits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    original_splu = layer_surface_network.splu
    calls = 0

    def counted_splu(matrix):
        nonlocal calls
        calls += 1
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", counted_splu)
    first = np.float64(1.0e6)
    second = np.nextafter(first, np.float64(np.inf))
    network.solve([first])
    network.solve([second])
    network.solve([first])

    assert calls == 2
    cached_bits = {key[1][:8] for key in network._frequency_result_cache}
    assert cached_bits == {
        np.asarray([first], dtype="<f8").tobytes(),
        np.asarray([second], dtype="<f8").tobytes(),
    }


@pytest.mark.parametrize(
    ("available_bytes", "expected_maximum"),
    [(128 * 1024**3, 2), (16 * 1024**3, 1)],
)
def test_process_global_factor_reservation_caps_concurrent_solves_by_live_memory(
    monkeypatch: pytest.MonkeyPatch,
    available_bytes: int,
    expected_maximum: int,
) -> None:
    networks = tuple(
        compile_layer_surface_network(
            NODES,
            partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
            via_links=(),
            ports=(LayerSurfacePort(f"P{index}", NODES[0], NODES[3]),),
        )
        for index in range(2)
    )
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: available_bytes,
    )
    original_splu = layer_surface_network.splu
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    both_active = threading.Event()

    def tracked_splu(matrix):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            if active >= 2:
                both_active.set()
        try:
            if expected_maximum == 2:
                assert both_active.wait(timeout=2.0)
            else:
                time.sleep(0.05)
            return original_splu(matrix)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(layer_surface_network, "splu", tracked_splu)
    start = threading.Barrier(3)
    outcomes: list[object] = []

    def run(network) -> None:
        start.wait(timeout=2.0)
        try:
            outcomes.append(network.solve([1.0e6]))
        except BaseException as exc:
            outcomes.append(exc)

    workers = [
        threading.Thread(target=run, args=(network,), name=f"global-factor-{index}")
        for index, network in enumerate(networks)
    ]
    for worker in workers:
        worker.start()
    start.wait(timeout=2.0)
    for worker in workers:
        worker.join(timeout=3.0)

    assert all(not worker.is_alive() for worker in workers)
    assert len(outcomes) == 2
    assert not any(isinstance(outcome, BaseException) for outcome in outcomes)
    assert maximum_active == expected_maximum
    assert layer_surface_network._ACTIVE_FACTOR_RESERVATIONS == 0


def test_factor_reservation_releases_after_exception_and_waiter_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def make_network(port_id: str):
        return compile_layer_surface_network(
            NODES,
            partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
            via_links=(),
            ports=(LayerSurfacePort(port_id, NODES[0], NODES[3]),),
        )

    failing = make_network("FAIL")
    monkeypatch.setattr(
        layer_surface_network,
        "_available_physical_memory_bytes",
        lambda: 16 * 1024**3,
    )
    original_splu = layer_surface_network.splu
    monkeypatch.setattr(
        layer_surface_network,
        "splu",
        lambda _matrix: (_ for _ in ()).throw(ValueError("factor failed")),
    )
    with pytest.raises(LayerSurfaceNetworkError, match="Kron block is singular"):
        failing.solve([1.0e6])
    assert layer_surface_network._ACTIVE_FACTOR_RESERVATIONS == 0

    holder = make_network("HOLDER")
    waiter = make_network("WAITER")
    entered = threading.Event()
    release = threading.Event()

    def blocked_splu(matrix):
        entered.set()
        assert release.wait(timeout=2.0)
        return original_splu(matrix)

    monkeypatch.setattr(layer_surface_network, "splu", blocked_splu)
    holder_outcome: list[object] = []
    waiter_outcome: list[object] = []
    cancel_waiter = threading.Event()

    def run_holder() -> None:
        try:
            holder_outcome.append(holder.solve([1.0e6]))
        except BaseException as exc:
            holder_outcome.append(exc)

    def run_waiter() -> None:
        try:
            waiter_outcome.append(
                waiter.solve(
                    [1.0e6],
                    is_cancelled=cancel_waiter.is_set,
                )
            )
        except BaseException as exc:
            waiter_outcome.append(exc)

    holder_thread = threading.Thread(target=run_holder, name="permit-holder")
    waiter_thread = threading.Thread(target=run_waiter, name="permit-waiter")
    holder_thread.start()
    assert entered.wait(timeout=2.0)
    waiter_thread.start()
    time.sleep(0.08)
    cancel_waiter.set()
    waiter_thread.join(timeout=2.0)
    release.set()
    holder_thread.join(timeout=3.0)

    assert not waiter_thread.is_alive()
    assert not holder_thread.is_alive()
    assert len(waiter_outcome) == 1
    assert isinstance(waiter_outcome[0], RuntimeError)
    assert "cancelled" in str(waiter_outcome[0])
    assert len(holder_outcome) == 1
    assert not isinstance(holder_outcome[0], BaseException)
    assert layer_surface_network._ACTIVE_FACTOR_RESERVATIONS == 0


def test_finite_parallel_vias_and_gap_form_the_expected_series_network() -> None:
    capacitance = 1.8e-9
    resistance = 0.012
    inductance = 0.35e-9
    count = 6
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(1, 3, capacitance, upper="L2", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "V1",
                NODES[0],
                NODES[1],
                count,
                "finite_parallel_rl",
                resistance,
                inductance,
            ),
        ),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = np.asarray([1.0e6, 20.0e6, 100.0e6])
    result = network.solve(frequencies)
    omega = 2.0 * np.pi * frequencies
    expected_z = (resistance + 1j * omega * inductance) / count + 1.0 / (
        1j * omega * capacitance
    )
    np.testing.assert_allclose(
        result.effective_admittance_by_port["P"],
        1.0 / expected_z,
        rtol=1.0e-10,
        atol=1.0e-17,
    )
    assert result.diagnostics.finite_via_link_count == 1


def test_portless_components_are_pruned_without_changing_owner_ledger() -> None:
    nodes = (*NODES, "ISO-A", "ISO-B")
    capacitance = 2.0e-9
    matrix = np.zeros((len(nodes), len(nodes)), dtype=np.float64)
    for first, second, value in ((0, 3, capacitance), (4, 5, 7.0e-9)):
        matrix[first, first] += value
        matrix[second, second] += value
        matrix[first, second] -= value
        matrix[second, first] -= value
    partial = DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer="L1",
            lower_layer="L3",
            nominal_relative_permittivity=4.0,
            net_names=nodes,
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )
    isolated_link = LayerSurfaceViaLink(
        "isolated-finite",
        "ISO-A",
        "ISO-B",
        2,
        "finite_parallel_rl",
        0.01,
        0.2e-9,
        owner_ids=("via:iso-1", "via:iso-2"),
    )
    network = compile_layer_surface_network(
        nodes,
        partials=(partial,),
        via_links=(isolated_link,),
        ports=(LayerSurfacePort("P", nodes[0], nodes[3]),),
    )

    frequency = 3.0e6
    result = network.solve([frequency])

    np.testing.assert_allclose(
        result.effective_admittance_by_port["P"][0],
        1j * 2.0 * np.pi * frequency * capacitance,
        rtol=1.0e-12,
        atol=1.0e-18,
    )
    assert result.diagnostics.reduced_node_count == len(nodes)
    assert result.diagnostics.active_reduced_node_count == 2
    assert result.diagnostics.pruned_portless_node_count == len(nodes) - 2
    assert result.diagnostics.active_structural_component_count == 1
    assert tuple(network.via_links[0].owner_ids) == (
        "via:iso-1",
        "via:iso-2",
    )


def test_parallel_endpoint_links_keep_distinct_frequency_dependent_rl_terms() -> None:
    capacitance = 1.0e-9
    links = (
        LayerSurfaceViaLink(
            "parallel-a",
            NODES[0],
            NODES[3],
            2,
            "finite_parallel_rl",
            0.01,
            0.2e-9,
            owner_ids=("via:a1", "via:a2"),
        ),
        LayerSurfaceViaLink(
            "parallel-b",
            NODES[0],
            NODES[3],
            3,
            "finite_parallel_rl",
            0.03,
            0.8e-9,
            owner_ids=("via:b1", "via:b2", "via:b3"),
        ),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=links,
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = np.asarray([1.0e6, 50.0e6, 200.0e6])

    result = network.solve(frequencies)

    omega = 2.0 * np.pi * frequencies
    expected = (
        1j * omega * capacitance
        + 2.0 / (0.01 + 1j * omega * 0.2e-9)
        + 3.0 / (0.03 + 1j * omega * 0.8e-9)
    )
    np.testing.assert_allclose(
        result.effective_admittance_by_port["P"],
        expected,
        rtol=1.0e-12,
        atol=1.0e-12,
    )
    assert tuple(link.owner_ids for link in network.via_links) == (
        ("via:a1", "via:a2"),
        ("via:b1", "via:b2", "via:b3"),
    )


def test_many_open_ports_are_solved_in_bounded_exact_rhs_batches() -> None:
    capacitance = 2.5e-9
    ports = tuple(
        LayerSurfacePort(f"P{index}", NODES[0], NODES[3])
        for index in range(11)
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=(),
        ports=ports,
    )
    frequency = 8.0e6

    result = network.solve([frequency])

    expected = 1j * 2.0 * np.pi * frequency * capacitance
    for port in ports:
        np.testing.assert_allclose(
            result.effective_admittance_by_port[port.port_id][0],
            expected,
            rtol=1.0e-12,
            atol=1.0e-18,
        )
    assert result.diagnostics.maximum_port_rhs_batch_size == 4


def test_rhs_batch_residual_gate_checks_each_port_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacitance = 2.5e-9
    ports = tuple(
        LayerSurfacePort(f"P{index}", NODES[0], NODES[3])
        for index in range(4)
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=(),
        ports=ports,
    )
    original_splu = layer_surface_network.splu

    class PerturbedFactor:
        def __init__(self, factor: object) -> None:
            self._factor = factor
            self.U = factor.U

        def solve(self, rhs: np.ndarray) -> np.ndarray:
            solution = np.asarray(self._factor.solve(rhs)).copy()
            if solution.ndim == 2 and solution.shape[1] == 4:
                solution[:, 0] *= 1.0 + 3.0e-9
            return solution

    monkeypatch.setattr(
        layer_surface_network,
        "splu",
        lambda matrix: PerturbedFactor(original_splu(matrix)),
    )

    with pytest.raises(LayerSurfaceNetworkError, match="residual is excessive"):
        network.solve([8.0e6])


def test_factor_pivot_ratio_rejects_forward_unreliable_real_superlu_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.5e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    original_splu = layer_surface_network.splu

    class DelegatingFactor:
        def __init__(self, factor: object) -> None:
            self._factor = factor
            self.U = type(
                "PivotView",
                (),
                {"diagonal": lambda _self: np.asarray([1.0, 1.0e-17])},
            )()

        def solve(self, rhs: np.ndarray) -> np.ndarray:
            return np.asarray(self._factor.solve(rhs))

    monkeypatch.setattr(
        layer_surface_network,
        "splu",
        lambda matrix: DelegatingFactor(original_splu(matrix)),
    )

    with pytest.raises(LayerSurfaceNetworkError, match="forward-reliability") as exc_info:
        network.solve([8.0e6])

    message = str(exc_info.value)
    assert "frequency_hz=8000000" in message
    assert "component_index=0" in message
    assert "u_pivot_abs_min=1.000e-17" in message
    assert "u_pivot_abs_max=1.000e+00" in message
    assert "pivot_ratio=1.000e+17" in message
    assert "retained_nodes=1" in message
    assert "local_nnz=1" in message
    assert "local_abs_min=1.257e-01" in message
    assert "local_abs_max=1.257e-01" in message
    assert "backward_residual=0.000e+00" in message
    assert (
        "matrix_sha256=beb18d600835869fe1bf59108684b20fb8c266978c87ad9d70eae6899bdb8f8b)"
        in message
    )


def test_factor_pivot_ratio_ceiling_preserves_real_result_and_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def make_network() -> object:
        return compile_layer_surface_network(
            NODES,
            partials=(_edge(0, 3, 2.5e-9, upper="L1", lower="L3"),),
            via_links=(),
            ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
        )

    baseline = make_network().solve([8.0e6])
    network = make_network()
    original_splu = layer_surface_network.splu
    solve_calls = 0

    class DelegatingFactor:
        def __init__(self, factor: object) -> None:
            self._factor = factor
            self.U = type(
                "PivotView",
                (),
                {"diagonal": lambda _self: np.asarray([1.0, 1.0e-13])},
            )()

        def solve(self, rhs: np.ndarray) -> np.ndarray:
            nonlocal solve_calls
            solve_calls += 1
            return np.asarray(self._factor.solve(rhs))

    monkeypatch.setattr(
        layer_surface_network,
        "splu",
        lambda matrix: DelegatingFactor(original_splu(matrix)),
    )
    result = network.solve([8.0e6])

    assert solve_calls == 1
    np.testing.assert_allclose(
        result.effective_admittance_by_port["P"],
        baseline.effective_admittance_by_port["P"],
        rtol=1.0e-12,
        atol=1.0e-18,
    )
    assert result.diagnostics.maximum_factor_pivot_ratio == pytest.approx(1.0e13)
    assert result.diagnostics.maximum_relative_residual <= 1.0e-9


def test_finite_link_suppression_changes_rl_stamp_and_structural_components() -> None:
    capacitance = 1.8e-9
    resistance = 0.012
    inductance = 0.35e-9
    measured_link = LayerSurfaceViaLink(
        "route-z",
        NODES[0],
        NODES[3],
        1,
        "finite_parallel_rl",
        resistance,
        inductance,
        owner_ids=("ViaMeasured",),
    )
    structural_link = LayerSurfaceViaLink(
        "route-a",
        NODES[1],
        NODES[2],
        1,
        "finite_parallel_rl",
        0.02,
        0.2e-9,
        owner_ids=("ViaStructural",),
    )
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=(measured_link, structural_link),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )
    frequencies = np.asarray([1.0e6, 20.0e6, 100.0e6])

    baseline = network.solve(frequencies)
    suppressed = network.solve(
        frequencies,
        disabled_via_link_ids=("route-z", "route-a"),
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    reversed_request = network.solve(
        frequencies,
        disabled_via_link_ids=("route-a", "route-z"),
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )

    omega = 2.0 * np.pi * frequencies
    expected_baseline = 1j * omega * capacitance + 1.0 / (
        resistance + 1j * omega * inductance
    )
    expected_suppressed = 1j * omega * capacitance
    np.testing.assert_allclose(
        baseline.effective_admittance_by_port["P"],
        expected_baseline,
        rtol=1.0e-11,
        atol=1.0e-16,
    )
    np.testing.assert_allclose(
        suppressed.effective_admittance_by_port["P"],
        expected_suppressed,
        rtol=1.0e-11,
        atol=1.0e-18,
    )
    np.testing.assert_array_equal(
        reversed_request.effective_admittance_by_port["P"],
        suppressed.effective_admittance_by_port["P"],
    )
    assert baseline.diagnostics.structural_component_count == 2
    assert suppressed.diagnostics.structural_component_count == 3
    assert suppressed.diagnostics.finite_via_link_count == 2
    assert suppressed.diagnostics.disabled_via_link_count == 2
    assert suppressed.diagnostics.disabled_via_link_ids == (
        "route-a",
        "route-z",
    )
    assert len(suppressed.diagnostics.disabled_via_link_ids_sha256 or "") == 64
    assert (
        suppressed.diagnostics.disabled_via_link_ids_sha256
        == reversed_request.diagnostics.disabled_via_link_ids_sha256
    )
    assert suppressed.diagnostics.solve_identity_sha256 is not None
    assert (
        suppressed.diagnostics.solve_identity_sha256
        == reversed_request.diagnostics.solve_identity_sha256
    )
    assert baseline.diagnostics.solve_identity_sha256 is None
    assert tuple(link.owner_ids for link in network.via_links) == (
        ("ViaMeasured",),
        ("ViaStructural",),
    )


@pytest.mark.parametrize(
    ("disabled_ids", "message"),
    (
        (("missing-link",), "unknown"),
        (("finite-link", "finite-link"), "duplicates"),
        (("ideal-link",), "topology-only"),
        (("FINITE-LINK",), "unknown"),
    ),
)
def test_finite_link_suppression_rejects_unsafe_requests(
    disabled_ids: tuple[str, ...],
    message: str,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 1.0e-9, upper="L1", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "finite-link",
                NODES[0],
                NODES[3],
                1,
                "finite_parallel_rl",
                0.01,
                0.2e-9,
            ),
            LayerSurfaceViaLink(
                "ideal-link",
                NODES[1],
                NODES[2],
                1,
                "topology_only_ideal",
            ),
        ),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )

    with pytest.raises(LayerSurfaceNetworkError, match=message):
        network.solve(
            [1.0e6],
            disabled_via_link_ids=disabled_ids,
            base_network_identity_sha256=BASE_NETWORK_SHA256,
        )


def test_finite_link_suppression_requires_base_network_identity() -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 1.0e-9, upper="L1", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "finite-link",
                NODES[0],
                NODES[3],
                1,
                "finite_parallel_rl",
                0.01,
                0.2e-9,
            ),
        ),
        ports=(LayerSurfacePort("P", NODES[0], NODES[3]),),
    )

    with pytest.raises(LayerSurfaceNetworkError, match="hash-bound"):
        network.solve([1.0e6], disabled_via_link_ids=("finite-link",))


def test_source_order_does_not_change_layer_network_result() -> None:
    partials = (
        _edge(0, 2, 2.0e-9, upper="L1", lower="L2"),
        _edge(2, 3, 3.0e-9, upper="L2", lower="L3"),
    )
    ports = (
        LayerSurfacePort("P1", NODES[0], NODES[3]),
        LayerSurfacePort("P2", NODES[2], NODES[3]),
    )
    first = compile_layer_surface_network(
        NODES, partials=partials, via_links=(), ports=ports
    ).solve([2.0e6, 9.0e6])
    second = compile_layer_surface_network(
        NODES, partials=tuple(reversed(partials)), via_links=(), ports=tuple(reversed(ports))
    ).solve([2.0e6, 9.0e6])
    for port in ("P1", "P2"):
        np.testing.assert_allclose(
            first.effective_admittance_by_port[port],
            second.effective_admittance_by_port[port],
            rtol=1.0e-12,
            atol=1.0e-18,
        )


def test_selected_and_other_rail_terminations_are_merged_before_kron() -> None:
    c_series = 2.0e-9
    c_other = 3.0e-9
    load_ohm = 2.5
    network = compile_layer_surface_network(
        NODES,
        partials=(
            _edge(0, 2, c_series, upper="L1", lower="L2"),
            _edge(2, 3, c_other, upper="L2", lower="L3"),
        ),
        via_links=(),
        ports=(
            LayerSurfacePort("R1", NODES[0], NODES[3]),
            LayerSurfacePort("R2", NODES[2], NODES[3]),
        ),
    )
    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "selected-r1", "R1", NODES[0], NODES[3], 1.0e-3, "cap:r1"
            ),
            _termination_cluster(
                "other-r2", "R2", NODES[2], NODES[3], load_ohm, "cap:r2"
            ),
        ),
    )
    frequencies = np.asarray([1.0e6, 10.0e6])
    result = network.solve(
        frequencies,
        termination_manifest=manifest,
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )

    omega = 2.0 * np.pi * frequencies
    first_impedance = 1.0 / (1j * omega * c_series)
    other_admittance = 1j * omega * c_other + 1.0 / load_ohm
    expected = 1.0 / 1.0e-3 + 1.0 / (
        first_impedance + 1.0 / other_admittance
    )
    np.testing.assert_allclose(
        result.effective_admittance_by_port["R1"],
        expected,
        rtol=1.0e-11,
        atol=1.0e-16,
    )
    assert result.diagnostics.active_termination_cluster_count == 2
    assert result.diagnostics.excluded_selected_rail_cluster_count == 0
    assert result.diagnostics.termination_manifest_sha256 == manifest.manifest_sha256
    assert result.diagnostics.solve_identity_sha256 == manifest.cache_identity_sha256(
        BASE_NETWORK_SHA256, "R1", frequencies
    )


def test_one_rail_measurement_port_is_open_while_its_physical_load_is_active() -> None:
    capacitance = 2.2e-9
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, capacitance, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("R1", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "selected-r1", "R1", NODES[0], NODES[3], 1.0e-3, "cap:r1"
            ),
        ),
    )
    frequencies = np.asarray([1.0e6, 5.0e6])
    result = network.solve(
        frequencies,
        termination_manifest=manifest,
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )

    np.testing.assert_allclose(
        result.effective_admittance_by_port["R1"],
        1j * 2.0 * np.pi * frequencies * capacitance + 1.0 / 1.0e-3,
        rtol=1.0e-12,
        atol=1.0e-12,
    )
    assert result.diagnostics.active_termination_cluster_count == 1
    assert result.diagnostics.excluded_selected_rail_cluster_count == 0


def test_physical_termination_surface_maps_through_ideal_reduced_node() -> None:
    capacitance = 2.5e-9
    load_ohm = 2.0
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(1, 3, capacitance, upper="L2", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "IDEAL", NODES[0], NODES[1], 1, "topology_only_ideal"
            ),
        ),
        ports=(LayerSurfacePort("R1", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "other-r2", "R2", NODES[1], NODES[3], load_ohm, "cap:r2"
            ),
        ),
    )
    mapping = network.termination_reduced_node_mapping(manifest)
    assert mapping[0] == mapping[1] == network.reduced_node_index(NODES[0])

    frequencies = np.asarray([1.0e6, 5.0e6])
    result = network.solve(
        frequencies,
        termination_manifest=manifest,
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    np.testing.assert_allclose(
        result.effective_admittance_by_port["R1"],
        1j * 2.0 * np.pi * frequencies * capacitance + 1.0 / load_ohm,
        rtol=1.0e-12,
        atol=1.0e-16,
    )


def test_termination_edge_participates_in_component_and_gauge_construction() -> None:
    capacitance = 2.0e-9
    load_ohm = 3.0
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 2, capacitance, upper="L1", lower="L2"),),
        via_links=(),
        ports=(LayerSurfacePort("R1", NODES[0], NODES[3]),),
    )
    with pytest.raises(LayerSurfaceNetworkError, match="spans disconnected"):
        network.solve([2.0e6])

    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "bridge-r2", "R2", NODES[2], NODES[3], load_ohm, "cap:r2"
            ),
        ),
    )
    frequency = 2.0e6
    result = network.solve(
        [frequency],
        termination_manifest=manifest,
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    expected = 1.0 / (
        1.0 / (1j * 2.0 * np.pi * frequency * capacitance) + load_ohm
    )
    np.testing.assert_allclose(
        result.effective_admittance_by_port["R1"][0],
        expected,
        rtol=1.0e-11,
        atol=1.0e-16,
    )
    assert result.diagnostics.structural_component_count == 2


def test_termination_owner_cannot_duplicate_a_base_via_owner() -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(1, 3, 2.0e-9, upper="L2", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "BASE-VIA",
                NODES[0],
                NODES[1],
                1,
                "topology_only_ideal",
                owner_ids=("via:shared",),
            ),
        ),
        ports=(LayerSurfacePort("R1", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "duplicate", "R2", NODES[1], NODES[3], 2.0, "VIA:SHARED"
            ),
        ),
    )

    with pytest.raises(LayerSurfaceNetworkError, match="duplicates base link"):
        network.solve(
            [1.0e6],
            termination_manifest=manifest,
            selected_rail_id="R1",
            base_network_identity_sha256=BASE_NETWORK_SHA256,
        )


def test_termination_mapping_cache_counts_once_and_does_not_trust_sha_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(1, 3, 2.0e-9, upper="L2", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "BASE-VIA",
                NODES[0],
                NODES[1],
                1,
                "topology_only_ideal",
                owner_ids=("via:shared",),
            ),
        ),
        ports=(LayerSurfacePort("R1", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "safe", "R2", NODES[1], NODES[3], 2.0, "cap:safe"
            ),
        ),
    )
    original = type(network)._termination_reduced_node_mapping_uncached
    calls = 0

    def counted(
        instance: object,
        candidate: object,
    ) -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        return original(instance, candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(
        type(network),
        "_termination_reduced_node_mapping_uncached",
        counted,
    )

    expected = tuple(network.reduced_node_index(item) for item in NODES)
    for _rail_index in range(92):
        assert network.termination_reduced_node_mapping(manifest) == expected
    assert calls == 1

    conflicting = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "conflict", "R2", NODES[1], NODES[3], 2.0, "VIA:SHARED"
            ),
        ),
    )
    # A separately constructed object must not reuse a cache entry even if a
    # caller forges the same public digest.  Its owner conflict remains fatal.
    conflicting = replace(
        conflicting,
        manifest_sha256=manifest.manifest_sha256,
    )
    with pytest.raises(LayerSurfaceNetworkError, match="duplicates base link"):
        network.termination_reduced_node_mapping(conflicting)
    assert calls == 2
    assert network.termination_reduced_node_mapping(manifest) == expected
    assert calls == 2

    network.clear_termination_mapping_cache()
    assert not network._termination_mapping_cache
    assert network.termination_reduced_node_mapping(manifest) == expected
    assert calls == 3

    cloned = replace(network)
    assert cloned._termination_mapping_cache is not network._termination_mapping_cache
    assert (
        cloned._termination_mapping_cache_lock
        is not network._termination_mapping_cache_lock
    )
    assert not cloned._termination_mapping_cache


def test_termination_mapping_cache_is_bounded_by_exact_manifest_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(0, 3, 2.0e-9, upper="L1", lower="L3"),),
        via_links=(),
        ports=(LayerSurfacePort("R1", NODES[0], NODES[3]),),
    )
    manifests = tuple(
        compile_layer_surface_termination_manifest(
            NODES,
            (
                _termination_cluster(
                    f"safe-{index}",
                    "R2",
                    NODES[0],
                    NODES[3],
                    2.0,
                    f"cap:{index}",
                ),
            ),
        )
        for index in range(5)
    )
    original = type(network)._termination_reduced_node_mapping_uncached
    calls = 0

    def counted(
        instance: object,
        candidate: object,
    ) -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        return original(instance, candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(
        type(network),
        "_termination_reduced_node_mapping_uncached",
        counted,
    )

    for manifest in manifests:
        network.termination_reduced_node_mapping(manifest)
    assert calls == 5
    assert len(network._termination_mapping_cache) == 4

    network.termination_reduced_node_mapping(manifests[0])
    assert calls == 6
    assert len(network._termination_mapping_cache) == 4


def test_termination_mapping_cache_rechecks_same_object_after_dependency_tamper() -> None:
    network = compile_layer_surface_network(
        NODES,
        partials=(_edge(1, 3, 2.0e-9, upper="L2", lower="L3"),),
        via_links=(
            LayerSurfaceViaLink(
                "BASE-VIA",
                NODES[0],
                NODES[1],
                1,
                "topology_only_ideal",
                owner_ids=("via:shared",),
            ),
        ),
        ports=(LayerSurfacePort("R1", NODES[0], NODES[3]),),
    )
    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "safe", "R2", NODES[1], NODES[3], 2.0, "cap:safe"
            ),
        ),
    )
    network.termination_reduced_node_mapping(manifest)

    # A frozen production manifest cannot change through its public API.  This
    # adversarial mutation proves the shortcut still binds the exact digest
    # and cluster-owner dependencies rather than identity alone.
    object.__setattr__(manifest, "manifest_sha256", "f" * 64)
    conflicting = compile_layer_surface_termination_manifest(
        NODES,
        (
            _termination_cluster(
                "conflict", "R2", NODES[1], NODES[3], 2.0, "VIA:SHARED"
            ),
        ),
    )
    object.__setattr__(manifest, "clusters", conflicting.clusters)
    with pytest.raises(LayerSurfaceNetworkError, match="duplicates base link"):
        network.termination_reduced_node_mapping(manifest)
