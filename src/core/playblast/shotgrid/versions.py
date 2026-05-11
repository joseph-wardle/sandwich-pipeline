from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from core.shotgrid import ShotGrid, ShotGridError, Task, User

log = logging.getLogger(__name__)


class UploadTarget(StrEnum):
    """Where a playblast Version is delivered after upload."""

    VERSION_ONLY = "version_only"
    REVIEW = "review"


@dataclass(frozen=True)
class PlayblastEntity:
    """Identifies the ShotGrid entity (Shot or Asset) a playblast belongs to."""

    kind: Literal["shot", "asset"]
    value: str

    @classmethod
    def shot(cls, shot_code: str) -> PlayblastEntity:
        return cls(kind="shot", value=str(shot_code).strip())

    @classmethod
    def asset(cls, display_name: str) -> PlayblastEntity:
        return cls(kind="asset", value=str(display_name).strip())


@dataclass(frozen=True)
class PlayblastVersionUploadRequest:
    """Normalized input for creating and uploading a ShotGrid Version."""

    entity: PlayblastEntity
    movie_path: Path | str
    version_name: str
    description: str | None = None
    artist_display_name: str | None = None
    task_id: int | None = None
    upload_target: UploadTarget = UploadTarget.VERSION_ONLY
    review_playlist_id: int | None = None
    extra_version_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlayblastVersionUploadResult:
    """Outcome of a playblast ShotGrid upload attempt."""

    entity: PlayblastEntity
    version_name: str
    message: str
    movie_path: Path | None = None
    version_id: int | None = None
    warnings: tuple[str, ...] = ()
    _failed: bool = False

    @property
    def ok(self) -> bool:
        return not self._failed

    @property
    def failed(self) -> bool:
        return self._failed

    @classmethod
    def success(
        cls,
        request: PlayblastVersionUploadRequest,
        *,
        message: str,
        version_id: int,
        movie_path: Path,
        warnings: Sequence[str] = (),
    ) -> PlayblastVersionUploadResult:
        return cls(
            entity=request.entity,
            version_name=str(request.version_name).strip(),
            message=message,
            movie_path=movie_path,
            version_id=version_id,
            warnings=tuple(warnings),
            _failed=False,
        )

    @classmethod
    def failure(
        cls,
        request: PlayblastVersionUploadRequest,
        message: str,
        *,
        version_id: int | None = None,
        warnings: Sequence[str] = (),
    ) -> PlayblastVersionUploadResult:
        return cls(
            entity=request.entity,
            version_name=str(request.version_name).strip(),
            message=message,
            movie_path=None,
            version_id=version_id,
            warnings=tuple(warnings),
            _failed=True,
        )


@dataclass(frozen=True)
class _NormalizedUploadRequest:
    """A `PlayblastVersionUploadRequest` whose values have all been validated:
    movie_path is a real non-empty file, identifiers are non-empty, etc."""

    entity: PlayblastEntity
    movie_path: Path
    version_name: str
    description: str | None
    artist_display_name: str | None
    task_id: int | None
    upload_target: UploadTarget
    review_playlist_id: int | None
    extra_version_fields: dict[str, Any]


class _UploadValidationError(Exception):
    """Carries a user-facing message for upload-input validation failures.
    Caught once at the top of `upload_playblast_version` and converted to a
    failed Result."""


# Dispatch table for the entity-kind-specific SG calls. The two callables
# differ only in which SG client method they use; everything else in the
# upload flow is identical.
_EntityLookup = Callable[[ShotGrid, str], Any]
_VersionCreator = Callable[..., Any]


def _shot_lookup(connection: ShotGrid, value: str) -> Any:
    return connection.get_shot(code=value)


def _asset_lookup(connection: ShotGrid, value: str) -> Any:
    return connection.get_asset(display_name=value)


def _create_shot_version(connection: ShotGrid, entity: Any, **kwargs: Any) -> Any:
    return connection.create_shot_version(entity, **kwargs)


def _create_asset_version(connection: ShotGrid, entity: Any, **kwargs: Any) -> Any:
    return connection.create_asset_version(entity, **kwargs)


_ENTITY_DISPATCH: dict[str, tuple[_EntityLookup, _VersionCreator]] = {
    "shot": (_shot_lookup, _create_shot_version),
    "asset": (_asset_lookup, _create_asset_version),
}


