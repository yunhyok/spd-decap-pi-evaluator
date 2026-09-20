"""SPD Decap PI Evaluator v0.23.1: bounded adaptive L02 nonself quadrature.

Only integration cells are bisected; original charge/current supports and R/D
stay fixed. The source integral uses the existing analytic tetra/triangle inner
kernel. Positive outer rules of orders 4/8 estimate each integration leaf's
error; the largest weighted error is bisected along its longest edge. Every
source primitive contributes to every evaluated target, with no pair dropping.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import heapq
import json
from pathlib import Path
from time import monotonic, perf_counter
import traceback

import numpy as np

from assemble_astra_l02_finite_charge_self import prism_union, wall_union
from astra_layered_charge_action import halfspace_action, _direct_points
from astra_stratified_charge_green import EPS0, ROOT, source_background
from qualify_astra_tetra_charge_green import quadrature, measure
from qualify_astra_tetra_volume_green import tetra_inner, triangle_moments


@lru_cache(maxsize=4)
def rule(vertex_count, order):
    vertices = np.vstack((np.zeros(3),np.eye(3)))[:vertex_count]
    points,weights = quadrature(vertices,order)
    bary = np.column_stack((1-points.sum(axis=1),points[:,:vertex_count-1]))
    assert np.max(abs(bary.sum(axis=1)-1)) < 2e-15 and abs(weights.sum()-1) < 1e-13
    return bary,weights


def bisect(vertices):
    a,b = np.unravel_index(np.argmax(np.linalg.norm(vertices[:,None]-vertices[None,:],axis=2)),
                           (len(vertices),len(vertices)))
    midpoint = (vertices[a]+vertices[b])/2
    left,right = vertices.copy(),vertices.copy()
    left[a],right[b] = midpoint,midpoint
    if len(vertices)==4:
        for child in (left,right):
            if np.linalg.det(child[1:]-child[:1])<0:
                child[[1,2]]=child[[2,1]]
    assert abs((measure(left)+measure(right))/measure(vertices)-1)<1e-10
    return left,right


class AdaptiveOuter:
    """Reusable one-direction integral with an aggregate absolute error indicator."""
    def __init__(self,observer,source,deadline,*,max_leaves=8192):
        self.deadline,self.max_leaves=deadline,max_leaves
        self.anchor=source[0][0].copy()
        source=[vertices-self.anchor for vertices in source]
        self.source_measure=sum(measure(vertices) for vertices in source)
        self.observer_measure=sum(measure(vertices) for vertices in observer)
        interfaces,eps,_=source_background()
        self.interface=interfaces[0]-self.anchor[2]
        assert all(np.min(v[:,2])>self.interface for v in source)
        self.factor=1/(4*np.pi*EPS0*eps[1])
        self.reflection=(eps[1]-eps[0])/(eps[1]+eps[0])
        self.sources=[]
        for vertices in source:
            image=vertices.copy();image[:,2]=2*self.interface-image[:,2]
            if len(vertices)==4 and np.linalg.det(image[1:]-image[:1])<0:
                image[[1,2]]=image[[2,1]]
            self.sources.append((vertices,image,tetra_inner if len(vertices)==4 else triangle_moments))
        self.heap=[];self.next_id=0;self.value=0j;self.error=0.;self.points=0;self.splits=0
        for vertices in observer:
            self._add(vertices-self.anchor,0)

    def potential(self,points):
        assert monotonic()<self.deadline,'Adaptive nonself execution boundary'
        self.points+=len(points)
        result=np.zeros(len(points),complex)
        for source,image,inner in self.sources:
            result+=inner(source,points)[0]+self.reflection*inner(image,points)[0]
        result*=self.factor/self.source_measure
        assert np.isfinite(result).all() and np.all(result.real>0)
        return result

    def _add(self,vertices,depth):
        fraction=measure(vertices)/self.observer_measure
        estimates=[]
        for order in (4,8):
            bary,weights=rule(len(vertices),order)
            estimates.append(fraction*(weights@self.potential(bary@vertices)))
        error=float(abs(estimates[1]-estimates[0]))
        record=(vertices,estimates[1],error,depth)
        heapq.heappush(self.heap,(-error,self.next_id,record));self.next_id+=1
        self.value+=estimates[1];self.error+=error

    def refine(self,tolerance):
        while self.error>tolerance*abs(self.value):
            assert monotonic()<self.deadline,'Adaptive nonself execution boundary'
            assert len(self.heap)<self.max_leaves,'Adaptive outer leaf budget'
            _,_,(vertices,value,error,depth)=heapq.heappop(self.heap)
            self.value-=value;self.error=max(0.,self.error-error)
            for child in bisect(vertices):
                self._add(child,depth+1)
            self.splits+=1
        return self.value

    def receipt(self):
        return dict(integration_leaves=len(self.heap),longest_edge_bisections=self.splits,
                    evaluated_outer_points=self.points,maximum_depth=max(row[2][3] for row in self.heap),
                    relative_aggregate_indicator=float(self.error/abs(self.value)))


@lru_cache(maxsize=16)
def tensor_rule(orders):
    from scipy.special import roots_legendre
    rules=[roots_legendre(order) for order in orders]
    coordinates=np.meshgrid(*[(x+1)/2 for x,w in rules],indexing='ij')
    weights=np.meshgrid(*[w/2 for x,w in rules],indexing='ij')
    return np.column_stack([x.ravel() for x in coordinates]),np.prod(weights,axis=0).ravel()


class AnisotropicOuter(AdaptiveOuter):
    """Adaptive prism/wall parameter boxes; no change to the spatial basis.

    The triangle map is (1-v)*((1-u)*A+u*B)+v*C, with normalized Jacobian
    2*(1-v). The z coordinate is independent. Comparing an 8-point rule to a
    4-point rule on each axis selects transverse or longitudinal refinement.
    """
    def __init__(self,domains,source,deadline,*,max_leaves=8192):
        super().__init__([],source,deadline,max_leaves=max_leaves)
        measures=[]
        for vertices,z_bounds in domains:
            if len(vertices)==3:
                measures.append(abs(np.linalg.det((vertices[1:]-vertices[0]).T))*(z_bounds[1]-z_bounds[0])/2)
            else:
                measures.append(np.linalg.norm(vertices[1]-vertices[0])*(z_bounds[1]-z_bounds[0]))
        total=sum(measures)
        assert total>0
        for (vertices,z_bounds),volume in zip(domains,measures):
            if len(vertices)==3:
                a,b=np.unravel_index(np.argmax(np.linalg.norm(vertices[:,None]-vertices[None,:],axis=2)),(3,3))
                c=int(np.setdiff1d(np.arange(3),[a,b])[0])
                vertices=vertices[[a,b,c]]
            base=np.column_stack((vertices,np.full(len(vertices),z_bounds[0])))-self.anchor
            domain=(base,float(z_bounds[1]-z_bounds[0]),float(volume/total))
            dim=3 if len(vertices)==3 else 2
            self._add_box(domain,np.zeros(dim),np.ones(dim),np.zeros(dim,np.int64))

    def _add_box(self,domain,lo,hi,depth):
        base,height,fraction=domain;dim=len(lo)
        rules=[(8,)*dim]+[tuple(4 if j==axis else 8 for j in range(dim)) for axis in range(dim)]
        blocks=[];block_weights=[]
        for orders in rules:
            unit,weight=tensor_rule(orders)
            parameter=lo+(hi-lo)*unit
            if dim==3:
                u,v,w=parameter.T
                point=(1-v[:,None])*((1-u[:,None])*base[0]+u[:,None]*base[1])+v[:,None]*base[2]
                point[:,2]+=height*w
                jacobian=2*(1-v)
            else:
                u,w=parameter.T
                point=(1-u[:,None])*base[0]+u[:,None]*base[1]
                point[:,2]+=height*w
                jacobian=np.ones(len(u))
            blocks.append(point)
            block_weights.append(weight*jacobian*fraction*np.prod(hi-lo))
        potential=self.potential(np.vstack(blocks))
        cursor=0;estimates=[]
        for weights in block_weights:
            estimates.append(weights@potential[cursor:cursor+len(weights)]);cursor+=len(weights)
        directional=np.abs(np.asarray(estimates[1:])-estimates[0])
        error=float(directional.sum());axis=int(np.argmax(directional))
        record=(domain,lo,hi,depth,estimates[0],error,axis)
        heapq.heappush(self.heap,(-error,self.next_id,record));self.next_id+=1
        self.value+=estimates[0];self.error+=error

    def refine(self,tolerance):
        while self.error>tolerance*abs(self.value):
            assert monotonic()<self.deadline,'Anisotropic nonself execution boundary'
            assert len(self.heap)<self.max_leaves,'Anisotropic outer leaf budget'
            _,_,(domain,lo,hi,depth,value,error,axis)=heapq.heappop(self.heap)
            self.value-=value;self.error=max(0.,self.error-error)
            mid=(lo[axis]+hi[axis])/2
            next_depth=depth.copy();next_depth[axis]+=1
            left_hi=hi.copy();left_hi[axis]=mid
            right_lo=lo.copy();right_lo[axis]=mid
            self._add_box(domain,lo,left_hi,next_depth)
            self._add_box(domain,right_lo,hi,next_depth)
            self.splits+=1
        return self.value

    def receipt(self):
        depth=np.max(np.array([row[2][3] for row in self.heap]),axis=0)
        return dict(integration_leaves=len(self.heap),parameter_box_bisections=self.splits,
                    evaluated_outer_points=self.points,maximum_depth_per_axis=depth.tolist(),
                    relative_aggregate_indicator=float(self.error/abs(self.value)))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(out,seconds,anisotropic=False):
    started=monotonic();timer=perf_counter();deadline=started+seconds
    out.mkdir(exist_ok=False);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    research=ROOT/'outputs/research'
    current=research/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
    charge=research/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz'
    self_path=research/'astra-l02-complete-self-20260912/complete-self.npz'
    pins={current:'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
          charge:'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
          self_path:'03b38c0a431eb7b275bedfa8f8c5114501485d6e90b0e2dd93cbcdce2f34b08b'}
    for name in ('qualify_astra_tetra_charge_green.py','qualify_astra_tetra_volume_green.py',
                 'assemble_astra_l02_finite_charge_self.py','astra_layered_charge_action.py'):
        path=ROOT/'tools/research'/name;pins[path]=sha(path)
    cases=[];failure=None
    try:
        for path,digest in pins.items():assert sha(path)==digest,path
        # The edge neighbors 75/80 were identified from the three exact q0
        # vertices. This bounded set includes edge, vertex, distant and wall cases.
        selected=np.array([0,75,1622616] if anisotropic else [0,1368285,1,75,1622616],np.int64)
        with np.load(charge,allow_pickle=False) as data:
            rows=data['charge_row_ids'][selected]
            z_bounds=data['z_interval_um']*1e-6
            spread_col=data['spread_col'];mask=np.isin(spread_col,selected)
            point_columns=spread_col[mask];point_weights=data['spread_data'][mask]
            points=data['quadrature_points_um'][mask]*1e-6
            walls=data['exterior_segment_vertices_um'][:1]*1e-6
            wall_row=int(data['exterior_charge_row_ids'][0])
            assert rows[-1]==wall_row
        with np.load(self_path,allow_pickle=False) as data:
            assert np.array_equal(data['original_charge_rows'][selected],rows)
            physical_self=data['physical_halfspace_self_p'][selected]
        with np.load(current,allow_pickle=False) as data:
            parent=data['piece_parent_free_ordinal'];keep=np.isin(parent,rows[:-1])
            triangles=data['piece_triangle_xy_m'][keep];parent=parent[keep]
        supports={};domains={}
        for column,row in zip(selected[:-1],rows[:-1]):
            actual_triangles=triangles[parent==row]
            supports[int(column)]=prism_union(actual_triangles,z_bounds)[0]
            domains[int(column)]=[(triangle,z_bounds) for triangle in actual_triangles]
        supports[int(selected[-1])]=wall_union(walls,z_bounds)[0]
        domains[int(selected[-1])]=[(segment,z_bounds) for segment in walls]
        lookup={int(column):(points[point_columns==column],point_weights[point_columns==column]) for column in selected}
        for p,w in lookup.values():assert abs(w.sum()-1)<2e-15
        labels={1368285:'far_clipped_union',1:'shared_vertex',75:'shared_long_edge',1622616:'shared_long_boundary_wall'}
        for target in selected[1:]:
            before=perf_counter();target=int(target)
            with (out/'events.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(dict(status='START_PAIR',pair=[0,target],elapsed_s=perf_counter()-timer))+'\n')
            p0,w0=lookup[0];pt,wt=lookup[target]
            point=wt@halfspace_action(p0,w0,targets=pt,point_action=_direct_points)[:,0]
            if anisotropic:
                forward=AnisotropicOuter(domains[target],supports[0],deadline)
                reverse=AnisotropicOuter(domains[0],supports[target],deadline)
            else:
                forward=AdaptiveOuter(supports[target],supports[0],deadline)
                reverse=AdaptiveOuter(supports[0],supports[target],deadline)
            history=[];previous=None
            for gate in (5e-5,1.25e-5,3.125e-6,7.8125e-7):
                values=np.array([forward.refine(gate),reverse.refine(gate)])
                scale=max(abs(values))
                reciprocal=float(abs(values[0]-values[1])/scale)
                change=None if previous is None else float(max(abs(values-previous))/scale)
                history.append(dict(requested_leaf_aggregate_gate=gate,forward=forward.receipt(),reverse=reverse.receipt(),
                                    raw_reciprocity_relative=reciprocal,successive_global_refinement_relative=change,
                                    directed_physical_per_f=[[v.real,v.imag] for v in values]))
                if previous is not None and reciprocal<5e-5 and change<5e-5:break
                previous=values.copy()
            else:raise RuntimeError(f'Unchanged nonself gate failed for {target}: {history}')
            diagonal=physical_self[[0,int(np.flatnonzero(selected==target)[0])]].real
            assert np.all(diagonal>0)
            def eigenvalues(off_diagonal):
                matrix=np.array([[diagonal[0],off_diagonal],[off_diagonal,diagonal[1]]])
                normalized=matrix/np.sqrt(diagonal[:,None]*diagonal[None,:])
                return np.linalg.eigvalsh(normalized).tolist()
            case=dict(pair=[0,target],case=labels[target],original_charge_rows=[int(rows[0]),int(rows[np.flatnonzero(selected==target)[0]])],
                      source_primitive_count=len(supports[0]),target_primitive_count=len(supports[target]),
                      saved_point_counts=[len(p0),len(pt)],point_block_per_f=[point.real,point.imag],
                      directed_physical_per_f=[[v.real,v.imag] for v in values],
                      directed_halfspace_delta_per_f=[[(v-point).real,(v-point).imag] for v in values],
                      point_block_relative_error=float(max(abs(values-point))/scale),
                      physical_halfspace_self_diagonal_real_per_f=diagonal.tolist(),
                      self_corrected_point_2x2_normalized_real_eigenvalues=eigenvalues(point.real),
                      nonself_corrected_2x2_normalized_real_symmetric_part_eigenvalues=eigenvalues(values.real.mean()),
                      seconds=perf_counter()-before,history=history)
            cases.append(case)
            (out/f'pair-0-{target}.json').write_text(json.dumps(case,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            print(json.dumps(dict(pair=[0,target],seconds=case['seconds'],point_relative_error=case['point_block_relative_error'],
                                  final=history[-1])),flush=True)
        for path,digest in pins.items():assert sha(path)==digest,f'Input changed during execution: {path}'
    except Exception:
        failure=traceback.format_exc()
    report=dict(status='PASS_SELECTED_ADAPTIVE_L02_NONSELF' if failure is None else 'STOP_SELECTED_ADAPTIVE_NONSELF',
                driver_sha256=sha(Path(__file__)),source_pins={str(p.relative_to(ROOT)):v for p,v in pins.items()},
                elapsed_s=perf_counter()-timer,budget_seconds=seconds,cases=cases,failure=failure,
                integration_partition='anisotropic prism/wall parameter boxes' if anisotropic else 'longest-edge tetra/triangle bisection',
                incomplete_pair_diagnostics=(dict(pair=[0,int(target)],forward=forward.receipt(),reverse=reverse.receipt())
                                             if failure and 'reverse' in locals() else None),
                scope='Only the explicit q0 pairs in this receipt. Exact analytic source integration plus adaptive positive outer quadrature; all directions and original support/q ownership retained. Successive-order and subdivision indicators are not rigorous error bounds. Only the halfspace nonself block is replaced; existing smooth deeper-stack and owned self terms stay separate. No full near-set completeness, unchanged-basis convergence or board-port claim.')
    (out/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},allow_nan=False),flush=True)
    return 0 if failure is None else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--seconds',type=float,default=120.)
    parser.add_argument('--anisotropic-only',action='store_true')
    args=parser.parse_args();raise SystemExit(run(args.output,args.seconds,args.anisotropic_only))
