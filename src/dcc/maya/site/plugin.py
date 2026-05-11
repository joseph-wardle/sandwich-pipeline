from __future__ import annotations

import logging

from maya import cmds
from maya.api.OpenMaya import MObject
from dcc.maya.command import (
    add_named_command,
    get_decorated_commands,
    register_command_from_description,
)

log = logging.getLogger("dcc.maya.plugin")

PLUGIN_DISPLAY_NAME = "Sandwich Pipeline"
PLUGIN_NAME = "pipeline.py"
COMMAND_PREFIX = "SKD_"
HOTKEY_SET_NAME = "Sandwich_Pipeline"

CUSTOM_HOTKEYS_TO_ADD: dict[str, str] = {"CreateMotionTrail": "ctrl+alt+m"}

maya_useNewAPI = True  # Tell Maya to use the Python API 2.0


# --- Standard Maya plug-in entry points ---
def initializePlugin(plugin: MObject) -> None:
    if not cmds.hotkeySet(HOTKEY_SET_NAME, query=True, exists=True):
        cmds.hotkeySet(HOTKEY_SET_NAME, current=True)
    else:
        cmds.hotkeySet(HOTKEY_SET_NAME, edit=True, current=True)

    for command in get_decorated_commands():
        register_command_from_description(
            command, prefix=COMMAND_PREFIX, base_category="Pipeline", default=True
        )

    for command, hotkey in CUSTOM_HOTKEYS_TO_ADD.items():
        add_named_command(command, command, hotkey)

    log.info(f"{PLUGIN_DISPLAY_NAME} initialized")


def uninitializePlugin(plugin: MObject) -> None:
    log.info(f"{PLUGIN_DISPLAY_NAME} un-initialized")
