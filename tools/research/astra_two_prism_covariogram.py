"""SPD Decap PI Evaluator v0.23.1: bounded two-prism current covariogram.

No geometry/basis modification. Both prisms have the same fixed depth interval.
The remaining 2D displacement integral uses positive Gaussian rules and exact
polygon overlap moments (up to the floating-point geometry implementation).
"""
from time import monotonic
import numpy as np
import shapely
from scipy.special import roots_legendre
from astra_prism_covariogram_self import depth_direct


def cross(a, b):
    return a[..., 0]*b[..., 1]-a[..., 1]*b[..., 0]


def overlap_moments(a, b, displacement):
    """Area, centroid and central trace covariance of A intersect (B-u)."""
    # Condition the polygon operation in the observer's affine coordinates.
    # Moment arithmetic is about a polygon vertex, then about its centroid;
    # subtracting global raw second moments is unsafe for tiny boundary slivers.
    edges = a[1:]-a[0]
    inverse = np.linalg.inv(edges)
    local_b = (b[None]-displacement[:, None]-a[0])@inverse
    overlaps = shapely.intersection(shapely.Polygon([[0.,0.],[1.,0.],[0.,1.]]), shapely.polygons(local_b))
    rings = shapely.get_exterior_ring(overlaps)
    coords, owner = shapely.get_coordinates(rings, return_index=True)
    first_index = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
    assert np.array_equal(owner[first_index], np.arange(len(displacement))), 'Numerically empty overlap'
    origin = coords[first_index]
    same = owner[:-1] == owner[1:]
    left, right, group = coords[:-1][same], coords[1:][same], owner[:-1][same]
    left = left-origin[group]; right = right-origin[group]
    fan_area = abs(cross(left, right))/2
    area = np.bincount(group, weights=fan_area, minlength=len(displacement))
    assert np.all(area > 0), 'Numerically zero-area overlap'
    centroid = np.column_stack([np.bincount(group,weights=fan_area*(left[:,d]+right[:,d])/3,minlength=len(area))/area for d in range(2)])
    zero = -centroid[group]@edges
    left = (left-centroid[group])@edges; right = (right-centroid[group])@edges
    trace_integral = fan_area*(np.sum(zero*zero+left*left+right*right+zero*left+zero*right+left*right,axis=1))/6
    central_trace = np.bincount(group, weights=trace_integral, minlength=len(area))/area
    return area*abs(np.linalg.det(edges)), (origin+centroid)@edges+a[0], central_trace


def event_lines(a, b):
    """Overlap topology changes only on vertex/translated-edge incidences."""
    normals, constants = [], []
    for fixed, moving in ((a, b), (b, a)):
        sign = 1 if fixed is a else -1
        for p in fixed:
            for j in range(3):
                edge = moving[(j+1) % 3]-moving[j]
                normal = np.array([-edge[1], edge[0]])
                normal /= np.linalg.norm(normal)
                normals.append(normal)
                constants.append(sign*float(normal@(moving[j]-p)))
    return np.array(normals), np.array(constants)


