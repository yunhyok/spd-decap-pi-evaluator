"""Static saved-input review for the 933-bridge Joule CSR assembly contract."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'outputs/research/astra-power-chain-joule-assembly-static-review-01'
P={'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz':'9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5','outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json':'583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790','outputs/research/astra-adjacent-joint-joule-overlap-01/overlap-metric.npz':'a47b073c22489b1098af2de254784001322840baca7f1200c91d347460b5294c'}
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 for p,x in P.items():assert H(ROOT/p)==x,p
 a=json.loads((ROOT/'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json').read_text()); inst=a['instances'];deg={}
 for e in inst:
  for q in ('left_pin','right_pin'):deg[e[q]]=deg.get(e[q],0)+1
 hist={str(k):sum(v==k for v in deg.values()) for k in sorted(set(deg.values()))};assert len(inst)==933 and len(deg)==978 and hist=={'1':90,'2':888}
 with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as z:B=z['face_flux_basis'];G=z['energy_gram_ohm']
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:xyz=z['vertices_local_um']*1e-6;cell=z['cells'];body=z['cell_body']
 with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:col=z['local_rt0_face_columns'];sign=z['local_rt0_face_signs'];sigma=float(z['conductivity_s_m'][0])
 tet=xyz[cell];V=np.abs(np.linalg.det(tet[:,1:]-tet[:,:1]))/6;c=tet.mean(1);var=np.square(tet-c[:,None]).sum((1,2))/20;flux=sign[:,:,None]*B[col];jc=np.einsum('cim,cid->cmd',flux,c[:,None]-tet)/(3*V[:,None,None]);jr=flux.sum(1)/(3*V[:,None])
 ids=[np.flatnonzero(body==i) for i in range(3)]
 def metric(x,y,owner):return np.einsum('c,cid,cjd->ij',V[owner]/sigma,jc[x],jc[y])+np.einsum('c,ci,cj->ij',V[owner]*var[owner]/sigma,jr[x],jr[y])
 gl,gr,gb=[metric(i,i,i) for i in ids];cross=metric(ids[1],ids[0],ids[0]);joint=gl+gr+gb; two=np.block([[joint,cross],[cross.T,joint]])
 with np.load(ROOT/'outputs/research/astra-adjacent-joint-joule-overlap-01/overlap-metric.npz') as z:saved_cross=z['shared_post_right_left_cross_ohm'];saved_two=z['adjacent_joint_metric_ohm'];eig=z['scaled_metric_eigenvalues'];co=z['random_complex_coefficients']
 rng=np.random.default_rng(933);x=rng.normal(size=4665)+1j*rng.normal(size=4665); # target full-coordinate CSR test recipe
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'STATIC_ASSEMBLY_CONTRACT_ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'pins':P,'graph':{'physical_posts':len(deg),'bridges':len(inst),'components':len(a['components']),'degree_histogram':hist,'degree2_shared_posts':hist['2'],'component_sizes':{str(k):sum(c['count']==k for c in a['components']) for k in sorted(set(c['count'] for c in a['components']))}},'template':{'five_lift_columns':list(B.shape),'joint_gram_relative_to_saved':float(np.linalg.norm(joint-G)/np.linalg.norm(G)),'body_cells':[len(i) for i in ids],'unique_bridge_body_cells':len(ids[2])},'adjacent_cross':{'formula':'C=Sigma_cells(right local edge, left next edge; owner=shared post) [V/sigma jc.jc + V*variance/sigma jr*jr]','cross_relative_to_saved':float(np.linalg.norm(cross-saved_cross)/np.linalg.norm(saved_cross)),'two_metric_relative_to_saved':float(np.linalg.norm(two-saved_two)/np.linalg.norm(saved_two)),'cross_frobenius_relative_to_joint':float(np.linalg.norm(cross)/np.linalg.norm(joint)),'normalized_min_eigenvalue':float(eig.min()),'two_joint_random_energy_ohm':float(np.real(co.ravel().conj()@two@co.ravel()))},'assembly_requirement':{'coordinates':4665,'mapping':'each bridge edge contributes exactly once to its unique bridge body and once each to left/right post body; at every degree-2 post add the off-diagonal right(edge e)-left(edge e+1) 5x5 cross block and conjugate transpose','forbidden':'Do not sum two independent 5x5 joint energies at a shared post: it omits C+C^H; do not assign a post or bridge body to more than one edge.','checks':['CSR Hermitian data symmetry and one random complex x: x^H R x equals sum of unique post/bridge cell energies','every 933 bridge body appears once and every 978 post body once in body-owner ledger','888 degree-2 cross pairs, orientation from bridge left/right pin order, translation consistency','local blocks SPD and global CSR positive on random complex vectors; no duplicate diagonal body contribution']},'random_coordinate_seed':933,'random_coordinate_length':int(x.size),'scope':'Static local-Joule assembly contract only. It does not assemble a 4665 CSR, solve, Green/FMM, or approve a full board/current space.'}
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
