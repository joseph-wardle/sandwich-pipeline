from __future__ import annotations

from pipe.shotgrid import ShotGrid


def _default_db_connection() -> ShotGrid:
    # `env_sg` holds the gitignored production credentials; keep the import
    # lazy so importing this module on a host without credentials does not
    # raise at module-load time.
    from env_sg import DB_Config

    return ShotGrid.connect(DB_Config)


from pipe.playblast.shotgrid.paths import (  # noqa: E402
    default_version_name_from_movie_path,
    resolve_preferred_upload_movie_path,
)
from pipe.playblast.shotgrid.playlists import (  # noqa: E402
    PlayblastReviewPlaylistOption,
    list_recent_review_playlists,
)
from pipe.playblast.shotgrid.versions import (  # noqa: E402
    PlayblastEntity,
    PlayblastVersionUploadRequest,
    PlayblastVersionUploadResult,
    UploadTarget,
    upload_playblast_version,
)

__all__ = [
    "PlayblastEntity",
    "PlayblastReviewPlaylistOption",
    "PlayblastVersionUploadRequest",
    "PlayblastVersionUploadResult",
    "UploadTarget",
    "default_version_name_from_movie_path",
    "list_recent_review_playlists",
    "resolve_preferred_upload_movie_path",
    "upload_playblast_version",
]
