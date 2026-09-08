"""Add menu entries for non-plugin third party gizmos."""

import nuke

# (Tab label, gizmo class name) — the class is the .gizmo filename without
# its extension.

# Kuwahara: anisotropic Kuwahara filter, Blink implementation by Derek Flood
# (https://sharktacos.github.io), adapted from the Blender GLSL script.
GIZMOS = [
    ("Kuwahara", "df_kuwahara_map"),
]

_menu = nuke.menu("Nodes").addMenu("SKD")

for _label, _node_class in GIZMOS:
    _menu.addCommand(_label, f"nuke.createNode('{_node_class}')")
