"""Independent saved-geometry QA for the selected-G L02 surface-owner partition."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/research/astra-selected-g-l02-single-owned-partition-02"
OUT = ROOT / "outputs/research/astra-selected-g-l02-single-owned-partition-review-02"
PINS = {
    "tools/research/prepare_astra_selected_g_l02_single_owned_partition.py": "b46fd2560eea5b85759c7f571035089cb5de21289a1da9244bbac07a1069b660",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/result.json": "048491fdf7fa97440834b09213595bb9f5120ebf510c8c6108a42ed9bc80ff22",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/post-interface-instances.npz": "d4c5a28a20267c0977a1893b411973632e37f7c8de5a96c412870b9e3ab67726",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/selected-g-r30-post-pad-owners.wkb": "15b273b1a459a069a70ee74916b7b9782038dfbd7249e043dce7aec933d3b27f",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/selected-g-l02-residual-owner.wkb": "3a23b953242ccfeed9c427fd8a2d5e72aa1aa7267d92152813ee14300ab27a82",
    "outputs/research/astra-selected-g-l02-single-owned-partition-02/selected-g-l02-owned-component.wkb": "b88581f6e8013a554b5d3fc289275b6ab411b1743da41d33408815ee6411266d",
    "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz": "00afa62dabc05a825be3255cfc9888da628403d9ef783cba09ec6d88514bfe59",
    "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json": "5157020fd21232c25a236bf0a9892503a927218c5d164c1b0364372c7deb1bb0",
    "outputs/research/astra-selected-g-l02-conductor-ownership-06/ownership-map.npz": "29988273c47db33e567b9acc52a1be2f4351a16bc42720a118b459ce61921826",
    "outputs/research/astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz": "c5c0dab9ac22d750aeecf3a4290c2b9ef9285bcf7ed33a5d8a7a2775c8430984",
    "outputs/research/astra-selected-g-l02-conductor-ownership-06/selected-g-trace-only-addition.wkb": "2c82e28c920466a21a827482fdbf3398e4b218e7d0fc01e41db55a5a93a61f1f",
}


def sha(path): return sha256(path.read_bytes()).hexdigest()
def rel(x, scale): return float(np.linalg.norm(x) / max(float(scale), 1e-300))
def tri_union(v, f, ids): return shapely.union_all([Polygon(v[f[i], :2]) for i in ids])

def ring_key(coords):
    """Orientation/start independent exact-coordinate key for a WKB ring."""
    p = [tuple(map(float, x)) for x in coords[:-1]]
    # A lexicographically minimal rotation begins at a lexicographically minimal
    # point; testing only tied minima is linear for ordinary polygon rings.
    def candidates(q):
        minimum = min(q)
        return [tuple(q[k:]+q[:k]) for k,x in enumerate(q) if x == minimum]
    return min(candidates(p) + candidates(list(reversed(p))))


def zone_boundary_vectors(vertices, cells, zone, target=2):
    # Direct cell-local enumeration, retaining cell ordering rather than face arrays.
    local_faces = ((1,2,3,0), (0,3,2,1), (0,1,3,2), (0,2,1,3))
    rows = {}
    for ci, tet in enumerate(cells):
        for a,b,c,opp in local_faces:
            key = tuple(sorted((int(tet[a]), int(tet[b]), int(tet[c]))))
            p = vertices[tet[[a,b,c]]]
            av = np.cross(p[1]-p[0], p[2]-p[0]) / 2.0
            # normal must face away from omitted vertex.
            if np.dot(av, vertices[tet[opp]] - p[0]) > 0: av = -av
            rows.setdefault(key, []).append((ci, av))
    surface = []
    for entries in rows.values():
        target_entries = [x for x in entries if zone[x[0]] == target]
        if len(target_entries) == 1 and len(entries) != 1 or (len(entries) == 1 and len(target_entries) == 1):
            surface.append(target_entries[0][1])
    return np.asarray(surface), rows


def main():
    assert not OUT.exists()
    for p, h in PINS.items(): assert sha(ROOT/p) == h, p
    result = json.loads((SOURCE/"result.json").read_text())
    assert result["status"] == "QUALIFIED_SELECTED_G_L02_SINGLE_OWNER_INTERFACE_PARTITION"
    with np.load(SOURCE/"post-interface-instances.npz") as z: i = {k:z[k] for k in z.files}
    with np.load(ROOT/"outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz") as z: m = {k:z[k] for k in z.files}
    with np.load(ROOT/"outputs/research/astra-selected-g-l02-conductor-ownership-06/ownership-map.npz") as z: own = {k:z[k] for k in z.files}
    ledger = json.loads((ROOT/"outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json").read_text())["pad_records"]
    assert len(ledger) == len(i["pin_id"]) == 978
    expected_centers = np.array([r["center_pm"] for r in ledger], float)/1e6
    assert np.array_equal(i["pin_id"], np.array([r["pin_id"] for r in ledger]))
    assert np.array_equal(i["center_um"], expected_centers)
    assert np.array_equal(i["source_node_id"], np.array([r["source_node_id"] for r in ledger]))
    assert np.array_equal(i["source_first_via_id"], np.array([r["first_via_id"] for r in ledger]))
    assert np.array_equal(i["lower_node_id"], np.array([r["lower_node_id"] for r in ledger]))
    assert np.array_equal(i["next_via_id"], np.array([r["next_via_id"] for r in ledger]))
    assert np.array_equal(i["pin_id"], own["pad_pin_id"]) and np.array_equal(i["center_um"], own["pad_center_um"])
    v, cells, zone, f, av = (i["local_vertices_um"], m["cells"], m["cell_zone"], i["local_face_vertices"], i["local_face_area_vector_um2"])
    assert np.array_equal(v,m["vertices_local_um"]) and np.array_equal(f,m["face_vertices"]) and np.array_equal(av,m["face_area_vector_um2"])
    side, mate = i["r30_post_outward_interface_face_ids"], i["r30_residual_outward_interface_face_vertices"]
    assert len(side) == len(mate) == 200 and np.array_equal(mate, f[side][:,::-1])
    direct_av = np.cross(v[f[side,1]]-v[f[side,0]], v[f[side,2]]-v[f[side,0]])/2
    local_av_same = np.linalg.norm(direct_av-av[side],axis=1)
    local_av_opposite = np.linalg.norm(direct_av+av[side],axis=1)
    local_av_error = float(np.max(np.minimum(local_av_same,local_av_opposite))/max(np.max(np.linalg.norm(av[side],axis=1)),1e-300))
    mate_av = np.cross(v[mate[:,1]]-v[mate[:,0]], v[mate[:,2]]-v[mate[:,0]])/2
    mate_normal_error = rel(mate_av+direct_av, np.linalg.norm(direct_av))
    panels = [tuple(sorted({tuple(v[x,:2]) for x in f[k]})) for k in side]
    from collections import Counter
    panel_count = Counter(panels)
    assert set(panel_count.values()) == {2} and len(panel_count) == 100
    lower_all = np.r_[i["l02_lower_r20_next_via_contact_face_ids"], i["l02_lower_r30_complement_face_ids"]]
    pad_local = tri_union(v,f,lower_all)
    pad_wkb = shapely.from_wkb((SOURCE/"selected-g-r30-post-pad-owners.wkb").read_bytes())
    residual = shapely.from_wkb((SOURCE/"selected-g-l02-residual-owner.wkb").read_bytes())
    target = shapely.from_wkb((SOURCE/"selected-g-l02-owned-component.wkb").read_bytes())
    trace = shapely.from_wkb((ROOT/"outputs/research/astra-selected-g-l02-conductor-ownership-06/selected-g-trace-only-addition.wkb").read_bytes())
    with np.load(ROOT/"outputs/research/astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz") as z:
        off, payload = z["island_wkb_offsets"], z["island_wkb_bytes"]
    islands = [shapely.from_wkb(payload[off[k]:off[k+1]].tobytes()) for k in range(len(off)-1)]
    # Avoid re-unioning a 9.29e9 um2, 9,687-hole polygon.  Instead compare every
    # saved pad part to its directly translated local source polygon and prove
    # removal by exact WKB ring membership/area accounting.
    parts = list(shapely.get_parts(pad_wkb))
    assert len(parts) == 978
    expected = [affinity.translate(pad_local, xoff=x, yoff=y) for x,y in i["center_um"]]
    from scipy.spatial import cKDTree
    part_centers = np.array([(g.centroid.x,g.centroid.y) for g in parts])
    expected_centers = np.array([(g.centroid.x,g.centroid.y) for g in expected])
    dist, assignment = cKDTree(expected_centers).query(part_centers, k=1)
    assert len(np.unique(assignment)) == 978
    pad_part_area_max = max(abs(parts[k].area-expected[int(assignment[k])].area) for k in range(978))
    pad_part_center_max = float(dist.max())
    pad_keys = {ring_key(x.exterior.coords) for x in parts}
    assert len(pad_keys) == 978 and pad_part_area_max < 1e-10 and pad_part_center_max < 1e-9, (len(pad_keys),pad_part_area_max,pad_part_center_max)
    target_holes = {ring_key(x.coords) for x in target.interiors}
    residual_holes = {ring_key(x.coords) for x in residual.interiors}
    removed_holes = residual_holes-target_holes
    assert len(residual_holes)==9687 and len(target_holes)==8709 and removed_holes == pad_keys
    # The selected source component is the only island bounding all 978 exact
    # pad centers.  Its and trace-only areas independently reconstruct target
    # area within the frozen 1e-2 um2 physical-scale bookkeeping gate.
    lo, hi = i["center_um"].min(0), i["center_um"].max(0)
    selected = [n for n,x in enumerate(islands) if x.bounds[0] <= lo[0] and x.bounds[1] <= lo[1] and x.bounds[2] >= hi[0] and x.bounds[3] >= hi[1]]
    assert len(selected) == 1
    source_art = islands[selected[0]]
    target_area_signed = source_art.area + trace.area - target.area
    partition_area_signed = pad_wkb.area + residual.area - target.area
    # Ring insertion plus area equality proves the saved owner union/XOR and
    # no-overlap contract without an expensive duplicate GEOS whole-board union.
    assert abs(target_area_signed) < 1e-2 and abs(partition_area_signed) < 1e-2
    top, r20, r30 = i["l02_top_annulus_face_ids"], i["l02_lower_r20_next_via_contact_face_ids"], i["l02_lower_r30_complement_face_ids"]
    boundary = set(m["boundary_face_ids"].tolist())
    assert set(top).issubset(boundary) and set(r20).issubset(boundary) and set(r30).issubset(boundary)
    assert len(top)==196 and len(r20)==96 and len(r30)==196 and not(set(top)&set(r20) or set(top)&set(r30) or set(r20)&set(r30))
    top_poly, r20_poly, r30_poly = tri_union(v,f,top), tri_union(v,f,r20), tri_union(v,f,r30)
    horizontal_xor = max(top_poly.symmetric_difference(r30_poly).area, pad_local.symmetric_difference(shapely.union_all([r20_poly,r30_poly])).area, r20_poly.intersection(r30_poly).area)
    assert horizontal_xor < 1e-9
    assert np.allclose(v[f[top],2],55.0) and np.allclose(v[f[r20],2],75.0) and np.allclose(v[f[r30],2],75.0)
    det = np.linalg.det(v[cells[zone==2,1:]]-v[cells[zone==2,:1]])/6
    saved_vol = m["cell_volume_um3"][zone==2]
    vol_err = rel(det-saved_vol, np.linalg.norm(saved_vol)); min_det=float(det.min())
    surf, rows = zone_boundary_vectors(v,cells,zone)
    closure = rel(surf.sum(axis=0), np.linalg.norm(surf))
    assert min_det > 0 and vol_err < 1e-13 and closure < 1e-12
    # Per-instance translated r30 mesh has 200 faces/100 paired panels and exact local area.
    local_side_area=float(np.linalg.norm(av[side],axis=1).sum()); expected_side=float(pad_local.length*20)
    assert local_av_error < 1e-13 and mate_normal_error < 1e-13 and abs(local_side_area-expected_side)/expected_side < 2e-14, (local_av_error,mate_normal_error,abs(local_side_area-expected_side)/expected_side,np.linalg.norm(direct_av),np.linalg.norm(av[side]),np.einsum('ij,ij->i',direct_av,av[side]).min(),np.einsum('ij,ij->i',direct_av,av[side]).max())
    metrics=dict(instances=978, selected_artwork_raw_island_index=int(selected[0]), source_artwork_components=len(islands),
        trace_only_components=len(list(shapely.get_parts(trace))), pad_area_um2=float(pad_wkb.area), residual_area_um2=float(residual.area), target_area_um2=float(target.area),
        pad_part_area_max_um2=float(pad_part_area_max), pad_part_center_max_um=float(pad_part_center_max), removed_pad_hole_count=len(removed_holes), target_area_signed_um2=float(target_area_signed), partition_area_signed_um2=float(partition_area_signed),
        side_triangles=200, side_panels=100, local_area_vector_relative=local_av_error, local_face_vertices_vs_owner_area_orientation="mixed_unoriented_face_order", reversed_mate_normal_relative=mate_normal_error, local_side_area_um2=local_side_area,
        top_annulus_faces=196, lower_r20_faces=96, lower_r30_complement_faces=196, horizontal_partition_xor_um2=float(horizontal_xor), min_zone2_signed_tet_volume_um3=min_det, zone2_signed_volume_relative=vol_err, zone2_boundary_closure_relative=closure)
    gates={
        "all_978_instance_identity_maps": True,
        "all_978_pad_parts_match_translated_local_r30": pad_part_area_max < 1e-10 and pad_part_center_max < 1e-9,
        "all_978_pad_holes_inserted_in_residual": len(removed_holes)==978,
        "artwork_plus_trace_area_closure": abs(target_area_signed) < 1e-2,
        "pad_plus_residual_area_closure": abs(partition_area_signed) < 1e-2,
        "reversed_side_mates_and_100_panels": mate_normal_error < 1e-13 and len(panel_count)==100,
        "top_lower_face_partition": horizontal_xor < 1e-9,
        "positive_zone2_tets_and_closure": min_det > 0 and vol_err < 1e-13 and closure < 1e-12,
    }
    assert all(gates.values())
    OUT.mkdir(parents=True)
    np.savez_compressed(OUT/"independent-metrics.npz", centers_um=i["center_um"], local_side_face_ids=side, local_side_area_vectors=direct_av, local_mate_area_vectors=mate_av, zone2_signed_volumes=det, zone2_boundary_area_sum=surf.sum(axis=0), metrics=np.array([pad_part_area_max,pad_part_center_max,target_area_signed,partition_area_signed,horizontal_xor,vol_err,closure]))
    receipt=dict(program="SPD Decap PI Evaluator",version="0.23.1",status="ACCEPT_WITH_SCOPE",pins=PINS,metrics=metrics,gates=gates,
        artifacts={"independent-metrics.npz":sha(OUT/"independent-metrics.npz")},
        scope="Accepts the saved surface-owner contract only: selected artwork plus trace-only copper is partitioned once between 978 r30 post-pad owners and a residual WKB owner; each local r30 side has an exactly reversed residual mate. The residual has no volume cells here, so this does not approve two-sided current conformity, a boundary condition, charge/current spaces, Green/FMM, field, port, ground, or board behavior.")
    (OUT/"independent-review.json").write_text(json.dumps(receipt,indent=2)+"\n")
    print(json.dumps(receipt,indent=2))

if __name__ == "__main__": main()
