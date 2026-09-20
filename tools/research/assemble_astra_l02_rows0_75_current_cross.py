"""SPD Decap PI Evaluator v0.23.1: bounded rows 0/75 current-cross check."""
from __future__ import annotations
import json, traceback
from pathlib import Path
from time import monotonic
import numpy as np
from assemble_astra_l02_finite_charge_self import prism_union
from check_astra_l02_shared_edge_current import CurrentOuter, R, ROOT, sha
from prepare_astra_retained_sheet_current_green import chunks

OUT=R/'astra-l02-rows0-75-current-cross-20260912-01'
CURRENT=R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
SINGLE=R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz'
OLD=R/'astra-l02-shared-edge-current-20260912-01/result.json'
PINS={CURRENT:'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',SINGLE:'5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05',OLD:'47ce499360a9e73f31a9b4ed0979d78b8813fc47c63c58b32ea227e3b582a2f6'}
SOURCES={Path(__file__).with_name('check_astra_l02_shared_edge_current.py'):'999b0f58db4b5211796abdb5dc6b0e80d9fcd4801c46fc6eb7bd333f31456e15',Path(__file__).with_name('assemble_astra_l02_finite_charge_self.py'):'88946f1a6ab3ed4b18493da0eb3c2efe10408a2ed13e1e26080b7377c8ffc5ad',Path(__file__).with_name('prepare_astra_retained_sheet_current_green.py'):'967a64f8fdc7468a01aeefd8c319cd4e4a2c84c6a8c54d0a75899bfebca9eb9b',Path(__file__).with_name('measure_astra_l02_adaptive_nonself.py'):'6294cd99aabbed5a8b9756fd0c5acaf13e9edd03170c79c9f924d19ac1969504',Path(__file__).with_name('qualify_astra_tetra_volume_green.py'):'24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb'}
TOLS=(5e-5,1.25e-5,3.125e-6,7.8125e-7); GATE=5e-5

def point_cross(point,basis,weight,a,b):
    d=np.linalg.norm(point[a][:,None]-point[b][None],axis=-1); inv=np.divide(1.,d,out=np.zeros_like(d),where=d>0)
    return 1e-7*np.einsum('p,pic,pq,qjc,q->ij',weight[a],basis[a],inv,basis[b],weight[b],optimize=True)
def local_matrix(selfs,forward,reverse):
    out=np.zeros((6,6)); out[:3,:3]=selfs[0]; out[3:,3:]=selfs[1]; out[:3,3:]=forward; out[3:,:3]=reverse; return out
def energy(previous,current):
    chol=np.linalg.cholesky(current); error=np.linalg.solve(chol,previous-current); error=np.linalg.solve(chol,error.T).T
    return float(np.max(np.abs(np.linalg.eigvalsh((error+error.T)/2))))
def scatter(matrix,columns):
    unique=np.unique(columns); incidence=np.zeros((6,len(unique))); np.add.at(incidence,(np.arange(6),np.searchsorted(unique,columns.reshape(-1))),1.)
    return unique,incidence,incidence.T@matrix@incidence

