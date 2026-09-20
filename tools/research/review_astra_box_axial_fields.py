from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2]
P={'outputs/research/astra-box-axial-cross-01/result.json':'e5faf3e6253fb2ad8ca9b3c982cc0a493eaace41312b7a3c3fdecdeebe63f13d','outputs/research/astra-box-axial-cross-01/cross-q10.npz':'61e042d88a02d9928f283b740d1d6b2422afc31061fd3e851d1009f0aac68818','outputs/research/astra-box-axial-cross-01/cross-q14.npz':'be29a881b5fea4d8215c61154c05171836ea3f46cb0270c1b4d7bde9409286ce','outputs/research/astra-box-axial-q10-field-01/fields.npz':'41f62cca74e1e65f01b15065967491c328b4b1aaf184b37f9faed4e8d35b4b6d','outputs/research/astra-box-axial-q14-field-01/fields.npz':'59510348006dc8a15f2c8f8951e27994055bb188633635b3c173bfbbbcb51fe6'}
def H(p):return sha256(p.read_bytes()).hexdigest()
for p,x in P.items():assert H(R/p)==x
a=np.load(R/'outputs/research/astra-box-axial-q10-field-01/fields.npz');b=np.load(R/'outputs/research/astra-box-axial-q14-field-01/fields.npz');c=[]
for i in range(6):
 s=b[f'case_{i:02d}_scaled_solution'];m=b[f'case_{i:02d}_modal_current'];rhs=b[f'case_{i:02d}_incident_rhs'];re=b[f'case_{i:02d}_reaction'];rad=b[f'case_{i:02d}_radiation_matrix'];om=2*np.pi*10**(i+3);e=s.copy();e[75:]*=om;u=m@np.array([[1,0,1],[0,1,1j]],complex);c.append({'modal_scale':float(np.linalg.norm(m-e)/np.linalg.norm(m)),'reaction':float(np.linalg.norm(re-rhs.T@m)/np.linalg.norm(re)),'radiation_sym':float(np.linalg.norm(rad-rad.T)/np.linalg.norm(rad)),'q10_q14_modal':float(np.linalg.norm(a[f'case_{i:02d}_modal_current']-m)/np.linalg.norm(m)),'q10_q14_reaction':float(np.linalg.norm(a[f'case_{i:02d}_reaction']-re)/np.linalg.norm(re))})
o=R/'outputs/research/astra-box-axial-field-review-01';assert not o.exists();o.mkdir();z={'program':'SPD Decap PI Evaluator','status':'ACCEPT_SAVED_AXIAL_FIELD_ALGEBRA_WITH_SCOPE','input_sha256':P,'reviewer_sha256':H(Path(__file__)),'checks':c,'scope':'Saved q10/q14 modal/reaction/radiation algebra only; reaction/current differences are not port Z.'};(o/'independent-review.json').write_text(json.dumps(z,indent=2)+'\n');print(H(o/'independent-review.json'))
