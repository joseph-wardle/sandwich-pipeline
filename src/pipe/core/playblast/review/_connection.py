"""The ShotGrid connection `pipe.core.playblast.review` submodules share."""

from __future__ import annotations

from pipe.core.shotgrid import ShotGrid


def default_db_connection() -> ShotGrid:
    # `env_sg` holds the gitignored production credentials; keep the import
    # lazy so importing this module on a host without credentials does not
    # raise at module-load time.
    from env_sg import DB_Config

    return ShotGrid.connect(DB_Config)


__all__ = ["default_db_connection"]
