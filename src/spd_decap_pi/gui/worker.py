"""Cancelable background jobs for the standalone desktop application."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from time import monotonic
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    started = Signal()
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class _CoalescedProgress:
    """Keep high-frequency worker callbacks from flooding the GUI event queue."""

    def __init__(
        self,
        emit: Callable[[int, str], None],
        *,
        minimum_interval_s: float,
    ) -> None:
        self._emit = emit
        self._minimum_interval_s = minimum_interval_s
        self._last_emit_s: float | None = None
        self._latest: tuple[int, str] | None = None

    def __call__(self, value: int, message: str) -> None:
        clamped = max(0, min(100, int(value)))
        update = (clamped, str(message))
        self._latest = update
        now = monotonic()
        if (
            clamped == 100
            or self._last_emit_s is None
            or now - self._last_emit_s >= self._minimum_interval_s
        ):
            self._emit_latest(now)

    def flush(self) -> None:
        if self._latest is not None:
            self._emit_latest(monotonic())

    def _emit_latest(self, now: float) -> None:
        if self._latest is None:
            return
        value, message = self._latest
        self._emit(value, message)
        self._last_emit_s = now
        self._latest = None


class FunctionWorker(QRunnable):
    """Run a callable with ``progress`` and ``is_cancelled`` callbacks."""

    PROGRESS_MINIMUM_INTERVAL_S = 0.075

    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """Whether cancellation was requested (read-only to consumers)."""

        return self._cancelled

    @Slot()
    def run(self) -> None:
        self.signals.started.emit()
        progress = _CoalescedProgress(
            self.signals.progress.emit,
            minimum_interval_s=self.PROGRESS_MINIMUM_INTERVAL_S,
        )
        try:
            result = self.function(
                *self.args,
                progress=progress,
                is_cancelled=lambda: self._cancelled,
                **self.kwargs,
            )
            progress.flush()
            if not self._cancelled:
                self.signals.result.emit(result)
        except Exception:  # pragma: no cover - exercised by GUI integration
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()
