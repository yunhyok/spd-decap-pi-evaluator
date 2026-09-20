"""Saved-array audit of Chebyshev-source sample metrics."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-joint-chebyshev-sources-review-02'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 helper=ROOT/'tools/research/qualify_astra_joint_chebyshev_sources.py';res=ROOT/'outputs/research/astra-joint-chebyshev-sources-01/result.json';art=ROOT/'outputs/research/astra-joint-chebyshev-sources-01/compression.npz';rh=json.loads(res.read_text());assert H(helper)==rh['driver_sha256'] and H(art)==rh['artifact_sha256']
 with np.load(art) as z:
  total=z['authoritative_monopole'];moment={};maximum={}
  for n in (4,6,8):
   w=z[f'proxy_weights_n{n}'];p=z[f'proxy_points_n{n}'];center=(z['lower_m']+z['upper_m'])/2;moment[str(n)]={'proxy_weight_finite':bool(np.isfinite(w).all()),'proxy_first_moment_norm_m':float(np.linalg.norm((p-center).T@w))}
  groups=[slice(3*i,3*i+3) for i in range(9)]+[slice(27,28),slice(28,29)]
  for name in ('real','imaginary','sine_tail'):
   a=z[f'proxy_{name}_n8'];b=z[f'direct_{name}_q3']; rows=[]
   for ci,s in enumerate(groups):
    for fi in range(3):
     for ri in range(3):
      q=slice(26*ri,26*(ri+1));den=np.linalg.norm(b[fi,q,s]);rows.append((float(np.linalg.norm((a-b)[fi,q,s])/max(den,1e-300)),ci,fi,ri,float(den)))
   maximum[name]=dict(zip(('relative','channel_group','frequency_index','radius_index','reference_norm'),max(rows)))
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'P2_METRIC_AGGREGATION_SCOPE','reviewer_sha256':H(__file__),'pins':{'helper':H(helper),'result':H(res),'compression':H(art)},'saved_status':rh['status'],'moment_readback':moment,'n8_required_per_frequency_radius_channel_maxima':maximum,'finding':{'P2':'grouped_relative computes one norm over every frequency and all 78 targets for each channel group. Its n4/n6/n8 gate is therefore not a per-frequency/per-radius/channel guarantee; cancellation or a large easy target can mask a failing subgroup. Gate each (frequency,radius,channel group), retain a max table, and apply the same rule to q3-v-q4 and static control.'},'saved_n8_result':'No masked saved failure found in this artifact: n8 max real subgroup 5.723781842e-6; imaginary/tail max 4.5378621e-11, all <5e-5. This does not repair the gate definition.','static_kernel_read':'Driver uses cos(kR)/R, sine-tail recurrence then -k*authoritative_monopole, with k=2pi*f/C0; tensor source weights preserve monopole and first moments.','scope':'Read-only saved sample audit; no direct full source rerun, Green/FMM/field or board claim.'}
 out['supersedes']='review-01: its monopole comparison used authoritative integrated moments rather than the saved q3 channel sum, so it is not an admissible independent monopole metric.'
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
