"""SPD Decap PI Evaluator v0.23.1: saved 1MHz power/L04 partial magnetics."""
import hashlib
import json
from pathlib import Path
import time

import fmm3dpy
import fmm3dpy.lfmm3d_fortran as native_fmm
import numpy as np
from scipy import sparse
from apply_astra_l25_rt0_magnetic import _geometry, _scatter, MU0
from check_astra_l14_l25_point_rows import SOURCE, PIN, direct_rows

ROOT = Path('C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator')
R = ROOT/'outputs/research'
OLD = R/'astra-l14-l25-fixed-action-20260914-01'
STREAM = R/'astra-l04-fixed-contact-stream-01'
Q04 = STREAM/'l04-fixed-contact-current.npz'
SPACE = STREAM/'l04-fixed-contact-rt0-space.npz'
MESH = R/'astra-l04-conditional-sheet-mesh-02/l04-conditional-sheet-mesh-before-stiffness.npz'
SELF = R/'astra-l04-rt0-self-magnetic-01/rt0-self-magnetic.npz'
RUNTIME = R/'astra-l14-l25-fmm-nd2-runtime-20260914-01/result.json'
PINS = {SOURCE: PIN,
    Q04: 'dec07e1681c8400c43b6c79ab7a25ef37f7c19443441ad369016f6daefbfe9fd',
    SPACE: '5d31b3c6183eb4f43723eb80d1a545953a42fb2c9320be425d3ebc034cb51bb6',
    MESH: '6f2f396fe2319d60ad4b1586fd7043960e42f1d85c29f28a1d9c082302a3a211',
    SELF: '20d2cfe73a5170b082cf5378e7e4c18c505f8667d808bb022c74d2828377c65c',
    OLD/'reduction.json': 'e34a20e24a2af8b42010cf09d322f3d74fcdd8c2216c6252cc03f150fa2e477b',
    OLD/'fixed-current-point-action.npz': 'd2e851c701a85f38ce3b34493e342e10bd195fdc1a9a3e22ebd275c1e7cdc437',
    RUNTIME: '321160e7f8d63366c36df2b4bc64fc15bcc7fef3919a649c377e403154655f5e',
    STREAM/'result.json': '3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1'}
OUTPUT = ACTION = ACTION_RECEIPT = None  # Bound to a new HQ output by the owned-worker guard.
OMEGA = 2*np.pi*1e6


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def pair(value):
    return [float(value.real),float(value.imag)]


def run():
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    stream = json.loads((STREAM/'result.json').read_text())
    assert stream['frequency_hz'] == 1e6 and stream['source_current_a'] == 1
    assert stream['status'] == 'PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'
    return dict(program='SPD Decap PI Evaluator',version='0.23.1',
                inputs={str(p):h for p,h in PINS.items()},
                qualification={'fmm_nd2':json.loads(RUNTIME.read_text()), 'scalar_split':qualify_scalar()},
                scope='Reuse saved accepted-contact 1MHz L04 conditional R-minimum field; no new reconstruction. No derivative or finite board accuracy claim.')


def scalar_fields(sources, moment, targets, own, label, checkpoint=None):
    """Four serial real channels, with exact source self omission."""
    sources, targets = np.asfortranarray(sources.T), np.asfortranarray(targets.T)
    source_field = np.zeros(moment.shape, complex) if own else None
    target_field = np.zeros((targets.shape[1], 2), complex)
    calls = []
    for component in range(2):
        for part in ('real', 'imag'):
            print(json.dumps(dict(phase='FMM_START',label=label,component=component,part=part,nd=1)),flush=True)
            started = time.perf_counter()
            charge = np.asfortranarray(getattr(moment[:,component],part))
            if own:
                # The pinned Python pg=pgt=1 wrapper redundantly calls source-only first.
                potential, target, ier = native_fmm.lfmm3d_st_c_p(1e-5,sources,charge,targets)
                assert ier == 0 and potential.shape == (len(moment),) and np.isfinite(potential).all()
                source_field[:,component] += (1 if part=='real' else 1j)*potential
                del potential
            else:
                result = fmm3dpy.lfmm3d(eps=1e-5,sources=sources,charges=charge,targets=targets,pg=0,pgt=1,nd=1)
                target, ier = result.pottarg, result.ier
            assert ier == 0 and target.shape == (targets.shape[1],) and np.isfinite(target).all()
            target_field[:,component] += (1 if part=='real' else 1j)*target
            calls.append(dict(label=label,component=component,part=part,nd=1,seconds=time.perf_counter()-started,ier=int(ier)))
            print(json.dumps(calls[-1]),flush=True)
            if checkpoint is not None:
                arrays = dict(target_potential=target_field, completed_channels=np.array(len(calls)))
                if own:
                    arrays['source_potential'] = source_field
                np.savez_compressed(checkpoint,**arrays)
            del target, charge
            if not own:
                del result
    return source_field,target_field,calls


