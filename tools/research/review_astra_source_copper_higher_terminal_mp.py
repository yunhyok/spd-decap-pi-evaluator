"""SPD Decap PI Evaluator v0.23.1: MP60 readback audit of saved terminal systems."""
from pathlib import Path
from time import monotonic
import argparse, hashlib, json, traceback
from decimal import Decimal, getcontext
import numpy as np

PROGRAM="SPD Decap PI Evaluator"; VERSION="0.23.1"
REPRODUCTION_TOLERANCE=1e-10
ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'tools/research/diagnose_astra_source_copper_higher_terminal.py'
RESULT=ROOT/'outputs/research/astra-source-copper-higher-terminal-01/result.json'
FIELDS=ROOT/'outputs/research/astra-source-copper-higher-terminal-01/fields.npz'
PINS={str(SOURCE.relative_to(ROOT)):'2b09aab847491b73c87753a3a481626896d99aed9789b714c6529bc9ee899d98',str(RESULT.relative_to(ROOT)):'541026b093fa3ac946e04c40c52ac56accb39b36c4893aca5aceca71e68f3b25',str(FIELDS.relative_to(ROOT)):'edb04ce6c5381ea8eca57f5aab9fc10f34bb9b864878d67c2df422f681b9a7b7'}
TARGET_FREQUENCIES={1000.,1e6,1e7,1e8}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):
    with path.open('x',encoding='utf8') as f: json.dump(data,f,indent=2,allow_nan=False)
def decimal_solve(a,b):
    """Exact-binary64 input, 60-decimal-digit complex Gaussian elimination."""
    n=len(a); r=2*n; columns=np.asarray(b).shape[1]
    rows=[]
    for i in range(n):
        ar=[Decimal.from_float(float(x.real)) for x in a[i]]; ai=[Decimal.from_float(float(x.imag)) for x in a[i]]
        br=[Decimal.from_float(float(x.real)) for x in b[i]]; bi=[Decimal.from_float(float(x.imag)) for x in b[i]]
        rows.append(ar+[-x for x in ai]+br); rows.append(ai+ar+bi)
    for k in range(r):
        pivot=max(range(k,r),key=lambda i:abs(rows[i][k]))
        if not rows[pivot][k]: raise RuntimeError('singular saved system')
        rows[k],rows[pivot]=rows[pivot],rows[k]; pk=rows[k][k]
        for i in range(k+1,r):
            factor=rows[i][k]/pk
            if factor:
                for j in range(k+1,r+columns): rows[i][j]-=factor*rows[k][j]
            rows[i][k]=Decimal(0)
    out=[[Decimal(0)]*columns for _ in range(r)]
    for i in range(r-1,-1,-1):
        for col in range(columns): out[i][col]=(rows[i][r+col]-sum(rows[i][j]*out[j][col] for j in range(i+1,r)))/rows[i][i]
    return np.array([[complex(float(out[i][j]),float(out[i+n][j])) for j in range(columns)] for i in range(n)])

