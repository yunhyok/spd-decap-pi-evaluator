"""Read-only receipt for the frozen 48-tetra constant-current reproduction."""
import argparse,hashlib,json,os
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]; DRIVER=ROOT/'tools/research/diagnose_astra_constant_current_3d_field.py'; BASE=ROOT/'outputs/research/astra-constant-current-48-field-01'; KERNEL=ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz'
P={'driver':'2cd3eeff4071297fede205c91bccac31e25768b6eecb97fa7f4e2ca2aa610a95','result':'7e70781d89176c6e0043ae7a0d3edb1e3cbc84b3ebc782e8b08cebdaac9d4ec0','fields':'04af5c36e1d158ca951ff199ab235a2358f2aaeed6518988ede5296ce12e3a72','kernel':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02'}
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def rel(a,b):return float(np.linalg.norm(a-b)/max(np.linalg.norm(b),np.finfo(float).tiny))
def main(out):
 rp=BASE/'result.json';fp=BASE/'fields.npz';assert sha(DRIVER)==P['driver'] and sha(rp)==P['result'] and sha(fp)==P['fields'] and sha(KERNEL)==P['kernel'];r=json.loads(rp.read_text());assert r['script_sha256']==P['driver'] and r['kernel_sha256']==P['kernel'] and (r['tetrahedra'],r['solve_unknowns'],r['loops'],r['surface_charge_ranges'])==(48,72,25,47)
 with np.load(fp,allow_pickle=False) as d:
  T=d['current_transform'];q=d['boundary_divergence_range'];h=d['cell_integrated_current_map'];st=[]
  for i,c in enumerate(r['cases']):
   s=d[f'case_{i:02d}_scaled_solution'];rhs=d[f'case_{i:02d}_incident_rhs'];cur=d[f'case_{i:02d}_current'];rho=d[f'case_{i:02d}_boundary_charge'];R=d[f'case_{i:02d}_reaction'];mom=d[f'case_{i:02d}_current_volume_moment'];dip=d[f'case_{i:02d}_charge_dipole'];om=2*np.pi*c['frequency_hz'];modal=s.copy();modal[25:]*=om
   st.append((rel(cur,T@modal),rel(rho,1j*q@s[25:]),rel(R,rhs.T@modal),rel(mom,h.sum(axis=0)@modal),rel(dip,mom/(1j*om))))
 checks={'transform_shape':list(T.shape),'charge_shape':list(q.shape),'integrated_current_shape':list(h.shape),'max_current_map':max(x[0] for x in st),'max_charge_map':max(x[1] for x in st),'max_reaction':max(x[2] for x in st),'max_moment':max(x[3] for x in st),'max_dipole':max(x[4] for x in st),'max_field_reproduction_change':max(c['field_l2_change_from_48_tetra'] for c in r['cases']),'max_absorption_reproduction_change':max(c['absorption_relative_change'] for c in r['cases']),'max_power':max(c['power_relative'] for c in r['cases']),'max_radiation':max(c['radiation_relative'] for c in r['cases'])}
 assert checks['transform_shape']==[192,72] and checks['charge_shape']==[48,47] and checks['integrated_current_shape']==[48,3,72] and all(checks[x]<1e-10 for x in ('max_current_map','max_charge_map','max_reaction','max_moment','max_dipole')) and checks['max_field_reproduction_change']<1e-8 and checks['max_absorption_reproduction_change']<1e-8
 out.mkdir(parents=True,exist_ok=False);p=out/'independent-review.json';t=out/'independent-review.json.tmp';x={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_CONSTANT_CURRENT_48_REPRODUCTION','pins':P,'checks':checks,'scope':'Source-free homogeneous finite-box scalar projection/reproduction only; no 384 solve, convergence, source-solid, interface, port, or board claim.'};t.write_text(json.dumps(x,indent=2)+'\n');os.replace(t,p);print(json.dumps({'status':x['status'],'sha256':sha(p)}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);main(p.parse_args().output.resolve())
