"""SPD Decap PI Evaluator v0.23.1:120s shared-edge metadata/geometry census.

No Green integrations, parser, triangulation, current matrix or near action.
"""
from pathlib import Path
from time import perf_counter
import hashlib, json, traceback
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-l02-shared-edge-reuse-preflight-20260912-01'
CURRENT = R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
SELF = R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz'
PINS = {CURRENT: '24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
        SELF: '5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05'}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for b in iter(lambda: stream.read(8*1024**2), b''):
            h.update(b)
    return h.hexdigest()


def run():
    start = perf_counter(); OUT.mkdir(exist_ok=False)
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='IN_PROGRESS_GEOMETRY_PREFLIGHT', phases=[])
    def record(phase, **data):
        report['elapsed_s'] = perf_counter()-start
        report['phases'].append(dict(phase=phase, elapsed_s=report['elapsed_s'], **data))
        (OUT/'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report['phases'][-1]), flush=True)
        assert perf_counter()-start < 118, '120s bounded metadata preflight'
    try:
        for path, pin in PINS.items():
            assert sha(path) == pin
        with np.load(CURRENT) as z:
            triangles = z['original_free_triangle_xy_m']; pieces = z['piece_triangle_xy_m']
            parent = z['piece_parent_free_ordinal']; columns = z['compact_local_columns']
            current_ids = z['retained_original_current_ids']; h = float(np.diff(z['z_bounds_m'])[0])
        active = np.unique(parent); nrow = len(triangles)
        assert len(active) == 1583762 and len(current_ids) == 3095418
        group = np.full(nrow, -1, np.int32); perm = np.full((nrow, 3), -1, np.int8)
        with np.load(SELF) as z:
            self_rows = z['original_free_ordinals']; group[self_rows] = z['representative_group']
            perm[self_rows] = z['canonical_vertex_permutation']
            assert np.array_equal(columns[self_rows], z['global_current_columns'])
        invperm = np.argsort(perm, axis=1).astype(np.int8)
        # Tail statistics use actual owned piece geometry, aggregated by the
        # original current-cell owner; the20 clipped cells keep their IDs.
        edge = pieces[:, [1, 2, 0]]-pieces[:, [2, 0, 1]]
        maxedge = np.sqrt(np.square(edge).sum(axis=2).max(axis=1))
        e1, e2 = pieces[:, 1]-pieces[:, 0], pieces[:, 2]-pieces[:, 0]
        area2 = abs(e1[:, 0]*e2[:, 1]-e1[:, 1]*e2[:, 0])
        assert np.all(area2 > 0)
        aspect = maxedge**2/area2
        owner_length = np.zeros(nrow); owner_aspect = np.zeros(nrow)
        np.maximum.at(owner_length, parent, maxedge); np.maximum.at(owner_aspect, parent, aspect)
        tails = []
        for label, values, thresholds in [('maximum_edge_m', owner_length, [1e-4, 1e-3, 1e-2]),
                                           ('longest_edge_over_shortest_altitude', owner_aspect, [100., 1000., 10000.])]:
            for threshold in thresholds:
                selected = active[values[active] > threshold]
                tails.append(dict(metric=label, threshold=threshold, retained_cell_members=len(selected),
                                  fraction=float(len(selected)/len(active)),
                                  involved_retained_current_columns=int(len(np.unique(columns[selected])))))
        report['tails'] = tails
        report['tail_definition'] = 'Actual integration-prism maximum edge and longest-edge/minimum-altitude=Lmax^2/(2A), maximum over pieces per original cell. Counts weight every retained cell member once; current-column counts are unions of its three existing local columns.'
        report['tail_extrema'] = dict(maximum_edge_m=float(owner_length.max()), maximum_aspect=float(owner_aspect.max()),
                                      largest_edge_original_cell=int(owner_length.argmax()), largest_aspect_original_cell=int(owner_aspect.argmax()))
        flat = columns[active].ravel(); assert np.all(flat >= 0)
        order = np.argsort(flat, kind='stable'); sorted_col = flat[order]
        counts = np.bincount(flat, minlength=len(current_ids)); assert counts.max() == 2
        starts = np.r_[0, np.flatnonzero(np.diff(sorted_col))+1]
        interior_start = starts[counts[sorted_col[starts]] == 2]
        index_a, index_b = order[interior_start], order[interior_start+1]
        ca, cb = active[index_a//3], active[index_b//3]
        fa, fb = (index_a % 3).astype(np.int8), (index_b % 3).astype(np.int8)
        shared_current = sorted_col[interior_start]
        assert np.all(ca < cb)
        edge_length = np.empty(len(ca)); mismatch = 0; not_opposite = 0
        for first in range(0, len(ca), 32768):
            sl = slice(first, first+32768); ar, br = ca[sl], cb[sl]; af, bf = fa[sl], fb[sl]
            a0, a1 = triangles[ar, (af+1)%3], triangles[ar, (af+2)%3]
            b0, b1 = triangles[br, (bf+1)%3], triangles[br, (bf+2)%3]
            same = np.all(a0 == b0, axis=1) & np.all(a1 == b1, axis=1)
            reverse = np.all(a0 == b1, axis=1) & np.all(a1 == b0, axis=1)
            mismatch += int(np.count_nonzero(~(same | reverse)))
            e = a1-a0; va, vb = triangles[ar, af]-a0, triangles[br, bf]-a0
            signa = e[:, 0]*va[:, 1]-e[:, 1]*va[:, 0]
            signb = e[:, 0]*vb[:, 1]-e[:, 1]*vb[:, 0]
            not_opposite += int(np.count_nonzero(signa*signb >= 0))
            edge_length[sl] = np.linalg.norm(e, axis=1)
        assert mismatch == not_opposite == 0
        eligible = (group[ca] >= 0) & (group[cb] >= 0)
        pair_index = np.flatnonzero(eligible)
        a, b = ca[eligible].copy(), cb[eligible].copy()
        ja, jb = invperm[a, fa[eligible]], invperm[b, fb[eligible]]
        swap = (group[a] > group[b]) | ((group[a] == group[b]) & (ja > jb))
        a[swap], b[swap] = b[swap], a[swap].copy()
        ja[swap], jb[swap] = jb[swap], ja[swap].copy()
        endpoint_match = np.empty(len(a), np.int8); chirality = np.empty(len(a), np.int8)
        opposite = np.array([[1, 2], [0, 2], [0, 1]])
        for first in range(0, len(a), 32768):
            sl = slice(first, first+32768); n = len(a[sl]); local = np.arange(n)
            ta = triangles[a[sl]][local[:, None], perm[a[sl]]]
            tb = triangles[b[sl]][local[:, None], perm[b[sl]]]
            endpoint_match[sl] = np.any(ta[local, opposite[ja[sl], 0]] != tb[local, opposite[jb[sl], 0]], axis=1)
            crossa = np.linalg.det(np.stack((ta[:, 1]-ta[:, 0], ta[:, 2]-ta[:, 0]), axis=-1))
            crossb = np.linalg.det(np.stack((tb[:, 1]-tb[:, 0], tb[:, 2]-tb[:, 0]), axis=-1))
            chirality[sl] = np.sign(crossa*crossb).astype(np.int8)
        keys = np.column_stack((group[a], group[b], ja, jb, endpoint_match, chirality))
        unique, representatives, inverse, multiplicity = np.unique(keys, axis=0, return_index=True, return_inverse=True, return_counts=True)
        shape_keys, shape_reps, shape_inverse = np.unique(keys[:, :2], axis=0, return_index=True, return_inverse=True)
        subgroups_per_shape = np.bincount(shape_inverse[representatives], minlength=len(shape_keys))
        min_length = np.full(len(shape_keys), np.inf); max_length = np.zeros(len(shape_keys))
        np.minimum.at(min_length, shape_inverse, edge_length[eligible]); np.maximum.at(max_length, shape_inverse, edge_length[eligible])
        shape_length_ratio = max_length/min_length
        report['counts'] = dict(retained_cells=len(active), retained_currents=len(current_ids), integration_prisms=len(pieces),
            facets_with_one_retained_owner=int(np.count_nonzero(counts == 1)), facets_with_two_retained_owners=len(ca),
            exact_shared_edge_endpoint_mismatches=mismatch, non_opposite_cell_pairs=not_opposite,
            uncut_single_pairs=len(a), clipped_or_union_pairs=int(np.count_nonzero(~eligible)),
            two_scalar_shape_id_candidates=len(shape_keys), refined_pair_geometry_candidates=len(unique),
            shape_id_candidates_with_multiple_refined_keys=int(np.count_nonzero(subgroups_per_shape > 1)),
            shape_id_candidates_with_shared_edge_length_ratio_above_1p000001=int(np.count_nonzero(shape_length_ratio > 1.000001)))
        report['candidate_key'] = ['min ordered scalar representative group', 'other scalar representative group',
                                    'canonical shared facet A', 'canonical shared facet B',
                                    'canonical shared endpoint correspondence', 'canonical orientation-sign product']
        report['multiplicity'] = dict(maximum=int(multiplicity.max()), median=float(np.median(multiplicity)),
            singleton_candidates=int(np.count_nonzero(multiplicity == 1)),
            largest100_candidates_pair_count=int(np.sort(multiplicity)[-100:].sum()),
            ideal_integral_reduction=float(len(a)/len(unique)))
        record('candidate_counts', **report['counts'], **report['multiplicity'])

        residual = np.empty(len(a)); beta = np.empty(len(a)); sigma_delta = np.empty(len(a))
        for first in range(0, len(a), 16384):
            assert perf_counter()-start < 110, 'Geometry-fit time cap'
            sl = slice(first, first+16384); rep = representatives[inverse[sl]]; n = len(rep); arange = np.arange(n)
            xa = triangles[a[rep]][arange[:, None], perm[a[rep]]]
            xb = triangles[b[rep]][arange[:, None], perm[b[rep]]]
            ya = triangles[a[sl]][arange[:, None], perm[a[sl]]]
            yb = triangles[b[sl]][arange[:, None], perm[b[sl]]]
            ex = np.stack((xa[:, 1]-xa[:, 0], xa[:, 2]-xa[:, 0]), axis=-1)
            ey = np.stack((ya[:, 1]-ya[:, 0], ya[:, 2]-ya[:, 0]), axis=-1)
            f = np.linalg.solve(ex.swapaxes(1, 2), ey.swapaxes(1, 2)).swapaxes(1, 2)
            predicted = np.einsum('nij,nkj->nki', f, xb-xa[:, :1])+ya[:, :1]
            scale = np.maximum(owner_length[a[sl]], owner_length[b[sl]])
            residual[sl] = np.linalg.norm(predicted-yb, axis=2).max(axis=1)/scale
            singular = np.linalg.svd(f, compute_uv=False)
            smax, smin = singular[:, 0], singular[:, 1]; determinant = abs(np.linalg.det(f))
            lower = smin**2*np.minimum(smin, 1.)**2/determinant
            upper = smax**2*np.maximum(smax, 1.)**2/determinant
            beta[sl] = np.maximum(abs(1-1/lower), abs(1-1/upper))
            sigma_delta[sl] = np.maximum(abs(smax-1), abs(smin-1))
        report['common_affine_screen'] = dict(maximum_second_triangle_vertex_residual_over_maxedge=float(residual.max()),
            counts_above={str(t):int(np.count_nonzero(residual > t)) for t in [1e-12, 1e-10, 1e-8]},
            floating_zero_residual_members=int(np.count_nonzero(residual == 0)),
            maximum_first_triangle_singular_value_deviation=float(sigma_delta.max()),
            maximum_conditional_full_energy_relative_bound=float(beta.max()),
            conditional_bounds_above_1e_6=int(np.count_nonzero(beta > 1e-6)),
            scope='F maps the first triangle. Nonzero second-triangle residual means a common exact affine map has NOT been established; the recorded energy bound is conditional, not an accepted reuse certificate. Floating residual tests also contain numerical roundoff.')
        bad_shape = np.argsort(shape_length_ratio)[-5:][::-1]
        examples = []
        for g in bad_shape:
            member = np.flatnonzero(shape_inverse == g)
            lo = member[np.argmin(edge_length[eligible][member])]; hi = member[np.argmax(edge_length[eligible][member])]
            examples.append(dict(scalar_shape_ids=shape_keys[g].tolist(), shared_edge_length_ratio=float(shape_length_ratio[g]),
                first_pair_original_cells=[int(ca[pair_index[lo]]), int(cb[pair_index[lo]])],
                second_pair_original_cells=[int(ca[pair_index[hi]]), int(cb[pair_index[hi]])],
                first_retained_current_column=int(shared_current[pair_index[lo]]), second_retained_current_column=int(shared_current[pair_index[hi]]),
                first_candidate_key=keys[lo].tolist(), second_candidate_key=keys[hi].tolist()))
        report['two_shape_ids_falsification_examples'] = examples
        report['special_original_pairs'] = np.c_[ca[~eligible], cb[~eligible], shared_current[~eligible]].tolist()
        report['conditional_integration_cost_hours'] = [dict(seconds_per_representative=t,
            all_separate_hours=float(len(a)*t/3600), candidate_only_hours=float(len(unique)*t/3600)) for t in [1., 3., 10.]]
        report['reuse_conditions'] = [
            'Use one common invertible XY affine map F and translation for both actual triangles, with the same20um thickness and unchanged z55..75um. Matching each triangle independently is insufficient.',
            'Map local RT0 basis indices using the saved actual canonical vertex permutations and local flux signs; a pair swap transposes the mutual block. Assemble the shared current using its original global column.',
            'For A=diag(F,1), flux-preserving currents obey J_member(Ax+t)=A J_reference(x)/abs(detF). Static vacuum isotropic magnetic energy has lower=smin(F)^2*min(smin(F),1)^2/abs(detF), upper=smax(F)^2*max(smax(F),1)^2/abs(detF).',
            'These Loewner inequalities concern the full two-cell6x6 energy matrix or its conforming5-DOF restriction. They do not bound the signed3x3 mutual block alone.',
            'If member-specific self blocks are retained while only mutual entries are reused, qualify the resulting whole pair using the whitened energy metric, including self accuracy and geometry changes. Weak current modes cannot use an entrywise or Frobenius gate.',
            'The Fourier bound requires one common exact affine map. A hinge/piecewise map or nonzero second-triangle mismatch needs a separate error bound or a split group; do not reuse independent scalar geometry bounds.',
            'Candidate keys and floating fits are a preflight only. No representative Green integral or member reuse was certified here. The20 clipped original cells and their incident pairs remain separate owned cases.'
        ]
        artifact = OUT/'pair-candidates.npz'
        np.savez_compressed(artifact, interior_current_column=shared_current, original_cell_a=ca, original_cell_b=cb,
            original_local_facet_a=fa, original_local_facet_b=fb, eligible_interior_pair_index=pair_index,
            candidate_group=inverse, candidate_representative_eligible_index=representatives,
            candidate_keys=unique, candidate_multiplicity=multiplicity, canonical_pair_swapped=swap,
            second_triangle_common_affine_residual=residual, conditional_full_energy_relative_bound=beta,
            thickness_m=np.array([h]))
        report.update(status='COMPLETED_SHARED_EDGE_CANDIDATE_AND_TAIL_PREFLIGHT', artifact_sha256=sha(artifact),
                      driver_sha256=sha(Path(__file__)), source_pins={str(p.relative_to(ROOT)): h for p, h in PINS.items()},
                      scope='Linear-size geometry/ownership metadata only. No Green blocks, SPD parse, triangulation, CurrentOuter/Terra edits, near assembly, or physical solve.')
        record('complete', status=report['status'], common_affine_screen=report['common_affine_screen'], tails=tails)
    except Exception:
        report.update(status='FAILED_PRESERVED_GEOMETRY_PREFLIGHT', traceback=traceback.format_exc(), elapsed_s=perf_counter()-start)
        (OUT/'failure.json').write_text(json.dumps(report, indent=2))
        raise


if __name__ == '__main__':
    run()
