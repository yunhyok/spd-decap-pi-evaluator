"""SPD Decap PI Evaluator v0.23.1: degree-four radial overlap reuse pilot.

Five interior Chebyshev samples recover each matrix polynomial. Two additional
off-grid samples validate it. This is a numerical check, not an error theorem.
"""
from time import perf_counter, monotonic
import numpy as np
import shapely
from scipy.special import roots_legendre
from numpy.polynomial.chebyshev import chebvander
from astra_two_prism_covariogram import cross, event_lines, overlap_moments
from astra_prism_covariogram_self import depth_direct


def angular_radial_panels(a,b,order):
    hull=shapely.convex_hull(shapely.MultiPoint((b[:,None]-a[None]).reshape(-1,2)))
    vertices=np.asarray(hull.exterior.coords)[:-1]
    if cross(vertices,np.roll(vertices,-1,axis=0)).sum()<0: vertices=vertices[::-1]
    assert hull.distance(shapely.Point(0.,0.))<1e-14
    nodes,weights=roots_legendre(order); t,wt=(nodes+1)/2,weights/2
    normals,constants=event_lines(a,b)
    arrangement=[]
    for i in range(len(normals)):
        for j in range(i):
            mat=normals[[i,j]]
            if abs(np.linalg.det(mat))>1e-12: arrangement.append(np.linalg.solve(mat,constants[[i,j]]))
    arrangement=np.asarray(arrangement).reshape(-1,2)
    for left,right in zip(vertices,np.roll(vertices,-1,axis=0)):
        factor=cross(left,right)
        if factor==0: continue
        assert factor>0
        edge=right-left; closest=float(np.clip(-left@edge/(edge@edge),0.,1.))
        cuts=[0.,closest,1.]
        coeff=arrangement@np.linalg.inv(np.stack([left,edge])); r=coeff[:,0]
        valid=(r>1e-12)&(r<=1+1e-10); s=coeff[valid,1]/r[valid]
        cuts.extend(s[(s>0)&(s<1)].tolist())
        divisor=normals@edge; valid=abs(divisor)>1e-30
        s=(constants[valid]-normals[valid]@left)/divisor[valid]
        cuts.extend(s[(s>0)&(s<1)].tolist())
        cuts=np.sort(cuts); cuts=cuts[(cuts>1e-11)&(cuts<1-1e-11)]
        cuts=np.r_[0.,cuts[np.r_[True,np.diff(cuts)>1e-11]],1.] if len(cuts) else np.array([0.,1.])
        for lo,hi in zip(cuts[:-1],cuts[1:]):
            angular=lo+(hi-lo)*t; angular_weight=(hi-lo)*wt
            scale=np.linalg.norm(left+closest*edge)/np.linalg.norm(edge)
            if scale>0 and (lo==closest or hi==closest) and (hi-lo)/scale>4:
                extent=np.arcsinh((hi-lo)/scale); direction=1 if lo==closest else -1
                angular=closest+direction*scale*np.sinh(t*extent)
                angular_weight=wt*scale*extent*np.cosh(t*extent)
            boundary=left+angular[:,None]*edge
            with np.errstate(divide='ignore',invalid='ignore'): crossing=constants[None]/(boundary@normals.T)
            crossing=np.where(np.isfinite(crossing),np.clip(crossing,0.,1.),0.)
            rc=np.sort(np.column_stack([np.zeros(len(t)),crossing,np.ones(len(t))]),axis=1)
            # Same qualified partition coalescing; retained endpoints cover all
            # displacement domain. Every positive interval is evaluated below.
            for j in range(1,rc.shape[1]-1):
                close=rc[:,j]-rc[:,j-1]<1e-12; rc[close,j]=rc[close,j-1]
            rc[rc>1-1e-12]=1.
            rlo,span=rc[:,:-1],np.diff(rc,axis=1); ii,jj=np.nonzero(span>0)
            yield boundary[ii],factor*angular_weight[ii],rlo[ii,jj],span[ii,jj],t,wt


