"""SPD Decap PI Evaluator v0.23.1: retained TOP or L02 trace/pad-side intersections.

All-net bounded cache query; compare flat and round trace-end conventions.
This partitions trace contacts only, not source artwork or the whole volume.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic
import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json':'a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525',
 'outputs/research/astra-device-post-tetra-template-01/mesh.npz':'ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02',
 'outputs/research/astra-3d-source-domain-inventory-01/result.json':'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663'}


def run(output, lower=False):
    start=monotonic()
    pins=dict(PINS)
    lower_path='outputs/research/astra-device-l02-ground-contacts-01/lower-contact-ledger.json'
    if lower:pins[lower_path]='d48824f9fd6421742eb3abe0e00afcac649c2f581cdd6aead042f6769cf83b8c'
    for path,pin in pins.items(): assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
    pads=json.loads((ROOT/next(iter(PINS))).read_bytes())['pads']
    assert len(pads)==1956 and all(p['diameter_pm']==100000000 and p['layer']=='Signal$TOP' for p in pads)
    radius,height,z=50.,25.,0.
    if lower:
        lower_rows=json.loads((ROOT/lower_path).read_bytes());assert len(lower_rows)==len(pads)
        for pad,row in zip(pads,lower_rows,strict=True):
            node=row['lower_node'];assert row['pin_id']==pad['pin_id'] and row['role']==pad['role']
            assert node['net_fold']==pad['net'].casefold() and node['layer_id']=='Signal$L02(DGND)'
            assert (node['x_pm'],node['y_pm'])==(pad['x_pm'],pad['y_pm'])
            pad.update(source_node_id=node['node_id'],layer=node['layer_id'],diameter_pm=60000000)
        radius,height,z=30.,20.,75.
    layer=pads[0]['layer'];assert all(p['layer']==layer for p in pads)
    inventory=json.loads((ROOT/list(PINS)[2]).read_bytes())
    with np.load(ROOT/list(PINS)[1],allow_pickle=False) as mesh:
        vertices=mesh['vertices_local_m']*1e6
    xy=vertices[(vertices[:,2]==z)&(np.abs(np.linalg.norm(vertices[:,:2],axis=1)-radius)<1e-10),:2]
    xy=xy[np.argsort(np.arctan2(xy[:,1],xy[:,0]))]; assert len(xy)==96
    centers=np.array([[p['x_pm'],p['y_pm']] for p in pads],dtype=np.int64)
    bounds=np.r_[centers.min(axis=0)-int(radius*1e6),centers.max(axis=0)+int(radius*1e6)]
    cache=ROOT/inventory['inputs']['raw_spatial']['path']
    assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
    connection=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True); connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.set_progress_handler(lambda:int(monotonic()-start>30),10000)
    try:
        meta=dict(connection.execute('SELECT key,value FROM meta'))
        assert all(meta[k]==v for k,v in inventory['cache_identity']['raw_meta'].items())
        query='''SELECT t.*,a.x_pm AS sx,a.y_pm AS sy,b.x_pm AS ex,b.y_pm AS ey,
                 a.net_fold AS start_net,b.net_fold AS end_net,
                 a.source_record_sha256 AS start_node_sha256,b.source_record_sha256 AS end_node_sha256
                 FROM traces t LEFT JOIN nodes a ON a.node_id_fold=t.start_node_id_fold
                 LEFT JOIN nodes b ON b.node_id_fold=t.end_node_id_fold
                 WHERE t.layer_id_fold=? AND (t.width_pm IS NULL OR
                 (t.max_x_pm+t.width_pm/2>=? AND t.max_y_pm+t.width_pm/2>=?
                 AND t.min_x_pm-t.width_pm/2<=? AND t.min_y_pm-t.width_pm/2<=?)) ORDER BY t.ordinal'''
        rows=[dict(row) for row in connection.execute(query,(layer.casefold(),*map(int,bounds)))]
        assert len(rows)<100000
    finally: connection.close()
    valid=[]; unresolved=[]; flat=[]; rounded=[]
    for row in rows:
        if not row['width_pm'] or row['width_pm']<=0 or any(row[k] is None for k in ('sx','sy','ex','ey')):
            unresolved.append(row); continue
        assert row['net_fold']==row['start_net']==row['end_net']
        assert (row['min_x_pm'],row['min_y_pm'],row['max_x_pm'],row['max_y_pm']) == (
            min(row['sx'],row['ex']),min(row['sy'],row['ey']),max(row['sx'],row['ex']),max(row['sy'],row['ey']))
        line=LineString([(row['sx']/1e6,row['sy']/1e6),(row['ex']/1e6,row['ey']/1e6)])
        if line.length==0: unresolved.append(row); continue
        valid.append(row); flat.append(line.buffer(row['width_pm']/2e6,cap_style='flat'))
        rounded.append(line.buffer(row['width_pm']/2e6,cap_style='round',quad_segs=24))
    tree=shapely.STRtree(rounded)
    records=[]; used=set(); conflicts=[]
    for pad in pads:
        center=np.array([pad['x_pm'],pad['y_pm']])/1e6
        polygon=Polygon(xy+center); boundary=polygon.boundary
        candidates=sorted(map(int,tree.query(polygon,predicate='intersects')))
        supported=[i for i in candidates if boundary.intersection(rounded[i]).length>1e-9]
        used.update(candidates)
        for i in candidates:
            if valid[i]['net_fold']!=pad['net'].casefold():
                conflicts.append(dict(pin_id=pad['pin_id'],trace_id=valid[i]['trace_id'],trace_net=valid[i]['net_name'],
                    overlap_area_um2=float(polygon.intersection(rounded[i]).area)))
        flat_contact=boundary.intersection(shapely.union_all([flat[i] for i in candidates]))
        round_contact=boundary.intersection(shapely.union_all([rounded[i] for i in candidates]))
        delta=flat_contact.symmetric_difference(round_contact).length
        records.append(dict(pin_id=pad['pin_id'],role=pad['role'],source_node_id=pad['source_node_id'],
            trace_ordinals=[valid[i]['ordinal'] for i in candidates],
            side_contact_trace_ordinals=[valid[i]['ordinal'] for i in supported],
            nonincident_node_contact_trace_ordinals=[valid[i]['ordinal'] for i in supported if pad['source_node_id'].casefold()
                not in (valid[i]['start_node_id_fold'],valid[i]['end_node_id_fold'])],
            flat_contact_length_um=float(flat_contact.length),round_contact_length_um=float(round_contact.length),
            endcap_symmetric_difference_length_um=float(delta),round_side_contact_area_um2=float(round_contact.length*height),
            round_contact_wkb_hex=shapely.to_wkb(round_contact).hex()))
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    ledger=output/'trace-contact-ledger.json'
    ledger.write_text(json.dumps(dict(pads=records,traces=[valid[i] for i in sorted(used)],unresolved=unresolved,
        foreign_net_intersections=conflicts),indent=2,allow_nan=False),encoding='utf-8')
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='INSPECTED_ACTUAL_LAYER_TRACE_CONTACTS__OTHER_SOURCE_INTERFACES_REMAIN',
        elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=pins,
        layer=layer,pad_radius_um=radius,metal_thickness_um=height,
        ledger_sha256=sha256(ledger.read_bytes()).hexdigest(),source_bbox_pm=bounds.tolist(),
        cached_candidate_trace_count=len(rows),intersecting_trace_count=len(used),
        unresolved_trace_count=len(unresolved),foreign_net_intersection_count=len(conflicts),
        pad_side_contact_trace_count_histogram=dict(Counter(len(r['side_contact_trace_ordinals']) for r in records)),
        pads_with_nonincident_node_side_contacts=sum(bool(r['nonincident_node_contact_trace_ordinals']) for r in records),
        maximum_endcap_symmetric_difference_length_um=max(r['endcap_symmetric_difference_length_um'] for r in records),
        total_round_side_contact_area_um2=sum(r['round_side_contact_area_um2'] for r in records),
        scope='Retained selected-layer trace contacts on the source-sized96gon pad boundary, using all nets and width-expanded '
              'centerline bounds. Flat/round endcap variants are explicit geometry conventions. Round union owns '
              'overlaps once. Boundary-line x layer thickness is contact area only, not a mesh-face partition or '
              'current sharing. Source artwork contacts, lower pads, dielectric domain, full conductor union and '
              'global boundary coupling remain; uncovered pad sides must not yet be forced to zero flux. '
              'Unresolved rows or foreign-net intersections require review, not silent omission. No source SPD '
              'read, field solve, old board rerun, calibration or product change occurred.')
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--lower',action='store_true',help='Use the pinned actual L02 first-via lower pads.')
    args=parser.parse_args();destination=args.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination,args.lower)
