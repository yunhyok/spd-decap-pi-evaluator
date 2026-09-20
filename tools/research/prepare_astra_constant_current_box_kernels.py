"""SPD Decap PI Evaluator v0.23.1: third-mesh homogeneous-current kernels.

Keep finite conductivity and the full retarded Green function. A divergence-free
RT0 current is constant within each tetrahedron, so its magnetic form uses the
scalar volume kernel; its charge form uses the body's boundary triangles.
This is a controlled box, not a source solid or a board accuracy certificate.
"""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
import prepare_astra_refined_3d_box_kernels as prior
import qualify_astra_total_current_material_basis as topology

ROOT=prior.ROOT
OLD=ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz'
PINS={
    'tools/research/prepare_astra_refined_3d_box_kernels.py':'67145b502bd924b514b6d3173701ca11521f20a2ef8d845a17e9454c5c6a664d',
    'tools/research/qualify_astra_total_current_material_basis.py':'0d07656e676b24967ae0bb9a955af5d0b616ba99b570118b4584ba5dab170327',
    'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02',
    'outputs/research/astra-refined-3d-box-kernels-01/result.json':'88fdb9c9c11c3f836fad94f022638bdd841ae7f20c0d3e0601161f0ccbee1745',
    'outputs/research/astra-refined-3d-box-kernels-review-01/independent-review.json':'49a7c99768cb9befb988c1cfcf35acafad44e6be4d7c79b82d674583195aa77c',
}


def pair_key(a,b):
    """Translation/pair exchange only: preserve the ordered Duffy quadrature rule."""
    a=np.asarray(a,int);b=np.asarray(b,int)
    ab=(tuple((a-a[0]).ravel()),tuple((b-a[0]).ravel()))
    ba=(tuple((b-b[0]).ravel()),tuple((a-b[0]).ravel()))
    return min(ab,ba)


def grid_geometry(dimensions):
    unit=np.asarray(prior.static.box_tetrahedra(np.ones(3)),int)
    grid=np.array([t+[i,j,k] for i in range(4) for j in range(4) for k in range(4) for t in unit])
    tetrahedra=grid*(dimensions/4)
    triangles,owners,incidence,lift,cycles=topology.current_topology(tetrahedra)
    boundary=np.array([i for i,pair in enumerate(owners) if len(pair)==1])
    assert grid.shape==(384,4,3) and triangles.shape==(864,3,3) and len(boundary)==192
    surface_grid=np.rint(triangles[boundary]/(dimensions/4)).astype(int)
    assert np.max(abs(triangles[boundary]-surface_grid*(dimensions/4)))<1e-18
    return grid,tetrahedra,triangles,boundary,surface_grid,incidence,lift,cycles,owners


def old_cache(data,dimensions):
    entities=list(data['tetrahedra_m'])+list(data['triangles_m'])
    grid=[np.rint(x/(dimensions/2)).astype(int) for x in entities]
    matrix=data['static_scalar_per_m'];moments=data['scalar_distance_moments']
    raw=data['raw_static_scalar_per_m']
    cache={};duplicate_error=0.
    for indexes in (range(48),range(48,len(entities))):
        for a in indexes:
            for b in range(a,indexes.stop):
                key=pair_key(grid[a],grid[b])
                value=(2*matrix[a,b],moments[:,a,b]*(.5**np.arange(1,8)),
                       abs(raw[a,b]-raw[b,a])/abs(matrix[a,b]))
                if key in cache:
                    duplicate_error=max(duplicate_error,abs(value[0]/cache[key][0]-1),prior.relative(value[1],cache[key][1]))
                else:
                    cache[key]=value
    assert duplicate_error<1e-10
    return cache,duplicate_error


def paired_static(a,b,qa,qb):
    forward=qa[1] @ prior.exact_inner_average(b,qa[0])
    reverse=qb[1] @ prior.exact_inner_average(a,qb[0])
    value=(forward+reverse)/2
    return value,float(abs(forward-reverse)/abs(value))


