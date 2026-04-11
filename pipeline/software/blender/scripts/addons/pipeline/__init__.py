import logging

from bpy.types import Operator
from bpy.utils import register_class, unregister_class
from pipe.b.operator import get_decorated_operators

bl_info = {"name": "Sandwich Pipeline", "blender": (5, 0, 1), "category": "Pipeline"}

registered_operators: set[type[Operator]] = set()

log = logging.getLogger("pipe.b.addon")


def register():
    global registered_operators
    registered_operators = get_decorated_operators()
    for operator in registered_operators:
        register_class(operator)
        log.debug(f"{operator} registered as operator.")
    log.info("Pipeline addon loaded!")


def unregister():
    for operator in registered_operators:
        unregister_class(operator)
    log.info("Pipeline addon unloaded!")


if __name__ == "__main__":
    register()
