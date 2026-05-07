from maya import cmds
from maya.OpenMayaUI import MQtUtil
from Qt.QtCompat import wrapInstance
from Qt.QtGui import QColor
from Qt.QtWidgets import QMainWindow


def blend_color(color1: QColor, color2: QColor, blend: float) -> QColor:
    t = min(1, max(0, blend))
    r = int(color1.red() + t * (color2.red() - color1.red()))
    g = int(color1.green() + t * (color2.green() - color1.green()))
    b = int(color1.blue() + t * (color2.blue() - color1.blue()))
    return QColor(r, g, b)


def get_maya_main_window():
    mw_ptr = MQtUtil.mainWindow()
    return wrapInstance(int(mw_ptr), QMainWindow)


def delete_workspace_control(control: str):
    if cmds.workspaceControl(control, query=True, exists=True):
        cmds.workspaceControl(control, edit=True, close=True)
        cmds.deleteUI(control, control=True)


def check_and_restore_workspace_control(control: str) -> bool:
    """Checks if a workspaceControl exists, and if so it restores it and returns True, else False"""
    if cmds.workspaceControl(control, exists=True):
        cmds.workspaceControl(control, edit=True, restore=True)
        return True
    return False
