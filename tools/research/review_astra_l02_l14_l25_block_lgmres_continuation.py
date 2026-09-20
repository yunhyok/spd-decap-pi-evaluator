"""Independent saved-field acceptance replay for combined L02/L14/L25 LGMRES continuation."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import numpy as np
from scipy import sparse

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'
P={
'driver':(R/'astra-l02-l14-l25-combined-block-lgmres-02/driver-at-run.py','7d59f69d9b7a85d8ea1da558a0eb9ebbb1e517c0294611b78f6d8cfb388cf8a7'),
'result':(R/'astra-l02-l14-l25-combined-block-lgmres-02/result.json','832841f9277bc47a9be5ef87b1cee940bff176405698f48ea4fb08ed26e9c097'),
'field':(R/'astra-l02-l14-l25-combined-block-lgmres-02/field.npz','c7360f92732f956c6c63c3e564cd97315b26a09a9a79414446bdcd4a6fce76f3'),
'diagnostic':(R/'astra-l02-l14-l25-combined-block-lgmres-02/unvalidated-diagnostic.json','506985519b1f5402163d61198698ec5ddc2dad2de1fe4ef655a39a229eae6b0b'),
'external':(R/'astra-l02-l14-l25-combined-block-lgmres-02/external-budget.json','0c2d721588653bfb423e57b0ce61cab2bb1b067a4e443586c2ad884c25fb8eaf'),
'operator':(R/'astra-l02-l14-l25-combined-operator-02/combined-operator.npz','45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8'),
'mapping':(R/'astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz','a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469'),
'categories':(R/'astra-combined-source-categories-01/combined-source-categories.npz','f9253786da9eb88367c6bde51e78c64b647528e2856358b1fdd204ade1db6e9f'),
'warm':(R/'astra-l02-l14-l25-combined-block-lgmres-01/unvalidated-field.npz','a67921f72714411ca233e63b33ec77595fe77e1359e10e90e83489c773480a44'),
}
N,L14,L25,NP,NQ=756_889,903_945,1_483_296,2_340_069,604_031; POS,NEG,GAUGE=2699,2656,0
def h(p):
  with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def csc(z,k): return sparse.csc_matrix((z[k+'_data'],z[k+'_indices'],z[k+'_indptr']),shape=tuple(z[k+'_shape']))
def ma(x):return float(np.max(np.abs(x),initial=0.))
def gamma(n):
  x=np.asarray(n,dtype=float)*np.finfo(float).eps; return x/(1-x)
def rowdeg(a):return np.bincount(a.indices,minlength=a.shape[0])
def branch_action(a,b,y,v):
  cur=y*(v[a]-v[b]); out=np.zeros(v.size,complex); np.add.at(out,a,cur);np.add.at(out,b,-cur);return out
def lap(a,b,y):
  m=sparse.coo_matrix((np.r_[y,y,-y,-y],(np.r_[a,b,a,b],np.r_[a,b,b,a])),shape=(NP,NP)).tocsc();m.sum_duplicates();m.eliminate_zeros();return m
def pair(x):return [float(x.real),float(x.imag)]
def run(out):
  out.mkdir(parents=True,exist_ok=False)
  for k,(p,s) in P.items():assert h(p)==s,k
  diag=json.loads(P['diagnostic'][0].read_text()); result=json.loads(P['result'][0].read_text()); external=json.loads(P['external'][0].read_text())
  with np.load(P['field'][0],allow_pickle=False) as z:
    v=z['active_voltage_v']; q=z['l25_branch_current_a']; amp=float(z['source_current_amplitude_a'][0]); port=z['source_positive_negative_gauge_active_indices']; callbacks=int(z['completed_outer_callbacks'][0]); opcount=int(z['solver_operator_matvec_count'][0]); pcount=int(z['solver_preconditioner_matvec_count'][0]); warmsha=bytes(z['warm_combined_field_sha256_utf8']).decode(); opsha=bytes(z['combined_operator_sha256_utf8']).decode()
  with np.load(P['warm'][0],allow_pickle=False) as z:warmv=z['active_voltage_v'];warmq=z['l25_branch_current_a'];assert warmv[GAUGE]==0
  with np.load(P['operator'][0],allow_pickle=False) as z:y,r,b=(csc(z,k) for k in ('y','r','b'));portop=z['positive_negative_gauge_active_indices']
  with np.load(P['mapping'][0],allow_pickle=False) as z:a,bb,fy=(z['final_finite_first_active_index'],z['final_finite_second_active_index'],z['final_finite_admittance_s'])
  assert y.shape==(NP,NP) and r.shape==(NQ,NQ) and b.shape==(NP,NQ) and v.shape==(NP,) and q.shape==(NQ,)
  assert a.shape==bb.shape==fy.shape==(1_692_409,) and np.array_equal(port,(POS,NEG,GAUGE)) and np.array_equal(portop,port) and amp==1 and opsha==P['operator'][1] and warmsha==P['warm'][1]
  # Whole-sheet partition/target ownership and B placement.
  native_targets=np.array([718402-1,258027-1,349710-1]); native=np.setdiff1d(np.arange(N-1),native_targets,assume_unique=True);l14=np.r_[718402-1,np.arange(N-1,L14-1)];l25=np.r_[258027-1,np.arange(L14-1,L25-1)];l02=np.r_[349710-1,np.arange(L25-1,NP-1)]
  own=np.full(NP-1,-1,np.int8)
  for i,x in enumerate((native,l14,l25,l02)):assert np.all(own[x]<0);own[x]=i
  assert np.all(own>=0) and tuple(map(len,(native,l14,l25,l02)))==(756885,147057,579352,856774) and b[1:,:][native,:].nnz==b[1:,:][l14,:].nnz==b[1:,:][l02,:].nnz==0 and b[1:,:][l25,:].nnz==b.nnz
  # Exact finite branch action and original seven source categories.
  fa=branch_action(a,bb,fy,v); fm=lap(a,bb,fy); fma=fm@v; vab=np.abs(v); bm=np.zeros(NP); pm=np.abs(fy)*(vab[a]+vab[bb]);np.add.at(bm,a,pm);np.add.at(bm,bb,pm); fd=np.bincount(np.r_[a,bb],minlength=NP)+rowdeg(fm); fbound=gamma(16*(fd+10))*(bm+abs(fm)@vab); ferr=fa-fma; fratio=ma(ferr/np.maximum(fbound,np.finfo(float).tiny))
  yact=y@v; physical=fa.copy(); sm=fm.copy(); mag=bm+abs(fm)@vab+abs(y)@vab; count=fd+rowdeg(y); powers={}; names=[]
  with np.load(P['categories'][0],allow_pickle=False) as z:
    names=json.loads(np.asarray(z['category_names_json_utf8'],np.uint8).tobytes()); assert set(names)=={'retained_gc','termination','l14_sheet_dc','l14_distributed_gc','l25_distributed_gc','l02_distributed_gc','l02_sheet_dc'} and np.array_equal(z['positive_negative_gauge_active_indices'],port)
    for name in names:
      m=csc(z,name);act=m@v;physical+=act;sm=(sm+m).tocsc();mag+=abs(m)@vab;count+=rowdeg(m);powers[name]=np.conj(np.vdot(v,act));assert m.shape==(NP,NP)
  diff=sm-y;diff.eliminate_zeros(); sbound=abs(diff)@vab+gamma(16*(count+20))*mag; serr=physical-yact; sratio=ma(serr/np.maximum(sbound,np.finfo(float).tiny))
  kcl=yact+b@q;kcl[POS]-=1;kcl[NEG]+=1; pkcl=physical+b@q;pkcl[POS]-=1;pkcl[NEG]+=1; con=r@q-b.T@v
  sourcepower=np.conj(np.vdot(v,yact)); powers={'finite_branches':np.conj(np.vdot(v,fa)),**powers,'l25_rt0_resistance':np.vdot(q,r@q)}; zdd=v[POS]-v[NEG]; closure=abs(sum(powers.values())-zdd); source_replay=abs(sum(list(powers.values())[:-1])-sourcepower)/(abs(sourcepower)+sum(abs(x) for x in list(powers.values())[:-1]))
  yk=y[1:,1:];bk=b[1:,:];fro=np.sqrt(np.sum(abs(yk.data)**2)+2*np.sum(abs(bk.data)**2)+np.sum(abs(r.data)**2));back=np.sqrt(np.linalg.norm(kcl[1:])**2+np.linalg.norm(con)**2)/(fro*np.sqrt(np.linalg.norm(v[1:])**2+np.linalg.norm(q)**2)+np.sqrt(2))
  pn=np.asarray([np.asarray(diag['physical']['power_contributions_ohm'][k][0])+1j*np.asarray(diag['physical']['power_contributions_ohm'][k][1]) for k in powers]); pv=np.asarray(list(powers.values())); perr=ma(pv-pn)/max(ma(pn),np.finfo(float).tiny)
  # Scaled final residual and callback arithmetic.
  ps=1/np.sqrt(np.asarray(abs(yk).sum(axis=1)).ravel()+np.asarray(abs(bk).sum(axis=1)).ravel());qs=1/np.sqrt(np.asarray(abs(bk).sum(axis=0)).ravel()+np.asarray(abs(r).sum(axis=1)).ravel());rhs=np.zeros(NP-1+NQ,complex);rhs[POS-1]=1;rhs[NEG-1]=-1;srhs=np.r_[ps,qs]*rhs;sv=np.r_[v[1:]/ps,q/qs];sres=np.r_[ps*(yk@v[1:]+bk@q),qs*(bk.T@v[1:]-r@q)]-srhs;final=float(np.linalg.norm(sres)/np.linalg.norm(srhs));hist=diag['lgmres']['outer_history']; expected_calls=((1,0),(3,2),(5,4),(24,23));history_ok=len(hist)==4 and all(row['callback_index']==i and row['completed_outer_cycles']==i and (row['operator_matvec_count'],row['preconditioner_matvec_count'])==expected_calls[i] for i,row in enumerate(hist))
  assert ma(ferr)/max(ma(fma),1e-300)<3e-12 and fratio<=1 and ma(serr)/max(ma(yact),1e-300)<5e-12 and sratio<=1 and ma(kcl)<1e-7 and ma(pkcl)<1e-7 and ma(con)<1e-7 and back<=1e-9 and all(x.real>=-1e-10 for x in powers.values()) and closure<=max(abs(zdd),1e-300)*1e-7 and history_ok and callbacks==4 and opcount==24 and pcount==23
  assert result['status']=='COMPLETED_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ' and diag['lgmres']['info']==0 and final<=1e-9 and abs(final-diag['lgmres']['final_scaled_residual_relative'])<1e-18
  assert external['status']=='COMPLETED_NATIVE_WORKER' and external['exit_code']==0 and external['elapsed_s']<=600 and external['sampled_peak_private_bytes']<=external['max_memory_bytes']==24*2**30
  metrics={'finite_action_relative':ma(ferr)/max(ma(fma),1e-300),'finite_envelope_ratio':fratio,'source_action_relative':ma(serr)/max(ma(yact),1e-300),'source_envelope_ratio':sratio,'csc_kcl_max_a':ma(kcl),'physical_kcl_max_a':ma(pkcl),'constitutive_max_v':ma(con),'backward':float(back),'zdd_ohm':pair(zdd),'power_closure_ohm':float(closure),'power_replay_relative':float(source_replay),'power_vs_diagnostic_relative':perr,'final_scaled_residual_relative':final,'lgmres_info':int(diag['lgmres']['info']),'callback_history_exact':history_ok,'operator_preconditioner_calls':[opcount,pcount],'category_power_ohm':{k:pair(x) for k,x in powers.items()}}
  np.savez_compressed(out/'replay-arrays.npz',finite_error_a=ferr,source_error_a=serr,finite_envelope_a=fbound,source_envelope_a=sbound,scaled_residual=sres)
  metrics['external_guard']={'elapsed_s':external['elapsed_s'],'peak_private_bytes':external['sampled_peak_private_bytes'],'max_runtime_s':external['max_runtime_s'],'max_memory_bytes':external['max_memory_bytes']}
  receipt={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_CONDITIONAL_LGMRES_CONTINUATION_SAVED_REPLAY','inputs':{k:{'path':str(p),'sha256':s} for k,(p,s) in P.items()},'metrics':metrics,'scope':'Acceptance replay for the pinned conditional combined operator field only. It does not compare PowerSI, establish board accuracy, add Green/FMM physics, or validate spatial/model convergence.'}
  (out/'independent-review.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':run(R/'astra-l02-l14-l25-combined-block-lgmres-continuation-review-02')
