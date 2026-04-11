import logging
from typing import TypeVar

from bpy.types import Operator

decorated_operators: set[type[Operator]] = set()

T = TypeVar("T", bound=Operator)

log = logging.getLogger(__name__)


def blender_operator(cls: type[T]) -> type[T]:
    """
    Decorator that tags a blender operator to be loaded in the pipeline addon.
    NOTE: The operator will only be automatically registered if your function has already been imported when the Blender pipeline addon is initialized.
    """
    global decorated_operators
    decorated_operators.add(cls)
    return cls


def get_decorated_operators() -> set[type[Operator]]:
    return decorated_operators
