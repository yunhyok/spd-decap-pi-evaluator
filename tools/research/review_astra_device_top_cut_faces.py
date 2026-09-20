"""Independent saved-only audit of Device TOP cut-face measures."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/research/astra-device-top-cut-faces-review-01'
PINS={
 'tools/research/prepare_astra_device_top_cut_faces.py':'78562832383601d781078c1b2f773af49b76e625a97a1e5bfb8904baaf7e615b',
 'outputs/research/astra-device-top-cut-faces-01/result.json':'d0e1856016cf6f0eb98147f8c691614d23d2c535411e706024b3670bbeb5cd5a',
 'outputs/research/astra-device-top-cut-faces-01/cut-faces.npz':'18cfdd8dd1a1bb6b9e1a6f2ae5afdefff139950b5024746fe1cfdc965da8c0d4',
 'outputs/research/astra-device-pad-first-via-geometry-05/geometry.npz':'5976729e9e6dc21266980cdb7c74cd5f33d076eead80828c5299b07e1307954d',
 'outputs/research/astra-device-post-tetra-template-01/mesh.npz':'ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02',
 'outputs/research/astra-device-top-artwork-contacts-01/artwork-contact-ledger.json':'1fee5ba69576be25dc0579181b10945abd1c877cf03e803e3b2ae08d5b96a38e',}
def digest(p:Path)->str:return sha256(p.read_bytes()).hexdigest()
def need(x:bool,m:str)->None:
 if not x:raise AssertionError(m)
def frac(a,b,rising):
 return np.where(rising,(b-a)*(b+a),2*(b-a)-(b-a)*(b+a))
def main()->None:
 actual={k:digest(ROOT/k) for k in PINS};need(actual==PINS,'pins')
 result=json.loads((ROOT/'outputs/research/astra-device-top-cut-faces-01/result.json').read_text());contact=json.loads((ROOT/'outputs/research/astra-device-top-artwork-contacts-01/artwork-contact-ledger.json').read_text())
 with np.load(ROOT/'outputs/research/astra-device-top-cut-faces-01/cut-faces.npz',allow_pickle=False) as z: a={k:z[k] for k in z.files}
 with np.load(ROOT/'outputs/research/astra-device-post-tetra-template-01/mesh.npz',allow_pickle=False) as z: vertices,cells,faces,owners,avec=z['vertices_local_m']*1e6,z['cells'],z['face_vertices'],z['first_owner_cell'],z['face_area_vector_m2']
 n=1956;f=192;need(len(a['pad_ids'])==n and a['contact_area_fraction'].shape==(n,f),'dimensions')
 need(len(a['template_side_face_ids'])==f and len(a['interval_pad_index'])==62232 and (a['contact_area_fraction']>=0).all() and (a['contact_area_fraction']<=1).all(),'interval/fraction domain')
 need(str(a['contact_status'])=='RETAINED_CONDUCTOR_CONTACT_WITHOUT_ASSEMBLED_MATE' and str(a['complement_status'])=='RETAINED_REMAINING_SOURCE_MODEL_BOUNDARY','retained statuses')
 side=a['template_side_face_ids'];edges=a['template_edge_xy_um'];face_edges=a['template_side_face_edge_index'];triuz=a['template_side_triangle_uz'];fractions=a['contact_area_fraction'];
 need(np.array_equal(a['template_side_face_owner_cell'],owners[side]),'owners')
 for face,owner,local in zip(side,a['template_side_face_owner_cell'],a['template_side_owner_local_face'],strict=True): need(set(faces[face])==set(np.delete(cells[owner],local)),'owner local')
 rising=triuz[:,:,0].sum(axis=1)==2; expected=np.zeros_like(fractions)
 lengths=np.linalg.norm(edges[:,1]-edges[:,0],axis=1);by_pad=np.zeros(n)
 for p,e,u0,u1 in zip(a['interval_pad_index'],a['interval_edge_index'],a['interval_u0'],a['interval_u1'],strict=True):
  need(0<=p<n and 0<=e<96 and 0<=u0<u1<=1,'interval')
  ids=np.flatnonzero(face_edges==e);need(len(ids)==2,'two faces per edge');expected[p,ids]+=frac(u0,u1,rising[ids]);by_pad[p]+=(u1-u0)*lengths[e]
 need(np.max(np.abs(expected-fractions))<2e-14,'fraction reconstruction')
 source={r['pin_id']:r for r in contact['pads']};need(len(source)==n,'contact ledger')
 maxlen=max(abs(by_pad[i]-source[str(pin)]['combined_trace_artwork_contact_length_um']) for i,pin in enumerate(a['pad_ids']));need(maxlen<2e-10,'projection lengths')
 areas=np.linalg.norm(avec[side],axis=1);cutarea=float(fractions@areas@np.ones(n))*1e12;sourcearea=sum(r['combined_trace_artwork_contact_length_um']*25 for r in contact['pads']);need(abs(cutarea-sourcearea)<1e-5,'area closure')
 b=a['template_side_b_area_vector_m2'];need(np.array_equal(b,-avec[side]),'B negative outward')
 split=np.max(np.abs(fractions[:,:,None]*b+(1-fractions[:,:,None])*b-b));need(split<1e-24,'B closure')
 receipt={'program':'review_astra_device_top_cut_faces','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':digest(Path(__file__)),'pins':actual,'recomputed':{'links':n,'side_template_faces':f,'merged_intervals':len(a['interval_pad_index']),'positive_face_measures':int((fractions>0).sum()),'partial_face_measures':int(((fractions>0)&(fractions<1)).sum()),'contact_area_um2':cutarea,'source_union_area_um2':sourcearea,'maximum_interval_projection_length_error_um':maxlen,'fraction_reconstruction_error':float(np.max(np.abs(expected-fractions))),'B_split_closure_m2':float(split)},'scope':'Both saved contact and complement face measures remain RETAINED_EXTERNAL. Fractions are constant-owner-current flux measures on clipped triangles, not conforming geometry or a Green/field convergence claim. No row is eliminated; opposing trace/artwork volume mates and current DOFs remain absent.'}
 OUT.mkdir(parents=True,exist_ok=False);target=OUT/'independent-review.json';target.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':receipt['status'],'receipt_sha256':digest(target)}))
if __name__=='__main__':main()
