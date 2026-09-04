"""Menu entries for the loose vendored gizmos in `third_party`.

Nuke runs this automatically (GUI sessions only) because site/init.py puts
this folder on the plugin path.

Being on the plugin path is only half of it: that makes `nuke.createNode()`
and reopening saved comps work, but the Tab search reads `nuke.menu("Nodes")`,
so a gizmo needs an addCommand here before it can be tabbed into a script.
The Tab search matches the label, so label things the way an artist would
type them.
"""

import nuke

# (Tab label, gizmo class name) — the class is the .gizmo filename without
# its extension.
#
# Kuwahara: anisotropic Kuwahara filter, Blink implementation by Derek Flood
# (https://sharktacos.github.io), adapted from the Blender GLSL script.
GIZMOS = [
    ("Kuwahara", "df_kuwahara_map"),
]

# addMenu returns the existing SKD menu when SKD_Tools/menu.py has already
# built it, so these land alongside the rest of the show's tools either way.
# No icon: SKD_Tools/images is only added to the plugin path in that folder's
# menu.py, which may not have run yet when this file is executed.
_menu = nuke.menu("Nodes").addMenu("SKD")

for _label, _node_class in GIZMOS:
    _menu.addCommand(_label, f"nuke.createNode('{_node_class}')")
