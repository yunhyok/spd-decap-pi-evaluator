import numpy as np
import pytest

from spd_decap_pi._core.io.touchstone import (
    TouchstoneError,
    TouchstoneNetwork,
    open_circuit_zpp,
    powersi_header_label_for_rail,
    powersi_rail_from_header_label,
    read_touchstone,
    s_to_z,
    validate_port_manifest,
)


def write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf8")
    return path


def test_nonreciprocal_three_port_is_row_major(tmp_path):
    values = "1e6 " + " ".join(f"{value} 0" for value in (.1, .2, .3, .4, .5, .6, .7, .8, .9))
    network = read_touchstone(write(tmp_path, "n.s3p", "# Hz S RI R 1\n" + values))
    assert network.s_parameters[0, 0, 1] == 0.2
    assert network.s_parameters[0, 1, 0] == 0.4


def test_legacy_two_port_order(tmp_path):
    network = read_touchstone(write(tmp_path, "n.s2p", "# MHz S RI R 50\n1 .1 0 .2 0 .3 0 .4 0"))
    assert network.frequencies_hz[0] == 1e6
    assert network.s_parameters[0, 0, 1] == 0.3
    assert network.s_parameters[0, 1, 0] == 0.2


def test_streaming_wrapped_records_and_comments(tmp_path):
    body = """! Port[1] = VDD/0
! Port[2] = VDD/1
# Hz S RI R 50
100000 0.1 0 0.2 0 ! first half of legacy record
0.3 0 0.4 0
! comments may occur between wrapped fields
1000000 0.11 0 0.21 0 0.31 0 0.41 0
"""
    network = read_touchstone(write(tmp_path, "stream.s2p", body))
    assert network.s_parameters.shape == (2, 2, 2)
    assert network.port_mapping == {1: "VDD/0", 2: "VDD/1"}
    assert network.s_parameters[0, 0, 1] == 0.3
    assert network.s_parameters[1, 1, 0] == 0.21


def test_partial_wrapped_record_fails_closed(tmp_path):
    with pytest.raises(TouchstoneError, match="complete records"):
        read_touchstone(write(tmp_path, "partial.s2p", "# Hz S RI R 50\n1 .1 0 .2 0 .3 0"))


def test_numeric_data_before_option_line_fails_closed(tmp_path):
    with pytest.raises(TouchstoneError, match="must precede"):
        read_touchstone(write(tmp_path, "out-of-order.s1p", "1 .1 0\n# Hz S RI R 50"))


@pytest.mark.parametrize(
    ("fmt", "pair", "expected"),
    [("RI", "0.5 0.5", 0.5 + 0.5j), ("MA", "2 90", 2j), ("DB", "6.020599913 180", -2 + 0j)],
)
def test_format_units_wrapping_and_inline_comments(tmp_path, fmt, pair, expected):
    text = f"! Port[1] = A\n# GHz S {fmt} R 50\n1 {pair} {pair} ! inline\n{pair} {pair}\n"
    network = read_touchstone(write(tmp_path, "x.s2p", text))
    assert network.port_mapping == {1: "A"}
    assert np.allclose(network.s_parameters[0, 0, 0], expected)


def test_known_s_to_z_and_open_port(tmp_path):
    network = read_touchstone(write(tmp_path, "x.s1p", "# Hz S RI R 50\n1e6 0.5 0"))
    result = s_to_z(network)
    assert np.allclose(result.z_parameters[0, 0, 0], 150)
    assert np.allclose(open_circuit_zpp(result, 1), [150])
    with pytest.raises(TouchstoneError):
        open_circuit_zpp(result, True)
    with pytest.raises(TouchstoneError):
        open_circuit_zpp(result, 1.5)


def test_open_circuit_zpp_is_not_a_matched_other_port_measurement():
    z_parameters = np.asarray([[[10 + 0j, 5 + 0j], [5 + 0j, 20 + 0j]]])
    result = type("Result", (), {"z_parameters": z_parameters})()
    open_input = open_circuit_zpp(result, 1)[0]
    matched_other_port = z_parameters[0, 0, 0] - z_parameters[0, 0, 1] * z_parameters[0, 1, 0] / (z_parameters[0, 1, 1] + 50.0)
    assert open_input == 10.0
    assert matched_other_port == pytest.approx(10.0 - 25.0 / 70.0)
    assert open_input != matched_other_port


