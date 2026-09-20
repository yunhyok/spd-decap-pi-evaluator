"""SPD Decap PI Evaluator v0.23.1: saved-only QA of L02 continuation vias."""
from pathlib import Path
from time import monotonic
import argparse,hashlib,json,math,traceback

PROGRAM='SPD Decap PI Evaluator';VERSION='0.23.1';ROOT=Path(__file__).resolve().parents[2]
HELPER=ROOT/'tools/research/inspect_astra_device_l02_via_contacts.py';RESULT=ROOT/'outputs/research/astra-device-l02-via-contacts-03/result.json';LEDGER=ROOT/'outputs/research/astra-device-l02-via-contacts-03/lower-via-contact-ledger.json'
PINS={str(HELPER.relative_to(ROOT)):'2319f9cf7be8256b36a9108b4984653308acf4742fa435e70c64414892c36bd1',str(RESULT.relative_to(ROOT)):'24b32ddef4854bf80d63de9583ae5afd97ed5664e3e0d256a4ae9c5a658cd43a',str(LEDGER.relative_to(ROOT)):'7d2477d09ab5e50cbea2397eeb19385389c7722fdd05d682987c08736a02affa'}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(p,x):
    with p.open('x',encoding='utf8') as f:json.dump(x,f,indent=2,allow_nan=False)
def self_check():
    a=96*20**2*math.sin(2*math.pi/96)/2;assert abs(a-1255.7400812180804)<1e-9;print('PASS_L02_CONTINUATION_VIA_SAVED_QA_SELF_CHECK')
def run(output):
    start=monotonic();self_check()
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for p,h in PINS.items():
            if sha(ROOT/p)!=h:raise RuntimeError('pin mismatch '+p)
        receipt=json.loads(RESULT.read_text(encoding='utf8'));ledger=json.loads(LEDGER.read_text(encoding='utf8'));entities=ledger['entities'];pads=ledger['pads']
        if len(pads)!=1956 or len(entities)!=24919 or ledger['foreign_net_contacts'] or ledger['unsupported_candidates']:raise RuntimeError('saved ledger cardinality')
        expected_area=96*20**2*math.sin(2*math.pi/96)/2;mismatch=0;hash_count=0;areas=[];via_ids=[]
        for p in pads:
            contacts=p['other_via_contacts']
            if len(contacts)!=1: mismatch+=1;continue
            c=contacts[0];e=entities[c['entity_index']];via_ids.append(e['via_id']);areas.append(c['lower_solid_barrel_contact_um2'])
            endpoint=(e['start_layer_id'],e['end_layer_id'])==('Signal$L02(DGND)','Signal$L03(SIG1)')
            centered=(e['start_x_pm'],e['start_y_pm'],e['end_x_pm'],e['end_y_pm'])==(p['lower_x_pm'],p['lower_y_pm'],p['lower_x_pm'],p['lower_y_pm'])
            valid=(endpoint and e['padstack_id']=='DR-0203_60' and e['net_name']==p['net'] and e['start_node_id']==p['lower_node_id'] and centered and c['same_net'] and c['shares_source_endpoint'])
            hashes=(len(e['source_record_sha256'])==64 and len(e['padstack_source']['source_record_sha256'])==64 and len(e['regular_source']['source_record_sha256'])==64)
            if not valid:mismatch+=1
            hash_count+=hashes
        if mismatch or len(set(via_ids))!=1956 or hash_count!=1956 or max(abs(a-expected_area) for a in areas)>1e-8:raise RuntimeError('continuation via reconciliation')
        if receipt['selected_pad_count']!=1956 or receipt['unsupported_count'] or receipt['foreign_net_contact_count'] or receipt['other_contact_count_histogram']!={'1':1956}:raise RuntimeError('receipt')
        checks=dict(lower_L02_pad_count=1956,distinct_DR_0203_60_L02_L03_via_count=len(set(via_ids)),endpoint_net_node_center_mismatch_count=mismatch,saved_source_hash_triplet_count=hash_count,solid_r20_polygon_bottom_contact_area_um2=expected_area,maximum_bottom_contact_area_error_um2=max(abs(a-expected_area) for a in areas),foreign_net_contact_count=len(ledger['foreign_net_contacts']),unsupported_candidate_count=len(ledger['unsupported_candidates']))
        result=dict(program=PROGRAM,version=VERSION,status='ACCEPTED_SAVED_L02_CONTINUATION_VIA_GEOMETRY_OWNERSHIP_ONLY',pins=PINS,driver_sha256=sha(Path(__file__)),checks=checks,elapsed_s=monotonic()-start,scope='Saved-ledger QA only. It verifies each selected lower L02 pad has one saved solid DR-0203_60 L02-to-L03 continuation via with matching saved endpoint, net, node, center and hashes. The r20 contact is the saved 96-gon source-model support. No raw SPD/database access, geometry manufacture, field/current distribution, conductor closure, board solve or accuracy claim.')
        once(output/'result.json',result);print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as e:
        once(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_L02_CONTINUATION_VIA_SAVED_QA',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc(),elapsed_s=monotonic()-start));raise
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.self_check:self_check()
    elif a.output:run(a.output.resolve())
    else:p.error('choose --self-check or --output')
