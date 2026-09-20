"""Read-only saved-field receipt for refined unrestricted and homogeneous controls."""
import argparse, hashlib, json, os
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
DRIVER=ROOT/'tools/research/diagnose_astra_refined_3d_field.py'
KERNEL=ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz'
KRECEIPT=KERNEL.parent/'result.json'
COARSE=ROOT/'outputs/research/astra-3d-current-charge-field-02/fields.npz'
TARGETS={
 'unrestricted':('astra-refined-3d-field-01','6dc817b944c97a48be420531036d31da517ad53662d1ffb186b610f9dd98d856','136fdbd4b2959a801d379a199c633d8c5e998f17205665ee3cc9228984c68e83'),
 'homogeneous':('astra-homogeneous-refined-3d-field-01','f0bb57c49dd51d5f26d1c1ca5701bc937a95055dbfbeefd70d911fc54fb28388','b01a32fbdb82af9285f1e9205d4f8d5dced506ec67fb22227c731ee7945a6e9a')}
PINS={'driver':'c5a915cf8ba3deee830e6bf4b989eecc3408b1251e8fd46d9a49db5a7ac5225a','kernel':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02','receipt':'88fdb9c9c11c3f836fad94f022638bdd841ae7f20c0d3e0601161f0ccbee1745','coarse':'3625526b832b76fcfa32727d33bcb0d52a179e56728801ee4ace26d68f26ab2d'}
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1048576),b''): h.update(x)
 return h.hexdigest()
def rel(a,b): return float(np.linalg.norm(a-b)/max(np.linalg.norm(b),np.finfo(float).tiny))
def main(out):
 assert sha(DRIVER)==PINS['driver'] and sha(KERNEL)==PINS['kernel'] and sha(KRECEIPT)==PINS['receipt'] and sha(COARSE)==PINS['coarse']
 kr=json.loads(KRECEIPT.read_text()); assert kr['kernels_sha256']==PINS['kernel']
 with np.load(KERNEL,allow_pickle=False) as d: B=d['distributional_divergence']
 review={}
 for name,(folder,rh,fh) in TARGETS.items():
  base=ROOT/'outputs/research'/folder; rp=base/'result.json'; fp=base/'fields.npz'
  assert sha(rp)==rh and sha(fp)==fh
  r=json.loads(rp.read_text()); assert r['script_sha256']==PINS['driver'] and r['kernel_sha256']==PINS['kernel'] and r['kernel_receipt_sha256']==PINS['receipt'] and r['coarse_fields_sha256']==PINS['coarse']
  with np.load(fp,allow_pickle=False) as d:
   loop=d['loop_basis']; ran=d['range_basis']; T=np.column_stack((loop,ran)); stats=[]
   for i,c in enumerate(r['cases']):
    s=d[f'case_{i:02d}_scaled_solution']; rhs=d[f'case_{i:02d}_incident_rhs']; cur=d[f'case_{i:02d}_current']; q=d[f'case_{i:02d}_charge']; react=d[f'case_{i:02d}_reaction']; om=2*np.pi*c['frequency_hz']; modal=s.copy(); modal[len(loop.T):]*=om
    stats.append({'j_Tmodal_relative':rel(cur,T@modal),'q_iBDs_relative':rel(q,1j*B@ran@s[len(loop.T):]),'continuity_relative':float(np.max(abs(B@cur+1j*om*q)/np.maximum(abs(B)@abs(cur)+om*abs(q),np.finfo(float).tiny))),'reaction_rhsTmodal_relative':rel(react,rhs.T@modal),'reaction_reciprocity':rel(react,react.T),'power_reported':c['power_relative_error']})
   arrays={'loop_shape':list(loop.shape),'range_shape':list(ran.shape),'transform_shape':list(T.shape),'max_j':max(x['j_Tmodal_relative'] for x in stats),'max_q':max(x['q_iBDs_relative'] for x in stats),'max_continuity':max(x['continuity_relative'] for x in stats),'max_reaction':max(x['reaction_rhsTmodal_relative'] for x in stats),'max_reciprocity':max(x['reaction_reciprocity'] for x in stats),'max_power_reported':max(x['power_reported'] for x in stats),'max_field_change':max(c['field_l2_change_from_six_tetra'] for c in r['cases']),'min_field_change':min(c['field_l2_change_from_six_tetra'] for c in r['cases']),'max_absorption_change':max(c['absorption_relative_change'] for c in r['cases']),'min_absorption_change':min(c['absorption_relative_change'] for c in r['cases'])}
   if name=='homogeneous':
    interior=np.flatnonzero(np.count_nonzero(B,axis=1)!=1); arrays['max_homogeneous_interior_rows']=float(max(np.max(abs(B[interior]@d[f'case_{i:02d}_current'])) for i in range(6))); assert arrays['transform_shape']==[192,72] and arrays['range_shape']==[192,47]
   review[name]=arrays
   assert arrays['max_j']<1e-12 and arrays['max_q']<1e-12 and arrays['max_continuity']<1e-12 and arrays['max_reaction']<1e-12 and arrays['max_reciprocity']<1e-10 and arrays['max_power_reported']<1e-5
 out.mkdir(parents=True,exist_ok=False); p=out/'independent-review.json'; t=out/'independent-review.json.tmp'
 value={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_REFINED_3D_SAVED_FIELDS','pins':{'driver':sha(DRIVER),'kernel':sha(KERNEL),'kernel_receipt':sha(KRECEIPT),'coarse':sha(COARSE)},'checks':review,'finding':'Both two-level comparisons remain nonconverged: field L2 change 86.24–90.97% and absorption change 74.38–92.35%.','scope':'Saved finite-box controls only; no solve replay or physical/board accuracy claim.'}
 t.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n'); os.replace(t,p); print(json.dumps({'status':value['status'],'sha256':sha(p)}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);main(p.parse_args().output.resolve())
