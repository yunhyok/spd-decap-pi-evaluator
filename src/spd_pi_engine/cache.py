"""Cache v2 for extracts and layer shapes (plan C14/C15, risk 5-1/5-4).

What changes against the research cache (`exp5/pipeline.py:65-108`):

* the file name is a key, not a design id: `(spd sha256, port, max_layers, cache_format,
  parser_version)` for an extract, `(spd sha256, layer, cache_format, parser_version)` for a
  layer's shapes -- so the shapes are shared by every port of a design and eight concurrent
  `prepare()` calls never write the same file (the research cache rewrote one whole
  `shapes_{tag}.pkl` per port);
* **no product object is pickled** (C15): the pydantic `StackupLayer` rows are stored as plain
  dicts and the decap models as their `.SUBCKT` source text, re-parsed on load by the product's
  own `parse_passive_subcircuit` -- so a product release cannot make an old cache unreadable;
* every entry carries a header `{cache_format, engine_version, numpy_version, parser_version,
  spd_sha256}` and a mismatch (or a truncated/corrupt file) is silently a cache miss;
* writes are tmp + `os.replace` with the Windows PermissionError retry of `pipeline.py:65-80`.

Nothing here touches the filesystem at import time (C12).
"""
from __future__ import annotations

import functools
import hashlib
import os
import pickle
import re
import time
from pathlib import Path

import numpy as np

from spd_decap_pi._core.domain import StackupLayer
from spd_decap_pi._core.models.spice import parse_passive_subcircuit

CACHE_FORMAT = 2


@functools.lru_cache(maxsize=1)
def parser_version() -> tuple:
    """(product core version, sha256/12 of the SPD parser, sha256/12 of the SPICE model parser).

    Keyed on the source so a parser change inside one release still invalidates the cache.
    """
    from spd_decap_pi._core import version as core_version
    from spd_decap_pi._core.io import spd as spd_io
    from spd_decap_pi._core.models import spice

    def h(mod):
        return hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()[:12]

    return (core_version.__version__, h(spd_io), h(spice))


def _engine_version() -> str:
    import spd_pi_engine

    return spd_pi_engine.__version__


def header(spd_sha256: str) -> dict:
    return dict(cache_format=CACHE_FORMAT, engine_version=_engine_version(),
                numpy_version=np.__version__, parser_version=parser_version(), spd_sha256=spd_sha256)


def atomic_pickle_dump(obj, fn) -> None:
    """tmp + os.replace, with the retry of `exp5/pipeline._atomic_pickle_dump`.

    Same-volume os.replace is atomic on Windows, so concurrent readers never observe a
    partially-written file.  os.replace itself can raise PermissionError if another process has
    fn open for read right now (no FILE_SHARE_DELETE) -> retry briefly instead of crashing.
    """
    fn = str(fn)
    tmp = fn + f".tmp{os.getpid()}"
    with open(tmp, "wb") as f:
        pickle.dump(obj, f)
    for attempt in range(100):
        try:
            os.replace(tmp, fn)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(text))[:n]


