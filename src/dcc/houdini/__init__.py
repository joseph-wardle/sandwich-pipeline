"""Houdini integration package.

Re-exports `HoudiniLauncher` so `framework.dispatch.find_implementation` can
locate the concrete launcher from the outer venv. `HoudiniRuntime` and the
publish/shading/shot feature modules import `hou` at module level and are
reachable only via `from dcc.houdini.<sub> import ...` once inside the
Houdini interpreter.
"""

from __future__ import annotations

from dcc.houdini.launch import HoudiniLauncher

__all__ = ["HoudiniLauncher"]
