"""SPD Decap PI Evaluator v0.23.1: bounded refined 3-D box kernels.

Uniformly split the accepted 100um x 100um x source-TOP-thickness control
box into 2 x 2 x 2 subcubes, each with the same six Kuhn tetrahedra. Build
paired-orientation static and retarded RT0/P0 Green matrices for a subsequent
two-level field diagnostic. This is a finite-box kernel control, not a source
solid, board model, terminal result, or PowerSI accuracy claim.
"""
from __future__ import annotations

from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import traceback

import numpy as np

import qualify_astra_tetra_volume_green as static
import qualify_astra_tetra_retarded_green as retarded
import qualify_astra_tetra_charge_green as charge
import qualify_astra_charge_retarded_green as electric


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
DEGREE = 8
PINS = {
    "source_helper": ("tools/research/qualify_astra_joint_tm_boundary.py", "10032e17838f0f6f0105051424679c29de3ef83acc63cdc9d8075523368ed247"),
    "source_inputs": ("outputs/research/astra-native-frequency-stamps-01/source-frequency-inputs.json", "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc"),
    "static_helper": ("tools/research/qualify_astra_tetra_volume_green.py", "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb"),
    "static_result": ("outputs/research/astra-tetra-static-green-01/result.json", "d4e7d1dbe65d5f016833f7c14b6c72af6bce5b0f91cf943cdf7e3161f106ad02"),
    "static_blocks": ("outputs/research/astra-tetra-static-green-01/blocks.npz", "d1c0b21d327328d1d42f7c12d1a2bf3009b61addb2d83593fce175044105c654"),
    "retarded_helper": ("tools/research/qualify_astra_tetra_retarded_green.py", "a575dce0fe2d0f87a3b041209d18055f061f04c6f9510f8b0a5cca0b609bf12d"),
    "retarded_result": ("outputs/research/astra-tetra-retarded-green-02/result.json", "51edd52ae9b58334847d9afba72902ed233767f0f977b2a6c56b7c1e267a6ca9"),
    "retarded_blocks": ("outputs/research/astra-tetra-retarded-green-02/blocks.npz", "c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2"),
    "charge_helper": ("tools/research/qualify_astra_tetra_charge_green.py", "aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9"),
    "charge_result": ("outputs/research/astra-tetra-charge-green-01/result.json", "ad2dfe4c8aa6e7c9f6e6d7afd554469655f926f920a50969295665d010983b90"),
    "charge_blocks": ("outputs/research/astra-tetra-charge-green-01/charge.npz", "ed22fe446522687f89f8021fe6d122110bbb4b0282c97377d4a8984d33b02b60"),
    "charge_retarded_helper": ("tools/research/qualify_astra_charge_retarded_green.py", "cbe0f2b0e6a5b414b8fa481119db228a1e82dcd05cd70dbf0bed84e13c39a1c9"),
    "charge_retarded_result": ("outputs/research/astra-charge-retarded-green-02/result.json", "17d1a5c35d06e0481c7735aca117ca7273b41db02b47e1ea313353707ca81de9"),
    "charge_retarded_blocks": ("outputs/research/astra-charge-retarded-green-02/charge.npz", "05c122a8d4d599a7a8371678b7e580fa28d599bc972a3107d8fbf1970890b43c"),
}


def require(condition, message):
    if not bool(condition):
        raise AssertionError(message)


def relative(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), np.finfo(float).tiny))


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def check_deadline(deadline, stage):
    require(monotonic() < deadline, f"deadline exceeded during {stage}")


def verify_pins():
    for name, (path, expected) in PINS.items():
        require(static.source.sha(ROOT / path) == expected, f"pin mismatch: {name}")
    rows, _ = static.source.source_inputs()
    require(rows[0]["name"] == "Signal$TOP", "source TOP row changed")
    require(rows[0]["thickness_um"] == 25.0, "source TOP thickness changed")
    return rows


