"""Substance Designer integration package.

Re-exports `SubstanceDesignerLauncher` so `framework.dispatch.find_implementation`
can locate the concrete launcher. Substance Designer has no in-DCC runtime
contract — there is no `runtime.py` module to import.
"""

from __future__ import annotations

from dcc.substance_designer.launch import SubstanceDesignerLauncher

__all__ = ["SubstanceDesignerLauncher"]
