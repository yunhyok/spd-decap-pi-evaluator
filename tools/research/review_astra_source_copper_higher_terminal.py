"""SPD Decap PI Evaluator v0.23.1: saved 58-variable higher-Cu terminal review."""
from __future__ import annotations

import hashlib
import json
from math import pi
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/"outputs/research/astra-source-copper-higher-terminal-01"
REVIEW = ROOT/"outputs/research/astra-source-copper-higher-terminal-review-01"
EPS0 = 8.8541878128e-12
MU0 = 4e-7*pi
PINS = {
    "producer": ("tools/research/diagnose_astra_source_copper_higher_terminal.py", "2b09aab847491b73c87753a3a481626896d99aed9789b714c6529bc9ee899d98"),
    "driver": ("outputs/research/astra-source-copper-higher-terminal-01/driver-at-run.py", "2b09aab847491b73c87753a3a481626896d99aed9789b714c6529bc9ee899d98"),
    "result": ("outputs/research/astra-source-copper-higher-terminal-01/result.json", "541026b093fa3ac946e04c40c52ac56accb39b36c4893aca5aceca71e68f3b25"),
    "fields": ("outputs/research/astra-source-copper-higher-terminal-01/fields.npz", "edb04ce6c5381ea8eca57f5aab9fc10f34bb9b864878d67c2df422f681b9a7b7"),
    "space": ("outputs/research/astra-source-copper-higher-space-01/space.npz", "127b4bd12c347b3d6e60d7c5dd15eba9dc28ce4a38a2f714c582ddd4cbb61904"),
    "old_fields": ("outputs/research/astra-source-copper-curl-terminal-01/fields.npz", "7680fb2623c7d59bd3d77767d6e966c9bead34f571537f2ab429dd7bd2b0262d"),
    "contacts": ("outputs/research/astra-source-potential-contacts-01/contacts.npz", "55294d51fdb4da642499e38e7e613a226d9fc5561d2159e73f38de1f853aa21a"),
    "low_face": ("outputs/research/astra-source-potential-kernels-01/kernels-v14-f28.npz", "6f1e9d601acb36cc0db23603fcf21b8a821f5c638567dd37dd66a61d4d485f61"),
    "high_face": ("outputs/research/astra-source-potential-kernels-01/kernels-v20-f36.npz", "bffa3704cff006606f987911cb4121efaa39a8bf6c16cf704a7b4600afbcf437"),
    "green_result": ("outputs/research/astra-source-copper-higher-green-01/result.json", "9c20ba03d07ff7399cff1fdbb34c9946008bb0388c449cf1c12b1c022a84afbd"),
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def relative(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a)-np.asarray(b))/max(np.linalg.norm(b), np.finfo(float).tiny))


