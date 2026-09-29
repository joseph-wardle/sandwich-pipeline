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


def is_open_project(path: Path | None) -> bool:
    """Return True if *path* is the open project's file."""
    project_path = current_project_path()
    if project_path is None or path is None:
        return False
    try:
        return project_path.samefile(path)
    except OSError:
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


def run_when_project_editable(callback: Callable[[], None]) -> None:
    """Run *callback* once the open project is loaded and idle.

    Dropped if that project closes first, so it never runs on the next one.
    """
    if not sp.project.is_open():
        log.warning("No project is open; dropping the deferred project callback.")
        return
    _ProjectBoundCallback(callback).attempt()


class _ProjectBoundCallback:
    """Run a callback once its project is editable, unless the project closes."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._finished = False
        self._waiting_for_edition = False
        sp.event.DISPATCHER.connect_strong(
            sp.event.ProjectAboutToClose, self._on_project_about_to_close
        )

    def attempt(self) -> None:
        if self._finished:
            return

        if sp.project.is_busy():
            sp.project.execute_when_not_busy(self.attempt)
            return

        try:
            in_edition_state = sp.project.is_in_edition_state()
        except ServiceNotFoundError:
            log.exception("Failed to query project edition state; dropping callback.")
            self._finish()
            return
        if not in_edition_state:
            self._wait_for_edition()
            return

        self._finish()
        # Painter's dispatcher prints and swallows exceptions from listeners,
        # so a failure here shows up only in Painter's log.
        self._callback()

    def _wait_for_edition(self) -> None:
        if self._waiting_for_edition:
            return
        self._waiting_for_edition = True
        sp.event.DISPATCHER.connect_strong(
            sp.event.ProjectEditionEntered, self._on_edition_entered
        )

    def _stop_waiting_for_edition(self) -> None:
        if not self._waiting_for_edition:
            return
        self._waiting_for_edition = False
        sp.event.DISPATCHER.disconnect(
            sp.event.ProjectEditionEntered, self._on_edition_entered
        )

    def _on_edition_entered(self, _event: sp.event.Event) -> None:
        self._stop_waiting_for_edition()
        self.attempt()

    def _on_project_about_to_close(self, _event: sp.event.Event) -> None:
        log.warning("Project closed before it was editable; dropping callback.")
        self._finish()

    def _finish(self) -> None:
        self._finished = True
        self._stop_waiting_for_edition()
        sp.event.DISPATCHER.disconnect(
            sp.event.ProjectAboutToClose, self._on_project_about_to_close
        )
