"""The Confirm engine: persists a viewed preview to its checked destinations."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import attrs

from pipe.core.playblast.clip import (
    Destination,
    DestinationId,
    DiskDestination,
    PreviewClip,
    PrevisTakeDestination,
    ShotGridDestination,
)
from pipe.core.playblast.encoding import build_image_input_chain, encode_movie
from pipe.core.playblast.naming import existing_filenames, next_versioned_basename
from pipe.core.playblast.presets import FFmpegPreset
from pipe.core.playblast.review.versions import (
    PlayblastVersionUploadRequest,
    find_playblast_version_codes,
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


@attrs.frozen
class ChosenDisk:
    destination: DiskDestination
    # Browsable rows let the artist repoint the folder, so this can differ from
    # the directory the DCC declared.
    directory: Path


@attrs.frozen
class ChosenShotGrid:
    destination: ShotGridDestination
    playlist_id: int | None = None
    description: str | None = None


@attrs.frozen
class ChosenTake:
    destination: PrevisTakeDestination


ChosenDestination = ChosenDisk | ChosenShotGrid | ChosenTake


@attrs.frozen
class DestinationOutcome:
    """One delivery result. `detail` is what the artist reads; `path` is set
    only where the movie landed somewhere it will stay."""

    id: DestinationId
    ok: bool
    detail: str
    path: Path | None = None


@attrs.frozen
class ConfirmResult:
    basename: str
    outcomes: tuple[DestinationOutcome, ...]


def confirm_clip(
    clip: PreviewClip,
    chosen: tuple[ChosenDestination, ...],
    *,
    basename: str | None = None,
) -> ConfirmResult:
    """Deliver one clip to every checked destination."""

    outcomes: list[DestinationOutcome] = []

    take = next((choice for choice in chosen if isinstance(choice, ChosenTake)), None)
    if take is not None:
        take_outcome, take_basename = _deliver_take(clip, take.destination)
        outcomes.append(take_outcome)
        # A take that failed before it could allocate a version yields no
        # basename; the folders then version themselves as usual below.
        basename = basename or take_basename

    if basename is None:
        basename = _next_basename(clip, chosen)

    for choice in chosen:
        if isinstance(choice, ChosenDisk):
            outcomes.append(_deliver_to_folder(clip, choice, basename))

    kept = next((outcome.path for outcome in outcomes if outcome.path), None)
    for choice in chosen:
        if isinstance(choice, ChosenShotGrid):
            outcomes.append(_upload_to_shotgrid(clip, choice, basename, kept))

    return ConfirmResult(basename=basename, outcomes=tuple(outcomes))


def _next_basename(clip: PreviewClip, chosen: tuple[ChosenDestination, ...]) -> str:
    """Version past every folder the clip declares, not only the checked ones:
    unchecking a folder must not rewind the count and overwrite what is in it."""
    directories = {
        destination.directory
        for destination in clip.destinations
        if isinstance(destination, DiskDestination)
    } | {choice.directory for choice in chosen if isinstance(choice, ChosenDisk)}

    return next_versioned_basename(
        clip.output_prefix,
        [
            *existing_filenames(directories),
            *_shotgrid_version_codes(clip.output_prefix, chosen),
        ],
    )


def _shotgrid_version_codes(
    prefix: str, chosen: tuple[ChosenDestination, ...]
) -> tuple[str, ...]:
    """Queried only when ShotGrid is checked. Unlike a folder, a Version code
    that repeats overwrites nothing, so an unchecked row is not worth a round
    trip on the Confirm thread."""
    if not any(isinstance(choice, ChosenShotGrid) for choice in chosen):
        return ()
    try:
        return find_playblast_version_codes(prefix)
    except Exception:
        # Losing the delivery to a failed read is worse than a repeated code.
        log.exception("Could not read existing ShotGrid Version codes for %s", prefix)
        return ()


def _deliver_take(
    clip: PreviewClip, destination: PrevisTakeDestination
) -> tuple[DestinationOutcome, str | None]:
    """Returns the outcome and the version basename the take allocated."""
    stamp = destination.stamp
    try:
        version = load_manifest(
            stamp.sequence_code, previs_root=stamp.previs_root
        ).next_take_version(stamp.shot_code)
    except Exception as exc:
        log.exception("Could not allocate a take version for %s", stamp.shot_code)
        return _failed(destination, exc), None

    basename = naming.take_filename(stamp.shot_code, version).removesuffix(
        naming.TAKE_SUFFIX
    )
    directory = playblasts_dir(stamp.sequence_code, previs_root=stamp.previs_root)
    try:
        final_path = _encode_and_copy(clip, directory, destination.preset, basename)
        _stamp_take(destination, version)
    except Exception as exc:
        log.exception(
            "Send to Edit failed for take v%s of %s", version, stamp.shot_code
        )
        return _failed(destination, exc), basename
    return _delivered(destination, final_path), basename


def _stamp_take(destination: PrevisTakeDestination, version: int) -> None:
    stamp = destination.stamp
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
    clip: PreviewClip, chosen: ChosenDisk, basename: str
) -> DestinationOutcome:
    destination = chosen.destination
    try:
        final_path = _encode_and_copy(
            clip, chosen.directory, destination.preset, basename
        )
    except Exception as exc:
        log.exception("Confirm delivery to '%s' failed", destination.name)
        return _failed(destination, exc)
    return _delivered(destination, final_path)


def _encode_and_copy(
    clip: PreviewClip, directory: Path, preset: FFmpegPreset, basename: str
) -> Path:
    movie = _encoded_movie(clip, preset, basename)
    directory.mkdir(mode=0o770, parents=True, exist_ok=True)
    final_path = directory / movie.name
    shutil.copyfile(movie, final_path)
    return final_path


def _upload_to_shotgrid(
    clip: PreviewClip,
    chosen: ChosenShotGrid,
    basename: str,
    disk_path: Path | None,
) -> DestinationOutcome:
    destination = chosen.destination
    try:
        result = upload_playblast_version(
            PlayblastVersionUploadRequest(
                entity=destination.entity,
                movie_path=_encoded_movie(clip, destination.preset, basename),
                version_name=basename,
                description=chosen.description,
                artist_display_name=destination.artist_display_name,
                review_playlist_id=chosen.playlist_id,
                disk_path=disk_path,
            )
        )
    except Exception as exc:
        log.exception("ShotGrid upload failed for %s", destination.entity.description)
        return _failed(destination, exc)

    return DestinationOutcome(
        id=destination.id,
        ok=result.ok,
        detail=" ".join([result.message, *result.warnings]),
    )


def _encoded_movie(clip: PreviewClip, preset: FFmpegPreset, basename: str) -> Path:
    """Encode once per (preset, basename)"""
    directory = clip.frames_dir / preset.name.lower()
    movie = directory / f"{basename}.{preset.ext}"
    if movie.exists():
        return movie
    directory.mkdir(parents=True, exist_ok=True)
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


def _delivered(destination: Destination, path: Path) -> DestinationOutcome:
    return DestinationOutcome(id=destination.id, ok=True, detail=str(path), path=path)


def _failed(destination: Destination, exc: Exception) -> DestinationOutcome:
    return DestinationOutcome(
        id=destination.id, ok=False, detail=str(exc) or exc.__class__.__name__
    )


__all__ = [
    "ChosenDestination",
    "ChosenDisk",
    "ChosenShotGrid",
    "ChosenTake",
    "ConfirmResult",
    "DestinationOutcome",
    "confirm_clip",
]
