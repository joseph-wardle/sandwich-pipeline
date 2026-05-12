from __future__ import annotations

import substance_painter as sp

from dcc.substance_painter.runtime import get_main_qt_window
from core.ui.dialogs import MessageDialog


class sRGBChecker:
    srgb_channels: list[sp.textureset.Channel]

    def __init__(self) -> None:
        self.srgb_channels = []

    def check(self) -> bool:
        """Return True if sRGB channels are properly configured"""
        for ts in sp.textureset.all_texture_sets():
            try:
                stack = ts.get_stack()
            except ValueError:
                MessageDialog(
                    get_main_qt_window(),
                    "Warning! sRGB Checker could not get stack! You are doing something cool with material layering. Please show this to Dallin so he can fix it.",
                ).exec_()
                return False

            for ch in stack.all_channels().values():
                if ch.format() in [
                    sp.textureset.ChannelFormat.sRGB8,
                    sp.textureset.ChannelFormat.RGB8,
                ]:
                    self.srgb_channels.append(ch)

        return not bool(self.srgb_channels)
