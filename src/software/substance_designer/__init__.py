"""Compatibility shim — real implementation lives in `dcc.substance_designer`.

`from software.substance_designer import SubstanceDesignerDCC` continues to
resolve here through Phase 5 of the structural refactor.
"""

from __future__ import annotations

from dcc.substance_designer.launch import (
    SubstanceDesignerLauncher as SubstanceDesignerDCC,
)

__all__ = ["SubstanceDesignerDCC"]
