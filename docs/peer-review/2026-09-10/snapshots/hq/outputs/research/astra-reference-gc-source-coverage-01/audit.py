"""Explain saved adjacent-gap coverage from pinned metadata; no geometry or solve."""
from pathlib import Path
from collections import Counter
from time import monotonic
import hashlib
import json
import sqlite3
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RESEARCH = HERE.parent
PINS = {
    "compiled": ("astra-step4-basis-01/indexes/compiled-topology.sqlite", "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"),
    "raw": ("astra-step4-basis-01/indexes/raw-spatial.sqlite", "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7"),
    "field": ("astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz", "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
}

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def read_db(path, query):
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        return db.execute(query).fetchall()

started = monotonic()
deadline = started + 20
assert not (HERE / "result.json").exists()
for relative, expected in PINS.values():
    assert digest(RESEARCH / relative) == expected, relative
surface = json.loads(read_db(RESEARCH / PINS["compiled"][0],
    "SELECT payload FROM views WHERE name='surface'")[0][0])
layers = [row[0] for row in read_db(RESEARCH / PINS["raw"][0],
    "SELECT layer_name FROM stackup_layers WHERE layer_kind='conductor' ORDER BY layer_ordinal")]
assets = surface["geometry_assets"]
counts = Counter(asset["layer"] for asset in assets)
island_identity = {}
for asset in assets:
    for island in asset["island_ids"]:
        assert island not in island_identity
        island_identity[island] = (asset["layer"], asset["net"])
expected_pairs = [(a, b) for a, b in zip(layers, layers[1:]) if a in counts and b in counts]
excluded_pairs = [(a, b) for a, b in zip(layers, layers[1:]) if a not in counts or b not in counts]
rows = []
with np.load(RESEARCH / PINS["field"][0], allow_pickle=False) as saved:
    for index in range(36):
        assert monotonic() < deadline
        prefix = f"partial_{index:02d}"
        def unpack(key):
            return json.loads(saved[prefix + key].tobytes())
        upper, lower = unpack("_upper_layer"), unpack("_lower_layer")
        names = unpack("_net_names")
        identities = [island_identity[name] for name in names]
        assert all(layer in (upper, lower) for layer, net in identities)
        data = saved[prefix + "_nominal_c_data"]
        indices = saved[prefix + "_nominal_c_indices"]
        indptr = saved[prefix + "_nominal_c_indptr"]
        shape = saved[prefix + "_nominal_c_shape"]
        assert tuple(shape) == (len(names), len(names))
        assert len(indices) == len(data) == indptr[-1]
        same_net_edges = 0
        edge_count = 0
        for column in range(len(names)):
            for k in range(int(indptr[column]), int(indptr[column + 1])):
                row = int(indices[k])
                if row >= column:
                    continue
                assert data[k] < 0 and identities[row][0] != identities[column][0]
                edge_count += 1
                same_net_edges += identities[row][1].casefold() == identities[column][1].casefold()
        rows.append(dict(index=index, upper=upper, lower=lower, island_count=len(names),
            edge_count=edge_count, same_net_edge_count=same_net_edges))
assert [(row["upper"], row["lower"]) for row in rows] == expected_pairs
assert len(layers) == 48 and len(counts) == 42 and len(expected_pairs) == 36 and len(excluded_pairs) == 11
isolated = sorted(layer for layer in counts if not any(layer in pair for pair in expected_pairs))
assert isolated == ["Signal$L04(DGND)", "Signal$L06(DGND)", "Signal$L18(DGND)"]
assert sum(row["same_net_edge_count"] for row in rows) > 0
sources = ["src/spd_decap_pi/_core/solver/layerwise_network.py",
           "src/spd_decap_pi/_core/solver/multilayer_capacitance.py"]
result = dict(program="SPD Decap PI Evaluator", version="0.23.1",
    status="PASS_SAVED_ADJACENT_GC_SOURCE_COVERAGE_AUDIT", audit_sha256=digest(Path(__file__)),
    inputs={key: dict(path=relative, sha256=sha) for key, (relative, sha) in PINS.items()},
    source_code_sha256={name: digest(ROOT / name) for name in sources},
    conductor_layer_count=len(layers), retained_artwork_layer_count=len(counts),
    retained_artwork_asset_count=len(assets), physical_adjacent_gap_count=len(layers)-1,
    saved_partial_count=len(rows), retained_layers_with_no_adjacent_partial=isolated,
    layers_without_retained_artwork=[layer for layer in layers if layer not in counts],
    excluded_physical_adjacent_pairs=excluded_pairs, saved_partials=rows,
    same_net_edge_count=sum(row["same_net_edge_count"] for row in rows),
    elapsed_s=monotonic()-started,
    scope="Metadata and saved CSC audit only. Production island extractor retains same-NET overlaps. L04 has no adjacent partial because L03/L05 lack retained artwork. Missing artwork does not mean absent copper or zero C/G. Trace/pad geometry, opening/fringing, same-layer and nonadjacent electric fields are not qualified by this audit. Existing projected G/C conserves the old model; it does not establish complete reference physics.")
with (HERE / "result.json").open("x", encoding="utf-8") as stream:
    json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
    stream.write("\n")
print(json.dumps({key: result[key] for key in ("status", "saved_partial_count", "same_net_edge_count", "elapsed_s")}))
