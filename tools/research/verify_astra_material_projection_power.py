"""SPD Decap PI Evaluator v0.23.1: verify saved conforming fields by Fourier power.

No linear solve or Green integration. Keep the preceding operator-extracted
radiation report intact; compute positive far-field power directly here.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_material_conforming_projection as projected

ROOT=projected.ROOT
PINS={**projected.PINS,
    'tools/research/diagnose_astra_material_conforming_projection.py':'6d2228b6e1f08b3f3d01cc93e10d6b1143079023ba435a2c4327739b31e14315',
    'outputs/research/astra-material-conforming-projection-01/result.json':'8c7aea68c150ecb3d1a2bbb3f453d3b974a2b8bba14d958bf18de42d9381b8ec',
    'outputs/research/astra-material-conforming-projection-01/fields.npz':'2cdec173683e2715c97855f4d078a09b59b35484cd5799b23a8f032e3ba9268a'}


def run(output):
    start=monotonic();assert not output.exists()
    prior=projected.trial.material.prior;old=prior.old
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as s:
        tet=s['tetrahedra_m'];tri=s['triangles_m'];frequencies=s['frequencies_hz']
    centers=tet.mean(axis=1);dimensions=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+dimensions/2
    volumes=np.array([old.static.faces(t)[0] for t in tet]);qdata=old.qbasis(tet,4)
    drives=np.array([[1,0,1],[0,1,1j]],complex);cases=[];arrays={}
    report=json.loads((ROOT/'outputs/research/astra-material-conforming-projection-01/result.json').read_bytes())
    with np.load(ROOT/'outputs/research/astra-box-material-trial-test-01/fields.npz',allow_pickle=False) as a, np.load(ROOT/'outputs/research/astra-material-conforming-projection-01/fields.npz',allow_pickle=False) as b:
        t=a['real_current_transform'];h=np.einsum('tid,tin->tdn',(centers[:,None,:]-tet)/3,t.reshape(48,4,80))
        total=-tri.mean(axis=1).T @ a['real_face_divergence']
        for fi,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(old.source.EPS0*old.source.MU0)
            _,_,weights,directions=prior.bubble.far_bubbles(dimensions,k,4,20,48)
            amplitude=prior.rt0_fourier(qdata,volumes,h,total,center,k,directions)
            transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
            factor=omega*old.source.MU0*k/(16*np.pi*np.pi)
            for mi,method in enumerate(('conjugate','bilinear')):
                key=f'{method}_{fi:02d}';x=b[key+'_contrast_modal_current'] @ drives
                far=np.einsum('ndi,ij->ndj',transverse,x)
                radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
                c=report['cases'][2*fi+mi];ext=np.array(c['extinction_w']);loss=np.array(c['absorption_w']);reported_rad=np.array(c['radiation_w'])
                physical=(ext-loss-radiation)/(abs(ext)+abs(loss)+radiation)
                cases.append(dict(frequency_hz=float(frequency),method=method,radiation_w=radiation.tolist(),physical_power_defect_relative=physical.tolist(),
                    operator_radiation_difference_relative=float(np.linalg.norm(reported_rad-radiation)/np.linalg.norm(radiation)),
                    operator_radiation_difference_power_scaled_max=float(np.max(abs(reported_rad-radiation)/(abs(ext)+abs(loss)+radiation)))))
                arrays[key+'_transverse_far_amplitude']=far
                assert min(radiation)>0
    maximum=max(max(abs(np.array(c['physical_power_defect_relative']))) for c in cases if c['method']=='conjugate')
    assert maximum<1e-5
    with (output/'far-fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,weights=weights,directions=directions)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_SAVED_CONFORMING_FIELD_FOURIER_POWER',
        pins=PINS,script_sha256=old.source.sha(Path(__file__)),fields_sha256=old.source.sha(output/'far-fields.npz'),
        maximum_conjugate_physical_power_defect_relative=maximum,cases=cases,elapsed_s=monotonic()-start,
        scope='Astra physical readback, not a separate-agent review. Direct positive Fourier radiation of saved projected fields; no solve or Green replay. Confirms power but does not confer continuum, interface field, reciprocal approximation, terminal, board or PowerSI accuracy.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps({k:result[k] for k in ('status','maximum_conjugate_physical_power_defect_relative','elapsed_s')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
