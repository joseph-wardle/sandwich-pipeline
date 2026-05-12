from __future__ import annotations

import re
import sys
from typing import cast

import maya.OpenMayaUI as omUI
from framework.interface import DCCRuntime
from Qt import QtCompat, QtWidgets


class MayaRuntime(DCCRuntime):
    def get_main_qt_window(self) -> QtWidgets.QWidget | None:
        if not self.is_headless():
            ptr = omUI.MQtUtil.mainWindow()
            if ptr is not None:
                return cast(
                    QtWidgets.QWidget,
                    QtCompat.wrapInstance(int(ptr), QtWidgets.QWidget),
                )
        return None

    def is_headless(self) -> bool:
        pattern = re.compile(r"^.*mayapy(?:\.?(?:bin|exe))$")
        return bool(pattern.match(sys.executable))


_runtime = MayaRuntime()

get_main_qt_window = _runtime.get_main_qt_window
is_headless = _runtime.is_headless
