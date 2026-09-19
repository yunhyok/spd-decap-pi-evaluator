"""Adapter from the product Evaluation entry points to ``spd_pi_engine`` (W11-a).

The engine package is never imported at module scope: it pulls in matplotlib,
Pillow and the SPD parser, and importing it eagerly would both slow the GUI
start-up and create an import cycle (the engine imports ``spd_decap_pi``).  The
same deferred-import convention the layerwise bridge uses
(``_core/services.py`` ``build_layerwise_uniform_source_model``) applies here.

Every engine solve runs in a **subprocess** (``engine_worker.py``): cuDSS
handles accumulate in a long-lived process, ``Model.solve`` has no cancellation
hook, and the BLAS thread count has to be set in the worker's environment
without touching the caller's (``docs/engine/W12A_REPORT.md`` §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .metrics import (
    ConfidenceAssessment,
    ConfidenceCategory,
    ConfidenceLevel,
    TargetMask,
    compute_evaluation_metrics,
)
from .modal import ModalSolveResult, SolverDiagnostics
from .profiles import (
    ENGINE_PROFILES,
    ENGINE_REFERENCE_MODE,
    SolverProfile,
    solver_profile,
    solver_profile_static_identity_sha256,
)

#: ``ScenarioDecap.pad_state`` value that removes a footprint from the rail.
_ISOLATION_GAP = "ISOLATION_GAP"

#: Blocker/error codes surfaced to the user (plan §1-3, §4).
ENGINE_SPD_MISSING = "ENGINE_SPD_MISSING"
ENGINE_SPD_SHA256_MISMATCH = "ENGINE_SPD_SHA256_MISMATCH"
ENGINE_RAIL_PORT_NOT_UNIQUE = "ENGINE_RAIL_PORT_NOT_UNIQUE"
ENGINE_CROSS_NET_DECAP_ASSIGNMENT = "ENGINE_CROSS_NET_DECAP_ASSIGNMENT"
ENGINE_RAIL_TOO_LARGE = "ENGINE_RAIL_TOO_LARGE"

#: ``ModelOptions`` of the frozen exp28/p baseline.  This must stay character
#: for character identical to ``tests/engine/test_reproduction.py`` ``OPT``.
ENGINE_MESH = {"h": 200.0, "fh": 50.0, "top_h": 50.0, "sub": (20, 10, 10), "fringe": True}

#: How often :func:`solve` looks at ``is_cancelled`` while the worker runs.
_CANCEL_POLL_S = 0.2


class EngineSolveError(RuntimeError):
    """The engine worker refused or failed one rail."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


def engine_cache_dir() -> Path:
    """Extraction cache directory (``SPD_PI_ENGINE_CACHE`` or the app data dir).

    Nothing is created here: the worker makes the directory the first time it
    writes, so importing this module never touches the filesystem.
    """

    configured = os.environ.get("SPD_PI_ENGINE_CACHE", "").strip()
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if not base:
        base = str(Path.home() / ".cache")
    return Path(base) / "SPD Decap PI Evaluator" / "engine-cache"


def engine_receipt_dir() -> Path:
    """Where the verbatim engine receipts are kept (never overwritten)."""

    return engine_cache_dir().parent / "outputs" / "engine-receipts"


def engine_frequencies() -> np.ndarray:
    """The engine's own frequency ladder; the product 401-point sweep is unused."""

    from spd_pi_engine import ladder_freqs

    return ladder_freqs()


def freq_grid_sha256(freqs: Sequence[float]) -> str:
    return _sha256_json([float(value) for value in freqs])


