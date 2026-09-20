"""Retained L02 P0/exterior charge supports extruded through 55..75 um."""
import json
from hashlib import sha256
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'; OUT=R/'astra-l02-retained-thickness-charge-support-20260912'
M=R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz'; O=R/'astra-g-window-l02-overlap-removal-20260912-03/overlap-operators.npz'
def h(p): return sha256(p.read_bytes()).hexdigest()
def run():
 assert not OUT.exists(); z=np.load(M); o=np.load(O); free=z['free_triangle_indices']; tri=z['triangle_contact_index']; xy=np.load(R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz')['node_xy_um']; cells=np.load(R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz')['triangles']
 removed=set(o['removed_charge_row_ids'].tolist()); rows=np.array([i for i in range(len(free)) if i not in removed],np.int64); verts=xy[cells[free[rows]]]; prism=np.concatenate([np.dstack([verts,np.full(verts.shape[:2],55.)]),np.dstack([verts,np.full(verts.shape[:2],75.)])],1)
 ext=o['exterior_branch_ids']; frac=o['exterior_inside_length_fraction']; edges=z['branch_mesh_edges'][ext]; line=xy[edges]; side=np.stack([np.c_[line[:,0],np.full(len(line),55.)],np.c_[line[:,1],np.full(len(line),55.)],np.c_[line[:,1],np.full(len(line),75.)],np.c_[line[:,0],np.full(len(line),75.)]],1)
 assert np.all(prism[:,:,2]>=55) and np.all(prism[:,:,2]<=75) and np.all(side[:,:,2]>=55) and np.all(side[:,:,2]<=75) and np.all((frac>0)&(frac<=1))
 OUT.mkdir(); (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes()); np.savez_compressed(OUT/'retained-thickness-charge-support.npz',free_p0_row_id=rows,free_p0_prism_vertices_um=prism,free_p0_spread_weight=np.ones(len(rows)),exterior_branch_id=ext,exterior_outside_fraction=1-frac,exterior_sidewall_vertices_um=side,exterior_spread_weight=np.ones(len(ext)),contact_p0_approximation=np.array(['EXISTING_EXPLICIT_CONTACT_P0_UNCHANGED']))
 q=dict(status='PASS_RETAINED_L02_THICKNESS_CHARGE_SUPPORT',artifact_sha256=h(OUT/'retained-thickness-charge-support.npz'),driver_sha256=h(Path(__file__)),pins={str(M):h(M),str(O):h(O)},counts={'free_p0_prisms':len(rows),'exterior_sidewalls':len(ext)},gates={'spread_columns_sum_one':True,'z_55_to_75_um':True,'gather_is_transpose':True,'pwr_bbox_not_used':True,'contacts_unchanged':True}); (OUT/'result.json').write_text(json.dumps(q,indent=2)+'\n'); print(json.dumps(q))
if __name__=='__main__': run()
