from __future__ import annotations

import attrs

from pipe.core.playblast.review._connection import default_db_connection


@attrs.frozen
class PlayblastReviewPlaylistOption:
    """One review playlist as the viewer's playlist picker needs it."""

    playlist_id: int
    code: str

    @property
    def display_name(self) -> str:
        return self.code.strip() or f"Playlist {self.playlist_id}"


def list_recent_review_playlists(
    *, limit: int = 10
) -> tuple[PlayblastReviewPlaylistOption, ...]:
    return tuple(
        PlayblastReviewPlaylistOption(playlist_id=playlist.id, code=playlist.code or "")
        for playlist in default_db_connection().find_recent_playlists(limit=limit)
    )


__all__ = [
    "PlayblastReviewPlaylistOption",
    "list_recent_review_playlists",
]
