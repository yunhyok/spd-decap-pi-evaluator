"""Freeze L04 source-contact inputs from pinned cached SQLite/NPZ artifacts."""
from __future__ import annotations
import argparse, json, time
import qualify_astra_l02_source_contacts as base
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'; L='signal$l04(dgnd)'; T=71610; FIRST=1692369
P={'raw_db':(R/'astra-step4-basis-01/indexes/raw-spatial.sqlite','287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7'),'compiled':(R/'astra-step4-basis-01/indexes/compiled-topology.sqlite','5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b'),'raw':(R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz','6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7'),'inventory':(R/'astra-l04-source-inventory-root-02/result.json','c86cf3410188e5386bfa8b5f4b57faddc6c219456e59a4ba8ac1d8e9b5f041d1'),'splits':(R/'astra-l04-cached-path-splits-02/result.json','17ca350bbb4913283315812e746b547d3ee01c4fec85133d411fb0e8602fe48d'),'binding_result':(R/'astra-l02-circuit-contact-binding-01/result.json','1ef00f9c88553faf71f6cd87b4899c23d4a1be598a18a08c19b8776148690efc'),'binding_npz':(R/'astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz','61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020'),'islands':(R/'astra-l04-hidden-pad-trace-artwork-01/l04-source-islands.npz','e8536def5005b9f3664ad8c006cb06d4bca39b6c3631f364bf5ba761f6dc573c')}

P.update({
 'source_helper':(Path(base.__file__),'282def7c0eff7a7e6b822766bbb104ee910945a2af95b49d800badcd89a4deb1'),
 'persistence':(Path(base.mass.__file__),'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3'),
 'inventory_review':(R/'astra-l04-source-inventory-root-02/independent-review-final.json','1759b05d10147e4571bb3d9589782295ad808aa9b924ba16529079489b2e921f'),
 'split_review':(R/'astra-l04-cached-path-splits-02/independent-review.json','79d4810633fc84655914be1c2a7abc5ed3e5ff6997e2b9a2b90a896967a83103'),
 'binding_review':(R/'astra-l02-circuit-contact-binding-01/independent-review.json','be2dff54dfce3ab01933b391112972ac46ca9118b54195d505212a9489d29f6c'),
 'geometry_review':(R/'astra-l04-hidden-pad-trace-artwork-01/independent-review.json','405a0b4c2a81e3d95f71e432c5f71901087a8278dc89518c768d99046cbf5b78'),
})
sha=base.digest

def pack(value):
 return np.frombuffer(json.dumps(value,separators=(',',':'),allow_nan=False).encode(),dtype=np.uint8)

def inputs():
 for name,(path,expected) in P.items(): assert sha(path)==expected,name
 return {name:{'path':str(path.resolve()),'sha256':expected} for name,(path,expected) in P.items()}

def expanded_native_indices(indices,replaced):
 removed=np.sort(np.asarray(replaced,dtype=np.int64))
 assert len(np.unique(removed))==len(removed) and not np.intersect1d(indices,removed).size
 return np.asarray(indices,dtype=np.int64)-np.searchsorted(removed,indices)

def self_check():
 replaced=[7,3]; ordinary=np.array([0,2,4,8])
 kept=np.flatnonzero(~np.isin(np.arange(10),replaced))
 assert np.array_equal(kept[expanded_native_indices(ordinary,replaced)],ordinary)
 junctions=[(7,12),(3,10)]; positions={original:j for j,(original,contact) in enumerate(junctions)}
 assert positions[3]==1 and 10-len(replaced)+positions[3]==9 and junctions[positions[3]][1]==10
 try: expanded_native_indices(np.array([3]),replaced)
 except AssertionError: pass
 else: raise AssertionError('removed composite accepted as ordinary')
 xy=np.array([[1,0],[0,0],[1,0]])
 unique,inverse,counts=np.unique(xy,axis=0,return_inverse=True,return_counts=True)
 assert np.array_equal(unique[inverse],xy) and counts.tolist()==[1,2]
 assert json.loads(pack({'ids':['via:a']}).tobytes())=={'ids':['via:a']}

def collect():
 started=time.monotonic(); deadline=started+60; ins=inputs()
 inv=json.loads(P['inventory'][0].read_bytes()); split=json.loads(P['splits'][0].read_bytes())
 assert inv['active_index']==T and inv['native_incident_links']==76146
 with np.load(P['islands'][0],allow_pickle=False) as z:
  ids=json.loads(z['island_ids_json_utf8'].tobytes()); offsets=z['island_wkb_offsets']
  assert ids==sorted(ids) and len(ids)==len(set(ids))==289 and set(ids)==set(inv['component']['island_ids'])
  assert offsets.shape==(290,) and offsets[0]==0 and np.all(np.diff(offsets)>0) and offsets[-1]==len(z['island_wkb_bytes'])
 with np.load(P['binding_npz'][0],allow_pickle=False) as b: junctions=json.loads(b['junctions_json_utf8'].tobytes())
 ja={row['replaced_active_finite_index']:j for j,row in enumerate(junctions)}
 paths={row['original_active_finite_index']:row for row in split['paths']}
 assert len(junctions)==len(ja)==len(paths)==20 and set(ja)==set(paths)
 assert sorted(row['contact_ordinal'] for row in junctions)==list(range(38836,38856))
 replaced=np.array(list(ja),dtype=np.int64)
 with np.load(P['raw'][0],allow_pickle=False) as z:
  first=z['finite_first_active_indices']; second=z['finite_second_active_indices']; original=z['finite_active_original_indices']
  count=z['finite_count']; resistance=z['finite_resistance_ohm_per_via']; inductance=z['finite_inductance_h_per_via']
  assert len(first)==FIRST+20
  incident=np.flatnonzero((first==T)^(second==T))
  assert len(incident)==76146 and not np.any((first==T)&(second==T))
  all_owners=json.loads(z['all_finite_link_owner_ids_json'].tobytes())
  owners={int(i):[v.casefold() for v in json.loads(all_owners[int(original[i])])] for i in incident}
  del all_owners
 ordinary=np.asarray([i for i in incident if i not in ja],dtype=np.int64)
 assert len(ordinary)==76136 and all(len(owners[int(i)])==1 for i in ordinary)
 assert all(len(owners[int(i)])==3 for i in incident if i in ja)
 native=[v for values in owners.values() for v in values]
 assert len(native)==len(set(native))==76166
 ordinary_by_via={owners[int(i)][0]:int(i) for i in ordinary}
 assert len(ordinary_by_via)==len(ordinary)
 with base.read_db(P['raw_db'][0],deadline) as db:
  meta=dict(db.execute('SELECT key,value FROM meta'))
  assert meta['source_sha256']==base.RAW_SOURCE_SHA and meta['logical_rows_sha256']==base.RAW_LOGICAL_SHA
  rows=[dict(row) for row in db.execute("SELECT * FROM vias WHERE net_fold='dgnd' AND (start_layer_id_fold=? OR end_layer_id_fold=?) ORDER BY ordinal",(L,L))]
  used=sorted({row['padstack_id_fold'] for row in rows}); placeholders=','.join('?' for _ in used)
  pads=[dict(row) for row in db.execute('SELECT * FROM pad_shapes WHERE layer_id_fold=? AND padstack_id_fold IN ('+placeholders+') ORDER BY padstack_id_fold',(L,*used))]
  stacks=[dict(row) for row in db.execute('SELECT * FROM padstacks WHERE padstack_id_fold IN ('+placeholders+') ORDER BY padstack_id_fold',used)]
 assert len(rows)==76166 and all(row['status']=='EXACT' for row in rows)
 assert len(used)==len(pads)==len(stacks)==4
 assert [row['padstack_id_fold'] for row in pads]==[row['padstack_id_fold'] for row in stacks]==used
 for pad,stack in zip(pads,stacks,strict=True):
  assert pad['shape_kind']=='CIRCLE' and pad['width_pm']==pad['height_pm']>stack['drill_diameter_pm']>0 and stack['material']=='COPPER'
  assert (pad['width_pm'],stack['drill_diameter_pm'])==((60000000,40000000) if pad['padstack_id_fold'].endswith('_60') else (100000000,60000000))
 by={'via:'+row['via_id_fold']:row for row in rows}
 assert len(by)==len(rows)
 assert set(by)-set(native)==set(inv['source_l04_owners_outside_target_boundary'])
 assert set(native)-set(by)==set(inv['non_l04_owners_inside_target_boundary'])
 exceptions={}; path_maps=[]
 for i,j in ja.items():
  path,junction=paths[i],junctions[j]
  assert [int(first[i]),int(second[i])]==path['original_active_endpoints']==junction['original_active_endpoints']
  assert path['l02_contact_ordinal']==junction['contact_ordinal'] and count[i]==1
  remap=path['action']=='REMAP_EXISTING_L02_FIRST_LEG_L04_ENDPOINT'
  assert remap or path['action']=='REPLACE_EXISTING_L02_FIRST_LEG_WITH_TWO_SERIES_LEGS'
  assert (T in path['original_active_endpoints'])==remap
  assert len(path['l04_touching_via_ids'])==(1 if remap else 2)
  source={row['via_id_fold']:row for row in junction['source_vias_in_native_path_order']}
  for leg,prefix in zip(path['old_l02_legs'],('first_leg_','second_leg_'),strict=True):
   for quantity in ('resistance_ohm','inductance_h'): assert leg[quantity]==junction[prefix+quantity]
  for via in path['l04_touching_via_ids']:
   key='via:'+via; assert key not in exceptions and key not in ordinary_by_via
   row=by[key]
   for name in ('source_record_sha256','start_layer_id_fold','end_layer_id_fold','start_node_id_fold','end_node_id_fold','start_x_pm','start_y_pm','end_x_pm','end_y_pm','padstack_id_fold','rotation_microdegrees'):
    assert row[name]==source[via][name],(via,name)
   side='start' if row['start_layer_id_fold']==L else 'end'
   assert row[side+'_node_id_fold']==path['l04_node_id_fold']
   assert (row[side+'_x_pm'],row[side+'_y_pm'])==(path['x_pm'],path['y_pm'])
   exceptions[key]=(1 if remap else 2,i,j)
  path_maps.append({**path,'junction_array_ordinal':j,'existing_first_leg_expanded_index':FIRST+j})
 assert len(exceptions)==30 and set(ordinary_by_via)|set(exceptions)==set(by)
 assert sum(v[0]==1 for v in exceptions.values())==10 and len({v[1] for v in exceptions.values() if v[0]==2})==10
 expanded=dict(zip(map(int,ordinary),map(int,expanded_native_indices(ordinary,replaced)),strict=True))
 records=[]
 for key,row in by.items():
  if key in exceptions: category,i,j=exceptions[key]; expanded_i=FIRST+j
  else: category,i,j=0,ordinary_by_via[key],-1; expanded_i=expanded[i]
  at_start=row['start_layer_id_fold']==L
  assert at_start^(row['end_layer_id_fold']==L)
  assert row['start_x_pm']==row['end_x_pm'] and row['start_y_pm']==row['end_y_pm']
  records.append((category,i,j,expanded_i,row,at_start))
 records.sort(key=lambda row:(row[0],row[3],row[4]['ordinal']))
 selected=np.array([row[1] for row in records],dtype=np.int64)
 assert np.all(count[selected]==1) and np.all(resistance[selected]>0) and np.all(inductance[selected]>=0)
 category=np.array([row[0] for row in records],dtype=np.int8)
 assert np.bincount(category).tolist()==[76136,10,20]
 source_rows=[row[4] for row in records]
 xy=np.array([(row['start_x_pm'],row['start_y_pm']) for row in source_rows],dtype=np.int64)
 unique,group,group_counts=np.unique(xy,axis=0,return_inverse=True,return_counts=True)
 assert len(unique)==38278 and np.array_equal(unique[group],xy)
 definitions=[{'padstack_index':j,'l04_pad_shape':pad,'padstack':stack} for j,(pad,stack) in enumerate(zip(pads,stacks,strict=True))]
 arrays={
  'category':category,'active_finite_index':selected,'original_finite_index':original[selected],
  'expanded_branch_index':np.array([row[3] for row in records],dtype=np.int64),
  'junction_array_ordinal':np.array([row[2] for row in records],dtype=np.int64),
  'l02_contact_ordinal':np.array([junctions[row[2]]['contact_ordinal'] if row[2]>=0 else -1 for row in records],dtype=np.int64),
  'original_first_active_index':first[selected],'original_second_active_index':second[selected],
  'original_count':count[selected],'original_resistance_ohm':resistance[selected],'original_inductance_h':inductance[selected],
  'original_l04_target_side':np.where(category==2,-1,np.where(first[selected]==T,0,1)).astype(np.int8),
  'source_via_ordinal':np.array([row['ordinal'] for row in source_rows],dtype=np.int64),
  'l04_endpoint_is_start':np.array([row[5] for row in records],dtype=np.bool_),
  'x_pm':xy[:,0],'y_pm':xy[:,1],
  'rotation_microdegrees':np.array([row['rotation_microdegrees'] for row in source_rows],dtype=np.int64),
  'padstack_index':np.array([used.index(row['padstack_id_fold']) for row in source_rows],dtype=np.int16),
  'coincident_group_xy_pm':unique,'coincident_group_index':group,'coincident_group_counts':group_counts,
  'via_ids_json_utf8':pack([row['via_id'] for row in source_rows]),
  'source_rows_json_utf8':pack(source_rows),'path_maps_json_utf8':pack(path_maps),'pad_definitions_json_utf8':pack(definitions),
 }
 assert time.monotonic()<deadline
 result={
  'program':'SPD Decap PI Evaluator','version':'0.23.1','inputs':ins,'script_sha256':sha(Path(__file__)),
  'source_via_count_by_category':np.bincount(category).tolist(),'ordinary_native_branches':len(ordinary),
  'endpoint_remaps':sum(len(p['l04_touching_via_ids'])==1 for p in path_maps),
  'hidden_midpoint_splits':sum(len(p['l04_touching_via_ids'])==2 for p in path_maps),
  'source_via_count':len(source_rows),'unique_xy_count':len(unique),'island_count':len(ids),
  'native_original_links':len(incident),'original_composites_already_removed':len(replaced),
  'accepted_expanded_branch_count':FIRST+40,'pad_definitions':definitions,'path_maps':path_maps,
  'scope':'Source-contact inputs only. Category0 maps ordinary native branches into the accepted expanded array;1 remaps existing L02 first-leg endpoints;2 locates two source vias at each of10 hidden midpoints inside existing first legs. Original R/L values belong to original composites, not new split legs. Exact XY groups are not qualified electrical contacts. No original composite is re-added. No conductor union, mesh, G/C, solve, or accuracy claim.',
  'elapsed_s':time.monotonic()-started,
 }
 return result,arrays

def main():
 parser=argparse.ArgumentParser(description=__doc__); mode=parser.add_mutually_exclusive_group(required=True)
 mode.add_argument('--output',type=Path); mode.add_argument('--dry-source-count',action='store_true'); mode.add_argument('--self-check',action='store_true')
 args=parser.parse_args()
 if args.self_check: self_check(); print('PASS_L04_SOURCE_CONTACT_SELF_CHECK'); return
 out=args.output.resolve() if args.output else None
 if out: out.mkdir(exist_ok=False); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 try:
  result,arrays=collect()
  result['status']='COMPLETED_L04_SOURCE_CONTACT_INPUT_LEDGER' if out else 'PASS_L04_FULL_SOURCE_CONTACT_DRY_CHECK'
  if out:
   target=out/'contact-ledger-inputs.npz'; base.mass.atomic_npz(target,**arrays)
   result['output']={'path':str(target),'sha256':sha(target),'bytes':target.stat().st_size}
   base.mass.atomic_json(out/'result.json',result)
  print(json.dumps({key:result[key] for key in ('status','source_via_count_by_category','source_via_count','unique_xy_count','elapsed_s')}))
 except BaseException as exc:
  if out: base.mass.atomic_json(out/'failure.json',{'status':'STOP_L04_SOURCE_CONTACT_INPUT_LEDGER','error_type':type(exc).__name__,'error':str(exc)})
  raise

if __name__=='__main__': main()
