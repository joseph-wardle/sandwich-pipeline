"""One place for the `mayaUSDExport` call and the Windows workaround. Can we just burn Windows?"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from platform import system
from typing import Any

from maya import cmds as mc
from pxr import Sdf

_IS_WINDOWS = system() == "Windows"


def export_selection(destination: Path, **kwargs: Any) -> None:
    """Export the current selection to `destination`, creating its directory."""
    written = _staging_path(destination)
    mc.mayaUSDExport(file=str(written), selection=True, **kwargs)  # type: ignore
    _deliver(written, destination)


def export_layer(layer: Sdf.Layer, destination: Path) -> None:
    """Write `layer` to `destination`, creating its directory."""
    written = _staging_path(destination)
    if not layer.Export(str(written)):
        raise OSError(f"Could not write {written}")
    _deliver(written, destination)


def _staging_path(destination: Path) -> Path:
    """Where to write first: a local temp file on Windows, else the destination.

    Writing USD straight to a network drive on Windows fails:
    https://github.com/PixarAnimationStudios/OpenUSD/issues/849
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _IS_WINDOWS:
        return Path(os.getenv("TEMP", "")) / destination.name
    return destination


def _deliver(written: Path, destination: Path) -> None:
    if written != destination:
        shutil.move(str(written), str(destination))
