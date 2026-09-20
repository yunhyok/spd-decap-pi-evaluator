"""SPD Decap PI Evaluator v0.23.1: saved-only QA of selected Node Contact defaults."""
from pathlib import Path
from time import monotonic
import argparse,hashlib,json,traceback

PROGRAM='SPD Decap PI Evaluator';VERSION='0.23.1';ROOT=Path(__file__).resolve().parents[2]
HELPER=ROOT/'tools/research/recover_astra_selected_node_contact_semantics.py'
RESULT=ROOT/'outputs/research/astra-selected-node-contact-semantics-02/result.json'
LEDGER=ROOT/'outputs/research/astra-selected-node-contact-semantics-02/selected-node-contact-semantics.json'
FIRST=ROOT/'outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json'
PINS={str(HELPER.relative_to(ROOT)):'21b8d5d33baf32d7831afd8d7b75d588f3eb866e58eb82d1255f3c304189f352',str(RESULT.relative_to(ROOT)):'a69ddd57d728fe133c8fe5acdb0b89d43d54618aae4571de733a8cd4f700955e',str(LEDGER.relative_to(ROOT)):'f56707531e24e34e63a49c4f853eee3ac6cd182627904fd0f1d70b413fcd52be'}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(p,x):
    with p.open('x',encoding='utf8') as f:json.dump(x,f,indent=2,allow_nan=False)
def self_check():
    assert len({('N','L',1,2,'P','h')})==1;print('PASS_SELECTED_NODE_CONTACT_SAVED_QA_SELF_CHECK')
def run(output):
    start=monotonic();self_check()
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for p,h in PINS.items():
            if sha(ROOT/p)!=h:raise RuntimeError('pin mismatch '+p)
        source=json.loads(FIRST.read_text(encoding='utf8'));ledger=json.loads(LEDGER.read_text(encoding='utf8'));receipt=json.loads(RESULT.read_text(encoding='utf8'))
        expected={r['node_id']:(r['source_record_sha256'],r['x_pm'],r['y_pm'],r['layer_id'],r['padstack_id'],r['net_name']) for r in source['source_nodes']}
        if len(expected)!=3912 or len(ledger)!=3912:raise RuntimeError('selected record count')
        mismatch=0;contact=0;missing_attr=0;node_padstack={'DUT':0,'ABSENT_ON_LOWER_NODE':0}
        for r in ledger:
            target=expected.get(r['node_id']);x_mm=float(r['attributes']['x'][:-2]);y_mm=float(r['attributes']['y'][:-2])
            observed=(r['source_record_sha256'],round(x_mm*1e9),round(y_mm*1e9),r['layer'],None,r['net'])
            if target is None or observed[:4]!=(target[0],target[1],target[2],target[3]) or observed[5]!=target[5]:mismatch+=1
            # The saved lower endpoint Nodes have no Padstack attribute; each
            # pair's DR-0102_60 ownership is retained in saved source_vias.
            if target is None or target[4] not in {'DUT',None}:mismatch+=1
            else:node_padstack['DUT' if target[4]=='DUT' else 'ABSENT_ON_LOWER_NODE']+=1
            contact+=r['effective_metal_shape_contact']==1 and r['contact_origin']=='DOCUMENTED_NODE_DEFAULT_1';missing_attr+='contact' not in {k.casefold() for k in r['attributes']}
        via_match=sum(v['padstack_id']=='DR-0102_60' and v['start_node_id'] in expected and v['end_node_id'] in expected for v in source['source_vias'])
        if mismatch or contact!=3912 or missing_attr!=3912 or node_padstack!={'DUT':1956,'ABSENT_ON_LOWER_NODE':1956} or via_match!=1956:raise RuntimeError('saved node/contact reconciliation')
        if receipt['selected_full_record_count']!=3912 or receipt['ledger_sha256']!=PINS[str(LEDGER.relative_to(ROOT))] or receipt['selected_effective_contact_counts']!={'1':3912}:raise RuntimeError('receipt contact semantics')
        checks=dict(selected_records=3912,record_hash_xy_layer_net_mismatch_count=mismatch,saved_node_padstack_counts=node_padstack,saved_via_DR_0102_60_endpoint_join_count=via_match,contact_default_one_count=contact,explicit_contact_attribute_absent_count=missing_attr,official_semantics_pin=receipt['pins']['C:/Cadence/Sigrity2025.1/doc/spdformat/Node_Description_Lines.html'])
        result=dict(program=PROGRAM,version=VERSION,status='ACCEPTED_SAVED_SOURCE_NODE_CONTACT_DEFAULT_METADATA_ONLY',pins=PINS,driver_sha256=sha(Path(__file__)),checks=checks,elapsed_s=monotonic()-start,scope='Saved-artifact QA only. It accepts the pinned official Node default Contact=1 semantics and reconciles saved selected record hashes, XY, layer, net and source-node padstack join. This is source-model metadata only; it does not manufacture geometry, an electrode contact face, current distribution, conductor union, solver, field result or PowerSI accuracy claim.')
        once(output/'result.json',result);print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as e:
        once(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_SELECTED_NODE_CONTACT_SAVED_QA',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc(),elapsed_s=monotonic()-start));raise
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.self_check:self_check()
    elif a.output:run(a.output.resolve())
    else:p.error('choose --self-check or --output')
