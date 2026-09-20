"""SPD Decap PI Evaluator v0.23.1: new source-material TM joint-boundary control.

Exp(+j omega t + j kx x), prescribed H at the outer copper faces. This is a
Maxwell boundary oracle, not an implemented general volume-current PEEC solver.
Run once per output directory; the production solver and old controls are unused.
"""
from pathlib import Path
from time import monotonic
import argparse
import hashlib
import json
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.linalg import solve, solve_banded

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * np.pi
FREQUENCIES = (1e3, 1e4, 1e5, 1e6, 1e7, 1e8)
PERIODS = (1e-3, 1e-4)  # Controlled Fourier periods, not board ports or mesh policy.
MESHES = (16, 32, 64, 128)
PINS = {
    "source": ("astra-native-frequency-stamps-01/source-frequency-inputs.json", "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc"),
    "low": ("astra-native-low-band-stamps-01/receipt.json", "1968b9091d7eab184c2c0ad195fdc847661377484624a4ac4985d06e42216299"),
    "high": ("astra-native-frequency-stamps-01/receipt.json", "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856"),
    "low_review": ("astra-native-low-band-stamps-01/independent-review.json", "1fed75a2227ffb90f937914ebf2b112614736b632b9fec053ac9876125168228"),
    "high_review": ("astra-native-frequency-stamps-01/independent-review.json", "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(a, b):
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), np.finfo(float).tiny))


def source_inputs():
    data = {}
    for name, (path, expected) in PINS.items():
        assert sha(R / path) == expected, path
        data[name] = json.loads((R / path).read_bytes())
    rows = data["source"]["stackup_layers"][:3]
    assert [row["name"] for row in rows] == ["Signal$TOP", "Medium$DR0102", "Signal$L02(DGND)"]
    assert [row["thickness_um"] for row in rows] == [25.0, 30.0, 20.0]
    assert rows[0]["conductivity_s_m"] == rows[2]["conductivity_s_m"] == 59590000.0
    properties = {}
    for name in ("low", "high"):
        receipt = data[name]
        gap = receipt["gap_properties"][0]
        assert gap["upper_layer"] == rows[0]["name"] and gap["lower_layer"] == rows[2]["name"]
        for f, dk, df in zip(receipt["frequencies_hz"], gap["dk"], gap["df"], strict=True):
            value = (dk, df)
            if f in properties:
                assert np.allclose(properties[f], value, rtol=1e-14, atol=0)
            properties[f] = value
    return rows, properties


def materials(rows, properties, f):
    eps = []
    sigma = []
    for index, row in enumerate(rows):
        dk, df = properties[f] if index == 1 else (row["dk"], row["df"])
        eps.append(EPS0 * dk * (1 - 1j * df))
        sigma.append(float(row["conductivity_s_m"] or 0))
    eps, sigma = np.array(eps), np.array(sigma)
    return np.array([row["thickness_um"] * 1e-6 for row in rows]), eps, sigma, sigma + 2j*np.pi*f*eps


def analytic(thickness, gamma, omega, kx):
    """Independent exact local endpoint modes; never multiply ill-scaled global transfers."""
    q = np.sqrt(kx*kx + 1j*omega*MU0*gamma)
    assert np.all(q.real >= 0)
    diag = q/gamma/np.tanh(q*thickness)
    off = -q/gamma/np.sinh(q*thickness)
    matrix = np.zeros((4, 4), complex)
    for layer in range(3):
        matrix[layer:layer+2, layer:layer+2] += np.array([[diag[layer], off[layer]], [off[layer], diag[layer]]])
    h = np.zeros((4, 2), complex)
    h[0, 0], h[-1, 1] = 1, -1
    condition, backward = solve_interfaces(matrix, h)
    reaction = matrix @ h
    z = np.vstack((reaction[0], -reaction[-1]))
    return q, h, z, condition, backward


