"""The previs viewport-capture primitive."""

from __future__ import annotations

import copy
from typing import Any

from mayacapture.capture import capture  # type: ignore[import-not-found]

from pipe.dcc.maya.previs.cameras import resolve_camera_node

CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720


def capture_cut(
    filename: str,
    camera: str,
    start_frame: int,
    end_frame: int,
    capture_kwargs: dict[str, Any],
) -> None:
    """Capture one camera's `[start_frame, end_frame]` to PNGs under `filename`.

    `capture_kwargs` is the resolved viewport-option dict from
    `apply_viewport_options`. It is deep-copied here because `capture()` mutates
    its nested option dicts in place, so a caller reusing one dict across cuts
    (the sequence stitcher) would otherwise carry mutations from one cut into the
    next.
    """
    capture(
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
        filename=filename,
        start_frame=start_frame,
        end_frame=end_frame,
        camera=resolve_camera_node(camera),
        format="image",
        compression="png",
        off_screen=True,
        show_ornaments=False,
        overwrite=True,
        maintain_aspect_ratio=False,
        viewer=0,
        **copy.deepcopy(capture_kwargs),
    )


__all__ = ["CAPTURE_WIDTH", "CAPTURE_HEIGHT", "capture_cut"]
