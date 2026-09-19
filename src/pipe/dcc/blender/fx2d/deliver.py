from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import bpy
from bpy.types import Collection, Context, Event, Operator, Scene

from env_sg import DB_Config
from pipe.core.shot import blender_fx2d_stream, shot_owner_for
from pipe.core.shotgrid import ShotGrid
from pipe.core.versioning import save_version
from pipe.dcc.blender.fx2d import scene as fx2d_scene

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

log = logging.getLogger(__name__)

_VERSION = re.compile(r"^V_(\d+)$")


def render_root(shot_root: Path) -> Path:
    """Renders live on /cache under the path the shot has on /groups.

    Nuke's auto-read applies the same rule, and once a shot has a /cache render
    folder it stops looking anywhere else.
    """
    return Path("/cache", *shot_root.parts[2:]) / "render"


def _layers(scene: Scene) -> list[Collection]:
    """Top-level collections to deliver: not pipeline-owned, not hidden, not empty."""
    return [
        collection
        for collection in scene.collection.children
        if collection.name != fx2d_scene.CONTEXT
        and not collection.hide_render
        and collection.all_objects
    ]


def _next_version(layer_dir: Path) -> int:
    numbers = [
        int(match[1])
        for path in layer_dir.glob("V_*")
        if (match := _VERSION.match(path.name))
    ]
    return max(numbers, default=0) + 1


class PIPELINE_OT_fx2d_deliver(Operator):
    """Render every effect layer to a new version for comp."""

    bl_idname = "pipeline.fx2d_deliver"
    bl_label = "Deliver"

    @classmethod
    def poll(cls, context: Context) -> bool:
        if fx2d_scene.shot_root() is None:
            cls.poll_message_set(fx2d_scene.NOT_FX2D_FILE)
            return False
        return True

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        return context.window_manager.invoke_confirm(  # type: ignore
            self,
            event,
            message="Render and deliver every effect layer? Blender is busy until it finishes.",
        )

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = fx2d_scene.shot_root()
        scene, view_layer = context.scene, context.view_layer
        if shot_root is None or scene is None or view_layer is None:
            return {"CANCELLED"}

        if scene.collection.objects:
            names = ", ".join(obj.name for obj in scene.collection.objects)
            self.report(
                {"ERROR"},
                "Could not deliver because these objects are outside every collection "
                f"and would render into every layer: {names}. Move them into a "
                "layer collection.",
            )
            return {"CANCELLED"}
        layers = _layers(scene)
        if not layers:
            self.report(
                {"ERROR"},
                "Could not deliver because no layer collection has anything in it.",
            )
            return {"CANCELLED"}

        versions = {
            layer.name: f"V_{_next_version(render_root(shot_root) / layer.name):02d}"
            for layer in layers
        }
        delivering = ", ".join(
            f"{name} {version}" for name, version in versions.items()
        )

        # Settings first, then save, so the saved file records what was rendered.
        fx2d_scene.apply_render_settings(scene, view_layer)
        bpy.ops.wm.save_mainfile()

        # Versioned before rendering, so every delivered layer can be traced back
        # to the file that drew it even if the render is interrupted.
        shot = ShotGrid.connect(DB_Config).get_shot(code=shot_root.name)
        stream = blender_fx2d_stream(shot, owner=shot_owner_for(shot))
        try:
            save_version(Path(bpy.data.filepath), stream, title=f"Deliver {delivering}")
        except Exception:
            log.exception("Could not version %s before delivering.", bpy.data.filepath)
            self.report(
                {"ERROR"},
                "Could not deliver because a version of this file could not be saved "
                f"under {stream.backup_dir}. Nothing was rendered. Check that you can "
                "write to the shot folder, then try again.",
            )
            return {"CANCELLED"}

        frame_count = scene.frame_end - scene.frame_start + 1
        top_level = [
            collection
            for collection in scene.collection.children
            if collection.name != fx2d_scene.CONTEXT
        ]
        hidden_before = {collection: collection.hide_render for collection in top_level}
        output_before = scene.render.filepath
        try:
            for layer in layers:
                for collection in top_level:
                    collection.hide_render = collection != layer
                layer_dir = render_root(shot_root) / layer.name
                version = versions[layer.name]
                # Nuke reads the highest V_NN as soon as it exists, so frames are
                # rendered into a hidden folder and renamed once all are written.
                hidden = layer_dir / f".{version}"
                if hidden.exists():
                    shutil.rmtree(hidden)
                images = hidden / "images"
                images.mkdir(parents=True)
                # A trailing slash gives digits-only names (0996.exr), which is
                # the only form Nuke's auto-read can find.
                scene.render.filepath = f"{images}/"
                bpy.ops.render.render(animation=True)

                written = len(list(images.glob("*.exr")))
                if written != frame_count:
                    self.report(
                        {"ERROR"},
                        f"Could not deliver {layer.name}: only {written} of "
                        f"{frame_count} frames were rendered. Nothing was delivered "
                        "for this layer; run Deliver again.",
                    )
                    return {"CANCELLED"}
                hidden.rename(layer_dir / version)
        finally:
            for collection, was_hidden in hidden_before.items():
                collection.hide_render = was_hidden
            scene.render.filepath = output_before

        in_front = [
            obj.name
            for layer in layers
            for obj in layer.all_objects
            if obj.type == "GREASEPENCIL" and obj.show_in_front
        ]
        if in_front:
            self.report(
                {"WARNING"},
                "These Grease Pencil objects have In Front on, so holdout geometry "
                f"does not cut them: {', '.join(in_front)}.",
            )
        self.report(
            {"INFO"},
            f"Delivered {delivering} ({frame_count} frames) to "
            f"{render_root(shot_root)}",
        )
        return {"FINISHED"}
