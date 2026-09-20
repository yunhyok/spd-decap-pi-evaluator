"""SPD Decap PI Evaluator v0.23.1: reduce one saved labelled sheet action."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from check_astra_l14_l25_point_rows import SOURCE, PIN

OMEGA = 2*np.pi*1e6
MU0 = 4*np.pi*1e-7
GAP = .00021262886699454942


def pair(value):
    return [float(value.real), float(value.imag)]


def main(out):
    guard = json.loads((out/'external-budget.json').read_text())
    receipt = json.loads((out/'fixed-current-point-action-receipt.json').read_text())
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and receipt['point_row_gate']
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == PIN
    qualification = json.loads((out/'qualification.json').read_text())
    moments = []
    with np.load(SOURCE, allow_pickle=False) as z:
        for layer, current_key in [('l14','l14_sheet_current_density_a_per_m'),('l25','l25_average_current_a_per_m')]:
            tri = z[layer+'_triangle_vertices_um']*1e-6
            edges = tri[:, 1:]-tri[:, :1]
            area = abs(edges[:, 0, 0]*edges[:, 1, 1]-edges[:, 0, 1]*edges[:, 1, 0])/2
            moments.append(area[:, None]*z[current_key])
    bilinear = np.zeros((2,2), complex)
    hermitian = np.zeros((2,2), complex)
    with np.load(out/'fixed-current-point-action.npz', allow_pickle=False) as fields:
        for source, name in enumerate(('potential_source14','potential_source25')):
            field = fields[name]
            for observer, (offset, moment) in enumerate(((0,moments[0]),(len(moments[0]),moments[1]))):
                part = field[offset:offset+len(moment)]
                bilinear[observer,source] = MU0*np.sum(moment*part)
                hermitian[observer,source] = MU0*np.sum(moment.conj()*part)
    scale = max(abs(bilinear[0,1]),abs(bilinear[1,0]),abs(hermitian[0,1]),abs(hermitian[1,0]),np.finfo(float).tiny)
    reciprocal = float(abs(bilinear[0,1]-bilinear[1,0])/scale)
    hreciprocal = float(abs(hermitian[0,1]-hermitian[1,0].conjugate())/scale)
    local = qualification['supplied_local_addends']
    l14_b = complex(*local['l14_same_triangle']['ordinary_h'])
    l14_h = float(local['l14_same_triangle']['hermitian_h'])
    l25_z = complex(*local['l25_sparse_aggregate']['ordinary_jw_i_transpose_l_i_ohm'])
    l25_h = local['l25_sparse_aggregate']['hermitian_omega_i_h_l_i_ohm']/OMEGA
    total_b = bilinear.sum()+l14_b+l25_z/(1j*OMEGA)
    total_h = hermitian.sum()+l14_h+l25_h
    terms = {}
    for observer in range(2):
        for source in range(2):
            terms[f'observer{[14,25][observer]}_source{[14,25][source]}'] = dict(
                ordinary_jomega_ohm=pair(1j*OMEGA*bilinear[observer,source]),
                hermitian_omega_scale_ohm=pair(OMEGA*hermitian[observer,source]))
    report = dict(program='SPD Decap PI Evaluator',version='0.23.1',
                  status='PASS_PROVISIONAL_LABELLED_FIXED_CURRENT_REDUCTION' if max(reciprocal,hreciprocal)<5e-5 else 'UNRESOLVED_RECIPROCAL_REDUCTION',
                  frequency_hz=1e6, centroid_terms=terms,
                  ordinary_cross_both_directions_ohm=pair(1j*OMEGA*(bilinear[0,1]+bilinear[1,0])),
                  hermitian_cross_both_directions_omega_scale_ohm=pair(OMEGA*(hermitian[0,1]+hermitian[1,0])),
                  local_l14_same_triangle_ordinary_ohm=pair(1j*OMEGA*l14_b),
                  local_l25_existing_self_plus_near_ordinary_ohm=pair(l25_z),
                  total_provisional_ordinary_ohm=pair(1j*OMEGA*total_b),
                  total_provisional_hermitian_omega_scale_ohm=pair(OMEGA*total_h),
                  magnitude_relative_to_existing_board_gap=float(abs(OMEGA*total_b)/GAP),
                  ordinary_cross_reciprocity_relative=reciprocal,
                  hermitian_cross_reciprocity_relative=hreciprocal,
                  omitted_l14_same_triangle_absolute_upper_ohm=local['l14_same_triangle']['omitted_upper_ohm'],
                  current_board_error_percent=26.18268397573785,
                  actual_board_solve_executed=False,
                  limitations=['Fixed accepted R/GC current only; no finite impedance update or PowerSI improvement claim.',
                    'L14 distinct near and global interlayer finite-support corrections are not qualified; selected pairs show material differences.',
                    'L14 finite-depth constant self and existing L25 local sheet self/selected-near model are separately owned addends.',
                    'Finite/native R/L unchanged; first-post self and full return not represented.',
                    'Hermitian omega scale is not complex port impedance, loss, or stored energy.'])
    (out/'reduction.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
