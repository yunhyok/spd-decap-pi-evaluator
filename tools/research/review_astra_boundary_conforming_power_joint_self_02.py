from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-boundary-conforming-power-joint-self-review-02'
def h(p):return sha256(p.read_bytes()).hexdigest()
def load(p):
 with np.load(R/p,allow_pickle=False) as z:return {k:z[k] for k in z.files}
def main():
 oldp=load('outputs/research/astra-conforming-power-joint-01/post-template.npz');newp=load('outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz');old=load('outputs/research/astra-conforming-power-joint-01/joint-template.npz');new=load('outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz')
 zones={}
 for z in np.unique(oldp['cell_zone']):zones[str(z)]=[float(oldp['cell_volume_um3'][oldp['cell_zone']==z].sum()),float(newp['cell_volume_um3'][newp['cell_zone']==z].sum())]
 bridge=[float(old['cell_volume_um3'][old['cell_body']==2].sum()),float(new['cell_volume_um3'][new['cell_body']==2].sum())]
 q={'program':'review_astra_boundary_conforming_power_joint_self_02','version':2,'status':'P1_INCOMPLETE_UNION_COMPARISON','reviewer_sha256':h(Path(__file__)),'recomputed':{'post_zone_volumes_um3_old_new':zones,'bridge_volume_um3_old_new':bridge,'old_new_joint_positive':[bool((old['cell_volume_um3']>0).all()),bool((new['cell_volume_um3']>0).all())]},'p1':'This runtime lacks Shapely, so exact projected triangle unary_union/symmetric-difference and normalized WKB comparisons at z=0/25/55/75 are not computed by this receipt. Cell-zone and bridge volume equality are saved-array checks only. Review01 self-block positivity remains structural readback, not independent quadrature.','scope':'Geometry/local-self saved checks only; no mutual/retarded/FMM/field claim.'};O.mkdir(parents=True,exist_ok=True);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
