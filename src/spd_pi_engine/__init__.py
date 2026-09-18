"""SPD PI engine — the frozen research model (exp8→exp5→exp4→exp3→exp1, baseline exp28/p)
as a reusable package.  Numerics are not changed: every module here is a line-for-line port of
`tools/research-claude/`, with implicit state promoted to explicit arguments
(docs/engine/ENGINE_PLAN_2026-09-18.md).

W1 = the pure compute core: geometry (rasterisation, scanline), homogenise (window conductance),
solver (YPattern, cuDSS) and the Backend that replaced the environment flags.
W2 = reference (the per-cell plane reference search) and model (Sheet + the single Model that
Model3/Model4/ModelB became, driven by ModelOptions).
W3 = spd_source (port enumeration, extraction, prepare) and cache (cache v2).
W4 = api (Design -> Rail -> Model -> Result) and receipt (receipt v1, numerics_id, the G1-G5 gate
metrics of exp3/run3 + run11 as `attach_reference`, and the `validity` contract field).

    from spd_pi_engine import Design, ModelOptions, Backend, FLAGS_P
    rail = Design.open(spd).rail("Port18_SITE0", cache_dir)
    res = rail.build(ModelOptions(flags=FLAGS_P, fringe=True), Backend()).solve(freqs)
    rec = res.receipt()
"""
from __future__ import annotations

__version__ = "0.1.0.dev0"

from . import api, cache, geometry, homogenise, model, receipt, reference, solver, spd_source
from .api import DecapSite, Design, Rail
from .backend import DEFAULT, Backend
from .cache import CacheDir
from .model import FLAGS, FLAGS_LEGACY, FLAGS_P, FLAGS_PMK, FLAGS_Q, Model, ModelOptions, Sheet
from .receipt import (RECEIPT_VERSION, VALIDITY_NOTES, Result, attach_reference,
                      decap_config_sha256, ladder_gates, numerics_id)
from .reference import DesignConventions, ReferenceSearch
from .spd_source import (SPD_PARSER_SYMBOLS, check_parser_api, extract, list_ports,
                         load_layer_shapes, prepare, sha256_of)

__all__ = ["Backend", "CacheDir", "DEFAULT", "DecapSite", "Design", "DesignConventions", "FLAGS",
           "FLAGS_LEGACY", "FLAGS_P", "FLAGS_PMK", "FLAGS_Q", "Model", "ModelOptions",
           "RECEIPT_VERSION", "Rail", "ReferenceSearch", "Result", "SPD_PARSER_SYMBOLS", "Sheet",
           "VALIDITY_NOTES", "api", "attach_reference", "cache", "check_parser_api",
           "decap_config_sha256", "extract", "geometry", "homogenise", "ladder_gates", "list_ports",
           "load_layer_shapes", "model", "numerics_id", "prepare", "receipt", "reference",
           "sha256_of", "solver", "spd_source", "__version__"]
