"""SPD Decap PI Evaluator v0.23.1: geometry-only cost of complete near/far partition."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MESH = 'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz'
PIN = '14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb'


def census(tetrahedra, leaf_size, deadline):
    center = tetrahedra.mean(axis=1)
    lower, upper = tetrahedra.min(axis=1), tetrahedra.max(axis=1)
    nodes = []

    def build(ids):
        lo, hi = lower[ids].min(axis=0), upper[ids].max(axis=0)
        index = len(nodes)
        node = dict(count=len(ids), center=(lo+hi)/2, radius=np.linalg.norm(hi-lo)/2,
                    children=None)
        nodes.append(node)
        if len(ids) > leaf_size:
            axis = np.argmax(np.ptp(center[ids], axis=0))
            ordered = ids[np.argsort(center[ids, axis], kind='stable')]
            cut = len(ordered)//2
            node['children'] = (build(ordered[:cut]), build(ordered[cut:]))
        return index

    build(np.arange(len(tetrahedra)))
    stack = [(0, 0)]
    far_pairs = near_pairs = far_blocks = near_blocks = 0
    active_far_nodes = set()
    while stack:
        assert monotonic() < deadline, 'bounded geometry census'
        i, j = stack.pop()
        a, b = nodes[i], nodes[j]
        if i == j:
            if a['children'] is None:
                near_pairs += a['count']*(a['count']-1)//2
                near_blocks += 1
            else:
                left, right = a['children']
                stack.extend(((left, left), (left, right), (right, right)))
        elif np.linalg.norm(a['center']-b['center']) >= max(
                3*a['radius']+b['radius'], a['radius']+3*b['radius']):
            # Each entire target box lies outside 3R of the other source box.
            # ponytail: radius separation is a partition rule, not an interpolation error bound.
            far_pairs += a['count']*b['count']
            far_blocks += 1
            active_far_nodes.update((i, j))
        elif a['children'] is None and b['children'] is None:
            near_pairs += a['count']*b['count']
            near_blocks += 1
        elif b['children'] is None or (a['children'] is not None and a['radius'] >= b['radius']):
            stack.extend((child, j) for child in a['children'])
        else:
            stack.extend((i, child) for child in b['children'])
    expected = len(tetrahedra)*(len(tetrahedra)-1)//2
    assert near_pairs+far_pairs == expected
    return dict(leaf_size=leaf_size, nodes=len(nodes), unordered_intercell_pairs=expected,
                near_pairs=near_pairs, far_pairs=far_pairs, near_leaf_blocks=near_blocks,
                far_blocks=far_blocks, active_far_nodes=len(active_far_nodes),
                n8_scalar_kernel_entries=far_blocks*512**2,
                n8_five_vector_weight_bytes=len(active_far_nodes)*512*5*3*8)


def main():
    output = ROOT/'outputs/research/astra-joint-cluster-partition-01'
    assert not output.exists()
    started = monotonic()
    assert sha256((ROOT/MESH).read_bytes()).hexdigest() == PIN
    with np.load(ROOT/MESH) as data:
        tets = data['vertices_local_um'][data['cells']]
    cases = [census(tets, size, started+60) for size in (8, 32, 128)]
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='COMPLETE_GEOMETRY_PARTITION_CENSUS', cases=cases,
                  elapsed_s=monotonic()-started, mesh_sha256=PIN,
                  driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  scope='Complete unordered intercell pair count, self cells separate. '
                  'No Green integration, new FMM, source omission, error bound, field or board claim.')
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    (output/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
