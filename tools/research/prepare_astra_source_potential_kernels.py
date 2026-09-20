"""SPD Decap PI Evaluator v0.23.1: finite source-contact scalar Green blocks.

Only18 volume and32 active face densities; reuse analytic inner R^-1..R^7.
Retain both observer orientations and refinement. No finite field solve.
"""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
import prepare_astra_source_potential_contacts as contacts
import qualify_astra_tetra_charge_green as charge
import qualify_astra_tetra_power_inner as inner

ROOT=contacts.ROOT
PINS={**contacts.PINS,
    'tools/research/prepare_astra_source_potential_contacts.py':'99d70edcaabb94dca36a247e444db297c369c10b92f1e1025741f2dfc2628aaf',
    'outputs/research/astra-source-potential-contacts-01/result.json':'2900ab09e7ea10b3a774ee031214a63a89f3ef21c38d5ec722cb75553882fff4',
    'outputs/research/astra-source-potential-contacts-01/contacts.npz':'55294d51fdb4da642499e38e7e613a226d9fc5561d2159e73f38de1f853aa21a',
    'tools/research/qualify_astra_tetra_charge_green.py':'aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9',
    'tools/research/qualify_astra_tetra_power_inner.py':'ac5705ef325a828df1dcb90251d211504ddcee7d87ef4dbf70b667913e90ea11'}


def integrate(entities,order,deadline,kind):
    samples=[charge.quadrature(e,order) for e in entities];points=np.concatenate([p for p,w in samples]);weights=np.array([w for p,w in samples])
    n=len(entities);raw=np.empty((9,n,n));routine=inner.tetra_powers if kind=='volume' else inner.triangle_powers
    for j,entity in enumerate(entities):
        if monotonic()>deadline:raise TimeoutError(kind+' Green integration deadline')
        value=routine(entity,points)/charge.measure(entity)
        raw[:,:,j]=np.sum(value.reshape(9,n,-1)*weights[None,:,:],axis=2)
        if j%8==0:print(json.dumps(dict(stage=kind,observer_order=order,source_entity=j,source_count=n)),flush=True)
    centers=entities.mean(axis=1);vertices=entities.shape[1]
    variance=np.sum((entities-centers[:,None,:])**2,axis=(1,2))/(vertices*(vertices+1))
    distance2=np.sum((centers[:,None,:]-centers[None,:,:])**2,axis=2)+variance[:,None]+variance[None,:]
    paired=(raw+raw.transpose(0,2,1))/2;roots=np.sqrt(paired[0].diagonal())
    checks=dict(observer_order=order,unit_density_constant_max=float(np.max(abs(raw[1]-1))),
        exact_distance2_scaled_max=float(np.max(abs(raw[3]-distance2))/np.max(distance2)),
        raw_static_orientation_scaled_max=float(np.max(abs(raw[0]-raw[0].T)/roots[:,None]/roots[None,:])),
        minimum_normalized_static_eigenvalue=float(np.linalg.eigvalsh(paired[0]/roots[:,None]/roots[None,:]).min()))
    assert checks['unit_density_constant_max']<1e-11 and checks['exact_distance2_scaled_max']<1e-10
    # The exact R^0 integral of two normalized densities is one; check before substituting.
    paired[1]=1.
    return raw,paired,checks


def retarded(powers,frequencies,length):
    result=[]
    for frequency in frequencies:
        k=2*np.pi*frequency*np.sqrt(contacts.old.source.MU0*contacts.old.source.EPS0)
        result.append((powers[0]+sum((-1j*k*length)**n/factorial(n)*powers[n] for n in range(1,9)))/length)
    return np.array(result)


