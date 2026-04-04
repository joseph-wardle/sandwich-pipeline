from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

UPLOAD_STATUS_SUCCESS = "success"
UPLOAD_STATUS_FAILED = "failed"
UPLOAD_TARGET_VERSION_ONLY = "version_only"
UPLOAD_TARGET_REVIEW = "review"
_SUPPORTED_UPLOAD_TARGETS = {
    UPLOAD_TARGET_VERSION_ONLY,
    UPLOAD_TARGET_REVIEW,
}


@dataclass(frozen=True)
class PlayblastVersionUploadRequest:
    """Normalized input for creating and uploading a ShotGrid Version."""

    shot_code: str
    movie_path: Path | str
    version_name: str
    description: str | None = None
    path_to_frames: str | None = None
    artist_display_name: str | None = None
    task_id: int | None = None
    upload_target: str = UPLOAD_TARGET_VERSION_ONLY
    review_playlist_id: int | None = None
    upload_field: str = "sg_uploaded_movie"
    extra_version_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetPlayblastVersionUploadRequest:
    """Normalized input for creating and uploading an Asset ShotGrid Version."""

    asset_display_name: str
    movie_path: Path | str
    version_name: str
    description: str | None = None
    path_to_frames: str | None = None
    artist_display_name: str | None = None
    task_id: int | None = None
    upload_target: str = UPLOAD_TARGET_VERSION_ONLY
    review_playlist_id: int | None = None
    upload_field: str = "sg_uploaded_movie"
    extra_version_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlayblastReviewPlaylistOption:
    """Normalized review playlist option for UI selection lists."""

    playlist_id: int
    code: str
    updated_at: Any | None = None
    created_at: Any | None = None

    @property
    def display_name(self) -> str:
        code = self.code.strip()
        if code:
            return code
        return f"Playlist {self.playlist_id}"


@dataclass(frozen=True)
class PlayblastVersionUploadResult:
    """Outcome for a playblast ShotGrid upload attempt."""

    status: str
    message: str
    shot_code: str
    version_name: str
    movie_path: Path | None = None
    version_id: int | None = None
    attachment_id: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == UPLOAD_STATUS_SUCCESS


@dataclass(frozen=True)
class AssetPlayblastVersionUploadResult:
    """Outcome for an asset playblast ShotGrid upload attempt."""

    status: str
    message: str
    asset_display_name: str
    version_name: str
    movie_path: Path | None = None
    version_id: int | None = None
    attachment_id: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == UPLOAD_STATUS_SUCCESS


@dataclass(frozen=True)
class _NormalizedUploadRequest:
    shot_code: str
    movie_path: Path
    version_name: str
    description: str | None
    path_to_frames: str | None
    artist_display_name: str | None
    task_id: int | None
    upload_target: str
    review_playlist_id: int | None
    upload_field: str
    extra_version_fields: dict[str, Any]


@dataclass(frozen=True)
class _NormalizedAssetUploadRequest:
    asset_display_name: str
    movie_path: Path
    version_name: str
    description: str | None
    path_to_frames: str | None
    artist_display_name: str | None
    task_id: int | None
    upload_target: str
    review_playlist_id: int | None
    upload_field: str
    extra_version_fields: dict[str, Any]


def default_version_name_from_movie_path(movie_path: Path | str) -> str:
    """Derive a default Version code from the playblast filename stem."""
    return Path(str(movie_path)).stem.strip()


def resolve_preferred_upload_movie_path(
    output_paths: Iterable[Path | str],
    *,
    preferred_paths: Iterable[Path | str] | None = None,
) -> Path | None:
    """Resolve a deterministic movie path for ShotGrid upload.

    Selection order:
    1) first valid file in `preferred_paths`
    2) first valid file in `output_paths`

    A valid file exists on disk and is non-empty.
    """

    normalized_outputs = _normalized_unique_paths(output_paths)
    normalized_preferred = _normalized_unique_paths(preferred_paths or [])

    for path in normalized_preferred:
        if _is_valid_movie_file(path):
            return path

    for path in normalized_outputs:
        if _is_valid_movie_file(path):
            return path

    return None


