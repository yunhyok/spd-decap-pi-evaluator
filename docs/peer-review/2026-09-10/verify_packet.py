"""Verify published review files and optional downloaded arrays; never run a solver."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, help='Directory containing the seven Release NPZ files')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    paths = set()
    malformed = {r['path'] for r in manifest['historical_json_parse_errors']}
    for row in manifest['files']:
        path = (root/row['path']).resolve()
        assert path.is_relative_to(root), row['path']
        assert path not in paths, row['path']
        paths.add(path)
        assert path.stat().st_size == row['bytes'], row['path']
        assert digest(path) == row['sha256'], row['path']
        if path.suffix == '.json' and row['path'] not in malformed:
            json.loads(path.read_text(encoding='utf-8-sig'))
    actual = {p.resolve() for p in (root/'snapshots').rglob('*') if p.is_file()}
    assert actual == paths, 'Snapshot inventory differs from manifest'
    checked = 0
    for doc in root.glob('*.md'):
        text = doc.read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([^)]+)\)', text):
            target = target.strip('<>')
            assert not re.match(r'^[A-Za-z]:[/\\]', target), (doc.name, target)
            url = urlsplit(target)
            if url.scheme or not url.path:
                continue
            path = (doc.parent/unquote(url.path)).resolve()
            assert path.is_relative_to(root) and path.is_file(), (doc.name, target)
            if re.fullmatch(r'L\d+', url.fragment):
                assert int(url.fragment[1:]) <= len(path.read_text(encoding='utf-8-sig').splitlines()), target
            checked += 1
    if args.assets:
        for row in manifest['release_assets']:
            assert Path(row['asset_name']).name == row['asset_name']
            path = args.assets/row['asset_name']
            assert path.stat().st_size == row['bytes'], row['asset_name']
            assert digest(path) == row['sha256'], row['asset_name']
    print(f"PASS: {len(paths)} snapshots; {checked} local document links; "
          f"{len(manifest['release_assets']) if args.assets else 0} downloaded arrays verified.")
    print(f"Preserved historical malformed JSON: {len(malformed)} (listed in manifest).")


if __name__ == '__main__':
    main()
