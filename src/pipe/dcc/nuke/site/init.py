import os

import nuke

# `third_party` lives outside NUKE_PATH (which is this `site` folder), so its
# contents have to be added explicitly. launch.py exports DCC_NUKE_THIRD_PARTY
# for exactly this; gizmos in there also read it via [getenv] in file knobs.
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
