"""SPD Decap PI Evaluator v0.23.1: test zero extension on an actual next bridge.

Local seed construction does not automatically define a conforming global lift.
"""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from qualify_astra_source_joint_self_green import ROOT, PINS


def main():
    output = ROOT/'outputs/research/astra-joint-overlap-current-conformity-02'
    output.mkdir()
    ledger = ROOT/'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json'
    assert sha256(ledger.read_bytes()).hexdigest() == '583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790'
    instances = json.loads(ledger.read_text(encoding='utf-8'))['instances']
    starts = {row['left_pin']: row for row in instances}
    assert len(starts) == len(instances)
    left = next(row for row in instances if row['right_pin'] in starts)
    right = starts[left['right_pin']]
    assert np.allclose(np.array(right['translation_xy_um'])-left['translation_xy_um'], [130., 0.], rtol=0, atol=1e-9)
    mesh = ROOT/list(PINS)[2]
    assert sha256(mesh.read_bytes()).hexdigest() == PINS[list(PINS)[2]]
    with np.load(mesh, allow_pickle=False) as d:
        cells, faces, body = d['cells'], d['face_vertices'], d['cell_body']
        first, shared, boundary = d['first_owner_cell'], d['shared_interface_face_ids'], d['boundary_face_ids']
        xyz = d['vertices_local_um']
    translation_ids = cells[body == 1]-cells[body == 0]
    assert np.unique(translation_ids).size == 1
    post_vertex_count = int(translation_ids[0, 0])
    local_interface = shared[body[first[shared]] == 0]
    assert len(local_interface) == 32
    face_lookup = {tuple(row): i for i, row in enumerate(faces)}
    future_interface = np.array([face_lookup[tuple(row+post_vertex_count)] for row in faces[local_interface]])
    assert np.isin(future_interface, boundary).all() and np.all(body[first[future_interface]] == 1)
    assert np.max(abs(xyz[faces[future_interface]]-xyz[faces[local_interface]]-[130., 0., 0.])) < 1e-12
    space = ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz'
    assert sha256(space.read_bytes()).hexdigest() == '2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'
    with np.load(space, allow_pickle=False) as d:
        modes, names = d['face_flux_seed_modes'], d['seed_names']
    jump = modes[future_interface]
    scale = np.linalg.norm(modes, axis=0)
    relative = np.linalg.norm(jump, axis=0)/scale
    failures = relative > 1e-12
    assert np.count_nonzero(failures) > 0 and not failures[:3].any() and not failures[11:].any()
    artifact = output/'interface-jumps.npz'
    np.savez_compressed(artifact, future_shared_face_ids=future_interface, local_template_interface_face_ids=local_interface,
        zero_extension_current_jump_a=jump, jump_relative_to_full_mode=relative, seed_names=names)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='REJECT_NAIVE_ZERO_EXTENSION_OF_ALL_LOCAL_JOINT_SEEDS',
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), mesh_sha256=PINS[list(PINS)[2]],
        ledger_sha256=sha256(ledger.read_bytes()).hexdigest(), artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),
        actual_adjacent_traces=[left, right], future_shared_faces=len(future_interface),
        rejected_seed_names=names[failures].tolist(), relative_interface_jump=relative.tolist(),
        scope='Two existing adjacent130um source traces identify a real next bridge. The translated '
        '32 exact conforming interface faces are exterior to the first two-post local patch but '
        'interior in the continued source. Zero extension of local gradient seeds gives nonzero '
        'normal-current jumps there. This rejects that naive prolongation, not the saved local '
        'energy seeds. Shared conforming interface fluxes or a constraint-preserving extension '
        'must precede global reduction; overlapping local spaces may instead precondition the '
        'full conforming operator. No new conductor crop, field or board result.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
