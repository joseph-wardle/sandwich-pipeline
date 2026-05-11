"""Find a per-DCC concrete implementation of a framework ABC by module name.

The pipeline entry point (`src/__main__.py`) uses `find_implementation` to look
up the `DCCLauncher` subclass for a given DCC name. Per-DCC launcher modules
(e.g. `dcc.maya`) re-export their concrete `<Dcc>Launcher` class via their
package `__init__.py`; this dispatcher imports that module and returns the
single concrete subclass found.
"""

from __future__ import annotations

import importlib
import importlib.util
from inspect import getmembers, isabstract, isclass


def find_implementation(cls: type, module: str, package: str | None = None) -> type:
    """Find an implementation of `cls` in the specified `module`.

    Imports the module and returns the single concrete (non-abstract) subclass
    of `cls` that it exposes. Raises `ValueError` if the module cannot be
    located, and `AssertionError` if the module does not expose exactly one
    matching implementation.
    """
    if importlib.util.find_spec(module, package):
        imported_module = importlib.import_module(module, package)

        classes = getmembers(
            imported_module,
            lambda obj: isclass(obj) and not isabstract(obj) and issubclass(obj, cls),
        )

        if len(classes) < 1:
            raise AssertionError(
                f"module '{module}' does not contain an "
                f"implementation of class '{cls.__name__}'"
            )
        elif len(classes) > 1:
            raise AssertionError(
                f"module '{module}' contains multiple "
                f"implementations of class '{cls.__name__}'"
            )

        return classes[0][1]

    else:
        raise ValueError(f"could not find module '{module}'")
