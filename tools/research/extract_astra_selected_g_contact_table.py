"""SPD Decap PI Evaluator v0.23.1: saved selected-G contact table extraction."""
from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parents[2];base=ROOT/'outputs/research';paths=['astra-device-terminal-pads-01/device-terminal-pads.json','astra-device-top-trace-contacts-01/trace-contact-ledger.json','astra-device-top-artwork-contacts-01/artwork-contact-ledger.json','astra-device-l02-ground-contacts-01/lower-contact-ledger.json','astra-device-l02-trace-contacts-01/trace-contact-ledger.json','astra-device-l02-via-contacts-03/lower-via-contact-ledger.json']
def main():
 out=base/'astra-selected-g-contact-table-01';out.mkdir();(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes());x=[json.loads((base/p).read_text()) for p in paths];pads=[p for p in x[0]['pads'] if p['role']=='ground'];assert len(pads)==978
 table=[]
 for p in pads:table.append({'pin_id':p['pin_id'],'source_node_id':p['source_node_id'],'via_id':p['via_id'],'center_pm':[p['x_pm'],p['y_pm']],'top_trace':next((r for r in x[1]['pads'] if r['pin_id']==p['pin_id']),None),'top_artwork':next((r for r in x[2]['pads'] if r['pin_id']==p['pin_id']),None),'l02_ground':next((r for r in x[3] if r.get('pin_id')==p['pin_id']),None),'l02_trace':next((r for r in x[4]['pads'] if r['pin_id']==p['pin_id']),None),'l02_via':next((r for r in x[5]['pads'] if r['pin_id']==p['pin_id']),None)})
 r={'program':'SPD Decap PI Evaluator','version':'0.23.1','input_hashes':{p:hashlib.sha256((base/p).read_bytes()).hexdigest() for p in paths},'selected_ground_count':978,'known_external_top_traces':293,'known_artwork_interfaces':36,'external_status':'RETAINED_UNKNOWN_CONTINUATION','members':table,'scope':'Saved-table extraction only; no complete G return claim.'};json.dump(r,(out/'g-contact-table.json').open('x'),indent=2);json.dump({'status':'EXTRACTED_SELECTED_G_SAVED_CONTACT_TABLE','selected_ground_count':978},(out/'result.json').open('x'),indent=2)
if __name__=='__main__':main()
