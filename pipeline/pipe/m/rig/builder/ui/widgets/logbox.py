import logging

from Qt import QtGui
from Qt.QtWidgets import QApplication, QPlainTextEdit, QWidget


class RigTestFormatter(logging.Formatter):
    """Custom formatter to prepends 'TEST:' to log messages."""

    def format(self, record: logging.LogRecord) -> str:
        # Get the base message from the logger
        message = record.getMessage()

        return f"TEST: {message}"


class QPlainTextEditLogHandler(logging.Handler):
    def __init__(
        self, text_edit: QPlainTextEdit, formatter: logging.Formatter | None = None
    ):
        super().__init__()
        self.text_edit = text_edit
        if formatter:
            self.setFormatter(formatter)

    def emit(self, record):
        msg = self.format(record)
        # Use appendPlainText to keep it efficient
        try:
            self.text_edit.appendPlainText(msg)
            # Auto-scroll to bottom
            self.text_edit.moveCursor(QtGui.QTextCursor.End)
            QApplication.processEvents()
        except RuntimeError:
            self.close()


class RigBuildLogBox(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent=parent)
        self.setPlainText("Rig Build Log")
        self.setReadOnly(True)
        self.setMinimumSize(32, 24)
        self.setBaseSize(128, 64)
        self._connections: list[tuple[logging.Logger, logging.Handler]] = []
        self.destroyed.connect(self._on_destroyed)

    def clear_log(self):
        self.clear()

    def connect_test_logger(self, logger: logging.Logger):
        self.connect_logger(logger, RigTestFormatter())

    def connect_logger(
        self, logger: logging.Logger, formatter: logging.Formatter | None = None
    ):
        handler = QPlainTextEditLogHandler(self, formatter)
        logger.addHandler(handler)
        self._connections.append((logger, handler))

    def disconnect_loggers(self):
        for logger, handler in self._connections:
            logger.removeHandler(handler)
        self._connections.clear()

    def _on_destroyed(self):
        self.disconnect_loggers()