def list_recent_review_playlists(
    *,
    conn: Any | None = None,
    limit: int = 10,
) -> tuple[PlayblastReviewPlaylistOption, ...]:
    """Return normalized recent review playlists for UI upload target selectors."""

    connection = conn or _default_db_connection()
    raw_rows = connection.get_recent_review_playlists(limit=limit)

    normalized_options: list[PlayblastReviewPlaylistOption] = []
    seen_playlist_ids: set[int] = set()
    for raw_row in raw_rows:
        option = _normalize_review_playlist_option(raw_row)
        if option is None:
            continue
        if option.playlist_id in seen_playlist_ids:
            continue
        seen_playlist_ids.add(option.playlist_id)
        normalized_options.append(option)

    return tuple(normalized_options)


def upload_playblast_version(
    request: PlayblastVersionUploadRequest,
    *,
    conn: Any | None = None,
) -> PlayblastVersionUploadResult:
    """Create a ShotGrid Version for a shot and upload the playblast movie.

    This is the single entrypoint for playblast-to-ShotGrid uploads.
    """

    normalized_or_error = _normalize_request(request)
    if isinstance(normalized_or_error, PlayblastVersionUploadResult):
        return normalized_or_error
    normalized = normalized_or_error

    try:
        connection = conn or _default_db_connection()
    except Exception as exc:
        log.exception("Could not resolve ShotGrid connection")
        return _failed_result(
            normalized,
            "Could not connect to ShotGrid: " f"{_format_exception_details(exc)}",
        )

    try:
        shot = connection.get_shot_by_code(normalized.shot_code)
    except Exception as exc:
        log.exception("Could not resolve shot '%s' in ShotGrid", normalized.shot_code)
        return _failed_result(
            normalized,
            "Could not resolve shot "
            f"'{normalized.shot_code}' in ShotGrid: "
            f"{_format_exception_details(exc)}",
        )

    shot_id = _extract_entity_id(shot)
    if shot_id is None:
        return _failed_result(
            normalized,
            f"Shot '{normalized.shot_code}' is missing a valid ShotGrid id.",
        )

    warnings: list[str] = []
    user_id = _resolve_user_id(connection, normalized.artist_display_name, warnings)

    try:
        created_version = connection.create_version_for_shot(
            shot=shot,
            code=normalized.version_name,
            user=user_id,
            task=normalized.task_id,
            video_path=normalized.path_to_frames,
            description=normalized.description,
            # Review linking is handled as a separate final step so upload success
            # is preserved even if playlist linking fails.
            playlist_id=None,
            extra_fields=normalized.extra_version_fields,
        )
    except Exception as exc:
        log.exception(
            "ShotGrid Version creation failed for shot '%s'", normalized.shot_code
        )
        return _failed_result(
            normalized,
            "ShotGrid Version creation failed: " f"{_format_exception_details(exc)}",
            warnings=warnings,
        )

    version_id = _extract_entity_id(created_version)
    if version_id is None:
        return _failed_result(
            normalized,
            "ShotGrid did not return a valid Version id after creation.",
            warnings=warnings,
        )

    try:
        attachment_id = connection.upload_version_movie(
            version_id,
            str(normalized.movie_path),
            field=normalized.upload_field,
        )
    except Exception as exc:
        log.exception("ShotGrid movie upload failed for Version %s", version_id)
        return _failed_result(
            normalized,
            "ShotGrid movie upload failed: " f"{_format_exception_details(exc)}",
            version_id=version_id,
            warnings=warnings,
        )

    review_linked = False
    if (
        normalized.upload_target == UPLOAD_TARGET_REVIEW
        and normalized.review_playlist_id is not None
    ):
        try:
            connection.link_version_to_playlist(
                version_id=version_id,
                playlist_id=normalized.review_playlist_id,
            )
            review_linked = True
        except Exception as exc:
            failure_reason = _format_exception_details(exc)
            log.exception(
                "ShotGrid review link failed "
                "(shot_code=%s, version_id=%s, playlist_id=%s, reason=%s)",
                normalized.shot_code,
                version_id,
                normalized.review_playlist_id,
                failure_reason,
            )
            warnings.append(
                "Version upload succeeded, but linking to review playlist "
                f"{normalized.review_playlist_id} failed: "
                f"{failure_reason}"
            )

    return PlayblastVersionUploadResult(
        status=UPLOAD_STATUS_SUCCESS,
        message=_success_message_for_upload_outcome(
            normalized.upload_target,
            review_linked=review_linked,
        ),
        shot_code=normalized.shot_code,
        version_name=normalized.version_name,
        movie_path=normalized.movie_path,
        version_id=version_id,
        attachment_id=_extract_entity_id(attachment_id),
        warnings=tuple(warnings),
    )


