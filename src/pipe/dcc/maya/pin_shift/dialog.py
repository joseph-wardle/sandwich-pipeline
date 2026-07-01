"""Modeless dialog for the pin shift tool: pick a pivot and a frame count, then insert or remove that much time."""

from __future__ import annotations

import maya.cmds as mc
from Qt import QtWidgets

from pipe.dcc.maya.command import maya_command, undo_chunk
from pipe.dcc.maya.pin_shift.shift import PinShiftResult, affected_curves, pin_shift
from pipe.dcc.maya.runtime import get_main_qt_window

# Maya renders an inViewMessage as HTML, so a colored span tints the feedback by severity.
_WARN_COLOR = "#f4b400"  # amber: the action ran but moved nothing
_DESTRUCTIVE_COLOR = "#d9534f"  # red: Remove cuts keys

_dialog: PinShiftDialog | None = None


@maya_command(name="pin_shift", label="Pin Shift", category="animation", icon="pin.png")
def run() -> PinShiftDialog:
    """Open the pin shift dialog, closing any existing instance first."""
    global _dialog
    if _dialog is not None:
        try:
            _dialog.close()
            _dialog.deleteLater()
        except Exception:
            pass

    _dialog = PinShiftDialog(get_main_qt_window())
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    return _dialog


class PinShiftDialog(QtWidgets.QDialog):
    """Pivot/frames inputs plus Insert and Remove buttons. Stays open between actions."""

    def __init__(self, parent: QtWidgets.QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pin Shift")
        self.setModal(False)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        pivot_row = QtWidgets.QHBoxLayout()
        pivot_row.addWidget(QtWidgets.QLabel("Pivot frame"))
        self._pivot = QtWidgets.QSpinBox()
        self._pivot.setRange(-1_000_000, 1_000_000)
        self._pivot.setValue(int(mc.currentTime(query=True)))  # default to the playhead
        pivot_row.addWidget(self._pivot)
        layout.addLayout(pivot_row)

        frames_row = QtWidgets.QHBoxLayout()
        frames_row.addWidget(QtWidgets.QLabel("Frames"))
        self._frames = QtWidgets.QSpinBox()
        self._frames.setRange(1, 1_000_000)
        frames_row.addWidget(self._frames)
        layout.addLayout(frames_row)

        button_row = QtWidgets.QHBoxLayout()
        insert_button = QtWidgets.QPushButton("Insert")
        insert_button.clicked.connect(self._insert)
        button_row.addWidget(insert_button)
        remove_button = QtWidgets.QPushButton("Remove")
        remove_button.setStyleSheet(f"color: {_DESTRUCTIVE_COLOR};")
        remove_button.clicked.connect(self._remove)
        button_row.addWidget(remove_button)
        layout.addLayout(button_row)

    def _insert(self) -> None:
        self._apply(self._frames.value())

    def _remove(self) -> None:
        self._apply(-self._frames.value())

    def _apply(self, amount: int) -> None:
        pivot = self._pivot.value()
        curves = affected_curves()
        if not curves:
            _warn("Pin shift: no time-driven curves to shift.")
            return
        with undo_chunk("Pin shift"):
            result = pin_shift(pivot, amount, curves)
        _report(amount, pivot, result)


def _report(amount: int, pivot: int, result: PinShiftResult) -> None:
    """Flash a one-line summary, warning-styled when the action moved nothing."""
    if result.curves_shifted == 0:
        if result.skipped:
            _warn(
                f"Pin shift: all {len(result.skipped)} curve(s) are referenced or locked."
            )
        else:
            _warn(f"Pin shift: no movable keys after frame {pivot}.")
        return

    verb = "Inserted" if amount > 0 else "Removed"
    parts = [
        f"{verb} {abs(amount)} frame(s) at {pivot} on {result.curves_shifted} curve(s)"
    ]
    if result.keys_deleted:
        parts.append(f"{result.keys_deleted} key(s) cut")
    if result.skipped:
        parts.append(f"{len(result.skipped)} skipped (referenced/locked)")
    _flash("; ".join(parts) + ".")


def _flash(message: str) -> None:
    mc.inViewMessage(message=message, position="midCenter", fade=True)


def _warn(message: str) -> None:
    _flash(f'<span style="color:{_WARN_COLOR}">{message}</span>')
