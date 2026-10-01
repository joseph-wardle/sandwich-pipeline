"""Houdini publish — component-output HDA, asset builder, node layouts, hooks.

`main.py` is the legacy `publish.py` content (deterministic component-output
publish service). `hooks.py` is the legacy `publish_hooks.py`. Re-exports below
keep `from pipe.dcc.houdini.publish import PublishOptions, publish_component` and
the equivalent legacy `pipe.dcc.houdini.publish.main` shim path working.

`version.py` is the shot and set publish: what a hip's publish node declares,
written as the next publish version. `history.py` lists those versions, to open
the hip that made one or to make one current. `load_layers.py` is the reading
end: which publish version each row of a shot hip's load layers node reads.
"""

from __future__ import annotations

from pipe.dcc.houdini.publish.main import PublishOptions, publish_component

__all__ = ["PublishOptions", "publish_component"]
