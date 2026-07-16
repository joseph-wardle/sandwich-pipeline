from __future__ import annotations

import logging
import math
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import maya.cmds as mc
from pipe.core.hud import (
    ARTIST,
    HudContent,
    apply_hud,
    labeled_line,
)
from pipe.core.playblast.encoding import build_image_input_chain, encode_movie
from pipe.core.ui.progress import progress_scope
from pipe.core.util.users import resolve_artist_display_name
from mayacapture.capture import capture  # type: ignore[import-not-found]
from Qt import QtWidgets

from pipe.dcc.maya.playblast.turnaround.config import (
    Elevation,
    TurnaroundPass,
    TurnaroundPlayblastConfig,
    _node_uuid,
)
from pipe.dcc.maya.playblast.turnaround.framing import (
    SweptProfile,
    area_weighted_centroid,
    bounding_radius,
    fit_camera,
    pivot_override,
    sample_review_surface,
    swept_profile,
)
from pipe.dcc.maya.util.selection import maintain_selection

# Turnaround-specific HUD labels. Cross-DCC labels (Artist, ...) live in
# :mod:`pipe.core.hud`.
_LABEL_ASSET = "Asset"
_LABEL_POINTS = "Points"

log = logging.getLogger(__name__)

# Flat charcoal
BACKGROUND = (0.161, 0.161, 0.161)


class MTurnaroundPlayblaster:
    """Capture a sequence of asset turnaround passes into one movie."""

    _config: TurnaroundPlayblastConfig

    def configure(self, config: TurnaroundPlayblastConfig) -> MTurnaroundPlayblaster:
        self._config = config
        return self

    def playblast(self, *, parent: QtWidgets.QWidget | None = None) -> None:
        config = self._config
        if not config.review_roots:
            raise ValueError("No review roots were resolved for turnaround export.")

        samples = sample_review_surface(config.review_roots)
        pivot = pivot_override() or area_weighted_centroid(samples)
        profile = swept_profile(samples, pivot)

        steps = [_pass_label(index, p) for index, p in enumerate(config.passes)]
        steps += ["Assembling frames", "Encoding movies"]

        with tempfile.TemporaryDirectory(prefix="skd_turnaround_") as temp_dir:
            temp_root = Path(temp_dir)
            combined_base = temp_root / "turnaround_combined"

            with progress_scope(
                parent=parent,
                title="Turnaround Playblast",
                steps=steps,
            ) as progress:
                with (
                    maintain_selection(),
                    _preserved_current_time(),
                    _held_animation(),
                    _orbiting_turnaround_camera(
                        pivot=pivot,
                        frames_per_pass=config.frames_per_pass,
                        focal_length=config.focal_length,
                    ) as (camera_transform, camera_shape),
                ):
                    pass_bases: list[Path] = []
                    for index, turnaround_pass in enumerate(config.passes):
                        progress.begin_step(
                            _pass_label(index, turnaround_pass),
                            "Rendering frames — this may take a moment...",
                        )
                        _frame_camera_for_pass(
                            camera_transform,
                            camera_shape,
                            profile=profile,
                            pivot=pivot,
                            elevation=turnaround_pass.elevation,
                            aspect=config.width / config.height,
                            padding=config.camera_padding,
                        )
                        pass_base = temp_root / f"turnaround_pass_{index:02d}"
                        self._capture_pass(
                            output_base=pass_base,
                            camera_shape=camera_shape,
                            review_roots=config.review_roots,
                            wireframe_on_shaded=turnaround_pass.wireframe_on_shaded,
                        )
                        pass_bases.append(pass_base)

                progress.begin_step("Assembling frames")
                self._assemble_combined_sequence(pass_bases, combined_base)

                progress.begin_step("Encoding movies", "Running FFmpeg...")
                self._encode_output_movies(combined_base=combined_base)

    def _capture_pass(
        self,
        *,
        output_base: Path,
        camera_shape: str,
        review_roots: tuple[str, ...],
        wireframe_on_shaded: bool,
    ) -> None:
        config = self._config
        capture(
            camera=camera_shape,
            width=config.width,
            height=config.height,
            filename=str(output_base),
            start_frame=1,
            end_frame=config.frames_per_pass,
            format="image",
            compression="png",
            # Keep this capture on-screen. Off-screen (`off_screen=True`) renders
            # an empty frame here: Viewport 2.0's hidden buffer does not draw the
            # isolated turnaround geometry on Linux, and it also drops the
            # wireframe-on-shaded overlay. The shot and previs playblasters get
            # away with off-screen because they render the whole scene unisolated.
            off_screen=False,
            show_ornaments=False,
            overwrite=True,
            maintain_aspect_ratio=False,
            viewer=False,
            isolate=list(review_roots),
            display_options={
                "displayGradient": False,
                "background": BACKGROUND,
            },
            viewport_options={
                "displayAppearance": "smoothShaded",
                "shadows": True,
                # HUD bakes during encode (apply_hud), so the viewport HUD is off.
                "headsUpDisplay": False,
                "wireframeOnShaded": wireframe_on_shaded,
            },
            viewport2_options={
                "multiSampleEnable": True,
                "lineAAEnable": True,
                "ssaoEnable": True,
            },
        )

    def _assemble_combined_sequence(
        self,
        pass_bases: list[Path],
        combined_base: Path,
    ) -> None:
        destination_frame = 1
        for pass_base in pass_bases:
            self._copy_sequence(
                source_base=pass_base,
                destination_base=combined_base,
                source_start=1,
                destination_start=destination_frame,
                frame_count=self._config.frames_per_pass,
            )
            destination_frame += self._config.frames_per_pass

    @staticmethod
    def _copy_sequence(
        *,
        source_base: Path,
        destination_base: Path,
        source_start: int,
        destination_start: int,
        frame_count: int,
    ) -> None:
        for offset in range(frame_count):
            source_frame = source_start + offset
            destination_frame = destination_start + offset
            source_path = source_base.with_name(
                f"{source_base.name}.{source_frame:04d}.png"
            )
            if not source_path.is_file():
                raise FileNotFoundError(f"Missing turnaround frame: {source_path}")

            destination_path = destination_base.with_name(
                f"{destination_base.name}.{destination_frame:04d}.png"
            )
            shutil.copyfile(source_path, destination_path)
            if offset % 10 == 0:
                QtWidgets.QApplication.processEvents()

    def _encode_output_movies(self, *, combined_base: Path) -> None:
        image_pattern = str(combined_base) + ".%04d.png"
        resolution = (self._config.width, self._config.height)
        hud = self._hud_content()

        for preset, output_bases in self._config.output_paths.items():
            if not output_bases:
                continue

            temp_movie_path = combined_base.with_suffix(f".{preset.ext}")
            input_chain = build_image_input_chain(
                image_pattern,
                start_frame=1,
                frame_rate=self._config.frame_rate,
            )
            input_chain = apply_hud(input_chain, hud, resolution)
            encode_movie(
                input_chain,
                output_path=temp_movie_path,
                preset=preset,
                frame_rate=self._config.frame_rate,
                start_frame=1,
            )

            for output_base in output_bases:
                output_path = Path(str(output_base) + f".{preset.ext}")
                output_path.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
                shutil.copyfile(temp_movie_path, output_path)
                QtWidgets.QApplication.processEvents()

    def _hud_content(self) -> HudContent:
        config = self._config
        left_lines = [labeled_line(ARTIST, resolve_artist_display_name())]
        if config.hud_asset_details:
            point_count = _polygon_point_count(config.review_roots)
            left_lines.append(labeled_line(_LABEL_ASSET, config.asset_label))
            left_lines.append(labeled_line(_LABEL_POINTS, f"{point_count:,}"))
        return HudContent(left_lines=tuple(left_lines), frame_start=1)


