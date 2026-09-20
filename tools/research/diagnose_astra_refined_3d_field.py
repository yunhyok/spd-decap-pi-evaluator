"""SPD Decap PI Evaluator v0.23.1: uniformly refined finite-body field diagnostic.

Consume a separately pinned kernel bundle. Compare its field with the frozen
six-tetra response; small equation residuals do not substitute for convergence.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree,shortest_path
from scipy.linalg import qr
import diagnose_astra_3d_current_charge_field as coarse
import qualify_astra_total_current_material_basis as topology

ROOT=coarse.ROOT
COARSE_FIELDS=ROOT/'outputs/research/astra-3d-current-charge-field-02/fields.npz'
COARSE_SHA='3625526b832b76fcfa32727d33bcb0d52a179e56728801ee4ace26d68f26ab2d'
SUPPORT_PINS={
    'tools/research/diagnose_astra_3d_current_charge_field.py':'4a6e46e9dc674b872ea205e0921a0673b29f8d4f2c91fd0d8f2a493b13baef10',
    'tools/research/qualify_astra_tetra_volume_green.py':'24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    'tools/research/qualify_astra_joint_tm_boundary.py':'10032e17838f0f6f0105051424679c29de3ef83acc63cdc9d8075523368ed247',
    'tools/research/qualify_astra_total_current_material_basis.py':'0d07656e676b24967ae0bb9a955af5d0b616ba99b570118b4584ba5dab170327',
    'tools/research/qualify_astra_tetra_retarded_green.py':'a575dce0fe2d0f87a3b041209d18055f061f04c6f9510f8b0a5cca0b609bf12d',
    'tools/research/qualify_astra_charge_retarded_green.py':'cbe0f2b0e6a5b414b8fa481119db228a1e82dcd05cd70dbf0bed84e13c39a1c9',
    'tools/research/qualify_astra_tetra_charge_green.py':'aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9',
    'tools/research/probe_astra_triangle_static_potential.py':'75f55868574d16236b5e6dc14370c3fefffda2d3c171707f330eda1af8561b1e',
    'outputs/research/astra-3d-current-charge-field-02/result.json':'484d6f46b6ea93e261e8b81d0958ecdc949fdf38c4658815604d0e13e051dbfb',
    'outputs/research/astra-tetra-retarded-green-02/blocks.npz':'c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2',
}


def integer_loop_range(divergence):
    count,size=divergence.shape
    assert np.all(np.sum(divergence==1,axis=0)==1) and np.all(np.sum(divergence==-1,axis=0)==1)
    a,b=np.argmax(divergence,axis=0),np.argmin(divergence,axis=0)
    lookup={(int(x),int(y)):edge for edge,(x,y) in enumerate(zip(a,b))}
    adjacency=csr_matrix((np.tile(np.arange(1,size+1),2),(np.r_[a,b],np.r_[b,a])),shape=(count,count))
    tree=minimum_spanning_tree(adjacency)
    tree_ids=np.sort(tree.data.astype(int)-1)
    assert len(tree_ids)==count-1
    _,predecessor=shortest_path(tree+tree.T,directed=False,unweighted=True,return_predecessors=True)
    chords=np.setdiff1d(np.arange(size),tree_ids)
    loops=np.zeros((size,len(chords)),int)
    for column,edge in enumerate(chords):
        loops[edge,column]=1
        node=int(a[edge]); origin=int(b[edge])
        while node!=origin:
            previous=int(predecessor[origin,node])
            assert previous>=0
            if (previous,node) in lookup:
                loops[lookup[(previous,node)],column]=1
            else:
                loops[lookup[(node,previous)],column]=-1
            node=previous
    ranges=np.eye(size)[:,tree_ids]
    assert np.array_equal(divergence @ loops,np.zeros((count,len(chords))))
    assert np.linalg.matrix_rank(np.column_stack((loops,ranges)))==size
    return loops,ranges


def incident(qdata,k,loops,ranges,moments):
    remainder=np.zeros((len(moments),2),complex)
    for cell,(points,weights,basis) in enumerate(qdata):
        remainder[4*cell:4*cell+4]=np.einsum('p,pid,p->id',weights,basis[:,:,:2],np.expm1(-1j*k*points[:,2]))
    return np.vstack((loops.T @ remainder,ranges.T @ (moments[:,:2]+remainder)))


def homogeneous_charge_range(tetrahedra,bmat,loops):
    """Uniform gamma, no interior impressed source: divJ=0 and internal Jn continuous.

    This real subspace retains the full retarded operator, finite conductivity
    and arbitrary neutral boundary charge. It is not valid across unequal media.
    """
    _,_,_,lift,cycles=topology.current_topology(tetrahedra)
    physical=lift @ cycles
    _,triangular,pivots=qr(bmat @ physical,pivoting=True,mode='economic')
    boundary=np.flatnonzero(np.count_nonzero(bmat,axis=1)==1)
    rank=int(np.count_nonzero(abs(triangular.diagonal())>1e-12))
    assert rank==len(boundary)-1
    ranges=physical[:,pivots[:rank]]  # QR selects columns; their integer values stay exact.
    transform=np.column_stack((loops,ranges))
    interior=np.setdiff1d(np.arange(len(bmat)),boundary)
    assert np.array_equal(bmat[interior] @ transform,np.zeros((len(interior),transform.shape[1])))
    assert np.linalg.matrix_rank(transform)==transform.shape[1]==physical.shape[1]
    return ranges


def field_change(qdata,current,coarse_tetrahedra,old_current):
    difference=norm=0.
    for cell,(points,weights,basis) in enumerate(qdata):
        new=np.einsum('pid,ie->pde',basis,current[4*cell:4*cell+4])
        old=np.zeros_like(new)
        assigned=np.zeros(len(points),bool)
        for index,tetra in enumerate(coarse_tetrahedra):
            bary=(points-tetra[0]) @ np.linalg.inv((tetra[1:]-tetra[0]).T).T
            inside=(bary.min(axis=1)>=-1e-12)&(bary.sum(axis=1)<=1+1e-12)&~assigned
            volume,_=coarse.magnetic.static.faces(tetra)
            values=(points[inside,None,:]-tetra)/(3*volume)
            old[inside]=np.einsum('pid,ie->pde',values,old_current[4*index:4*index+4])
            assigned|=inside
        assert assigned.all()
        difference+=np.sum(weights[:,None,None]*abs(new-old)**2)
        norm+=np.sum(weights[:,None,None]*abs(new)**2)
    return float(np.sqrt(difference/norm))


def physical_continuity(tetrahedra,triangles,bmat,current,gram):
    cells=len(tetrahedra)
    volumes=np.array([coarse.magnetic.static.faces(t)[0] for t in tetrahedra])
    volume_divergence=(bmat[:cells] @ current)/volumes[:,None]
    jump=0.
    for index,triangle in enumerate(triangles):
        row=bmat[cells+index]
        if np.count_nonzero(row)==2:
            area=np.linalg.norm(np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0]))/2
            jump+=np.sum(abs(row @ current)**2)/area
    norm=float(np.trace(current.conj().T @ gram @ current).real)
    length=float(np.linalg.norm(np.ptp(tetrahedra.reshape(-1,3),axis=0)))
    return dict(bulk_divergence_fixed_body_scale=float(length*np.sqrt(np.sum(volumes[:,None]*abs(volume_divergence)**2)/norm)),
        internal_normal_jump_fixed_body_scale=float(np.sqrt(length*jump/norm)))


def run(bundle,expected,receipt_path,receipt_sha,output,homogeneous=False):
    started=monotonic()
    assert not output.exists() and coarse.magnetic.static.source.sha(bundle)==expected
    assert coarse.magnetic.static.source.sha(COARSE_FIELDS)==COARSE_SHA
    for path,pin in SUPPORT_PINS.items():
        assert coarse.magnetic.static.source.sha(ROOT/path)==pin,path
    assert coarse.magnetic.static.source.sha(receipt_path)==receipt_sha
    kernel_receipt=json.loads(receipt_path.read_bytes())
    assert kernel_receipt['kernels_sha256']==expected
    assert kernel_receipt['status'].startswith(('PASS_','COMPLETE_'))
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(bundle,allow_pickle=False) as data:
        required=('tetrahedra_m','triangles_m','distributional_divergence','geometric_mass','exact_basis_volume_moments',
            'static_magnetic_h','magnetic_tail_h','static_scalar_per_m','scalar_tail_per_m','frequencies_hz')
        tetrahedra,triangles,bmat,gram,moments,lstatic,ltails,pstatic,ptails,frequencies=(data[key] for key in required)
    size=len(tetrahedra)*4
    assert len(tetrahedra)==48 and len(triangles)==120 and bmat.shape==(168,192)
    assert gram.shape==lstatic.shape==(size,size) and pstatic.shape==(len(bmat),len(bmat))
    assert ltails.shape==(6,size,size) and ptails.shape==(6,len(bmat),len(bmat))
    assert tetrahedra.shape==(48,4,3) and triangles.shape==(120,3,3) and moments.shape==(size,3)
    assert all(np.all(np.isfinite(array)) for array in (tetrahedra,triangles,bmat,gram,moments,lstatic,ltails,pstatic,ptails))
    assert np.array_equal(frequencies,coarse.magnetic.static.source.FREQUENCIES)
    for matrix in (lstatic,pstatic,*ltails,*ptails):
        assert np.linalg.norm(matrix-matrix.T)<1e-13*np.linalg.norm(matrix)
    loops,ranges=integer_loop_range(bmat)
    if homogeneous:
        ranges=homogeneous_charge_range(tetrahedra,bmat,loops)
        assert ranges.shape==(192,47)
    split=loops.shape[1]
    assert split==25
    transform=np.column_stack((loops,ranges))
    brange=bmat @ ranges
    centroids=np.array([entity.mean(axis=0) for entity in list(tetrahedra)+list(triangles)])
    assert np.linalg.norm(centroids.T @ bmat+moments.T)<1e-12*np.linalg.norm(moments)
    qdata=coarse.quadrature_basis(tetrahedra)
    maximum_edge=float(np.max(np.linalg.norm(tetrahedra[:,:,None,:]-tetrahedra[:,None,:,:],axis=-1)))
    thicknesses=np.ptp(tetrahedra[:,:,2],axis=1)
    with np.load(ROOT/coarse.PINS['magnetic'][0],allow_pickle=False) as data:
        coarse_tetrahedra=data['tetrahedra_m']
    old=json.loads((COARSE_FIELDS.parent/'result.json').read_bytes())
    rows,properties=coarse.magnetic.static.source.source_inputs()
    drives=np.array([[1,0,1],[0,1,1j]],complex)
    factor=1/(4*np.pi*coarse.magnetic.static.source.EPS0)
    cases,arrays=[],{}
    with np.load(COARSE_FIELDS,allow_pickle=False) as old_fields:
        for index,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency
            k=omega*np.sqrt(coarse.magnetic.static.source.MU0*coarse.magnetic.static.source.EPS0)
            _,eps,sigma,gamma=coarse.magnetic.static.source.materials(rows,properties,frequency)
            kappa=gamma[0]-1j*omega*coarse.magnetic.static.source.EPS0
            decay_length=1/np.sqrt(1j*omega*coarse.magnetic.static.source.MU0*gamma[0]).real
            lfull=lstatic+ltails[index]-1j*k*1e-7*(moments @ moments.T)
            zcurrent=gram/kappa+1j*omega*lfull
            system=transform.T @ zcurrent @ transform
            system[:,split:]*=omega
            system[split:,split:]+=brange.T @ (factor*(pstatic+ptails[index])) @ brange/1j
            rhs=incident(qdata,k,loops,ranges,moments)
            solved,condition,backward=coarse.scaled_solve(system,rhs)
            modal=solved.copy(); modal[split:]*=omega
            current=transform @ modal
            charges=1j*brange @ solved[split:]
            exact_moment=-centroids.T @ brange @ modal[split:]
            reaction=rhs.T @ modal
            j,q,u=current @ drives,charges @ drives,modal @ drives
            extinction=np.real(np.sum(u.conj()*(rhs @ drives),axis=0))
            absorption=(1/kappa).real*np.diag(j.conj().T @ gram @ j).real
            jmom=exact_moment @ drives
            radiation=omega*(1e-7*k*np.sum(abs(jmom)**2,axis=0)-np.diag(j.conj().T @ ltails[index].imag @ j).real
                +factor*np.diag(q.conj().T @ ptails[index].imag @ q).real)
            independent=omega*coarse.magnetic.static.source.MU0*k/(16*np.pi*np.pi)*coarse.far_field(qdata,k,j,jmom)
            power=float(np.max(abs(extinction-absorption-radiation)/(abs(extinction)+abs(absorption)+abs(radiation))))
            previous=old['cases'][index]
            continuity=bmat @ current+1j*omega*charges
            scale=abs(bmat) @ abs(current)+omega*abs(charges)
            case=dict(frequency_hz=float(frequency),scaled_condition=condition,backward_history=backward,
                material_decay_length_m=float(decay_length),maximum_tetra_edge_over_decay_length=float(maximum_edge/decay_length),
                tetra_z_extent_over_decay_length=[float(thicknesses.min()/decay_length),float(thicknesses.max()/decay_length)],
                continuity_relative=float(np.max(abs(continuity)/np.maximum(scale,np.finfo(float).tiny))),
                reciprocal_reaction_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)),
                extinction_w=extinction.tolist(),absorption_w=absorption.tolist(),radiation_w=radiation.tolist(),
                independent_radiation_w=independent.tolist(),radiation_relative_error=float(np.max(abs(radiation-independent)/independent)),
                power_relative_error=power,field_l2_change_from_six_tetra=field_change(qdata,current,coarse_tetrahedra,old_fields[f'case_{index:02d}_current']),
                absorption_relative_change=float(np.max(abs(absorption-np.array(previous['absorption_w']))/abs(absorption))),
                dipole_relative_change=float(np.linalg.norm(centroids.T @ charges-old_fields[f'case_{index:02d}_charge_dipole'])/np.linalg.norm(centroids.T @ charges)),
                **physical_continuity(tetrahedra,triangles,bmat,current,gram))
            cases.append(case)
            for key,value in dict(current=current,charge=charges,scaled_solution=solved,incident_rhs=rhs,reaction=reaction,
                current_volume_moment=exact_moment,charge_dipole=centroids.T @ charges).items():
                arrays[f'case_{index:02d}_{key}']=value
            print(json.dumps(case),flush=True)
    gates=dict(equations=all(c['backward_history'][-1]<1e-12 for c in cases),continuity=all(c['continuity_relative']<1e-12 for c in cases),
        reciprocal_reaction=all(c['reciprocal_reaction_relative']<1e-10 for c in cases),
        independent_radiation=all(c['radiation_relative_error']<1e-5 for c in cases),power=all(c['power_relative_error']<1e-5 for c in cases),
        positive_loss=all(min(c['absorption_w'])>0 and min(c['radiation_w'])>0 for c in cases))
    arrays.update(loop_basis=loops,range_basis=ranges)
    with (output/'fields.npz').open('xb') as stream:
        np.savez_compressed(stream,**arrays)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status=('COMPLETE_' if all(gates.values()) else 'STOP_')+('HOMOGENEOUS_' if homogeneous else '')+'REFINED_3D_FIELD_DIAGNOSTIC',
        gates=gates,kernel_path=str(bundle),kernel_sha256=expected,kernel_receipt_sha256=receipt_sha,
        support_pins=SUPPORT_PINS,coarse_fields_sha256=COARSE_SHA,
        script_sha256=coarse.magnetic.static.source.sha(Path(__file__)),fields_sha256=coarse.magnetic.static.source.sha(output/'fields.npz'),
        homogeneous_current_constraint=homogeneous,
        mesh={'tetrahedra':len(tetrahedra),'currents':size,'charge_entities':len(bmat),'loops':split,'charge_range':ranges.shape[1],'solve_unknowns':transform.shape[1]},
        cases=cases,elapsed_s=monotonic()-started,
        scope='Uniform2x2x2 subdivision of the same finite source-copper material control box. Real RT0 currents, normalized volume/face charges, integer graph cycles and omega-scaled charge range. Compared with frozen unrestricted six-tetra field without rerunning it. '+('Uniform material current subspace imposes zero bulk divergence and internal normal continuity, retaining finite conductivity, full retardation and neutral boundary charges; the comparison changes both mesh and current subspace. ' if homogeneous else 'Unrestricted broken currents retain volume and internal face charges. ')+'A field/absorption difference is reported, not a converged solution or full-board accuracy claim. No material-interface, real source solid, terminal or PowerSI response is qualified.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2,allow_nan=False); stream.write('\n')
    print(json.dumps({key:result[key] for key in ('status','gates','elapsed_s')}),flush=True)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernels',type=Path);parser.add_argument('--expected-sha256');parser.add_argument('--output',type=Path)
    parser.add_argument('--kernel-receipt',type=Path);parser.add_argument('--receipt-sha256')
    parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--homogeneous-current',action='store_true')
    args=parser.parse_args()
    if args.self_check:
        b=np.array([[1,0,-1],[-1,1,0],[0,-1,1]])
        c,d=integer_loop_range(b)
        assert c.shape==(3,1) and d.shape==(3,2)
        from qualify_astra_tetra_charge_green import charge_entities
        tetrahedra=coarse.magnetic.static.box_tetrahedra([100e-6,100e-6,25e-6])
        _,bmat=charge_entities(tetrahedra)
        loops,_=integer_loop_range(bmat)
        ranges=homogeneous_charge_range(tetrahedra,bmat,loops)
        assert ranges.shape==(24,11)
        print('PASS_INTEGER_LOOP_RANGE_SELF_CHECK')
    else:
        assert args.kernels and args.output and args.expected_sha256 and args.kernel_receipt and args.receipt_sha256
        raise SystemExit(run(args.kernels.resolve(),args.expected_sha256,args.kernel_receipt.resolve(),args.receipt_sha256,args.output.resolve(),args.homogeneous_current))
