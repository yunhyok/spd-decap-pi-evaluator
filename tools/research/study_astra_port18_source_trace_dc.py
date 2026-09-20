"""SPD Decap PI Evaluator v0.23.1: two actual source trace/pad DC sheets.

Research-only source DC correction candidate. No full-board solve or magnetic
coefficient is generated. Micrometre similarity coordinates are valid for DC.
"""
import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np
from shapely import wkb
from shapely.geometry import Point, box
from probe_astra_dyadic_trace_sheet import inward_dyadic, solve

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    start = monotonic()
    source_path = R / 'astra-port18-artwork-joins-20260912.json'
    source = json.loads(source_path.read_bytes())
    assert source['status'] == 'RESTORED_TWO_EXACT_SOURCE_TRACE_JOINS_NOT_SHEET_SOLUTION'
    output = R / 'astra-port18-source-trace-dc-20260912-02'
    output.mkdir(exist_ok=False)
    cases = []
    for join in source['joins']:
        assert join['binding_kind'] == 'exact-source-trace'
        assert not join['surface_rows_covering_join']
        # A source-node degree check alone is insufficient to isolate a sheet.
        assert join['domain_isolation']['no_additional_conductor_contacts']
        domain = wkb.loads(join['source_derived_trace_pad_domain']['wkb_hex'], hex=True)
        assert domain.is_valid and len(join['source_derived_pad_footprints']) == 2
        assert all(p['source_row']['width_pm'] == p['source_row']['height_pm'] == 60000000
                   for p in join['source_derived_pad_footprints'])
        binding = join['active_quotient_binding']
        nodes = [join['source_nodes'][binding[key]] for key in ('from_end_node_id', 'to_start_node_id')]
        delta = np.array([nodes[1][axis]-nodes[0][axis] for axis in ('x_pm','y_pm')]) / 1e6
        length = float(np.linalg.norm(delta))
        width = join['trace']['width_pm'] / 1e6
        assert length == 60. and width == 50.
        # Rigid DC similarity coordinates rebuild source analytic circles;
        # the cached96-sided WKB remains provenance, not the new mesh boundary.
        local = box(0., -width/2, length, width/2)
        for x in (0., length):
            local = local.union(Point(x, 0.).buffer(30., quad_segs=32))
        quantum = 2.**-20
        artwork = inward_dyadic(local, quantum)
        # Source40um filled barrel cuts; the30um copper pads remain in domain.
        contacts = [inward_dyadic(Point(x, 0.).buffer(20., quad_segs=32), quantum) for x in (0., length)]
        material = join['stackup_material']
        rows = [solve(artwork, contacts, level, material['conductivity_s_per_m'],
                      material['thickness_um']*1e-6, 'research:port18:' + join['trace']['trace_id'])
                for level in (2, 3)]
        change = abs(rows[1]['r_ohm']-rows[0]['r_ohm']) / rows[1]['r_ohm']
        case = dict(trace_id=join['trace']['trace_id'], source_hash=join['trace']['source_record_sha256'],
                    source_nodes=[n['node_id'] for n in nodes], binding=binding,
                    cached_source_rendering_circle_sides=96, mesh_circle_sides=128,
                    conductor_pad_radius_um=30., electrode_radius_um=20.,
                    rendering_area_change_fraction=(local.area-domain.area)/domain.area,
                    dyadic_quantum_um=quantum, domain_hausdorff_um=local.hausdorff_distance(artwork),
                    cases=rows, observed_mesh_relative_change=change,
                    passes_1pct_pair_screen=bool(change < .01),
                    resistance_candidate_ohm=rows[-1]['r_ohm'])
        cases.append(case)
        print(json.dumps(case), flush=True)
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='COMPLETED_SOURCE_TRACE_DC_CANDIDATES_NOT_BOARD_VALIDATED',
                  source=dict(path=str(source_path), sha256=digest(source_path)), cases=cases,
                  driver_sha256=digest(Path(__file__)), elapsed_s=monotonic()-start,
                  limitations=['A fixed128-sided circle model and two mesh levels give a sensitivity screen, not full spatial convergence; failed96-sided inward snapping is preserved separately.',
                               'DC similarity coordinates cannot be reused as SI coordinates for AC magnetic assembly.',
                               'Source-derived20um equipotential barrel cuts omit3D current redistribution.',
                               'No external/mutual inductance, dielectric correction, full-board solution or PowerSI comparison.'])
    (output/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status':report['status'],'elapsed_s':report['elapsed_s']}))


if __name__ == '__main__':
    run()
