"""Compatibility shim — real implementation lives in `dcc.houdini`.

`from software.houdini import HoudiniDCC` continues to resolve here through
Phase 5 of the structural refactor.
"""

from __future__ import annotations

from dcc.houdini.launch import HoudiniLauncher as HoudiniDCC

__all__ = ["HoudiniDCC"]
