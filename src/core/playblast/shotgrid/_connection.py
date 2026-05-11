"""Lazy default ShotGrid connection used by `core.playblast.shotgrid`
helpers when a caller hasn't passed an explicit `conn`. Lives in its own
module so sibling submodules can import it without the `__init__.py`
reverse-import dance the package used to do."""

from __future__ import annotations

from core.shotgrid import ShotGrid


def default_db_connection() -> ShotGrid:
    # `env_sg` holds the gitignored production credentials; keep the import
    # lazy so importing this module on a host without credentials does not
    # raise at module-load time.
    from env_sg import DB_Config

    return ShotGrid.connect(DB_Config)


__all__ = ["default_db_connection"]
