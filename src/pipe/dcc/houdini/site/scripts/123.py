"""Force the ACES viewport transform on every Houdini startup"""

from __future__ import annotations

import hou

from pipe.core.color import DEFAULT_VIEW, DISPLAY


def _apply_ocio_defaults() -> None:
    for tab in hou.ui.paneTabs():
        if isinstance(tab, hou.SceneViewer):
            tab.setUsingOCIO(True)
            tab.setOCIODisplayView(DISPLAY, DEFAULT_VIEW)


# hdefereval exists only in graphical sessions, not hython.
if hou.isUIAvailable():
    import hdefereval

    hdefereval.executeDeferred(_apply_ocio_defaults)
