"""SPD Decap PI Evaluator v0.23.1: exact lattice remap of saved native columns.

The destination is the planned 125 um, 801-square grid. This check does not
certify coverage of every other physical support or form any Green kernel.
"""
from fractions import Fraction
from pathlib import Path
from time import perf_counter
import hashlib
import json
import numpy as np
from scipy import sparse

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
SOURCE = R/'astra-retained-gc-sparse-grid-map-20260912-05'
OUT = R/'astra-native-grid-integer-remap-20260912'


def remap_columns(matrix, source_origin_steps, source_shape,
                  destination_origin_steps, destination_shape):
    """Same lattice spacing, integer origin coordinates, unchanged weights."""
    source_origin_steps = np.asarray(source_origin_steps)
    destination_origin_steps = np.asarray(destination_origin_steps)
    assert source_origin_steps.dtype.kind in 'iu'
    assert destination_origin_steps.dtype.kind in 'iu'
    assert source_origin_steps.shape == destination_origin_steps.shape == (2,)
    assert len(source_shape) == len(destination_shape) == 2
    assert min(*source_shape, *destination_shape) > 0
    original = matrix.tocsc()
    assert original.shape[0] == int(np.prod(source_shape))
    assert np.isfinite(original.data).all()
    shift = source_origin_steps-destination_origin_steps
    ix, iy = np.divmod(original.indices.astype(np.int64), source_shape[1])
    nx, ny = ix+shift[0], iy+shift[1]
    assert np.all((nx >= 0) & (nx < destination_shape[0]))
    assert np.all((ny >= 0) & (ny < destination_shape[1]))
    new_rows = nx*destination_shape[1]+ny
    return sparse.csc_matrix((original.data.copy(), new_rows,
                              original.indptr.copy()),
                             shape=(int(np.prod(destination_shape)), original.shape[1]))


def run():
    started = perf_counter()
    pins = {'result.json': '3e07f2865e18c46395ef3d9b58ed7f671b6b8998977f1bf58a3938352f8b30bd',
            'grid-to-selected-face-map.npz': '7d8bba97e941aef5bd3e4fee5c827ac8a13b3facb2bae610c46a813243b63132'}
    for name, expected in pins.items():
        assert hashlib.sha256((SOURCE/name).read_bytes()).hexdigest() == expected
    receipt = json.loads((SOURCE/'result.json').read_text())
    spacing = Fraction(str(receipt['grid']['spacing_m']))
    steps = [Fraction(str(v))/spacing for v in receipt['grid']['origin_m']]
    assert all(v.denominator == 1 for v in steps)
    source_steps = tuple(map(int, steps))
    shape = tuple(receipt['grid']['shape'])
    destination_steps, destination_shape = (-400, -400), (801, 801)
    original = sparse.load_npz(SOURCE/'grid-to-selected-face-map.npz').tocsc()
    remapped = remap_columns(original, source_steps, shape,
                             destination_steps, destination_shape)
    old_rows = np.arange(int(np.prod(shape)), dtype=np.int64)
    ix, iy = np.divmod(old_rows, shape[1])
    sx, sy = np.subtract(source_steps, destination_steps)
    new_rows = (ix+sx)*destination_shape[1]+iy+sy
    assert len(np.unique(new_rows)) == len(old_rows)
    assert np.array_equal(remapped.data, original.data)
    assert np.array_equal(remapped.indptr, original.indptr)
    assert np.array_equal(remapped.indices, new_rows[original.indices])
    q = np.array([0.75+0.25j, -1.125+0.5j])
    field = np.sin(np.arange(remapped.shape[0])*.007)+1j*np.cos(np.arange(remapped.shape[0])*.011)
    before, after = original@q, remapped@q
    expected = np.zeros_like(after); expected[new_rows] = before
    assert np.array_equal(after, expected)
    assert np.array_equal(remapped.T@field, original.T@field[new_rows])
    OUT.mkdir(exist_ok=False)
    artifact = OUT/'remapped-selected-columns.npz'
    sparse.save_npz(artifact, remapped)
    restored = sparse.load_npz(artifact)
    assert restored.shape == remapped.shape
    assert all(np.array_equal(getattr(restored, k), getattr(remapped, k))
               for k in ['indices', 'indptr', 'data'])
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_EXACT_INTEGER_LATTICE_REMAP', input_pins=pins,
                  source_origin_steps=source_steps, destination_origin_steps=destination_steps,
                  source_shape=shape, destination_shape=destination_shape,
                  spacing_m=float(spacing), integer_shift=[int(sx), int(sy)],
                  unchanged_coefficients=True, exact_scatter_reindex=True,
                  exact_ordinary_transpose_gather=True, exact_saved_readback=True,
                  artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                  driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  elapsed_s=perf_counter()-started,
                  scope='Saved two native columns only; exact physical lattice node identity, no interpolation. Planned destination grid; coverage of other supports and full Green action are separate.')
    (OUT/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    run()