def solve_interfaces(matrix, h):
    """Solve the two interior interfaces, report residual in original equations."""
    rhs = -matrix[1:3][:, [0, 3]] @ h[[0, 3]]
    scale = np.max(np.abs(matrix[1:3, 1:3]), axis=1)
    scaled = matrix[1:3, 1:3]/scale[:, None]
    h[1:3] = solve(scaled, rhs/scale[:, None])
    denominator = np.abs(matrix[1:3]) @ np.abs(h)
    backward = np.max(np.abs(matrix[1:3] @ h)/np.maximum(denominator, np.finfo(float).tiny))
    return float(np.linalg.cond(scaled)), float(backward)


def analytic_field(q, endpoints, gamma, thickness, layer, z):
    p, d = q[layer], thickness[layer]
    left, right = endpoints[layer], endpoints[layer+1]
    h = (np.sinh(p*(d-z))[:, None]*left + np.sinh(p*z)[:, None]*right)/np.sinh(p*d)
    derivative = p*(-np.cosh(p*(d-z))[:, None]*left + np.cosh(p*z)[:, None]*right)/np.sinh(p*d)
    return h, -derivative/gamma[layer]


def finite_elements(thickness, gamma, omega, kx, n):
    lengths = np.repeat(thickness/n, n)
    layer = np.repeat(np.arange(3), n)
    a = 1/gamma[layer]
    b = kx*kx*a + 1j*omega*MU0
    local_diag = a/lengths + b*lengths/3
    off = -a/lengths + b*lengths/6
    diag = np.r_[local_diag, 0] + np.r_[0, local_diag]
    h = np.zeros((3*n+1, 2), complex)
    h[0, 0], h[-1, 1] = 1, -1
    # Condense homogeneous interiors before mixing material coefficients (~1e14 contrast).
    # This preserves the original P1 equations and their small cross-port responses.
    matrix = np.zeros((4, 4), complex)
    lifts = []
    for material in range(3):
        ld, lo = local_diag[material*n], off[material*n]
        band = np.zeros((3, n-1), complex)
        band[1] = 1
        band[0, 1:] = band[2, :-1] = lo/(2*ld)
        rhs = np.zeros((n-1, 2), complex)
        rhs[0, 0] = rhs[-1, 1] = -lo/(2*ld)
        lift = np.vstack(([1, 0], solve_banded((1, 1), band, rhs), [0, 1]))
        condensed = ld*np.eye(2) + lo*lift[[1, -2]]
        matrix[material:material+2, material:material+2] += condensed
        lifts.append(lift)
    interfaces = h[::n].copy()
    condition, condensed_backward = solve_interfaces(matrix, interfaces)
    for material, lift in enumerate(lifts):
        h[material*n:(material+1)*n+1] = lift @ interfaces[material:material+2]
    reaction = diag[:, None]*h
    reaction[:-1] += off[:, None]*h[1:]
    reaction[1:] += off[:, None]*h[:-1]
    denominator = np.abs(diag[:, None]*h)
    denominator[:-1] += np.abs(off[:, None]*h[1:])
    denominator[1:] += np.abs(off[:, None]*h[:-1])
    backward = float(np.max(np.abs(reaction[1:-1])/np.maximum(denominator[1:-1], np.finfo(float).tiny)))
    return h, np.vstack((reaction[0], -reaction[-1])), backward, condition, condensed_backward


def tangential_jump(thickness, gamma, q, exact_h, fem_h=None, n=None):
    left_fields, right_fields, reference = [], [], []
    for interface in (1, 2):
        left = analytic_field(q, exact_h, gamma, thickness, interface-1, np.array([thickness[interface-1]]))[1][0]
        right = analytic_field(q, exact_h, gamma, thickness, interface, np.array([0.0]))[1][0]
        reference.append((left+right)/2)
        if fem_h is not None:
            left = -(fem_h[interface*n]-fem_h[interface*n-1])/(thickness[interface-1]/n*gamma[interface-1])
            right = -(fem_h[interface*n+1]-fem_h[interface*n])/(thickness[interface]/n*gamma[interface])
        left_fields.append(left)
        right_fields.append(right)
    left_fields, right_fields, reference = map(np.array, (left_fields, right_fields, reference))
    # Aggregate the actual interface fields: a single nearly-zero column cannot
    # resolve its own one-sided P1 slope. Weak Z entries retain separate gates.
    scale = max(np.linalg.norm(reference), .5*(np.linalg.norm(left_fields)+np.linalg.norm(right_fields)), np.finfo(float).tiny)
    return float(np.linalg.norm(left_fields-right_fields)/scale)


