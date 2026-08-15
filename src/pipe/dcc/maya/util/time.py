from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import maya.api.OpenMaya as om
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


def scene_frame_rate() -> int:
    # Converting a one-frame MTime covers every time unit, including the `Xfps`
    # ones `currentUnit` reports by name and would need a lookup table for.
    seconds_per_frame = om.MTime(1.0, om.MTime.uiUnit()).asUnits(om.MTime.kSeconds)
    return round(1.0 / seconds_per_frame)
