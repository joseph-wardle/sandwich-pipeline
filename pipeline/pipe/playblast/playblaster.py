from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from abc import ABCMeta, abstractmethod
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import ffmpeg  # type: ignore[import-untyped]

from pipe.playblast.presets import FFmpegPreset

if TYPE_CHECKING:
    from typing import Self

    from pipe.shotgrid import Shot


log = logging.getLogger(__name__)


class Playblaster(metaclass=ABCMeta):
    """Cross-DCC base for playblasters. Uses FFmpeg to encode videos.

    Subclasses implement `_write_images` to dump a PNG sequence; this base
    handles encoding via FFmpeg, copying to multiple output paths, post-
    processing for VLC compatibility, and emitting telemetry.
    """

    _shot: Shot
    _in_context: bool

    FR = 24

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
    def _telemetry_preset_name(preset: object | None) -> str:
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
        except OSError:
            pass
        return 0

    def _telemetry_scope(self) -> dict[str, str] | None:
        try:
            from pipe.telemetry import extract_scope
        except Exception:
            return None

        scope = extract_scope(self._shot)
        shot_code = str(getattr(self._shot, "code", "")).strip()
        if shot_code:
            scope.setdefault("shot", shot_code)
        return scope or None

    @staticmethod
    def _new_playblast_action_id() -> str | None:
        try:
            from pipe.telemetry import new_action_id
        except Exception:
            return None
        return new_action_id()

    def _emit_playblast_event(
        self,
        *,
        status: str,
        preset: str,
        output_count: int,
        frame_start: int,
        frame_end: int,
        duration_ms: int,
        output_size_bytes: int,
        action_id: str | None,
        error_message: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        try:
            from pipe.telemetry import (
                STATUS_ERROR,
                STATUS_SUCCESS,
                emit,
                events,
                get_event_definition,
            )
        except Exception:
            log.debug(
                "Telemetry import unavailable for playblast.create", exc_info=True
            )
            return

        status_value = STATUS_SUCCESS if status == "success" else STATUS_ERROR
        payload = {
            "preset": str(preset),
            "output_count": max(0, int(output_count)),
            "frame_start": int(frame_start),
            "frame_end": int(frame_end),
            "fps": max(1, int(self.FR)),
        }
        metrics = {
            "duration_ms": max(0, int(duration_ms)),
            "output_size_bytes": max(0, int(output_size_bytes)),
        }

        error = None
        if status == "error":
            error_code = "PLAYBLAST_FAILED"
            try:
                definition = get_event_definition(events.EVENT_PLAYBLAST_CREATE)
                if definition.error_codes:
                    error_code = definition.error_codes[0]
            except Exception:
                pass
            error = {
                "code": error_code,
                "message": error_message or "Playblast failed",
                "exception_type": exception_type or "RuntimeError",
            }

        emit(
            events.EVENT_PLAYBLAST_CREATE,
            status=status_value,
            action_id=action_id,
            payload=payload,
            metrics=metrics,
            scope=self._telemetry_scope(),
            error=error,
        )

    def _do_playblast(
        self,
        out_paths: dict[FFmpegPreset, list[Path | str]] | None = None,
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
        playblast_action_id = self._new_playblast_action_id()

        # do the playblast
        image_write_started_at = time.perf_counter()
        try:
            self._write_images(str(tempdir / FILENAME))
        except Exception as exc:
            duration_ms = int((time.perf_counter() - image_write_started_at) * 1000)
            self._emit_playblast_event(
                status="error",
                preset="unknown",
                output_count=expected_total_outputs,
                frame_start=frame_start,
                frame_end=frame_end,
                duration_ms=duration_ms,
                output_size_bytes=0,
                action_id=playblast_action_id,
                error_message=str(exc),
                exception_type=type(exc).__name__,
            )
            raise

        # 0 padding on negative numbers
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
            preset_started_at = time.perf_counter()
            preset_name = self._telemetry_preset_name(preset)
            expected_outputs = len(paths)
            try:
                out_filename = str(tempdir / FILENAME) + "." + preset.ext
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
            except ffmpeg.Error as e:
                if e.stdout:
                    print("stdout:", e.stdout.decode())
                if e.stderr:
                    print("stderr:", e.stderr.decode())
                duration_ms = int((time.perf_counter() - preset_started_at) * 1000)
                self._emit_playblast_event(
                    status="error",
                    preset=preset_name,
                    output_count=expected_outputs,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    duration_ms=duration_ms,
                    output_size_bytes=0,
                    action_id=playblast_action_id,
                    error_message=str(e),
                    exception_type=type(e).__name__,
                )
                raise

            # copy video out of tempdir
            final_paths: list[Path] = []

            try:
                for path in (Path(str(p) + "." + preset.ext) for p in paths):
                    if not path.parent.exists():
                        path.parent.mkdir(mode=0o770, parents=True)
                    shutil.copyfile(out_filename, path)
                    final_paths.append(path)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - preset_started_at) * 1000)
                self._emit_playblast_event(
                    status="error",
                    preset=preset_name,
                    output_count=expected_outputs,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    duration_ms=duration_ms,
                    output_size_bytes=0,
                    action_id=playblast_action_id,
                    error_message=str(exc),
                    exception_type=type(exc).__name__,
                )
                raise

            # run postprocess so video works in vlc
            for final_path in final_paths:
                try:
                    self._run_postprocess(final_path)
                except Exception as e:
                    log.error(f"Post-process failed for {final_path}: {e}")
            output_size_bytes = sum(self._safe_file_size(path) for path in final_paths)
            duration_ms = int((time.perf_counter() - preset_started_at) * 1000)
            self._emit_playblast_event(
                status="success",
                preset=preset_name,
                output_count=len(final_paths),
                frame_start=frame_start,
                frame_end=frame_end,
                duration_ms=duration_ms,
                output_size_bytes=output_size_bytes,
                action_id=playblast_action_id,
            )

        # clean up if not in debug mode
        if not log.isEnabledFor(logging.DEBUG):
            for p in tempdir.glob(FILENAME + "*"):
                p.unlink()

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


__all__ = ["Playblaster"]