def engine_port_for_rail(
    spd_path: str | Path,
    rail_id: str,
    *,
    ports: Mapping[str, str] | None = None,
) -> str:
    """The one SPD ``.Port`` that drives ``rail_id`` (the SPD net of the rail).

    ``ports`` is the ``{port: rail_net}`` map ``spd_pi_engine.port_rails``
    returns; pass it when several rails are resolved against the same SPD so the
    file is memory-mapped once.  Zero or more than one match is an error: the
    engine models exactly one port per rail (plan §7).
    """

    if ports is None:
        from spd_pi_engine import port_rails

        ports = port_rails(spd_path)
    key = str(rail_id).strip().casefold()
    matching = sorted(port for port, net in ports.items() if str(net).casefold() == key)
    if len(matching) != 1:
        raise EngineSolveError(
            ENGINE_RAIL_PORT_NOT_UNIQUE,
            f"rail {rail_id!r} is driven by {len(matching)} SPD port(s) "
            f"({', '.join(matching) or 'none'}); the engine models exactly one",
        )
    return matching[0]


def decap_config(scenario: Any, rail_id: str) -> dict[str, str | None]:
    """``{refdes: model_id | None}`` for one rail, in the engine's spelling.

    A decap that the user moved *off* this rail is explicitly unmounted rather
    than dropped: the engine's decap set is fixed by the SPD ``.Connect`` net, so
    "no longer on this rail" can only be expressed as ``None``.  A decap moved
    *onto* the rail from another net cannot be expressed at all and is rejected
    by :func:`cross_net_decap_refdes` before any solve starts.
    """

    key = str(rail_id).strip().casefold()
    config: dict[str, str | None] = {}
    for decap in scenario.decaps:
        current = str(decap.current_rail_id).casefold()
        source = str(decap.source_rail_id).casefold()
        if current == key:
            mounted = bool(decap.enabled) and str(decap.pad_state) != _ISOLATION_GAP
            config[decap.refdes] = decap.model_id if mounted else None
        elif source == key:
            config[decap.refdes] = None
    return config


def cross_net_decap_refdes(scenario: Any, rail_id: str) -> tuple[str, ...]:
    """Refdes assigned *into* ``rail_id`` from another source rail (unmodelable)."""

    key = str(rail_id).strip().casefold()
    return tuple(
        decap.refdes
        for decap in scenario.decaps
        if str(decap.current_rail_id).casefold() == key
        and str(decap.source_rail_id).casefold() != key
    )


def extra_models(
    scenario: Any,
    attachments: Mapping[str, bytes] | None,
    config: Mapping[str, str | None],
) -> dict[str, str]:
    """``{model_id: .SUBCKT text}`` for models the SPD itself does not carry.

    The texts come from the scenario's own cap library
    (``ProjectSpec.metadata["cap_model_sources"]`` -> ``attachments``), which is
    where the product stores a user-imported SPICE model.  Models that came from
    the SPD are already registered inside the engine and are not repeated here.
    """

    wanted = {str(value) for value in config.values() if value}
    if not wanted:
        return {}
    sources = scenario.base_project.metadata.get("cap_model_sources")
    if not isinstance(sources, Mapping):
        return {}
    payloads = dict(attachments or {})
    texts: dict[str, str] = {}
    for model_id, source in sources.items():
        if str(model_id) not in wanted or not isinstance(source, Mapping):
            continue
        asset = source.get("source_asset")
        raw = payloads.get(asset) if isinstance(asset, str) else None
        if raw is None:
            continue
        # ponytail: the whole .lib text is handed over, and the engine's
        # add_decap_model parses the single .SUBCKT in it.  A multi-subckt
        # library needs source["subckt_name"] to be honoured -- split the block
        # here if one ever shows up (the worker fails loudly on ambiguity).
        texts[str(model_id)] = bytes(raw).decode("utf-8-sig")
    return texts


