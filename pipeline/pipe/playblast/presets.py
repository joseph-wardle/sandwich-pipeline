from __future__ import annotations

from enum import Enum
from typing import Any


class FFmpegPreset(Enum):
    """Catalog of named FFmpeg encoding presets used by playblast outputs.

    Each member's value is a `(ext, out_kwargs_items)` tuple. `out_kwargs` is
    stored as a tuple-of-tuples so the Enum value is hashable, and exposed as
    a fresh dict via the property.
    """

    EDIT_SQ = (
        "mov",
        (
            ("vcodec", "dnxhd"),
            ("pix_fmt", "yuv422p"),
            ("vprofile", "dnxhr_sq"),
            # Number from Avid's table in the DNxHD whitepaper.
            ("video_bitrate", "124M"),
            ("movflags", "+faststart"),
        ),
    )
    WEB = (
        "mp4",
        (
            ("vcodec", "libx264"),
            ("preset", "medium"),
            ("tune", "animation"),
            ("crf", 20),
            ("pix_fmt", "yuv420p"),
            ("movflags", "+faststart"),
        ),
    )

    @property
    def ext(self) -> str:
        return self.value[0]

    @property
    def out_kwargs(self) -> dict[str, Any]:
        return dict(self.value[1])


__all__ = ["FFmpegPreset"]
