"""SPD Decap PI Evaluator v0.23.1: selected trace attributes and layer defaults.

Read only the accepted Trace interval and23.9KiB of layer metadata. Hash the exact
Trace interval; extract selected full records without replaying the SPD importer.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic
from recover_astra_source_padstack_semantics import pm

ROOT=Path(__file__).resolve().parents[2]
SOURCE=Path('D:/S4LB002-2Para_260729_1_injected.spd')
PINS={
 'tools/research/recover_astra_source_padstack_semantics.py':'2a31f6f656c730c3a78d6db3da0ab9d07ebba79754041a94886b060b510af3c0',
 'outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json':'ec6a4771c2427af09ab6d7588973f428a60f7c2cea53f75046644499f4f7e3ac',
 'outputs/research/astra-device-l02-trace-contacts-01/trace-contact-ledger.json':'d0d947bbfb270b4e8f51fc836cd58d9de701216693cd9e27d32d0ab3da459135',
 'outputs/research/astra-3d-source-domain-inventory-01/result.json':'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663',
 'C:/Cadence/Sigrity2025.1/doc/spdformat/Trace_Description_Lines.html':'241c9ec0c105d67c2b0e27db1a751a268f8a1a0673c58dcf8b3580df69252a5b',
 'C:/Cadence/Sigrity2025.1/doc/spdformat/Signal_Layer_Description_Lines.html':'307e3c4cdca0fb575e8e0bd988e9463a442ca653ebde375d86829199f850ca50'}
ATTR=re.compile(rb'(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)\s*=\s*(\S+)')


def attributes(block):
    pairs=[(key.decode('ascii').casefold(),value.decode('utf-8')) for key,value in ATTR.findall(block)]
    assert len(pairs)==len(dict(pairs)), 'duplicate case-insensitive attribute'
    return dict(pairs)


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
    inventory=json.loads((ROOT/list(PINS)[3]).read_bytes())
    assert SOURCE.stat().st_size==inventory['cache_identity']['source_coverage']['source_size_bytes']
    expected={}
    for path in list(PINS)[1:3]:
        ledger=json.loads((ROOT/path).read_bytes())
        for row in ledger['traces']+ledger['unresolved']:
            key=row['trace_id_fold']
            assert key not in expected or expected[key]==row
            expected[key]=row
    cache=ROOT/inventory['inputs']['raw_spatial']['path']
    assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
    c=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON');c.set_progress_handler(lambda:int(monotonic()-start>20),10000)
    try:
        meta=dict(c.execute('SELECT key,value FROM meta'));assert all(meta[k]==v for k,v in inventory['cache_identity']['raw_meta'].items())
        section=dict(c.execute("SELECT * FROM section_coverage WHERE section_name='Trace'").fetchone())
        layers={row['layer_id']:dict(row) for row in c.execute('SELECT * FROM layers WHERE is_conductor=1 ORDER BY ordinal')}
    finally:c.close()
    assert len(layers)==48
    conductors={row['name']:row for row in inventory['stackup']['conductors']}
    assert set(layers)==set(conductors)
    assert section['byte_start']==674226856 and section['byte_size']==175264775
    layer_start,layer_end=422134492,422158408
    with SOURCE.open('rb') as stream:
        stream.seek(section['byte_start']);data=stream.read(section['byte_size'])
        stream.seek(layer_start);layer_data=stream.read(layer_end-layer_start)
    assert len(layer_data)==layer_end-layer_start
    assert len(data)==section['byte_size'] and sha256(data).hexdigest()==section['section_sha256']
    layer_records={}
    for name,row in layers.items():
        pattern=rb'(?m)^'+re.escape(name.encode())+rb'[^\r\n]*\r?\n(?:[ \t]*\+[^\r\n]*\r?\n)*Patch[^\r\n]*\r?\n'
        found=list(re.finditer(pattern,layer_data));assert len(found)==1
        block=found[0][0];assert sha256(block).hexdigest()==row['source_record_sha256']
        attr=attributes(block);width=pm(attr['width'].encode()) if 'width' in attr else 100000000
        thickness=pm(attr['thickness'].encode())
        assert thickness==round(conductors[name]['thickness_um']*1e6) and width>0
        layer_records[name]=dict(source_offset=layer_start+found[0].start(),source_record_sha256=row['source_record_sha256'],
            width_pm=width,width_origin='SOURCE_LAYER_EXPLICIT' if 'width' in attr else 'DOCUMENTED_SIGNAL_DEFAULT_100UM',
            thickness_pm=thickness,material=conductors[name]['material'],
            conductivity_s_m_from_accepted_inventory=conductors[name]['conductivity_s_m'],
            attributes=attr,raw_text=block.decode('utf-8'))
    assert layer_records['Signal$TOP']['width_pm']==36000000 and layer_records['Signal$L02(DGND)']['width_pm']==23000000
    selected=[];count=0
    for match in re.finditer(rb'(?m)^Trace[^\r\n]*\r?\n(?:[ \t]*\+[^\r\n]*\r?\n)*',data):
        count+=1;block=match[0];key=block.split(None,1)[0].split(b'::',1)[0].decode('utf-8').casefold()
        if key not in expected:continue
        row=expected[key];assert sha256(block).hexdigest()==row['source_record_sha256'],key
        attr=attributes(block)
        explicit=pm(attr['width'].encode()) if 'width' in attr else None
        assert explicit==row['width_pm'],key
        width=explicit if explicit is not None else layer_records[row['layer_id']]['width_pm']
        selected.append(dict(trace_id=row['trace_id'],net=row['net_name'],layer=row['layer_id'],
            source_offset=section['byte_start']+match.start(),source_record_sha256=row['source_record_sha256'],
            cached_width_pm=row['width_pm'],effective_width_pm=width,width_origin='TRACE_EXPLICIT' if explicit is not None else 'SIGNAL_LAYER_DEFAULT',
            attributes=attr,raw_text=block.decode('utf-8')))
    assert count==section['raw_header_count'] and len(selected)==len(expected)==len({r['trace_id'].casefold() for r in selected})
    global_properties=Counter(key.decode('ascii').casefold() for key,_ in ATTR.findall(data))
    selected_properties=Counter(key for row in selected for key in row['attributes'])
    inherited=[row for row in selected if row['width_origin']=='SIGNAL_LAYER_DEFAULT']
    # The original L02 census kept all unknown-width rows, even outside its bbox.
    bounds=[-3479800000,11678300000,12700200000,20848900000]
    inherited_bbox_candidates=[]
    for row in inherited:
        old=expected[row['trace_id'].casefold()];half=row['effective_width_pm']/2
        if old['max_x_pm']+half>=bounds[0] and old['max_y_pm']+half>=bounds[1] and old['min_x_pm']-half<=bounds[2] and old['min_y_pm']-half<=bounds[3]:
            inherited_bbox_candidates.append(row['trace_id'])
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    ledger=output/'selected-trace-semantics.json'
    ledger.write_text(json.dumps(dict(layers=layer_records,traces=selected),indent=2,allow_nan=False),encoding='utf-8')
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='RECOVERED_SELECTED_TRACE_ATTRIBUTES_AND_DOCUMENTED_LAYER_WIDTH_DEFAULTS',
        elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
        ledger_sha256=sha256(ledger.read_bytes()).hexdigest(),source_section=section,
        source_bytes_read=section['byte_size']+len(layer_data),full_source_hash_recomputed=False,
        layer_metadata_interval=[layer_start,layer_end],conductor_layer_count=len(layer_records),
        layer_width_origin_counts=dict(Counter(row['width_origin'] for row in layer_records.values())),
        global_missing_explicit_trace_width_count=count-global_properties.get('width',0),
        global_attribute_names_casefolded=True,
        trace_header_count=count,selected_full_record_count=len(selected),all_selected_record_hashes_match=True,
        global_trace_property_counts=dict(global_properties),selected_attribute_counts=dict(selected_properties),
        inherited_width_count=len(inherited),inherited_width_pm_counts=dict(Counter(row['effective_width_pm'] for row in inherited)),
        inherited_width_traces_intersecting_selected_l02_bbox=inherited_bbox_candidates,
        scope='One newly required bounded Trace-section read with its full accepted section hash, plus hash-matched '
              'all48 conductor layer declarations. Official SPD Trace Width inherits the Signal layer Width when omitted. '
              'Only selected source records are parsed for raw key/value evidence; no full SPD importer, Node/Via '
              'section scan, circuit solve or product mutation occurs. Extra trace attributes remain explicit '
              'and must be interpreted before physical promotion. A default width is source-model input, not '
              'a fitted fabrication value or proof of a particular port/contact model.')
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
