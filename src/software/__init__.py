"""Compatibility shim — per-DCC packages now live under `dcc.<name>`.

Phase 4 of the structural refactor moved every `software.<dcc>` package
(launcher, plugins, scripts) into `dcc.<dcc>`. The shims at
`src/software/<dcc>/__init__.py` re-export the canonical `<Dcc>Launcher`
under the legacy `<Dcc>DCC` name so existing
`from software.<dcc> import <Dcc>DCC` imports keep working through Phase 5.

The stale `"unreal"` entry that used to live in `__all__` here is gone —
there was never an `unreal/` directory to back it.
"""

from __future__ import annotations

__all__: list[str] = []
