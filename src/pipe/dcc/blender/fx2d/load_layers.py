"""The Load Layers dialog: choose the rows, then reload."""

from __future__ import annotations

from typing import TYPE_CHECKING

import bpy
from bpy.types import Context, Event, Operator, UILayout

from pipe.dcc.blender.fx2d import layers, util
from pipe.dcc.blender.fx2d.reload import ReloadError, assigned_sets, reload, seed_rows

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems


class SKD_OT_fx2d_load_layers(Operator):
    """Choose which of the shot's publishes to import, and which version of each.

    Reloads on OK. Everything it imports cuts the drawing where a stroke passes
    behind it.
    """

    bl_idname = "skd.fx2d_load_layers"
    bl_label = "Load Layers"

    # Edited here and copied to the scene once the reload succeeds, so Cancel and a
    # failed reload leave the file's rows as they were.
    chosen: bpy.props.PointerProperty(  # type: ignore
        type=layers.SKD_PG_fx2d_layers, options={"SKIP_SAVE"}
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        return util.poll_fx2d_file(cls)

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        assert context.scene is not None and shot_root is not None
        layers.copy_layers(layers.scene_layers(context.scene), self.chosen)
        # A file never reloaded has no rows yet: show the ones its reload would give it.
        if not self.chosen.rows:
            seed_rows(shot_root, self.chosen, assigned_sets(shot_root.name) or [])
        # Lists versions published since the dialog last opened.
        layers.forget_versions()
        return context.window_manager.invoke_props_dialog(self, confirm_text="Reload")  # type: ignore

    def draw(self, context: Context) -> None:
        layout = self.layout
        assert layout is not None
        for row in self.chosen.rows:
            if not row.is_set:
                _draw_department(layout, row)
        sets = [row for row in self.chosen.rows if row.is_set]
        if sets:
            layout.separator()
            for row in sets:
                layout.prop(row, "enabled", text=row.name)
        layout.separator()
        column = layout.column(heading="Include")
        for switch in layers.SWITCHES:
            column.prop(self.chosen, switch)

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        scene, view_layer = context.scene, context.view_layer
        assert shot_root is not None and scene is not None and view_layer is not None
        try:
            message = reload(scene, view_layer, shot_root, self.chosen)
        except ReloadError as error:
            self.report(
                {"ERROR"}, f"Could not reload because {error} Nothing was changed."
            )
            return {"CANCELLED"}
        layers.copy_layers(self.chosen, layers.scene_layers(scene))
        self.report({"INFO"}, message)
        return {"FINISHED"}


def _draw_department(layout: UILayout, row: layers.SKD_PG_fx2d_row) -> None:
    split = layout.split(factor=0.4)
    toggle = split.row()
    # Always loaded: it gives the shot its camera and frame range.
    toggle.enabled = row.name != layers.CAMERA
    toggle.prop(row, "enabled", text=row.name)
    split.prop(row, "version", text="")
