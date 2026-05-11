import nuke
from core.util.util import get_pipe_path

nuke.pluginAddPath("./gizmos")
nuke.pluginAddPath("./icons")
nuke.pluginAddPath("./images")
nuke.pluginAddPath("./nk_files")
nuke.pluginAddPath("./toolsets")
nuke.pluginAddPath("./scripts")


def make_bobo_read_node():
    import bobo_read_node

    # run the normal read now
    bobo_read_node.auto_read_latest_exr()


def make_bobo_fx_read_node():
    import bobo_read_node

    # run the FX read now
    bobo_read_node.auto_read_latest_fx_exr()


def make_bobo_cfx_read_node():
    import bobo_read_node

    # run the FX read now
    bobo_read_node.auto_read_latest_cfx_exr()


def make_bobo_write_node():
    import bobo_write_node_v2

    bobo_write_node_v2.main()


def import_render_layers():
    import render_layer_selector

    render_layer_selector.run()


def import_USD_cam():
    import import_usd_camera

    import_usd_camera.run()


def choose_shot():
    import open_shot

    open_shot.run()


def set_frameRange_and_aspectRatio():
    import set_frameRange_and_aspectRatio

    set_frameRange_and_aspectRatio.run()


################################### Nungeon buttons (Sidebar) ###################################
toolbar = nuke.menu("Nodes")
m = toolbar.addMenu("SKD", icon="MicrowaveIcon.png")


m.addCommand(
    "Template",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/SKD_Tools/toolsets/shotTemplate.nk")}")',
    icon="MicrowaveIcon.png",
)
m.addCommand(
    "Depth Fog",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/SKD_Tools/toolsets/depth_fog.nk")}")',
    icon="MicrowaveIcon.png",
)
m.addCommand(
    "Deep Fog",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/SKD_Tools/toolsets/deep_fog.nk")}")',
    icon="MicrowaveIcon.png",
)

m.addCommand(
    "Lightwrap (upper matrix)",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/SKD_Tools/toolsets/bobo_lightwrap.nk")}")',
    icon="MicrowaveIcon.png",
)
m.addCommand(
    "Relight",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/SKD_Tools/toolsets/relight_template.nk")}")',
    icon="MicrowaveIcon.png",
)

m.addCommand(
    "Eye Light",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/SKD_Tools/toolsets/eyelights.nk")}")',
    icon="MicrowaveIcon.png",
)
m.addCommand(
    "Sky Dome (Basic)",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/SKD_Tools/toolsets/bobo_skydome_basic.nk")}")',
    icon="MicrowaveIcon.png",
)


# m.addCommand("FrameBurn", "nuke.createNode('FrameBurn')", icon="nungeonIcon.png")
m.addCommand("Grade_AOV", "nuke.createNode('grade_AOV')", icon="MicrowaveIcon.png")
m.addCommand("luma Distort", "nuke.createNode('lumaDistort')", icon="MicrowaveIcon.png")
# m.addCommand("Roughen Edges", "nuke.createNode('roughenEdges')", icon="MicrowaveIcon.png") #broken but worked on previous films
# lens node
m.addCommand("Lens", "nuke.createNode('Lens')", icon="MicrowaveIcon.png")
print(
    f"nuke.nodePaste({str(get_pipe_path() / 'software/nuke/tools/SKD_Tools/toolsets/shotTemplate.nk')})"
)
m.addCommand("SKD Write Node", "make_bobo_write_node()", icon="MicrowaveIcon.png")
m.addCommand("SKD Open Shot", "choose_shot()", icon="MicrowaveIcon.png")
m.addCommand("SKD Read Node", "make_bobo_read_node()", icon="MicrowaveIcon.png")

m.addCommand("SKD FX Read", "make_bobo_fx_read_node()", icon="MicrowaveIcon.png")
m.addCommand("SKD CFX Read", "make_bobo_cfx_read_node()", icon="MicrowaveIcon.png")

################################### Nungeon Shelf Tool Buttons ###################################
menu = nuke.menu("Nuke")
menu.addCommand("[Choose Shot]", "choose_shot()")
menu.addCommand("[Import Render Layers]", "import_render_layers()")
menu.addCommand("[Import USD Camera]", "import_USD_cam()")
menu.addCommand("[Set Project Settings]", "set_frameRange_and_aspectRatio()")
