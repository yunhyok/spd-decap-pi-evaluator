"""Independently review saved finite-L02 sheet assembly and field artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zipfile import ZipFile

import numpy as np
from scipy import sparse

import reconstruct_astra_native_loaded_field as recon
import run_astra_l14_sheet_r_shadow as l14
from run_astra_two_sheet_frequency_shadow import branch_action


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs/research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
PRODUCER_SHA256 = "8437a7eb0398fdef919704c33a6ba2e9498c810b79e04b5df533462c8ce55105"
ASSEMBLY_ACCEPT = "ACCEPT_CONDITIONAL_L02_SHEET_R_ASSEMBLY_INDEPENDENT_REVIEW"
FIELD_ACCEPT = "ACCEPT_CONDITIONAL_L02_SHEET_R_FIELD_INDEPENDENT_REVIEW"
NATIVE_SIZE = 756_889
TARGET = 349_710
FREQUENCY_HZ = 1.0e6
SHEET_CONDUCTANCE_S = 59_590_000.0 * 20.0e-6

IDEAL_RESULT = RESEARCH / "astra-l02-ideal-junction-board-1mhz-01/result.json"
IDEAL_REVIEW = RESEARCH / "astra-l02-ideal-junction-board-1mhz-01/independent-review.json"
IDEAL_FIELD = RESEARCH / "astra-l02-ideal-junction-board-1mhz-01/epsilon-1-field.npz"
SOURCE_PINS = {
    "raw": l14.PINS["raw"],
    "derived": l14.PINS["derived"],
    "baseline_reconstruction": l14.PINS["baseline_reconstruction"],
    "ideal_result": (IDEAL_RESULT, "24b607363552baa6518f79f972335da378e833d852e8290c7659e07fba7b541c"),
    "ideal_review": (IDEAL_REVIEW, "658b13744c71efcaefbb3260b11d171570c6f49d181bc491ca29086dbb987b81"),
    "ideal_field": (IDEAL_FIELD, "ab584c9a56403a1cfb42e70a2aa6d86ff03c36b38aabb191724e1a783c1d0854"),
    "binding_producer": (ROOT / "tools/research/prepare_astra_l02_circuit_contact_binding.py", "16d52f45cc5e4659d1e5bc6a14be6211ea71a103a81cc772a9c02c62c0ec89d9"),
    "l14_solver": (Path(l14.__file__), "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803"),
    "native_reconstructor": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "trace_helper": (Path(l14.trace.__file__), "52b5e319e071827bb1854f1ea79f066f2fd9e856f410a23534832ad22edb40c4"),
    "branch_current_helper": (ROOT / "tools/research/run_astra_two_sheet_frequency_shadow.py", "7e42bbb02679aa974451776e7f9577a68ba832f003884958afc744091f45da5c"),
    "gc_projector": (ROOT / "tools/research/project_astra_l02_gc_mass.py", "b50915a6ec98b9d09665eac678a101444d055c610d6f9601f1439b0fe06e76eb"),
    "persistence_helper": (ROOT / "tools/research/project_astra_l14_gc_mass.py", "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
TRIPLETS = {
    "mesh": ("COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT",
             "ACCEPT_SAVED_L02_PAD_DOMAIN_MESH_AND_STIFFNESS_INDEPENDENT_REVIEW",
             "snapshot", "snapshot"),
    "binding": ("COMPLETED_SOURCE_L02_CIRCUIT_CONTACT_BINDING",
                "ACCEPT_L02_CIRCUIT_CONTACT_BINDING_INDEPENDENT_REVIEW",
                "output", "binding_npz"),
    "gc": ("COMPLETED_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK",
           "ACCEPT_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK_INDEPENDENT_REVIEW",
           "output", "capacitance_npz"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked(path: Path, expected: str, label: str) -> dict[str, Any]:
    path = path.resolve()
    require(len(expected or "") == 64 and set(expected) <= set("0123456789abcdef"),
            f"{label} SHA-256 pin is invalid")
    require(path.is_file() and sha(path) == expected, f"{label} SHA-256 differs")
    return {"path": str(path), "sha256": expected, "size_bytes": path.stat().st_size}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path.name} is not a JSON object")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    require(not path.exists(), f"review output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def maximum(values: np.ndarray) -> float:
    return float(np.max(np.abs(values))) if np.size(values) else 0.0


def decoded_json(archive: Any, key: str) -> Any:
    value = np.asarray(archive[key])
    require(value.ndim == 1 and value.dtype == np.uint8, f"{key} is not packed uint8 JSON")
    return json.loads(value.tobytes().decode("utf-8"))


def decoded_text(archive: Any, key: str) -> str:
    value = np.asarray(archive[key])
    require(value.ndim == 1 and value.dtype == np.uint8, f"{key} is not packed uint8 text")
    return value.tobytes().decode("utf-8")


def read_csc(archive: Any, prefix: str, data_key: str | None = None) -> sparse.csc_matrix:
    shape = tuple(map(int, np.asarray(archive[prefix + "_shape"])))
    result = sparse.csc_matrix((archive[data_key or prefix + "_data"],
                                archive[prefix + "_indices"], archive[prefix + "_indptr"]),
                               shape=shape)
    result.check_format(full_check=True)
    require(np.all(np.isfinite(result.data)), f"{prefix} contains nonfinite values")
    return result


def same_binding(inputs: dict[str, Any], key: str, record: dict[str, Any]) -> None:
    saved = inputs.get(key, {})
    require(saved.get("sha256") == record["sha256"], f"result {key} SHA binding differs")
    require(Path(saved.get("path", "")).resolve() == Path(record["path"]),
            f"result {key} path binding differs")


def close_number(actual: float, saved: float, label: str, *, rtol: float = 2e-9,
                 atol: float = 1e-15) -> None:
    require(np.isfinite(actual) and np.isfinite(saved)
            and abs(actual - saved) <= max(atol, rtol * max(abs(actual), abs(saved))),
            f"{label} differs")


def remap_matrix(matrix: sparse.spmatrix, mapping: np.ndarray, size: int) -> sparse.csc_matrix:
    """Apply P.T @ matrix @ P by direct COO index remapping."""
    coo = matrix.tocoo(copy=False)
    require(mapping.shape == (matrix.shape[0],) and np.all((mapping >= 0) & (mapping < size)),
            "sparse remap mapping differs")
    result = sparse.coo_matrix((coo.data, (mapping[coo.row], mapping[coo.col])),
                               shape=(size, size), dtype=np.complex128).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    result.sort_indices()
    return result


def contact_remap(node_count: int, nodes: np.ndarray, indptr: np.ndarray,
                  count: int) -> tuple[np.ndarray, np.ndarray]:
    require(nodes.ndim == indptr.ndim == 1 and indptr.shape == (count + 1,)
            and indptr[0] == 0 and indptr[-1] == len(nodes), "contact CSR differs")
    require(np.all(np.diff(indptr) == 16) and len(nodes) == 16 * count,
            "contact node multiplicity differs")
    require(np.all((nodes >= 0) & (nodes < node_count)) and len(np.unique(nodes)) == len(nodes),
            "contact nodes overlap or are out of range")
    mapping = np.full(node_count, -1, dtype=np.int64)
    mapping[nodes] = np.repeat(np.arange(count, dtype=np.int64), 16)
    free = np.flatnonzero(mapping < 0)
    mapping[free] = count + np.arange(len(free), dtype=np.int64)
    require(np.array_equal(np.unique(mapping), np.arange(count + len(free), dtype=np.int64)),
            "contracted sheet basis is not contiguous")
    return mapping, free


def retained_gc(raw: Any, surface_lookup: dict[str, int], surface_active: np.ndarray,
                size: int) -> tuple[sparse.csc_matrix, sparse.csc_matrix, dict[str, int]]:
    coefficients = np.asarray(raw["partial_actual_1mhz_dispersion_admittance_scale_s"],
                              dtype=np.complex128)
    require(coefficients.shape == (36,) and np.all(np.isfinite(coefficients)),
            "partial coefficient vector differs")
    kept = sparse.csc_matrix((size, size), dtype=np.complex128)
    old_zero = None
    for ordinal in range(36):
        prefix = f"partial_{ordinal:02d}"
        names = recon._decode_text_vector(raw[prefix + "_net_names"], prefix)
        local = recon._csc_from_snapshot(raw, prefix)
        local_surface = np.asarray([surface_lookup.get(name, -1) for name in names], dtype=np.int64)
        require(np.all(local_surface >= 0), f"{prefix} source surface binding differs")
        active = surface_active[local_surface]
        require(np.all(active >= 0), f"{prefix} source surface is inactive")
        coo = local.tocoo(copy=False)
        row, column = active[coo.row], active[coo.col]
        live = (row >= 0) & (column >= 0)
        if ordinal == 0:
            upper = sparse.triu(local, k=1).tocoo()
            uf, us = active[upper.row], active[upper.col]
            require(len(upper.data) == 2064 and np.all((uf == TARGET) ^ (us == TARGET)),
                    "partial-0 owner partition differs")
            old_zero = sparse.coo_matrix((coo.data[live] * coefficients[0],
                                          (row[live], column[live])),
                                         shape=(NATIVE_SIZE, NATIVE_SIZE),
                                         dtype=np.complex128).tocsc()
            old_zero.sum_duplicates(); old_zero.eliminate_zeros(); old_zero.sort_indices()
            continue
        require(not np.any(((row[live] == TARGET) | (column[live] == TARGET))
                           & (coo.data[live] != 0)), f"unowned {prefix} touches L02")
        block = sparse.coo_matrix((coo.data[live] * coefficients[ordinal],
                                   (row[live], column[live])), shape=(size, size),
                                  dtype=np.complex128).tocsc()
        block.sum_duplicates()
        kept += block
    require(old_zero is not None, "partial-0 block was not reconstructed")
    kept.sum_duplicates(); kept.eliminate_zeros(); kept.sort_indices()
    return kept, old_zero, {"partial_ordinal": 0, "removed_owner_stamps": 2064,
                            "retained_nonincident_partial_0_stamps": 0}


def rewired_finite(raw: Any, binding: Any, contact_active: np.ndarray,
                   size: int) -> tuple[sparse.csc_matrix, sparse.csc_matrix,
                                       tuple[np.ndarray, np.ndarray, np.ndarray], dict[str, int]]:
    first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
    second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
    count = np.asarray(raw["finite_count"], dtype=np.float64)
    resistance = np.asarray(raw["finite_resistance_ohm_per_via"], dtype=np.float64)
    inductance = np.asarray(raw["finite_inductance_h_per_via"], dtype=np.float64)
    original = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)
    admittance = count / (resistance + 2j * np.pi * FREQUENCY_HZ * inductance)
    index = np.asarray(binding["active_finite_index"], dtype=np.int64)
    incidence = np.flatnonzero((first == TARGET) ^ (second == TARGET))
    require(index.shape == (76139,) and len(np.unique(index)) == len(index)
            and np.array_equal(np.sort(index), incidence)
            and not np.any((first == TARGET) & (second == TARGET)),
            "binding does not cover the 76139 native L02 branches")
    for name, source in (("original_finite_index", original), ("first_active_index", first),
                         ("second_active_index", second), ("native_count", count),
                         ("resistance_ohm", resistance), ("inductance_h", inductance)):
        require(np.array_equal(np.asarray(binding[name]), source[index]),
                f"binding {name} differs from source")
    ordinal = np.asarray(binding["native_contact_ordinal"], dtype=np.int64)
    side = np.asarray(binding["target_side"], dtype=np.int8)
    native_ordinals = np.unique(ordinal)
    require(ordinal.shape == side.shape == index.shape and len(native_ordinals) == 38836
            and np.all((ordinal >= 0) & (ordinal < len(contact_active))),
            "native contact ownership differs")
    moved_first, moved_second = first.copy(), second.copy()
    rows0, rows1 = index[side == 0], index[side == 1]
    require(len(rows0) + len(rows1) == 76139 and np.all(first[rows0] == TARGET)
            and np.all(second[rows1] == TARGET), "native target-side orientation differs")
    moved_first[rows0] = contact_active[ordinal[side == 0]]
    moved_second[rows1] = contact_active[ordinal[side == 1]]

    junctions = decoded_json(binding, "junctions_json_utf8")
    require(isinstance(junctions, list) and len(junctions) == 20, "junction count differs")
    replaced = np.asarray([row["replaced_active_finite_index"] for row in junctions], dtype=np.int64)
    support_ordinal = np.asarray([row["contact_ordinal"] for row in junctions], dtype=np.int64)
    require(len(np.unique(replaced)) == len(np.unique(support_ordinal)) == 20
            and not np.intersect1d(index, replaced).size
            and not np.intersect1d(native_ordinals, support_ordinal).size
            and np.array_equal(np.unique(np.r_[native_ordinals, support_ordinal]),
                               np.arange(38856, dtype=np.int64)),
            "native/junction contact ownership does not partition 38856 contacts")
    supports = np.asarray(binding["contact_support_index"], dtype=np.int64)
    drill = np.asarray([row["drill_support_index"] for row in junctions], dtype=np.int64)
    require(np.array_equal(supports[support_ordinal], drill), "junction support mapping differs")
    require(all([int(first[i]), int(second[i])] == row["original_active_endpoints"]
                for i, row in zip(replaced, junctions, strict=True))
            and np.all(count[replaced] == 1.0), "replaced composite provenance differs")
    leg_r = np.asarray([[row["first_leg_resistance_ohm"], row["second_leg_resistance_ohm"]]
                        for row in junctions], dtype=np.float64)
    leg_l = np.asarray([[row["first_leg_inductance_h"], row["second_leg_inductance_h"]]
                        for row in junctions], dtype=np.float64)
    require(np.all(leg_r > 0) and np.all(leg_l > 0)
            and np.allclose(leg_r.sum(axis=1), resistance[replaced], rtol=1e-13, atol=0)
            and np.allclose(leg_l.sum(axis=1), inductance[replaced], rtol=1e-13, atol=0),
            "two-leg composite values do not reproduce source")
    owners = [owner for row in junctions for owner in row["source_vias_in_native_path_order"]]
    require(len(owners) == 93 and len({owner["via_id_fold"] for owner in owners}) == 93,
            "junction source owner count/identity differs")
    for row, expected_r, expected_l in zip(junctions, leg_r, leg_l, strict=True):
        split = int(row["l02_split_after_source_via_count"])
        ordered = row["source_vias_in_native_path_order"]
        require(0 < split < len(ordered), "junction owner split differs")
        for key, expected in (("resistance_ohm", expected_r), ("inductance_h", expected_l)):
            sums = np.asarray((sum(owner[key] for owner in ordered[:split]),
                               sum(owner[key] for owner in ordered[split:])))
            require(np.allclose(sums, expected, rtol=1e-13, atol=0),
                    f"junction {key} source reconstruction differs")
    keep = np.ones(len(first), dtype=np.bool_); keep[replaced] = False
    junction_active = contact_active[support_ordinal]
    new_first = np.r_[moved_first[keep], first[replaced], junction_active]
    new_second = np.r_[moved_second[keep], junction_active, second[replaced]]
    leg_y = 1.0 / (leg_r + 2j * np.pi * FREQUENCY_HZ * leg_l)
    new_y = np.r_[admittance[keep], leg_y[:, 0], leg_y[:, 1]]
    require(np.all((new_first >= 0) & (new_first < size))
            and np.all((new_second >= 0) & (new_second < size))
            and np.all(new_first != new_second) and np.all(np.isfinite(new_y))
            and np.all(new_y.real > 0), "rewired finite arrays are invalid")
    finite = l14.trace.laplacian(new_first, new_second, new_y, size)
    original_finite = l14.trace.laplacian(first, second, admittance, NATIVE_SIZE)
    delta = l14.trace.laplacian(
        np.r_[first[replaced], np.full(20, TARGET), first[replaced]],
        np.r_[np.full(20, TARGET), second[replaced], second[replaced]],
        np.r_[leg_y[:, 0], leg_y[:, 1], -admittance[replaced]], NATIVE_SIZE)
    corrected = (original_finite + delta).tocsc()
    return finite, corrected, (new_first, new_second, new_y), {
        "native_incident_endpoints_rewired": 76139, "native_contact_count": 38836,
        "composite_branches_removed": 20, "positive_rl_legs_added": 40,
        "ordered_unique_source_rl_owners": 93, "corrected_finite_branch_count": len(new_y),
    }


def sparse_gate(actual: sparse.csc_matrix, expected: sparse.csc_matrix,
                label: str) -> dict[str, Any]:
    value = l14.trace.sparse_difference(actual, expected)
    require(value["relative_maximum_difference"] < 2e-12, f"{label} recollapse differs")
    return value


def triangle_sheet_action(xy: np.ndarray, triangles: np.ndarray, mesh_active: np.ndarray,
                          voltage: np.ndarray, *, batch: int = 131072) -> tuple[np.ndarray, float]:
    """Reassemble sheet nodal currents and Joule power directly from P1 triangles."""
    require(xy.ndim == 2 and xy.shape[1] == 2 and triangles.ndim == 2
            and triangles.shape[1] == 3 and mesh_active.shape == (len(xy),),
            "triangle Joule input shapes differ")
    require(np.all(np.isfinite(xy)) and triangles.min() >= 0 and triangles.max() < len(xy),
            "triangle Joule mesh values differ")
    action = np.zeros(len(voltage), dtype=np.complex128)
    joule = 0.0
    for start in range(0, len(triangles), batch):
        tri = triangles[start:start + batch]
        points = xy[tri]
        det = ((points[:, 1, 0] - points[:, 0, 0]) * (points[:, 2, 1] - points[:, 0, 1])
               - (points[:, 1, 1] - points[:, 0, 1]) * (points[:, 2, 0] - points[:, 0, 0]))
        require(np.all(np.isfinite(det)) and np.all(det != 0), "zero/nonfinite triangle in Joule check")
        bx = np.column_stack((points[:, 1, 1] - points[:, 2, 1],
                              points[:, 2, 1] - points[:, 0, 1],
                              points[:, 0, 1] - points[:, 1, 1])) / det[:, None]
        by = np.column_stack((points[:, 2, 0] - points[:, 1, 0],
                              points[:, 0, 0] - points[:, 2, 0],
                              points[:, 1, 0] - points[:, 0, 0])) / det[:, None]
        area = 0.5 * np.abs(det)
        active = mesh_active[tri]
        local_v = voltage[active]
        gx = np.sum(local_v * bx, axis=1)
        gy = np.sum(local_v * by, axis=1)
        local_i = SHEET_CONDUCTANCE_S * area[:, None] * (bx * gx[:, None] + by * gy[:, None])
        flat = active.ravel()
        values = local_i.ravel()
        action += (np.bincount(flat, weights=values.real, minlength=len(voltage))
                   + 1j * np.bincount(flat, weights=values.imag, minlength=len(voltage)))
        joule += float(np.sum(SHEET_CONDUCTANCE_S * area
                              * (np.abs(gx) ** 2 + np.abs(gy) ** 2), dtype=np.float64))
    return action, joule


def triplet(args: argparse.Namespace, name: str, reviewed: dict[str, Any],
            documents: dict[str, Any], result_inputs: dict[str, Any]) -> None:
    for kind in ("result", "npz", "review"):
        path = getattr(args, f"{name}_{kind}")
        meta = checked(path, getattr(args, f"{name}_{kind}_sha256"), f"{name} {kind}")
        reviewed[f"{name}_{kind}"] = meta
        same_binding(result_inputs, f"{name}_{kind}", meta)
        if kind != "npz":
            documents[f"{name}_{kind}"] = load_json(path)
    producer_result, independent = documents[f"{name}_result"], documents[f"{name}_review"]
    result_status, review_status, result_key, review_key = TRIPLETS[name]
    require(producer_result.get("status") == result_status
            and independent.get("status") == review_status, f"{name} accepted status differs")
    require(producer_result.get(result_key, {}).get("sha256") == reviewed[f"{name}_npz"]["sha256"],
            f"{name} result-to-NPZ binding differs")
    prior = independent.get("reviewed", {})
    require(prior.get("result", {}).get("sha256") == reviewed[f"{name}_result"]["sha256"]
            and prior.get(review_key, {}).get("sha256") == reviewed[f"{name}_npz"]["sha256"],
            f"{name} independent-review binding differs")
    findings = independent.get("findings")
    require(findings is None or findings == []
            or (isinstance(findings, dict) and not findings.get("p1") and not findings.get("p2")),
            f"{name} accepted review contains findings")


def common_documents(args: argparse.Namespace, mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    reviewed = {
        "review_script": checked(Path(__file__), sha(Path(__file__)), "review script"),
        "producer": checked(Path(__file__).with_name("run_astra_l02_sheet_r_shadow.py"),
                            PRODUCER_SHA256, "producer"),
        "result": checked(args.result, args.result_sha256, "result"),
        "guard": checked(args.guard, args.guard_sha256, "external guard"),
        "frozen_driver": checked(args.driver, args.driver_sha256, "frozen driver"),
        "assembly": checked(args.assembly, args.assembly_sha256, "assembly JSON"),
    }
    require(args.driver_sha256 == PRODUCER_SHA256, "frozen driver is not the reviewed producer")
    require(args.driver.resolve() == args.result.resolve().parent / "driver-at-run.py",
            "frozen driver is not result-directory driver-at-run.py")
    documents = {"result": load_json(args.result), "guard": load_json(args.guard),
                 "assembly": load_json(args.assembly)}
    result, guard = documents["result"], documents["guard"]
    expected_status = ("VERIFIED_CONDITIONAL_L02_SHEET_R_ASSEMBLY_ONLY" if mode == "assembly"
                       else "COMPLETED_CONDITIONAL_L02_SHEET_R_SHADOW")
    require(result.get("program") == PROGRAM and result.get("version") == VERSION
            and result.get("status") == expected_status and result.get("script_sha256") == PRODUCER_SHA256,
            "result identity/status/producer differs")
    require(documents["assembly"] == result.get("assembly"),
            "assembly JSON is not deeply equal to embedded result assembly")
    require(guard.get("status") == "COMPLETED_NATIVE_WORKER" and guard.get("exit_code") == 0,
            "external worker did not complete cleanly")
    require(float(guard.get("elapsed_s", np.inf)) <= float(guard.get("max_runtime_s", -np.inf))
            and int(guard.get("sampled_peak_private_bytes", 1)) <= int(guard.get("max_memory_bytes", 0)),
            "external worker resource bound differs")
    command = guard.get("worker_command")
    allowed = {reviewed["producer"]["path"], reviewed["frozen_driver"]["path"]}
    resolved_tokens = set()
    if isinstance(command, list):
        for token in command:
            try:
                resolved_tokens.add(str(Path(str(token)).resolve()))
            except (OSError, ValueError):
                pass
    require(isinstance(command, list) and allowed & resolved_tokens,
            "external guard worker command does not name the reviewed producer/driver")
    result_inputs = result.get("inputs", {})
    require(isinstance(result_inputs, dict), "result inputs are missing")
    for name, (path, expected) in SOURCE_PINS.items():
        meta = checked(Path(path), expected, name)
        reviewed[name] = meta
        same_binding(result_inputs, name, meta)
    for name in TRIPLETS:
        triplet(args, name, reviewed, documents, result_inputs)
    ideal_result = load_json(IDEAL_RESULT)
    ideal_review = load_json(IDEAL_REVIEW)
    documents["ideal_result"], documents["ideal_review"] = ideal_result, ideal_review
    require(ideal_result.get("status") == "COMPLETED_CONDITIONAL_L02_IDEAL_JUNCTION_SHADOW"
            and ideal_result.get("frequency_hz") == FREQUENCY_HZ,
            "corrected ideal result differs")
    require(ideal_review.get("status") == "ACCEPT_CONDITIONAL_L02_IDEAL_JUNCTION_FIELD_INDEPENDENT_REVIEW"
            and ideal_review.get("findings") == []
            and ideal_review.get("reviewed", {}).get("result", {}).get("sha256") == SOURCE_PINS["ideal_result"][1]
            and ideal_review.get("reviewed", {}).get("field", {}).get("sha256") == SOURCE_PINS["ideal_field"][1],
            "corrected ideal independent-review binding differs")
    return reviewed, documents


def review(args: argparse.Namespace, mode: str) -> dict[str, Any]:
    reviewed, documents = common_documents(args, mode)
    result, saved_assembly = documents["result"], documents["assembly"]
    binding_result = documents["binding_result"]
    mesh_result, mesh_review = documents["mesh_result"], documents["mesh_review"]
    binding_review, gc_review = documents["binding_review"], documents["gc_review"]
    require(mesh_review.get("reviewed", {}).get("frozen_driver", {}).get("sha256")
            == mesh_result.get("script_sha256"), "mesh frozen-driver review binding differs")
    require(binding_result.get("script_sha256") == SOURCE_PINS["binding_producer"][1]
            and binding_review.get("reviewed", {}).get("producer", {}).get("sha256")
            == SOURCE_PINS["binding_producer"][1], "binding producer provenance differs")
    require(binding_result.get("native_via_count") == 76139
            and binding_result.get("native_contact_count") == 38836
            and binding_result.get("junction_count") == 20
            and binding_result.get("junction_source_via_owner_count") == 93,
            "binding ownership counts differ")
    leaves = binding_result.get("excluded_leaves")
    require(isinstance(leaves, list) and len(leaves) == 2
            and {row.get("source_via_id") for row in leaves} == {"via1312784", "via1612214"}
            and all(row.get("shares_native_contact_support") is True
                    and row.get("circuit_policy") == "retain original excluded leaf; add no circuit branch"
                    for row in leaves), "two excluded-leaf policy differs")
    gc_result = documents["gc_result"]
    require(gc_result.get("script_sha256") == SOURCE_PINS["gc_projector"][1]
            and gc_review.get("reviewed", {}).get("frozen_driver", {}).get("sha256")
            == SOURCE_PINS["gc_projector"][1], "GC producer provenance differs")
    owner_contract = gc_result.get("owner_contract", {})
    require(owner_contract.get("partial_ordinal") == 0 and owner_contract.get("owner_count") == 2064
            and owner_contract.get("external_active_identity_count") == 1696
            and owner_contract.get("area_relative_tolerance") == 2e-9,
            "accepted GC owner contract differs")

    for path in (args.mesh_npz, args.binding_npz, args.gc_npz, SOURCE_PINS["raw"][0],
                 SOURCE_PINS["derived"][0], IDEAL_FIELD):
        if Path(path).suffix == ".npz":
            with ZipFile(path) as archive:
                require(archive.testzip() is None, f"{Path(path).name} NPZ CRC differs")

    with (np.load(args.mesh_npz, allow_pickle=False) as mesh,
          np.load(args.binding_npz, allow_pickle=False) as binding,
          np.load(args.gc_npz, allow_pickle=False) as gc_block,
          np.load(SOURCE_PINS["raw"][0], allow_pickle=False) as raw,
          np.load(SOURCE_PINS["derived"][0], allow_pickle=False) as derived,
          np.load(IDEAL_FIELD, allow_pickle=False) as ideal_field):
        require(all(value.dtype.kind != "O" for archive in (mesh, binding, gc_block)
                    for value in archive.values()), "object array in accepted NPZ")
        xy = np.asarray(mesh["node_xy_um"], dtype=np.float64)
        triangles = np.asarray(mesh["triangles"], dtype=np.int64)
        stiffness = read_csc(mesh, "stiffness")
        supports = np.asarray(mesh["contact_support_index"], dtype=np.int64)
        require(supports.shape == (38856,) and np.all(np.diff(supports) > 0)
                and np.array_equal(supports, binding["contact_support_index"]),
                "mesh/binding contact order differs")
        mapping, free = contact_remap(len(xy), np.asarray(mesh["contact_node_indices"], dtype=np.int64),
                                      np.asarray(mesh["contact_node_indptr"], dtype=np.int64), len(supports))
        local_size = len(supports) + len(free)
        sheet_local = remap_matrix(stiffness * SHEET_CONDUCTANCE_S, mapping, local_size)
        sheet_scale = max(maximum(sheet_local.data), 1.0)
        require(maximum((sheet_local - sheet_local.T).data) <= 2e-12 * sheet_scale
                and maximum(np.asarray(sheet_local.sum(axis=1)).ravel()) <= 2e-12 * sheet_scale,
                "contracted sheet matrix identities differ")
        sheet_active = np.r_[TARGET, np.arange(NATIVE_SIZE, NATIVE_SIZE + local_size - 1, dtype=np.int64)]
        size = NATIVE_SIZE + local_size - 1
        sheet = remap_matrix(sheet_local, sheet_active, size)
        contact_active = sheet_active[:len(supports)]
        mesh_active = sheet_active[mapping]

        surface_ids = recon._decode_text_vector(raw["surface_node_ids"], "surface IDs")
        surface_lookup = {name: ordinal for ordinal, name in enumerate(surface_ids)}
        require(len(surface_lookup) == len(surface_ids), "source surface IDs repeat")
        global_to_active = np.asarray(raw["global_to_active_indices"], dtype=np.int64)
        surface_active = global_to_active[np.asarray(raw["surface_to_reduced_indices"], dtype=np.int64)]
        require(len(raw["active_global_reduced_indices"]) == NATIVE_SIZE, "native active size differs")
        original_gc, _ = recon._build_partial_matrix(raw, surface_lookup=surface_lookup,
            surface_to_reduced=raw["surface_to_reduced_indices"], global_to_active=global_to_active,
            active_size=NATIVE_SIZE)
        kept_gc, old_partial0, owner_partition = retained_gc(raw, surface_lookup, surface_active, size)
        external = np.asarray(gc_block["external_active_indices"], dtype=np.int64)
        require(external.shape == (1696,) and np.all(np.diff(external) > 0)
                and np.all((external >= 0) & (external < NATIVE_SIZE)) and TARGET not in external,
                "GC external active identities differ")
        capacitance = read_csc(gc_block, "capacitance", "capacitance_data_f")
        require(capacitance.shape == (len(xy) + len(external),) * 2,
                "GC block basis differs")
        coefficient = complex(np.asarray(raw["partial_actual_1mhz_dispersion_admittance_scale_s"],
                                         dtype=np.complex128)[0])
        distributed_gc = remap_matrix(capacitance.astype(np.complex128) * coefficient,
                                      np.r_[mesh_active, external], size)
        finite, corrected_finite, finite_edges, finite_contract = rewired_finite(
            raw, binding, contact_active, size)
        termination_native, termination_y, _ = recon._termination_matrix(derived, NATIVE_SIZE)
        tp = np.asarray(derived["termination_positive_active_indices"], dtype=np.int64)
        tn = np.asarray(derived["termination_negative_active_indices"], dtype=np.int64)
        require(not np.any((tp == TARGET) | (tn == TARGET)), "termination touches split target")
        termination = termination_native.copy(); termination.resize((size, size))
        collapse = np.arange(size, dtype=np.int64); collapse[NATIVE_SIZE:] = TARGET
        recollapse = {
            "retained_gc_recollapse": sparse_gate(l14.trace.collapse_matrix(kept_gc, collapse, NATIVE_SIZE),
                                                   original_gc - old_partial0, "retained GC"),
            "distributed_partial_0_recollapse": sparse_gate(
                l14.trace.collapse_matrix(distributed_gc, collapse, NATIVE_SIZE), old_partial0,
                "distributed partial-0 GC"),
            "gc_only_recollapse": sparse_gate(
                l14.trace.collapse_matrix(kept_gc + distributed_gc, collapse, NATIVE_SIZE),
                original_gc, "GC-only"),
            "finite_recollapse": sparse_gate(
                l14.trace.collapse_matrix(finite, collapse, NATIVE_SIZE), corrected_finite, "finite"),
        }
        total = kept_gc + distributed_gc + finite + termination + sheet
        recollapse["full_equipotential_recollapse"] = sparse_gate(
            l14.trace.collapse_matrix(total, collapse, NATIVE_SIZE),
            original_gc + corrected_finite + termination_native, "full equipotential")
        sheet_collapse = maximum(l14.trace.collapse_matrix(sheet, collapse, NATIVE_SIZE).data)
        require(sheet_collapse <= 2e-12 * max(maximum(sheet.data), 1.0),
                "sheet does not vanish under equipotential collapse")

        batch = np.asarray(raw["batch_port_indices"], dtype=np.int64)
        require(batch.shape == (1,) and int(batch[0]) == 0
                and recon._decode_text_vector(raw["batch_port_ids"], "batch ports") == (l14.RAIL,),
                "saved source batch/rail differs")
        positive, negative = map(int, global_to_active[raw["solve_port_reduced_nodes"][0]])
        gauge = int(np.asarray(raw["gauge_active_index"], dtype=np.int64).item())
        ideal = documents["ideal_result"]
        require((positive, negative) == (2699, 2656)
                and (positive, negative) == (ideal["assembly"]["positive_active_index"],
                                             ideal["assembly"]["negative_active_index"])
                and gauge == ideal["assembly"]["gauge_active_index"],
                "source/gauge provenance differs")
        ideal_voltage = np.asarray(ideal_field["active_voltage"], dtype=np.complex128)
        require(ideal_voltage.shape == (NATIVE_SIZE,) and ideal_voltage[gauge] == 0,
                "corrected ideal field basis/gauge differs")
        baseline = complex(ideal_voltage[positive] - ideal_voltage[negative])
        require(abs(baseline - complex(*ideal["point"]["zdd_ohm"])) <= 1e-18,
                "corrected ideal voltage differs")
        ideal_rhs = np.zeros(NATIVE_SIZE, dtype=np.complex128)
        ideal_rhs[positive], ideal_rhs[negative] = 1.0, -1.0
        ideal_matrix = original_gc + corrected_finite + termination_native
        ideal_csc = maximum(ideal_matrix @ ideal_voltage - ideal_rhs)
        collapsed_first, collapsed_second = collapse[finite_edges[0]], collapse[finite_edges[1]]
        ideal_physical = maximum(original_gc @ ideal_voltage
            + branch_action(collapsed_first, collapsed_second, finite_edges[2], ideal_voltage)
            + branch_action(tp, tn, termination_y, ideal_voltage) - ideal_rhs)
        require(ideal_csc < 1e-7 and ideal_physical < 1e-7,
                "corrected ideal saved field KCL differs")

        expected_counts = {
            "native_active_nodes": NATIVE_SIZE, "expanded_active_nodes": size,
            "mesh_nodes": len(xy), "sheet_contracted_nodes": local_size,
            "contact_count": 38856, "contact_nodes_per_support": 16,
            "free_sheet_nodes": len(free), "frequency_hz": FREQUENCY_HZ,
            "batch_port_index": 0, "positive_active_index": positive,
            "negative_active_index": negative, "gauge_active_index": gauge,
        }
        for key, value in expected_counts.items():
            require(saved_assembly.get(key) == value, f"saved assembly {key} differs")
        require(saved_assembly.get("equality_contraction") == "sparse index remap; no Kron"
                and saved_assembly.get("gc_owner_partition") == owner_partition,
                "saved assembly contraction/owner contract differs")
        for key, value in finite_contract.items():
            require(saved_assembly.get(key) == value, f"saved assembly {key} differs")
        for key, value in recollapse.items():
            require(value["relative_maximum_difference"] < 2e-12
                    and saved_assembly.get(key, {}).get("relative_maximum_difference", np.inf) < 2e-12,
                    f"saved assembly {key} gate differs")
        close_number(sheet_collapse, float(saved_assembly["sheet_equipotential_collapse_max_abs_s"]),
                     "sheet collapse", rtol=1e-8)
        close_number(ideal_csc, float(saved_assembly["corrected_ideal_csc_residual_max_abs_a"]),
                     "corrected ideal CSC residual", rtol=1e-7)
        close_number(ideal_physical,
                     float(saved_assembly["corrected_ideal_source_current_residual_max_abs_a"]),
                     "corrected ideal physical residual", rtol=1e-7)
        require(saved_assembly.get("corrected_ideal_zdd_ohm") == pair(baseline),
                "saved corrected ideal impedance differs")
        checks: dict[str, Any] = {
            "contact_remap": {"contacts": 38856, "nodes_per_contact": 16,
                              "free_nodes": len(free), "contiguous": True},
            "branch_ownership": {**finite_contract, "excluded_shared_support_leaves": 2},
            "recollapse": recollapse,
            "sheet_equipotential_collapse_max_abs_s": sheet_collapse,
            "corrected_ideal": {"zdd_ohm": pair(baseline),
                                "csc_residual_max_abs_a": ideal_csc,
                                "physical_residual_max_abs_a": ideal_physical},
        }

        if mode == "field":
            for key, path, expected, label in (
                ("epsilon_one", args.epsilon_one, args.epsilon_one_sha256, "epsilon-1 JSON"),
                ("initial_field", args.initial_field, args.initial_field_sha256,
                 "initial unvalidated field NPZ"),
                ("field", args.field, args.field_sha256, "field NPZ"),
                ("assembly_review", args.assembly_review, args.assembly_review_sha256,
                 "accepted assembly review")):
                reviewed[key] = checked(path, expected, label)
            epsilon = load_json(args.epsilon_one)
            assembly_review = load_json(args.assembly_review)
            require(epsilon == result.get("point"),
                    "epsilon-1 JSON is not deeply equal to embedded result point")
            require(assembly_review.get("program") == PROGRAM
                    and assembly_review.get("version") == VERSION
                    and assembly_review.get("status") == ASSEMBLY_ACCEPT,
                    "assembly independent-review status differs")
            prior = assembly_review.get("reviewed", {})
            require(prior.get("assembly", {}).get("sha256") == args.assembly_sha256
                    and prior.get("producer", {}).get("sha256") == PRODUCER_SHA256
                    and prior.get("review_script", {}).get("sha256") == reviewed["review_script"]["sha256"],
                    "field is not bound to the accepted identical assembly")
            for name in TRIPLETS:
                for kind in ("result", "npz", "review"):
                    require(prior.get(f"{name}_{kind}", {}).get("sha256")
                            == reviewed[f"{name}_{kind}"]["sha256"],
                            f"accepted assembly {name} {kind} binding differs")
            point = result["point"]
            initial_meta = point.get("initial_field", {})
            require(initial_meta.get("sha256") == args.initial_field_sha256
                    and Path(initial_meta.get("path", "")).resolve() == args.initial_field.resolve()
                    and initial_meta.get("size_bytes") == args.initial_field.stat().st_size
                    and initial_meta.get("status")
                    == "UNVALIDATED_INITIAL_SOLVE_BEFORE_SOURCE_CURRENT_CALLBACK",
                    "result-to-initial-field binding/status differs")
            require(point.get("field", {}).get("sha256") == args.field_sha256
                    and Path(point["field"]["path"]).resolve() == args.field.resolve()
                    and point["field"].get("size_bytes") == args.field.stat().st_size
                    and point["field"].get("status") == "UNVALIDATED_BEFORE_ACCEPTANCE_GATES"
                    and point.get("field_saved_before_acceptance_gates") is True,
                    "result-to-field binding differs")
            with ZipFile(args.initial_field) as archive:
                require(archive.testzip() is None, "initial field NPZ CRC differs")
            with ZipFile(args.field) as archive:
                require(archive.testzip() is None, "field NPZ CRC differs")
            with np.load(args.initial_field, allow_pickle=False) as initial:
                require(all(value.dtype.kind != "O" for value in initial.values()),
                        "object array in initial field NPZ")
                initial_voltage = np.asarray(initial["active_voltage"], dtype=np.complex128)
                initial_rhs = np.asarray(initial["source_current_rhs_a"], dtype=np.complex128)
                expected_initial_rhs = np.zeros(size, dtype=np.complex128)
                expected_initial_rhs[positive], expected_initial_rhs[negative] = 1.0, -1.0
                require(initial_voltage.shape == initial_rhs.shape == (size,)
                        and np.array_equal(initial_rhs, expected_initial_rhs)
                        and np.array_equal(initial["sheet_active_indices"], sheet_active)
                        and np.array_equal(initial["contact_support_index"], supports),
                        "initial field basis/contact/RHS arrays differ")
                require(np.array_equal(initial["source_positive_active_index"], [positive])
                        and np.array_equal(initial["source_negative_active_index"], [negative])
                        and np.array_equal(initial["source_gauge_active_index"], [gauge]),
                        "initial field source/gauge arrays differ")
                require(decoded_text(initial, "field_status_utf8")
                        == "UNVALIDATED_L02_SHEET_R_SHADOW_FIELD"
                        and decoded_text(initial, "mesh_npz_sha256_utf8") == args.mesh_npz_sha256
                        and decoded_text(initial, "binding_npz_sha256_utf8") == args.binding_npz_sha256
                        and decoded_text(initial, "gc_npz_sha256_utf8") == args.gc_npz_sha256
                        and decoded_text(initial, "source_corrected_ideal_field_sha256_utf8")
                        == SOURCE_PINS["ideal_field"][1], "initial field provenance strings differ")
            with np.load(args.field, allow_pickle=False) as field:
                require(all(value.dtype.kind != "O" for value in field.values()),
                        "object array in field NPZ")
                voltage = np.asarray(field["active_voltage"], dtype=np.complex128)
                rhs = np.asarray(field["source_current_rhs_a"], dtype=np.complex128)
                require(voltage.shape == rhs.shape == (size,) and np.all(np.isfinite(voltage))
                        and np.array_equal(field["sheet_active_indices"], sheet_active)
                        and np.array_equal(field["contact_support_index"], supports),
                        "field basis/contact arrays differ")
                require(np.array_equal(field["source_positive_active_index"], [positive])
                        and np.array_equal(field["source_negative_active_index"], [negative])
                        and np.array_equal(field["source_gauge_active_index"], [gauge])
                        and voltage[gauge] == 0, "field source/gauge arrays differ")
                expected_rhs = np.zeros(size, dtype=np.complex128)
                expected_rhs[positive], expected_rhs[negative] = 1.0, -1.0
                require(np.array_equal(rhs, expected_rhs), "field RHS differs")
                require(decoded_text(field, "field_status_utf8") == "UNVALIDATED_L02_SHEET_R_SHADOW_FIELD"
                        and decoded_text(field, "mesh_npz_sha256_utf8") == args.mesh_npz_sha256
                        and decoded_text(field, "binding_npz_sha256_utf8") == args.binding_npz_sha256
                        and decoded_text(field, "gc_npz_sha256_utf8") == args.gc_npz_sha256
                        and decoded_text(field, "source_corrected_ideal_field_sha256_utf8")
                        == SOURCE_PINS["ideal_field"][1], "field provenance strings differ")

            matrix = total.tocsc(); matrix.sum_duplicates(); matrix.eliminate_zeros(); matrix.sort_indices()
            guards = l14.trace.matrix_guard(matrix)
            csc_residual = maximum(matrix @ voltage - rhs)
            independent_sheet_action, triangle_joule = triangle_sheet_action(
                xy, triangles, mesh_active, voltage)
            sheet_action_error = maximum(independent_sheet_action - sheet @ voltage)
            sheet_action_scale = max(maximum(sheet @ voltage), np.finfo(float).tiny)
            require(sheet_action_error / sheet_action_scale < 2e-9,
                    "independent triangle sheet action differs from saved stiffness")
            new_first, new_second, new_y = finite_edges
            actions = {
                "retained_gc": kept_gc @ voltage,
                "distributed_gc": distributed_gc @ voltage,
                "finite_via": branch_action(new_first, new_second, new_y, voltage),
                "termination": branch_action(tp, tn, termination_y, voltage),
                "sheet_dc": independent_sheet_action,
            }
            physical_residual = maximum(sum(actions.values()) - rhs)
            require(csc_residual < 1e-7 and physical_residual < 1e-7,
                    "field CSC/physical KCL gate failed")
            zdd = complex(voltage[positive] - voltage[negative])
            retained = np.delete(np.arange(size, dtype=np.int64), gauge)
            local = matrix[retained, :][:, retained].tocsc()
            residual = local @ voltage[retained] - rhs[retained]
            backward = float(np.linalg.norm(residual) / max(
                np.linalg.norm(local.data) * np.linalg.norm(voltage[retained])
                + np.linalg.norm(rhs[retained]), np.finfo(float).tiny))
            require(backward <= 1e-9, "independent normalized backward error failed")
            powers = {name: complex(np.conj(np.vdot(voltage, action)))
                      for name, action in actions.items()}
            closure = float(abs(sum(powers.values()) - zdd))
            require(zdd.real >= -1e-12 and all(value.real >= -1e-12 for value in powers.values())
                    and closure <= max(abs(zdd), np.finfo(float).tiny) * 1e-7,
                    "field passivity/power closure failed")
            triangle_joule_relative = abs(triangle_joule - powers["sheet_dc"].real) / max(
                triangle_joule, abs(powers["sheet_dc"].real), np.finfo(float).tiny)
            require(triangle_joule_relative < 2e-9 and abs(powers["sheet_dc"].imag) < 1e-10,
                    "independent triangle Joule identity failed")
            require(abs(zdd - complex(*point["zdd_ohm"])) <= max(1e-15, abs(zdd) * 2e-12)
                    and float(point["csc_matvec_residual_max_abs_a"]) < 1e-7
                    and float(point["physical_residual_max_abs_a"]) < 1e-7
                    and float(point["normalized_backward_residual"]) <= 1e-9,
                    "saved field point gate differs")
            close_number(backward, float(point["normalized_backward_residual"]),
                         "normalized backward residual", rtol=1e-8)
            require(point.get("matrix") == guards, "saved CSC guard metadata differs")
            saved_power = point.get("power_contributions_ohm", {})
            require(set(saved_power) == set(powers), "saved power categories differ")
            for name, value in powers.items():
                require(abs(value - complex(*saved_power[name]))
                        <= max(1e-15, abs(value) * 2e-8), f"saved {name} power differs")
            close_number(closure, float(point["power_closure_error_ohm"]),
                         "power closure", rtol=2e-6)
            corrections = point.get("current_corrections")
            require(isinstance(corrections, list) and 1 <= len(corrections) <= 4
                    and corrections[-1]["physical_residual_max_abs_a"] < 1e-7
                    and corrections[-1]["zdd_ohm"] == point["zdd_ohm"],
                    "saved source-current correction history differs")
            pivot = float(point.get("pivot_abs_ratio", np.inf))
            require(np.isfinite(pivot) and 0 < pivot <= 1e13, "producer pivot receipt failed")
            checks["field"] = {
                "zdd_ohm": pair(zdd), "csc_residual_max_abs_a": csc_residual,
                "physical_residual_max_abs_a": physical_residual,
                "normalized_backward_residual": backward,
                "matrix": guards, "power_contributions_ohm": {name: pair(value) for name, value in powers.items()},
                "power_closure_error_ohm": closure,
                "triangle_sheet_action_relative_error": sheet_action_error / sheet_action_scale,
                "triangle_joule_w": triangle_joule,
                "triangle_joule_relative_error": triangle_joule_relative,
                "pivot_abs_ratio_producer_receipt_only": pivot,
            }

    status = ASSEMBLY_ACCEPT if mode == "assembly" else FIELD_ACCEPT
    return {
        "program": PROGRAM, "version": VERSION, "status": status, "mode": mode,
        "reviewed": reviewed, "checks": checks, "findings": [],
        "scope": ("Independent saved sparse/contact/source reconstruction and corrected-ideal recollapse review. "
                  "Field mode additionally reviews the saved voltage by CSC and branch-current KCL, impedance, "
                  "backward error, category power, closure, and direct triangle Joule reassembly. No factorization, "
                  "mesh generation, source overlap, projection, or circuit solve is repeated."),
        "limitations": (["Sparse-LU pivot ratio is checked only as a bounded finite producer receipt; no factorization is repeated."]
                        if mode == "field" else []),
    }


def self_check() -> dict[str, Any]:
    nodes = np.arange(32, dtype=np.int64)
    mapping, free = contact_remap(35, nodes, np.asarray((0, 16, 32)), 2)
    require(np.array_equal(mapping[:16], np.zeros(16, dtype=np.int64))
            and np.array_equal(mapping[16:32], np.ones(16, dtype=np.int64))
            and np.array_equal(free, (32, 33, 34)), "contact-remap coupon failed")
    edges = l14.trace.laplacian(np.arange(34), np.arange(1, 35), np.ones(34), 35)
    contracted = remap_matrix(edges, mapping, 5)
    require(contracted.shape == (5, 5) and maximum(np.asarray(contracted.sum(axis=1)).ravel()) < 1e-12,
            "sparse-remap coupon failed")
    xy = np.asarray(((0.0, 0.0), (2.0, 0.0), (0.0, 1.0)))
    tri = np.asarray(((0, 1, 2),), dtype=np.int64)
    voltage = np.asarray((0.2 + 0.1j, 0.7 - 0.2j, -0.1 + 0.4j))
    action, joule = triangle_sheet_action(xy, tri, np.arange(3, dtype=np.int64), voltage, batch=1)
    require(maximum(np.asarray((action.sum(),))) < 1e-12 and joule > 0
            and abs(float(np.vdot(voltage, action).real) - joule) < max(1e-12, joule * 1e-14),
            "triangle Joule coupon failed")
    y = np.asarray((2.0 - 0.25j,))
    branch = branch_action(np.asarray((0,)), np.asarray((1,)), y, voltage[:2])
    require(maximum(branch - l14.trace.laplacian(np.asarray((0,)), np.asarray((1,)), y, 2)
                    @ voltage[:2]) < 1e-15, "branch-current coupon failed")
    return {"program": PROGRAM, "version": VERSION,
            "status": "PASS_L02_SHEET_R_SHADOW_REVIEW_SELF_CHECK"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--result", type=Path); parser.add_argument("--result-sha256")
    parser.add_argument("--guard", type=Path); parser.add_argument("--guard-sha256")
    parser.add_argument("--driver", type=Path); parser.add_argument("--driver-sha256")
    parser.add_argument("--assembly", type=Path); parser.add_argument("--assembly-sha256")
    for name in TRIPLETS:
        for kind in ("result", "npz", "review"):
            parser.add_argument(f"--{name}-{kind}", type=Path)
            parser.add_argument(f"--{name}-{kind}-sha256")
    parser.add_argument("--epsilon-one", type=Path); parser.add_argument("--epsilon-one-sha256")
    parser.add_argument("--initial-field", type=Path); parser.add_argument("--initial-field-sha256")
    parser.add_argument("--field", type=Path); parser.add_argument("--field-sha256")
    parser.add_argument("--assembly-review", type=Path); parser.add_argument("--assembly-review-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    check = self_check()
    if args.self_check:
        print(json.dumps(check, sort_keys=True))
        return
    common = ("result", "result_sha256", "guard", "guard_sha256", "driver", "driver_sha256",
              "assembly", "assembly_sha256", "output")
    triplet_names = tuple(f"{name}_{kind}{suffix}" for name in TRIPLETS
                          for kind in ("result", "npz", "review") for suffix in ("", "_sha256"))
    if any(getattr(args, name) is None for name in common + triplet_names):
        parser.error("all result/guard/driver/assembly and mesh/binding/GC triplet paths and SHA pins are required")
    field_names = ("epsilon_one", "epsilon_one_sha256", "initial_field", "initial_field_sha256",
                   "field", "field_sha256",
                   "assembly_review", "assembly_review_sha256")
    supplied = [getattr(args, name) is not None for name in field_names]
    if any(supplied) and not all(supplied):
        parser.error("field mode requires epsilon-one, initial-field, field, and accepted assembly-review paths and SHA pins")
    mode = "field" if all(supplied) else "assembly"
    value = review(args, mode)
    atomic_json(args.output, value)
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": value["status"],
                      "review_sha256": sha(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
