"""Force pipeline OCIO display+view on every Blender file load.

Blender's bundled startup.blend bakes in display='sRGB', view='AgX',
output_view='Standard' which dont exist in our config. Blender
auto-remaps the viewport to the closest match (ends up on
ACES 1.0 - SDR Video) but the render output override lands on
Un-tone-mapped, which would silently strip the ACES Output Transform
from saved renders.

This matters to literally nobody. We dont render in blender, and likely never will.

But hey, I went downt his rabbit hole, so thought I would fix this problem that doesn't
exist in case it benefits anyone in the future
"""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from pipe.core.color import DEFAULT_VIEW, DISPLAY


@persistent
def _apply_color_defaults(_dummy) -> None:
    for scene in bpy.data.scenes:
        # These enums are populated at runtime from the loaded OCIO config, so
        # the static bpy stubs (Blender's built-in items only) reject our values.
        scene.display_settings.display_device = DISPLAY  # type: ignore
        scene.view_settings.view_transform = DEFAULT_VIEW  # type: ignore
        scene.render.image_settings.color_management = "FOLLOW_SCENE"


def register():
    bpy.app.handlers.load_post.append(_apply_color_defaults)


def unregister():
    if _apply_color_defaults in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_apply_color_defaults)


if __name__ == "__main__":
    register()
