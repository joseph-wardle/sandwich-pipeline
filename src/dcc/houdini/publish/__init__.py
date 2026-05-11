"""Houdini publish — component-output HDA, asset builder, node layouts, hooks.

`main.py` is the legacy `publish.py` content (deterministic component-output
publish service). `hooks.py` is the legacy `publish_hooks.py`. Re-exports below
keep `from dcc.houdini.publish import PublishOptions, publish_component` and
the equivalent legacy `dcc.houdini.publish.main` shim path working.
"""

from __future__ import annotations

from dcc.houdini.publish.main import PublishOptions, publish_component

__all__ = ["PublishOptions", "publish_component"]
