from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import bpy
import OpenImageIO as oiio
from bpy.types import Camera, Context, Event, Operator

from pipe.dcc.blender.fx2d import util

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

log = logging.getLogger(__name__)

BACKDROP = "backdrop"

# Denoised frames are what comp reads, so they are what the artist should draw over.
_IMAGE_DIRS = ("images_dn", "images")


def latest_frames(layer_dir: Path) -> list[Path]:
    """The frames of the newest version that has any, or [] when none does.

    A version folder exists from the moment a render is submitted, so the newest
    one is often still empty.
    """
    versions = sorted(
        (path for path in layer_dir.glob("v*") if util.VERSION.match(path.name)),
        key=lambda path: int(path.name[1:]),
        reverse=True,
    )
    for version in versions:
        for name in _IMAGE_DIRS:
            frames = sorted((version / name).glob("*.exr"))
            if frames:
                return frames
    return []


def _render_layers(shot_root: Path) -> dict[str, list[Path]]:
    """Render layers with frames, leaving out the effect layers fx2d delivers."""
    root = util.render_root(shot_root)
    if not root.is_dir():
        return {}
    layers = {
        path.name: latest_frames(path)
        for path in sorted(root.iterdir())
        if path.is_dir() and not path.name.startswith(util.LAYER_PREFIX)
    }
    return {name: frames for name, frames in layers.items() if frames}


def label(frames: list[Path]) -> str:
    """`env v006` for frames under `env/v006/images_dn/`."""
    return f"{frames[0].parents[2].name} {frames[0].parents[1].name}"


def _write_proxy(source: Path, target: Path) -> None:
    reader = oiio.ImageInput.open(str(source))
    if reader is None:
        raise OSError(f"Could not read {source}: {oiio.geterror()}")
    spec = reader.spec()
    pixels = reader.read_image(0, 0, 0, 4, oiio.HALF)
    reader.close()

    out_spec = oiio.ImageSpec(spec.width, spec.height, 4, oiio.HALF)
    out_spec.channelnames = ("R", "G", "B", "A")
    out_spec.attribute("compression", "dwaa")
    # Written under a hidden name and renamed, so an interrupted run never leaves a
    # broken frame that a later run would take for finished.
    partial = target.with_name(f".{target.name}")
    writer = oiio.ImageOutput.create(str(partial))
    if writer is None or not writer.open(str(partial), out_spec):
        raise OSError(f"Could not write {partial}: {oiio.geterror()}")
    written = writer.write_image(pixels)
    writer.close()
    if not written:
        raise OSError(f"Could not write {partial}: {writer.geterror()}")
    os.replace(partial, target)


def proxy_frames(shot_root: Path, frames: list[Path]) -> list[Path]:
    """RGBA-only copies of render frames, built the first time they are needed.

    A render frame carries every AOV, which is too heavy for Blender to play back.
    """
    directory = util.backdrop_root(shot_root).joinpath(*frames[0].parts[-4:-1])
    directory.mkdir(parents=True, exist_ok=True)
    proxies = [directory / frame.name for frame in frames]
    stale = [
        (frame, proxy)
        for frame, proxy in zip(frames, proxies)
        # The farm re-renders failed frames into the same version folder.
        if not proxy.exists() or proxy.stat().st_mtime < frame.stat().st_mtime
    ]
    window_manager = bpy.context.window_manager
    assert window_manager is not None
    window_manager.progress_begin(0, len(stale))
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = pool.map(lambda pair: _write_proxy(*pair), stale)
            for done, _ in enumerate(jobs, start=1):
                window_manager.progress_update(done)
    finally:
        window_manager.progress_end()
    return proxies


def set_backdrop(camera: Camera, shot_root: Path, render_frames: list[Path]) -> None:
    """Show a render behind the camera view, replacing the last backdrop.

    Background images are display only: Blender never renders them, so a backdrop
    cannot end up in an effect layer.
    """
    frames = proxy_frames(shot_root, render_frames)
    for background in list(camera.background_images):
        if background.image is not None and background.image.name == BACKDROP:
            camera.background_images.remove(background)
    old = bpy.data.images.get(BACKDROP)
    if old is not None:
        bpy.data.images.remove(old)

    image = bpy.data.images.load(str(frames[0]))
    image.name = BACKDROP
    image.source = "SEQUENCE"
    background = camera.background_images.new()
    background.image = image
    background.alpha = 1.0
    first = int(frames[0].stem)
    user = background.image_user
    user.frame_start = first
    # Frame numbers on disk are scene frames, so scene frame N shows file N.
    user.frame_offset = first - 1
    user.frame_duration = int(frames[-1].stem) - first + 1
    camera.show_background_images = True


# Blender reads enum items from C after this function returns, so the list has to
# outlive the call.
_LAYER_ITEMS: list[tuple[str, str, str]] = []


def _layer_items(self: Operator, context: Context) -> list[tuple[str, str, str]]:
    shot_root = util.shot_root()
    layers = _render_layers(shot_root) if shot_root is not None else {}
    _LAYER_ITEMS[:] = [
        (name, label(frames), str(frames[0].parent)) for name, frames in layers.items()
    ]
    return _LAYER_ITEMS


class SKD_OT_fx2d_set_backdrop(Operator):
    """Show the latest render of one of the shot's layers behind the camera view."""

    bl_idname = "skd.fx2d_set_backdrop"
    bl_label = "Set Backdrop"
    bl_property = "layer"

    layer: bpy.props.EnumProperty(name="Render Layer", items=_layer_items)  # type: ignore

    @classmethod
    def poll(cls, context: Context) -> bool:
        shot_root = util.shot_root()
        if shot_root is None or context.scene is None:
            cls.poll_message_set(util.NOT_FX2D_FILE)
            return False
        if context.scene.camera is None:
            cls.poll_message_set("This scene has no camera to show a backdrop behind.")
            return False
        return True

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        assert shot_root is not None
        # Checked here, not in poll: poll runs on every panel redraw and this reads
        # the render folders over the network.
        if not _render_layers(shot_root):
            self.report(
                {"ERROR"},
                "Could not set a backdrop because this shot has no rendered frames "
                f"under {util.render_root(shot_root)} yet.",
            )
            return {"CANCELLED"}
        context.window_manager.invoke_search_popup(self)  # type: ignore
        return {"RUNNING_MODAL"}

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        scene = context.scene
        assert shot_root is not None and scene is not None and scene.camera is not None
        frames = latest_frames(util.render_root(shot_root) / self.layer)
        camera = scene.camera.data
        assert isinstance(camera, Camera)
        try:
            set_backdrop(camera, shot_root, frames)
        except OSError as error:
            log.exception("Could not build the backdrop proxy for %s.", label(frames))
            self.report(
                {"ERROR"},
                f"Could not set the backdrop because its preview frames could not be "
                f"written under {util.backdrop_root(shot_root)}: {error}",
            )
            return {"CANCELLED"}
        message = (
            f"Backdrop is {label(frames)} ({len(frames)} frames). "
            "Look through the camera to see it."
        )
        first, last = int(frames[0].stem), int(frames[-1].stem)
        if not first <= scene.frame_current <= last:
            # Outside the rendered frames the backdrop is blank, which looks like
            # the tool did nothing.
            scene.frame_current = first
            message += f" Moved to frame {first}, the first rendered frame."
        self.report({"INFO"}, message)
        return {"FINISHED"}
