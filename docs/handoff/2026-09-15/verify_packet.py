"""Verify published handoff bytes using only the Python standard library."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
for item in manifest["files"]:
    path = (root / item["path"]).resolve()
    assert path.is_relative_to(root), item["path"]
    data = path.read_bytes()
    assert len(data) == item["bytes"], item["path"]
    assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
handoff = root.parents[2] / "handoff.md"
assert hashlib.sha256(handoff.read_text(encoding="utf-8").encode("utf-8")).hexdigest() == manifest["published_handoff_sha256"]
print(f"PASS: {len(manifest['files'])} snapshots and handoff.md")
