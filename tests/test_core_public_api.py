from __future__ import annotations

from spd_decap_pi._core import io, solver


def test_production_packages_do_not_export_research_only_kernels() -> None:
    solver_research_names = {
        "MfdmOperator",
        "MfdmArtwork",
        "MultilayerCapacitanceModel",
        "UniformC00Assembly",
        "ViaPeecOperator",
        "compile_mfdm_operator",
        "extract_multilayer_bulk_capacitance",
        "compile_via_peec",
    }
    io_research_names = {
        "SpdConductorGraph",
        "ConductorComponent",
        "extract_spd_conductor_graph",
    }

    assert solver_research_names.isdisjoint(solver.__all__)
    assert io_research_names.isdisjoint(io.__all__)
    assert all(not hasattr(solver, name) for name in solver_research_names)
    assert all(not hasattr(io, name) for name in io_research_names)
