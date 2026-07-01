# type: ignore
import hou

node = hou.pwd()

# Add code to modify the stage.
stage = node.editableStage()

# Create HDA Instance
parent = node.parent()

# Replace with HDA definition path
hda_node = parent.createNode(
    "sleister::modify_curves_prerender::1.0", "modify_curves_prerender"
)

# Connect node to upstream
source_node = parent.node("begin_sublayer_fx")
hda_node.setInput(0, source_node)

# Cook it to apply
hda_node.cook(force=True)

# Set display flag
hda_node.setDisplayFlag(True)
