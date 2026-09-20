"""One source trace/pad DC patch and its isolated native two-via boundary."""

from dataclasses import asdict
from hashlib import sha256
import json
from math import cos, pi
from pathlib import Path
import sqlite3
import sys
from time import monotonic
from types import SimpleNamespace

import numpy as np
from shapely.geometry import Point, box

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.solver.global_mna import DifferentialPort, SeriesBranchBlock, compile_global_mna
from spd_decap_pi._core.solver.tri_fem_sheet import FiniteSheetContact, TriFemSheetError, compile_tri_fem_sheet
from spd_decap_pi._core.via_model import classify_via_conductor, SOLID_COPPER_FILLED_MICROVIA

BASE = ROOT / "outputs/research/astra-step4-basis-01"
OUTPUT = ROOT / "docs/evaluation-research/astra_isolated_trace_patch_2026-09-06.json"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    started = monotonic()
    source_path = ROOT / "docs/evaluation-research/astra_source_trace_resistance_2026-09-06.json"
    source = source_path.read_bytes()
    assert sha256(source).hexdigest() == "180322bdace7d171ddc3ee395e94ae762ebb3a963491ec502cc119cb962b8275"
    trace = json.loads(source)["traces"]["Trace311318"]
    first, second = trace["nodes"]
    rail = trace["trace"]["net_fold"]
    with sqlite3.connect((BASE / "indexes/raw-spatial.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON"); db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        vias = [dict(r) for r in db.execute("SELECT * FROM vias WHERE net_fold=? AND via_id_fold IN ('via336273','via336274')", (rail,))]
        pads = [dict(r) for r in db.execute("SELECT * FROM pad_shapes WHERE padstack_id_fold IN ('dr-0506_60','dr-0607_60') AND layer_id=?", (trace["trace"]["layer_id"],))]
        stacks = [dict(r) for r in db.execute("SELECT * FROM padstacks WHERE padstack_id_fold IN ('dr-0506_60','dr-0607_60')")]
        layers = [dict(r) for r in db.execute("SELECT * FROM stackup_layers ORDER BY layer_ordinal")]
        surfaces = [dict(r) for r in db.execute("SELECT * FROM surfaces WHERE net_fold=? AND layer_id_fold=? LIMIT 2", (rail, trace["trace"]["layer_id_fold"]))]
        incident = [dict(r) for r in db.execute("SELECT * FROM traces WHERE net_fold=? AND (start_node_id_fold IN (?,?) OR end_node_id_fold IN (?,?)) LIMIT 3", (rail, first["node_id_fold"], second["node_id_fold"], first["node_id_fold"], second["node_id_fold"]))]
    assert len(vias) == len(pads) == len(stacks) == 2 and not surfaces
    assert len(incident) == 1 and incident[0] == trace["trace"]
    assert all(p["shape_kind"] == "CIRCLE" and p["width_pm"] == p["height_pm"] == 60000000 for p in pads)
    assert all(p["drill_diameter_pm"] == 40000000 and p["material"] == "COPPER" for p in stacks)
    classified = []
    layer_objects = [SimpleNamespace(name=l["layer_name"], is_conductor=l["layer_kind"] == "conductor", thickness_um=l["thickness_um"]) for l in layers]
    for via in vias:
        p = next(p for p in stacks if p["padstack_id_fold"] == via["padstack_id_fold"])
        c = classify_via_conductor(drill_diameter_um=p["drill_diameter_pm"] / 1e6, padstack_material=p["material"], start_layer=via["start_layer_id"], end_layer=via["end_layer_id"], stackup_layers=layer_objects)
        assert c.conductor_model == SOLID_COPPER_FILLED_MICROVIA
        classified.append(asdict(c))
    with sqlite3.connect((BASE / "indexes/compiled-topology.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON"); db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        refs = [dict(r) for r in db.execute("SELECT * FROM link_owners WHERE owner_id IN ('via:via336273','via:via336274')")]
        links = [dict(db.execute("SELECT * FROM links WHERE kind=? AND ordinal=?", (r["kind"], r["link_ordinal"])).fetchone()) for r in refs]
        assert len(links) == 2 and all(l["kind"] == 1 and l["parallel_count"] == 1 for l in links)
        common, = set.intersection(*[{l["first_node"], l["second_node"]} for l in links])
        incidence = [dict(r) for r in db.execute("SELECT * FROM links WHERE first_node=? OR second_node=? LIMIT 4", (common, common))]
        assert sorted(l["link_id"] for l in incidence) == sorted(l["link_id"] for l in links)
        node = dict(db.execute("SELECT * FROM nodes WHERE kind=0 AND ordinal=?", (common,)).fetchone())
        assert node["node_id"].startswith("spd-finite-via-vertex:")
        assert db.execute("SELECT COUNT(*) FROM rail_ports WHERE positive_node=? OR negative_node=?", (common, common)).fetchone()[0] == 0
    assert first["x_pm"] == second["x_pm"] and first["y_pm"] - second["y_pm"] == 60000000
    assert trace["trace"]["width_pm"] == 50000000
    # Rigid rotation/translation to local coordinates preserves the source geometry.
    # ponytail: fixed 64-sided circles; curved-boundary error is disclosed separately.
    centers = (Point(0, 0), Point(60e-6, 0))
    artwork = box(0, -25e-6, 60e-6, 25e-6).union(centers[0].buffer(30e-6, quad_segs=16)).union(centers[1].buffer(30e-6, quad_segs=16))
    electrodes = [FiniteSheetContact(f"barrel{i}", f"research:filled-microvia-section:{i}", p.buffer(20e-6, quad_segs=16)) for i, p in enumerate(centers)]
    rows = []
    for level in (0, 1, 2):
        try:
            sheet = compile_tri_fem_sheet("research:Trace311318-and-two-pads", artwork, contacts=electrodes,
                conductivity_s_per_m=trace["stackup_layer"]["conductivity_s_per_m"], thickness_m=trace["stackup_layer"]["thickness_um"] * 1e-6,
                refinement_levels=level, max_nodes=10000, max_triangles=20000, max_contacts=2, max_contact_work=500000)
            y = sheet.contact_admittance_s(0, model="dc")
            b = np.array([1., -1.])
            resistance = float((b @ np.linalg.pinv(y) @ b).real)
            norm = float(np.linalg.norm(y))
            assert resistance > 0 and np.linalg.norm(y @ np.ones(2)) < norm * 1e-10
            assert np.linalg.norm(y - y.T) < norm * 1e-10 and np.linalg.eigvalsh(y.real).min() >= -norm * 1e-10
            rows.append({"level": level, "nodes": len(sheet.mesh.node_xy_m), "triangles": len(sheet.mesh.triangles), "r_ohm": resistance, "operator_sha256": sheet.identity_sha256})
        except TriFemSheetError as exc:
            rows.append({"level": level, "status": "STOP", "code": exc.code, "detail": str(exc)})
            break
        if monotonic() - started > 55:
            raise TimeoutError("isolated source trace probe exceeded budget")
    complete = len(rows) == 3 and all("r_ohm" in r for r in rows)
    local = None
    if complete:
        frequency = 1e6
        impedances = [l["resistance_ohm"] + 2j * pi * frequency * l["inductance_h"] for l in links]
        owner_ids = tuple(r["owner_id"] for r in refs)
        baseline_blocks = [SeriesBranchBlock((l["link_id"],), ("left" if i == 0 else "joint",), ("joint" if i == 0 else "right",), np.array([[impedances[i]]]), "saved-native-via", owner_ids=(owner_ids[i],)) for i, l in enumerate(links)]
        shadow_blocks = [SeriesBranchBlock((l["link_id"],), ("left" if i == 0 else "pad1",), ("pad0" if i == 0 else "right",), np.array([[impedances[i]]]), "saved-native-via", owner_ids=(owner_ids[i],)) for i, l in enumerate(links)]
        shadow_blocks.append(SeriesBranchBlock(("Trace311318",), ("pad0",), ("pad1",), np.array([[rows[-1]["r_ohm"]]]), "source-trace-pad-dc", owner_ids=(trace["trace"]["owner_id"],)))
        ports = (DifferentialPort("isolated-boundary", "left", "right"),)
        before = compile_global_mna(("left", "joint", "right"), branch_blocks=baseline_blocks, ports=ports).solve(frequency).impedance_ohm[0, 0]
        after = compile_global_mna(("left", "pad0", "pad1", "right"), branch_blocks=shadow_blocks, ports=ports).solve(frequency).impedance_ohm[0, 0]
        assert abs(before - sum(impedances)) < 1e-10 and abs(after - before - rows[-1]["r_ohm"]) < 1e-10
        local = {"frequency_hz": frequency, "baseline_z_ohm": [before.real, before.imag], "shadow_z_ohm": [after.real, after.imag], "retained_via_owner_ids": owner_ids, "new_trace_owner": trace["trace"]["owner_id"], "native_gc_changed": False}
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "ACCEPT_LOCAL_TRACE_PATCH_ONLY" if complete else "STOP_LOCAL_TRACE_PATCH_MESH",
        "elapsed_s": monotonic() - started, "source_trace": trace, "via_records": vias, "pad_shapes": pads, "padstacks": stacks,
        "microvia_classifications": classified, "native_links": links, "native_owner_refs": refs, "native_degree_two_node": node,
        "sheet_rows": rows, "last_refinement_relative_change": abs(rows[-1]["r_ohm"] - rows[-2]["r_ohm"]) / rows[-1]["r_ohm"] if complete else None,
        "local_boundary_solve": local, "max_pad_circle_sagitta_um": 30 * (1 - cos(pi / 64)), "max_electrode_circle_sagitta_um": 20 * (1 - cos(pi / 64)),
        "scope": "Source 60 um trace plus two 60 um regular pads; injection uses 40 um copper-filled microvia sections under the existing user-confirmed qualified MLO assumption, not a source-proven generic fill flag or shrunken regular pads. Two-dimensional uniform-thickness DC sheet, finite equipotential barrel electrodes; 3D injection/skin/external-L/other-net fields absent. Local native degree-two boundary only. No full return/board, numeric G/C replacement, PowerSI comparison, or production acceptance."}
    OUTPUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in ("source_trace", "via_records", "pad_shapes", "padstacks", "native_links")}))


if __name__ == "__main__":
    main()
