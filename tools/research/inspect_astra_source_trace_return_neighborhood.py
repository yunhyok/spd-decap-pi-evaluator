"""SPD Decap PI Evaluator v0.23.1: source trace/pad/via versus assumed return.

Read existing source caches and accepted conditional DGND unions. No new SPD
parse, field solve, conductor union, or interpretation of a viewport as a port.
"""
from pathlib import Path
from time import monotonic
from hashlib import sha256
import argparse
import json
import sqlite3
import shapely
from shapely.geometry import LineString, box

ROOT=Path(__file__).resolve().parents[2]
PINS={
    'outputs/research/astra-3d-source-domain-inventory-01/result.json':'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663',
    'outputs/research/astra-l02-flat-conductor-domain-01/result.json':'c2757aa69aca3186852b8332c2ccf6cc84ec170f1a34fdb0098a09a74e8050d4',
    'outputs/research/astra-l02-pad-conductor-domain-01/result.json':'91e16302f7b7c6b5a43067a6208331f1aa1acee75440aa48ba035c02f1350a9d',
    'outputs/research/astra-l02-flat-conductor-domain-01/l02-artwork-flat-trace-domain.wkb':'b99d76360170a0e3c80dde1a84982fd5d6c5658e2bdc9cc0c261123c8bdb0e26',
    'outputs/research/astra-l02-pad-conductor-domain-01/l02-pad-augmented-conductor-domain.wkb':'306515d688f6359c49a867c18240bfd0c18008086a4e8eab0da056cff97bf6f7'}


