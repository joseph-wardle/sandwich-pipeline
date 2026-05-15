from __future__ import annotations

import logging
import math
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import maya.cmds as mc
from mayacapture.capture import capture  # type: ignore[import-not-found]
from Qt import QtWidgets

from core.hud import (
    ARTIST,
    HudContent,
    apply_hud,
    labeled_line,
)
from core.ui.progress import progress_scope
from dcc.maya.playblast.turnaround.config import (
    TurnaroundPlayblastConfig,
    _first_parent,
    _node_uuid,
)
from dcc.maya.util.selection import maintain_selection
from core.playblast.encoding import build_image_input_chain, encode_movie
from core.util.users import resolve_artist_display_name

# Turnaround-specific HUD labels. Cross-DCC labels (Artist, ...) live in
# :mod:`core.hud`.
_LABEL_ASSET = "Asset"
_LABEL_POINTS = "Points"

log = logging.getLogger(__name__)

BACKGROUND_COLOR = (0.33, 0.33, 0.33)
BACKGROUND_TOP = (0.42, 0.44, 0.47)
BACKGROUND_BOTTOM = (0.17, 0.17, 0.18)


class MTurnaroundPlayblaster:
    """Capture a shaded and wireframe asset turnaround into one movie."""

    _config: TurnaroundPlayblastConfig

    def configure(self, config: TurnaroundPlayblastConfig) -> MTurnaroundPlayblaster:
        self._config = config
        return self

    def playblast(self, *, parent: QtWidgets.QWidget | None = None) -> None:
        config = self._config
        if not config.review_roots:
            raise ValueError("No review roots were resolved for turnaround export.")

        steps = ["Capturing shaded pass"]
        if config.include_wireframe_pass:
            steps.append("Capturing wireframe pass")
        steps += ["Assembling frames", "Encoding movies"]

        with tempfile.TemporaryDirectory(prefix="skd_turnaround_") as temp_dir:
            temp_root = Path(temp_dir)
            shaded_base = temp_root / "turnaround_shaded"
            wireframe_base = temp_root / "turnaround_wireframe"
            combined_base = temp_root / "turnaround_combined"

            with progress_scope(
                parent=parent,
                title="Turnaround Playblast",
                steps=steps,
            ) as progress:
                with (
                    maintain_selection(),
                    _preserved_current_time(),
                    _staged_turntable_roots(
                        config.review_roots,
                        frames_per_pass=config.frames_per_pass,
                    ) as staged_roots,
                    _temporary_turnaround_camera(
                        staged_roots,
                        focal_length=config.focal_length,
                        camera_padding=config.camera_padding,
                    ) as camera_shape,
                ):
                    progress.begin_step(
                        "Capturing shaded pass",
                        "Rendering frames \u2014 this may take a moment...",
                    )
                    self._capture_pass(
                        output_base=shaded_base,
                        camera_shape=camera_shape,
                        review_roots=staged_roots,
                        wireframe_on_shaded=False,
                    )

                    if config.include_wireframe_pass:
                        progress.begin_step(
                            "Capturing wireframe pass",
                            "Rendering frames \u2014 this may take a moment...",
                        )
                        self._capture_pass(
                            output_base=wireframe_base,
                            camera_shape=camera_shape,
                            review_roots=staged_roots,
                            wireframe_on_shaded=True,
                        )

                progress.begin_step("Assembling frames")
                self._assemble_combined_sequence(
                    shaded_base=shaded_base,
                    wireframe_base=wireframe_base
                    if config.include_wireframe_pass
                    else None,
                    combined_base=combined_base,
                )

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

        viewport_options: dict[str, str | bool] = {
            # Shaded view's documented topology overlay is `wireframeOnShaded`.
            "displayAppearance": "smoothShaded",
            # HUD bakes during encode (apply_hud), so the viewport HUD is off.
            "headsUpDisplay": False,
            "wireframeOnShaded": wireframe_on_shaded,
        }
        if config.use_default_material:
            viewport_options["useDefaultMaterial"] = True
        if config.use_shadows:
            viewport_options["shadows"] = True

        viewport2_options: dict[str, bool] = {}
        if config.use_anti_aliasing:
            viewport2_options.update(
                {
                    "lineAAEnable": True,
                    "multiSampleEnable": True,
                }
            )

        capture(
            camera=camera_shape,
            width=config.width,
            height=config.height,
            filename=str(output_base),
            start_frame=1,
            end_frame=config.frames_per_pass,
            format="image",
            compression="png",
            # Keep this capture on-screen. `mayacapture` does not expose
            # Maya's `offScreenViewportUpdate` flag, and the topology overlay
            # needs a live model panel draw to be reliable.
            off_screen=False,
            show_ornaments=False,
            overwrite=True,
            maintain_aspect_ratio=False,
            viewer=False,
            isolate=list(review_roots),
            display_options={
                "displayGradient": True,
                "background": BACKGROUND_COLOR,
                "backgroundTop": BACKGROUND_TOP,
                "backgroundBottom": BACKGROUND_BOTTOM,
            },
            viewport_options=viewport_options,
            viewport2_options=viewport2_options,
        )

    def _assemble_combined_sequence(
        self,
        *,
        shaded_base: Path,
        wireframe_base: Path | None,
        combined_base: Path,
    ) -> None:
        self._copy_sequence(
            source_base=shaded_base,
            destination_base=combined_base,
            source_start=1,
            destination_start=1,
            frame_count=self._config.frames_per_pass,
        )

        if wireframe_base is None:
            return

        self._copy_sequence(
            source_base=wireframe_base,
            destination_base=combined_base,
            source_start=1,
            destination_start=self._config.frames_per_pass + 1,
            frame_count=self._config.frames_per_pass,
        )

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
        point_count = _polygon_point_count(config.review_roots)
        return HudContent(
            left_lines=(
                labeled_line(ARTIST, resolve_artist_display_name()),
                labeled_line(_LABEL_ASSET, config.asset_label),
                labeled_line(_LABEL_POINTS, f"{point_count:,}"),
            ),
            frame_start=1,
        )


