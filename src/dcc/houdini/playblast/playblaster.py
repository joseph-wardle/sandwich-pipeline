from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, cast

import hou

from dcc.houdini.playblast.config import DEFAULT_RESOLUTION
from dcc.houdini.playblast.hud import (
    build_hud_filter_args,
    resolve_current_hip_version,
)
from core.playblast import FFmpegPreset, Playblaster
from core.users import resolve_artist_display_name

if TYPE_CHECKING:
    from core.shotgrid import Shot

log = logging.getLogger(__name__)

USE_BEAUTY_ONLY = True
FORCE_ALL_VISIBLE = True
HIDE_CONSTRUCTION_PLANE = True

_GUIDES_TO_DISABLE = (
    "CameraMask",
    "FieldGuide",
    "FillSelections",
    "FloatingGnomon",
    "FollowSelection",
    "GroupList",
    "IKCriticalZone",
    "NodeGuides",
    "NodeHandles",
    "ObjectNames",
    "ObjectPaths",
    "ObjectSelection",
    "OriginGnomon",
    "ParticleGnomon",
    "SafeArea",
    "ShowDrawTime",
    "ViewPivot",
    "XYPlane",
    "XZPlane",
    "YZPlane",
)


class HPlayblaster(Playblaster):
    _camera_path: str | None
    _out_paths: dict[FFmpegPreset, list[Path | str]]
    _shot: Shot
    _tails: tuple[int, int]

    def __init__(self) -> None:
        self._camera_path = None
        self._out_paths = {}
        self._tails = (0, 0)
        try:
            self.fps = int(round(hou.fps()))
        except Exception:
            pass

    def configure(
        self,
        shot: Shot,
        out_paths: dict[FFmpegPreset, list[Path | str]],
        tails: tuple[int, int] = (0, 0),
        camera_path: str | None = None,
    ) -> "HPlayblaster":
        self._shot = shot
        self._out_paths = out_paths
        self._tails = tails
        self._camera_path = camera_path.strip() or None if camera_path else None
        return self

    def _build_ffmpeg_input(
        self, shot: Shot, tempdir: Path, basename: str, start_frame: int
    ) -> Any:
        # HUD bakes into the input filter chain so it propagates to every
        # preset's encode in a single pass. Postprocessing would force a
        # re-encode of every output and was the cause of the silent
        # codec-rewrite bug fixed in the prior PR.
        chain = super()._build_ffmpeg_input(shot, tempdir, basename, start_frame)
        version_label, title = resolve_current_hip_version(shot)
        for filter_kwargs in build_hud_filter_args(
            shot_code=shot.code or "",
            artist_display_name=resolve_artist_display_name(),
            start_frame=start_frame,
            resolution=DEFAULT_RESOLUTION,
            now=datetime.now(),
            version_label=version_label,
            title=title,
        ):
            chain = chain.filter("drawtext", **filter_kwargs)
        return chain

    def _write_images(self, shot: Shot, path: str) -> None:
        cut_in, cut_out = shot.frame_range
        start_frame = cut_in - self._tails[0]
        end_frame = cut_out + self._tails[1]

        scene_viewer, viewport = _scene_viewer_and_viewport()
        flip = scene_viewer.flipbookSettings().stash()
        _configure_flipbook(flip, path, start_frame, end_frame)

        with (
            _applied_viewport_camera(viewport, self._camera_path),
            _clean_viewport(scene_viewer, viewport),
        ):
            _run_flipbook(scene_viewer, viewport, flip)

    def playblast(self) -> None:
        super()._do_playblast(self._shot, self._out_paths, self._tails)


def _scene_viewer_and_viewport() -> tuple[hou.SceneViewer, hou.GeometryViewport]:
    tab = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if tab is None:
        raise RuntimeError("No Scene Viewer found for flipbook export.")

    scene_viewer = cast(hou.SceneViewer, tab)

    # Prefer selected viewport if available; fall back to current.
    sel = getattr(scene_viewer, "selectedViewport", None)
    viewport = None
    if callable(sel):
        try:
            viewport = sel()
        except Exception:
            viewport = None
    viewport = viewport or scene_viewer.curViewport()

    if viewport is None:
        raise RuntimeError("No active viewport found for flipbook export.")

    return scene_viewer, viewport


def _configure_flipbook(
    settings: hou.FlipbookSettings,
    path: str,
    start_frame: int,
    end_frame: int,
) -> None:
    settings.output(f"{path}.$F4.png")
    settings.frameRange((start_frame, end_frame))
    settings.outputToMPlay(False)

    settings.useResolution(True)
    settings.resolution(DEFAULT_RESOLUTION)

    # These are policy choices; keep them grouped.
    settings.outputZoom(100)
    settings.useSheetSize(False)
    settings.cropOutMaskOverlay(True)
    settings.renderAllViewports(False)

    if FORCE_ALL_VISIBLE:
        settings.visibleTypes(hou.flipbookObjectType.Visible)
        settings.visibleObjects("*")

    if USE_BEAUTY_ONLY:
        settings.beautyPassOnly(True)


