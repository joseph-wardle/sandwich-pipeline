from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import bpy
from bpy.types import Collection, Context, Event, Operator, Scene

from env_sg import DB_Config
from pipe.core.shot import blender_fx2d_stream, shot_owner_for
from pipe.core.shotgrid import ShotGrid
from pipe.core.versioning import save_version
from pipe.dcc.blender.fx2d import util

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

log = logging.getLogger(__name__)


def _layers(scene: Scene) -> list[Collection]:
    """Top-level collections to deliver: not pipeline-owned, not hidden, not empty."""
    return [
        collection
        for collection in scene.collection.children
        if collection.name != util.CONTEXT
        and not collection.hide_render
        and collection.all_objects
    ]


def _next_version(layer_dir: Path) -> str:
    numbers = [
        int(match[1])
        for path in layer_dir.glob("V_*")
        if (match := util.VERSION.match(path.name))
    ]
    return f"V_{max(numbers, default=0) + 1:02d}"


def _uncut(scene: Scene) -> list[str]:
    """Grease Pencil objects the holdout cannot cut: In Front draws over everything."""
    context_collection = scene.collection.children.get(util.CONTEXT)
    holdout = context_collection and context_collection.children.get(util.HOLDOUT)
    if not holdout or not any(obj.type == "MESH" for obj in holdout.all_objects):
        return []
    return [
        obj.name
        for layer in _layers(scene)
        for obj in layer.all_objects
        if obj.type == "GREASEPENCIL" and obj.show_in_front
    ]


class PIPELINE_OT_fx2d_deliver(Operator):
    """Render every effect layer to a new version for comp."""

    bl_idname = "pipeline.fx2d_deliver"
    bl_label = "Deliver"

    @classmethod
    def poll(cls, context: Context) -> bool:
        if util.shot_root() is None:
            cls.poll_message_set(util.NOT_FX2D_FILE)
            return False
        return True

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        assert context.scene is not None
        message = (
            "Render and deliver every effect layer? Blender is busy until it finishes."
        )
        # Asked before rendering: afterwards the uncut layer is already comp's newest.
        uncut = _uncut(context.scene)
        if uncut:
            message += (
                "\nThe holdout will not cut these Grease Pencil objects because they "
                f"have In Front on: {', '.join(uncut)}."
            )
        return context.window_manager.invoke_confirm(self, event, message=message)  # type: ignore

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        scene, view_layer = context.scene, context.view_layer
        assert shot_root is not None and scene is not None and view_layer is not None

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

        layer_dirs = {layer: util.layer_dir(shot_root, layer) for layer in layers}
        versions = {layer: _next_version(layer_dirs[layer]) for layer in layers}
        delivering = ", ".join(
            f"{layer_dirs[layer].name} {versions[layer]}" for layer in layers
        )

        # Settings first, then save, so the saved file records what was rendered.
        util.apply_render_settings(scene, view_layer)
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

        frame_count = len(
            range(scene.frame_start, scene.frame_end + 1, scene.frame_step)
        )
        top_level = [
            collection
            for collection in scene.collection.children
            if collection.name != util.CONTEXT
        ]
        hidden_before = {collection: collection.hide_render for collection in top_level}
        output_before = scene.render.filepath
        try:
            for layer in layers:
                for collection in top_level:
                    collection.hide_render = collection != layer
                layer_dir, version = layer_dirs[layer], versions[layer]
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
                        f"Could not deliver {layer_dir.name}: only {written} of "
                        f"{frame_count} frames were rendered. Nothing was delivered "
                        "for this layer; run Deliver again.",
                    )
                    return {"CANCELLED"}
                hidden.rename(layer_dir / version)
        finally:
            for collection, was_hidden in hidden_before.items():
                collection.hide_render = was_hidden
            scene.render.filepath = output_before

        self.report(
            {"INFO"},
            f"Delivered {delivering} ({frame_count} frames) to "
            f"{util.render_root(shot_root)}",
        )
        return {"FINISHED"}
