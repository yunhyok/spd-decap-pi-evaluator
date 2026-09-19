"""``spd_pi_engine`` solve worker: one rail, one build, N decap configurations.

Started as a subprocess by :mod:`spd_decap_pi._core.solver.engine_adapter`
(``python -m spd_decap_pi._core.solver.engine_worker <request.json>``, or
``<app>.exe --engine-worker <request.json>`` in the frozen build).  It writes
``<request.json>.result.json`` and reports ``PROGRESS <pct> <message>`` lines on
stdout.  The BLAS thread count is already pinned in this process's environment
by the adapter, so nothing here touches it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from .engine_adapter import (
    ENGINE_MESH,
    ENGINE_RAIL_TOO_LARGE,
    ENGINE_SPD_MISSING,
    ENGINE_SPD_SHA256_MISMATCH,
    EngineSolveRequest,
)


def _progress(value: int, message: str) -> None:
    print(f"PROGRESS {int(value)} {message}", flush=True)


def _library_versions() -> dict[str, str]:
    """Numerics-relevant library versions (plan §5; not part of numerics_id)."""

    import matplotlib
    import numpy
    import PIL
    import scipy

    return {
        "matplotlib": matplotlib.__version__,
        "numpy": numpy.__version__,
        "Pillow": PIL.__version__,
        "scipy": scipy.__version__,
    }


def _memory_guard(rail, solver: str) -> None:
    """Refuse a rail this box cannot hold before the build allocates anything.

    ponytail: the only pre-build size signal is the extraction stats, so the
    rail's node count is used as an unknowns floor together with the P18-class
    default.  It catches "this box has 2 GB free", not a 10 % misestimate; wire
    a real unknowns predictor in if a rail ever surprises us.
    """

    from spd_pi_engine import HardwareProfile
    from spd_pi_engine.hardware import (
        DEFAULT_UNKNOWNS,
        rss_per_process_MB,
        vram_per_process_MB,
    )

    profile = HardwareProfile.detect()
    unknowns = max(int(rail.estimate_cost()["n_rail_nodes"]), DEFAULT_UNKNOWNS)
    need_mb = rss_per_process_MB(unknowns)
    have_mb = profile.ram_free_GB * 1024.0
    if need_mb > have_mb:
        raise SystemExit(
            f"{ENGINE_RAIL_TOO_LARGE}: rail needs about {need_mb:,.0f} MB of RAM "
            f"but only {have_mb:,.0f} MB is free"
        )
    gpu = profile.gpu
    if solver in ("cudss", "auto") and gpu is not None:
        vram_mb = vram_per_process_MB(unknowns)
        if vram_mb > gpu["vram_free_MB"]:
            _progress(
                12,
                f"GPU has {gpu['vram_free_MB']:,.0f} MB free but this rail needs about "
                f"{vram_mb:,.0f} MB; the engine falls back to the CPU solver",
            )


def run(request: EngineSolveRequest) -> dict:
    from spd_pi_engine import Backend, Design, FLAGS_P, ModelOptions

    spd = Path(request.spd_path)
    if not spd.is_file():
        raise SystemExit(f"{ENGINE_SPD_MISSING}: {spd}")
    design = Design.open(spd)
    if design.sha256.lower() != request.spd_sha256.lower():
        raise SystemExit(
            f"{ENGINE_SPD_SHA256_MISMATCH}: {spd} is sha256 {design.sha256}, "
            f"the scenario was imported from {request.spd_sha256}"
        )

    _progress(5, f"Extracting {request.port} from the SPD")
    rail = design.rail(request.port, request.cache_dir)
    _memory_guard(rail, request.solver)

    _progress(20, f"Building the plane-pair model for {rail.rail_net}")
    options = ModelOptions(
        reference=request.reference_mode, flags=FLAGS_P, **ENGINE_MESH
    )
    model = rail.build(options, Backend(request.solver, fast=True))
    for model_id, text in request.extra_models.items():
        if model_id not in model.ex["models"]:
            model.add_decap_model(model_id, text)

    freqs = np.asarray(request.freqs, dtype=float)
    receipts: dict[str, dict] = {}
    configs = request.configs or {"as_built": {}}
    for index, (role, config) in enumerate(configs.items()):
        _progress(
            40 + round(55 * index / max(len(configs), 1)),
            f"Solving {role} at {freqs.size} frequencies",
        )
        model.set_decaps(dict(config or {}), replace=True)
        receipts[role] = model.solve(freqs, verbose=False).receipt()
    _progress(99, "Writing the engine receipt")
    return {"receipts": receipts, "libraries": _library_versions()}


def main(argv: list[str]) -> int:
    request_path = Path(argv[0])
    request = EngineSolveRequest.from_json(request_path.read_text(encoding="utf-8"))
    payload = run(request)
    out = request_path.with_name(request_path.name + ".result.json")
    out.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=float),
        encoding="utf-8",
    )
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