def upload_asset_playblast_version(
    request: AssetPlayblastVersionUploadRequest,
    *,
    conn: Any | None = None,
) -> AssetPlayblastVersionUploadResult:
    """Create a ShotGrid Version for an asset and upload the playblast movie."""

    normalized_or_error = _normalize_asset_request(request)
    if isinstance(normalized_or_error, AssetPlayblastVersionUploadResult):
        return normalized_or_error
    normalized = normalized_or_error

    try:
        connection = conn or _default_db_connection()
    except Exception as exc:
        log.exception("Could not resolve ShotGrid connection")
        return _failed_asset_result(
            normalized,
            "Could not connect to ShotGrid: " f"{_format_exception_details(exc)}",
        )

    try:
        asset = connection.get_asset_by_display_name(normalized.asset_display_name)
    except Exception as exc:
        log.exception(
            "Could not resolve asset '%s' in ShotGrid", normalized.asset_display_name
        )
        return _failed_asset_result(
            normalized,
            "Could not resolve asset "
            f"'{normalized.asset_display_name}' in ShotGrid: "
            f"{_format_exception_details(exc)}",
        )

    asset_id = _extract_entity_id(asset)
    if asset_id is None:
        return _failed_asset_result(
            normalized,
            (
                f"Asset '{normalized.asset_display_name}' is missing a valid "
                "ShotGrid id."
            ),
        )

    warnings: list[str] = []
    user_id = _resolve_user_id(connection, normalized.artist_display_name, warnings)

    try:
        created_version = connection.create_version_for_asset(
            asset=asset,
            code=normalized.version_name,
            user=user_id,
            task=normalized.task_id,
            video_path=normalized.path_to_frames,
            description=normalized.description,
            playlist_id=None,
            extra_fields=normalized.extra_version_fields,
        )
    except Exception as exc:
        log.exception(
            "ShotGrid Version creation failed for asset '%s'",
            normalized.asset_display_name,
        )
        return _failed_asset_result(
            normalized,
            "ShotGrid Version creation failed: " f"{_format_exception_details(exc)}",
            warnings=warnings,
        )

    version_id = _extract_entity_id(created_version)
    if version_id is None:
        return _failed_asset_result(
            normalized,
            "ShotGrid did not return a valid Version id after creation.",
            warnings=warnings,
        )

    try:
        attachment_id = connection.upload_version_movie(
            version_id,
            str(normalized.movie_path),
            field=normalized.upload_field,
        )
    except Exception as exc:
        log.exception("ShotGrid movie upload failed for Version %s", version_id)
        return _failed_asset_result(
            normalized,
            "ShotGrid movie upload failed: " f"{_format_exception_details(exc)}",
            version_id=version_id,
            warnings=warnings,
        )

    review_linked = False
    if (
        normalized.upload_target == UPLOAD_TARGET_REVIEW
        and normalized.review_playlist_id is not None
    ):
        try:
            connection.link_version_to_playlist(
                version_id=version_id,
                playlist_id=normalized.review_playlist_id,
            )
            review_linked = True
        except Exception as exc:
            failure_reason = _format_exception_details(exc)
            log.exception(
                "ShotGrid review link failed "
                "(asset_display_name=%s, version_id=%s, playlist_id=%s, reason=%s)",
                normalized.asset_display_name,
                version_id,
                normalized.review_playlist_id,
                failure_reason,
            )
            warnings.append(
                "Version upload succeeded, but linking to review playlist "
                f"{normalized.review_playlist_id} failed: "
                f"{failure_reason}"
            )

    return AssetPlayblastVersionUploadResult(
        status=UPLOAD_STATUS_SUCCESS,
        message=_success_message_for_upload_outcome(
            normalized.upload_target,
            review_linked=review_linked,
        ),
        asset_display_name=normalized.asset_display_name,
        version_name=normalized.version_name,
        movie_path=normalized.movie_path,
        version_id=version_id,
        attachment_id=_extract_entity_id(attachment_id),
        warnings=tuple(warnings),
    )


