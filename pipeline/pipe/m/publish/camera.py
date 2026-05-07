from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from Qt.QtCore import QRegExp
from Qt.QtGui import QRegExpValidator
from Qt.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

if TYPE_CHECKING:
    from typing import Any, Sequence

import maya.cmds as mc
from shared.util import get_production_path

from pipe.glui.dialogs import FilteredListDialog
from pipe.shotgrid import SGEntity, Shot

from .publisher import Publisher
from .usdchaser import ExportChaser, ExportChaserMode

log = logging.getLogger(__name__)


class PublishCameraDialog(FilteredListDialog):
    _camera: QComboBox

    def __init__(self, parent: QWidget | None, items: Sequence[str]) -> None:
        super().__init__(
            parent,
            items,
            "Publish Camera",
            "Select a shot to publish the camera for",
            accept_button_name="Publish",
        )

        self._camera = QComboBox(
            self,
        )
        cameras = mc.ls(cameras=True, visible=True)
        self._camera.addItems(cameras)
        self._camera.setCurrentText(cameras[0])
        validator = QRegExpValidator(QRegExp("|".join(cameras)))
        self._camera.setValidator(validator)

        camera_widget = QWidget()
        camera_layout = QHBoxLayout(camera_widget)
        camera_label = QLabel("Camera:")
        camera_layout.addWidget(camera_label, 1)
        camera_layout.addWidget(self._camera, 99)

        self._layout.insertWidget(0, camera_widget)


class CameraPublisher(Publisher):
    _PUBLISH_KIND = "camera"

    def __init__(self) -> None:
        super().__init__(PublishCameraDialog)

    def _get_entity_list(self) -> list[str]:
        return sorted(s.code for s in self._conn.find_shots() if s.code is not None)

    def _get_entity_from_name(self, display_name: str) -> SGEntity | None:
        return self._conn.get_shot(code=display_name)

    def _get_save_path(self) -> Path | None:
        shot = cast(Shot, self._entity)
        return get_production_path() / shot.shot_path / "cam" / "cam.usd"

    def _presave(self) -> bool:
        mc.select(self._camera, replace=True)
        return True

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        shot = cast(Shot, self._entity)
        cut_in, cut_out = shot.frame_range
        start = cut_in - 5
        end = cut_out + 5
        return {
            "chaser": [ExportChaser.ID],
            "chaserArgs": [(ExportChaser.ID, "mode", ExportChaserMode.CAM)],
            "frameRange": (start, end),
            "frameStride": 1.0,
        }

    def _get_confirm_message(self) -> str:
        return f"The camera has been exported to {self._publish_path}"

    @property
    def _camera(self) -> str:
        return cast(PublishCameraDialog, self._dialog)._camera.currentText()
