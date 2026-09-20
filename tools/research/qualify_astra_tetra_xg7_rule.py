"""SPD Decap PI Evaluator v0.23.1: qualify positive degree-seven 31-point rule."""
from hashlib import sha256
from math import factorial
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import qualify_astra_tetra_jacobi_rule as previous

ROOT = previous.ROOT
SOURCE = 'https://raw.githubusercontent.com/FEniCS/basix/main/cpp/basix/quadrature.cpp'
PINS = {
    'tools/research/qualify_astra_tetra_jacobi_rule.py': 'b9dd7ccc41a39a3e8d1214dc9fc9cdef14e09f2a6637d321d25bca7874c6a49e',
    'outputs/research/astra-tetra-jacobi-rule-02/rule-comparison.npz': 'a6541ee4308d8462ccae8510d5c7f5d215995a842ca9b26b3fb70620a32afdc9',
    **previous.PINS,
}


def rule():
    # Numerical Xiao-Gimbutas degree-seven table from MIT-licensed FEniCS Basix.
    xyz = np.fromstring('''
0.001996825818299818 0.01920799348858535 0.6513348958482376
0.06092218458545083 0.3234568417895977 0.6151709883118704
0.0005004334442718418 0.6355215105837613 0.0598944722319085
0.6279832293585974 0.293770036523707 0.02748237819283441
0.05213668905801093 0.06201109193664409 0.05718215451677883
0.8245440666953954 0.05989419506998693 0.05677586668994691
0.062815072845237 0.8207453007415948 0.05891041282560915
0.6315484739180046 0.02583316773173042 0.280405238101906
0.001613532619990097 0.1991105720528834 0.2637473753385648
0.3196583760970118 0.5991009436200256 0.03521379529745457
0.5508889781422127 0.2234702025301428 0.2255285376972644
0.3505284068372833 0.003929651487087849 0.2418446130585829
0.2307849002376704 0.01403308447330531 0.5661630745306973
0.063969430325799 0.06247402252315021 0.812872555571
0.3239480709891824 0.0624444127129091 0.5863212858301218
0.2343964973359623 0.528711306413653 0.2169982993458658
0.3501153045071709 0.2615087691765827 0.01197587688915757
0.2753098106871322 0.05300013833454678 0.04497766312688006
0.0762414702839689 0.03316569983103569 0.2748015348979936
0.02253298349383202 0.2604205879982621 0.5646501024676913
0.04463026790663657 0.6018235776318118 0.2899940343655131
0.5990192398798975 0.0528776293788545 0.05497958289551413
0.0680111048992614 0.2977852343835241 0.04764944310089234
0.1572418559860032 0.5504559416248597 0.04374347016073189
0.5003786698498154 0.2582805473674438 0.08266909560739215
0.2898612819086906 0.3971744029949173 0.1710488778187786
0.1112511334269427 0.1074624307831534 0.4758491617393153
0.07400870213911578 0.4103580959949396 0.2447616509193416
0.2175544442163533 0.2521305306293954 0.4487392553835752
0.4372480897645487 0.1098959763270211 0.2765716827388576
0.2188126225475045 0.176438223014948 0.1757661102664513
''', sep=' ').reshape(31, 3)
    weights = 6*np.fromstring('''
0.0012846968603334146 0.002000632031369977 0.002085684575720105
0.002783666843940815 0.0030095129140263084 0.0032004686326964665
0.0034317247720467565 0.003452013693960475 0.0034787841936317
0.0035154609736464167 0.0036967198625352964 0.003724778580430695
0.0037352275658985514 0.003869468221310365 0.0038818311595533
0.00409373831432887 0.004554985719607738 0.0046768630523221786
0.00471879793532209 0.004799380599205763 0.005235187909249938
0.005632644217125163 0.0061559681852397475 0.006973088601266729
0.007165193660663451 0.008796944275592277 0.010050252598534952
0.010698139822576646 0.011118534527621958 0.011123248236813444
0.01372302813009497
''', sep=' ')
    bary = np.c_[1-xyz.sum(axis=1), xyz]
    assert bary.shape == (31, 4) and weights.shape == (31,)
    assert np.all(bary > 0) and np.all(weights > 0) and abs(weights.sum()-1) < 1e-13
    return bary, weights


