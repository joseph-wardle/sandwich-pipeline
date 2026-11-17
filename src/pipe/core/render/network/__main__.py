"""Open the Render Network window, optionally on one shot.

Needs a Python with a Qt binding, which the pipeline venv lacks, so the
desktop launcher runs it in mayapy:

    PYTHONPATH=src /usr/autodesk/maya2026/bin/mayapy -m pipe.core.render.network [A_080]
"""

from __future__ import annotations

import argparse
import logging
import sys

from Qt import QtWidgets

from pipe.core.render.lighters import current_user, lit_shots
from pipe.core.render.network import theme
from pipe.core.render.network.window import TITLE, RenderNetworkWindow
from pipe.core.shotgrid import Shot, ShotGrid, is_previs_shot_code
from pipe.core.ui import MessageDialog
from pipe.core.util.users import is_admin

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Edit and send a shot's render layers."
    )
    parser.add_argument("shot", nargs="?", help="open this shot, e.g. A_080")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    theme.apply(app)
    try:
        shots = _load_shots()
    except _StartupError as exc:
        MessageDialog(None, str(exc), TITLE).exec_()
        sys.exit(1)

    window = RenderNetworkWindow(shots, args.shot, _load_lit_shots(), is_admin())
    window.show()
    sys.exit(app.exec_())


class _StartupError(Exception):
    """Why the window cannot open, worded for the artist."""


def _load_shots() -> list[Shot]:
    """Every active shot in ShotGrid, previs aside."""
    # env_sg holds gitignored production credentials; import lazily so
    # importing this module never requires them.
    from env_sg import DB_Config

    try:
        shots = ShotGrid.connect(DB_Config).find_shots()
    # shotgun_api3 raises its own untyped errors while connecting and reading.
    except Exception as exc:
        log.exception("Could not read shots from ShotGrid")
        raise _StartupError(
            "Could not reach ShotGrid. Check your network connection and try again."
        ) from exc
    shots = [s for s in shots if s.code and not is_previs_shot_code(s.code)]
    if not shots:
        raise _StartupError("ShotGrid lists no shots for this project.")
    return sorted(shots, key=lambda shot: shot.code or "")


def _load_lit_shots() -> set[str] | None:
    """The codes of the shots the artist lights, or None when ShotGrid can't say."""
    from env_sg import DB_Config

    try:
        conn = ShotGrid.connect(DB_Config)
        return {shot.code or "" for shot in lit_shots(conn, current_user(conn))}
    # Only Publish Lighting needs it, so the window opens without it.
    except Exception:
        log.exception("Could not read which shots this artist lights")
        return None


if __name__ == "__main__":
    main()
