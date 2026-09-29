from __future__ import annotations

from enum import Enum
from typing import Any

_SRGB = (
    (
        "vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2:out_color_matrix=bt709:out_range=tv,"
        "setparams=color_primaries=bt709:colorspace=bt709:range=tv",
    ),
    ("colorspace", "bt709"),
    ("color_primaries", "bt709"),
    ("color_trc", "iec61966-2-1"),
    ("color_range", "tv"),
)


class FFmpegPreset(Enum):
    """Catalog of named FFmpeg encodes, shared by playblasts and Tractor Encode.

    Each member's value is a `(ext, options)` tuple of ffmpeg output options,
    stored as a tuple-of-tuples so the Enum value is hashable.
    """

    EDIT_SQ = (
        "mov",
        (
            ("vcodec", "dnxhd"),
            ("pix_fmt", "yuv422p"),
            # The profile sets the bitrate, and dnxhd ignores any other.
            ("vprofile", "dnxhr_sq"),
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
    # ProRes 422 HQ at its full 10 bits, so it needs frames deeper than 8. The
    # profile sets the quality; a qscale would override it.
    MASTER = (
        "mov",
        (
            ("vcodec", "prores_ks"),
            ("vprofile", "hq"),
            ("vendor", "apl0"),
            ("pix_fmt", "yuv422p10le"),
        ),
    )

    @property
    def ext(self) -> str:
        return self.value[0]

    @property
    def out_kwargs(self) -> dict[str, Any]:
        """The options as ffmpeg-python output kwargs."""
        return dict(_SRGB + self.value[1])

    def args(self) -> list[str]:
        """The options as ffmpeg command-line arguments."""
        return [a for k, v in _SRGB + self.value[1] for a in (f"-{k}", str(v))]


__all__ = ["FFmpegPreset"]
