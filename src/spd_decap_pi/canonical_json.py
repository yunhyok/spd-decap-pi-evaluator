"""Streaming canonical JSON primitives shared by persistence and evidence code.

The project evidence format is the UTF-8 encoding produced by ``json.dumps``
with sorted object keys, no insignificant whitespace, ``ensure_ascii=False``
and non-finite numbers disabled.  Python's stock encoder only treats concrete
``dict``/``list`` containers as JSON objects/arrays.  Persisted topology views
are deliberately exposed through lazy read-only ``Mapping``/``Sequence``
wrappers, so converting those wrappers back to concrete containers would
duplicate hundreds of MiB of topology data.

This module preserves the exact wire format while yielding bounded chunks for
arbitrary JSON-compatible Mapping/Sequence implementations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
from typing import Any


_SCALAR_ENCODER = json.JSONEncoder(
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
)


def iter_canonical_json_bytes(value: Any) -> Iterable[bytes]:
    """Yield canonical UTF-8 JSON without materialising the complete payload.

    Object keys are required to be strings.  That is the JSON data-model
    contract used by all project metadata and prevents ambiguous coercions
    that the standard encoder otherwise permits for a few Python-only key
    types.
    """

    active: set[int] = set()

    def emit(item: Any) -> Iterable[bytes]:
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                raise ValueError("circular reference in canonical JSON mapping")
            active.add(identity)
            try:
                keys = list(item)
                if any(not isinstance(key, str) for key in keys):
                    raise TypeError("canonical JSON object keys must be strings")
                keys.sort()
                yield b"{"
                for ordinal, key in enumerate(keys):
                    if ordinal:
                        yield b","
                    for chunk in _SCALAR_ENCODER.iterencode(key):
                        yield chunk.encode("utf-8")
                    yield b":"
                    yield from emit(item[key])
                yield b"}"
            finally:
                active.remove(identity)
            return

        if isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray, memoryview)
        ):
            identity = id(item)
            if identity in active:
                raise ValueError("circular reference in canonical JSON sequence")
            active.add(identity)
            try:
                yield b"["
                for ordinal, child in enumerate(item):
                    if ordinal:
                        yield b","
                    yield from emit(child)
                yield b"]"
            finally:
                active.remove(identity)
            return

        for chunk in _SCALAR_ENCODER.iterencode(item):
            yield chunk.encode("utf-8")

    yield from emit(value)


def iter_concrete_canonical_json_bytes(value: Any) -> Iterable[bytes]:
    """Yield canonical JSON quickly for a concrete ``dict``/``list`` tree.

    ``iter_canonical_json_bytes`` deliberately supports arbitrary lazy
    ``Mapping``/``Sequence`` implementations.  That flexibility requires a
    Python-level recursive visit and is costly for the multi-GiB inline SPD
    certificate built during a fresh import.  The import compiler owns a
    concrete tree containing only normal JSON ``dict``/``list`` containers,
    so the standard encoder can stream the exact same wire bytes with much
    lower dispatch overhead.  Callers must not use this helper for lazy views.
    """

    for chunk in _SCALAR_ENCODER.iterencode(value):
        yield chunk.encode("utf-8")


def concrete_canonical_json_bytes(value: Any) -> bytes:
    """Return canonical bytes for an explicitly concrete JSON tree.

    Unlike :func:`canonical_json_bytes`, this deliberately does not support
    lazy ``Mapping``/``Sequence`` wrappers.  It is intended for bounded rows
    whose complete container tree is known to consist of normal ``dict`` and
    ``list`` instances.
    """

    return _SCALAR_ENCODER.encode(value).encode("utf-8")


def canonical_json_bytes(value: Any) -> bytes:
    """Return canonical JSON bytes; use only where a contiguous blob is needed."""

    return b"".join(iter_canonical_json_bytes(value))


__all__ = [
    "canonical_json_bytes",
    "concrete_canonical_json_bytes",
    "iter_canonical_json_bytes",
    "iter_concrete_canonical_json_bytes",
]