def pairs(distributional):
    return [[(int(i//4), int(i%4)) for i in np.flatnonzero(row)] for row in distributional]


def run():
    for path, expected in PINS.values():
        assert digest(ROOT/path) == expected, path
    report = json.loads((ROOT/PINS["result"][0]).read_text(encoding="utf-8"))
    assert report["program"] == "SPD Decap PI Evaluator" and report["version"] == "0.23.1"
    assert report["status"] == "STOP_SOURCE_HIGHER_COPPER_TERMINAL_DIAGNOSTIC"
    assert report["script_sha256"] == PINS["producer"][1] and report["fields_sha256"] == PINS["fields"][1]
    assert {k for k, v in report["gates"].items() if not v} == {"interface_own_trace_below_one_percent"}

    with np.load(ROOT/PINS["fields"][0], allow_pickle=False) as z:
        saved = {k: z[k] for k in z.files}
    with np.load(ROOT/PINS["old_fields"][0], allow_pickle=False) as z:
        old = {k: z[k] for k in z.files}
    with np.load(ROOT/PINS["space"][0], allow_pickle=False) as z:
        space = {k: z[k] for k in z.files}
    with np.load(ROOT/PINS["contacts"][0], allow_pickle=False) as z:
        tetrahedra, triangles = z["tetrahedra_m"], z["triangles_m"]
        material, distributional = z["material_id"], z["distributional_face_divergence"]
    assert saved["mass"].shape == (48, 48) and saved["regional_mass"].shape == (3, 48, 48)
    assert np.array_equal(saved["mass"], space["mass"])
    assert saved["cell_integrated_current_map"].shape == (18, 3, 48)
    assert saved["noncontact_divergence"].shape == (24, 48) and saved["contact_divergence"].shape == (8, 48)

    volumes = abs(np.linalg.det(tetrahedra[:, 1:]-tetrahedra[:, :1]))/6
    old_h = old["cell_integrated_current_map"]
    new_h = space["new_cell_integrated_current_map"]
    reconstructed_regions = []
    for region in range(3):
        choose = material == region
        aa = old["regional_mass"][region]
        rt0_cross = np.einsum("tdi,tdj,t->ij", old_h[choose, :, :36], new_h[choose], 1/volumes[choose])
        polynomial_cross = np.zeros((6, 6))
        bb = np.zeros((6, 6))
        if region != 1:
            sl = slice(0, 3) if region == 0 else slice(3, 6)
            polynomial_cross[sl, sl] = space["polynomial_mass"][:6, 6:][sl, sl]
            bb[sl, sl] = space["polynomial_mass"][6:, 6:][sl, sl]
        ab = np.vstack((rt0_cross, polynomial_cross))
        reconstructed_regions.append(np.block([[aa, ab], [ab.T, bb]]))
    reconstructed_regions = np.asarray(reconstructed_regions)
    region_error = relative(reconstructed_regions, saved["regional_mass"])
    mass_sum_error = relative(reconstructed_regions.sum(axis=0), saved["mass"])
    regional_min_normalized_eigenvalue = 1.0
    for matrix in reconstructed_regions:
        diagonal = np.diag(matrix); keep = np.flatnonzero(diagonal > diagonal.max()*1e-14)
        normalized = matrix[np.ix_(keep, keep)]/np.sqrt(diagonal[keep])[:, None]/np.sqrt(diagonal[keep])[None, :]
        regional_min_normalized_eigenvalue = min(regional_min_normalized_eigenvalue,
                                                  float(np.linalg.eigvalsh(normalized).min()))

    active = saved["active_charge_faces"].astype(int)
    contact = saved["contact_faces"].astype(int); noncontact = saved["noncontact_faces"].astype(int)
    lookup = {int(face): i for i, face in enumerate(active)}
    cc = np.array([lookup[int(face)] for face in contact]); nc = np.array([lookup[int(face)] for face in noncontact])
    bn, bc = saved["noncontact_divergence"], saved["contact_divergence"]
    vmap = saved["terminal_potential_map"]; dmap = saved["differential_voltage_map"]; cmap = saved["common_voltage_map"]
    transform = saved["old_real_current_transform"]
    owners = pairs(distributional)
    areas = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=1)/2
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    polar_x, polar_w = np.polynomial.legendre.leggauss(20)
    sphere_weights = np.repeat(polar_w, 48)*2*pi/48
    case_lookup = {(float(c["frequency_hz"]), c["kernel_tag"]): c for c in report["cases"]}
    kernels = {}
    for tag, key in (("v14f28", "low_face"), ("v20f36", "high_face")):
        with np.load(ROOT/PINS[key][0], allow_pickle=False) as z:
            kernels[tag] = (z["frequencies_hz"], z["face_green_per_m"])

    maxima = {name: 0.0 for name in (
        "old_block", "scaled_backward", "unscaled_backward", "charge", "potential", "terminal_voltage",
        "terminal_current", "admittance", "contact_potential", "continuity", "pair_kcl", "impedance",
        "material_electric", "material_current", "absorption", "terminal_power", "radiation", "power_report",
        "interface_report", "change_report", "q_current_report", "q_admittance_report")}
    worst_interface = 0.0; per_order = {}; refinements = []
    frequencies = np.array([1e3, 1e4, 1e5, 1e6, 1e7, 1e8])
    for fi, frequency in enumerate(frequencies):
        omega = 2*pi*frequency; per_order[fi] = {}
        for tag, (kernel_frequencies, face_green) in kernels.items():
            assert kernel_frequencies[fi] == frequency
            prefix = f"{tag}_{fi:02d}_"; case = case_lookup[(float(frequency), tag)]
            system, rhs, solution = saved[prefix+"system"], saved[prefix+"rhs"], saved[prefix+"scaled_solution"]
            current_scale, charge_scale = saved[prefix+"current_scale"], saved[prefix+"charge_scale"]
            column_scale = np.r_[current_scale, charge_scale, np.ones(2)]
            unknown = column_scale[:, None]*solution
            scaled_residual = system@solution-rhs
            unscaled = system/column_scale[None, :]
            unscaled_residual = unscaled@unknown-rhs
            maxima["scaled_backward"] = max(maxima["scaled_backward"], float(np.max(abs(scaled_residual)/np.maximum(abs(system)@abs(solution)+abs(rhs), np.finfo(float).tiny))))
            maxima["unscaled_backward"] = max(maxima["unscaled_backward"], float(np.max(abs(unscaled_residual)/np.maximum(abs(unscaled)@abs(unknown)+abs(rhs), np.finfo(float).tiny))))
            old_indices = np.r_[0:42, 48:58]
            maxima["old_block"] = max(maxima["old_block"], relative(system[np.ix_(old_indices, old_indices)], old[prefix+"system"]), relative(rhs[old_indices], old[prefix+"rhs"]))

            modal, qc, common = unknown[:48], unknown[48:56], unknown[56:58]
            gamma = saved[prefix+"cell_gamma"]; kappa = gamma-1j*omega*EPS0
            qn = 1j*bn@modal/omega; charge = np.zeros((32, 2), complex); charge[nc] = qn; charge[cc] = qc
            potential = face_green[fi]@charge/(4*pi*EPS0)
            terminal_voltage = dmap+cmap@common
            terminal_current = vmap.T@bc@modal/(kappa[0]/gamma[0]); admittance = dmap.T@terminal_current
            for name, actual, key in (("charge", charge, "charge"), ("potential", potential, "potential"),
                ("terminal_voltage", terminal_voltage, "terminal_voltage"), ("terminal_current", terminal_current, "terminal_current"),
                ("admittance", admittance, "port_admittance")):
                maxima[name] = max(maxima[name], relative(actual, saved[prefix+key]))
            maxima["continuity"] = max(maxima["continuity"], float(np.max(abs(bn@modal+1j*omega*qn)/np.maximum(abs(bn)@abs(modal)+omega*abs(qn), np.finfo(float).tiny))))
            maxima["contact_potential"] = max(maxima["contact_potential"], relative(potential[cc], vmap@terminal_voltage))
            pair_kcl = np.array([abs(terminal_current[a]+terminal_current[b])/np.maximum(np.hypot(abs(terminal_current[a]), abs(terminal_current[b])), np.finfo(float).tiny) for a, b in ((0, 2), (1, 3))])
            maxima["pair_kcl"] = max(maxima["pair_kcl"], float(np.max(abs(pair_kcl-np.asarray(case["pair_kcl_relative"])))))
            impedance = 1/np.diag(admittance); reported_z = np.array([complex(*x) for x in case["shorted_port_impedance_ohm"]])
            maxima["impedance"] = max(maxima["impedance"], relative(impedance, reported_z))

            u = modal@drives
            energies = np.array([np.diag(u.conj().T@r@u).real for r in reconstructed_regions])
            region_gamma = np.array([gamma[material == region][0] for region in range(3)])
            region_kappa = np.array([kappa[material == region][0] for region in range(3)])
            loss = np.sum((region_gamma.real/abs(region_kappa)**2)[:, None]*energies, axis=0)
            for region, item in enumerate(case["material_observables"]):
                e = np.sqrt(energies[region]/volumes[material == region].sum())/abs(region_kappa[region])
                j = np.sqrt(energies[region])*abs(region_gamma[region]/region_kappa[region])
                maxima["material_electric"] = max(maxima["material_electric"], relative(e, item["electric_rms_v_m"]))
                maxima["material_current"] = max(maxima["material_current"], relative(j, item["total_current_l2_a_sqrt_inverse_m"]))
            pin = np.real(np.sum((terminal_voltage@drives).conj()*(terminal_current@drives), axis=0))
            far = saved[prefix+"transverse_body_far"]
            k = omega*np.sqrt(MU0*EPS0)
            radiation = omega*MU0*k/(16*pi*pi)*np.einsum("n,ndj,ndj->j", sphere_weights, far.conj(), far).real
            closure = (pin-loss-radiation)/(abs(pin)+abs(loss)+radiation)
            maxima["absorption"] = max(maxima["absorption"], relative(loss, case["absorption_w"]))
            maxima["terminal_power"] = max(maxima["terminal_power"], relative(pin, case["terminal_input_power_w"]))
            maxima["radiation"] = max(maxima["radiation"], relative(radiation, case["radiation_from_body_current_w"]))
            maxima["power_report"] = max(maxima["power_report"], float(np.max(abs(closure-case["port_minus_loss_body_radiation_relative"]))))

            face_u = (transform@u[:36]).reshape(18, 4, 3)*(gamma/kappa)[:, None, None]
            rebuilt = []
            for pair_id in ((0, 1), (1, 2)):
                jump = np.zeros(3); own = np.zeros(3); count = 0
                for face, adjacency in enumerate(owners):
                    if len(adjacency) != 2:
                        continue
                    (a, af), (b, bf) = adjacency
                    if tuple(sorted((int(material[a]), int(material[b])))) != pair_id:
                        continue
                    left, right = face_u[a, af], face_u[b, bf]
                    jump += abs(left+right)**2/areas[face]
                    own += (abs(left)**2+abs(right)**2)/areas[face]
                    count += 1
                assert count == 2
                rebuilt.append(np.sqrt(jump/np.maximum(own, np.finfo(float).tiny)))
            for actual, item in zip(rebuilt, case["interfaces"]):
                maxima["interface_report"] = max(maxima["interface_report"], float(np.max(abs(actual-item["normal_u_jump_own_trace_relative"]))))
                worst_interface = max(worst_interface, float(np.max(actual)))

            reference = np.vstack((old[prefix+"current"], np.zeros((6, 2))))
            delta = (modal-reference)@drives
            current_change = np.sqrt(np.diag(delta.conj().T@saved["mass"]@delta).real/np.diag(u.conj().T@saved["mass"]@u).real)
            old_z = 1/np.diag(old[prefix+"port_admittance"])
            complex_change = abs(impedance-old_z)/abs(impedance)
            resistance_change = (impedance.real-old_z.real)/impedance.real
            maxima["change_report"] = max(maxima["change_report"], float(np.max(abs(current_change-case["current_mass_change_vs42_relative_new"]))),
                float(np.max(abs(complex_change-case["complex_z_change_vs42_relative_new"]))), float(np.max(abs(resistance_change-case["resistance_change_vs42_relative_new"]))))
            per_order[fi][tag] = (modal, admittance)
        low, high = per_order[fi]["v14f28"], per_order[fi]["v20f36"]
        difference = low[0]-high[0]
        current_q = float(np.sqrt(np.trace(difference.conj().T@saved["mass"]@difference).real/np.trace(high[0].conj().T@saved["mass"]@high[0]).real))
        y_q = relative(low[1], high[1]); item = report["quadrature_comparisons"][fi]
        maxima["q_current_report"] = max(maxima["q_current_report"], abs(current_q-item["current_mass_refinement_relative"]))
        maxima["q_admittance_report"] = max(maxima["q_admittance_report"], abs(y_q-item["port_admittance_refinement_relative"]))
        refinements.append(dict(frequency_hz=float(frequency), current_mass_refinement_relative=current_q, port_admittance_refinement_relative=y_q))

    gates = {
        "regional_exact_mass": max(region_error, mass_sum_error) < 2e-13 and regional_min_normalized_eigenvalue > -1e-12,
        "stored_equations": max(maxima["scaled_backward"], maxima["unscaled_backward"], maxima["old_block"]) < 2e-12,
        "charge_contact_circuit": max(maxima[k] for k in ("charge", "potential", "terminal_voltage", "terminal_current", "admittance", "contact_potential", "continuity", "pair_kcl", "impedance")) < 2e-12,
        "exact_mass_work": max(maxima[k] for k in ("material_electric", "material_current", "absorption", "terminal_power", "radiation", "power_report")) < 2e-12,
        "interface_and_sensitivity_reports": max(maxima["interface_report"], maxima["change_report"], maxima["q_current_report"], maxima["q_admittance_report"]) < 2e-12,
        "physical_stop_reproduced": worst_interface > 0.01,
    }
    assert all(gates.values()), dict(gates=gates, maxima=maxima, region_error=region_error,
                                     mass_sum_error=mass_sum_error,
                                     regional_min_normalized_eigenvalue=regional_min_normalized_eigenvalue)
    receipt = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_SAVED_58_ALGEBRA_EXACT_MASS_WORK__CONFIRM_STOP_INTERFACE",
        "reviewer_sha256": digest(Path(__file__)), "pins": {k: list(v) for k, v in PINS.items()},
        "gates": gates, "maxima": maxima, "regional_mass_relative": region_error,
        "regional_mass_sum_relative": mass_sum_error,
        "regional_minimum_normalized_supported_eigenvalue": regional_min_normalized_eigenvalue,
        "worst_interface_own_trace_relative": worst_interface, "quadrature_comparisons": refinements,
        "scope": ("Read-only saved-array review: 58-equation residual and frozen42 subblock, independent/contact charges, terminal circuit, "
                  "regional exact polynomial mass loss and RMS, interface own traces, saved body far power, and q14/q20 changes. "
                  "No solve or Green integration was repeated. "
                  "The algebra/work is accepted while the physical field remains STOP above the 1% interface discriminator. "
                  "The 42-to48 changes are sensitivity, not attribution or convergence. No board-Z or PowerSI accuracy approval.")}
    REVIEW.mkdir()
    with (REVIEW/"independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False); stream.write("\n")


if __name__ == "__main__":
    run()
