from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipe.shotgrid import ShotGrid


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
    connection = conn or _default_db_connection()
    return tuple(
        PlayblastReviewPlaylistOption(
            playlist_id=playlist.id,
            code=(playlist.code or "").strip(),
            updated_at=playlist.updated_at,
            created_at=playlist.created_at,
        )
        for playlist in connection.find_recent_playlists(limit=limit)
    )


def _default_db_connection() -> ShotGrid:
    # `env_sg` holds the gitignored production credentials; keep the import
    # lazy so importing this module on a host without credentials does not
    # raise at module-load time.
    from env_sg import DB_Config

    return ShotGrid.connect(DB_Config)


__all__ = [
    "PlayblastReviewPlaylistOption",
    "list_recent_review_playlists",
]
