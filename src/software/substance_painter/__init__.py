"""Compatibility shim — real implementation lives in `dcc.substance_painter`.

`from software.substance_painter import SubstancePainterDCC` continues to resolve here through
Phase 5 of the structural refactor.
"""

from __future__ import annotations

from dcc.substance_painter.launch import SubstancePainterLauncher as SubstancePainterDCC

__all__ = ["SubstancePainterDCC"]
