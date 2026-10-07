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

# Comps made before UHD keep this format, saved in their script.
nuke.addFormat("1920 1080 Bobo_aspect_ratio")

# Renders stay 1080p until finals; each Read's Reformat sizes them up. Nuke's
# default proxy, scale 0.5, shows a UHD comp at 1080p.
nuke.knobDefault("Root.format", "UHD_4K")

# color management
nuke.knobDefault("Root.colorManagement", "OCIO")
