"""Fixed-1MHz retained exterior baseline and original device-port source.

Reuses the five non-L02 categories and the explicit L25 R/B block, alongside
the corrected native finite branches. This is not a complete field operator:
L02 distributed GC ownership and physical electromagnetic cross terms remain
unresolved. The baseline must not be silently added to a geometric P operator.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator

from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    R/'astra-pwr-g-native-frequency-boundary-20260912/pwr-g-native-frequency-boundary.npz': 'f456db948e8f1a77788808054870bb8346debfe4f5b9cc49b6570697de5d6cfa',
    R/'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz': '01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c',
    R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz': '6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7',
}


def csc(z, name):
    return sparse.csc_matrix((z[name+'_data'], z[name+'_indices'], z[name+'_indptr']), shape=tuple(z[name+'_shape']))


def assemble():
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    paths = list(PINS)
    with np.load(paths[0], allow_pickle=False) as z:
        nv = int(z['native_potential_count'][0]); frequency = float(z['frequency_hz'][0])
        first = z['retained_finite_first_active_index']; second = z['retained_finite_second_active_index']
        y = z['retained_finite_admittance_s']
    assert frequency == 1e6
    with np.load(paths[1], allow_pickle=False) as z:
        names = json.loads(z['category_names_json_utf8'].tobytes())
        assert set(names) == {'retained_gc', 'termination', 'l14_sheet_dc', 'l14_distributed_gc', 'l25_distributed_gc'}
        categories = {name:csc(z, 'category_'+name) for name in names}
        l25_r = csc(z, 'l25_r'); l25_b = csc(z, 'l25_b')
        unresolved_gc = dict(free_cell_nonzero_stamps=int(np.count_nonzero(z['free_cell_gc_admittance_s'])),
                             contact_stamps=int(len(z['contact_gc_admittance_s'])))
    base_count = l25_b.shape[0]; nj = l25_r.shape[0]
    assert l25_r.shape == (nj, nj) and l25_b.shape[1] == nj
    assert all(a.shape == (base_count, base_count) for a in categories.values())
    with np.load(paths[2], allow_pickle=False) as z:
        batch = z['batch_port_indices']; source_pair = z['solve_port_reduced_nodes'][int(batch[0])]
        port_nodes = z['global_to_active_indices'][source_pair]
    assert batch.shape == (1,) and np.array_equal(port_nodes, [2699, 2656])
    source = np.zeros(nv+nj, complex); source[port_nodes] = [1., -1.]

    def action(x):
        x = np.asarray(x).reshape(-1)
        assert len(x) == nv+nj
        v = x[:nv]; j = x[nv:]
        current = y*(v[first]-v[second])
        node = np.zeros(nv, complex)
        np.add.at(node, first, current); np.add.at(node, second, -current)
        for matrix in categories.values():
            node[:base_count] += matrix@v[:base_count]
        node[:base_count] += l25_b@j
        conductor = l25_b.T@v[:base_count]-l25_r@j
        return np.r_[node, conductor]

    operator = LinearOperator((nv+nj, nv+nj), matvec=action, dtype=complex)
    metadata = dict(native_potentials=nv, l25_current_count=nj, finite_branch_count=len(y),
                    category_nnz={name:int(a.nnz) for name,a in categories.items()},
                    unresolved_l02_gc=unresolved_gc, source_nodes=port_nodes.tolist(),
                    source_global_reduced_nodes=source_pair.tolist(), frequency_hz=frequency)
    return operator, source, metadata


def run():
    started = monotonic(); out = R/'astra-retained-exterior-baseline-20260912'
    assert not out.exists()
    operator, source, metadata = assemble()
    rng = np.random.default_rng(20260912)
    x = rng.normal(size=operator.shape[0])+1j*rng.normal(size=operator.shape[0])
    y = rng.normal(size=operator.shape[0])+1j*rng.normal(size=operator.shape[0])
    ax = operator@x; ay = operator@y
    reciprocity = float(abs(x@ay-y@ax)/max(abs(x@ay), abs(y@ax), 1e-30))
    assert reciprocity < 1e-10 and np.isfinite(ax).all() and np.isfinite(ay).all()
    port_drop = x[metadata['source_nodes'][0]]-x[metadata['source_nodes'][1]]
    assert abs(source@x-port_drop) < 1e-13
    out.mkdir(); (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'source.npz', source_nonzero_indices=np.flatnonzero(source),
                        source_nonzero_values=source[source != 0], operator_shape=np.asarray(operator.shape))
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='EXECUTED_RETAINED_NON_L02_EXTERIOR_BASELINE', metadata=metadata,
                  complex_ordinary_reciprocity_relative=reciprocity, elapsed_s=monotonic()-started,
                  driver_sha256=sha(Path(__file__)), source_sha256=sha(out/'source.npz'),
                  pins={str(p):h for p,h in PINS.items()},
                  equations=['Y_retained v + B25 j25 = source', 'B25.T v - R25 j25 = 0'],
                  scope='Executable fixed1MHz non-L02 retained component baseline and original1A device-port RHS. L02 GC exchange is not yet joined; contact charge exchange, field-aware native cross terms, dielectric ownership, magnetic L and full P are required before a physical port operator or solve. No new board Z.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
