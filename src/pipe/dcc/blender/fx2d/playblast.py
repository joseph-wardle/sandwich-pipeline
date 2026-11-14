"""Playblast: render the strokes over the backdrop or the geometry, play them, send them on.

Nothing persists until the artist presses Send. The preview is a second Blender
window whose Sequencer plays the HUD-burned frames, with the destinations in its
sidebar. Send hands the frames to the shared playblast engine on a thread,
reporting to the main window's status bar.
"""

from __future__ import annotations

import functools
import logging
import queue
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import attrs
import bpy
from bpy.app.handlers import persistent
from bpy.types import (
    Area,
    Camera,
    CameraBackgroundImage,
    CompositorNodeTree,
    Context,
    Event,
    Operator,
    Panel,
    PropertyGroup,
    Scene,
    SpaceSequenceEditor,
    Window,
)

from env_sg import DB_Config
from pipe.core.hud import ARTIST, HudContent, labeled_line, line_date, line_shot
from pipe.core.playblast import (
    ChosenDestination,
    ChosenDisk,
    ChosenShotGrid,
    ConfirmResult,
    Destination,
    DestinationId,
    DiskDestination,
    FFmpegPreset,
    PreviewClip,
    ShotEntity,
    ShotGridDestination,
    confirm_clip,
    custom_folder_destination,
    edit_destination,
)
from pipe.core.playblast.confirm import failure_summary
from pipe.core.playblast.encoding import burn_hud_frames
from pipe.core.playblast.errors import artist_reason
from pipe.core.playblast.review.playlists import list_review_playlists
from pipe.core.playblast.tempdir import create_preview_dir
from pipe.core.shot import blender_fx2d_stream
from pipe.core.shotgrid import ShotGrid
from pipe.core.util.users import resolve_artist_display_name
from pipe.core.versioning import (
    current_version_label,
    list_version_records,
    version_label,
)
from pipe.dcc.blender.fx2d import util
from pipe.dcc.blender.fx2d.backdrop import BACKDROP

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

log = logging.getLogger(__name__)

PREVIEW = "skd_playblast"
SHOT_FOLDER_ID = DestinationId("shot_folder")
# Enough to anti-alias strokes; more samples barely change a frame's render time,
# which is mostly evaluating the scene.
_SAMPLES = 16
_FRAMES = "playblast"
_NO_PLAYLIST = ("NONE", "None", "", 0)


@attrs.define(eq=False)
class _Preview:
    clip: PreviewClip
    workspace: str
    # The preview window's workspace's sequencer scene before the preview.
    sequencer_scene: str | None
    # Set by a Send that failed somewhere, so the retry keeps its version.
    basename: str | None = None
    # Unticked for the retry; ticked again once the preview closes, so the
    # next playblast starts with what the artist chose.
    delivered: set[str] = attrs.field(factory=set)


_preview: _Preview | None = None

# Enum items are read from C after the callback returns, so they live here.
_playlists: list[tuple[str, str, str, int]] = [_NO_PLAYLIST]
_playlist_error = ""


@attrs.define
class _Send:
    """One Send running on a thread."""

    preview: _Preview
    steps: queue.SimpleQueue[str] = attrs.field(factory=queue.SimpleQueue)
    result: ConfirmResult | None = None
    error: str = ""
    thread: threading.Thread | None = None


_send: _Send | None = None


def _sending() -> bool:
    return _send is not None and _send.thread is not None and _send.thread.is_alive()


# ----------------------------------------------------------------------
# Capture
# ----------------------------------------------------------------------


