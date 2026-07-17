"""The Confirm engine: persists a viewed preview to its checked destinations."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import attrs

from pipe.core.playblast.encoding import build_image_input_chain, encode_movie
from pipe.core.playblast.naming import next_versioned_basename
from pipe.core.playblast.presets import FFmpegPreset
from pipe.core.playblast.preview_spec import Destination, PreviewClip
from pipe.core.playblast.review.versions import (
    PlayblastEntity,
    PlayblastVersionUploadRequest,
    UploadTarget,
    upload_playblast_version,
)

log = logging.getLogger(__name__)

SHOTGRID_DESTINATION_NAME = "ShotGrid"


@attrs.frozen
class ConfirmChoices:
    """What the artist checked in the Confirm panel for one clip."""

    destinations: tuple[Destination, ...]
    upload_to_shotgrid: bool = False
    review_playlist_id: int | None = None
    description: str | None = None


@attrs.frozen
class DestinationOutcome:
    """One delivery result: `detail` is the final path (or upload message)
    on success, the artist-facing reason on failure."""

    name: str
    ok: bool
    detail: str


@attrs.frozen
class ConfirmResult:
    basename: str
    outcomes: tuple[DestinationOutcome, ...]

    @property
    def failed(self) -> tuple[DestinationOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.ok)


def confirm_clip(
    clip: PreviewClip,
    choices: ConfirmChoices,
    *,
    fps: int,
    basename: str | None = None,
) -> ConfirmResult:
    """Deliver one clip to every checked destination."""
    if basename is None:
        basename = next_versioned_basename(
            clip.output_prefix,
            [destination.directory for destination in choices.destinations],
        )

    outcomes = [
        _deliver_to_folder(clip, destination, basename, fps)
        for destination in choices.destinations
    ]
    if choices.upload_to_shotgrid:
        outcomes.append(_upload_to_shotgrid(clip, choices, basename, fps))
    return ConfirmResult(basename=basename, outcomes=tuple(outcomes))


def _deliver_to_folder(
    clip: PreviewClip, destination: Destination, basename: str, fps: int
) -> DestinationOutcome:
    try:
        movie = _encoded_movie(clip, destination.preset, basename, fps)
        destination.directory.mkdir(mode=0o770, parents=True, exist_ok=True)
        final_path = destination.directory / movie.name
        shutil.copyfile(movie, final_path)
    except Exception as exc:
        log.exception("Confirm delivery to '%s' failed", destination.name)
        return DestinationOutcome(destination.name, ok=False, detail=_reason(exc))
    return DestinationOutcome(destination.name, ok=True, detail=str(final_path))


def _upload_to_shotgrid(
    clip: PreviewClip, choices: ConfirmChoices, basename: str, fps: int
) -> DestinationOutcome:
    shotgrid = clip.shotgrid
    if shotgrid is None:
        return DestinationOutcome(
            SHOTGRID_DESTINATION_NAME,
            ok=False,
            detail="This preview has no ShotGrid entity to upload to.",
        )

    try:
        # ShotGrid transcodes whatever it receives, so the upload always
        # uses the WEB encode — shared with any checked WEB folder row.
        movie = _encoded_movie(clip, FFmpegPreset.WEB, basename, fps)
        result = upload_playblast_version(
            PlayblastVersionUploadRequest(
                entity=PlayblastEntity(
                    kind=shotgrid.entity_kind, value=shotgrid.entity_value
                ),
                movie_path=movie,
                version_name=basename,
                description=choices.description,
                artist_display_name=shotgrid.artist_display_name,
                upload_target=(
                    UploadTarget.REVIEW
                    if choices.review_playlist_id is not None
                    else UploadTarget.VERSION_ONLY
                ),
                review_playlist_id=choices.review_playlist_id,
            )
        )
    except Exception as exc:
        log.exception(
            "ShotGrid upload failed for %s '%s'",
            shotgrid.entity_kind,
            shotgrid.entity_value,
        )
        return DestinationOutcome(
            SHOTGRID_DESTINATION_NAME, ok=False, detail=_reason(exc)
        )

    detail = " ".join([result.message, *result.warnings])
    return DestinationOutcome(SHOTGRID_DESTINATION_NAME, ok=result.ok, detail=detail)


def _encoded_movie(
    clip: PreviewClip, preset: FFmpegPreset, basename: str, fps: int
) -> Path:
    """Encode the clip's frames to `preset`, returning the movie in the
    clip's tempdir. A retry (or a second destination sharing the preset)
    reuses the movie already encoded for this basename."""
    movie = clip.frames_dir / f"{basename}.{preset.ext}"
    if movie.exists():
        return movie
    return encode_movie(
        build_image_input_chain(
            str(clip.frames_dir / clip.frames_basename) + ".%04d.png",
            start_frame=clip.frame_start,
            frame_rate=fps,
        ),
        output_path=movie,
        preset=preset,
        frame_rate=fps,
        start_frame=clip.frame_start,
    )


def _reason(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


__all__ = [
    "SHOTGRID_DESTINATION_NAME",
    "ConfirmChoices",
    "ConfirmResult",
    "DestinationOutcome",
    "confirm_clip",
]
