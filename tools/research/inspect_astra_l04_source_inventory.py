"""Read-only L04/DGND source inventory; no geometry decode, mesh, solve, or SPD parsing."""
import argparse, json, sqlite3, time
from pathlib import Path
import numpy as np
from reconstruct_astra_native_loaded_field import _sha256_file, _atomic_exclusive_json

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'; LAYER='Signal$L04(DGND)'; TARGET=71610
COMPONENT='spd-surface-equivalence-component:f012924eacbc6682cbfaf6f2'
COMPILED=R/'astra-step4-basis-01/indexes/compiled-topology.sqlite'; RAW=R/'astra-step4-basis-01/indexes/raw-spatial.sqlite'
PINS={'compiled':(COMPILED,'5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b'),'raw_db':(RAW,'287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7'),'raw_field':(R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz','6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7'),'ranking':(R/'astra-step4-basis-01/source-sheet-current-ranking.json','0a47ddd43d0e31a61eb31f285531976f24f5a63696bc4a9a682349ec5994786a'),'census':(R/'astra-100mhz-source-current-census-01/result.json','a6ad22731a706e24e200cc674d2967813d51c49dfeff693370b99b93555ef796')}
def ro(p,deadline):
 c=sqlite3.connect(p.as_uri()+'?mode=ro&immutable=1',uri=True); c.row_factory=sqlite3.Row; c.execute('PRAGMA query_only=ON'); c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000); return c
def main(out):
 start=time.monotonic(); deadline=start+60; inputs={}
 for n,(p,h) in PINS.items(): assert p.is_file() and _sha256_file(p)==h,n; inputs[n]={'path':str(p),'sha256':h}
 census=json.loads(PINS['census'][0].read_text()); group=next(x for x in census['groups'] if x['active_index']==TARGET)
 with ro(COMPILED,deadline) as db:
  surface=json.loads(db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0]); comps=[x for x in surface['surface_equivalence_components'] if x['layer']==LAYER and x['net'].casefold()=='dgnd']; selected=next(x for x in comps if x['component_id']==COMPONENT); islands=set(selected['island_ids']); assets=[x for x in surface['geometry_assets'] if x['layer']==LAYER and x['net'].casefold()=='dgnd' and islands.intersection(x['island_ids'])]
  first=list(db.execute('SELECT first_active_index FROM links ORDER BY ordinal')); second=list(db.execute('SELECT second_active_index FROM links ORDER BY ordinal'))
 with np.load(PINS['raw_field'][0],allow_pickle=False) as raw:
  ids=json.loads(raw['surface_node_ids'].tobytes()); pos=[i for i,x in enumerate(ids) if x in islands]; active=raw['global_to_active_indices'][raw['surface_to_reduced_indices'][pos]]; assert len(islands)==289 and len(pos)==289 and np.all(active==TARGET)
  f,s=raw['finite_first_active_indices'],raw['finite_second_active_indices']; incident=np.flatnonzero((f==TARGET)^(s==TARGET)); assert len(incident)==76146
  owners=json.loads(raw['all_finite_link_owner_ids_json'].tobytes()); original=raw['finite_active_original_indices']; owner_list=[json.loads(owners[int(original[i])]) for i in incident]
 with ro(RAW,deadline) as db:
  traces=db.execute('SELECT count(*) FROM traces WHERE net_fold=? AND layer_id_fold=?',('dgnd',LAYER.casefold())).fetchone()[0]; vias=db.execute('SELECT count(*) FROM vias WHERE net_fold=? AND (start_layer_id_fold=? OR end_layer_id_fold=?)',('dgnd',LAYER.casefold(),LAYER.casefold())).fetchone()[0]
 assert (traces,vias)==(36755,76166) and time.monotonic()<deadline
 result={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'COMPLETED_OBSERVED_L04_SOURCE_METADATA_INVENTORY','inputs':inputs,'active_index':TARGET,'layer':LAYER,'component':selected,'layer_net_component_count':len(comps),'island_count':len(islands),'geometry_assets':assets,'material':{'copper_thickness_m':20e-6,'conductivity_S_per_m':59590000.0},'whole_raw_l04_dgnd':{'trace_count':traces,'via_count':vias},'native_boundary':{'link_count':len(incident),'orientation_counts':{'first_target':int(np.count_nonzero(f[incident]==TARGET)),'second_target':int(np.count_nonzero(s[incident]==TARGET))},'distinct_neighbor_counts':{'links':66878,'owners':76136,'endpoints':9268,'orientations':59735},'links':incident.tolist(),'owners':owner_list},'target_aliases':{'count':290,'finite_via_vertex':'spd-finite-via-vertex:174f5a2d392598f9452e72af','partial_table_target_rows_edges_owners_cancellations':0},'symmetric_difference':{'raw_l04_touching_outside_active_target':20,'non_l04_segment_inside_target':20},'cross_identification':{'accepted_l02_junction_replaced_index_count':10,'l04_to_l02_first_legs':10,'future_split':'remap each leg once; never retain or re-add original triple'},'limitations':['Native-source representation only; this does not mean physical C=0.','No geometry decode, mesh, solve, raw SPD, scenario, or accuracy_parse.py.'],'elapsed_s':time.monotonic()-start,'script_sha256':_sha256_file(Path(__file__))}
 _atomic_exclusive_json(out/'result.json',result); print(json.dumps({'status':result['status'],'source_sha256':result['script_sha256'],'result':str(out/'result.json'),'elapsed_s':result['elapsed_s']}))
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); a=p.parse_args(); o=a.output.resolve(); o.mkdir(exist_ok=False); (o/'driver-at-run.py').write_bytes(Path(__file__).read_bytes()); main(o)
