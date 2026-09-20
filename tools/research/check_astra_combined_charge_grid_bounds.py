"""SPD Decap PI Evaluator v0.23.1: bounded saved-array grid coverage check."""
from pathlib import Path
from time import perf_counter
from zipfile import ZipFile
import hashlib
import json
import math
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
SOURCES = {
    'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz':
        ('a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b', ['vertices_um']),
    'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz':
        ('72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf', ['volume_charge_vertices_um', 'surface_charge_vertices_um']),
    'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz':
        ('9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c', ['quadrature_points_um']),
}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(8*1024**2), b''):
            h.update(data)
    return h.hexdigest()


def array_bounds(archive, name):
    with archive.open(name+'.npy') as stream:
        version = np.lib.format.read_magic(stream)
        assert version in ((1, 0), (2, 0))
        reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
        shape, fortran, dtype = reader(stream)
        assert not fortran and dtype.kind == 'f' and shape[-1] == 3
        rows = math.prod(shape[:-1]); left = rows
        low, high = np.full(2, np.inf), np.full(2, -np.inf)
        while left:
            count = min(left, 65536)
            buffer = stream.read(count*3*dtype.itemsize)
            assert len(buffer) == count*3*dtype.itemsize
            block = np.frombuffer(buffer, dtype=dtype).reshape(count, 3)
            assert np.isfinite(block).all()
            low = np.minimum(low, block[:, :2].min(axis=0))
            high = np.maximum(high, block[:, :2].max(axis=0))
            left -= count
        assert stream.read(1) == b''
    return dict(array=name, shape=shape, rows=rows, xy_min_um=low.tolist(), xy_max_um=high.tolist())


def run():
    started = perf_counter(); evidence = []
    for relative, (pin, names) in SOURCES.items():
        path = R/relative; assert sha(path) == pin
        with ZipFile(path) as archive:
            for name in names:
                row = array_bounds(archive, name)
                row.update(source=relative, source_sha256=pin)
                evidence.append(row)
    qualifier = R/'astra-retained-gc-exact-polygon-support-20260912-12-qualification/result.json'
    qsha = '209b1780c98de9606a3b67b3a2165f77f85ba8ef1cfddd57bc4edc45789226db'
    assert sha(qualifier) == qsha
    native = json.loads(qualifier.read_text())['geometry_bounds_um']
    evidence.append(dict(source=str(qualifier.relative_to(R)), source_sha256=qsha,
                         xy_min_um=native['xy_min'], xy_max_um=native['xy_max'], reused_qualified_bounds=True))
    low = np.min([row['xy_min_um'] for row in evidence], axis=0)
    high = np.max([row['xy_max_um'] for row in evidence], axis=0)
    origin_um = np.array([-50000., -50000.]); spacing_um = 125.; shape = (801, 801)
    min_index = np.floor((low-origin_um)/spacing_um).astype(int)-1
    max_index = np.floor((high-origin_um)/spacing_um).astype(int)+2
    assert np.all(min_index >= 0) and np.all(max_index < shape)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_CURRENT_QUALIFIED_CHARGE_DOMAINS_COMMON_GRID_COVERAGE',
                  evidence=evidence, combined_min_um=low.tolist(), combined_max_um=high.tolist(),
                  origin_m=(origin_um/1e6).tolist(), origin_lattice_steps=[-400, -400],
                  shape=shape, spacing_m=spacing_um*1e-6,
                  cubic_stencil_min_index=min_index.tolist(), cubic_stencil_max_index=max_index.tolist(),
                  maximum_coordinate_batch_rows=65536, elapsed_s=perf_counter()-started,
                  driver_sha256=sha(Path(__file__)),
                  scope='Existing PWR/G support vertices, L02 charge points and qualified native face bounds only. Positive PWR/G quadrature lies within these vertices. Separate additional L14/L25 supports still need their own binding. No global point copy, geometry generation, kernel, or board solve.')
    out = R/'astra-combined-charge-grid-bounds-20260912.json'
    assert not out.exists()
    out.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    run()
