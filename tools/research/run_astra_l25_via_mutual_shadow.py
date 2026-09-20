"""Conditional 350-via mutual-only replacement on the frozen RT0 board at 1 MHz."""
import argparse
import gc
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
import numpy as np
from scipy import sparse
from scipy.linalg import lu_factor, lu_solve
import run_astra_l25_rt0_board_shadow as board
from run_astra_l25_magnetic_self_shadow import BOARD_SHA, GC, GC_SHA, PRIOR, PRIOR_SHA

ROOT, R = board.ROOT, board.R
VIA = R / "astra-l25-native-via-magnetic-compatibility-01"
VIA_SHA = "b41c39863399dc74b65ddbf2413c2fd670120ebe325f37e5d5fa3045ed0a2d09"
VIA_MATRIX_SHA = "a4b0ffc6d98e5cfac6124d7dcd5780831a25f76f7f0a254164814ee9ec7e081f"
SOURCE = R / "astra-l25-source-sheet-01/receipt.json"
SOURCE_SHA = "a45f6601bae33dc72f9c56190cd85a42037c4046c460bedbdd7cecbc9e85851b"
GUARD_SHA = "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"


def coupled_block(first, second, resistance, inductance, omega, size):
    """Condense only the selected small coupled branch block, retaining its currents."""
    m = len(first)
    if (second.shape != first.shape or resistance.shape != first.shape
            or inductance.shape != (m, m) or np.any(first == second)
            or np.any(resistance <= 0) or not np.isfinite(resistance).all()
            or not np.isfinite(inductance).all() or not np.array_equal(inductance, inductance.T)
            or np.min(np.linalg.eigvalsh(inductance)) <= 0):
        raise ValueError("Positive R and symmetric positive L with distinct branch endpoints required")
    nodes, inverse = np.unique(np.r_[first, second], return_inverse=True)
    if nodes[0] < 0 or nodes[-1] >= size:
        raise ValueError("Branch endpoint outside the assembled board")
    incidence = np.zeros((len(nodes), m))
    incidence[inverse[:m], np.arange(m)] = 1
    incidence[inverse[m:], np.arange(m)] = -1
    z = np.diag(resistance)+1j*omega*inductance
    factor = lu_factor(z)
    admittance = incidence @ lu_solve(factor, incidence.T)
    if not np.isfinite(admittance).all() or np.linalg.norm(admittance-admittance.T) > np.linalg.norm(admittance)*1e-12:
        raise ValueError("Coupled branch admittance reciprocity failed")
    stamp = sparse.coo_matrix((admittance.ravel(), (np.repeat(nodes, len(nodes)), np.tile(nodes, len(nodes)))), shape=(size, size)).tocsc()
    def action(v):
        current = lu_solve(factor, v[first]-v[second])
        injection = np.zeros(size, complex)
        np.add.at(injection, first, current)
        np.add.at(injection, second, -current)
        return injection, current
    return stamp, action


