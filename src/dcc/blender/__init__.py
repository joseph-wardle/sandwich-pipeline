"""Blender integration package.

Re-exports `BlenderLauncher` so `framework.dispatch.find_implementation` can
locate the concrete launcher from the outer venv. Blender has no in-DCC
runtime contract defined yet — there is no `runtime.py` module.
"""

from __future__ import annotations

from dcc.blender.launch import BlenderLauncher

__all__ = ["BlenderLauncher"]
