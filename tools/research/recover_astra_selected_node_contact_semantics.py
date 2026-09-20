"""SPD Decap PI Evaluator v0.23.1: bounded source Node Contact recovery.

Read the accepted Node interval once to settle an attribute absent from the
cache schema. Do not run the full SPD importer or read Trace/Via sections.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic
import traceback
from recover_astra_selected_trace_semantics import ATTR,attributes
from recover_astra_source_padstack_semantics import pm

ROOT=Path(__file__).resolve().parents[2]
SOURCE=Path('D:/S4LB002-2Para_260729_1_injected.spd')
PINS={
 'outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json':'35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303',
 'outputs/research/astra-3d-source-domain-inventory-01/result.json':'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663',
 'tools/research/recover_astra_selected_trace_semantics.py':'b9c6baf2145bbd126de8ab2f3ee5a0f8d34f1e92130b8633de824c7e69afaa27',
 'tools/research/recover_astra_source_padstack_semantics.py':'2a31f6f656c730c3a78d6db3da0ab9d07ebba79754041a94886b060b510af3c0',
 'C:/Cadence/Sigrity2025.1/doc/spdformat/Node_Description_Lines.html':'9e8454c7bbbc5f911feec89ffa7e426b37b3eed33817572061a54f0b3fc1fc52',
 'src/spd_decap_pi/raw_spatial_contact_compiler.py':'a1d83f96cfda9fe62e1f30cfec5868596b752dd17a447c000b18ddb63dfb5c7b'}


def signed_pm(token):
    return -pm(token[1:].encode()) if token.startswith('-') else pm(token.encode())


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        rows=json.loads((ROOT/list(PINS)[0]).read_bytes())['source_nodes']
        expected={row['node_id_fold']:row for row in rows};assert len(expected)==3912
        inventory=json.loads((ROOT/list(PINS)[1]).read_bytes())
        assert SOURCE.stat().st_size==inventory['cache_identity']['source_coverage']['source_size_bytes']
        cache=ROOT/inventory['inputs']['raw_spatial']['path'];assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
        c=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON');c.set_progress_handler(lambda:int(monotonic()-start>20),10000)
        try:
            meta=dict(c.execute('SELECT key,value FROM meta'));assert all(meta[k]==v for k,v in inventory['cache_identity']['raw_meta'].items())
            section=dict(c.execute("SELECT * FROM section_coverage WHERE section_name='Node'").fetchone())
        finally:c.close()
        assert (section['byte_start'],section['byte_end'])==(422158408,674226856)
        with SOURCE.open('rb') as stream:
            stream.seek(section['byte_start']);data=stream.read(section['byte_size'])
        assert len(data)==section['byte_size'] and sha256(data).hexdigest()==section['section_sha256']
        selected=[];count=0
        for match in re.finditer(rb'(?m)^Node[^\r\n]*\r?\n(?:[ \t]*\+[^\r\n]*\r?\n)*',data):
            count+=1;block=match[0]
            # The tracked Node grammar excludes the optional !! annotation
            # from node identity; retain it in the full hash-verified text.
            key=block.split(None,1)[0].split(b'::',1)[0].split(b'!!',1)[0].decode('utf-8').casefold()
            if key not in expected:continue
            row=expected[key];assert sha256(block).hexdigest()==row['source_record_sha256'],key
            attr=attributes(block)
            assert (signed_pm(attr['x']),signed_pm(attr['y']))==(row['x_pm'],row['y_pm'])
            assert attr['layer'].casefold()==row['layer_id_fold']
            assert attr.get('padstack','').casefold()==(row['padstack_id_fold'] or '')
            contact=int(attr.get('contact','1'));assert contact in (0,1)
            selected.append(dict(node_id=row['node_id'],net=row['net_name'],layer=row['layer_id'],
                source_offset=section['byte_start']+match.start(),source_record_sha256=row['source_record_sha256'],
                attributes=attr,raw_text=block.decode('utf-8'),effective_metal_shape_contact=contact,
                contact_origin='NODE_EXPLICIT' if 'contact' in attr else 'DOCUMENTED_NODE_DEFAULT_1'))
        assert count==section['raw_header_count'] and len(selected)==len(expected)==len({r['node_id'].casefold() for r in selected}), (count,section['raw_header_count'],len(selected),len(expected))
        global_attributes=Counter();global_contact_values=Counter()
        for match in ATTR.finditer(data):
            key=match[1].decode('ascii').casefold();global_attributes[key]+=1
            if key=='contact':global_contact_values[match[2].decode('utf-8')]+=1
        ledger=output/'selected-node-contact-semantics.json'
        ledger.write_text(json.dumps(selected,indent=2,allow_nan=False),encoding='utf-8')
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
            status='RECOVERED_SOURCE_NODE_CONTACT_SEMANTICS_FOR_SELECTED_DEVICE_ENDPOINTS',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            ledger_sha256=sha256(ledger.read_bytes()).hexdigest(),source_section=section,
            source_bytes_read=len(data),full_source_hash_recomputed=False,node_header_count=count,
            selected_full_record_count=len(selected),all_selected_record_hashes_match=True,
            global_casefolded_attribute_counts=dict(global_attributes),global_explicit_contact_values=dict(global_contact_values),
            selected_contact_origin_counts=dict(Counter(r['contact_origin'] for r in selected)),
            selected_effective_contact_counts=dict(Counter(r['effective_metal_shape_contact'] for r in selected)),
            scope='The official SPD Node rule defaults metal-shape contact to1 when Contact is omitted; '
                  'trace-end electrical contact is separately assumed by the format. Selected complete '
                  'source records match accepted cached hashes and coordinates. This resolves this Contact '
                  'attribute gap, not all via/metal union rules, whole-PowerSI port area, dielectric extent, '
                  'current convergence or board accuracy. One bounded Node interval only; no full SPD '
                  'import, cache mutation, Trace/Via reread or field solve.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8')
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
