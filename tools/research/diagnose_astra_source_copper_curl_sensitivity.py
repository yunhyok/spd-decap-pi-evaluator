"""SPD Decap PI Evaluator v0.23.1: saved52 precision and six-mode attribution.

No Green integration or new geometry. High precision checks the stored float64
equations, not assembly error. Mode removal measures joint-model sensitivity.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import mpmath as mp
import diagnose_astra_source_copper_curl_terminal as terminal

ROOT=terminal.ROOT
old=terminal.old
PINS={**terminal.PINS,
    'tools/research/diagnose_astra_source_copper_curl_terminal.py':'fa3996de5f1b43b78c1ebdb107a3f8f6db2c6a0d13b10cdcea5ef4f5b01c95fd',
    'outputs/research/astra-source-copper-curl-terminal-01/result.json':'0fd925a78ffe4931b5c363431b16bdbcc267bf37b1e306d7b835cbf309040ef6',
    'outputs/research/astra-source-copper-curl-terminal-01/fields.npz':'7680fb2623c7d59bd3d77767d6e966c9bead34f571537f2ab429dd7bd2b0262d'}


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-source-copper-curl-terminal-01/fields.npz',allow_pickle=False) as s:data={k:s[k] for k in s.files}
    mass=data['mass'];bc=data['contact_divergence'];vmap=data['terminal_potential_map'];dmap=data['differential_voltage_map']
    rows,properties=old.source.source_inputs();precision=[];attribution=[];arrays={};mp.mp.dps=60
    masks=dict(z_odd_only=[36,39],z_even_only=[37,40],y_even_only=[38,41],both_even_only=[37,38,40,41])
    for fi,frequency in enumerate(old.source.FREQUENCIES):
        prefix=f'v20f36_{fi:02d}_';matrix=data[prefix+'system'];rhs=data[prefix+'rhs']
        fullscale=np.r_[data[prefix+'current_scale'],data[prefix+'charge_scale'],np.ones(2)]
        reference=data[prefix+'current'];yref=data[prefix+'port_admittance'];zref=1/yref.diagonal()
        _,_,_,gammas=old.source.materials(rows,properties,frequency)
        ratio=1-2j*np.pi*frequency*old.source.EPS0/gammas[0]
        if frequency in (1000,1e6,1e7,1e8):
            # Promote the exact stored binary floats; do not round-trip through text.
            m=mp.matrix([[mp.mpc(float(v.real),float(v.imag)) for v in row] for row in matrix])
            b=mp.matrix([[mp.mpc(float(v.real),float(v.imag)) for v in row] for row in rhs]);solved=np.empty((52,2),complex)
            for side in range(2):
                answer=mp.lu_solve(m,b[:,side]);solved[:,side]=[complex(v) for v in answer]
            modal=fullscale[:42,None]*solved[:42];delta=modal-reference;y=dmap.T @ vmap.T @ bc @ modal/ratio;z=1/y.diagonal()
            case=dict(frequency_hz=float(frequency),decimal_digits=mp.mp.dps,
                current_mass_vs_float64_relative=float(np.sqrt(np.trace(delta.conj().T @ mass @ delta).real/np.trace(modal.conj().T @ mass @ modal).real)),
                port_admittance_vs_float64_relative=float(np.linalg.norm(y-yref)/np.linalg.norm(y)),
                shorted_z_vs_float64_relative=(abs(z-zref)/abs(z)).tolist())
            precision.append(case);arrays[f'mp60_{fi:02d}_scaled_solution']=solved;print(json.dumps(dict(precision=case)),flush=True)
        for name,selected in masks.items():
            indices=np.r_[0:36,selected,42:52];a=matrix[np.ix_(indices,indices)];b=rhs[indices]
            solved,condition,backward=old.reference.coarse.scaled_solve(a,b)
            full=np.zeros((52,2),complex);full[indices]=solved;modal=fullscale[:42,None]*full[:42]
            y=dmap.T @ vmap.T @ bc @ modal/ratio;z=1/y.diagonal();delta=modal-reference
            case=dict(frequency_hz=float(frequency),retained_curls=name,current_coordinates=36+len(selected),
                condition_equilibrated=condition,backward_equilibrated=backward,
                shorted_port_impedance_ohm=[[float(v.real),float(v.imag)] for v in z],
                complex_z_difference_from_all6_relative_all6=(abs(z-zref)/abs(zref)).tolist(),
                resistance_difference_from_all6_relative_all6=((z.real-zref.real)/zref.real).tolist(),
                current_mass_difference_from_all6_relative_all6=float(np.sqrt(np.trace(delta.conj().T @ mass @ delta).real/np.trace(reference.conj().T @ mass @ reference).real)))
            attribution.append(case);arrays[f'{name}_{fi:02d}_current']=modal
    assert max(max(c['shorted_z_vs_float64_relative']) for c in precision)<1e-8
    assert max(c['current_mass_vs_float64_relative'] for c in precision)<1e-8
    assert max(c['backward_equilibrated'][-1] for c in attribution)<1e-10
    with (output/'solutions.npz').open('xb') as stream:np.savez_compressed(stream,**arrays)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_STORED52_PRECISION_WITH_MODE_SENSITIVITY',pins=PINS,
        script_sha256=old.source.sha(Path(__file__)),solutions_sha256=old.source.sha(output/'solutions.npz'),elapsed_s=monotonic()-start,
        precision=precision,mode_removal=attribution,mpmath_version=mp.__version__,
        scope='60-decimal LU re-solves the exact stored binary64 52 equations at four frequencies, bounding solve-roundoff sensitivity only. Four specified curl subsets are solved from stored submatrices; z-odd/even and y-even names describe local polynomial shapes, not separable physical skin/proximity mechanisms. Removing a function measures model sensitivity, not its share of a measured PowerSI error. No new Green integration, interface approval, spatial convergence, board result or end-to-end timing claim.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);destination=p.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_COPPER_CURL_SENSITIVITY',error=repr(error)),indent=2),encoding='utf-8')
        raise
