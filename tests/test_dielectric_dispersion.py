from __future__ import annotations

import mmap
from pathlib import Path

import numpy as np
import pytest

from spd_decap_pi._core.domain import (
    DielectricPropertyPoint,
    MLOOutline,
    ProjectSpec,
    RailSpec,
    StackupLayer,
)
from spd_decap_pi._core.solver.evaluator import _plane_component
from spd_decap_pi._core.io.spd import _parse_layers, _parse_materials
from spd_decap_pi._core.solver.modal import (
    EPSILON_0_F_PER_M,
    DielectricDispersion,
    DielectricLayer,
    ModalSolverError,
    RectangularCavitySolver,
    RectangularPlane,
)
from spd_decap_pi.scenario import ScenarioSpec, SourceIdentity
from spd_decap_pi.scenario_io import load_scenario, save_scenario


def _parse_fixture(tmp_path: Path, text: str):
    source = tmp_path / "materials.spd"
    source.write_text(text, encoding="ascii")
    diagnostics = []
    with source.open("rb") as stream, mmap.mmap(
        stream.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        dielectrics, metals = _parse_materials(data, 0, len(data), diagnostics)
        layers = _parse_layers(
            data, 0, len(data), {}, dielectrics, metals, set(), diagnostics
        )
    return dielectrics, layers, diagnostics


MATERIAL_TABLE = """.DielectricModel ABF
*Frequency(MHz) Permittivity LossTangent
1 3.4 0.005
1000 3.3 0.004
.EndDielectricModel
"""


def test_dielectric_dispersion_interpolates_log_frequency_and_clamps() -> None:
    dispersion = DielectricDispersion(
        frequencies_hz=(1.0e6, 1.0e9),
        relative_permittivities=(3.4, 3.1),
        loss_tangents=(0.0041, 0.0038),
    )

    dk, df = dispersion.interpolate(np.asarray([1.0e5, 1.0e7, 1.0e9, 1.0e10]))

    assert dk == pytest.approx([3.4, 3.3, 3.1, 3.1])
    assert df == pytest.approx([0.0041, 0.0040, 0.0038, 0.0038])


@pytest.mark.parametrize(
    "frequencies,dk,df",
    [
        ((1.0e6, 1.0e6), (3.4, 3.3), (0.004, 0.004)),
        ((1.0e6,), (0.0,), (0.004,)),
        ((1.0e6,), (3.4,), (-0.001,)),
    ],
)
def test_dielectric_dispersion_rejects_invalid_source_table(
    frequencies: tuple[float, ...], dk: tuple[float, ...], df: tuple[float, ...]
) -> None:
    with pytest.raises(ModalSolverError):
        DielectricDispersion(frequencies, dk, df)


def test_modal_shunt_uses_complex_series_dielectric_rows() -> None:
    first = DielectricLayer(
        thickness_m=10e-6,
        dispersion=DielectricDispersion((1e6, 1e9), (4.0, 3.0), (0.01, 0.02)),
        material="A",
    )
    second = DielectricLayer(
        thickness_m=20e-6,
        dispersion=DielectricDispersion((1e6,), (5.0,), (0.03,)),
        material="B",
    )
    plane = RectangularPlane(
        width_m=1e-3,
        height_m=1e-3,
        separation_m=30e-6,
        relative_permittivity=4.0,
        dielectric_layers=(first, second),
    )
    frequencies = np.asarray([1e6, 1e9])

    actual = RectangularCavitySolver._shunt_admittance_per_area(frequencies, plane)
    expected = []
    for frequency, dk, df in ((1e6, 4.0, 0.01), (1e9, 3.0, 0.02)):
        denominator = 10e-6 / (EPSILON_0_F_PER_M * dk * (1.0 - 1j * df))
        denominator += 20e-6 / (EPSILON_0_F_PER_M * 5.0 * (1.0 - 1j * 0.03))
        expected.append(1j * 2.0 * np.pi * frequency / denominator)
    assert actual == pytest.approx(expected)


def test_modal_shunt_without_table_retains_legacy_scalar_equation() -> None:
    plane = RectangularPlane(1e-3, 1e-3, 30e-6, 3.4, loss_tangent=0.004)
    frequency = np.asarray([1e6, 1e9])
    actual = RectangularCavitySolver._shunt_admittance_per_area(frequency, plane)
    expected = (
        1j
        * 2.0
        * np.pi
        * frequency
        * EPSILON_0_F_PER_M
        * 3.4
        * (1.0 - 1j * 0.004)
        / 30e-6
    )
    assert actual == pytest.approx(expected)


@pytest.mark.parametrize(
    "layer_attributes, expected_dk, expected_df, expected_table_length",
    [
        ("Permittivity = 3.8", (3.8, 3.8), (0.005, 0.004), 2),
        ("LossTangent = 0.012", (3.4, 3.3), (0.012, 0.012), 2),
        ("Permittivity = 3.8 LossTangent = 0.012", (), (), 0),
    ],
)
def test_layer_dielectric_overrides_take_precedence_per_axis(
    tmp_path: Path,
    layer_attributes: str,
    expected_dk: tuple[float, ...],
    expected_df: tuple[float, ...],
    expected_table_length: int,
) -> None:
    fixture = MATERIAL_TABLE + (
        f"Medium$D1 Thickness = 30um Material = ABF {layer_attributes}\n"
    )

    _materials, layers, diagnostics = _parse_fixture(tmp_path, fixture)

    assert {item.code for item in diagnostics} == {"METAL_MODELS_MISSING"}
    layer = layers[0]
    assert len(layer.dielectric_properties) == expected_table_length
    assert tuple(item.dk for item in layer.dielectric_properties) == expected_dk
    assert tuple(item.df for item in layer.dielectric_properties) == expected_df
    if "Permittivity" in layer_attributes:
        assert layer.dk == pytest.approx(3.8)
    if "LossTangent" in layer_attributes:
        assert layer.df == pytest.approx(0.012)


@pytest.mark.parametrize(
    "rows",
    [
        "1 3.4 0.005\n1 3.3 0.004",
        "-1 3.4 0.005\n1000 3.3 0.004",
    ],
)
def test_invalid_or_duplicate_material_frequency_discards_curve_with_diagnostic(
    tmp_path: Path, rows: str
) -> None:
    fixture = f""".DielectricModel ABF
*Frequency(MHz) Permittivity LossTangent
{rows}
.EndDielectricModel
Medium$D1 Thickness = 30um Material = ABF
"""

    materials, layers, diagnostics = _parse_fixture(tmp_path, fixture)

    assert materials["abf"].properties == ()
    assert layers[0].dielectric_properties == []
    assert any(item.code == "DIELECTRIC_MODEL_TABLE_INVALID" for item in diagnostics)


@pytest.mark.parametrize(
    "unit, value, expected_hz",
    [("Hz", 7.0, 7.0), ("kHz", 7.0, 7.0e3), ("GHz", 7.0, 7.0e9)],
)
def test_material_frequency_header_units_are_converted_to_hz(
    tmp_path: Path, unit: str, value: float, expected_hz: float
) -> None:
    fixture = f""".DielectricModel ABF
*Frequency({unit}) Permittivity LossTangent
{value:g} 3.4 0.005
.EndDielectricModel
Medium$D1 Thickness = 30um Material = ABF
"""

    _materials, layers, diagnostics = _parse_fixture(tmp_path, fixture)

    assert {item.code for item in diagnostics} == {"METAL_MODELS_MISSING"}
    assert layers[0].dielectric_properties[0].frequency_hz == pytest.approx(
        expected_hz
    )


def test_project_plane_combines_dispersive_and_scalar_dielectric_rows() -> None:
    layers = [
        StackupLayer(name="PWR", thickness_um=20.0, conductivity_s_m=5.8e7),
        StackupLayer(
            name="D1",
            thickness_um=10.0,
            dk=3.3,
            df=0.004,
            dielectric_properties=[
                DielectricPropertyPoint(frequency_hz=1e6, dk=3.4, df=0.005),
                DielectricPropertyPoint(frequency_hz=1e9, dk=3.3, df=0.004),
            ],
        ),
        StackupLayer(name="D2", thickness_um=20.0, dk=4.2, df=0.01),
        StackupLayer(name="GND", thickness_um=20.0, conductivity_s_m=5.8e7),
    ]

    plane = _plane_component(
        layers[0], layers[3], layers, 0, 3, 1000.0, 1000.0, selected=True
    )
    dk, df = plane.dielectric_layers[1].dispersion.interpolate([1e6, 1e9])

    assert len(plane.dielectric_layers) == 2
    assert dk == pytest.approx([4.2, 4.2])
    assert df == pytest.approx([0.01, 0.01])


def _project(*, low_dk: float) -> ProjectSpec:
    return ProjectSpec(
        outline=MLOOutline(width_um=1_000.0, height_um=1_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(
                name="PWR",
                thickness_um=20.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD"],
            ),
            StackupLayer(
                name="D1",
                thickness_um=30.0,
                dk=3.3,
                df=0.004,
                material="ABF-GL102",
                dielectric_properties=[
                    DielectricPropertyPoint(frequency_hz=1e6, dk=low_dk, df=0.0041),
                    DielectricPropertyPoint(frequency_hz=1e9, dk=3.3, df=0.004),
                ],
            ),
            StackupLayer(
                name="GND",
                thickness_um=20.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        rails=[
            RailSpec(
                rail_id="VDD",
                family="VDD",
                domain="VDD",
                net="VDD",
                site="SITE0",
                pwr_layer="PWR",
                gnd_layer="GND",
            )
        ],
    )


def test_project_plane_carries_source_material_table_and_scenario_fingerprint_changes() -> None:
    first = _project(low_dk=3.4)
    second = _project(low_dk=3.5)
    plane = _plane_component(
        first.stackup_layers[0],
        first.stackup_layers[2],
        first.stackup_layers,
        0,
        2,
        1_000.0,
        1_000.0,
        selected=True,
    )
    assert plane.relative_permittivity == pytest.approx(3.3)
    assert plane.dielectric_layers[0].material == "ABF-GL102"
    assert plane.dielectric_layers[0].dispersion.frequencies_hz == (1e6, 1e9)

    source = SourceIdentity(
        path="board.spd", name="board.spd", size_bytes=1, sha256="a" * 64
    )
    first_scenario = ScenarioSpec(source=source, normalized_project=first.model_dump(mode="json"))
    second_scenario = ScenarioSpec(source=source, normalized_project=second.model_dump(mode="json"))
    assert first_scenario.design_fingerprint != second_scenario.design_fingerprint


def test_legacy_normalized_project_without_table_is_not_rewritten_with_defaults() -> None:
    project = _project(low_dk=3.4).model_dump(mode="json")
    for row in project["stackup_layers"]:
        row.pop("material", None)
        row.pop("dielectric_properties", None)
    source = SourceIdentity(
        path="board.spd", name="board.spd", size_bytes=1, sha256="a" * 64
    )

    scenario = ScenarioSpec(source=source, normalized_project=project)

    assert all(
        "material" not in row and "dielectric_properties" not in row
        for row in scenario.normalized_project["stackup_layers"]
    )


def test_dispersion_table_survives_scenario_save_reload_with_same_fingerprint(
    tmp_path: Path,
) -> None:
    source = SourceIdentity(
        path="board.spd", name="board.spd", size_bytes=1, sha256="a" * 64
    )
    scenario = ScenarioSpec(
        source=source,
        normalized_project=_project(low_dk=3.4).model_dump(mode="json"),
    )
    destination = tmp_path / "dispersion.spdpi"

    save_scenario(scenario, destination)
    loaded = load_scenario(destination)

    assert loaded.design_fingerprint == scenario.design_fingerprint
    dielectric = loaded.base_project.stackup_layers[1]
    assert dielectric.material == "ABF-GL102"
    assert [item.frequency_hz for item in dielectric.dielectric_properties] == [
        1e6,
        1e9,
    ]
