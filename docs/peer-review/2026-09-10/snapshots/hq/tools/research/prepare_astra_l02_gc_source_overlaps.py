"""Bind2064 frozen L02 G/C owners to exact source overlap polygons, without a mesh."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from zipfile import ZipFile

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import Polygon, box
import project_astra_l14_gc_mass as mass

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
LEDGER = R / "astra-l02-gc-owner-ledger-01/result.json"
LEDGER_SHA = "69fb96a75b7bbe8a73d6704b237043272b7b1ca2da8c35880894205f606982b8"
BUNDLE = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\source_plane_ownership_candidate.spdpi")
MASS_SHA = "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def overlap(target, shell, holes, tree, external):
    if target.covers(external):
        return external
    cut = external if shell.covers(external) else shell.intersection(external)
    selected = tree.query(cut, predicate="intersects")
    return cut.difference(shapely.union_all(holes[selected])) if len(selected) else cut


def main(output):
    started = time.monotonic()
    # This exactly restricts the source polygon, including holes and outer boundaries.
    toy = Polygon(box(0, 0, 10, 10).exterior.coords, [box(2, 2, 4, 4).exterior.coords])
    shell, holes = Polygon(toy.exterior), np.array([Polygon(ring) for ring in toy.interiors], dtype=object)
    tree = STRtree(holes)
    for external in (box(3, 3, 6, 6), box(-1, -1, 3, 3), box(6, 6, 7, 7)):
        assert overlap(toy, shell, holes, tree, external).symmetric_difference(toy.intersection(external)).area == 0
    assert digest(LEDGER) == LEDGER_SHA and digest(Path(mass.__file__)) == MASS_SHA
    ledger = json.loads(LEDGER.read_bytes())
    assert ledger["status"] == "COMPLETED_L02_GC_SOURCE_EDGE_ASSET_LEDGER" and ledger["source_owner_edge_count"] == 2064
    assets = [ledger["target_geometry_asset"], *ledger["external_assets"]]
    assert len(assets) == 26
    asset_dir = output / "source-assets"
    asset_dir.mkdir()
    polygons, asset_receipts = {}, []
    with ZipFile(BUNDLE) as archive:
        for asset in assets:
            compressed = archive.read("attachments/" + asset["asset"])
            assert hashlib.sha256(compressed).hexdigest() == asset["asset_sha256"]
            path = asset_dir / Path(asset["asset"]).name
            with path.open("xb") as stream:
                stream.write(compressed)
            polygons[asset["asset_sha256"]] = mass.polygon_map(asset["layer"], asset["net"], asset["asset_sha256"], compressed)
            asset_receipts.append({**asset, "local_path": str(path.resolve()), "bytes": len(compressed)})
            assert time.monotonic() - started < 90
    target = polygons[ledger["target_geometry_asset"]["asset_sha256"]][ledger["target_island_id"]]
    assert target.geom_type == "Polygon" and target.is_valid
    shell = Polygon(target.exterior)
    holes = np.array([Polygon(ring) for ring in target.interiors], dtype=object)
    tree = STRtree(holes)
    shapely.prepare(target)
    shapely.prepare(shell)
    wkbs, rows, direct_checks = [], [], []
    selected_checks = {0, 1, 517, 1031, 1547, 2063}
    for index, owner in enumerate(ledger["owners"]):
        external = polygons[owner["external_geometry_asset"]["asset_sha256"]][owner["external_island_id"]]
        cut = overlap(target, shell, holes, tree, external)
        assert cut.is_valid and not cut.is_empty and cut.area > 0
        if index in selected_checks:
            direct = target.intersection(external)
            relative = cut.symmetric_difference(direct).area / cut.area
            assert relative < 1e-10
            direct_checks.append({"owner_index": index, "symmetric_difference_relative_area": relative})
        cap = owner["nominal_capacitance_f"]
        area = float(cut.area)
        wkbs.append(shapely.to_wkb(cut))
        rows.append({"owner_fingerprint": owner["fingerprint"], "area_um2": area, "nominal_capacitance_f": cap, "retained_native_density_f_per_um2": cap / area})
        if index % 128 == 0:
            print(json.dumps({"event": "source_overlap", "owners_completed": index + 1, "elapsed_s": time.monotonic() - started}), flush=True)
        assert time.monotonic() - started < 90
    offsets = np.r_[0, np.cumsum([len(wkb) for wkb in wkbs], dtype=np.int64)]
    data = np.frombuffer(b"".join(wkbs), dtype=np.uint8)
    assert offsets[-1] == len(data) and len(rows) == 2064
    arrays = output / "source-overlap-polygons.npz"
    mass.atomic_npz(arrays, wkb_bytes=data, wkb_offsets=offsets, owner_fingerprints_json_utf8=np.frombuffer(json.dumps([row["owner_fingerprint"] for row in rows]).encode(), dtype=np.uint8), overlap_area_um2=np.array([row["area_um2"] for row in rows]), nominal_capacitance_f=np.array([row["nominal_capacitance_f"] for row in rows]))
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_GC_SOURCE_OVERLAPS", "script_sha256": digest(Path(__file__)), "ledger_sha256": LEDGER_SHA,
              "polygon_helper_sha256": MASS_SHA, "target_island_id": ledger["target_island_id"], "target_hole_count": len(holes), "target_coordinate_count": int(shapely.get_num_coordinates(target)),
              "source_assets": asset_receipts, "owner_count": len(rows), "owners": rows, "direct_whole_polygon_checks": direct_checks,
              "output": {"path": str(arrays.resolve()), "sha256": digest(arrays), "bytes": arrays.stat().st_size}, "elapsed_s": time.monotonic() - started,
              "scope": "Exact source overlap support for the frozen2064 C owners; source polygons preserve holes. C/overlap-area densities retain the original native C and do not estimate or fit new capacitance. No mesh, quadrature mass, new current basis, G/C operator or finite response has been generated."}
    mass.atomic_json(output / "result.json", result)
    print(json.dumps({k: result[k] for k in ("status", "owner_count", "target_hole_count", "target_coordinate_count", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    main(output)
