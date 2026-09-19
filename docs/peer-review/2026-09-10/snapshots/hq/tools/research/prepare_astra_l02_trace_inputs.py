"""Bind all saved L02 DGND traces to source endpoints and widths; no geometry union."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
import project_astra_l14_gc_mass as mass

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
DB = R / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
DB_SHA = "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7"
INVENTORY = R / "astra-l02-source-inventory-02/result.json"
INVENTORY_SHA = "0af954600a5070f8f2fa27a81ba941889b46ac9f26ac2b76d8669f4ce3af93f2"
MASS_SHA = "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(output):
    started = time.monotonic()
    for path, expected in ((DB, DB_SHA), (INVENTORY, INVENTORY_SHA), (Path(mass.__file__), MASS_SHA)):
        assert sha(path) == expected
    previous = json.loads(INVENTORY.read_bytes())
    db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    db.set_progress_handler(lambda: int(time.monotonic() - started > 60), 10000)
    try:
        meta = dict(db.execute("SELECT key,value FROM meta"))
        assert meta == previous["raw_index_meta"]
        rows = db.execute("SELECT t.ordinal,t.trace_id,t.owner_id,t.start_node_id,t.end_node_id,t.width_pm,t.width_status,t.geometry_status,t.source_record_sha256,a.x_pm,a.y_pm,b.x_pm,b.y_pm,a.source_record_sha256,b.source_record_sha256 FROM traces t LEFT JOIN nodes a ON a.node_id_fold=t.start_node_id_fold AND a.net_fold=t.net_fold AND a.layer_id_fold=t.layer_id_fold LEFT JOIN nodes b ON b.node_id_fold=t.end_node_id_fold AND b.net_fold=t.net_fold AND b.layer_id_fold=t.layer_id_fold WHERE t.net_fold=? AND t.layer_id_fold=? ORDER BY t.ordinal", ("dgnd", "signal$l02(dgnd)")).fetchall()
    finally:
        db.close()
    assert len(rows) == previous["whole_layer_net_source_trace_count"] == 38662
    assert len({row[0] for row in rows}) == len({row[1].casefold() for row in rows}) == len(rows)
    assert all(row[6:8] == ("EXACT", "EXACT") and all(v is not None for v in row[9:]) and row[5] > 0 for row in rows)
    xy = np.asarray([row[9:13] for row in rows], dtype=np.float64).reshape(-1, 2, 2) * 1e-6
    widths = np.array([row[5] for row in rows], dtype=np.float64) * 1e-6
    assert xy.shape == (38662, 2, 2) and widths.shape == (38662,) and np.isfinite(xy).all()
    lengths = np.linalg.norm(xy[:, 1] - xy[:, 0], axis=1)
    assert lengths.shape == (38662,) and np.all(np.isfinite(lengths)) and np.all(lengths > 0)
    packed = lambda value: np.frombuffer(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(), dtype=np.uint8)
    arrays = output / "source-trace-inputs.npz"
    mass.atomic_npz(arrays, source_trace_ordinal=np.array([row[0] for row in rows], dtype=np.int64), trace_ids_json_utf8=packed([row[1] for row in rows]), owner_ids_json_utf8=packed([row[2] for row in rows]), endpoint_node_ids_json_utf8=packed([row[3:5] for row in rows]), endpoint_xy_um=xy, width_um=widths, centerline_length_um=lengths, source_trace_row_sha256_json_utf8=packed([row[8] for row in rows]), source_endpoint_row_sha256_json_utf8=packed([row[13:15] for row in rows]))
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_EXACT_SOURCE_TRACE_INPUTS", "script_sha256": sha(Path(__file__)),
              "inputs": {"raw_db": {"path": str(DB), "sha256": DB_SHA}, "inventory": {"path": str(INVENTORY), "sha256": INVENTORY_SHA}, "persistence_helper_sha256": MASS_SHA},
              "raw_index_meta": meta, "layer": "Signal$L02(DGND)", "net": "DGND", "trace_count": len(rows), "unique_source_endpoint_node_count": len({node for row in rows for node in row[3:5]}),
              "width_um_counts": dict(sorted(Counter(widths.tolist()).items())), "total_centerline_length_um": float(lengths.sum()), "width_and_geometry_status": "EXACT",
              "output": {"path": str(arrays.resolve()), "sha256": sha(arrays), "bytes": arrays.stat().st_size}, "elapsed_s": time.monotonic() - started,
              "scope": "Exact saved source trace inputs and endpoint identities only, sorted by source trace ordinal. No artwork membership, trace interior/width contact, endcap semantics, pad union, conductor R/L, mesh or board replacement is certified. Whole-layer traces are not automatically all incident to one native surface node."}
    assert result["elapsed_s"] < 60
    mass.atomic_json(output / "result.json", result)
    print(json.dumps({k: result[k] for k in ("status", "trace_count", "unique_source_endpoint_node_count", "width_um_counts", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    main(output)
