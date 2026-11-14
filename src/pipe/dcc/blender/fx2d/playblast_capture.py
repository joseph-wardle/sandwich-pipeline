from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import bpy
from bpy.types import Camera, CameraBackgroundImage, CompositorNodeTree, Scene

from env_sg import DB_Config
from pipe.core.hud import ARTIST, HudContent, labeled_line, line_date, line_shot
from pipe.core.playblast import (
    Destination,
    DestinationId,
    DiskDestination,
    FFmpegPreset,
    PreviewClip,
    ShotEntity,
    ShotGridDestination,
    custom_folder_destination,
    edit_destination,
)
from pipe.core.playblast.encoding import burn_hud_frames
from pipe.core.playblast.tempdir import create_preview_dir
from pipe.core.shot import blender_fx2d_stream
from pipe.core.shotgrid import ShotGrid
from pipe.core.util.users import resolve_artist_display_name
from pipe.core.versioning import list_version_records, version_label
from pipe.dcc.blender.fx2d import util
from pipe.dcc.blender.fx2d.backdrop import BACKDROP

SHOT_FOLDER_ID = DestinationId("shot_folder")
_SAMPLES = 16
_FRAMES = "playblast"
_TREE = "skd_playblast"


@contextmanager
def _overridden(target: Any, **values: object) -> Generator[None]:
    before: dict[str, object] = {}
    try:
        for name, value in values.items():
            before[name] = getattr(target, name)
            setattr(target, name, value)
        yield
    finally:
        for name, value in before.items():
            setattr(target, name, value)


def _hud_content(shot_root: Path, scene: Scene) -> HudContent:
    shot = ShotGrid.connect(DB_Config).get_shot(code=shot_root.name)
    stream = blender_fx2d_stream(shot)
    records = list_version_records(stream)
    latest = version_label(records[0].version) if records else None
    return HudContent(
        left_lines=(
            labeled_line(ARTIST, resolve_artist_display_name()),
            line_shot(shot_root.name, latest, unsaved=bpy.data.is_dirty),
        ),
        right_lines=(line_date(),),
        frame_start=scene.frame_start,
    )


def _destinations(shot_root: Path) -> tuple[Destination, ...]:
    code = shot_root.name
    return (
        ShotGridDestination(entity=ShotEntity(code)),
        DiskDestination(
            id=SHOT_FOLDER_ID,
            name="Shot Folder",
            directory=shot_root / util.DEPARTMENT / "flip",
            preset=FFmpegPreset.WEB,
        ),
        custom_folder_destination(),
        edit_destination(code, util.DEPARTMENT, default_on=False),
    )


def backdrop(camera: Camera) -> CameraBackgroundImage | None:
    """The background image, or None."""
    return next(
        (
            background
            for background in camera.background_images
            if background.image is not None and background.image.name == BACKDROP
        ),
        None,
    )


def _backdrop_tree(background: CameraBackgroundImage) -> CompositorNodeTree:
    """A compositor tree that lays the render over the backdrop."""
    tree = bpy.data.node_groups.new(_TREE, "CompositorNodeTree")
    assert isinstance(tree, CompositorNodeTree) and tree.interface is not None
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    nodes, links = tree.nodes, tree.links
    image: Any = nodes.new("CompositorNodeImage")
    image.image = background.image
    user = background.image_user
    image.frame_start = user.frame_start
    image.frame_offset = user.frame_offset
    image.frame_duration = user.frame_duration
    scale: Any = nodes.new("CompositorNodeScale")
    scale.inputs["Type"].default_value = "Render Size"
    scale.inputs["Frame Type"].default_value = "Fit"
    over = nodes.new("CompositorNodeAlphaOver")
    links.new(image.outputs["Image"], scale.inputs["Image"])
    links.new(scale.outputs["Image"], over.inputs["Background"])
    links.new(
        nodes.new("CompositorNodeRLayers").outputs["Image"], over.inputs["Foreground"]
    )
    links.new(over.outputs["Image"], nodes.new("NodeGroupOutput").inputs[0])
    return tree


def _render_over_backdrop(scene: Scene, background: CameraBackgroundImage) -> None:
    assert scene.eevee is not None
    tree = _backdrop_tree(background)
    try:
        with (
            _overridden(
                scene.render,
                engine="BLENDER_EEVEE",
                film_transparent=True,
                use_compositing=True,
            ),
            _overridden(scene, compositing_node_group=tree),
            _overridden(scene.eevee, taa_render_samples=_SAMPLES),
        ):
            bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        bpy.data.node_groups.remove(tree)


def _render_geometry(scene: Scene) -> None:
    with _overridden(scene.render, film_transparent=False):
        bpy.ops.render.opengl(animation=True, view_context=False)


def _render(
    scene: Scene, directory: Path, background: CameraBackgroundImage | None
) -> None:
    window_manager = bpy.context.window_manager
    assert window_manager is not None
    render = scene.render
    frames = range(scene.frame_start, scene.frame_end + 1)

    def on_frame(_scene: Scene, *_: object) -> None:
        window_manager.progress_update(_scene.frame_current - scene.frame_start + 1)

    bpy.app.handlers.frame_change_post.append(on_frame)
    window_manager.progress_begin(0, len(frames))
    try:
        with (
            _overridden(render, filepath=f"{directory}/{_FRAMES}."),
            _overridden(
                render.image_settings,
                media_type="IMAGE",
                file_format="PNG",
                color_mode="RGB",
                color_depth="8",
            ),
        ):
            if background is None:
                _render_geometry(scene)
            else:
                _render_over_backdrop(scene, background)
    finally:
        window_manager.progress_end()
        bpy.app.handlers.frame_change_post.remove(on_frame)

    written = len(list(directory.glob(f"{_FRAMES}.*.png")))
    if written != len(frames):
        raise RuntimeError(f"Only {written} of {len(frames)} frames were rendered.")


def capture(
    scene: Scene, shot_root: Path, background: CameraBackgroundImage | None
) -> PreviewClip:
    """Renders to a new temp folder and burns the HUD onto the frames."""
    hud = _hud_content(shot_root, scene)
    directory = create_preview_dir()
    _render(scene, directory, background)
    render = scene.render
    scale = render.resolution_percentage / 100
    burn_hud_frames(
        f"{directory}/{_FRAMES}.%04d.png",
        f"{directory}/{_FRAMES}.hud.%04d.png",
        hud,
        (round(render.resolution_x * scale), round(render.resolution_y * scale)),
        start_frame=scene.frame_start,
    )
    return PreviewClip(
        label=shot_root.name,
        frames_dir=directory,
        frames_basename=f"{_FRAMES}.hud",
        frame_start=scene.frame_start,
        frame_end=scene.frame_end,
        fps=round(render.fps / render.fps_base),
        output_prefix=f"{shot_root.name}_{util.DEPARTMENT}",
        destinations=_destinations(shot_root),
    )
