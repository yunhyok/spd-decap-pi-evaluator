"""Saved-only audit of the selected Device L02 conditional-DGND contact census."""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/research/astra-device-l02-ground-contacts-review-01'
PINS={
 'tools/research/inspect_astra_device_l02_ground_contacts.py':'c242a7ecd3d21b462c6db93c419d7e4cb0ba8c54b6d43afeb400d0ffa670a627',
 'outputs/research/astra-device-l02-ground-contacts-01/result.json':'5f9fea636bacfb26cb2404aba2a19948deeb0e468a6c3aaf485d0ac53e6cf04a',
 'outputs/research/astra-device-l02-ground-contacts-01/lower-contact-ledger.json':'d48824f9fd6421742eb3abe0e00afcac649c2f581cdd6aead042f6769cf83b8c',
 'outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json':'a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525',}
def digest(p:Path)->str:return sha256(p.read_bytes()).hexdigest()
def need(x:bool,m:str)->None:
 if not x:raise AssertionError(m)
def main()->None:
 actual={k:digest(ROOT/k) for k in PINS};need(actual==PINS,'pins')
 result=json.loads((ROOT/'outputs/research/astra-device-l02-ground-contacts-01/result.json').read_text())
 rows=json.loads((ROOT/'outputs/research/astra-device-l02-ground-contacts-01/lower-contact-ledger.json').read_text())
 pads={p['pin_id']:p for p in json.loads((ROOT/'outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json').read_text())['pads']}
 need(len(rows)==len(pads)==1956,'coverage');need(Counter(x['role'] for x in rows)=={'power':978,'ground':978},'roles')
 for row in rows:
  pad=pads[row['pin_id']]; via,node=row['via'],row['lower_node']
  need(row['role']==pad['role'] and via['via_id']==pad['via_id'] and via['net_name']==pad['net'] and via['source_record_sha256']==pad['via_record_sha256'],'via identity/hash')
  need({via['start_layer_id'],via['end_layer_id']}=={'Signal$TOP','Signal$L02(DGND)'} and (via['start_x_pm'],via['start_y_pm'])==(pad['x_pm'],pad['y_pm'])==(via['end_x_pm'],via['end_y_pm']),'vertical xy/layers')
  need(node['net_name']==pad['net'] and (node['x_pm'],node['y_pm'])==(pad['x_pm'],pad['y_pm']) and node['padstack_id'] is None,'lower node')
  fraction=row['dgnd_overlap_fraction'];need((pad['role']=='ground' and fraction==1.0) or (pad['role']=='power' and fraction==0.0),'conditional union result')
 receipt={'program':'review_astra_device_l02_ground_contacts','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':digest(Path(__file__)),'pins':actual,'recomputed':{'rows':1956,'roles':{'power':978,'ground':978},'ground_full_60um_coverage':978,'power_zero_conditional_dgnd_overlap':978,'raw_lower_node_padstack_none':1956},'scope':'The saved ledger proves the 60um lower-pad relation only to the pinned conditional DGND union. Other-net/lower-trace/next-via boundaries, node Contact flags, drill subtraction, conductor/dielectric assembly and electrical conditions remain unresolved.'}
 OUT.mkdir(parents=True,exist_ok=False);target=OUT/'independent-review.json';target.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':receipt['status'],'receipt_sha256':digest(target)}))
if __name__=='__main__':main()
