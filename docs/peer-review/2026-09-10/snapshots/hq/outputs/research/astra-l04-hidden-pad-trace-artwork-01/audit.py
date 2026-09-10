"""Test complete local pad-to-trace-to-artwork witnesses without a domain union."""
from pathlib import Path
from time import monotonic
import json
import hashlib
import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import LineString
import project_astra_l14_gc_mass as mass

HERE = Path(__file__).resolve().parent
R = HERE.parent
PINS = {
    'trace_contacts': (R/'astra-l04-hidden-pad-trace-01/result.json', 'cd568cd786581817a96d2cf557acd855e2106c64afb406fe8c9798ef02eb5d94'),
    'inventory': (R/'astra-l04-source-inventory-root-02/result.json', 'c86cf3410188e5386bfa8b5f4b57faddc6c219456e59a4ba8ac1d8e9b5f041d1'),
    'artwork': (R/'astra-l04-exception-pad-artwork-01/0001-f7ddeb208553e127.spdgeom.zlib', 'f7ddeb208553e1279e4a4ca36333d2b5e0180b7ea6391b3a3705afccc3a896f3'),
    'polygon_helper': (Path(mass.__file__), 'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3'),
}
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
started = monotonic()
assert not (HERE/'result.json').exists()
for path, digest in PINS.values():
    assert sha(path) == digest, path
contacts = json.loads(PINS['trace_contacts'][0].read_bytes())
inventory = json.loads(PINS['inventory'][0].read_bytes())
asset = inventory['geometry_assets'][0]
mapping = mass.polygon_map(asset['layer'], asset['net'], asset['asset_sha256'], PINS['artwork'][0].read_bytes())
assert set(mapping) == set(inventory['component']['island_ids']) and len(mapping) == 289
ids = sorted(mapping)
polygons = np.asarray([mapping[k] for k in ids], dtype=object)
assert all(g.is_valid and not g.is_empty and g.area > 0 for g in polygons)
tree = STRtree(polygons)
shapely.prepare(polygons)
wkb = [shapely.to_wkb(g) for g in polygons]
cache = HERE/'l04-source-islands.npz'
mass.atomic_npz(cache, island_ids_json_utf8=np.frombuffer(json.dumps(ids).encode(), dtype=np.uint8),
                island_wkb_bytes=np.frombuffer(b''.join(wkb), dtype=np.uint8),
                island_wkb_offsets=np.r_[0, np.cumsum([len(x) for x in wkb], dtype=np.int64)])
rows = []
for site in contacts['rows']:
    evidence = []
    for item in site['candidates']:
        if item['pad_overlap_area_um2'] <= 0:
            continue
        t = item['trace_source']
        ends = item['source_endpoints']
        body = LineString([(p['x_pm']*1e-6, p['y_pm']*1e-6) for p in ends]).buffer(t['width_pm']*0.5e-6, cap_style='flat')
        assert body.geom_type == 'Polygon' and body.is_valid and body.area == item['flat_body_area_um2']
        candidates = tree.query(body, predicate='intersects')
        cuts = [(int(k), body if polygons[k].covers(body) else polygons[k].intersection(body)) for k in candidates]
        positive = [{'island_id': ids[k], 'area_um2': g.area} for k, g in cuts if g.area > 0]
        assert all(g.is_valid for _, g in cuts)
        evidence.append({'trace_id': t['trace_id'], 'trace_ordinal': t['ordinal'],
                         'source_sha256': t['source_record_sha256'],
                         'pad_overlap_area_um2': item['pad_overlap_area_um2'],
                         'artwork_overlap_area_um2': shapely.union_all([g for _, g in cuts]).area,
                         'positive_artwork_islands': positive})
        assert monotonic()-started < 50
    rows.append({'original_active_finite_index': site['original_active_finite_index'],
                 'source_node': site['source_node'], 'xy_pm': site['xy_pm'],
                 'pad_trace_candidates': evidence,
                 'positive_contiguous_bridge_count': sum(bool(e['positive_artwork_islands']) for e in evidence)})
result = {'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
          'status': 'COMPLETED_CONDITIONAL_L04_LOCAL_PAD_TRACE_ARTWORK_WITNESSES',
          'inputs': {k: {'path': str(p), 'sha256': h} for k, (p, h) in PINS.items()},
          'script_sha256': sha(Path(__file__)), 'rows': rows, 'site_count': len(rows),
          'sites_with_positive_contiguous_bridge': sum(r['positive_contiguous_bridge_count'] > 0 for r in rows),
          'source_islands': {'path': str(cache), 'sha256': sha(cache), 'bytes': cache.stat().st_size, 'count': len(ids)},
          'elapsed_s': monotonic()-started,
          'scope': 'A single connected exact-width flat trace polygon has positive area overlap with both the source pad polygon and one of the289 original L04 component islands. This certifies a local polygon-material path under the inherited circle/flat-cap convention. No full conductor union, contact coalescence, drill electrode, mesh, G/C or joint-circuit assembly is certified. Source island WKBs are cached verbatim without union or repair for later source qualification.'}
mass.atomic_json(HERE/'result.json', result)
print(json.dumps({k: result[k] for k in ('status', 'sites_with_positive_contiguous_bridge', 'elapsed_s')}))