def _normalize_request(
    request: PlayblastVersionUploadRequest,
) -> _NormalizedUploadRequest | PlayblastVersionUploadResult:
    shot_code = str(request.shot_code).strip()
    if not shot_code:
        return PlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message="Shot code is required for ShotGrid upload.",
            shot_code="",
            version_name=str(request.version_name).strip(),
            movie_path=None,
        )

    version_name = str(request.version_name).strip()
    if not version_name:
        return PlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message="Version name is required for ShotGrid upload.",
            shot_code=shot_code,
            version_name="",
            movie_path=None,
        )

    movie_path = Path(str(request.movie_path)).expanduser().resolve()
    if not movie_path.exists() or not movie_path.is_file():
        return PlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=f"Playblast movie file was not found: {movie_path}",
            shot_code=shot_code,
            version_name=version_name,
            movie_path=movie_path,
        )

    if movie_path.stat().st_size < 1:
        return PlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=f"Playblast movie file is empty: {movie_path}",
            shot_code=shot_code,
            version_name=version_name,
            movie_path=movie_path,
        )

    upload_field = str(request.upload_field).strip()
    if not upload_field:
        return PlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message="Upload field cannot be empty.",
            shot_code=shot_code,
            version_name=version_name,
            movie_path=movie_path,
        )

    upload_target = _normalize_upload_target(request.upload_target)
    if upload_target is None:
        return PlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=(
                "Upload target must be 'version_only' or 'review' for ShotGrid upload."
            ),
            shot_code=shot_code,
            version_name=version_name,
            movie_path=movie_path,
        )

    review_playlist_id = _optional_positive_int(request.review_playlist_id)
    if upload_target == UPLOAD_TARGET_REVIEW and review_playlist_id is None:
        return PlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=(
                "A valid review playlist id is required when upload target is 'review'."
            ),
            shot_code=shot_code,
            version_name=version_name,
            movie_path=movie_path,
        )
    if upload_target == UPLOAD_TARGET_VERSION_ONLY:
        review_playlist_id = None

    description = _optional_text(request.description)
    path_to_frames = _optional_text(request.path_to_frames) or str(movie_path)
    artist_display_name = _optional_text(request.artist_display_name)
    task_id = _optional_positive_int(request.task_id)

    normalized_extra_fields: dict[str, Any] = {}
    for field_name, value in request.extra_version_fields.items():
        normalized_name = str(field_name).strip()
        if not normalized_name:
            continue
        if value is None:
            continue
        normalized_extra_fields[normalized_name] = value

    return _NormalizedUploadRequest(
        shot_code=shot_code,
        movie_path=movie_path,
        version_name=version_name,
        description=description,
        path_to_frames=path_to_frames,
        artist_display_name=artist_display_name,
        task_id=task_id,
        upload_target=upload_target,
        review_playlist_id=review_playlist_id,
        upload_field=upload_field,
        extra_version_fields=normalized_extra_fields,
    )


