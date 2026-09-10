"""Join reviewed L02 source owners to drill supports without a mesh or circuit solve."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

import project_astra_l14_gc_mass as mass

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
TARGET = 349710
PINS = {
    "pad_result": (R / "astra-l02-pad-conductor-domain-01/result.json", "91e16302f7b7c6b5a43067a6208331f1aa1acee75440aa48ba035c02f1350a9d"),
    "pad_review": (R / "astra-l02-pad-conductor-domain-01/independent-review.json", "ac4f8ba26c1ad976d843fc0177e449f24523382498b21f7c65ad5cf5a5cae7f6"),
    "contacts": (R / "astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz", "c5c0dab9ac22d750aeecf3a4290c2b9ef9285bcf7ed33a5d8a7a2775c8430984"),
    "contact_review": (R / "astra-l02-source-contact-inputs-01/independent-review.json", "666463b00303314583df7c6413c803d417b01d94f680e96c9b0696ab0fb7499c"),
    "paths": (R / "astra-l02-composite-source-paths-03/result.json", "e534da4f465ecb081ae3ec0bcdf96a1ba5329bd4f5c1353b6dfa262ef4f858ff"),
    "paths_review": (R / "astra-l02-composite-source-paths-03/independent-review.json", "3ae31ad5e917cd6d207e90fdf654918fc8dfb51f31f1c20113529df4ed4c98be"),
    "ideal_baseline": (R / "astra-l02-ideal-junction-board-1mhz-01/result.json", "24b607363552baa6518f79f972335da378e833d852e8290c7659e07fba7b541c"),
    "ideal_review": (R / "astra-l02-ideal-junction-board-1mhz-01/independent-review.json", "658b13744c71efcaefbb3260b11d171570c6f49d181bc491ca29086dbb987b81"),
    "persistence": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(output):
    started = time.monotonic()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    pad = json.loads(PINS["pad_result"][0].read_bytes())
    support_meta = pad["outputs"]["support_map"]
    assert sha(Path(support_meta["path"])) == support_meta["sha256"]
    with np.load(support_meta["path"], allow_pickle=False) as data:
        support = {key: data[key] for key in data.files}
    with np.load(PINS["contacts"][0], allow_pickle=False) as data:
        contact = {key: data[key] for key in (
            "active_finite_index", "original_finite_index", "first_active_index", "second_active_index",
            "target_side", "source_via_ordinal", "x_pm", "y_pm", "native_count", "resistance_ohm", "inductance_h",
            "l02_endpoint_is_start", "l02_endpoint_node_ids_json_utf8", "via_ids_json_utf8")}
    drill = np.flatnonzero(support["support_is_drill"])
    assert len(drill) == 38856 and np.all(np.diff(drill) > 0)
    native = support["native_drill_support_index"]
    assert native.shape == (76139,) and len(np.unique(contact["active_finite_index"])) == len(native)
    assert np.array_equal(contact["active_finite_index"], support["native_active_finite_index"])
    assert np.array_equal(contact["source_via_ordinal"], support["native_source_via_ordinal"])
    via_ids = json.loads(contact["via_ids_json_utf8"].tobytes())
    endpoint_ids = json.loads(contact["l02_endpoint_node_ids_json_utf8"].tobytes())
    assert via_ids == json.loads(support["native_via_ids_json_utf8"].tobytes())
    assert len(via_ids) == len({name.casefold() for name in via_ids}) == len(endpoint_ids) == 76139
    assert all(isinstance(name, str) and name for name in endpoint_ids)
    assert contact["l02_endpoint_is_start"].shape == native.shape and contact["l02_endpoint_is_start"].dtype == np.bool_
    assert np.array_equal(contact["x_pm"], support["support_x_pm"][native])
    assert np.array_equal(contact["y_pm"], support["support_y_pm"][native])
    assert np.array_equal(contact["target_side"], np.where(contact["first_active_index"] == TARGET, 0, 1))
    assert np.all((contact["first_active_index"] == TARGET) ^ (contact["second_active_index"] == TARGET))
    ids = json.loads(support["excluded_via_ids_json_utf8"].tobytes())
    excluded = {name.casefold(): int(index) for name, index in zip(ids, support["excluded_drill_support_index"], strict=True)}
    assert len(excluded) == 42
    paths = json.loads(PINS["paths"][0].read_bytes())["links"]
    junctions, used = [], set()
    for link in paths:
        pair = link["l02_excluded_owner_ids"]
        assert len(pair) == 2 and not used.intersection(pair)
        used.update(pair)
        index = excluded[pair[0]]
        assert index == excluded[pair[1]] and support["support_is_drill"][index]
        for owner in pair:
            row = next(row for row in link["source_vias_in_native_path_order"] if row["via_id_fold"] == owner)
            sides = [side for side in ("start", "end") if row[side + "_node_id_fold"] == link["l02_junction_node_id_fold"]]
            assert len(sides) == 1
            side = sides[0]
            assert row[side + "_layer_id_fold"] == "signal$l02(dgnd)"
            assert (row[side + "_x_pm"], row[side + "_y_pm"]) == (support["support_x_pm"][index], support["support_y_pm"][index])
        assert TARGET not in link["active_endpoints"] and link["parallel_count"] == 1
        for kind in ("resistance_ohm", "inductance_h"):
            a, b = link["first_leg_" + kind], link["second_leg_" + kind]
            assert a > 0 and b > 0 and abs((a+b)-link[kind]) <= abs(link[kind])*1e-13
        junctions.append({"replaced_active_finite_index": link["active_finite_index"], "link_id": link["link_id"],
            "source_owner_count": len(link["source_vias_in_native_path_order"]), "source_owner_pair": pair,
            "source_vias_in_native_path_order": link["source_vias_in_native_path_order"],
            "source_path_node_ids_fold": link["source_path_node_ids_fold"],
            "source_ideal_trace_bridges": link["source_ideal_trace_bridges"],
            "ordered_owner_digest": link["ordered_owner_digest"],
            "l02_junction_node_id_fold": link["l02_junction_node_id_fold"],
            "l02_split_after_source_via_count": link["l02_split_after_source_via_count"],
            "drill_support_index": index, "contact_ordinal": int(np.searchsorted(drill, index)),
            "original_active_endpoints": link["active_endpoints"],
            **{name: link[name] for name in ("first_leg_resistance_ohm", "first_leg_inductance_h", "second_leg_resistance_ohm", "second_leg_inductance_h")}})
    assert len(junctions) == 20 and len(used) == 40
    assert len({row["replaced_active_finite_index"] for row in junctions}) == 20
    all_owners = [owner for row in junctions for owner in row["source_vias_in_native_path_order"]]
    assert len(all_owners) == len({owner["via_id_fold"] for owner in all_owners}) == 93
    assert all(owner["resistance_ohm"] > 0 and owner["inductance_h"] > 0 for owner in all_owners)
    junction_support = np.asarray([row["drill_support_index"] for row in junctions])
    assert len(np.unique(junction_support)) == 20
    assert not np.intersect1d(native, junction_support).size
    assert not np.intersect1d(contact["active_finite_index"], [row["replaced_active_finite_index"] for row in junctions]).size
    leaves = [{"source_via_id": name, "drill_support_index": index,
               "shares_native_contact_support": bool(np.any(native == index)), "circuit_policy": "retain original excluded leaf; add no circuit branch"}
              for name, index in excluded.items() if name not in used]
    assert {row["source_via_id"] for row in leaves} == {"via1312784", "via1612214"}
    assert all(row["shares_native_contact_support"] and row["drill_support_index"] not in junction_support for row in leaves)
    assert np.array_equal(np.unique(np.r_[native, junction_support]), drill)
    path = output / "circuit-contact-binding.npz"
    mass.atomic_npz(path, **contact, native_drill_support_index=native, contact_support_index=drill,
        native_contact_ordinal=np.searchsorted(drill, native),
        junctions_json_utf8=np.frombuffer(json.dumps(junctions, separators=(",", ":")).encode(), dtype=np.uint8))
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_SOURCE_L02_CIRCUIT_CONTACT_BINDING",
        "script_sha256": sha(Path(__file__)), "inputs": {name: {"path": str(p), "sha256": h} for name, (p,h) in PINS.items()},
        "support_map": support_meta, "native_via_count": len(native), "native_contact_count": len(np.unique(native)),
        "junction_count": len(junctions), "junction_source_via_owner_count": sum(row["source_owner_count"] for row in junctions),
        "all_drill_supports_bound_to_native_or_junction": True, "excluded_leaves": leaves, "junctions": junctions,
        "output": {"path": str(path), "sha256": sha(path)}, "elapsed_s": time.monotonic()-started,
        "scope": "Frequency-independent source mapping only. Native branch orientation/count/R/L preserved;20 original composites will be replaced by40 positive-RL legs at20 distinct new supports.2 leaves retain their original exclusion. Mesh contact ordering must equal the sorted support IDs. Future equipotential recollapse must recover the accepted corrected ideal-junction baseline, not untouched native. No mesh, circuit assembly, LU, magnetic model or accuracy claim."}
    mass.atomic_json(output / "result.json", result)
    print(json.dumps({key: result[key] for key in ("status", "native_via_count", "native_contact_count", "junction_count", "excluded_leaves", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        run(output)
    except BaseException as exc:
        mass.atomic_json(output / "failure.json", {"status": "STOP_L02_CIRCUIT_CONTACT_BINDING", "error_type": type(exc).__name__, "error": str(exc)})
        raise
