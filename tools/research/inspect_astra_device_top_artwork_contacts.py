"""SPD Decap PI Evaluator v0.23.1: source TOP artwork/trace retained interfaces."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic
import zipfile
import zlib
import numpy as np
import shapely
from shapely.geometry import Polygon
from spd_decap_pi._core.geometry.ordered_boolean import ordered_spd_geometry
import inspect_astra_device_top_trace_contacts as trace

ROOT=trace.ROOT
PINS={**trace.PINS,
 'src/spd_decap_pi/_core/geometry/ordered_boolean.py':'72e366d1dbf289f62531fa44a14b67977afd1318ceea03f8f5ea9f42e7f05134',
 'outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json':'ec6a4771c2427af09ab6d7588973f428a60f7c2cea53f75046644499f4f7e3ac'}
BUNDLE=Path('D:/SPD-Decap-PI-Evaluator-W7/e2f219e71d8c8a397009f72242cce10d78cfc7ab/260902-d115b-source-plane-ownership-materialization-04/source_plane_ownership_candidate.spdpi')


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
    pads=json.loads((ROOT/list(PINS)[0]).read_bytes())['pads']
    inventory=json.loads((ROOT/list(PINS)[2]).read_bytes())
    traces=json.loads((ROOT/list(PINS)[4]).read_bytes());assert not traces['unresolved'] and not traces['foreign_net_intersections']
    traces={row['pin_id']:row for row in traces['pads']}
    with np.load(ROOT/list(PINS)[1],allow_pickle=False) as mesh:vertices=mesh['vertices_local_m']*1e6
    xy=vertices[(vertices[:,2]==0)&(np.abs(np.linalg.norm(vertices[:,:2],axis=1)-50)<1e-10),:2]
    xy=xy[np.argsort(np.arctan2(xy[:,1],xy[:,0]))];assert len(xy)==96
    centers=np.array([[p['x_pm'],p['y_pm']] for p in pads],dtype=np.int64)
    bounds=np.r_[centers.min(axis=0)-50000000,centers.max(axis=0)+50000000]
    cache=ROOT/inventory['inputs']['raw_spatial']['path']
    assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
    c=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    c.set_progress_handler(lambda:int(monotonic()-start>15),10000)
    try:
        meta=dict(c.execute('SELECT key,value FROM meta'));assert all(meta[k]==v for k,v in inventory['cache_identity']['raw_meta'].items())
        surfaces=[dict(row) for row in c.execute('''SELECT * FROM surfaces WHERE layer_id_fold='signal$top'
            AND max_x_pm>=? AND max_y_pm>=? AND min_x_pm<=? AND min_y_pm<=? ORDER BY ordinal''',tuple(map(int,bounds)))]
        assets=[]
        for row in surfaces:
            found=list(c.execute('SELECT DISTINCT source_asset_name,source_asset_sha256 FROM plane_primitives WHERE layer_name=? AND net_name=?',
                (row['layer_id'],row['net_name'])))
            assert len(found)==1 and found[0][1]==row['artwork_asset_sha256']
            assets.append(dict(net=row['net_name'],name=found[0][0],sha256=found[0][1]))
    finally:c.close()
    assert BUNDLE.stat().st_size==925278361
    parts=[];part_nets=[];source_records=[]
    with zipfile.ZipFile(BUNDLE) as archive:
        for asset in assets:
            data=archive.read('attachments/'+asset['name']);assert sha256(data).hexdigest()==asset['sha256']
            payload=json.loads(zlib.decompress(data));assert payload['layer']=='Signal$TOP' and payload['net']==asset['net']
            geometry=ordered_spd_geometry(payload,is_cancelled=lambda:monotonic()-start>90)
            assert geometry is not None and geometry.is_valid and not geometry.is_empty
            polygons=list(geometry.geoms) if geometry.geom_type=='MultiPolygon' else [geometry]
            assert all(p.geom_type=='Polygon' for p in polygons)
            parts.extend(polygons);part_nets.extend([asset['net']]*len(polygons))
            source_records.append(dict(**asset,compressed_bytes=len(data),primitive_count=len(payload['primitive_order']),
                polygon_count=len(polygons),geometry_wkb_hex=shapely.to_wkb(geometry).hex()))
    tree=shapely.STRtree(parts);records=[];conflicts=[]
    for pad in pads:
        polygon=Polygon(xy+np.array([pad['x_pm'],pad['y_pm']])/1e6)
        candidates=sorted(map(int,tree.query(polygon,predicate='intersects')))
        same=[i for i in candidates if part_nets[i].casefold()==pad['net'].casefold()]
        for i in candidates:
            if i not in same:conflicts.append(dict(pin_id=pad['pin_id'],artwork_net=part_nets[i],
                overlap_area_um2=float(polygon.intersection(parts[i]).area)))
        geometry=shapely.union_all([parts[i] for i in same])
        contact=polygon.boundary.intersection(geometry)
        trace_contact=shapely.from_wkb(bytes.fromhex(traces[pad['pin_id']]['round_contact_wkb_hex']))
        combined=shapely.union_all([contact,trace_contact])
        records.append(dict(pin_id=pad['pin_id'],role=pad['role'],source_artwork_polygon_count=len(same),
            artwork_contact_length_um=float(contact.length),artwork_overlap_area_um2=float(polygon.intersection(geometry).area),
            combined_trace_artwork_contact_length_um=float(combined.length),
            combined_trace_artwork_contact_wkb_hex=shapely.to_wkb(combined).hex()))
        assert monotonic()-start<90
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    ledger=output/'artwork-contact-ledger.json';ledger.write_text(json.dumps(dict(pads=records,source_artwork=source_records,
        foreign_net_intersections=conflicts),indent=2,allow_nan=False),encoding='utf-8')
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='INSPECTED_SOURCE_TOP_ARTWORK_AND_TRACE_CONTACTS__OTHER_PAD_AND_VIA_UNION_REMAINS',
        elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
        ledger_sha256=sha256(ledger.read_bytes()).hexdigest(),source_asset_count=len(assets),
        source_asset_bytes_read=sum(r['compressed_bytes'] for r in source_records),
        source_artwork_polygon_count=len(parts),foreign_net_intersection_count=len(conflicts),
        pads_with_positive_artwork_side_contact=sum(r['artwork_contact_length_um']>1e-9 for r in records),
        pads_with_positive_artwork_overlap=sum(r['artwork_overlap_area_um2']>1e-9 for r in records),
        combined_side_contact_area_um2=sum(r['combined_trace_artwork_contact_length_um']*25 for r in records),
        scope='All-net TOP surface bounds select complete hash-verified source geometry assets. The existing '
              'ordered PowerSI boolean helper preserves polarity order and its circle tessellation convention. '
              'Only actual intersecting artwork and trace unions enter pad-side contact lengths; no clipping '
              'window becomes a physical boundary. Other source pad/via overlaps, lower-pad interfaces and '
              'global conductor/dielectric assembly remain. No zero flux is imposed on remaining surfaces, '
              'and no full scenario, raw SPD, board field or PowerSI calibration is read or run.')
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
