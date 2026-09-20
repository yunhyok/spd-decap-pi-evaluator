"""Vertical-frame saved geometry proof for selected-G L02 junction."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import from_wkb
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-selected-g-l02-junction-review-03'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 d=ROOT/'outputs/research/astra-selected-g-l02-junction-05';r=json.loads((d/'result.json').read_text());assert H(d/'driver-at-run.py')==r['driver_sha256']
 with np.load(d/'common-g-post-template.npz') as z:p={k:z[k] for k in z.files}
 with np.load(d/'l02-junction-template.npz') as z:j={k:z[k] for k in z.files}
 v=p['vertices_local_um'];f=p['face_vertices'];tri=v[f];low=np.flatnonzero(np.all(abs(tri[:,:,2]-75)<1e-10,axis=1));pad=unary_union([Polygon(tri[i,:,:2]) for i in low]);pad1=__import__('shapely').affinity.translate(pad,yoff=225.2)
 raw=j['source_trace_rectangle_wkb_hex'].item();trace=from_wkb(bytes.fromhex(str(raw)));bridge=from_wkb(bytes.fromhex(str(j['single_owned_bridge_wkb_hex'].item())));expect=trace.difference(pad.union(pad1));xor=bridge.symmetric_difference(expect).area
 sv=j['vertices_local_um'][j['face_vertices'][j['shared_interface_face_ids']]];x,y,z=sv[:,:,0],sv[:,:,1],sv[:,:,2];mp=p['new_cell_to_existing_post_cell'];out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),'post':H(d/'common-g-post-template.npz'),'junction':H(d/'l02-junction-template.npz')},'vertical_frame':{'trace_bounds':list(trace.bounds),'pad0_area':pad.area,'pad1_area':pad1.area,'bridge_area':bridge.area,'xor_area':xor,'support_x':[float(x.min()),float(x.max())],'support_y':[float(y.min()),float(y.max())],'support_z':[float(z.min()),float(z.max())],'shared_faces':len(j['shared_interface_face_ids']),'per_end':28},'cell_map':{'identical':int((mp>=0).sum()),'new_split':int((mp<0).sum()),'old_replaced':36},'scope':'Vertical L02 trace geometry only; DGND artwork ownership and external continuations remain retained, no field/ground claim.'}
 assert xor<1e-8 and len(j['shared_interface_face_ids'])==56 and abs(x.min()+12.5)<1e-9 and abs(x.max()-12.5)<1e-9 and abs(z.min()-55)<1e-9 and abs(z.max()-75)<1e-9 and (mp>=0).sum()==2568 and (mp<0).sum()==72
 OUT.mkdir(parents=True,exist_ok=False);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
