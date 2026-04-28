from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class _FFmpegPresetSpec:
    """Encoding parameters carried by each FFmpegPreset enum member."""

    ext: str
    out_kwargs: dict[str, Any]

    def __hash__(self) -> int:
        return hash(frozenset(self.out_kwargs.items()))


class FFmpegPreset(_FFmpegPresetSpec, Enum):
    """Catalog of named FFmpeg encoding presets used by playblast outputs."""

    EDIT_SQ = (
        "mov",
        {
            "vcodec": "dnxhd",
            "pix_fmt": "yuv422p",
            "vprofile": "dnxhr_sq",
            # this number comes from Avid's table in the DNxHD whitepaper
            "video_bitrate": "124M",
        },
    )
    EDIT_HQX = (
        "mov",
        {
            "vcodec": "dnxhd",
            "pix_fmt": "yuv422p10le",
            "vprofile": "dnxhr_hqx",
            "video_bitrate": "188M",
        },
    )
    WEB = (
        "mp4",
        {
            "vcodec": "libx264",
            "preset": "veryslow",
            "tune": "animation",
            "crf": 20,
        },
    )
    H265 = (
        "mp4",
        {
            "vcodec": "libx265",
            "preset": "slow",
            "crf": 23,
            "pix_fmt": "yuv420p",
        },
    )


__all__ = ["FFmpegPreset"]
