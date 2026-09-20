"""Saved numerical QA for the seven-column P-joint basis extension."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-joint-seven-basis-extension-review-01'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 d=ROOT/'outputs/research/astra-joint-seven-basis-extension-01';r=json.loads((d/'result.json').read_text());assert H(ROOT/'tools/research/prepare_astra_joint_seven_basis_extension.py')==r['driver_sha256'] and H(d/'result.json')=='a13d8339234a5f1fbe816f22f13f19e6dbd0d928d307878bfb6b41720af83e4f' and H(d/'seven-basis.npz')==r['artifact_sha256']
 for p,h in r['pins'].items():assert H(ROOT/p)==h,p
 with np.load(d/'seven-basis.npz') as z:a={k:z[k] for k in z.files}
 with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:R=csr_matrix((z['resistance_data_ohm'],z['mass_col'],z['mass_row_ptr']),shape=tuple(z['mass_shape']))
 with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as z:old=z['face_flux_basis']
 raw=a['raw_face_flux_basis'];fin=a['final_face_flux_basis'];G=fin.T@(R@fin);Gr=raw.T@(R@raw);rng=np.random.default_rng(7);x=rng.normal(size=7)+1j*rng.normal(size=7);fine=float(np.real((fin@x).conj()@(R@(fin@x))));small=float(np.real(x.conj()@G@x));out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'pins':{'driver':H(ROOT/'tools/research/prepare_astra_joint_seven_basis_extension.py'),'result':H(d/'result.json'),'artifact':H(d/'seven-basis.npz'),**r['pins']},'checks':{'old_five_unchanged_max':float(np.abs(raw[:,:5]-old).max()),'raw_R_relative':float(np.linalg.norm(Gr-a['physical_raw_resistance_ohm'])/np.linalg.norm(Gr)),'final_R_relative':float(np.linalg.norm(G-a['physical_final_resistance_ohm'])/np.linalg.norm(G)),'raw_divergence_max':float(abs(a['divergence_raw']).max()),'final_divergence_max':float(abs(a['divergence_final']).max()),'old_new_orthogonality_max':float(abs(G[:5,5:]).max()),'final_spd_min':float(np.linalg.eigvalsh(G).min()),'transform_reconstruction_max':float(abs(raw@a['raw_to_final_transform']-fin).max()),'new_complex_energy_relative':abs(fine-small)/fine,'terminal_raw':a['terminal_flux_raw_a'].tolist(),'terminal_final':a['terminal_flux_final_a'].tolist()},'scope':'Basis columns only; complementary boundary/circulation/charge spaces remain. No Green/field/port/board approval.'}
 assert out['checks']['old_five_unchanged_max']==0 and out['checks']['raw_R_relative']<1e-12 and out['checks']['final_R_relative']<1e-12 and out['checks']['raw_divergence_max']<1e-12 and out['checks']['final_divergence_max']<1e-12 and out['checks']['final_spd_min']>0 and out['checks']['new_complex_energy_relative']<1e-12
 OUT.mkdir(parents=True,exist_ok=False);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