def run():
    started=monotonic(); history=[]; report=None; OUT.mkdir(exist_ok=False); (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
      for path,digest in {**PINS,**SOURCES}.items(): assert sha(path)==digest,f'pin mismatch {path}'
      old=json.loads(OLD.read_text(encoding='utf-8')); oldf,oldr=map(float,old['directed_cross_h']); assert old['original_free_rows']==[0,75] and old['local_indices']==[1,1]
      with np.load(CURRENT,allow_pickle=False) as z: payload={k:z[k] for k in ('piece_triangle_xy_m','piece_parent_free_ordinal','original_free_triangle_xy_m','compact_local_columns','local_signs')}
      rows=np.array([0,75]); triangle=payload['original_free_triangle_xy_m'][rows]; columns=payload['compact_local_columns'][rows]; signs=payload['local_signs'][rows]
      assert np.intersect1d(columns[0],columns[1]).tolist()==[75]
      actual=np.intersect1d(np.flatnonzero(np.any(payload['compact_local_columns']==75,axis=1)),np.unique(payload['piece_parent_free_ordinal'])); assert np.array_equal(actual,rows)
      assert all(np.count_nonzero(payload['piece_parent_free_ordinal']==row)==1 for row in rows)
      bounds=np.array([55e-6,75e-6]); domains=[[(triangle[i],bounds)] for i in range(2)]; tetra=[prism_union(triangle[i:i+1],bounds)[0] for i in range(2)]
      points=[]; bases=[]; weights=[]
      for i in range(2):
        small=dict(piece_triangle_xy_m=triangle[i:i+1],piece_parent_free_ordinal=np.array([0]),original_free_triangle_xy_m=triangle[i:i+1],compact_local_columns=columns[i:i+1],local_signs=signs[i:i+1])
        point,_,basis,weight=next(chunks(small)); points.append(point[0]); bases.append(basis[0]); weights.append(weight[0])
      pointf=point_cross(points,bases,weights,0,1); pointr=point_cross(points,bases,weights,1,0)
      with np.load(SINGLE,allow_pickle=False) as z:
        selected=[int(np.flatnonzero(z['original_free_ordinals']==row)[0]) for row in rows]; selfs=[z['physical_block_h'][i] for i in selected]; selfp=[z['point_block_h'][i] for i in selected]
      assert all(np.isfinite(a).all() for a in selfs+selfp)
      deadline=monotonic()+120.; fw={}; rv={}
      for i in range(3):
       for j in range(3):
        if (i,j)!=(1,1):
          fw[i,j]=CurrentOuter(domains[0],tetra[1],triangle[0],triangle[1],i,j,signs[0,i],signs[1,j],deadline)
          rv[i,j]=CurrentOuter(domains[1],tetra[0],triangle[1],triangle[0],j,i,signs[1,j],signs[0,i],deadline)
      previous=None
      for tolerance in TOLS:
       assert monotonic()<deadline,'120 s bounded run limit'; forward=np.empty((3,3)); reverse=np.empty((3,3))
       for i in range(3):
        for j in range(3):
         if (i,j)==(1,1): forward[i,j],reverse[j,i]=oldf,oldr
         else: forward[i,j]=fw[i,j].refine(tolerance).real; reverse[j,i]=rv[i,j].refine(tolerance).real
       raw=local_matrix(selfs,forward,reverse); candidate=(raw+raw.T)/2
       reciprocity=float(np.linalg.norm(forward-reverse.T)/np.linalg.norm(forward)); refinement=None if previous is None else energy(previous,candidate)
       history.append(dict(tolerance=tolerance,raw_forward_h=forward.tolist(),raw_reverse_h=reverse.tolist(),arithmetic_reciprocal_cross_h=((forward+reverse.T)/2).tolist(),raw_reciprocity=reciprocity,energy_normalized_refinement=refinement,forward_receipts={f'{i},{j}':o.receipt() for (i,j),o in fw.items()},reverse_receipts={f'{i},{j}':o.receipt() for (i,j),o in rv.items()}))
       if previous is not None and reciprocity<=GATE and refinement<=GATE: break
       previous=candidate
      else: raise RuntimeError('cross reciprocity/refinement gate failed')
      arithmetic=(forward+reverse.T)/2; candidate=local_matrix(selfs,arithmetic,arithmetic.T); pointlocal=local_matrix(selfp,pointf,pointr); unique,incidence,correction=scatter(candidate,columns); _,_,rawscatter=scatter(raw,columns)
      action=np.arange(1,len(unique)+1,dtype=float); delta_action=incidence.T@(candidate-pointlocal)@(incidence@action); sr,sc=np.nonzero(correction)
      report=dict(status='PASS_ROWS0_75_FULL_RT0_CROSS_BLOCKS',original_free_rows=rows.tolist(),local_current_columns=columns.tolist(),local_signs=signs.tolist(),global_current_columns=unique.tolist(),reused_directed_pair=dict(local_pair=[1,1],forward_h=oldf,reverse_h=oldr),raw_forward_h=forward.tolist(),raw_reverse_h=reverse.tolist(),arithmetic_reciprocal_cross_h=arithmetic.tolist(),authoritative_point_forward_h=pointf.tolist(),authoritative_point_reverse_h=pointr.tolist(),physical_local_raw_h=raw.tolist(),physical_local_candidate_h=candidate.tolist(),point_local_h=pointlocal.tolist(),sparse_correction_h=correction.tolist(),raw_scattered_h=rawscatter.tolist(),raw_reciprocity=float(np.linalg.norm(raw-raw.T)/np.linalg.norm(candidate)),candidate_reciprocity=float(np.linalg.norm(candidate-candidate.T)/np.linalg.norm(candidate)),independent_physical_minus_point_action_h=delta_action.tolist(),history=history,pins={str(p.relative_to(ROOT)):d for p,d in {**PINS,**SOURCES}.items()},scope='Only global current 75 has exactly these two cells. The five global currents touched by rows 0/75 do not thereby have complete support; no full Green or board action.')
      np.savez_compressed(OUT/'rows0-75-current-cross.npz',original_free_rows=rows,local_current_columns=columns,local_signs=signs,global_current_columns=unique,local_to_global_incidence=incidence,raw_forward_h=forward,raw_reverse_h=reverse,arithmetic_reciprocal_cross_h=arithmetic,authoritative_point_forward_h=pointf,authoritative_point_reverse_h=pointr,physical_local_raw_h=raw,physical_local_candidate_h=candidate,point_local_h=pointlocal,sparse_correction_row=sr,sparse_correction_col=sc,sparse_correction_data_h=correction[sr,sc],independent_physical_minus_point_action_h=delta_action)
    except Exception:
      report=dict(status='STOP_ROWS0_75_CURRENT_CROSS',traceback=traceback.format_exc(),history=history); (OUT/'failure.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8'); raise
    finally:
      if report is not None:
       report['driver_sha256']=sha(Path(__file__)); report['elapsed_s']=monotonic()-started; (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
if __name__=='__main__': run()