def _pass_label(index: int, turnaround_pass: TurnaroundPass) -> str:
    mode = "wireframe" if turnaround_pass.wireframe_on_shaded else "shaded"
    return f"Pass {index + 1}: {turnaround_pass.elevation.label} {mode}"


@contextmanager
def _preserved_current_time():
    current_time = int(mc.currentTime(query=True))
    try:
        yield
    finally:
        mc.currentTime(current_time, edit=True)


@contextmanager
def _held_animation() -> Iterator[None]:
    """Freeze the scene's existing animation on the current pose."""
    blocked = [
        curve
        for curve in (mc.ls(type="animCurve") or [])
        if mc.getAttr(f"{curve}.nodeState") == 0
    ]
    for curve in blocked:
        # Blocking: the curve stops driving, holding its last-evaluated value.
        mc.setAttr(f"{curve}.nodeState", 2)  # type: ignore
    try:
        yield
    finally:
        for curve in blocked:
            if mc.objExists(curve):
                mc.setAttr(f"{curve}.nodeState", 0)  # type: ignore


@contextmanager
def _orbiting_turnaround_camera(
    *,
    pivot: tuple[float, float],
    frames_per_pass: int,
    focal_length: float,
) -> Iterator[tuple[str, str]]:
    """Yield a temporary camera that orbits the pivot as the timeline plays.

    The camera hangs under a scene-local group whose rotateY is keyed one
    full revolution per pass. Orbiting the camera instead of spinning the
    geometry leaves the scene untouched: referenced nodes (rigs) cannot be
    reparented under a turntable group, and with the viewport headlight
    following the camera the rendered movie is identical either way.
    """
    orbit_group = str(
        mc.createNode("transform", name=_unique_name("assetTurnaroundOrbit_GRP"))
    )
    try:
        mc.xform(
            orbit_group,
            worldSpace=True,
            translation=(pivot[0], 0.0, pivot[1]),
        )
        camera_name = _unique_name("assetTurnaround_cam")
        camera_transform, camera_shape = mc.camera(name=camera_name)  # type: ignore
        mc.setAttr(f"{camera_shape}.focalLength", focal_length)  # type: ignore
        # Vertical film fit: the rendered height tracks the vertical aperture,
        # so `fit_camera` can frame purely from the vertical field of view.
        mc.setAttr(f"{camera_shape}.filmFit", 2)  # type: ignore
        camera_transform = str(mc.parent(camera_transform, orbit_group)[0])
        _set_linear_orbit_animation(orbit_group, frames_per_pass=frames_per_pass)
        yield camera_transform, str(camera_shape)
    finally:
        if mc.objExists(orbit_group):
            mc.delete(orbit_group)


