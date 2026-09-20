from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-outer-static-touching-replacement-review-01'
P={'outputs/research/astra-outer-static-touching-replacement-02/driver-at-run.py':'592f6a37bd609b8c34d00615f8dc75239da3553dcf721b6d0ace00de050a6b00','outputs/research/astra-outer-static-touching-replacement-02/result.json':'3e65a6e20e8fbd305bcb01a9bb66cbeff89a1e0c020ff6388bc20dff09c0c0d0','outputs/research/astra-outer-static-touching-replacement-02/self-replacement.npz':'bcf2202698cefd0c256a3cd20152259ccc1bd50d602ec8bb6d4527e2914a1655'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 a={k:h(R/k) for k in P};q={'program':'review_astra_outer_static_touching_replacement','version':1,'status':'P1_PIN_MISMATCH','reviewer_sha256':h(Path(__file__)),'pins_actual':a,'note':'Result hash was not supplied in full by task payload; cannot pin strict review without inventing it.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