@contextmanager
def _applied_viewport_camera(
    viewport: hou.GeometryViewport,
    camera_path: str | None,
) -> Iterator[None]:
    if not camera_path:
        yield
        return

    try:
        camera_node = hou.node(camera_path)
    except Exception:
        camera_node = None

    if camera_node is None:
        log.warning(
            "Could not find camera '%s'; using current viewport camera.", camera_path
        )
        yield
        return

    try:
        original_camera_path = viewport.cameraPath()
    except Exception:
        original_camera_path = ""
    try:
        default_camera = viewport.defaultCamera()
    except Exception:
        default_camera = None

    try:
        viewport.setCamera(camera_node.path())
    except Exception:
        log.warning(
            "Could not set viewport camera to '%s'; using current viewport camera.",
            camera_path,
            exc_info=True,
        )
        yield
        return

    try:
        yield
    finally:
        try:
            if original_camera_path:
                viewport.setCamera(original_camera_path)
            elif default_camera is not None:
                viewport.setDefaultCamera(default_camera)
            else:
                viewport.useDefaultCamera()
        except Exception:
            pass


@contextmanager
def _clean_viewport(
    scene_viewer: hou.SceneViewer, viewport: hou.GeometryViewport
) -> Iterator[None]:
    vp_state = _apply_viewport_overrides(viewport)
    sv_state = (
        _apply_scene_viewer_overrides(scene_viewer) if HIDE_CONSTRUCTION_PLANE else None
    )
    try:
        yield
    finally:
        if sv_state is not None:
            _restore_scene_viewer_overrides(scene_viewer, sv_state)
        _restore_viewport_overrides(viewport, vp_state)


def _apply_scene_viewer_overrides(scene_viewer: hou.SceneViewer) -> dict[str, bool]:
    state: dict[str, bool] = {}

    # Perspective "floor grid" is usually the construction plane.
    try:
        cp = scene_viewer.constructionPlane()
        state["cp_visible"] = bool(cp.isVisible())
        cp.setIsVisible(False)
    except Exception:
        pass

    # Some layouts show reference plane too.
    try:
        rp = scene_viewer.referencePlane()
        state["rp_visible"] = bool(rp.isVisible())
        rp.setIsVisible(False)
    except Exception:
        pass

    return state


def _restore_scene_viewer_overrides(
    scene_viewer: hou.SceneViewer, state: dict[str, bool]
) -> None:
    try:
        if "cp_visible" in state:
            scene_viewer.constructionPlane().setIsVisible(state["cp_visible"])
    except Exception:
        pass

    try:
        if "rp_visible" in state:
            scene_viewer.referencePlane().setIsVisible(state["rp_visible"])
    except Exception:
        pass


def _apply_viewport_overrides(
    viewport: hou.GeometryViewport,
) -> dict[str, object] | None:
    try:
        s = viewport.settings()
    except Exception:
        log.warning("Could not access viewport settings for clean playblast.")
        return None

    state: dict[str, object] = {"settings": s, "guides": {}}

    # Ortho grid (not the same as the perspective construction plane grid).
    try:
        state["displayOrthoGrid"] = s.displayOrthoGrid()
        s.setDisplayOrthoGrid(False)
    except Exception:
        pass

    try:
        state["viewMaskOpacity"] = s.viewMaskOpacity()
        s.setViewMaskOpacity(0.0)
    except Exception:
        pass

    _disable_guides(s, state)

    return state


def _restore_viewport_overrides(
    viewport: hou.GeometryViewport, state: dict[str, object] | None
) -> None:
    if not state:
        return
    s = state.get("settings")
    if not isinstance(s, hou.GeometryViewportSettings):
        return

    display_ortho_grid = state.get("displayOrthoGrid")
    if isinstance(display_ortho_grid, bool):
        try:
            s.setDisplayOrthoGrid(display_ortho_grid)
        except Exception:
            pass

    view_mask_opacity = state.get("viewMaskOpacity")
    if isinstance(view_mask_opacity, (int, float)):
        try:
            s.setViewMaskOpacity(float(view_mask_opacity))
        except Exception:
            pass

    _restore_guides(s, state)


def _disable_guides(
    settings: hou.GeometryViewportSettings, state: dict[str, object]
) -> None:
    guide_enum = getattr(hou, "viewportGuide", None)
    if (
        guide_enum is None
        or not hasattr(settings, "guideEnabled")
        or not hasattr(settings, "enableGuide")
    ):
        return

    guides_state: dict[str, bool] = {}
    for name in _GUIDES_TO_DISABLE:
        guide = getattr(guide_enum, name, None)
        if guide is None:
            continue
        try:
            guides_state[name] = bool(settings.guideEnabled(guide))
            settings.enableGuide(guide, False)
        except Exception:
            continue

    state["guides"] = guides_state


def _restore_guides(
    settings: hou.GeometryViewportSettings, state: dict[str, object]
) -> None:
    guide_enum = getattr(hou, "viewportGuide", None)
    guides_state = state.get("guides")
    if (
        guide_enum is None
        or not isinstance(guides_state, dict)
        or not hasattr(settings, "enableGuide")
    ):
        return

    typed_guides_state = cast(dict[str, bool], guides_state)
    for name, enabled in typed_guides_state.items():
        guide = getattr(guide_enum, name, None)
        if guide is None:
            continue
        try:
            settings.enableGuide(guide, bool(enabled))
        except Exception:
            continue


def _run_flipbook(
    scene_viewer: hou.SceneViewer,
    viewport: hou.GeometryViewport,
    settings: hou.FlipbookSettings,
) -> None:
    # Older Houdini builds don't accept the `interactive` (3rd) argument; if
    # the call rejects it with a TypeError, retry with the 2-arg signature.
    # Any other exception (e.g. flipbook write failure) is genuine.
    try:
        scene_viewer.flipbook(viewport, settings, False)
    except TypeError:
        scene_viewer.flipbook(viewport, settings)
