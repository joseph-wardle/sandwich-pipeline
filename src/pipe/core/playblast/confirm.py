"""The Confirm engine: persists a viewed preview to its checked destinations."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import attrs

from pipe.core.playblast.encoding import build_image_input_chain, encode_movie
from pipe.core.playblast.naming import next_versioned_basename
from pipe.core.playblast.presets import FFmpegPreset
from pipe.core.playblast.clip import Destination, PreviewClip, PrevisStamp
from pipe.core.playblast.review.versions import (
    PlayblastEntity,
    PlayblastVersionUploadRequest,
    UploadTarget,
    upload_playblast_version,
)
from pipe.core.previs import (
    load_manifest,
    mutate_manifest,
    naming,
    playblasts_dir,
    utcnow_iso,
)
from pipe.core.previs.model import Take

log = logging.getLogger(__name__)

SHOTGRID_DESTINATION_NAME = "ShotGrid"
SEND_TO_EDIT_DESTINATION_NAME = "Send to Edit"

_TAKE_PRESET = FFmpegPreset.EDIT_SQ


@attrs.frozen
class ConfirmChoices:
    """What the artist checked in the Confirm panel for one clip."""

    destinations: tuple[Destination, ...]
    upload_to_shotgrid: bool = False
    review_playlist_id: int | None = None
    description: str | None = None
    send_to_edit: bool = False


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
    basename: str | None = None,
) -> ConfirmResult:
    """Deliver one clip to every checked destination."""

    outcomes: list[DestinationOutcome] = []

    take_stamp = clip.previs_stamp if choices.send_to_edit else None
    if take_stamp is not None:
        take_outcome, take_basename = _deliver_take(clip, take_stamp)
        outcomes.append(take_outcome)
        # A take that failed before it could allocate a version yields no
        # basename; the folders then version themselves as usual below.
        basename = basename or take_basename

    if basename is None:
        basename = next_versioned_basename(
            clip.output_prefix,
            [destination.directory for destination in choices.destinations],
        )

    outcomes += [
        _deliver_to_folder(clip, destination, basename)
        for destination in choices.destinations
    ]
    if choices.upload_to_shotgrid:
        outcomes.append(_upload_to_shotgrid(clip, choices, basename))
    return ConfirmResult(basename=basename, outcomes=tuple(outcomes))


def _deliver_take(
    clip: PreviewClip, stamp: PrevisStamp
) -> tuple[DestinationOutcome, str | None]:
    """Deliver the immutable previs take: encode, copy into the sequence's
    playblasts dir, and stamp the manifest."""
    try:
        version = load_manifest(
            stamp.sequence_code, previs_root=stamp.previs_root
        ).next_take_version(stamp.shot_code)
    except Exception as exc:
        log.exception("Could not allocate a take version for %s", stamp.shot_code)
        return (
            DestinationOutcome(
                SEND_TO_EDIT_DESTINATION_NAME, ok=False, detail=_reason(exc)
            ),
            None,
        )

    basename = naming.take_filename(stamp.shot_code, version).removesuffix(
        naming.TAKE_SUFFIX
    )
    directory = playblasts_dir(stamp.sequence_code, previs_root=stamp.previs_root)
    try:
        final_path = _encode_and_copy(clip, directory, _TAKE_PRESET, basename)
        _stamp_take(stamp, version)
    except Exception as exc:
        log.exception(
            "Send to Edit failed for take v%s of %s", version, stamp.shot_code
        )
        return (
            DestinationOutcome(
                SEND_TO_EDIT_DESTINATION_NAME, ok=False, detail=_reason(exc)
            ),
            basename,
        )
    return (
        DestinationOutcome(
            SEND_TO_EDIT_DESTINATION_NAME, ok=True, detail=str(final_path)
        ),
        basename,
    )


def _stamp_take(stamp: PrevisStamp, version: int) -> None:
    """Append the take to its shot and point the shot's current take at it."""
    take = Take(
        version=version,
        source_filename=stamp.source_filename,
        camera=stamp.camera,
        created_at=utcnow_iso(),
        duration_frames=stamp.duration_frames,
    )
    mutate_manifest(
        stamp.sequence_code,
        lambda manifest: manifest.add_take(stamp.shot_code, take),
        previs_root=stamp.previs_root,
    )


def _deliver_to_folder(
    clip: PreviewClip, destination: Destination, basename: str
) -> DestinationOutcome:
    try:
        final_path = _encode_and_copy(
            clip, destination.directory, destination.preset, basename
        )
    except Exception as exc:
        log.exception("Confirm delivery to '%s' failed", destination.name)
        return DestinationOutcome(destination.name, ok=False, detail=_reason(exc))
    return DestinationOutcome(destination.name, ok=True, detail=str(final_path))


def _encode_and_copy(
    clip: PreviewClip, directory: Path, preset: FFmpegPreset, basename: str
) -> Path:
    """Encode the clip to `preset` and copy it into `directory`, returning the
    delivered path. Shared by folder deliveries and the previs take."""
    movie = _encoded_movie(clip, preset, basename)
    directory.mkdir(mode=0o770, parents=True, exist_ok=True)
    final_path = directory / movie.name
    shutil.copyfile(movie, final_path)
    return final_path


def _upload_to_shotgrid(
    clip: PreviewClip, choices: ConfirmChoices, basename: str
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
        movie = _encoded_movie(clip, FFmpegPreset.WEB, basename)
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


def _encoded_movie(clip: PreviewClip, preset: FFmpegPreset, basename: str) -> Path:
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
            frame_rate=clip.fps,
        ),
        output_path=movie,
        preset=preset,
        frame_rate=clip.fps,
        start_frame=clip.frame_start,
    )


def _reason(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


__all__ = [
    "SEND_TO_EDIT_DESTINATION_NAME",
    "SHOTGRID_DESTINATION_NAME",
    "ConfirmChoices",
    "ConfirmResult",
    "DestinationOutcome",
    "confirm_clip",
]
