"""SPD Decap PI Evaluator v0.23.1: exact current/terminal lift of one saved port branch.

This verifies the existing source-owned scalar RL stamp only. It neither imports
canonical field coefficients nor assembles or solves the production board.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from assemble_astra_l02_conditional_hybrid_operator import branch_laplacian


ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def current_terminal_block(resistance, inductance, count, frequency):
    assert resistance > 0 and inductance >= 0 and count > 0 and frequency > 0
    z = (resistance+2j*np.pi*frequency*inductance)/count
    # Current points first->second; H is outward from the conductor into terminals.
    h = np.array([[-1.], [1.]])
    block = np.zeros((3, 3), dtype=complex)
    block[0, 0], block[0, 1:], block[1:, 0] = z, h[:, 0], -h[:, 0]
    return block, h, z


def run():
    source_row = 587
    raw_path = R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz'
    pack_path = R/'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz'
    with np.load(raw_path, allow_pickle=False) as raw:
        first = int(raw['finite_first_active_indices'][source_row])
        second = int(raw['finite_second_active_indices'][source_row])
        resistance = float(raw['finite_resistance_ohm_per_via'][source_row])
        inductance = float(raw['finite_inductance_h_per_via'][source_row])
        count = float(raw['finite_count'][source_row])
        original = int(raw['finite_active_original_indices'][source_row])
        owner = json.loads(json.loads(raw['all_finite_link_owner_ids_json'].tobytes())[original])
    assert (first, second, owner) == (2699, 663929, ['via:via507127'])
    with np.load(pack_path, allow_pickle=False) as pack:
        matches = np.flatnonzero(pack['final_finite_native_active_row'] == source_row)
        assert len(matches) == 1
        index = int(matches[0])
        assert int(pack['final_finite_split_leg'][index]) == -1
        assert int(pack['finite_first_active_index'][index]) == first
        assert int(pack['finite_second_active_index'][index]) == second
        saved_y = complex(pack['finite_admittance_s'][index])
    with np.load(R/'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz', allow_pickle=False) as op:
        assert int(op['positive_active_index'][0]) == first
    voltage = np.array([.37+.2j, -.19+.1j])
    rows = []
    for frequency in (1e6, 1e7):
        block, h, z = current_terminal_block(resistance, inductance, count, frequency)
        schur = block[1:, 1:]-np.outer(block[1:, 0], block[0, 1:])/block[0, 0]
        native = branch_laplacian([0], [1], [1/z], 2).toarray()
        stamp_error = float(np.max(abs(schur-native))/np.max(abs(native)))
        current = -(h[:, 0]@voltage)/z
        terminal = -h[:, 0]*current
        current_equation = z*current+h[:, 0]@voltage
        circuit_error = float(np.linalg.norm(terminal-native@voltage)/np.linalg.norm(terminal))
        source_power = voltage@np.conj(terminal)
        conductor_power = z*abs(current)**2
        power_error = float(abs(source_power-conductor_power)/abs(conductor_power))
        assert stamp_error < 1e-13 and circuit_error < 1e-13 and power_error < 1e-13
        assert abs(current_equation) < 1e-13*max(abs(z*current), 1.)
        assert abs(terminal.sum()) < 1e-13*max(np.linalg.norm(terminal), 1.)
        if frequency == 1e6:
            assert abs(1/z-saved_y) < 1e-13*abs(saved_y)
        rows.append(dict(frequency_hz=frequency, source_z_ohm=[z.real,z.imag],
                         stamp_relative_error=stamp_error, current_kcl_relative_error=circuit_error,
                         power_relative_error=power_error))
    report = dict(program='SPD Decap PI Evaluator',version='0.23.1',
                  status='PASS_EXACT_SOURCE_RL_CURRENT_TERMINAL_LIFT_ONLY',
                  source=dict(native_row=source_row,pack_row=index,owner=owner,
                              first_active=first,second_active=second,split_leg=-1,
                              resistance_per_via_ohm=resistance,inductance_per_via_h=inductance,count=count),
                  cases=rows, driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  reusable='Explicit current and terminal-incidence block; eliminating current exactly reproduces the existing actual source branch stamp.',
                  not_qualified=[
                      'No added charge or geometric field coefficient; source GC and every other branch remain outside this unchanged stamp.',
                      'The reduced source node is not a measured finite electrode geometry.',
                      'Full field replacement requires identifying the original geometric cuts, contact area, return path, free-charge owner and removed scalar RL/GC terms.',
                      'The canonical has unresolved spatial/thickness error and supplies no reference Z or component values here.',
                      'This is not a full-path or board solve, convergence proof, or PowerSI accuracy improvement.',
                  ])
    output=R/'astra-port18-source-branch-bridge-20260912.json'
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    run()