def refined_geometry(dimensions):
    """Return 48 consistently wound tetrahedra and subcube/local IDs."""
    dimensions = np.asarray(dimensions, float)
    half = dimensions / 2
    unit = np.asarray(static.box_tetrahedra(np.ones(3)))
    tetrahedra, subcube, local = [], [], []
    for ix in range(2):
        for iy in range(2):
            for iz in range(2):
                offset = np.array([ix, iy, iz], float)
                tetrahedra.extend((unit + offset) * half)
                subcube.extend([4 * ix + 2 * iy + iz] * 6)
                local.extend(range(6))
    return np.asarray(tetrahedra), np.asarray(subcube), np.asarray(local)


def charge_topology(tetrahedra):
    """Normalized volume/unique-face charges and local RT0 divergence."""
    face_ids, triangles = {}, []
    divergence = np.zeros((168, 192), dtype=np.int8)
    divergence[:48] = np.repeat(np.eye(48, dtype=np.int8), 4, axis=1)
    for ti, tetra in enumerate(tetrahedra):
        for fi, (triangle, _, _) in enumerate(static.faces(tetra)[1]):
            key = tuple(sorted(map(tuple, triangle)))
            if key not in face_ids:
                face_ids[key] = len(triangles)
                triangles.append(triangle)
            divergence[48 + face_ids[key], 4 * ti + fi] = -1
    require(len(triangles) == 120, f"expected 120 unique faces, got {len(triangles)}")
    require(np.array_equal(divergence.sum(axis=0), np.zeros(192)), "B columns not neutral")
    require(np.all(np.sum(divergence == 1, axis=0) == 1), "B volume incidence")
    require(np.all(np.sum(divergence == -1, axis=0) == 1), "B face incidence")
    return np.asarray(triangles), divergence


def mass_and_moments(tetrahedra):
    mass = np.zeros((192, 192))
    moments = np.empty((192, 3))
    volumes = np.empty(48)
    for ti, tetra in enumerate(tetrahedra):
        volume, _ = static.faces(tetra)
        delta = tetra.mean(axis=0) - tetra
        variance = np.sum(delta * delta) / 20
        block = (variance + delta @ delta.T) / (9 * volume)
        sl = slice(4 * ti, 4 * ti + 4)
        mass[sl, sl] = block
        moments[sl] = delta / 3
        volumes[ti] = volume
    return mass, moments, volumes


def uniform_flux(tetrahedra):
    return np.asarray([[area * normal for _, normal, area in static.faces(tetra)[1]]
                       for tetra in tetrahedra])


def static_magnetic(tetrahedra, subcube, local, old_blocks, order, deadline):
    """Raw analytic-inner rule; caller forms the orientation-paired average."""
    raw = np.zeros((192, 192))
    for a, ta in enumerate(tetrahedra):
        sa = slice(4 * a, 4 * a + 4)
        for b, tb in enumerate(tetrahedra):
            sb = slice(4 * b, 4 * b + 4)
            if subcube[a] == subcube[b]:
                raw[sa, sb] = .5 * old_blocks[local[a], local[b]]
            else:
                check_deadline(deadline, "static magnetic cross-subcubes")
                raw[sa, sb] = static.tetra_pair(ta, tb, order)
        if a % 8 == 7:
            print(json.dumps({"stage": "static_magnetic", "tetrahedra": a + 1}), flush=True)
    return raw


def vector_qdata(tetrahedra, order):
    result = []
    for tetra in tetrahedra:
        points, weights = static.tetra_quadrature(tetra, order)
        volume, _ = static.faces(tetra)
        weighted = weights[:, None, None] * (points[:, None, :] - tetra) / (3 * volume)
        result.append((points, weighted))
    return result


def vector_pair_moments(a, b, length):
    pa, wa = a
    pb, wb = b
    radius = np.linalg.norm(pa[:, None, :] - pb[None, :, :], axis=2) / length
    require(radius.max() <= 1 + 2e-14, "normalized vector distance exceeds box diagonal")
    result = np.zeros((DEGREE - 1, 4, 4))
    power = radius.copy()
    for p in range(DEGREE - 1):
        for axis in range(3):
            result[p] += wa[:, :, axis].T @ (power @ wb[:, :, axis])
        power *= radius
    return result


