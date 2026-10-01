"""The load layers node: which publish version each of its rows reads."""

from __future__ import annotations

from pathlib import Path

import hou

from pipe.core.publish import current_version, version_layer_path, versions
from pipe.core.shot import current_layer_path

CURRENT = "current"
CAMERA = "cam"

_ENABLE = "{}_enable"
_VERSION = "{}_version"
_VERSION_SUFFIX = _VERSION.format("")


def layer_path(node: hou.Node, row: str) -> str:
    """The layer a row reads."""
    current = _current(node, row)
    pin = _text(node, _VERSION.format(row))
    if pin == CURRENT:
        return str(current)
    return str(version_layer_path(current, int(pin.removeprefix("v"))))


def version_menu(node: hou.Node, row: str) -> list[str]:
    """A row's menu: its department's current layer, then each version to pin to."""
    current = _current(node, row)
    behind = current_version(current)
    label = _name(behind) if behind is not None else "not published"
    menu = [CURRENT, f"Current \N{EM DASH} {label}"]
    for version in versions(current):
        menu += [_name(version), _name(version)]
    return menu


def show_pins(node: hou.Node) -> None:
    """List the pinned rows in the node's comment, so a pin shows in the network."""
    pins = [f"{row} {pin}" for row in _rows(node) if (pin := _pin(node, row))]
    node.setComment("Pinned: " + ", ".join(pins) if pins else "")
    node.setGenericFlag(hou.nodeFlag.DisplayComment, bool(pins))


def camera_missing(node: hou.Node) -> bool:
    """Whether the camera row is on and reads a layer that isn't there."""
    enabled = bool(node.evalParm(_ENABLE.format(CAMERA)))
    return enabled and not Path(layer_path(node, CAMERA)).is_file()


def _current(node: hou.Node, row: str) -> Path:
    return current_layer_path(Path(_text(node, "shot")), row)


def _rows(node: hou.Node) -> list[str]:
    return [
        parm.name().removesuffix(_VERSION_SUFFIX)
        for parm in node.parms()
        if parm.name().endswith(_VERSION_SUFFIX)
    ]


def _pin(node: hou.Node, row: str) -> str | None:
    """The version an enabled row is pinned to."""
    version = _text(node, _VERSION.format(row))
    if version == CURRENT or not node.evalParm(_ENABLE.format(row)):
        return None
    return version


def _text(node: hou.Node, name: str) -> str:
    # evalParm's type covers every kind of parm.
    return str(node.evalParm(name))


def _name(version: int) -> str:
    return f"v{version:03d}"