def assemble(entities,grid,cache,length,deadline,kind):
    count=len(entities)
    static=np.empty((count,count));moments=np.empty((7,count,count))
    outer=[prior.charge.quadrature(x,16) for x in entities]
    regular_order=6 if kind=='volume' else 8
    regular=[prior.charge.quadrature(x,regular_order) for x in entities]
    reused=new=0;orientation=0.;samples=[]
    for a in range(count):
        for b in range(a,count):
            prior.check_deadline(deadline,kind+' scalar assembly')
            key=pair_key(grid[a],grid[b])
            if key in cache:
                value,distance,skew=cache[key];reused+=1
            else:
                value,skew=paired_static(entities[a],entities[b],outer[a],outer[b])
                distance=prior.scalar_pair_moments(regular[a],regular[b],length)
                cache[key]=(value,distance,skew);new+=1
                if len(samples)<6:
                    samples.append((a,b,value,distance.copy()))
            static[a,b]=static[b,a]=value
            moments[:,a,b]=moments[:,b,a]=distance
            orientation=max(orientation,skew)
        if a%32==31:
            print(json.dumps(dict(stage=kind,entities=a+1,new_unique_pairs=new,reused_pairs=reused)),flush=True)
    refinement=[]
    for a,b,value,distance in samples:
        prior.check_deadline(deadline,kind+' sampled quadrature')
        higher,_=paired_static(entities[a],entities[b],prior.charge.quadrature(entities[a],24),prior.charge.quadrature(entities[b],24))
        hm=prior.scalar_pair_moments(prior.charge.quadrature(entities[a],regular_order+2),prior.charge.quadrature(entities[b],regular_order+2),length)
        refinement.append(dict(a=a,b=b,static_relative=float(abs(value/higher-1)),moments_relative=prior.relative(distance,hm)))
    return static,moments,dict(new_unique_pairs=new,reused_pairs=reused,maximum_raw_orientation_relative=orientation,sampled_refinement=refinement)


def tails(moments,frequencies,length):
    result=[]
    for frequency in frequencies:
        k=2*np.pi*frequency*np.sqrt(prior.static.source.MU0*prior.static.source.EPS0)
        assert abs(k*length)<=1
        value=np.zeros_like(moments[0],complex)
        for n in range(2,9):
            value+=(-1j*k*length)**n/factorial(n)*moments[n-2]/length
        result.append(value)
    return np.array(result)