def _key(*parts) -> str:
    return hashlib.sha256("|".join(repr(p) for p in parts).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ extract v2 payload
def encode_extract(ex: dict) -> dict:
    """The extract without product objects: `stackup_layers_obj` -> dicts, `models` -> dropped
    (their `.SUBCKT` text is already in `model_texts`)."""
    out = {k: v for k, v in ex.items() if k not in ("stackup_layers_obj", "models")}
    out["stackup_layers"] = [L.model_dump() for L in ex["stackup_layers_obj"]]
    return out


def decode_extract(payload: dict) -> dict:
    """Rebuild the product objects: `StackupLayer(**d)` (via_model reads .name/.is_conductor/
    .thickness_um) and `parse_passive_subcircuit(text)` (the model_id and source_hash come from
    the text, so the object is the one extract built)."""
    ex = {k: v for k, v in payload.items() if k != "stackup_layers"}
    ex["stackup_layers_obj"] = [StackupLayer(**d) for d in payload["stackup_layers"]]
    models = {}
    for mid, text in payload["model_texts"].items():
        m = parse_passive_subcircuit(text, source_name=f"cache:{mid}")
        if m.model_id != mid:
            raise ValueError(f"cached .SUBCKT text for {mid} re-parsed as {m.model_id}")
        models[mid] = m
    ex["models"] = models
    return ex


class CacheDir:
    """Extract + shapes cache under one directory.  The directory is created on first write."""

    def __init__(self, root):
        self.root = Path(root)

    # -------------------------------------------------- paths
    def extract_path(self, spd_sha256: str, port: str, max_layers: int) -> Path:
        k = _key(spd_sha256, port, max_layers, CACHE_FORMAT, parser_version())
        return self.root / f"extract_{_slug(port)}_{k}.pkl"

    def shapes_path(self, spd_sha256: str, layer: str) -> Path:
        k = _key(spd_sha256, layer, CACHE_FORMAT, parser_version())
        return self.root / f"shapes_{_slug(layer)}_{k}.pkl"

    # -------------------------------------------------- io
    def _read(self, path: Path, spd_sha256: str):
        """(payload) or None -- a missing, unreadable or stale entry is a miss, silently."""
        try:
            with open(path, "rb") as fh:
                head, payload = pickle.load(fh)
        except (FileNotFoundError, EOFError, pickle.UnpicklingError, ValueError, TypeError,
                AttributeError, ModuleNotFoundError, PermissionError):
            return None
        if head != header(spd_sha256):
            return None
        return payload

    def _write(self, path: Path, spd_sha256: str, payload) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_pickle_dump((header(spd_sha256), payload), path)

    # -------------------------------------------------- entries
    def load_extract(self, spd_sha256: str, port: str, max_layers: int):
        payload = self._read(self.extract_path(spd_sha256, port, max_layers), spd_sha256)
        return None if payload is None else decode_extract(payload)

    def save_extract(self, spd_sha256: str, port: str, max_layers: int, ex: dict) -> Path:
        path = self.extract_path(spd_sha256, port, max_layers)
        self._write(path, spd_sha256, encode_extract(ex))
        return path

    def load_shapes(self, spd_sha256: str, layer: str):
        return self._read(self.shapes_path(spd_sha256, layer), spd_sha256)

    def save_shapes(self, spd_sha256: str, layer: str, geoms: dict) -> Path:
        path = self.shapes_path(spd_sha256, layer)
        self._write(path, spd_sha256, geoms)
        return path


def demo(tmp=None) -> None:
    """Self-check without SPD data: header mismatch, corrupt file and round-trip of a
    StackupLayer + a .SUBCKT text are all handled."""
    import tempfile

    text = ".SUBCKT DEMO_CAP 1 2\nC1 1 a 100n\nR1 a 2 5m\nL1 a 2 1n\n.ENDS\n"
    m = parse_passive_subcircuit(text, source_name="demo")
    ex = dict(spd_path="x.spd", port_name="Port1", rail_nodes={"Node1": (1.0, 2.0, "L1", None)},
              stackup_layers_obj=[StackupLayer(name="L1", thickness_um=15.0, conductivity_s_m=5.8e7),
                                  StackupLayer(name="D1", thickness_um=30.0, dk=3.4, df=0.01)],
              models={m.model_id: m}, model_texts={m.model_id: text},
              gnd_xy_by_layer={"L1": np.arange(4.0).reshape(2, 2)})
    with tempfile.TemporaryDirectory() as d:
        c = CacheDir(Path(d) / "sub")
        assert c.load_extract("sha", "Port1", 3) is None, "empty cache must miss"
        p = c.save_extract("sha", "Port1", 3, ex)
        got = c.load_extract("sha", "Port1", 3)
        assert got is not None
        assert got["stackup_layers_obj"] == ex["stackup_layers_obj"], "StackupLayer round-trip"
        assert got["models"] == ex["models"], "PassiveSubcircuitModel round-trip"
        assert np.array_equal(got["gnd_xy_by_layer"]["L1"], ex["gnd_xy_by_layer"]["L1"])
        assert set(got) == set(ex), "key set must survive the round-trip"
        assert b"spd_decap_pi" not in p.read_bytes(), "cache v2 must not pickle product objects"
        assert c.load_extract("other-sha", "Port1", 3) is None, "sha mismatch must regenerate"
        p.write_bytes(p.read_bytes()[:20])
        assert c.load_extract("sha", "Port1", 3) is None, "truncated file must regenerate"
        c.save_shapes("sha", "Signal$L14(MAIN_POWER4)", {"DGND": {"order": []}})
        assert c.load_shapes("sha", "Signal$L14(MAIN_POWER4)") == {"DGND": {"order": []}}
    print("DEMO PASS (cache v2: round-trip, no product objects in the pickle, stale -> miss)")


if __name__ == "__main__":
    demo()
