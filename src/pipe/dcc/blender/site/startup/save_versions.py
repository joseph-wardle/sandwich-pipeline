"""Keep the last 3 saves of every file beside it, as .blend1 to .blend3.

An fx2d file saves itself each time it opens, so Blender's default of 1 loses the
file as it was before a bad reload as soon as it is opened again.
"""

import bpy


def register():
    preferences = bpy.context.preferences
    assert preferences is not None
    preferences.filepaths.save_version = 3


def unregister():
    pass


if __name__ == "__main__":
    register()
