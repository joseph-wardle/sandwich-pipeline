import nuke
from shared.util import get_pipe_path

nuke.pluginAddPath("./gizmos")
nuke.pluginAddPath("./icons")
nuke.pluginAddPath("./images")
nuke.pluginAddPath("./nk_files")
nuke.pluginAddPath("./toolsets")
nuke.pluginAddPath("./scripts")


def make_bobo_read_node():
    import bobo_read_node  # type: ignore[import-not-found]

    # run the normal read now
    bobo_read_node.auto_read_latest_exr()


def make_bobo_fx_read_node():
    import bobo_read_node  # type: ignore[import-not-found]

    # run the FX read now
    bobo_read_node.auto_read_latest_fx_exr()


def make_bobo_cfx_read_node():
    import bobo_read_node  # type: ignore[import-not-found]

    # run the FX read now
    bobo_read_node.auto_read_latest_cfx_exr()


def make_bobo_write_node():
    import bobo_write_node_v2  # type: ignore[import-not-found]

    bobo_write_node_v2.main()


def import_render_layers():
    import render_layer_selector  # type: ignore[import-not-found]

    render_layer_selector.run()


def import_USD_cam():
    import import_usd_camera  # type: ignore[import-not-found]

    import_usd_camera.run()


def choose_shot():
    import open_shot  # type: ignore[import-not-found]

    open_shot.run()


def set_frameRange_and_aspectRatio():
    import set_frameRange_and_aspectRatio  # type: ignore[import-not-found]

    set_frameRange_and_aspectRatio.run()


################################### Nungeon buttons (Sidebar) ###################################
toolbar = nuke.menu("Nodes")
m = toolbar.addMenu("SKD", icon="BobukeIcon.png")


m.addCommand(
    "Template",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/BobukeTools/toolsets/shotTemplate.nk")}")',
    icon="BobukeIcon.png",
)
m.addCommand(
    "Depth Fog",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/BobukeTools/toolsets/depth_fog.nk")}")',
    icon="BobukeIcon.png",
)
m.addCommand(
    "Deep Fog",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/BobukeTools/toolsets/deep_fog.nk")}")',
    icon="BobukeIcon.png",
)

m.addCommand(
    "Lightwrap (upper matrix)",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/BobukeTools/toolsets/bobo_lightwrap.nk")}")',
    icon="BobukeIcon.png",
)
m.addCommand(
    "Relight",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/BobukeTools/toolsets/relight_template.nk")}")',
    icon="BobukeIcon.png",
)

m.addCommand(
    "Eye Light",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/BobukeTools/toolsets/eyelights.nk")}")',
    icon="BobukeIcon.png",
)
m.addCommand(
    "Sky Dome (Basic)",
    f'nuke.nodePaste("{str(get_pipe_path() / "software/nuke/tools/BobukeTools/toolsets/bobo_skydome_basic.nk")}")',
    icon="BobukeIcon.png",
)


# m.addCommand("FrameBurn", "nuke.createNode('FrameBurn')", icon="nungeonIcon.png")
m.addCommand("Grade_AOV", "nuke.createNode('grade_AOV')", icon="BobukeIcon.png")
m.addCommand("luma Distort", "nuke.createNode('lumaDistort')", icon="BobukeIcon.png")
# m.addCommand("Roughen Edges", "nuke.createNode('roughenEdges')", icon="BobukeIcon.png") #broken but worked on previous films
# lens node
m.addCommand("Lens", "nuke.createNode('Lens')", icon="BobukeIcon.png")
print(
    f"nuke.nodePaste({str(get_pipe_path() / 'software/nuke/tools/BobukeTools/toolsets/shotTemplate.nk')})"
)
m.addCommand("SKD Write Node", "make_bobo_write_node()", icon="BobukeIcon.png")
m.addCommand("SKD Open Shot", "choose_shot()", icon="BobukeIcon.png")
m.addCommand("SKD Read Node", "make_bobo_read_node()", icon="BobukeIcon.png")

m.addCommand("SKD FX Read", "make_bobo_fx_read_node()", icon="BobukeIcon.png")
m.addCommand("SKD CFX Read", "make_bobo_cfx_read_node()", icon="BobukeIcon.png")

################################### Nungeon Shelf Tool Buttons ###################################
menu = nuke.menu("Nuke")
menu.addCommand("[Choose Shot]", "choose_shot()")
menu.addCommand("[Import Render Layers]", "import_render_layers()")
menu.addCommand("[Import USD Camera]", "import_USD_cam()")
menu.addCommand("[Set Project Settings]", "set_frameRange_and_aspectRatio()")
