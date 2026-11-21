"""Run slow reads, such as Tractor's replies and EXR frames, off the window's thread."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from Qt import QtCore


class _Signals(QtCore.QObject):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(object)


class _Runnable(QtCore.QRunnable):
    def __init__(self, work: Callable[[], Any], signals: _Signals) -> None:
        super().__init__()
        self._work = work
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:
            self._report(self._signals.failed, exc)
        else:
            self._report(self._signals.done, result)

    @staticmethod
    def _report(signal: Any, value: object) -> None:
        try:
            signal.emit(value)
        except RuntimeError:
            # The window closed while this ran; no one is left to tell.
            pass


# Signals objects stay referenced until their work reports back.
_pending: set[_Signals] = set()


def run(
    work: Callable[[], Any],
    done: Callable[[Any], None],
    failed: Callable[[Exception], None],
) -> None:
    """Call `work` on a pool thread, then `done` or `failed` on the window's thread."""
    # Made on the window's thread, so its signals are delivered there.
    signals = _Signals()
    _pending.add(signals)

    def finish(callback: Callable[[Any], None], value: Any) -> None:
        _pending.discard(signals)
        callback(value)

    signals.done.connect(lambda result: finish(done, result))
    signals.failed.connect(lambda exc: finish(failed, exc))
    QtCore.QThreadPool.globalInstance().start(_Runnable(work, signals))
