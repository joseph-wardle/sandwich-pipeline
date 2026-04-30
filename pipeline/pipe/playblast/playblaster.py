from __future__ import annotations

import logging
import re
import shutil
import time
from abc import ABCMeta, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from pipe.playblast.encoding import build_image_input_chain, encode_movie
from pipe.playblast.presets import FFmpegPreset
from pipe.playblast.tempdir import resolve_playblast_tempdir

if TYPE_CHECKING:
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

    fps: int = 24

    @abstractmethod
    def _write_images(self, shot: Shot, path: str) -> None:
        pass

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
        shot: Shot,
        out_paths: dict[FFmpegPreset, list[Path | str]] | None = None,
        tails: tuple[int, int] = (0, 0),
    ) -> None:
        out_paths = out_paths or {}

        tempdir = self._resolve_tempdir()
        image_basename = self._image_basename(shot)
        self._cleanup_temp_files(tempdir, image_basename)

        cut_in, cut_out = shot.frame_range
        frame_start = cut_in - tails[0]
        frame_end = cut_out + tails[1]
        action_id = self._new_playblast_action_id()
        expected_total_outputs = sum(len(paths) for paths in out_paths.values())

        with self._telemetry_phase(
            shot=shot,
            preset="unknown",
            expected_outputs=expected_total_outputs,
            frame_start=frame_start,
            frame_end=frame_end,
            action_id=action_id,
        ):
            self._write_images(shot, str(tempdir / image_basename))

        self._normalize_frame_filenames(tempdir, image_basename)

        encoded_input = self._build_ffmpeg_input(
            shot, tempdir, image_basename, frame_start
        )

        for preset, paths in out_paths.items():
            with self._telemetry_phase(
                shot=shot,
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
        """Trigger a playblast. Concrete implementations build inputs from
        configured state and call `super()._do_playblast(shot, out_paths, tails)`."""
        pass

    # ------------------------------------------------------------------
    # Pipeline steps (small, single-responsibility helpers).
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_tempdir() -> Path:
        return resolve_playblast_tempdir()

    @staticmethod
    def _image_basename(shot: Shot) -> str:
        return "playblast_temp." + (shot.code or "")

    @staticmethod
    def _cleanup_temp_files(tempdir: Path, basename: str) -> None:
        for path in tempdir.glob(basename + "*"):
            path.unlink()

    @staticmethod
    def _normalize_frame_filenames(tempdir: Path, basename: str) -> None:
        # Houdini emits negative frame numbers as `name.-3.png`; ffmpeg's
        # image2 demuxer needs fixed-width zero-padded numbers
        # (`name.-0003.png`). Rewrite both signs to a uniform width.
        pattern = re.compile(rf"{re.escape(basename)}\.(\-?\d+)\.png$")
        for path in tempdir.glob(f"{basename}.*.png"):
            match = pattern.match(path.name)
            if not match:
                continue
            new_name = f"{basename}.{_padded_signed_int(int(match.group(1)))}.png"
            path.rename(path.with_name(new_name))

    def _build_ffmpeg_input(
        self, shot: Shot, tempdir: Path, basename: str, start_frame: int
    ) -> Any:
        del shot  # base impl ignores shot context; HPlayblaster's HUD uses it
        return build_image_input_chain(
            str(tempdir / basename) + ".%04d.png",
            start_frame=start_frame,
            frame_rate=self.fps,
        )

    def _encode_preset(
        self,
        input_chain: Any,
        preset: FFmpegPreset,
        tempdir: Path,
        basename: str,
        start_frame: int,
    ) -> Path:
        return encode_movie(
            input_chain,
            output_path=Path(str(tempdir / basename) + "." + preset.ext),
            preset=preset,
            frame_rate=self.fps,
            start_frame=start_frame,
        )

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
    # TODO(telemetry-rewrite): The telemetry hooks below are scaffolding
    # around today's `pipe.telemetry.emit`. The telemetry system is being
    # rewritten in a follow-up PR; when that rewrite lands, the module-
    # level `_safe_import_telemetry` helper plus the three methods that
    # call it (`_telemetry_scope`, `_new_playblast_action_id`,
    # `_emit_playblast_event`) collapse into the new telemetry adapter.
    # ------------------------------------------------------------------

    @contextmanager
    def _telemetry_phase(
        self,
        *,
        shot: Shot,
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
                shot=shot,
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
            shot=shot,
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

    @staticmethod
    def _telemetry_scope(shot: Shot) -> dict[str, str] | None:
        telemetry = _safe_import_telemetry()
        if telemetry is None:
            return None
        scope = telemetry.extract_scope(shot)
        shot_code = str(getattr(shot, "code", "")).strip()
        if shot_code:
            scope.setdefault("shot", shot_code)
        return scope or None

    @staticmethod
    def _new_playblast_action_id() -> str | None:
        telemetry = _safe_import_telemetry()
        if telemetry is None:
            return None
        return telemetry.new_action_id()

    def _emit_playblast_event(
        self,
        *,
        shot: Shot,
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
        telemetry = _safe_import_telemetry()
        if telemetry is None:
            log.debug("Telemetry import unavailable for playblast.create")
            return

        status_value = (
            telemetry.STATUS_SUCCESS if status == "success" else telemetry.STATUS_ERROR
        )
        payload = {
            "preset": str(preset),
            "output_count": max(0, int(output_count)),
            "frame_start": int(frame_start),
            "frame_end": int(frame_end),
            "fps": max(1, int(self.fps)),
        }
        metrics = {
            "duration_ms": max(0, int(duration_ms)),
            "output_size_bytes": max(0, int(output_size_bytes)),
        }

        error = None
        if status == "error":
            error_code = "PLAYBLAST_FAILED"
            try:
                definition = telemetry.get_event_definition(
                    telemetry.events.EVENT_PLAYBLAST_CREATE
                )
                if definition.error_codes:
                    error_code = definition.error_codes[0]
            except Exception:
                pass
            error = {
                "code": error_code,
                "message": error_message or "Playblast failed",
                "exception_type": exception_type or "RuntimeError",
            }

        telemetry.emit(
            telemetry.events.EVENT_PLAYBLAST_CREATE,
            status=status_value,
            action_id=action_id,
            payload=payload,
            metrics=metrics,
            scope=self._telemetry_scope(shot),
            error=error,
        )


def _padded_signed_int(num: int, width: int = 4) -> str:
    """Render `num` as a fixed-width zero-padded integer, preserving a leading
    `-` for negatives but emitting no sign for positives.

    `f"{num:+05d}"` gives `+0003`/`-0003`; we strip the `+` so positives
    render as `0003`. Width is the *digit* width, so the rendered string is
    `width` chars for positives and `width + 1` for negatives.
    """
    return f"{num:+0{width + 1}d}".replace("+", "")


def _safe_import_telemetry() -> Any:
    """Return `pipe.telemetry` if importable, else `None`. Treats import-
    time failures as "telemetry is optional" so playblasts keep working on
    hosts without telemetry credentials."""
    try:
        import pipe.telemetry as telemetry
    except Exception:
        return None
    return telemetry


__all__ = ["Playblaster"]
