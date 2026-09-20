"""Bounded owner1635 geometry-cost controls on four saved 256-triangle windows."""
from pathlib import Path
import json
import time

import numpy as np
import shapely

import project_astra_l02_gc_mass as producer


def main():
    started = time.perf_counter()
    root = Path(__file__).resolve().parents[2]
    output = root / 'outputs/research/astra-l02-gc-owner1635-cost-controls-02'
    output.mkdir(exist_ok=False)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    pins = producer.verify_fixed_inputs()
    mesh_path = root / 'outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz'
    pins['mesh'] = producer.verify_file(mesh_path, '137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9', 'mesh')
    pins['producer'] = producer.verify_file(Path(producer.__file__), '47af653f03ab5306549cad4870a3aa109f345f9891756e4e11c35c12f5d78081', 'producer')
    with np.load(mesh_path, allow_pickle=False) as saved:
        xy, triangles = saved['node_xy_um'], saved['triangles']
    with np.load(producer.OVERLAP_NPZ, allow_pickle=False) as saved:
        offsets = saved['wkb_offsets']
        overlap = shapely.from_wkb(saved['wkb_bytes'][offsets[1635]:offsets[1636]].tobytes())
    shapely.prepare(overlap)
    rows = []
    for begin in (0, 98304, 1048576, 2097152):
        points = xy[triangles[begin:begin + 256]]
        geometries = shapely.polygons(points)
        tick = time.perf_counter()
        covered = shapely.covers(overlap, geometries)
        cover_s = time.perf_counter() - tick
        partial = np.flatnonzero(~covered)
        tick = time.perf_counter()
        hits = shapely.intersects(overlap, geometries[partial])
        intersects_s = time.perf_counter() - tick
        tick = time.perf_counter()
        direct = shapely.intersection(geometries[partial], overlap)
        direct_s = time.perf_counter() - tick
        areas = shapely.area(direct)
        assert np.all(areas[~hits] == 0), 'prepared disjoint filter lost positive area'
        tick = time.perf_counter()
        filtered = shapely.intersection(geometries[partial[hits]], overlap)
        filtered_s = time.perf_counter() - tick
        assert np.all(shapely.equals_exact(filtered, direct[hits], tolerance=0.0))
        tick = time.perf_counter()
        lower, upper = points.min(axis=(0, 1)), points.max(axis=(0, 1))
        local_overlap = shapely.intersection(overlap, shapely.box(*lower, *upper))
        local = shapely.intersection(geometries[partial], local_overlap)
        local_s = time.perf_counter() - tick
        difference_area = shapely.area(shapely.symmetric_difference(direct, local))
        area_relative = float(np.max(difference_area / np.maximum(areas, 1.), initial=0.))
        tick = time.perf_counter()
        maximum_mass_relative = 0.
        for position in np.flatnonzero(areas > 0):
            parent = points[partial[position]]
            mass, _, area = producer.clipped_triangle_mass(direct[position], parent)
            local_mass, _, local_area = producer.clipped_triangle_mass(local[position], parent)
            maximum_mass_relative = max(maximum_mass_relative, float(np.max(abs(mass-local_mass))) / max(float(np.max(abs(mass))), np.finfo(float).tiny))
            assert abs(area-local_area) <= max(area, 1.) * 2e-9
        mass_s = time.perf_counter() - tick
        assert area_relative <= 2e-9 and maximum_mass_relative <= 5e-10
        row = dict(candidate_begin=begin, candidates=len(points), covered=int(covered.sum()),
            partial=len(partial), prepared_intersecting=int(hits.sum()), positive_partial=int((areas>0).sum()),
            cover_s=cover_s, prepared_intersects_s=intersects_s, full_overlap_intersections_s=direct_s,
            filtered_intersections_s=filtered_s, bbox_clip_and_intersections_s=local_s,
            both_mass_controls_s=mass_s, local_symmetric_difference_relative_max=area_relative,
            local_mass_relative_max=maximum_mass_relative,
            full_overlap_coordinates=int(shapely.get_num_coordinates(overlap)), local_overlap_coordinates=int(shapely.get_num_coordinates(local_overlap)))
        rows.append(row)
        print(json.dumps(row), flush=True)
    result = dict(program=producer.PROGRAM, version=producer.VERSION,
        status='PASS_L02_OWNER1635_GEOMETRY_COST_CONTROLS', inputs=pins, windows=rows,
        script_sha256=producer.digest(Path(__file__)), elapsed_s=time.perf_counter()-started,
        scope='1024 saved triangles only; prepared disjoint skipping and local-bbox intersection compared with full-source intersection. No full projection, mesh compilation, source change, gate relaxation or solve. Timings are window controls, not a whole-owner runtime prediction.')
    producer.l14_mass.atomic_json(output/'result.json', result)


if __name__ == '__main__':
    main()
