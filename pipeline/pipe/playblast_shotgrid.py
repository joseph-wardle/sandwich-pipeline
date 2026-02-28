from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger(__name__)

UPLOAD_STATUS_SUCCESS = "success"
UPLOAD_STATUS_FAILED = "failed"


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
    playlist_id: int | None = None
    upload_field: str = "sg_uploaded_movie"
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
    playlist_id: int | None
    upload_field: str
    extra_version_fields: dict[str, Any]


def default_version_name_from_movie_path(movie_path: Path | str) -> str:
    """Derive a default Version code from the playblast filename stem."""
    return Path(str(movie_path)).stem.strip()


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
            f"Could not connect to ShotGrid: {exc}",
        )

    try:
        shot = connection.get_shot_by_code(normalized.shot_code)
    except Exception as exc:
        log.exception("Could not resolve shot '%s' in ShotGrid", normalized.shot_code)
        return _failed_result(
            normalized,
            f"Could not resolve shot '{normalized.shot_code}' in ShotGrid: {exc}",
        )

    shot_id = _extract_entity_id(shot)
    if shot_id is None:
        return _failed_result(
            normalized,
            f"Shot '{normalized.shot_code}' is missing a valid ShotGrid id.",
        )

    project_id = _resolve_project_id(connection)
    if project_id is None:
        return _failed_result(
            normalized,
            "Could not resolve ShotGrid project id from the DB connection.",
        )

    warnings: list[str] = []
    user_id = _resolve_user_id(connection, normalized.artist_display_name, warnings)

    payload = _build_version_payload(
        normalized,
        shot_id=shot_id,
        project_id=project_id,
        user_id=user_id,
    )

    shotgrid_client = _resolve_shotgrid_client(connection)
    if shotgrid_client is None:
        return _failed_result(
            normalized,
            "DB connection does not expose a ShotGrid client for Version creation.",
            warnings=warnings,
        )

    try:
        created_version = shotgrid_client.create("Version", payload)
    except Exception as exc:
        log.exception(
            "ShotGrid Version creation failed for shot '%s'", normalized.shot_code
        )
        return _failed_result(
            normalized,
            f"ShotGrid Version creation failed: {exc}",
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
            f"ShotGrid movie upload failed: {exc}",
            version_id=version_id,
            warnings=warnings,
        )

    return PlayblastVersionUploadResult(
        status=UPLOAD_STATUS_SUCCESS,
        message="Version created and movie uploaded to ShotGrid.",
        shot_code=normalized.shot_code,
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

    description = _optional_text(request.description)
    path_to_frames = _optional_text(request.path_to_frames) or str(movie_path)
    artist_display_name = _optional_text(request.artist_display_name)
    task_id = _optional_positive_int(request.task_id)
    playlist_id = _optional_positive_int(request.playlist_id)

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
        playlist_id=playlist_id,
        upload_field=upload_field,
        extra_version_fields=normalized_extra_fields,
    )


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


def _default_db_connection() -> Any:
    from env_sg import DB_Config

    from pipe.db import DB

    return DB.Get(DB_Config)


def _resolve_project_id(connection: Any) -> int | None:
    project_id = getattr(connection, "_id", None)
    if isinstance(project_id, int) and project_id > 0:
        return project_id
    return None


def _resolve_shotgrid_client(connection: Any) -> Any | None:
    return getattr(connection, "_sg", None)


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


def _build_version_payload(
    request: _NormalizedUploadRequest,
    *,
    shot_id: int,
    project_id: int,
    user_id: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": request.version_name,
        "entity": {"type": "Shot", "id": shot_id},
        "project": {"type": "Project", "id": project_id},
    }

    if request.description:
        payload["description"] = request.description
    if request.path_to_frames:
        payload["sg_path_to_frames"] = request.path_to_frames
    if user_id is not None:
        payload["user"] = {"type": "HumanUser", "id": user_id}
    if request.task_id is not None:
        payload["sg_task"] = {"type": "Task", "id": request.task_id}
    if request.playlist_id is not None:
        payload["playlists"] = [{"type": "Playlist", "id": request.playlist_id}]

    payload.update(request.extra_version_fields)
    return payload


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


__all__ = [
    "PlayblastVersionUploadRequest",
    "PlayblastVersionUploadResult",
    "UPLOAD_STATUS_FAILED",
    "UPLOAD_STATUS_SUCCESS",
    "default_version_name_from_movie_path",
    "upload_playblast_version",
]