def run(output,max_seconds):
    started=monotonic();deadline=started+max_seconds
    assert not output.exists()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,expected in PINS.items():
            assert prior.static.source.sha(ROOT/path)==expected,path
        prior.verify_pins()
        dimensions=np.array([1e-4,1e-4,25e-6]);length=float(np.linalg.norm(dimensions))
        grid,tet,triangles,boundary,sgrid,incidence,lift,cycles,owners=grid_geometry(dimensions)
        with np.load(OLD,allow_pickle=False) as data:
            cache,duplicate_error=old_cache(data,dimensions)
            frequencies=data['frequencies_hz']
        kv,mv,volume_checks=assemble(tet,grid,cache,length,deadline,'volume')
        ks,ms,surface_checks=assemble(triangles[boundary],sgrid,cache,length,deadline,'surface')
        volumes=np.array([prior.charge.measure(x) for x in tet])
        polarization=np.array([prior.static.faces(tet[owners[i][0][0]])[1][owners[i][0][1]][1]*prior.charge.measure(triangles[i]) for i in boundary])
        reference,_=prior.static.box_self_reference(dimensions)
        polarization_reference=prior.charge.uniform_polarization_reference(dimensions)
        energies=np.diag(polarization.T @ ks @ polarization)
        checks=dict(uniform_volume_relative=float(abs(volumes @ kv @ volumes/reference-1)),
            polarization_relative=float(np.max(abs(energies/polarization_reference-1))),
            polarization_trace_relative=float(abs(energies.sum()/(4*np.pi*dimensions.prod())-1)),
            volume_minimum_eigenvalue=float(np.linalg.eigvalsh(kv).min()),surface_minimum_eigenvalue=float(np.linalg.eigvalsh(ks).min()),
            old_translation_duplicate_relative=duplicate_error,volume=volume_checks,surface=surface_checks)
        gates=dict(uniform_volume=checks['uniform_volume_relative']<1e-6,polarization=checks['polarization_relative']<1e-5,
            trace=checks['polarization_trace_relative']<1e-5,positive_scalar=checks['volume_minimum_eigenvalue']>0 and checks['surface_minimum_eigenvalue']>0,
            sampled_quadrature=all(x['static_relative']<1e-5 and x['moments_relative']<1e-4 for item in (volume_checks,surface_checks) for x in item['sampled_refinement']))
        arrays=dict(tetrahedra_m=tet,triangles_m=triangles,boundary_face_indices=boundary,volumes_m3=volumes,
            cell_face_incidence=incidence,local_face_lift=lift,solenoidal_face_cycles=cycles,
            static_volume_per_m=kv,static_boundary_per_m=ks,volume_distance_moments=mv,boundary_distance_moments=ms,
            volume_tail_per_m=tails(mv,frequencies,length),boundary_tail_per_m=tails(ms,frequencies,length),frequencies_hz=frequencies,
            uniform_polarization_face_charge=polarization,polarization_reference_m3=polarization_reference)
        assert all(np.all(np.isfinite(x)) for x in arrays.values())
        gates['tail_symmetry']=all(prior.relative(x,x.T)<1e-13 for key in ('volume_tail_per_m','boundary_tail_per_m') for x in arrays[key])
        with (output/'kernels.npz').open('xb') as stream:
            np.savez_compressed(stream,**arrays)
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'CONSTANT_CURRENT_BOX_KERNEL_CONTROL',
            pins=PINS,script_sha256=prior.static.source.sha(Path(__file__)),kernels_sha256=prior.static.source.sha(output/'kernels.npz'),
            geometry=dict(tetrahedra=384,all_faces=864,boundary_faces=192,homogeneous_current_unknowns=480,subdivision=[4,4,4]),
            gates=gates,checks=checks,elapsed_s=monotonic()-started,
            scope='Same100x100x25um uniform-copper control. Exact homogeneous solenoidal RT0 currents are cellwise constant. Magnetic volume-volume and charge boundary-boundary scalar kernels retain the full degree8 retarded Green tail with the exact constant term separate. Frozen local pairs are scaled by1/s for static and s^p for physical distance power p; exact integer translation keys reuse congruent pairs. New pairs use symmetric analytic-inner/q16 static and q6(volume)/q8(surface) regular quadrature. No field solve, mesh convergence, source solid, material-interface, terminal or board accuracy is claimed.')
        prior.write_json(output/'result.json',result)
        print(json.dumps({k:result[k] for k in ('status','gates','elapsed_s','kernels_sha256')}),flush=True)
        return 0 if all(gates.values()) else 2
    except BaseException as error:
        prior.write_json(output/'failure.json',dict(status='STOP_CONSTANT_CURRENT_KERNEL_EXCEPTION',exception=repr(error),elapsed_s=monotonic()-started))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path);parser.add_argument('--max-seconds',type=float,default=175)
    parser.add_argument('--self-check',action='store_true')
    args=parser.parse_args()
    if args.self_check:
        a=np.array([[0,0,0],[1,0,0],[1,1,0],[1,1,1]])
        b=a+[2,-1,0]
        assert pair_key(a,b)==pair_key(b,a)==pair_key(a+3,b+3)
        assert pair_key(a[::-1],b)!=pair_key(a,b)
        assert pair_key(a,b)!=pair_key(a,b+[1,0,0])
        print('PASS_TRANSLATION_PAIR_KEY_SELF_CHECK')
    else:
        assert args.output
        raise SystemExit(run(args.output.resolve(),args.max_seconds))
