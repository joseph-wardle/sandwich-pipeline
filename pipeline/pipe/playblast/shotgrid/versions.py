from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pipe.shotgrid import ShotGrid, ShotGridError, Task, User

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
    artist_display_name: str | None = None
    task_id: int | None = None
    upload_target: str = UPLOAD_TARGET_VERSION_ONLY
    review_playlist_id: int | None = None
    extra_version_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetPlayblastVersionUploadRequest:
    """Normalized input for creating and uploading an Asset ShotGrid Version."""

    asset_display_name: str
    movie_path: Path | str
    version_name: str
    description: str | None = None
    artist_display_name: str | None = None
    task_id: int | None = None
    upload_target: str = UPLOAD_TARGET_VERSION_ONLY
    review_playlist_id: int | None = None
    extra_version_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlayblastVersionUploadResult:
    """Outcome for a playblast ShotGrid upload attempt."""

    status: str
    message: str
    shot_code: str
    version_name: str
    movie_path: Path | None = None
    version_id: int | None = None
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
    artist_display_name: str | None
    task_id: int | None
    upload_target: str
    review_playlist_id: int | None
    extra_version_fields: dict[str, Any]


@dataclass(frozen=True)
class _NormalizedAssetUploadRequest:
    asset_display_name: str
    movie_path: Path
    version_name: str
    description: str | None
    artist_display_name: str | None
    task_id: int | None
    upload_target: str
    review_playlist_id: int | None
    extra_version_fields: dict[str, Any]


def upload_playblast_version(
    request: PlayblastVersionUploadRequest,
    *,
    conn: ShotGrid | None = None,
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
        # Connect-time failures (missing env_sg.py, import errors, etc.) are
        # not ShotGridErrors; keep this catch broad and surface a friendly
        # message to the artist.
        log.exception("Could not resolve ShotGrid connection")
        return _failed_result(
            normalized,
            f"Could not connect to ShotGrid: {_format_exception_details(exc)}",
        )

    try:
        shot = connection.get_shot(code=normalized.shot_code)
    except ShotGridError as exc:
        log.exception("Could not resolve shot '%s' in ShotGrid", normalized.shot_code)
        return _failed_result(
            normalized,
            "Could not resolve shot "
            f"'{normalized.shot_code}' in ShotGrid: "
            f"{_format_exception_details(exc)}",
        )

    warnings: list[str] = []
    user = _resolve_user(connection, normalized.artist_display_name, warnings)
    task = _resolve_task(connection, normalized.task_id, warnings)

    try:
        version = connection.create_shot_version(
            shot,
            code=normalized.version_name,
            user=user,
            task=task,
            description=normalized.description,
            extra_fields=dict(normalized.extra_version_fields) or None,
        )
    except ShotGridError as exc:
        log.exception(
            "ShotGrid Version creation failed for shot '%s'", normalized.shot_code
        )
        return _failed_result(
            normalized,
            f"ShotGrid Version creation failed: {_format_exception_details(exc)}",
            warnings=warnings,
        )

    version_id = version.id
    try:
        connection.upload_movie(version, normalized.movie_path)
    except ShotGridError as exc:
        log.exception("ShotGrid movie upload failed for Version %s", version_id)
        return _failed_result(
            normalized,
            f"ShotGrid movie upload failed: {_format_exception_details(exc)}",
            version_id=version_id,
            warnings=warnings,
        )

    review_linked = False
    if (
        normalized.upload_target == UPLOAD_TARGET_REVIEW
        and normalized.review_playlist_id is not None
    ):
        try:
            playlist = connection.get_playlist(id=normalized.review_playlist_id)
            connection.link_to_playlist(version, playlist)
            review_linked = True
        except ShotGridError as exc:
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
        warnings=tuple(warnings),
    )


