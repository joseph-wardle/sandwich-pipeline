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

    if sp.project.is_busy():
        MessageDialog(
            parent,
            "Substance Painter is busy. Wait for the current operation to finish.",
            action_name,
        ).exec_()
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


def run_when_project_editable(callback: Callable[[], None]) -> None:
    """Run *callback* as soon as the project is open, loaded, and idle."""
    if not sp.project.is_open():
        _run_once_on_project_edition_entered(
            lambda: run_when_project_editable(callback)
        )
        return

    if sp.project.is_busy():
        sp.project.execute_when_not_busy(lambda: run_when_project_editable(callback))
        return

    try:
        if not sp.project.is_in_edition_state():
            _run_once_on_project_edition_entered(
                lambda: run_when_project_editable(callback)
            )
            return
    except ServiceNotFoundError:
        return

    callback()


def _run_once_on_project_edition_entered(callback: Callable[[], None]) -> None:
    """Run *callback* the next time the project enters edition state."""

    def _on_edition_entered(_event: sp.event.Event) -> None:
        # Painter's dispatcher prints and swallows exceptions from listeners,
        # so a failure here would silently drop the callback.
        sp.event.DISPATCHER.disconnect(
            sp.event.ProjectEditionEntered, _on_edition_entered
        )
        callback()

    sp.event.DISPATCHER.connect_strong(
        sp.event.ProjectEditionEntered, _on_edition_entered
    )
