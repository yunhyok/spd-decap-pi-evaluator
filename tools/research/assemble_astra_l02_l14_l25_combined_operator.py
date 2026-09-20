"""Assemble the pinned L02/L14/L25 mixed operator once, without LU or a field solve."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import time


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
RUNTIME = ROOT / "outputs/research-runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import numpy as np
from scipy import sparse


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
FREQUENCY_HZ = 1.0e6
NATIVE_SIZE, L14_SIZE, L25_SIZE, COMBINED_SIZE = 756_889, 903_945, 1_483_296, 2_340_069
CURRENT_SIZE = 604_031
L02_TARGET, L14_TARGET, L25_TARGET = 349_710, 718_402, 258_027

PINS = {
    "assembly_map_result": (
        R / "astra-l02-l14-l25-combined-assembly-map-02/result.json",
        "cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81",
    ),
    "assembly_map": (
        R / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz",
        "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469",
    ),
    "assembly_map_review": (
        R / "astra-l02-l14-l25-combined-assembly-map-review-01/independent-review.json",
        "5f0436c3f574e5eafd7f9d957d5aca56d283ef562fe1105132356008da36eaad",
    ),
    "l02_helper": (
        ROOT / "tools/research/run_astra_l02_sheet_r_shadow.py",
        "8437a7eb0398fdef919704c33a6ba2e9498c810b79e04b5df533462c8ce55105",
    ),
    "l02_result": (
        R / "astra-l02-sheet-r-board-1mhz-01/result.json",
        "a491c951b7ec8c209fb194cb8dfa82d25a94230abefffbb97c94af55179b0e9b",
    ),
    "l25_helper": (
        ROOT / "tools/research/run_astra_l25_rt0_board_shadow.py",
        "edb0d82ff2b9af9b44259541cca0d2abdef7e52c4c1ebd29ba2c990e7d65ef98",
    ),
    "l25_gc_receipt": (
        R / "astra-l25-refined-gc-areas-02/result.json",
        "8c0cd86b78ac647eadbf15962eadb5ad4d7a2ab385e41488982771a2432a69ef",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def file_receipt(path: Path) -> dict:
    return {"path": str(path), "sha256": sha(path), "size_bytes": path.stat().st_size}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    temporary.replace(path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    temporary.replace(path)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def max_abs(values) -> float:
    values = np.asarray(values)
    return float(np.max(np.abs(values), initial=0.0))


def extend_square(matrix: sparse.spmatrix, size: int) -> sparse.csc_matrix:
    result = matrix.tocsc(copy=True)
    result.resize((size, size))
    return result


def remap_square(matrix: sparse.spmatrix, mapping: np.ndarray, size: int) -> sparse.csc_matrix:
    require(matrix.shape == (len(mapping), len(mapping)), "remapped matrix/source mapping shape differs")
    coo = matrix.tocoo(copy=False)
    result = sparse.coo_matrix((coo.data, (mapping[coo.row], mapping[coo.col])),
                               shape=(size, size), dtype=coo.dtype).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


def branch_laplacian(first: np.ndarray, second: np.ndarray, admittance: np.ndarray,
                     size: int) -> sparse.csc_matrix:
    count = len(first)
    result = sparse.coo_matrix(
        (np.r_[admittance, admittance, -admittance, -admittance],
         (np.r_[first, second, first, second], np.r_[first, second, second, first])),
        shape=(size, size), dtype=np.complex128,
    ).tocsc()
    require(count == len(second) == len(admittance), "finite branch vector lengths differ")
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


def branch_action(first: np.ndarray, second: np.ndarray, admittance: np.ndarray,
                  voltage: np.ndarray) -> np.ndarray:
    current = admittance * (voltage[first] - voltage[second])
    result = np.zeros(len(voltage), dtype=np.complex128)
    np.add.at(result, first, current)
    np.add.at(result, second, -current)
    return result


class EventBudget:
    def __init__(self, path: Path):
        self.path, self.started, self.runtime_s = path, time.perf_counter(), 180.0

    def elapsed(self):
        return time.perf_counter() - self.started

    def emit(self, event, **payload):
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"event": event, "elapsed_s": self.elapsed(), **payload},
                                    sort_keys=True, allow_nan=False) + "\n")


def capture_l02(output: Path, document: dict):
    module = load_module("astra_combined_l02", PINS["l02_helper"][0])
    captured = {}
    original_partition = module.retained_gc_without_l02
    original_solve = module.solve_point_checkpointed

    def partition_wrapper(*args, **kwargs):
        result = original_partition(*args, **kwargs)
        captured["original_partial_0"] = result[1].copy()
        return result

    def solve_capture(categories, sheet, gauge, positive, negative, sheet_active,
                      destination, budget, baseline, actions, field_arrays=None):
        captured.update(categories=dict(categories), sheet=sheet, gauge=int(gauge),
                        positive=int(positive), negative=int(negative),
                        sheet_active=np.asarray(sheet_active, dtype=np.int64).copy())
        return {"status": "CAPTURED_ASSEMBLY_WITHOUT_SOLVE"}

    module.retained_gc_without_l02 = partition_wrapper
    module.solve_point_checkpointed = solve_capture
    args = SimpleNamespace(output=output, assemble_only=False)
    for family in ("mesh", "binding", "gc"):
        for kind in ("result", "npz", "review"):
            entry = document["inputs"][f"{family}_{kind}"]
            setattr(args, f"{family}_{kind}", Path(entry["path"]))
            setattr(args, f"{family}_{kind}_sha256", entry["sha256"])
    try:
        returned = module.run(args, EventBudget(output.parent / "l02-progress.jsonl"))
    finally:
        module.retained_gc_without_l02 = original_partition
        module.solve_point_checkpointed = original_solve
    require(returned["point"]["status"] == "CAPTURED_ASSEMBLY_WITHOUT_SOLVE"
            and set(captured) == {"categories", "sheet", "gauge", "positive", "negative",
                                  "sheet_active", "original_partial_0"},
            "L02 assembly callback capture differs")
    return captured


def extract_l25_categories(returned: dict) -> dict:
    actions = returned["actions"]
    closure = dict(zip(actions.__code__.co_freevars,
                       (cell.cell_contents for cell in actions.__closure__), strict=True))
    require("categories" in closure and isinstance(closure["categories"], dict),
            "L25 action closure does not retain source categories")
    categories = dict(closure["categories"])
    require(set(categories) == {"retained_gc", "finite_via", "termination", "l14_sheet_dc",
                                "l14_distributed_gc", "l25_distributed_gc"},
            "L25 returned category set differs")
    return categories


def aggregate_to_native(action: np.ndarray, mapping: np.ndarray) -> np.ndarray:
    result = np.zeros(NATIVE_SIZE, dtype=np.complex128)
    np.add.at(result, mapping, action)
    return result


def save_csc(archive: dict[str, np.ndarray], prefix: str, matrix: sparse.csc_matrix) -> None:
    archive[prefix + "_data"] = matrix.data
    archive[prefix + "_indices"] = matrix.indices
    archive[prefix + "_indptr"] = matrix.indptr
    archive[prefix + "_shape"] = np.asarray(matrix.shape, dtype=np.int64)


def run(output: Path) -> dict:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    inputs = {}
    documents = {}
    for name, (path, expected) in PINS.items():
        actual = sha(path)
        require(actual == expected, f"{name} SHA-256 differs")
        inputs[name] = file_receipt(path)
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    require(documents["assembly_map_result"]["status"]
            == "PASS_L02_L14_L25_COMBINED_ASSEMBLY_MAP_NO_LU"
            and documents["assembly_map_result"]["output"]["sha256"] == PINS["assembly_map"][1]
            and documents["assembly_map_review"]["status"]
            == "ACCEPT_L02_L14_L25_COMBINED_ASSEMBLY_MAP_INDEPENDENT_REVIEW",
            "combined assembly-map acceptance chain differs")

    l02_output = output / "l02-captured-assembly"
    l02_output.mkdir()
    l02 = capture_l02(l02_output, documents["l02_result"])
    require(l02["sheet"].shape == (1_613_662, 1_613_662)
            and l02["original_partial_0"].shape == (NATIVE_SIZE, NATIVE_SIZE),
            "captured L02 matrix dimensions differ")

    native_index = np.arange(NATIVE_SIZE, dtype=np.int64)
    native_voltage = ((native_index % 1009)/1009.0
                      + 1j*((19*native_index) % 1013)/1013.0).astype(np.complex128)
    l02_collapse = np.arange(1_613_662, dtype=np.int64)
    l02_collapse[NATIVE_SIZE:] = L02_TARGET
    l02_lifted = native_voltage[l02_collapse]
    l02_action = l02["sheet"] @ l02_lifted
    for matrix in l02["categories"].values():
        l02_action += matrix @ l02_lifted
    native_reference_action = aggregate_to_native(l02_action, l02_collapse)
    l02_distributed_gc_source = l02["categories"]["distributed_gc"]
    l02_sheet_source = l02["sheet"]
    original_partial_0 = l02["original_partial_0"]
    l02_source_category_nnz = {name: int(matrix.nnz) for name, matrix in l02["categories"].items()}
    l02_source_category_nnz["sheet_dc"] = int(l02_sheet_source.nnz)
    del l02_action, l02_lifted, l02["categories"]
    gc.collect()

    l25_module = load_module("astra_combined_l25", PINS["l25_helper"][0])
    l25_output = output / "l25-return-assembly"
    l25_returned = l25_module.run(SimpleNamespace(
        gc_receipt=PINS["l25_gc_receipt"][0],
        gc_receipt_sha256=PINS["l25_gc_receipt"][1],
        output=l25_output,
        return_assembly=True,
    ))
    require(l25_returned["y"].shape == (L25_SIZE, L25_SIZE)
            and l25_returned["r"].shape == (CURRENT_SIZE, CURRENT_SIZE)
            and l25_returned["b"].shape == (L25_SIZE, CURRENT_SIZE),
            "returned L25 mixed assembly dimensions differ")
    l25_categories = extract_l25_categories(l25_returned)
    probe_index = np.arange(L25_SIZE, dtype=np.int64)
    l25_probe = ((probe_index % 521)/521.0 + 1j*((7*probe_index) % 523)/523.0).astype(np.complex128)
    returned_action = l25_returned["y"] @ l25_probe
    category_action = np.zeros(L25_SIZE, dtype=np.complex128)
    for matrix in l25_categories.values():
        category_action += matrix @ l25_probe
    callback_action = sum(l25_returned["actions"](l25_probe).values(),
                          np.zeros(L25_SIZE, dtype=np.complex128))
    l25_action_scale = max(max_abs(returned_action), np.finfo(float).tiny)
    l25_closure_replay = max_abs(returned_action-category_action)/l25_action_scale
    l25_callback_replay = max_abs(returned_action-callback_action)/l25_action_scale
    l25_category_callback_replay = max_abs(category_action-callback_action)/l25_action_scale
    # The returned CSC matrix, its separately accumulated category matrices and
    # branch-wise callback use different sparse summation orders.  Gate all
    # three representations at the same fixed arithmetic tolerance used by the
    # downstream one-owned action check.
    require(max(l25_closure_replay, l25_callback_replay,
                l25_category_callback_replay) <= 2e-12,
            "L25 returned matrix/category/callback closure differs")
    resistance = l25_returned["r"].tocsc(copy=True)
    incidence = l25_returned["b"].tocsc(copy=True)
    port = (int(l25_returned["positive"]), int(l25_returned["negative"]), int(l25_returned["gauge"]))
    del returned_action, category_action, callback_action, l25_probe, probe_index, l25_returned
    l25_categories.pop("finite_via")
    gc.collect()

    with np.load(PINS["assembly_map"][0], allow_pickle=False) as mapping:
        final_first = np.asarray(mapping["final_finite_first_active_index"], dtype=np.int64)
        final_second = np.asarray(mapping["final_finite_second_active_index"], dtype=np.int64)
        final_y = np.asarray(mapping["final_finite_admittance_s"], dtype=np.complex128)
        require(final_first.shape == final_second.shape == final_y.shape == (1_692_409,),
                "saved final finite branch vectors differ")

    l02_mapping = np.r_[np.arange(NATIVE_SIZE, dtype=np.int64),
                         np.arange(L25_SIZE, COMBINED_SIZE, dtype=np.int64)]
    require(len(l02_mapping) == l02_sheet_source.shape[0], "L02-to-combined coordinate map differs")
    l02_sheet = remap_square(l02_sheet_source, l02_mapping, COMBINED_SIZE)
    l02_distributed_gc = remap_square(l02_distributed_gc_source, l02_mapping, COMBINED_SIZE)
    del l02_sheet_source, l02_distributed_gc_source, l02_mapping
    gc.collect()

    final_categories = {}
    for name, matrix in l25_categories.items():
        final_categories[name] = extend_square(matrix, COMBINED_SIZE)
    partial_0 = extend_square(original_partial_0, COMBINED_SIZE)
    retained = (final_categories["retained_gc"] - partial_0).tocsc()
    retained.sum_duplicates()
    retained.eliminate_zeros()
    final_categories["retained_gc"] = retained
    final_categories["l02_distributed_gc"] = l02_distributed_gc
    final_categories["l02_sheet_dc"] = l02_sheet
    del partial_0, original_partial_0, l25_categories
    gc.collect()

    retained_scale = max(max_abs(retained.data), 1.0)
    retained_target = max(max_abs(retained.getrow(L02_TARGET).data),
                          max_abs(retained.getcol(L02_TARGET).data))
    require(retained_target <= retained_scale*2e-12,
            "a native retained G/C entry remains on the replaced L02 target")
    require(set(final_categories) == {"retained_gc", "termination", "l14_sheet_dc",
                                      "l14_distributed_gc", "l25_distributed_gc",
                                      "l02_distributed_gc", "l02_sheet_dc"},
            "combined nonfinite category set differs")
    for name, matrix in final_categories.items():
        require(matrix.shape == (COMBINED_SIZE, COMBINED_SIZE)
                and np.all(np.isfinite(matrix.data)), f"{name} combined block is invalid")

    incidence.resize((COMBINED_SIZE, CURRENT_SIZE))
    require(resistance.shape == (CURRENT_SIZE, CURRENT_SIZE)
            and incidence.shape == (COMBINED_SIZE, CURRENT_SIZE)
            and np.all(np.isfinite(resistance.data)) and np.all(np.isfinite(incidence.data)),
            "combined R/B blocks are invalid")
    b_column_sum = max_abs(np.asarray(incidence.sum(axis=0)).ravel())
    b_values = np.unique(incidence.data)
    require(np.array_equal(b_values, [-1.0, 1.0]) and b_column_sum == 0.0,
            "L25 incidence orientation/column sum differs")
    r_scale = max(max_abs(resistance.data), 1.0)
    r_symmetry = max_abs((resistance-resistance.T).data)/r_scale
    require(r_symmetry <= 2e-12 and np.all(resistance.diagonal() > 0),
            "L25 resistance symmetry/positive diagonal gate failed")

    finite = branch_laplacian(final_first, final_second, final_y, COMBINED_SIZE)
    combined_y = finite.copy()
    for matrix in final_categories.values():
        combined_y += matrix
    combined_y = combined_y.tocsc()
    combined_y.sum_duplicates()
    combined_y.eliminate_zeros()
    require(combined_y.shape == (COMBINED_SIZE, COMBINED_SIZE)
            and np.all(np.isfinite(combined_y.data)), "combined Y is invalid")

    combined_collapse = np.arange(COMBINED_SIZE, dtype=np.int64)
    combined_collapse[NATIVE_SIZE:L14_SIZE] = L14_TARGET
    combined_collapse[L14_SIZE:L25_SIZE] = L25_TARGET
    combined_collapse[L25_SIZE:] = L02_TARGET
    collapsed_voltage = native_voltage[combined_collapse]
    combined_source_action = branch_action(final_first, final_second, final_y, collapsed_voltage)
    for matrix in final_categories.values():
        combined_source_action += matrix @ collapsed_voltage
    collapsed_combined_action = aggregate_to_native(combined_source_action, combined_collapse)
    recollapse_relative = max_abs(collapsed_combined_action-native_reference_action) / max(
        max_abs(native_reference_action), np.finfo(float).tiny)
    require(recollapse_relative <= 5e-12,
            "combined source action does not recollapse to the saved corrected L02 native assembly")
    del collapsed_voltage, combined_source_action, collapsed_combined_action, native_reference_action
    gc.collect()

    combined_index = np.arange(COMBINED_SIZE, dtype=np.int64)
    voltage = ((combined_index % 1031)/1031.0
               + 1j*((23*combined_index) % 1033)/1033.0).astype(np.complex128)
    physical_action = branch_action(final_first, final_second, final_y, voltage)
    for matrix in final_categories.values():
        physical_action += matrix @ voltage
    csc_action = combined_y @ voltage
    source_action_relative = max_abs(physical_action-csc_action)/max(max_abs(csc_action), np.finfo(float).tiny)
    require(source_action_relative <= 2e-12,
            "one-owned source branch/category action does not reproduce combined Y")
    driven_power = np.vdot(voltage, physical_action)
    require(np.isfinite(driven_power) and driven_power.real >= -1e-8,
            "combined deterministic driven passivity diagnostic failed")
    del combined_index, voltage, physical_action, csc_action
    gc.collect()

    y_scale = max(max_abs(combined_y.data), 1.0)
    y_symmetry = max_abs((combined_y-combined_y.T).data)/y_scale
    y_row_sum = max_abs(np.asarray(combined_y.sum(axis=1)).ravel())/y_scale
    require(y_symmetry <= 2e-12 and y_row_sum <= 3e-12,
            "combined Y symmetry/row-sum gate failed")

    arrays = {}
    save_csc(arrays, "y", combined_y)
    save_csc(arrays, "r", resistance)
    save_csc(arrays, "b", incidence)
    arrays["positive_negative_gauge_active_indices"] = np.asarray(port, dtype=np.int64)
    arrays["combined_collapse_to_native_active_index"] = combined_collapse
    arrays["assembly_map_sha256_utf8"] = np.frombuffer(PINS["assembly_map"][1].encode("ascii"), dtype=np.uint8)
    artifact = output / "combined-operator.npz"
    atomic_npz(artifact, **arrays)

    category_nnz = {name: int(matrix.nnz) for name, matrix in final_categories.items()}
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_L02_L14_L25_COMBINED_OPERATOR_ASSEMBLY_NO_LU",
        "frequency_hz": FREQUENCY_HZ,
        "inputs": inputs,
        "driver": file_receipt(driver),
        "output": file_receipt(artifact),
        "dimensions": {
            "potential_count": COMBINED_SIZE,
            "current_count": CURRENT_SIZE,
            "mixed_unknown_count": COMBINED_SIZE + CURRENT_SIZE,
            "gauge_eliminated_unknown_count": COMBINED_SIZE + CURRENT_SIZE - 1,
            "y_nnz": int(combined_y.nnz),
            "r_nnz": int(resistance.nnz),
            "b_nnz": int(incidence.nnz),
            "finite_branch_count": int(len(final_y)),
        },
        "category_nnz": category_nnz,
        "captured_l02_source_category_nnz": l02_source_category_nnz,
        "checks": {
            "l25_returned_category_action_relative_error": l25_closure_replay,
            "l25_returned_callback_action_relative_error": l25_callback_replay,
            "l25_category_vs_callback_action_relative_error": l25_category_callback_replay,
            "combined_to_corrected_native_source_action_relative_error": recollapse_relative,
            "combined_csc_vs_one_owned_source_action_relative_error": source_action_relative,
            "combined_y_symmetry_relative_max": y_symmetry,
            "combined_y_row_sum_relative_max": y_row_sum,
            "l25_r_symmetry_relative_max": r_symmetry,
            "l25_b_column_sum_max": b_column_sum,
            "retained_gc_l02_target_relative_max": retained_target/retained_scale,
            "deterministic_driven_power": [float(driven_power.real), float(driven_power.imag)],
            "single_final_finite_branch_list_used": True,
            "no_lu_or_field_solve": True,
        },
        "port": {"positive_active_index": port[0], "negative_active_index": port[1],
                 "gauge_active_index": port[2]},
        "elapsed_s": time.perf_counter()-started,
        "scope": "One executable 1MHz sparse Y/R/B assembly combining the pinned conditional L02, L14 and L25 source operators. The native finite network is constructed once from the accepted final branch list; no large delta subtraction, LU, voltage/current field or PowerSI comparison is performed.",
        "limitations": [
            "The existing conditional sheet/contact discretizations and their owner projections are reused unchanged.",
            "The saved operator is an assembly prerequisite, not an accepted board response; a guarded solve and source-current/power checks remain pending.",
            "L02 conservative current/charge and magnetic coupling beyond this nodal sheet model remain separate work.",
        ],
    }
    atomic_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=R / "astra-l02-l14-l25-combined-operator-02")
    args = parser.parse_args()
    try:
        result = run(args.output.resolve())
        print(json.dumps({"status": result["status"], "dimensions": result["dimensions"],
                          "checks": result["checks"], "elapsed_s": result["elapsed_s"],
                          "output": result["output"]}, sort_keys=True, allow_nan=False))
    except Exception as error:
        if args.output.exists() and not (args.output / "failure.json").exists():
            atomic_json(args.output / "failure.json", {
                "program": PROGRAM, "version": VERSION,
                "status": "STOP_L02_L14_L25_COMBINED_OPERATOR_ASSEMBLY",
                "error": f"{type(error).__name__}: {error}",
            })
        raise


if __name__ == "__main__":
    main()