def run(output):
    started = perf_counter()
    board.pin(Path(board.__file__), BOARD_SHA)
    board.pin(Path(__file__).with_name("run_astra_l25_magnetic_self_shadow.py"), "d6a1dce4b390d59c52e29d81b18aae02d3fa5f3b9ac95a8e403422cba182a489")
    for path, digest in ((PRIOR, PRIOR_SHA), (SOURCE, SOURCE_SHA), (VIA/"result.json", VIA_SHA)):
        board.pin(path, digest)
    prior, source, via = [json.loads(path.read_bytes()) for path in (PRIOR, SOURCE, VIA/"result.json")]
    if via["status"] != "COMPLETED_CONDITIONAL_AXIAL_VIA_NATIVE_DIAGONAL_COMPATIBILITY" or via["matrix_sha256"] != VIA_MATRIX_SHA:
        raise ValueError("Frozen source350 compatibility result required")
    matrix = board.load_npz(VIA/"axial-native-l.npz", VIA_MATRIX_SHA)
    binding = board.load_npz(*board.PINS["binding"])
    assembly = board.run(SimpleNamespace(output=output/"source-assembly", gc_receipt=GC, gc_receipt_sha256=GC_SHA, return_assembly=True))
    for key in ("inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt"):
        if assembly[key] != prior[key]:
            raise ValueError(f"Frozen R-only source differs: {key}")
    index = binding["native_active_finite_index"]
    if len(index) != 350 or len(np.unique(index)) != 350 or not np.array_equal(index, matrix["native_active_finite_index"]) or not np.all(binding["native_parallel_count"] == 1):
        raise ValueError("Exact 350 individual native branch ordering required")
    rows_by_index = {row["active_finite_index"]: row for row in source["via_rows"]}
    rows = [rows_by_index[int(i)] for i in index]
    if via["via_ids"] != [row["via_id"] for row in rows]:
        raise ValueError("Axial matrix source owner ordering differs")
    height, target_centers = 0., []
    for _, name, _, thickness_um in via["stackup_rows"]:
        if name == "Signal$L25(MAIN_POWER4)":
            target_centers.append((height+thickness_um/2)*1e-6)
        height += thickness_um
    if len(target_centers) != 1:
        raise ValueError("Exactly one L25 metal center required in the pinned source stackup")
    z25, = target_centers
    spans = matrix["spans_m"]
    lower = np.isclose(spans[:, 1], z25, rtol=0, atol=1e-15)
    upper = np.isclose(spans[:, 0], z25, rtol=0, atol=1e-15)
    if np.count_nonzero(lower) != 175 or np.count_nonzero(upper) != 175 or not np.all(lower ^ upper):
        raise ValueError("175 source spans on each side of L25 metal center required")
    target_first = binding["target_is_native_first_endpoint"]
    direction = np.where(target_first, 1., -1.) * np.where(upper, 1., -1.)
    ordinal = binding["l25_local_electrode_potential_index"]-539641
    active = assembly["field_extra"]["l25_potential_active_indices"]
    target = active[assembly["assembly"]["l25_free_cell_count"]+ordinal]
    other = binding["native_other_active_index"]
    first, second = np.where(target_first, target, other), np.where(target_first, other, target)
    resistance = binding["native_resistance_ohm_per_via"]
    native_l = binding["native_inductance_h_per_via"]
    if not np.array_equal(np.diag(matrix["matrix_h"]), native_l):
        raise ValueError("Native inductance diagonal must remain unchanged")
    inductance = direction[:, None]*matrix["matrix_h"]*direction[None, :]
    omega, size = 2*np.pi*1e6, assembly["y"].shape[0]
    native_y = 1/(resistance+1j*omega*native_l)
    frequency = board.helper("frequency")
    trace = board.helper("l14").trace
    original = trace.laplacian(first, second, native_y, size)
    zero, zero_action = coupled_block(first, second, resistance, np.diag(native_l), omega, size)
    zero_difference = trace.sparse_difference(zero, original)
    if zero_difference["relative_maximum_difference"] > 2e-12:
        raise ValueError("Zero-mutual block fails exact original350 recollapse")
    replacement, via_action = coupled_block(first, second, resistance, inductance, omega, size)
    base_actions = assembly["actions"]
    def actions(v):
        result = base_actions(v)
        result["finite_via"] -= frequency.branch_action(first, second, native_y, v)
        result["l25_coupled_via"] = via_action(v)[0]
        return result
    base = board.helper("budget")
    field = board.load_npz(PRIOR.parent/prior["field"]["path"], prior["field"]["sha256"])
    for key, value in assembly["field_extra"].items():
        if not np.array_equal(field[key], value):
            raise ValueError("Saved R-only field active mapping differs")
    v0, q0 = field["active_voltage_v"], field["l25_branch_current_a"]
    old_current = native_y*(v0[first]-v0[second])
    zero_injection, zero_current = zero_action(v0)
    current_error = float(np.max(abs(zero_current-old_current)))
    old_injection = frequency.branch_action(first, second, native_y, v0)
    injection_error = float(np.max(abs(zero_injection-old_injection)))
    zero_power = np.vdot(zero_current, (resistance+1j*omega*native_l)*zero_current)
    original_power = np.conj(np.vdot(v0, old_injection))
    if (current_error > np.max(abs(old_current))*2e-12
            or injection_error > np.max(abs(old_injection))*2e-12
            or abs(zero_power-original_power) > max(abs(original_power)*1e-10, 1e-14)):
        raise ValueError("Zero-mutual branch current or category power does not recollapse")
    rhs = np.zeros(size, complex)
    rhs[assembly["positive"]], rhs[assembly["negative"]] = 1, -1
    kcl = float(np.max(abs(sum(base_actions(v0).values())+assembly["b"]@q0-rhs)))
    constitutive = float(np.max(abs(assembly["r"]@q0-assembly["b"].T@v0)))
    if max(current_error, injection_error, kcl, constitutive) >= 1e-7:
        raise ValueError("Saved R-only field or zero-mutual current control fails")
    z0 = v0[assembly["positive"]]-v0[assembly["negative"]]
    if abs(z0-complex(*prior["point"]["zdd_ohm"])) > 1e-15:
        raise ValueError("Saved R-only Device voltage differs")
    control = {"zero_mutual_matrix": zero_difference, "current_max_abs_a": current_error,
               "injection_max_abs_a": injection_error, "saved_kcl_max_a": kcl,
               "saved_constitutive_max_v": constitutive, "zdd_ohm": board.pair(z0),
               "zero_mutual_power_ohm": board.pair(zero_power), "original_selected_power_ohm": board.pair(original_power), "fresh_board_lu": False}
    base._write_json_exclusive(output/"alpha0-control.json", control)
    del field, v0, q0, zero, zero_action, zero_injection, original, old_current, old_injection
    gc.collect()
    y = assembly["y"]-trace.laplacian(first, second, native_y, size)+replacement
    budget = base._Budget(output/"progress.jsonl")
    watcher = budget.start_watchdog()
    try:
        checkpoint = lambda v, q: board.save_unvalidated(output, v, q, {**assembly["field_extra"], "selected_via_current_a": via_action(v)[1], "selected_via_compiled_to_axial_sign": direction, "selected_via_active_finite_index": index}, base)
        point, voltage, current = board.solve_mixed(y, assembly["r"], assembly["b"], assembly["gauge"], assembly["positive"], assembly["negative"], actions, budget, checkpoint)
        via_current = via_action(voltage)[1]
        via_power = np.vdot(via_current, resistance*via_current)+1j*omega*np.vdot(via_current, inductance@via_current)
        port_power = complex(*point["power_contributions_ohm"]["l25_coupled_via"])
        if abs(via_power-port_power) > max(abs(via_power)*1e-10, 1e-14):
            raise ValueError("Coupled via current energy differs from category port power")
        report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": "COMPLETED_CONDITIONAL_L25_350_VIA_MUTUAL_1MHZ",
                  "script_sha256": board.sha(Path(__file__)), "board_driver_sha256": BOARD_SHA,
                  "via_compatibility_sha256": VIA_SHA, "via_matrix_sha256": VIA_MATRIX_SHA,
                  "source_receipt_sha256": SOURCE_SHA, "baseline_sha256": PRIOR_SHA,
                  "rail_id": assembly["rail_id"], "frequency_hz": 1e6,
                  "source": {key: assembly[key] for key in ("inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt", "assembly", "driver")},
                  "alpha0_control": control, "point": point, "coupled_via_power_ohm": board.pair(via_power),
                  "compiled_to_axial_direction_counts": {str(sign): int(np.count_nonzero(direction == sign)) for sign in (-1, 1)},
                  "elapsed_s": perf_counter()-started,
                  "scope": "Only the exact rewired350 native scalar RL stamps are replaced once by their coupled block. Native R/L diagonals remain unchanged; source axial mutual is added with compiled orientation congruence. L14/L25 sheets remain the frozen DC model, all other native terms remain. This is selected-via mutual sensitivity, not complete return, other-via/sheet mutual, pad/bend/volume/ESL ownership, broadband or PowerSI accuracy acceptance. No new self term, nearest-ground assignment or reference fitting."}
        json.dumps(report, allow_nan=False)
        os.link(output/"unvalidated-field.npz", output/"field.npz")
        report["field"] = base._file_receipt(output/"field.npz")
        base._write_json_exclusive(output/"result.json", report)
        print(json.dumps({"status": report["status"], "zdd_ohm": point["zdd_ohm"], "elapsed_s": report["elapsed_s"]}), flush=True)
    finally:
        budget.stop.set(); watcher.join(timeout=5)


