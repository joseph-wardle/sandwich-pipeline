"""Project lifecycle helpers for Substance Painter."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import substance_painter as sp
from Qt import QtWidgets
from substance_painter.exception import ProjectError, ServiceNotFoundError

from pipe.core.ui import MessageDialog

log = logging.getLogger(__name__)

_TEMPLATE_SUFFIX = ".spt"


def current_project_path() -> Path | None:
    """Return the file path of the currently open project, or None."""
    try:
        path_str = sp.project.file_path()
    except (ProjectError, ServiceNotFoundError):
        return None
    if not path_str:
        return None
    path = Path(path_str)
    if path.suffix.lower() == _TEMPLATE_SUFFIX:
        return None
    return path


def check_not_busy(parent: QtWidgets.QWidget | None, action_name: str) -> bool:
    """Return True if Painter is idle; otherwise tell the artist to wait."""
    if not sp.project.is_busy():
        return True
    MessageDialog(
        parent,
        "Substance Painter is busy. Wait for the current operation to finish.",
        action_name,
    ).exec_()
    return False


def check_project_editable(parent: QtWidgets.QWidget | None, action_name: str) -> bool:
    """Return True if the project is open, loaded, and idle.

    Otherwise shows the artist a dialog, titled *action_name*, saying what
    to wait for, and returns False.  Never waits or changes project state.
    """
    if not sp.project.is_open():
        MessageDialog(
            parent,
            "No Substance Painter project is open. Open an asset project first.",
            action_name,
        ).exec_()
        return False

    if not check_not_busy(parent, action_name):
        return False

    try:
        in_edition_state = sp.project.is_in_edition_state()
    except ServiceNotFoundError:
        log.exception(f"Failed to query project edition state for {action_name}.")
        return False
    if not in_edition_state:
        MessageDialog(
            parent,
            "The project is still loading. Wait for it to finish before continuing.",
            action_name,
        ).exec_()
        return False

    return True


def save_project(show_error: Callable[[str, str], object]) -> bool:
    """Save the open project; on failure call *show_error(message, title)*."""
    try:
        sp.project.save()
    except ProjectError:
        log.exception("Failed to save the Substance Painter project.")
        show_error(
            "Failed to save the project. Resolve file issues and try again.",
            "Save Failed",
        )
        return False

    if sp.project.needs_saving():
        show_error(
            "The project still appears unsaved. Save manually and try again.",
            "Save Required",
        )
        return False

    return True
