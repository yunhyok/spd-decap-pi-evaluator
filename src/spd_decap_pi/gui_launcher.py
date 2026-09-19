"""PyInstaller-friendly entry point for the SPD Decap PI Evaluator GUI.

``--engine-worker <request.json>`` re-enters this same executable as the
``spd_pi_engine`` solve worker (``_core/solver/engine_worker.py``).  The frozen
build has no importable interpreter, so the branch has to live here -- and
before Qt is imported, because the worker must never build a QApplication.
"""

import os
import sys


def _bind_std_stream(stream, fd):
    """A PyInstaller ``--windowed`` build starts with ``sys.stdout``/``sys.stderr``
    as ``None`` (no console) unless the parent gave real stdio handles.  Rebind to
    the inherited OS file descriptor when it is valid, else fall back to
    ``os.devnull``, so a bare ``print()`` anywhere below this point can never raise
    ``AttributeError: 'NoneType' object has no attribute 'write'`` (W11-d follow-up:
    a --engine-worker run that hit that, or any other unhandled exception, could
    sit at ~0% CPU indefinitely under piped/unredirected stdio instead of exiting).
    """
    if stream is not None:
        return stream
    try:
        os.fstat(fd)  # raises OSError on an invalid/absent handle without side effects
        return os.fdopen(fd, "w", encoding="utf-8", errors="replace", closefd=False)
    except OSError:
        return open(os.devnull, "w", encoding="utf-8")


def _fail_engine_worker(request_path: str, exc: BaseException) -> None:
    """Report an --engine-worker exception as robustly as possible, then leave via
    the most forceful exit available (W11-d follow-up).  ``<request>.error.json``
    does not depend on stdout/stderr working at all, so the caller
    (``engine_adapter.solve()``) can read it back even if stdio was unusable.
    ``os._exit()`` (not ``sys.exit()``) skips atexit/interpreter-teardown handlers,
    since testing showed a --windowed build can hang there rather than in the
    exception itself.
    """
    import json
    import traceback

    if request_path:
        try:
            with open(request_path + ".error.json", "w", encoding="utf-8") as f:
                json.dump({"error": repr(exc), "traceback": traceback.format_exc()}, f)
        except OSError:
            pass
    try:
        print(f"ERROR {exc!r}", flush=True)
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(1)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--engine-worker":
        sys.stdout = _bind_std_stream(sys.stdout, 1)
        sys.stderr = _bind_std_stream(sys.stderr, 2)
        # spd_pi_engine.geometry imports matplotlib.path at module scope; matplotlib
        # picks a GUI backend on first use unless MPLBACKEND is already set, and the
        # worker never builds a QApplication (W11_PLAN_2026-09-19.md, this branch is
        # the frozen-build "no Qt import" contract). Set before the import below.
        os.environ.setdefault("MPLBACKEND", "Agg")
        request_path = sys.argv[2] if len(sys.argv) > 2 else ""
        try:
            from spd_decap_pi._core.solver.engine_worker import main as engine_worker_main

            return engine_worker_main(sys.argv[2:])
        except BaseException as exc:  # never hang on an unhandled exception (W11-d)
            _fail_engine_worker(request_path, exc)  # always os._exit()s, never returns
    from spd_decap_pi.gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
