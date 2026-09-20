"""SPD Decap PI Evaluator v0.23.1: five shared lifts plus two mapped P vertical lifts."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from scipy.sparse import csr_matrix

ROOT=Path(__file__).resolve().parents[2]
PINS={
 "outputs/research/astra-shared-interface-interior-lifts-04/result.json":"2df58fa459a0e016916b8c6f3e23c8c44cedd58d191b69111a2ad6688e131eb8",
 "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz":"9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92",
 "outputs/research/astra-post-top-to-lower-transport-lift-01/result.json":"a644574cba19bbc00af35cd879e350ef944c029ab07da1f1272ec1aabba86e96",
 "outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz":"b169d79ce8fc61c141efa572ce2aa2c4905fc5efa3905cac139dd07efc165538",
 "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":"14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
 "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":"2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
}
def sha(p):return sha256(p.read_bytes()).hexdigest()
def face_key(vertices,face,shift):
    return tuple(np.rint((vertices[face]-shift)*1e15).astype(np.int64).ravel())
def run(out):
    t=monotonic(); arrays={}; out.mkdir(parents=False,exist_ok=False);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
      for p,h in PINS.items():assert sha(ROOT/p)==h,p
      with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as z: old=z['face_flux_basis'];top_l=z['electrode_left_ids'];top_r=z['electrode_right_ids'];old_gram=z['energy_gram_ohm']
      with np.load(ROOT/'outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz') as z:
        vertical=z['face_flux_basis'][:,0]; post_cells=z['post_cell_ids']; vtop=z['top_patch_face_ids'];vlower=z['lower_contact_face_ids'];active=z['active_face_ids'];saved_ev=float(z['post_joule_energy_ohm'][0])
      with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:
        vertices=z['vertices_local_um']*1e-6;cells=z['cells'];body=z['cell_body'];faces=z['face_vertices'];boundary=z['boundary_face_ids']
      with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:
        R=csr_matrix((z['resistance_data_ohm'],z['mass_col'],z['mass_row_ptr']),shape=tuple(z['mass_shape']));D=csr_matrix((z['volume_b_data'],z['volume_b_col'],z['volume_b_row_ptr']),shape=tuple(z['volume_b_shape']));bfaces=z['boundary_face_ids']
      left=np.flatnonzero(body==0);right=np.flatnonzero(body==1);assert np.array_equal(post_cells,left) and old.shape==(12546,5)
      # Exact translated face map based on ordered local face vertex coordinates.
      shift=vertices[cells[right]].mean((0,1))-vertices[cells[left]].mean((0,1));assert np.linalg.norm(shift[1:])<1e-18
      lookup={tuple(np.rint(vertices[faces[f]]*1e15).astype(np.int64).ravel()):int(f) for f in range(len(faces))}
      mapped=np.full(len(faces),-1,dtype=np.int64)
      for f in np.flatnonzero(np.abs(vertical)>0):
        # source left coordinates + shift equal a right-face coordinate
        target=tuple(np.rint((vertices[faces[f]]+shift)*1e15).astype(np.int64).ravel())
        mapped[f]=lookup.get(target,-1)
        assert mapped[f]>=0,(f,target)
      vr=np.zeros_like(vertical);vr[mapped[mapped>=0]]=vertical[mapped>=0]
      raw=np.column_stack((old,vertical,vr)); assert np.array_equal(raw[:,:5],old)
      # Four integrated outward terminal maps, raw then final.
      lower_l=vlower; lower_r=mapped[vlower]; top_r_v=mapped[vtop]
      assert np.all(lower_r>=0) and np.all(top_r_v>=0)
      terminal_raw=np.vstack((raw[top_l].sum(0),raw[top_r].sum(0),raw[lower_l].sum(0),raw[lower_r].sum(0)))
      expected_old=np.vstack((old[top_l].sum(0),old[top_r].sum(0),np.zeros(5),np.zeros(5)))
      assert np.max(np.abs(terminal_raw[:,:5]-expected_old))<3e-14
      assert np.max(np.abs(terminal_raw[:,5]-np.array([-1.,0.,1.,0.])))<3e-14
      assert np.max(np.abs(terminal_raw[:,6]-np.array([0.,-1.,0.,1.])))<3e-14
      assert np.max(np.abs(D@raw))<3e-12
      other=np.setdiff1d(bfaces,np.r_[vtop,vlower,top_r_v,lower_r]);assert np.max(np.abs(raw[other,5:]))<3e-14
      gram=raw.T@(R@raw);assert np.linalg.norm(gram[:5,:5]-old_gram)/np.linalg.norm(old_gram)<2e-12
      assert abs(gram[5,5]-saved_ev)/saved_ev<3e-12 and abs(gram[6,6]-saved_ev)/saved_ev<3e-12
      # Keep old5 fixed.  R-project new raw columns away from old then normalize only new2.
      coeff=np.linalg.solve(gram[:5,:5],gram[:5,5:]); residual=raw[:,5:]-old@coeff;gres=residual.T@(R@residual);L=np.linalg.cholesky((gres+gres.T)/2);Tnew=np.linalg.inv(L.T)
      transform=np.eye(7);transform[:5,5:]=-coeff@Tnew;transform[5:,5:]=Tnew
      final=raw@transform;final_gram=final.T@(R@final);terminal_final=terminal_raw@transform
      assert np.array_equal(final[:,:5],old) and np.max(np.abs(final_gram[:5,5:]))<2e-12 and np.max(np.abs(final_gram[5:,5:]-np.eye(2)))<2e-12
      eig=np.linalg.eigvalsh((gram+gram.T)/2); assert eig.min()>0 and np.linalg.eigvalsh((final_gram+final_gram.T)/2).min()>0
      # Cellwise RT0 reconstruction checks use the full physical R identity.
      rmap=float(np.linalg.norm(final.T@(R@final)-final_gram)/np.linalg.norm(final_gram));assert rmap<2e-14
      arrays=dict(raw_face_flux_basis=raw,final_face_flux_basis=final,raw_to_final_transform=transform,physical_raw_resistance_ohm=gram,physical_final_resistance_ohm=final_gram,terminal_names=np.array(['TOP_L','TOP_R','LOWER_L','LOWER_R']),terminal_flux_raw_a=terminal_raw,terminal_flux_final_a=terminal_final,old_terminal_flux_a=expected_old,vertical_left_face_map=np.flatnonzero(np.abs(vertical)>0),vertical_right_face_map=mapped[np.flatnonzero(np.abs(vertical)>0)],left_post_cells=left,right_post_cells=right,divergence_raw=D@raw,divergence_final=D@final,translation_m=shift)
      metrics=dict(cell_count=int(len(cells)),face_count=int(raw.shape[0]),raw_vertical_terminal_map=terminal_raw[:,5:].tolist(),raw_divergence_max=float(np.max(np.abs(D@raw))),other_exterior_vertical_max=float(np.max(np.abs(raw[other,5:]))),old_gram_relative=float(np.linalg.norm(gram[:5,:5]-old_gram)/np.linalg.norm(old_gram)),vertical_energy_relative_left=float(abs(gram[5,5]-saved_ev)/saved_ev),vertical_energy_relative_right=float(abs(gram[6,6]-saved_ev)/saved_ev),raw_spd_min=float(eig.min()),final_spd_min=float(np.linalg.eigvalsh((final_gram+final_gram.T)/2).min()),old_new_orthogonality=float(np.max(np.abs(final_gram[:5,5:]))),new_normalization=float(np.max(np.abs(final_gram[5:,5:]-np.eye(2)))),cellwise_energy_mapping_relative=rmap)
      status,failure='PASS_JOINT_SEVEN_BASIS_EXTENSION',None
    except Exception:
      metrics,status,failure={},'STOP_JOINT_SEVEN_BASIS_EXTENSION',traceback.format_exc()
    np.savez_compressed(out/'seven-basis.npz',**arrays);result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=status,failure=failure,pins=PINS,metrics=metrics,elapsed_s=monotonic()-t,driver_sha256=sha(Path(__file__)),artifact_sha256=sha(out/'seven-basis.npz'),scope='Five shared TOP/interior coordinates remain unchanged; two mapped ideal-fixture TOP-to-actual-r20-lower columns are R-orthogonalized/normalized only in the new subspace. Raw zero other-exterior flux is a basis construction. Fine/exterior/charge complements and common P port semantics remain; no Green, field, port solve, return closure, or board claim.')
    (out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');print(json.dumps(result));return 0 if failure is None else 2
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);raise SystemExit(run(p.parse_args().output.resolve()))
