"""One place for the `mayaUSDExport` call and the Windows workaround. Can we just burn Windows?"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from platform import system
from typing import Any

from maya import cmds as mc

_IS_WINDOWS = system() == "Windows"


def export_selection(destination: Path, **kwargs: Any) -> None:
    """Export the current selection to `destination`, creating its directory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = (
        Path(os.getenv("TEMP", "")) / destination.name if _IS_WINDOWS else destination
    )

    mc.mayaUSDExport(file=str(written), selection=True, **kwargs)  # type: ignore

    if written != destination:
        shutil.move(str(written), str(destination))
