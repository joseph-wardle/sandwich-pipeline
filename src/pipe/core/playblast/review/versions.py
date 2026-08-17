"""Deliver a playblast to ShotGrid: create a Version, upload the movie, and
put it in front of reviewers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import assert_never

import attrs

from pipe.core.playblast.clip import (
    AssetEntity,
    ReviewEntity,
    ScratchEntity,
    ShotEntity,
    is_unlinked,
)
from pipe.core.playblast.review._connection import default_db_connection
from pipe.core.shotgrid import Asset, Shot, ShotGrid, ShotGridError, User, Version

log = logging.getLogger(__name__)


@attrs.frozen
class PlayblastVersionUploadRequest:
    entity: ReviewEntity
    movie_path: Path
    version_name: str
    description: str | None = None
    artist_display_name: str | None = None
    review_playlist_id: int | None = None
    # Where this playblast was also kept, if anywhere. `movie_path` is a temp
    # encode, so it is the only location worth recording on the Version.
    disk_path: Path | None = None


@attrs.frozen
class PlayblastVersionUploadResult:
    ok: bool
    message: str
    warnings: tuple[str, ...] = ()


def find_playblast_version_codes(prefix: str) -> tuple[str, ...]:
    """Existing ShotGrid Version codes starting with `prefix`."""
    return tuple(default_db_connection().find_version_codes(code_starts_with=prefix))


def upload_playblast_version(
    request: PlayblastVersionUploadRequest,
) -> PlayblastVersionUploadResult:
    rejection = _rejection(request)
    if rejection is not None:
        return _failed(rejection)

    try:
        connection = default_db_connection()
    except Exception as exc:
        # Connect-time failures (missing env_sg.py, import errors) are not
        # ShotGridErrors, so keep this catch broad.
        log.exception("Could not resolve ShotGrid connection")
        return _failed(f"Could not connect to ShotGrid: {_describe(exc)}")

    try:
        linked = _resolve_entity(connection, request.entity)
    except ShotGridError as exc:
        log.exception("Could not resolve %s in ShotGrid", request.entity.description)
        return _failed(
            f"Could not find {request.entity.description} in ShotGrid: "
            f"{_describe(exc)}"
        )

    warnings: list[str] = []
    try:
        version = connection.create_version(
            code=request.version_name,
            entity=linked,
            user=_resolve_user(connection, request.artist_display_name, warnings),
            description=_version_description(request),
            path_to_frames=request.disk_path,
        )
    except ShotGridError as exc:
        log.exception(
            "ShotGrid Version creation failed for %s", request.entity.description
        )
        return _failed(f"ShotGrid Version creation failed: {_describe(exc)}", warnings)

    try:
        connection.upload_movie(version, request.movie_path)
    except ShotGridError as exc:
        log.exception("ShotGrid movie upload failed for Version id=%s", version.id)
        return _failed(
            f"ShotGrid movie upload failed, leaving an empty Version "
            f"'{request.version_name}': {_describe(exc)}",
            warnings,
        )

    return _add_to_playlist(connection, version, request, warnings)


def _rejection(request: PlayblastVersionUploadRequest) -> str | None:
    """An artist-facing reason not to attempt the upload at all."""
    if not request.version_name.strip():
        return "This playblast has no version name to upload under."
    if not request.movie_path.is_file():
        return f"The playblast movie was not found: {request.movie_path}"
    if request.movie_path.stat().st_size < 1:
        return f"The playblast movie is empty: {request.movie_path}"
    if is_unlinked(request.entity) and request.review_playlist_id is None:
        return (
            "This scene has no shot or asset in ShotGrid, so its Version would "
            "only be findable inside a review playlist. Pick a playlist first."
        )
    return None


def _resolve_entity(connection: ShotGrid, entity: ReviewEntity) -> Shot | Asset | None:
    """`None` for an unlinked entity, which ShotGrid records at project level."""
    if isinstance(entity, ShotEntity):
        return connection.get_shot(code=entity.code)
    if isinstance(entity, AssetEntity):
        return connection.get_asset(display_name=entity.display_name)
    if isinstance(entity, ScratchEntity):
        return None
    assert_never(entity)


def _version_description(request: PlayblastVersionUploadRequest) -> str | None:
    parts = []
    # Nothing else on an unlinked Version says where it came from.
    if is_unlinked(request.entity):
        parts.append(f"From {request.entity.description}.")
    if request.description:
        parts.append(request.description)
    return "\n".join(parts) or None


def _add_to_playlist(
    connection: ShotGrid,
    version: Version,
    request: PlayblastVersionUploadRequest,
    warnings: list[str],
) -> PlayblastVersionUploadResult:
    uploaded = "Version created and movie uploaded to ShotGrid."
    if request.review_playlist_id is None:
        return _ok(uploaded, warnings)
    try:
        connection.link_to_playlist(version, playlist_id=request.review_playlist_id)
    except ShotGridError as exc:
        log.exception(
            "Could not add Version id=%s to playlist id=%s",
            version.id,
            request.review_playlist_id,
        )
        reason = _describe(exc)
        if is_unlinked(request.entity):
            return _failed(
                "The movie uploaded, but adding it to the review playlist "
                f"failed, so nobody would find it: {reason}",
                warnings,
            )
        warnings.append(f"Could not add the Version to the review playlist: {reason}")
        return _ok(uploaded, warnings)
    return _ok(
        "Version created, movie uploaded, and added to the review playlist.", warnings
    )


def _resolve_user(
    connection: ShotGrid,
    artist_display_name: str | None,
    warnings: list[str],
) -> User | None:
    if not artist_display_name:
        return None
    try:
        return connection.get_user(name=artist_display_name)
    except ShotGridError:
        log.exception("Could not resolve ShotGrid user '%s'", artist_display_name)
        warnings.append(
            f"Could not resolve ShotGrid user '{artist_display_name}'. "
            "Continuing without an artist link."
        )
        return None


def _ok(message: str, warnings: list[str]) -> PlayblastVersionUploadResult:
    return PlayblastVersionUploadResult(
        ok=True, message=message, warnings=tuple(warnings)
    )


def _failed(
    message: str, warnings: list[str] | None = None
) -> PlayblastVersionUploadResult:
    return PlayblastVersionUploadResult(
        ok=False, message=message, warnings=tuple(warnings or ())
    )


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


__all__ = [
    "PlayblastVersionUploadRequest",
    "PlayblastVersionUploadResult",
    "find_playblast_version_codes",
    "upload_playblast_version",
]
