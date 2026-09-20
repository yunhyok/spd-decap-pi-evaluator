"""SPD Decap PI Evaluator v0.23.1: bounded normalized rows 0/75 RT0 cross."""
from __future__ import annotations
import json, traceback
from pathlib import Path
from time import monotonic
import numpy as np
from assemble_astra_l02_finite_charge_self import prism_union
from check_astra_l02_shared_edge_current import R, ROOT, sha
from measure_astra_l02_adaptive_nonself import AnisotropicOuter
from prepare_astra_retained_sheet_current_green import chunks
from qualify_astra_tetra_volume_green import tetra_inner

OUT=R/'astra-l02-rows0-75-whitened-current-cross-20260912-04'
CURRENT=R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
SINGLE=R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz'
OLD=R/'astra-l02-shared-edge-current-20260912-01/result.json'
PINS={CURRENT:'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',SINGLE:'5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05',OLD:'47ce499360a9e73f31a9b4ed0979d78b8813fc47c63c58b32ea227e3b582a2f6'}
SOURCES={Path(__file__).with_name('check_astra_l02_shared_edge_current.py'):'999b0f58db4b5211796abdb5dc6b0e80d9fcd4801c46fc6eb7bd333f31456e15',Path(__file__).with_name('assemble_astra_l02_finite_charge_self.py'):'88946f1a6ab3ed4b18493da0eb3c2efe10408a2ed13e1e26080b7377c8ffc5ad',Path(__file__).with_name('prepare_astra_retained_sheet_current_green.py'):'967a64f8fdc7468a01aeefd8c319cd4e4a2c84c6a8c54d0a75899bfebca9eb9b',Path(__file__).with_name('measure_astra_l02_adaptive_nonself.py'):'6294cd99aabbed5a8b9756fd0c5acaf13e9edd03170c79c9f924d19ac1969504',Path(__file__).with_name('qualify_astra_tetra_volume_green.py'):'24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb'}
TOLS=(5e-5,1.25e-5,3.125e-6,7.8125e-7); GATE=5e-5; ABS_GATE=5e-6

class WhiteOuter(AnisotropicOuter):
 def __init__(self,observer,source,ot,st,c,d,os,ss,deadline):
  self.ov=ot; self.sv=st; self.c=np.asarray(c)*np.asarray(os); self.d=np.asarray(d)*np.asarray(ss)
  self.od=abs(np.linalg.det(ot[1:]-ot[0]))*20e-6; self.sd=abs(np.linalg.det(st[1:]-st[0]))*20e-6
  self.volume=sum(abs(np.linalg.det(t[1:]-t[0]))*(b[1]-b[0])/2 for t,b in observer); super().__init__(observer,source,deadline)
 def potential(self,points):
  assert monotonic()<self.deadline,'120 s normalized-current boundary'; self.points+=len(points); scalar=np.zeros(len(points)); moment=np.zeros((len(points),3))
  for source,_,_ in self.sources:
   s,m=tetra_inner(source,points); scalar+=s; moment+=m
  r=points[:,:2]; test=(self.c.sum()*r-self.c@((self.ov-self.anchor[:2])))/self.od
  trial=((self.d.sum()*r-self.d@(self.sv-self.anchor[:2]))*scalar[:,None]+self.d.sum()*moment[:,:2])/self.sd
  value=1e-7*self.volume*np.einsum('pi,pi->p',test,trial); assert np.isfinite(value).all(); return value

def point_cross(point,basis,weight,a,b):
 d=np.linalg.norm(point[a][:,None]-point[b][None],axis=-1); inv=np.divide(1.,d,out=np.zeros_like(d),where=d>0)
 return 1e-7*np.einsum('p,pic,pq,qjc,q->ij',weight[a],basis[a],inv,basis[b],weight[b],optimize=True)
def whiten_matrix(w): return np.block([[np.eye(3),w],[w.T,np.eye(3)]])
def energy(previous,current):
 l=np.linalg.cholesky(current); e=np.linalg.solve(l,previous-current); e=np.linalg.solve(l,e.T).T; return float(np.max(np.abs(np.linalg.eigvalsh((e+e.T)/2))))
