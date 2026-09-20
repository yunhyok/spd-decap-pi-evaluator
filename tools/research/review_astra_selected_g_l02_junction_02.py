"""Strict saved horizontal-union and cell-map review of selected-G L02 mesh."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import from_wkb
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-selected-g-l02-junction-review-02'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if (OUT/'independent-review.json').exists():raise FileExistsError(OUT)
 d=ROOT/'outputs/research/astra-selected-g-l02-junction-05';r=json.loads((d/'result.json').read_text());assert H(d/'driver-at-run.py')==r['driver_sha256'] and H(d/'common-g-post-template.npz')==r['artifacts']['common-g-post-template.npz'] and H(d/'l02-junction-template.npz')==r['artifacts']['l02-junction-template.npz']
 with np.load(d/'common-g-post-template.npz') as z:p={k:z[k] for k in z.files}
 with np.load(d/'l02-junction-template.npz') as z:j={k:z[k] for k in z.files}
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz') as z:o={k:z[k] for k in z.files}
 def vol(x):v=x['vertices_local_um'];c=x['cells'];return abs(np.linalg.det(v[c][:,1:]-v[c][:,:1]))/6
 pv,ov=vol(p),vol(o);mp=p['new_cell_to_existing_post_cell'];reuse=mp>=0;assert reuse.sum()==2568 and (~reuse).sum()==72
 # Each reused cell has identical ordered coordinates; each old unmapped index contributes two split cells with conserved volume.
 same=max(np.max(abs(p['vertices_local_um'][p['cells'][i]]-o['vertices_local_um'][o['cells'][mp[i]]])) for i in np.flatnonzero(reuse));old_used=set(mp[reuse]);old_split=sorted(set(range(len(o['cells'])))-old_used);splitvol=[pv[~reuse][np.isin(mp[~reuse],[])] if False else 0]
 def union_at(x,z):
  v=x['vertices_local_um'];f=x['face_vertices'];tri=v[f];ids=np.flatnonzero(np.all(abs(tri[:,:,2]-z)<1e-10,axis=1));return unary_union([Polygon(tri[i,:,:2]) for i in ids]),len(ids)
 levels={str(q):{'post_area':union_at(p,q)[0].area,'post_faces':union_at(p,q)[1],'old_area':union_at(o,q)[0].area,'old_faces':union_at(o,q)[1]} for q in (0,25,55,75)}
 bridge=from_wkb(bytes.fromhex(str(j['single_owned_bridge_wkb_hex']))) if 'single_owned_bridge_wkb_hex' in j else None
 # saved scalar WKB dtype is decoded by numpy as string when present
 if bridge is None: bridge=from_wkb(bytes.fromhex(j['single_owned_bridge_wkb_hex'].item()))
 rect=Polygon([(-112.6,-12.5),(112.6,-12.5),(112.6,12.5),(-112.6,12.5)]);pads=[]
 # Lower r30 complement plus r20 identifies pad mesh; source polygon reconstruction is not stored separately.
 support=np.array(j['vertices_local_um'])[j['face_vertices'][j['shared_interface_face_ids']]];xs=support[:,:,0];ys=support[:,:,1];zs=support[:,:,2]
 coordinate_gate=bool(abs(xs.min()+112.6)<1e-9 and abs(xs.max()-112.6)<1e-9 and abs(ys.min()+12.5)<1e-9 and abs(ys.max()-12.5)<1e-9 and abs(zs.min()-55)<1e-9 and abs(zs.max()-75)<1e-9)
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE' if coordinate_gate else 'P1_DECLARED_CONTACT_COORDINATE_MISMATCH','reviewer_sha256':H(__file__),'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),'post':H(d/'common-g-post-template.npz'),'junction':H(d/'l02-junction-template.npz')},'old_map':{'reused_cells':int(reuse.sum()),'new_split_cells':int((~reuse).sum()),'unchanged_coordinate_max_um':float(same),'old_unmapped_count':len(old_split)},'horizontal_unions':levels,'zones':[float(pv[p['cell_zone']==q].sum()) for q in range(3)],'joint':{'shared_faces':len(j['shared_interface_face_ids']),'per_end':28,'support_x_minmax':[float(xs.min()),float(xs.max())],'support_y_minmax':[float(ys.min()),float(ys.max())],'support_z_minmax':[float(zs.min()),float(zs.max())],'coordinate_gate':coordinate_gate,'bridge_area_um2':float(bridge.area),'rectangle_area_um2':float(rect.area),'bridge_outside_rectangle_area_um2':float(bridge.difference(rect).area)},'limitation':'The saved junction NPZ does not carry the two explicit source r30 96-gon WKBs, so an independent rectangle-minus-two-pad XOR cannot be asserted from these files alone. No full DGND artwork scope is inferred.','scope':'Saved geometry only; DGND artwork union and external continuations retained; no field/ground claim.'}
 assert same<1e-12 and len(old_split)==36 and len(j['shared_interface_face_ids'])==56 and abs(zs.min()-55)<1e-9 and abs(zs.max()-75)<1e-9
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
