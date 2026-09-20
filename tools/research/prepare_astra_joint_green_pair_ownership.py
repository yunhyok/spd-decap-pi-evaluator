"""SPD Decap PI Evaluator v0.23.1: complete source-joint Green pair census.

The distance rule chooses integration treatment, never removes an interaction.
All cell-current and cell/exterior-face charge supports remain represented.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
from scipy.sparse import csr_matrix, coo_matrix
from qualify_astra_source_joint_self_green import ROOT, PINS


def run(output):
    start = monotonic()
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    mesh = ROOT/list(PINS)[2]
    assert sha256(mesh.read_bytes()).hexdigest() == PINS[list(PINS)[2]]
    with np.load(mesh, allow_pickle=False) as d:
        vertices = d['vertices_local_um']*1e-6
        cells = d['cells']
        boundary = d['boundary_face_ids']
        faces = d['face_vertices'][boundary]
        volumes = d['cell_volume_um3']*1e-18
    nc, nb = len(cells), len(faces)
    entity_vertices = np.full((nc+nb, 4), -1, dtype=np.int32)
    entity_vertices[:nc] = cells
    entity_vertices[nc:, :3] = faces
    centers = np.vstack((vertices[cells].mean(axis=1), vertices[faces].mean(axis=1)))
    radii = np.r_[np.linalg.norm(vertices[cells]-centers[:nc, None], axis=2).max(axis=1),
                  np.linalg.norm(vertices[faces]-centers[nc:, None], axis=2).max(axis=1)]
    areas = np.linalg.norm(np.cross(vertices[faces[:, 1]]-vertices[faces[:, 0]],
                                   vertices[faces[:, 2]]-vertices[faces[:, 0]]), axis=1)/2
    assert np.min(radii) > 0 and np.min(areas) > 0
    n = len(centers)
    # ponytail: bounded9180-support census uses row tiles; use a spatial tree
    # for the same conservative radius test when instantiating the full source.
    offsets = [0]
    columns = []
    rule_counts = {str(eta): 0 for eta in (1.5, 2., 3.)}
    for first in range(0, n, 128):
        assert monotonic()-start < 90
        last = min(first+128, n)
        distance = np.linalg.norm(centers[first:last, None]-centers[None], axis=2)
        radius_sum = radii[first:last, None]+radii[None]
        for eta in (1.5, 2., 3.):
            rule_counts[str(eta)] += int(np.count_nonzero(distance <= eta*radius_sum))
        near = distance <= 2.*radius_sum
        for row in near:
            ids = np.flatnonzero(row).astype(np.int32)
            columns.append(ids)
            offsets.append(offsets[-1]+len(ids))
    indices = np.concatenate(columns)
    offsets = np.asarray(offsets, dtype=np.int64)
    near = csr_matrix((np.ones(len(indices), dtype=np.int8), indices, offsets), shape=(n, n))
    assert np.all(near.diagonal() == 1) and (near != near.T).nnz == 0
    valid = entity_vertices >= 0
    entity_rows = np.broadcast_to(np.arange(n)[:, None], entity_vertices.shape)[valid]
    vertex_map = coo_matrix((np.ones(valid.sum(), dtype=np.int8),
                            (entity_rows, entity_vertices[valid])), shape=(n, len(vertices))).tocsr()
    touching = vertex_map @ vertex_map.T
    # Independent topological enumeration verifies no touching pair is sent far.
    assert touching.nnz == touching.multiply(near).nnz
    unique_total = n*(n+1)//2
    unique_near = (near.nnz+n)//2
    vector_near = near[:nc, :nc]
    counts = dict(all_scalar_ordered=n*n, scalar_self=n,
        scalar_near_ordered_including_self=int(near.nnz), scalar_far_ordered=int(n*n-near.nnz),
        scalar_unique_total=unique_total, scalar_unique_near_including_self=int(unique_near),
        scalar_unique_far=int(unique_total-unique_near),
        vector_self=nc, vector_near_ordered_including_self=int(vector_near.nnz),
        vector_far_ordered=int(nc*nc-vector_near.nnz), topologically_touching_ordered=int(touching.nnz))
    assert counts['scalar_unique_far']+counts['scalar_unique_near_including_self'] == unique_total
    artifact = output/'pair-ownership.npz'
    np.savez_compressed(artifact, entity_vertex_ids=entity_vertices, entity_centers_m=centers,
        entity_bounding_radii_m=radii, entity_measures_si=np.r_[volumes, areas],
        boundary_face_ids=boundary, cell_count=np.array([nc]),
        near_row_ptr=offsets, near_col=indices, near_radius_factor=np.array([2.]))
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='COMPLETE_SOURCE_JOINT_PAIR_OWNERSHIP_CENSUS', elapsed_s=monotonic()-start,
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        mesh_sha256=sha256(mesh.read_bytes()).hexdigest(),
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),
        supports=dict(current_tetrahedra=nc, charge_volumes=nc, exterior_charge_faces=nb),
        counts=counts, radius_rule_ordered_counts=rule_counts,
        kernel_ownership='One all-point Green action plus analytic/adaptive near minus identical-quadrature '
        'near point action. Coincident self points are omitted in both point actions. The exact near block '
        'then restores the entire self integral. No pair is deleted and no pair is added twice.',
        scope='Complete combinatorial ownership only. Radius2 is a conservative quadrature-treatment '
        'candidate, not a demonstrated error tolerance. Touching coverage is independently checked through '
        'shared vertex incidence. All9180 cell/exterior charge supports and5304 current cells survive. '
        'No kernel assembly, dielectric/contact elimination, full field, mesh convergence or board accuracy.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
