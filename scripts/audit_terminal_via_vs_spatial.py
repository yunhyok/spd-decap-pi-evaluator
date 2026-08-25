"""Read-only, hash-bound W7 terminal-Via versus spatial owner audit.

This audit preserves source-bound Via inventory but never promotes an owner or
authorizes physics changes.  A structurally valid result therefore exits 2.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if not (_SRC_ROOT / "spd_decap_pi").is_dir():
    raise RuntimeError("audit must run from the active SPD Decap PI Evaluator checkout")
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import spd_decap_pi as _ACTIVE_PACKAGE

if _SRC_ROOT not in Path(_ACTIVE_PACKAGE.__file__).resolve().parents:
    raise RuntimeError("audit imported spd_decap_pi outside the active checkout")

from spd_decap_pi._core.domain import RailSpec, StackupLayer
from spd_decap_pi.evaluation import _source_terminal_estimate
from spd_decap_pi.scenario import (
    DecapConnectionKind,
    ScenarioDecap,
    SharedPadConnectionAnalysis,
    derive_shared_pad_current_components,
)

_MOUNTED_PATH = Path(__file__).with_name("audit_mounted_path.py")
_SPEC = importlib.util.spec_from_file_location("_w7_mounted_path", _MOUNTED_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("mounted-path audit module is unavailable")
_MOUNTED = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOUNTED)

LOADED_RAILS = tuple(_MOUNTED.LOADED_RAILS)
EXPECTED_SOURCE = dict(_MOUNTED.EXPECTED_SOURCE)
EXPECTED_CANDIDATE = dict(_MOUNTED.EXPECTED_CANDIDATE)
EXPECTED_IMPORT = dict(_MOUNTED.EXPECTED_IMPORT)
EXPECTED_CORRELATION = dict(_MOUNTED.EXPECTED_CORRELATION)
EXPECTED_PREVIOUS_W7 = {
    "basename": "mounted_path_audit.json",
    "size_bytes": 15796,
    "sha256": "e3c28144b4578c6d17846b70bc345ffab634eb58c9faa698401b9bffcc663ceb",
}
SCHEMA = "powersi-terminal-via-vs-spatial-audit-v1"
TOOL = "SPD Decap PI Evaluator v0.23.0"
SELECTED_BLOCK = "terminal_landing_spatial_core"
_FRAGMENT_LIMITS = {
    ("decaps",): 256 * 1024 * 1024,
    ("connection_analysis",): 512 * 1024 * 1024,
    ("normalized_project", "rails"): 16 * 1024 * 1024,
    ("normalized_project", "stackup_layers"): 16 * 1024 * 1024,
}
EXPECTED_SELECTED_COUNT = 8986
EXPECTED_PREVIOUS_SELECTED_COUNT = 8986
_READ_BUDGET = {
    "candidate_outer_sha_sequential": 1,
    "zip_central_manifest": 1,
    "scenario_streaming_pass": 1,
    "small_json_each_max": 1,
}


class IntegrityError(ValueError):
    """Global trust-boundary error; output must not be published."""


def _strict_json(raw: bytes, label: str) -> Any:
    return _MOUNTED._strict_json(raw, label)


def _identity(path: Path, expected: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        return _MOUNTED._file_identity(path, expected, label)
    except (OSError, ValueError, TypeError) as exc:
        raise IntegrityError(f"{label}: identity failure") from exc


def _json_snapshot(path: Path, expected: Mapping[str, Any], label: str) -> tuple[Any, dict[str, Any]]:
    if path.name != expected.get("basename") or not path.is_file():
        raise IntegrityError(f"{label}: basename/file mismatch")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IntegrityError(f"{label}: read failure") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if "size_bytes" in expected and len(raw) != expected["size_bytes"]:
        raise IntegrityError(f"{label}: size mismatch")
    if digest != expected.get("sha256"):
        raise IntegrityError(f"{label}: SHA-256 mismatch")
    return _strict_json(raw, label), {"basename": path.name, "size_bytes": len(raw), "sha256": digest}


def _require_hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value.lower() != value:
        raise IntegrityError(f"{label}: SHA-256 is malformed")
    try:
        int(value, 16)
    except ValueError as exc:
        raise IntegrityError(f"{label}: SHA-256 is malformed") from exc
    return value


def _source_bound(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrityError(f"{label}: identity is missing")
    result = {
        "basename": value.get("basename"),
        "size_bytes": value.get("size_bytes"),
        "sha256": value.get("sha256"),
    }
    if not isinstance(result["basename"], str) or type(result["size_bytes"]) is not int:
        raise IntegrityError(f"{label}: identity is malformed")
    _require_hex(result["sha256"], label)
    return result


def _same_identity(value: Any, expected: Mapping[str, Any], label: str) -> None:
    observed = _source_bound(value, label)
    if any(observed[key] != expected[key] for key in ("basename", "size_bytes", "sha256")):
        raise IntegrityError(f"{label}: identity mismatch")


def _git_identity(expected_head: str) -> dict[str, Any]:
    if len(expected_head) != 40 or expected_head.lower() != expected_head:
        raise IntegrityError("expected-head must be a lowercase 40-hex commit")
    try:
        int(expected_head, 16)
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=_REPO_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        observed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=_REPO_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise IntegrityError("git identity unavailable") from exc
    if branch != "main" or observed != expected_head or dirty:
        raise IntegrityError("audit requires clean main at expected-head")
    return {"branch": branch, "clean": True, "expected": expected_head, "observed": observed}


def _read_scenario_once(stream: Any, expected_size: int, expected_sha256: str) -> dict[str, Any]:
    """Hash one member pass and capture only four canonical JSON fragments."""
    digest = hashlib.sha256()
    total = 0
    frames: list[dict[str, Any]] = []
    quoted = False
    escaped = False
    token = bytearray()
    capture: bytearray | None = None
    capture_stack: list[int] = []
    capture_path: tuple[str, ...] | None = None
    fragments: dict[tuple[str, ...], Any] = {}
    targets = {
        ("decaps",): list,
        ("connection_analysis",): dict,
        ("normalized_project", "rails"): list,
        ("normalized_project", "stackup_layers"): list,
    }
    target_types = {**targets, ("normalized_project",): dict}
    pending_target_path: tuple[str, ...] | None = None
    seen_target_paths: set[tuple[str, ...]] = set()
    root_started = False
    root_closed = False

    def finish_fragment() -> None:
        nonlocal capture, capture_path
        if capture is None or capture_path is None:
            raise IntegrityError("scenario fragment state is incomplete")
        path = capture_path
        value = _strict_json(bytes(capture), f"scenario fragment {path!r}")
        if not isinstance(value, targets[path]):
            raise IntegrityError(f"scenario fragment has the wrong JSON type: {path!r}")
        fragments[path] = value
        capture = None
        capture_path = None
        capture_stack.clear()

    def begin_fragment(path: tuple[str, ...], byte: int) -> None:
        nonlocal capture, capture_path
        expected = targets[path]
        if byte not in (91, 123) or (expected is list and byte != 91) or (expected is dict and byte != 123):
            raise IntegrityError(f"scenario fragment has the wrong opening type: {path!r}")
        capture_path = path
        capture = bytearray((byte,))
        capture_stack[:] = [byte]

    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
        total += len(block)
        for byte in block:
            if capture is not None:
                capture.append(byte)
                if len(capture) > _FRAGMENT_LIMITS[capture_path]:
                    raise IntegrityError("scenario fragment exceeds audit limit")
                if quoted:
                    if escaped:
                        escaped = False
                    elif byte == 92:
                        escaped = True
                    elif byte == 34:
                        quoted = False
                elif byte == 34:
                    quoted = True
                elif byte in (91, 123):
                    capture_stack.append(byte)
                elif byte in (93, 125):
                    if not capture_stack or (capture_stack.pop(), byte) not in ((91, 93), (123, 125)):
                        raise IntegrityError("scenario fragment bracket mismatch")
                    if not capture_stack:
                        finish_fragment()
                continue

            if pending_target_path is not None:
                if byte in b" \t\r\n":
                    continue
                path = pending_target_path
                pending_target_path = None
                expected = target_types[path]
                if byte not in (91, 123) or (expected is list and byte != 91) or (expected is dict and byte != 123):
                    raise IntegrityError(f"scenario fragment has the wrong opening type: {path!r}")
                parent = frames[-1] if frames else None
                if parent and parent["kind"] == "object":
                    parent["key"] = None
                if path in targets:
                    begin_fragment(path, byte)
                else:
                    frames.append({"kind": "object", "path": path, "key": None, "expect_key": True})
                continue

            if root_closed:
                if byte not in b" \t\r\n":
                    raise IntegrityError("scenario has trailing non-whitespace")
                continue
            if not root_started:
                if byte in b" \t\r\n":
                    continue
                if byte != 123:
                    raise IntegrityError("scenario root must be one object")
                root_started = True
                frames.append({"kind": "object", "path": (), "key": None, "expect_key": True})
                continue

            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
                    if frames and frames[-1]["kind"] == "object" and frames[-1]["expect_key"]:
                        try:
                            frames[-1]["key"] = token.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise IntegrityError("scenario key is not UTF-8") from exc
                    token.clear()
                else:
                    token.append(byte)
                continue
            if byte == 34:
                quoted = True
                token.clear()
                continue
            if not frames:
                raise IntegrityError("scenario root frame is missing")
            if byte == 58:
                frame = frames[-1]
                if frame["kind"] != "object" or frame["key"] is None or not frame["expect_key"]:
                    raise IntegrityError("scenario colon is outside an object key")
                candidate_path = tuple(frame["path"] + (frame["key"],))
                if candidate_path in target_types:
                    if candidate_path in seen_target_paths or pending_target_path is not None:
                        raise IntegrityError(f"scenario duplicate target key: {candidate_path!r}")
                    seen_target_paths.add(candidate_path)
                    pending_target_path = candidate_path
                frame["expect_key"] = False
                continue
            if byte in (91, 123):
                parent = frames[-1]
                path = tuple(parent["path"] + (parent["key"],)) if parent["kind"] == "object" and parent["key"] is not None else tuple(parent["path"])
                if parent["kind"] == "object":
                    parent["key"] = None
                frames.append({"kind": "array" if byte == 91 else "object", "path": path, "key": None, "expect_key": byte == 123})
                continue
            if byte in (93, 125):
                frame = frames[-1]
                expected = 93 if frame["kind"] == "array" else 125
                if byte != expected:
                    raise IntegrityError("scenario bracket mismatch")
                frames.pop()
                if not frames:
                    root_closed = True
                continue
            if byte == 44:
                frame = frames[-1]
                if frame["kind"] == "object":
                    frame["expect_key"] = True
                    frame["key"] = None
                continue

    if quoted or capture is not None or pending_target_path is not None or frames or not root_started or not root_closed or total != expected_size or digest.hexdigest() != expected_sha256:
        raise IntegrityError("scenario stream is incomplete or identity-mismatched")
    if set(fragments) != set(targets) or ("normalized_project",) not in seen_target_paths:
        raise IntegrityError("scenario canonical fragments are missing")
    return {"decaps": fragments[("decaps",)], "connection_analysis": fragments[("connection_analysis",)], "rails": fragments[("normalized_project", "rails")], "stackup_layers": fragments[("normalized_project", "stackup_layers")]}


def _candidate_snapshot(path: Path, identity: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        with ZipFile(path) as archive:
            manifest, by_name = _MOUNTED._validate_archive(archive)
            if not isinstance(manifest, Mapping):
                raise IntegrityError("candidate manifest is malformed")
            if (manifest.get("format") != "spd-decap-pi-scenario" or manifest.get("format_version") != 1 or manifest.get("schema_version") != "0.1" or manifest.get("app_version") != "0.23.0" or manifest.get("raw_spd_embedded") is not False or manifest.get("scenario_file") != "scenario.json"):
                raise IntegrityError("candidate scenario_file is not canonical")
            scenario_size = manifest.get("scenario_size")
            scenario_sha = manifest.get("scenario_sha256")
            if type(scenario_size) is not int or scenario_size < 0:
                raise IntegrityError("candidate scenario size is malformed")
            _require_hex(scenario_sha, "candidate scenario")
            entries = manifest.get("attachments")
            if not isinstance(entries, list):
                raise IntegrityError("candidate attachments are missing")
            declared: dict[str, Mapping[str, Any]] = {}
            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise IntegrityError("candidate attachment is malformed")
                name, member = entry.get("name"), entry.get("path")
                if not isinstance(name, str) or not isinstance(member, str):
                    raise IntegrityError("candidate attachment identity is malformed")
                try:
                    canonical = _MOUNTED._safe_attachment_name(name)
                except Exception as exc:
                    raise IntegrityError("candidate attachment name is unsafe") from exc
                if canonical != name or member != f"attachments/{name}" or name.casefold() in seen or member.casefold() in seen:
                    raise IntegrityError("candidate attachment duplicate/path mismatch")
                if type(entry.get("size")) is not int or entry["size"] < 0:
                    raise IntegrityError("candidate attachment size is malformed")
                _require_hex(entry.get("sha256"), "candidate attachment")
                seen.update((name.casefold(), member.casefold()))
                declared[member] = dict(entry)
            if set(by_name) - {"manifest.json", "scenario.json"} != set(declared):
                raise IntegrityError("candidate archive/member set mismatch")
            for member, entry in declared.items():
                info = by_name.get(member)
                if info is None or info.file_size != entry["size"]:
                    raise IntegrityError("candidate attachment size mismatch")
            info = by_name.get("scenario.json")
            if info is None or info.file_size != scenario_size:
                raise IntegrityError("candidate scenario size mismatch")
            fragments = _read_scenario_once(archive.open("scenario.json"), scenario_size, scenario_sha)
            return fragments, {"identity": dict(identity), "manifest": dict(manifest), "attachments": declared}
    except IntegrityError:
        raise
    except (BadZipFile, KeyError, OSError) as exc:
        raise IntegrityError("candidate archive failure") from exc


def _validate_inputs(import_json: Mapping[str, Any], correlation: Mapping[str, Any], candidate: Mapping[str, Any], import_identity: Mapping[str, Any]) -> None:
    _MOUNTED._validate_import(import_json, candidate["identity"])
    _same_identity(correlation.get("source"), EXPECTED_SOURCE, "correlation source")
    _same_identity(correlation.get("candidate_bundle"), candidate["identity"], "correlation candidate")
    binding = correlation.get("candidate_import_report_binding")
    if not isinstance(binding, Mapping) or binding.get("basename") != import_identity["basename"] or binding.get("sha256") != import_identity["sha256"] or binding.get("status") != "validated" or binding.get("fresh_import_mode") is not True:
        raise IntegrityError("correlation/import binding mismatch")


def _model_snapshot(fragments: Mapping[str, Any]) -> tuple[list[ScenarioDecap], SharedPadConnectionAnalysis, list[RailSpec], list[StackupLayer]]:
    try:
        decaps = [ScenarioDecap.model_validate(item) for item in fragments["decaps"]]
        analysis = SharedPadConnectionAnalysis.model_validate(fragments["connection_analysis"])
        rails = [RailSpec.model_validate(item) for item in fragments["rails"]]
        stackup = [StackupLayer.model_validate(item) for item in fragments["stackup_layers"]]
    except Exception as exc:
        raise IntegrityError("scenario typed fragment validation failed") from exc
    if len({item.refdes.casefold() for item in decaps}) != len(decaps):
        raise IntegrityError("scenario decap REFDES are not unique")
    if len({item.rail_id.casefold() for item in rails}) != len(rails):
        raise IntegrityError("scenario rail IDs are not unique")
    if len({item.name.casefold() for item in stackup}) != len(stackup):
        raise IntegrityError("scenario stackup layer names are not unique")
    return decaps, analysis, rails, stackup


def _inventory(decaps: list[ScenarioDecap], analysis: SharedPadConnectionAnalysis, rails: list[RailSpec], stackup: list[StackupLayer]) -> dict[str, Any]:
    decap_by_refdes = {item.refdes.casefold(): item for item in decaps}
    if len({key.casefold() for key in analysis.connections}) != len(analysis.connections) or any(key != value.refdes for key, value in analysis.connections.items()):
        raise IntegrityError("connection mapping keys and refdes are not exact and unique")
    connection_by_refdes = {key.casefold(): value for key, value in analysis.connections.items()}
    if set(decap_by_refdes) != set(connection_by_refdes):
        raise IntegrityError("decap/connection REFDES sets differ")
    seen_vias: dict[str, tuple[str, str, str]] = {}
    rail_map = {item.rail_id.casefold(): item for item in rails}
    shared_units: dict[str, tuple[str, str, tuple[Any, ...], tuple[Any, ...]]] = {}
    for cluster in analysis.clusters:
        try:
            derived = derive_shared_pad_current_components(cluster, decap_by_refdes, connection_by_refdes, analysis_version=analysis.version)
        except Exception as exc:
            raise IntegrityError("shared-pad component derivation failed") from exc
        if derived.shared_power_via_conflicts or derived.shared_ground_via_conflicts or not derived.components or not derived.ground_components:
            raise IntegrityError("shared-pad Via conflict or incomplete component mapping")
        for mapping in derived.capacitor_component_mappings:
            mapping_key = mapping.refdes.casefold()
            if mapping_key in shared_units:
                raise IntegrityError("shared-pad component mapping reuses a REFDES")
            pwr = derived.components[mapping.power_component_index]
            gnd = derived.ground_components[mapping.ground_component_index]
            shared_units[mapping_key] = (f"{cluster.cluster_id}:P:{mapping.power_component_index}", f"{cluster.cluster_id}:G:{mapping.ground_component_index}", pwr.power_vias, gnd.ground_vias)
    per_rail: dict[str, dict[str, Any]] = {}
    selected = [item for item in decaps if item.enabled and item.source_mounted and item.pad_state.value == "NORMAL" and item.current_rail_id in LOADED_RAILS]
    if not selected:
        raise IntegrityError("no selected loaded decaps")
    for item in selected:
        if item.current_rail_id.casefold() != item.source_rail_id.casefold() or item.current_net.casefold() != item.source_net.casefold() or not item.model_id:
            raise IntegrityError("selected decap source/current scope mismatch")
        if item.current_rail_id.casefold() not in rail_map:
            raise IntegrityError("selected decap rail is not in normalized rails")
        rail = rail_map[item.current_rail_id.casefold()]
        if rail.rail_id.casefold() != item.current_rail_id.casefold() or rail.net.casefold() != item.current_net.casefold() or item.current_net.casefold() != item.source_net.casefold():
            raise IntegrityError("rail/net/source binding mismatch")
        connection = connection_by_refdes.get(item.refdes.casefold())
        if connection is None:
            raise IntegrityError(f"missing connection for {item.refdes}")
        if connection.kind not in {DecapConnectionKind.DIRECT, DecapConnectionKind.SHARED_ANCHOR, DecapConnectionKind.SHARED_DUMMY}:
            raise IntegrityError("selected decap connection is not actionable")
        family = per_rail.setdefault(item.current_rail_id, {"direct": 0, "shared": 0, "pwr_units": set(), "gnd_units": set(), "pwr_vias": {}, "gnd_vias": {}, "segments": 0, "length_um": 0.0, "resistance_ohm": 0.0, "inductance_h": 0.0, "landing_count": 0, "landing_x_min": None, "landing_x_max": None, "landing_y_min": None, "landing_y_max": None, "target_layers": set(), "target_nodes": set(), "evidence_sha256": set(), "segment_classifications": {}})
        family["direct" if connection.kind == DecapConnectionKind.DIRECT else "shared"] += 1
        if connection.kind == DecapConnectionKind.DIRECT:
            unit_id = (f"direct:{item.refdes.casefold()}", f"direct:{item.refdes.casefold()}", connection.power_vias, connection.ground_vias)
        else:
            unit_id = shared_units.get(item.refdes.casefold())
            if unit_id is None:
                raise IntegrityError("shared decap has no derived component unit")
        family["pwr_units"].add(unit_id[0])
        family["gnd_units"].add(unit_id[1])
        for net_name, landings, unit_key in (("PWR", unit_id[2], unit_id[0]), ("GND", unit_id[3], unit_id[1])):
            for landing in landings:
                via_key = landing.via_id.casefold()
                target = family["pwr_vias"] if net_name == "PWR" else family["gnd_vias"]
                fingerprint = landing.model_dump_json(exclude_none=True)
                prior = seen_vias.get(via_key)
                owner = (unit_key, net_name, fingerprint)
                if prior is not None and prior != owner:
                    raise IntegrityError("physical Via evidence is reused incompatibly")
                if prior is None:
                    seen_vias[via_key] = owner
                if via_key in target:
                    continue
                target[via_key] = landing.endpoint_node_id
                if not landing.path_evidence:
                    raise IntegrityError("target Via path evidence is missing")
                expected_layer = rail_map[item.current_rail_id.casefold()].pwr_layer if net_name == "PWR" else rail_map[item.current_rail_id.casefold()].gnd_layer
                matching_path = landing.evidence_for_layer(expected_layer)
                if matching_path is None or sum(path.target_layer.casefold() == expected_layer.casefold() for path in landing.path_evidence) != 1:
                    raise IntegrityError("Via target layer is missing or ambiguous")
                for path in (matching_path,):
                    if path.trace_hops != 0 or path.trace_alternate_exit:
                        raise IntegrityError("Via path contains trace hop/alternate exit")
                    try:
                        resistance, inductance, models = _source_terminal_estimate(path.segments, stackup)
                    except Exception as exc:
                        raise IntegrityError("source terminal Via estimate failed") from exc
                    if not math.isfinite(resistance) or not math.isfinite(inductance):
                        raise IntegrityError("Via estimate is non-finite")
                    family["segments"] += len(path.segments)
                    family["length_um"] += sum(segment.length_um for segment in path.segments)
                    family["resistance_ohm"] += resistance
                    family["inductance_h"] += inductance
                    family.setdefault("model_count", 0)
                    family["model_count"] += len(models)
                    for segment, model in zip(path.segments, models, strict=True):
                        model_r = float(getattr(model, "resistance_ohm", 0.0))
                        model_l = float(getattr(model, "inductance_h", 0.0))
                        if not math.isfinite(model_r) or not math.isfinite(model_l):
                            raise IntegrityError("segment classification is non-finite")
                        classification = model.classification
                        row_key = (net_name, segment.start_layer, segment.end_layer, segment.padstack_material, classification.conductor_model, classification.fill_provenance)
                        row = family["segment_classifications"].setdefault(row_key, {"terminal": net_name, "start_layer": segment.start_layer, "end_layer": segment.end_layer, "padstack_material": segment.padstack_material, "conductor_model": classification.conductor_model, "fill_provenance": classification.fill_provenance, "count": 0, "length_um": 0.0, "resistance_ohm": 0.0, "inductance_h": 0.0})
                        row["count"] += 1
                        row["length_um"] += float(segment.length_um)
                        row["resistance_ohm"] += model_r
                        row["inductance_h"] += model_l
                    family["landing_count"] += 1
                    family["landing_x_min"] = landing.x_um if family["landing_x_min"] is None else min(family["landing_x_min"], landing.x_um)
                    family["landing_x_max"] = landing.x_um if family["landing_x_max"] is None else max(family["landing_x_max"], landing.x_um)
                    family["landing_y_min"] = landing.y_um if family["landing_y_min"] is None else min(family["landing_y_min"], landing.y_um)
                    family["landing_y_max"] = landing.y_um if family["landing_y_max"] is None else max(family["landing_y_max"], landing.y_um)
                    family["target_layers"].add(path.target_layer)
                    family["target_nodes"].add(path.target_node_id)
                    family["evidence_sha256"].add(hashlib.sha256(path.model_dump_json().encode("utf-8")).hexdigest())
    if {key.casefold() for key in per_rail} != {key.casefold() for key in LOADED_RAILS}:
        raise IntegrityError("not all loaded rails have selected Via inventory")
    for value in per_rail.values():
        if not all(value[key] for key in ("pwr_units", "gnd_units", "pwr_vias", "gnd_vias")):
            raise IntegrityError("rail Via inventory is incomplete")
        value["pwr_vias"] = sorted(value["pwr_vias"])
        value["gnd_vias"] = sorted(value["gnd_vias"])
        value["pwr_units"] = sorted(value["pwr_units"])
        value["gnd_units"] = sorted(value["gnd_units"])
        value["length_um"] = float(value["length_um"])
        value["resistance_ohm"] = float(value["resistance_ohm"])
        value["inductance_h"] = float(value["inductance_h"])
        value["target_layers"] = sorted(value["target_layers"], key=str.casefold)
        value["target_nodes"] = sorted(value["target_nodes"], key=str.casefold)
        value["evidence_sha256"] = sorted(value["evidence_sha256"])
        value["segment_classifications"] = sorted(value["segment_classifications"].values(), key=lambda row: tuple(str(row[key] or "").casefold() for key in ("terminal", "start_layer", "end_layer", "padstack_material", "conductor_model", "fill_provenance")))
    return {"selected_loaded_decap_count": len(selected), "per_rail": per_rail}


def audit(candidate: Path, import_report: Path, correlation_report: Path, previous_w7_audit: Path, expected_head: str) -> dict[str, Any]:
    git_identity = _git_identity(expected_head)
    candidate_identity = _identity(candidate, EXPECTED_CANDIDATE, "candidate")
    import_json, import_identity = _json_snapshot(import_report, EXPECTED_IMPORT, "import report")
    correlation, correlation_identity = _json_snapshot(correlation_report, EXPECTED_CORRELATION, "correlation report")
    previous, previous_identity = _json_snapshot(previous_w7_audit, EXPECTED_PREVIOUS_W7, "previous W7 audit")
    if not all(isinstance(value, Mapping) for value in (import_json, correlation, previous)):
        raise IntegrityError("small audit JSON roots must be objects")
    if previous.get("selected_cap_count") != EXPECTED_PREVIOUS_SELECTED_COUNT or not isinstance(previous.get("diagnostic_failures"), list) or len(previous["diagnostic_failures"]) != 6 or not isinstance(previous.get("cap_only"), Mapping) or not isinstance(previous.get("site_pair_deltas_db"), Mapping) or not isinstance(previous.get("rails"), Mapping):
        raise IntegrityError("previous W7 observation subset is not the frozen six-rail audit")
    _validate_inputs(import_json, correlation, {"identity": candidate_identity}, import_identity)
    fragments, _ = _candidate_snapshot(candidate, candidate_identity)
    decaps, analysis, rails, stackup = _model_snapshot(fragments)
    if analysis.source_sha256 != EXPECTED_SOURCE["sha256"]:
        raise IntegrityError("connection analysis source SHA mismatch")
    inventory = _inventory(decaps, analysis, rails, stackup)
    if inventory["selected_loaded_decap_count"] != EXPECTED_SELECTED_COUNT or inventory["selected_loaded_decap_count"] != previous["selected_cap_count"]:
        raise IntegrityError("selected loaded decap count is not bound to frozen W7 count")
    previous_subset = {key: previous.get(key) for key in ("selected_cap_count", "diagnostic_failures", "cap_only", "site_pair_deltas_db", "rails")}
    read_budget = {**_READ_BUDGET, "fragment_limits_bytes": {"decaps": _FRAGMENT_LIMITS[("decaps",)], "connection_analysis": _FRAGMENT_LIMITS[("connection_analysis",)], "rails": _FRAGMENT_LIMITS[("normalized_project", "rails")], "stackup_layers": _FRAGMENT_LIMITS[("normalized_project", "stackup_layers")]}, "full_scenario_hydration": False}
    return {"tool": TOOL, "schema": SCHEMA, "status": "diagnostic_complete", "source": dict(EXPECTED_SOURCE), "candidate": candidate_identity, "import_report": import_identity, "correlation_report": correlation_identity, "previous_w7_audit": previous_identity, "git": git_identity, "read_budget": read_budget, "audited_scope": SELECTED_BLOCK, "selected_investigation_block": None, "terminal_via_inventory": inventory, "previous_w7_observation": previous_subset, "terminal_via_numeric_accuracy": "N/A", "pad_antipad_current_spreading_numeric": "N/A", "independent_signature_available": False, "causal_owner": None, "owner_status": "unclassified", "physics_authorized": False, "exclusive_causality_unproven": True, "diagnostic_failures": ["independent Via signature unavailable", "pad/antipad/current-spreading numeric evidence unavailable"], "exit_code": 2}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--import-report", type=Path, required=True)
    parser.add_argument("--correlation-report", type=Path, required=True)
    parser.add_argument("--previous-w7-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    created = False
    try:
        result = audit(args.candidate, args.import_report, args.correlation_report, args.previous_w7_audit, args.expected_head)
        with args.output.open("x", encoding="utf-8", newline="") as stream:
            created = True
            json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except (IntegrityError, OSError, TypeError, ValueError) as exc:
        try:
            if created and args.output.exists():
                args.output.unlink()
        except OSError:
            pass
        print(f"integrity failure: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
