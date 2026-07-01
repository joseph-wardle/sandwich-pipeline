"""`MComparePlayblaster` — grid playblast comparing primary + alternates.

For one previs shot, captures the primary and each (live) alternate at
1280×720 over the shot's *max-intrinsic* length, then composes a single
movie that places them side-by-side via FFmpeg's `xstack`. Cameras shorter
than the longest go to black after their last keyed frame (pad via `tpad`).

The final encoded movie is scaled and letterboxed to fit a 1280×720 output
box — cells stay 16:9, fewer cameras = bigger cells. Single-camera shots
short-circuit through `MPlayblaster` upstream of this class (the Compare
checkbox disables itself when there's nothing to compare), so this code
path always sees ≥ 2 cameras.
"""

from __future__ import annotations

import copy
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import ffmpeg  # type: ignore[import-untyped]
import maya.cmds as mc
from mayacapture.capture import capture  # type: ignore[import-not-found]

from pipe.core.hud import (
    ARTIST,
    FONT_PATH,
    HudContent,
    apply_hud,
    labeled_line,
    line_date,
    line_shot,
)
from pipe.core.playblast import FFmpegPreset
from pipe.core.playblast.comparison import build_xstack_layout, pick_grid
from pipe.core.playblast.encoding import FFmpegEncodeError, encode_movie
from pipe.core.playblast.tempdir import resolve_playblast_tempdir
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.playblast.previs._viewport import apply_viewport_options
from pipe.dcc.maya.previs.cameras import resolve_camera_node
from pipe.dcc.maya.util.selection import maintain_selection

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


CELL_WIDTH = 1280
CELL_HEIGHT = 720
OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
FPS = 24

# Per-cell label sizing — ratios match `pipe.core.hud` so the cell label visually
# reads as a sibling of the frame-level HUD lines. Each ratio is multiplied
# by `max(cols, rows)` at render time so the post-`xstack` scale-down lands
# the label at ~21px in the final 720-tall output, regardless of grid shape.
_PER_CELL_FONTSIZE_RATIO = 0.029
_PER_CELL_PADDING_RATIO = 0.020
_PER_CELL_BORDER_RATIO = 0.0028
_PER_CELL_SEPARATOR = " · "  # MIDDLE DOT


@dataclass
class MCompareShotConfig:
    """Inputs for one compare-alternates playblast.

    `cameras` lists primary-first; `camera_durations[i]` is camera `i`'s
    intrinsic animation length in frames. `start_frame` is the capture's
    first frame (typically the previs sequence's frame 1001), `total_frames`
    is the longest camera's intrinsic length.
    """

    cameras: list[str]
    camera_durations: list[int]
    focal_lengths: list[float | None]
    start_frame: int
    total_frames: int
    paths: dict[FFmpegPreset, list[Path | str]]
    shot_label: str
    viewport_options: dict[str, bool] = field(default_factory=dict)

    def final_output_paths(self) -> list[Path]:
        out: list[Path] = []
        for preset, bases in self.paths.items():
            for base in bases:
                out.append(Path(str(base) + "." + preset.ext))
        return out