def digest(path):
    result=sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert digest(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    inventory=json.loads((ROOT/next(iter(PINS))).read_bytes());control=inventory['source_geometry_controls']['trace'];source=control['row']
    cache=ROOT/inventory['inputs']['raw_spatial']['path'];assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
    connection=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True);connection.row_factory=sqlite3.Row
    connection.set_progress_handler(lambda:int(monotonic()-start>30),10000)
    try:
        meta=dict(connection.execute('SELECT key,value FROM meta'))
        for key in ('source_sha256','project_binding_sha256','geometry_identity_sha256'):assert meta[key]==inventory['cache_identity']['raw_meta'][key],key
        found=dict(connection.execute('SELECT * FROM traces WHERE trace_id_fold=?',(source['trace_id_fold'],)).fetchone());assert found==source
        endpoints=[source['start_node_id_fold'],source['end_node_id_fold']];incident={}
        # No endpoint index exists; retain a bounded full-table adjacency read so
        # an incorrectly named net cannot hide a physically shared source node.
        for table in ('traces','vias'):
            incident[table]=[dict(r) for r in connection.execute('SELECT * FROM '+table+' WHERE start_node_id_fold IN (?,?) OR end_node_id_fold IN (?,?) ORDER BY ordinal',endpoints+endpoints)]
        node_ids=sorted({r[key] for rows in incident.values() for r in rows for key in ('start_node_id_fold','end_node_id_fold')})
        nodes=[dict(connection.execute('SELECT * FROM nodes WHERE node_id_fold=?',(node,)).fetchone()) for node in node_ids]
        pad_ids=sorted({r['padstack_id_fold'] for r in nodes+incident['vias'] if r['padstack_id_fold'] is not None})
        stacks=[dict(connection.execute('SELECT * FROM padstacks WHERE padstack_id_fold=?',(key,)).fetchone()) for key in pad_ids]
        pads=[dict(r) for key in pad_ids for r in connection.execute('SELECT * FROM pad_shapes WHERE padstack_id_fold=? ORDER BY ordinal',(key,))]
    finally:connection.close()
    node_by_id={r['node_id_fold']:r for r in nodes}
    for stored in (control['start_node'],control['end_node']):assert node_by_id[stored['node_id_fold']]==stored
    points=[(node_by_id[key]['x_pm']/1e6,node_by_id[key]['y_pm']/1e6) for key in endpoints]
    footprint=LineString(points).buffer(source['width_pm']/2e6,cap_style='flat');assert abs(footprint.area-control['length_um']*control['width_um'])<1e-9
    center=footprint.centroid;viewport=box(center.x-300,center.y-200,center.x+300,center.y+200);variants=[]
    for name,path in (('artwork_flat_trace','outputs/research/astra-l02-flat-conductor-domain-01/l02-artwork-flat-trace-domain.wkb'),
                      ('pad_augmented_no_drill_subtraction','outputs/research/astra-l02-pad-conductor-domain-01/l02-pad-augmented-conductor-domain.wkb')):
        geometry=shapely.from_wkb((ROOT/path).read_bytes());assert geometry.is_valid and not geometry.is_empty
        overlap=geometry.intersection(footprint);missing=footprint.difference(geometry);local=geometry.intersection(viewport)
        assert abs(overlap.area+missing.area-footprint.area)<1e-9
        file=output/f'{name}-viewport.wkb';file.write_bytes(shapely.to_wkb(local))
        variants.append(dict(variant=name,layer='Signal$L02(DGND)',net='DGND',overlap_um2=float(overlap.area),missing_um2=float(missing.area),
            overlap_fraction=float(overlap.area/footprint.area),covers_entire_control_projection=bool(geometry.covers(footprint)),
            under_control_geometry_wkt=overlap.wkt,viewport_area_um2=float(local.area),viewport_wkb_sha256=digest(file)))
    source_nodes=[node_by_id[key] for key in endpoints]
    endpoint_top_pads=[p for p in pads if p['layer_id_fold']==source['layer_id_fold'] and p['padstack_id_fold'] in {n['padstack_id_fold'] for n in source_nodes}]
    l02_vias=[r for r in incident['vias'] if 'signal$l02(dgnd)' in (r['start_layer_id_fold'],r['end_layer_id_fold'])]
    assert len(endpoint_top_pads)==1 and endpoint_top_pads[0]['shape_kind']=='CIRCLE' and endpoint_top_pads[0]['width_pm']==100_000_000
    assert len(incident['traces'])==3 and len(l02_vias)==2 and all(v['net_fold']==source['net_fold'] for v in l02_vias)
    assert all(abs(v['overlap_um2']-1125)<1e-9 for v in variants)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='VERIFIED_CACHED_NEIGHBORHOOD__RECTANGULAR_RETURN_NOT_SOURCE_GEOMETRY',pins=PINS,
        script_sha256=digest(Path(__file__)),elapsed_s=monotonic()-start,cache_meta={k:meta[k] for k in ('source_sha256','project_binding_sha256','geometry_identity_sha256')},
        cache_byte_hash_recomputed=False,source_trace=source,adjacent_source_records=incident,source_nodes=nodes,padstacks=stacks,pad_shapes=pads,
        flat_trace_projection_um2=float(footprint.area),flat_trace_projection_wkt=footprint.wkt,viewport_bounds_um=list(viewport.bounds),ground_variants=variants,
        findings=dict(endpoint_dut_pad_diameter_um=100.,additional_incident_trace_count=2,incident_power_vias_to_l02=2,
            layer_name_is_not_net_identity=True,assumed_full_ground_projection_fraction=1.,cached_dgnd_projection_fraction=variants[0]['overlap_fraction']),
        scope='Cached 2-D source binding and geometry comparison only. Both existing conditional DGND variants cover only 1125 of5850um2 beneath the finite trace control; its full-width L02 strip is a declared artificial return. DUT endpads, continued routing and same-power-net vias are present. L02 layer name does not identify DGND electrical ownership. Existing flat trace-end convention, pad tessellation and un-subtracted drill policy remain conditional. The600x400um viewport is only a display/extraction window, never a physical termination, dielectric boundary, return cutoff or complete current path. No conductor/void volume union, new field, port impedance, board solve or PowerSI-error attribution.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);destination=p.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_TRACE_RETURN_NEIGHBORHOOD',error=repr(error)),indent=2),encoding='utf-8')
        raise
