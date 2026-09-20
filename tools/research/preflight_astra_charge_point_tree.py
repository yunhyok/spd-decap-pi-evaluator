"""SPD Decap PI Evaluator v0.23.1: distinct charge-domain tree-only preflight.

Loads the saved charge cubature directly, preserving its nonsorted point-to-
charge-column map. No current cubature is substituted. FMM actions and large
plane-wave arrays are never evaluated here. Full-target group tree counts do
not strictly bound every changed bounding box of a later target chunk.
"""
import ctypes as ct
import itertools
import json
from pathlib import Path
from time import monotonic

import numpy as np

from astra_fmm3d_tree_tuning import LeafTunedAction, host_memory
from astra_openmp_point_action import ROOT, sha
from apply_astra_owned_3d_green import TET_BARY, TRI_BARY

R=ROOT/'outputs/research'
PINS={
    R/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz':'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
    R/'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz':'a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b',
    R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz':'72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
}


def point_geometry():
    for p,h in PINS.items():
        assert sha(p)==h,p
    paths=list(PINS)
    with np.load(paths[0]) as z:
        sheet=z['quadrature_points_um']*1e-6;columns=z['spread_col'];weights=z['spread_data']
        rows=z['charge_row_ids'];ptr=z['spread_row_ptr'];kinds=z['support_kind']
        assert np.array_equal(z['spread_shape'],[7526978,2440492])
        assert len(sheet)==len(columns)==len(weights)==7526978
        assert len(rows)==2440492 and np.unique(rows).size==len(rows)
        assert ptr[0]==0 and ptr[-1]==len(sheet) and np.all(np.diff(ptr)==1)
        assert columns.min()==0 and columns.max()==len(rows)-1 and (weights>0).all()
        norm=float(np.max(abs(np.bincount(columns,weights=weights,minlength=len(rows))-1)))
        assert norm<2e-15
        assert np.array_equal(z['contact_charge_columns'],np.flatnonzero(kinds==1))
        assert np.array_equal(rows[kinds==0],z['free_charge_row_ids'])
        assert np.array_equal(rows[kinds==1],z['contact_charge_row_ids'])
        assert np.array_equal(rows[kinds==2],z['exterior_charge_row_ids'])
        normalization=dict(sheet_charge_rows=len(rows),sheet_points=len(sheet),maximum_column_sum_error=norm,
                           sheet_point_columns_monotonic=bool(np.all(np.diff(columns)>=0)),
                           rule=str(z['quadrature_rule'][0]),sheet_z_range_m=[float(sheet[:,2].min()),float(sheet[:,2].max())])
    with np.load(paths[1]) as z:
        xyz=z['vertices_um'];tet=xyz[z['cells']];tri=xyz[z['face_vertices'][z['free_surface_face_ids']]]
    with np.load(paths[2]) as z:
        assert np.array_equal(rows,z['retained_sheet_charge_row_ids'])
        gt=z['volume_charge_vertices_um'];gf=z['surface_charge_vertices_um']
    points=np.empty((9152078,3));points[:len(sheet)]=sheet;start=len(sheet)
    groups=[];qstart=len(rows)
    for name,vertices,rule in [('PWR volume',tet,TET_BARY),('G volume',gt,TET_BARY),
                               ('PWR free surface',tri,TRI_BARY),('G free surface',gf,TRI_BARY)]:
        first=start
        for index in range(0,len(vertices),16384):
            p=np.einsum('qi,tid->tqd',rule,vertices[index:index+16384]*1e-6).reshape(-1,3)
            points[start:start+len(p)]=p;start+=len(p)
        groups.append(dict(name=name,point_interval=[first,start],charge_interval=[qstart,qstart+len(vertices)],
                           points_per_charge=len(rule),point_weight=1/len(rule)))
        qstart+=len(vertices)
    assert start==len(points) and qstart==2883898
    normalization.update(total_charge_rows=qstart,total_points=start,pwr_g_order=groups,
                         sheet_columns_exactly_match_coupling_retained_charge_rows=True)
    return points,normalization


