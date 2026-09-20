"""Actual G-neighborhood self L/P blocks for the source-boundary charge partition.

Reuse the existing analytic inner kernels and the previously fixed self-block
criterion. No changes to copper mesh, charge support, or conductivity.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_source_joint_charge_green import scalar_pair
from qualify_astra_conforming_power_joint_sparse_current import local_mass
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-g-complete-self-green-20260912'
PINS = {
    R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz': '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
    R/'astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz': '600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5',
    ROOT/'tools/research/qualify_astra_tetra_volume_green.py': '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
    ROOT/'tools/research/qualify_astra_tetra_charge_green.py': 'aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9',
}


def run():
    started = monotonic()
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    with np.load(list(PINS)[0], allow_pickle=False) as z:
        tetra = z['volume_charge_vertices_um']*1e-6
        triangles = z['surface_charge_vertices_um']*1e-6
        face_ids = z['free_3d_surface_face_ids']
    nc = len(tetra); count = nc+len(triangles)
    volume = abs(np.linalg.det(tetra[:, 1:]-tetra[:, :1]))/6
    vector = np.full((nc, 4, 4), np.nan); scalar = np.full(count, np.nan)
    order_used = np.zeros(count, np.int16); error = np.full((count, 3), np.nan)
    accepted = np.zeros(count, bool); completed = 0; failure = None
    OUT.mkdir(exist_ok=False); (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for row in range(count):
            vertices = tetra[row] if row < nc else triangles[row-nc]
            if row < nc:
                whitening = np.linalg.solve(np.linalg.cholesky(local_mass(vertices, volume[row])).T, np.eye(4))
            prev_s = prev_l = None
            for order in (8, 16, 32, 64):
                assert monotonic()-started < 900, '900s self-assembly execution boundary'
                s = scalar_pair(vertices, vertices, order)
                ds = np.inf if prev_s is None else abs(s-prev_s)/s
                dl = reciprocity = 0.; positive = True
                if row < nc:
                    block = tetra_pair(vertices, vertices, order)
                    normalized = whitening.T@block@whitening
                    dl = np.inf if prev_l is None else float(np.linalg.norm(normalized-prev_l)/np.linalg.norm(normalized))
                    reciprocity = float(np.linalg.norm(normalized-normalized.T)/np.linalg.norm(normalized))
                    positive = bool(np.linalg.eigvalsh((normalized+normalized.T)/2).min() > 0)
                    prev_l = normalized; vector[row] = block
                prev_s = s
                passed = np.isfinite(s) and s > 0 and ds < 5e-5 and dl < 5e-5 and reciprocity < 5e-5 and positive
                if passed:
                    break
            scalar[row] = s; error[row] = ds, dl, reciprocity
            order_used[row] = order; accepted[row] = passed; completed = row+1
            if completed % 256 == 0:
                print(json.dumps(dict(completed=completed, total=count, failed=int((~accepted[:completed]).sum()), elapsed_s=monotonic()-started)), flush=True)
    except Exception as exc:
        failure = repr(exc)
    np.savez_compressed(OUT/'self-blocks.npz', static_vector_self_h=vector, static_scalar_self_per_m=scalar,
                        final_order=order_used, final_relative_errors=error, accepted_at_fixed_gate=accepted,
                        free_surface_face_ids=face_ids, cell_count=np.array([nc]))
    status = 'ACCEPT_COMPLETE_G_SOURCE_SELF_BLOCKS' if completed == count and accepted.all() and failure is None else 'INCOMPLETE_G_SOURCE_SELF_BLOCKS'
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1', status=status, completed_supports=completed,
                  current_cell_self_blocks=nc, charge_surface_self_blocks=len(triangles), total_supports=count,
                  elapsed_s=monotonic()-started, fixed_relative_gate=5e-5,
                  failed_completed_supports=np.flatnonzero(~accepted[:completed]).tolist(), failure=failure,
                  pins={str(p): h for p, h in PINS.items()}, driver_sha256=sha(Path(__file__)),
                  artifact_sha256=sha(OUT/'self-blocks.npz'),
                  scope='Complete G tetra-current and volume/free-surface-charge static self blocks, on actual replacement geometry. Source and artificial cut faces are excluded from free charge according to existing D/H/C. No off-diagonal pair is represented by this diagonal; full all-pair operator assembly continues separately. No material background, port solve or board accuracy is inferred.')
    (OUT/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
