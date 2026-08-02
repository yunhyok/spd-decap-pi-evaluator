from __future__ import annotations

import pickle

import numpy as np
import pytest

from spd_decap_pi._core.models import (
    LayerPairNetwork,
    LayerPairNetworkError,
    cascade_layer_pair_networks,
    cascade_layer_pair_stack,
)
from spd_decap_pi._core.models.layer_pair import _solve_interface


def _stamp(matrix: np.ndarray, left: int, right: int, admittance: complex) -> None:
    matrix[left, left] += admittance
    matrix[right, right] += admittance
    matrix[left, right] -= admittance
    matrix[right, left] -= admittance


def _pair_from_branches(
    frequencies: np.ndarray,
    top_ids: tuple[str, ...],
    bottom_ids: tuple[str, ...],
    branches: tuple[tuple[int, int, complex, complex], ...],
    *,
    grounding: complex = 0.0j,
) -> LayerPairNetwork:
    """Build one reciprocal pair from frequency-dependent branch admittances."""

    count = len(top_ids) + len(bottom_ids)
    matrix = np.zeros((frequencies.size, count, count), dtype=np.complex128)
    for frequency_index, frequency in enumerate(frequencies):
        for left, right, conductance, susceptance_scale in branches:
            _stamp(
                matrix[frequency_index],
                left,
                right,
                conductance + 1j * susceptance_scale * frequency / frequencies[0],
            )
        matrix[frequency_index, np.arange(count), np.arange(count)] += grounding
    return LayerPairNetwork(frequencies, top_ids, bottom_ids, matrix)


def _monolithic_reference(networks: tuple[LayerPairNetwork, ...]) -> np.ndarray:
    """Independently stamp, merge, and Kron-reduce an ordered layer stack."""

    frequency_count = networks[0].frequencies_hz.size
    interface_ids: list[tuple[str, ...]] = [networks[0].top_port_ids]
    interface_ids.extend(network.bottom_port_ids for network in networks)
    node_offsets: list[int] = []
    next_offset = 0
    for ids in interface_ids:
        node_offsets.append(next_offset)
        next_offset += len(ids)
    retained = tuple(
        range(len(interface_ids[0]))
    ) + tuple(
        range(node_offsets[-1], node_offsets[-1] + len(interface_ids[-1]))
    )
    output = np.empty(
        (frequency_count, len(retained), len(retained)), dtype=np.complex128
    )
    for frequency_index in range(frequency_count):
        full = np.zeros((next_offset, next_offset), dtype=np.complex128)
        for pair_index, network in enumerate(networks):
            top_ids = interface_ids[pair_index]
            if set(network.top_port_ids) != set(top_ids):
                raise AssertionError("test network top IDs do not match the previous interface")
            top_map = np.asarray(
                [
                    node_offsets[pair_index] + top_ids.index(port_id)
                    for port_id in network.top_port_ids
                ],
                dtype=np.intp,
            )
            bottom_ids = interface_ids[pair_index + 1]
            bottom_map = np.asarray(
                [
                    node_offsets[pair_index + 1] + bottom_ids.index(port_id)
                    for port_id in network.bottom_port_ids
                ],
                dtype=np.intp,
            )
            port_map = np.concatenate((top_map, bottom_map))
            full[np.ix_(port_map, port_map)] += network.admittance_siemens[
                frequency_index
            ]
        internal = tuple(index for index in range(next_offset) if index not in retained)
        y_rr = full[np.ix_(retained, retained)]
        y_ri = full[np.ix_(retained, internal)]
        y_ir = full[np.ix_(internal, retained)]
        y_ii = full[np.ix_(internal, internal)]
        output[frequency_index] = y_rr - y_ri @ np.linalg.solve(y_ii, y_ir)
    return output


def _passive_stack(pair_count: int) -> tuple[LayerPairNetwork, ...]:
    frequencies = np.asarray([1.0e6, 4.0e6, 2.0e7], dtype=np.float64)
    interfaces = tuple(
        (f"L{index}-A", f"L{index}-B") for index in range(pair_count + 1)
    )
    return tuple(
        _pair_from_branches(
            frequencies,
            interfaces[index],
            interfaces[index + 1][::-1],
            (
                (0, 2, 0.25 + 0.10 * index, 1.5e-8 + index * 0.3e-8),
                (0, 3, 0.07 + 0.02 * index, -0.4e-8),
                (1, 2, 0.06 + 0.03 * index, 0.5e-8),
                (1, 3, 0.31 + 0.08 * index, 1.1e-8 + index * 0.2e-8),
                (0, 1, 0.04 + 0.01 * index, 0.2e-8),
                (2, 3, 0.03 + 0.01 * index, -0.1e-8),
            ),
            grounding=0.13 + 0.01j,
        )
        for index in range(pair_count)
    )