@contextmanager
def _overridden(target: Any, **values: object) -> Generator[None]:
    """Sets attributes of a Blender struct, restoring them on the way out."""
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
    """Artist and the fx2d.blend version the last Deliver saved, `*` once it has changed."""
    shot = ShotGrid.connect(DB_Config).get_shot(code=shot_root.name)
    stream = blender_fx2d_stream(shot)
    records = list_version_records(stream)
    latest = version_label(records[0].version) if records else None
    matching, _ = current_version_label(stream, Path(bpy.data.filepath))
    return HudContent(
        left_lines=(
            labeled_line(ARTIST, resolve_artist_display_name()),
            line_shot(
                shot_root.name,
                latest,
                unsaved=bpy.data.is_dirty or matching != latest,
            ),
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
        # Off: edit has one version counter per shot, so this would replace the
        # shot's newer delivery from another department in the cut.
        edit_destination(code, util.DEPARTMENT, default_on=False),
    )


def _backdrop(camera: Camera) -> CameraBackgroundImage | None:
    """The background image Set Backdrop added, or None before it has run."""
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
    tree = bpy.data.node_groups.new(PREVIEW, "CompositorNodeTree")
    assert isinstance(tree, CompositorNodeTree) and tree.interface is not None
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    nodes, links = tree.nodes, tree.links
    image: Any = nodes.new("CompositorNodeImage")
    image.image = background.image
    user = background.image_user
    image.frame_start = user.frame_start
    image.frame_offset = user.frame_offset
    image.frame_duration = user.frame_duration
    # The proxy of a 4K render is 4K; fit it to the frame as the camera view does.
    scale = nodes.new("CompositorNodeScale")
    scale.inputs["Type"].default_value = "Render Size"  # type: ignore
    scale.inputs["Frame Type"].default_value = "Fit"  # type: ignore
    over = nodes.new("CompositorNodeAlphaOver")
    links.new(image.outputs["Image"], scale.inputs["Image"])
    links.new(scale.outputs["Image"], over.inputs["Background"])
    links.new(
        nodes.new("CompositorNodeRLayers").outputs["Image"], over.inputs["Foreground"]
    )
    links.new(over.outputs["Image"], nodes.new("NodeGroupOutput").inputs[0])
    return tree


def _render_over_backdrop(scene: Scene, background: CameraBackgroundImage) -> None:
    """EEVEE, the strokes cut by the holdout as comp gets them; Workbench ignores it.

    Background images never render, so the backdrop is laid under the strokes in
    the compositor. Before the view transform, it looks as it does behind the camera
    and as comp has it.
    """
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
    """Workbench, as the viewport draws it: the context meshes solid grey, hiding
    the strokes behind them by depth, as in a Houdini playblast."""
    with _overridden(scene.render, film_transparent=False):
        bpy.ops.render.opengl(animation=True, view_context=False)


def _render(
    scene: Scene, directory: Path, background: CameraBackgroundImage | None
) -> None:
    """Renders the shot range over the backdrop, or against the geometry for None."""
    window_manager = bpy.context.window_manager
    assert window_manager is not None
    render = scene.render
    frames = range(scene.frame_start, scene.frame_end + 1)

    # Not render_write: Workbench renders never call it.
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


def _capture(
    scene: Scene, shot_root: Path, background: CameraBackgroundImage | None
) -> PreviewClip:
    """Renders to a new temp folder and burns the HUD onto the frames."""
    # Read before rendering, so the HUD describes the file as it was captured.
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


# ----------------------------------------------------------------------
# Preview window
# ----------------------------------------------------------------------


def _preview_scene(source: Scene, clip: PreviewClip) -> Scene:
    """A scene whose Sequencer plays the clip's frames."""
    scene = bpy.data.scenes.new(PREVIEW)
    scene.frame_start, scene.frame_end = clip.frame_start, clip.frame_end
    scene.frame_current = clip.frame_start
    scene.render.fps, scene.render.fps_base = clip.fps, 1.0
    scene.render.resolution_x = source.render.resolution_x
    scene.render.resolution_y = source.render.resolution_y
    scene.render.resolution_percentage = source.render.resolution_percentage
    scene.sync_mode = "FRAME_DROP"
    editor = scene.sequence_editor_create()
    frames = sorted(clip.frames_dir.glob(f"{clip.frames_basename}.*.png"))
    strip: Any = editor.strips.new_image(
        clip.label, str(frames[0]), 1, clip.frame_start
    )
    for frame in frames[1:]:
        strip.elements.append(frame.name)
    # The frames went through the view transform as they rendered. Shown as data
    # through no view, they look exactly like the movie they become.
    strip.colorspace_settings.name = "Raw"
    view_settings = scene.view_settings
    assert view_settings is not None
    # The stubs list only the "NONE" view; the views come from the OCIO config.
    view_settings.view_transform = "Raw"  # type: ignore
    return scene


def _region(area: Area, kind: str) -> Any:
    return next(region for region in area.regions if region.type == kind)


def _lay_out(window: Window) -> Area:
    """One Sequencer preview over a timeline. Returns the preview area."""
    screen = window.screen
    assert screen is not None
    for area in list(screen.areas):
        if area.type in ("PROPERTIES", "OUTLINER"):
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.screen.area_close()
    for area in screen.areas:
        if area.type == "DOPESHEET_EDITOR":
            area.ui_type = "TIMELINE"
    area = max(screen.areas, key=lambda area: area.width * area.height)
    area.type = "SEQUENCE_EDITOR"
    space = area.spaces.active
    assert isinstance(space, SpaceSequenceEditor)
    space.view_type = "PREVIEW"
    space.show_region_ui = True
    # The timeline below already has the playback controls.
    space.show_region_footer = False
    return area


def _show_skd_tab() -> float | None:
    """A timer, repeating until the preview's sidebar shows the SKD tab.

    The tab is read-only, or quietly reset, until the sidebar has drawn with its
    tabs, which a busy Blender may not have done yet. Stops once the preview closes.
    """
    window_manager = bpy.context.window_manager
    assert window_manager is not None
    for window in window_manager.windows:
        if window.scene is None or window.scene.name != PREVIEW:
            continue
        assert window.screen is not None
        for area in window.screen.areas:
            if area.type != "SEQUENCE_EDITOR":
                continue
            sidebar = _region(area, "UI")
            try:
                sidebar.active_panel_category = "SKD"
            except AttributeError:
                return 0.2
            return None if sidebar.active_panel_category == "SKD" else 0.2
    return None


def _open_preview(context: Context, clip: PreviewClip) -> None:
    global _preview
    assert context.scene is not None and context.window_manager is not None
    scene = _preview_scene(context.scene, clip)
    with context.temp_override(window=context.window):
        bpy.ops.wm.window_new_main()
    window = context.window_manager.windows[-1]
    # A window opened beside another is independent only when it is a main
    # window, so playing it leaves the artist's frame alone.
    window.scene = scene
    workspace = window.workspace
    assert workspace is not None
    # The Sequencer shows the workspace's sequencer scene, not the window's.
    before = workspace.sequencer_scene
    _preview = _Preview(clip, workspace.name, before.name if before else None)
    workspace.sequencer_scene = scene

    area = _lay_out(window)
    with context.temp_override(
        window=window, area=area, region=_region(area, "WINDOW")
    ):
        bpy.ops.skd.fx2d_playblast_watch("INVOKE_DEFAULT")  # type: ignore
        bpy.ops.screen.animation_play()
    bpy.app.timers.register(_show_skd_tab, first_interval=0.2)


def _close_preview() -> None:
    """Closes the preview window and removes its scene. Safe to call twice."""
    window_manager = bpy.context.window_manager
    assert window_manager is not None
    for window in list(window_manager.windows):
        if window.scene is not None and window.scene.name == PREVIEW:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.window_close()
    scene = bpy.data.scenes.get(PREVIEW)
    if scene is not None:
        bpy.data.scenes.remove(scene)


def _remove_preview() -> None:
    """Closes the preview on screen, putting back its workspace's sequencer scene."""
    global _preview
    if _preview is None:
        return
    _close_preview()
    for id in _preview.delivered:
        setattr(_props(bpy.context), id, True)
    workspace = bpy.data.workspaces.get(_preview.workspace)
    if workspace is not None:
        name = _preview.sequencer_scene
        workspace.sequencer_scene = bpy.data.scenes.get(name) if name else None
    _preview = None


def _remove_if_current(preview: _Preview | None) -> None:
    if preview is _preview:
        _remove_preview()


@persistent
def remove_saved_preview(_path: str) -> None:
    """A `load_post` handler: forgets the last file's preview, and closes the
    one a file saved with a preview open reopens with."""
    global _preview
    _preview = None
    _close_preview()


def _fetch_playlists() -> None:
    """Runs on a thread: the newest review playlists, for the Playlist menu."""
    global _playlist_error
    try:
        options = list_review_playlists(limit=50)
    except Exception as exc:
        log.exception("Could not list ShotGrid review playlists.")
        _playlist_error = artist_reason(exc)
        return
    _playlist_error = ""
    _playlists[:] = [
        _NO_PLAYLIST,
        *((str(o.playlist_id), o.display_name, "", o.playlist_id) for o in options),
    ]


def _playlist_items(
    self: PropertyGroup, context: Context | None
) -> list[tuple[str, str, str, int]]:
    return _playlists


class SKD_PG_fx2d_playblast(PropertyGroup):
    """The routing choices. Kept on the window manager, which is never saved."""

    # Named by destination id, so the panel and Send find them from the clip.
    shotgrid: bpy.props.BoolProperty(name="ShotGrid", default=True)  # type: ignore
    shot_folder: bpy.props.BoolProperty(name="Shot Folder", default=True)  # type: ignore
    custom_folder: bpy.props.BoolProperty(name="Custom Folder", default=False)  # type: ignore
    edit: bpy.props.BoolProperty(name="Send to Edit", default=False)  # type: ignore
    # Numbered by playlist id, so a pick survives the list being fetched again.
    playlist: bpy.props.EnumProperty(name="Playlist", items=_playlist_items)  # type: ignore
    description: bpy.props.StringProperty(name="Description")  # type: ignore
    custom_folder_path: bpy.props.StringProperty(  # type: ignore
        name="Folder", subtype="DIR_PATH"
    )


def _camera(context: Context) -> Camera:
    assert context.scene is not None and context.scene.camera is not None
    camera = context.scene.camera.data
    assert isinstance(camera, Camera)
    return camera


def _props(context: Context) -> SKD_PG_fx2d_playblast:
    return context.window_manager.skd_fx2d_playblast  # type: ignore


class SKD_OT_fx2d_playblast(Operator):
    """Render the shot and play it, ready to send"""

    bl_idname = "skd.fx2d_playblast"
    bl_label = "Playblast"

    # Not remembered: each Playblast starts from what the camera shows.
    show: bpy.props.EnumProperty(  # type: ignore
        name="Show",
        items=(
            (
                "BACKDROP",
                "Backdrop",
                "The strokes over the backdrop, as comp will have them",
            ),
            (
                "GEOMETRY",
                "Geometry",
                "The strokes against the shot's geometry in grey, as in a Houdini playblast",
            ),
        ),
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        if not util.poll_fx2d_file(cls):
            return False
        if context.scene is None or context.scene.camera is None:
            cls.poll_message_set("This scene has no camera to playblast through.")
            return False
        return True

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        camera = _camera(context)
        if _backdrop(camera) is None:
            self.show = "GEOMETRY"
            return self.execute(context)
        self.show = "BACKDROP" if camera.show_background_images else "GEOMETRY"
        window_manager = context.window_manager
        assert window_manager is not None
        return window_manager.invoke_props_dialog(self, confirm_text="Playblast")

    def draw(self, context: Context) -> None:
        assert self.layout is not None
        self.layout.prop(self, "show", expand=True)

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        shot_root = util.shot_root()
        scene = context.scene
        assert shot_root is not None and scene is not None
        background = _backdrop(_camera(context)) if self.show == "BACKDROP" else None
        _remove_preview()
        _props(context).description = ""
        threading.Thread(target=_fetch_playlists, daemon=True).start()
        try:
            clip = _capture(scene, shot_root, background)
        except Exception as exc:
            log.exception("Playblast of %s failed.", bpy.data.filepath)
            self.report(
                {"ERROR"}, f"Could not playblast {shot_root.name}. {artist_reason(exc)}"
            )
            return {"CANCELLED"}
        _open_preview(context, clip)
        return {"FINISHED"}


class SKD_OT_fx2d_playblast_watch(Operator):
    """Lives in the preview window: Blender cancels it when the window closes."""

    bl_idname = "skd.fx2d_playblast_watch"
    bl_label = "Watch Playblast Preview"
    bl_options = {"INTERNAL"}

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        self.preview = _preview
        assert context.window_manager is not None
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        return {"PASS_THROUGH"}

    def cancel(self, context: Context) -> None:
        # The window is mid-close here; removing its scene now would pull it out
        # from under Blender.
        bpy.app.timers.register(functools.partial(_remove_if_current, self.preview))


class SKD_OT_fx2d_playblast_discard(Operator):
    """Close the preview without sending it"""

    bl_idname = "skd.fx2d_playblast_discard"
    bl_label = "Discard"

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        # After this operator returns: it runs inside the window being closed.
        bpy.app.timers.register(functools.partial(_remove_if_current, _preview))
        return {"FINISHED"}


def _folder(destination: DiskDestination, context: Context) -> Path:
    path = _props(context).custom_folder_path.strip()
    if destination.browsable and path:
        return Path(bpy.path.abspath(path))
    return destination.directory


def _chosen(clip: PreviewClip, context: Context) -> tuple[ChosenDestination, ...]:
    props = _props(context)
    chosen: list[ChosenDestination] = []
    for destination in clip.destinations:
        if not getattr(props, destination.id):
            continue
        if isinstance(destination, ShotGridDestination):
            playlist = props.playlist
            chosen.append(
                ChosenShotGrid(
                    destination,
                    playlist_id=int(playlist) if playlist.isdigit() else None,
                    description=props.description.strip() or None,
                )
            )
        elif isinstance(destination, DiskDestination):
            chosen.append(ChosenDisk(destination, _folder(destination, context)))
    return tuple(chosen)


def _folders(clip: PreviewClip, context: Context) -> tuple[Path, ...]:
    """Every folder the version number must be new in, ticked or not, as the Qt viewer does."""
    return tuple(
        _folder(destination, context)
        for destination in clip.destinations
        if isinstance(destination, DiskDestination)
    )


def _run_send(
    send: _Send, chosen: tuple[ChosenDestination, ...], folders: tuple[Path, ...]
) -> None:
    clip = send.preview.clip
    names = {destination.id: destination.name for destination in clip.destinations}
    try:
        send.result = confirm_clip(
            clip,
            chosen,
            folders,
            basename=send.preview.basename,
            on_step=lambda id, step: send.steps.put(f"{names[id]}: {step}"),
        )
    except Exception as exc:
        log.exception("Sending the playblast of %s failed.", clip.label)
        send.error = artist_reason(exc)


class SKD_OT_fx2d_playblast_send(Operator):
    """Send the playblast to every ticked destination. Blender stays usable while it uploads"""

    bl_idname = "skd.fx2d_playblast_send"
    bl_label = "Send"

    @classmethod
    def poll(cls, context: Context) -> bool:
        if _preview is None:
            return False
        props = _props(context)
        if _sending():
            cls.poll_message_set("The last playblast is still sending.")
            return False
        if not any(getattr(props, d.id) for d in _preview.clip.destinations):
            cls.poll_message_set("Tick at least one destination.")
            return False
        if props.custom_folder and not props.custom_folder_path.strip():
            cls.poll_message_set("Choose a folder for Custom Folder, or untick it.")
            return False
        return True

    def execute(self, context: Context) -> set[OperatorReturnItems]:
        global _send
        assert _preview is not None
        clip = _preview.clip
        _send = _Send(_preview)
        _send.thread = threading.Thread(
            target=_run_send,
            args=(_send, _chosen(clip, context), _folders(clip, context)),
        )
        _send.thread.start()
        window_manager = context.window_manager
        assert window_manager is not None
        main = next(w for w in window_manager.windows if w.scene.name != PREVIEW)
        with context.temp_override(window=main):
            bpy.ops.skd.fx2d_playblast_progress("INVOKE_DEFAULT")  # type: ignore
        return {"FINISHED"}


def _finish(send: _Send, context: Context) -> tuple[str, str]:
    """Closes the preview once every destination has it, or leaves the failed
    ones ticked for a retry. Returns the report level and message."""
    if send.result is None:
        return "ERROR", f"Could not send the playblast. {send.error}"
    preview = send.preview
    names = {
        destination.id: destination.name for destination in preview.clip.destinations
    }
    rows = [
        f"{names[outcome.id]}: {outcome.detail}" for outcome in send.result.outcomes
    ]
    if all(outcome.ok for outcome in send.result.outcomes):
        # After this operator returns, as Discard does.
        bpy.app.timers.register(functools.partial(_remove_if_current, preview))
        return "INFO", f"Playblast sent. {' | '.join(rows)}"
    if preview is _preview:
        preview.basename = send.result.basename
        props = _props(context)
        for outcome in send.result.outcomes:
            if outcome.ok:
                setattr(props, outcome.id, False)
                preview.delivered.add(outcome.id)
        # The panel redraws on its own only under the mouse.
        assert context.window_manager is not None
        for window in context.window_manager.windows:
            if window.scene.name == PREVIEW:
                for area in window.screen.areas:
                    area.tag_redraw()
    message = " | ".join([failure_summary(send.result), *rows]).strip(" |")
    return "ERROR", f"Could not send the playblast. {message}"


class SKD_OT_fx2d_playblast_progress(Operator):
    """Reports a running Send in the status bar; only modal operators reach it."""

    bl_idname = "skd.fx2d_playblast_progress"
    bl_label = "Playblast Send Progress"
    bl_options = {"INTERNAL"}

    def invoke(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        window_manager = context.window_manager
        assert window_manager is not None
        self.timer = window_manager.event_timer_add(0.25, window=context.window)
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Event) -> set[OperatorReturnItems]:
        if event.type != "TIMER" or _send is None:
            return {"PASS_THROUGH"}
        step = None
        while not _send.steps.empty():
            step = _send.steps.get()
        if step is not None:
            self.report({"INFO"}, step)
        if _sending():
            return {"PASS_THROUGH"}
        level, message = _finish(_send, context)
        self.report({level}, message)  # type: ignore
        assert context.window_manager is not None
        context.window_manager.event_timer_remove(self.timer)
        return {"FINISHED"}


class SKD_PT_fx2d_playblast(Panel):
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "SKD"
    bl_label = "Playblast"

    @classmethod
    def poll(cls, context: Context) -> bool:
        return (
            _preview is not None
            and context.scene is not None
            and context.scene.name == PREVIEW
        )

    def draw(self, context: Context) -> None:
        layout = self.layout
        assert layout is not None and _preview is not None
        props = _props(context)
        for destination in _preview.clip.destinations:
            layout.prop(props, destination.id, text=destination.name)
            if not getattr(props, destination.id):
                continue
            if isinstance(destination, ShotGridDestination):
                column = layout.column()
                column.prop(props, "playlist")
                if _playlist_error:
                    column.label(
                        text=f"Playlists unavailable: {_playlist_error}", icon="ERROR"
                    )
                column.prop(props, "description")
            elif destination.browsable:
                layout.prop(props, "custom_folder_path", text="")
        row = layout.row()
        row.scale_y = 1.4
        row.operator(
            SKD_OT_fx2d_playblast_send.bl_idname,
            text="Retry Failed" if _preview.basename else "Send",
            icon="EXPORT",
        )
        row.operator(SKD_OT_fx2d_playblast_discard.bl_idname, icon="TRASH")
