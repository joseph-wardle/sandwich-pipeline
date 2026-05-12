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
    """Outer-process launcher contract — implemented per DCC in `dcc.<name>.launch`.

    Subclasses define their own `__init__` (signatures vary per DCC — Maya
    takes `is_python_shell`/`extra_args`; Substance Painter refuses
    `is_python_shell=True`; etc.). The only enforced contract is `launch()`.
    """

    @abstractmethod
    def launch(self) -> None:
        """Launch the DCC subprocess."""
        raise NotImplementedError


class DCCRuntime(metaclass=ABCMeta):
    """In-DCC runtime contract — implemented per DCC in `dcc.<name>.runtime`.

    Subclasses are typically instantiated once at module load
    (`_runtime = <Dcc>Runtime()`) so their per-DCC API imports happen exactly
    when the module is first imported from inside the DCC interpreter.
    """

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
