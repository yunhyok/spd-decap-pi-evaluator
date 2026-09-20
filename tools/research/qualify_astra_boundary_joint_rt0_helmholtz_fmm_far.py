"""SPD Decap PI Evaluator v0.23.1: boundary-joint all-source FMM FAR qualifier."""
from pathlib import Path
import argparse,hashlib,json,numpy as np
from apply_astra_boundary_joint_rt0_helmholtz_fmm import action,self_check
ROOT=Path(__file__).resolve().parents[2]
def run(out):
 if out.exists():raise FileExistsError(out)
 out.mkdir(parents=True);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz',allow_pickle=False) as d:v=d['vertices_local_um']*1e-6;c=d['cells']
 with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz',allow_pickle=False) as d:x=d['energy_orthonormal_face_flux'][:,0]
 targets=v[c[[0,1,2]]].mean(1);values=[];meta=[]
 for f in (1e3,1e6,1e8):
  z,m=action(x,f,targets);values.append(z);meta.append(m)
 np.savez_compressed(out/'far-action.npz',targets_m=targets,frequencies_hz=np.array([1e3,1e6,1e8]),vector_potential=np.array(values),omitted_points=np.array([m['omitted'] for m in meta]))
 json.dump({'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'STOP_REQUIRES_SAVED_SELF_CONTROL_TARGET_SELECTION_AND_DIRECT_NEAR_COMPARISON','scope':'All 5304 q3 sources were applied; receipt intentionally stops before claiming required saved worst/median/largest bridge target ownership.'},(out/'result.json').open('x'),indent=2)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args();self_check() if a.self_check else run(a.output.resolve())
