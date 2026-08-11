from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any


SCHEMA = "spd-decap-r4-numerical-source-manifest-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_PATH_CHARACTERS = frozenset("*?[\\")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _git_blob(repo: Path, revision: str, path: str) -> bytes:
    result = subprocess.run(
        ("git", "cat-file", "blob", f"{revision}:{path}"),
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"missing Git blob {revision}:{path}: {detail}")
    return result.stdout


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _load_manifest(payload: bytes) -> dict[str, Any]:
    value = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON token: {token}")
        ),
    )
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("R4 numerical-source manifest schema is invalid")
    if payload != _canonical_bytes(value):
        raise ValueError("R4 numerical-source manifest is not canonical JSON")
    return value


def _validated_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("manifest path must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"manifest path contains a control character: {value!r}")
    if any(character in FORBIDDEN_PATH_CHARACTERS for character in value):
        raise ValueError(f"manifest path contains an attribute metacharacter: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or ".." in path.parts:
        raise ValueError(f"manifest path is not canonical and relative: {value}")
    return value


def _require_unset_text_attribute(repo: Path, paths: tuple[str, ...]) -> None:
    result = subprocess.run(
        ("git", "check-attr", "--cached", "text", "--", *paths),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ValueError(f"git check-attr failed: {result.stderr.strip()}")
    rows = tuple(line for line in result.stdout.splitlines() if line)
    if len(rows) != len(paths):
        raise ValueError("Git text-attribute inventory is incomplete")
    for path, row in zip(paths, rows, strict=True):
        if row != f"{path}: text: unset":
            raise ValueError(f"Git text attribute is not unset for {path}: {row}")


def verify_git_snapshot(
    *,
    repo: Path,
    revision: str,
    manifest_path: str,
    expected_manifest_sha256: str,
) -> int:
    repo = repo.resolve()
    manifest_path = _validated_path(manifest_path)
    if not SHA256_RE.fullmatch(expected_manifest_sha256):
        raise ValueError("expected manifest SHA-256 is not canonical")
    manifest_payload = _git_blob(repo, revision, manifest_path)
    actual_manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise ValueError("R4 numerical-source manifest Git blob identity changed")
    manifest = _load_manifest(manifest_payload)
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("R4 numerical-source file inventory is invalid")
    paths: list[str] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("R4 numerical-source file entry is invalid")
        path = _validated_path(raw_entry.get("path"))
        size_bytes = raw_entry.get("size_bytes")
        sha256 = raw_entry.get("sha256")
        if type(size_bytes) is not int or size_bytes < 0:
            raise ValueError(f"manifest size is invalid for {path}")
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise ValueError(f"manifest SHA-256 is invalid for {path}")
        payload = _git_blob(repo, revision, path)
        if len(payload) != size_bytes or hashlib.sha256(payload).hexdigest() != sha256:
            raise ValueError(f"Git blob differs from R4 source snapshot: {path}")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("R4 numerical-source paths are not unique and sorted")
    attribute_paths = tuple((*paths, manifest_path))
    _require_unset_text_attribute(repo, attribute_paths)
    return len(paths)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    count = verify_git_snapshot(
        repo=args.repo,
        revision=args.revision,
        manifest_path=args.manifest,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )
    print(
        "PASS: Git revision "
        f"{args.revision} contains {count} exact R4 source blobs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
