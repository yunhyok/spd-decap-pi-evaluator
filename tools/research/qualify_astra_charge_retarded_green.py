"""SPD Decap PI Evaluator v0.23.1: neutral 3D retarded charge kernel.

Reuse accepted singular scalar blocks and saved volume distance moments. The
constant -ik term cancels exactly on the neutral distributional-divergence space.
"""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
from numpy.polynomial.legendre import leggauss
import qualify_astra_tetra_charge_green as charge
import qualify_astra_tetra_retarded_green as volume

ROOT = volume.ROOT
PINS = {
    'charge_helper': ('tools/research/qualify_astra_tetra_charge_green.py','aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9'),
    'charge_result': ('outputs/research/astra-tetra-charge-green-01/result.json','ad2dfe4c8aa6e7c9f6e6d7afd554469655f926f920a50969295665d010983b90'),
    'volume_helper': ('tools/research/qualify_astra_tetra_retarded_green.py','a575dce0fe2d0f87a3b041209d18055f061f04c6f9510f8b0a5cca0b609bf12d'),
    'volume_result': ('outputs/research/astra-tetra-retarded-green-02/result.json','51edd52ae9b58334847d9afba72902ed233767f0f977b2a6c56b7c1e267a6ca9'),
    'volume_blocks': ('outputs/research/astra-tetra-retarded-green-02/blocks.npz','c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2'),
}


def scalar_distance_moments(entities, order, length, deadline, volume_moments=None):
    points_weights = [charge.quadrature(entity, order) for entity in entities]
    moments = np.zeros((volume.DEGREE-1, len(entities), len(entities)))
    if volume_moments is not None:
        moments[:, :6, :6] = volume_moments
    # ponytail: this small dense control is not a full-board pairwise assembler.
    for a, (pa, wa) in enumerate(points_weights):
        for b, (pb, wb) in enumerate(points_weights):
            if volume_moments is not None and a < 6 and b < 6:
                continue
            for start in range(0, len(pa), 96):
                assert monotonic() < deadline, 'scalar regular deadline'
                radius = np.linalg.norm(pa[start:start+96,None]-pb[None],axis=2)/length
                assert radius.max() <= 1+1e-14
                weights = wa[start:start+96,None]*wb[None]
                power = radius.copy()
                for p in range(volume.DEGREE-1):
                    moments[p,a,b] += np.sum(weights*power)
                    power *= radius
    return moments


def scalar_tail(moments, k, length):
    assert abs(k*length) <= 1
    result = np.zeros_like(moments[0],dtype=complex)
    for n in range(2,volume.DEGREE+1):
        result += (-1j*k*length)**n/factorial(n)*moments[n-2]/length
    return result


def charge_radiation_reference(entities, charges, k):
    u,w = leggauss(16)
    phi = 2*np.pi*np.arange(32)/32
    directions = np.stack(np.broadcast_arrays(np.sqrt(1-u[:,None]**2)*np.cos(phi),
        np.sqrt(1-u[:,None]**2)*np.sin(phi),u[:,None]),axis=-1).reshape(-1,3)
    weights = np.broadcast_to(w[:,None]*2*np.pi/32,(16,32)).ravel()
    assert np.linalg.norm(charges.sum(axis=0)) < 1e-14*np.linalg.norm(charges)
    transform = np.zeros((len(directions),charges.shape[1]),complex)
    for entity, coefficients in zip(entities,charges):
        points, quadrature = charge.quadrature(entity,8)
        transform += (np.expm1(-1j*k*(directions @ points.T)) @ quadrature)[:,None]*coefficients
    return k/(4*np.pi)*np.sum(weights[:,None]*abs(transform)**2,axis=0)


