"""Asset turnaround subsystem.

`AssetTurnaroundDialog` (in `dialog.py`) drives an orbit-around-an-asset
playblast captured by `MTurnaroundPlayblaster` (in `playblaster.py`). The
`TurnaroundPlayblastConfig` shape and `resolve_turnaround_review_roots()`
geometry resolver live in `config.py`.
"""

from pipe.m.playblast.turnaround.dialog import (
    AssetTurnaroundDialog as AssetTurnaroundDialog,
)

__all__ = ["AssetTurnaroundDialog"]
