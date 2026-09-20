"""Independent saved-artifact review of adjacent-gap G/C source coverage."""
from __future__ import annotations

import argparse
from hashlib import file_digest
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic
import zipfile

import numpy as np


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
PRODUCER_DIR = RESEARCH / "astra-reference-gc-source-coverage-01"
PINS = {
    "producer": (
        PRODUCER_DIR / "audit.py",
        "eb249149510af4335e925a90efe583bd3ca841e958fd39144dca54cf6f9a27d5",
    ),
    "result": (
        PRODUCER_DIR / "result.json",
        "4545b8bcbb81b24553cf4ca596a429852a0b678114fbdd3bb22a3135e0c1f594",
    ),
    "compiled": (
        RESEARCH / "astra-step4-basis-01/indexes/compiled-topology.sqlite",
        "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b",
    ),
    "raw": (
        RESEARCH / "astra-step4-basis-01/indexes/raw-spatial.sqlite",
        "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7",
    ),
    "field": (
        RESEARCH / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz",
        "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    ),
    "layerwise": (
        ROOT / "src/spd_decap_pi/_core/solver/layerwise_network.py",
        "dde4d562b47cc0103037b1efc5ff350a560cddffd53d9f3aaebc2579ad702b2b",
    ),
    "capacitance": (
        ROOT / "src/spd_decap_pi/_core/solver/multilayer_capacitance.py",
        "56b01319194ed0e8253eb1d60efed3e4ba5dcae34e22c8a70c19cebad6fc30b2",
    ),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def read_one(path: Path, query: str) -> object:
    deadline = monotonic() + 20.0
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10_000)
        row = db.execute(query).fetchone()
    if row is None or len(row) != 1:
        raise ValueError("source query did not return exactly one value")
    return row[0]


def unpack(archive: np.lib.npyio.NpzFile, key: str) -> object:
    return json.loads(archive[key].tobytes())


def decode_sql_json(value: object) -> object:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, (str, bytes, bytearray)):
        raise ValueError("SQL JSON value has an unsupported type")
    return json.loads(value)


