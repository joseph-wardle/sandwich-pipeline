from __future__ import annotations

import logging
import platform
import subprocess
import sys
from functools import wraps
from typing import TYPE_CHECKING

from .struct import dict_index, dotdict

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Any, Callable, Sequence

    # Qt is only used in type annotations below (which are deferred by
    # `from __future__ import annotations`). Keeping the import under
    # TYPE_CHECKING lets `core.util` load in Qt-less contexts such as the
    # outer launcher venv before a DCC subprocess starts.
    from Qt import QtWidgets

    from .filemanager import FileManager

log = logging.getLogger(__name__)


def checkbox_callback_helper(
    checkbox: QtWidgets.QCheckBox, widget: QtWidgets.QWidget
) -> Callable[[], None]:
    """Helper function to generate a callback to enable/disable a widget when
    a checkbox is checked"""

    def inner() -> None:
        widget.setEnabled(checkbox.isChecked())

    return inner


def log_errors(fun):
    @wraps(fun)
    def wrap(*args, **kwargs):
        try:
            return fun(*args, **kwargs)
        except Exception as e:
            log.error(e, exc_info=True)
            raise

    return wrap


def reload_pipeline(extra_modules: Sequence[ModuleType] | None = None) -> None:
    """Reload all pipeline python modules"""
    if extra_modules is None:
        extra_modules = []
    else:
        extra_modules = list(extra_modules)

    pipeline_modules = [
        module
        for name, module in sys.modules.items()
        if (name.startswith(("core", "dcc")))
        and ("shotgun_api3" not in name)
        or (name == "env")
    ] + extra_modules

    for module in pipeline_modules:
        if (name := module.__name__) in sys.modules:
            log.info(f"Unloading {name}")
            del sys.modules[name]


try:

    def silent_startupinfo() -> subprocess.STARTUPINFO | None:  # type: ignore
        """Returns a Windows-only object to make sure tasks launched through
        subprocess don't open a cmd window.

        Returns:
            subprocess.STARTUPINFO -- the properly configured object if we are on
                                    Windows, otherwise None
        """
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()  # type: ignore
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore
        return startupinfo
except Exception:

    def silent_startupinfo() -> Any | None:
        pass


def __getattr__(name: str) -> object:
    """Lazily expose `FileManager` so importing `core.util` stays light.

    `FileManager` pulls in Qt, ShotGrid, and the glui dialog suite — none of
    which are needed by callers that only want `silent_startupinfo`,
    `log_errors`, etc. Resolving it through `__getattr__` keeps `core.util`
    importable in Qt-less contexts (the outer launcher venv, telemetry
    backend tools) while still letting `from core.util import FileManager`
    work where the heavy stack is available.
    """
    if name == "FileManager":
        from .filemanager import FileManager as _fm

        globals()["FileManager"] = _fm
        return _fm
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "checkbox_callback_helper",
    "dict_index",
    "dotdict",
    "log_errors",
    "reload_pipeline",
    "silent_startupinfo",
    "FileManager",
]
