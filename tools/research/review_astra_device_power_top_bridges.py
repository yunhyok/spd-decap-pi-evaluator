"""Saved-only audit of canonical selected-power TOP bridge02."""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-device-power-top-bridges-review-01'
P={'tools/research/prepare_astra_device_power_top_bridges.py':'bbef5ca185703088e0fa51a4a0c2f4fc2c505e7512a468b4cdd5bf2b91a8e220','outputs/research/astra-device-power-top-bridges-02/result.json':'150cef6607c156e1be6d4c1b97a63d48cdabadb43ed3d7ae25cae430615ffb0c','outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json':'583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790','outputs/research/astra-device-power-top-bridges-02/selected-power-top-union.wkb':'65a9b8ea8424f9b44c308dd504baec813a37fb71ba1b4c8867a7b777c12b295f'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(ROOT/k) for k in P};n(a==P,'pins');r=json.loads((ROOT/'outputs/research/astra-device-power-top-bridges-02/result.json').read_text());x=json.loads((ROOT/'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json').read_text())
 ins,com,mates=x['instances'],x['components'],x['canonical_interface_mates'];n(len(ins)==933 and len(com)==45 and len(mates)==64,'counts');n(Counter(q['count'] for q in com)=={4:6,8:21,107:6,12:12},'chains');n(sum(q['count'] for q in com)==978 and sum(q['count']-1 for q in com)==933,'graph')
 n(all(q['right_pin']!=q['left_pin'] and len(q['pad_indices'])==2 and q['translation_xy_um'][0]==q['translation_xy_um'][0] for q in ins),'translations');n(x['bridge_z_interval_um']==[0.,25.],'z')
 for q in mates:
  n(len(q['shared_polygon_xyz_um'])>=3 and 0<=q['u0']<q['u1']<=1 and 0<q['area_fraction']<=1,'mate support');n(np.max(np.abs(np.array(q['pad_b_area_vector_m2'])+np.array(q['bridge_b_area_vector_m2'])))<1e-24,'opposing B')
 n(abs(r['canonical_bridge_volume_um3']-r['canonical_bridge_area_um2']*25)<1e-8 and r['canonical_interface_mate_piece_count']==64,'volume');n(r['pad_bridge_overlap_area_um2']==0 and r['owned_source_union_symmetric_difference_area_um2']<1e-5 and r['authoritative_source_union_polygon_count']==45,'union measures')
 receipt={'program':'review_astra_device_power_top_bridges','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'translations':933,'chains':45,'chain_sizes':{'4':6,'8':21,'12':12,'107':6},'canonical_volume_um3':r['canonical_bridge_volume_um3'],'mate_pieces':64,'max_mate_B_sum_m2':0.0,'authoritative_union_polygons':45,'union_symmetric_difference_area_um2':r['owned_source_union_symmetric_difference_area_um2']},'scope':'The original overlapping 45-component union remains authoritative; no snap or buffer is accepted. The six 107-pad chains span global geometry. Current/volume conformity, DOFs and Green assembly remain open.'}
 OUT.mkdir(parents=True,exist_ok=False);t=OUT/'independent-review.json';t.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':receipt['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
