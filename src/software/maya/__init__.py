"""Compatibility shim — real implementation lives in `dcc.maya`.

`from software.maya import MayaDCC` continues to resolve here through
Phase 5 of the structural refactor.
"""

from __future__ import annotations

from dcc.maya.launch import MayaLauncher as MayaDCC

__all__ = ["MayaDCC"]
