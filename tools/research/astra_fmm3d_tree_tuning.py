"""SPD Decap PI Evaluator v0.23.1: unchanged-eps FMM leaf tuning/preflight.

Uses upstream lfmm3d_ndiv with every near interaction enabled. The full-domain
mode calls only pts_tree_mem and tiny expansion-size routines; it never calls
the full FMM. Its workspace estimate is conservative planning, not a bound on
every Fortran allocation or proof that a later process fits available memory.
"""
import argparse
import ctypes as ct
import json
from pathlib import Path
from time import monotonic

import numpy as np

from astra_openmp_point_action import OpenMPPointAction, ROOT, BUILD, sha
from apply_astra_owned_3d_green import TET_BARY, TRI_BARY, prepare, spread, gather

R = ROOT/'outputs/research'
PINS = {
    R/'astra-retained-sheet-current-green-20260912-02/current-support.npz':'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
    R/'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz':'a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b',
    R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz':'72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
}


class LeafTunedAction(OpenMPPointAction):
    def __init__(self, ndiv, threads=4):
        super().__init__(threads)
        assert isinstance(ndiv, int) and ndiv > 0
        self.ndiv = ndiv
        self.tuned = self.library.lfmm3d_ndiv_
        self.tuned.argtypes = [ct.c_void_p]*24
        self.tuned.restype = None

    def _real(self, sources, values, targets, eps):
        integer = lambda x: ct.c_int64(x)
        nd=integer(1); ns=integer(sources.shape[1]); nt=integer(0 if targets is None else targets.shape[1])
        ifcharge=integer(1); ifdipole=integer(0); iper=integer(0)
        ifpgh=integer(1 if targets is None else 0); ifpghtarg=integer(0 if targets is None else 1)
        ndiv=integer(self.ndiv); idivflag=integer(0); ifnear=integer(1); ier=integer(-1)
        precision=ct.c_double(eps)
        dummy=np.zeros(6); timeinfo=np.zeros(6)
        values=np.ascontiguousarray(values, dtype=np.float64)
        output=np.empty(ns.value if targets is None else nt.value)
        pot=output if targets is None else dummy
        pottarg=dummy if targets is None else output
        targets=dummy if targets is None else targets
        self.tuned(ct.byref(nd),ct.byref(precision),ct.byref(ns),sources.ctypes.data,
                   ct.byref(ifcharge),values.ctypes.data,ct.byref(ifdipole),dummy.ctypes.data,
                   ct.byref(iper),ct.byref(ifpgh),pot.ctypes.data,dummy.ctypes.data,dummy.ctypes.data,
                   ct.byref(nt),targets.ctypes.data,ct.byref(ifpghtarg),pottarg.ctypes.data,
                   dummy.ctypes.data,dummy.ctypes.data,ct.byref(ndiv),ct.byref(idivflag),
                   ct.byref(ifnear),timeinfo.ctypes.data,ct.byref(ier))
        if ier.value:
            raise RuntimeError(f'lfmm3d_ndiv returned ier={ier.value}')
        assert np.isfinite(output).all()
        self.last_timeinfo=timeinfo.tolist()
        return output

    def tree_memory(self, sources):
        sources=np.asfortranarray(sources.T)
        dummy=np.zeros((3,1),order='F')
        integers=[ct.c_int64(x) for x in (len(sources.T),0,0,self.ndiv,0,51,0,0,0,0,0)]
        ns,nt,idiv,ndiv,nlmin,nlmax,ifunif,iper,nlevels,nboxes,ltree=integers
        routine=self.library.pts_tree_mem_
        routine.argtypes=[ct.c_void_p]*13; routine.restype=None
        started=monotonic()
        routine(sources.ctypes.data,ct.byref(ns),dummy.ctypes.data,ct.byref(nt),
                ct.byref(idiv),ct.byref(ndiv),ct.byref(nlmin),ct.byref(nlmax),
                ct.byref(ifunif),ct.byref(iper),ct.byref(nlevels),ct.byref(nboxes),ct.byref(ltree))
        return dict(ndiv=self.ndiv,points=ns.value,levels=nlevels.value,boxes=nboxes.value,
                    tree_int64_length=ltree.value,tree_only_seconds=monotonic()-started)

    def expansion_size(self, eps=1e-9):
        # Exact isep=1 branch of upstream lfmm3dmain at this fixed eps.
        assert eps == 1e-9
        nlams=ct.c_int64(29); nphysical=np.zeros(29,np.int64)
        f=self.library.numthetafour_; f.argtypes=[ct.c_void_p]*2;f.restype=None
        f(nphysical.ctypes.data,ct.byref(nlams))
        nterms=ct.c_int64(0); precision=ct.c_double(eps)
        f=self.library.l3dterms_;f.argtypes=[ct.c_void_p]*2;f.restype=None
        f(ct.byref(precision),ct.byref(nterms))
        assert nphysical.min()>0 and nterms.value>0
        return dict(nlams=29,nexptotp=int(nphysical.sum()),nterms=nterms.value)


