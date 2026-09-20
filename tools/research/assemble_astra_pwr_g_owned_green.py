"""Reuse exact template self blocks over the actual full PWR component plus G.

All nonself interactions use the existing common point spread/gather action.
Near quadrature and the dielectric/exterior field closure remain explicit work.
"""
import argparse
import json
from pathlib import Path
from time import monotonic

import numpy as np

from apply_astra_owned_3d_green import prepare, apply
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PINS = {
    R/'astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz': 'a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b',
    R/'astra-boundary-conforming-power-joint-01/joint-template.npz': '14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',
    R/'astra-source-joint-all-self-green-02/self-blocks.npz': 'fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd',
    R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz': '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
    R/'astra-g-complete-self-green-20260912/self-blocks.npz': 'd62fb76057f25ab3a68a670c5509922c56c598437b5f3e51da4d321b332c2cca',
}


def assemble():
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    paths = list(PINS)
    with np.load(paths[0], allow_pickle=False) as z:
        xyz = z['vertices_um']; cells = z['cells']; faces = z['face_vertices']
        owner = z['first_owner_cell']; free = z['free_surface_face_ids']
        kind = z['cell_owner_kind']; template = z['cell_template_cell_index']
        columns = z['local_rt0_face_columns']; signs = z['local_rt0_face_signs']
        face_count = int(z['resistance_shape'][0])
    with np.load(paths[1], allow_pickle=False) as z:
        joint_xyz = z['vertices_local_um']; joint_cells = z['cells']
        joint_faces = z['face_vertices']; joint_owner = z['first_owner_cell']
        joint_local = z['first_owner_local_face']; joint_boundary = z['boundary_face_ids']
        assert np.array_equal(joint_cells[2604:5208]-1172, joint_cells[:2604])
    with np.load(paths[2], allow_pickle=False) as z:
        assert z['accepted_at_fixed_gate'].all()
        assert np.array_equal(z['boundary_face_ids'], joint_boundary)
        cached_l = z['static_vector_self_h']; cached_p = z['static_scalar_self_per_m']
    canonical_cell = np.where(kind == 0, template, 5208+template)
    tetra = xyz[cells]
    # Exact ordered owner-cell mapping; no edge-length or nearest-shape matching.
    shape_error = np.max(abs((tetra-tetra[:, :1])-(joint_xyz[joint_cells[canonical_cell]]-joint_xyz[joint_cells[canonical_cell, :1]])))
    assert shape_error < 5e-12, shape_error
    surface_cache = {}
    for ordinal, face in enumerate(joint_boundary):
        cell = int(joint_owner[face]); local = int(joint_local[face])
        key = (0, cell % 2604, local) if cell < 5208 else (1, cell-5208, local)
        surface_cache.setdefault(key, (5304+ordinal, int(face)))
    free_owner = owner[free]
    opposite = np.argmax(columns[free_owner] == free[:, None], axis=1)
    assert np.all(np.sum(columns[free_owner] == free[:, None], axis=1) == 1)
    cache_rows = np.empty(len(free), np.int64)
    canonical_face = np.empty(len(free), np.int64)
    for row, (cell, local) in enumerate(zip(free_owner, opposite)):
        cache_rows[row], canonical_face[row] = surface_cache[(int(kind[cell]), int(template[cell]), int(local))]
    triangles = xyz[faces[free]]
    # Surface orientation is immaterial for unit-integrated scalar charge, but
    # sorted vertices in each source-owned triangle must agree after translation.
    tri_centered = triangles-triangles.mean(axis=1, keepdims=True)
    canonical_tri = joint_xyz[joint_faces[canonical_face]]
    canonical_tri -= canonical_tri.mean(axis=1, keepdims=True)
    vertex_distance = np.max(abs(tri_centered[:, :, None, :]-canonical_tri[:, None, :, :]), axis=3)
    vertex_permutation = np.argmin(vertex_distance, axis=2)
    assert np.all(np.sort(vertex_permutation, axis=1) == np.arange(3))
    triangle_error = np.max(np.min(vertex_distance, axis=2))
    assert triangle_error < 8e-12, triangle_error
    pwr_l = cached_l[canonical_cell]
    pwr_p = np.r_[cached_p[canonical_cell], cached_p[cache_rows]]
    pwr_cell_count = len(tetra); pwr_surface_count = len(triangles)
    with np.load(paths[3], allow_pickle=False) as z:
        g_tetra = z['volume_charge_vertices_um']; g_tri = z['surface_charge_vertices_um']
        g_cols = z['local_current_face_ids']; g_signs = z['local_current_face_signs']
        g_faces = int(z['local_resistance_shape'][0])
    with np.load(paths[4], allow_pickle=False) as z:
        assert z['accepted_at_fixed_gate'].all()
        g_l = z['static_vector_self_h']; g_p = z['static_scalar_self_per_m']
    self_l = np.concatenate([pwr_l, g_l])
    self_p = np.r_[pwr_p[:pwr_cell_count], g_p[:len(g_tetra)], pwr_p[pwr_cell_count:], g_p[len(g_tetra):]]
    prepared = prepare(np.concatenate([tetra, g_tetra])*1e-6,
                       np.concatenate([triangles, g_tri])*1e-6,
                       np.concatenate([columns, g_cols+face_count]),
                       np.concatenate([signs, g_signs]), face_count+g_faces, self_l, self_p)
    metadata = dict(pwr_current_count=face_count, g_current_count=g_faces,
                    pwr_cell_count=pwr_cell_count, g_cell_count=len(g_tetra),
                    pwr_surface_count=pwr_surface_count, g_surface_count=len(g_tri),
                    template_cell_shape_error_um=float(shape_error),
                    template_surface_shape_error_um=float(triangle_error))
    mapping = dict(pwr_canonical_joint_cell=canonical_cell, pwr_free_surface_cache_row=cache_rows,
                   pwr_free_surface_canonical_joint_face=canonical_face,
                   static_vector_self_h=self_l, static_scalar_self_per_m=self_p)
    return prepared, mapping, metadata


