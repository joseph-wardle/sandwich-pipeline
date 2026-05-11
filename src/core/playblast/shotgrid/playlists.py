from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.shotgrid import ShotGrid


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


def list_recent_review_playlists(
    *,
    conn: ShotGrid | None = None,
    limit: int = 10,
) -> tuple[PlayblastReviewPlaylistOption, ...]:
    """Return recent review playlists as UI-friendly options."""
    if conn is None:
        from core.playblast.shotgrid._connection import default_db_connection

        connection = default_db_connection()
    else:
        connection = conn
    return tuple(
        PlayblastReviewPlaylistOption(
            playlist_id=playlist.id,
            code=(playlist.code or "").strip(),
            updated_at=playlist.updated_at,
            created_at=playlist.created_at,
        )
        for playlist in connection.find_recent_playlists(limit=limit)
    )


__all__ = [
    "PlayblastReviewPlaylistOption",
    "list_recent_review_playlists",
]
