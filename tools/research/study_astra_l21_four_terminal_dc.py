"""Conditional four-terminal source L21 DC sheet; no native AC replacement."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from time import monotonic
from types import SimpleNamespace
import zlib

import numpy as np
from shapely.geometry import Point, Polygon

from probe_astra_dyadic_trace_sheet import ROOT, inward_dyadic
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.solver.tri_fem_sheet import FiniteSheetContact, TriFemSheetError, compile_tri_fem_sheet
from spd_decap_pi._core.via_model import classify_via_conductor, SOLID_COPPER_FILLED_MICROVIA


def database(name):
    path = ROOT / "outputs/research/astra-step4-basis-01/indexes" / name
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON"); db.execute("PRAGMA trusted_schema=OFF")
    deadline = monotonic() + 10
    db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
    return db


def main():
    output = ROOT / "docs/evaluation-research/astra_l21_four_terminal_dc_2026-09-07.json"
    if output.exists():
        raise FileExistsError(output)
    started = monotonic()
    source = (ROOT / "outputs/research/astra-step3-source-index-01/l21-plane-bridge.json").read_bytes()
    assert sha256(source).hexdigest() == "e4bbbc54e4f1708ad684605e41c5a17cb0dc7fcdd49c2794437196803c82ec01"
    bridge = json.loads(source)
    compressed = (ROOT / "outputs/research/astra-step5-l21-source-01/0219-b0ab5dda526415e8.spdgeom.zlib").read_bytes()
    assert sha256(compressed).hexdigest() == bridge["primitive"]["source_asset_sha256"]
    decoded = zlib.decompress(compressed)
    assert sha256(decoded).hexdigest() == "1185fdc52e9b395894cb5eca35698ee63e9509cc3efbb8c62ed3235925e5c9b0"
    geometry = json.loads(decoded)
    assert geometry["primitive_order"] == [["positive_polygon", 0]] and not geometry["negative_polygons_um"]
    assert geometry["positive_polygons_um"][0] == [[v["x_um"], v["y_um"]] for v in bridge["vertices"]]
    contacts = bridge["contacts"]
    rail, layer = geometry["net"].casefold(), geometry["layer"]
    via_ids = [c["via"]["via_id"].casefold() for c in contacts]
    with database("raw-spatial.sqlite") as db:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        nodes = [dict(r) for r in db.execute("SELECT * FROM nodes WHERE net_fold=? AND layer_id_fold=?", (rail, layer.casefold()))]
        assert {n["node_id"] for n in nodes} == {c["node"]["node_id"] for c in contacts}
        layers = [dict(r) for r in db.execute("SELECT * FROM stackup_layers ORDER BY layer_ordinal")]
        positions = {r["layer_name"]: r["layer_ordinal"] for r in layers}
        vias = [dict(r) for r in db.execute("SELECT * FROM vias WHERE net_fold=?", (rail,))]
        crossing = [v for v in vias if min(positions[v["start_layer_id"]], positions[v["end_layer_id"]]) <= positions[layer] <= max(positions[v["start_layer_id"]], positions[v["end_layer_id"]])]
        assert {v["via_id_fold"] for v in crossing} == set(via_ids), "additional intermediate-layer injection"
        assert db.execute("SELECT COUNT(*) FROM traces WHERE net_fold=? AND layer_id_fold=?", (rail, layer.casefold())).fetchone()[0] == 0
        padstacks = [dict(r) for r in db.execute("SELECT * FROM padstacks WHERE padstack_id_fold IN ('dr-2021_60','dr-2128_350')")]
        pads = [dict(r) for r in db.execute("SELECT * FROM pad_shapes WHERE padstack_id_fold IN ('dr-2021_60','dr-2128_350') AND layer_id=?", (layer,))]
        primitive = dict(db.execute("SELECT * FROM plane_primitives WHERE primitive_ordinal=?", (bridge["primitive"]["primitive_ordinal"],)).fetchone())
        assert primitive == bridge["primitive"]
    material, = [r for r in layers if r["layer_name"] == layer]
    assert material == bridge["stackup_rows"][0]
    layer_objects = [SimpleNamespace(name=r["layer_name"], is_conductor=r["layer_kind"] == "conductor", thickness_um=r["thickness_um"]) for r in layers]
    classifications, radii = [], []
    for index, contact in enumerate(contacts):
        via, = [v for v in crossing if v["via_id_fold"] == via_ids[index]]
        node, = [n for n in nodes if n["node_id"] == contact["node"]["node_id"]]
        assert via["source_record_sha256"] == contact["via"]["source_record_sha256"]
        assert node["source_record_sha256"] == contact["node"]["source_record_sha256"]
        pad, = [p for p in pads if p["padstack_id_fold"] == via["padstack_id_fold"]]
        assert pad["source_record_sha256"] == contact["pad_shape"]["source_record_sha256"]
        stack, = [p for p in padstacks if p["padstack_id_fold"] == via["padstack_id_fold"]]
        classification = classify_via_conductor(drill_diameter_um=stack["drill_diameter_pm"] / 1e6, padstack_material=stack["material"], start_layer=via["start_layer_id"], end_layer=via["end_layer_id"], stackup_layers=layer_objects)
        classifications.append(asdict(classification))
        if index == 0:
            assert via["via_id"] == "Via336239" and classification.conductor_model != SOLID_COPPER_FILLED_MICROVIA
            radii.append(pad["width_pm"] / 2e6)  # Explicit historical whole-core-pad equipotential approximation.
        else:
            assert classification.conductor_model == SOLID_COPPER_FILLED_MICROVIA
            radii.append(stack["drill_diameter_pm"] / 2e6)
    assert radii == [175., 20., 20., 20.]
    with database("compiled-topology.sqlite") as db:
        references = [dict(r) for r in db.execute("SELECT * FROM link_owners WHERE owner_id IN ('via:via336239','via:via336240','via:via336279','via:via336280')")]
        links = [dict(db.execute("SELECT * FROM links WHERE kind=? AND ordinal=?", (r["kind"], r["link_ordinal"])).fetchone()) for r in references]
        assert len(links) == 4 and all(l["kind"] == 1 for l in links)
        common, = set.intersection(*[{l["first_node"], l["second_node"]} for l in links])
        incidence = [dict(r) for r in db.execute("SELECT * FROM links WHERE first_node=? OR second_node=? LIMIT 8", (common, common))]
        assert common == 680823 and len(incidence) == 5 and sum(l["kind"] == 0 for l in incidence) == 1
    origin = (-11700., 12403.)
    polygon = Polygon([(x - origin[0], y - origin[1]) for x, y in geometry["positive_polygons_um"][0]])
    quantum = 2.**-20
    artwork = inward_dyadic(polygon, quantum)
    centers = [(c["node"]["x_pm"] / 1e6 - origin[0], c["node"]["y_pm"] / 1e6 - origin[1]) for c in contacts]
    basis = np.vstack((-np.ones((1, 3)), np.eye(3)))
    rows, failure = [], None
    for sides in (64, 128):
        electrodes = [inward_dyadic(Point(x, y).buffer(radius, quad_segs=sides // 4), quantum) for (x, y), radius in zip(centers, radii)]
        for level in ((0, 1, 2, 3) if sides == 64 else (2, 3)):
            try:
                sheet = compile_tri_fem_sheet("research:l21:conditional-four-terminal-dc:um", artwork,
                    contacts=[FiniteSheetContact(c["node"]["node_id"], "research:l21-injection:" + c["via"]["owner_id"], p) for c, p in zip(contacts, electrodes)],
                    conductivity_s_per_m=material["conductivity_s_per_m"], thickness_m=material["thickness_um"] * 1e-6,
                    refinement_levels=level, max_nodes=65000, max_triangles=130000, max_contacts=4, max_contact_work=8000000)
                y = sheet.contact_admittance_s(0, model="dc").real
                norm = np.linalg.norm(y)
                assert np.linalg.norm(y @ np.ones(4)) < norm * 1e-10 and np.linalg.norm(y - y.T) < norm * 1e-10
                assert np.linalg.eigvalsh(y).min() >= -norm * 1e-10
                z = basis.T @ np.linalg.pinv(y) @ basis
                pair_r = []
                for a in range(4):
                    for b in range(a + 1, 4):
                        current = np.eye(4)[a] - np.eye(4)[b]
                        pair_r.append(float(current @ np.linalg.pinv(y) @ current))
                rows.append({"circle_sides": sides, "level": level, "nodes": len(sheet.mesh.node_xy_m), "triangles": len(sheet.mesh.triangles),
                    "contact_node_order": [c["node"]["node_id"] for c in contacts], "y_s": y.tolist(), "balanced_z_ohm": z.tolist(), "pair_r_ohm": pair_r,
                    "operator_sha256": sheet.identity_sha256})
            except TriFemSheetError as exc:
                failure = {"circle_sides": sides, "level": level, "code": exc.code, "detail": str(exc)}
                break
            if monotonic() - started > 55:
                failure = {"code": "RESEARCH_TIME_BUDGET", "detail": "55 s work budget reached"}
                break
        if failure:
            break
    changes = {}
    if failure is None:
        for sides in (64, 128):
            a, b = [r for r in rows if r["circle_sides"] == sides][-2:]
            changes[f"mesh_{sides}_max_pair_relative"] = float(np.max(np.abs(np.array(b["pair_r_ohm"]) - a["pair_r_ohm"]) / b["pair_r_ohm"]))
        changes["circle_64_to_128_max_pair_relative"] = float(np.max(np.abs(np.array(rows[-1]["pair_r_ohm"]) - rows[3]["pair_r_ohm"]) / rows[-1]["pair_r_ohm"]))
    result = {"program": bridge["program"], "version": bridge["version"], "status": "ACCEPT_CONDITIONAL_FOUR_TERMINAL_DC_ONLY" if failure is None and all(v <= .02 for v in changes.values()) else "STOP_CONDITIONAL_FOUR_TERMINAL_DC",
        "source_bridge_sha256": sha256(source).hexdigest(), "source_asset_sha256": sha256(compressed).hexdigest(), "raw_basis_meta": meta,
        "material": material, "crossing_vias": crossing, "padstacks": padstacks, "pad_shapes": pads, "via_classifications": classifications,
        "electrode_radii_um": radii, "complete_source_scope": "All four AON source nodes and crossing via spans on L21; no source traces; one full positive polygon, no negative primitives.",
        "native_owner_refs": references, "native_incident_links": incidence, "native_ac_replacement_closed": False,
        "coordinate_origin_um": origin, "dyadic_quantum_um": quantum, "sheet_rows": rows, "relative_changes": changes, "gate": .02, "failure": failure,
        "balanced_basis": basis.tolist(), "elapsed_s": monotonic() - started,
        "scope": "Full floating four-terminal DC sheet under three qualified filled-microvia barrel electrodes and a separately retained whole-core-pad equipotential approximation. The core is not classified as filled; its 175 um pad electrode is not a plating/current-injection certification. Nominal source copper is preserved through inward geometric approximation; original whole-pad tangency STOP remains. Full Y and balanced Z are retained with no chosen current split. Native fifth ideal artwork link and its G/C distribution are unresolved for AC replacement. No external/mutual L, source plane replacement, production promotion, board Zii or PowerSI comparison."}
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "relative_changes", "failure", "elapsed_s")}))


if __name__ == "__main__":
    main()
