import argparse,hashlib,json,os
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];D=R/'outputs/research/astra-box-eddy-poisson-limit-01';P={'driver':'7a674bee15ccee2502848a55198a5ee9fdc1338ba6d436e95b14814991e3d954','result':'1081ca3f6147e8435fd439f53ce00b02c07fc05ce4d9ddd7d3543f8f5792646c'}
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(o):
 assert h(D/'driver-at-run.py')==P['driver'] and h(D/'result.json')==P['result'];r=json.loads((D/'result.json').read_text());cs=r['cases'];assert [x['tetrahedra'] for x in cs]==[6,48,384] and [x['closed_current_coordinates'] for x in cs]==[1,25,289]
 vals=[]
 for c in cs:
  e=np.array(c['exact_geometric_response_m5']);a=np.array(c['geometric_response_m5']);n=a/np.sqrt(e[:,None]*e[None,:]);w=np.linalg.eigvalsh(n); vals.append({'name':c['name'],'max_series_bound':max(x['relative_series_bound'] for x in c['poisson_references']),'max_double_bound':max(x['double_series_relative_bound'] for x in c['poisson_references']),'mass_residual':c['mass_solve_residual'],'pytagorean_error':c['exact_projection_field_l2_error'],'eig':w.tolist(),'deficit':c['eddy_loss_deficit_relative']});assert w.min()>-1e-12 and w.max()<=1+1e-11
 assert vals[0]['eig'][0]==0 and vals[0]['pytagorean_error']==[0.9252662357833873,0.9252662357833873]
 o.mkdir(parents=True,exist_ok=False);p=o/'independent-review.json';t=o/'independent-review.json.tmp';x={'status':'ACCEPT_BOX_EDDY_POISSON_LIMIT','pins':P,'cases':vals,'scope':'omega-to-zero uniform homogeneous rectangular-body Poisson benchmark only; not finite-frequency, port, board, or skin-converged.'};t.write_text(json.dumps(x,indent=2)+'\n');os.replace(t,p);print(json.dumps({'status':x['status'],'sha256':h(p)}))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--output',type=Path,required=True);main(a.parse_args().output.resolve())
