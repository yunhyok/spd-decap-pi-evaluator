"""Pinned aggregation of qualified L04 source-contact currents by support.

This prepares no mesh, resistance matrix, current solve, field, SQLite query, FMM,
or magnetic action.  It only applies the saved source-orientation rules to the
qualified contact-current artifact and preserves the resulting residuals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RUN_RELEASED = True
OUTPUT_NAME = "astra-l04-fixed-contact-currents-01"
PREFLIGHT_NAME = "astra-l04-fixed-contact-currents-preflight-01"
RECORD_COUNT = 76_166
GROUP_COUNT = 38_278

PINS = {
    "qualified_rebind_result": (
        R / "astra-l04-source-contact-current-rebind-02/result.json",
        "dd9ae1512395f1393b868055269044b858fede31ae9132d00f577406cc1e4d24",
    ),
    "qualified_rebind_artifact": (
        R / "astra-l04-source-contact-current-rebind-01/l04-source-contact-current-rebind.npz",
        "2cf4dcb6d97a31a01d818bbd600906a3f5f1dbcec8f0a7d0b3105f22b23635f7",
    ),
    "rebind_attempt_driver": (
        R / "astra-l04-source-contact-current-rebind-01/driver-at-run.py",
        "e881e1194203f1201694ad999fac8081be85d077856e39e1a8d92bbc70905ee3",
    ),
    "contact_inputs": (
        R / "astra-l04-source-contact-inputs-01/contact-ledger-inputs.npz",
        "6e5886f81a14c1f1fec43a4af64a175bca518486cfd74fbc7794c551bd74ec4a",
    ),
    "pad_result": (
        R / "astra-l04-pad-conductor-domain-01/result.json",
        "bde2eb7c636276da737d0e6c91b0309603d9570899ccf5bbbfc2acffe7a74f96",
    ),
    "pad_support_map": (
        R / "astra-l04-pad-conductor-domain-01/l04-pad-drill-support-map.npz",
        "7c76d3e61680d2092bdb3ebe53a03fd52030de478da570a35c4ce09baafc92ff",
    ),
    "pad_external_budget": (
        R / "astra-l04-pad-conductor-domain-01/external-budget.json",
        "4a4cca2dc0b6e40721ad22a5b1c54d67da9d0b2813f981179de1aeef9f194547",
    ),
    "budget_helper": (
        ROOT / "tools/research/reconstruct_astra_native_loaded_field.py",
        "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213",
    ),
}


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def atomic_json(path: Path, document: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def verify_pins() -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for name, (path, expected) in PINS.items():
        require(path.is_file() and sha256(path) == expected, f"pinned {name}")
        values[name] = receipt(path)
    return values


def load_budget():
    helper, expected = PINS["budget_helper"]
    require(sha256(helper) == expected, "pinned Budget helper")
    runtime = str(ROOT / "outputs" / "research-runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import reconstruct_astra_native_loaded_field as recon

    require(Path(recon.__file__).resolve() == helper.resolve(), "Budget helper import location")
    return recon._Budget.create(60.0, 4.0), recon


def complex_pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def deterministic_sum(values: np.ndarray) -> complex:
    return complex(math.fsum(float(value.real) for value in values), math.fsum(float(value.imag) for value in values))


def deterministic_envelope(values: np.ndarray) -> float:
    return 32.0 * np.finfo(np.float64).eps * max(1.0, float(np.abs(values).sum()))


def grouped_sum(values: np.ndarray, group: np.ndarray, count: int) -> np.ndarray:
    order = np.argsort(group, kind="stable")
    counts = np.bincount(group, minlength=count)
    result = np.zeros(count, dtype=np.complex128)
    present = np.flatnonzero(counts)
    starts = np.cumsum(counts, dtype=np.int64) - counts
    ordered = values[order]
    result[present] = (
        np.add.reduceat(ordered.real, starts[present])
        + 1j * np.add.reduceat(ordered.imag, starts[present])
    )
    return result


def group_rows(group: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(group, kind="stable")
    counts = np.bincount(group, minlength=GROUP_COUNT).astype(np.int64, copy=False)
    return np.r_[np.int64(0), np.cumsum(counts, dtype=np.int64)], order.astype(np.int64, copy=False)


def preflight() -> dict[str, object]:
    pins = verify_pins()
    qualified = json.loads(PINS["qualified_rebind_result"][0].read_bytes())
    pad = json.loads(PINS["pad_result"][0].read_bytes())
    external = json.loads(PINS["pad_external_budget"][0].read_bytes())
    require(qualified["status"] == "QUALIFIED_L04_SAVED_SOURCE_CONTACT_CURRENT_REBIND", "qualified rebind status")
    require(qualified["qualified_artifact"]["sha256"] == PINS["qualified_rebind_artifact"][1], "qualified artifact receipt")
    require(qualified["inputs"]["attempt_driver"]["sha256"] == PINS["rebind_attempt_driver"][1], "attempt driver receipt")
    require(pad["status"] == "COMPLETED_L04_PAD_DOMAIN_CANDIDATE", "pad candidate status")
    require(pad["outputs"]["support_map"]["sha256"] == PINS["pad_support_map"][1], "pad map receipt")
    require(external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0, "pad external guard")
    budget, _recon = load_budget()
    budget.check("fixed-contact current preflight")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_DISABLED_L04_FIXED_CONTACT_CURRENT_PREFLIGHT" if not RUN_RELEASED else "PASS_L04_FIXED_CONTACT_CURRENT_PREFLIGHT",
        "run_released": RUN_RELEASED,
        "record_count": RECORD_COUNT,
        "coincident_group_count": GROUP_COUNT,
        "pins": pins,
        "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)"},
        "scope": "Disabled source-current aggregation only. It will preserve saved currents and residuals without a mesh, R/G/C, solve, field, SQLite query, FMM or magnetic action.",
    }


def load_arrays() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
    with np.load(PINS["qualified_rebind_artifact"][0], allow_pickle=False) as archive:
        rebind = {key: np.asarray(archive[key]) for key in (
            "category", "active_finite_index", "original_finite_index", "original_l04_target_side",
            "source_via_ordinal", "l04_endpoint_is_start", "parent_current_first_to_second_a")}
    with np.load(PINS["contact_inputs"][0], allow_pickle=False) as archive:
        contact = {key: np.asarray(archive[key]) for key in (
            "category", "active_finite_index", "original_finite_index", "expanded_branch_index",
            "original_l04_target_side", "source_via_ordinal", "l04_endpoint_is_start",
            "coincident_group_index", "coincident_group_counts")}
    with np.load(PINS["pad_support_map"][0], allow_pickle=False) as archive:
        pads = {key: np.asarray(archive[key]) for key in (
            "source_pad_support_index", "source_drill_support_index", "pad_support_index", "drill_support_index",
            "pad_support_component_index", "drill_support_component_index", "source_contact_component_index",
            "source_drill_component_index", "category", "active_finite_index", "original_finite_index",
            "expanded_branch_index", "original_l04_target_side", "source_via_ordinal", "l04_endpoint_is_start",
            "coincident_group_index")}
    return rebind, contact, pads


def aggregate() -> tuple[dict[str, np.ndarray], dict[str, object]]:
    rebind, contact, pads = load_arrays()
    for source in (rebind, contact, pads):
        require(all(value.shape == (RECORD_COUNT,) for key, value in source.items() if key not in (
            "pad_support_index", "drill_support_index", "pad_support_component_index", "drill_support_component_index",
            "coincident_group_counts")), "record array shape")
    for key in ("category", "active_finite_index", "original_finite_index", "original_l04_target_side", "source_via_ordinal", "l04_endpoint_is_start"):
        require(np.array_equal(rebind[key], contact[key]) and np.array_equal(rebind[key], pads[key]), f"saved row identity {key}")
    for key in ("category", "active_finite_index", "original_finite_index", "expanded_branch_index", "original_l04_target_side", "source_via_ordinal", "l04_endpoint_is_start", "coincident_group_index"):
        require(np.array_equal(contact[key], pads[key]), f"contact/pad map identity {key}")
    category = rebind["category"].astype(np.int8, copy=False)
    side = rebind["original_l04_target_side"].astype(np.int8, copy=False)
    starts = rebind["l04_endpoint_is_start"].astype(np.bool_, copy=False)
    current = rebind["parent_current_first_to_second_a"].astype(np.complex128, copy=False)
    group = contact["coincident_group_index"].astype(np.int64, copy=False)
    require(np.bincount(category, minlength=3).tolist() == [76_136, 10, 20], "saved category counts")
    group_counts = np.bincount(group, minlength=GROUP_COUNT).astype(np.int64, copy=False)
    saved_group_counts = contact["coincident_group_counts"].astype(np.int64, copy=False)
    sizes, size_counts = np.unique(group_counts, return_counts=True)
    require(np.all((group >= 0) & (group < GROUP_COUNT)) and np.array_equal(group_counts, saved_group_counts), "saved coincident-group counts")
    require(np.array_equal(sizes, np.asarray([1, 2])) and np.array_equal(size_counts, np.asarray([390, 37_888])), "saved group-size histogram")
    require(np.all((side[category < 2] == 0) | (side[category < 2] == 1)), "category 0/1 target side")
    outward = np.empty(RECORD_COUNT, dtype=np.complex128)
    ordinary = category < 2
    hidden = category == 2
    outward[ordinary] = np.where(side[ordinary] == 0, current[ordinary], -current[ordinary])
    outward[hidden] = np.where(starts[hidden], current[hidden], -current[hidden])
    require(np.all(np.isfinite(outward.real)) and np.all(np.isfinite(outward.imag)), "finite saved outward currents")
    hidden_groups = np.unique(group[hidden])
    require(len(hidden_groups) == 10 and np.array_equal(np.bincount(group[hidden], minlength=GROUP_COUNT)[hidden_groups], np.full(10, 2)), "category-2 group pairs")
    for value in hidden_groups:
        pair = outward[group == value]
        require(pair.shape == (2,) and pair[0] + pair[1] == 0.0j, "category-2 bitwise opposite pair")
    expected = json.loads(PINS["qualified_rebind_result"][0].read_bytes())["native_l04_boundary_current"]
    expected_signed = complex(*expected["signed_outward_sum_a"])
    expected_absolute = float(expected["absolute_incident_sum_a"])
    boundary = outward[ordinary]
    signed = deterministic_sum(boundary)
    absolute = math.fsum(float(abs(value)) for value in boundary)
    envelope = deterministic_envelope(boundary)
    require(abs(signed - expected_signed) <= envelope and abs(absolute - expected_absolute) <= envelope, "qualification02 boundary current total")
    group_current = grouped_sum(outward, group, GROUP_COUNT)
    pad_map = pads["source_pad_support_index"].astype(np.int64, copy=False)
    drill_map = pads["source_drill_support_index"].astype(np.int64, copy=False)
    component = pads["source_contact_component_index"].astype(np.int64, copy=False)
    drill_component = pads["source_drill_component_index"].astype(np.int64, copy=False)
    group_pad = np.empty(GROUP_COUNT, dtype=np.int32)
    group_drill = np.empty(GROUP_COUNT, dtype=np.int32)
    group_component = np.empty(GROUP_COUNT, dtype=np.int32)
    indptr, indices = group_rows(group)
    for value in range(GROUP_COUNT):
        rows = indices[indptr[value]:indptr[value + 1]]
        require(len(np.unique(pad_map[rows])) == 1 and len(np.unique(drill_map[rows])) == 1, "group has unique pad/drill support")
        require(len(np.unique(component[rows])) == 1 and np.array_equal(component[rows], drill_component[rows]), "group has one material component")
        group_pad[value] = pad_map[rows[0]]
        group_drill[value] = drill_map[rows[0]]
        group_component[value] = component[rows[0]]
    require(np.array_equal(np.sort(group_pad), np.sort(pads["pad_support_index"])) and np.array_equal(np.sort(group_drill), np.sort(pads["drill_support_index"])), "group/support bijections")
    component_count = int(component.max()) + 1
    physical_component_current = grouped_sum(outward, component, component_count)
    replay_component_current = grouped_sum(group_current, group_component.astype(np.int64), component_count)
    aggregation_difference = replay_component_current - physical_component_current
    aggregation_envelope = 32.0 * np.finfo(np.float64).eps * max(
        1.0, float(np.abs(outward).sum()) + float(np.abs(group_current).sum())
    )
    require(np.all(np.abs(aggregation_difference) <= aggregation_envelope), "component aggregation replay roundoff")
    arrays = {
        "schema_version": np.asarray([1], dtype=np.int64),
        "record_current_outward_from_l04_sheet_a": outward,
        "coincident_group_current_outward_from_l04_sheet_a": group_current,
        "physical_component_source_outward_sum_a": physical_component_current,
        "component_aggregation_replay_outward_sum_a": replay_component_current,
        "component_aggregation_replay_difference_a": aggregation_difference,
        "coincident_group_row_indptr": indptr,
        "coincident_group_row_indices": indices,
        "group_to_pad_support_index": group_pad,
        "group_to_drill_support_index": group_drill,
        "group_to_component_index": group_component,
        "coincident_group_index": group,
        "source_contact_row_index": np.arange(RECORD_COUNT, dtype=np.int64),
        "source_record_index": np.arange(RECORD_COUNT, dtype=np.int64),
        "parent_current_first_to_second_a": current,
        "source_via_ordinal": rebind["source_via_ordinal"].astype(np.int64, copy=False),
        "original_l04_target_side": side,
        "l04_endpoint_is_start": starts,
        "category": category,
        "active_finite_index": contact["active_finite_index"].astype(np.int64, copy=False),
        "original_finite_index": contact["original_finite_index"].astype(np.int64, copy=False),
        "expanded_branch_index": contact["expanded_branch_index"].astype(np.int64, copy=False),
    }
    summary = {
        "category_counts": np.bincount(category, minlength=3).astype(int).tolist(),
        "category2_group_count": int(len(hidden_groups)),
        "coincident_group_size_histogram": {str(int(size)): int(count) for size, count in zip(sizes, size_counts, strict=True)},
        "qualification02_boundary_signed_outward_sum_a": complex_pair(signed),
        "qualification02_boundary_absolute_incident_sum_a": float(absolute),
        "deterministic_summation_envelope_a": float(envelope),
        "component_count": component_count,
        "physical_component_source_outward_sum_a": [complex_pair(value) for value in physical_component_current],
        "component_aggregation_replay_difference_max_abs_a": float(np.abs(aggregation_difference).max(initial=0.0)),
        "component_aggregation_replay_roundoff_envelope_a": float(aggregation_envelope),
    }
    return arrays, summary


def worker(output: Path) -> dict[str, object]:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release this source-current aggregation")
    require(output.is_dir() and not (output / "result.json").exists(), "fresh worker output")
    driver = output / "driver-at-run.py"
    require(driver.is_file() and sha256(driver) == sha256(Path(__file__)), "frozen current helper changed")
    budget, recon = load_budget()
    try:
        report = preflight()
        arrays, summary = aggregate()
        budget.check("fixed contact-current aggregation")
        artifact = output / "l04-fixed-contact-currents.npz"
        atomic_npz(artifact, **arrays)
        budget.check("fixed contact-current checkpoint")
        result = {**report, "status": "COMPLETED_L04_FIXED_CONTACT_CURRENTS_SOURCE_AGGREGATION", "driver": receipt(driver),
                  "artifact": receipt(artifact), "aggregation": summary,
                  "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)"},
                  "scope": "Saved current orientation and coincident-group aggregation only. A later RT0 Bq electrode-incidence RHS uses the negative of coincident_group_current_outward_from_l04_sheet_a. Physical component source-outward sums are preserved separately from aggregation replay roundoff differences. No current is corrected, redistributed, solved or promoted to a field."}
        recon._atomic_exclusive_json(output / "result.json", result)
        return result
    except BaseException as error:
        recon._atomic_exclusive_json(output / "failure.json", {"program": PROGRAM, "version": VERSION,
            "status": "STOP_L04_FIXED_CONTACT_CURRENTS_SOURCE_AGGREGATION", "error_type": type(error).__name__,
            "error": str(error), "driver": receipt(driver), "budget": budget.receipt()})
        raise


def self_check() -> None:
    report = preflight()
    record = np.asarray([1 + 2j, 1 + 2j, 3 - 1j], dtype=np.complex128)
    category = np.asarray([0, 1, 2], dtype=np.int8)
    side = np.asarray([0, 1, 0], dtype=np.int8)
    starts = np.asarray([False, False, True])
    outward = np.where(category < 2, np.where(side == 0, record, -record), np.where(starts, record, -record))
    require(np.array_equal(outward, np.asarray([1 + 2j, -1 - 2j, 3 - 1j])), "orientation self check")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_L04_FIXED_CONTACT_CURRENT_STATIC_SELF_CHECK",
                      "preflight_status": report["status"], "run_released": RUN_RELEASED}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    require(sum((args.self_check, args.preflight, args.run)) == 1, "select exactly one mode")
    output = (args.output or R / (PREFLIGHT_NAME if args.preflight else OUTPUT_NAME)).resolve()
    if args.self_check:
        self_check()
    elif args.preflight:
        require(not output.exists(), "refusing preflight overwrite")
        output.mkdir(parents=True)
        driver = output / "driver-at-run.py"
        driver.write_bytes(Path(__file__).read_bytes())
        report = preflight()
        report["driver"] = receipt(driver)
        atomic_json(output / "preflight.json", report)
        print(json.dumps({"status": report["status"], "receipt": receipt(output / "preflight.json")}, sort_keys=True))
    else:
        require(RUN_RELEASED, "RUN_RELEASED=False; source review must release --run")
        require(not output.exists(), "refusing existing output")
        output.mkdir(parents=True)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        print(json.dumps({"status": worker(output)["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
