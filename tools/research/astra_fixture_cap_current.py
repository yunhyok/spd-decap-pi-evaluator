"""Finite-prism cap currents for the source-derived small port fixture.

Reuse the qualified analytic inner moments and matrix adaptive outer rule.
No projection of the anisotropic prism modes into tetrahedral RT0 is made.
"""
from time import monotonic

import numpy as np

from assemble_astra_l02_finite_charge_self import prism_union
from astra_matrix_current_outer import MatrixWhiteOuter
from measure_astra_l02_adaptive_nonself import AnisotropicOuter
from qualify_astra_tetra_volume_green import tetra_inner


class CapOuter(MatrixWhiteOuter):
    """Two cap-normal modes per prism, normalized to one outward ampere."""

    def __init__(self, observer_xy, source_xy, z_bounds, deadline, *, observer_cholesky=None, source_cholesky=None):
        observer_xy, source_xy, z_bounds = map(
            lambda x: np.asarray(x, dtype=float), (observer_xy, source_xy, z_bounds))
        assert observer_xy.shape == source_xy.shape == (3, 2)
        assert z_bounds.shape == (2,) and np.isfinite(z_bounds).all()
        height = z_bounds[1] - z_bounds[0]
        assert height > 0
        self.observer_area = abs(np.linalg.det(observer_xy[1:]-observer_xy[0]))/2
        self.source_area = abs(np.linalg.det(source_xy[1:]-source_xy[0]))/2
        assert self.observer_area > 0 and self.source_area > 0
        self.height = height
        self.z_bounds = z_bounds.copy()
        self.volume = self.observer_area*height
        self.inner_batches = 0
        assert (observer_cholesky is None) == (source_cholesky is None)
        self.left = None if observer_cholesky is None else np.linalg.solve(observer_cholesky,np.eye(2))
        self.right = None if source_cholesky is None else np.linalg.solve(source_cholesky,np.eye(2))
        sources, _ = prism_union([source_xy], z_bounds)
        AnisotropicOuter.__init__(self, [(observer_xy, z_bounds)], sources, deadline)

    def potential(self, points):
        assert monotonic() < self.deadline, 'Bounded fixture cap quadrature'
        self.points += len(points)
        scalar = np.zeros(len(points))
        z_moment = np.zeros(len(points))
        for source, _, _ in self.sources:
            s, m = tetra_inner(source, points)
            scalar += s
            z_moment += m[:, 2]
            self.inner_batches += 1
        # Bottom=(z-z1)/(A*h), top=(z-z0)/(A*h).
        endpoints = self.z_bounds[::-1]-self.anchor[2]
        z = points[:, 2, None]-endpoints
        test = z/(self.observer_area*self.height)
        trial = (z*scalar[:, None]+z_moment[:, None])/(self.source_area*self.height)
        value = 1e-7*self.volume*test[:, :, None]*trial[:, None, :]
        if self.left is not None:
            value = np.einsum('ai,pij,bj->pab',self.left,value,self.right)
        assert np.isfinite(value).all()
        return value


def self_check():
    """Independent constant-z-current identity, orientation, and translation."""
    from qualify_astra_tetra_volume_green import tetra_quadrature

    tri = np.array([[0., 0.], [1., 0.], [.2, .7]])*1e-4
    bounds = np.array([55., 75.])*1e-6
    first = CapOuter(tri, tri, bounds, monotonic()+30)
    value = first.refine_absolute(1e-5*np.linalg.norm(first.value, 2))
    source, _ = prism_union([tri], bounds)
    independent = 0.
    for observer in source:
        points, weights = tetra_quadrature(observer, 12)
        scalar = sum(tetra_inner(s-points[0], points-points[0])[0] for s in source)
        independent += weights@scalar
    # i_bottom=-1, i_top=+1 generates constant Jz=1/A.
    uniform = np.array([-1., 1.])
    expected = 1e-7*independent/first.observer_area**2
    error = abs(uniform@value@uniform-expected)/expected
    assert error < 2e-4, error
    shift = np.array([.01, -.02])
    second = CapOuter(tri+shift, tri+shift, bounds+.003, monotonic()+30)
    translated = second.refine_absolute(1e-5*np.linalg.norm(second.value, 2))
    assert np.allclose(value, translated, rtol=2e-8, atol=0)
    assert np.linalg.eigvalsh((value+value.T)/2).min() > 0
    assert value[0, 1] < 0 and value[1, 0] < 0
    print({'constant_current_relative_error': float(error), 'cap_current_check': 'PASS'}, flush=True)


if __name__ == '__main__':
    self_check()
