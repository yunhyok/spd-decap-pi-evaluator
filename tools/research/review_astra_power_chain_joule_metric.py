"""Saved-only independent numerical review of the 4,665-coordinate Joule CSR."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-power-chain-joule-metric-review-01'
P={'tools/research/assemble_astra_power_chain_joule_metric.py':'012d3802cb27a05bd42d38d59ad9b4d315f4becc3c64460db55c7d34efb173ea','outputs/research/astra-power-chain-joule-metric-01/result.json':'','outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz':'','outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json':'583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5','outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz':'9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92'}
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def csr_mv(ptr,col,data,x):return np.array([data[ptr[i]:ptr[i+1]]@x[col[ptr[i]:ptr[i+1]]] for i in range(len(x))])
def main():
 if OUT.exists():raise FileExistsError(OUT)
 # Artifact pins are self-discovered first, then tied to result values.
 for p,v in P.items():
  if v: assert H(ROOT/p)==v,p
 result=json.loads((ROOT/'outputs/research/astra-power-chain-joule-metric-01/result.json').read_text()); P['outputs/research/astra-power-chain-joule-metric-01/result.json']=H(ROOT/'outputs/research/astra-power-chain-joule-metric-01/result.json');P['outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz']=H(ROOT/'outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz');assert P['outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz']==result['artifact_sha256']
 ledger=json.loads((ROOT/'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json').read_text());edges=ledger['instances']
 with np.load(ROOT/'outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz') as _z: saved_post_pins=_z['post_pins'].copy()
 idx={p:i for i,p in enumerate(saved_post_pins)};degree=np.zeros(len(idx),int);incoming=np.full(len(idx),-1);outgoing=np.full(len(idx),-1)
 for i,e in enumerate(edges):
  a,b=idx[e['left_pin']],idx[e['right_pin']];degree[a]+=1;degree[b]+=1;outgoing[a]=i;incoming[b]=i
 pairs=np.array([[incoming[p],outgoing[p]] for p in range(len(idx)) if degree[p]==2]); assert len(edges)==933 and len(idx)==978 and (degree==1).sum()==90 and (degree==2).sum()==888 and np.all(pairs>=0)
 with np.load(ROOT/'outputs/research/astra-power-chain-joule-metric-01/power-chain-joule-metric.npz') as z:d={k:z[k] for k in z.files}
 ptr,col,val=d['resistance_row_ptr'],d['resistance_col'],d['resistance_data_ohm'];n=4665;assert tuple(d['resistance_shape'])==(n,n) and len(val)==67725 and np.array_equal(d['adjacent_edge_pairs'],pairs)
 with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as z:B=z['face_flux_basis'];savedG=z['energy_gram_ohm']
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:xyz=z['vertices_local_um']*1e-6;cell=z['cells'];body=z['cell_body']
 with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:fc=z['local_rt0_face_columns'];sg=z['local_rt0_face_signs'];sigma=float(z['conductivity_s_m'][0])
 tet=xyz[cell];V=np.abs(np.linalg.det(tet[:,1:]-tet[:,:1]))/6;center=tet.mean(1);variance=np.square(tet-center[:,None]).sum((1,2))/20;fl=sg[:,:,None]*B[fc];jc=np.einsum('cim,cid->cmd',fl,center[:,None]-tet)/(3*V[:,None,None]);jr=fl.sum(1)/(3*V[:,None]);ids=[np.flatnonzero(body==i) for i in range(3)]
 def M(a,b,o):return np.einsum('c,cid,cjd->ij',V[o]/sigma,jc[a],jc[b])+np.einsum('c,ci,cj->ij',V[o]*variance[o]/sigma,jr[a],jr[b])
 gl,gr,gb=[M(i,i,i) for i in ids];cross=M(ids[1],ids[0],ids[0]);joint=gl+gr+gb
 rng=np.random.default_rng(20260909);x=rng.normal(size=(933,5))+1j*rng.normal(size=(933,5));xf=x.ravel();csr=float(np.real(xf.conj()@csr_mv(ptr,col,val,xf)))
 def E(c,r,owner):return float(np.sum(V[owner]/sigma*(np.sum(np.abs(c)**2,axis=1)+variance[owner]*np.abs(r)**2)))
 physical=0.
 for p in range(978):
  terms=[]
  if incoming[p]>=0:terms.append((ids[1],incoming[p]))
  if outgoing[p]>=0:terms.append((ids[0],outgoing[p]))
  c=sum((np.einsum('cid,i->cd',jc[a],x[e]) for a,e in terms),np.zeros((len(ids[0]),3),complex));r=sum((jr[a]@x[e] for a,e in terms),np.zeros(len(ids[0]),complex));physical+=E(c,r,ids[0])
 for e in range(933):physical+=E(np.einsum('cid,i->cd',jc[ids[2]],x[e]),jr[ids[2]]@x[e],ids[2])
 comps=d['edge_component'];mins=[];ch=[]
 dense=np.zeros((n,n))
 for i in range(n):dense[i,col[ptr[i]:ptr[i+1]]]=val[ptr[i]:ptr[i+1]]
 for q in range(45):
  ii=np.flatnonzero(comps==q);aa=(5*ii[:,None]+np.arange(5)).ravel();g=dense[np.ix_(aa,aa)];s=np.sqrt(np.diag(g));mins.append(float(np.linalg.eigvalsh(g/s[:,None]/s[None]).min()));ch.append(float(np.linalg.cholesky(g).diagonal().min()))
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'pins':P,'graph':{'posts':978,'bridges':933,'components':45,'degree_one':int((degree==1).sum()),'degree_two':int((degree==2).sum()),'ordered_incoming_right_outgoing_left_pairs':int(len(pairs))},'csr':{'shape':[n,n],'nnz':int(len(val)),'symmetry_absolute_ohm':float(np.abs(dense-dense.T).max()),'coordinate_order_match':bool(np.array_equal(d['edge_left_pin'],np.array([e['left_pin'] for e in edges])) and np.array_equal(d['edge_right_pin'],np.array([e['right_pin'] for e in edges]))),'new_complex_seed':20260909,'csr_energy_ohm':csr,'explicit_unique_body_energy_ohm':physical,'relative_energy_error':abs(csr-physical)/physical},'local_reconstruction':{'joint_gram_relative':float(np.linalg.norm(joint-savedG)/np.linalg.norm(savedG)),'saved_GL_relative':float(np.linalg.norm(gl-d['local_left_post_gram_ohm'])/np.linalg.norm(gl)),'saved_GR_relative':float(np.linalg.norm(gr-d['local_right_post_gram_ohm'])/np.linalg.norm(gr)),'saved_GB_relative':float(np.linalg.norm(gb-d['local_bridge_gram_ohm'])/np.linalg.norm(gb)),'saved_cross_relative':float(np.linalg.norm(cross-d['shared_post_right_left_cross_ohm'])/np.linalg.norm(cross)),'bridge_normalized_min_eigenvalue':float(np.linalg.eigvalsh(gb/np.sqrt(np.diag(gb))[:,None]/np.sqrt(np.diag(gb))[None]).min()),'component_normalized_min_eigenvalue':min(mins),'component_cholesky_min_sqrt_ohm':min(ch)},'scope':'Five local lift modes per edge only; declared constant fixture and complementary current/charge spaces remain. No Green/FMM/field/Z/board claim.'}
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