@pytest.mark.parametrize("pair_count", [2, 3, 4])
def test_cascade_matches_independent_monolithic_kron(pair_count: int) -> None:
    networks = _passive_stack(pair_count)
    actual = cascade_layer_pair_stack(networks)
    expected = _monolithic_reference(networks)
    np.testing.assert_allclose(actual.admittance_siemens, expected, rtol=1e-11, atol=1e-11)
    actual_impedance = np.linalg.inv(actual.admittance_siemens)
    expected_impedance = np.linalg.inv(expected)
    np.testing.assert_allclose(actual_impedance, expected_impedance, rtol=1e-11, atol=1e-11)


def test_cascade_aligns_shuffled_interface_order() -> None:
    frequencies = np.asarray([1.0e6, 5.0e6])
    first = _pair_from_branches(
        frequencies,
        ("TOP",),
        ("I-A", "I-B"),
        ((0, 1, 0.4, 1.0e-8), (0, 2, 0.7, 1.7e-8)),
    )
    second_ordered = _pair_from_branches(
        frequencies,
        ("I-A", "I-B"),
        ("BOTTOM",),
        ((0, 2, 0.3, 0.8e-8), (1, 2, 0.6, 1.1e-8)),
    )
    permutation = (1, 0, 2)
    shuffled = LayerPairNetwork(
        frequencies,
        ("I-B", "I-A"),
        ("BOTTOM",),
        second_ordered.admittance_siemens[:, permutation, :][:, :, permutation],
    )
    expected = cascade_layer_pair_networks(first, second_ordered)
    actual = cascade_layer_pair_networks(first, shuffled)
    np.testing.assert_allclose(actual.admittance_siemens, expected.admittance_siemens, rtol=1e-12)


def test_series_special_case_and_midpoint_shunt_reject_scalar_sum() -> None:
    frequencies = np.asarray([1.0e6])
    series_first = _pair_from_branches(frequencies, ("A",), ("I",), ((0, 1, 1.0, 0.0),))
    series_second = _pair_from_branches(frequencies, ("I",), ("C",), ((0, 1, 0.5, 0.0),))
    series = cascade_layer_pair_networks(series_first, series_second)
    np.testing.assert_allclose(series.admittance_siemens[0, 0, 0], 1.0 / 3.0, rtol=1e-13)

    shunt = _pair_from_branches(frequencies, ("I",), ("C",), ((0, 1, 0.5, 0.0),))
    midpoint = LayerPairNetwork(
        frequencies,
        ("A",),
        ("I",),
        np.asarray([[[1.5, -1.0], [-1.0, 1.5]]], dtype=np.complex128),
    )
    actual = cascade_layer_pair_networks(midpoint, shunt)
    # Exact merged network retains a midpoint shunt (0.5 S).  A scalar series
    # merge would produce the branch-only two-port entry 1/3 S, while the
    # exact multiport has 1 S at A and -1/4 S transfer to C.
    np.testing.assert_allclose(actual.admittance_siemens[0, 0, 0], 1.0, rtol=1e-13)
    np.testing.assert_allclose(actual.admittance_siemens[0, 0, 1], -0.25, rtol=1e-13)
    assert not np.isclose(actual.admittance_siemens[0, 0, 1], -1.0 / 3.0)


def test_sampled_passivity_diagnostic_is_explicitly_limited() -> None:
    network = _passive_stack(2)[0]
    diagnostic = network.sampled_passivity_diagnostic()
    assert diagnostic.sampled_positive_semidefinite
    assert diagnostic.evaluated_frequency_count == 3
    assert diagnostic.worst_frequency_hz in network.frequencies_hz
    assert diagnostic.worst_frequency_tolerance_siemens > 0.0
    active = LayerPairNetwork(
        np.asarray([1.0e6]),
        ("A",),
        ("B",),
        np.asarray([[[-1.0, 0.0], [0.0, 1.0]]], dtype=np.complex128),
    )
    assert not active.sampled_passivity_diagnostic().sampled_positive_semidefinite