def actual_g(output):
    source=list(PINS)[2]
    assert sha(source)==PINS[source]
    cache=R/'astra-g-complete-self-green-20260912/self-blocks.npz'
    reference=R/'astra-g-all-pair-current-charge-action-20260912/action.npz'
    assert sha(cache)=='d62fb76057f25ab3a68a670c5509922c56c598437b5f3e51da4d321b332c2cca'
    assert sha(reference)=='6b4f30c58258fd77855fb259cb8c878dc63db48642e9a2da4e28d43a3062a4b2'
    with np.load(source) as z:
        tet=z['volume_charge_vertices_um']*1e-6; tri=z['surface_charge_vertices_um']*1e-6
        columns=z['local_current_face_ids'];signs=z['local_current_face_signs'];ni=int(z['local_resistance_shape'][0])
    with np.load(cache) as z:
        prepared=prepare(tet,tri,columns,signs,ni,z['static_vector_self_h'],z['static_scalar_self_per_m'])
    with np.load(reference) as z:
        current=z['currents'][0];charge=z['charges'][0]
        frozen=(z['inductance_action_h_a'][0],z['potential_raw_action_c_per_m'][0])
    values=spread(prepared,current,charge);points=prepared['points']
    rows=np.linspace(0,len(points)-1,32,dtype=int)
    distance=np.linalg.norm(points[rows,None]-points[None,:],axis=2)
    direct=(np.divide(1.,distance,out=np.zeros_like(distance),where=distance>0)/(4*np.pi))@values
    receipts=[]
    for ndiv in (800,1600):
        action=LeafTunedAction(ndiv,4)
        started=monotonic();potential=action(points,values,1e-9);seconds=monotonic()-started
        results=gather(prepared,potential,current,charge)
        errors=[float(np.linalg.norm(x-y)/np.linalg.norm(y)) for x,y in zip(results,frozen)]
        point_errors=[float(np.linalg.norm(potential[rows,c]-direct[:,c])/np.linalg.norm(direct[:,c])) for c in range(4)]
        assert max(errors+point_errors)<1e-9,(errors,point_errors)
        receipt=dict(ndiv=ndiv,seconds=seconds,relative_L_P=errors,direct_target_relative=point_errors,
                     tree=action.tree_memory(points),last_channel_timeinfo=action.last_timeinfo)
        receipts.append(receipt);print(json.dumps(receipt),flush=True)
    report=dict(status='PASS_ACTUAL_G_NDIV',points=len(points),eps=1e-9,threads=4,nd=1,ifnear=1,
                checks=receipts,driver_sha256=sha(Path(__file__)),
                baseline_receipt_sha256=sha(R/'astra-openmp-actual-g-20260912.json'))
    output.write_text(json.dumps(report,indent=2)+'\n')


