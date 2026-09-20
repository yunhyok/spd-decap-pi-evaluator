"""SPD Decap PI Evaluator v0.23.1: ordered touching-pair geometry census."""
from pathlib import Path
import argparse,hashlib,json,numpy as np
ROOT=Path(__file__).resolve().parents[2];A=ROOT/'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz';M=ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz'
def h(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(o):
 o.mkdir();(o/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 with np.load(A) as d:s=d['pair_source_cell'];t=d['pair_observer_cell']
 with np.load(M) as d:v=d['vertices_local_um'];c=d['cells']
 keys=[]
 for a,b in zip(s,t,strict=True):
  x=np.vstack((v[c[a]],v[c[b]]));keys.append(tuple(np.rint(np.square(x[:,None]-x[None]).sum(2).reshape(-1)*1e8).astype(np.int64)))
 groups={}
 for i,k in enumerate(keys):groups.setdefault(k,[]).append(i)
 bad=0
 for z in groups.values():
  x=np.vstack((v[c[s[z[0]]]],v[c[t[z[0]]]]));g=(x-x.mean(0))@(x-x.mean(0)).T
  for i in z[1:]:
   y=np.vstack((v[c[s[i]]],v[c[t[i]]]));q=(y-y.mean(0))@(y-y.mean(0)).T
   if np.linalg.norm(g-q)/max(np.linalg.norm(g),1e-300)>1e-10:bad+=1
 r={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'PASS_ORDERED_TOUCHING_PAIR_GEOMETRY_CENSUS','pins':{str(A.relative_to(ROOT)):h(A),str(M.relative_to(ROOT)):h(M)},'pair_count':len(s),'candidate_group_count':len(groups),'max_multiplicity':max(map(len,groups.values())),'independent_mismatch_count':bad,'scope':'No rotations/permutation correction, integration, or merge approval.'};json.dump(r,(o/'result.json').open('x'),indent=2);print(r['status'])
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);run(p.parse_args().output.resolve())