def test_passivity_diagnostic_uses_each_frequency_scale() -> None:
    network = LayerPairNetwork(
        np.asarray([1.0e6, 2.0e6]),
        ("A",),
        ("B",),
        np.asarray(
            [
                [[1.0e12, 0.0], [0.0, 1.0e12]],
                [[-0.5, 0.0], [0.0, 1.0]],
            ],
            dtype=np.complex128,
        ),
    )
    diagnostic = network.sampled_passivity_diagnostic()
    assert not diagnostic.sampled_positive_semidefinite
    assert diagnostic.worst_frequency_index == 1
    assert diagnostic.worst_frequency_hz == 2.0e6
    np.testing.assert_allclose(
        diagnostic.worst_frequency_minimum_conductance_eigenvalue_siemens,
        -0.5,
        rtol=1e-13,
    )
    np.testing.assert_allclose(
        diagnostic.worst_frequency_tolerance_siemens, 1.0e-12, rtol=1e-13
    )
    np.testing.assert_allclose(
        diagnostic.worst_frequency_margin_siemens,
        diagnostic.worst_frequency_minimum_conductance_eigenvalue_siemens
        + diagnostic.worst_frequency_tolerance_siemens,
        rtol=1e-13,
    )


def test_passivity_diagnostic_exact_zero_tolerance_and_tiny_active_network() -> None:
    lossless = LayerPairNetwork(
        np.asarray([1.0e6]),
        ("A",),
        ("B",),
        np.asarray([[[0.0, -2.0j], [-2.0j, 0.0]]], dtype=np.complex128),
    )
    exact = lossless.sampled_passivity_diagnostic(
        relative_tolerance=0.0, absolute_tolerance_siemens=0.0
    )
    assert exact.sampled_positive_semidefinite
    assert exact.worst_frequency_tolerance_siemens == 0.0
    tiny_active = LayerPairNetwork(
        np.asarray([1.0e6]),
        ("A",),
        ("B",),
        np.asarray([[[-1.0e-18, 0.0], [0.0, 2.0e-18]]], dtype=np.complex128),
    )
    assert not tiny_active.sampled_passivity_diagnostic().sampled_positive_semidefinite


def test_network_copies_and_freezes_numeric_inputs() -> None:
    frequencies = np.asarray([1.0e6])
    matrix = np.asarray([[[1.0, -1.0], [-1.0, 1.0]]], dtype=np.complex128)
    network = LayerPairNetwork(frequencies, ("A",), ("B",), matrix)
    frequencies[0] = 2.0e6
    matrix[0, 0, 0] = 9.0
    np.testing.assert_allclose(network.frequencies_hz, [1.0e6])
    np.testing.assert_allclose(network.admittance_siemens[0, 0, 0], 1.0)
    with pytest.raises(ValueError):
        network.admittance_siemens[0, 0, 0] = 2.0
    with pytest.raises(ValueError):
        network.admittance_siemens.setflags(write=True)
    with pytest.raises(ValueError):
        network.frequencies_hz.setflags(write=True)


def test_network_pickle_roundtrip_revalidates_immutable_backing() -> None:
    network = _passive_stack(2)[0]
    restored = pickle.loads(pickle.dumps(network))
    assert isinstance(restored, LayerPairNetwork)
    assert restored.top_port_ids == network.top_port_ids
    assert restored.bottom_port_ids == network.bottom_port_ids
    np.testing.assert_allclose(restored.frequencies_hz, network.frequencies_hz)
    np.testing.assert_allclose(restored.admittance_siemens, network.admittance_siemens)
    with pytest.raises(ValueError):
        restored.frequencies_hz.setflags(write=True)
    with pytest.raises(ValueError):
        restored.admittance_siemens.setflags(write=True)


@pytest.mark.parametrize(
    ("frequencies", "top", "bottom", "matrix", "message"),
    [
        (np.asarray([0.0]), ("A",), ("B",), np.ones((1, 2, 2)), "strictly positive"),
        (
            np.asarray([1.0, 1.0]),
            ("A",),
            ("B",),
            np.tile(np.eye(2, dtype=np.complex128), (2, 1, 1)),
            "strictly increasing",
        ),
        (
            np.asarray([2.0, 1.0]),
            ("A",),
            ("B",),
            np.tile(np.eye(2, dtype=np.complex128), (2, 1, 1)),
            "strictly increasing",
        ),
        (np.asarray([1.0]), ("A", "A"), ("B",), np.ones((1, 3, 3)), "must be unique"),
        (np.asarray([1.0]), ("A",), ("A",), np.ones((1, 2, 2)), "must be disjoint"),
        (np.asarray([1.0]), ("A",), ("B",), np.asarray([[[1.0, 2.0], [1.0, 1.0]]]), "reciprocal"),
        (np.asarray([1.0]), ("A",), ("B",), np.asarray([[[np.nan, 0.0], [0.0, 1.0]]]), "finite"),
    ],
)
def test_network_validation_fails_closed(
    frequencies: np.ndarray,
    top: tuple[str, ...],
    bottom: tuple[str, ...],
    matrix: np.ndarray,
    message: str,
) -> None:
    with pytest.raises(LayerPairNetworkError, match=message):
        LayerPairNetwork(frequencies, top, bottom, matrix)


