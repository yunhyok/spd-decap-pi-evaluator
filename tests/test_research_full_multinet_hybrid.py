from __future__ import annotations

import numpy as np
import pytest

from spd_decap_pi._core.solver.global_mna import DifferentialPort
from spd_decap_pi.research_full_multinet_hybrid import (
    DecapRlcBranch,
    FullMultinetHybridError,
    RawComponentMaxwellCapacitance,
    SourceEvidence,
    compile_loaded_hybrid,
    uniform_mode_replacement_admittance,
)


def _raw() -> RawComponentMaxwellCapacitance:
    # P0--P1--DGND: raw indefinite full component matrix, not grounded C.
    c = 1.0e-9
    return RawComponentMaxwellCapacitance(
        ("P0/component-A", "P1/component-B", "DGND/component-1"),
        np.array(((c, -c, 0.0), (-c, 2 * c, -c), (0.0, -c, c))),
        ("DGND/component-1",),
        {"gap-1": np.array(((c / 3.3, -c / 3.3, 0.0), (-c / 3.3, 2 * c / 3.3, -c / 3.3), (0.0, -c / 3.3, c / 3.3)))},
    )


def _evidence(**updates: bool) -> SourceEvidence:
    values = dict(dgnd_connectivity_proven=True, component_artwork_connectivity_proven=True, decap_network_complete=True, shared_pad_network_complete=True, microvia_solid_fill_proven=True, microvia_geometry_complete=True)
    values.update(updates)
    return SourceEvidence(**values)


def test_raw_component_c_retains_all_nodes_and_unit_partials() -> None:
    raw = _raw()
    assert raw.node_ids == ("P0/component-A", "P1/component-B", "DGND/component-1")
    np.testing.assert_allclose(raw.admittance_s(1e6, {"gap-1": 3.3}), raw.admittance_s(1e6))
    with pytest.raises(FullMultinetHybridError, match="row sums"):
        RawComponentMaxwellCapacitance(("a", "g"), np.eye(2), ("g",))


def test_uniform_mode_is_replaced_not_double_counted() -> None:
    raw = _raw()
    frequency = 2e6
    correction = np.array(((0.0j, 1e-3j, -1e-3j), (1e-3j, 0.0j, -1e-3j), (-1e-3j, -1e-3j, 2e-3j)))
    combined = uniform_mode_replacement_admittance(raw, frequency, correction)
    np.testing.assert_allclose(combined, raw.admittance_s(frequency) + correction)


def test_source_evidence_blocks_before_any_loaded_schur_or_peec() -> None:
    with pytest.raises(FullMultinetHybridError, match="COMPONENT_ARTWORK_CONNECTIVITY_UNPROVEN"):
        compile_loaded_hybrid(
            _raw(), _evidence(component_artwork_connectivity_proven=False), decaps=(),
            ports=(DifferentialPort("p", "P0/component-A", "DGND/component-1"),),
        )


def test_loaded_rlc_network_solves_raw_complex_symmetric_mna() -> None:
    operator = compile_loaded_hybrid(
        _raw(), _evidence(),
        decaps=(DecapRlcBranch("C1", "P0/component-A", "DGND/component-1", 0.02, 0.2e-9, 100e-9),),
        ports=(
            DifferentialPort("P0", "P0/component-A", "DGND/component-1"),
            DifferentialPort("P1", "P1/component-B", "DGND/component-1"),
        ),
    )
    result = operator.solve(1.0e6)
    assert result.diagnostics.backward_relative_residual <= 1e-10
    np.testing.assert_allclose(result.impedance_ohm, result.impedance_ohm.T, atol=1e-12)
    assert result.impedance_ohm.shape == (2, 2)
