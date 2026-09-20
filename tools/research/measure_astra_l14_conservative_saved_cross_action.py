"""SPD Decap PI Evaluator v0.23.1: conservative L14 readout against saved cross A."""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from project_astra_separated_layer_grid import project
from probe_astra_l25_saved_current_moments import moments
from reconstruct_astra_native_loaded_field import _Budget
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'outputs/research'
OUT=R/'astra-l14-conservative-saved-cross-action-20260912'
PINS={
    R/'astra-l14-fixed-load-spike-refined-pair-20260912/pair.npz':'4604376124395f22d5ad923100230152062d1117c1cd275f160615cd218aa98f',
    R/'astra-l14-fixed-load-spike-refined-pair-20260912/result.json':'a125f0e1b91b02a5a98aa3d29c4d8e4541345b1eb0e1d15f38aac2b63ea4a7c2',
    R/'astra-separated-layer-grid-mutual-h64-01/cross-layer-vector-potential.npz':'e4f375a705919b42f0f9e4d23fadeb94546cf2943fd5dfa413aafb19e4203687',
    R/'astra-separated-layer-grid-mutual-h64-01/result.json':'7ed771eabe7483718c9b654f3159dffda8b37936e34ecf9ffc21986915d956a8',
    R/'astra-separated-layer-grid-projection-h64-01/l14-projection-checkpoint.npz':'fbc06c450e14922a905f0142a7b9f8dc22f3fc07c52722b2e58312ba9c52f091',
    ROOT/'tools/research/project_astra_separated_layer_grid.py':'49d823d9f4c625e95b29775cf02acb7900a8b287c51c2babfe3842f5f24a6415',
    ROOT/'tools/research/probe_astra_l25_saved_current_moments.py':'e30fefd2ecb618f58fa8ca2eb3486b0f05623a75f33e1be6a24f590be593367e',
}


def run():
    started=monotonic(); budget=_Budget.create(180,4)
    for p,h in PINS.items(): assert sha(p)==h,p
    paths=list(PINS)
    result=json.loads(paths[1].read_text()); action_result=json.loads(paths[3].read_text())
    assert result['qualified'] and all(result['gates'].values())
    assert result['artifact_sha256']==PINS[paths[0]]
    assert action_result['artifact']['sha256']==PINS[paths[2]]
    assert action_result['frequency_hz']==1e6 and action_result['source_current_a']==1
    with np.load(paths[0],allow_pickle=False) as z:
        vertices=z['node_xy_um'][z['triangles'][z['free_triangle_indices']]]
        local=z['local_facet_branch_index']; signs=z['local_outward_flux_sign']; current=z['rt0_current_a']
        flux=np.zeros(local.shape,complex); valid=local>=0
        flux[valid]=current[local[valid]]*signs[valid]
    area,average,alpha,mean_norm,variance_norm=moments(vertices*1e-6,flux)
    energy=float((mean_norm.sum()+variance_norm.sum())/1191.8)
    energy_error=abs(energy-result['rt0_joule_w'])/result['rt0_joule_w']
    assert energy_error<1e-9
    with np.load(paths[2],allow_pickle=False) as z:
        a_other=z['l14_vector_potential_vs_per_m']; origin=z['origin_xy_um']; pitch=float(z['pitch_um'][0]); shape=z['shape_yx']
        assert np.array_equal(shape,[1554,1554]) and pitch==64 and np.array_equal(origin,[-49728,-49728])
    with np.load(paths[4],allow_pickle=False) as z:
        old_grid=z['grid_integrated_current_a_m']
        assert np.array_equal(z['origin_xy_um'],origin) and np.array_equal(z['shape_yx'],shape) and float(z['pitch_um'][0])==pitch
    old_cross=complex(2*np.sum(old_grid*a_other))
    saved_cross=2*sum(complex(*p['forward_bilinear_j']) for p in action_result['pairs'] if 'l14' in p['layers'])
    action_readback=abs(old_cross-saved_cross)/max(abs(saved_cross),1e-30)
    assert action_readback<1e-10
    del old_grid
    OUT.mkdir(exist_ok=False); (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'phase':'project_final_rt0_current_to_existing_h64_grid','triangles':len(vertices)}),flush=True)
    grid,pm=project(vertices,average,alpha,origin,pitch,shape,budget=budget)
    cross=complex(2*np.sum(grid*a_other))
    # The explicit factor two counts L14-to-other and other-to-L14 reciprocal terms.
    # This is a fixed-field readout; it does not make the modified fields a board solution.
    omega=2*np.pi*1e6
    pair=lambda v:[float(v.real),float(v.imag)]
    np.savez_compressed(OUT/'l14-projected-current.npz',grid_integrated_current_a_m=grid,
                        origin_xy_um=origin,pitch_um=np.array([pitch]),shape_yx=shape)
    report=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='COMPLETED_CONSERVATIVE_L14_SAVED_CROSS_READOUT',
                elapsed_s=monotonic()-started,projection=pm,rt0_moment_energy_w=energy,
                rt0_energy_relative_error=energy_error,saved_action_readback_relative=action_readback,
                old_p1_twice_l14_other_bilinear_j=pair(old_cross),new_rt0_twice_l14_other_bilinear_j=pair(cross),
                old_p1_frozen_cross_sensitivity_ohm=pair(1j*omega*old_cross),
                new_rt0_frozen_cross_sensitivity_ohm=pair(1j*omega*cross),
                relative_cross_readout_change=abs(cross-old_cross)/abs(old_cross),
                finite_space_diagnostic=dict(p1_energy_w=result['p1_joule_w'],rt0_energy_w=result['rt0_joule_w'],
                                            gap_w=result['refined_gap_w'],gap_relative_p1=result['refined_gap_w']/result['p1_joule_w']),
                budget=budget.receipt(),inputs={str(p):h for p,h in PINS.items()},driver_sha256=sha(Path(__file__)),
                artifact_sha256=sha(OUT/'l14-projected-current.npz'),
                scope='One affine-conservative L14 projection and readout against saved L02+L25 cross vector potential at 1MHz. Same h64 source-square/target-centre kernel and other-layer fields. Baseline is the original coarse P1 field; new source is final fixed-P0 local RT0 reconstruction. No new Green/FFT, self block, coefficient insertion, coupled solve, finite delta Z, magnetic-error bound, full return closure or broadband/PowerSI accuracy claim.')
    (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    run()
