"""Compatibility shim — real implementation lives in `dcc.blender`.

`from software.blender import BlenderDCC` continues to resolve here through
Phase 5 of the structural refactor.
"""

from __future__ import annotations

from dcc.blender.launch import BlenderLauncher as BlenderDCC

__all__ = ["BlenderDCC"]
