"""Substance Painter integration package.

Re-exports `SubstancePainterLauncher` so `framework.dispatch.find_implementation`
can locate the concrete launcher from the outer venv. `SubstancePainterRuntime`
is deliberately NOT re-exported here — it imports `substance_painter.ui` at
module level and is reachable only via `from dcc.substance_painter.runtime
import ...` once inside the Substance Painter interpreter.
"""

from __future__ import annotations

from dcc.substance_painter.launch import SubstancePainterLauncher

__all__ = ["SubstancePainterLauncher"]