def upload_playblast_version(
    request: PlayblastVersionUploadRequest,
    *,
    conn: ShotGrid | None = None,
) -> PlayblastVersionUploadResult:
    """Create a ShotGrid Version for a Shot or Asset and upload the playblast.

    Single entrypoint for both shot and asset playblasts; dispatches on
    `request.entity.kind`.
    """
    try:
        normalized = _validate(request)
    except _UploadValidationError as exc:
        return PlayblastVersionUploadResult.failure(request, str(exc))

    try:
        connection = _resolve_connection(conn)
    except Exception as exc:
        # Connect-time failures (missing env_sg.py, import errors, etc.)
        # are not ShotGridErrors; keep this catch broad.
        log.exception("Could not resolve ShotGrid connection")
        return PlayblastVersionUploadResult.failure(
            request,
            f"Could not connect to ShotGrid: {_describe_exception(exc)}",
        )

    lookup, version_creator = _ENTITY_DISPATCH[normalized.entity.kind]

    try:
        sg_entity = lookup(connection, normalized.entity.value)
    except ShotGridError as exc:
        log.exception(
            "Could not resolve %s '%s' in ShotGrid",
            normalized.entity.kind,
            normalized.entity.value,
        )
        return PlayblastVersionUploadResult.failure(
            request,
            f"Could not resolve {normalized.entity.kind} "
            f"'{normalized.entity.value}' in ShotGrid: "
            f"{_describe_exception(exc)}",
        )

    warnings: list[str] = []
    user = _resolve_user(connection, normalized.artist_display_name, warnings)
    task = _resolve_task(connection, normalized.task_id, warnings)

    try:
        version = version_creator(
            connection,
            sg_entity,
            code=normalized.version_name,
            user=user,
            task=task,
            description=normalized.description,
            extra_fields=dict(normalized.extra_version_fields) or None,
        )
    except ShotGridError as exc:
        log.exception(
            "ShotGrid Version creation failed for %s '%s'",
            normalized.entity.kind,
            normalized.entity.value,
        )
        return PlayblastVersionUploadResult.failure(
            request,
            f"ShotGrid Version creation failed: {_describe_exception(exc)}",
            warnings=warnings,
        )

    version_id = version.id
    try:
        connection.upload_movie(version, normalized.movie_path)
    except ShotGridError as exc:
        log.exception("ShotGrid movie upload failed for Version %s", version_id)
        return PlayblastVersionUploadResult.failure(
            request,
            f"ShotGrid movie upload failed: {_describe_exception(exc)}",
            version_id=version_id,
            warnings=warnings,
        )

    review_linked = _try_link_review(connection, version, normalized, warnings)

    return PlayblastVersionUploadResult.success(
        request,
        message=_success_message(normalized.upload_target, review_linked=review_linked),
        version_id=version_id,
        movie_path=normalized.movie_path,
        warnings=warnings,
    )


def _validate(request: PlayblastVersionUploadRequest) -> _NormalizedUploadRequest:
    entity_value = request.entity.value.strip()
    if not entity_value:
        raise _UploadValidationError(
            f"{request.entity.kind.title()} identifier is required for ShotGrid upload."
        )

    version_name = str(request.version_name).strip()
    if not version_name:
        raise _UploadValidationError("Version name is required for ShotGrid upload.")

    movie_path = Path(str(request.movie_path)).expanduser().resolve()
    if not movie_path.exists() or not movie_path.is_file():
        raise _UploadValidationError(
            f"Playblast movie file was not found: {movie_path}"
        )
    if movie_path.stat().st_size < 1:
        raise _UploadValidationError(f"Playblast movie file is empty: {movie_path}")

    review_playlist_id = _optional_positive_int(request.review_playlist_id)
    if request.upload_target == UploadTarget.REVIEW and review_playlist_id is None:
        raise _UploadValidationError(
            "A valid review playlist id is required when upload target is 'review'."
        )
    if request.upload_target == UploadTarget.VERSION_ONLY:
        review_playlist_id = None

    return _NormalizedUploadRequest(
        entity=PlayblastEntity(kind=request.entity.kind, value=entity_value),
        movie_path=movie_path,
        version_name=version_name,
        description=_optional_text(request.description),
        artist_display_name=_optional_text(request.artist_display_name),
        task_id=_optional_positive_int(request.task_id),
        upload_target=request.upload_target,
        review_playlist_id=review_playlist_id,
        extra_version_fields=_normalize_extra_fields(request.extra_version_fields),
    )


def _resolve_connection(conn: ShotGrid | None) -> ShotGrid:
    if conn is not None:
        return conn
    from core.playblast.shotgrid._connection import default_db_connection

    return default_db_connection()


def _normalize_extra_fields(extras: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in extras.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        if value is None:
            continue
        normalized[normalized_key] = value
    return normalized


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
    return parsed if parsed >= 1 else None


def _describe_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _success_message(upload_target: UploadTarget, *, review_linked: bool) -> str:
    if upload_target == UploadTarget.REVIEW:
        if review_linked:
            return (
                "Version created, movie uploaded, and linked to the selected "
                "review playlist."
            )
        return (
            "Version created and movie uploaded to ShotGrid. Review playlist "
            "linking was not completed."
        )
    return "Version created and movie uploaded to ShotGrid."


def _try_link_review(
    connection: ShotGrid,
    version: Any,
    normalized: _NormalizedUploadRequest,
    warnings: list[str],
) -> bool:
    if (
        normalized.upload_target != UploadTarget.REVIEW
        or normalized.review_playlist_id is None
    ):
        return False
    try:
        playlist = connection.get_playlist(id=normalized.review_playlist_id)
        connection.link_to_playlist(version, playlist)
    except ShotGridError as exc:
        failure_reason = _describe_exception(exc)
        log.exception(
            "ShotGrid review link failed "
            "(%s=%s, version_id=%s, playlist_id=%s, reason=%s)",
            normalized.entity.kind,
            normalized.entity.value,
            version.id,
            normalized.review_playlist_id,
            failure_reason,
        )
        warnings.append(
            "Version upload succeeded, but linking to review playlist "
            f"{normalized.review_playlist_id} failed: {failure_reason}"
        )
        return False
    return True


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


__all__ = [
    "PlayblastEntity",
    "PlayblastVersionUploadRequest",
    "PlayblastVersionUploadResult",
    "UploadTarget",
    "upload_playblast_version",
]
