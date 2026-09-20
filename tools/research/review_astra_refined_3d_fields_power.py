"""Read-only power addendum for frozen refined field controls."""
import argparse, hashlib, json, os
from pathlib import Path
import numpy as np
import qualify_astra_joint_tm_boundary as source

ROOT=Path(__file__).resolve().parents[2]
DRIVER=ROOT/'tools/research/diagnose_astra_refined_3d_field.py'; KERNEL=ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz'
SOURCE=ROOT/'tools/research/qualify_astra_joint_tm_boundary.py'
PINS={'driver':'c5a915cf8ba3deee830e6bf4b989eecc3408b1251e8fd46d9a49db5a7ac5225a','kernel':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02','source':'10032e17838f0f6f0105051424679c29de3ef83acc63cdc9d8075523368ed247'}
TARGETS={'unrestricted':('astra-refined-3d-field-01','6dc817b944c97a48be420531036d31da517ad53662d1ffb186b610f9dd98d856','136fdbd4b2959a801d379a199c633d8c5e998f17205665ee3cc9228984c68e83'),'homogeneous':('astra-homogeneous-refined-3d-field-01','f0bb57c49dd51d5f26d1c1ca5701bc937a95055dbfbeefd70d911fc54fb28388','b01a32fbdb82af9285f1e9205d4f8d5dced506ec67fb22227c731ee7945a6e9a')}
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def main(out):
 assert all(sha({'driver':DRIVER,'kernel':KERNEL,'source':SOURCE}[k])==v for k,v in PINS.items())
 with np.load(KERNEL,allow_pickle=False) as d: mass=d['geometric_mass']
 rows,properties=source.source_inputs(); drives=np.array([[1,0,1],[0,1,1j]],complex); checks={}
 for name,(folder,rh,fh) in TARGETS.items():
  base=ROOT/'outputs/research'/folder; rp=base/'result.json'; fp=base/'fields.npz'; assert sha(rp)==rh and sha(fp)==fh
  r=json.loads(rp.read_text()); assert r['script_sha256']==PINS['driver'] and r['kernel_sha256']==PINS['kernel']
  vals=[]
  with np.load(fp,allow_pickle=False) as d:
   split=d['loop_basis'].shape[1]
   for i,c in enumerate(r['cases']):
    om=2*np.pi*c['frequency_hz']; s=d[f'case_{i:02d}_scaled_solution']; modal=s.copy(); modal[split:]*=om; rhs=d[f'case_{i:02d}_incident_rhs']; cur=d[f'case_{i:02d}_current']@drives
    _,eps,sigma,gamma=source.materials(rows,properties,c['frequency_hz']); kappa=gamma[0]-1j*om*source.EPS0
    ext=np.real(np.sum(np.conj(modal@drives)*(rhs@drives),axis=0)); absorp=(1/kappa).real*np.diag(cur.conj().T@mass@cur).real
    re=np.asarray(c['extinction_w']); ra=np.asarray(c['absorption_w']); rr=np.asarray(c['radiation_w']); scale=np.abs(re)+np.abs(ra)+np.abs(rr)
    vals.append((float(np.max(abs(ext-re))),float(np.max(abs(absorp-ra))),float(np.max(abs(ext-absorp-rr)/scale))))
  checks[name]={'max_extinction_absolute':max(x[0] for x in vals),'max_absorption_absolute':max(x[1] for x in vals),'max_power_closure_using_reported_radiation_relative':max(x[2] for x in vals),'radiation':'reported receipt value only; not independently rebuilt'}
  assert checks[name]['max_extinction_absolute']<1e-28 and checks[name]['max_absorption_absolute']<1e-28 and checks[name]['max_power_closure_using_reported_radiation_relative']<1e-5
 out.mkdir(parents=True,exist_ok=False); p=out/'independent-review.json'; t=out/'independent-review.json.tmp'; x={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_REFINED_3D_SAVED_POWER','pins':PINS,'checks':checks,'scope':'Saved power addendum only; radiation is read from frozen receipts and not independently rebuilt.'};t.write_text(json.dumps(x,indent=2)+'\n');os.replace(t,p);print(json.dumps({'status':x['status'],'sha256':sha(p)}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);main(p.parse_args().output.resolve())
