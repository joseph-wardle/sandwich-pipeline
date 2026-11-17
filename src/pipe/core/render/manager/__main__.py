"""Open the Render Manager for the artist who is logged in.

Needs a Python with a Qt binding, which the pipeline venv lacks, so the
desktop launcher runs it in mayapy:

    PYTHONPATH=src /usr/autodesk/maya2026/bin/mayapy -m pipe.core.render.manager
"""

from __future__ import annotations

import logging
import sys

from Qt import QtWidgets

from pipe.core.render.lighters import LIGHTING_STEP, current_user, lit_shots
from pipe.core.render.manager.window import RenderManagerWindow
from pipe.core.shotgrid import (
    Shot,
    ShotGrid,
    ShotGridAmbiguous,
    ShotGridError,
    ShotGridNotFound,
    User,
    is_previs_shot_code,
)
from pipe.core.ui import MessageDialog
from pipe.core.util.users import resolve_artist_display_name

log = logging.getLogger(__name__)

_TITLE = "Render Manager"


class _StartupError(Exception):
    """Why the manager cannot open, worded for the artist."""


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    )
    app = QtWidgets.QApplication(sys.argv)
    try:
        user, shots = _load_assignments()
    except _StartupError as exc:
        MessageDialog(None, str(exc), _TITLE).exec_()
        sys.exit(1)

    window = RenderManagerWindow(shots, user.name or "")
    window.show()
    sys.exit(app.exec_())


def _load_assignments() -> tuple[User, list[Shot]]:
    """Reads ShotGrid: the artist, and the shots they have a Lighting task on."""
    # env_sg holds gitignored production credentials; import lazily so
    # importing this module never requires them.
    from env_sg import DB_Config

    name = resolve_artist_display_name()
    unreachable = _StartupError(
        "Could not reach ShotGrid. Check your network connection and try again."
    )
    try:
        conn = ShotGrid.connect(DB_Config)
    # shotgun_api3 contacts the server while constructing, and raises its own
    # untyped errors there; anything here is reported as unreachable.
    except Exception as exc:
        log.exception("Could not open a ShotGrid connection")
        raise unreachable from exc
    try:
        user = current_user(conn)
        lit = lit_shots(conn, user)
    except (ShotGridNotFound, ShotGridAmbiguous) as exc:
        log.warning("Could not identify the artist %r: %s", name, exc)
        raise _StartupError(
            f'Could not find exactly one ShotGrid user named "{name}". Ask a TD '
            "to check your name in ShotGrid and in the pipeline's usernames.json."
        ) from exc
    except ShotGridError as exc:
        log.exception("ShotGrid lookup failed for %r", name)
        raise unreachable from exc

    shots = [shot for shot in lit if not is_previs_shot_code(shot.code)]
    if not shots:
        raise _StartupError(
            f"ShotGrid has no {LIGHTING_STEP} tasks assigned to {name}, so there "
            "are no shots to manage renders for."
        )
    return user, shots


if __name__ == "__main__":
    main()
