"""Substance Painter in-DCC runtime — main Qt window + headless detection."""

from __future__ import annotations

from substance_painter import ui

from framework.interface import DCCRuntime
from Qt import QtWidgets


class SubstancePainterRuntime(DCCRuntime):
    def __init__(self) -> None:
        super().__init__()

    def get_main_qt_window(self) -> QtWidgets.QWidget | None:
        return ui.get_main_window()

    def is_headless(self) -> bool:
        return False


_runtime = SubstancePainterRuntime()

get_main_qt_window = _runtime.get_main_qt_window
is_headless = _runtime.is_headless