def self_check():
    first, second = np.array([0, 1]), np.array([1, 2])
    resistance, inductance = np.array([2., 3.]), np.array([[.7, -.2], [-.2, .6]])
    v = np.array([.7+.2j, -.1+.4j, .2-.8j])
    stamp, action = coupled_block(first, second, resistance, inductance, 2., 3)
    injection, current = action(v)
    assert np.max(abs(stamp@v-injection)) < 1e-14
    assert abs(np.conj(np.vdot(v, injection))-(np.vdot(current, resistance*current)+2j*np.vdot(current, inductance@current))) < 1e-14
    direction = np.array([-1., 1.])
    reversed_stamp, reversed_action = coupled_block(np.array([1, 1]), np.array([0, 2]), resistance, direction[:, None]*inductance*direction[None, :], 2., 3)
    assert np.max(abs((stamp-reversed_stamp).data), initial=0.) < 1e-14
    assert np.max(abs(reversed_action(v)[1]-direction*current)) < 1e-14
    print("SPD Decap PI Evaluator v0.23.1: coupled-via orientation/energy SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--native-worker", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.output is None:
        parser.error("--output required")
    elif args.native_worker:
        board.pin(args.output/"driver-at-run.py", board.sha(Path(__file__)))
        run(args.output)
    else:
        from probe_astra_fmm3d_runtime import guarded_source_worker
        guard_path = Path(__file__).with_name("probe_astra_fmm3d_runtime.py")
        board.pin(guard_path, GUARD_SHA)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output/"driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        (args.output/"guard-helper-pinned.py").write_bytes(guard_path.read_bytes())
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(args.output.resolve())]
        raise SystemExit(guarded_source_worker(args.output, worker_command=command, max_runtime_s=240.))
