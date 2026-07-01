from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypeVar

import bpy
from bpy.types import Operator

decorated_operators: set[OperatorDescription] = set()
decorated_classes: set[
    type[
        bpy.types.Panel
        | bpy.types.UIList
        | bpy.types.Menu
        | bpy.types.Header
        | bpy.types.Operator
        | bpy.types.KeyingSetInfo
        | bpy.types.RenderEngine
        | bpy.types.AssetShelf
        | bpy.types.FileHandler
        | bpy.types.PropertyGroup
        | bpy.types.AddonPreferences
        | bpy.types.NodeTree
        | bpy.types.Node
        | bpy.types.NodeSocket
    ]
] = set()

T = TypeVar("T", bound=Operator)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperatorDescription:
    operator: type[Operator]
    add_to_menu: bool


def blender_operator(add_to_menu: bool = False):
    """
    Decorator that tags a blender operator to be loaded in the pipeline addon.
    NOTE: The operator will only be automatically registered if the class has already been imported
    when the Blender pipeline addon is initialized.
    """

    def decorator(cls: type[T]):
        operator_description = OperatorDescription(cls, add_to_menu)
        global decorated_operators
        decorated_operators.add(operator_description)
        return cls

    return decorator


def blender_class(cls):
    global decorated_classes
    decorated_classes.add(cls)
    return cls


def get_decorated_operators() -> set[OperatorDescription]:
    return decorated_operators


def get_decorated_classes() -> (
    set[
        type[
            bpy.types.Panel
            | bpy.types.UIList
            | bpy.types.Menu
            | bpy.types.Header
            | bpy.types.Operator
            | bpy.types.KeyingSetInfo
            | bpy.types.RenderEngine
            | bpy.types.AssetShelf
            | bpy.types.FileHandler
            | bpy.types.PropertyGroup
            | bpy.types.AddonPreferences
            | bpy.types.NodeTree
            | bpy.types.Node
            | bpy.types.NodeSocket
        ]
    ]
):
    return decorated_classes
