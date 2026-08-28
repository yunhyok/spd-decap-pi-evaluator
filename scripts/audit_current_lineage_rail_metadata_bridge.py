"""17DV read-only metadata bridge gate (no raw-spatial or physics access)."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import monotonic
from zipfile import ZipFile, ZipInfo

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str((_ROOT / "src").resolve()))
from spd_decap_pi.compiled_topology_asset import CompiledTopologyAssetError, load_compiled_topology_asset
from spd_decap_pi.scenario import SCENARIO_SCHEMA_VERSION, ScenarioSpec, _ScenarioValidationMemo
from spd_decap_pi._core.domain import ProjectSpec
from spd_decap_pi.scenario_io import _manifest_hash, _manifest_int, _read_archive_member, _strict_json, _validate_archive
from spd_decap_pi.version import __version__ as APP_VERSION

SCHEMA = "17dv-metadata-bridge-v1"
APP = "SPD Decap PI Evaluator"
RAIL_ID = "ADC_VDD_180_VQPS_SYS_1_AON/0"
EXPECTED_CANDIDATE_SIZE = 911_542_390
EXPECTED_CANDIDATE_SHA256 = "fbe6abeb5655918134ecb47235edfd3b81b891ed5ec95545e03c9651937c6bcc"
EXPECTED_REPORT_SIZE = 8622
EXPECTED_REPORT_SHA256 = "87d83364998ba09639cf36b30e34559cf598da04f653309967145a4dd1681f2f"
EXPECTED_SOURCE_SHA256 = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
EXPECTED_COMPILED_NAME = "topology/layerwise-compiled-topology-v1-fac8e711e65a3fe4.sqlite.zlib"
EXPECTED_COMPILED_PATH = "attachments/topology/layerwise-compiled-topology-v1-fac8e711e65a3fe4.sqlite.zlib"
EXPECTED_COMPILED_SIZE = 112_530_944
EXPECTED_COMPILED_SHA256 = "c7530202d6873d72fe2c01e5c00296ce5b83f2df33222991f1e98f9f45c97094"
EXPECTED_MANIFEST_SIZE = 79_199
EXPECTED_SCENARIO_SIZE = 760_816_272
EXPECTED_SCENARIO_SHA256 = "15115693d43bdfe69bfcf2d17faeb465da0adac640072183ee24eeea414fa9d9"
EXPECTED_PROJECT_BINDING_SHA256 = "52b04151f8c46ad2bc903dbf4e62855e0e29042b428ce38a4aafc9e98c519760"
EXPECTED_CERTIFICATE_SHA256 = "fac8e711e65a3fe4f82d3d32fd7cb862bcf2781e02e5037fb4f16ba5a07c46b3"
EXPECTED_TOPOLOGY_SHA256 = "a12a76a1060cb466b3a35f4e43165e1db6e7a28c96e8a071cb0ea01e9945c6ef"
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_ANCHORS = 2
MAX_VERTEX_SURFACE_LINKS = 4096
STOP_CODES = {"STOP_INPUT_IDENTITY", "STOP_CONTRACT_UNSUPPORTED", "STOP_NO_AUTHORITATIVE_BRIDGE", "STOP_RESOURCE_OR_CANCELLED"}
LIMITATIONS = ["metadata bridge only; no geometry decode/containment; no owner-off/global assembly; no raw-spatial; no W6/Touchstone/correlation/solver/physics/profile/GUI/build/release"]


class AuditStop(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code if code in STOP_CODES else "STOP_CONTRACT_UNSUPPORTED"
        super().__init__(message)


def _fail(message: str, code: str = "STOP_NO_AUTHORITATIVE_BRIDGE") -> None:
    raise AuditStop(code, message)


def _sha_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha_file(path: Path, started: float | None = None, deadline_s: float | None = None) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if started is not None and deadline_s is not None: _deadline(started, deadline_s)
            digest.update(chunk)
    return digest.hexdigest()


def _stamp(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _git_state(expected_head: str) -> dict[str, object]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "symbolic-ref", "--short", "HEAD"], cwd=_ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=_ROOT, text=True).splitlines()
    if head.casefold() != expected_head.casefold() or branch.casefold() != "main" or any(line != "?? accuracy_parse.py" for line in status):
        _fail("git identity is not approved", "STOP_INPUT_IDENTITY")
    return {"head": head, "branch": branch, "tracked_clean": True, "allowed_untracked": ["?? accuracy_parse.py"] if status else []}


def _deadline(started: float, deadline_s: float) -> None:
    if monotonic() - started > deadline_s:
        _fail("deadline exceeded", "STOP_RESOURCE_OR_CANCELLED")


class _ZipOpenTracker:
    """Record the actual archive members whose bytes are opened."""

    def __init__(self, archive: ZipFile):
        self.archive = archive
        self.events: list[str] = []
        self.payloads: dict[str, bytearray] = {}

    def open(self, member: str | ZipInfo, mode: str = "r", pwd=None, *, force_zip64: bool = False):
        name = member.filename if isinstance(member, ZipInfo) else str(member)
        self.events.append(name)
        stream = self.archive.open(member, mode=mode, pwd=pwd, force_zip64=force_zip64)
        capture = self.payloads.setdefault(name, bytearray()) if name.casefold() == "manifest.json" else None

        class _Stream:
            def read(self, size=-1):
                chunk = stream.read(size)
                if capture is not None:
                    capture.extend(chunk)
                return chunk

            def __enter__(self):
                stream.__enter__(); return self

            def __exit__(self, *args):
                return stream.__exit__(*args)

        return _Stream()

    def infolist(self):
        return self.archive.infolist()


def _read_selected(candidate: Path, report: Path, started: float, deadline_s: float):
    _deadline(started, deadline_s)
    try:
        candidate_ok = candidate.stat().st_size == EXPECTED_CANDIDATE_SIZE and _sha_file(candidate, started, deadline_s) == EXPECTED_CANDIDATE_SHA256
        report_ok = report.stat().st_size == EXPECTED_REPORT_SIZE and _sha_file(report, started, deadline_s) == EXPECTED_REPORT_SHA256
    except OSError:
        _fail("candidate/report identity unavailable", "STOP_INPUT_IDENTITY")
    if not candidate_ok: _fail("candidate identity mismatch", "STOP_INPUT_IDENTITY")
    if not report_ok: _fail("report identity mismatch", "STOP_INPUT_IDENTITY")
    with ZipFile(candidate) as raw_archive:
        archive = _ZipOpenTracker(raw_archive)
        cancelled = lambda: monotonic() - started > deadline_s
        manifest, by_name = _validate_archive(archive, is_cancelled=cancelled)
        if by_name["manifest.json"].file_size != EXPECTED_MANIFEST_SIZE:
            _fail("manifest identity mismatch", "STOP_INPUT_IDENTITY")
        scenario_info = by_name.get("scenario.json")
        if scenario_info is None:
            _fail("scenario.json is missing", "STOP_CONTRACT_UNSUPPORTED")
        scenario_size = _manifest_int(manifest, "scenario_size")
        scenario_hash = _manifest_hash(manifest.get("scenario_sha256"), label="scenario SHA-256")
        if scenario_info.file_size != scenario_size:
            _fail("scenario declaration size mismatch", "STOP_CONTRACT_UNSUPPORTED")
        scenario_bytes = _read_archive_member(archive, scenario_info, is_cancelled=cancelled)
        if len(scenario_bytes) != scenario_size or _sha_bytes(scenario_bytes) != scenario_hash or _sha_bytes(scenario_bytes) != EXPECTED_SCENARIO_SHA256:
            _fail("scenario identity mismatch", "STOP_INPUT_IDENTITY")
        raw = _strict_json(scenario_bytes, label="scenario.json")
        if (raw.get("schema_version") != SCENARIO_SCHEMA_VERSION
                or manifest.get("schema_version") != SCENARIO_SCHEMA_VERSION
                or raw.get("app_version") != APP_VERSION
                or manifest.get("app_version") != APP_VERSION):
            _fail("scenario declaration mismatch", "STOP_CONTRACT_UNSUPPORTED")
        validation_memo = _ScenarioValidationMemo()
        try:
            scenario = ScenarioSpec.model_validate(raw, context=validation_memo)
            ProjectSpec.model_validate(scenario.normalized_project)
        except Exception as exc:
            _fail(f"scenario validation failed: {exc}", "STOP_CONTRACT_UNSUPPORTED")
        entries = manifest.get("attachments")
        if not isinstance(entries, list):
            _fail("manifest attachments unsupported", "STOP_CONTRACT_UNSUPPORTED")
        selected = []; declared_paths = set(); declared_names = set(); declared_hashes = {}
        for entry in entries:
            if not isinstance(entry, Mapping):
                _fail("invalid attachment declaration", "STOP_CONTRACT_UNSUPPORTED")
            name, path = str(entry.get("name", "")), str(entry.get("path", ""))
            size = entry.get("size"); digest = _manifest_hash(entry.get("sha256"), label=f"attachment {name} SHA-256")
            key = name.casefold()
            if not name or path != f"attachments/{name}" or path.casefold() in {x.casefold() for x in declared_paths} or key in declared_names or path not in by_name or type(size) is not int or by_name[path].file_size != size:
                _fail("attachment manifest mismatch", "STOP_CONTRACT_UNSUPPORTED")
            declared_paths.add(path); declared_names.add(key); declared_hashes[key] = digest
            if name.casefold() == EXPECTED_COMPILED_NAME.casefold():
                selected.append((name, path, size, digest))
        if set(by_name) != {"manifest.json", "scenario.json", *declared_paths}:
            _fail("archive member set is not canonical", "STOP_CONTRACT_UNSUPPORTED")
        if len(selected) != 1 or selected[0][1] != EXPECTED_COMPILED_PATH:
            _fail("compiled attachment identity unsupported", "STOP_CONTRACT_UNSUPPORTED")
        name, path, size, digest = selected[0]
        compiled_bytes = _read_archive_member(archive, path, is_cancelled=cancelled)
        if size != EXPECTED_COMPILED_SIZE or len(compiled_bytes) != size or digest != EXPECTED_COMPILED_SHA256 or _sha_bytes(compiled_bytes) != EXPECTED_COMPILED_SHA256:
            _fail("compiled attachment identity mismatch", "STOP_INPUT_IDENTITY")
        scenario_names = {str(x).casefold() for x in scenario.attachment_names}
        if scenario_names != declared_names:
            _fail("scenario attachment declaration mismatch", "STOP_CONTRACT_UNSUPPORTED")
        scenario_hashes = {str(k).casefold(): str(v).casefold() for k, v in scenario.attachment_hashes.items()}
        if scenario_hashes != declared_hashes:
            _fail("scenario attachment hash declaration mismatch", "STOP_CONTRACT_UNSUPPORTED")
        expected_fingerprint = _manifest_hash(manifest.get("design_fingerprint"), label="design fingerprint")
        if scenario._design_fingerprint(validation_memo) != expected_fingerprint:
            _fail("scenario design fingerprint mismatch", "STOP_INPUT_IDENTITY")
    opened_unique = list(dict.fromkeys(archive.events))
    expected_opened = {"manifest.json", "scenario.json", EXPECTED_COMPILED_PATH}
    if [name.casefold() for name in archive.events] != ["manifest.json", "scenario.json", EXPECTED_COMPILED_PATH.casefold()]:
        _fail("archive opened an unapproved member", "STOP_CONTRACT_UNSUPPORTED")
    manifest_hash_actual = _sha_bytes(bytes(archive.payloads["manifest.json"]))
    return manifest, scenario, {EXPECTED_COMPILED_NAME: compiled_bytes}, {"archive_member_count": len(by_name), "opened_events": list(archive.events), "opened_unique_names": opened_unique, "manifest_size": by_name["manifest.json"].file_size, "manifest_sha256": manifest_hash_actual, "scenario_size": scenario_size, "scenario_sha256": _sha_bytes(scenario_bytes), "design_fingerprint": scenario._design_fingerprint(validation_memo)}


def _get(obj: object, key: str, default=None):
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _bridge_rows(project: object, compiled: object, *, started: float | None = None, deadline_s: float | None = None) -> dict[str, object]:
    rails = [r for r in _get(project, "rails", ()) if str(_get(r, "rail_id", "")).casefold() == RAIL_ID.casefold()]
    if len(rails) != 1: _fail("rail is missing or ambiguous")
    rail = rails[0]; topology = _get(compiled, "topology")
    ports = [p for p in _get(topology, "rail_ports", ()) if str(_get(p, "rail_id", "")).casefold() == RAIL_ID.casefold()]
    if len(ports) != 1: _fail("compiled rail port is missing or ambiguous")
    port = ports[0]; rail_net = str(_get(rail, "net", "")); selected_net = str(_get(port, "selected_net", "")); ref_net = str(_get(port, "reference_net", ""))
    if not rail_net or not ref_net or selected_net.casefold() != rail_net.casefold(): _fail("logical rail selected NET mismatch", "STOP_CONTRACT_UNSUPPORTED")
    spd = _get(_get(project, "metadata", {}), "spd_import", {}); prov = _get(spd, "selected_plane_pair_provenance", {})
    matches = [v for k, v in prov.items() if str(k).casefold() == rail_net.casefold() and isinstance(v, Mapping)] if isinstance(prov, Mapping) else []
    if len(matches) != 1 or str(matches[0].get("rail_net", "")).casefold() != rail_net.casefold() or str(matches[0].get("source_sha256", "")).casefold() != EXPECTED_SOURCE_SHA256.casefold() or matches[0].get("source_graph_pair_unresolved") is True or matches[0].get("pwr_artwork_proven") is not True or matches[0].get("gnd_artwork_proven") is not True or not matches[0].get("pwr_layer") or not matches[0].get("gnd_layer") or not matches[0].get("pwr_artwork_net") or not matches[0].get("gnd_artwork_net") or any(len(str(matches[0].get(key, ""))) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in str(matches[0].get(key, ""))) for key in ("pwr_artwork_sha256", "gnd_artwork_sha256")): _fail("selected-plane provenance does not bind logical rail")
    if str(matches[0]["pwr_layer"]).casefold() == str(matches[0]["gnd_layer"]).casefold() or str(matches[0]["pwr_artwork_net"]).casefold() == str(matches[0]["gnd_artwork_net"]).casefold(): _fail("selected-plane provenance does not bind logical rail")
    pair = dict(matches[0]); evidence = _get(compiled, "external_port_proof_view", {}); expected_p = {str(x).casefold() for x in _get(port, "positive_pin_ids", ())}; expected_g = {str(x).casefold() for x in _get(port, "negative_pin_ids", ())}; target_pins = expected_p | expected_g
    if (not str(_get(rail, "pwr_layer", "")).strip() or not str(_get(rail, "gnd_layer", "")).strip() or str(_get(rail, "pwr_layer", "")).casefold() != str(pair["pwr_layer"]).casefold() or str(_get(rail, "gnd_layer", "")).casefold() != str(pair["gnd_layer"]).casefold()): _fail("rail layers do not match selected pair", "STOP_CONTRACT_UNSUPPORTED")
    if len(expected_p) != 3 or len(expected_g) != 3 or expected_p & expected_g: _fail("compiled rail port does not expose exactly six target pins")
    bindings = {}
    for b in _get(evidence, "rail_anchor_bindings", ()):
        if isinstance(b, Mapping) and str(b.get("rail_id", "")).casefold() == RAIL_ID.casefold() and str(b.get("pin_id", "")).casefold() in target_pins:
            pin = str(b.get("pin_id", "")).casefold()
            if pin in bindings: _fail("target rail bindings are missing or duplicated")
            bindings[pin] = b
    if set(bindings) != target_pins: _fail("target rail bindings are missing or duplicated")
    contacts = {}
    for c in _get(evidence, "terminal_contacts", ()):
        if isinstance(c, Mapping) and str(c.get("pin_id", "")).casefold() in target_pins:
            pin = str(c.get("pin_id", "")).casefold()
            if pin in contacts: _fail("target contact is ambiguous")
            contacts[pin] = c
    if set(contacts) != target_pins: _fail("target contacts are incomplete")
    anchors_p = {str(x).casefold() for x in _get(port, "positive_anchor_node_ids", ())}; anchors_g = {str(x).casefold() for x in _get(port, "negative_anchor_node_ids", ())}
    positive_node = str(_get(port, "positive_node_id", "")).strip().casefold(); negative_node = str(_get(port, "negative_node_id", "")).strip().casefold()
    if (len(anchors_p) != 1 or len(anchors_g) != 1 or anchors_p & anchors_g or len(anchors_p | anchors_g) > MAX_ANCHORS or not positive_node or not negative_node or positive_node == negative_node or positive_node != next(iter(anchors_p)) or negative_node != next(iter(anchors_g))): _fail("target anchors exceed bounded contract", "STOP_CONTRACT_UNSUPPORTED")
    rows = []; seen_landing = set(); seen_owner = set(); branches = {}; vmap = _get(topology, "vertex_by_landing_key", {}); emap = _get(topology, "first_edge_by_landing_key", {}); omap = _get(topology, "owner_edge_by_id", {})
    for pin in sorted(target_pins):
        b, c = bindings[pin], contacts[pin]; role = str(b.get("role", "")).casefold(); net = str(c.get("net", "")); first = str(c.get("first_via_quotient_edge_id", "")).strip(); exposed = str(c.get("exposed_quotient_vertex_id", "")).strip(); via = str(c.get("incident_via_id", "")).strip(); branch = str(b.get("branch_id", "")).strip(); expected_net = rail_net if role == "power" else ref_net
        if role not in {"power", "ground"} or not branch or str(c.get("status", "")).casefold() != "complete" or not first or not exposed or not via or net.casefold() != expected_net.casefold(): _fail("contact proof does not bind logical NET", "STOP_CONTRACT_UNSUPPORTED")
        candidates = [k for k, value in vmap.items() if len(k) == 2 and str(k[0]).casefold() == via.casefold() and str(value).casefold() == exposed.casefold() and str(emap.get(k, "")).casefold() == first.casefold()] if isinstance(vmap, Mapping) else []
        if len(candidates) != 1: _fail("authoritative Via landing key is absent or ambiguous")
        landing = (str(candidates[0][0]).casefold(), str(candidates[0][1]).casefold()); owner = f"via:{landing[0]}"
        if landing in seen_landing or owner in seen_owner or str(omap.get(owner, "")).casefold() != first.casefold(): _fail("Via landing/owner bridge is missing or duplicated")
        row = {"branch_id": branch, "pin_id": str(b.get("pin_id", pin)), "role": role, "net": net, "landing_key": list(landing), "vertex_id": exposed, "first_edge_id": first, "owner_id": owner}; rows.append(row); branches.setdefault(branch, []).append(row); seen_landing.add(landing); seen_owner.add(owner)
    if len(branches) != 3 or any(len(v) != 2 or {x["role"] for x in v} != {"power", "ground"} for v in branches.values()): _fail("branches are not exactly three")
    if {r["pin_id"].casefold() for r in rows if r["role"] == "power"} != expected_p or {r["pin_id"].casefold() for r in rows if r["role"] == "ground"} != expected_g: _fail("contact pins do not match compiled rail port")
    actual_p = {r["vertex_id"].casefold() for r in rows if r["role"] == "power"}; actual_g = {r["vertex_id"].casefold() for r in rows if r["role"] == "ground"}
    if actual_p != anchors_p or actual_g != anchors_g: _fail("contact vertices do not exactly match role anchor sets")
    targets = {r["vertex_id"].casefold() for r in rows}; selected_links = []
    for link in _get(topology, "topology_links", ()):
        if started is not None and deadline_s is not None: _deadline(started, deadline_s)
        first = str(_get(link, "first_node_id", "")).casefold(); owner_prefix = f"finite-vertex-surface:{first}:"
        owners = _get(link, "owner_ids", ()) or (_get(link, "owner_id", ""),)
        matching = [str(owner) for owner in owners if str(owner).casefold().startswith(owner_prefix)]
        if str(_get(link, "mode", "")).casefold() == "topology_only_ideal" and first in targets and _get(link, "count", None) == 1 and len(owners) == 1 and len(matching) == 1: selected_links.append(link)
    if len(selected_links) > MAX_VERTEX_SURFACE_LINKS: _fail("selected surface links exceed bound", "STOP_RESOURCE_OR_CANCELLED")
    by_vertex = {}
    for link in selected_links: by_vertex.setdefault(str(_get(link, "first_node_id", "")).casefold(), []).append(link)
    cert = _get(topology, "certificate", {}) or _get(compiled, "scenario_certificate_view", {}); components = list(_get(cert, "surface_equivalence_components", ())); assets = list(_get(cert, "geometry_assets", ())); records = list(_get(spd, "plane_geometries", ())); surfaces = []
    bridge_rows = []; surface_seen = set(); surface_identity_seen = set(); link_seen = set(); owner_seen = set()
    vertex_rows = {}
    for row in rows: vertex_rows.setdefault(row["vertex_id"].casefold(), row)
    for row in vertex_rows.values():
        if started is not None and deadline_s is not None: _deadline(started, deadline_s)
        found = by_vertex.get(row["vertex_id"].casefold(), []); layer = str(pair["pwr_layer"] if row["role"] == "power" else pair["gnd_layer"])
        if not found: _fail("finite-vertex-surface topology link is absent or ambiguous")
        for link in found:
            island = str(_get(link, "second_node_id", "")); link_id = str(_get(link, "link_id", "")); owners = _get(link, "owner_ids", ()) or (_get(link, "owner_id", ""),); matching = [str(x) for x in owners if str(x).casefold().startswith(f"finite-vertex-surface:{row['vertex_id'].casefold()}:")]
            if not link_id or len(owners) != 1 or len(matching) != 1 or _get(link, "count", None) != 1 or str(_get(link, "mode", "")).casefold() != "topology_only_ideal": _fail("finite-vertex-surface topology link is invalid")
            comps = []; ars = []; recs = []
            for item in components:
                if started is not None and deadline_s is not None: _deadline(started, deadline_s)
                if island.casefold() in {str(i).casefold() for i in _get(item, "island_ids", ())} and str(_get(item, "layer", "")).casefold() == layer.casefold(): comps.append(item)
            for item in assets:
                if started is not None and deadline_s is not None: _deadline(started, deadline_s)
                if island.casefold() in {str(i).casefold() for i in _get(item, "island_ids", ())} and str(_get(item, "layer", "")).casefold() == layer.casefold(): ars.append(item)
            for item in records:
                if started is not None and deadline_s is not None: _deadline(started, deadline_s)
                if island.casefold() in {str(i).casefold() for i in _get(item, "island_ids", ())} and str(_get(item, "layer", "")).casefold() == layer.casefold(): recs.append(item)
            if len(comps) != 1 or len(ars) != 1 or len(recs) != 1: _fail("artwork island has no unique plane geometry record")
            component = comps[0]; physical_net = str(_get(recs[0], "net", ""))
            expected_net = str(pair.get("pwr_artwork_net" if row["role"] == "power" else "gnd_artwork_net", ""))
            component_islands = {str(i).casefold() for i in _get(component, "island_ids", ())}
            asset_islands = {str(i).casefold() for i in _get(ars[0], "island_ids", ())}
            record_islands = {str(i).casefold() for i in _get(recs[0], "island_ids", ())}
            evidence_sha = str(_get(component, "component_evidence_sha256", "")).strip()
            asset_sha = str(_get(ars[0], "asset_sha256", "")).strip()
            record_sha = str(_get(recs[0], "asset_sha256", "")).strip()
            representative = str(_get(component, "representative_island_id", "")).strip()
            if (not str(_get(component, "component_id", "")).strip() or representative.casefold() not in component_islands or island.casefold() not in component_islands
                    or not component_islands <= asset_islands or asset_islands != record_islands
                    or str(_get(component, "net", "")).casefold() != expected_net.casefold()
                    or physical_net.casefold() != expected_net.casefold()
                    or str(_get(ars[0], "net", "")).casefold() != expected_net.casefold()
                    or str(_get(component, "layer", "")).casefold() != layer.casefold()
                    or str(_get(ars[0], "layer", "")).casefold() != layer.casefold()
                    or str(_get(recs[0], "layer", "")).casefold() != layer.casefold()
                    or str(_get(component, "contact_status", "")).casefold() != "complete"
                    or len(evidence_sha) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in evidence_sha)
                    or not str(_get(ars[0], "asset", "")) or str(_get(ars[0], "asset", "")) != str(_get(recs[0], "asset", ""))
                    or len(asset_sha) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in asset_sha)
                    or asset_sha.casefold() != record_sha.casefold()):
                _fail("artwork island has no unique plane geometry record")
            identity = (row["role"], link_id.casefold(), matching[0].casefold(), island.casefold())
            if identity in surface_seen or link_id.casefold() in link_seen or matching[0].casefold() in owner_seen: _fail("finite-vertex-surface link is duplicated")
            surface_seen.add(identity)
            link_seen.add(link_id.casefold()); owner_seen.add(matching[0].casefold())
            surface_value = {"role": row["role"], "layer": layer, "net": physical_net, "island_id": island, "asset": str(_get(recs[0], "asset", "")), "asset_sha256": record_sha}
            surface_identity = tuple((surface_value[key] if key in {"asset", "role"} else str(surface_value[key]).casefold()) for key in ("role", "layer", "net", "island_id", "asset", "asset_sha256"))
            if surface_identity not in surface_identity_seen:
                surface_identity_seen.add(surface_identity)
                surfaces.append(surface_value)
            bridge_rows.append({"role": row["role"], "vertex_id": row["vertex_id"], "topology_link_id": link_id, "topology_owner_id": matching[0], "island_id": island, "component_id": str(_get(component, "component_id", "")), "component_evidence_sha256": str(_get(component, "component_evidence_sha256", "")), "component_island_ids": sorted(str(i) for i in _get(component, "island_ids", ())), "layer": layer, "net": physical_net, "asset": str(_get(recs[0], "asset", "")), "asset_sha256": str(_get(recs[0], "asset_sha256", ""))})
    p = {(x["layer"].casefold(), x["net"].casefold(), x["asset"], x["asset_sha256"].casefold()) for x in surfaces if x["role"] == "power"}; g = {(x["layer"].casefold(), x["net"].casefold(), x["asset"], x["asset_sha256"].casefold()) for x in surfaces if x["role"] == "ground"}
    if len(p) != 1 or len(g) != 1 or {x["island_id"] for x in surfaces if x["role"] == "power"} & {x["island_id"] for x in surfaces if x["role"] == "ground"}: _fail("PWR/GND artwork surfaces do not converge disjointly")
    ordered = tuple(sorted(rows, key=lambda x: (x["branch_id"].casefold(), x["role"], x["pin_id"].casefold())))
    surfaces.sort(key=lambda x: (x["role"], x["layer"].casefold(), x["net"].casefold(), x["island_id"].casefold(), x["asset"], x["asset_sha256"].casefold()))
    return {"rail": {"rail_id": str(_get(rail, "rail_id")), "net": rail_net}, "port": {"rail_id": str(_get(port, "rail_id")), "selected_net": selected_net, "reference_net": ref_net}, "selected_pair": pair, "contact_rows": ordered, "bridge_rows": sorted(bridge_rows, key=lambda x: (x["role"], x["vertex_id"].casefold(), x["topology_link_id"].casefold())), "surface_identities": surfaces}


def audit_candidate(candidate: Path, report: Path, expected_head: str, *, deadline_s: float = 480.0) -> dict[str, object]:
    started = monotonic()
    if shutil.disk_usage(tempfile.gettempdir()).free < 4 * 1024**3: _fail("temporary storage below 4 GiB", "STOP_RESOURCE_OR_CANCELLED")
    git = _git_state(expected_head)
    try:
        manifest, scenario, attachments, opened = _read_selected(candidate, report, started, deadline_s)
    except RuntimeError as exc:
        if "cancel" in str(exc).casefold() or monotonic() - started > deadline_s: _fail(str(exc), "STOP_RESOURCE_OR_CANCELLED")
        raise
    project = scenario.base_project; spd = project.metadata.get("spd_import", {})
    if scenario.source.sha256.casefold() != EXPECTED_SOURCE_SHA256 or str(spd.get("source_sha256", "")).casefold() != EXPECTED_SOURCE_SHA256: _fail("source identity mismatch", "STOP_INPUT_IDENTITY")
    records = spd.get("plane_geometries", ()); artwork_ids = tuple(str(i) for row in records for i in row.get("island_ids", ())); cancelled = lambda: monotonic() - started > deadline_s
    try:
        compiled = load_compiled_topology_asset(project, attachments, artwork_ids, is_cancelled=cancelled)
    except CompiledTopologyAssetError as exc:
        if "CANCEL" in exc.code or cancelled(): _fail(str(exc), "STOP_RESOURCE_OR_CANCELLED")
        if "IDENTITY" in exc.code or "MISMATCH" in exc.code or "PROJECT" in exc.code: _fail(str(exc), "STOP_INPUT_IDENTITY")
        if "INVALID" in exc.code or "UNSUPPORTED" in exc.code: _fail(str(exc), "STOP_CONTRACT_UNSUPPORTED")
        _fail(str(exc), "STOP_NO_AUTHORITATIVE_BRIDGE")
    except RuntimeError as exc:
        if cancelled() or "cancel" in str(exc).casefold(): _fail(str(exc), "STOP_RESOURCE_OR_CANCELLED")
        raise
    if compiled is None: _fail("compiled topology attachment is missing", "STOP_CONTRACT_UNSUPPORTED")
    topology = getattr(compiled, "topology", None)
    compiled_meta = spd.get("layerwise_compiled_topology_asset", {}) if isinstance(spd, Mapping) else {}
    expected = {"source_sha256": EXPECTED_SOURCE_SHA256, "project_binding_sha256": EXPECTED_PROJECT_BINDING_SHA256, "certificate_evidence_sha256": EXPECTED_CERTIFICATE_SHA256, "topology_identity_sha256": EXPECTED_TOPOLOGY_SHA256}
    if not isinstance(compiled_meta, Mapping): _fail("compiled project metadata is invalid", "STOP_CONTRACT_UNSUPPORTED")
    for key, value in expected.items():
        if str(compiled_meta.get(key, "")).casefold() != value.casefold(): _fail(f"compiled project metadata {key} mismatch", "STOP_INPUT_IDENTITY")
    if str(getattr(topology, "source_sha256", "")).casefold() != EXPECTED_SOURCE_SHA256.casefold() or str(getattr(topology, "certificate_evidence_sha256", "")).casefold() != EXPECTED_CERTIFICATE_SHA256.casefold() or str(getattr(topology, "topology_identity_sha256", "")).casefold() != EXPECTED_TOPOLOGY_SHA256.casefold():
        _fail("loaded topology identity mismatch", "STOP_INPUT_IDENTITY")
    evidence = _bridge_rows(project, compiled, started=started, deadline_s=deadline_s); stamp_input = {"schema": SCHEMA, "app": APP, "version": APP_VERSION, "result": "PASS_METADATA_BRIDGE_ONLY", "git": git, "candidate_sha256": EXPECTED_CANDIDATE_SHA256, "candidate_size": EXPECTED_CANDIDATE_SIZE, "report_sha256": EXPECTED_REPORT_SHA256, "report_size": EXPECTED_REPORT_SIZE, "source_sha256": EXPECTED_SOURCE_SHA256, "manifest_size": opened["manifest_size"], "manifest_sha256": opened["manifest_sha256"], "scenario_size": opened["scenario_size"], "scenario_sha256": opened["scenario_sha256"], "design_fingerprint": opened["design_fingerprint"], "compiled": {"name": EXPECTED_COMPILED_NAME, "size": EXPECTED_COMPILED_SIZE, "sha256": EXPECTED_COMPILED_SHA256, **expected}, "rail": evidence["rail"], "port": evidence["port"], "selected_pair": evidence["selected_pair"], "contact_rows": evidence["contact_rows"], "bridge_rows": evidence["bridge_rows"], "surface_identities": evidence["surface_identities"], "archive_member_count": opened["archive_member_count"], "opened_events": opened["opened_events"], "opened_unique_names": opened["opened_unique_names"], "raw_spatial_loaded": False, "limitations": LIMITATIONS}
    stamp = _stamp(stamp_input)
    result = dict(stamp_input); result.update({"evidence_sha256": stamp, "exit_code": 0, "limitations": stamp_input["limitations"], "stamp_input": stamp_input}); return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--candidate", type=Path, required=True); parser.add_argument("--report", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--expected-head", required=True); args = parser.parse_args(argv)
    if args.output.exists() or not args.output.parent.exists(): return 2
    try: result = audit_candidate(args.candidate, args.report, args.expected_head)
    except AuditStop as exc: result = {"schema": SCHEMA, "app": APP, "version": APP_VERSION, "result": exc.code, "exit_code": 2, "error": str(exc)}
    except Exception as exc: result = {"schema": SCHEMA, "app": APP, "version": APP_VERSION, "result": "STOP_CONTRACT_UNSUPPORTED", "exit_code": 2, "error": f"{type(exc).__name__}: {exc}"}
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if len(payload.encode()) > MAX_OUTPUT_BYTES: payload = json.dumps({"schema": SCHEMA, "app": APP, "version": APP_VERSION, "result": "STOP_RESOURCE_OR_CANCELLED", "exit_code": 2, "error": "output exceeds 8 MiB"}, sort_keys=True) + "\n"
    try:
        with args.output.open("x", encoding="utf-8", newline="") as stream: stream.write(payload)
    except FileExistsError: return 2
    return int(result["exit_code"])


if __name__ == "__main__": raise SystemExit(main())
