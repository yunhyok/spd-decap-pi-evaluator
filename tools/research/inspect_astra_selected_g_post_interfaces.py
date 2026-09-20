"""SPD Decap PI Evaluator v0.23.1: saved selected-G post/interface census.

This reads only frozen ledgers and mesh artifacts.  It identifies reusable
source-model interfaces without grounding, closing, or deleting continuations.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
import shapely


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    "outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json": "ec6a4771c2427af09ab6d7588973f428a60f7c2cea53f75046644499f4f7e3ac",
    "outputs/research/astra-device-top-artwork-contacts-01/artwork-contact-ledger.json": "1fee5ba69576be25dc0579181b10945abd1c877cf03e803e3b2ae08d5b96a38e",
    "outputs/research/astra-device-l02-ground-contacts-01/lower-contact-ledger.json": "d48824f9fd6421742eb3abe0e00afcac649c2f581cdd6aead042f6769cf83b8c",
    "outputs/research/astra-device-l02-trace-contacts-01/trace-contact-ledger.json": "d0d947bbfb270b4e8f51fc836cd58d9de701216693cd9e27d32d0ab3da459135",
    "outputs/research/astra-device-l02-via-contacts-03/lower-via-contact-ledger.json": "7d2477d09ab5e50cbea2397eeb19385389c7722fdd05d682987c08736a02affa",
    "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz": "5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz": "3f8b47b136bc32c8dcba503911de42495853c8b4c24f2962daee957b3d643975",
    "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz": "b169d79ce8fc61c141efa572ce2aa2c4905fc5efa3905cac139dd07efc165538",
}


def _read(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _counter_json(values):
    return {str(k): int(v) for k, v in sorted(Counter(values).items(), key=lambda x: str(x[0]))}


def _line_signature(geometry, center):
    parts = []
    endpoints = []
    for part in shapely.get_parts(geometry):
        if part.length <= 1e-8:
            continue
        xy = np.asarray(part.coords, dtype=float) - center
        q = tuple((round(float(x), 9), round(float(y), 9)) for x, y in xy)
        parts.append(min(q, tuple(reversed(q))))
        endpoints.extend((xy[0], xy[-1]))
    # Artificial LineString cuts have even endpoint degree.  The two odd
    # endpoints are the physical contact/complement transitions.
    clusters = []
    for p in endpoints:
        for c in clusters:
            if np.linalg.norm(p - c[0]) < 1e-7:
                c[1] += 1
                break
        else:
            clusters.append([p.copy(), 1])
    transitions = sorted(
        ((float(c[0][0]), float(c[0][1])) for c in clusters if c[1] % 2),
        key=lambda p: (p[0], p[1]),
    )
    assert len(transitions) == 2
    return tuple(sorted(parts)), transitions


def run(output: Path):
    start = monotonic()
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for path, expected in PINS.items():
            assert sha256((ROOT / path).read_bytes()).hexdigest() == expected, path

        terminal = _read(next(iter(PINS)))
        pads = [p for p in terminal["pads"] if p["role"] == "ground"]
        assert len(pads) == 978
        pad_by_pin = {p["pin_id"]: p for p in pads}
        pad_by_node = {p["source_node_id"]: p for p in pads}
        assert len(pad_by_pin) == len(pad_by_node) == 978
        assert all(p["net"].casefold() == "dgnd" and p["diameter_pm"] == 100_000_000 for p in pads)

        top = _read("outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json")
        top_pad = {p["pin_id"]: p for p in top["pads"] if p["role"] == "ground"}
        top_trace = {int(t["ordinal"]): t for t in top["traces"]}
        assert len(top_pad) == 978 and not top["unresolved"] and not top["foreign_net_intersections"]
        top_incidence = defaultdict(list)
        for p in pads:
            for ordinal in top_pad[p["pin_id"]]["side_contact_trace_ordinals"]:
                top_incidence[int(ordinal)].append(p["pin_id"])
        assert len(top_incidence) == 1097

        top_rows = []
        graph = {p["pin_id"]: set() for p in pads}
        for ordinal, member_pins in sorted(top_incidence.items()):
            trace = top_trace[ordinal]
            assert trace["net_fold"] == "dgnd" and trace["layer_id"] == "Signal$TOP"
            assert trace["width_pm"] == 45_000_000 and trace["sy"] == trace["ey"]
            assert abs(trace["sx"] - trace["ex"]) == 130_000_000
            assert len(member_pins) in (1, 2)
            selected_nodes = {pad_by_pin[p]["source_node_id"] for p in member_pins}
            endpoint_nodes = {trace["start_node_id"], trace["end_node_id"]}
            assert selected_nodes <= endpoint_nodes and len(selected_nodes) == len(member_pins)
            endpoint_records = []
            for pin in member_pins:
                pad = pad_by_pin[pin]
                center = np.array([pad["x_pm"], pad["y_pm"]], dtype=np.int64)
                if trace["start_node_id"] == pad["source_node_id"]:
                    here = np.array([trace["sx"], trace["sy"]], dtype=np.int64)
                    there = np.array([trace["ex"], trace["ey"]], dtype=np.int64)
                else:
                    here = np.array([trace["ex"], trace["ey"]], dtype=np.int64)
                    there = np.array([trace["sx"], trace["sy"]], dtype=np.int64)
                assert np.array_equal(here, center)
                delta = (there - center) // 1_000_000
                assert tuple(delta) in ((-130, 0), (130, 0))
                endpoint_records.append(dict(pin_id=pin, outward_side=int(np.sign(delta[0])), delta_um=delta.tolist()))
            if len(member_pins) == 2:
                a, b = member_pins
                graph[a].add(b)
                graph[b].add(a)
            top_rows.append(dict(
                ordinal=ordinal, trace_id=trace["trace_id"], source_record_sha256=trace["source_record_sha256"],
                start_node_id=trace["start_node_id"], end_node_id=trace["end_node_id"],
                start_node_sha256=trace["start_node_sha256"], end_node_sha256=trace["end_node_sha256"],
                selected_endpoint_count=len(member_pins), disposition="INTERNAL_SELECTED_G" if len(member_pins) == 2 else "RETAINED_EXTERNAL_CONTINUATION",
                endpoints=endpoint_records, length_um=130.0, width_um=45.0,
            ))
        assert Counter(len(v) for v in top_incidence.values()) == Counter({2: 804, 1: 293})

        components = []
        seen = set()
        artwork = _read("outputs/research/astra-device-top-artwork-contacts-01/artwork-contact-ledger.json")
        artwork_pad = {p["pin_id"]: p for p in artwork["pads"] if p["role"] == "ground"}
        assert len(artwork_pad) == 978 and not artwork["foreign_net_intersections"]
        assert [a["sha256"] for a in artwork["source_artwork"] if a["net"].casefold() == "dgnd"] == ["525516ead1fe33ec08320ae04c763c75b4b58ae5a1e1cfe6829744afc1e4993f"]
        external_per_pad = Counter(ep["pin_id"] for row in top_rows if row["selected_endpoint_count"] == 1 for ep in row["endpoints"])
        for seed in sorted(pad_by_pin):
            if seed in seen:
                continue
            queue = deque([seed]); seen.add(seed); members = []
            while queue:
                u = queue.popleft(); members.append(u)
                for v in graph[u]:
                    if v not in seen:
                        seen.add(v); queue.append(v)
            edge_count = sum(len(graph[u]) for u in members) // 2
            external_count = sum(external_per_pad[u] for u in members)
            artwork_count = sum(bool(artwork_pad[u]["source_artwork_polygon_count"]) for u in members)
            assert edge_count == len(members) - 1 and max((len(graph[u]) for u in members), default=0) <= 2
            components.append(dict(component_id=len(components), member_pins=sorted(members), member_count=len(members),
                internal_trace_count=edge_count, retained_external_trace_count=external_count,
                retained_artwork_member_count=artwork_count))
        assert len(components) == 174 and sum(c["internal_trace_count"] for c in components) == 804

        artwork_rows = []
        signature_to_members = defaultdict(list)
        for pin, row in sorted(artwork_pad.items()):
            if not row["source_artwork_polygon_count"]:
                continue
            trace_geometry = shapely.from_wkb(bytes.fromhex(top_pad[pin]["round_contact_wkb_hex"]))
            combined = shapely.from_wkb(bytes.fromhex(row["combined_trace_artwork_contact_wkb_hex"]))
            contact = combined.difference(trace_geometry)
            assert abs(contact.length - row["artwork_contact_length_um"]) < 1e-8
            pad = pad_by_pin[pin]
            center = np.array([pad["x_pm"], pad["y_pm"]], dtype=float) / 1e6
            signature, transitions = _line_signature(contact, center)
            key = json.dumps(signature, separators=(",", ":"))
            signature_to_members[key].append(pin)
            artwork_rows.append(dict(pin_id=pin, local_signature_key=key,
                contact_length_um=float(contact.length), component_count=len([q for q in shapely.get_parts(contact) if q.length > 1e-8]),
                transition_points_local_um=transitions, artwork_only_contact_wkb_hex=shapely.to_wkb(contact).hex(),
                source_artwork_polygon_count=row["source_artwork_polygon_count"],
                disposition="RETAINED_DGND_ARTWORK_CONTINUATION"))
        assert len(artwork_rows) == 36 and len(signature_to_members) == 14
        family_keys = sorted(signature_to_members, key=lambda k: (-len(signature_to_members[k]), k))
        family_id = {k: i for i, k in enumerate(family_keys)}
        for row in artwork_rows:
            row["family_id"] = family_id[row.pop("local_signature_key")]
        artwork_families = [dict(family_id=i, member_pins=sorted(signature_to_members[k]), member_count=len(signature_to_members[k]),
            local_polyline_signature=json.loads(k)) for i, k in enumerate(family_keys)]

        lower = _read("outputs/research/astra-device-l02-ground-contacts-01/lower-contact-ledger.json")
        lower_pad = {p["pin_id"]: p for p in lower if p["role"] == "ground"}
        assert len(lower_pad) == 978
        assert all(p["dgnd_overlap_fraction"] == 1.0 and p["via"]["padstack_id"] == "DR-0102_60" for p in lower_pad.values())
        assert all(p["via"]["source_record_sha256"] == pad_by_pin[pin]["via_record_sha256"] for pin, p in lower_pad.items())

        l02 = _read("outputs/research/astra-device-l02-trace-contacts-01/trace-contact-ledger.json")
        l02_pad = {p["pin_id"]: p for p in l02["pads"] if p["role"] == "ground"}
        l02_trace = {int(t["ordinal"]): t for t in l02["traces"]}
        assert len(l02_pad) == 978 and not l02["foreign_net_intersections"]
        l02_incidence = defaultdict(list)
        for p in pads:
            for ordinal in l02_pad[p["pin_id"]]["side_contact_trace_ordinals"]:
                l02_incidence[int(ordinal)].append(p["pin_id"])
        assert len(l02_incidence) == 1691 and Counter(len(v) for v in l02_incidence.values()) == Counter({1: 1479, 2: 212})
        l02_rows = []
        for ordinal, member_pins in sorted(l02_incidence.items()):
            trace = l02_trace[ordinal]
            assert trace["net_fold"] == "dgnd" and trace["layer_id"] == "Signal$L02(DGND)"
            assert trace["width_pm"] == 25_000_000 and trace["sx"] == trace["ex"]
            assert abs(trace["sy"] - trace["ey"]) == 180_000_000
            endpoints = []
            for pin in member_pins:
                pad = pad_by_pin[pin]
                cx, cy = pad["x_pm"], pad["y_pm"]
                assert trace["sx"] == cx
                dy = np.array([trace["sy"] - cy, trace["ey"] - cy], dtype=np.int64) / 1e6
                assert np.allclose(sorted(map(abs, dy)), [22.6, 202.6], rtol=0, atol=1e-9)
                side = int(np.sign(dy[np.argmin(abs(dy))]))
                endpoints.append(dict(pin_id=pin, outward_side=side, endpoint_offsets_um=dy.tolist()))
            if len(member_pins) == 2:
                y = sorted(pad_by_pin[p]["y_pm"] for p in member_pins)
                assert y[1] - y[0] == 225_200_000
            l02_rows.append(dict(ordinal=ordinal, trace_id=trace["trace_id"], source_record_sha256=trace["source_record_sha256"],
                start_node_id=trace["start_node_id"], end_node_id=trace["end_node_id"],
                start_node_sha256=trace["start_node_sha256"], end_node_sha256=trace["end_node_sha256"],
                selected_pad_count=len(member_pins), disposition="INTERNAL_SELECTED_G" if len(member_pins) == 2 else "RETAINED_EXTERNAL_CONTINUATION",
                endpoints=endpoints, length_um=180.0, width_um=25.0))

        next_vias = _read("outputs/research/astra-device-l02-via-contacts-03/lower-via-contact-ledger.json")
        next_pad = {p["pin_id"]: p for p in next_vias["pads"] if p["role"] == "ground"}
        entities = {int(e["entity_index"]): e for e in next_vias["entities"]}
        assert len(next_pad) == 978 and not next_vias["unsupported_candidates"] and not next_vias["foreign_net_contacts"]

        interface_classes = Counter()
        pad_rows = []
        for p in pads:
            pin = p["pin_id"]
            td = len(top_pad[pin]["side_contact_trace_ordinals"])
            ad = int(bool(artwork_pad[pin]["source_artwork_polygon_count"]))
            ld = len(l02_pad[pin]["side_contact_trace_ordinals"])
            signature = (td, ad, ld)
            interface_classes[signature] += 1
            assert signature in ((2, 0, 2), (0, 1, 1), (1, 1, 0))
            next_row = next_pad[pin]
            assert len(next_row["other_via_contacts"]) == 1
            contact = next_row["other_via_contacts"][0]
            entity = entities[int(contact["entity_index"])]
            assert contact["same_net"] and contact["shares_source_endpoint"]
            assert entity["net_fold"] == "dgnd" and entity["padstack_id"] == "DR-0203_60"
            assert {entity["start_layer_id"], entity["end_layer_id"]} == {"Signal$L02(DGND)", "Signal$L03(SIG1)"}
            pad_rows.append(dict(pin_id=pin, source_node_id=p["source_node_id"], center_pm=[p["x_pm"], p["y_pm"]],
                source_node_record_sha256=p["source_node_record_sha256"], first_via_id=p["via_id"], first_via_record_sha256=p["via_record_sha256"],
                top_trace_degree=td, top_artwork_contact=bool(ad), l02_trace_degree=ld,
                interface_class=f"TOP_TRACE_{td}__ARTWORK_{ad}__L02_TRACE_{ld}",
                lower_node_id=lower_pad[pin]["lower_node"]["node_id"], lower_node_record_sha256=lower_pad[pin]["lower_node"]["source_record_sha256"],
                next_via_id=entity["via_id"], next_via_record_sha256=entity["source_record_sha256"],
                next_via_padstack_id=entity["padstack_id"], next_via_barrel_contact_um2=contact["lower_solid_barrel_contact_um2"]))
        assert interface_classes == Counter({(2, 0, 2): 942, (0, 1, 1): 19, (1, 1, 0): 17})

        with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz", allow_pickle=False) as post:
            side_ids = post["top_side_face_ids"].copy(); side_tag = post["top_side_contact_end"].copy()
            assert Counter(side_tag.tolist()) == Counter({0: 136, -1: 32, 1: 32})
        with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as joint:
            shared = joint["shared_interface_face_ids"].copy(); assert len(shared) == 64
        with np.load(ROOT / "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz", allow_pickle=False) as low:
            lower_contact = low["contact_face_ids"].copy(); lower_complement = low["complement_face_ids"].copy()
            assert len(lower_contact) == 96 and len(lower_complement) == 192
        with np.load(ROOT / "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz", allow_pickle=False) as lift:
            top_patch = lift["top_patch_face_ids"].copy(); assert len(top_patch) == 484

        internal_top = [r for r in top_rows if r["selected_endpoint_count"] == 2]
        instance_arrays = output / "g-interface-map.npz"
        np.savez_compressed(instance_arrays,
            pad_pin_id=np.array([p["pin_id"] for p in pads]),
            pad_source_node_id=np.array([p["source_node_id"] for p in pads]),
            pad_center_um=np.array([[p["x_pm"], p["y_pm"]] for p in pads], dtype=np.int64) / 1e6,
            internal_top_trace_ordinal=np.array([r["ordinal"] for r in internal_top], dtype=np.int64),
            internal_top_west_pad_index=np.array([min((pad_by_pin[e["pin_id"]]["x_pm"], [p["pin_id"] for p in pads].index(e["pin_id"])) for e in r["endpoints"])[1] for r in internal_top], dtype=np.int64),
            internal_top_east_pad_index=np.array([max((pad_by_pin[e["pin_id"]]["x_pm"], [p["pin_id"] for p in pads].index(e["pin_id"])) for e in r["endpoints"])[1] for r in internal_top], dtype=np.int64),
            top_side_face_ids=side_ids, top_side_face_tag=side_tag,
            top_left_contact_face_ids=side_ids[side_tag == -1], top_right_contact_face_ids=side_ids[side_tag == 1],
            top_patch_face_ids=top_patch, lower_r20_contact_face_ids=lower_contact,
            lower_r30_complement_face_ids=lower_complement, canonical_top_joint_shared_face_ids=shared,
        )

        ledger_path = output / "g-post-interface-ledger.json"
        ledger = dict(
            pad_records=pad_rows, top_trace_records=top_rows, top_components=components,
            top_artwork_records=artwork_rows, top_artwork_families=artwork_families,
            l02_trace_records=l02_rows,
            source_artwork=[{k: v for k, v in a.items() if k != "geometry_wkb_hex"} for a in artwork["source_artwork"]],
            dispositions=dict(
                internal_top_trace="reuse the exact 130x45um two-post conforming joint by translation",
                external_top_trace="retain the selected post contact and unassembled external continuation",
                top_artwork="retain exact source boundary polylines; the opposing full DGND artwork component is not locally truncated",
                l02_trace="requires a new r30/25um-wide conforming side junction; external traces remain continuations",
                lower_pad="the r30 pad is wholly overlapped by saved DGND artwork and requires single-owned union assembly",
                lower_via="reuse exact r20 96-face contact to the DR-0203_60 continuation"))
        ledger_path.write_text(json.dumps(ledger, indent=2, allow_nan=False), encoding="utf-8")

        result = dict(
            program="SPD Decap PI Evaluator", version="0.23.1", status="PASS_SELECTED_G_POST_INTERFACE_CENSUS",
            elapsed_s=monotonic() - start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
            artifacts={ledger_path.name: sha256(ledger_path.read_bytes()).hexdigest(), instance_arrays.name: sha256(instance_arrays.read_bytes()).hexdigest()},
            selected_ground_posts=len(pads), top_internal_traces=804, top_external_trace_continuations=293,
            top_internal_components=len(components), top_component_size_histogram=_counter_json(c["member_count"] for c in components),
            top_trace_degree_histogram=_counter_json(len(top_pad[p["pin_id"]]["side_contact_trace_ordinals"]) for p in pads),
            top_artwork_continuations=len(artwork_rows), top_artwork_translation_families=len(artwork_families),
            l02_internal_traces=212, l02_external_trace_continuations=1479,
            l02_trace_degree_histogram=_counter_json(len(l02_pad[p["pin_id"]]["side_contact_trace_ordinals"]) for p in pads),
            post_interface_class_histogram={str(k): int(v) for k, v in sorted(interface_classes.items())},
            exact_reusable_top_contact_faces_per_end=32, exact_top_patch_faces=484,
            exact_lower_r20_contact_faces=96, exact_lower_r30_complement_faces=192,
            next_l02_to_l03_vias=978,
            smallest_new_conforming_geometry=(
                "One r30 lower-pad / 25um-wide vertical-trace junction at 225.2um pad pitch. "
                "The existing r50 TOP 45um horizontal joint reuses exactly; artwork mates cannot be locally closed "
                "without partitioning the retained full DGND artwork component."),
            scope=(
                "Saved source-model interface census and exact translation/face mapping only. Internal TOP joints are reusable; "
                "293 TOP traces, 36 TOP artwork contacts, 1479 L02 traces, the full DGND overlap, and lower-via continuations "
                "remain owned interfaces. No ground, zero flux, equipotential, external-current deletion, Green, field, port, "
                "board, manufactured-bore, or PowerSI-accuracy condition is imposed."))
        (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(result))
    except Exception:
        (output / "failure.json").write_text(json.dumps(dict(elapsed_s=monotonic() - start, traceback=traceback.format_exc()), indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