def upload_asset_playblast_version(
    request: AssetPlayblastVersionUploadRequest,
    *,
    conn: ShotGrid | None = None,
) -> AssetPlayblastVersionUploadResult:
    """Create a ShotGrid Version for an asset and upload the playblast movie."""

    normalized_or_error = _normalize_asset_request(request)
    if isinstance(normalized_or_error, AssetPlayblastVersionUploadResult):
        return normalized_or_error
    normalized = normalized_or_error

    try:
        connection = conn or _default_db_connection()
    except Exception as exc:
        # See note in upload_playblast_version: connect-time failures are
        # not ShotGridErrors, so this catch stays broad.
        log.exception("Could not resolve ShotGrid connection")
        return _failed_asset_result(
            normalized,
            f"Could not connect to ShotGrid: {_format_exception_details(exc)}",
        )

    try:
        asset = connection.get_asset(display_name=normalized.asset_display_name)
    except ShotGridError as exc:
        log.exception(
            "Could not resolve asset '%s' in ShotGrid", normalized.asset_display_name
        )
        return _failed_asset_result(
            normalized,
            "Could not resolve asset "
            f"'{normalized.asset_display_name}' in ShotGrid: "
            f"{_format_exception_details(exc)}",
        )

    warnings: list[str] = []
    user = _resolve_user(connection, normalized.artist_display_name, warnings)
    task = _resolve_task(connection, normalized.task_id, warnings)

    try:
        version = connection.create_asset_version(
            asset,
            code=normalized.version_name,
            user=user,
            task=task,
            description=normalized.description,
            extra_fields=dict(normalized.extra_version_fields) or None,
        )
    except ShotGridError as exc:
        log.exception(
            "ShotGrid Version creation failed for asset '%s'",
            normalized.asset_display_name,
        )
        return _failed_asset_result(
            normalized,
            f"ShotGrid Version creation failed: {_format_exception_details(exc)}",
            warnings=warnings,
        )

    version_id = version.id
    try:
        connection.upload_movie(version, normalized.movie_path)
    except ShotGridError as exc:
        log.exception("ShotGrid movie upload failed for Version %s", version_id)
        return _failed_asset_result(
            normalized,
            f"ShotGrid movie upload failed: {_format_exception_details(exc)}",
            version_id=version_id,
            warnings=warnings,
        )

    review_linked = False
    if (
        normalized.upload_target == UPLOAD_TARGET_REVIEW
        and normalized.review_playlist_id is not None
    ):
        try:
            playlist = connection.get_playlist(id=normalized.review_playlist_id)
            connection.link_to_playlist(version, playlist)
            review_linked = True
        except ShotGridError as exc:
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
        artist_display_name=artist_display_name,
        task_id=task_id,
        upload_target=upload_target,
        review_playlist_id=review_playlist_id,
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
        artist_display_name=artist_display_name,
        task_id=task_id,
        upload_target=upload_target,
        review_playlist_id=review_playlist_id,
        extra_version_fields=normalized_extra_fields,
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
    except (TypeError, ValueError):
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


def _default_db_connection() -> ShotGrid:
    # `env_sg` holds the gitignored production credentials; keep the import
    # lazy so importing this module on a host without credentials does not
    # raise at module-load time.
    from env_sg import DB_Config

    return ShotGrid.connect(DB_Config)


def _resolve_user(
    connection: ShotGrid,
    artist_display_name: str | None,
    warnings: list[str],
) -> User | None:
    """Resolve an artist display name to a ``User`` entity, or warn and return None."""
    if not artist_display_name:
        return None
    try:
        return connection.get_user(name=artist_display_name)
    except ShotGridError:
        warnings.append(
            f"Could not resolve ShotGrid user '{artist_display_name}'. "
            "Continuing without user link."
        )
        return None


def _resolve_task(
    connection: ShotGrid,
    task_id: int | None,
    warnings: list[str],
) -> Task | None:
    """Resolve a task id to a ``Task`` entity, or warn and return None."""
    if task_id is None:
        return None
    try:
        return connection.get_task(id=task_id)
    except ShotGridError:
        warnings.append(
            f"Could not resolve ShotGrid task id={task_id}. "
            "Continuing without task link."
        )
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
    "PlayblastVersionUploadRequest",
    "PlayblastVersionUploadResult",
    "UPLOAD_STATUS_FAILED",
    "UPLOAD_STATUS_SUCCESS",
    "UPLOAD_TARGET_REVIEW",
    "UPLOAD_TARGET_VERSION_ONLY",
    "upload_asset_playblast_version",
    "upload_playblast_version",
]
