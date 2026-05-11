"""High-level orchestration for uploading a playblast Version to ShotGrid.

Consolidates the three near-identical orchestrations that used to live in
`pipe/houdini/playblast/launcher.py`, `pipe/maya/playblast/shot/dialog.py`, and
`pipe/maya/playblast/turnaround/dialog.py`. Each call site now passes a
`PlayblastUploadIntent` describing what the user picked in the UI; this
module handles version-name fallback, target resolution, the upload itself,
and the user-facing message lines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from core.playblast.shotgrid.paths import (
    default_version_name_from_movie_path,
    resolve_preferred_upload_movie_path,
)
from core.playblast.shotgrid.versions import (
    PlayblastEntity,
    PlayblastVersionUploadRequest,
    PlayblastVersionUploadResult,
    UploadTarget,
    upload_playblast_version,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlayblastUploadIntent:
    """What the user asked for in the playblast dialog.

    The dialog is responsible for validating that at least one of
    `upload_version` or `upload_to_review` is True, and for blocking
    invalid combinations (e.g. review-only with no playlist selected).
    By the time this intent reaches `run_playblast_upload`, validation
    has already accepted it.
    """

    entity: PlayblastEntity
    output_paths: tuple[Path, ...]
    preferred_paths: tuple[Path, ...]
    description: str | None
    artist_display_name: str | None
    upload_version: bool
    upload_to_review: bool
    review_playlist_id: int | None
    review_load_error: str | None
    fallback_version_name: str | None = None


def run_playblast_upload(intent: PlayblastUploadIntent) -> list[str]:
    """Resolve upload target, run `upload_playblast_version`, and return
    user-facing message lines for the dialog to show.

    Each line is prefixed with `ShotGrid Upload:` (success/failure/skip) or
    `ShotGrid Warning:` (advisory). Catches `Exception` from the upload
    itself, logs it, and converts to a failure line.
    """
    if not (intent.upload_version or intent.upload_to_review):
        return ["ShotGrid Upload: Skipped - no upload option selected."]

    movie_path = resolve_preferred_upload_movie_path(
        intent.output_paths,
        preferred_paths=intent.preferred_paths,
    )
    if movie_path is None:
        log.warning(
            "ShotGrid upload requested but no valid movie output was found "
            "for %s '%s'.",
            intent.entity.kind,
            intent.entity.value,
        )
        return ["ShotGrid Upload: Skipped - no valid playblast movie file was found."]

    version_name = (
        default_version_name_from_movie_path(movie_path)
        or intent.fallback_version_name
        or f"{intent.entity.value}_playblast"
    )

    upload_target, review_playlist_id, fallback_warning, fallback_reason = (
        _resolve_upload_target(intent)
    )

    request = PlayblastVersionUploadRequest(
        entity=intent.entity,
        movie_path=movie_path,
        version_name=version_name,
        description=intent.description or None,
        artist_display_name=intent.artist_display_name or None,
        upload_target=upload_target,
        review_playlist_id=review_playlist_id,
    )

    try:
        upload_result = upload_playblast_version(request)
    except Exception as exc:
        log.exception(
            "ShotGrid upload failed for %s '%s'",
            intent.entity.kind,
            intent.entity.value,
        )
        return [f"ShotGrid Upload: Failed - {exc}"]

    return _build_message_lines(
        upload_result,
        intent=intent,
        fallback_warning=fallback_warning,
        fallback_reason=fallback_reason,
    )


def _resolve_upload_target(
    intent: PlayblastUploadIntent,
) -> tuple[UploadTarget, int | None, str | None, str | None]:
    """Decide the effective `UploadTarget` and the review playlist id, plus
    an optional fallback warning + reason when review upload is downgraded
    to version-only.

    Returns `(target, review_playlist_id, fallback_warning, fallback_reason)`.
    `fallback_warning` is the artist-facing line; `fallback_reason` goes to
    the diagnostic log.
    """
    if not intent.upload_to_review:
        return (UploadTarget.VERSION_ONLY, None, None, None)

    playlist_id = intent.review_playlist_id
    if isinstance(playlist_id, int) and playlist_id > 0:
        return (UploadTarget.REVIEW, playlist_id, None, None)

    if intent.review_load_error:
        return (
            UploadTarget.VERSION_ONLY,
            None,
            "Review upload skipped because recent reviews could not be loaded. "
            "Version upload continued.",
            intent.review_load_error,
        )
    return (
        UploadTarget.VERSION_ONLY,
        None,
        "Review upload skipped because no valid review playlist was selected. "
        "Version upload continued.",
        "missing review playlist id",
    )


def _build_message_lines(
    upload_result: PlayblastVersionUploadResult,
    *,
    intent: PlayblastUploadIntent,
    fallback_warning: str | None,
    fallback_reason: str | None,
) -> list[str]:
    message_lines: list[str] = []
    if upload_result.ok:
        success_message = (
            f"ShotGrid Upload: Success - {upload_result.version_name}"
            f" ({upload_result.entity.kind} {upload_result.entity.value})."
        )
        if upload_result.version_id is not None:
            success_message = (
                f"{success_message} Version ID: {upload_result.version_id}."
            )
        message_lines.append(success_message)
    else:
        message_lines.append(f"ShotGrid Upload: Failed - {upload_result.message}")

    if fallback_warning and upload_result.ok:
        message_lines.append(f"ShotGrid Warning: {fallback_warning}")
    if fallback_warning:
        log.warning(
            "ShotGrid review upload fallback to version upload "
            "(%s=%s, version_id=%s, playlist_id=%s, reason=%s)",
            intent.entity.kind,
            intent.entity.value,
            upload_result.version_id,
            intent.review_playlist_id,
            fallback_reason or "review playlist unavailable",
        )
    for warning in upload_result.warnings:
        message_lines.append(f"ShotGrid Warning: {warning}")

    return message_lines


__all__ = ["PlayblastUploadIntent", "run_playblast_upload"]
