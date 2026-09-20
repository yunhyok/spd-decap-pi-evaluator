"""Independent saved-field replay for the stopped combined L02/L14/L25 LGMRES run."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import numpy as np
from scipy import sparse

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'
P={
'driver':(R/'astra-l02-l14-l25-combined-block-lgmres-01/driver-at-run.py','853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692'),
'field':(R/'astra-l02-l14-l25-combined-block-lgmres-01/unvalidated-field.npz','a67921f72714411ca233e63b33ec77595fe77e1359e10e90e83489c773480a44'),
'diagnostic':(R/'astra-l02-l14-l25-combined-block-lgmres-01/unvalidated-diagnostic.json','7e6390ebd935fb83708ae9ff795c98f85f10adefa9149dbb1391d80040cb09dc'),
'operator':(R/'astra-l02-l14-l25-combined-operator-02/combined-operator.npz','45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8'),
'mapping':(R/'astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz','a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469'),
'categories':(R/'astra-combined-source-categories-01/combined-source-categories.npz','f9253786da9eb88367c6bde51e78c64b647528e2856358b1fdd204ade1db6e9f'),
'warm':(R/'astra-l02-l14-l25-combined-block-gmres-02/unvalidated-field.npz','2d1ac88c8465f9c5162bca2daa5212cdb0dd6270f4c9ef38ad912be81ec8c61b'),
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
  diag=json.loads(P['diagnostic'][0].read_text())
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
  ps=1/np.sqrt(np.asarray(abs(yk).sum(axis=1)).ravel()+np.asarray(abs(bk).sum(axis=1)).ravel());qs=1/np.sqrt(np.asarray(abs(bk).sum(axis=0)).ravel()+np.asarray(abs(r).sum(axis=1)).ravel());rhs=np.zeros(NP-1+NQ,complex);rhs[POS-1]=1;rhs[NEG-1]=-1;srhs=np.r_[ps,qs]*rhs;sv=np.r_[v[1:]/ps,q/qs];sres=np.r_[ps*(yk@v[1:]+bk@q),qs*(bk.T@v[1:]-r@q)]-srhs;final=float(np.linalg.norm(sres)/np.linalg.norm(srhs));hist=diag['lgmres']['outer_history'];history_ok=len(hist)==12 and all(row['callback_index']==i and row['completed_outer_cycles']==i and row['operator_matvec_count']==1+21*i and row['preconditioner_matvec_count']==21*i for i,row in enumerate(hist))
  assert ma(ferr)/max(ma(fma),1e-300)<3e-12 and fratio<=1 and ma(serr)/max(ma(yact),1e-300)<5e-12 and sratio<=1 and ma(kcl)<1e-7 and ma(pkcl)<1e-7 and ma(con)<1e-7 and back<=1e-9 and all(x.real>=-1e-10 for x in powers.values()) and closure<=max(abs(zdd),1e-300)*1e-7 and history_ok and callbacks==12 and opcount==pcount==252
  assert diag['lgmres']['info']==12 and final>1e-9 and abs(final-diag['lgmres']['final_scaled_residual_relative'])<1e-18
  metrics={'finite_action_relative':ma(ferr)/max(ma(fma),1e-300),'finite_envelope_ratio':fratio,'source_action_relative':ma(serr)/max(ma(yact),1e-300),'source_envelope_ratio':sratio,'csc_kcl_max_a':ma(kcl),'physical_kcl_max_a':ma(pkcl),'constitutive_max_v':ma(con),'backward':float(back),'zdd_ohm':pair(zdd),'power_closure_ohm':float(closure),'power_replay_relative':float(source_replay),'power_vs_diagnostic_relative':perr,'final_scaled_residual_relative':final,'lgmres_info':12,'callback_history_exact':history_ok,'operator_preconditioner_calls':[opcount,pcount],'category_power_ohm':{k:pair(x) for k,x in powers.items()}}
  np.savez_compressed(out/'replay-arrays.npz',finite_error_a=ferr,source_error_a=serr,finite_envelope_a=fbound,source_envelope_a=sbound,scaled_residual=sres)
  receipt={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'UNVALIDATED_LGMRES_INFO_12','inputs':{k:{'path':str(p),'sha256':s} for k,(p,s) in P.items()},'metrics':metrics,'scope':'Physical action replay passes the fixed arithmetic/closure gates, but LGMRES info=12 and final scaled residual exceeds rtol=1e-9. This remains an unvalidated saved field, with no PowerSI, board, Green/FMM, or new solve claim.'}
  (out/'independent-review.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':run(R/'astra-l02-l14-l25-combined-block-lgmres-review-01')
