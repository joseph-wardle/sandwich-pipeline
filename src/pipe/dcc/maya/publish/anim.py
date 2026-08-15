from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Any, Generator

    from pipe.core.shotgrid import Shot

import maya.cmds as mc
from pipe.core.util.paths import get_production_path

from pipe.core.ui import MessageDialog
from pipe.core.struct.timeline import Timeline

from .anim_lock import confirm_anim_republish_allowed
from .namespaces import confirm_publishable
from .publisher import Publisher
from .usdchaser import ExportChaser, ExportChaserMode

log = logging.getLogger(__name__)

CACHE_SET = "rig_geo_grp"

# Frames of preroll spent blending animated channels from their default values
# into the authored animation, so CFX sims warm up from a rig at the origin.
ORIGIN_TRANSITION = 4

_TRS_DEFAULTS: dict[str, float] = {
    "translateX": 0.0,
    "translateY": 0.0,
    "translateZ": 0.0,
    "rotateX": 0.0,
    "rotateY": 0.0,
    "rotateZ": 0.0,
    "scaleX": 1.0,
    "scaleY": 1.0,
    "scaleZ": 1.0,
}


class AnimPublisher(Publisher):
    _PUBLISH_KIND = "anim"

    _shot: Shot
    _timeline: Timeline
    _init_success: bool
    spline_publish: bool

    def __init__(self, spline_publish: bool = False) -> None:
        super().__init__(use_sg_entity=False)
        self.spline_publish = spline_publish

        shot_codes = mc.fileInfo("code", query=True)
        if not shot_codes:
            MessageDialog(
                self._window,
                "Could not detect which shot this scene belongs to. Animation "
                "can only be published from a shot file created through the "
                "pipeline.",
                "Cannot Publish Animation",
            ).exec_()
            self._init_success = False
            return

        self._shot = self._conn.get_shot(code=shot_codes[0])
        self._timeline = Timeline.from_shot(self._shot)
        self._init_success = True

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
        if not cache_sets:
            MessageDialog(
                self._window,
                f"No '{CACHE_SET}' set found in this scene. Animation is "
                "exported from that set — reference at least one character "
                "rig before publishing.",
                "Cannot Publish Animation",
            ).exec_()
            return False

        publishable = confirm_publishable(self._window, cache_sets)
        if not publishable:
            # Stopping here is what keeps the publish safe: `mc.select` with
            # nothing to select is a silent no-op, so falling through would
            # export whatever the artist happened to have selected.
            return False

        mc.select(*publishable, replace=True)

        return True

    def _get_save_path(self) -> Path | None:
        publish_path = get_production_path() / self._shot.shot_path / "anim/usd"
        filename = "main.usd" if not self.spline_publish else "spline.usd"
        return publish_path / filename

    def _do_publish_export(self) -> None:
        # The origin keys exist only while the export runs: every cancel path
        # in `publish()` returns before this, and the undo on exit hands the
        # artist their scene back unchanged.
        with _origin_keyframes(self._timeline.preroll):
            super()._do_publish_export()

    def _get_mayausd_kwargs(self) -> dict[str, Any]:
        chaser_mode = (
            ExportChaserMode.ANIM
            if not self.spline_publish
            else ExportChaserMode.SPLINE_ANIM
        )
        return {
            "chaser": [ExportChaser.ID],
            "chaserArgs": [
                (ExportChaser.ID, "mode", chaser_mode),
                (ExportChaser.ID, "timeline", self._timeline.to_json()),
            ],
            "exportColorSets": False,
            "exportComponentTags": False,
            "exportUVs": False,
            "shadingMode": "none",
            "exportMaterials": False,
            "frameRange": (
                self._timeline.preroll,
                self._timeline.end,
            ),
            "frameStride": 1.0 / self._shot.substeps,
            "stripNamespaces": False,
        }

    def _get_confirm_message(self) -> str:
        return f"Animation has been exported to {self._publish_path}"


@contextmanager
def _origin_keyframes(start_frame: int) -> Generator[None, None, None]:
    """Temporarily key animated TRS channels to their defaults at the start of
    preroll. Every edit lands in one undo chunk that is unwound on exit, so the
    export sees the origin transition and the artist's scene keeps none of it.
    """
    channels = _keyed_trs_channels()
    if not channels:
        # Nothing to key — and undoing an empty chunk would eat the artist's
        # previous edit instead of ours.
        yield
        return

    undo_was_on = mc.undoInfo(query=True, state=True)
    if not undo_was_on:
        mc.undoInfo(state=True)

    mc.undoInfo(openChunk=True, chunkName="animPublishOriginKeys")
    try:
        _key_origin_transition(channels, start_frame)
        yield
    finally:
        mc.undoInfo(closeChunk=True)
        mc.undo()
        if not undo_was_on:
            mc.undoInfo(state=False)


def _keyed_trs_channels() -> list[tuple[str, float]]:
    """TRS plugs driven by time-based anim curves, with their default values."""

    channels: list[tuple[str, float]] = []
    for node in mc.ls(dagObjects=True, type="transform"):
        if not mc.keyframe(node, query=True, name=True):
            continue
        for attr, default in _TRS_DEFAULTS.items():
            curves = cast(
                "list[str]", mc.keyframe(f"{node}.{attr}", query=True, name=True) or []
            )
            if any(
                cast("str", mc.nodeType(c)).startswith("animCurveT") for c in curves
            ):
                channels.append((f"{node}.{attr}", default))
    log.debug("Origin transition: %d keyed TRS channels", len(channels))
    return channels


def _key_origin_transition(channels: list[tuple[str, float]], start_frame: int) -> None:
    for plug, default in channels:
        mc.setKeyframe(plug, time=start_frame + ORIGIN_TRANSITION, insert=True)
        mc.setKeyframe(plug, time=start_frame, value=default)
