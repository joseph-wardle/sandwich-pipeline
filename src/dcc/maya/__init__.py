"""Maya integration package.

Re-exports `MayaLauncher` so `framework.dispatch.find_implementation` can
locate the concrete launcher from the outer venv. `MayaRuntime` and every
feature module (assetfile, publish, playblast, shotfile, layout, rig,
symmetry, command, util/*) import `maya.cmds` / `maya.OpenMayaUI` at module
level and are reachable only via `from dcc.maya.<sub> import ...` once
inside the Maya interpreter.
"""

from __future__ import annotations

from dcc.maya.launch import MayaLauncher

__all__ = ["MayaLauncher"]