def aggregate_abs(receipt):
 # The adaptive rule exposes only relative aggregate indicators; convert its own
 # accepted normalized integral into an absolute bound in self=I units.
 return abs(receipt['relative_aggregate_indicator']*receipt['integral_h'])

def run():
 started=monotonic(); history=[]; report=None; OUT.mkdir(exist_ok=False); (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 try:
  for p,h in {**PINS,**SOURCES}.items(): assert sha(p)==h,f'pin mismatch {p}'
  old=json.loads(OLD.read_text(encoding='utf-8')); oldf,oldr=map(float,old['directed_cross_h'])
  with np.load(CURRENT,allow_pickle=False) as z: payload={k:z[k] for k in ('piece_triangle_xy_m','piece_parent_free_ordinal','original_free_triangle_xy_m','compact_local_columns','local_signs')}
  rows=np.array([0,75]); tri=payload['original_free_triangle_xy_m'][rows]; cols=payload['compact_local_columns'][rows]; signs=payload['local_signs'][rows]
  assert np.intersect1d(cols[0],cols[1]).tolist()==[75]; actual=np.intersect1d(np.flatnonzero(np.any(payload['compact_local_columns']==75,axis=1)),np.unique(payload['piece_parent_free_ordinal'])); assert np.array_equal(actual,rows)
  assert all(np.count_nonzero(payload['piece_parent_free_ordinal']==r)==1 for r in rows)
  with np.load(SINGLE,allow_pickle=False) as z:
   selected=[int(np.flatnonzero(z['original_free_ordinals']==r)[0]) for r in rows]; selfh=[z['physical_block_h'][i] for i in selected]; selfp=[z['point_block_h'][i] for i in selected]
  l0,l1=np.linalg.cholesky(selfh[0]),np.linalg.cholesky(selfh[1]); coeff=[np.linalg.inv(l0),np.linalg.inv(l1)]
  bounds=np.array([55e-6,75e-6]); domains=[[(tri[i],bounds)] for i in range(2)]; tetra=[prism_union(tri[i:i+1],bounds)[0] for i in range(2)]
  points=[]; bases=[]; weights=[]
  for i in range(2):
   small=dict(piece_triangle_xy_m=tri[i:i+1],piece_parent_free_ordinal=np.array([0]),original_free_triangle_xy_m=tri[i:i+1],compact_local_columns=cols[i:i+1],local_signs=signs[i:i+1]); p,_,b,w=next(chunks(small)); points.append(p[0]); bases.append(b[0]); weights.append(w[0])
  pointf=point_cross(points,bases,weights,0,1); pointr=point_cross(points,bases,weights,1,0)
  deadline=monotonic()+120.; fw={}; rv={}
  for i in range(3):
   for j in range(3):
    fw[i,j]=WhiteOuter(domains[0],tetra[1],tri[0],tri[1],coeff[0][i],coeff[1][j],signs[0],signs[1],deadline)
    rv[i,j]=WhiteOuter(domains[1],tetra[0],tri[1],tri[0],coeff[1][j],coeff[0][i],signs[1],signs[0],deadline)
  previous=None
  for tol in TOLS:
   assert monotonic()<deadline,'120 s bounded run limit'; wf=np.empty((3,3)); wr=np.empty((3,3))
   for i in range(3):
    for j in range(3): wf[i,j]=fw[i,j].refine(tol).real; wr[j,i]=rv[i,j].refine(tol).real
   w=(wf+wr.T)/2; normalized=whiten_matrix(w); rec=float(np.linalg.norm(wf-wr.T)/np.linalg.norm(wf)); change=None if previous is None else energy(previous,normalized)
   fr={f'{i},{j}':o.receipt() for (i,j),o in fw.items()}; rr={f'{i},{j}':o.receipt() for (i,j),o in rv.items()}
   # receipt integral is added below by this driver, making the absolute gate auditable.
   for key,val in fr.items(): val['integral_h']=float(wf[tuple(map(int,key.split(',')))])
   for key,val in rr.items(): i,j=map(int,key.split(',')); val['integral_h']=float(wr[j,i])
   absolute=max([aggregate_abs(x) for x in list(fr.values())+list(rr.values())])
   history.append(dict(tolerance=tol,raw_forward_w=wf.tolist(),raw_reverse_w=wr.tolist(),arithmetic_reciprocal_w=w.tolist(),raw_reciprocity=rec,maximum_adaptive_absolute_indicator=absolute,energy_normalized_refinement=change,forward_receipts=fr,reverse_receipts=rr))
   if previous is not None and rec<=GATE and absolute<=ABS_GATE and change<=GATE: break
   previous=normalized
  else: raise RuntimeError('normalized reciprocity/absolute/refinement gate failed')
  physicalf=l0@wf@l1.T; physicalr=l1@wr@l0.T; physical=l0@w@l1.T; old_delta=[float(physicalf[1,1]-oldf),float(physicalr[1,1]-oldr)]
  pointlocal=np.block([[selfp[0],pointf],[pointr,selfp[1]]]); physlocal=np.block([[selfh[0],physical],[physical.T,selfh[1]]]); unique=np.unique(cols); inc=np.zeros((6,len(unique))); np.add.at(inc,(np.arange(6),np.searchsorted(unique,cols.reshape(-1))),1.); correction=inc.T@physlocal@inc; sr,sc=np.nonzero(correction); action=np.arange(1,len(unique)+1,dtype=float); delta=inc.T@(physlocal-pointlocal)@(inc@action)
  report=dict(status='PASS_ROWS0_75_WHITENED_RT0_CROSS',original_free_rows=rows.tolist(),local_current_columns=cols.tolist(),local_signs=signs.tolist(),global_current_columns=unique.tolist(),raw_forward_w=wf.tolist(),raw_reverse_w=wr.tolist(),arithmetic_reciprocal_w=w.tolist(),reconstructed_raw_forward_h=physicalf.tolist(),reconstructed_raw_reverse_h=physicalr.tolist(),reconstructed_candidate_cross_h=physical.tolist(),accepted_old_11_independent_reconstruction_delta_h=old_delta,authoritative_point_forward_h=pointf.tolist(),authoritative_point_reverse_h=pointr.tolist(),physical_local_h=physlocal.tolist(),point_local_h=pointlocal.tolist(),sparse_correction_h=correction.tolist(),independent_physical_minus_point_action_h=delta.tolist(),history=history,pins={str(p.relative_to(ROOT)):h for p,h in {**PINS,**SOURCES}.items()},scope='Only global current 75 has exactly rows 0 and 75. The five global current columns touched here do not have complete support, and no board/Green action is formed.')
  np.savez_compressed(OUT/'rows0-75-whitened-current-cross.npz',original_free_rows=rows,local_current_columns=cols,local_signs=signs,global_current_columns=unique,local_to_global_incidence=inc,raw_forward_w=wf,raw_reverse_w=wr,arithmetic_reciprocal_w=w,reconstructed_raw_forward_h=physicalf,reconstructed_raw_reverse_h=physicalr,reconstructed_candidate_cross_h=physical,authoritative_point_forward_h=pointf,authoritative_point_reverse_h=pointr,physical_local_h=physlocal,point_local_h=pointlocal,sparse_correction_row=sr,sparse_correction_col=sc,sparse_correction_data_h=correction[sr,sc],independent_physical_minus_point_action_h=delta)
 except Exception:
  report=dict(status='STOP_ROWS0_75_WHITENED_CURRENT_CROSS',traceback=traceback.format_exc(),history=history); (OUT/'failure.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8'); raise
 finally:
  if report is not None:
   report['driver_sha256']=sha(Path(__file__)); report['elapsed_s']=monotonic()-started; (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
if __name__=='__main__': run()