def two_prism_polynomial(a,b,height,la,lb,signs_a,signs_b,order,deadline,*,max_geometry_points=500000):
    started=perf_counter(); anchor=np.asarray(a)[0]
    a,b=np.asarray(a)-anchor,np.asarray(b)-anchor
    aa=abs(cross(a[1]-a[0],a[2]-a[0]))/2; ab=abs(cross(b[1]-b[0],b[2]-b[0]))/2
    ca=np.linalg.solve(la,np.eye(3))*np.asarray(signs_a)[None]
    cb=np.linalg.solve(lb,np.eye(3))*np.asarray(signs_b)[None]
    sa,sb=ca.sum(axis=1),cb.sum(axis=1); va,vb=ca@a,cb@b
    sample_x=np.cos((2*np.arange(5)+1)*np.pi/10)
    extra_x=np.array([-.381,.713]); all_x=np.r_[sample_x,extra_x]
    inverse=np.linalg.inv(chebvander(sample_x,4)); extra_v=chebvander(extra_x,4)
    w=np.zeros((3,3)); constant=np.zeros((3,3)); mass=0.
    poly_error=0.; mass_error=0.; relative_discrepancy=0.; point_count=0; radial_count=0; panels=0
    for boundary,angular_weight,rlo,span,t,wt in angular_radial_panels(a,b,order):
        assert monotonic()<deadline, 'Radial-polynomial deadline'
        r=rlo[:,None]+span[:,None]*(all_x+1)/2
        displacement=(boundary[:,None]*r[:,:,None]).reshape(-1,2)
        point_count+=len(displacement); radial_count+=len(rlo)*len(t); panels+=1
        assert point_count<=max_geometry_points, 'Radial-polynomial geometry point budget'
        values=[]
        for start in range(0,len(displacement),8192):
            assert monotonic()<deadline, 'Radial-polynomial deadline'
            u=displacement[start:start+8192]
            area,mean,trace=overlap_moments(a,b,u)
            test=mean[:,None]*sa[None,:,None]-va[None]
            trial=(mean+u)[:,None]*sb[None,:,None]-vb[None]
            moment=np.einsum('pid,pjd->pij',test,trial)+trace[:,None,None]*sa[None,:,None]*sb[None,None]
            density=area/(aa*ab)
            values.append(np.column_stack([density, (1e-7/4*density[:,None,None]*moment).reshape(-1,9)]))
        values=np.concatenate(values).reshape(len(rlo),7,10)
        coefficients=np.einsum('ij,njk->nik',inverse,values[:,:5])
        discrepancy=np.einsum('ij,njk->nik',extra_v,coefficients)-values[:,5:]
        matrix_discrepancy=np.linalg.norm(discrepancy[:,:,1:].reshape(-1,2,3,3),ord=2,axis=(-2,-1)).max(axis=1)
        scale=np.linalg.norm(values[:,:,1:].reshape(-1,7,3,3),ord=2,axis=(-2,-1)).max(axis=1)
        relative_discrepancy=max(relative_discrepancy,float(np.max(matrix_discrepancy/np.maximum(scale,1e-300))))
        radial=rlo[:,None]+span[:,None]*t[None]**2
        measure=angular_weight[:,None]*span[:,None]*2*t[None]*wt[None]*radial
        kernel=depth_direct(np.linalg.norm(boundary,axis=1)[:,None]*radial,height)
        # The recovered polynomial is in the interval's x=2*(r-rlo)/span-1.
        interpolated=np.einsum('ij,njk->nik',chebvander(2*t*t-1,4),coefficients)
        mass+=np.sum(measure*interpolated[:,:,0])
        matrix=interpolated[:,:,1:].reshape(-1,len(t),3,3)
        constant+=np.einsum('nt,ntij->ij',measure,matrix)
        w+=np.einsum('nt,ntij->ij',measure*kernel,matrix)
        poly_error+=np.sum(np.sum(measure*kernel,axis=1)*matrix_discrepancy)
        mass_error+=np.sum(np.sum(measure,axis=1)*abs(discrepancy[:,:,0]).max(axis=1))
    expected=1e-7/4*((a.mean(axis=0)*sa[:,None]-va)@(b.mean(axis=0)*sb[:,None]-vb).T)
    return w,dict(order=order,elapsed_s=perf_counter()-started,geometry_points=point_count,
        radial_kernel_points=radial_count,angular_panels=panels,
        normalization_error=abs(float(mass)-1),normalized_overlap_mass=float(mass),
        kernel_one_matrix_relative_error=float(np.linalg.norm(constant-expected)/np.linalg.norm(expected)),
        extra_node_matrix_relative_max=relative_discrepancy,
        integrated_extra_node_matrix_indicator=float(poly_error),integrated_extra_node_mass_indicator=float(mass_error),
        fitting='Degree4, 5 interior Chebyshev nodes, 2 additional off-grid nodes; each whitened matrix numerator is evaluated directly.',
        partition_coverage='Same frozen event partition as qualified covariogram; all positive radial intervals retained. Coalescing only changes cut locations, with endpoints fixed.',
        indicator_scope='Two off-grid discrepancies are additional numerical indicators; they are not a rigorous interpolation bound.')
