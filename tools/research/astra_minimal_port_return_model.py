"""SPD Decap PI Evaluator v0.23.1: finite two-conductor port reference.

This is a small canonical Galerkin problem for separating same-matrix numerical
error from spatial and quadrature sensitivity.  It is not a board model.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools" / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools" / "research"))

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
EPS0 = 8.8541878128e-12
SIGMA_CU = 58.0e6
EPS_R = 4.0

MM = 1.0e-3
UM = 1.0e-6
X_PATCH = 2.0 * MM
Y_PATCH = 1.0 * MM
PWR_Z = (0.0, 40.0 * UM)
RETURN_Z = (-140.0 * UM, -100.0 * UM)
PAD_Z = (300.0 * UM, 340.0 * UM)
LOAD_R_OHM = 10.0e-3
LOAD_L_H = 1.0e-9
LOAD_C_F = 1.0e-6

ELECTRODES = ("source_pwr", "source_return", "load_pwr", "load_return")


def _axis(parts: tuple[float, ...], refinement: int) -> np.ndarray:
    values: list[float] = []
    for left, right in zip(parts, parts[1:]):
        segment = np.linspace(left, right, refinement + 1)
        values.extend(segment[:-1])
    values.append(parts[-1])
    return np.asarray(values, dtype=float)


def _positive_tetra(points: np.ndarray) -> np.ndarray:
    result = np.asarray(points, dtype=float).copy()
    if np.linalg.det((result[1:] - result[0]).T) < 0.0:
        result[[1, 2]] = result[[2, 1]]
    assert np.linalg.det((result[1:] - result[0]).T) > 0.0
    return result


def _block_tetrahedra(
    xparts: tuple[float, ...],
    yparts: tuple[float, ...],
    zparts: tuple[float, ...],
    refinement: int,
    x_only: bool = False,
) -> list[np.ndarray]:
    x, y, z = (_axis(parts, refinement if axis == 0 or (axis == 1 and not x_only) else 1)
               for axis, parts in enumerate((xparts, yparts, zparts)))
    result: list[np.ndarray] = []
    patterns = (
        (0, 1, 3, 7), (0, 3, 2, 7), (0, 2, 6, 7),
        (0, 6, 4, 7), (0, 4, 5, 7), (0, 5, 1, 7),
    )
    for ix in range(len(x) - 1):
        for iy in range(len(y) - 1):
            for iz in range(len(z) - 1):
                vertices = np.array([
                    [x[ix + dx], y[iy + dy], z[iz + dz]]
                    for dz in (0, 1) for dy in (0, 1) for dx in (0, 1)
                ])
                # The bit order above is x-fast: 000,100,010,110,001,...
                result.extend(_positive_tetra(vertices[list(pattern)]) for pattern in patterns)
    return result


def _geometry(level: int, x_only: bool = False) -> tuple[np.ndarray, np.ndarray]:
    if int(level) not in (1, 2):
        raise ValueError("n is the canonical mesh level and must be 1 or 2")
    r = int(level)
    if x_only and r != 2:
        raise ValueError('x_only is the fixed x2/y1 intermediate spatial comparison')
    y = (0.0, Y_PATCH)
    blocks = (
        ("pwr", (0.0, .5*MM, 1.0*MM, 1.5*MM, 2.0*MM), y, PWR_Z),
        ("pwr", (0.0, .5*MM), y, (PWR_Z[1], PAD_Z[0])),
        ("pwr", (0.0, .5*MM, 1.0*MM), y, PAD_Z),
        ("return", (0.0, .5*MM, 1.0*MM, 1.5*MM, 2.0*MM), y, RETURN_Z),
        ("return", (2.0*MM, 2.1*MM, 2.5*MM), y, RETURN_Z),
        ("return", (2.1*MM, 2.5*MM), y, (RETURN_Z[1], PAD_Z[0])),
        ("return", (1.5*MM, 2.1*MM, 2.5*MM), y, PAD_Z),
    )
    cells: list[np.ndarray] = []
    conductor: list[int] = []
    for name, xs, ys, zs in blocks:
        local = _block_tetrahedra(xs, ys, zs, r, x_only=x_only)
        cells.extend(local)
        conductor.extend([0 if name == "pwr" else 1] * len(local))
    tetrahedra = np.asarray(cells, dtype=float)
    labels = np.asarray(conductor, dtype=np.int8)
    keys = [tuple(np.round(tetra.reshape(-1), 15)) for tetra in tetrahedra]
    if len(keys) != len(set(keys)):
        raise AssertionError("overlapping canonical cuboid cells")
    return tetrahedra, labels


def _face(tetra: np.ndarray, opposite: int) -> np.ndarray:
    return np.delete(tetra, opposite, axis=0)


def _face_key(triangle: np.ndarray) -> tuple[tuple[float, ...], ...]:
    return tuple(sorted(tuple(np.round(point, 15)) for point in triangle))


def _electrode_name(triangle: np.ndarray, conductor: int) -> str | None:
    x, y, z = triangle.T
    atol = 1.0e-14
    if np.allclose(z, PAD_Z[1], rtol=0.0, atol=atol):
        if conductor == 0 and x.min() >= -atol and x.max() <= 1.0*MM + atol:
            return "source_pwr"
        if conductor == 1 and x.min() >= 1.5*MM - atol and x.max() <= 2.5*MM + atol:
            return "source_return"
    if np.allclose(y, Y_PATCH, rtol=0.0, atol=atol):
        in_x = x.min() >= 1.0*MM - atol and x.max() <= 1.5*MM + atol
        if in_x and conductor == 0 and z.min() >= PWR_Z[0]-atol and z.max() <= PWR_Z[1]+atol:
            return "load_pwr"
        if in_x and conductor == 1 and z.min() >= RETURN_Z[0]-atol and z.max() <= RETURN_Z[1]+atol:
            return "load_return"
    return None


def _topology(tetrahedra: np.ndarray, conductor: np.ndarray) -> dict:
    owners: dict[tuple, list[tuple[int, int, np.ndarray]]] = {}
    for cell, tetra in enumerate(tetrahedra):
        for local in range(4):
            triangle = _face(tetra, local)
            owners.setdefault(_face_key(triangle), []).append((cell, local, triangle))
    if any(len(value) not in (1, 2) for value in owners.values()):
        raise AssertionError("non-manifold tetrahedral face")

    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    boundary: list[tuple[int, int, int, np.ndarray]] = []
    for global_face, face_owners in enumerate(owners.values()):
        for index, (cell, local, triangle) in enumerate(face_owners):
            rows.append(4*cell + local)
            cols.append(global_face)
            data.append(1.0 if index == 0 else -1.0)
        if len(face_owners) == 1:
            cell, local, triangle = face_owners[0]
            boundary.append((global_face, cell, local, triangle))
    local_to_global = sparse.coo_matrix(
        (data, (rows, cols)), shape=(4*len(tetrahedra), len(owners))
    ).tocsr()

    electrode_rows = {name: [] for name in ELECTRODES}
    free_faces: list[np.ndarray] = []
    free_global: list[int] = []
    free_conductor: list[int] = []
    for global_face, cell, _local, triangle in boundary:
        name = _electrode_name(triangle, int(conductor[cell]))
        if name is None:
            free_faces.append(triangle)
            free_global.append(global_face)
            free_conductor.append(int(conductor[cell]))
        else:
            electrode_rows[name].append(global_face)
    if any(not electrode_rows[name] for name in ELECTRODES):
        raise AssertionError("every finite electrode must own exterior faces")

    volume_divergence = sparse.kron(
        sparse.eye(len(tetrahedra), format="csr"), np.ones((1, 4)), format="csr"
    ) @ local_to_global
    surface_divergence = sparse.coo_matrix(
        (-np.ones(len(free_global)), (np.arange(len(free_global)), free_global)),
        shape=(len(free_global), local_to_global.shape[1]),
    ).tocsr()
    divergence = sparse.vstack((volume_divergence, surface_divergence), format="csr")
    h = sparse.coo_matrix(
        (
            np.ones(sum(len(electrode_rows[name]) for name in ELECTRODES)),
            (
                np.concatenate([
                    np.full(len(electrode_rows[name]), index, dtype=np.int64)
                    for index, name in enumerate(ELECTRODES)
                ]),
                np.concatenate([np.asarray(electrode_rows[name], dtype=np.int64) for name in ELECTRODES]),
            ),
        ),
        shape=(len(ELECTRODES), local_to_global.shape[1]),
    ).tocsr()
    balance = np.asarray(divergence.sum(axis=0) - h.sum(axis=0)).ravel()
    if np.linalg.norm(balance) != 0.0:
        raise AssertionError("charge/electrode ownership identity")
    return {
        "local_to_global": local_to_global,
        "divergence": divergence,
        "electrode": h,
        "free_faces": np.asarray(free_faces, dtype=float),
        "free_conductor": np.asarray(free_conductor, dtype=np.int8),
        "electrode_face_counts": {name: len(electrode_rows[name]) for name in ELECTRODES},
    }


def _pair(value: complex) -> list[float]:
    return [float(np.real(value)), float(np.imag(value))]


def assemble(n: int, frequency_hz: float, quadrature_order: int = 8, *, x_only: bool = False) -> dict:
    """Assemble the canonical matrix and one-ampere differential source."""
    frequency = float(frequency_hz)
    if not np.isfinite(frequency) or frequency <= 0.0:
        raise ValueError("frequency_hz must be finite and positive")
    if int(quadrature_order) < 2:
        raise ValueError("quadrature_order must be >=2")
    tetrahedra, conductor = _geometry(int(n), x_only=x_only)
    topology = _topology(tetrahedra, conductor)
    c = topology["local_to_global"]
    d = topology["divergence"]
    h = topology["electrode"]
    entities = list(tetrahedra) + list(topology["free_faces"])

    import astra_minimal_port_kernels as kernels
    fields = kernels.assemble_fields(
        tetrahedra, c, charge_entities=entities, order=int(quadrature_order)
    )
    rgeom = sparse.csc_matrix(fields["mass"])
    lmatrix = np.asarray(fields["inductance"], dtype=float)
    p_raw = np.asarray(fields["potential_raw_per_m"], dtype=float)
    ni, nq, nu = c.shape[1], d.shape[0], len(ELECTRODES)
    if rgeom.shape != (ni, ni) or lmatrix.shape != (ni, ni) or p_raw.shape != (nq, nq):
        raise AssertionError("field kernel dimensions disagree with topology")
    if not (np.isfinite(rgeom.data).all() and np.isfinite(lmatrix).all() and np.isfinite(p_raw).all()):
        raise AssertionError("field matrices must be finite")
    omega = 2.0*np.pi*frequency
    zcurrent = rgeom/SIGMA_CU + sparse.csc_matrix(1j*omega*lmatrix)
    potential = p_raw/(4.0*np.pi*EPS0*EPS_R)

    load_z = LOAD_R_OHM + 1j*omega*LOAD_L_H + 1.0/(1j*omega*LOAD_C_F)
    yload = np.zeros((nu, nu), dtype=complex)
    stamp = np.array([[1.0, -1.0], [-1.0, 1.0]], dtype=complex)/load_z
    yload[np.ix_((2, 3), (2, 3))] = stamp
    a = sparse.bmat((
        (zcurrent, -d.T @ sparse.csc_matrix(potential), h.T),
        (d, 1j*omega*sparse.eye(nq, format="csc"), None),
        (-h, None, sparse.csc_matrix(yload)),
    ), format="csc")
    b = np.zeros(ni+nq+nu, dtype=complex)
    source = np.array([1.0, -1.0, 0.0, 0.0])
    b[ni+nq:] = source
    port = b.copy()

    closed = sparse.vstack((d, h), format="csr")
    cycle_dimension = int(ni - np.linalg.matrix_rank(closed.toarray(), tol=1.0e-11))
    if cycle_dimension <= 0:
        raise AssertionError("canonical current space must retain circulation")
    l_symmetric = (lmatrix+lmatrix.T)/2.0
    l_eigenvalues = np.linalg.eigvalsh(l_symmetric)
    p_eigenvalues = np.linalg.eigvalsh((potential+potential.T)/2.0)
    l_scale = max(float(np.max(np.abs(l_eigenvalues))), np.finfo(float).tiny)
    p_scale = max(float(np.max(np.abs(p_eigenvalues))), np.finfo(float).tiny)
    l_symmetry = float(np.linalg.norm(lmatrix-lmatrix.T)/max(np.linalg.norm(lmatrix), np.finfo(float).tiny))
    p_symmetry = float(np.linalg.norm(potential-potential.T)/max(np.linalg.norm(potential), np.finfo(float).tiny))

    def recover(x: np.ndarray) -> dict:
        x = np.asarray(x, dtype=complex)
        current, charge, electrode_v = x[:ni], x[ni:ni+nq], x[ni+nq:]
        residual = b-a@x
        current_row = residual[:ni]
        continuity = residual[ni:ni+nq]
        electrode_kcl = residual[ni+nq:]
        charge_total = complex(np.sum(charge))
        pwr_mask = np.r_[conductor == 0, topology["free_conductor"] == 0]
        pwr_charge = complex(np.sum(charge[pwr_mask]))
        return_charge = complex(np.sum(charge[~pwr_mask]))
        source_power = complex(source @ electrode_v)
        conductor_power = complex(np.vdot(current, zcurrent @ current))
        scalar_power = complex(-1j*omega*np.vdot(charge, potential @ charge))
        load_power = complex(np.dot(electrode_v, np.conj(yload @ electrode_v)))
        power_defect = source_power-(conductor_power+scalar_power+load_power)
        power_scale = max(abs(source_power), abs(conductor_power)+abs(scalar_power)+abs(load_power),
                          np.finfo(float).tiny)
        row_scale = np.asarray(abs(a)@abs(x)+abs(b), dtype=float)
        row_scale = np.maximum(row_scale, np.finfo(float).tiny)
        global_backward = float(np.max(abs(residual)/row_scale))
        current_backward = float(np.max(abs(current_row)/row_scale[:ni]))
        continuity_backward = float(np.max(abs(continuity)/row_scale[ni:ni+nq]))
        electrode_backward = float(np.max(abs(electrode_kcl)/row_scale[ni+nq:]))
        metrics = {
            "componentwise_backward_error": global_backward,
            "current_constitutive_backward_error": current_backward,
            "charge_continuity_backward_error": continuity_backward,
            "electrode_kcl_backward_error": electrode_backward,
            "current_constitutive_residual_norm_v": float(np.linalg.norm(current_row)),
            "charge_continuity_residual_norm_a": float(np.linalg.norm(continuity)),
            "electrode_kcl_residual_norm_a": float(np.linalg.norm(electrode_kcl)),
            "total_charge_c": _pair(charge_total),
            "pwr_volume_charge_c": _pair(pwr_charge),
            "return_total_charge_c": _pair(return_charge),
            "source_outward_currents_a": [_pair(value) for value in (h@current)[:2]],
            "load_outward_currents_a": [_pair(value) for value in (h@current)[2:]],
            "electrode_voltage_v": [_pair(value) for value in electrode_v],
            "load_current_a": _pair((electrode_v[2]-electrode_v[3])/load_z),
            "source_complex_power_va": _pair(source_power),
            "conductor_complex_power_va": _pair(conductor_power),
            "scalar_complex_power_va": _pair(scalar_power),
            "load_complex_power_va": _pair(load_power),
            "complex_power_relative_defect": float(abs(power_defect)/power_scale),
        }
        gates = {
            "finite": bool(np.isfinite(x).all() and np.isfinite(residual).all()),
            "global_equations": bool(global_backward <= 1.0e-9),
            "constitutive": bool(current_backward <= 1.0e-9),
            "charge_continuity": bool(continuity_backward <= 1.0e-9),
            "electrode_kcl": bool(electrode_backward <= 1.0e-9),
            "total_charge_neutrality": bool(abs(charge_total) <= 1.0e-9*max(np.linalg.norm(charge), 1.0e-30)),
            "circulation_retained": bool(cycle_dimension > 0),
            "magnetic_symmetry": bool(l_symmetry <= 2.0e-8),
            "magnetic_psd_screen": bool(float(l_eigenvalues.min()) >= -2.0e-8*l_scale),
            "scalar_symmetry": bool(p_symmetry <= 2.0e-8),
            "scalar_positive_screen": bool(float(p_eigenvalues.min()) >= -2.0e-8*p_scale),
            "nonnegative_real_dissipation": bool(conductor_power.real >= -1.0e-12*power_scale
                                                   and load_power.real >= -1.0e-12*power_scale),
            "complex_power_identity": bool(abs(power_defect) <= 2.0e-8*power_scale),
        }
        return {"metrics": metrics, "gates": gates}

    physical_contract = {
        "model": "aligned-cuboid-two-conductor-rt0-charge-electrode-mna-v1",
        "dimensions_m": {
            "patch": [X_PATCH, Y_PATCH, PWR_Z[1]-PWR_Z[0]],
            "dielectric_gap": RETURN_Z[1] * -1.0,
            "pwr_post": [.5*MM, Y_PATCH, PAD_Z[0]-PWR_Z[1]],
            "return_post": [.4*MM, Y_PATCH, PAD_Z[0]-RETURN_Z[1]],
            "pwr_pad": [1.0*MM, Y_PATCH, PAD_Z[1]-PAD_Z[0]],
            "return_pad": [1.0*MM, Y_PATCH, PAD_Z[1]-PAD_Z[0]],
        },
        "materials": {"conductor_sigma_s_per_m": SIGMA_CU,
                      "uniform_background_relative_permittivity": EPS_R,
                      "conductor_relative_permittivity": EPS_R},
        "electrodes": list(ELECTRODES),
        "canonical_load": {"kind": "series_RLC_between_load_electrodes",
                           "resistance_ohm": LOAD_R_OHM,
                           "inductance_h": LOAD_L_H,
                           "capacitance_f": LOAD_C_F,
                           "fitted": False},
        "source": {"current_a": 1.0, "incidence": [1.0, -1.0, 0.0, 0.0]},
    }
    metadata = {
        "program": PROGRAM,
        "version": VERSION,
        "physical_contract": physical_contract,
        "discretization": {
            "mesh_level": int(n), "tetrahedra": len(tetrahedra),
            "axis_refinement": [int(n), 1 if x_only else int(n), 1],
            "mesh_variant": "x2-y1-intermediate" if x_only else "xy-uniform",
            "global_rt0_currents": ni, "charge_entities": nq,
            "volume_charge_entities": len(tetrahedra),
            "free_surface_charge_entities": len(topology["free_faces"]),
            "electrode_face_counts": topology["electrode_face_counts"],
            "closed_current_cycle_dimension": cycle_dimension,
            "quadrature_order": int(quadrature_order),
        },
        "ownership": {
            "current_resistance": "volume RT0 geometric mass divided by copper conductivity",
            "magnetic": "all-row volume RT0 Green matrix from astra_minimal_port_kernels",
            "scalar": "all volume and non-electrode free-surface charge Green matrix in uniform epsr=4",
            "contacts": "conforming shared tetra faces; no duplicate contact impedance",
            "electrodes": "H outward flux plus four finite equipotential reaction voltages",
            "load": "declared series RLC only; geometric connection field remains in volume kernels",
        },
        "assumptions_and_limits": [
            "Canonical aligned cuboids with rectangular posts and pads; no circular barrel or antipad.",
            "Uniform dielectric background and conductor epsilon equal background, so kappa=sigma.",
            "Finite source/load electrode planes exclude those faces from free-charge ownership.",
            "No external source-lead geometry; source voltage is measured at the pad electrodes.",
            "Two conductors are jointly neutral, not separately constrained to zero net charge.",
            "This does not represent the production board stackup or qualify PowerSI accuracy.",
        ],
        "matrix": {"unknown_order": ["global_rt0_current_a", "integrated_charge_c", "electrode_voltage_v"],
                   "shape": list(a.shape), "nnz": int(a.nnz)},
        "magnetic_diagnostics": {"ordinary_symmetry_relative": l_symmetry,
                                 "minimum_symmetric_eigenvalue_h": float(l_eigenvalues.min()),
                                 "maximum_symmetric_eigenvalue_h": float(l_eigenvalues.max())},
        "scalar_diagnostics": {"ordinary_symmetry_relative": p_symmetry,
                               "minimum_symmetric_eigenvalue_v_per_c": float(p_eigenvalues.min()),
                               "maximum_symmetric_eigenvalue_v_per_c": float(p_eigenvalues.max())},
        "kernel_diagnostics": fields["diagnostics"],
    }
    return {"A": a, "b": b, "port": port, "metadata": metadata, "recover": recover}


def self_check() -> None:
    intermediate, labels = _geometry(2, x_only=True)
    assert len(intermediate) == 192
    assert _topology(intermediate, labels)['local_to_global'].shape[0] == 4*192
    for level in (1, 2):
        tetrahedra, conductor = _geometry(level)
        topology = _topology(tetrahedra, conductor)
        assert topology["local_to_global"].shape[0] == 4*len(tetrahedra)
        assert all(value > 0 for value in topology["electrode_face_counts"].values())
    case = assemble(1, 10.0e6, quadrature_order=4)
    a, b = case["A"], case["b"]
    x = spsolve(a, b)
    assert np.linalg.norm(a@x-b) <= 1.0e-9*np.linalg.norm(b)
    assert all(case["recover"](x)["gates"].values())
    assert case["port"].shape == b.shape


if __name__ == "__main__":
    self_check()
    print(f"{PROGRAM} v{VERSION}: minimal port-return model self-check PASS")