def vector_distance_moments(tetrahedra, subcube, local, old_moments, length,
                            order, deadline):
    qdata = vector_qdata(tetrahedra, order)
    result = np.zeros((DEGREE - 1, 192, 192))
    for a in range(48):
        sa = slice(4 * a, 4 * a + 4)
        for b in range(a, 48):
            sb = slice(4 * b, 4 * b + 4)
            if subcube[a] == subcube[b]:
                # Array index p stores (R/L)^(p+1); affine RT0 double volume
                # integration scales as s^2, so the total factor is s^(p+3).
                block = np.asarray([
                    (.5 ** (p + 3)) * .5 * (
                        old_moments[p, local[a], local[b]]
                        + old_moments[p, local[b], local[a]].T
                    ) for p in range(DEGREE - 1)
                ])
            else:
                check_deadline(deadline, "vector distance moments")
                block = vector_pair_moments(qdata[a], qdata[b], length)
            result[:, sa, sb] = block
            result[:, sb, sa] = block.transpose(0, 2, 1)
        if a % 8 == 7:
            print(json.dumps({"stage": "vector_moments", "tetrahedra": a + 1}), flush=True)
    return result


def magnetic_tails(moments, frequencies, length):
    wave = 2 * np.pi * frequencies * np.sqrt(static.source.MU0 * static.source.EPS0)
    tails = []
    for k in wave:
        require(abs(k * length) <= 1, "retarded series outside qualified |kL| range")
        matrix = np.zeros((192, 192), complex)
        for n in range(2, DEGREE + 1):
            matrix += (-1j * k * length) ** n / factorial(n) * moments[n - 2] / length
        tails.append(1e-7 * matrix)
    return np.asarray(tails), wave


def exact_inner_average(source, points):
    origin = source[0]
    inner = static.tetra_inner if len(source) == 4 else static.triangle_moments
    return inner(source - origin, points - origin)[0] / charge.measure(source)


def scalar_static(entities, order, deadline):
    qdata = [charge.quadrature(entity, order) for entity in entities]
    count = len(entities)
    raw = np.zeros((count, count))
    for a in range(count):
        pa, wa = qdata[a]
        for b in range(a, count):
            check_deadline(deadline, "static scalar pairs")
            pb, wb = qdata[b]
            raw[a, b] = wa @ exact_inner_average(entities[b], pa)
            if b != a:
                raw[b, a] = wb @ exact_inner_average(entities[a], pb)
        if a % 16 == 15:
            print(json.dumps({"stage": "static_scalar", "entities": a + 1}), flush=True)
    return raw


def scalar_pair_moments(a, b, length):
    pa, wa = a
    pb, wb = b
    radius = np.linalg.norm(pa[:, None, :] - pb[None, :, :], axis=2) / length
    require(radius.max() <= 1 + 2e-14, "normalized scalar distance exceeds box diagonal")
    weights = wa[:, None] * wb[None, :]
    result = np.empty(DEGREE - 1)
    power = radius.copy()
    for p in range(DEGREE - 1):
        result[p] = np.sum(weights * power)
        power *= radius
    return result


def scalar_distance_moments(entities, vector_moments, flux, volumes, length,
                            order, deadline):
    result = np.zeros((DEGREE - 1, 168, 168))
    blocks = vector_moments.reshape(DEGREE - 1, 48, 4, 48, 4)
    result[:, :48, :48] = np.einsum(
        "ai,paibj,bj->pab", flux[:, :, 0], blocks, flux[:, :, 0]
    ) / (volumes[None, :, None] * volumes[None, None, :])
    qdata = [charge.quadrature(entity, order) for entity in entities]
    for a in range(168):
        for b in range(max(a, 48), 168):
            check_deadline(deadline, "scalar retarded moments")
            value = scalar_pair_moments(qdata[a], qdata[b], length)
            result[:, a, b] = value
            result[:, b, a] = value
        if a % 16 == 15:
            print(json.dumps({"stage": "scalar_moments", "entities": a + 1}), flush=True)
    return result


