from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pxr import Sdf

if TYPE_CHECKING:
    from typing import Any

    from core.shotgrid import Shot

import maya.cmds as mc
from core.util.paths import get_production_path
from dcc.houdini.launch import HoudiniLauncher

from core.ui.dialogs import MessageDialog
from dcc.maya.util.util import maintain_selection
from core.struct.timeline import Timeline

from .anim_lock import confirm_anim_republish_allowed
from .publisher import Publisher
from .usdchaser import ExportChaser, ExportChaserMode

log = logging.getLogger(__name__)

CACHE_SET = "cache_SET"
PROP_SET = "prop_SET"


class AnimPublisher(Publisher):
    _PUBLISH_KIND = "customanim"

    _shot: Shot
    _init_success: bool

    def __init__(self):
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

    def _prepublish(self) -> bool:
        if not self._init_success:
            return False

        # Check if we want to do a full animation publish or just a rig
        sel = mc.ls(selection=True, long=True)
        if not sel:
            MessageDialog(
                self._window,
                "Please select a rig root to publish.",
                "No Selection",
            ).exec_()
            return False

        self._rig_root = sel[0]

        # Select only this rig
        mc.select(self._rig_root, hierarchy=True)

        if not confirm_anim_republish_allowed(
            parent=self._window,
            sequence_code=self._shot.sequence.code if self._shot.sequence else None,
            shot_code=self._shot.code,
            publish_path=self._get_save_path(),
        ):
            return False
        return True

    def _get_save_path(self) -> Path | None:
        rig_name = self._rig_root.split("|")[-1]  # get the short name
        save_path = (
            get_production_path() / self._shot.shot_path / f"rigs/usd/{rig_name}.usd"
        )
        return save_path

    def _presave(self) -> bool:
        # Make sure only the rig hierarchy is selected
        mc.select(self._rig_root, hierarchy=True)
        return True

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        timeline = Timeline.from_shot(self._shot, preroll_duration=55)
        prop_sets = mc.ls("::" + PROP_SET, sets=True)
        props = dict()
        with maintain_selection():
            for s in prop_sets:
                mc.select(s)
                namespace = s.split(":")[0]
                props[namespace] = [n.split(":")[1] for n in mc.ls(selection=True)]

        return {
            "chaser": [ExportChaser.ID],
            "chaserArgs": [
                (ExportChaser.ID, "mode", ExportChaserMode.ANIM),
                (ExportChaser.ID, "props", json.dumps(props)),
                (ExportChaser.ID, "timeline", timeline.to_json()),
            ],
            "exportColorSets": False,
            "exportComponentTags": False,
            "exportUVs": False,
            "frameRange": (
                timeline.preroll,
                timeline.end,
            ),
            "frameStride": 1.0,
            "shadingMode": "none",
            "stripNamespaces": False,
        }

    def _get_confirm_message(self):
        return f"Animation has been exported to {self._publish_path}"

    def _postpublish(self) -> None:
        """Launch a Houdini process to compute the anim post-process HDA"""
        post_script = ";".join(
            [
                "from dcc.houdini.shot.animpostprocess import AnimPostProcessor",
                f"AnimPostProcessor().run('{self._shot.code}')",
                "exit()",
            ]
        )

        HoudiniLauncher(is_python_shell=True, extra_args=["-c", post_script]).launch()

        root_layer = Sdf.Layer.FindOrOpen(str(self._publish_path))
        root_layer.subLayerPaths.append("post-process.usd")
        root_layer.Save()
