import os

import nuke

"""Register non-plugin third party gizmos."""

_THIRD_PARTY = os.environ["DCC_NUKE_THIRD_PARTY"]

nuke.pluginAddPath(
    os.path.join(
        _THIRD_PARTY, "NukeSurvivalToolkit_publicRelease", "NukeSurvivalToolkit"
    )
)
# Registers the loose vendored gizmos; see that folder's init.py / menu.py.
nuke.pluginAddPath(os.path.join(_THIRD_PARTY, "scripts"))
nuke.pluginAddPath("./SKD_Tools")

# aspect ratio
nuke.addFormat("1920 1080 Bobo_aspect_ratio")

nuke.knobDefault("Root.format", "Bobo_aspect_ratio")

# color management
nuke.knobDefault("Root.colorManagement", "OCIO")
