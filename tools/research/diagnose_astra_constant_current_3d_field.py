"""SPD Decap PI Evaluator v0.23.1: homogeneous finite3D constant-current field.

The real solenoidal RT0 space is unchanged. Evaluate its cellwise constant
magnetic current with scalar volume kernels and its surface charge with scalar
boundary kernels. First reproduce the frozen48-tetra field, then compare384.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
from scipy.linalg import qr
import diagnose_astra_refined_3d_field as reference

ROOT=reference.ROOT
OLD_FIELDS=ROOT/'outputs/research/astra-homogeneous-refined-3d-field-01/fields.npz'
PINS={**reference.SUPPORT_PINS,
    'tools/research/diagnose_astra_refined_3d_field.py':'c5a915cf8ba3deee830e6bf4b989eecc3408b1251e8fd46d9a49db5a7ac5225a',
    'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02',
    'outputs/research/astra-homogeneous-refined-3d-field-01/fields.npz':'b01a32fbdb82af9285f1e9205d4f8d5dced506ec67fb22227c731ee7945a6e9a',
    'outputs/research/astra-homogeneous-refined-3d-field-01/result.json':'f0bb57c49dd51d5f26d1c1ca5701bc937a95055dbfbeefd70d911fc54fb28388',
}
source=reference.coarse.magnetic.static.source
static=reference.coarse.magnetic.static


def frame(tet,triangles):
    tri,owners,incidence,lift,cycles=reference.topology.current_topology(tet)
    assert len(tri)==len(triangles) and all(sorted(map(tuple,a))==sorted(map(tuple,b)) for a,b in zip(tri,triangles))
    boundary=np.array([i for i,pair in enumerate(owners) if len(pair)==1])
    internal=np.setdiff1d(np.arange(len(tri)),boundary)
    loop,_=reference.integer_loop_range(incidence[:,internal])
    face_loop=np.zeros((len(tri),loop.shape[1]),int);face_loop[internal]=loop
    face_ids=np.argmax(abs(lift),axis=1);signs=lift[np.arange(len(lift)),face_ids]
    c=signs[:,None]*face_loop[face_ids]
    physical=signs[:,None]*cycles[face_ids]
    boundary_rows=np.array([4*owners[i][0][0]+owners[i][0][1] for i in boundary])
    _,r,pivots=qr(-physical[boundary_rows],pivoting=True,mode='economic')
    rank=int(np.count_nonzero(abs(r.diagonal())>1e-12))
    assert rank==len(boundary)-1
    d=physical[:,pivots[:rank]];transform=np.column_stack((c,d))
    assert np.linalg.matrix_rank(transform)==transform.shape[1]==len(tri)-len(tet)
    assert np.array_equal(transform.reshape(len(tet),4,-1).sum(axis=1),np.zeros((len(tet),transform.shape[1])))
    assert np.count_nonzero(c[boundary_rows])==0
    q=-d[boundary_rows]
    assert np.count_nonzero(q.sum(axis=0))==0
    moments=np.array([(x.mean(axis=0)-x)/3 for x in tet])
    h=np.einsum('tid,tin->tdn',moments,transform.reshape(len(tet),4,-1))
    return boundary,transform,q,h,c.shape[1]


def qbasis(tet,order):
    result=[]
    for t in tet:
        points,weights=static.tetra_quadrature(t,order)
        volume,_=static.faces(t)
        result.append((points,weights,(points[:,None,:]-t)/(3*volume)))
    return result


def project(h,matrix):
    return sum(h[:,axis,:].T @ matrix @ h[:,axis,:] for axis in range(3))


def run(bundle,expected,receipt,receipt_sha,output):
    started=monotonic()
    assert not output.exists() and source.sha(bundle)==expected and source.sha(receipt)==receipt_sha
    for path,pin in PINS.items():
        assert source.sha(ROOT/path)==pin,path
    kernel_result=json.loads(receipt.read_bytes())
    assert kernel_result['kernels_sha256']==expected and kernel_result['status'].startswith(('PASS_','COMPLETE_'))
    with np.load(bundle,allow_pickle=False) as data:
        tet,tri,frequencies=data['tetrahedra_m'],data['triangles_m'],data['frequencies_hz']
        boundary,transform,q,h,split=frame(tet,tri)
        cells=len(tet)
        assert (cells,transform.shape[1],split,q.shape[1]) in ((48,72,25,47),(384,480,289,191))
        if 'static_volume_per_m' in data:
            assert np.array_equal(boundary,data['boundary_face_indices'])
            kv,ks=data['static_volume_per_m'],data['static_boundary_per_m']
            tv,ts=data['volume_tail_per_m'],data['boundary_tail_per_m']
        else:
            indices=cells+boundary
            kv,ks=data['static_scalar_per_m'][:cells,:cells],data['static_scalar_per_m'][np.ix_(indices,indices)]
            tv=data['scalar_tail_per_m'][:,:cells,:cells]
            ts=data['scalar_tail_per_m'][:,indices[:,None],indices]
    assert np.array_equal(frequencies,source.FREQUENCIES)
    assert kv.shape==(cells,cells) and ks.shape==(len(boundary),len(boundary))
    for matrix in (kv,ks,*tv,*ts):
        assert np.all(np.isfinite(matrix)) and np.linalg.norm(matrix-matrix.T)<1e-13*np.linalg.norm(matrix)
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    volumes=np.array([static.faces(x)[0] for x in tet])
    gram=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
    magnetic=1e-7*project(h,kv)
    centroids=tri[boundary].mean(axis=1)
    total=np.column_stack((np.zeros((3,split)),-centroids.T @ q))
    assert np.linalg.norm(h.sum(axis=0)-total)<1e-12*np.linalg.norm(h)
    constant_magnetic=1e-7*total.T @ total
    scalar=q.T @ ks @ q/(4*np.pi*source.EPS0)
    qdata=qbasis(tet,4)
    rows,properties=source.source_inputs()
    drives=np.array([[1,0,1],[0,1,1j]],complex)
    old_result=json.loads(OLD_FIELDS.with_name('result.json').read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as data:
        old_tet=data['tetrahedra_m']
    arrays=dict(current_transform=transform,boundary_divergence_range=q,cell_integrated_current_map=h)
    cases=[]
    with np.load(OLD_FIELDS,allow_pickle=False) as old:
        for index,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(source.MU0*source.EPS0)
            _,_,_,gamma=source.materials(rows,properties,frequency)
            kappa=gamma[0]-1j*omega*source.EPS0
            magnetic_tail=1e-7*project(h,tv[index])
            scalar_tail=q.T @ ts[index] @ q/(4*np.pi*source.EPS0)
            z=gram/kappa+1j*omega*(magnetic+magnetic_tail-1j*k*constant_magnetic)
            system=z.copy();system[:,split:]*=omega
            system[split:,split:]+=(scalar+scalar_tail)/1j
            mean_phase=np.array([np.dot(weights,np.expm1(-1j*k*points[:,2]))/volume for (points,weights,_),volume in zip(qdata,volumes)])
            rhs=total[:2].T+np.einsum('tdn,t->nd',h[:,:2],mean_phase)
            solved,condition,backward=reference.coarse.scaled_solve(system,rhs)
            modal=solved.copy();modal[split:]*=omega
            current=transform @ modal
            charge=1j*q @ solved[split:]
            exact_moment=total @ modal
            u=modal @ drives;rho=charge @ drives;j=current @ drives;jmoment=exact_moment @ drives
            extinction=np.real(np.sum(u.conj()*(rhs @ drives),axis=0))
            absorption=(1/kappa).real*np.diag(u.conj().T @ gram @ u).real
            radiation=omega*(1e-7*k*np.sum(abs(jmoment)**2,axis=0)-np.diag(u.conj().T @ magnetic_tail.imag @ u).real
                +np.diag(rho.conj().T @ ts[index].imag @ rho).real/(4*np.pi*source.EPS0))
            independent=omega*source.MU0*k/(16*np.pi*np.pi)*reference.coarse.far_field(qdata,k,j,jmoment)
            reaction=rhs.T @ modal
            power=float(np.max(abs(extinction-absorption-radiation)/(abs(extinction)+abs(absorption)+abs(radiation))))
            case=dict(frequency_hz=float(frequency),condition=condition,backward=backward,
                reciprocal_reaction_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)),
                extinction_w=extinction.tolist(),absorption_w=absorption.tolist(),radiation_w=radiation.tolist(),independent_radiation_w=independent.tolist(),
                power_relative=power,radiation_relative=float(np.max(abs(radiation-independent)/independent)),
                field_l2_change_from_48_tetra=reference.field_change(qdata,current,old_tet,old[f'case_{index:02d}_current']),
                absorption_relative_change=float(np.max(abs(absorption-np.array(old_result['cases'][index]['absorption_w']))/abs(absorption))),
                dipole_relative_change=float(np.linalg.norm(centroids.T @ charge-old[f'case_{index:02d}_charge_dipole'])/np.linalg.norm(centroids.T @ charge)))
            if index==len(frequencies)-1:
                high=omega*source.MU0*k/(16*np.pi*np.pi)*reference.coarse.far_field(qbasis(tet,8),k,j,jmoment)
                case['radiation_q4_q8_relative']=float(np.max(abs(high-independent)/high))
            cases.append(case);print(json.dumps(case),flush=True)
            for key,value in dict(current=current,boundary_charge=charge,scaled_solution=solved,incident_rhs=rhs,reaction=reaction,current_volume_moment=exact_moment,charge_dipole=centroids.T @ charge).items():
                arrays[f'case_{index:02d}_{key}']=value
    gates=dict(equations=all(c['backward'][-1]<1e-12 for c in cases),reciprocity=all(c['reciprocal_reaction_relative']<1e-10 for c in cases),
        power=all(c['power_relative']<1e-5 for c in cases),radiation=all(c['radiation_relative']<1e-5 for c in cases),
        radiation_quadrature=cases[-1]['radiation_q4_q8_relative']<1e-10,positive_loss=all(min(c['absorption_w'])>0 and min(c['radiation_w'])>0 for c in cases))
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('COMPLETE_' if all(gates.values()) else 'STOP_')+'CONSTANT_CURRENT_3D_FIELD_DIAGNOSTIC',
        gates=gates,pins=PINS,kernel_sha256=expected,kernel_receipt_sha256=receipt_sha,script_sha256=source.sha(Path(__file__)),fields_sha256=source.sha(output/'fields.npz'),
        tetrahedra=cells,solve_unknowns=transform.shape[1],loops=split,surface_charge_ranges=q.shape[1],cases=cases,elapsed_s=monotonic()-started,
        scope='Real homogeneous solenoidal RT0 currents evaluated as exact cellwise constants. Full finite conductivity, degree8 retarded volume/boundary kernels and separately exact constant radiation term retained. Compared to saved48-tetra homogeneous field;48 case tests scalar projection,384 case tests a further mesh. Algebraic success is not mesh convergence, source-solid, material-interface, terminal, board or PowerSI acceptance.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps({k:result[k] for k in ('status','gates','elapsed_s')}),flush=True)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernels',type=Path,required=True);parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--kernel-receipt',type=Path,required=True);parser.add_argument('--receipt-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args()
    raise SystemExit(run(a.kernels.resolve(),a.expected_sha256,a.kernel_receipt.resolve(),a.receipt_sha256,a.output.resolve()))
