"""SPD Decap PI Evaluator v0.23.1: cached L02 via continuation geometry.

Identify actual lower mates of the source post; do not terminate its lower cut.
The polygonal barrel is the previously declared solid-via model candidate.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic
import traceback
import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from inspect_astra_device_top_other_pad_via_contacts import _shape

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json':'35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303',
 'outputs/research/astra-3d-source-domain-inventory-01/result.json':'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663',
 'outputs/research/astra-source-padstack-semantics-01/result.json':'8862633bbec8e700eef50a8126abb5765fc4a54e262c339df61e413241d76edc',
 'tools/research/inspect_astra_device_top_other_pad_via_contacts.py':'39ca6991a4ed94e139c575e32ed132997c901a5c0dda4e5730df78ee1ced14bf'}


def disc(x,y,r):
    angle=np.arange(96)*(2*np.pi/96)
    return Polygon(np.c_[x+r*np.cos(angle),y+r*np.sin(angle)])


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        assert abs(disc(0,0,20).intersection(disc(0,0,30)).area/disc(0,0,20).area-1)<1e-14
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        ledger=json.loads((ROOT/list(PINS)[0]).read_bytes());inventory=json.loads((ROOT/list(PINS)[1]).read_bytes())
        recovered=json.loads((ROOT/list(PINS)[2]).read_bytes())
        pad_pins={r['padstack_id'].casefold():r['source_record_sha256'] for r in recovered['padstacks']}
        first={v['via_id_fold']:v for v in ledger['source_vias']}
        centers=np.array([[v['end_x_pm'],v['end_y_pm']] for v in first.values()],dtype=np.int64)
        assert len(first)==1956 and all(v['end_layer_id_fold']=='signal$l02(dgnd)' for v in first.values())
        cache=ROOT/inventory['inputs']['raw_spatial']['path'];assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
        c=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        c.set_progress_handler(lambda:int(monotonic()-start>40),10000)
        try:
            meta=dict(c.execute('SELECT key,value FROM meta'));assert all(meta[k]==v for k,v in inventory['cache_identity']['raw_meta'].items())
            padstacks={r['padstack_id_fold']:dict(r) for r in c.execute('SELECT * FROM padstacks')}
            assert all(pad_pins[k]==p['source_record_sha256'] for k,p in padstacks.items())
            regular={r['padstack_id_fold']:dict(r) for r in c.execute("SELECT * FROM pad_shapes WHERE layer_id_fold='signal$l02(dgnd)'")}
            margin=30_000_000+max([p['drill_diameter_pm']//2 for p in padstacks.values() if p['drill_diameter_pm'] is not None]+[max(p['width_pm'],p['height_pm'])//2 for p in regular.values()])
            lo=centers.min(axis=0)-margin;hi=centers.max(axis=0)+margin
            sql='''SELECT v.*,a.ordinal AS start_layer_ordinal,b.ordinal AS end_layer_ordinal
              FROM vias v JOIN layers a ON a.layer_id_fold=v.start_layer_id_fold
              JOIN layers b ON b.layer_id_fold=v.end_layer_id_fold
              JOIN padstacks p ON p.padstack_id_fold=v.padstack_id_fold
              WHERE min(a.ordinal,b.ordinal)<=2 AND max(a.ordinal,b.ordinal)>=2
              AND ((min(v.start_x_pm,v.end_x_pm)<=? AND max(v.start_x_pm,v.end_x_pm)>=?
              AND min(v.start_y_pm,v.end_y_pm)<=? AND max(v.start_y_pm,v.end_y_pm)>=?)
              OR p.drill_diameter_pm IS NULL)
              ORDER BY v.ordinal'''
            candidates=[dict(r) for r in c.execute(sql,tuple(map(int,[hi[0],lo[0],hi[1],lo[1]])))]
        finally:c.close()
        entities=[];unsupported=[];geometries=[]
        for v in candidates:
            if (v['start_x_pm'],v['start_y_pm'])!=(v['end_x_pm'],v['end_y_pm']):
                unsupported.append(dict(v,reason='OBLIQUE_VIA_REQUIRES_TRUE_LAYER_INTERSECTION'));continue
            p=padstacks[v['padstack_id_fold']];x=v['start_x_pm']/1e6;y=v['start_y_pm']/1e6
            if p['drill_diameter_pm'] is None or p['drill_diameter_pm']<=0:
                unsupported.append(dict(v,reason='MISSING_OR_NONPOSITIVE_SOURCE_OUTER_DIAMETER'));continue
            barrel=disc(x,y,p['drill_diameter_pm']/2e6);r=regular.get(v['padstack_id_fold'])
            shape=barrel
            if r:
                points,status=_shape(dict(r,x_pm=v['start_x_pm'],y_pm=v['start_y_pm'],rotation_microdegrees=v['rotation_microdegrees']))
                if points is None:unsupported.append(dict(v,reason=status));continue
                shape=Polygon(points).union(barrel)
            assert shape.is_valid and shape.area>0
            entities.append(dict(v,entity_index=len(entities),padstack_source=p,regular_source=r,
                effective_l02_support_wkb_hex=shape.wkb_hex,solid_barrel_wkb_hex=barrel.wkb_hex,
                extends_below_l02=max(v['start_layer_ordinal'],v['end_layer_ordinal'])>2,
                is_through_l02=min(v['start_layer_ordinal'],v['end_layer_ordinal'])<2<max(v['start_layer_ordinal'],v['end_layer_ordinal'])))
            geometries.append(shape)
        tree=STRtree(geometries);records=[];conflicts=[]
        for record in ledger['records']:
            v=first[record['contact']['incident_via_id'].casefold()];pad=disc(v['end_x_pm']/1e6,v['end_y_pm']/1e6,30)
            candidate_ids=sorted(map(int,tree.query(pad)));contacts=[];own=[]
            for i in candidate_ids:
                e=entities[i];area=float(pad.intersection(geometries[i]).area)
                if e['via_id_fold']==v['via_id_fold']:own.append(i);continue
                if area<=0:continue
                barrel=disc(e['start_x_pm']/1e6,e['start_y_pm']/1e6,e['padstack_source']['drill_diameter_pm']/2e6)
                hit=dict(entity_index=i,same_net=e['net_fold']==v['net_fold'],overlap_um2=area,
                    lower_solid_barrel_contact_um2=float(pad.intersection(barrel).area) if e['extends_below_l02'] else 0.,
                    shares_source_endpoint=v['end_node_id_fold'] in (e['start_node_id_fold'],e['end_node_id_fold']))
                contacts.append(hit)
                if not hit['same_net']:conflicts.append(dict(pin_id=record['anchor']['pin_id'],**hit))
            assert len(own)==1
            records.append(dict(pin_id=record['anchor']['pin_id'],role=record['role'],net=v['net_name'],
                first_via_id=v['via_id'],lower_node_id=v['end_node_id'],lower_x_pm=v['end_x_pm'],lower_y_pm=v['end_y_pm'],
                candidate_entity_indices=candidate_ids,own_first_via_entity_index=own[0],other_via_contacts=contacts))
        out=output/'lower-via-contact-ledger.json'
        out.write_text(json.dumps(dict(entities=entities,pads=records,unsupported_candidates=unsupported,foreign_net_contacts=conflicts),indent=2,allow_nan=False),encoding='utf-8')
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='CACHED_L02_VIA_CONTINUATIONS__NOT_COMPLETE_LAYER_CLOSURE',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            ledger_sha256=sha256(out.read_bytes()).hexdigest(),candidate_count=len(candidates),supported_count=len(entities),
            unsupported_count=len(unsupported),foreign_net_contact_count=len(conflicts),selected_pad_count=len(records),
            candidate_padstack_counts=dict(Counter(e['padstack_id'] for e in entities)),
            other_contact_count_histogram=dict(Counter(len(p['other_via_contacts']) for p in records)),
            lower_barrel_contact_count_histogram=dict(Counter(sum(h['lower_solid_barrel_contact_um2']>0 for h in p['other_via_contacts']) for p in records)),
            source_endpoint_alias_contacts=sum(not h['shares_source_endpoint'] for p in records for h in p['other_via_contacts']),
            scope='All-net cached vias spanning or ending on L02 inside the expanded Device bounds. '
              'Positive via-pad/solid-barrel geometry overlaps are candidates for conductor union, not a field solve. '
              'Explicit first-via exclusion and all candidate geometries are saved. The database drill_diameter_pm '
              'is source OUTER diameter, never an empty hole. Lower-barrel area uses the declared solid-via candidate. '
              'Node pads, traces, artwork, antipad precedence and remote/later-layer geometry remain separate. '
              'No source SPD read, cache mutation, physical isolation condition, board solve or accuracy claim.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps(result))
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8');raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