@contextmanager
def _preserved_current_time():
    current_time = int(mc.currentTime(query=True))
    try:
        yield
    finally:
        mc.currentTime(current_time, edit=True)


@contextmanager
def _staged_turntable_roots(
    review_roots: Iterable[str],
    *,
    frames_per_pass: int,
):
    resolved_roots: list[str] = []
    for root in review_roots:
        current_root = _current_node_path(_node_uuid(root))
        if current_root:
            resolved_roots.append(current_root)

    resolved_root_paths = tuple(resolved_roots)
    if not resolved_root_paths:
        raise ValueError("No valid review roots were found in the scene.")

    root_records: list[tuple[str, str | None]] = []
    for root in resolved_root_paths:
        parent = _parent_path(root)
        parent_uuid = _node_uuid(parent) if parent else None
        root_records.append((_node_uuid(root), parent_uuid))

    turntable_group = str(
        mc.createNode("transform", name=_unique_name("assetTurnaroundTurntable_GRP"))
    )
    center = _bounding_box_center(resolved_root_paths)
    mc.xform(turntable_group, worldSpace=True, translation=center)

    try:
        for root_uuid, _ in root_records:
            current_root = _current_node_path(root_uuid)
            if not current_root:
                raise RuntimeError("Could not resolve review root before staging.")
            mc.parent(current_root, turntable_group, absolute=True)

        _set_linear_turntable_animation(
            turntable_group,
            frames_per_pass=frames_per_pass,
        )
        staged_roots = tuple(
            path
            for path in (_current_node_path(root_uuid) for root_uuid, _ in root_records)
            if path
        )
        if len(staged_roots) != len(root_records):
            raise RuntimeError("Could not resolve staged turnaround roots.")
        yield staged_roots
    finally:
        for root_uuid, original_parent_uuid in root_records:
            current_root = _current_node_path(root_uuid)
            if not current_root:
                continue

            original_parent = (
                _current_node_path(original_parent_uuid)
                if original_parent_uuid is not None
                else None
            )
            if original_parent and mc.objExists(original_parent):
                mc.parent(current_root, original_parent, absolute=True)
            else:
                mc.parent(current_root, world=True, absolute=True)

        if mc.objExists(turntable_group):
            mc.delete(turntable_group)


