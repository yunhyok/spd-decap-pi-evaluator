from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-conforming-power-joint-sparse-current-review-02'
P={'outputs/research/astra-conforming-power-joint-sparse-current-02/driver-at-run.py':'dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e','outputs/research/astra-conforming-power-joint-sparse-current-02/result.json':'9a4974ecea44fefec7c2bddae4304fed016400da979ff8225fcdf392b834a677','outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz':'01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins');r=json.loads((R/'outputs/research/astra-conforming-power-joint-sparse-current-02/result.json').read_text())
 with np.load(R/'outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 f=int(x['face_count'][0]);n(f==18994 and int(x['cell_count'][0])==8064,'dimensions');
 for pre,rows in [('local_rt0_lift',32256),('volume_b',8064),('distributional_b',13796),('mass',f)]:n(x[pre+'_row_ptr'][0]==0 and x[pre+'_row_ptr'][-1]==len(x[pre+'_col']) and len(x[pre+'_row_ptr'])==rows+1,'csr '+pre)
 n(np.allclose(x['resistance_data_ohm'],x['mass_data_inv_m']/x['conductivity_s_m'][0]),'ohmic');n(len(x['shared_interface_face_ids'])==64 and x['shared_interface_owner_cells'].shape==(64,2),'shared');n(not set(x['shared_interface_face_ids'])&set(x['boundary_face_ids']),'shared interior');n(dict(zip(*np.unique(x['boundary_charge_state'],return_counts=True)))=={'DECLARED_PAD_TOP_ELECTRODE':1512,'RETAINED_BRIDGE_TOP_EXTERNAL':32,'RETAINED_LOWER_INTERFACE':952,'RETAINED_OTHER_EXTERNAL':3236},'boundary')
 q={'program':'review_astra_conforming_power_joint_sparse_current','version':2,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'cells':8064,'faces':f,'shared_internal':64,'boundary_states':{'pad_top':1512,'bridge_top':32,'lower':952,'other':3236},'csr_mass_nnz':len(x['mass_col']),'ohmic_scaling_exact':True},'scope':'Sparse RT0 copper mass/topology only; no Green, field, dense rank, replicated P933 chain or board claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
