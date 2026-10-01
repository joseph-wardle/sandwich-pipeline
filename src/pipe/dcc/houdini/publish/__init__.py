"""Houdini publish — component-output HDA, asset builder, node layouts, hooks.

`main.py` is the legacy `publish.py` content (deterministic component-output
publish service). `hooks.py` is the legacy `publish_hooks.py`. Re-exports below
keep `from pipe.dcc.houdini.publish import PublishOptions, publish_component` and
the equivalent legacy `pipe.dcc.houdini.publish.main` shim path working.

`version.py` is the shot and set publish: what a hip's publish node declares,
written as the next publish version.
"""

from __future__ import annotations

from pipe.dcc.houdini.publish.main import PublishOptions, publish_component

__all__ = ["PublishOptions", "publish_component"]
