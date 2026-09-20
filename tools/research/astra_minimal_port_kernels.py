"""SPD Decap PI Evaluator v0.23.1: small canonical full Green matrices."""
from functools import lru_cache
from time import perf_counter

import numpy as np
from scipy import sparse

import qualify_astra_tetra_volume_green as magnetic
import qualify_astra_tetra_charge_green as electric
from qualify_astra_conforming_power_joint_sparse_current import local_mass


@lru_cache(maxsize=2)
def _fields(tetra_bytes, count, entity_bytes, order):
    tetrahedra = np.frombuffer(tetra_bytes, dtype=float).reshape(count, 4, 3)
    entities = [np.frombuffer(value, dtype=float).reshape(-1, 3) for value in entity_bytes]
    started = perf_counter()
    mass = sparse.block_diag([local_mass(t, magnetic.faces(t)[0]) for t in tetrahedra]).tocsr()
    # ponytail: dense all-pairs canonical only; use qualified fast operators for board scale.
    inductance = np.empty((4*count, 4*count))
    last = started
    for a, observer in enumerate(tetrahedra):
        for b, source in enumerate(tetrahedra):
            inductance[4*a:4*a+4, 4*b:4*b+4] = magnetic.tetra_pair(observer, source, order)
        if perf_counter()-last > 25:
            print(f'Canonical magnetic assembly: {a+1}/{count} rows', flush=True)
            last = perf_counter()
    potential = np.empty((len(entities), len(entities)))
    for a, observer in enumerate(entities):
        points, weights = electric.quadrature(observer, order)
        for b, source in enumerate(entities):
            origin = source[0]
            inner = magnetic.tetra_inner if len(source) == 4 else magnetic.triangle_moments
            potential[a, b] = weights @ inner(source-origin, points-origin)[0]/electric.measure(source)
        if perf_counter()-last > 25:
            print(f'Canonical charge assembly: {a+1}/{len(entities)} rows', flush=True)
            last = perf_counter()
    assert np.isfinite(inductance).all() and np.isfinite(potential).all()
    diagnostics = dict(tetrahedra=count, charge_entities=len(entities), order=order,
                       magnetic_raw_reciprocity=float(np.linalg.norm(inductance-inductance.T)/np.linalg.norm(inductance)),
                       potential_raw_reciprocity=float(np.linalg.norm(potential-potential.T)/np.linalg.norm(potential)),
                       integration_rule='Arithmetic mean of both directed outer quadratures, fixed before solve; no spectral clipping.',
                       assembly_seconds=perf_counter()-started)
    return mass, (inductance+inductance.T)/2, (potential+potential.T)/2, diagnostics


def assemble_fields(tetrahedra, local_to_global_flux_matrix, charge_entities, order):
    tetrahedra = np.asarray(tetrahedra, dtype=float)
    entities = [np.asarray(e, dtype=float) for e in charge_entities]
    assert tetrahedra.ndim == 3 and tetrahedra.shape[1:] == (4, 3)
    assert np.isfinite(tetrahedra).all() and isinstance(order, int) and order >= 2
    assert entities and all(e.shape in ((3, 3), (4, 3)) and np.isfinite(e).all() for e in entities)
    transform = sparse.csr_matrix(local_to_global_flux_matrix)
    assert transform.shape[0] == 4*len(tetrahedra) and np.isfinite(transform.data).all()
    mass, inductance, potential, diagnostics = _fields(
        tetrahedra.tobytes(), len(tetrahedra), tuple(e.tobytes() for e in entities), order)
    return dict(mass=(transform.T @ mass @ transform).toarray(),
                inductance=np.asarray(transform.T @ inductance @ transform),
                potential_raw_per_m=potential.copy(), diagnostics=diagnostics.copy())


def self_check():
    tetra = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])*1e-3
    face = magnetic.faces(tetra)[1][0][0]
    first = assemble_fields([tetra], np.eye(4), [tetra, face], 4)
    shift = np.array([.003, -.002, .001])
    second = assemble_fields([tetra+shift], np.eye(4), [tetra+shift, face+shift], 4)
    for key in ('mass', 'inductance', 'potential_raw_per_m'):
        assert np.allclose(first[key], second[key], rtol=1e-10, atol=1e-20), key
    volume, faces = magnetic.faces(tetra)
    flux = np.array([area*normal for _, normal, area in faces])
    assert np.allclose(flux.T @ first['mass'] @ flux, volume*np.eye(3), rtol=1e-12, atol=1e-24)
    assert first['potential_raw_per_m'].shape == (2, 2)
    assert np.linalg.eigvalsh(first['mass']).min() > 0
    print('PASS: translated full kernels, constant-current mass, dynamic charge count.', flush=True)


if __name__ == '__main__':
    self_check()
