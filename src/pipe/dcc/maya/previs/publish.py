"""Publish previs shot cameras to `cam/cam.usd` — one shot or the whole sequence.

This is the cam.usd bake only. Breaking out the full RLO scene (meshes, blocking
anim, cut stamps) is a separate operation; see `breakout.py`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import maya.cmds as mc
from env_sg import DB_Config

from pipe.core.shotgrid import Shot, ShotGrid, ShotGridError
from pipe.core.ui import MessageDialog
from pipe.core.util.paths import get_production_path
from pipe.core.util.time_samples import offset_layer

from pipe.dcc.maya.publish.usdchaser import ExportChaser, ExportChaserMode
from pipe.dcc.maya.runtime import get_main_qt_window
from pipe.dcc.maya.util.selection import maintain_selection

from . import cameras, state
from .state import PrevisShot, PrevisState, utcnow_iso

log = logging.getLogger(__name__)

_DIALOG_TITLE = "Publish Shot Cameras"


def publish_shot_camera(previs_shot: PrevisShot, sg_shot: Shot) -> Path:
    """Export the primary to `<shot>/cam/cam.usd`, retimed to start at the shot's `cut_in`."""
    if not previs_shot.primary:
        raise ValueError(f"Previs shot {previs_shot.id} has no primary camera.")
    if sg_shot.cut_in is None:
        raise ValueError(f"ShotGrid shot {sg_shot.code!r} is missing cut_in.")

    camera_shape = cameras.camera_shape_for_namespace(previs_shot.primary)
    if camera_shape is None:
        raise RuntimeError(
            f"Could not find a camera under namespace {previs_shot.primary}."
        )

    span = cameras.camera_animation_range(previs_shot.primary)
    if span is None:
        raise RuntimeError(
            f"Primary camera {previs_shot.primary} has no keyframes; cannot publish."
        )
    primary_start, primary_end = span

    publish_path = get_production_path() / sg_shot.shot_path / "cam" / "cam.usd"
    publish_path.parent.mkdir(parents=True, exist_ok=True)

    with maintain_selection():
        mc.select(camera_shape, replace=True)
        mc.mayaUSDExport(  # type: ignore
            file=str(publish_path),
            selection=True,
            stripNamespaces=True,
            chaser=[ExportChaser.ID],
            chaserArgs=[(ExportChaser.ID, "mode", ExportChaserMode.CAM)],
            frameRange=(primary_start, primary_end),
            frameStride=1.0,
        )

    # Shift exported time samples so cam.usd starts at the shot's canonical cut_in.
    offset_layer(publish_path, float(sg_shot.cut_in) - primary_start)

    previs_shot.cam_animation_hash = cameras.compute_animation_hash(previs_shot.primary)
    return publish_path


def publish_all_shot_cameras(state: PrevisState, conn: ShotGrid) -> list[Path]:
    paths: list[Path] = []
    for shot in state.shots:
        if not shot.shotgrid_code:
            log.info("Skipping unpaired previs shot %s.", shot.id)
            continue
        sg_shot = conn.get_shot(code=shot.shotgrid_code)
        paths.append(publish_shot_camera(shot, sg_shot))
    if paths:
        state.last_published_at = utcnow_iso()
    return paths


def publish_all_shot_cameras_interactive() -> None:
    """Shelf-button entry: read state, connect to ShotGrid, publish, report.

    Wraps `publish_all_shot_cameras` so the artist gets file-not-previs and
    ShotGrid-connection failures as readable dialogs instead of console tracebacks.
    """
    parent = get_main_qt_window()
    current_state = state.read_state()
    if current_state is None:
        MessageDialog(
            parent,
            "Not in a previs file. Open a previs file before publishing shot cameras.",
            _DIALOG_TITLE,
        ).exec_()
        return

    try:
        conn = ShotGrid.connect(DB_Config)
    except ShotGridError as exc:
        log.exception("Could not connect to ShotGrid for previs publish.")
        MessageDialog(
            parent,
            f"Could not connect to ShotGrid.\n\n{exc}",
            _DIALOG_TITLE,
        ).exec_()
        return

    try:
        paths = publish_all_shot_cameras(current_state, conn)
    except Exception as exc:
        log.exception("Previs shot-camera publish failed.")
        MessageDialog(
            parent,
            f"Publish failed.\n\n{exc}",
            _DIALOG_TITLE,
        ).exec_()
        return

    # Persist the updated `last_published_at` + per-shot publish hashes.
    state.write_state(current_state)

    if not paths:
        MessageDialog(
            parent,
            "No shots were published. Assign ShotGrid codes to shots first.",
            _DIALOG_TITLE,
        ).exec_()
        return

    lines = [f"Published {len(paths)} shot{'s' if len(paths) != 1 else ''}:", ""]
    lines.extend(str(p) for p in paths)
    MessageDialog(parent, "\n".join(lines), _DIALOG_TITLE).exec_()