def test_cascade_fails_closed_for_mismatch_grid_singular_and_ill_conditioned() -> None:
    good = _pair_from_branches(np.asarray([1.0e6]), ("A",), ("I",), ((0, 1, 1.0, 0.0),))
    mismatch = _pair_from_branches(np.asarray([2.0e6]), ("I",), ("C",), ((0, 1, 1.0, 0.0),))
    with pytest.raises(LayerPairNetworkError, match="same frequency grid"):
        cascade_layer_pair_networks(good, mismatch)
    wrong_ids = _pair_from_branches(np.asarray([1.0e6]), ("OTHER",), ("C",), ((0, 1, 1.0, 0.0),))
    with pytest.raises(LayerPairNetworkError, match="must match"):
        cascade_layer_pair_networks(good, wrong_ids)
    singular_first = LayerPairNetwork(
        np.asarray([1.0e6]), ("A",), ("I",), np.zeros((1, 2, 2), dtype=np.complex128)
    )
    singular_second = LayerPairNetwork(
        np.asarray([1.0e6]), ("I",), ("C",), np.zeros((1, 2, 2), dtype=np.complex128)
    )
    with pytest.raises(LayerPairNetworkError, match="singular|unstable"):
        cascade_layer_pair_networks(singular_first, singular_second)
    unstable_first = LayerPairNetwork(
        np.asarray([1.0e6]),
        ("A1", "A2"),
        ("I1", "I2"),
        np.asarray([[[0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0e-18]]]),
    )
    unstable_second = LayerPairNetwork(
        np.asarray([1.0e6]),
        ("I1", "I2"),
        ("C1", "C2"),
        np.zeros((1, 4, 4), dtype=np.complex128),
    )
    with pytest.raises(LayerPairNetworkError, match="unstable"):
        cascade_layer_pair_networks(unstable_first, unstable_second)


def _interface_condition_case(condition: float) -> tuple[LayerPairNetwork, LayerPairNetwork]:
    frequencies = np.asarray([1.0e6])
    first_matrix = np.zeros((1, 4, 4), dtype=np.complex128)
    first_matrix[0, 2:, 2:] = np.diag([1.0, 1.0 / condition])
    return (
        LayerPairNetwork(frequencies, ("A1", "A2"), ("I1", "I2"), first_matrix),
        LayerPairNetwork(
            frequencies,
            ("I1", "I2"),
            ("C1", "C2"),
            np.zeros((1, 4, 4), dtype=np.complex128),
        ),
    )


def test_cascade_enforces_explicit_forward_error_budget() -> None:
    epsilon = np.finfo(np.float64).eps
    accepted = _interface_condition_case(0.5e-8 / epsilon)
    cascade_layer_pair_networks(*accepted)
    rejected = _interface_condition_case(2.0e-8 / epsilon)
    with pytest.raises(LayerPairNetworkError, match="forward-error budget"):
        cascade_layer_pair_networks(*rejected)


def test_interface_residual_checks_each_rhs_column(monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = np.eye(2, dtype=np.complex128)
    right_hand_side = np.asarray([[1.0e12, 1.0e-12], [0.0, 0.0]], dtype=np.complex128)

    def weak_column_is_wrong(
        _matrix: np.ndarray, right_hand_side: np.ndarray
    ) -> np.ndarray:
        solution = right_hand_side.copy()
        solution[0, 1] = 1.0e-3
        return solution

    monkeypatch.setattr(np.linalg, "solve", weak_column_is_wrong)
    with pytest.raises(LayerPairNetworkError, match="numerically unstable"):
        _solve_interface(matrix, right_hand_side, frequency_hz=1.0e6)


def test_stack_requires_at_least_two_networks() -> None:
    with pytest.raises(LayerPairNetworkError, match="at least two"):
        cascade_layer_pair_stack((_passive_stack(2)[0],))
