"""SPD Decap PI Evaluator v0.23.1: resume missing finite-depth self templates."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from astra_prism_covariogram_self import prism_self

ROOT = Path('C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator')
SOURCE = ROOT/'outputs/research/astra-combined-magnetic-source-descriptor-02/combined-magnetic-source-descriptor.npz'
OLD = Path('C:/Users/User/.codex/worktrees/353f/SPD Decap PI Evaluator/outputs/child-port-physics/l14-self/l14-self-coefficients.npz')
RESUME = ROOT/'outputs/research/astra-l14-complete-prism-self-20260914-01'
PINS = {SOURCE:'7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02',
        OLD:'4ae0e0da393da9f7505e79a74e801ef14061b27ffca0072a2622b6b183dd9294',
        RESUME/'l14-self-coefficients.npz':'9cc66197b88ca91944164944680a7ca3abc55108241f28666d204e22545278a8',
        RESUME/'convergence.json':'d03c9c777c4966cb34b26c8b75a0d78217685dc3de5a100090174492b1cc8724',
        ROOT/'tools/research/astra_prism_covariogram_self.py':'71f055b52712230e9f255671bb7e7b3a4c709f9f347598ba4ecea53fbe313a15'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def complete(out):
    started = time.monotonic()
    for path, expected in PINS.items():
        assert digest(path) == expected, path
    with np.load(SOURCE,allow_pickle=False) as z:
        triangles = z['l14_triangle_vertices_um']*1e-6
    with np.load(OLD,allow_pickle=False) as z:
        data = {key:z[key] for key in z.files}
    with np.load(RESUME/'l14-self-coefficients.npz',allow_pickle=False) as z:
        for key in ('template_direct_bare_per_m','template_order','template_last_relative_change'):
            data[key] = z[key]
    previous_levels = {row['group']:row['levels'][-1] for row in json.loads((RESUME/'convergence.json').read_text())}
    bare = data['template_direct_bare_per_m']
    old_values = bare.copy()
    missing = np.flatnonzero(~np.isfinite(bare))
    representatives = data['template_representative_triangle']
    area = data['area_m2']
    upper = 2*np.sqrt(np.pi/area)
    history = []
    for gid in missing:
        if time.monotonic()-started > 90:
            break
        row = int(representatives[gid])
        triangle = triangles[row]-triangles[row,0]
        last_order,previous,_ = previous_levels[int(gid)]
        levels = []
        for order in (2048,4096):
            assert order > last_order
            value = prism_self(triangle,data['slab_z_m'],0.,order)[0]
            change = None if previous is None else abs(value-previous)/value
            levels.append([order,value,change])
            if change is not None and change <= 1e-6:
                assert value > 0 and value <= upper[row]*(1+1e-9)
                bare[gid] = value
                data['template_order'][gid] = order
                data['template_last_relative_change'][gid] = change
                break
            previous = value
        history.append(dict(group=int(gid),triangle=row,levels=levels))
        if len(history)%128 == 0:
            # Preserve partial work even if the external budget later terminates the process.
            np.savez_compressed(out/'template-checkpoint.npz',bare=bare,orders=data['template_order'],changes=data['template_last_relative_change'])
            print(json.dumps(dict(computed=len(history),remaining=int(np.isnan(bare).sum()),seconds=time.monotonic()-started)),flush=True)
    covered = np.isfinite(old_values)
    assert np.array_equal(bare[covered],old_values[covered])
    group = data['template_index']
    coefficients = 1e-7*area**2*bare[group]
    valid = np.isfinite(coefficients)
    assert np.all(coefficients[valid] > 0)
    # Bounds are geometric coefficients; they must be weighted by each NEW current.
    upper_coefficients = 1e-7*area**2*upper
    artifact = out/'l14-self-coefficients.npz'
    np.savez_compressed(artifact,original_triangle_index=data['original_triangle_index'],
        slab_z_m=data['slab_z_m'],area_m2=area,template_index=group,
        template_direct_bare_per_m=bare,template_order=data['template_order'],
        template_last_relative_change=data['template_last_relative_change'],
        coefficient_for_sheet_density_h_m2=coefficients,computed_triangle_mask=valid,
        geometric_upper_coefficient_h_m2=upper_coefficients)
    save(out/'convergence.json',history)
    report = dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='PASS_ALL_L14_FINITE_DEPTH_SELF_TEMPLATES' if valid.all() else 'PARTIAL_L14_SELF_RESUME',
        inputs={str(p):h for p,h in PINS.items()},driver_sha256=digest(Path(__file__)),
        artifact_sha256=digest(artifact),original_qualified_templates=int(covered.sum()),
        newly_qualified_templates=int(np.isfinite(bare).sum()-covered.sum()),
        missing_templates=int(np.isnan(bare).sum()),missing_triangles=int((~valid).sum()),
        existing_coefficients_unchanged=True,seconds=time.monotonic()-started,
        maximum_refinement_indicator=float(np.nanmax(data['template_last_relative_change'])),
        scope='Finite20um constant-sheet same-triangle coefficient for arbitrary new currents. Successive-order difference is an indicator, not a rigorous quadrature bound. No FMM, near completion, board solve or accuracy claim.')
    save(out/'result.json',report)
    print(json.dumps(report),flush=True)
    return 0 if valid.all() else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--worker',action='store_true')
    args = parser.parse_args()
    if args.worker:
        raise SystemExit(complete(args.output))
    args.output.mkdir(parents=True,exist_ok=False)
    frozen = args.output/'driver-at-run.py'
    frozen.write_bytes(Path(__file__).read_bytes())
    from probe_astra_fmm3d_runtime import guarded_source_worker
    raise SystemExit(guarded_source_worker(args.output,worker_command=[sys.executable,'-B',str(frozen.resolve()),'--worker','--output',str(args.output.resolve())],max_runtime_s=120))
