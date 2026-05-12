from __future__ import annotations

import re
import sys

import hou
from framework.interface import DCCRuntime
from Qt import QtWidgets


class HoudiniRuntime(DCCRuntime):
    def get_main_qt_window(self) -> QtWidgets.QWidget | None:
        if not self.is_headless():
            return hou.qt.mainWindow()
        return None

    def is_headless(self) -> bool:
        try:
            if hasattr(hou, "isUIAvailable"):
                return not hou.isUIAvailable()
        except Exception:
            pass

        return bool(re.match(r"^.*ython(?:\.exe)?3?", sys.executable))


_runtime = HoudiniRuntime()

get_main_qt_window = _runtime.get_main_qt_window
is_headless = _runtime.is_headless
