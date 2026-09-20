"""One-edge spatial refinement with the same finite physical boundary."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy import sparse
from scipy.linalg import block_diag

import compare_astra_minimal_port_return as comparison
from compare_astra_source_fixture_port import ROOT, RDIR, FIXTURE, read, make_case
from astra_matrix_current_outer import generalized_difference

FINE = Path('C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture/return-mesh-refinement/return-mesh-refinement.npz')


def matrix(z,k):
    return sparse.csr_matrix((z[k+'_data'],z[k+'_col'],z[k+'_row_ptr']),shape=z[k+'_shape']).toarray()


def run():
    assert hashlib.sha256(FINE.read_bytes()).hexdigest() == '88eeb5f19f8ebb380f576c2904c9d516c862ce8922d5581e2fb9266161b48599'
    out=RDIR/'astra-refined-fixture-port-20260914-01'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    (out/'source-at-run.npz').write_bytes(FINE.read_bytes())
    coarse_dir=RDIR/'astra-source-fixture-port-20260914-q8-n64/cross05'
    coarse=read(coarse_dir/'fields.npz')
    old=read(FIXTURE); fine=read(FINE)
    dr,hr,rr=[matrix(fine,k) for k in ('D','H','R')]
    t,u=fine['current_prolongation_T'],fine['charge_prolongation_U']
    assert np.max(abs(dr@t-u@coarse['D'][164:,258:])) < 1e-13
    assert np.max(abs(hr@t-coarse['H'][[1,3],258:])) < 1e-13
    assert np.linalg.norm(t.T@rr@t-coarse['R'][258:,258:])/np.linalg.norm(coarse['R'][258:,258:]) < 1e-12
    resistance=block_diag(coarse['R'][:258,:258],rr)
    divergence=block_diag(coarse['D'][:164,:258],dr)
    h=np.zeros((4,271));h[:,:258]=coarse['H'][:,:258];h[[1,3],258:]=hr
    current=read(RDIR/'astra-refined-return-current-20260914-01/local-current-blocks.npz')
    assert np.array_equal(current['triangles_m'],fine['triangles_xy_m'])
    assert np.array_equal(current['z_bounds_m'],fine['z_bounds_m'])
    cxy=np.zeros((9,7))
    cxy[np.arange(9),fine['local_face_slot'].ravel()]=fine['local_face_sign'].ravel()
    lr=block_diag(cxy.T@current['L_xy_unsigned_local_h']@cxy,current['L_cap_local_h'])
    fields_dir=RDIR/'astra-refined-return-fields-20260914-01'
    far=read(fields_dir/'far-blocks.npz')
    cp=np.zeros((384,258));cp[np.arange(384),old['pwr_local_face_index'].ravel()]=old['pwr_local_face_sign'].ravel()
    cr=np.zeros((15,13))
    for cell in range(3):
        cr[5*cell:5*cell+3,:7]=cxy[3*cell:3*cell+3]
        cr[5*cell+3,7+2*cell]=1;cr[5*cell+4,8+2*cell]=1
    lc=cp.T@far['L_unsigned_local_pwr_return_h']@cr
    inductance=np.block([[coarse['L'][:258,:258],lc],[lc.T,lr]])
    pr=read(fields_dir/'halfspace-return.npz')['P_halfspace_raw_per_f']
    with np.load(RDIR/'astra-fixture-pwr-fields-20260914-q8-02/field-blocks.npz') as z:
        pp=z['P_halfspace_per_f']
    pc=far['P_halfspace_pwr_return_per_f']
    potential=np.block([[pp,pc],[pc.T,(pr+pr.T)/2]])+read(fields_dir/'deep-charge.npz')['P_deep_per_f']
    metadata=dict(program='SPD Decap PI Evaluator',version='0.23.1',
                  physical_contract='Same original finite fixture geometry and four terminal voltage groups; only source edge of row0 subdivided at exact midpoint. Source subface currents remain independent.',
                  original05_transplanted=False,source_fixture_sha256=hashlib.sha256(FINE.read_bytes()).hexdigest())
    case=make_case(dict(R=resistance,D=divergence,H=h,L=inductance,P=potential),old['termination_z_ohm'][0],metadata)
    np.savez_compressed(out/'fields.npz',**case['fields'])
    comparison.model=SimpleNamespace(assemble=lambda *args,**kwargs:case)
    answer=comparison.compare_case(2,1e6,8,out)
    coarse_result=json.loads((coarse_dir/'n1-f1000000-q8/result.json').read_text())
    zc,zf=complex(*coarse_result['z_direct_ohm']),complex(*answer['z_direct_ohm'])
    ti=block_diag(np.eye(258),t);uq=block_diag(np.eye(164),u)
    l_projected=ti.T@inductance@ti
    p_projected=uq.T@potential@uq
    projection=dict(L_energy_relative=generalized_difference(l_projected,coarse['L'],coarse['L']),
                    P_real_energy_relative=generalized_difference(p_projected.real,coarse['P'].real,coarse['P'].real),
                    P_relative_norm=float(np.linalg.norm(p_projected-coarse['P'])/np.linalg.norm(coarse['P'])))
    report=dict(status='PASS_ONE_EDGE_SPATIAL_COMPARISON' if all(answer['gates'].values()) else 'FAIL_REFINED_FIXTURE_GATE',
                coarse_z_ohm=comparison.pair(zc),fine_z_ohm=comparison.pair(zf),delta_z_ohm=comparison.pair(zf-zc),
                spatial_relative_change=float(abs(zf-zc)/abs(zf)),projection=projection,case=answer,
                scope='One spatial enrichment on identical geometry/boundary, not a mesh-converged reference or original-board PowerSI result. Fine source currents are independent; original05 was not transported to the new basis.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','coarse_z_ohm','fine_z_ohm','delta_z_ohm','spatial_relative_change','projection')}),flush=True)


if __name__ == '__main__':
    run()