def run(output, run_action):
    started = monotonic(); assert not output.exists()
    prepared, mapping, metadata = assemble()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    artifact = output/'template-self-map.npz'
    np.savez_compressed(artifact, **mapping)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='ASSEMBLED_PWR_G_OWNED_SELF_MAPPING', metadata=metadata,
                  points=len(prepared['points']), driver_sha256=sha(Path(__file__)),
                  artifact_sha256=sha(artifact), pins={str(p):h for p,h in PINS.items()},
                  scope='Exact source-template self reuse plus common full-pair point action. No nonself cutoff, no material/background, no exterior sheet action, no near-quadrature certificate, no port solution.')
    (output/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)
    if run_action:
        rng = np.random.default_rng(20260912)
        current = rng.normal(size=prepared['current_count'])+1j*rng.normal(size=prepared['current_count'])
        charge = rng.normal(size=len(mapping['static_scalar_self_per_m']))+1j*rng.normal(size=len(mapping['static_scalar_self_per_m']))
        print(json.dumps(dict(stage='full_pwr_g_all_pair_start', elapsed_s=monotonic()-started)), flush=True)
        action_started = monotonic()
        lvalue, pvalue = apply(prepared, current, charge)
        assert np.isfinite(lvalue).all() and np.isfinite(pvalue).all()
        np.savez_compressed(output/'action.npz', current=current, charge=charge,
                            inductance_action=lvalue, bare_potential_action=pvalue)
        report.update(status='EXECUTED_COMPLETE_PWR_G_SELF_CORRECTED_POINT_ACTION',
                      action_elapsed_s=monotonic()-action_started,
                      magnetic_energy_h_a2=float(np.vdot(current,lvalue).real),
                      scalar_energy_c2_per_m=float(np.vdot(charge,pvalue).real),
                      action_sha256=sha(output/'action.npz'))
        assert report['magnetic_energy_h_a2'] > 0 and report['scalar_energy_c2_per_m'] > 0
    report['elapsed_s'] = monotonic()-started
    (output/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-action', action='store_true')
    args = parser.parse_args(); run(args.output, args.run_action)
