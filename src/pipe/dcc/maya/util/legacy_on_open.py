"""Converts the open-hook script nodes older scenes carry.

Delete this module and its call in `on_open` once no scene in the production
holds a node named in `_LEGACY_NODE_NAMES`.
"""

from __future__ import annotations

import re

from maya import cmds as mc

# `skdOnOpen` and the prior projects' names all ran the same script.
_LEGACY_NODE_NAMES = ("skdOnOpen", "lndOnOpen", "boboOnOpen")
# Their module paths changed across renames; the class names never did.
_MANAGER_IMPORT = re.compile(r"import (\w+)")


def adopt_legacy_on_open_node() -> str | None:
    """Delete the scene's open-hook node and return the manager class it named.

    Runs from the after-open callback, which Maya calls before the node's own
    script, so the node never runs and Safe Mode never prompts for it.
    """
    nodes = [name for name in _LEGACY_NODE_NAMES if mc.objExists(name)]
    if not nodes:
        return None
    script = str(mc.scriptNode(nodes[0], query=True, beforeScript=True) or "")
    mc.delete(*nodes)
    match = _MANAGER_IMPORT.search(script)
    return match.group(1) if match else None