def full_points():
    for p,h in PINS.items():
        assert sha(p)==h,p
    points=np.empty((11127786,3))
    start=0
    with np.load(list(PINS)[0]) as z:
        pieces=z['piece_triangle_xy_m']
    for first in range(0,len(pieces),16384):
        piece=pieces[first:first+16384]
        xy=np.einsum('qi,tij->tqj',TRI_BARY,piece)
        count=len(piece)*6
        view=points[start:start+count].reshape(-1,6,3)
        view[:,:,:2]=np.repeat(xy,2,axis=1)
        view[:,:,2]=65e-6+np.tile([-1.,1.],3)*10e-6/np.sqrt(3.)
        start+=count
    assert start==9502686
    with np.load(list(PINS)[1]) as z:
        xyz=z['vertices_um'];tet=xyz[z['cells']];tri=xyz[z['face_vertices'][z['free_surface_face_ids']]]
    with np.load(list(PINS)[2]) as z:
        g_tet=z['volume_charge_vertices_um'];g_tri=z['surface_charge_vertices_um']
    for vertices,rule in ((tet,TET_BARY),(g_tet,TET_BARY),(tri,TRI_BARY),(g_tri,TRI_BARY)):
        for first in range(0,len(vertices),16384):
            p=np.einsum('qi,tid->tqd',rule,vertices[first:first+16384]*1e-6).reshape(-1,3)
            points[start:start+len(p)]=p;start+=len(p)
    assert start==len(points)
    return points


def host_memory():
    class Memory(ct.Structure):
        _fields_=[('length',ct.c_uint32),('load',ct.c_uint32)]+[(x,ct.c_uint64) for x in
                  ('total_physical','available_physical','total_pagefile','available_pagefile',
                   'total_virtual','available_virtual','available_extended_virtual')]
    m=Memory();m.length=ct.sizeof(m)
    assert ct.windll.kernel32.GlobalMemoryStatusEx(ct.byref(m))
    return {x:int(getattr(m,x)) for x in ('total_physical','available_physical','available_pagefile')}


def preflight(output):
    started=monotonic();points=full_points();memory=host_memory()
    receipts=[]
    report=dict(status='IN_PROGRESS_TREE_ONLY',domain='retained L02 current cubature plus existing PWR/G shared current-charge points',
                sheet_current_points=9502686,pwr_g_points=1625100,total_points=len(points),
                eps=1e-9,nd=1,threads=4,ifnear=1,memory_snapshot=memory,checks=receipts,
                driver_sha256=sha(Path(__file__)),pins={str(p):h for p,h in PINS.items()},
                scope='Tree only, no full FMM allocation or action; current point domain, not the distinct sheet-charge cubature.')
    cap=min(.6*memory['available_physical'],.5*memory['total_physical'])
    report['conservative_workspace_cap_bytes']=int(cap)
    for ndiv in (400,800,1600):
        action=LeafTunedAction(ndiv,4);sizes=action.expansion_size()
        receipt=action.tree_memory(points);n=receipt['boxes'];order=sizes['nterms']
        plane=16*6*sizes['nexptotp']*n
        # Main + worst-case ghost plane waves, both multipole/local arrays,
        # generous list/metadata margin, point buffers and thread scratch.
        planning=2*plane+32*(order+1)*(2*order+1)*n+8*1000*n+160*len(points)+2*1024**3
        receipt.update(sizes,main_plane_wave_bytes=plane,planning_workspace_bytes=planning,
                       fits_planning_cap=bool(planning<cap))
        receipts.append(receipt);report['elapsed_s']=monotonic()-started
        output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(receipt),flush=True)
    report['status']='COMPLETED_TREE_ONLY_PREFLIGHT'
    report['elapsed_s']=monotonic()-started
    output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('g','preflight'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();assert not args.output.exists()
    (actual_g if args.mode=='g' else preflight)(args.output)
