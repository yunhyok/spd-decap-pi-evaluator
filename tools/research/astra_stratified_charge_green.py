"""SPD Decap PI Evaluator v0.23.1: fixed-1MHz dielectric-only scalar Green.

This electroquasistatic background has infinite lateral strata and air outside
the source stack. Finite copper is NEVER a background metal or grounded plane.
Internal copper-free z bands are resin-filled; unlike neighboring resins meet
at the copper-band midpoint. These are declared model assumptions, not a
certificate of the finite board dielectric/void geometry.

For exp(+j omega t), eps_r = dk*(1-j*df). The returned k*g uses relative eps:
  [-d_z eps_r d_z + eps_r*k^2] g = delta(z-z_source)
  G(rho,z,z_source) = integral J0(k*rho)*(k*g) dk / (2*pi*EPS0).
Thus homogeneous G=1/(4*pi*EPS0*eps_r*R). Both g and eps_r*g' are
continuous at dielectric interfaces; the source jump of eps_r*g' is -1.
No dielectric polarization unknowns are needed for this specified background.
The resulting P is frequency-dependent, reciprocal (transpose, not adjoint),
and must replace overlapping native dielectric G/C stamps rather than add to
them. This helper does not assemble conductor supports, P, or a port system.

Support integration must split volumes/faces at every dielectric interface.
A support wholly inside one open layer uses that layer's direct singular
coefficient; a face lying on an interface uses the interface coefficient.
Nearby reflected self/near terms must be integrated on the reflected support,
not replaced by a diagonal point value. Zero-thickness edge charge has no
finite Coulomb self integral: sheet boundary charges require finite sidewalls
and the same thickness lift as their current/divergence basis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


EPS0 = 8.8541878128e-12
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs/research"
SOURCE = RESEARCH / "astra-native-frequency-stamps-01/source-frequency-inputs.json"
MATERIAL = RESEARCH / "astra-native-frequency-stamps-01/receipt.json"
PINS = {
    SOURCE: "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc",
    MATERIAL: "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856",
}


def source_background():
    """Return (interfaces_m, eps_r, receipt) from the two frozen source files."""
    for path, expected in PINS.items():
        with path.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == expected, path
    rows = json.loads(SOURCE.read_bytes())["stackup_layers"]
    material = json.loads(MATERIAL.read_bytes())
    assert len(rows) == 95 and len({row["name"] for row in rows}) == 95
    by_name = {row["name"]: i for i, row in enumerate(rows)}
    frequency_index = material["frequencies_hz"].index(1e6)
    profiles = {}
    profile_gap = {}
    for gap in material["gap_properties"]:
        first, last = by_name[gap["upper_layer"]], by_name[gap["lower_layer"]]
        assert last == first + 2, "material profile must name adjacent copper layers"
        name = rows[first + 1]["material"]
        pair = (float(gap["dk"][frequency_index]), float(gap["df"][frequency_index]))
        assert pair[0] > 0 and pair[1] >= 0 and np.isfinite(pair).all()
        if name in profiles:
            assert np.allclose(profiles[name], pair, rtol=1e-14, atol=0), name
        else:
            profiles[name] = pair
            profile_gap[name] = (gap["upper_layer"], gap["lower_layer"])
    assert set(profiles) == {"ABF-GL102", "EL190T"}
    assert np.allclose(profiles["ABF-GL102"], (3.4, .0041), rtol=1e-14, atol=0)
    assert np.allclose(profiles["EL190T"], (4.7, .01), rtol=1e-14, atol=0)
    z = np.r_[0., np.cumsum([row["thickness_um"] for row in rows])]
    assert np.all(np.diff(z) > 0)
    dielectric_eps = {}
    for i, row in enumerate(rows):
        if i % 2 == 0:
            assert row["material"] == "COPPER" and row["conductivity_s_m"] > 0
        else:
            dk, df = profiles[row["material"]]
            dielectric_eps[i] = dk * (1 - 1j * df)
    intervals = []
    changed_voids = []
    for i, row in enumerate(rows):
        if i % 2:
            intervals.append((z[i], z[i + 1], dielectric_eps[i]))
        elif i in (0, len(rows) - 1):
            intervals.append((z[i], z[i + 1], 1. + 0j))
        else:
            above, below = dielectric_eps[i - 1], dielectric_eps[i + 1]
            middle = (z[i] + z[i + 1]) / 2
            intervals.extend(((z[i], middle, above), (middle, z[i + 1], below)))
            if above != below:
                changed_voids.append(dict(layer=row["name"], midpoint_um=middle))
    interfaces, eps = [], [1. + 0j]
    for first, last, value in intervals:
        if value != eps[-1]:
            interfaces.append(first * 1e-6)
            eps.append(value)
    assert eps[-1] == 1 and len(interfaces) == 4 and len(eps) == 5
    receipt = dict(
        program="SPD Decap PI Evaluator", version="0.23.1", frequency_hz=1e6,
        source_pins={str(path.relative_to(ROOT)): digest for path, digest in PINS.items()},
        source_layer_count=len(rows), source_stack_thickness_um=float(z[-1]),
        material_profiles_1mhz={name: dict(dk=pair[0], df=pair[1], source_gap=profile_gap[name])
                               for name, pair in profiles.items()},
        merged_media=["air", "ABF-GL102", "EL190T", "ABF-GL102", "air"],
        interfaces_um=(np.asarray(interfaces) * 1e6).tolist(),
        relative_permittivity_real=[value.real for value in eps],
        relative_permittivity_imag=[value.imag for value in eps],
        differing_resin_void_band_midpoints=changed_voids,
        assumptions=[
            "Infinite lateral dielectric strata; finite board outline, cutouts and voids are unverified.",
            "All copper remains explicit finite conductor geometry, never a background metal/ground.",
            "Internal copper-free bands extend adjacent resin; unlike resins meet at the band midpoint.",
            "Air fills outside the first/last recorded dielectric, with no assumed solder mask.",
            "1MHz electroquasistatic P; static vacuum L is a separate declared approximation.",
            "Copper displacement-permittivity contrast is omitted when existing conduction-only R is used.",
        ],
        scope="Background scalar kernel only; no finite-board, field, port or PowerSI accuracy claim.",
    )
    return np.asarray(interfaces), np.asarray(eps), receipt


def spectral_kg(k, z, z_source, interfaces_m, relative_epsilon):
    """Return k*g for nonnegative scalar/array k; k=0 is its finite limit.

    The interface admittances are eta_up=eps_r*u'/(k*u) and
    eta_down=-eps_r*v'/(k*v), for solutions decaying into each halfspace.
    Only tanh and decaying exponentials occur; no growing transfer matrices.
    """
    k = np.asarray(k, dtype=float)
    interfaces = np.asarray(interfaces_m, dtype=float)
    eps = np.asarray(relative_epsilon, dtype=complex)
    assert np.isfinite(k).all() and np.all(k >= 0)
    assert np.isfinite([z, z_source]).all()
    assert interfaces.ndim == eps.ndim == 1 and len(eps) == len(interfaces) + 1
    assert np.isfinite(interfaces).all() and np.all(np.diff(interfaces) > 0)
    assert np.isfinite(eps).all() and np.all(eps.real > 0) and np.all(eps.imag <= 0)
    xs = np.unique(np.r_[interfaces, z, z_source])
    es = eps[np.searchsorted(interfaces, (xs[:-1] + xs[1:]) / 2)]
    up = np.empty((len(xs),) + k.shape, complex)
    down = np.empty_like(up)
    up[0], down[-1] = eps[0], eps[-1]
    for j, e in enumerate(es):
        t = np.tanh(k * (xs[j + 1] - xs[j]))
        up[j + 1] = e * (up[j] + e * t) / (e + up[j] * t)
    for j in range(len(es) - 1, -1, -1):
        e = es[j]
        t = np.tanh(k * (xs[j + 1] - xs[j]))
        down[j] = e * (down[j + 1] + e * t) / (e + down[j + 1] * t)
    target, source = np.searchsorted(xs, [z, z_source])
    value = 1 / (up[source] + down[source])
    for j in range(min(target, source), max(target, source)):
        ratio = (up[j] if target < source else down[j + 1]) / es[j]
        distance = k * (xs[j + 1] - xs[j])
        value *= 2 * np.exp(-distance) / ((1 + ratio) + (1 - ratio) * np.exp(-2 * distance))
    return value


def halfspace_green(rho, z, z_source, eps_above, eps_below):
    """Exact point Green for a dielectric interface at z=0 (V/C)."""
    assert rho >= 0 and np.isfinite([rho, z, z_source]).all()
    distance = np.hypot(rho, z - z_source)
    assert distance > 0, "point coincidence requires an integrated support self term"
    if z * z_source < 0:
        return 1 / (2 * np.pi * EPS0 * (eps_above + eps_below) * distance)
    host, other = ((eps_above, eps_below) if z < 0 or z_source < 0 else (eps_below, eps_above))
    reflection = (host - other) / (host + other)
    return (1 / distance + reflection / np.hypot(rho, z + z_source)) / (4 * np.pi * EPS0 * host)


def _snap_interface(z, interfaces):
    """Recognize the same source interface despite coordinate conversion ULPs."""
    if not len(interfaces):
        return float(z), None
    nearest = int(np.argmin(abs(interfaces - z)))
    tolerance = 32 * np.finfo(float).eps * max(1e-6, abs(z), float(np.max(abs(interfaces))))
    if abs(interfaces[nearest] - z) <= tolerance:
        return float(interfaces[nearest]), nearest
    return float(z), None


def singular_coefficient(z, interfaces_m, relative_epsilon):
    """Physical coefficient multiplying an owned bare-1/R support integral.

    Inside a layer: 1/(4*pi*EPS0*eps_r). Exactly on a dielectric interface:
    1/(2*pi*EPS0*(eps_left+eps_right)). An interface coefficient applies to an
    interface face, not an entire volume merely touching that interface.
    """
    interfaces = np.asarray(interfaces_m, float)
    eps = np.asarray(relative_epsilon, complex)
    spectral_kg(0., z, z, interfaces, eps)  # Shared material/coordinate validation.
    z, boundary = _snap_interface(z, interfaces)
    if boundary is not None:
        return 1 / (2 * np.pi * EPS0 * (eps[boundary] + eps[boundary + 1]))
    return 1 / (4 * np.pi * EPS0 * eps[np.searchsorted(interfaces, z)])


def point_subtractions(z, z_source, interfaces_m, relative_epsilon):
    """Return analytic terms c*exp(-k*a) removed from relative-epsilon k*g.

    A term has physical real-space value c/(2*pi*EPS0*hypot(rho,a)).
    The direct transmitted path includes an endpoint interface response.
    Same-open-layer pairs additionally remove the single image in each
    adjacent interface; these include every possible coincident image.
    """
    interfaces = np.asarray(interfaces_m, float)
    eps = np.asarray(relative_epsilon, complex)
    spectral_kg(0., z, z_source, interfaces, eps)
    z, target_boundary = _snap_interface(z, interfaces)
    zp, source_boundary = _snap_interface(z_source, interfaces)
    if z == zp:
        coefficient = singular_coefficient(z, interfaces, eps) * (2 * np.pi * EPS0)
    else:
        direction = 1 if z > zp else -1
        if source_boundary is None:
            layer = int(np.searchsorted(interfaces, zp))
            coefficient = 1 / (2 * eps[layer])
        else:
            layer = source_boundary + (direction > 0)
            coefficient = 1 / (eps[source_boundary] + eps[source_boundary + 1])
        crossed = (np.flatnonzero((interfaces > zp) & (interfaces <= z)) if direction > 0
                   else np.flatnonzero((interfaces < zp) & (interfaces >= z))[::-1])
        for _ in crossed:
            next_layer = layer + direction
            coefficient *= 2 * eps[layer] / (eps[layer] + eps[next_layer])
            layer = next_layer
    terms = [dict(kind="direct_or_transmitted", coefficient=complex(coefficient),
                  vertical_distance_m=abs(z - zp))]
    layer = int(np.searchsorted(interfaces, z))
    if source_boundary is None and target_boundary is None and layer == np.searchsorted(interfaces, zp):
        for boundary in (layer - 1, layer):
            if 0 <= boundary < len(interfaces):
                other = boundary if boundary < layer else boundary + 1
                reflection = (eps[layer] - eps[other]) / (eps[layer] + eps[other])
                distance = abs(z - interfaces[boundary]) + abs(zp - interfaces[boundary])
                assert distance > 0, "interface point must use its combined singular coefficient"
                terms.append(dict(kind="adjacent_interface_image", coefficient=complex(reflection / (2 * eps[layer])),
                                  vertical_distance_m=float(distance)))
    return z, zp, terms


def point_green(rho, z, z_source, interfaces_m, relative_epsilon, *, rtol=1e-7,
                atol=0., max_tail_factor=64., integration_limit=1024):
    """Physical noncoincident point Green plus numerical convergence evidence.

    Analytic direct/interface/image terms are restored after Hankel integration
    of the remainder. QUADPACK errors and successive doubled-cutoff changes
    are estimates, not a certified bound. Failure returns converged=False;
    callers must inspect it before using the value. No source pair is dropped.
    """
    from functools import lru_cache
    from scipy.integrate import quad
    from scipy.special import j0

    assert np.isfinite(rho) and rho >= 0 and np.isfinite([rtol, atol]).all()
    assert 0 < rtol < 1 and atol >= 0 and np.isfinite(max_tail_factor) and max_tail_factor >= 32
    assert isinstance(integration_limit, int) and integration_limit >= 64
    interfaces = np.asarray(interfaces_m, float)
    eps = np.asarray(relative_epsilon, complex)
    z, zp, terms = point_subtractions(z, z_source, interfaces, eps)
    if rho == 0 and z == zp:
        raise ValueError("Coincident point: integrate the support using singular_coefficient(), not point_green().")
    coefficients = np.array([term["coefficient"] for term in terms])
    heights = np.array([term["vertical_distance_m"] for term in terms])
    analytic = np.sum(coefficients / np.hypot(rho, heights)) / (2 * np.pi)
    # All zero-distance local singular/image paths are already analytic. The
    # remaining stack reflections travel across at least one finite layer.
    thickness = np.diff(interfaces)
    scale = max(abs(z - zp), float(np.min(thickness)) if len(thickness) else max(rho, abs(z - zp)), 1e-15)
    tolerance = max(atol * EPS0, rtol * abs(analytic))
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Positive finite absolute/relative point tolerance required")
    quadrature_atol = tolerance * (2 * np.pi * scale) / 32
    total, error, evaluations = 0j, 0., 0
    messages, history = [], []
    previous, upper, converged = 0., 8., False
    tail = np.inf
    while upper <= max_tail_factor:
        @lru_cache(maxsize=8192)
        def integrand(t):
            k = t / scale
            residual = spectral_kg(k, z, zp, interfaces, eps) - np.sum(coefficients * np.exp(-k * heights))
            return j0(k * rho) * residual

        # Help QUADPACK resolve Bessel oscillations without requiring a uniform
        # spatial grid or a periodic zero-mode convention.
        step = np.pi * scale / rho if rho else np.inf
        first = int(np.floor(previous / step)) + 1 if np.isfinite(step) else 1
        points = np.arange(first * step, upper, step) if np.isfinite(step) else np.array([])
        if len(points) >= integration_limit // 2:
            messages.append("Oscillation panel budget exceeded; increase integration_limit for this separation.")
            break
        pieces = []
        for component in (lambda t: integrand(t).real, lambda t: integrand(t).imag):
            answer = quad(component, previous, upper, epsabs=quadrature_atol,
                          epsrel=rtol / 16, points=points, limit=integration_limit, full_output=1)
            pieces.append(answer[0])
            error += answer[1] / (2 * np.pi * scale)
            evaluations += answer[2]["neval"]
            if len(answer) > 3:
                messages.append(answer[3])
        increment = complex(*pieces) / (2 * np.pi * scale)
        total += increment
        tail = abs(increment) if previous else np.inf
        value = analytic + total
        target = max(atol * EPS0, rtol * abs(value))
        history.append(dict(cutoff_rad_per_m=upper / scale,
                            tail_change_abs_v_per_c=float(tail / EPS0) if previous else None,
                            quadrature_estimated_abs_v_per_c=float(error / EPS0)))
        if upper >= 32 and max(tail, error) <= target and not messages:
            converged = True
            break
        previous, upper = upper, upper * 2
    value = (analytic + total) / EPS0
    estimated_error = max(tail, error) / EPS0
    return dict(value_v_per_c=complex(value), converged=converged,
                estimated_abs_error_v_per_c=float(estimated_error),
                estimated_relative_error=float(estimated_error / abs(value)) if value else float("inf"),
                quadrature_estimated_abs_v_per_c=float(error / EPS0), evaluations=evaluations,
                refinement_history=history, integration_messages=list(dict.fromkeys(messages)),
                analytic_terms=terms, kernel_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                error_scope="QUADPACK and successive-cutoff estimates; not a certified total discretization or board error.")


def point_self_check():
    """Actual-stack point reciprocity and integration refinement, including interface traces."""
    interfaces, eps, receipt = source_background()
    separations = [
        ("interface25", 225e-6, 25e-6, 25e-6),
        ("sheet55_to75", 35e-6, 55e-6, 75e-6),
        ("near_interface_image", 2e-6, 25e-6 - 1e-9, 25e-6 - 2e-9),
        ("actual_pwr_to_g", float(np.hypot(11890 - 8405, 17277 - 20368)) * 1e-6, 12.5e-6, 65e-6),
        ("axial55_to75", 0., 55e-6, 75e-6),
        ("across_core", 225e-6, 65e-6, 1950e-6),
        ("two_interface_faces", 225e-6, interfaces[0], interfaces[-1]),
        ("outside_opposite_halfspaces", 1e-3, -100e-6, 3100e-6),
    ]
    rows = []
    for name, rho, z, zp in separations:
        coarse = point_green(rho, z, zp, interfaces, eps, rtol=1e-6, max_tail_factor=32)
        fine = point_green(rho, z, zp, interfaces, eps, rtol=1e-8)
        reverse = point_green(rho, zp, z, interfaces, eps, rtol=1e-8)
        assert coarse["converged"] and fine["converged"] and reverse["converged"], name
        refinement = abs(coarse["value_v_per_c"] - fine["value_v_per_c"]) / abs(fine["value_v_per_c"])
        reciprocity = abs(reverse["value_v_per_c"] - fine["value_v_per_c"]) / abs(fine["value_v_per_c"])
        assert refinement < 1e-6 and reciprocity < 1e-8, name
        rows.append(dict(case=name, rho_m=rho, z_m=z, z_source_m=zp,
                         value_real_v_per_c=fine["value_v_per_c"].real,
                         value_imag_v_per_c=fine["value_v_per_c"].imag,
                         refinement_relative=float(refinement), reciprocity_relative=float(reciprocity),
                         estimated_relative_error=fine["estimated_relative_error"], evaluations=fine["evaluations"]))
    boundary = interfaces[0]
    expected = 1 / (2 * np.pi * EPS0 * (eps[0] + eps[1]))
    assert abs(singular_coefficient(25e-6, interfaces, eps) / expected - 1) < 1e-14
    assert point_subtractions(boundary, boundary, interfaces, eps)[2][0]["vertical_distance_m"] == 0
    try:
        point_green(0., boundary, boundary, interfaces, eps)
    except ValueError:
        pass
    else:
        raise AssertionError("Point coincidence must not hide an interface singularity")
    return dict(status="PASS_ACTUAL_STRATIFIED_POINT_GREEN_CHECK", cases=rows,
                source_background=receipt, kernel_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def self_check():
    """One small runnable uniform/halfspace/reciprocity and Hankel check."""
    from scipy.integrate import quad
    from scipy.special import j0

    upper, lower = 1. + 0j, 3.4 * (1 - .0041j)
    k = np.array([0., 1e-6, .3, 3., 300., 3e5])
    reflection = (lower - upper) / (lower + upper)
    errors, reciprocity = [], []
    for z, zp in ((.002, .003), (-.002, .003), (0., .003), (.003, 0.), (0., 0.)):
        uniform = spectral_kg(k, z, zp, [], [lower])
        assert np.allclose(uniform, np.exp(-k * abs(z - zp)) / (2 * lower), rtol=1e-13, atol=0)
        value = spectral_kg(k, z, zp, [0.], [upper, lower])
        exact = ((np.exp(-k * abs(z - zp)) + reflection * np.exp(-k * (z + zp))) / (2 * lower)
                 if z >= 0 and zp >= 0 else np.exp(-k * abs(z - zp)) / (upper + lower))
        keep = abs(exact) > 1e-200
        errors.append(float(np.max(abs(value[keep] - exact[keep]) / abs(exact[keep]))))
        reverse = spectral_kg(k, zp, z, [0.], [upper, lower])
        reciprocity.append(float(np.max(abs(value[keep] - reverse[keep]) / abs(value[keep]))))
        assert abs(value[0] - 1 / (upper + lower)) < 1e-15
    rho, z, zp = .004, .002, .003
    # Subtract the direct singular term before numerical Hankel integration.
    def integrand(wave):
        reflected = spectral_kg(wave, z, zp, [0.], [upper, lower]) - np.exp(-wave * abs(z - zp)) / (2 * lower)
        return j0(wave * rho) * reflected / (2 * np.pi)
    regular = (quad(lambda wave: integrand(wave).real, 0, 12000, epsabs=1e-10)[0]
               + 1j * quad(lambda wave: integrand(wave).imag, 0, 12000, epsabs=1e-10)[0])
    actual = (regular + 1 / (4 * np.pi * lower * np.hypot(rho, z - zp))) / EPS0
    exact = halfspace_green(rho, z, zp, upper, lower)
    hankel_error = float(abs(actual - exact) / abs(exact))
    assert max(errors) < 1e-12 and max(reciprocity) < 1e-12 and hankel_error < 1e-10
    interfaces, eps, receipt = source_background()
    assert np.allclose(spectral_kg(k, 0., 75e-6, interfaces, eps),
                       spectral_kg(k, 75e-6, 0., interfaces, eps), rtol=1e-12, atol=0)
    return dict(status="PASS_STRATIFIED_SCALAR_GREEN_CHECK", spectral_relative=max(errors),
                reciprocity_relative=max(reciprocity), hankel_relative=hankel_error,
                source_background=receipt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, help="Write the tested source background to a new JSON file")
    parser.add_argument("--point-check", action="store_true", help="Run actual-stack point integration checks")
    args = parser.parse_args()
    checked = point_self_check() if args.point_check else self_check()
    if args.receipt:
        with args.receipt.open("x", encoding="utf-8") as stream:
            json.dump(checked["source_background"], stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps(checked, allow_nan=False), flush=True)
