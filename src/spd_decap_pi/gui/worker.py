"""Cancelable background jobs for the standalone desktop application."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    started = Signal()
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    """Run a callable with ``progress`` and ``is_cancelled`` callbacks."""

    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        self.signals.started.emit()
        try:
            result = self.function(
                *self.args,
                progress=self.signals.progress.emit,
                is_cancelled=lambda: self._cancelled,
                **self.kwargs,
            )
            if not self._cancelled:
                self.signals.result.emit(result)
        except Exception:  # pragma: no cover - exercised by GUI integration
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()
