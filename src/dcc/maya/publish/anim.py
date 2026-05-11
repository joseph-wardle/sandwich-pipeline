from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    from core.shotgrid import Shot

import maya.cmds as mc
from core.util.util import get_production_path

from core.glui.dialogs import MessageDialog
from core.struct.timeline import Timeline

from .anim_lock import confirm_anim_republish_allowed
from .publisher import Publisher
from .usdchaser import ExportChaser, ExportChaserMode

log = logging.getLogger(__name__)

CACHE_SET = "rig_geo_grp"


class AnimPublisher(Publisher):
    _PUBLISH_KIND = "anim"

    _shot: Shot
    _init_success: bool

    def __init__(self, spline_publish: bool = False):
        super().__init__(use_sg_entity=False)
        try:
            shot_code = mc.fileInfo("code", query=True)[0]
            self._init_success = True
        except IndexError:
            mc.error("Could not find shot code in fileInfo! Cannot export shot.")
            error = MessageDialog(
                self._window,
                "Error: could not detect shot code. Please reach out to Scott",
            )
            error.exec_()
            self._init_success = False

        self._shot = self._conn.get_shot(code=shot_code)
        self.spline_publish = spline_publish

    def _prepublish(self) -> bool:
        if not self._init_success:
            return False

        if not confirm_anim_republish_allowed(
            parent=self._window,
            sequence_code=self._shot.sequence.code if self._shot.sequence else None,
            shot_code=self._shot.code,
            publish_path=self._get_save_path(),
        ):
            return False

        cache_sets = mc.ls("::" + CACHE_SET, sets=True)
        mc.select(*cache_sets, replace=True)

        return True

    def _get_save_path(self) -> Path | None:
        publish_path = get_production_path() / self._shot.shot_path / "anim/usd"
        filename = "main.usd" if not self.spline_publish else "spline.usd"
        return publish_path / filename

    def _presave(self) -> bool:
        return True

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        timeline = Timeline.from_shot(self._shot, preroll_duration=55)
        chaser_mode = (
            ExportChaserMode.ANIM
            if not self.spline_publish
            else ExportChaserMode.SPLINE_ANIM
        )
        return {
            "chaser": [ExportChaser.ID],
            "chaserArgs": [
                (ExportChaser.ID, "mode", chaser_mode),
                (ExportChaser.ID, "timeline", timeline.to_json()),
            ],
            "exportColorSets": False,
            "exportComponentTags": False,
            "exportUVs": False,
            "shadingMode": "none",
            "exportMaterials": False,
            "frameRange": (
                timeline.preroll,
                timeline.end,
            ),
            "frameStride": 1.0,
            "stripNamespaces": False,
        }

    def _get_confirm_message(self):
        return f"Animation has been exported to {self._publish_path}"

    def _postpublish(self) -> None:
        """Launch a Houdini process to compute the anim post-process HDA"""

        # This might be useful later so I'll leave it here. Currently we aren't using it.

        # post_script = ";".join(
        #     [
        #         "from dcc.houdini.shot.animpostprocess import AnimPostProcessor",
        #         f"AnimPostProcessor().run('{self._shot.code}')",
        #         "exit()",
        #     ]
        # )

        # HoudiniLauncher(is_python_shell=True, extra_args=["-c", post_script]).launch()

        # root_layer = Sdf.Layer.FindOrOpen(str(self._publish_path))
        # root_layer.subLayerPaths.append("post-process.usd")
        # root_layer.Save()