def run(output,max_seconds):
    start=monotonic()
    for path,pin in PINS.items():assert contacts.old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-source-potential-contacts-01/contacts.npz',allow_pickle=False) as s:
        tet=s['tetrahedra_m'];tri=s['triangles_m'];active=s['active_charge_faces'];mid=s['material_id']
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);length=max(dimensions);frequencies=contacts.old.source.FREQUENCIES
    volumes=np.array([charge.measure(t) for t in tet]);_,owners,*_=contacts.old.reference.topology.current_topology(tet)
    flux=np.zeros((len(active),3))
    for i,face in enumerate(active):
        if len(owners[face])==1:
            cell,local_face=owners[face][0];_,normal,area=contacts.old.static.faces(tet[cell])[1][local_face]
            flux[i]=area*normal
    assert np.linalg.norm(flux.sum(axis=0))<1e-12*np.linalg.norm(flux)
    face_reference=charge.uniform_polarization_reference(dimensions);volume_references=[]
    selections=[np.flatnonzero(mid==i) for i in range(3)]+[np.arange(18)]
    for indices in selections:
        d=np.ptp(tet[indices].reshape(-1,3),axis=0);volume_references.append(contacts.old.static.box_self_reference(d)[0])
    cases=[];previous=None
    for volume_order,face_order in ((14,28),(20,36)):
        rv,v,cv=integrate(tet/length,volume_order,start+max_seconds,'volume')
        rf,f,cf=integrate(tri[active]/length,face_order,start+max_seconds,'face')
        observed=[float(volumes[ix] @ (v[0][np.ix_(ix,ix)]/length) @ volumes[ix]) for ix in selections]
        uniform=np.diag(flux.T @ (f[0]/length) @ flux)
        gaussian=max(abs(np.array(observed)/volume_references-1));polarization=max(abs(uniform/face_reference-1))
        refinement={}
        if previous is not None:
            for name,now,before in (('volume',v,previous[0]),('face',f,previous[1])):
                roots=np.sqrt(now[0].diagonal());refinement[name]=float(np.max(abs(now[0]-before[0])/roots[:,None]/roots[None,:]))
        previous=v,f
        filename=output/f'kernels-v{volume_order}-f{face_order}.npz'
        with filename.open('xb') as stream:np.savez_compressed(stream,raw_normalized_volume_powers=rv,raw_normalized_face_powers=rf,
            normalized_volume_powers=v,normalized_face_powers=f,volume_green_per_m=retarded(v,frequencies,length),
            face_green_per_m=retarded(f,frequencies,length),active_charge_faces=active,frequencies_hz=frequencies,length_scale_m=length)
        case=dict(volume=cv,face=cf,gaussian_box_self_max_relative=float(gaussian),gaussian_face_polarization_max_relative=float(polarization),
            static_refinement_diagonal_scaled=refinement,kernels_sha256=contacts.old.source.sha(filename))
        cases.append(case);print(json.dumps(case),flush=True)
    last=cases[-1]
    gates=dict(exact_moments=True,gaussian_volume=last['gaussian_box_self_max_relative']<1e-5,
        gaussian_face=last['gaussian_face_polarization_max_relative']<1e-5,
        refinement=max(last['static_refinement_diagonal_scaled'].values())<1e-4,
        raw_orientation=max(last[name]['raw_static_orientation_scaled_max'] for name in ('volume','face'))<1e-4,
        positive_static=min(last[name]['minimum_normalized_static_eigenvalue'] for name in ('volume','face'))>1e-8)
    max_radius=np.linalg.norm(dimensions);max_k=2*np.pi*max(frequencies)*np.sqrt(contacts.old.source.MU0*contacts.old.source.EPS0)
    bound=np.exp(max_k*max_radius)*(max_k*max_radius)**9/factorial(9)/max_radius
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'SOURCE_POTENTIAL_SCALAR_KERNELS',
        pins=PINS,script_sha256=contacts.old.source.sha(Path(__file__)),gates=gates,cases=cases,elapsed_s=monotonic()-start,
        volume_entities=18,face_entities=32,maximum_frequency_taylor_absolute_bound_per_m=float(bound),
        includes_leading_minus_ik=True,
        scope='New finite18-volume/32-active-face scalar kernels on saved source-property rectangular control. Analytic R^-1..R^7 inner recurrence; normalized mean densities; both raw orientations retained and their explicitly paired quadrature used. R0/R2 identities, four independent Gaussian volume self controls and three boundary-polarization controls; fixed observer-order comparison. Full complex Green includes the exact leading -ik term. No clipping, omission of ABF, field solve, interface continuity, continuum/board or PowerSI accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--max-seconds',type=float,default=300)
    a=p.parse_args();destination=a.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination,a.max_seconds)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_POTENTIAL_KERNEL',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