def two_prism_whitened(a, b, height, la, lb, signs_a, signs_b, order,
                       deadline, *, radial_events=False, max_points=1_500_000):
    started = monotonic()
    anchor = np.asarray(a)[0].copy()
    a, b = np.asarray(a)-anchor, np.asarray(b)-anchor
    aa, ab = abs(cross(a[1]-a[0], a[2]-a[0]))/2, abs(cross(b[1]-b[0], b[2]-b[0]))/2
    assert min(aa, ab, height) > 0
    ca = np.linalg.solve(la, np.eye(3))*np.asarray(signs_a)[None]
    cb = np.linalg.solve(lb, np.eye(3))*np.asarray(signs_b)[None]
    sa, sb = ca.sum(axis=1), cb.sum(axis=1)
    ca_vertices, cb_vertices = ca@a, cb@b
    hull = shapely.convex_hull(shapely.MultiPoint((b[:, None]-a[None]).reshape(-1, 2)))
    vertices = np.asarray(hull.exterior.coords)[:-1]
    if cross(vertices, np.roll(vertices, -1, axis=0)).sum() < 0:
        vertices = vertices[::-1]
    # This bounded pilot owns intersecting/touching triangle pairs only.
    assert hull.distance(shapely.Point(0., 0.)) < 1e-14
    nodes, wg = roots_legendre(order)
    t, wt = (nodes+1)/2, wg/2
    normals, constants = event_lines(a, b)
    arrangement_vertices = []
    if radial_events:
        for i in range(len(normals)):
            for j in range(i):
                mat = normals[[i, j]]
                if abs(np.linalg.det(mat)) > 1e-12:
                    arrangement_vertices.append(np.linalg.solve(mat, constants[[i, j]]))
    arrangement_vertices = np.asarray(arrangement_vertices).reshape(-1, 2)
    w = np.zeros((3, 3)); mass = 0.; constant_matrix = np.zeros((3, 3))
    count, panels = 0, 0
    for left, right in zip(vertices, np.roll(vertices, -1, axis=0)):
        factor = cross(left, right)
        if factor == 0:
            continue
        assert factor > 0, 'Origin must lie in the displacement hull'
        edge = right-left
        closest = float(np.clip(-left@edge/(edge@edge), 0., 1.))
        cuts = [0., closest, 1.]
        if radial_events:
            coeff = arrangement_vertices@np.linalg.inv(np.stack([left, edge]))
            r = coeff[:, 0]
            valid = (r > 1e-12) & (r <= 1+1e-10)
            s = coeff[valid, 1]/r[valid]
            cuts.extend(s[(s > 0) & (s < 1)].tolist())
            divisor = normals@edge
            valid = abs(divisor) > 1e-30
            s = (constants[valid]-normals[valid]@left)/divisor[valid]
            cuts.extend(s[(s > 0) & (s < 1)].tolist())
        cuts = np.sort(np.array(cuts))
        cuts = cuts[(cuts > 1e-11) & (cuts < 1-1e-11)]
        cuts = np.r_[0., cuts[np.r_[True, np.diff(cuts) > 1e-11]], 1.] if len(cuts) else np.array([0.,1.])
        for lo, hi in zip(cuts[:-1], cuts[1:]):
            assert monotonic() < deadline, 'Covariogram overall numeric deadline'
            angular = lo+(hi-lo)*t; angular_weight = (hi-lo)*wt
            # Hyperbolic angular rule resolves the closest direction of long fans.
            scale = np.linalg.norm(left+closest*edge)/np.linalg.norm(edge)
            if scale > 0 and (lo == closest or hi == closest) and (hi-lo)/scale > 4:
                extent = np.arcsinh((hi-lo)/scale)
                direction = 1 if lo == closest else -1
                angular = closest+direction*scale*np.sinh(t*extent)
                angular_weight = wt*scale*extent*np.cosh(t*extent)
            boundary = left+angular[:, None]*edge
            if radial_events:
                divisor = boundary@normals.T
                with np.errstate(divide='ignore', invalid='ignore'):
                    crossing = constants[None]/divisor
                crossing = np.where(np.isfinite(crossing), np.clip(crossing, 0., 1.), 0.)
                radial_cuts = np.sort(np.column_stack([np.zeros(len(t)), crossing, np.ones(len(t))]), axis=1)
                # Coalesce only partition boundaries. The whole radial interval
                # [0,1] remains covered; no displacement interaction is removed.
                for j in range(1, radial_cuts.shape[1]-1):
                    close = radial_cuts[:,j]-radial_cuts[:,j-1] < 1e-12
                    radial_cuts[close,j] = radial_cuts[close,j-1]
                radial_cuts[radial_cuts > 1-1e-12] = 1.
            else:
                radial_cuts = np.tile([0., 1.], (len(t), 1))
            rlo, span = radial_cuts[:, :-1], np.diff(radial_cuts, axis=1)
            radial = rlo[:, :, None]+span[:, :, None]*t[None, None]**2
            weight = (factor*angular_weight[:, None, None]*span[:, :, None]*2*t[None, None]*wt[None, None]*radial)
            displacement = boundary[:, None, None]*radial[:, :, :, None]
            valid = weight.ravel() > 0
            displacement, weight = displacement.reshape(-1, 2)[valid], weight.ravel()[valid]
            count += len(weight); panels += 1
            assert count <= max_points, 'Covariogram explicit point budget'
            for start in range(0, len(weight), 8192):
                assert monotonic() < deadline, 'Covariogram overall numeric deadline'
                u, measure = displacement[start:start+8192], weight[start:start+8192]
                area, mean, trace = overlap_moments(a, b, u)
                test = mean[:, None]*sa[None, :, None]-ca_vertices[None]
                trial = (mean+u)[:, None]*sb[None, :, None]-cb_vertices[None]
                moment = np.einsum('pid,pjd->pij', test, trial)+trace[:, None, None]*sa[None, :, None]*sb[None, None]
                normalized = measure*area/(aa*ab)
                mass += normalized.sum()
                constant_matrix += 1e-7/4*np.einsum('p,pij->ij', normalized, moment)
                w += 1e-7/4*np.einsum('p,pij->ij', normalized*depth_direct(np.linalg.norm(u, axis=1), height), moment)
    expected_constant = 1e-7/4*((a.mean(axis=0)*sa[:, None]-ca_vertices)@(b.mean(axis=0)*sb[:, None]-cb_vertices).T)
    constant_error = float(np.linalg.norm(constant_matrix-expected_constant)/max(np.linalg.norm(expected_constant), 1e-300))
    return w, dict(order=order, radial_events=radial_events, points=count, angular_panels=panels,
        normalized_overlap_mass=float(mass), normalization_error=abs(float(mass)-1),
        kernel_one_matrix_relative_error=constant_error, elapsed_s=monotonic()-started,
        partition_coalescing_radial=1e-12, partition_coalescing_angular=1e-11,
        partition_coverage='All fan angular [0,1] and radial [0,1] intervals retained; coalesced cuts change only the quadrature partition.')
