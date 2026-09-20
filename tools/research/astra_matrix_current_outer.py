"""SPD Decap PI Evaluator v0.23.1: one matrix-valued RT0 outer partition.

All9 self-whitened cross entries share each tetra_inner scalar/moment batch.
The inherited finite-prism parameterization and tetra inner kernel are frozen.
"""
from time import monotonic
import heapq
import numpy as np
from measure_astra_l02_adaptive_nonself import AnisotropicOuter, tensor_rule
from qualify_astra_tetra_volume_green import tetra_inner


class MatrixWhiteOuter(AnisotropicOuter):
    def __init__(self, observer, source, observer_triangle, source_triangle,
                 observer_cholesky, source_cholesky, observer_signs, source_signs,
                 deadline, *, max_leaves=8192):
        self.ot, self.st = np.asarray(observer_triangle), np.asarray(source_triangle)
        self.c = np.linalg.solve(observer_cholesky, np.eye(3))*np.asarray(observer_signs)[None, :]
        self.d = np.linalg.solve(source_cholesky, np.eye(3))*np.asarray(source_signs)[None, :]
        self.od = abs(np.linalg.det(self.ot[1:]-self.ot[0]))*20e-6
        self.sd = abs(np.linalg.det(self.st[1:]-self.st[0]))*20e-6
        self.volume = sum(abs(np.linalg.det(t[1:]-t[0]))*(b[1]-b[0])/2 for t, b in observer)
        self.inner_batches = 0
        super().__init__(observer, source, deadline, max_leaves=max_leaves)

    def potential(self, points):
        assert monotonic() < self.deadline, 'Bounded matrix-valued current quadrature'
        self.points += len(points)
        scalar = np.zeros(len(points)); moment = np.zeros((len(points), 3))
        for source, _, _ in self.sources:
            s, m = tetra_inner(source, points)
            scalar += s; moment += m; self.inner_batches += 1
        r = points[:, :2]
        cs, ds = self.c.sum(axis=1), self.d.sum(axis=1)
        test = (r[:, None, :]*cs[None, :, None]-(self.c@(self.ot-self.anchor[:2]))[None])/self.od
        trial = ((r[:, None, :]*ds[None, :, None]-(self.d@(self.st-self.anchor[:2]))[None])*scalar[:, None, None]
                 +ds[None, :, None]*moment[:, None, :2])/self.sd
        value = 1e-7*self.volume*np.einsum('pid,pjd->pij', test, trial)
        assert np.isfinite(value).all()
        return value

    def _add_box(self, domain, lo, hi, depth):
        base, height, fraction = domain
        assert len(lo) == 3, 'Pilot owns triangular prisms only'
        rules = [(8, 8, 8)]+[tuple(4 if j == axis else 8 for j in range(3)) for axis in range(3)]
        blocks, block_weights = [], []
        for orders in rules:
            unit, weight = tensor_rule(orders)
            u, v, w = (lo+(hi-lo)*unit).T
            point = (1-v[:, None])*((1-u[:, None])*base[0]+u[:, None]*base[1])+v[:, None]*base[2]
            point[:, 2] += height*w
            blocks.append(point)
            block_weights.append(weight*2*(1-v)*fraction*np.prod(hi-lo))
        potential = self.potential(np.vstack(blocks))
        cursor, estimates = 0, []
        for weight in block_weights:
            estimates.append(np.einsum('p,pij->ij', weight, potential[cursor:cursor+len(weight)]))
            cursor += len(weight)
        directional = np.array([np.linalg.norm(value-estimates[0], 2) for value in estimates[1:]])
        error, axis = float(directional.sum()), int(directional.argmax())
        leaf = (domain, lo, hi, depth, estimates[0], error, axis)
        heapq.heappush(self.heap, (-error, self.next_id, leaf)); self.next_id += 1
        self.value += estimates[0]; self.error += error

    def refine_absolute(self, tolerance):
        """Aggregate directional spectral-norm indicator in self=I units."""
        while self.error > tolerance:
            assert monotonic() < self.deadline, 'Bounded matrix-valued current quadrature'
            assert len(self.heap) < self.max_leaves, 'Matrix outer leaf budget'
            _, _, (domain, lo, hi, depth, value, error, axis) = heapq.heappop(self.heap)
            self.value -= value; self.error = max(0., self.error-error)
            mid = (lo[axis]+hi[axis])/2
            next_depth = depth.copy(); next_depth[axis] += 1
            left_hi, right_lo = hi.copy(), lo.copy(); left_hi[axis] = mid; right_lo[axis] = mid
            self._add_box(domain, lo, left_hi, next_depth)
            self._add_box(domain, right_lo, hi, next_depth)
            self.splits += 1
        return self.value.real.copy()

    def receipt(self):
        return dict(integration_leaves=len(self.heap), parameter_box_bisections=self.splits,
                    evaluated_outer_points=self.points, tetra_inner_batches=self.inner_batches,
                    maximum_depth_per_axis=np.max([leaf[2][3] for leaf in self.heap], axis=0).tolist(),
                    aggregate_absolute_matrix_indicator=float(self.error),
                    indicator_scope='Sum of directional4/8-rule spectral-norm differences; a quadrature indicator, not a rigorous error bound.')


def pair_energy_matrix(w):
    return np.block([[np.eye(3), w], [w.T, np.eye(3)]])


def generalized_difference(left, right, metric):
    chol = np.linalg.cholesky(metric)
    delta = np.linalg.solve(chol, left-right)
    delta = np.linalg.solve(chol, delta.T).T
    return float(np.max(abs(np.linalg.eigvalsh((delta+delta.T)/2))))
