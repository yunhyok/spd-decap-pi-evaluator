"""SPD Decap PI Evaluator v0.23.1: selected exact-face sparse grid map pilot."""
from __future__ import annotations
import hashlib, json, math, traceback
from pathlib import Path
from time import perf_counter
import numpy as np
from scipy import sparse
from shapely import wkb as shapely_wkb
from astra_layered_charge_action import _cubic_weights

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'
OUT=R/'astra-retained-gc-sparse-grid-map-20260912-05'
ART=R/'astra-retained-gc-exact-polygon-support-20260912-11/retained-gc-exact-polygon-support.npz'
QUAL=R/'astra-retained-gc-exact-polygon-support-20260912-12-qualification/result.json'
MAN=R/'astra-retained-gc-exact-polygon-support-20260912-11/manifest.json'
DIAG=R/'astra-retained-gc-exact-polygon-support-20260912-diagnostic/first-large-checkpoint.json'
CP=R/'astra-retained-gc-exact-polygon-support-20260912-09-checkpoints'
PINS={ART:'8aa88cfc280a2ea011b572a7571a8a166f8d48cb09404fd7c365894ecf16464d',QUAL:'209b1780c98de9606a3b67b3a2165f77f85ba8ef1cfddd57bc4edc45789226db',MAN:'ef14e6d8ddce70dc3a6b2d0ecaddb609bfc5269c54c4ba5907a473e4b9355178',DIAG:'d2fe16007a8f76cbd864afeff96483c4b96f74a83a9aebbb7a7d8201ab858ada',Path(__file__).with_name('astra_layered_charge_action.py'):'210d5627323262480625b8fac806ddf7484d85e7f19662e1503b8c787e4b1e81',Path(__file__).with_name('astra_fixed_vertical_charge_action.py'):'3022a35c4450e428661a30e5ea9316721a5a33beb7b43bc14cab0a14773e251f'}
LARGE='spd-surface-island:9e1fc7deba01aafe928bb0fc'; H=125e-6
FULL_MIN=np.array([-49255.00066666667,-49255.00066666667])*1e-6; FULL_MAX=np.array([49255.00066666667,49263.666666666664])*1e-6
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stencil(points,origin,shape):
 scaled=(points-origin)/H; cell=np.floor(scaled).astype(int); offsets=np.arange(-1,3); ix=cell[:,0,None]+offsets; iy=cell[:,1,None]+offsets
 assert np.all(ix>=0) and np.all(iy>=0) and np.all(ix<shape[0]) and np.all(iy<shape[1])
 return ix,iy,_cubic_weights(scaled[:,0]-cell[:,0]),_cubic_weights(scaled[:,1]-cell[:,1])
def cache_rule(island,wkb):
 geometry=shapely_wkb.loads(wkb); assert geometry.wkb==wkb
 key=hashlib.sha256((island+geometry.wkb_hex).encode()).hexdigest(); path=CP/(key+'.npz'); assert path.is_file(),f'missing checkpoint {path.name}'
 with np.load(path,allow_pickle=False) as z: xy=z['xy']; weight=z['weights']
 assert xy.ndim==2 and xy.shape[1]==2 and len(xy)==len(weight) and np.isfinite(xy).all() and np.all(weight>0)
 return path,xy,weight
