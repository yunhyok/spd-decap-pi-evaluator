"""Saved-only review of Device L02 retained traces and inherited-width overlay."""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/research/astra-device-l02-trace-contacts-review-01'
PINS={
 'outputs/research/astra-device-l02-trace-contacts-01/driver-at-run.py':'e339eed6a334554455dc20bc73982f428f19527b8670a3b8cd7796bdde4a0c33',
 'outputs/research/astra-device-l02-trace-contacts-01/result.json':'1c74e69b0e415dac67be1896d0cc3b04553e061471ca019db4fd916295988215',
 'outputs/research/astra-device-l02-trace-contacts-01/trace-contact-ledger.json':'d0d947bbfb270b4e8f51fc836cd58d9de701216693cd9e27d32d0ab3da459135',
 'outputs/research/astra-selected-trace-semantics-02/driver-at-run.py':'b9c6baf2145bbd126de8ab2f3ee5a0f8d34f1e92130b8633de824c7e69afaa27',
 'outputs/research/astra-selected-trace-semantics-02/result.json':'f58a446f6ca0a8844e73d4fb16b7d2a13611ec2536b810e73ed773c68b5bcdd3',
 'outputs/research/astra-selected-trace-semantics-02/selected-trace-semantics.json':'c873be2de4ef19ff65f2b799e7f634b4a75f67d6e5bca5baee294631fcb0451b',}
def digest(p:Path)->str:return sha256(p.read_bytes()).hexdigest()
def need(x:bool,m:str)->None:
 if not x:raise AssertionError(m)
def main()->None:
 actual={k:digest(ROOT/k) for k in PINS};need(actual==PINS,'pins')
 result=json.loads((ROOT/'outputs/research/astra-device-l02-trace-contacts-01/result.json').read_text());ledger=json.loads((ROOT/'outputs/research/astra-device-l02-trace-contacts-01/trace-contact-ledger.json').read_text())
 sem_result=json.loads((ROOT/'outputs/research/astra-selected-trace-semantics-02/result.json').read_text());sem=json.loads((ROOT/'outputs/research/astra-selected-trace-semantics-02/selected-trace-semantics.json').read_text())
 rows=ledger['pads'];need(len(rows)==1956 and Counter(x['role'] for x in rows)=={'power':978,'ground':978},'pads')
 hist=Counter(len(x['side_contact_trace_ordinals']) for x in rows);need(hist=={0:995,2:942,1:19},'contact histogram')
 power=[x for x in rows if x['role']=='power'];ground=[x for x in rows if x['role']=='ground'];need(all(not x['side_contact_trace_ordinals'] for x in power),'P zero')
 need(Counter(len(x['side_contact_trace_ordinals']) for x in ground)=={2:942,1:19,0:17},'G histogram')
 need(sum(bool(x['nonincident_node_contact_trace_ordinals']) for x in ground)==961 and all(x['nonincident_node_contact_trace_ordinals'] for x in ground if x['side_contact_trace_ordinals']),'G nonincident')
 trace_by_id={x['trace_id']:x for x in sem['traces']};unresolved=ledger['unresolved'];need(len(unresolved)==34==result['unresolved_trace_count'],'unresolved')
 layer=sem['layers']['Signal$L02(DGND)'];need(layer['width_pm']==23000000 and layer['width_origin']=='SOURCE_LAYER_EXPLICIT' and layer['attributes']['width']=='2.300000e+01u' and all(k==k.casefold() for k in layer['attributes']),'L02 default')
 bbox=result['source_bbox_pm']
 for row in unresolved:
  item=trace_by_id[row['trace_id']];need(item['source_record_sha256']==row['source_record_sha256'] and item['cached_width_pm'] is None and item['effective_width_pm']==23000000 and item['width_origin']=='SIGNAL_LAYER_DEFAULT','inherited mapping')
  need(not (row['max_x_pm']+11500000>=bbox[0] and row['max_y_pm']+11500000>=bbox[1] and row['min_x_pm']-11500000<=bbox[2] and row['min_y_pm']-11500000<=bbox[3]),'inherited row outside bbox')
 need(sem_result['global_missing_explicit_trace_width_count']==217153 and sem_result['global_attribute_names_casefolded'] and sem_result['inherited_width_traces_intersecting_selected_l02_bbox']==[],'global/default scope')
 receipt={'program':'review_astra_device_l02_trace_contacts','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':digest(Path(__file__)),'pins':actual,'recomputed':{'pads':1956,'power_zero_contacts':978,'ground_two_contacts':942,'ground_one_contact':19,'ground_zero_contacts':17,'ground_nonincident_contact_pads':961,'inherited_l02_width_rows':34,'inherited_l02_width_pm':23000000,'inherited_rows_intersecting_device_bbox':0,'global_missing_explicit_trace_widths':217153},'scope':'This checks frozen L02 trace overlays and source-model layer-default width semantics only. Artwork, other-pad/next-via interfaces, current face partition, conductor union and field/PowerSI claims remain open.'}
 OUT.mkdir(parents=True,exist_ok=False);target=OUT/'independent-review.json';target.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':receipt['status'],'receipt_sha256':digest(target)}))
if __name__=='__main__':main()
