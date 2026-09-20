"""SPD Decap PI Evaluator v0.23.1: constant-tetra distance-power integrals.

Divergence-theorem recurrences extend the verified 1/R inner integral to
R^0,...,R^7 for polynomial-current cross Green integration. No field solve.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import qualify_astra_tetra_volume_green as static

ROOT=Path(__file__).resolve().parents[2]
PINS={
    'tools/research/qualify_astra_tetra_volume_green.py':'24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02'}


def triangle_powers(vertices,points):
    edge=vertices[[1,2,0]]-vertices
    lengths=np.linalg.norm(edge,axis=1)
    normal=np.cross(edge[0],-edge[2]);area=np.linalg.norm(normal)/2;normal/=2*area
    tangent=edge/lengths[:,None];outward=np.cross(tangent,normal)
    delta=vertices[None,:,:]-points[:,None,:]
    distance=np.einsum('ned,ed->ne',delta,outward)
    lower=np.einsum('ned,ed->ne',delta,tangent);upper=lower+lengths
    height=abs((vertices[0]-points) @ normal)[:,None]
    r0=np.hypot(distance,height);safe=np.where(r0==0,1.,r0)
    asinh=np.arcsinh(upper/safe)-np.arcsinh(lower/safe);asinh[r0==0]=0
    ru,rl=np.hypot(r0,upper),np.hypot(r0,lower)
    dr=lengths*(upper+lower)/(ru+rl)
    line={-1:asinh,0:np.broadcast_to(lengths,lower.shape)}
    inverse,_=static.triangle_moments(vertices,points)
    face={-1:inverse,0:np.full(len(points),area)}
    for power in range(1,8):
        difference=dr*sum(ru**(power-1-j)*rl**j for j in range(power))
        boundary=lengths*ru**power+lower*difference
        line[power]=(boundary+power*r0*r0*line[power-2])/(power+1)
        face[power]=(np.sum(distance*line[power],axis=1)+power*height[:,0]**2*face[power-2])/(power+2)
    return np.array([face[p] for p in range(-1,8)])


def tetra_powers(tetra,points):
    origin=tetra[0].copy();tetra=tetra-origin;points=points-origin
    volume,boundary=static.faces(tetra)
    result=np.zeros((9,len(points)))
    for vertices,normal,_ in boundary:
        distance=(vertices[0]-points) @ normal
        result+=triangle_powers(vertices,points)*distance
    result/=np.arange(2,11)[:,None]
    assert np.max(abs(result[1]-volume))/volume<1e-10
    result[1]=volume
    return result


def run(output):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert static.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as data:
        tetrahedra=data['tetrahedra_m']/1e-4
    checks=[];arrays={}
    for cell in (0,17,47):
        tetra=tetrahedra[cell];center=tetra.mean(axis=0)
        points=np.vstack((center,tetra[0],tetra[1:].mean(axis=0),center+[.3,.2,.1],center+[-.4,.5,.2],center+[2.,-1.,3.]))
        actual=tetra_powers(tetra,points)
        inverse,_=static.tetra_inner(tetra,points)
        static_error=float(np.max(abs(actual[0]-inverse)/abs(inverse)))
        translated=tetra_powers(tetra+[123.,-54.,7.],points+[123.,-54.,7.])
        scaled=tetra_powers(.37*tetra,.37*points)/(.37**np.arange(2,11)[:,None])
        translation_error=float(np.max(abs(translated-actual)/abs(actual)))
        scaling_error=float(np.max(abs(scaled-actual)/abs(actual)))
        quad_values=[]
        for order in (18,26):
            qp,qw=static.tetra_quadrature(tetra,order)
            radius=np.linalg.norm(points[:,None,:]-qp[None,:,:],axis=2)
            quad_values.append(np.array([radius**p @ qw for p in range(8)]))
        even_error=float(np.max(abs(quad_values[-1][::2]-actual[1::2])/abs(actual[1::2])))
        odd_error=float(np.max(abs(quad_values[-1][1::2]-actual[2::2])/abs(actual[2::2])))
        odd_refinement=float(np.max(abs(quad_values[-1][1::2]-quad_values[0][1::2])/abs(actual[2::2])))
        outside_odd=float(np.max(abs(quad_values[-1][1::2,3:]-actual[2::2,3:])/abs(actual[2::2,3:])))
        assert static_error<1e-12 and translation_error<1e-10 and scaling_error<1e-11
        assert even_error<1e-10 and odd_error<1e-4 and outside_odd<1e-10
        assert np.all(actual>0)
        check=dict(cell=cell,static_reference_relative=static_error,translation_relative=translation_error,
            scaling_relative=scaling_error,even_polynomial_quadrature_relative=even_error,
            odd_quadrature_relative=odd_error,odd_quadrature_refinement_relative=odd_refinement,
            outside_odd_quadrature_relative=outside_odd)
        checks.append(check);print(json.dumps(check),flush=True)
        arrays[f'cell_{cell}_points']=points;arrays[f'cell_{cell}_powers']=actual
    with (output/'powers.npz').open('xb') as stream:np.savez_compressed(stream,**arrays)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_TETRA_DISTANCE_POWER_INNER',
        pins=PINS,script_sha256=static.source.sha(Path(__file__)),checks=checks,elapsed_s=monotonic()-start,
        powers_sha256=static.source.sha(output/'powers.npz'),
        scope='Constant-source tetra inner integrals R^-1 through R^7 from signed-face divergence recurrences in normalized geometry. Reuses the qualified inverse-radius triangle primitive. Even-power independent volume quadrature is polynomial-exact; odd-power q18/q26 controls include boundary/interior/exterior observations, and translation/scaling are checked. No observer integration, RT0-bubble block, finite-frequency field, material junction, port or board solve is performed.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],elapsed_s=result['elapsed_s'])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
