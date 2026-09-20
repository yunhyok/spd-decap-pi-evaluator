import argparse,hashlib,json,os
from pathlib import Path
R=Path(__file__).resolve().parents[2];D=R/'outputs/research/astra-box-eddy-polynomial-basis-01';P={'driver':'48e8c5552ec9499548785e34a81dacef64af2fcf1f5a751ea6aa165fe52ef0a3','result':'85252d666b3f2fd0fee9ae54341da11ea790882cde10abe75f547f3cbd40eda5'}
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(o):
 assert h(D/'driver-at-run.py')==P['driver'] and h(D/'result.json')==P['result'];r=json.loads((D/'result.json').read_text());cs=r['cases'];assert [c['magnetic_axis'] for c in cs]==['x','y','z']
 checks=[]
 for c in cs:
  m=c['modes'];ns=[x['stream_modes'] for x in m];assert ns==[1,4,9,16,25,49,81];e=[x['poisson_energy_m4'] for x in m];assert all(b>a for a,b in zip(e,e[1:]));checks.append({'axis':c['magnetic_axis'],'max_quad':max(x['quadrature_refinement_relative'] for x in m),'max_residual':max(x['mass_solve_residual'] for x in m),'deficit16':m[3]['loss_deficit_relative'],'deficit81':m[-1]['loss_deficit_relative']})
 assert max(x['max_quad'] for x in checks)<1e-12 and max(x['max_residual'] for x in checks)<1e-12
 o.mkdir(parents=True,exist_ok=False);p=o/'independent-review.json';t=o/'independent-review.json.tmp';x={'status':'ACCEPT_BOX_EDDY_POLYNOMIAL_BASIS','pins':P,'checks':checks,'scope':'Polynomial stream-function approximation-space Poisson diagnostic only; no mixed-axis/fullwave/current-charge basis or board/fullband/port claim.'};t.write_text(json.dumps(x,indent=2)+'\n');os.replace(t,p);print(json.dumps({'status':x['status'],'sha256':h(p)}))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--output',type=Path,required=True);main(a.parse_args().output.resolve())
