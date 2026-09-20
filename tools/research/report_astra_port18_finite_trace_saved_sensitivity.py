"""Saved-only first-order sensitivity of the accepted 1 MHz hybrid port to two trace resistances."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
OUTPUT = RESEARCH / "astra-port18-finite-trace-saved-sensitivity-20260912.json"

PINS = {
    "accepted_result": (
        RESEARCH / "astra-l02-hybrid-right-correction-01" / "result.json",
        "7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3",
    ),
    "accepted_field": (
        RESEARCH / "astra-l02-hybrid-right-correction-01" / "field.npz",
        "960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b",
    ),
    "hybrid_pack": (
        RESEARCH / "astra-l02-full-face-hybrid-operator-06" / "hybrid-operator-pack.npz",
        "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
    ),
    "selected_chain": (
        RESEARCH / "astra-port18-selected-pin-chain-topology-20260912.json",
        "0c6943c193f03f05bcfb4ba81950997afbc23513641993c4ed3bee7f93a259bc",
    ),
    "trace_dc": (
        RESEARCH / "astra-port18-source-trace-dc-20260912-02" / "result.json",
        "9aad2e6eccde871e24d57bd56c4298d6a7657e9c4bfb7ba973bbbe32924971a1",
    ),
}

TARGETS = (
    {"owner": "via:via507122", "pack_row": 571168, "trace_id": "Trace480236"},
    {"owner": "via:via507117", "pack_row": 945101, "trace_id": "Trace480235"},
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cpair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def main() -> None:
    verified = {}
    for name, (path, expected) in PINS.items():
        actual = sha256(path)
        if actual != expected:
            raise AssertionError(f"{name} hash mismatch: {actual}")
        verified[name] = {"path": str(path.resolve()), "sha256": actual}

    accepted = json.loads(PINS["accepted_result"][0].read_text(encoding="utf-8"))
    chain = json.loads(PINS["selected_chain"][0].read_text(encoding="utf-8"))
    dc = json.loads(PINS["trace_dc"][0].read_text(encoding="utf-8"))
    if accepted["status"] != "COMPLETED_CONDITIONAL_HYBRID_BLOCK_LGMRES_1MHZ":
        raise AssertionError("accepted result status changed")
    if int(accepted["lgmres"]["info"]) != 0:
        raise AssertionError("accepted result is not numerically converged")
    if not all(bool(value) for value in accepted["physical"]["gates"].values()):
        raise AssertionError("accepted physical receipt has a failed gate")
    if dc["status"] != "COMPLETED_SOURCE_TRACE_DC_CANDIDATES_NOT_BOARD_VALIDATED":
        raise AssertionError("trace DC status changed")

    chain_by_owner = {row["owners"][0].lower(): row for row in chain["rows"]}
    dc_by_trace = {case["trace_id"]: case for case in dc["cases"]}
    with np.load(PINS["accepted_field"][0], allow_pickle=False) as field, np.load(
        PINS["hybrid_pack"][0], allow_pickle=False
    ) as pack:
        voltage = np.asarray(field["active_voltage_v"], dtype=np.complex128)
        port_indices = np.asarray(
            field["source_positive_negative_gauge_active_indices"], dtype=np.int64
        )
        source_current = float(np.asarray(field["source_current_amplitude_a"]).reshape(-1)[0])
        if source_current == 0.0 or port_indices.shape != (3,):
            raise AssertionError("invalid source normalization or port index shape")

        def active_value(index: int) -> complex:
            return 0.0j if index < 0 else complex(voltage[index])

        z_field = (
            active_value(int(port_indices[0])) - active_value(int(port_indices[1]))
        ) / source_current
        z_receipt = complex(*accepted["physical"]["physical"]["zdd_ohm"])

        rows = []
        currents = []
        trace_resistances = []
        for target in TARGETS:
            chain_row = chain_by_owner[target["owner"]]
            row = int(target["pack_row"])
            if int(chain_row["pack_row"]) != row or int(chain_row["direction"]) != 1:
                raise AssertionError(f"source-path direction changed for {target['owner']}")
            first = int(pack["finite_first_active_index"][row])
            second = int(pack["finite_second_active_index"][row])
            if (first, second) != (int(chain_row["pack_first"]), int(chain_row["pack_second"])):
                raise AssertionError(f"pack endpoints changed for {target['owner']}")
            admittance = complex(pack["finite_admittance_s"][row])
            current = (active_value(first) - active_value(second)) * admittance
            trace_case = dc_by_trace[target["trace_id"]]
            if not bool(trace_case["passes_1pct_pair_screen"]):
                raise AssertionError(f"DC screen failed for {target['trace_id']}")
            trace_r = float(trace_case["resistance_candidate_ohm"])
            currents.append(current)
            trace_resistances.append(trace_r)
            rows.append(
                {
                    **target,
                    "pack_first_active_index": first,
                    "pack_second_active_index": second,
                    "source_path_direction": int(chain_row["direction"]),
                    "lookup_and_sign": "i=(active_voltage_v[first]-active_voltage_v[second])*finite_admittance_s[row]",
                    "branch_current_a": cpair(current),
                    "trace_resistance_candidate_ohm": trace_r,
                }
            )

    derivative = sum(r * i * i for r, i in zip(trace_resistances, currents)) / (
        source_current * source_current
    )
    spread = max(abs(currents[0] - currents[1]), 0.0)
    z_scale = max(abs(z_field), np.finfo(float).tiny)
    gates = {
        "field_port_matches_accepted_receipt": bool(abs(z_field - z_receipt) <= 2e-12 * z_scale),
        "selected_currents_finite": bool(np.isfinite(np.asarray(currents)).all()),
        "selected_source_path_directions_match_pack": True,
        "selected_current_spread_le_2e_12_a": bool(spread <= 2e-12),
        "dc_pair_screens_pass": True,
    }
    if not all(gates.values()):
        raise AssertionError(f"saved sensitivity gate failure: {gates}")

    report = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "SAVED_ONLY_FIRST_ORDER_SENSITIVITY_NOT_FINITE_RESPONSE_OR_ACCURACY_BOUND",
        "frequency_hz": 1_000_000.0,
        "inputs": verified,
        "accepted_solution": {
            "lgmres_info": int(accepted["lgmres"]["info"]),
            "scaled_residual_relative": float(accepted["lgmres"]["final_scaled_residual_relative"]),
            "all_physical_gates_pass": True,
            "source_positive_negative_gauge_active_indices": port_indices.tolist(),
            "source_current_amplitude_a": source_current,
            "z_from_saved_field_ohm": cpair(z_field),
            "z_from_accepted_physical_receipt_ohm": cpair(z_receipt),
        },
        "selected_rows": rows,
        "derivation": {
            "branch_orientation": "Each selected current is first-to-second in the final hybrid pack; both rows have source-path direction +1 in the pinned chain.",
            "port_voltage": "active_voltage_v[positive]-active_voltage_v[negative], with a negative index interpreted as the zero-volt gauge",
            "ordinary_bilinear_formula": "delta_Z=sum_k(delta_R_k*i_k*i_k)/(I_source*I_source); no complex conjugation",
            "normalization": "The accepted field uses I_source=source_current_amplitude_a; both branch currents and delta_Z use that same saved normalization.",
        },
        "metrics": {
            "selected_current_spread_a": spread,
            "delta_z_first_order_ohm": cpair(derivative),
            "delta_z_abs_ohm": float(abs(derivative)),
            "delta_z_relative_to_accepted_z_abs": float(abs(derivative) / z_scale),
            "delta_z_parts_per_million_of_accepted_z_abs": float(1e6 * abs(derivative) / z_scale),
        },
        "gates": gates,
        "limitations": [
            "This is an ordinary-transpose first-order same-port sensitivity evaluated at one accepted conditional 1 MHz field.",
            "It is not an exact finite-resistance re-solve, a broadband bound, a PowerSI accuracy claim, or evidence that the two local traces dominate the port error.",
            "The trace resistance is a conditional finite-contact DC candidate; AC magnetic and larger field coupling remain outside this calculation.",
        ],
    }
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "sha256": sha256(OUTPUT), "metrics": report["metrics"]}))


if __name__ == "__main__":
    main()