def _normalize_asset_request(
    request: AssetPlayblastVersionUploadRequest,
) -> _NormalizedAssetUploadRequest | AssetPlayblastVersionUploadResult:
    asset_display_name = str(request.asset_display_name).strip()
    if not asset_display_name:
        return AssetPlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message="Asset display name is required for ShotGrid upload.",
            asset_display_name="",
            version_name=str(request.version_name).strip(),
            movie_path=None,
        )

    version_name = str(request.version_name).strip()
    if not version_name:
        return AssetPlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message="Version name is required for ShotGrid upload.",
            asset_display_name=asset_display_name,
            version_name="",
            movie_path=None,
        )

    movie_path = Path(str(request.movie_path)).expanduser().resolve()
    if not movie_path.exists() or not movie_path.is_file():
        return AssetPlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=f"Playblast movie file was not found: {movie_path}",
            asset_display_name=asset_display_name,
            version_name=version_name,
            movie_path=movie_path,
        )

    if movie_path.stat().st_size < 1:
        return AssetPlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=f"Playblast movie file is empty: {movie_path}",
            asset_display_name=asset_display_name,
            version_name=version_name,
            movie_path=movie_path,
        )

    upload_field = str(request.upload_field).strip()
    if not upload_field:
        return AssetPlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message="Upload field cannot be empty.",
            asset_display_name=asset_display_name,
            version_name=version_name,
            movie_path=movie_path,
        )

    upload_target = _normalize_upload_target(request.upload_target)
    if upload_target is None:
        return AssetPlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=(
                "Upload target must be 'version_only' or 'review' for ShotGrid upload."
            ),
            asset_display_name=asset_display_name,
            version_name=version_name,
            movie_path=movie_path,
        )

    review_playlist_id = _optional_positive_int(request.review_playlist_id)
    if upload_target == UPLOAD_TARGET_REVIEW and review_playlist_id is None:
        return AssetPlayblastVersionUploadResult(
            status=UPLOAD_STATUS_FAILED,
            message=(
                "A valid review playlist id is required when upload target is 'review'."
            ),
            asset_display_name=asset_display_name,
            version_name=version_name,
            movie_path=movie_path,
        )
    if upload_target == UPLOAD_TARGET_VERSION_ONLY:
        review_playlist_id = None

    description = _optional_text(request.description)
    path_to_frames = _optional_text(request.path_to_frames) or str(movie_path)
    artist_display_name = _optional_text(request.artist_display_name)
    task_id = _optional_positive_int(request.task_id)

    normalized_extra_fields: dict[str, Any] = {}
    for field_name, value in request.extra_version_fields.items():
        normalized_name = str(field_name).strip()
        if not normalized_name:
            continue
        if value is None:
            continue
        normalized_extra_fields[normalized_name] = value

    return _NormalizedAssetUploadRequest(
        asset_display_name=asset_display_name,
        movie_path=movie_path,
        version_name=version_name,
        description=description,
        path_to_frames=path_to_frames,
        artist_display_name=artist_display_name,
        task_id=task_id,
        upload_target=upload_target,
        review_playlist_id=review_playlist_id,
        upload_field=upload_field,
        extra_version_fields=normalized_extra_fields,
    )


def _normalized_unique_paths(paths: Iterable[Path | str]) -> list[Path]:
    normalized_paths: list[Path] = []
    seen_paths: set[Path] = set()
    for raw_path in paths:
        path = Path(str(raw_path)).expanduser().resolve()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        normalized_paths.append(path)
    return normalized_paths


