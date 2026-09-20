"""Join disjoint verified L02 self coefficients; no Green integration is rerun."""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'


def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def main():
    out = R / 'astra-l02-complete-self-20260912'
    out.mkdir(exist_ok=False)
    pins = {
        'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz': '9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
        'astra-l02-single-support-self-20260912-full/registry.npz': 'e2e4627ae4ab9b60876b74dc99f8951890fec5865a31a6184c6dfa1fc9e76c6b',
        'astra-l02-single-support-self-20260912-full/single-support-self.npz': '4ab4e4ea8bd5e7fb3634c1be474dcf234661b6ea7f08c0c6fc2f71121a50098f',
        'astra-l02-contact-union-self-20260912/contact-self.npz': '9e820422d16053ff7dbf20666645fe6d660de52ccba5ff18c3fdefe9127214e6',
        'astra-l02-multi-free-self-20260912-01/multi-free-self.npz': '42c9b998ae0d85fcbf99d9d255eb7483f70df0a4791d9c26a8ef75c1bfea25d9',
    }
    for name, expected in pins.items():
        assert digest(R/name) == expected, name
    dependencies = {
        'astra_l02_point_self.py': '7f46d03c669f7d0cf3e54613222762e72f9f8f54e7a1835c402e74ecd8858785',
        'astra_stratified_charge_green.py': '5808ad457d3d58b47413f47f02e30ce5f51c3365f16eb4d2562b2a742007451b',
        'astra_prism_covariogram_self.py': '71f055b52712230e9f255671bb7e7b3a4c709f9f347598ba4ecea53fbe313a15',
        'assemble_astra_l02_single_support_self.py': 'c68cf82028e546f7648ae0305606d8080093977a7c585dd845e750f1b967af3b',
    }
    for name, expected in dependencies.items():
        assert digest(ROOT/'tools/research'/name) == expected, name
    assert digest(R/'astra-l02-single-support-self-20260912-full/driver-at-run.py') == dependencies['assemble_astra_l02_single_support_self.py']
    with np.load(R/list(pins)[0], allow_pickle=False) as z:
        rows = z['charge_row_ids']
        exterior_rows = z['exterior_charge_row_ids']
        segment_counts = np.diff(z['exterior_segment_offsets'])
        contact_columns = z['contact_charge_columns']
    with np.load(R/list(pins)[1], allow_pickle=False) as z:
        wall_columns = z['columns'][int(z['prism_columns']):]
        assert np.array_equal(rows[wall_columns], exterior_rows[segment_counts == 1])
        assert np.all(segment_counts == 1)
        unresolved = z['unresolved_columns']
    count = len(rows)
    seen = np.zeros(count, np.uint8)
    physical = np.empty(count, complex)
    point = np.empty(count, complex)
    delta = np.empty(count, complex)
    indicator = np.empty(count)
    component_counts = {}
    for name in list(pins)[2:]:
        multi = 'multi-free' in name
        with np.load(R/name, allow_pickle=False) as z:
            col = z['columns' if multi else 'charge_columns']
            old = z['original_rows' if multi else 'original_charge_rows']
            exact = z['physical_self_per_f' if multi else 'physical_halfspace_self_p']
            discrete = z['point_self_per_f' if multi else 'point_halfspace_self_p']
            correction = z['self_delta_per_f' if multi else 'physical_halfspace_self_delta_p']
            error = z['target_relative_refinement' if multi else 'combined_refinement_geometry_indicator']
            assert np.all((col >= 0) & (col < count)) and len(np.unique(col)) == len(col)
            assert not np.any(seen[col]) and np.array_equal(rows[col], old)
            assert np.array_equal(exact-discrete, correction)
            assert np.isfinite(exact).all() and np.isfinite(discrete).all() and np.isfinite(correction).all()
            assert np.all(exact.real > 0) and np.isfinite(error).all() and np.all((error >= 0) & (error <= 5e-5))
            seen[col] = 1
            physical[col], point[col], delta[col], indicator[col] = exact, discrete, correction, error
            component_counts[name] = len(col)
            if multi:
                assert np.array_equal(np.sort(np.r_[col, contact_columns]), unresolved)
    assert np.all(seen == 1) and count == 2440492
    target = out/'complete-self.npz'
    np.savez_compressed(target, original_charge_rows=rows, physical_halfspace_self_p=physical,
                        point_halfspace_self_p=point, physical_halfspace_self_delta_p=delta,
                        numerical_refinement_geometry_indicator=indicator)
    with np.load(target, allow_pickle=False) as saved:
        assert np.array_equal(saved['original_charge_rows'], rows)
        assert np.array_equal(saved['physical_halfspace_self_delta_p'], delta)
        assert np.array_equal(saved['physical_halfspace_self_p']-saved['point_halfspace_self_p'], delta)
    report = dict(status='PASS_COMPLETE_L02_SELF_JOIN', q_count=count, component_counts=component_counts,
                  maximum_numerical_indicator=float(indicator.max()), wall_identity_verified=len(wall_columns),
                  input_sha256=pins, current_dependency_sha256=dependencies, artifact_sha256=digest(target),
                  scope='All retained L02 q self, direct plus 25um image only. Indicator is not an interval-certified bound. Keep smooth deep diagonals. Nonself quadrature, current Green and actual board port accuracy remain unresolved.')
    (out/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
