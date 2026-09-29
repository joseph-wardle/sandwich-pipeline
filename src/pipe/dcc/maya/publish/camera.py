from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from Qt.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

if TYPE_CHECKING:
    from typing import Any, Sequence

import maya.cmds as mc
from pipe.core.util.paths import get_production_path

from pipe.core.ui import FilteredListDialog, MessageDialog, MessageDialogCustomButtons
from pipe.core.shotgrid import SGEntity, Shot

from .publisher import Publisher
from .usdchaser import ExportChaser, ExportChaserMode

log = logging.getLogger(__name__)

_MM_PER_INCH = 25.4  # Maya stores film aperture in inches

_SHOT_ASPECT = 16 / 9
# Loose enough for the old D-sequence rig (OLDShotCam)
_ASPECT_TOLERANCE = 0.01


def _publishable_cameras() -> list[str]:
    """Scene cameras, 16:9 first so the dialog opens on one whenever the scene has one."""
    cameras = [
        camera
        for camera in mc.ls(cameras=True) or []
        if not mc.camera(camera, query=True, startupCamera=True)
    ]
    return sorted(cameras, key=lambda camera: not _has_shot_aspect(camera))


def _shot_camera(cameras: Sequence[str], shot_code: str | None) -> str | None:
    """The camera in the shot's own namespace, e.g. `A_040:shotCamShape`."""
    for camera in cameras:
        leaf = camera.rsplit("|", 1)[-1]
        if leaf.rpartition(":")[0] == shot_code:
            return camera
    return None


def _film_back_mm(camera: str) -> tuple[float, float]:
    width = mc.getAttr(f"{camera}.horizontalFilmAperture") * _MM_PER_INCH
    height = mc.getAttr(f"{camera}.verticalFilmAperture") * _MM_PER_INCH
    return width, height


def _has_shot_aspect(camera: str) -> bool:
    width, height = _film_back_mm(camera)
    return abs(width / height - _SHOT_ASPECT) < _ASPECT_TOLERANCE


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

        self._camera = QComboBox(self)
        self._camera.addItems(_publishable_cameras())

        camera_widget = QWidget()
        camera_layout = QHBoxLayout(camera_widget)
        camera_label = QLabel("Camera:")
        camera_layout.addWidget(camera_label, 1)
        camera_layout.addWidget(self._camera, 99)

        self._layout.insertWidget(0, camera_widget)

    def _on_item_selected(self) -> None:
        # Break-out names each RLO camera after its shot, so picking the shot picks
        # its camera. Scenes without one keep whatever camera is showing.
        cameras = [self._camera.itemText(i) for i in range(self._camera.count())]
        if shot_camera := _shot_camera(cameras, self.get_selected_item()):
            self._camera.setCurrentText(shot_camera)


class CameraPublisher(Publisher):
    _PUBLISH_KIND = "camera"

    def __init__(self) -> None:
        super().__init__(PublishCameraDialog)

    def _prepublish(self) -> bool:
        if _publishable_cameras():
            return True
        MessageDialog(
            self._window,
            "There are no cameras in this scene to publish. Open the shot's "
            "RLO scene, or add a camera, then publish again.",
            "Cannot Publish: No Camera",
        ).exec_()
        return False

    def _get_entity_list(self) -> list[str]:
        return sorted(s.code for s in self._conn.find_shots() if s.code is not None)

    def _get_entity_from_name(self, display_name: str) -> SGEntity | None:
        return self._conn.get_shot(code=display_name)

    def _get_save_path(self) -> Path | None:
        shot = cast(Shot, self._entity)
        return get_production_path() / shot.shot_path / "cam" / "cam.usd"

    def _presave(self) -> bool:
        if not _has_shot_aspect(self._camera) and not self._confirm_off_aspect():
            return False
        mc.select(self._camera, replace=True)
        return True

    def _confirm_off_aspect(self) -> bool:
        width, height = _film_back_mm(self._camera)
        return bool(
            MessageDialogCustomButtons(
                self._window,
                f"{self._camera} has a {width:.2f} x {height:.2f} mm film back "
                f"({width / height:.2f}:1), not 16:9 (1.78:1).\n\n"
                "Shot cameras are normally 16:9. Renders crop this camera to 16:9, "
                "so its viewport frame won't match the final image. "
                "Publish this camera anyway?",
                "Camera Is Not 16:9",
                has_cancel_button=True,
                ok_name="Publish Anyway",
                cancel_name="Cancel",
            ).exec_()
        )

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        shot = cast(Shot, self._entity)
        cut_in, cut_out = shot.frame_range
        start = cut_in - 5
        end = cut_out + 5
        return {
            "chaser": [ExportChaser.ID],
            "chaserArgs": [(ExportChaser.ID, "mode", ExportChaserMode.CAM)],
            "frameRange": (start, end),
            "frameStride": 1.0 / shot.substeps,
        }

    def _get_confirm_message(self) -> str:
        return f"The camera has been exported to {self._publish_path}"

    @property
    def _camera(self) -> str:
        return cast(PublishCameraDialog, self._dialog)._camera.currentText()