def run(output):
    start=monotonic()
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True); (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,expected in PINS.items():
            actual=sha(ROOT/path)
            if actual!=expected: raise RuntimeError(f'pin mismatch {path}: {actual}')
        source_record=json.loads(RESULT.read_text(encoding='utf8'))
        if source_record['status']!='STOP_SOURCE_HIGHER_COPPER_TERMINAL_DIAGNOSTIC': raise RuntimeError('unexpected source status')
        getcontext().prec=60
        with np.load(FIELDS,allow_pickle=False) as data: saved={k:data[k] for k in data.files}
        mass=saved['mass']; ncurrent=mass.shape[0]
        if ncurrent!=48: raise RuntimeError(f'expected 48 saved current coordinates, got {ncurrent}')
        if saved['terminal_potential_map'].shape[1]!=4 or saved['differential_voltage_map'].shape!=(4,2): raise RuntimeError('terminal maps')
        vmap=saved['terminal_potential_map']; dmap=saved['differential_voltage_map']; bc=saved['contact_divergence']
        cases=[]
        saved_frequencies=[]
        for item in source_record['cases']:
            if item['frequency_hz'] not in saved_frequencies: saved_frequencies.append(item['frequency_hz'])
        for entry in source_record['cases']:
            frequency=float(entry['frequency_hz'])
            if frequency not in TARGET_FREQUENCIES: continue
            tag=entry['kernel_tag']; ordinal=saved_frequencies.index(entry['frequency_hz'])
            # Cases are saved tag-major within each frequency; locate by names, not list order.
            matches=[name for name in saved if name.startswith(f'{tag}_') and name.endswith('_system')]
            candidates=[]
            for name in matches:
                index=int(name.split('_')[1]); key=f'{tag}_{index:02d}_'
                if index==ordinal: candidates.append(key)
            if len(candidates)!=1: raise RuntimeError(f'saved case lookup {tag} {frequency}: {candidates}')
            key=candidates[0]; system=saved[key+'system']; rhs=saved[key+'rhs']; current=saved[key+'current']; y0=saved[key+'port_admittance']; ti0=saved[key+'terminal_current']
            if system.shape!=(ncurrent+10,ncurrent+10) or rhs.shape[0]!=ncurrent+10: raise RuntimeError('n+10 system layout')
            high=decimal_solve(system,rhs)
            current_high=high[:ncurrent]*saved[key+'current_scale'][:,None]
            # The stored terminal-current conversion is a single material factor.
            raw0=vmap.T@bc@current; raw_high=vmap.T@bc@current_high
            mask=np.abs(raw0)>np.finfo(float).tiny
            gains=(ti0[mask]/raw0[mask]); gain=np.mean(gains)
            if np.max(np.abs(gains-gain))/max(abs(gain),np.finfo(float).tiny)>1e-12: raise RuntimeError('terminal-current conversion is not scalar')
            y_high=dmap.T@(gain*raw_high); z_high=1/np.diag(y_high); z0=1/np.diag(y0)
            delta=current_high-current
            current_error=float(np.sqrt(np.trace(delta.conj().T@mass@delta).real/np.trace(current.conj().T@mass@current).real))
            y_error=float(np.linalg.norm(y_high-y0)/np.linalg.norm(y0)); z_error=float(np.linalg.norm(z_high-z0)/np.linalg.norm(z0))
            unscaled_condition=float(np.linalg.cond(system))
            cases.append(dict(frequency_hz=frequency,kernel_tag=tag,system_shape=list(system.shape),
                source_condition_equilibrated=float(entry['condition_equilibrated']),unscaled_binary64_condition_2=unscaled_condition,
                current_mass_relative_error=current_error,port_admittance_relative_error=y_error,port_impedance_relative_error=z_error,
                fixed_reproduction_tolerance=REPRODUCTION_TOLERANCE,
                passes=bool(max(current_error,y_error,z_error)<REPRODUCTION_TOLERANCE)))
        if len(cases)!=8: raise RuntimeError(f'expected 8 saved systems, got {len(cases)}')
        gates={'source_is_saved_stop':True,'all_mp60_reproductions':all(c['passes'] for c in cases)}
        result=dict(program=PROGRAM,version=VERSION,status=('PASS_' if all(gates.values()) else 'STOP_')+'SAVED_SOURCE_COPPER_HIGHER_TERMINAL_MP60_AUDIT',
            pins=PINS,script_sha256=sha(Path(__file__)),source_status=source_record['status'],ncurrent=ncurrent,system_dimension=ncurrent+10,
            decimal_digits=60,fixed_reproduction_tolerance=REPRODUCTION_TOLERANCE,
            frequencies_hz=sorted(TARGET_FREQUENCIES),gates=gates,cases=cases,
            maximum_current_mass_relative_error=max(c['current_mass_relative_error'] for c in cases),maximum_port_admittance_relative_error=max(c['port_admittance_relative_error'] for c in cases),maximum_port_impedance_relative_error=max(c['port_impedance_relative_error'] for c in cases),elapsed_s=monotonic()-start,
            rejected_attempt_01='Its PASS gate multiplied an unscaled condition number by machine epsilon, producing a tolerance above one; the numerical values remain preserved but that acceptance is rejected.',
            scope='Readback-only MP60 solve of saved binary64 48-current/58-equation terminal matrices. A fixed 1e-10 reproduction gate is independent of the diagnostic unscaled condition number. It does not reconstruct Green, fields, modes, contacts, geometry, or physics; it measures float64 linear-solve sensitivity only and does not overturn the source terminal STOP interface gate.')
        write(output/'result.json',result); print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as e:
        write(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_SAVED_SOURCE_COPPER_HIGHER_TERMINAL_MP60_AUDIT',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc(),elapsed_s=monotonic()-start)); raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True); a=p.parse_args(); run(a.output.resolve())
