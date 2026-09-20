"""Independent saved-array review of the 384-tetra constant-current control."""
from __future__ import annotations

import argparse
import hashlib
import json
from math import factorial, pi
from pathlib import Path

import numpy as np
from numpy.polynomial.legendre import leggauss

import qualify_astra_joint_tm_boundary as source
import qualify_astra_tetra_volume_green as static


ROOT = Path(__file__).resolve().parents[2]
KERNEL_DIR = ROOT / "outputs/research/astra-constant-current-box-kernels-01"
FIELD_DIR = ROOT / "outputs/research/astra-constant-current-384-field-01"
OLD_KERNEL = ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz"
OLD_FIELD_DIR = ROOT / "outputs/research/astra-homogeneous-refined-3d-field-01"
PINS = {
    "kernel_driver": (KERNEL_DIR / "driver-at-run.py", "f8799602c111b98a973ba106ffe3179446ba6238db5f6b71d370e3fb41fb7773"),
    "kernel": (KERNEL_DIR / "kernels.npz", "abd6a03542cf9c315690ed9751b5134f41c293eef6201e75cd59b1acbcf024b8"),
    "kernel_result": (KERNEL_DIR / "result.json", "b29a26512a694d88b705cc9eaab361ebe2e097724d8333ce1cd6163230533308"),
    "kernel_guard": (ROOT / "outputs/research/astra-constant-current-box-kernels-01.resource-guard.json", "04911ecd78ddf637a5befe95c87741ffcfec288e0dec0198cd4de23e7fe501a2"),
    "field_driver": (FIELD_DIR / "driver-at-run.py", "2cd3eeff4071297fede205c91bccac31e25768b6eecb97fa7f4e2ca2aa610a95"),
    "field": (FIELD_DIR / "fields.npz", "9fc218ca9fab49c8d14599b3d226aeb82b30ac1e7db4346cd1fa1acfb37b4c84"),
    "field_result": (FIELD_DIR / "result.json", "c0d0df32b7074f72b34833a3777751b732a7cb7cef7e7e4059bce073d2e262e7"),
    "old_kernel": (OLD_KERNEL, "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02"),
    "old_field": (OLD_FIELD_DIR / "fields.npz", "b01a32fbdb82af9285f1e9205d4f8d5dced506ec67fb22227c731ee7945a6e9a"),
    "old_result": (OLD_FIELD_DIR / "result.json", "f0bb57c49dd51d5f26d1c1ca5701bc937a95055dbfbeefd70d911fc54fb28388"),
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(actual, expected) -> float:
    return float(np.linalg.norm(actual - expected) / max(np.linalg.norm(expected), np.finfo(float).tiny))


def face_key(face) -> tuple:
    return tuple(sorted(map(tuple, np.asarray(face))))


def geometry_checks(tetrahedra, triangles, boundary, incidence, lift, cycles):
    owners: dict[tuple, list[tuple[int, int]]] = {}
    for cell, tetrahedron in enumerate(tetrahedra):
        for local in range(4):
            key = face_key(np.delete(tetrahedron, local, axis=0))
            owners.setdefault(key, []).append((cell, local))
    saved = {face_key(face): index for index, face in enumerate(triangles)}
    assert len(saved) == len(triangles) == len(owners) == 864 and set(saved) == set(owners)
    rebuilt_boundary = np.array(sorted(saved[key] for key, value in owners.items() if len(value) == 1))
    assert np.array_equal(boundary, rebuilt_boundary) and len(boundary) == 192
    expected_abs = np.zeros_like(incidence)
    for key, pairs in owners.items():
        index = saved[key]
        for cell, _ in pairs:
            expected_abs[cell, index] = 1
    assert np.array_equal(abs(incidence), expected_abs)
    assert np.array_equal(incidence.sum(axis=0), np.isin(np.arange(864), boundary).astype(int))
    face_ids = np.argmax(abs(lift), axis=1)
    signs = lift[np.arange(len(lift)), face_ids]
    assert np.array_equal(signs, incidence[np.repeat(np.arange(384), 4), face_ids])
    assert np.array_equal(incidence @ cycles, np.zeros((384, 480), dtype=cycles.dtype))
    return face_ids, signs


def rebuild_tail(moments, frequencies, length):
    output = []
    for frequency in frequencies:
        k = 2 * pi * frequency * np.sqrt(source.MU0 * source.EPS0)
        value = np.zeros_like(moments[0], dtype=complex)
        for power in range(2, 9):
            value += (-1j * k * length) ** power / factorial(power) * moments[power - 2] / length
        output.append(value)
    return np.asarray(output)


def quadrature(tetrahedra, order):
    output = []
    for tetrahedron in tetrahedra:
        points, weights = static.tetra_quadrature(tetrahedron, order)
        volume = static.faces(tetrahedron)[0]
        output.append((points, weights, volume))
    return output


def far_radiation(qdata, k, density, exact_moment, omega):
    # Different angular rule from the producer (20x40 rather than 16x32).
    u, wu = leggauss(20)
    phi = 2 * pi * np.arange(40) / 40
    directions = np.stack(np.broadcast_arrays(np.sqrt(1 - u[:, None] ** 2) * np.cos(phi),
        np.sqrt(1 - u[:, None] ** 2) * np.sin(phi), u[:, None]), axis=-1).reshape(-1, 3)
    weights = np.broadcast_to(wu[:, None] * 2 * pi / 40, (20, 40)).ravel()
    transform = np.broadcast_to(exact_moment, (len(directions), 3, density.shape[2])).copy()
    for cell, (points, qw, _) in enumerate(qdata):
        phase = np.expm1(1j * k * (directions @ points.T))
        transform += np.einsum("np,p,de->nde", phase, qw, density[cell])
    longitudinal = np.einsum("nd,nde->ne", directions, transform)
    transverse = transform - directions[:, :, None] * longitudinal[:, None, :]
    integral = np.sum(weights[:, None, None] * abs(transverse) ** 2, axis=(0, 1))
    return omega * source.MU0 * k / (16 * pi * pi) * integral


def containing_coarse_cells(fine, coarse):
    result = []
    for point in fine.mean(axis=1):
        found = []
        for index, tetrahedron in enumerate(coarse):
            bary = np.linalg.solve((tetrahedron[1:] - tetrahedron[0]).T, point - tetrahedron[0])
            if bary.min() >= -1e-12 and bary.sum() <= 1 + 1e-12:
                found.append(index)
        assert len(found) == 1
        result.append(found[0])
    return np.asarray(result)


def run(output: Path):
    assert not output.exists()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    kernel_result = json.loads(PINS["kernel_result"][0].read_bytes())
    field_result = json.loads(PINS["field_result"][0].read_bytes())
    old_result = json.loads(PINS["old_result"][0].read_bytes())
    guard = json.loads(PINS["kernel_guard"][0].read_bytes())
    assert kernel_result["status"] == "PASS_CONSTANT_CURRENT_BOX_KERNEL_CONTROL"
    assert kernel_result["kernels_sha256"] == PINS["kernel"][1]
    assert field_result["status"] == "COMPLETE_CONSTANT_CURRENT_3D_FIELD_DIAGNOSTIC"
    assert field_result["kernel_sha256"] == PINS["kernel"][1]
    assert field_result["kernel_receipt_sha256"] == PINS["kernel_result"][1]
    assert guard["exit_code"] == 0 and guard["stop_reason"] is None
    assert guard["driver_sha256"] == PINS["kernel_driver"][1]
    for path, expected in kernel_result["pins"].items():
        assert sha(ROOT / path) == expected
    for path, expected in field_result["pins"].items():
        assert sha(ROOT / path) == expected

    with np.load(PINS["kernel"][0], allow_pickle=False) as bundle:
        tet = bundle["tetrahedra_m"]
        tri = bundle["triangles_m"]
        boundary = bundle["boundary_face_indices"]
        incidence = bundle["cell_face_incidence"]
        lift = bundle["local_face_lift"]
        cycles = bundle["solenoidal_face_cycles"]
        volumes = bundle["volumes_m3"]
        kv = bundle["static_volume_per_m"]
        ks = bundle["static_boundary_per_m"]
        mv = bundle["volume_distance_moments"]
        ms = bundle["boundary_distance_moments"]
        tv = bundle["volume_tail_per_m"]
        ts = bundle["boundary_tail_per_m"]
        frequencies = bundle["frequencies_hz"]
    assert tet.shape == (384, 4, 3) and tri.shape == (864, 3, 3)
    assert np.allclose(tet.reshape(-1, 3).min(axis=0), 0, rtol=0, atol=0)
    assert np.allclose(tet.reshape(-1, 3).max(axis=0), [1e-4, 1e-4, 25e-6], rtol=0, atol=1e-20)
    assert np.all(volumes > 0) and np.max(volumes) == np.min(volumes)
    assert abs(volumes.sum() / (1e-4 * 1e-4 * 25e-6) - 1) < 1e-14
    face_ids, signs = geometry_checks(tet, tri, boundary, incidence, lift, cycles)
    assert np.linalg.matrix_rank(incidence.astype(float)) == 384
    assert np.linalg.matrix_rank(cycles.astype(float)) == 480
    length = float(np.linalg.norm([1e-4, 1e-4, 25e-6]))
    tail_volume_error = relative(rebuild_tail(mv, frequencies, length), tv)
    tail_surface_error = relative(rebuild_tail(ms, frequencies, length), ts)
    kernel_symmetry = max(relative(matrix, matrix.T) for matrix in (kv, ks, *mv, *ms, *tv, *ts))
    volume_minimum_eigenvalue = float(np.linalg.eigvalsh(kv).min())
    surface_minimum_eigenvalue = float(np.linalg.eigvalsh(ks).min())

    with np.load(PINS["field"][0], allow_pickle=False) as fields:
        transform = fields["current_transform"]
        q = fields["boundary_divergence_range"]
        h = fields["cell_integrated_current_map"]
        assert transform.shape == (1536, 480) and q.shape == (192, 191) and h.shape == (384, 3, 480)
        local_boundary_rows = np.asarray([np.flatnonzero(face_ids == face)[0] for face in boundary])
        assert np.array_equal(transform.reshape(384, 4, 480).sum(axis=1), np.zeros((384, 480), int))
        assert np.array_equal(transform[local_boundary_rows, :289], np.zeros((192, 289), int))
        assert np.array_equal(q, -transform[local_boundary_rows, 289:])
        assert np.array_equal(q.sum(axis=0), np.zeros(191, int))
        assert np.linalg.matrix_rank(transform.astype(float)) == 480 and np.linalg.matrix_rank(q.astype(float)) == 191
        local_moments = (tet.mean(axis=1)[:, None, :] - tet) / 3
        rebuilt_h = np.einsum("tid,tin->tdn", local_moments, transform.reshape(384, 4, 480))
        h_error = relative(rebuilt_h, h)
        total = np.column_stack((np.zeros((3, 289)), -tri[boundary].mean(axis=1).T @ q))
        moment_identity_error = relative(h.sum(axis=0), total)

        gram = np.einsum("tdi,tdj,t->ij", h, h, 1 / volumes)
        magnetic = source.MU0 / (4 * pi) * sum(h[:, axis].T @ kv @ h[:, axis] for axis in range(3))
        constant_magnetic = source.MU0 / (4 * pi) * total.T @ total
        scalar = q.T @ ks @ q / (4 * pi * source.EPS0)
        rows, properties = source.source_inputs()
        drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
        qdata8 = quadrature(tet, 8)

        with np.load(PINS["old_kernel"][0], allow_pickle=False) as old_bundle:
            old_tet = old_bundle["tetrahedra_m"]
            old_volumes = np.asarray([static.faces(item)[0] for item in old_tet])
            old_local_moments = old_bundle["exact_basis_volume_moments"].reshape(48, 4, 3)
        fine_to_old = containing_coarse_cells(tet, old_tet)
        with np.load(PINS["old_field"][0], allow_pickle=False) as old_fields:
            old_transform = np.column_stack((old_fields["loop_basis"], old_fields["range_basis"]))
            assert old_transform.shape == (192, 72)
            old_h = np.einsum("tid,tin->tdn", old_local_moments, old_transform.reshape(48, 4, 72))
            equation_error = current_error = charge_error = reaction_error = moment_error = dipole_error = 0.0
            extinction_error = absorption_error = radiation_error = power_error = 0.0
            field_change_error = absorption_change_error = dipole_change_error = 0.0
            high_radiation_error = 0.0
            recomputed_cases = []
            for index, (frequency, recorded) in enumerate(zip(frequencies, field_result["cases"], strict=True)):
                omega = 2 * pi * frequency
                k = omega * np.sqrt(source.MU0 * source.EPS0)
                _, _, _, gamma = source.materials(rows, properties, float(frequency))
                kappa = gamma[0] - 1j * omega * source.EPS0
                magnetic_tail = source.MU0 / (4 * pi) * sum(h[:, axis].T @ tv[index] @ h[:, axis] for axis in range(3))
                scalar_tail = q.T @ ts[index] @ q / (4 * pi * source.EPS0)
                zmatrix = gram / kappa + 1j * omega * (magnetic + magnetic_tail - 1j * k * constant_magnetic)
                system = zmatrix.copy()
                system[:, 289:] *= omega
                system[289:, 289:] += (scalar + scalar_tail) / 1j
                mean_phase = np.asarray([np.dot(weights, np.expm1(-1j * k * points[:, 2])) / volume for points, weights, volume in qdata8])
                rhs = total[:2].T + np.einsum("tdn,t->nd", h[:, :2], mean_phase)
                solved = fields[f"case_{index:02d}_scaled_solution"]
                modal = solved.copy()
                modal[289:] *= omega
                current = transform @ modal
                charge = 1j * q @ solved[289:]
                exact_moment = total @ modal
                reaction = rhs.T @ modal
                dipole = tri[boundary].mean(axis=1).T @ charge
                scale = abs(system) @ abs(solved) + abs(rhs)
                equation_error = max(equation_error, float(np.max(abs(system @ solved - rhs) / np.maximum(scale, np.finfo(float).tiny))))
                current_error = max(current_error, relative(current, fields[f"case_{index:02d}_current"]))
                charge_error = max(charge_error, relative(charge, fields[f"case_{index:02d}_boundary_charge"]))
                reaction_error = max(reaction_error, relative(reaction, fields[f"case_{index:02d}_reaction"]))
                moment_error = max(moment_error, relative(exact_moment, fields[f"case_{index:02d}_current_volume_moment"]))
                dipole_error = max(dipole_error, relative(dipole, fields[f"case_{index:02d}_charge_dipole"]))

                u = modal @ drives
                rho = charge @ drives
                jmoment = exact_moment @ drives
                extinction = np.real(np.sum(u.conj() * (rhs @ drives), axis=0))
                absorption = (1 / kappa).real * np.diag(u.conj().T @ gram @ u).real
                radiation = omega * (source.MU0 / (4 * pi) * k * np.sum(abs(jmoment) ** 2, axis=0)
                    - np.diag(u.conj().T @ magnetic_tail.imag @ u).real
                    + np.diag(rho.conj().T @ ts[index].imag @ rho).real / (4 * pi * source.EPS0))
                power = np.max(abs(extinction - absorption - radiation) / (abs(extinction) + abs(absorption) + abs(radiation)))
                extinction_error = max(extinction_error, relative(extinction, recorded["extinction_w"]))
                absorption_error = max(absorption_error, relative(absorption, recorded["absorption_w"]))
                radiation_error = max(radiation_error, relative(radiation, recorded["radiation_w"]))
                power_error = max(power_error, abs(float(power) - recorded["power_relative"]))

                fine_integrated = np.einsum("tdn,ne->tde", h, modal)
                old_solved = old_fields[f"case_{index:02d}_scaled_solution"]
                old_modal = old_solved.copy()
                old_modal[25:] *= omega
                old_integrated = np.einsum("tdn,ne->tde", old_h, old_modal)
                fine_density = fine_integrated / volumes[:, None, None]
                old_density = old_integrated / old_volumes[:, None, None]
                difference = np.sum(volumes[:, None, None] * abs(fine_density - old_density[fine_to_old]) ** 2)
                norm = np.sum(volumes[:, None, None] * abs(fine_density) ** 2)
                field_change = float(np.sqrt(difference / norm))
                old_u = old_modal @ drives
                old_gram = np.einsum("tdi,tdj,t->ij", old_h, old_h, 1 / old_volumes)
                old_absorption = (1 / kappa).real * np.diag(old_u.conj().T @ old_gram @ old_u).real
                absorption_change = float(np.max(abs(absorption - old_absorption) / abs(absorption)))
                old_dipole = old_fields[f"case_{index:02d}_charge_dipole"]
                dipole_change = float(np.linalg.norm(dipole - old_dipole) / np.linalg.norm(dipole))
                field_change_error = max(field_change_error, abs(field_change - recorded["field_l2_change_from_48_tetra"]))
                absorption_change_error = max(absorption_change_error, abs(absorption_change - recorded["absorption_relative_change"]))
                dipole_change_error = max(dipole_change_error, abs(dipole_change - recorded["dipole_relative_change"]))
                if index == len(frequencies) - 1:
                    density_driven = fine_density @ drives
                    independent = far_radiation(quadrature(tet, 4), k, density_driven, jmoment, omega)
                    high_radiation_error = relative(independent, recorded["independent_radiation_w"])
                recomputed_cases.append(dict(frequency_hz=float(frequency), field_change=field_change,
                    absorption_change=absorption_change, dipole_change=dipole_change, power_relative=float(power)))

    checks = dict(
        kernel_guard_elapsed_s=guard["elapsed_s"], kernel_guard_peak_private_bytes=guard["peak_sampled_private_bytes"],
        tetrahedra=len(tet), all_faces=len(tri), boundary_faces=len(boundary), current_unknowns=transform.shape[1],
        loops=289, surface_charge_ranges=q.shape[1], kernel_symmetry_relative=kernel_symmetry,
        volume_minimum_eigenvalue=volume_minimum_eigenvalue, surface_minimum_eigenvalue=surface_minimum_eigenvalue,
        volume_tail_rebuild_relative=tail_volume_error, boundary_tail_rebuild_relative=tail_surface_error,
        current_map_rebuild_relative=h_error, current_moment_identity_relative=moment_identity_error,
        maximum_equation_componentwise_backward=equation_error, maximum_saved_current_relative=current_error,
        maximum_saved_charge_relative=charge_error, maximum_saved_reaction_relative=reaction_error,
        maximum_saved_current_moment_relative=moment_error, maximum_saved_charge_dipole_relative=dipole_error,
        maximum_extinction_relative=extinction_error, maximum_absorption_relative=absorption_error,
        maximum_radiation_relative=radiation_error, maximum_power_metric_absolute_difference=power_error,
        high_frequency_independent_radiation_relative=high_radiation_error,
        maximum_reported_reciprocity=max(case["reciprocal_reaction_relative"] for case in field_result["cases"]),
        field_change_rebuild_absolute=field_change_error, absorption_change_rebuild_absolute=absorption_change_error,
        dipole_change_rebuild_absolute=dipole_change_error, recomputed_cases=recomputed_cases)
    print(json.dumps({key: checks[key] for key in (
        "maximum_equation_componentwise_backward", "maximum_extinction_relative", "maximum_absorption_relative",
        "maximum_radiation_relative", "high_frequency_independent_radiation_relative",
        "field_change_rebuild_absolute", "absorption_change_rebuild_absolute", "dipole_change_rebuild_absolute")}), flush=True)
    assert kernel_symmetry < 1e-13 and volume_minimum_eigenvalue > 0 and surface_minimum_eigenvalue > 0
    assert max(tail_volume_error, tail_surface_error, h_error, moment_identity_error) < 1e-12
    assert max(equation_error, current_error, charge_error, reaction_error, moment_error, dipole_error) < 1e-11
    assert max(extinction_error, absorption_error, radiation_error) < 1e-10
    assert power_error < 1e-10 and high_radiation_error < 1e-10
    assert max(field_change_error, absorption_change_error, dipole_change_error) < 1e-10
    assert min(item["field_change"] for item in recomputed_cases) > 0.5

    output.mkdir(parents=True)
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    review = dict(program="SPD Decap PI Evaluator", version="0.23.1",
        status="ACCEPT_CONSTANT_CURRENT_384_PHYSICS_SAVED_REVIEW", pins={name: expected for name, (_, expected) in PINS.items()},
        reviewer_sha256=sha(Path(__file__)), checks=checks,
        scope="Saved-array review only. The finite, homogeneous, source-free 100x100x25um box passes algebraic and power checks. The 48-to-384 field and absorption changes remain large, so this is not mesh convergence, an actual source solid, a terminal/board model, skin convergence, or PowerSI accuracy.")
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": review["status"], "review_sha256": sha(output / "independent-review.json")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