def group_tree(action,sources,targets):
    sources=np.asfortranarray(sources.T);targets=np.asfortranarray(targets.T)
    ns,nt,idiv,ndiv,nlmin,nlmax,ifunif,iper,nlevels,nboxes,ltree=[ct.c_int64(x) for x in
        (sources.shape[1],targets.shape[1],0,3200,0,51,0,0,0,0,0)]
    f=action.library.pts_tree_mem_;f.argtypes=[ct.c_void_p]*13;f.restype=None
    t=monotonic()
    f(sources.ctypes.data,ct.byref(ns),targets.ctypes.data,ct.byref(nt),ct.byref(idiv),ct.byref(ndiv),
      ct.byref(nlmin),ct.byref(nlmax),ct.byref(ifunif),ct.byref(iper),ct.byref(nlevels),ct.byref(nboxes),ct.byref(ltree))
    return dict(source_points=ns.value,target_points=nt.value,boxes=nboxes.value,levels=nlevels.value,
                tree_int64_length=ltree.value,tree_seconds=monotonic()-t)


def run(output):
    assert not output.exists()
    started=monotonic();points,normalization=point_geometry()
    action=LeafTunedAction(3200,4);sizes=action.expansion_size();memory=host_memory()
    interface=25e-6;tolerance=32*np.finfo(float).eps*max(1e-6,abs(interface))
    points[abs(points[:,2]-interface)<=tolerance,2]=interface
    side=np.sign(points[:,2]-interface).astype(np.int8)
    indices={k:np.flatnonzero(side==k) for k in (-1,0,1)}
    report=dict(status='IN_PROGRESS_CHARGE_TREE_ONLY',program='SPD Decap PI Evaluator',version='0.23.1',
                normalization=normalization,eps=1e-9,nd=1,ndiv=3200,threads=4,ifnear=1,
                source_group_points={str(k):len(v) for k,v in indices.items()},memory_snapshot=memory,
                planning_cap_bytes=int(min(.6*memory['available_physical'],.5*memory['total_physical'])),
                checks=[],pins={str(p):h for p,h in PINS.items()},driver_sha256=sha(Path(__file__)),
                scope='Distinct saved sheet-charge point domain plus frozen PWR/G order. Tree counts only; no FMM action or allocation certificate.')
    def record(name,result):
        n=result['boxes'];order=sizes['nterms'];plane=16*6*sizes['nexptotp']*n
        common=2*plane+32*(order+1)*(2*order+1)*n+8*1000*n+2*1024**3
        full=common+160*(result['source_points']+result['target_points'])
        chunk=common+160*(result['source_points']+min(result['target_points'],131072))
        result.update(name=name,main_plane_wave_bytes=plane,full_target_planning_bytes=full,
                      chunk_target_planning_bytes=chunk,chunk_plan_fits_cap=bool(chunk<report['planning_cap_bytes']),
                      target_chunk=131072,**sizes)
        report['checks'].append(result);report['elapsed_s']=monotonic()-started
        output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(result),flush=True)
    complete=action.tree_memory(points)
    record('all charge points source/self',dict(source_points=len(points),target_points=0,**{k:v for k,v in complete.items() if k not in ('points','ndiv')}))
    for k,label in ((-1,'air'),(1,'ABF'),(0,'interface')):
        selected=points[indices[k]]
        if not len(selected):
            continue
        record(label+' direct sources to all charge targets',group_tree(action,selected,points))
        if k:
            mirrored=selected.copy();mirrored[:,2]=2*interface-mirrored[:,2]
            record(label+' reflected sources to same-host targets',group_tree(action,mirrored,selected))
    report['status']='COMPLETED_CHARGE_HALFSPACE_TREE_PREFLIGHT'
    report['elapsed_s']=monotonic()-started
    report['limitations']=[
        'Planning includes a worst-case ghost-array term and buffer margins, but not a certified full-process allocation bound.',
        'Group counts use all actual targets; a target chunk can change root bounding-box alignment and therefore box count.',
        'Halfspace Python copies, full outputs, charge spread/gather and the separate deep FFT operator also require resident memory.',
        'Current-domain 9502686 sheet points were not reused or rechecked.',
        'Restored electrodes outside z0..75 are not included and require their own geometry/operator preflight.']
    output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
