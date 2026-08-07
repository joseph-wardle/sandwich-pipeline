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


def list_review_playlists(
    *, search: str = "", limit: int = 10
) -> tuple[PlayblastReviewPlaylistOption, ...]:
    """Return up to `limit` playlists, most recently updated first, restricted to
    those whose code contains `search`.

    Every ShotGrid Playlist is a review list — there is no review-only subtype to
    filter on — so `search` is the only way past the `limit` most recent ones.
    """
    return tuple(
        PlayblastReviewPlaylistOption(playlist_id=playlist.id, code=playlist.code or "")
        for playlist in default_db_connection().find_playlists(
            code_contains=search.strip() or None, limit=limit
        )
    )


__all__ = [
    "PlayblastReviewPlaylistOption",
    "list_review_playlists",
]
