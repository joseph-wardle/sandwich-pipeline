"""The `skdOnOpen` script node: re-runs a file manager's `run_on_open` on each open.

Files carrying the prior project's node names (`lndOnOpen`, `boboOnOpen`) are
migrated to `skdOnOpen` when they next pass through a manager's open flow.
"""

from __future__ import annotations

from typing import Protocol

from maya import cmds as mc

ON_OPEN_NODE_NAME = "skdOnOpen"
_LEGACY_NODE_NAMES = ("lndOnOpen", "boboOnOpen")


class _OnOpenManager(Protocol):
    def run_on_open(self) -> None: ...


def install_on_open_node(manager: _OnOpenManager) -> None:
    cls = type(manager)
    package = cls.__module__.rsplit(".", 1)[0]  # package that re-exports cls

    had_node = mc.objExists(ON_OPEN_NODE_NAME) or any(
        mc.objExists(name) for name in _LEGACY_NODE_NAMES
    )
    for name in _LEGACY_NODE_NAMES:
        if mc.objExists(name):
            mc.delete(name)  # legacy nodes have no afterScript

    if not mc.objExists(ON_OPEN_NODE_NAME):
        mc.scriptNode(
            beforeScript=(
                f"from {package} import {cls.__name__}; {cls.__name__}.run_on_open()"
            ),
            name=ON_OPEN_NODE_NAME,
            scriptType=1,
            sourceType="python",
        )

    if not had_node:  # nothing fired on load, so install hooks now
        manager.run_on_open()
