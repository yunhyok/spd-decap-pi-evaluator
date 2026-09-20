"""Add source L04 pads to the saved flat domain under the existing external guard.

The launcher supplies a fresh output directory containing driver-at-run.py.
Drill footprints remain separate geometry; no electrode or current law is chosen.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
import shapely
from shapely import STRtree

import prepare_astra_l02_pad_conductor_domain as pads
import project_astra_l14_gc_mass as mass
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    "contact_result": (R / "astra-l04-source-contact-inputs-01/result.json", "96c7396b9c4d8cba85677ca18e643bad1376e7f3047614c5a9b158071662861d"),
    "contact_inputs": (R / "astra-l04-source-contact-inputs-01/contact-ledger-inputs.npz", "6e5886f81a14c1f1fec43a4af64a175bca518486cfd74fbc7794c551bd74ec4a"),
    "contact_review": (R / "astra-l04-source-contact-inputs-review-01/independent-review.json", "3b144bee81a2ef83ed906968cf91240176e54f4f93d9e2e851cb5e58b9875240"),
    "pad_helper": (Path(pads.__file__), "8acd1493a99124f30eb5a3a35526cccfb85e98263d3b4f05e50e5d1fa0028f14"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "placed_pad_geometry": pads.PINS["placed_pad_geometry"],
    "persistence_helper": pads.PINS["persistence_helper"],
}
MAX_SECONDS = 300.0


def require(value, message):
    if not value:
        raise ValueError(message)


def receipt(path):
    path = Path(path)
    return {"path": str(path.resolve()), "sha256": pads.digest(path), "size_bytes": path.stat().st_size}


def verify(path, digest):
    require(pads.digest(Path(path)) == digest, f"SHA-256 differs: {path}")
    return receipt(path)


def source_supports():
    document = json.loads(PINS["contact_result"][0].read_bytes())
    require(document["status"] == "COMPLETED_L04_SOURCE_CONTACT_INPUT_LEDGER", "contact ledger status")
    definitions = sorted(document["pad_definitions"], key=lambda item: item["padstack_index"])
    require([item["padstack_index"] for item in definitions] == list(range(4)), "pad definition order")
    pad_diameter = np.asarray([item["l04_pad_shape"]["width_pm"] for item in definitions], dtype=np.int64)
    drill_diameter = np.asarray([item["padstack"]["drill_diameter_pm"] for item in definitions], dtype=np.int64)
    require(all(item["l04_pad_shape"]["shape_kind"] == "CIRCLE"
                and item["l04_pad_shape"]["height_pm"] == item["l04_pad_shape"]["width_pm"] for item in definitions)
            and np.all(pad_diameter > drill_diameter) and np.all(drill_diameter > 0), "source circle definitions")
    with np.load(PINS["contact_inputs"][0], allow_pickle=False) as data:
        x, y, kind = data["x_pm"], data["y_pm"], data["padstack_index"]
        retained = {name: data[name] for name in (
            "category", "source_via_ordinal", "via_ids_json_utf8", "coincident_group_index",
            "active_finite_index", "original_finite_index", "expanded_branch_index", "junction_array_ordinal",
            "l02_contact_ordinal", "original_l04_target_side", "l04_endpoint_is_start",
            "original_first_active_index", "original_second_active_index")}
        retained["source_contact_row_index"] = np.arange(len(x), dtype=np.int64)
    require(x.shape == y.shape == kind.shape == (76166,) and np.all((kind >= 0) & (kind < 4)), "contact array shapes")
    require(np.bincount(retained["category"]).tolist() == [76136, 10, 20], "source category partition")
    all_pad = [(int(a), int(b), int(pad_diameter[c])) for a, b, c in zip(x, y, kind, strict=True)]
    all_drill = [(int(a), int(b), int(drill_diameter[c])) for a, b, c in zip(x, y, kind, strict=True)]
    keys, pad_map, drill_map = pads.support_table(all_pad, all_drill)
    require(all(keys[int(index)] == key for index, key in zip(pad_map, all_pad, strict=True))
            and all(keys[int(index)] == key for index, key in zip(drill_map, all_drill, strict=True)), "exact support mapping")
    return keys, pad_map, drill_map, retained


def coverage(domain, geometries, budget, label):
    strict = np.asarray(shapely.covers(domain, geometries), dtype=np.bool_)
    residue = np.zeros(len(geometries), dtype=np.float64)
    for count, index in enumerate(np.flatnonzero(~strict)):
        residue[index] = geometries[index].difference(domain).area
        if count % 128 == 0:
            budget.check(label)
    budget.check(label)
    return strict, residue


def run(args):
    output = args.output.resolve()
    frozen = output / "driver-at-run.py"
    require(output.is_dir() and frozen.is_file() and pads.digest(frozen) == pads.digest(Path(__file__)), "fresh guarded worker directory")
    require(not (output / "result.json").exists() and not (output / "l04-pad-augmented-conductor-domain.wkb").exists(), "refusing existing result")
    budget = recon._Budget.create(MAX_SECONDS, 8)
    deadline = time.monotonic() + MAX_SECONDS
    try:
        inputs = {name: verify(path, digest) for name, (path, digest) in PINS.items()}
        inputs["flat_result"] = verify(args.flat_result, args.flat_sha256)
        inputs["flat_guard"] = verify(args.flat_result.parent / "external-budget.json", args.flat_guard_sha256)
        flat_result = json.loads(args.flat_result.read_bytes())
        guard = json.loads((args.flat_result.parent / "external-budget.json").read_bytes())
        require(flat_result["status"] == "COMPLETED_L04_FLAT_TRACE_ISLAND_DOMAIN_CANDIDATE"
                and guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0, "flat candidate completion")
        for name in ("raw_union", "mapping", "driver"):
            inputs["flat_" + name] = verify(flat_result[name]["path"], flat_result[name]["sha256"])
        flat = shapely.from_wkb(Path(flat_result["raw_union"]["path"]).read_bytes())
        require(flat.is_valid and flat.geom_type in ("Polygon", "MultiPolygon"), "flat domain geometry")
        keys, pad_map, drill_map, retained = source_supports()
        pad_support, drill_support = np.unique(pad_map), np.unique(drill_map)
        polygons = np.empty(len(keys), dtype=object)
        for start in range(0, len(keys), 2048):
            polygons[start:start + 2048] = [pads.placed_circle(key) for key in keys[start:start + 2048]]
            budget.check("source circle construction")
        shapely.prepare(flat)
        already_covered = np.asarray(shapely.covers(flat, polygons[pad_support]), dtype=np.bool_)
        added_support = pad_support[~already_covered]
        # ponytail: skip only strictly covered pads; every other source pad enters the union.
        domain = pads.union_batched([flat, *polygons[added_support]], deadline, "l04_new_pad_material")
        domain_path = output / "l04-pad-augmented-conductor-domain.wkb"
        pads.atomic_bytes(domain_path, shapely.to_wkb(domain))
        budget.check("pad-augmented raw domain checkpoint")
        drill_union = pads.union_batched(polygons[drill_support], deadline, "l04_source_drills")
        drill_path = output / "l04-source-drill-footprint-union.wkb"
        pads.atomic_bytes(drill_path, shapely.to_wkb(drill_union))
        budget.check("source drill union checkpoint")
        shapely.prepare(domain)
        pad_strict, pad_residue = coverage(domain, polygons[pad_support], budget, "source pad coverage")
        drill_strict, drill_residue = coverage(domain, polygons[drill_support], budget, "source drill coverage")
        parts = pads.parts(domain)
        tree = STRtree(parts)
        support_component = np.full(len(keys), -1, dtype=np.int32)
        for count, index in enumerate(np.unique(np.r_[pad_support, drill_support])):
            matches = tree.query(polygons[index].representative_point(), predicate="within")
            require(len(matches) == 1, "source pad/drill has no unique material component")
            support_component[index] = matches[0]
            if count % 1024 == 0:
                budget.check("source contact component mapping")
        require(np.array_equal(support_component[pad_map], support_component[drill_map]), "concentric source pad/drill component mismatch")
        flat_parts = pads.parts(flat)
        flat_component = []
        for part in flat_parts:
            matches = tree.query(part.representative_point(), predicate="within")
            require(len(matches) == 1, "flat component has no unique pad-domain component")
            flat_component.append(int(matches[0]))
        key_array = np.asarray(keys, dtype=np.int64)
        mapping_path = output / "l04-pad-drill-support-map.npz"
        mass.atomic_npz(mapping_path, support_xy_pm=key_array[:, :2], support_diameter_pm=key_array[:, 2],
                        source_pad_support_index=pad_map, source_drill_support_index=drill_map,
                        pad_support_index=pad_support, drill_support_index=drill_support,
                        pad_already_strictly_covered_by_flat=already_covered,
                        pad_strictly_covered=pad_strict, pad_uncovered_area_um2=pad_residue,
                        drill_strictly_covered=drill_strict, drill_uncovered_area_um2=drill_residue,
                        pad_support_component_index=support_component[pad_support],
                        drill_support_component_index=support_component[drill_support],
                        source_contact_component_index=support_component[pad_map],
                        source_drill_component_index=support_component[drill_map],
                        flat_component_to_pad_domain_component_index=np.asarray(flat_component, dtype=np.int32), **retained)
        budget.check("source support mapping checkpoint")
        flat_covered = bool(domain.covers(flat))
        flat_residue_area = 0.0 if flat_covered else float(flat.difference(domain).area)
        result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": "COMPLETED_L04_PAD_DOMAIN_CANDIDATE", "driver": receipt(frozen), "inputs": inputs,
                  "source_rows": 76166, "unique_pad_supports": len(pad_support), "unique_drill_supports": len(drill_support),
                  "pad_supports_already_strictly_covered_by_flat": int(already_covered.sum()), "new_pad_union_inputs": len(added_support),
                  "domain": pads.boundary_stats(domain), "drill_union": pads.boundary_stats(drill_union),
                  "strict_pad_coverage_count": int(pad_strict.sum()), "strict_drill_coverage_count": int(drill_strict.sum()),
                  "pad_uncovered_area_sum_um2": float(pad_residue.sum()), "drill_uncovered_area_sum_um2": float(drill_residue.sum()),
                  "strict_flat_domain_coverage": flat_covered, "flat_uncovered_area_um2": flat_residue_area,
                  "row_provenance": "source_contact_row_index indexes the exact pinned contact_inputs NPZ; its source_rows_json_utf8 and path_maps_json_utf8 retain all source-record hashes and path ownership without duplicating their JSON. Compact native/split/endpoint identity arrays are copied in source row order.",
                  "outputs": {"domain": receipt(domain_path), "drill_union": receipt(drill_path), "support_map": receipt(mapping_path)},
                  "budget": budget.receipt(),
                  "scope": "Conditional flat traces plus all source 256-edge inscribed circle pads. Every source pad/drill row remains mapped. Separate material components and GEOS coverage residues are preserved without repair, snapping or renormalization. Drill footprints are not subtracted and are not yet electrodes. No mesh, circuit rewrite, current reconstruction, G/C or magnetic/PowerSI result."}
        budget.check("final domain statistics")
        result["budget"] = budget.receipt()
        mass.atomic_json(output / "result.json", result)
        print(json.dumps({key: result[key] for key in ("status", "new_pad_union_inputs", "strict_pad_coverage_count", "strict_drill_coverage_count", "budget")}), flush=True)
    except BaseException as error:
        mass.atomic_json(output / "failure.json", {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                         "status": "STOP_L04_PAD_DOMAIN_CANDIDATE", "error_type": type(error).__name__, "error": str(error),
                         "driver": receipt(frozen), "budget": budget.receipt()})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flat-result", type=Path, required=True)
    parser.add_argument("--flat-sha256", required=True)
    parser.add_argument("--flat-guard-sha256", required=True)
    run(parser.parse_args())
