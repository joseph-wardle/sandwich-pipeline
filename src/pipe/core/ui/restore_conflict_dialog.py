from __future__ import annotations

from Qt import QtCore, QtWidgets

RESTORE_SAVE_FIRST = "save_first"
RESTORE_DISCARD = "discard"
RESTORE_CANCEL = "cancel"

_MESSAGE = (
    "Your working file has changes that are not saved as a version.\n"
    "Restoring replaces it with the selected version."
)


def prompt_restore_conflict(parent: QtWidgets.QWidget | None) -> str:
    """Ask how to handle un-versioned work before a restore overwrites it.

    Returns one of ``RESTORE_SAVE_FIRST``, ``RESTORE_DISCARD``, ``RESTORE_CANCEL``.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Unsaved Work")
    box.setWindowFlags(box.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
    box.setText(_MESSAGE)

    save_btn = box.addButton("Save Version First…", QtWidgets.QMessageBox.AcceptRole)
    discard_btn = box.addButton(
        "Discard & Restore", QtWidgets.QMessageBox.DestructiveRole
    )
    box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(save_btn)

    box.exec_()
    clicked = box.clickedButton()
    if clicked is save_btn:
        return RESTORE_SAVE_FIRST
    if clicked is discard_btn:
        return RESTORE_DISCARD
    return RESTORE_CANCEL


__all__ = [
    "RESTORE_CANCEL",
    "RESTORE_DISCARD",
    "RESTORE_SAVE_FIRST",
    "prompt_restore_conflict",
]
