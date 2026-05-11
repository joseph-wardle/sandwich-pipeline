from Qt import QtCore
from Qt.QtWidgets import QProgressBar


class RigBuildProgressBar(QProgressBar):
    def __init__(self):
        super().__init__()
        pass

    @QtCore.Slot(float)
    def update_progress(self, progress: float):
        self.setValue(int(progress * 100))