def scalar_tails(moments, wave, length):
    result = []
    for k in wave:
        matrix = np.zeros((168, 168), complex)
        for n in range(2, DEGREE + 1):
            matrix += (-1j * k * length) ** n / factorial(n) * moments[n - 2] / length
        result.append(matrix)
    return np.asarray(result)


def sampled_refinement(tetrahedra, entities, length, deadline):
    tet_pairs = ((0, 6), (0, 7), (0, 12), (5, 47), (11, 24), (23, 40))
    mag8, mag16, vec4, vec6 = [], [], [], []
    q4, q6 = vector_qdata(tetrahedra, 4), vector_qdata(tetrahedra, 6)
    for a, b in tet_pairs:
        check_deadline(deadline, "sampled vector refinement")
        mag8.append((static.tetra_pair(tetrahedra[a], tetrahedra[b], 8) +
                     static.tetra_pair(tetrahedra[b], tetrahedra[a], 8).T) / 2)
        mag16.append((static.tetra_pair(tetrahedra[a], tetrahedra[b], 16) +
                      static.tetra_pair(tetrahedra[b], tetrahedra[a], 16).T) / 2)
        vec4.append(vector_pair_moments(q4[a], q4[b], length))
        vec6.append(vector_pair_moments(q6[a], q6[b], length))
    entity_pairs = ((0, 48), (0, 119), (47, 167), (48, 49), (48, 167), (93, 132))
    scalar8, scalar16, tail6, tail8 = [], [], [], []
    qs6 = [charge.quadrature(entity, 6) for entity in entities]
    qs8 = [charge.quadrature(entity, 8) for entity in entities]
    for a, b in entity_pairs:
        check_deadline(deadline, "sampled scalar refinement")
        pa8, wa8 = charge.quadrature(entities[a], 8)
        pb8, wb8 = charge.quadrature(entities[b], 8)
        pa16, wa16 = charge.quadrature(entities[a], 16)
        pb16, wb16 = charge.quadrature(entities[b], 16)
        scalar8.append(.5 * (wa8 @ exact_inner_average(entities[b], pa8) +
                             wb8 @ exact_inner_average(entities[a], pb8)))
        scalar16.append(.5 * (wa16 @ exact_inner_average(entities[b], pa16) +
                              wb16 @ exact_inner_average(entities[a], pb16)))
        tail6.append(scalar_pair_moments(qs6[a], qs6[b], length))
        tail8.append(scalar_pair_moments(qs8[a], qs8[b], length))
    return {
        "static_magnetic_q8_to_q16_relative": relative(mag8, mag16),
        "vector_moments_q4_to_q6_relative": relative(vec4, vec6),
        "static_scalar_q8_to_q16_relative": relative(scalar8, scalar16),
        "scalar_moments_q6_to_q8_relative": relative(tail6, tail8),
        "tetra_pair_count": len(tet_pairs),
        "entity_pair_count": len(entity_pairs),
    }


def topology_self_check():
    dimensions = np.array([1e-4, 1e-4, 25e-6])
    tetrahedra, subcube, local = refined_geometry(dimensions)
    triangles, divergence = charge_topology(tetrahedra)
    mass, moments, volumes = mass_and_moments(tetrahedra)
    flux = uniform_flux(tetrahedra)
    entities = list(tetrahedra) + list(triangles)
    centroids = np.asarray([entity.mean(axis=0) for entity in entities])
    require(tetrahedra.shape == (48, 4, 3), "tetra shape")
    require(triangles.shape == (120, 3, 3), "triangle shape")
    require(len(np.unique(subcube)) == 8 and np.array_equal(np.unique(local), np.arange(6)), "subcube/local IDs")
    require(np.linalg.eigvalsh(mass).min() > 0, "mass positivity")
    require(np.all(volumes > 0), "positive tetra volumes")
    require(relative(centroids.T @ divergence, -moments.T) < 2e-15, "B first moments")
    surface = divergence @ flux.reshape(192, 3)
    require(np.linalg.norm(surface[:48]) < 1e-14 * np.linalg.norm(surface), "uniform bulk divergence")
    interior = np.flatnonzero(np.sum(divergence[48:] != 0, axis=1) == 2)
    require(len(interior) == 72, "internal-face count")
    require(np.linalg.norm(surface[48 + interior]) < 1e-14 * np.linalg.norm(flux), "uniform internal face jumps")
    print("PASS_REFINED_3D_BOX_KERNEL_SELF_CHECK")