def interface_quantities(h, gamma, eps, sigma, omega, kx):
    jumps, contrast_jumps, residuals, total_jumps = [], [], [], []
    for interface in (1, 2):
        field = 1j*kx*h[interface][None, :]/gamma[interface-1:interface+1, None]
        e, s, g = (v[interface-1:interface+1, None] for v in (eps, sigma, gamma))
        free = e[1]*field[1]-e[0]*field[0]
        charge = EPS0*(field[1]-field[0])
        jc = s*field
        contrast = (g-1j*omega*EPS0)*field
        total = g*field
        norm = np.maximum(np.sum(np.abs(jc)+omega*np.abs(e*field), axis=0), np.finfo(float).tiny)
        residuals.append(float(np.max(np.abs(jc[1]-jc[0]+1j*omega*free)/norm)))
        residuals.append(float(np.max(np.abs(contrast[1]-contrast[0]+1j*omega*charge)/norm)))
        total_jumps.append(float(np.max(np.abs(total[1]-total[0])/norm)))
        jumps.append(free)
        contrast_jumps.append(charge)
    return np.array(jumps), np.array(contrast_jumps), max(residuals), max(total_jumps)


def field_and_power(thickness, gamma, eps, sigma, omega, kx, q, exact_h, fem_h=None, n=None):
    points, weights = leggauss(8 if fem_h is not None else 32)
    power = np.zeros((3, 2), complex)  # dissipative, magnetic-reactive, electric-reactive terms
    errors = []
    for layer, d in enumerate(thickness):
        intervals = n if fem_h is not None else 1
        electric_error = electric_reference = 0.0
        for cell in range(intervals):
            z = (cell+(points+1)/2)*d/intervals
            weight = weights*d/(2*intervals)
            ref_h, ref_ex = analytic_field(q, exact_h, gamma, thickness, layer, z)
            ref_ez = 1j*kx*ref_h/gamma[layer]
            if fem_h is None:
                h, ex = ref_h, ref_ex
            else:
                left, right = fem_h[layer*n+cell:layer*n+cell+2]
                h = (1-(points+1)/2)[:, None]*left + ((points+1)/2)[:, None]*right
                ex = np.broadcast_to(-(right-left)/(d/n*gamma[layer]), h.shape)
            ez = 1j*kx*h/gamma[layer]
            electric = np.abs(ex)**2+np.abs(ez)**2
            power[0] += (sigma[layer]-omega*eps[layer].imag)*np.sum(weight[:, None]*electric, axis=0)
            power[1] += 1j*omega*MU0*np.sum(weight[:, None]*np.abs(h)**2, axis=0)
            power[2] -= 1j*omega*eps[layer].real*np.sum(weight[:, None]*electric, axis=0)
            electric_error += float(np.sum(weight[:, None]*(np.abs(ex-ref_ex)**2+np.abs(ez-ref_ez)**2)))
            electric_reference += float(np.sum(weight[:, None]*(np.abs(ref_ex)**2+np.abs(ref_ez)**2)))
        errors.append(float(np.sqrt(electric_error/max(electric_reference, np.finfo(float).tiny))))
    return power, errors


