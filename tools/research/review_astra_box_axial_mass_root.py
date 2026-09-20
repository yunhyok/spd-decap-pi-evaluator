"""SPD Decap PI Evaluator v0.23.1: independent axial mass artifact review.

Use Astra's monomial-curl formulas, not the producer's candidate implementation.
Reconstruct cell moments, full mass, forcing and projection before comparing.
"""
from pathlib import Path
import json
import numpy as np
import qualify_astra_box_axial_green as formula

ROOT=formula.ROOT
PINS={
    'tools/research/qualify_astra_box_axial_green.py':'c24cf015c4c2788914c67cca8539411194d20734851835293acd051f49925801',
    'outputs/research/astra-box-axial-current-space-02/space.npz':'42504b92ebfb93fe401baf294a57ef9d362a29f65026f03b12f065b94a6f05c7',
    'outputs/research/astra-box-axial-current-space-02/result.json':'285dd00015e65400868e2729fddc794d2681cb14925f465c545e3f399fd6f740',
    'outputs/research/astra-box-polynomial-current-space-01/space.npz':'41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9',
    'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02'}


def run():
    for path,pin in PINS.items():assert formula.green.space.field.source.sha(ROOT/path)==pin,path
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:tet=saved['tetrahedra_m']
    vertices=tet.reshape(-1,3);dimensions=np.ptp(vertices,axis=0);center=(vertices.min(axis=0)+vertices.max(axis=0))/2
    x,w=np.polynomial.legendre.leggauss(10)
    points=center+np.array(np.meshgrid(x,x,x,indexing='ij')).reshape(3,-1).T*dimensions/2
    weights=(w[:,None,None]*w[None,:,None]*w[None,None,:]).ravel()*np.prod(dimensions)/8
    values=formula.values(points,center,dimensions);new=values[:,:,48:]
    poly_mass=np.einsum('n,ndi,ndj->ij',weights,values,values);new_mass=poly_mass[48:,48:]
    f=np.stack([.5*np.cross(axis,points-center) for axis in np.eye(3)],axis=2)
    new_rhs=np.einsum('n,ndi,nds->is',weights,new,f)
    mean=np.einsum('n,ndi->di',weights,new)
    moments=[];volumes=[]
    for t in tet:
        p,wt=formula.enriched.inner.static.tetra_quadrature(t,10)
        moments.append(np.einsum('n,ndi->di',wt,formula.values(p,center,dimensions,axial_only=True)))
        volumes.append(formula.enriched.inner.static.faces(t)[0])
    moments=np.array(moments)
    with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as saved:
        old_mass=saved['n48_mass'];old_rhs=saved['n48_uniform_curl_rhs'];old_map=saved['n48_cell_integrated_current_map'];q=saved['n48_boundary_divergence_range']
    cross=np.einsum('tdi,tdj,t->ij',old_map,moments,1/np.array(volumes));cross[25:73]=poly_mass[:48,48:]
    order=np.r_[np.arange(73),[120,121],np.arange(73,120)]
    mass=np.block([[old_mass,cross],[cross.T,new_mass]])[np.ix_(order,order)]
    rhs=np.vstack((old_rhs,new_rhs))[order]
    scale=np.sqrt(mass.diagonal());normal=mass/scale[:,None]/scale[None,:]
    solved=np.linalg.solve(normal[:75,:75],rhs[:75]/scale[:75,None])/scale[:75,None]
    schur=normal[73:75,73:75]-normal[73:75,:73] @ np.linalg.solve(normal[:73,:73],normal[:73,73:75])
    with np.load(ROOT/'outputs/research/astra-box-axial-current-space-02/space.npz',allow_pickle=False) as saved:
        full_error=float(np.max(abs(mass-saved['mass'])/scale[:,None]/scale[None,:]))
        moment_error=float(np.linalg.norm(moments-saved['new_cell_integrated_current_map'])/np.linalg.norm(moments))
        cross_scale=np.sqrt(old_mass.diagonal())[:,None]*np.sqrt(new_mass.diagonal())[None,:]
        cross_error=float(np.max(abs(cross-saved['old_to_new_cross_mass'])/cross_scale))
        delta=solved-saved['closed_uniform_curl_solution']
        solution_error=float(np.sqrt(np.trace(delta.T @ mass[:75,:75] @ delta)/np.trace(solved.T @ mass[:75,:75] @ solved)))
        response=rhs[:75].T @ solved;saved_response=saved['uniform_curl_rhs'][:75].T @ saved['closed_uniform_curl_solution']
        response_error=float(np.linalg.norm(response-saved_response)/np.linalg.norm(response))
        rhs_scale=np.sqrt(new_mass.diagonal())[:,None]*np.sqrt(np.diag(response))[None,:]
        rhs_error=float(np.max(abs(new_rhs-saved['uniform_curl_rhs'][73:75])/rhs_scale))
        order_exact=bool(np.array_equal(order,saved['coordinate_order']) and np.array_equal(q,saved['boundary_divergence_range']))
    mean_scaled=float(np.max(abs(mean)/np.sqrt(new_mass.diagonal()*np.prod(dimensions))[None,:]))
    checks=dict(full_mass_diagonal_scaled_max=full_error,cell_moment_saved_q8_direct_q10_relative=moment_error,
        old_to_new_cross_mass_diagonal_scaled_max=cross_error,projection_current_mass_norm_relative=solution_error,
        response_relative=response_error,zero_rhs_difference_physical_scaled_max=rhs_error,
        zero_mean_physical_scaled_max=mean_scaled,normalized_schur_eigenvalues=np.linalg.eigvalsh((schur+schur.T)/2).tolist(),order_and_charge_exact=order_exact)
    assert order_exact and max(full_error,moment_error,cross_error,solution_error,response_error,rhs_error,mean_scaled)<1e-10
    assert min(checks['normalized_schur_eigenvalues'])>.6
    output=ROOT/'outputs/research/astra-box-axial-root-mass-review-01';assert not output.exists();output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='ACCEPT_INDEPENDENT_AXIAL_MASS_AND_PROJECTION',
        input_sha256=PINS,script_sha256=formula.green.space.field.source.sha(Path(__file__)),checks=checks,
        scope='Astra independently implemented monomial curls, whole-box tensor Gauss10, and tetra Gauss10. Reconstruct saved q12 new/old-bubble mass and forcing, saved q8 cell moments, RT0 cross, full reordered122 mass, normalized Schur, and75-closed Poisson projection. Frozen old120 space is assumed qualified. Exact-zero quantities use physical scales. No Green, finite-frequency or board accuracy claim. Earlier review01-03 are not final independent acceptance.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(result,indent=2))


if __name__=='__main__':run()
