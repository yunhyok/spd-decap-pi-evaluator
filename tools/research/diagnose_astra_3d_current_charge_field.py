"""SPD Decap PI Evaluator v0.23.1: first finite-3D current/charge field diagnostic.

One source-material conducting box in vacuum, illuminated by two plane waves.
The real broken-RT0 space retains volume and face charges. An exact integer
loop plus omega-scaled charge range avoids low-frequency charge cancellation.
This uses a coarse frozen mesh and cannot certify physical/board accuracy.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.linalg import block_diag,lu_factor,lu_solve
import qualify_astra_tetra_retarded_green as magnetic
import qualify_astra_charge_retarded_green as electric

ROOT=magnetic.ROOT
PINS={
    'magnetic':('outputs/research/astra-tetra-retarded-green-02/blocks.npz','c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2'),
    'electric':('outputs/research/astra-charge-retarded-green-02/charge.npz','05c122a8d4d599a7a8371678b7e580fa28d599bc972a3107d8fbf1970890b43c'),
    'static_charge':('outputs/research/astra-tetra-charge-green-01/charge.npz','ed22fe446522687f89f8021fe6d122110bbb4b0282c97377d4a8984d33b02b60'),
}


def mass(tetra):
    volume,_=magnetic.static.faces(tetra)
    delta=tetra.mean(axis=0)-tetra
    variance=np.sum(delta*delta)/20
    return (variance+delta @ delta.T)/(9*volume)


def scaled_solve(matrix,rhs):
    rows=np.max(abs(matrix),axis=1)
    normalized=matrix/rows[:,None]
    columns=np.max(abs(normalized),axis=0)
    normalized/=columns[None,:]
    lu=lu_factor(normalized)
    solution=lu_solve(lu,rhs/rows[:,None])/columns[:,None]
    history=[]
    for iteration in range(4):
        residual=rhs-matrix @ solution
        scale=abs(matrix) @ abs(solution)+abs(rhs)
        history.append(float(np.max(abs(residual)/np.maximum(scale,np.finfo(float).tiny))))
        if history[-1]<1e-14:
            break
        solution+=lu_solve(lu,residual/rows[:,None])/columns[:,None]
    return solution,float(np.linalg.cond(normalized)),history


def quadrature_basis(tetrahedra):
    result=[]
    for tetra in tetrahedra:
        points,weights=magnetic.static.tetra_quadrature(tetra,8)
        volume,_=magnetic.static.faces(tetra)
        basis=(points[:,None,:]-tetra)/(3*volume)
        result.append((points,weights,basis))
    return result


def incident(qdata,k,loop,range_basis,moments):
    phase=np.zeros((24,2),complex)
    for cell,(points,weights,basis) in enumerate(qdata):
        phase[4*cell:4*cell+4]=np.einsum('p,pid,p->id',weights,basis[:,:,:2],np.expm1(-1j*k*points[:,2]))
    full=moments[:,:2]+phase
    # The loop has exactly zero volume moment; do not numerically subtract it.
    return np.vstack((loop.T @ phase,range_basis.T @ full)),full


def far_field(qdata,k,current,exact_moment):
    u,w=leggauss(16)
    phi=2*np.pi*np.arange(32)/32
    directions=np.stack(np.broadcast_arrays(np.sqrt(1-u[:,None]**2)*np.cos(phi),
        np.sqrt(1-u[:,None]**2)*np.sin(phi),u[:,None]),axis=-1).reshape(-1,3)
    weights=np.broadcast_to(w[:,None]*2*np.pi/32,(16,32)).ravel()
    transform=np.broadcast_to(exact_moment,(len(directions),3,current.shape[1])).copy()
    for cell,(points,quadrature,basis) in enumerate(qdata):
        density=np.einsum('pid,ie->pde',basis,current[4*cell:4*cell+4])
        phase=np.expm1(1j*k*(directions @ points.T))
        transform+=np.einsum('np,p,pde->nde',phase,quadrature,density)
    longitudinal=np.einsum('nd,nde->ne',directions,transform)
    transverse=transform-directions[:,:,None]*longitudinal[:,None,:]
    return np.sum(weights[:,None,None]*abs(transverse)**2,axis=(0,1))


def run(output):
    started=monotonic()
    assert not output.exists()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path,expected in PINS.values():
        assert magnetic.static.source.sha(ROOT/path)==expected,path
    with np.load(ROOT/PINS['magnetic'][0],allow_pickle=False) as data:
        tetrahedra=data['tetrahedra_m']
        lstatic=magnetic.flatten(data['static_blocks'])
        ltail=np.array([magnetic.flatten(blocks) for blocks in data['tail_blocks']])
        moments=data['exact_basis_volume_moments'].reshape(24,3)
        frequencies=data['frequencies_hz']
    with np.load(ROOT/PINS['electric'][0],allow_pickle=False) as data:
        pstatic=data['static_matrix_per_m']
        ptail=data['regular_tail_per_m']
        bmat=data['distributional_divergence']
    with np.load(ROOT/PINS['static_charge'][0],allow_pickle=False) as data:
        entities=list(tetrahedra)+list(data['triangles_m'])
    # New symmetric two-orientation Galerkin quadrature, not the frozen raw rule:
    # Q_ab = (outer_a inner_b + outer_b inner_a)/2. Preserve the raw-rule defect
    # as measured quadrature uncertainty; this enforces the real bilinear form,
    # not physical mesh accuracy. No eigenvalues or matrix entries are clipped.
    quadrature_changes={}
    for name,matrix in (('static_magnetic',lstatic),('static_scalar',pstatic)):
        symmetric=(matrix+matrix.T)/2
        quadrature_changes[name]=float(np.linalg.norm(symmetric-matrix)/np.linalg.norm(matrix))
        if name=='static_magnetic':
            lstatic=symmetric
        else:
            pstatic=symmetric
    ltail=(ltail+ltail.transpose(0,2,1))/2
    ptail=(ptail+ptail.transpose(0,2,1))/2
    assert np.linalg.eigvalsh(pstatic).min()>0 and np.linalg.eigvalsh(lstatic).min()>0
    loop=np.rint(magnetic.face_loop(tetrahedra)*np.sqrt(6))[:,None]
    assert np.array_equal(bmat @ loop,np.zeros((24,1)))
    pivot=int(np.flatnonzero(loop[:,0])[0])
    range_basis=np.delete(np.eye(24),pivot,axis=1)
    transform=np.column_stack((loop,range_basis))
    assert np.linalg.matrix_rank(transform)==24 and np.linalg.matrix_rank(bmat @ range_basis)==23
    assert np.linalg.norm(loop.T @ moments)<1e-14*np.linalg.norm(moments)
    brange=bmat @ range_basis
    geom_mass=block_diag(*(mass(tetra) for tetra in tetrahedra))
    qdata=quadrature_basis(tetrahedra)
    rows,properties=magnetic.static.source.source_inputs()
    factor=1/(4*np.pi*magnetic.static.source.EPS0)
    centroids=np.array([entity.mean(axis=0) for entity in entities])
    assert np.linalg.norm(centroids.T @ bmat+moments.T)<1e-13*np.linalg.norm(moments)
    drives=np.array([[1,0,1],[0,1,1j]],complex)
    cases,arrays=[],{}
    for index,frequency in enumerate(frequencies):
        omega=2*np.pi*frequency
        k=omega*np.sqrt(magnetic.static.source.MU0*magnetic.static.source.EPS0)
        _,eps,sigma,gamma=magnetic.static.source.materials(rows,properties,frequency)
        kappa=gamma[0]-1j*omega*magnetic.static.source.EPS0
        # The constant radiation term is applied through exact integrated moments.
        lfull=lstatic+ltail[index]-1j*k*1e-7*(moments @ moments.T)
        zcurrent=geom_mass/kappa+1j*omega*lfull
        pfull=factor*(pstatic+ptail[index])
        projected=transform.T @ zcurrent @ transform
        system=projected.copy()
        system[:,1:]*=omega
        system[1:,1:]+=brange.T @ pfull @ brange/1j
        rhs,full_incident=incident(qdata,k,loop,range_basis,moments)
        solved,condition,backward=scaled_solve(system,rhs)
        coefficients=solved.copy()
        coefficients[1:]*=omega
        current=transform @ coefficients
        density_charge=1j*brange @ solved[1:]
        exact_moment=moments.T @ range_basis @ coefficients[1:]
        reaction=rhs.T @ coefficients
        currents=current @ drives
        charges=density_charge @ drives
        modal=coefficients @ drives
        extinction=np.real(np.sum(np.conj(modal)*(rhs @ drives),axis=0))
        absorption=(1/kappa).real*np.real(np.diag(currents.conj().T @ geom_mass @ currents))
        # Evaluate magnetic/scalar radiation separately from much larger static terms.
        jmom=exact_moment @ drives
        magnetic_radiation=omega*(1e-7*k*np.sum(abs(jmom)**2,axis=0)-np.diag(currents.conj().T @ ltail[index].imag @ currents).real)
        scalar_radiation=omega*factor*np.diag(charges.conj().T @ ptail[index].imag @ charges).real
        radiation=magnetic_radiation+scalar_radiation
        independent=omega*magnetic.static.source.MU0*k/(16*np.pi*np.pi)*far_field(qdata,k,currents,jmom)
        power_error=abs(extinction-absorption-radiation)/(abs(extinction)+abs(absorption)+abs(radiation))
        continuity=bmat @ current+1j*omega*density_charge
        continuity_scale=abs(bmat) @ abs(current)+omega*abs(density_charge)
        # A coarse unrestricted real space measures, rather than assumes, bulk charge.
        divergence=current.reshape(6,4,2).sum(axis=1)
        bulk_ratio=float(np.linalg.norm(divergence)/np.linalg.norm(current))
        charge_dipole=centroids.T @ density_charge
        dipole_relative=float(np.linalg.norm(charge_dipole-exact_moment/(1j*omega))/np.linalg.norm(charge_dipole))
        case=dict(frequency_hz=float(frequency),scaled_condition=condition,backward_history=backward,
            current_charge_continuity_relative=float(np.max(abs(continuity)/np.maximum(continuity_scale,np.finfo(float).tiny))),
            dipole_identity_relative=dipole_relative,
            reciprocal_incident_reaction_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)),
            extinction_w=extinction.tolist(),absorption_w=absorption.tolist(),radiation_w=radiation.tolist(),
            independent_transverse_radiation_w=independent.tolist(),
            radiation_relative_error=float(np.max(abs(radiation-independent)/independent)),
            extinction_power_relative_error=float(max(power_error)),
            bulk_divergence_flux_ratio=bulk_ratio,
            induced_dipole_norm_c_m=float(np.linalg.norm(charge_dipole)),
            current_coefficient_norm_a=float(np.linalg.norm(current)))
        cases.append(case)
        for key,value in dict(current=current,charge=density_charge,scaled_solution=solved,reaction=reaction,
            charge_dipole=charge_dipole,exact_current_volume_moment=exact_moment).items():
            arrays[f'case_{index:02d}_{key}']=value
        print(json.dumps(case),flush=True)
    gates=dict(equations=all(c['backward_history'][-1]<1e-12 for c in cases),
        continuity=all(c['current_charge_continuity_relative']<1e-12 for c in cases),
        dipole=all(c['dipole_identity_relative']<1e-10 for c in cases),
        reciprocity=all(c['reciprocal_incident_reaction_relative']<1e-5 for c in cases),
        positive_loss=all(min(c['absorption_w'])>0 and min(c['radiation_w'])>0 for c in cases),
        independent_radiation=all(c['radiation_relative_error']<1e-5 for c in cases),
        power=all(c['extinction_power_relative_error']<1e-5 for c in cases))
    arrays.update(loop=loop,range_basis=range_basis,geometric_mass=geom_mass)
    with (output/'fields.npz').open('xb') as stream:
        np.savez_compressed(stream,**arrays)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='COMPLETE_3D_CURRENT_CHARGE_FIELD_DIAGNOSTIC' if all(gates.values()) else 'STOP_3D_CURRENT_CHARGE_FIELD_DIAGNOSTIC',
        gates=gates,script_sha256=magnetic.static.source.sha(Path(__file__)),fields_sha256=magnetic.static.source.sha(output/'fields.npz'),
        pins=PINS,source_pins=magnetic.static.source.PINS,quadrature_rule='symmetric two-orientation average of exact-inner/positive-outer quadrature',
        quadrature_relative_changes=quadrature_changes,cases=cases,elapsed_s=monotonic()-started,
        scope='Finite controlled100x100x25um copper body in vacuum, source TOP sigma and retained normalized epsilon. Six tetrahedra,24real broken-RT0 contrast-current unknowns and24volume/face charge shapes with23independent neutral charges. Two incident transverse plane waves and [1,i]. Full retarded volume/charge kernels, exact integer-loop and omega-scaled range; no added internal/gap inductance. This is the first coupled3D field diagnostic, not a qualified physical response: no mesh-refinement, skin/bulk/normal-continuity convergence, material interface, actual trace/pad/via body or source port, board or PowerSI accuracy is claimed. Power quantities use peak phasors with the common half factor omitted.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2,allow_nan=False)
        stream.write('\n')
    print(json.dumps({key:result[key] for key in ('status','gates','elapsed_s')}),flush=True)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
