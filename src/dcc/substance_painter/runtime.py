from __future__ import annotations

from framework.interface import DCCRuntime
from Qt import QtWidgets
from substance_painter import ui


class SubstancePainterRuntime(DCCRuntime):
    def get_main_qt_window(self) -> QtWidgets.QWidget | None:
        return ui.get_main_window()

    def is_headless(self) -> bool:
        return False


_runtime = SubstancePainterRuntime()

get_main_qt_window = _runtime.get_main_qt_window
is_headless = _runtime.is_headless