def qualify_scalar():
    rng = np.random.default_rng(20260914)
    sources, targets = rng.random((19,3)), rng.random((11,3))+2
    moment = rng.normal(size=(19,2))+1j*rng.normal(size=(19,2))
    own, cross, _ = scalar_fields(sources,moment,targets,True,'tiny-combined')
    _, separate, _ = scalar_fields(sources,moment,targets,False,'tiny-target')
    dense_own, scale_own = direct_rows(sources,moment,sources,np.arange(len(sources)))
    dense_cross, scale_cross = direct_rows(sources,moment,targets,np.full(len(targets),-1))
    error = max(float(np.max(abs(own-dense_own)/scale_own)),
                float(np.max(abs(cross-dense_cross)/scale_cross)),
                float(np.max(abs(separate-cross)/scale_cross)))
    assert error < 1e-12
    return dict(status='PASS_SCALAR_NATIVE_COMBINED_AND_TARGET_DENSE',maximum_scaled_error=error,
                source_self_omission=True,native_calls=8)


def source_action():
    assert fmm3dpy.__version__ == '2.1.0'
    points_p, moments_p = [], []
    with np.load(SOURCE,allow_pickle=False) as z:
        for layer, key in [('l14','l14_sheet_current_density_a_per_m'),('l25','l25_average_current_a_per_m')]:
            t = z[layer+'_triangle_vertices_um']*1e-6
            e = t[:,1:]-t[:,:1]
            a = abs(e[:,0,0]*e[:,1,1]-e[:,0,1]*e[:,1,0])/2
            points_p.append(np.column_stack((t.mean(axis=1),np.full(len(t),z[layer+'_slab_z_um'].mean()*1e-6))))
            moments_p.append(a[:,None]*z[key])
    pp, mp = np.vstack(points_p), np.vstack(moments_p)
    with np.load(Q04,allow_pickle=False) as f,np.load(SPACE,allow_pickle=False) as s,np.load(MESH,allow_pickle=False) as m:
        q = f['branch_current_a']
        t = m['node_xy_um'][m['triangles'][s['free_triangle_indices']]]*1e-6
        geom = _geometry(t,s['local_facet_branch_index'],s['local_outward_flux_sign'],len(q))
        m04 = _scatter(q,*geom[1:6])
        p04 = geom[-1].T.copy(); p04[:,2] = 165e-6
    del geom,t,e,a,points_p,moments_p
    split = len(mp)
    assert split == 794050 and len(m04) == 1589827
    # Reuse the pinned P-P action; zero-charge L04 points need no place in its source tree.
    with np.load(OLD/'fixed-current-point-action.npz',allow_pickle=False) as old_action:
        pp_field = old_action['potential_source14']+old_action['potential_source25']
    assert pp_field.shape == mp.shape and np.isfinite(pp_field).all()
    _, p04_field, calls = scalar_fields(pp,mp,p04,False,'P',ACTION.with_name('checkpoint-source-P.npz'))
    labelled = [np.vstack((pp_field,p04_field))]
    del pp_field,p04_field
    self04_field, to_p_field, calls04 = scalar_fields(p04,m04,pp,True,'L04',ACTION.with_name('checkpoint-source-L04.npz'))
    labelled.append(np.vstack((to_p_field,self04_field)))
    del self04_field,to_p_field
    calls += calls04
    np.savez_compressed(ACTION,potential_sourceP=labelled[0],potential_source04=labelled[1])
    save(ACTION_RECEIPT,dict(status='RAW_ACTION_SAVED_BEFORE_CHECKS',calls=calls,artifact_sha256=sha(ACTION)))
    point_error = {}
    reference_arrays = {}
    for obs,(obs_points,obs_moment,offset) in enumerate(((pp,mp,0),(p04,m04,split))):
        rows = np.unique(np.r_[np.argsort(np.linalg.norm(obs_moment,axis=1))[-4:],np.linspace(0,len(obs_points)-1,4,dtype=int)])
        for src,(source_points,moment) in enumerate(((pp,mp),(p04,m04))):
            exact, absolute = direct_rows(source_points,moment,obs_points[rows],rows if src==obs else np.full(len(rows),-1))
            actual = labelled[src][rows+offset]
            point_error[f'{obs}_{src}'] = float(np.max(abs(actual-exact)/np.maximum(absolute,np.finfo(float).tiny)))
            reference_arrays[f'exact_{obs}_{src}'] = exact
            reference_arrays[f'actual_{obs}_{src}'] = actual
    np.savez_compressed(ACTION.with_name('direct-row-check.npz'),**reference_arrays)
    b,h = np.zeros((2,2),complex),np.zeros((2,2),complex)
    for src, field in enumerate(labelled):
        for obs,(moment,offset) in enumerate(((mp,0),(m04,split))):
            block = field[offset:offset+len(moment)]
            b[obs,src] = MU0*np.sum(moment*block)
            h[obs,src] = MU0*np.sum(moment.conj()*block)
    with np.load(SELF,allow_pickle=False) as z:
        matrix = sparse.csc_matrix((z['lself_data'],z['lself_indices'],z['lself_indptr']),shape=tuple(z['lself_shape']))
        lq = matrix@q
        b04,h04 = q@lq,np.vdot(q,lq)
    old = json.loads((OLD/'reduction.json').read_text())
    prior_pp = sum(complex(*term['ordinary_jomega_ohm']) for term in old['centroid_terms'].values())/(1j*OMEGA)
    p_local_z = complex(*old['local_l14_same_triangle_ordinary_ohm'])+complex(*old['local_l25_existing_self_plus_near_ordinary_ohm'])
    scale = max(abs(b[0,1]),abs(b[1,0]),abs(h[0,1]),abs(h[1,0]),np.finfo(float).tiny)
    reciprocity = float(abs(b[0,1]-b[1,0])/scale)
    hreciprocity = float(abs(h[0,1]-h[1,0].conjugate())/scale)
    pp_error = float(abs(b[0,0]-prior_pp)/max(abs(prior_pp),np.finfo(float).tiny))
    passed = max(*point_error.values(),reciprocity,hreciprocity,pp_error) < 5e-5
    report = dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='PASS_CONDITIONAL_POWER_L04_MAGNETIC_DIAGNOSTIC' if passed else 'UNRESOLVED_POINT_ACTION_CHECKS',
        calls=calls,power_only_action_reused=True,point_row_relative=point_error,ordinary_reciprocity_relative=reciprocity,
        hermitian_reciprocity_relative=hreciprocity,power_only_replay_relative=pp_error,
        ordinary_centroid_jomega_ohm=[[pair(1j*OMEGA*v) for v in row] for row in b],
        hermitian_centroid_omega_scale_ohm=[[pair(OMEGA*v) for v in row] for row in h],
        ordinary_power_l04_both_directions_ohm=pair(1j*OMEGA*(b[0,1]+b[1,0])),
        l04_thin_sheet_local_self_ordinary_ohm=pair(1j*OMEGA*b04),
        l04_thin_sheet_local_self_hermitian_omega_scale_ohm=pair(OMEGA*h04),
        total_conditional_ordinary_ohm=pair(1j*OMEGA*(b.sum()+b04)+p_local_z),
        source_current_reconstruction_executed=False,board_solve_executed=False,
        limitations=['L04 is fixed-contact conditional R-minimum, not original collapsed-circuit derivative.',
          'Same-layer near and finite-support cross approximations remain; no full-loop or PowerSI accuracy claim.',
          'L04/L25 local self are thin-sheet RT0; L14 local self is finite-depth constant P1.',
          'Contact interiors, vertical transfer, other native conductors and impressed port closure not reconstructed.',
          'No synthetic closing segment or current rebalance; no energy half factor in these quadratic forms.'])
    save(ACTION_RECEIPT,report)
    assert passed, report
