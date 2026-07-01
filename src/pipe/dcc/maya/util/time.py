from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from maya import cmds

if TYPE_CHECKING:
    from typing import Generator


@contextmanager
def maintain_current_time() -> Generator[int, None, None]:
    ctime = cmds.currentTime(query=True)
    try:
        yield ctime
    finally:
        cmds.currentTime(ctime)