class MComparePlayblaster:
    """Run a compare playblast for one `MCompareShotConfig`.

    Not a `Playblaster` subclass: the base's `_do_playblast` assumes a single
    PNG-sequence → single-encode shape, which doesn't fit the multi-camera
    xstack pipeline. Reuses `pipe.core.playblast.encoding.encode_movie` and the
    shared FFmpeg-input helpers, but owns its own capture-and-compose loop.
    """

    _config: MCompareShotConfig

    def configure(self, config: MCompareShotConfig) -> MComparePlayblaster:
        self._config = config
        return self

    def playblast(self) -> None:
        with maintain_selection():
            mc.select(clear=True)
            tempdir = resolve_playblast_tempdir()
            camera_image_patterns = self._capture_all_cameras(tempdir)
            try:
                self._encode_and_publish(camera_image_patterns, tempdir)
            finally:
                self._cleanup_camera_images(tempdir)

    # ------------------------------------------------------------------
    # Step 1: capture every camera at the shot's start frame
    # ------------------------------------------------------------------

    def _capture_all_cameras(self, tempdir: Path) -> list[str]:
        """Capture each camera into its own basename. Returns the printf
        patterns FFmpeg needs as inputs."""
        capture_kwargs = apply_viewport_options({}, self._config.viewport_options)
        patterns: list[str] = []
        for camera, duration in zip(
            self._config.cameras, self._config.camera_durations, strict=True
        ):
            basename = self._image_basename_for_camera(camera)
            self._cleanup_basename(tempdir, basename)
            end_frame = self._config.start_frame + max(duration - 1, 0)
            capture(
                width=CELL_WIDTH,
                height=CELL_HEIGHT,
                filename=str(tempdir / basename),
                start_frame=self._config.start_frame,
                end_frame=end_frame,
                camera=resolve_camera_node(camera),
                format="image",
                compression="png",
                off_screen=True,
                show_ornaments=False,
                overwrite=True,
                maintain_aspect_ratio=False,
                viewer=0,
                **copy.deepcopy(capture_kwargs),
            )
            patterns.append(str(tempdir / f"{basename}.%04d.png"))
        return patterns

    def _image_basename_for_camera(self, camera: str) -> str:
        # Maya's `capture` writes `<basename>.<frame>.png`. Per-camera basename
        # keeps captures from colliding when multiple cameras share frames.
        safe_camera = camera.replace(":", "_").replace("/", "_").replace("|", "_")
        return f"playblast_compare.{self._config.shot_label}.{safe_camera}"

    @staticmethod
    def _cleanup_basename(tempdir: Path, basename: str) -> None:
        for path in tempdir.glob(f"{basename}*"):
            path.unlink()

    def _cleanup_camera_images(self, tempdir: Path) -> None:
        if log.isEnabledFor(logging.DEBUG):
            return
        for camera in self._config.cameras:
            self._cleanup_basename(tempdir, self._image_basename_for_camera(camera))

    # ------------------------------------------------------------------
    # Step 2: compose the grid, encode once, copy to every destination
    # ------------------------------------------------------------------

    def _encode_and_publish(
        self, camera_image_patterns: list[str], tempdir: Path
    ) -> None:
        cols, rows = pick_grid(len(self._config.cameras))
        composed_filter = self._build_composed_filter(camera_image_patterns, cols, rows)

        for preset, output_bases in self._config.paths.items():
            if not output_bases:
                continue
            preset_temp = (
                tempdir / f"playblast_compare.{self._config.shot_label}.{preset.ext}"
            )
            self._encode_preset(composed_filter, preset_temp, preset)
            for base in output_bases:
                self._copy_to_destination(preset_temp, Path(str(base)), preset)

    def _build_composed_filter(
        self,
        camera_image_patterns: list[str],
        cols: int,
        rows: int,
    ) -> Any:
        """Build the full filter chain: per-cell `drawtext` → `xstack` →
        `scale` → `pad` → frame-level `apply_hud`. The final stage matches
        the single-shot playblaster's HUD style so a compare playblast reads
        as a member of the same family in a dailies playlist."""
        per_cell_inputs: list[Any] = []
        for index, pattern in enumerate(camera_image_patterns):
            stream = ffmpeg.input(
                pattern,
                start_number=self._config.start_frame,
                r=FPS,
                colorspace="bt709",
                color_trc="iec61966-2-1",
            ).filter("format", "yuv422p")
            stream = self._pad_to_full_length(stream, camera_index=index)
            stream = self._draw_camera_label(
                stream, camera_index=index, cols=cols, rows=rows
            )
            per_cell_inputs.append(stream)

        # Empty cells (n < cols*rows) are filled with a black 1280×720 input so
        # `xstack`'s `inputs` count matches the layout's coordinates exactly.
        # The black input MUST be given an explicit duration — `lavfi color=`
        # generates an infinite stream by default, and `xstack` doesn't stop
        # at the shortest input, so without `d=` the encode runs forever.
        empty_cell_count = cols * rows - len(per_cell_inputs)
        filler_seconds = self._config.total_frames / FPS
        for _ in range(empty_cell_count):
            per_cell_inputs.append(self._make_black_cell_input(filler_seconds))

        layout = build_xstack_layout(cols, rows, CELL_WIDTH, CELL_HEIGHT)
        stacked = ffmpeg.filter(
            per_cell_inputs,
            "xstack",
            inputs=len(per_cell_inputs),
            layout=layout,
        )
        scaled = stacked.filter(
            "scale",
            w=OUTPUT_WIDTH,
            h=OUTPUT_HEIGHT,
            force_original_aspect_ratio="decrease",
        )
        padded = scaled.filter(
            "pad",
            w=OUTPUT_WIDTH,
            h=OUTPUT_HEIGHT,
            x="(ow-iw)/2",
            y="(oh-ih)/2",
            color="black",
        )
        return apply_hud(padded, self._hud_content(), (OUTPUT_WIDTH, OUTPUT_HEIGHT))

    def _hud_content(self) -> HudContent:
        """Frame-level HUD: Artist + Shot on the left, Date on the right,
        auto frame counter bottom-right. Per-cell camera/focal/role live in
        `_draw_camera_label`."""
        return HudContent(
            left_lines=(
                labeled_line(ARTIST, resolve_artist_display_name()),
                line_shot(self._config.shot_label),
            ),
            right_lines=(line_date(),),
            frame_start=self._config.start_frame,
        )

    def _pad_to_full_length(self, stream: Any, *, camera_index: int) -> Any:
        """Pad a camera's stream with black frames after its last keyed frame
        so every input runs `total_frames` frames long for `xstack`."""
        duration = self._config.camera_durations[camera_index]
        pad_frames = max(0, self._config.total_frames - duration)
        if pad_frames == 0:
            return stream
        pad_seconds = pad_frames / FPS
        return stream.filter(
            "tpad", stop_mode="add", stop_duration=pad_seconds, color="black"
        )

    def _draw_camera_label(
        self,
        stream: Any,
        *,
        camera_index: int,
        cols: int,
        rows: int,
    ) -> Any:
        """Burn a per-cell label combining camera namespace, focal length,
        and primary/alt role into the top-left corner of the cell.

        Font size scales with `max(cols, rows)` so the post-`xstack` scale-
        down leaves the text at a constant output-resolution size regardless
        of grid shape. Outline-only (no backing box) matches `pipe.core.hud`.
        """
        text = _format_cell_label(
            camera=self._config.cameras[camera_index],
            focal_length=self._config.focal_lengths[camera_index]
            if camera_index < len(self._config.focal_lengths)
            else None,
            is_primary=camera_index == 0,
        )
        scale = max(cols, rows)
        fontsize = round(CELL_HEIGHT * _PER_CELL_FONTSIZE_RATIO * scale)
        padding = round(CELL_HEIGHT * _PER_CELL_PADDING_RATIO * scale)
        borderw = max(1, round(CELL_HEIGHT * _PER_CELL_BORDER_RATIO * scale))
        return stream.filter(
            "drawtext",
            text=text,
            x=str(padding),
            y=str(padding),
            fontfile=str(FONT_PATH),
            fontsize=str(fontsize),
            fontcolor="white",
            borderw=str(borderw),
            bordercolor="black",
        )

    @staticmethod
    def _make_black_cell_input(duration_seconds: float) -> Any:
        # `color` input source generates a solid colour video at the chosen
        # size and rate. `d=` bounds its length — omit it and the source is
        # infinite, which would make `xstack` (and the encode) run forever.
        return ffmpeg.input(
            f"color=c=black:s={CELL_WIDTH}x{CELL_HEIGHT}:r={FPS}:d={duration_seconds}",
            f="lavfi",
        )

    def _encode_preset(
        self, composed_filter: Any, output_path: Path, preset: FFmpegPreset
    ) -> Path:
        try:
            return encode_movie(
                composed_filter,
                output_path=output_path,
                preset=preset,
                frame_rate=FPS,
                start_frame=self._config.start_frame,
            )
        except FFmpegEncodeError:
            raise

    @staticmethod
    def _copy_to_destination(source: Path, base: Path, preset: FFmpegPreset) -> Path:
        destination = base.with_name(base.name + "." + preset.ext)
        if not destination.parent.exists():
            destination.parent.mkdir(mode=0o770, parents=True)
        shutil.copyfile(source, destination)
        return destination


def _format_cell_label(
    *, camera: str, focal_length: float | None, is_primary: bool
) -> str:
    """Compose the per-cell label `cam_001 · 35mm · primary`, dropping any
    field that doesn't apply (no focal length, alt rather than primary)."""
    parts: list[str] = [camera]
    if focal_length is not None:
        parts.append(f"{focal_length:.0f}mm")
    if is_primary:
        parts.append("primary")
    return _PER_CELL_SEPARATOR.join(parts)


__all__ = [
    "MComparePlayblaster",
    "MCompareShotConfig",
]
