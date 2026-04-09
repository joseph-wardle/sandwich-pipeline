import nuke

nuke.pluginAddPath("./NukeSurvivalToolkit_publicRelease/NukeSurvivalToolkit")
nuke.pluginAddPath("./SKD_Tools")

# aspect ratio
nuke.addFormat("1920 1080 Bobo_aspect_ratio")

nuke.knobDefault("Root.format", "Bobo_aspect_ratio")

# color management
nuke.knobDefault("Root.colorManagement", "OCIO")
