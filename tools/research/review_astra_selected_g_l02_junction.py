"""Direct saved mesh audit of the canonical selected-G L02 junction."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-selected-g-l02-junction-review-01'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 d=ROOT/'outputs/research/astra-selected-g-l02-junction-05';r=json.loads((d/'result.json').read_text());assert H(d/'driver-at-run.py')==r['driver_sha256']
 for n,h in r['artifacts'].items():assert H(d/n)==h
 with np.load(d/'common-g-post-template.npz') as z:p={k:z[k] for k in z.files}
 with np.load(d/'l02-junction-template.npz') as z:j={k:z[k] for k in z.files}
 def audit(x):
  v=x['vertices_local_um'];c=x['cells'];vol=abs(np.linalg.det(v[c][:,1:]-v[c][:,:1]))/6;fv=x['face_vertices'];own=np.zeros(len(fv),int)
  for ci,cell in enumerate(c):
   for q in range(4):own[np.where((fv==np.sort(np.delete(cell,q))).all(1))[0][0]]+=1
  return vol,own
 pv,po=audit(p);jv,jo=audit(j);mp=p['new_cell_to_existing_post_cell'];reused=(mp>=0).sum();split=(mp<0).sum()
 # Direct z-plane/contact categories.
 vv=p['vertices_local_um'];ff=p['face_vertices'];top=np.all(abs(vv[ff][:,:,2])<1e-12,axis=1);low=np.all(abs(vv[ff][:,:,2]-75)<1e-12,axis=1)
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),**r['artifacts']},'post':{'cells':len(p['cells']),'positive_volume_min_um3':float(pv.min()),'zone_volumes_um3':[float(pv[p['cell_zone']==q].sum()) for q in range(3)],'manifold_internal_all_two':bool(np.all(po[p['internal_face_ids']]==2)),'boundary_all_one':bool(np.all(po[p['boundary_face_ids']]==1)),'top_r50_partition_faces':len(p['top_patch_face_ids']),'lower_r20_faces':len(p['lower_r20_contact_face_ids']),'lower_r30_remainder_faces':len(p['lower_r30_complement_face_ids']),'top_plane_faces':int(top.sum()),'lower_plane_faces':int(low.sum()),'mapping_reused':int(reused),'mapping_new_split':int(split)},'joint':{'cells':len(j['cells']),'positive_volume_min_um3':float(jv.min()),'manifold_internal_all_two':bool(np.all(jo[j['internal_face_ids']]==2)),'boundary_all_one':bool(np.all(jo[j['boundary_face_ids']]==1)),'bridge_cells':len(j['bridge_cells']),'shared_post_bridge_faces':len(j['shared_interface_face_ids']),'bridge_planar_triangles':len(j['bridge_planar_triangles'])},'declared_contact_geometry':{'top_per_end':32,'l02_per_end':28,'trace_width_um':25.,'trace_length_um':180.,'pitch_um':225.2,'source_contact_length_per_end_um':r['source_contact_length_per_end_um']},'scope':'Saved geometry only. DGND artwork union ownership and external continuations remain unresolved/retained; no field or ground claim.'}
 assert out['post']['manifold_internal_all_two'] and out['post']['boundary_all_one'] and out['joint']['manifold_internal_all_two'] and out['joint']['boundary_all_one'] and pv.min()>0 and jv.min()>0 and reused==2568 and split==72
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
