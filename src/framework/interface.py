from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typing

"""Abstract DCC integration contracts.

`DCCLauncher` is the outer-process side: per-DCC implementations build env
vars and command lines and call `subprocess`. `framework.launcher.Launcher`
provides the concrete subprocess+telemetry machinery that subclasses inherit.

`DCCRuntime` is the in-DCC side: per-DCC implementations expose runtime
context (main Qt window, headless detection) to feature code that runs
inside the DCC's interpreter.
"""


class DCCLauncher(metaclass=ABCMeta):
    """Outer-process launcher contract — implemented per DCC in `dcc.<name>.launch`."""

    @abstractmethod
    def __init__(self):
        """Initialize the launcher."""
        raise NotImplementedError

    @abstractmethod
    def launch(self) -> None:
        """Launch the DCC subprocess."""
        raise NotImplementedError


class DCCRuntime(metaclass=ABCMeta):
    """In-DCC runtime contract — implemented per DCC in `dcc.<name>.runtime`."""

    @abstractmethod
    def __init__(self, id: str | None = None) -> None:
        """Initialize the runtime.

        The `id` parameter is accepted for compatibility with the legacy
        `DCCLocalizer` concrete base, where per-DCC subclasses passed the
        DCC name via `super().__init__("<name>")`. The argument is unused;
        Phase 5 of the structural refactor drops it from per-DCC runtime
        classes when they move under `dcc/<name>/runtime.py`.
        """
        raise NotImplementedError

    @abstractmethod
    def get_main_qt_window(self) -> typing.Any:
        """Return the DCC's main Qt window for parenting dialogs.

        Returns None if no main window is available (e.g. headless mode).
        """
        raise NotImplementedError

    @abstractmethod
    def is_headless(self) -> bool:
        """Return whether the DCC is running in headless mode (no GUI)."""
        raise NotImplementedError
