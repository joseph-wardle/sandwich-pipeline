from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from Qt.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

if TYPE_CHECKING:
    from typing import Any, Sequence

import maya.cmds as mc

from pipe.core.publish.target import Target, shot_target
from pipe.core.shotgrid import Shot
from pipe.core.ui import (
    FilteredListDialog,
    MessageDialog,
    MessageDialogCustomButtons,
    PublishRows,
)
from pipe.dcc.maya.playblast import PrevisPlayblastDialog

from .usdchaser import ExportChaser, ExportChaserMode
from .version import VersionPublisher

log = logging.getLogger(__name__)

DEPARTMENT = "cam"

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
    rows: PublishRows

    def __init__(self, parent: QWidget | None, shots: Sequence[Shot]) -> None:
        self._shots = {shot.code: shot for shot in shots if shot.code is not None}
        super().__init__(
            parent,
            sorted(self._shots),
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

        self.rows = PublishRows("")
        # Above the buttons.
        self._layout.insertWidget(self._layout.count() - 1, self.rows)

    def target(self) -> Target:
        """The version the selected shot's camera becomes."""
        # Publish is enabled only while a shot is selected.
        code = cast(str, self.get_selected_item())
        return shot_target(self._shots[code], DEPARTMENT)

    def camera(self) -> str:
        return self._camera.currentText()

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item() is not None
        self.rows.set_version_label(self.target().label if selected else "")
        # Break-out names each RLO camera after its shot, so picking the shot picks
        # its camera. Scenes without one keep whatever camera is showing.
        cameras = [self._camera.itemText(i) for i in range(self._camera.count())]
        if shot_camera := _shot_camera(cameras, self.get_selected_item()):
            self._camera.setCurrentText(shot_camera)


class CameraPublisher(VersionPublisher):
    _PUBLISH_KIND = "camera"

    def _choose(self) -> bool:
        if not _publishable_cameras():
            MessageDialog(
                self._window,
                "There are no cameras in this scene to publish. Open the shot's "
                "RLO scene, or add a camera, then publish again.",
                "Cannot Publish: No Camera",
            ).exec_()
            return False

        dialog = PublishCameraDialog(self._window, self._conn.find_shots())
        if not dialog.exec_():
            return False
        camera = dialog.camera()
        if not _has_shot_aspect(camera) and not self._confirm_off_aspect(camera):
            return False

        self._target = dialog.target()
        self._choice = dialog.rows.choice()
        mc.select(camera, replace=True)
        return True

    def _confirm_off_aspect(self, camera: str) -> bool:
        width, height = _film_back_mm(camera)
        return bool(
            MessageDialogCustomButtons(
                self._window,
                f"{camera} has a {width:.2f} x {height:.2f} mm film back "
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
        shot = cast(Shot, self._target.entity)
        cut_in, cut_out = shot.frame_range
        start = cut_in - 5
        end = cut_out + 5
        return {
            "chaser": [ExportChaser.ID],
            "chaserArgs": [(ExportChaser.ID, "mode", ExportChaserMode.CAM)],
            "frameRange": (start, end),
            "frameStride": 1.0 / shot.substeps,
        }

    def _open_playblast(self, description: str) -> None:
        # The playblast tool for an RLO scene, where cameras are published from.
        PrevisPlayblastDialog(self._window, description).show()
