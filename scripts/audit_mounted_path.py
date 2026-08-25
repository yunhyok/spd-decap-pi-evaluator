"""Read-only W7 audit of the frozen mounted-path comparison evidence.

This tool is deliberately diagnostic. A structurally valid result never assigns
an exclusive physical owner and therefore always exits with status 2.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if not (_SRC_ROOT / "spd_decap_pi").is_dir():
    raise RuntimeError("audit must run from the active SPD Decap PI Evaluator checkout")
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import spd_decap_pi as _ACTIVE_PACKAGE

_PACKAGE_FILE = Path(_ACTIVE_PACKAGE.__file__).resolve()
if _SRC_ROOT not in _PACKAGE_FILE.parents:
    raise RuntimeError("audit imported spd_decap_pi outside the active checkout")

from spd_decap_pi._core.models import parse_passive_subcircuit
from spd_decap_pi.scenario_io import ScenarioFormatError, _manifest_hash, _safe_attachment_name, _validate_archive

_BASE = Path(__file__).resolve().parent / "benchmark_raw_spd_powersi_correlation.py"
_SPEC = importlib.util.spec_from_file_location("_w7_benchmark", _BASE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("benchmark module is unavailable")
_BENCHMARK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCHMARK)

LOADED_RAILS = tuple(_BENCHMARK.LOADED_FINAL_HOLDOUT_RAILS)
FIXED_GRID = _BENCHMARK.fixed_grid
RESONANCE_CANDIDATES = _BENCHMARK._resonance_candidates
CROSSINGS = _BENCHMARK._crossings

EXPECTED_SOURCE = {"basename": "S4LB002-2Para_260729_1_injected.spd", "size_bytes": 1116717287, "sha256": "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"}
EXPECTED_CANDIDATE = {"basename": "S4LB002-2Para_260729_1_injected_candidate.spdpi", "size_bytes": 796205663, "sha256": "8b02836c03aa38c447fba37ddd30434a3e4ed34ce772654fa5bc3a8512543320"}
EXPECTED_IMPORT = {"basename": "import_save_validation_report.json", "sha256": "5a2714c7ce0d90df6cc9c4155c8f5ef43b78802cec47872d53f1c8361b4261a3"}
EXPECTED_CORRELATION = {"basename": "correlation_report.json", "sha256": "969e40046e3a099d09557ba7500693460362336d76b067962436bd3b5177abc4"}
SELECTED_INVESTIGATION_BLOCK = "terminal_landing_spatial_core"
_OMISSIONS = ("secondary-layer lateral spreading", "via mutual", "pad/antipad exact-core")
_PAIR_FAMILIES = ("ADC_VDD_055_VTRIP", "ADC_VDD_070_VINT", "ADC_VDD_075_VCPU")
_ANCHORS_HZ = (1.0e5, 1.0e6, 1.0e7, 1.0e8)


class IntegrityError(ValueError):
    """A global trust-boundary failure; no partial output is published."""


def _pairs(pairs: list[tuple[str, Any]], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError(f"{label}: duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=lambda pairs: _pairs(pairs, label), parse_constant=lambda value: (_ for _ in ()).throw(IntegrityError(f"{label}: non-finite JSON value {value}")))
    except IntegrityError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"{label}: invalid JSON") from exc


def _file_identity(path: Path, expected: Mapping[str, Any], label: str) -> dict[str, Any]:
    if path.name != expected.get("basename") or not path.is_file():
        raise IntegrityError(f"{label}: basename/file mismatch")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    actual = digest.hexdigest()
    if "size_bytes" in expected and size != expected["size_bytes"]:
        raise IntegrityError(f"{label}: size mismatch")
    if actual != expected.get("sha256"):
        raise IntegrityError(f"{label}: SHA-256 mismatch")
    return {"basename": path.name, "size_bytes": size, "sha256": actual}


def _json_file(path: Path, expected: Mapping[str, Any], label: str) -> tuple[Any, dict[str, Any]]:
    identity = _file_identity(path, expected, label)
    return _strict_json(path.read_bytes(), label), identity


def _read_scenario(stream: Any, *, expected_size: int, expected_sha256: str) -> list[dict[str, Any]]:
    """Hash once and capture only a top-level object ``decaps`` array."""
    digest = hashlib.sha256()
    total = 0
    stack: list[int] = []
    quoted = False
    escaped = False
    token = bytearray()
    last_string: str | None = None
    capture_stack: list[int] | None = None
    capture = bytearray()
    decoded: list[dict[str, Any]] | None = None
    awaiting_decaps_array = False

    def finish_capture() -> None:
        nonlocal decoded, capture_stack
        value = _strict_json(bytes(capture), "candidate decaps")
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise IntegrityError("candidate: decaps is not an object array")
        decoded = value
        capture_stack = None

    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
        total += len(block)
        for byte in block:
            if capture_stack is not None:
                capture.append(byte)
                if quoted:
                    if escaped:
                        escaped = False
                    elif byte == 92:
                        escaped = True
                    elif byte == 34:
                        quoted = False
                    continue
                if byte == 34:
                    quoted = True
                elif byte in (91, 123):
                    capture_stack.append(byte)
                elif byte in (93, 125):
                    if not capture_stack:
                        raise IntegrityError("candidate: decaps bracket underflow")
                    opening = capture_stack.pop()
                    if (opening, byte) not in ((91, 93), (123, 125)):
                        raise IntegrityError("candidate: decaps bracket mismatch")
                    if not capture_stack:
                        finish_capture()
                if len(capture) > 256 * 1024 * 1024:
                    raise IntegrityError("candidate: decaps fragment exceeds audit limit")
                continue
            if awaiting_decaps_array:
                if byte in b" \t\r\n":
                    continue
                if byte != 91:
                    raise IntegrityError("candidate: top-level decaps value is not an array")
                capture_stack = [91]
                capture = bytearray(b"[")
                awaiting_decaps_array = False
                continue
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
                    try:
                        last_string = token.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise IntegrityError("candidate: invalid scenario string") from exc
                    token.clear()
                else:
                    token.append(byte)
                continue
            if byte == 34:
                quoted = True
                token.clear()
                continue
            if byte == 58 and len(stack) == 1 and last_string == "decaps":
                if decoded is not None:
                    raise IntegrityError("candidate: duplicate top-level decaps key")
                awaiting_decaps_array = True
                last_string = None
                continue
            if byte in (91, 123):
                stack.append(byte)
                last_string = None
                continue
            if byte in (93, 125):
                if not stack:
                    raise IntegrityError("candidate: scenario bracket underflow")
                stack.pop()
                last_string = None
                continue
            if byte not in b" \t\r\n":
                last_string = None
    if capture_stack is not None or awaiting_decaps_array or decoded is None:
        raise IntegrityError("candidate: top-level decaps is missing or incomplete")
    if total != expected_size or digest.hexdigest() != expected_sha256:
        raise IntegrityError("candidate: scenario identity mismatch")
    return decoded


def _candidate_snapshot(path: Path, identity: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        with ZipFile(path) as archive:
            try:
                manifest, by_name = _validate_archive(archive)
            except ScenarioFormatError as exc:
                raise IntegrityError(f"candidate archive: {exc}") from exc
            if not isinstance(manifest, Mapping):
                raise IntegrityError("candidate: manifest is not an object")
            scenario_file = manifest.get("scenario_file")
            scenario_size = manifest.get("scenario_size")
            scenario_sha = manifest.get("scenario_sha256")
            if (manifest.get("format") != "spd-decap-pi-scenario" or manifest.get("format_version") != 1 or manifest.get("schema_version") != "0.1" or manifest.get("app_version") != "0.23.0" or manifest.get("raw_spd_embedded") is not False or scenario_file != "scenario.json" or type(scenario_size) is not int or not isinstance(scenario_sha, str)):
                raise IntegrityError("candidate: canonical scenario identity is missing")
            entries = manifest.get("attachments")
            if not isinstance(entries, list):
                raise IntegrityError("candidate: attachments are missing")
            declared: dict[str, Mapping[str, Any]] = {}
            folded_declared: set[str] = set()
            for entry in entries:
                if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("path"), str):
                    raise IntegrityError("candidate: attachment declaration is malformed")
                logical_name = entry["name"]
                name = entry["path"]
                try:
                    canonical_name = _safe_attachment_name(logical_name)
                except ScenarioFormatError as exc:
                    raise IntegrityError(f"candidate: unsafe attachment name {logical_name!r}") from exc
                if canonical_name != logical_name or logical_name.casefold() in folded_declared or name.casefold() in folded_declared or name != f"attachments/{logical_name}":
                    raise IntegrityError("candidate: duplicate attachment declaration")
                if type(entry.get("size")) is not int or entry["size"] < 0 or not isinstance(entry.get("sha256"), str):
                    raise IntegrityError("candidate: attachment identity is malformed")
                try:
                    canonical_hash = _manifest_hash(entry["sha256"], label="attachment SHA-256")
                except ScenarioFormatError as exc:
                    raise IntegrityError("candidate: attachment SHA-256 is malformed") from exc
                if entry["sha256"] != canonical_hash:
                    raise IntegrityError("candidate: attachment SHA-256 is malformed")
                folded_declared.update((logical_name.casefold(), name.casefold()))
                declared[name] = entry
            if set(by_name) - {"manifest.json", "scenario.json"} != set(declared):
                raise IntegrityError("candidate: declared ZIP member set mismatch")
            scenario_info = by_name["scenario.json"]
            if scenario_info.file_size != scenario_size:
                raise IntegrityError("candidate: scenario size declaration mismatch")
            decaps = _read_scenario(archive.open("scenario.json"), expected_size=scenario_size, expected_sha256=scenario_sha)
            return decaps, {"identity": dict(identity), "manifest": manifest, "attachments": declared}
    except (BadZipFile, KeyError, OSError) as exc:
        raise IntegrityError("candidate: invalid ZIP archive") from exc


def _require_identity(value: Any, expected: Mapping[str, Any], label: str) -> None:
    if not isinstance(value, Mapping) or any(value.get(k) != expected[k] for k in ("basename", "size_bytes", "sha256")):
        raise IntegrityError(f"{label}: identity mismatch")


def _validate_import(report: Mapping[str, Any], candidate_identity: Mapping[str, Any]) -> None:
    if report.get("schema_version") != "candidate-import-save-validation-v1" or report.get("status") != "passed" or report.get("report_version") != 1 or report.get("app_version") != "0.23.0" or report.get("mode") != "import_save_only":
        raise IntegrityError("import report: status/mode mismatch")
    _require_identity(report.get("source"), EXPECTED_SOURCE, "import source")
    candidate = report.get("candidate_bundle")
    _require_identity(candidate, candidate_identity, "import candidate")
    if candidate.get("reuse_requested") is not False:
        raise IntegrityError("import report: reuse was requested")
    imported = report.get("import")
    binding = report.get("candidate_import_report_binding")
    if not isinstance(imported, Mapping) or imported.get("mode") != "fresh_import" or not isinstance(binding, Mapping):
        raise IntegrityError("import report: fresh-import binding missing")
    if binding.get("status") != "validated_same_process_fresh_import" or binding.get("fresh_import_mode") is not True:
        raise IntegrityError("import report: fresh-import binding invalid")
    if report.get("frequency_solves_executed") != 0 or report.get("touchstone_read") is not False:
        raise IntegrityError("import report: unexpected frequency/touchstone work")
    atomic = report.get("atomic_save_load_validation")
    required_atomic = ("archive_manifest_and_member_hashes_validated", "scenario_schema_validated_after_reload", "source_identity_validated_after_reload", "attachment_hashes_validated_after_reload")
    if not isinstance(atomic, Mapping) or any(atomic.get(key) is not True for key in required_atomic):
        raise IntegrityError("import report: atomic save/load validation is incomplete")


def _peak_frequency(value: Any, label: str) -> float:
    if not isinstance(value, list) or not value or not isinstance(value[0], Mapping):
        raise IntegrityError(f"correlation: {label} peak is missing")
    frequency = value[0].get("frequency_hz")
    if not isinstance(frequency, (int, float)) or not math.isfinite(float(frequency)) or float(frequency) <= 0:
        raise IntegrityError(f"correlation: {label} peak is malformed")
    return float(frequency)


def _crossing(value: Any, label: str) -> float:
    if not isinstance(value, list) or not value:
        raise IntegrityError(f"correlation: {label} crossing is missing")
    number = value[0]
    if not isinstance(number, (int, float)) or not math.isfinite(float(number)) or float(number) <= 0:
        raise IntegrityError(f"correlation: {label} crossing is malformed")
    return float(number)


def _anchor(metrics: Mapping[str, Any], hz: float) -> Mapping[str, float]:
    key = f"{hz / 1e6:g}MHz"
    anchors = metrics.get("anchors")
    row = anchors.get(key) if isinstance(anchors, Mapping) else None
    if not isinstance(row, Mapping):
        raise IntegrityError(f"correlation: anchor {key} is missing")
    result: dict[str, float] = {}
    for field in ("model_magnitude_ohm", "reference_magnitude_ohm", "signed_magnitude_error_db"):
        value = row.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or (field != "signed_magnitude_error_db" and float(value) <= 0):
            raise IntegrityError(f"correlation: anchor {key} {field} is malformed")
        result[field] = float(value)
    return result


def _rail_provenance(row: Mapping[str, Any], baseline: dict[str, str]) -> list[str]:
    failures: list[str] = []
    provenance = row.get("solver_provenance")
    if not isinstance(provenance, Mapping):
        return ["solver provenance missing"]
    if provenance.get("scenario_raw_via_owner_exact_once") is not True:
        failures.append("scenario_raw_via_owner_exact_once")
    if provenance.get("scenario_retarget_route_count") != 0 or provenance.get("scenario_suppressed_base_cut_count") != 0:
        failures.append("retarget/suppressed route counts")
    if provenance.get("terminal_surface_contact_proof_status") != "proven" or provenance.get("terminal_artwork_proof_status") != "proven":
        failures.append("terminal proof")
    if provenance.get("source_trace_width_available") is not False or provenance.get("finite_trace_rl_link_count") != 0:
        failures.append("trace R/L omission")
    scope = provenance.get("spatial_scope")
    if not isinstance(scope, str):
        failures.append("spatial scope missing")
    else:
        failures.extend(f"spatial omission: {text}" for text in _OMISSIONS if text not in scope)
    for key in ("scenario_plan_sha256", "termination_manifest_sha256"):
        value = provenance.get(key)
        if not isinstance(value, str) or not value:
            failures.append(key)
        elif baseline.setdefault(key, value) != value:
            failures.append(f"identity drift: {key}")
    cap_count = provenance.get("scenario_cap_body_count")
    via_count = provenance.get("finite_via_rl_link_count")
    selected_clusters = provenance.get("termination_active_selected_rail_cluster_count")
    if type(cap_count) is not int or cap_count <= 0 or type(via_count) is not int or via_count <= 0:
        failures.append("cap/via counts")
    if type(selected_clusters) is not int or selected_clusters <= 0:
        failures.append("termination selected cluster count")
    return failures


def _cap_summary(selected: list[dict[str, Any]], models: Mapping[str, np.ndarray], rail_rows: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    grid = FIXED_GRID()
    counts = {rail: Counter(str(item.get("model_id")) for item in selected if item.get("current_rail_id") == rail) for rail in LOADED_RAILS}
    for rail in LOADED_RAILS:
        if not counts[rail]:
            failures.append(f"{rail}: active cap mix missing")
    for family in _PAIR_FAMILIES:
        if counts.get(f"{family}/0") != counts.get(f"{family}/1"):
            failures.append(f"{family}: /0-/1 cap mix differs")
    summary: dict[str, Any] = {}
    for rail in LOADED_RAILS:
        admittance = np.zeros(grid.size, dtype=np.complex128)
        for model_id, count in counts[rail].items():
            values = models.get(model_id)
            if values is None:
                failures.append(f"{rail}: cap model is unavailable")
                continue
            if not np.all(np.isfinite(values.real)) or not np.all(np.isfinite(values.imag)) or np.any(values == 0):
                failures.append(f"{rail}: cap impedance is nonfinite/zero")
                continue
            admittance += count / values
        if not np.all(np.isfinite(admittance.real)) or not np.all(np.isfinite(admittance.imag)) or np.any(admittance == 0):
            failures.append(f"{rail}: cap admittance is nonfinite/zero")
            continue
        impedance = 1.0 / admittance
        peaks = RESONANCE_CANDIDATES(grid, impedance)
        crossings = CROSSINGS(grid, impedance.imag)
        metrics = rail_rows[rail].get("metrics", {})
        resonances = metrics.get("resonances", {}) if isinstance(metrics, Mapping) else {}
        model_peak = _peak_frequency(resonances.get("model_local_peaks"), f"{rail} model")
        model_index = int(np.argmin(np.abs(np.log(grid / model_peak))))
        if not peaks or abs(int(np.argmin(np.abs(np.log(grid / peaks[0]["frequency_hz"])))) - model_index) > 1:
            failures.append(f"{rail}: cap first peak bin mismatch")
        summary[rail] = {"model_counts": dict(sorted(counts[rail].items())), "total": int(sum(counts[rail].values())), "first_peak": peaks[0] if peaks else None, "imaginary_zero_crossings_hz": crossings, "anchor_impedance_ohm": {f"{hz / 1e6:g}MHz": {"real": float(impedance[int(np.argmin(np.abs(np.log(grid / hz))))].real), "imag": float(impedance[int(np.argmin(np.abs(np.log(grid / hz))))].imag)} for hz in _ANCHORS_HZ}}
    return summary, failures


def _audit_diagnostics(decaps: list[dict[str, Any]], correlation: Mapping[str, Any], models: Mapping[str, np.ndarray]) -> dict[str, Any]:
    failures: list[str] = []
    mode = correlation.get("runs", {}).get("candidate", {}).get("10", {})
    rail_rows = mode.get("rails") if isinstance(mode, Mapping) else None
    if not isinstance(rail_rows, Mapping):
        raise IntegrityError("correlation: mode10 rails are missing")
    baseline: dict[str, str] = {}
    active_counts = {rail: sum(1 for item in decaps if item.get("current_rail_id") == rail and item.get("enabled") is True) for rail in LOADED_RAILS}
    if any(count <= 0 for count in active_counts.values()):
        failures.append("active loaded decap count missing")
    rails_out: dict[str, Any] = {}
    for rail in LOADED_RAILS:
        row = rail_rows.get(rail)
        if not isinstance(row, Mapping) or row.get("status") != "completed":
            failures.append(f"{rail}: mode10 incomplete")
            continue
        provenance_failures = _rail_provenance(row, baseline)
        failures.extend(f"{rail}: {failure}" for failure in provenance_failures)
        metrics = row.get("metrics")
        if not isinstance(metrics, Mapping):
            failures.append(f"{rail}: metrics missing")
            continue
        anchors = {str(hz): _anchor(metrics, hz) for hz in _ANCHORS_HZ}
        signed = {key: value["signed_magnitude_error_db"] for key, value in anchors.items()}
        if not (-1.0 <= signed["100000.0"] < 0.0 and signed["1000000.0"] < signed["100000.0"] and signed["10000000.0"] < signed["1000000.0"] and signed["100000000.0"] < signed["1000000.0"]):
            failures.append(f"{rail}: signed anchor ordering")
        resonances = metrics.get("resonances")
        if not isinstance(resonances, Mapping):
            raise IntegrityError(f"correlation: {rail} resonance data missing")
        model_peak = _peak_frequency(resonances.get("model_local_peaks"), f"{rail} model")
        reference_peak = _peak_frequency(resonances.get("reference_local_peaks"), f"{rail} reference")
        model_crossing = _crossing(resonances.get("model_imaginary_zero_crossings_hz"), f"{rail} model")
        reference_crossing = _crossing(resonances.get("reference_imaginary_zero_crossings_hz"), f"{rail} reference")
        if model_crossing <= reference_crossing:
            failures.append(f"{rail}: first imaginary crossing")
        provenance = row["solver_provenance"]
        rails_out[rail] = {"active_decap_count": active_counts[rail], "signed_anchor_error_db": signed, "candidate_first_peak_hz": model_peak, "reference_first_peak_hz": reference_peak, "candidate_first_crossing_hz": model_crossing, "reference_first_crossing_hz": reference_crossing, "provenance_failures": provenance_failures, "mounted_path_provenance": {key: provenance.get(key) for key in ("scenario_cap_body_count", "finite_via_rl_link_count", "scenario_raw_via_owner_exact_once", "scenario_retarget_route_count", "scenario_suppressed_base_cut_count", "terminal_surface_contact_proof_status", "terminal_artwork_proof_status", "finite_trace_rl_link_count", "source_trace_width_available", "spatial_scope")}}
    for family in _PAIR_FAMILIES:
        left = rail_rows.get(f"{family}/0", {}).get("solver_provenance", {}).get("termination_active_selected_rail_cluster_count")
        right = rail_rows.get(f"{family}/1", {}).get("solver_provenance", {}).get("termination_active_selected_rail_cluster_count")
        if type(left) is not int or type(right) is not int or left <= 0 or right <= 0 or left != right:
            failures.append(f"{family}: termination selected cluster mismatch")
    cap_only, cap_failures = _cap_summary([item for item in decaps if item.get("current_rail_id") in LOADED_RAILS and item.get("enabled") is True], models, rail_rows)
    failures.extend(cap_failures)
    pair_deltas: dict[str, Any] = {}
    for family in _PAIR_FAMILIES:
        candidate_deltas: list[float] = []
        reference_deltas: list[float] = []
        for hz in _ANCHORS_HZ:
            left = _anchor(rail_rows[f"{family}/0"]["metrics"], hz)
            right = _anchor(rail_rows[f"{family}/1"]["metrics"], hz)
            candidate_deltas.append(20.0 * math.log10(left["model_magnitude_ohm"] / right["model_magnitude_ohm"]))
            reference_deltas.append(20.0 * math.log10(left["reference_magnitude_ohm"] / right["reference_magnitude_ohm"]))
        candidate_rms = math.sqrt(sum(value * value for value in candidate_deltas) / len(candidate_deltas))
        reference_rms = math.sqrt(sum(value * value for value in reference_deltas) / len(reference_deltas))
        if candidate_rms >= reference_rms:
            failures.append(f"{family}: candidate site RMS is not smaller")
        pair_deltas[family] = {"candidate_db": candidate_deltas, "reference_db": reference_deltas, "candidate_rms_db": candidate_rms, "reference_rms_db": reference_rms}
    cluster_counts = {family: int(rail_rows[f"{family}/0"]["solver_provenance"]["termination_active_selected_rail_cluster_count"]) for family in _PAIR_FAMILIES}
    return {"selected_investigation_block": SELECTED_INVESTIGATION_BLOCK if not failures else None, "rails": rails_out, "active_loaded_decap_counts": active_counts, "termination_active_selected_rail_cluster_count": cluster_counts, "site_pair_deltas_db": pair_deltas, "cap_only": cap_only, "diagnostic_failures": failures, "causal_owner": None, "owner_status": "unclassified", "cap_body_accuracy": "N/A", "terminal_via_numeric_accuracy": "N/A", "quantitative_subterm_allocation": "N/A", "exclusive_causality_unproven": True}


def audit(candidate: Path, import_report: Path, correlation_report: Path) -> dict[str, Any]:
    candidate_identity = _file_identity(candidate, EXPECTED_CANDIDATE, "candidate")
    import_json, import_identity = _json_file(import_report, EXPECTED_IMPORT, "import report")
    correlation, correlation_identity = _json_file(correlation_report, EXPECTED_CORRELATION, "correlation report")
    if not isinstance(import_json, Mapping) or not isinstance(correlation, Mapping):
        raise IntegrityError("reports must be JSON objects")
    _validate_import(import_json, candidate_identity)
    _require_identity(correlation.get("source"), EXPECTED_SOURCE, "correlation source")
    _require_identity(correlation.get("candidate_bundle"), candidate_identity, "correlation candidate")
    binding = correlation.get("candidate_import_report_binding")
    if not isinstance(binding, Mapping) or binding.get("basename") != import_identity["basename"] or binding.get("sha256") != import_identity["sha256"] or binding.get("status") != "validated" or binding.get("fresh_import_mode") is not True:
        raise IntegrityError("correlation/import report binding mismatch")
    decaps, candidate_meta = _candidate_snapshot(candidate, candidate_identity)
    selected = [item for item in decaps if item.get("current_rail_id") in LOADED_RAILS and item.get("enabled") is True]
    if not selected:
        raise IntegrityError("candidate has no active loaded decaps")
    for item in selected:
        if item.get("source_mounted") is not True or item.get("pad_state") != "NORMAL" or not isinstance(item.get("model_id"), str) or not item["model_id"]:
            raise IntegrityError("candidate: selected decap scope is malformed")
        if item.get("current_rail_id") != item.get("source_rail_id") or item.get("current_net") != item.get("source_net"):
            raise IntegrityError("candidate: selected decap source scope mismatch")
    model_ids = sorted({str(item.get("model_id")) for item in selected})
    if len(model_ids) != 3:
        raise IntegrityError("candidate: expected exactly three selected cap models")
    grid = FIXED_GRID()
    models: dict[str, np.ndarray] = {}
    with ZipFile(candidate) as archive:
        for model_id in model_ids:
            names = [name for name in candidate_meta["attachments"] if name.startswith("attachments/cap_models/")]
            exact = [name for name in names if Path(name).stem == model_id]
            matches = exact if exact else [name for name in names if Path(name).stem.startswith(model_id + "-")]
            if len(matches) != 1:
                raise IntegrityError(f"candidate: selected cap model attachment missing for {model_id}")
            name = matches[0]
            entry = candidate_meta["attachments"][name]
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            with archive.open(name) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    chunks.append(chunk)
            raw = b"".join(chunks)
            if len(raw) != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise IntegrityError(f"candidate: selected cap model hash mismatch for {name}")
            model = parse_passive_subcircuit(raw.decode("utf-8-sig"), subckt_name=model_id, source_name=name)
            values = np.asarray(model.impedance(grid), dtype=np.complex128)
            if values.shape != grid.shape:
                raise IntegrityError(f"candidate: selected cap model grid mismatch for {model_id}")
            models[model_id] = values
    diagnostics = _audit_diagnostics(decaps, correlation, models)
    diagnostics.update({"tool": "SPD Decap PI Evaluator v0.23.0", "schema": "powersi-mounted-path-audit-v1", "status": "diagnostic_fail" if diagnostics["diagnostic_failures"] else "diagnostic_pass", "source": dict(EXPECTED_SOURCE), "candidate": candidate_identity, "import_report": import_identity, "correlation_report": correlation_identity, "selected_cap_count": len(selected), "selected_loaded_rail_count": len(LOADED_RAILS)})
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--import-report", type=Path, required=True)
    parser.add_argument("--correlation-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = audit(args.candidate, args.import_report, args.correlation_report)
    except IntegrityError as exc:
        print(f"integrity failure: {exc}", file=sys.stderr)
        return 2
    try:
        with args.output.open("x", encoding="utf-8", newline="") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (FileExistsError, OSError) as exc:
        print(f"output publish failure: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
