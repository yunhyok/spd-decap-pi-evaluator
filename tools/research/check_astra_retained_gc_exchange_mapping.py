"""Verify restored face/native exchange metadata; no geometry or point load."""
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy import sparse

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    folder = R/'astra-retained-gc-exact-polygon-support-20260912-11'
    artifact = folder/'retained-gc-exact-polygon-support.npz'
    audit = R/'astra-retained-gc-electrode-ownership-20260912/ownership.json'
    assert sha(artifact) == '8aa88cfc280a2ea011b572a7571a8a166f8d48cb09404fd7c365894ecf16464d'
    assert sha(audit) == 'f1cc02dbc71c54a0fc69ef4a75f5cd511bf35673f3fec8a83daffd798d2186aa'
    expected = json.loads(audit.read_text(encoding='utf-8'))
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['artifact_sha256'] == sha(artifact)
    # The centroid gate is still unresolved. This check qualifies metadata only.
    assert not manifest['gates']['centroid_first_moment']
    assert all(value for key,value in manifest['gates'].items() if key != 'centroid_first_moment')
    with np.load(artifact, allow_pickle=False) as z:
        ids = json.loads(z['island_ids_json_utf8'].tobytes().decode())
        support = z['support_island_index']
        height = z['support_face_z_um']
        reduced, active = z['support_global_reduced_index'], z['support_active_index']
        layer, asset = z['support_layer'], z['support_asset_sha256']
        ledger = [[ids[int(i)], float(h), int(r), int(a), str(l), str(s)]
                  for i,h,r,a,l,s in zip(support,height,reduced,active,layer,asset)]
        for side in ('upper', 'lower'):
            columns = z[f'owner_{side}_support_index']
            assert np.array_equal(active[columns], z[f'owner_{side}_active_index'])
            assert np.array_equal(reduced[columns], z[f'owner_{side}_global_reduced_index'])
            assert [ids[int(support[c])] for c in columns] == z[f'owner_{side}_island_id'].tolist()
        partials = np.unique(z['owner_partial_ordinal'])
    ledger_sha = hashlib.sha256(json.dumps(ledger, separators=(',', ':')).encode()).hexdigest()
    assert ledger_sha == expected['face_ledger_sha256']
    assert np.array_equal(active, expected['exchange_map']['row_native_active_index'])
    assert len(ledger) == 4386 and len(ids) == 2467 and len(np.unique(active)) == 1048
    shape = tuple(expected['exchange_map']['shape'])
    matrix = sparse.csc_matrix((np.ones(len(active)), (active, np.arange(len(active)))), shape=shape)
    assert matrix.nnz == 4386 and np.array_equal(np.asarray(matrix.sum(axis=0)).ravel(), np.ones(4386))
    q = np.sin(np.arange(4386)*.13) + 1j*np.cos(np.arange(4386)*.17)
    v = np.zeros(shape[0], complex)
    aliases = np.unique(active)
    v[aliases] = np.cos(aliases*.001) + 1j*np.sin(aliases*.0013)
    injected = matrix @ q
    left, right = v @ injected, (matrix.T @ v) @ q
    duality = float(abs(left-right)/max(abs(left),abs(right),1e-300))
    conservation = float(abs(injected.sum()-q.sum())/max(abs(q.sum()),1e-300))
    assert duality <= 2e-12 and conservation <= 2e-12
    out = R/'astra-retained-gc-exchange-metadata-20260912'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    target = out/'native-face-exchange.npz'
    sparse.save_npz(target, matrix)
    saved = sparse.load_npz(target)
    assert np.array_equal(saved.indices, matrix.indices) and np.array_equal(saved.indptr, matrix.indptr)
    assert np.array_equal(saved.data, matrix.data) and np.array_equal(saved@q, injected)
    report = dict(status='PASS_RESTORED_NATIVE_FACE_EXCHANGE_METADATA_ONLY',
                  shape=list(shape), faces=4386, islands=2467, native_aliases=1048,
                  retained_partial_ordinals=partials.tolist(), face_ledger_sha256=ledger_sha,
                  ordinary_transpose_duality_relative=duality, total_exchange_conservation_relative=conservation,
                  artifact_sha256=sha(target), source_artifact_sha256=sha(artifact), ownership_sha256=sha(audit),
                  driver_sha256=sha(Path(__file__)),
                  scope='E maps one independent face exchange to its existing native alias, with no extra shorts. Restored centroid qualification remains pending. No GC retirement, new physical P, coupled board operator, or board solve is authorized by this metadata check.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
