"""Bring a hip saved before publish versions up to date as it opens.

Only *Sandwich Kwon Do* has such hips. Please remove this module, its call in
`HFileManager._load_hip_file`, and the load layers 1.0 definition
(`otls/lop_skd.main.load_layers.1.0.hdanc`, with `LOAD_LAYERS_1` in
`util/nodetypes.py`) for future productions.
"""

from __future__ import annotations

import logging
import re
from typing import cast

import hou
from Qt import QtWidgets

from pipe.core.ui import MessageDialog
from pipe.dcc.houdini.hipfile.departments import PUBLISHING_DEPARTMENTS
from pipe.dcc.houdini.util import nodetypes

log = logging.getLogger(__name__)

TITLE = "Hip Updated"

# The USD ROP a shot hip published through, and a set hip.
_PUBLISH_ROPS = ("PUBLISH", "publish_set")
# The load layers node's camera row was named `camera`.
_OLD_CAMERA_TOGGLE = "camera_enable"
_CAMERA_TOGGLE = "cam_enable"

# The nodes artists point at a publish by hand, as a groom does to read the
# animation, and the file parms they have (`filepath1`, `filepath2`, ...).
_READERS = (
    (hou.sopNodeTypeCategory(), "usdimport"),
    (hou.lopNodeTypeCategory(), "sublayer"),
    (hou.lopNodeTypeCategory(), nodetypes.IMPORT_CAMERA),
)
_FILE_PARM = "filepath"
_DEPARTMENTS = "|".join(("anim", *PUBLISHING_DEPARTMENTS))
# Where each publish was, and where its current layer is now.
_MOVED = (
    (re.compile(rf"/({_DEPARTMENTS})/usd/main\.usd$"), r"/\1/publish/\1.usd"),
    (re.compile(r"/anim/usd/spline\.usd$"), "/anim/publish/anim.spline.usd"),
    (re.compile(r"/cam/cam\.usd$"), "/cam/publish/cam.usd"),
)


def upgrade(window: QtWidgets.QWidget | None) -> None:
    """Swap what the open hip holds from before publish versions; tell the artist."""
    changes = [*_swap_load_layers(), *_swap_publish_rops(), *_repoint_readers()]
    if not changes:
        return
    message = "\n".join(
        ["Updated for publish versions:", "", *changes, "", "Save the hip to keep it."]
    )
    MessageDialog(window, message, TITLE).exec_()


def _swap_load_layers() -> list[str]:
    changes = []
    for old in _editable(hou.lopNodeTypeCategory(), nodetypes.LOAD_LAYERS_1):
        camera = old.evalParm(_OLD_CAMERA_TOGGLE)
        # Rows that kept their name keep their toggle. What an artist changed
        # inside an unlocked node is not carried over.
        new = old.changeNodeType(nodetypes.LOAD_LAYERS, keep_network_contents=False)
        cast(hou.Parm, new.parm(_CAMERA_TOGGLE)).set(camera)
        changes.append(f"  {new.path()}: the new load layers node, same rows on")
    return changes


def _swap_publish_rops() -> list[str]:
    changes = []
    for rop in _editable(hou.lopNodeTypeCategory(), "usd_rop"):
        if rop.name() not in _PUBLISH_ROPS:
            continue
        new = rop.changeNodeType(
            nodetypes.PUBLISH, keep_parms=False, keep_network_contents=False
        )
        # The ROP's spare parms come along even so.
        new.removeSpareParms()
        changes.append(f"  {new.path()}: now the publish node")
    return changes


def _repoint_readers() -> list[str]:
    count = 0
    for category, name in _READERS:
        for node in _editable(category, name):
            for parm in node.parms():
                if not parm.name().startswith(_FILE_PARM):
                    continue
                old = parm.rawValue()
                new = _moved(old)
                if new != old:
                    parm.set(new)
                    log.info("%s: %s now reads %s", node.path(), parm.name(), new)
                    count += 1
    if not count:
        return []
    return [f"  paths to old publishes pointed at the new ones: {count}"]


def _moved(path: str) -> str:
    for old, new in _MOVED:
        path = old.sub(new, path)
    return path


def _editable(category: hou.NodeTypeCategory, name: str) -> list[hou.Node]:
    """Nodes of a type, but for those a locked HDA holds, which can't be changed."""
    node_type = cast(hou.NodeType, hou.nodeType(category, name))
    return [node for node in node_type.instances() if not node.isInsideLockedHDA()]
