from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from maya import cmds

if TYPE_CHECKING:
    from typing import Generator


@contextmanager
def maintain_selection() -> Generator[None, None, None]:
    selection = cmds.ls(selection=True, long=True, ufeObjects=True, absoluteName=True)
    try:
        yield
    finally:
        cmds.select(*selection, replace=True)
