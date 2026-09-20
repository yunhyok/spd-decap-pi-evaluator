"""SPD Decap PI Evaluator v0.23.1: one distributed Device port, 1956 pads.

These maps act on integrated physical pad currents and potentials. They do not
choose a surface mesh, electrode contact area, current density, or field model.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PADS = 'outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json'
PADS_SHA = 'a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525'
RECOVERY = 'outputs/research/astra-device-terminal-pads-01/result.json'
RECOVERY_SHA = '46a6adee4f37d5bc139d7714cc1810a92cadb1ae927979186882d844f2062b28'


def run(output):
    start = monotonic()
    data = (ROOT/PADS).read_bytes()
    assert sha256(data).hexdigest() == PADS_SHA
    assert sha256((ROOT/RECOVERY).read_bytes()).hexdigest() == RECOVERY_SHA
    pads = json.loads(data)['pads']
    assert len(pads) == len({row['pin_id'] for row in pads}) == 1956
    branches = sorted({row['branch_id'] for row in pads})
    assert len(branches) == 978
    index = {branch: ordinal for ordinal, branch in enumerate(branches)}
    pair_index = np.full((978, 2), -1, dtype=np.int64)
    terminal_map = np.zeros((1956, 2), dtype=np.int64)
    for ordinal, pad in enumerate(pads):
        role = {'power': 0, 'ground': 1}[pad['role']]
        assert pair_index[index[pad['branch_id']], role] == -1
        pair_index[index[pad['branch_id']], role] = ordinal
        terminal_map[ordinal, role] = 1
    assert np.all(pair_index >= 0) and np.array_equal(terminal_map.sum(axis=0), [978, 978])
    difference = np.array([.5, -.5])
    common = np.ones(2)
    assert np.array_equal(terminal_map @ common, np.ones(1956))
    # A valid group-port current need not return through its own source branch.
    witness = np.zeros(1956, dtype=complex)
    witness[pair_index[0, 0]] = .7+.2j
    witness[pair_index[1, 0]] = .3-.2j
    witness[pair_index[2, 1]] = -1
    group_current = terminal_map.T @ witness
    assert np.allclose(group_current, [1, -1], rtol=0, atol=1e-15)
    assert abs(witness.sum()) < 1e-15
    assert np.count_nonzero(np.abs(witness[pair_index].sum(axis=1)) > 1e-15) == 3
    voltage, floating = 1+.4j, 2.3-.1j
    pad_voltage = terminal_map @ (difference*voltage + common*floating)
    port_current = difference @ group_current
    work_error = abs(np.vdot(pad_voltage, witness) - np.conjugate(voltage)*port_current)
    assert work_error < 1e-14 and abs(port_current-1) < 1e-15
    positions = np.array([[row['x_pm'], row['y_pm']] for row in pads], dtype=np.int64)
    pair_offsets = positions[pair_index[:, 0]] - positions[pair_index[:, 1]]
    # Integer pair offsets are small (<131um); square them only after this bound.
    assert np.max(np.abs(pair_offsets)) < 131_000_000
    distance_squared_pm = np.sum(pair_offsets*pair_offsets, axis=1)
    assert np.unique(distance_squared_pm).size == 1
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(output/'group-port.npz', terminal_voltage_map=terminal_map,
        differential_voltage_map=difference, common_voltage_map=common,
        source_branch_pair_indices=pair_index, source_pad_xy_pm=positions,
        global_only_kcl_witness=witness, witness_pad_voltage=pad_voltage)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='QUALIFIED_ONE_DEVICE_GROUP_PORT_ALGEBRA__FIELD_BOUNDARY_STILL_OPEN',
        elapsed_s=monotonic()-start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        pins={PADS:PADS_SHA, RECOVERY:RECOVERY_SHA},
        maps_sha256=sha256((output/'group-port.npz').read_bytes()).hexdigest(),
        pad_count=1956, source_branch_pair_count=978, electrical_port_count=1,
        independent_floating_common_potentials=1, terminal_kcl_conditions=1,
        virtual_work_absolute_error=work_error,
        witness_nonzero_source_branch_current_sums=3,
        paired_pad_center_distance_um=float(np.sqrt(distance_squared_pm[0])/1e6),
        scope='978 power pads share one group potential and 978 ground pads share another. '
              'One differential voltage and one floating common potential; one whole-port KCL. '
              'Source branch pairing is provenance, not 978 isolated two-terminal circuits. '
              'Integrated currents remain free: no equal-per-pad current or branchwise KCL. '
              'Transpose of the voltage map sums pad currents and preserves virtual work; '
              'the witness is an algebra test, not a solved board field. Common potential '
              'uses the same infinity-referenced formulation as the current VIE work; '
              'no arbitrary finite-frequency gauge is added. Actual contact-face partition, '
              'conductor/drill volume, current-charge operators, inactive-net coupling and '
              'external-source convention remain necessary before a physical solve.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
