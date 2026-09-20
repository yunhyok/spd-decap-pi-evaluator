"""SPD Decap PI Evaluator v0.23.1: actual source-joint charge self/near blocks."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from qualify_astra_tetra_charge_green import quadrature, measure
from qualify_astra_tetra_volume_green import tetra_inner, triangle_moments
from qualify_astra_source_joint_self_green import ROOT, PINS


def scalar_pair(observer, source, order):
    origin = source[0]
    p, w = quadrature(observer-origin, order)
    inner = tetra_inner if len(source) == 4 else triangle_moments
    return float(w @ inner(source-origin, p)[0]/measure(source))


def run(output):
    start = monotonic()
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        mesh = ROOT/list(PINS)[2]
        assert sha256(mesh.read_bytes()).hexdigest() == PINS[list(PINS)[2]]
        helper = ROOT/'tools/research/qualify_astra_tetra_charge_green.py'
        assert sha256(helper.read_bytes()).hexdigest() == 'aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9'
        with np.load(mesh, allow_pickle=False) as d:
            xyz = d['vertices_local_um']*1e-6
            cells = d['cells']
            boundary = d['boundary_face_ids']
            faces = d['face_vertices'][boundary]
        tet = xyz[cells]
        singular = np.linalg.svd(tet[:, 1:]-tet[:, :1], compute_uv=False)
        condition = singular[:, 0]/singular[:, -1]
        worst = int(np.argmax(condition))
        common = np.isin(cells, cells[worst]).sum(axis=1)
        selected_cells = [worst]
        for count in (3, 2, 1):
            ids = np.flatnonzero(common == count)
            selected_cells.append(int(ids[np.argmin(np.linalg.norm(tet[ids].mean(axis=1)-tet[worst].mean(axis=0), axis=1))]))
        tri = xyz[faces]
        singular_tri = np.linalg.svd(tri[:, 1:]-tri[:, :1], compute_uv=False)
        tri_condition = singular_tri[:, 0]/singular_tri[:, -1]
        selected_faces = [int(np.argmax(tri_condition))]
        top = np.all(tri[:, :, 2] == 0, axis=1)
        lower = np.all(tri[:, :, 2] == 75e-6, axis=1)
        for mask in (top, lower, ~(top | lower)):
            ids = np.flatnonzero(mask)
            selected_faces.append(int(ids[np.argmin(np.linalg.norm(tri[ids].mean(axis=1)-tet[worst].mean(axis=0), axis=1))]))
        for cell in selected_cells:
            ids = np.flatnonzero(np.isin(faces, cells[cell]).sum(axis=1) == 3)
            selected_faces.extend(map(int, ids))
        selected_faces = sorted(set(selected_faces))
        entities = [tet[i] for i in selected_cells]+[tri[i] for i in selected_faces]
        n = len(entities)
        matrix = np.zeros((n, n))
        orders = np.zeros((n, n), dtype=np.int16)
        changes = np.zeros((n, n))
        reciprocity = np.zeros((n, n))
        histories = []
        for a, observer in enumerate(entities):
            for b in range(a, n):
                source = entities[b]
                previous = None
                history = []
                for order in (8, 16, 32, 64):
                    assert monotonic()-start < 90, 'source charge kernel deadline'
                    ab = scalar_pair(observer, source, order)
                    ba = ab if a == b else scalar_pair(source, observer, order)
                    assert min(ab, ba) > 0 and np.isfinite([ab, ba]).all()
                    values = np.array([ab, ba])
                    change = None if previous is None else float(np.linalg.norm(values-previous)/np.linalg.norm(values))
                    reciprocal = abs(ab-ba)/max(ab, ba)
                    history.append(dict(order=order, relative_change=change, raw_reciprocity=reciprocal))
                    previous = values
                    if change is not None and change < 5e-5 and reciprocal < 5e-5:
                        break
                matrix[a, b], matrix[b, a] = ab, ba
                orders[a, b] = orders[b, a] = order
                changes[a, b] = changes[b, a] = change
                reciprocity[a, b] = reciprocity[b, a] = reciprocal
                histories.append(dict(entity_pair=[a, b], orders=history))
        scale = 1/np.sqrt(np.diag(matrix))
        normalized = scale[:, None]*matrix*scale[None]
        eigenvalues = np.linalg.eigvalsh((normalized+normalized.T)/2)
        gate = bool(changes.max() < 5e-5 and reciprocity.max() < 5e-5 and eigenvalues.min() > 0)
        artifact = output/'charge-blocks.npz'
        np.savez_compressed(artifact, scalar_kernel_per_m=matrix, quadrature_order=orders,
            relative_change=changes, raw_reciprocity=reciprocity,
            selected_cell_ids=np.array(selected_cells), selected_boundary_face_ids=boundary[selected_faces],
            selected_tetrahedra_m=tet[selected_cells], selected_triangles_m=tri[selected_faces])
        result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='ACCEPT_SELECTED_SOURCE_CHARGE_SELF_NEAR_BLOCKS' if gate else 'STOP_SELECTED_SOURCE_CHARGE_KERNEL_GATE',
            elapsed_s=monotonic()-start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
            mesh_sha256=sha256(mesh.read_bytes()).hexdigest(), artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),
            selected_cells=selected_cells, selected_boundary_faces=boundary[selected_faces].tolist(),
            worst_tetra_condition=float(condition[worst]), worst_triangle_condition=float(tri_condition.max()),
            fixed_relative_gate=5e-5, maximum_change=float(changes.max()), maximum_raw_reciprocity=float(reciprocity.max()),
            normalized_symmetric_part_eigenvalues=eigenvalues.tolist(), refinement=histories,
            scope='Actual5304-tet source-joint supports: volume-volume, volume-face and face-face '
            'self/near interactions with unit-integrated P0 densities. Analytic inner1/R and positive '
            'outer quadrature reuse qualified helpers. Raw forward/reverse values are retained; the '
            'symmetric part is only a positivity diagnostic. No all-support matrix, retarded kernel, '
            'contact constraints, physical charge solution, mesh convergence or board accuracy.')
        (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps({k: result[k] for k in ('status', 'elapsed_s', 'maximum_change', 'maximum_raw_reciprocity', 'artifact_sha256')}), flush=True)
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(), elapsed_s=monotonic()-start), indent=2), encoding='utf-8')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
