import argparse,hashlib,json,os
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];K=R/'outputs/research/astra-constant-current-box-kernels-01/kernels.npz';KR=K.parent/'result.json';F=R/'outputs/research/astra-constant-current-384-field-01';D=F/'driver-at-run.py';P={'kernel':'abd6a03542cf9c315690ed9751b5134f41c293eef6201e75cd59b1acbcf024b8','receipt':'b29a26512a694d88b705cc9eaab361ebe2e097724d8333ce1cd6163230533308','result':'c0d0df32b7074f72b34833a3777751b732a7cb7cef7e7e4059bce073d2e262e7','fields':'9fc218ca9fab49c8d14599b3d226aeb82b30ac1e7db4346cd1fa1acfb37b4c84','driver':'2cd3eeff4071297fede205c91bccac31e25768b6eecb97fa7f4e2ca2aa610a95'}
def s(p):
 h=hashlib.sha256();h.update(p.read_bytes());return h.hexdigest()
def rel(a,b):return float(np.linalg.norm(a-b)/max(np.linalg.norm(b),np.finfo(float).tiny))
def main(o):
 assert s(K)==P['kernel'] and s(KR)==P['receipt'] and s(F/'result.json')==P['result'] and s(F/'fields.npz')==P['fields'] and s(D)==P['driver'];r=json.loads((F/'result.json').read_text());assert r['kernel_sha256']==P['kernel'] and r['kernel_receipt_sha256']==P['receipt']
 with np.load(K,allow_pickle=False) as d:
  tet=d['tetrahedra_m'];tri=d['triangles_m'];bd=d['boundary_face_indices'];kv=d['static_volume_per_m'];ks=d['static_boundary_per_m'];tv=d['volume_tail_per_m'];ts=d['boundary_tail_per_m'];assert tet.shape==(384,4,3) and tri.shape==(864,3,3) and bd.shape==(192,) and kv.shape==(384,384) and ks.shape==(192,192) and tv.shape==(6,384,384) and ts.shape==(6,192,192)
  sym=max(rel(x,x.T) for x in (kv,ks,*tv,*ts));mine=min(np.linalg.eigvalsh(kv).min(),np.linalg.eigvalsh(ks).min())
 with np.load(F/'fields.npz',allow_pickle=False) as d:
  T=d['current_transform'];q=d['boundary_divergence_range'];h=d['cell_integrated_current_map'];vals=[]
  for i,c in enumerate(r['cases']):
   z=d[f'case_{i:02d}_scaled_solution'];rhs=d[f'case_{i:02d}_incident_rhs'];cur=d[f'case_{i:02d}_current'];rho=d[f'case_{i:02d}_boundary_charge'];rr=d[f'case_{i:02d}_reaction'];om=2*np.pi*c['frequency_hz'];m=z.copy();m[289:]*=om;vals.append((rel(cur,T@m),rel(rho,1j*q@z[289:]),rel(rr,rhs.T@m)))
  chk={'T':list(T.shape),'q':list(q.shape),'h':list(h.shape),'max_j':max(x[0] for x in vals),'max_rho':max(x[1] for x in vals),'max_reaction':max(x[2] for x in vals),'kernel_symmetry_max':sym,'kernel_min_eigenvalue':float(mine),'max_power':max(x['power_relative'] for x in r['cases']),'max_radiation':max(x['radiation_relative'] for x in r['cases']),'field_change_range':[min(x['field_l2_change_from_48_tetra'] for x in r['cases']),max(x['field_l2_change_from_48_tetra'] for x in r['cases'])],'absorption_change_range':[min(x['absorption_relative_change'] for x in r['cases']),max(x['absorption_relative_change'] for x in r['cases'])]};assert chk['T']==[1536,480] and chk['q']==[192,191] and chk['h']==[384,3,480] and max(chk[x] for x in ('max_j','max_rho','max_reaction'))<1e-10 and sym<1e-13 and mine>0
 o.mkdir(parents=True,exist_ok=False);p=o/'independent-review.json';t=o/'independent-review.json.tmp';x={'status':'ACCEPT_CONSTANT_CURRENT_384_SAVED_CONTROL','pins':P,'checks':chk,'scope':'Finite homogeneous box only; 48-to-384 change is not convergence or board/skin accuracy.'};t.write_text(json.dumps(x,indent=2)+'\n');os.replace(t,p);print(json.dumps({'status':x['status'],'sha256':s(p)}))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--output',type=Path,required=True);main(a.parse_args().output.resolve())