def run(output):
    started = monotonic()
    assert not output.exists(), "output directory must be new"
    output.mkdir(parents=True)
    rows, properties = source_inputs()
    cases, arrays = [], {}
    for period in PERIODS:
        kx = 2*np.pi/period
        for f in FREQUENCIES:
            assert monotonic()-started < 90, "bounded joint control exceeded90s"
            omega = 2*np.pi*f
            thickness, eps, sigma, gamma = materials(rows, properties, f)
            q, exact_h, exact_z, exact_condition, exact_backward = analytic(thickness, gamma, omega, kx)
            exact_tangent = tangential_jump(thickness, gamma, q, exact_h)
            exact_power, _ = field_and_power(thickness, gamma, eps, sigma, omega, kx, q, exact_h)
            exact_pin = np.diag(exact_z)
            power_scale = np.abs(exact_pin)+np.sum(np.abs(exact_power), axis=0)
            exact_closure = float(np.max(np.abs(exact_pin-exact_power.sum(axis=0))/power_scale))
            charge, contrast_charge, charge_residual, normal_residual = interface_quantities(exact_h, gamma, eps, sigma, omega, kx)
            _, negative_h, negative_z, _, _ = analytic(thickness, gamma, omega, -kx)
            negative_charge, negative_contrast, _, _ = interface_quantities(negative_h, gamma, eps, sigma, omega, -kx)
            parity = max(relative(negative_z, exact_z), relative(negative_charge, -charge), relative(negative_contrast, -contrast_charge))
            history = []
            for n in MESHES:
                h, z, backward, condition, condensed_backward = finite_elements(thickness, gamma, omega, kx, n)
                power, errors = field_and_power(thickness, gamma, eps, sigma, omega, kx, q, exact_h, h, n)
                candidate_charge, candidate_contrast, candidate_continuity, candidate_normal = interface_quantities(h[::n], gamma, eps, sigma, omega, kx)
                closure = float(np.max(np.abs(np.diag(z)-power.sum(axis=0))/(np.abs(np.diag(z))+np.sum(np.abs(power), axis=0))))
                history.append(dict(cells_per_layer=n, impedance_component_relative_error=float(np.max(np.abs(z-exact_z)/np.maximum(np.abs(exact_z), np.finfo(float).tiny))),
                    electric_field_relative_l2_by_material=errors, free_charge_relative_error=relative(candidate_charge, charge),
                    contrast_charge_relative_error=relative(candidate_contrast, contrast_charge),
                    power_relative_closure=closure, post_elimination_current_charge_identity_residual=candidate_continuity,
                    post_elimination_normal_current_identity_residual=candidate_normal, unscaled_row_backward_error=backward,
                    condensed_interface_condition=condition, condensed_unscaled_backward_error=condensed_backward,
                    maximum_abs_q_times_cell=float(np.max(np.abs(q)*thickness/n)),
                    one_sided_tangential_e_relative_jump=tangential_jump(thickness, gamma, q, exact_h, h, n),
                    reciprocity_relative_error=relative(z, z.T), minimum_port_resistance_eigenvalue=float(np.linalg.eigvalsh((z+z.conj().T)/2).min())))
            prefix = f"case_{len(cases):02d}"
            arrays.update({prefix+"_exact_h": exact_h, prefix+"_fem_h": h, prefix+"_exact_z": exact_z, prefix+"_fem_z": z,
                prefix+"_free_interface_charge": charge, prefix+"_contrast_interface_charge": contrast_charge,
                prefix+"_exact_power_terms": exact_power, prefix+"_fem_power_terms": power})
            cases.append(dict(frequency_hz=f, controlled_period_m=period, epsilon_relative_real=(eps.real/EPS0).tolist(),
                dielectric_loss_tangent=properties[f][1], exact_power_relative_closure=exact_closure,
                exact_post_elimination_charge_identity_residual=charge_residual, exact_post_elimination_normal_identity_residual=normal_residual,
                exact_interface_condition=exact_condition, exact_unscaled_backward_error=exact_backward,
                exact_one_sided_tangential_e_relative_jump=exact_tangent,
                signed_wavenumber_parity_error=parity, refinement=history))
    failures = []
    for i, case in enumerate(cases):
        end = case["refinement"][-1]
        gates = {
            "analytic_power": case["exact_power_relative_closure"] < 1e-10,
            "analytic_charge_identity": case["exact_post_elimination_charge_identity_residual"] < 1e-10,
            "analytic_unscaled_backward": case["exact_unscaled_backward_error"] < 1e-12,
            "analytic_tangential_e": case["exact_one_sided_tangential_e_relative_jump"] < 1e-10,
            "interface_condition": max(case["exact_interface_condition"], end["condensed_interface_condition"])*np.finfo(float).eps < 1e-10,
            "signed_mode_parity": case["signed_wavenumber_parity_error"] < 1e-12,
            "all_impedance_entries": end["impedance_component_relative_error"] < 1e-3,
            "each_material_field": max(end["electric_field_relative_l2_by_material"]) < .02,
            "interface_charges": max(end["free_charge_relative_error"], end["contrast_charge_relative_error"]) < 1e-3,
            "numerical_power": end["power_relative_closure"] < 1e-9,
            "row_backward": max(end["unscaled_row_backward_error"], end["condensed_unscaled_backward_error"]) < 1e-12,
            "tangential_e": end["one_sided_tangential_e_relative_jump"] < .05,
            "tangential_e_refinement": end["one_sided_tangential_e_relative_jump"] < case["refinement"][0]["one_sided_tangential_e_relative_jump"]*.2,
            "tangential_e_last_refinement": end["one_sided_tangential_e_relative_jump"] <= case["refinement"][-2]["one_sided_tangential_e_relative_jump"],
            "reciprocity": end["reciprocity_relative_error"] < 1e-10,
            "passivity": end["minimum_port_resistance_eigenvalue"] >= 0,
            "refinement": end["impedance_component_relative_error"] < case["refinement"][0]["impedance_component_relative_error"]*.1,
        }
        failures.extend(f"case{i}:{name}" for name, passed in gates.items() if not passed)
    npz_path = output/"fields.npz"
    with npz_path.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
    result = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="PASS_JOINT_TM_BOUNDARY_CONTROL" if not failures else "STOP_JOINT_TM_BOUNDARY_CONTROL",
        script_sha256=sha(Path(__file__)), inputs={key: dict(path=path, sha256=expected) for key, (path, expected) in PINS.items()},
        normalized_material_rows=rows, mu_relative_assumption=1, epsilon0_f_per_m=EPS0, mu0_h_per_m=MU0,
        port_convention="K=[Hy(lower),-Hy(upper)], V=[Ex(lower),Ex(upper)]; no-half Pin=K^H V", 
        fields_sha256=sha(npz_path), elapsed_s=monotonic()-started, failures=failures, cases=cases,
        scope="New bounded periodic-Fourier Maxwell interface oracle. Uses retained normalized TOP/DR0102/L02 material data; copper dk4.5 is a retained normalized value, not a new raw-source material qualification. Lower-than1MHz dielectric values are the existing source-profile endpoint clamp, not independent measured dispersion. No copper-kernel or old G/C audit replay. Source dimensional/material control only; lateral periods are prescribed tests, not actual board ports. Homogeneous-subdomain condensed P1 FEM is compared with exact local endpoint modes. Interface free and contrast charges are exposed after field elimination; their continuity residuals are algebraic post-elimination identities, not independent charge-equation tests. The independent interface test is the one-sided tangential E jump, aggregated over both interfaces/excitations; weak Z entries and each material field have separate gates. This is not a general mixed-current/charge VIE implementation or qualification of finite edges, apertures, via/pad junctions, full3D quadrature, global return, board Z, PowerSI accuracy or frequency-interpolation accuracy. Complex reactive terms are Poynting balance terms, not a dispersive stored-energy formula.")
    with (output/"result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key:result[key] for key in ("status", "failures", "elapsed_s")}))
    return 0 if not failures else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
