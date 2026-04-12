from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypeVar

from bpy.types import Operator

decorated_operators: set[OperatorDescription] = set()

T = TypeVar("T", bound=Operator)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperatorDescription:
    operator: type[Operator]
    add_to_menu: bool


def blender_operator(add_to_menu: bool = False):
    """
    Decorator that tags a blender operator to be loaded in the pipeline addon.
    NOTE: The operator will only be automatically registered if your function has already been imported when the Blender pipeline addon is initialized.
    """

    def decorator(cls: type[T]):
        operator_description = OperatorDescription(cls, add_to_menu)
        global decorated_operators
        decorated_operators.add(operator_description)
        return cls

    return decorator


def get_decorated_operators() -> set[OperatorDescription]:
    return decorated_operators