def run(output: Path) -> dict[str, object]:
    started = monotonic()
    for label, (path, expected) in PINS.items():
        if sha256(path) != expected:
            raise ValueError(f"{label} SHA-256 changed")
    producer_result = json.loads(PINS["result"][0].read_text(encoding="utf-8"))
    if (
        producer_result.get("program") != PROGRAM
        or producer_result.get("version") != VERSION
        or producer_result.get("status") != "PASS_SAVED_ADJACENT_GC_SOURCE_COVERAGE_AUDIT"
        or producer_result.get("audit_sha256") != PINS["producer"][1]
    ):
        raise ValueError("producer result identity/status changed")
    for key in ("compiled", "raw", "field"):
        if producer_result["inputs"][key]["sha256"] != PINS[key][1]:
            raise ValueError(f"producer {key} input binding changed")
    for key in ("layerwise", "capacitance"):
        relative = str(PINS[key][0].relative_to(ROOT)).replace("\\", "/")
        if producer_result["source_code_sha256"][relative] != PINS[key][1]:
            raise ValueError(f"producer {key} source binding changed")

    layerwise_text = PINS["layerwise"][0].read_text(encoding="utf-8")
    capacitance_text = PINS["capacitance"][0].read_text(encoding="utf-8")
    sparse_start = capacitance_text.index(
        "def extract_sparse_adjacent_gap_island_capacitance("
    )
    sparse_stop = capacitance_text.index(
        "def extract_multilayer_bulk_capacitance(", sparse_start
    )
    sparse_source = capacitance_text[sparse_start:sparse_stop]
    caller_start = layerwise_text.index("source_model = capacitance_model_from_project(")
    caller_stop = layerwise_text.index("local_partials.extend(", caller_start)
    caller_source = layerwise_text[caller_start:caller_stop]
    if not (
        "island_resolved=True" in caller_source
        and "extract_sparse_adjacent_gap_island_capacitance(" in caller_source
        and "if model.enable_nonadjacent_opening_coupling:" in sparse_source
        and "pair = (upper_name, lower_name)" in sparse_source
        and "if upper_net == lower_net" not in sparse_source
    ):
        raise ValueError("production sparse island-resolved call contract changed")

    layers = decode_sql_json(
        read_one(
            PINS["raw"][0],
            "SELECT json_group_array(layer_name) FROM "
            "(SELECT layer_name FROM stackup_layers WHERE layer_kind='conductor' "
            "ORDER BY layer_ordinal)",
        )
    )
    trace_counts = {
        layer: int(
            read_one(
                PINS["raw"][0],
                "SELECT count(*) FROM traces WHERE layer_id_fold="
                + "'"
                + layer.casefold().replace("'", "''")
                + "'",
            )
        )
        for layer in (
            "Signal$L03(SIG1)",
            "Signal$L05(SIG2)",
            "Signal$L07(SIG3)",
            "Signal$L17(SIG4)",
            "Signal$L19(SIG5)",
            "Signal$BOTTOM",
        )
    }
    surface = decode_sql_json(
        read_one(
            PINS["compiled"][0],
            "SELECT payload FROM views WHERE name='surface'",
        )
    )
    assets = surface["geometry_assets"]
    retained_layers = {str(asset["layer"]) for asset in assets}
    island_identity: dict[str, tuple[str, str]] = {}
    for asset in assets:
        identity = (str(asset["layer"]), str(asset["net"]))
        for island in asset["island_ids"]:
            if island in island_identity:
                raise ValueError("surface island identity is duplicated")
            island_identity[str(island)] = identity
    expected_pairs = [
        (upper, lower)
        for upper, lower in zip(layers, layers[1:])
        if upper in retained_layers and lower in retained_layers
    ]
    excluded_pairs = [
        (upper, lower)
        for upper, lower in zip(layers, layers[1:])
        if upper not in retained_layers or lower not in retained_layers
    ]

    computed: list[dict[str, object]] = []
    maximum_symmetry = 0.0
    maximum_row_sum_relative = 0.0
    with zipfile.ZipFile(PINS["field"][0]) as package:
        if package.testzip() is not None:
            raise ValueError("saved field NPZ CRC failed")
    with np.load(PINS["field"][0], allow_pickle=False) as archive:
        prefixes = sorted(
            key.removesuffix("_upper_layer")
            for key in archive.files
            if re.fullmatch(r"partial_[0-9]{2}_upper_layer", key)
        )
        if prefixes != [f"partial_{index:02d}" for index in range(36)]:
            raise ValueError("saved partial prefix inventory changed")
        for ordinal, prefix in enumerate(prefixes):
            upper = str(unpack(archive, prefix + "_upper_layer"))
            lower = str(unpack(archive, prefix + "_lower_layer"))
            names = tuple(str(item) for item in unpack(archive, prefix + "_net_names"))
            data = np.asarray(archive[prefix + "_nominal_c_data"], dtype=np.float64)
            indices = np.asarray(archive[prefix + "_nominal_c_indices"], dtype=np.int64)
            indptr = np.asarray(archive[prefix + "_nominal_c_indptr"], dtype=np.int64)
            shape = tuple(int(item) for item in archive[prefix + "_nominal_c_shape"])
            if shape != (len(names), len(names)) or len(set(names)) != len(names):
                raise ValueError(f"{prefix} node layout changed")
            if (
                indptr.shape != (len(names) + 1,)
                or indptr[0] != 0
                or indptr[-1] != len(data)
                or len(indices) != len(data)
                or np.any(np.diff(indptr) < 0)
                or np.any(indices < 0)
                or np.any(indices >= len(names))
                or not np.all(np.isfinite(data))
            ):
                raise ValueError(f"{prefix} CSC arrays are malformed")
            columns = np.repeat(np.arange(len(names), dtype=np.int64), np.diff(indptr))
            entries = {
                (int(row), int(column)): float(value)
                for row, column, value in zip(indices, columns, data, strict=True)
            }
            if len(entries) != len(data):
                raise ValueError(f"{prefix} retains duplicate CSC coordinates")
            symmetry = max(
                (
                    abs(value - entries.get((column, row), float("nan")))
                    for (row, column), value in entries.items()
                ),
                default=0.0,
            )
            maximum_symmetry = max(maximum_symmetry, symmetry)
            scale = max(float(np.max(np.abs(data), initial=0.0)), np.finfo(float).tiny)
            row_sums = np.zeros(len(names), dtype=np.float64)
            np.add.at(row_sums, indices, data)
            row_sum_relative = float(
                np.max(np.abs(row_sums), initial=0.0) / scale
            )
            maximum_row_sum_relative = max(maximum_row_sum_relative, row_sum_relative)
            diagonal = indices == columns
            if (
                symmetry != 0.0
                or row_sum_relative > 2.0e-12
                or np.any(data[diagonal] <= 0.0)
                or np.any(data[~diagonal] >= 0.0)
            ):
                raise ValueError(f"{prefix} is not an exact passive Maxwell graph stamp")
            identity = [island_identity[name] for name in names]
            if any(layer not in (upper, lower) for layer, _net in identity):
                raise ValueError(f"{prefix} contains an island from another layer")
            undirected = (indices < columns) & ~diagonal
            edge_count = int(np.count_nonzero(undirected))
            same_net_count = 0
            for row, column in zip(indices[undirected], columns[undirected], strict=True):
                left, right = identity[int(row)], identity[int(column)]
                if left[0] == right[0]:
                    raise ValueError(f"{prefix} contains a same-layer capacitance edge")
                same_net_count += int(left[1].casefold() == right[1].casefold())
            computed.append(
                {
                    "index": ordinal,
                    "upper": upper,
                    "lower": lower,
                    "island_count": len(names),
                    "edge_count": edge_count,
                    "same_net_edge_count": same_net_count,
                }
            )

    isolated = sorted(
        layer
        for layer in retained_layers
        if not any(layer in pair for pair in expected_pairs)
    )
    gates = {
        "producer_and_inputs_hash_bound": True,
        "production_uses_island_resolved_sparse_extractor": True,
        "sparse_extractor_is_adjacent_only": True,
        "sparse_extractor_has_no_same_net_skip": True,
        "physical_conductor_count_48": len(layers) == 48,
        "physical_gap_count_47": len(layers) - 1 == 47,
        "retained_artwork_layers_42": len(retained_layers) == 42,
        "retained_artwork_assets_371": len(assets) == 371,
        "saved_partial_prefixes_36": len(computed) == 36,
        "saved_pairs_equal_all_retained_adjacent_pairs": [
            (row["upper"], row["lower"]) for row in computed
        ]
        == expected_pairs,
        "excluded_physical_pairs_11": len(excluded_pairs) == 11,
        "isolated_retained_layers_exact": isolated
        == ["Signal$L04(DGND)", "Signal$L06(DGND)", "Signal$L18(DGND)"],
        "saved_partial_rows_match_producer": computed
        == producer_result["saved_partials"],
        "same_net_edges_1790": sum(
            int(row["same_net_edge_count"]) for row in computed
        )
        == 1790,
        "producer_excluded_pairs_match": [list(pair) for pair in excluded_pairs]
        == producer_result["excluded_physical_adjacent_pairs"],
        "saved_csc_exactly_symmetric": maximum_symmetry == 0.0,
        "saved_csc_maxwell_row_sum": maximum_row_sum_relative <= 2.0e-12,
        "missing_artwork_layer_trace_counts_independently_match": trace_counts
        == {
            "Signal$L03(SIG1)": 14_295,
            "Signal$L05(SIG2)": 17_981,
            "Signal$L07(SIG3)": 14_365,
            "Signal$L17(SIG4)": 9_036,
            "Signal$L19(SIG5)": 9_030,
            "Signal$BOTTOM": 0,
        },
    }
    if not all(gates.values()):
        raise ValueError("saved adjacent-gap G/C review gate failed")

    output.mkdir(parents=True, exist_ok=False)
    frozen = Path(__file__).read_bytes()
    (output / "reviewer-at-run.py").write_bytes(frozen)
    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_SAVED_ADJACENT_GC_SOURCE_COVERAGE_INDEPENDENT_REVIEW",
        "reviewer_sha256": sha256(Path(__file__)),
        "inputs": {
            key: {"path": str(path), "sha256": expected}
            for key, (path, expected) in PINS.items()
        },
        "counts": {
            "physical_conductors": len(layers),
            "physical_adjacent_gaps": len(layers) - 1,
            "retained_artwork_layers": len(retained_layers),
            "retained_artwork_assets": len(assets),
            "saved_adjacent_partials": len(computed),
            "excluded_physical_adjacent_pairs": len(excluded_pairs),
            "same_net_edges_retained": sum(
                int(row["same_net_edge_count"]) for row in computed
            ),
        },
        "retained_layers_with_no_adjacent_partial": isolated,
        "source_trace_counts_on_layers_without_retained_artwork": trace_counts,
        "metrics": {
            "saved_csc_symmetry_max_abs_f": maximum_symmetry,
            "saved_csc_row_sum_max_relative_to_entry": maximum_row_sum_relative,
        },
        "gates": gates,
        "findings": [],
        "evidence": {
            "actual_caller": "layerwise_network.py:4103-4132 uses island_resolved=True and extract_sparse_adjacent_gap_island_capacitance",
            "sparse_contract": "multilayer_capacitance.py:578-607,696-938 retains every positive adjacent island overlap and rejects nonadjacent mode",
            "retained_blocks": "layerwise_network.py:997-1019 forms contiguous retained-artwork conductor blocks",
        },
        "limitations": [
            "No source geometry or capacitance was recomputed; this review checks saved CSC structure, metadata, hashes and the real production call contract.",
            "No retained artwork on a layer does not prove absent copper or zero G/C.",
            "The five omitted SIG artwork layers still contain 64,707 source trace rows in the pinned raw index; a reference rebuild must qualify trace/pad geometry rather than treat those layers as empty.",
            "The production sparse model is adjacent-only and does not qualify nonadjacent openings, fringing, coplanar fields or missing artwork.",
            "The 1,790 same-NET edges prove the production island-resolved path did not apply the dense bulk extractor's same-NET skip; they do not establish complete reference electrostatics.",
        ],
        "elapsed_s": monotonic() - started,
    }
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=PROGRAM)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review = run(args.output.resolve())
    print(
        json.dumps(
            {
                "status": review["status"],
                "counts": review["counts"],
                "elapsed_s": review["elapsed_s"],
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