def _frame_camera_for_pass(
    camera_transform: str,
    camera_shape: str,
    *,
    profile: SweptProfile,
    pivot: tuple[float, float],
    elevation: Elevation,
    aspect: float,
    padding: float,
) -> None:
    phi = math.radians(float(elevation))
    fit = fit_camera(
        profile,
        elevation=phi,
        vertical_fov=_vertical_fov(camera_shape),
        aspect=aspect,
        padding=padding,
    )

    aim = (pivot[0], fit.aim_y, pivot[1])
    position = (
        aim[0],
        aim[1] + fit.distance * math.sin(phi),
        aim[2] - fit.distance * math.cos(phi),
    )
    # At the top, the camera looks straight down, so a Y up vector is degenerate;
    # face the front of the asset toward the top of frame instead.
    world_up = (0.0, 0.0, -1.0) if elevation is Elevation.TOP else (0.0, 1.0, 0.0)

    reach = bounding_radius(profile, fit.aim_y)
    mc.setAttr(f"{camera_shape}.nearClipPlane", max(0.1, fit.distance - 2.0 * reach))  # type: ignore
    mc.setAttr(f"{camera_shape}.farClipPlane", max(fit.distance + 4.0 * reach, 1000.0))  # type: ignore

    _aim_camera(camera_transform, position=position, aim=aim, world_up=world_up)


def _aim_camera(
    camera_transform: str,
    *,
    position: tuple[float, float, float],
    aim: tuple[float, float, float],
    world_up: tuple[float, float, float],
) -> None:
    mc.xform(camera_transform, worldSpace=True, translation=position)
    aim_locator: str = mc.spaceLocator(name=_unique_name("assetTurnaroundAim_LOC"))[0]  # type: ignore
    mc.xform(aim_locator, worldSpace=True, translation=aim)
    constraint: str = mc.aimConstraint(  # type: ignore
        aim_locator,
        camera_transform,
        aimVector=(0, 0, -1),
        upVector=(0, 1, 0),
        worldUpType="vector",
        worldUpVector=world_up,
    )[0]
    # The constraint has done its job orienting the camera; drop it so the
    # next pass can re-aim from scratch.
    mc.delete(constraint, aim_locator)


def _vertical_fov(camera_shape: str) -> float:
    aperture_mm = float(mc.getAttr(f"{camera_shape}.verticalFilmAperture")) * 25.4
    focal_length = float(mc.getAttr(f"{camera_shape}.focalLength"))
    return 2.0 * math.atan(aperture_mm / (2.0 * focal_length))


def _polygon_point_count(review_roots: tuple[str, ...]) -> int:
    mesh_shapes: dict[str, str] = {}
    for root in review_roots:
        for mesh in mc.ls(root, dagObjects=True, long=True, type="mesh") or []:
            mesh_path = str(mesh)
            if not mc.objExists(mesh_path):
                continue
            try:
                if mc.getAttr(f"{mesh_path}.intermediateObject"):
                    continue
            except Exception:
                continue
            mesh_shapes[_node_uuid(mesh_path)] = mesh_path

    point_count = 0
    for mesh_path in mesh_shapes.values():
        try:
            point_count += int(mc.polyEvaluate(mesh_path, vertex=True) or 0)
        except (RuntimeError, ValueError):
            log.warning("Could not evaluate point count for mesh '%s'.", mesh_path)
    return point_count


def _set_linear_orbit_animation(
    orbit_group: str,
    *,
    frames_per_pass: int,
) -> None:
    start_frame = 1
    end_key_frame = frames_per_pass + 1
    # -360: the camera orbits clockwise so the subject appears to spin the
    # same counterclockwise direction the old turntable gave.
    mc.setKeyframe(orbit_group, attribute="rotateY", t=start_frame, v=0.0)  # type: ignore
    mc.setKeyframe(orbit_group, attribute="rotateY", t=end_key_frame, v=-360.0)  # type: ignore
    mc.keyTangent(
        orbit_group,
        attribute="rotateY",
        time=(start_frame, end_key_frame),
        inTangentType="linear",
        outTangentType="linear",
    )


def _unique_name(base_name: str) -> str:
    if not mc.objExists(base_name):
        return base_name

    index = 1
    while True:
        candidate = f"{base_name}{index}"
        if not mc.objExists(candidate):
            return candidate
        index += 1


__all__ = ["MTurnaroundPlayblaster"]