@dataclass(frozen=True, slots=True)
class EngineSolveRequest:
    """One worker invocation: one rail, one build, N decap configurations."""

    spd_path: str
    spd_sha256: str
    port: str
    reference_mode: str
    freqs: tuple[float, ...]
    cache_dir: str
    solver: str = "auto"
    #: ``{role: {refdes: model_id | None}}`` -- solved in order, one build.
    configs: dict[str, dict[str, str | None]] = field(default_factory=dict)
    extra_models: dict[str, str] = field(default_factory=dict)
    #: BLAS threads for the worker process.  ``None`` = ``hardware.plan_threads``,
    #: ``0`` = inherit the caller's environment (what a bit-for-bit rerun wants,
    #: ``spd_pi_engine`` README §10-4).
    threads: int | None = None

    def to_json(self) -> str:
        payload = {
            "spd_path": self.spd_path,
            "spd_sha256": self.spd_sha256,
            "port": self.port,
            "reference_mode": self.reference_mode,
            "freqs": [float(value) for value in self.freqs],
            "cache_dir": self.cache_dir,
            "solver": self.solver,
            "configs": {
                role: dict(config) for role, config in self.configs.items()
            },
            "extra_models": dict(self.extra_models),
            "threads": self.threads,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "EngineSolveRequest":
        payload = json.loads(text)
        return cls(
            spd_path=str(payload["spd_path"]),
            spd_sha256=str(payload["spd_sha256"]),
            port=str(payload["port"]),
            reference_mode=str(payload["reference_mode"]),
            freqs=tuple(float(value) for value in payload["freqs"]),
            cache_dir=str(payload["cache_dir"]),
            solver=str(payload.get("solver", "auto")),
            configs={
                str(role): {str(k): (None if v is None else str(v)) for k, v in config.items()}
                for role, config in (payload.get("configs") or {}).items()
            },
            extra_models={
                str(k): str(v) for k, v in (payload.get("extra_models") or {}).items()
            },
            threads=payload.get("threads"),
        )


@dataclass(frozen=True, slots=True)
class EngineSolveResult:
    """One worker run: the receipts it produced and where they were kept."""

    receipts: dict[str, dict[str, Any]]
    receipt_paths: dict[str, str]
    receipt_sha256: dict[str, str]
    libraries: dict[str, str]


def _worker_argv(request_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        # The frozen build has no importable interpreter: re-enter the launcher.
        return [sys.executable, "--engine-worker", str(request_path)]
    return [
        sys.executable,
        "-m",
        "spd_decap_pi._core.solver.engine_worker",
        str(request_path),
    ]


def _worker_env(request: EngineSolveRequest) -> dict[str, str]:
    """Thread pinning goes in the *worker's* environment, never the caller's."""

    env = dict(os.environ)
    threads = request.threads
    if threads is None:
        from spd_pi_engine import HardwareProfile, plan_threads

        threads = plan_threads(HardwareProfile.detect(), 1).threads
    if threads:
        env.update(
            {
                name: str(int(threads))
                for name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS")
            }
        )
    return env


def solve(
    request: EngineSolveRequest,
    *,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> EngineSolveResult:
    """Run one rail in the engine worker and keep its receipts."""

    from spd_pi_engine import unique_path

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    receipt_dir = engine_receipt_dir()
    receipt_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="engine-solve-", dir=str(receipt_dir)))
    request_path = work / "request.json"
    request_path.write_text(request.to_json(), encoding="utf-8")
    result_path = work / "request.json.result.json"

    process = subprocess.Popen(
        _worker_argv(request_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_worker_env(request),
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    # Cancellation must not wait for the worker's next stdout line: the solve
    # phase can run for minutes without printing.  A reader thread drains the
    # pipe (which must stay drained anyway) while this loop polls `cancelled()`
    # on a timer and kills the process.
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def _drain(stream: Any) -> None:
        try:
            for raw in stream:
                lines.put(raw.rstrip())
        finally:
            lines.put(None)

    threading.Thread(target=_drain, args=(process.stdout,), daemon=True).start()
    tail: list[str] = []
    while True:
        if cancelled():
            process.kill()
            process.wait()
            raise RuntimeError("evaluation cancelled")
        try:
            line = lines.get(timeout=_CANCEL_POLL_S)
        except queue.Empty:
            continue
        if line is None:
            break
        tail.append(line)
        del tail[:-40]
        if line.startswith("PROGRESS "):
            _, _, rest = line.partition(" ")
            value, _, message = rest.partition(" ")
            try:
                report(int(float(value)), message)
            except ValueError:
                pass
    returncode = process.wait()
    if returncode != 0 or not result_path.is_file():
        detail = "\n".join(tail[-12:]) or f"exit code {returncode}"
        code = next(
            (
                known
                for known in (
                    ENGINE_SPD_MISSING,
                    ENGINE_SPD_SHA256_MISMATCH,
                    ENGINE_RAIL_TOO_LARGE,
                )
                if any(known in item for item in tail)
            ),
            "ENGINE_WORKER_FAILED",
        )
        raise EngineSolveError(code, f"engine worker failed: {detail}")

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    receipts: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {}
    digests: dict[str, str] = {}
    for role, receipt in payload["receipts"].items():
        # write_bytes, not write_text: on Windows the text writer would turn the
        # JSON's "\n" into "\r\n" and the kept file would no longer hash to the
        # `receipt_sha256` this result reports.
        blob = json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, indent=1
        ).encode("utf-8")
        kept = unique_path(
            receipt_dir / f"receipt_{request.port}_{role}_{receipt['numerics_id'][:12]}.json"
        )
        kept.write_bytes(blob)
        receipts[role] = receipt
        paths[role] = str(kept)
        digests[role] = sha256(blob).hexdigest()
    for item in (result_path, request_path):
        item.unlink(missing_ok=True)
    work.rmdir()
    return EngineSolveResult(
        receipts=receipts,
        receipt_paths=paths,
        receipt_sha256=digests,
        libraries=dict(payload.get("libraries") or {}),
    )


def solve_request(
    *,
    spd_path: str | Path,
    spd_sha256: str,
    port: str,
    profile: SolverProfile | str,
    configs: Mapping[str, Mapping[str, str | None]],
    extra_models: Mapping[str, str] | None = None,
    freqs: Sequence[float] | None = None,
    cache_dir: str | Path | None = None,
    solver: str = "auto",
    threads: int | None = None,
) -> EngineSolveRequest:
    """Build the frozen request for one engine profile."""

    resolved = solver_profile(profile)
    if resolved not in ENGINE_PROFILES:
        raise EngineSolveError(
            "ENGINE_PROFILE_UNKNOWN", f"{resolved.key} is not an engine profile"
        )
    grid = engine_frequencies() if freqs is None else np.asarray(freqs, dtype=float)
    return EngineSolveRequest(
        spd_path=str(spd_path),
        spd_sha256=str(spd_sha256).lower(),
        port=str(port),
        reference_mode=ENGINE_REFERENCE_MODE[resolved.key],
        freqs=tuple(float(value) for value in grid),
        cache_dir=str(cache_dir if cache_dir is not None else engine_cache_dir()),
        solver=solver,
        configs={
            str(role): {str(k): (None if v is None else str(v)) for k, v in config.items()}
            for role, config in configs.items()
        },
        extra_models=dict(extra_models or {}),
        threads=threads,
    )


def _sha256_json(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256((text + "\n").encode("utf-8")).hexdigest()


def engine_confidence(
    freqs: Sequence[float], notes: Sequence[str]
) -> tuple[ConfidenceAssessment, ...]:
    """Three fixed disclosure bands (plan §1-2); the engine is never HIGH.

    Below 100 kHz the package feed-path R convention against PowerSI is still
    unresolved, inside 100 kHz - 100 MHz the validity notes give the measured
    spread, and above 100 MHz nothing was validated at all.
    """

    start = float(min(freqs)) if len(freqs) else 1.0e3
    return (
        ConfidenceAssessment(
            category=ConfidenceCategory.MODEL_COVERAGE,
            start_hz=min(start, 1.0e5),
            stop_hz=1.0e5,
            level=ConfidenceLevel.LOW,
            reason=(
                "below 100 kHz the package feed-path R convention against PowerSI "
                "is unresolved (Re Z_ref/Re Z_model median 1.46 @100 kHz)"
            ),
        ),
        ConfidenceAssessment(
            category=ConfidenceCategory.MODEL_COVERAGE,
            start_hz=1.0e5,
            stop_hz=1.0e8,
            level=ConfidenceLevel.LOW,
            reason="engine validated band; " + " ".join(notes[:2]),
        ),
        ConfidenceAssessment(
            category=ConfidenceCategory.MODEL_COVERAGE,
            start_hz=1.0e8,
            stop_hz=1.0e9,
            level=ConfidenceLevel.LOW,
            reason=(
                "above 100 MHz the engine ladder stops and nothing was validated "
                "(outside the verified range)"
            ),
        ),
    )


def evaluation_outcome(
    receipt: Mapping[str, Any],
    *,
    rail_id: str,
    profile: SolverProfile | str,
    target: TargetMask,
    critical_band_hz: tuple[float, float],
    receipt_sha256: str,
    libraries: Mapping[str, str] | None = None,
):
    """Assemble the product ``EvaluationOutcome`` from one engine receipt."""

    from .evaluator import EvaluationOutcome

    resolved = solver_profile(profile)
    freqs = np.asarray(receipt["freq"], dtype=float)
    impedance = np.asarray(receipt["Z_re"], dtype=float) + 1j * np.asarray(
        receipt["Z_im"], dtype=float
    )
    # The engine is a direct sparse LU, not a modal expansion: there is no mode
    # count, and it does not measure a per-frequency condition number or
    # residual.  The real size/identity numbers live in `solver_provenance`.
    solve_result = ModalSolveResult(
        frequencies_hz=freqs,
        impedance_ohm=impedance,
        diagnostics=SolverDiagnostics(
            condition_numbers=np.zeros(freqs.shape, dtype=float),
            relative_residuals=np.zeros(freqs.shape, dtype=float),
            mode_count=0,
        ),
    )
    metrics = compute_evaluation_metrics(
        freqs, impedance, target, critical_band_hz=critical_band_hz
    )
    notes = tuple(str(item) for item in receipt["validity"]["notes"])
    numerics_id = str(receipt["numerics_id"])
    provenance = {
        "profile_key": resolved.key,
        "profile_badge": resolved.badge,
        "source_only": True,
        "powersi_used_for_parameters": False,
        "compiler_algorithm_id": resolved.compiler_algorithm_id,
        "solver_static_identity_sha256": solver_profile_static_identity_sha256(resolved),
        "engine_version": str(receipt["engine_version"]),
        "numerics_id": numerics_id,
        "reference_mode": str(receipt["reference_mode"]),
        "port": str(receipt["port"]),
        "rail_net": str(receipt["rail"]),
        "receipt_sha256": receipt_sha256,
        "spd_sha256": str(receipt["spd_sha256"]),
        "decap_config_sha256": str(receipt["decap_config_sha256"]),
        "freq_grid_sha256": freq_grid_sha256(freqs),
        "prune_basis": str(receipt["prune_basis"]),
        "unknowns": int(receipt["unknowns"]),
        "backend": dict(receipt["backend"]),
        "validity_notes": list(notes),
        "library_versions": dict(libraries or {}),
    }
    return EvaluationOutcome(
        rail_id=rail_id,
        solve=solve_result,
        metrics=metrics,
        confidence=engine_confidence(freqs, notes),
        assumptions=notes,
        solver_version=f"spd-pi-engine-{receipt['engine_version']}-{numerics_id[:12]}",
        solver_profile_key=resolved.key,
        solver_provenance=provenance,
    )


__all__ = [
    "ENGINE_CROSS_NET_DECAP_ASSIGNMENT",
    "ENGINE_MESH",
    "ENGINE_RAIL_PORT_NOT_UNIQUE",
    "ENGINE_RAIL_TOO_LARGE",
    "ENGINE_SPD_MISSING",
    "ENGINE_SPD_SHA256_MISMATCH",
    "EngineSolveError",
    "EngineSolveRequest",
    "EngineSolveResult",
    "cross_net_decap_refdes",
    "decap_config",
    "engine_cache_dir",
    "engine_confidence",
    "engine_frequencies",
    "engine_port_for_rail",
    "engine_receipt_dir",
    "evaluation_outcome",
    "extra_models",
    "freq_grid_sha256",
    "solve",
    "solve_request",
]