def test_port_manifest_requires_exact_header_labels(tmp_path):
    network = read_touchstone(
        write(
            tmp_path,
            "x.s2p",
            "! Port[1] = 2nd_SITE0-VDD/0\n! Port[2] = 2nd_SITE1-VDD/1\n# Hz S RI R 50\n1 .1 0 .2 0 .3 0 .4 0",
        )
    )
    assert validate_port_manifest(network, {"VDD/0": 1, "VDD/1": 2}, require_complete_header=True) == {1: "2nd_SITE0-VDD/0", 2: "2nd_SITE1-VDD/1"}
    with pytest.raises(TouchstoneError, match="mismatch"):
        validate_port_manifest(network, {"VDD/1": 1})


def test_port_manifest_allows_only_explicit_header_override_or_powersi_site_convention(tmp_path):
    network = read_touchstone(
        write(tmp_path, "x.s1p", "! Port[1] = custom-labelled-port\n# Hz S RI R 50\n1 .1 0")
    )
    assert powersi_header_label_for_rail("ADC_VDD_075_VCPU/1") == "2nd_SITE1-ADC_VDD_075_VCPU/1"
    assert validate_port_manifest(network, {"ADC_VDD_075_VCPU/0": 1}, expected_header_labels={"ADC_VDD_075_VCPU/0": "custom-labelled-port"}) == {1: "custom-labelled-port"}
    with pytest.raises(TouchstoneError, match="mismatch"):
        validate_port_manifest(network, {"ADC_VDD_075_VCPU/0": 1})
    with pytest.raises(TouchstoneError, match="requires a rail"):
        powersi_header_label_for_rail("ADC_VDD_075_VCPU")


def test_run_qualified_power_si_header_is_exactly_canonicalized(tmp_path):
    network = read_touchstone(
        write(
            tmp_path,
            "x.s2p",
            "! Port[1] = SITE0_0805-VDD/0\n"
            "! Port[2] = SITE1_0805-VDD/1\n"
            "# Hz S RI R 50\n1 .1 0 .2 0 .3 0 .4 0",
        )
    )

    assert powersi_rail_from_header_label("2nd_SITE0-VDD/0") == "VDD/0"
    assert powersi_rail_from_header_label("SITE1_0805-VDD/1") == "VDD/1"
    assert validate_port_manifest(
        network,
        {"VDD/0": 1, "VDD/1": 2},
        require_complete_header=True,
    ) == {1: "SITE0_0805-VDD/0", 2: "SITE1_0805-VDD/1"}


@pytest.mark.parametrize(
    "label",
    (
        "SITE1_0805-VDD/0",
        "SITE0-VDD/0",
        "2nd_SITE0_0805-VDD/0",
        "third_SITE0-VDD/0",
        "SITE0__0805-VDD/0",
        "SITE0_0805-VDD",
    ),
)
def test_power_si_header_parser_rejects_wrong_site_or_unknown_spelling(label):
    with pytest.raises(TouchstoneError):
        powersi_rail_from_header_label(label)


def test_explicit_header_override_supports_a_non_site_scenario_rail(tmp_path):
    network = read_touchstone(
        write(tmp_path, "x.s1p", "! Port[1] = externally-named-port\n# Hz S RI R 50\n1 .1 0")
    )
    assert validate_port_manifest(
        network,
        {"ADC_VDD_075_VCPU": 1},
        expected_header_labels={"ADC_VDD_075_VCPU": "externally-named-port"},
    ) == {1: "externally-named-port"}


@pytest.mark.parametrize(
    "body",
    [
        "# Hz S RI R 50\n1 .1 0",
        "# Hz S RI R 50\n2 .1 0\n1 .1 0",
        "# Hz Y RI R 50\n1 .1 0 .1 0 .1 0 .1 0",
    ],
)
def test_invalid_input_fails_closed(tmp_path, body):
    with pytest.raises(TouchstoneError):
        read_touchstone(write(tmp_path, "bad.s2p", body))


def test_option_only_file_fails_closed(tmp_path):
    with pytest.raises(TouchstoneError):
        read_touchstone(write(tmp_path, "empty.s1p", "# Hz S RI R 50\n"))


def test_s_to_z_rejects_near_singular_forward_error_but_accepts_high_z_boundary():
    frequency = np.asarray([1e6])
    safe = np.asarray([[[0j, 0j], [0j, 1.0 - 1e-7]]])
    accepted = s_to_z(TouchstoneNetwork(frequency, safe, 50.0, {}, "RI"))
    assert accepted.condition_numbers[0] < 1e8
    assert accepted.z_parameters[0, 1, 1].real > 1e8
    unsafe = np.asarray([[[0j, 0j], [0j, 1.0 - 1e-10]]])
    with pytest.raises(TouchstoneError, match="unreliable"):
        s_to_z(TouchstoneNetwork(frequency, unsafe, 50.0, {}, "RI"))