def run(output):
    started = monotonic()
    assert not output.exists()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.values():
        assert volume.static.source.sha(ROOT/path) == expected,path
    receipt = json.loads((ROOT/PINS['charge_result'][0]).read_bytes())
    charge_path = ROOT/'outputs/research/astra-tetra-charge-green-01/charge.npz'
    assert volume.static.source.sha(charge_path) == receipt['charge_sha256']
    with np.load(charge_path,allow_pickle=False) as data:
        tetrahedra = data['tetrahedra_m']
        entities = list(tetrahedra)+list(data['triangles_m'])
        divergence = data['distributional_divergence']
        static_matrix = data['normalized_coulomb_matrix_per_m']
        uniform_flux = data['uniform_current_flux'].reshape(6,4,3)
        uniform_charge = data['uniform_current_charge']
    with np.load(ROOT/PINS['volume_blocks'][0],allow_pickle=False) as data:
        vector_moments = data['distance_moments']
    volumes = np.array([charge.measure(tetra) for tetra in tetrahedra])
    volume_moments = np.einsum('ai,pabij,bj->pab',uniform_flux[:,:,0],vector_moments,uniform_flux[:,:,0])/(volumes[:,None]*volumes[None])
    length = float(np.linalg.norm(np.ptp(tetrahedra.reshape(-1,3),axis=0)))
    frequencies = np.array([1e3,1e4,1e5,1e6,1e7,1e8])
    wave = 2*np.pi*frequencies*np.sqrt(volume.static.source.MU0*volume.static.source.EPS0)
    assert np.array_equal(divergence.sum(axis=0),np.zeros(24))
    history, previous = [], None
    for order in (4,8,12,16):
        moments = scalar_distance_moments(entities,order,length,started+110,volume_moments)
        tails = np.array([scalar_tail(moments,k,length) for k in wave])
        projected = divergence.T @ tails[-1] @ divergence
        energy = np.diag(uniform_charge.T @ tails[-1] @ uniform_charge)
        history.append(dict(order=order,polarization_tail_real_m3=energy.real.tolist(),
            projected_tail_relative_change=None if previous is None else float(np.linalg.norm(projected-previous)/np.linalg.norm(projected)),
            raw_tail_reciprocity=float(np.linalg.norm(tails[-1]-tails[-1].T)/np.linalg.norm(tails[-1]))))
        previous = projected
        print(json.dumps(history[-1]),flush=True)
    cases=[]
    for frequency,k,tail in zip(frequencies,wave,tails):
        loss = -np.diag(uniform_charge.T @ tail @ uniform_charge).imag
        reference = charge_radiation_reference(entities,uniform_charge,k)
        series_bound = len(entities)/length*np.exp(k*length)*(k*length)**(volume.DEGREE+1)/factorial(volume.DEGREE+1)
        cases.append(dict(frequency_hz=float(frequency),polarization_radiation_m3=loss.tolist(),
            independent_radiation_m3=reference.tolist(),radiation_relative_error=float(np.max(abs(loss-reference)/reference)),
            series_remainder_frobenius_bound_per_m=float(series_bound),series_bound_relative_to_tail=float(series_bound/np.linalg.norm(tail)),
            conjugacy_relative_error=float(np.linalg.norm(scalar_tail(moments,-k,length)-tail.conj())/np.linalg.norm(tail))))
    gates=dict(projected_tail_convergence=history[-1]['projected_tail_relative_change'] < 1e-3,
        raw_reciprocity=history[-1]['raw_tail_reciprocity'] < 1e-12,
        independent_neutral_radiation=all(c['radiation_relative_error'] < 1e-8 and min(c['polarization_radiation_m3']) > 0 for c in cases),
        bounded_series=all(c['series_bound_relative_to_tail'] < 1e-16 for c in cases),
        conjugacy=all(c['conjugacy_relative_error'] < 1e-14 for c in cases))
    with (output/'charge.npz').open('xb') as stream:
        np.savez_compressed(stream,static_matrix_per_m=static_matrix,regular_tail_per_m=tails,
            scalar_distance_moments=moments,distributional_divergence=divergence,
            frequencies_hz=frequencies,uniform_current_charge=uniform_charge)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='PASS_NEUTRAL_RETARDED_CHARGE_GREEN_CONTROL' if all(gates.values()) else 'STOP_NEUTRAL_RETARDED_CHARGE_GREEN_CONTROL',
        gates=gates,script_sha256=volume.static.source.sha(Path(__file__)),charge_sha256=volume.static.source.sha(output/'charge.npz'),
        pins=PINS,singular_charge_sha256=receipt['charge_sha256'],refinement=history,cases=cases,elapsed_s=monotonic()-started,
        scope='Normalized P0 volume/face exp(-ikR)/R on the six-tetra controlled box. The omitted stored constant matrix is exactly -ik times ones and is identically zero under B.T P B because every distributional-current column is neutral. All remaining retarded terms are retained to the recorded degree8 numerical bound. Saved q16 volume moments reused; new surface and mixed quadrature refined independently. Sphere-Fourier identity checks three neutral polarization charge distributions. This is not a material/port solve, source copper solid or board accuracy result.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2,allow_nan=False)
        stream.write('\n')
    print(json.dumps({key:result[key] for key in ('status','gates','elapsed_s')}),flush=True)
    return 0 if all(gates.values()) else 2


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
