"""Shared progress dialog and context manager for long-running pipeline operations.

Provides a modal progress dialog that shows step-by-step progress to artists
during synchronous workflows (publishes, playblasts, exports). The dialog
displays a step counter, stage label, optional detail text, and a progress bar
that supports both determinate and indeterminate modes.

Synchronous workflows use the ``progress_scope`` context manager::

    with progress_scope(
        parent=window,
        title="Publishing Asset",
        steps=["Exporting USD", "Backing up scene"],
    ) as progress:
        progress.begin_step("Exporting USD")
        export_usd()
        progress.begin_step("Backing up scene")
        run_backup()

Async workflows (Substance Painter) create ``ProgressDialog`` directly and
call ``set_progress`` / ``finish`` manually.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from Qt import QtCore, QtWidgets
from Qt.QtWidgets import QDialog, QLabel, QProgressBar

log = logging.getLogger(__name__)


class ProgressDialog(QDialog):
    """Non-closable modal dialog with step counter, stage label, detail text,
    and a determinate/indeterminate progress bar.

    The dialog blocks user close attempts while active. Call ``finish`` to
    allow dismissal.
    """

    _allow_close: bool
    _detail_label: QLabel
    _progress_bar: QProgressBar
    _stage_label: QLabel
    _step_label: QLabel
    _total_steps: int

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        title: str,
        total_steps: int,
    ) -> None:
        super().__init__(parent)
        self._allow_close = False
        self._total_steps = total_steps

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlags(
            (self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
            | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setWindowModality(QtCore.Qt.WindowModal)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self._step_label = QLabel("")
        self._step_label.setStyleSheet("font-size: 11px; color: #8a8a8a;")
        layout.addWidget(self._step_label)

        self._stage_label = QLabel("Preparing...")
        self._stage_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(self._stage_label)

        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setRange(0, 0)
        layout.addWidget(self._progress_bar)

    def event(self, event: QtCore.QEvent) -> bool:
        if not self._allow_close and event.type() == QtCore.QEvent.Close:
            event.ignore()
            return True
        return super().event(event)

    def reject(self) -> None:
        if self._allow_close:
            super().reject()

    def set_progress(
        self,
        *,
        step: int,
        stage: str,
        detail: str = "",
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Update all labels and the progress bar.

        When *current* and *total* are both provided and *total* > 0, the bar
        shows determinate progress (e.g. "3 / 12"). Otherwise the bar pulses
        in indeterminate mode.
        """
        self._step_label.setText(f"Step {step} of {self._total_steps}")
        self._stage_label.setText(stage)
        self._detail_label.setText(detail)

        if current is not None and total is not None and total > 0:
            clamped_total = max(1, total)
            clamped_current = max(0, min(current, clamped_total))
            self._progress_bar.setRange(0, clamped_total)
            self._progress_bar.setValue(clamped_current)
            self._progress_bar.setFormat("%v / %m")
        else:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setFormat("")

        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

        QtWidgets.QApplication.processEvents()

    def finish(self) -> None:
        """Allow close and dismiss the dialog."""
        if self._allow_close:
            return
        self._allow_close = True
        self.close()


class ProgressScope:
    """Handle yielded by ``progress_scope`` for advancing through steps.

    Each call to ``begin_step`` moves to the named step and resets the bar
    to indeterminate mode. Use ``update_detail`` or ``update_substep`` for
    finer-grained feedback within a step.
    """

    _dialog: ProgressDialog
    _steps: Sequence[str]
    _current_step: int

    def __init__(self, dialog: ProgressDialog, steps: Sequence[str]) -> None:
        self._dialog = dialog
        self._steps = steps
        self._current_step = 0

    def begin_step(self, step_name: str, detail: str = "") -> None:
        """Advance to the named step. Resets the bar to indeterminate."""
        try:
            index = list(self._steps).index(step_name) + 1
        except ValueError:
            raise ValueError(
                f"Unknown step {step_name!r}. Declared steps: {list(self._steps)}"
            ) from None
        self._current_step = index
        self._dialog.set_progress(step=index, stage=step_name, detail=detail)

    def update_detail(self, detail: str) -> None:
        """Update the detail text within the current step."""
        if self._current_step == 0:
            return
        self._dialog.set_progress(
            step=self._current_step,
            stage=list(self._steps)[self._current_step - 1],
            detail=detail,
        )

    def update_substep(self, current: int, total: int, detail: str = "") -> None:
        """Show determinate sub-progress within the current step."""
        if self._current_step == 0:
            return
        self._dialog.set_progress(
            step=self._current_step,
            stage=list(self._steps)[self._current_step - 1],
            detail=detail,
            current=current,
            total=total,
        )


class _NoOpProgressScope:
    """Stub scope for headless environments where no QApplication exists."""

    def begin_step(self, step_name: str, detail: str = "") -> None:
        pass

    def update_detail(self, detail: str) -> None:
        pass

    def update_substep(self, current: int, total: int, detail: str = "") -> None:
        pass


@contextmanager
def progress_scope(
    *,
    parent: QtWidgets.QWidget | None,
    title: str,
    steps: Sequence[str],
) -> Iterator[ProgressScope | _NoOpProgressScope]:
    """Show a progress dialog for the duration of a synchronous operation.

    The dialog is guaranteed to close when the block exits, even on exception.
    In headless environments (no ``QApplication``), yields a no-op scope.
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        yield _NoOpProgressScope()
        return

    dialog = ProgressDialog(parent, title=title, total_steps=len(steps))
    scope = ProgressScope(dialog, steps)
    try:
        yield scope
    finally:
        dialog.finish()
