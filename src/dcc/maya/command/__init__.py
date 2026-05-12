from __future__ import annotations

import inspect
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from types import FunctionType
from typing import Any

from maya import cmds
from core.util.paths import get_function_source_code_url

decorated_commands: set[CommandDescription] = set()
registered_commands: list[str] = []

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandDescription:
    function: FunctionType
    name: str
    label: str
    description: str | None = None
    category: str | None = None
    hotkey: str | None = None
    icon: str | None = None
    help_url: str | None = None


def maya_command(
    name: str,
    label: str,
    description: str | None = None,
    category: str | None = None,
    hotkey: str | None = None,
    icon: str | None = None,
    help_url: str | None = None,
):
    """
    Decorator that tags a python function as a Maya Command with optional keyboard shortcut and icon.

    Args:
        name: Unique internal command identifier.
        label: Human-readable command name for UI display.
        description: Optional description of the command. Defaults to the
            decorated function's docstring.
        category: Optional UI grouping or menu category.
        hotkey: Optional hotkey string for runtime command binding. (eg. ctrl+alt+b)
        icon: Optional icon name or file path for UI display.
        help_url: Optional link to documentation of the command. If None it this will default to a link to the function source code.
    Returns:
        Callable: The original function, unchanged.

    NOTE: The command will only be automatically registered if your function has already been imported when the Maya pipeline plugin is initialized.
    """

    def decorator(func: FunctionType):
        resolved_description = (
            description if description is not None else inspect.getdoc(func)
        )
        command_description = CommandDescription(
            function=func,
            name=name,
            label=label,
            description=resolved_description,
            category=category,
            hotkey=hotkey,
            icon=icon,
            help_url=help_url,
        )
        global decorated_commands
        decorated_commands.add(command_description)
        return func

    return decorator


def get_decorated_commands() -> set[CommandDescription]:
    return decorated_commands


@contextmanager
def hotkey_set(name: str):
    """Context manager that creates a hotkeySet if it doesn't exist, sets it as current, and restores the previous set on exit."""
    prev_set: str = cmds.hotkeySet(query=True, current=True)  # type: ignore
    try:
        if cmds.hotkeySet(name, query=True, exists=True):
            cmds.hotkeySet(name, current=True)
        else:
            cmds.hotkeySet(name, edit=True, current=True)
        yield
    finally:
        cmds.hotkeySet(prev_set, edit=True, current=True)


def name_command_has_hotkey(name_command: str) -> bool:
    """Returns True if the given nameCommand has any hotkey bound to it."""
    try:
        key = cmds.hotkey(query=True, name=name_command)
        return bool(key)
    except RuntimeError:
        return False


def is_hotkey_assigned(key: str, ctrl=False, alt=False, shift=False) -> bool:
    """
    Returns True if the exact key combination (including modifiers) has a command assigned.
    """
    try:
        annotation = cmds.hotkeyCheck(
            keyString=key.upper() if shift else key,
            ctrlModifier=ctrl,
            altModifier=alt,
        )
        return bool(annotation)
    except RuntimeError:
        return False


def assign_hotkey(
    key: str,
    name_command: str,
    ctrl: bool = False,
    alt: bool = False,
    shift: bool = False,
    command_display_name: str | None = None,
    force=False,
):
    """
    Assigns a hotkey only if the key is not already bound.
    - key: key character (e.g., "d")
    - name_command: nameCommand to assign
    """
    if not force:
        if name_command_has_hotkey(name_command):
            return  # user has already customized this, don't touch it
        if is_hotkey_assigned(key, ctrl, alt, shift):
            mods = []
            if ctrl:
                mods.append("Ctrl")
            if alt:
                mods.append("Alt")
            if shift:
                mods.append("Shift")
            mods.append(key.upper())
            command_name_string = (
                command_display_name
                if command_display_name is not None
                else name_command
            )
            final_hotkey_string = "+".join(mods)

            log.debug(
                f' The pipeline command "{command_name_string}" has a default hotkey {final_hotkey_string},'
                "which was not applied as it is already assigned."
            )
            return  # the hotkey is assigned to something else

    cmds.hotkey(
        keyShortcut=key.upper() if shift else key,
        name=name_command,
        ctrlModifier=ctrl,
        altModifier=alt,
        shiftModifier=shift,
    )


def assign_hotkey_from_string(
    string: str,
    name_command: str,
    command_display_name: str | None = None,
):
    """
    Assigns a hotkey only if the key is not already bound.
    - string: the hotkey string (eg. ctrl+d)
    - name_command: nameCommand to assign
    """
    parts = [p.strip().lower() for p in string.split("+")]
    key = parts[-1]
    modifiers = parts[:-1]
    assign_hotkey(
        key,
        name_command,
        command_display_name=command_display_name,
        ctrl=any(m in ["ctrl", "ctl", "control"] for m in modifiers),
        alt="alt" in modifiers,
        shift="shift" in modifiers,
    )


def add_named_command(command: str, label: str, hotkey: str | None = None):
    # Named Command (hotkeys)
    if hotkey is not None:
        named_command = f"{command}NamedCommand"
        cmds.nameCommand(
            named_command,
            command=command,  # type: ignore
            sourceType="mel",  # runtime commands are always invoked via MEL
            annotation=label,
        )
        if hotkey is not None:
            assign_hotkey_from_string(hotkey, named_command, label)


def register_command_from_description(
    command: CommandDescription,
    default: bool = False,
    prefix: str | None = None,
    base_category: str | None = None,
):
    """
    Registers a Maya Command from a CommandDescription object.

    Args:
        command: the CommandDescription object used to generate the command.
        default: When True the generated command will be flagged as `default` (not-user delete-able, and won't be saved to preferences)
        prefix: When not None the prefix will be added to the beginning of the command name specified in the CommandDescription.
        base_category: When not None the category will have a base category of the specified string.
    """
    command_name: str = (
        f"{prefix}{command.name}" if prefix is not None else command.name
    )
    command_category_levels: list[str] = [
        level.title().replace(" ", "")
        for level in [base_category, command.category]
        if level
    ]

    command_category = (
        ".".join(command_category_levels) if command_category_levels else None
    )

    module = command.function.__module__
    func_name = command.function.__name__

    runtime_command_optional_args: dict[str, Any] = {}
    if command.description:
        runtime_command_optional_args["annotation"] = command.description

    if command.icon:
        runtime_command_optional_args["image"] = command.icon

    if command_category:
        runtime_command_optional_args["category"] = command_category

    if command.help_url:
        runtime_command_optional_args["helpUrl"] = command.help_url
    else:
        source_code_url = get_function_source_code_url(command.function)
        if source_code_url is not None:
            runtime_command_optional_args["helpUrl"] = source_code_url

    command_string = f"import {module}; {module}.{func_name}()"

    if cmds.runTimeCommand(command_name, exists=True):
        is_default: bool = cmds.runTimeCommand(command_name, query=True, default=True)  # type: ignore
        if not is_default:
            cmds.runTimeCommand(command_name, edit=True, delete=True)
        else:
            # Cannot delete default commands; skip re-creation
            log.info(
                "Runtime command '%s' was already present as a default command and couldn't be overwritten.",
                command_name,
            )
            registered_commands.append(command_name)
            add_named_command(command_name, command.label, command.hotkey)
            return

    cmds.runTimeCommand(
        command_name,
        label=command.label,
        commandLanguage="python",
        command=command_string,  # type: ignore
        plugin="plugin",
        default=default,
        **runtime_command_optional_args,
    )
    registered_commands.append(command_name)
    add_named_command(command_name, command.label, command.hotkey)
