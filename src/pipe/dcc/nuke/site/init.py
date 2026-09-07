import os

import nuke

_THIRD_PARTY = os.environ["DCC_NUKE_THIRD_PARTY"]

nuke.pluginAddPath(
    os.path.join(
        _THIRD_PARTY, "NukeSurvivalToolkit_publicRelease", "NukeSurvivalToolkit"
    )
)

nuke.pluginAddPath(os.path.join(_THIRD_PARTY, "scripts"))
nuke.pluginAddPath("./SKD_Tools")

# aspect ratio
nuke.addFormat("1920 1080 Bobo_aspect_ratio")

nuke.knobDefault("Root.format", "Bobo_aspect_ratio")

# color management
nuke.knobDefault("Root.colorManagement", "OCIO")
