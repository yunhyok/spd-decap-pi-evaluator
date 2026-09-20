"""SPD Decap PI Evaluator v0.23.1: two-sided separated-source Galerkin check."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
import qualify_astra_joint_chebyshev_sources as source

ROOT = source.ROOT
PINS = {
    'tools/research/qualify_astra_joint_chebyshev_sources.py': '82748eba7c94ae547f06011cf9fca706c0b81e505d91c4576f5f9ea1e851a029',
    'outputs/research/astra-joint-chebyshev-sources-02/result.json': '5e3f87ef2b8d3905652c5618c8a6f4132070e3c7d619567f6cb8ced616ce9dd1',
    'outputs/research/astra-joint-chebyshev-sources-02/compression.npz': '507b98a5da6186c767ebc4ed986e86ab467022b16e781f87ecc857acf9cd7910',
    'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json': '583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790',
    'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz': '9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92',
}


def select_pairs(radius):
    ledger = json.loads((ROOT/'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json').read_text())
    instances = ledger['instances']
    xy = np.array([x['translation_xy_um'] for x in instances])*1e-6
    delta = xy-xy[0]
    lengths = np.linalg.norm(delta, axis=1)
    admissible = lengths >= 4*radius
    masks = [admissible, admissible & (np.abs(delta[:, 1]) < 1e-12),
        admissible & (np.abs(delta[:, 1]) > 1e-12)]
    ids = [int(np.argmin(np.where(mask, lengths, np.inf))) for mask in masks]
    ids.append(int(np.argmax(lengths)))
    ids = sorted(set(ids))
    assert all(admissible[i] for i in ids)
    return [dict(source=instances[0], observer=instances[i],
        displacement_m=[*delta[i], 0.], center_distance_m=float(lengths[i])) for i in ids]


def pair_blocks(points, weights, total, displacement, transform, frequency):
    """Bilinear Green pairing; real lift functions retain transpose reciprocity."""
    distance = np.linalg.norm(points[:, None]+displacement-points[None], axis=2)
    assert distance.min() > 0
    k = 2*np.pi*frequency/source.geometry.C0
    x = k*distance
    assert x.max() < 1
    term = k*x*x/6
    tail = term.copy()
    for n in range(2, 13):
        term = -term*x*x/((2*n)*(2*n+1))
        tail += term
    current = np.einsum('pmd,mn->pnd', weights[:, :15].reshape(-1, 5, 3), transform)
    assert np.max(np.abs(current.imag)) == 0
    current = current.real
    monopole_j = transform.T @ total[:15].reshape(5, 3)
    charge = weights[:, 15:]
    output = {}
    for name, kernel in [('real', np.cos(x)/distance), ('tail', tail)]:
        j = sum(current[:, :, axis].T @ (kernel @ current[:, :, axis]) for axis in range(3))*1e-7
        q = charge.T @ (kernel @ charge)
        output[f'current_{name}'] = j
        output[f'charge_{name}'] = q
    output['current_imaginary'] = output['current_tail']-1e-7*k*(monopole_j @ monopole_j.T)
    output['charge_imaginary'] = output['charge_tail']-k*np.outer(total[15:], total[15:])
    return output


def relative(a, b):
    return float(np.linalg.norm(a-b)/max(np.linalg.norm(b), 1e-300))


def run(output):
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, arrays, history, failure = monotonic(), {}, [], None
    try:
        for path, pin in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
        with np.load(ROOT/'outputs/research/astra-joint-chebyshev-sources-02/compression.npz') as d:
            lower, upper = d['lower_m'], d['upper_m']
            total = d['authoritative_monopole'][12:]
            rules = {8: (d['proxy_points_n8'], d['proxy_weights_n8'][:, 12:])}
        with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as d:
            gram = d['energy_gram_ohm']
        transform = np.linalg.inv(np.linalg.cholesky(gram).T)
        assert np.max(np.abs(transform.T @ gram @ transform-np.eye(5))) < 1e-12
        pairs = select_pairs(np.linalg.norm(upper-lower)/2)
        arrays.update(authoritative_monopole=total, current_R_whitening=transform,
            pair_displacements_m=np.array([p['displacement_m'] for p in pairs]))
        joint, triangles, currents, charge, _, _, check_total = source.load_densities()
        assert np.array_equal(total, check_total[12:])
        points, channels = source.source_rule(4, joint, triangles, currents, charge)
        for order in (10, 12):
            before = monotonic()
            proxy, weighted, moments = source.interpolate_sources(points, channels[:, 12:], lower, upper, order, started+180)
            rules[order] = proxy, weighted
            print(json.dumps(dict(stage='compress_q4', order=order, elapsed_s=monotonic()-before, moments=moments)), flush=True)
        for order, (points, weights) in rules.items():
            before = monotonic()
            blocks = []
            for pair in pairs:
                for frequency in source.FREQUENCIES:
                    assert monotonic() < started+180, 'pair experiment deadline'
                    blocks.append(pair_blocks(points, weights, total, np.array(pair['displacement_m']), transform, frequency))
            for name in blocks[0]:
                arrays[f'{name}_n{order}'] = np.array([b[name] for b in blocks])
            arrays[f'points_n{order}'], arrays[f'weights_n{order}'] = points, weights
            history.append(dict(order=order, points=len(points), quadrature=3 if order == 8 else 4,
                pair_action_s=monotonic()-before))
            print(json.dumps(history[-1]), flush=True)
        errors = {}
        for coarse, fine in ((8, 10), (10, 12), (8, 12)):
            errors[f'n{coarse}_n{fine}'] = {name: [relative(a, b) for a, b in zip(arrays[f'{name}_n{coarse}'], arrays[f'{name}_n{fine}'], strict=True)]
                for name in blocks[0]}
        reverse = pair_blocks(*rules[8], total, -np.array(pairs[0]['displacement_m']), transform, 1e8)
        reciprocity = {name: relative(reverse[name].T, arrays[f'{name}_n8'][2]) for name in reverse}
        arrays.update({f'reverse_{name}': value for name, value in reverse.items()})
        worst = max(value for comparison in errors.values() for values in comparison.values() for value in values)
        accepted = worst < 5e-5 and max(reciprocity.values()) < 1e-10
        result = dict(status='ACCEPT_SAMPLED_SEPARATED_GALERKIN_REFINEMENT' if accepted else 'STOP_SEPARATED_GALERKIN_GATE',
            pairs=pairs, frequencies_hz=source.FREQUENCIES, history=history, relative_errors=errors,
            error_layout='pair-major then frequency; R-whitened5x5 current block and2x2 charge block Frobenius norm',
            reciprocity=reciprocity, worst_refinement_error=worst)
    except Exception:
        accepted = False
        failure = traceback.format_exc()
        result = dict(status='STOP_SEPARATED_GALERKIN_EXCEPTION', history=history)
    artifact = output/'galerkin-blocks.npz'
    np.savez_compressed(artifact, **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
        pins=PINS, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), failure=failure,
        scope='Selected actual separated power-joint placements only. Both source and observer integrals '
        'use proxy weights, retaining all fine-cell5lift currents and2independent test-charge densities. '
        'n8(q3) versus n10/n12(q4) tests joint source quadrature/interpolation effects; refinement is '
        'not an independent continuous-integral oracle or operator bound. Full outgoing kernel, explicit '
        'authoritative monopoles and stable imaginary tail; no near/self, contact solve, skin convergence, field or board claim.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('status', 'elapsed_s', 'failure')}), flush=True)
    return 0 if accepted else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
