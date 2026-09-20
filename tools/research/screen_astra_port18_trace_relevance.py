"""Saved native1MHz,1A port derivative for the two restored source Trace resistors."""
import hashlib
import json
from pathlib import Path

import numpy as np

R = Path(__file__).resolve().parents[2] / 'outputs/research'


def run():
    chain_path = R/'astra-port18-selected-pin-chain-topology-20260912.json'
    dc_path = R/'astra-port18-source-trace-dc-20260912-02/result.json'
    raw_path = R/'astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz'
    chain = json.loads(chain_path.read_bytes())['rows']
    dc = json.loads(dc_path.read_bytes())
    with np.load(raw_path, allow_pickle=False) as raw:
        frequency = float(raw['frequency_hz'][0])
        v = raw['active_voltage'][:, 0]
        assert frequency == 1e6 and len(v) == 756889
    currents = np.array([(v[s['from_active']]-v[s['to_active']]) /
                         (s['resistance_ohm']+2j*np.pi*frequency*s['inductance_h']) for s in chain])
    assert np.max(abs(currents-currents[0])) < 1e-10*abs(currents[0])
    # Ordinary reciprocal derivative uses i**2, not abs(i)**2. Joule heating
    # uses abs(i)**2; these are different even when this saved current is real.
    dz = sum(currents[index]**2*case['resistance_candidate_ohm'] for index, case in zip((5, 9), dc['cases'], strict=True))
    z = v[2699]-v[2656]
    pair = lambda value: [float(value.real), float(value.imag)]
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='SAVED_NATIVE_PORT_DERIVATIVE_SCREEN_NOT_FINITE_BOARD_CORRECTION',
                  frequency_hz=frequency, source_normalization_a=1., old_native_port_z_ohm=pair(z),
                  selected_path_current_a=pair(currents[0]),
                  thirteen_branch_current_spread_a=float(np.max(abs(currents-currents[0]))),
                  first_order_trace_delta_z_ohm=pair(dz),
                  first_order_relative_port_change=float(abs(dz)/abs(z)),
                  interpretation='This selected pin has little first-order influence in the saved native1MHz model. Do not spend a large board solve solely on these two resistors without new relevance evidence.',
                  limitations=['Native baseline has collapsedL14; this is not the current hybrid field operator.',
                               'The derivative is not a finite-change bound and says nothing about10MHz/100MHz or other pin geometries.',
                               'Do not multiply by fanout or use this result to explain the board correlation error.'],
                  inputs=dict(chain=str(chain_path),dc=str(dc_path),raw=str(raw_path)),
                  driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    output = R/'astra-port18-trace-relevance-20260912.json'
    assert not output.exists()
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    run()