def main():
    out = ROOT/'outputs/research/astra-tetra-xg7-rule-01'
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started, arrays = monotonic(), {}
    try:
        previous.geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        bary, weights = rule()
        arrays.update(barycentric=bary, normalized_weights=weights)
        errors = []
        for a in range(8):
            for b in range(8-a):
                for c in range(8-a-b):
                    exact = 6*factorial(a)*factorial(b)*factorial(c)/factorial(a+b+c+3)
                    errors.append(abs(np.dot(weights, bary[:, 1]**a*bary[:, 2]**b*bary[:, 3]**c)/exact-1))
        assert len(errors) == 120 and max(errors) < 1e-12
        joint = previous.geometry.load_joint()
        with np.load(ROOT/'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz') as d:
            current, base = d['whitened_face_currents'], d['corrected_matrix_q3']
        scale = np.linalg.inv(np.linalg.cholesky((base+base.T)/2))
        local = joint['local_face_signs'][:, :, None]*current[joint['local_face_columns']]
        tets = joint['tetrahedra_m']
        points = np.einsum('pi,cid->cpd', bary, tets)
        weighted = np.einsum('cim,cpid->cpmd', local, points[:, :, None]-tets[:, None])/3*weights[None, :, None, None]
        # Runnable physical first-moment check, independent of the point-pair comparison.
        exact_integral = np.einsum('cim,cid->cmd', local, tets.mean(axis=1)[:, None]-tets)/3
        moment_error = float(np.linalg.norm(weighted.sum(axis=1)-exact_integral)/np.linalg.norm(exact_integral))
        assert moment_error < 1e-12
        cases = []
        with np.load(ROOT/'outputs/research/astra-tetra-jacobi-rule-02/rule-comparison.npz') as d:
            for label in ('touch', 'nontouch'):
                pa, pb, reference = d[f'{label}_pair_a'], d[f'{label}_pair_b'], d[f'{label}_reference']
                values = np.empty_like(reference)
                for first in range(0, len(pa), 64):
                    assert monotonic()-started < 60
                    a, b = pa[first:first+64], pb[first:first+64]
                    distance = np.linalg.norm(points[a, :, None]-points[b, None, :], axis=3)
                    assert distance.min() > 0
                    potential = np.einsum('pij,pjnd->pind', 1/distance, weighted[b], optimize=True)
                    value = 1e-7*np.einsum('pimd,pind->pmn', weighted[a], potential, optimize=True)
                    values[first:first+64] = value+value.transpose(0, 2, 1)
                scaled = scale[None] @ (values-reference) @ scale.T[None]
                cases.append(dict(kind=label, pairs=len(pa), signed_error_original_energy=float(np.linalg.norm(scaled.sum(axis=0), 2)),
                                  error_l1_original_energy=float(np.linalg.norm(scaled, axis=(1, 2)).sum())))
                arrays[f'{label}_point_pair'] = values
        result = dict(status='QUALIFIED_DEGREE7_RULE_NEAR_ACCURACY_DIAGNOSTIC', degree=7, points=31,
                      moment_count=len(errors), maximum_relative_moment_error=max(errors),
                      physical_current_moment_relative=moment_error, cases=cases, failure=None)
    except Exception:
        result = dict(status='STOP_DEGREE7_RULE', failure=traceback.format_exc())
    np.savez_compressed(out/'rule-comparison.npz', **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
                  driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS, primary_source=SOURCE,
                  artifact_sha256=sha256((out/'rule-comparison.npz').read_bytes()).hexdigest(),
                  scope='All120 monomials through total degree7 and physical affine-current integral are gated. '
                  'Saved qualified near pairs diagnose 1/R point-rule error; this is not a singular-integral, FMM, field or board qualification.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['failure'] is None else 2


if __name__ == '__main__':
    raise SystemExit(main())