def _is_valid_movie_file(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except Exception:
        return False


def _normalize_review_playlist_option(
    raw_row: Any,
) -> PlayblastReviewPlaylistOption | None:
    if not isinstance(raw_row, Mapping):
        return None

    playlist_id = _extract_entity_id(raw_row)
    if playlist_id is None:
        return None

    code = str(raw_row.get("code") or "").strip()
    return PlayblastReviewPlaylistOption(
        playlist_id=playlist_id,
        code=code,
        updated_at=raw_row.get("updated_at"),
        created_at=raw_row.get("created_at"),
    )


def _normalize_upload_target(value: Any) -> str | None:
    normalized = str(value).strip().lower()
    if normalized in _SUPPORTED_UPLOAD_TARGETS:
        return normalized
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    if parsed < 1:
        return None
    return parsed


def _success_message_for_upload_outcome(
    upload_target: str,
    *,
    review_linked: bool,
) -> str:
    if upload_target == UPLOAD_TARGET_REVIEW:
        if review_linked:
            return (
                "Version created, movie uploaded, and linked to the selected review "
                "playlist."
            )
        return (
            "Version created and movie uploaded to ShotGrid. Review playlist linking "
            "was not completed."
        )
    return "Version created and movie uploaded to ShotGrid."


def _format_exception_details(exc: BaseException) -> str:
    """Flatten exception/cause/context chain into one readable message."""
    messages: list[str] = []
    visited_exception_ids: set[int] = set()
    current_exc: BaseException | None = exc

    while current_exc is not None:
        exception_id = id(current_exc)
        if exception_id in visited_exception_ids:
            break
        visited_exception_ids.add(exception_id)

        exception_name = type(current_exc).__name__
        exception_message = str(current_exc).strip()
        if exception_message:
            messages.append(f"{exception_name}: {exception_message}")
        else:
            messages.append(exception_name)

        current_exc = current_exc.__cause__ or current_exc.__context__

    if not messages:
        return "Unknown exception."
    return " <- ".join(messages)


def _default_db_connection() -> Any:
    from env_sg import DB_Config

    from pipe.db import DB

    return DB.Get(DB_Config)


def _resolve_user_id(
    connection: Any,
    artist_display_name: str | None,
    warnings: list[str],
) -> int | None:
    if not artist_display_name:
        return None

    try:
        user = connection.get_user_by_name(artist_display_name)
    except Exception:
        warnings.append(
            f"Could not resolve ShotGrid user '{artist_display_name}'. Continuing without user link."
        )
        return None

    user_id = _extract_entity_id(user)
    if user_id is None:
        warnings.append(
            f"Resolved user for '{artist_display_name}' is missing a valid id. Continuing without user link."
        )
        return None
    return user_id


def _extract_entity_id(entity: Any) -> int | None:
    if isinstance(entity, int) and entity > 0:
        return entity
    if isinstance(entity, Mapping):
        entity_id = entity.get("id")
        if isinstance(entity_id, int) and entity_id > 0:
            return entity_id
        return None

    entity_id = getattr(entity, "id", None)
    if isinstance(entity_id, int) and entity_id > 0:
        return entity_id
    return None


def _failed_result(
    request: _NormalizedUploadRequest,
    message: str,
    *,
    version_id: int | None = None,
    warnings: list[str] | None = None,
) -> PlayblastVersionUploadResult:
    return PlayblastVersionUploadResult(
        status=UPLOAD_STATUS_FAILED,
        message=message,
        shot_code=request.shot_code,
        version_name=request.version_name,
        movie_path=request.movie_path,
        version_id=version_id,
        warnings=tuple(warnings or []),
    )


def _failed_asset_result(
    request: _NormalizedAssetUploadRequest,
    message: str,
    *,
    version_id: int | None = None,
    warnings: list[str] | None = None,
) -> AssetPlayblastVersionUploadResult:
    return AssetPlayblastVersionUploadResult(
        status=UPLOAD_STATUS_FAILED,
        message=message,
        asset_display_name=request.asset_display_name,
        version_name=request.version_name,
        movie_path=request.movie_path,
        version_id=version_id,
        warnings=tuple(warnings or []),
    )


__all__ = [
    "AssetPlayblastVersionUploadRequest",
    "AssetPlayblastVersionUploadResult",
    "PlayblastReviewPlaylistOption",
    "PlayblastVersionUploadRequest",
    "PlayblastVersionUploadResult",
    "UPLOAD_STATUS_FAILED",
    "UPLOAD_STATUS_SUCCESS",
    "UPLOAD_TARGET_REVIEW",
    "UPLOAD_TARGET_VERSION_ONLY",
    "default_version_name_from_movie_path",
    "list_recent_review_playlists",
    "resolve_preferred_upload_movie_path",
    "upload_asset_playblast_version",
    "upload_playblast_version",
]
