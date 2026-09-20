"""SPD Decap PI Evaluator v0.23.1: source lower-pad relation to saved DGND union.

Reuse the existing conditional artwork/flat-trace/pad union. Hole indexing
evaluates exact intersections locally without repeatedly overlaying the board.
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
from shapely.geometry import Polygon
import inspect_astra_device_top_trace_contacts as trace

ROOT=trace.ROOT
PINS={**trace.PINS,
 'outputs/research/astra-l02-pad-conductor-domain-01/l02-pad-augmented-conductor-domain.wkb':'306515d688f6359c49a867c18240bfd0c18008086a4e8eab0da056cff97bf6f7'}


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
    pads=json.loads((ROOT/list(PINS)[0]).read_bytes())['pads'];inventory=json.loads((ROOT/list(PINS)[2]).read_bytes())
    with np.load(ROOT/list(PINS)[1],allow_pickle=False) as mesh:vertices=mesh['vertices_local_m']*1e6
    xy=vertices[(vertices[:,2]==75)&(abs(np.linalg.norm(vertices[:,:2],axis=1)-30)<1e-10),:2]
    xy=xy[np.argsort(np.arctan2(xy[:,1],xy[:,0]))];assert len(xy)==96
    cache=ROOT/inventory['inputs']['raw_spatial']['path'];assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
    c=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    c.set_progress_handler(lambda:int(monotonic()-start>30),10000)
    lower=[]
    try:
        meta=dict(c.execute('SELECT key,value FROM meta'));assert all(meta[k]==v for k,v in inventory['cache_identity']['raw_meta'].items())
        for pad in pads:
            via=dict(c.execute('SELECT * FROM vias WHERE via_id_fold=?',(pad['via_id'].casefold(),)).fetchone())
            assert via['source_record_sha256']==pad['via_record_sha256'] and via['net_fold']==pad['net'].casefold()
            assert {via['start_layer_id_fold'],via['end_layer_id_fold']}=={'signal$top','signal$l02(dgnd)'}
            assert via['padstack_id']=='DR-0102_60' and via['status']=='EXACT'
            assert (via['start_x_pm'],via['start_y_pm'])==(pad['x_pm'],pad['y_pm'])==(via['end_x_pm'],via['end_y_pm'])
            side='start' if via['start_layer_id_fold']=='signal$l02(dgnd)' else 'end'
            other='end' if side=='start' else 'start'
            assert via[other+'_node_id']==pad['source_node_id']
            node=dict(c.execute('SELECT * FROM nodes WHERE node_id_fold=?',(via[side+'_node_id_fold'],)).fetchone())
            assert node['net_fold']==pad['net'].casefold() and (node['x_pm'],node['y_pm'])==(pad['x_pm'],pad['y_pm'])
            lower.append(dict(pin_id=pad['pin_id'],role=pad['role'],via=via,lower_node=node))
    finally:c.close()
    geometry=shapely.from_wkb((ROOT/list(PINS)[3]).read_bytes());assert geometry.is_valid
    parts=list(geometry.geoms) if geometry.geom_type=='MultiPolygon' else [geometry]
    outers=[Polygon(p.exterior) for p in parts];shapely.prepare(outers)
    holes=[[Polygon(ring) for ring in p.interiors] for p in parts]
    outer_tree=shapely.STRtree(outers);hole_trees=[shapely.STRtree(h) for h in holes]
    polygons=[]; intersections=[]
    for row,pad in zip(lower,pads,strict=True):
        polygon=Polygon(xy+np.array([pad['x_pm'],pad['y_pm']])/1e6);pieces=[]
        for parent in map(int,outer_tree.query(polygon,predicate='intersects')):
            local=polygon if outers[parent].covers(polygon) else polygon.intersection(outers[parent])
            selected=list(map(int,hole_trees[parent].query(polygon,predicate='intersects')))
            if selected:local=local.difference(shapely.union_all([holes[parent][i] for i in selected]))
            pieces.append(local)
        overlap=shapely.union_all(pieces)
        row.update(pad_polygon_area_um2=float(polygon.area),dgnd_overlap_area_um2=float(overlap.area),
            dgnd_overlap_fraction=float(overlap.area/polygon.area),dgnd_side_contact_length_um=float(overlap.intersection(polygon.boundary).length))
        polygons.append(polygon);intersections.append(overlap)
        assert monotonic()-start<60
    samples=sorted(set([0,len(pads)-1,next(i for i,p in enumerate(pads) if p['role']=='ground'),
        int(np.argmax([r['dgnd_overlap_fraction'] for r in lower])),int(np.argmin([r['dgnd_overlap_fraction'] for r in lower]))]))
    direct_errors=[]
    for i in samples:
        direct=geometry.intersection(polygons[i]);direct_errors.append(float(direct.symmetric_difference(intersections[i]).area/polygons[i].area))
    assert max(direct_errors)<1e-10
    summary={}
    for role in ('power','ground'):
        rows=[r for r in lower if r['role']==role];fractions=[r['dgnd_overlap_fraction'] for r in rows]
        summary[role]=dict(count=len(rows),positive_overlap_count=sum(r['dgnd_overlap_area_um2']>1e-9 for r in rows),
            minimum_overlap_fraction=min(fractions),maximum_overlap_fraction=max(fractions),
            full_within_1e_minus8_count=sum(abs(f-1)<1e-8 for f in fractions),
            lower_node_padstack_counts=dict(Counter(str(r['lower_node']['padstack_id']) for r in rows)))
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    ledger=output/'lower-contact-ledger.json';ledger.write_text(json.dumps(lower,indent=2,allow_nan=False),encoding='utf-8')
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='INSPECTED_SOURCE_LOWER_PADS_AGAINST_EXISTING_CONDITIONAL_DGND_UNION',
        elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
        ledger_sha256=sha256(ledger.read_bytes()).hexdigest(),role_summary=summary,
        direct_full_geometry_sample_indices=samples,maximum_direct_intersection_difference=max(direct_errors),
        scope='All1956 actual selected vias are verified vertical TOP-to-L02 DR-0102_60 at the recovered pad centers; '
              'lower-node orientation is independent of source start/end order. Source-sized60um regular-pad96gons '
              'are intersected with the frozen conditional DGND artwork/flat-trace/pad union without drill subtraction. '
              'Indexed holes preserve that saved union locally and direct full-geometry samples check the operation. '
              'This does not complete power-net trace/pad unions, later vias, all foreign nets, node Contact flags, '
              'dielectric domains, electrical boundary conditions or current/charge assembly. No field or board rerun.')
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