@contextmanager
def _temporary_turnaround_camera(
    review_roots: Iterable[str],
    *,
    focal_length: float,
    camera_padding: float,
):
    bbox = _exact_bounding_box(review_roots)
    center = _bounding_box_center_from_bbox(bbox)
    size_x, size_y, size_z = _bounding_box_size_from_bbox(bbox)
    radius = max(0.5 * math.sqrt(size_x**2 + size_y**2 + size_z**2), 1.0)
    del size_y

    camera_transform, camera_shape = mc.camera(name=_unique_name("assetTurnaround_cam"))  # type: ignore
    aim_locator: str = mc.spaceLocator(name=_unique_name("assetTurnaroundAim_LOC"))[0]  # type: ignore
    aim_constraint = None

    try:
        mc.setAttr(f"{camera_shape}.focalLength", focal_length)  # type: ignore
        distance = _camera_distance_for_radius(
            camera_shape,
            radius=radius,
            padding=camera_padding,
        )
        near_clip = max(0.1, distance - (radius * 2.0))
        far_clip = max(distance + (radius * 4.0), 1000.0)
        mc.setAttr(f"{camera_shape}.nearClipPlane", near_clip)  # type: ignore
        mc.setAttr(f"{camera_shape}.farClipPlane", far_clip)  # type: ignore

        camera_position = (center[0], center[1], center[2] - distance)
        aim_position = center
        mc.xform(camera_transform, worldSpace=True, translation=camera_position)
        mc.xform(aim_locator, worldSpace=True, translation=aim_position)
        aim_constraint = mc.aimConstraint(  # type: ignore
            aim_locator,
            camera_transform,
            aimVector=(0, 0, -1),
            upVector=(0, 1, 0),
            worldUpType="vector",
            worldUpVector=(0, 1, 0),
        )[0]
        yield str(camera_shape)
    finally:
        if aim_constraint and mc.objExists(aim_constraint):  # type: ignore
            mc.delete(aim_constraint)  # type: ignore
        if mc.objExists(aim_locator):
            mc.delete(aim_locator)
        if mc.objExists(camera_transform):
            mc.delete(camera_transform)


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


def _exact_bounding_box(
    nodes: Iterable[str],
) -> tuple[float, float, float, float, float, float]:
    resolved_nodes = list(nodes)
    if not resolved_nodes:
        raise ValueError("Bounding box requires at least one node.")

    min_x, min_y, min_z, max_x, max_y, max_z = mc.exactWorldBoundingBox(resolved_nodes)  # type: ignore
    return (
        float(min_x),
        float(min_y),
        float(min_z),
        float(max_x),
        float(max_y),
        float(max_z),
    )


def _bounding_box_center(nodes: Iterable[str]) -> tuple[float, float, float]:
    return _bounding_box_center_from_bbox(_exact_bounding_box(nodes))


def _bounding_box_center_from_bbox(
    bbox: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float]:
    min_x, min_y, min_z, max_x, max_y, max_z = bbox
    return (
        (min_x + max_x) * 0.5,
        (min_y + max_y) * 0.5,
        (min_z + max_z) * 0.5,
    )


def _bounding_box_size_from_bbox(
    bbox: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float]:
    min_x, min_y, min_z, max_x, max_y, max_z = bbox
    return (
        max(max_x - min_x, 0.001),
        max(max_y - min_y, 0.001),
        max(max_z - min_z, 0.001),
    )


def _camera_distance_for_radius(
    camera_shape: str,
    *,
    radius: float,
    padding: float,
) -> float:
    focal_length = float(mc.getAttr(f"{camera_shape}.focalLength"))
    horizontal_aperture = (
        float(mc.getAttr(f"{camera_shape}.horizontalFilmAperture")) * 25.4
    )
    vertical_aperture = float(mc.getAttr(f"{camera_shape}.verticalFilmAperture")) * 25.4

    horizontal_fov = 2.0 * math.atan(horizontal_aperture / (2.0 * focal_length))
    vertical_fov = 2.0 * math.atan(vertical_aperture / (2.0 * focal_length))
    fit_fov = max(min(horizontal_fov, vertical_fov), 0.001)
    return (radius * padding) / math.sin(fit_fov * 0.5)


def _set_linear_turntable_animation(
    turntable_group: str,
    *,
    frames_per_pass: int,
) -> None:
    start_frame = 1
    end_key_frame = frames_per_pass + 1
    mc.setAttr(f"{turntable_group}.rotateX", 0)  # type: ignore
    mc.setAttr(f"{turntable_group}.rotateY", 0)  # type: ignore
    mc.setAttr(f"{turntable_group}.rotateZ", 0)  # type: ignore
    mc.setKeyframe(turntable_group, attribute="rotateY", t=start_frame, v=0.0)  # type: ignore
    mc.setKeyframe(turntable_group, attribute="rotateY", t=end_key_frame, v=360.0)  # type: ignore
    mc.keyTangent(
        turntable_group,
        attribute="rotateY",
        time=(start_frame, end_key_frame),
        inTangentType="linear",
        outTangentType="linear",
    )


def _current_node_path(node_uuid: str) -> str | None:
    matches = mc.ls(node_uuid, long=True) or []
    if not matches:
        return None
    return str(matches[0])


def _parent_path(node: str) -> str | None:
    return _first_parent(node)


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
