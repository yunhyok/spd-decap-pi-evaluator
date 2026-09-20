"""SPD Decap PI Evaluator v0.23.1: independent saved six-Cu-curl review."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / "tools/research/qualify_astra_source_copper_curls.py"
OUT = ROOT / "outputs/research/astra-source-copper-curl-space-01"
REVIEW = ROOT / "outputs/research/astra-source-copper-curl-space-review-01"
PINS = {
    PRODUCER: "12a49f050a730787e3897e0d6c5d5daca05d17cbef85df6eae587bf5500a14bb",
    OUT / "driver-at-run.py": "12a49f050a730787e3897e0d6c5d5daca05d17cbef85df6eae587bf5500a14bb",
    OUT / "result.json": "3503459be635dfd3e1b967ee012a63f38a8f50932535a0847c447fb160610ea4",
    OUT / "space.npz": "e0e6191c54478c51baed4bc03468aab73ce0ccb17e88c0dec70dad75bcc47ede",
    ROOT / "outputs/research/astra-total-current-material-basis-01/basis.npz":
        "1919e0eb246c72814b643ea77a6e0dc29850a1b57b3dda278fce26e51bad4964",
    ROOT / "outputs/research/astra-source-potential-terminal-02/fields.npz":
        "33262a8b2fad33a718af80e7cf341f9611d65d5fa18e28447107528503b1544d",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def poly(*coefficients: float) -> np.ndarray:
    return np.asarray(coefficients, float)


ONE = poly(1)
PHI = poly(1, 0, -1)
ODD = poly(0, 1, 0, -1)


def derivative(p: np.ndarray) -> np.ndarray:
    return np.arange(1, len(p)) * p[1:]


def modes(dim: np.ndarray):
    """Explicit curls in the saved order: Ay(phi,phi), Ay(phi,odd), Az(phi,odd)."""
    length = float(max(dim))
    potentials = [(1, [PHI, ONE, PHI]), (1, [PHI, ONE, ODD]), (2, [PHI, ODD, ONE])]
    result = []
    for axis, factors in potentials:
        a, b = (axis + 1) % 3, (axis + 2) % 3
        terms = {}
        for component, differentiated, sign in ((a, b, 1), (b, a, -1)):
            q = list(factors)
            q[differentiated] = derivative(q[differentiated])
            terms[component] = (sign * 2 * length / dim[differentiated], q)
        result.append(terms)
    return result


def evaluate(points: np.ndarray, center: np.ndarray, dim: np.ndarray) -> np.ndarray:
    s = 2 * (points - center) / dim
    answer = np.zeros((len(points), 3, 3))
    for column, terms in enumerate(modes(dim)):
        for component, (scale, factors) in terms.items():
            value = np.full(len(points), scale)
            for axis, p in enumerate(factors):
                value *= np.polynomial.polynomial.polyval(s[:, axis], p)
            answer[:, component, column] = value
    return answer


def divergence_values(points: np.ndarray, center: np.ndarray, dim: np.ndarray) -> np.ndarray:
    s = 2 * (points-center)/dim
    answer = np.zeros((len(points), 3))
    for column, terms in enumerate(modes(dim)):
        for component, (scale, factors) in terms.items():
            q = list(factors); q[component] = derivative(q[component])
            value = np.full(len(points), scale*2/dim[component])
            for axis, p in enumerate(q):
                value *= np.polynomial.polynomial.polyval(s[:, axis], p)
            answer[:, column] += value
    return answer


def tensor_rule(center: np.ndarray, dim: np.ndarray, order: int):
    x, w = np.polynomial.legendre.leggauss(order)
    xx = np.stack(np.meshgrid(x, x, x, indexing="ij"), axis=-1).reshape(-1, 3)
    ww = (w[:, None, None] * w[None, :, None] * w[None, None, :]).ravel()
    return center + xx * dim / 2, ww * np.prod(dim) / 8


def tetra_rule(tetrahedron: np.ndarray, order: int):
    x, w = np.polynomial.legendre.leggauss(order)
    x, w = (x + 1) / 2, w / 2
    u, v, z = np.meshgrid(x, x, x, indexing="ij")
    wu, wv, wz = np.meshgrid(w, w, w, indexing="ij")
    u, v, z = u.ravel(), v.ravel(), z.ravel()
    p = ((1-u)[:, None] * tetrahedron[0]
         + (u*(1-v))[:, None] * tetrahedron[1]
         + (u*v*(1-z))[:, None] * tetrahedron[2]
         + (u*v*z)[:, None] * tetrahedron[3])
    volume = abs(np.linalg.det((tetrahedron[1:] - tetrahedron[0]).T)) / 6
    weights = (wu * wv * wz).ravel() * 6 * volume * u**2 * v
    return p, weights, volume


def parity(p: np.ndarray) -> int:
    degrees = np.flatnonzero(p)
    assert len(degrees) and np.all(degrees % 2 == degrees[0] % 2)
    return int(degrees[0] % 2)


def correlation(offset: np.ndarray, left: np.ndarray, right: np.ndarray, order: int = 10):
    x, w = np.polynomial.legendre.leggauss(order)
    a = offset[:, None] + (1-offset[:, None]) * x
    b = -offset[:, None] + (1-offset[:, None]) * x
    return ((1-offset) * ((np.polynomial.polynomial.polyval(a, left)
                           * np.polynomial.polynomial.polyval(b, right)) @ w))


def self_green(dim: np.ndarray, radial_order: int = 28, angular_order: int = 42):
    length, volume = float(max(dim)), float(np.prod(dim))
    xr, wr = np.polynomial.legendre.leggauss(radial_order)
    xa, wa = np.polynomial.legendre.leggauss(angular_order)
    r, wr = (xr+1)/2, wr/2
    a, wa = (xa+1)/2, wa/2
    rr, aa, bb = np.meshgrid(r, a, a, indexing="ij")
    weights = (wr[:, None, None] * wa[None, :, None] * wa[None, None, :]).ravel() * rr.ravel()**2
    rr, aa, bb = rr.ravel(), aa.ravel(), bb.ravel()
    answer = np.zeros((9, 3, 3))
    mm = modes(dim)
    for major in range(3):
        other = [axis for axis in range(3) if axis != major]
        offsets = np.empty((len(rr), 3))
        offsets[:, major] = rr
        offsets[:, other[0]] = rr*aa
        offsets[:, other[1]] = rr*bb
        radius = np.linalg.norm(offsets * dim/length, axis=1)
        powers = np.vstack((weights/radius, *[weights*radius**p for p in range(8)]))
        for i, left in enumerate(mm):
            for j, right in enumerate(mm):
                overlap = np.zeros(len(rr))
                for component in left.keys() & right.keys():
                    sl, pl = left[component]; sr, pr = right[component]
                    if any(parity(x) != parity(y) for x, y in zip(pl, pr)):
                        continue
                    term = np.full(len(rr), sl*sr)
                    for axis in range(3):
                        term *= correlation(offsets[:, axis], pl[axis], pr[axis])
                    overlap += term
                answer[:, i, j] += powers @ overlap
    answer[0] *= volume**2/length
    answer[1:] *= volume**2
    return answer


def interlayer_green(centers: np.ndarray, dims: np.ndarray, xy_order: int = 40, z_order: int = 18):
    left, right = dims
    assert np.array_equal(left[:2], right[:2])
    length = float(max(left)); lx, ly = left[:2]
    x, w = np.polynomial.legendre.leggauss(xy_order)
    u, wu = (x+1)/2, w/2
    z, wz = np.polynomial.legendre.leggauss(z_order)
    dx, dy, za, zb = np.meshgrid(u, u, z, z, indexing="ij")
    weights = (wu[:, None, None, None] * wu[None, :, None, None]
               * wz[None, None, :, None] * wz[None, None, None, :]).ravel()
    dx, dy, za, zb = [q.ravel() for q in (dx, dy, za, zb)]
    dz = centers[0, 2]-centers[1, 2] + left[2]*za/2 - right[2]*zb/2
    radius = np.sqrt((lx*dx)**2 + (ly*dy)**2 + dz**2) / length
    powers = np.vstack((weights/radius, *[weights*radius**p for p in range(8)]))
    answer = np.zeros((9, 3, 3)); cache = {}
    for i, a in enumerate(modes(left)):
        for j, b in enumerate(modes(right)):
            product = np.zeros(len(radius))
            for component in a.keys() & b.keys():
                sa, pa = a[component]; sb, pb = b[component]
                term = np.full(len(radius), sa*sb)
                for axis, offset in ((0, dx), (1, dy)):
                    assert parity(pa[axis]) == parity(pb[axis])
                    key = (axis, tuple(pa[axis]), tuple(pb[axis]))
                    if key not in cache:
                        cache[key] = correlation(offset, pa[axis], pb[axis])
                    term *= cache[key]
                term *= np.polynomial.polynomial.polyval(za, pa[2])
                term *= np.polynomial.polynomial.polyval(zb, pb[2])
                product += term
            answer[:, i, j] = powers @ product
    factor = lx**2*ly**2*left[2]*right[2]/4
    answer[0] *= factor/length
    answer[1:] *= factor
    return answer


def poly_exp(p: np.ndarray, alpha: np.ndarray, terms: int = 18):
    answer = np.zeros_like(alpha, dtype=complex)
    for n in range(terms):
        moment = sum(c*2/(degree+n+1) for degree, c in enumerate(p) if (degree+n) % 2 == 0)
        answer += moment * (-1j*alpha)**n / math.factorial(n)
    return answer


def direct_fourier(centers, dims, k, directions, reference):
    answer = np.zeros((len(directions), 3, 6), complex)
    for region, (center, dim) in enumerate(zip(centers, dims)):
        alpha = k*directions*dim/2
        phase = np.exp(-1j*k*(directions @ (center-reference)))
        for column, mm in enumerate(modes(dim)):
            for component, (scale, factors) in mm.items():
                value = np.full(len(directions), scale*np.prod(dim)/8, complex)
                for axis, p in enumerate(factors):
                    value *= poly_exp(p, alpha[:, axis])
                answer[:, component, 3*region+column] = value*phase
    return answer


def run():
    for path, expected in PINS.items():
        assert digest(path) == expected, path
    result = json.loads((OUT/"result.json").read_text(encoding="utf-8"))
    assert result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1"
    assert result["status"] == "PASS_SIX_COPPER_CURL_SPACE_AND_POLYNOMIAL_GREEN"
    assert result["script_sha256"] == PINS[PRODUCER] and result["space_sha256"] == PINS[OUT/"space.npz"]
    with np.load(OUT/"space.npz", allow_pickle=False) as z:
        saved = {key: z[key] for key in z.files}
    assert all(np.all(np.isfinite(value)) for value in saved.values())
    with np.load(ROOT/"outputs/research/astra-total-current-material-basis-01/basis.npz", allow_pickle=False) as z:
        tetrahedra, material = z["tetrahedra_local_m"], z["cell_material_id"]
    with np.load(ROOT/"outputs/research/astra-source-potential-terminal-02/fields.npz", allow_pickle=False) as z:
        old_h, old_mass = z["cell_integrated_current_map"], z["mass"]

    centers, dims = saved["centers_m"], saved["dimensions_m"]
    pm = np.zeros((6, 6)); first = np.zeros((3, 3, 6)); cell = np.zeros((18, 3, 6))
    boundary = divergence = mean = 0.0
    for region, material_id in enumerate((0, 2)):
        sl = slice(3*region, 3*region+3); center, dim = centers[region], dims[region]
        points, weights = tensor_rule(center, dim, 12); value = evaluate(points, center, dim)
        pm[sl, sl] = np.einsum("n,ndi,ndj->ij", weights, value, value)
        first[:, :, sl] = np.einsum("n,na,ndi->dai", weights, points-center, value)
        mean = max(mean, float(np.max(abs(np.einsum("n,ndi->di", weights, value)))))
        for axis in range(3):
            for sign in (-1, 1):
                face = points.copy(); face[:, axis] = center[axis] + sign*dim[axis]/2
                boundary = max(boundary, float(np.max(abs(evaluate(face, center, dim)[:, axis]))))
        divergence = max(divergence, float(np.max(abs(divergence_values(points, center, dim)))
                                           * max(dim)/np.max(abs(value))))
        for index in np.flatnonzero(material == material_id):
            p, w, _ = tetra_rule(tetrahedra[index], 10)
            cell[index, :, sl] = np.einsum("n,ndi->di", w, evaluate(p, center, dim))
    volumes = np.array([tetra_rule(t, 2)[2] for t in tetrahedra])
    cross = np.einsum("tdi,tdj,t->ij", old_h, cell, 1/volumes)
    mass = np.block([[old_mass, cross], [cross.T, pm]])
    mass_relative = float(np.linalg.norm(mass-saved["mass"])/np.linalg.norm(saved["mass"]))
    moment_relative = float(np.linalg.norm(cell-saved["new_cell_integrated_current_map"])
                            / np.linalg.norm(saved["new_cell_integrated_current_map"]))
    first_relative = float(np.linalg.norm(first-saved["first_current_moments"])
                           / np.linalg.norm(saved["first_current_moments"]))
    roots = np.sqrt(np.diag(mass)); normalized = mass/roots[:, None]/roots[None, :]
    schur = normalized[36:, 36:] - normalized[36:, :36] @ np.linalg.solve(normalized[:36, :36], normalized[:36, 36:])
    schur_error = float(np.max(abs(np.linalg.eigvalsh(schur)-result["new_six_schur_eigenvalues"])))

    self0 = self_green(dims[0]); self1 = self_green(dims[1]); cross_green = interlayer_green(centers, dims)
    green = np.concatenate((np.concatenate((self0, cross_green), axis=2),
                            np.concatenate((cross_green.transpose(0, 2, 1), self1), axis=2)), axis=1)
    saved_green = saved["polynomial_distance_powers"]
    green_scaled = float(np.max(abs(green-saved_green)
        / np.sqrt(abs(saved_green[0].diagonal()))[:, None]
        / np.sqrt(abs(saved_green[0].diagonal()))[None, :]))

    driver_path = OUT/"driver-at-run.py"
    sys.path.insert(0, str(ROOT/"tools/research"))
    spec = importlib.util.spec_from_file_location("frozen_six_curl", driver_path)
    module = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module)
    directions = np.array([[2., 3., 5.], [-4., 1., 2.], [1., -7., 3.]])
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    reference = centers.mean(axis=0)
    fourier_errors = []
    constants = module.source.old.source
    for k in (2*np.pi*100e6*np.sqrt(constants.MU0*constants.EPS0), 0.3/max(dims.ravel())):
        observed = module.fourier(centers, dims, k, directions, reference)
        expected = direct_fourier(centers, dims, k, directions, reference)
        fourier_errors.append(float(np.linalg.norm(observed-expected)/np.linalg.norm(expected)))

    metrics = dict(mass_relative=mass_relative, cell_moment_relative=moment_relative,
                   first_moment_relative=first_relative, schur_eigenvalue_absolute=schur_error,
                   green_diagonal_scaled=green_scaled, fourier_relative_max=max(fourier_errors),
                   boundary_normal_absolute=boundary, divergence_scaled=divergence,
                   mean_current_absolute=mean)
    assert mass_relative < 1e-12 and moment_relative < 1e-12 and first_relative < 1e-12
    assert schur_error < 1e-12 and green_scaled < 2e-10 and max(fourier_errors) < 2e-11
    assert boundary < 1e-12 and divergence < 1e-12 and mean < 1e-20
    REVIEW.mkdir()
    receipt = dict(program="SPD Decap PI Evaluator", version="0.23.1",
        status="ACCEPT_WITH_SCOPE_SIX_COPPER_CURL_SPACE_AND_GREEN",
        reviewer_sha256=digest(Path(__file__)),
        reviewed={str(path.relative_to(ROOT)).replace("\\", "/"): expected for path, expected in PINS.items()},
        metrics=metrics,
        producer_fourier_regression_recorded=False,
        scope=("Independent explicit-curl tensor/tetra mass, Schur, difference-box self/interlayer Green, "
               "and frozen-driver Fourier checks. The Fourier formula is accepted by this reviewer but was "
               "not executed or recorded by the producer. These six zero-flux redistribution currents do not "
               "establish basis convergence, a complete source solid, board impedance, or PowerSI accuracy."))
    with (REVIEW/"independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)


if __name__ == "__main__":
    run()
