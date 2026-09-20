"""Independently match the 224 projected owners to saved source C edges."""

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy import sparse

from reconstruct_astra_native_loaded_field import _atomic_exclusive_json, _csc_from_snapshot, _sha256_file

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02"


def audit() -> dict:
    raw_path = RUN / "raw-field-snapshot.npz"
    inventory_path = RUN / "l14-gc-projection-inventory.json"
    assert _sha256_file(raw_path) == "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"
    assert _sha256_file(inventory_path) == "4409cdccc3b3d488d8f3abdd2f1e8b09a488d3c18a5bace010c765a240bc397f"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    results = []
    with np.load(raw_path, allow_pickle=False) as raw:
        for partial in inventory["mapping"]["partials"]:
            prefix = partial["npz_prefix"]
            names = json.loads(raw[f"{prefix}_net_names"].tobytes().decode("utf-8"))
            index = {name: i for i, name in enumerate(names)}
            assert len(index) == len(names)
            matrix = _csc_from_snapshot(raw, prefix)
            assert (matrix - matrix.T).nnz == 0
            upper = sparse.triu(matrix, k=1).tocoo()
            edges = {(int(i), int(j)): -float(c) for i, j, c in zip(upper.row, upper.col, upper.data, strict=True)}
            assert all(c > 0 for c in edges.values())
            removed = {}
            for owner in partial["owners"]:
                pair = tuple(sorted((index[owner["upper_island_id"]], index[owner["lower_island_id"]])))
                assert pair not in removed, "duplicate source owner edge"
                expected = float.fromhex(owner["capacitance_f_hex"])
                assert edges[pair] == expected, "source C does not match owner bit-for-bit"
                removed[pair] = expected
            retained = {pair: c for pair, c in edges.items() if pair not in removed}
            assert len(retained) == partial["retained_nonincident_source_edge_count"]
            target_indices = {index[owner["l14_island_id"]] for owner in partial["owners"]}
            assert not any(i in target_indices or j in target_indices for i, j in retained)
            rows, cols, data = [], [], []
            for (i, j), c in edges.items():
                rows.extend((i, j, i, j)); cols.extend((i, j, j, i)); data.extend((c, c, -c, -c))
            restamped = sparse.coo_matrix((data, (rows, cols)), shape=matrix.shape).tocsc()
            delta = (restamped - matrix).tocoo()
            error = float(np.max(np.abs(delta.data), initial=0.0))
            scale = float(np.max(np.abs(matrix.data)))
            assert error <= scale * 1e-13, "source partial is not the recorded edge Laplacian"
            results.append({"partial": prefix, "original_edges": len(edges), "removed_owner_edges": len(removed), "retained_nonincident_edges": len(retained), "owner_capacitance_bitwise_match": True, "retained_target_incidence": 0, "edge_restamp_max_error_f": error, "edge_restamp_relative_error": error / scale})
    assert [x["removed_owner_edges"] for x in results] == [110, 114]
    assert [x["retained_nonincident_edges"] for x in results] == [336, 412]
    return {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "VERIFIED_ORIGINAL_L14_GC_SOURCE_EDGE_PARTITION", "scope": "Original nominal C-edge partition only; no new mass matrix, solve, or accuracy claim.", "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "partials": results}


if __name__ == "__main__":
    result = audit()
    _atomic_exclusive_json(RUN / "hq-gc-source-edge-partition.json", result)
    print(json.dumps(result, separators=(",", ":")))