def run():
 t=perf_counter(); OUT.mkdir(exist_ok=False); (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes()); result=None
 try:
  for p,h in PINS.items(): assert sha(p)==h,f'pin mismatch {p}'
  qualification=json.loads(QUAL.read_text()); assert qualification['status'].startswith('PASS_') and qualification['inputs']['outputs\\research\\astra-retained-gc-exact-polygon-support-20260912-11\\retained-gc-exact-polygon-support.npz']==PINS[ART]
  manifest=json.loads(MAN.read_text()); support_rows=manifest['supports']; assert len(support_rows)==4386
  with np.load(ART,allow_pickle=False) as z:
   ids=json.loads(z['island_ids_json_utf8'].tobytes().decode()); offsets=z['island_wkb_offsets']; blob=z['island_wkb_bytes']; island=z['support_island_index']; facez=z['support_face_z_um']
  lookup={name:i for i,name in enumerate(ids)}; large_index=lookup[LARGE]; large_support=int(np.flatnonzero(island==large_index)[0])
  candidates=[(float(row['area_um2']),i) for i,row in enumerate(support_rows) if int(island[i])!=large_index]; _,small_support=min(candidates)
  selected=[large_support,int(small_support)]; selected_rows=[support_rows[i] for i in selected]
  rules=[]
  for support,row in zip(selected,selected_rows):
   iid=ids[int(island[support])]; lo,hi=int(offsets[int(island[support])]),int(offsets[int(island[support])+1]); wkb=blob[lo:hi].tobytes(); path,xy,w=cache_rule(iid,wkb)
   assert iid==row['island_id'] and float(facez[support])==float(row['conductor_face_z_um']) and abs(math.fsum(map(float,w))-1.)<=2e-15
   rules.append((support,iid,float(facez[support]),path,xy,w,hashlib.sha256(wkb).hexdigest()))
  diag=json.loads(DIAG.read_text()); assert diag['island_id']==LARGE and diag['triangle_points']==len(rules[0][4])==942168 and diag['checkpoint']==rules[0][3].name
  origin=np.floor(FULL_MIN/H)*H-2*H; shape=tuple((np.ceil((FULL_MAX-origin)/H).astype(int)+3).tolist()); cells=int(np.prod(shape)); assert shape==(795,795)
  start=perf_counter(); rr=[]; cc=[]; dd=[]; direct=[]
  for local,(_,_,_,_,xy,w,_) in enumerate(rules):
   ix,iy,wx,wy=stencil(xy*1e-6,origin,shape); direct.append((ix,iy,wx,wy,w)); base=np.arange(len(xy),dtype=np.int64)
   for a in range(4):
    for b in range(4): rr.append((ix[:,a]*shape[1]+iy[:,b]).astype(np.int64)); cc.append(np.full(len(xy),local,dtype=np.int32)); dd.append(w*wx[:,a]*wy[:,b])
  coo_data=np.concatenate(dd); coo_row=np.concatenate(rr); coo_col=np.concatenate(cc); grid_to_face=sparse.coo_matrix((coo_data,(coo_row,coo_col)),shape=(cells,len(rules))).tocsr(); setup=perf_counter()-start
  q=np.array([0.75+0.25j,-1.125+0.5j]); field=(np.sin(np.arange(cells)*0.007)+1j*np.cos(np.arange(cells)*0.011)).astype(complex)
  start=perf_counter(); spread=grid_to_face@q; gather=grid_to_face.T@field; sparse_s=perf_counter()-start
  start=perf_counter(); direct_spread=np.zeros(cells,complex); direct_gather=np.zeros(len(rules),complex)
  for local,(ix,iy,wx,wy,w) in enumerate(direct):
   for a in range(4):
    for b in range(4): np.add.at(direct_spread,ix[:,a]*shape[1]+iy[:,b],q[local]*w*wx[:,a]*wy[:,b]); direct_gather[local]+=np.sum(w*wx[:,a]*wy[:,b]*field[ix[:,a]*shape[1]+iy[:,b]])
  direct_s=perf_counter()-start; spread_rel=float(np.linalg.norm(spread-direct_spread)/max(np.linalg.norm(direct_spread),1e-30)); gather_rel=float(np.linalg.norm(gather-direct_gather)/max(np.linalg.norm(direct_gather),1e-30)); bilinear=float(abs(field@spread-gather@q)/max(abs(field@spread),1e-30)); hermitian=float(abs(np.vdot(field,spread)-np.vdot(gather,q))/max(abs(np.vdot(field,spread)),1e-30)); mass=np.asarray(grid_to_face.sum(axis=0)).ravel(); csc=grid_to_face.tocsc(); compensated=np.array([math.fsum(map(float,csc.data[csc.indptr[i]:csc.indptr[i+1]])) for i in range(csc.shape[1])])
  gates=dict(selected_checkpoint_identity_and_face_metadata=True,large_checkpoint_942168=True,same_algebraic_cubic_spread=float(spread_rel)<=2e-14,direct_gather_equality=float(gather_rel)<=2e-14,ordinary_bilinear_transpose=float(bilinear)<=2e-14,compensated_mass=float(np.max(abs(compensated-1)))<=2e-15)
  if not all(gates.values()): raise ValueError(str(gates))
  lists=sum(x.nbytes for x in rr+cc+dd); concat=coo_data.nbytes+coo_row.nbytes+coo_col.nbytes; direct_cache=sum(sum(x.nbytes for x in item) for item in direct); csr=grid_to_face.data.nbytes+grid_to_face.indices.nbytes+grid_to_face.indptr.nbytes; csc_bytes=csc.data.nbytes+csc.indices.nbytes+csc.indptr.nbytes; dense=q.nbytes+field.nbytes+spread.nbytes+gather.nbytes+direct_spread.nbytes+direct_gather.nbytes
  result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_RETAINED_GC_SELECTED_SPARSE_GRID_MAP',inputs={str(p.relative_to(ROOT)):h for p,h in PINS.items()},grid=dict(full_bounds_m=[FULL_MIN.tolist(),FULL_MAX.tolist()],origin_m=origin.tolist(),shape=shape,spacing_m=H),selected_faces=[dict(support_index=s,island_id=iid,face_z_um=z,checkpoint=str(path.relative_to(ROOT)),checkpoint_sha256=sha(path),point_count=len(xy),wkb_sha256=wkb) for s,iid,z,path,xy,w,wkb in rules],metrics=dict(setup_s=setup,sparse_apply_s=sparse_s,direct_stencil_s=direct_s,nnz=int(grid_to_face.nnz),csr_bytes=int(csr),csc_bytes=int(csc_bytes),coo_list_bytes=int(lists),coo_concatenated_bytes=int(concat),direct_stencil_cache_bytes=int(direct_cache),dense_bytes=int(dense),numpy_array_peak_lower_bound_bytes=int(lists+concat+direct_cache+csr+csc_bytes+dense),naive_csr_mass_max_abs=float(np.max(abs(mass-1))),compensated_csc_mass_max_abs=float(np.max(abs(compensated-1))),spread_relative=spread_rel,direct_gather_relative=gather_rel,ordinary_bilinear_transpose_relative=bilinear,hermitian_optional_relative=hermitian),gates=gates,scope='Same algebraic aggregation of every selected exact quadrature point through the existing cubic XY stencil; this is not quadrature downsampling. Array peak is a lower bound, not process peak. No global 33M-point array, kernel, singular/point-self term, board action, or solve is formed.')
  sparse.save_npz(OUT/'grid-to-selected-face-map.npz',grid_to_face); np.savez_compressed(OUT/'selected-face-map-check.npz',q=q,field=field,spread=spread,gather=gather,mass=mass,selected_support_index=np.asarray(selected),selected_face_z_um=np.asarray([r[2] for r in rules]))
 except Exception:
  result=dict(status='STOP_RETAINED_GC_SELECTED_SPARSE_GRID_MAP',traceback=traceback.format_exc()); (OUT/'failure.json').write_text(json.dumps(result,indent=2)+'\n'); raise
 finally:
  if result is not None:
   result['driver_sha256']=sha(Path(__file__)); result['elapsed_s']=perf_counter()-t; (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__': run()
