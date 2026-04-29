from __future__ import annotations

import logging
import os
import re
import shutil
import time
from abc import ABCMeta, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import ffmpeg  # type: ignore[import-untyped]

from pipe.playblast.presets import FFmpegPreset

if TYPE_CHECKING:
    from typing import Self

    from pipe.shotgrid import Shot


log = logging.getLogger(__name__)


@dataclass
class _TelemetryPhase:
    """Mutable record passed out of `_telemetry_phase` so the body can attach
    what it produced before the context manager emits the success event."""

    final_paths: list[Path] = field(default_factory=list)


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
        """Optional post-encode pass on each final output path.

        Default is a no-op. DCC-specific subclasses may override to add
        steps that need runtime DCC state — HUD burn-in via FFmpeg
        `drawtext`, slate-frame insertion, LUT application, etc. — by
        mutating the file at `video_path` in place.

        Encoding format choices belong on `FFmpegPreset.out_kwargs`,
        not here: this hook runs *after* the desired codec is already on
        disk, so don't re-encode it.
        """
        return

    def _do_playblast(
        self,
        out_paths: dict[FFmpegPreset, list[Path | str]] | None = None,
        tails: tuple[int, int] = (0, 0),
    ) -> None:
        if not self._in_context:
            raise RuntimeError("_do_playblast not called from within context self")
        out_paths = out_paths or {}

        tempdir = self._resolve_tempdir()
        image_basename = self._image_basename()
        self._cleanup_temp_files(tempdir, image_basename)

        cut_in, cut_out = self._shot.frame_range
        frame_start = cut_in - tails[0]
        frame_end = cut_out + tails[1]
        action_id = self._new_playblast_action_id()
        expected_total_outputs = sum(len(paths) for paths in out_paths.values())

        with self._telemetry_phase(
            preset="unknown",
            expected_outputs=expected_total_outputs,
            frame_start=frame_start,
            frame_end=frame_end,
            action_id=action_id,
        ):
            self._write_images(str(tempdir / image_basename))

        self._normalize_frame_filenames(tempdir, image_basename)

        encoded_input = self._build_ffmpeg_input(tempdir, image_basename, frame_start)

        for preset, paths in out_paths.items():
            with self._telemetry_phase(
                preset=self._telemetry_preset_name(preset),
                expected_outputs=len(paths),
                frame_start=frame_start,
                frame_end=frame_end,
                action_id=action_id,
            ) as phase:
                preset_temp = self._encode_preset(
                    encoded_input, preset, tempdir, image_basename, frame_start
                )
                phase.final_paths = self._copy_outputs(preset_temp, paths, preset.ext)
                for final_path in phase.final_paths:
                    self._safe_run_postprocess(final_path)

        if not log.isEnabledFor(logging.DEBUG):
            self._cleanup_temp_files(tempdir, image_basename)

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

    # ------------------------------------------------------------------
    # Pipeline steps (small, single-responsibility helpers).
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_tempdir() -> Path:
        return Path(os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))).resolve()

    def _image_basename(self) -> str:
        return "playblast_temp." + (self._shot.code or "")

    @staticmethod
    def _cleanup_temp_files(tempdir: Path, basename: str) -> None:
        for path in tempdir.glob(basename + "*"):
            path.unlink()

    @staticmethod
    def _normalize_frame_filenames(tempdir: Path, basename: str) -> None:
        # Houdini emits negative frame numbers as `name.-3.png`; ffmpeg's
        # image2 demuxer needs fixed-width zero-padded numbers
        # (`name.-0003.png`). Rewrite both signs to a uniform 5-char width.
        pattern = re.compile(rf"{re.escape(basename)}\.(\-?\d+)\.png$")
        for path in tempdir.glob(f"{basename}.*.png"):
            match = pattern.match(path.name)
            if not match:
                continue
            num = int(match.group(1))
            new_name = f"{basename}.{num:+05d}.png".replace("+", "")
            path.rename(path.with_name(new_name))

    def _build_ffmpeg_input(
        self, tempdir: Path, basename: str, start_frame: int
    ) -> Any:
        return ffmpeg.input(
            str(tempdir / basename) + ".%04d.png",
            start_number=start_frame,
            r=self.FR,
            # precisely define input colorspace
            colorspace="bt709",
            color_trc="iec61966-2-1",
        ).filter("format", "yuv422p")

    def _encode_preset(
        self,
        input_chain: Any,
        preset: FFmpegPreset,
        tempdir: Path,
        basename: str,
        start_frame: int,
    ) -> Path:
        out_filename = str(tempdir / basename) + "." + preset.ext
        try:
            ffmpeg.output(
                input_chain,
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
            raise
        return Path(out_filename)

    @staticmethod
    def _copy_outputs(
        source: Path,
        paths: list[Path | str],
        ext: str,
    ) -> list[Path]:
        final_paths: list[Path] = []
        for raw_path in paths:
            destination = Path(str(raw_path) + "." + ext)
            if not destination.parent.exists():
                destination.parent.mkdir(mode=0o770, parents=True)
            shutil.copyfile(source, destination)
            final_paths.append(destination)
        return final_paths

    def _safe_run_postprocess(self, final_path: Path) -> None:
        try:
            self._run_postprocess(final_path)
        except Exception as exc:
            log.error("Post-process failed for %s: %s", final_path, exc)

    # ------------------------------------------------------------------
    # Telemetry. Intentionally a thin wrapper around today's
    # `_emit_playblast_event`; the telemetry system itself is being
    # rewritten in the next PR and will replace this scaffolding.
    # ------------------------------------------------------------------

    @contextmanager
    def _telemetry_phase(
        self,
        *,
        preset: str,
        expected_outputs: int,
        frame_start: int,
        frame_end: int,
        action_id: str | None,
    ) -> Iterator[_TelemetryPhase]:
        phase = _TelemetryPhase()
        started_at = time.perf_counter()
        try:
            yield phase
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._emit_playblast_event(
                status="error",
                preset=preset,
                output_count=expected_outputs,
                frame_start=frame_start,
                frame_end=frame_end,
                duration_ms=duration_ms,
                output_size_bytes=0,
                action_id=action_id,
                error_message=str(exc),
                exception_type=type(exc).__name__,
            )
            raise
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        size_bytes = sum(self._safe_file_size(p) for p in phase.final_paths)
        self._emit_playblast_event(
            status="success",
            preset=preset,
            output_count=len(phase.final_paths) or expected_outputs,
            frame_start=frame_start,
            frame_end=frame_end,
            duration_ms=duration_ms,
            output_size_bytes=size_bytes,
            action_id=action_id,
        )

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


__all__ = ["Playblaster"]
