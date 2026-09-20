"""SPD Decap PI Evaluator v0.23.1: independent saved static auxiliary review."""
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse

import prepare_astra_l04_contact_ntd_action as ntd
from check_astra_l04_two_direction_saved_fit import digest, TOTAL


def matrix(z, prefix, format):
    return format((z[prefix+'_data'],z[prefix+'_indices'],z[prefix+'_indptr']),shape=tuple(z[prefix+'_shape']))


def main():
    start=perf_counter(); root=Path(__file__).resolve().parents[2]
    output=root/'outputs/research/astra-l04-10mhz-closed-magnetic-auxiliary-01'
    result=json.loads((output/'result.json').read_bytes()); guard=json.loads((output/'external-budget.json').read_bytes())
    assert guard['status']=='COMPLETED_NATIVE_WORKER' and guard['exit_code']==0
    assert guard['sampled_peak_private_bytes'] <= guard['max_memory_bytes']
    assert digest(output/'driver-at-run.py')==guard['driver_sha256']
    for name in ('matrix_checkpoint','factor_diagnostics'):
        assert digest(Path(result[name]['path']))==result[name]['sha256']
    with np.load(result['matrix_checkpoint']['path'],allow_pickle=False) as z:
        k=matrix(z,'k',sparse.csc_matrix); a=matrix(z,'scaled_homega',sparse.csc_matrix); d=z['h_scale']
    with np.load(result['factor_diagnostics']['path'],allow_pickle=False) as z:
        rhs,normal,transpose=z['actual_scaled_closed_rhs'],z['normal_solution'],z['transpose_solution']
    assert a.shape==k.shape==(644870,644870) and rhs.shape==d.shape==(644870,)
    assert all(np.isfinite(v).all() for v in (a.data,k.data,d,rhs,normal,transpose))
    source,source_sha=ntd.PINS['stream_system']; assert digest(source)==source_sha
    with np.load(source,allow_pickle=False) as z:
        h=matrix(z,'h',sparse.csr_matrix)
    assert np.array_equal(1/np.sqrt(h.diagonal()),d)
    scale=sparse.diags(d); expected=scale@h@scale+1j*(2*np.pi*1e7)*(scale@k@scale)
    assembly=float(np.max(np.abs((expected-a).data),initial=0)/np.max(np.abs(a.data)))
    assert assembly<=2e-12
    final=root/'outputs/research/astra-l04-10mhz-complete-current-gcrotmk-01/complete-current-final.npz'
    assert digest(final)=='85e8db88de6a07024a7076549d53e7fc4060789cffbca39f229e025ff4fdaa91'
    with np.load(final,allow_pickle=False) as z:
        assert np.array_equal(rhs,z['final_true_residual'][TOTAL:]); psi=z['candidate_psi_physical']
    space,space_sha=ntd.PINS['stream_space']; assert digest(space)==space_sha
    with np.load(space,allow_pickle=False) as z:
        labels=z['mesh_node_stream_index'][z['branch_mesh_edges']]; orientation=z['branch_stream_orientation']
    psi_gauge=np.r_[0j,psi]; current=orientation*(psi_gauge[labels[:,1]]-psi_gauge[labels[:,0]])
    self_path=root/'outputs/research/astra-l04-rt0-self-magnetic-01/rt0-self-magnetic.npz'
    assert digest(self_path)=='20d2cfe73a5170b082cf5378e7e4c18c505f8667d808bb022c74d2828377c65c'
    with np.load(self_path,allow_pickle=False) as z:
        force=matrix(z,'lself',sparse.csc_matrix)@current
    weights=orientation*force; count=len(d)+1
    def accumulate(values):
        return (np.bincount(labels[:,1],weights=values,minlength=count)-np.bincount(labels[:,0],weights=values,minlength=count))[1:]
    expected_k_psi=accumulate(weights.real)+1j*accumulate(weights.imag)
    action=float(np.linalg.norm(k@psi-expected_k_psi)/np.linalg.norm(expected_k_psi))
    normal_error=float(np.linalg.norm(a@normal-rhs)/np.linalg.norm(rhs))
    transpose_error=float(np.linalg.norm(a.T@transpose-rhs)/np.linalg.norm(rhs))
    assert action<=2e-8 and max(normal_error,transpose_error)<=2e-8
    report={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'PASS_SAVED_STATIC_HOMEGA_REVIEW',
            'result_sha256':digest(output/'result.json'),'guard_sha256':digest(output/'external-budget.json'),
            'matrix_sha256':result['matrix_checkpoint']['sha256'],'factor_diagnostics_sha256':result['factor_diagnostics']['sha256'],
            'raw_h_scaled_assembly_relative':assembly,'independent_bincount_k_action_relative':action,
            'normal_actual_rhs_relative':normal_error,'transpose_actual_rhs_relative':transpose_error,
            'elapsed_s':perf_counter()-start,'scope':'Saved matrices/vectors only; no factorization, H solve, FMM/full action, physical or accuracy acceptance.'}
    (output/'hq-saved-static-homega-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__': main()
