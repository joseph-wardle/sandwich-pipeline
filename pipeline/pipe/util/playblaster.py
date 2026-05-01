from __future__ import annotations

import logging
import os
import shutil
import subprocess
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import ffmpeg  # type: ignore[import-untyped]

from pipe.telemetry import (
    EVENT_PLAYBLAST_CREATE,
    PlayblastError,
    action,
    extract_scope,
)

if TYPE_CHECKING:
    from typing import Any, Self

    from pipe.shotgrid import Shot


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FFMpegPreset:
    ext: str
    out_kwargs: dict[str, Any]

    def __hash__(self):
        return hash(frozenset(self.out_kwargs.items()))


class Playblaster(metaclass=ABCMeta):
    """Parent class for creating playblasters. Uses FFmpeg to encode videos"""

    _shot: Shot
    _in_context: bool

    FR = 24

    class PRESET(FFMpegPreset, Enum):
        EDIT_SQ = (
            "mov",
            {
                "vcodec": "dnxhd",
                "pix_fmt": "yuv422p",
                "vprofile": "dnxhr_sq",
                # this number comes from Avid's table in the DNxHD whitepaper
                "video_bitrate": "124M",
            },
        )
        EDIT_HQX = (
            "mov",
            {
                "vcodec": "dnxhd",
                "pix_fmt": "yuv422p10le",
                "vprofile": "dnxhr_hqx",
                "video_bitrate": "188M",
            },
        )
        WEB = (
            "mp4",
            {
                "vcodec": "libx264",
                "preset": "veryslow",
                "tune": "animation",
                "crf": 20,
            },
        )
        H265 = (
            "mp4",
            {
                "vcodec": "libx265",
                "preset": "slow",
                "crf": 23,
                "pix_fmt": "yuv420p",
            },
        )

    def __init__(self) -> None:
        pass

    @abstractmethod
    def _write_images(self, path: str) -> None:
        pass

    def __enter__(self) -> Self:
        self._in_context = True
        return self

    def __call__(self, shot: Shot, *args):
        self._shot = shot
        return self

    def __exit__(self, *args) -> None:
        self._in_context = False

    def _run_postprocess(self, video_path: Path) -> None:
        temp_output = video_path.with_suffix(".post.mov")

        cmd = [
            "ffmpeg",
            "-y",  # overwrite without asking
            "-i",
            str(video_path),
            "-vf",
            "format=yuv420p",
            "-c:v",
            "dnxhd",
            "-profile:v",
            "dnxhr_hq",
            "-crf",
            "18",
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temp_output),
        ]

        log.info(f"Running FFmpeg post-process: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)

        # Replace original with post-processed file
        video_path.unlink()
        temp_output.rename(video_path)

    @staticmethod
    def _preset_name(preset: object | None) -> str:
        if isinstance(preset, Enum):
            normalized = str(preset.name).strip().lower()
            if normalized:
                return normalized
        if preset is None:
            return "unknown"
        normalized = str(preset).strip().lower()
        return normalized or "unknown"

    @staticmethod
    def _safe_file_size(path: Path) -> int:
        try:
            if path.is_file():
                return int(path.stat().st_size)
        except Exception:
            pass
        return 0

    def _playblast_scope(self) -> dict[str, str] | None:
        scope = extract_scope(self._shot)
        shot_code = str(getattr(self._shot, "code", "")).strip()
        if shot_code:
            scope.setdefault("shot", shot_code)
        return scope or None

    def _do_playblast(
        self,
        out_paths: dict[PRESET, list[Path | str]] | None = None,
        tails: tuple[int, int] = (0, 0),
    ) -> None:
        if not self._in_context:
            raise RuntimeError("_do_playblast not called from within context self")

        if not out_paths:
            out_paths = {}
        expected_total_outputs = sum(len(paths) for paths in out_paths.values())

        tempdir = Path(os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))).resolve()

        FILENAME = "bobo_pb_temp." + (self._shot.code or "")

        # remove any old playblasts
        for p in tempdir.glob(FILENAME + "*"):
            p.unlink()

        cut_in, cut_out = self._shot.frame_range
        frame_start = cut_in - tails[0]
        frame_end = cut_out + tails[1]
        common_payload: dict[str, object] = {
            "frame_start": frame_start,
            "frame_end": frame_end,
            "fps": max(1, int(self.FR)),
        }
        scope = self._playblast_scope()

        # Image write — failure here aborts every preset, so it gets one
        # `playblast.create` event tagged preset="unknown".
        try:
            self._write_images(str(tempdir / FILENAME))
        except Exception as exc:
            with action(
                EVENT_PLAYBLAST_CREATE,
                payload={
                    **common_payload,
                    "preset": "unknown",
                    "output_count": expected_total_outputs,
                },
                scope=scope,
            ):
                raise PlayblastError(str(exc) or exc.__class__.__name__) from exc
            return  # unreachable; raise above propagates

        # 0 padding on negative numbers
        import re

        pattern = re.compile(rf"{re.escape(FILENAME)}\.(\-?\d+)\.png$")
        for p in tempdir.glob(f"{FILENAME}.*.png"):
            match = pattern.match(p.name)
            if not match:
                continue
            num = int(match.group(1))
            new_name = f"{FILENAME}.{num:+05d}.png".replace("+", "")
            new_path = p.with_name(new_name)
            p.rename(new_path)

        # use ffmpeg to encode the video
        start_frame = frame_start
        images = ffmpeg.input(
            str(tempdir / FILENAME) + ".%04d.png",
            start_number=start_frame,
            r=self.FR,
            # precisely define input colorspace
            colorspace="bt709",
            color_trc="iec61966-2-1",
        ).filter("format", "yuv422p")

        for preset, paths in out_paths.items():
            preset_name = self._preset_name(preset)
            expected_outputs = len(paths)
            with action(
                EVENT_PLAYBLAST_CREATE,
                payload={
                    **common_payload,
                    "preset": preset_name,
                    "output_count": expected_outputs,
                },
                scope=scope,
            ) as t:
                self._encode_and_publish_preset(
                    preset=preset,
                    paths=paths,
                    images=images,
                    out_filename=str(tempdir / FILENAME) + "." + preset.ext,
                    start_frame=start_frame,
                    t=t,
                )

        # clean up if not in debug mode
        if not log.isEnabledFor(logging.DEBUG):
            for p in tempdir.glob(FILENAME + "*"):
                p.unlink()

    def _encode_and_publish_preset(
        self,
        *,
        preset: Any,
        paths: list[Path | str],
        images: Any,
        out_filename: str,
        start_frame: int,
        t: Any,
    ) -> None:
        """Encode a single preset's video, copy it to all destination paths,
        and run post-process. Raises PlayblastError on encode/copy failure;
        post-process failures are best-effort and logged."""
        try:
            ffmpeg.output(
                images,
                out_filename,
                **preset.out_kwargs,
                timecode="00:00:{:02}:{:02}".format(
                    start_frame // self.FR,
                    start_frame % self.FR,
                ),
                r=self.FR,
            ).overwrite_output().run()
        except ffmpeg.Error as exc:
            if exc.stdout:
                print("stdout:", exc.stdout.decode())
            if exc.stderr:
                print("stderr:", exc.stderr.decode())
            raise PlayblastError(str(exc) or exc.__class__.__name__) from exc

        final_paths: list[Path] = []
        try:
            for path in (Path(str(p) + "." + preset.ext) for p in paths):
                if not path.parent.exists():
                    path.parent.mkdir(mode=0o770, parents=True)
                shutil.copyfile(out_filename, path)
                final_paths.append(path)
        except Exception as exc:
            raise PlayblastError(str(exc) or exc.__class__.__name__) from exc

        # Post-process is best-effort — failure does not invalidate the playblast.
        for final_path in final_paths:
            try:
                self._run_postprocess(final_path)
            except Exception as exc:
                log.error(f"Post-process failed for {final_path}: {exc}")

        t.update_payload(output_count=len(final_paths))

    @abstractmethod
    def playblast(self) -> None:
        """Function to be called by the user to trigger a playblast.
        This should call `_do_playblast` from within a `with self(...)`
        block.
        Looks something like:
            >>> def playblast(self) -> None:
            >>>     with self(shot):
            >>>         super()._do_playblast([filepath])
        """
        pass
