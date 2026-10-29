"""DaVinci Resolve integration package.

Re-exports `ResolveLauncher` so `framework.dispatch.find_implementation` can
locate the concrete launcher from the outer venv.
"""

from __future__ import annotations

from pipe.dcc.resolve.launch import ResolveLauncher

__all__ = ["ResolveLauncher"]
