"""Point Nuke at the pipeline OCIO config.

Nuke 16 needs both `Root.OCIO_config = custom` *and*
`Root.customOCIOConfigPath` set to the config file — `$OCIO` alone
flips Nuke into a state where it reverts to a previously-loaded
config. If you know why, please fix. But it works for now

See context/color.md for the architectural overview.
"""

import os

import nuke

from pipe.core.color import DEFAULT_VIEW, DISPLAY

_VIEWER_PROCESS = f"{DEFAULT_VIEW} ({DISPLAY})"
_OCIO_PATH = os.environ["OCIO"]

# Defaults for any newly-created Root / Viewer.
nuke.knobDefault("Root.customOCIOConfigPath", _OCIO_PATH)
nuke.knobDefault("Root.OCIO_config", "custom")
nuke.knobDefault("Viewer.viewerProcess", _VIEWER_PROCESS)

# Patch the Root that already exists when menu.py runs
_root = nuke.root()
if _root["customOCIOConfigPath"].value() != _OCIO_PATH:
    _root["customOCIOConfigPath"].setValue(_OCIO_PATH)
if _root["OCIO_config"].value() != "custom":
    _root["OCIO_config"].setValue("custom")
