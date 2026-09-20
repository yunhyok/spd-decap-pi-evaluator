"""Conditional local L21 sheet plus four saved via branches at 1 MHz."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import pi
from pathlib import Path
import sys
from time import monotonic
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.solver.global_mna import (  # noqa: E402
    DifferentialPort, EquipotentialConstraint, NodalAdmittanceBlock,
    SeriesBranchBlock, compile_global_mna,
)

PROGRAM, VERSION, FREQUENCY_HZ = "SPD Decap PI Evaluator", "0.23.1", 1e6
OUTPUT = ROOT / "docs/evaluation-research/astra_l21_local_power_feed_2026-09-07-02.json"
INPUTS = {
    "boundary10": ("docs/evaluation-research/astra_l21_triangle_boundary10_2026-09-07.json", "547f06c5c41d4f4a1d8ee19c70ba449d931546a5b3e6891efc4c74ed0c4c9b3d"),
    "source": ("docs/evaluation-research/astra_l21_four_terminal_dc_2026-09-07.json", "76bbb152f89b0e8086128f34a489f06c77ba10b2dfae0b952fc245723881ce3b"),
    "ownership": ("outputs/research/astra-step4-basis-01/replacement-boundary.json", "8de6711dd64ba9d4391f5b604223cb6f6eae70bc0df20faaf044b3a59c8eef22"),
    "old_triangle": ("docs/evaluation-research/astra_l21_triangle_mesh_2026-09-07.json", "dc9b2816fb8dd109db55e4080bfe049a73a2ac65c2fd1af3c7c2ed6800bc1589"),
}
CONTACTS = ("Node2140546", "Node2140547", "Node2140578", "Node2140579")
EXTERNAL = ("compiled-node:750336", "compiled-node:229906", "compiled-node:482985")
OWNERS = ("via:via336239", "via:via336240", "via:via336279", "via:via336280")


def require(value, message):
    if not value:
        raise ValueError(message)


def load_inputs():
    docs, evidence = {}, {}
    for name, (relative, expected) in INPUTS.items():
        path, raw = ROOT / relative, (ROOT / relative).read_bytes()
        require(sha256(raw).hexdigest() == expected, f"input hash mismatch: {name}")
        docs[name] = json.loads(raw)
        evidence[name] = {"path": relative, "size_bytes": len(raw), "sha256": expected}
    return docs, evidence


def scalar(name, positive, negative, impedance, owner):
    return SeriesBranchBlock((name,), (positive,), (negative,), np.array(((impedance,),)), f"native-{name}", (owner,))


def relative(a, b):
    return float(np.linalg.norm(a - b) / max(float(np.linalg.norm(b)), 1e-30))


def cmatrix(a):
    return [[[float(complex(x).real), float(complex(x).imag)] for x in row] for row in a]


def solve(kind, nodes, y, r, vias, ports):
    owner = ("source-asset:b0ab5dda526415e8:conditional-l21-dc-sheet",)
    kwargs = {"branch_blocks": vias, "ports": ports}
    start = 0
    if kind == "nodal":
        kwargs["nodal_admittances"] = (NodalAdmittanceBlock(CONTACTS, y, "conditional-l21-y4", owner),)
    elif kind == "branch":
        sheet = SeriesBranchBlock(("sheet-left", "sheet-upper", "sheet-lower"), CONTACTS[1:], (CONTACTS[0],) * 3, r, "conditional-l21-r3", owner)
        kwargs["branch_blocks"] = (sheet, *vias)
        start = 3
    elif kind == "ideal":
        kwargs["equipotential_constraints"] = tuple(EquipotentialConstraint(f"ideal-{i}", node, CONTACTS[0]) for i, node in enumerate(CONTACTS[1:]))
    result = compile_global_mna(nodes, **kwargs).solve(FREQUENCY_HZ)
    return result, result.branch_currents_a_per_a[start:start + 4]


def diagnostics(result):
    d = result.diagnostics
    return {name: getattr(d, name) for name in (
        "component_count", "gauge_node_ids", "branch_impedance_condition_estimate",
        "scaled_saddle_condition_estimate", "backward_relative_residual",
        "nodal_kcl_relative_residual", "branch_equation_relative_residual",
        "raw_reciprocity_relative_error", "min_hermitian_impedance_eigenvalue_ohm",
        "passivity_tolerance_ohm",
    )}


def build_result(limit):
    started = monotonic()
    docs, evidence = load_inputs()
    boundary, source, ownership, old = (docs[k] for k in ("boundary10", "source", "ownership", "old_triangle"))
    require(boundary["status"] == "DIAGNOSTIC_LOCAL_DC_SINGLE_MESH_ONLY" and boundary["failure"] is None, "boundary10 status changed")
    require(not source["native_ac_replacement_closed"] and ownership["status"] == "PARTIAL_SAME_BASIS_REPLACEMENT_NOT_READY", "ownership boundary changed")
    require(all(ownership["basis_matches"].values()), "same-basis ownership is incomplete")
    row, = boundary["sheet_rows"]
    require(tuple(row["contact_node_order"]) == CONTACTS and (row["circle_sides"], row["area_target_spacing_um"]) == (128, 5.0), "selected sheet row changed")
    y, r, basis = (np.asarray(value, dtype=np.complex128) for value in (row["y_s"], row["balanced_z_ohm"], boundary["balanced_basis"]))

    refs = {x["link_ordinal"]: x["owner_id"] for x in source["native_owner_refs"]}
    links = {refs[x["ordinal"]]: x for x in source["native_incident_links"] if x["kind"] == 1}
    expected = {
        OWNERS[0]: (750336, 680823, 0.0008328251054298727, 3.88101732126172e-10),
        OWNERS[1]: (229906, 680823, 0.000767864509798958, 3.1615798330306487e-11),
        OWNERS[2]: (482985, 680823, 0.000767864509798958, 3.1615798330306487e-11),
        OWNERS[3]: (482985, 680823, 0.000767864509798958, 3.1615798330306487e-11),
    }
    require(set(links) == set(OWNERS), "native owner set changed")
    for owner, values in expected.items():
        x = links[owner]
        require((x["first_node"], x["second_node"], x["resistance_ohm"], x["inductance_h"]) == values, f"native binding changed: {owner}")
    omega = 2 * pi * FREQUENCY_HZ
    zc = expected[OWNERS[0]][2] + 1j * omega * expected[OWNERS[0]][3]
    zm = expected[OWNERS[1]][2] + 1j * omega * expected[OWNERS[1]][3]
    vias = (
        scalar("via336239", EXTERNAL[0], CONTACTS[0], zc, OWNERS[0]),
        scalar("via336240", EXTERNAL[1], CONTACTS[1], zm, OWNERS[1]),
        scalar("via336279", EXTERNAL[2], CONTACTS[2], zm, OWNERS[2]),
        scalar("via336280", EXTERNAL[2], CONTACTS[3], zm, OWNERS[3]),
    )
    ports = (DifferentialPort("left", EXTERNAL[1], EXTERNAL[0]), DifferentialPort("right", EXTERNAL[2], EXTERNAL[0]))
    order, reverse = (*EXTERNAL, *CONTACTS), tuple(reversed((*EXTERNAL, *CONTACTS)))
    full, fi = solve("nodal", order, y, r, vias, ports)
    branch, bi = solve("branch", order, y, r, vias, ports)
    ideal, ii = solve("ideal", order, y, r, vias, ports)
    full2, fi2 = solve("nodal", reverse, y, r, vias, ports)
    branch2, bi2 = solve("branch", reverse, y, r, vias, ports)

    k = np.array(((1., 0., 0.), (0., 1., 1.)), dtype=np.complex128)
    aik = np.linalg.solve(r + np.eye(3) * zm, k.T)
    grouped = k @ aik
    analytic_z = zc * np.ones((2, 2)) + np.linalg.inv(grouped)
    micro_i = aik @ np.linalg.inv(grouped)
    analytic_i = np.vstack((-np.ones((1, 2)), micro_i))
    ideal_z = np.array(((zc + zm, zc), (zc, zc + zm / 2)))
    ideal_i = np.array(((-1., -1.), (1., 0.), (0., .5), (0., .5)), dtype=np.complex128)
    errors = {
        "full_vs_branch_z": relative(full.impedance_ohm, branch.impedance_ohm),
        "full_vs_analytic_z": relative(full.impedance_ohm, analytic_z),
        "full_vs_branch_via_currents": relative(fi, bi),
        "full_vs_analytic_via_currents": relative(fi, analytic_i),
        "full_reordered_z": relative(full2.impedance_ohm, full.impedance_ohm),
        "full_reordered_via_currents": relative(fi2, fi),
        "branch_reordered_z": relative(branch2.impedance_ohm, branch.impedance_ohm),
        "branch_reordered_via_currents": relative(bi2, bi),
        "ideal_vs_closed_form_z": relative(ideal.impedance_ohm, ideal_z),
        "ideal_vs_closed_form_via_currents": relative(ii, ideal_i),
        "y_from_balanced_r": relative(basis @ np.linalg.solve(r, basis.T), y),
        "analytic_current_kcl": float(np.linalg.norm(np.vstack((analytic_i[0] + analytic_i[1:].sum(axis=0), k @ micro_i - np.eye(2))))),
    }
    old_y, old_r = np.asarray(old["sheet_rows"][-1]["y_s"]), np.asarray(old["sheet_rows"][-1]["balanced_z_ohm"])
    old_sign = {"maximum_off_diagonal_y_s": float(old_y[~np.eye(4, dtype=bool)].max()), "minimum_balanced_z_entry_ohm": float(old_r.min()), "minimum_balanced_z_eigenvalue_ohm": float(np.linalg.eigvalsh(old_r).min())}
    selected_sign = {"maximum_off_diagonal_y_s": float(y.real[~np.eye(4, dtype=bool)].max()), "minimum_balanced_z_eigenvalue_ohm": float(np.linalg.eigvalsh(r.real).min())}
    ds = {name: diagnostics(value) for name, value in (("full", full), ("branch", branch), ("ideal", ideal), ("full_reordered", full2), ("branch_reordered", branch2))}
    checks = {
        "composition_and_gauge_errors_below_1e_10": all(x < 1e-10 for x in errors.values()),
        "gauge_nodes_changed": full.diagnostics.gauge_node_ids != full2.diagnostics.gauge_node_ids and branch.diagnostics.gauge_node_ids != branch2.diagnostics.gauge_node_ids,
        "selected_dc_sign_and_positive_r": bool(selected_sign["maximum_off_diagonal_y_s"] <= 0 and selected_sign["minimum_balanced_z_eigenvalue_ohm"] > 0),
        "old_mesh_rejected_without_false_passivity_claim": bool(np.isclose(old_sign["maximum_off_diagonal_y_s"], 847.5426754014502) and np.isclose(old_sign["minimum_balanced_z_entry_ohm"], -2.4767431334761977e-5) and old_sign["minimum_balanced_z_eigenvalue_ohm"] > 0),
        "all_mna_outputs_passive_reciprocal": all(x["min_hermitian_impedance_eigenvalue_ohm"] >= -x["passivity_tolerance_ohm"] and x["raw_reciprocity_relative_error"] < 1e-10 for x in ds.values()),
    }
    elapsed = monotonic() - started
    require(elapsed < limit, f"runtime exceeded {limit:g} seconds")
    return {
        "program": PROGRAM, "version": VERSION,
        "status": "ACCEPT_CONDITIONAL_LOCAL_POWER_FEED_ONLY" if all(checks.values()) else "STOP",
        "frequency_hz": FREQUENCY_HZ, "inputs": evidence,
        "source_contact_order": list(CONTACTS), "external_nodes": {"reference_power_exit_not_ground": 750336, "left": 229906, "right_shared": 482985},
        "native_via_owners": list(OWNERS), "native_via_impedance_ohm": {"core": [zc.real, zc.imag], "micro_each": [zm.real, zm.imag]},
        "selected_sheet": {"nodal_y_s": cmatrix(y), "balanced_r_ohm": cmatrix(r)},
        "analytic_local_z_ohm": cmatrix(analytic_z), "full_y_mna_z_ohm": cmatrix(full.impedance_ohm), "series_branch_mna_z_ohm": cmatrix(branch.impedance_ohm), "ideal_sheet_closed_form_z_ohm": cmatrix(ideal_z),
        "analytic_via_currents_a_per_a": cmatrix(analytic_i), "full_y_via_currents_a_per_a": cmatrix(fi), "series_via_currents_a_per_a": cmatrix(bi),
        "right_port_solved_split": {"via336279": [float(micro_i[1, 1].real), float(micro_i[1, 1].imag)], "via336280": [float(micro_i[2, 1].real), float(micro_i[2, 1].imag)], "sum": [float((micro_i[1, 1] + micro_i[2, 1]).real), float((micro_i[1, 1] + micro_i[2, 1]).imag)]},
        "left_port_induced_right_loop": {"via336279": [float(micro_i[1, 0].real), float(micro_i[1, 0].imag)], "via336280": [float(micro_i[2, 0].real), float(micro_i[2, 0].imag)]},
        "relative_errors": errors, "old_triangle_rejection": old_sign, "selected_boundary10_sign": selected_sign,
        "diagnostics": ds, "checks": checks,
        "ownership": {"conditional_sheet_owner": "source-asset:b0ab5dda526415e8", "native_vias_retained_once": list(OWNERS), "native_gc_changed": False, "native_fifth_artwork_gc_link_included": False},
        "scope": "CONDITIONAL_LOCAL_L21_POWER_FEED_ONLY",
        "limitations": ["DC sheet R is held constant at 1 MHz.", "Via336239 retains a conditional whole-pad electrode, not plating/current-injection certification.", "The fifth native artwork/G-C link is excluded only from this labelled local experiment.", "No full AC replacement, board current sharing, Evaluation Zii, PowerSI comparison or product promotion."],
        "resources": {"elapsed_s": elapsed, "max_runtime_s": limit},
    }


def main():
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} conditional local L21 power-feed")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--max-runtime-s", type=float, default=55.)
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - conditional local L21 power-feed")
    require(np.isfinite(args.max_runtime_s) and 1 <= args.max_runtime_s <= 55, "max runtime must be 1..55 seconds")
    require(not args.output.exists(), f"refusing existing output: {args.output}")
    result = build_result(args.max_runtime_s)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
