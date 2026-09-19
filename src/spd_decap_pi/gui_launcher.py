"""PyInstaller-friendly entry point for the SPD Decap PI Evaluator GUI.

``--engine-worker <request.json>`` re-enters this same executable as the
``spd_pi_engine`` solve worker (``_core/solver/engine_worker.py``).  The frozen
build has no importable interpreter, so the branch has to live here -- and
before Qt is imported, because the worker must never build a QApplication.
"""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--engine-worker":
        from spd_decap_pi._core.solver.engine_worker import main as engine_worker_main

        return engine_worker_main(sys.argv[2:])
    from spd_decap_pi.gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