def run(output, max_seconds, max_rss_gib):
    started = monotonic()
    deadline = started + max_seconds
    require(not output.exists(), "output already exists")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        rows = verify_pins()
        with np.load(ROOT / PINS["static_blocks"][0], allow_pickle=False) as data:
            old_tetrahedra = data["tetrahedra_m"]
            old_static = data["box_local_rt0_blocks"]
        with np.load(ROOT / PINS["retarded_blocks"][0], allow_pickle=False) as data:
            old_vector_moments = data["distance_moments"]
            frequencies = data["frequencies_hz"]
        require(np.array_equal(frequencies, np.asarray(static.source.FREQUENCIES)), "frequency grid")
        dimensions = np.array([1e-4, 1e-4, rows[0]["thickness_um"] * 1e-6])
        tetrahedra, subcube, local = refined_geometry(dimensions)
        require(np.allclose(tetrahedra[:6], old_tetrahedra * .5, rtol=0, atol=1e-20), "half-scale qualified Kuhn cell")
        triangles, divergence = charge_topology(tetrahedra)
        entities = list(tetrahedra) + list(triangles)
        mass, moments, volumes = mass_and_moments(tetrahedra)
        flux = uniform_flux(tetrahedra)
        length = float(np.linalg.norm(dimensions))
        centroids = np.asarray([entity.mean(axis=0) for entity in entities])
        require(relative(centroids.T @ divergence, -moments.T) < 2e-13, "B/moment identity")

        raw_l = static_magnetic(tetrahedra, subcube, local, old_static, 16, deadline)
        static_l = (raw_l + raw_l.T) / 2
        vector_moments = vector_distance_moments(tetrahedra, subcube, local, old_vector_moments, length, 6, deadline)
        tails_l, wave = magnetic_tails(vector_moments, frequencies, length)
        raw_p = scalar_static(entities, 16, deadline)
        static_p = (raw_p + raw_p.T) / 2
        scalar_moments = scalar_distance_moments(entities, vector_moments, flux, volumes, length, 8, deadline)
        tails_p = scalar_tails(scalar_moments, wave, length)
        refinement = sampled_refinement(tetrahedra, entities, length, deadline)

        uniform = flux.reshape(192, 3)
        surface = divergence @ uniform
        density = np.r_[volumes, np.zeros(120)]
        box_reference, reference_error = static.box_self_reference(dimensions)
        polarization_reference = charge.uniform_polarization_reference(dimensions)
        magnetic_energy = np.diag(uniform.T @ static_l @ uniform)
        polarization_energy = np.diag(surface.T @ static_p @ surface)
        static_l_min = float(np.linalg.eigvalsh(static_l).min())
        static_p_min = float(np.linalg.eigvalsh(static_p).min())
        basis_absolute_integrals = np.asarray([
            max(np.linalg.norm(tetra - vertex, axis=1)) / 3
            for tetra in tetrahedra for vertex in tetra
        ])
        x = float(np.max(wave) * length)
        magnetic_series_bound = (1e-7 / length * np.exp(x) * x ** (DEGREE + 1)
                                 / factorial(DEGREE + 1)
                                 * np.linalg.norm(np.outer(basis_absolute_integrals,
                                                           basis_absolute_integrals)))
        scalar_series_bound = (len(entities) / length * np.exp(x)
                               * x ** (DEGREE + 1) / factorial(DEGREE + 1))
        raw_l_skew = relative(raw_l, raw_l.T)
        raw_p_skew = relative(raw_p, raw_p.T)
        checks = {
            "distributional_column_sum_max": int(np.max(np.abs(divergence.sum(axis=0)))),
            "moment_identity_relative": relative(centroids.T @ divergence, -moments.T),
            "mass_minimum_eigenvalue": float(np.linalg.eigvalsh(mass).min()),
            "static_magnetic_minimum_eigenvalue_h": static_l_min,
            "static_scalar_minimum_eigenvalue_per_m": static_p_min,
            "raw_static_magnetic_reciprocity": raw_l_skew,
            "raw_static_scalar_reciprocity": raw_p_skew,
            "paired_static_magnetic_reciprocity": relative(static_l, static_l.T),
            "paired_static_scalar_reciprocity": relative(static_p, static_p.T),
            "magnetic_uniform_box_relative_error": relative(magnetic_energy, np.full(3, 1e-7 * box_reference)),
            "scalar_uniform_volume_relative_error": float(abs(density @ static_p @ density / box_reference - 1)),
            "polarization_relative_error": relative(polarization_energy, polarization_reference),
            "trace_delta_self_relative_error": float(abs(polarization_energy.sum() / (4 * np.pi * dimensions.prod()) - 1)),
            "gaussian_reference_relative_error": float(reference_error),
            "magnetic_tail_reciprocity_max": float(max(relative(x, x.T) for x in tails_l)),
            "scalar_tail_reciprocity_max": float(max(relative(x, x.T) for x in tails_p)),
            "maximum_kr": float(np.max(wave) * length),
            "magnetic_series_remainder_frobenius_bound_h": float(magnetic_series_bound),
            "magnetic_series_bound_relative_to_tail": float(magnetic_series_bound /
                                                               np.linalg.norm(tails_l[-1])),
            "scalar_series_remainder_frobenius_bound_per_m": float(scalar_series_bound),
            "scalar_series_bound_relative_to_tail": float(scalar_series_bound /
                                                             np.linalg.norm(tails_p[-1])),
        }
        gates = {
            "topology": checks["distributional_column_sum_max"] == 0,
            "moment_identity": checks["moment_identity_relative"] < 1e-12,
            "positive_mass": checks["mass_minimum_eigenvalue"] > 0,
            "positive_static_magnetic": static_l_min > 0,
            "positive_static_scalar": static_p_min > 0,
            "paired_reciprocity": checks["paired_static_magnetic_reciprocity"] < 1e-14 and checks["paired_static_scalar_reciprocity"] < 1e-14,
            "raw_quadrature_disagreement_recorded": raw_l_skew < 1e-5 and raw_p_skew < 1e-4,
            "uniform_box": checks["magnetic_uniform_box_relative_error"] < 1e-5 and checks["scalar_uniform_volume_relative_error"] < 1e-5,
            "polarization": checks["polarization_relative_error"] < 1e-4 and checks["trace_delta_self_relative_error"] < 1e-4,
            "retarded_reciprocity": checks["magnetic_tail_reciprocity_max"] < 1e-13 and checks["scalar_tail_reciprocity_max"] < 1e-13,
            "sampled_refinement": refinement["static_magnetic_q8_to_q16_relative"] < 1e-3 and refinement["vector_moments_q4_to_q6_relative"] < 2e-3 and refinement["static_scalar_q8_to_q16_relative"] < 2e-3 and refinement["scalar_moments_q6_to_q8_relative"] < 2e-3,
            "series_range": checks["maximum_kr"] < 1,
            "bounded_series": checks["magnetic_series_bound_relative_to_tail"] < 1e-16 and checks["scalar_series_bound_relative_to_tail"] < 1e-16,
        }
        arrays = {
            "tetrahedra_m": tetrahedra,
            "triangles_m": triangles,
            "distributional_divergence": divergence,
            "geometric_mass": mass,
            "exact_basis_volume_moments": moments,
            "static_magnetic_h": static_l,
            "magnetic_tail_h": tails_l,
            "static_scalar_per_m": static_p,
            "scalar_tail_per_m": tails_p,
            "frequencies_hz": frequencies,
            "raw_static_magnetic_h": raw_l,
            "raw_static_scalar_per_m": raw_p,
            "vector_distance_moments": vector_moments,
            "scalar_distance_moments": scalar_moments,
            "uniform_current_face_flux": uniform,
            "uniform_current_charge": surface,
            "polarization_reference_m3": polarization_reference,
            "subcube_id": subcube,
            "local_tetrahedron_id": local,
        }
        for name, value in arrays.items():
            require(np.all(np.isfinite(value)), f"nonfinite array: {name}")
        kernel_path = output / "kernels.npz"
        with kernel_path.open("xb") as stream:
            np.savez_compressed(stream, **arrays)
        status = "PASS_REFINED_3D_BOX_KERNEL_CONTROL" if all(gates.values()) else "STOP_REFINED_3D_BOX_KERNEL_CONTROL"
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": status,
            "gates": gates,
            "script_sha256": static.source.sha(Path(__file__)),
            "kernels_sha256": static.source.sha(kernel_path),
            "pins": {name: {"path": path, "sha256": expected} for name, (path, expected) in PINS.items()},
            "geometry": {"dimensions_m": dimensions.tolist(), "subdivision": [2, 2, 2], "subcubes": 8, "tetrahedra": 48, "current_dofs": 192, "unique_faces": 120, "charge_entities": 168},
            "quadrature": {"static_cross_tetra_outer_order": 16, "vector_regular_cross_order": 6, "static_scalar_outer_order": 16, "scalar_regular_order": 8, "pairing": "two-orientation arithmetic Galerkin average; raw static matrices retained", "within_subcube": "frozen six-tetra static q32 blocks scaled by 1/2 and q16 distance-moment array index p scaled by s^(p+3), representing physical power p+1"},
            "checks": checks,
            "sampled_refinement": refinement,
            "elapsed_s": monotonic() - started,
            "resource_contract": {"internal_max_seconds": max_seconds, "requested_external_max_private_gib": max_rss_gib, "rss_not_measured_or_enforced_by_this_helper": True},
            "scope": "Uniform 2x2x2 refinement of the accepted 100um x 100um x source-TOP25um finite-box control. Static singular terms use analytic source-inner integration and positive observer quadrature; regular exp(-ikR)/R terms retain degree8. Paired orientations define a symmetric Galerkin quadrature while raw static disagreement remains saved. The lateral box is controlled, not extracted source copper. This does not qualify source trace endcaps, via barrels, material interfaces, a converged field, terminal/board impedance, or PowerSI accuracy.",
        }
        write_json(output / "result.json", result)
        print(json.dumps({"status": status, "gates": gates, "kernels_sha256": result["kernels_sha256"], "elapsed_s": result["elapsed_s"]}), flush=True)
        return 0 if all(gates.values()) else 2
    except BaseException as error:
        write_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_REFINED_3D_BOX_KERNEL_EXCEPTION", "script_sha256": static.source.sha(Path(__file__)), "exception_type": type(error).__name__, "exception": str(error), "elapsed_s": monotonic() - started, "traceback": traceback.format_exc()})
        raise


def main(args):
    if args.self_check:
        topology_self_check()
        return 0
    if args.dry_check:
        rows = verify_pins()
        dimensions = np.array([1e-4, 1e-4, rows[0]["thickness_um"] * 1e-6])
        tetrahedra, _, _ = refined_geometry(dimensions)
        triangles, divergence = charge_topology(tetrahedra)
        require(tetrahedra.shape == (48, 4, 3) and triangles.shape == (120, 3, 3), "dry geometry schema")
        require(divergence.shape == (168, 192), "dry divergence schema")
        print("PASS_REFINED_3D_BOX_KERNEL_DRY_CHECK")
        return 0
    require(args.output is not None, "--output required")
    return run(args.output.resolve(), args.max_seconds, args.max_rss_gib)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-check", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=180.0)
    parser.add_argument("--max-rss-gib", type=float, default=24.0)
    raise SystemExit(main(parser.parse_args()))
