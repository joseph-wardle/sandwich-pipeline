import bpy
from bpy.types import Context

from pipe.b.operator import blender_operator


@blender_operator
class HelloWorld(bpy.types.Operator):
    """Hello World"""

    bl_idname = "wm.pipeline_helloworld"
    bl_label = "Hello World!"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Prints to the console"

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context: Context):
        print("Hello World!")

        return {"FINISHED"}
